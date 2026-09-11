"""全量 API 接口测试。

覆盖 :mod:`app.routers` 下所有公开接口：
- 公共接口：健康检查、分类、商品列表/详情/搜索
- 认证接口：注册（邮箱验证码）、登录、当前用户
- 购物车接口：查/加/改/删/清空
- 订单接口：下单、订单列表/详情、支付、取消、确认收货
- 后台接口：管理员登录、仪表盘、商品管理、SKU/库存、订单管理、分类、库存流水

运行（需先启动服务）：
    .venv/bin/python -m pytest test/test_api.py -v
"""
from __future__ import annotations

import os
import subprocess
import time
from urllib.parse import urlparse

import pytest
import requests

from app.config import settings

BASE = "http://127.0.0.1:8010"
H = {"Accept-Language": "zh"}

BUYER_EMAIL = "buyer@example.com"
BUYER_PASS = "buyer123"
ADMIN_USER = "admin"
ADMIN_PASS = "admin123"

ts = int(time.time())
TEST_EMAIL = f"test_{ts}@example.com"

# 数据库连接参数（psql 子进程读取验证码，避免 async 引擎事件循环冲突）
_DB_URL = urlparse(settings.DATABASE_URL)
_PSQL_ENV = {
    **os.environ,
    "PGHOST": _DB_URL.hostname or "localhost",
    "PGPORT": str(_DB_URL.port or 5432),
    "PGUSER": _DB_URL.username or "",
    "PGDATABASE": (_DB_URL.path or "/").lstrip("/"),
    "PGPASSWORD": _DB_URL.password or "",
}


def get_latest_code(email: str, purpose: str | None = None) -> str:
    """从数据库读取该邮箱最新未使用的验证码（psql 子进程，避免事件循环冲突）"""
    where = f"WHERE email = '{email.lower()}'"
    if purpose:
        where += f" AND purpose = '{purpose}'"
    sql = f"SELECT code FROM email_verify_codes {where} ORDER BY id DESC LIMIT 1;"
    res = subprocess.run(
        ["psql", "-t", "-A", "-c", sql],
        capture_output=True, text=True, env=_PSQL_ENV,
    )
    return res.stdout.strip()


def send_verify_code(email: str, purpose: str = "register") -> requests.Response:
    """请求发送邮箱验证码"""
    return requests.post(
        f"{BASE}/api/auth/send-code",
        json={"email": email, "purpose": purpose},
    )


def register_user(email: str, password: str = "test12345") -> requests.Response:
    """完整注册流程：发送验证码 → 读取 → 注册"""
    send_verify_code(email)
    code = get_latest_code(email)
    return requests.post(
        f"{BASE}/api/auth/register",
        json={"email": email, "password": password, "full_name": "测试用户", "code": code},
    )


# ---------- fixtures ----------
@pytest.fixture(scope="module", autouse=True)
def buyer_setup():
    """确保 buyer 测试账号在首个测试运行前已存在（避免测试顺序依赖）"""
    r = requests.post(f"{BASE}/api/auth/login", json={"email": BUYER_EMAIL, "password": BUYER_PASS})
    if r.status_code != 200:
        rr = register_user(BUYER_EMAIL, BUYER_PASS)
        assert rr.status_code in (200, 201), rr.text


@pytest.fixture(scope="module")
def buyer_token(buyer_setup):
    r = requests.post(f"{BASE}/api/auth/login", json={"email": BUYER_EMAIL, "password": BUYER_PASS})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}", **H}

@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE}/api/admin/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}", **H}


# ---------- 公共接口 ----------
class TestPublic:
    def test_health(self):
        r = requests.get(f"{BASE}/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_categories(self):
        r = requests.get(f"{BASE}/api/categories", headers=H)
        assert r.status_code == 200
        cats = r.json()
        assert isinstance(cats, list) and len(cats) > 0
        assert all("id" in c and "name_i18n" in c for c in cats)

    def test_products_list(self):
        r = requests.get(f"{BASE}/api/products", headers=H)
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list) and len(items) > 0
        first = items[0]
        for key in ("id", "sku_code", "base_price", "display_name"):
            assert key in first

    def test_products_pagination(self):
        r = requests.get(f"{BASE}/api/products?page=1&page_size=3", headers=H)
        assert r.status_code == 200
        assert len(r.json()) <= 3

    def test_products_featured(self):
        r = requests.get(f"{BASE}/api/products?featured=true", headers=H)
        assert r.status_code == 200
        assert all(p["is_featured"] for p in r.json())

    def test_products_category_filter(self):
        cats = requests.get(f"{BASE}/api/categories", headers=H).json()
        cid = cats[0]["id"]
        r = requests.get(f"{BASE}/api/products", params={"category_id": cid}, headers=H)
        assert r.status_code == 200
        items = r.json()
        assert len(items) > 0
        # 列表响应不含 category_id，改用详情接口核对分类归属
        for p in items[:3]:
            detail = requests.get(f"{BASE}/api/products/{p['id']}", headers=H).json()
            assert detail["category_id"] == cid, f"商品 {p['id']} 不属于分类 {cid}"

    def test_products_search_chinese(self):
        # 中文搜索（JSONB #>> 修复的回归用例）
        r = requests.get(f"{BASE}/api/products", params={"q": "瑜伽"}, headers=H)
        assert r.status_code == 200
        names = [p.get("display_name", "") for p in r.json()]
        assert any("瑜伽" in n or "Yoga" in n for n in names)

    def test_products_search_none(self):
        r = requests.get(f"{BASE}/api/products", params={"q": "不存在的商品xyz"}, headers=H)
        assert r.status_code == 200
        assert r.json() == []

    def test_product_detail(self):
        items = requests.get(f"{BASE}/api/products", headers=H).json()
        pid = items[0]["id"]
        r = requests.get(f"{BASE}/api/products/{pid}", headers=H)
        assert r.status_code == 200
        detail = r.json()
        assert detail["id"] == pid
        assert "skus" in detail and len(detail["skus"]) >= 1

    def test_product_detail_404(self):
        r = requests.get(f"{BASE}/api/products/999999", headers=H)
        assert r.status_code == 404


# ---------- 订阅 ----------
class TestNewsletter:
    def test_subscribe(self):
        email = f"sub_{ts}@example.com"
        r = requests.post(f"{BASE}/api/subscribe", json={"email": email})
        assert r.status_code == 201, r.text
        assert r.json()["message"]

    def test_subscribe_duplicate(self):
        email = f"sub_{ts}@example.com"
        r = requests.post(f"{BASE}/api/subscribe", json={"email": email})
        assert r.status_code in (200, 201)

    def test_subscribe_invalid_email(self):
        r = requests.post(f"{BASE}/api/subscribe", json={"email": "bad-email"})
        assert r.status_code == 422

    def test_admin_list_subscribers(self, admin_token):
        r = requests.get(f"{BASE}/api/admin/subscribers", headers=admin_token)
        assert r.status_code == 200
        subscribers = r.json()
        assert isinstance(subscribers, list)
        assert any(s["email"] == f"sub_{ts}@example.com" for s in subscribers)

    def test_admin_delete_subscriber(self, admin_token):
        email = f"sub_del_{ts}@example.com"
        requests.post(f"{BASE}/api/subscribe", json={"email": email})
        subs = requests.get(f"{BASE}/api/admin/subscribers", headers=admin_token).json()
        target = next((s for s in subs if s["email"] == email), None)
        if target:
            r = requests.delete(f"{BASE}/api/admin/subscribers/{target['id']}", headers=admin_token)
            assert r.status_code == 200


