# PayPal 支付集成方案（调研 + 落地实现）

跨境收单通道的完整方案：**拿什么账号、放哪些环境变量、后端/前端怎么改、哪些坑要提前避开**。

> **当前状态：已实现并跑通沙箱建单**（`mock` 与 `paypal` 两通道同时可用）。
> 距上线还差 3 件事：① 在开发者后台订阅 webhook 并把 `PAYPAL_WEBHOOK_ID` 填进 `.env`；
> ② 把 `BASE_URL` 改成公网 HTTPS 域名；③ 换成 live 凭据（`PAYPAL_MODE=live`）。详见第 8 节。

---

## 0. 结论速览

| 决策点 | 结论 | 状态 |
|---|---|---|
| 集成方式 | **JS SDK Buttons + Orders v2**（create → approve → capture） | ✅ 已实现 |
| 账务落库时机 | **capture 成功后 + webhook `PAYMENT.CAPTURE.COMPLETED` 双保险** | ✅ 已实现 |
| 收单币种 | **USD**（订单仍按 CNY 记账，下单时快照汇率） | ✅ 已实现 |
| 商户凭据 | **全部放 `.env`** | ✅ 已配置（沙箱） |
| webhook | **必须接**（退款/拒付/掉单兜底） | 🟡 代码就绪，ID 已配、URL 已纠正，待部署 |
| 前端交互 | 四个付款页面统一 `PyMall.startPay()` + 共享支付弹窗 | ✅ 已实现 |

### 1.1 实测结果（沙箱）

| 环节 | 结果 |
|---|---|
| OAuth2 取 token | ✅ `api-m.sandbox.paypal.com` 200（live 域名 401，确认是沙箱凭据） |
| 建 PayPal 订单 | ✅ 真实返回 `order id`（如 `4G557551YX2385138`），金额 `USD 27.64`（¥199 ÷ 7.2） |
| 重复点「支付」 | ✅ 复用同一个 PayPal 订单（幂等） |
| 未获批准时 capture | ✅ 返回「买家尚未在 PayPal 完成付款授权」，非 500 |
| 弹窗渲染按钮 | ✅ PayPal / 借记卡或信用卡按钮正常渲染 |
| 验签链路（真 webhook_id + 假签名） | ✅ PayPal 回 `FAILURE` → 接口 400「签名校验未通过」，不改任何状态 |
| 事件结构比对 | ✅ 用 `POST /v1/notifications/simulate-event` 拿到的官方样例事件，结构与我们解析的 `resource.supplementary_data.related_ids.order_id` 一致 |
| webhook 落账/退款/失败 | ✅ 由 `test/test_paypal.py` 覆盖（含端点级闭环与幂等） |

> 唯一需要人工过一遍的环节：**用沙箱买家账号在 PayPal 页面点「批准」**，
> 这一步没法用脚本代替（需要登录 PayPal 买家）。步骤见第 7.2 节。
>
> ⚠️ **mock 事件（Webhooks simulator）不走 postback 验签，也不会进投递日志**
> （`GET /v1/notifications/webhooks-events/{id}` 对 mock 事件返回 404），
> 所以“PayPal → 我们的公网 URL”这一段只能在**部署后用真实付款**验证。

---

## 1. 现状盘点：项目已有的支付骨架

| 位置 | 现状 |
|---|---|
| `app/payments.py` | `BasePaymentGateway` 抽象（`create_payment` / `query_payment` / `handle_callback` / `refund`）+ `GATEWAYS` 注册表 + `MockGateway` 可用，`Alipay/Wechat/Stripe` 是占位实现 |
| `app/config.py` | `PAYMENT_GATEWAY_ENABLED` 字典控制通道开关；已新增 `PAYPAL_*` 配置项 |
| `POST /api/orders/checkout` | 校验支付方式 → 校验通道是否开通 → 建 `Payment`（`currency="CNY"`，`status=UNPAID`） |
| `POST /api/orders/{order_no}/pay` | 调 `gateway.create_payment()`，把返回的 `transaction_no` / `pay_url` 塞回 `Payment`，`status=PROCESSING` |
| `GET /api/payments/mock/confirm` | mock 收银台，直接 `_mark_payment_success()`（幂等 + 扣库存 + 加销量） |
| `POST /api/payments/callback` | 通用回调入口，`payload["method"]` 选网关 |
| 前端 4 处 `pay()` | `cart.html`、`order-detail.html`、`orders.html`、`account.html`：都假设 `pay_url` 是**同源相对地址**，`fetch(pay_url)` 当 GET 拿 JSON |

