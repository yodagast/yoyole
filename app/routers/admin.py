"""ERP 后台路由：管理员登录、仪表盘、商品/订单/库存管理"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import require_admin
from app.i18n import get_lang
from app.models import (
    Address,
    AdminUser,
    AdminRole,
    Category,
    Customer,
    Order,
    OrderStatus,
    Payment,
    Product,
    Review,
    ReviewStatus,
    SKU,
    StockMovement,
    WishlistItem,
    CartItem,
)
from app.schemas import (
    AdminCustomerOut,
    AdminLoginIn,
    AdminPasswordChangeIn,
    AdminPasswordResetIn,
    AdminTokenOut,
    AdminUserIn,
    AdminUserOut,
    AdminUserUpdateIn,
    CategoryOut,
    DashboardOut,
    Message,
    OrderOut,
    ProductCreateIn,
    ProductOut,
    ProductUpdateIn,
    SKUIn,
    SKUOut,
)
from app.security import create_access_token, hash_password, verify_password
from app.routers.orders import _order_to_out
from app.routers.product_admin import log_action

router = APIRouter(prefix="/api/admin", tags=["admin"])

logger = logging.getLogger(__name__)


@router.post("/login", response_model=AdminTokenOut)
async def admin_login(payload: AdminLoginIn, db: AsyncSession = Depends(get_db)):
    """管理员登录"""
    result = await db.execute(
        select(AdminUser).where(AdminUser.username == payload.username)
    )
    admin = result.scalar_one_or_none()
    if not admin or not verify_password(payload.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not admin.is_active:
        raise HTTPException(status_code=403, detail="账号已禁用")

    admin.last_login = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()

    token = create_access_token(subject=str(admin.id), extra={"type": "admin", "role": admin.role.value})
    return AdminTokenOut(
        access_token=token,
        username=admin.username,
        role=admin.role.value,
        full_name=admin.full_name,
    )


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """ERP 仪表盘统计"""
    products_count = (await db.execute(select(func.count(Product.id)))).scalar() or 0
    orders_count = (await db.execute(select(func.count(Order.id)))).scalar() or 0
    customers_count = (await db.execute(select(func.count(Customer.id)))).scalar() or 0

    revenue = (await db.execute(
        select(func.coalesce(func.sum(Order.total_amount), 0)).where(
            Order.status.in_([OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.COMPLETED])
        )
    )).scalar() or Decimal("0")

    pending_orders = (await db.execute(
        select(func.count(Order.id)).where(Order.status == OrderStatus.PENDING)
    )).scalar() or 0

    low_stock = (await db.execute(
        select(func.count(SKU.id)).where(SKU.stock <= SKU.low_stock_threshold)
    )).scalar() or 0

    return DashboardOut(
        products_count=int(products_count),
        orders_count=int(orders_count),
        customers_count=int(customers_count),
        revenue=Decimal(str(revenue)),
        pending_orders=int(pending_orders),
        low_stock=int(low_stock),
    )


def _apply_product_filters(
    stmt,
    *,
    q: str | None,
    status: str | None,
    review_status: str | None,
    category_id: int | None,
    source: str | None,
    tab: str,
    product_id: str | None,
    sku_code: str | None,
):
    """把后台商品列表的全部筛选条件应用到查询上（列表接口与计数接口共用）

    统一在此处维护筛选口径，避免「列表按条件筛、总数按标签页算」这类不一致。
    """
    from sqlalchemy.dialects.postgresql import JSONB

    from app.routers.product_admin import TABS, apply_tab_filter

    if tab not in TABS:
        raise HTTPException(status_code=400, detail=f"tab 必须是 {'/'.join(TABS)}")

    # 状态标签页（默认 all 不含回收站商品）
    stmt = apply_tab_filter(stmt, tab)

    if q:
        pattern = f"%{q}%"
        name_jsonb = Product.name_i18n.cast(JSONB)
        stmt = stmt.where(
            Product.sku_code.ilike(pattern)
            | name_jsonb["zh"].astext.ilike(pattern)
            | name_jsonb["en"].astext.ilike(pattern)
        )
    if status:
        stmt = stmt.where(Product.status == status)
    if review_status:
        stmt = stmt.where(Product.review_status == review_status)
    if category_id is not None:
        stmt = stmt.where(Product.category_id == category_id)
    if source:
        stmt = stmt.where(Product.source == source)
    if product_id:
        # 支持空格/逗号分隔的多 ID 查询
        raw_ids = [x for x in re.split(r"[\s,]+", product_id.strip()) if x]
        ids: list[int] = []
        for x in raw_ids:
            if not x.isdigit():
                raise HTTPException(status_code=400, detail=f"商品 ID 必须为数字：{x}")
            ids.append(int(x))
        if ids:
            stmt = stmt.where(Product.id.in_(ids))
    if sku_code:
        stmt = stmt.where(
            Product.id.in_(
                select(SKU.product_id).where(SKU.sku_code.ilike(f"%{sku_code}%"))
            )
        )
    return stmt


async def _fill_favorite_counts(db: AsyncSession, products: list[Product]) -> None:
    """批量填充商品收藏数（`favorite_count` 不是 Product 表字段，需聚合 WishlistItem）

    后台商品列表有「收藏」列，若不填充会恒显示 0（前台 catalog 另有自己的聚合）。
    """
    if not products:
        return
    rows = (
        await db.execute(
            select(WishlistItem.product_id, func.count(WishlistItem.id))
            .where(WishlistItem.product_id.in_([p.id for p in products]))
            .group_by(WishlistItem.product_id)
        )
    ).all()
    counts = {pid: int(n or 0) for pid, n in rows}
    for p in products:
        p.favorite_count = counts.get(p.id, 0)


@router.get("/products-count")
async def admin_count_products(
    q: str | None = None,
    status: str | None = None,
    review_status: str | None = None,
    category_id: int | None = None,
    source: str | None = None,
    tab: str = Query("all", description="状态标签页：all/on_sale/off_shelf/sold_out/pending/rejected/draft/deleted"),
    product_id: str | None = Query(None, description="商品 ID，支持空格/逗号分隔多个"),
    sku_code: str | None = Query(None, description="规格编码（SKU）模糊匹配"),
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """筛选后的商品总数（后台分页显示「共 N 条」，与列表接口筛选口径一致）

    不带筛选条件时等价于该标签页的数量；带筛选时才是真正命中的条数。
    """
    stmt = select(func.count(Product.id)).select_from(Product)
    stmt = _apply_product_filters(
        stmt, q=q, status=status, review_status=review_status,
        category_id=category_id, source=source, tab=tab,
        product_id=product_id, sku_code=sku_code,
    )
    total = int((await db.execute(stmt)).scalar() or 0)
    return {"total": total}


@router.get("/products", response_model=list[ProductOut])
async def admin_list_products(
    q: str | None = None,
    status: str | None = None,
    review_status: str | None = None,
    category_id: int | None = None,
    source: str | None = None,
    tab: str = Query("all", description="状态标签页：all/on_sale/off_shelf/sold_out/pending/rejected/draft/deleted"),
    product_id: str | None = Query(None, description="商品 ID，支持空格/逗号分隔多个"),
    sku_code: str | None = Query(None, description="规格编码（SKU）模糊匹配"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    limit: int = Query(0, ge=0, le=2000, description=">0 时忽略分页，直接返回前 N 条（兼容旧调用）"),
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """商品列表（后台）

    - `tab`：单维度状态标签页（已删除商品需显式用 `tab=deleted`）
    - `page` / `page_size`：分页；**带筛选时**总数请用 `/products-count`（同参数），
      不带筛选时也可直接用 `/products-status-counts` 的对应标签数量
    - 兼容旧调用：`limit>0` 时不分页，直接返回前 `limit` 条
    """
    stmt = select(Product).options(selectinload(Product.skus))
    stmt = _apply_product_filters(
        stmt, q=q, status=status, review_status=review_status,
        category_id=category_id, source=source, tab=tab,
        product_id=product_id, sku_code=sku_code,
    )

    stmt = stmt.order_by(Product.id.desc())
    if limit:
        stmt = stmt.limit(limit)
    else:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(stmt)
    rows = result.scalars().all()
    await _fill_favorite_counts(db, rows)
    return rows


@router.get("/products/{product_id}", response_model=ProductOut)
async def admin_get_product(
    product_id: int,
    request: Request,
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """单个商品详情（后台，**不限状态**）

    与前台 `/api/products/{id}` 不同：草稿 / 已下架 / 待审核 / 已驳回 / 回收站商品
    都能取到，供后台「预览」「编辑页加载」使用。
    """
    product = (
        await db.execute(
            select(Product)
            .options(selectinload(Product.skus))
            .where(Product.id == product_id)
        )
    ).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")

    product.display_name = product.name(get_lang(request))
    return product


@router.post("/products", response_model=ProductOut, status_code=201)
async def admin_create_product(
    payload: ProductCreateIn,
    admin: AdminUser = Depends(require_admin({"superadmin", "operator"})),
    db: AsyncSession = Depends(get_db),
):
    """新增商品（使用 Pydantic 校验，避免裸 dict 传入非法字段导致 Decimal 异常）"""
    # 校验分类存在
    if payload.category_id is not None:
        cat_exists = await db.execute(
            select(Category).where(Category.id == payload.category_id)
        )
        if not cat_exists.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="分类不存在")

    product = Product(
        sku_code=payload.sku_code,
        brand=payload.brand,
        weight_kg=payload.weight_kg,
        name_i18n={"zh": payload.name_zh, "en": payload.name_en or ""},
        description_i18n={"zh": payload.description_zh, "en": payload.description_en or ""},
        category_id=payload.category_id,
        main_image=payload.main_image,
        images=payload.images,
        base_price=payload.base_price,
        status=payload.status,
        is_featured=payload.is_featured,
        # 后台人工录入的视为已审核；批量导入的会显式设为 pending
        source="manual",
        review_status="approved",
        reviewed_by=admin.username,
        reviewed_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(product)
    await db.flush()
    log_action(
        db, product, "create",
        {"sku_code": product.sku_code, "base_price": str(product.base_price),
         "status": product.status, "category_id": product.category_id,
         "sku_count": len(payload.skus)},
        admin.username,
    )

    for sku_data in payload.skus:
        db.add(
            SKU(
                product_id=product.id,
                sku_code=sku_data.get("sku_code") or "",
                price=Decimal(str(sku_data.get("price", 0))),
                cost_price=(
                    Decimal(str(sku_data["cost_price"]))
                    if sku_data.get("cost_price") is not None else None
                ),
                stock=int(sku_data.get("stock", 0)),
                attributes=sku_data.get("attributes", {}),
                is_active=sku_data.get("is_active", True),
            )
        )
    await db.commit()

    result = await db.execute(
        select(Product).options(selectinload(Product.skus)).where(Product.id == product.id)
    )
    return result.scalar_one()


@router.put("/products/{product_id}", response_model=ProductOut)
async def admin_update_product(
    product_id: int,
    payload: ProductUpdateIn,
    admin: AdminUser = Depends(require_admin({"superadmin", "operator"})),
    db: AsyncSession = Depends(get_db),
):
    """更新商品（文字/图片/价格/状态等；None 字段表示不修改）"""
    result = await db.execute(
        select(Product).options(selectinload(Product.skus)).where(Product.id == product_id)
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")

    if "category_id" in payload.model_fields_set and payload.category_id is not None:
        cat_exists = await db.execute(select(Category).where(Category.id == payload.category_id))
        if not cat_exists.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="分类不存在")
        product.category_id = payload.category_id
    elif "category_id" in payload.model_fields_set:
        product.category_id = None

    if payload.name_zh is not None or payload.name_en is not None:
        name = dict(product.name_i18n or {})
        if payload.name_zh is not None:
            name["zh"] = payload.name_zh
        if payload.name_en is not None:
            name["en"] = payload.name_en
        product.name_i18n = name

    if "brand" in payload.model_fields_set:
        product.brand = payload.brand or None
    if "weight_kg" in payload.model_fields_set:
        product.weight_kg = payload.weight_kg

    if payload.description_zh is not None or payload.description_en is not None:
        desc = dict(product.description_i18n or {})
        if payload.description_zh is not None:
            desc["zh"] = payload.description_zh
        if payload.description_en is not None:
            desc["en"] = payload.description_en
        product.description_i18n = desc

    if "main_image" in payload.model_fields_set:
        product.main_image = payload.main_image or None
    if "images" in payload.model_fields_set:
        product.images = [i for i in payload.images if i]
    if payload.base_price is not None:
        product.base_price = payload.base_price
    if payload.status is not None:
        product.status = payload.status
    if payload.is_featured is not None:
        product.is_featured = payload.is_featured

    # 留痕：只记录本次请求实际提交的字段
    changed = {
        k: str(v) for k, v in payload.model_dump(exclude_unset=True).items()
    }
    if changed:
        log_action(db, product, "update", {"fields": changed}, admin.username)

    await db.commit()
    await db.refresh(product)
    return product


@router.post("/products/{product_id}/skus", status_code=201)
async def admin_add_sku(
    product_id: int,
    payload: SKUIn,
    admin: AdminUser = Depends(require_admin({"superadmin", "operator"})),
    db: AsyncSession = Depends(get_db),
):
    """为商品新增 SKU

    `sku_code` 全局唯一。若该商品下已存在同编码的 SKU（例如「删规格后重新添加同名规格」
    导致的停用残留），则**复用该记录**（恢复启用并更新价格/库存），避免唯一约束 500。
    """
    product = (
        await db.execute(
            select(Product).options(selectinload(Product.skus)).where(Product.id == product_id)
        )
    ).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")

    # 本商品内同编码：复用（含已停用的残留记录）
    for existing in (product.skus or []):
        if existing.sku_code == payload.sku_code:
            existing.price = payload.price
            existing.cost_price = payload.cost_price
            existing.stock = payload.stock
            existing.attributes = payload.attributes
            existing.is_active = payload.is_active
            await db.commit()
            return {"id": existing.id, "sku_code": existing.sku_code, "reused": True}

    # 同编码被**其他商品**占用：明确报错，避免 500
    taken = (
        await db.execute(select(SKU).where(SKU.sku_code == payload.sku_code))
    ).scalar_one_or_none()
    if taken:
        raise HTTPException(
            status_code=400,
            detail=f"规格编码 {payload.sku_code} 已被其他商品占用，请改用其他编码",
        )

    sku = SKU(
        product_id=product_id,
        sku_code=payload.sku_code,
        price=payload.price,
        cost_price=payload.cost_price,
        stock=payload.stock,
        attributes=payload.attributes,
        is_active=payload.is_active,
    )
    db.add(sku)
    await db.commit()
    return {"id": sku.id, "sku_code": sku.sku_code}


@router.put("/skus/{sku_id}", response_model=SKUOut)
async def admin_update_sku(
    sku_id: int,
    payload: SKUIn,
    admin: AdminUser = Depends(require_admin({"superadmin", "operator"})),
    db: AsyncSession = Depends(get_db),
):
    """更新 SKU（价格/库存/规格属性等）"""
    result = await db.execute(select(SKU).where(SKU.id == sku_id))
    sku = result.scalar_one_or_none()
    if not sku:
        raise HTTPException(status_code=404, detail="SKU 不存在")

    if payload.sku_code and payload.sku_code != sku.sku_code:
        # 编码全局唯一：被本商品/其他商品的其它 SKU 占用时明确报错，避免唯一约束 500
        conflict = (
            await db.execute(
                select(SKU).where(SKU.sku_code == payload.sku_code, SKU.id != sku.id)
            )
        ).scalar_one_or_none()
        if conflict:
            if conflict.product_id == sku.product_id:
                # 同一商品内与本 SKU 属性重合 → 视为重复规格
                raise HTTPException(
                    status_code=400,
                    detail=f"规格编码 {payload.sku_code} 在本商品中已存在",
                )
            raise HTTPException(
                status_code=400,
                detail=f"规格编码 {payload.sku_code} 已被其他商品占用",
            )
        sku.sku_code = payload.sku_code
    if payload.price is not None:
        sku.price = payload.price
    if payload.cost_price is not None:
        sku.cost_price = payload.cost_price
    if payload.stock is not None:
        delta = payload.stock - sku.stock
        sku.stock = payload.stock
        if delta and payload.stock:
            db.add(
                StockMovement(
                    sku_id=sku.id,
                    change_qty=delta,
                    balance_after=payload.stock,
                    reason="admin_update_sku",
                    reference="",
                    operator=admin.username,
                )
            )
    sku.attributes = payload.attributes
    sku.is_active = payload.is_active

    await db.commit()
    await db.refresh(sku)
    return sku


@router.put("/skus/{sku_id}/stock")
async def admin_update_stock(
    sku_id: int,
    payload: dict,
    admin: AdminUser = Depends(require_admin({"superadmin", "operator"})),
    db: AsyncSession = Depends(get_db),
):
    """调整 SKU 库存"""
    result = await db.execute(select(SKU).where(SKU.id == sku_id))
    sku = result.scalar_one_or_none()
    if not sku:
        raise HTTPException(status_code=404, detail="SKU 不存在")

    new_stock = int(payload.get("stock", sku.stock))
    delta = new_stock - sku.stock
    sku.stock = new_stock
    db.add(
        StockMovement(
            sku_id=sku.id,
            change_qty=delta,
            balance_after=new_stock,
            reason=payload.get("reason", "admin_adjust"),
            reference="",
            operator=admin.username,
        )
    )
    await db.commit()
    return {"sku_id": sku.id, "stock": sku.stock, "available_stock": sku.available_stock}


@router.get("/orders", response_model=list[OrderOut])
async def admin_list_orders(
    request: Request,
    status: str | None = None,
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """订单列表（后台）"""
    stmt = select(Order).options(
        selectinload(Order.items), selectinload(Order.payments)
    ).order_by(Order.id.desc())
    if status and status in OrderStatus._value2member_map_:
        stmt = stmt.where(Order.status == OrderStatus(status))
    result = await db.execute(stmt)
    return [_order_to_out(o, get_lang(request)) for o in result.scalars().all()]


@router.post("/orders/{order_no}/ship", response_model=Message)
async def admin_ship_order(
    order_no: str,
    admin: AdminUser = Depends(require_admin({"superadmin", "operator"})),
    db: AsyncSession = Depends(get_db),
):
    """发货"""
    result = await db.execute(select(Order).where(Order.order_no == order_no))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.status != OrderStatus.PAID:
        raise HTTPException(status_code=400, detail="仅已支付订单可发货")

    order.status = OrderStatus.SHIPPED
    order.shipped_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    return Message(message="已发货")


@router.get("/categories", response_model=list[CategoryOut])
async def admin_list_categories(
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """类目列表（含派生统计：关联商品数 / 子类目数 / 父类目名）

    后台「类目管理」表与独立编辑页共用此接口。
    """
    cats = list(
        (await db.execute(select(Category).order_by(Category.sort_order, Category.id)))
        .scalars()
        .all()
    )
    await _fill_category_stats(db, cats)
    return cats


async def _fill_category_stats(db: AsyncSession, cats: list[Category]) -> None:
    """批量填充类目派生统计（避免 N+1 查询）"""
    if not cats:
        return

    # 商品数：按 category_id 分组，只统计未进回收站的
    rows = (
        await db.execute(
            select(Product.category_id, func.count(Product.id))
            .where(Product.deleted_at.is_(None))
            .group_by(Product.category_id)
        )
    ).all()
    by_cat = {cid: int(n or 0) for cid, n in rows}

    # 上架中的商品数（前台可见口径：status=active 且已过审）
    active_rows = (
        await db.execute(
            select(Product.category_id, func.count(Product.id))
            .where(
                Product.deleted_at.is_(None),
                Product.status == "active",
                Product.review_status == "approved",
            )
            .group_by(Product.category_id)
        )
    ).all()
    active_by_cat = {cid: int(n or 0) for cid, n in active_rows}

    # 子类目数
    child_rows = (
        await db.execute(
            select(Category.parent_id, func.count(Category.id))
            .where(Category.parent_id.is_not(None))
            .group_by(Category.parent_id)
        )
    ).all()
    child_by_cat = {cid: int(n or 0) for cid, n in child_rows}

    # 父类目名：父类目可能不在传入的列表里（单条详情场景），因此单独查一遍
    parent_ids = {c.parent_id for c in cats if c.parent_id}
    label_by_id: dict[int, str] = {}
    if parent_ids:
        parent_rows = (
            await db.execute(
                select(Category.id, Category.name_i18n, Category.code)
                .where(Category.id.in_(parent_ids))
            )
        ).all()
        for pid, name_i18n, code in parent_rows:
            label_by_id[pid] = (name_i18n or {}).get("zh") or code

    for c in cats:
        c.product_count = by_cat.get(c.id, 0)
        c.active_product_count = active_by_cat.get(c.id, 0)
        c.children_count = child_by_cat.get(c.id, 0)
        c.parent_name = label_by_id.get(c.parent_id, "") if c.parent_id else ""


@router.get("/categories/{category_id}", response_model=CategoryOut)
async def admin_get_category(
    category_id: int,
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """单个类目详情（供独立编辑页加载，含统计字段）"""
    cat = (
        await db.execute(select(Category).where(Category.id == category_id))
    ).scalar_one_or_none()
    if not cat:
        raise HTTPException(status_code=404, detail="分类不存在")
    await _fill_category_stats(db, [cat])
    return cat


@router.post("/categories", response_model=CategoryOut, status_code=201)
async def admin_create_category(
    payload: dict,
    admin: AdminUser = Depends(require_admin({"superadmin", "operator"})),
    db: AsyncSession = Depends(get_db),
):
    """新增类目（兼容两种 body：扁平字段 `name_zh` 或 `name_i18n` 字典）"""
    code = str(payload.get("code") or "").strip().lower()
    name_i18n = payload.get("name_i18n")
    if not isinstance(name_i18n, dict):
        name_i18n = {
            "zh": str(payload.get("name_zh") or "").strip(),
            "en": str(payload.get("name_en") or "").strip(),
        }
    if not name_i18n.get("zh"):
        raise HTTPException(status_code=400, detail="分类编码和中文名称不能为空")
    if not code:
        raise HTTPException(status_code=400, detail="分类编码和中文名称不能为空")
    name_i18n["en"] = name_i18n.get("en") or name_i18n["zh"]

    existing = await db.execute(select(Category).where(Category.code == code))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="分类编码已存在")

    parent_id = payload.get("parent_id")
    if parent_id is not None:
        ok = (
            await db.execute(select(Category.id).where(Category.id == parent_id))
        ).scalar_one_or_none()
        if ok is None:
            raise HTTPException(status_code=400, detail="父类目不存在")
        # 防止形成环：新建时父类目不可能等于自身
        parent_id = int(parent_id)

    cat = Category(
        code=code,
        parent_id=parent_id,
        name_i18n=name_i18n,
        sort_order=int(payload.get("sort_order") or 0),
        is_active=bool(payload.get("is_active", True)),
    )
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    await _fill_category_stats(db, [cat])
    return cat


@router.put("/categories/{category_id}", response_model=CategoryOut)
async def admin_update_category(
    category_id: int,
    payload: dict,
    admin: AdminUser = Depends(require_admin({"superadmin", "operator"})),
    db: AsyncSession = Depends(get_db),
):
    """编辑类目（独立编辑页一次性提交全部字段）"""
    result = await db.execute(select(Category).where(Category.id == category_id))
    category = result.scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=404, detail="分类不存在")

    if "code" in payload:
        code = str(payload["code"] or "").strip().lower()
        if not code:
            raise HTTPException(status_code=400, detail="分类编码不能为空")
        duplicate = await db.execute(
            select(Category).where(Category.code == code, Category.id != category_id)
        )
        if duplicate.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="分类编码已存在")
        category.code = code

    name_i18n = payload.get("name_i18n")
    if not isinstance(name_i18n, dict) and ("name_zh" in payload):
        name_i18n = {
            "zh": str(payload.get("name_zh") or "").strip(),
            "en": str(payload.get("name_en") or "").strip(),
        }
    if isinstance(name_i18n, dict):
        if not name_i18n.get("zh"):
            raise HTTPException(status_code=400, detail="中文名称不能为空")
        name_i18n["en"] = name_i18n.get("en") or name_i18n["zh"]
        category.name_i18n = name_i18n

    if "parent_id" in payload and payload["parent_id"] is not None:
        parent_id = int(payload["parent_id"])
        if parent_id == category_id:
            raise HTTPException(status_code=400, detail="父类目不能是自己")
        # 防环：父类目不能是自己的后代
        descendants = await _category_descendant_ids(db, category_id)
        if parent_id in descendants:
            raise HTTPException(status_code=400, detail="父类目不能是自己的子类目")

    for field in ("parent_id", "sort_order", "is_active"):
        if field in payload:
            setattr(category, field, payload[field])

    await db.commit()
    await db.refresh(category)
    await _fill_category_stats(db, [category])
    return category


async def _category_descendant_ids(db: AsyncSession, category_id: int) -> set[int]:
    """收集某类目的全部后代 ID（用于父类目防环校验）"""
    children = (
        await db.execute(
            select(Category.id, Category.parent_id).where(Category.parent_id.is_not(None))
        )
    ).all()
    by_parent: dict[int, list[int]] = {}
    for cid, pid in children:
        by_parent.setdefault(pid, []).append(cid)

    out: set[int] = set()
    stack = list(by_parent.get(category_id, []))
    while stack:
        cur = stack.pop()
        if cur in out:
            continue
        out.add(cur)
        stack.extend(by_parent.get(cur, []))
    return out


@router.delete("/categories/{category_id}", response_model=Message)
async def admin_delete_category(
    category_id: int,
    admin: AdminUser = Depends(require_admin({"superadmin", "operator"})),
    db: AsyncSession = Depends(get_db),
):
    """删除类目。

    阻止条件：该类目下仍有商品，或仍有子类目（避免留下孤儿节点）。
    """
    result = await db.execute(select(Category).where(Category.id == category_id))
    category = result.scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=404, detail="分类不存在")

    used = await db.execute(
        select(Product.id).where(Product.category_id == category_id).limit(1)
    )
    if used.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="该分类仍有关联商品，不能删除")

    child = await db.execute(
        select(Category.id).where(Category.parent_id == category_id).limit(1)
    )
    if child.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="该类目下仍有子类目，请先删除子类目")

    await db.delete(category)
    await db.commit()
    return Message(message="分类已删除")


@router.get("/stock-movements")
async def stock_movements(
    limit: int = Query(50, ge=1, le=200),
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(StockMovement).order_by(StockMovement.id.desc()).limit(limit)
    )
    return result.scalars().all()


# ---------- 评价审核 ----------
@router.get("/reviews", response_model=list[dict])
async def admin_list_reviews(
    request: Request,
    status: str | None = None,
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """评价列表（最新优先，可按状态过滤 pending/approved/rejected）"""
    stmt = (
        select(Review)
        .options(selectinload(Review.customer), selectinload(Review.product))
        .order_by(Review.id.desc())
    )
    if status and status in ReviewStatus._value2member_map_:
        stmt = stmt.where(Review.status == ReviewStatus(status))
    result = await db.execute(stmt)
    out = []
    for r in result.scalars().all():
        out.append(
            {
                "id": r.id,
                "product_id": r.product_id,
                "product_name": r.product.name(get_lang(request)) if r.product else "",
                "customer_id": r.customer_id,
                "customer_name": (
                    (r.customer.full_name or r.customer.email)
                    if r.customer else "匿名"
                ),
                "rating": r.rating,
                "title": r.title,
                "content": r.content,
                "status": r.status.value,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    return out


@router.post("/reviews/{review_id}/moderate")
async def moderate_review(
    review_id: int,
    payload: dict,
    admin: AdminUser = Depends(require_admin({"superadmin", "operator"})),
    db: AsyncSession = Depends(get_db),
):
    """审核评价：approved 通过 / rejected 拒绝"""
    result = await db.execute(select(Review).where(Review.id == review_id))
    review = result.scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=404, detail="评价不存在")

    new_status = payload.get("status", "")
    if new_status not in ReviewStatus._value2member_map_:
        raise HTTPException(status_code=400, detail="非法状态")
    review.status = ReviewStatus(new_status)
    await db.commit()
    return {"id": review.id, "status": review.status.value}


# ============ 客户管理（1000） ============
@router.get("/customers", response_model=list[AdminCustomerOut])
async def admin_list_customers(
    q: str | None = None,
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """客户列表（含订单数、累计消费）"""
    stmt = select(Customer).order_by(Customer.id.desc())
    if q:
        stmt = stmt.where(
            (Customer.email.ilike(f"%{q}%"))
            | (Customer.full_name.ilike(f"%{q}%"))
            | (Customer.phone.ilike(f"%{q}%"))
        )
    customers = (await db.execute(stmt)).scalars().all()

    # 统计每个客户的订单数与消费额
    stats = (
        await db.execute(
            select(
                Customer.id,
                func.count(Order.id),
                func.coalesce(func.sum(Order.total_amount), 0),
            )
            .outerjoin(Order, Order.customer_id == Customer.id)
            .where(
                Order.status.in_([OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.COMPLETED])
                | Order.status.is_(None)
            )
            .group_by(Customer.id)
        )
    ).all()
    stat_map = {cid: (cnt, total) for cid, cnt, total in stats}

    out = []
    for c in customers:
        cnt, total = stat_map.get(c.id, (0, Decimal("0")))
        item = AdminCustomerOut.model_validate(c)
        item.orders_count = int(cnt)
        item.total_spent = total or Decimal("0")
        out.append(item)
    return out


@router.put("/customers/{customer_id}/status", response_model=AdminCustomerOut)
async def admin_toggle_customer(
    customer_id: int,
    payload: dict,
    admin: AdminUser = Depends(require_admin({"superadmin", "operator"})),
    db: AsyncSession = Depends(get_db),
):
    """启用/禁用客户账号"""
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="客户不存在")
    is_active = payload.get("is_active")
    if not isinstance(is_active, bool):
        raise HTTPException(status_code=400, detail="is_active 必须为布尔值")
    customer.is_active = is_active
    await db.commit()
    await db.refresh(customer)
    item = AdminCustomerOut.model_validate(customer)
    item.orders_count = 0
    item.total_spent = Decimal("0")
    return item


@router.delete("/customers/{customer_id}", response_model=Message)
async def admin_delete_customer(
    customer_id: int,
    admin: AdminUser = Depends(require_admin({"superadmin"})),
    db: AsyncSession = Depends(get_db),
):
    """删除客户（级联清理其购物车、收藏、地址、评价）"""
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="客户不存在")
    # 订单表外键为 SET NULL，删除客户不会删除订单，先手工解除（保留订单记录）
    await db.execute(
        CartItem.__table__.delete().where(CartItem.customer_id == customer_id)
    )
    await db.execute(
        WishlistItem.__table__.delete().where(WishlistItem.customer_id == customer_id)
    )
    await db.execute(
        Address.__table__.delete().where(Address.customer_id == customer_id)
    )
    await db.execute(
        Review.__table__.delete().where(Review.customer_id == customer_id)
    )
    await db.execute(
        Order.__table__.update().where(Order.customer_id == customer_id).values(customer_id=None)
    )
    await db.delete(customer)
    await db.commit()
    return Message(message="客户已删除")


# ============ 商品删除 ============
@router.delete("/products/{product_id}", response_model=Message)
async def admin_delete_product(
    product_id: int,
    admin: AdminUser = Depends(require_admin({"superadmin", "operator"})),
    db: AsyncSession = Depends(get_db),
):
    """删除商品（**软删除**，移入回收站，可在后台恢复）

    如需不可恢复的抹除，请调用 `DELETE /api/admin/products/{id}/purge`（仅超管）。
    """
    result = await db.execute(
        select(Product).options(selectinload(Product.skus)).where(Product.id == product_id)
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    if product.deleted_at is not None:
        return Message(message="商品已在回收站中")

    product.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    # 留痕（回收站商品仍可恢复，故此处不解除外键）
    log_action(
        db, product, "delete",
        {"sku_code": product.sku_code, "status": product.status,
         "review_status": product.review_status, "soft": True},
        admin.username,
    )
    await db.commit()
    return Message(message="商品已移入回收站")


# ============ 订单详情 ============
@router.get("/orders/{order_no}", response_model=OrderOut)
async def admin_order_detail(
    order_no: str,
    request: Request,
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """订单详情（后台）"""
    result = await db.execute(
        select(Order)
        .options(
            selectinload(Order.items),
            selectinload(Order.payments),
            selectinload(Order.customer),
        )
        .where(Order.order_no == order_no)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    return _order_to_out(order, get_lang(request))


# ============ 管理员账号管理（仅 superadmin） ============
@router.get("/admins", response_model=list[AdminUserOut])
async def admin_list_admins(
    admin: AdminUser = Depends(require_admin({"superadmin"})),
    db: AsyncSession = Depends(get_db),
):
    """管理员列表（超管专属）"""
    result = await db.execute(select(AdminUser).order_by(AdminUser.id))
    return result.scalars().all()


@router.post("/admins", response_model=AdminUserOut, status_code=201)
async def admin_create_admin(
    payload: AdminUserIn,
    admin: AdminUser = Depends(require_admin({"superadmin"})),
    db: AsyncSession = Depends(get_db),
):
    """新增管理员（超管专属）"""
    if payload.role not in AdminRole._value2member_map_:
        raise HTTPException(status_code=400, detail="非法角色")
    existing = await db.execute(select(AdminUser).where(AdminUser.username == payload.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = AdminUser(
        username=payload.username,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=AdminRole(payload.role),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.put("/me/password", response_model=Message)
async def admin_change_my_password(
    payload: AdminPasswordChangeIn,
    current: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """修改**当前登录账号**的密码。

    必须提供 `old_password` 校验身份，防止会话被劫持后直接改密。
    超管与普通管理员都可用；超管改别人的密码走 `PUT /admins/{id}`。
    """
    if not verify_password(payload.old_password, current.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if payload.old_password == payload.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")

    current.password_hash = hash_password(payload.new_password)
    await db.commit()
    logger.info("[admin] 管理员 %s 修改了自己的密码", current.username)
    return Message(message="密码已修改，下次登录请使用新密码")


@router.put("/admins/{admin_id}/password", response_model=Message)
async def admin_reset_admin_password(
    admin_id: int,
    payload: AdminPasswordResetIn,
    current: AdminUser = Depends(require_admin({"superadmin"})),
    db: AsyncSession = Depends(get_db),
):
    """超级管理员重置**他人**密码（无需对方原密码）。

    不允许通过本接口改自己的密码——那会绕过当前密码校验，
    改自己请走 `PUT /me/password`。
    """
    if admin_id == current.id:
        raise HTTPException(
            status_code=400,
            detail="修改自己的密码请使用「修改密码」（需验证当前密码）",
        )
    result = await db.execute(select(AdminUser).where(AdminUser.id == admin_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="管理员不存在")

    user.password_hash = hash_password(payload.new_password)
    await db.commit()
    logger.info(
        "[admin] %s 重置了 %s 的密码", current.username, user.username
    )
    return Message(message=f"已重置 {user.username} 的密码")


@router.put("/admins/{admin_id}", response_model=AdminUserOut)
async def admin_update_admin(
    admin_id: int,
    payload: AdminUserUpdateIn,
    current: AdminUser = Depends(require_admin({"superadmin"})),
    db: AsyncSession = Depends(get_db),
):
    """修改管理员（角色/姓名/状态/重置密码），超管专属且不能禁用自己的超管"""
    result = await db.execute(select(AdminUser).where(AdminUser.id == admin_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="管理员不存在")

    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.role is not None:
        if payload.role not in AdminRole._value2member_map_:
            raise HTTPException(status_code=400, detail="非法角色")
        user.role = AdminRole(payload.role)
    if payload.is_active is not None:
        # 防止把自己的超管账号禁用
        if user.id == current.id and not payload.is_active:
            raise HTTPException(status_code=400, detail="不能禁用当前登录的超管账号")
        user.is_active = payload.is_active
    if payload.password:
        # 改自己的密码必须验证原密码，走 PUT /me/password
        if user.id == current.id:
            raise HTTPException(
                status_code=400,
                detail="修改自己的密码请使用「修改密码」（需验证当前密码）",
            )
        user.password_hash = hash_password(payload.password)

    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/admins/{admin_id}", response_model=Message)
async def admin_delete_admin(
    admin_id: int,
    current: AdminUser = Depends(require_admin({"superadmin"})),
    db: AsyncSession = Depends(get_db),
):
    """删除管理员（不能删除自己）"""
    if admin_id == current.id:
        raise HTTPException(status_code=400, detail="不能删除当前登录账号")
    result = await db.execute(select(AdminUser).where(AdminUser.id == admin_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="管理员不存在")
    await db.delete(user)
    await db.commit()
    return Message(message="管理员已删除")