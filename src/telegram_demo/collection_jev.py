"""Opt-in text Jev for one persisted collection run, never a heuristic fallback.

One object owns a shared *request-count* cap, not a dollar/cost cap, across watch
callbacks. Cache identity follows JevClient's endpoint/requested-model identity;
a moving model alias does not automatically invalidate earlier classifications.
"""
from __future__ import annotations

import argparse
import copy
import math
import os
import uuid

from .jev_client import JevClient, JevError
from .storage import Database
from .text_classifier import (ClassifierStore, LABELS, MAX_TEXT_CHARS, QUESTIONS,
                              RUBRIC_VERSION, text_hash)

_SCAN_LIMIT = 1000
# SQLite trim() otherwise only removes ASCII spaces, unlike Python str.strip().
_WHITESPACE = " \t\n\r\v\f\x1c\x1d\x1e\x1f\x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
_SAFE_API_CODES = {"configuration", "invalid_response", "auth", "rate_limit",
                   "http_error", "timeout", "network"}


class _Modifier(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, self.const if self.nargs == 0 else values)
        setattr(namespace, "_collection_jev_modifier", True)


def add_collection_jev_arguments(parser) -> None:
    """Add collection-only switches; from_args enforces opt-in dependencies."""
    parser.add_argument("--jev", action="store_true", help="classify newly saved text with Jev (opt in)")
    parser.add_argument("--jev-limit", type=int, default=10, action=_Modifier,
                        help="shared cap of 1..1000 HTTP attempts, not dollars (default: 10)")
    parser.add_argument("--jev-model", default=os.environ.get("JEV_MODEL", "jev-latest"), action=_Modifier,
                        help="requested Jev model; moving aliases keep the existing cache identity")
    parser.add_argument("--jev-timeout", type=float, default=60, action=_Modifier,
                        help="request timeout in seconds, >0 and <=300 (default: 60)")
    parser.add_argument("--jev-include-demo", nargs=0, const=True, default=False, action=_Modifier,
                        help="also consider demo-namespace text")
    parser.add_argument("--jev-dry-run", nargs=0, const=True, default=False, action=_Modifier,
                        help="preview bounded candidates, without API calls or classification writes")


class _StaleSource(RuntimeError):
    pass


