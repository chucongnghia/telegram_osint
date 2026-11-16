"""Offline collector/storage/Jev integration; no Telegram, auth, or model traffic.

Only JevClient.ask and collector I/O boundaries are faked. Actual CLI parsing,
JSON writing, heuristic analysis, SQLite persistence, run-scoped classification,
watch queue/draining, and invocation-wide request accounting remain in use.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack, closing
import copy
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from telegram_demo import analyzer, storage
from telegram_demo.cli import database, demo_telegram as web, demo_telegram_api as api
from telegram_demo.cli import find_private_groups as groups, keyword_usernames as search
from telegram_demo.cli import username_info as profile
from telegram_demo.jev_client import JevError
from telegram_demo.text_classifier import LABELS, QUESTIONS


def answer():
    return {"model": "offline-resolved-model", "answers": {"classification": {
        "type": "choice", "choice": "other", "confidence": 1.0,
        "probabilities": {key: float(key == "other") for key in LABELS}}}}


def post(mid, text=None):
    return {"channel": "integration_fixture", "channel_title": "Fixture",
            "chat_id": 123, "peer_type": "channel", "message_id": str(mid),
            "text": text if text is not None else f"Offline message number {mid}",
            "posted_at": f"2026-01-01T00:{mid % 60:02d}:00+00:00",
            "source": "telegram-api", "has_media": False,
            "pipeline_status": {"collected": True, "downloaded": False}}


class FakeClient:
    """Small Telethon boundary: the producer runs before disconnect completes."""

    def __init__(self, count=0, producer=None):
        self.count = count
        self.producer = producer
        self.handler = None
        self.disconnects = 0
        self.tasks = []

    def on(self, event):
        def register(handler):
            self.handler = handler
            return handler
        return register

    async def emit(self, mid):
        await self.handler(SimpleNamespace(message=SimpleNamespace(id=mid), chat=object()))

    async def run_until_disconnected(self):
        if self.producer is not None:
            await self.producer(self)
        else:
            for mid in range(1, self.count + 1):
                await self.emit(mid)

    async def disconnect(self):
        self.disconnects += 1

    async def get_me(self):
        return SimpleNamespace(first_name="Offline", username="fixture", id=1)

    async def get_entity(self, target):
        return SimpleNamespace(id=123, title="Fixture", username="integration_fixture")

    async def iter_messages(self, entity, limit):
        for mid in range(1, min(self.count, limit) + 1):
            yield SimpleNamespace(id=mid, action=False)


class IntegrationCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.db_path = self.directory / "collection.sqlite3"
        self.out = self.directory / "collection.json"
        # Preserve all mutable CLI globals even when a test raises or fails.
        previous = (storage._active_path, storage._active_tool, storage._active_jev)
        self.addCleanup(self.restore_storage, previous)
        storage._active_path, storage._active_tool, storage._active_jev = None, "", None
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.stack.enter_context(patch("sys.stdout", self.stdout))
        self.stack.enter_context(patch("sys.stderr", self.stderr))
        self.ask = self.stack.enter_context(patch(
            "telegram_demo.collection_jev.JevClient.ask", return_value=answer()))
        # Fail closed if any code accidentally bypasses one of our I/O fakes.
        self.stack.enter_context(patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")))
        self.stack.enter_context(patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("HTTP forbidden")))
        self.open_client = self.stack.enter_context(patch.object(
            api, "open_client", new_callable=AsyncMock, side_effect=AssertionError("Telegram forbidden")))
        self.stack.enter_context(patch.object(
            api, "open_bot_client", new_callable=AsyncMock, side_effect=AssertionError("Bot auth forbidden")))
        self.scrape = self.stack.enter_context(patch.object(
            web, "scrape_channel", side_effect=AssertionError("Scraping forbidden")))
        self.lookup = self.stack.enter_context(patch.object(
            profile, "lookup_web", side_effect=AssertionError("Profile network forbidden")))
        self.search_api = self.stack.enter_context(patch.object(
            search, "_run_api", side_effect=AssertionError("Search network forbidden")))
        self.group_open = self.stack.enter_context(patch.object(
            groups, "open_client", new_callable=AsyncMock, side_effect=AssertionError("Group auth forbidden")))

    @staticmethod
    def restore_storage(previous):
        storage._active_path, storage._active_tool, storage._active_jev = previous

    def configure(self, *flags, tool="integration"):
        parser = argparse.ArgumentParser()
        storage.add_storage_arguments(parser, collection=True)
        args = parser.parse_args(["--db", str(self.db_path), *flags])
        storage.configure_storage(args, tool)
        return args

    def enable_key(self):
        # This placeholder is used only with the ask fake above, never with HTTP.
        os.environ["TYPESAFE_API_KEY"] = "offline-fixture-key"

    def invoke(self, module, *flags):
        with patch.object(sys, "argv", ["fixture", "--db", str(self.db_path), *flags]):
            return module.main()

    def sql(self, statement, parameters=()):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(statement, parameters)]

    def count(self, table):
        return self.sql(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]

    def payload(self):
        with self.out.open(encoding="utf-8") as fh:
            return json.load(fh)

    def watch_args(self):
        return SimpleNamespace(watch=["@integration_fixture"], jev=True, media_dir=None,
                               ocr=False, cycles=0, session=None, watch_once=False,
                               interval=1, out=str(self.out), no_report=True)

    async def wait_until(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(0.001)
        await asyncio.wait_for(wait(), timeout=3)

    def run_watch(self, client, *, flags=("--jev",), args=None):
        if "--jev-dry-run" not in flags:
            self.enable_key()
        self.configure(*flags, tool="demo_telegram_api")
        self.open_client.side_effect = None
        self.open_client.return_value = (client, None)
        with patch.object(api, "events", SimpleNamespace(NewMessage=lambda **kwargs: kwargs), create=True), \
                patch.object(api, "to_record", side_effect=lambda msg, chat: post(msg.id)):
            return asyncio.run(asyncio.wait_for(
                api.run_watch(args or self.watch_args(), 123, "fake-hash"), timeout=15))


class TestSharedStorageIntegration(IntegrationCase):
    def test_saved_run_is_committed_and_storage_connection_closed_before_hook(self):
        original_database = storage.Database
        opened = []

        class TrackingDatabase(original_database):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                opened.append(self)

        record = post(1)
        record["analysis"] = analyzer.analyze(record)
        expected = analyzer.json_payload([record], "telethon")
        self.configure()

        def process(path, run_id):
            self.assertEqual(path, str(self.db_path))
            self.assertIsNone(opened[0].conn)
            with closing(sqlite3.connect(path, timeout=0.1)) as conn:
                self.assertEqual(json.loads(conn.execute(
                    "SELECT payload_json FROM runs WHERE id=?", (run_id,)).fetchone()[0]), expected)
                # The hook can immediately acquire a writer lock from another handle.
                conn.execute("BEGIN IMMEDIATE")
                conn.rollback()

        hook = Mock(side_effect=process)
        storage._active_jev = SimpleNamespace(process_run=hook)
        with patch.object(storage, "Database", TrackingDatabase):
            rid = storage.persist_payload(expected, source="fixture")
        hook.assert_called_once_with(str(self.db_path), rid)
        self.assertEqual(self.count("analyses"), 1)

    def test_write_json_passes_classify_false_and_extra_to_real_storage(self):
        self.configure()
        hook = Mock()
        storage._active_jev = SimpleNamespace(process_run=hook)
        record = post(1)
        record["analysis"] = analyzer.analyze(record)
        result = analyzer.write_json(str(self.out), [record], "telethon", extra={"marker": "kept"}, classify=False)
        self.assertEqual(result, [record])
        self.assertEqual(self.payload()["marker"], "kept")
        self.assertEqual(self.count("runs"), 1)
        hook.assert_not_called()
        self.ask.assert_not_called()

    def test_hook_error_keeps_json_run_and_original_heuristic(self):
        self.configure()
        storage._active_jev = SimpleNamespace(process_run=Mock(side_effect=RuntimeError("fixture failure")))
        record = post(1, "Tuyển cộng tác viên hoàn tiền 100% làm nhiệm vụ nhận hoa hồng")
        record["analysis"] = analyzer.analyze(record)
        with self.assertRaisesRegex(RuntimeError, "fixture failure"):
            analyzer.write_json(str(self.out), [record], "telethon")
        self.assertEqual(self.payload()["posts"][0]["analysis"], record["analysis"])
        self.assertEqual(self.count("runs"), 1)
        self.assertEqual(json.loads(self.sql("SELECT analysis_json FROM analyses")[0]["analysis_json"]), record["analysis"])

    def test_reconfiguration_clears_previous_hook_including_validation_failure(self):
        self.enable_key()
        self.configure("--jev")
        old = storage._active_jev
        self.assertIsNotNone(old)
        self.configure()
        self.assertIsNone(storage._active_jev)
        storage.persist_payload({"posts": [post(1)]})
        self.ask.assert_not_called()
        self.configure("--jev")
        os.environ.pop("TYPESAFE_API_KEY")
        with self.assertRaisesRegex(RuntimeError, "TYPESAFE_API_KEY"):
            self.configure("--jev")
        self.assertIsNone(storage._active_jev)
        self.assertIsNone(storage._active_path)
        self.assertIsNone(storage.persist_payload({"posts": [post(2)]}))
        self.assertEqual(self.count("runs"), 1)


class TestCollectorCLIIntegration(IntegrationCase):
    collectors = (api, web, groups, search, profile)

    def test_all_five_collectors_have_jev_help_database_utility_does_not(self):
        for module in self.collectors:
            with self.subTest(module=module.__name__):
                self.stdout.seek(0)
                self.stdout.truncate()
                with self.assertRaises(SystemExit) as caught:
                    self.invoke(module, "--help")
                self.assertEqual(caught.exception.code, 0)
                for option in ("--jev", "--jev-limit", "--jev-model", "--jev-timeout",
                               "--jev-include-demo", "--jev-dry-run"):
                    self.assertIn(option, self.stdout.getvalue())
        self.stdout.seek(0)
        self.stdout.truncate()
        with self.assertRaises(SystemExit) as caught:
            database.main(["--db", str(self.db_path), "--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertNotIn("--jev", self.stdout.getvalue())
        self.ask.assert_not_called()
        self.assertFalse(self.db_path.exists())

    def test_invalid_combinations_fail_friendly_before_any_collection(self):
        self.enable_key()
        for module in self.collectors:
            for flags in (("--no-db", "--jev"), ("--jev-limit", "10"),
                          ("--jev-model", "jev-latest"), ("--jev-timeout", "60"),
                          ("--jev-include-demo",), ("--jev-dry-run",)):
                with self.subTest(module=module.__name__, flags=flags):
                    self.assertEqual(self.invoke(module, *flags), 1)
                    self.assertIsNone(storage._active_path)
                    self.assertIsNone(storage._active_jev)
        self.assertNotIn("Traceback", self.stderr.getvalue())
        self.ask.assert_not_called()
        self.open_client.assert_not_called()
        self.group_open.assert_not_called()
        self.scrape.assert_not_called()
        self.lookup.assert_not_called()
        self.search_api.assert_not_called()
        self.assertFalse(self.db_path.exists())

    def test_missing_key_fails_all_five_before_collection(self):
        for module in self.collectors:
            with self.subTest(module=module.__name__):
                self.assertEqual(self.invoke(module, "--jev"), 1)
        self.assertIn("TYPESAFE_API_KEY", self.stderr.getvalue())
        self.ask.assert_not_called()
        self.assertFalse(self.db_path.exists())

    def test_report_login_and_setup_bot_are_not_classification_modes(self):
        self.enable_key()
        for module, flags in ((api, ("--login",)), (api, ("--login-bot",)),
                              (api, ("--setup-bot", "fake-token")),
                              (api, ("--setup-bot",)),
                              (api, ("--report", str(self.out))),
                              (web, ("--report", str(self.out)))):
            with self.subTest(module=module.__name__, flags=flags):
                self.assertEqual(self.invoke(module, "--jev", *flags), 1)
        self.ask.assert_not_called()
        self.open_client.assert_not_called()
        self.assertFalse(self.db_path.exists())
        self.assertFalse(self.out.exists())

    def prepare_web(self, *records):
        self.scrape.side_effect = None
        self.scrape.return_value = (copy.deepcopy(list(records)), {"title": "Fixture"})

    def test_web_main_classifies_only_just_saved_run_not_backlog(self):
        self.enable_key()
        with storage.Database(self.db_path) as db:
            old = db.save_payload({"posts": [post(99, "Unclassified old backlog")]})
        self.prepare_web(post(1, "New web message"))
        self.assertEqual(self.invoke(web, "--channel", "fixture", "--out", str(self.out), "--no-report", "--jev"), 0)
        self.ask.assert_called_once_with({"text": "New web message"}, QUESTIONS)
        self.assertEqual(self.count("runs"), 2)
        self.assertEqual(self.count("messages"), 2)
        self.assertEqual(self.count("analyses"), 1)
        result = self.sql("SELECT input_text,response_json,status,label FROM message_classifications")[0]
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["label"], "other")
        self.assertNotEqual(json.loads(result["response_json"])["collection_run_id"], old)
        self.assertEqual(self.payload()["posts"][0]["text"], "New web message")

    def test_web_api_failure_is_nonzero_but_json_and_heuristics_survive(self):
        self.enable_key()
        self.ask.side_effect = JevError("timeout", "Offline fixture timeout")
        self.prepare_web(post(1))
        self.assertEqual(self.invoke(web, "--channel", "fixture", "--out", str(self.out), "--jev"), 1)
        self.assertEqual(self.count("runs"), 1)
        self.assertEqual(self.count("analyses"), 1)
        self.assertIn("analysis", self.payload()["posts"][0])
        result = self.sql("SELECT status,label,error_code FROM message_classifications")[0]
        self.assertEqual(result, {"status": "error", "label": None, "error_code": "timeout"})
        self.ask.assert_called_once()

    def test_default_disabled_and_no_db_keep_model_and_classification_writes_off(self):
        self.prepare_web(post(1))
        self.assertEqual(self.invoke(web, "--channel", "fixture", "--out", str(self.out), "--no-report"), 0)
        self.assertIsNone(storage._active_jev)
        self.assertEqual(self.count("message_classifications"), 0)
        self.assertEqual(self.count("runs"), 1)
        self.assertEqual(self.invoke(web, "--channel", "fixture", "--out", str(self.out), "--no-db", "--no-report"), 0)
        self.assertEqual(self.count("runs"), 1)
        self.ask.assert_not_called()

    def test_web_dry_run_needs_no_key_and_writes_no_classifications(self):
        self.prepare_web(post(1), post(2))
        self.assertEqual(self.invoke(web, "--channel", "fixture", "--out", str(self.out),
                                     "--no-report", "--jev", "--jev-dry-run"), 0)
        self.assertEqual(self.count("messages"), 2)
        self.assertEqual(self.count("message_classifications"), 0)
        self.assertEqual(storage._active_jev.previewed_total, 2)
        self.ask.assert_not_called()

    def test_api_batch_with_fake_telethon_client_uses_real_history_and_dedup(self):
        self.enable_key()
        client = FakeClient(count=3)
        self.open_client.side_effect = None
        self.open_client.return_value = (client, None)
        with patch.object(api, "_missing", return_value=0), \
                patch.object(api, "load_credentials", return_value=(123, "fake-hash")), \
                patch.object(api, "to_record", side_effect=lambda msg, chat: post(msg.id)):
            code = self.invoke(api, "--channel", "fixture", "--channel", "fixture",
                               "--out", str(self.out), "--no-report", "--jev", "--jev-limit", "2")
        self.assertEqual(code, 0)
        self.assertEqual(client.disconnects, 1)
        self.assertEqual(self.count("runs"), 1)
        self.assertEqual(self.count("messages"), 3)
        self.assertEqual(self.count("analyses"), 3)
        self.assertEqual(self.count("message_classifications"), 2)
        self.assertEqual(self.ask.call_count, 2)
        self.assertEqual(len(self.payload()["posts"]), 3)

    def test_groups_mocked_crawl_classifies_messages_and_preserves_found_histories(self):
        self.enable_key()
        client = FakeClient()
        self.group_open.side_effect = None
        self.group_open.return_value = (client, None)
        history = [{"message_id": "7", "text": "Raw private history", "posted_at": "2026-01-01"}]

        async def discover(client, args, found):
            found.add("https://t.me/+offlinefixture", "fixture", "original context", "2026-01-01")

        async def crawl(client, found, limit, out):
            found.items["https://t.me/+offlinefixture"].update(
                joined=True, title="Private fixture", chat_id=456, peer_type="channel",
                history=copy.deepcopy(history), messages=1)

        with patch.object(groups, "TelegramClient", object()), \
                patch.object(groups, "load_credentials", return_value=(123, "fake-hash")), \
                patch.object(groups, "session_path", return_value=str(self.directory / "fake-session")), \
                patch.object(groups, "discover", side_effect=discover), \
                patch.object(groups, "crawl_found", side_effect=crawl):
            code = self.invoke(groups, "--discover", "--crawl", "--out", str(self.out), "--jev")
        self.assertEqual(code, 0)
        self.assertEqual(client.disconnects, 1)
        self.ask.assert_called_once_with({"text": "Raw private history"}, QUESTIONS)
        self.assertEqual(self.payload()["found"][0]["history"], history)
        run = json.loads(self.sql("SELECT payload_json FROM runs")[0]["payload_json"])
        self.assertEqual(run["found"], self.payload()["found"])
        self.assertEqual(run["posts"][0]["chat_id"], 456)
        self.assertIn("analysis", run["posts"][0])
        self.assertEqual(self.count("analyses"), 1)
        self.assertEqual(self.count("invite_links"), 1)
        self.assertEqual(self.count("message_classifications"), 1)

    def test_search_metadata_only_persists_without_model_calls(self):
        self.enable_key()
        self.search_api.side_effect = None
        self.search_api.return_value = ([], [{"username": "fixture", "name": "Fixture", "kind": "channel"}], 0)
        self.assertEqual(self.invoke(search, "--api", "--keyword", "fixture", "--jev"), 0)
        self.assertEqual(self.count("search_results"), 1)
        self.assertEqual(self.count("runs"), 1)
        self.assertEqual(self.count("messages"), 0)
        self.assertEqual(self.count("message_classifications"), 0)
        self.ask.assert_not_called()

    def test_profile_metadata_only_persists_without_model_calls(self):
        self.enable_key()
        self.lookup.side_effect = None
        self.lookup.return_value = ({"username": "fixture", "kind": "user", "title": "Fixture", "exists": True}, [])
        with patch.object(profile, "print_profile"):
            self.assertEqual(self.invoke(profile, "fixture", "--no-photo", "--out", str(self.out), "--jev"), 0)
        self.assertEqual(self.count("profile_snapshots"), 1)
        self.assertEqual(self.count("messages"), 0)
        self.assertEqual(self.count("message_classifications"), 0)
        self.assertEqual(self.payload()["profiles"][0]["username"], "fixture")
        self.ask.assert_not_called()


class TestAsyncWatchIntegration(IntegrationCase):
    def assert_complete_watch(self, count):
        self.assertEqual(len(self.payload()["posts"]), count)
        self.assertEqual({p["message_id"] for p in self.payload()["posts"]},
                         {str(mid) for mid in range(1, count + 1)})
        self.assertEqual(self.count("messages"), count)
        runs = self.sql("SELECT id,source,payload_json FROM runs ORDER BY id")
        self.assertEqual(len(runs), count + 1)
        for run in runs[:-1]:
            self.assertEqual(run["source"], "watch-event")
            payload = json.loads(run["payload_json"])
            self.assertTrue(payload["watch_incremental"])
            self.assertEqual(len(payload["posts"]), 1)
        self.assertEqual(json.loads(runs[-1]["payload_json"]), self.payload())
        self.assertEqual(len(self.sql("SELECT * FROM run_messages WHERE run_id=?", (runs[-1]["id"],))), count)
        return runs

    def test_watch_drains_worker_and_does_not_reclassify_final_snapshot(self):
        client = FakeClient(count=5)
        self.assertEqual(self.run_watch(client), 0)
        self.assert_complete_watch(5)
        self.assertEqual(self.ask.call_count, 5)
        self.assertEqual(self.count("message_classifications"), 5)
        self.assertEqual(self.count("analyses"), 10)  # event runs plus complete snapshot
        self.assertEqual(client.disconnects, 1)
        self.assertEqual(storage._active_jev.attempts_total, 5)

    def test_watch_bounded_queue_drains_already_accepted_callbacks(self):
        async def concurrent_producer(client):
            client.tasks = [asyncio.create_task(client.emit(mid)) for mid in range(1, 81)]
            # Start every callback; some block on the bounded 64-event queue.
            await asyncio.sleep(0)
            self.assertTrue(any(not task.done() for task in client.tasks))

        client = FakeClient(producer=concurrent_producer)
        with patch.object(api.asyncio, "Queue", wraps=asyncio.Queue) as queue_factory:
            self.assertEqual(self.run_watch(client, flags=("--jev", "--jev-limit", "3")), 0)
        queue_factory.assert_called_once_with(maxsize=64)
        self.assertTrue(all(task.done() for task in client.tasks))
        self.assert_complete_watch(80)
        self.assertEqual(self.ask.call_count, 3)
        self.assertEqual(self.count("message_classifications"), 3)
        self.assertEqual(storage._active_jev.attempts_total, 3)
        self.assertEqual(storage._active_jev.remaining, 0)

    def test_watch_default_request_budget_is_shared_across_events(self):
        self.assertEqual(self.run_watch(FakeClient(count=14)), 0)
        self.assert_complete_watch(14)
        self.assertEqual(self.ask.call_count, 10)
        self.assertEqual(self.count("message_classifications"), 10)
        self.assertEqual(storage._active_jev.attempts_total, 10)

    def test_watch_event_loop_heartbeat_and_incremental_commit_during_blocked_ask(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        heartbeat = []
        main_thread = threading.get_ident()

        def blocking_ask(state, questions):
            self.assertNotEqual(threading.get_ident(), main_thread)
            started.set()
            if not release.wait(timeout=4):
                raise AssertionError("Event loop failed to release blocked ask")
            return answer()

        async def producer(client):
            await client.emit(1)
            await self.wait_until(started.is_set)
            try:
                # The run and original heuristic are durable while inference waits;
                # neither the event loop nor a SQLite writer lock is held by HTTP.
                self.assertEqual(self.count("runs"), 1)
                self.assertEqual(self.count("messages"), 1)
                self.assertEqual(self.count("analyses"), 1)
                self.assertEqual(self.count("message_classifications"), 0)
                self.assertFalse(self.out.exists())
                with closing(sqlite3.connect(self.db_path, timeout=0.1)) as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.rollback()
                for tick in range(5):
                    await asyncio.sleep(0.001)
                    heartbeat.append(tick)
                await client.emit(2)
                self.assertFalse(release.is_set())
            finally:
                release.set()

        self.ask.side_effect = blocking_ask
        self.assertEqual(self.run_watch(FakeClient(producer=producer)), 0)
        self.assertEqual(heartbeat, list(range(5)))
        self.assert_complete_watch(2)
        self.assertEqual(self.ask.call_count, 2)

    def test_first_api_error_latches_jev_but_future_events_and_snapshot_survive(self):
        self.ask.side_effect = JevError("rate_limit", "Offline rate limit")

        async def producer(client):
            await client.emit(1)
            await self.wait_until(lambda: storage._active_jev.failed)
            # These are produced after failure, not merely queued before it.
            for mid in range(2, 7):
                await client.emit(mid)

        self.assertEqual(self.run_watch(FakeClient(producer=producer)), 1)
        self.assert_complete_watch(6)
        self.ask.assert_called_once()
        self.assertEqual(storage._active_jev.attempts_total, 1)
        self.assertEqual(self.count("analyses"), 12)
        results = self.sql("SELECT status,label,error_code,input_text FROM message_classifications")
        self.assertEqual(results, [{"status": "error", "label": None,
                                    "error_code": "rate_limit", "input_text": post(1)["text"]}])
        self.assertIn("Jev", self.stderr.getvalue())
        self.assertTrue(all("analysis" in rec for rec in self.payload()["posts"]))

    def test_watch_dry_run_keeps_raw_events_but_no_api_or_classification_writes(self):
        self.assertEqual(self.run_watch(FakeClient(count=6), flags=(
            "--jev", "--jev-dry-run", "--jev-limit", "2")), 0)
        self.assert_complete_watch(6)
        self.assertEqual(storage._active_jev.previewed_total, 2)
        self.assertEqual(storage._active_jev.attempts_total, 0)
        self.assertEqual(self.count("message_classifications"), 0)
        self.ask.assert_not_called()

    def test_disabled_watch_has_only_final_sqlite_run_no_incremental_writes(self):
        async def producer(client):
            for mid in range(1, 4):
                await client.emit(mid)
                self.assertFalse(self.db_path.exists())

        args = self.watch_args()
        args.jev = False
        self.assertEqual(self.run_watch(FakeClient(producer=producer), flags=(), args=args), 0)
        self.assertEqual(len(self.payload()["posts"]), 3)
        self.assertEqual(self.count("runs"), 1)
        self.assertEqual(self.count("analyses"), 3)
        self.assertEqual(self.count("message_classifications"), 0)
        self.ask.assert_not_called()

    def test_worker_payload_error_does_not_strand_queue_and_final_raw_snapshot_survives(self):
        # Only the worker calls api.json_payload; final write_json uses the
        # analyzer's original builder, so final JSON/SQLite remains usable.
        with patch.object(api, "json_payload", side_effect=ValueError("Offline payload failure")) as build:
            self.assertEqual(self.run_watch(FakeClient(count=4)), 1)
        self.assertEqual(build.call_count, 4)
        self.assertEqual(self.count("runs"), 1)
        self.assertEqual(self.count("messages"), 4)
        self.assertEqual(self.count("analyses"), 4)
        self.assertEqual(self.count("message_classifications"), 0)
        self.assertEqual({rec["message_id"] for rec in self.payload()["posts"]},
                         {"1", "2", "3", "4"})
        self.assertEqual(json.loads(self.sql("SELECT payload_json FROM runs")[0]["payload_json"]), self.payload())
        self.assertIn("Jev", self.stderr.getvalue())
        self.ask.assert_not_called()

    def test_worker_cancelled_with_full_queue_saves_all_collected_and_returns_error_not_130(self):
        async def producer(client):
            client.tasks = [asyncio.create_task(client.emit(mid)) for mid in range(1, 81)]
            await asyncio.sleep(0)
            self.assertTrue(any(not task.done() for task in client.tasks))
            # Let the worker actually die, then submit a future event. A dead
            # worker must not make new callbacks wait on its already full queue.
            await self.wait_until(lambda: build.call_count > 0)
            await client.emit(81)

        client = FakeClient(producer=producer)
        with patch.object(api, "json_payload", side_effect=asyncio.CancelledError()) as build:
            # run_watch helper has a bounded asyncio.wait_for; this cancellation
            # belongs to the worker, unlike root cancellation tests below.
            self.assertEqual(self.run_watch(client), 1)
        build.assert_called_once()
        self.assertTrue(all(task.done() for task in client.tasks))
        self.assertTrue(any(task.cancelled() for task in client.tasks))
        self.assertEqual(self.count("runs"), 1)
        self.assertEqual(self.count("messages"), 81)
        self.assertEqual(self.count("analyses"), 81)
        self.assertEqual(self.count("message_classifications"), 0)
        self.assertEqual({rec["message_id"] for rec in self.payload()["posts"]},
                         {str(mid) for mid in range(1, 82)})
        self.assertEqual(json.loads(self.sql("SELECT payload_json FROM runs")[0]["payload_json"]), self.payload())
        self.assertIn("Worker", self.stderr.getvalue())
        self.ask.assert_not_called()

    def test_watch_cancelled_after_events_drains_and_preserves_snapshot(self):
        async def producer(client):
            await client.emit(1)
            await client.emit(2)
            raise asyncio.CancelledError()

        self.assertEqual(self.run_watch(FakeClient(producer=producer)), 130)
        self.assert_complete_watch(2)
        self.assertEqual(self.ask.call_count, 2)
        self.assertEqual(self.count("message_classifications"), 2)

    def test_watch_cancelled_without_events_returns_interrupted_not_success(self):
        async def producer(client):
            raise asyncio.CancelledError()

        self.assertEqual(self.run_watch(FakeClient(producer=producer)), 130)
        self.assertFalse(self.db_path.exists())
        self.assertFalse(self.out.exists())
        self.ask.assert_not_called()

    def test_watch_once_timeout_still_drains_and_writes_snapshot(self):
        async def producer(client):
            await client.emit(1)
            await client.emit(2)
            await asyncio.Event().wait()

        args = self.watch_args()
        args.watch_once = True
        args.interval = 0.03
        self.assertEqual(self.run_watch(FakeClient(producer=producer), args=args), 0)
        self.assert_complete_watch(2)
        self.assertEqual(self.ask.call_count, 2)


if __name__ == "__main__":
    unittest.main()
