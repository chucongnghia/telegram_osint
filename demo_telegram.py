#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""demo_telegram.py — thu thập bài đăng công khai từ Telegram **không cần API**.

Cách hoạt động: đọc trang xem trước công khai `https://t.me/s/<kênh>` (HTML tĩnh),
nên **chỉ dùng thư viện chuẩn của Python** — không cần `pip install`, không cần
`api_id`/`api_hash`, không cần đăng nhập.

    python3 demo_telegram.py --demo                       # offline, dữ liệu mẫu
    python3 demo_telegram.py --channel durov --limit 25   # kênh công khai (chạy thật)
    python3 demo_telegram.py --channel baomoi --channel tintucvtv --limit 20
    python3 demo_telegram.py --seed-file channels.txt     # mỗi dòng 1 kênh / link t.me
    python3 demo_telegram.py --report posts_tg.json       # in lại báo cáo từ JSON đã lưu

`--channel` nhận `durov`, `@durov`, `t.me/durov`, `https://t.me/s/durov`.
Tự phân trang bằng `?before=<message_id>` (mỗi trang ~20 bài) và chống trùng theo
khóa `kênh/message_id`.

Giới hạn: **không** xem được group kín / kênh riêng tư, không tải media, không OCR —
dùng `demo_telegram_api.py` cho những việc đó.
Bộ từ điển + chấm điểm + phần in báo cáo nằm ở `analyzer.py` (dùng chung 2 bản).
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from analyzer import (NOUN_WEB, RE_HASHTAG, analyze, demo_posts, normalize,
                      print_history, print_report, write_json)

PREVIEW_URL = "https://t.me/s/{channel}"
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")
TIMEOUT = 25
PAGE_DELAY = 0.6            # nghỉ giữa các trang cho lịch sự
BACKEND = "web (t.me/s)"

TAG_RE = re.compile(r"<[^>]+>")
BR_RE = re.compile(r"<br\s*/?>", re.I)
BLOCK_END_RE = re.compile(r"</(?:p|div|blockquote)>", re.I)
WS_RE = re.compile(r"[ \t\u00a0]+")


# ---------------------------------------------------------------------------
# 1. Tải trang
# ---------------------------------------------------------------------------

