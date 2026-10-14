#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""username_info.py — CHỨC NĂNG 2: nhập username -> trả về **thông tin của tài khoản đó**.

Khác với `demo_telegram.py` (thu thập + chấm điểm + báo cáo), file này chỉ tập trung
vào một việc: **username vào, hồ sơ công khai ra**.

    username  ->  mở hồ sơ công khai  ->  loại tài khoản (Kênh / Nhóm / Bot / Người dùng),
                                          tên hiển thị, giới thiệu, quy mô, ID, xác minh,
                                          và thống kê bài đăng gần đây (nếu là kênh/nhóm)

Hai chế độ:
    (mặc định)  đọc trang công khai `https://t.me/<username>` — **chỉ thư viện chuẩn**,
                không cần đăng nhập, nhưng không thấy được group kín và không có ID.
    --api       dùng Telegram API (Telethon) — thêm **ID, verified, is_bot**, số thành viên,
                và tra được cả **group kín** mà tài khoản đã tham gia (cần `--login` trước).

Ví dụ
-----
    python3 username_info.py durov
    python3 username_info.py durov @BotFather pythonchat vitalikbuterin
    python3 username_info.py durov --limit 25 --out profile.json     # kèm thống kê bài đăng
    .venv-api/bin/python username_info.py durov pythonchat --api     # thêm ID/verified
    python3 username_info.py --from-json usernames.json              # lấy username từ chức năng 1
"""

from __future__ import annotations

import argparse
import json
import sys

from analyzer import NOUN_API, NOUN_WEB, analyze, print_profile, write_json


# ---------------------------------------------------------------------------
# 1. Nguồn danh sách username
# ---------------------------------------------------------------------------

def usernames_from_json(path: str) -> list[str]:
    """Lấy danh sách username từ JSON (kết quả `--out` của keyword_usernames.py)."""
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    raw = payload.get("usernames", []) if isinstance(payload, dict) else payload
    out: list[str] = []
    for item in raw:
        name = item.get("username") if isinstance(item, dict) else item
        name = (name or "").lstrip("@").strip()
        if name and name not in out:
            out.append(name)
    return out


# ---------------------------------------------------------------------------
# 2. Tra cứu: bản web (mặc định)
# ---------------------------------------------------------------------------

def lookup_web(name: str, limit: int) -> tuple[dict, list[dict]]:
    """Tra một username qua trang công khai; kênh thì lấy thêm bài gần đây."""
    import demo_telegram as web

    profile = web.lookup_user(name)
    records: list[dict] = []
    if limit > 0 and profile.get("kind") == "channel" and profile.get("exists"):
        try:
            records, _info = web.scrape_channel(profile.get("username") or name, limit)
        except RuntimeError as exc:
            print(f"    ! Không lấy được bài đăng gần đây: {exc}", file=sys.stderr)
        for rec in records:
            rec["analysis"] = analyze(rec)
    return profile, records


# ---------------------------------------------------------------------------
# 3. Tra cứu: bản Telegram API (--api)
# ---------------------------------------------------------------------------

async def _lookup_api(names: list[str], args) -> tuple[list[dict], list[dict], int]:
    """Tra lần lượt qua API. Trả `(profiles, records, exit_code)`."""
    try:
        import demo_telegram_api as api
    except ImportError as exc:                       # pragma: no cover
        print(f"[!] Không nạp được demo_telegram_api.py: {exc}", file=sys.stderr)
        return [], [], 111
    if api.TelegramClient is None:                   # pragma: no cover
        api._missing()
        return [], [], 111

    creds = argparse.Namespace(api_id=args.api_id, api_hash=args.api_hash)
    api_id, api_hash = api.load_credentials(creds)
    if not api_id or not api_hash:
        print(api.credentials_help(), file=sys.stderr)
        return [], [], 1

    client, err = await api.open_client(api_id, api_hash, interactive=False,
                                        session=args.session)
    if client is None:
        print(f"[!] {err}", file=sys.stderr)
        return [], [], 1

    profiles: list[dict] = []
    records: list[dict] = []
    try:
        for name in names:
            try:
                profile, found = await api.lookup_entity(client, name, args.limit)
            except RuntimeError as exc:
                print(f"[!] {exc}", file=sys.stderr)
                continue
            for rec in found:
                rec["analysis"] = analyze(rec)
            profiles.append(profile)
            records += found
    finally:
        await client.disconnect()
    return profiles, records, 0


# ---------------------------------------------------------------------------
# 4. Chạy
# ---------------------------------------------------------------------------

def run(args) -> int:
    names: list[str] = []
    for name in list(args.usernames) + list(args.user or []):
        name = (name or "").strip()
        if name and name not in names:
            names.append(name)
    if args.from_json:
        for name in usernames_from_json(args.from_json):
            if name not in names:
                names.append(name)

    if not names:
        print("[!] Chưa nhập username nào.\n"
              "    Ví dụ: python3 username_info.py durov @BotFather\n"
              "           python3 username_info.py --from-json usernames.json",
              file=sys.stderr)
        return 1

    print("=" * 100)
    print("THÔNG TIN TÀI KHOẢN THEO USERNAME".center(100))
    print("=" * 100)

    noun = NOUN_API if args.api else NOUN_WEB
    profiles: list[dict] = []
    records: list[dict] = []

    if args.api:
        import asyncio
        profiles, records, code = asyncio.run(_lookup_api(names, args))
        if code and not profiles:
            return code
        for profile in profiles:
            own = [r for r in records
                   if (r.get("channel") or "").lower() == (profile.get("username") or "").lower()]
            print_profile(profile, own or None, noun=noun)
    else:
        for name in names:
            try:
                profile, found = lookup_web(name, args.limit)
            except RuntimeError as exc:
                print(f"[!] {name}: {exc}", file=sys.stderr)
                continue
            profiles.append(profile)
            records += found
            print_profile(profile, found or None, noun=noun)

    if not profiles:
        return 1

    if args.out:
        write_json(args.out, records, backend="telethon" if args.api else "web (t.me)",
                   noun=noun, extra={"profiles": profiles})
        print(f"[i] Đã lưu thông tin {len(profiles)} tài khoản vào {args.out}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Nhập username -> in thông tin công khai của tài khoản (chức năng 2).",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("usernames", nargs="*", metavar="USERNAME",
                   help="username (hoặc @username, t.me/username) cần tra; nhiều username OK")
    p.add_argument("--user", action="append", metavar="USERNAME",
                   help="cách nhập khác, lặp lại được")
    p.add_argument("--from-json", metavar="FILE",
                   help="đọc danh sách username từ JSON do keyword_usernames.py tạo")
    p.add_argument("--limit", type=int, default=0,
                   help="số bài gần đây lấy kèm để thống kê (mặc định 0 = chỉ thông tin)")
    p.add_argument("--api", action="store_true",
                   help="dùng Telegram API (Telethon): có ID/verified, tra được group kín")
    p.add_argument("--session", default="", metavar="TEN",
                   help="tên phiên API (mặc định tg_session; ví dụ tk-phu)")
    p.add_argument("--api-id", help="api_id (mặc định: TG_API_ID hoặc tg_api.json)")
    p.add_argument("--api-hash", help="api_hash (mặc định: TG_API_HASH hoặc tg_api.json)")
    p.add_argument("--out", metavar="FILE", help="ghi kết quả ra JSON")
    return run(p.parse_args())


if __name__ == "__main__":
    sys.exit(main())
