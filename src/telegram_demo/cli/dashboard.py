"""Serve the project's local dashboard without installing web dependencies."""
from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path

from ..storage import DEFAULT_DB


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Dashboard Telegram cục bộ tại http://127.0.0.1:8765.",
        epilog="Mặc định chỉ nghe loopback. --share mở trong LAN kèm khóa truy cập; "
               "đọc dữ liệu không tự chạy Telegram hay model.",
    )
    parser.add_argument("--db", default=os.environ.get("TELEGRAM_DB_PATH") or str(DEFAULT_DB),
                        help="DB ứng dụng mặc định (không tự tạo hay migrate khi xem)")
    parser.add_argument("--port", type=int, default=8765, help="cổng loopback, mặc định 8765")
    parser.add_argument("--open", action="store_true", help="mở trình duyệt sau khi server khởi động")
    parser.add_argument("--share", action="store_true",
                        help="mở dashboard trong mạng LAN (0.0.0.0) kèm khóa truy cập tự sinh")
    parser.add_argument("--share-key", metavar="KHOA",
                        help="khóa truy cập tự đặt (≥8 ký tự); bật chế độ chia sẻ")
    parser.add_argument("--host", metavar="HOST",
                        help="địa chỉ lắng nghe, mặc định 127.0.0.1 (hoặc 0.0.0.0 khi --share)")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port phải nằm trong 1..65535")
    if args.share_key is not None and len(args.share_key) < 8:
        parser.error("--share-key phải có ít nhất 8 ký tự")
    sharing = args.share or args.share_key is not None or (args.host and args.host not in ("127.0.0.1", "localhost", "::1"))
    share_key = None
    if sharing:
        share_key = args.share_key or secrets.token_urlsafe(9)
        if args.host is None:
            args.host = "0.0.0.0"
        elif args.host in ("127.0.0.1", "localhost", "::1"):
            parser.error("--host loopback không dùng được với --share")
    host = args.host or "127.0.0.1"
    from ..dashboard_service import serve_dashboard
    try:
        serve_dashboard(db_path=Path(args.db).expanduser(), port=args.port, open_browser=args.open,
                        host=host, share_key=share_key)
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[!] Không mở được dashboard: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
