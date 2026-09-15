# 商品管理后台：状态流转 · 批量管理 · 人工审核 · 接口文档

管理后台「商品管理」的完整说明。界面参考主流电商卖家后台（如拼多多商家后台）的
**单维度状态标签页 + 多字段搜索 + 行内快捷编辑 + 批量工具栏**布局。

## 0. 后台导航结构

| 侧边栏 | 视图 key | 说明 |
|---|---|---|
| 仪表盘 | `dashboard` | 商品/订单/客户/营收概览 |
| 商品管理 | `products` | 状态标签页、批量管理、审核、操作记录 |
| 类目管理 | `categories` | 商品分类 |
| 订单管理 | `orders` | 订单查询、状态流转、批量操作、导出（见 [`order-admin.md`](./order-admin.md)）|
| 用户管理 | `customers` | 顾客账号 |
| 订阅管理 | `newsletter` | 邮件订阅者与群发 |
| **品牌管理** | `brand` | 品牌故事、Hero 区、成长经历与品牌价值观（前台「关于我们」页） |
| **首页和用户管理** | `home` | 用户故事审核、首页 Hero 轮播 |
| 管理员权限 | `admins` | 仅 superadmin 可见；含修改 / 重置密码 |
| 接口文档 | `apidocs` | 商品管理接口文档 |

> 原「内容管理」已拆分：品牌故事 + 成长经历与品牌价值观 → **品牌管理**；
> 用户故事审核 + 首页轮播 → **首页和用户管理**。
> 旧链接 `?view=cms` 会自动跳转到「品牌管理」，保持向后兼容。
> 页面数据在切换视图时按需加载（`loadBrandContent()` / `loadHomeContent()`），互不干扰。

---

## 0.5 账号密码管理

### 修改自己的密码（所有管理员可用）

| 入口 | 位置 |
|---|---|
| 侧边栏 | 底部「修改密码」（退出登录上方） |
| 管理员权限页 | 右上角「修改我的密码」 |
| 管理员列表 | 自己那一行的「修改密码」按钮 |

弹窗需填写 **当前密码 + 新密码 + 确认新密码**，前端即时校验（≥6 位、两次一致、不与原密码相同），
后端二次校验，接口 `PUT /api/admin/me/password`：

```jsonc
// 请求
{ "old_password": "admin123", "new_password": "newSecret123" }
// 200 → { "message": "密码已修改，下次登录请使用新密码" }
// 400 → 当前密码不正确 / 新密码不能与当前密码相同
// 422 → 新密码少于 6 位
```

> 改密不影响当前已签发的 JWT（会话继续有效），下次登录使用新密码。

### 重置他人密码（仅超级管理员）

管理员列表中**他人**那一行的「重置密码」按钮 → 二次确认 → 输入新密码即可，
**无需对方原密码**。接口 `PUT /api/admin/admins/{id}/password`。

### 安全约束

| 约束 | 说明 |
|---|---|
| 改自己必须验原密码 | 防止会话被劫持后静默改密 |
| `PUT /admins/{id}` 的 `password` 字段**不能改自己** | 返回 400，提示走「修改密码」 |
| `PUT /admins/{id}/password` **不能重置自己** | 返回 400，同上（否则可绕过原密码校验）|
| 重置他人需 `superadmin` | 普通角色访问返回 403 |
| 密码强度 | 后端 `Field(min_length=6, max_length=128)`，前端同步校验 |

---

## 1. 商品状态（单维度）

一个商品在任意时刻只有**一个**状态，由「是否删除 / 审核结果 / 上下架 / 库存」派生：

| 状态 | 标签 | 含义 | 前台可见 |
|---|---|---|---|
| `on_sale` | 在售中 | 已上架且总库存 > 0 | ✅ |
| `off_shelf` | 已下架 | 主动下架 | ❌ |
| `sold_out` | 已售罄 | 已上架但总库存 = 0 | ✅（显示售罄）|
| `pending` | 发布中 | 已提交，等待人工审核 | ❌ |
| `rejected` | 已驳回 | 审核未通过 | ❌ |
| `draft` | 草稿箱 | 未提交上架 | ❌ |
| `deleted` | 已删除 | 在回收站，可恢复 | ❌ |

**判定优先级**：回收站 > 审核结果 > 上下架 > 库存

派生逻辑集中在 `app/models.py: derive_product_status()`；后台列表通过
`ProductOut.display_status` / `display_status_label` 直接拿到。

### 前台可见性