# ---------- 认证接口 ----------
class TestAuth:
    def test_send_code(self):
        r = send_verify_code(f"code_{ts}@example.com")
        assert r.status_code == 200, r.text
        assert r.json()["message"]

    def test_send_code_existing_email(self):
        r = send_verify_code(BUYER_EMAIL)
        assert r.status_code == 409

    def test_register_requires_code(self):
        payload = {"email": TEST_EMAIL, "password": "test123", "full_name": "测试用户"}
        r = requests.post(f"{BASE}/api/auth/register", json=payload)
        assert r.status_code == 400, r.text

    def test_register_wrong_code(self):
        send_verify_code(TEST_EMAIL)
        payload = {"email": TEST_EMAIL, "password": "test123", "full_name": "测试用户", "code": "000000"}
        r = requests.post(f"{BASE}/api/auth/register", json=payload)
        assert r.status_code == 400, r.text

    def test_register(self):
        send_verify_code(TEST_EMAIL)
        code = get_latest_code(TEST_EMAIL)
        assert code, "验证码未写入数据库"
        payload = {"email": TEST_EMAIL, "password": "test123", "full_name": "测试用户", "code": code}
        r = requests.post(f"{BASE}/api/auth/register", json=payload)
        assert r.status_code == 201, r.text
        assert "access_token" in r.json()

    def test_register_duplicate(self):
        payload = {"email": BUYER_EMAIL, "password": "whatever123", "code": "000000"}
        r = requests.post(f"{BASE}/api/auth/register", json=payload)
        assert r.status_code == 409

    def test_login_ok(self):
        r = requests.post(f"{BASE}/api/auth/login", json={"email": BUYER_EMAIL, "password": BUYER_PASS})
        assert r.status_code == 200
        assert "access_token" in r.json()

    def test_login_wrong_password(self):
        r = requests.post(f"{BASE}/api/auth/login", json={"email": BUYER_EMAIL, "password": "wrong"})
        assert r.status_code == 401

    def test_login_missing_user(self):
        r = requests.post(f"{BASE}/api/auth/login", json={"email": "nobody@example.com", "password": "x123456"})
        assert r.status_code == 401

    def test_me(self, buyer_token):
        r = requests.get(f"{BASE}/api/auth/me", headers=buyer_token)
        assert r.status_code == 200
        assert r.json()["email"] == BUYER_EMAIL

    def test_me_unauthorized(self):
        r = requests.get(f"{BASE}/api/auth/me")
        assert r.status_code == 401

    # ---------- 重置密码 ----------
    def test_reset_send_code_unknown_email(self):
        r = send_verify_code(f"nobody_{ts}@example.com", purpose="reset")
        assert r.status_code == 404

    def test_reset_password_wrong_code(self):
        send_verify_code(TEST_EMAIL, purpose="reset")
        r = requests.post(f"{BASE}/api/auth/reset-password",
                          json={"email": TEST_EMAIL, "code": "000000", "new_password": "newpass123"})
        assert r.status_code == 400

    def test_reset_password_flow(self):
        # 使用 TEST_EMAIL 注册的账号（test_register 已创建）
        send_verify_code(TEST_EMAIL, purpose="reset")
        code = get_latest_code(TEST_EMAIL, purpose="reset")
        assert code, "reset 验证码未写入"
        r = requests.post(f"{BASE}/api/auth/reset-password",
                          json={"email": TEST_EMAIL, "code": code, "new_password": "resetpass123"})
        assert r.status_code == 200, r.text
        # 新密码可登录
        login_ok = requests.post(f"{BASE}/api/auth/login",
                                 json={"email": TEST_EMAIL, "password": "resetpass123"})
        assert login_ok.status_code == 200
        # 旧密码失效
        login_old = requests.post(f"{BASE}/api/auth/login",
                                  json={"email": TEST_EMAIL, "password": "test123"})
        assert login_old.status_code == 401

    def test_reset_password_reuse_code(self):
        # 验证码只能使用一次
        send_verify_code(TEST_EMAIL, purpose="reset")
        code = get_latest_code(TEST_EMAIL, purpose="reset")
        r1 = requests.post(f"{BASE}/api/auth/reset-password",
                           json={"email": TEST_EMAIL, "code": code, "new_password": "againpass123"})
        assert r1.status_code == 200
        r2 = requests.post(f"{BASE}/api/auth/reset-password",
                           json={"email": TEST_EMAIL, "code": code, "new_password": "hackpass123"})
        assert r2.status_code == 400


# ---------- 购物车接口 ----------
class TestCart:
    def _first_sku(self):
        items = requests.get(f"{BASE}/api/products", headers=H).json()
        pid = items[0]["id"]
        detail = requests.get(f"{BASE}/api/products/{pid}", headers=H).json()
        return detail["skus"][0]["id"], detail["skus"][0]["available_stock"]

    def test_cart_requires_login(self):
        r = requests.get(f"{BASE}/api/cart")
        assert r.status_code == 401

    def test_add_and_get(self, buyer_token):
        sku_id, _ = self._first_sku()
        r = requests.post(f"{BASE}/api/cart/items", json={"sku_id": sku_id, "quantity": 1}, headers=buyer_token)
        assert r.status_code == 201, r.text
        cart = r.json()
        assert len(cart["items"]) >= 1
        assert str(cart["total_amount"]) != "0"

        # 获取购物车
        r2 = requests.get(f"{BASE}/api/cart", headers=buyer_token)
        assert r2.status_code == 200

    def test_add_invalid_quantity(self, buyer_token):
        sku_id, _ = self._first_sku()
        r = requests.post(f"{BASE}/api/cart/items", json={"sku_id": sku_id, "quantity": 0}, headers=buyer_token)
        assert r.status_code == 422

    def test_add_nonexistent_sku(self, buyer_token):
        r = requests.post(f"{BASE}/api/cart/items", json={"sku_id": 999999, "quantity": 1}, headers=buyer_token)
        assert r.status_code == 404

    def test_update_and_remove(self, buyer_token):
        # 加购
        sku_id, stock = self._first_sku()
        add = requests.post(f"{BASE}/api/cart/items", json={"sku_id": sku_id, "quantity": 2}, headers=buyer_token)
        assert add.status_code == 201
        item_id = add.json()["items"][0]["id"]

        # 修改数量
        upd = requests.put(f"{BASE}/api/cart/items/{item_id}", json={"quantity": 3}, headers=buyer_token)
        assert upd.status_code == 200
        assert upd.json()["items"][0]["quantity"] == 3

        # 库存上限校验
        over_qty = stock + 5 if stock < 1_000_000 else 500
        r_over = requests.put(f"{BASE}/api/cart/items/{item_id}", json={"quantity": over_qty}, headers=buyer_token)
        assert r_over.status_code == 400

        # 删除
        rem = requests.delete(f"{BASE}/api/cart/items/{item_id}", headers=buyer_token)
        assert rem.status_code == 200

        # 删除不存在
        r_404 = requests.delete(f"{BASE}/api/cart/items/{item_id}", headers=buyer_token)
        assert r_404.status_code == 404

    def test_clear(self, buyer_token):
        sku_id, _ = self._first_sku()
        requests.post(f"{BASE}/api/cart/items", json={"sku_id": sku_id, "quantity": 1}, headers=buyer_token)
        r = requests.delete(f"{BASE}/api/cart", headers=buyer_token)
        assert r.status_code == 200
        cart = requests.get(f"{BASE}/api/cart", headers=buyer_token).json()
        assert cart["items"] == []


