"""收藏路由：收藏增删查、数量、移入购物车"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import get_current_customer
from app.i18n import get_lang
from app.models import CartItem, Customer, Product, SKU, WishlistItem
from app.schemas import Message, WishlistAddIn, WishlistItemOut, WishlistOut

router = APIRouter(prefix="/api/wishlist", tags=["wishlist"])


def _item_to_out(item: WishlistItem, lang: str) -> WishlistItemOut:
    p = item.product
    return WishlistItemOut(
        id=item.id,
        product_id=p.id,
        product_name=p.name(lang),
        main_image=p.main_image,
        base_price=p.base_price,
        created_at=item.created_at,
    )


async def _build_wishlist(customer_id: int, db: AsyncSession, lang: str) -> WishlistOut:
    result = await db.execute(
        select(WishlistItem)
        .options(selectinload(WishlistItem.product))
        .where(WishlistItem.customer_id == customer_id)
        .order_by(WishlistItem.id.desc())
    )
    items = result.scalars().all()
    return WishlistOut(
        items=[_item_to_out(it, lang) for it in items],
        total=len(items),
        product_ids=[it.product_id for it in items],
    )


@router.get("", response_model=WishlistOut)
async def get_wishlist(
    request: Request,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """获取收藏列表（含 product_ids 便于前端高亮）"""
    return await _build_wishlist(customer.id, db, get_lang(request))


@router.post("", response_model=WishlistOut, status_code=201)
async def add_to_wishlist(
    payload: WishlistAddIn,
    request: Request,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """添加收藏"""
    product = (
        await db.execute(select(Product).where(Product.id == payload.product_id))
    ).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")

    existing = await db.execute(
        select(WishlistItem).where(
            WishlistItem.customer_id == customer.id,
            WishlistItem.product_id == payload.product_id,
        )
    )
    if existing.scalar_one_or_none():
        # 已收藏则幂等返回
        return await _build_wishlist(customer.id, db, get_lang(request))

    db.add(
        WishlistItem(customer_id=customer.id, product_id=payload.product_id)
    )
    await db.commit()
    return await _build_wishlist(customer.id, db, get_lang(request))


@router.delete("/{product_id}", response_model=WishlistOut)
async def remove_from_wishlist(
    product_id: int,
    request: Request,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """取消收藏"""
    result = await db.execute(
        select(WishlistItem).where(
            WishlistItem.customer_id == customer.id,
            WishlistItem.product_id == product_id,
        )
    )
    item = result.scalar_one_or_none()
    if item:
        await db.delete(item)
        await db.commit()
    return await _build_wishlist(customer.id, db, get_lang(request))


@router.delete("", response_model=Message)
async def clear_wishlist(
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """清空收藏"""
    await db.execute(delete(WishlistItem).where(WishlistItem.customer_id == customer.id))
    await db.commit()
    return Message(message="已清空收藏")


@router.post("/move-to-cart/{product_id}", response_model=Message)
async def move_to_cart(
    product_id: int,
    request: Request,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """将收藏商品加入购物车（取第一个有货 SKU）"""
    product = (
        await db.execute(
            select(Product).options(selectinload(Product.skus)).where(Product.id == product_id)
        )
    ).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")

    sku = next(
        (s for s in product.skus if s.is_active and s.available_stock > 0), None
    )
    if not sku:
        raise HTTPException(status_code=400, detail="商品缺货")

    existing = await db.execute(
        select(CartItem).where(
            CartItem.customer_id == customer.id, CartItem.sku_id == sku.id
        )
    )
    item = existing.scalar_one_or_none()
    if item:
        if item.quantity + 1 > sku.available_stock:
            raise HTTPException(status_code=400, detail="库存不足")
        item.quantity += 1
    else:
        db.add(CartItem(customer_id=customer.id, sku_id=sku.id, quantity=1))

    # 移入购物车后同步取消收藏
    wl_delete = await db.execute(
        delete(WishlistItem).where(
            WishlistItem.customer_id == customer.id,
            WishlistItem.product_id == product_id,
        )
    )
    if wl_delete.rowcount:
        await db.commit()
        return Message(message="已加入购物车并取消收藏")

    await db.commit()
    return Message(message="已加入购物车")