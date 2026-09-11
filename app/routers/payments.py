"""支付处理路由：模拟支付确认、支付回调"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import (
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    PaymentStatus,
    SKU,
    StockMovement,
)
from app.payments import get_gateway

router = APIRouter(prefix="/api/payments", tags=["payments"])

_NOW = lambda: datetime.now(timezone.utc).replace(tzinfo=None)


async def _mark_payment_success(db: AsyncSession, transaction_no: str) -> Payment:
    """标记支付成功，更新订单与库存"""
    result = await db.execute(
        select(Payment)
        .options(
            # 从根实体 Payment 开始完整链式预加载：order -> items -> sku -> product
            selectinload(Payment.order)
            .selectinload(Order.items)
            .selectinload(OrderItem.sku)
            .selectinload(SKU.product),
        )
        .where(Payment.transaction_no == transaction_no)
    )
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="支付记录不存在")

    if payment.status == PaymentStatus.SUCCESS:
        return payment

    now = _NOW()
    payment.status = PaymentStatus.SUCCESS
    payment.paid_at = now

    order = payment.order
    if order.status == OrderStatus.PENDING:
        order.status = OrderStatus.PAID
        order.paid_at = now

        # 扣减真实库存，释放锁定库存，累加销量
        # 注意：订单项的 SKU/Product 已通过 selectinload 预加载，直接使用，
        # 避免在 commit 前执行额外的 select 语句。
        for item in order.items:
            sku = item.sku
            if sku:
                sku.stock = max(sku.stock - item.quantity, 0)
                sku.locked_stock = max(sku.locked_stock - item.quantity, 0)
                db.add(
                    StockMovement(
                        sku_id=sku.id,
                        change_qty=-item.quantity,
                        balance_after=sku.stock,
                        reason="order_paid",
                        reference=order.order_no,
                    )
                )
                product = sku.product
                if product:
                    product.sales_count += item.quantity

    await db.commit()
    # 刷新后仍可安全访问 payment.order（对象已在会话中）
    await db.refresh(payment)
    return payment


@router.get("/mock/confirm")
async def mock_confirm(
    txn_no: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """模拟支付确认（相当于模拟网关的收银台确认）"""
    payment = await _mark_payment_success(db, txn_no)

    lang = getattr(request.state, "lang", "zh")
    order_no = payment.order.order_no
    amount = payment.amount
    if lang == "zh":
        msg = f"支付成功！订单 {order_no}，金额 ¥{amount}"
        ok = "返回订单"
    else:
        msg = f"Payment successful! Order {order_no}, amount ¥{amount}"
        ok = "Back to Orders"
    return {
        "success": True,
        "order_no": order_no,
        "amount": str(amount),
        "message": msg,
    }


@router.post("/callback")
async def payment_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """支付网关异步回调（幂等处理）"""
    try:
        payload = await request.json()
    except Exception:
        payload = dict(request.query_params)

    method = payload.get("method", "mock")
    try:
        gateway = get_gateway(method)
    except ValueError:
        raise HTTPException(status_code=400, detail="未知支付通道")

    success, txn_no, raw = await gateway.handle_callback(payload)
    if not success or not txn_no:
        return {"success": False, "message": "回调校验失败"}

    payment = await _mark_payment_success(db, txn_no)
    return {
        "success": True,
        "order_no": payment.order.order_no,
        "status": payment.status.value,
    }


@router.get("/query/{transaction_no}")
async def query_payment(transaction_no: str, db: AsyncSession = Depends(get_db)):
    """查询支付状态"""
    result = await db.execute(
        select(Payment)
        .options(selectinload(Payment.order))
        .where(Payment.transaction_no == transaction_no)
    )
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="支付记录不存在")
    return {
        "transaction_no": payment.transaction_no,
        "order_no": payment.order.order_no,
        "method": payment.method.value,
        "amount": str(payment.amount),
        "status": payment.status.value,
    }