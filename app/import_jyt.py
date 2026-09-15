"""命令行：从产品册 pptx 导入瑜伽服饰商品（CLI 包装）

核心逻辑在 `app.product_import`，本文件只负责命令行参数与日志。

用法（项目根目录）：
    .venv/bin/python -m app.import_jyt [pptx路径] [选项]

示例：
    # 使用默认产品册，清空后全量导入
    .venv/bin/python -m app.import_jyt
    # 指定路径
    .venv/bin/python -m app.import_jyt "/path/to/产品册.pptx"
    # 只预览解析结果，不写数据库、不落盘图片
    .venv/bin/python -m app.import_jyt --dry-run
    # 增量导入：保留现有商品，仅新增不存在的货号
    .venv/bin/python -m app.import_jyt --mode merge

HTTP 接口见 `app/routers/imports.py`（POST /api/admin/import/products）。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from app.database import async_session_factory
from app.product_import import import_pptx, parse_pptx

logger = logging.getLogger("import_jyt")

# 默认产品册路径（不带参数时使用）
DEFAULT_PPTX = Path(
    "/Users/huangyong/Documents/跨境/jyt/20260728-产品册合集_带logo_v3.pptx"
)


def _print_preview(source: str, total: int, items: list, skipped: list) -> None:
    """dry-run：打印解析概要"""
    print(f"\n[预览] {source}：共 {total} 页，解析出 {len(items)} 款商品，"
          f"跳过 {len(skipped)} 页")
    print(f"{'页码':<6}{'货号':<14}{'品名':<22}{'分类':<14}{'颜色':<8}{'尺码':<12}价格")
    print("-" * 96)
    for it in items[:30]:
        print(f"{it.slide:<6}{it.sku_code:<14}{it.name_zh[:20]:<22}"
              f"{it.category_code:<14}{len(it.colors):<8}{'/'.join(it.sizes):<12}"
              f"{it.base_price}")
    if len(items) > 30:
        print(f"... 其余 {len(items) - 30} 款略")
    warns = [f"slide {s['slide']}: {s['reason']}" for s in skipped]
    warns += [f"slide {it.slide}: {w}" for it in items for w in it.warnings]
    if warns:
        print(f"\n[提示] 共 {len(warns)} 条，前 15 条：")
        for w in warns[:15]:
            print("  -", w)


async def main(args: argparse.Namespace) -> None:
    pptx_path = Path(args.pptx)
    logger.info("[import_jyt] 解析 %s", pptx_path)

    # ---------- 预览模式：不写库、不落盘 ----------
    if args.dry_run:
        parsed = parse_pptx(pptx_path, save_images=False)
        _print_preview(parsed.source, parsed.total_slides,
                       parsed.items, parsed.skipped_slides)
        return

    # ---------- 正式导入 ----------
    async with async_session_factory() as db:
        summary = await import_pptx(
            db,
            pptx_path,
            mode=args.mode,
            save_images=True,
            save_swatches=args.save_swatches,
            clean_out_dir=args.clean,
            featured_every=args.featured_every,
            review_status=args.review_status,
            reviewed_by="cli",
        )

    logger.info(
        "[import_jyt] 完成：%s 共 %s 页 → 导入 %s 款，跳过 %s，失败 %s",
        summary.source, summary.total_slides,
        summary.imported, summary.skipped, summary.failed,
    )
    logger.info("[import_jyt] 校验：products=%s, categories=%s, skus=%s",
                summary.products_total, summary.categories_total, summary.skus_total)
    if summary.warnings:
        logger.info("[import_jyt] 提示 %s 条：", len(summary.warnings))
        for w in summary.warnings[:20]:
            logger.info("  - %s", w)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从产品册 PPT 导入瑜伽服饰商品到网站"
    )
    parser.add_argument(
        "pptx", nargs="?", default=str(DEFAULT_PPTX),
        help=f"产品册 pptx 路径（默认：{DEFAULT_PPTX}）",
    )
    parser.add_argument(
        "--mode", choices=["replace", "merge"], default="replace",
        help="replace=清空后全量导入（默认）；merge=保留现有，仅新增不存在的货号",
    )
    parser.add_argument(
        "--review-status", choices=["pending", "approved"], default="pending",
        help="导入后审核状态：pending=待审核（默认，需后台审核）/ approved=直接上架",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只解析预览，不写数据库、不保存图片",
    )
    parser.add_argument(
        "--save-swatches", action="store_true",
        help="同时保存小图（色卡/logo），默认不保存以免产生无用文件",
    )
    parser.add_argument(
        "--clean", action="store_true",
        help="导入前清空图片输出目录 static/uploads/jyt，避免残留旧图",
    )
    parser.add_argument(
        "--featured-every", type=int, default=20,
        help="每 N 款商品标记一个推荐位（0 表示不标记，默认 20）",
    )
    return parser


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(main(build_parser().parse_args()))
