"""微信支付 APIv3 底层协议实现：签名、验签、回调解密、金额换算

为什么手写而不用官方 SDK：官方只提供 Java/PHP/Go SDK，Python 侧没有官方库；
第三方库（wechatpayv3）质量参差且引入额外维护成本。这里只实现本项目用到的
最小集合，并用官方文档给出的**已知向量**做单元测试（见 test/test_wechatpay.py）。

三个协议要点（违反就会 401 / 验签失败）：
1. 请求签名串是「方法\\nURL\\n时间戳\\n随机串\\n报文主体\\n」，注意 **URL 必须带上 query string**，
   且报文主体为 GET 等空体请求时是空串；
2. 平台侧签名（应答/回调）用「时间戳\\n随机串\\n报文主体\\n」，**报文主体必须用原始字节**，
   解析后重新序列化会验签失败；
3. 回调报文是 AES-256-GCM 加密的（AEAD_AES_256_GCM），密钥是商户平台设置的 32 位 APIv3 密钥，
   nonce 与 associated_data 来自 resource 对象。
"""
from __future__ import annotations

import base64
import json
import random
import string
import time
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

# 导入放在函数里会让下方的测试打桩变复杂，这里统一在顶部导入
import httpx
from cryptography.exceptions import InvalidTag, InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class WechatPayError(RuntimeError):
    """微信支付调用/配置异常（message 可直接展示给用户）"""


def amount_to_fen(amount: Decimal | str | float) -> int:
    """元 → 分（微信金额一律整数分；四舍五入到分，避免 19.9 → 1989 的浮点误差）"""
    return int(Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100)


def fen_to_amount(fen: int | str) -> Decimal:
    """分 → 元"""
    return (Decimal(str(fen)) / 100).quantize(Decimal("0.01"))


def nonce_str(length: int = 32) -> str:
    """随机串（微信要求不超过 32 位，大小写字母+数字）"""
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def build_request_sign_message(
    method: str, url_path: str, timestamp: str, nonce: str, body: str = ""
) -> str:
    """构造请求签名串（商户侧私钥签名用）

    ⚠️ url_path 必须带 query（如 `/v3/pay/transactions/out-trade-no/X?mchid=Y`），
    漏了 query 会直接 401 SIGN_ERROR。
    """
    return f"{method.upper()}\n{url_path}\n{timestamp}\n{nonce}\n{body}\n"


def build_response_sign_message(timestamp: str, nonce: str, body: str | bytes) -> bytes:
    """构造平台侧验签串（应答/回调）

    body 用原始字节拼，保证与微信实际签名时看到的内容逐字节一致。
    """
    if isinstance(body, str):
        body = body.encode("utf-8")
    return timestamp.encode() + b"\n" + nonce.encode() + b"\n" + body + b"\n"


