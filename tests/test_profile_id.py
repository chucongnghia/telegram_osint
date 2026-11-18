"""Tra cứu hồ sơ bằng ID số (không chỉ username).

Kiểm tra lớp phân loại đầu vào của `profile` và việc ID được truyền nguyên dạng
số nguyên xuống Telethon (`get_entity` phân giải ID khác username).
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from telegram_demo.analyzer import make_profile           # noqa: E402
from telegram_demo.cli import username_info as ui         # noqa: E402

HAS_TELETHON = importlib.util.find_spec("telethon") is not None


class TargetParsingTests(unittest.TestCase):
    def test_username_forms_stay_strings(self):
        for raw in ("durov", " @durov ", "t.me/durov", "BotFather"):
            self.assertIsInstance(ui.target_of(raw), str)
        self.assertEqual(ui.target_of(" @durov "), "durov")

    def test_decimal_ids_become_integers(self):
        self.assertEqual(ui.target_of("123456789"), 123456789)
        self.assertEqual(ui.target_of(" 9876543210 "), 9876543210)
        self.assertEqual(ui.target_of("@123456789"), 123456789)

    def test_marked_channel_and_basic_chat_ids(self):
        self.assertEqual(ui.target_of("-1001234567890"), -1001234567890)
        self.assertEqual(ui.target_of("-42"), -42)

    def test_mixed_or_overlong_values_are_username_like(self):
        for raw in ("12a34", "12345abc", "1" * 20, "", "user-123"):
            self.assertIsInstance(ui.target_of(raw), str)


class WebModeIdTests(unittest.TestCase):
    def test_web_mode_refuses_numeric_id_with_clear_message(self):
        args = argparse.Namespace(usernames=["123456789"], user=None, from_json="", api=False,
                                  limit=0, photo_dir="", no_photo=True, out="", common_limit=0)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ui.run(args)
        self.assertEqual(code, 1)
        self.assertIn("t.me", err.getvalue())
        self.assertIn("--api", err.getvalue())
        self.assertIn("123456789", err.getvalue())
        self.assertIn("THÔNG TIN TÀI KHOẢN", out.getvalue())


@unittest.skipUnless(HAS_TELETHON, "cần telethon cho nhánh API")
class ApiModeIdTests(unittest.TestCase):
    def test_numeric_id_reaches_lookup_entity_as_integer(self):
        from telegram_demo.cli import demo_telegram_api as api

        seen: list[object] = []

        class FakeClient:
            async def disconnect(self):
                return None

        async def fake_lookup(client, target, limit=0, photo_dir="", common_limit=0, save_photo=True):
            seen.append(target)
            return make_profile("", "user", source="telethon"), []

        async def fake_open_client(*args, **kwargs):
            return FakeClient(), ""

        saved = (api.lookup_entity, api.open_client, api.load_credentials)
        api.lookup_entity, api.open_client = fake_lookup, fake_open_client
        api.load_credentials = lambda creds: (1, "hash")
        try:
            args = argparse.Namespace(usernames=["123456789", "durov"], user=None, from_json="",
                                      api=True, limit=0, photo_dir="", no_photo=True, out="",
                                      common_limit=0, session="", api_id=None, api_hash=None)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = ui.run(args)
        finally:
            api.lookup_entity, api.open_client, api.load_credentials = saved
        self.assertEqual(code, 0)
        self.assertEqual(seen, [123456789, "durov"])


if __name__ == "__main__":
    unittest.main()
