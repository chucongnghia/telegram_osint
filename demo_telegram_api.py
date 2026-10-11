#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""

Công cụ dùng: **Telethon** (thư viện chuẩn thức của Telegram cho Python) + tuỳ chọn
**pytesseract** (OCR) và **Pillow** (mở ảnh). Tất cả đều là thư viện ngoài, cài bằng pip.

Chuẩn bị (một lần)
-----------------
1. Vào https://my.telegram.org → API development tools → lấy `api_id` và `api_hash`.
2. Cài thư viện và đăng nhập:
       pip install telethon
       python3 demo_telegram_api.py --login
   (nhập số điện thoại + mã OTP Telegram gửi về app; phiên được lưu vào tg_session.session)

Cách dùng
---------
    python3 demo_telegram_api.py --demo                      # offline, không cần credential
    python3 demo_telegram_api.py --channel durov --limit 30  # kênh công khai, qua API
    python3 demo_telegram_api.py --channel @tennhom --limit 100   # group kín (bạn đã tham gia)
    python3 demo_telegram_api.py --search "đầu tư lợi nhuận" --limit 50
    python3 demo_telegram_api.py --channel kenh --media-dir media --ocr
    python3 demo_telegram_api.py --report posts_api.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# 0. Import Telethon (báo lỗi rõ ràng nếu chưa cài)
# ---------------------------------------------------------------------------

try:
    from telethon import TelegramClient, functions, utils as tl_utils
    from telethon.errors import RPCError
    from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument, Channel, Chat, User
except ImportError:                                        # pragma: no cover
    TelegramClient = None
    RPCError = Exception

    def _missing():
        print("[!] Chưa cài Telethon. Cài bằng:\n"
              "      pip install telethon\n"
              "    Hoặc chạy bản không cần API: python3 demo_telegram.py --demo",
              file=sys.stderr)
        return 111
else:
    def _missing():
        return None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_PATH = os.path.join(SCRIPT_DIR, "tg_session")

# ---------------------------------------------------------------------------
# 1. Bộ từ điển + chấm điểm (giống demo_telegram.py, copy để 2 file độc lập)
# ---------------------------------------------------------------------------

SCAM_GROUPS = {
    "Investment": ("Đầu tư / lợi nhuận cao", 22, [
        "đầu tư", "lợi nhuận", "sinh lời", "cam kết lợi nhuận", "làm giàu",
        "kiếm tiền online", "thu nhập thụ động", "sàn đầu tư", "forex",
        "tiền ảo", "usdt", "gấp 3 lần", "gấp 5 lần", "x2 tài khoản",
        "kèo thơm", "bao lời", "cam kết hoàn vốn", "phiên tín hiệu",
    ]),
    "Job": ("Việc làm online giả", 18, [
        "việc làm online", "việc nhẹ lương cao", "tuyển cộng tác viên", "ctv",
        "làm tại nhà", "chốt đơn", "hoàn tiền", "làm nhiệm vụ",
        "không cần kinh nghiệm", "thu nhập 20 triệu", "tuyển gấp", "đa cấp",
        "tuyển đại lý", "cộng tác viên chốt đơn",
    ]),
    "Loan": ("Cho vay trực tuyến", 20, [
        "cho vay", "vay vốn", "vay nhanh", "vay tiền online", "không thế chấp",
        "giải ngân trong ngày", "chỉ cần cccd", "app vay", "lãi suất 0%",
        "vay tín chấp", "tín dụng đen", "bùng nợ", "hỗ trợ nợ xấu",
    ]),
    "Giveaway": ("Trúng thưởng / quà tặng", 16, [
        "trúng thưởng", "nhận quà", "quà tặng miễn phí", "tri ân khách hàng",
        "minigame", "quay số", "nhận thưởng", "phần thưởng", "airdrop",
        "chỉ cần bình luận", "chỉ cần chia sẻ", "like và share", "tặng code",
    ]),
    "Impersonation": ("Giả mạo tổ chức / cá nhân", 20, [
        "giả mạo", "chính chủ", "hotline chính thức", "công an", "tòa án",
        "thuế", "shipper", "nhân viên ngân hàng", "chuyên gia tài chính",
        "mentor", "fanpage chính thức", "tổng đài", "admin chính thức",
    ]),
    "Shopping": ("Mua sắm / bán hàng đáng ngờ", 14, [
        "sale sốc", "giảm giá 90%", "thanh lý", "xả kho", "cọc trước",
        "chuyển khoản trước", "không ship cod", "sỉ giá gốc",
    ]),
}

