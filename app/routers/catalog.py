"""商品目录路由：分类、商品列表、详情、搜索"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.i18n import get_lang
from app.models import Category, Product, WishlistItem
from app.schemas import CategoryOut, ProductDetailOut, ProductListOut

router = APIRouter(prefix="/api", tags=["catalog"])


def _name_like(kw: str):
    """构造商品多语言名称的模糊匹配条件。

    说明：name_i18n 是 JSON 列（如 {"zh": "智能手机 Pro"}），PostgreSQL 中
    json::text 结果带前导引号（"{\"zh\": \"智能手机 Pro\"}"），直接用 ilike('%kw%')
    无法匹配中文。将 JSON 转为 jsonb 后通过 #>> 运算符取出 zh/en 字段再匹配。
    """
    from sqlalchemy.dialects.postgresql import JSONB

    pattern = f"%{kw}%"
    name_jsonb = Product.name_i18n.cast(JSONB)
    return or_(
        name_jsonb["zh"].astext.ilike(pattern),
        name_jsonb["en"].astext.ilike(pattern),
        Product.sku_code.ilike(pattern),
    )


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(db: AsyncSession = Depends(get_db)):
    """获取所有启用分类"""
    result = await db.execute(
        select(Category)
        .where(Category.is_active.is_(True))
        .order_by(Category.sort_order, Category.id)
    )
    return result.scalars().all()


@router.get("/products", response_model=list[ProductListOut])
async def list_products(
    request: Request,
    category_id: int | None = None,
    q: str | None = None,
    featured: bool | None = None,
    min_price: Decimal | None = None,
    max_price: Decimal | None = None,
    sort_by: str = "default",  # default | price_asc | price_desc | sales_desc | favorites_desc | newest
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """商品列表，支持分类、关键字搜索、推荐过滤、价格区间、排序和分页"""
    favorite_count = func.count(WishlistItem.id).label("favorite_count")
    stmt = (
        select(Product, favorite_count)
        .outerjoin(WishlistItem, WishlistItem.product_id == Product.id)
        .where(Product.status == "active")
        .group_by(Product.id)
    )

    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    if featured is not None:
        stmt = stmt.where(Product.is_featured.is_(featured))
    if q:
        stmt = stmt.where(_name_like(q))
    # 价格区间（基于基础价）
    if min_price is not None:
        stmt = stmt.where(Product.base_price >= min_price)
    if max_price is not None:
        stmt = stmt.where(Product.base_price <= max_price)

    # 排序
    if sort_by == "price_asc":
        stmt = stmt.order_by(Product.base_price.asc(), Product.id.desc())
    elif sort_by == "price_desc":
        stmt = stmt.order_by(Product.base_price.desc(), Product.id.desc())
    elif sort_by == "sales_desc":
        stmt = stmt.order_by(Product.sales_count.desc(), Product.id)
    elif sort_by == "favorites_desc":
        stmt = stmt.order_by(favorite_count.desc(), Product.id.desc())
    elif sort_by == "newest":
        stmt = stmt.order_by(Product.created_at.desc(), Product.id.desc())
    else:
        stmt = stmt.order_by(Product.is_featured.desc(), Product.id.desc())

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    rows = result.all()
    products = []
    for product, count in rows:
        product.favorite_count = int(count or 0)
        products.append(product)

    # 多语言名称
    lang = get_lang(request)
    for p in products:
        p.display_name = p.name(lang)
    return products


@router.get("/products/autocomplete")
async def autocomplete(
    q: str = Query("", min_length=1, max_length=100),
    limit: int = Query(8, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    """搜索建议（autocomplete）：按名称/关键字匹配返回候选"""
    if not q.strip():
        return []
    result = await db.execute(
        select(Product)
        .where(Product.status == "active", _name_like(q.strip()))
        .order_by(Product.is_featured.desc(), Product.sales_count.desc())
        .limit(limit)
    )
    return [
        {"id": p.id, "sku_code": p.sku_code, "name_zh": p.name_i18n.get("zh", ""),
         "name_en": p.name_i18n.get("en", ""), "base_price": str(p.base_price),
         "main_image": p.main_image}
        for p in result.scalars().all()
    ]


@router.get("/products/{product_id}", response_model=ProductDetailOut)
async def product_detail(
    product_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """商品详情（含 SKU 列表）"""
    result = await db.execute(
        select(Product)
        .options(selectinload(Product.skus))
        .where(Product.id == product_id, Product.status == "active")
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")

    lang = get_lang(request)
    product.display_name = product.name(lang)
    return product


