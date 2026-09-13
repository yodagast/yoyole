"""pytest 全局 fixture：测试结束后自动清理测试产生的数据。

清理策略（通过 API 不可行——数据无删除接口，故直接操作数据库）：
- 测试商品：sku_code 前缀 `PYTEST-`（含 SKU、库存流水，级联删除）
- 测试分类：code 前缀 `pytest-`
- 测试账号：email 前缀 `test_` 且后缀 `@example.com`（含购物车、订单、支付，级联删除）
- 残留未完成订单（PENDING / PAID）：测试下单不支付会锁定库存，且订单表有外键
  约束（status 枚举含 REFUNDED，无 DELETE 接口），故统一取消并释放锁定库存
- 全量恢复：测试下单/支付会消耗 stock、累计 sales_count，测试开始时对全量
  SKU stock / locked_stock 与 Product sales_count 做快照，结束后按快照恢复，
  并删除测试期间创建的订单（order_items / payments 级联删除）
"""
from __future__ import annotations

import asyncio
import os

# 测试环境强制走「开发模式」邮件：不发真实邮件，验证码打印在服务日志/回传 debug_code。
# （pydantic-settings 环境变量优先级高于 .env，故此处覆盖 SMTP_USER 为空即可。）
os.environ["SMTP_USER"] = ""
os.environ["SMTP_HOST"] = ""

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models import (
    Address,
    AdminUser,
    Category,
    Customer,
    EmailVerifyCode,
    NewsletterSubscriber,
    Order,
    OrderItem,
    Product,
    Review,
    SKU,
    StockMovement,
    WishlistItem,
)


async def _snapshot_state() -> dict:
    """测试前快照：SKU 库存、商品销量、最大订单 id（用于删除测试订单）"""
    async with async_session_factory() as session:
        sku_rows = (
            await session.execute(select(SKU.id, SKU.stock, SKU.locked_stock))
        ).all()
        prod_rows = (
            await session.execute(select(Product.id, Product.sales_count))
        ).all()
        max_oid = (
            await session.execute(
                select(Order.id).order_by(Order.id.desc()).limit(1)
            )
        ).scalar()
        return {
            "sku_stock": {r[0]: (r[1], r[2]) for r in sku_rows},
            "prod_sales": {r[0]: r[1] for r in prod_rows},
            "max_order_id": max_oid or 0,
        }


async def _restore_state(snapshot: dict) -> None:
    """按快照恢复库存与销量，并删除测试期间创建的订单"""
    async with async_session_factory() as session:
        for sku_id, (stock, locked) in snapshot["sku_stock"].items():
            await session.execute(
                update(SKU)
                .where(SKU.id == sku_id)
                .values(stock=stock, locked_stock=locked)
            )
        for pid, sales in snapshot["prod_sales"].items():
            await session.execute(
                update(Product).where(Product.id == pid).values(sales_count=sales)
            )
        if snapshot["max_order_id"]:
            await session.execute(
                delete(Order).where(Order.id > snapshot["max_order_id"])
            )
        await session.commit()


async def _cleanup() -> None:
    async with async_session_factory() as session:
        session: AsyncSession
        # 先删库存流水（引用 SKU）
        await _delete_by_sku_prefix(session)
        # 新表：评价/收藏/地址（先删引用测试数据的，再由级联兜底）
        # 测试账号前缀：test_（通用）、rv_（评价测试专用）
        ids = await select_customer_ids(session)
        if ids:
            await session.execute(
                delete(Review).where(Review.customer_id.in_(ids))
            )
            await session.execute(
                delete(WishlistItem).where(WishlistItem.customer_id.in_(ids))
            )
            await session.execute(
                delete(Address).where(Address.customer_id.in_(ids))
            )
        # 邮箱验证码测试数据
        await session.execute(
            delete(EmailVerifyCode).where(
                EmailVerifyCode.email.like("code\\_%@example.com")
            )
        )
        await session.execute(
            delete(EmailVerifyCode).where(
                EmailVerifyCode.email.like("test\\_%@example.com")
            )
        )
        await session.execute(
            delete(EmailVerifyCode).where(
                EmailVerifyCode.email.like("rv\\_%@example.com")
            )
        )
        # 测试账号发出的验证码（防止残留）
        await session.execute(delete(Product).where(Product.sku_code.like("PYTEST-%")))
        await session.execute(delete(Category).where(Category.code.like("pytest-%")))
        # 订阅者测试数据
        await session.execute(
            delete(NewsletterSubscriber).where(
                NewsletterSubscriber.email.like("sub\\_%@example.com")
            )
        )
        # 测试管理员账号（ptest_ 前缀）
        await session.execute(
            delete(AdminUser).where(AdminUser.username.like("ptest\\_%"))
        )
        await session.execute(
            delete(Customer).where(Customer.email.like("test\\_%@example.com"))
        )
        await session.execute(
            delete(Customer).where(Customer.email.like("rv\\_%@example.com"))
        )
        # 取消所有未完成订单（PENDING/PAID）：它们是测试残留，会锁定库存。
        # 注意：先释放 locked_stock，再取消订单。
        await _release_locked_stock(session)
        await session.execute(
            update(Order)
            .where(Order.status.in_(["PENDING", "PAID"]))
            .values(status="CANCELLED")
        )
        await session.commit()


async def _release_locked_stock(session: AsyncSession) -> None:
    """释放未完成订单（PENDING/PAID）占用的 locked_stock"""
    # SELECT order_items 里属于未完成订单的 sku 占用
    rows = (
        await session.execute(
            OrderItem.__table__.select()
            .join(Order, Order.id == OrderItem.order_id)
            .where(Order.status.in_(["PENDING", "PAID"]))
        )
    ).all()
    per_sku: dict[int, int] = {}
    for row in rows:
        per_sku[row.sku_id] = per_sku.get(row.sku_id, 0) + row.quantity
    for sku_id, qty in per_sku.items():
        await session.execute(
            update(SKU)
            .where(SKU.id == sku_id)
            .values(locked_stock=SKU.locked_stock - qty)
        )


async def select_customer_ids(session: AsyncSession) -> list[int]:
    """返回测试账号的 customer_id 列表（test_ / rv_ 前缀）"""
    result = await session.execute(
        Customer.__table__.select().with_only_columns(Customer.id).where(
            (Customer.email.like("test\\_%@example.com"))
            | (Customer.email.like("rv\\_%@example.com"))
        )
    )
    return list(result.scalars().all())


async def _delete_by_sku_prefix(session: AsyncSession) -> None:
    """删除测试商品下所有 SKU 的库存流水（select 出 sku id 再删）"""
    sku_ids = (
        (
            await session.execute(
                SKU.__table__.select()
                .with_only_columns(SKU.id)
                .join(Product, SKU.product_id == Product.id)
                .where(Product.sku_code.like("PYTEST-%"))
            )
        )
        .scalars()
        .all()
    )
    if sku_ids:
        await session.execute(delete(StockMovement).where(StockMovement.sku_id.in_(sku_ids)))


@pytest.fixture(scope="session", autouse=True)
def cleanup_after_tests():
    # 复用同一个事件循环：asyncpg 连接池绑定在首次创建的 loop 上，
    # 多次 asyncio.run() 会各自新建 loop 导致 "attached to a different loop"。
    loop = asyncio.new_event_loop()
    snapshot = loop.run_until_complete(_snapshot_state())
    yield
    loop.run_until_complete(_cleanup())
    loop.run_until_complete(_restore_state(snapshot))
    loop.close()