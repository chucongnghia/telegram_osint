"""Small stdlib-only client for the Typesafe Jev choice-question API.

Credentials are checked only when asking, so construction is safe for offline
planning. Errors deliberately exclude credentials, request data, and server data.
Confidence and probabilities are provider outputs, not calibrated accuracy.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import math
import os
import socket
import urllib.error
import urllib.parse
import urllib.request


_DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
_DEFAULT_MODEL = "jev-latest"
_MAX_RESPONSE_BYTES = 1024 * 1024
_PROBABILITY_TOLERANCE = 1e-3


class JevError(RuntimeError):
    """A sanitized client failure; ``code`` is suitable for programmatic use."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Returning None makes urllib raise HTTPError instead of forwarding the
        # Authorization header (including redirects to the same origin).
        return None


def _valid_endpoint(endpoint):
    if not isinstance(endpoint, str) or not endpoint or any(
        char.isspace() or ord(char) < 32 or ord(char) == 127 for char in endpoint
    ):
        return False
    try:
        parsed = urllib.parse.urlsplit(endpoint)
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            return False
        if parsed.fragment or parsed.port == 0:
            return False
        if parsed.scheme == "https":
            return True
        if parsed.scheme != "http":
            return False
        if parsed.hostname.lower() == "localhost":
            return True
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def _unit_number(value):
    # bool is an int subclass, but is never a probability.
    return type(value) in (int, float) and 0 <= value <= 1 and math.isfinite(value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Nonfinite JSON constant")


class JevClient:
    """POST ``{model, state, questions}`` and validate complete choice answers.

    ``endpoint`` is a full URL, not a base URL. ``model`` remains the requested
    model even if the server resolves an alias to another nonblank model name.
    ``backend_key`` identifies endpoint and requested model, never credentials.
    No retries, redirects, or local classification fallbacks are performed.
    """

    def __init__(self, api_key=None, endpoint=None, model=None, timeout=60):
        self.endpoint = os.environ.get("JEV_API_URL", _DEFAULT_ENDPOINT) if endpoint is None else endpoint
        self.model = os.environ.get("JEV_MODEL", _DEFAULT_MODEL) if model is None else model
        if not _valid_endpoint(self.endpoint):
            raise JevError("configuration", "Invalid Jev endpoint configuration.")
        if not isinstance(self.model, str) or not self.model.strip():
            raise JevError("configuration", "Invalid Jev model configuration.")
        if type(timeout) not in (int, float) or not 0 < timeout <= 300 or not math.isfinite(timeout):
            raise JevError("configuration", "Invalid Jev timeout configuration.")
        self.timeout = timeout
        self._api_key = api_key
        identity = json.dumps([self.endpoint, self.model], ensure_ascii=True, separators=(",", ":"))
        self.backend_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @staticmethod
    def _criteria(questions):
        if not isinstance(questions, dict) or not questions:
            raise JevError("configuration", "Invalid Jev choice questions.")
        criteria = {}
        for qid, question in questions.items():
            if not isinstance(qid, str) or not qid.strip() or not isinstance(question, dict):
                raise JevError("configuration", "Invalid Jev choice questions.")
            labels = question.get("criteria")
            if question.get("type") != "choice" or not isinstance(labels, dict) or not labels:
                raise JevError("configuration", "Invalid Jev choice questions.")
            if any(not isinstance(label, str) or not label.strip() for label in labels):
                raise JevError("configuration", "Invalid Jev choice questions.")
            criteria[qid] = set(labels)
        return criteria

    @staticmethod
    def _validate_response(response, criteria):
        valid = isinstance(response, dict)
        if valid:
            model = response.get("model")
            answers = response.get("answers")
            usage = response.get("usage")
            valid = (
                isinstance(model, str) and bool(model.strip())
                and isinstance(answers, dict) and set(answers) == set(criteria)
                and isinstance(usage, dict)
                and all(type(usage.get(key)) is int and usage[key] >= 0
                        for key in ("input_tokens", "output_tokens"))
            )
        if not valid:
            raise JevError("invalid_response", "Invalid Jev response.")
        for qid, labels in criteria.items():
            answer = answers[qid]
            if not isinstance(answer, dict):
                raise JevError("invalid_response", "Invalid Jev response.")
            probabilities = answer.get("probabilities")
            if not (
                answer.get("type") == "choice"
                and isinstance(answer.get("choice"), str) and answer["choice"] in labels
                and _unit_number(answer.get("confidence"))
                and isinstance(probabilities, dict) and set(probabilities) == labels
                and all(_unit_number(value) for value in probabilities.values())
                and abs(math.fsum(probabilities.values()) - 1) <= _PROBABILITY_TOLERANCE
            ):
                raise JevError("invalid_response", "Invalid Jev response.")

    def ask(self, state, questions) -> dict:
        """Return the unmodified decoded response, or raise sanitized JevError."""
        api_key = os.environ.get("TYPESAFE_API_KEY") if self._api_key is None else self._api_key
        if not isinstance(api_key, str) or not api_key.strip():
            raise JevError("configuration", "Jev API credentials are required.")
        # Restrict to visible ASCII, preventing header injection and urllib's
        # otherwise unsanitized header-encoding errors.
        if any(not 33 <= ord(char) <= 126 for char in api_key):
            raise JevError("configuration", "Invalid Jev API credentials.")
        criteria = self._criteria(questions)
        try:
            body = json.dumps(
                {"model": self.model, "state": state, "questions": questions},
                ensure_ascii=False, allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
            raise JevError("configuration", "Invalid Jev request data.") from None
        try:
            request = urllib.request.Request(
                self.endpoint, data=body, method="POST",
                headers={"Authorization": "Bearer " + api_key,
                         "Content-Type": "application/json", "Accept": "application/json"},
            )
            opener = urllib.request.build_opener(_NoRedirect())
            with opener.open(request, timeout=self.timeout) as response:
                if not 200 <= response.status < 300:
                    raise JevError("http_error", "Jev HTTP request failed.")
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            if status in (401, 403):
                raise JevError("auth", "Jev authentication failed.") from None
            if status == 429:
                raise JevError("rate_limit", "Jev rate limit reached.") from None
            raise JevError("http_error", "Jev HTTP request failed.") from None
        except (TimeoutError, socket.timeout):
            raise JevError("timeout", "Jev request timed out.") from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise JevError("timeout", "Jev request timed out.") from None
            raise JevError("network", "Jev network request failed.") from None
        except (OSError, http.client.HTTPException):
            raise JevError("network", "Jev network request failed.") from None
        except (ValueError, UnicodeError):
            raise JevError("configuration", "Invalid Jev request configuration.") from None
        if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE_BYTES:
            raise JevError("invalid_response", "Invalid Jev response.")
        try:
            result = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                                parse_constant=_reject_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise JevError("invalid_response", "Invalid Jev response.") from None
        self._validate_response(result, criteria)
        return result
