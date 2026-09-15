# 订单管理后台：订单查询 · 状态流转 · 批量操作 · 接口文档

管理后台「订单管理」的完整说明。界面参考主流电商卖家后台（如拼多多商家后台）的
**单维度状态标签页 + 多字段搜索 + 订单卡片列表 + 批量工具栏**布局。

## 0. 与旧接口的关系

| 口径 | 路径 | 说明 |
|---|---|---|
| 旧（兼容保留） | `GET /api/admin/orders` | 返回 `list[OrderOut]`，仅支持 `status` 过滤 |
| 旧（兼容保留） | `POST /api/admin/orders/{no}/ship` | 无快递公司 / 单号入参 |
| **新（增强）** | `/api/admin/orders-search*` | 多字段筛选 + 分页 + 状态流转 + 批量 + 导出 |

后端实现位于 [`app/routers/order_admin.py`](../app/routers/order_admin.py)，
新接口**不改动**旧接口行为，前端已切到新接口，旧接口仅作兼容。

---

## 1. 订单状态（单维度标签页）

| tab | 中文 | 触发条件 |
|---|---|---|
| `all` | 全部订单 | — |
| `pending` | 待付款 | 下单未支付 |
| `paid` | 待发货 | 支付成功 |
| `shipped` | 待收货 | 商家已发货 |
| `completed` | 已完成 | 确认收货 / 后台确认完成 |
| `refunded` | 退款 / 售后 | 已支付被退款（库存回滚） |
| `cancelled` | 已取消 | 未支付被取消（释放锁定库存） |

标签页顺序与内容统一定义在 `app/models.py` 的 `ORDER_TABS`，后端 `TABS` 直接由它派生
（`[key for key, _ in ORDER_TABS]`），前端 `ORDER_TABS` 与之保持一致。

> ⚠️ 新增状态时必须同时补 `ORDER_TABS`，否则该标签页点击后返回
> 400「tab 必须是 …」（曾因漏 `cancelled` 导致「已取消」标签页报错）。
> 测试 `test_all_tabs_accepted` / `test_tabs_cover_all_order_statuses` 锁住该不变量。

中文标签统一定义在 `app/models.py` 的 `ORDER_STATUS_LABELS` / `order_status_label()`，
响应同时返回机器可读的 `status` 与展示用的 `status_label`，前端不做硬编码映射。

---

## 2. 接口清单

所有接口均需管理员 JWT；标注 🔒 的写操作仅 `superadmin` / `operator` 可调用（其他角色 403）。

### 2.1 状态计数（标签页角标）

```http
GET /api/admin/orders-status-counts
```

```jsonc
{
  "all": 269, "pending": 0, "paid": 0, "shipped": 3,
  "completed": 58, "refunded": 2, "cancelled": 206,
  "pending_ship_alert": 0
}
```

- `all` 恒等于各状态之和。
- `pending_ship_alert`：**已支付但超过 24 小时未发货**的订单数（阈值 `PENDING_SHIP_ALERT_HOURS`），
  前端在页面顶部渲染成橙色提醒条，点击直接跳到「待发货」标签页。

### 2.2 订单列表

```http
GET /api/admin/orders-search
```

| 参数 | 类型 | 说明 |
|---|---|---|
| `tab` | string | 状态标签页，默认 `all`；非法值 400（错误信息列出全部合法值）|
| `order_no` | string | 订单号；**支持逗号 / 空格分隔多值**（多值走精确 `IN`，单值走模糊） |
| `product_id` | string | 商品 ID（纯数字 → 经 SKU 关联精确匹配）或规格编码（非数字 → 模糊匹配）；支持多值 |
| `receiver` | string | 收件人姓名，模糊 |
| `phone` | string | 收件人手机号，模糊 |
| `tracking_no` | string | 快递单号，模糊 |
| `keyword` | string | 通用关键词：订单号 / 收件人 / 手机号 / 快递单号 任一命中 |
| `date_from` / `date_to` | string | 下单时间范围 `YYYY-MM-DD`（也接受 ISO 日期时间）；`date_to` 自动补到 23:59:59；解析失败**静默忽略**该条件 |
| `page` / `page_size` | int | 分页，默认 1 / 20，`page_size ≤ 200` |
| `limit` | int | `>0` 时忽略分页（兼容旧调用），`≤ 2000` |

