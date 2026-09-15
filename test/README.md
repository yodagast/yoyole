# 测试目录

对 PyMall 电商独立站的全部接口与页面进行自动化测试。

## 环境要求

- 后端服务已启动：`./run.sh start`（默认 `127.0.0.1:8020`）
- 已安装 `requests` 与 `pytest`：
  ```bash
  .venv/bin/python -m pip install -r requirements.txt requests pytest
  ```

> 测试默认访问 `http://127.0.0.1:8010`。若服务跑在其他端口（如 `run.sh` 默认的 8020），
> 用环境变量覆盖：
> ```bash
> TEST_BASE_URL=http://127.0.0.1:8020 .venv/bin/python -m pytest test/ -v
> ```

## 运行全部测试

```bash
.venv/bin/python -m pytest test/ -v
```

## 分模块运行

| 命令 | 覆盖范围 |
|---|---|
| `.venv/bin/python -m pytest test/test_api.py -v` | 全部后端 API 接口 |
| `.venv/bin/python -m pytest test/test_pages.py -v` | 全部前端页面与静态资源 |

## 测试覆盖清单

### API（`test_api.py`）

- **公共**：`GET /api/health`、`GET /api/categories`、`GET /api/products`（列表/分页/推荐/分类过滤/中英文搜索）、`GET /api/products/{id}`（详情/404）
- **认证**：`POST /api/auth/register`（成功/重复邮箱）、`POST /api/auth/login`（成功/密码错/用户不存在）、`GET /api/auth/me`（登录/未登录）
- **购物车**：`GET /api/cart`（未登录 401）、`POST /api/cart/items`（加购/数量非法 422/SKU 不存在 404）、`PUT /api/cart/items/{id}`（改数量/库存超限/不存在）、`DELETE /api/cart/items/{id}`、`DELETE /api/cart`
- **订单**：`POST /api/orders/checkout`（空购物车/未开通支付方式/缺字段）、`GET /api/orders`、`GET /api/orders/{order_no}`、`POST /api/orders/{order_no}/pay` → `GET /api/payments/mock/confirm` → 后台发货 → 客户确认收货 全流程、`POST /api/orders/{order_no}/cancel`、订单不存在 404
- **后台**：`POST /api/admin/login`（成功/密码错）、`GET /api/admin/dashboard`、`GET /api/admin/products`、`POST /api/admin/products`（创建商品+SKU/缺字段 422）、`POST /api/admin/products/{id}/skus`、`PUT /api/admin/skus/{id}/stock`、`GET /api/admin/orders`（全部/按状态）、`POST /api/admin/orders/{no}/ship`（成功/404）、`GET /api/admin/categories`、`POST /api/admin/categories`、`GET /api/admin/stock-movements`、未登录 401、客户 token 拒访后台
- **商品批量导入**：`GET /api/admin/import/format`（权限/规则）、`POST /api/admin/import/products`（未登录 401、非 pptx 400、mode 非法 400、`dry_run` 预览解析且不写库）
  - 测试用最小 pptx 由 `python-pptx` 现场生成，无需外部素材
  - 详见 [`docs/product-import.md`](../docs/product-import.md)
- **商品审核 / 批量管理 / 审计**：`GET /api/admin/products-review-stats`、`POST /api/admin/products/{id}/review`（通过/驳回/重置、驳回缺理由 400、404）、`POST /api/admin/products/bulk`（上下架/审核/改分类/推荐/回收站/彻底删除、参数校验 400/422）、`GET /api/admin/product-audit-logs`（过滤/401）、`GET /api/admin/products/{id}/audit-logs`、`GET /api/admin/api-docs/products`、列表筛选、只读角色拒写 403、未过审商品前台隐藏
  - 写操作均作用于测试自建商品，不污染真实数据
  - 详见 [`docs/product-admin.md`](../docs/product-admin.md)
- **商品生命周期（PDD 风格）**：`GET /api/admin/products-status-counts`（7 种状态的标签角标）、`GET /api/admin/products?tab=…&page=…&page_size=…`（状态标签页 + 分页 + 多字段搜索：`product_id` 多值 / `sku_code`）、`GET /api/admin/products/{id}/publish-check`（上架体检）、`POST …/publish`、`POST …/unpublish`、`DELETE /api/admin/products/{id}`（软删除）、`POST …/restore`、`DELETE …/purge`（需先入回收站）、`PUT …/price`、`PUT …/stock`（总库存均摊语义）
  - 覆盖状态派生（在售中 / 已售罄）、上架阻止项、回收站可见性、边界校验
