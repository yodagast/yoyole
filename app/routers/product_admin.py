"""商品管理增强路由：人工审核、批量管理、操作审计、接口文档

补充 `app/routers/admin.py` 中已有的商品 CRUD：
- 单商品审核（通过／驳回／重置待审）与审核备注
- 批量操作（上下架／审核／改分类／推荐／删除）
- 商品操作审计日志（谁在何时改了什么）
- 供管理后台展示的商品接口文档

审核门槛：只有 `review_status = approved` 且 `status = active` 的商品才在前台展示。
批量导入的商品默认 `pending`，需人工审核后上架。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import require_admin
from app.models import (
    AdminUser,
    Category,
    Product,
    ProductAuditLog,
    SKU,
    StockMovement,
    audit_action_label,
)
from app.schemas import (
    Message,
    ProductAuditActionOut,
    ProductAuditLogOut,
    ProductAuditMetaOut,
    ProductBulkIn,
    ProductBulkResultOut,
    ProductOut,
    ProductPriceIn,
    ProductPublishCheckOut,
    ProductReviewIn,
    ProductReviewStatsOut,
    ProductStatusCountsOut,
    ProductStatusIn,
    ProductStockIn,
    ProductSKUPriceIn,
    ProductSKUStockIn,
    SKUStockMovementOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin-product"])

# 可写角色（与既有商品接口保持一致）
WRITE_ROLES = {"superadmin", "operator"}

REVIEW_ACTIONS = {"approve": "approved", "reject": "rejected", "pending": "pending"}
VALID_STATUS = {"active", "draft", "off_shelf"}

# 「商品状态」标签页（PDD 风格单维度状态）
TABS = ("all", "on_sale", "off_shelf", "sold_out",
        "pending", "rejected", "draft", "deleted")
STATUS_LABELS = {
    "all": "全部",
    "on_sale": "在售中",
    "off_shelf": "已下架",
    "sold_out": "已售罄",
    "pending": "发布中",
    "rejected": "已驳回",
    "draft": "草稿箱",
    "deleted": "已删除",
}


# ---------------------------------------------------------------- 状态查询辅助
def active_stock_subq():
    """相关子查询：该商品启用 SKU 的库存合计（用于「在售/售罄」判定）"""
    return (
        select(func.coalesce(func.sum(SKU.stock), 0))
        .where(SKU.product_id == Product.id, SKU.is_active.is_(True))
        .correlate(Product)
        .scalar_subquery()
    )


def apply_tab_filter(stmt, tab: str):
    """把「状态标签页」翻译成查询条件。

    `all` / 各在售类标签都**不含已删除**商品（已删除单独一个标签页）。
    """
    if tab == "deleted":
        return stmt.where(Product.deleted_at.is_not(None))

    stmt = stmt.where(Product.deleted_at.is_(None))
    if tab in ("all", "draft", "off_shelf", "sold_out", "on_sale"):
        # 这些标签只关心已过审且未删除的商品
        stmt = stmt.where(Product.review_status == "approved")

    if tab == "pending":
        return stmt.where(Product.review_status == "pending")
    if tab == "rejected":
        return stmt.where(Product.review_status == "rejected")
    if tab == "draft":
        return stmt.where(Product.status == "draft")
    if tab == "off_shelf":
        return stmt.where(Product.status == "off_shelf")
    if tab == "on_sale":
        return stmt.where(Product.status == "active", active_stock_subq() > 0)
    if tab == "sold_out":
        return stmt.where(Product.status == "active", active_stock_subq() <= 0)
    return stmt  # all


def status_counts_stmt(tab: str):
    """各标签页的数量统计（用于标签角标）"""
    return select(func.count(Product.id)).select_from(Product)


def publish_blockers(product: Product) -> list[str]:
    """上架前置校验：返回阻止上架的原因（空列表表示可上架）"""
    blockers: list[str] = []
    if product.deleted_at is not None:
        blockers.append("商品在回收站中，请先恢复")
    if product.review_status == "rejected":
        blockers.append("商品已被驳回，请修改后重新提交审核")
    elif product.review_status != "approved":
        blockers.append("商品尚未通过审核（当前：发布中）")
    if not product.main_image:
        blockers.append("缺少商品主图")
    if not (product.skus or []):
        blockers.append("至少需要 1 个 SKU 规格")
    if product.base_price is None or product.base_price <= 0:
        blockers.append("基础价必须大于 0")
    return blockers


def publish_warnings(product: Product) -> list[str]:
    """不阻止上架、但建议处理的提示"""
    warnings: list[str] = []
    if product.total_stock <= 0:
        warnings.append("库存为 0，上架后将显示为「已售罄」")
    inactive = [s for s in (product.skus or []) if not s.is_active]
    if inactive:
        warnings.append(f"有 {len(inactive)} 个 SKU 处于停用状态")
    if not (product.description_i18n or {}).get("zh"):
        warnings.append("缺少中文商品描述")
    return warnings


async def _load_product(db: AsyncSession, product_id: int) -> Product:
    product = (
        await db.execute(
            select(Product)
            .options(selectinload(Product.skus))
            .where(Product.id == product_id)
        )
    ).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    return product


def _touch_status(product: Product, now: datetime) -> None:
    product.last_status_at = now


# ---------------------------------------------------------------- 状态统计
@router.get("/products-status-counts", response_model=ProductStatusCountsOut)
async def product_status_counts(
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """各状态标签页的商品数量（后台标签角标）"""
    data: dict[str, int] = {}
    for tab in TABS:
        stmt = apply_tab_filter(status_counts_stmt(tab), tab)
        data[tab] = int((await db.execute(stmt)).scalar() or 0)
    return ProductStatusCountsOut(**data)


# ---------------------------------------------------------------- 上架 / 下架
@router.get("/products/{product_id}/publish-check", response_model=ProductPublishCheckOut)
async def check_publish(
    product_id: int,
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """上架前体检：返回阻止项与建议项，供后台在点「上架」前提示"""
    product = await _load_product(db, product_id)
    blockers = publish_blockers(product)
    return ProductPublishCheckOut(
        can_publish=not blockers,
        blockers=blockers,
        warnings=publish_warnings(product),
    )


@router.post("/products/{product_id}/publish", response_model=ProductOut)
async def publish_product(
    product_id: int,
    payload: ProductStatusIn | None = None,
    admin: AdminUser = Depends(require_admin(WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """上架商品（会先做前置校验，不满足则返回 400 并说明原因）"""
    product = await _load_product(db, product_id)
    blockers = publish_blockers(product)
    if blockers:
        raise HTTPException(
            status_code=400,
            detail={"message": "商品不满足上架条件", "blockers": blockers},
        )

    before = product.status
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    product.status = "active"
    product.off_shelf_reason = None
    _touch_status(product, now)
    log_action(db, product, "publish", {"from": before, "to": "active"},
               admin.username)
    await db.commit()
    await db.refresh(product)
    return product


@router.post("/products/{product_id}/unpublish", response_model=ProductOut)
async def unpublish_product(
    product_id: int,
    payload: ProductStatusIn | None = None,
    admin: AdminUser = Depends(require_admin(WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """下架商品（可填写下架原因，写入审计日志）"""
    product = await _load_product(db, product_id)
    if product.deleted_at is not None:
        raise HTTPException(status_code=400, detail="商品在回收站中，无法下架")

    reason = (payload.reason.strip() if payload and payload.reason else "")
    before = product.status
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    product.status = "off_shelf"
    product.off_shelf_reason = reason or None
    _touch_status(product, now)
    log_action(db, product, "unpublish",
               {"from": before, "to": "off_shelf", "reason": reason},
               admin.username)
    await db.commit()
    await db.refresh(product)
    return product


# ---------------------------------------------------------------- 回收站
@router.post("/products/{product_id}/restore", response_model=ProductOut)
async def restore_product(
    product_id: int,
    admin: AdminUser = Depends(require_admin(WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """从回收站恢复商品（恢复后为下架状态，需手动上架）"""
    product = await _load_product(db, product_id)
    if product.deleted_at is None:
        raise HTTPException(status_code=400, detail="商品不在回收站中")

    product.deleted_at = None
    product.status = "off_shelf"
    _touch_status(product, datetime.now(timezone.utc).replace(tzinfo=None))
    log_action(db, product, "restore", {"to_status": "off_shelf"}, admin.username)
    await db.commit()
    await db.refresh(product)
    return product


@router.delete("/products/{product_id}/purge", response_model=Message)
async def purge_product(
    product_id: int,
    admin: AdminUser = Depends(require_admin({"superadmin"})),
    db: AsyncSession = Depends(get_db),
):
    """彻底删除（仅超管，不可恢复）。

    安全约束：商品必须先在回收站中（软删除），避免误操作直接抹除在售商品。
    """
    product = await _load_product(db, product_id)
    if product.deleted_at is None:
        raise HTTPException(
            status_code=400,
            detail="请先将商品移入回收站，再执行彻底删除",
        )
    log_action(db, product, "purge",
               {"sku_code": product.sku_code, "status": product.status,
                "review_status": product.review_status},
               admin.username)
    await db.execute(StockMovement.__table__.delete().where(
        StockMovement.sku_id.in_([s.id for s in product.skus])
    ))
    await db.delete(product)
    await db.commit()
    return Message(message="商品已彻底删除")


# ---------------------------------------------------------------- 行内快捷编辑
@router.put("/products/{product_id}/price", response_model=ProductOut)
async def quick_price(
    product_id: int,
    payload: ProductPriceIn,
    admin: AdminUser = Depends(require_admin(WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """行内快速改价（可选同步所有 SKU）"""
    product = await _load_product(db, product_id)
    before = str(product.base_price)
    product.base_price = payload.base_price

    synced = 0
    if payload.sync_skus:
        for sku in product.skus:
            sku.price = payload.base_price
            synced += 1

    log_action(
        db, product, "quick_price",
        {"from": before, "to": str(payload.base_price),
         "synced_skus": synced, "reason": payload.reason},
        admin.username,
    )
    await db.commit()
    await db.refresh(product)
    return product


def distribute_total(total: int, n: int) -> list[int]:
    """把商品总库存均摊到 n 个 SKU 上（余数分给前几个，保证合计精确等于 total）"""
    base, rem = divmod(total, n)
    return [base + (1 if i < rem else 0) for i in range(n)]


@router.put("/products/{product_id}/stock", response_model=ProductOut)
async def quick_stock(
    product_id: int,
    payload: ProductStockIn,
    admin: AdminUser = Depends(require_admin(WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """行内快速改库存：按**商品总库存**语义处理，均摊到全部启用 SKU。

    - `mode=set`：把总库存设为 `value`（均摊到各 SKU）
    - `mode=add`：在总库存基础上增减 `value`
    每次变更都写入库存流水（StockMovement）与审计日志。
    """
    if payload.mode not in ("set", "add"):
        raise HTTPException(status_code=400, detail="mode 必须是 set 或 add")
    product = await _load_product(db, product_id)
    skus = [s for s in product.skus if s.is_active]
    if not skus:
        raise HTTPException(status_code=400, detail="该商品没有启用的 SKU")

    before_total = sum(s.stock or 0 for s in skus)
    if payload.mode == "set":
        if payload.value < 0:
            raise HTTPException(status_code=400, detail="总库存不能为负")
        targets = distribute_total(payload.value, len(skus))
    else:
        deltas = distribute_total(payload.value, len(skus))
        targets = [(s.stock or 0) + d for s, d in zip(skus, deltas)]
        if any(t < 0 for t in targets):
            raise HTTPException(
                status_code=400,
                detail=f"减少后库存不能为负（当前总库存 {before_total}）",
            )

    for sku, target in zip(skus, targets):
        delta = target - (sku.stock or 0)
        sku.stock = target
        if delta:
            db.add(StockMovement(
                sku_id=sku.id, change_qty=delta, balance_after=target,
                reason=payload.reason or "quick_edit", reference="",
                operator=admin.username,
            ))
    after_total = sum(s.stock or 0 for s in skus)

    log_action(
        db, product, "quick_stock",
        {"mode": payload.mode, "value": payload.value,
         "from_total": before_total, "to_total": after_total,
         "skus": len(skus), "reason": payload.reason},
        admin.username,
    )
    await db.commit()
    await db.refresh(product)
    return product


# ---------------------------------------------------------------- 规格级改价 / 改库存
def _sku_items_or_400(product: Product, raw_items) -> list[tuple]:
    """校验提交的 SKU 均属于该商品，返回 `(提交项, SKU)` 列表（保持原有顺序）

    重复提交同一 SKU 时以最后一次为准（保持第一次出现的位置）。
    """
    sku_map = {s.id: s for s in (product.skus or [])}
    seen: dict[int, object] = {}
    for item in raw_items:
        if item.sku_id not in sku_map:
            raise HTTPException(
                status_code=400,
                detail=f"规格 {item.sku_id} 不属于商品 {product.id}",
            )
        seen[item.sku_id] = item  # 后出现的覆盖先出现的
    return [(seen[s.id], s) for s in (product.skus or []) if s.id in seen]


@router.put("/products/{product_id}/sku-prices", response_model=ProductOut)
async def update_sku_prices(
    product_id: int,
    payload: ProductSKUPriceIn,
    admin: AdminUser = Depends(require_admin(WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """按规格（SKU）批量改价 —— 后台「修改价格」弹窗提交。

    只处理规格价格（前台展示的单买价），不涉及拼单价。
    改价后商品基础价自动跟随为**启用规格中的最低价**，保证列表页价格口径一致。
    """
    product = await _load_product(db, product_id)
    items = _sku_items_or_400(product, payload.items)

    changes: list[dict] = []
    spans: list[dict] = []
    for item, sku in items:
        before = sku.price
        after = item.price
        if before == after:
            continue
        sku.price = after
        changes.append({
            "sku_code": sku.sku_code,
            "from": str(before),
            "to": str(after),
        })
        span = {"from": str(before), "to": str(after)}
        if span not in spans:
            spans.append(span)

    if not changes:
        return product

    # 基础价跟随最低启用规格价（列表页/上架体检都用 base_price）
    base_before = product.base_price
    active_prices = [s.price for s in product.skus if s.is_active]
    if active_prices:
        base_after = min(active_prices)
        if base_after != base_before:
            product.base_price = base_after

    log_action(
        db, product, "sku_price",
        {
            "changed": len(changes),
            "skus": len(product.skus),
            "spans": spans,
            "base_price_from": str(base_before),
            "base_price_to": str(product.base_price),
            "items": changes,
            "reason": payload.reason,
        },
        admin.username,
    )
    await db.commit()
    await db.refresh(product)
    return product


@router.get("/products/{product_id}/stock-movements",
            response_model=list[SKUStockMovementOut])
async def product_stock_movements(
    product_id: int,
    limit: int = Query(50, ge=1, le=200),
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """某商品的库存修改记录（库存流水，最新优先）——后台「查看修改记录」"""
    product = await _load_product(db, product_id)
    sku_map = {s.id: s for s in (product.skus or [])}
    if not sku_map:
        return []
    rows = (
        await db.execute(
            select(StockMovement)
            .where(StockMovement.sku_id.in_(list(sku_map)))
            .order_by(StockMovement.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        SKUStockMovementOut(
            id=m.id,
            sku_id=m.sku_id,
            sku_code=sku_map[m.sku_id].sku_code if m.sku_id in sku_map else "",
            attributes=sku_map[m.sku_id].attributes if m.sku_id in sku_map else {},
            change_qty=m.change_qty,
            balance_after=m.balance_after,
            reason=m.reason or "",
            reference=m.reference,
            operator=m.operator,
            created_at=m.created_at,
        )
        for m in rows
    ]


@router.put("/products/{product_id}/sku-stocks", response_model=ProductOut)
async def update_sku_stocks(
    product_id: int,
    payload: ProductSKUStockIn,
    admin: AdminUser = Depends(require_admin(WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """按规格（SKU）批量改库存 —— 后台「修改库存」弹窗提交。

    - `mode=add`：在每个规格当前库存上增减 `value`（可为负）
    - `mode=set`：把每个规格库存设为 `value`

    每次变更都写入库存流水（StockMovement）与审计日志。
    """
    if payload.mode not in ("set", "add"):
        raise HTTPException(status_code=400, detail="mode 必须是 set 或 add")
    product = await _load_product(db, product_id)
    items = _sku_items_or_400(product, payload.items)

    before_total = sum(s.stock or 0 for s in (product.skus or []) if s.is_active)
    changes: list[dict] = []
    for item, sku in items:
        before = sku.stock or 0
        after = item.value if payload.mode == "set" else before + item.value
        if after < 0:
            raise HTTPException(
                status_code=400,
                detail=f"规格 {sku.sku_code} 减少后库存不能为负（当前 {before}）",
            )
        if after == before:
            continue
        sku.stock = after
        db.add(StockMovement(
            sku_id=sku.id, change_qty=after - before, balance_after=after,
            reason=payload.reason or "quick_edit", reference="",
            operator=admin.username,
        ))
        changes.append({
            "sku_code": sku.sku_code,
            "from": before,
            "to": after,
        })

    if not changes:
        return product

    after_total = sum(s.stock or 0 for s in (product.skus or []) if s.is_active)
    deltas = {c["to"] - c["from"] for c in changes}
    log_action(
        db, product, "sku_stock",
        {
            "mode": payload.mode,
            # 各规格增减量一致时记录该口径值，否则为 None（摘要改看合计）
            "value": deltas.pop() if len(deltas) == 1 else None,
            "changed": len(changes),
            "from_total": before_total,
            "to_total": after_total,
            "items": changes,
            "reason": payload.reason,
        },
        admin.username,
    )
    await db.commit()
    await db.refresh(product)
    return product


# ---------------------------------------------------------------- 审计日志
def log_action(
    db: AsyncSession,
    product: Product | None,
    action: str,
    detail: dict,
    operator: str,
    *,
    sku: str = "",
    name: str = "",
) -> None:
    """写入一条商品审计日志（调用方负责 commit）"""
    db.add(
        ProductAuditLog(
            product_id=product.id if product else None,
            product_sku=sku or (product.sku_code if product else ""),
            product_name=name or (
                (product.name_i18n or {}).get("zh", "") if product else ""
            ),
            action=action,
            detail=detail,
            operator=operator,
        )
    )


# ---------------------------------------------------------------- 审核统计
@router.get("/products-review-stats", response_model=ProductReviewStatsOut)
async def review_stats(
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """商品审核概览：各状态数量与来源分布"""
    rows = (
        await db.execute(
            select(Product.review_status, func.count(Product.id)).group_by(
                Product.review_status
            )
        )
    ).all()
    by_status = {r[0]: int(r[1]) for r in rows}

    src_rows = (
        await db.execute(
            select(Product.source, func.count(Product.id)).group_by(Product.source)
        )
    ).all()
    by_source = {r[0]: int(r[1]) for r in src_rows}

    return ProductReviewStatsOut(
        total=sum(by_status.values()),
        pending=by_status.get("pending", 0),
        approved=by_status.get("approved", 0),
        rejected=by_status.get("rejected", 0),
        imported=by_source.get("import", 0),
        manual=by_source.get("manual", 0),
    )


# ---------------------------------------------------------------- 单商品审核
@router.post("/products/{product_id}/review", response_model=ProductOut)
async def review_product(
    product_id: int,
    payload: ProductReviewIn,
    admin: AdminUser = Depends(require_admin(WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """审核单个商品：通过 / 驳回 / 重置为待审核"""
    target = REVIEW_ACTIONS.get(payload.action)
    if not target:
        raise HTTPException(
            status_code=400,
            detail=f"action 必须是 {'/'.join(REVIEW_ACTIONS)} 之一",
        )

    product = (
        await db.execute(
            select(Product)
            .options(selectinload(Product.skus))   # 返回 ProductOut 需要 skus，异步下需预加载
            .where(Product.id == product_id)
        )
    ).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")

    if target == "rejected" and not payload.note.strip():
        raise HTTPException(status_code=400, detail="驳回必须填写理由（note）")

    before = product.review_status
    product.review_status = target
    product.review_note = payload.note.strip() or None
    product.reviewed_by = admin.username
    product.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)

    log_action(
        db, product, "review",
        {"from": before, "to": target, "note": payload.note.strip()},
        admin.username,
    )
    await db.commit()
    await db.refresh(product)
    return product


@router.get("/products/{product_id}/audit-logs", response_model=list[ProductAuditLogOut])
async def product_audit_logs(
    product_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    action: str | None = None,
    q: str | None = None,
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """查看某个商品的操作/审核记录（支持分页与动作筛选）"""
    stmt = select(ProductAuditLog).where(
        ProductAuditLog.product_id == product_id
    )
    if action:
        stmt = stmt.where(ProductAuditLog.action == action)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(ProductAuditLog.product_name.ilike(like))
    stmt = stmt.order_by(ProductAuditLog.id.desc()).offset(offset).limit(limit)
    return (await db.execute(stmt)).scalars().all()


def _audit_filter(
    stmt,
    *,
    action: str | None,
    operator: str | None,
    q: str | None,
    product_id: int | None,
):
    """给审计日志查询套上统一过滤条件（action 为 None 时不按动作过滤）"""
    if product_id is not None:
        stmt = stmt.where(ProductAuditLog.product_id == product_id)
    if action:
        stmt = stmt.where(ProductAuditLog.action == action)
    if operator:
        stmt = stmt.where(ProductAuditLog.operator == operator)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            ProductAuditLog.product_sku.ilike(like)
            | ProductAuditLog.product_name.ilike(like)
        )
    return stmt


@router.get("/product-audit-logs/meta", response_model=ProductAuditMetaOut)
async def audit_log_meta(
    product_id: int | None = None,
    action: str | None = None,
    operator: str | None = None,
    q: str | None = None,
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """日志筛选元信息：总数 + 各动作数量 + 操作人列表

    用于日志弹窗渲染「全部动作」下拉的数量角标与分页总数。
    各动作数量**不受 action 过滤影响**，否则下拉里只剩当前选中项。
    """
    base = _audit_filter(
        select(ProductAuditLog),
        action=action,
        operator=operator,
        q=q,
        product_id=product_id,
    )
    total = (
        await db.execute(
            select(func.count()).select_from(base.subquery())
        )
    ).scalar_one()

    counts_stmt = _audit_filter(
        select(ProductAuditLog.action, func.count(ProductAuditLog.id)),
        action=None,  # 关键：动作计数不受动作过滤影响
        operator=operator,
        q=q,
        product_id=product_id,
    ).group_by(ProductAuditLog.action)
    rows = (await db.execute(counts_stmt)).all()
    actions = sorted(
        (
            ProductAuditActionOut(
                action=a, label=audit_action_label(a), count=int(c)
            )
            for a, c in rows
        ),
        key=lambda x: -x.count,
    )

    ops = _audit_filter(
        select(ProductAuditLog.operator),
        action=action,
        operator=operator,
        q=q,
        product_id=product_id,
    ).distinct()
    operators = sorted(
        o for (o,) in (await db.execute(ops)).all() if o
    )
    return ProductAuditMetaOut(total=int(total), actions=actions, operators=operators)


@router.get("/product-audit-logs", response_model=list[ProductAuditLogOut])
async def all_audit_logs(
    action: str | None = None,
    operator: str | None = None,
    q: str | None = None,
    product_id: int | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """全局商品操作日志（可按动作/操作人/商品/货号或名称过滤，支持分页）"""
    stmt = _audit_filter(
        select(ProductAuditLog),
        action=action,
        operator=operator,
        q=q,
        product_id=product_id,
    )
    stmt = stmt.order_by(ProductAuditLog.id.desc()).offset(offset).limit(limit)
    return (await db.execute(stmt)).scalars().all()


# ---------------------------------------------------------------- 批量管理
@router.post("/products/bulk", response_model=ProductBulkResultOut)
async def bulk_products(
    payload: ProductBulkIn,
    admin: AdminUser = Depends(require_admin(WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """商品批量管理：上架 / 下架 / 审核 / 改分类 / 推荐 / 删除 / 恢复 / 彻底删除

    返回每个动作的统计与失败原因；不存在的 ID 计入 `skipped`。
    上架会对每个商品做前置体检，不满足的单独计入 `failed` 并说明原因。
    """
    ids = list(dict.fromkeys(payload.ids))          # 去重且保持顺序
    action = payload.action
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    ALLOWED = ("status", "publish", "unpublish", "review", "category",
               "featured", "delete", "restore", "purge")

    # ---------- 先校验参数（与商品是否存在无关，避免非法请求被静默放过）----------
    if action not in ALLOWED:
        raise HTTPException(
            status_code=400, detail=f"action 必须是 {' / '.join(ALLOWED)}",
        )

    status_value = ""
    review_target = ""
    if action == "status":
        status_value = str(payload.value or "")
        if status_value not in VALID_STATUS:
            raise HTTPException(
                status_code=400, detail=f"value 必须是 {'/'.join(sorted(VALID_STATUS))}"
            )
    elif action == "review":
        review_target = REVIEW_ACTIONS.get(str(payload.value or ""), "")
        if not review_target:
            raise HTTPException(
                status_code=400,
                detail=f"value 必须是 {'/'.join(sorted(REVIEW_ACTIONS))}",
            )
        if review_target == "rejected" and not payload.note.strip():
            raise HTTPException(status_code=400, detail="批量驳回必须填写理由（note）")
    elif action == "category" and payload.category_id is not None:
        exists = (
            await db.execute(
                select(Category).where(Category.id == payload.category_id)
            )
        ).scalar_one_or_none()
        if not exists:
            raise HTTPException(status_code=400, detail="目标分类不存在")
    elif action == "purge" and admin.role.value != "superadmin":
        raise HTTPException(status_code=403, detail="彻底删除仅限超级管理员")

    result = ProductBulkResultOut(
        action=action, requested=len(ids), succeeded=0, skipped=0, failed=0,
    )

    # 上架/恢复需要 SKU 明细来体检，其余动作不需要
    stmt = select(Product).where(Product.id.in_(ids))
    if action == "publish":
        stmt = stmt.options(selectinload(Product.skus))
    products = (await db.execute(stmt)).scalars().all()
    found = {p.id: p for p in products}
    missing = [i for i in ids if i not in found]
    if missing:
        result.skipped += len(missing)
        result.errors.append(f"商品不存在，已跳过：{missing[:20]}")

    if not products:
        return result

    try:
        # ---------- 批量上下架（直接改状态，不做体检）----------
        if action == "status":
            status_note = payload.note.strip()
            for p in products:
                p.status = status_value
                # 下架原因：批量下架时写入，重新上架时清空（否则会残留旧原因）
                if status_value == "off_shelf":
                    p.off_shelf_reason = status_note or None
                elif status_value == "active":
                    p.off_shelf_reason = None
                _touch_status(p, now)
                log_action(db, p, "bulk_status",
                           {"to": status_value, "reason": status_note},
                           admin.username)
            result.succeeded = len(products)

        # ---------- 批量上架（逐个体检，不合格的计入 failed）----------
        elif action == "publish":
            for p in products:
                blockers = publish_blockers(p)
                if blockers:
                    result.failed += 1
                    result.errors.append(f"{p.sku_code}: {'；'.join(blockers)}")
                    continue
                before = p.status
                p.status = "active"
                p.off_shelf_reason = None
                _touch_status(p, now)
                log_action(db, p, "bulk_publish", {"from": before, "to": "active"},
                           admin.username)
                result.succeeded += 1

        # ---------- 批量下架 ----------
        elif action == "unpublish":
            reason = payload.note.strip()
            for p in products:
                if p.deleted_at is not None:
                    result.failed += 1
                    result.errors.append(f"{p.sku_code}: 在回收站中，无法下架")
                    continue
                before = p.status
                p.status = "off_shelf"
                p.off_shelf_reason = reason or None
                _touch_status(p, now)
                log_action(db, p, "bulk_unpublish",
                           {"from": before, "to": "off_shelf", "reason": reason},
                           admin.username)
                result.succeeded += 1

        # ---------- 批量审核 ----------
        elif action == "review":
            note = payload.note.strip()
            for p in products:
                before = p.review_status
                p.review_status = review_target
                p.review_note = note or None
                p.reviewed_by = admin.username
                p.reviewed_at = now
                log_action(
                    db, p, "bulk_review",
                    {"from": before, "to": review_target, "note": note}, admin.username,
                )
            result.succeeded = len(products)

        # ---------- 批量改分类 ----------
        elif action == "category":
            for p in products:
                before = p.category_id
                p.category_id = payload.category_id
                log_action(
                    db, p, "bulk_category",
                    {"from": before, "to": payload.category_id}, admin.username,
                )
            result.succeeded = len(products)

        # ---------- 批量推荐 ----------
        elif action == "featured":
            value = bool(payload.value)
            for p in products:
                p.is_featured = value
                log_action(db, p, "bulk_featured", {"to": value}, admin.username)
            result.succeeded = len(products)

        # ---------- 批量移入回收站（软删除，可恢复）----------
        elif action == "delete":
            for p in products:
                if p.deleted_at is not None:
                    result.skipped += 1
                    continue
                p.deleted_at = now
                log_action(db, p, "bulk_delete",
                           {"sku_code": p.sku_code, "status": p.status,
                            "review_status": p.review_status, "soft": True},
                           admin.username)
                result.succeeded += 1
            result.product_ids = [p.id for p in products if p.deleted_at is not None]

        # ---------- 批量从回收站恢复 ----------
        elif action == "restore":
            for p in products:
                if p.deleted_at is None:
                    result.skipped += 1
                    continue
                p.deleted_at = None
                p.status = "off_shelf"
                _touch_status(p, now)
                log_action(db, p, "bulk_restore", {"to_status": "off_shelf"},
                           admin.username)
                result.succeeded += 1

        # ---------- 批量彻底删除（仅超管，不可恢复）----------
        else:   # action == "purge"
            eligible = [p for p in products if p.deleted_at is not None]
            result.skipped += len(products) - len(eligible)
            if products and len(eligible) != len(products):
                result.errors.append("仅回收站中的商品可彻底删除，其余已跳过")
            for p in eligible:
                log_action(
                    db, p, "purge",
                    {"sku_code": p.sku_code, "status": p.status,
                     "review_status": p.review_status},
                    admin.username,
                )
            ids_to_purge = [p.id for p in eligible]
            for p in eligible:
                await db.delete(p)
            await db.flush()
            result.succeeded = len(ids_to_purge)
            result.product_ids = ids_to_purge

    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.exception("批量操作失败：%s", action)
        raise HTTPException(status_code=500, detail=f"批量操作失败：{exc}") from exc

    await db.commit()
    if action not in ("delete", "purge"):
        result.product_ids = [p.id for p in products]
    logger.info(
        "[bulk] %s 由 %s 执行：成功 %s，跳过 %s，失败 %s",
        action, admin.username, result.succeeded, result.skipped, result.failed,
    )
    return result


@router.post("/products/bulk-publish-all", response_model=Message)
async def bulk_approve_all_pending(
    admin: AdminUser = Depends(require_admin(WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """一键通过全部「待审核」商品（导入后快速上架用）"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    pending = (
        await db.execute(
            select(Product).where(
                Product.review_status == "pending",
                Product.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    if not pending:
        return Message(message="没有待审核的商品")

    for p in pending:
        p.review_status = "approved"
        p.review_note = None
        p.reviewed_by = admin.username
        p.reviewed_at = now
        log_action(db, p, "bulk_review", {"from": "pending", "to": "approved"},
                   admin.username, name="")
    await db.commit()
    logger.info("[bulk] %s 一键通过 %s 款待审核商品", admin.username, len(pending))
    return Message(message=f"已通过 {len(pending)} 款商品")


@router.put("/products/{product_id}/review-status", response_model=Message)
async def set_review_status(
    product_id: int,
    payload: ProductReviewIn,
    admin: AdminUser = Depends(require_admin(WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """兼容别名：语义同 `POST /products/{id}/review`"""
    await review_product(product_id, payload, admin, db)
    return Message(message="审核完成")


# ---------------------------------------------------------------- 接口文档
_PRODUCT_API_DOCS: list[dict] = [
    {
        "group": "认证",
        "items": [
            {
                "method": "POST", "path": "/api/admin/login",
                "summary": "管理员登录，获取 access_token",
                "auth": "无",
                "body": {"username": "admin", "password": "admin123"},
                "curl": "curl -X POST $BASE/api/admin/login -H 'Content-Type: application/json' "
                        "-d '{\"username\":\"admin\",\"password\":\"admin123\"}'",
            },
        ],
    },
    {
        "group": "商品查询（状态标签页 / 多字段搜索）",
        "items": [
            {
                "method": "GET", "path": "/api/admin/products",
                "summary": "商品列表（后台含下架/待审核/回收站）",
                "auth": "管理员",
                "query": {
                    "tab": "状态标签页：all / on_sale / off_shelf / sold_out / "
                           "pending / rejected / draft / deleted",
                    "product_id": "商品 ID，支持空格或逗号分隔多个",
                    "sku_code": "规格编码（SKU）模糊匹配",
                    "q": "按货号或中英文名称模糊搜索",
                    "category_id": "分类 ID",
                    "source": "manual / import",
                    "status": "active / draft / off_shelf",
                    "review_status": "pending / approved / rejected",
                    "page": "页码，默认 1",
                    "page_size": "每页条数，默认 20，最大 200",
                    "limit": ">0 时忽略分页，直接返回前 N 条（兼容旧调用）",
                },
                "curl": "curl \"$BASE/api/admin/products?tab=on_sale&page=1&page_size=20\" -H \"$AUTH\"",
            },
            {
                "method": "GET", "path": "/api/admin/products-status-counts",
                "summary": "各状态标签页的商品数量（标签角标）",
                "auth": "管理员",
                "curl": "curl $BASE/api/admin/products-status-counts -H \"$AUTH\"",
            },
            {
                "method": "GET", "path": "/api/admin/products-review-stats",
                "summary": "审核概览：各审核状态与来源数量",
                "auth": "管理员",
                "curl": "curl $BASE/api/admin/products-review-stats -H \"$AUTH\"",
            },
        ],
    },
    {
        "group": "上下架与状态流转",
        "items": [
            {
                "method": "GET", "path": "/api/admin/products/{id}/publish-check",
                "summary": "上架前体检：返回阻止项 blockers 与建议项 warnings",
                "auth": "管理员",
                "curl": "curl $BASE/api/admin/products/123/publish-check -H \"$AUTH\"",
            },
            {
                "method": "POST", "path": "/api/admin/products/{id}/publish",
                "summary": "上架（先体检，不满足返回 400 并列出原因）",
                "auth": "superadmin / operator",
                "body": {},
                "curl": "curl -X POST $BASE/api/admin/products/123/publish -H \"$AUTH\"",
            },
            {
                "method": "POST", "path": "/api/admin/products/{id}/unpublish",
                "summary": "下架（可填写下架原因，写入审计日志）",
                "auth": "superadmin / operator",
                "body": {"reason": "换季清仓"},
                "curl": "curl -X POST $BASE/api/admin/products/123/unpublish -H \"$AUTH\" "
                        "-H 'Content-Type: application/json' -d '{\"reason\":\"换季清仓\"}'",
            },
        ],
    },
    {
        "group": "回收站（软删除）",
        "items": [
            {
                "method": "DELETE", "path": "/api/admin/products/{id}",
                "summary": "移入回收站（软删除，可恢复）",
                "auth": "superadmin / operator",
                "curl": "curl -X DELETE $BASE/api/admin/products/123 -H \"$AUTH\"",
            },
            {
                "method": "POST", "path": "/api/admin/products/{id}/restore",
                "summary": "从回收站恢复（恢复后为「已下架」，需重新上架）",
                "auth": "superadmin / operator",
                "curl": "curl -X POST $BASE/api/admin/products/123/restore -H \"$AUTH\"",
            },
            {
                "method": "DELETE", "path": "/api/admin/products/{id}/purge",
                "summary": "彻底删除（仅超管，不可恢复，级联删除 SKU）",
                "auth": "superadmin",
                "curl": "curl -X DELETE $BASE/api/admin/products/123/purge -H \"$AUTH\"",
            },
        ],
    },
    {
        "group": "行内快捷编辑",
        "items": [
            {
                "method": "PUT", "path": "/api/admin/products/{id}/price",
                "summary": "改价（可选同步全部 SKU 价格）",
                "auth": "superadmin / operator",
                "body": {"base_price": 159, "sync_skus": True, "reason": "促销"},
                "curl": "curl -X PUT $BASE/api/admin/products/123/price -H \"$AUTH\" "
                        "-H 'Content-Type: application/json' "
                        "-d '{\"base_price\":159,\"sync_skus\":true}'",
            },
            {
                "method": "PUT", "path": "/api/admin/products/{id}/stock",
                "summary": "改库存（按「商品总库存」语义，均摊到全部启用 SKU，写库存流水）",
                "auth": "superadmin / operator",
                "body": {"mode": "set | add", "value": 500, "reason": "补货"},
                "curl": "curl -X PUT $BASE/api/admin/products/123/stock -H \"$AUTH\" "
                        "-H 'Content-Type: application/json' "
                        "-d '{\"mode\":\"set\",\"value\":500}'",
            },
            {
                "method": "PUT", "path": "/api/admin/products/{id}/sku-prices",
                "summary": "按规格批量改价（后台「修改价格」弹窗，不含拼单价；"
                           "基础价自动跟随最低启用规格价）",
                "auth": "superadmin / operator",
                "body": {
                    "items": [{"sku_id": 1959857926508, "price": 51.84}],
                    "reason": "促销",
                },
                "curl": "curl -X PUT $BASE/api/admin/products/123/sku-prices -H \"$AUTH\" "
                        "-H 'Content-Type: application/json' "
                        "-d '{\"items\":[{\"sku_id\":1959857926508,\"price\":51.84}]}'",
            },
            {
                "method": "PUT", "path": "/api/admin/products/{id}/sku-stocks",
                "summary": "按规格批量改库存（mode=add 增减 / mode=set 设为，逐 SKU 写库存流水）",
                "auth": "superadmin / operator",
                "body": {
                    "mode": "add",
                    "items": [{"sku_id": 1959857926508, "value": 10}],
                    "reason": "补货",
                },
                "curl": "curl -X PUT $BASE/api/admin/products/123/sku-stocks -H \"$AUTH\" "
                        "-H 'Content-Type: application/json' "
                        "-d '{\"mode\":\"add\",\"items\":[{\"sku_id\":1959857926508,\"value\":10}]}'",
            },
            {
                "method": "GET", "path": "/api/admin/products/{id}/stock-movements",
                "summary": "该商品的库存修改记录（库存流水，最新优先）",
                "auth": "superadmin / operator / viewer",
                "query": {"limit": "返回条数，1~200，默认 50"},
                "curl": "curl \"$BASE/api/admin/products/123/stock-movements?limit=50\" "
                        "-H \"$AUTH\"",
            },
        ],
    },
    {
        "group": "商品增删改（人工管理）",
        "items": [
            {
                "method": "POST", "path": "/api/admin/products",
                "summary": "新增商品（可同时创建 SKU）",
                "auth": "superadmin / operator",
                "body": {
                    "sku_code": "SKU-001", "name_zh": "商品名", "name_en": "Name",
                    "base_price": 199, "category_id": 1,
                    "main_image": "/static/uploads/xxx.jpg", "images": [],
                    "status": "active", "is_featured": False,
                    "skus": [{"sku_code": "SKU-001-001", "price": 199, "stock": 100,
                              "attributes": {"颜色": "黑", "尺码": "M"}}],
                },
                "curl": "curl -X POST $BASE/api/admin/products -H \"$AUTH\" "
                        "-H 'Content-Type: application/json' -d @product.json",
            },
            {
                "method": "PUT", "path": "/api/admin/products/{id}",
                "summary": "更新商品（仅传需要修改的字段）",
                "auth": "superadmin / operator",
                "body": {"name_zh": "新名称", "base_price": 259, "status": "active",
                         "category_id": 2, "is_featured": True},
                "curl": "curl -X PUT $BASE/api/admin/products/123 -H \"$AUTH\" "
                        "-H 'Content-Type: application/json' "
                        "-d '{\"base_price\":259}'",
            },
            {
                "method": "DELETE", "path": "/api/admin/products/{id}",
                "summary": "删除商品（级联删除其 SKU）",
                "auth": "superadmin / operator",
                "curl": "curl -X DELETE $BASE/api/admin/products/123 -H \"$AUTH\"",
            },
            {
                "method": "POST", "path": "/api/admin/products/{id}/skus",
                "summary": "为商品新增 SKU",
                "auth": "superadmin / operator",
                "body": {"sku_code": "SKU-001-009", "price": 199, "stock": 50,
                         "attributes": {"颜色": "白", "尺码": "L"}},
                "curl": "curl -X POST $BASE/api/admin/products/123/skus -H \"$AUTH\" "
                        "-H 'Content-Type: application/json' -d @sku.json",
            },
            {
                "method": "PUT", "path": "/api/admin/skus/{sku_id}/stock",
                "summary": "调整 SKU 库存（写入库存流水）",
                "auth": "superadmin / operator",
                "body": {"stock": 200, "reason": "manual_adjust"},
                "curl": "curl -X PUT $BASE/api/admin/skus/456/stock -H \"$AUTH\" "
                        "-H 'Content-Type: application/json' -d '{\"stock\":200}'",
            },
        ],
    },
    {
        "group": "批量导入 / 更新",
        "items": [
            {
                "method": "POST", "path": "/api/admin/import/products",
                "summary": "上传产品册 PPT 批量导入（支持预览）",
                "auth": "superadmin / operator",
                "form": {
                    "file": "产品册 .pptx（必填，≤200MB）",
                    "mode": "merge=仅新增（默认） / replace=清空后全量",
                    "dry_run": "true 只预览不写库",
                    "review_status": "导入后审核状态：pending（默认）/ approved",
                    "save_swatches": "是否保存色卡/logo 小图",
                    "clean_images": "导入前清空 static/uploads/jyt",
                    "featured_every": "每 N 款标推荐，0 表示不标",
                    "preview_limit": "返回预览条数，0 表示全部",
                },
                "curl": "curl -X POST $BASE/api/admin/import/products -H \"$AUTH\" "
                        "-F 'file=@产品册.pptx' -F 'mode=merge' -F 'dry_run=true'",
            },
            {
                "method": "GET", "path": "/api/admin/import/format",
                "summary": "查看产品册 PPT 的字段解析规则与分类映射",
                "auth": "管理员",
                "curl": "curl $BASE/api/admin/import/format -H \"$AUTH\"",
            },
        ],
    },
    {
        "group": "批量管理",
        "items": [
            {
                "method": "POST", "path": "/api/admin/products/bulk",
                "summary": "批量操作：上下架 / 审核 / 改分类 / 推荐 / 删除",
                "auth": "superadmin / operator",
                "body": {
                    "ids": [1, 2, 3],
                    "action": "status | publish | unpublish | review | category | "
                              "featured | delete | restore | purge",
                    "value": "action 对应的值（status→active/draft/off_shelf；"
                             "review→approve/reject/pending；featured→true/false）",
                    "category_id": "action=category 时的目标分类 ID",
                    "note": "驳回理由 / 下架原因",
                },
                "curl": "# 批量上架（逐个做上架体检）\n"
                        "curl -X POST $BASE/api/admin/products/bulk -H \"$AUTH\" "
                        "-H 'Content-Type: application/json' "
                        "-d '{\"ids\":[1,2,3],\"action\":\"publish\"}'\n\n"
                        "# 批量下架（带原因）\n"
                        "curl -X POST $BASE/api/admin/products/bulk -H \"$AUTH\" "
                        "-H 'Content-Type: application/json' "
                        "-d '{\"ids\":[1,2,3],\"action\":\"unpublish\",\"note\":\"换季\"}'\n\n"
                        "# 批量审核通过\n"
                        "curl -X POST $BASE/api/admin/products/bulk -H \"$AUTH\" "
                        "-H 'Content-Type: application/json' "
                        "-d '{\"ids\":[1,2,3],\"action\":\"review\",\"value\":\"approve\"}'\n\n"
                        "# 批量移入回收站 / 恢复 / 彻底删除\n"
                        "curl -X POST $BASE/api/admin/products/bulk -H \"$AUTH\" "
                        "-H 'Content-Type: application/json' "
                        "-d '{\"ids\":[1,2,3],\"action\":\"delete\"}'\n"
                        "curl -X POST $BASE/api/admin/products/bulk -H \"$AUTH\" "
                        "-H 'Content-Type: application/json' "
                        "-d '{\"ids\":[1,2,3],\"action\":\"restore\"}'\n"
                        "curl -X POST $BASE/api/admin/products/bulk -H \"$AUTH\" "
                        "-H 'Content-Type: application/json' "
                        "-d '{\"ids\":[1,2,3],\"action\":\"purge\"}'",
            },
            {
                "method": "POST", "path": "/api/admin/products/bulk-publish-all",
                "summary": "一键通过全部待审核商品",
                "auth": "superadmin / operator",
                "curl": "curl -X POST $BASE/api/admin/products/bulk-publish-all -H \"$AUTH\"",
            },
        ],
    },
    {
        "group": "人工审核",
        "items": [
            {
                "method": "POST", "path": "/api/admin/products/{id}/review",
                "summary": "审核单个商品",
                "auth": "superadmin / operator",
                "body": {"action": "approve | reject | pending",
                         "note": "驳回时必填的审核意见"},
                "curl": "curl -X POST $BASE/api/admin/products/123/review -H \"$AUTH\" "
                        "-H 'Content-Type: application/json' "
                        "-d '{\"action\":\"approve\",\"note\":\"图片合规\"}'",
            },
        ],
    },
    {
        "group": "操作审计",
        "items": [
            {
                "method": "GET", "path": "/api/admin/product-audit-logs",
                "summary": "全局商品操作日志（分页）",
                "auth": "管理员",
                "query": {"action": "按动作过滤（如 publish / quick_stock）",
                          "operator": "按操作人过滤",
                          "q": "按货号/名称模糊搜索",
                          "product_id": "只看某个商品",
                          "limit": "每页条数，默认 100，最大 500",
                          "offset": "偏移量（分页），默认 0"},
                "curl": "# 第 1 页\n"
                        "curl \"$BASE/api/admin/product-audit-logs?limit=50\" -H \"$AUTH\"\n\n"
                        "# 第 2 页\n"
                        "curl \"$BASE/api/admin/product-audit-logs?limit=50&offset=50\" -H \"$AUTH\"",
            },
            {
                "method": "GET", "path": "/api/admin/product-audit-logs/meta",
                "summary": "日志筛选元信息（总数 / 各动作数量 / 操作人列表）",
                "auth": "管理员",
                "query": {"product_id": "限定某个商品", "action": "选定动作后 total 收窄",
                          "operator": "按操作人过滤", "q": "按货号/名称模糊搜索"},
                "curl": "curl \"$BASE/api/admin/product-audit-logs/meta\" -H \"$AUTH\"",
            },
            {
                "method": "GET", "path": "/api/admin/products/{id}/audit-logs",
                "summary": "单个商品的操作/审核记录（分页）",
                "auth": "管理员",
                "query": {"action": "按动作过滤", "limit": "默认 50，最大 200",
                          "offset": "偏移量，默认 0"},
                "curl": "curl \"$BASE/api/admin/products/123/audit-logs?limit=50\" -H \"$AUTH\"",
            },
        ],
    },
]

_AUTH_HEADER = "Authorization: Bearer <access_token>"


@router.get("/api-docs/products")
async def product_api_docs(
    admin: AdminUser = Depends(require_admin()),
):
    """商品管理接口文档（供管理后台「接口文档」页展示）。

    返回结构化数据，前端按分组渲染；`curl` 字段中的 `$BASE` 为服务地址、
    `$AUTH` 为认证头占位符。
    """
    return {
        "title": "商品管理接口文档",
        "version": "1.0",
        "base_url": "/api",
        "auth": {
            "type": "Bearer Token",
            "header": _AUTH_HEADER,
            "how_to_get": "调用 POST /api/admin/login 获取 access_token",
            "roles": {
                "读操作": "superadmin / operator / viewer",
                "写操作": "superadmin / operator",
            },
        },
        "review_workflow": {
            "field": "review_status",
            "values": {
                "pending": "待审核（批量导入默认）",
                "approved": "已通过 —— 前台可见",
                "rejected": "已驳回 —— 前台不可见，需填写理由",
            },
            "visibility_rule": "仅 status=active 且 review_status=approved 且未删除的商品在前台展示",
        },
        "product_status": {
            "description": "后台按单一「商品状态」分标签页管理，由 删除/审核/上下架/库存 派生",
            "values": {
                "on_sale": "在售中 —— 已上架且库存 > 0",
                "off_shelf": "已下架 —— 主动下架",
                "sold_out": "已售罄 —— 已上架但总库存为 0",
                "pending": "发布中 —— 等待审核",
                "rejected": "已驳回 —— 审核未通过",
                "draft": "草稿箱 —— 未提交上架",
                "deleted": "已删除 —— 在回收站，可恢复",
            },
            "publish_blockers": [
                "商品在回收站中", "审核未通过", "缺少商品主图",
                "没有 SKU 规格", "基础价 <= 0",
            ],
        },
        "groups": _PRODUCT_API_DOCS,
    }
