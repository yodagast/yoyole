"""Pydantic 请求/响应模型"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)


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
    # 派生字段：关联商品数 / 未删除商品数 / 子类目数（路由填充，供后台列表展示）
    product_count: int = 0
    active_product_count: int = 0
    children_count: int = 0
    parent_name: str = ""
    created_at: datetime | None = None


class CategoryIn(BaseModel):
    """类目新增/编辑（完整字段，供独立编辑页提交）"""

    code: str = Field(min_length=1, max_length=50, description="类目编码，全局唯一")
    name_zh: str = Field(min_length=1, max_length=100, description="中文名称")
    name_en: str = Field(default="", max_length=100, description="英文名称")
    parent_id: int | None = Field(default=None, description="父类目 ID，留空为顶级")
    sort_order: int = Field(default=0, description="排序值，越小越靠前")
    is_active: bool = Field(default=True, description="是否启用")

    @field_validator("code")
    @classmethod
    def _norm_code(cls, v: str) -> str:
        """编码统一小写并只保留字母/数字/下划线/连字符"""
        code = (v or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9_\-]+", code):
            raise ValueError("类目编码只能包含小写字母、数字、下划线或连字符")
        return code

    @field_validator("name_zh")
    @classmethod
    def _strip_zh(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("中文名称不能为空")
        return v

    def to_name_i18n(self) -> dict[str, str]:
        return {"zh": self.name_zh, "en": (self.name_en or "").strip() or self.name_zh}


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
    # 审核与来源
    review_status: str = "approved"
    review_note: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    source: str = "manual"
    # 按语言解析后的展示名（后台预览用；列表接口通常不填，前台回退 sku_code）
    display_name: str = ""
    # 状态派生（后端计算，供后台展示单维度状态）
    total_stock: int = 0
    display_status: str = ""
    display_status_label: str = ""
    # 收藏数（按需在路由里用 WishlistItem 聚合填充；非 Product 表字段）
    favorite_count: int = 0
    deleted_at: datetime | None = None
    off_shelf_reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    skus: list[SKUOut]

    @model_validator(mode="after")
    def _derive_status(self):
        """按「回收站 > 审核 > 上下架 > 库存」派生单一商品状态与中文标签"""
        from app.models import PRODUCT_STATUS_LABELS, derive_product_status

        self.display_status = derive_product_status(
            deleted_at=self.deleted_at,
            review_status=self.review_status,
            status=self.status,
            total_stock=self.total_stock,
        )
        self.display_status_label = PRODUCT_STATUS_LABELS.get(
            self.display_status, self.display_status
        )
        return self


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


# ---------- 后台订单管理 ----------
class AdminOrderItemOut(OrderItemOut):
    """后台订单明细（额外带 SKU 主键，便于跳转商品/规格）"""

    sku_id: int | None = None


class AdminOrderOut(BaseModel):
    """后台订单（含买家与物流信息，供列表与详情共用）"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    order_no: str
    status: str
    status_label: str = ""
    currency: str
    subtotal: Decimal
    shipping_fee: Decimal
    discount: Decimal
    total_amount: Decimal
    # 商品总额（明细小计合计，用于「商品总价」列）
    goods_amount: Decimal = Decimal("0")
    item_count: int = 0            # 明细条数（行数）
    total_quantity: int = 0        # 商品件数（数量合计）
    receiver_name: str = ""
    receiver_phone: str = ""
    receiver_address: str = ""
    remark: str | None = None
    admin_note: str | None = None  # 商家备注（后台添加）
    tracking_no: str | None = None  # 快递单号
    carrier: str | None = None      # 快递公司
    cancel_reason: str | None = None  # 取消 / 退款原因
    # 买家
    customer_id: int | None = None
    customer_email: str = ""
    customer_name: str = ""
    created_at: datetime
    paid_at: datetime | None = None
    shipped_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    items: list[AdminOrderItemOut] = []
    payments: list[AdminPaymentOut] = []