### 直接照搬会踩的 5 个坑

1. **`pay_url` 抽象不适用**。PayPal 没有「跳转到我们生成的 URL」这一步：要么前端渲染 PayPal 按钮（SDK 内嵌 iframe），要么跳转到 `paypal.com/checkoutnow?token=<paypal_order_id>`。4 处 `fetch(pay.pay_url)` 全部要改成「按 method 分支」。
2. **回调入口吃不下 PayPal 的请求**。`/api/payments/callback` 用 `await request.json()` 解析，而 webhook **验签必须用原始 raw body**（解析再序列化会签名失败），且要读 5 个 `paypal-*` 请求头。必须新开一个专用端点。
3. **`Payment` 表缺字段**。没有地方存 PayPal 的 `order_id`（下单时生成，形如 `5O190127TN364715T`）和 `capture_id`（退款要用它），也没有存汇率/手续费。
4. **金额口径**。站点按 CNY 定价，PayPal 要 USD，需要汇率快照；capture 时要拿回调金额和库里的 USD 金额比对，防篡改。
5. **`notify_url` 拼错了**。现在用 `request.url.scheme/netloc` 拼回调地址，反代（nginx → 8020）下会得到 `http://内网IP/...`；而 **PayPal 只向公网 HTTPS:443 投递 webhook**。应改用 `settings.BASE_URL`。

---

## 2. 开工前要准备的东西（账号与资质）

| 项 | 说明 |
|---|---|
| PayPal 企业账户 | 需营业执照 + 法人身份信息；中国区账户**只做跨境收单**，不能收境内人民币 |
| 提现/结算 | 账户内余额提现到国内银行账户，走 PayPal 官方提现通道（结汇由合作机构处理），到账周期与手续费以官方规则为准 |
| REST App | 开发者后台 → Apps & Credentials → 新建 App，得到 `client_id` + `client_secret`（sandbox 与 live 各一套） |
| Webhook 订阅 | 在同一 App 下订阅 `https://<你的域名>/api/payments/paypal/webhook`，记下 **webhook_id**（验签必需） |
| Sandbox 买家账号 | Developer Dashboard → Sandbox → Accounts，用于沙箱付款测试 |
| 费率 | 跨境费率显著高于国内通道（官方费率表为准），定价时要把费率、汇损、拒付风险算进成本 |

---

## 3. 集成方式选型

| 方案 | 做法 | 取舍 |
|---|---|---|
| **A. JS SDK Buttons（推荐）** | 前端加载 `sdk/js`，`createOrder` 调我们的 `pay` 接口，`onApprove` 调我们的 capture 接口 | 体验最好，无需处理卡数据；必须配 webhook |
| B. 纯跳转 redirect | 后端 create order 时带 `payment_source.paypal.experience_context.return_url`，用户回跳后 capture | 前端改动更小，但离开站内页面，转化略低 |
| C. Payment Links / 手工收款 | 后台生成收款链接 | 无法自动对账，不适合站内下单流程 |

选 **A**；若想先小步验证，可先用 B 打通链路再换 A。

---

## 4. 环境变量（全部放 `.env`）

`app/config.py` 已加好字段，`.env` 里已备好注释模板（取消注释即可）：

