# 项目记忆（MEMORY）

保存本仓库的开发历程、关键决策与已验证事实。来源：实际开发中验证过的做法，供 AI 与开发者复用。

## 项目状态（2026-08-21）

- 平台：电商独立站（PyMall）
- 后端：FastAPI + async SQLAlchemy + PostgreSQL，入口 `main.py`，双挂载：`/static` 与 `/`
- 前端：Vue 3 重构完成（jjshouse 风格），7 个页面：index/products/cart/orders/about/wishlist/admin
- 服务器：`127.0.0.1:8010`，`.venv/bin/uvicorn main:app`
- 数据库：`psql -d ecommerce`

## 开发履历

### 阶段一：后端 bug 修复（已完成并验证）
- 静态文件 404 → `app.mount("/static", ...)` + `app.mount("/", StaticFiles(html=True))` 双挂载
- 中文搜索失败 → `name_i18n` 转 JSONB 用 `#>>` 取 zh/en 字段匹配
- `last_login` 列缺失 → `run_light_migrations` 轻量迁移
- 支付确认 500 → OrderItem 补 `sku` relationship + 链式 `selectinload`
- 未配置支付通道下单 → checkout 400 拦截
- `pay` 接口返回字段：`success/transaction_no/pay_url/amount`

### 阶段二：Vue 3 前端重构（已完成并验证）
- 从 jQuery 旧前端重构为 Vue 3 Global Build 多页应用
- `pymall.js` 共享层：mountPyMall 编排、store、i18n、api、认证、组件

### 阶段三：立即购买（已完成并验证）
- 商品卡片 `.meta` 行加 `⚡ 立即购买` 按钮（`.buy-link`），`@click.stop.prevent="buyNow(p)"`
- `PyMall.buyNow(p)`：登录检查 → 取 SKU → 加购 → 跳购物车

### 阶段四：模态关闭修复（已完成并验证）
- 详情模态 × / 遮罩 / SKU 切换关不掉，根因是 `detailOpen`/`selectedSku` 只读 computed
- 改为 `{get, set}` 双向后全部恢复正常

### 阶段五（已完成）：
- 导航栏隐藏 `管理后台` 链接（MainNav 移除 `<a href="/admin.html">`，后台仍可 URL 直达：`/admin.html`）
- 新建 `test/`：全量 API（test_api.py）+ 页面（test_pages.py）pytest 测试，**60 个用例全部通过**
  - `test/conftest.py`：session 级自动清理测试数据（商品 `PYTEST-%`、分类 `pytest-%`、账号 `test_%@example.com`），运行后自动归零
  - 测试数据特征前缀：商品 `PYTEST-`、分类 `pytest-`、账号 `test_<ts>@example.com`、订单 remark `pytest 下单`
- 新建 `.skill/`：SKILL.md 开发指南（架构/API/踩坑）+ MEMORY.md 履历记忆
- `pymall.js?v=2` → `?v=3`（4 个 HTML 同步升级，避免浏览器缓存旧 JS）
- 新增 `.vscode/settings.json`（watcher/exclude 配置）

### 测试要点（重要）
- 运行：`.venv/bin/python -m pytest test/ -v`（需先启动服务 127.0.0.1:8010）
- mock 回调 `handle_callback` 恒返回 success=True，未知 txn 由 `_mark_payment_success` 抛 404——callback 传未知 txn 应断言 404
- `ProductListOut`（列表接口）不含 `category_id` 字段，分类过滤核实需查详情接口

### 阶段六（已完成，93 测试通过）：
- **任务 1（完整电商后端 + 前端集成）**：按 Sanoy24/fastapi-ecommerce 参考补齐后端接口并集成 Vue 前端
  - 新表：`reviews`（审核流：pending→approved/rejected）、`wishlist_items`（uq_customer_product）、`addresses`（is_default 互斥）——lifespan 自动 create_all
  - 新路由：`wishlist`（CRUD/move-to-cart）、`addresses`、`reviews`（列表/提交/reviewable/my-reviews）、catalog 加 autocomplete + 价格/销量筛选、admin 加评价审核
  - 前端：`wishlist.html` 收藏页、`products.html` 收藏♥+评价表单+筛选栏+autocomplete 建议面板、`cart.html` 地址选择 chip、`index.html` 收藏按钮、导航 ❤ 徽章
- **任务 2（品牌页）**：`about.html`（Hero/故事/使命/探索/里程碑/价值观/社区订阅），导航「关于我们」+ 页脚「品牌故事 →」入口，中英双语
- **任务 3（订单需登录）**：checkout 已有 `get_current_customer` 401 保护；前端未登录引导登录
- **pymall.js v4 → v5**（新增 `already_reviewed`/`wishlist_*` 等翻译键，6 个 HTML 同步升级）
- **move-to-cart 挂收藏**：加入购物车后自动删除收藏项（前端列表同步过滤 + 徽章刷新）
- **测试**：TestReviews 全部用临时账号（`rv_` 前缀，conftest 清理）；TestWishlist 断言 move-to-cart 后收藏被移除；TestCatalogFilter 覆盖价格/排序/autocomplete——**93 passed**

### 测试账号
- 买家：`buyer@example.com` / `buyer123`
- 管理员：`admin` / `admin123`（走 `/api/admin/login`，admin_users 表）

## 重要提醒
- 改 share 层 JS 后，HTML 里 `pymall.js?v=N` 版本号要递增（浏览器 HTTP 缓存无 Cache-Control）
- 支付仅 mock 通道可用
- admin.html 使用旧版 `static/js/common.js`，与 Vue 页面隔离
- 测试评价污染：评价数据归属具体账号，测试用临时账号避免残留；buyer 账号的历史购买会影响「未购买不可评」的断言