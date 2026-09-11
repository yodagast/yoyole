"""支付网关抽象层：定义统一接口，提供模拟支付实现，便于扩展真实通道"""
from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


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


@dataclass
class PaymentResult:
    """支付发起结果"""
    success: bool
    transaction_no: str
    pay_url: str = ""
    provider_response: dict[str, Any] = field(default_factory=dict)
    error: str = ""


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


# 支付网关注册表：通过工厂模式获取实例，扩展新通道只需实现 BasePaymentGateway 并注册
GATEWAYS: dict[str, type[BasePaymentGateway]] = {
    "mock": MockGateway,
    "alipay": AlipayGateway,
    "wechat": WechatGateway,
    "stripe": StripeGateway,
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