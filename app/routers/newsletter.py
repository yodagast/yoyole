"""新闻订阅路由：前端订阅邮箱 + 后台群发邮件"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_admin
from app.mailer import send_email
from app.models import AdminUser, NewsletterSubscriber
from app.schemas import Message, NewsletterSendIn, SubscribeIn, SubscriberOut

router = APIRouter(prefix="/api", tags=["newsletter"])


@router.post("/subscribe", response_model=Message, status_code=201)
async def subscribe(payload: SubscribeIn, db: AsyncSession = Depends(get_db)):
    """订阅优惠信息（页脚/关于页入口）"""
    email = payload.email.lower()
    existing = await db.execute(
        select(NewsletterSubscriber).where(NewsletterSubscriber.email == email)
    )
    sub = existing.scalar_one_or_none()
    if sub:
        if not sub.is_active:
            sub.is_active = True
            await db.commit()
        return Message(message="您已订阅，无需重复订阅")

    db.add(NewsletterSubscriber(email=email, is_active=True))
    await db.commit()
    return Message(message="订阅成功")


# ---------- 后台管理 ----------
@router.get("/admin/subscribers", response_model=list[SubscriberOut])
async def admin_list_subscribers(
    q: str | None = None,
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """订阅者列表"""
    stmt = select(NewsletterSubscriber).order_by(NewsletterSubscriber.id.desc())
    if q:
        stmt = stmt.where(NewsletterSubscriber.email.ilike(f"%{q}%"))
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/admin/subscribers/send", response_model=Message)
async def admin_send_newsletter(
    payload: NewsletterSendIn,
    admin: AdminUser = Depends(require_admin({"superadmin", "operator"})),
    db: AsyncSession = Depends(get_db),
):
    """向所有有效订阅者群发优惠邮件"""
    result = await db.execute(
        select(NewsletterSubscriber).where(NewsletterSubscriber.is_active.is_(True))
    )
    subscribers = result.scalars().all()
    if not subscribers:
        raise HTTPException(status_code=400, detail="暂无有效订阅者")

    html = (
        f"<div style='font-family:sans-serif;max-width:600px;margin:0 auto;'>"
        f"<div style='background:#d81e53;color:#fff;padding:16px 24px;font-size:20px;font-weight:700;'>PyMall 优惠订阅</div>"
        f"<div style='padding:24px;color:#333;'>{payload.content}</div>"
        f"<div style='padding:0 24px 24px;color:#999;font-size:12px;'>"
        f"此邮件由 PyMall 官方发送，如不想再收到，请联系客服退订。</div>"
        f"</div>"
    )

    success = 0
    failed = 0
    for s in subscribers:
        sent, error = await send_email(s.email, payload.subject, payload.content, html)
        if sent or error is None:  # 开发模式（模拟发送）也算成功
            success += 1
        else:
            failed += 1

    return Message(message=f"群发完成：成功 {success} 封，失败 {failed} 封")


@router.delete("/admin/subscribers/{subscriber_id}", response_model=Message)
async def admin_delete_subscriber(
    subscriber_id: int,
    admin: AdminUser = Depends(require_admin({"superadmin", "operator"})),
    db: AsyncSession = Depends(get_db),
):
    """删除订阅者"""
    result = await db.execute(
        select(NewsletterSubscriber).where(NewsletterSubscriber.id == subscriber_id)
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="订阅者不存在")
    await db.delete(sub)
    await db.commit()
    return Message(message="已删除")