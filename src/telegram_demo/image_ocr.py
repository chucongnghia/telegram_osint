"""Offline, bounded image inspection and Tesseract OCR using only the stdlib.

Executables are discovered lazily; importing this module never launches tools or
fetches models. Confidence is Tesseract's uncalibrated 0..100 OCR engine score,
not an accuracy probability, content-risk score, or scam assessment.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import stat
import struct
import subprocess
import tempfile
import time


_MAX_OUTPUT = 1024 * 1024
_MAX_TEXT = 32000
_MAX_WORDS = 4000
_MAX_FRAME_TEXT = 48000
_LIMITATIONS = [
    "OCR may omit or misread text; no content or scam judgment is inferred.",
    "Confidence is an uncalibrated Tesseract OCR engine score from 0 to 100, not an accuracy probability.",
    "Word boxes use the normalized image coordinates, not the original image coordinates.",
]


class OCRError(RuntimeError):
    """A safe failure code/message, optionally carrying completed frame results."""

    def __init__(self, code, message):
        self.code = re.sub(r"[^a-z0-9_]", "_", str(code).lower())[:64] or "ocr_error"
        self.partial_result = None
        super().__init__(message)


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _positive(value, name, integer=False):
    try:
        valid = (not isinstance(value, bool)
                 and isinstance(value, (int,) if integer else (int, float))
                 and math.isfinite(value) and value > 0)
    except (ValueError, OverflowError):
        valid = False
    if not valid:
        raise OCRError("configuration", "Invalid " + name + ".")
    return value


def _local_path(value):
    try:
        raw = os.fspath(value)
        if (not isinstance(raw, str) or not raw or "\x00" in raw or "://" in raw
                or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", raw)):
            raise ValueError
        path = Path(raw).absolute()
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise OCRError("invalid_input", "Symbolic-link image inputs are not supported.")
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError
        return path, info
    except FileNotFoundError:
        raise OCRError("input_missing", "Image input does not exist.") from None
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        if isinstance(exc, OCRError):
            raise
        raise OCRError("invalid_input", "A local regular image file is required.") from None


def _header(data):
    """Parse bounded container headers; never ask a decoder to discover size."""
    try:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            offset, size, animated = 8, None, False
            ended = False
            while offset + 12 <= len(data):
                length = struct.unpack_from(">I", data, offset)[0]
                kind = data[offset + 4:offset + 8]
                end = offset + 12 + length
                if end > len(data):
                    raise ValueError
                if offset == 8:
                    if kind != b"IHDR" or length != 13:
                        raise ValueError
                    size = struct.unpack_from(">II", data, offset + 8)
                if kind == b"acTL":
                    animated = True
                if kind == b"IEND":
                    if length or end != len(data):
                        raise ValueError
                    ended = True
                    break
                offset = end
            if animated:
                raise OCRError("animated_image", "Animated images are not supported.")
            if not ended or size is None:
                raise ValueError
            return "PNG", *size
        if data.startswith(b"\xff\xd8"):
            if not data.endswith(b"\xff\xd9"):
                raise ValueError
            offset = 2
            while offset < len(data):
                if data[offset] != 255:
                    raise ValueError
                while offset < len(data) and data[offset] == 255:
                    offset += 1
                marker = data[offset]
                offset += 1
                if marker in (0xD9, 0xDA):
                    break
                if marker == 0x01 or 0xD0 <= marker <= 0xD7:
                    continue
                length = struct.unpack_from(">H", data, offset)[0]
                if length < 2 or offset + length > len(data):
                    raise ValueError
                if marker in (0xC0, 0xC1, 0xC2):
                    if length < 8:
                        raise ValueError
                    height, width = struct.unpack_from(">HH", data, offset + 3)
                    return "JPEG", width, height
                offset += length
            raise ValueError
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            if len(data) < 20 or struct.unpack_from("<I", data, 4)[0] + 8 != len(data):
                raise ValueError
            offset, size, animated = 12, None, False
            while offset + 8 <= len(data):
                kind = data[offset:offset + 4]
                length = struct.unpack_from("<I", data, offset + 4)[0]
                start = offset + 8
                end = start + length
                if end + (length & 1) > len(data):
                    raise ValueError
                chunk = data[start:end]
                if kind in (b"ANIM", b"ANMF"):
                    animated = True
                if kind == b"VP8X":
                    if length != 10:
                        raise ValueError
                    animated |= bool(chunk[0] & 2)
                    size = (1 + int.from_bytes(chunk[4:7], "little"),
                            1 + int.from_bytes(chunk[7:10], "little"))
                elif kind == b"VP8 " and size is None:
                    if length < 10 or chunk[3:6] != b"\x9d\x01\x2a":
                        raise ValueError
                    size = tuple(n & 0x3fff for n in struct.unpack_from("<HH", chunk, 6))
                elif kind == b"VP8L" and size is None:
                    if length < 5 or chunk[0] != 0x2f:
                        raise ValueError
                    bits = int.from_bytes(chunk[1:5], "little")
                    size = ((bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1)
                offset = end + (length & 1)
            if animated:
                raise OCRError("animated_image", "Animated images are not supported.")
            if offset != len(data) or size is None:
                raise ValueError
            return "WEBP", *size
    except (IndexError, struct.error, ValueError):
        raise OCRError("invalid_image", "Image container headers are invalid.") from None
    raise OCRError("unsupported_format", "Only local PNG, JPEG, and WEBP images are supported.")


class LocalOCR:
    """Local OCR; timeout applies to each subprocess, without automatic downloads."""

    def __init__(self, tesseract=None, ffmpeg=None, ffprobe=None, languages="vie+eng",
                 timeout=30, max_bytes=20 * 1024 * 1024, max_pixels=20_000_000,
                 max_side=2000, psm=11):
        self.timeout = _positive(timeout, "timeout")
        self.max_bytes = _positive(max_bytes, "byte limit", True)
        self.max_pixels = _positive(max_pixels, "pixel limit", True)
        self.max_side = _positive(max_side, "maximum side", True)
        if not isinstance(psm, int) or isinstance(psm, bool) or not 0 <= psm <= 13:
            raise OCRError("configuration", "Invalid Tesseract page segmentation mode.")
        if (not isinstance(languages, str) or not languages
                or any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", item)
                       for item in languages.split("+"))):
            raise OCRError("configuration", "Invalid OCR language selection.")
        self.languages = list(dict.fromkeys(languages.split("+")))
        self.psm = psm
        self._executables = {"tesseract": tesseract, "ffmpeg": ffmpeg, "ffprobe": ffprobe}

    def _binary(self, name):
        supplied = self._executables[name]
        try:
            selected = os.fspath(supplied) if supplied is not None else name
            if not isinstance(selected, str) or not selected or "\x00" in selected:
                raise ValueError
            found = shutil.which(selected)
            if found is None and supplied is None:
                found = shutil.which(str(Path.home() / ".local" / "bin" / name))
        except (ValueError, TypeError, OSError):
            found = None
        if found is None:
            raise OCRError("missing_binary", "Required " + name + " executable is unavailable.")
        return found

    def _run(self, args):
        """Drain both pipes into capped private spools; never retain raw errors."""
        try:
            with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
                with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, shell=False) as process:
                    try:
                        with selectors.DefaultSelector() as selector:
                            selector.register(process.stdout, selectors.EVENT_READ, (out, 0))
                            selector.register(process.stderr, selectors.EVENT_READ, (err, 1))
                            counts = [0, 0]
                            deadline = time.monotonic() + self.timeout
                            while selector.get_map():
                                remaining = deadline - time.monotonic()
                                if remaining <= 0:
                                    raise OCRError("timeout", "Local image processing timed out.")
                                for key, _ in selector.select(min(0.05, remaining)):
                                    chunk = os.read(key.fd, 65536)
                                    if not chunk:
                                        selector.unregister(key.fileobj)
                                        continue
                                    spool, index = key.data
                                    counts[index] += len(chunk)
                                    if counts[index] > _MAX_OUTPUT:
                                        raise OCRError("output_limit", "Local tool output exceeded its limit.")
                                    spool.write(chunk)
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise OCRError("timeout", "Local image processing timed out.")
                            try:
                                status = process.wait(timeout=remaining)
                            except subprocess.TimeoutExpired:
                                raise OCRError("timeout", "Local image processing timed out.") from None
                        if status:
                            raise OCRError("process_failed", "Local tool could not process this image.")
                        out.seek(0)
                        return out.read(_MAX_OUTPUT + 1)
                    except BaseException:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        process.wait()
                        raise
        except FileNotFoundError:
            raise OCRError("missing_binary", "A required local executable is unavailable.") from None
        except OSError:
            raise OCRError("process_failed", "Local image processing could not start or complete.") from None

    def preflight(self):
        binary = self._binary("tesseract")
        version = self._run([binary, "--version"]).decode("utf-8", "replace")
        match = re.match(r"tesseract\s+([A-Za-z0-9.+_-]{1,80})(?:\s|$)", version)
        if not match:
            raise OCRError("invalid_output", "Tesseract version response is invalid.")
        listing = self._run([binary, "--list-langs"]).decode("utf-8", "replace")
        available = sorted(set(line.strip() for line in listing.splitlines()
                               if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", line.strip())))
        if any(language not in available for language in self.languages):
            raise OCRError("language_unavailable", "Requested OCR language data is unavailable locally.")
        # Check the full local processing chain, without executing an image.
        self._binary("ffmpeg")
        self._binary("ffprobe")
        return {"provider": "tesseract", "version": match.group(1),
                "languages_available": available, "languages_requested": list(self.languages)}

    def _snapshot(self, value, directory):
        path, info = _local_path(value)
        if info.st_size <= 0:
            raise OCRError("invalid_image", "Image file is empty.")
        if info.st_size > self.max_bytes:
            raise OCRError("byte_limit", "Image exceeds the byte limit.")
        snapshot = directory / "source.image"
        digest = self._copy_hash(path, _signature(info), snapshot)
        data = snapshot.read_bytes()
        format_name, width, height = _header(data)
        if width <= 0 or height <= 0:
            raise OCRError("invalid_image", "Image dimensions are invalid.")
        if width * height > self.max_pixels:
            raise OCRError("pixel_limit", "Image exceeds the pixel limit.")
        demuxer = {"PNG": "png_pipe", "JPEG": "jpeg_pipe", "WEBP": "webp_pipe"}[format_name]
        probe = self._run([self._binary("ffprobe"), "-v", "error", "-protocol_whitelist", "file",
                           "-f", demuxer, "-show_entries", "stream=width,height,codec_name",
                           "-of", "json", str(snapshot)])
        try:
            streams = json.loads(probe)["streams"]
            if len(streams) != 1:
                raise ValueError
            stream = streams[0]
            if (type(stream["width"]) is not int or type(stream["height"]) is not int
                    or stream["width"] != width or stream["height"] != height
                    or stream.get("codec_name") != {"PNG": "png", "JPEG": "mjpeg", "WEBP": "webp"}[format_name]):
                raise ValueError
        except (ValueError, KeyError, TypeError, IndexError, RecursionError):
            raise OCRError("invalid_image", "Image header inspection failed.") from None
        metadata = {"path": str(path), "sha256": digest, "size": info.st_size,
                    "width": width, "height": height, "format": format_name}
        self._verify(path, info, digest)
        return path, info, snapshot, demuxer, metadata

    def _copy_hash(self, path, signature, destination=None):
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or _signature(info) != signature:
                    raise OCRError("input_changed", "Image input changed during processing.")
                digest = hashlib.sha256()
                output = destination.open("xb") if destination else None
                try:
                    remaining = info.st_size
                    while remaining:
                        chunk = source.read(min(65536, remaining))
                        if not chunk:
                            raise OCRError("input_changed", "Image input changed during processing.")
                        remaining -= len(chunk)
                        digest.update(chunk)
                        if output:
                            output.write(chunk)
                    if source.read(1) or _signature(os.fstat(source.fileno())) != signature:
                        raise OCRError("input_changed", "Image input changed during processing.")
                finally:
                    if output:
                        output.close()
                if path.is_symlink() or _signature(path.stat()) != signature:
                    raise OCRError("input_changed", "Image input changed during processing.")
            if destination:
                destination.chmod(0o400)
            return digest.hexdigest()
        except OSError:
            raise OCRError("input_changed", "Image input became unavailable during processing.") from None

    def _verify(self, path, info, digest):
        if self._copy_hash(path, _signature(info)) != digest:
            raise OCRError("input_changed", "Image input changed during processing.")

    def inspect(self, path):
        with tempfile.TemporaryDirectory(prefix="local-ocr-") as temp:
            return self._snapshot(path, Path(temp))[4]

    def _normalize(self, snapshot, demuxer, output, max_side, max_output):
        self._run([self._binary("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error",
                   "-protocol_whitelist", "file", "-f", demuxer, "-i", str(snapshot),
                   "-map", "0:v:0", "-frames:v", "1", "-vf",
                   f"scale=w='min({max_side},iw)':h='min({max_side},ih)':force_original_aspect_ratio=decrease",
                   "-map_metadata", "-1", "-threads", "1", "-fs", str(max_output),
                   "-update", "1", "-n", str(output)])
        try:
            info = output.lstat()
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= max_output:
                raise ValueError
            format_name, width, height = _header(output.read_bytes())
            if width <= 0 or height <= 0 or max(width, height) > max_side or width * height > self.max_pixels:
                raise ValueError
            if format_name != ("JPEG" if output.suffix == ".jpg" else "PNG"):
                raise ValueError
            output.chmod(0o400)
            return {"width": width, "height": height, "format": format_name,
                    "size": info.st_size, "max_side": max_side, "orientation": "ffmpeg autorotate",
                    "single_frame": True, "boxes_coordinate_space": "normalized_image"}
        except (OSError, ValueError):
            raise OCRError("invalid_output", "Normalized image is missing, invalid, or exceeds its limit.") from None

    def _parse_tsv(self, raw, width, height):
        if len(raw) > _MAX_OUTPUT:
            raise OCRError("output_limit", "Tesseract output exceeded its byte limit.")
        try:
            text = raw.decode("utf-8", "strict")
            reader = csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE)
            required = {"level", "page_num", "block_num", "par_num", "line_num", "left", "top",
                        "width", "height", "conf", "text"}
            if not reader.fieldnames or not required.issubset(reader.fieldnames):
                raise ValueError
            words, lines, previous = [], [], None
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError
                if int(row["level"]) != 5 or not row["text"].strip():
                    continue
                confidence = float(row["conf"])
                if not math.isfinite(confidence) or not 0 <= confidence <= 100:
                    raise ValueError
                left, top, box_width, box_height = (int(row[key]) for key in ("left", "top", "width", "height"))
                if (min(left, top, box_width, box_height) < 0 or left + box_width > width
                        or top + box_height > height):
                    raise ValueError
                word = row["text"].strip()
                if any(ord(char) < 32 for char in word):
                    raise ValueError
                key = tuple(int(row[name]) for name in ("page_num", "block_num", "par_num", "line_num"))
                if key != previous:
                    lines.append([])
                lines[-1].append(word)
                previous = key
                words.append({"text": word, "confidence": confidence,
                              "bbox": {"left": left, "top": top, "width": box_width, "height": box_height}})
                if len(words) > _MAX_WORDS:
                    raise OCRError("word_limit", "OCR word count exceeds its limit.")
            result_text = "\n".join(" ".join(line) for line in lines)
            if len(result_text) > _MAX_TEXT:
                raise OCRError("text_limit", "OCR text exceeds its character limit.")
            return result_text, words
        except (ValueError, TypeError, UnicodeError, csv.Error, OverflowError):
            raise OCRError("invalid_output", "Tesseract returned invalid TSV output.") from None

    def recognize(self, path):
        with tempfile.TemporaryDirectory(prefix="local-ocr-") as temp:
            directory = Path(temp)
            original, info, snapshot, demuxer, metadata = self._snapshot(path, directory)
            preflight = self.preflight()
            output = directory / "normalized.png"
            preprocessing = self._normalize(snapshot, demuxer, output, self.max_side, self.max_bytes)
            raw = self._run([self._binary("tesseract"), str(output), "stdout", "-l",
                             "+".join(self.languages), "--psm", str(self.psm), "tsv"])
            text, words = self._parse_tsv(raw, preprocessing["width"], preprocessing["height"])
            self._verify(original, info, metadata["sha256"])
            return {"provider": "tesseract", "version": preflight["version"],
                    "languages": list(self.languages), "text": text, "words": words,
                    "image": metadata, "preprocessing": preprocessing, "limitations": list(_LIMITATIONS)}

    def prepare_for_vision(self, path, artifact_dir):
        """Create an absent private directory containing one <=640px, <=2MiB JPEG."""
        try:
            directory = Path(artifact_dir).absolute()
            if any(part.is_symlink() for part in (directory, *directory.parents)):
                raise ValueError
            directory.mkdir(mode=0o700, exist_ok=False)
        except (OSError, TypeError, ValueError):
            raise OCRError("artifact_directory", "A fresh private artifact directory is required.") from None
        with tempfile.TemporaryDirectory(prefix="local-ocr-") as temp:
            original, info, snapshot, demuxer, metadata = self._snapshot(path, Path(temp))
            output = directory / "vision.jpg"
            self._normalize(snapshot, demuxer, output, min(self.max_side, 640), 2 * 1024 * 1024)
            self._verify(original, info, metadata["sha256"])
            digest = hashlib.sha256(output.read_bytes()).hexdigest()
            return {"path": str(output), "sha256": digest, "timestamp": 0.0}

    def recognize_frames(self, frames):
        if not isinstance(frames, (list, tuple)) or len(frames) > 60:
            raise OCRError("frame_limit", "OCR accepts a sequence of at most 60 frames.")
        result = {"provider": "tesseract", "version": None, "languages": list(self.languages),
                  "frames": [], "text": ""}
        try:
            # Validate the entire batch's structure before spending work on any
            # file or invoking OCR. Runtime file/engine failures still retain
            # already completed observations below.
            pending = []
            for frame in frames:
                if not isinstance(frame, dict) or "path" not in frame or "timestamp" not in frame:
                    raise OCRError("invalid_frame", "Each OCR frame requires a path and timestamp.")
                timestamp = frame["timestamp"]
                if (isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
                        or not math.isfinite(timestamp) or timestamp < 0):
                    raise OCRError("invalid_frame", "Frame timestamp is invalid.")
                expected = frame.get("sha256")
                if expected is not None and (not isinstance(expected, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", expected)):
                    raise OCRError("hash_mismatch", "Frame expected SHA-256 is invalid.")
                pending.append((frame["path"], timestamp, expected))
            for path, timestamp, expected in pending:
                before = self.inspect(path)
                if expected is not None and before["sha256"] != expected.lower():
                    raise OCRError("hash_mismatch", "Frame SHA-256 does not match the expected digest.")
                recognized = self.recognize(path)
                after = self.inspect(path)
                if (before != after or recognized["image"]["sha256"] != before["sha256"]):
                    raise OCRError("input_changed", "Frame changed during OCR processing.")
                combined = "\n\n".join([entry["text"] for entry in result["frames"]] + [recognized["text"]])
                if len(combined) > _MAX_FRAME_TEXT:
                    raise OCRError("text_limit", "Combined frame OCR text exceeds its character limit.")
                if result["version"] is not None and result["version"] != recognized["version"]:
                    raise OCRError("engine_changed", "OCR engine version changed during frame processing.")
                result["version"] = recognized["version"]
                result["frames"].append({"timestamp": timestamp, "sha256": before["sha256"],
                                         "text": recognized["text"], "words": recognized["words"]})
                result["text"] = combined
            if not frames:
                result["version"] = self.preflight()["version"]
            return result
        except OCRError as exc:
            exc.partial_result = result
            raise
