"""安全相关工具：密码哈希、JWT 签发与校验"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.config import settings

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    """生成带盐的密码哈希（PBKDF2-HMAC-SHA256）"""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000
    ).hex()
    return f"pbkdf2_sha256${salt}${digest}"


def verify_password(password: str, hashed: str) -> bool:
    """校验密码"""
    try:
        algo, salt, digest = hashed.split("$", 2)
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000
        ).hex()
        return secrets.compare_digest(calc, digest)
    except (ValueError, AttributeError):
        return False


def create_access_token(subject: str, extra: dict[str, Any] | None = None) -> str:
    """签发 JWT 访问令牌"""
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """解析并校验 JWT，失败抛 jwt.PyJWTError"""
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])