"""从 /tmp/jyt_products 导入 218 款瑜伽服饰商品（一次性导入脚本）

数据来源：/Users/huangyong/Documents/jyt/20260728-产品册合集_带logo_v3.pptx
运行方式（项目根目录）：
    .venv/bin/python -m app.import_jyt

流程：
1. 读取 products.json（文本字段）与 manifest.json（图片URL）
2. 清空旧商品/旧分类，重建服装分类树
3. 解析每个 slide 的货号/品名/面料/尺码/颜色
4. 生成 Product + SKU（颜色×尺码），写入 base_price
5. 幂等：重复执行前先清理全部商品
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, select

from app.database import async_session_factory
from app.models import Category, Product, SKU

logger = logging.getLogger(__name__)

SRC_DIR = Path("/tmp/jyt_products")

# ---------------------------------------------------------------- 分类映射
# 按品名关键词归类到服装分类
CATEGORY_RULES = [
    # (分类code, 分类zh, 分类en, 关键词列表)
    ("bras",       "文胸", "Bras",          ["文胸"]),
    ("vests",      "背心", "Vests",         ["背心", "吊带"]),
    ("tshirts",    "短袖", "T-Shirts",      ["短袖"]),
    ("longsleeves", "长袖", "Long Sleeves", ["长袖", "开衫", "罩衫"]),
    ("jackets",    "外套", "Jackets",       ["外套", "连帽衫", "拉链连帽"]),
    ("shorts",     "短裤", "Shorts",        ["短裤", "三分裤", "五分裤", "七分裤"]),
    ("pants",      "长裤", "Pants",         ["长裤", "喇叭裤", "裙裤", "阔腿裤", "瑜伽裤", "机车裤", "烟管裤", "直筒裤"]),
    ("skirts",     "裙子", "Skirts",        ["短裙", "百褶裙"]),
]

# 面料成分基线价格（元）—— 按品类给合理零售价
BASE_PRICE_BY_CAT = {
    "bras": 129, "vests": 99, "tshirts": 119, "longsleeves": 139,
    "jackets": 199, "shorts": 119, "pants": 169, "skirts": 119,
}

_ROUND_PRICE = {99: 99.00, 109: 109.00, 119: 119.00, 129: 129.00, 139: 139.00,
                149: 149.00, 159: 159.00, 169: 169.00, 179: 179.00, 189: 189.00}


def pick_category(name: str) -> str:
    """按品名关键词返回分类 code"""
    for code, _, _, kws in CATEGORY_RULES:
        for kw in kws:
            if kw in name:
                return code
    return "other"


# ---------------------------------------------------------------- 字段解析
def _texts(pr: dict) -> list[str]:
    return pr.get("texts", [])


def _joined(pr: dict) -> str:
    return pr.get("joined", "")


def parse_num(pr: dict) -> str | None:
    num = pr.get("num")
    if num and re.match(r"^[A-Za-z0-9]{4,12}$", num):
        return num
    # 1) 带"货号/Number"标签
    m = re.search(r"货号/Number\s*[|]?\s*([A-Za-z0-9]{4,12})", _joined(pr))
    if m:
        return m.group(1)
    # 2) 独立的裸货号行（如 "JYBXD0004" / "OMMN070" / "JYWQ015" 单独一行）
    for t in _texts(pr):
        t2 = re.sub(r"[^A-Za-z0-9]", "", t)
        if re.fullmatch(r"[A-Za-z]{2,6}\d{3,}", t2):
            return t2
    return None


def parse_name(pr: dict) -> str:
    n = pr.get("name")
    if n and n != "(无品名)":
        return n
    return "瑜伽服饰"


def parse_fabrics(pr: dict) -> dict[str, str]:
    """解析面料成分：{"面料": "80%锦纶20%氨纶", "网纱": "94%聚酯纤维6%氨纶"}"""
    zh_map = {
        "聚酯纤维": "Polyester", "锦纶": "Nylon", "氨纶": "Elastane",
        "棉": "Cotton", "羊毛": "Wool", "腈纶": "Acrylic",
        "涤纶": "Polyester", "粘纤": "Viscose",
    }
    out: dict[str, str] = {}
    for t in _texts(pr):
        # 中文面料行（优先）
        for m in re.finditer(
            r"([\u4e00-\u9fa5]{1,4})[：:]\s*((?:\d+\.?\d*%[\u4e00-\u9fa5A-Za-z/]{1,12})+)",
            t,
        ):
            key, val = m.group(1), m.group(2).strip()
            if key not in ("货号", "品名", "尺码", "颜色", "颜色 ", "Number", "Composition"):
                out[key] = val
    # 无中文面料 → 翻译 Composition 行
    if not out:
        m = re.search(r"Composition:\s*([\d.%A-Za-z, ]+)\b", _joined(pr))
        if m:
            comp = m.group(1).strip()
            for zh, en in zh_map.items():
                comp = comp.replace(en, zh)
            out["面料"] = comp
    return out


def parse_sizes(pr: dict) -> list[str]:
    m = re.search(r"尺码[：:]\s*([A-Z]{1,4}(?:/[A-Z]{1,4})+|均码)", _joined(pr))
    if not m:
        return ["S", "M", "L", "XL", "XXL"]
    raw = m.group(1)
    if raw == "均码":
        return ["均码"]
    return raw.split("/")


def parse_colors(pr: dict) -> list[str]:
    """提取颜色中文名（保序去重），噪音过滤"""
    texts = _texts(pr)
    ci = None
    for i, t in enumerate(texts):
        # "颜色/Color" 或 "颜 色/Color"（含空格）
        compact = t.replace(" ", "").replace("　", "")
        if "颜色" in compact and "Color" in compact:
            ci = i
            break
    if ci is None:
        for i, t in enumerate(texts):
            if t.strip().startswith("颜色") or t.strip().startswith("颜 色"):
                ci = i
                break
    if ci is None:
        return []
    out: list[str] = []
    stop_keys = ("品名：", "面料：", "尺码：", "Composition:", "Size:",
                 "参考出货价", "参考价")
    for line in texts[ci + 1:]:
        line = line.strip()
        if not line:
            continue
        if any(k in line for k in stop_keys):
            break
        # 新的颜色区块
        if "颜色" in line.replace(" ", "") and "Color" in line:
            break
        # "货号/Number" 空标签行 -> 跳过；带值 -> 进入下一区块
        if "货号" in line:
            if re.search(r"货号/Number\s*[|]?\s*[A-Za-z0-9]{4,}", line):
                break
            continue
        for m in re.findall(r"[\u4e00-\u9fa5]{2,6}", line):
            if m in ("颜色", "Color", "出货价", "参考"):
                continue
            if m not in out:
                out.append(m)
    return out[:6]


def _en_name(zh: str) -> str:
    """中文品名 → 英文品名（简单映射）"""
    mapping = {
        "锦纶": "Nylon", "蜜桃": "Peach", "条纹": "Striped", "宽松": "Loose",
        "文胸": "Sports Bra", "背心": "Tank Top", "短袖": "T-Shirt",
        "长袖": "Long Sleeve", "外套": "Jacket", "短裤": "Shorts",
        "长裤": "Pants", "短裙": "Skirt", "裙裤": "Skirt Pants",
        "网纱": "Mesh", "镂空": "Cutout", "拼色": "Color Block",
        "撞色": "Contrast", "撞条": "Banded", "织带": "Webbing",
        "基础": "Basic", "运动": "Sport",
        "喇叭裤": "Flare Pants", "工字背": "Racerback", "挂脖": "Halter",
        "背交叉": "Crossback", "高腰": "High Waist", "小高领": "Turtleneck",
        "五分裤": "Capri", "七分裤": "7/8 Pants", "阔腿裤": "Wide Leg",
        "连帽": "Hooded", "加绒": "Fleece", "吊带": "Strap",
        "半拉链": "Half Zip", "字母": "Letter", "速干": "Quick Dry",
        "防晒": "UV", "百褶": "Pleated", "连体": "One Piece",
        "抽绳": "Drawstring", "口袋": "Pocket",
        "美背": "Back", "开叉": "Slit", "假两件": "Two-Piece",
        "升级版": "Pro", "拼接": "Patchwork", "卫衣": "Hoodie",
        "喇叭": "Flare", "线感": "Seamed", "菱格": "Diamond",
        "翻腰": "Fold Waist", "抽皱": "Ruffled", "扭结": "Twist",
        "金链": "Gold Chain", "人影线": "Stitch", "束口": "Cuffed",
        "直筒": "Straight", "烟管": "Tapered", "机车": "Moto",
        "甜心": "Sweet", "防晒": "UV", "冰丝": "Ice Silk",
        "弧线": "Curved", "弧背": "Curved Back", "肩带": "Strap",
        "排扣": "Button", "渐变": "Gradient", "吊染": "Dye Ombre",
    }
    en = zh
    for k, v in mapping.items():
        en = en.replace(k, v)
    return en


# ---------------------------------------------------------------- 主流程
async def import_all() -> None:
    with open(SRC_DIR / "products.json", encoding="utf-8") as f:
        products = json.load(f)
    with open(SRC_DIR / "manifest.json", encoding="utf-8") as f:
        manifest = json.load(f)

    async with async_session_factory() as db:
        # 1) 清理旧数据
        for model in (SKU, Product, Category):
            await db.execute(delete(model))
        await db.flush()

        # 2) 重建分类
        cats: dict[str, Category] = {}
        for code, zh, en, _ in CATEGORY_RULES:
            cat = Category(code=code, name_i18n={"zh": zh, "en": en},
                           sort_order=len(cats) + 1)
            db.add(cat)
            cats[code] = cat
        other = Category(code="other", name_i18n={"zh": "其他", "en": "Other"},
                         sort_order=99)
        db.add(other)
        cats["other"] = other
        await db.flush()

        # 3) 逐个导入商品
        imported = 0
        skipped = 0
        featured_counter = 0
        img_stats = Counter()
        for pr in products:
            slide = pr["slide"]
            name_zh = parse_name(pr)
            num = parse_num(pr)
            if not num:
                num = f"JYT-{slide:03d}"
            code = pick_category(name_zh)
            cat = cats[code]
            base_price = Decimal(str(BASE_PRICE_BY_CAT.get(code, 99)))

            # 判断面料是否加绒/含羊毛等 → 上调价格
            joined = _joined(pr)
            if "加绒" in name_zh or "加绒" in joined:
                base_price = Decimal(str(BASE_PRICE_BY_CAT.get(code, 99) + 40))
            if "外套" in name_zh and "加绒" in joined:
                base_price = Decimal(str(199 + 60))

            fabrics = parse_fabrics(pr)
            sizes = parse_sizes(pr)
            colors = parse_colors(pr)
            if not colors:
                colors = ["默认色"]
            if not sizes:
                sizes = ["均码"]

            # 图片
            man = manifest.get(str(slide), {})
            main_urls = man.get("main", [])
            detail_urls = man.get("details", [])
            swatches = man.get("swatches", [])
            main_image = main_urls[0] if main_urls else None
            images = main_urls + detail_urls
            # 详情长图：用背景大图
            if not main_image:
                main_image = f"/static/uploads/jyt/s{slide}/s{slide}_picture_20.jpg"
            img_stats["main"] += bool(main_image)
            img_stats["detail"] += len(detail_urls)

            # 描述（含面料/尺码/颜色）
            fabric_desc = "；".join(f"{k}：{v}" for k, v in fabrics.items()) or "优质面料"
            en_short = _en_name(name_zh)
            desc_zh = (
                f"{name_zh}，采用{fabric_desc}，"
                f"提供{'/'.join(sizes)}尺码，"
                f"颜色：{'、'.join(colors)}。"
                f"YOYOLE 瑜伽服系列，柔韧贴合，吸湿速干，适合瑜伽、健身与日常穿搭。"
            )
            desc_en = (
                f"{en_short} in {fabric_desc}, sizes {'/'.join(sizes)}. "
                f"Colors: {', '.join(_en_name(c) for c in colors)}. "
                f"YOYOLE activewear — soft, stretchy, quick-dry for yoga, "
                f"fitness and daily wear."
            )

            product = Product(
                category_id=cat.id,
                sku_code=num,
                name_i18n={"zh": name_zh, "en": en_short},
                description_i18n={"zh": desc_zh, "en": desc_en},
                main_image=main_image,
                images=images,
                base_price=base_price,
                brand="YOYOLE",
                weight_kg=Decimal("0.300"),
                status="active",
                is_featured=False,
            )
            # 每 20 个选一个推荐
            if imported % 20 == 0:
                product.is_featured = True
            db.add(product)
            await db.flush()

            # SKU：颜色 × 尺码
            sku_counter = 0
            for color in colors:
                for size in sizes:
                    sku_counter += 1
                    sku_code = f"{num}-{sku_counter:03d}"
                    db.add(
                        SKU(
                            product_id=product.id,
                            sku_code=sku_code,
                            attributes={"颜色": color, "尺码": size},
                            price=base_price,
                            cost_price=base_price * Decimal("0.45"),
                            stock=80 + (sku_counter * 13) % 60,
                            locked_stock=0,
                            low_stock_threshold=10,
                            is_active=True,
                        )
                    )
            imported += 1
            if slide in (1, 50, 100, 150, 200):
                logger.info(f"  slide {slide}: {name_zh} ({num}) "
                            f"{len(colors)}色 × {len(sizes)}码 → {cat.code}")

        await db.commit()
        logger.info(f"[import_jyt] 完成：导入 {imported} 款商品，跳过 {skipped}，图片统计 {dict(img_stats)}")

        # 4) 校验
        cnt = (await db.execute(select(Product))).scalars().all()
        cat_cnt = (await db.execute(select(Category))).scalars().all()
        sku_cnt = (await db.execute(select(SKU))).scalars().all()
        logger.info(f"[import_jyt] 校验: products={len(cnt)}, categories={len(cat_cnt)}, skus={len(sku_cnt)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(import_all())