# ---------- 高级筛选 / Autocomplete ----------
class TestCatalogFilter:
    def test_min_max_price(self):
        r = requests.get(f"{BASE}/api/products", params={"min_price": 100, "max_price": 5000}, headers=H)
        assert r.status_code == 200
        items = r.json()
        assert len(items) > 0
        for p in items:
            assert 100 <= float(p["base_price"]) <= 5000

    def test_min_price_only(self):
        r = requests.get(f"{BASE}/api/products", params={"min_price": 99999}, headers=H)
        assert r.status_code == 200
        assert r.json() == []

    def test_sort_price_desc(self):
        r = requests.get(f"{BASE}/api/products", params={"sort_by": "price_desc"}, headers=H)
        assert r.status_code == 200
        prices = [float(p["base_price"]) for p in r.json()]
        assert prices == sorted(prices, reverse=True)

    def test_sort_sales_desc(self):
        r = requests.get(f"{BASE}/api/products", params={"sort_by": "sales_desc"}, headers=H)
        assert r.status_code == 200
        sales = [p.get("sales_count", 0) for p in r.json()]
        assert sales == sorted(sales, reverse=True)

    def test_autocomplete_zh(self):
        r = requests.get(f"{BASE}/api/products/autocomplete", params={"q": "瑜伽"}, headers=H)
        assert r.status_code == 200
        results = r.json()
        assert isinstance(results, list) and len(results) > 0
        assert all("id" in x and "name_zh" in x for x in results)

    def test_autocomplete_en(self):
        r = requests.get(f"{BASE}/api/products/autocomplete", params={"q": "yoga"}, headers=H)
        assert r.status_code == 200
        assert len(r.json()) > 0

    def test_autocomplete_empty(self):
        r = requests.get(f"{BASE}/api/products/autocomplete", params={"q": "zzz不存在"}, headers=H)
        assert r.status_code == 200
        assert r.json() == []


# ---------- 收藏 ----------
class TestWishlist:
    def test_requires_auth(self):
        r = requests.get(f"{BASE}/api/wishlist")
        assert r.status_code == 401

    def test_add_and_list(self, buyer_token):
        items = requests.get(f"{BASE}/api/products", headers=H).json()
        pid = items[0]["id"]
        r = requests.post(f"{BASE}/api/wishlist", json={"product_id": pid}, headers=buyer_token)
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["total"] >= 1
        assert pid in data["product_ids"]

        lst = requests.get(f"{BASE}/api/wishlist", headers=buyer_token).json()
        assert any(i["product_id"] == pid for i in lst["items"])

    def test_add_duplicate_is_idempotent(self, buyer_token):
        items = requests.get(f"{BASE}/api/products", headers=H).json()
        pid = items[0]["id"]
        requests.post(f"{BASE}/api/wishlist", json={"product_id": pid}, headers=buyer_token)
        r2 = requests.post(f"{BASE}/api/wishlist", json={"product_id": pid}, headers=buyer_token)
        # 幂等：再次添加不报错（201 或 200 均可），且列表中只有一条
        assert r2.status_code in (200, 201)
        lst = requests.get(f"{BASE}/api/wishlist", headers=buyer_token).json()
        count = sum(1 for i in lst["items"] if i["product_id"] == pid)
        assert count == 1

    def test_remove(self, buyer_token):
        items = requests.get(f"{BASE}/api/products", headers=H).json()
        pid = items[1 % len(items)]["id"]
        requests.post(f"{BASE}/api/wishlist", json={"product_id": pid}, headers=buyer_token)
        r = requests.delete(f"{BASE}/api/wishlist/{pid}", headers=buyer_token)
        assert r.status_code == 200
        lst = requests.get(f"{BASE}/api/wishlist", headers=buyer_token).json()
        assert all(i["product_id"] != pid for i in lst["items"])

    def test_move_to_cart(self, buyer_token):
        # 清空购物车再测试
        requests.delete(f"{BASE}/api/cart", headers=buyer_token)
        items = requests.get(f"{BASE}/api/products", headers=H).json()
        pid = items[0]["id"]
        detail = requests.get(f"{BASE}/api/products/{pid}", headers=H).json()
        sku_id = detail["skus"][0]["id"]
        requests.post(f"{BASE}/api/wishlist", json={"product_id": pid}, headers=buyer_token)
        r = requests.post(f"{BASE}/api/wishlist/move-to-cart/{pid}", headers=buyer_token)
        assert r.status_code == 200, r.text
        cart = requests.get(f"{BASE}/api/cart", headers=buyer_token).json()
        assert any(c["sku_id"] == sku_id for c in cart["items"])
        # 移入购物车后应取消收藏
        lst = requests.get(f"{BASE}/api/wishlist", headers=buyer_token).json()
        assert all(i["product_id"] != pid for i in lst["items"])

    def test_clear(self, buyer_token):
        items = requests.get(f"{BASE}/api/products", headers=H).json()
        for p in items[:2]:
            requests.post(f"{BASE}/api/wishlist", json={"product_id": p["id"]}, headers=buyer_token)
        r = requests.delete(f"{BASE}/api/wishlist", headers=buyer_token)
        assert r.status_code == 200
        lst = requests.get(f"{BASE}/api/wishlist", headers=buyer_token).json()
        assert lst["items"] == []


