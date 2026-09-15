# 商品批量导入（产品册 PPT）

从产品册 `.pptx` 文件批量导入瑜伽服饰商品到站点，自动生成 **Product + SKU（颜色 × 尺码）**，并把 PPT 中的图片抽取为商品主图与详情图。

提供两种使用方式：

| 方式 | 入口 | 适用场景 |
|---|---|---|
| HTTP 接口 | `POST /api/admin/import/products` | 运营在后台/脚本中上传导入、支持先预览 |
| 命令行 | `python -m app.import_jyt` | 本地/服务器一次性全量导入 |

两者共用同一套解析与入库逻辑（`app/product_import.py`），结果一致。

---

## 1. 依赖

```bash
.venv/bin/python -m pip install python-pptx
```

> 注意：本仓库的 `.venv/bin/pip` 可能是指向其他项目的软链，请使用 `.venv/bin/python -m pip` 安装。
> `python-pptx` 已写入 `requirements.txt`（同时依赖 `lxml`、`pillow`）。

---

## 2. PPT 格式约定

**一个 slide 对应一个商品。** 解析器按以下规则读取字段（不区分大小写、允许字符间有空格/换行）：

| 字段 | 解析规则 | 缺失时 |
|---|---|---|
| **货号** | ① 独立成行的裸货号（字母开头且含数字，长度 4–12，如 `JYJD017`、`MTWCX02`、`MT5FK`）<br>② `货号/Number` 标签后的值 | 生成 `JYT-{页码}` 并告警 |
| **品名** | `品名：xxx` 行 | `瑜伽服饰` |
| **面料** | `面料：80%锦纶20%氨纶`、`网纱：94%聚酯纤维6%氨纶` 等「中文：成分」行（可多组） | 回退解析 `Composition: ...` 并把英文成分译为中文；仍无则 `优质面料` |
| **尺码** | `尺码：S/M/L/XL/XXL` 行，支持 `均码` | `S/M/L/XL/XXL` |
| **颜色** | ① `颜色/Color` 标记之后的文字（标记可被拆成多行多个 run）<br>② 独立色卡文本框中的中文词<br>③ 英文颜色兜底（如 `Red`→`红色`） | `默认色` 并告警（最多 6 个颜色） |
| **图片** | 面积大且近乎全白（>50% 且白底率 >85%）→ 判定为版式占位图，**丢弃**；面积最大者 → **主图**；面积 ≤ 页面 1.2% → 色卡/logo（默认不落盘）；其余 → **详情图** | 无图时告警 |

### 图片输出

```
static/uploads/jyt/s{页码}/main.{ext}      # 主图（唯一）
static/uploads/jyt/s{页码}/detail_{n}.{ext} # 详情图
```

URL 形如 `/static/uploads/jyt/s202/main.jpg`。

#### 版式占位图的识别

产品册每页都铺了一张**大图**（占页面 71.7% 或 100%），内容为白底 + 橙色占位线 + 色卡，**不含服装**，若当作主图会导致商品图全是空白版式，必须剔除。

但同样是大图的还有**拼版商品图**（一张图内排了正面/侧面/背面三张模特照），它必须保留。两者面积完全相同，只能靠内容区分：

| 类型 | 面积占比 | 白底率 | 处理 |
|---|---|---|---|
| 版式占位图 | 71.7% / 100% | ≈ 0.95 ~ 1.00 | **丢弃** |
| 拼版商品图 | 71.7% | ≈ 0.48 ~ 0.73 | 保留为主图 |
| 单张模特照 | ≤ 37.8% | 0 ~ 0.75 | 保留 |

实测两类在白底率上有明显断层（0.73 与 0.95 之间），因此规则为「**面积 > 50% 且白底率 > 85%**」才判定为占位图。阈值见 `app/product_import.py` 的 `BACKGROUND_AREA_RATIO` / `BLANK_WHITE_RATIO`。

