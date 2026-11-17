"""Serve the project's local dashboard without installing web dependencies."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from ..storage import DEFAULT_DB


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="TínTrạm — dashboard Telegram cục bộ tại http://127.0.0.1:8765.",
        epilog="Chỉ lắng nghe loopback; đọc dữ liệu không tự chạy Telegram hay model.",
    )
    parser.add_argument("--db", default=os.environ.get("TELEGRAM_DB_PATH") or str(DEFAULT_DB),
                        help="DB ứng dụng mặc định (không tự tạo hay migrate khi xem)")
    parser.add_argument("--port", type=int, default=8765, help="cổng loopback, mặc định 8765")
    parser.add_argument("--open", action="store_true", help="mở trình duyệt sau khi server khởi động")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port phải nằm trong 1..65535")
    from ..dashboard_service import serve_dashboard
    try:
        serve_dashboard(db_path=Path(args.db).expanduser(), port=args.port, open_browser=args.open)
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[!] Không mở được dashboard: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