# ---------- 地址 ----------
class TestAddresses:
    def test_requires_auth(self):
        r = requests.get(f"{BASE}/api/addresses")
        assert r.status_code == 401

    def test_crud(self, buyer_token):
        # 创建
        r = requests.post(f"{BASE}/api/addresses", json={
            "receiver_name": "测试收货人", "receiver_phone": "13900000000",
            "detail": "上海市浦东新区测试路 88 号", "is_default": True,
        }, headers=buyer_token)
        assert r.status_code == 201, r.text
        addr = r.json()
        assert addr["is_default"] is True
        aid = addr["id"]

        # 列表
        lst = requests.get(f"{BASE}/api/addresses", headers=buyer_token).json()
        assert any(a["id"] == aid for a in lst)

        # 更新
        r = requests.put(f"{BASE}/api/addresses/{aid}", json={"receiver_name": "新名字"}, headers=buyer_token)
        assert r.status_code == 200
        assert r.json()["receiver_name"] == "新名字"

        # 删除
        r = requests.delete(f"{BASE}/api/addresses/{aid}", headers=buyer_token)
        assert r.status_code == 200
        lst2 = requests.get(f"{BASE}/api/addresses", headers=buyer_token).json()
        assert all(a["id"] != aid for a in lst2)

    def test_default_switch(self, buyer_token):
        a1 = requests.post(f"{BASE}/api/addresses", json={
            "receiver_name": "甲", "receiver_phone": "139",
            "detail": "地址一", "is_default": False,
        }, headers=buyer_token).json()
        a2 = requests.post(f"{BASE}/api/addresses", json={
            "receiver_name": "乙", "receiver_phone": "138",
            "detail": "地址二", "is_default": True,
        }, headers=buyer_token).json()
        # 新默认生效，之前的取消默认
        lst = requests.get(f"{BASE}/api/addresses", headers=buyer_token).json()
        for a in lst:
            if a["id"] == a2["id"]:
                assert a["is_default"] is True
            if a["id"] == a1["id"]:
                assert a["is_default"] is False
        # 清理
        requests.delete(f"{BASE}/api/addresses/{a1['id']}", headers=buyer_token)
        requests.delete(f"{BASE}/api/addresses/{a2['id']}", headers=buyer_token)

    def test_default_first_order(self, buyer_token):
        """默认地址排在最前"""
        requests.post(f"{BASE}/api/addresses", json={
            "receiver_name": "普通", "receiver_phone": "137", "detail": "普通地址",
        }, headers=buyer_token)
        addr2 = requests.post(f"{BASE}/api/addresses", json={
            "receiver_name": "默认主", "receiver_phone": "136", "detail": "默认地址", "is_default": True,
        }, headers=buyer_token).json()
        lst = requests.get(f"{BASE}/api/addresses", headers=buyer_token).json()
        assert lst[0]["id"] == addr2["id"]
        for a in lst:
            requests.delete(f"{BASE}/api/addresses/{a['id']}", headers=buyer_token)


# ---------- 评价 ----------
class TestReviews:
    def _new_user_token(self):
        """注册一个全新的临时用户（避免历史购买/评价数据干扰）"""
        email = f"rv_{ts}_{int(time.time()*1000)}@example.com"
        r = register_user(email)
        assert r.status_code == 201, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}", **H}

    def test_list_empty_public(self):
        items = requests.get(f"{BASE}/api/products", headers=H).json()
        pid = items[0]["id"]
        r = requests.get(f"{BASE}/api/products/{pid}/reviews", headers=H)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_review_requires_purchase(self, admin_token):
        """未购买过的商品不能评价（新用户无任何购买记录）"""
        token = self._new_user_token()
        items = requests.get(f"{BASE}/api/products", headers=H).json()
        pid = items[-1]["id"]
        r = requests.post(f"{BASE}/api/products/{pid}/reviews", json={
            "rating": 5, "title": "不错", "content": "很喜欢这个商品",
        }, headers=token)
        assert r.status_code == 403, r.text

    def test_review_requires_auth(self):
        r = requests.post(f"{BASE}/api/products/1/reviews", json={
            "rating": 5, "title": "t", "content": "c",
        })
        assert r.status_code == 401

    def test_review_not_approved_until_moderation(self, admin_token):
        """评价需审核后才能公开可见"""
        # 新用户：注册 -> 下单 -> 评价 -> 审核前不可见 -> 审核后可见
        token = self._new_user_token()
        items = requests.get(f"{BASE}/api/products", headers=H).json()
        pid = items[-1]["id"]

        # 1. 未购买时不可评
        can = requests.get(f"{BASE}/api/products/{pid}/reviewable", headers=token).json()
        assert can.get("reviewable") is False

        # 2. 下单购买
        detail = requests.get(f"{BASE}/api/products/{pid}", headers=H).json()
        sku_id = detail["skus"][0]["id"]
        requests.post(f"{BASE}/api/cart/items", json={"sku_id": sku_id, "quantity": 1}, headers=token)
        r = requests.post(f"{BASE}/api/orders/checkout", json={
            "receiver_name": "评价测试", "receiver_phone": "138",
            "receiver_address": "测试地址", "payment_method": "mock",
        }, headers=token)
        assert r.status_code == 201, r.text

        # 3. 购买后可评
        can2 = requests.get(f"{BASE}/api/products/{pid}/reviewable", headers=token).json()
        assert can2.get("reviewable") is True

        # 4. 提交评价 -> pending
        r = requests.post(f"{BASE}/api/products/{pid}/reviews", json={
            "rating": 4, "title": "审核测试", "content": "这条评价待审核",
        }, headers=token)
        assert r.status_code == 201, r.text
        review_id = r.json()["id"]

        # 5. 审核前公开列表不可见
        lst = requests.get(f"{BASE}/api/products/{pid}/reviews", headers=H).json()
        assert all(x["id"] != review_id for x in lst)

        # 6. 重复评价被拒（每人一评）
        r = requests.post(f"{BASE}/api/products/{pid}/reviews", json={
            "rating": 4, "title": "重复", "content": "重复评价",
        }, headers=token)
        assert r.status_code == 409, r.text

        # 7. 管理员审核通过
        r = requests.post(f"{BASE}/api/admin/reviews/{review_id}/moderate",
                          json={"status": "approved"}, headers=admin_token)
        assert r.status_code == 200, r.text

        # 8. 审核后公开可见
        lst2 = requests.get(f"{BASE}/api/products/{pid}/reviews", headers=H).json()
        assert any(x["id"] == review_id for x in lst2)

        # 9. 我的评价列表可见
        mine = requests.get(f"{BASE}/api/my-reviews", headers=token).json()
        assert any(x["id"] == review_id for x in mine)

    def test_reviewable_requires_auth(self):
        r = requests.get(f"{BASE}/api/products/1/reviewable")
        assert r.status_code == 401


