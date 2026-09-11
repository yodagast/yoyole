---
name: ecommerce-dev
description: PyMall 电商独立站（FastAPI + async SQLAlchemy + PostgreSQL + Vue 3）的开发指南与常见陷阱。当在这个仓库中开发前端、后端、测试或修复 bug 时使用。
---

# YOYOLE 瑜伽户外独立站 — 开发指南（SKILL）

## 品牌规范

- 品牌名称：YOYOLE；业务主题：瑜伽练习、户外生活、徒步与自然探索。
- 全站 Logo 统一使用 `static/img/yoyole-logo.svg`，共享导航由 `static/js/pymall.js` 渲染，后台页面也必须引用同一资源。
- 不得新增潮流玩具、泛时尚、电子产品、图书等旧主题文案；商品种子内容应保持瑜伽/户外相关。
- 修改 `static/js/pymall.js` 后，所有引用该文件的 HTML 页面必须递增 query 版本号，避免浏览器缓存旧品牌内容。

## 技术栈

- 后端：FastAPI + async SQLAlchemy 2.0 + PostgreSQL（`postgresql+asyncpg://huangyong@localhost:5432/ecommerce`）
- 前端：Vue 3.4.38 Global Build（`static/js/vue.global.prod.js`），无构建步骤的多页应用
- 数据库命令：`psql -d ecommerce`（用户 huangyong 无密码）

## 启动

```bash
.venv/bin/uvicorn main:app          # 127.0.0.1:8010
# 或 ./run.sh
```

- 启动时通过 lifespan 自动建表 + 轻量迁移 + 种子数据
- 静态文件改动**无需重启后端**

## 前端架构

- 共享层 `static/js/pymall.js`：IIFE 挂 `window.PyMall` 与 `window.mountPyMall(options)`
  - `mountPyMall({ setup(){...} })` → `createApp` → 注册 `top-bar`/`main-nav`/`auth-modal`/`site-footer` → `mount('#app')`
  - 导出 `PyMall`: `Vue, store, t, tt, money, localName, getLang, switchLang, getToken, setToken, login, register, logout, refreshCart, buyNow, toast, openAuth, components{...}`
- 每页一个 Vue 应用，根组件 setup 里返回模板所需函数与 computed
- 设计系统 `static/css/jjshouse.css`：品牌玫红 `--jjs-primary:#d81e53`
- 多语言：`TRANSLATIONS`（zh/en），`store.lang` reactive
- 登录态：localStorage `access_token`；语言：localStorage + cookie `lang`

## API 契约速查

| 接口 | 说明 |
|---|---|
| `GET /api/products?category_id=&q=&featured=&page=&page_size=` | 列表（数组） |
| `GET /api/products/{id}` | 详情（含 `skus[]`） |
| `POST /api/auth/login\|register` | 返回 `{access_token}` |
| `GET/POST/PUT/DELETE /api/cart*` | 购物车 CRUD |
| `POST /api/orders/checkout` | 必带 `receiver_name+receiver_phone+receiver_address+payment_method` |
| `POST /api/orders/{order_no}/pay` | 返回 `{success, transaction_no, pay_url, amount}` |
| `GET /api/payments/mock/confirm?txn_no=` | 模拟支付确认 |
| `POST /api/admin/login` | 管理员登录 |
| `GET/POST /api/wishlist` | 收藏列表（`{items,total,product_ids}`）／添加（201，幂等） |
| `DELETE /api/wishlist/{id}` / `DELETE /api/wishlist` | 取消收藏／清空 |
| `POST /api/wishlist/move-to-cart/{id}` | 移入购物车（自动取消收藏，缺货 400） |
| `GET/POST /api/addresses`、`PUT/DELETE /api/addresses/{id}` | 收货地址 CRUD（is_default 互斥，列表默认在前） |
| `GET /api/products/{id}/reviews` | 公开评价列表（仅 APPROVED） |
| `POST /api/products/{id}/reviews` | 提交评价（未购 403、每人一评 409、PENDING） |
| `GET /api/products/{id}/reviewable` | `{reviewable, reason: not_purchased\|already_reviewed\|ok}`（需登录 401） |
| `GET /api/my-reviews` | 我的评价 |
| `POST /api/admin/reviews/{id}/moderate` | 管理员审核评价（需 `/api/admin/login`） |
| `GET /api/products?min_price=&max_price=&sort_by=` | 高级筛选：`sort_by` ∈ `price_asc/price_desc/sales_desc` |
| `GET /api/products/autocomplete?q=&limit=` | 搜索建议，返回 `id/sku_code/name_zh/name_en/base_price/main_image` |

- **支付方式仅 mock 开通**（`PAYMENT_GATEWAY_ENABLED`），alipay/wechat/stripe 后端 400
- 多语言名称：后端按 `Accept-Language` 返回 `display_name`
- **`/products/autocomplete` 路由必须注册在 `/products/{product_id}` 之前**，否则被路径参数吞掉（FastAPI 按声明顺序匹配）

## 高频踩坑（务必遵守）

1. **模板用到 `t()`/全局函数的组件必须有 `setup(){ return {...} }`**，否则 `t is not a function`
2. **凡模板里有赋值的 computed 必须 `{get, set}` 双向**（如 `detailOpen`、`selectedSku`、`kw`），只读 computed 赋值会静默失败——典型的「模态关不掉」症状
3. **卡片级 `@click` 与子按钮 `@click.stop.prevent`** 防冒泡
4. **改了 pymall.js 后，6 个 HTML 的引用版本号要 +1**（`pymall.js?v=4` → `?v=5`，about/cart/index/orders/products/wishlist 共 6 个文件），否则浏览器 HTTP 缓存（服务器只发 ETag 无 Cache-Control）会用旧 JS
5. **模板不能直接访问 `PyMall` 全局**，必须在 setup return 里返回
6. 中文搜索：后端已用 JSONB `#>>` 修复，前端直接传 `q` 即可
7. 支付 `pay` 接口字段是 `transaction_no`（不是 txn）
8. 后端 SKU/库存模型：`stock` 真实库存、`locked_stock` 下单未支付锁定、`available_stock` = stock - locked_stock
9. Playwright 验证表单输入需原生 setter + `dispatchEvent('input')`；点击优先 `el.click()` + `page.evaluate`
10. **测试勿用持久化 buyer 账号产生评价/订单残留**：评价测试必须新注册临时账号（如 `rv_<ts>@example.com`，conftest 会清理 `test_`/`rv_` 前缀），否则二次运行 409「您已评价过」
11. **`CartItemOut` 无 `product_id` 字段**，购物车断言用 `sku_id`（先查详情取 SKU id）
12. **`/products/autocomplete` 必须在 `/products/{product_id}` 之前声明**（同上 API 契约）
13. 评价提交有「moved to cart 自动取消收藏」联动；前端 `alreadyReviewed` 状态区分「已评价过/未登录」两种提示

## 测试

```bash
.venv/bin/python -m pytest test/ -v
```

- 测试依赖 `requests`/`pytest`，需先启动服务
- 测试账号：买家 `buyer@example.com`/`buyer123`；管理员 `admin`/`admin123`

## 管理后台

- 入口隐藏：导航栏无 admin 链接，直接访问 `/admin.html`
- `static/js/common.js` 是旧版后台脚本，仅供 admin.html 使用，勿用于 Vue 页面