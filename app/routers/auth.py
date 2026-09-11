"""客户认证路由：邮箱验证码、注册、登录、当前用户"""
from __future__ import annotations

import random
import string
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.deps import get_current_customer
from app.mailer import send_email
from app.models import Customer, EmailVerifyCode
from app.schemas import (
    CustomerOut,
    CustomerUpdateIn,
    LoginIn,
    Message,
    RegisterIn,
    ResetPasswordIn,
    SendVerifyCodeIn,
    TokenOut,
)
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

_NOW = lambda: datetime.now(timezone.utc).replace(tzinfo=None)


def _gen_code(length: int | None = None) -> str:
    """生成纯数字验证码"""
    n = length or settings.EMAIL_CODE_LENGTH
    return "".join(random.choices(string.digits, k=n))


async def _send_code(db: AsyncSession, email: str, purpose: str = "register") -> dict:
    """生成并发送邮箱验证码，同时作废该邮箱之前的验证码。

    返回 dict，含 send 结果：
    - sent: 是否真实通过 SMTP 发出
    - debug_code: 开发模式（未配置 SMTP，仅打印日志）时回传验证码，便于本地体验
    """
    email = email.lower()
    now = _NOW()

    # 作废旧码，防止多个有效验证码
    await db.execute(
        delete(EmailVerifyCode).where(
            EmailVerifyCode.email == email,
            EmailVerifyCode.purpose == purpose,
        )
    )

    code = _gen_code()
    expires = now + timedelta(minutes=settings.EMAIL_CODE_EXPIRE_MINUTES)
    db.add(
        EmailVerifyCode(
            email=email, code=code, purpose=purpose, expires_at=expires
        )
    )
    await db.commit()

    if purpose == "reset":
        subject = "PyMall 重置密码验证码"
        intro = "您好！您正在重置 PyMall 账号的登录密码。"
    else:
        subject = "PyMall 注册验证码"
        intro = "您好！您正在注册 PyMall 账号。"
    html = (
        f"<div style='font-family:sans-serif;padding:20px;'>"
        f"<h2 style='color:#d81e53;'>PyMall 邮箱验证码</h2>"
        f"<p>{intro}您的验证码是：</p>"
        f"<p style='font-size:28px;font-weight:700;letter-spacing:6px;color:#d81e53'>{code}</p>"
        f"<p style='color:#888;font-size:13px;'>验证码 {settings.EMAIL_CODE_EXPIRE_MINUTES} 分钟内有效，请勿泄露给他人。</p>"
        f"</div>"
    )
    body = f"{intro}\n您的验证码是：{code}\n验证码 {settings.EMAIL_CODE_EXPIRE_MINUTES} 分钟内有效，请勿泄露给他人。"
    sent, error = await send_email(email, subject, body, html)

    result: dict = {"sent": sent}
    # 开发模式（模拟发送）回传验证码，方便前端直接展示
    if not sent and not error and settings.DEBUG:
        result["debug_code"] = code
    if error:
        result["error"] = error
    return result


@router.post("/send-code", status_code=200)
async def send_code(payload: SendVerifyCodeIn, db: AsyncSession = Depends(get_db)):
    """向指定邮箱发送验证码（purge：注册 register / 重置密码 reset）"""
    email = payload.email.lower()
    if payload.purpose == "register":
        existing = await db.execute(select(Customer).where(Customer.email == email))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="邮箱已被注册")
    elif payload.purpose == "reset":
        existing = await db.execute(select(Customer).where(Customer.email == email))
        if not existing.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="该邮箱尚未注册")

    result = await _send_code(db, email, payload.purpose)
    resp = {"message": "验证码已发送", "email": email}
    # SMTP 发送失败：提示错误（并作废刚生成的验证码，避免用户拿无效码注册）
    if result.get("error"):
        await db.execute(
            delete(EmailVerifyCode).where(
                EmailVerifyCode.email == email,
                EmailVerifyCode.purpose == payload.purpose,
                EmailVerifyCode.used.is_(False),
            )
        )
        await db.commit()
        raise HTTPException(status_code=502, detail=f"验证码邮件发送失败，请稍后重试或联系客服")
    # 开发模式（未配置 SMTP）：回传验证码便于本地体验
    if result.get("debug_code"):
        resp["debug_code"] = result["debug_code"]
        resp["message"] = "验证码已发送（开发模式，见下方提示）"
    return resp


