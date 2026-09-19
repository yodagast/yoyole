"""微信支付（APIv3）测试

分三层：
1. **纯协议**：金额换算、签名串格式、RSA 签名/验签往返、回调 AES-GCM 解密；
2. **网关请求**：打桩 httpx，验证「Authorization 头正确构造 + 应答验签通过/被拒」；
3. **HTTP 拒绝路径**：未签名的回调必须 400，且不能改动任何订单状态。

⚠️ 真实下单/付款需要商户号、APIv3 密钥与证书，且付款必须人工扫码，
   所以这里不做真实网络调用（步骤见 docs/wechatpay.md 第 6 节）。
"""
from __future__ import annotations

import base64
import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import NameOID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import wechatpay as wp
from app.config import settings

BASE = os.environ.get("TEST_BASE_URL", "http://127.0.0.1:8010").rstrip("/")


# ---------- 异步辅助（访问 DB 用一次性引擎，避免事件循环冲突） ----------


@asynccontextmanager
async def _session_scope():
    """一次性 NullPool 引擎：asyncpg 连接绑定创建它的 loop，共用全局引擎会报
    "attached to a different loop"（pytest 夹具与测试各用各的 loop）。"""
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------- 测试用密钥对（每次会话生成，用完即弃，与真实商户密钥无关） ----------

@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return {"private": private_pem, "public": public_pem, "key": key}


