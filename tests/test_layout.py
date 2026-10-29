"""Offline regressions for the src package, root launcher, and project paths."""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import io
import json
import os
from pathlib import Path
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

from telegram_demo import analyzer, paths, storage
from telegram_demo.cli import demo_telegram as web
from telegram_demo.cli import demo_telegram_api as api
from telegram_demo.cli import find_private_groups as groups
from telegram_demo.cli import keyword_usernames as keyword
from telegram_demo.cli import username_info as profile

COMMANDS = {
    "web": "demo_telegram", "api": "demo_telegram_api", "groups": "find_private_groups",
    "search": "keyword_usernames", "profile": "username_info", "db": "database",
    "classify": "classify_messages", "video": "video",
}


def load_launcher():
    spec = importlib.util.spec_from_file_location("layout_test_launcher", ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    # Importing the launcher may configure the bytecode cache; do not leak that
    # process-global setting into unrelated tests.
    with patch.object(sys, "pycache_prefix", sys.pycache_prefix):
        spec.loader.exec_module(module)
    return module


class TestLauncher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = Path(self.tmp.name)
        self.db = self.cwd / "unused.sqlite3"
        self.env = {**os.environ, "TELEGRAM_DB_PATH": str(self.db),
                    "PYTHONDONTWRITEBYTECODE": "1"}

    def run_launcher(self, *args):
        return subprocess.run([sys.executable, "-B", str(ROOT / "main.py"), *args],
                              cwd=self.cwd, env=self.env, capture_output=True,
                              text=True, timeout=30)

    def test_root_help_lists_all_commands_without_database(self):
        for args in ((), ("--help",)):
            with self.subTest(args=args):
                result = self.run_launcher(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                for command in COMMANDS:
                    self.assertIn(command, result.stdout)
        self.assertFalse(self.db.exists())
        self.assertEqual(list(self.cwd.iterdir()), [])

    def test_all_command_help_from_other_cwd_has_no_database_side_effects(self):
        for command in COMMANDS:
            with self.subTest(command=command):
                result = self.run_launcher(command, "--help")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--help", result.stdout)
                self.assertFalse(self.db.exists())
        self.assertEqual(list(self.cwd.iterdir()), [])

    def test_help_never_reads_sessions_credentials_or_opens_network_database(self):
        code = '''
import builtins, io, os, runpy, socket, sqlite3, sys
from unittest.mock import patch
original_open, original_io_open = builtins.open, io.open
def guarded(opener):
    def check(filename, *args, **kwargs):
        if isinstance(filename, (str, bytes, os.PathLike)):
            name = os.fsdecode(filename)
            if name.endswith((".session", ".session-journal", "tg_api.json")):
                raise AssertionError("help attempted credential/session access")
        return opener(filename, *args, **kwargs)
    return check
def forbidden(*args, **kwargs):
    raise AssertionError("help attempted network/database access")
with patch.object(builtins, "open", guarded(original_open)), \\
     patch.object(io, "open", guarded(original_io_open)), \\
     patch.object(sqlite3, "connect", forbidden), \\
     patch.object(socket.socket, "connect", forbidden), \\
     patch.object(socket.socket, "connect_ex", forbidden), \\
     patch.object(socket, "create_connection", forbidden):
    for command in %r:
        sys.argv = [%r, command, "--help"]
        try: runpy.run_path(sys.argv[0], run_name="__main__")
        except SystemExit as exc:
            if exc.code != 0: raise
''' % (list(COMMANDS), str(ROOT / "main.py"))
        result = subprocess.run([sys.executable, "-B", "-c", code], cwd=self.cwd,
                                env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.db.exists())

    def test_all_legacy_basename_aliases_show_command_help(self):
        for command, module in COMMANDS.items():
            with self.subTest(command=command):
                canonical = self.run_launcher(command, "--help")
                legacy = self.run_launcher(module + ".py", "--help")
                self.assertEqual(legacy.returncode, 0, legacy.stderr)
                self.assertEqual(legacy.stdout, canonical.stdout)
        self.assertFalse(self.db.exists())

    def test_dispatch_forwards_remaining_arguments_unchanged(self):
        launcher = load_launcher()
        original_argv = sys.argv
        for command, module in COMMANDS.items():
            # Global --db deliberately precedes the db/classify internal verb.
            arguments = (["--db", "relative dir/offline.sqlite3", "stats"]
                         if command == "db" else
                         ["--db", "relative dir/offline.sqlite3", "run", "--dry-run"]
                         if command == "classify" else
                         ["--keyword", "đầu tư", "--out", "relative dir/out.json", "--", "literal"])
            seen = []
            fake = SimpleNamespace(main=lambda: seen.append(sys.argv[:]) or 7)
            with self.subTest(command=command), patch.object(
                    launcher.importlib, "import_module", return_value=fake) as imported:
                self.assertEqual(launcher.main([command, *arguments]), 7)
                imported.assert_called_once_with("telegram_demo.cli." + module)
                self.assertEqual(seen[0][1:], arguments)
                self.assertIs(sys.argv, original_argv)

    def test_dispatch_restores_argv_on_error(self):
        launcher = load_launcher()
        original_argv = sys.argv
        fake = SimpleNamespace(main=Mock(side_effect=RuntimeError("offline failure")))
        with patch.object(launcher.importlib, "import_module", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, "offline failure"):
                launcher.main(["web", "--demo"])
        self.assertIs(sys.argv, original_argv)

    def test_invalid_command_returns_error_without_database(self):
        result = self.run_launcher("not-a-command")
        self.assertEqual(result.returncode, 2)
        self.assertIn("not-a-command", result.stderr)
        self.assertFalse(self.db.exists())

    def test_db_and_classify_preserve_global_db_before_internal_command(self):
        result = self.run_launcher("db", "--db", str(self.db), "init")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.db.exists())
        result = self.run_launcher("classify", "--db", str(self.db), "run", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["messages"], [])


class TestProjectPaths(unittest.TestCase):
    def test_project_owned_paths_are_root_absolute_paths(self):
        expected = {"PROJECT_ROOT": ROOT, "CONFIG_DIR": ROOT / "config",
                    "DATA_DIR": ROOT / "data", "JSON_DIR": ROOT / "json_folder",
                    "SESSIONS_DIR": ROOT / "sessions",
                    "PROFILE_PHOTOS_DIR": ROOT / "media" / "profile_photos"}
        for name, value in expected.items():
            with self.subTest(name=name):
                self.assertEqual(Path(getattr(paths, name)), value)
                self.assertTrue(Path(getattr(paths, name)).is_absolute())
        self.assertEqual(storage.DEFAULT_DB, ROOT / "data" / "telegram.sqlite3")
        self.assertEqual(Path(api.API_CONFIG_PATH), ROOT / "json_folder" / "tg_api.json")

    def test_package_modules_and_parser_defaults_from_other_cwd(self):
        code = '''
import argparse, importlib, json
from unittest.mock import patch
from telegram_demo import analyzer, paths, storage
from telegram_demo.cli import demo_telegram_api as api
names = ["analyzer", "storage", "text_classifier", "jev_client"]
names += ["cli." + n for n in %r]
modules = {n: importlib.import_module("telegram_demo." + n).__file__ for n in names}
class StopParsing(BaseException): pass
defaults = {}
for name in ["demo_telegram", "demo_telegram_api", "username_info"]:
    def capture(parser, *args, **kwargs):
        defaults[name] = {key: parser.get_default(key) for key in ["photo_dir", "out", "db"]}
        raise StopParsing()
    with patch.object(argparse.ArgumentParser, "parse_args", capture):
        try: importlib.import_module("telegram_demo.cli." + name).main()
        except StopParsing: pass
print(json.dumps({"modules": modules, "defaults": defaults,
                  "json": analyzer.resolve_json("posts_tg.json"),
                  "session": api.session_path(""), "db": str(storage.DEFAULT_DB)}))
''' % list(COMMANDS.values())
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"}
            env.pop("TELEGRAM_DB_PATH", None)
            result = subprocess.run([sys.executable, "-B", "-c", code], cwd=tmp, env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            for filename in data["modules"].values():
                self.assertTrue(Path(filename).is_relative_to(SRC / "telegram_demo"))
            for name, defaults in data["defaults"].items():
                with self.subTest(module=name):
                    self.assertEqual(defaults["photo_dir"], str(ROOT / "media" / "profile_photos"))
                    self.assertEqual(defaults["db"], str(ROOT / "data" / "telegram.sqlite3"))
            self.assertEqual(data["defaults"]["demo_telegram"]["out"], str(ROOT / "json_folder" / "posts_web.json"))
            self.assertEqual(data["defaults"]["demo_telegram_api"]["out"], str(ROOT / "json_folder" / "posts_api.json"))
            self.assertEqual(data["json"], str(ROOT / "json_folder" / "posts_tg.json"))
            self.assertEqual(data["session"], str(ROOT / "sessions" / "tg_session"))
            self.assertEqual(data["db"], str(ROOT / "data" / "telegram.sqlite3"))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_bare_json_names_use_unchanged_root_json_folder(self):
        self.assertEqual(analyzer.json_path("result.json"), str(ROOT / "json_folder" / "result.json"))
        self.assertEqual(analyzer.json_path(""), str(ROOT / "json_folder"))
        self.assertEqual(analyzer.resolve_json("posts_tg.json"), str(ROOT / "json_folder" / "posts_tg.json"))
        self.assertEqual(analyzer.resolve_json("missing-layout-test.json"), "missing-layout-test.json")
        for explicit in ("relative/result.json", str(ROOT / "elsewhere" / "result.json")):
            with self.subTest(explicit=explicit):
                self.assertEqual(analyzer.json_path(explicit), explicit)
                self.assertEqual(analyzer.resolve_json(explicit), explicit)

    def test_existing_local_json_keeps_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "posts_tg.json"
            fixture.write_text("[]", encoding="utf-8")
            # Mock cwd-dependent existence rather than changing global cwd.
            original_exists = os.path.exists
            with patch.object(analyzer.os.path, "exists",
                              side_effect=lambda path: path == "posts_tg.json" or original_exists(path)):
                self.assertEqual(analyzer.resolve_json("posts_tg.json"), "posts_tg.json")

    def test_session_default_and_full_prefixes_never_duplicate(self):
        cases = [("", False, "tg_session"), ("other", False, "tg_session_other"),
                 ("tg_session", False, "tg_session"),
                 ("tg_session_other.session", False, "tg_session_other"),
                 ("", True, "tg_bot_session"), ("other", True, "tg_bot_other"),
                 ("tg_bot_session", True, "tg_bot_session"),
                 ("tg_bot_other.session", True, "tg_bot_other"),
                 ("tg_bot_other", False, "tg_bot_other"),
                 ("tg_session_other", True, "tg_session_other")]
        for name, bot, basename in cases:
            with self.subTest(name=name, bot=bot):
                self.assertEqual(api.session_path(name, bot=bot), str(ROOT / "sessions" / basename))

    def test_explicit_session_directories_are_not_rebased(self):
        for name in ("custom/tg_session", "custom/tg_session.session",
                     "/tmp/offline-layout-session.session", "./tg_session.session"):
            expected = name.removesuffix(".session")
            for bot in (False, True):
                with self.subTest(name=name, bot=bot):
                    self.assertEqual(api.session_path(name, bot=bot), expected)
            self.assertEqual(groups.session_path(name), expected)

    def test_private_groups_uses_shared_session_naming(self):
        for name in ("", "other", "tg_session", "tg_session_other.session", "tg_bot_other"):
            with self.subTest(name=name):
                self.assertEqual(groups.session_path(name), api.session_path(name))


class TestPackageRelativeDynamicImports(unittest.TestCase):
    def test_keyword_web_import_uses_packaged_backend(self):
        record = {"channel": "offline", "message_id": "1", "text": "Offline sample"}
        with patch.object(web, "scrape_channel", return_value=([record], {})) as scrape:
            found = keyword.records_from_web(["offline"], 2)
        scrape.assert_called_once_with("offline", 2)
        self.assertEqual(len(found), 1)
        self.assertIn("analysis", found[0])

    def test_keyword_api_import_and_collection_are_fully_mocked(self):
        args = argparse.Namespace(api_id=1, api_hash="offline", session="test", limit=2, api_search_limit=3)
        client = SimpleNamespace(disconnect=AsyncMock())
        record = {"channel": "offline", "message_id": "1", "text": "Offline sample"}
        extra = [{"username": "offline", "keywords": ["sample"]}]
        with patch.object(api, "TelegramClient", object()), \
                patch.object(api, "load_credentials", return_value=(1, "offline")), \
                patch.object(api, "open_client", new=AsyncMock(return_value=(client, None))) as opened, \
                patch.object(api, "fetch_history", new=AsyncMock(return_value=[record])) as history, \
                patch.object(api, "search_chats", new=AsyncMock(return_value=extra)) as search:
            self.assertIs(keyword._import_api(), api)
            records, rows, status = asyncio.run(keyword._collect_api(args, ["offline"], ["sample"]))
        self.assertEqual(status, 0)
        self.assertEqual(rows, extra)
        self.assertIn("analysis", records[0])
        opened.assert_awaited_once_with(1, "offline", interactive=False, session="test")
        history.assert_awaited_once_with(client, "offline", 2)
        search.assert_awaited_once_with(client, "sample", limit=3)
        client.disconnect.assert_awaited_once()

    def test_profile_web_import_uses_packaged_backend(self):
        row = {"username": "offline", "exists": True, "kind": "channel"}
        record = {"channel": "offline", "message_id": "1", "text": "Offline sample"}
        with patch.object(web, "lookup_user", return_value=row) as lookup, \
                patch.object(web, "scrape_channel", return_value=([record], {})) as scrape:
            result, records = profile.lookup_web("offline", 2, photo_dir="custom/photos", save_photo=False)
        self.assertEqual(result, row)
        self.assertIn("analysis", records[0])
        lookup.assert_called_once_with("offline", photo_dir="custom/photos", save=False)
        scrape.assert_called_once_with("offline", 2)

    def test_profile_api_import_and_lookup_are_fully_mocked(self):
        args = argparse.Namespace(api_id=1, api_hash="offline", session="test", limit=2,
                                  photo_dir="custom/photos", common_limit=3)
        client = SimpleNamespace(disconnect=AsyncMock())
        row = {"username": "offline", "kind": "user"}
        record = {"channel": "offline", "message_id": "1", "text": "Offline sample"}
        with patch.object(api, "TelegramClient", object()), \
                patch.object(api, "load_credentials", return_value=(1, "offline")), \
                patch.object(api, "open_client", new=AsyncMock(return_value=(client, None))) as opened, \
                patch.object(api, "lookup_entity", new=AsyncMock(return_value=(row, [record]))) as lookup:
            profiles, records, status = asyncio.run(profile._lookup_api(["offline"], args))
        self.assertEqual((profiles, status), ([row], 0))
        self.assertIn("analysis", records[0])
        opened.assert_awaited_once_with(1, "offline", interactive=False, session="test")
        lookup.assert_awaited_once_with(client, "offline", 2, photo_dir="custom/photos", common_limit=3)
        client.disconnect.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