ACTION_SIGNALS = {
    "money_transfer": ("Yêu cầu chuyển tiền", 20, [
        "chuyển khoản", "cọc trước", "nạp tiền", "nộp phí", "phí xác nhận",
        "stk", "số tài khoản", "momo", "zalopay", "ví điện tử", "nạp usdt",
    ]),
    "commission": ("Hứa hẹn hoa hồng", 14, [
        "hoa hồng", "chiết khấu cao", "thưởng nóng", "giới thiệu người", "ref",
    ]),
    "offplatform": ("Điều hướng sang kênh/nhóm khác", 18, [
        "link bio", "telegram", "zalo", "whatsapp", "inbox", "ib",
        "link đăng ký", "truy cập link", "tải app", "nhóm kín", "sđt",
        "số điện thoại", "join nhóm", "vào nhóm", "add tele",
    ]),
    "urgency": ("Thúc ép / khan hiếm giả tạo", 10, [
        "duy nhất hôm nay", "chỉ còn", "nhanh tay", "số lượng có hạn",
        "cơ hội cuối", "đừng bỏ lỡ", "trong 24h", "slot cuối",
    ]),
}

THRESHOLD_SUSPICIOUS, THRESHOLD_HIGH_RISK = 25, 60

RE_URL = re.compile(r"(https?://[^\s<>\"]+|www\.[^\s<>\"]+|[a-z0-9\-]+\.(?:com|net|vn|org|io|xyz|top|info|site|online|me|app|pro)(?:/[^\s<>\"]*)?)", re.I)
RE_PHONE = re.compile(r"(?<!\d)(?:\+84|0)(?:3|5|7|8|9)\d{8}(?!\d)")
RE_BANK_ACCOUNT = re.compile(r"(?<!\d)\d{9,16}(?!\d)")
RE_HASHTAG = re.compile(r"#([\w\u00C0-\u1EF9]{2,})", re.U)
RE_MENTION = re.compile(r"@([A-Za-z][\w]{3,31})", re.U)
RE_TG_INVITE = re.compile(r"(?:https?://)?t\.me/(?:\+[\w-]+|joinchat/[\w-]+)", re.I)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", str(text or ""))
    text = text.replace("\u200b", " ").replace("\ufeff", "")
    return re.sub(r"\s+", " ", re.sub(r"[\r\n\t]+", " ", text)).strip().lower()


def no_accent(text: str) -> str:
    t = unicodedata.normalize("NFD", text)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return unicodedata.normalize("NFC", t.replace("đ", "d").replace("Đ", "D"))


def extract_entities(text: str) -> dict:
    urls = []
    for u in RE_URL.findall(text or ""):
        u = u.rstrip(".,;)")
        if u.lower() not in [x.lower() for x in urls]:
            urls.append(u)
    phones = list(dict.fromkeys(RE_PHONE.findall(text or "")))
    accounts = [a for a in dict.fromkeys(RE_BANK_ACCOUNT.findall(text or "")) if a not in phones]
    return {
        "urls": urls[:6],
        "phones": phones[:5],
        "bank_accounts": accounts[:5],
        "mentions": list(dict.fromkeys(RE_MENTION.findall(text or "")))[:6],
        "invite_links": list(dict.fromkeys(RE_TG_INVITE.findall(text or "")))[:5],
    }