```
前台展示  ⟺  status = 'active'  AND  review_status = 'approved'  AND  deleted_at IS NULL
```

---

## 2. 状态流转（上架 / 下架 / 回收站）

```
             ┌─────────── 审核通过 ───────────┐
  导入/新建 ──┤                                ▼
             └─ pending ─驳回→ rejected     草稿 ─上架→ 在售中 ⇄ 已售罄
                                                 ▲         │
                                                 └── 下架 ──┘
                                                      │
                                      删除（软删除）──→ 回收站 ─恢复→ 已下架
                                                          └─彻底删除（仅超管）
```

### 上架前体检

`POST /products/{id}/publish` 会先校验，不通过返回 `400` 并列出原因：

**阻止上架（blockers）**
- 商品在回收站中
- 审核未通过（`rejected` / `pending`）
- 缺少商品主图
- 没有 SKU 规格
- 基础价 ≤ 0

**仅提示（warnings）**
- 库存为 0（上架后显示「已售罄」）
- 存在停用的 SKU
- 缺少中文描述

后台点「上架」时先调 `publish-check`，把阻止项/提示项展示给运营确认。

### 回收站（软删除）

- `DELETE /products/{id}` → **软删除**，进入回收站（数据保留，可恢复）
- `POST /products/{id}/restore` → 恢复，恢复后为「已下架」，需重新上架
- `DELETE /products/{id}/purge` → **彻底删除**（仅 `superadmin`）
  - 安全约束：必须先在回收站中，避免误抹在售商品

---

## 3. 后台界面

### 状态标签页
顶部标签带实时数量角标：`全部 / 在售中 / 已下架 / 已售罄 / 发布中 / 已驳回 / 草稿箱 / 已删除商品`
（数量来自 `GET /products-status-counts`）。切换标签即时刷新列表与计数。

### 多字段搜索
| 字段 | 说明 |
|---|---|
| 商品ID | 支持空格或逗号分隔多个（如 `1,2 3`） |
| 商品编码 | 按货号/中英文名称模糊匹配 |
| 规格编码 | 按 SKU 编码模糊匹配 |
| 商品名称 | 按名称模糊匹配 |
| 分类 / 来源 / 上架状态 / 审核状态 | 点「展开所有筛选项」显示 |

### 表格
`商品信息（缩略图 + 名称 + ID/编码/分类/规格数/来源） | 价格(元) | 总库存 | 收藏 | 销量 | 状态 | 创建时间 | 操作`

- **价格** 与 **总库存** 支持**行内点击直接修改**（同时提供「修改价格」「修改库存」链接）
- **操作列**按当前状态动态给出可用动作（文字链接）：
  - 在售 / 售罄 → 编辑、**下架**、预览、操作记录、删除
  - 待审 / 驳回 → 编辑、**审核**、预览、操作记录、删除
  - 下架 / 草稿 → 编辑、**上架**、预览、操作记录、删除
  - 回收站 → **恢复**、**彻底删除**（超管）、操作记录
- 分页：每页 10/20/50/100 可选，显示「共 N 条 / 第 x 页」
  - 总数取自 `GET /products-count`（**与列表同筛选口径**）；未筛选时等价于标签页数量
  - 加筛选/搜索后分页与「共 N 条」同步收窄，命中 0 条时不显示分页器
- **预览**：跳到 `/products.html?id=X`；管理员已登录时前台自动改用后台接口，
  因此**草稿 / 已下架 / 待审核 / 已驳回**商品也能预览（普通访客仍只能看在售已过审商品）

### 规格（SKU）级修改价格 / 修改库存（参考 PDD 卖家后台，不含拼单价）

点「修改价格」或「修改库存」打开**按规格（SKU）批量编辑**弹窗，不再按商品总库存均摊：

**修改价格**

| 功能区 | 说明 |
|---|---|
| 商品头 | 主图 + 名称 + ID/货号 + 规格数 |
| 批量栏 | 按「颜色 / 尺码」筛选后，「加 / 减」某个金额一键套用到匹配规格（自动推断属性 key，兼容自定义属性名） |
| 规格明细 | 每个规格：缩略图 + 规格ID + 规格属性 + 规格编码 + 停用标记、当前价格、可编辑的改后价格（带变化高亮） |
| 提交 | 只提交**有变化**的规格；改价后商品**基础价自动跟随最低启用规格价**，保证列表页价格口径一致 |

**修改库存**

