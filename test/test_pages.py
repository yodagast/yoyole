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
        "home-grid",             # 首页专用网格（固定列数，保证每行铺满）
        "HOME_PAGE_SIZE",        # 每区块取 12 款（12 能被 6/4/3/2 整除）
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
        "about-page",              # 品牌故事页专属设计令牌作用域
        "ab-hero",                 # 满幅图 Hero
        "ab-story-grid",           # 品牌故事（图文双栏 + 引文）
        "ab-values-grid",          # 价值观（深色三栏编辑风）
        "ab-timeline",             # 成长历程（中轴交替时间线）
        "ab-cta-btn",              # 收尾 CTA
        "/api/site-content?page=about",   # 文案由后台品牌管理维护
        "site-footer",             # 页脚（含统一邮箱订阅入口）
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


def test_footer_newsletter_sits_after_payment():
    """「订阅优惠信息」应是页脚最后一列、紧邻「支付方式」右侧，且不再有独立通栏色带

    历史形态是一条通栏渐变带（.newsletter），两侧留白很大；
    现已并入 .footer-inner 的第 5 列 .footer-news。
    """
    r = requests.get(f"{BASE}/static/js/pymall.js")
    assert r.status_code == 200
    js = r.text

    # 通栏色带已移除
    assert 'class="newsletter"' not in js
    assert "newsletter-inner" not in js

    # 订阅列存在且在支付方式之后
    assert "footer-col footer-news" in js
    pay_at = js.index("payment_method")
    news_at = js.index("footer-col footer-news")
    assert news_at > pay_at, "订阅列应排在「支付方式」列之后"

    # 表单与提交逻辑仍在
    assert "footer-news-form" in js
    assert "/api/subscribe" in js


def test_footer_newsletter_email_is_writable():
    """页脚订阅的 email 必须用可写 computed 暴露给 v-model

    只给 getter 的 computed 是只读的：v-model 写不进去，state.email 永远为空，
    点「订阅」必然提示「请输入您的邮箱」——表单完全不可用。
    """
    r = requests.get(f"{BASE}/static/js/pymall.js")
    assert r.status_code == 200
    js = r.text
    # 不应再出现只读 computed 的写法
    assert "Vue.computed(() => state.email)" not in js
    # 应使用带 setter 的可写 computed
    assert "set: function (v) { state.email = v; }" in js


def test_css_has_version_param():
    """jjshouse.css 必须带 ?v= 版本号，否则改动后浏览器会继续用旧缓存"""
    r = requests.get(f"{BASE}/index.html")
    assert r.status_code == 200
    assert "jjshouse.css?v=" in r.text


def test_section_does_not_clobber_container_padding():
    """`.section` 只能设纵向内边距

    若写成 `padding: Xpx 0 Ypx`，会连带把左右内边距置 0；而 .section 在 .container
    之后定义、优先级相同 → 覆盖掉 .container 的 20px 左右留白，
    使 `class="section container"` 的网格比内部套 .container 的网格宽 40px（错位）。
    """
    r = requests.get(f"{BASE}/static/css/jjshouse.css")
    assert r.status_code == 200
    css = r.text
    assert "padding-top: 54px" in css and "padding-bottom: 34px" in css
    assert ".section {\n  padding: 54px 0 34px;" not in css
    assert "padding: 46px 0 30px" not in css


def test_home_grid_has_fixed_columns():
    """首页网格必须按断点固定列数（12 能被 6/4/3/2 整除），否则最后一行会留空"""
    r = requests.get(f"{BASE}/index.html")
    assert r.status_code == 200
    html = r.text
    assert ".home-grid" in html
    for cols in ("repeat(4, 1fr)", "repeat(6, 1fr)", "repeat(3, 1fr)", "repeat(2, 1fr)"):
        assert cols in html, f"首页网格缺少固定列数：{cols}"


