"""SQLite persistence for Telegram tools (stdlib only, independent of analyzer).

Each successful collection is an immutable run with its original JSON payload.
Normalized tables support deduplication and queries; snapshots preserve old scores.
No Telegram credentials or session keys belong in this database.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import unicodedata
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .paths import DATA_DIR
from .video_schema import VIDEO_SCHEMA
from .image_schema import IMAGE_SCHEMA

SCHEMA_VERSION = 4
APPLICATION_ID = 0x54475343  # TGSC; never mistake a Telethon session for app data
DEFAULT_DB = DATA_DIR / "telegram.sqlite3"
_active_path: str | None = None
_active_tool = ""
_active_jev = None

CLASSIFICATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS message_classifications (
    id INTEGER PRIMARY KEY,
    message_row_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    batch_id TEXT NOT NULL, created_at TEXT NOT NULL,
    provider TEXT NOT NULL, model TEXT NOT NULL, backend_key TEXT NOT NULL,
    rubric_version TEXT NOT NULL, rubric_json TEXT NOT NULL,
    input_sha256 TEXT NOT NULL, input_text TEXT NOT NULL,
    source_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('success','error','skipped')),
    label TEXT, probabilities_json TEXT, response_json TEXT, error_code TEXT,
    CHECK((status='success' AND label IS NOT NULL) OR
          (status!='success' AND label IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_classifications_cache
    ON message_classifications(message_row_id,input_sha256,rubric_version,backend_key,status);
CREATE INDEX IF NOT EXISTS idx_classifications_label ON message_classifications(label);
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, tool TEXT NOT NULL,
    backend TEXT NOT NULL, source TEXT NOT NULL, namespace TEXT NOT NULL,
    import_key TEXT UNIQUE, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY, namespace TEXT NOT NULL, peer_type TEXT,
    telegram_id TEXT, username TEXT, title TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'unknown', first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE(namespace, peer_type, telegram_id)
);
CREATE TABLE IF NOT EXISTS entity_aliases (
    namespace TEXT NOT NULL, username TEXT NOT NULL COLLATE NOCASE,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    PRIMARY KEY(namespace, username)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL, text TEXT NOT NULL DEFAULT '',
    search_text TEXT NOT NULL DEFAULT '', posted_at TEXT, url TEXT,
    collected_at TEXT, record_json TEXT NOT NULL,
    UNIQUE(entity_id, message_id)
);
CREATE TABLE IF NOT EXISTS run_messages (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    PRIMARY KEY(run_id, message_id)
);
CREATE TABLE IF NOT EXISTS analyses (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    score INTEGER, level TEXT, category TEXT, analyzer_version TEXT,
    analysis_json TEXT NOT NULL, PRIMARY KEY(run_id, message_id)
);
CREATE TABLE IF NOT EXISTS profile_snapshots (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    fetched_at TEXT, profile_json TEXT NOT NULL,
    PRIMARY KEY(run_id, entity_id)
);
CREATE TABLE IF NOT EXISTS common_groups (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    owner_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    group_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    observer TEXT NOT NULL DEFAULT '', group_json TEXT NOT NULL,
    PRIMARY KEY(run_id, owner_id, group_id)
);
CREATE TABLE IF NOT EXISTS group_summaries (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    score INTEGER, level TEXT, summary_json TEXT NOT NULL,
    PRIMARY KEY(run_id, entity_id)
);
CREATE TABLE IF NOT EXISTS search_results (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    keywords_json TEXT NOT NULL, result_json TEXT NOT NULL,
    PRIMARY KEY(run_id, entity_id)
);
CREATE TABLE IF NOT EXISTS invite_links (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    invite TEXT NOT NULL, record_json TEXT NOT NULL,
    PRIMARY KEY(run_id, invite)
);
CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(posted_at);
CREATE INDEX IF NOT EXISTS idx_analyses_score ON analyses(score);
CREATE INDEX IF NOT EXISTS idx_entities_username ON entities(username COLLATE NOCASE);
""" + CLASSIFICATION_SCHEMA + VIDEO_SCHEMA + IMAGE_SCHEMA


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def search_text(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def add_storage_arguments(parser, *, collection: bool = False) -> None:
    parser.add_argument("--db", default=os.environ.get("TELEGRAM_DB_PATH") or str(DEFAULT_DB),
                        metavar="FILE", help="SQLite database (mặc định data/telegram.sqlite3)")
    parser.add_argument("--no-db", action="store_true", help="không lưu SQLite trong lần chạy này")
    if collection:
        from .collection_jev import add_collection_jev_arguments
        add_collection_jev_arguments(parser)


def configure_storage(args, tool: str) -> None:
    global _active_path, _active_tool, _active_jev
    # Clear previous in-process CLI state before validating a new invocation.
    _active_path, _active_jev = None, None
    if getattr(args, "jev", False) and any(
        getattr(args, name, False) for name in ("report", "login", "login_bot")
    ):
        raise RuntimeError("--jev chỉ dùng khi thu thập tin, không dùng với --report/--login")
    if getattr(args, "jev", False) and getattr(args, "setup_bot", None) is not None:
        raise RuntimeError("--jev không dùng với --setup-bot")
    if hasattr(args, "jev"):
        from .collection_jev import CollectionJev
        try:
            _active_jev = CollectionJev.from_args(args)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from None
    _active_path = None if getattr(args, "no_db", False) else str(args.db)
    _active_tool = tool


def persist_payload(payload: dict | list, source: str = "", *, classify: bool = True) -> int | None:
    if _active_path is None:
        return None
    try:
        with Database(_active_path) as db:
            version = "sha256:" + hashlib.sha256(Path(__file__).with_name("analyzer.py").read_bytes()).hexdigest()
            run_id = db.save_payload(payload, tool=_active_tool, source=source, analyzer_version=version)
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        raise RuntimeError(f"Không lưu được SQLite '{_active_path}': {exc}") from exc
    print(f"[i] SQLite: run #{run_id} → {_active_path}")
    # Commit collection first, then release its connection before any paid call.
    # A Jev failure must never roll back original collection/heuristic evidence.
    if classify and _active_jev is not None:
        _active_jev.process_run(_active_path, run_id)
    return run_id


class Database:
    def __init__(self, path: str | Path = DEFAULT_DB):
        self.path = str(path)
        self.conn: sqlite3.Connection | None = None
        try:
            if self.path != ":memory:":
                target = Path(self.path).expanduser().absolute()
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                # Create with restrictive permissions before SQLite opens it.
                fd = os.open(target, os.O_CREAT | os.O_RDWR, 0o600)
                os.close(fd)
                os.chmod(target, 0o600)
                self.path = str(target)
            self.conn = sqlite3.connect(self.path, timeout=10)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys=ON")
            version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            app_id = self.conn.execute("PRAGMA application_id").fetchone()[0]
            if version not in (0, 1, 2, 3, SCHEMA_VERSION):
                raise ValueError(f"Schema version {version} chưa được hỗ trợ (cần {SCHEMA_VERSION})")
            if version != 0 and app_id != APPLICATION_ID:
                raise ValueError("Database không thuộc ứng dụng Telegram scanner")
            if version == 0:
                existing = self.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                if existing:
                    raise ValueError("Không khởi tạo trên database không thuộc ứng dụng (ví dụ session Telethon)")
                self.conn.executescript("BEGIN;\n" + SCHEMA +
                                        f"\nPRAGMA user_version={SCHEMA_VERSION};\nPRAGMA application_id={APPLICATION_ID};\nCOMMIT;")
            if version in (1, 2, 3):
                # Additive migration: no historical runs/scores/evidence are rewritten.
                additions = ((CLASSIFICATION_SCHEMA if version == 1 else "") +
                             (VIDEO_SCHEMA if version < 3 else "") + IMAGE_SCHEMA)
                self.conn.executescript("BEGIN IMMEDIATE;\n" + additions +
                                        f"\nPRAGMA user_version={SCHEMA_VERSION};\nCOMMIT;")
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    @contextmanager
    def transaction(self):
        with self.conn:
            yield

    def _merge_provisional(self, old: int, new: int) -> None:
        """Attach earlier web-only observations to a subsequently resolved ID."""
        for msg in self.conn.execute("SELECT * FROM messages WHERE entity_id=?", (old,)).fetchall():
            duplicate = self.conn.execute(
                "SELECT id FROM messages WHERE entity_id=? AND message_id=?", (new, msg["message_id"])
            ).fetchone()
            if duplicate is None:
                self.conn.execute("UPDATE messages SET entity_id=? WHERE id=?", (new, msg["id"]))
                continue
            dest = duplicate["id"]
            old_run = self.conn.execute("SELECT MAX(run_id) FROM run_messages WHERE message_id=?", (msg["id"],)).fetchone()[0] or 0
            dest_run = self.conn.execute("SELECT MAX(run_id) FROM run_messages WHERE message_id=?", (dest,)).fetchone()[0] or 0
            if old_run > dest_run:
                self.conn.execute("UPDATE messages SET text=?,search_text=?,posted_at=?,url=?,collected_at=?,record_json=? WHERE id=?",
                                  (msg["text"], msg["search_text"], msg["posted_at"], msg["url"], msg["collected_at"], msg["record_json"], dest))
            self.conn.execute("INSERT OR IGNORE INTO run_messages SELECT run_id, ? FROM run_messages WHERE message_id=?",
                              (dest, msg["id"]))
            self.conn.execute("INSERT OR IGNORE INTO analyses SELECT run_id, ?, score, level, category, analyzer_version, analysis_json FROM analyses WHERE message_id=?",
                              (dest, msg["id"]))
            # Keep AI judgments linked through identity promotion, with their
            # immutable original text/source even if the canonical message changed.
            self.conn.execute("UPDATE message_classifications SET message_row_id=? WHERE message_row_id=?",
                              (dest, msg["id"]))
            self.conn.execute("UPDATE video_analyses SET message_row_id=? WHERE message_row_id=?",
                              (dest, msg["id"]))
            self.conn.execute("UPDATE image_analyses SET message_row_id=? WHERE message_row_id=?",
                              (dest, msg["id"]))
            self.conn.execute("DELETE FROM messages WHERE id=?", (msg["id"],))
        for table, fields in (
            ("profile_snapshots", "fetched_at, profile_json"),
            ("group_summaries", "score, level, summary_json"),
            ("search_results", "keywords_json, result_json"),
        ):
            self.conn.execute(f"INSERT OR IGNORE INTO {table} SELECT run_id, ?, {fields} FROM {table} WHERE entity_id=?", (new, old))
        self.conn.execute("INSERT OR IGNORE INTO common_groups SELECT run_id, CASE WHEN owner_id=? THEN ? ELSE owner_id END, CASE WHEN group_id=? THEN ? ELSE group_id END, observer, group_json FROM common_groups WHERE owner_id=? OR group_id=?",
                          (old, new, old, new, old, old))
        self.conn.execute("UPDATE entity_aliases SET entity_id=? WHERE entity_id=?", (new, old))
        self.conn.execute("DELETE FROM entities WHERE id=?", (old,))

    def _bind_alias(self, namespace: str, username: str, eid: int) -> None:
        previous = self.conn.execute(
            "SELECT e.id,e.telegram_id FROM entity_aliases a JOIN entities e ON a.entity_id=e.id WHERE a.namespace=? AND a.username=?",
            (namespace, username)).fetchone()
        if previous is not None and previous["id"] != eid and previous["telegram_id"] is None:
            self._merge_provisional(previous["id"], eid)
        self.conn.execute(
            "INSERT INTO entity_aliases VALUES(?,?,?) ON CONFLICT(namespace,username) DO UPDATE SET entity_id=excluded.entity_id",
            (namespace, username, eid))

    def _entity(self, namespace: str, username="", telegram_id=None, kind="unknown",
                title="", peer_type="") -> int:
        username = str(username or "").strip().lstrip("@")
        # Numeric labels from legacy collectors are not public usernames.
        if username.lstrip("-").isdigit():
            if telegram_id is None:
                telegram_id = username
            username = ""
        peer_type = peer_type or ("user" if kind in ("user", "bot") else "channel")
        tid = str(telegram_id) if telegram_id is not None else None
        alias = self.conn.execute(
            "SELECT e.* FROM entity_aliases a JOIN entities e ON e.id=a.entity_id WHERE a.namespace=? AND a.username=?",
            (namespace, username)).fetchone() if username else None
        entity = self.conn.execute(
            "SELECT * FROM entities WHERE namespace=? AND peer_type=? AND telegram_id=?",
            (namespace, peer_type, tid)).fetchone() if tid is not None else None
        if entity is not None and alias is not None and entity["id"] != alias["id"] and alias["telegram_id"] is None:
            self._merge_provisional(alias["id"], entity["id"])
        if entity is None and alias is not None and (tid is None or alias["telegram_id"] is None):
            entity = alias
        if entity is None:
            cur = self.conn.execute(
                "INSERT INTO entities(namespace,peer_type,telegram_id,username,title,kind,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?)",
                (namespace, peer_type, tid, username or None, title or "", kind or "unknown", now(), now()))
            eid = cur.lastrowid
        else:
            eid = entity["id"]
            self.conn.execute(
                "UPDATE entities SET peer_type=CASE WHEN ? IS NOT NULL THEN ? ELSE peer_type END, telegram_id=COALESCE(?,telegram_id), username=COALESCE(NULLIF(?,''),username), title=COALESCE(NULLIF(?,''),title), kind=CASE WHEN ?!='unknown' THEN ? ELSE kind END, last_seen=? WHERE id=?",
                (tid, peer_type, tid, username, title or "", kind or "unknown", kind or "unknown", now(), eid))
        if username:
            self._bind_alias(namespace, username, eid)
        return eid

    def save_payload(self, payload: dict | list, tool: str = "import", source: str = "",
                     import_key: str | None = None, analyzer_version: str = "unversioned") -> int:
        """Atomic run + normalized data. Never recompute historical analysis."""
        original_payload = payload
        if isinstance(payload, list):
            payload = {"posts": payload}
        if not isinstance(payload, dict):
            raise ValueError("Payload phải là object hoặc danh sách bài đăng")
        if any(k in payload for k in ("api_id", "api_hash", "bot_token", "auth_key")):
            raise ValueError("Từ chối nhập credential/session vào database")
        if not any(k in payload for k in ("posts", "profiles", "usernames", "found", "invite_links")):
            raise ValueError("JSON không có dữ liệu được hỗ trợ (posts/profiles/usernames/found)")
        for key in ("posts", "profiles", "usernames", "groups", "found", "invite_links"):
            values = payload.get(key, [])
            if not isinstance(values, list) or any(not isinstance(v, dict) and not (key == "usernames" and isinstance(v, str)) for v in values):
                raise ValueError(f"'{key}' phải là danh sách object")
        encoded = dumps(original_payload)
        posts = list(payload.get("posts", []))
        # Older discovery exports nest actual messages under found[].history.
        # Index them without changing the original snapshot or inventing scores.
        if not posts:
            for group in payload.get("found", []):
                for item in group.get("history") or []:
                    if not isinstance(item, dict):
                        raise ValueError("found[].history phải là danh sách object")
                    record = dict(item)
                    record.setdefault("channel", group.get("channel") or str(group.get("chat_id") or group.get("invite") or ""))
                    record.setdefault("channel_title", group.get("title") or "")
                    record.setdefault("source", "telegram-api")
                    for key in ("chat_id", "peer_type"):
                        if group.get(key) is not None:
                            record.setdefault(key, group[key])
                    posts.append(record)
        backend = str(payload.get("backend") or "legacy-json")
        fallback_namespace = "demo" if "demo" in backend.lower() else "telegram"

        def record_namespace(record):
            source = record.get("source")
            return ("demo" if source == "demo" else "telegram") if source else fallback_namespace

        namespaces = {record_namespace(r) for r in posts + payload.get("profiles", [])}
        namespace = next(iter(namespaces)) if len(namespaces) == 1 else "mixed" if namespaces else fallback_namespace
        channel_namespaces = {}
        for record in posts:
            channel_namespaces.setdefault(str(record.get("channel") or "").lower(), set()).add(record_namespace(record))
        with self.transaction():
            if import_key:
                previous = self.conn.execute("SELECT id FROM runs WHERE import_key=?", (import_key,)).fetchone()
                if previous:
                    return previous[0]
            cur = self.conn.execute(
                "INSERT INTO runs(created_at,tool,backend,source,namespace,import_key,payload_json) VALUES(?,?,?,?,?,?,?)",
                (now(), tool, backend, source, namespace, import_key, encoded))
            rid = cur.lastrowid
            for profile in payload.get("profiles", []):
                profile_namespace = record_namespace(profile)
                eid = self._entity(profile_namespace, profile.get("username"), profile.get("account_id"),
                                   profile.get("kind", "unknown"), profile.get("title"), profile.get("peer_type", ""))
                for alias in profile.get("usernames", []):
                    if isinstance(alias, str) and alias:
                        self._bind_alias(profile_namespace, alias.lstrip("@"), eid)
                self.conn.execute("INSERT OR REPLACE INTO profile_snapshots VALUES(?,?,?,?)",
                                  (rid, eid, profile.get("fetched_at"), dumps(profile)))
                for group in profile.get("common_groups", []):
                    gid = self._entity(profile_namespace, group.get("username"), group.get("id"),
                                       group.get("kind", "group"), group.get("title"), group.get("peer_type", ""))
                    self.conn.execute("INSERT OR REPLACE INTO common_groups VALUES(?,?,?,?,?)",
                                      (rid, eid, gid, str(payload.get("observer") or ""), dumps(group)))
            for post in posts:
                if post.get("message_id") is None or not (post.get("channel") or post.get("chat_id")):
                    raise ValueError("Bài đăng cần channel/chat_id và message_id để chống trùng")
                eid = self._entity(record_namespace(post), post.get("channel"), post.get("chat_id"),
                                   post.get("entity_kind", "unknown"), post.get("channel_title"), post.get("peer_type", ""))
                record = {k: v for k, v in post.items() if k != "_msg_ref"}
                self.conn.execute(
                    "INSERT INTO messages(entity_id,message_id,text,search_text,posted_at,url,collected_at,record_json) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(entity_id,message_id) DO UPDATE SET text=excluded.text,search_text=excluded.search_text,posted_at=excluded.posted_at,url=excluded.url,collected_at=excluded.collected_at,record_json=excluded.record_json",
                    (eid, str(post["message_id"]), post.get("text") or "", search_text(post.get("text") or ""), post.get("posted_at"), post.get("url"), post.get("collected_at"), dumps(record)))
                mid = self.conn.execute("SELECT id FROM messages WHERE entity_id=? AND message_id=?", (eid, str(post["message_id"]))).fetchone()[0]
                self.conn.execute("INSERT OR IGNORE INTO run_messages VALUES(?,?)", (rid, mid))
                analysis = post.get("analysis")
                if isinstance(analysis, dict):
                    self.conn.execute("INSERT OR REPLACE INTO analyses VALUES(?,?,?,?,?,?,?)",
                                      (rid, mid, analysis.get("scam_score"), analysis.get("level"), analysis.get("scam_category_vi"),
                                       analysis.get("analyzer_version") or payload.get("analyzer_version") or analyzer_version, dumps(analysis)))
            for group in payload.get("groups", []):
                candidates = channel_namespaces.get(str(group.get("channel") or "").lower(), set())
                group_namespace = next(iter(candidates)) if len(candidates) == 1 else namespace
                eid = self._entity(group_namespace, group.get("channel"), title=group.get("channel_title"))
                self.conn.execute("INSERT OR REPLACE INTO group_summaries VALUES(?,?,?,?,?)",
                                  (rid, eid, group.get("group_score"), group.get("group_level"), dumps(group)))
            for row in payload.get("usernames", []):
                row = {"username": row} if isinstance(row, str) else row
                eid = self._entity(namespace, row.get("username"), kind=row.get("kind", "unknown"), title=row.get("name") or row.get("title"))
                self.conn.execute("INSERT OR REPLACE INTO search_results VALUES(?,?,?,?)",
                                  (rid, eid, dumps(row.get("keywords", payload.get("keywords", []))), dumps(row)))
            for invite in payload.get("found", []) + payload.get("invite_links", []):
                link = invite.get("invite") or invite.get("link")
                if link:
                    self.conn.execute("INSERT OR REPLACE INTO invite_links VALUES(?,?,?)", (rid, link, dumps(invite)))
            return rid

    def import_json(self, path: str | Path) -> int:
        path = Path(path).resolve()
        if path.name.lower() == "tg_api.json":
            raise ValueError("Từ chối nhập tg_api.json (credential)")
        with path.open(encoding="utf-8") as fh:
            payload = json.load(fh)
        digest = hashlib.sha256(dumps(payload).encode()).hexdigest()
        return self.save_payload(payload, tool="import-json", source=str(path), import_key=digest)

    def stats(self) -> dict:
        tables = ("runs", "entities", "messages", "analyses", "profile_snapshots", "common_groups",
                  "group_summaries", "search_results", "invite_links", "message_classifications",
                  "video_analyses", "video_classifications", "image_analyses", "image_classifications")
        return {t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}

    def runs(self, limit: int = 20) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT id,created_at,tool,backend,namespace,source FROM runs ORDER BY id DESC LIMIT ?", (limit,))]

    def payload(self, run_id: int) -> dict | list:
        row = self.conn.execute("SELECT payload_json FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"Không có run #{run_id}")
        return json.loads(row[0])

    def messages(self, keyword: str = "", username: str = "", limit: int = 20) -> list[dict]:
        # INSTR treats %, _ and quotes literally; every user value is bound.
        sql = "SELECT m.record_json FROM messages m JOIN entities e ON e.id=m.entity_id WHERE instr(m.search_text,?)>0"
        params = [search_text(keyword)]
        if username:
            sql += " AND e.id IN (SELECT entity_id FROM entity_aliases WHERE username=? COLLATE NOCASE)"
            params.append(username.lstrip("@"))
        sql += " ORDER BY m.posted_at DESC,m.id DESC LIMIT ?"
        params.append(limit)
        return [json.loads(r[0]) for r in self.conn.execute(sql, params)]

    def profiles(self, username: str, limit: int = 20) -> list[dict]:
        return [json.loads(r[0]) for r in self.conn.execute(
            "SELECT p.profile_json FROM profile_snapshots p WHERE p.entity_id IN (SELECT entity_id FROM entity_aliases WHERE username=? COLLATE NOCASE) ORDER BY p.run_id DESC LIMIT ?", (username.lstrip("@"), limit))]

    def backup(self, target: str | Path) -> None:
        dest = Path(target).expanduser().resolve()
        if dest == Path(self.path).resolve() or dest.exists():
            raise ValueError("File backup phải là đường dẫn mới, khác database nguồn")
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(dest)) as other:
            self.conn.backup(other)