| 功能区 | 说明 |
|---|---|
| 提示条 | ERP 同步提醒话术 |
| 商品头 | 同改价 |
| 批量栏 | 按「颜色 / 尺码」筛选后，对匹配规格统一「加 / 减」N 件（可切换加/减） |
| 规格明细 | 每行：规格信息、价格、当前库存、加减输入（带「加/减」切换与实时「改后库存」预览，负库存即时标红提示）、改后库存 |
| 修改记录 | 底部「查看修改记录」→ 该商品库存流水（时间/规格/变动/变动后/原因/操作人，最新优先，每次变更都写 StockMovement） |

约束：价格必须 > 0；库存加减后不能为负（为负的行被跳过或拦截）；未停用规格都参与编辑。
> 本项目**不涉及拼单价**（`sku_prices` / `sku_stocks` 只处理规格的单买价与库存），如需拼团玩法请另行扩展。

**弹窗交互**：`Esc` 关闭、点遮罩关闭、明细输入框内回车即提交；提交中按钮置灰并显示「提交中...」
防重复提交（双击只会写入一次库存流水）。

### 商品编辑页（`/admin-product-edit.html`）

| 能力 | 说明 |
|---|---|
| 单商品加载 | 按 `?id=` 走 `/api/admin/products/{id}` 精确加载（**不限状态**，草稿/下架/待审/回收站都能编辑），不再受列表首页 20 条分页限制 |
| 规格方案 | 颜色 × 尺码 自动重建 SKU 矩阵，尽量保留已有价格与库存 |
| SKU 状态列 | 每行可点击切换「启用 / 停用」，停用行灰显且不计入总库存 |
| 删除规格 | 被删规格组合的旧 SKU **自动停用**（保留历史流水），前台与购物车不再可选 |
| 图文详情图 | 上传详情大图走 `kind=detail`，返回 `/d/static/uploads/...`（带 `/d/` 标记） |

**SKU 编码（`sku_code`）规则**：全局唯一。

| 场景 | 行为 |
|---|---|
| 同商品内重复编码（删规格后又加回同名规格） | **复用原记录**（恢复启用并更新价格/库存），返回 `reused: true`；前端也会优先复用待停用 SKU |
| 编码被其他商品占用 | 返回 `400` 并提示改用其他编码（不再 500） |
| 更新 SKU 时改成已占编码 | 返回 `400`（同商品内提示「已存在」，跨商品提示「已被占用」）|

### 图片两类划分（`/d/` 标记）

商品 `images` 数组同时存放**轮播图**与**图文详情大图**，靠 URL 前缀区分：

| 类型 | URL 形式 | 来源 |
|---|---|---|
| 主图 / 轮播图 | `/static/uploads/...` | `kind=image` 上传、产品册主图 |
| 图文详情图 | `/d/static/uploads/...` | `kind=detail` 上传、产品册 `detail_*.png` |

- `/d/static` 与 `/static` 挂载同一目录，图片访问路径都有效
- 前台详情页轮播只取非 `/d/` 图；图文详情区只取 `/d/` 图
- 历史数据的 jyt 详情图由轻量迁移自动补 `/d/` 前缀（仅 `detail_` 文件）

### 批量工具栏
`批量上架`（逐个体检）、`批量下架`（可填原因）、`一键通过待审核`，以及「更多批量操作」：

批量上架（不体检）、批量转草稿、批量下架（不体检）、批量审核通过/驳回/重置待审、
批量设为/取消推荐、批量修改分类、批量从回收站恢复、批量移入回收站、批量彻底删除（超管）。

约束：单次 ≤ 500 条；ID 自动去重；不存在的 ID 计入 `skipped`；
上架体检不合格的计入 `failed` 并在 `errors` 给出原因；破坏性操作有二次确认。

**「下架原因」的两个入口都要传 `note`**：

| 入口 | action | 说明 |
|---|---|---|
| 工具栏「批量下架」按钮 | `unpublish` | 逐个体检（回收站中的商品会被跳过）|
| 「更多批量操作 → 批量下架（不体检）」 | `status` + `value=off_shelf` | 直接改状态，不做体检 |

两者都接受 `note` 作为下架原因写入 `products.off_shelf_reason`；
改为 `active`（上架 / 批量上架不体检）时该字段会被清空，避免残留旧原因。

> 历史问题：`status` 分支曾经忽略 `note`，但前端在选中「批量下架（不体检）」时
> 会显示下架原因输入框 → 用户填写的原因为被静默丢弃。

### 商品列表字段口径

列表接口 `GET /api/admin/products` 返回 `ProductOut`，其中两个字段**不是 products 表字段**，
需要路由额外计算，漏算时会静默显示为 0：

