"""ERP 后台路由：管理员登录、仪表盘、商品/订单/库存管理"""
from __future__ import annotations

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
)
from app.security import create_access_token, hash_password, verify_password
from app.routers.orders import _order_to_out

router = APIRouter(prefix="/api/admin", tags=["admin"])


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


@router.get("/products", response_model=list[ProductOut])
async def admin_list_products(
    q: str | None = None,
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """商品列表（后台含下架）"""
    stmt = select(Product).options(selectinload(Product.skus)).order_by(Product.id.desc())
    if q:
        stmt = stmt.where(Product.sku_code.ilike(f"%{q}%"))
    result = await db.execute(stmt)
    return result.scalars().all()


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
    )
    db.add(product)
    await db.flush()

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
    """为商品新增 SKU"""
    result = await db.execute(select(Product).where(Product.id == product_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="商品不存在")

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
    result = await db.execute(select(Category).order_by(Category.sort_order, Category.id))
    return result.scalars().all()


@router.post("/categories", response_model=CategoryOut, status_code=201)
async def admin_create_category(
    payload: dict,
    admin: AdminUser = Depends(require_admin({"superadmin", "operator"})),
    db: AsyncSession = Depends(get_db),
):
    code = str(payload.get("code") or "").strip().lower()
    name_i18n = payload.get("name_i18n") or {"zh": "", "en": ""}
    if not code or not name_i18n.get("zh"):
        raise HTTPException(status_code=400, detail="分类编码和中文名称不能为空")
    existing = await db.execute(select(Category).where(Category.code == code))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="分类编码已存在")
    cat = Category(
        code=code,
        parent_id=payload.get("parent_id"),
        name_i18n=name_i18n,
        sort_order=int(payload.get("sort_order", 0)),
        is_active=payload.get("is_active", True),
    )
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return cat


@router.put("/categories/{category_id}", response_model=CategoryOut)
async def admin_update_category(
    category_id: int,
    payload: dict,
    admin: AdminUser = Depends(require_admin({"superadmin", "operator"})),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Category).where(Category.id == category_id))
    category = result.scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=404, detail="分类不存在")
    if "code" in payload:
        code = str(payload["code"] or "").strip().lower()
        if not code:
            raise HTTPException(status_code=400, detail="分类编码不能为空")
        duplicate = await db.execute(select(Category).where(Category.code == code, Category.id != category_id))
        if duplicate.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="分类编码已存在")
        category.code = code
    if "name_i18n" in payload:
        name_i18n = payload["name_i18n"] or {}
        if not name_i18n.get("zh"):
            raise HTTPException(status_code=400, detail="中文名称不能为空")
        category.name_i18n = name_i18n
    for field in ("parent_id", "sort_order", "is_active"):
        if field in payload:
            setattr(category, field, payload[field])
    await db.commit()
    await db.refresh(category)
    return category


@router.delete("/categories/{category_id}", response_model=Message)
async def admin_delete_category(
    category_id: int,
    admin: AdminUser = Depends(require_admin({"superadmin", "operator"})),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Category).where(Category.id == category_id))
    category = result.scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=404, detail="分类不存在")
    used = await db.execute(select(Product.id).where(Product.category_id == category_id).limit(1))
    if used.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="该分类仍有关联商品，不能删除")
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
    """删除商品（级联删除 SKU、库存流水等）"""
    result = await db.execute(
        select(Product).options(selectinload(Product.skus)).where(Product.id == product_id)
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    # 检查订单项引用（订单保存快照不引用商品，但保留友好提示）
    await db.execute(StockMovement.__table__.delete().where(
        StockMovement.sku_id.in_([s.id for s in product.skus])
    ))
    await db.delete(product)
    await db.commit()
    return Message(message="商品已删除")


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