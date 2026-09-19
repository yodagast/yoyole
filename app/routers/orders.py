"""订单与支付路由：下单、支付、订单查询、取消/确认"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, inspect as sa_inspect, select
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
    utc_to_local_naive,
)
from app.payments import PaymentRequest, cny_to_usd, get_gateway
from app.schemas import (
    CheckoutIn,
    Message,
    OrderCreateOut,
    OrderItemOut,
    OrderOut,
    PaymentOut,
)

router = APIRouter(prefix="/api", tags=["orders"])

logger = logging.getLogger(__name__)

# Python 写入的 UTC 时间 → 本地时间（与 DB 生成的 created_at 同口径）
utc_to_local = utc_to_local_naive


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
        paid_at=utc_to_local(order.paid_at),
        shipped_at=utc_to_local(order.shipped_at),
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
    # 占位通道（未接入真实 API 的 alipay/wechat/stripe 等）禁止提交订单。
    # 判断依据统一取 PAYMENT_GATEWAY_ENABLED，不要再硬编码通道名——否则新增
    # 通道（如 paypal）会绕开开关，用户下单后才发现付不了款。
    if not settings.PAYMENT_GATEWAY_ENABLED.get(gateway.name):
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


def _paypal_pay_payload(order: Order, payment: Payment, gateway) -> dict:
    """下发给前端的 PayPal 渲染参数

    只含公开信息（client_id 本来就要给浏览器）；**client_secret / webhook_id 永不出后端**。
    """
    return {
        "success": True,
        "method": "paypal",
        "order_no": order.order_no,
        "transaction_no": payment.transaction_no,
        "paypal_order_id": payment.provider_order_id,
        "client_id": gateway.client_id,
        "sdk_host": gateway.sdk_host,
        "mode": gateway.mode,
        "currency": payment.currency,
        "amount": str(payment.amount),
        "order_total": str(order.total_amount),
        "order_currency": order.currency,
        "fx_rate": str(payment.fx_rate or ""),
        "pay_url": f"{gateway.sdk_host}/checkoutnow?token={payment.provider_order_id}",
    }


async def _start_paypal_payment(db: AsyncSession, order: Order, payment: Payment) -> dict:
    """发起（或复用）PayPal 支付，返回 JS SDK 需要的参数

    幂等设计：同一订单重复点「支付」时**复用**已建的 PayPal 订单。这样
    「付款中断后再回来继续支付」拿到的是同一个 PayPal 订单号，不会在 PayPal
    侧堆一堆废单，也不会让用户重复付款。
    """
    gateway = get_gateway("paypal")
    if not gateway.configured:
        raise HTTPException(
            status_code=400,
            detail="PayPal 通道未配置：请在 .env 填写 PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET",
        )

    if payment.provider_order_id:
        info = await gateway.query_payment(payment.provider_order_id)
        state = str(info.get("status", "")).lower()
        if state in ("created", "approved", "saved"):
            return _paypal_pay_payload(order, payment, gateway)
        if state == "completed":
            # 买家其实已经付了，只是本地还没落账（回调未到 / 前端丢了响应）。
            # 返回 already_paid，让前端直接调 capture 接口——那边是幂等的，
            # 会把已有 capture 读回来并补上落账。
            return {**_paypal_pay_payload(order, payment, gateway), "already_paid": True}
        logger.info("[paypal] 订单 %s 的原 PayPal 单状态=%s，重建支付单", order.order_no, state)

    rate = settings.PAYPAL_FX_CNY_PER_USD
    usd = cny_to_usd(order.total_amount, rate)
    # 支付记录跟随「实际扣款币种/金额」，并把汇率快照下来，回调时据此校验金额
    payment.currency = settings.PAYPAL_CURRENCY
    payment.amount = usd
    payment.fx_rate = Decimal(str(rate))

    result = await gateway.create_payment(
        PaymentRequest(
            order_no=order.order_no,
            amount=usd,
            currency=settings.PAYPAL_CURRENCY,
            subject=f"订单 {order.order_no}",
            method="paypal",
            notify_url=f"{settings.BASE_URL.rstrip('/')}/api/payments/paypal/webhook",
        )
    )
    if not result.success:
        payment.status = PaymentStatus.FAILED
        payment.gateway_response = {"error": result.error}
        await db.commit()
        raise HTTPException(status_code=400, detail=result.error)

    # 注意：不改写 transaction_no（那是我们的内部流水号，回调/查询都按它找记录），
    # PayPal 的订单号单独存 provider_order_id。
    payment.provider_order_id = result.transaction_no
    payment.status = PaymentStatus.PROCESSING
    payment.gateway_response = result.provider_response
    await db.commit()
    return _paypal_pay_payload(order, payment, gateway)


async def _start_wechat_payment(
    request: Request, db: AsyncSession, order: Order, payment: Payment
) -> dict:
    """发起（或复用）微信支付，返回前端要用的下单信息

    - PC 浏览器：返回 `code_url`，前端画成二维码让用户扫；
    - 手机浏览器：返回 `h5_url`，前端直接跳转拉起微信。

    幂等/复用：订单已下过单且微信侧仍是「未支付」时直接复用原二维码，避免用户
    反复点「支付」在微信侧堆废单（微信侧 code_url 有效期 2 小时，过期需重新下单）。
    """
    gateway = get_gateway("wechat")
    if not gateway.configured:
        raise HTTPException(
            status_code=400,
            detail="微信支付通道未配置：请在 .env 补齐 WECHATPAY_* 各项（见 docs/wechatpay.md）",
        )

    channel = "h5" if _is_mobile_ua(request.headers.get("user-agent", "")) else "native"

    if payment.provider_order_id:
        info = await gateway.query_payment(payment.provider_order_id)
        state = str(info.get("status", "")).lower()
        if state in ("notpay", "userpaying"):
            resp = payment.gateway_response or {}
            # 只有在同一终端形态下才复用（PC 拿到的 code_url 对手机没用）
            if resp.get("channel") == channel and resp.get("code_url") or resp.get("h5_url"):
                return _wechat_pay_payload(order, payment, channel, expired=False)
        if state == "success":
            # 用户其实付过了，只是回调还没到 —— 让前端直接去查单，那边会补落账
            return {**_wechat_pay_payload(order, payment, channel, expired=False), "already_paid": True}
        logger.info("[wechat] 订单 %s 原支付单状态=%s，重新下单", order.order_no, state)

    result = await gateway.create_payment(
        PaymentRequest(
            order_no=order.order_no,
            amount=order.total_amount,
            currency="CNY",
            subject=_order_subject(order),
            method="wechat",
            channel=channel,
            client_ip=_client_ip(request),
            notify_url=f"{settings.BASE_URL.rstrip('/')}/api/payments/wechat/notify",
        )
    )
    if not result.success:
        payment.status = PaymentStatus.FAILED
        payment.gateway_response = {"error": result.error}
        await db.commit()
        raise HTTPException(status_code=400, detail=result.error)

    # 微信没有独立的「交易号」可提前拿，用商户订单号作为对账锚点
    payment.provider_order_id = order.order_no
    payment.status = PaymentStatus.PROCESSING
    payment.gateway_response = result.provider_response
    await db.commit()
    return _wechat_pay_payload(order, payment, channel, expired=False)


def _wechat_pay_payload(order: Order, payment: Payment, channel: str, *, expired: bool) -> dict:
    resp = payment.gateway_response or {}
    return {
        "success": True,
        "method": "wechat",
        "channel": channel,
        "order_no": order.order_no,
        "transaction_no": payment.transaction_no,
        "amount": str(payment.amount),
        "currency": payment.currency,
        "code_url": resp.get("code_url", ""),
        "h5_url": resp.get("h5_url", ""),
        "pay_url": resp.get("h5_url") or resp.get("code_url", ""),
        "expired": expired,
    }


def _is_mobile_ua(ua: str) -> bool:
    """粗判手机浏览器：决定微信走 H5 还是 Native（H5 只能在手机浏览器里拉起微信）"""
    ua = (ua or "").lower()
    return any(k in ua for k in ("mobile", "android", "iphone", "ipad", "micromessenger"))


def _client_ip(request: Request) -> str:
    """取用户真实 IP（反代下 request.client.host 是代理地址，优先 X-Forwarded-For）"""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


def _order_subject(order: Order) -> str:
    """商品描述：用户微信账单里会看到，用首个商品名 + 件数（别写「订单 XXX」这种废话）

    ⚠️ `order.items` 是懒加载关系：在 async 会话里若没提前 selectinload，
    访问它会抛 MissingGreenlet（对外就是 500，支付直接失败）。
    所以这里先判断加载状态，未加载就退化成不带商品名的描述——
    描述只影响账单展示，不该拖死整个支付流程。
    """
    items = [] if "items" in sa_inspect(order).unloaded else list(order.items or [])
    if not items:
        return f"YOYOLE 订单 {order.order_no}"
    first = items[0].product_name or "商品"
    total_qty = sum(i.quantity or 0 for i in items)
    return f"{first} 等 {total_qty} 件商品" if len(items) > 1 else first


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
        # ⚠️ items 必须一起预加载：微信分支要用商品名生成账单描述，
        # 懒加载在 async 会话里会抛 MissingGreenlet（500）。
        .options(selectinload(Order.items), selectinload(Order.payments))
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

    # PayPal 是「前端 SDK 渲染按钮 + 服务端 capture」的交互模型，没有可 GET 的 pay_url，
    # 因此单独走一条分支：这里只建单，真正扣款在 /api/payments/paypal/capture。
    if pending_payment.method == PaymentMethod.PAYPAL:
        return await _start_paypal_payment(db, order, pending_payment)

    # 微信支付同样是异步付款：Native 返回二维码链接、H5 返回跳转链接，
    # 用户付完后由回调（+前端轮询）落账，所以这里也只是「下单」。
    if pending_payment.method == PaymentMethod.WECHAT:
        return await _start_wechat_payment(request, db, order, pending_payment)

    pay_req = PaymentRequest(
        order_no=order.order_no,
        amount=order.total_amount,
        currency=order.currency,
        subject=f"订单 {order.order_no}",
        method=pending_payment.method.value,
        # ⚠️ 不能用 request.url.netloc：反代（nginx → 127.0.0.1:8020）下会拼出内网 http 地址，
        # 而支付网关只往公网 HTTPS 地址回通知，线上会直接丢回调。
        notify_url=f"{settings.BASE_URL.rstrip('/')}/api/payments/callback",
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