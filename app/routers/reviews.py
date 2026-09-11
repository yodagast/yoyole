"""商品评价路由：前台提交/查询评价"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import get_current_customer
from app.i18n import get_lang
from app.models import Customer, Order, OrderItem, Product, Review, ReviewStatus, SKU
from app.schemas import Message, ReviewIn, ReviewOut

router = APIRouter(prefix="/api", tags=["reviews"])


def _review_to_out(review: Review, lang: str) -> ReviewOut:
    name = ""
    if review.customer:
        name = review.customer.full_name or review.customer.email.split("@")[0]
    return ReviewOut(
        id=review.id,
        product_id=review.product_id,
        customer_id=review.customer_id,
        customer_name=name,
        rating=review.rating,
        title=review.title,
        content=review.content,
        status=review.status.value,
        created_at=review.created_at,
    )


@router.get("/products/{product_id}/reviews", response_model=list[ReviewOut])
async def list_reviews(
    product_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """获取商品已通过审核的评价"""
    result = await db.execute(
        select(Review)
        .options(selectinload(Review.customer))
        .where(
            Review.product_id == product_id,
            Review.status == ReviewStatus.APPROVED,
        )
        .order_by(Review.id.desc())
    )
    return [_review_to_out(r, get_lang(request)) for r in result.scalars().all()]


@router.get("/products/{product_id}/reviewable", response_model=dict)
async def product_reviewable(
    product_id: int,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """当前用户是否可评价该商品（购买过该商品且未评价过）"""
    ordered = await db.execute(
        select(OrderItem)
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Order.customer_id == customer.id,
            OrderItem.sku_id.in_(select(SKU.id).where(SKU.product_id == product_id)),
        )
        .limit(1)
    )
    purchased = ordered.scalar_one_or_none() is not None
    if not purchased:
        return {"reviewable": False, "reason": "not_purchased"}

    existing = await db.execute(
        select(Review).where(
            Review.product_id == product_id, Review.customer_id == customer.id
        )
    )
    if existing.scalar_one_or_none():
        return {"reviewable": False, "reason": "already_reviewed"}

    return {"reviewable": True, "reason": "ok"}


@router.post("/products/{product_id}/reviews", response_model=ReviewOut, status_code=201)
async def create_review(
    product_id: int,
    payload: ReviewIn,
    request: Request,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """发表评价（仅限购买过该商品的用户，评价待审核）"""
    product = (await db.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")

    # 校验是否购买过
    ordered = await db.execute(
        select(OrderItem)
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Order.customer_id == customer.id,
            OrderItem.sku_id.in_(
                select(SKU.id).where(SKU.product_id == product_id)
            ),
        )
        .limit(1)
    )
    if not ordered.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="仅购买过该商品的用户可评价")

    # 每人每商品一评
    existing = await db.execute(
        select(Review).where(
            Review.product_id == product_id, Review.customer_id == customer.id
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="您已评价过该商品")

    review = Review(
        product_id=product_id,
        customer_id=customer.id,
        rating=payload.rating,
        title=payload.title,
        content=payload.content,
        status=ReviewStatus.PENDING,
    )
    db.add(review)
    await db.commit()
    await db.refresh(review)
    return _review_to_out(review, get_lang(request))


@router.get("/orders/{order_no}/reviewable", response_model=list[dict])
async def reviewable_items(
    order_no: str,
    request: Request,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """获取订单中可评价的商品（供「评价」入口使用）"""
    order = (
        await db.execute(
            select(Order)
            .options(selectinload(Order.items))
            .where(Order.order_no == order_no, Order.customer_id == customer.id)
        )
    ).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")

    # 已评价的 product_id 集合
    reviewed = await db.execute(
        select(Review.product_id).where(Review.customer_id == customer.id)
    )
    reviewed_ids = set(reviewed.scalars().all())

    items: list[dict] = []
    for it in order.items:
        product = (
            await db.execute(
                select(Product)
                .options(selectinload(Product.skus))
                .where(Product.id == (it.sku.product_id if it.sku else None))
            )
        ).scalar_one_or_none() if it.sku else None
        if not product:
            continue
        items.append(
            {
                "product_id": product.id,
                "product_name": product.name(get_lang(request)),
                "main_image": product.main_image,
                "reviewed": product.id in reviewed_ids,
            }
        )
    return items


@router.get("/my-reviews", response_model=list[ReviewOut])
async def my_reviews(
    request: Request,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """当前用户的评价列表"""
    result = await db.execute(
        select(Review)
        .options(selectinload(Review.customer))
        .where(Review.customer_id == customer.id)
        .order_by(Review.id.desc())
    )
    return [_review_to_out(r, get_lang(request)) for r in result.scalars().all()]