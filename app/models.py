"""数据模型：商品、分类、SKU、库存、购物车、订单、支付、管理员"""
from __future__ import annotations

import enum
import json
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# ---------------------------------------------------------------- 商品状态派生
PRODUCT_STATUS_LABELS: dict[str, str] = {
    "on_sale": "在售中",
    "off_shelf": "已下架",
    "sold_out": "已售罄",
    "pending": "发布中",
    "rejected": "已驳回",
    "draft": "草稿箱",
    "deleted": "已删除",
}


def derive_product_status(
    *, deleted_at, review_status: str, status: str, total_stock: int
) -> str:
    """由 (是否删除, 审核状态, 上下架状态, 库存) 派生单一「商品状态」。

    后台以该状态做单维度标签页管理（全部/在售中/已下架/已售罄/发布中/已驳回/草稿箱/已删除）。
    判定优先级：回收站 > 审核结果 > 上下架 > 库存。
    """
    if deleted_at is not None:
        return "deleted"
    if review_status == "rejected":
        return "rejected"
    if review_status == "pending":
        return "pending"
    if status == "draft":
        return "draft"
    if status == "off_shelf":
        return "off_shelf"
    return "sold_out" if (total_stock or 0) <= 0 else "on_sale"


# ------------------------------------------------- 审计日志：动作标签 / 可读摘要
AUDIT_ACTION_LABELS: dict[str, str] = {
    "create": "新增商品",
    "update": "编辑商品",
    "delete": "移入回收站",
    "restore": "从回收站恢复",
    "purge": "彻底删除",
    "publish": "上架",
    "unpublish": "下架",
    "quick_price": "修改价格",
    "quick_stock": "修改库存",
    "sku_price": "修改规格价格",
    "sku_stock": "修改规格库存",
    "review": "人工审核",
    "bulk_status": "批量改状态",
    "bulk_publish": "批量上架",
    "bulk_unpublish": "批量下架",
    "bulk_review": "批量审核",
    "bulk_category": "批量改分类",
    "bulk_featured": "批量改推荐",
    "bulk_delete": "批量移入回收站",
    "bulk_restore": "批量恢复",
    "bulk_purge": "批量彻底删除",
    "import_products": "产品册导入",
}

# 徽标色调：green 正向 / orange 待定 / red 破坏性 / gray 中性 / blue 新增编辑
AUDIT_ACTION_TONES: dict[str, str] = {
    "create": "blue",
    "update": "blue",
    "import_products": "blue",
    "publish": "green",
    "bulk_publish": "green",
    "restore": "green",
    "bulk_restore": "green",
    "review": "orange",
    "bulk_review": "orange",
    "unpublish": "gray",
    "bulk_unpublish": "gray",
    "bulk_status": "gray",
    "bulk_category": "gray",
    "bulk_featured": "gray",
    "quick_price": "gray",
    "quick_stock": "gray",
    "sku_price": "gray",
    "sku_stock": "gray",
    "delete": "red",
    "bulk_delete": "red",
    "purge": "red",
    "bulk_purge": "red",
}

# 审计日志 detail 中出现的枚举值 → 中文
AUDIT_VALUE_LABELS: dict[str, str] = {
    **PRODUCT_STATUS_LABELS,
    "active": "在售中",
    "approved": "已通过",
    "manual": "人工录入",
    "import": "产品册导入",
}

# 审核状态专用标签（与商品状态词区分，避免「发布中」当审核结果读起来歧义）
REVIEW_STATUS_LABELS: dict[str, str] = {
    "pending": "待审核",
    "approved": "已通过",
    "rejected": "已驳回",
}


def audit_action_label(action: str) -> str:
    """动作英文 → 中文标签（未知动作原样返回）"""
    return AUDIT_ACTION_LABELS.get(action, action or "-")


def audit_action_tone(action: str) -> str:
    """动作 → 徽标色调（未知动作归为 gray）"""
    return AUDIT_ACTION_TONES.get(action, "gray")


