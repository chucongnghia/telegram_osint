"""Offline launcher and static UI contracts; never launch Chrome, Telegram, or a model."""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
from telegram_demo.cli import dashboard


class DashboardLauncherTests(unittest.TestCase):
    def test_help_is_safe_from_foreign_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "not-created.sqlite3"
            result = subprocess.run(
                [sys.executable, "-B", str(ROOT / "main.py"), "dashboard", "--help"],
                cwd=tmp, env={**os.environ, "TELEGRAM_DB_PATH": str(database)},
                text=True, capture_output=True, timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--port", result.stdout)
            self.assertIn("--db", result.stdout)
            self.assertFalse(database.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_port_invalid_is_rejected_before_server(self):
        for port in ("0", "65536", "-1", "abc"):
            with self.subTest(port=port), redirect_stderr(io.StringIO()):
                with patch("telegram_demo.dashboard_service.serve_dashboard") as serve:
                    with self.assertRaises(SystemExit) as exc:
                        dashboard.main(["--port", port])
                    self.assertEqual(exc.exception.code, 2)
                    serve.assert_not_called()

    def test_launch_forwards_only_supported_options(self):
        with patch("telegram_demo.dashboard_service.serve_dashboard") as serve:
            self.assertEqual(dashboard.main(["--db", "data/custom.sqlite3", "--port", "8766", "--open"]), 0)
            serve.assert_called_once_with(db_path=Path("data/custom.sqlite3"), port=8766, open_browser=True)

    def test_server_failure_is_nonzero(self):
        with patch("telegram_demo.dashboard_service.serve_dashboard", side_effect=OSError("Address already in use")):
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(dashboard.main([]), 1)
            self.assertIn("Không mở được dashboard", output.getvalue())
        with patch("telegram_demo.dashboard_service.serve_dashboard", side_effect=KeyboardInterrupt):
            self.assertEqual(dashboard.main([]), 130)

    @unittest.skipUnless(shutil.which("node"), "Node is optional and only needed for isolated client contract tests")
    def test_isolated_javascript_client_contract(self):
        result = subprocess.run(
            [shutil.which("node"), str(ROOT / "tests" / "test_dashboard_client.mjs")],
            cwd=ROOT, text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("All dashboard client contract tests passed.", result.stdout)

    def test_root_shell_has_single_named_controls_and_local_assets(self):
        static = ROOT / "src" / "telegram_demo" / "dashboard"
        html = (static / "index.html").read_text(encoding="utf-8")
        ids = re.findall(r'\bid="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)))
        for name in ("global-query", "database-select", "refresh-button", "view-root", "page-action", "drawer", "drawer-content"):
            self.assertIn(name, ids)
        self.assertIn('lang="vi"', html)
        self.assertIn('data-enter', html)
        self.assertIn('id="page-action" type="button"', html)
        self.assertNotIn("https://fonts.googleapis.com", html)
        for path in re.findall(r'(?:src|href)="(/[^"?#]*)"', html):
            self.assertTrue((static / path.lstrip("/")).is_file(), path)


if __name__ == "__main__":
    unittest.main()
