"""应用配置模块"""
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """全局配置，可通过环境变量或 .env 覆盖"""
    APP_NAME: str = "YOYOLE 瑜伽户外生活"
    DEBUG: bool = True

    # 数据库连接（asyncpg 异步驱动）
    DATABASE_URL: str = "postgresql+asyncpg://huangyong@localhost:5432/ecommerce"

    # 邮箱验证码 / SMTP（用于注册邮箱验证）
    EMAIL_CODE_EXPIRE_MINUTES: int = 10
    EMAIL_CODE_LENGTH: int = 6
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 25
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "noreply@pymall.local"
    SMTP_FROM_NAME: str = "PyMall 电商"
    SMTP_USE_SSL: bool = False
    SMTP_USE_TLS: bool = False

    # 安全
    SECRET_KEY: str = "pythonshop-dev-secret-key-change-me"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7

    # 多语言
    DEFAULT_LANGUAGE: str = "zh"
    SUPPORTED_LANGUAGES: list[str] = ["zh", "en"]

    # 支付回调地址
    BASE_URL: str = "http://127.0.0.1:8010"

    # 各支付通道是否已开通（未开通的通道不允许下单）
    PAYMENT_GATEWAY_ENABLED: dict[str, bool] = {
        "mock": True,
        "alipay": False,
        "wechat": False,
        "stripe": False,
        "paypal": False,
    }

    # ---------- PayPal（跨境收单）----------
    # 商户凭据一律只放 .env（已 gitignore），不要写进代码 / 静态资源 / 日志。
    # 前端只允许使用 PAYPAL_CLIENT_ID（JS SDK 必须），CLIENT_SECRET 永不下发。
    # PAYPAL_MODE 决定 API 域名：sandbox → api-m.sandbox.paypal.com，live → api-m.paypal.com
    PAYPAL_MODE: str = "sandbox"
    PAYPAL_CLIENT_ID: str = ""
    PAYPAL_CLIENT_SECRET: str = ""
    # 在开发者后台为 webhook 监听 URL 订阅事件时生成，验签必需（缺失则拒绝回调）
    PAYPAL_WEBHOOK_ID: str = ""
    # 收单币种：PayPal 的 CNY 仅限境内账户余额，跨境收单必须用 USD 等外币
    PAYPAL_CURRENCY: str = "USD"
    # 站点商品以 CNY 定价，走 PayPal 时按此汇率换算成 USD（下单时快照到支付记录，
    # 避免回调时汇率漂移导致金额对不上）。汇率变动频繁，建议定期改这里的值。
    PAYPAL_FX_CNY_PER_USD: float = 7.2

    # ---------- 微信支付（APIv3，国内收款：PC 扫码 Native + 手机 H5）----------
    # 商户凭据只放 .env / 文件，代码与前端不得出现任何密钥。
    # 需要准备的东西（详见 docs/wechatpay.md 第 2 节）：
    #   1. 商户号 mchid（商户平台 → 账户中心 → 商户信息）
    #   2. 已与 mchid 绑定的 appid（公众号/开放平台/小程序任意一个均可，Native 下单也必填）
    #   3. APIv3 密钥（32 位，商户平台 → 账户中心 → API 安全 → 设置 APIv3 密钥）
    #   4. 商户 API 证书（apiclient_key.pem）+ 证书序列号（申请时会显示）
    #   5. 微信支付公钥或平台证书（验签/解密用；推荐公钥，一次申请长期有效）
    WECHATPAY_MCHID: str = ""
    WECHATPAY_APPID: str = ""
    WECHATPAY_API_V3_KEY: str = ""
    WECHATPAY_CERT_SERIAL_NO: str = ""
    # 商户 API 证书私钥：优先用文件路径（更安全，不会出现在环境变量/进程列表里）
    WECHATPAY_PRIVATE_KEY_PATH: str = "cert/apiclient_key.pem"
    # 兼容方式：直接把 PEM 内容写进环境变量（适合不方便挂文件的部署，注意用单行 \n 转义）
    WECHATPAY_PRIVATE_KEY_PEM: str = ""
    # 微信支付公钥（推荐）或平台证书，用于验证应答/回调签名；同样支持文件或内容
    WECHATPAY_PUBLIC_KEY_PATH: str = "cert/wxpay_public_key.pem"
    WECHATPAY_PUBLIC_KEY_PEM: str = ""
    # 公钥 id（形如 PUB_KEY_ID_0000000000000024101100397200000006）
    WECHATPAY_PUBLIC_KEY_ID: str = ""
    # 多张平台证书目录（用平台证书而非公钥时填写，文件名即证书序列号）
    WECHATPAY_PLATFORM_CERT_DIR: str = "cert/platform"
    # API 域名：主域名就近接入 / 备域名异地接入（跨城冗灾用）；本地无外网时便于指向 mock
    WECHATPAY_API_BASE: str = "https://api.mch.weixin.qq.com"
    # 单笔订单允许支付的最长时长（分钟），传给 time_expire；微信 code_url 本身 2 小时有效
    WECHATPAY_PAY_EXPIRE_MINUTES: int = 120

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    # ---------- PayPal 派生配置（不单独占环境变量，由上面几项推导） ----------
    @property
    def paypal_live(self) -> bool:
        """是否是生产（live）环境：字符串比对，避免 .env 写成 "Live"/"LIVE" 时误判"""
        return (self.PAYPAL_MODE or "sandbox").strip().lower() == "live"

    @property
    def paypal_api_base(self) -> str:
        """REST API 域名（服务端调用）"""
        return "https://api-m.paypal.com" if self.paypal_live else "https://api-m.sandbox.paypal.com"

    @property
    def paypal_sdk_host(self) -> str:
        """JS SDK / 收银台域名（下发给前端）"""
        return "https://www.paypal.com" if self.paypal_live else "https://www.sandbox.paypal.com"

    @property
    def paypal_configured(self) -> bool:
        """凭据是否齐备（缺凭据时前端不应该看到 PayPal 选项）"""
        return bool(self.PAYPAL_CLIENT_ID and self.PAYPAL_CLIENT_SECRET)

    @property
    def paypal_enabled(self) -> bool:
        """对外可用 = 开关打开 且 凭据齐备"""
        return self.paypal_configured and bool(self.PAYMENT_GATEWAY_ENABLED.get("paypal"))

    # ---------- 微信支付派生配置 ----------
    @property
    def wechatpay_configured(self) -> bool:
        """凭据是否齐备：商户号 + appid + APIv3 密钥 + 证书序列号 + 私钥"""
        return bool(
            self.WECHATPAY_MCHID
            and self.WECHATPAY_APPID
            and self.WECHATPAY_API_V3_KEY
            and self.WECHATPAY_CERT_SERIAL_NO
            and (self.WECHATPAY_PRIVATE_KEY_PEM or self.wechatpay_private_key_path_exists)
        )

    @property
    def wechatpay_private_key_path_exists(self) -> bool:
        from pathlib import Path

        path = Path(self.WECHATPAY_PRIVATE_KEY_PATH)
        if not path.is_absolute():
            path = Path(__file__).resolve().parent.parent / path
        return path.is_file()

    @property
    def wechatpay_enabled(self) -> bool:
        """对外可用 = 开关打开 且 凭据齐备"""
        return self.wechatpay_configured and bool(self.PAYMENT_GATEWAY_ENABLED.get("wechat"))

    @property
    def wechatpay_private_key(self) -> bytes:
        """商户 API 证书私钥（优先文件，其次环境变量内容）"""
        from pathlib import Path

        if self.WECHATPAY_PRIVATE_KEY_PEM:
            # 环境变量里的 PEM 常写成单行 \n 转义，这里统一还原
            return self.WECHATPAY_PRIVATE_KEY_PEM.replace("\\n", "\n").encode()
        path = Path(self.WECHATPAY_PRIVATE_KEY_PATH)
        if not path.is_absolute():
            path = Path(__file__).resolve().parent.parent / path
        return path.read_bytes() if path.is_file() else b""

    def wechatpay_public_key_for(self, serial: str = "") -> bytes:
        """取验签用的公钥/证书

        优先微信支付公钥（一次申请长期有效）；用平台证书的部署下按序列号去
        `WECHATPAY_PLATFORM_CERT_DIR` 找同名文件（平台证书 5 年一换、且可同时存在多张）。
        """
        from pathlib import Path

        if serial.startswith("PUB_KEY_ID") and self.WECHATPAY_PUBLIC_KEY_PEM:
            return self.WECHATPAY_PUBLIC_KEY_PEM.replace("\\n", "\n").encode()
        if self.WECHATPAY_PUBLIC_KEY_PEM:
            return self.WECHATPAY_PUBLIC_KEY_PEM.replace("\\n", "\n").encode()

        root = Path(__file__).resolve().parent.parent
        if serial and not serial.startswith("PUB_KEY_ID"):
            cert = Path(self.WECHATPAY_PLATFORM_CERT_DIR)
            if not cert.is_absolute():
                cert = root / cert
            candidate = cert / f"{serial}.pem"
            if candidate.is_file():
                return candidate.read_bytes()

        path = Path(self.WECHATPAY_PUBLIC_KEY_PATH)
        if not path.is_absolute():
            path = root / path
        return path.read_bytes() if path.is_file() else b""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()