"""商品包（文件夹）导出 / 导入接口

与产品册 PPT 导入（`app/routers/imports.py`）**完全独立**，只处理「文件夹形式的商品包」。

导出：
    POST /api/admin/product-package/export        → JSON，落盘到服务器目录
    POST /api/admin/product-package/export-zip    → 直接下载 .zip（浏览器另存）

导入：
    POST /api/admin/product-package/import        → multipart，上传 .zip 导入
    POST /api/admin/product-package/import-server → 直接导入服务器上已导出的包（不经上传）

辅助：
    GET  /api/admin/product-package/format        → 商品包格式说明（前端展示用）

权限：superadmin / operator。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import require_admin
from app.models import AdminUser, Product
from app.product_package import (
    FORMAT_NAME,
    FORMAT_VERSION,
    MAX_PRODUCTS_PER_PACKAGE,
    STATIC_DIR,
    export_products_to_dir,
    extract_zip,
    import_products_from_dir,
    load_manifest,
)
from app.routers.product_admin import log_action
from app.schemas import (
    Message,
    ProductPackageExportIn,
    ProductPackageExportOut,
    ProductPackageImportOut,
    ProductPackageImportServerIn,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/product-package", tags=["admin-product-package"])

WRITE_ROLES = {"superadmin", "operator"}

# 导出根目录：static/exports/<包名>/（在 /static 挂载下可直接下载）
EXPORT_ROOT = STATIC_DIR / "exports"

MAX_ZIP_SIZE = 800 * 1024 * 1024      # 800MB
MAX_PACKAGE_NAME = 60

_SAFE_NAME_RE = re.compile(r"[^\w\u4e00-\u9fa5.\-]+")


def _safe_package_name(raw: str, *, prefix: str = "products") -> str:
    """清洗商品包名，避免路径穿越与非法字符"""
    name = _SAFE_NAME_RE.sub("_", (raw or "").strip())
    name = name.strip("._-")
    if not name:
        name = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return name[:MAX_PACKAGE_NAME]


def _to_public_url(path: Path) -> str | None:
    """把 exports 目录下的文件映射为可下载 URL"""
    try:
        rel = path.resolve().relative_to(STATIC_DIR.resolve())
    except ValueError:
        return None
    return f"/static/{rel.as_posix()}"


async def _load_products_with_skus(
    db: AsyncSession, ids: list[int]
) -> list[Product]:
    """按 ID 取商品（带 SKU 与分类），保持传入顺序"""
    rows = (
        await db.execute(
            select(Product)
            .options(selectinload(Product.skus))
            .where(Product.id.in_(ids))
        )
    ).scalars().all()
    by_id = {p.id: p for p in rows}
    return [by_id[i] for i in ids if i in by_id]


# ---------------------------------------------------------------- 导出
async def _do_export(
    payload: ProductPackageExportIn,
    admin: AdminUser,
    db: AsyncSession,
):
    """公共导出逻辑：取商品 → 落盘 → 记审计日志"""
    products = await _load_products_with_skus(db, payload.ids)
    if not products:
        raise HTTPException(status_code=404, detail="没有找到可导出的商品")

    missing_ids = [i for i in payload.ids if i not in {p.id for p in products}]
    name = _safe_package_name(payload.package_name)
    out_dir = EXPORT_ROOT / name
    if out_dir.exists():
        # 同名包已存在：加时间戳后缀，避免覆盖上一次的导出结果
        name = f"{name}_{datetime.now().strftime('%H%M%S')}"
        out_dir = EXPORT_ROOT / name

    try:
        result = await export_products_to_dir(
            db, products, out_dir,
            package_name=name,
            zip_output=payload.zip_output,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        logger.exception("[export] 落盘失败")
        raise HTTPException(status_code=500, detail=f"导出失败：{exc}") from exc

    if not payload.include_images and result.image_count:
        # include_images=False：删掉图片目录，只保留清单（供纯数据迁移用）
        shutil.rmtree(out_dir / "images", ignore_errors=True)
        result.image_count = 0
        result.warnings.append("已按要求只导出清单，未包含图片")

    log_action(
        db, None, "export_package",
        {
            "package": name, "products": result.product_count,
            "skus": result.sku_count, "images": result.image_count,
            "zip": bool(result.zip_path),
        },
        admin.username, sku=name, name=f"商品包导出（{result.product_count} 款）",
    )
    await db.commit()

    out = ProductPackageExportOut(
        package_name=name,
        out_dir=result.out_dir,
        manifest_path=str(Path(result.out_dir) / "manifest.json"),
        product_count=result.product_count,
        sku_count=result.sku_count,
        image_count=result.image_count,
        missing_images=result.missing_images,
        warnings=result.warnings,
        zip_path=result.zip_path,
        zip_bytes=result.zip_bytes,
    )
    if result.zip_path:
        out.zip_url = _to_public_url(Path(result.zip_path))
    if missing_ids:
        out.warnings.append(f"以下商品 ID 不存在，已忽略：{missing_ids[:20]}")
    return out


@router.post("/export", response_model=ProductPackageExportOut)
async def export_package(
    payload: ProductPackageExportIn,
    admin: AdminUser = Depends(require_admin(WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """导出选中商品为**服务器上的文件夹**（`static/exports/<包名>/`）。

    产物结构：`manifest.json` + `categories.json` + `images/` + `README.txt`，
    可直接拷贝到本地或另一台服务器。勾选 `zip_output` 时额外生成 `.zip`。
    """
    return await _do_export(payload, admin, db)


@router.post("/export-zip")
async def export_package_zip(
    payload: ProductPackageExportIn,
    admin: AdminUser = Depends(require_admin(WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """导出并**立即下载** `<包名>.zip`（浏览器另存到本地文件夹）。

    与 `/export` 的区别只是响应体：这里直接返回文件流，
    前端用原生 fetch + Blob 下载即可，无需再点第二次。
    """
    payload = payload.model_copy(update={"zip_output": True})
    out = await _do_export(payload, admin, db)
    if not out.zip_path or not Path(out.zip_path).exists():
        raise HTTPException(status_code=500, detail="打包失败，请改用「导出到服务器」重试")

    return FileResponse(
        out.zip_path,
        media_type="application/zip",
        filename=f"{out.package_name}.zip",
        headers={
            # 前端可读取文件名与统计信息
            "X-Package-Name": out.package_name,
            "X-Product-Count": str(out.product_count),
            "X-Image-Count": str(out.image_count),
            "Access-Control-Expose-Headers":
                "X-Package-Name, X-Product-Count, X-Image-Count",
        },
    )


@router.get("/exports", response_model=list[dict])
async def list_exports(
    admin: AdminUser = Depends(require_admin()),
):
    """列出服务器上已导出的商品包

    一个包同时存在 `<包名>/` 目录与 `<包名>.zip` 时合并为一行（否则会出现同名重复行，
    且 zip 单独一行时拿不到商品数）。
    """
    if not EXPORT_ROOT.exists():
        return []

    def dir_count(path: Path) -> int:
        try:
            return int(load_manifest(path).get("product_count") or 0)
        except ValueError:
            return 0

    def zip_count(path: Path) -> int:
        """从 zip 内部的 manifest.json 读商品数（读取失败则 0）"""
        try:
            with zipfile.ZipFile(path) as zf:
                name = next(
                    (n for n in zf.namelist()
                     if n == "manifest.json" or n.endswith("/manifest.json")),
                    None,
                )
                if not name:
                    return 0
                data = json.loads(zf.read(name).decode("utf-8"))
                return int(data.get("product_count") or 0)
        except (zipfile.BadZipFile, OSError, ValueError, UnicodeDecodeError):
            return 0

    out: list[dict] = []
    seen: set[str] = set()

    # 先处理目录（信息最全），再补「只有 zip、没有同名目录」的包。
    # 若反过来（或按名称混排），`X.zip` 会排在 `X/` 前面从而产生同名重复行。
    entries = list(EXPORT_ROOT.iterdir())
    dirs = sorted((p for p in entries if p.is_dir()), reverse=True)
    zips = sorted((p for p in entries if p.suffix == ".zip"), reverse=True)

    for child in dirs:
        name = child.name
        seen.add(name)
        zip_path = child.with_suffix(".zip")
        entry = {
            "name": name,
            "path": str(child),
            "product_count": dir_count(child),
            "created_at": datetime.fromtimestamp(
                child.stat().st_mtime
            ).isoformat(timespec="seconds"),
            "has_zip": zip_path.exists(),
        }
        if zip_path.exists():
            entry["zip_url"] = _to_public_url(zip_path)
            entry["zip_bytes"] = zip_path.stat().st_size
        out.append(entry)

    for child in zips:
        if child.stem in seen:
            continue          # 已有对应目录，合并到那一行展示
        out.append({
            "name": child.stem,
            "path": str(child),
            "product_count": zip_count(child),
            "created_at": datetime.fromtimestamp(
                child.stat().st_mtime
            ).isoformat(timespec="seconds"),
            "has_zip": True,
            "zip_url": _to_public_url(child),
            "zip_bytes": child.stat().st_size,
        })
    return out


# ---------------------------------------------------------------- 导入
def _check_import_params(mode: str, review_status: str) -> None:
    """两个导入接口共用的参数校验"""
    if mode not in ("merge", "update"):
        raise HTTPException(status_code=400, detail="mode 必须是 merge 或 update")
    if review_status not in ("pending", "approved", "rejected"):
        raise HTTPException(
            status_code=400, detail="review_status 必须是 pending / approved / rejected"
        )


async def _import_dir_and_log(
    db: AsyncSession,
    pkg_dir: Path,
    *,
    source: str,
    mode: str,
    review_status: str,
    admin: AdminUser,
) -> ProductPackageImportOut:
    """把已就绪的商品包目录入库，并记审计日志、提交事务

    上传导入与「服务器已导出包导入」共用这一段，避免两条链路各写一份导致行为分叉。
    """
    try:
        result = await import_products_from_dir(
            db, pkg_dir,
            mode=mode,
            review_status=review_status,
            operator=admin.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    log_action(
        db, None, "import_package",
        {
            "source_file": source, "mode": mode,
            "review_status": review_status,
            "imported": result.imported, "updated": result.updated,
            "skipped": result.skipped, "failed": result.failed,
            "skus_created": result.sku_created,
            "categories_created": result.categories_created,
            "images": result.images_imported,
        },
        admin.username, sku=source,
        name=f"商品包导入（{result.imported + result.updated} 款）",
    )
    await db.commit()

    logger.info(
        "[import-pkg] %s 由 %s 导入：新增 %s，更新 %s，跳过 %s，失败 %s（mode=%s）",
        source, admin.username, result.imported,
        result.updated, result.skipped, result.failed, mode,
    )

    return ProductPackageImportOut(
        source=result.source or source,
        mode=mode,
        imported=result.imported,
        updated=result.updated,
        skipped=result.skipped,
        failed=result.failed,
        sku_created=result.sku_created,
        sku_updated=result.sku_updated,
        categories_created=result.categories_created,
        images_imported=result.images_imported,
        missing_images=result.missing_images[:50],
        errors=result.errors[:50],
        warnings=result.warnings[:50],
        products_total=result.products_total,
        skus_total=result.skus_total,
    )


@router.post("/import", response_model=ProductPackageImportOut)
async def import_package(
    file: UploadFile = File(..., description="商品包 .zip（内部含 manifest.json 与 images/）"),
    mode: str = Form("merge", description="merge=已存在货号跳过（默认） / update=覆盖更新"),
    review_status: str = Form(
        "pending",
        description="导入后审核状态：pending=待审核（默认）/ approved=直接上架",
    ),
    admin: AdminUser = Depends(require_admin(WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """从上传的商品包 `.zip` 导入商品。

    包内需包含 `manifest.json`（唯一必需文件）与 `images/` 目录；
    压缩包允许外层多套一层目录（macOS「压缩」默认行为已兼容）。

    注意：大包走 multipart 上传时可能被前置反向代理拦下（nginx 默认 1MB）→ 413，
    此时后端根本收不到请求；服务器上已有的包改用 `/import-server` 导入。
    """
    _check_import_params(mode, review_status)

    filename = file.filename or "package.zip"
    if Path(filename).suffix.lower() not in (".zip",):
        raise HTTPException(
            status_code=400,
            detail="请上传 .zip 商品包（先把商品包文件夹压缩为 zip 再上传）",
        )

    tmp_dir = Path(tempfile.mkdtemp(prefix="pkg-"))
    zip_path = tmp_dir / "package.zip"
    try:
        # ---------- 落盘并校验大小 ----------
        size = 0
        with zip_path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_ZIP_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail=f"商品包超过 {MAX_ZIP_SIZE // 1024 // 1024}MB 上限",
                    )
                out.write(chunk)
        await file.close()

        if not zipfile.is_zipfile(zip_path):
            raise HTTPException(status_code=400, detail="上传的文件不是有效的 zip 压缩包")

        # ---------- 解压（含路径穿越与体积校验）----------
        extract_dir = tmp_dir / "extracted"
        try:
            pkg_dir = await asyncio.to_thread(extract_zip, zip_path, extract_dir)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except zipfile.BadZipFile as exc:
            raise HTTPException(status_code=400, detail="压缩包已损坏，无法解压") from exc

        # ---------- 入库 ----------
        return await _import_dir_and_log(
            db, pkg_dir,
            source=filename, mode=mode,
            review_status=review_status, admin=admin,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.post("/import-server", response_model=ProductPackageImportOut)
async def import_package_from_server(
    payload: ProductPackageImportServerIn,
    admin: AdminUser = Depends(require_admin(WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """直接导入服务器上已导出的商品包（**完全不经过浏览器上传**）

    存在的理由：商品包动辄几十上百 MB，走 multipart 上传时会被前置反向代理拦下 ——
    nginx 默认 `client_max_body_size 1m`，超限直接回 **413 纯 HTML 错误页**，
    不带 JSON `detail`，前端只能弹「导入失败（HTTP 413）」，看着像后端故障，
    实际后端根本没收到请求（`run.log` 里连访问日志都没有）。

    而商品包本来就导出在服务器 `static/exports/<包名>/`，直接读本地目录导入即可绕开
    这一环，也省掉「下载 37MB zip 再上传一遍」的往返。请求体是 JSON，只有几十字节。
    """
    _check_import_params(payload.mode, payload.review_status)

    name = _safe_package_name(payload.name)
    target = (EXPORT_ROOT / name).resolve()
    if not str(target).startswith(str(EXPORT_ROOT.resolve())):
        raise HTTPException(status_code=400, detail="非法的包名")

    tmp_dir: Path | None = None
    try:
        if target.is_dir():
            pkg_dir = target
        else:
            # 只有 zip、没有同名目录（例如手工拷进 exports 的包）：解压到临时目录再导入
            zip_path = target.with_suffix(".zip")
            if not zip_path.exists():
                raise HTTPException(
                    status_code=404, detail=f"服务器上找不到商品包「{name}」"
                )
            tmp_dir = Path(tempfile.mkdtemp(prefix="pkg-srv-"))
            try:
                pkg_dir = await asyncio.to_thread(
                    extract_zip, zip_path, tmp_dir / "extracted"
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except zipfile.BadZipFile as exc:
                raise HTTPException(status_code=400, detail="压缩包已损坏，无法解压") from exc

        return await _import_dir_and_log(
            db, pkg_dir,
            source=name, mode=payload.mode,
            review_status=payload.review_status, admin=admin,
        )
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@router.get("/format")
async def package_format_help(
    admin: AdminUser = Depends(require_admin()),
):
    """商品包格式说明（前端「商品包说明」弹窗用）"""
    return {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "max_products": MAX_PRODUCTS_PER_PACKAGE,
        "layout": {
            "manifest.json": "商品清单，唯一必需文件（勿改名）",
            "categories.json": "可选，用到的类目定义（按 code 匹配，缺失时按商品自动创建）",
            "images/": "可选但推荐，manifest 中以相对路径引用；缺图不会导致导入失败",
            "README.txt": "可选，说明文件",
        },
        "modes": {
            "merge": "已有货号跳过，只新增（默认，安全）",
            "update": "已有货号覆盖名称/描述/价格/图片并同步 SKU（包内未出现的旧 SKU 会停用）",
        },
        "review_status": {
            "pending": "导入后待审核，前台不展示（默认）",
            "approved": "导入即上架，前台立即可见",
        },
        "notes": [
            "导出的图片路径为相对路径（images/xxx.jpg），导入时会重新落地为新文件，"
            "因此商品包可跨环境搬运",
            "SKU 编码全局唯一；若某规格编码已被其他商品占用，该规格会被跳过并在 warnings 提示",
            "审核状态与上下架状态不会被覆盖式导入修改，需在后台单独控制",
        ],
    }


@router.delete("/exports/{name}", response_model=Message)
async def delete_export(
    name: str,
    admin: AdminUser = Depends(require_admin(WRITE_ROLES)),
):
    """删除服务器上某个已导出的商品包（目录与同名 zip）

    注意：只有 zip、没有同名目录的包也是合法状态（手工拷进 exports 的包就是如此），
    不能只用 `target.exists()` 判断存在性 —— 否则目录被删掉后 zip 永远清不掉。
    """
    safe = _safe_package_name(name)
    target = (EXPORT_ROOT / safe).resolve()
    if not str(target).startswith(str(EXPORT_ROOT.resolve())):
        raise HTTPException(status_code=400, detail="非法的包名")
    zip_path = target.with_suffix(".zip")
    if not target.exists() and not zip_path.exists():
        raise HTTPException(status_code=404, detail="商品包不存在")
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink(missing_ok=True)
    if zip_path.exists():
        zip_path.unlink(missing_ok=True)
    logger.info("[export] %s 删除了商品包 %s", admin.username, safe)
    return Message(message=f"商品包 {safe} 已删除")