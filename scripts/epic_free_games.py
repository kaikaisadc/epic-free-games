"""读取 Epic 的免费促销，并生成可在浏览器中确认的领取链接。"""

import argparse
import base64
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.header import Header
from functools import partial
from http.client import HTTPException
from itertools import chain
from typing import Literal

FREE_GAMES_ENDPOINT = (
    "https://store-site-backend-static-ipv4.ak.epicgames.com/freeGamesPromotions"
)
PURCHASE_ENDPOINT = "https://www.epicgames.com/store/purchase"
ID_LOGIN_ENDPOINT = "https://www.epicgames.com/id/login"
WEB_CLIENT_ID = "875a3b57d3a640a6b7f9b4e883463ab4"

# 与工作流的假日 cron 和 UTC 触发时刻保持一致。
DAILY_CRONS = {"17 23 * 12 *", "17 23 1-8 1 *"}
SCHEDULE_TIME = timedelta(hours=23, minutes=17)
FRESH_WINDOW = timedelta(hours=25)
HTTP_TIMEOUT = 30

type OfferId = tuple[str, str]
type Channel = tuple[str, Callable[[], None]]


class CatalogError(Exception):
    """促销数据读取失败。"""


class NotificationError(Exception):
    """通知服务未确认接收。"""


@dataclass(frozen=True, slots=True)
class Window:
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class Game:
    title: str
    offer_id: OfferId
    window: Window


def fetch_promotions() -> list[object]:
    query = urllib.parse.urlencode(
        {"locale": "en-US", "country": "US", "allowCountries": "US"}
    )
    request = urllib.request.Request(
        f"{FREE_GAMES_ENDPOINT}?{query}",
        headers={"Accept": "application/json", "User-Agent": "epic-free-games"},
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise CatalogError(f"Epic 目录返回 HTTP {exc.code}") from None
    except (OSError, HTTPException):
        raise CatalogError("Epic 目录连接失败") from None
    except ValueError:
        raise CatalogError("Epic 目录响应无法解析") from None

    match payload:
        case {
            "data": {"Catalog": {"searchStore": {"elements": list() as elements}}}
        } if not payload.get("errors"):
            return elements
        case _:
            raise CatalogError("Epic 目录响应缺少促销列表")


def free_windows(groups: object) -> Iterator[Window]:
    if not isinstance(groups, list):
        return
    for group in groups:
        match group:
            case {"promotionalOffers": list() as offers}:
                for offer in offers:
                    if (window := parse_window(offer)) is not None:
                        yield window


def parse_window(offer: object) -> Window | None:
    match offer:
        case {
            "startDate": str() as start,
            "endDate": str() as end,
            "discountSetting": {"discountPercentage": int() as percentage},
        } if type(percentage) is int and percentage == 0:
            try:
                starts_at = datetime.fromisoformat(start)
                ends_at = datetime.fromisoformat(end)
                if (
                    starts_at.tzinfo is not None
                    and ends_at.tzinfo is not None
                    and starts_at < ends_at
                ):
                    return Window(starts_at.astimezone(UTC), ends_at.astimezone(UTC))
            except (ValueError, OverflowError):
                return None
    return None


def classify(
    element: object, now: datetime
) -> tuple[Literal["active", "upcoming"], Game] | None:
    match element:
        case {
            "title": str() as title,
            "namespace": str() as namespace,
            "id": str() as offer_id,
            "promotions": dict() as promotions,
        } if title.strip() and namespace and offer_id:
            offer = namespace, offer_id
        case _:
            return None

    current = list(free_windows(promotions.get("promotionalOffers")))
    match element:
        case {"price": {"totalPrice": {"discountPrice": int() as price}}} if (
            type(price) is int and price == 0
        ):
            active = min(
                (window for window in current if window.start <= now < window.end),
                key=lambda window: (window.end, window.start),
                default=None,
            )
            if active is not None:
                return "active", Game(title, offer, active)

    future = min(
        (
            window
            for window in chain(
                current, free_windows(promotions.get("upcomingPromotionalOffers"))
            )
            if window.start > now
        ),
        key=lambda window: window.start,
        default=None,
    )
    if future is not None:
        return "upcoming", Game(title, offer, future)
    return None


def checkout_link(offers: Iterable[OfferId]) -> str:
    offers_query = urllib.parse.urlencode(
        [("offers", f"1-{namespace}-{offer_id}") for namespace, offer_id in offers]
    )
    checkout = (
        f"{PURCHASE_ENDPOINT}?highlightColor=0078f2&{offers_query}"
        "&orderId&purchaseToken&showNavigation=true"
    )
    query = urllib.parse.urlencode(
        {"noHostRedirect": "true", "redirectUrl": checkout, "client_id": WEB_CLIENT_ID}
    )
    return f"{ID_LOGIN_ENDPOINT}?{query}"


def markdown_title(title: str) -> str:
    return re.sub(r"([\\\x60*_\[\]<>])", r"\\\1", " ".join(title.split()))


def build_report(
    elements: Iterable[object], now: datetime, *, only_new: bool
) -> tuple[int, str]:
    active: dict[OfferId, Game] = {}
    upcoming: dict[OfferId, Game] = {}
    cutoff = now - FRESH_WINDOW
    for element in elements:
        match classify(element, now):
            case "active", game if not only_new or game.window.start >= cutoff:
                active.setdefault(game.offer_id, game)
            case "upcoming", game:
                upcoming.setdefault(game.offer_id, game)

    lines = ["**新上架的免费游戏**" if only_new else "**本周可以白领的游戏**", ""]
    if len(active) > 1:
        lines.extend(
            [f"**[打开合并结账页（{len(active)} 款）]({checkout_link(active)})**", ""]
        )
    for game in active.values():
        lines.extend(
            [
                f"**{markdown_title(game.title)}**（{game.window.end:%Y-%m-%d %H:%M} UTC 截止）",
                f"[前往 Epic 领取]({checkout_link([game.offer_id])})",
                "",
            ]
        )
    if not active:
        lines.extend(["当前没有符合条件的免费促销。", ""])

    previews = sorted(upcoming.values(), key=lambda game: game.window.start)
    if previews:
        lines.append("**后续预告**")
        for game in previews:
            lines.append(
                f"- {markdown_title(game.title)}（{game.window.start:%Y-%m-%d %H:%M} UTC 起）"
            )
    return len(active), "\n".join(lines).rstrip()


def http_post(url: str, data: bytes, headers: dict[str, str]) -> bytes:
    try:
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        exc.close()
        raise NotificationError(f"HTTP {exc.code}") from None
    except (OSError, HTTPException):
        raise NotificationError("连接或响应读取失败") from None
    except ValueError:
        raise NotificationError("通知地址或请求格式无效") from None


def post_json(url: str, payload: Mapping[str, object]) -> bytes:
    return http_post(
        url, json.dumps(payload).encode(), {"Content-Type": "application/json"}
    )


def ensure_accepted(response: bytes, *code_keys: str) -> None:
    try:
        payload = json.loads(response)
    except ValueError:
        raise NotificationError("响应无法解析") from None
    if not isinstance(payload, dict):
        raise NotificationError("响应缺少状态码")
    code = next((payload[key] for key in code_keys if key in payload), None)
    if (type(code) is int and code == 0) or code == "0":
        return
    if type(code) is int:
        raise NotificationError(f"服务返回错误码 {code}")
    raise NotificationError("响应缺少有效状态码")


def feishu_sign(timestamp: str, secret: str) -> str:
    key = f"{timestamp}\n{secret}".encode()
    return base64.b64encode(hmac.digest(key, b"", "sha256")).decode("ascii")


def notify_serverchan(key: str, title: str, body: str) -> None:
    response = http_post(
        f"https://sctapi.ftqq.com/{urllib.parse.quote(key, safe='')}.send",
        urllib.parse.urlencode({"title": title, "desp": body}).encode(),
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    ensure_accepted(response, "code")


def notify_feishu(webhook: str, title: str, body: str, secret: str = "") -> None:
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": title}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": body}}],
        },
    }
    if secret:
        timestamp = str(int(time.time()))
        payload.update(timestamp=timestamp, sign=feishu_sign(timestamp, secret))
    ensure_accepted(post_json(webhook, payload), "code", "StatusCode")