def test_index_inline_section_padding_keeps_side_gutter():
    """index.html 内联样式里的 .section 也不能用 `padding: Xpx 0 Ypx`

    内联 <style> 在外部样式表之后生效，写四值简写同样会把 .container 的
    左右留白置 0，让两个商品区块左右错位 20px。
    """
    r = requests.get(f"{BASE}/index.html")
    assert r.status_code == 200
    html = r.text
    assert ".section { padding-top: 54px; padding-bottom: 34px; }" in html
    assert ".section { padding: 54px 0 34px; }" not in html
    assert ".section { padding: 34px 0 22px; }" not in html


@pytest.mark.parametrize("page", ["index.html", "products.html", "cart.html", "orders.html", "about.html", "stories.html", "wishlist.html", "story-detail.html", "account.html"])
def test_common_components_present(page):
    """所有前端页面都应引用 pymall.js（含共享组件）"""
    r = requests.get(f"{BASE}/{page}")
    html = r.text
    assert "pymall.js" in html, f"{page} 缺少 pymall.js 引用"


# ---------- 「关于我们」品牌故事页（编辑杂志风）----------
def test_about_uses_editorial_serif_and_palette():
    """关于我们页要有衬线标题栈、奶油底与深色块的设计令牌"""
    r = requests.get(f"{BASE}/about.html")
    assert r.status_code == 200
    html = r.text
    for token in ("--ab-serif", "--ab-cream", "--ab-ink", "--ab-accent"):
        assert token in html, f"缺少设计令牌 {token}"
    # 衬线字体栈要覆盖 macOS / Windows 常见中文字体
    assert "Songti SC" in html and "SimSun" in html


def test_about_section_rhythm_alternates():
    """区块必须是「深-浅-深-浅-深」交替节奏"""
    r = requests.get(f"{BASE}/about.html")
    html = r.text
    assert "ab-section--cream" in html
    assert "ab-section--ink" in html
    # Hero 与收尾 CTA 都用深色，与中间区块形成呼应
    assert html.count("ab-section--ink") >= 2


def test_about_timeline_alternates_around_center_line():
    """成长历程必须是中轴交替时间线（每项一半宽 + 奇偶不同侧）

    否则会退化成一列卡片，失去模板的编辑风排版特征。
    """
    r = requests.get(f"{BASE}/about.html")
    html = r.text
    assert ".ab-timeline::before" in html, "缺少中轴线"
    assert ".ab-tl-item {\n      position: relative;\n      width: 50%;" in html, \
        "时间线项应为半宽（左右交替）"
    assert ".ab-tl-item:nth-child(even)" in html, "缺少偶数项反向排布"
    assert ".ab-tl-dot" in html and "border-radius: 50%" in html
    # 窄屏要退化为左轴单列
    assert ".ab-tl-item:nth-child(even) {" in html


def test_about_has_no_legacy_classnames():
    """旧版 about 页的类名不应再残留（避免样式与结构各自为政）"""
    r = requests.get(f"{BASE}/about.html")
    html = r.text
    for legacy in ('class="about-hero"', 'class="story-section"', 'class="story-grid"',
                   'class="milestones"', 'class="values-section"', 'class="brand-cta"'):
        assert legacy not in html, f"残留旧类名：{legacy}"


def _css_rule(html, selector):
    """粗略截取内联 <style> 里某个选择器的声明块，用于断言设计细节"""
    idx = html.index(selector)
    return html[idx:html.index('}', idx)]


def test_about_timeline_cards_are_dark_with_light_text():
    """时间线卡片为黑底，卡片内文字必须整体翻转为浅色

    只把 background 改黑、不改文字是最容易掉进去的坑：
    年份原本是深玫红 `--ab-accent-deep`、正文原本是 `--ab-text-soft`(#5c5751)，
    留在黑底上会整片看不见。
    """
    r = requests.get(f"{BASE}/about.html")
    assert r.status_code == 200
    html = r.text

    card = _css_rule(html, '.ab-tl-card {')
    assert 'background: var(--ab-ink)' in card, "时间线卡片应为深色底"
    assert 'background: #fff' not in card

    assert 'color: #ff9dbd' in _css_rule(html, '.ab-tl-year {'), \
        "黑底年份需改用亮玫红（深玫红在黑底上对比度不足）"
    assert 'color: #fff' in _css_rule(html, '.ab-tl-title {')
    assert 'rgba(255,255,255,0.62)' in _css_rule(html, '.ab-tl-desc {')