def sign_with_private_key(message: str, private_key_pem: bytes | str) -> str:
    """用商户 API 证书私钥做 SHA256withRSA 签名，返回 base64"""
    if isinstance(private_key_pem, str):
        private_key_pem = private_key_pem.encode()
    try:
        key = serialization.load_pem_private_key(private_key_pem, password=None)
    except Exception as exc:  # noqa: BLE001
        raise WechatPayError("商户 API 证书私钥无法加载，请检查 apiclient_key.pem 是否完整") from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise WechatPayError("商户 API 证书私钥不是 RSA 私钥")
    signature = key.sign(message.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode()


def verify_with_public_key(message: bytes, signature_b64: str, public_key_pem: bytes | str) -> bool:
    """用微信支付公钥/平台证书验证 SHA256withRSA 签名"""
    if isinstance(public_key_pem, str):
        public_key_pem = public_key_pem.encode()
    try:
        key = serialization.load_pem_public_key(public_key_pem)
    except Exception:  # noqa: BLE001
        # 平台证书是 X.509 证书而非裸公钥，这里兼容一下
        from cryptography import x509

        key = x509.load_pem_x509_certificate(public_key_pem).public_key()
    if not isinstance(key, rsa.RSAPublicKey):
        return False
    try:
        key.verify(base64.b64decode(signature_b64), message, padding.PKCS1v15(), hashes.SHA256())
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def decrypt_resource(resource: dict[str, Any], api_v3_key: str) -> dict[str, Any]:
    """解密回调报文里的 resource（AEAD_AES_256_GCM）

    ciphertext 是 base64(密文+16 字节 auth tag)，nonce 与 associated_data 都是明文。
    """
    if not api_v3_key or len(api_v3_key.encode()) != 32:
        raise WechatPayError("APIv3 密钥必须是 32 位，请检查 .env 的 WECHATPAY_API_V3_KEY")
    algorithm = (resource.get("algorithm") or "AEAD_AES_256_GCM").upper()
    if algorithm != "AEAD_AES_256_GCM":
        raise WechatPayError(f"不支持的回调加密算法：{algorithm}")
    ciphertext = base64.b64decode(resource.get("ciphertext") or "")
    nonce = (resource.get("nonce") or "").encode()
    associated_data = (resource.get("associated_data") or "").encode()
    if not 8 <= len(nonce) <= 128:
        raise WechatPayError("回调报文的 nonce 长度非法，无法解密")
    try:
        # ⚠️ cryptography 的参数顺序是 decrypt(nonce, data, associated_data)
        plaintext = AESGCM(api_v3_key.encode()).decrypt(nonce, ciphertext, associated_data)
    except InvalidTag as exc:
        raise WechatPayError("回调报文解密失败，请核对 .env 的 WECHATPAY_API_V3_KEY") from exc
    return json.loads(plaintext.decode("utf-8"))


def build_authorization(
    mchid: str,
    serial_no: str,
    private_key_pem: bytes | str,
    method: str,
    url_path: str,
    body: str = "",
) -> str:
    """构造 Authorization 请求头（WECHATPAY2-SHA256-RSA2048）"""
    timestamp = str(int(time.time()))
    nonce = nonce_str()
    message = build_request_sign_message(method, url_path, timestamp, nonce, body)
    signature = sign_with_private_key(message, private_key_pem)
    return (
        f'WECHATPAY2-SHA256-RSA2048 mchid="{mchid}",'
        f'nonce_str="{nonce}",signature="{signature}",'
        f'timestamp="{timestamp}",serial_no="{serial_no}"'
    )


# 错误码 → 可操作的中文指引
# 微信原始 message 偏笼统（如「商户号该产品权限未开通」不说开哪个），
# 这里补上「去哪点、点哪个」，让运营拿到报错就能自己修，不用来回问开发。
_ERROR_HINTS: dict[str, str] = {
    "APPID_MCHID_NOT_MATCH": (
        "appid 与商户号未绑定。处理：商户平台 → 产品中心 → APPID 账号管理 → 关联 AppID，"
        "提交后用该 appid 对应的平台（公众号/小程序/开放平台）确认授权"
    ),
    "MCH_NOT_EXISTS": "商户号不存在，请核对 .env 的 WECHATPAY_MCHID",
    "NO_AUTH": (
        "该产品权限未开通。处理：商户平台 → 产品中心，按需开通"
        "「Native 支付」（PC 扫码）与「H5 支付」（手机网页），"
        "H5 还需要额外配置 H5 支付域名"
    ),
    "SIGN_ERROR": (
        "签名验证不通过。处理：核对 WECHATPAY_CERT_SERIAL_NO 与 apiclient_key.pem 是否配套；"
        "若接口带 query（如查单），query 必须计入签名串"
    ),
    "PARAM_ERROR": "参数错误，请查看微信返回的具体字段说明",
    "OUT_TRADE_NO_USED": "商户订单号重复，请重新下单（同一订单号不能重复下单）",
    "ORDER_CLOSED": "订单已关闭，请重新下单",
    "ORDER_PAID": "订单已支付，无需重复支付",
    "FREQUENCY_LIMITED": "请求过于频繁，请稍后重试",
    "SYSTEM_ERROR": "微信支付系统异常，请稍后用相同参数重试",
    "NOT_ENOUGH": "商户账户余额不足，无法退款",
    "RESOURCE_NOT_EXISTS": "订单号不存在，或该订单未支付（未支付不能退款）",
}

# 网页端收款必须开通的产品权限：报错时提示用户去开对应权限
_WEB_PRODUCT_SCOPE = {
    "native": "Native 支付（PC 扫码）",
    "h5": "H5 支付（手机网页）",
}


def wechatpay_error_text(status_code: int, data: dict[str, Any], channel: str = "") -> str:
    """微信错误体 → 「原文 + 去哪修」的可操作文案"""
    code = str(data.get("code") or "")
    raw = data.get("message") or code or "未知错误"
    parts = [f"微信支付返回 {status_code}：{raw}"]
    hint = _ERROR_HINTS.get(code)
    if hint:
        if code == "NO_AUTH" and channel in _WEB_PRODUCT_SCOPE:
            scope = _WEB_PRODUCT_SCOPE[channel]
            hint = f"{hint}（本次下单走的是「{scope}」，请确认该权限已开通）"
        parts.append(hint)
        # 微信参数类错误会带 field，一并带上便于定位是哪个字段不匹配
    details = data.get("details") or []
    fields = [d.get("field") for d in details if d.get("field")]
    if fields:
        parts.append(f"涉及字段：{', '.join(fields)}")
    return "；".join(parts)