def analyze(post: dict, ocr_text: str = "") -> dict:
    """Chấm điểm nghi ngờ 0-100. `ocr_text`: chữ đọc được từ ảnh (nếu bật --ocr)."""
    text = post.get("text") or ""
    haystack = normalize(" ".join([text, ocr_text or "",
                                   post.get("channel_title") or "", post.get("channel") or ""]))
    plain = no_accent(haystack)

    def hits(keywords):
        return [k for k in keywords if k in haystack or no_accent(k) in plain]

    score, signals, group_scores = 0.0, [], {}

    for label, (name_vi, weight, keywords) in SCAM_GROUPS.items():
        found = hits(keywords)
        if found:
            pts = weight * min(1.0, 0.6 + 0.2 * (len(found) - 1))
            group_scores[label] = pts
            signals.append((name_vi, pts, found[:6]))

    if group_scores:
        ranked = sorted(group_scores.items(), key=lambda kv: -kv[1])
        score += ranked[0][1] + sum(p * 0.5 for _, p in ranked[1:3])

    for name_vi, weight, keywords in ACTION_SIGNALS.values():
        found = hits(keywords)
        if found:
            pts = weight * min(1.0, 0.6 + 0.2 * (len(found) - 1))
            score += pts
            signals.append((name_vi, pts, found[:6]))

    ent = extract_entities(text)
    if post.get("link_preview"):
        ent["urls"] = list(dict.fromkeys(ent["urls"] + [post["link_preview"]]))
    if ocr_text:
        ocr_ent = extract_entities(ocr_text)
        for key in ("urls", "phones", "bank_accounts", "invite_links"):
            ent[key] = list(dict.fromkeys(ent[key] + ocr_ent[key]))
    if ent["urls"]:
        score += 12
        signals.append(("Có đường link ngoài nền tảng", 12.0, ent["urls"]))
    if ent["phones"] or ent["bank_accounts"]:
        score += 15
        signals.append(("Số điện thoại / số tài khoản trong bài", 15.0,
                        (ent["phones"] + ent["bank_accounts"])[:4]))
    if ent["invite_links"]:
        score += 12
        signals.append(("Link mời vào nhóm/kênh riêng (t.me/+...)", 12.0, ent["invite_links"]))
    if post.get("forwarded_from"):
        score += 6
        signals.append(("Bài đăng forward từ nguồn khác", 6.0, [post["forwarded_from"]]))
    if post.get("has_media") and any(k in haystack for k in ("kết quả", "lệnh", "lợi nhuận", "biến động")):
        score += 5
        signals.append(("Ảnh/video kèm nội dung tài chính", 5.0, ["media + nội dung tài chính"]))
    if ocr_text:
        score += 5
        signals.append(("Có chữ đọc được từ ảnh (OCR)", 5.0, [ocr_text[:80]]))

    score = int(round(min(100.0, score)))
    level = ("High-risk" if score >= THRESHOLD_HIGH_RISK
             else "Suspicious" if score >= THRESHOLD_SUSPICIOUS else "Normal")
    category = None
    if group_scores and level != "Normal":
        category = max(group_scores.items(), key=lambda kv: kv[1])[0]

    top = sorted(signals, key=lambda s: -s[1])[:3]
    summary = ("Nhóm nghi ngờ: " + SCAM_GROUPS[category][0] if category
               else "Không có nhóm rõ ràng")
    if top:
        summary += " | Tín hiệu chính: " + "; ".join(n for n, _, _ in top)
    else:
        summary = "Không phát hiện tín hiệu đáng ngờ."

    return {
        "scam_score": score, "level": level,
        "scam_category": category,
        "scam_category_vi": SCAM_GROUPS[category][0] if category else None,
        "signals": [{"label_vi": n, "points": round(p, 1), "matched": m} for n, p, m in
                    sorted(signals, key=lambda s: -s[1])],
        "entities": ent, "summary": summary,
    }


# ---------------------------------------------------------------------------
# 2. OCR (tuỳ chọn) — đọc chữ trong ảnh để tìm SĐT / STK / link
# ---------------------------------------------------------------------------

_OCR_ENGINE = None


def ocr_available() -> bool:
    try:
        import PIL  # noqa: F401
        try:
            import pytesseract  # noqa: F401
            return True
        except ImportError:
            import easyocr  # noqa: F401
            return True
    except ImportError:
        return False


def ocr_image(path: str) -> str:
    """Đọc chữ trong ảnh. Trả chuỗi rỗng nếu thiếu thư viện (không làm sập chương trình)."""
    global _OCR_ENGINE
    try:
        from PIL import Image
    except ImportError:
        print("    ! Bỏ qua OCR: chưa có Pillow (pip install pillow)")
        return ""
    try:
        import pytesseract
        return normalize(pytesseract.image_to_string(Image.open(path), lang="vie+eng"))
    except ImportError:
        pass
    except Exception as exc:                       # thiếu binary tesseract, ảnh lỗi...
        print(f"    ! pytesseract lỗi ({type(exc).__name__}), thử easyocr...")
    try:
        import easyocr
        if _OCR_ENGINE is None:
            _OCR_ENGINE = easyocr.Reader(["vi", "en"], gpu=False, verbose=False)
        lines = _OCR_ENGINE.readtext(path, detail=0)
        return normalize(" ".join(lines))
    except ImportError:
        print("    ! Bỏ qua OCR: cài `pip install pytesseract pillow` (kèm apt install tesseract-ocr) "
              "hoặc `pip install easyocr`")
        return ""
    except Exception as exc:
        print(f"    ! OCR lỗi: {type(exc).__name__}: {exc}")
        return ""


# ---------------------------------------------------------------------------
# 3. Credential + kết nối
# ---------------------------------------------------------------------------

def load_credentials(args) -> tuple[int, str]:
    """Lấy api_id/api_hash từ: tham số dòng lệnh → biến môi trường → file tg_api.json."""
    api_id = args.api_id or os.environ.get("TG_API_ID")
    api_hash = args.api_hash or os.environ.get("TG_API_HASH")
    cfg = os.path.join(SCRIPT_DIR, "tg_api.json")
    if (not api_id or not api_hash) and os.path.exists(cfg):
        try:
            with open(cfg, encoding="utf-8") as fh:
                data = json.load(fh)
            api_id = api_id or data.get("api_id")
            api_hash = api_hash or data.get("api_hash")
        except (OSError, json.JSONDecodeError):
            pass
    return int(api_id or 0), (api_hash or "")