class OrderStatusCountsOut(BaseModel):
    """订单各状态数量（后台标签页角标）"""

    all: int
    pending: int      # 待付款
    paid: int         # 待发货
    shipped: int      # 待收货
    completed: int    # 已完成
    cancelled: int    # 已取消
    refunded: int     # 退款/售后
    # 待发货提醒（paid 且超过 N 小时未发货）
    pending_ship_alert: int = 0


class OrderShipIn(BaseModel):
    """订单发货"""

    carrier: str = Field(default="", max_length=50, description="快递公司")
    tracking_no: str = Field(default="", max_length=60, description="快递单号")


class OrderNoteIn(BaseModel):
    """订单备注（商家备注，仅后台可见）"""

    note: str = Field(default="", max_length=500)


class OrderCancelIn(BaseModel):
    """订单取消 / 退款"""

    reason: str = Field(default="", max_length=200)
    refund: bool = Field(default=False, description="是否走退款流程（已支付订单）")


class OrderBatchIn(BaseModel):
    """订单批量操作"""

    order_nos: list[str] = Field(min_length=1, max_length=200)
    action: str = Field(description="ship=批量发货 / note=批量备注")
    carrier: str = Field(default="", max_length=50)
    tracking_no: str = Field(default="", max_length=60)
    note: str = Field(default="", max_length=500)


# ---------- 支付 ----------
class PayIn(BaseModel):
    method: str = "mock"


class PayPalCaptureIn(BaseModel):
    """PayPal 扣款入参（前端 SDK onApprove 回调时提交，服务端负责 capture）"""

    order_no: str = Field(min_length=1, max_length=64)


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transaction_no: str
    method: str
    amount: Decimal
    currency: str
    status: str
    gateway_response: dict[str, Any]


class AdminPaymentOut(PaymentOut):
    """后台支付记录（额外带中文展示值）"""

    method_label: str = ""
    status_label: str = ""


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


class AdminPasswordChangeIn(BaseModel):
    """修改当前登录管理员的密码（需验证当前密码）"""

    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


class AdminPasswordResetIn(BaseModel):
    """超级管理员重置指定管理员的密码（无需原密码）"""

    new_password: str = Field(min_length=6, max_length=128)


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


# ---------- 商品审核 / 批量管理 / 审计日志 ----------
class ProductReviewIn(BaseModel):
    """单个商品审核"""

    action: str = Field(description="approve=通过 / reject=驳回 / pending=重置为待审核")
    note: str = Field(default="", max_length=500, description="审核备注，驳回时建议填写")


class ProductBulkIn(BaseModel):
    """商品批量操作

    action 取值：
    - `status`      ：批量改上下架，`value` 为 active/draft/off_shelf
    - `review`      ：批量审核，`value` 为 approve/reject/pending
    - `category`    ：批量改分类，`category_id` 为目标分类（null 表示置为未分类）
    - `featured`    ：批量设置推荐，`value` 为 true/false
    - `delete`      ：批量删除
    """

    ids: list[int] = Field(min_length=1, max_length=500, description="商品 ID 列表")
    action: str = Field(
        description="status / review / category / featured / delete"
    )
    value: str | bool | None = Field(
        default=None, description="操作值（随 action 而定）"
    )
    category_id: int | None = Field(default=None, description="action=category 时的目标分类")
    note: str = Field(default="", max_length=500, description="审核备注（action=review 时可用）")


class ProductBulkResultOut(BaseModel):
    """批量操作结果"""

    action: str
    requested: int        # 请求处理的商品数
    succeeded: int        # 成功数
    skipped: int          # 跳过数（不存在/无变化）
    failed: int           # 失败数
    errors: list[str] = []
    product_ids: list[int] = []


