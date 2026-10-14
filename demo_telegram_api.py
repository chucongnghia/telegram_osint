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
    python3 demo_telegram_api.py --user durov --user @tennhom      # tra cứu username/ID
    python3 demo_telegram_api.py --search "đầu tư lợi nhuận" --limit 50
    python3 demo_telegram_api.py --channel kenh --media-dir media --ocr
    python3 demo_telegram_api.py --report posts_api.json
    python3 demo_telegram_api.py --login --session tk-phu    # phiên riêng: tg_session_tk-phu.session

Chế độ BOT (không cần số điện thoại, chỉ cần token từ @BotFather)
-----------------------------------------------------------------
    python3 demo_telegram_api.py --setup-bot <token>          # kiểm tra token + liệt kê group bot đang ở
    python3 demo_telegram_api.py --login-bot --bot-token <token>
    python3 demo_telegram_api.py --bot-token <token> --watch @tennhom --watch-once
    python3 demo_telegram_api.py --bot-token <token> --watch @tennhom --interval 30 --cycles 10

Bot **chỉ nhận tin nhắn mới** kể từ khi được thêm vào nhóm, **không đọc được lịch sử
cũ** và **không dùng được tìm kiếm toàn cục** (`--search`) — script sẽ tự cảnh báo.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

# Bộ từ điển + chấm điểm dùng chung cho cả hai backend (chi tiết: analyzer.py)
from analyzer import (NOUN_API, NOUN_WEB, RE_HASHTAG, USERNAME_RE, analyze,
                      group_summary, make_profile, normalize, print_group_section,
                      print_history, print_profile, print_report, print_username_payload,
                      print_username_section, username_payload, write_json)
from analyzer import demo_posts as demo_posts_shared

# ---------------------------------------------------------------------------
# 0. Import Telethon (báo lỗi rõ ràng nếu chưa cài)
# ---------------------------------------------------------------------------

try:
    from telethon import TelegramClient, events, functions, utils as tl_utils
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
DEFAULT_SESSION = "tg_session"          # phiên tài khoản người dùng
BOT_SESSION_PREFIX = "tg_bot"           # phiên bot
API_CONFIG_PATH = os.path.join(SCRIPT_DIR, "tg_api.json")


def session_path(name: str = "", bot: bool = False) -> str:
    """Đường dẫn phiên (không kèm đuôi .session).

    * rỗng            -> tg_session / tg_bot_session
    * "tk-phu"        -> tg_session_tk-phu / tg_bot_tk-phu
    * "tg_session_tk-phu" (tên đầy đủ) -> dùng nguyên tên, không ghép thêm tiền tố
    * "phu/x.session" -> dùng nguyên đường dẫn người dùng đưa
    """
    name = (name or "").strip()
    if name.endswith(".session"):
        name = name[: -len(".session")]
    if name and (os.sep in name or "/" in name):
        return name
    if not name:
        return os.path.join(SCRIPT_DIR, "tg_bot_session" if bot else DEFAULT_SESSION)
    full_default = "tg_bot_session" if bot else DEFAULT_SESSION
    if name in (full_default, BOT_SESSION_PREFIX) or name.startswith(f"{BOT_SESSION_PREFIX}_") \
            or name.startswith(f"{DEFAULT_SESSION}_"):
        return os.path.join(SCRIPT_DIR, name)
    return os.path.join(SCRIPT_DIR, f"{BOT_SESSION_PREFIX if bot else DEFAULT_SESSION}_{name}")


def session_kind(path: str) -> str:
    """Đoán loại phiên theo tên file: 'BOT' hay 'TÀI KHOẢN NGƯỜI DÙNG'."""
    return "BOT" if os.path.basename(path).startswith(BOT_SESSION_PREFIX) else "TÀI KHOẢN NGƯỜI DÙNG"

# ---------------------------------------------------------------------------
# 1. Bộ từ điển + chấm điểm  ->  nay nằm ở analyzer.py (dùng chung 2 backend)
# ---------------------------------------------------------------------------
#
# Trước đây khối này bị COPY nguyên vào cả demo_telegram.py và
# demo_telegram_api.py, nên hai bản dễ lệch nhau. Nay chỉ còn một nguồn duy
# nhất là analyzer.py (import ở đầu file):
#   * SCAM_GROUPS / ACTION_SIGNALS / THRESHOLD_* / RE_*  — từ điển + ngưỡng
#   * normalize() / no_accent() / extract_entities()     — chuẩn hoá + thực thể
#   * analyze(post, ocr_text="")                         — điểm 0-100 cho 1 bài
#   * group_summary(records, noun=...)                   — điểm rủi ro theo group
#   * print_group_section(groups, noun=...)              — in mục [5] của báo cáo
# Giải thích chi tiết: xem GIAI_THICH_CODE.md.


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
    if (not api_id or not api_hash) and os.path.exists(API_CONFIG_PATH):
        data = _read_api_config()
        api_id = api_id or data.get("api_id")
        api_hash = api_hash or data.get("api_hash")
    return int(api_id or 0), (api_hash or "")


