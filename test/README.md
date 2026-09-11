# 测试目录

对 PyMall 电商独立站的全部接口与页面进行自动化测试。

## 环境要求

- 后端服务已启动：`uvicorn main:app`（默认 `127.0.0.1:8010`），见 `run.sh`
- 已安装 `requests` 与 `pytest`：
  ```bash
  .venv/bin/pip install -r requirements.txt requests pytest
  ```

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

### 页面（`test_pages.py`）

- `index.html`、`products.html`、`cart.html`、`orders.html`、`admin.html`：HTTP 200 + 关键内容
- 静态资源：`/static/css/jjshouse.css`、`/static/css/style.css`、`/static/js/pymall.js` 可访问
- 首页导航不再出现 `href="/admin.html"`（隐藏 admin 入口）

## 测试账号

- 买家：`buyer@example.com` / `buyer123`
- 管理员：`admin` / `admin123`

> 注意：测试会真实写入数据（注册新用户、创建商品、下单）。业务数据使用带时间戳的唯一标识，不与种子数据冲突。