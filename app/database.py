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
    # 商品审核与来源（人工管理与审核功能）
    ("products", "review_status", "VARCHAR(20) DEFAULT 'approved'"),
    ("products", "review_note", "VARCHAR(500) NULL"),
    ("products", "reviewed_by", "VARCHAR(50) NULL"),
    ("products", "reviewed_at", "TIMESTAMP NULL"),
    ("products", "source", "VARCHAR(20) DEFAULT 'manual'"),
    # 软删除（回收站）与状态留痕
    ("products", "deleted_at", "TIMESTAMP NULL"),
    ("products", "off_shelf_reason", "VARCHAR(200) NULL"),
    ("products", "last_status_at", "TIMESTAMP NULL"),
    # 订单管理增强：商家备注 / 物流 / 完成与取消时间
    ("orders", "admin_note", "VARCHAR(500) NULL"),
    ("orders", "carrier", "VARCHAR(50) NULL"),
    ("orders", "tracking_no", "VARCHAR(60) NULL"),
    ("orders", "cancel_reason", "VARCHAR(200) NULL"),
    ("orders", "completed_at", "TIMESTAMP NULL"),
    ("orders", "cancelled_at", "TIMESTAMP NULL"),
    # PayPal 收单：网关侧订单号/扣款号 + 汇率快照
    ("payments", "provider_order_id", "VARCHAR(64) NULL"),
    ("payments", "provider_capture_id", "VARCHAR(64) NULL"),
    ("payments", "fx_rate", "NUMERIC(10, 4) NULL"),
    # 已有表补列后建索引（幂等建索引语句见下方 _INDEXES）
]

# 需要补充的 PostgreSQL ENUM 取值：(类型名, 取值)
# 说明：SQLAlchemy 的 Enum 存的是「枚举成员名」，Python 侧加了成员（如 Paypal）
# 并不会自动改数据库类型，必须 ALTER TYPE；否则写入直接报 invalid input value。
_ENUM_VALUES: list[tuple[str, str]] = [
    ("payment_method", "PAYPAL"),
]

# 幂等建索引（轻量迁移里无法用 ADD COLUMN 带索引）
_INDEXES: list[str] = [
    'CREATE INDEX IF NOT EXISTS ix_payments_provider_order_id ON payments (provider_order_id)',
]

# 补充列后需要回填默认值的语句（幂等）
_BACKFILLS: list[str] = [
    # 历史商品视为已审核通过，否则加了审核门槛后前台会全部消失
    "UPDATE products SET review_status = 'approved' WHERE review_status IS NULL",
    "UPDATE products SET source = 'manual' WHERE source IS NULL",
    # 历史 jyt 导入的图文详情图 URL 统一补 /d/ 前缀标记
    # （仅匹配 detail_ 详情图，主图 main.jpg 不动；重复执行幂等——已被替换的不再匹配）
    "UPDATE products SET images = ("
    "  regexp_replace(images::text, '\"/static/uploads/jyt/(s[0-9]+/detail_)', '\"/d/static/uploads/jyt/\\1', 'g')"
    ")::json"
    " WHERE images::text LIKE '%/detail_%' AND images::text NOT LIKE '%\"/d/static/uploads/jyt/%'",
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

        # 补充 ENUM 取值（PG 12+ 允许在事务里 ADD VALUE，但同一事务内不能使用该值）
        for enum_name, value in _ENUM_VALUES:
            exists = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                        "WHERE t.typname = :n AND e.enumlabel = :v"
                    ),
                    {"n": enum_name, "v": value},
                )
            ).scalar()
            if not exists:
                await conn.execute(
                    text(f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{value}'")
                )
                logger.info("[migrate] 已为 ENUM %s 补充取值 %s", enum_name, value)

        # 幂等建索引
        for sql in _INDEXES:
            try:
                await conn.execute(text(sql))
            except Exception:  # noqa: BLE001  表尚未创建时跳过
                logger.debug("[migrate] 建索引跳过：%s", sql)

        # 回填默认值（表不存在时忽略）
        for sql in _BACKFILLS:
            try:
                await conn.execute(text(sql))
            except Exception:  # noqa: BLE001  表尚未创建时跳过
                logger.debug("[migrate] 回填跳过：%s", sql)