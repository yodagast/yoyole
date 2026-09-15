# 商品包导出 / 导入（文件夹形式）

以**文件夹**为单位批量搬运商品，与产品册 PPT 导入**完全独立**（本功能不涉及 PPT 解析）。

- 后端核心：`app/product_package.py`
- HTTP 接口：`app/routers/product_package.py`（前缀 `/api/admin/product-package`）

---

## 1. 商品包结构

```
<包名>/
├── manifest.json       # 商品清单 —— 唯一必需文件
├── categories.json     # 用到的类目定义（按 code 匹配，不存在则自动创建）
├── images/             # 所有商品图片（文件名即内容哈希）
│   ├── 6f6050a6ac583c0c.jpg
│   └── ...
└── README.txt          # 给人看的说明
```

`manifest.json`：

```jsonc
{
  "format": "yoyole-product-package",   // 固定值，用于识别
  "version": 1,
  "exported_at": "2026-09-15T15:02:28",
  "package_name": "2026春夏瑜伽裤",
  "product_count": 2,
  "sku_count": 41,
  "products": [
    {
      "sku_code": "JYMK005",
      "name_i18n": {"zh": "锦纶防晒阔腿裤", "en": "NylonUVWide Leg"},
      "description_i18n": {"zh": "...", "en": "..."},
      "category_code": "pants",
      "brand": "YOYOLE",
      "weight_kg": "0.300",
      "base_price": "169.00",
      "is_featured": false,
      "main_image": "images/6f6050a6ac583c0c.jpg",
      "images": ["images/6f6050a6ac583c0c.jpg", "images/538a2974598a06f7.png"],
      "skus": [
        {"sku_code": "JYMK005-009",
         "attributes": {"颜色": "松烟蓝", "尺码": "S"},
         "price": "169.00", "cost_price": "76.05",
         "stock": 108, "locked_stock": 0,
         "low_stock_threshold": 10, "is_active": true}
      ]
    }
  ]
}
```

### 设计要点

| 要点 | 说明 |
|---|---|
| **图片相对化** | 导出时把 `/static/uploads/...` 复制进 `images/` 并改写为相对路径；导入时再落地为新的 `/static/uploads/YYYY/MM/DD/<uuid>.<ext>`。因此包可以跨环境、跨机器搬运 |
| **图片去重** | 导出文件名用**内容哈希**（`sha256[:16]`），同一张图被多个商品引用时只存一份；重复导出同一商品得到的文件名也一致 |
| **不含状态** | 清单里**不写** `review_status` / `status` / `deleted_at`。审核与上下架状态由导入参数和后台单独控制，避免把「已驳回」这类状态误带到新环境 |
| **唯一必需文件** | 只有 `manifest.json` 是必需的。缺 `images/` → 图片丢失但不报错；缺 `categories.json` → 按商品的 `category_code` 自动建类目 |
| **缺图不致命** | 图片找不到只记入 `missing_images` warning，商品照常导入 |

---

## 2. 导出

### `POST /api/admin/product-package/export`

把选中商品导出为**服务器上的文件夹** `static/exports/<包名>/`。

```jsonc
{
  "ids": [1562, 1561],        // 必填，1~2000 个
  "package_name": "2026春夏",  // 可留空 → 自动按时间命名
  "include_images": true,     // 默认 true
  "zip_output": false         // true 时额外生成 <包名>.zip
}
```

响应：

```jsonc
{
  "package_name": "2026春夏",
  "out_dir": "/root/yoyole/static/exports/2026春夏",
  "manifest_path": ".../manifest.json",
  "product_count": 2,
  "sku_count": 41,
  "image_count": 6,
  "missing_images": [],       // 外部链接 / 文件缺失的图片 URL
  "warnings": [],
  "zip_path": ".../2026春夏.zip",
  "zip_bytes": 1294621,
  "zip_url": "/static/exports/2026春夏.zip"   // 可直接下载
}
```

- 名称会被清洗（非法字符 → `_`），长度截断到 60
- **同名包已存在时自动加 `_HHMMSS` 后缀**，不会覆盖上一次的导出结果

### `POST /api/admin/product-package/export-zip`

同上参数（强制 `zip_output=true`），但**直接返回文件流**，浏览器另存为 `<包名>.zip`。

响应头带统计信息，前端可直接读取：

```
X-Package-Name: 2026春夏
X-Product-Count: 2
X-Image-Count: 6
Content-Disposition: attachment; filename="2026春夏.zip"
```

### `GET /api/admin/product-package/exports`

列出服务器上已导出的包。**同名目录与 `.zip` 会合并为一行**（否则会出现两行同名包，且 zip 那行拿不到商品数）：

```jsonc
[{
  "name": "2026春夏",
  "path": "/root/yoyole/static/exports/2026春夏",
  "product_count": 2,
  "created_at": "2026-09-15T15:02:28",
  "has_zip": true,
  "zip_url": "/static/exports/2026春夏.zip",
  "zip_bytes": 1294621
}]
```

只有 zip、没有同名目录时（手工拷入的包），商品数从 zip 内部的 `manifest.json` 读取。

### `DELETE /api/admin/product-package/exports/{name}`

删除目录与同名 zip。包名经过路径穿越校验。

---

## 3. 导入

### `POST /api/admin/product-package/import`

`multipart/form-data`：