def test_about_story_sits_on_page_background():
    """「我们的故事」必须与页面底色一致，不做白色卡片

    参考模板通篇一个底色，故事文字直接落在页面上。
    若给它加回白底 + 边框，奶油底色上会浮出一块突兀的白色面板。

    另外卡片的横向内边距必须一起去掉：留着会让故事文字比其它区块内缩一截，
    与整页左基线对不齐（图文间距改由 grid gap 承担）。
    """
    r = requests.get(f"{BASE}/about.html")
    assert r.status_code == 200
    html = r.text

    grid = _css_rule(html, '.ab-story-grid {')
    assert 'background: #fff' not in grid, "故事区块不应再有白色底"
    assert 'border: 1px' not in grid, "故事区块不应再有边框"
    assert 'gap:' in grid, "图文间距应由 gap 承担"

    copy = _css_rule(html, '.ab-story-copy {')
    assert 'clamp(4px, 1.2vw, 12px) 0' in copy, \
        "故事文字应去掉横向内边距，否则与容器左基线对不齐"


def test_about_dark_blocks_merge_with_footer():
    """收尾 CTA 必须与页脚无缝相接

    两个坑：
    1. 页面深色块若与页脚不同色，接缝处会出现一条色差；
    2. `.footer` 有 40px 外边距，会让 CTA 与页脚之间露出页面底色（奶油），
       在页尾形成一条割裂两片深色的空白带。

    参考站的做法是「同色 + 无间隙」，此处用 .about-page 作用域屏蔽页脚外边距来对齐。
    """
    r = requests.get(f"{BASE}/about.html")
    assert r.status_code == 200
    html = r.text
    # 深色令牌必须直接引用页脚色，避免硬编码出另一种深色
    assert "--ab-ink: var(--jjs-dark);" in html, "深色区块应与页脚同色"
    # 页脚外边距必须在本页屏蔽，且优先级要高过断点里的 .ab-section 简写 padding
    assert ".about-page .footer { margin-top: 0; }" in html, "页脚 40px 外边距未屏蔽，会出现空隙带"
    assert ".about-page .ab-cta { padding-bottom: 0; }" in html, \
        "收尾 CTA 底部留白未收紧（且需 .about-page 前缀压过断点规则）"
    # CTA 要有独立类名，否则无法单独收紧底部留白
    assert 'class="ab-section ab-section--ink ab-cta"' in html


def test_about_keeps_cms_bindings():
    """重构后后台「品牌管理」的字段仍要全部驱动前端"""
    r = requests.get(f"{BASE}/about.html")
    assert r.status_code == 200
    html = r.text
    assert "/api/site-content?page=about" in html
    for binding in ("hero_tag", "hero_title", "hero_subtitle", "story_title",
                    "story_content", "milestones", "values", "cta_title", "cta_desc"):
        assert binding in html, f"缺少后台字段绑定：{binding}"
    # Hero 与故事图都要有默认占位，接口异常时版式不塌
    assert "DEFAULT_HERO_IMG" in html
    assert "picsum.photos/seed/yoyole-nature" in html


