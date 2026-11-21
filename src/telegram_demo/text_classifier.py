"""Text-only Jev classification of calls to illegal action.

Versioned rubric, immutable evidence, SQLite queries. No Telegram access, no URL
fetching, no fallback to keyword scores. The judgment is a triage signal for a
human reviewer, NOT a legal conclusion or a safety guarantee.
"""
from __future__ import annotations

import hashlib
import json
import uuid

from .incitement import LABELS_VI, QUESTIONS as INCITEMENT_QUESTIONS
from .storage import Database, dumps, now, search_text

LABELS = dict(LABELS_VI)
QUESTIONS = {"classification": INCITEMENT_QUESTIONS["incitement"]}
RUBRIC_VERSION = "text-v2:sha256:" + hashlib.sha256(dumps(QUESTIONS).encode()).hexdigest()
MAX_TEXT_CHARS = 16000
MANIFEST_FORMAT = "telegram-jev-requests-v1"
RESULT_FORMAT = "telegram-jev-results-v1"


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tool_backend(model: str) -> str:
    return "dsh-tool:" + hashlib.sha256(("jev_ask:" + model).encode()).hexdigest()


class ClassifierStore:
    def __init__(self, db: Database):
        self.db = db

    def candidates(self, *, backend_key: str, limit=10, keyword="", username="",
                   run_id=None, message_ids=(), include_demo=False, recheck=False) -> list[dict]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit phải từ 1 đến 1000")
        sql = """SELECT m.id AS message_row_id,m.text,m.record_json,
                 m.message_id,e.namespace,e.peer_type,e.telegram_id,e.username
                 FROM messages m JOIN entities e ON e.id=m.entity_id
                 WHERE length(trim(m.text))>0 AND instr(m.search_text,?)>0"""
        params = [search_text(keyword)]
        if not include_demo:
            sql += " AND e.namespace!='demo'"
        if username:
            sql += " AND e.id IN (SELECT entity_id FROM entity_aliases WHERE username=? COLLATE NOCASE)"
            params.append(username.lstrip("@"))
        if run_id is not None:
            sql += " AND EXISTS (SELECT 1 FROM run_messages rm WHERE rm.message_id=m.id AND rm.run_id=?)"
            params.append(run_id)
        if message_ids:
            sql += " AND m.id IN (" + ",".join("?" for _ in message_ids) + ")"
            params.extend(message_ids)
        # Filter successes and deterministic terminal skips before LIMIT. Real
        # upstream errors remain retryable; --recheck explicitly bypasses this.
        if not recheck:
            sql += """ AND NOT EXISTS (SELECT 1 FROM message_classifications c
                       WHERE c.message_row_id=m.id AND c.input_text=m.text
                       AND c.rubric_version=? AND c.backend_key=?
                        AND (c.status='success' OR
                             (c.status='skipped' AND c.error_code='text_too_long')))"""
            params.extend((RUBRIC_VERSION, backend_key))
        sql += " ORDER BY m.posted_at DESC,m.id DESC LIMIT ?"
        params.append(limit)
        items = []
        for row in self.db.conn.execute(sql, params):
            source = {key: row[key] for key in
                      ("message_id", "namespace", "peer_type", "telegram_id", "username")}
            source["record"] = json.loads(row["record_json"])
            items.append({"message_row_id": row["message_row_id"],
                          "input_sha256": text_hash(row["text"]),
                          "state": {"text": row["text"]}, "source": source})
        return items

    def cached(self, item: dict, backend_key: str) -> bool:
        return self.db.conn.execute(
            "SELECT 1 FROM message_classifications WHERE message_row_id=? AND input_sha256=? "
            "AND rubric_version=? AND backend_key=? AND status='success' LIMIT 1",
            (item["message_row_id"], item["input_sha256"], RUBRIC_VERSION, backend_key)
        ).fetchone() is not None

    def validate_item(self, item: dict) -> None:
        """Bind imported work to the exact current source/text; reject stale/tampered inputs."""
        mid = item.get("message_row_id")
        if isinstance(mid, bool) or not isinstance(mid, int) or mid < 1:
            raise ValueError("message_row_id không hợp lệ")
        if not isinstance(item.get("state"), dict) or not isinstance(item.get("source"), dict):
            raise ValueError("state/source phải là object")
        text = item["state"].get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
            raise ValueError("Văn bản trống/không hợp lệ hoặc quá dài")
        if item.get("input_sha256") != text_hash(text):
            raise ValueError("Hash văn bản không khớp")
        row = self.db.conn.execute(
            "SELECT m.text,m.message_id,m.record_json,e.namespace,e.peer_type,e.telegram_id "
            "FROM messages m JOIN entities e ON e.id=m.entity_id WHERE m.id=?", (mid,)
        ).fetchone()
        if row is None or row["text"] != text:
            raise ValueError("Tin nhắn đã đổi hoặc không còn trong database; chuẩn bị yêu cầu mới")
        source = item.get("source", {})
        for key in ("message_id", "namespace", "peer_type", "telegram_id"):
            if source.get(key) != row[key]:
                raise ValueError("Định danh nguồn không khớp database")
        if source.get("record") != json.loads(row["record_json"]):
            raise ValueError("Snapshot nguồn không khớp database; chuẩn bị yêu cầu mới")

    def save(self, item: dict, *, batch_id: str, provider: str, model: str, backend_key: str,
             status: str, label=None, probabilities=None, response=None, error_code=None) -> int:
        if status not in ("success", "error", "skipped"):
            raise ValueError("Trạng thái phân loại không hợp lệ")
        if status == "success" and label not in LABELS:
            raise ValueError("Nhãn không hợp lệ")
        if status != "success" and label is not None:
            raise ValueError("Không gán nhãn cho yêu cầu lỗi/bỏ qua")
        # Each judgment commits separately. No writer lock is held during inference.
        with self.db.transaction():
            cur = self.db.conn.execute(
                """INSERT INTO message_classifications
                (message_row_id,batch_id,created_at,provider,model,backend_key,
                 rubric_version,rubric_json,input_sha256,input_text,source_json,status,
                 label,probabilities_json,response_json,error_code)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item["message_row_id"], batch_id, now(), provider, model, backend_key,
                 RUBRIC_VERSION, dumps(QUESTIONS), item["input_sha256"], item["state"]["text"],
                 dumps(item["source"]), status, label,
                 dumps(probabilities) if probabilities is not None else None,
                 dumps(response) if response is not None else None, error_code))
            return cur.lastrowid

    def results(self, limit=20, label="", status="", message_id=None) -> list[dict]:
        sql = """SELECT c.*,m.text AS current_text FROM message_classifications c
                 LEFT JOIN messages m ON m.id=c.message_row_id WHERE 1=1"""
        params = []
        for column, value in (("label", label), ("status", status), ("message_row_id", message_id)):
            if value not in (None, ""):
                sql += f" AND c.{column}=?"
                params.append(value)
        sql += " ORDER BY c.id DESC LIMIT ?"
        params.append(limit)
        output = []
        for row in self.db.conn.execute(sql, params):
            item = dict(row)
            item["stale"] = item.pop("current_text") != item["input_text"]
            for key in ("rubric_json", "source_json", "probabilities_json", "response_json"):
                item[key[:-5]] = json.loads(item.pop(key)) if row[key] is not None else None
            item["label_vi"] = LABELS.get(item["label"])
            output.append(item)
        return output


def new_manifest(items: list[dict], model: str, recheck=False) -> dict:
    return {"format": MANIFEST_FORMAT, "batch_id": str(uuid.uuid4()), "model": model,
            "rubric_version": RUBRIC_VERSION, "questions": json.loads(dumps(QUESTIONS)), "items": items,
            "recheck": bool(recheck)}


def validate_manifest(manifest: dict) -> None:
    if not isinstance(manifest, dict) or manifest.get("format") != MANIFEST_FORMAT:
        raise ValueError("Không đúng định dạng Jev requests")
    if manifest.get("rubric_version") != RUBRIC_VERSION or manifest.get("questions") != QUESTIONS:
        raise ValueError("Rubric đã đổi; phải chuẩn bị yêu cầu mới")
    if not isinstance(manifest.get("model"), str) or not manifest["model"].strip():
        raise ValueError("Thiếu model yêu cầu")
    if not isinstance(manifest.get("batch_id"), str) or not manifest["batch_id"]:
        raise ValueError("Thiếu batch_id")
    if not isinstance(manifest.get("recheck", False), bool):
        raise ValueError("recheck phải là boolean")
    items = manifest.get("items")
    if not isinstance(items, list) or len(items) > 1000 or any(not isinstance(i, dict) for i in items):
        raise ValueError("Danh sách yêu cầu không hợp lệ")
    mids = [i.get("message_row_id") for i in items]
    if any(isinstance(mid, bool) or not isinstance(mid, int) for mid in mids) or len(mids) != len(set(mids)):
        raise ValueError("ID yêu cầu không hợp lệ hoặc trùng")