- **操作日志展示**：`products/{id}/audit-logs` 的 `action_label` / `tone` / `summary` 派生字段（中文动作、徽标配色、人话摘要不裸露 JSON）、`limit`/`offset` 分页不重不漏、`action` 过滤、`product-audit-logs/meta` 的总数与各动作数量（动作计数不随动筛选收窄）
- **订单查询（增强）**：`GET /api/admin/orders-status-counts`（7 状态角标 + 待发货超时提醒，`all` = 各状态之和）、`GET /api/admin/orders-search`（`tab` + `order_no` 多值 + `product_id`（商品ID/规格编码）+ `receiver`/`phone`/`tracking_no`/`keyword` + 日期范围 + 分页/`limit`）、`GET /api/admin/orders-search-count`（与列表**同口径**）、`GET /api/admin/orders-search/{no}`（`status_label`/`goods_amount`/`total_quantity`/买家邮箱/支付中文标签、时间递增）、`POST …/ship`（成功/重复 400/未支付 400/404）、`POST …/note`（写入/读取/清空/超长 422）、`POST …/cancel`（未支付释放锁定库存；已支付未选退款 400；`refund=true` 回滚库存并置「退款/售后」；重复取消 400）、`POST …/complete`（前置状态校验/完成后拒绝取消）、`POST …/confirm-payment`、`POST …/bulk`（`ship`/`note`、去重、跳过原因、非法 action 400、空列表 422）、`GET /orders-search-export`（UTF-8 BOM、表头、按筛选收窄）、未登录 401、非法 `tab` 400、**7 个标签页全部可用（`cancelled` 回归）且数量之和 = `all`**
  - 用例内部 `track` fixture 兜底取消测试订单，不残留锁定库存
  - 详见 [`docs/order-admin.md`](../docs/order-admin.md)
- **收藏数 / 批量下架原因**：`GET /api/admin/products` 返回 `favorite_count`（聚合 `wishlist_items`，曾恒为 0）、`POST /api/admin/products/bulk` 的 `status` 动作写入并清空 `off_shelf_reason`
- **类目管理（独立编辑页）**：`GET /api/admin/categories/{id}` 单条详情、列表与详情的派生统计（`product_count` / `active_product_count` / `children_count` / `parent_name`）、扁平字段（`name_zh`/`name_en`）与 `name_i18n` 双协议、编码小写规整、重复编码 409、父类目关系与**防环**（不能选自己或自己的后代）、**删除约束**（有关联商品 409 / 有子类目 409）
  - 详见 [`docs/product-admin.md`](../docs/product-admin.md) 第 0.4 节
- **商品包导出 / 导入（文件夹形式，不涉及 PPT）**：`POST /api/admin/product-package/export`（生成 `manifest.json` + `categories.json` + `images/` + `README.txt`，图片相对化、内容哈希去重、不含审核/上下架状态）、`POST …/export-zip`（直接返回 zip 流）、`GET …/exports`（**同名目录与 zip 合并为一行**）、`DELETE …/exports/{name}`、`POST …/import`（`merge` 跳过已存在 / `update` 覆盖并停用包内未出现的旧 SKU）、`GET …/format`
  - 容错与安全：缺 manifest / 格式不符 / 版本过高 / 空 products / 路径穿越 / 非 zip / 伪 zip → 400；SKU 被其他商品占用只跳过该规格；单商品失败走 savepoint 只回滚自己；缺图记 warning 不失败；zip 外层多套一层目录可识别
  - 详见 [`docs/product-package.md`](../docs/product-package.md)

### 页面（`test_pages.py`）

- `index.html`、`products.html`、`cart.html`、`orders.html`、`admin.html`：HTTP 200 + 关键内容
- 静态资源：`/static/css/jjshouse.css`、`/static/css/style.css`、`/static/js/pymall.js` 可访问
- 首页导航不再出现 `href="/admin.html"`（隐藏 admin 入口）

## 测试账号

- 买家：`buyer@example.com` / `buyer123`
- 管理员：`admin` / `admin123`

> 注意：测试会真实写入数据（注册新用户、创建商品、下单）。业务数据使用带时间戳的唯一标识，不与种子数据冲突。