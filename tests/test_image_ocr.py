"""Offline image/OCR tests: synthetic headers and mocked local executables only."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from telegram_demo import image_ocr
from telegram_demo.image_ocr import LocalOCR, OCRError


def png(width=100, height=80, animated=False):
    def chunk(kind, content):
        return (struct.pack(">I", len(content)) + kind + content
                + struct.pack(">I", zlib.crc32(kind + content)))
    body = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    if animated:
        body += chunk(b"acTL", struct.pack(">II", 2, 0))
    return b"\x89PNG\r\n\x1a\n" + body + chunk(b"IEND", b"")


def jpeg(width=100, height=80):
    return b"\xff\xd8\xff\xc0\x00\x0b\x08" + struct.pack(">HH", height, width) + b"\x01\x01\x11\x00\xff\xd9"


def webp(width=100, height=80, animated=False):
    payload = bytes([2 if animated else 0, 0, 0, 0]) + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    body = b"WEBPVP8X" + struct.pack("<I", 10) + payload
    return b"RIFF" + struct.pack("<I", len(body)) + body


TSV_HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"


def tsv(rows=None):
    if rows is None:
        rows = [(1, "Xin", "92.5"), (1, "chào", "86"), (2, "Việt Nam", "70")]
    return (TSV_HEADER + "".join("5\t1\t1\t1\t%d\t1\t2\t3\t20\t10\t%s\t%s\n" % (line, confidence, text)
                               for line, text, confidence in rows)).encode()


class MockTools:
    def __init__(self):
        self.calls = []
        self.tsv = tsv()
        self.probe = None
        self.on_command = None
        self.normalized = None

    def __call__(self, args):
        self.calls.append(args)
        if self.on_command:
            self.on_command(args)
        if "--version" in args:
            return b"tesseract 5.5.1\n SECRET PRIVATE ignored metadata\n"
        if "--list-langs" in args:
            return b"List of available languages in SECRET PRIVATE (3):\neng\nosd\nvie\n"
        if "-show_entries" in args:
            if self.probe is not None:
                return self.probe
            format_name, width, height = image_ocr._header(Path(args[-1]).read_bytes())
            return json.dumps({"streams": [{"width": width, "height": height,
                                            "codec_name": {"PNG": "png", "JPEG": "mjpeg", "WEBP": "webp"}[format_name]}]}).encode()
        if "-frames:v" in args:
            output = Path(args[-1])
            output.write_bytes(self.normalized or (jpeg() if output.suffix == ".jpg" else png()))
            return b""
        return self.tsv


class OCRTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source.png"
        self.source.write_bytes(png())
        self.tools = MockTools()
        self.which = patch.object(image_ocr.shutil, "which", side_effect=lambda name: "/mock/" + Path(name).name).start()
        self.run = patch.object(LocalOCR, "_run", side_effect=self.tools).start()
        self.addCleanup(patch.stopall)
        self.ocr = LocalOCR()

    def assert_code(self, code, action):
        with self.assertRaises(OCRError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertNotIn(str(self.root), str(caught.exception))
        return caught.exception

    def test_lazy_constructor_and_sanitized_error_code(self):
        self.which.assert_not_called()
        self.run.assert_not_called()
        self.assertEqual(OCRError("BAD\n/path SECRET", "safe").code, "bad__path_secret")

    def test_invalid_configuration(self):
        for name in ("timeout", "max_bytes", "max_pixels", "max_side"):
            for value in (0, -1, True, None, "1", float("inf"), float("nan"), 10 ** 1000):
                with self.subTest(name=name, value=repr(value)):
                    self.assert_code("configuration", lambda: LocalOCR(**{name: value}))
        for name in ("max_bytes", "max_pixels", "max_side"):
            self.assert_code("configuration", lambda: LocalOCR(**{name: 1.5}))
        for value in ("", "../eng", "eng+", "eng --psm 1", ["eng"], "eng\nsecret"):
            self.assert_code("configuration", lambda: LocalOCR(languages=value))
        for value in (-1, 14, True, "11", 1.5):
            self.assert_code("configuration", lambda: LocalOCR(psm=value))

    def test_preflight_contract_and_missing_language(self):
        self.assertEqual(self.ocr.preflight(), {"provider": "tesseract", "version": "5.5.1",
                                              "languages_available": ["eng", "osd", "vie"],
                                              "languages_requested": ["vie", "eng"]})
        self.assert_code("language_unavailable", lambda: LocalOCR(languages="fra").preflight())
        self.assertNotIn("SECRET", json.dumps(self.ocr.preflight()))

    def test_preflight_rejects_untrusted_version_output(self):
        original = self.tools.__call__
        def malformed(args):
            return b"SECRET PRIVATE invalid version" if "--version" in args else original(args)
        self.run.side_effect = malformed
        self.assert_code("invalid_output", self.ocr.preflight)

    def test_exact_text_word_and_frame_limits(self):
        self.tools.tsv = tsv([(1, "x" * 32000, "50")])
        self.assertEqual(len(self.ocr.recognize(self.source)["text"]), 32000)
        self.tools.tsv = tsv([(1, "a", "50")] * 4000)
        self.assertEqual(len(self.ocr.recognize(self.source)["words"]), 4000)
        self.tools.tsv = tsv([(1, "x" * 23999, "50")])
        pair = [{"path": self.source, "timestamp": 0}, {"path": self.source, "timestamp": 1}]
        self.assertEqual(len(self.ocr.recognize_frames(pair)["text"]), 48000)
        self.tools.tsv = TSV_HEADER.encode()
        frames = [{"path": self.source, "timestamp": index} for index in range(60)]
        self.assertEqual(len(self.ocr.recognize_frames(frames)["frames"]), 60)

    def test_frame_engine_timeout_keeps_prior_observations(self):
        original = self.ocr.recognize
        calls = 0
        def recognize(path):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OCRError("timeout", "Local image processing timed out.")
            return original(path)
        with patch.object(self.ocr, "recognize", side_effect=recognize):
            error = self.assert_code("timeout", lambda: self.ocr.recognize_frames([
                {"path": self.source, "timestamp": 0}, {"path": self.source, "timestamp": 1}]))
        self.assertEqual(len(error.partial_result["frames"]), 1)
        self.assertEqual(error.partial_result["frames"][0]["text"], "Xin chào\nViệt Nam")

    def test_missing_binary_and_user_local_fallback(self):
        self.which.side_effect = lambda name: str(name) if str(name).startswith(str(Path.home() / ".local/bin")) else None
        self.assertEqual(self.ocr._binary("tesseract"), str(Path.home() / ".local/bin/tesseract"))
        self.which.side_effect = None
        self.which.return_value = None
        self.assert_code("missing_binary", self.ocr.preflight)
        for selected in ("", 12, b"binary", "a\x00b"):
            self.assert_code("missing_binary", lambda: LocalOCR(tesseract=selected).preflight())

    def test_inspect_formats_and_original_hash(self):
        for extension, payload in (("png", png()), ("jpg", jpeg()), ("webp", webp())):
            with self.subTest(format=extension):
                source = self.root / ("file." + extension)
                source.write_bytes(payload)
                result = self.ocr.inspect(source)
                self.assertEqual(result, {"path": str(source), "sha256": hashlib.sha256(payload).hexdigest(),
                                          "size": len(payload), "width": 100, "height": 80,
                                          "format": {"png": "PNG", "jpg": "JPEG", "webp": "WEBP"}[extension]})
        self.assertTrue(all("-show_entries" in args for args in self.tools.calls))
        self.assertTrue(all(str(self.source) not in args for args in self.tools.calls))

    def test_reject_nonlocal_special_symlinks_and_stdin_names(self):
        for value in ("https://example.test/image.png", "file:/etc/passwd", "pipe:0", "data:image/png",
                      "", None, b"image.png", 12, "a\x00b", self.root):
            self.assert_code("invalid_input", lambda: self.ocr.inspect(value))
        self.assert_code("input_missing", lambda: self.ocr.inspect(self.root / "missing.png"))
        symlink = self.root / "link.png"
        symlink.symlink_to(self.source)
        self.assert_code("invalid_input", lambda: self.ocr.inspect(symlink))
        linked_dir = self.root / "linked"
        linked_dir.symlink_to(self.root, target_is_directory=True)
        self.assert_code("invalid_input", lambda: self.ocr.inspect(linked_dir / self.source.name))
        fifo = self.root / "fifo.png"
        os.mkfifo(fifo)
        self.assert_code("invalid_input", lambda: self.ocr.inspect(fifo))
        self.assertFalse(self.tools.calls)
        stdin_name = self.root / "-"
        stdin_name.write_bytes(png())
        self.ocr.inspect(stdin_name)
        self.assertTrue(Path(self.tools.calls[-1][-1]).is_absolute())
        self.assertNotEqual(self.tools.calls[-1][-1], "-")

    def test_content_not_extension_and_unsupported_formats(self):
        misleading = self.root / "image.svg"
        misleading.write_bytes(png())
        self.assertEqual(self.ocr.inspect(misleading)["format"], "PNG")
        for data in (b"GIF89a", b"<svg>secret</svg>", b"%PDF1.7", b"SECRET malformed"):
            self.source.write_bytes(data)
            self.assert_code("unsupported_format", lambda: self.ocr.inspect(self.source))

    def test_byte_and_pixel_limits_before_any_probe_or_decode(self):
        self.assert_code("byte_limit", lambda: LocalOCR(max_bytes=1).inspect(self.source))
        self.source.write_bytes(png(10000, 10000))
        self.assert_code("pixel_limit", lambda: self.ocr.inspect(self.source))
        self.assertFalse(self.tools.calls)
        self.source.write_bytes(b"")
        self.assert_code("invalid_image", lambda: self.ocr.inspect(self.source))

    def test_animations_and_truncated_headers(self):
        for data in (png(animated=True), webp(animated=True)):
            self.source.write_bytes(data)
            self.assert_code("animated_image", lambda: self.ocr.inspect(self.source))
        for data in (png()[:-1], png(0, 80), jpeg()[:-2], webp()[:-1], b"\xff\xd8\xff\xd9"):
            self.source.write_bytes(data)
            self.assert_code("invalid_image", lambda: self.ocr.inspect(self.source))
        self.assertFalse(self.tools.calls)

    def test_invalid_probe_schema_or_header_disagreement(self):
        for payload in (b"SECRET", b"null", b"[]", b"{}", b"{" * 2000,
                        b'{"streams":[]}', b'{"streams":[{"width":100,"height":79,"codec_name":"png"}]}',
                        b'{"streams":[{"width":true,"height":80,"codec_name":"png"}]}',
                        b'{"streams":[{"width":100,"height":80,"codec_name":"h264"}]}'):
            self.tools.probe = payload
            self.assert_code("invalid_image", lambda: self.ocr.inspect(self.source))

    def test_recognition_reconstructs_lines_and_documents_confidence(self):
        result = self.ocr.recognize(self.source)
        self.assertEqual(result["text"], "Xin chào\nViệt Nam")
        self.assertEqual(result["words"][0], {"text": "Xin", "confidence": 92.5,
                                              "bbox": {"left": 2, "top": 3, "width": 20, "height": 10}})
        self.assertEqual(result["provider"], "tesseract")
        self.assertEqual(result["languages"], ["vie", "eng"])
        self.assertEqual(result["preprocessing"]["width"], 100)
        self.assertIn("uncalibrated", " ".join(result["limitations"]))
        self.assertNotIn("SECRET", json.dumps(result))
        commands = self.tools.calls
        normalize = next(args for args in commands if "-frames:v" in args)
        self.assertEqual(normalize[normalize.index("-protocol_whitelist") + 1], "file")
        self.assertEqual(normalize[normalize.index("-frames:v") + 1], "1")
        self.assertIn("-n", normalize)
        self.assertIn("-nostdin", normalize)
        tesseract_args = commands[-1]
        self.assertEqual(tesseract_args[-6:], ["stdout", "-l", "vie+eng", "--psm", "11", "tsv"])
        self.assertFalse(Path(tesseract_args[1]).exists())

    def test_empty_tsv_and_nonword_rows(self):
        self.tools.tsv = (TSV_HEADER + "1\t1\t0\t0\t0\t0\t0\t0\t100\t80\t-1\t\n").encode()
        result = self.ocr.recognize(self.source)
        self.assertEqual(result["text"], "")
        self.assertEqual(result["words"], [])

    def test_invalid_tsv_scores_geometry_and_structure(self):
        payloads = [b"SECRET", b"\xff", tsv([(1, "word", "nan")]), tsv([(1, "word", "101")]),
                    tsv([(1, "word", "-1")]), tsv().replace(b"\t2\t3\t20\t10", b"\t99\t3\t20\t10"),
                    tsv().replace(b"92.5\tXin", b"92.5\tXin\textra"), tsv()[:-6] + b"\x00bad\n"]
        for payload in payloads:
            self.tools.tsv = payload
            self.assert_code("invalid_output", lambda: self.ocr.recognize(self.source))

    def test_word_text_and_output_limits_explicit_without_truncation(self):
        for payload, code in ((tsv([(1, "a", "50")] * 4001), "word_limit"),
                              (tsv([(1, "a" * 32001, "50")]), "text_limit"),
                              (b"x" * (1024 * 1024 + 1), "output_limit")):
            self.tools.tsv = payload
            self.assert_code(code, lambda: self.ocr.recognize(self.source))

    def test_source_changes_during_probe_or_ocr_are_rejected(self):
        for stage in ("-show_entries", "tsv"):
            self.source.write_bytes(png())
            def change(args):
                if stage in args:
                    self.source.write_bytes(png(110, 80))
            self.tools.on_command = change
            self.assert_code("input_changed", lambda: self.ocr.recognize(self.source))

    def test_source_replacement_symlink_is_rejected(self):
        other = self.root / "other.png"
        other.write_bytes(png())
        def replace(args):
            if "tsv" in args:
                self.source.unlink()
                self.source.symlink_to(other)
        self.tools.on_command = replace
        self.assert_code("input_changed", lambda: self.ocr.recognize(self.source))

    def test_normalized_limits(self):
        self.tools.normalized = png(2001, 1)
        self.assert_code("invalid_output", lambda: self.ocr.recognize(self.source))
        self.tools.normalized = b"not an image"
        self.assert_code("unsupported_format", lambda: self.ocr.recognize(self.source))

    def test_prepare_vision_fresh_private_jpeg_and_hash(self):
        directory = self.root / "vision"
        frame = self.ocr.prepare_for_vision(self.source, directory)
        output = Path(frame["path"])
        self.assertEqual(output.parent, directory)
        self.assertEqual(output.suffix, ".jpg")
        self.assertEqual(frame["timestamp"], 0.0)
        self.assertEqual(frame["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o400)
        original = output.read_bytes()
        self.assert_code("artifact_directory", lambda: self.ocr.prepare_for_vision(self.source, directory))
        self.assertEqual(output.read_bytes(), original)
        self.tools.normalized = jpeg(641, 10)
        self.assert_code("invalid_output", lambda: self.ocr.prepare_for_vision(self.source, self.root / "too-large"))

    def test_frames_timestamps_hashes_and_combined_contract(self):
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        result = self.ocr.recognize_frames([{"path": self.source, "timestamp": 0.0, "sha256": digest},
                                            {"path": self.source, "timestamp": 2.5}])
        self.assertEqual([frame["timestamp"] for frame in result["frames"]], [0, 2.5])
        self.assertEqual(result["frames"][0]["sha256"], digest)
        self.assertEqual(result["text"], "Xin chào\nViệt Nam\n\nXin chào\nViệt Nam")
        self.assertEqual(result["version"], "5.5.1")
        self.assertEqual(self.ocr.recognize_frames([])["frames"], [])

    def test_frame_hash_mismatch_retains_completed_frames(self):
        failure = self.assert_code("hash_mismatch", lambda: self.ocr.recognize_frames([
            {"path": self.source, "timestamp": 0},
            {"path": self.source, "timestamp": 1, "sha256": "0" * 64}]))
        self.assertEqual(len(failure.partial_result["frames"]), 1)
        self.assertEqual(failure.partial_result["text"], "Xin chào\nViệt Nam")
        self.assertEqual(failure.partial_result["frames"][0]["timestamp"], 0)

    def test_frame_combined_limit_and_per_frame_error_partial(self):
        self.tools.tsv = tsv([(1, "x" * 25000, "50")])
        failure = self.assert_code("text_limit", lambda: self.ocr.recognize_frames([
            {"path": self.source, "timestamp": 0}, {"path": self.source, "timestamp": 1}]))
        self.assertEqual(len(failure.partial_result["frames"]), 1)
        self.assertEqual(len(failure.partial_result["text"]), 25000)
        failure = self.assert_code("input_missing", lambda: self.ocr.recognize_frames([
            {"path": self.source, "timestamp": 0}, {"path": self.root / "absent.png", "timestamp": 1}]))
        self.assertEqual(len(failure.partial_result["frames"]), 1)

    def test_entire_frame_structure_validated_before_any_inspection_or_ocr(self):
        for invalid, code in (({'path': self.source, 'timestamp': -1}, 'invalid_frame'),
                              ({'path': self.source, 'timestamp': 1, 'sha256': 'bad'}, 'hash_mismatch')):
            with patch.object(self.ocr, 'inspect') as inspect, patch.object(self.ocr, 'recognize') as recognize:
                failure = self.assert_code(code, lambda: self.ocr.recognize_frames([
                    {'path': self.source, 'timestamp': 0}, invalid]))
                inspect.assert_not_called()
                recognize.assert_not_called()
                self.assertEqual(failure.partial_result['frames'], [])

    def test_frame_validation_caps_and_mutation_after_recognition(self):
        self.assert_code("frame_limit", lambda: self.ocr.recognize_frames([{}] * 61))
        self.assert_code("frame_limit", lambda: self.ocr.recognize_frames(iter([])))
        for frame in ({}, {"path": self.source}, {"path": self.source, "timestamp": -1},
                      {"path": self.source, "timestamp": float("nan")}, {"path": self.source, "timestamp": True}):
            self.assert_code("invalid_frame", lambda: self.ocr.recognize_frames([frame]))
        self.assert_code("hash_mismatch", lambda: self.ocr.recognize_frames([
            {"path": self.source, "timestamp": 0, "sha256": "SECRET"}]))
        original = self.ocr.recognize
        def change(path):
            result = original(path)
            self.source.write_bytes(png(101, 80))
            return result
        with patch.object(self.ocr, "recognize", side_effect=change):
            failure = self.assert_code("input_changed", lambda: self.ocr.recognize_frames([
                {"path": self.source, "timestamp": 0}]))
        self.assertEqual(failure.partial_result["frames"], [])


class FakeSelector:
    def __init__(self):
        self.keys = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def register(self, fileobj, events, data):
        self.keys[fileobj] = types.SimpleNamespace(fileobj=fileobj, fd=fileobj.fileno(), data=data)

    def unregister(self, fileobj):
        del self.keys[fileobj]

    def get_map(self):
        return self.keys

    def select(self, timeout):
        return [(key, 1) for key in self.keys.values()]


class FakeProcess:
    def __init__(self, stdout=b"ok", stderr=b"", status=0):
        self.stdout = tempfile.TemporaryFile()
        self.stderr = tempfile.TemporaryFile()
        self.stdout.write(stdout)
        self.stdout.seek(0)
        self.stderr.write(stderr)
        self.stderr.seek(0)
        self.status = status
        self.killed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stdout.close()
        self.stderr.close()
        return False

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return self.status


class RunnerTests(unittest.TestCase):
    def run_fake(self, process, timeout=30):
        self.addCleanup(patch.stopall)
        self.popen = patch.object(image_ocr.subprocess, "Popen", return_value=process).start()
        patch.object(image_ocr.selectors, "DefaultSelector", FakeSelector).start()
        return LocalOCR(timeout=timeout)._run(["/mock/tool", "arg"])

    def assert_failure(self, code, process, **kwargs):
        with self.assertRaises(OCRError) as caught:
            self.run_fake(process, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertTrue(process.killed)

    def test_shell_false_no_stdin_and_bounded_pipes(self):
        self.assertEqual(self.run_fake(FakeProcess()), b"ok")
        args, kwargs = self.popen.call_args
        self.assertEqual(args[0], ["/mock/tool", "arg"])
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], subprocess.PIPE)

    def test_stdout_and_stderr_overflow_are_explicit_and_killed(self):
        self.assert_failure("output_limit", FakeProcess(stdout=b"x" * (1024 * 1024 + 1)))
        patch.stopall()
        self.assert_failure("output_limit", FakeProcess(stderr=b"SECRET" * 200000))

    def test_raw_tool_error_is_never_reflected(self):
        self.assert_failure("process_failed", FakeProcess(stderr=b"SECRET KEY PRIVATE /path", status=1))

    def test_deadline_kills_and_waits(self):
        process = FakeProcess()
        with patch.object(image_ocr.time, "monotonic", side_effect=[0, 2]):
            self.assert_failure("timeout", process, timeout=1)

    def test_spawn_errors_are_sanitized(self):
        for error, code in ((FileNotFoundError("SECRET"), "missing_binary"),
                            (PermissionError("SECRET"), "process_failed")):
            with patch.object(image_ocr.subprocess, "Popen", side_effect=error):
                with self.assertRaises(OCRError) as caught:
                    LocalOCR()._run(["/mock/tool"])
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("SECRET", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
