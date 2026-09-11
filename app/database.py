"""异步数据库引擎与会话管理"""
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger(__name__)

# 异步引擎（asyncpg 驱动）
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    future=True,
)

# 异步会话工厂
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类"""


async def get_db() -> AsyncSession:
    """FastAPI 依赖：提供请求级异步会话"""
    async with async_session_factory() as session:
        yield session


async def init_db() -> None:
    """建表（开发环境使用，生产建议用 Alembic 迁移）"""
    from app import models  # noqa: F401  确保模型被注册

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# 轻量迁移：为已有表补充缺失字段
# 说明：Base.metadata.create_all 只会在表不存在时建表，模型新增字段后不会
# 自动给已有表加列。为方便开发迭代，这里维护一个「缺列 → ALTER TABLE」清单。
_MIGRATIONS: list[tuple[str, str, str]] = [
    # (表名, 列名, 列定义)
    ("customers", "last_login", "TIMESTAMP NULL"),
    ("user_stories", "category", "VARCHAR(30) DEFAULT 'life'"),
    ("user_stories", "reject_reason", "VARCHAR(500) NULL"),
    ("user_stories", "tags", "JSON DEFAULT '[]'::json"),
]


async def run_light_migrations() -> None:
    """执行轻量迁移（幂等：检测列是否存在，缺失才 ALTER）"""
    async with engine.begin() as conn:
        for table, column, column_def in _MIGRATIONS:
            exists = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = :t AND column_name = :c"
                    ),
                    {"t": table, "c": column},
                )
            ).scalar()
            if not exists:
                await conn.execute(
                    text(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{column}" {column_def}')
                )
                logger.info("[migrate] 已为 %s.%s 补充列 %s", table, column, column_def)