| 字段 | 来源 | 说明 |
|---|---|---|
| `total_stock` | `Product.total_stock` 属性（启用 SKU 库存合计）| 列表「总库存」列 |
| `favorite_count` | 路由内聚合 `wishlist_items` | 列表「收藏」列；前台 `catalog` 有自己的聚合 |

---

## 4. 人工审核

| 审核状态 | 说明 |
|---|---|
| `pending` | 待审核（**批量导入默认**） |
| `approved` | 已通过 → 前台可见 |
| `rejected` | 已驳回 → 前台不可见，**必须填写理由** |

- 后台人工录入的商品默认 `approved`（人工录入即确认）
- 产品册批量导入默认 `pending`，可用 `review_status=approved` 跳过
- 审核弹窗支持「通过 / 驳回 / 重置为待审核」，驳回强制填理由

---

## 5. 操作审计

所有人工与批量操作写入 `product_audit_logs`：

| 动作 | 触发 |
|---|---|
| `create` / `update` / `delete` | 人工增改删（`delete` 为软删除） |
| `publish` / `unpublish` | 单商品上下架 |
| `restore` / `purge` | 回收站恢复 / 彻底删除 |
| `quick_price` / `quick_stock` | 行内改价 / 改库存 |
| `review` | 单商品审核 |
| `bulk_publish` / `bulk_unpublish` / `bulk_review` / `bulk_status` / `bulk_category` / `bulk_featured` / `bulk_delete` / `bulk_restore` | 批量操作 |
| `import_products` | 产品册批量导入 |

后台「商品操作记录」查看全部日志，或点某行「操作记录」看该商品历史。
商品彻底删除后日志仍保留（`product_id` 置空，靠货号/名称追溯）。

### 5.1 日志弹窗的展示设计

日志以**结构化摘要**呈现，不再裸露 JSON：

| 列 | 展示方式 |
|---|---|
| 时间 | `今天 11:43:58` + 下方灰色完整日期，今天/昨天自动识别 |
| 动作 | 中文标签徽标（`上架`/`移入回收站`/`修改库存`…），带色调圆点，下方小字保留英文 action 便于对照接口 |
| 商品 | 商品名 + `货号 · #ID` 两行（单商品视图下自动隐藏该列） |
| 操作人 | 操作人账号 |
| 操作详情 | **人话摘要**（状态变化用 `→` 高亮、金额用等宽字体），下方「查看原始数据」可展开完整 JSON |

摘要由后端 `app/models.py::describe_audit_detail()` 统一生成，示例：

| 原始 `detail` | 展示摘要 |
|---|---|
| `{"from":"off_shelf","to":"active"}` | 已下架 → 在售中 |
| `{"from":"active","to":"off_shelf","reason":"测试下架"}` | 在售中 → 已下架 · 原因：测试下架 |
| `{"sku_code":"JYMK005","status":"off_shelf","soft":true}` | 移入回收站（可恢复） · 原状态 已下架 |
| `{"from":"150.00","to":"169","synced_skus":16}` | ¥150.00 → ¥169.00 · 同步 16 个规格 |
| `{"mode":"set","value":100,"from_total":800,"to_total":100,"skus":16}` | 总库存 设为 100（800 → 100） · 16 个规格 |
| `{"mode":"add","value":30,"from_total":100,"to_total":130,"skus":16}` | 总库存 +30（100 → 130） · 16 个规格 |
| `{"from":"pending","to":"rejected","note":"图片不清晰"}` | 待审核 → 已驳回 · 理由：图片不清晰 |
| `{"imported":218,"total_slides":219,"mode":"merge"}` | 导入 218 个商品 · 共 219 页 · 模式 merge |

未知动作/结构自动回退为 `key=value` 拼接，脏数据也不会让接口报错。

### 5.2 日志筛选与分页

| 能力 | 说明 |
|---|---|
| 关键词 | 搜索货号 / 商品名称 / 操作人（输入防抖 400ms） |
| 动作下拉 | **按真实日志动态生成**并带数量角标，如 `修改库存（31）`、`全部动作（691）` |
| 操作人下拉 | 同样动态生成；勾选「只看我的操作」自动切到当前登录账号 |
| 加载更多 | 每次 30 条，底部显示 `已显示 30 条 / 共 691 条` |
| 数量口径 | 动作下拉的数量**不随动作筛选变化**（否则选定后只剩自己），由两次 `meta` 请求分别取「筛选后总数」与「各动作数量」 |

