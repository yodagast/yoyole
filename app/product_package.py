"""商品包（文件夹）导出 / 导入

以**文件夹**为单位搬运商品，与产品册 PPT 导入完全独立（互不复用解析逻辑）。

导出产出的目录结构（可直接拷贝、压缩、上传到别处）：

```
<商品包名>/
├── manifest.json          # 商品包元数据与商品清单（唯一必需文件）
├── categories.json        # 用到的类目（导入时按 code 匹配或自动建）
├── images/                # 所有引用到的图片（文件名即 URL 里的文件名）
│   ├── a1b2c3d4.jpg
│   └── ...
└── README.txt             # 给人看的说明
```

`manifest.json` 的结构：

```jsonc
{
  "format": "yoyole-product-package",
  "version": 1,
  "exported_at": "2026-09-15T12:00:00",
  "product_count": 2,
  "products": [
    {
      "sku_code": "JYMK005",
      "name_i18n": {"zh": "锦纶防晒阔腿裤", "en": "..."},
      "description_i18n": {"zh": "...", "en": "..."},
      "category_code": "pants",
      "brand": "YOYOLE",
      "weight_kg": "0.300",
      "base_price": "169.00",
      "main_image": "images/a1b2c3d4.jpg",
      "images": ["images/a1b2c3d4.jpg", "images/e5f6.jpg"],
      "is_featured": false,
      "skus": [
        {"sku_code": "JYMK005-001", "attributes": {"颜色": "松烟蓝", "尺码": "S"},
         "price": "169.00", "cost_price": "76.05", "stock": 80,
         "low_stock_threshold": 10, "is_active": true}
      ]
    }
  ]
}
```

设计要点：
- **图片路径相对化**：导出时把 `/static/uploads/...` 复制进 `images/` 并改写为相对路径，
  导入时再落地为新的 `/static/uploads/YYYY/MM/DD/<hash>.<ext>`，因此包可以跨环境搬运。
- **导入默认不改动已有商品**：按 `sku_code` 判断，默认跳过已存在的货号（`merge`）；
  也可选择 `update`（覆盖已有商品的字段与规格）。
- **审核状态可指定**：默认 `pending`（待审核，与产品册导入一致），可传 `approved` 直接上架。
- 不依赖 PPT、不依赖 `app.product_import`。
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Product, SKU

logger = logging.getLogger(__name__)

# 包标识与版本
FORMAT_NAME = "yoyole-product-package"
FORMAT_VERSION = 1

# 项目根目录与图片目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "static"
UPLOAD_DIR = STATIC_DIR / "uploads"

# 允许的图片扩展名（与 uploads.py 保持一致）
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".avif"}

# 单个包内的图片总大小上限（解压后），防止 zip 炸弹
MAX_TOTAL_IMAGE_BYTES = 800 * 1024 * 1024   # 800MB
MAX_PRODUCTS_PER_PACKAGE = 2000


# ============================================================ 导出
@dataclass
class ExportResult:
    """导出结果"""

    out_dir: str
    product_count: int = 0
    sku_count: int = 0
    image_count: int = 0
    missing_images: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    zip_path: str | None = None
    zip_bytes: int = 0


def _url_to_local_path(url: str) -> Path | None:
    """把公开 URL 映射回磁盘路径。

    支持两种前缀（指向同一目录）：
    - `/static/uploads/2026/09/15/a.jpg`
    - `/d/static/uploads/2026/09/15/a.jpg`（图文详情图标记）
    外部 URL（http/https）返回 None。
    """
    if not url:
        return None
    u = url.strip()
    for prefix in ("/d/static/uploads/", "/static/uploads/"):
        if u.startswith(prefix):
            return UPLOAD_DIR / u[len(prefix):]
    return None


def _new_image_name(src: Path) -> str:
    """为导出图片生成稳定、无冲突的文件名（内容哈希 + 原扩展名）

    用内容哈希而非 uuid：同一张图在多个商品间复用时可去重，
    且重复导出同一个商品得到的文件名一致。
    """
    ext = src.suffix.lower()
    if ext not in IMAGE_EXTS:
        ext = ".jpg"
    try:
        digest = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
    except OSError:
        digest = uuid.uuid4().hex[:16]
    return f"{digest}{ext}"


def _collect_image_urls(products: Sequence[Product]) -> list[str]:
    """收集商品引用到的全部图片 URL（去重、保持顺序）"""
    seen: dict[str, None] = {}
    for p in products:
        if p.main_image:
            seen.setdefault(p.main_image, None)
        for url in (p.images or []):
            if url:
                seen.setdefault(url, None)
    return list(seen)


def _category_code_map(categories: Sequence[Category]) -> dict[int, str]:
    return {c.id: c.code for c in categories}


def _decimal_str(value: Any, default: str = "0") -> str:
    """Decimal / 数字 → 字符串（JSON 里保留两位，避免精度丢失）"""
    if value is None:
        return default
    try:
        return f"{Decimal(str(value)):.2f}"
    except (InvalidOperation, ValueError):
        return default


async def export_products_to_dir(
    db: AsyncSession,
    products: Sequence[Product],
    out_dir: str | Path,
    *,
    package_name: str = "",
    zip_output: bool = False,
) -> ExportResult:
    """把商品导出成文件夹（可选同时打包 zip）。

    - `products`：已带 `skus` 的 Product 列表
    - `out_dir`：目标目录（会被创建；已存在则沿用并覆盖同名文件）
    - `zip_output`：为 True 时额外生成 `<out_dir>.zip` 并返回路径
    """
    if len(products) > MAX_PRODUCTS_PER_PACKAGE:
        raise ValueError(f"单次最多导出 {MAX_PRODUCTS_PER_PACKAGE} 款商品")

    out = Path(out_dir).expanduser().resolve()
    img_dir = out / "images"
    img_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 类目 ----------
    cat_ids = {p.category_id for p in products if p.category_id}
    categories: list[Category] = []
    if cat_ids:
        categories = list(
            (await db.execute(select(Category).where(Category.id.in_(cat_ids))))
            .scalars()
            .all()
        )
    code_by_id = _category_code_map(categories)

    # ---------- 图片：复制 + 建立 URL → 相对路径 映射 ----------
    url_to_rel: dict[str, str] = {}
    missing: list[str] = []
    copied = 0
    for url in _collect_image_urls(products):
        local = _url_to_local_path(url)
        if local is None:
            missing.append(url)
            continue
        if not local.exists():
            missing.append(url)
            continue
        fname = _new_image_name(local)
        dest = img_dir / fname
        if not dest.exists():
            try:
                shutil.copy2(local, dest)
                copied += 1
            except OSError as exc:
                missing.append(f"{url}（复制失败：{exc}）")
                continue
        url_to_rel[url] = f"images/{fname}"

    def rel(url: str | None) -> str | None:
        if not url:
            return None
        return url_to_rel.get(url, url)   # 未落地的保留原值（外部 URL）

    # ---------- 商品清单 ----------
    items: list[dict] = []
    sku_total = 0
    for p in products:
        skus = [
            {
                "sku_code": s.sku_code,
                "attributes": s.attributes or {},
                "price": _decimal_str(s.price),
                "cost_price": (
                    _decimal_str(s.cost_price) if s.cost_price is not None else None
                ),
                "stock": int(s.stock or 0),
                "locked_stock": int(s.locked_stock or 0),
                "low_stock_threshold": int(s.low_stock_threshold or 5),
                "is_active": bool(s.is_active),
            }
            for s in (p.skus or [])
        ]
        sku_total += len(skus)
        items.append({
            "sku_code": p.sku_code,
            "name_i18n": p.name_i18n or {},
            "description_i18n": p.description_i18n or {},
            "category_code": code_by_id.get(p.category_id),
            "brand": p.brand,
            "weight_kg": _decimal_str(p.weight_kg, "0.300") if p.weight_kg else None,
            "base_price": _decimal_str(p.base_price),
            "status": p.status,
            # 导出时只搬运内容，审核状态在导入时由调用方决定
            "is_featured": bool(p.is_featured),
            "main_image": rel(p.main_image),
            "images": [rel(u) for u in (p.images or []) if u],
            "skus": skus,
        })

    manifest = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "package_name": package_name or out.name,
        "product_count": len(items),
        "sku_count": sku_total,
        "products": items,
    }

    categories_doc = {
        "categories": [
            {
                "code": c.code,
                "name_i18n": c.name_i18n or {},
                "sort_order": int(c.sort_order or 0),
                "is_active": bool(c.is_active),
            }
            for c in categories
        ]
    }

    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "categories.json").write_text(
        json.dumps(categories_doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "README.txt").write_text(_readme_text(manifest, len(url_to_rel)), encoding="utf-8")

    result = ExportResult(
        out_dir=str(out),
        product_count=len(items),
        sku_count=sku_total,
        image_count=copied,
        missing_images=missing[:50],
    )
    if missing:
        result.warnings.append(
            f"有 {len(missing)} 张图片未能打包（文件不存在或为外部链接），已在清单中保留原 URL"
        )

    logger.info(
        "[export] %s：商品 %s，SKU %s，图片 %s，输出 %s",
        package_name or out.name, len(items), sku_total, copied, out,
    )

    if zip_output:
        zip_path = out.with_suffix(".zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(out.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(out.parent).as_posix())
        result.zip_path = str(zip_path)
        result.zip_bytes = zip_path.stat().st_size
        logger.info("[export] 已打包 %s（%.1f KB）", zip_path, result.zip_bytes / 1024)

    return result


def _readme_text(manifest: dict, image_count: int) -> str:
    return (
        "YOYOLE 商品包\n"
        "==============\n\n"
        f"导出时间：{manifest['exported_at']}\n"
        f"商品数量：{manifest['product_count']}\n"
        f"规格数量：{manifest['sku_count']}\n"
        f"图片数量：{image_count}\n\n"
        "目录说明\n"
        "--------\n"
        "manifest.json    商品清单（导入时唯一必需的文件，请勿改名）\n"
        "categories.json  用到的类目定义（按 code 匹配，不存在时自动创建）\n"
        "images/          所有商品图片，manifest 中以相对路径引用\n\n"
        "如何导入\n"
        "--------\n"
        "后台「商品管理」→「导入商品包」，选择本文件夹（或打包后的 .zip）：\n"
        "  · 已存在的货号默认跳过（可选择覆盖更新）\n"
        "  · 导入后默认进入「待审核」，审核通过后前台才展示\n\n"
        "注意：请保持 images/ 与 manifest.json 的相对位置不变，"
        "否则图片将无法正确落地。\n"
    )


# ============================================================ 导入
@dataclass
class ImportResult:
    """导入结果"""

    source: str
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    sku_created: int = 0
    sku_updated: int = 0
    categories_created: int = 0
    images_imported: int = 0
    missing_images: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    products_total: int = 0
    skus_total: int = 0


def load_manifest(src_dir: Path) -> dict:
    """读取并校验 manifest.json"""
    path = src_dir / "manifest.json"
    if not path.exists():
        raise ValueError("商品包缺少 manifest.json（请确认选择的是商品包目录）")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"manifest.json 不是合法的 JSON：{exc}") from exc

    if data.get("format") != FORMAT_NAME:
        raise ValueError(
            f"不是有效的商品包（format={data.get('format')!r}，应为 {FORMAT_NAME!r}）"
        )
    version = data.get("version")
    if not isinstance(version, int) or version > FORMAT_VERSION:
        raise ValueError(f"商品包版本不支持：{version}（当前最高 {FORMAT_VERSION}）")

    products = data.get("products")
    if not isinstance(products, list) or not products:
        raise ValueError("商品包里没有任何商品（products 为空）")
    if len(products) > MAX_PRODUCTS_PER_PACKAGE:
        raise ValueError(f"商品数量超过上限 {MAX_PRODUCTS_PER_PACKAGE}")
    return data


def extract_zip(zip_path: Path, dest_dir: Path) -> Path:
    """把商品包 zip 解压到 dest_dir，返回「含 manifest.json 的目录」

    做了路径穿越与解压体积校验，避免恶意压缩包写到目录外或撑爆磁盘。
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            # 路径穿越防护
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"压缩包内含非法路径：{name}")
            total += info.file_size
            if total > MAX_TOTAL_IMAGE_BYTES:
                raise ValueError("压缩包解压后体积过大，已中止")
        zf.extractall(dest_dir)

    # manifest.json 可能在根，也可能被多包了一层目录（macOS 压缩常见）
    if (dest_dir / "manifest.json").exists():
        return dest_dir
    for child in sorted(dest_dir.iterdir()):
        if child.is_dir() and (child / "manifest.json").exists():
            return child
    raise ValueError("压缩包里找不到 manifest.json")