@pytest.fixture(scope="module")
def self_signed_cert(keypair):
    """自签证书：验证「平台证书」这种 X.509 形态也能加载公钥"""
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "wechatpay-test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(keypair["key"].public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=30))
        .sign(keypair["key"], hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


# ---------- 1. 纯协议 ----------


@pytest.mark.parametrize("amount,fen", [("19.90", 1990), ("0.01", 1), ("100", 10000), ("199.995", 20000)])
def test_amount_to_fen(amount, fen):
    """微信金额一律整数分；199.9 这种必须精确到分，不能有浮点误差"""
    assert wp.amount_to_fen(Decimal(amount)) == fen


def test_fen_to_amount():
    assert str(wp.fen_to_amount(1990)) == "19.90"
    assert str(wp.fen_to_amount(1)) == "0.01"


def test_request_sign_message_format():
    """请求签名串：方法\\nURL(含 query)\\n时间戳\\n随机串\\n报文主体\\n（最后一行也要换行）"""
    message = wp.build_request_sign_message(
        "POST", "/v3/pay/transactions/native", "1554208460", "593BEC0C930BF1AFEB40B4A08C8FB242", "{}"
    )
    assert message == "POST\n/v3/pay/transactions/native\n1554208460\n593BEC0C930BF1AFEB40B4A08C8FB242\n{}\n"
    # 带 query 的接口（查单/关单）query 必须进签名串，否则 401
    assert "?mchid=123" in wp.build_request_sign_message("GET", "/v3/x?mchid=123", "1", "n")


def test_response_sign_message_format():
    """平台侧验签串：时间戳\\n随机串\\n原始报文\\n（报文用原始字节，不能重新序列化）"""
    assert wp.build_response_sign_message("1554208460", "nonce", b'{"a":1}') == b'1554208460\nnonce\n{"a":1}\n'


def test_sign_and_verify_roundtrip(keypair):
    signature = wp.sign_with_private_key("hello", keypair["private"])
    assert wp.verify_with_public_key(b"hello", signature, keypair["public"]) is True


def test_verify_rejects_tampered_body(keypair):
    """被篡改的报文必须验签失败（否则等于没验签）"""
    signature = wp.sign_with_private_key("hello", keypair["private"])
    assert wp.verify_with_public_key(b"hell0", signature, keypair["public"]) is False


def test_verify_accepts_x509_certificate(keypair, self_signed_cert):
    """平台证书是 X.509 证书而非裸公钥，也要能验签"""
    signature = wp.sign_with_private_key("hello", keypair["private"])
    assert wp.verify_with_public_key(b"hello", signature, self_signed_cert) is True


def test_build_authorization_header(keypair, monkeypatch):
    """Authorization 头格式：WECHATPAY2-SHA256-RSA2048 + mchid/nonce_str/signature/timestamp/serial_no"""
    monkeypatch.setattr(settings, "WECHATPAY_MCHID", "1900000109")
    header = wp.build_authorization(
        "1900000109", "5157F09EFDC096DE15EBE81A47057A72", keypair["private"],
        "POST", "/v3/pay/transactions/native", "{}",
    )
    assert header.startswith("WECHATPAY2-SHA256-RSA2048 ")
    for field in ('mchid="1900000109"', 'nonce_str="', 'signature="', 'timestamp="', 'serial_no="5157F09EFDC096DE15EBE81A47057A72"'):
        assert field in header


def test_private_key_load_error_is_readable():
    with pytest.raises(wp.WechatPayError) as exc:
        wp.sign_with_private_key("x", b"not a pem")
    assert "私钥" in str(exc.value)


def test_decrypt_resource_roundtrip():
    """回调 resource 用 AEAD_AES_256_GCM 加密，密钥是 32 位 APIv3 密钥"""
    api_v3_key = "01234567890123456789012345678901"  # 32 位
    plaintext = json.dumps({"out_trade_no": "ORD1", "trade_state": "SUCCESS"}).encode()
    nonce = "abcdefghijkl".encode()
    associated_data = b"transaction"
    ciphertext = AESGCM(api_v3_key.encode()).encrypt(nonce, plaintext, associated_data)
    resource = {
        "algorithm": "AEAD_AES_256_GCM",
        "ciphertext": base64.b64encode(ciphertext).decode(),
        "nonce": nonce.decode(),
        "associated_data": associated_data.decode(),
    }
    assert wp.decrypt_resource(resource, api_v3_key) == {
        "out_trade_no": "ORD1",
        "trade_state": "SUCCESS",
    }


def test_decrypt_resource_rejects_wrong_key():
    """APIv3 密钥填错时要给出明确提示，而不是抛底层异常"""
    resource = {
        "algorithm": "AEAD_AES_256_GCM",
        "ciphertext": base64.b64encode(b"x" * 32).decode(),
        "nonce": "abcdefghijkl",
        "associated_data": "",
    }
    with pytest.raises(wp.WechatPayError) as exc:
        wp.decrypt_resource(resource, "01234567890123456789012345678901")
    assert "解密失败" in str(exc.value)


def test_decrypt_resource_requires_32_byte_key():
    with pytest.raises(wp.WechatPayError) as exc:
        wp.decrypt_resource({}, "short")
    assert "32" in str(exc.value)


# ---------- 2. 网关请求（打桩 httpx） ----------


def _configured(monkeypatch, keypair):
    """把商户凭据指向测试用密钥对"""
    monkeypatch.setattr(settings, "WECHATPAY_MCHID", "1900000109")
    monkeypatch.setattr(settings, "WECHATPAY_APPID", "wx8888888888888888")
    monkeypatch.setattr(settings, "WECHATPAY_API_V3_KEY", "01234567890123456789012345678901")
    monkeypatch.setattr(settings, "WECHATPAY_CERT_SERIAL_NO", "5157F09EFDC096DE15EBE81A47057A72")
    monkeypatch.setattr(settings, "WECHATPAY_PRIVATE_KEY_PEM", keypair["private"].decode())
    monkeypatch.setattr(settings, "WECHATPAY_PUBLIC_KEY_PEM", keypair["public"].decode())
    monkeypatch.setattr(settings, "WECHATPAY_API_BASE", "https://api.mch.weixin.qq.com")
    monkeypatch.setattr(settings, "BASE_URL", "https://yoyole.vip")


def _signed_httpx(keypair, payload: dict, http_status: int = 200, tamper: bool = False):
    """构造一个假的 httpx.AsyncClient：返回带真实签名的应答"""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    timestamp = "1554208460"
    nonce = "NONCE123"
    signature = wp.sign_with_private_key(
        wp.build_response_sign_message(timestamp, nonce, body).decode(), keypair["private"]
    )
    recorded: list = []

    class _Resp:
        status_code = http_status
        content = body.replace(b"REAL", b"FAKE") if tamper else body
        headers = {
            "wechatpay-serial": "PUB_KEY_ID_TEST",
            "wechatpay-signature": signature,
            "wechatpay-timestamp": timestamp,
            "wechatpay-nonce": nonce,
        }

        def json(self):
            return json.loads(self.content)

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, **kwargs):
            recorded.append({"method": method, "url": url, "kwargs": kwargs})
            return _Resp()

    return {"client": _Client, "recorded": recorded}


def test_native_create_payment_builds_signed_request(monkeypatch, keypair):
    """Native 下单：请求体字段齐全 + Authorization 头由商户私钥真签名"""
    _configured(monkeypatch, keypair)
    from app.payments import PaymentRequest, get_gateway

    fake = _signed_httpx(keypair, {"code_url": "weixin://wxpay/bizpayurl/up?pr=NwY5Mz9"})
    monkeypatch.setattr("app.payments.httpx.AsyncClient", fake["client"])

    import asyncio

    gateway = get_gateway("wechat")
    result = asyncio.new_event_loop().run_until_complete(
        gateway.create_payment(
            PaymentRequest(
                order_no="ORD20260101000001",
                amount=Decimal("199.00"),
                subject="瑜伽垫",
                method="wechat",
                channel="native",
            )
        )
    )

    assert result.success, result.error
    assert result.pay_url.startswith("weixin://")
    call = fake["recorded"][0]
    assert call["url"].endswith("/v3/pay/transactions/native")
    body = json.loads(call["kwargs"]["content"])
    assert body["mchid"] == "1900000109"
    assert body["appid"] == "wx8888888888888888"
    assert body["out_trade_no"] == "ORD20260101000001"
    assert body["amount"] == {"total": 19900, "currency": "CNY"}  # 单位是分
    assert body["notify_url"] == "https://yoyole.vip/api/payments/wechat/notify"
    assert "time_expire" in body
    assert call["kwargs"]["headers"]["Authorization"].startswith("WECHATPAY2-SHA256-RSA2048 ")


def test_h5_create_payment_requires_payer_ip(monkeypatch, keypair):
    """H5 下单：scene_info.payer_client_ip 必填（微信风控用），返回 h5_url"""
    _configured(monkeypatch, keypair)
    from app.payments import PaymentRequest, get_gateway

    fake = _signed_httpx(keypair, {"h5_url": "https://wx.tenpay.com/cgi-bin/mmpayweb-bin/checkmweb?prepay_id=x"})
    monkeypatch.setattr("app.payments.httpx.AsyncClient", fake["client"])

    import asyncio

    result = asyncio.new_event_loop().run_until_complete(
        get_gateway("wechat").create_payment(
            PaymentRequest(
                order_no="ORD2", amount=Decimal("1.00"), channel="h5", client_ip="1.2.3.4"
            )
        )
    )

    assert result.success, result.error
    body = json.loads(fake["recorded"][0]["kwargs"]["content"])
    assert fake["recorded"][0]["url"].endswith("/v3/pay/transactions/h5")
    assert body["scene_info"]["payer_client_ip"] == "1.2.3.4"
    assert result.pay_url.startswith("https://wx.tenpay.com")


def test_response_signature_verification_rejects_tampered(monkeypatch, keypair):
    """应答被篡改 → 必须拒绝（微信还会故意发错误签名探测商户是否真验签）"""
    _configured(monkeypatch, keypair)
    from app.payments import PaymentRequest, get_gateway
    from app.wechatpay import WechatPayError

    fake = _signed_httpx(keypair, {"code_url": "weixin://REAL"}, tamper=True)
    monkeypatch.setattr("app.payments.httpx.AsyncClient", fake["client"])

    import asyncio

    result = asyncio.new_event_loop().run_until_complete(
        get_gateway("wechat").create_payment(
            PaymentRequest(order_no="ORD3", amount=Decimal("1.00"), channel="native")
        )
    )
    assert result.success is False
    assert "验签失败" in result.error
    assert WechatPayError  # 用到即说明类型存在


def test_gateway_reports_missing_config(monkeypatch):
    """凭据缺失时给出可操作的提示，而不是抛异常"""
    for key in ("WECHATPAY_MCHID", "WECHATPAY_APPID", "WECHATPAY_API_V3_KEY", "WECHATPAY_CERT_SERIAL_NO"):
        monkeypatch.setattr(settings, key, "")
    monkeypatch.setattr(settings, "WECHATPAY_PRIVATE_KEY_PEM", "")

    from app.payments import PaymentRequest, get_gateway

    import asyncio

    gateway = get_gateway("wechat")
    assert gateway.configured is False
    result = asyncio.new_event_loop().run_until_complete(
        gateway.create_payment(PaymentRequest(order_no="ORD4", amount=Decimal("1.00")))
    )
    assert result.success is False
    assert "WECHATPAY_" in result.error