---

## 5.5 后台弹窗规范（不使用浏览器原生对话框）

后台**不再使用** `alert` / `confirm` / `prompt` —— 原生对话框样式不可控、移动端体验差、
且会阻塞渲染线程。所有确认与输入统一走页面内弹窗组件 `App.dialog`（`static/js/common.js`）：

| 接口 | 用途 | 返回 |
|---|---|---|
| `App.dialog(opts)` | 通用弹窗（可带输入框 / 多行文本域） | `Promise` |
| `App.confirmDialog(msg, opts)` | 确认类操作 | `Promise<boolean>` |
| `App.promptDialog(opts)` | 输入类操作（如填写下架原因） | `Promise<string \| null>`（`null` = 取消） |

**视觉设计**对齐前台商品详情弹窗：16px 圆角白卡、右上角悬浮 `×`、`uiModalIn` 入场动画、
按语义着色的圆形图标（`danger` 红 / `warn` 黄 / `info` 蓝）、主次双按钮。

**交互**：`Esc` 关闭、点击遮罩关闭、单行输入回车即确认、`required` 字段失焦提示、
输入框自动聚焦。

**调用示例**

```js
// 危险操作二次确认
if (!await App.confirmDialog('彻底删除后不可恢复，将连同全部 SKU 一并删除。', {
      title: '彻底删除商品', tone: 'danger', okText: '确认彻底删除' })) return;

// 收集原因（取消时返回 null）
var reason = await App.promptDialog({
  title: '下架商品', tone: 'warn', okText: '确认下架',
  message: '下架后该商品将不在前台展示。',
  field: { label: '下架原因', placeholder: '可留空，将记录到操作日志' },
});
if (reason === null) return;
```

**已覆盖的原生对话框**

| 位置 | 原实现 | 现实现 |
|---|---|---|
| 商品上架 / 下架 | `confirm` + `prompt` | `info` 确认 + `warn` 输入（下架原因） |
| 移入回收站 / 彻底删除 | `confirm` | `warn` / `danger` 确认 |
| 批量上架 / 下架 / 改分类 / 改状态 / 推荐 / 回收站 / 审核 | `confirm` + `prompt` | 带条数与影响的语义化确认 |
| 一键通过全部待审核 | `confirm` | `info` 确认 |
| 用户 / 管理员 / 订阅者 / 用户故事 / 轮播 / 类目 删除 | `confirm` | `danger` 确认 |
| 新增 / 编辑管理员、新增 / 编辑类目 | 连续 `prompt` | 分步 `dialog`（带必填校验） |
| 群发邮件 | `confirm` | `warn` 确认（回显邮件主题） |
| 订单发货 | `confirm` | `info` 确认 |

> `admin.html` 内的 `ask()` / `askConfirm()` / `askText()` 是 `App.dialog` 系列的语法糖别名，
> 二者共用同一套 DOM 与样式，不存在重复实现。

---

## 6. 接口一览

所有接口需 `Authorization: Bearer <token>`。
读操作需 `superadmin` / `operator` / `viewer`；写操作需 `superadmin` / `operator`；
**彻底删除（purge）仅 `superadmin`**。

### 查询
| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/admin/products` | 列表：`tab` / `product_id` / `sku_code` / `q` / `category_id` / `source` / `status` / `review_status` / `page` / `page_size` / `limit` |
| `GET` | `/api/admin/products-count` | **筛选后的商品总数**（参数同列表，供分页「共 N 条」；不带筛选时等于标签页数量） |
| `GET` | `/api/admin/products/{id}` | **单个商品详情**（不限状态：草稿/下架/待审/回收站均可取；后台预览与编辑页用） |
| `GET` | `/api/admin/products-status-counts` | 各状态标签数量 |
| `GET` | `/api/admin/products-review-stats` | 审核概览 |

### 状态流转
| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/admin/products/{id}/publish-check` | 上架体检 |
| `POST` | `/api/admin/products/{id}/publish` | 上架 |
| `POST` | `/api/admin/products/{id}/unpublish` | 下架（可带 `reason`） |
| `POST` | `/api/admin/products/{id}/restore` | 回收站恢复 |
| `DELETE` | `/api/admin/products/{id}/purge` | 彻底删除（超管） |