返回 `list[AdminOrderOut]`，按 `Order.id` 倒序。

### 2.3 筛选总数

```http
GET /api/admin/orders-search-count   # 参数与列表完全一致 → {"total": 12}
```

> 列表与计数共用 `_apply_order_filters()`，保证「共查到 N 个订单」与列表条数**同口径**，
> 避免两个接口筛选条件漂移导致的分页错乱。

### 2.4 订单详情

```http
GET /api/admin/orders-search/{order_no}   # 404 订单不存在
```

```jsonc
{
  "order_no": "ORD20260914095559644456",
  "status": "shipped", "status_label": "待收货",
  "total_amount": "199.00", "goods_amount": "199.00",
  "item_count": 1, "total_quantity": 2,
  "receiver_name": "张三", "receiver_phone": "13800001111", "receiver_address": "…",
  "remark": "买家备注", "admin_note": "商家备注", "cancel_reason": null,
  "carrier": "中通快递", "tracking_no": "ZT999",
  "customer_id": 12, "customer_email": "buyer@example.com", "customer_name": "买家",
  "created_at": "2026-09-14T17:55:59", "paid_at": "2026-09-14T17:55:59",
  "shipped_at": "2026-09-14T19:31:13", "completed_at": null, "cancelled_at": null,
  "items": [{ "sku_id": 88, "sku_spec": {"颜色": "默认色"}, "quantity": 2, "…": "…" }],
  "payments": [{ "method": "mock", "method_label": "模拟支付",
                 "status": "success", "status_label": "支付成功", "…": "…" }]
}
```

**时间口径**：所有时间字段统一为**本地时间**（`YYYY-MM-DDTHH:MM:SS` 无时区）。
Python 侧写入的 `paid_at` / `shipped_at` / `completed_at` / `cancelled_at` 存的是 UTC-naive，
出参前经 `utc_to_local_naive()` 转换；`created_at` 由 DB `server_default=func.now()` 生成，
本身已是本地时间，直接透传。详见第 4 节。

**枚举中文化**：`payments[].method_label` / `status_label` 由后端字典给出
（`mock` → 模拟支付、`success` → 支付成功），前端不再显示英文枚举。

### 2.5 状态流转（写操作）

| 接口 | 说明 | 前置状态 | 失败 |
|---|---|---|---|
| 🔒 `POST /orders-search/{no}/ship` | 发货，body 可选 `{carrier, tracking_no}` | `paid` | 400（未支付 / 已发货 / 已完成 / 已取消 / 已退款）|
| 🔒 `POST /orders-search/{no}/note` | 商家备注，body `{note}`（≤500 字） | 任意 | 422 超长 |
| 🔒 `POST /orders-search/{no}/cancel` | 取消 / 退款，body `{reason, refund}` | `pending` / `paid` / `shipped` | 400（已完成 / 已取消或已退款 / **已支付未选退款**）|
| 🔒 `POST /orders-search/{no}/complete` | 确认完成 | `shipped` | 400（非待收货）|
| 🔒 `POST /orders-search/{no}/confirm-payment` | 线下收款，待付款 → 待发货 | `pending` | 400（非待付款）|

全部返回更新后的 `AdminOrderOut`。

**取消 / 退款的库存语义**（`cancel` 接口）：

| 原状态 | `refund` | 结果 | 库存 |
|---|---|---|---|
| `pending` | 任意 | → `cancelled` | 释放 `locked_stock`（`change_qty=0` 流水，`reason=order_cancel`）|
| `paid` / `shipped` | 必须 `true` | → `refunded`，支付记录置 `refunded` | `stock` 加回（`change_qty=+qty` 流水，`reason=order_refund`）|
| `paid` / `shipped` | `false` | 400「已支付订单请选择「退款」而非直接取消」 | 不变 |
| `completed` | — | 400「该订单已完成，不支持取消」 | 不变 |

### 2.6 批量操作