def _audit_value(value, labels: dict[str, str] | None = None) -> str:
    """把 detail 中的值渲染成中文可读文本"""
    if isinstance(value, bool):
        return "是" if value else "否"
    if value is None or value == "":
        return "无"
    if isinstance(value, dict):
        # 多语言对象取中文；其余压成短 JSON
        if "zh" in value:
            return str(value["zh"])
        text = json.dumps(value, ensure_ascii=False)
        return text if len(text) <= 40 else text[:37] + "…"
    if isinstance(value, (list, tuple)):
        text = "、".join(str(v) for v in value)
        return text if len(text) <= 40 else text[:37] + "…"
    table = labels if labels is not None else AUDIT_VALUE_LABELS
    return table.get(str(value), str(value))


def _money(value) -> str:
    try:
        return f"¥{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def describe_audit_detail(action: str, detail: dict | None) -> str:
    """把审计日志的 detail JSON 转成一句人话摘要（供后台展示）。

    无法识别的动作/结构回退为 `key=value` 拼接，绝不抛异常。
    """
    d = detail or {}
    if not isinstance(d, dict):
        return str(d)

    # 审核相关动作的 from/to 是审核状态，用审核标签而非商品状态标签
    is_review = action in ("review", "bulk_review")
    labels = REVIEW_STATUS_LABELS if is_review else AUDIT_VALUE_LABELS

    def val(key):
        return _audit_value(d.get(key), labels)

    def pair() -> str:
        return f"{val('from')} → {val('to')}"

    try:
        # ---- 新增 / 编辑 ----
        if action == "create":
            parts = []
            if d.get("sku_code"):
                parts.append(f"货号 {d['sku_code']}")
            if d.get("base_price") is not None:
                parts.append(f"基础价 {_money(d['base_price'])}")
            if d.get("sku_count") is not None:
                parts.append(f"{d['sku_count']} 个规格")
            return " · ".join(parts) or "新建商品"

        if action == "update":
            fields = d.get("fields") or {}
            if not isinstance(fields, dict) or not fields:
                return "无字段变更"
            keys = list(fields.keys())
            shown = keys[:3]
            text = "、".join(f"{k}={_audit_value(fields[k])}" for k in shown)
            if len(keys) > len(shown):
                text += f" 等 {len(keys)} 项"
            return f"修改 {text}"

        # ---- 回收站 ----
        if action in ("delete", "bulk_delete"):
            base = "移入回收站（可恢复）"
            if d.get("status"):
                base += f" · 原状态 {val('status')}"
            return base

        if action in ("restore", "bulk_restore"):
            return f"恢复为 {val('to_status')}，需重新上架"

        if action in ("purge", "bulk_purge"):
            base = "彻底删除，不可恢复"
            if d.get("status"):
                base += f" · 原状态 {val('status')}"
            return base

        # ---- 上下架 ----
        if action in ("publish", "bulk_publish"):
            return pair()

        if action in ("unpublish", "bulk_unpublish"):
            text = pair()
            if d.get("reason"):
                text += f" · 原因：{d['reason']}"
            return text

        if action == "bulk_status":
            return f"批量设为 {val('to')}"

        # ---- 行内快捷编辑 ----
        if action == "quick_price":
            text = f"{_money(d.get('from'))} → {_money(d.get('to'))}"
            if d.get("synced_skus"):
                text += f" · 同步 {d['synced_skus']} 个规格"
            if d.get("reason"):
                text += f" · 原因：{d['reason']}"
            return text

        if action == "quick_stock":
            value = d.get("value")
            text = (
                f"总库存 +{value}"
                if d.get("mode") == "add"
                else f"总库存 设为 {value}"
            )
            if d.get("from_total") is not None and d.get("to_total") is not None:
                text += f"（{d['from_total']} → {d['to_total']}）"
            if d.get("skus"):
                text += f" · {d['skus']} 个规格"
            if d.get("reason") and d["reason"] != "quick_edit":
                text += f" · 原因：{d['reason']}"
            return text

        # ---- 规格级批量改价 / 改库存（后台弹窗）----
        if action == "sku_price":
            changed = d.get("changed") or 0
            text = f"修改 {changed} 个规格的价格"
            spans = d.get("spans") or []
            if spans:
                text += " · " + "、".join(
                    f"{_money(s.get('from'))} → {_money(s.get('to'))}" for s in spans[:3]
                )
                if len(spans) > 3:
                    text += f" 等 {len(spans)} 种"
            if d.get("reason"):
                text += f" · 原因：{d['reason']}"
            return text

        if action == "sku_stock":
            changed = d.get("changed") or 0
            mode = d.get("mode")
            value = d.get("value")
            if mode == "set" and value is not None:
                text = f"{changed} 个规格库存 设为 {value}"
            elif mode == "set":
                text = f"{changed} 个规格库存 设为不同值"
            elif value is not None:
                text = f"{changed} 个规格库存 {'+' if value >= 0 else ''}{value}"
            else:
                delta = (d.get("to_total") or 0) - (d.get("from_total") or 0)
                text = (
                    f"调整 {changed} 个规格库存（合计 "
                    f"{'+' if delta >= 0 else ''}{delta}）"
                )
            if d.get("from_total") is not None and d.get("to_total") is not None:
                text += f"（总库存 {d['from_total']} → {d['to_total']}）"
            if d.get("reason") and d["reason"] != "quick_edit":
                text += f" · 原因：{d['reason']}"
            return text

        # ---- 审核 ----
        if is_review:
            text = pair()
            if d.get("note"):
                text += f" · 理由：{d['note']}"
            return text

        # ---- 分类 / 推荐 ----
        if action == "bulk_category":
            return f"分类 {val('from')} → {val('to')}"

        if action == "bulk_featured":
            return "设为推荐" if d.get("to") else "取消推荐"

        # ---- 批量导入 ----
        if action == "import_products":
            parts = []
            if d.get("imported") is not None:
                parts.append(f"导入 {d['imported']} 个商品")
            if d.get("total_slides"):
                parts.append(f"共 {d['total_slides']} 页")
            if d.get("mode"):
                parts.append(f"模式 {d['mode']}")
            if d.get("review_status"):
                parts.append(
                    "审核 " + _audit_value(d["review_status"], REVIEW_STATUS_LABELS)
                )
            if d.get("skipped"):
                parts.append(f"跳过 {d['skipped']}")
            return " · ".join(parts) or "批量导入完成"
    except Exception:  # noqa: BLE001 —— 展示层永不因脏数据报错
        pass

    # 兜底：key=value 拼接
    if not d:
        return "-"
    return " · ".join(f"{k}={_audit_value(v)}" for k, v in list(d.items())[:4])