结果（本产品册 219 页）：主图 218 张、详情图 425 张，剔除 136 张空白版式图。

> 小图（色卡/logo，面积 ≤ 1.2%）**默认不落盘**：实测这些图里多数是品牌 logo，混入图集反而干扰展示。
> 如确需导出，命令行加 `--save-swatches`、接口传 `save_swatches=true`。

### 分类映射

按品名关键词归类（`app/product_import.py: CATEGORY_RULES`）：

| code | 中文 | 关键词 |
|---|---|---|
| `bras` | 文胸 | 文胸 |
| `vests` | 背心 | 背心、吊带 |
| `tshirts` | 短袖 | 短袖 |
| `longsleeves` | 长袖 | 长袖、开衫、罩衫 |
| `jackets` | 外套 | 外套、连帽衫、拉链连帽 |
| `shorts` | 短裤 | 短裤、三分裤、五分裤、七分裤 |
| `pants` | 长裤 | 长裤、喇叭裤、裙裤、阔腿裤、瑜伽裤、机车裤、烟管裤、直筒裤 |
| `skirts` | 裙子 | 短裙、百褶裙 |
| `other` | 其他 | 未命中任何关键词 |

价格按品类取基线价，命中「加绒」+40，「外套」且「加绒」按 199+60。

### SKU 生成

对每个商品的 **颜色 × 尺码** 组合生成一个 SKU：

- `sku_code`：`{货号}-{序号:03d}`
- `attributes`：`{"颜色": "黑色", "尺码": "M"}`
- `price` = 商品基础价；`cost_price` = 基础价 × 0.45
- `stock` = `80 + (序号 × 13) % 60`

---

## 3. HTTP 接口

### `POST /api/admin/import/products`

从上传的 pptx 批量导入商品。

**鉴权**：`Authorization: Bearer <admin token>`，需要 `superadmin` 或 `operator` 角色。

**请求**：`multipart/form-data`

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `file` | file | 必填 | 产品册 `.pptx`，最大 200MB |
| `mode` | string | `merge` | `merge`=保留现有商品，仅新增不存在的货号<br>`replace`=先清空 商品/SKU/分类 再全量导入 |
| `dry_run` | bool | `false` | `true` 只解析预览，**不写数据库、不保存图片** |
| `review_status` | string | `pending` | 导入后审核状态：`pending` 待审核（需人工审核后才上架）/ `approved` 直接上架 |
| `save_swatches` | bool | `false` | 是否保存色卡/logo 小图 |
| `clean_images` | bool | `false` | 导入前清空 `static/uploads/jyt`，避免残留旧图 |
| `featured_every` | int | `20` | 每 N 款标记一个推荐位，`0` 表示不标记 |
| `preview_limit` | int | `20` | 返回的预览条数，`0` 表示全部 |

> 导入商品的 `source` 记为 `import`，默认 `review_status=pending`，需在后台审核
> 通过后才会在前台展示（详见 [`docs/product-admin.md`](./product-admin.md)）。

**响应** `200`

```jsonc
{
  "source": "20260728-产品册合集_带logo_v3.pptx",
  "mode": "replace",
  "dry_run": false,
  "total_slides": 219,        // PPT 总页数
  "parsed_products": 218,     // 解析出的商品数
  "imported": 218,            // 实际入库数
  "skipped": 1,               // 跳过数（重复货号 / 已存在 / 异常）
  "failed": 0,                // 写入失败数
  "products_total": 218,      // 库中商品总数
  "skus_total": 4738,         // 库中 SKU 总数
  "categories_total": 9,      // 库中分类总数
  "images": { "main": 218, "detail": 560 },
  "skipped_slides": [
    { "slide": 133, "reason": "货号 JYMA162 与第 30 页重复", "sku_code": "JYMA162" }
  ],
  "warnings": ["slide 202: PPT 中未找到颜色文字（仅色卡图），已使用「默认色」"],
  "preview": [
    {
      "slide": 1, "sku_code": "JYJD017",
      "name_zh": "锦纶基础短袖", "name_en": "NylonBasicT-Shirt",
      "category_code": "tshirts", "base_price": "119.00",
      "colors": ["柔纱粉", "爱马仕蓝"], "sizes": ["S", "M", "L", "XL", "XXL"],
      "main_image": "/static/uploads/jyt/s1/main.jpg",
      "images": ["/static/uploads/jyt/s1/main.jpg"],
      "warnings": []
    }
  ]
}
```

