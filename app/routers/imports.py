"""商品批量导入接口：从产品册 PPT（.pptx）解析并导入商品

设计要点：
- 复用 `app.product_import` 的核心解析/入库逻辑，与命令行脚本保持同一套规则
- PPT 解析是 CPU/IO 密集的同步操作，放到线程池执行，避免阻塞事件循环
- 两段式导入：`dry_run=true` 先预览解析结果，确认无误后再正式入库
- 入库模式：
    * `merge`（默认）：保留现有商品，仅新增不存在的货号，适合增量补充
    * `replace`     ：先清空商品/SKU/分类再全量导入，适合整册重置

权限：需要管理员（superadmin / operator）。
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_admin
from app.models import AdminUser
from app.product_import import CATEGORY_RULES, import_items, parse_pptx
from app.routers.product_admin import log_action
from app.schemas import (
    ImportSkippedSlideOut,
    ImportedProductOut,
    ProductImportResultOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/import", tags=["admin-import"])

# 允许的扩展名与大小上限
ALLOWED_EXT = {".pptx"}
MAX_PPTX_SIZE = 200 * 1024 * 1024   # 200MB


def _validate_upload(file: UploadFile) -> str:
    """校验上传文件，返回扩展名"""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"仅支持 {', '.join(sorted(ALLOWED_EXT))} 文件，收到：{ext or '未知'}",
        )
    return ext


async def _save_temp(file: UploadFile) -> Path:
    """把上传的 pptx 落到临时文件，返回路径（调用方负责删除）"""
    size = 0
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pptx")
    try:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_PPTX_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"文件超过 {MAX_PPTX_SIZE // 1024 // 1024}MB 上限",
                )
            tmp.write(chunk)
    except Exception:
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)
        raise
    tmp.close()
    return Path(tmp.name)


def _to_preview(items, limit: int) -> list[ImportedProductOut]:
    """把解析结果转成响应模型（最多 limit 条；limit<=0 表示全部）"""
    chosen = items if limit <= 0 else items[:limit]
    return [
        ImportedProductOut(
            slide=it.slide,
            sku_code=it.sku_code,
            name_zh=it.name_zh,
            name_en=it.name_en,
            category_code=it.category_code,
            base_price=it.base_price,
            colors=it.colors,
            sizes=it.sizes,
            main_image=it.main_image,
            images=it.images,
            warnings=it.warnings,
        )
        for it in chosen
    ]


@router.post("/products", response_model=ProductImportResultOut)
async def import_products_from_pptx(
    file: UploadFile = File(..., description="产品册 .pptx 文件"),
    mode: str = Form("merge", description="merge=增量（默认） / replace=清空后全量"),
    dry_run: bool = Form(False, description="true 时只解析预览，不写入数据库"),
    save_swatches: bool = Form(False, description="是否保存色卡/logo 小图"),
    clean_images: bool = Form(False, description="导入前清空 static/uploads/jyt 目录"),
    featured_every: int = Form(20, description="每 N 款标记一个推荐位，0 表示不标记"),
    preview_limit: int = Form(20, description="返回的预览条数，0 表示全部"),
    review_status: str = Form(
        "pending",
        description="导入后审核状态：pending=待审核（默认，需人工审核）/ approved=直接上架",
    ),
    admin: AdminUser = Depends(require_admin({"superadmin", "operator"})),
    db: AsyncSession = Depends(get_db),
):
    """从产品册 PPT 批量导入商品。

    返回解析/入库统计；`dry_run=true` 时不会写入数据库，
    便于先确认解析结果再正式导入。

    导入的商品默认 `review_status=pending`（待审核），需在后台审核通过后才会
    在前台展示；如需导入即上架，传 `review_status=approved`。
    """
    if mode not in ("merge", "replace"):
        raise HTTPException(status_code=400, detail="mode 必须是 merge 或 replace")
    if featured_every < 0:
        raise HTTPException(status_code=400, detail="featured_every 不能为负数")
    if review_status not in ("pending", "approved", "rejected"):
        raise HTTPException(
            status_code=400, detail="review_status 必须是 pending / approved / rejected"
        )

    _validate_upload(file)
    tmp_path = await _save_temp(file)
    source_name = file.filename or tmp_path.name

    try:
        # 解析为重活：放线程池，避免阻塞事件循环
        parsed = await asyncio.to_thread(
            parse_pptx,
            tmp_path,
            save_images=not dry_run,
            save_swatches=save_swatches,
            clean_out_dir=clean_images and not dry_run,
        )

        skipped_slides = [
            ImportSkippedSlideOut(**s) for s in parsed.skipped_slides
        ]
        warnings = [f"slide {s['slide']}: {s['reason']}" for s in parsed.skipped_slides]
        warnings += [f"slide {it.slide}: {w}" for it in parsed.items for w in it.warnings]

        # ---------- 预览模式 ----------
        if dry_run:
            return ProductImportResultOut(
                source=source_name,
                mode=mode,
                dry_run=True,
                total_slides=parsed.total_slides,
                parsed_products=len(parsed.items),
                imported=0,
                skipped=len(parsed.skipped_slides),
                failed=0,
                products_total=0,
                skus_total=0,
                categories_total=0,
                images=parsed.images,
                skipped_slides=skipped_slides,
                warnings=warnings,
                preview=_to_preview(parsed.items, preview_limit),
            )

        # ---------- 正式入库 ----------
        summary = await import_items(
            db,
            parsed.items,
            source=source_name,
            mode=mode,
            featured_every=featured_every,
            review_status=review_status,
            reviewed_by=admin.username,
        )

        skipped_slides.extend(
            ImportSkippedSlideOut(slide=0, reason=w) for w in summary.warnings
        )
        warnings.extend(summary.warnings)

        # 导入动作留痕（商品级 source/review_status 由导入逻辑写入）
        log_action(
            db, None, "import_products",
            {
                "source_file": source_name, "mode": mode,
                "total_slides": parsed.total_slides,
                "parsed": len(parsed.items), "imported": summary.imported,
                "skipped": summary.skipped, "failed": summary.failed,
                "review_status": review_status,
            },
            admin.username, sku=source_name, name="批量导入",
        )
        await db.commit()

        logger.info(
            "[import] %s 由 %s 导入：新增 %s，跳过 %s，失败 %s（mode=%s）",
            source_name, admin.username, summary.imported,
            summary.skipped, summary.failed, mode,
        )

        return ProductImportResultOut(
            source=source_name,
            mode=mode,
            dry_run=False,
            total_slides=parsed.total_slides,
            parsed_products=len(parsed.items),
            imported=summary.imported,
            skipped=summary.skipped + len(parsed.skipped_slides),
            failed=summary.failed,
            products_total=summary.products_total,
            skus_total=summary.skus_total,
            categories_total=summary.categories_total,
            images=summary.images,
            skipped_slides=skipped_slides,
            warnings=warnings,
            preview=_to_preview(parsed.items, preview_limit),
        )
    finally:
        tmp_path.unlink(missing_ok=True)


@router.get("/format")
async def import_format_help(
    admin: AdminUser = Depends(require_admin()),
):
    """返回产品册 PPT 的字段解析规则（供前端/运维对照自查）。

    每个 slide 视作一个商品页，解析规则：
    - 货号：优先取独立成行的裸货号（字母开头、含数字，长度 4-12），
      其次取「货号/Number」标签后的值
    - 品名：取「品名：xxx」行
    - 面料：取「面料：成分」等「中文：百分比成分」行；
      无中文时回退解析「Composition: ...」并把英文成分译成中文
    - 尺码：取「尺码：S/M/L/XL/XXL」行
    - 颜色：优先取「颜色/Color」标记之后的文字；否则取独立色卡文本框中的中文词；
      仍为空时用英文颜色兜底，最后回退为「默认色」
    - 图片：面积最大者为主图，面积 <= 页面 1.2% 的视为色卡/logo，其余为详情图
    """
    return {
        "supported_ext": sorted(ALLOWED_EXT),
        "max_size_mb": MAX_PPTX_SIZE // 1024 // 1024,
        "one_slide_per_product": True,
        "fields": {
            "sku_code": "独立成行的裸货号（字母开头、含数字，4-12 位），或「货号/Number」后的值；缺失时用 JYT-{页码}",
            "name": "「品名：xxx」行",
            "fabrics": "「面料：80%锦纶20%氨纶」等「中文：成分」行；回退解析「Composition: ...」",
            "sizes": "「尺码：S/M/L/XL/XXL」行，支持「均码」；缺失默认 S/M/L/XL/XXL",
            "colors": "「颜色/Color」标记之后的文字；或独立色卡文本框；缺失回退英文颜色→「默认色」（最多 6 个）",
            "images": "面积最大为主图；<=页面 1.2% 为色卡/logo（默认不保存）；其余为详情图",
        },
        "categories": [
            {"code": code, "zh": zh, "en": en, "keywords": kws}
            for code, zh, en, kws in CATEGORY_RULES
        ] + [
            # 未命中任何关键词时的兜底分类
            {"code": "other", "zh": "其他", "en": "Other", "keywords": []}
        ],
        "modes": {
            "merge": "保留现有商品，仅新增不存在的货号（默认）",
            "replace": "先清空 商品/SKU/分类 再全量导入",
        },
    }
