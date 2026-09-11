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
    }

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()