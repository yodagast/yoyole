# 微信支付接入（APIv3：PC 扫码 Native + 手机 H5）

国内收款通道的完整方案：**要准备什么资质、放哪些环境变量、代码怎么改、哪些坑必须避开**。
本文记录的是**已实现**的状态（与 `docs/paypal.md` 同一套网关抽象，两条通道可并存）。

> **当前状态**：代码与测试全部就绪（`test/test_wechatpay.py` 23 个用例通过），
> 但 `.env` 里还没有真实商户凭据，所以**微信暂未出现在支付方式里**（这是有意设计：
> 凭据不齐时不给用户看到这个选项，避免下单后付不了款）。
> 补上凭据后按第 6 节验证即可上线。

---

## 0. 结论速览

| 决策点 | 结论 | 理由 |
|---|---|---|
| 收款形态 | **PC 用 Native 扫码 + 手机浏览器用 H5**，按 User-Agent 自动切换 | `code_url`（Native）只能在扫码场景用；H5 的 `h5_url` 只能在手机浏览器拉起微信。两者都不用公众号授权，**不需要用户 openid**（JSAPI 才需要） |
| 是否要实现 JSAPI | 本项目**不做** | JSAPI 需要公众号 + 网页授权拿 openid + 配置支付授权目录，属于另一个量级；微信内直接打开站点时走 H5/引导跳浏览器更简单 |
| 协议实现 | **手写 APIv3 最小集合**（`app/wechatpay.py`） | 官方只提供 Java/PHP/Go SDK，Python 侧无官方库；只实现本项目用到的接口，避免引入第三方依赖 |
| 落账时机 | **回调 + 前端轮询查单 双保险** | 微信明确要求「不能只依赖回调，要结合查单接口」；回调延迟/丢失时靠轮询兜底 |
| 密钥管理 | 私钥/公钥**放文件**（`cert/`，已 gitignore），也支持环境变量内容 | 环境变量会出现在进程列表与容器配置里；文件方式更安全，且平台证书轮换时换文件即可 |
| 退款 | 异步受理 + 退款结果回调 | 微信退款接口只表示「受理成功」，最终结果由 `refund-notify` 同步 |

---

## 1. 两种收款形态怎么选（自动）

后端按 `User-Agent` 判断（`app/routers/orders.py::_is_mobile_ua`）：

| 客户端 | 接口 | 返回 | 前端表现 |
|---|---|---|---|
| PC 浏览器 | `POST /v3/pay/transactions/native` | `code_url`（`weixin://...`） | 弹窗里**本地渲染二维码**，用户微信扫一扫；前端每 3 秒查单 |
| 手机浏览器 | `POST /v3/pay/transactions/h5` | `h5_url` | 直接 `location.href` 跳转拉起微信，付完回浏览器 |

> 二维码是本地用 `static/js/qrcode.min.js`（qrcodejs 1.0.0，19KB，已随站点托管）
> 画的，**不依赖任何外部 CDN**，也没有用「第三方二维码 API」那种把订单信息发出去的做法。

---

## 2. 开工前要在商户平台准备 5 样东西

| # | 项 | 去哪拿 | 对应配置 |
|---|---|---|---|
| 1 | **商户号 mchid**（10 位） | 商户平台 → 账户中心 → 商户信息 | `WECHATPAY_MCHID` |
| 2 | **appid**（与 mchid 已绑定） | 商户平台 → 产品中心 → AppID 账号管理；公众号/开放平台/小程序任意一个 | `WECHATPAY_APPID` |
| 3 | **APIv3 密钥**（32 位） | 商户平台 → 账户中心 → API 安全 → 设置 APIv3 密钥 | `WECHATPAY_API_V3_KEY` |
| 4 | **商户 API 证书**（含序列号） | 同上 → 申请 API 证书，下载后解压得 `apiclient_key.pem`，序列号在页面显示 | `WECHATPAY_PRIVATE_KEY_PATH` + `WECHATPAY_CERT_SERIAL_NO` |
| 5 | **微信支付公钥**（推荐）或平台证书 | 同上 → 申请公钥（一次申请长期有效；平台证书 5 年一换） | `WECHATPAY_PUBLIC_KEY_PATH` / `..._PEM` + `WECHATPAY_PUBLIC_KEY_ID` |

