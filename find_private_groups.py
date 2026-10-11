#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
find_private_groups.py — Tìm và đọc group KÍN (private) trên Telegram.

Vì sao cần script riêng?
------------------------
Group kín KHÔNG có username thì Telegram không đưa vào chỉ mục tìm kiếm — đã kiểm
chứng: `contacts.Search` chỉ trả về Channel có username, `messages.SearchGlobal`
chỉ tìm trong các chat tài khoản đã tham gia. Muốn tới group kín, cách thực tế là:

    Kênh công khai (quảng cáo) ──► bóc link mời t.me/+abc ──► join ──► đọc lịch sử

Script làm đúng chuỗi đó, chia 3 bước tách rời để bạn kiểm soát:

    B1  --discover   quét kênh công khai (và/hoặc tìm kiếm toàn cục) → tìm link mời
    B2  --join       join các link mời đã tìm được (CÓ RỦI RO cho tài khoản)
    B3  --crawl      đọc lịch sử các group đã join

Cách dùng
---------
    # B1: tìm link mời từ một kênh công khai (không join gì cả, an toàn)
    python3 find_private_groups.py --discover --channel @kenh_cong_khai --limit 500 \
        --out found.json

    # B1 (thêm): tìm link mời từ kết quả tìm kiếm toàn cục
    python3 find_private_groups.py --discover --search "đầu tư lợi nhuận" --out found.json

    # B1b: thử cả trong các nhóm bạn đã tham gia (tìm link mời được share nội bộ)
    python3 find_private_groups.py --discover --in-my-chats --out found.json

    # B2: join (mặc định CHỈ thử 5 link, nghỉ 20s giữa các lần)
    python3 find_private_groups.py --join --in found.json --max-join 5 --delay 20

    # B3: đọc lịch sử các group đã join
    python3 find_private_groups.py --crawl --in found.json --limit 1000 --out groups.json

    # Hoặc làm cả 3 bước một lượt (vẫn cần bạn xác nhận trước khi join)
    python3 find_private_groups.py --discover --channel @kenh --join --crawl --limit 300

CẢNH BÁO
--------
    - Join nhiều group lạ trong thời gian ngắn có thể khiến Telegram HẠN CHẾ hoặc
      KHOÁ tài khoản (FloodWait / PeerFlood). Script mặc định rất dè dặt: tối đa 5
      link, nghỉ 20 giây mỗi lần, và bỏ qua link đã join.
    - Nên dùng tài khoản phụ (SIM phụ) cho việc này, không dùng tài khoản chính.
    - Chỉ thu thập dữ liệu công khai trong phạm vi phục vụ nghiên cứu/đồ án.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone

try:
    from telethon import TelegramClient, functions
    from telethon.errors import FloodWaitError
    from telethon.tl.types import InputMessagesFilterEmpty
except ImportError:                                   # pragma: no cover
    TelegramClient = None
    functions = None

    class FloodWaitError(Exception):
        seconds = 0

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SESSION = os.path.join(SCRIPT_DIR, "tg_session")

# Link mời nhóm kín: t.me/+hash (mới) và t.me/joinchat/hash (cũ)
RE_INVITE = re.compile(r"(?:https?://)?t\.me/(?:\+[\w-]{8,}|joinchat/[\w-]{8,})", re.I)


def normalize(text: str) -> str:
    text = str(text or "").replace("\u200b", " ").replace("\ufeff", "")
    return re.sub(r"\s+", " ", re.sub(r"[\r\n\t]+", " ", text)).strip()


def extract_invites(text: str) -> list[str]:
    """Bóc mọi link mời nhóm trong một đoạn văn bản."""
    out = []
    for m in RE_INVITE.findall(text or ""):
        link = m if m.lower().startswith("http") else "https://" + m
        link = link.rstrip(".,;)")
        if link not in out:
            out.append(link)
    return out


# ---------------------------------------------------------------------------
# Credential / kết nối (giống các script khác)
# ---------------------------------------------------------------------------

def load_credentials(args) -> tuple[int, str]:
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


def session_path(name: str) -> str:
    if not name:
        return DEFAULT_SESSION
    return os.path.join(SCRIPT_DIR, f"tg_session_{re.sub(r'[^\w.-]', '_', name)}")


