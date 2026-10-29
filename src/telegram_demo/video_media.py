"""Bounded, local-only FFmpeg media preparation (no downloads or models).

Only common self-contained video containers are accepted. Artifact directories
are exclusively created and failures intentionally leave their partial contents
for the caller to inspect/remove. Sources are never moved or deleted. A caller
that subsequently copies/imports a source must compare that copy's SHA-256 with
``media['sha256']`` as well; this module checks stability through extraction.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time


_MAX_OUTPUT = 1024 * 1024
_MAX_PIXELS = 3840 * 2160
# Excludes image/playlist/concat demuxers, even when disguised as .mp4.
_DEMUXERS = "mov,matroska,webm,avi,mpeg,mpegts,flv,ogg,asf"
_FORMAT_NAMES = set((_DEMUXERS + ",mp4,m4a,3gp,3g2,mj2").split(","))
_PLAYLIST_SUFFIXES = {".m3u", ".m3u8", ".mpd", ".ffconcat", ".concat", ".pls"}


class VideoMediaError(RuntimeError):
    """A failure with a stable code and no subprocess output or input data."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _positive_number(value, name):
    try:
        valid = (isinstance(value, (int, float)) and not isinstance(value, bool)
                 and math.isfinite(value) and value > 0)
    except (ValueError, OverflowError):
        valid = False
    if not valid:
        raise VideoMediaError("configuration", "Invalid " + name + ".")
    return float(value)


def _limits(max_bytes, max_duration):
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise VideoMediaError("configuration", "Invalid byte limit.")
    return max_bytes, _positive_number(max_duration, "duration limit")


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _local_path(value):
    try:
        raw = os.fspath(value)
        if (not isinstance(raw, str) or not raw or "\x00" in raw
                or "://" in raw or raw.lower().startswith(
                    ("file:", "http:", "https:", "ftp:", "pipe:", "concat:", "data:"))):
            raise VideoMediaError("invalid_input", "A local regular video file is required.")
        path = Path(raw).absolute()
        if path.is_symlink():
            raise VideoMediaError("invalid_input", "Symbolic-link video inputs are not supported.")
        path = path.resolve(strict=True)
        info = path.stat()
    except FileNotFoundError:
        raise VideoMediaError("input_missing", "Video input does not exist.") from None
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        if isinstance(exc, VideoMediaError):
            raise
        raise VideoMediaError("invalid_input", "A local regular video file is required.") from None
    if not stat.S_ISREG(info.st_mode) or path.suffix.lower() in _PLAYLIST_SUFFIXES:
        raise VideoMediaError("invalid_input", "A local regular video file is required.")
    return path, info


def _hash(path, expected=None):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or (expected and _signature(info) != expected):
                raise VideoMediaError("input_changed", "Video input changed during processing.")
            digest = hashlib.sha256()
            remaining = info.st_size
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise VideoMediaError("input_changed", "Video input changed during processing.")
                digest.update(chunk)
                remaining -= len(chunk)
            if source.read(1):
                raise VideoMediaError("input_changed", "Video input changed during processing.")
            after = os.fstat(source.fileno())
        if _signature(info) != _signature(after) or _signature(path.stat()) != _signature(info):
            raise VideoMediaError("input_changed", "Video input changed during processing.")
        return digest.hexdigest()
    except OSError:
        raise VideoMediaError("input_changed", "Video input became unavailable during processing.") from None


def _timestamps(duration, interval, max_frames):
    if interval * max_frames >= duration:
        return [i * interval for i in range(max_frames) if i * interval < duration]
    if max_frames == 1:
        return [duration / 2]
    # Spread a capped sample across the entire clip, including its last interval.
    last = min(math.nextafter(duration, 0), duration - min(interval, duration / 2))
    return [last * (i / (max_frames - 1)) for i in range(max_frames)]


