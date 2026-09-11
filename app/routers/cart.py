"""购物车路由：增删改查"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import get_current_customer
from app.i18n import get_lang
from app.models import CartItem, Customer, Product, SKU
from app.schemas import CartAddIn, CartItemOut, CartOut, CartUpdateIn, Message

router = APIRouter(prefix="/api/cart", tags=["cart"])


async def _build_cart(customer_id: int, db: AsyncSession, lang: str) -> CartOut:
    """构建购物车响应数据"""
    result = await db.execute(
        select(CartItem)
        .options(selectinload(CartItem.sku).selectinload(SKU.product))
        .where(CartItem.customer_id == customer_id)
        .order_by(CartItem.id.desc())
    )
    items = result.scalars().all()

    out_items: list[CartItemOut] = []
    total = Decimal("0")
    for item in items:
        sku = item.sku
        product = sku.product
        unit_price = sku.price
        subtotal = unit_price * item.quantity
        total += subtotal
        out_items.append(
            CartItemOut(
                id=item.id,
                sku_id=sku.id,
                quantity=item.quantity,
                sku_code=sku.sku_code,
                product_name=product.name(lang),
                sku_spec=sku.attributes or {},
                image=product.main_image,
                unit_price=unit_price,
                subtotal=subtotal,
                available_stock=sku.available_stock,
            )
        )
    return CartOut(items=out_items, total_amount=total)


@router.get("", response_model=CartOut)
async def get_cart(
    request: Request,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """获取当前客户购物车"""
    return await _build_cart(customer.id, db, get_lang(request))


@router.post("/items", response_model=CartOut, status_code=201)
async def add_to_cart(
    payload: CartAddIn,
    request: Request,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """加入购物车"""
    sku_result = await db.execute(
        select(SKU).options(selectinload(SKU.product)).where(SKU.id == payload.sku_id)
    )
    sku = sku_result.scalar_one_or_none()
    if not sku or not sku.is_active:
        raise HTTPException(status_code=404, detail="商品规格不存在")

    # 检查库存
    existing = await db.execute(
        select(CartItem).where(
            CartItem.customer_id == customer.id, CartItem.sku_id == payload.sku_id
        )
    )
    item = existing.scalar_one_or_none()
    if item:
        new_qty = item.quantity + payload.quantity
        if new_qty > sku.available_stock:
            raise HTTPException(status_code=400, detail="库存不足")
        item.quantity = new_qty
    else:
        if payload.quantity > sku.available_stock:
            raise HTTPException(status_code=400, detail="库存不足")
        db.add(CartItem(customer_id=customer.id, sku_id=payload.sku_id, quantity=payload.quantity))

    await db.commit()
    return await _build_cart(customer.id, db, get_lang(request))


@router.put("/items/{item_id}", response_model=CartOut)
async def update_cart_item(
    item_id: int,
    payload: CartUpdateIn,
    request: Request,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """修改购物车条目数量"""
    result = await db.execute(
        select(CartItem)
        .options(selectinload(CartItem.sku))
        .where(CartItem.id == item_id, CartItem.customer_id == customer.id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="购物车条目不存在")

    if payload.quantity > item.sku.available_stock:
        raise HTTPException(status_code=400, detail="库存不足")
    item.quantity = payload.quantity
    await db.commit()
    return await _build_cart(customer.id, db, get_lang(request))


@router.delete("/items/{item_id}", response_model=CartOut)
async def remove_cart_item(
    item_id: int,
    request: Request,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """删除购物车条目"""
    result = await db.execute(
        select(CartItem).where(CartItem.id == item_id, CartItem.customer_id == customer.id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="购物车条目不存在")
    await db.delete(item)
    await db.commit()
    return await _build_cart(customer.id, db, get_lang(request))


@router.delete("", response_model=Message)
async def clear_cart(
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """清空购物车"""
    from sqlalchemy import delete

    await db.execute(delete(CartItem).where(CartItem.customer_id == customer.id))
    await db.commit()
    return Message(message="购物车已清空")