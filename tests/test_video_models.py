"""Offline model tests: mocked faster-whisper and HTTP plus real loopback HTTP."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error
import zlib

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
from telegram_demo import video_models as vm
from telegram_demo.video_models import VideoModelError, VisionClient, WhisperTranscriber


def png(width=2, height=2):
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\xff\0\0" * width) * height)) + chunk(b"IEND", b""))


def payload(observation=None, **extra):
    return {"model": "resolved-model", "choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(observation if observation is not None else
                              {"description": "A red image.", "ocr_text": ""})}}], **extra}


class Response(io.BytesIO):
    status = 200


class ErrorAssertions:
    def error(self, code, call):
        with self.assertRaises(VideoModelError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        for secret in ("private-api-secret", "private-provider-error", "private-audio"):
            self.assertNotIn(secret, str(caught.exception))
            self.assertNotIn(secret, "".join(traceback.format_exception(caught.exception)))
        return caught.exception


class WhisperTests(ErrorAssertions, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.audio = Path(self.tmp.name) / "audio.wav"
        self.audio.write_bytes(b"offline-audio")
        self.engine = Mock()
        self.engine.transcribe.return_value = (iter([SimpleNamespace(start=0, end=1.5, text=" Xin chào ")]),
                                               SimpleNamespace(language="vi", language_probability=0.98))
        self.module = SimpleNamespace(WhisperModel=Mock(return_value=self.engine))
        self.imp = patch.object(vm.importlib, "import_module", return_value=self.module)
        self.imp.start()
        self.addCleanup(self.imp.stop)

    def test_cpu_int8_local_only_and_output(self):
        client = WhisperTranscriber(download_root="weights", threads=3)
        result = client.transcribe(self.audio)
        self.module.WhisperModel.assert_called_once_with("tiny", device="cpu", compute_type="int8",
            cpu_threads=3, download_root="weights", local_files_only=True)
        self.engine.transcribe.assert_called_once_with(str(self.audio), language="vi")
        self.assertEqual(result, {"provider": "faster-whisper", "model": "tiny", "language": "vi",
            "language_probability": 0.98, "segments": [{"start": 0.0, "end": 1.5, "text": "Xin chào"}],
            "text": "Xin chào"})

    def test_preflight_never_loads(self):
        WhisperTranscriber().preflight()
        self.module.WhisperModel.assert_not_called()

    def test_explicit_download_and_cached_model(self):
        client = WhisperTranscriber(model="base", allow_download=True)
        client.transcribe(self.audio)
        client.transcribe(self.audio)
        self.assertEqual(self.module.WhisperModel.call_count, 1)
        self.assertFalse(self.module.WhisperModel.call_args.kwargs["local_files_only"])

    def test_dependency_and_configuration_distinct(self):
        with patch.object(vm.importlib, "import_module", side_effect=ImportError("private-api-secret")):
            self.error("dependency", WhisperTranscriber().preflight)
        for kwargs in ({"model": ""}, {"threads": 0}, {"threads": True}, {"allow_download": 1},
                       {"language": "invalid"}, {"model": "tiny.en"}, {"download_root": 1}):
            with self.subTest(kwargs=kwargs):
                self.error("configuration", WhisperTranscriber(**kwargs).preflight)

    def test_missing_cache_no_retry_no_download(self):
        self.module.WhisperModel.side_effect = RuntimeError("private-api-secret cache absent")
        self.error("model_unavailable", lambda: WhisperTranscriber().transcribe(self.audio))
        self.assertTrue(self.module.WhisperModel.call_args.kwargs["local_files_only"])
        self.assertEqual(self.module.WhisperModel.call_count, 1)

    def test_library_and_lazy_generator_failures_sanitized(self):
        self.engine.transcribe.side_effect = RuntimeError("private-audio")
        self.error("transcription_error", lambda: WhisperTranscriber().transcribe(self.audio))
        def broken():
            raise RuntimeError("private-audio")
            yield
        self.engine.transcribe.side_effect = None
        self.engine.transcribe.return_value = (broken(), SimpleNamespace(language="vi", language_probability=1))
        self.error("transcription_error", lambda: WhisperTranscriber().transcribe(self.audio))

    def test_missing_audio(self):
        self.error("input", lambda: WhisperTranscriber().transcribe(self.audio.with_name("missing")))
        self.module.WhisperModel.assert_not_called()

    def test_invalid_timestamps_and_text(self):
        for start, end, text in ((float("nan"), 1, "x"), (0, float("inf"), "x"), (-1, 1, "x"),
                                 (2, 1, "x"), (False, 1, "x"), (0, 1, None)):
            with self.subTest(start=start, end=end, text=text):
                self.engine.transcribe.return_value = ([SimpleNamespace(start=start, end=end, text=text)],
                    SimpleNamespace(language="vi", language_probability=1))
                self.error("invalid_response", lambda: WhisperTranscriber().transcribe(self.audio))

    def test_language_validation(self):
        for language, probability in (("", 1), ("vi", float("nan")), ("vi", -0.1), ("vi", 1.1), ("vi", True)):
            self.engine.transcribe.return_value = ([], SimpleNamespace(language=language, language_probability=probability))
            self.error("invalid_response", lambda: WhisperTranscriber().transcribe(self.audio))

    def test_output_limits_reject_not_truncate(self):
        for segments in ([SimpleNamespace(start=0, end=1, text="x" * 64001)],
                         [SimpleNamespace(start=0, end=1, text="x")] * 3001):
            self.engine.transcribe.return_value = (segments, SimpleNamespace(language="vi", language_probability=1))
            self.error("output_limit", lambda: WhisperTranscriber().transcribe(self.audio))

    def test_exact_output_limits_and_empty_audio_result(self):
        self.engine.transcribe.return_value = ([SimpleNamespace(start=0, end=1, text="x" * 64000)],
                                              SimpleNamespace(language="vi", language_probability=1))
        self.assertEqual(len(WhisperTranscriber().transcribe(self.audio)["text"]), 64000)
        self.engine.transcribe.return_value = ([], SimpleNamespace(language="vi", language_probability=1))
        self.assertEqual(WhisperTranscriber().transcribe(self.audio)["text"], "")


class VisionTests(ErrorAssertions, unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.image = Path(self.tmp.name) / "frame.png"
        self.image.write_bytes(png())
        self.frames = [{"path": self.image, "timestamp": 1.125}]
        self.opener = Mock()
        self.build = patch.object(vm.urllib.request, "build_opener", return_value=self.opener)
        self.build.start()
        self.addCleanup(self.build.stop)
        self.client = VisionClient(model="qwen-vl", endpoint="http://127.0.0.1:11434/v1")
        self.respond(payload())

    def respond(self, body):
        self.opener.open.return_value = Response(json.dumps(body).encode())

    def test_per_frame_structured_request_and_provenance(self):
        self.frames[0]["sha256"] = hashlib.sha256(self.image.read_bytes()).hexdigest()
        self.opener.open.side_effect = lambda *args, **kwargs: Response(json.dumps(payload()).encode())
        frames = self.frames + [{"path": self.image, "timestamp": 2}]
        result = self.client.describe(frames)
        self.assertEqual(self.opener.open.call_count, 2)
        self.assertEqual(result["model"], "qwen-vl")
        self.assertEqual(result["resolved_model"], "resolved-model")
        self.assertEqual([f["timestamp"] for f in result["frames"]], [1.125, 2])
        self.assertIsInstance(result["frames"][1]["timestamp"], int)
        req = self.opener.open.call_args.args[0]
        body = json.loads(req.data)
        self.assertEqual(req.full_url, "http://127.0.0.1:11434/v1/chat/completions")
        self.assertEqual(self.opener.open.call_args.kwargs, {"timeout": 60})
        self.assertEqual(body["model"], "qwen-vl")
        self.assertIn("untrusted data", body["messages"][0]["content"])
        self.assertIn("Do not identify faces", body["messages"][0]["content"])
        image = body["messages"][1]["content"][1]["image_url"]["url"]
        self.assertEqual(base64.b64decode(image.split(",")[1]), self.image.read_bytes())
        self.assertNotIn("Authorization", req.headers)
        self.assertEqual(len(self.client.backend_key), 64)
        self.assertEqual(self.client.provenance["endpoint"], req.full_url)

    def test_completed_frames_survive_later_model_failure(self):
        first = payload({'description': 'Already paid frame', 'ocr_text': 'DEMO'})
        with patch.object(self.client, '_request', side_effect=[first, VideoModelError('network_error', 'safe')]):
            with self.assertRaises(VideoModelError) as caught:
                self.client.describe(self.frames * 2)
        self.assertEqual(caught.exception.partial_result['frames'][0]['description'], 'Already paid frame')
        self.assertEqual(len(caught.exception.partial_result['responses']), 1)

    def test_preflight_offline_and_missing_model(self):
        self.client.preflight()
        self.opener.open.assert_not_called()
        self.error("configuration", VisionClient().preflight)

    def test_environment_config_and_key_redaction(self):
        with patch.dict(os.environ, {"VISION_MODEL": "explicit-model", "VISION_API_URL": "https://example.com/v1",
                                     "VISION_API_KEY": "private-api-secret"}):
            client = VisionClient(allow_remote=True)
            self.respond(payload({"description": "private-api-secret echo", "ocr_text": ""},
                                 api_key="different-secret", nested={"Authorization": "Bearer other", "detail": "private-api-secret"}))
            result = client.describe(self.frames)
            serialized = json.dumps(result)
            self.assertNotIn("private-api-secret", serialized)
            self.assertNotIn("different-secret", serialized)
            self.assertNotIn("Bearer other", serialized)
            req = self.opener.open.call_args.args[0]
            self.assertEqual(req.get_header("Authorization"), "Bearer private-api-secret")
            self.assertNotIn("private-api-secret", json.dumps(client.provenance))
            self.assertEqual(client.backend_key, VisionClient(model="explicit-model", endpoint="https://example.com/v1",
                                                             api_key="another", allow_remote=True).backend_key)

    def test_remote_gate_and_auth(self):
        self.error("remote_disabled", VisionClient(model="m").preflight)
        self.error("configuration", VisionClient(model="m", allow_remote=True).preflight)
        VisionClient(model="m", allow_remote=True, api_key="private-api-secret").preflight()
        self.opener.open.assert_not_called()

    def test_url_safety(self):
        bad = ["http://example.com/v1", "file:///tmp/image", "ftp://localhost/v1", "https://user:secret@example.com/v1",
               "https://example.com/v1?key=secret", "https://example.com/v1#fragment", "https://example.com:0/v1",
               "https://example.com:bad/v1", "http://127.0.0.1.example.com/v1", "https://example.com\\@localhost/v1",
               "https://example.com/%76%31", "http://[::1", "https://example.com/other", "https://example.com/v1\n"]
        for endpoint in bad:
            with self.subTest(endpoint=endpoint):
                self.error("configuration", VisionClient(model="m", endpoint=endpoint, allow_remote=True, api_key="key").preflight)

    def test_loopback_hosts(self):
        for endpoint in ("http://localhost:1234", "http://127.0.0.2/v1", "http://[::1]:1234/v1/chat/completions"):
            VisionClient(model="m", endpoint=endpoint).preflight()
        self.error("remote_disabled", VisionClient(model="m", endpoint="https://example.com/v1").preflight)

    def test_config_validation(self):
        for kwargs in ({"provider": "fake"}, {"timeout": 0}, {"timeout": 301}, {"timeout": float("nan")},
                       {"timeout": True}, {"allow_remote": 1}, {"api_key": "bad\nheader"}, {"model": "\n"}):
            config = {"model": "m", "endpoint": "http://localhost/v1", **kwargs}
            self.error("configuration", VisionClient(**config).preflight)

    def test_huge_numbers_and_deep_json_are_rejected(self):
        self.error("input", lambda: self.client.describe([{"path": self.image, "timestamp": 10 ** 500}]))
        self.error("configuration", VisionClient(model="m", timeout=10 ** 500).preflight)
        self.opener.open.return_value = Response(b'{"nested":' + b'[' * 70 + b'0' + b']' * 70 + b'}')
        self.error("invalid_response", lambda: self.client.describe(self.frames))

    def test_error_code_is_sanitized(self):
        self.assertEqual(VideoModelError("secret\ncode", "Generic error.").code, "model_error")

    def test_frames_limits_before_network(self):
        for frames in ([], self.frames * 61, None, [{"path": self.image, "timestamp": -1}],
                       [{"path": self.image, "timestamp": float("inf")}],
                       [{"path": self.image, "timestamp": True}], [{"path": self.image, "timestamp": "1"}],
                       [{"path": self.image, "timestamp": 1, "sha256": "bad"}],
                       [{"path": self.image.with_name("missing"), "timestamp": 1}]):
            self.error("input", lambda: self.client.describe(frames))
        self.opener.open.assert_not_called()

    def test_image_dimensions_and_bytes(self):
        for image in (png(641, 2), png(1, 641), png(0, 0)):
            self.image.write_bytes(image)
            self.error("input_limit", lambda: self.client.describe(self.frames))
        self.image.write_bytes(b"x" * (vm.MAX_IMAGE_BYTES + 1))
        self.error("input_limit", lambda: self.client.describe(self.frames))
        self.image.write_bytes(b"not an image")
        self.error("input", lambda: self.client.describe(self.frames))
        self.opener.open.assert_not_called()

    def test_total_encoded_cap_and_batch_validated_first(self):
        with patch.object(vm, "MAX_ENCODED_BYTES", 100):
            self.error("input_limit", lambda: self.client.describe(self.frames * 2))
        self.error("input", lambda: self.client.describe(self.frames + [{"path": "missing", "timestamp": 2}]))
        self.opener.open.assert_not_called()

    def test_jpeg_webp_dimensions(self):
        jpeg = b"\xff\xd8\xff\xc0" + struct.pack(">HBHHB", 8, 8, 20, 30, 1) + b"\xff\xd9"
        self.assertEqual(vm._image_info(jpeg), ("image/jpeg", 30, 20))
        webp = b"RIFF" + b"\0" * 4 + b"WEBPVP8X" + b"\0" * 8 + (29).to_bytes(3, "little") + (19).to_bytes(3, "little")
        self.assertEqual(vm._image_info(webp), ("image/webp", 30, 20))
        self.error("input", lambda: vm._image_info(webp[:20] + b"\x02" + webp[21:]))

    def test_strict_observation_schema_and_lengths(self):
        bad = [{"description": "x"}, {"description": "x", "ocr_text": "", "extra": "x"},
               {"description": 1, "ocr_text": ""}, {"description": "x", "ocr_text": None},
               {"description": "x" * 4001, "ocr_text": ""}, {"description": "x", "ocr_text": "x" * 8001}, []]
        for observation in bad:
            self.respond(payload(observation))
            self.error("invalid_response", lambda: self.client.describe(self.frames))
        self.respond(payload({"description": "x" * 4000, "ocr_text": "x" * 8000}))
        self.assertEqual(len(self.client.describe(self.frames)["frames"][0]["ocr_text"]), 8000)

    def test_duplicate_nonfinite_and_non_json(self):
        for raw in (b'{"choices":[],"choices":[]}', b'{"x":NaN}', b'bad json', b'\xff', b'[]', b'{"x":Infinity}', b'{"x":1e400}'):
            self.opener.open.return_value = Response(raw)
            self.error("invalid_response", lambda: self.client.describe(self.frames))
        for text in ('{"description":"a","description":"b","ocr_text":""}',
                     '{"description":"a","ocr_text":"","x":NaN}', '```json\n{}\n```'):
            body = payload()
            body["choices"][0]["message"]["content"] = text
            self.respond(body)
            self.error("invalid_response", lambda: self.client.describe(self.frames))

    def test_finish_reason_and_shape_validation(self):
        cases = [{}, {"choices": []}, {"choices": [None]}, {"choices": "bad"}]
        truncated = payload()
        truncated["choices"][0]["finish_reason"] = "length"
        cases += [truncated, payload(model=1)]
        for body in cases:
            self.respond(body)
            self.error("invalid_response", lambda: self.client.describe(self.frames))

    def test_http_errors_timeout_and_response_cap_no_retry(self):
        for exception, code in ((urllib.error.HTTPError("http://private-api-secret", 401, "private-provider-error", {}, None), "http_error"),
                                (urllib.error.URLError("private-provider-error"), "network_error"),
                                (TimeoutError("private-provider-error"), "network_error")):
            self.opener.open.reset_mock()
            self.opener.open.side_effect = exception
            self.error(code, lambda: self.client.describe(self.frames))
            self.opener.open.assert_called_once()
        self.opener.open.side_effect = None
        self.opener.open.return_value = Response(b"x" * (vm.MAX_RESPONSE_BYTES + 1))
        self.error("response_limit", lambda: self.client.describe(self.frames))

    def test_http_error_message_names_status_and_provider_reason(self):
        body = json.dumps({"error": {"code": 404, "status": "NOT_FOUND",
                                     "message": "This model models/gemini-2.5-flash is no longer available to new users."}}).encode()
        self.opener.open.side_effect = urllib.error.HTTPError(self.client.endpoint, 404, "Not Found", {},
                                                             io.BytesIO(body))
        message = str(self.error("http_error", lambda: self.client.describe(self.frames)))
        self.assertIn("404", message)
        self.assertIn("NOT_FOUND", message)
        self.assertIn("no longer available", message)
        # A key echoed by the provider is redacted before the message is surfaced.
        keyed = VisionClient(model="qwen-vl", endpoint="http://127.0.0.1:11434/v1", api_key="private-api-secret")
        body = json.dumps({"error": {"status": "UNAUTHORIZED", "message": "API key not valid: private-api-secret"}}).encode()
        self.opener.open.side_effect = urllib.error.HTTPError(keyed.endpoint, 401, "Unauthorized", {}, io.BytesIO(body))
        message = str(self.error("http_error", lambda: keyed.describe(self.frames)))
        self.assertIn("401", message)
        self.assertIn("[đã ẩn]", message)

    def test_gemini_official_header_and_payload(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "private-api-secret"}):
            client = VisionClient(provider="gemini", model="gemini-user-selected", allow_remote=True)
            self.respond({"modelVersion": "gemini-resolved", "candidates": [{"finishReason": "STOP", "content": {
                "parts": [{"text": '{"description":"A red image.","ocr_text":""}'}]}}]})
            result = client.describe(self.frames)
            self.assertEqual(result["resolved_model"], "gemini-resolved")
            req = self.opener.open.call_args.args[0]
            self.assertEqual(req.full_url, "https://generativelanguage.googleapis.com/v1beta/models/gemini-user-selected:generateContent")
            self.assertEqual(req.get_header("X-goog-api-key"), "private-api-secret")
            self.assertNotIn("private-api-secret", req.full_url)
            body = json.loads(req.data)
            self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
            self.assertEqual(body["contents"][0]["parts"][1]["inlineData"]["mimeType"], "image/png")

    def test_gemini_accepts_thought_signature_parts(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "private-api-secret"}):
            client = VisionClient(provider="gemini", model="gemini-3.1-flash-lite", allow_remote=True)
            self.respond({"modelVersion": "gemini-3.1-flash-lite", "candidates": [{"finishReason": "STOP", "content": {
                "parts": [{"text": '{"description":"Ảnh có chữ.","ocr_text":"OCR-2026"}',
                           "thoughtSignature": "EpwBCpkBAWkUfRPx"}]}}]})
            result = client.describe(self.frames)
            self.assertEqual(result["resolved_model"], "gemini-3.1-flash-lite")
            self.assertEqual(result["frames"][0]["description"], "Ảnh có chữ.")
            # A thinking part is dropped, not merged into the answer.
            self.respond({"candidates": [{"finishReason": "STOP", "content": {"parts": [
                {"text": "internal reasoning", "thought": True},
                {"text": '{"description":"d","ocr_text":""}'}]}}]})
            self.assertEqual(client.describe(self.frames)["frames"][0]["description"], "d")

    def test_gemini_remote_and_endpoint_restriction(self):
        self.error("remote_disabled", VisionClient(provider="gemini", model="m", api_key="key").preflight)
        for kwargs in ({"endpoint": "http://localhost/v1"}, {"model": "models/m"}, {"model": "m?key=x"}):
            self.error("configuration", VisionClient(provider="gemini", model=kwargs.pop("model", "m"),
                api_key="key", allow_remote=True, **kwargs).preflight)

    def test_gemini_invalid_outputs(self):
        client = VisionClient(provider="gemini", model="m", api_key="key", allow_remote=True)
        for body in ({}, {"candidates": []}, {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": []}}]},
                     {"candidates": [{"content": {"parts": [{"inlineData": {}}]}}]}):
            self.respond(body)
            self.error("invalid_response", lambda: client.describe(self.frames))


class LoopbackTests(ErrorAssertions, unittest.TestCase):
    def test_real_http_no_redirect_and_optional_bearer(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received.append((self.path, self.headers.get("Authorization"), json.loads(body)))
                if self.path.startswith("/redirect/"):
                    self.send_response(302)
                    self.send_header("Location", "/v1/chat/completions")
                    self.end_headers()
                else:
                    data = json.dumps(payload()).encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
                image = Path(tmp) / "image.png"
                image.write_bytes(png())
                frames = [{"path": image, "timestamp": 0}]
                base = "http://127.0.0.1:" + str(server.server_port)
                result = VisionClient(model="qwen", endpoint=base, api_key="private-api-secret").describe(frames)
                self.assertEqual(result["frames"][0]["timestamp"], 0)
                self.assertEqual(received[0][1], "Bearer private-api-secret")
                self.error("http_error", lambda: VisionClient(model="qwen", endpoint=base + "/redirect/chat/completions",
                    api_key="private-api-secret").describe(frames))
                self.assertEqual(len(received), 2)  # Never follow redirect or retry.
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