class FFmpegMedia:
    """Lazy binary discovery; timeout is per command and limited to 600 seconds."""

    def __init__(self, ffmpeg=None, ffprobe=None, timeout=120):
        self.timeout = _positive_number(timeout, "timeout")
        if self.timeout > 600:
            raise VideoMediaError("configuration", "Timeout must not exceed 600 seconds.")
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe

    def _binary(self, name):
        supplied = self._ffmpeg if name == "ffmpeg" else self._ffprobe
        selected = supplied if supplied is not None else os.environ.get(name.upper() + "_BIN", name)
        try:
            if not selected or not isinstance(os.fspath(selected), str):
                raise ValueError
            found = shutil.which(selected)
        except (OSError, TypeError, ValueError):
            found = None
        if not found:
            raise VideoMediaError("missing_binary", "Required " + name + " executable is unavailable.")
        return found

    def _run(self, args):
        """Spool output off-heap; monitor caps and read at most one MiB.

        Check temporary-file sizes every 50ms and also on successful exit. No
        raw output (including stderr) is ever incorporated into an exception.
        """
        try:
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=stdout,
                                      stderr=stderr, shell=False) as process:
                    deadline = time.monotonic() + self.timeout
                    try:
                        while True:
                            if (os.fstat(stdout.fileno()).st_size > _MAX_OUTPUT
                                    or os.fstat(stderr.fileno()).st_size > _MAX_OUTPUT):
                                raise VideoMediaError("output_limit", "Media tool output exceeded its limit.")
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise VideoMediaError("timeout", "Media processing timed out.")
                            try:
                                status = process.wait(timeout=min(0.05, remaining))
                                break
                            except subprocess.TimeoutExpired:
                                continue
                        if (os.fstat(stdout.fileno()).st_size > _MAX_OUTPUT
                                or os.fstat(stderr.fileno()).st_size > _MAX_OUTPUT):
                            raise VideoMediaError("output_limit", "Media tool output exceeded its limit.")
                        if status:
                            raise VideoMediaError("process_failed", "Media tool could not process the video.")
                        stdout.seek(0)
                        return stdout.read(_MAX_OUTPUT + 1)
                    except BaseException:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass  # It may have exited between the cap check and kill.
                        process.wait()
                        raise
        except FileNotFoundError:
            raise VideoMediaError("missing_binary", "Required media executable is unavailable.") from None
        except (OSError, ValueError):
            raise VideoMediaError("process_failed", "Media tool could not be run.") from None

    def inspect(self, video_path, *, max_bytes=512 * 1024 * 1024, max_duration=600) -> dict:
        """Validate/hash a local video; return JSON-safe, allowlisted metadata."""
        max_bytes, max_duration = _limits(max_bytes, max_duration)
        path, info = _local_path(video_path)
        if info.st_size <= 0:
            raise VideoMediaError("invalid_input", "Video input is empty.")
        if info.st_size > max_bytes:
            raise VideoMediaError("size_limit", "Video input exceeds the byte limit.")
        signature = _signature(info)
        sha256 = _hash(path, signature)
        raw = self._run([
            self._binary("ffprobe"), "-v", "error", "-protocol_whitelist", "file",
            "-format_whitelist", _DEMUXERS, "-show_entries",
            "format=format_name,duration:stream=index,codec_type,width,height,duration:stream_disposition=attached_pic",
            "-of", "json", "-i", str(path),
        ])
        try:
            if _signature(path.stat()) != signature:
                raise VideoMediaError("input_changed", "Video input changed during processing.")
        except OSError:
            raise VideoMediaError("input_changed", "Video input became unavailable during processing.") from None
        try:
            data = json.loads(raw)
            streams = data["streams"]
            fmt = data["format"]
            if not isinstance(streams, list) or not isinstance(fmt, dict):
                raise ValueError
            if not isinstance(fmt.get("format_name"), str):
                raise ValueError
            names = fmt["format_name"].split(",")
            if not names or not all(name in _FORMAT_NAMES for name in names):
                raise ValueError
            clean_streams = []
            videos = []
            durations = []
            for stream in streams:
                if not isinstance(stream, dict):
                    raise ValueError
                kind = stream.get("codec_type")
                if kind not in ("audio", "video"):
                    continue
                index = stream["index"]
                if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                    raise ValueError
                clean = {"index": index, "codec_type": kind}
                if kind == "video":
                    disposition = stream.get("disposition", {})
                    if not isinstance(disposition, dict):
                        raise ValueError
                    if disposition.get("attached_pic", 0):
                        continue
                    width, height = stream["width"], stream["height"]
                    if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in (width, height)):
                        raise ValueError
                    if width * height > _MAX_PIXELS:
                        raise VideoMediaError("resolution_limit", "Video resolution exceeds the pixel limit.")
                    clean.update(width=width, height=height)
                    videos.append(clean)
                duration = stream.get("duration")
                if duration is not None and duration != "N/A":
                    if isinstance(duration, bool):
                        raise ValueError
                    duration = float(duration)
                    if not math.isfinite(duration) or duration <= 0:
                        raise ValueError
                    durations.append(duration)
                    clean["duration"] = duration
                clean_streams.append(clean)
            duration = fmt.get("duration")
            if duration is not None and duration != "N/A":
                if isinstance(duration, bool):
                    raise ValueError
                duration = float(duration)
                if not math.isfinite(duration) or duration <= 0:
                    raise ValueError
                durations.append(duration)
            if not videos or not durations:
                raise ValueError
            duration = max(durations)
        except (ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError):
            raise VideoMediaError("invalid_probe", "Media metadata is invalid or unsupported.") from None
        if duration > max_duration:
            raise VideoMediaError("duration_limit", "Video duration exceeds the limit.")
        return {
            "path": str(path), "sha256": sha256, "size_bytes": info.st_size,
            "duration": duration, "video_duration": videos[0].get("duration", duration),
            "has_audio": any(s["codec_type"] == "audio" for s in clean_streams),
            "width": videos[0]["width"], "height": videos[0]["height"],
            "ffprobe": {"format": {"format_name": ",".join(names), "duration": duration},
                        "streams": clean_streams},
        }

    @staticmethod
    def _artifact(path):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        except OSError:
            raise VideoMediaError("output_error", "Cannot create a private media artifact.") from None

    @staticmethod
    def _finish_artifact(path):
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
                raise OSError
            path.chmod(0o600)
        except OSError:
            raise VideoMediaError("output_error", "Media artifact is missing or invalid.") from None

    def extract(self, video_path, output_dir, *, interval=5.0, max_frames=12,
                max_duration=600, max_bytes=512 * 1024 * 1024) -> dict:
        """Create private JPEG/WAV artifacts; partial files remain on failure."""
        interval = _positive_number(interval, "sampling interval")
        if isinstance(max_frames, bool) or not isinstance(max_frames, int) or not 1 <= max_frames <= 60:
            raise VideoMediaError("configuration", "Frame limit must be between 1 and 60.")
        media = self.inspect(video_path, max_bytes=max_bytes, max_duration=max_duration)
        binary = self._binary("ffmpeg")
        path = Path(media["path"])
        try:
            signature = _signature(path.stat())
            requested = Path(output_dir).absolute()
            if os.path.lexists(requested):
                raise FileExistsError
            directory = requested.resolve()
            directory.mkdir(mode=0o700)
            directory.chmod(0o700)
        except FileExistsError:
            raise VideoMediaError("output_exists", "Artifact directory must not already exist.") from None
        except (OSError, TypeError, ValueError, RuntimeError):
            raise VideoMediaError("output_error", "Cannot create the artifact directory.") from None
        video = next(s for s in media["ffprobe"]["streams"] if s["codec_type"] == "video")
        base = [binary, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-protocol_whitelist", "file", "-format_whitelist", _DEMUXERS]
        frames = []
        for number, timestamp in enumerate(_timestamps(media["video_duration"], interval, max_frames), 1):
            frame = directory / ("frame_%04d.jpg" % number)
            self._artifact(frame)
            self._run(base + ["-ss", format(timestamp, ".17g"), "-i", str(path),
                              "-map", "0:" + str(video["index"]), "-an", "-sn", "-dn",
                              "-frames:v", "1", "-vf",
                              "scale=w='min(640,iw)':h='min(640,ih)':force_original_aspect_ratio=decrease",
                              "-threads", "1", "-q:v", "3", "-f", "image2", str(frame)])
            self._finish_artifact(frame)
            frames.append({"timestamp": timestamp, "path": str(frame), "sha256": _hash(frame)})
        audio_path = None
        if media["has_audio"]:
            audio = directory / "audio.wav"
            self._artifact(audio)
            self._run(base + ["-i", str(path), "-map", "0:a:0", "-vn", "-sn", "-dn",
                              "-t", format(media["duration"], ".17g"), "-ac", "1", "-ar", "16000",
                              "-c:a", "pcm_s16le", "-threads", "1", "-f", "wav", str(audio)])
            self._finish_artifact(audio)
            audio_path = str(audio)
        if _hash(path, signature) != media["sha256"]:
            raise VideoMediaError("input_changed", "Video input changed during processing.")
        return {"media": media, "artifact_dir": str(directory), "audio_path": audio_path,
                "frames": frames, "sampling": {"interval": interval, "max_frames": max_frames,
                                               "coverage": "sampled_frames_not_full_video",
                                               "video_duration": media["video_duration"],
                                               "media_duration": media["duration"],
                                               "unsampled_audio_tail_seconds": max(0.0, media["duration"] - media["video_duration"])}}