class OrderStatus(str, enum.Enum):
    PENDING = "pending"        # 待支付
    PAID = "paid"              # 已支付
    SHIPPED = "shipped"        # 已发货
    COMPLETED = "completed"    # 已完成
    CANCELLED = "cancelled"    # 已取消
    REFUNDED = "refunded"      # 已退款


# 订单状态中文标签（后台列表/标签页/徽标共用）
ORDER_STATUS_LABELS: dict[str, str] = {
    "pending": "待付款",
    "paid": "待发货",
    "shipped": "待收货",
    "completed": "已完成",
    "cancelled": "已取消",
    "refunded": "退款/售后",
}

# 后台订单标签页（key → 标签，`all` 表示全部）
ORDER_TABS: list[tuple[str, str]] = [
    ("all", "全部订单"),
    ("pending", "待付款"),
    ("paid", "待发货"),
    ("shipped", "待收货"),
    ("completed", "已完成"),
    ("refunded", "退款/售后"),
    ("cancelled", "已取消"),
]


def order_status_label(status) -> str:
    """订单状态 → 中文标签（未知状态原样返回）"""
    key = status.value if hasattr(status, "value") else str(status or "")
    return ORDER_STATUS_LABELS.get(key, key or "-")


def utc_to_local_naive(dt: datetime | None) -> datetime | None:
    """把「Python 写入的 UTC 无时区时间」转成本地（服务器时区）无时区时间。

    背景：模型里的 `created_at` 由数据库 `now()` 生成 → 落库是**本地时间**；
    而部分字段（订单 `paid_at`/`shipped_at`/`completed_at`/`cancelled_at` 等）由
    Python 用 `datetime.now(timezone.utc)` 写入 → 落库是 **UTC**。
    两者直接展示会差一个时区（如「支付时间」早于「下单时间」），
    因此统一在序列化时把后者转成本地时间，保证同一订单内时间轴顺序正确。
    """
    if dt is None:
        return None
    # 已带时区信息：直接用 astimezone 转本地再抹掉时区
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    # 无时区：按 UTC 解释，转成本地
    return dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


class PaymentStatus(str, enum.Enum):
    UNPAID = "unpaid"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    REFUNDED = "refunded"


