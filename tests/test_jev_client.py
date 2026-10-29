"""Fully offline urllib-mocked tests for the Jev choice API client."""
from __future__ import annotations

import copy
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request
import urllib.response
from email.message import Message

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import jev_client
from jev_client import JevClient, JevError


QUESTIONS = {
    "risk": {"type": "choice", "instructions": "Phân loại bài viết 日本語",
             "criteria": {"safe": "Bình thường", "risk": "Cần xem xét"}},
    "topic": {"type": "choice", "instructions": "Choose topic",
              "criteria": {"other": "Other"}},
}


def valid_response():
    return {
        "model": "jev-resolved-2026",
        "answers": {
            "risk": {"type": "choice", "choice": "risk", "confidence": 0.7,
                     "probabilities": {"safe": 0.3, "risk": 0.7}},
            "topic": {"type": "choice", "choice": "other", "confidence": 1,
                      "probabilities": {"other": 1}},
        },
        "usage": {"input_tokens": 14, "output_tokens": 7},
        "extra": {"unchanged": "Tiếng Việt"},
    }


class Response(io.BytesIO):
    status = 200

    def __init__(self, body):
        super().__init__(body)
        self.read = Mock(wraps=self.read)


class TestJevClient(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.opener = Mock()
        self.build = patch("jev_client.urllib.request.build_opener", return_value=self.opener)
        self.build_mock = self.build.start()
        self.addCleanup(self.build.stop)
        self.client = JevClient(api_key="test-key")
        self.respond(valid_response())

    def respond(self, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return self.respond_bytes(raw)

    def respond_bytes(self, raw):
        response = Response(raw)
        self.opener.open.return_value = response
        return response

    def assert_code(self, code, operation=None):
        with self.assertRaises(JevError) as caught:
            (operation or (lambda: self.client.ask({"body": "private-body"}, QUESTIONS)))()
        self.assertEqual(caught.exception.code, code)
        for secret in ("test-key", "private-body", "private-server-detail"):
            self.assertNotIn(secret, str(caught.exception))
            self.assertNotIn(secret, repr(caught.exception))
        return caught.exception

    def test_unicode_post_and_unmodified_response(self):
        expected = valid_response()
        original_questions = copy.deepcopy(QUESTIONS)
        state = {"text": "Cảnh báo lừa đảo — 日本語 🧪"}
        result = self.client.ask(state, QUESTIONS)
        self.assertEqual(result, expected)
        self.assertEqual(QUESTIONS, original_questions)
        self.assertEqual(self.client.model, "jev-latest")
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(request.data.decode("utf-8")),
                         {"model": "jev-latest", "state": state, "questions": QUESTIONS})
        self.assertIn("Cảnh báo".encode(), request.data)
        self.assertEqual(self.opener.open.call_args.kwargs, {"timeout": 60})
        self.opener.open.assert_called_once()

    def test_raw_decoded_dict_identity_is_preserved(self):
        expected = valid_response()
        with patch("jev_client.json.loads", return_value=expected):
            self.assertIs(self.client.ask({}, QUESTIONS), expected)

    def test_no_credentials_needed_for_construction(self):
        client = JevClient()
        self.assertEqual(client.model, "jev-latest")
        self.assert_code("configuration", lambda: client.ask({}, QUESTIONS))
        self.opener.open.assert_not_called()

    def test_api_key_environment_resolved_at_ask(self):
        client = JevClient()
        os.environ["TYPESAFE_API_KEY"] = "late-key"
        client.ask({}, QUESTIONS)
        self.assertEqual(self.opener.open.call_args.args[0].get_header("Authorization"), "Bearer late-key")

    def test_environment_and_explicit_overrides(self):
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "environment-key",
                                    "JEV_API_URL": "https://env.example/custom",
                                    "JEV_MODEL": "env-model"}):
            env_client = JevClient()
            env_client.ask({}, QUESTIONS)
            request = self.opener.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://env.example/custom")
            self.assertEqual(json.loads(request.data)["model"], "env-model")
            self.assertEqual(request.get_header("Authorization"), "Bearer environment-key")
            explicit = JevClient("explicit-key", "http://127.0.0.1:9000/full", "explicit-model", 2.5)
            self.respond(valid_response())
            explicit.ask({}, QUESTIONS)
            request = self.opener.open.call_args.args[0]
            self.assertEqual(request.full_url, "http://127.0.0.1:9000/full")
            self.assertEqual(json.loads(request.data)["model"], "explicit-model")
            self.assertEqual(request.get_header("Authorization"), "Bearer explicit-key")
            self.assertEqual(self.opener.open.call_args.kwargs["timeout"], 2.5)

    def test_backend_key_stable_credential_independent(self):
        first = JevClient("one")
        second = JevClient("two")
        self.assertEqual(first.backend_key, second.backend_key)
        expected = hashlib.sha256(json.dumps([first.endpoint, first.model], separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(first.backend_key, expected)
        self.assertRegex(first.backend_key, r"^[0-9a-f]{64}$")
        self.assertNotEqual(first.backend_key, JevClient(model="other").backend_key)
        self.assertNotEqual(first.backend_key, JevClient(endpoint="https://example.test/full").backend_key)

    def test_invalid_timeout_configuration(self):
        for value in (True, False, 0, -1, 300.01, float("inf"), float("nan"), "60", None, 10**1000):
            with self.subTest(timeout=repr(value)):
                self.assert_code("configuration", lambda: JevClient(timeout=value))
        self.assertEqual(JevClient(timeout=300).timeout, 300)
        self.assertEqual(JevClient(timeout=0.1).timeout, 0.1)

    def test_invalid_endpoint_configuration(self):
        for endpoint in ("", "http://example.com/full", "ftp://127.0.0.1/full", "https:///full",
                         "https://user:private-server-detail@example.test/full", "https://example.test/#fragment",
                         "https://example.test:bad/full", "https://example.test:99999/full",
                         "https://example.test:0/full", " https://example.test/full",
                         "https://exam\nple.test/full", "http://127.0.0.1.evil.test/full", None, 4):
            if endpoint is None:
                continue  # None selects the default by design.
            with self.subTest(endpoint=endpoint):
                self.assert_code("configuration", lambda: JevClient(endpoint=endpoint))

    def test_loopback_http_allowed(self):
        for endpoint in ("http://localhost:8000/api", "http://127.0.0.1/api", "http://127.2.3.4/api",
                         "http://[::1]:8000/api"):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(JevClient(endpoint=endpoint).endpoint, endpoint)

    def test_invalid_model_and_environment(self):
        for model in ("", "  ", 4, False):
            with self.subTest(model=model):
                self.assert_code("configuration", lambda: JevClient(model=model))
        with patch.dict(os.environ, {"JEV_MODEL": ""}):
            self.assert_code("configuration", JevClient)
        with patch.dict(os.environ, {"JEV_API_URL": ""}):
            self.assert_code("configuration", JevClient)

    def test_invalid_api_key_configuration(self):
        for key in ("", "  ", "abc\r\nAuthorization: bad", "with space", "tiếng", 12, True):
            with self.subTest(key=key):
                self.assert_code("configuration", lambda: JevClient(api_key=key).ask({}, QUESTIONS))
        self.opener.open.assert_not_called()

    def test_invalid_questions(self):
        cases = [None, [], {}, {"q": {}}, {"q": {"type": "score", "criteria": {"a": "a"}}},
                 {"q": {"type": "choice", "criteria": []}}, {"q": {"type": "choice", "criteria": {}}},
                 {"q": {"type": "choice", "criteria": {1: "a"}}}, {"": QUESTIONS["risk"]},
                 {1: QUESTIONS["risk"]}]
        for questions in cases:
            with self.subTest(questions=questions):
                self.assert_code("configuration", lambda: self.client.ask({}, questions))
        self.opener.open.assert_not_called()

    def test_invalid_request_serialization(self):
        circular = []
        circular.append(circular)
        for state in (object(), {"bad": float("nan")}, circular, "\ud800"):
            with self.subTest(state=type(state).__name__):
                self.assert_code("configuration", lambda: self.client.ask(state, QUESTIONS))
        self.opener.open.assert_not_called()

    def test_response_models_validated_but_alias_resolution_allowed(self):
        for model in ("", " ", None, 5, False):
            payload = valid_response()
            payload["model"] = model
            self.respond(payload)
            with self.subTest(model=model):
                self.assert_code("invalid_response")
        self.respond(valid_response())
        self.assertEqual(self.client.ask({}, QUESTIONS)["model"], "jev-resolved-2026")

    def test_exact_answer_ids_required(self):
        cases = [{}, {"risk": valid_response()["answers"]["risk"]},
                 {**valid_response()["answers"], "extra": valid_response()["answers"]["topic"]}, [], None]
        for answers in cases:
            payload = valid_response()
            payload["answers"] = answers
            self.respond(payload)
            with self.subTest(answers=answers):
                self.assert_code("invalid_response")

    def test_invalid_answer_shapes_and_choices(self):
        for answer in (None, [], {}, {**valid_response()["answers"]["risk"], "type": "noul"},
                       {**valid_response()["answers"]["risk"], "choice": "missing"},
                       {**valid_response()["answers"]["risk"], "choice": ["risk"]}):
            payload = valid_response()
            payload["answers"]["risk"] = answer
            self.respond(payload)
            with self.subTest(answer=answer):
                self.assert_code("invalid_response")

    def test_confidence_rejects_bool_nonfinite_and_out_of_range(self):
        for confidence in (True, False, -0.1, 1.01, None, "0.7", [], float("nan"), float("inf"), 10**1000):
            payload = valid_response()
            payload["answers"]["risk"]["confidence"] = confidence
            self.respond(payload)
            with self.subTest(confidence=repr(confidence)):
                self.assert_code("invalid_response")

    def test_confidence_is_not_an_accuracy_threshold(self):
        payload = valid_response()
        payload["answers"]["risk"]["confidence"] = 0
        self.respond(payload)
        self.assertEqual(self.client.ask({}, QUESTIONS)["answers"]["risk"]["confidence"], 0)

    def test_probabilities_exact_criteria_and_valid_numbers(self):
        cases = [None, [], {}, {"risk": 1}, {"risk": 0.7, "safe": 0.2, "extra": 0.1},
                 {"risk": True, "safe": 0}, {"risk": 0.7, "safe": "0.3"},
                 {"risk": -0.2, "safe": 1.2}, {"risk": float("inf"), "safe": 0},
                 {"risk": float("nan"), "safe": 0}, {"risk": 0.1, "safe": 0.1},
                 {"risk": 10**1000, "safe": 0}]
        for probabilities in cases:
            payload = valid_response()
            payload["answers"]["risk"]["probabilities"] = probabilities
            self.respond(payload)
            with self.subTest(probabilities=repr(probabilities)):
                self.assert_code("invalid_response")

    def test_probability_sum_tolerance(self):
        for probability, accepted in ((0.6995, True), (0.7005, True), (0.698, False), (0.702, False)):
            payload = valid_response()
            payload["answers"]["risk"]["probabilities"]["risk"] = probability
            self.respond(payload)
            with self.subTest(probability=probability):
                if accepted:
                    self.assertEqual(self.client.ask({}, QUESTIONS), payload)
                else:
                    self.assert_code("invalid_response")

    def test_required_usage_nonnegative_integers(self):
        cases = [None, [], {}, {"input_tokens": 1}, {"output_tokens": 1}]
        for field in ("input_tokens", "output_tokens"):
            for value in (True, False, -1, 1.0, "1", None):
                cases.append({"input_tokens": 1, "output_tokens": 1, field: value})
        for usage in cases:
            payload = valid_response()
            payload["usage"] = usage
            self.respond(payload)
            with self.subTest(usage=usage):
                self.assert_code("invalid_response")
        payload = valid_response()
        payload["usage"] = {"input_tokens": 0, "output_tokens": 0}
        self.respond(payload)
        self.assertEqual(self.client.ask({}, QUESTIONS), payload)

    def test_missing_top_level_fields(self):
        for field in ("model", "answers", "usage"):
            payload = valid_response()
            del payload[field]
            self.respond(payload)
            with self.subTest(field=field):
                self.assert_code("invalid_response")

    def test_malformed_nonobject_and_duplicate_json(self):
        for raw in (b"", b"private-server-detail", b"{", b"[]", b"null", b"true", b"42", b"\xff",
                    b'{"model":"one","model":"two"}', b"[NaN]", b"[Infinity]",
                    b"[" * 2000 + b"]" * 2000):
            self.respond_bytes(raw)
            with self.subTest(raw=raw[:40]):
                self.assert_code("invalid_response")

    def test_bounded_response_read(self):
        response = self.respond_bytes(b"x" * (1024 * 1024 + 100))
        self.assert_code("invalid_response")
        response.read.assert_called_once_with(1024 * 1024 + 1)
        self.assertTrue(response.closed)

    def test_exact_size_limit_accepted(self):
        encoded = json.dumps(valid_response()).encode()
        response = self.respond_bytes(encoded + b" " * (1024 * 1024 - len(encoded)))
        self.assertEqual(self.client.ask({}, QUESTIONS), valid_response())
        response.read.assert_called_once_with(1024 * 1024 + 1)

    def test_http_error_codes_sanitized_and_not_retried(self):
        for status, expected in ((401, "auth"), (403, "auth"), (429, "rate_limit"),
                                 (500, "http_error"), (400, "http_error"), (302, "http_error")):
            self.opener.open.reset_mock()
            body = Response(b"private-server-detail test-key private-body")
            self.opener.open.side_effect = urllib.error.HTTPError(
                "https://private-server-detail/", status, "private-server-detail", {}, body)
            with self.subTest(status=status):
                error = self.assert_code(expected)
                self.assertTrue(error.__suppress_context__)
                self.opener.open.assert_called_once()
                body.read.assert_not_called()
                self.assertTrue(body.closed)

    def test_network_error_codes_sanitized_and_not_retried(self):
        for exception in (urllib.error.URLError("private-server-detail test-key"),
                          OSError("private-server-detail"), ConnectionError("private-server-detail"),
                          http.client.IncompleteRead(b"private-server-detail", 100)):
            self.opener.open.reset_mock()
            self.opener.open.side_effect = exception
            with self.subTest(exception=type(exception).__name__):
                error = self.assert_code("network")
                self.assertTrue(error.__suppress_context__)
                self.opener.open.assert_called_once()

    def test_timeout_errors_including_wrapped_timeout(self):
        for exception in (TimeoutError("private-server-detail"), socket.timeout("private-server-detail"),
                          urllib.error.URLError(socket.timeout("private-server-detail"))):
            self.opener.open.reset_mock()
            self.opener.open.side_effect = exception
            with self.subTest(exception=type(exception).__name__):
                self.assert_code("timeout")
                self.opener.open.assert_called_once()

    def test_timeout_during_response_read(self):
        response = self.opener.open.return_value
        response.read.side_effect = TimeoutError("private-server-detail")
        self.assert_code("timeout")
        self.assertTrue(response.closed)

    def test_non_success_status_never_parsed(self):
        response = self.opener.open.return_value
        response.status = 302
        self.assert_code("http_error")
        response.read.assert_not_called()

    def test_redirect_handler_refuses_every_redirect(self):
        self.client.ask({}, QUESTIONS)
        handler = self.build_mock.call_args.args[0]
        self.assertIsInstance(handler, urllib.request.HTTPRedirectHandler)
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                self.assertIsNone(handler.redirect_request(
                    urllib.request.Request("https://original.test", data=b"secret",
                                           headers={"Authorization": "Bearer test-key"}),
                    None, code, "private-server-detail", {}, "https://attacker.test"))

    def test_real_urllib_opener_never_follows_mocked_redirects(self):
        # Exercise urllib's actual redirect pipeline, with a fake transport so
        # no local server or external network is needed.
        self.build.stop()
        calls = []

        class FakeTransport(urllib.request.HTTPHandler):
            def http_open(self, request):
                calls.append(request)
                headers = Message()
                headers["Location"] = "http://attacker.example/steal"
                result = urllib.response.addinfourl(io.BytesIO(b"private-server-detail"), headers,
                                                   request.full_url, code=status)
                result.msg = "Redirect"
                return result

        original_build = urllib.request.build_opener
        for status in (301, 302, 303, 307, 308):
            calls.clear()
            with self.subTest(status=status), patch(
                "jev_client.urllib.request.build_opener",
                side_effect=lambda handler: original_build(handler, FakeTransport()),
            ):
                client = JevClient("test-key", "http://127.0.0.1:12345/api")
                self.assert_code("http_error", lambda: client.ask({}, QUESTIONS))
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0].full_url, "http://127.0.0.1:12345/api")


if __name__ == "__main__":
    unittest.main()