def credentials_help() -> str:
    return (
        "Chưa có api_id / api_hash. Cách lấy (miễn phí, 2 phút):\n"
        "  1. Mở https://my.telegram.org  → đăng nhập bằng số điện thoại Telegram\n"
        "  2. Vào 'API development tools' → tạo app → copy api_id và api_hash\n"
        "  3. Truyền vào chương trình theo một trong ba cách:\n"
        "       python3 demo_telegram_api.py --login --api-id 123456 --api-hash abcdef...\n"
        "       export TG_API_ID=123456 TG_API_HASH=abcdef...\n"
        "       hoặc tạo file tg_api.json: {\"api_id\": 123456, \"api_hash\": \"abcdef...\"}\n"
        "  Lần đầu chạy sẽ hỏi số điện thoại + mã OTP; phiên được lưu vào tg_session.session.\n"
        "  Không muốn dùng API? Chạy bản web: python3 demo_telegram.py --channel <kênh>"
    )


async def open_client(api_id: int, api_hash: str, interactive: bool = True):
    """Mở client. Trả (client, None) nếu OK, (None, thông báo lỗi) nếu không."""
    client = TelegramClient(SESSION_PATH, api_id, api_hash)
    try:
        await client.connect()
    except Exception as exc:
        return None, f"Không kết nối được tới Telegram: {type(exc).__name__}: {exc}"
    if not await client.is_user_authorized():
        if not interactive:
            await client.disconnect()
            return None, ("Phiên chưa đăng nhập. Chạy trước:  "
                          "python3 demo_telegram_api.py --login")
        try:
            print("[i] Chưa đăng nhập — bắt đầu đăng nhập (một lần)...")
            await client.start()
        except Exception as exc:
            await client.disconnect()
            return None, f"Đăng nhập thất bại: {type(exc).__name__}: {exc}"
    return client, None


# ---------------------------------------------------------------------------
# 4. Chuyển message của Telethon -> record thống nhất
# ---------------------------------------------------------------------------

def media_kind(msg) -> tuple[bool, list[str], str]:
    """Trả (có media?, loại media, tên file gợi ý)."""
    if not msg.media:
        return False, [], ""
    if isinstance(msg.media, MessageMediaPhoto):
        return True, ["photo"], "photo.jpg"
    if isinstance(msg.media, MessageMediaDocument):
        doc = msg.media.document
        mime = getattr(doc, "mime_type", "") or ""
        if mime.startswith("video"):
            return True, ["video"], "video.mp4"
        if mime.startswith("audio"):
            return True, ["audio"], "audio.mp3"
        if mime.startswith("image"):
            return True, ["photo"], "image.jpg"
        return True, ["document"], "document.bin"
    return True, ["other"], "media.bin"


def extract_link_preview(msg) -> str:
    try:
        for ent in (msg.entities or []):
            if type(ent).__name__ == "MessageEntityTextUrl":
                return ent.url
        if getattr(msg, "web_preview", None) is not None:
            return getattr(msg.web_preview, "url", "") or ""
    except Exception:
        pass
    return ""


def to_record(msg, entity) -> dict:
    text = normalize(msg.message or "")
    has_media, kinds, _ = media_kind(msg)
    fwd_name = ""
    if getattr(msg, "forward", None) is not None:
        fwd = msg.forward
        fwd_name = (getattr(fwd, "from_name", None) or getattr(fwd, "chat", None)
                    and getattr(getattr(fwd, "chat", None), "title", None)) or "nguồn ẩn"
    username = getattr(entity, "username", None) or ""
    title = getattr(entity, "title", None) or username or str(getattr(entity, "id", ""))
    return {
        "message_id": str(msg.id),
        "url": f"https://t.me/{username}/{msg.id}" if username else "",
        "channel": username or str(getattr(entity, "id", "")),
        "channel_title": title,
        "text": text,
        "hashtags": list(dict.fromkeys(h.lower() for h in RE_HASHTAG.findall(text))),
        "posted_at": msg.date.replace(tzinfo=timezone.utc).isoformat() if msg.date else None,
        "views": getattr(msg, "views", 0) or 0,
        "forwards": getattr(msg, "forwards", 0) or 0,
        "replies": (getattr(msg.replies, "replies", 0) if getattr(msg, "replies", None) else 0),
        "author": title,
        "forwarded_from": fwd_name or None,
        "has_media": has_media,
        "media_types": kinds,
        "media_count": len(kinds),
        "link_preview": extract_link_preview(msg),
        "source": "telegram-api",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_status": {"collected": True, "downloaded": False, "audio_extracted": False,
                            "transcribed": False, "frames_extracted": False, "ocr_done": False},
        "_msg_ref": msg,            # giữ để tải media; bị loại bỏ trước khi ghi JSON
    }


