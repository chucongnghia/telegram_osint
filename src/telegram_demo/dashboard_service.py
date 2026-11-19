"""Loopback-only, read-only browsing and explicitly submitted local CLI jobs.

No Telegram/model modules are imported while browsing. Only this module owns its
child processes; database browsing deliberately does not use storage.Database,
whose constructor migrates schemas. The web UI is not a remote administration API.
"""
from __future__ import annotations

from collections import deque
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import mimetypes
import math
import os
from pathlib import Path
import queue
import re
import secrets
import signal
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit
import uuid
import webbrowser

from .paths import DATA_DIR, JSON_DIR, MEDIA_DIR, PROJECT_ROOT, SESSIONS_DIR
from .storage import APPLICATION_ID, DEFAULT_DB, search_text

STATIC_DIR = Path(__file__).with_name("dashboard")
MAX_BODY = 65536
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}
VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo", ".mpeg": "video/mpeg", ".mpg": "video/mpeg", ".3gp": "video/3gpp", ".ts": "video/mp2t"}
IMAGE_OCR_TYPES = {ext: IMAGE_TYPES[ext] for ext in (".jpg", ".jpeg", ".png", ".webp")}
MEDIA_TYPES = {**IMAGE_TYPES, **VIDEO_TYPES}
TERMINAL = {"succeeded", "failed", "cancelled"}
CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self'; media-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"


class DashboardError(ValueError):
    def __init__(self, message, code="invalid_input", status=400):
        super().__init__(message)
        self.code, self.status = code, status


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value, name, maximum=256, required=False):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise DashboardError(f"{name}: chuỗi không hợp lệ hoặc quá dài.")
    value = value.strip()
    if required and not value:
        raise DashboardError(f"Cần nhập {name}.")
    return value


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise DashboardError(f"{name} phải là số nguyên từ {low} đến {high}.")
    return value


def _boolean(value, name):
    if type(value) is not bool:
        raise DashboardError(f"{name} phải là boolean.")
    return value


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _is_loopback_host(host):
    host = (host or "").lower().strip("[]")
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


def _vision_endpoint(raw):
    text = _text(raw, "vision endpoint", 400, True)
    parsed = urlsplit(text)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise DashboardError("Endpoint thị giác phải là http(s)://host/đường-dẫn, không kèm userinfo, query hay fragment.")
    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname):
        raise DashboardError("Chỉ dùng HTTP cho endpoint loopback; endpoint thị giác từ xa phải dùng HTTPS.")
    return text


def _vision_model_name(raw):
    text = _text(raw, "vision model", 200, True)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}", text):
        raise DashboardError("Tên model thị giác chứa ký tự không hợp lệ.")
    return text


def _secret_key(raw, name):
    key = _text(raw, name, 512, True)
    if any(not 33 <= ord(c) <= 126 for c in key):
        raise DashboardError(f"{name} chỉ nhận ký tự ASCII hiển thị.")
    return key


def _json(raw, fallback=None):
    if not isinstance(raw, str) or len(raw) > 2 * 1024 * 1024:
        return fallback
    try:
        return json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        return fallback


def _safe_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        p = urlsplit(value)
        return value if p.scheme in ("http", "https") and p.hostname in ("t.me", "telegram.me", "www.t.me") and not p.username and not p.password else None
    except ValueError:
        return None


def _public_value(value, depth=0):
    """Bound public model evidence; never serialize arbitrary source/options JSON."""
    if depth > 8:
        return None
    if isinstance(value, str):
        return value[:16000]
    if type(value) is float and not math.isfinite(value):
        return None
    if value is None or type(value) in (bool, int, float):
        return value
    if isinstance(value, list):
        return [_public_value(v, depth + 1) for v in value[:200]]
    if isinstance(value, dict):
        return {str(k)[:80]: _public_value(v, depth + 1) for k, v in list(value.items())[:100]
                if not re.search(r"key|token|credential|authorization|secret|password|path|source_json|options", str(k), re.I)}
    return None


def _secrets_path():
    """Private local file holding dashboard-entered keys (0600, git-ignored, plaintext)."""
    return JSON_DIR / "dashboard_secrets.json"