⚠️ 解压证书包得到的文件名是 **`wxpay_pub_key.pem`**（不是 `wxpay_public_key.pem`），
配置 `.env` 时要以实际文件名为准，否则验签时找不到公钥。
证书序列号可从 `apiclient_cert.pem` 读出核对：

```bash
.venv/bin/python -c "from cryptography import x509;\
print(format(x509.load_pem_x509_certificate(open('cert/1750784880_20260919_cert/apiclient_cert.pem','rb').read()).serial_number,'X'))"
# 应与 .env 的 WECHATPAY_CERT_SERIAL_NO 完全一致
```

另外要开通产品权限：**Native 支付**与 **H5 支付**（商户平台 → 产品中心 → 申请）。
H5 支付还需要配置 **H5 支付域名**（必须是备案过的域名）。

⚠️ 第 3 项丢失去只能重设，重设瞬间旧密钥失效 → 回调解密会全部失败，建议双人保管。

---

## 3. 环境变量（全部放 `.env`）

模板见 `.env.wechat.example`（不含任何真实密钥）。核心项：

```dotenv
WECHATPAY_MCHID=1900000109
WECHATPAY_APPID=wx8888888888888888
WECHATPAY_API_V3_KEY=<32 位>
WECHATPAY_CERT_SERIAL_NO=<40 位十六进制>
WECHATPAY_PRIVATE_KEY_PATH=cert/apiclient_key.pem
WECHATPAY_PUBLIC_KEY_PATH=cert/wxpay_public_key.pem
WECHATPAY_PUBLIC_KEY_ID=PUB_KEY_ID_0000000000000024101100397200000006
PAYMENT_GATEWAY_ENABLED={"mock": true, "alipay": false, "wechat": true, "stripe": false, "paypal": true}
```

两种提供密钥的方式（二选一，优先级：环境变量内容 > 文件）：

| 方式 | 配置 | 适用 |
|---|---|---|
| **文件**（推荐） | `cert/apiclient_key.pem`、`cert/wxpay_public_key.pem` | 常规部署；`cert/` 已在 `.gitignore` |
| 环境变量内容 | `WECHATPAY_PRIVATE_KEY_PEM` / `WECHATPAY_PUBLIC_KEY_PEM`（PEM 全文，换行写 `\n`） | 不能挂文件的环境 |

用「平台证书」而非公钥时：把证书按**序列号**命名放进 `cert/platform/<序列号>.pem`，
代码会按回调头里的 `Wechatpay-Serial` 自动挑对应的证书（平台证书可同时存在多张）。

> ⚠️ 覆盖 `PAYMENT_GATEWAY_ENABLED` 必须写**完整 JSON**（pydantic-settings 对 dict 是整体替换），
> 否则会把 PayPal 的开关一起冲掉。

---

## 4. 后端实现（已完成）

| 文件 | 内容 |
|---|---|
| `app/wechatpay.py` | **协议层**：金额换算（元↔分）、请求签名串 + `Authorization` 构造、平台侧验签、回调 AES-256-GCM 解密 |
| `app/payments.py` | `WechatGateway`：Native/H5 下单、查单、退款、（回调走专用端点） |
| `app/config.py` | `WECHATPAY_*` 配置 + 派生属性 `wechatpay_configured / wechatpay_private_key / wechatpay_public_key_for(serial)` |
| `app/routers/orders.py` | `pay` 接口的微信分支（按 UA 选形态、复用未支付二维码、商品描述用真实商品名） |
| `app/routers/payments.py` | `POST /api/payments/wechat/notify`（回调）、`GET /api/payments/wechat/status/{order_no}`（查单）、`POST /api/payments/wechat/refund-notify`（退款结果） |
| `app/routers/order_admin.py` | 后台「取消并退款」按通道分流：微信按商户订单号退，PayPal 按 capture id 退 |

### 4.1 接口契约

