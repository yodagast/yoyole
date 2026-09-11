"""数据模型：商品、分类、SKU、库存、购物车、订单、支付、管理员"""
from __future__ import annotations

import enum
from datetime import datetime
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


class OrderStatus(str, enum.Enum):
    PENDING = "pending"        # 待支付
    PAID = "paid"              # 已支付
    SHIPPED = "shipped"        # 已发货
    COMPLETED = "completed"    # 已完成
    CANCELLED = "cancelled"    # 已取消
    REFUNDED = "refunded"      # 已退款


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

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

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