class PaymentMethod(str, enum.Enum):
    ALIPAY = "alipay"
    WECHAT = "wechat"
    STRIPE = "stripe"
    PAYPAL = "paypal"
    MOCK = "mock"


class AdminRole(str, enum.Enum):
    SUPERADMIN = "superadmin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class Category(Base):
    """商品分类"""
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    # 多语言名称，JSON 结构：{"zh": "电子产品", "en": "Electronics"}
    name_i18n: Mapped[dict] = mapped_column(JSON, default=dict)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    children = relationship("Category", backref="parent", remote_side=[id])
    products = relationship("Product", back_populates="category")

    def name(self, lang: str) -> str:
        return self.name_i18n.get(lang) or self.name_i18n.get("zh") or self.code


class Product(Base):
    """商品（SPU）"""
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sku_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # 多语言字段 {zh, en}
    name_i18n: Mapped[dict] = mapped_column(JSON, default=dict)
    description_i18n: Mapped[dict] = mapped_column(JSON, default=dict)
    # 主图与轮播图
    main_image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    images: Mapped[list] = mapped_column(JSON, default=list)
    # 基础价格（以分为单位存整数，避免浮点误差；或用 Numeric）
    base_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(100), nullable=True)
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active/draft/off_shelf
    # 审核状态：pending=待审核 / approved=已通过 / rejected=已驳回
    # 只有 approved 的商品才在前台展示（批量导入默认 pending，需人工审核）
    review_status: Mapped[str] = mapped_column(
        String(20), default="approved", index=True
    )
    review_note: Mapped[str | None] = mapped_column(String(500), nullable=True)  # 审核备注/驳回理由
    reviewed_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 来源：manual=后台人工录入 / import=产品册批量导入
    source: Mapped[str] = mapped_column(String(20), default="manual")
    # 软删除：非空则商品已进回收站（可在后台恢复）。物理删除请显式走 purge 接口
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    # 最后一次上下架操作留痕
    off_shelf_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_status_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)
    sales_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    category = relationship("Category", back_populates="products")
    skus = relationship("SKU", back_populates="product", cascade="all, delete-orphan")
    reviews = relationship("Review", back_populates="product")

    def name(self, lang: str) -> str:
        return self.name_i18n.get(lang) or self.name_i18n.get("zh") or self.sku_code

    def description(self, lang: str) -> str:
        return self.description_i18n.get(lang) or self.description_i18n.get("zh", "")

    @property
    def total_stock(self) -> int:
        """所有启用 SKU 的库存合计"""
        return sum(s.stock or 0 for s in (self.skus or []) if s.is_active)

    def compute_display_status(self, total_stock: int | None = None) -> str:
        """派生「商品状态」——后台按此做单维度分状态管理（见 derive_product_status）"""
        return derive_product_status(
            deleted_at=self.deleted_at,
            review_status=self.review_status,
            status=self.status,
            total_stock=self.total_stock if total_stock is None else total_stock,
        )

    @property
    def total_stock(self) -> int:
        """所有启用 SKU 的库存合计"""
        return sum(s.stock or 0 for s in (self.skus or []) if s.is_active)

    def compute_display_status(self, total_stock: int | None = None) -> str:
        """派生「商品状态」——后台按此做单维度分状态管理。

        | 值 | 标签 | 含义 |
        |---|---|---|
        | `deleted`   | 已删除 | 在回收站，可从回收站恢复 |
        | `rejected`  | 已驳回 | 审核未通过，需修改后重新提交 |
        | `pending`   | 发布中 | 已提交，等待人工审核 |
        | `draft`     | 草稿箱 | 未提交上架 |
        | `off_shelf` | 已下架 | 主动下架 |
        | `sold_out`  | 已售罄 | 已上架但库存为 0 |
        | `on_sale`   | 在售中 | 正常在售 |
        """
        return derive_product_status(
            deleted_at=self.deleted_at,
            review_status=self.review_status,
            status=self.status,
            total_stock=self.total_stock if total_stock is None else total_stock,
        )


class ProductAuditLog(Base):
    """商品操作审计日志（人工管理 / 审核留痕）

    商品被删除后仍保留记录（product_id 置空，靠 product_sku/product_name 追溯）。
    """

    __tablename__ = "product_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True
    )
    product_sku: Mapped[str] = mapped_column(String(64), default="")
    product_name: Mapped[str] = mapped_column(String(200), default="")
    # create / update / delete / review / bulk_status / bulk_delete / bulk_category ...
    action: Mapped[str] = mapped_column(String(30), index=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    operator: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )


class SKU(Base):
    """商品规格（SKU），一个商品可有多个规格"""
    __tablename__ = "skus"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    sku_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # 规格描述，如 {"颜色": "黑色", "尺寸": "L"}，多语言值可存字典
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)  # 成本价
    stock: Mapped[int] = mapped_column(Integer, default=0)  # 实时库存
    locked_stock: Mapped[int] = mapped_column(Integer, default=0)  # 锁定库存（下单未支付）
    low_stock_threshold: Mapped[int] = mapped_column(Integer, default=5)  # 低库存阈值
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    product = relationship("Product", back_populates="skus")

    @property
    def available_stock(self) -> int:
        return max(self.stock - self.locked_stock, 0)


class Customer(Base):
    """客户（注册用户）"""
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(100), default="")
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    language: Mapped[str] = mapped_column(String(10), default="zh")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    orders = relationship("Order", back_populates="customer")
    reviews = relationship("Review", back_populates="customer")
    wishlist = relationship("WishlistItem", back_populates="customer")
    addresses = relationship("Address", back_populates="customer")


class EmailVerifyCode(Base):
    """邮箱注册验证码"""
    __tablename__ = "email_verify_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    code: Mapped[str] = mapped_column(String(10), nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), default="register")  # register / reset
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CartItem(Base):
    """购物车条目（按 SKU 维度）"""
    __tablename__ = "cart_items"
    __table_args__ = (UniqueConstraint("customer_id", "sku_id", name="uq_cart_customer_sku"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    sku_id: Mapped[int] = mapped_column(
        ForeignKey("skus.id", ondelete="CASCADE"), index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    sku = relationship("SKU")


class Order(Base):
    """订单"""
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status"), default=OrderStatus.PENDING, index=True
    )
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    shipping_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    discount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)

    # 收货信息
    receiver_name: Mapped[str] = mapped_column(String(100), default="")
    receiver_phone: Mapped[str] = mapped_column(String(30), default="")
    receiver_address: Mapped[str] = mapped_column(Text, default="")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 后台运营字段
    admin_note: Mapped[str | None] = mapped_column(String(500), nullable=True)  # 商家备注
    carrier: Mapped[str | None] = mapped_column(String(50), nullable=True)      # 快递公司
    tracking_no: Mapped[str | None] = mapped_column(String(60), nullable=True)  # 快递单号
    cancel_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    customer = relationship("Customer", back_populates="orders")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="order")


class OrderItem(Base):
    """订单明细（下单时快照商品信息）"""
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    sku_id: Mapped[int | None] = mapped_column(
        ForeignKey("skus.id", ondelete="SET NULL"), nullable=True
    )
    product_name: Mapped[str] = mapped_column(String(255))  # 快照名称
    sku_spec: Mapped[dict] = mapped_column(JSON, default=dict)  # 快照规格
    sku_code: Mapped[str] = mapped_column(String(64))
    image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    order = relationship("Order", back_populates="items")
    sku = relationship("SKU")  # 指向下单时的 sku（可能已被删除，此时为 None）


class Payment(Base):
    """支付记录"""
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod, name="payment_method"), default=PaymentMethod.MOCK
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status"), default=PaymentStatus.UNPAID, index=True
    )
    gateway_response: Mapped[dict] = mapped_column(JSON, default=dict)
    # 第三方网关侧的订单号（PayPal order id，形如 5O190127TN364715T）。
    # 注意与 transaction_no 的分工：transaction_no 是我们自己的流水号（PAY...），
    # provider_order_id 是网关的，capture / 退款都要用它。
    provider_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # 第三方网关侧的扣款号（PayPal capture id，退款走它）
    provider_capture_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 下单时的汇率快照（站点按 CNY 定价，PayPal 按 USD 收单）
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    order = relationship("Order", back_populates="payments")


class AdminUser(Base):
    """后台 ERP 管理员"""
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(100), default="")
    role: Mapped[AdminRole] = mapped_column(
        Enum(AdminRole, name="admin_role"), default=AdminRole.OPERATOR
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class StockMovement(Base):
    """库存流水（出入库记录，用于 ERP 对账）"""
    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku_id: Mapped[int] = mapped_column(
        ForeignKey("skus.id", ondelete="CASCADE"), index=True
    )
    change_qty: Mapped[int] = mapped_column(Integer, nullable=False)  # 正入负出
    balance_after: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(100), default="manual")
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)  # 关联单号
    operator: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    sku = relationship("SKU")