async def open_client(api_id: int, api_hash: str, session: str):
    client = TelegramClient(session, api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        return client, "AUTH_NEEDED"
    me = await client.get_me()
    print(f"[i] Phiên: {os.path.basename(session)}.session — "
          f"{getattr(me, 'first_name', '')} (@{getattr(me, 'username', None) or getattr(me, 'id', '')})")
    return client, None


# ---------------------------------------------------------------------------
# B1. Khám phá link mời nhóm kín
# ---------------------------------------------------------------------------

class Found:
    """Gom link mời + nguồn tìm thấy, khử trùng lặp."""

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}

    def add(self, link: str, source: str, context: str, when: str | None) -> None:
        link = link.rstrip("/")
        rec = self.items.setdefault(link, {
            "invite": link,
            "sources": [],
            "contexts": [],
            "posted_at": when,
            "joined": False,
            "join_error": None,
            "title": None,
            "messages": 0,
        })
        if source not in rec["sources"]:
            rec["sources"].append(source)
        ctx = normalize(context)[:200]
        if ctx and ctx not in rec["contexts"]:
            rec["contexts"].append(ctx)

    def as_list(self) -> list[dict]:
        return list(self.items.values())


async def scan_messages_for_invites(client, entity, limit: int, source: str,
                                    found: Found, search: str | None = None) -> int:
    """Quét tin nhắn của một chat, bóc link mời. Trả số link mới tìm được."""
    kwargs = {"limit": limit}
    if search:
        kwargs["search"] = search
    n_before = len(found.items)
    try:
        async for msg in client.iter_messages(entity, **kwargs):
            if not msg:
                continue
            text = msg.message or ""
            # link preview / nút bấm inline cũng có thể chứa link mời
            try:
                reply = getattr(msg, "reply_markup, None", None)
                for row in (getattr(reply, "rows", []) or []):
                    for btn in row.buttons:
                        url = getattr(btn, "url", None)
                        if url:
                            text += " " + url
            except Exception:
                pass
            for link in extract_invites(text):
                when = msg.date.replace(tzinfo=timezone.utc).isoformat() if msg.date else None
                found.add(link, source, text, when)
    except FloodWaitError as exc:
        print(f"    ! Telegram yêu cầu chờ {getattr(exc, 'seconds', 60)}s")
    except Exception as exc:
        print(f"    ! lỗi khi quét {source}: {type(exc).__name__}: {str(exc)[:70]}")
    return len(found.items) - n_before


async def discover(client, args, found: Found) -> None:
    """B1: quét kênh công khai / tìm kiếm toàn cục / các chat đã tham gia."""
    # (a) kênh công khai chỉ định
    targets = list(args.channel or [])
    for seed in args.seed_file or []:
        with open(seed, encoding="utf-8") as fh:
            targets += [ln.split("#")[0].strip() for ln in fh if ln.split("#")[0].strip()]
    for target in targets:
        try:
            entity = await client.get_entity(target)
        except Exception as exc:
            print(f"  ! không truy cập được {target}: {type(exc).__name__}")
            continue
        print(f"  [scan] {getattr(entity, 'title', target)} ({target})")
        n = await scan_messages_for_invites(client, entity, args.limit,
                                            f"kênh {target}", found)
        print(f"    -> +{n} link mời (tổng {len(found.items)})")

    # (b) tìm kiếm toàn cục theo từ khóa: lấy chat rồi quét link mời trong đó
    for kw in args.search or []:
        print(f"  [search] '{kw}'")
        try:
            res = await client(functions.contacts.SearchRequest(q=kw, limit=20))
        except Exception as exc:
            print(f"    ! tìm kiếm lỗi: {type(exc).__name__}")
            continue
        for chat in res.chats:
            try:
                entity = await client.get_entity(chat)
            except Exception:
                continue
            name = getattr(entity, "title", getattr(entity, "username", "?"))
            n = await scan_messages_for_invites(client, entity, args.limit,
                                                f"search:{kw}", found)
            if n:
                print(f"    + {name}: {n} link mời")

    # (c) quét link mời được chia sẻ trong chính các chat bạn đã tham gia
    if args.in_my_chats:
        print("  [my-chats] quét link mời trong các chat bạn đã tham gia")
        try:
            dialogs = await client.get_dialogs(limit=args.my_chats_limit)
        except Exception as exc:
            print(f"    ! không lấy được danh sách chat: {type(exc).__name__}")
            dialogs = []
        for d in dialogs:
            title = getattr(d, "title", None) or getattr(d, "name", "?")
            n = await scan_messages_for_invites(client, d.entity, args.limit,
                                                "chat đã tham gia", found)
            if n:
                print(f"    + {title}: {n} link mời")