def notify_ntfy(topic: str, title: str, body: str, base: str) -> None:
    http_post(
        f"{base.rstrip('/')}/{urllib.parse.quote(topic, safe='')}",
        body.encode(),
        {"Title": Header(title, "utf-8").encode(), "Content-Type": "text/markdown"},
    )


def notify_dingtalk(webhook: str, title: str, body: str) -> None:
    payload = {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": f"### {title}\n\n{body}"},
    }
    ensure_accepted(post_json(webhook, payload), "errcode")


def configured_channels(title: str, body: str) -> list[Channel]:
    channels: list[Channel] = []
    if key := os.getenv("SERVERCHAN_KEY"):
        channels.append(("Server酱", partial(notify_serverchan, key, title, body)))
    if webhook := os.getenv("FEISHU_WEBHOOK"):
        secret = os.getenv("FEISHU_SECRET", "")
        channels.append(("飞书", partial(notify_feishu, webhook, title, body, secret)))
    if topic := os.getenv("NTFY_TOPIC"):
        base = os.getenv("NTFY_BASE") or "https://ntfy.sh"
        channels.append(("ntfy", partial(notify_ntfy, topic, title, body, base)))
    if webhook := os.getenv("DINGTALK_WEBHOOK"):
        channels.append(("钉钉", partial(notify_dingtalk, webhook, title, body)))
    return channels


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="预览通知")
    parser.add_argument("--test-sign", metavar="SECRET", help="预览飞书签名")
    args = parser.parse_args(argv)

    if args.test_sign is not None:
        timestamp = str(int(time.time()))
        print(f"timestamp = {timestamp}")
        print(f"sign = {feishu_sign(timestamp, args.test_sign)}")
        return 0

    now = datetime.now(UTC)
    only_new = os.getenv("TRIGGER_SCHEDULE", "").strip() in DAILY_CRONS
    if only_new and not args.dry_run and (now - SCHEDULE_TIME).weekday() == 3:
        print("假日任务与周报重叠，由周报发送通知。")
        return 0

    try:
        count, body = build_report(fetch_promotions(), now, only_new=only_new)
    except CatalogError as exc:
        print(f"读取失败：{exc}", file=sys.stderr)
        return 2
    title = f"Epic 免费游戏：{count} 款可领" if count else "Epic 免费游戏：当前无"
    print(f"{title}\n\n{body}")

    if args.dry_run:
        return 0
    if only_new and count == 0:
        print("没有新上架的免费促销，跳过推送。")
        return 0

    channels = configured_channels(title, body)
    if not channels:
        print("请至少配置一个通知渠道。", file=sys.stderr)
        return 1

    exit_code = 0
    for label, send in channels:
        try:
            send()
        except NotificationError as exc:
            print(f"{label} 推送失败：{exc}", file=sys.stderr)
            exit_code = 1
        else:
            print(f"{label} 推送成功")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