| 接口 | 说明 |
|---|---|
| `POST /api/orders/{no}/pay` | 微信订单返回 `method/channel/code_url|h5_url/amount`（`code_url` 是要画二维码的 `weixin://` 串，不是跳转地址） |
| `POST /api/payments/wechat/notify` | 微信回调：验签 → 解密 → 金额校验 → 落账；返回 `{"code":"SUCCESS"}` |
| `GET /api/payments/wechat/status/{order_no}` | 查单（前端轮询用）；查得已支付就**复用回调同一段落账逻辑** |
| `POST /api/payments/wechat/refund-notify` | 退款结果；`ABNORMAL` 会打 error 日志提示人工处理 |

### 4.2 三个协议要点（写错就 401 / 验签失败）

1. **请求签名串**：`方法\nURL(含 query)\n时间戳\n随机串\n报文主体\n` —— 查单/关单的 query 必须带上，
   漏了直接 `401 SIGN_ERROR`（这是最常见的坑）。
2. **平台侧验签串**：`时间戳\n随机串\n原始报文\n` —— 报文必须是**原始字节**，
   解析成对象再序列化会验签失败。
3. **回调解密**：`AEAD_AES_256_GCM`，密钥是 32 位 APIv3 密钥，
   `nonce` 与 `associated_data` 取自 `resource`；密文是 `base64(密文 + 16 字节 auth tag)`。

> 微信还会**故意**在极少数应答/回调里发错误签名（`WECHATPAY/SIGNTEST/` 前缀）来探测商户
> 是否真的验签 —— 所以验签失败必须拒绝，不能"忽略错误继续处理"。

### 4.3 幂等与一致性

- 同一订单重复点「支付」：微信侧仍是未支付时**复用原二维码**（避免堆废单）；
  终端形态变了（PC→手机）才重新下单。
- 落账幂等：`payment.status == SUCCESS` 直接短路（回调重投、轮询先到都不会重复扣库存）。
- 金额校验：回调/查单金额必须等于订单金额，不等则**不落账**并打 error 日志转人工。
- 回调业务异常**不抛 5xx**：微信要求 5 秒内应答，否则按 15s/15s/30s/… 重试 15 次；
  所以处理失败也返回 200（幂等 + 查单兜底能自愈）。

---

## 5. 前端实现（已完成）

| 位置 | 内容 |
|---|---|
| `pymall.js` `startPay()` | 按 `method` 分发：`wechat` + `channel=h5` 直接跳转；`wechat` + `native` 开二维码弹窗 |
| `pymall.js` `PayDialog` | 一个弹窗两种形态：PayPal 渲染 SDK 按钮，微信本地画二维码 + **每 3 秒轮询查单**（最多 30 分钟）；查到已支付自动收尾并提示 |
| `pymall.js` `loadQrcodeLib()` | 按需加载 `/static/js/qrcode.min.js`（本地托管，不依赖 CDN） |
| `cart.html` | 支付方式列表加微信（顺序：PayPal → 微信 → 模拟支付 → …），开关仍由 `/api/payments/methods` 下发 |
| `jjshouse.css` | `.pay-qr` / `.pay-polling` 等样式 |

---

## 6. 测试

### 6.1 自动化（已入库）

`test/test_wechatpay.py` 共 23 个用例：

| 层次 | 覆盖点 |
|---|---|
| 纯协议 | 金额换算（199.995 → 20000 分）、请求/应答签名串**逐字节格式**、RSA 签名验签往返、篡改报文必须失败、X.509 平台证书也能验签、`Authorization` 头字段、回调 AES-GCM 解密往返 + 错误密钥提示 + 32 位校验 |
| 网关请求（打桩 httpx） | Native 下单请求体（金额是分、notify_url、time_expire、Authorization 真签名）、H5 需 `payer_client_ip`、**应答被篡改必须拒绝**、凭据缺失给可操作提示 |
| HTTP 拒绝路径 | 未签名回调 → 400（notify 与 refund-notify）、查单需登录、未配置时微信不出现在支付方式里 |

### 6.2 人工验证（配好凭据后）

1. **先自检配置**：
   ```bash
   curl -s http://127.0.0.1:8020/api/payments/methods
   # 期望 enabled.wechat = true（false 说明凭据没齐）
   ```