**错误**

| 状态码 | 场景 |
|---|---|
| `400` | 文件非 `.pptx`、`mode` 非法、`featured_every` 为负 |
| `401` | 未登录 / token 无效 |
| `403` | 非管理员或无写权限 |
| `413` | 文件超过 200MB |

### `GET /api/admin/import/format`

返回当前解析规则（字段说明、分类关键词、入库模式），便于运营自查 PPT 是否合规。无需请求体，需管理员登录。

---

## 4. 命令行用法

```bash
# 使用默认产品册，清空后全量导入
.venv/bin/python -m app.import_jyt

# 指定路径
.venv/bin/python -m app.import_jyt "/path/to/产品册.pptx"

# 只预览，不写库、不落盘
.venv/bin/python -m app.import_jyt --dry-run

# 增量导入（保留现有商品）
.venv/bin/python -m app.import_jyt --mode merge

# 导入前清空图片目录
.venv/bin/python -m app.import_jyt --clean
```

| 选项 | 说明 |
|---|---|
| `pptx` | 产品册路径（位置参数，缺省用脚本内置 `DEFAULT_PPTX`） |
| `--mode {replace,merge}` | 入库模式，默认 `replace` |
| `--dry-run` | 只解析预览 |
| `--save-swatches` | 保存色卡/logo 小图 |
| `--clean` | 导入前清空 `static/uploads/jyt` |
| `--featured-every N` | 每 N 款标记推荐位，0 表示不标记 |

---

## 5. 调用示例

```bash
# 1) 登录拿 token
TOKEN=$(curl -s -X POST http://127.0.0.1:8020/api/admin/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# 2) 先预览，确认解析结果
curl -s -X POST http://127.0.0.1:8020/api/admin/import/products \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/产品册.pptx" \
  -F "dry_run=true" -F "preview_limit=10"

# 3) 正式导入（全量重置 + 清理旧图）
curl -s -X POST http://127.0.0.1:8020/api/admin/import/products \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/产品册.pptx" \
  -F "mode=replace" -F "clean_images=true"
```

---

## 6. 已知限制

| 现象 | 原因 | 处理 |
|---|---|---|
| 部分商品颜色为「默认色」 | 该页 PPT 只有色卡图片、没有颜色文字 | 已在 `warnings` 中逐页提示，需人工补录或修改源 PPT |
| 某些 slide 被跳过 | 同一货号在 PPT 中出现多页 | 保留首次出现的页面，其余记入 `skipped_slides` |
| 少数商品货号形如 `JYT-013` | PPT 中该页确实没有货号 | 已在 `warnings` 中提示，建议补货号后重导 |
| 该页无服装实拍图 | 该页只有版式占位图与 logo，没有模特照 | `main_image` 置空并告警；建议在源 PPT 补图后重导 |
| 主图是拼版图（含多视图 + 色卡） | 该页把整套商品展示合成在一张图内 | 属源 PPT 版式如此，保留原图；如需拆分需人工裁图 |

---

## 7. 相关文件

| 文件 | 说明 |
|---|---|
| `app/product_import.py` | 核心解析 + 入库逻辑（CLI 与接口共用） |
| `app/import_jyt.py` | 命令行入口 |
| `app/routers/imports.py` | HTTP 接口 |
| `app/schemas.py` | `ProductImportResultOut` 等响应模型 |
