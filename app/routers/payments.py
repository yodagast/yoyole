"""支付处理路由：模拟支付确认、支付回调、PayPal 扣款与 webhook"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.deps import get_current_customer
from app.models import (
    Customer,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    PaymentMethod,
    PaymentStatus,
    SKU,
    StockMovement,
)
from app.payments import get_gateway, verify_paypal_webhook
from app.schemas import PayPalCaptureIn

router = APIRouter(prefix="/api/payments", tags=["payments"])
logger = logging.getLogger(__name__)

_NOW = lambda: datetime.now(timezone.utc).replace(tzinfo=None)  # noqa: E731


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


# ================= PayPal =================


@router.get("/methods")
async def payment_methods():
    """可用支付通道

    前端据此渲染支付方式选项，不要在页面里硬编码开关：新增/下线通道时
    只改 .env 的 PAYMENT_GATEWAY_ENABLED 即可，不用改前端。
    PayPal 除了开关打开，还要求凭据已配置——只开开关没填 .env 时不给这个选项，
    否则用户下单后才发现付不了款。
    """
    enabled = {
        name: bool(settings.PAYMENT_GATEWAY_ENABLED.get(name))
        for name in ("mock", "alipay", "wechat", "stripe", "paypal")
    }
    enabled["paypal"] = settings.paypal_enabled
    return {"enabled": enabled, "default": "mock"}


async def _load_customer_order(db: AsyncSession, order_no: str, customer: Customer) -> Order:
    """按订单号 + 归属人加载订单（带明细与支付记录），避免越权操作他人订单"""
    result = await db.execute(
        select(Order)
        .options(selectinload(Order.items), selectinload(Order.payments))
        .where(Order.order_no == order_no, Order.customer_id == customer.id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    return order


def _find_paypal_payment(order: Order) -> Payment | None:
    for p in (order.payments or []):
        if p.method == PaymentMethod.PAYPAL:
            return p
    return None


@router.post("/paypal/capture")
async def paypal_capture(
    payload: PayPalCaptureIn,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """PayPal 扣款（前端 SDK onApprove 回调时调用）

    为什么必须在服务端 capture：金额校验与落账不能信前端。
    幂等：订单已 paid 直接返回成功；PayPal 侧已 capture 时网关会回查并返回同一笔 capture。
    """
    order = await _load_customer_order(db, payload.order_no, customer)
    payment = _find_paypal_payment(order)
    if not payment:
        raise HTTPException(status_code=400, detail="该订单不是 PayPal 支付")

    # 已落账：直接返回成功（用户多点一次、或 webhook 已经先到）
    if payment.status == PaymentStatus.SUCCESS:
        return {
            "success": True,
            "already_paid": True,
            "order_no": order.order_no,
            "status": order.status.value,
            "capture_id": payment.provider_capture_id or "",
        }
    if order.status != OrderStatus.PENDING:
        raise HTTPException(status_code=400, detail="订单状态不允许支付")
    if not payment.provider_order_id:
        raise HTTPException(status_code=400, detail="该订单尚未在 PayPal 侧创建支付单")

    gateway = get_gateway("paypal")
    if not gateway.configured:
        raise HTTPException(status_code=400, detail="PayPal 通道未配置")

    result = await gateway.capture_order(payment.provider_order_id, order.order_no)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "PayPal 扣款失败")

    # 金额/币种必须与下单时的快照完全一致——这是防篡改的关键一环：
    # 若被人改了前端金额，这里必须拒绝落账（但钱已经扣了，所以要留日志人工介入）
    try:
        amount_ok = Decimal(str(result.get("amount") or "0")) == Decimal(str(payment.amount))
    except (InvalidOperation, TypeError):
        amount_ok = False
    currency_ok = (result.get("currency") or "") == (payment.currency or "")
    if not (amount_ok and currency_ok):
        logger.error(
            "[paypal] 扣款金额与订单不一致 order=%s expect=%s %s actual=%s %s capture=%s",
            order.order_no, payment.amount, payment.currency,
            result.get("amount"), result.get("currency"), result.get("capture_id"),
        )
        raise HTTPException(
            status_code=400,
            detail="PayPal 扣款金额与订单不一致，已记录并转人工核对",
        )

    payment.provider_capture_id = result.get("capture_id") or ""
    payment.gateway_response = {
        **(payment.gateway_response or {}),
        "capture": {
            "id": result.get("capture_id"),
            "status": result.get("status"),
            "amount": result.get("amount"),
            "currency": result.get("currency"),
        },
    }
    await _mark_payment_success(db, payment.transaction_no)
    return {
        "success": True,
        "order_no": order.order_no,
        "status": OrderStatus.PAID.value,
        "capture_id": payment.provider_capture_id,
    }


async def _rollback_stock(db: AsyncSession, order: Order, operator: str) -> None:
    """退款回滚库存（与后台「取消并退款」同一口径：库存加回 + 留流水）"""
    for item in (order.items or []):
        if not item.sku_id:
            continue
        sku = (await db.execute(select(SKU).where(SKU.id == item.sku_id))).scalar_one_or_none()
        if not sku:
            continue
        qty = item.quantity or 0
        sku.stock = (sku.stock or 0) + qty
        db.add(
            StockMovement(
                sku_id=sku.id,
                change_qty=qty,
                balance_after=sku.stock,
                reason="order_refund",
                reference=order.order_no,
                operator=operator,
            )
        )


async def _find_paypal_payment_by_event(db: AsyncSession, resource: dict) -> Payment | None:
    """按 webhook 事件定位支付记录

    首选 related_ids.order_id（PayPal 订单号 = 我们存的 provider_order_id）；
    兜底用建单时写入的 custom_id（我们的订单号）反查，双保险避免掉单。
    """
    related = ((resource.get("supplementary_data") or {}).get("related_ids") or {})
    paypal_order_id = related.get("order_id") or ""
    if paypal_order_id:
        result = await db.execute(
            select(Payment)
            .options(selectinload(Payment.order).selectinload(Order.items))
            .where(Payment.provider_order_id == paypal_order_id)
        )
        payment = result.scalar_one_or_none()
        if payment:
            return payment
    order_no = resource.get("custom_id") or ""
    if order_no:
        result = await db.execute(
            select(Order)
            .options(selectinload(Order.items), selectinload(Order.payments))
            .where(Order.order_no == order_no)
        )
        order = result.scalar_one_or_none()
        if order:
            return _find_paypal_payment(order)
    return None


async def _handle_paypal_event(db: AsyncSession, event_type: str, event: dict) -> bool:
    """处理 PayPal 事件（全部幂等：PayPal 会重投最多 25 次 / 3 天）"""
    resource = event.get("resource") or {}

    if event_type == "PAYMENT.CAPTURE.COMPLETED":
        payment = await _find_paypal_payment_by_event(db, resource)
        if not payment:
            logger.warning("[paypal] webhook 找不到对应支付记录：%s", json.dumps(resource)[:300])
            return False
        if payment.status == PaymentStatus.SUCCESS:
            return True  # 已落账（前端 capture 先到），幂等返回
        payment.provider_capture_id = resource.get("id") or payment.provider_capture_id
        await _mark_payment_success(db, payment.transaction_no)
        logger.info("[paypal] webhook 补落账成功：订单 %s", payment.order.order_no)
        return True

    if event_type in (
        "PAYMENT.CAPTURE.DENIED",
        "PAYMENT.CAPTURE.DECLINED",
        "PAYMENT.CAPTURE.REVERSED",
        "PAYMENT.CAPTURE.FAILED",
    ):
        payment = await _find_paypal_payment_by_event(db, resource)
        if not payment or payment.status == PaymentStatus.SUCCESS:
            return False
        payment.status = PaymentStatus.FAILED
        payment.gateway_response = {**(payment.gateway_response or {}), "failed_event": event_type}
        await db.commit()
        logger.warning("[paypal] 扣款失败事件 %s：订单 %s", event_type, payment.transaction_no)
        return True

    if event_type == "PAYMENT.CAPTURE.REFUNDED":
        payment = await _find_paypal_payment_by_event(db, resource)
        if not payment:
            return False
        if payment.status == PaymentStatus.REFUNDED:
            return True  # 我们自己发起的退款（后台取消并退款）会走同一条路径，幂等
        payment.status = PaymentStatus.REFUNDED
        order = payment.order
        if order and order.status in (OrderStatus.PAID, OrderStatus.SHIPPED):
            order.status = OrderStatus.REFUNDED
            await _rollback_stock(db, order, "paypal-webhook")
        await db.commit()
        logger.info("[paypal] 收到 PayPal 侧退款，已置为已退款：订单 %s", payment.transaction_no)
        return True

    if event_type == "CHECKOUT.ORDER.APPROVED":
        # 买家已批准但还没扣款：等 capture / PAYMENT.CAPTURE.COMPLETED，这里只记日志
        logger.info("[paypal] 买家已批准订单：%s", json.dumps(resource)[:300])
        return True

    return False


@router.post("/paypal/webhook")
async def paypal_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """PayPal 异步事件通知（掉单兜底 + 退款/拒付同步）

    ⚠️ 三个硬要求：
    1. **必须用原始 body 验签**（解析后重新序列化会验签失败）；
    2. 必须返回 2xx，否则 PayPal 会重投 25 次 / 3 天，所以处理逻辑全部幂等；
    3. 必须只投递到公网 HTTPS:443 地址 —— PayPal 连不上内网/HTTP。
    """
    raw = await request.body()
    ok, reason = await verify_paypal_webhook(request.headers, raw)
    if not ok:
        logger.warning("[paypal] webhook 验签失败：%s", reason)
        raise HTTPException(status_code=400, detail=f"webhook 验签失败：{reason}")

    try:
        event = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="webhook body 不是合法 JSON")

    event_type = str(event.get("event_type") or "")
    handled = await _handle_paypal_event(db, event_type, event)
    return {"received": True, "event_type": event_type, "handled": handled}