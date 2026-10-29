"""Offline classification regression tests; no production DB/network/API keys."""
from __future__ import annotations

import copy
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from telegram_demo import storage
from telegram_demo.cli import classify_messages as cli
from telegram_demo.storage import Database
from telegram_demo.text_classifier import (ClassifierStore, LABELS, MAX_TEXT_CHARS, QUESTIONS,
                             RESULT_FORMAT, RUBRIC_VERSION, new_manifest, tool_backend,
                             validate_manifest)

TOOL_OUTPUT = ("Jev jev-1.14.0 (requested alias jev-latest resolved to this version), 123 input tokens. "
               "Probabilities are not calibrated.\n\n"
               "classification: scam_indicators (highest probability, confidence 0.987)\n"
               "    distribution: scam_indicators 0.987 / other 0.013")


def post(mid, text="Cam kết lãi 30% mỗi ngày. Chuyển tiền ngay.", **fields):
    return {"channel": "test_channel", "message_id": str(mid), "text": text,
            "analysis": {"scam_score": 12, "level": "Normal"}, **fields}


class ClassifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.db = Database(self.path)
        self.store = ClassifierStore(self.db)
        self.backend = tool_backend("jev-latest")
        self.payload = {"backend": "web", "posts": [post(1)]}
        self.run_id = self.db.save_payload(self.payload)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def item(self):
        return self.store.candidates(backend_key=self.backend)[0]

    def save(self, item=None, **changes):
        params = dict(batch_id="test-batch", provider="test-jev", model="jev-1.14.0",
                      backend_key=self.backend, status="success", label="scam_indicators",
                      response={"test": True})
        params.update(changes)
        return self.store.save(item or self.item(), **params)

    def call(self, *args):
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            code = cli.main(["--db", str(self.path), *map(str, args)])
        return code, out.getvalue(), err.getvalue()

    def artifacts(self, count=1, recheck=False):
        items = self.store.candidates(backend_key=self.backend, limit=count, recheck=True)
        manifest = new_manifest(items, "jev-latest", recheck)
        results = {"format": RESULT_FORMAT, "batch_id": manifest["batch_id"],
                   "results": [{"message_row_id": i["message_row_id"],
                                "input_sha256": i["input_sha256"], "tool_output": TOOL_OUTPUT}
                               for i in items]}
        req, res = Path(self.temp.name) / "req.json", Path(self.temp.name) / "res.json"
        req.write_text(json.dumps(manifest), encoding="utf-8")
        res.write_text(json.dumps(results), encoding="utf-8")
        return req, res, manifest, results

    def test_rubric_covers_context_and_prompt_injection(self):
        self.assertEqual(set(QUESTIONS["classification"]["criteria"]), set(LABELS))
        self.assertIn("dữ liệu không đáng tin", QUESTIONS["classification"]["instructions"])
        self.assertIn("không xác minh", QUESTIONS["classification"]["instructions"])
        self.assertEqual(len(RUBRIC_VERSION.split(":")[-1]), 64)

    def test_preserves_heuristic_and_original_run(self):
        self.save()
        row = self.store.results()[0]
        self.assertEqual(row["label"], "scam_indicators")
        self.assertEqual(row["rubric"], QUESTIONS)
        self.assertEqual(row["input_text"], self.payload["posts"][0]["text"])
        self.assertEqual(self.db.payload(self.run_id), self.payload)
        self.assertEqual(self.db.messages()[0]["analysis"]["scam_score"], 12)
        self.assertEqual(self.db.stats()["analyses"], 1)
        self.assertFalse(row["stale"])

    def test_cache_success_only_and_advance_limit(self):
        self.db.save_payload({"posts": [post(2)]})
        first = self.store.candidates(backend_key=self.backend, limit=1)[0]
        self.save(first, status="error", label=None, error_code="auth", response=None)
        self.assertEqual(self.store.candidates(backend_key=self.backend, limit=1)[0], first)
        self.save(first)
        next_item = self.store.candidates(backend_key=self.backend, limit=1)[0]
        self.assertNotEqual(next_item["message_row_id"], first["message_row_id"])
        self.assertEqual(len(self.store.candidates(backend_key=self.backend, recheck=True)), 2)
        self.assertEqual(len(self.store.candidates(backend_key="another-backend")), 2)

    def test_edited_text_new_work_and_stale_previous(self):
        self.save()
        self.db.save_payload({"posts": [post(1, "Nội dung đã thay đổi")]})
        self.assertEqual(len(self.store.candidates(backend_key=self.backend)), 1)
        self.assertTrue(self.store.results()[0]["stale"])

    def test_same_text_different_messages_independent(self):
        self.db.save_payload({"posts": [post(2)]})
        self.save()
        self.assertEqual(len(self.store.candidates(backend_key=self.backend)), 1)

    def test_filters_demo_empty_username_keyword_run_and_ids(self):
        demo_run = self.db.save_payload({"backend": "demo", "posts": [post(10)]})
        self.db.save_payload({"posts": [post(3, "   "), post(4, "bán giày") ]})
        items = self.store.candidates(backend_key=self.backend)
        self.assertEqual(len(items), 2)
        self.assertEqual(len(self.store.candidates(backend_key=self.backend, include_demo=True)), 3)
        self.assertEqual(len(self.store.candidates(backend_key=self.backend, keyword="lai", username="@TEST_CHANNEL")), 1)
        self.assertEqual(self.store.candidates(backend_key=self.backend, keyword="' OR 1=1 --"), [])
        self.assertEqual(self.store.candidates(backend_key=self.backend, username="' OR 1=1 --"), [])
        self.assertEqual(self.store.candidates(backend_key=self.backend, run_id=demo_run), [])
        self.assertEqual(len(self.store.candidates(backend_key=self.backend, run_id=self.run_id)), 1)
        self.assertEqual(len(self.store.candidates(backend_key=self.backend, message_ids=[items[0]["message_row_id"]])), 1)
        with self.assertRaises(ValueError):
            self.store.candidates(backend_key=self.backend, limit=1001)

    def test_results_filters_bound(self):
        self.save()
        self.assertEqual(len(self.store.results(label="scam_indicators", status="success")), 1)
        self.assertEqual(self.store.results(label="' OR 1=1 --"), [])

    def test_schema_v1_migration_preserves_payload(self):
        path = Path(self.temp.name) / "v1.sqlite3"
        with closing(sqlite3.connect(path)) as conn:
            conn.executescript(storage.SCHEMA.replace(storage.CLASSIFICATION_SCHEMA, "").replace(storage.VIDEO_SCHEMA, "") +
                               f"PRAGMA user_version=1; PRAGMA application_id={storage.APPLICATION_ID};")
            conn.execute("INSERT INTO runs VALUES(1,'test','test','web','test','telegram',NULL,?)",
                         (json.dumps(self.payload),))
            conn.commit()
        with Database(path) as db:
            self.assertEqual(db.conn.execute("PRAGMA user_version").fetchone()[0], storage.SCHEMA_VERSION)
            self.assertEqual(db.payload(1), self.payload)
            self.assertEqual(db.stats()["message_classifications"], 0)
            self.assertEqual(db.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_foreign_v1_rejected_without_creating_classification_table(self):
        path = Path(self.temp.name) / "foreign.sqlite3"
        with closing(sqlite3.connect(path)) as conn:
            conn.executescript("CREATE TABLE private(x); PRAGMA user_version=1;")
        with self.assertRaises(ValueError):
            Database(path)
        with closing(sqlite3.connect(path)) as conn:
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='message_classifications'").fetchone())

    def test_merge_identity_retains_both_judgments(self):
        self.db.save_payload({"posts": [post(1, channel="old_name", chat_id=42, peer_type="channel")]})
        self.db.save_payload({"posts": [post(1, channel="new_name")]})
        items = self.store.candidates(backend_key=self.backend)
        for item in items:
            self.save(item)
        self.db.save_payload({"profiles": [{"username": "new_name", "account_id": 42, "kind": "channel"}]})
        results = self.store.results()
        merged = [r for r in results if r["source"]["username"] in ("old_name", "new_name")]
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["message_row_id"], merged[1]["message_row_id"])
        self.assertEqual(self.db.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_deleted_message_keeps_evidence(self):
        item = self.item()
        self.save(item)
        with self.db.transaction():
            self.db.conn.execute("DELETE FROM messages WHERE id=?", (item["message_row_id"],))
        result = self.store.results()[0]
        self.assertIsNone(result["message_row_id"])
        self.assertTrue(result["stale"])
        self.assertEqual(result["input_text"], item["state"]["text"])

    def test_cli_prepare_permissions_no_network_and_no_overwrite(self):
        target = Path(self.temp.name) / "requests.json"
        code, out, err = self.call("prepare", "--out", target)
        self.assertEqual((code, err), (0, ""))
        manifest = json.loads(target.read_text())
        validate_manifest(manifest)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.db.stats()["message_classifications"], 0)
        code, _, _ = self.call("prepare", "--out", target)
        self.assertEqual(code, 2)

    def test_apply_live_format_preserves_raw_output_and_idempotent(self):
        req, res, _, _ = self.artifacts()
        self.assertEqual(self.call("apply", "--requests", req, "--results", res)[0], 0)
        row = self.store.results()[0]
        self.assertEqual(row["model"], "jev-1.14.0")
        self.assertEqual(row["provider"], "dsh-jev-tool-import")
        self.assertEqual(row["response"]["tool_output"], TOOL_OUTPUT)
        self.assertIsNone(row["probabilities"])
        self.assertEqual(self.call("apply", "--requests", req, "--results", res)[0], 0)
        self.assertEqual(self.db.stats()["message_classifications"], 1)

    def test_apply_recheck_retains_history(self):
        self.save()
        req, res, _, _ = self.artifacts(recheck=True)
        self.assertEqual(self.call("apply", "--requests", req, "--results", res)[0], 0)
        self.assertEqual(self.db.stats()["message_classifications"], 2)

    def test_apply_rejects_tampered_hash_and_no_partial_writes(self):
        self.db.save_payload({"posts": [post(2)]})
        req, res, _, results = self.artifacts(count=2)
        results["results"][1]["input_sha256"] = "wrong"
        res.write_text(json.dumps(results))
        self.assertEqual(self.call("apply", "--requests", req, "--results", res)[0], 2)
        self.assertEqual(self.db.stats()["message_classifications"], 0)

    def test_apply_rejects_stale_text_and_wrong_rubric(self):
        req, res, manifest, _ = self.artifacts()
        manifest["questions"]["classification"]["instructions"] = "changed"
        req.write_text(json.dumps(manifest))
        self.assertEqual(self.call("apply", "--requests", req, "--results", res)[0], 2)
        req, res, _, _ = self.artifacts()
        self.db.save_payload({"posts": [post(1, "changed text")]})
        self.assertEqual(self.call("apply", "--requests", req, "--results", res)[0], 2)
        self.assertEqual(self.db.stats()["message_classifications"], 0)

    def test_apply_missing_result_nonzero_no_invented_labels(self):
        self.db.save_payload({"posts": [post(2)]})
        req, res, _, results = self.artifacts(count=2)
        results["results"].pop()
        res.write_text(json.dumps(results))
        code, out, _ = self.call("apply", "--requests", req, "--results", res)
        self.assertEqual(code, 1)
        self.assertIn("thiếu kết quả 1", out)
        self.assertEqual(self.db.stats()["message_classifications"], 1)

    def test_invalid_tool_output_rejected(self):
        for output in ("", "made up", TOOL_OUTPUT.replace("scam_indicators (", "unlisted ("), TOOL_OUTPUT + "\nclassification: other (highest probability)"):
            with self.assertRaises(ValueError):
                cli.parse_tool_output(output)

    def test_bad_manifest_structure(self):
        valid = new_manifest([self.item()], "jev-latest")
        for changed in ({"items": [1]}, {"items": [{"message_row_id": []}]},
                        {"recheck": "yes"}, {"model": ""}):
            invalid = {**valid, **changed}
            with self.assertRaises(ValueError):
                validate_manifest(invalid)
        broken = self.item()
        broken["state"] = []
        with self.assertRaises(ValueError):
            self.store.validate_item(broken)

    def test_cli_results_json(self):
        self.save()
        code, out, err = self.call("results", "--label", "scam_indicators")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)[0]["label"], "scam_indicators")

    def test_direct_api_success_and_resume(self):
        from telegram_demo.jev_client import JevClient
        response = {"model": "jev-1.14.0", "answers": {"classification": {
            "type": "choice", "choice": "scam_indicators", "confidence": 0.9,
            "probabilities": {key: (0.9 if key == "scam_indicators" else 0.025) for key in LABELS}}},
            "usage": {"input_tokens": 100, "output_tokens": 10}}
        with patch.object(JevClient, "ask", return_value=response) as ask:
            self.assertEqual(self.call("run", "--model", "jev-latest")[0], 0)
            self.assertEqual(ask.call_count, 1)
            self.assertEqual(self.call("run", "--model", "jev-latest")[0], 0)
            self.assertEqual(ask.call_count, 1)
        row = self.store.results()[0]
        self.assertEqual(row["probabilities"]["scam_indicators"], 0.9)
        self.assertEqual(row["response"]["requested_model"], "jev-latest")

    def test_direct_api_failure_stored_no_fallback_and_retry_possible(self):
        from telegram_demo.jev_client import JevClient, JevError
        with patch.object(JevClient, "ask", side_effect=JevError("auth", "redacted")):
            code, _, err = self.call("run")
        self.assertEqual(code, 1)
        row = self.store.results()[0]
        self.assertEqual(row["status"], "error")
        self.assertIsNone(row["label"])
        self.assertEqual(row["error_code"], "auth")
        self.assertEqual(len(self.store.candidates(backend_key=JevClient().backend_key)), 1)

    def test_dry_run_no_network_no_inference_records(self):
        from telegram_demo.jev_client import JevClient
        with patch.object(JevClient, "ask", side_effect=AssertionError("must not call")):
            code, out, err = self.call("run", "--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(len(json.loads(out)["messages"]), 1)
        self.assertEqual(self.db.stats()["message_classifications"], 0)

    def test_long_text_run_advances_past_terminal_skip(self):
        from telegram_demo.jev_client import JevClient
        self.db.save_payload({"posts": [post(2, "a" * (MAX_TEXT_CHARS + 1))]})
        response = {"model": "jev-1.14.0", "answers": {"classification": {
            "type": "choice", "choice": "other", "confidence": 1.0,
            "probabilities": {key: float(key == "other") for key in LABELS}}},
            "usage": {"input_tokens": 100, "output_tokens": 10}}
        with patch.object(JevClient, "ask", return_value=response) as ask:
            self.assertEqual(self.call("run", "--limit", "1")[0], 1)
            ask.assert_not_called()
            self.assertEqual(self.call("run", "--limit", "1")[0], 0)
            self.assertEqual(ask.call_count, 1)
            self.assertEqual(self.call("run", "--limit", "1")[0], 0)
            self.assertEqual(ask.call_count, 1)
        self.assertEqual(self.db.stats()["message_classifications"], 2)
        self.assertEqual(len(self.store.candidates(backend_key=JevClient().backend_key)), 0)
        self.assertEqual(len(self.store.candidates(backend_key=JevClient().backend_key, recheck=True)), 2)
        self.db.save_payload({"posts": [post(2, "edited shorter text")]})
        self.assertEqual(len(self.store.candidates(backend_key=JevClient().backend_key)), 1)

    def test_long_text_prepare_advances_past_terminal_skip(self):
        self.db.save_payload({"posts": [post(2, "a" * (MAX_TEXT_CHARS + 1))]})
        first = Path(self.temp.name) / "first.json"
        second = Path(self.temp.name) / "second.json"
        self.assertEqual(self.call("prepare", "--limit", "1", "--out", first)[0], 1)
        self.assertEqual(json.loads(first.read_text())["items"], [])
        self.assertEqual(self.call("prepare", "--limit", "1", "--out", second)[0], 0)
        self.assertEqual(len(json.loads(second.read_text())["items"]), 1)
        self.assertEqual(self.db.stats()["message_classifications"], 1)
        self.assertEqual(self.store.results()[0]["error_code"], "text_too_long")

    def test_generated_manifest_byte_bound_prevents_unreadable_output(self):
        target = Path(self.temp.name) / "large.json"
        with patch.object(cli, "MAX_JSON_BYTES", 100):
            code, _, err = self.call("prepare", "--out", target)
            self.assertEqual(code, 2)
            self.assertIn("giảm --limit", err)
            self.assertFalse(target.exists())
            self.assertEqual(self.db.stats()["message_classifications"], 0)
            # UTF-8 byte count, not Python character count, controls the bound.
            with self.assertRaises(ValueError):
                cli.write_new_json(str(target), {"text": "đ" * 60})
            self.assertFalse(target.exists())

    def test_long_text_skipped_not_truncated_or_sent(self):
        from telegram_demo.jev_client import JevClient
        self.db.save_payload({"posts": [post(1, "a" * (MAX_TEXT_CHARS + 1))]})
        with patch.object(JevClient, "ask", side_effect=AssertionError("must not call")):
            code, _, _ = self.call("run")
        self.assertEqual(code, 1)
        row = self.store.results()[0]
        self.assertEqual(row["status"], "skipped")
        self.assertEqual(row["error_code"], "text_too_long")
        self.assertEqual(len(row["input_text"]), MAX_TEXT_CHARS + 1)


if __name__ == "__main__":
    unittest.main()
