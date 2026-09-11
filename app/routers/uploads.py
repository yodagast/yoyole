"""文件上传接口：商品图片、站点轮播图片/视频等资源管理

- 上传文件保存到 `static/uploads/` 下，按日期分目录（YYYY/MM/DD）
- 图片与视频分别校验扩展名与大小（图片 5MB、视频 50MB）
- 返回可公开访问的 URL（如 /static/uploads/2026/08/22/xxx.png）
"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.deps import get_current_customer, require_admin
from app.models import AdminUser

router = APIRouter(prefix="/api", tags=["uploads"])

# 上传根目录（相对项目根，挂载于 /static/uploads）
UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "uploads"

ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".avif"}
ALLOWED_VIDEO_EXT = {".mp4", ".webm", ".mov", ".m3u8"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024        # 5MB
MAX_VIDEO_SIZE = 100 * 1024 * 1024      # 100MB

_CONTENT_TYPE_HINT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/avif": ".avif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}


def _safe_ext(filename: str, content_type: str | None) -> str:
    """根据文件名后缀或 content-type 推导安全扩展名"""
    suffix = Path(filename or "").suffix.lower()
    if suffix in ALLOWED_IMAGE_EXT | ALLOWED_VIDEO_EXT:
        return suffix
    if content_type in _CONTENT_TYPE_HINT:
        return _CONTENT_TYPE_HINT[content_type]
    raise HTTPException(status_code=400, detail="不支持的文件类型")


@router.get("/uploads-list")
async def list_uploaded_files(
    admin: AdminUser = Depends(require_admin()),
):
    """（管理用）列出已上传的图片/视频 URL"""
    if not UPLOAD_DIR.exists():
        return []
    out = []
    for path in sorted(UPLOAD_DIR.rglob("*")):
        if path.is_file() and path.suffix.lower() in ALLOWED_IMAGE_EXT | ALLOWED_VIDEO_EXT:
            rel = path.relative_to(UPLOAD_DIR).as_posix()
            out.append(f"/static/uploads/{rel}")
    return out


@router.post("/upload", status_code=201)
async def upload_file(
    kind: str = "image",  # image | video
    file: UploadFile = File(...),
    admin: AdminUser = Depends(require_admin()),
):
    """上传图片或视频，返回 {url}"""
    if kind not in ("image", "video"):
        raise HTTPException(status_code=400, detail="kind 必须是 image 或 video")

    ext = _safe_ext(file.filename or "", file.content_type)
    allowed = ALLOWED_IMAGE_EXT if kind == "image" else ALLOWED_VIDEO_EXT
    if ext not in allowed:
        raise HTTPException(status_code=400, detail=f"{kind} 类型不允许：{ext or '未知'}")

    # 严格校验：视频必须声明合法 content-type（防伪装文件）
    if kind == "video" and file.content_type not in {
        "video/mp4", "video/webm", "video/quicktime", "application/vnd.apple.mpegurl",
    }:
        raise HTTPException(status_code=400, detail="视频 content-type 不合法")

    return await _save_upload(file, kind, ext)


async def _save_upload(file: UploadFile, kind: str, ext: str) -> dict:
    """校验后的上传文件落盘并返回公开 URL。"""
    limit = MAX_IMAGE_SIZE if kind == "image" else MAX_VIDEO_SIZE
    now = datetime.now()
    day_dir = UPLOAD_DIR / now.strftime("%Y/%m/%d")
    day_dir.mkdir(parents=True, exist_ok=True)

    fname = f"{uuid.uuid4().hex}{ext}"
    dest = day_dir / fname
    written = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise HTTPException(status_code=413, detail=f"文件过大（上限 {limit // (1024*1024)}MB）")
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="文件保存失败")

    await file.close()
    url = f"/static/uploads/{now.strftime('%Y/%m/%d')}/{fname}"
    return {"url": url, "kind": kind, "size": written}


@router.post("/user-story-upload", status_code=201)
async def upload_user_story_image(
    file: UploadFile = File(...),
    customer=Depends(get_current_customer),
):
    """登录用户上传用户故事配图。"""
    ext = _safe_ext(file.filename or "", file.content_type)
    if ext not in ALLOWED_IMAGE_EXT:
        raise HTTPException(status_code=400, detail=f"图片类型不允许：{ext or '未知'}")
    return await _save_upload(file, "image", ext)