def json_ready(record: dict) -> dict:
    """Bỏ các đối tượng không serialize được (message của Telethon)."""
    return {k: v for k, v in record.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# 5. Các chế độ thu thập
# ---------------------------------------------------------------------------

async def fetch_history(client, target: str, limit: int, progress=print) -> list[dict]:
    """Lấy `limit` bài mới nhất của một kênh/nhóm (kể cả group kín nếu đã tham gia)."""
    try:
        entity = await client.get_entity(target)
    except Exception as exc:
        raise RuntimeError(
            f"Không truy cập được '{target}' ({type(exc).__name__}: {exc}).\n"
            "      Kiểm tra: tên/ID đúng chưa, tài khoản đã tham gia nhóm đó chưa, "
            "và đã --login chưa."
        )
    title = getattr(entity, "title", None) or getattr(entity, "username", target)
    print(f"  [history] {title}  (id={getattr(entity, 'id', '?')}, "
          f"loại={type(entity).__name__})")
    records = []
    async for msg in client.iter_messages(entity, limit=limit):
        if not msg or msg.action:            # bỏ thông báo hệ thống (join, pin...)
            continue
        records.append(to_record(msg, entity))
    records.sort(key=lambda r: r["posted_at"] or "")
    print(f"    lấy được {len(records)} tin nhắn")
    return records


async def search_global(client, keyword: str, limit: int, per_chat: int = 20) -> list[dict]:
    """Tìm kiếm toàn cục theo từ khóa (chỉ trong các chat tài khoản tham gia)."""
    print(f"  [search] toàn cục: '{keyword}'")
    try:
        found = await client(functions.contacts.SearchRequest(q=keyword, limit=limit))
    except Exception as exc:
        raise RuntimeError(
            f"Tìm kiếm thất bại ({type(exc).__name__}: {exc}).\n"
            "      Lưu ý: contacts.Search chỉ tìm trong các chat bạn đã tham gia, "
            "và cần phiên đã đăng nhập."
        )
    chat_names = [getattr(c, "title", None) or getattr(c, "username", "?") for c in found.chats]
    print(f"    tìm thấy {len(chat_names)} chat: {', '.join(chat_names[:8])}"
          f"{' ...' if len(chat_names) > 8 else ''}")
    records: list[dict] = []
    for chat in found.chats:
        if len(records) >= limit:
            break
        try:
            entity = await client.get_entity(chat)
            async for msg in client.iter_messages(entity, limit=per_chat, search=keyword):
                if not msg or msg.action:
                    continue
                records.append(to_record(msg, entity))
        except Exception as exc:
            print(f"    ! bỏ qua {getattr(chat, 'title', chat)}: {type(exc).__name__}")
    records.sort(key=lambda r: -(r["analysis"]["scam_score"] if "analysis" in r else 0))
    return records[:limit]


async def download_media(client, records: list[dict], media_dir: str, do_ocr: bool) -> None:
    """Tải ảnh/video của các bài về máy, tuỳ chọn OCR ảnh."""
    os.makedirs(media_dir, exist_ok=True)
    ok = fail = 0
    for rec in records:
        msg = rec.get("_msg_ref")
        if not msg or not rec["has_media"]:
            continue
        try:
            path = await client.download_media(msg, file=os.path.join(media_dir, rec["message_id"]))
        except Exception as exc:
            fail += 1
            print(f"    ! tải media {rec['message_id']} lỗi: {type(exc).__name__}")
            continue
        if not path:
            continue
        ok += 1
        rec["media_path"] = path
        rec["pipeline_status"]["downloaded"] = True
        print(f"    ↓ {os.path.basename(path)}")
        if do_ocr and path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            txt = ocr_image(path)
            if txt:
                rec["ocr_text"] = txt
                rec["pipeline_status"]["ocr_done"] = True
                rec["analysis"] = analyze(rec, ocr_text=txt)   # chấm lại điểm có OCR
                print(f"      OCR: {txt[:70]}")
    print(f"  [media] tải được {ok} file, lỗi {fail} → {media_dir}")






# ---------------------------------------------------------------------------
# Dữ liệu mẫu offline cho --demo (không cần mạng, không cần tài khoản).
# Cấu trúc: (username, tên hiển thị, số người đăng ký, [tin nhắn])
# mỗi tin: (nội dung, số media, có forward?, lượt xem)
# 5 kênh đầu là kênh lừa đảo, 3 kênh cuối là kênh sạch (để kiểm chứng
# công cụ không báo động giả).
# ---------------------------------------------------------------------------

DEMO_ITEMS = [
    ("dautu.sieuloinhuan", "Đầu tư tài chính - Lợi nhuận 30%/tháng", 48200, [
        ("Cam kết lợi nhuận 30%/tháng, đầu tư chỉ từ 2 triệu, rút tiền trong ngày! "
         "Anh em vào nhóm kín để nhận kèo: https://t.me/+AbCdEf123 #dautu #loinhuan", 2, True, 1446),
        ("Kết quả tuần này: 12 lệnh thắng liên tiếp, tài khoản x2 sau 7 ngày. "
         "Nạp tối thiểu 5 triệu, hoa hồng giới thiệu 20%. Inbox @admin_dautu_pro", 3, False, 3856),
        ("Chỉ còn 5 slot cuối cho gói VIP tháng 11, cam kết hoàn vốn 100%. "
         "Chuyển khoản STK 1234567890 (Vietcombank) rồi báo admin để kích hoạt.", 1, False, 6266),
    ]),
    ("vieclam.ctv.hoantien", "Tuyển CTV chốt đơn - Hoàn tiền 100%", 96400, [
        ("Tuyển cộng tác viên chốt đơn tại nhà, không cần kinh nghiệm, thu nhập 20 triệu/tháng. "
         "Làm nhiệm vụ hoàn tiền 100% đơn hàng. Đăng ký: https://t.me/vieclam_bot", 1, True, 2892),
        ("Hướng dẫn làm nhiệm vụ: nạp 500k vào ví, chốt 3 đơn là rút được cả gốc lẫn lãi. "
         "Liên hệ zalo 0987654321 để được hỗ trợ.", 1, False, 7712),
    ]),
    ("vayvon.nhanh365", "Hỗ trợ vay vốn online", 27300, [
        ("Vay tiền online chỉ cần CCCD, không thế chấp, giải ngân trong ngày, lãi suất 0% tháng đầu. "
         "Đăng ký app vay: https://vay365.example.com/dangky", 2, False, 819),
        ("Hỗ trợ nợ xấu, bùng nợ vẫn vay được. Hồ sơ đơn giản, duyệt trong 5 phút. "
         "SĐT 0912345678 (call/zalo).", 1, False, 2184),
    ]),
    ("quatang.trian2026", "Sự kiện tri ân - Trúng thưởng", 143000, [
        ("Chúc mừng 100 khách hàng may mắn trúng thưởng iPhone 16 Pro Max! "
         "Chỉ cần chia sẻ bài viết và bình luận. Nhận quà miễn phí, số lượng có hạn.", 3, True, 4290),
        ("Danh sách trúng thưởng đã có, ai có tên thì chuyển phí vận chuyển 150k "
         "để nhận quà. STK: 9876543210 - Momo: 0987654321", 1, False, 11440),
    ]),
    ("hotro.nganhang.vn", "Hỗ trợ tài khoản ngân hàng", 8900, [
        ("Thông báo: tài khoản của bạn có giao dịch bất thường. Vui lòng xác nhận "
         "số tài khoản và chuyển phí xác nhận 500.000đ để mở lại dịch vụ. "
         "Hotline chính thức 0912345678.", 1, False, 267),
    ]),
    ("tintuc.congnghe", "Tin tức công nghệ", 245000, [
        ("Telegram ra mắt tính năng mới cho phép tìm kiếm trong kênh và nhóm, "
         "cùng nhiều cải tiến về bảo mật.", 1, False, 7350),
        ("Tổng hợp các mẫu điện thoại tầm trung đáng mua trong tháng này.", 2, False, 19600),
        ("Cách bật xác thực hai lớp để bảo vệ tài khoản Telegram của bạn.", 1, False, 31850),
    ]),
    ("hocpython.mienphi", "Học lập trình Python miễn phí", 31200, [
        ("Bài 1: Biến và kiểu dữ liệu trong Python. Tài liệu tham khảo ở phần bình luận.", 2, False, 936),
        ("Bài 2: Vòng lặp for/while, bài tập cuối bài có đáp án.", 1, False, 2496),
    ]),
    ("amthuc.mebap", "Ẩm thực mỗi ngày", 58700, [
        ("Công thức phở bò nước dùng trong 8 tiếng, chi tiết từng bước.", 3, False, 1761),
        ("Mẹo chọn thịt tươi ngon khi đi chợ.", 2, False, 4696),
    ]),
]


def demo_posts(limit: int) -> list[dict]:
    now = datetime.now(timezone.utc)
    out: list[dict] = []
    for ci, (channel, title, subs, messages) in enumerate(DEMO_ITEMS):
        for mi, (text, media_count, forwarded, views) in enumerate(messages):
            if len(out) >= limit:
                return out
            from datetime import timedelta
            posted = now - timedelta(days=ci, hours=mi * 3 + 1)
            text = normalize(text)
            out.append({
                "message_id": str(1000 + ci * 100 + mi),
                "url": f"https://t.me/{channel}/{1000 + ci * 100 + mi}",
                "channel": channel, "channel_title": title, "subscribers": subs,
                "text": text,
                "hashtags": list(dict.fromkeys(h.lower() for h in RE_HASHTAG.findall(text))),
                "posted_at": posted.isoformat(), "views": views, "forwards": views // 20,
                "replies": views // 40, "author": title,
                "forwarded_from": ("kênh ẩn danh" if forwarded else None),
                "has_media": media_count > 0, "media_types": ["photo"] * media_count,
                "media_count": media_count, "link_preview": "",
                "source": "demo", "collected_at": now.isoformat(),
                "_note": "DỮ LIỆU MẪU offline - không phải bài đăng thật",
                "pipeline_status": {"collected": True, "downloaded": False, "audio_extracted": False,
                                    "transcribed": False, "frames_extracted": False, "ocr_done": False},
            })
    return out


# ---------------------------------------------------------------------------
# 7. Báo cáo
# ---------------------------------------------------------------------------

def report(records: list[dict], sources: list[str] | None = None) -> None:
    total = len(records)
    levels = Counter(r["analysis"]["level"] for r in records)
    cats = Counter(r["analysis"]["scam_category_vi"] for r in records
                   if r["analysis"]["scam_category_vi"])
    channels = Counter(r["channel"] for r in records)
    line = "=" * 74

    print("\n" + line)
    print("BÁO CÁO THU THẬP TELEGRAM QUA API (Telethon)".center(74))
    if sources:
        print(("Nguồn: " + ", ".join(sources))[:74].center(74))
    print(line)
    print(f"\n[1] TỔNG QUAN: {total} bài đăng")
    for lv in ("Normal", "Suspicious", "High-risk"):
        print(f"    {lv:<12}: {levels.get(lv, 0)}")
    if total:
        print(f"    Cần rà soát : {sum(levels.get(l, 0) for l in ('Suspicious', 'High-risk')) / total * 100:.1f}%")

    print("\n[2] NHÓM LỪA ĐẢO (mức 2):")
    for name, n in (cats.most_common() or [("(không có)", 0)]):
        print(f"    {name:<34} {n:>3}  {'█' * n}")

    print("\n[3] TOP KÊNH:")
    for ch, n in channels.most_common(8):
        print(f"    {('@' + ch):<30} {n:>3}  {'█' * n}")

    print("\n[4] TOP BÀI ĐĂNG NGHI NGỜ CAO NHẤT:")
    for r in sorted(records, key=lambda x: -x["analysis"]["scam_score"])[:5]:
        a = r["analysis"]
        extra = " | OCR" if r.get("ocr_text") else ""
        print(f"    [{a['scam_score']:>3}/100 | {a['level']:<10}] @{r['channel']} "
              f"({a['scam_category_vi'] or 'không rõ nhóm'}){extra}")
        print(f"        {r['url']}")
        print(f"        {a['summary'][:160]}")
    print("\nLưu ý: điểm số là TÍN HIỆU HỖ TRỢ RÀ SOÁT, không phải kết luận.")
    print(line)


# ---------------------------------------------------------------------------
# 7b. In danh sách tin nhắn đã crawl (thay cho báo cáo thống kê)
# ---------------------------------------------------------------------------

def print_history(records: list[dict], sources: list[str] | None = None) -> None:
    """In từng tin nhắn đã crawl: id, thời gian, lượt xem, media, nội dung."""
    if sources:
        print("\n[i] Nguồn: " + ", ".join(sources))
    print(f"[i] Tổng: {len(records)} tin nhắn")
    print("-" * 78)
    for i, r in enumerate(records, 1):
        when = (r.get("posted_at") or "")[:19].replace("T", " ")
        media = ""
        if r.get("media_count"):
            kinds = list(dict.fromkeys(r.get("media_types") or []))
            label = ",".join(kinds) or "media"
            media = f" [{r['media_count']} {label}]" if r["media_count"] > 1 else f" [{label}]"
        extra = f" fwd={r['forwards']}" if r.get("forwards") else ""
        print(f"{i:>4}. {r['channel']}/{r['message_id']}  {when}  views={r.get('views', 0)}"
              f"{extra}{media}")
        if r.get("url"):
            print(f"      {r['url']}")
        text = (r.get("text") or "").strip()
        print(f"      {text[:400]}{' ...' if len(text) > 400 else ''}" if text
              else "      (không có nội dung chữ)")
    print("-" * 78)


# ---------------------------------------------------------------------------
# 8. main
# ---------------------------------------------------------------------------

async def run_async(args) -> int:
    missing = _missing()
    if missing:
        return missing

    api_id, api_hash = load_credentials(args)
    if not api_id or not api_hash:
        print("[!] " + credentials_help(), file=sys.stderr)
        return 2

    client, err = await open_client(api_id, api_hash, interactive=True)
    if err:
        print(f"[!] {err}", file=sys.stderr)
        print("     (Thiếu api_id/api_hash? Chạy --login trước.)", file=sys.stderr)
        return 2

    me = await client.get_me()
    print(f"[i] Đăng nhập: {getattr(me, 'first_name', '')} "
          f"(@{getattr(me, 'username', '') or getattr(me, 'id', '')})")

    if args.login:
        print("[i] Đăng nhập thành công. Phiên lưu tại: " + SESSION_PATH + ".session")
        await client.disconnect()
        return 0

    records: list[dict] = []
    sources: list[str] = []

    for target in args.channel or []:
        sources.append(f"kênh/nhóm {target}")
        try:
            records += await fetch_history(client, target, args.limit)
        except RuntimeError as exc:
            print(f"    ! {exc}")

    for kw in args.search or []:
        sources.append(f"tìm kiếm '{kw}'")
        try:
            found = await search_global(client, kw, args.limit)
            records += found
        except RuntimeError as exc:
            print(f"    ! {exc}")

    # chống trùng theo kênh/message_id (dùng cho cả 2 nguồn ở trên)
    unique: dict[str, dict] = {}
    for rec in records:
        unique.setdefault(f"{rec['channel']}/{rec['message_id']}", rec)
    records = list(unique.values())

    for rec in records:                       # chấm điểm (chưa có OCR)
        rec["analysis"] = analyze(rec)

    if args.media_dir:
        await download_media(client, records, args.media_dir, args.ocr)

    await client.disconnect()

    if not records:
        print("[!] Không thu thập được bài đăng nào.")
        return 1

    out = [{k: v for k, v in r.items() if not k.startswith("_")} for r in records]
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(),
                   "total": len(out), "backend": "telethon", "posts": out},
                  fh, ensure_ascii=False, indent=2)
    print(f"[i] Đã lưu {len(out)} bài đăng vào {args.out}")
    if args.no_report:
        print_history(out, sources)
    else:
        report(out, sources)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Demo thu thập bài đăng Telegram qua API (Telethon) + chấm điểm lừa đảo.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--demo", action="store_true",
                   help="chạy dữ liệu mẫu offline (không cần api_id/api_hash)")
    p.add_argument("--login", action="store_true",
                   help="đăng nhập một lần rồi lưu phiên (chạy trước khi thu thập)")
    p.add_argument("--api-id", type=int, help="api_id lấy từ my.telegram.org")
    p.add_argument("--api-hash", help="api_hash lấy từ my.telegram.org")
    p.add_argument("--channel", action="append", metavar="@KENH",
                   help="kênh công khai / group kín / ID (lặp lại được)")
    p.add_argument("--search", action="append", metavar="TUKHOA",
                   help="tìm kiếm toàn cục theo từ khóa (trong các chat đã tham gia)")
    p.add_argument("--media-dir", metavar="DIR",
                   help="tải ảnh/video về thư mục này")
    p.add_argument("--ocr", action="store_true",
                   help="đọc chữ trong ảnh (cần pytesseract+easyocr/Pillow), chấm lại điểm")
    p.add_argument("--limit", type=int, default=30, help="số bài tối đa mỗi nguồn (mặc định 30)")
    p.add_argument("--out", default="posts_api.json", help="file JSON kết quả")
    p.add_argument("--no-report", action="store_true",
                   help="chỉ IN DANH SÁCH tin nhắn đã crawl, không in báo cáo thống kê")
    p.add_argument("--report", metavar="JSON", help="chỉ in báo cáo từ file JSON đã lưu")
    args = p.parse_args()

    if args.report:
        with open(args.report, encoding="utf-8") as fh:
            report(json.load(fh)["posts"])
        return 0

    if not any([args.demo, args.channel, args.search, args.login]):
        args.demo = True      # không chọn nguồn nào -> chạy dữ liệu mẫu offline

    if args.demo:
        records = demo_posts(args.limit)
        for rec in records:
            rec["analysis"] = analyze(rec)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"generated_at": datetime.now(timezone.utc).isoformat(),
                       "total": len(records), "backend": "demo (offline)", "posts": records},
                      fh, ensure_ascii=False, indent=2)
        print(f"[i] Đã lưu {len(records)} bài đăng mẫu vào {args.out}")
        if args.no_report:
            print_history(records, ["dữ liệu mẫu offline (không cần API)"])
        else:
            report(records, ["dữ liệu mẫu offline (không cần API)"])
        return 0

    return asyncio.run(run_async(args))


if __name__ == "__main__":
    sys.exit(main())