def fetch(url: str) -> str:
    """Tải URL và trả HTML (báo lỗi rõ ràng nếu mạng/HTTP có vấn đề)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept-Language": "vi,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} khi tải {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Không kết nối được tới {url} ({exc.reason}). "
                           "Kiểm tra mạng/proxy.") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"Hết thời gian chờ khi tải {url}") from exc
    return raw.decode(charset, "replace")


# ---------------------------------------------------------------------------
# 2. Phân tích HTML của trang xem trước
# ---------------------------------------------------------------------------

def channel_name(raw: str) -> str:
    """Chuẩn hoá tên kênh: 'durov', '@durov', 't.me/durov', 'https://t.me/s/durov'."""
    name = (raw or "").strip().rstrip("/")
    if not name:
        raise RuntimeError("Tên kênh rỗng")
    if "+" in name or "joinchat" in name.lower():
        raise RuntimeError(
            f"'{raw}' là link mời nhóm kín — bản web KHÔNG xem được. "
            "Dùng demo_telegram_api.py (Telethon) và đảm bảo tài khoản đã tham gia nhóm.")
    if "://" in name:
        name = urllib.parse.urlparse(name).path
    name = name.split("?")[0]
    for prefix in ("/s/", "s/"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    name = name.strip("/")
    if name.startswith("t.me/"):
        name = name[len("t.me/"):]
    elif name.startswith("telegram.me/"):
        name = name[len("telegram.me/"):]
    else:
        # 'durov' hoặc '@durov' hoặc 'durov/123' (bỏ phần số bài nếu có)
        name = name.split("/")[0]
    name = name.lstrip("@").strip()
    if not name or not re.match(r"^[A-Za-z0-9_]{3,}$", name):
        raise RuntimeError(
            f"Tên kênh không hợp lệ: '{raw}'. Ví dụ đúng: durov, @durov, t.me/durov.")
    return name


def _text_of(fragment: str) -> str:
    """Chuyển một đoạn HTML thành văn bản thuần (giữ xuống dòng rồi gộp lại)."""
    if not fragment:
        return ""
    s = BR_RE.sub("\n", fragment)
    s = BLOCK_END_RE.sub("\n", s)
    s = TAG_RE.sub("", s)
    s = html.unescape(s)
    s = WS_RE.sub(" ", s)
    return normalize(s)


def parse_count(text: str) -> int:
    """'10.4M' -> 10400000, '17.3K' -> 17300, '1 234' -> 1234."""
    t = (text or "").strip().replace("\u00a0", "").replace(" ", "")
    m = re.match(r"^([\d.,]+)\s*([KMB]?)$", t, re.I)
    if not m:
        return 0
    num = m.group(1).replace(",", "")
    try:
        value = float(num)
    except ValueError:
        return 0
    mult = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[m.group(2).upper()]
    return int(value * mult)


def parse_channel_info(page: str, fallback: str) -> dict:
    """Lấy tên kênh, mô tả, số người đăng ký từ trang xem trước."""
    def grab(pattern: str) -> str:
        m = re.search(pattern, page, re.S)
        return html.unescape(m.group(1)).strip() if m else ""

    title = _text_of(grab(r'class="tgme_channel_info_header_title"[^>]*>(.*?)</div>'))
    desc = _text_of(grab(r'class="tgme_channel_info_description"[^>]*>(.*?)</div>'))
    username = grab(r'class="tgme_channel_info_header_username"[^>]*>\s*<a[^>]*>@?([^<]*)</a>')
    subs = 0
    for value, kind in re.findall(
            r'class="counter_value"[^>]*>([^<]*)</span>\s*<span class="counter_type"[^>]*>([^<]*)</span>',
            page):
        if kind.strip().lower() == "subscribers":
            subs = parse_count(value)
            break
    return {
        "channel": (username or fallback).lstrip("@").strip() or fallback,
        "channel_title": title or fallback,
        "channel_description": desc,
        "subscribers": subs,
    }


def media_types_of(block: str) -> list[str]:
    """Đoán loại media trong một bài (ảnh/video/tài liệu/âm thanh/sticker)."""
    kinds: list[str] = []
    kinds += ["photo"] * len(re.findall(r'tgme_widget_message_photo_wrap', block))
    kinds += ["video"] * len(re.findall(r'js-message_video_player', block))
    kinds += ["document"] * len(re.findall(r'tgme_widget_message_document_wrap', block))
    kinds += ["audio"] * len(re.findall(r'tgme_widget_message_(?:voice|audio)', block))
    kinds += ["sticker"] * len(re.findall(r'tgme_widget_message_sticker', block))
    return kinds


def parse_messages(page: str, info: dict) -> list[dict]:
    """Tách từng bài đăng trong một trang xem trước."""
    marks = list(re.finditer(r'<div class="tgme_widget_message[^"]*"\s+data-post="([^"]+)"', page))
    records: list[dict] = []
    for i, m in enumerate(marks):
        block = page[m.start(): marks[i + 1].start() if i + 1 < len(marks) else len(page)]
        data_post = m.group(1)
        if "/" not in data_post:
            continue
        channel, message_id = data_post.rsplit("/", 1)
        if not message_id.isdigit():
            continue

        text_m = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.S)
        text = _text_of(text_m.group(1)) if text_m else ""

        views_m = re.search(r'class="tgme_widget_message_views"[^>]*>([^<]*)</span>', block)
        views = parse_count(views_m.group(1)) if views_m else 0

        date_m = re.search(r'<time[^>]*datetime="([^"]+)"', block)
        posted_at = date_m.group(1).strip() if date_m else None

        author_m = re.search(r'class="tgme_widget_message_owner_name"[^>]*>'
                             r'(?:<span[^>]*>)?([^<]*)', block)
        author = _text_of(author_m.group(1)) if author_m else info["channel_title"]

        fwd_m = re.search(r'class="tgme_widget_message_forwarded_from_name"[^>]*>'
                          r'(?:<span[^>]*>)?([^<]*)', block)
        forwarded_from = _text_of(fwd_m.group(1)) or None if fwd_m else None

        preview_m = re.search(r'class="tgme_widget_message_link_preview"[^>]*href="([^"]*)"', block)
        media_types = media_types_of(block)
        records.append({
            "message_id": message_id,
            "url": f"https://t.me/{channel}/{message_id}",
            "channel": info["channel"] or channel,
            "channel_title": info["channel_title"],
            "subscribers": info["subscribers"],
            "text": text,
            "hashtags": list(dict.fromkeys(h.lower() for h in RE_HASHTAG.findall(text))),
            "posted_at": posted_at,
            "views": views,
            "author": author or info["channel_title"],
            "forwarded_from": forwarded_from,
            "has_media": bool(media_types),
            "media_types": media_types,
            "media_count": len(media_types),
            "link_preview": html.unescape(preview_m.group(1)) if preview_m else "",
            "source": "telegram",
            "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
            "pipeline_status": {"collected": True, "downloaded": False, "audio_extracted": False,
                                "transcribed": False, "frames_extracted": False, "ocr_done": False},
        })
    return records


def scrape_channel(name: str, limit: int, progress=print) -> tuple[list[dict], dict]:
    """Lấy tối đa `limit` bài mới nhất của một kênh công khai (có phân trang)."""
    info: dict = {}
    records: list[dict] = []
    seen: set[str] = set()
    before: str | None = None
    page_no = 0

    while len(records) < limit:
        page_no += 1
        url = PREVIEW_URL.format(channel=name)
        if before:
            url += f"?before={before}"
        try:
            page = fetch(url)
        except RuntimeError as exc:
            if page_no == 1:
                raise
            progress(f"    ! trang {page_no}: {exc} — dừng phân trang.")
            break

        if page_no == 1:
            info = parse_channel_info(page, name)
            if not re.search(r"tgme_channel_info|tgme_widget_message", page):
                raise RuntimeError(
                    f"Kênh '@{name}' không có bản xem trước công khai (hoặc không tồn tại).\n"
                    "      Nguyên nhân thường gặp: kênh riêng tư/đã bị khoá, hoặc sai tên kênh.\n"
                    "      Kênh công khai thử được: durov, telegram, baomoi, tintucvtv.")
            progress(f"  [web] {info['channel_title']} (@{info['channel']}) — "
                     f"{info['subscribers']:,} người đăng ký".replace(",", "."))

        found = parse_messages(page, info)
        new = [r for r in found if r["message_id"] not in seen]
        for r in new:
            seen.add(r["message_id"])
        records += new
        progress(f"    trang {page_no}: {len(found)} bài ({len(new)} bài mới), "
                 f"tổng {len(records)}")

        if not new:
            break
        before_m = re.findall(r'data-before="(\d+)"', page)
        if not before_m:
            break
        before = before_m[-1]
        if PAGE_DELAY:
            time.sleep(PAGE_DELAY)

    return records[:limit], info


# ---------------------------------------------------------------------------
# 3. main
# ---------------------------------------------------------------------------

def read_seed_file(path: str) -> list[str]:
    """Đọc danh sách kênh: mỗi dòng 1 kênh hoặc link t.me; bỏ dòng trống/`#`."""
    out: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
    return out


def main() -> int:
    p = argparse.ArgumentParser(
        description="Thu thập bài đăng Telegram công khai qua trang web t.me/s (chỉ thư viện chuẩn).",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--demo", action="store_true",
                   help="chạy dữ liệu mẫu offline (không cần mạng)")
    p.add_argument("--channel", action="append", metavar="KENH",
                   help="kênh công khai: durov, @durov, t.me/durov (lặp lại được)")
    p.add_argument("--seed-file", metavar="FILE",
                   help="file danh sách kênh, mỗi dòng 1 kênh hoặc link t.me")
    p.add_argument("--limit", type=int, default=20,
                   help="số bài tối đa cho MỖI kênh (mặc định 20)")
    p.add_argument("--out", default="posts_web.json", help="file JSON kết quả")
    p.add_argument("--no-report", action="store_true",
                   help="chỉ IN DANH SÁCH bài đã lấy, không in báo cáo thống kê")
    p.add_argument("--report", metavar="JSON", help="chỉ in báo cáo từ file JSON đã lưu")
    args = p.parse_args()

    if args.report:
        with open(args.report, encoding="utf-8") as fh:
            payload = json.load(fh)
        records = payload.get("posts", []) if isinstance(payload, dict) else payload
        if not records:
            print(f"[!] Không có bài đăng nào trong {args.report}", file=sys.stderr)
            return 1
        print_report(records, [args.report], title="BÁO CÁO THU THẬP TELEGRAM (bản web)",
                     noun=NOUN_WEB)
        return 0

    targets: list[str] = list(args.channel or [])
    if args.seed_file:
        try:
            targets += read_seed_file(args.seed_file)
        except OSError as exc:
            print(f"[!] Không đọc được {args.seed_file}: {exc}", file=sys.stderr)
            return 2

    if args.demo or not targets:
        records = demo_posts(args.limit, dialect="web")
        for rec in records:
            rec["analysis"] = analyze(rec)
        out = write_json(args.out, records, "demo (offline)", noun=NOUN_WEB)
        sources = ["dữ liệu mẫu offline (không cần mạng)"]
        if args.no_report:
            print_history(out, sources, noun=NOUN_WEB)
        else:
            print_report(out, sources, title="BÁO CÁO THU THẬP TELEGRAM (bản web)", noun=NOUN_WEB)
        return 0

    records: list[dict] = []
    sources: list[str] = []
    seen: set[str] = set()
    errors = 0
    for raw in targets:
        try:
            name = channel_name(raw)
        except RuntimeError as exc:
            print(f"[!] {exc}", file=sys.stderr)
            errors += 1
            continue
        sources.append(f"kênh @{name}")
        try:
            found, _info = scrape_channel(name, args.limit)
        except RuntimeError as exc:
            print(f"[!] {exc}", file=sys.stderr)
            errors += 1
            continue
        for rec in found:
            key = f"{rec['channel']}/{rec['message_id']}"
            if key not in seen:
                seen.add(key)
                records.append(rec)

    if not records:
        print("[!] Không thu thập được bài đăng nào.", file=sys.stderr)
        return 1

    for rec in records:
        rec["analysis"] = analyze(rec)
    records.sort(key=lambda r: -((r.get("analysis") or {}).get("scam_score", 0)))

    out = write_json(args.out, records, BACKEND, noun=NOUN_WEB)
    if args.no_report:
        print_history(out, sources, noun=NOUN_WEB)
    else:
        print_report(out, sources, title="BÁO CÁO THU THẬP TELEGRAM (bản web)", noun=NOUN_WEB)
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