| 变量 | 示例 | 说明 | 敏感 |
|---|---|---|---|
| `PAYPAL_MODE` | `sandbox` \| `live` | 决定 API 域名与 JS SDK 域名 | 否 |
| `PAYPAL_CLIENT_ID` | `AX....` | REST App 的 client id；**会经 SDK 下发到浏览器**（PayPal 设计如此） | 半敏感 |
| `PAYPAL_CLIENT_SECRET` | `EK....` | 只用于服务端换 `access_token`，**绝不下发/打印** | **是** |
| `PAYPAL_WEBHOOK_ID` | `0NH55953DH663215D` | 订阅 webhook 时生成，验签必需；缺失就拒绝所有回调 | 是 |
| `PAYPAL_CURRENCY` | `USD` | 收单币种。**不能填 CNY**（见第 9 节） | 否 |
| `PAYPAL_FX_CNY_PER_USD` | `7.2` | 站点 CNY 价 → USD 的换算率 | 否 |

域名对照（代码里按 `PAYPAL_MODE` 二选一，不要硬编码）：

| 用途 | sandbox | live |
|---|---|---|
| REST API | `https://api-m.sandbox.paypal.com` | `https://api-m.paypal.com` |
| JS SDK | `https://www.sandbox.paypal.com/sdk/js` | `https://www.paypal.com/sdk/js` |

> ⚠️ 开通通道时要写**完整 JSON**：`PAYMENT_GATEWAY_ENABLED={"mock": true, ..., "paypal": true}`，
> 否则字典里其它通道会被整体覆盖掉（pydantic-settings 对 dict 类型是整体替换，不是合并）。
>
> 当前 `.env` 已填好沙箱凭据，并已打开开关：
> `PAYMENT_GATEWAY_ENABLED={"mock": true, "alipay": false, "wechat": false, "stripe": false, "paypal": true}`。

---

## 5. 后端实现（已完成）

| 文件 | 做了什么 |
|---|---|
| `app/config.py` | `PAYPAL_*` 配置项 + 派生属性 `paypal_live` / `paypal_api_base` / `paypal_sdk_host` / `paypal_configured` / `paypal_enabled` |
| `app/payments.py` | `PayPalGateway`（token 缓存/建单/capture/查询/退款）+ `cny_to_usd()` / `amount_str()` / `verify_paypal_webhook()` |
| `app/models.py` | `PaymentMethod.PAYPAL`；`Payment.provider_order_id` / `provider_capture_id` / `fx_rate` |
| `app/database.py` | 轻量迁移：补上述三列 + `ALTER TYPE payment_method ADD VALUE 'PAYPAL'` |
| `app/routers/orders.py` | 通道开关改为统一读 `PAYMENT_GATEWAY_ENABLED`；`pay` 接口新增 PayPal 分支（建单/复用）；`notify_url` 改用 `settings.BASE_URL` |
| `app/routers/payments.py` | `GET /api/payments/methods`、`POST /api/payments/paypal/capture`、`POST /api/payments/paypal/webhook` |
| `app/routers/order_admin.py` | `PAY_METHOD_LABELS` 加 PayPal；后台「取消并退款」先调 PayPal 退款，失败则中止（避免假退款） |
| `app/i18n.py` | `paypal` 文案 |

### 5.1 接口契约

| 接口 | 说明 |
|---|---|
| `GET /api/payments/methods` | `{enabled: {mock, alipay, wechat, stripe, paypal}, default}`；paypal 的 enabled 还要求凭据已配置 |
| `POST /api/orders/{no}/pay` | paypal 订单返回 `method/paypal_order_id/client_id/sdk_host/currency/amount/order_total/fx_rate/mode`（**不含任何密钥**），并保留 `pay_url` 作为兼容/兜底跳转地址 |
| `POST /api/payments/paypal/capture` | 需登录 + 订单归属校验；capture 后校验金额/币种与快照一致才落账 |
| `POST /api/payments/paypal/webhook` | raw body 验签 → 处理 `PAYMENT.CAPTURE.COMPLETED/DENIED/REVERSED/REFUNDED`、`CHECKOUT.ORDER.APPROVED`，全部幂等 |

