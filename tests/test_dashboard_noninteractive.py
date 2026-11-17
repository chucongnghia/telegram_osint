"""Headless dashboard jobs may only reuse existing Telegram authorization."""
from __future__ import annotations

import asyncio
from contextlib import redirect_stdout, redirect_stderr
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
from telegram_demo.cli import demo_telegram_api as api


class DashboardNoninteractiveTests(unittest.TestCase):
    def test_missing_session_does_not_construct_or_connect(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "missing")
            with patch.object(api, "session_path", return_value=path), patch.object(api, "TelegramClient", create=True) as factory:
                client, error = asyncio.run(api.open_client(123, "fixture", interactive=False))
            self.assertIsNone(client)
            self.assertIn("không tạo phiên mới", error)
            factory.assert_not_called()
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_expired_session_does_not_start_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "existing")
            Path(path + ".session").touch()
            client = Mock(connect=AsyncMock(), is_user_authorized=AsyncMock(return_value=False), disconnect=AsyncMock(), start=AsyncMock())
            with patch.object(api, "session_path", return_value=path), patch.object(api, "session_kind", return_value="fixture"), patch.object(api, "TelegramClient", return_value=client, create=True), redirect_stdout(io.StringIO()):
                actual, error = asyncio.run(api.open_client(123, "fixture", interactive=False))
            self.assertIsNone(actual)
            self.assertIn("Phiên chưa đăng nhập", error)
            client.start.assert_not_awaited()
            client.disconnect.assert_awaited_once()

    def test_valid_session_reused_without_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "existing")
            Path(path + ".session").touch()
            client = Mock(connect=AsyncMock(), is_user_authorized=AsyncMock(return_value=True), disconnect=AsyncMock(), start=AsyncMock())
            with patch.object(api, "session_path", return_value=path), patch.object(api, "session_kind", return_value="fixture"), patch.object(api, "TelegramClient", return_value=client, create=True), redirect_stdout(io.StringIO()):
                actual, error = asyncio.run(api.open_client(123, "fixture", interactive=False))
            self.assertIs(actual, client)
            self.assertIsNone(error)
            client.start.assert_not_awaited()

    def test_watch_forwards_noninteractive_for_user_and_bot(self):
        for bot in (False, True):
            with self.subTest(bot=bot), redirect_stderr(io.StringIO()):
                args = SimpleNamespace(non_interactive=True, session="fixture")
                with patch.object(api, "open_client", AsyncMock(return_value=(None, "expired"))) as opened, patch.object(api, "open_bot_client", AsyncMock()) as bot_opened:
                    self.assertEqual(asyncio.run(api.run_watch(args, 123, "fixture", bot=bot)), 2)
                opened.assert_awaited_once_with(123, "fixture", interactive=False, session="fixture", bot=bot)
                bot_opened.assert_not_awaited()

    def test_collect_forwards_noninteractive_for_user_and_bot(self):
        for bot in (False, True):
            with self.subTest(bot=bot), redirect_stderr(io.StringIO()):
                args = SimpleNamespace(non_interactive=True, session="fixture", watch=None)
                with patch.object(api, "_missing", return_value=0), patch.object(api, "load_credentials", return_value=(123, "fixture")), patch.object(api, "bot_mode", return_value=bot), patch.object(api, "open_client", AsyncMock(return_value=(None, "expired"))) as opened, patch.object(api, "open_bot_client", AsyncMock()) as bot_opened:
                    self.assertEqual(asyncio.run(api.run_async(args)), 2)
                opened.assert_awaited_once_with(123, "fixture", interactive=False, session="fixture", bot=bot)
                bot_opened.assert_not_awaited()

    def test_noninteractive_login_options_rejected_before_auth(self):
        for flag in ("--login", "--login-bot", "--setup-bot"):
            with self.subTest(flag=flag), redirect_stderr(io.StringIO()):
                with patch.object(sys, "argv", ["api", "--non-interactive", flag]), patch.object(api, "configure_storage") as configure:
                    with self.assertRaises(SystemExit) as exc:
                        api._main()
                self.assertEqual(exc.exception.code, 2)
                configure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
