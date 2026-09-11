"""应用入口：注册路由、中间件、静态文件，启动时建表与初始化种子数据"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import engine
from app.i18n import I18nMiddleware
from app.routers import (
    addresses,
    admin,
    auth,
    cart,
    catalog,
    cms,
    newsletter,
    orders,
    payments,
    reviews,
    uploads,
    wishlist,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化数据库表与种子数据"""
    from app.database import Base, run_light_migrations
    from app import models  # noqa: F401  确保模型注册

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await run_light_migrations()

    from app.seed import seed_all

    await seed_all()
    yield
    await engine.dispose()


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    lifespan=lifespan,
)

# 中间件：多语言解析
app.add_middleware(I18nMiddleware)

# API 路由
app.include_router(catalog.router)
app.include_router(auth.router)
app.include_router(cart.router)
app.include_router(orders.router)
app.include_router(payments.router)
app.include_router(reviews.router)
app.include_router(wishlist.router)
app.include_router(addresses.router)
app.include_router(wishlist.router)
app.include_router(cms.router)
app.include_router(newsletter.router)
app.include_router(uploads.router)
app.include_router(admin.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME}


# 前端静态文件
# 注意：HTML 中引用的资源带 /static 前缀（如 /static/css/style.css），
# 因此先将静态目录挂载到 /static，再把首页等页面挂到根路径 /。
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/", StaticFiles(directory="static", html=True), name="home")