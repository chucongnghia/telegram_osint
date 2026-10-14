#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""keyword_usernames.py — CHỨC NĂNG 1: nhập keyword -> trả về **list username** liên quan.

Khác với `demo_telegram.py` (thu thập + chấm điểm + báo cáo đầy đủ), file này chỉ tập
trung vào một việc: **từ khoá vào, danh sách username ra**.

    keyword  ->  quét nội dung  ->  gom mọi username gặp trong bài khớp từ khoá
                                 (bài đăng trong kênh, @nhắc, link t.me, nguồn forward,
                                  và kết quả tìm chat nếu chạy qua Telegram API)
                                 ->  in bảng xếp theo điểm nghi ngờ cao nhất

Nguồn dữ liệu (chọn một hoặc nhiều, có thể trộn):
    --demo                dữ liệu mẫu offline, không cần mạng
    --channel KENH        kênh công khai: durov, @durov, t.me/durov (lặp lại được)
    --seed-file FILE      file danh sách kênh, mỗi dòng 1 kênh
    --json FILE           tra thẳng trong JSON đã lưu (posts_web.json, nhom1.json, ...)
    --api                 dùng Telegram API (Telethon): tìm chat theo từ khoá + đọc được
                          cả group kín mà tài khoản đã tham gia

Cách dùng ngắn nhất — quên hết cờ đi, chỉ cần từ khoá; script tự tìm trên Telegram:

    .venv-api/bin/python keyword_usernames.py --keyword "hathanh"

        @hathanh123   Hà Thanh
        @hathanhp     thanh Meshchain.Ai
        ...

    (không chọn nguồn nào -> tự dùng Telegram API. Nếu chưa có Telethon/phiên đăng nhập
     thì dùng --demo để xem thử định dạng kết quả.)

Ví dụ
-----
    # offline, minh hoạ nhanh
    python3 keyword_usernames.py --keyword "đầu tư" --keyword "hoàn tiền" --demo

    # quét một kênh công khai thật (chỉ thư viện chuẩn)
    python3 keyword_usernames.py --keyword "telegram" --channel durov --limit 25

    # tra lại trong dữ liệu đã lưu, chỉ lấy username >= 25 điểm và chỉ khớp trong nội dung bài
    python3 keyword_usernames.py --keyword "chiến sự" --json nhom1.json --min-score 25 --text-only

    # qua Telegram API: thêm kết quả tìm chat theo từ khoá (cần .venv-api + --login)
    .venv-api/bin/python keyword_usernames.py --keyword "chiến sự" --api

    # lưu kết quả ra file
    python3 keyword_usernames.py --keyword "đầu tư" --demo --out usernames.json