class ProductAuditLogOut(BaseModel):
    """商品操作审计日志

    `detail` 为原始 JSON；后端额外派生 `action_label`（中文动作）、`tone`（徽标配色）
    与 `summary`（人话摘要），后台日志弹窗直接渲染，避免展示裸 JSON。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int | None
    product_sku: str
    product_name: str
    action: str
    detail: dict[str, Any] = {}
    operator: str
    created_at: datetime

    # 派生展示字段（只读，由 action/detail 计算）
    action_label: str = ""
    tone: str = "gray"
    summary: str = ""

    @model_validator(mode="after")
    def _fill_display(self):
        from app.models import (
            audit_action_label,
            audit_action_tone,
            describe_audit_detail,
        )

        self.action_label = audit_action_label(self.action)
        self.tone = audit_action_tone(self.action)
        self.summary = describe_audit_detail(self.action, self.detail)
        return self


class ProductAuditActionOut(BaseModel):
    """审计日志可选动作（用于日志弹窗的筛选下拉）"""

    action: str
    label: str
    count: int


class ProductAuditMetaOut(BaseModel):
    """审计日志筛选元信息"""

    total: int
    actions: list[ProductAuditActionOut] = []
    operators: list[str] = []


class ProductReviewStatsOut(BaseModel):
    """审核概览统计"""

    total: int
    pending: int
    approved: int
    rejected: int
    imported: int
    manual: int


# ---------- 商品状态（PDD 风格标签页）----------
class ProductStatusCountsOut(BaseModel):
    """按派生状态的商品数量（用于后台状态标签页角标）"""

    all: int
    on_sale: int       # 在售中
    off_shelf: int     # 已下架
    sold_out: int      # 已售罄
    pending: int       # 发布中（待审核）
    rejected: int      # 已驳回
    draft: int         # 草稿箱
    deleted: int       # 已删除（回收站）


class ProductPriceIn(BaseModel):
    """行内快速改价"""

    base_price: Decimal = Field(gt=0, description="新的基础价")
    sync_skus: bool = Field(
        default=True, description="是否同步把所有 SKU 的价格也改为新价"
    )
    reason: str = Field(default="", max_length=200, description="改价备注")


class ProductStockIn(BaseModel):
    """行内快速改库存（作用于该商品全部启用 SKU）"""

    mode: str = Field(default="set", description="set=设为该值 / add=在当前基础上增减")
    value: int = Field(description="目标库存或增减量")
    reason: str = Field(default="quick_edit", max_length=200)


class SKUPriceItemIn(BaseModel):
    """单个规格（SKU）的目标价格"""

    sku_id: int
    price: Decimal = Field(gt=0, description="该规格改后的价格")


class ProductSKUPriceIn(BaseModel):
    """按规格（SKU）批量改价（后台「修改价格」弹窗提交）

    只提交**规格价格**（对应前台的单买价），不含拼单价。
    """

    items: list[SKUPriceItemIn] = Field(min_length=1, description="要改价的规格列表")
    reason: str = Field(default="", max_length=200, description="改价备注")


class SKUStockItemIn(BaseModel):
    """单个规格（SKU）的库存变更"""

    sku_id: int
    value: int = Field(
        description="mode=set 时为目标库存（≥0）；mode=add 时为增减量（可为负）"
    )


class ProductSKUStockIn(BaseModel):
    """按规格（SKU）批量改库存（后台「修改库存」弹窗提交）

    - `mode=set`：把每个规格的库存**设为** `value`
    - `mode=add`：在每个规格当前库存上**增减** `value`
    """

    mode: str = Field(default="add", description="set=设为该值 / add=在当前基础上增减")
    items: list[SKUStockItemIn] = Field(min_length=1)
    reason: str = Field(default="quick_edit", max_length=200)


class SKUStockMovementOut(BaseModel):
    """库存修改记录（库存流水，带规格信息）"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    sku_id: int
    sku_code: str = ""
    attributes: dict[str, Any] = {}
    change_qty: int
    balance_after: int
    reason: str = ""
    reference: str | None = None
    operator: str | None = None
    created_at: datetime | None = None


class ProductStatusIn(BaseModel):
    """上架 / 下架"""

    reason: str = Field(default="", max_length=200, description="下架原因（可选）")


class ProductPublishCheckOut(BaseModel):
    """上架前体检结果"""

    can_publish: bool
    blockers: list[str] = []     # 阻止上架的问题
    warnings: list[str] = []     # 不阻止但建议处理


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