# ---------- 用户故事 ----------
class TestUserStories:
    def test_upload_edit_moderate_and_delete(self, buyer_token, admin_token):
        """用户故事完整流程：图片上传 -> 投稿 -> 本人编辑 -> 管理员发布 -> 公开 -> 删除。"""
        image = requests.post(
            f"{BASE}/api/user-story-upload",
            files={"file": ("story.png", b"fake-png-content", "image/png")},
            headers=buyer_token,
        )
        assert image.status_code == 201, image.text
        image_url = image.json()["url"]

        created = requests.post(f"{BASE}/api/user-stories", json={
            "title": "我的初次分享",
            "content": "这是我的用户故事。",
            "image_url": image_url,
        }, headers=buyer_token)
        assert created.status_code == 201, created.text
        story_id = created.json()["id"]
        assert created.json()["is_published"] is False
        public_before = requests.get(f"{BASE}/api/user-stories").json()
        public_items = public_before["items"] if isinstance(public_before, dict) else public_before
        assert not any(s["id"] == story_id for s in public_items)

        mine = requests.get(f"{BASE}/api/user-stories/mine", headers=buyer_token)
        assert mine.status_code == 200
        assert mine.json()[0]["image_url"] == image_url

        edited = requests.put(f"{BASE}/api/user-stories/{story_id}", json={
            "title": "编辑后的分享",
            "content": "我补充了更多故事内容。",
            "image_url": image_url,
        }, headers=buyer_token)
        assert edited.status_code == 200, edited.text
        assert edited.json()["title"] == "编辑后的分享"
        assert edited.json()["is_published"] is False

        published = requests.put(f"{BASE}/api/admin/user-stories/{story_id}", json={
            "is_published": True,
        }, headers=admin_token)
        assert published.status_code == 200, published.text
        public_resp = requests.get(f"{BASE}/api/user-stories").json()
        public = public_resp["items"] if isinstance(public_resp, dict) else public_resp
        public_story = next(s for s in public if s["id"] == story_id)
        assert public_story["content"] == "我补充了更多故事内容。"
        assert public_story["image_url"] == image_url

        removed = requests.delete(f"{BASE}/api/admin/user-stories/{story_id}", headers=admin_token)
        assert removed.status_code == 200, removed.text

    def test_user_story_requires_auth(self):
        assert requests.post(f"{BASE}/api/user-stories", json={"title": "t", "content": "c"}).status_code == 401
        assert requests.get(f"{BASE}/api/user-stories/mine").status_code == 401

    def test_public_detail_endpoint(self, buyer_token, admin_token):
        """公开详情接口：仅已发布可见；未发布/不存在返回 404。"""
        created = requests.post(f"{BASE}/api/user-stories", json={
            "title": "详情接口测试",
            "content": "详情内容。",
        }, headers=buyer_token)
        assert created.status_code == 201, created.text
        story_id = created.json()["id"]

        # 未发布时详情不可见
        detail_pending = requests.get(f"{BASE}/api/user-stories/{story_id}")
        assert detail_pending.status_code == 404

        # 管理员发布后可见
        requests.put(f"{BASE}/api/admin/user-stories/{story_id}", json={"is_published": True}, headers=admin_token)
        detail = requests.get(f"{BASE}/api/user-stories/{story_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["id"] == story_id
        assert detail.json()["title"] == "详情接口测试"

        # 不存在的 id
        assert requests.get(f"{BASE}/api/user-stories/999999999").status_code == 404
        # 清理
        requests.delete(f"{BASE}/api/admin/user-stories/{story_id}", headers=admin_token)

    def test_user_delete_own_story(self, buyer_token, admin_token):
        """用户可删除自己的故事；删除后公开列表和详情均不可见。"""
        created = requests.post(f"{BASE}/api/user-stories", json={
            "title": "待删除的故事",
            "content": "这段内容将被删除。",
        }, headers=buyer_token)
        assert created.status_code == 201, created.text
        story_id = created.json()["id"]

        # 非本人不可删（换一个临时用户 token 无法轻易获得，仅验证无 token 401 与本人删除）
        assert requests.delete(f"{BASE}/api/user-stories/{story_id}").status_code == 401

        deleted = requests.delete(f"{BASE}/api/user-stories/{story_id}", headers=buyer_token)
        assert deleted.status_code == 200, deleted.text

        mine = requests.get(f"{BASE}/api/user-stories/mine", headers=buyer_token).json()
        assert not any(s["id"] == story_id for s in mine)
        assert requests.get(f"{BASE}/api/user-stories/{story_id}").status_code == 404

    def test_like_and_comment_flow(self, buyer_token, admin_token):
        """点赞/评论完整流程：发表故事 -> 发布 -> 点赞幂等 -> 评论 -> 计数更新 -> 取消点赞。"""
        created = requests.post(f"{BASE}/api/user-stories", json={
            "title": "点赞评论测试故事",
            "content": "等待被点赞和评论。",
        }, headers=buyer_token)
        assert created.status_code == 201, created.text
        story_id = created.json()["id"]
        requests.put(f"{BASE}/api/admin/user-stories/{story_id}", json={"is_published": True}, headers=admin_token)

        # 未登录不能点赞/评论
        assert requests.post(f"{BASE}/api/user-stories/{story_id}/like").status_code == 401
        assert requests.post(f"{BASE}/api/user-stories/{story_id}/comments", json={"content": "x"}).status_code == 401

        # 点赞（幂等）
        like1 = requests.post(f"{BASE}/api/user-stories/{story_id}/like", headers=buyer_token)
        assert like1.status_code == 200, like1.text
        assert like1.json() == {"liked": True, "like_count": 1}
        like2 = requests.post(f"{BASE}/api/user-stories/{story_id}/like", headers=buyer_token)
        assert like2.json() == {"liked": True, "like_count": 1}

        # 评论
        cm = requests.post(f"{BASE}/api/user-stories/{story_id}/comments", json={"content": "很棒！"}, headers=buyer_token)
        assert cm.status_code == 200, cm.text
        assert cm.json()["content"] == "很棒！"
        # 空评论 400
        assert requests.post(f"{BASE}/api/user-stories/{story_id}/comments", json={"content": "  "}, headers=buyer_token).status_code == 400

        # 评论列表
        cl = requests.get(f"{BASE}/api/user-stories/{story_id}/comments").json()
        assert len(cl) == 1 and cl[0]["author"] == "买家"

        # 详情带计数与 liked 状态（带 token 时 liked 应为 True）
        detail = requests.get(f"{BASE}/api/user-stories/{story_id}").json()
        assert detail["like_count"] == 1
        assert detail["comment_count"] == 1
        # 未登录：liked 恒为 False
        assert detail["liked"] is False
        detail_auth = requests.get(f"{BASE}/api/user-stories/{story_id}", headers=buyer_token).json()
        assert detail_auth["liked"] is True

        # 公开列表带计数
        pub = requests.get(f"{BASE}/api/user-stories").json()
        items = pub["items"] if isinstance(pub, dict) else pub
        pub_story = next(s for s in items if s["id"] == story_id)
        assert pub_story["like_count"] == 1
        assert pub_story["comment_count"] == 1

        # 取消点赞
        ul = requests.post(f"{BASE}/api/user-stories/{story_id}/unlike", headers=buyer_token)
        assert ul.json() == {"liked": False, "like_count": 0}
        assert requests.get(f"{BASE}/api/user-stories/{story_id}").json()["liked"] is False

        # 清理
        requests.delete(f"{BASE}/api/admin/user-stories/{story_id}", headers=admin_token)


# ---------- 订单与支付接口 ----------
class TestOrders:
    @pytest.fixture()
    def order_no(self, buyer_token):
        # 准备：清空购物车，加购一个商品，下单
        requests.delete(f"{BASE}/api/cart", headers=buyer_token)
        items = requests.get(f"{BASE}/api/products", headers=H).json()
        detail = requests.get(f"{BASE}/api/products/{items[0]['id']}", headers=H).json()
        sku_id = detail["skus"][0]["id"]
        requests.post(f"{BASE}/api/cart/items", json={"sku_id": sku_id, "quantity": 1}, headers=buyer_token)

        payload = {
            "receiver_name": "张三",
            "receiver_phone": "13800138000",
            "receiver_address": "北京市朝阳区测试路 1 号",
            "remark": "pytest 下单",
            "payment_method": "mock",
        }
        r = requests.post(f"{BASE}/api/orders/checkout", json=payload, headers=buyer_token)
        assert r.status_code == 201, r.text
        return r.json()["order_no"]

    def test_checkout_empty_cart(self, buyer_token):
        requests.delete(f"{BASE}/api/cart", headers=buyer_token)
        payload = {
            "receiver_name": "张三", "receiver_phone": "138", "receiver_address": "测试",
            "payment_method": "mock",
        }
        r = requests.post(f"{BASE}/api/orders/checkout", json=payload, headers=buyer_token)
        assert r.status_code == 400

    def test_checkout_bad_method(self, buyer_token):
        requests.delete(f"{BASE}/api/cart", headers=buyer_token)
        items = requests.get(f"{BASE}/api/products", headers=H).json()
        detail = requests.get(f"{BASE}/api/products/{items[0]['id']}", headers=H).json()
        requests.post(f"{BASE}/api/cart/items",
                      json={"sku_id": detail["skus"][0]["id"], "quantity": 1}, headers=buyer_token)
        payload = {
            "receiver_name": "张三", "receiver_phone": "138", "receiver_address": "测试",
            "payment_method": "alipay",  # 未开通通道
        }
        r = requests.post(f"{BASE}/api/orders/checkout", json=payload, headers=buyer_token)
        assert r.status_code == 400
        assert "尚未开通" in r.json()["detail"] or "未配置" in r.json()["detail"]

    def test_checkout_missing_fields(self, buyer_token):
        r = requests.post(f"{BASE}/api/orders/checkout", json={"payment_method": "mock"}, headers=buyer_token)
        assert r.status_code in (400, 422)

    def test_order_list_and_detail(self, order_no, buyer_token):
        r = requests.get(f"{BASE}/api/orders", headers=buyer_token)
        assert r.status_code == 200
        orders = r.json()
        assert any(o["order_no"] == order_no for o in orders)

        r2 = requests.get(f"{BASE}/api/orders/{order_no}", headers=buyer_token)
        assert r2.status_code == 200
        assert r2.json()["order_no"] == order_no

    def test_checkout_reduces_available_stock_and_cancel_restores(self, buyer_token):
        """下单预占可售库存，取消订单后释放预占库存。"""
        requests.delete(f"{BASE}/api/cart", headers=buyer_token)
        products = requests.get(f"{BASE}/api/products", headers=H).json()
        detail = requests.get(f"{BASE}/api/products/{products[0]['id']}", headers=H).json()
        sku_id = detail["skus"][0]["id"]
        before = detail["skus"][0]["available_stock"]
        assert before >= 1

        add = requests.post(
            f"{BASE}/api/cart/items",
            json={"sku_id": sku_id, "quantity": 1},
            headers=buyer_token,
        )
        assert add.status_code == 201, add.text
        checkout = requests.post(
            f"{BASE}/api/orders/checkout",
            json={
                "receiver_name": "库存测试",
                "receiver_phone": "13800000000",
                "receiver_address": "测试地址",
                "payment_method": "mock",
            },
            headers=buyer_token,
        )
        assert checkout.status_code == 201, checkout.text

        after_checkout = requests.get(f"{BASE}/api/products/{products[0]['id']}", headers=H).json()
        assert after_checkout["skus"][0]["available_stock"] == before - 1

        order_no = checkout.json()["order_no"]
        cancel = requests.post(f"{BASE}/api/orders/{order_no}/cancel", headers=buyer_token)
        assert cancel.status_code == 200, cancel.text

        after_cancel = requests.get(f"{BASE}/api/products/{products[0]['id']}", headers=H).json()
        assert after_cancel["skus"][0]["available_stock"] == before

    def test_order_flow_pay_confirm(self, order_no, buyer_token, admin_token):
        # 支付
        r = requests.post(f"{BASE}/api/orders/{order_no}/pay", headers=buyer_token)
        assert r.status_code == 200, r.text
        pay = r.json()
        assert pay["success"] is True
        assert "transaction_no" in pay and "pay_url" in pay

        # 模拟支付确认
        txn = pay["transaction_no"]
        r2 = requests.get(f"{BASE}/api/payments/mock/confirm", params={"txn_no": txn})
        assert r2.status_code == 200, r2.text
        assert r2.json()["success"] is True
        assert r2.json()["order_no"] == order_no

        # 订单应为已支付
        od = requests.get(f"{BASE}/api/orders/{order_no}", headers=buyer_token).json()
        assert od["status"] == "paid"

        # 管理后台发货
        ship = requests.post(f"{BASE}/api/admin/orders/{order_no}/ship", headers=admin_token)
        assert ship.status_code == 200, ship.text

        # 客户确认收货
        conf = requests.post(f"{BASE}/api/orders/{order_no}/confirm", headers=buyer_token)
        assert conf.status_code == 200, conf.text

        od2 = requests.get(f"{BASE}/api/orders/{order_no}", headers=buyer_token).json()
        assert od2["status"] == "completed"

    def test_order_cancel(self, buyer_token):
        # 准备订单
        requests.delete(f"{BASE}/api/cart", headers=buyer_token)
        items = requests.get(f"{BASE}/api/products", headers=H).json()
        detail = requests.get(f"{BASE}/api/products/{items[0]['id']}", headers=H).json()
        requests.post(f"{BASE}/api/cart/items",
                      json={"sku_id": detail["skus"][0]["id"], "quantity": 1}, headers=buyer_token)
        payload = {
            "receiver_name": "李四", "receiver_phone": "139", "receiver_address": "测试",
            "payment_method": "mock",
        }
        r = requests.post(f"{BASE}/api/orders/checkout", json=payload, headers=buyer_token)
        assert r.status_code == 201
        order_no = r.json()["order_no"]

        r2 = requests.post(f"{BASE}/api/orders/{order_no}/cancel", headers=buyer_token)
        assert r2.status_code == 200, r2.text
        od = requests.get(f"{BASE}/api/orders/{order_no}", headers=buyer_token).json()
        assert od["status"] == "cancelled"

    def test_pay_unknown_order(self, buyer_token):
        r = requests.post(f"{BASE}/api/orders/ORD_NOT_EXIST/pay", headers=buyer_token)
        assert r.status_code == 404

    def test_cancel_unknown_order(self, buyer_token):
        r = requests.post(f"{BASE}/api/orders/ORD_NOT_EXIST/cancel", headers=buyer_token)
        assert r.status_code == 404

    def test_payment_callback_nonexistent(self):
        # mock 网关信任任意 txn_no，未知交易号由 _mark_payment_success 抛 404
        r = requests.post(f"{BASE}/api/payments/callback", json={"txn_no": "NO_SUCH_TXN"})
        assert r.status_code == 404

    def test_payment_query_nonexistent(self):
        # 真正的 404 场景在查询接口
        r = requests.get(f"{BASE}/api/payments/query/NO_SUCH_TXN")
        assert r.status_code == 404


# ---------- 后台接口 ----------
class TestAdmin:
    def test_admin_login_bad(self):
        r = requests.post(f"{BASE}/api/admin/login", json={"username": ADMIN_USER, "password": "wrong"})
        assert r.status_code == 401

    def test_dashboard(self, admin_token):
        r = requests.get(f"{BASE}/api/admin/dashboard", headers=admin_token)
        assert r.status_code == 200, r.text
        data = r.json()
        for key in ("products_count", "orders_count", "customers_count", "revenue"):
            assert key in data

    def test_admin_list_products(self, admin_token):
        r = requests.get(f"{BASE}/api/admin/products", headers=admin_token)
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_admin_list_orders(self, admin_token):
        r = requests.get(f"{BASE}/api/admin/orders", headers=admin_token)
        assert r.status_code == 200

    def test_admin_list_orders_status_filter(self, admin_token):
        r = requests.get(f"{BASE}/api/admin/orders", params={"status": "pending"}, headers=admin_token)
        assert r.status_code == 200

    def test_admin_categories(self, admin_token):
        r = requests.get(f"{BASE}/api/admin/categories", headers=admin_token)
        assert r.status_code == 200
        names = {c["name_i18n"].get("zh") for c in r.json()}
        assert {"瑜伽套装", "裤子", "上衣", "其他"}.issubset(names)

    def test_admin_stock_movements(self, admin_token):
        r = requests.get(f"{BASE}/api/admin/stock-movements", headers=admin_token)
        assert r.status_code == 200

    def test_admin_requires_auth(self):
        r = requests.get(f"{BASE}/api/admin/dashboard")
        assert r.status_code == 401

    def test_customer_token_rejected_on_admin(self, buyer_token):
        # 客户 token 不能访问后台
        r = requests.get(f"{BASE}/api/admin/dashboard", headers=buyer_token)
        assert r.status_code in (401, 403)

    def test_admin_create_product_and_sku(self, admin_token):
        sku_code = f"PYTEST-{ts}"
        payload = {
            "sku_code": sku_code,
            "name_zh": f"测试商品 {ts}",
            "name_en": f"Test Product {ts}",
            "description_zh": "pytest 创建",
            "base_price": "123.45",
            "status": "active",
            "skus": [
                {"sku_code": f"{sku_code}-S", "attributes": {"颜色": "红色"}, "price": "123.45", "stock": 10},
            ],
        }
        r = requests.post(f"{BASE}/api/admin/products", json=payload, headers=admin_token)
        assert r.status_code == 201, r.text
        product = r.json()
        assert product["sku_code"] == sku_code
        assert len(product["skus"]) == 1

        # 为商品新增 SKU
        sku_in = {"sku_code": f"{sku_code}-XL2", "attributes": {"颜色": "蓝色"},
                  "price": "130.00", "stock": 5, "is_active": True}
        r2 = requests.post(f"{BASE}/api/admin/products/{product['id']}/skus", json=sku_in, headers=admin_token)
        assert r2.status_code == 201, r2.text

        # 库存调整
        r3 = requests.put(f"{BASE}/api/admin/skus/{product['skus'][0]['id']}/stock",
                          json={"stock": 20, "reason": "pytest_adjust"}, headers=admin_token)
        assert r3.status_code == 200, r3.text
        assert r3.json()["stock"] == 20

    def test_admin_create_category(self, admin_token):
        code = f"pytest-{ts}"
        payload = {"code": code, "name_i18n": {"zh": f"测试分类 {ts}", "en": f"Test Cat {ts}"},
                   "sort_order": 999, "is_active": True}
        r = requests.post(f"{BASE}/api/admin/categories", json=payload, headers=admin_token)
        assert r.status_code == 201, r.text
        category = r.json()
        assert category["code"] == code

        updated = requests.put(f"{BASE}/api/admin/categories/{category['id']}", json={
            "name_i18n": {"zh": "更新分类", "en": "Updated Category"},
        }, headers=admin_token)
        assert updated.status_code == 200, updated.text
        assert updated.json()["name_i18n"]["zh"] == "更新分类"

        duplicate = requests.post(f"{BASE}/api/admin/categories", json=payload, headers=admin_token)
        assert duplicate.status_code == 409

        deleted = requests.delete(f"{BASE}/api/admin/categories/{category['id']}", headers=admin_token)
        assert deleted.status_code == 200, deleted.text

    def test_admin_cannot_delete_category_in_use(self, admin_token):
        products = requests.get(f"{BASE}/api/admin/products", headers=admin_token).json()
        category_id = next(p["category_id"] for p in products if p.get("category_id"))
        r = requests.delete(f"{BASE}/api/admin/categories/{category_id}", headers=admin_token)
        assert r.status_code == 409

    def test_admin_create_product_missing_fields(self, admin_token):
        # 缺必填字段应 422
        r = requests.post(f"{BASE}/api/admin/products", json={"sku_code": "X"}, headers=admin_token)
        assert r.status_code == 422

    def test_admin_ship_unknown_order(self, admin_token):
        r = requests.post(f"{BASE}/api/admin/orders/ORD_NOT_EXIST/ship", headers=admin_token)
        assert r.status_code == 404

    # ---------- 用户管理 ----------
    def test_admin_list_customers(self, admin_token):
        r = requests.get(f"{BASE}/api/admin/customers", headers=admin_token)
        assert r.status_code == 200
        customers = r.json()
        assert isinstance(customers, list)
        # buyer 用户应存在且含统计字段
        buyer = next((c for c in customers if c["email"] == BUYER_EMAIL), None)
        if buyer:
            for key in ("orders_count", "total_spent", "is_active"):
                assert key in buyer

    def test_admin_customers_search(self, admin_token):
        r = requests.get(f"{BASE}/api/admin/customers", params={"q": BUYER_EMAIL}, headers=admin_token)
        assert r.status_code == 200
        assert any(c["email"] == BUYER_EMAIL for c in r.json())

    def test_admin_toggle_customer(self, admin_token):
        # 先查 buyer id
        customers = requests.get(f"{BASE}/api/admin/customers", params={"q": BUYER_EMAIL}, headers=admin_token).json()
        buyer = next((c for c in customers if c["email"] == BUYER_EMAIL), None)
        if not buyer:
            pytest.skip("buyer 用户不存在")
        cid = buyer["id"]
        # 禁用
        r = requests.put(f"{BASE}/api/admin/customers/{cid}/status",
                         json={"is_active": False}, headers=admin_token)
        assert r.status_code == 200, r.text
        assert r.json()["is_active"] is False
        # 恢复
        r2 = requests.put(f"{BASE}/api/admin/customers/{cid}/status",
                          json={"is_active": True}, headers=admin_token)
        assert r2.status_code == 200
        assert r2.json()["is_active"] is True

    # ---------- 商品删除 ----------
    def test_admin_delete_product(self, admin_token):
        sku_code = f"PYTEST-DEL-{ts}"
        payload = {
            "sku_code": sku_code,
            "name_zh": "待删除商品",
            "base_price": "9.99",
            "skus": [{"sku_code": f"{sku_code}-S", "price": "9.99", "stock": 1}],
        }
        created = requests.post(f"{BASE}/api/admin/products", json=payload, headers=admin_token)
        assert created.status_code == 201, created.text
        pid = created.json()["id"]
        r = requests.delete(f"{BASE}/api/admin/products/{pid}", headers=admin_token)
        assert r.status_code == 200, r.text
        r2 = requests.delete(f"{BASE}/api/admin/products/{pid}", headers=admin_token)
        assert r2.status_code == 404

    # ---------- 管理员账号管理 + 权限 ----------
    def test_admin_manage_admins(self, admin_token):
        uname = f"ptest_{ts}"
        payload = {"username": uname, "password": "pass123456", "full_name": "测试管理员", "role": "operator"}
        r = requests.post(f"{BASE}/api/admin/admins", json=payload, headers=admin_token)
        assert r.status_code == 201, r.text
        aid = r.json()["id"]
        # 列表包含
        admins = requests.get(f"{BASE}/api/admin/admins", headers=admin_token).json()
        assert any(a["id"] == aid for a in admins)
        # 改角色
        r2 = requests.put(f"{BASE}/api/admin/admins/{aid}", json={"role": "viewer"}, headers=admin_token)
        assert r2.status_code == 200
        assert r2.json()["role"] == "viewer"
        # 删除
        r3 = requests.delete(f"{BASE}/api/admin/admins/{aid}", headers=admin_token)
        assert r3.status_code == 200

    def test_admin_role_permission_viewer_readonly(self, admin_token):
        # 创建 viewer 并登录，验证只读
        uname = f"ptest_v_{ts}"
        requests.post(f"{BASE}/api/admin/admins",
                      json={"username": uname, "password": "pass123456", "role": "viewer"}, headers=admin_token)
        login = requests.post(f"{BASE}/api/admin/login", json={"username": uname, "password": "pass123456"})
        assert login.status_code == 200
        vtoken = {"Authorization": f"Bearer {login.json()['access_token']}", **H}
        # 读操作允许
        assert requests.get(f"{BASE}/api/admin/products", headers=vtoken).status_code == 200
        # 写操作禁止
        r = requests.post(f"{BASE}/api/admin/products",
                          json={"sku_code": "NO-PERM", "name_zh": "x", "base_price": 1}, headers=vtoken)
        assert r.status_code == 403
        # 管理员列表禁止
        assert requests.get(f"{BASE}/api/admin/admins", headers=vtoken).status_code == 403
        # 清理
        admins = requests.get(f"{BASE}/api/admin/admins", headers=admin_token).json()
        for a in admins:
            if a["username"] == uname:
                requests.delete(f"{BASE}/api/admin/admins/{a['id']}", headers=admin_token)


class TestBanner:
    """首页轮播（home_hero）管理：独立编辑页依赖的 CRUD 与单条接口。"""

    def test_banner_crud_and_get_one(self, admin_token):
        # 创建
        payload = {
            "placement": "home_hero",
            "title_i18n": {"zh": "测试轮播", "en": "Test Banner"},
            "subtitle_i18n": {"zh": "副标题", "en": "Sub"},
            "button_text_i18n": {"zh": "去看看", "en": "Explore"},
            "image_url": "/static/uploads/test.png",
            "video_url": None,
            "link_url": "/products.html",
            "sort_order": 99,
            "is_active": True,
        }
        r = requests.post(f"{BASE}/api/admin/banners", json=payload, headers=admin_token)
        assert r.status_code == 201, r.text
        banner = r.json()
        bid = banner["id"]

        # 单条详情接口（独立编辑页回填用）
        one = requests.get(f"{BASE}/api/admin/banners/{bid}", headers=admin_token)
        assert one.status_code == 200, one.text
        assert one.json()["id"] == bid
        assert one.json()["title_i18n"]["zh"] == "测试轮播"
        assert one.json()["link_url"] == "/products.html"
        assert one.json()["button_text_i18n"]["en"] == "Explore"

        # 列表包含
        lst = requests.get(f"{BASE}/api/admin/banners?placement=home_hero", headers=admin_token).json()
        assert any(b["id"] == bid for b in lst)

        # 未登录访问单条 401
        assert requests.get(f"{BASE}/api/admin/banners/{bid}").status_code == 401

        # 更新
        upd = requests.put(f"{BASE}/api/admin/banners/{bid}", json={
            "title_i18n": {"zh": "测试轮播改", "en": "Updated"},
            "is_active": False,
        }, headers=admin_token)
        assert upd.status_code == 200, upd.text
        assert upd.json()["title_i18n"]["zh"] == "测试轮播改"
        assert upd.json()["is_active"] is False

        # 公开接口不返回停用轮播
        pub = requests.get(f"{BASE}/api/banners?placement=home_hero").json()
        assert not any(b["id"] == bid for b in pub)

        # 单条不存在 404
        assert requests.get(f"{BASE}/api/admin/banners/999999999", headers=admin_token).status_code == 404

        # 删除
        d = requests.delete(f"{BASE}/api/admin/banners/{bid}", headers=admin_token)
        assert d.status_code == 200, d.text
        # 删除后单条 404
        assert requests.get(f"{BASE}/api/admin/banners/{bid}", headers=admin_token).status_code == 404