### 5.2 幂等与一致性（实际做法）

- 建单/扣款/退款都带 `PayPal-Request-Id`；同一订单重复点「支付」**复用**已建的 PayPal 订单（`provider_order_id` 缓存 + 查询状态）；
- PayPal 侧已 capture（`ORDER_ALREADY_CAPTURED`）时自动回查摘回已有 capture 当成功，用户不会看到报错；
- 金额严格校验：capture 响应金额/币种必须等于下单快照（不等则**不落账** + error 日志转人工）；
- webhook 全部幂等：已 success 不重复扣库存，已 refunded 不重复回滚。

### 5.2 `app/payments.py`：新增 `PayPalGateway`

要点：**懒加载 access_token（缓存到过期前）**、所有请求带 `PayPal-Request-Id` 幂等头、金额用 Decimal + 字符串化。

```python
class PayPalGateway(BasePaymentGateway):
    name = "paypal"

    def __init__(self):
        s = settings
        if not (s.PAYPAL_CLIENT_ID and s.PAYPAL_CLIENT_SECRET):
            raise RuntimeError("PayPal 未配置，请在 .env 填写 PAYPAL_CLIENT_ID/SECRET")
        host = "api-m.sandbox.paypal.com" if s.PAYPAL_MODE == "sandbox" else "api-m.paypal.com"
        self.api = f"https://{host}"
        self._token, self._token_exp = "", 0.0

    async def _access_token(self) -> str:
        """OAuth2 client_credentials，提前 60s 续期；token 只存内存，不落库"""
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{self.api}/v1/oauth2/token",
                auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET),
                data={"grant_type": "client_credentials"},
            )
            r.raise_for_status()
            d = r.json()
        self._token, self._token_exp = d["access_token"], time.time() + d["expires_in"]
        return self._token

    async def create_payment(self, req: PaymentRequest) -> PaymentResult:
        """建 PayPal 订单，返回 order_id（存进 payment.transaction_no）。

        注意：amount 必须是「已快照汇率换算后的 USD」，由调用方算好传进来。
        """
        body = {
            "intent": "CAPTURE",
            "purchase_units": [{
                "reference_id": req.order_no,          # 对账锚点：我们的订单号
                "custom_id": req.order_no,
                "amount": {"currency_code": req.currency, "value": f"{req.amount:.2f}"},
            }],
        }
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                f"{self.api}/v2/checkout/orders", json=body,
                headers={
                    "Authorization": f"Bearer {await self._access_token()}",
                    "PayPal-Request-Id": f"create-{req.order_no}",   # 幂等：重复调用不会建两个订单
                },
            )
        if r.status_code >= 400:
            return PaymentResult(success=False, transaction_no="", error=r.text[:300])
        d = r.json()
        return PaymentResult(
            success=True,
            transaction_no=d["id"],
            pay_url=f"https://www.paypal.com/checkoutnow?token={d['id']}",  # 方案 B 备用的跳转地址
            provider_response=d,
        )

    async def capture_order(self, paypal_order_id: str, order_no: str) -> dict:
        """capture 时同样带幂等头：重复调用返回同一笔 capture，不会重复扣款"""
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                f"{self.api}/v2/checkout/orders/{paypal_order_id}/capture",
                headers={
                    "Authorization": f"Bearer {await self._access_token()}",
                    "PayPal-Request-Id": f"capture-{order_no}",
                },
            )
        return {"status_code": r.status_code, "body": r.json()}

    async def handle_callback(self, payload): ...   # 见 5.4：走验签接口，不信任明文
    async def refund(self, transaction_no, amount): ...
```

### 5.3 `app/routers/orders.py`：`pay` 接口分支

