"""PayPal 支付通道测试

覆盖三层：
1. **纯函数**：CNY→USD 汇率换算（含配置写错时的兜底）；
2. **HTTP 拒绝路径**：未验签的 webhook、非 PayPal 订单的 capture、密钥不下发前端；
3. **落账逻辑**：webhook 事件 → 订单变已支付 / 退款回滚库存 / 扣款失败置失败。

⚠️ 这里不测「真实 PayPal 网络调用」：capture 需要沙箱买家账号在 PayPal 页面点批准，
   只能人工验证（步骤见 docs/paypal.md 第 7 节）。落账逻辑通过直接调
   `_handle_paypal_event` 覆盖——签名只能由 PayPal 生成，绕过验签但走的代码路径完全一致。
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
import requests
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import NullPool

from app.config import settings
from app.models import Order, Payment, PaymentStatus, SKU
from app.routers.payments import _handle_paypal_event

BASE = os.environ.get("TEST_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
BUYER = {"email": "buyer@example.com", "password": "buyer123"}


# ---------- 工具 ----------


@asynccontextmanager
async def _session_scope():
    """一次性引擎 + 会话

    ⚠️ 不能用 `app.database` 的全局引擎：asyncpg 连接绑定在创建它的 loop 上，
    而 pytest 夹具、本文件里的 asyncio.run 各用各的 loop，共用连接池必然报
    "attached to a different loop"。自建 NullPool 引擎、用完就关，互不干扰。
    """
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


def _run(coro):
    """在独立事件循环里跑异步辅助代码"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(scope="module")
def buyer_token():
    r = requests.post(f"{BASE}/api/auth/login", json=BUYER)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _pick_sku_with_stock() -> int:
    products = requests.get(f"{BASE}/api/products", params={"limit": 20}).json()
    items = products if isinstance(products, list) else products.get("items", [])
    for p in items:
        detail = requests.get(f"{BASE}/api/products/{p['id']}").json()
        for sku in detail.get("skus", []):
            if sku.get("is_active") and (sku.get("available_stock") or 0) > 2:
                return sku["id"]
    pytest.skip("没有可用库存的 SKU")