```http
POST /api/admin/orders-search/bulk      # 🔒
{ "order_nos": ["ORD…1", "ORD…2"], "action": "ship", "carrier": "圆通速递", "tracking_no": "" }
{ "order_nos": ["ORD…1"], "action": "note", "note": "批量备注：加急" }
```

- `order_nos`：≤200 条，自动 **去空格 / 去重**，空列表 422。
- `action`：仅 `ship` / `note`，其他值 **400**（在循环外校验）。
- 逐单处理，跳过项附原因，返回 `Message`：

```jsonc
{ "message": "成功 2 条，跳过 2 条；ORD_NOT_EXIST: 订单不存在；ORD…: 当前状态为「待付款」，不可发货" }
```

> 跳过原因最多展示 5 条，避免响应体过长。

### 2.7 导出 CSV

```http
GET /api/admin/orders-search-export    # 参数与列表一致，另有 limit（默认 5000，≤20000）
```

- 编码 **UTF-8 + BOM**，Excel 直接打开不乱码；`Content-Disposition: attachment; filename="orders_YYYYmmdd_HHMMSS.csv"`。
- 列：订单号 / 订单状态 / 商品总价 / 实收金额 / 商品件数 / 收货人 / 收件人手机号 /
  收货地址 / 快递公司 / 快递单号 / 买家邮箱 / 商品明细 / 商家备注 / 下单时间 / 发货时间。
- 商品明细格式：`商品名(规格编码)×数量`，多商品用 ` | ` 连接。

---

## 3. 前端页面

### 3.1 订单列表（`static/admin.html`，视图 key `orders`）

```
┌ 待发货提醒条（仅 pending_ship_alert > 0 时出现）
├ 状态标签页：全部(269) 待付款(0) 待发货(0) 待收货(3) 已完成(58) 已取消(206) 退款/售后(2)
├ 搜索区：订单号 / 商品ID / 收件人 / 手机号 / 快递单号 / 关键词
│   └ 「展开更多」：下单开始日期 / 结束日期 + 快捷区间（今天 / 近 7 天 / 近 30 天）
├ 批量工具栏：批量备注 / 批量发货 / 已选 N 项 / 清空选择 / 导出 CSV
└ 订单卡片列表 + 分页器
```

- 每张卡片：商品图 + 商品名 + 规格（颜色/尺码）+ 规格编码 + 单价 + ×数量、
  收件人 / 手机号 / 地址、买家、金额与商品总价、件数、买家备注 chip、
  物流（快递公司 + 单号）、按状态给出的操作按钮（发货 / 备注 / 取消 / 退款 / 完成 / 确认收款 / 查看详情）。
- 关键状态放在 `odState`（`tab / page / pageSize / total / counts / list / inited`），
  选中集合同步到 `odSelected`；列表与计数**并行请求**。
- `Esc` / 点击遮罩关闭弹窗；批量与单条写操作按钮有 **双重提交保护**。

### 3.2 订单详情（`static/admin-order-detail.html?order_no=…`）

- 顶部状态横幅（按状态配色）+ 订单号 + 订单金额。
- **订单进度 timeline**：下单成功 → 买家付款 → 商家发货 → 已完成，各节点显示对应时间，
  未发生的节点置灰；取消 / 退款订单高亮为危险色。
- 物流信息（快递公司 / 单号 + 一键复制）、收货信息（买家备注 / 商家备注 / 取消原因）、
  订单商品（含规格）、金额明细（商品小计 / 运费 / 优惠 / 实付款）、
  订单信息（买家、件数、各时间节点、支付单号、支付方式 · 支付状态）。
- 操作按钮按 `localStorage.admin_role` 与订单状态共同决定（`superadmin` / `operator` 才可见）。
- 数据优先取 `/api/admin/orders-search/{no}`，失败回退旧接口 `/api/admin/orders/{no}`。

### 3.3 状态文案口径（易错）

订单状态文案**必须取自后端 `status_label`**，不要用 `common.js` 的通用翻译表：