async def _verify_code(db: AsyncSession, email: str, code: str, purpose: str) -> EmailVerifyCode:
    """校验验证码（存在、未过期、未使用、匹配），通过则返回记录"""
    now = _NOW()
    result = await db.execute(
        select(EmailVerifyCode)
        .where(
            EmailVerifyCode.email == email,
            EmailVerifyCode.purpose == purpose,
            EmailVerifyCode.used.is_(False),
        )
        .order_by(EmailVerifyCode.id.desc())
    )
    vc = result.scalars().first()
    if not vc:
        raise HTTPException(status_code=400, detail="验证码不存在，请先获取验证码")
    if vc.expires_at < now:
        raise HTTPException(status_code=400, detail="验证码已过期，请重新获取")
    if vc.code != code.strip():
        raise HTTPException(status_code=400, detail="验证码错误")
    return vc


@router.post("/reset-password", response_model=Message)
async def reset_password(payload: ResetPasswordIn, db: AsyncSession = Depends(get_db)):
    """通过邮箱验证码重置密码"""
    email = payload.email.lower()
    customer = (
        await db.execute(select(Customer).where(Customer.email == email))
    ).scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="该邮箱尚未注册")

    vc = await _verify_code(db, email, payload.code, "reset")
    # 标记验证码已使用，更新密码
    vc.used = True
    customer.password_hash = hash_password(payload.new_password)
    await db.commit()
    return Message(message="密码已重置，请使用新密码登录")


@router.post("/register", response_model=TokenOut, status_code=201)
async def register(payload: RegisterIn, db: AsyncSession = Depends(get_db)):
    """注册新客户（需先通过邮箱验证码验证）"""
    email = payload.email.lower()

    existing = await db.execute(select(Customer).where(Customer.email == email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="邮箱已被注册")

    if not payload.code:
        raise HTTPException(status_code=400, detail="请先获取并填写邮箱验证码")

    vc = await _verify_code(db, email, payload.code, "register")
    # 标记验证码已使用
    vc.used = True

    customer = Customer(
        email=email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        phone=payload.phone,
        language=payload.language or "zh",
    )
    db.add(customer)
    await db.commit()
    await db.refresh(customer)

    token = create_access_token(subject=str(customer.id), extra={"type": "customer"})
    return TokenOut(access_token=token, customer=CustomerOut.model_validate(customer))


@router.post("/login", response_model=TokenOut)
async def login(payload: LoginIn, db: AsyncSession = Depends(get_db)):
    """客户登录"""
    result = await db.execute(select(Customer).where(Customer.email == payload.email.lower()))
    customer = result.scalar_one_or_none()
    if not customer or not verify_password(payload.password, customer.password_hash):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    if not customer.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用")

    customer.last_login = _NOW()
    await db.commit()

    token = create_access_token(subject=str(customer.id), extra={"type": "customer"})
    return TokenOut(access_token=token, customer=CustomerOut.model_validate(customer))


@router.get("/me", response_model=CustomerOut)
async def me(customer: Customer = Depends(get_current_customer)):
    """获取当前登录客户信息"""
    return CustomerOut.model_validate(customer)


@router.patch("/me", response_model=CustomerOut)
async def update_me(
    payload: CustomerUpdateIn,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """更新当前登录客户资料（姓名/手机号）"""
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(customer, field, value)
    await db.commit()
    await db.refresh(customer)
    return CustomerOut.model_validate(customer)