class ReviewStatus(str, enum.Enum):
    PENDING = "pending"    # 待审核
    APPROVED = "approved"  # 已通过
    REJECTED = "rejected"  # 已拒绝


class Review(Base):
    """商品评价（审核通过后前台展示）"""
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    rating: Mapped[int] = mapped_column(Integer, default=5)  # 1~5
    title: Mapped[str] = mapped_column(String(200), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, name="review_status"), default=ReviewStatus.PENDING, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    product = relationship("Product", back_populates="reviews")
    customer = relationship("Customer", back_populates="reviews")


class WishlistItem(Base):
    """收藏（愿望清单）"""
    __tablename__ = "wishlist_items"
    __table_args__ = (
        UniqueConstraint("customer_id", "product_id", name="uq_wishlist_customer_product"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    product = relationship("Product")
    customer = relationship("Customer", back_populates="wishlist")


class Address(Base):
    """用户收货地址"""
    __tablename__ = "addresses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    receiver_name: Mapped[str] = mapped_column(String(100), default="")
    receiver_phone: Mapped[str] = mapped_column(String(30), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    customer = relationship("Customer", back_populates="addresses")


class SiteBanner(Base):
    """站点内容（首页轮播 / 页面区块）：图片、视频、文字内容管理

    用于 ERP 后台「内容管理」维护前台首页及各页面展示内容：
    - 首页轮播：placement='home_hero'，含标题/副标题/跳转链接/图片或视频
    - 页面区块：placement 可扩展（如 'about_hero'、'home_feature' 等）
    """
    __tablename__ = "site_banners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    placement: Mapped[str] = mapped_column(String(50), default="home_hero", index=True)
    # 多语言标题/副标题/按钮文字：{"zh": "...", "en": "..."}；单语言内容放 zh
    title_i18n: Mapped[dict] = mapped_column(JSON, default=dict)
    subtitle_i18n: Mapped[dict] = mapped_column(JSON, default=dict)
    button_text_i18n: Mapped[dict] = mapped_column(JSON, default=dict)
    # 媒体：图片 URL 与视频 URL（mp4/webm）
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    video_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 点击跳转（商品详情 /products.html?id=1、分类 /products.html?category_id=2、外部链接）
    link_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def title(self, lang: str) -> str:
        return self.title_i18n.get(lang) or self.title_i18n.get("zh", "")

    def subtitle(self, lang: str) -> str:
        return self.subtitle_i18n.get(lang) or self.subtitle_i18n.get("zh", "")

    def button_text(self, lang: str) -> str:
        return self.button_text_i18n.get(lang) or self.button_text_i18n.get("zh", "")


class SiteContent(Base):
    """站点内容（页面文字/图片 KV）持久化存储

    对应首页/关于页等区块内容，key 形如 page:section.field（如 about:story.content），
    value 为 JSON（多语言用 {"zh": "...", "en": "..."} 或叶子值）。
    后台 ERP「内容管理」修改后写入数据库，重启不丢失。
    """
    __tablename__ = "site_contents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 形如 about:story.content，唯一键
    key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)  # {"zh": "...", "en": "..."}
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class UserStory(Base):
    """用户故事投稿，发布前由后台审核。"""
    __tablename__ = "user_stories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    category: Mapped[str] = mapped_column(String(30), default="life", index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reject_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class StoryLike(Base):
    """用户故事点赞（一个用户对一篇故事只能点一次）"""
    __tablename__ = "story_likes"
    __table_args__ = (UniqueConstraint("user_story_id", "customer_id", name="uq_story_like"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_story_id: Mapped[int] = mapped_column(
        ForeignKey("user_stories.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class StoryComment(Base):
    """用户故事评论"""
    __tablename__ = "story_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_story_id: Mapped[int] = mapped_column(
        ForeignKey("user_stories.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    content: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    customer = relationship("Customer")


class NewsletterSubscriber(Base):
    """新闻订阅者（首页/页脚「订阅优惠信息」）"""
    __tablename__ = "newsletter_subscribers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())