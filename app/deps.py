"""FastAPI 依赖：当前客户、当前管理员"""
from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AdminUser, Customer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="无效的凭证",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_customer(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> Customer:
    """从 JWT 解析当前登录客户，未登录返回匿名且 id=None 时由各接口自行处理"""
    if not token:
        # 兼容 Cookie 中的 token
        token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="未登录")

    try:
        from app.security import decode_access_token

        data = decode_access_token(token)
    except jwt.PyJWTError:
        raise CREDENTIALS_EXC

    if data.get("type") != "customer":
        raise CREDENTIALS_EXC

    customer_id = int(data.get("sub", 0))
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer or not customer.is_active:
        raise CREDENTIALS_EXC
    return customer


async def get_optional_customer(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> Customer | None:
    """获取可选客户（未登录返回 None）"""
    if not token:
        token = request.cookies.get("access_token")
    if not token:
        return None
    try:
        from app.security import decode_access_token

        data = decode_access_token(token)
    except jwt.PyJWTError:
        return None
    if data.get("type") != "customer":
        return None
    result = await db.execute(select(Customer).where(Customer.id == int(data.get("sub", 0))))
    return result.scalar_one_or_none()


def require_admin(required_roles: set[str] | None = None):
    """管理员权限依赖工厂"""

    async def _dependency(
        request: Request,
        token: str | None = Depends(oauth2_scheme),
        db: AsyncSession = Depends(get_db),
    ) -> AdminUser:
        if not token:
            token = request.cookies.get("admin_token")
        if not token:
            raise HTTPException(status_code=401, detail="未登录")
        from app.security import decode_access_token

        try:
            data = decode_access_token(token)
        except jwt.PyJWTError:
            raise CREDENTIALS_EXC
        if data.get("type") != "admin":
            raise CREDENTIALS_EXC

        result = await db.execute(select(AdminUser).where(AdminUser.id == int(data.get("sub", 0))))
        admin = result.scalar_one_or_none()
        if not admin or not admin.is_active:
            raise CREDENTIALS_EXC
        if required_roles and admin.role.value not in required_roles:
            raise HTTPException(status_code=403, detail="权限不足")
        return admin

    return _dependency