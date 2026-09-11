"""初始化种子数据：默认管理员、示例分类与商品（幂等）"""
from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import select

from app.database import async_session_factory
from app.models import AdminRole, AdminUser, Category, Product, SKU
from app.security import hash_password

logger = logging.getLogger(__name__)


async def seed_all() -> None:
    """写入默认数据，已存在则跳过，保证可重复执行"""
    async with async_session_factory() as db:
        # 1) 默认管理员
        exists = await db.execute(
            select(AdminUser).where(AdminUser.username == "admin")
        )
        if not exists.scalar_one_or_none():
            db.add(
                AdminUser(
                    username="admin",
                    password_hash=hash_password("admin123"),
                    full_name="超级管理员",
                    role=AdminRole.SUPERADMIN,
                    is_active=True,
                )
            )
            logger.info("[seed] 创建默认管理员 admin / admin123")

        # 2) 分类
        categories_data = [
            ("electronics", {"zh": "瑜伽套装", "en": "Yoga Sets"}, 1),
            ("clothing", {"zh": "裤子", "en": "Pants"}, 2),
            ("home", {"zh": "上衣", "en": "Tops"}, 3),
            ("books", {"zh": "其他", "en": "Other"}, 4),
        ]
        categories: dict[str, Category] = {}
        for code, name_i18n, sort_order in categories_data:
            result = await db.execute(select(Category).where(Category.code == code))
            cat = result.scalar_one_or_none()
            if not cat:
                cat = Category(code=code, name_i18n=name_i18n, sort_order=sort_order)
                db.add(cat)
                await db.flush()
            categories[code] = cat

        # 3) 商品与 SKU
        products_data = [
            {
                "category": "electronics",
                "sku_code": "PHONE-001",
                "name_i18n": {"zh": "天然软木瑜伽垫 Pro", "en": "Cork Yoga Mat Pro"},
                "description_i18n": {
                    "zh": "亲肤天然软木表层，稳固支撑每一次体式练习。",
                    "en": "A skin-friendly cork surface with stable support for every practice.",
                },
                "main_image": "https://picsum.photos/seed/phone/600/400",
                "images": [],
                "base_price": "4999.00",
                "brand": "YOYOLE",
                "is_featured": True,
                "skus": [
                    {"sku_code": "PHONE-001-BLK-256", "attributes": {"颜色": "森林绿", "厚度": "6mm"}, "price": "399.00", "stock": 100},
                    {"sku_code": "PHONE-001-WHT-512", "attributes": {"颜色": "沙砾灰", "厚度": "8mm"}, "price": "459.00", "stock": 50},
                ],
            },
            {
                "category": "electronics",
                "sku_code": "LAPTOP-001",
                "name_i18n": {"zh": "轻量防滑瑜伽砖", "en": "Lightweight Yoga Block"},
                "description_i18n": {
                    "zh": "高密度环保泡棉，帮助伸展、平衡与恢复。",
                    "en": "Dense eco foam for stretching, balance, and recovery.",
                },
                "main_image": "https://picsum.photos/seed/laptop/600/400",
                "images": [],
                "base_price": "6999.00",
                "brand": "YOYOLE",
                "is_featured": True,
                "skus": [
                    {"sku_code": "LAPTOP-001-SLV", "attributes": {"颜色": "岩石灰"}, "price": "129.00", "stock": 30},
                ],
            },
            {
                "category": "clothing",
                "sku_code": "TSHIRT-001",
                "name_i18n": {"zh": "云感高腰瑜伽紧身裤", "en": "Cloud High-Waist Yoga Leggings"},
                "description_i18n": {
                    "zh": "四向弹力与高腰支撑，适合练习和日常移动。",
                    "en": "Four-way stretch and high-waist support for practice and everyday movement.",
                },
                "main_image": "https://picsum.photos/seed/tshirt/600/400",
                "images": [],
                "base_price": "99.00",
                "brand": "YOYOLE",
                "is_featured": False,
                "skus": [
                    {"sku_code": "TSHIRT-001-L", "attributes": {"颜色": "苔藓绿", "尺码": "L"}, "price": "269.00", "stock": 200},
                    {"sku_code": "TSHIRT-001-XL", "attributes": {"颜色": "苔藓绿", "尺码": "XL"}, "price": "269.00", "stock": 180},
                ],
            },
            {
                "category": "books",
                "sku_code": "CUP-001",
                "name_i18n": {"zh": "真空保温户外水壶", "en": "Insulated Outdoor Bottle"},
                "description_i18n": {
                    "zh": "轻便耐用，长途徒步和城市通勤都能保持温度。",
                    "en": "Lightweight and durable, keeping drinks at temperature on hikes or commutes.",
                },
                "main_image": "https://picsum.photos/seed/mug/600/400",
                "images": [],
                "base_price": "39.90",
                "brand": "YOYOLE",
                "is_featured": False,
                "skus": [
                    {"sku_code": "CUP-001-WHT", "attributes": {"颜色": "云雾白"}, "price": "199.00", "stock": 500},
                ],
            },
            {
                "category": "books",
                "sku_code": "BOOK-001",
                "name_i18n": {"zh": "轻量徒步背包 18L", "en": "Lightweight Hiking Pack 18L"},
                "description_i18n": {
                    "zh": "贴合背负系统与防泼水面料，为周末远足留出刚好的空间。",
                    "en": "A comfortable carry system and water-resistant fabric for weekend hikes.",
                },
                "main_image": "https://picsum.photos/seed/book/600/400",
                "images": [],
                "base_price": "59.00",
                "brand": "YOYOLE",
                "is_featured": False,
                "skus": [
                    {"sku_code": "BOOK-001-P", "attributes": {"颜色": "山影黑"}, "price": "499.00", "stock": 80},
                ],
            },
        ]

        for pdata in products_data:
            result = await db.execute(
                select(Product).where(Product.sku_code == pdata["sku_code"])
            )
            product = result.scalar_one_or_none()
            if not product:
                product = Product(
                    category_id=categories[pdata["category"]].id,
                    sku_code=pdata["sku_code"],
                    name_i18n=pdata["name_i18n"],
                    description_i18n=pdata["description_i18n"],
                    main_image=pdata["main_image"],
                    images=pdata["images"],
                    base_price=Decimal(pdata["base_price"]),
                    brand=pdata["brand"],
                    status="active",
                    is_featured=pdata["is_featured"],
                )
                db.add(product)
                await db.flush()
                for sku_data in pdata["skus"]:
                    db.add(
                        SKU(
                            product_id=product.id,
                            sku_code=sku_data["sku_code"],
                            attributes=sku_data["attributes"],
                            price=Decimal(sku_data["price"]),
                            cost_price=Decimal(sku_data["price"]) * Decimal("0.6"),
                            stock=sku_data["stock"],
                            locked_stock=0,
                            low_stock_threshold=10,
                            is_active=True,
                        )
                    )
            else:
                # 既有开发数据库也要随品牌升级同步为瑜伽户外商品。
                product.category_id = categories[pdata["category"]].id
                product.name_i18n = pdata["name_i18n"]
                product.description_i18n = pdata["description_i18n"]
                product.base_price = Decimal(pdata["base_price"])
                product.brand = pdata["brand"]
                product.is_featured = pdata["is_featured"]

        # 已有数据库中的分类也必须同步名称，避免前台残留旧类目。
        for code, name_i18n, sort_order in categories_data:
            cat = categories[code]
            cat.name_i18n = name_i18n
            cat.sort_order = sort_order

        await db.commit()
        logger.info("[seed] 种子数据初始化完成")