def _make_order(buyer_token, method: str = "paypal") -> str:
    sku_id = _pick_sku_with_stock()
    requests.delete(f"{BASE}/api/cart", headers=buyer_token)
    r = requests.post(
        f"{BASE}/api/cart/items",
        json={"sku_id": sku_id, "quantity": 1},
        headers=buyer_token,
    )
    assert r.status_code == 201, r.text
    r = requests.post(
        f"{BASE}/api/orders/checkout",
        headers=buyer_token,
        json={
            "receiver_name": "PayPal 测试",
            "receiver_phone": "13800000000",
            "receiver_address": "浙江省杭州市余杭区测试路 1 号",
            "payment_method": method,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["order_no"]


@pytest.fixture
def paypal_order(buyer_token):
    """一个 PayPal 待支付订单（未真正调 PayPal 建单），测试结束顺带释放锁定库存"""
    order_no = _make_order(buyer_token, "paypal")
    yield order_no
    requests.post(f"{BASE}/api/orders/{order_no}/cancel", headers=buyer_token)


async def _prepare_paypal_payment(
    order_no: str,
    paypal_order_id: str,
    *,
    amount: str = "27.64",
    currency: str = "USD",
    status: PaymentStatus = PaymentStatus.PROCESSING,
) -> None:
    """把订单的支付记录改成「已在 PayPal 建单」的样子（跳过真实网络建单）"""
    async with _session_scope() as session:
        order = (
            await session.execute(select(Order).where(Order.order_no == order_no))
        ).scalar_one()
        payment = (
            await session.execute(select(Payment).where(Payment.order_id == order.id))
        ).scalar_one()
        payment.provider_order_id = paypal_order_id
        payment.amount = Decimal(amount)
        payment.currency = currency
        payment.fx_rate = Decimal("7.2")
        payment.status = status
        await session.commit()


async def _state(order_no: str) -> dict:
    async with _session_scope() as session:
        order = (
            await session.execute(
                select(Order).options(selectinload(Order.items)).where(Order.order_no == order_no)
            )
        ).scalar_one()
        payment = (
            await session.execute(select(Payment).where(Payment.order_id == order.id))
        ).scalar_one()
        stocks = {}
        for item in order.items:
            if not item.sku_id:
                continue
            sku = (await session.execute(select(SKU).where(SKU.id == item.sku_id))).scalar_one()
            stocks[item.sku_id] = {"stock": sku.stock, "locked": sku.locked_stock}
        return {
            "order_status": order.status.value,
            "payment_status": payment.status.value,
            "capture_id": payment.provider_capture_id,
            "stocks": stocks,
        }


def _capture_resource(paypal_order_id: str, capture_id: str = "CAP-TEST-1", value: str = "27.64"):
    return {
        "id": capture_id,
        "amount": {"value": value, "currency_code": "USD"},
        "supplementary_data": {"related_ids": {"order_id": paypal_order_id}},
    }


def _dispatch(event_type: str, resource: dict) -> bool:
    async def _do():
        async with _session_scope() as session:
            return await _handle_paypal_event(
                session, event_type, {"id": "WH-TEST", "event_type": event_type, "resource": resource}
            )

    return _run(_do())


# ---------- 1. 纯函数 ----------


@pytest.mark.parametrize("cny,expect", [("199.00", "27.64"), ("7.20", "1.00"), ("0.01", "0.01")])
def test_cny_to_usd(cny, expect):
    """站点按 CNY 定价、PayPal 按 USD 收单，汇率换算必须精确到分"""
    from app.payments import cny_to_usd

    assert str(cny_to_usd(Decimal(cny), 7.2)) == expect


@pytest.mark.parametrize("rate", [0, -1, "abc", None])
def test_cny_to_usd_survives_bad_rate(rate):
    """汇率配置写错不能把整站支付搞挂：退回 0.01 兜底/默认汇率"""
    from app.payments import cny_to_usd

    value = cny_to_usd(Decimal("72"), rate if rate is not None else 7.2)
    assert value > 0


def test_amount_str_is_two_decimals():
    """PayPal 只接受最多两位小数的字符串金额"""
    from app.payments import amount_str

    assert amount_str(Decimal("27.6")) == "27.60"
    assert amount_str("27.644") == "27.64"


# ---------- 2. HTTP 拒绝路径 ----------


def test_payment_methods_endpoint_exposes_switches():
    """通道开关由后端下发，前端不硬编码；响应里不能出现任何密钥"""
    r = requests.get(f"{BASE}/api/payments/methods")
    assert r.status_code == 200
    body = r.json()
    assert set(body["enabled"]) == {"mock", "alipay", "wechat", "stripe", "paypal"}
    assert body["enabled"]["mock"] is True
    assert body["enabled"]["alipay"] is False
    assert "secret" not in r.text.lower().replace("client_secret", "")  # 只允许出现字段名以外的内容
    assert "EPPht4OGCO" not in r.text  # 真实 client_secret 片段


def test_frontend_never_contains_client_secret():
    """client_secret 绝不能进前端资源（前端只该拿 client_id）"""
    js = requests.get(f"{BASE}/static/js/pymall.js").text
    assert "client_secret" not in js
    assert "PAYPAL_CLIENT_SECRET" not in js


def test_webhook_rejects_unverified_request(buyer_token):
    """没带 PayPal 签名头的请求必须被拒，且不能改动任何支付状态"""
    r = requests.post(
        f"{BASE}/api/payments/paypal/webhook",
        json={"event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": _capture_resource("X")},
    )
    assert r.status_code == 400
    assert "验签失败" in r.json()["detail"]


def test_capture_rejects_non_paypal_order(buyer_token):
    """mock 支付的订单不能走 PayPal capture"""
    order_no = _make_order(buyer_token, "mock")
    r = requests.post(
        f"{BASE}/api/payments/paypal/capture",
        headers=buyer_token,
        json={"order_no": order_no},
    )
    assert r.status_code == 400
    assert "不是 PayPal" in r.json()["detail"]
    requests.post(f"{BASE}/api/orders/{order_no}/cancel", headers=buyer_token)


def test_capture_requires_paypal_order_id(buyer_token, paypal_order):
    """还没在 PayPal 侧建单就调 capture：给出可读错误（而不是 500）"""
    r = requests.post(
        f"{BASE}/api/payments/paypal/capture",
        headers=buyer_token,
        json={"order_no": paypal_order},
    )
    assert r.status_code == 400
    assert "尚未在 PayPal 侧创建支付单" in r.json()["detail"]


def test_capture_requires_login(paypal_order):
    """capture 属于订单操作，必须登录"""
    r = requests.post(f"{BASE}/api/payments/paypal/capture", json={"order_no": paypal_order})
    assert r.status_code in (401, 403)


def test_capture_rejects_unknown_order(buyer_token):
    r = requests.post(
        f"{BASE}/api/payments/paypal/capture",
        headers=buyer_token,
        json={"order_no": "ORD_NOT_EXIST_PAYPAL"},
    )
    assert r.status_code == 404


# ---------- 3. 落账逻辑 ----------


def test_webhook_capture_completed_marks_order_paid(buyer_token, paypal_order):
    """掉单兜底：前端没调 capture，webhook 也要把订单落成已支付，并扣库存"""
    paypal_order_id = "PP-ORDER-COMPLETED"
    _run(_prepare_paypal_payment(paypal_order, paypal_order_id))
    before = _run(_state(paypal_order))

    assert _dispatch("PAYMENT.CAPTURE.COMPLETED", _capture_resource(paypal_order_id, "CAP-1")) is True

    after = _run(_state(paypal_order))
    assert after["payment_status"] == "success"
    assert after["order_status"] == "paid"
    assert after["capture_id"] == "CAP-1"
    # 真实库存扣减、锁定库存释放
    for sku_id, snap in before["stocks"].items():
        assert after["stocks"][sku_id]["stock"] == snap["stock"] - 1
        assert after["stocks"][sku_id]["locked"] == snap["locked"] - 1

    # 幂等：PayPal 会重投，重复事件不能再扣一次库存
    assert _dispatch("PAYMENT.CAPTURE.COMPLETED", _capture_resource(paypal_order_id, "CAP-1")) is True
    again = _run(_state(paypal_order))
    assert again["stocks"] == after["stocks"]
    assert again["order_status"] == "paid"


def test_webhook_refund_restores_stock(buyer_token, paypal_order):
    """PayPal 侧发起退款：订单置「退款/售后」并把库存加回来"""
    paypal_order_id = "PP-ORDER-REFUND"
    _run(_prepare_paypal_payment(paypal_order, paypal_order_id))
    initial = _run(_state(paypal_order))

    _dispatch("PAYMENT.CAPTURE.COMPLETED", _capture_resource(paypal_order_id, "CAP-2"))
    paid = _run(_state(paypal_order))
    assert paid["payment_status"] == "success"

    assert _dispatch("PAYMENT.CAPTURE.REFUNDED", _capture_resource(paypal_order_id, "CAP-2")) is True
    refunded = _run(_state(paypal_order))
    assert refunded["payment_status"] == "refunded"
    assert refunded["order_status"] == "refunded"
    for sku_id, snap in initial["stocks"].items():
        assert refunded["stocks"][sku_id]["stock"] == snap["stock"], "退款必须把库存加回原值"

    # 幂等：后台「取消并退款」也会让 PayPal 回一条同样的退款事件
    assert _dispatch("PAYMENT.CAPTURE.REFUNDED", _capture_resource(paypal_order_id, "CAP-2")) is True
    assert _run(_state(paypal_order))["stocks"] == refunded["stocks"]


def test_webhook_denied_marks_payment_failed(buyer_token, paypal_order):
    """扣款被拒/撤销：支付记录置失败，订单仍可重新支付"""
    paypal_order_id = "PP-ORDER-DENIED"
    _run(_prepare_paypal_payment(paypal_order, paypal_order_id))

    assert _dispatch("PAYMENT.CAPTURE.DENIED", _capture_resource(paypal_order_id)) is True
    state = _run(_state(paypal_order))
    assert state["payment_status"] == "failed"
    assert state["order_status"] == "pending"


def test_webhook_ignores_unknown_order_event():
    """事件找不到对应订单：不要抛异常（要返回 2xx，否则 PayPal 会一直重投）"""
    assert _dispatch("PAYMENT.CAPTURE.COMPLETED", _capture_resource("PP-ORDER-NOT-OURS")) is False
    assert _dispatch("SOME.UNKNOWN.EVENT", {"id": "X"}) is False


# ---------- 4. 验签函数（httpx 打桩，不依赖外网） ----------

# PayPal 官方要求：回传的事件必须与收到的原文**完全一致**，
# 解析成对象再序列化会验签失败，所以下面这条断言拿的是原始字节。
_RAW_EVENT = b'{"id":"WH-1","event_type":"PAYMENT.CAPTURE.COMPLETED","resource":{"id":"CAP-1"}}'


def _fake_httpx(recorder: list, verify_payload: dict, verify_status: int = 200):
    """替换 httpx.AsyncClient：token 请求回固定 token，验签请求回指定结果"""

    class _Resp:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, **kwargs):
            recorder.append({"url": url, "kwargs": kwargs})
            if "/v1/oauth2/token" in url:
                return _Resp(200, {"access_token": "fake-token", "expires_in": 3600})
            return _Resp(verify_status, verify_payload)

    return _Client


def _signature_headers() -> dict:
    return {
        "paypal-transmission-id": "9c1a2b30-1343-11ef-ac58-e32457403f67",
        "paypal-transmission-time": "2026-09-18T08:00:00Z",
        "paypal-cert-url": "https://api-m.sandbox.paypal.com/v1/notifications/certs/CERT-abc",
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-transmission-sig": "ZmFrZQ==",
    }


def test_verify_webhook_signature_sends_raw_body_verbatim(monkeypatch):
    """验签回传时，事件原文必须原封不动（不能被解析后重新序列化）"""
    from app import payments as pp

    recorder: list = []
    monkeypatch.setattr(pp.httpx, "AsyncClient", _fake_httpx(recorder, {"verification_status": "SUCCESS"}))

    ok, reason = _run(pp.verify_paypal_webhook(_signature_headers(), _RAW_EVENT))

    assert ok is True, reason
    verify_call = [c for c in recorder if "verify-webhook-signature" in c["url"]]
    assert verify_call, "没有调用 PayPal 验签接口"
    content = verify_call[-1]["kwargs"]["content"]
    assert _RAW_EVENT in content, "事件原文被改写过，PayPal 验签会失败"
    assert json.loads(content)["webhook_id"] == settings.PAYPAL_WEBHOOK_ID


def test_verify_webhook_signature_rejects_failure_response(monkeypatch):
    """PayPal 说签名不通过 → 必须拒绝（fail-closed）"""
    from app import payments as pp

    recorder: list = []
    monkeypatch.setattr(pp.httpx, "AsyncClient", _fake_httpx(recorder, {"verification_status": "FAILURE"}))

    ok, reason = _run(pp.verify_paypal_webhook(_signature_headers(), _RAW_EVENT))

    assert ok is False
    assert "签名" in reason


def test_verify_webhook_signature_rejects_bad_http_status(monkeypatch):
    """验签接口返回非 200 也不能放过"""
    from app import payments as pp

    recorder: list = []
    monkeypatch.setattr(
        pp.httpx, "AsyncClient", _fake_httpx(recorder, {"name": "INVALID"}, verify_status=400)
    )

    ok, _ = _run(pp.verify_paypal_webhook(_signature_headers(), _RAW_EVENT))
    assert ok is False


def test_verify_webhook_signature_requires_headers(monkeypatch):
    """缺 paypal-transmission-* 请求头：直接拒绝，不发网络请求"""
    from app import payments as pp

    recorder: list = []
    monkeypatch.setattr(pp.httpx, "AsyncClient", _fake_httpx(recorder, {"verification_status": "SUCCESS"}))

    ok, reason = _run(pp.verify_paypal_webhook({}, _RAW_EVENT))

    assert ok is False
    assert "请求头" in reason
    assert recorder == [], "缺头时不该发起任何请求"


def test_verify_webhook_signature_requires_webhook_id(monkeypatch):
    """没配 PAYPAL_WEBHOOK_ID 时必须拒绝（否则等于不验签就落账）"""
    from app import payments as pp

    monkeypatch.setattr(settings, "PAYPAL_WEBHOOK_ID", "")
    ok, reason = _run(pp.verify_paypal_webhook(_signature_headers(), _RAW_EVENT))

    assert ok is False
    assert "WEBHOOK_ID" in reason


def test_webhook_http_endpoint_rejects_request_without_signature():
    """带 event 但没签名的请求：400 且不落地（配置了 webhook_id 之后仍要 fail-closed）"""
    r = requests.post(
        f"{BASE}/api/payments/paypal/webhook",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": "CAP"}}),
    )
    assert r.status_code == 400
    assert "验签失败" in r.json()["detail"]


def test_webhook_endpoint_marks_paid_after_signature_verified(buyer_token, paypal_order, monkeypatch):
    """端点级闭环：验签通过 → 解析原始 body → 落账

    这里把「验签」打桩成通过（真签名只能由 PayPal 生成），其余全是线上同一份代码：
    raw body 读取、JSON 解析、事件分发、订单落账。
    """
    from app.routers import payments as router

    paypal_order_id = "PP-ORDER-ENDPOINT"
    _run(_prepare_paypal_payment(paypal_order, paypal_order_id))

    async def _verified(headers, raw_body):  # noqa: ARG001
        return True, ""

    monkeypatch.setattr(router, "verify_paypal_webhook", _verified)

    class _FakeRequest:
        def __init__(self, raw: bytes):
            self._raw = raw
            self.headers: dict = {}

        async def body(self) -> bytes:
            return self._raw

    raw = json.dumps(
        {
            "id": "WH-ENDPOINT",
            "event_type": "PAYMENT.CAPTURE.COMPLETED",
            "resource": _capture_resource(paypal_order_id, "CAP-ENDPOINT"),
        }
    ).encode()

    async def _call():
        async with _session_scope() as session:
            return await router.paypal_webhook(_FakeRequest(raw), session)

    result = _run(_call())
    assert result["received"] is True and result["handled"] is True

    state = _run(_state(paypal_order))
    assert state["payment_status"] == "success"
    assert state["order_status"] == "paid"
    assert state["capture_id"] == "CAP-ENDPOINT"


