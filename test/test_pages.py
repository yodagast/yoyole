"""前端页面测试。

覆盖 static/ 下所有页面（Vue 重写后的 7 个页面）：
- index.html 首页
- products.html 商品列表页
- cart.html 购物车页
- orders.html 订单页
- admin.html 管理后台页
- about.html 品牌故事页
- wishlist.html 我的收藏页

对每个页面断言：
1. HTTP 200 且 Content-Type 为 text/html
2. 页面包含关键挂载点与资源引用（Vue 全局脚本、jjshouse 样式）
3. 静态资源（CSS/JS）均可访问（200）

运行（需先启动服务）：
    .venv/bin/python -m pytest test/test_pages.py -v
"""
from __future__ import annotations

import os

import pytest
import requests

# 目标服务地址：默认 8010，可覆盖（如 export TEST_BASE_URL=http://127.0.0.1:8020）
BASE = os.environ.get("TEST_BASE_URL", "http://127.0.0.1:8010").rstrip("/")

# 页面 -> 关键内容断言（该页面必须包含的字符串）
PAGE_ASSERTS = {
    "index.html": [
        "mountPyMall",           # Vue 应用挂载
        'id="app"',              # 根挂载容器
        "jjshouse",              # 设计语言
        "buy_now",               # 立即购买链接
        "goDetail",              # 商品卡片跳转
        "hero-slide-box",        # 首页轮播（优化后大图卡片）
        "hot_sale",              # 热销商品区块
        "featured",              # 推荐商品区块
    ],
    "products.html": [
        "mountPyMall",
        "modal-mask",            # 详情模态
        "buyNow",                # 立即购买（卡片 + 模态）
        "selectedSku",           # SKU 选择
        "detailOpen",            # 模态开关（v-if）
    ],
    "cart.html": [
        "mountPyMall",
        "empty_cart",            # 空购物车
        "checkout",              # 结算
        "'/api/orders/checkout'",  # 结算接口
    ],
    "orders.html": [
        "mountPyMall",
        "no_orders",             # 空订单
        "order-card",            # 订单卡片
        "'/api/orders/' + o.order_no + '/pay'",  # 支付操作
        "'/api/orders/' + o.order_no + '/cancel'",  # 取消操作
    ],
    "admin.html": [
        "admin",                 # 后台
    ],
    "admin-product-edit.html": [
        "基本信息",              # Tab1 基本信息
        "商品图片",              # Tab2 商品媒体编辑
        "SKU规格管理",           # Tab3 SKU 库存编辑
        "商品详情",              # Tab4 商品详情编辑
        "saveProduct",           # 保存逻辑
        "params.get('new')",     # 新增商品独立页面模式
    ],
    "admin-category-edit.html": [
        "类目编辑",              # 页面标题
        "类目概览",              # 顶部统计卡
        "关联商品",              # 关联商品数统计
        "上级类目",              # 父类目下拉
        "'/api/admin/categories/'",  # 详情接口（独立编辑页加载）
        "params.get('new')",     # 新增类目模式
    ],
    "about.html": [
        "mountPyMall",
        "story-section",           # 品牌故事区块
        "milestones",              # 成长历程
        "site-footer",             # 页脚（含统一邮箱订阅入口）
        "about-hero",              # Hero 区域
    ],
    "stories.html": [
        "mountPyMall",
        "id=\"user-stories\"",     # 用户故事区块
        "discover-grid",           # 故事卡片网格
        "'/api/user-stories'",     # 公开故事接口
        "activeTag",               # 标签筛选
    ],
    "wishlist.html": [
        "mountPyMall",
        "my_wishlist",           # 我的收藏标题
        "'/api/wishlist'",       # 收藏接口
        "move-to-cart",          # 移入购物车
        "wishlist_empty",        # 空态
    ],
    "story-detail.html": [
        "mountPyMall",
        "用户故事详情",           # 页面标题
        "'/api/user-stories/'",  # 详情接口
        "sd-card",               # 详情卡片
    ],
    "admin-banner-edit.html": [
        "轮播图编辑",             # 页面标题
        "首页 Hero 预览",        # 实时预览
        "title_i18n",            # 多语文案
        "'/api/admin/banners/'", # 单条 fetch
        "params.get('new')",     # 新增模式
        "uploadImageFile",       # 图片上传
    ],
    "account.html": [
        "mountPyMall",
        "我的故事",               # 我的故事 tab
        "'/api/user-stories/mine'",  # 我的投稿接口
        "story-detail.html",     # 已发布故事链接详情页
    ],
}

# 每页引用的静态资源（正则抓取后断言全部可访问）
STATIC_ASSETS = [
    "/static/css/jjshouse.css",
    "/static/css/style.css",
    "/static/js/pymall.js",
]

ALL_PAGES = list(PAGE_ASSERTS.keys())


@pytest.mark.parametrize("page", ALL_PAGES)
def test_page_returns_200(page):
    r = requests.get(f"{BASE}/{page}")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("Content-Type", "")


@pytest.mark.parametrize("page,keywords", PAGE_ASSERTS.items())
def test_page_contains_key_content(page, keywords):
    r = requests.get(f"{BASE}/{page}")
    html = r.text
    for kw in keywords:
        assert kw in html, f"{page} 缺少关键内容: {kw}"


@pytest.mark.parametrize("asset", STATIC_ASSETS)
def test_static_assets_available(asset):
    r = requests.get(f"{BASE}{asset}")
    assert r.status_code == 200


def test_index_has_hidden_admin_nav():
    """主页导航不应再出现「管理后台」链接（隐藏 admin 入口）"""
    r = requests.get(f"{BASE}/index.html")
    html = r.text
    # 页面本身不引用 admin.html 作为导航入口
    assert "href=\"/admin.html\"" not in html


def test_nav_has_about_link():
    """主导航（pymall.js MainNav）应包含品牌故事（about）入口和用户故事（stories）入口"""
    r = requests.get(f"{BASE}/static/js/pymall.js")
    assert r.status_code == 200
    assert 'href="/about.html"' in r.text
    assert 'href="/stories.html"' in r.text


@pytest.mark.parametrize("page", ["index.html", "products.html", "cart.html", "orders.html", "about.html", "stories.html", "wishlist.html", "story-detail.html", "account.html"])
def test_common_components_present(page):
    """所有前端页面都应引用 pymall.js（含共享组件）"""
    r = requests.get(f"{BASE}/{page}")
    html = r.text
    assert "pymall.js" in html, f"{page} 缺少 pymall.js 引用"