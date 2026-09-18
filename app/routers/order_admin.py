"""订单管理增强路由：多字段查询、状态流转、批量操作、导出

参考主流电商卖家后台（如拼多多商家后台）「订单查询」页重构：
- 多字段筛选（订单号 / 商品ID / 收件人 / 手机号 / 快递单号 / 时间范围）
- 状态标签页 + 数量角标
- 发货（含快递公司 / 单号）、备注、取消、退款
- 批量发货 / 批量备注、批量导出 CSV

> 与 `app/routers/admin.py` 的 `/orders` 接口并存：
> 旧接口保持兼容（返回 `list[OrderOut]`），此处提供增强版 `/orders-search`。
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import require_admin
from app.i18n import get_lang
from app.payments import get_gateway
from app.models import (
    AdminUser,
    Customer,
    Order,
    OrderItem,
    OrderStatus,
    ORDER_STATUS_LABELS,
    ORDER_TABS,
    order_status_label,
    utc_to_local_naive,
)
from app.schemas import (
    AdminOrderItemOut,
    AdminOrderOut,
    AdminPaymentOut,
    Message,
    OrderBatchIn,
    OrderCancelIn,
    OrderNoteIn,
    OrderShipIn,
    OrderStatusCountsOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin-order"])

WRITE_ROLES = {"superadmin", "operator"}

# Python 写入的 UTC 时间 → 本地时间，保证与 DB 生成的 created_at 口径一致
to_local = utc_to_local_naive

# 订单状态标签页（含「全部」）：直接取自 ORDER_TABS，避免两处维护漏项
TABS = [key for key, _ in ORDER_TABS]
TAB_LABELS = {k: v for k, v in ORDER_TABS}

# 支付方式 / 支付状态中文（后台展示用）
PAY_METHOD_LABELS = {
    "alipay": "支付宝", "wechat": "微信支付", "stripe": "Stripe",
    "paypal": "PayPal", "mock": "模拟支付",
}
PAY_STATUS_LABELS = {
    "unpaid": "未支付", "processing": "处理中", "success": "支付成功",
    "failed": "支付失败", "refunded": "已退款",
}

# 待发货超时提醒阈值（小时）：已支付但超过该时长未发货
PENDING_SHIP_ALERT_HOURS = 24


def _order_to_admin_out(order: Order, lang: str) -> AdminOrderOut:
    """Order ORM → 后台订单响应（含买家、金额汇总、物流）"""
    items = []
    goods_amount = 0
    total_qty = 0
    for it in order.items:
        items.append(
            AdminOrderItemOut(
                sku_id=it.sku_id,
                product_name=it.product_name,
                sku_spec=it.sku_spec or {},
                sku_code=it.sku_code,
                image=it.image,
                unit_price=it.unit_price,
                quantity=it.quantity,
                subtotal=it.subtotal,
            )
        )
        goods_amount += it.subtotal or 0
        total_qty += it.quantity or 0

    payments = []
    for p in (order.payments or []):
        m = p.method.value if hasattr(p.method, "value") else str(p.method)
        st = p.status.value if hasattr(p.status, "value") else str(p.status)
        payments.append(
            AdminPaymentOut(
                transaction_no=p.transaction_no,
                method=m,
                amount=p.amount,
                currency=p.currency,
                status=st,
                gateway_response=p.gateway_response or {},
                method_label=PAY_METHOD_LABELS.get(m, m),
                status_label=PAY_STATUS_LABELS.get(st, st),
            )
        )

    customer = order.customer
    return AdminOrderOut(
        id=order.id,
        order_no=order.order_no,
        status=order.status.value if hasattr(order.status, "value") else str(order.status),
        status_label=order_status_label(order.status),
        currency=order.currency,
        subtotal=order.subtotal,
        shipping_fee=order.shipping_fee,
        discount=order.discount,
        total_amount=order.total_amount,
        goods_amount=goods_amount,
        item_count=len(items),
        total_quantity=total_qty,
        receiver_name=order.receiver_name or "",
        receiver_phone=order.receiver_phone or "",
        receiver_address=order.receiver_address or "",
        remark=order.remark,
        admin_note=order.admin_note,
        tracking_no=order.tracking_no,
        carrier=order.carrier,
        cancel_reason=order.cancel_reason,
        customer_id=order.customer_id,
        customer_email=(customer.email if customer else ""),
        customer_name=(customer.full_name if customer else "") or "",
        created_at=order.created_at,
        paid_at=to_local(order.paid_at),
        shipped_at=to_local(order.shipped_at),
        completed_at=to_local(order.completed_at),
        cancelled_at=to_local(order.cancelled_at),
        items=items,
        payments=payments,
    )


def _apply_order_filters(
    stmt,
    *,
    tab: str,
    order_no: str | None,
    product_id: str | None,
    receiver: str | None,
    phone: str | None,
    tracking_no: str | None,
    keyword: str | None,
    date_from: str | None,
    date_to: str | None,
):
    """把订单查询页的筛选条件应用到查询上（列表 / 计数 / 导出共用同一口径）"""
    if tab not in TABS:
        raise HTTPException(status_code=400, detail=f"tab 必须是 {'/'.join(TABS)}")
    if tab != "all":
        try:
            stmt = stmt.where(Order.status == OrderStatus(tab))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"未知订单状态：{tab}")

    if order_no:
        # 支持逗号/空格分隔的多个订单号（后台常见「批量查订单」）
        tokens = [x for x in re.split(r"[\s,]+", order_no.strip()) if x]
        if len(tokens) > 1:
            stmt = stmt.where(Order.order_no.in_(tokens))
        else:
            stmt = stmt.where(Order.order_no.ilike(f"%{tokens[0]}%"))
    if receiver:
        stmt = stmt.where(Order.receiver_name.ilike(f"%{receiver}%"))
    if phone:
        stmt = stmt.where(Order.receiver_phone.ilike(f"%{phone}%"))
    if tracking_no:
        stmt = stmt.where(Order.tracking_no.ilike(f"%{tracking_no}%"))
    if keyword:
        # 通用关键词：订单号 / 收件人 / 手机号 / 快递单号
        pattern = f"%{keyword}%"
        stmt = stmt.where(
            or_(
                Order.order_no.ilike(pattern),
                Order.receiver_name.ilike(pattern),
                Order.receiver_phone.ilike(pattern),
                Order.tracking_no.ilike(pattern),
            )
        )
    if product_id:
        # 按商品 ID / 规格编码反查订单：
        # - 纯数字 → 经 SKU 关联到 products.id 精确匹配
        # - 其他   → 按明细快照的规格编码模糊匹配
        from app.models import SKU

        raw = [x for x in re.split(r"[\s,]+", product_id.strip()) if x]
        conds = []
        pid_list = [int(x) for x in raw if x.isdigit()]
        code_list = [x for x in raw if not x.isdigit()]
        if pid_list:
            conds.append(
                OrderItem.sku_id.in_(
                    select(SKU.id).where(SKU.product_id.in_(pid_list))
                )
            )
        for code in code_list:
            conds.append(OrderItem.sku_code.ilike(f"%{code}%"))
        if conds:
            stmt = stmt.where(
                Order.id.in_(select(OrderItem.order_id).where(or_(*conds)))
            )

    if date_from:
        dt = _parse_date(date_from, end_of_day=False)
        if dt:
            stmt = stmt.where(Order.created_at >= dt)
    if date_to:
        dt = _parse_date(date_to, end_of_day=True)
        if dt:
            stmt = stmt.where(Order.created_at <= dt)
    return stmt


def _parse_date(value: str, *, end_of_day: bool) -> datetime | None:
    """解析 ISO 日期（`YYYY-MM-DD` 或带时间），失败返回 None（忽略该条件）"""
    value = (value or "").strip()
    if not value:
        return None
    try:
        if len(value) == 10:
            d = datetime.fromisoformat(value)
            if end_of_day:
                d = d.replace(hour=23, minute=59, second=59)
            return d
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


async def _load_order(db: AsyncSession, order_no: str, *, with_customer: bool = False) -> Order:
    opts = [selectinload(Order.items), selectinload(Order.payments)]
    if with_customer:
        opts.append(selectinload(Order.customer))
    order = (
        await db.execute(select(Order).options(*opts).where(Order.order_no == order_no))
    ).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    return order


# ---------------------------------------------------------------- 状态计数
@router.get("/orders-status-counts", response_model=OrderStatusCountsOut)
async def order_status_counts(
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """订单各状态数量（后台标签页角标）+ 待发货超时提醒数"""
    rows = (
        await db.execute(
            select(Order.status, func.count(Order.id)).group_by(Order.status)
        )
    ).all()
    by_status: dict[str, int] = {}
    for status, count in rows:
        key = status.value if hasattr(status, "value") else str(status)
        by_status[key] = int(count)

    total = sum(by_status.values())
    deadline = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        hours=PENDING_SHIP_ALERT_HOURS
    )
    alert = (
        await db.execute(
            select(func.count(Order.id)).where(
                Order.status == OrderStatus.PAID, Order.created_at <= deadline
            )
        )
    ).scalar() or 0

    return OrderStatusCountsOut(
        all=total,
        pending=by_status.get("pending", 0),
        paid=by_status.get("paid", 0),
        shipped=by_status.get("shipped", 0),
        completed=by_status.get("completed", 0),
        cancelled=by_status.get("cancelled", 0),
        refunded=by_status.get("refunded", 0),
        pending_ship_alert=int(alert),
    )


# ---------------------------------------------------------------- 查询
@router.get("/orders-search", response_model=list[AdminOrderOut])
async def search_orders(
    request: Request,
    tab: str = Query("all", description="状态标签页：all/pending/paid/shipped/completed/cancelled/refunded"),
    order_no: str | None = Query(None, description="订单编号（支持逗号/空格分隔多个）"),
    product_id: str | None = Query(None, description="商品ID / 规格编码"),
    receiver: str | None = Query(None, description="收件人姓名"),
    phone: str | None = Query(None, description="收件人手机号"),
    tracking_no: str | None = Query(None, description="快递单号"),
    keyword: str | None = Query(None, description="通用关键词（订单号/收件人/手机号/快递单号）"),
    date_from: str | None = Query(None, description="下单开始日期 YYYY-MM-DD"),
    date_to: str | None = Query(None, description="下单结束日期 YYYY-MM-DD"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    limit: int = Query(0, ge=0, le=2000, description=">0 时忽略分页（兼容旧调用）"),
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """订单查询（后台）：多字段筛选 + 分页；总数见 `/orders-search-count`"""
    stmt = select(Order).options(
        selectinload(Order.items),
        selectinload(Order.payments),
        selectinload(Order.customer),
    )
    stmt = _apply_order_filters(
        stmt, tab=tab, order_no=order_no, product_id=product_id, receiver=receiver,
        phone=phone, tracking_no=tracking_no, keyword=keyword,
        date_from=date_from, date_to=date_to,
    )
    stmt = stmt.order_by(Order.id.desc())
    if limit:
        stmt = stmt.limit(limit)
    else:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    lang = get_lang(request)
    rows = (await db.execute(stmt)).scalars().all()
    return [_order_to_admin_out(o, lang) for o in rows]


@router.get("/orders-search-count")
async def count_orders(
    tab: str = Query("all"),
    order_no: str | None = None,
    product_id: str | None = None,
    receiver: str | None = None,
    phone: str | None = None,
    tracking_no: str | None = None,
    keyword: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """筛选后的订单总数（分页「共查到 N 个订单」用，与列表同口径）"""
    stmt = select(func.count(Order.id)).select_from(Order)
    stmt = _apply_order_filters(
        stmt, tab=tab, order_no=order_no, product_id=product_id, receiver=receiver,
        phone=phone, tracking_no=tracking_no, keyword=keyword,
        date_from=date_from, date_to=date_to,
    )
    total = int((await db.execute(stmt)).scalar() or 0)
    return {"total": total}


@router.get("/orders-search/{order_no}", response_model=AdminOrderOut)
async def order_detail(
    order_no: str,
    request: Request,
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """订单详情（后台）：含买家信息、金额汇总、物流与时间节点"""
    order = await _load_order(db, order_no, with_customer=True)
    return _order_to_admin_out(order, get_lang(request))


# ---------------------------------------------------------------- 状态流转
@router.post("/orders-search/{order_no}/ship", response_model=AdminOrderOut)
async def ship_order(
    order_no: str,
    request: Request,
    payload: OrderShipIn | None = None,
    admin: AdminUser = Depends(require_admin(WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """发货（可填快递公司 / 单号）。仅「待发货」订单可发货。"""
    order = await _load_order(db, order_no, with_customer=True)
    if order.status == OrderStatus.SHIPPED:
        raise HTTPException(status_code=400, detail="该订单已发货")
    if order.status == OrderStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="该订单已完成，无需发货")
    if order.status == OrderStatus.CANCELLED:
        raise HTTPException(status_code=400, detail="该订单已取消，无法发货")
    if order.status == OrderStatus.REFUNDED:
        raise HTTPException(status_code=400, detail="该订单已退款，无法发货")
    if order.status != OrderStatus.PAID:
        raise HTTPException(status_code=400, detail="仅已支付订单可发货")

    if payload:
        order.carrier = (payload.carrier or "").strip() or order.carrier
        order.tracking_no = (payload.tracking_no or "").strip() or order.tracking_no

    order.status = OrderStatus.SHIPPED
    order.shipped_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    await db.refresh(order)
    return _order_to_admin_out(order, get_lang(request))


@router.post("/orders-search/{order_no}/note", response_model=AdminOrderOut)
async def update_order_note(
    order_no: str,
    request: Request,
    payload: OrderNoteIn,
    admin: AdminUser = Depends(require_admin(WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """更新商家备注（仅后台可见）"""
    order = await _load_order(db, order_no, with_customer=True)
    order.admin_note = (payload.note or "").strip() or None
    await db.commit()
    await db.refresh(order)
    return _order_to_admin_out(order, get_lang(request))


async def _refund_via_gateway(order: Order, operator: str) -> str:
    """已支付订单退款：真实通道调网关退款接口，返回错误信息（空串 = 成功）

    最坏的情况是「本地显示已退款，钱其实没退回去」，所以网关退款失败时**必须**中止
    后续状态变更，让运营看到报错去处理，而不是静默变成一个假的已退款。
    mock 通道直接放过（开发/演示用）；PayPal 走 `captures/{id}/refund`。
    """
    from app.models import PaymentStatus

    for p in (order.payments or []):
        if p.status != PaymentStatus.SUCCESS:
            continue
        method = p.method.value if hasattr(p.method, "value") else str(p.method)
        if method != "paypal":
            continue
        gateway = get_gateway(method)
        capture_id = p.provider_capture_id or ""
        if not capture_id:
            return "该订单缺少 PayPal 扣款号（capture id），无法自动退款，请到 PayPal 后台人工处理"
        result = await gateway.refund(capture_id, p.amount)
        if not result.get("success"):
            return f"PayPal 退款失败：{result.get('error') or '未知错误'}"
        logger.info(
            "[refund] PayPal 退款成功 order=%s capture=%s amount=%s operator=%s",
            order.order_no, capture_id, p.amount, operator,
        )
    return ""


@router.post("/orders-search/{order_no}/cancel", response_model=AdminOrderOut)
async def cancel_order(
    order_no: str,
    request: Request,
    payload: OrderCancelIn | None = None,
    admin: AdminUser = Depends(require_admin(WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """取消订单（`refund=true` 时走退款：状态置为「退款/售后」）

    - 待付款订单 → 直接取消，并释放锁定库存
    - 已支付/已发货订单 → 需 `refund=true`，状态置为「退款/售后」并回滚库存
    """
    from app.models import PaymentStatus, SKU, StockMovement

    order = await _load_order(db, order_no, with_customer=True)
    if order.status in (OrderStatus.CANCELLED, OrderStatus.REFUNDED):
        raise HTTPException(status_code=400, detail="该订单已取消或已退款")
    if order.status == OrderStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="该订单已完成，不支持取消")

    reason = (payload.reason if payload else "").strip()
    want_refund = bool(payload and payload.refund)
    was_paid = order.status in (OrderStatus.PAID, OrderStatus.SHIPPED)

    if was_paid and not want_refund:
        raise HTTPException(
            status_code=400, detail="已支付订单请选择「退款」而非直接取消"
        )

    # 真实支付通道（PayPal）要先真正把钱退回去，再改本地状态
    if was_paid:
        refund_error = await _refund_via_gateway(order, admin.username)
        if refund_error:
            raise HTTPException(status_code=400, detail=refund_error)

    # 回滚库存：未支付订单释放锁定；已支付订单把真实库存加回
    for item in order.items:
        if not item.sku_id:
            continue
        sku = (
            await db.execute(select(SKU).where(SKU.id == item.sku_id))
        ).scalar_one_or_none()
        if not sku:
            continue
        qty = item.quantity or 0
        if was_paid:
            sku.stock = (sku.stock or 0) + qty
            db.add(StockMovement(
                sku_id=sku.id, change_qty=qty,
                balance_after=sku.stock, reason="order_refund",
                reference=order.order_no, operator=admin.username,
            ))
        else:
            sku.locked_stock = max((sku.locked_stock or 0) - qty, 0)
            db.add(StockMovement(
                sku_id=sku.id, change_qty=0,
                balance_after=(sku.stock or 0) - (sku.locked_stock or 0),
                reason="order_cancel", reference=order.order_no,
                operator=admin.username,
            ))

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if was_paid:
        order.status = OrderStatus.REFUNDED
        for p in (order.payments or []):
            p.status = PaymentStatus.REFUNDED
    else:
        order.status = OrderStatus.CANCELLED
    order.cancelled_at = now
    order.cancel_reason = reason or None

    await db.commit()
    await db.refresh(order)
    return _order_to_admin_out(order, get_lang(request))


@router.post("/orders-search/{order_no}/complete", response_model=AdminOrderOut)
async def complete_order(
    order_no: str,
    request: Request,
    admin: AdminUser = Depends(require_admin(WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """确认收货 / 完成订单（把「待收货」置为「已完成」）"""
    order = await _load_order(db, order_no, with_customer=True)
    if order.status != OrderStatus.SHIPPED:
        raise HTTPException(status_code=400, detail="仅「待收货」订单可确认完成")
    order.status = OrderStatus.COMPLETED
    order.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    await db.refresh(order)
    return _order_to_admin_out(order, get_lang(request))


@router.post("/orders-search/{order_no}/confirm-payment", response_model=AdminOrderOut)
async def confirm_payment(
    order_no: str,
    request: Request,
    admin: AdminUser = Depends(require_admin(WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """线下收款：把「待付款」订单标记为已支付（转为待发货）"""
    from app.models import PaymentStatus

    order = await _load_order(db, order_no, with_customer=True)
    if order.status != OrderStatus.PENDING:
        raise HTTPException(status_code=400, detail="仅「待付款」订单可确认收款")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    order.status = OrderStatus.PAID
    order.paid_at = now
    for p in (order.payments or []):
        if p.status != PaymentStatus.SUCCESS:
            p.status = PaymentStatus.SUCCESS
            p.paid_at = now
    await db.commit()
    await db.refresh(order)
    return _order_to_admin_out(order, get_lang(request))


# ---------------------------------------------------------------- 批量 / 导出
@router.post("/orders-search/bulk", response_model=Message)
async def bulk_order_action(
    payload: OrderBatchIn,
    admin: AdminUser = Depends(require_admin(WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """订单批量操作：`ship`（批量发货）/ `note`（批量备注）"""
    nos = list(dict.fromkeys([n.strip() for n in payload.order_nos if n.strip()]))
    if not nos:
        raise HTTPException(status_code=400, detail="请提供订单号")
    # 先校验 action（在循环外），否则列表里全是无效订单号时会绕过校验直接返回成功
    if payload.action not in ("ship", "note"):
        raise HTTPException(status_code=400, detail="action 必须是 ship 或 note")

    orders = (
        await db.execute(select(Order).where(Order.order_no.in_(nos)))
    ).scalars().all()
    found = {o.order_no: o for o in orders}

    succeeded, skipped, errors = 0, 0, []
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for no in nos:
        order = found.get(no)
        if not order:
            skipped += 1
            errors.append(f"{no}: 订单不存在")
            continue
        if payload.action == "ship":
            if order.status != OrderStatus.PAID:
                skipped += 1
                errors.append(
                    f"{no}: 当前状态为「{order_status_label(order.status)}」，不可发货"
                )
                continue
            order.carrier = (payload.carrier or "").strip() or order.carrier
            order.tracking_no = (payload.tracking_no or "").strip() or order.tracking_no
            order.status = OrderStatus.SHIPPED
            order.shipped_at = now
            succeeded += 1
        else:
            order.admin_note = (payload.note or "").strip() or order.admin_note
            succeeded += 1

    await db.commit()
    detail = f"；{'；'.join(errors[:5])}" if errors else ""
    return Message(message=f"成功 {succeeded} 条，跳过 {skipped} 条{detail}")


@router.get("/orders-search-export")
async def export_orders(
    tab: str = Query("all"),
    order_no: str | None = None,
    product_id: str | None = None,
    receiver: str | None = None,
    phone: str | None = None,
    tracking_no: str | None = None,
    keyword: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(5000, ge=1, le=20000),
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """按当前筛选条件导出订单 CSV（UTF-8 BOM，Excel 直接打开不乱码）"""
    stmt = select(Order).options(
        selectinload(Order.items), selectinload(Order.customer)
    )
    stmt = _apply_order_filters(
        stmt, tab=tab, order_no=order_no, product_id=product_id, receiver=receiver,
        phone=phone, tracking_no=tracking_no, keyword=keyword,
        date_from=date_from, date_to=date_to,
    )
    stmt = stmt.order_by(Order.id.desc()).limit(limit)
    orders = (await db.execute(stmt)).scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "订单号", "订单状态", "商品总价", "实收金额", "商品件数",
        "收货人", "收件人手机号", "收货地址", "快递公司", "快递单号",
        "买家邮箱", "商品明细", "商家备注", "下单时间", "发货时间",
    ])
    for o in orders:
        detail = " | ".join(
            f"{it.product_name}({it.sku_code})×{it.quantity}" for it in (o.items or [])
        )
        writer.writerow([
            o.order_no,
            order_status_label(o.status),
            f"{o.subtotal:.2f}",
            f"{o.total_amount:.2f}",
            sum(it.quantity or 0 for it in (o.items or [])),
            o.receiver_name,
            o.receiver_phone,
            o.receiver_address,
            o.carrier or "",
            o.tracking_no or "",
            (o.customer.email if o.customer else ""),
            detail,
            o.admin_note or "",
            o.created_at.strftime("%Y-%m-%d %H:%M:%S") if o.created_at else "",
            o.shipped_at.strftime("%Y-%m-%d %H:%M:%S") if o.shipped_at else "",
        ])

    # BOM 让 Excel 正确识别 UTF-8
    data = ("\ufeff" + buf.getvalue()).encode("utf-8")
    filename = f"orders_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )