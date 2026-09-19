"""支付网关抽象层：定义统一接口，提供模拟支付实现，便于扩展真实通道"""
from __future__ import annotations

import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping

import httpx

from app.config import settings
from app.wechatpay import (
    WechatPayError,
    amount_to_fen,
    build_authorization,
    build_response_sign_message,
    fen_to_amount,
    verify_with_public_key,
    wechatpay_error_text,
)

logger = logging.getLogger(__name__)


class PayPalError(RuntimeError):
    """PayPal 调用/配置异常（消息可直接回给前端展示）"""


def amount_str(value: Decimal | str | float) -> str:
    """PayPal 要求金额是「最多两位小数的字符串」，禁止科学计数法/浮点尾差"""
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def cny_to_usd(amount_cny: Decimal | str | float, rate: float | str | None = None) -> Decimal:
    """站点 CNY 金额 → PayPal 收单金额（USD）

    PayPal 的 CNY 只用于境内账户余额，跨境收单必须换外币，所以这里按固定汇率折算。
    **调用方必须把用到的汇率一起快照进支付记录**，否则汇率变动后回调金额会对不上。
    金额至少 0.01（PayPal 不接受 0 元订单）。
    """
    raw_rate = rate if rate is not None else settings.PAYPAL_FX_CNY_PER_USD
    try:
        dec_rate = Decimal(str(raw_rate))
    except Exception:  # noqa: BLE001  配置写错时退回默认汇率，避免整站支付挂掉
        dec_rate = Decimal("7.2")
    if dec_rate <= 0:
        dec_rate = Decimal("7.2")
    usd = (Decimal(str(amount_cny)) / dec_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return max(usd, Decimal("0.01"))


def paypal_error_text(status_code: int, data: dict[str, Any]) -> str:
    """从 PayPal 错误体里抽一句人话（PayPal 的错误结构层级较深）"""
    detail = data.get("error_description") or data.get("message") or ""
    issues = []
    for d in (data.get("details") or []):
        issue = d.get("issue") or ""
        desc = d.get("description") or ""
        if issue or desc:
            issues.append(f"{issue} {desc}".strip())
    if issues:
        detail = "; ".join(issues)
    return f"PayPal 返回 {status_code}：{detail or '未知错误'}"


@dataclass
class PaymentResult:
    """支付发起结果"""
    success: bool
    transaction_no: str
    pay_url: str = ""
    provider_response: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class PaymentRequest:
    """发起支付请求参数"""
    order_no: str
    amount: Decimal
    currency: str = "CNY"
    subject: str = ""
    method: str = "mock"
    return_url: str = ""
    notify_url: str = ""
    # 客户端形态（微信需要区分：手机浏览器走 H5，PC 走 Native 扫码）
    channel: str = "native"
    # 用户 IP（H5 下单必填，微信风控用）
    client_ip: str = ""


class BasePaymentGateway(ABC):
    """支付网关统一接口"""

    name: str = "base"

    @abstractmethod
    async def create_payment(self, req: PaymentRequest) -> PaymentResult:
        """创建支付交易，返回第三方支付跳转地址或预支付信息"""

    @abstractmethod
    async def query_payment(self, transaction_no: str) -> dict[str, Any]:
        """查询支付状态"""

    @abstractmethod
    async def handle_callback(self, payload: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        """处理异步回调，返回 (是否支付成功, 交易号, 原始响应)"""

    @abstractmethod
    async def refund(self, transaction_no: str, amount: Decimal) -> dict[str, Any]:
        """退款"""


class MockGateway(BasePaymentGateway):
    """模拟支付网关：用于开发与演示，支付发起即视为可支付状态，需调用确认接口完成支付"""

    name = "mock"

    async def create_payment(self, req: PaymentRequest) -> PaymentResult:
        txn_no = f"MOCK{int(time.time() * 1000)}{uuid.uuid4().hex[:8].upper()}"
        pay_url = f"/api/payments/mock/confirm?txn_no={txn_no}&amount={req.amount}"
        return PaymentResult(
            success=True,
            transaction_no=txn_no,
            pay_url=pay_url,
            provider_response={"channel": "mock", "order_no": req.order_no, "amount": str(req.amount)},
        )

    async def query_payment(self, transaction_no: str) -> dict[str, Any]:
        return {"status": "pending", "transaction_no": transaction_no}

    async def handle_callback(self, payload: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        txn_no = payload.get("txn_no", "")
        return True, txn_no, {"channel": "mock", "ack": "success"}

    async def refund(self, transaction_no: str, amount: Decimal) -> dict[str, Any]:
        return {"success": True, "transaction_no": transaction_no, "refund_amount": str(amount)}


class AlipayGateway(BasePaymentGateway):
    """支付宝网关占位实现（需配置合作方密钥后接入真实 API）"""

    name = "alipay"

    async def create_payment(self, req: PaymentRequest) -> PaymentResult:
        return PaymentResult(success=False, transaction_no="", error="Alipay 通道尚未配置")

    async def query_payment(self, transaction_no: str) -> dict[str, Any]:
        return {"status": "unknown"}

    async def handle_callback(self, payload: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        return False, "", {}

    async def refund(self, transaction_no: str, amount: Decimal) -> dict[str, Any]:
        return {"success": False, "error": "Alipay 通道尚未配置"}


class WechatGateway(BasePaymentGateway):
    """微信支付网关占位实现"""

    name = "wechat"

    async def create_payment(self, req: PaymentRequest) -> PaymentResult:
        return PaymentResult(success=False, transaction_no="", error="微信支付通道尚未配置")

    async def query_payment(self, transaction_no: str) -> dict[str, Any]:
        return {"status": "unknown"}

    async def handle_callback(self, payload: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        return False, "", {}

    async def refund(self, transaction_no: str, amount: Decimal) -> dict[str, Any]:
        return {"success": False, "error": "微信支付通道尚未配置"}


class StripeGateway(BasePaymentGateway):
    """Stripe 网关占位实现"""

    name = "stripe"

    async def create_payment(self, req: PaymentRequest) -> PaymentResult:
        return PaymentResult(success=False, transaction_no="", error="Stripe 通道尚未配置")

    async def query_payment(self, transaction_no: str) -> dict[str, Any]:
        return {"status": "unknown"}

    async def handle_callback(self, payload: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        return False, "", {}

    async def refund(self, transaction_no: str, amount: Decimal) -> dict[str, Any]:
        return {"success": False, "error": "Stripe 通道尚未配置"}


class PayPalGateway(BasePaymentGateway):
    """PayPal 跨境收单网关（Orders v2）

    完整链路（前端 JS SDK Buttons + 服务端 capture + webhook 兜底）：

        前端点「立即支付」→ POST /api/orders/{no}/pay → create_payment() 建 PayPal 订单
        → 弹窗里买家批准（onApprove）→ POST /api/payments/paypal/capture → capture_order() 扣款
        → 落账（_mark_payment_success）；掉单则由 webhook 补上

    约定：
    - 币种用 settings.PAYPAL_CURRENCY（USD）；站点按 CNY 定价，换算由调用方完成并快照汇率；
    - 金额一律字符串传输（amount_str）；
    - 写操作都带 PayPal-Request-Id 幂等头：网络重试/用户重复点击不会重复建单、重复扣款；
    - access_token 只留在进程内存（约 9 小时有效，提前 60s 续期），不落库、不打日志。
    """

    name = "paypal"

    def __init__(self) -> None:
        self.client_id = settings.PAYPAL_CLIENT_ID
        self.client_secret = settings.PAYPAL_CLIENT_SECRET
        self.mode = "live" if settings.paypal_live else "sandbox"
        self.api_base = settings.paypal_api_base
        self.sdk_host = settings.paypal_sdk_host
        self._token = ""
        self._token_expires_at = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    # ---------- 底层请求 ----------

    async def access_token(self) -> str:
        """OAuth2 client_credentials 取 token（带内存缓存）"""
        if not self.configured:
            raise PayPalError(
                "PayPal 未配置：请在 .env 填写 PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET"
            )
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{self.api_base}/v1/oauth2/token",
                    auth=(self.client_id, self.client_secret),
                    data={"grant_type": "client_credentials"},
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise PayPalError(f"PayPal 鉴权请求失败：{exc}") from exc
        if resp.status_code != 200:
            raise PayPalError(f"PayPal 鉴权失败（{resp.status_code}），请检查 .env 里的 client_id/secret")
        body = resp.json()
        self._token = body.get("access_token", "")
        self._token_expires_at = time.time() + float(body.get("expires_in") or 0)
        return self._token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        content: bytes | None = None,
        request_id: str = "",
    ) -> tuple[int, dict[str, Any]]:
        """统一请求入口：返回 (状态码, 解析后的 body)"""
        token = await self.access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if content is not None:
            headers["Content-Type"] = "application/json"
        elif method.upper() in ("POST", "PATCH", "PUT"):
            # ⚠️ PayPal 对无 body 的 POST 也要求声明 JSON（capture 缺了它直接 415）
            headers["Content-Type"] = "application/json"
        if request_id:
            # 幂等键：同样的键重复调用只会生效一次
            headers["PayPal-Request-Id"] = request_id
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.request(
                    method,
                    f"{self.api_base}{path}",
                    json=json_body if content is None else None,
                    content=content,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise PayPalError(f"PayPal 请求失败：{exc}") from exc
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001  少数错误响应没有 JSON body
            data = {}
        return resp.status_code, data if isinstance(data, dict) else {"raw": data}

    # ---------- 接口实现 ----------

    async def create_payment(self, req: PaymentRequest) -> PaymentResult:
        """建 PayPal 订单；成功时 transaction_no = PayPal order id"""
        if not self.configured:
            return PaymentResult(
                success=False,
                transaction_no="",
                error="PayPal 未配置：请在 .env 填写 PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET",
            )
        currency = req.currency or settings.PAYPAL_CURRENCY
        body = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": req.order_no,
                    "custom_id": req.order_no,          # 对账/排障时用来回查我们的订单号
                    "description": (req.subject or f"Order {req.order_no}")[:127],
                    "amount": {"currency_code": currency, "value": amount_str(req.amount)},
                }
            ],
        }
        try:
            status, data = await self._request(
                "POST", "/v2/checkout/orders", json_body=body, request_id=f"create-{req.order_no}"
            )
        except PayPalError as exc:
            return PaymentResult(success=False, transaction_no="", error=str(exc))
        if status >= 400:
            return PaymentResult(success=False, transaction_no="", error=paypal_error_text(status, data))
        order_id = data.get("id", "")
        return PaymentResult(
            success=True,
            transaction_no=order_id,
            # 兜底跳转地址（前端 SDK 不可用时可用，例如企业内网屏蔽了 paypal.com 的脚本）
            pay_url=f"{self.sdk_host}/checkoutnow?token={order_id}",
            provider_response={
                "id": order_id,
                "status": data.get("status"),
                "mode": self.mode,
                "currency": currency,
                "amount": amount_str(req.amount),
            },
        )

    async def query_payment(self, transaction_no: str) -> dict[str, Any]:
        """查 PayPal 订单状态（transaction_no = PayPal order id）"""
        if not self.configured or not transaction_no:
            return {"status": "unknown", "transaction_no": transaction_no}
        status, data = await self._request("GET", f"/v2/checkout/orders/{transaction_no}")
        if status >= 400:
            return {"status": "unknown", "transaction_no": transaction_no, "error": paypal_error_text(status, data)}
        return {
            "status": str(data.get("status", "")).lower() or "unknown",
            "transaction_no": transaction_no,
            "raw": data,
        }

    async def capture_order(self, paypal_order_id: str, order_no: str) -> dict[str, Any]:
        """捕获（真正扣款）

        返回 `{ok, capture_id, amount, currency, status, error}`。
        幂等处理：重复 capture 会收到 422 `ORDER_ALREADY_CAPTURED`，此时回查订单把
        已有 capture 读出来当成功处理——用户刷新页面/重复点击不该看到报错。
        """
        if not self.configured:
            return {"ok": False, "error": "PayPal 未配置"}
        status, data = await self._request(
            "POST",
            f"/v2/checkout/orders/{paypal_order_id}/capture",
            json_body={},  # capture 无需参数，但要带 JSON 空对象（否则 415）
            request_id=f"capture-{order_no}",
        )
        if status >= 400:
            issues = {d.get("issue") for d in (data.get("details") or [])}
            if "ORDER_ALREADY_CAPTURED" in issues:
                logger.info("[paypal] 订单 %s 已被捕获，改走回查", order_no)
                return await self._read_capture(paypal_order_id, order_no)
            if "ORDER_NOT_APPROVED" in issues:
                return {"ok": False, "error": "买家尚未在 PayPal 完成付款授权，请重新发起支付"}
            return {"ok": False, "error": paypal_error_text(status, data)}
        return self._extract_capture(data) or {
            "ok": False,
            "error": "PayPal 返回里没有 capture 信息，请联系客服人工核对",
        }

    async def _read_capture(self, paypal_order_id: str, order_no: str) -> dict[str, Any]:
        """回查订单，读出已存在的 capture（幂等补偿路径）"""
        status, data = await self._request("GET", f"/v2/checkout/orders/{paypal_order_id}")
        if status >= 400:
            return {"ok": False, "error": paypal_error_text(status, data)}
        return self._extract_capture(data) or {
            "ok": False,
            "error": f"PayPal 订单 {paypal_order_id} 无可用 capture（订单号 {order_no}）",
        }

    @staticmethod
    def _extract_capture(payload: dict[str, Any]) -> dict[str, Any] | None:
        """取第一笔 capture

        capture 响应与订单详情的结构一致，都是
        `purchase_units[].payments.captures[]`，所以两种来源共用这段解析。
        """
        for unit in (payload.get("purchase_units") or []):
            captures = ((unit.get("payments") or {}).get("captures") or [])
            if not captures:
                continue
            cap = captures[0]
            amount = cap.get("amount") or {}
            return {
                "ok": True,
                "capture_id": cap.get("id", ""),
                "status": cap.get("status", ""),
                "amount": amount.get("value", ""),
                "currency": amount.get("currency_code", ""),
                # custom_id 是建单时写进去的我们的订单号，用于对账兜底
                "order_no": unit.get("custom_id") or cap.get("custom_id") or "",
                "raw": cap,
            }
        return None

    async def handle_callback(self, payload: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        """PayPal 不走通用回调入口

        PayPal 的异步通知必须验签（raw body + paypal-* 请求头），通用入口拿不到这些，
        所以 PayPal 的事件由 `POST /api/payments/paypal/webhook` 专门处理。
        这里固定返回失败，避免被伪造的 JSON 直接判成功。
        """
        return False, "", {"error": "PayPal 回调请走 /api/payments/paypal/webhook（需验签）"}

    async def refund(self, transaction_no: str, amount: Decimal) -> dict[str, Any]:
        """退款：transaction_no 传 capture id（PayPal 只按 capture 退款）"""
        if not self.configured:
            return {"success": False, "error": "PayPal 未配置"}
        if not transaction_no:
            return {"success": False, "error": "缺少 PayPal capture id，无法退款"}
        body = {
            "amount": {
                "value": amount_str(amount),
                "currency_code": settings.PAYPAL_CURRENCY,
            }
        }
        status, data = await self._request(
            "POST",
            f"/v2/payments/captures/{transaction_no}/refund",
            json_body=body,
            request_id=f"refund-{transaction_no}-{amount_str(amount)}",
        )
        if status >= 400:
            return {"success": False, "error": paypal_error_text(status, data)}
        return {
            "success": True,
            "transaction_no": data.get("id", ""),
            "status": data.get("status", ""),
            "refund_amount": amount_str(amount),
        }


async def verify_paypal_webhook(headers: Mapping[str, str], raw_body: bytes) -> tuple[bool, str]:
    """校验 PayPal webhook 签名（postback 方式），返回 (是否可信, 失败原因)

    为什么不自己算 CRC32 + RSA：那需要额外依赖与证书缓存；postback 由 PayPal 自己校验，
    代价是多一次 HTTPS 往返（webhook 是低频路径，可接受）。

    ⚠️ 官方明确要求把事件原文**原封不动**回传，解析成对象再序列化可能验签失败，
    所以这里用字符串拼接把 raw body 直接嵌进 webhook_event 字段，不做二次序列化。
    """
    webhook_id = settings.PAYPAL_WEBHOOK_ID
    if not webhook_id:
        return False, "未配置 PAYPAL_WEBHOOK_ID"

    transmission_id = headers.get("paypal-transmission-id", "")
    transmission_time = headers.get("paypal-transmission-time", "")
    cert_url = headers.get("paypal-cert-url", "")
    auth_algo = headers.get("paypal-auth-algo", "")
    transmission_sig = headers.get("paypal-transmission-sig", "")
    if not all((transmission_id, transmission_time, cert_url, transmission_sig)):
        return False, "缺少 paypal-transmission-* 签名请求头"

    gateway = get_gateway("paypal")
    try:
        token = await gateway.access_token()
    except PayPalError as exc:
        return False, str(exc)

    jd = lambda v: json.dumps(v).encode()  # noqa: E731  保证转义正确
    payload = (
        b'{"transmission_id":' + jd(transmission_id)
        + b',"transmission_time":' + jd(transmission_time)
        + b',"cert_url":' + jd(cert_url)
        + b',"auth_algo":' + jd(auth_algo)
        + b',"transmission_sig":' + jd(transmission_sig)
        + b',"webhook_id":' + jd(webhook_id)
        + b',"webhook_event":' + raw_body.strip() + b"}"
    )
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{settings.paypal_api_base}/v1/notifications/verify-webhook-signature",
                content=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
    except httpx.HTTPError as exc:
        return False, f"验签请求失败：{exc}"
    body = {}
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        pass
    if resp.status_code != 200:
        return False, f"验签接口返回 {resp.status_code}"
    if str(body.get("verification_status", "")).upper() != "SUCCESS":
        return False, "签名校验未通过"
    return True, ""


class WechatGateway(BasePaymentGateway):
    """微信支付网关（APIv3）

    两种收款形态，按客户端自动选：
    - **Native**（PC 扫码）：下单返回 `code_url`，前端把它转成二维码；
    - **H5**（手机浏览器）：下单返回 `h5_url`，在浏览器里跳转拉起微信。

    两者都是**异步**付款：用户付款后微信通过回调通知我们（加前端轮询兜底），
    所以 create_payment 只负责「下单 + 给出让用户付钱的东西」。
    """

    name = "wechat"

    def __init__(self) -> None:
        self.api_base = (settings.WECHATPAY_API_BASE or "").rstrip("/")
        self.last_error = ""

    @property
    def configured(self) -> bool:
        return settings.wechatpay_configured

    # ---------- 底层请求：签名 + 验签 ----------

    async def _request(
        self,
        method: str,
        url_path: str,
        *,
        body: dict | None = None,
        skip_verify: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """发请求并（默认）验证应答签名，返回 (状态码, body)

        url_path 必须带 query（签名串包含 query，漏了会 401）。
        """
        if not self.configured:
            raise WechatPayError(
                "微信支付未配置：请在 .env 填 WECHATPAY_MCHID / APPID / API_V3_KEY / "
                "CERT_SERIAL_NO，并放好商户 API 证书私钥与微信支付公钥"
            )
        body_str = json.dumps(body, ensure_ascii=False, separators=(",", ":")) if body is not None else ""
        authorization = build_authorization(
            settings.WECHATPAY_MCHID,
            settings.WECHATPAY_CERT_SERIAL_NO,
            settings.wechatpay_private_key,
            method,
            url_path,
            body_str,
        )
        headers = {
            "Authorization": authorization,
            "Accept": "application/json",
            "User-Agent": "yoyole-wechatpay/1.0",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        kwargs: dict[str, Any] = {"headers": headers}
        if body is not None:
            kwargs["content"] = body_str.encode("utf-8")

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.request(method, f"{self.api_base}{url_path}", **kwargs)
        except httpx.HTTPError as exc:
            raise WechatPayError(f"微信支付请求失败：{exc}") from exc

        raw = resp.content
        if resp.status_code >= 400:
            try:
                data = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                data = {}
            return resp.status_code, data

        # 验签：微信会在极少数请求里发「探测流量」（错误签名）来判断商户是否真的验签，
        # 所以这里不能跳过——验签失败一律视为不可信应答。
        # 下载账单等返回二进制/文件流的接口不签名，调用方用 skip_verify 显式跳过。
        if not skip_verify:
            self._verify_response(resp.headers, raw)

        try:
            return resp.status_code, json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return resp.status_code, {}

    @staticmethod
    def _verify_response(headers: Mapping[str, str], raw_body: bytes) -> None:
        serial = headers.get("wechatpay-serial", "")
        signature = headers.get("wechatpay-signature", "")
        timestamp = headers.get("wechatpay-timestamp", "")
        nonce = headers.get("wechatpay-nonce", "")
        if not (serial and signature and timestamp and nonce):
            # 反代/CDN 过滤了微信扩展头时会走到这里（线上排查清单里很常见的一条）
            raise WechatPayError(
                "微信支付应答缺少 Wechatpay-Signature/Timestamp/Nonce 头，"
                "通常是反向代理或 CDN 过滤了扩展头"
            )
        public_key = settings.wechatpay_public_key_for(serial)
        if not public_key:
            raise WechatPayError(
                f"找不到序列号 {serial} 对应的微信支付公钥/平台证书，请检查 .env 与 cert/ 目录"
            )
        message = build_response_sign_message(timestamp, nonce, raw_body)
        if not verify_with_public_key(message, signature, public_key):
            raise WechatPayError("微信支付应答验签失败（可能是签名探测流量，或公钥与当前商户号不匹配）")

    # ---------- 接口实现 ----------

    async def create_payment(self, req: PaymentRequest) -> PaymentResult:
        """下单：手机浏览器走 H5，其它（PC）走 Native 扫码

        金额单位是「分」；out_trade_no 用我们的订单号，回调/查单都以它为准。
        """
        if not self.configured:
            return PaymentResult(
                success=False,
                transaction_no="",
                error="微信支付未配置：请在 .env 补齐 WECHATPAY_* 各项",
            )
        client = (req.channel or "native").lower()
        path = "/v3/pay/transactions/h5" if client == "h5" else "/v3/pay/transactions/native"
        expire_at = datetime.now().astimezone() + timedelta(
            minutes=settings.WECHATPAY_PAY_EXPIRE_MINUTES
        )
        body: dict[str, Any] = {
            "appid": settings.WECHATPAY_APPID,
            "mchid": settings.WECHATPAY_MCHID,
            "description": (req.subject or f"订单 {req.order_no}")[:127],
            "out_trade_no": req.order_no,
            "notify_url": req.notify_url or f"{settings.BASE_URL.rstrip('/')}/api/payments/wechat/notify",
            "time_expire": expire_at.strftime("%Y-%m-%dT%H:%M:%S%z").replace("+0800", "+08:00"),
            "attach": req.order_no[:128],
            "amount": {"total": amount_to_fen(req.amount), "currency": "CNY"},
        }
        if client == "h5":
            body["scene_info"] = {
                "payer_client_ip": req.client_ip or "127.0.0.1",
                "h5_info": {"type": "Wap", "app_name": "YOYOLE", "app_url": settings.BASE_URL},
            }

        try:
            status, data = await self._request("POST", path, body=body)
        except WechatPayError as exc:
            self.last_error = str(exc)
            return PaymentResult(success=False, transaction_no="", error=str(exc))
        if status >= 400:
            error = wechatpay_error_text(status, data, client)
            self.last_error = error
            return PaymentResult(success=False, transaction_no="", error=error)

        if client == "h5":
            h5_url = data.get("h5_url", "")
            return PaymentResult(
                success=True,
                transaction_no=req.order_no,
                pay_url=h5_url,
                provider_response={"channel": "h5", "h5_url": h5_url, "out_trade_no": req.order_no},
            )
        code_url = data.get("code_url", "")
        return PaymentResult(
            success=True,
            transaction_no=req.order_no,
            # 不是跳转地址，而是要渲染成二维码的 weixin:// 串，前端据此画码
            pay_url=code_url,
            provider_response={"channel": "native", "code_url": code_url, "out_trade_no": req.order_no},
        )

    async def query_payment(self, transaction_no: str) -> dict[str, Any]:
        """按商户订单号查单（transaction_no 即 out_trade_no）"""
        if not self.configured or not transaction_no:
            return {"status": "unknown", "transaction_no": transaction_no}
        path = (
            f"/v3/pay/transactions/out-trade-no/{transaction_no}"
            f"?mchid={settings.WECHATPAY_MCHID}"
        )
        try:
            status, data = await self._request("GET", path)
        except WechatPayError as exc:
            return {"status": "unknown", "transaction_no": transaction_no, "error": str(exc)}
        if status >= 400:
            return {"status": "unknown", "transaction_no": transaction_no, "error": wechatpay_error_text(status, data)}
        return {
            "status": str(data.get("trade_state", "")).lower() or "unknown",
            "transaction_no": transaction_no,
            "transaction_id": data.get("transaction_id", ""),
            "amount": data.get("amount"),
            "raw": data,
        }

    async def handle_callback(self, payload: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        """微信回调不走通用入口（需要验签 + AES-GCM 解密），见 /api/payments/wechat/notify"""
        return False, "", {"error": "微信支付回调请走 /api/payments/wechat/notify（需验签解密）"}

    async def refund(self, transaction_no: str, amount: Decimal) -> dict[str, Any]:
        """退款：transaction_no 传我们的订单号（微信侧按 out_trade_no 退）

        幂等键是 out_refund_no；重试必须用同一个，否则会重复退款。
        """
        if not self.configured:
            return {"success": False, "error": "微信支付未配置"}
        out_refund_no = f"RF{transaction_no}"[:64]
        body = {
            "out_trade_no": transaction_no,
            "out_refund_no": out_refund_no,
            "reason": "商户退款",
            "notify_url": f"{settings.BASE_URL.rstrip('/')}/api/payments/wechat/refund-notify",
            "amount": {
                "refund": amount_to_fen(amount),
                "total": amount_to_fen(amount),
                "currency": "CNY",
            },
        }
        try:
            status, data = await self._request("POST", "/v3/refund/domestic/refunds", body=body)
        except WechatPayError as exc:
            return {"success": False, "error": str(exc)}
        if status >= 400:
            return {"success": False, "error": wechatpay_error_text(status, data)}
        return {
            "success": True,
            "transaction_no": data.get("refund_id", ""),
            "status": data.get("status", ""),
            "refund_amount": str(fen_to_amount((data.get("amount") or {}).get("refund", 0))),
        }


# 支付网关注册表：通过工厂模式获取实例，扩展新通道只需实现 BasePaymentGateway 并注册
GATEWAYS: dict[str, type[BasePaymentGateway]] = {
    "mock": MockGateway,
    "alipay": AlipayGateway,
    "wechat": WechatGateway,
    "stripe": StripeGateway,
    "paypal": PayPalGateway,
}


# 网关实例缓存（无状态，单例即可）
_INSTANCES: dict[str, BasePaymentGateway] = {}


def get_gateway(method: str) -> BasePaymentGateway:
    """获取支付网关实例"""
    method = method.lower()
    if method not in GATEWAYS:
        raise ValueError(f"不支持的支付方式: {method}")
    if method not in _INSTANCES:
        _INSTANCES[method] = GATEWAYS[method]()
    return _INSTANCES[method]