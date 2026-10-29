#!/usr/bin/env python3
"""Classify saved Telegram text with Jev; keep AI judgments apart from heuristics."""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import uuid
from pathlib import Path

from ..analyzer import json_path, resolve_json
from ..storage import DEFAULT_DB, Database
from ..text_classifier import (ClassifierStore, LABELS, MAX_TEXT_CHARS, QUESTIONS,
                             RESULT_FORMAT, RUBRIC_VERSION, new_manifest, tool_backend,
                             validate_manifest)

MAX_JSON_BYTES = 16 * 1024 * 1024


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("phải >= 1")
    return number


def read_json(path):
    path = Path(resolve_json(str(path)))
    with path.open("rb") as fh:
        data = fh.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise ValueError("JSON vượt giới hạn 16 MiB")
    return json.loads(data.decode("utf-8"))


def write_new_json(name, data):
    # Apply consumes this same UTF-8 byte limit. Never emit a successful manifest
    # that our own reader cannot accept (text is also present in source.record).
    encoded = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    if len(encoded) > MAX_JSON_BYTES:
        raise ValueError("JSON vượt giới hạn 16 MiB; giảm --limit hoặc chia batch nhỏ hơn")
    path = Path(json_path(name)).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(encoded)
    return path


def options(args):
    return {"limit": args.limit, "keyword": args.keyword, "username": args.username,
            "run_id": args.run_id, "message_ids": args.message_id,
            "include_demo": args.include_demo, "recheck": args.recheck}


def run_api(store, args):
    from ..jev_client import JevClient, JevError
    try:
        client = JevClient(model=args.model, timeout=args.timeout)
    except JevError as exc:
        print(f"[!] Jev {exc.code}: cấu hình không hợp lệ", file=sys.stderr)
        return 2
    items = store.candidates(backend_key=client.backend_key, **options(args))
    if args.dry_run:
        print(json.dumps({"model": client.model, "rubric_version": RUBRIC_VERSION,
                          "messages": [{"message_row_id": i["message_row_id"],
                                        "input_sha256": i["input_sha256"],
                                        "characters": len(i["state"]["text"]),
                                        "source": {k: v for k, v in i["source"].items() if k != "record"}}
                                       for i in items]}, ensure_ascii=False, indent=2))
        return 0
    batch_id = str(uuid.uuid4())
    success = errors = skipped = 0
    for item in items:
        if len(item["state"]["text"]) > MAX_TEXT_CHARS:
            store.save(item, batch_id=batch_id, provider="typesafe-jev",
                       model=client.model, backend_key=client.backend_key, status="skipped",
                       error_code="text_too_long")
            skipped += 1
            print(f"[skip] row #{item['message_row_id']}: vượt {MAX_TEXT_CHARS} ký tự, không cắt ngầm", file=sys.stderr)
            continue
        try:
            response = client.ask(item["state"], QUESTIONS)
        except JevError as exc:
            store.save(item, batch_id=batch_id, provider="typesafe-jev", model=client.model,
                       backend_key=client.backend_key, status="error", error_code=exc.code)
            errors += 1
            print(f"[!] row #{item['message_row_id']}: Jev {exc.code}; không có nhãn dự phòng", file=sys.stderr)
            # Fail fast on upstream/config errors; do not bill/retry every selected row.
            break
        answer = response["answers"]["classification"]
        store.save(item, batch_id=batch_id, provider="typesafe-jev", model=response["model"],
                   backend_key=client.backend_key, status="success", label=answer["choice"],
                   probabilities=answer["probabilities"],
                   response={"requested_model": client.model, "response": response})
        success += 1
        print(f"[i] row #{item['message_row_id']}: {answer['choice']} — {LABELS[answer['choice']]}")
    print(f"[i] batch {batch_id}: thành công={success}, lỗi={errors}, bỏ qua={skipped}, "
          f"chưa xử lý={len(items)-success-errors-skipped}")
    return 1 if errors or skipped else 0


def prepare(store, args):
    items = store.candidates(backend_key=tool_backend(args.model), **options(args))
    valid = [i for i in items if len(i["state"]["text"]) <= MAX_TEXT_CHARS]
    manifest = new_manifest(valid, args.model, args.recheck)
    target = write_new_json(args.out, manifest)
    print(f"[i] Đã chuẩn bị {len(valid)} yêu cầu: {target}; chưa gọi Jev, chưa gán nhãn")
    if len(valid) != len(items):
        for item in items:
            if len(item["state"]["text"]) > MAX_TEXT_CHARS:
                store.save(item, batch_id=manifest["batch_id"], provider="dsh-jev-tool-import",
                           model=args.model, backend_key=tool_backend(args.model),
                           status="skipped", error_code="text_too_long")
        print(f"[skip] {len(items)-len(valid)} tin quá dài; đã ghi nhận để lần sau tiếp tục, không cắt ngầm", file=sys.stderr)
        return 1
    return 0