- 把「占位通道」硬编码元组 `("alipay", "wechat", "stripe")` 改成**由 `PAYMENT_GATEWAY_ENABLED` 统一判断**（否则新增通道会绕过开关）。
- `method == "paypal"` 时：
  1. 用快照汇率把 `order.total_amount`（CNY）换成 USD：`usd = (total / settings.PAYPAL_FX_CNY_PER_USD).quantize(Decimal("0.01"))`，至少 `0.01`；
  2. `Payment.currency = "USD"`，把 `{"fx_cny_per_usd": ..., "cny_amount": ...}` 写进 `gateway_response`；
  3. 调 `create_payment()` 拿 `paypal_order_id`；
  4. 返回体给前端 **`client_id` + `currency` + `paypal_order_id`**（而不是 `pay_url`）。
- `notify_url` 改用 `settings.BASE_URL`，别用 `request.url.netloc`。

### 5.4 `app/routers/payments.py`：新增两个端点

| 端点 | 作用 | 要点 |
|---|---|---|
| `POST /api/payments/paypal/capture` | 前端 `onApprove` 调用，服务端 capture | 校验订单归属 + 状态为 `PROCESSING`；capture 返回的 `amount` 必须等于库里快照的 USD 金额；成功后 `_mark_payment_success()`（函数本身已按 `status==SUCCESS` 幂等） |
| `POST /api/payments/paypal/webhook` | 收 PayPal 事件 | **读 raw body** 做验签；验签失败返回 4xx 且**不做任何落账**；成功必须返回 `200` |

验签（推荐「自校验」，不额外依赖 PayPal 接口；官方也提供 postback 方式）：

```
原始签名串 = f"{paypal-transmission-id}|{paypal-transmission-time}|{PAYPAL_WEBHOOK_ID}|{crc32(raw_body)}"
用 paypal-cert-url 下载的证书公钥（缓存）验证 paypal-transmission-sig（SHA256withRSA）
```

要处理的事件：

| 事件 | 处理 |
|---|---|
| `CHECKOUT.ORDER.APPROVED` | 记录即可（用户已批准，等 capture） |
| `PAYMENT.CAPTURE.COMPLETED` | 兜底落账（前端没回调成功时靠它） |
| `PAYMENT.CAPTURE.DENIED` / `PAYMENT.CAPTURE.REVERSED` | `Payment.status = FAILED`，订单回滚到可重新支付 |
| `PAYMENT.CAPTURE.REFUNDED` | 标记 `REFUNDED` + 库存回滚（复用现有退款口径） |

> ⚠️ webhook 必须**幂等 + 去重**：PayPal 在非 2xx 时会重投 25 次 / 3 天。建议加一张
> `paypal_webhook_events(event_id unique)` 表，或至少用 `payment.status` 做幂等短路。
> ⚠️ 沙箱的 mock event（Webhooks simulator）**不支持 postback 验签**，其 webhook_id 固定为字符串 `WEBHOOK_ID`，
> 自校验方式才可用。

### 5.5 `app/models.py`：`Payment` 加字段（配 alembic 迁移）

| 列 | 类型 | 用途 |
|---|---|---|
| `provider_order_id` | `String(64)` | PayPal 订单号（`create` 返回，capture 用它） |
| `provider_capture_id` | `String(64)` | capture id，退款走 `POST /v2/payments/captures/{id}/refund` |
| `fx_rate` | `Numeric(10, 4)` | 下单时快照的汇率，便于对账复算 |

`PaymentMethod` 枚举要加 `PAYPAL = "paypal"`（PostgreSQL enum 需 `ALTER TYPE ... ADD VALUE`，alembic 里手写）。

### 5.6 后台与对账

- `app/routers/order_admin.py` 的 `PAY_METHOD_LABELS` 加 `"paypal": "PayPal"`；
- 退款接口按 method 分流：mock 直接返回成功，paypal 调 `refund()`；
- 建议每天拉一次 `GET /v2/transaction-search` 或对账 API 与本地 `Payment` 比对，防止掉单。

---

## 6. 前端实现（已完成）

