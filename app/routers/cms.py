"""站点内容管理（CMS）：首页轮播、页面文字区块的管理与公开读取

- 公开接口：GET /api/banners?placement=home_hero 返回启用中的轮播
- 管理接口（require_admin）：
  - GET  /api/admin/banners                 轮播列表（含停用）
  - POST /api/admin/banners                 新增轮播
  - PUT  /api/admin/banners/{id}            更新轮播
  - DELETE /api/admin/banners/{id}          删除轮播
  - GET/PUT /api/admin/cms-content          页面文字内容（K-V，多语言 JSON）
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import String, delete, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_customer, get_optional_customer, require_admin
from app.i18n import get_lang
from app.models import AdminUser, Customer, SiteBanner, SiteContent, StoryComment, StoryLike, UserStory
from app.routers.uploads import ALLOWED_IMAGE_EXT, _safe_ext, _save_upload

router = APIRouter(prefix="/api", tags=["cms"])

# 默认页面文字内容（首页/关于页等区块文字，ERP 后台可修改）
DEFAULT_CMS_CONTENT = {
    "home": {
        "benefits": {
            "free_ship": {"zh": "全场包邮", "en": "Free Shipping"},
            "free_ship_desc": {"zh": "满 99 元免运费", "en": "Free over $99"},
            "safe_pay": {"zh": "安全支付", "en": "Secure Payment"},
            "safe_pay_desc": {"zh": "多种支付方式保障", "en": "Multi-way protected"},
            "easy_return": {"zh": "无忧退货", "en": "Easy Return"},
            "easy_return_desc": {"zh": "7 天无理由退换", "en": "7-day free returns"},
            "support": {"zh": "贴心服务", "en": "24/7 Support"},
            "support_desc": {"zh": "在线客服 7x24 小时", "en": "Online service 24/7"},
        },
        "sections": {
            "hot_categories_title": {"zh": "热门分类", "en": "Hot Categories"},
            "featured_title": {"zh": "为你推荐", "en": "Featured"},
            "banner_slogan": {"zh": "瑜伽与户外新日常", "en": "A New Everyday in Yoga and Outdoors"},
        },
    },
    "about": {
        "hero_tag": {"zh": "ABOUT YOYOLE", "en": "ABOUT YOYOLE"},
        "hero_title": {"zh": "YOYOLE，让身体回到自然", "en": "YOYOLE brings the body back to nature"},
        "hero_subtitle": {"zh": "从瑜伽练习到山野远行，用认真设计的装备支持每一次自在出发。", "en": "Thoughtful gear for yoga practice, outdoor adventures, and a life in motion."},
        "story_title": {"zh": "我们的故事", "en": "Our Story"},
        "story_content": {
            "zh": "YOYOLE 创立于 2020 年，从一群热爱瑜伽与户外生活的人开始。我们相信好的装备不应限制身体，而应让人与自然连接得更深。",
            "en": "Founded in 2020 by people who love yoga and the outdoors, YOYOLE believes good gear should free the body and deepen our connection with nature.",
        },
        "milestones_kicker": {"zh": "MILESTONES", "en": "MILESTONES"},
        "milestones_title": {"zh": "成长历程", "en": "Milestones"},
        "values_kicker": {"zh": "VALUES", "en": "VALUES"},
        "values_title": {"zh": "我们的价值观", "en": "Our Values"},
        "milestones": [
            {"time": {"zh": "2020", "en": "2020"}, "title": {"zh": "品牌创立", "en": "Brand Founded"}, "desc": {"zh": "从热爱潮流文化的小团队起步。", "en": "Started with a small team passionate about trend culture."}},
            {"time": {"zh": "2021", "en": "2021"}, "title": {"zh": "首次亮相", "en": "First Launch"}, "desc": {"zh": "用原创设计连接更多年轻用户。", "en": "Connected with more young users through original design."}},
            {"time": {"zh": "2023", "en": "2023"}, "title": {"zh": "社区成长", "en": "Community Grows"}, "desc": {"zh": "用户故事成为品牌灵感的重要来源。", "en": "User stories became an important source of inspiration."}},
            {"time": {"zh": "2025", "en": "2025"}, "title": {"zh": "走向全球", "en": "Going Global"}, "desc": {"zh": "把潮流生活带给更多城市。", "en": "Bringing trend life to more cities."}},
        ],
        "values": [
            {"title": {"zh": "持续创造", "en": "Keep Creating"}, "desc": {"zh": "保持好奇，把灵感变成日常。", "en": "Stay curious and turn inspiration into everyday life."}},
            {"title": {"zh": "真诚连接", "en": "Connect Truly"}, "desc": {"zh": "尊重每一种表达，认真回应每份热爱。", "en": "Respect every expression and answer every passion."}},
            {"title": {"zh": "开放共生", "en": "Grow Together"}, "desc": {"zh": "与用户、创作者和城市共同成长。", "en": "Grow together with users, creators, and cities."}},
        ],
    },
}


def _banner_to_dict(b: SiteBanner, lang: str) -> dict:
    return {
        "id": b.id,
        "placement": b.placement,
        "title": b.title(lang),
        "title_i18n": b.title_i18n,
        "subtitle": b.subtitle(lang),
        "subtitle_i18n": b.subtitle_i18n,
        "button_text": b.button_text(lang),
        "button_text_i18n": b.button_text_i18n,
        "image_url": b.image_url,
        "video_url": b.video_url,
        "link_url": b.link_url,
        "sort_order": b.sort_order,
        "is_active": b.is_active,
    }


# ---------- 公开读取 ----------
@router.get("/banners")
async def list_banners(
    request: Request,
    placement: str = Query("home_hero"),
    db: AsyncSession = Depends(get_db),
):
    """前台公开：读取启用中的轮播/内容，按 sort_order 排序"""
    lang = get_lang(request)
    result = await db.execute(
        select(SiteBanner)
        .where(SiteBanner.placement == placement, SiteBanner.is_active.is_(True))
        .order_by(SiteBanner.sort_order, SiteBanner.id)
    )
    return [_banner_to_dict(b, lang) for b in result.scalars().all()]


@router.get("/site-content")
async def get_site_content(
    request: Request,
    page: str = Query("home"),
    db: AsyncSession = Depends(get_db),
):
    """前台公开：读取页面文字内容（DB 持久化内容优先，未配置时回退默认值）"""
    return await _load_page_content_async(db, page)


def _user_story_to_dict(
    story: UserStory,
    customer: Customer | None = None,
    like_count: int = 0,
    comment_count: int = 0,
    liked: bool = False,
) -> dict:
    """序列化用户故事：审核状态由 is_published + reject_reason 推导（approved/pending/rejected）"""
    if story.is_published:
        status = "approved"
    elif story.reject_reason:
        status = "rejected"
    else:
        status = "pending"
    # 兼容旧数据：tags 为空时回退到 category 派生
    tags = story.tags or []
    if not tags:
        tags = [story.category or "life"]
    return {
        "id": story.id,
        "title": story.title,
        "content": story.content,
        "image_url": story.image_url,
        "category": story.category or "life",
        "tags": tags,
        "author": (customer.full_name or customer.email.split("@")[0]) if customer else "用户",
        "author_email": customer.email if customer else None,
        "is_published": story.is_published,
        "status": status,
        "reject_reason": story.reject_reason,
        "created_at": story.created_at.isoformat() if story.created_at else None,
        "like_count": like_count,
        "comment_count": comment_count,
        "liked": liked,
    }


# 用户故事允许的默认标签（可自定义，前端标签筛选从公开列表聚合）
STORY_CATEGORIES = {"yoga": "瑜伽", "outdoor": "户外", "life": "生活"}


def _clean_tags(raw) -> list[str]:
    """清洗标签：字符串转数组、去空、去重、截断"""
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    seen = set()
    out = []
    for t in raw:
        t = str(t or "").strip()
        if t and t not in seen and len(t) <= 20:
            seen.add(t)
            out.append(t)
    return out[:6]


@router.get("/user-stories")
async def list_user_stories(
    category: str | None = Query(None, description="按分类筛选：yoga/outdoor/life"),
    tag: str | None = Query(None, description="按标签筛选（模糊匹配）"),
    db: AsyncSession = Depends(get_db),
):
    """前台公开：只展示已审核发布的用户故事，支持按分类/标签筛选。"""
    query = (
        select(UserStory, Customer)
        .join(Customer, Customer.id == UserStory.customer_id)
        .where(UserStory.is_published.is_(True), Customer.is_active.is_(True))
    )
    if category and category in STORY_CATEGORIES:
        query = query.where(UserStory.category == category)
    if tag:
        tag = tag.strip()
        # tags JSON 数组包含匹配；兼容 tags 为空（旧数据）时按 category 匹配
        query = query.where(
            or_(
                UserStory.tags.cast(JSONB).contains([tag]),
                UserStory.category == tag,
            )
        )
    query = query.order_by(UserStory.created_at.desc(), UserStory.id.desc())
    result = await db.execute(query)
    rows = result.all()

    story_ids = [s.id for s, _ in rows]
    like_counts: dict[int, int] = {}
    comment_counts: dict[int, int] = {}
    if story_ids:
        like_rows = (
            await db.execute(
                select(StoryLike.user_story_id, func.count()).group_by(StoryLike.user_story_id).where(StoryLike.user_story_id.in_(story_ids))
            )
        ).all()
        like_counts = dict(like_rows)
        comment_rows = (
            await db.execute(
                select(StoryComment.user_story_id, func.count()).group_by(StoryComment.user_story_id).where(StoryComment.user_story_id.in_(story_ids))
            )
        ).all()
        comment_counts = dict(comment_rows)

    stories = [
        _user_story_to_dict(
            story, customer,
            like_count=like_counts.get(story.id, 0),
            comment_count=comment_counts.get(story.id, 0),
            liked=False,
        )
        for story, customer in rows
    ]
    # 聚合全部标签（用于前台标签墙），按频率排序
    tag_counts: dict[str, int] = {}
    for s in stories:
        for t in s["tags"]:
            tag_counts[t] = tag_counts.get(t, 0) + 1
    all_tags = sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"items": stories, "tags": [{"name": name, "count": cnt} for name, cnt in all_tags]}


@router.post("/user-stories", status_code=201)
async def create_user_story(
    payload: dict,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    title = str(payload.get("title") or "").strip()
    content = str(payload.get("content") or "").strip()
    if not title or not content:
        raise HTTPException(status_code=400, detail="标题和分享内容不能为空")
    if len(title) > 200 or len(content) > 5000:
        raise HTTPException(status_code=400, detail="标题或分享内容过长")
    category = str(payload.get("category") or "life").strip()
    # 标签化改造后 category 为兼容字段：不在枚举内时回退 life，不阻断提交
    if category not in STORY_CATEGORIES:
        category = "life"
    tags = _clean_tags(payload.get("tags"))
    story = UserStory(
        customer_id=customer.id,
        title=title,
        content=content,
        image_url=str(payload.get("image_url") or "") or None,
        category=category,
        tags=tags,
        is_published=False,
    )
    db.add(story)
    await db.commit()
    await db.refresh(story)
    return _user_story_to_dict(story, customer)


@router.get("/user-stories/mine")
async def list_my_user_stories(
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserStory).where(UserStory.customer_id == customer.id).order_by(UserStory.created_at.desc())
    )
    return [_user_story_to_dict(story, customer) for story in result.scalars().all()]


@router.get("/user-stories/{story_id}")
async def get_user_story(
    story_id: int,
    customer: Customer | None = Depends(get_optional_customer),
    db: AsyncSession = Depends(get_db),
):
    """前台公开：单条用户故事详情（仅已审核发布，供详情页展示）。"""
    result = await db.execute(
        select(UserStory, Customer)
        .join(Customer, Customer.id == UserStory.customer_id)
        .where(UserStory.id == story_id, UserStory.is_published.is_(True), Customer.is_active.is_(True))
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="用户故事不存在或未发布")
    story, author = row
    like_count = (
        await db.execute(select(func.count()).select_from(StoryLike).where(StoryLike.user_story_id == story_id))
    ).scalar() or 0
    comment_count = (
        await db.execute(select(func.count()).select_from(StoryComment).where(StoryComment.user_story_id == story_id))
    ).scalar() or 0
    liked = False
    if customer:
        liked = (
            await db.execute(
                select(StoryLike).where(StoryLike.user_story_id == story_id, StoryLike.customer_id == customer.id)
            )
        ).first() is not None
    return _user_story_to_dict(story, author, like_count=like_count, comment_count=comment_count, liked=liked)


@router.post("/user-stories/{story_id}/like")
async def like_user_story(
    story_id: int,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """点赞故事（幂等：重复点赞返回当前状态）。"""
    story = (await db.execute(select(UserStory).where(UserStory.id == story_id))).scalar_one_or_none()
    if not story or not story.is_published:
        raise HTTPException(status_code=404, detail="用户故事不存在或未发布")
    existing = (
        await db.execute(
            select(StoryLike).where(StoryLike.user_story_id == story_id, StoryLike.customer_id == customer.id)
        )
    ).scalar_one_or_none()
    if not existing:
        db.add(StoryLike(user_story_id=story_id, customer_id=customer.id))
        await db.commit()
    like_count = (
        await db.execute(select(func.count()).select_from(StoryLike).where(StoryLike.user_story_id == story_id))
    ).scalar() or 0
    return {"liked": True, "like_count": like_count}


@router.post("/user-stories/{story_id}/unlike")
async def unlike_user_story(
    story_id: int,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """取消点赞（幂等）。"""
    await db.execute(
        delete(StoryLike).where(StoryLike.user_story_id == story_id, StoryLike.customer_id == customer.id)
    )
    await db.commit()
    like_count = (
        await db.execute(select(func.count()).select_from(StoryLike).where(StoryLike.user_story_id == story_id))
    ).scalar() or 0
    return {"liked": False, "like_count": like_count}


@router.get("/user-stories/{story_id}/comments")
async def list_story_comments(
    story_id: int,
    db: AsyncSession = Depends(get_db),
):
    """公开：故事评论列表（按时间正序）。"""
    story = (await db.execute(select(UserStory).where(UserStory.id == story_id))).scalar_one_or_none()
    if not story or not story.is_published:
        raise HTTPException(status_code=404, detail="用户故事不存在或未发布")
    rows = (
        await db.execute(
            select(StoryComment, Customer)
            .join(Customer, Customer.id == StoryComment.customer_id)
            .where(StoryComment.user_story_id == story_id)
            .order_by(StoryComment.created_at.asc(), StoryComment.id.asc())
        )
    ).all()
    return [
        {
            "id": c.id,
            "content": c.content,
            "author": (cu.full_name or cu.email.split("@")[0]) if cu else "用户",
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c, cu in rows
    ]


@router.post("/user-stories/{story_id}/comments")
async def create_story_comment(
    story_id: int,
    payload: dict,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """评论故事。"""
    story = (await db.execute(select(UserStory).where(UserStory.id == story_id))).scalar_one_or_none()
    if not story or not story.is_published:
        raise HTTPException(status_code=404, detail="用户故事不存在或未发布")
    content = str(payload.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="评论内容不能为空")
    if len(content) > 500:
        raise HTTPException(status_code=400, detail="评论内容不能超过 500 字")
    comment = StoryComment(user_story_id=story_id, customer_id=customer.id, content=content)
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return {
        "id": comment.id,
        "content": comment.content,
        "author": customer.full_name or customer.email.split("@")[0],
        "created_at": comment.created_at.isoformat() if comment.created_at else None,
    }


@router.put("/user-stories/{story_id}")
async def update_my_user_story(
    story_id: int,
    payload: dict,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(UserStory).where(UserStory.id == story_id, UserStory.customer_id == customer.id))
    story = result.scalar_one_or_none()
    if not story:
        raise HTTPException(status_code=404, detail="用户故事不存在")
    title = str(payload.get("title") or "").strip()
    content = str(payload.get("content") or "").strip()
    if not title or not content:
        raise HTTPException(status_code=400, detail="标题和分享内容不能为空")
    if len(title) > 200 or len(content) > 5000:
        raise HTTPException(status_code=400, detail="标题或分享内容过长")
    story.title = title
    story.content = content
    if "image_url" in payload:
        story.image_url = str(payload.get("image_url") or "") or None
    if "category" in payload:
        category = str(payload.get("category") or "life").strip()
        if category not in STORY_CATEGORIES:
            category = "life"
        story.category = category
    if "tags" in payload:
        story.tags = _clean_tags(payload.get("tags"))
    # 编辑后强制重新审核，并清空驳回理由
    story.is_published = False
    story.reject_reason = None
    await db.commit()
    await db.refresh(story)
    return _user_story_to_dict(story, customer)


@router.delete("/user-stories/{story_id}")
async def delete_my_user_story(
    story_id: int,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """用户删除自己的故事（物理删除）。"""
    result = await db.execute(select(UserStory).where(UserStory.id == story_id, UserStory.customer_id == customer.id))
    story = result.scalar_one_or_none()
    if not story:
        raise HTTPException(status_code=404, detail="用户故事不存在")
    await db.delete(story)
    await db.commit()
    return {"message": "已删除"}


# ---------- 页面内容持久化（SiteContent） ----------

def _flatten_content(page: str, content: dict, prefix: str = "") -> list[tuple[str, dict | str]]:
    """递归把页面内容扁平化为 (key, value)。

    叶子规则：
    - dict 且键 ⊆ {zh, en} → 多语言内容，整体保存
    - dict（其他键）→ 容器，递归展开（key 用 '.' 连接）
    - 标量（str/int/bool/URL）→ 直接保存原值
    """
    out: list[tuple[str, dict | str]] = []
    for k, v in content.items():
        key = f"{page}:{prefix}{k}" if prefix else f"{page}:{k}"
        if isinstance(v, dict):
            # 多语言叶子：{"zh","en"} → 整体保存
            if set(v.keys()) <= {"zh", "en"} and v:
                out.append((key, v))
            else:
                out.extend(_flatten_content(page, v, prefix=f"{key.split(':', 1)[1]}."))
        else:
            out.append((key, v))
    return out


def _unflatten_content(rows, page: str) -> dict:
    """把 DB 行 (key, value) 还原为页面嵌套 dict"""
    merged: dict = {}
    for r in rows:
        parts = r.key.split(":")
        if len(parts) < 2:
            continue
        rest = parts[1]  # "story_title" 或 "benefits.free_ship" 或 "story.image"
        segments = rest.split(".")
        node = merged
        for i, seg in enumerate(segments[:-1]):
            node = node.setdefault(seg, {})
        node[segments[-1]] = r.value
    return merged


async def _load_page_content_async(db: AsyncSession, page: str) -> dict:
    """从 SiteContent 表加载页面内容，DB 值覆盖默认值"""
    defaults = DEFAULT_CMS_CONTENT.get(page, {})
    if page not in DEFAULT_CMS_CONTENT:
        return defaults

    result = await db.execute(
        select(SiteContent).where(SiteContent.key.like(f"{page}:%"))
    )
    rows = result.scalars().all()
    if not rows:
        return defaults

    merged = _unflatten_content(rows, page)

    # DB 值覆盖默认值（默认值兜底未配置部分）
    def _overlay(base, patch):
        if isinstance(base, dict) and isinstance(patch, dict):
            out = dict(base)
            for k, v in patch.items():
                out[k] = _overlay(base.get(k), v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
            return out
        return patch

    return _overlay(defaults, merged)


async def _save_page_content(db: AsyncSession, page: str, content: dict) -> None:
    """将页面内容扁平化写入 SiteContent 表（幂等 upsert）"""
    existing_result = await db.execute(
        select(SiteContent).where(SiteContent.key.like(f"{page}:%"))
    )
    existing = {r.key: r for r in existing_result.scalars().all()}

    pending = _flatten_content(page, content)
    keys_seen: set[str] = set()
    for key, val in pending:
        keys_seen.add(key)
        if key in existing:
            existing[key].value = val
        else:
            db.add(SiteContent(key=key, value=val))

    for key, row in existing.items():
        if key not in keys_seen:
            await db.delete(row)
    await db.commit()


# ---------- 管理接口 ----------
@router.get("/admin/banners")
async def admin_list_banners(
    placement: str | None = None,
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(SiteBanner).order_by(SiteBanner.placement, SiteBanner.sort_order, SiteBanner.id)
    if placement:
        stmt = stmt.where(SiteBanner.placement == placement)
    result = await db.execute(stmt)
    return [_banner_to_dict(b, "zh") for b in result.scalars().all()]


@router.get("/admin/banners/{banner_id}")
async def admin_get_banner(
    banner_id: int,
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """管理端读取单条轮播（供独立编辑页回填）。"""
    result = await db.execute(select(SiteBanner).where(SiteBanner.id == banner_id))
    banner = result.scalar_one_or_none()
    if not banner:
        raise HTTPException(status_code=404, detail="轮播不存在")
    return _banner_to_dict(banner, "zh")


@router.post("/admin/banners", status_code=201)
async def admin_create_banner(
    payload: dict,
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    banner = SiteBanner(
        placement=str(payload.get("placement") or "home_hero"),
        title_i18n=payload.get("title_i18n") or {"zh": "", "en": ""},
        subtitle_i18n=payload.get("subtitle_i18n") or {"zh": "", "en": ""},
        button_text_i18n=payload.get("button_text_i18n") or {"zh": "", "en": ""},
        image_url=payload.get("image_url") or None,
        video_url=payload.get("video_url") or None,
        link_url=payload.get("link_url") or None,
        sort_order=int(payload.get("sort_order") or 0),
        is_active=bool(payload.get("is_active", True)),
    )
    db.add(banner)
    await db.commit()
    await db.refresh(banner)
    return _banner_to_dict(banner, "zh")


@router.put("/admin/banners/{banner_id}")
async def admin_update_banner(
    banner_id: int,
    payload: dict,
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(SiteBanner).where(SiteBanner.id == banner_id))
    banner = result.scalar_one_or_none()
    if not banner:
        raise HTTPException(status_code=404, detail="轮播不存在")

    if "placement" in payload:
        banner.placement = str(payload["placement"])
    if "title_i18n" in payload:
        banner.title_i18n = payload["title_i18n"]
    if "subtitle_i18n" in payload:
        banner.subtitle_i18n = payload["subtitle_i18n"]
    if "button_text_i18n" in payload:
        banner.button_text_i18n = payload["button_text_i18n"]
    if "image_url" in payload:
        banner.image_url = payload["image_url"] or None
    if "video_url" in payload:
        banner.video_url = payload["video_url"] or None
    if "link_url" in payload:
        banner.link_url = payload["link_url"] or None
    if "sort_order" in payload:
        banner.sort_order = int(payload["sort_order"])
    if "is_active" in payload:
        banner.is_active = bool(payload["is_active"])
    await db.commit()
    await db.refresh(banner)
    return _banner_to_dict(banner, "zh")


@router.delete("/admin/banners/{banner_id}")
async def admin_delete_banner(
    banner_id: int,
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(SiteBanner).where(SiteBanner.id == banner_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="轮播不存在")
    await db.execute(delete(SiteBanner).where(SiteBanner.id == banner_id))
    await db.commit()
    return {"message": "已删除"}


# ---------- 页面文字内容管理 ----------
@router.get("/admin/cms-content")
async def admin_get_cms_content(
    page: str = Query("home"),
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """后台读取：DB 持久化内容优先，未配置回退默认值"""
    return await _load_page_content_async(db, page)


@router.put("/admin/cms-content")
async def admin_put_cms_content(
    payload: dict,
    admin: AdminUser = Depends(require_admin({"superadmin", "operator"})),
    db: AsyncSession = Depends(get_db),
):
    """保存页面文字/图片内容（持久化到 SiteContent 表，重启不丢失）"""
    page = str(payload.get("page") or "home")
    content = payload.get("content") or {}
    # 合并：仅覆盖传入的字段，其余保留 DB/默认值
    base = await _load_page_content_async(db, page)
    merged = _deep_merge(base, content)
    await _save_page_content(db, page, merged)
    return {"message": "已保存", "page": page}


@router.get("/admin/user-stories")
async def admin_list_user_stories(
    status: str | None = Query(None, description="按状态筛选：pending 待审核 / approved 已发布 / rejected 已驳回"),
    category: str | None = Query(None, description="按分类筛选：yoga/outdoor/life"),
    admin: AdminUser = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(UserStory, Customer)
        .join(Customer, Customer.id == UserStory.customer_id)
    )
    if status == "pending":
        query = query.where(UserStory.is_published.is_(False), UserStory.reject_reason.is_(None))
    elif status == "approved":
        query = query.where(UserStory.is_published.is_(True))
    elif status == "rejected":
        query = query.where(UserStory.is_published.is_(False), UserStory.reject_reason.is_not(None))
    if category and category in STORY_CATEGORIES:
        query = query.where(UserStory.category == category)
    query = query.order_by(UserStory.created_at.desc(), UserStory.id.desc())
    result = await db.execute(query)
    return [_user_story_to_dict(story, customer) for story, customer in result.all()]


@router.put("/admin/user-stories/{story_id}")
async def admin_update_user_story(
    story_id: int,
    payload: dict,
    admin: AdminUser = Depends(require_admin({"superadmin", "operator"})),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(UserStory).where(UserStory.id == story_id))
    story = result.scalar_one_or_none()
    if not story:
        raise HTTPException(status_code=404, detail="用户故事不存在")
    for field in ("title", "content", "image_url"):
        if field in payload:
            setattr(story, field, str(payload[field] or "").strip() or None)
    if "category" in payload:
        category = str(payload.get("category") or "life").strip()
        if category not in STORY_CATEGORIES:
            category = "life"
        story.category = category
    if "tags" in payload:
        story.tags = _clean_tags(payload.get("tags"))
    if "is_published" in payload:
        story.is_published = bool(payload["is_published"])
        # 发布成功时清空驳回理由；撤下保留原驳回理由以便追溯
        if story.is_published:
            story.reject_reason = None
    if "reject_reason" in payload:
        # 驳回：填入理由并强制下架
        story.reject_reason = str(payload["reject_reason"] or "").strip() or None
        if story.reject_reason:
            story.is_published = False
    await db.commit()
    await db.refresh(story)
    customer = (await db.execute(select(Customer).where(Customer.id == story.customer_id))).scalar_one_or_none()
    return _user_story_to_dict(story, customer)


@router.delete("/admin/user-stories/{story_id}")
async def admin_delete_user_story(
    story_id: int,
    admin: AdminUser = Depends(require_admin({"superadmin", "operator"})),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(UserStory).where(UserStory.id == story_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="用户故事不存在")
    await db.execute(delete(UserStory).where(UserStory.id == story_id))
    await db.commit()
    return {"message": "已删除"}


def _deep_merge(base: dict, patch: dict) -> dict:
    """递归合并，patch 覆盖 base 的叶子值，返回新 dict"""
    result = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result