def _write_private_json(path: Path, payload: dict) -> None:
    """Ghi JSON nguyên tử, mode 0600, không theo symlink (cùng kiểu dashboard_secrets.json)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    except OSError as exc:
        raise DashboardError(f"Không ghi được file cấu hình cục bộ: {exc.strerror or 'lỗi IO'}") from None


def _api_config_path() -> Path:
    return JSON_DIR / "tg_api.json"


def _api_credentials() -> tuple[int | None, str | None]:
    """api_id/api_hash từ file CLI dùng chung (json_folder/tg_api.json) hoặc biến môi trường."""
    api_id = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")
    path = _api_config_path()
    try:
        if path.is_file() and not path.is_symlink():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                api_id = api_id or data.get("api_id")
                api_hash = api_hash or data.get("api_hash")
    except (OSError, ValueError):
        pass
    try:
        identifier = int(api_id)
    except (TypeError, ValueError):
        identifier = None
    if identifier is not None and not 1 <= identifier <= 2_147_483_647:
        identifier = None
    digest = api_hash.strip().lower() if isinstance(api_hash, str) else None
    if not digest or not re.fullmatch(r"[0-9a-f]{32}", digest):
        digest = None
    if identifier is None or digest is None:
        return None, None
    return identifier, digest


class DashboardApp:
    def __init__(self, db_path=DEFAULT_DB):
        self.db_path = Path(db_path).expanduser().absolute()
        self._jobs = {}
        self._queue = queue.Queue(maxsize=8)
        self._lock = threading.RLock()
        self._worker = None
        self._closed = False
        self._jev_key = None
        self._vision_key = None
        self._vision_model = None
        self._vision_provider = None
        self._vision_url = None
        self._redactions = set()
        self._processes = {}
        self._cancelled = set()
        self._load_secrets()

    def _load_secrets(self):
        """Restore keys saved by `settings`; a malformed file is ignored, never deleted."""
        try:
            data = json.loads(_secrets_path().read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return
        if not isinstance(data, dict):
            return
        try:
            self._jev_key = _secret_key(data.get("jev_key"), "jev")
        except DashboardError:
            pass
        vision = data.get("vision")
        if not isinstance(vision, dict):
            return
        if vision.get("provider") in ("gemini", "openai"):
            self._vision_provider = vision["provider"]
        try:
            self._vision_model = _vision_model_name(vision["model"])
        except (KeyError, DashboardError):
            pass
        if self._vision_provider != "gemini":
            try:
                self._vision_url = _vision_endpoint(vision["url"])
            except (KeyError, DashboardError):
                pass
        try:
            key = _secret_key(vision["key"], "Khóa thị giác")
        except (KeyError, DashboardError):
            key = None
        if key:
            self._vision_key = key
            self._redactions.add(key)

    def _save_secrets(self):
        """Persist the current keys so restarting the dashboard keeps them.

        The file is plaintext, mode 0600, inside `json_folder/` (git-ignored) — same
        practical model as `json_folder/tg_api.json`, not encryption at rest.
        """
        payload = {}
        if self._jev_key:
            payload["jev_key"] = self._jev_key
        vision = {"provider": self._vision_provider, "model": self._vision_model,
                  "url": None if self._vision_provider == "gemini" else self._vision_url,
                  "key": self._vision_key}
        vision = {name: value for name, value in vision.items() if value}
        if vision:
            payload["vision"] = vision
        path = _secrets_path()
        if not payload:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise DashboardError(f"Không xoá được file cấu hình cục bộ: {exc.strerror or 'lỗi IO'}") from None
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".tmp")
            with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(temporary, path)
        except OSError as exc:
            raise DashboardError(f"Không ghi được file cấu hình cục bộ: {exc.strerror or 'lỗi IO'}") from None

    @staticmethod
    def _is_database(path):
        try:
            if path.is_symlink() or not path.is_file():
                return False
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as conn:
                return conn.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        except (OSError, sqlite3.Error):
            return False

    def databases(self):
        candidates = {}
        if DATA_DIR.is_dir() and not DATA_DIR.is_symlink():
            for path in sorted(DATA_DIR.glob("*.sqlite3"))[:200]:
                if "backup" not in path.name.lower() and path.resolve().parent == DATA_DIR.resolve() and self._is_database(path):
                    candidates[path.name] = path
        # An explicitly supplied test/custom DB is the only exception to DATA_DIR.
        if ("backup" not in self.db_path.name.lower() and self.db_path.suffix == ".sqlite3"
                and not (self.db_path.parent == DATA_DIR and DATA_DIR.is_symlink()) and self._is_database(self.db_path)):
            candidates[self.db_path.name] = self.db_path
        return candidates

    def database_path(self, name=None):
        name = self.db_path.name if not name else _text(name, "database", 160, True)
        if Path(name).name != name or "/" in name or "\\" in name or not name.endswith(".sqlite3") or "backup" in name.lower():
            raise DashboardError("Chỉ chọn tên database ứng dụng trong danh sách.", "invalid_database")
        path = self.databases().get(name)
        if path is None:
            if name == self.db_path.name and not self.db_path.exists():
                raise DashboardError("Database chưa tồn tại. Hãy khởi tạo bằng CLI hoặc chọn database đã có; dashboard không tự tạo dữ liệu.", "database_missing", 409)
            raise DashboardError("Database không hợp lệ hoặc không thuộc ứng dụng.", "invalid_database", 404)
        return path

    @contextmanager
    def read_db(self, name=None):
        path = self.database_path(name)
        conn = None
        try:
            conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            if conn.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                raise DashboardError("Database không thuộc ứng dụng.", "invalid_database", 409)
            yield conn
        except sqlite3.Error:
            raise DashboardError("Không đọc được database hoặc schema chưa được hỗ trợ. Hãy kiểm tra bằng CLI.", "database_unavailable", 409) from None
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def _tables(conn):
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    @staticmethod
    def _message_select(conn):
        tables = DashboardApp._tables(conn)
        classification = "NULL AS label,NULL AS classification_model,NULL AS classification_status,0 AS classification_stale"
        join = ""
        if "message_classifications" in tables:
            classification = "c.label,c.model AS classification_model,c.status AS classification_status,CASE WHEN c.id IS NOT NULL AND c.input_text<>m.text THEN 1 ELSE 0 END AS classification_stale"
            join = " LEFT JOIN message_classifications c ON c.id=(SELECT MAX(c2.id) FROM message_classifications c2 WHERE c2.message_row_id=m.id)"
        return """SELECT m.id,COALESCE(NULLIF(e.title,''),e.username,'') AS channel,e.username,
            substr(m.text,1,16000) AS text,m.message_id,m.posted_at,m.collected_at,
            a.score,a.level,a.category AS heuristic_category,""" + classification + """,
            m.url,e.namespace FROM messages m JOIN entities e ON e.id=m.entity_id
            LEFT JOIN analyses a ON a.message_id=m.id AND a.run_id=(SELECT MAX(a2.run_id) FROM analyses a2 WHERE a2.message_id=m.id)""" + join

    def messages(self, conn, query="", label="", level="", limit=20, offset=0, username=""):
        query = _text(query, "từ khoá", 256)
        label = _text(label, "nhãn", 80)
        level = _text(level, "mức độ", 80)
        _integer(limit, "limit", 1, 100)
        _integer(offset, "offset", 0, 10000000)
        base = self._message_select(conn)
        where, args = ["instr(m.search_text,?)>0"], [search_text(query)]
        username = _text(username, "nguồn", 256).lstrip("@")
        if username:
            where.append("(e.username=? COLLATE NOCASE OR e.title=? COLLATE NOCASE)")
            args.extend([username, username])
        if label:
            if "message_classifications" not in self._tables(conn):
                where.append("1=1" if label == "unclassified" else "1=0")
            elif label == "unclassified":
                where.append("(c.id IS NULL OR c.status!='success' OR c.input_text<>m.text)")
            elif label in ("error", "skipped"):
                where.append("c.status=?")
                args.append(label)
            else:
                where.append("c.label=? AND c.status='success' AND c.input_text=m.text")
                args.append(label)
        if level:
            where.append("a.level=?")
            args.append(level)
        sql = base + " WHERE " + " AND ".join(where)
        total = conn.execute("SELECT COUNT(*) FROM (" + sql + ")", args).fetchone()[0]
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY m.posted_at DESC,m.id DESC LIMIT ? OFFSET ?", [*args, limit, offset])]
        for row in rows:
            row["url"] = _safe_url(row["url"])
            row["classification_stale"] = bool(row["classification_stale"])
        return {"items": rows, "total": total, "limit": limit, "offset": offset}

    def message_detail(self, conn, message_id):
        row = conn.execute(self._message_select(conn) + " WHERE m.id=?", (message_id,)).fetchone()
        if row is None:
            raise DashboardError("Không tìm thấy tin nhắn.", "not_found", 404)
        message = dict(row)
        message["url"] = _safe_url(message["url"])
        message["classification_stale"] = bool(message["classification_stale"])
        entity = dict(conn.execute("SELECT e.id,e.username,e.title,e.kind,e.namespace,e.peer_type,e.telegram_id,e.first_seen,e.last_seen FROM entities e JOIN messages m ON e.id=m.entity_id WHERE m.id=?", (message_id,)).fetchone())
        analyses = [dict(r) for r in conn.execute("SELECT run_id,score,level,category,analyzer_version FROM analyses WHERE message_id=? ORDER BY run_id DESC LIMIT 100", (message_id,))]
        classifications = []
        if "message_classifications" in self._tables(conn):
            current = conn.execute("SELECT text FROM messages WHERE id=?", (message_id,)).fetchone()[0]
            for r in conn.execute("SELECT id,created_at,model,provider,status,label,error_code,substr(input_text,1,16000) AS input_text,input_sha256 FROM message_classifications WHERE message_row_id=? ORDER BY id DESC LIMIT 100", (message_id,)):
                item = dict(r)
                item["stale"] = item["input_sha256"] != hashlib.sha256(current.encode()).hexdigest()
                classifications.append(item)
        return {"message": message, "analyses": analyses, "classifications": classifications, "entity": entity}

    def sources(self, conn):
        return [dict(r) for r in conn.execute("""SELECT e.id,e.username,e.title,e.kind,COUNT(m.id) AS message_count,
            COALESCE(SUM(CASE WHEN a.score>=25 THEN 1 ELSE 0 END),0) AS flagged
            FROM entities e LEFT JOIN messages m ON m.entity_id=e.id
            LEFT JOIN analyses a ON a.message_id=m.id AND a.run_id=(SELECT MAX(a2.run_id) FROM analyses a2 WHERE a2.message_id=m.id)
            GROUP BY e.id ORDER BY message_count DESC,e.id DESC LIMIT 500""")]

    def runs(self, conn, limit=200):
        return [dict(r) for r in conn.execute("""SELECT r.id,r.created_at,r.tool,r.source,r.backend,COUNT(rm.message_id) AS total
            FROM runs r LEFT JOIN run_messages rm ON rm.run_id=r.id GROUP BY r.id ORDER BY r.id DESC LIMIT ?""", (limit,))]

    def overview(self, conn):
        tables = self._tables(conn)
        counts = {name: conn.execute("SELECT COUNT(*) FROM " + name).fetchone()[0] for name in ("messages", "entities", "runs")}
        labels = []
        if "message_classifications" in tables:
            labels = [dict(r) for r in conn.execute("""SELECT c.label,COUNT(*) AS count FROM message_classifications c JOIN messages m ON m.id=c.message_row_id
                WHERE c.status='success' AND c.input_text=m.text AND c.id=(SELECT MAX(c2.id) FROM message_classifications c2 WHERE c2.message_row_id=c.message_row_id)
                AND c.message_row_id IS NOT NULL GROUP BY label ORDER BY count DESC,label""")]
        counts["classified"] = sum(r["count"] for r in labels)
        counts["scam_indicators"] = sum(r["count"] for r in labels if r["label"] == "scam_indicators")
        counts["flagged"] = conn.execute("SELECT COUNT(*) FROM analyses a WHERE a.score>=25 AND a.run_id=(SELECT MAX(a2.run_id) FROM analyses a2 WHERE a2.message_id=a.message_id)").fetchone()[0]
        counts["image_analyses"] = conn.execute("SELECT COUNT(*) FROM image_analyses").fetchone()[0] if "image_analyses" in tables else 0
        counts["video_analyses"] = conn.execute("SELECT COUNT(*) FROM video_analyses").fetchone()[0] if "video_analyses" in tables else 0
        daily_rows = conn.execute("SELECT date(COALESCE(posted_at,collected_at)) AS day,COUNT(*) AS count FROM messages WHERE date(COALESCE(posted_at,collected_at)) IS NOT NULL GROUP BY day ORDER BY day DESC LIMIT 14").fetchall()
        daily = []
        if daily_rows:
            end = date.fromisoformat(daily_rows[0]["day"])
            start = end - timedelta(days=13)
            values = {r["day"]: r["count"] for r in daily_rows}
            daily = [{"day": (start + timedelta(days=i)).isoformat(), "count": values.get((start + timedelta(days=i)).isoformat(), 0)} for i in range(14)]
        return {"counts": counts, "labels": labels, "daily": daily, "sources": self.sources(conn),
                "recent_runs": self.runs(conn, 10), "recent_messages": self.messages(conn, limit=10)["items"]}

    def media_path(self, relative, allowed=None):
        relative = _text(relative, "đường dẫn media", 500, True)
        p = Path(relative)
        if p.is_absolute() or ".." in p.parts or "\\" in relative:
            raise DashboardError("Đường dẫn media không hợp lệ.", "invalid_media", 403)
        target = MEDIA_DIR / p
        try:
            if MEDIA_DIR.is_symlink():
                raise DashboardError("Không cho phép thư mục media là liên kết.", "invalid_media", 403)
            root = MEDIA_DIR.resolve()
            if not target.resolve().is_relative_to(root) or any(x.is_symlink() for x in [target, *target.parents] if x != root and x.is_relative_to(MEDIA_DIR)):
                raise DashboardError("Không cho phép media liên kết ra ngoài.", "invalid_media", 403)
            if not target.is_file() or target.suffix.lower() not in (allowed or MEDIA_TYPES):
                raise DashboardError("Không tìm thấy media được phép.", "not_found", 404)
        except OSError:
            raise DashboardError("Không đọc được media.", "not_found", 404) from None
        return target

    def _media_ref(self, raw, allowed=None):
        if not isinstance(raw, str):
            return None
        try:
            path = Path(raw)
            if path.is_absolute():
                raw = path.relative_to(MEDIA_DIR).as_posix()
            elif raw.startswith("media/"):
                raw = raw[6:]
            target = self.media_path(raw, allowed)
            return "/api/media?path=" + quote(target.relative_to(MEDIA_DIR).as_posix(), safe="")
        except (ValueError, DashboardError):
            return None

    def profiles(self, conn, query=""):
        query = _text(query, "từ khoá", 256).lower()
        rows = conn.execute("""SELECT p.profile_json,e.username,e.title,e.kind,e.namespace FROM profile_snapshots p
            JOIN entities e ON p.entity_id=e.id WHERE p.run_id=(SELECT MAX(p2.run_id) FROM profile_snapshots p2 WHERE p2.entity_id=p.entity_id)
            AND (instr(lower(COALESCE(e.username,'')),?)>0 OR instr(lower(e.title),?)>0) ORDER BY p.run_id DESC LIMIT 200""", (query, query))
        items = []
        for r in rows:
            raw = _json(r["profile_json"], {})
            if not isinstance(raw, dict):
                raw = {}
            item = {k: r[k] for k in ("username", "title", "kind", "namespace")}
            for key in ("phone", "has_photo"):
                if key in raw:
                    item[key] = _public_value(raw[key])
            item["bio"] = _public_value(raw.get("bio") or raw.get("about") or "")
            item["participants_count"] = _public_value(raw.get("participants_count") if raw.get("participants_count") is not None else raw.get("members") if raw.get("members") is not None else raw.get("subscribers"))
            item["common_groups"] = [{k: _public_value(g[k]) for k in ("id", "username", "title", "kind") if k in g}
                                     for g in raw.get("common_groups", [])[:100] if isinstance(g, dict)] if isinstance(raw.get("common_groups", []), list) else []
            item["photo_url"] = self._media_ref(raw.get("photo_path"), IMAGE_TYPES)
            items.append(item)
        return {"items": items, "total": len(items)}

    def _media_files(self, allowed):
        files = []
        if MEDIA_DIR.is_dir() and not MEDIA_DIR.is_symlink():
            # At most 200 examined entries, including directories/non-media files;
            # unlike os.walk/sorted(glob), this never materializes an unbounded directory.
            pending, inspected = [MEDIA_DIR], 0
            while pending and inspected < 200:
                directory = pending.pop()
                try:
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            if inspected >= 200:
                                break
                            inspected += 1
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                pending.append(Path(entry.path))
                                continue
                            p = Path(entry.path)
                            if p.suffix.lower() not in allowed:
                                continue
                            try:
                                relative = p.relative_to(MEDIA_DIR).as_posix()
                                valid = self.media_path(relative, allowed)
                                st = valid.stat()
                                files.append({"path": relative, "name": p.name, "size": st.st_size, "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()})
                            except (OSError, DashboardError):
                                continue
                except OSError:
                    continue
        return sorted(files, key=lambda item: item["path"])

    def search_results(self, conn, limit=100):
        """Nguồn tìm được theo từ khoá, đọc từ bảng search_results của storage."""
        limit = max(1, min(int(limit), 200))
        if "search_results" not in self._tables(conn):
            return {"items": [], "total": 0}
        rows = conn.execute(
            "SELECT s.run_id, e.username, e.title, e.peer_type, e.telegram_id, s.keywords_json "
            "FROM search_results s JOIN entities e ON e.id = s.entity_id "
            "ORDER BY s.run_id DESC, e.username LIMIT ?", (limit,)).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM search_results").fetchone()[0]
        items = []
        for row in rows:
            username = row["username"] or ""
            items.append({"run_id": row["run_id"], "username": username,
                          "title": row["title"] or username,
                          "kind": row["peer_type"] or "unknown",
                          "telegram_id": row["telegram_id"],
                          "keywords": _json(row["keywords_json"], [])})
        return {"items": items, "total": total}

    def images(self, conn):
        analyses, classifications = [], []
        tables = self._tables(conn)
        if "image_analyses" in tables:
            for row in conn.execute("SELECT id,status,source_path,image_sha256,created_at,error_code,media_json,ocr_json,vision_json,evidence_json FROM image_analyses ORDER BY id DESC LIMIT 20"):
                raw = dict(row)
                source_ref = self._media_ref(raw["source_path"], IMAGE_TYPES)
                item = {key: raw[key] for key in ("id", "status", "created_at", "error_code")}
                item.update(source_path=unquote(source_ref.split("path=", 1)[1]) if source_ref else None,
                            source_url=source_ref, source_sha256=raw["image_sha256"],
                            media=_public_value(_json(raw["media_json"], {})),
                            ocr=_public_value(_json(raw["ocr_json"], {})),
                            vision=_public_value(_json(raw["vision_json"], {})),
                            evidence=_public_value(_json(raw["evidence_json"], {})))
                analyses.append(item)
        if "image_classifications" in tables:
            classifications = [dict(row) for row in conn.execute("SELECT id,image_analysis_id,status,label,model,error_code FROM image_classifications ORDER BY id DESC LIMIT 100")]
        return {"files": self._media_files(IMAGE_OCR_TYPES), "analyses": analyses, "classifications": classifications}

    def videos(self, conn):
        files = self._media_files(VIDEO_TYPES)
        tables = self._tables(conn)
        analyses, classifications = [], []
        if "video_analyses" in tables:
            for row in conn.execute("SELECT id,status,source_path,video_sha256,created_at,error_code,transcript_json,vision_json,evidence_json,media_json FROM video_analyses ORDER BY id DESC LIMIT 20"):
                raw = dict(row)
                media = _json(raw["media_json"], {})
                evidence = _json(raw["evidence_json"], {})
                source_ref = self._media_ref(raw["source_path"], VIDEO_TYPES)
                source_path = unquote(source_ref.split("path=", 1)[1]) if source_ref else None
                item = {k: raw[k] for k in ("id", "status", "created_at", "error_code")}
                item.update(source_path=source_path, source_sha256=raw["video_sha256"], transcript=_public_value(_json(raw["transcript_json"], {})),
                            vision=_public_value(_json(raw["vision_json"], {})), evidence=_public_value(evidence),
                            sampling=_public_value(evidence.get("sampling")) if isinstance(evidence, dict) else None, frames=[])
                item["local_ocr"] = _public_value(media.get("local_ocr")) if isinstance(media, dict) else None
                if isinstance(media, dict) and isinstance(media.get("frames"), list):
                    for frame in media["frames"][:60]:
                        if isinstance(frame, dict):
                            url = self._media_ref(frame.get("path"), IMAGE_TYPES)
                            if url:
                                item["frames"].append({"url": url, "timestamp": frame.get("timestamp")})
                analyses.append(item)
        if "video_classifications" in tables:
            classifications = [dict(r) for r in conn.execute("SELECT id,video_analysis_id,status,label,model,error_code FROM video_classifications ORDER BY id DESC LIMIT 100")]
        return {"files": files, "analyses": analyses, "classifications": classifications}

    def _vision(self):
        """Merged, non-secret view of vision configuration; the key stays in memory."""
        provider = self._vision_provider or os.environ.get("VISION_PROVIDER") or "gemini"
        model = self._vision_model or os.environ.get("VISION_MODEL")
        url = self._vision_url or os.environ.get("VISION_API_URL")
        key = self._vision_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("VISION_API_KEY")
        remote = provider == "gemini" or bool(url and not _is_loopback_host(urlsplit(url).hostname))
        if provider == "gemini":
            ready = bool(model) and bool(key)
        else:
            ready = bool(model and url) and (bool(key) or not remote)
        return {"provider": provider, "model": model, "url": url, "key": key, "remote": remote, "ready": ready}

    def _vision_argv(self, consent):
        """Vision flags for a child job, or a clear refusal; never a silent downgrade."""
        state = self._vision()
        if not state["ready"]:
            raise DashboardError(
                "Chưa cấu hình thị giác. Mở Cài đặt để nhập key Gemini và tên model, "
                "hoặc chọn bỏ qua thị giác (kết quả sẽ thiếu một phần).", "configuration", 409)
        if state["remote"] and not consent:
            raise DashboardError(
                "Mô hình thị giác từ xa sẽ gửi ảnh/khung hình ra ngoài và có thể tính phí. "
                "Cần tự xác nhận đồng ý trước khi chạy.", "consent_required", 409)
        args = ["--vision", "--vision-provider", state["provider"], "--vision-model", state["model"]]
        if state["url"]:
            args += ["--vision-url", state["url"]]
        if state["remote"]:
            args.append("--allow-remote")
        return args

    def config(self):
        sessions = []
        if SESSIONS_DIR.is_dir():
            sessions = sorted(p.stem for p in SESSIONS_DIR.glob("*.session") if p.is_file() and not p.is_symlink() and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", p.stem))[:100]
        whisper = importlib.util.find_spec("faster_whisper") is not None
        if not whisper:
            whisper = any((PROJECT_ROOT / ".venv-video" / "lib").glob("python*/site-packages/faster_whisper/__init__.py"))
        vision = self._vision()
        api_id, _ = _api_credentials()
        return {"jev_configured": bool(self._jev_key or os.environ.get("TYPESAFE_API_KEY")),
                "vision_configured": vision["ready"],
                "vision_provider": vision["provider"],
                "vision_model": vision["model"],
                "vision_remote": vision["remote"],
                "vision_key_set": bool(vision["key"]),
                "vision_endpoint_set": bool(vision["url"]),
                "whisper_available": whisper, "tesseract_available": bool(shutil.which("tesseract")), "sessions": sessions,
                "api_configured": api_id is not None, "api_id": api_id}

    def bootstrap(self, token):
        return {"token": token, "databases": [{"name": name, "label": name} for name in self.databases()],
                "active_database": self.db_path.name, "config": self.config(), "version": "1.0", "jobs": self.jobs()}

    def settings(self, data):
        if not isinstance(data, dict) or not data:
            raise DashboardError("Thiếu cấu hình cần lưu.")
        if set(data) & {"api_id", "api_hash"}:
            if set(data) != {"api_id", "api_hash"}:
                raise DashboardError("Gửi riêng api_id và api_hash trong một lần.")
            try:
                identifier = int(data["api_id"])
            except (TypeError, ValueError):
                raise DashboardError("api_id phải là số nguyên Telegram cấp cho ứng dụng.") from None
            if not 1 <= identifier <= 2_147_483_647:
                raise DashboardError("api_id nằm ngoài khoảng Telegram cho phép.")
            digest = str(data["api_hash"]).strip().lower()
            if not re.fullmatch(r"[0-9a-f]{32}", digest):
                raise DashboardError("api_hash phải là 32 ký tự hex (0-9, a-f).")
            merged = {}
            path = _api_config_path()
            try:
                if path.is_file() and not path.is_symlink():
                    existing = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(existing, dict):
                        merged = {k: v for k, v in existing.items() if isinstance(k, str)}
            except (OSError, ValueError):
                merged = {}
            merged.update({"api_id": identifier, "api_hash": digest})
            _write_private_json(path, merged)
            self._redactions.add(digest)
            return {"config": self.config()}
        jev = set(data) & {"jev_key", "clear_jev"}
        vision_fields = set(data) & {"vision_key", "vision_model", "vision_provider", "vision_url", "clear_vision"}
        if jev and vision_fields:
            raise DashboardError("Gửi riêng cấu hình Jev hoặc thị giác trong mỗi lần.")
        if jev:
            if set(data) - {"jev_key", "clear_jev"} or len(data) != 1:
                raise DashboardError("Chỉ gửi jev_key hoặc clear_jev.")
            with self._lock:
                if "jev_key" in data:
                    key = _secret_key(data["jev_key"], "Jev key")
                    if len(self._redactions) >= 40:
                        raise DashboardError("Đã thay key nhiều lần; hãy khởi động lại dashboard.", "settings_limit", 409)
                    self._jev_key = key
                    self._redactions.add(key)
                elif data["clear_jev"] is True:
                    self._jev_key = None
                else:
                    raise DashboardError("clear_jev phải là true.")
                self._save_secrets()
            return {"config": self.config()}
        if set(data) - vision_fields:
            raise DashboardError("Chỉ nhận vision_key, vision_model, vision_provider, vision_url hoặc clear_vision.")
        if "clear_vision" in data:
            if set(data) != {"clear_vision"} or data["clear_vision"] is not True:
                raise DashboardError("clear_vision phải là true và gửi riêng.")
            with self._lock:
                self._vision_key = None
                self._vision_model = None
                self._vision_provider = None
                self._vision_url = None
                self._save_secrets()
            return {"config": self.config()}
        # Validate the whole prospective configuration before changing anything.
        with self._lock:
            provider = _text(data["vision_provider"], "vision provider", 20, True) if "vision_provider" in data else self._vision_provider
            provider = provider or (os.environ.get("VISION_PROVIDER") or "gemini")
            if provider not in ("gemini", "openai"):
                raise DashboardError("vision_provider chỉ nhận gemini hoặc openai.")
            model = _vision_model_name(data["vision_model"]) if "vision_model" in data else self._vision_model
            model = model or os.environ.get("VISION_MODEL")
            url = _vision_endpoint(data["vision_url"]) if "vision_url" in data else self._vision_url
            url = url or os.environ.get("VISION_API_URL")
            key = _secret_key(data["vision_key"], "Khóa thị giác") if "vision_key" in data else None
            key = key or self._vision_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("VISION_API_KEY")
            if provider == "gemini":
                # Gemini uses its official endpoint; never keep a stale OpenAI-compatible URL.
                url = None
            if provider == "openai" and not url:
                raise DashboardError("Với provider openai cần URL endpoint (loopback cho Qwen-VL cục bộ, hoặc HTTPS từ xa).")
            remote = provider == "gemini" or bool(url and not _is_loopback_host(urlsplit(url).hostname))
            ready = (bool(model) and bool(key)) if provider == "gemini" else bool(model and url) and (bool(key) or not remote)
            if not ready:
                raise DashboardError(
                    "Cần đủ model và API key" + ("" if provider == "gemini" else " (endpoint từ xa cũng cần key)")
                    + " để bật thị giác; nếu chưa có, hãy để trống và chọn bỏ qua thị giác.",
                    "configuration", 409)
            if key and len(self._redactions) >= 40:
                raise DashboardError("Đã thay key nhiều lần; hãy khởi động lại dashboard.", "settings_limit", 409)
            if "vision_provider" in data:
                self._vision_provider = provider
            if "vision_model" in data:
                self._vision_model = model
            self._vision_url = url
            if key:
                self._vision_key = key
                self._redactions.add(key)
            self._save_secrets()
        return {"config": self.config()}

    def _session(self, raw):
        raw = _text(raw, "session", 100)
        name = raw or "tg_session"
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", name) or name not in self.config()["sessions"]:
            raise DashboardError("Cần chọn phiên Telegram đã đăng nhập bằng CLI; dashboard không đăng nhập tự động.", "session_required", 409)
        if not (name == "tg_session" or name.startswith("tg_session_")):
            # Existing CLI prepends tg_session_ to arbitrary names; never select a different file silently.
            raise DashboardError("Tên phiên chưa được CLI hỗ trợ trực tiếp; chọn phiên tg_session*.", "session_required", 409)
        return name

    @staticmethod
    def _names(raw, field="channels"):
        text = _text(raw, field, 2000, True)
        names = [x.strip().lstrip("@") for x in re.split(r"[,\n]", text) if x.strip()]
        if not 1 <= len(names) <= 20 or any(not (re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", n)
                                                  or re.fullmatch(r"-?\d{1,19}", n)) for n in names):
            raise DashboardError(f"{field}: nhập tối đa 20 username hoặc ID số, phân cách dấu phẩy hoặc xuống dòng.")
        return list(dict.fromkeys(names))

    def build_job(self, kind, database, params, job_id):
        if not isinstance(params, dict):
            raise DashboardError("params phải là object.")
        supported = {
            "collect": {"backend", "channels", "limit", "session", "jev", "jev_limit", "demo"},
            "watch": {"channels", "session", "jev", "jev_limit", "interval", "cycles", "once"},
            "classify": {"limit", "query", "username", "include_demo", "recheck"},
            "lookup": {"username", "limit", "backend", "session"},
            "search": {"keyword", "limit", "backend", "session"},
            "download": {"url", "session"},
            "video_analyze": {"path", "skip_vision", "language", "model", "max_frames", "ocr", "ocr_language", "allow_remote"},
            "image_analyze": {"path", "ocr_language", "vision", "allow_remote"},
            "image_classify": {"image_id", "allow_partial", "recheck"},
            "video_classify": {"analysis_id", "allow_partial", "recheck"},
            "db_backup": set(),
        }
        if kind not in supported:
            raise DashboardError("Tác vụ chưa được dashboard hỗ trợ; không có thao tác tham gia nhóm.", "unsupported")
        if set(params) - supported[kind]:
            raise DashboardError("params chứa tuỳ chọn không được hỗ trợ.")
        db = self.database_path(database)
        p = dict(params)
        argv, interpreter = [], sys.executable
        output = str(JSON_DIR / f"dashboard_{job_id}.json")
        dbflag = ["--db", str(db)]
        if kind in ("collect", "watch", "lookup", "search"):
            backend = p.setdefault("backend", "api" if kind == "watch" else "web")
            if backend not in ("api", "web"):
                raise DashboardError("backend chỉ nhận web hoặc api.")
            if kind == "search" and backend == "web":
                raise DashboardError("CLI tìm kiếm không có chế độ web không kèm kênh; hãy chọn API với phiên đã có.", "unsupported")
            demo = _boolean(p.setdefault("demo", False), "demo") if kind == "collect" else False
            if backend == "api" and not demo:
                p["session"] = self._session(p.get("session", ""))
                candidate = PROJECT_ROOT / ".venv-api" / "bin" / "python"
                if not candidate.is_file():
                    raise DashboardError("Thiếu Python .venv-api; cấu hình môi trường bằng CLI trước.", "dependency", 409)
                interpreter = str(candidate)
            elif p.get("session"):
                _text(p["session"], "session", 100)
            if kind in ("collect", "watch"):
                p["channels"] = _text(p.get("channels", ""), "channels", 2000, not demo)
                names = self._names(p["channels"]) if p["channels"] else []
                argv = ["api" if backend == "api" else "web", *dbflag, "--out", output, "--no-photo", "--no-report"]
                for name in names:
                    argv += ["--watch" if kind == "watch" else "--channel", name]
                if kind == "collect":
                    p["limit"] = _integer(p.get("limit", 20), "limit", 1, 1000)
                    argv += ["--limit", str(p["limit"])]
                    if demo:
                        argv += ["--demo"]
                else:
                    p["interval"] = _integer(p.get("interval", 60), "interval", 10, 3600)
                    p["cycles"] = _integer(p.get("cycles", 100), "cycles", 1, 1000)
                    p["once"] = _boolean(p.get("once", True), "once")
                    if not p["once"]:
                        raise DashboardError("Dashboard chỉ hỗ trợ theo dõi có thời hạn; once phải là true.", "unsupported")
                    argv += ["--interval", str(p["interval"]), "--cycles", str(p["cycles"]), "--watch-once"]
                p["jev"] = _boolean(p.get("jev", False), "jev")
                p["jev_limit"] = _integer(p.get("jev_limit", 10), "jev_limit", 1, 1000)
                if p["jev"]:
                    self._require_jev()
                    argv += ["--jev", "--jev-limit", str(p["jev_limit"])]
                    if demo:
                        argv += ["--jev-include-demo"]
            elif kind == "lookup":
                names = self._names(p.get("username", ""), "username")
                p["limit"] = _integer(p.get("limit", 0), "limit", 0, 100)
                if backend != "api" and any(re.fullmatch(r"-?\d{1,19}", n) for n in names):
                    raise DashboardError("ID số chỉ tra được bằng Telegram API; chọn backend API cho mục này.",
                                         "unsupported")
                argv = ["profile", *names, *dbflag, "--limit", str(p["limit"]), "--no-photo", "--out", output]
                if backend == "api":
                    argv += ["--api"]
            else:
                keyword = _text(p.get("keyword", ""), "keyword", 256, True)
                p["limit"] = _integer(p.get("limit", 20), "limit", 1, 100)
                argv = ["search", *dbflag, "--api", "--keyword", keyword, "--limit", str(p["limit"]), "--api-search-limit", str(p["limit"]), "--out", output]
            if backend == "api" and not demo:
                argv += ["--session", p["session"]]
        elif kind == "classify":
            self._require_jev()
            p["limit"] = _integer(p.get("limit", 10), "limit", 1, 100)
            argv = ["classify", *dbflag, "run", "--limit", str(p["limit"])]
            for key, flag in (("query", "--keyword"), ("username", "--username")):
                p[key] = _text(p.get(key, ""), key, 256)
                if p[key]:
                    argv += [flag, p[key]]
            for key in ("include_demo", "recheck"):
                p[key] = _boolean(p.get(key, False), key)
                if p[key]:
                    argv += ["--" + key.replace("_", "-")]
        elif kind == "download":
            from .cli.telegram_download import parse_message_link, DownloadError
            p["url"] = _text(p.get("url", ""), "url", 2048, True)
            try:
                parse_message_link(p["url"])
            except DownloadError:
                raise DashboardError("Cần link đúng một bài Telegram, không phải link mời/hồ sơ.") from None
            p["session"] = self._session(p.get("session", ""))
            interpreter = str(PROJECT_ROOT / ".venv-api" / "bin" / "python")
            if not Path(interpreter).is_file():
                raise DashboardError("Thiếu Python .venv-api.", "dependency", 409)
            argv = ["download", p["url"], "--session", p["session"], "--output-dir", str(MEDIA_DIR / "telegram_downloads")]
        elif kind == "image_analyze":
            path = self.media_path(p.get("path", ""), IMAGE_OCR_TYPES)
            p["ocr_language"] = self._ocr_language(p.get("ocr_language", "vie+eng"))
            p["vision"] = _boolean(p.get("vision", False), "vision")
            p["allow_remote"] = _boolean(p.get("allow_remote", False), "allow_remote")
            argv = ["image", *dbflag, "analyze", str(path), "--ocr-language", p["ocr_language"], "--timeout", "30"]
            if p["vision"]:
                argv += self._vision_argv(p["allow_remote"])
        elif kind == "image_classify":
            self._require_jev()
            p["image_id"] = _integer(p.get("image_id"), "image_id", 1, 2147483647)
            with self.read_db(db.name) as conn:
                if "image_analyses" not in self._tables(conn) or not conn.execute("SELECT 1 FROM image_analyses WHERE id=?", (p["image_id"],)).fetchone():
                    raise DashboardError("Không tìm thấy phân tích ảnh.", "not_found", 404)
            argv = ["image", *dbflag, "classify", str(p["image_id"])]
            for key in ("allow_partial", "recheck"):
                p[key] = _boolean(p.get(key, False), key)
                if p[key]:
                    argv += ["--" + key.replace("_", "-")]
        elif kind == "video_analyze":
            path = self.media_path(p.get("path", ""), VIDEO_TYPES)
            p["language"] = p.get("language", "vi")
            p["model"] = p.get("model", "tiny")
            if p["language"] not in ("vi", "en", "auto") or p["model"] not in ("tiny", "base"):
                raise DashboardError("Chỉ hỗ trợ ngôn ngữ vi/en/auto và Whisper tiny/base.")
            p["max_frames"] = _integer(p.get("max_frames", 6), "max_frames", 1, 12)
            p["allow_remote"] = _boolean(p.get("allow_remote", False), "allow_remote")
            p["skip_vision"] = _boolean(p.get("skip_vision", not self.config()["vision_configured"]), "skip_vision")
            candidate = PROJECT_ROOT / ".venv-video" / "bin" / "python"
            if not candidate.is_file():
                raise DashboardError("Thiếu Python .venv-video.", "dependency", 409)
            interpreter = str(candidate)
            argv = ["video", *dbflag, "analyze", str(path), "--whisper-model", p["model"], "--language", p["language"], "--max-frames", str(p["max_frames"])]
            if p["skip_vision"]:
                argv += ["--skip-vision"]
            else:
                argv += self._vision_argv(p["allow_remote"])
            p["ocr"] = _boolean(p.get("ocr", True), "ocr")
            p["ocr_language"] = self._ocr_language(p.get("ocr_language", "vie+eng"))
            if p["ocr"]:
                argv += ["--ocr", "--ocr-language", p["ocr_language"]]
            # No --allow-model-download: whisper weights are never fetched implicitly.
        elif kind == "video_classify":
            self._require_jev()
            p["analysis_id"] = _integer(p.get("analysis_id"), "analysis_id", 1, 2147483647)
            with self.read_db(db.name) as conn:
                if "video_analyses" not in self._tables(conn) or not conn.execute("SELECT 1 FROM video_analyses WHERE id=?", (p["analysis_id"],)).fetchone():
                    raise DashboardError("Không tìm thấy phân tích video.", "not_found", 404)
            argv = ["video", *dbflag, "classify", str(p["analysis_id"])]
            for key in ("allow_partial", "recheck"):
                p[key] = _boolean(p.get(key, False), key)
                if p[key]:
                    argv += ["--" + key.replace("_", "-")]
        if kind in ("collect", "watch") and p["backend"] == "api":
            argv += ["--non-interactive"]
        return ([interpreter, str(PROJECT_ROOT / "main.py"), *argv] if kind != "db_backup" else None), p, db

    @staticmethod
    def _ocr_language(value):
        if value not in ("vie+eng", "eng", "vie"):
            raise DashboardError("OCR chỉ hỗ trợ vie+eng, eng hoặc vie.")
        return value

    def _require_jev(self):
        if not self.config()["jev_configured"]:
            raise DashboardError("Chưa cấu hình Jev key. Thêm key trong bộ nhớ hoặc môi trường trước khi gửi tác vụ.", "jev_required", 409)

    def _job_view(self, job):
        return {k: (list(v) if k == "logs" else dict(v) if k == "params" else v) for k, v in job.items() if not k.startswith("_")}

    def jobs(self):
        with self._lock:
            return [self._job_view(job) for job in reversed(list(self._jobs.values()))]

    def job(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise DashboardError("Không tìm thấy tác vụ.", "not_found", 404)
            return self._job_view(job)

    def submit(self, data):
        if not isinstance(data, dict) or set(data) - {"kind", "db", "params"}:
            raise DashboardError("Tác vụ phải gồm kind, db và params.")
        kind = _text(data.get("kind", ""), "kind", 40, True)
        job_id = uuid.uuid4().hex
        argv, params, db = self.build_job(kind, data.get("db"), data.get("params", {}), job_id)
        job = {"id": job_id, "kind": kind, "status": "queued", "created_at": _now(), "started_at": None,
               "ended_at": None, "exit_code": None, "params": {"db": db.name, **params}, "logs": deque(maxlen=100),
               "_argv": argv, "_db": db, "_drained": False}
        with self._lock:
            if self._closed:
                raise DashboardError("Dashboard đang đóng.", "closed", 409)
            if len(self._jobs) >= 40:
                completed = next((key for key, value in self._jobs.items() if value["status"] in TERMINAL and value["_drained"]), None)
                if completed is None:
                    raise DashboardError("Đã đạt giới hạn 40 tác vụ.", "queue_full", 409)
                del self._jobs[completed]
                self._cancelled.discard(completed)
            if self._queue.full():
                raise DashboardError("Hàng đợi đầy, hãy chờ tác vụ hiện tại.", "queue_full", 409)
            self._jobs[job_id] = job
            self._queue.put_nowait(job_id)
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work, name="dashboard-jobs", daemon=True)
                self._worker.start()
            return self._job_view(job)

    def _sanitize(self, line):
        line = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line)
        line = "".join(c for c in line if c == "\t" or 32 <= ord(c) != 127)
        with self._lock:
            values = set(self._redactions)
        values.update(v for k, v in os.environ.items() if v and re.search(r"KEY|TOKEN|SECRET|PASSWORD|API_HASH", k, re.I))
        for value in sorted(values, key=len, reverse=True):
            line = line.replace(value, "[đã ẩn]")
        line = re.sub(r"(?i)((?:api[_ -]?(?:key|hash|id)|auth[_ -]?key|bot[_ -]?token|authorization|password|secret)\s*[:=]\s*)\S+", r"\1[đã ẩn]", line)
        return line[:4000]

    def _log(self, job, line):
        line = self._sanitize(line)
        if line:
            with self._lock:
                job["logs"].append(line)

    def _backup(self, job):
        # The backup exception is explicit and runs only after a CSRF-protected submission.
        directory = DATA_DIR
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = directory / ("dashboard-backup_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + job["id"][:8] + ".sqlite3")
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with self.read_db(job["_db"].name) as source:
            with closing(sqlite3.connect(target)) as destination:
                source.backup(destination, pages=100, progress=lambda *_: self._backup_progress(job))
        self._log(job, "Đã tạo bản sao SQLite: " + target.name)
        return 0

    def _backup_progress(self, job):
        if job["id"] in self._cancelled:
            raise DashboardError("Đã huỷ sao lưu.", "cancelled", 409)

    def _work(self):
        while True:
            try:
                job_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                if self._closed:
                    return
                continue
            job = self._jobs[job_id]
            process = None
            try:
                with self._lock:
                    if job_id in self._cancelled:
                        continue
                    job["status"], job["started_at"] = "running", _now()
                if job["kind"] == "db_backup":
                    code = self._backup(job)
                else:
                    env = os.environ.copy()
                    with self._lock:
                        if self._jev_key:
                            env["TYPESAFE_API_KEY"] = self._jev_key
                        vision = self._vision()
                    if vision["key"]:
                        # Keys travel only in the child environment, never in argv or logs.
                        env["GEMINI_API_KEY" if vision["provider"] == "gemini" else "VISION_API_KEY"] = vision["key"]
                    env["PYTHONUNBUFFERED"] = "1"
                    process = subprocess.Popen(job["_argv"], cwd=str(PROJECT_ROOT), env=env, stdin=subprocess.DEVNULL,
                                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
                    with self._lock:
                        self._processes[job_id] = process
                        cancelled = job_id in self._cancelled
                    if cancelled:
                        self._signal_process(process, signal.SIGINT)
                        threading.Thread(target=self._finish_cancel, args=(job_id, process), daemon=True).start()
                    pending, discarding = b"", False
                    while True:
                        chunk = process.stdout.read1(4096)
                        if not chunk:
                            break
                        pending += chunk
                        while b"\n" in pending:
                            line, pending = pending.split(b"\n", 1)
                            if not discarding and len(line) <= 8192:
                                self._log(job, line.decode("utf-8", "replace"))
                            else:
                                self._log(job, "[Dòng log quá dài đã được ẩn]")
                            discarding = False
                        if len(pending) > 8192:
                            pending, discarding = b"", True
                    if pending and not discarding:
                        self._log(job, pending.decode("utf-8", "replace"))
                    code = process.wait()
                with self._lock:
                    job["exit_code"] = code
                    job["status"] = "cancelled" if job_id in self._cancelled else "succeeded" if code == 0 else "failed"
            except Exception:
                self._log(job, "Không hoàn tất tác vụ; kiểm tra môi trường/phiên bằng CLI. Chi tiết riêng tư đã được ẩn.")
                with self._lock:
                    job["status"] = "cancelled" if job_id in self._cancelled else "failed"
            finally:
                try:
                    if process is not None:
                        try:
                            if process.poll() is None:
                                self._signal_process(process, signal.SIGTERM)
                                try:
                                    process.wait(timeout=2)
                                except subprocess.TimeoutExpired:
                                    self._signal_process(process, signal.SIGKILL)
                                    process.wait(timeout=2)
                        except Exception:
                            pass
                        finally:
                            if process.stdout:
                                try:
                                    process.stdout.close()
                                except Exception:
                                    pass
                finally:
                    # Queue accounting must survive cleanup failure as well as CLI failure.
                    with self._lock:
                        self._processes.pop(job_id, None)
                        job["ended_at"] = _now()
                        job["_drained"] = True
                    self._queue.task_done()

    @staticmethod
    def _signal_process(process, sig):
        try:
            if process.poll() is None:
                # start_new_session makes the child PID its own owned process group.
                os.killpg(process.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def _finish_cancel(self, job_id, process):
        for sig, timeout in ((None, 3), (signal.SIGTERM, 2), (signal.SIGKILL, 2)):
            with self._lock:
                if self._processes.get(job_id) is not process:
                    return
            if sig is not None:
                self._signal_process(process, sig)
            try:
                process.wait(timeout=timeout)
                return
            except subprocess.TimeoutExpired:
                continue

    def cancel(self, job_id):
        with self._lock:
            if job_id not in self._jobs:
                raise DashboardError("Không tìm thấy tác vụ.", "not_found", 404)
            job = self._jobs[job_id]
            if job["status"] in TERMINAL:
                return self._job_view(job)
            self._cancelled.add(job_id)
            process = self._processes.get(job_id)
            job["status"] = "cancelling" if job["started_at"] else "cancelled"
            if not job["started_at"]:
                job["ended_at"] = _now()
            if process is not None:
                self._signal_process(process, signal.SIGINT)
                threading.Thread(target=self._finish_cancel, args=(job_id, process), daemon=True).start()
            return self._job_view(job)

    def close(self):
        with self._lock:
            self._closed = True
            ids = [j["id"] for j in self._jobs.values() if j["status"] not in TERMINAL]
        for job_id in ids:
            self.cancel(job_id)
        if self._worker is not None:
            self._worker.join(timeout=8)
        with self._lock:
            self._jev_key = None
            self._vision_key = None
            self._vision_model = None
            self._vision_provider = None
            self._vision_url = None
            self._redactions.clear()

    def create_server(self, port=8765):
        _integer(port, "port", 0, 65535)
        app = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "Dashboard/1.0"
            sys_version = ""

            def log_message(self, *_):
                pass  # Do not log requests, tokens, URLs, or submitted values.

            def _headers(self, status, mime, length, extra=None):
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", CSP)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()

            def _reply(self, data, status=200):
                raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
                self._headers(status, "application/json; charset=utf-8", len(raw))
                if self.command != "HEAD":
                    self.wfile.write(raw)

            def _guard(self, mutation=False):
                hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
                values = self.headers.get_all("Host", [])
                if len(values) != 1 or values[0].lower() not in hosts:
                    raise DashboardError("Host không thuộc dashboard loopback.", "forbidden_host", 403)
                if self.path.startswith("/api"):
                    origins = self.headers.get_all("Origin", [])
                    if len(origins) > 1 or origins and origins[0] not in {"http://" + host for host in hosts}:
                        raise DashboardError("Origin không được phép.", "forbidden_origin", 403)
                    if self.headers.get("Sec-Fetch-Site") == "cross-site":
                        raise DashboardError("Không cho phép yêu cầu cross-site.", "forbidden_origin", 403)
                    if mutation:
                        tokens = self.headers.get_all("X-Dashboard-Token", [])
                        if len(tokens) != 1 or not secrets.compare_digest(tokens[0], self.server.dashboard_token):
                            raise DashboardError("Thiếu token dashboard hợp lệ.", "csrf", 403)

            def _query(self):
                if len(self.path) > 4096:
                    raise DashboardError("URL quá dài.")
                parsed = urlsplit(self.path)
                if parsed.scheme or parsed.netloc:
                    raise DashboardError("Chỉ nhận đường dẫn cục bộ.", "invalid_path", 403)
                query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=20)
                if any(len(v) != 1 for v in query.values()):
                    raise DashboardError("Tham số bị lặp.")
                return parsed.path, {k: v[0] for k, v in query.items()}

            def _media(self, query):
                if set(query) != {"path"}:
                    raise DashboardError("Cần path media tương đối.")
                path = app.media_path(query["path"])
                with path.open("rb") as stream:
                    size = os.fstat(stream.fileno()).st_size
                    start, end, status, extra = 0, size - 1, 200, {}
                    if path.suffix.lower() in VIDEO_TYPES:
                        extra["Accept-Ranges"] = "bytes"
                        requested = self.headers.get("Range")
                        if requested:
                            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
                            if not match or not any(match.groups()) or len(requested) > 80:
                                raise DashboardError("Range video không hợp lệ.", "invalid_range", 400)
                            left, right = match.groups()
                            if not left:
                                length = int(right)
                                start = max(0, size - length)
                            else:
                                start = int(left)
                                end = min(int(right), size - 1) if right else size - 1
                            if start > end or start >= size or (not left and int(right) == 0):
                                self._headers(416, "application/json", 0, {"Content-Range": f"bytes */{size}"})
                                return
                            status = 206
                            extra["Content-Range"] = f"bytes {start}-{end}/{size}"
                    length = max(0, end - start + 1)
                    self._headers(status, MEDIA_TYPES[path.suffix.lower()], length, extra)
                    if self.command != "HEAD":
                        stream.seek(start)
                        remaining = length
                        while remaining:
                            chunk = stream.read(min(65536, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)

            def _get(self):
                self._guard()
                route, q = self._query()
                if route == "/api/bootstrap":
                    self._reply(app.bootstrap(self.server.dashboard_token))
                elif route == "/api/jobs":
                    self._reply({"items": app.jobs()})
                elif re.fullmatch(r"/api/jobs/[a-f0-9]{32}", route):
                    self._reply(app.job(route.rsplit("/", 1)[1]))
                elif route == "/api/media":
                    self._media(q)
                elif route.startswith("/api/"):
                    with app.read_db(q.pop("db", None)) as conn:
                        if route == "/api/overview":
                            result = app.overview(conn)
                        elif route == "/api/messages":
                            try:
                                limit, offset = int(q.get("limit", "20")), int(q.get("offset", "0"))
                            except ValueError:
                                raise DashboardError("limit/offset phải là số nguyên.") from None
                            result = app.messages(conn, q.get("query", ""), q.get("label", ""), q.get("level", ""), limit, offset, q.get("username", ""))
                        elif re.fullmatch(r"/api/messages/[1-9][0-9]{0,12}", route):
                            result = app.message_detail(conn, int(route.rsplit("/", 1)[1]))
                        elif route == "/api/sources":
                            items = app.sources(conn)
                            result = {"items": items, "total": conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]}
                        elif route == "/api/search-results":
                            try:
                                limit = int(q.get("limit", "100"))
                            except ValueError:
                                raise DashboardError("limit phải là số nguyên.") from None
                            result = app.search_results(conn, limit)
                        elif route == "/api/runs":
                            result = {"items": app.runs(conn), "total": conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]}
                        elif route == "/api/profiles":
                            result = app.profiles(conn, q.get("query", ""))
                        elif route == "/api/images":
                            if q:
                                raise DashboardError("Ảnh chỉ nhận tên cơ sở dữ liệu; quét và bản ghi luôn có giới hạn.")
                            result = app.images(conn)
                        elif route == "/api/videos":
                            result = app.videos(conn)
                        else:
                            raise DashboardError("Không tìm thấy API.", "not_found", 404)
                    self._reply(result)
                else:
                    # Only whitelisted asset types in the dedicated package directory.
                    name = "index.html" if route == "/" else unquote(route).lstrip("/")
                    path = STATIC_DIR / name
                    allowed = {".html", ".css", ".js", ".woff", ".woff2", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico"}
                    if Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name or path.suffix.lower() not in allowed or not path.resolve().is_relative_to(STATIC_DIR.resolve()) or path.is_symlink() or not path.is_file():
                        raise DashboardError("Không tìm thấy tài nguyên.", "not_found", 404)
                    if path.stat().st_size > 10 * 1024 * 1024:
                        raise DashboardError("Tài nguyên quá lớn.", "not_found", 404)
                    raw = path.read_bytes()
                    self._headers(200, mimetypes.guess_type(path.name)[0] or "application/octet-stream", len(raw))
                    if self.command != "HEAD":
                        self.wfile.write(raw)

            def _post(self):
                self._guard(mutation=True)
                route, q = self._query()
                if q or self.headers.get("Transfer-Encoding") or self.headers.get_content_type() != "application/json":
                    raise DashboardError("Cần JSON, không nhận query hoặc transfer encoding.")
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) != 1 or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= MAX_BODY:
                    raise DashboardError("JSON phải có Content-Length từ 1 đến 65536.")
                raw = self.rfile.read(int(lengths[0]))
                try:
                    def pairs(values):
                        result = {}
                        for k, v in values:
                            if k in result:
                                raise ValueError()
                            result[k] = v
                        return result
                    data = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                except (ValueError, UnicodeError, RecursionError):
                    raise DashboardError("JSON không hợp lệ.") from None
                if route == "/api/settings":
                    self._reply(app.settings(data))
                elif route == "/api/jobs":
                    self._reply({"job": app.submit(data)}, 200)
                elif re.fullmatch(r"/api/jobs/[a-f0-9]{32}/cancel", route):
                    if data != {}:
                        raise DashboardError("Huỷ tác vụ nhận object rỗng.")
                    self._reply(app.cancel(route.split("/")[3]))
                else:
                    raise DashboardError("Không tìm thấy API.", "not_found", 404)

            def _run(self, action):
                self.connection.settimeout(10)
                try:
                    action()
                except DashboardError as exc:
                    self._reply({"error": str(exc), "code": exc.code}, exc.status)
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    pass
                except (ValueError, TypeError, OSError, sqlite3.Error):
                    self._reply({"error": "Không xử lý được yêu cầu; dữ liệu hoặc đường dẫn không hợp lệ.", "code": "invalid_input"}, 400)

            def do_GET(self):
                self._run(self._get)

            def do_HEAD(self):
                self._run(self._get)

            def do_POST(self):
                self._run(self._post)

            def do_OPTIONS(self):
                self._run(lambda: (_ for _ in ()).throw(DashboardError("Không bật CORS.", "forbidden", 403)))

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            request_queue_size = 16

            def server_close(self):
                super().server_close()
                app.close()

        server = Server(("127.0.0.1", port), Handler)
        server.dashboard_token = secrets.token_urlsafe(32)
        return server


def serve_dashboard(db_path=DEFAULT_DB, port=8765, open_browser=False):
    """Run the dashboard on IPv4 loopback only; Ctrl-C cancels owned children."""
    app = DashboardApp(db_path)
    server = app.create_server(port)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Dashboard cục bộ: {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