# ---------------------------------------------------------------------------
# B2. Join các link mời
# ---------------------------------------------------------------------------

async def join_invites(client, found: Found, max_join: int, delay: int) -> None:
    """B2: join từng link mời chưa tham gia. Rất dè dặt để tránh bị hạn chế."""
    todo = [r for r in found.as_list() if not r["joined"]][:max_join]
    if not todo:
        print("  [join] không có link nào cần join")
        return
    print(f"  [join] thử {len(todo)} link (nghỉ {delay}s giữa các lần)...")
    for i, rec in enumerate(todo, 1):
        link = rec["invite"]
        try:
            from telethon.tl.functions.messages import ImportChatInviteRequest
            from telethon.tl.functions.channels import JoinChannelRequest
            if "/+" in link:
                updates = await client(ImportChatInviteRequest(link.split("/+")[-1]))
                chats = getattr(updates, "chats", []) or []
                rec["joined"] = True
                rec["title"] = getattr(chats[0], "title", None) if chats else None
            else:
                await client(JoinChannelRequest(link))
                rec["joined"] = True
            print(f"    [{i}/{len(todo)}] OK: {link} → {rec['title'] or '(đã join)'}")
        except FloodWaitError as exc:
            wait = getattr(exc, "seconds", 60)
            rec["join_error"] = f"FloodWait {wait}s"
            print(f"    [{i}/{len(todo)}] ! Telegram yêu cầu chờ {wait}s — DỪNG join "
                  "để bảo vệ tài khoản")
            break
        except Exception as exc:
            name = type(exc).__name__
            rec["join_error"] = f"{name}: {str(exc)[:80]}"
            if name in ("InviteHashExpiredError", "InviteHashInvalidError"):
                print(f"    [{i}/{len(todo)}] link hết hạn/không hợp lệ: {link}")
            elif "Already" in name or "UserAlreadyParticipant" in name:
                rec["joined"] = True
                print(f"    [{i}/{len(todo)}] đã ở trong nhóm: {link}")
            else:
                print(f"    [{i}/{len(todo)}] lỗi {name}: {str(exc)[:70]}")
        if i < len(todo):
            await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# B3. Đọc lịch sử các group đã tìm được
# ---------------------------------------------------------------------------