def _store_image(src: Path, *, subdir: str | None = None) -> str | None:
    """把商品包里的图片落地为新的上传文件，返回公开 URL

    文件按上传日期分目录、用 uuid 命名，与 `uploads.py` 的既有风格一致。
    返回 None 表示源文件不存在（调用方记录 warning）。
    """
    if not src.exists() or not src.is_file():
        return None
    ext = src.suffix.lower()
    if ext not in IMAGE_EXTS:
        ext = ".jpg"
    now = datetime.now()
    day_dir = UPLOAD_DIR / now.strftime("%Y/%m/%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex}{ext}"
    dest = day_dir / fname
    shutil.copy2(src, dest)
    # 图文详情图（detail_ 前缀或原 URL 带 /d/）保留 /d/ 标记
    prefix = "/d/static/uploads" if subdir == "detail" else "/static/uploads"
    return f"{prefix}/{now.strftime('%Y/%m/%d')}/{fname}"


def _is_detail_path(rel: str) -> bool:
    """判断包内图片是否为「图文详情图」（导出时保留了 /d/ 前缀或 detail_ 文件名）"""
    return rel.startswith("/d/") or "/d/" in rel or Path(rel).name.startswith("detail_")


def _resolve_pkg_image(src_dir: Path, rel: str) -> Path | None:
    """把 manifest 里的图片相对路径解析为包内真实路径

    兼容三种写法：`images/x.jpg`、`x.jpg`、以及遗留的 `/static/uploads/...`
    绝对 URL（这种直接按项目目录解析，用于本机自导自回）。
    """
    rel = (rel or "").strip()
    if not rel:
        return None
    if rel.startswith("http://") or rel.startswith("https://"):
        return None
    if rel.startswith("/"):
        local = _url_to_local_path(rel)
        return local if local and local.exists() else None
    candidate = src_dir / rel
    if candidate.exists():
        return candidate
    # 退一步：只按文件名在包内找
    by_name = src_dir / "images" / Path(rel).name
    return by_name if by_name.exists() else None


async def _ensure_category(
    db: AsyncSession,
    code: str,
    name_i18n: dict | None,
    cache: dict[str, Category],
    *,
    sort_order: int = 0,
    created_counter: list[int] | None = None,
) -> Category:
    """按 code 取类目，不存在则创建（导入时不因缺类目而失败）"""
    key = (code or "other").strip().lower() or "other"
    if key in cache:
        return cache[key]

    cat = (
        await db.execute(select(Category).where(Category.code == key))
    ).scalar_one_or_none()
    if cat is None:
        names = name_i18n or {}
        cat = Category(
            code=key,
            name_i18n={
                "zh": names.get("zh") or key,
                "en": names.get("en") or key,
            },
            sort_order=sort_order,
            is_active=True,
        )
        db.add(cat)
        await db.flush()
        if created_counter is not None:
            created_counter[0] += 1
        logger.info("[import-pkg] 自动创建类目 %s", key)

    cache[key] = cat
    return cat


def _decimal(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def import_products_from_dir(
    db: AsyncSession,
    src_dir: str | Path,
    *,
    mode: str = "merge",
    review_status: str = "pending",
    operator: str = "import",
) -> ImportResult:
    """从商品包目录导入商品。

    `mode`：
    - `merge` （默认）：已有货号跳过，只新增
    - `update`         ：已有货号覆盖名称/描述/价格/图片等，并同步 SKU
                         （SKU 按 sku_code 匹配，缺失的补建，多出的停用）
    `review_status`：导入后的审核状态（pending 待审 / approved 直接上架）
    """
    if mode not in ("merge", "update"):
        raise ValueError("mode 必须是 merge 或 update")
    if review_status not in ("pending", "approved", "rejected"):
        raise ValueError("review_status 必须是 pending / approved / rejected")

    src = Path(src_dir).expanduser().resolve()
    manifest = load_manifest(src)

    result = ImportResult(source=manifest.get("package_name") or src.name)

    # ---------- 类目：先建好包内声明的（携带中英文名），再兜底 ----------
    cat_cache: dict[str, Category] = {}
    created_counter = [0]
    categories_doc_path = src / "categories.json"
    if categories_doc_path.exists():
        try:
            doc = json.loads(categories_doc_path.read_text(encoding="utf-8"))
            for entry in doc.get("categories", []):
                code = (entry.get("code") or "").strip().lower()
                if not code:
                    continue
                await _ensure_category(
                    db, code, entry.get("name_i18n"),
                    cat_cache,
                    sort_order=_int(entry.get("sort_order"), 0),
                    created_counter=created_counter,
                )
        except (json.JSONDecodeError, OSError) as exc:
            result.warnings.append(f"categories.json 读取失败，将按商品自动建类目：{exc}")

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for idx, item in enumerate(manifest["products"]):
        sku_code = str(item.get("sku_code") or "").strip()
        label = sku_code or f"第 {idx + 1} 条"
        if not sku_code:
            result.failed += 1
            result.errors.append(f"第 {idx + 1} 条缺少 sku_code，已跳过")
            continue

        # 每个商品一个 savepoint：单个商品出错只回滚它自己，
        # 不像整会话 rollback 那样把前面已导入的商品也一起丢掉
        sp = await db.begin_nested()
        try:
            existing = (
                await db.execute(
                    select(Product).where(Product.sku_code == sku_code)
                )
            ).scalar_one_or_none()

            if existing is not None and mode == "merge":
                result.skipped += 1
                continue

            # ---------- 图片落地 ----------
            main_rel = item.get("main_image")
            main_url = None
            if main_rel:
                p = _resolve_pkg_image(src, str(main_rel))
                if p:
                    main_url = _store_image(p)
                    if main_url:
                        result.images_imported += 1
                if not main_url:
                    result.missing_images.append(str(main_rel))

            img_urls: list[str] = []
            for rel in (item.get("images") or []):
                rel_s = str(rel)
                p = _resolve_pkg_image(src, rel_s)
                if not p:
                    result.missing_images.append(rel_s)
                    continue
                url = _store_image(p, subdir="detail" if _is_detail_path(rel_s) else None)
                if url:
                    img_urls.append(url)
                    result.images_imported += 1
                else:
                    result.missing_images.append(rel_s)

            # ---------- 类目 ----------
            cat_code = str(item.get("category_code") or "other").strip().lower() or "other"
            cat = await _ensure_category(
                db, cat_code, None, cat_cache, created_counter=created_counter
            )

            base_price = _decimal(item.get("base_price"))
            name_i18n = item.get("name_i18n") or {"zh": sku_code}
            desc_i18n = item.get("description_i18n") or {}

            is_new = existing is None
            product = existing or Product(sku_code=sku_code)

            product.category_id = cat.id
            product.name_i18n = name_i18n
            product.description_i18n = desc_i18n
            product.brand = item.get("brand") or "YOYOLE"
            product.base_price = base_price
            product.weight_kg = (
                _decimal(item["weight_kg"]) if item.get("weight_kg") else Decimal("0.300")
            )
            product.is_featured = bool(item.get("is_featured"))

            if is_new:
                # 新建：状态与审核状态由导入参数决定
                product.status = "active"
                product.review_status = review_status
                product.reviewed_by = operator if review_status == "approved" else None
                product.reviewed_at = now if review_status == "approved" else None
                product.source = "import"
                product.main_image = main_url
                product.images = img_urls
                db.add(product)
                await db.flush()
                result.imported += 1
            else:
                # 更新：只覆盖内容字段，不动审核状态与上下架状态（由后台单独控制）
                if main_url:
                    product.main_image = main_url
                if img_urls:
                    product.images = img_urls
                result.updated += 1

            # ---------- SKU ----------
            # 显式查询该商品已有的 SKU（不用 product.skus 关系属性：
            # 异步下惰性加载会抛 MissingGreenlet，且 savepoint 回滚后关系状态不可靠）
            existing_skus: dict[str, SKU] = {
                s.sku_code: s
                for s in (
                    await db.execute(select(SKU).where(SKU.product_id == product.id))
                ).scalars().all()
            }
            seen_sku_codes: set[str] = set()

            for sku_item in (item.get("skus") or []):
                s_code = str(sku_item.get("sku_code") or "").strip()
                if not s_code:
                    continue
                # 全局唯一约束：同编码已被其他商品占用时跳过，避免整批失败
                owner = (
                    await db.execute(select(SKU).where(SKU.sku_code == s_code))
                ).scalar_one_or_none()
                if owner is not None and owner.product_id != product.id:
                    result.warnings.append(
                        f"规格编码 {s_code} 已被其他商品（ID {owner.product_id}）占用，已跳过"
                    )
                    continue

                seen_sku_codes.add(s_code)
                sku = existing_skus.get(s_code) or owner
                if sku is None:
                    sku = SKU(product_id=product.id, sku_code=s_code)
                    db.add(sku)
                    result.sku_created += 1
                else:
                    result.sku_updated += 1

                sku.attributes = sku_item.get("attributes") or {}
                sku.price = _decimal(sku_item.get("price"), str(base_price))
                sku.cost_price = (
                    _decimal(sku_item["cost_price"])
                    if sku_item.get("cost_price") is not None
                    else sku.price * Decimal("0.45")
                )
                sku.stock = _int(sku_item.get("stock"), 0)
                sku.locked_stock = _int(sku_item.get("locked_stock"), 0)
                sku.low_stock_threshold = _int(sku_item.get("low_stock_threshold"), 5)
                sku.is_active = bool(sku_item.get("is_active", True))

            # 包内未出现的旧 SKU：停用（保留历史，避免破坏订单/流水引用）
            for s_code, sku in existing_skus.items():
                if s_code not in seen_sku_codes and sku.is_active:
                    sku.is_active = False
                    result.warnings.append(f"{s_code} 未在商品包中，已停用")

            # flush 让本商品的写入在 savepoint 内落到连接上，便于及早暴露约束冲突
            await db.flush()

        except Exception as exc:  # noqa: BLE001
            logger.exception("[import-pkg] 商品 %s 导入失败", label)
            result.failed += 1
            result.errors.append(f"{label} 导入失败：{exc}")
            await sp.rollback()
            # savepoint 回滚可能撤销了本次新建的类目，清空缓存避免引用已失效对象
            cat_cache.clear()
        else:
            await sp.commit()

    await db.commit()

    result.categories_created = created_counter[0]
    result.products_total = int(
        (await db.execute(select(func.count(Product.id)))).scalar() or 0
    )
    result.skus_total = int((await db.execute(select(func.count(SKU.id)))).scalar() or 0)

    logger.info(
        "[import-pkg] %s：新增 %s，更新 %s，跳过 %s，失败 %s，图片 %s",
        result.source, result.imported, result.updated,
        result.skipped, result.failed, result.images_imported,
    )
    return result