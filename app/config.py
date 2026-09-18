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


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()