| 位置 | 做了什么 |
|---|---|
| `pymall.js` `startPay(orderNo, {onPaid})` | 四个付款页面共用：mock 走「服务端 `pay_url` 直接确认」；paypal 开支付弹窗 |
| `pymall.js` `PayDialog` 组件 | 共享弹窗：订单号 / CNY 金额 / 实际扣款 USD + 汇率提示 / 沙箱提示 / PayPal 按钮容器；`createOrder` → `/pay`，`onApprove` → `/capture` |
| `pymall.js` `loadPayPalSdk()` | 按需插入 `<script src="{sdk_host}/sdk/js?...">`，按 client-id 缓存，失败可重试 |
| `cart.html` | 支付方式列表改为**后端下发**（`/api/payments/methods`）+ 未开通通道置灰不可点；结算后走 `startPay` |
| `orders.html` / `order-detail.html` / `account.html` | 支付按钮改调 `PyMall.startPay`，并挂上 `<pay-dialog>` |
| `jjshouse.css` | 新增 `.pay-modal` / `.pay-line` / `.pay-usd` / `.pay-sandbox` / `.pay-method.disabled` 等样式 |
| i18n | 新增 `paypal`、`pay_title`、`pay_fx_tip`、`pay_sandbox_tip`、`pay_success/cancelled/failed` 等中英文案 |

> 前端只拿得到 `client_id` 与 `sdk_host`；`client_secret` / `webhook_id` 全程在服务端。
> 测试 `test_frontend_never_contains_client_secret` 守着这条线。
>
> ⚠️ 容易踩的坑：模板 `ref="box"` 对应的 ref 必须在 `setup()` 里 **return**，
> 否则 Vue 拿不到 DOM，PayPal 会报 `Expected element to be passed to render iframe`。

---

## 7. 测试方案

### 7.1 自动化（已入库）

`test/test_paypal.py` 共 19 个用例，分三层：

| 层次 | 覆盖点 |
|---|---|
| 纯函数 | CNY→USD 换算（199→27.64、下限 0.01）、汇率配错不退化为 0、金额两位小数 |
| HTTP 拒绝路径 | 未验签 webhook → 400；mock 订单调 capture → 400；未建单 → 400；未登录 → 401/403；订单不存在 → 404；`client_secret` 不入前端资源 |
| 落账逻辑 | `PAYMENT.CAPTURE.COMPLETED` → 订单 paid + 扣库存 + 释放锁定（重投不重复扣）；`REFUNDED` → 退款 + 库存回原值（重复不重复加）；`DENIED` → failed；未知订单事件返回 False 而非报错 |
| 验签函数 | 打桩 httpx：SUCCESS 放行、FAILURE / 非 200 拒绝、缺签名头不发请求、缺 `PAYPAL_WEBHOOK_ID` 拒绝；**断言回传 body 里的事件原文与收到的一模一样** |
| 端点级闭环 | 打桩验签为通过 → 真读 raw body → 解析 → 落账（端点内除验签外都是线上代码） |

> 落账测试直接调 `_handle_paypal_event`：签名只能由 PayPal 生成，所以绕过验签，
> 但走的是线上完全相同的处理代码。用一次性 `NullPool` 引擎避免与 pytest 夹具的
> 事件循环冲突（asyncpg 连接绑定创建它的 loop）。

### 7.2 人工验证（沙箱，1 分钟）

1. 拿到一个待支付订单：商品页加入购物车 → 结算，支付方式选 PayPal；
2. 点「立即支付」→ 弹窗里点 PayPal 按钮；
3. 用**沙箱买家账号**登录（Developer Dashboard → Sandbox → Accounts 里的 Personal 账号）；
4. 批准付款 → 回到站点弹窗自动 capture → 订单变「已支付」，后台订单列表支付方式显示 PayPal。

验证边界情况可用 Webhooks simulator 造事件（注意：mock 事件不支持 postback 验签，
自校验方式才可用，其 webhook_id 固定为字符串 `WEBHOOK_ID`）。

---

