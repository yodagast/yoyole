"""多语言国际化支持：翻译表 + 语言解析中间件"""
from __future__ import annotations

from typing import Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

# UI 文案翻译表
TRANSLATIONS: dict[str, dict[str, str]] = {
    "zh": {
        "home": "首页",
        "products": "商品",
        "cart": "购物车",
        "orders": "我的订单",
        "login": "登录",
        "register": "注册",
        "logout": "退出登录",
        "admin": "管理后台",
        "search": "搜索",
        "search_placeholder": "搜索商品...",
        "featured_products": "推荐商品",
        "all_products": "全部商品",
        "add_to_cart": "加入购物车",
        "buy_now": "立即购买",
        "price": "价格",
        "stock": "库存",
        "sku": "规格",
        "total": "合计",
        "quantity": "数量",
        "checkout": "结算",
        "receiver_name": "收货人",
        "receiver_phone": "联系电话",
        "receiver_address": "收货地址",
        "remark": "备注",
        "submit_order": "提交订单",
        "pay_now": "立即支付",
        "payment_method": "支付方式",
        "order_no": "订单号",
        "order_status": "订单状态",
        "order_time": "下单时间",
        "order_detail": "订单详情",
        "empty_cart": "购物车是空的",
        "go_shopping": "去逛逛",
        "language": "语言",
        "email": "邮箱",
        "password": "密码",
        "full_name": "姓名",
        "phone": "手机号",
        "mock_pay": "模拟支付",
        "alipay": "支付宝",
        "wechat": "微信支付",
        "stripe": "Stripe",
        "pending": "待支付",
        "paid": "已支付",
        "shipped": "已发货",
        "completed": "已完成",
        "cancelled": "已取消",
        "refunded": "已退款",
        "status": "状态",
        "actions": "操作",
        "cancel_order": "取消订单",
        "confirm_receipt": "确认收货",
        "no_orders": "暂无订单",
        "subtotal": "商品小计",
        "shipping_fee": "运费",
        "discount": "优惠",
        "dashboard": "仪表盘",
        "category": "分类",
        "inventory": "库存",
        "reports": "报表",
        "settings": "设置",
        "customers": "客户",
        "welcome": "欢迎",
        "products_count": "商品总数",
        "orders_count": "订单总数",
        "revenue": "营收",
        "pending_orders": "待支付订单",
        "customers_count": "客户总数",
        "low_stock": "低库存商品",
        "add_product": "新增商品",
        "edit": "编辑",
        "delete": "删除",
        "save": "保存",
        "cancel": "取消",
        "back": "返回",
        "loading": "加载中...",
        "operation_success": "操作成功",
        "operation_failed": "操作失败",
        "confirm": "确认",
        "not_logged_in": "请先登录",
        "unit_price": "单价",
    },
    "en": {
        "home": "Home",
        "products": "Products",
        "cart": "Cart",
        "orders": "My Orders",
        "login": "Login",
        "register": "Register",
        "logout": "Logout",
        "admin": "Admin",
        "search": "Search",
        "search_placeholder": "Search products...",
        "featured_products": "Featured Products",
        "all_products": "All Products",
        "add_to_cart": "Add to Cart",
        "buy_now": "Buy Now",
        "price": "Price",
        "stock": "Stock",
        "sku": "SKU",
        "total": "Total",
        "quantity": "Quantity",
        "checkout": "Checkout",
        "receiver_name": "Receiver",
        "receiver_phone": "Phone",
        "receiver_address": "Address",
        "remark": "Remark",
        "submit_order": "Submit Order",
        "pay_now": "Pay Now",
        "payment_method": "Payment Method",
        "order_no": "Order No.",
        "order_status": "Order Status",
        "order_time": "Order Time",
        "order_detail": "Order Detail",
        "empty_cart": "Your cart is empty",
        "go_shopping": "Go Shopping",
        "language": "Language",
        "email": "Email",
        "password": "Password",
        "full_name": "Full Name",
        "phone": "Phone",
        "mock_pay": "Mock Pay",
        "alipay": "Alipay",
        "wechat": "WeChat Pay",
        "stripe": "Stripe",
        "pending": "Pending",
        "paid": "Paid",
        "shipped": "Shipped",
        "completed": "Completed",
        "cancelled": "Cancelled",
        "refunded": "Refunded",
        "status": "Status",
        "actions": "Actions",
        "cancel_order": "Cancel Order",
        "confirm_receipt": "Confirm Receipt",
        "no_orders": "No orders yet",
        "subtotal": "Subtotal",
        "shipping_fee": "Shipping Fee",
        "discount": "Discount",
        "dashboard": "Dashboard",
        "category": "Categories",
        "inventory": "Inventory",
        "reports": "Reports",
        "settings": "Settings",
        "customers": "Customers",
        "welcome": "Welcome",
        "products_count": "Products",
        "orders_count": "Orders",
        "revenue": "Revenue",
        "pending_orders": "Pending Orders",
        "customers_count": "Customers",
        "low_stock": "Low Stock",
        "add_product": "Add Product",
        "edit": "Edit",
        "delete": "Delete",
        "save": "Save",
        "cancel": "Cancel",
        "back": "Back",
        "loading": "Loading...",
        "operation_success": "Success",
        "operation_failed": "Failed",
        "confirm": "Confirm",
        "not_logged_in": "Please login first",
        "unit_price": "Unit Price",
    },
}


def resolve_language(lang: str | None) -> str:
    """规范化语言代码，回退到默认语言"""
    if lang and lang in settings.SUPPORTED_LANGUAGES:
        return lang
    return settings.DEFAULT_LANGUAGE


def translate(key: str, lang: str) -> str:
    """根据语言获取翻译文案"""
    table = TRANSLATIONS.get(lang) or TRANSLATIONS[settings.DEFAULT_LANGUAGE]
    return table.get(key) or TRANSLATIONS[settings.DEFAULT_LANGUAGE].get(key, key)


class I18nMiddleware(BaseHTTPMiddleware):
    """解析请求语言：优先级 query?lang= > Cookie lang > Accept-Language > 默认"""

    async def dispatch(self, request: Request, call_next):
        lang = settings.DEFAULT_LANGUAGE

        # 1) query 参数
        query_lang = request.query_params.get("lang")
        if query_lang:
            lang = resolve_language(query_lang)
        else:
            # 2) Cookie
            cookie_lang = request.cookies.get("lang")
            if cookie_lang:
                lang = resolve_language(cookie_lang)
            else:
                # 3) Accept-Language 头
                accept = request.headers.get("accept-language")
                if accept:
                    first = accept.split(",")[0].strip().split("-")[0].lower()
                    lang = resolve_language(first)

        request.state.lang = lang
        request.state.t = lambda key: translate(key, lang)
        response = await call_next(request)
        return response


def get_lang(request: Request) -> str:
    """便捷获取当前请求语言"""
    return getattr(request.state, "lang", settings.DEFAULT_LANGUAGE)


def get_t(request: Request) -> Callable[[str], str]:
    """便捷获取翻译函数"""
    return getattr(request.state, "t", lambda key: translate(key, settings.DEFAULT_LANGUAGE))