class CollectionJev:
    """Sequential collection classifier with a lifetime cap and latched failures."""

    def __init__(self, *, limit=10, model=None, timeout=60, include_demo=False, dry_run=False):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("--jev-limit must be an integer from 1 to 1000")
        if type(timeout) not in (int, float) or not 0 < timeout <= 300 or not math.isfinite(timeout):
            raise ValueError("--jev-timeout must be >0 and <=300 seconds")
        model = os.environ.get("JEV_MODEL", "jev-latest") if model is None else model
        if not isinstance(model, str) or not model.strip():
            raise ValueError("--jev-model must be nonblank")
        if type(include_demo) is not bool or type(dry_run) is not bool:
            raise ValueError("Invalid collection Jev flags")
        key = os.environ.get("TYPESAFE_API_KEY")
        if not dry_run and (not isinstance(key, str) or not key.strip()):
            raise RuntimeError("TYPESAFE_API_KEY is required for --jev (except --jev-dry-run)")
        if not dry_run and any(not 33 <= ord(char) <= 126 for char in key):
            raise RuntimeError("Invalid Jev API credentials.")
        try:
            self.client = JevClient(model=model, timeout=timeout)
        except JevError:
            raise RuntimeError("Invalid collection Jev configuration.") from None
        self.limit = limit
        self.include_demo = include_demo
        self.dry_run = dry_run
        self.attempts_total = 0
        self.previewed_total = 0
        self.failed = False
        self.error_code = None

    @classmethod
    def from_args(cls, args) -> CollectionJev | None:
        """Validate before collection; old namespaces without these flags work."""
        if not getattr(args, "jev", False):
            defaults = {"jev_limit": 10, "jev_model": os.environ.get("JEV_MODEL", "jev-latest"),
                        "jev_timeout": 60, "jev_include_demo": False, "jev_dry_run": False}
            if getattr(args, "_collection_jev_modifier", False) or any(
                hasattr(args, name) and getattr(args, name) != default
                for name, default in defaults.items()
            ):
                raise ValueError("Collection Jev modifiers require --jev")
            return None
        if getattr(args, "no_db", False):
            raise ValueError("--jev requires SQLite; cannot combine --jev with --no-db")
        return cls(limit=getattr(args, "jev_limit", 10), model=getattr(args, "jev_model", None),
                   timeout=getattr(args, "jev_timeout", 60),
                   include_demo=getattr(args, "jev_include_demo", False),
                   dry_run=getattr(args, "jev_dry_run", False))

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.attempts_total - self.previewed_total)

    def _fail(self, code):
        self.failed = True
        self.error_code = code
        raise RuntimeError(f"Collection Jev failed ({code}); no fallback labels.") from None

    def _candidates(self, db, store, run_id):
        # Select short texts first so deterministic oversize skips cannot crowd
        # smaller eligible messages out of the bounded 1000-row scan. Exclude
        # cached successes/terminal skips BEFORE this selection, not after LIMIT.
        sql = """SELECT m.id FROM messages m JOIN entities e ON e.id=m.entity_id
                 JOIN run_messages rm ON rm.message_id=m.id
                 WHERE rm.run_id=? AND length(trim(m.text,?))>0"""
        params = [run_id, _WHITESPACE]
        if not self.include_demo:
            sql += " AND e.namespace!='demo'"
        sql += """ AND NOT EXISTS (SELECT 1 FROM message_classifications c
                   WHERE c.message_row_id=m.id AND c.input_text=m.text
                   AND c.rubric_version=? AND c.backend_key=?
                   AND (c.status='success' OR
                       (c.status='skipped' AND c.error_code='text_too_long')))
                   ORDER BY (length(m.text)>?),m.posted_at DESC,m.id DESC LIMIT ?"""
        params.extend((RUBRIC_VERSION, self.client.backend_key, MAX_TEXT_CHARS, _SCAN_LIMIT))
        ids = [row[0] for row in db.conn.execute(sql, params).fetchall()]
        if not ids:
            return []
        return store.candidates(backend_key=self.client.backend_key, limit=_SCAN_LIMIT,
                                run_id=run_id, message_ids=ids, include_demo=self.include_demo)

    @staticmethod
    def _validate(db, store, item):
        try:
            if len(item["state"]["text"]) <= MAX_TEXT_CHARS:
                store.validate_item(item)
            else:
                # validate_item intentionally refuses oversize input; bind a
                # terminal skip to the same exact canonical snapshot ourselves.
                current = store.candidates(backend_key="", limit=1, include_demo=True,
                                           message_ids=[item["message_row_id"]], recheck=True)
                if not current or current[0] != item or text_hash(item["state"]["text"]) != item["input_sha256"]:
                    raise ValueError("stale")
            row = db.conn.execute(
                "SELECT e.username FROM messages m JOIN entities e ON e.id=m.entity_id WHERE m.id=?",
                (item["message_row_id"],)).fetchone()
            if row is None or row[0] != item["source"].get("username"):
                raise ValueError("stale")
        except ValueError:
            raise _StaleSource() from None

    def _save(self, store, item, batch_id, run_id, *, status, response=None,
              error_code=None, label=None, probabilities=None):
        provenance = {"requested_model": self.client.model, "collection_run_id": run_id}
        if response is not None:
            provenance["response"] = response
        return store.save(item, batch_id=batch_id, provider="typesafe-jev",
                          model=response["model"] if response is not None else self.client.model,
                          backend_key=self.client.backend_key, status=status, label=label,
                          probabilities=probabilities, response=provenance, error_code=error_code)

    def _record_error(self, db, store, item, batch_id, run_id, code, response=None):
        with db.transaction():
            db.conn.execute("BEGIN IMMEDIATE")
            evidence = copy.deepcopy(item)
            if not db.conn.execute("SELECT 1 FROM messages WHERE id=?", (item["message_row_id"],)).fetchone():
                evidence["message_row_id"] = None  # preserve requested source after deletion
            self._save(store, evidence, batch_id, run_id, status="error", error_code=code,
                       response=response)

    def process_run(self, db_path, run_id) -> dict:
        """Classify only run_id's text, opening/closing our own SQLite handle.

        The handle remains open, but no transaction or writer lock spans ask().
        Post-inference validation and the INSERT are protected by one short write
        transaction. Stale inputs preserve their requested source as error evidence.
        """
        if self.failed:
            self._fail(self.error_code)
        summary = {"collection_run_id": run_id, "candidates": 0, "attempted": 0,
                   "success": 0, "skipped": 0, "previewed": 0, "dry_run": self.dry_run}
        try:
            if type(run_id) is not int or run_id < 1:
                raise ValueError("Invalid run")
            with Database(db_path) as db:
                if not db.conn.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone():
                    raise ValueError("Missing run")
                store = ClassifierStore(db)
                items = self._candidates(db, store, run_id) if self.remaining else []
                summary["candidates"] = len(items)
                batch_id = str(uuid.uuid4())
                for item in items:
                    if not item["state"]["text"].strip():
                        continue
                    try:
                        self._validate(db, store, item)
                    except _StaleSource:
                        if not self.dry_run:
                            self._record_error(db, store, item, batch_id, run_id, "stale_source")
                        self._fail("stale_source")
                    if len(item["state"]["text"]) > MAX_TEXT_CHARS:
                        if not self.dry_run:
                            try:
                                with db.transaction():
                                    db.conn.execute("BEGIN IMMEDIATE")
                                    self._validate(db, store, item)
                                    self._save(store, item, batch_id, run_id, status="skipped",
                                               error_code="text_too_long")
                            except _StaleSource:
                                self._record_error(db, store, item, batch_id, run_id, "stale_source")
                                self._fail("stale_source")
                        summary["skipped"] += 1
                        continue
                    if not self.remaining:
                        continue
                    if self.dry_run:
                        self.previewed_total += 1
                        summary["previewed"] += 1
                        print(f"[i] Jev preview row #{item['message_row_id']}: {len(item['state']['text'])} characters")
                        continue
                    self.attempts_total += 1  # conservative: an ask attempt reserves one HTTP slot
                    summary["attempted"] += 1
                    try:
                        response = self.client.ask(copy.deepcopy(item["state"]), copy.deepcopy(QUESTIONS))
                    except JevError as exc:
                        code = exc.code if exc.code in _SAFE_API_CODES else "api_error"
                        self._record_error(db, store, item, batch_id, run_id, code)
                        self._fail(code)
                    except Exception:
                        self._record_error(db, store, item, batch_id, run_id, "processing_error")
                        self._fail("processing_error")
                    try:
                        with db.transaction():
                            db.conn.execute("BEGIN IMMEDIATE")
                            self._validate(db, store, item)
                            answer = response["answers"]["classification"]
                            self._save(store, item, batch_id, run_id, status="success", response=response,
                                       label=answer["choice"], probabilities=answer["probabilities"])
                    except _StaleSource:
                        self._record_error(db, store, item, batch_id, run_id, "stale_source", response)
                        self._fail("stale_source")
                    summary["success"] += 1
                    print(f"[i] Jev row #{item['message_row_id']}: {answer['choice']} — {LABELS[answer['choice']]}")
        except Exception:
            if not self.failed:
                self._fail("processing_error")
            raise RuntimeError(f"Collection Jev failed ({self.error_code}); no fallback labels.") from None
        summary.update(attempts_total=self.attempts_total, remaining=self.remaining)
        print(f"[i] Jev run #{run_id}: success={summary['success']}, skipped={summary['skipped']}, "
              f"previewed={summary['previewed']}; HTTP attempts={self.attempts_total}/{self.limit} "
              "(request cap, not dollars)")
        return summary
