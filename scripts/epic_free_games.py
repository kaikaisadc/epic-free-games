#!/usr/bin/env python3
"""
Epic 免费游戏检查器（无凭据版）

设计要点：**完全不持有任何 Epic 凭据**。
依赖的促销接口是公开的，不需要登录、不需要 token，
所以这个脚本可以安全地跑在任何云端，仓库里没有任何秘密可泄露。

用法：
    python3 epic_free_games.py --dry-run          # 只打印，不发通知
    SERVERCHAN_KEY=xxx python3 epic_free_games.py # 推送到微信（Server酱）
    NTFY_TOPIC=xxx python3 epic_free_games.py     # 推送到 ntfy
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

FREE_GAMES_ENDPOINT = (
    "https://store-site-backend-static-ipv4.ak.epicgames.com/freeGamesPromotions"
)
PURCHASE_ENDPOINT = "https://www.epicgames.com/store/purchase"
ID_LOGIN_ENDPOINT = "https://www.epicgames.com/id/login"
# Epic 网页商店的公开 client id（来自 claabs/epicgames-freegames-node 的 constants.ts）
WEB_CLIENT_ID = "875a3b57d3a640a6b7f9b4e883463ab4"
UA = "EpicGamesLauncher/14.0.8-22004686+++Portal+Release-Live"


def http_post(url: str, data: bytes, headers: dict, timeout: int = 30) -> int:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


def checkout_link(namespace: str, offer_id: str) -> str:
    """生成 Epic 官方预填结账链接：点开后确认即可完成领取。"""
    checkout = (
        f"{PURCHASE_ENDPOINT}?highlightColor=0078f2"
        f"&offers=1-{namespace}-{offer_id}&orderId&purchaseToken&showNavigation=true"
    )
    query = urllib.parse.urlencode(
        {"noHostRedirect": "true", "redirectUrl": checkout, "client_id": WEB_CLIENT_ID}
    )
    return f"{ID_LOGIN_ENDPOINT}?{query}"


def fetch_promotions() -> list:
    url = f"{FREE_GAMES_ENDPOINT}?" + urllib.parse.urlencode(
        {"locale": "en-US", "country": "US", "allowCountries": "US"}
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    return payload["data"]["Catalog"]["searchStore"]["elements"]


def classify(element: dict, now: str) -> dict:
    promotions = element.get("promotions") or {}
    windows = []
    for key in ("promotionalOffers", "upcomingPromotionalOffers"):
        for group in promotions.get(key, []) or []:
            for offer in group["promotionalOffers"]:
                windows.append(
                    {
                        "upcoming": key.startswith("upcoming"),
                        "start": offer["startDate"],
                        "end": offer["endDate"],
                        "discount": offer["discountSetting"]["discountPercentage"],
                    }
                )

    free = [w for w in windows if w["discount"] == 0]
    active = [w for w in free if not w["upcoming"] and w["start"] <= now <= w["end"]]
    future = [w for w in free if w["upcoming"] or w["start"] > now]

    if active:
        return {"state": "active", "window": min(active, key=lambda w: w["end"])}
    if future:
        return {"state": "upcoming", "window": min(future, key=lambda w: w["start"])}
    return {"state": "other", "window": None}


def build_report() -> tuple:
    now = datetime.now(timezone.utc).isoformat()
    elements = fetch_promotions()

    active, upcoming = [], []
    for element in elements:
        info = classify(element, now)
        if info["state"] == "active":
            active.append((element, info["window"]))
        elif info["state"] == "upcoming":
            upcoming.append((element, info["window"]))

    lines = ["**本周可以白领的游戏**", ""]
    for element, window in active:
        link = checkout_link(element["namespace"], element["id"])
        ends = window["end"][:10]
        lines.append(f"- **{element['title']}**  _（{ends} 截止）_")
        lines.append(f"  领取：{link}")
    if not active:
        lines.append("- 此刻没有免费游戏在活动窗口内（Epic 每周四 15:00 UTC 轮换）")

    if upcoming:
        lines.append("")
        lines.append("**下周预告**")
        for element, window in sorted(upcoming, key=lambda x: x[1]["start"]):
            lines.append(f"- {element['title']}  _{window['start'][:10]} 起_")

    return active, "\n".join(lines)


def notify_serverchan(key: str, title: str, body: str) -> None:
    url = f"https://sctapi.ftqq.com/{key}.send"
    data = urllib.parse.urlencode({"title": title, "desp": body}).encode()
    status = http_post(url, data, {"Content-Type": "application/x-www-form-urlencoded"})
    print(f"Server酱 推送完成，HTTP {status}")


def notify_ntfy(topic: str, title: str, body: str, base: str = "https://ntfy.sh") -> None:
    status = http_post(
        f"{base.rstrip('/')}/{topic}",
        body.encode(),
        {"Title": title.encode("ascii", "ignore").decode() or "Epic Free Games",
         "Content-Type": "text/markdown"},
    )
    print(f"ntfy 推送完成，HTTP {status}")


def notify_dingtalk(webhook: str, title: str, body: str) -> None:
    payload = json.dumps(
        {"msgtype": "markdown", "markdown": {"title": title, "text": f"### {title}\n\n{body}"}}
    ).encode()
    status = http_post(webhook, payload, {"Content-Type": "application/json"})
    print(f"钉钉 推送完成，HTTP {status}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只打印，不发通知")
    args = parser.parse_args()

    active, body = build_report()
    title = f"Epic 免费游戏：{len(active)} 个可领" if active else "Epic 免费游戏：本周无"

    print(f"标题：{title}")
    print("-" * 60)
    print(body)
    print("-" * 60)

    if args.dry_run:
        print("（dry-run，未发送通知）")
        return 0

    sent = False
    if key := os.environ.get("SERVERCHAN_KEY"):
        notify_serverchan(key, title, body)
        sent = True
    if topic := os.environ.get("NTFY_TOPIC"):
        notify_ntfy(topic, title, body, os.environ.get("NTFY_BASE", "https://ntfy.sh"))
        sent = True
    if webhook := os.environ.get("DINGTALK_WEBHOOK"):
        notify_dingtalk(webhook, title, body)
        sent = True

    if not sent:
        print("没有配置任何通知渠道（SERVERCHAN_KEY / NTFY_TOPIC / DINGTALK_WEBHOOK）", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as exc:
        print(f"网络错误：{exc}", file=sys.stderr)
        sys.exit(2)
