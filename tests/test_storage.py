"""Offline SQLite tests; never use the production database or Telegram session."""
from __future__ import annotations

import copy
from contextlib import closing
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import storage
from storage import Database


def post(channel="test_channel", mid="1", **fields):
    return {"channel": channel, "message_id": mid, "text": "Đầu tư 100%_ an toàn?",
            "analysis": {"scam_score": 42, "level": "Suspicious"}, **fields}


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "data" / "test.sqlite3"
        self.db = Database(self.path)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_schema_pragmas_and_permissions(self):
        self.assertEqual(self.db.conn.execute("PRAGMA user_version").fetchone()[0], storage.SCHEMA_VERSION)
        self.assertEqual(self.db.conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(self.db.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.db.conn.execute("PRAGMA busy_timeout").fetchone()[0], 10000)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        with Database(self.path) as again:
            self.assertEqual(again.stats()["runs"], 0)

    def test_roundtrip_unicode_and_unknown_fields(self):
        payload = {"posts": [post(extra={"x": "Tiếng Việt"})], "custom": [1, 2], "backend": "web"}
        rid = self.db.save_payload(payload)
        self.assertEqual(self.db.payload(rid), payload)
        self.assertEqual(self.db.messages()[0], payload["posts"][0])

    def test_message_dedup_and_historical_analysis(self):
        payload = {"posts": [post()]}
        first = self.db.save_payload(payload)
        changed = copy.deepcopy(payload)
        changed["posts"][0]["analysis"]["scam_score"] = 99
        self.db.save_payload(changed, analyzer_version="test-v2")
        counts = self.db.stats()
        self.assertEqual((counts["runs"], counts["messages"], counts["analyses"]), (2, 1, 2))
        self.assertEqual(self.db.payload(first)["posts"][0]["analysis"]["scam_score"], 42)
        self.assertEqual(self.db.messages()[0]["analysis"]["scam_score"], 99)
        self.assertEqual(self.db.conn.execute("SELECT analyzer_version FROM analyses ORDER BY run_id DESC").fetchone()[0], "test-v2")

    def test_message_ids_scoped_by_chat(self):
        self.db.save_payload({"posts": [post("channel_one"), post("channel_two")]})
        self.assertEqual(self.db.stats()["messages"], 2)

    def test_demo_isolated_from_live(self):
        self.db.save_payload({"posts": [post()], "backend": "demo (offline)"})
        self.db.save_payload({"posts": [post()], "backend": "telethon"})
        self.assertEqual(self.db.stats()["entities"], 2)
        self.assertEqual(self.db.stats()["messages"], 2)

    def test_mixed_demo_and_real_keep_separate_identities(self):
        rid = self.db.save_payload({"posts": [post(source="demo"), post(source="telegram-api")]})
        self.assertEqual(self.db.stats()["messages"], 2)
        self.assertEqual(self.db.stats()["entities"], 2)
        self.assertEqual(self.db.runs()[0]["namespace"], "mixed")
        self.assertEqual(len(self.db.payload(rid)["posts"]), 2)

    def test_username_rename_uses_stable_id(self):
        self.db.save_payload({"profiles": [{"username": "old_name", "account_id": 123, "kind": "user"}]})
        self.db.save_payload({"profiles": [{"username": "new_name", "account_id": 123, "kind": "user"}]})
        self.assertEqual(self.db.stats()["entities"], 1)
        self.assertEqual(len(self.db.profiles("@OLD_NAME")), 2)
        self.assertEqual(len(self.db.profiles("new_name")), 2)

    def test_same_numeric_id_different_peer_types(self):
        profiles = [{"username": "a_user", "account_id": 42, "kind": "user", "peer_type": "user"},
                    {"username": "a_chat", "account_id": 42, "kind": "group", "peer_type": "chat"},
                    {"username": "a_channel", "account_id": 42, "kind": "group", "peer_type": "channel"}]
        self.db.save_payload({"profiles": profiles})
        self.assertEqual(self.db.stats()["entities"], 3)

    def test_promote_web_identity_on_api_lookup(self):
        self.db.save_payload({"posts": [post("some_name")]})
        self.db.save_payload({"profiles": [{"username": "some_name", "account_id": 123, "kind": "channel"}],
                              "posts": [post("some_name", chat_id=123, peer_type="channel")]})
        self.assertEqual(self.db.stats()["entities"], 1)
        self.assertEqual(self.db.stats()["messages"], 1)

    def test_merge_existing_id_with_provisional_history(self):
        self.db.save_payload({"posts": [post("old_name", chat_id=123, peer_type="channel")]})
        self.db.save_payload({"posts": [post("new_name")]})
        self.db.save_payload({"profiles": [{"username": "new_name", "account_id": 123, "kind": "channel"}]})
        self.assertEqual(self.db.stats()["entities"], 1)
        self.assertEqual(self.db.stats()["messages"], 1)
        self.assertEqual(self.db.stats()["analyses"], 2)
        self.assertEqual(self.db.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_secondary_alias_merges_earlier_web_history(self):
        self.db.save_payload({"posts": [post("secondary", mid="1")]})
        self.db.save_payload({"profiles": [{"username": "primary", "usernames": ["primary", "secondary"],
                                           "account_id": 42, "kind": "channel"}]})
        self.db.save_payload({"posts": [post("primary", mid="2", chat_id=42, peer_type="channel")]})
        self.assertEqual(self.db.stats()["entities"], 1)
        self.assertEqual(len(self.db.messages(username="secondary")), 2)
        self.assertEqual(len(self.db.messages(username="primary")), 2)

    def test_merge_keeps_newest_observation_of_edited_message(self):
        self.db.save_payload({"posts": [post("old_name", chat_id=42, peer_type="channel", text="old text")]})
        self.db.save_payload({"posts": [post("new_name", text="new edited text")]})
        self.db.save_payload({"profiles": [{"username": "new_name", "account_id": 42, "kind": "channel"}]})
        self.assertEqual(self.db.messages()[0]["text"], "new edited text")
        self.assertEqual(len(self.db.messages("edited")), 1)
        self.assertEqual(self.db.stats()["analyses"], 2)
        self.assertEqual(self.db.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_reassigned_username_does_not_merge_known_accounts(self):
        self.db.save_payload({"profiles": [{"username": "reused", "account_id": 1, "kind": "user"}]})
        self.db.save_payload({"profiles": [{"username": "reused", "account_id": 2, "kind": "user"}]})
        self.assertEqual(self.db.stats()["entities"], 2)
        self.assertEqual(self.db.profiles("reused")[0]["account_id"], 2)

    def test_profiles_common_groups_searches_invites(self):
        payload = {"profiles": [{"username": "person", "account_id": 1, "kind": "user", "phone": "test-only",
                                 "common_groups": [{"id": 7, "username": "group_chat", "kind": "group", "title": "Nhóm"}]}],
                   "keywords": ["test"], "usernames": [{"username": "other_user", "keywords": ["test"], "name": "Tên"}],
                   "invite_links": [{"link": "https://t.me/+example"}],
                   "groups": [{"channel": "group_chat", "group_score": 20, "group_level": "Normal"}], "posts": []}
        rid = self.db.save_payload(payload)
        stats = self.db.stats()
        for key in ("profile_snapshots", "common_groups", "group_summaries", "search_results", "invite_links"):
            self.assertEqual(stats[key], 1)
        self.assertEqual(self.db.payload(rid), payload)
        self.assertEqual(self.db.profiles("person")[0]["phone"], "test-only")

    def test_common_groups_keep_typed_peer_ids(self):
        import demo_telegram_api as api
        basic = type("Chat", (), {"id": 50, "title": "Basic group"})()
        supergroup = type("Channel", (), {"id": 50, "title": "Supergroup", "broadcast": False})()
        profile = {"username": "person", "account_id": 12, "kind": "user",
                   "common_groups": [api._chat_row(basic), api._chat_row(supergroup)]}
        self.db.save_payload({"profiles": [profile]})
        self.assertEqual(self.db.stats()["common_groups"], 2)
        self.assertEqual(self.db.stats()["entities"], 3)

    def test_import_legacy_nested_history(self):
        payload = {"found": [{"invite": "https://t.me/+example", "title": "Nhóm", "chat_id": 123,
                              "peer_type": "chat", "history": [{"message_id": "9", "text": "Tin cũ"}]}]}
        rid = self.db.save_payload(payload)
        self.assertEqual(self.db.stats()["messages"], 1)
        self.assertEqual(self.db.stats()["analyses"], 0)
        self.assertEqual(self.db.messages("tin cu")[0]["message_id"], "9")
        self.assertEqual(self.db.payload(rid), payload)

    def test_list_payload_roundtrip_preserves_root_type(self):
        payload = [post()]
        rid = self.db.save_payload(payload)
        self.assertEqual(self.db.payload(rid), payload)
        self.assertEqual(self.db.stats()["messages"], 1)

    def test_search_literal_values_and_accent_folding(self):
        self.db.save_payload({"posts": [post()]})
        self.assertEqual(len(self.db.messages("dau tu", "@TEST_CHANNEL")), 1)
        self.assertEqual(len(self.db.messages("100%_")), 1)
        self.assertEqual(self.db.messages("' OR 1=1 --"), [])
        self.assertEqual(self.db.messages(username="' OR 1=1 --"), [])

    def test_transaction_rolls_back_partial_run(self):
        with self.assertRaises(ValueError):
            self.db.save_payload({"posts": [post(), {"text": "missing identifiers"}]})
        self.assertEqual(self.db.stats()["runs"], 0)
        self.assertEqual(self.db.stats()["messages"], 0)
        self.assertEqual(self.db.stats()["entities"], 0)

    def test_import_idempotent_and_preserves_historical_scores(self):
        fixture = ROOT / "json_folder" / "posts_tg.json"
        first = self.db.import_json(fixture)
        self.assertEqual(first, self.db.import_json(fixture))
        self.assertEqual(self.db.stats()["runs"], 1)
        self.assertEqual(self.db.stats()["messages"], 17)
        self.assertEqual(self.db.conn.execute("SELECT DISTINCT analyzer_version FROM analyses").fetchone()[0], "unversioned")
        self.assertEqual(self.db.payload(first), json.loads(fixture.read_text(encoding="utf-8")))

    def test_reject_credentials_and_unknown_json(self):
        for payload in ({"api_id": 1, "api_hash": "secret"}, {"unrelated": 1}, {"posts": "invalid"}):
            with self.assertRaises(ValueError):
                self.db.save_payload(payload)
        with self.assertRaises(ValueError):
            self.db.import_json(ROOT / "json_folder" / "tg_api.json")
        self.assertEqual(self.db.stats()["runs"], 0)

    def test_does_not_modify_foreign_sqlite_database(self):
        foreign = Path(self.tmp.name) / "session.sqlite3"
        with closing(sqlite3.connect(foreign)) as conn:
            conn.execute("CREATE TABLE sessions(id INTEGER)")
        with self.assertRaises(ValueError):
            Database(foreign)
        with closing(sqlite3.connect(foreign)) as conn:
            self.assertEqual(conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [("sessions",)])

    def test_future_schema_rejected(self):
        self.db.conn.execute("PRAGMA user_version=999")
        with self.assertRaises(ValueError):
            Database(self.path)

    def test_backup_live_wal_consistent(self):
        self.db.save_payload({"posts": [post()]})
        target = Path(self.tmp.name) / "backup.sqlite3"
        self.db.backup(target)
        with Database(target) as backup:
            self.assertEqual(backup.stats(), self.db.stats())
            self.assertEqual(backup.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        with self.assertRaises(ValueError):
            self.db.backup(target)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_configured_persistence_and_disable(self):
        import argparse
        previous = storage._active_path, storage._active_tool
        try:
            storage.configure_storage(argparse.Namespace(db=str(self.path), no_db=False), "test")
            storage.persist_payload({"posts": [post()]})
            storage.configure_storage(argparse.Namespace(db=str(self.path), no_db=True), "test")
            self.assertIsNone(storage.persist_payload({"posts": [post()]}))
            self.assertEqual(self.db.stats()["runs"], 1)
        finally:
            storage._active_path, storage._active_tool = previous


class TestSQLiteCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbpath = Path(self.tmp.name) / "app.sqlite3"
        self.env = {**os.environ, "TELEGRAM_DB_PATH": str(self.dbpath)}

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, script, *args):
        return subprocess.run([sys.executable, str(ROOT / script), *map(str, args)],
                              cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=30)

    def test_init_import_query_export_and_backup(self):
        for args in (("init",), ("import-json", "posts_tg.json"), ("import-json", "posts_tg.json")):
            result = self.cli("database.py", *args)
            self.assertEqual(result.returncode, 0, result.stderr)
        stats = self.cli("database.py", "stats")
        self.assertEqual(json.loads(stats.stdout)["runs"], 1)
        rows = self.cli("database.py", "messages", "--keyword", "loi nhuan")
        self.assertTrue(json.loads(rows.stdout))
        exported = Path(self.tmp.name) / "export.json"
        result = self.cli("database.py", "export", "1", "--out", exported)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(exported.read_text(encoding="utf-8"))["total"], 17)
        self.assertNotEqual(self.cli("database.py", "export", "1", "--out", exported).returncode, 0)
        self.assertEqual(self.cli("database.py", "backup", Path(self.tmp.name) / "backup.sqlite3").returncode, 0)

    def test_offline_tools_save_with_and_without_json(self):
        output = Path(self.tmp.name) / "posts.json"
        for script in ("demo_telegram.py", "demo_telegram_api.py"):
            result = self.cli(script, "--demo", "--limit", "17", "--no-report", "--out", output)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.cli("keyword_usernames.py", "--demo", "--keyword", "đầu tư")
        self.assertEqual(result.returncode, 0, result.stderr)
        with Database(self.dbpath) as db:
            self.assertEqual(db.stats()["runs"], 3)
            self.assertEqual(db.stats()["messages"], 17)
            self.assertGreater(db.stats()["search_results"], 0)
            self.assertEqual(db.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        result = self.cli("demo_telegram.py", "--demo", "--no-db", "--no-report", "--out", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        with Database(self.dbpath) as db:
            self.assertEqual(db.stats()["runs"], 3)

    def test_profile_tool_saves_without_out_offline_mock(self):
        import argparse
        import username_info
        profile = {"username": "a_user", "kind": "user", "exists": True, "source": "test"}
        args = argparse.Namespace(usernames=["a_user"], user=None, from_json=None, api=False,
                                  no_photo=True, photo_dir="", limit=0, common_limit=0, out=None)
        previous = storage._active_path, storage._active_tool
        try:
            storage.configure_storage(argparse.Namespace(db=str(self.dbpath), no_db=False), "username_info")
            with patch.object(username_info, "lookup_web", return_value=(profile, [])), patch.object(username_info, "print_profile"):
                self.assertEqual(username_info.run(args), 0)
            with Database(self.dbpath) as db:
                self.assertEqual(db.stats()["profile_snapshots"], 1)
                self.assertEqual(db.profiles("a_user")[0], profile)
        finally:
            storage._active_path, storage._active_tool = previous

    def test_missing_and_bad_inputs_return_nonzero(self):
        self.assertNotEqual(self.cli("database.py", "stats").returncode, 0)
        self.assertFalse(self.dbpath.exists())
        self.assertNotEqual(self.cli("database.py", "import-json", "missing.json").returncode, 0)
        self.assertNotEqual(self.cli("database.py", "import-json", "tg_api.json").returncode, 0)
        self.assertNotEqual(self.cli("database.py", "messages", "--limit", "-1").returncode, 0)
        self.assertNotEqual(self.cli("database.py", "export", "999", "--out", Path(self.tmp.name) / "no.json").returncode, 0)
        self.assertFalse((Path(self.tmp.name) / "no.json").exists())

    def test_api_run_preserves_same_id_messages_from_different_peer_types(self):
        import argparse
        import asyncio
        import contextlib
        import io
        from unittest.mock import AsyncMock
        import demo_telegram_api as api

        output = Path(self.tmp.name) / "typed-peers.json"
        chat_post = post("42", chat_id=42, peer_type="chat", entity_kind="group",
                         text="Basic chat message")
        channel_post = post("42", chat_id=42, peer_type="channel", entity_kind="group",
                            text="Supergroup message")
        args = argparse.Namespace(limit=1, session=None, watch=None, login=False,
                                  login_bot=False, user=None, channel=["basic", "supergroup"],
                                  search=None, keyword=None, media_dir=None, no_report=True,
                                  out=str(output))
        client = argparse.Namespace(
            get_me=AsyncMock(return_value=argparse.Namespace(first_name="Offline", username="test")),
            disconnect=AsyncMock())
        previous = storage._active_path, storage._active_tool
        try:
            storage.configure_storage(argparse.Namespace(db=str(self.dbpath), no_db=False),
                                      "demo_telegram_api")
            with patch.object(api, "_missing", return_value=0), \
                    patch.object(api, "load_credentials", return_value=(1, "offline")), \
                    patch.object(api, "bot_mode", return_value=False), \
                    patch.object(api, "open_client", new=AsyncMock(return_value=(client, None))), \
                    patch.object(api, "fetch_history", new=AsyncMock(
                        side_effect=[[chat_post], [channel_post, dict(channel_post)]])), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(asyncio.run(api.run_async(args)), 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["posts"]), 2)
            self.assertEqual(len(payload["groups"]), 2)
            self.assertEqual({row["peer_type"] for row in payload["groups"]}, {"chat", "channel"})
            self.assertEqual({row["peer_type"] for row in payload["posts"]}, {"chat", "channel"})
            with Database(self.dbpath) as db:
                self.assertEqual(db.stats()["messages"], 2)
                self.assertEqual({row["peer_type"] for row in db.messages()}, {"chat", "channel"})
            client.disconnect.assert_awaited_once()
        finally:
            storage._active_path, storage._active_tool = previous


if __name__ == "__main__":
    unittest.main()