## 8. 完成情况与上线待办

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 网关 + `pay` 分支 + `/capture` | ✅ 沙箱已跑通建单/幂等/错误处理 |
| P1 | webhook 端点 + 验签 + 事件处理（幂等） | ✅ 代码与测试就绪，待订阅 webhook |
| P2 | 前端统一 `startPay` + 支付弹窗 + 通道开关下发 | ✅ 已实现 |
| P3 | 后台退款分流 + 对账 | 🟡 退款分流已做；对账脚本未做 |

### 上线 checklist

1. **订阅 webhook**（✅ 已做，但 URL 必须写全路径）：

   ```
   ✅ https://<你的域名>/api/payments/paypal/webhook
   ❌ https://<你的域名>/paypal/webhook          ← 漏了 /api/payments/paypal 前缀，会 404/405
   ```

   ⚠️ 踩过的坑：订阅时只写了 `/paypal/webhook`，PayPal 会一直投递到 404 的地址
   （非 2xx 会重投 25 次 / 3 天），什么都不会落库。改 URL 可用 Webhooks API：

   ```bash
   curl -X PATCH https://api-m.sandbox.paypal.com/v1/notifications/webhooks/$WEBHOOK_ID \
     -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
     -d '[{"op":"replace","path":"/url","value":"https://<域名>/api/payments/paypal/webhook"}]'
   ```

   订阅事件建议至少包含：`PAYMENT.CAPTURE.COMPLETED` / `.DENIED` / `.DECLINED` / `.REVERSED` /
   `.REFUNDED`、`CHECKOUT.ORDER.APPROVED`（当前该 App 已订阅 `*`，覆盖得到）。
2. **先把新代码部署到该域名**：部署前 `/api/payments/methods` 是 404（新路由不存在），
   PayPal 投递只会拿到 405（静态文件挂载对 POST 的默认响应），webhook 等于空转；
3. **配 `BASE_URL`**：改成公网 HTTPS 域名（反代下 `request.url.netloc` 是内网地址）；
4. **切 live**：`PAYPAL_MODE=live` + 换 live 的 `CLIENT_ID/SECRET`；
5. **部署后人工跑一遍**（7.2 节）：真实小额付款 + 后台「取消并退款」验证钱真的退回去了；
6. 建议补：每日拉一次 PayPal 交易报表与 `Payment` 表对账（防掉单），告警接 `logger.error`。

---

## 9. 风险与注意事项

1. **币种**：官方文档明确 CNY「仅作为境内账户的支付货币与余额货币」（原表注 3）。中国区企业账户做跨境收款请用 USD 等外币，扣款币种与结算币种不一致时还有换汇成本。
2. **汇率**：用固定汇率（`PAYPAL_FX_CNY_PER_USD`）简单但会随行情偏离，建议定期维护，或改成定时任务拉取央行/第三方汇率写进配置缓存；**汇率必须在下单时快照**，回调时不再换算。
3. **金额精度**：PayPal 金额字符串最多两位小数，且部分币种（JPY/HUF/TWD）不支持小数——若以后加币种要单独处理。
4. **webhook 网络要求**：只投递到公网 **HTTPS 443**；nginx 反代要放行该路径，且**不要**对请求体做变形（gzip/重写都会破坏验签）。
5. **拒付与争议**：PayPal 买家保护较强，需准备发货凭证（快递单号，可用 `POST /v2/checkout/orders/{id}/track` 回填）与客服流程。
6. **合规**：跨境收款涉及外汇与税务口径，收入确认、结汇凭证要与财务对齐。

---

## 10. 相关代码位置

| 文件 | 说明 |
|---|---|
| [`app/config.py`](../app/config.py) | `PAYPAL_*` 配置项（已加） |
| [`.env`](../.env) | 商户凭据填写处（已 gitignore，内含注释模板） |
| [`app/payments.py`](../app/payments.py) | 网关抽象与注册表（`PayPalGateway` 待加） |
| [`app/routers/orders.py`](../app/routers/orders.py) | `POST /api/orders/{order_no}/pay` |
| [`app/routers/payments.py`](../app/routers/payments.py) | `/api/payments/*` 回调与查询 |