### 增删改（人工管理）
| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/admin/products` | 新增（`source=manual`、`review_status=approved`） |
| `PUT` | `/api/admin/products/{id}` | 更新 |
| `DELETE` | `/api/admin/products/{id}` | 软删除（移入回收站） |
| `POST` | `/api/admin/products/{id}/skus` | 新增 SKU |
| `PUT` | `/api/admin/products/{id}/price` | 行内改价（旧接口，兼容保留） |
| `PUT` | `/api/admin/products/{id}/stock` | 行内改库存（旧接口，兼容保留） |
| `PUT` | `/api/admin/products/{id}/sku-prices` | **按规格批量改价**（不含拼单价，基础价自动跟随最低规格价） |
| `PUT` | `/api/admin/products/{id}/sku-stocks` | **按规格批量改库存**（`mode=add` 增减 / `mode=set` 设为，逐 SKU 写流水） |
| `GET` | `/api/admin/products/{id}/stock-movements` | 该商品的库存修改记录（库存流水，最新优先） |
| `PUT` | `/api/admin/skus/{sku_id}` | 更新单个 SKU（`is_active=false` 即停用） |
| `POST` | `/api/upload?kind=image\|detail\|video` | 图片/视频上传（`detail` 返回 `/d/` 前缀 URL） |
| `PUT` | `/api/admin/skus/{sku_id}/stock` | 调整单个 SKU 库存 |

### 批量 / 审核 / 审计 / 导入
| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/admin/products/bulk` | 批量操作（9 种 action） |
| `POST` | `/api/admin/products/bulk-publish-all` | 一键通过全部待审核 |
| `POST` | `/api/admin/products/{id}/review` | 单商品审核 |
| `GET` | `/api/admin/product-audit-logs` | 全局操作日志（`action` / `operator` / `q` / `product_id` / `limit`≤500 / `offset`） |
| `GET` | `/api/admin/product-audit-logs/meta` | 日志筛选元信息（总数 / 各动作数量 / 操作人列表） |
| `GET` | `/api/admin/products/{id}/audit-logs` | 单商品日志（`limit`≤200 / `offset`） |
| `POST` | `/api/admin/import/products` | 产品册 PPT 导入 |
| `GET` | `/api/admin/api-docs/products` | 结构化接口文档 |

---

## 7. 后台「接口文档」页

侧边栏 →「接口文档」，按分组渲染上述接口：method / 路径 / 权限 / Query·Form·Body 参数表 /
**可直接复制的 curl 示例**（`$BASE` 自动替换为当前站点地址），并附：

- 认证方式与权限矩阵
- 商品状态派生说明（7 种状态 + 上架阻止项）
- 审核机制与前台可见性规则

右上角可跳转 FastAPI Swagger（`/docs`）在线调试。

---

## 8. 相关文件

| 文件 | 说明 |
|---|---|
| `app/models.py` | `derive_product_status()` / `Product.total_stock` / `ProductAuditLog` |
| `app/routers/product_admin.py` | 状态计数 / 上下架 / 回收站 / 行内编辑 / 批量 / 审计 / 接口文档 |
| `app/routers/admin.py` | 商品 CRUD（列表支持 tab + 分页，删除为软删除） |
| `app/routers/catalog.py` | 前台过滤（未过审、已删除不露出） |
| `app/schemas.py` | `ProductOut` 自动派生 `display_status` |
| `app/database.py` | 轻量迁移（自动补列 + 历史数据回填） |
| `static/admin.html` | 后台商品管理界面 |
| `static/admin-product-edit.html` | 商品编辑页（基本信息 / 图片 / SKU / 详情） |
| `static/products.html` | 前台商品页（`?id=` 直达详情，管理员登录时可预览任意状态商品） |
| `static/js/pymall.js` | 前台公共库（`PyMall.getAdminToken()` 供后台预览判定管理员身份） |
| `static/js/common.js` | `App.dialog` / `App.confirmDialog` / `App.promptDialog`（页面内弹窗，替代原生对话框） |
| `test/test_api.py::TestProductLifecycle` | 生命周期自动化测试 |
| `test/test_api.py::TestProductReviewAndBulk` | 审核与批量测试 |
| `docs/product-import.md` | 产品册 PPT 批量导入 |

---

## 9. 数据库迁移

模型新增字段由 `app/database.py` 轻量迁移在启动时自动补齐：

- `products`：`review_status` / `review_note` / `reviewed_by` / `reviewed_at` / `source`
- `products`：`deleted_at` / `off_shelf_reason` / `last_status_at`
- 回填历史商品 `review_status='approved'`、`source='manual'`（避免加审核门槛后前台清空）

新表 `product_audit_logs` 由 `Base.metadata.create_all` 自动创建。
