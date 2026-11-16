"""Offline contracts for parsing Telegram post links and downloading one video."""
from __future__ import annotations

import asyncio
import dataclasses
from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from telegram_demo.cli import telegram_download as download


ERRORS = (ValueError, OSError, RuntimeError)


def video_message(message_id=42, *, mime_type="video/mp4", payload=b"offline video"):
    """Duck-typed Telethon message: no imports, authorization, or real media."""
    document = SimpleNamespace(mime_type=mime_type, size=len(payload), attributes=[])
    return SimpleNamespace(
        id=message_id, action=None, video=document, document=document,
        media=SimpleNamespace(document=document), noforwards=False,
        chat=SimpleNamespace(noforwards=False),
        file=SimpleNamespace(ext=".mp4", size=len(payload)), grouped_id=123456,
    )


def fake_client(message, payload=b"offline video"):
    async def write_media(selected_message, *, file):
        # A stream, not a directory/path, must be given to the Telegram client.
        if not callable(getattr(file, "write", None)):
            raise AssertionError("download_media must receive an exclusive file handle")
        file.write(payload)
        # An untrusted returned filename must not determine the result path.
        return "../../untrusted-remote-name.mp4"

    return SimpleNamespace(
        get_messages=AsyncMock(return_value=message),
        get_entity=AsyncMock(return_value=SimpleNamespace(noforwards=False)),
        download_media=AsyncMock(side_effect=write_media),
        iter_messages=Mock(side_effect=AssertionError("must not fetch history or albums")),
    )


class MessageLinkTests(unittest.TestCase):
    def test_result_is_the_documented_dataclass(self):
        result = download.parse_message_link("https://t.me/Example_1/42")
        self.assertIsInstance(result, download.MessageLink)
        self.assertTrue(dataclasses.is_dataclass(result))
        self.assertEqual(result.peer, "Example_1")
        self.assertEqual(result.message_id, 42)
        self.assertEqual(download.MessageLink("Example_1", 42), result)

    def test_supported_hosts_schemes_and_public_paths(self):
        for host in ("t.me", "www.t.me", "telegram.me", "www.telegram.me"):
            for scheme in ("http://", "https://"):
                for path in ("/Example_1/42", "/s/Example_1/42", "/Example_1/9/42"):
                    url = scheme + host + path
                    with self.subTest(url=url):
                        result = download.parse_message_link(url)
                        self.assertEqual((result.peer, result.message_id), ("Example_1", 42))
        for url in ("t.me/Example_1/42", "t.me/s/Example_1/42", "t.me/Example_1/9/42"):
            with self.subTest(url=url):
                self.assertEqual(download.parse_message_link(url), download.MessageLink("Example_1", 42))

    def test_private_and_topic_paths_select_final_message(self):
        for prefix in ("https://t.me", "http://telegram.me", "t.me"):
            for path in ("/c/1234567890/42", "/c/1234567890/7/42"):
                with self.subTest(url=prefix + path):
                    result = download.parse_message_link(prefix + path)
                    self.assertEqual(result.peer, -1001234567890)
                    self.assertIs(type(result.peer), int)
                    self.assertEqual(result.message_id, 42)

    def test_identifier_boundaries(self):
        for url, peer in (
            ("https://t.me/A/1", "A"),
            ("https://t.me/" + "A" * 32 + "/2147483647", "A" * 32),
            ("https://t.me/A/2147483647/2147483647", "A"),
            ("https://t.me/c/1/1", -1000000000001),
            ("https://t.me/c/123456/42", -1000000123456),
            ("https://t.me/c/9999999999/2147483647/2147483647", -1009999999999),
        ):
            with self.subTest(url=url):
                self.assertEqual(download.parse_message_link(url).peer, peer)

    def test_allowed_single_and_timestamp_query(self):
        for query in ("single", "single=", "t=12", "t=1m2s", "single&t=12", "t=12&single"):
            with self.subTest(query=query):
                self.assertEqual(
                    download.parse_message_link("https://t.me/Example/42?" + query),
                    download.MessageLink("Example", 42),
                )

    def test_invalid_urls_hosts_and_link_kinds(self):
        urls = (
            "", "https://example.org/Example/42", "https://t.me.evil.test/Example/42",
            "https://evilt.me/Example/42", "https://t.me@evil.test/Example/42",
            "https://user@t.me/Example/42", "https://user:pass@t.me/Example/42",
            "https://t.me:443/Example/42", "http://t.me:80/Example/42",
            "ftp://t.me/Example/42", "tg://resolve?domain=Example&post=42",
            "https://t.me/Example/42#fragment", "https://t.me/Example/42#",
            "https://t.me", "https://t.me/", "https://t.me/Example",
            "https://t.me/+invite", "https://t.me/joinchat/invite",
            "https://t.me/c/123", "https://t.me/s/Example",
            "https://t.me/c/42", "https://t.me/s/42", "https://t.me/s/c/123/42",
            "https://t.me/Example/1/2/3", "https://t.me/c/123/1/2/3",
            "https://t.me/Example//42", "https://t.me//Example/42",
            "https://t.me/../Example/42", "https://t.me/Exam%70le/42",
            "https://t.me/Example/%34%32", "https://t.me/Example/42?%73ingle",
            "https://t.me/Example/42?comment=5", "https://t.me/Example/42?thread=5",
            "https://t.me/Example/42?unknown=1", "https://t.me/Example/42?single&comment=5",
            "https://t.me/Example/42?single&t=1&unknown=1",
            "https://t.me/1Example/42", "https://t.me/_Example/42",
            "https://t.me/Ex-ample/42", "https://t.me/Ex.ample/42",
            "https://t.me/Đemo/42", "https://t.me/" + "A" * 33 + "/42",
        )
        for url in urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                download.parse_message_link(url)

    def test_message_topic_and_private_channel_ids_are_canonical_and_bounded(self):
        invalid_ids = ("0", "00", "01", "-1", "+1", "1.0", "1e2", "abc", "١", "１２", "2147483648")
        for identifier in invalid_ids:
            for path in (
                f"/Example/{identifier}", f"/Example/{identifier}/42",
                f"/c/123/{identifier}", f"/c/123/{identifier}/42",
            ):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    download.parse_message_link("https://t.me" + path)
        for identifier in ("0", "00", "01", "-1", "+1", "abc", "١", "10000000000", "10000000000000"):
            with self.subTest(channel_id=identifier), self.assertRaises(ValueError):
                download.parse_message_link(f"https://t.me/c/{identifier}/42")


class DownloadOneTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root / "not-yet-created" / "videos"
        self.target = download.MessageLink("Example_1", 42)
        self.payload = b"offline video"
        self.message = video_message(payload=self.payload)
        self.client = fake_client(self.message, self.payload)

    async def test_exactly_one_message_stream_destination_permissions_and_no_album(self):
        result = await download.download_one(self.client, self.target, self.output)
        self.assertIsInstance(result, Path)
        self.assertEqual(result, self.output / "telegram_Example_1_42.mp4")
        self.assertEqual(result.read_bytes(), self.payload)
        self.assertEqual(stat.S_IMODE(result.stat().st_mode), 0o600)
        self.assertEqual(list(self.output.iterdir()), [result])
        self.client.get_messages.assert_awaited_once_with("Example_1", ids=42)
        self.client.iter_messages.assert_not_called()
        self.client.download_media.assert_awaited_once()
        args, kwargs = self.client.download_media.await_args
        self.assertEqual(args, (self.message,))
        handle = kwargs["file"]
        self.assertTrue(handle.closed)

    async def test_private_peer_gets_deterministic_safe_name(self):
        target = download.MessageLink(-1001234567890, 42)
        result = await download.download_one(self.client, target, self.output)
        self.assertEqual(result.name, "telegram_-1001234567890_42.mp4")
        self.client.get_messages.assert_awaited_once_with(target.peer, ids=42)

    async def test_video_mime_document_without_convenience_video_is_accepted(self):
        self.message.video = None
        result = await download.download_one(self.client, self.target, self.output)
        self.assertEqual(result.read_bytes(), self.payload)

    async def test_video_attribute_with_direct_document_is_accepted(self):
        self.message.document.mime_type = "application/octet-stream"
        result = await download.download_one(self.client, self.target, self.output)
        self.assertEqual(result.read_bytes(), self.payload)

    async def assert_rejected_before_output(self, message):
        self.client.get_messages.return_value = message
        with self.assertRaises(ERRORS):
            await download.download_one(self.client, self.target, self.output)
        self.client.download_media.assert_not_awaited()
        self.assertFalse(self.output.exists())
        self.client.get_messages.assert_awaited_once_with(self.target.peer, ids=42)

    async def test_missing_message_rejected_without_creating_output(self):
        await self.assert_rejected_before_output(None)

    async def test_mismatched_message_id_rejected(self):
        self.message.id = 43
        await self.assert_rejected_before_output(self.message)

    async def test_nonvideo_document_rejected(self):
        self.message.video = None
        self.message.document.mime_type = "application/pdf"
        await self.assert_rejected_before_output(self.message)

    async def test_text_only_message_rejected(self):
        self.message.video = None
        self.message.document = None
        self.message.media = None
        self.message.file = None
        await self.assert_rejected_before_output(self.message)

    async def test_service_message_rejected_even_with_video_fields(self):
        self.message.action = SimpleNamespace()
        await self.assert_rejected_before_output(self.message)

    async def test_webpage_embedded_video_is_not_a_direct_video(self):
        self.message.media = SimpleNamespace(webpage=SimpleNamespace(document=self.message.document))
        self.message.document = None
        await self.assert_rejected_before_output(self.message)

    async def test_webpreview_is_rejected_even_if_document_property_is_populated(self):
        self.message.media = SimpleNamespace(webpage=SimpleNamespace(document=self.message.document))
        await self.assert_rejected_before_output(self.message)

    async def test_untrusted_remote_filename_is_not_used(self):
        self.message.document.attributes = [SimpleNamespace(file_name="../../outside.mp4")]
        self.message.file.name = "../../outside.mp4"
        result = await download.download_one(self.client, self.target, self.output)
        self.assertEqual(result, self.output / "telegram_Example_1_42.mp4")
        self.assertFalse((self.root / "outside.mp4").exists())

    async def test_protected_message_rejected(self):
        self.message.noforwards = True
        await self.assert_rejected_before_output(self.message)

    async def test_protected_message_chat_rejected(self):
        self.message.chat.noforwards = True
        await self.assert_rejected_before_output(self.message)

    async def test_protected_resolved_chat_rejected(self):
        self.message.chat = None
        self.client.get_entity.return_value.noforwards = True
        await self.assert_rejected_before_output(self.message)
        self.client.get_entity.assert_awaited_once_with(self.target.peer)

    async def test_chat_protection_checked_before_downloading(self):
        self.message.chat = None

        async def check_chat(selected_message, *, file):
            self.client.get_entity.assert_awaited_once_with(self.target.peer)
            file.write(self.payload)

        self.client.download_media.side_effect = check_chat
        await download.download_one(self.client, self.target, self.output)

    async def test_existing_destination_is_never_overwritten(self):
        self.output.mkdir(parents=True)
        existing = self.output / "telegram_Example_1_42.mp4"
        existing.write_bytes(b"existing precious video")
        with self.assertRaises(ERRORS):
            await download.download_one(self.client, self.target, self.output)
        self.assertEqual(existing.read_bytes(), b"existing precious video")
        self.client.download_media.assert_not_awaited()
        self.assertEqual(list(self.output.iterdir()), [existing])

    async def test_existing_symlink_does_not_overwrite_external_file(self):
        self.output.mkdir(parents=True)
        external = self.root / "unrelated.mp4"
        external.write_bytes(b"leave this alone")
        existing = self.output / "telegram_Example_1_42.mp4"
        existing.symlink_to(external)
        with self.assertRaises(ERRORS):
            await download.download_one(self.client, self.target, self.output)
        self.assertTrue(existing.is_symlink())
        self.assertEqual(external.read_bytes(), b"leave this alone")
        self.client.download_media.assert_not_awaited()

    async def test_download_exception_removes_only_its_incomplete_file(self):
        self.output.mkdir(parents=True)
        keep = self.output / "unrelated.mp4"
        keep.write_bytes(b"keep")
        seen_handles = []

        async def broken(selected_message, *, file):
            seen_handles.append(file)
            file.write(b"partial")
            raise OSError("simulated offline interruption")

        self.client.download_media.side_effect = broken
        with self.assertRaises(ERRORS):
            await download.download_one(self.client, self.target, self.output)
        self.assertEqual(list(self.output.iterdir()), [keep])
        self.assertEqual(keep.read_bytes(), b"keep")
        self.assertTrue(seen_handles[0].closed)

    async def test_task_cancellation_removes_new_incomplete_file(self):
        async def cancelled(selected_message, *, file):
            file.write(b"partial")
            raise asyncio.CancelledError()

        self.client.download_media.side_effect = cancelled
        with self.assertRaises(asyncio.CancelledError):
            await download.download_one(self.client, self.target, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    async def test_empty_download_rejected_and_removed(self):
        self.client.download_media.side_effect = None
        self.client.download_media.return_value = None
        with self.assertRaises(ERRORS):
            await download.download_one(self.client, self.target, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    async def test_truncated_download_rejected_and_removed(self):
        self.message.document.size = len(self.payload) + 1
        with self.assertRaises(ERRORS):
            await download.download_one(self.client, self.target, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    async def test_oversized_download_rejected_and_removed(self):
        self.message.document.size = len(self.payload) - 1
        with self.assertRaises(ERRORS):
            await download.download_one(self.client, self.target, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    async def test_unknown_document_size_still_requires_nonempty_bytes(self):
        self.message.document.size = 0
        result = await download.download_one(self.client, self.target, self.output)
        self.assertEqual(result.read_bytes(), self.payload)

    async def test_unsafe_extension_cannot_escape_output_directory(self):
        for index, extension in enumerate(("../../escape.mp4", ".mp4/../../escape", ".mp4\\escape", ".mp4\x00bad")):
            with self.subTest(extension=extension):
                self.message.file.ext = extension
                output = self.root / f"safe-{index}"
                result = await download.download_one(self.client, self.target, output)
                self.assertEqual(result.parent.resolve(), output.resolve())
                self.assertEqual(result.name, "telegram_Example_1_42.mp4")
                self.assertEqual(list(output.iterdir()), [result])


class ImportAndCliTests(unittest.TestCase):
    def test_import_help_and_invalid_link_have_no_optional_or_persistent_side_effects(self):
        code = r'''
import builtins, importlib.abc, io, os, socket, sqlite3, sys
from pathlib import Path
from unittest.mock import patch
class BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'telethon', 'dotenv', 'requests', 'numpy', 'torch'}:
            raise AssertionError('optional dependency loaded: ' + fullname)
sys.meta_path.insert(0, BlockOptional())
def forbidden(*args, **kwargs):
    raise AssertionError('network/database/login attempted')
def guarded(opener):
    def check(filename, *args, **kwargs):
        if isinstance(filename, (str, bytes, os.PathLike)):
            name = os.fsdecode(filename)
            if name.endswith(('.session', '.session-journal', '.json', '.env')):
                raise AssertionError('credential/session read attempted: ' + name)
        return opener(filename, *args, **kwargs)
    return check
with patch.object(builtins, 'open', guarded(builtins.open)), \
     patch.object(io, 'open', guarded(io.open)), \
     patch.object(sqlite3, 'connect', forbidden), \
     patch.object(socket.socket, 'connect', forbidden), \
     patch.object(socket.socket, 'connect_ex', forbidden), \
     patch.object(socket, 'create_connection', forbidden), \
     patch.object(builtins, 'input', forbidden):
    from telegram_demo.cli import telegram_download
    try:
        status = telegram_download.main(['--help'])
    except SystemExit as exc:
        status = exc.code
    assert status == 0, status
    try:
        status = telegram_download.main(['https://example.org/no-post'])
    except SystemExit as exc:
        status = exc.code
    assert isinstance(status, int) and status != 0, status
assert list(Path.cwd().iterdir()) == [], 'unexpected persistent file created'
'''
        with tempfile.TemporaryDirectory() as temporary:
            env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1",
                   "TELEGRAM_DB_PATH": str(Path(temporary) / "unused.sqlite3")}
            result = subprocess.run([sys.executable, "-B", "-c", code], cwd=temporary,
                                    env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        for option in ("--output-dir", "--session", "--api-id", "--api-hash"):
            self.assertIn(option, result.stdout)


class CliSessionTests(unittest.TestCase):
    def setUp(self):
        from telegram_demo import cli

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.session = self.root / "existing-user"
        self.session.with_suffix(".session").write_bytes(b"mock existing user session")
        self.client = SimpleNamespace(
            connect=AsyncMock(), disconnect=AsyncMock(),
            is_user_authorized=AsyncMock(return_value=True),
            start=Mock(side_effect=AssertionError("must not log in interactively")),
            sign_in=Mock(side_effect=AssertionError("must not log in interactively")),
        )
        self.api = SimpleNamespace(
            TelegramClient=Mock(return_value=self.client),
            load_credentials=Mock(return_value=(12345, "offline-api-hash")),
            session_path=Mock(return_value=str(self.session)),
        )
        self.saved = self.root / "telegram_Example_42.mp4"
        self.downloader = AsyncMock(return_value=self.saved)
        self.addCleanup(patch.stopall)
        patch.object(cli, "demo_telegram_api", self.api, create=True).start()
        patch.dict(sys.modules, {"telegram_demo.cli.demo_telegram_api": self.api}).start()
        patch.object(download, "download_one", self.downloader).start()

    def invoke(self, url="https://t.me/Example/42", *extra):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return download.main([url, *extra])

    def test_existing_authorized_session_uses_helpers_then_disconnects(self):
        output = self.root / "selected-output"
        status = self.invoke("https://t.me/Example/42", "--session", "named-session",
                             "--api-id", "12345", "--api-hash", "offline-api-hash",
                             "--output-dir", str(output))
        self.assertEqual(status, 0)
        self.api.session_path.assert_called_once_with("named-session")
        self.api.load_credentials.assert_called_once()
        args = self.api.load_credentials.call_args.args[0]
        self.assertEqual((args.api_id, args.api_hash), (12345, "offline-api-hash"))
        self.api.TelegramClient.assert_called_once_with(str(self.session), 12345, "offline-api-hash")
        self.client.connect.assert_awaited_once()
        self.client.is_user_authorized.assert_awaited_once()
        self.downloader.assert_awaited_once_with(self.client, download.MessageLink("Example", 42), output)
        self.client.disconnect.assert_awaited_once()
        self.client.start.assert_not_called()
        self.client.sign_in.assert_not_called()

    def test_default_output_is_project_media_downloads(self):
        from telegram_demo.paths import MEDIA_DIR

        self.assertEqual(self.invoke(), 0)
        self.assertEqual(self.downloader.await_args.args[2], MEDIA_DIR / "telegram_downloads")

    def test_invalid_link_is_rejected_before_credentials_or_client(self):
        self.assertNotEqual(self.invoke("https://example.org/Example/42"), 0)
        self.api.load_credentials.assert_not_called()
        self.api.session_path.assert_not_called()
        self.api.TelegramClient.assert_not_called()
        self.downloader.assert_not_awaited()

    def test_help_does_not_load_credentials_or_create_client(self):
        with redirect_stdout(io.StringIO()):
            try:
                status = download.main(["--help"])
            except SystemExit as exc:
                status = exc.code
        self.assertEqual(status, 0)
        self.api.load_credentials.assert_not_called()
        self.api.session_path.assert_not_called()
        self.api.TelegramClient.assert_not_called()

    def test_missing_session_is_not_created_or_logged_in(self):
        self.api.session_path.return_value = str(self.root / "missing-session")
        self.assertNotEqual(self.invoke(), 0)
        self.api.TelegramClient.assert_not_called()
        self.downloader.assert_not_awaited()
        self.assertFalse((self.root / "missing-session.session").exists())

    def test_expired_session_refuses_login_and_disconnects(self):
        self.client.is_user_authorized.return_value = False
        self.assertNotEqual(self.invoke(), 0)
        self.downloader.assert_not_awaited()
        self.client.start.assert_not_called()
        self.client.sign_in.assert_not_called()
        self.client.disconnect.assert_awaited_once()

    def test_connect_failure_still_disconnects(self):
        self.client.connect.side_effect = OSError("mock connection failure")
        self.assertNotEqual(self.invoke(), 0)
        self.downloader.assert_not_awaited()
        self.client.disconnect.assert_awaited_once()

    def test_download_failure_still_disconnects(self):
        self.downloader.side_effect = RuntimeError("mock download failure")
        self.assertNotEqual(self.invoke(), 0)
        self.client.disconnect.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