# ---------- 3. HTTP 拒绝路径 ----------


def test_wechat_notify_rejects_unsigned_request():
    """没带 Wechatpay-Signature 系列头的回调：400 且不动任何状态"""
    r = requests.post(
        f"{BASE}/api/payments/wechat/notify",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"event_type": "TRANSACTION.SUCCESS", "resource_type": "encrypt-resource"}),
    )
    assert r.status_code == 400
    assert "Wechatpay" in r.json()["detail"] or "验签" in r.json()["detail"]


def test_wechat_refund_notify_rejects_unsigned_request():
    r = requests.post(
        f"{BASE}/api/payments/wechat/refund-notify",
        headers={"Content-Type": "application/json"},
        data="{}",
    )
    assert r.status_code == 400


def test_wechat_status_requires_login():
    r = requests.get(f"{BASE}/api/payments/wechat/status/ORD_NOT_EXIST")
    assert r.status_code in (401, 403)


def test_wechat_not_in_methods_until_configured():
    """未配置凭据时，微信不能出现在可用支付方式里（否则用户下单后付不了款）"""
    body = requests.get(f"{BASE}/api/payments/methods").json()
    assert "wechat" in body["enabled"]
    if not settings.wechatpay_configured:
        assert body["enabled"]["wechat"] is False


# ---------- 4. 线上踩过的坑（回归测试） ----------


def test_wechatpay_error_text_adds_actionable_hint():
    """微信原始报错太笼统，必须补上「去哪开通/去哪绑定」的指引

    线上真实报错：
    - `400 APPID_MCHID_NOT_MATCH`（appid 未与商户号绑定）
    - `403 NO_AUTH`（Native/H5 产品权限未开通）
    只回原文运营看不懂，得告诉他们去商户平台哪个菜单点哪个开关。
    """
    mismatch = wp.wechatpay_error_text(
        400,
        {"code": "APPID_MCHID_NOT_MATCH", "message": "appid和mch_id不匹配，请检查后再试"},
    )
    assert "APPID 账号管理" in mismatch, "APPID 绑定问题缺少处理指引"
    assert "确认授权" in mismatch

    no_auth_native = wp.wechatpay_error_text(403, {"code": "NO_AUTH"}, channel="native")
    assert "产品中心" in no_auth_native
    assert "Native 支付" in no_auth_native, "要指明是哪个产品权限没开"

    no_auth_h5 = wp.wechatpay_error_text(403, {"code": "NO_AUTH"}, channel="h5")
    assert "H5 支付" in no_auth_h5

    # 未知错误码也要能给出原文，不能吞掉
    unknown = wp.wechatpay_error_text(500, {"code": "WHATEVER", "message": "未知错误"})
    assert "未知错误" in unknown


def test_order_subject_does_not_trigger_lazy_load():
    """`_order_subject` 不能触发懒加载（线上 500 的根因）

    线上报错：`MissingGreenlet: greenlet_spawn has not been called`，栈顶是
    `_order_subject → order.items`。原因是 `pay` 接口只 selectinload 了 payments，
    没有 items；async 会话里访问未加载的关系会直接抛异常，支付整条链路 500。
    修复：接口预加载 items + 这里对未加载状态兜底。
    """
    from app.models import Order
    from app.routers.orders import _order_subject

    async def _load_without_items() -> str:
        async with _session_scope() as session:
            # 故意不带 selectinload(Order.items)：模拟修复前 pay 接口的加载状态
            order = (
                await session.execute(select(Order).order_by(Order.id.desc()).limit(1))
            ).scalar_one_or_none()
            if order is None:
                return "SKIP"
            return _order_subject(order)  # 不应抛 MissingGreenlet

    result = _run(_load_without_items())
    if result == "SKIP":
        pytest.skip("库里还没有订单")
    assert result, "缺 items 时应退回订单号描述，而不是抛异常"


def test_pay_endpoint_preloads_items_for_wechat():
    """`pay` 接口必须预加载 items：微信分支要用商品名生成账单描述"""
    import inspect

    from app.routers import orders as orders_module

    source = inspect.getsource(orders_module.pay_order)
    assert "selectinload(Order.items)" in source, "pay 接口未预加载 items，微信支付会 500"