def parse_tool_output(text):
    """Read only the discrete label/model from actual rendered jev_ask output.

    Display probabilities are rounded, so never convert them into full-precision scores.
    This is an operator-provided artifact, not a cryptographically authenticated response.
    """
    if not isinstance(text, str) or not text or len(text) > 1024 * 1024:
        raise ValueError("Thiếu hoặc quá dài tool_output Jev")
    model = re.search(r"\AJev (jev-[A-Za-z0-9._-]+)(?:\s|,)", text)
    labels = re.findall(r"^classification: ([a-z_]+)\s*\(", text, re.MULTILINE)
    if model is None or len(labels) != 1 or labels[0] not in LABELS:
        raise ValueError("Không nhận diện được model/nhãn trong kết quả jev_ask")
    return model.group(1), labels[0]


def apply_tool(store, args):
    manifest, results = read_json(args.requests), read_json(args.results)
    validate_manifest(manifest)
    if not isinstance(results, dict) or results.get("format") != RESULT_FORMAT:
        raise ValueError("Không đúng định dạng Jev results")
    if results.get("batch_id") != manifest["batch_id"]:
        raise ValueError("Kết quả không thuộc batch yêu cầu")
    rows = results.get("results")
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError("results phải là danh sách object")
    requested = {i["message_row_id"]: i for i in manifest["items"]}
    seen, validated = set(), []
    # Validate the entire artifact before writing any judgments.
    for result in rows:
        mid = result.get("message_row_id")
        if isinstance(mid, bool) or not isinstance(mid, int) or mid not in requested or mid in seen:
            raise ValueError("ID kết quả trùng hoặc không thuộc yêu cầu")
        seen.add(mid)
        item = requested[mid]
        if result.get("input_sha256") != item["input_sha256"]:
            raise ValueError("Kết quả không khớp hash yêu cầu")
        store.validate_item(item)
        model, label = parse_tool_output(result.get("tool_output"))
        validated.append((item, result["tool_output"], model, label))
    saved = cached = 0
    backend = tool_backend(manifest["model"])
    for item, output, model, label in validated:
        if not manifest.get("recheck", False) and store.cached(item, backend):
            cached += 1
            continue
        store.save(item, batch_id=manifest["batch_id"], provider="dsh-jev-tool-import",
                   model=model, backend_key=backend, status="success", label=label,
                   response={"transport": "operator-provided-jev-tool-output",
                             "requested_model": manifest["model"], "tool_output": output})
        saved += 1
        print(f"[i] row #{item['message_row_id']}: {label} — {LABELS[label]}")
    missing = len(requested) - len(seen)
    print(f"[i] Đã lưu {saved}, đã có {cached}, thiếu kết quả {missing}; không suy đoán nhãn còn thiếu")
    return 1 if missing else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Tin nhắn văn bản → Jev → SQLite (không thay điểm heuristic)")
    parser.add_argument("--db", default=os.environ.get("TELEGRAM_DB_PATH") or str(DEFAULT_DB))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "prepare"):
        sub = commands.add_parser(name, help="gọi API Jev" if name == "run" else "xuất yêu cầu cho công cụ Jev trong DSH")
        sub.add_argument("--model", default=os.environ.get("JEV_MODEL") or "jev-latest")
        sub.add_argument("--limit", type=positive, default=10, help="tối đa 1000, mặc định 10")
        sub.add_argument("--keyword", default="")
        sub.add_argument("--username", default="")
        sub.add_argument("--run-id", type=positive)
        sub.add_argument("--message-id", type=positive, action="append", default=[], help="row ID nội bộ SQLite, không phải Telegram message ID")
        sub.add_argument("--include-demo", action="store_true", help="cho phép namespace demo")
        sub.add_argument("--recheck", action="store_true", help="phân loại lại kể cả đã có kết quả thành công")
        if name == "run":
            sub.add_argument("--timeout", type=float, default=60)
            sub.add_argument("--dry-run", action="store_true", help="chỉ xem danh sách, không mạng, không lưu phân loại")
        else:
            sub.add_argument("--out", required=True, help="file mới; tên trần → json_folder/")
    apply = commands.add_parser("apply", help="nhập kết quả công cụ jev_ask; không gọi API")
    apply.add_argument("--requests", required=True)
    apply.add_argument("--results", required=True)
    results = commands.add_parser("results", help="đọc lịch sử phân loại offline")
    results.add_argument("--limit", type=positive, default=20)
    results.add_argument("--label", choices=LABELS, default="")
    results.add_argument("--status", choices=("success", "error", "skipped"), default="")
    results.add_argument("--message-id", type=positive)
    args = parser.parse_args(argv)
    if not Path(args.db).expanduser().is_file():
        parser.error("Database chưa tồn tại; thu thập/nhập JSON bằng database.py trước")
    try:
        with Database(args.db) as db:
            store = ClassifierStore(db)
            if args.command == "run":
                return run_api(store, args)
            if args.command == "prepare":
                return prepare(store, args)
            if args.command == "apply":
                return apply_tool(store, args)
            print(json.dumps(store.results(args.limit, args.label, args.status, args.message_id), ensure_ascii=False, indent=2))
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        print(f"[!] Phân loại: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("[!] Đã dừng; các kết quả đã commit vẫn giữ để tiếp tục lần sau", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
