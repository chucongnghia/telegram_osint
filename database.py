#!/usr/bin/env python3
"""Manage the application's SQLite database without Telegram or network access."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from analyzer import json_path, resolve_json
from storage import DEFAULT_DB, Database, add_storage_arguments


def positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("phải >= 1")
    return number


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Khởi tạo, nhập JSON và tra cứu SQLite Telegram")
    add_storage_arguments(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="khởi tạo schema (an toàn khi chạy lại)")
    commands.add_parser("stats", help="đếm số bản ghi từng bảng")
    imp = commands.add_parser("import-json", help="nhập dữ liệu cũ, không nhập credential")
    imp.add_argument("files", nargs="*", help="tên JSON hoặc đường dẫn")
    imp.add_argument("--all", action="store_true", help="nhập các JSON dữ liệu trong json_folder/")
    runs = commands.add_parser("runs", help="lịch sử thu thập/tìm kiếm/nhập dữ liệu")
    runs.add_argument("--limit", type=positive, default=20)
    messages = commands.add_parser("messages", help="tra bài đã lưu; không cần mạng")
    messages.add_argument("--keyword", default="", help="khớp chuỗi con, không phân biệt dấu tiếng Việt")
    messages.add_argument("--username", default="")
    messages.add_argument("--limit", type=positive, default=20)
    profiles = commands.add_parser("profiles", help="lịch sử hồ sơ username đã tra")
    profiles.add_argument("username")
    profiles.add_argument("--limit", type=positive, default=20)
    results = commands.add_parser("results", help="kết quả username của một lượt tìm kiếm")
    results.add_argument("run_id", type=positive)
    export = commands.add_parser("export", help="xuất nguyên snapshot của một run ra JSON")
    export.add_argument("run_id", type=positive)
    export.add_argument("--out", required=True, help="tên trần → json_folder/")
    backup = commands.add_parser("backup", help="backup nhất quán bằng SQLite backup API")
    backup.add_argument("target", help="file backup mới, không ghi đè")
    args = parser.parse_args(argv)
    if args.no_db:
        parser.error("database.py quản lý SQLite nên không nhận --no-db")
    if args.command not in ("init", "import-json") and not Path(args.db).expanduser().is_file():
        print("[!] Database chưa tồn tại. Chạy: python3 database.py init", file=sys.stderr)
        return 2
    try:
        with Database(args.db) as db:
            if args.command == "init":
                print(f"[i] SQLite sẵn sàng: {db.path} (schema v1, SQLite {sqlite3.sqlite_version})")
            elif args.command == "stats":
                print(json.dumps(db.stats(), ensure_ascii=False, indent=2))
            elif args.command == "runs":
                print(json.dumps(db.runs(args.limit), ensure_ascii=False, indent=2))
            elif args.command == "messages":
                print(json.dumps(db.messages(args.keyword, args.username, args.limit), ensure_ascii=False, indent=2))
            elif args.command == "profiles":
                print(json.dumps(db.profiles(args.username, args.limit), ensure_ascii=False, indent=2))
            elif args.command == "results":
                payload = db.payload(args.run_id)
                payload = payload if isinstance(payload, dict) else {}
                print(json.dumps({key: payload.get(key, []) for key in ("keywords", "usernames", "invite_links")}, ensure_ascii=False, indent=2))
            elif args.command == "export":
                payload = db.payload(args.run_id)
                target = Path(json_path(args.out))
                target.parent.mkdir(parents=True, exist_ok=True)
                # Exports can contain personal information, like the database.
                with target.open("x", encoding="utf-8") as fh:
                    target.chmod(0o600)
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
                print(f"[i] Đã xuất run #{args.run_id} → {target}")
            elif args.command == "backup":
                db.backup(args.target)
                print(f"[i] Đã backup → {args.target}")
            elif args.command == "import-json":
                files = [Path(resolve_json(f)) for f in args.files]
                if args.all:
                    files += sorted((Path(__file__).resolve().parent / "json_folder").glob("*.json"))
                if not files:
                    parser.error("import-json cần FILE hoặc --all")
                errors = 0
                for path in dict.fromkeys(p.resolve() for p in files):
                    if args.all and path.name.lower() == "tg_api.json":
                        print(f"[skip] {path.name}: credential, không nhập")
                        continue
                    try:
                        rid = db.import_json(path)
                        print(f"[i] {path.name} → run #{rid} (nội dung đã nhập sẽ không nhân đôi)")
                    except (ValueError, OSError, sqlite3.Error, TypeError) as exc:
                        print(f"[!] {path.name}: {exc}", file=sys.stderr)
                        errors += 1
                return 1 if errors else 0
    except (ValueError, OSError, sqlite3.Error, TypeError) as exc:
        print(f"[!] SQLite: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