async def crawl_found(client, found: Found, limit: int, out_path: str) -> int:
    """B3: đọc lịch sử của các group đã join, ghi vào cùng file kết quả."""
    recs = [r for r in found.as_list() if r["joined"]]
    if not recs:
        print("  [crawl] chưa có group nào được join — bỏ qua")
        return 0
    total = 0
    for rec in recs:
        try:
            entity = await client.get_entity(rec["invite"])
        except Exception as exc:
            print(f"    ! không mở được {rec['invite']}: {type(exc).__name__}")
            continue
        title = getattr(entity, "title", None) or rec["invite"]
        rec["title"] = title
        print(f"  [crawl] {title}")
        msgs = []
        try:
            async for msg in client.iter_messages(entity, limit=limit):
                if not msg or msg.action:
                    continue
                msgs.append({
                    "message_id": str(msg.id),
                    "text": normalize(msg.message or ""),
                    "posted_at": msg.date.replace(tzinfo=timezone.utc).isoformat() if msg.date else None,
                    "views": getattr(msg, "views", 0) or 0,
                    "forwards": getattr(msg, "forwards", 0) or 0,
                    "has_media": bool(msg.media),
                    "invites": extract_invites(msg.message or ""),
                })
        except FloodWaitError as exc:
            print(f"    ! chờ {getattr(exc, 'seconds', 60)}s — dừng crawl")
            break
        rec["messages"] = len(msgs)
        rec["history"] = msgs
        total += len(msgs)
        print(f"    -> {len(msgs)} tin nhắn")
    return total


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="find_private_groups.py",
        description="Tìm và đọc group kín Telegram qua link mời công khai.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--discover", action="store_true", help="B1: tìm link mời nhóm kín")
    p.add_argument("--join", action="store_true", help="B2: join các link mời đã tìm được")
    p.add_argument("--crawl", action="store_true", help="B3: đọc lịch sử group đã join")
    p.add_argument("--channel", action="append", metavar="@KENH",
                   help="kênh công khai để quét link mời (lặp lại được)")
    p.add_argument("--seed-file", metavar="FILE", help="file danh sách kênh, mỗi dòng một mục")
    p.add_argument("--search", action="append", metavar="TUKHOA",
                   help="từ khóa tìm kết quả rồi quét link mời trong đó")
    p.add_argument("--in-my-chats", action="store_true",
                   help="quét cả link mời được chia sẻ trong các chat bạn đã tham gia")
    p.add_argument("--my-chats-limit", type=int, default=50,
                   help="số chat gần nhất quét khi dùng --in-my-chats (mặc định 50)")
    p.add_argument("--max-join", type=int, default=5,
                   help="số link tối đa sẽ join (mặc định 5 — giữ nhỏ để an toàn)")
    p.add_argument("--delay", type=int, default=20,
                   help="nghỉ bao nhiêu giây giữa các lần join (mặc định 20)")
    p.add_argument("--limit", type=int, default=500,
                   help="số tin quét mỗi chat khi tìm (mặc định 500)")
    p.add_argument("--in", dest="infile", metavar="FILE",
                   help="đọc kết quả bước trước từ file này")
    p.add_argument("--out", default="found_groups.json", help="file JSON kết quả")
    p.add_argument("--session", metavar="TEN", help="tên phiên (mặc định: tg_session)")
    p.add_argument("--api-id", type=int, help="api_id (myl.telegram.org)")
    p.add_argument("--api-hash", help="api_hash (my.telegram.org)")
    return p


async def run(args) -> int:
    if TelegramClient is None:
        print("[!] Chưa cài Telethon. Cài bằng:  pip install telethon", file=sys.stderr)
        return 111

    api_id, api_hash = load_credentials(args)
    if not api_id or not api_hash:
        print("[!] Thiếu api_id/api_hash. Xem hướng dẫn trong demo_telegram_api.py "
              "hoặc tạo tg_api.json.", file=sys.stderr)
        return 2

    sess = session_path(args.session)
    client, err = await open_client(api_id, api_hash, sess)
    if err:
        await client.disconnect()
        print("[!] Phiên chưa đăng nhập. Chạy --login ở demo_telegram_api.py trước.",
              file=sys.stderr)
        return 2

    found = Found()
    # đọc kết quả bước trước nếu có
    if args.infile:
        try:
            with open(args.infile, encoding="utf-8") as fh:
                for rec in json.load(fh).get("found", []):
                    found.items[rec["invite"]] = rec
            print(f"[i] Đọc {len(found.items)} link từ {args.infile}")
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[!] Không đọc được {args.infile}: {exc}", file=sys.stderr)
            return 2

    if not any([args.discover, args.join, args.crawl]):
        print("[!] Chọn ít nhất một bước: --discover / --join / --crawl", file=sys.stderr)
        await client.disconnect()
        return 2

    if args.discover:
        print("[i] B1 — tìm link mời nhóm kín...")
        await discover(client, args, found)
        print(f"    tổng: {len(found.items)} link mời")

    if args.join:
        if not found.items:
            print("[!] Chưa có link nào để join (chạy --discover trước).", file=sys.stderr)
        else:
            print("[i] B2 — join group kín (dè dặt để bảo vệ tài khoản)...")
            await join_invites(client, found, args.max_join, args.delay)

    if args.crawl:
        print("[i] B3 — đọc lịch sử group đã join...")
        await crawl_found(client, found, args.limit, args.out)

    await client.disconnect()

    # ghi kết quả
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_invites": len(found.items),
            "joined": sum(1 for r in found.items.values() if r["joined"]),
            "found": found.as_list(),
        }, fh, ensure_ascii=False, indent=2)

    print(f"\n[i] Đã lưu {len(found.items)} link mời "
          f"({sum(1 for r in found.items.values() if r['joined'])} đã join) vào {args.out}")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n[!] Đã dừng theo yêu cầu.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
