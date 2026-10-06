#!/usr/bin/env python3
"""
Epic 免费游戏检查器（无凭据版）

设计要点：**完全不持有任何 Epic 凭据**。
依赖的促销接口是公开的，不需要登录、不需要 token，
所以这个脚本可以安全地跑在任何云端，仓库里没有任何秘密可泄露。

用法：
    python3 epic_free_games.py --dry-run              # 只打印，不发通知
    SERVERCHAN_KEY=xxx python3 epic_free_games.py     # 推送到微信（Server酱）
    FEISHU_WEBHOOK=xxx python3 epic_free_games.py     # 推送到飞书群
    NTFY_TOPIC=xxx python3 epic_free_games.py         # 推送到 ntfy
    DINGTALK_WEBHOOK=xxx python3 epic_free_games.py   # 推送到钉钉群

配了几个渠道就发几个，互不影响。
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
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


def http_post(url: str, data: bytes, headers: dict, timeout: int = 30):
    """返回 (http状态码, 响应正文)。"""
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


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
    """返回 (可领游戏数, 通知正文)。正文用 Markdown，飞书/Server酱/ntfy 都能渲染。"""
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
        lines.append(f"**{element['title']}**（{window['end'][:10]} 截止）")
        lines.append(f"[点此领取]({link})")
        lines.append("")
    if not active:
        lines.append("此刻没有免费游戏在活动窗口内（Epic 每周四 15:00 UTC 轮换）")

    if upcoming:
        lines.append("**下周预告**")
        for element, window in sorted(upcoming, key=lambda x: x[1]["start"]):
            lines.append(f"- {element['title']}（{window['start'][:10]} 起）")

    return len(active), "\n".join(lines)


def feishu_sign(timestamp: str, secret: str) -> str:
    """飞书自定义机器人加签：key 是 "时间戳\\n密钥"，消息体为空，输出 Base64。"""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        string_to_sign.encode("utf-8"), b"", digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def notify_serverchan(key: str, title: str, body: str) -> None:
    status, text = http_post(
        f"https://sctapi.ftqq.com/{key}.send",
        urllib.parse.urlencode({"title": title, "desp": body}).encode(),
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    print(f"Server酱 推送完成，HTTP {status}")


def notify_feishu(webhook: str, title: str, body: str, secret: str = "") -> None:
    """飞书群自定义机器人。用交互式卡片 + lark_md，保证加粗和链接正常渲染。"""
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": title}},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": body}}
            ],
        },
    }
    if secret:
        timestamp = str(int(time.time()))
        payload["timestamp"] = timestamp
        payload["sign"] = feishu_sign(timestamp, secret)

    status, text = http_post(
        webhook, json.dumps(payload).encode(), {"Content-Type": "application/json"}
    )
    result = json.loads(text) if text.strip().startswith("{") else {}
    code = result.get("code", result.get("StatusCode", -1))
    if code in (0, "0"):
        print(f"飞书 推送完成，HTTP {status}")
    else:
        # 常见：签名不对(19021/19022)、关键词不匹配(19024)、webhook 失效
        raise RuntimeError(f"飞书返回错误：{text.strip()[:200]}")


def notify_ntfy(topic: str, title: str, body: str, base: str = "https://ntfy.sh") -> None:
    # HTTP 头只能是 latin-1，中文标题必须按 RFC 2047 编码，否则 ntfy 会直接把 Unicode 当 latin-1 处理
    from email.header import Header

    status, _ = http_post(
        f"{base.rstrip('/')}/{topic}",
        body.encode(),
        {"Title": Header(title, "utf-8").encode(), "Content-Type": "text/markdown"},
    )
    print(f"ntfy 推送完成，HTTP {status}")


def notify_dingtalk(webhook: str, title: str, body: str) -> None:
    payload = json.dumps(
        {"msgtype": "markdown", "markdown": {"title": title, "text": f"### {title}\n\n{body}"}}
    ).encode()
    status, text = http_post(webhook, payload, {"Content-Type": "application/json"})
    result = json.loads(text) if text.strip().startswith("{") else {}
    if result.get("errcode", 0) == 0:
        print(f"钉钉 推送完成，HTTP {status}")
    else:
        raise RuntimeError(f"钉钉返回错误：{text.strip()[:200]}")


def configured_channels(title: str, body: str) -> list:
    """返回已配置的渠道列表 [(名称, 发送函数)]。

    用默认参数绑定当前值：闭包捕获的是变量而不是值，
    若直接写 lambda: ...v... 会让所有渠道都用上最后一次赋值的 v。
    """
    channels = []
    if key := os.environ.get("SERVERCHAN_KEY"):
        channels.append(("Server酱", lambda key=key: notify_serverchan(key, title, body)))
    if hook := os.environ.get("FEISHU_WEBHOOK"):
        secret = os.environ.get("FEISHU_SECRET", "")
        channels.append(("飞书", lambda hook=hook, secret=secret: notify_feishu(hook, title, body, secret)))
    if topic := os.environ.get("NTFY_TOPIC"):
        base = os.environ.get("NTFY_BASE", "https://ntfy.sh")
        channels.append(("ntfy", lambda topic=topic, base=base: notify_ntfy(topic, title, body, base)))
    if hook := os.environ.get("DINGTALK_WEBHOOK"):
        channels.append(("钉钉", lambda hook=hook: notify_dingtalk(hook, title, body)))
    return channels


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只打印，不发通知")
    parser.add_argument(
        "--test-sign", metavar="SECRET", help="只验证飞书签名算法能否正常计算"
    )
    args = parser.parse_args()

    if args.test_sign:
        ts = str(int(time.time()))
        print(f"timestamp  = {ts}")
        print(f"sign       = {feishu_sign(ts, args.test_sign)}")
        return 0

    count, body = build_report()
    title = f"Epic 免费游戏：{count} 个可领" if count else "Epic 免费游戏：本周无"

    print(f"标题：{title}")
    print("-" * 60)
    print(body)
    print("-" * 60)

    if args.dry_run:
        print("（dry-run，未发送通知）")
        return 0

    channels = configured_channels(title, body)
    if not channels:
        print(
            "没有配置任何通知渠道（SERVERCHAN_KEY / FEISHU_WEBHOOK / "
            "NTFY_TOPIC / DINGTALK_WEBHOOK）",
            file=sys.stderr,
        )
        return 1

    sent, failed = [], []
    for label, send in channels:
        try:
            send()
            sent.append(label)
        except Exception as exc:  # 一个渠道失败不影响其他渠道
            failed.append(label)
            print(f"{label} 推送失败：{exc}", file=sys.stderr)

    if sent:
        print(f"已推送渠道：{', '.join(sent)}")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as exc:
        print(f"网络错误：{exc}", file=sys.stderr)
        sys.exit(2)
