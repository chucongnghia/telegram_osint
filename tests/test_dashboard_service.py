"""Offline dashboard tests. Every database, media file, and backup is temporary."""
from contextlib import closing
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from telegram_demo import dashboard_service as ds
from telegram_demo.storage import APPLICATION_ID, Database
from telegram_demo.text_classifier import ClassifierStore


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for directory in ("data", "media", "sessions", "json_folder", "static"):
            (self.root / directory).mkdir()
        self.patcher = patch.multiple(ds, PROJECT_ROOT=self.root, DATA_DIR=self.root / "data",
                                     MEDIA_DIR=self.root / "media", SESSIONS_DIR=self.root / "sessions",
                                     JSON_DIR=self.root / "json_folder", STATIC_DIR=self.root / "static")
        self.patcher.start()
        self.dbpath = self.root / "data" / "test.sqlite3"
        with Database(self.dbpath) as db:
            db.save_payload({"backend": "web", "posts": [
                {"channel": "publicone", "message_id": 1, "text": "Cảnh báo chuyển tiền", "posted_at": "2026-01-14T12:00:00Z", "url": "https://t.me/publicone/1", "analysis": {"scam_score": 95, "level": "cao", "scam_category_vi": "Từ khoá"}},
                {"channel": "publicone", "message_id": 2, "text": "Tin thường", "posted_at": "2026-01-12T12:00:00Z", "analysis": {"scam_score": 5, "level": "thấp"}},
                {"channel": "sample", "message_id": 3, "text": "Demo only", "source": "demo", "posted_at": "2026-01-14T13:00:00Z"},
            ]}, tool="test", source="test")
            store = ClassifierStore(db)
            self.classification(store, 1, "Cảnh báo chuyển tiền", "scam_indicators")
            self.classification(store, 1, "Cảnh báo chuyển tiền", "reporting_warning")
            self.classification(store, 2, "Tin thường", "other")
            db.save_payload({"backend": "web", "profiles": [{"username": "publicone", "title": "Public One", "kind": "user", "phone": "+12345", "bio": "Saved public bio", "api_hash": "DO_NOT_EXPOSE", "common_groups": [{"id": 3, "title": "Visible", "auth_key": "DO_NOT_EXPOSE"}]}]})
        self.app = ds.DashboardApp(self.dbpath)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.app.close)

    @staticmethod
    def classification(store, mid, text, label=None, status="success"):
        store.save({"message_row_id": mid, "input_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "state": {"text": text}, "source": {"api_hash": "DO_NOT_EXPOSE"}},
                   batch_id="test", provider="test", model="test-model", backend_key="test",
                   status=status, label=label, error_code="http_error" if status == "error" else None)

    def start_server(self):
        server = self.app.create_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.shutdown)
        return server

    def request(self, server, method, path, payload=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        h = {"Host": f"127.0.0.1:{server.server_port}"}
        h.update(headers or {})
        body = None if payload is None else json.dumps(payload)
        if body is not None:
            h.setdefault("Content-Type", "application/json")
        conn.request(method, path, body, h)
        response = conn.getresponse()
        raw = response.read()
        result = (response.status, dict(response.getheaders()), raw)
        conn.close()
        return result

    def test_latest_classifications_separate_from_heuristics_and_daily_gaps(self):
        with self.app.read_db() as conn:
            overview = self.app.overview(conn)
            self.assertEqual(overview["counts"]["messages"], 3)
            self.assertEqual(overview["counts"]["classified"], 2)
            self.assertEqual(overview["counts"]["scam_indicators"], 0)
            self.assertEqual(overview["counts"]["flagged"], 1)
            self.assertEqual(sum(x["count"] for x in overview["labels"]), 2)
            self.assertEqual(len(overview["daily"]), 14)
            self.assertEqual(next(x["count"] for x in overview["daily"] if x["day"] == "2026-01-13"), 0)
            self.assertEqual(overview["daily"][-1], {"day": "2026-01-14", "count": 2})
            self.assertEqual(self.app.messages(conn, label="reporting_warning")["total"], 1)
            self.assertEqual(self.app.messages(conn, label="unclassified")["total"], 1)
            self.assertEqual(self.app.messages(conn, query="' OR 1=1 --")["total"], 0)
            self.assertEqual(self.app.messages(conn, query="canh bao")["total"], 1)
            self.assertIn("demo", {r["namespace"] for r in self.app.messages(conn)["items"]})

    def test_latest_error_does_not_count_old_label(self):
        with Database(self.dbpath) as db:
            self.classification(ClassifierStore(db), 1, "old", status="error")
        with self.app.read_db() as conn:
            self.assertEqual(self.app.overview(conn)["counts"]["classified"], 1)
            self.assertEqual(self.app.messages(conn, label="unclassified")["total"], 2)
            detail = self.app.message_detail(conn, 1)
            self.assertTrue(detail["classifications"][0]["stale"])
            self.assertFalse(detail["classifications"][1]["stale"])
            self.assertNotIn("DO_NOT_EXPOSE", json.dumps(detail))

    def test_stale_labels_error_filters_sources_and_text_bounds(self):
        with Database(self.dbpath) as db:
            db.conn.execute("UPDATE messages SET text=? WHERE id=1", ("edited text" * 2000,))
            db.conn.execute("UPDATE analyses SET score=25 WHERE message_id=2")
            db.conn.commit()
        with self.app.read_db() as conn:
            data = self.app.messages(conn, username="PUBLICONE")
            self.assertEqual(data["total"], 2)
            edited = next(m for m in data["items"] if m["id"] == 1)
            self.assertTrue(edited["classification_stale"])
            self.assertEqual(len(edited["text"]), 16000)
            self.assertEqual(self.app.messages(conn, label="reporting_warning")["total"], 0)
            self.assertEqual(self.app.messages(conn, label="unclassified")["total"], 2)
            self.assertEqual(self.app.overview(conn)["counts"]["classified"], 1)
            self.assertEqual(self.app.overview(conn)["counts"]["flagged"], 2)
            self.assertEqual(self.app.messages(conn, username="' OR 1=1 --")["total"], 0)
        with Database(self.dbpath) as db:
            self.classification(ClassifierStore(db), 2, "Tin thường", status="error")
        with self.app.read_db() as conn:
            self.assertEqual(self.app.messages(conn, label="error")["total"], 1)

    def test_read_only_old_schema_and_database_allowlist(self):
        before = self.dbpath.read_bytes()
        with self.app.read_db() as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM messages")
        self.assertEqual(before, self.dbpath.read_bytes())
        foreign = self.root / "data" / "foreign.sqlite3"
        with closing(sqlite3.connect(foreign)) as conn:
            conn.execute("CREATE TABLE private (secret)")
        backup = self.root / "data" / "test-backup.sqlite3"
        backup.write_bytes(before)
        link = self.root / "data" / "link.sqlite3"
        link.symlink_to(self.dbpath)
        self.assertEqual(set(self.app.databases()), {"test.sqlite3"})
        for name in ("../test.sqlite3", "foreign.sqlite3", "test-backup.sqlite3", "link.sqlite3"):
            with self.assertRaises(ds.DashboardError):
                self.app.database_path(name)
        with Database(self.dbpath) as db:
            db.conn.executescript("DROP TABLE message_classifications; DROP TABLE video_classifications; DROP TABLE video_analyses; DROP TABLE image_classifications; DROP TABLE image_analyses; PRAGMA user_version=1;")
        with self.app.read_db() as conn:
            self.assertEqual(self.app.overview(conn)["counts"]["classified"], 0)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertNotIn("video_analyses", self.app._tables(conn))

    def test_missing_db_explicit_without_creation_and_custom_db(self):
        missing = self.root / "data" / "missing.sqlite3"
        app = ds.DashboardApp(missing)
        self.addCleanup(app.close)
        self.assertEqual(app.bootstrap("token")["active_database"], "missing.sqlite3")
        with self.assertRaises(ds.DashboardError) as raised:
            with app.read_db():
                pass
        self.assertEqual(raised.exception.code, "database_missing")
        self.assertFalse(missing.exists())
        custom = self.root / "custom.sqlite3"
        with Database(custom):
            pass
        customapp = ds.DashboardApp(custom)
        self.addCleanup(customapp.close)
        with customapp.read_db() as conn:
            self.assertEqual(conn.execute("PRAGMA application_id").fetchone()[0], APPLICATION_ID)

    def test_profiles_redact_raw_and_safe_media(self):
        image = self.root / "media" / "portrait.jpg"
        image.write_bytes(b"jpeg")
        with Database(self.dbpath) as db:
            db.save_payload({"profiles": [{"username": "publicone", "kind": "user", "title": "Latest", "phone": "+999", "photo_path": str(image), "api_hash": "DO_NOT_EXPOSE"}]})
        with self.app.read_db() as conn:
            result = self.app.profiles(conn)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["title"], "Latest")
        self.assertEqual(result["items"][0]["phone"], "+999")
        self.assertIn("/api/media?path=portrait.jpg", result["items"][0]["photo_url"])
        self.assertNotIn("DO_NOT_EXPOSE", json.dumps(result))

    def test_http_bootstrap_host_origin_csrf_and_security_headers(self):
        server = self.start_server()
        status, headers, raw = self.request(server, "GET", "/api/bootstrap")
        self.assertEqual(status, 200)
        data = json.loads(raw)
        self.assertEqual(data["version"], "1.0")
        self.assertTrue(data["token"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertIn("script-src 'self';", headers["Content-Security-Policy"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        for host in ("evil.test", "127.0.0.1:1", "localhost", f"127.0.0.1:{server.server_port}.evil"):
            self.assertEqual(self.request(server, "GET", "/api/bootstrap", headers={"Host": host})[0], 403)
        self.assertEqual(self.request(server, "GET", "/api/bootstrap", headers={"Host": f"localhost:{server.server_port}"})[0], 200)
        for origin in ("null", "https://evil.test", "http://localhost:1"):
            for endpoint in ("/api/bootstrap", "/api/jobs", "/api/media?path=a.jpg"):
                self.assertEqual(self.request(server, "GET", endpoint, headers={"Origin": origin})[0], 403)
        self.assertEqual(self.request(server, "POST", "/api/settings", {"jev_key": "temporary-key"})[0], 403)
        auth = {"X-Dashboard-Token": data["token"]}
        status, _, raw = self.request(server, "POST", "/api/settings", {"jev_key": "temporary-key"}, auth)
        self.assertEqual(status, 200)
        self.assertNotIn(b"temporary-key", raw)
        self.assertTrue(json.loads(raw)["config"]["jev_configured"])
        status, _, raw = self.request(server, "POST", "/api/settings", {"clear_jev": True}, auth)
        self.assertEqual(status, 200)
        self.assertIsNone(self.app._jev_key)
        for invalid in ({"jev_key": ""}, {"jev_key": "x" * 513}, {"jev_key": "a\nb"}, {"clear_jev": False}):
            self.assertEqual(self.request(server, "POST", "/api/settings", invalid, auth)[0], 400)

    def test_http_ui_post_shapes_database_body_and_query_rejection(self):
        alternate = self.root / "data" / "selected.sqlite3"
        with Database(alternate):
            pass
        server = self.start_server()
        bootstrap = json.loads(self.request(server, "GET", "/api/bootstrap")[2])
        auth = {"X-Dashboard-Token": bootstrap["token"], "Origin": f"http://127.0.0.1:{server.server_port}"}
        status, _, raw = self.request(server, "POST", "/api/settings", {"jev_key": "ui-memory-key"}, auth)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(raw)["config"]["jev_configured"])
        self.assertNotIn(b"ui-memory-key", raw)
        class FakeProcess:
            pid = 999999997
            stdout = io.BytesIO(b"fixture only\n")
            def poll(self):
                return 0
            def wait(self, timeout=None):
                return 0
        body = {"kind": "collect", "db": "selected.sqlite3", "params": {"demo": True, "backend": "web", "limit": 2}}
        with patch.object(ds.subprocess, "Popen", return_value=FakeProcess()) as popen:
            status, _, raw = self.request(server, "POST", "/api/jobs", body, auth)
            self.assertEqual(status, 200, raw)
            job = json.loads(raw)["job"]
            self.assertEqual(job["params"]["db"], "selected.sqlite3")
            self.app._queue.join()
            self.assertIn(str(alternate), popen.call_args.args[0])
            self.assertEqual(popen.call_args.kwargs["env"]["TYPESAFE_API_KEY"], "ui-memory-key")
            self.assertNotIn("ui-memory-key", str(popen.call_args.args[0]))
            status, _, raw = self.request(server, "POST", f"/api/jobs/{job['id']}/cancel", {}, auth)
            self.assertEqual(status, 200, raw)
            self.assertEqual(json.loads(raw)["id"], job["id"])
            status, _, raw = self.request(server, "GET", f"/api/jobs/{job['id']}")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw)["logs"], ["fixture only"])
            for route, payload in (("/api/jobs", body), ("/api/settings", {"clear_jev": True}), (f"/api/jobs/{job['id']}/cancel", {})):
                status, headers, raw = self.request(server, "POST", route + "?db=selected.sqlite3", payload, auth)
                self.assertEqual(status, 400)
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertIn("code", json.loads(raw))
            self.assertEqual(popen.call_count, 1)
        self.assertEqual(self.request(server, "POST", "/api/settings", {"clear_jev": True}, auth)[0], 200)
        for route, key in (("overview", "counts"), ("messages", "items"), ("messages/1", "message"), ("sources", "items"), ("runs", "items"), ("profiles", "items"), ("videos", "analyses"), ("images", "analyses")):
            status, _, raw = self.request(server, "GET", "/api/" + route + "?db=test.sqlite3")
            self.assertEqual(status, 200, route)
            self.assertIn(key, json.loads(raw))

    def test_http_media_ranges_and_traversal(self):
        (self.root / "media" / "clip.mp4").write_bytes(b"0123456789")
        (self.root / "media" / "photo.jpg").write_bytes(b"photo")
        secret = self.root / "secret.jpg"
        secret.write_bytes(b"SECRET")
        (self.root / "media" / "escape.jpg").symlink_to(secret)
        (self.root / "media" / "config.json").write_text('{"secret":true}')
        server = self.start_server()
        status, headers, raw = self.request(server, "GET", "/api/media?path=clip.mp4", headers={"Range": "bytes=2-5"})
        self.assertEqual((status, raw), (206, b"2345"))
        self.assertEqual(headers["Content-Range"], "bytes 2-5/10")
        self.assertEqual(self.request(server, "GET", "/api/media?path=clip.mp4", headers={"Range": "bytes=-2"})[2], b"89")
        self.assertEqual(self.request(server, "GET", "/api/media?path=clip.mp4", headers={"Range": "bytes=99-"})[0], 416)
        self.assertEqual(self.request(server, "GET", "/api/media?path=photo.jpg")[2], b"photo")
        for path in ("../secret.jpg", "%2fetc%2fpasswd", "escape.jpg", "config.json", "..%5csecret.jpg"):
            self.assertIn(self.request(server, "GET", "/api/media?path=" + path)[0], (400, 403, 404))
        for path in ("/../main.py", "/%2e%2e/main.py", "/config/tg_api.json", "/.env"):
            self.assertEqual(self.request(server, "GET", path)[0], 404)

    def test_job_validation_fixed_argv_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ds.DashboardError):
                self.app.build_job("classify", None, {}, "id")
            self.app.settings({"jev_key": "memory-key"})
            argv, params, _ = self.app.build_job("classify", None, {"query": "literal query", "limit": 4}, "id")
            self.assertIn("--keyword", argv)
            self.assertIn("literal query", argv)
            self.assertNotIn("memory-key", str(argv) + str(params))
            argv, _, _ = self.app.build_job("collect", None, {"channels": "one,@two", "limit": 3}, "id")
            self.assertIn("--no-photo", argv)
            self.assertNotIn("--jev", argv)
            self.assertIn(str(self.root / "json_folder" / "dashboard_id.json"), argv)
            for kind, params in (("collect", {"channels": "--login"}), ("collect", {"channels": "one", "limit": True}),
                                 ("collect", {"channels": "one", "extra": "--login"}), ("search", {"backend": "web", "keyword": "x"}),
                                 ("download", {"url": "https://evil.test/x", "session": "../private"}),
                                 ("group_discover", {}), ("video_analyze", {"path": "../anything.mp4"})):
                with self.assertRaises(ds.DashboardError):
                    self.app.build_job(kind, None, params, "id")
        (self.root / "sessions" / "tg_session_test.session").write_bytes(b"not read")
        (self.root / ".venv-api" / "bin").mkdir(parents=True)
        (self.root / ".venv-api" / "bin" / "python").write_text("fake")
        argv, params, _ = self.app.build_job("watch", None, {"channels": "one", "session": "tg_session_test", "cycles": 8}, "id")
        self.assertIn("--watch-once", argv)
        self.assertIn("--non-interactive", argv)
        self.assertNotIn("-c", argv)
        self.assertEqual(params["cycles"], 8)
        self.assertEqual(argv[argv.index("--session") + 1], "tg_session_test")
        with self.assertRaises(ds.DashboardError) as raised:
            self.app.build_job("watch", None, {"channels": "one", "session": "tg_session_test", "once": False}, "id")
        self.assertEqual(raised.exception.code, "unsupported")

    def test_cancel_only_owned_group_and_drain_queued_cancel(self):
        reading, release = threading.Event(), threading.Event()
        class BlockingOutput:
            def read1(self, size):
                reading.set()
                if not release.wait(5):
                    raise RuntimeError("cancel did not release fake child")
                return b""
            def close(self):
                pass
        class FakeProcess:
            pid = 999999998
            stdout = BlockingOutput()
            returncode = None
            def poll(self):
                return self.returncode
            def wait(self, timeout=None):
                self.returncode = -2
                release.set()
                return self.returncode
        with patch.object(ds.subprocess, "Popen", return_value=FakeProcess()) as popen, patch.object(ds.os, "killpg") as killpg:
            active = self.app.submit({"kind": "collect", "params": {"demo": True}})
            self.assertTrue(reading.wait(3))
            queued = self.app.submit({"kind": "collect", "params": {"demo": True}})
            self.assertEqual(self.app.cancel(queued["id"])["status"], "cancelled")
            self.assertEqual(self.app.cancel(active["id"])["status"], "cancelling")
            self.app._queue.join()
            self.assertEqual(self.app.job(active["id"])["status"], "cancelled")
            self.assertEqual(self.app.job(queued["id"])["status"], "cancelled")
            self.assertEqual(popen.call_count, 1)
            killpg.assert_called_once_with(999999998, ds.signal.SIGINT)
            self.assertFalse(self.app._processes)

    def test_video_export_paths_evidence_and_bounded_scan(self):
        clip = self.root / "media" / "clip.mp4"
        clip.write_bytes(b"video")
        frame = self.root / "media" / "frame.jpg"
        frame.write_bytes(b"photo")
        with Database(self.dbpath) as db:
            db.conn.execute("""INSERT INTO video_analyses(created_at,pipeline_version,source_path,video_sha256,status,
                artifact_dir,options_json,media_json,transcript_json,vision_json,evidence_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""", ("2026-01-14", "test", str(clip), "hash", "partial", str(frame.parent),
                '{"api_key":"DO_NOT_EXPOSE"}', json.dumps({"frames": [{"path": str(frame), "timestamp": 1.5}, {"path": "/etc/passwd", "timestamp": 2}], "local_ocr": {"status": "success", "provider": "tesseract", "text": "Video chữ", "frames": [{"timestamp": 1.5, "words": [{"text": "chữ", "confidence": 90}]}], "source_path": "/secret", "api_key": "DO_NOT_EXPOSE"}}),
                '{"text":"Public words"}', '{"status":"skipped"}', '{"sampling":{"selected":1},"secret":"DO_NOT_EXPOSE","source_json":{"x":1},"nan":NaN}'))
            db.conn.commit()
        with self.app.read_db() as conn:
            result = self.app.videos(conn)
        self.assertEqual(result["files"][0]["path"], "clip.mp4")
        analysis = result["analyses"][0]
        self.assertEqual(analysis["source_path"], "clip.mp4")
        self.assertEqual(analysis["frames"], [{"url": "/api/media?path=frame.jpg", "timestamp": 1.5}])
        self.assertEqual(analysis["local_ocr"]["text"], "Video chữ")
        self.assertEqual(analysis["local_ocr"]["frames"][0]["words"][0]["confidence"], 90)
        self.assertNotIn("/secret", json.dumps(result))
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertNotIn("DO_NOT_EXPOSE", json.dumps(result))
        self.assertIsNone(analysis["evidence"]["nan"])
        for i in range(210):
            (self.root / "media" / f"clip{i}.mp4").write_bytes(b"v")
        with self.app.read_db() as conn:
            self.assertLessEqual(len(self.app.videos(conn)["files"]), 200)

    def image_fixture(self):
        image = self.root / "media" / "poster.png"
        image.write_bytes(b"fixture image, never decoded")
        ocr = {"status": "success", "provider": "tesseract", "version": "5.fixture", "languages": ["vie", "eng"],
               "text": "Cảnh báo <script>not HTML</script>", "words": [{"text": "Cảnh", "confidence": 94.5,
               "bbox": {"left": 1, "top": 2, "width": 30, "height": 10}}],
               "image": {"width": 300, "height": 200, "source_path": "/secret"}, "api_key": "DO_NOT_EXPOSE",
               "limitations": ["OCR does not understand scenes"]}
        with Database(self.dbpath) as db:
            cursor = db.conn.execute("""INSERT INTO image_analyses(created_at,pipeline_version,source_path,image_sha256,status,
                artifact_dir,options_json,media_json,ocr_json,vision_json,evidence_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""", ("2026-01-14", "test", str(image), "imagehash", "partial", str(image.parent),
                '{"api_key":"DO_NOT_EXPOSE"}', json.dumps({"width": 300, "path": str(image)}), json.dumps(ocr),
                '{"status":"skipped"}', '{"gaps":["vision_skipped"],"secret":"DO_NOT_EXPOSE","nan":NaN}'))
            image_id = cursor.lastrowid
            db.conn.execute("""INSERT INTO image_classifications(image_analysis_id,created_at,batch_id,provider,model,
                backend_key,rubric_version,rubric_json,evidence_sha256,evidence_json,source_json,status,label)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (image_id, "2026-01-14", "fixture", "mock", "fixture-model", "mock",
                "1", "{}", "hash", "{}", '{"secret":"DO_NOT_EXPOSE"}', "success", "insufficient_information"))
            db.conn.commit()
        return image, image_id

    def test_http_images_schema4_safe_evidence_and_media_refs(self):
        image, image_id = self.image_fixture()
        (self.root / "media" / "sub").mkdir()
        (self.root / "media" / "sub" / "nested.webp").write_bytes(b"fixture")
        (self.root / "media" / "linked.png").symlink_to(image)
        (self.root / "media" / "linked-dir").symlink_to(self.root / "media" / "sub", target_is_directory=True)
        (self.root / "media" / "not-ocr.gif").write_bytes(b"fixture")
        before = self.dbpath.read_bytes()
        server = self.start_server()
        status, headers, raw = self.request(server, "GET", "/api/images")
        self.assertEqual(status, 200, raw)
        self.assertEqual(headers["Cache-Control"], "no-store")
        result = json.loads(raw)
        self.assertEqual({f["path"] for f in result["files"]}, {"poster.png", "sub/nested.webp"})
        analysis = result["analyses"][0]
        self.assertEqual(analysis["id"], image_id)
        self.assertEqual(analysis["source_path"], "poster.png")
        self.assertEqual(analysis["source_url"], "/api/media?path=poster.png")
        self.assertEqual(analysis["source_sha256"], "imagehash")
        self.assertEqual(analysis["ocr"]["words"][0]["confidence"], 94.5)
        self.assertEqual(analysis["ocr"]["text"], "Cảnh báo <script>not HTML</script>")
        self.assertEqual(analysis["evidence"]["gaps"], ["vision_skipped"])
        self.assertIsNone(analysis["evidence"]["nan"])
        self.assertNotIn("DO_NOT_EXPOSE", raw.decode())
        self.assertNotIn(str(self.root), raw.decode())
        self.assertNotIn("/secret", raw.decode())
        self.assertEqual(result["classifications"][0]["image_analysis_id"], image_id)
        self.assertEqual(self.request(server, "GET", analysis["source_url"])[2], image.read_bytes())
        self.assertEqual(before, self.dbpath.read_bytes(), "image dashboard must never mutate/migrate storage")
        self.assertEqual(self.request(server, "GET", "/api/images?db=../test.sqlite3")[0], 400)
        self.assertEqual(self.request(server, "GET", "/api/images?limit=1000000")[0], 400)
        for i in range(210):
            (self.root / "media" / f"image{i}.png").write_bytes(b"fixture")
        with self.app.read_db() as conn:
            self.assertLessEqual(len(self.app.images(conn)["files"]), 200)

    def test_http_images_absent_on_read_only_schema1_through3(self):
        with closing(sqlite3.connect(self.dbpath)) as conn:
            conn.executescript("DROP TABLE image_classifications; DROP TABLE image_analyses;")
        server = self.start_server()
        for version in (1, 2, 3):
            with closing(sqlite3.connect(self.dbpath)) as conn:
                conn.execute(f"PRAGMA user_version={version}")
            before = self.dbpath.read_bytes()
            status, _, raw = self.request(server, "GET", "/api/images")
            self.assertEqual(status, 200, raw)
            self.assertEqual(json.loads(raw), {"files": [], "analyses": [], "classifications": []})
            self.assertEqual(before, self.dbpath.read_bytes())
            with self.app.read_db() as conn:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], version)
                self.assertNotIn("image_analyses", self.app._tables(conn))

    def test_image_job_argv_is_accepted_by_actual_cli_parser(self):
        from telegram_demo.cli import image as image_cli

        image = self.root / "media" / "parser-contract.png"
        image.write_bytes(b"fixture image, analyze is mocked")
        with patch.dict(os.environ, {}, clear=True):
            argv, _, _ = self.app.build_job("image_analyze", None,
                                          {"path": image.name, "ocr_language": "eng"}, "parser-contract")

        def inspect_dispatch(store, args):
            # Keep the actual parser, Database and ImageStore boundary. Only OCR /
            # vision execution is mocked, so flag drift must fail this contract.
            self.assertEqual(store.db.conn.execute("PRAGMA database_list").fetchone()[2], str(self.dbpath))
            self.assertEqual(args.command, "analyze")
            self.assertEqual(args.file, str(image))
            self.assertEqual(args.db, str(self.dbpath))
            self.assertEqual(args.timeout, 30)
            self.assertEqual(args.ocr_language, "eng")
            self.assertFalse(args.vision)
            self.assertFalse(args.allow_remote)
            return 0

        with patch.object(image_cli, "analyze", side_effect=inspect_dispatch) as analyze:
            # Strip the launcher, main.py and its image dispatcher only; do not
            # reconstruct parser arguments independently of DashboardApp.
            self.assertEqual(image_cli.main(argv[3:]), 0)
            analyze.assert_called_once()

    def test_image_and_video_ocr_fixed_jobs_and_validation(self):
        image, image_id = self.image_fixture()
        clip = self.root / "media" / "clip.mp4"
        clip.write_bytes(b"fixture")
        (self.root / ".venv-video" / "bin").mkdir(parents=True)
        (self.root / ".venv-video" / "bin" / "python").write_text("fixture, never executed")
        with patch.dict(os.environ, {}, clear=True):
            argv, params, _ = self.app.build_job("image_analyze", None, {"path": "poster.png"}, "fixture")
            self.assertEqual(argv[:3], [ds.sys.executable, str(self.root / "main.py"), "image"])
            self.assertIn(str(image), argv)
            self.assertEqual(argv[argv.index("--ocr-language") + 1], "vie+eng")
            self.assertEqual(argv[argv.index("--timeout") + 1], "30")
            self.assertNotIn("--vision", argv)
            self.assertFalse(params["vision"])
            self.assertNotIn("--allow-remote", argv)
            self.assertNotIn("--allow-model-download", argv)
            for language in ("vie+eng", "eng", "vie"):
                args, _, _ = self.app.build_job("image_analyze", None, {"path": "poster.png", "ocr_language": language}, "fixture")
                self.assertEqual(args[args.index("--ocr-language") + 1], language)
            invalid = [
                {"path": "../poster.png"}, {"path": str(image)}, {"path": "clip.mp4"},
                {"path": "poster.png", "ocr_language": "--help"}, {"path": "poster.png", "ocr_language": ["eng"]},
                {"path": "poster.png", "vision": "true"}, {"path": "poster.png", "vision": True},
                {"path": "poster.png", "tesseract": "/bin/sh"}, {"path": "poster.png", "allow_remote": "yes"},
                {"path": "poster.png", "ocr_timeout": 10000}, {"path": "poster.png", "language": "eng"},
            ]
            for suffix in ("gif", "bmp", "tiff"):
                (self.root / "media" / f"unsupported.{suffix}").write_bytes(b"fixture")
                invalid.append({"path": f"unsupported.{suffix}"})
            (self.root / "media" / "link.png").symlink_to(image)
            invalid.append({"path": "link.png"})
            for params in invalid:
                with self.subTest(params=params), self.assertRaises(ds.DashboardError):
                    self.app.build_job("image_analyze", None, params, "fixture")
            with self.assertRaises(ds.DashboardError):
                self.app.build_job("image_classify", None, {"image_id": image_id}, "fixture")
            self.app.settings({"jev_key": "mock-key-never-called"})
            argv, _, _ = self.app.build_job("image_classify", None, {"image_id": image_id, "allow_partial": True, "recheck": True}, "fixture")
            self.assertEqual(argv[2:7], ["image", "--db", str(self.dbpath), "classify", str(image_id)])
            self.assertIn("--allow-partial", argv)
            self.assertIn("--recheck", argv)
            for params in ({"image_id": 0}, {"image_id": 999}, {"image_id": True}, {"image_id": image_id, "allow_partial": "true"}):
                with self.assertRaises(ds.DashboardError):
                    self.app.build_job("image_classify", None, params, "fixture")
            for language in ("auto", "vi", "en"):
                argv, params, _ = self.app.build_job("video_analyze", None, {"path": "clip.mp4", "language": language}, "fixture")
                self.assertIn("--ocr", argv)
                self.assertEqual(argv[argv.index("--ocr-language") + 1], "vie+eng")
                self.assertIn("--skip-vision", argv)
                self.assertTrue(params["ocr"])
                self.assertNotIn("--allow-model-download", argv)
                self.assertNotIn("--allow-remote", argv)
            argv, _, _ = self.app.build_job("video_analyze", None, {"path": "clip.mp4", "ocr": False}, "fixture")
            self.assertNotIn("--ocr", argv)
            self.assertNotIn("--ocr-language", argv)
            for params in ({"path": "clip.mp4", "ocr": 1}, {"path": "clip.mp4", "ocr_language": "auto"}, {"path": "clip.mp4", "language": "eng"}):
                with self.assertRaises(ds.DashboardError):
                    self.app.build_job("video_analyze", None, params, "fixture")

    def test_vision_settings_validation_and_job_consent(self):
        self.image_fixture()
        clip = self.root / "media" / "clip.mp4"
        clip.write_bytes(b"fixture")
        (self.root / ".venv-video" / "bin").mkdir(parents=True)
        (self.root / ".venv-video" / "bin" / "python").write_text("fixture, never executed")
        with patch.dict(os.environ, {}, clear=True):
            # Nothing configured: vision jobs are refused, OCR-only jobs still build.
            with self.assertRaises(ds.DashboardError) as missing:
                self.app.build_job("image_analyze", None, {"path": "poster.png", "vision": True}, "fixture")
            self.assertEqual(missing.exception.code, "configuration")
            argv, _, _ = self.app.build_job("image_analyze", None, {"path": "poster.png"}, "fixture")
            self.assertNotIn("--vision", argv)
            self.assertFalse(self.app.config()["vision_configured"])

            # Incomplete or contradictory configurations are rejected before any state changes.
            for payload in (
                {"vision_provider": "gemini", "vision_model": "gemini-2.5-flash"},
                {"vision_provider": "openai", "vision_model": "qwen2.5vl:3b"},
                {"vision_provider": "openai", "vision_model": "qwen2.5vl:3b", "vision_url": "https://api.example/v1", "vision_key": ""},
                {"vision_provider": "other", "vision_model": "m", "vision_key": "k"},
                {"vision_provider": "openai", "vision_model": "m", "vision_url": "http://8.8.8.8/v1"},
                {"vision_provider": "openai", "vision_model": "m", "vision_url": "http://127.0.0.1:1/v1?key=secret"},
                {"vision_provider": "openai", "vision_model": "m", "vision_url": "http://user:pw@127.0.0.1:1/v1"},
                {"vision_model": "m", "jev_key": "k"},
                {"clear_vision": "yes"},
            ):
                with self.subTest(payload=payload), self.assertRaises(ds.DashboardError):
                    self.app.settings(payload)
            self.assertFalse(self.app.config()["vision_configured"])

            # Gemini: model + key; remote by definition and the key never leaves memory.
            self.app.settings({"vision_provider": "gemini", "vision_model": "gemini-2.5-flash", "vision_key": "gemini-memory-key"})
            config = self.app.config()
            self.assertTrue(config["vision_configured"])
            self.assertEqual(config["vision_provider"], "gemini")
            self.assertEqual(config["vision_model"], "gemini-2.5-flash")
            self.assertTrue(config["vision_remote"])
            self.assertTrue(config["vision_key_set"])
            self.assertNotIn("gemini-memory-key", json.dumps(self.app.config()))
            self.assertNotIn("gemini-memory-key", json.dumps(self.app.bootstrap("token")))
            with self.assertRaises(ds.DashboardError) as consent:
                self.app.build_job("image_analyze", None, {"path": "poster.png", "vision": True}, "fixture")
            self.assertEqual(consent.exception.code, "consent_required")
            argv, params, _ = self.app.build_job("image_analyze", None,
                                                 {"path": "poster.png", "vision": True, "allow_remote": True}, "fixture")
            for flag, value in (("--vision-provider", "gemini"), ("--vision-model", "gemini-2.5-flash")):
                self.assertEqual(argv[argv.index(flag) + 1], value)
            self.assertIn("--vision", argv)
            self.assertIn("--allow-remote", argv)
            self.assertEqual(argv.count("--allow-remote"), 1)
            self.assertNotIn("gemini-memory-key", " ".join(argv))
            self.assertTrue(params["allow_remote"])
            with self.assertRaises(ds.DashboardError):  # consent is a real boolean, not truthy text
                self.app.build_job("image_analyze", None, {"path": "poster.png", "vision": True, "allow_remote": "yes"}, "fixture")
            with self.assertRaises(ds.DashboardError) as video_consent:
                self.app.build_job("video_analyze", None, {"path": "clip.mp4", "skip_vision": False}, "fixture")
            self.assertEqual(video_consent.exception.code, "consent_required")
            argv, _, _ = self.app.build_job("video_analyze", None,
                                            {"path": "clip.mp4", "skip_vision": False, "allow_remote": True, "ocr": False}, "fixture")
            self.assertNotIn("--skip-vision", argv)
            self.assertIn("--allow-remote", argv)
            argv, _, _ = self.app.build_job("video_analyze", None, {"path": "clip.mp4", "skip_vision": True}, "fixture")
            self.assertIn("--skip-vision", argv)
            self.assertNotIn("--allow-remote", argv)

            # Local OpenAI-compatible endpoint: loopback HTTP is allowed without a key or consent.
            self.app.settings({"clear_vision": True})
            self.assertFalse(self.app.config()["vision_configured"])
            self.app.settings({"vision_provider": "openai", "vision_model": "qwen2.5vl:3b",
                               "vision_url": "http://127.0.0.1:11434/v1/chat/completions"})
            config = self.app.config()
            self.assertTrue(config["vision_configured"])
            self.assertFalse(config["vision_remote"])
            self.assertFalse(config["vision_key_set"])
            argv, _, _ = self.app.build_job("image_analyze", None, {"path": "poster.png", "vision": True}, "fixture")
            self.assertEqual(argv[argv.index("--vision-url") + 1], "http://127.0.0.1:11434/v1/chat/completions")
            self.assertEqual(argv[argv.index("--vision-provider") + 1], "openai")
            self.assertNotIn("--allow-remote", argv)

            # Switching provider drops a stale OpenAI-compatible URL instead of failing on it.
            self.app.settings({"vision_provider": "gemini", "vision_model": "gemini-2.5-flash", "vision_key": "gemini-second-key"})
            config = self.app.config()
            self.assertTrue(config["vision_configured"])
            self.assertTrue(config["vision_remote"])
            self.assertFalse(config["vision_endpoint_set"])

            # Remote OpenAI-compatible provider: key required, consent still per job, key never in argv.
            self.app.settings({"vision_provider": "openai", "vision_model": "gpt-4o-mini",
                               "vision_url": "https://api.example.com/v1/chat/completions", "vision_key": "openai-remote-key"})
            self.assertTrue(self.app.config()["vision_remote"])
            with self.assertRaises(ds.DashboardError) as remote_consent:
                self.app.build_job("image_analyze", None, {"path": "poster.png", "vision": True}, "fixture")
            self.assertEqual(remote_consent.exception.code, "consent_required")
            argv, _, _ = self.app.build_job("image_analyze", None,
                                            {"path": "poster.png", "vision": True, "allow_remote": True}, "fixture")
            self.assertIn("--allow-remote", argv)
            self.assertEqual(argv[argv.index("--vision-url") + 1], "https://api.example.com/v1/chat/completions")
            self.assertNotIn("openai-remote-key", " ".join(argv))

    def test_worker_injects_vision_key_only_into_child_environment(self):
        class FakeProcess:
            pid = 999999999
            def __init__(self, *args, **kwargs):
                self.stdout = io.BufferedReader(io.BytesIO(b"ok\n"))
                self.returncode = None
            def wait(self, timeout=None):
                self.returncode = 0
                return 0
            def poll(self):
                return self.returncode
        self.image_fixture()
        with patch.dict(os.environ, {}, clear=True):
            self.app.settings({"vision_provider": "gemini", "vision_model": "gemini-2.5-flash", "vision_key": "gemini-child-key"})
            with patch.object(ds.subprocess, "Popen", side_effect=[FakeProcess()]) as popen:
                job = self.app.submit({"kind": "image_analyze",
                                       "params": {"path": "poster.png", "vision": True, "allow_remote": True}})
                self.app._queue.join()
            self.assertEqual(self.app.job(job["id"])["status"], "succeeded")
            kwargs = popen.call_args.kwargs
            self.assertEqual(kwargs["env"]["GEMINI_API_KEY"], "gemini-child-key")
            self.assertNotIn("VISION_API_KEY", kwargs["env"])
            self.assertNotIn("gemini-child-key", " ".join(kwargs.get("args") or popen.call_args.args[0]))
            self.assertNotIn("gemini-child-key", json.dumps(self.app.jobs()))
            self.assertNotIn("gemini-child-key", json.dumps(self.app.job(job["id"])))
            self.assertIn("gemini-child-key", self.app._redactions)
            self.assertEqual(self.app._sanitize("key=gemini-child-key"), "key=[đã ẩn]")

    def test_entered_keys_persist_privately_across_restart(self):
        secrets_file = self.root / "json_folder" / "dashboard_secrets.json"
        self.app.settings({"jev_key": "jev-persist-key"})
        self.app.settings({"vision_provider": "gemini", "vision_model": "gemini-2.5-flash", "vision_key": "gemini-persist-key"})
        self.assertTrue(secrets_file.is_file())
        self.assertEqual(stat.S_IMODE(secrets_file.stat().st_mode), 0o600)
        self.assertEqual(secrets_file.stat().st_size, len(secrets_file.read_bytes()))
        stored = secrets_file.read_text(encoding="utf-8")
        self.assertIn("gemini-persist-key", stored)  # plaintext by design, mode 0600
        self.assertEqual([p.name for p in secrets_file.parent.iterdir()], ["dashboard_secrets.json"])
        for payload in (self.app.config(), self.app.bootstrap("token")):
            self.assertNotIn("gemini-persist-key", json.dumps(payload))
            self.assertNotIn("jev-persist-key", json.dumps(payload))

        self.image_fixture()
        restarted = ds.DashboardApp(self.dbpath)
        try:
            config = restarted.config()
            self.assertEqual(config["vision_provider"], "gemini")
            self.assertEqual(config["vision_model"], "gemini-2.5-flash")
            self.assertTrue(config["vision_configured"])
            self.assertTrue(config["vision_key_set"])
            self.assertTrue(config["jev_configured"])
            argv, _, _ = restarted.build_job("image_analyze", None,
                                             {"path": "poster.png", "vision": True, "allow_remote": True}, "fixture")
            self.assertNotIn("gemini-persist-key", " ".join(argv))
        finally:
            restarted.close()

        # Clearing vision keeps the Jev key; clearing both removes the file entirely.
        restarted = ds.DashboardApp(self.dbpath)
        try:
            restarted.settings({"clear_vision": True})
            self.assertFalse(restarted.config()["vision_configured"])
            self.assertTrue(restarted.config()["jev_configured"])
            restarted.settings({"clear_jev": True})
        finally:
            restarted.close()
        self.assertFalse(secrets_file.exists())

        # A corrupt file is ignored, not deleted, and the dashboard still starts.
        secrets_file.write_text("{not json", encoding="utf-8")
        broken = ds.DashboardApp(self.dbpath)
        try:
            self.assertFalse(broken.config()["vision_configured"])
        finally:
            broken.close()
        self.assertTrue(secrets_file.exists())

    def test_worker_mocked_child_redaction_bounded_logs_failure_drain_and_backup(self):
        class FakeProcess:
            pid = 999999999
            def __init__(self, *args, **kwargs):
                self.stdout = io.BufferedReader(io.BytesIO(("memory-key\n" + "row\n" * 120).encode()))
                self.returncode = None
            def wait(self, timeout=None):
                self.returncode = 0
                return 0
            def poll(self):
                return self.returncode
        self.app.settings({"jev_key": "memory-key"})
        with patch.object(ds.subprocess, "Popen", side_effect=[OSError("memory-key"), FakeProcess()]) as popen:
            first = self.app.submit({"kind": "collect", "params": {"demo": True}})
            second = self.app.submit({"kind": "collect", "params": {"demo": True}})
            self.app._queue.join()
            self.assertEqual(self.app.job(first["id"])["status"], "failed")
            result = self.app.job(second["id"])
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(len(result["logs"]), 100)
            self.assertNotIn("memory-key", json.dumps(self.app.jobs()))
            args, kwargs = popen.call_args
            self.assertEqual(kwargs["env"]["TYPESAFE_API_KEY"], "memory-key")
            self.assertEqual(kwargs["stdin"], ds.subprocess.DEVNULL)
            self.assertTrue(kwargs["start_new_session"])
            self.assertNotIn("shell", kwargs)
            self.assertEqual(kwargs["cwd"], str(self.root))
        self.assertEqual(self.app._sanitize("key=memory-key API_HASH=abcdef"), "key=[đã ẩn] API_HASH=[đã ẩn]")
        result = self.app.submit({"kind": "db_backup", "params": {}})
        self.app._queue.join()
        self.assertEqual(self.app.job(result["id"])["status"], "succeeded")
        backups = list((self.root / "data").glob("dashboard-backup_*.sqlite3"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 3)
        self.assertNotIn(backups[0].name, self.app.databases())


if __name__ == "__main__":
    unittest.main()