# ---------- 商品批量导入（产品册 PPT） ----------
class ImportSkippedSlideOut(BaseModel):
    """被跳过的 PPT 页面"""

    slide: int
    reason: str
    sku_code: str | None = None


class ImportedProductOut(BaseModel):
    """解析出的商品预览项"""

    slide: int
    sku_code: str
    name_zh: str
    name_en: str
    category_code: str
    base_price: Decimal
    colors: list[str] = []
    sizes: list[str] = []
    main_image: str | None = None
    images: list[str] = []
    warnings: list[str] = []


class ProductImportResultOut(BaseModel):
    """商品导入结果"""

    source: str                     # 上传文件名
    mode: str                       # replace / merge
    dry_run: bool                   # 是否为预览（未写库）
    total_slides: int               # PPT 总页数
    parsed_products: int            # 解析出的商品数
    imported: int                   # 实际入库商品数
    skipped: int                    # 跳过（重复货号/已存在/异常）
    failed: int                     # 写入失败数
    products_total: int             # 库中商品总数
    skus_total: int                 # 库中 SKU 总数
    categories_total: int           # 库中分类总数
    images: dict[str, int] = {}     # 图片统计 {main, detail}
    skipped_slides: list[ImportSkippedSlideOut] = []
    warnings: list[str] = []
    preview: list[ImportedProductOut] = []   # 前 N 条解析结果（dry_run 时更全）


class OrderStatusIn(BaseModel):
    status: str


# ---------- 商品包（文件夹）导出 / 导入 ----------
class ProductPackageExportIn(BaseModel):
    """商品包导出请求"""

    ids: list[int] = Field(
        min_length=1, max_length=2000, description="要导出的商品 ID 列表"
    )
    package_name: str = Field(
        default="", max_length=120,
        description="商品包名（作为文件夹名 / ZIP 内顶层目录名），留空则自动生成",
    )
    include_images: bool = Field(
        default=True, description="是否把商品图片一并打包进 images/"
    )
    zip_output: bool = Field(
        default=False, description="是否同时生成 .zip 供浏览器下载"
    )


class ProductPackageExportOut(BaseModel):
    """商品包导出结果"""

    package_name: str
    out_dir: str                      # 服务器上的绝对路径
    manifest_path: str                # manifest.json 路径
    product_count: int
    sku_count: int
    image_count: int
    missing_images: list[str] = []    # 未能打包的图片 URL
    warnings: list[str] = []
    zip_path: str | None = None       # 生成 zip 时的路径
    zip_bytes: int = 0
    zip_url: str | None = None        # 可下载 URL（/static/... 下的相对路径）


class ProductPackageImportServerIn(BaseModel):
    """从服务器已导出的商品包导入（不经浏览器上传）

    用 JSON 而非 multipart 是故意的：走 `App.api()` 即可，且请求体只有几十字节，
    不会被反向代理的 `client_max_body_size` 拦成 413。
    """

    name: str = Field(
        min_length=1, max_length=120,
        description="服务器 static/exports 下的包名（`GET /exports` 返回的 name）",
    )
    mode: str = Field(default="merge", description="merge=跳过已存在货号 / update=覆盖更新")
    review_status: str = Field(
        default="pending", description="pending=待审核（默认）/ approved=直接上架"
    )


class ProductPackageImportOut(BaseModel):
    """商品包导入结果"""

    source: str                       # 包名 / 上传文件名
    mode: str                         # merge / update
    imported: int                     # 新增商品数
    updated: int                      # 覆盖更新的商品数
    skipped: int                      # 已存在被跳过的商品数
    failed: int                       # 失败数
    sku_created: int
    sku_updated: int
    categories_created: int
    images_imported: int
    missing_images: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []
    products_total: int = 0
    skus_total: int = 0


class DashboardOut(BaseModel):
    products_count: int
    orders_count: int
    customers_count: int
    revenue: Decimal
    pending_orders: int
    low_stock: int
