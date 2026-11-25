import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from scripts import epic_free_games as epic

NOW = datetime(2026, 11, 26, 16, tzinfo=UTC)


def offer(
    offer_id="a",
    *,
    start="2026-11-26T16:00:00.000Z",
    end="2026-11-27T16:00:00Z",
    price=0,
):
    return {
        "title": f"Game {offer_id}",
        "namespace": "store",
        "id": offer_id,
        "price": {"totalPrice": {"discountPrice": price}},
        "promotions": {
            "promotionalOffers": [
                {
                    "promotionalOffers": [
                        {
                            "startDate": start,
                            "endDate": end,
                            "discountSetting": {"discountPercentage": 0},
                        }
                    ]
                }
            ]
        },
    }


class ReportTests(unittest.TestCase):
    def test_uses_real_utc_instants_and_exclusive_end(self):
        active = offer(start="2026-11-26T08:00:00-08:00")
        ended = offer("ended", start="2026-11-25T00:00:00Z", end=NOW.isoformat())
        count, text = epic.build_report([active, ended], NOW, only_new=False)

        self.assertEqual(count, 1)
        self.assertIn("Game a", text)
        self.assertNotIn("Game ended", text)

    def test_ignores_invalid_rows_and_refuses_paid_offers(self):
        paid = offer("paid", price=400)
        malformed = offer("invalid", start="invalid")
        future = offer("future", start="2026-11-27T00:00:00Z", price=1200)

        count, text = epic.build_report(
            [None, {}, malformed, paid, future], NOW, only_new=False
        )

        self.assertEqual(count, 0)
        self.assertNotIn("Game paid", text)
        self.assertIn("Game future", text)
        self.assertNotIn("前往 Epic 领取", text)

    def test_report_deduplicates_offers_and_preserves_combined_checkout(self):
        count, text = epic.build_report(
            [offer(), offer(), offer("b")], NOW, only_new=False
        )

        self.assertEqual(count, 2)
        self.assertEqual(text.count("**Game a**"), 1)
        self.assertIn("打开合并结账页（2 款）", text)
        signup_query = parse_qs(
            urlsplit(epic.checkout_link([("store", "a"), ("store", "b")])).query
        )
        checkout = urlsplit(signup_query["redirectUrl"][0])
        self.assertEqual(checkout.hostname, "www.epicgames.com")
        self.assertEqual(parse_qs(checkout.query)["offers"], ["1-store-a", "1-store-b"])

    def test_holiday_report_filters_old_offers(self):
        old = offer("old", start="2026-11-25T14:59:00Z")
        count, text = epic.build_report([old, offer()], NOW, only_new=True)

        self.assertEqual(count, 1)
        self.assertNotIn("Game old", text)

    def test_rejects_malformed_catalog(self):
        with (
            patch.object(
                epic.urllib.request,
                "urlopen",
                return_value=io.BytesIO(b'{"data": null}'),
            ),
            self.assertRaises(epic.CatalogError),
        ):
            epic.fetch_promotions()


class NotificationTests(unittest.TestCase):
    def test_serverchan_validates_business_status(self):
        with (
            patch.object(
                epic, "http_post", return_value=b'{"code": 102, "message": "secret"}'
            ),
            self.assertRaisesRegex(epic.NotificationError, "^服务返回错误码 102$"),
        ):
            epic.notify_serverchan("send-key", "title", "body")

    def test_feishu_legacy_status_and_signed_payload(self):
        with (
            patch.object(epic.time, "time", return_value=42),
            patch.object(epic, "post_json", return_value=b'{"StatusCode": 0}') as post,
        ):
            epic.notify_feishu("https://example.com/bot", "title", "body", "secret")

        _, payload = post.call_args.args
        self.assertEqual(payload["timestamp"], "42")
        self.assertEqual(payload["sign"], epic.feishu_sign("42", "secret"))

    def test_dingtalk_requires_an_acknowledgement(self):
        with (
            patch.object(epic, "post_json", return_value=b"{}"),
            self.assertRaises(epic.NotificationError),
        ):
            epic.notify_dingtalk("https://example.com/bot", "title", "body")

    def test_ntfy_encodes_chinese_title_and_uses_configured_base(self):
        with patch.object(epic, "http_post", return_value=b"OK") as post:
            epic.notify_ntfy("topic", "免费游戏", "body", "https://example.com/")

        url, _, headers = post.call_args.args
        self.assertEqual(url, "https://example.com/topic")
        self.assertTrue(headers["Title"].isascii())

    def test_http_error_omits_webhook_url(self):
        secret_url = "https://example.com/bot?access_token=secret"
        with (
            patch.object(
                epic.urllib.request,
                "urlopen",
                side_effect=HTTPError(secret_url, 403, secret_url, {}, None),
            ),
            self.assertRaisesRegex(epic.NotificationError, "^HTTP 403$"),
        ):
            epic.http_post(secret_url, b"body", {})


class CliTests(unittest.TestCase):
    def test_holiday_thursday_skips_even_after_utc_midnight(self):
        late_thursday_run = datetime(2026, 12, 4, 0, 5, tzinfo=UTC)
        with (
            patch.dict(os.environ, {"TRIGGER_SCHEDULE": "17 23 * 12 *"}, clear=True),
            patch.object(epic, "datetime") as clock,
            patch.object(epic, "fetch_promotions") as fetch,
            redirect_stdout(io.StringIO()),
        ):
            clock.now.return_value = late_thursday_run
            self.assertEqual(epic.main([]), 0)
        fetch.assert_not_called()

    def test_dry_run_does_not_send_messages(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(epic, "fetch_promotions", return_value=[]),
            patch.object(epic, "configured_channels") as channels,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(epic.main(["--dry-run"]), 0)
        channels.assert_not_called()

    def test_failure_does_not_prevent_other_channels_from_sending(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(epic, "fetch_promotions", return_value=[]),
            patch.object(
                epic,
                "notify_serverchan",
                side_effect=epic.NotificationError("HTTP 400"),
            ),
            patch.object(epic, "notify_ntfy") as notify,
            patch.dict(os.environ, {"SERVERCHAN_KEY": "key", "NTFY_TOPIC": "topic"}),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()) as stderr,
        ):
            self.assertEqual(epic.main([]), 1)
        notify.assert_called_once()
        self.assertIn("HTTP 400", stderr.getvalue())