2. **PC 扫码**：下单选微信支付 → 应弹出二维码 → 用**微信**扫码 → 输入金额确认付款
   → 页面应在几秒内自动提示「支付成功」，订单变「已支付」，后台支付方式显示「微信支付」。
3. **手机浏览器**：用手机打开站点（同一 WiFi 访问 `http://<内网IP>:8020`）→ 选微信支付
   → 应直接跳转拉起微信 → 付款后回到浏览器。
4. **退款**：后台订单管理 → 取消并退款 → 微信应收到退款通知；再查 `refund-notify`
   是否把支付记录置为 `refunded`（`run.log` 里搜 `[wechat]`）。

### 6.3 常见报错对照

| 现象 | 原因与处理 |
|---|---|
| `500 MissingGreenlet: greenlet_spawn has not been called` | **已修**：`pay` 接口只预加载了 payments、没预加载 items，而微信分支要用商品名生成账单描述；async 会话里访问未加载关系会直接抛异常。现已 `selectinload(Order.items)` + `_order_subject` 对未加载状态兜底 |
| `400 APPID_MCHID_NOT_MATCH`（appid和mch_id不匹配） | appid 没和商户号绑定。商户平台 → 产品中心 → **APPID 账号管理** → 关联 AppID，提交后用该 appid 所属平台（公众号/小程序/开放平台）**确认授权** |
| `403 NO_AUTH`（该产品权限未开通） | 商户平台 → **产品中心** 开通「Native 支付」（PC 扫码）与「H5 支付」（手机网页）；H5 还需配置 **H5 支付域名** |
| `401 SIGN_ERROR` | 签名串不对：查 query 是否漏进 URL；证书私钥与 `CERT_SERIAL_NO` 是否配套 |
| 回调 400「缺少 Wechatpay-Signature」 | 反代/CDN 过滤了微信扩展头（nginx 不要 `proxy_hide_header` 掉它们） |
| 回调 400「解密失败」 | `WECHATPAY_API_V3_KEY` 不对，或密钥被重设过 |
| 应答验签失败 | 公钥与当前商户号/App 不匹配，或用了平台证书却配了公钥 |

> 上面错误文案已内置「去哪点、点哪个」的中文指引（`app/wechatpay.py::wechatpay_error_text`），
> 运营拿到报错可直接自助处理。

---

## 7. 上线 checklist

1. 商户平台开通 **Native 支付** + **H5 支付**，并配置 **H5 支付域名**（备案域名）；
2. `cert/` 放好 `apiclient_key.pem` 与 `wxpay_public_key.pem`，`.env` 填 5 项必填；
3. `PAYMENT_GATEWAY_ENABLED` 里 `wechat: true`（且 JSON 完整）；
4. `BASE_URL` 改成公网 HTTPS 域名 —— 微信**只向公网可访问地址回调**，
   且回调地址不能带参数（`notify_url` 由代码拼为 `<BASE_URL>/api/payments/wechat/notify`）；
5. 确保反代**不丢** `Wechatpay-*` 请求头、不超时（微信要求 5 秒内应答）；
6. 上线后按 6.2 真付一笔小额，并验一遍退款；
7. 建议补：交易账单（`/v3/bill/tradebill`）与 `Payment` 表每日对账。

---

## 8. 相关代码位置

| 文件 | 说明 |
|---|---|
| [`app/wechatpay.py`](../app/wechatpay.py) | 协议层（签名/验签/解密/金额） |
| [`app/payments.py`](../app/payments.py) | `WechatGateway` 与网关注册表 |
| [`app/routers/payments.py`](../app/routers/payments.py) | 回调 / 查单 / 退款结果 |
| [`app/routers/orders.py`](../app/routers/orders.py) | `POST /api/orders/{no}/pay` 的微信分支 |
| [`.env.wechat.example`](../.env.wechat.example) | 配置模板（无密钥） |
| [`static/js/pymall.js`](../static/js/pymall.js) | `startPay` + `PayDialog`（二维码/轮询） |
| [`static/js/qrcode.min.js`](../static/js/qrcode.min.js) | 本地二维码生成（qrcodejs 1.0.0，MIT） |