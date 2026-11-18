"""Offline legacy collector OCR tests: no Telegram, sessions, databases or paid APIs."""
from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from telegram_demo import analyzer
from telegram_demo.cli import demo_telegram_api as api, keyword_usernames
from telegram_demo.image_ocr import OCRError


def record(mid=1):
    return {"channel": "offline_fixture", "message_id": str(mid),
            "text": "Original caption — untouched", "has_media": True,
            "_msg_ref": object(), "pipeline_status": {"collected": True, "downloaded": False}}


def recognized(text="ĐẦU TƯ\nHoàn tiền 100%  @RawName"):
    return {"provider": "tesseract", "version": "offline-test", "languages": ["vie", "eng"],
            "text": text, "words": [{"text": "ĐẦU", "confidence": 91.25,
                                       "bbox": {"left": 1, "top": 2, "width": 3, "height": 4}}],
            "image": {"sha256": "a" * 64, "width": 10, "height": 20},
            "preprocessing": {"width": 10, "height": 20},
            "limitations": ["OCR confidence is not an accuracy probability."]}


class TestCollectorOCREvidence(unittest.TestCase):
    def setUp(self):
        self.engine = Mock()
        self.engine.recognize.return_value = recognized()
        self.patch = patch.object(api, "_OCR_ENGINE", self.engine)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.output = io.StringIO()
        self.stdout = patch("sys.stdout", self.output)
        self.stdout.start()
        self.addCleanup(self.stdout.stop)

    def test_lazy_engine_uses_only_shared_local_ocr(self):
        with patch.object(api, "_OCR_ENGINE", None), \
                patch("telegram_demo.image_ocr.LocalOCR", return_value=self.engine) as factory:
            self.assertIs(api._ocr_engine(), self.engine)
            self.assertIs(api._ocr_engine(), self.engine)
        factory.assert_called_once_with()
        self.engine.preflight.assert_not_called()
        self.engine.recognize.assert_not_called()

    def test_wrapper_returns_raw_text_and_propagates_typed_errors(self):
        self.assertEqual(api.ocr_image("fixture.png"), recognized()["text"])
        self.engine.recognize.assert_called_once_with("fixture.png")
        self.engine.recognize.side_effect = OCRError("timeout", "secret path or stderr")
        with self.assertRaises(OCRError):
            api.ocr_image("fixture.png")
        self.assertNotIn("secret", self.output.getvalue())

    def test_available_checks_actual_preflight_not_python_packages(self):
        self.assertTrue(api.ocr_available())
        self.engine.preflight.assert_called_once_with()
        for code in ("missing_binary", "language_unavailable", "process_failed"):
            self.engine.preflight.side_effect = OCRError(code, "private diagnostics")
            self.assertFalse(api.ocr_available())
        self.engine.recognize.assert_not_called()

    def test_success_preserves_caption_raw_text_words_and_provenance(self):
        rec = record()
        original = rec["text"]
        expected = recognized()
        api.apply_ocr_record(rec, "fixture.png")
        self.assertEqual(rec["text"], original)
        self.assertEqual(rec["ocr_text"], expected["text"])
        self.assertEqual(rec["ocr"], {**expected, "status": "success", "error_code": None})
        self.assertTrue(rec["pipeline_status"]["ocr_done"])
        self.assertEqual(rec["analysis"], analyzer.analyze(rec, ocr_text=expected["text"]))
        self.assertEqual(self.engine.recognize.call_count, 1)

    def test_empty_success_is_done_and_replaces_previous_ocr(self):
        self.engine.recognize.return_value = {**recognized(""), "words": []}
        rec = {**record(), "ocr_text": "old OCR"}
        api.apply_ocr_record(rec, "blank.png")
        self.assertEqual(rec["ocr"]["status"], "success")
        self.assertIsNone(rec["ocr"]["error_code"])
        self.assertEqual(rec["ocr_text"], "")
        self.assertTrue(rec["pipeline_status"]["ocr_done"])
        self.assertEqual(rec["analysis"], analyzer.analyze(rec, ocr_text=""))

    def test_typed_failure_is_sanitized_and_keeps_original_and_previous_ocr(self):
        error = OCRError("TIMEOUT: unsafe/path", "PRIVATE_SECRET path and raw stderr")
        self.engine.recognize.side_effect = error
        rec = {**record(), "ocr_text": "Previous OCR"}
        api.apply_ocr_record(rec, "bad.png")
        self.assertFalse(rec["pipeline_status"]["ocr_done"])
        self.assertEqual(rec["ocr"]["status"], "error")
        self.assertEqual(rec["ocr"]["error_code"], "timeout__unsafe_path")
        self.assertEqual(rec["ocr"]["words"], [])
        self.assertEqual(rec["text"], "Original caption — untouched")
        self.assertEqual(rec["ocr_text"], "Previous OCR")
        self.assertEqual(rec["analysis"], analyzer.analyze(rec, ocr_text="Previous OCR"))
        self.assertNotIn("PRIVATE_SECRET", self.output.getvalue())
        self.assertNotIn("PRIVATE_SECRET", json.dumps(rec, default=str))

    def test_unexpected_failure_has_generic_safe_code(self):
        self.engine.recognize.side_effect = RuntimeError("PRIVATE_SECRET")
        rec = record()
        api.apply_ocr_record(rec, "bad.png")
        self.assertEqual(rec["ocr"]["error_code"], "ocr_error")
        self.assertFalse(rec["pipeline_status"]["ocr_done"])
        self.assertNotIn("PRIVATE_SECRET", self.output.getvalue())

    def test_json_reanalysis_preserves_ocr_contribution(self):
        rec = record()
        rec.pop("_msg_ref")
        rec["ocr_text"] = recognized()["text"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.json"
            path.write_text(json.dumps({"posts": [rec]}), encoding="utf-8")
            loaded = keyword_usernames.records_from_json(str(path))
        self.assertEqual(loaded[0]["analysis"], analyzer.analyze(rec, ocr_text=rec["ocr_text"]))
        self.engine.recognize.assert_not_called()


class TestCollectorOCRAsync(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.media_dir = self.temp.name
        self.engine = Mock()
        self.engine.recognize.return_value = recognized()
        engine_patch = patch.object(api, "_OCR_ENGINE", self.engine)
        engine_patch.start()
        self.addCleanup(engine_patch.stop)
        stdout = patch("sys.stdout", new_callable=io.StringIO)
        stdout.start()
        self.addCleanup(stdout.stop)

    async def test_batch_uses_shared_helper_sequentially_off_event_loop(self):
        main_thread = threading.get_ident()
        threads, paths = [], []

        def recognize(path):
            threads.append(threading.get_ident())
            paths.append(path)
            return recognized()

        self.engine.recognize.side_effect = recognize
        client = SimpleNamespace(download_media=AsyncMock(side_effect=["1.JPG", "2.png", "3.mp4"]))
        records = [record(mid) for mid in range(1, 4)]
        with patch.object(api, "apply_ocr_record", wraps=api.apply_ocr_record) as apply:
            await api.download_media(client, records, self.media_dir, True)
        self.assertEqual(apply.call_count, 2)
        self.assertEqual(paths, ["1.JPG", "2.png"])
        self.assertTrue(all(thread != main_thread for thread in threads))
        self.assertEqual([rec["ocr"]["status"] for rec in records[:2]], ["success", "success"])
        self.assertNotIn("ocr", records[2])
        self.assertTrue(all(rec["pipeline_status"]["downloaded"] for rec in records))

    async def test_batch_continues_after_error_and_marks_empty_success_done(self):
        self.engine.recognize.side_effect = [OCRError("invalid_image", "PRIVATE_SECRET"),
                                             {**recognized(""), "words": []}]
        client = SimpleNamespace(download_media=AsyncMock(side_effect=["bad.jpg", "blank.png"]))
        records = [record(1), record(2)]
        await api.download_media(client, records, self.media_dir, True)
        self.assertEqual(records[0]["ocr"]["error_code"], "invalid_image")
        self.assertFalse(records[0]["pipeline_status"]["ocr_done"])
        self.assertEqual(records[1]["ocr"]["status"], "success")
        self.assertTrue(records[1]["pipeline_status"]["ocr_done"])
        self.assertEqual(records[1]["ocr_text"], "")

    async def test_disabled_batch_never_touches_ocr(self):
        client = SimpleNamespace(download_media=AsyncMock(return_value="1.jpg"))
        rec = record()
        await api.download_media(client, [rec], self.media_dir, False)
        self.engine.recognize.assert_not_called()
        self.assertNotIn("ocr", rec)
        self.assertNotIn("ocr_text", rec)

    async def test_watch_shared_helper_offloads_with_two_slots_and_drains_on_disconnect(self):
        loop = asyncio.get_running_loop()
        two_started = asyncio.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        lock = threading.Lock()
        state = {"active": 0, "max": 0, "threads": [], "started": 0}
        main_thread = threading.get_ident()

        def recognize(path):
            with lock:
                state["threads"].append(threading.get_ident())
                state["active"] += 1
                state["started"] += 1
                state["max"] = max(state["max"], state["active"])
                if state["started"] == 2:
                    loop.call_soon_threadsafe(two_started.set)
            try:
                if not release.wait(3):
                    raise AssertionError("Event loop did not release OCR workers")
                return recognized()
            finally:
                with lock:
                    state["active"] -= 1

        self.engine.recognize.side_effect = recognize
        tasks = []
        client = SimpleNamespace(download_media=AsyncMock(side_effect=lambda msg, **kw: f"{msg.id}.png"),
                                 disconnect=AsyncMock())

        def on(event):
            def register(handler):
                client.handler = handler
                return handler
            return register

        async def run_until_disconnected():
            for mid in range(6):
                tasks.append(asyncio.create_task(client.handler(
                    SimpleNamespace(message=SimpleNamespace(id=mid), chat=None))))
            await asyncio.sleep(0)   # accept callbacks; disconnect before OCR completes

        client.on = on
        client.run_until_disconnected = run_until_disconnected
        args = SimpleNamespace(session=None, media_dir=self.media_dir, watch=["fixture"],
                               jev=False, ocr=True, cycles=0, watch_once=False, interval=1,
                               out=str(Path(self.media_dir) / "unused.json"), no_report=True)

        async def heartbeat():
            await asyncio.wait_for(two_started.wait(), timeout=2)
            # This must run while two synchronous OCR calls are blocked in threads.
            self.assertEqual(state["active"], 2)
            self.assertEqual(state["started"], 2)
            release.set()

        with patch.object(api, "open_client", AsyncMock(return_value=(client, None))), \
                patch.object(api, "events", SimpleNamespace(NewMessage=lambda **kw: kw), create=True), \
                patch.object(api, "to_record", side_effect=lambda msg, chat: record(msg.id)), \
                patch.object(api, "apply_ocr_record", wraps=api.apply_ocr_record) as apply, \
                patch.object(api, "write_json", side_effect=lambda path, rows, *a, **kw: rows) as write, \
                patch.object(api, "print_history"):
            try:
                result, _ = await asyncio.wait_for(asyncio.gather(
                    api.run_watch(args, 1, "offline-hash"), heartbeat()), timeout=5)
            finally:
                release.set()
                await asyncio.gather(*tasks, return_exceptions=True)
        self.assertEqual(result, 0)
        self.assertEqual(state["max"], 2)
        self.assertEqual(state["started"], 6)
        self.assertTrue(all(thread != main_thread for thread in state["threads"]))
        self.assertEqual(apply.call_count, 6)
        saved = write.call_args.args[1]
        self.assertEqual(len(saved), 6)
        self.assertTrue(all(rec["ocr"]["status"] == "success" for rec in saved))
        self.assertTrue(all(task.done() for task in tasks))


class TestCollectorOCRCLI(unittest.TestCase):
    def invoke(self, *flags):
        with patch.object(sys, "argv", ["collector", *flags]):
            return api.main()

    def test_help_and_non_ocr_modes_do_not_load_ocr(self):
        with patch.object(api, "_ocr_engine", side_effect=AssertionError("OCR must stay lazy")), \
                patch.object(api, "configure_storage"), patch.object(api, "write_json", return_value=[]), \
                patch.object(api, "report"), patch.object(api, "print_history"), \
                patch.object(api, "run_async", AsyncMock(return_value=0)), \
                patch.object(api, "run_bot_mode", AsyncMock(return_value=0)), \
                patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as caught:
                self.invoke("--help")
            self.assertEqual(caught.exception.code, 0)
            for flags in (("--demo",), ("--login",), ("--login-bot",), ("--setup-bot", "fake")):
                self.assertEqual(self.invoke(*flags), 0)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "report.json"
                path.write_text(json.dumps({"posts": [{"text": "offline"}]}), encoding="utf-8")
                self.assertEqual(self.invoke("--report", str(path)), 0)

    def test_invalid_ocr_combinations_fail_without_preflight_or_storage(self):
        cases = [("--ocr",), ("--ocr", "--channel", "fixture"),
                 ("--ocr", "--media-dir", "unused"),
                 ("--ocr", "--media-dir", "unused", "--keyword", "fixture"),
                 ("--ocr", "--media-dir", "unused", "--watch-once")]
        cases += [("--ocr", "--media-dir", "unused", "--channel", "fixture", *mode)
                  for mode in (("--demo",), ("--report", "missing.json"),
                               ("--login",), ("--login-bot",), ("--setup-bot", "fake"))]
        with patch.object(api, "_ocr_engine") as engine, patch.object(api, "configure_storage") as storage, \
                patch("sys.stderr", new_callable=io.StringIO), patch("sys.stdout", new_callable=io.StringIO):
            for flags in cases:
                with self.subTest(flags=flags), self.assertRaises(SystemExit) as caught:
                    self.invoke(*flags)
                self.assertEqual(caught.exception.code, 2)
        engine.assert_not_called()
        storage.assert_not_called()

    def test_preflight_failure_is_early_sanitized_and_nonzero(self):
        engine = Mock()
        engine.preflight.side_effect = OCRError("language_unavailable", "SECRET_PRIVATE stderr")
        with patch.object(api, "_ocr_engine", return_value=engine), \
                patch.object(api, "configure_storage") as storage, \
                patch.object(api, "run_async", AsyncMock()) as run, \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(self.invoke("--ocr", "--media-dir", "unused", "--channel", "fixture"), 2)
        self.assertIn("language_unavailable", stderr.getvalue())
        self.assertIn("vie+eng", stderr.getvalue())
        self.assertNotIn("SECRET_PRIVATE", stderr.getvalue())
        storage.assert_not_called()
        run.assert_not_called()
        engine.recognize.assert_not_called()

    def test_valid_sources_preflight_before_collector(self):
        for source in ("--channel", "--user", "--search", "--watch"):
            with self.subTest(source=source), patch.object(api, "_ocr_engine") as engine, \
                    patch.object(api, "configure_storage"), \
                    patch.object(api, "run_async", AsyncMock(return_value=0)) as run:
                self.assertEqual(self.invoke("--ocr", "--media-dir", "unused", source, "fixture"), 0)
                engine.return_value.preflight.assert_called_once_with()
                engine.return_value.recognize.assert_not_called()
                self.assertTrue(run.call_args.args[0].ocr)


if __name__ == "__main__":
    unittest.main()