| status | 后端 `status_label`（订单域）| `App.t(status)`（通用表，❌ 不要用）|
|---|---|---|
| `pending` | 待付款 | 待支付 |
| `paid` | **待发货** | 已支付 |
| `shipped` | **待收货** | 已发货 |
| `refunded` | 退款/售后 | 已退款 |

> 历史问题：订单详情页页头用 `App.t(o.status)`，导致同一订单在列表页显示「待收货」、
> 在详情页显示「已发货」。已改为 `o.status_label || App.t(o.status)` 兜底。

### 3.4 页面内函数的导出约定

`static/admin.html` 里所有内联 `onclick` 都通过 `window.Admin.*` 调用，
**函数写在闭包里不等于能被点到**——新增前端函数必须同时加进 `window.Admin = {...}` 导出表。

> 历史问题：订单页头部的「订单操作记录」写的是 `Admin.orderOpenLog()`，
> 但导出表里只有 `odOpenLog` → 点击报 `Admin.orderOpenLog is not a function`。
> 现已两个名字都导出（`odOpenLog` 为规范名，`orderOpenLog` 为兼容别名）。

同理，批量绑定事件时输入框 id 必须与 HTML 完全一致：
订单页的快递单号输入框是 `of-tracking`，曾误绑为 `of-tracker`，
导致该输入框按回车不触发搜索。

### 3.5 深链（`?view=xxx`）

`admin.html?view=orders` 这类深链在**已登录**时由初始化逻辑生效；
未登录时点登录后也曾停在仪表盘，现已把 `switchView(initialView)` 补进 `login()` 的成功回调。

---

## 4. 时间口径约定（重要）

数据库 `orders.created_at` 由 `server_default=func.now()` 生成，取值是 **数据库本地时间**
（`Asia/Shanghai`）；而 Python 侧的历史写法是
`datetime.now(timezone.utc).replace(tzinfo=None)`，写进去的是 **UTC-naive**。

两者直接混排会导致同一订单出现「支付时间比下单时间早 8 小时」的错误展示。
解决方案：

```python
# app/models.py
def utc_to_local_naive(dt: datetime | None) -> datetime | None:
    """UTC-naive / aware → 本地 naive；None 透传"""
```

- 订单域的出参统一做 `utc_to_local_naive()` 转换（`order_admin._order_to_admin_out`、
  `orders._order_to_out`），**存量数据的语义保持不变**，只在读取时归一。
- 新增的订单写操作（发货 / 完成 / 取消 / 收款）继续沿用 UTC-naive 写入，交给出参转换。
- 新增时间字段时请遵循同一约定，不要在 API 层直接返回原始值。

---

## 5. 测试

对应测试类：`test/test_api.py::TestAdminOrderManagement`（31 例）。

覆盖：鉴权 401、非法 `tab` 400、**7 个标签页全部可用且数量之和 = 全部**、
状态计数口径与新增订单后的增量、
多字段筛选（订单号多值 / 收件人 / 手机号 / 快递单号 / 关键词 / 商品 ID / 规格编码 / 日期范围 / 非法日期忽略）、
分页与 `limit` 模式、列表与计数一致性、详情字段（含 `status_label` / `goods_amount` /
`total_quantity` / 买家邮箱 / 支付中文标签）、发货（成功 / 重复 400 / 未支付 400 / 404）、
快递单号与关键词回查、商家备注（写入 / 读取 / 清空 / 超长 422）、
取消未支付释放锁定库存、取消已支付需 `refund`（否则 400）并回滚库存、重复取消 400、
确认收款（成功 / 非待付款 400）、完成订单（前置状态校验 / 时间递增 / 完成后拒绝取消）、
批量发货与批量备注（去重 / 跳过原因 / 非法 action 400 / 空列表 422）、
CSV 导出（BOM / 表头 / 按筛选条件收窄）。

```bash
TEST_BASE_URL=http://127.0.0.1:8020 .venv/bin/python -m pytest test/test_api.py::TestAdminOrderManagement -q
```

> 测试创建的订单会在会话结束时由 `test/conftest.py` 的库存 / 订单快照回滚自动清理，
> 用例内部另有 `track` fixture 兜底取消，避免残留订单锁定库存。
