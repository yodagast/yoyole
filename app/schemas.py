"""Pydantic 请求/响应模型"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ---------- 通用 ----------
class Message(BaseModel):
    message: str


# ---------- 订阅 ----------
class SubscribeIn(BaseModel):
    email: EmailStr


class SubscriberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    is_active: bool
    created_at: datetime


class NewsletterSendIn(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)


# ---------- 分类 ----------
class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    parent_id: int | None
    code: str
    name_i18n: dict[str, str]
    sort_order: int
    is_active: bool


# ---------- 商品 ----------
class SKUOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku_code: str
    attributes: dict[str, Any]
    price: Decimal
    stock: int
    available_stock: int
    is_active: bool


class SKUIn(BaseModel):
    sku_code: str
    attributes: dict[str, Any] = {}
    price: Decimal = Field(gt=0)
    cost_price: Decimal | None = None
    stock: int = 0
    is_active: bool = True


class ProductListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku_code: str
    name_i18n: dict[str, str]
    main_image: str | None
    base_price: Decimal
    is_featured: bool
    sales_count: int
    favorite_count: int = 0
    display_name: str = ""


class ProductDetailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku_code: str
    name_i18n: dict[str, str]
    description_i18n: dict[str, str]
    main_image: str | None
    images: list[str]
    base_price: Decimal
    category_id: int | None
    display_name: str = ""
    skus: list[SKUOut]


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku_code: str
    name_i18n: dict[str, str]
    description_i18n: dict[str, str]
    main_image: str | None
    images: list[str]
    base_price: Decimal
    brand: str | None
    weight_kg: Decimal | None
    status: str
    is_featured: bool
    sales_count: int
    category_id: int | None
    skus: list[SKUOut]


# ---------- 客户 / 认证 ----------
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    full_name: str = ""
    phone: str | None = None
    language: str | None = None
    code: str = Field(default="", max_length=10)  # 邮箱验证码


class SendVerifyCodeIn(BaseModel):
    email: EmailStr
    purpose: str = "register"  # register / reset


class ResetPasswordIn(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=10)
    new_password: str = Field(min_length=6)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class CustomerUpdateIn(BaseModel):
    full_name: str | None = None
    phone: str | None = None


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    phone: str | None
    language: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    customer: CustomerOut


# ---------- 购物车 ----------
class CartAddIn(BaseModel):
    sku_id: int
    quantity: int = Field(ge=1, le=999)


class CartUpdateIn(BaseModel):
    quantity: int = Field(ge=1, le=999)


class CartItemOut(BaseModel):
    id: int
    sku_id: int
    quantity: int
    sku_code: str
    product_name: str
    sku_spec: dict[str, Any]
    image: str | None
    unit_price: Decimal
    subtotal: Decimal
    available_stock: int


class CartOut(BaseModel):
    items: list[CartItemOut]
    total_amount: Decimal


# ---------- 评价 ----------
class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    title: str = ""
    content: str = ""


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    customer_id: int | None
    customer_name: str = ""
    rating: int
    title: str
    content: str
    status: str
    created_at: datetime


class ReviewModerationIn(BaseModel):
    status: str  # approved / rejected


# ---------- 收藏 ----------
class WishlistAddIn(BaseModel):
    product_id: int


class WishlistItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    product_name: str = ""
    main_image: str | None
    base_price: Decimal
    created_at: datetime


class WishlistOut(BaseModel):
    items: list[WishlistItemOut]
    total: int
    product_ids: list[int]


# ---------- 地址 ----------
class AddressIn(BaseModel):
    receiver_name: str
    receiver_phone: str
    detail: str
    is_default: bool = False


class AddressUpdateIn(BaseModel):
    receiver_name: str | None = None
    receiver_phone: str | None = None
    detail: str | None = None
    is_default: bool | None = None


class AddressOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    receiver_name: str
    receiver_phone: str
    detail: str
    is_default: bool


# ---------- 订单 ----------
class CheckoutIn(BaseModel):
    receiver_name: str
    receiver_phone: str
    receiver_address: str
    remark: str | None = None
    payment_method: str = "mock"


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_name: str
    sku_spec: dict[str, Any]
    sku_code: str
    image: str | None
    unit_price: Decimal
    quantity: int
    subtotal: Decimal


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_no: str
    status: str
    currency: str
    subtotal: Decimal
    shipping_fee: Decimal
    discount: Decimal
    total_amount: Decimal
    receiver_name: str
    receiver_phone: str
    receiver_address: str
    remark: str | None
    created_at: datetime
    paid_at: datetime | None
    shipped_at: datetime | None
    items: list[OrderItemOut]
    payments: list[PaymentOut] = []


class OrderCreateOut(BaseModel):
    order_no: str
    total_amount: Decimal
    status: str
    payment: dict[str, Any] | None = None


# ---------- 支付 ----------
class PayIn(BaseModel):
    method: str = "mock"


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transaction_no: str
    method: str
    amount: Decimal
    currency: str
    status: str
    gateway_response: dict[str, Any]


# ---------- 后台 ERP ----------
class AdminLoginIn(BaseModel):
    username: str
    password: str


class AdminTokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str
    full_name: str = ""


class AdminLoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    full_name: str
    role: str


# 管理后台：管理员账号管理
class AdminUserIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6)
    full_name: str = ""
    role: str = "operator"  # superadmin / operator / viewer


class AdminUserUpdateIn(BaseModel):
    full_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=6)


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    full_name: str
    role: str
    is_active: bool
    last_login: datetime | None
    created_at: datetime


# 管理后台：客户管理
class AdminCustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    phone: str | None
    language: str
    is_active: bool
    last_login: datetime | None
    created_at: datetime
    orders_count: int = 0
    total_spent: Decimal = Decimal("0")


class ProductCreateIn(BaseModel):
    category_id: int | None = None
    sku_code: str
    brand: str | None = None
    weight_kg: Decimal | None = None
    name_zh: str
    name_en: str = ""
    description_zh: str = ""
    description_en: str = ""
    main_image: str | None = None
    images: list[str] = []
    base_price: Decimal = Field(gt=0)
    status: str = "active"
    is_featured: bool = False
    skus: list[dict[str, Any]] = []  # [{sku_code, attributes, price, stock}]


class ProductUpdateIn(BaseModel):
    category_id: int | None = None
    name_zh: str | None = None
    name_en: str | None = None
    brand: str | None = None
    weight_kg: Decimal | None = None
    description_zh: str | None = None
    description_en: str | None = None
    main_image: str | None = None
    images: list[str] | None = None
    base_price: Decimal | None = None
    status: str | None = None
    is_featured: bool | None = None


class CategoryCreateIn(BaseModel):
    parent_id: int | None = None
    code: str
    name_zh: str
    name_en: str = ""
    sort_order: int = 0
    is_active: bool = True


class StockAdjustIn(BaseModel):
    sku_id: int
    change_qty: int
    reason: str = "manual"
    reference: str | None = None


class OrderStatusIn(BaseModel):
    status: str


class DashboardOut(BaseModel):
    products_count: int
    orders_count: int
    customers_count: int
    revenue: Decimal
    pending_orders: int
    low_stock: int
