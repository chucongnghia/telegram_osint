"""Download exactly one Telegram video post, using an existing user session."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import mimetypes
import os
from pathlib import Path
import re
import stat
import sys
from urllib.parse import parse_qsl, urlsplit

from ..paths import MEDIA_DIR


class DownloadError(ValueError):
    """An actionable error safe to display without Telegram credential values."""


@dataclass(frozen=True)
class MessageLink:
    peer: str | int
    message_id: int


def _positive(value: str, maximum: int = 2147483647) -> int:
    if not re.fullmatch(r"[1-9][0-9]*", value) or len(value) > 13:
        raise DownloadError("ID trong link phải là số nguyên dương.")
    number = int(value)
    if number > maximum:
        raise DownloadError("ID trong link vượt giới hạn Telegram.")
    return number


def parse_message_link(url: str) -> MessageLink:
    """Accept post links, never profile/invite/comment links or arbitrary URLs."""
    if not isinstance(url, str) or not url or url != url.strip():
        raise DownloadError("Cần link bài đăng Telegram hợp lệ.")
    if any(c.isspace() or ord(c) < 32 for c in url) or "%" in url or "\\" in url or "#" in url:
        raise DownloadError("Link Telegram có ký tự không được hỗ trợ.")
    if "://" not in url:
        url = "https://" + url
    try:
        parts = urlsplit(url)
        if (parts.scheme not in ("http", "https")
                or parts.netloc.lower() not in ("t.me", "www.t.me", "telegram.me", "www.telegram.me")
                or parts.fragment or parts.username is not None or parts.password is not None):
            raise ValueError
        # 'comment' refers to a different discussion post: never silently fetch the parent.
        query = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=False)
        if any(key not in ("single", "t") for key, _ in query):
            raise DownloadError("Chỉ hỗ trợ link bài đăng; bỏ tham số comment/thread hoặc link thảo luận.")
        segments = parts.path.removeprefix("/").removesuffix("/").split("/")
        preview = segments[0] == "s"
        if preview:
            segments = segments[1:]
            if not segments or segments[0] == "c":
                raise ValueError
        if segments and segments[0] == "c":
            if len(segments) not in (3, 4):
                raise ValueError
            channel = _positive(segments[1], 9999999999)
            if len(segments) == 4:
                _positive(segments[2])
            # Telethon marked channel IDs use an offset, NOT string concatenation.
            return MessageLink(-(1000000000000 + channel), _positive(segments[-1]))
        if len(segments) not in (2, 3):
            raise ValueError
        username = segments[0]
        if username in ("c", "s", "joinchat") or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", username):
            raise ValueError
        if len(segments) == 3:
            _positive(segments[1])
        return MessageLink(username, _positive(segments[-1]))
    except (ValueError, IndexError) as exc:
        if isinstance(exc, DownloadError):
            raise
        raise DownloadError("Dùng link dạng https://t.me/kenh/123 hoặc https://t.me/c/123456/789.") from None


def _extension(message) -> str:
    allowed = {".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi", ".mpeg", ".mpg",
               ".ogv", ".3gp", ".3g2", ".ts", ".mts", ".m2ts", ".wmv", ".flv", ".vob", ".asf"}
    extension = getattr(getattr(message, "file", None), "ext", None)
    if isinstance(extension, str) and extension.lower() in allowed:
        return extension.lower()
    mime = getattr(message.document, "mime_type", "")
    guessed = mimetypes.guess_extension(mime) if isinstance(mime, str) else None
    return guessed if guessed in allowed else ".mp4"


def _remove_own_partial(path: Path, identity: tuple[int, int]) -> None:
    """Remove only the exact regular file this invocation exclusively created."""
    try:
        info = path.lstat()
        if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == identity:
            path.unlink()
    except OSError:
        pass


async def download_one(client, target: MessageLink, output_dir: Path) -> Path:
    # One exact message; never scan history, albums, comments or neighbouring posts.
    message = await client.get_messages(target.peer, ids=target.message_id)
    if not message or getattr(message, "id", None) != target.message_id:
        raise DownloadError("Không tìm thấy bài đăng hoặc tài khoản không có quyền xem.")
    if (getattr(message, "action", None) is not None
            or getattr(message, "web_preview", None) is not None
            or getattr(getattr(message, "media", None), "webpage", None) is not None):
        raise DownloadError("Bài đăng không có video trực tiếp; không tải service message/link preview.")
    if (getattr(message, "noforwards", False)
            or getattr(getattr(message, "chat", None), "noforwards", False)):
        raise DownloadError("Bài đăng được bảo vệ nội dung; không tải bằng lệnh này.")
    chat = await client.get_entity(target.peer)
    if getattr(chat, "noforwards", False):
        raise DownloadError("Kênh/nhóm được bảo vệ nội dung; không tải bằng lệnh này.")
    document = getattr(message, "document", None)
    mime = getattr(document, "mime_type", "")
    if not document or not (getattr(message, "video", None)
                            or isinstance(mime, str) and mime.startswith("video/")):
        raise DownloadError("Bài đăng này không có file video trực tiếp (không tải ảnh/link preview).")
    directory = Path(output_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    peer_name = str(target.peer)
    # parse_message_link restricts public usernames, but keep the helper safe independently.
    if not re.fullmatch(r"[A-Za-z0-9_-]+", peer_name):
        raise DownloadError("Peer không hợp lệ cho tên file đầu ra.")
    destination = directory / f"telegram_{peer_name}_{target.message_id}{_extension(message)}"
    assert destination.parent == directory
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise DownloadError("File đích đã tồn tại; không ghi đè. Chọn --output-dir khác.") from None
    info = os.fstat(descriptor)
    identity = (info.st_dev, info.st_ino)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            await client.download_media(message, file=stream)
            stream.flush()
            size = os.fstat(stream.fileno()).st_size
        expected = getattr(document, "size", None)
        if size <= 0 or type(expected) is int and expected > 0 and size != expected:
            raise DownloadError("Video tải không đầy đủ; đã bỏ file chưa hoàn tất, hãy thử lại.")
    except BaseException:
        _remove_own_partial(destination, identity)
        raise
    return destination


async def _run(args) -> Path:
    target = parse_message_link(args.url)
    from . import demo_telegram_api as api
    if api.TelegramClient is None:
        raise DownloadError("Cần Telethon; chạy bằng .venv-api/bin/python main.py download ...")
    api_id, api_hash = api.load_credentials(args)
    if not api_id or not api_hash:
        raise DownloadError("Thiếu Telegram api_id/api_hash; chạy main.py api --login để cấu hình.")
    session = api.session_path(args.session)
    if not Path(session + ".session").is_file():
        raise DownloadError("Chưa có phiên Telegram. Chạy main.py api --login (cùng --session nếu có).")
    client = api.TelegramClient(session, api_id, api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise DownloadError("Phiên hết hạn/chưa đăng nhập; chạy main.py api --login trước.")
        return await download_one(client, target, args.output_dir)
    finally:
        try:
            await client.disconnect()
        except Exception:
            print("[!] Không xác nhận được ngắt kết nối Telegram.", file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Tải đúng một video từ link bài đăng Telegram; không quét lịch sử.")
    parser.add_argument("url", help="https://t.me/kenh/123 hoặc https://t.me/c/123456/789")
    parser.add_argument("--output-dir", type=Path, default=MEDIA_DIR / "telegram_downloads",
                        help="thư mục lưu video (mặc định media/telegram_downloads)")
    parser.add_argument("--session", default="", help="phiên Telegram đã đăng nhập, ví dụ tk-phu")
    parser.add_argument("--api-id", type=int, help="Telegram api_id; mặc định env/config hiện có")
    parser.add_argument("--api-hash", help="Telegram api_hash; mặc định env/config hiện có")
    args = parser.parse_args(argv)
    try:
        path = asyncio.run(_run(args))
        print(f"[i] Đã tải đúng 1 video: {path}")
        return 0
    except DownloadError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("[!] Đã dừng tải; file chưa hoàn tất được dọn dẹp.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"[!] Không tải được video ({type(exc).__name__}); kiểm tra link, quyền xem, phiên và kết nối.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
