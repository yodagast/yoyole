"""订单与支付路由：下单、支付、订单查询、取消/确认"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import get_current_customer
from app.config import settings
from app.i18n import get_lang
from app.models import (
    CartItem,
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
from app.payments import PaymentRequest, get_gateway
from app.schemas import (
    CheckoutIn,
    Message,
    OrderCreateOut,
    OrderItemOut,
    OrderOut,
    PaymentOut,
)

router = APIRouter(prefix="/api", tags=["orders"])


def _gen_order_no() -> str:
    return f"ORD{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6].upper()}"


def _order_to_out(order: Order, lang: str) -> OrderOut:
    """将 Order ORM 转为响应模型，并将快照名称按语言处理"""
    items = []
    for it in order.items:
        items.append(
            OrderItemOut(
                product_name=it.product_name,
                sku_spec=it.sku_spec,
                sku_code=it.sku_code,
                image=it.image,
                unit_price=it.unit_price,
                quantity=it.quantity,
                subtotal=it.subtotal,
            )
        )
    payments = []
    for p in order.payments:
        payments.append(
            PaymentOut(
                transaction_no=p.transaction_no,
                method=p.method.value if hasattr(p.method, "value") else str(p.method),
                amount=p.amount,
                currency=p.currency,
                status=p.status.value if hasattr(p.status, "value") else str(p.status),
                gateway_response=p.gateway_response or {},
            )
        )
    return OrderOut(
        id=order.id,
        order_no=order.order_no,
        status=order.status.value,
        currency=order.currency,
        subtotal=order.subtotal,
        shipping_fee=order.shipping_fee,
        discount=order.discount,
        total_amount=order.total_amount,
        receiver_name=order.receiver_name,
        receiver_phone=order.receiver_phone,
        receiver_address=order.receiver_address,
        remark=order.remark,
        created_at=order.created_at,
        paid_at=order.paid_at,
        shipped_at=order.shipped_at,
        items=items,
        payments=payments,
    )


@router.post("/orders/checkout", response_model=OrderCreateOut, status_code=201)
async def checkout(
    payload: CheckoutIn,
    request: Request,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """从购物车创建订单，锁定库存，创建支付记录"""
    lang = get_lang(request)

    # 加载购物车
    result = await db.execute(
        select(CartItem)
        .options(selectinload(CartItem.sku).selectinload(SKU.product))
        .where(CartItem.customer_id == customer.id)
    )
    cart_items = result.scalars().all()
    if not cart_items:
        raise HTTPException(status_code=400, detail="购物车为空")

    # 校验库存并计算金额
    subtotal = Decimal("0")
    order_items: list[OrderItem] = []
    skus_to_lock: list[SKU] = []
    for ci in cart_items:
        sku = ci.sku
        if not sku.is_active:
            raise HTTPException(status_code=400, detail=f"商品 {sku.sku_code} 已下架")
        if ci.quantity > sku.available_stock:
            raise HTTPException(status_code=400, detail=f"商品 {sku.sku_code} 库存不足")
        subtotal += sku.price * ci.quantity
        order_items.append(
            OrderItem(
                sku_id=sku.id,
                product_name=sku.product.name(lang),
                sku_spec=sku.attributes or {},
                sku_code=sku.sku_code,
                image=sku.product.main_image,
                unit_price=sku.price,
                quantity=ci.quantity,
                subtotal=sku.price * ci.quantity,
            )
        )
        skus_to_lock.append(sku)

    shipping_fee = Decimal("0")
    discount = Decimal("0")
    total = subtotal + shipping_fee - discount

    # 创建订单
    order = Order(
        order_no=_gen_order_no(),
        customer_id=customer.id,
        status=OrderStatus.PENDING,
        subtotal=subtotal,
        shipping_fee=shipping_fee,
        discount=discount,
        total_amount=total,
        receiver_name=payload.receiver_name,
        receiver_phone=payload.receiver_phone,
        receiver_address=payload.receiver_address,
        remark=payload.remark,
        items=order_items,
    )
    db.add(order)
    await db.flush()  # 获取 order.id

    # 锁定库存（下单未支付）
    for ci, sku in zip(cart_items, skus_to_lock):
        sku.locked_stock += ci.quantity
        db.add(
            StockMovement(
                sku_id=sku.id,
                change_qty=-ci.quantity,
                balance_after=sku.stock - sku.locked_stock,
                reason="order_lock",
                reference=order.order_no,
                operator=str(customer.id),
            )
        )

    # 创建支付记录
    method = (payload.payment_method or "mock").lower()
    # 校验支付方式是否可用（未配置的通道不允许下单，避免下单后无法支付）
    try:
        PaymentMethod(method)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"不支持的支付方式: {method}")
    try:
        gateway = get_gateway(method)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"支付方式 {method} 尚未配置")
    # 占位通道（alipay/wechat/stripe 未接入真实 API）禁止提交订单
    if gateway.name in ("alipay", "wechat", "stripe") and not settings.PAYMENT_GATEWAY_ENABLED.get(gateway.name):
        raise HTTPException(
            status_code=400,
            detail=f"支付方式 {gateway.name} 尚未开通，请选择「模拟支付」",
        )

    payment = Payment(
        transaction_no=f"PAY{uuid.uuid4().hex[:16].upper()}",
        order_id=order.id,
        method=PaymentMethod(method),
        amount=total,
        currency="CNY",
        status=PaymentStatus.UNPAID,
    )
    db.add(payment)

    # 清空购物车
    await db.execute(delete(CartItem).where(CartItem.customer_id == customer.id))

    await db.commit()

    return OrderCreateOut(
        order_no=order.order_no,
        total_amount=total,
        status=order.status.value,
        payment={"transaction_no": payment.transaction_no, "method": payment.method.value},
    )


@router.post("/orders/{order_no}/pay", response_model=dict)
async def pay_order(
    order_no: str,
    request: Request,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """发起支付，返回支付跳转地址"""
    result = await db.execute(
        select(Order)
        .options(selectinload(Order.payments))
        .where(Order.order_no == order_no, Order.customer_id == customer.id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")

    if order.status != OrderStatus.PENDING:
        raise HTTPException(status_code=400, detail="订单状态不允许支付")

    pending_payment = None
    for p in order.payments:
        if p.status in (PaymentStatus.UNPAID, PaymentStatus.PROCESSING, PaymentStatus.FAILED):
            pending_payment = p
            break
    if not pending_payment:
        raise HTTPException(status_code=400, detail="无可支付记录")

    gateway = get_gateway(pending_payment.method.value)
    pay_req = PaymentRequest(
        order_no=order.order_no,
        amount=order.total_amount,
        currency=order.currency,
        subject=f"订单 {order.order_no}",
        method=pending_payment.method.value,
        notify_url=f"{request.url.scheme}://{request.url.netloc}/api/payments/callback",
    )
    result_pay = await gateway.create_payment(pay_req)
    if not result_pay.success:
        pending_payment.status = PaymentStatus.FAILED
        pending_payment.gateway_response = {"error": result_pay.error}
        await db.commit()
        raise HTTPException(status_code=400, detail=result_pay.error)

    # 更新实际网关交易号
    pending_payment.transaction_no = result_pay.transaction_no
    pending_payment.status = PaymentStatus.PROCESSING
    pending_payment.gateway_response = result_pay.provider_response
    await db.commit()

    return {
        "success": True,
        "transaction_no": result_pay.transaction_no,
        "pay_url": result_pay.pay_url,
        "amount": str(order.total_amount),
    }


@router.get("/orders", response_model=list[OrderOut])
async def list_orders(
    request: Request,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """我的订单列表"""
    result = await db.execute(
        select(Order)
        .options(selectinload(Order.items), selectinload(Order.payments))
        .where(Order.customer_id == customer.id)
        .order_by(Order.id.desc())
    )
    orders = result.scalars().all()
    return [_order_to_out(o, get_lang(request)) for o in orders]


@router.get("/orders/{order_no}", response_model=OrderOut)
async def order_detail(
    order_no: str,
    request: Request,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """订单详情"""
    result = await db.execute(
        select(Order)
        .options(selectinload(Order.items), selectinload(Order.payments))
        .where(Order.order_no == order_no, Order.customer_id == customer.id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    return _order_to_out(order, get_lang(request))


@router.post("/orders/{order_no}/cancel", response_model=Message)
async def cancel_order(
    order_no: str,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """取消订单并释放锁定库存"""
    result = await db.execute(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.order_no == order_no, Order.customer_id == customer.id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.status not in (OrderStatus.PENDING,):
        raise HTTPException(status_code=400, detail="仅待支付订单可取消")

    # 释放锁定库存
    for item in order.items:
        if item.sku_id:
            sku_result = await db.execute(select(SKU).where(SKU.id == item.sku_id))
            sku = sku_result.scalar_one_or_none()
            if sku:
                sku.locked_stock = max(sku.locked_stock - item.quantity, 0)
                db.add(
                    StockMovement(
                        sku_id=sku.id,
                        change_qty=item.quantity,
                        balance_after=sku.stock - sku.locked_stock,
                        reason="order_cancel",
                        reference=order.order_no,
                        operator=str(customer.id),
                    )
                )

    order.status = OrderStatus.CANCELLED
    await db.commit()
    return Message(message="订单已取消")


@router.post("/orders/{order_no}/confirm", response_model=Message)
async def confirm_order(
    order_no: str,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """确认收货"""
    result = await db.execute(
        select(Order).where(Order.order_no == order_no, Order.customer_id == customer.id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.status != OrderStatus.SHIPPED:
        raise HTTPException(status_code=400, detail="仅已发货订单可确认收货")

    order.status = OrderStatus.COMPLETED
    await db.commit()
    return Message(message="已确认收货")