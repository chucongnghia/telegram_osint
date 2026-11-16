"""Offline tests of opt-in collection Jev, using only temporary databases."""
from __future__ import annotations

import argparse
from contextlib import closing
import copy
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from telegram_demo.collection_jev import CollectionJev, add_collection_jev_arguments
from telegram_demo.jev_client import JevError
from telegram_demo.storage import Database
from telegram_demo.text_classifier import ClassifierStore, LABELS, MAX_TEXT_CHARS, QUESTIONS, RUBRIC_VERSION


def response():
    return {"model": "resolved-model", "answers": {"classification": {
        "type": "choice", "choice": "other", "confidence": 0.2,
        "probabilities": {key: (1 if key == "other" else 0) for key in LABELS}}},
        "usage": {"input_tokens": 12, "output_tokens": 3},
        "extra": {"raw": "unaltered provider evidence"}}


def post(mid, text="A normal message", **kwargs):
    return {"channel": "example", "chat_id": "123", "message_id": mid,
            "text": text, "source": "telegram-api", "posted_at": f"2026-01-01T00:{mid % 60:02d}:00Z",
            **kwargs}


class TestCollectionJevOptions(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.ask = patch("telegram_demo.collection_jev.JevClient.ask")
        self.ask_mock = self.ask.start()
        self.addCleanup(self.ask.stop)

    def parser(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--no-db", action="store_true")
        add_collection_jev_arguments(parser)
        return parser

    def test_defaults_and_old_namespace_are_disabled(self):
        args = self.parser().parse_args([])
        self.assertFalse(args.jev)
        self.assertEqual(args.jev_limit, 10)
        self.assertEqual(args.jev_model, "jev-latest")
        self.assertEqual(args.jev_timeout, 60)
        self.assertFalse(args.jev_include_demo)
        self.assertFalse(args.jev_dry_run)
        self.assertIsNone(CollectionJev.from_args(args))
        self.assertIsNone(CollectionJev.from_args(argparse.Namespace()))
        self.ask_mock.assert_not_called()

    def test_missing_key_rejected_before_any_request(self):
        with self.assertRaisesRegex(RuntimeError, "TYPESAFE_API_KEY"):
            CollectionJev.from_args(self.parser().parse_args(["--jev"]))
        self.ask_mock.assert_not_called()

    def test_opt_in_construction_has_no_network_and_keeps_defaults(self):
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "secret-key"}):
            item = CollectionJev.from_args(self.parser().parse_args(["--jev"]))
        self.assertEqual(item.limit, 10)
        self.assertEqual(item.client.model, "jev-latest")
        self.assertEqual(item.client.timeout, 60)
        self.assertFalse(item.failed)
        self.assertIsNone(item.error_code)
        self.assertEqual(item.remaining, 10)
        self.ask_mock.assert_not_called()

    def test_explicit_modifiers_even_equal_to_defaults_need_opt_in(self):
        for flags in (["--jev-limit", "10"], ["--jev-model", "jev-latest"],
                      ["--jev-timeout", "60"], ["--jev-include-demo"], ["--jev-dry-run"]):
            with self.subTest(flags=flags), self.assertRaisesRegex(ValueError, "require --jev"):
                CollectionJev.from_args(self.parser().parse_args(flags))
        self.ask_mock.assert_not_called()

    def test_manual_namespace_modifiers_need_opt_in(self):
        for kwargs in ({"jev_limit": 11}, {"jev_model": "another"}, {"jev_timeout": 2},
                       {"jev_include_demo": True}, {"jev_dry_run": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                CollectionJev.from_args(argparse.Namespace(**kwargs))

    def test_no_db_rejected_including_dry_run(self):
        for flags in (["--jev", "--no-db"], ["--jev", "--no-db", "--jev-dry-run"]):
            with self.subTest(flags=flags), self.assertRaisesRegex(ValueError, "--no-db"):
                CollectionJev.from_args(self.parser().parse_args(flags))

    def test_model_environment_and_explicit_override(self):
        with patch.dict(os.environ, {"JEV_MODEL": "environment-model"}):
            parser = self.parser()
            self.assertEqual(parser.parse_args([]).jev_model, "environment-model")
            self.assertEqual(CollectionJev.from_args(parser.parse_args(
                ["--jev", "--jev-dry-run"])).client.model, "environment-model")
            self.assertEqual(CollectionJev.from_args(parser.parse_args(
                ["--jev", "--jev-dry-run", "--jev-model", "explicit"])).client.model, "explicit")
        self.ask_mock.assert_not_called()

    def test_limit_timeout_and_model_validation(self):
        cases = (["--jev-limit", "0"], ["--jev-limit", "1001"],
                 ["--jev-timeout", "0"], ["--jev-timeout", "-1"], ["--jev-timeout", "300.01"],
                 ["--jev-timeout", "inf"], ["--jev-timeout", "nan"], ["--jev-model", ""],
                 ["--jev-model", "   "])
        for flags in cases:
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                CollectionJev.from_args(self.parser().parse_args(["--jev", "--jev-dry-run", *flags]))
        valid = CollectionJev.from_args(self.parser().parse_args(
            ["--jev", "--jev-dry-run", "--jev-limit", "1000", "--jev-timeout", "300"]))
        self.assertEqual(valid.limit, 1000)
        self.assertEqual(valid.client.timeout, 300)
        self.ask_mock.assert_not_called()

    def test_noninteger_limit_and_nonnumeric_timeout_parser_errors(self):
        with patch("sys.stderr", new_callable=io.StringIO):
            for flags in (["--jev-limit", "2.5"], ["--jev-timeout", "oops"]):
                with self.subTest(flags=flags), self.assertRaises(SystemExit):
                    self.parser().parse_args(["--jev", *flags])

    def test_direct_bool_and_invalid_types_rejected(self):
        for kwargs in ({"limit": True}, {"limit": "10"}, {"timeout": True},
                       {"timeout": "60"}, {"timeout": 10**1000},
                       {"model": False}, {"include_demo": 1}, {"dry_run": "yes"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                CollectionJev(**{**{"dry_run": True}, **kwargs})

    def test_existing_client_endpoint_validation_is_preserved(self):
        for endpoint in ("http://remote.example/api", "https://user:key@example.test/", ""):
            with self.subTest(endpoint=endpoint), patch.dict(os.environ, {"JEV_API_URL": endpoint}), \
                    self.assertRaisesRegex(RuntimeError, "configuration") as caught:
                CollectionJev(dry_run=True)
            self.assertNotIn("user:key", str(caught.exception))
        self.ask_mock.assert_not_called()

    def test_credential_validation_is_eager_and_sanitized(self):
        for key in ("", "  ", "secret-key\r\ninject", "not ascii é", "with space"):
            with self.subTest(key=key), patch.dict(os.environ, {"TYPESAFE_API_KEY": key}), \
                    self.assertRaises(RuntimeError) as caught:
                CollectionJev()
            self.assertNotIn("secret-key", str(caught.exception))
            self.assertNotIn("inject", str(caught.exception))
        self.ask_mock.assert_not_called()

    def test_dry_run_needs_no_key_but_checks_endpoint_and_model(self):
        self.assertTrue(CollectionJev.from_args(self.parser().parse_args(
            ["--jev", "--jev-dry-run"])).dry_run)
        with patch.dict(os.environ, {"JEV_MODEL": ""}), self.assertRaises(ValueError):
            CollectionJev(dry_run=True)
        self.ask_mock.assert_not_called()


class TestCollectionJevRuns(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.env = patch.dict(os.environ, {"TYPESAFE_API_KEY": "secret-key"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.ask = patch("telegram_demo.collection_jev.JevClient.ask", return_value=response())
        self.ask_mock = self.ask.start()
        self.addCleanup(self.ask.stop)
        self.output = io.StringIO()
        self.stdout = patch("sys.stdout", self.output)
        self.stdout.start()
        self.addCleanup(self.stdout.stop)

    def save(self, *posts):
        with Database(self.path) as db:
            return db.save_payload({"posts": list(posts)}, tool="crawl", source="fixture")

    def results(self):
        with Database(self.path) as db:
            return ClassifierStore(db).results(limit=2000)

    def test_only_requested_run_no_backlog_and_immutable_evidence(self):
        old = self.save(post(1, "backlog"))
        run = self.save(post(2, "fresh", analysis={"scam_score": 99, "level": "high"}))
        with Database(self.path) as db:
            original_runs = [tuple(row) for row in db.conn.execute("SELECT * FROM runs")]
            original_analyses = [tuple(row) for row in db.conn.execute("SELECT * FROM analyses")]
            original_messages = [tuple(row) for row in db.conn.execute("SELECT * FROM messages")]
        item = CollectionJev()
        summary = item.process_run(self.path, run)
        self.assertNotEqual(old, run)
        self.assertEqual(summary["success"], 1)
        self.ask_mock.assert_called_once_with({"text": "fresh"}, QUESTIONS)
        saved = self.results()[0]
        self.assertEqual(saved["input_text"], "fresh")
        self.assertEqual(saved["source"]["record"]["analysis"]["scam_score"], 99)
        self.assertEqual(saved["model"], "resolved-model")
        self.assertEqual(saved["rubric"], QUESTIONS)
        self.assertEqual(saved["rubric_version"], RUBRIC_VERSION)
        self.assertEqual(saved["response"], {"requested_model": "jev-latest", "response": response(),
                                             "collection_run_id": run})
        with Database(self.path) as db:
            self.assertEqual(original_runs, [tuple(row) for row in db.conn.execute("SELECT * FROM runs")])
            self.assertEqual(original_analyses, [tuple(row) for row in db.conn.execute("SELECT * FROM analyses")])
            self.assertEqual(original_messages, [tuple(row) for row in db.conn.execute("SELECT * FROM messages")])

    def test_success_cache_before_limit_with_same_and_next_runs(self):
        item = CollectionJev(limit=3)
        first = self.save(post(1, "already classified"))
        item.process_run(self.path, first)
        again = item.process_run(self.path, first)
        self.assertEqual(again["attempted"], 0)
        run = self.save(post(1, "already classified", posted_at="2099"), post(2, "uncached", posted_at="2000"))
        summary = CollectionJev(limit=1).process_run(self.path, run)
        self.assertEqual(summary["success"], 1)
        self.assertEqual(self.ask_mock.call_count, 2)
        self.assertEqual(self.ask_mock.call_args.args[0], {"text": "uncached"})
        self.assertEqual(len(self.results()), 2)

    def test_model_backend_cache_identity_and_alias_limitation_unchanged(self):
        run = self.save(post(1))
        CollectionJev(model="alias").process_run(self.path, run)
        self.ask_mock.return_value = {**response(), "model": "new-alias-resolution"}
        self.assertEqual(CollectionJev(model="alias").process_run(self.path, run)["success"], 0)
        self.assertEqual(CollectionJev(model="different").process_run(self.path, run)["success"], 1)
        self.assertEqual(self.ask_mock.call_count, 2)

    def test_shared_watch_budget_does_not_reset_per_run(self):
        item = CollectionJev(limit=2)
        first = item.process_run(self.path, self.save(post(1)))
        second = item.process_run(self.path, self.save(post(2), post(3)))
        third = item.process_run(self.path, self.save(post(4)))
        self.assertEqual((first["attempted"], second["attempted"], third["attempted"]), (1, 1, 0))
        self.assertEqual(item.attempts_total, 2)
        self.assertEqual(item.remaining, 0)
        self.assertEqual(self.ask_mock.call_count, 2)
        self.assertEqual(len(self.results()), 2)
        self.assertIn("request cap, not dollars", self.output.getvalue())

    def test_blanks_and_demo_excluded_by_default(self):
        run = self.save(post(1, ""), post(2, " \t\n\u2003"), post(3, "demo text", source="demo"),
                        post(4, "real text"))
        self.assertEqual(CollectionJev(limit=1).process_run(self.path, run)["success"], 1)
        self.assertEqual(self.ask_mock.call_args.args[0], {"text": "real text"})
        self.assertEqual(len(self.results()), 1)
        self.assertEqual(CollectionJev(include_demo=True).process_run(self.path, run)["success"], 1)
        self.assertEqual(self.results()[0]["source"]["namespace"], "demo")

    def test_oversize_terminal_skip_no_http_or_fallback_and_no_starvation(self):
        run = self.save(post(1, "x" * (MAX_TEXT_CHARS + 1), posted_at="2099"),
                        post(2, "small", posted_at="2000"))
        item = CollectionJev(limit=1)
        summary = item.process_run(self.path, run)
        self.assertEqual(summary["attempted"], 1)
        self.assertEqual(summary["success"], 1)
        self.assertEqual(summary["skipped"], 1)
        self.ask_mock.assert_called_once_with({"text": "small"}, QUESTIONS)
        skipped = [row for row in self.results() if row["status"] == "skipped"][0]
        self.assertIsNone(skipped["label"])
        self.assertEqual(skipped["error_code"], "text_too_long")
        self.assertEqual(len(skipped["input_text"]), MAX_TEXT_CHARS + 1)
        next_summary = CollectionJev().process_run(self.path, run)
        self.assertEqual(next_summary["candidates"], 0)
        self.assertEqual(len(self.results()), 2)
        self.assertEqual(self.ask_mock.call_count, 1)

    def test_exact_text_length_limit_allowed(self):
        run = self.save(post(1, "x" * MAX_TEXT_CHARS))
        self.assertEqual(CollectionJev().process_run(self.path, run)["success"], 1)
        self.ask_mock.assert_called_once()

    def test_scan_bounded_and_oversize_cannot_crowd_out_short_candidate(self):
        run = self.save(*[post(mid, "x" * (MAX_TEXT_CHARS + 1), posted_at="2099")
                          for mid in range(1, 1002)], post(1002, "small", posted_at="2000"))
        summary = CollectionJev(limit=1).process_run(self.path, run)
        self.assertEqual(summary["candidates"], 1000)
        self.assertEqual(summary["success"], 1)
        self.assertEqual(summary["skipped"], 999)
        self.ask_mock.assert_called_once_with({"text": "small"}, QUESTIONS)

    def test_no_sqlite_transaction_or_writer_lock_during_inference(self):
        run = self.save(post(1))
        seen = []
        original = ClassifierStore.validate_item

        def validate(store, item):
            seen.append(store.db)
            return original(store, item)

        def infer(state, questions):
            self.assertFalse(seen[-1].conn.in_transaction)
            with closing(sqlite3.connect(self.path, timeout=0)) as other, other:
                other.execute("BEGIN IMMEDIATE")
                other.execute("UPDATE runs SET source=source WHERE id=?", (run,))
            return response()

        self.ask_mock.side_effect = infer
        with patch.object(ClassifierStore, "validate_item", validate):
            CollectionJev().process_run(self.path, run)
        self.assertGreaterEqual(len(seen), 2)
        self.assertTrue(all(db.conn is None for db in seen))

    def test_edit_during_inference_records_stale_original_without_label(self):
        original = post(1, "private original")
        run = self.save(original)

        def infer(state, questions):
            self.save(post(1, "edited later"))
            return response()

        self.ask_mock.side_effect = infer
        item = CollectionJev()
        with self.assertRaisesRegex(RuntimeError, "stale_source"):
            item.process_run(self.path, run)
        saved = self.results()[0]
        self.assertTrue(item.failed)
        self.assertEqual(item.error_code, "stale_source")
        self.assertEqual(saved["status"], "error")
        self.assertIsNone(saved["label"])
        self.assertTrue(saved["stale"])
        self.assertEqual(saved["input_text"], "private original")
        self.assertEqual(saved["source"]["record"], original)
        self.assertEqual(saved["response"]["response"], response())
        self.assertEqual(saved["response"]["collection_run_id"], run)
        self.ask_mock.assert_called_once()
        with self.assertRaises(RuntimeError):
            item.process_run(self.path, run)
        self.ask_mock.assert_called_once()

    def test_source_record_or_username_change_without_text_edit_is_stale(self):
        for column, value in (("record_json", json.dumps(post(1, "same", url="changed"))),
                              ("username", "changed_username")):
            with self.subTest(column=column):
                run = self.save(post(1, "same"))

                def infer(state, questions):
                    with Database(self.path) as db, db.transaction():
                        table = "entities" if column == "username" else "messages"
                        db.conn.execute(f"UPDATE {table} SET {column}=?", (value,))
                    return response()

                self.ask_mock.side_effect = infer
                with self.assertRaisesRegex(RuntimeError, "stale_source"):
                    CollectionJev().process_run(self.path, run)
                self.assertEqual(self.results()[0]["status"], "error")
                self.assertIsNone(self.results()[0]["label"])

    def test_deleted_source_records_detached_error_with_original_snapshot(self):
        run = self.save(post(1, "original"))

        def infer(state, questions):
            with Database(self.path) as db, db.transaction():
                db.conn.execute("DELETE FROM messages")
            return response()

        self.ask_mock.side_effect = infer
        with self.assertRaisesRegex(RuntimeError, "stale_source"):
            CollectionJev().process_run(self.path, run)
        row = self.results()[0]
        self.assertIsNone(row["message_row_id"])
        self.assertEqual(row["input_text"], "original")
        self.assertEqual(row["source"]["message_id"], "1")
        self.assertIsNone(row["label"])

    def test_stale_before_inference_no_http_and_saved_safe_error(self):
        run = self.save(post(1, "original"))
        original_candidates = CollectionJev._candidates

        def candidates(item, db, store, run_id):
            rows = original_candidates(item, db, store, run_id)
            self.save(post(1, "edited"))
            return rows

        item = CollectionJev()
        with patch.object(CollectionJev, "_candidates", candidates), self.assertRaisesRegex(RuntimeError, "stale_source"):
            item.process_run(self.path, run)
        self.ask_mock.assert_not_called()
        row = self.results()[0]
        self.assertEqual(row["input_text"], "original")
        self.assertEqual(row["error_code"], "stale_source")
        self.assertIsNone(row["label"])
        self.assertTrue(item.failed)

    def test_api_failure_saved_sanitized_no_retry_and_latched(self):
        run = self.save(post(1, "private-request"), post(2, "other request"))
        self.ask_mock.side_effect = JevError("auth", "secret-key private-request private-response")
        item = CollectionJev()
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "auth") as caught:
                item.process_run(self.path, run)
            for secret in ("secret-key", "private-request", "private-response"):
                self.assertNotIn(secret, str(caught.exception))
                self.assertNotIn(secret, repr(caught.exception))
                self.assertNotIn(secret, self.output.getvalue())
        self.ask_mock.assert_called_once()
        self.assertTrue(item.failed)
        self.assertEqual(item.error_code, "auth")
        self.assertEqual(item.attempts_total, 1)
        row = self.results()[0]
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["error_code"], "auth")
        self.assertIsNone(row["label"])
        self.assertNotIn("private-response", json.dumps(row))
        self.assertEqual(row["response"], {"collection_run_id": run, "requested_model": "jev-latest"})

    def test_unexpected_inference_failure_saved_and_latched_without_leak(self):
        run = self.save(post(1))
        self.ask_mock.side_effect = OSError("secret-key private-response")
        item = CollectionJev()
        with self.assertRaisesRegex(RuntimeError, "processing_error") as caught:
            item.process_run(self.path, run)
        self.assertNotIn("secret-key", str(caught.exception))
        self.assertNotIn("private-response", str(caught.exception))
        self.assertTrue(item.failed)
        self.assertEqual(self.results()[0]["error_code"], "processing_error")
        self.assertIsNone(self.results()[0]["label"])
        with self.assertRaises(RuntimeError):
            item.process_run(self.path, run)
        self.ask_mock.assert_called_once()

    def test_invalid_run_and_database_fail_closed_without_request(self):
        self.save(post(1))
        for run in (None, True, 0, -1, 999, "1"):
            with self.subTest(run=run):
                item = CollectionJev()
                with self.assertRaisesRegex(RuntimeError, "processing_error"):
                    item.process_run(self.path, run)
                self.assertTrue(item.failed)
        bad = Path(self.temp.name) / "unrelated.sqlite3"
        with closing(sqlite3.connect(bad)) as db, db:
            db.execute("CREATE TABLE secrets (value TEXT)")
        item = CollectionJev()
        with self.assertRaisesRegex(RuntimeError, "processing_error"):
            item.process_run(bad, 1)
        with self.assertRaises(RuntimeError):
            item.process_run(self.path, 1)
        self.ask_mock.assert_not_called()

    def test_save_failure_latches_and_suppresses_exception_details(self):
        run = self.save(post(1))
        item = CollectionJev()
        with patch.object(ClassifierStore, "save", side_effect=sqlite3.OperationalError("secret-key private-request")), \
                self.assertRaisesRegex(RuntimeError, "processing_error") as caught:
            item.process_run(self.path, run)
        self.assertNotIn("secret-key", str(caught.exception))
        self.assertTrue(item.failed)
        with self.assertRaises(RuntimeError):
            item.process_run(self.path, run)
        self.ask_mock.assert_called_once()
        self.assertEqual(self.results(), [])

    def test_dry_run_no_key_no_api_no_classification_writes_shared_preview_cap(self):
        run = self.save(post(1, "private-request"), post(2), post(3, "x" * (MAX_TEXT_CHARS + 1)))
        with patch.dict(os.environ, {}, clear=True):
            item = CollectionJev(limit=1, dry_run=True)
            first = item.process_run(self.path, run)
            second = item.process_run(self.path, self.save(post(4)))
        self.assertEqual(first["previewed"], 1)
        self.assertEqual(first["skipped"], 1)
        self.assertEqual(first["attempted"], 0)
        self.assertEqual(second["previewed"], 0)
        self.assertEqual(item.previewed_total, 1)
        self.assertEqual(item.attempts_total, 0)
        self.assertEqual(self.results(), [])
        self.ask_mock.assert_not_called()
        self.assertNotIn("private-request", self.output.getvalue())
        self.assertNotIn("secret-key", self.output.getvalue())

    def test_ask_cannot_mutate_saved_requested_snapshot_or_rubric(self):
        original_questions = copy.deepcopy(QUESTIONS)
        run = self.save(post(1, "original"))

        def infer(state, questions):
            state["text"] = "mutated request"
            questions.clear()
            return response()

        self.ask_mock.side_effect = infer
        CollectionJev().process_run(self.path, run)
        self.assertEqual(self.results()[0]["input_text"], "original")
        self.assertEqual(self.results()[0]["rubric"], original_questions)
        self.assertEqual(QUESTIONS, original_questions)


if __name__ == "__main__":
    unittest.main()