def test_footer_shows_icp_beian_and_operator():
    """页脚必须展示 ICP 备案号（可跳转工信部）和网站主办单位名称

    备案合规的两个硬性点，缺一即可能被判不合规：
    1. 备案号必须可点击跳转到工信部备案系统 https://beian.miit.gov.cn/；
       只写一行纯文本不跳转是常见踩坑点；
    2. 备案主体名称必须与备案证书完全一致，所以与备案号一起展示，
       且统一取常量，避免多处硬编码写错主体名。
    """
    r = requests.get(f"{BASE}/static/js/pymall.js")
    assert r.status_code == 200
    js = r.text

    assert "浙ICP备2026077052号" in js, "页脚缺少 ICP 备案号"
    assert "杭州余杭波动粒子信息经营部" in js, "页脚缺少网站主办单位名称"
    # 备案号必须链接到工信部备案系统，且外链需带 noopener
    assert "https://beian.miit.gov.cn/" in js, "备案号未链接到工信部备案系统"
    assert 'class="beian-link"' in js
    assert 'target="_blank"' in js and "noopener" in js
    # 备案信息与页面标题共用同一常量，避免多处硬编码不一致
    assert "var ICP_BEIAN_NO = '浙ICP备2026077052号';" in js
    assert "var COMPANY_NAME = '杭州余杭波动粒子信息经营部';" in js


def test_beian_link_is_styled():
    """备案号链接不能与版权文字同样暗淡/不可辨识

    .copyright 里所有文字都是 rgba(255,255,255,.5) 的浅灰，
    备案号若继承该颜色并叠加无下划线，会被判定为「备案号不易辨识」。
    """
    r = requests.get(f"{BASE}/static/css/jjshouse.css")
    assert r.status_code == 200
    css = r.text
    assert ".copyright .beian-link" in css, "备案号链接缺少独立样式，不易辨识"
    assert ".copyright .beian-link:hover" in css


@pytest.mark.parametrize("page", [
    "index.html", "products.html", "cart.html", "orders.html", "order-detail.html",
    "about.html", "stories.html", "story-detail.html", "wishlist.html", "account.html",
])
def test_page_title_contains_operator(page):
    """页面标题要带主办单位名称

    浏览器标签页显示的就是 <title>；关闭标签时，Chrome/Edge 会在
    「最近关闭的标签页」里回显该标题，主体名称因此始终可见。
    """
    r = requests.get(f"{BASE}/{page}")
    assert r.status_code == 200
    assert "杭州余杭波动粒子信息经营部" in r.text, f"{page} 的 <title> 缺少主办单位名称"


def test_dynamic_page_title_contains_operator():
    """故事页会按语言动态改写 document.title，改写后同样要带主办单位名称"""
    for page in ("stories.html", "story-detail.html"):
        r = requests.get(f"{BASE}/{page}")
        assert r.status_code == 200
        assert "PyMall.siteTitle(" in r.text, f"{page} 动态标题未走 siteTitle，切换语言后主体名称会丢失"


FAVICON_ASSETS = [
    "/static/img/favicon.svg",
    "/static/img/favicon-32x32.png",
    "/static/img/favicon-16x16.png",
    "/static/img/apple-touch-icon.png",
]


@pytest.mark.parametrize("page", ALL_PAGES)
def test_page_declares_favicon(page):
    """每个页面都要声明标签页图标，否则浏览器标签栏是空白的默认图标"""
    r = requests.get(f"{BASE}/{page}")
    assert r.status_code == 200
    html = r.text
    assert 'rel="icon"' in html, f'{page} 缺少 <link rel="icon">'
    assert "/static/img/favicon.svg" in html, f"{page} 未引用 SVG 标签页图标"


@pytest.mark.parametrize("asset", FAVICON_ASSETS)
def test_favicon_assets_available(asset):
    r = requests.get(f"{BASE}{asset}")
    assert r.status_code == 200
    assert r.content, f"{asset} 是空文件"


def test_favicon_ico_at_site_root():
    """浏览器会无条件请求 /favicon.ico（不读 <link>），必须命中而不是 404

    站点根路径由 `StaticFiles(directory="static", html=True)` 挂载，
    所以图标文件要放在 static/favicon.ico，不能只放 static/img/ 下。
    """
    r = requests.get(f"{BASE}/favicon.ico")
    assert r.status_code == 200, "/favicon.ico 未命中，需放到 static/ 根目录"
    assert r.content[:4] == b"\x00\x00\x01\x00", "/favicon.ico 不是合法的 ICO 文件"