| 字段 | 说明 |
|---|---|
| `file` | 商品包 `.zip`（内部含 `manifest.json`，允许外层多套一层目录 —— macOS「压缩」默认行为已兼容） |
| `mode` | `merge`（默认）已存在货号跳过 / `update` 覆盖更新 |
| `review_status` | `pending`（默认，待审核）/ `approved` 直接上架 / `rejected` |

响应：

```jsonc
{
  "source": "2026春夏", "mode": "merge",
  "imported": 2, "updated": 0, "skipped": 0, "failed": 0,
  "sku_created": 41, "sku_updated": 0,
  "categories_created": 0, "images_imported": 7,
  "missing_images": [], "errors": [], "warnings": [],
  "products_total": 220, "skus_total": 4779
}
```

### merge 与 update 的语义

| 场景 | `merge`（默认） | `update` |
|---|---|---|
| 货号已存在 | **跳过**，计入 `skipped` | **覆盖**名称/描述/价格/品牌/图片，计入 `updated` |
| 上下架状态 | 不动 | **不动**（仅新建时置 `active`） |
| 审核状态 | 不动 | **不动**（仅新建时按 `review_status`） |
| SKU 匹配 | 新增 | 按 `sku_code` 匹配更新，包内没有的补建 |
| 包内未出现的旧 SKU | 不动 | **停用**（`is_active=false`），保留历史，不影响订单与流水引用 |

### 容错与安全

| 情况 | 行为 |
|---|---|
| `manifest.json` 缺失 / 非法 JSON / `format` 不匹配 / 版本过高 | 400，明确说明原因 |
| `products` 为空或超 2000 条 | 400 |
| 压缩包含 `../` 路径穿越 | 400「压缩包内含非法路径」 |
| 解压后体积 > 800MB（zip 炸弹） | 400 |
| 上传体积 > 800MB | 413 |
| SKU 编码被**其他商品**占用 | 只跳过该规格，记入 `warnings`，其余规格正常导入 |
| 单个商品写入失败 | **只回滚该商品**（savepoint），前面已导入的保留，计入 `failed` + `errors` |
| 包内类目不存在 | 自动创建（`categories_created` +1） |

### 导入后前台不可见？

新建商品的审核状态是 `review_status`（默认 `pending`），而前台只展示
`status=active AND review_status=approved AND deleted_at IS NULL`。

导入后到后台点「商品管理 → 一键通过待审核」即可（`status` 已是 `active`，过审即上架）。

---

## 4. 后台界面

### 导出

1. 商品列表勾选商品（可跨页保留）
2. 工具栏「导出选中商品」
3. 弹窗里填包名、选择是否打包图片 / zip，然后：
   - **导出到服务器文件夹** → 落盘 `static/exports/<包名>/`，弹窗显示绝对路径与统计
   - **下载 zip 到本地** → 浏览器直接下载 `<包名>.zip`

> 「下载 zip 到本地」用的是**原生 fetch + Blob**：`App.api` 会强制设置
> `Content-Type: application/json`，用它发 multipart 或接收二进制都会出问题。

### 导入

1. 「导入商品包」→ 选择 `.zip`
2. 选择 merge / update（二次确认会说明两者差异）
3. 导入完成后弹窗展示：新增 / 更新 / 跳过 / 失败 四个数字，以及
   SKU 变化、自动建类目数、导入图片数、库中总数；提示 / 错误 / 缺失图片分块列出

### 已导出商品包

「已导出商品包」→ 列出服务器上的包（合并同名目录与 zip），可下载 zip 或删除。

---

## 5. 接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/admin/product-package/export` | 导出到服务器文件夹（可选生成 zip） |
| `POST` | `/api/admin/product-package/export-zip` | 导出并直接下载 zip |
| `GET` | `/api/admin/product-package/exports` | 列出已导出的包 |
| `DELETE` | `/api/admin/product-package/exports/{name}` | 删除包（目录 + zip） |
| `POST` | `/api/admin/product-package/import` | 从 zip 导入 |
| `GET` | `/api/admin/product-package/format` | 格式说明（前端弹窗用） |

写操作需要 `superadmin` / `operator`；`format` 与列表需要管理员登录。

审计日志动作：`export_package`、`import_package`（写入 `product_audit_logs`）。

---

## 6. 代码结构

| 文件 | 职责 |
|---|---|
| `app/product_package.py` | 导出/导入核心：清单读写、图片相对化与落地、zip 解压校验、savepoint 分批 |
| `app/routers/product_package.py` | HTTP 层：参数校验、临时目录管理、审计留痕、文件下载 |
| `static/admin.html` | 商品列表的导出/导入/包列表弹窗（`pkg*` 系列函数） |
| `test/test_api.py::TestProductPackage` | 25 个用例：格式、权限、导出结构、图片相对化、merge/update、去重、路径穿越、嵌套目录等 |

---

## 7. 与 PPT 导入的区别

| 维度 | 产品册 PPT 导入 | 商品包导入 |
|---|---|---|
| 输入 | `.pptx` 产品册 | `.zip` 商品包（或文件夹） |
| 解析 | 从 PPT 版面提取货号/品名/颜色/尺码/图片（启发式规则） | 读 `manifest.json`（结构化，无启发式） |
| 图片 | 从 PPT 内嵌图抽取 + 版面启发式过滤 | 直接用包内 `images/` 文件 |
| 适用 | 首次从产品册建站 | 已整理好的商品批量搬运 / 备份还原 / 跨环境同步 |
| 代码 | `app/product_import.py` | `app/product_package.py` |

两者互不依赖，可同时使用。详见 [`product-import.md`](./product-import.md)。