def load_bot_token(args) -> str:
    """Token bot: tham số dòng lệnh → biến môi trường TG_BOT_TOKEN → tg_api.json."""
    token = getattr(args, "bot_token", None) or os.environ.get("TG_BOT_TOKEN")
    if not token and os.path.exists(API_CONFIG_PATH):
        token = _read_api_config().get("bot_token")
    return (token or "").strip()


def _read_api_config() -> dict:
    try:
        with open(API_CONFIG_PATH, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_credentials(api_id: int, api_hash: str, bot_token: str = "") -> None:
    """Ghi api_id/api_hash (+ token bot nếu có) vào tg_api.json với quyền 600.

    Nhờ vậy các lần chạy sau không phải nhập lại; file chứa api_hash nên phải
    **không cho người khác đọc** (chmod 600) và không commit lên git.
    """
    data = _read_api_config()
    data.update({"api_id": api_id, "api_hash": api_hash})
    if bot_token:
        data["bot_token"] = bot_token
    try:
        with open(API_CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.chmod(API_CONFIG_PATH, 0o600)
        print(f"[i] Đã lưu credential vào {API_CONFIG_PATH} (quyền 600)")
    except OSError as exc:
        print(f"    ! Không ghi được {API_CONFIG_PATH}: {type(exc).__name__}: {exc}")


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
        "  Không có số điện thoại? Dùng bot: --setup-bot <token> (token từ @BotFather).\n"
        "  Không muốn dùng API? Chạy bản web: python3 demo_telegram.py --channel <kênh>"
    )


async def open_client(api_id: int, api_hash: str, interactive: bool = True,
                      session: str = "", bot: bool = False):
    """Mở client. Trả (client, None) nếu OK, (None, thông báo lỗi) nếu không."""
    path = session_path(session, bot=bot)
    if os.path.exists(path + ".session"):
        # Tái sử dụng phiên cũ: nói rõ loại phiên để tránh nhầm bot/người dùng.
        print(f"[i] Dùng lại phiên {os.path.basename(path)}.session "
              f"(loại: {session_kind(path)})")
    client = TelegramClient(path, api_id, api_hash)
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


async def open_bot_client(api_id: int, api_hash: str, token: str, session: str = ""):
    """Mở client bằng **token bot** (không cần số điện thoại, không cần OTP)."""
    path = session_path(session, bot=True)
    if os.path.exists(path + ".session"):
        print(f"[i] Dùng lại phiên bot {os.path.basename(path)}.session")
    client = TelegramClient(path, api_id, api_hash)
    try:
        await client.start(bot_token=token)
    except Exception as exc:
        try:
            await client.disconnect()
        except Exception:
            pass
        return None, (f"Đăng nhập bot thất bại: {type(exc).__name__}: {exc}\n"
                      "      Kiểm tra token (dạng 123456789:AAH...) và api_id/api_hash.")
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
        chat = getattr(fwd, "chat", None)
        # Bản cũ viết `A or B and C or "nguồn ẩn"`: do thứ tự ưu tiên của `and/or`
        # nên MỌI forward không có from_name đều thành "nguồn ẩn". Viết tường minh:
        fwd_name = (getattr(fwd, "from_name", None)
                    or getattr(chat, "title", None)
                    or getattr(chat, "username", None)
                    or "nguồn ẩn")
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


# ---------------------------------------------------------------------------
# 5. Các chế độ thu thập
# ---------------------------------------------------------------------------

async def fetch_history(client, target: str, limit: int, progress=print,
                        prefer_username: str = "") -> list[dict]:
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
    # Đối tượng "min" từ get_entity() có thể thiếu username -> dùng username đã
    # tra được ở --user để nhãn kênh/link trong báo cáo vẫn đẹp.
    if prefer_username:
        for rec in records:
            rec["channel"] = prefer_username
            rec["url"] = f"https://t.me/{prefer_username}/{rec['message_id']}"
    print(f"    lấy được {len(records)} tin nhắn")
    return records


def _usernames_of(entity) -> list[str]:
    """Mọi username của một thực thể (Telegram cho phép nhiều username phụ)."""
    out: list[str] = []
    main = getattr(entity, "username", None)
    if main:
        out.append(main)
    for item in getattr(entity, "usernames", None) or []:
        name = getattr(item, "username", None) or str(item)
        if name and name.lower() not in {u.lower() for u in out}:
            out.append(name)
    return out


def _chat_row(chat) -> dict:
    """Một dòng mô tả nhóm/kênh (dùng cho danh sách nhóm chung)."""
    row = {
        "title": getattr(chat, "title", "") or "",
        "username": getattr(chat, "username", None) or "",
        "kind": "channel" if getattr(chat, "broadcast", False) else "group",
        "id": getattr(chat, "id", None),
    }
    count = getattr(chat, "participants_count", None)
    if count:
        row["members"] = count
    return row


def _status_line(entity) -> str:
    """Trạng thái hoạt động của người dùng, diễn giải sang tiếng Việt."""
    status = getattr(entity, "status", None)
    if status is None:
        return ""
    name = type(status).__name__
    if name == "UserStatusOnline":
        return "đang online"
    if name == "UserStatusOffline":
        was = getattr(status, "was_online", None)
        return f"truy cập lần cuối {was:%d/%m/%Y %H:%M} UTC" if was else "offline"
    return {
        "UserStatusRecently": "truy cập gần đây",
        "UserStatusLastWeek": "truy cập trong tuần qua",
        "UserStatusLastMonth": "truy cập trong tháng qua",
        "UserStatusEmpty": "không công khai",
    }.get(name, "không rõ")


async def lookup_entity(client, target: str, limit: int = 0, photo_dir: str = "",
                        common_limit: int = 20, save_photo: bool = True
                        ) -> tuple[dict, list[dict]]:
    """Tra cứu thông tin một username/ID qua API (kể cả group kín đã tham gia).

    Trả về `(profile, records)`. `records` là các tin gần đây của kênh/nhóm
    (rỗng với người dùng/bot, hoặc khi `limit=0`).

    `photo_dir` khác rỗng thì tải ảnh đại diện về thư mục đó; `common_limit > 0`
    thì liệt kê các nhóm/kênh dùng chung (chỉ có ý nghĩa với người dùng).
    """
    try:
        entity = await client.get_entity(target)
    except Exception as exc:
        raise RuntimeError(
            f"Không tra được '{target}' ({type(exc).__name__}: {exc}).\n"
            "      Kiểm tra username/ID, hoặc tài khoản chưa từng tương tác với "
            "đối tượng này."
        )
    if isinstance(entity, User):
        kind = "bot" if getattr(entity, "bot", False) else "user"
    elif getattr(entity, "broadcast", False):
        kind = "channel"
    else:
        kind = "group"

    title = tl_utils.get_display_name(entity) or getattr(entity, "title", "") or ""
    about = None
    subscribers = members = None
    verified = bool(getattr(entity, "verified", False))
    full_username = None
    extras: dict = {}
    try:
        if isinstance(entity, User):
            full = await client(functions.users.GetFullUserRequest(id=entity))
            fu = full.full_user
            about = getattr(fu, "about", None)
            verified = verified or bool(getattr(fu, "verified", False))
            full_username = getattr(fu, "username", None)
            # Số điện thoại: Telegram chỉ trả khi là danh bạ hoặc họ để công khai.
            # (Với chính mình, `full_user.phone` rỗng nhưng `entity.phone` có.)
            extras["phone"] = getattr(entity, "phone", None) or getattr(fu, "phone", None)
            extras["common_chats_count"] = getattr(fu, "common_chats_count", None)
            extras["bot_commands"] = [
                {"command": getattr(c, "command", "") or "",
                 "description": getattr(c, "description", "") or ""}
                for c in (getattr(getattr(fu, "bot_info", None), "commands", None) or [])
                if getattr(c, "command", "")
            ]
            if common_limit:
                try:
                    common = await client(functions.messages.GetCommonChatsRequest(
                        user_id=entity.id, max_id=0, limit=common_limit))
                    extras["common_groups"] = [_chat_row(c) for c in common.chats]
                except Exception as exc:
                    print(f"    ! Không lấy được nhóm chung: {type(exc).__name__}: {exc}")
        elif isinstance(entity, Channel):
            full = await client(functions.channels.GetFullChannelRequest(channel=entity))
            chat = full.full_chat
            about = getattr(chat, "about", None)
            verified = verified or bool(getattr(chat, "verified", False))
            full_username = getattr(chat, "username", None)
            count = getattr(chat, "participants_count", None)
            if kind == "channel":
                subscribers = count
            else:
                members = count
        else:                                   # Chat: group cơ bản
            full = await client(functions.messages.GetFullChatRequest(chat_id=entity.id))
            chat = full.full_chat
            about = getattr(chat, "about", None)
            members = getattr(chat, "participants_count", None)
    except Exception as exc:
        print(f"    ! Không lấy được thông tin mở rộng: {type(exc).__name__}: {exc}")

    created = getattr(entity, "date", None)
    extras["created_at"] = created.strftime("%Y-%m-%d") if hasattr(created, "strftime") else None
    extras["last_seen"] = _status_line(entity)
    extras["usernames"] = _usernames_of(entity)
    extras["lang_code"] = getattr(entity, "lang_code", None)
    for key, attr in (("premium", "premium"), ("scam", "scam"), ("fake", "fake"),
                      ("restricted", "restricted"), ("mutual_contact", "mutual_contact"),
                      ("is_contact", "contact")):
        if getattr(entity, attr, None):
            extras[key] = True

    # `get_entity()` có thể trả về đối tượng "min" (chỉ có id), nên phải lấy
    # username từ full info hoặc từ chính tham số người dùng nhập.
    raw = str(target or "").lstrip("@").strip()
    username = (getattr(entity, "username", None) or full_username
                or (raw if USERNAME_RE.match(raw) else None)
                or str(getattr(entity, "id", target)))
    has_photo = bool(getattr(entity, "photo", None))
    extras["has_photo"] = has_photo
    if save_photo and photo_dir and has_photo:
        try:
            os.makedirs(photo_dir, exist_ok=True)
            path = await client.download_profile_photo(
                entity, file=os.path.join(photo_dir, f"{username}.jpg"))
            if path:
                extras["photo_path"] = path
        except Exception as exc:
            print(f"    ! Không tải được ảnh đại diện: {type(exc).__name__}: {exc}")

    profile = make_profile(
        username,
        kind, source="telethon", title=title, about=about, subscribers=subscribers,
        members=members, account_id=getattr(entity, "id", None), verified=verified,
        is_bot=(kind == "bot"), **extras,
    )
    print(f"  [user] @{profile['username']} — {profile['kind_vi']} (id={profile['account_id']})")

    records: list[dict] = []
    if limit and kind in ("channel", "group"):
        prefer = profile["username"] if USERNAME_RE.match(profile["username"]) else ""
        records = await fetch_history(client, target, limit, prefer_username=prefer)
    return profile, records


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
    # Lưu ý: KHÔNG sắp xếp ở đây — lúc này record chưa có khóa "analysis" nên bản
    # cũ sắp xếp theo scam_score luôn là no-op. Việc sắp xếp được làm trong
    # run_async() SAU khi đã chấm điểm.
    return records[:limit]


async def search_chats(client, keyword: str, limit: int = 20) -> list[dict]:
    """Tìm kênh/nhóm theo từ khoá, trả về username để đưa vào mục [6]."""
    try:
        found = await client(functions.contacts.SearchRequest(q=keyword, limit=limit))
    except Exception as exc:
        print(f"    ! Tìm chat theo từ khoá thất bại: {type(exc).__name__}: {exc}")
        return []
    out: list[dict] = []
    seen: set[str] = set()

    def add(username, name: str, kind: str, count=None, unit: str = "") -> None:
        """Thêm một kết quả; `kind` dùng lại mã của `analyzer.PROFILE_KIND_VI`."""
        username = str(username).strip() if username else None
        if not username or username.lower() in seen:
            return
        seen.add(username.lower())
        out.append({"username": username, "keywords": [keyword], "title": name or "",
                    "name": name or "", "kind": kind,
                    "scale": f"{count} {unit}" if count else ""})

    for chat in found.chats:                    # kênh / nhóm
        add(getattr(chat, "username", None), getattr(chat, "title", "") or "",
            "channel" if getattr(chat, "broadcast", False) else "group",
            getattr(chat, "participants_count", None),
            "người đăng ký" if getattr(chat, "broadcast", False) else "thành viên")
    for user in found.users:                    # người dùng / bot (contacts.Search trả ở đây)
        name = " ".join(x for x in (getattr(user, "first_name", None),
                                    getattr(user, "last_name", None)) if x)
        add(getattr(user, "username", None), name,
            "bot" if getattr(user, "bot", False) else "user")
    print(f"    tìm chat theo '{keyword}': {len(out)} username")
    return out


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
# Dữ liệu mẫu offline cho --demo  ->  nay nằm ở analyzer.py (dùng chung 2 bản)
# ---------------------------------------------------------------------------
# analyzer.DEMO_ITEMS: 17 bài của 8 kênh (5 kênh lừa đảo + 3 kênh sạch).
# Bản API thêm `forwards`/`replies`; bản web (t.me/s) thì không có 2 khóa này.

def demo_posts(limit: int) -> list[dict]:
    return demo_posts_shared(limit, dialect="api")


# ---------------------------------------------------------------------------
# 7. Báo cáo  (phần in dùng chung: analyzer.print_report)
# ---------------------------------------------------------------------------

def report(records: list[dict], sources: list[str] | None = None) -> None:
    """In báo cáo [1]-[5] cho bản API (backend Telethon)."""
    print_report(records, sources,
                 title="BÁO CÁO THU THẬP TELEGRAM QUA API (Telethon)", noun=NOUN_API)


# ---------------------------------------------------------------------------
# 8. Chế độ BOT (--setup-bot / --login-bot) và theo dõi tin nhắn mới (--watch)
# ---------------------------------------------------------------------------

def bot_mode(args) -> bool:
    """Có chạy bằng phiên BOT hay không.

    Dùng phiên bot khi có token bot (tham số `--bot-token`, biến môi trường
    `TG_BOT_TOKEN`, hoặc token đã lưu trong tg_api.json — nhờ vậy sau
    `--setup-bot` chỉ cần `--watch @nhom`), TRỪ KHI người dùng ép phiên người
    dùng bằng `--login` hoặc `--session <tên>` (tên không bắt đầu bằng tg_bot).
    """
    explicit = bool(getattr(args, "bot_token", None) or os.environ.get("TG_BOT_TOKEN"))
    if not (explicit or _read_api_config().get("bot_token")):
        return False
    if getattr(args, "login", False):
        return False
    name = (getattr(args, "session", "") or "").strip()
    if name and not os.path.basename(name).startswith(BOT_SESSION_PREFIX):
        return False
    return True


async def run_setup_bot(api_id: int, api_hash: str, token: str, session: str = "") -> int:
    """--setup-bot: kiểm tra token bot và liệt kê các nhóm/kênh bot đang tham gia."""
    client, err = await open_bot_client(api_id, api_hash, token, session)
    if err:
        print(f"[!] {err}", file=sys.stderr)
        return 2
    me = await client.get_me()
    print(f"[i] Token hợp lệ — bot @{getattr(me, 'username', '') or me.id} (id={me.id})")
    print("    ! Bot CHỈ nhận tin nhắn MỚI kể từ khi được thêm vào nhóm:")
    print("      không đọc được lịch sử cũ và không dùng được --search.")
    print("    ! Thêm bot vào nhóm: mở nhóm → Add members → tìm @username của bot.")
    count = 0
    async for dialog in client.iter_dialogs():
        if dialog.is_group or dialog.is_channel:
            count += 1
            kind = "nhóm" if dialog.is_group else "kênh"
            print(f"    - [{kind}] {dialog.name}  (id={dialog.id})")
    print(f"[i] Bot đang ở {count} nhóm/kênh.")
    if not count:
        print("    Chưa có nhóm nào — thêm bot vào nhóm rồi chạy lại lệnh này.")
    save_credentials(api_id, api_hash, token)      # lưu token để lần sau khỏi nhập
    await client.disconnect()
    return 0


async def run_bot_mode(args) -> int:
    """Nhánh chỉ dùng token bot: --setup-bot <token> và --login-bot --bot-token <token>."""
    missing = _missing()
    if missing:
        return missing
    api_id, api_hash = load_credentials(args)
    if not api_id or not api_hash:
        print("[!] " + credentials_help(), file=sys.stderr)
        return 2
    token = (args.setup_bot or "").strip() or load_bot_token(args)
    if not token:
        print("[!] Thiếu token bot. Dùng: --setup-bot <token> hoặc --bot-token <token>\n"
              "    (token lấy từ @BotFather, dạng 123456789:AAH...).", file=sys.stderr)
        return 2

    if args.setup_bot is not None:
        return await run_setup_bot(api_id, api_hash, token, args.session)

    client, err = await open_bot_client(api_id, api_hash, token, args.session)
    if err:
        print(f"[!] {err}", file=sys.stderr)
        return 2
    me = await client.get_me()
    path = session_path(args.session, bot=True)
    print(f"[i] Đăng nhập bot thành công: @{getattr(me, 'username', '') or me.id}")
    print(f"[i] Phiên lưu tại: {path}.session (loại: {session_kind(path)})")
    save_credentials(api_id, api_hash, token)
    await client.disconnect()
    return 0


async def run_watch(args, api_id: int, api_hash: str, bot: bool = False) -> int:
    """--watch: theo dõi tin nhắn MỚI và chấm điểm ngay khi nhận được."""
    if bot:
        token = load_bot_token(args)
        if not token:
            print("[!] Chế độ bot cần token: --bot-token <token> (hoặc TG_BOT_TOKEN).",
                  file=sys.stderr)
            return 2
        client, err = await open_bot_client(api_id, api_hash, token, args.session)
    else:
        client, err = await open_client(api_id, api_hash, interactive=True,
                                        session=args.session, bot=False)
    if err:
        print(f"[!] {err}", file=sys.stderr)
        return 2

    if args.media_dir:
        os.makedirs(args.media_dir, exist_ok=True)
    collected: list[dict] = []
    targets = list(args.watch or [])

    @client.on(events.NewMessage(chats=targets or None))
    async def _on_new(event):                                   # pragma: no cover
        msg = event.message
        try:
            rec = to_record(msg, event.chat)
        except Exception as exc:
            print(f"    ! bỏ qua tin {getattr(msg, 'id', '?')}: {type(exc).__name__}")
            return
        if args.media_dir and rec["has_media"]:
            path = None
            try:
                path = await client.download_media(
                    msg, file=os.path.join(args.media_dir, rec["message_id"]))
            except Exception as exc:
                print(f"    ! tải media lỗi: {type(exc).__name__}")
            if path:
                rec["media_path"] = path
                rec["pipeline_status"]["downloaded"] = True
                if args.ocr and path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    txt = ocr_image(path)
                    if txt:
                        rec["ocr_text"] = txt
                        rec["pipeline_status"]["ocr_done"] = True
        rec["analysis"] = analyze(rec, ocr_text=rec.get("ocr_text", ""))
        collected.append(rec)
        a = rec["analysis"]
        print(f"  [{a['scam_score']:>3}/100 | {a['level']:<10}] "
              f"@{rec['channel']}/{rec['message_id']}  {a['scam_category_vi'] or '—'}")
        snippet = " ".join((rec.get("text") or "").split())[:120]
        if snippet:
            print(f"        {snippet}")
        if args.cycles and len(collected) >= args.cycles:
            print(f"[i] Đã nhận đủ {args.cycles} tin — dừng theo --cycles.")
            asyncio.ensure_future(client.disconnect())

    print(f"[i] Chế độ: {'BOT' if bot else 'TÀI KHOẢN NGƯỜI DÙNG'}"
          f"{' | phiên ' + os.path.basename(session_path(args.session, bot=bot)) if args.session else ''}")
    if targets:
        print(f"[i] Đang theo dõi: {', '.join(targets)}")
    else:
        print("[i] Không truyền --watch → theo dõi MỌI hội thoại mà tài khoản/bot thấy.")
    if bot:
        print("[!] Bot chỉ nhận tin MỚI trong nhóm bot đã được thêm; không đọc được lịch sử cũ.")
    try:
        if args.watch_once:
            print(f"[i] Chờ tối đa {args.interval}s rồi thoát (--watch-once).")
            try:
                await asyncio.wait_for(client.run_until_disconnected(), timeout=args.interval)
            except asyncio.TimeoutError:
                print("[i] Hết thời gian chờ.")
        else:
            print("[i] Theo dõi liên tục — nhấn Ctrl+C để dừng.")
            await client.run_until_disconnected()
    except KeyboardInterrupt:
        print("\n[i] Đã dừng theo dõi (Ctrl+C).")
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

    if not collected:
        print("[i] Không nhận được tin nhắn mới nào.")
        return 0
    out = write_json(args.out, collected, "telethon-bot" if bot else "telethon-watch",
                     noun=NOUN_API)
    if args.no_report:
        print_history(out)
    else:
        report(out, [f"theo dõi {', '.join(targets) or 'mọi hội thoại'}"])
    return 0


# ---------------------------------------------------------------------------
# 9. main
# ---------------------------------------------------------------------------

async def run_async(args) -> int:
    missing = _missing()
    if missing:
        return missing

    api_id, api_hash = load_credentials(args)
    if not api_id or not api_hash:
        print("[!] " + credentials_help(), file=sys.stderr)
        return 2

    is_bot = bot_mode(args)

    if args.watch:
        return await run_watch(args, api_id, api_hash, bot=is_bot)

    if is_bot:
        token = load_bot_token(args)
        if not token:
            print("[!] Chế độ bot cần --bot-token <token>.", file=sys.stderr)
            return 2
        client, err = await open_bot_client(api_id, api_hash, token, args.session)
    else:
        client, err = await open_client(api_id, api_hash, interactive=True,
                                        session=args.session, bot=False)
    if err:
        print(f"[!] {err}", file=sys.stderr)
        print("     (Thiếu api_id/api_hash? Chạy --login trước.)", file=sys.stderr)
        return 2

    me = await client.get_me()
    kind = "BOT" if is_bot else "TÀI KHOẢN NGƯỜI DÙNG"
    print(f"[i] Đăng nhập: {getattr(me, 'first_name', '') or 'bot'} "
          f"(@{getattr(me, 'username', '') or getattr(me, 'id', '')})  — loại phiên: {kind}")

    if args.login or args.login_bot:
        path = session_path(args.session, bot=is_bot)
        print(f"[i] Phiên đã sẵn sàng: {path}.session (loại: {session_kind(path)})")
        if is_bot:
            save_credentials(api_id, api_hash, load_bot_token(args))
        else:
            save_credentials(api_id, api_hash)
        await client.disconnect()
        return 0

    records: list[dict] = []
    profiles: list[dict] = []
    sources: list[str] = []
    if args.media_dir:
        os.makedirs(args.media_dir, exist_ok=True)

    for target in args.user or []:
        try:
            profile, found = await lookup_entity(
                client, target, args.limit,
                photo_dir="" if getattr(args, "no_photo", False) else getattr(args, "photo_dir", "profile_photos"),
                common_limit=getattr(args, "common_limit", 20))
        except RuntimeError as exc:
            print(f"    ! {exc}")
            continue
        profiles.append(profile)
        sources.append(f"tra cứu @{profile['username']}")
        records += found

    for target in args.channel or []:
        sources.append(f"kênh/nhóm {target}")
        try:
            records += await fetch_history(client, target, args.limit)
        except RuntimeError as exc:
            print(f"    ! {exc}")

    if is_bot and args.search:
        print("    ! Bot không dùng được tìm kiếm toàn cục (--search) — bỏ qua.")
    else:
        for kw in args.search or []:
            sources.append(f"tìm kiếm '{kw}'")
            try:
                records += await search_global(client, kw, args.limit)
            except RuntimeError as exc:
                print(f"    ! {exc}")

    extra_usernames: list[dict] = []
    for kw in args.keyword or []:
        sources.append(f"từ khoá '{kw}'")
        if is_bot:
            print("    ! Bot không dùng được tìm chat theo từ khoá — chỉ xét các bài đã lấy.")
        else:
            extra_usernames += await search_chats(client, kw, args.limit)

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

    for profile in profiles:                  # in lại kèm thống kê điểm
        print_profile(profile, records, noun=NOUN_API)

    kw_payload = (username_payload(records, args.keyword, extra_usernames=extra_usernames)
                  if args.keyword else None)
    extra = {**({"profiles": profiles} if profiles else {}), **(kw_payload or {})}

    if not records:
        if profiles or extra_usernames:
            if kw_payload:
                print_username_payload(kw_payload, noun=NOUN_API)
            write_json(args.out, [], "telethon-bot" if is_bot else "telethon",
                       noun=NOUN_API, extra=extra)
            return 0
        print("[!] Không thu thập được bài đăng nào.")
        return 1

    # Sắp xếp SAU khi đã chấm điểm: bản cũ sắp xếp trong search_global() lúc
    # record còn chưa có khóa "analysis" nên thực chất không sắp xếp gì cả.
    records.sort(key=lambda r: -((r.get("analysis") or {}).get("scam_score", 0)))
    out = write_json(args.out, records, "telethon-bot" if is_bot else "telethon",
                     noun=NOUN_API, extra=extra or None)
    if args.no_report:
        print_history(out, sources)
    else:
        report(out, sources)
    if kw_payload:
        print_username_payload(kw_payload, noun=NOUN_API)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Demo thu thập bài đăng Telegram qua API (Telethon) + chấm điểm lừa đảo.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--demo", action="store_true",
                   help="chạy dữ liệu mẫu offline (không cần api_id/api_hash)")
    p.add_argument("--login", action="store_true",
                   help="đăng nhập một lần bằng số điện thoại rồi lưu phiên")
    p.add_argument("--login-bot", action="store_true",
                   help="đăng nhập bằng token bot rồi lưu phiên (không cần số điện thoại)")
    p.add_argument("--setup-bot", nargs="?", const="", metavar="TOKEN",
                   help="kiểm tra token bot + liệt kê nhóm/kênh bot đang ở (bỏ trống TOKEN "
                        "thì lấy token đã lưu)")
    p.add_argument("--bot-token", metavar="TOKEN",
                   help="token bot từ @BotFather (hoặc đặt biến môi trường TG_BOT_TOKEN)")
    p.add_argument("--session", metavar="TÊN",
                   help="chọn phiên: bỏ trống → tg_session.session; 'tk-phu' → "
                        "tg_session_tk-phu.session; với bot → tg_bot_<tên>.session")
    p.add_argument("--api-id", type=int, help="api_id lấy từ my.telegram.org")
    p.add_argument("--api-hash", help="api_hash lấy từ my.telegram.org")
    p.add_argument("--channel", action="append", metavar="@KENH",
                   help="kênh công khai / group kín / ID (lặp lại được)")
    p.add_argument("--user", action="append", metavar="USERNAME",
                   help="tra cứu thông tin một tài khoản/kênh/nhóm theo username hoặc ID "
                        "(lặp lại được); kênh/nhóm còn được lấy luôn tin gần đây")
    p.add_argument("--search", action="append", metavar="TUKHOA",
                   help="tìm kiếm toàn cục theo từ khóa (trong các chat đã tham gia)")
    p.add_argument("--keyword", action="append", metavar="TUKHOA",
                   help="nhập từ khoá rồi liệt kê username có thể liên quan (mục [6]); "
                        "với tài khoản người dùng còn tìm chat theo từ khoá đó (lặp lại được)")
    p.add_argument("--media-dir", metavar="DIR",
                   help="tải ảnh/video về thư mục này")
    p.add_argument("--ocr", action="store_true",
                   help="đọc chữ trong ảnh (cần pytesseract + Pillow), chấm lại điểm")
    p.add_argument("--watch", action="append", metavar="@KENH/NHOM",
                   help="theo dõi tin nhắn MỚI (lặp lại được; bỏ trống = mọi hội thoại)")
    p.add_argument("--watch-once", action="store_true",
                   help="chờ tối đa --interval giây rồi thoát, không theo dõi mãi")
    p.add_argument("--interval", type=int, default=60,
                   help="số giây chờ ở chế độ --watch-once (mặc định 60)")
    p.add_argument("--cycles", type=int, default=0,
                   help="dừng sau khi nhận đủ N tin nhắn mới (0 = chạy mãi)")
    p.add_argument("--limit", type=int, default=30, help="số bài tối đa mỗi nguồn (mặc định 30)")
    p.add_argument("--out", default="posts_api.json", help="file JSON kết quả")
    p.add_argument("--photo-dir", default="profile_photos", metavar="DIR",
                   help="thư mục lưu ảnh đại diện tải về khi tra --user (mặc định "
                        "profile_photos; dùng --no-photo để không tải)")
    p.add_argument("--no-photo", action="store_true", help="không tải ảnh đại diện")
    p.add_argument("--common-limit", type=int, default=20, metavar="N",
                   help="số nhóm/kênh dùng chung tối đa liệt kê khi tra --user (0 = bỏ qua)")
    p.add_argument("--no-report", action="store_true",
                   help="chỉ IN DANH SÁCH tin nhắn đã crawl, không in báo cáo thống kê")
    p.add_argument("--report", metavar="JSON", help="chỉ in báo cáo từ file JSON đã lưu")
    args = p.parse_args()

    if args.report:
        with open(args.report, encoding="utf-8") as fh:
            payload = json.load(fh)
        records = payload.get("posts", []) if isinstance(payload, dict) else payload
        profiles = (payload.get("profiles") or []) if isinstance(payload, dict) else []
        for profile in profiles:
            print_profile(profile, records, noun=NOUN_API)
        stored_usernames = payload.get("usernames") if isinstance(payload, dict) else None
        if args.keyword:
            print_username_payload(username_payload(records, args.keyword), noun=NOUN_API)
        elif stored_usernames:
            print_username_section(stored_usernames, payload.get("keywords"),
                                   payload.get("invite_links"), noun=NOUN_API)
        if not records:
            if profiles or stored_usernames or args.keyword:
                return 0
            print(f"[!] Không có bài đăng nào trong {args.report}", file=sys.stderr)
            return 1
        report(records, [args.report])
        return 0

    if args.setup_bot is not None or args.login_bot:
        return asyncio.run(run_bot_mode(args))

    if not any([args.demo, args.channel, args.search, args.login, args.watch, args.user,
                args.keyword]):
        args.demo = True      # không chọn nguồn nào -> chạy dữ liệu mẫu offline

    if args.demo:
        records = demo_posts(args.limit)
        for rec in records:
            rec["analysis"] = analyze(rec)
        kw_payload = username_payload(records, args.keyword) if args.keyword else None
        out = write_json(args.out, records, "demo (offline)", noun=NOUN_WEB, extra=kw_payload)
        if args.no_report:
            print_history(out, ["dữ liệu mẫu offline (không cần API)"])
        else:
            report(out, ["dữ liệu mẫu offline (không cần API)"])
        if kw_payload:
            print_username_payload(kw_payload, noun=NOUN_WEB)
        return 0

    return asyncio.run(run_async(args))


if __name__ == "__main__":
    sys.exit(main())
