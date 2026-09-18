"""PayPal webhook 配置体检脚本（只读；不会打印任何密钥）

用途：改完 .env / 订阅配置后，一条命令确认「订阅地址、订阅事件、样例事件结构」都对，
避免出现「webhook id 填了但地址写错」这种静默失效（PayPal 会一直投递到 404 的地址，
非 2xx 还会重投 25 次 / 3 天，站点侧什么都看不到）。

用法：
    .venv/bin/python test/paypal_webhook_audit.py                 # 只检查
    .venv/bin/python test/paypal_webhook_audit.py --fix-url       # 地址不对就顺手纠正
    .venv/bin/python test/paypal_webhook_audit.py --base-url https://staging.example.com

退出码：0 = 全部通过；1 = 有问题（需要处理）
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"

# 我们代码里注册的 webhook 路由（必须与 app/routers/payments.py 的 prefix + path 一致）
WEBHOOK_PATH = "/api/payments/paypal/webhook"

# 代码里实际处理的事件；订阅里缺了这些就会掉单 / 退款不同步
HANDLED_EVENTS = [
    "PAYMENT.CAPTURE.COMPLETED",
    "PAYMENT.CAPTURE.DENIED",
    "PAYMENT.CAPTURE.DECLINED",
    "PAYMENT.CAPTURE.REVERSED",
    "PAYMENT.CAPTURE.REFUNDED",
    "CHECKOUT.ORDER.APPROVED",
]


def load_env() -> dict[str, str]:
    """从 .env 读 PAYPAL_* 配置（不打印值，避免密钥进日志）"""
    values: dict[str, str] = {}
    if not ENV_PATH.exists():
        return values
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("PAYPAL_") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip()
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description="PayPal webhook 配置体检")
    parser.add_argument("--base-url", default="https://yoyole.vip", help="站点公网地址")
    parser.add_argument("--fix-url", action="store_true", help="订阅地址不对时自动纠正")
    parser.add_argument("--fix-events", action="store_true", help="补齐代码会处理但未订阅的事件（保留原有订阅）")
    parser.add_argument("--sample-event", action="store_true", help="顺带取一份样例事件比对结构")
    args = parser.parse_args()

    env = load_env()
    for key in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_WEBHOOK_ID"):
        if not env.get(key):
            print(f"✗ .env 缺少 {key}")
            return 1

    host = "api-m.paypal.com" if env.get("PAYPAL_MODE", "sandbox").lower() == "live" else "api-m.sandbox.paypal.com"
    expected_url = args.base_url.rstrip("/") + WEBHOOK_PATH
    problems: list[str] = []

    token_resp = requests.post(
        f"https://{host}/v1/oauth2/token",
        auth=(env["PAYPAL_CLIENT_ID"], env["PAYPAL_CLIENT_SECRET"]),
        data={"grant_type": "client_credentials"},
        timeout=15,
    )
    if token_resp.status_code != 200:
        print(f"✗ 取 access_token 失败（{token_resp.status_code}）：凭据可能填错，或环境与 PAYPAL_MODE 不匹配")
        return 1
    headers = {
        "Authorization": f"Bearer {token_resp.json()['access_token']}",
        "Content-Type": "application/json",
    }
    print(f"✓ 凭据可用（{host}）")

    webhook_id = env["PAYPAL_WEBHOOK_ID"]
    resp = requests.get(f"https://{host}/v1/notifications/webhooks/{webhook_id}", headers=headers, timeout=15)
    if resp.status_code != 200:
        print(f"✗ webhook id {webhook_id} 查不到（{resp.status_code}），请核对 PAYPAL_WEBHOOK_ID")
        return 1

    webhook = resp.json()
    url = webhook.get("url", "")
    events = [e.get("name") for e in (webhook.get("event_types") or [])]
    print(f"✓ webhook id 有效，当前订阅：{url}")
    print(f"  订阅事件数：{len(events)}" + ("（通配 *，覆盖全部）" if len(events) > 60 else ""))

    if url != expected_url:
        problems.append(f"订阅地址不对：期望 {expected_url}，实际 {url}")
    if events and "*" not in events:
        missing = [e for e in HANDLED_EVENTS if e not in events]
        if missing:
            problems.append(f"缺事件订阅：{', '.join(missing)}")
            if args.fix_events:
                # 用「原有 ∪ 缺失」整体替换：PayPal 的 PATCH 只能整段替换 event_types，
                # 直接只发缺失的几个会把原有订阅清掉。
                patch = requests.patch(
                    f"https://{host}/v1/notifications/webhooks/{webhook_id}",
                    headers=headers,
                    json=[{
                        "op": "replace",
                        "path": "/event_types",
                        "value": [{"name": n} for n in (events + missing)],
                    }],
                    timeout=15,
                )
                if patch.status_code == 200:
                    print(f"✓ 已补齐事件订阅：{', '.join(missing)}")
                    problems = [p for p in problems if not p.startswith("缺事件订阅")]
                else:
                    print(f"✗ 补齐事件订阅失败（{patch.status_code}）：{patch.text[:200]}")

    # 站点是否真的挂上了这个路由（新代码没部署时这里是 404/405）
    probe = requests.post(
        args.base_url.rstrip("/") + WEBHOOK_PATH,
        headers={"Content-Type": "application/json"},
        data=b"{}",
        timeout=15,
    )
    if probe.status_code in (404, 405):
        problems.append(
            f"{expected_url} 现在返回 {probe.status_code}：新代码还没部署，或 nginx 没转发 /api"
        )
    elif probe.status_code == 400:
        print("✓ 线上路由已存在（无签名 → 400 拒绝，符合预期）")
    else:
        print(f"  线上路由探测返回 {probe.status_code}（非预期，建议人工看一眼）")

    if args.fix_url and url != expected_url:
        patch = requests.patch(
            f"https://{host}/v1/notifications/webhooks/{webhook_id}",
            headers=headers,
            json=[{"op": "replace", "path": "/url", "value": expected_url}],
            timeout=15,
        )
        if patch.status_code == 200:
            print(f"✓ 已把订阅地址改为 {expected_url}")
            problems = [p for p in problems if not p.startswith("订阅地址不对")]
        else:
            print(f"✗ 纠正订阅地址失败（{patch.status_code}）：{patch.text[:200]}")

    if args.sample_event:
        resp = requests.post(
            f"https://{host}/v1/notifications/simulate-event",
            headers=headers,
            json={"webhook_id": webhook_id, "event_type": "PAYMENT.CAPTURE.COMPLETED"},
            timeout=15,
        )
        if resp.status_code < 400:
            resource = (resp.json().get("resource") or {})
            related = (resource.get("supplementary_data") or {}).get("related_ids")
            print("✓ 样例事件结构：")
            print(f"    resource.supplementary_data.related_ids = {related}   ← 代码靠这个定位支付记录")
            print(f"    resource.id（capture id）              = {resource.get('id')}")
            print(f"    resource.amount                        = {resource.get('amount')}")
            print(json.dumps(resource.get("supplementary_data"), ensure_ascii=False))
        else:
            print(f"  取样例事件失败（{resp.status_code}）：{resp.text[:200]}")

    if problems:
        print("\n需要处理：")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print("\n全部检查通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