"""

from __future__ import annotations

import argparse
import json
import sys

import analyzer
from analyzer import (NOUN_API, NOUN_WEB, analyze, demo_posts, json_path,
                      print_username_section, resolve_json, username_payload,
                      write_json)

BACKEND_WEB = "web (t.me/s)"
BACKEND_JSON = "json"


# ---------------------------------------------------------------------------
# 1. Thu thập dữ liệu
# ---------------------------------------------------------------------------

def records_from_json(path: str) -> list[dict]:
    """Đọc danh sách bài từ file JSON đã lưu (chấp nhận cả file chỉ có list)."""
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    records = payload.get("posts", []) if isinstance(payload, dict) else payload
    if not records:
        raise RuntimeError(f"File {path} không có bài đăng nào (khóa 'posts').")
    for rec in records:
        rec["analysis"] = analyze(rec)      # chấm lại bằng code hiện tại
    return records


def records_from_web(channels: list[str], limit: int) -> list[dict]:
    """Quét các kênh công khai qua trang `t.me/s/<kênh>` (chỉ thư viện chuẩn)."""
    import demo_telegram as web

    records: list[dict] = []
    for name in channels:
        try:
            found, _info = web.scrape_channel(name, limit)
        except RuntimeError as exc:
            print(f"[!] {exc}", file=sys.stderr)
            continue
        for rec in found:
            rec.setdefault("channel", name)
            rec["analysis"] = analyze(rec)
        records += found
    return records


def _import_api():
    """Nạp `demo_telegram_api` muộn (chỉ khi dùng --api) để bản web vẫn thuần stdlib."""
    try:
        import demo_telegram_api as api
    except ImportError as exc:                       # pragma: no cover
        print(f"[!] Không nạp được demo_telegram_api.py: {exc}", file=sys.stderr)
        return None
    if api.TelegramClient is None:                   # pragma: no cover
        api._missing()
        return None
    return api


async def _collect_api(args, channels: list[str], keywords: list[str]) -> tuple[list[dict], list[dict], int]:
    """Thu thập qua Telegram API. Trả `(records, extra_usernames, exit_code)`."""
    api = _import_api()
    if api is None:
        print("[i] Gợi ý: thêm --demo (hoặc --json FILE) để dùng bản không cần API.",
              file=sys.stderr)
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

    records: list[dict] = []
    try:
        for name in channels:
            try:
                records += await api.fetch_history(client, name, args.limit)
            except RuntimeError as exc:
                print(f"[!] {exc}", file=sys.stderr)
        extra: list[dict] = []
        for kw in keywords or [""]:
            if not kw:
                continue
            extra += await api.search_chats(client, kw, limit=args.api_search_limit)
    finally:
        await client.disconnect()

    for rec in records:
        rec["analysis"] = analyze(rec)
    return records, extra, 0


# ---------------------------------------------------------------------------
# 2. Lọc & trình bày
# ---------------------------------------------------------------------------

def filter_rows(rows: list[dict], min_score: int, top: int) -> list[dict]:
    """Giữ username có điểm cao nhất >= `min_score`, rồi cắt còn `top` dòng."""
    kept = [r for r in rows if (r.get("max_score") or 0) >= min_score]
    return kept[:top] if top and top > 0 else kept


def run(args) -> int:
    if args.loose:                      # "hơi giống": cho phép khớp chuỗi con
        analyzer.MATCH_WORD_BOUNDARY = False
        print("[i] --loose: khớp chuỗi con (rộng hơn, có thể nhiều nhiễu).")

    keywords = args.keyword or []
    channels = list(args.channel or [])
    if args.seed_file:
        import demo_telegram as web
        channels += web.read_seed_file(args.seed_file)

    # Không chọn nguồn nào -> mặc định tìm thẳng trên Telegram (API): đây là cách duy
    # nhất tìm được username "hơi giống" một cái tên, vì trang công khai t.me chỉ tra
    # được khi biết ĐÚNG username.
    if not (args.demo or args.json or channels or args.api):
        if not keywords:
            print("[!] Cần ít nhất một từ khoá: --keyword TUKHOA", file=sys.stderr)
            return 1
        print("[i] Không chọn nguồn dữ liệu → tìm trực tiếp trên Telegram bằng API "
              "(contacts.Search; cần Telethon + phiên đã đăng nhập).")
        args.api = True

    records: list[dict] = []
    extra: list[dict] = []
    sources: list[str] = []

    if args.json:
        args.json = resolve_json(args.json)
        records += records_from_json(args.json)
        sources.append(f"{BACKEND_JSON}: {args.json}")
    if args.demo:
        records += demo_posts(17, dialect="web")
        for rec in records:
            rec["analysis"] = analyze(rec)
        sources.append("demo (offline)")
    if channels:
        records += records_from_web(channels, args.limit)
        sources.append(f"{BACKEND_WEB}: {', '.join(channels)}")
    if args.api:
        api_records, extra, code = _run_api(args, channels, keywords)
        if code:
            return code
        records += api_records
        sources.append("telethon (API)")

    if not records and not extra:
        print("[!] Chưa có dữ liệu nào. Chọn nguồn: --demo | --channel K | --seed-file F "
              "| --json FILE | --api\n    Ví dụ: python3 keyword_usernames.py "
              "--keyword \"đầu tư\" --demo", file=sys.stderr)
        return 1

    payload = username_payload(records, keywords, extra_usernames=extra,
                               only_text=args.text_only)
    rows = filter_rows(payload["usernames"], args.min_score, args.top)

    print("=" * 100)
    print("TÌM USERNAME THEO TỪ KHOÁ".center(100))
    print("=" * 100)
    print(f"Nguồn dữ liệu : {'; '.join(sources) if sources else '(không có)'}")
    print(f"Từ khoá       : {', '.join(keywords) if keywords else '(không có — xét mọi bài)'}")
    pham_vi = "chỉ nội dung bài" if args.text_only else "nội dung bài + tên/mã kênh"
    print(f"Phạm vi khớp  : {pham_vi}")
    print(f"Bài đã quét   : {len(records)}" +
          (f" (+{len(extra)} chat từ tìm kiếm API)" if extra else ""))
    print(f"Username thấy : {len(payload['usernames'])}"
          + (f"  →  hiện {len(rows)} dòng" if len(rows) != len(payload["usernames"]) else ""))
    noun = NOUN_API if args.record_noun == "tin" else NOUN_WEB
    # Chỉ có kết quả tìm kiếm (không có bài nào) -> in gọn mỗi username một dòng.
    compact = args.compact or (not args.full and not records)
    print_username_section(rows, keywords, payload["invite_links"], noun=noun,
                           limit=args.top or len(rows) or 1, compact=compact)

    if args.out:
        args.out = json_path(args.out)
        write_json(args.out, records, backend="usernames-by-keyword", noun=noun,
                   extra={"keywords": keywords, "usernames": rows,
                          "invite_links": payload["invite_links"]})
        print(f"[i] Đã lưu danh sách username vào {args.out}")
    return 0


def _run_api(args, channels: list[str], keywords: list[str]):
    """Chạy `_collect_api` trong event loop; tách riêng để `run()` dễ đọc."""
    import asyncio
    return asyncio.run(_collect_api(args, channels, keywords))


# ---------------------------------------------------------------------------
# 3. main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="Nhập keyword -> in danh sách username liên quan (chức năng 1).",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--keyword", action="append", metavar="TUKHOA",
                   help="từ khoá cần tìm (lặp lại được; bỏ trống = xét mọi bài)")
    p.add_argument("--demo", action="store_true", help="dùng dữ liệu mẫu offline")
    p.add_argument("--channel", action="append", metavar="KENH",
                   help="kênh công khai cần quét (lặp lại được)")
    p.add_argument("--seed-file", metavar="FILE", help="file danh sách kênh")
    p.add_argument("--json", metavar="FILE", help="tra trong JSON đã lưu")
    p.add_argument("--api", action="store_true",
                   help="dùng Telegram API (Telethon): tìm chat theo từ khoá, đọc group kín "
                        "(mặc định tự bật khi không chọn nguồn dữ liệu nào khác)")
    p.add_argument("--session", default="", metavar="TEN",
                   help="tên phiên API (mặc định tg_session; ví dụ tk-phu)")
    p.add_argument("--api-id", help="api_id (mặc định: TG_API_ID hoặc tg_api.json)")
    p.add_argument("--api-hash", help="api_hash (mặc định: TG_API_HASH hoặc tg_api.json)")
    p.add_argument("--api-search-limit", type=int, default=20,
                   help="số chat tối đa cho mỗi từ khoá khi tìm qua API (mặc định 20)")
    p.add_argument("--limit", type=int, default=20,
                   help="số bài tối đa cho MỖI kênh khi tự thu thập (mặc định 20)")
    p.add_argument("--min-score", type=int, default=0,
                   help="chỉ hiện username có điểm nghi ngờ cao nhất >= mức này")
    p.add_argument("--top", type=int, default=0, help="chỉ hiện N username đầu (0 = tất cả)")
    p.add_argument("--loose", action="store_true",
                   help="khớp chuỗi con thay vì ranh giới từ (tìm rộng hơn)")
    p.add_argument("--text-only", action="store_true",
                   help="chỉ tìm trong nội dung bài, bỏ tên/mã kênh (đỡ khớp oan cả kênh)")
    p.add_argument("--record-noun", choices=["bài", "tin"], default="bài",
                   help="cách gọi đơn vị bài đăng trong câu thống kê (mặc định 'bài')")
    p.add_argument("--compact", action="store_true",
                   help="mỗi username in một dòng gọn: @username — Tên (Loại)")
    p.add_argument("--full", action="store_true",
                   help="in đầy đủ chi tiết từng username (mặc định khi có dữ liệu bài)")
    p.add_argument("--out", metavar="FILE",
                   help="ghi kết quả ra JSON (tên trần ⇒ nằm trong json_folder/)")
    return run(p.parse_args())


if __name__ == "__main__":
    sys.exit(main())
