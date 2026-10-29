"""Offline, subprocess-mocked tests; no FFmpeg install, network, or models."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from telegram_demo import video_media
from telegram_demo.video_media import FFmpegMedia, VideoMediaError


def probe(duration=11.5, audio=True):
    streams = [{"index": 0, "codec_type": "video", "width": 1280, "height": 720,
                "duration": str(duration), "disposition": {"attached_pic": 0},
                "tags": {"secret": "UNTRUSTED PRIVATE METADATA"}}]
    if audio:
        streams.append({"index": 1, "codec_type": "audio", "duration": str(duration)})
    return {"streams": streams,
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": str(duration),
                       "filename": "PRIVATE SOURCE", "tags": {"title": "PRIVATE"}}}


class FakeProcess:
    def __init__(self, factory, args, kwargs):
        self.factory, self.args, self.kwargs = factory, args, kwargs
        self.returncode = None
        self.killed = False
        self.written = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        if self.killed:
            return self.returncode
        if self.factory.timeout:
            raise subprocess.TimeoutExpired(self.args, timeout)
        if not self.written:
            self.written = True
            if self.factory.on_command:
                self.factory.on_command(self.args)
            is_probe = "-show_entries" in self.args
            data = self.factory.payload
            if data is None:
                data = json.dumps(self.factory.metadata).encode()
            self.kwargs["stdout"].write(data if is_probe else b"")
            self.kwargs["stderr"].write(self.factory.stderr)
            if not is_probe and self.factory.artifacts:
                artifact = Path(self.args[-1])
                # FFmpeg truncates the pre-created private output file.
                with artifact.open("wb") as handle:
                    handle.write(b"RIFF mocked PCM" if artifact.suffix == ".wav" else b"\xff\xd8mocked JPEG")
        self.returncode = self.factory.status
        return self.returncode


class FakeFFmpeg:
    def __init__(self):
        self.metadata = probe()
        self.payload = None
        self.stderr = b""
        self.status = 0
        self.timeout = False
        self.artifacts = True
        self.on_command = None
        self.calls = []
        self.processes = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        process = FakeProcess(self, args, kwargs)
        self.processes.append(process)
        return process


class VideoMediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "clip.mp4"
        self.source.write_bytes(b"local video fixture")
        self.fake = FakeFFmpeg()
        self.addCleanup(patch.stopall)
        self.which = patch.object(video_media.shutil, "which", side_effect=lambda name: "/bin/" + str(name).rsplit("/", 1)[-1]).start()
        patch.object(video_media.subprocess, "Popen", side_effect=self.fake).start()
        self.media = FFmpegMedia()

    def assert_code(self, code, action):
        with self.assertRaises(VideoMediaError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        text = str(caught.exception)
        self.assertNotIn("PRIVATE", text)
        self.assertNotIn(str(self.root), text)
        return caught.exception

    def test_inspect_contract_hash_and_sanitization(self):
        result = self.media.inspect(self.source)
        self.assertEqual(result["path"], str(self.source.resolve()))
        self.assertEqual(result["size_bytes"], self.source.stat().st_size)
        self.assertEqual(result["sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(result["duration"], 11.5)
        self.assertIsInstance(result["duration"], float)
        self.assertTrue(result["has_audio"])
        self.assertEqual((result["width"], result["height"]), (1280, 720))
        self.assertNotIn("PRIVATE", json.dumps(result, allow_nan=False))
        self.assertEqual(len(result["ffprobe"]["streams"]), 2)

    def test_probe_argument_order_and_subprocess_policy(self):
        self.media.inspect(self.source)
        args, kwargs = self.fake.calls[0]
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertNotEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertNotEqual(kwargs["stderr"], subprocess.PIPE)
        self.assertEqual(args[args.index("-protocol_whitelist") + 1], "file")
        self.assertNotIn("concat", args[args.index("-format_whitelist") + 1].split(","))
        self.assertLess(args.index("-format_whitelist"), args.index("-i"))
        self.assertEqual(args[-2:], ["-i", str(self.source)])
        self.assertNotIn("-show_format", args)

    def test_binary_precedence_and_lazy_missing(self):
        self.which.reset_mock()
        with patch.dict(os.environ, {"FFMPEG_BIN": "env-ffmpeg", "FFPROBE_BIN": "env-ffprobe"}):
            explicit = FFmpegMedia(ffmpeg="my-ffmpeg", ffprobe="my-ffprobe")
            self.which.assert_not_called()
            explicit.inspect(self.source)
            self.which.assert_called_with("my-ffprobe")
            self.assertEqual(explicit._binary("ffmpeg"), "/bin/my-ffmpeg")
            env = FFmpegMedia()
            env.inspect(self.source)
            self.which.assert_called_with("env-ffprobe")
            self.assertEqual(env._binary("ffmpeg"), "/bin/env-ffmpeg")
        self.which.side_effect = None
        self.which.return_value = None
        lazy = FFmpegMedia()
        self.assert_code("missing_binary", lambda: lazy.inspect(self.source))
        for value in ("", 5, b"binary"):
            self.assert_code("missing_binary", lambda: FFmpegMedia(ffprobe=value).inspect(self.source))

    def test_only_ffprobe_needed_for_inspect(self):
        self.which.side_effect = lambda name: "/bin/ffprobe" if name == "ffprobe" else None
        self.media.inspect(self.source)
        self.assert_code("missing_binary", lambda: self.media.extract(self.source, self.root / "out"))
        self.assertFalse((self.root / "out").exists())

    def test_invalid_timeouts(self):
        for value in (0, -1, True, "120", None, float("nan"), float("inf"), 600.1, 10**1000):
            with self.subTest(value=repr(value)):
                self.assert_code("configuration", lambda: FFmpegMedia(timeout=value))
        self.assertEqual(FFmpegMedia(timeout=600).timeout, 600)
        self.assertEqual(FFmpegMedia(timeout=0.01).timeout, 0.01)

    def test_invalid_limits(self):
        for value in (0, -1, True, 1.5, "5", None):
            with self.subTest(max_bytes=value):
                self.assert_code("configuration", lambda: self.media.inspect(self.source, max_bytes=value))
        for value in (0, -1, True, "5", None, float("nan"), float("inf"), 10**1000):
            with self.subTest(max_duration=repr(value)):
                self.assert_code("configuration", lambda: self.media.inspect(self.source, max_duration=value))
        self.assertFalse(self.fake.calls)

    def test_invalid_sampling(self):
        for value in (0, -1, True, "5", None, float("nan"), float("inf"), 10**1000):
            with self.subTest(interval=repr(value)):
                self.assert_code("configuration", lambda: self.media.extract(self.source, self.root / "out", interval=value))
        for value in (0, -1, True, 61, 1.5, "12", None):
            with self.subTest(max_frames=value):
                self.assert_code("configuration", lambda: self.media.extract(self.source, self.root / "out", max_frames=value))
        self.assertFalse(self.fake.calls)

    def test_nonlocal_missing_special_and_symlink_inputs(self):
        for value in ("https://example.test/x.mp4", "file:/etc/passwd", "pipe:0", "concat:a|b",
                      "", "a\x00b", None, 12, b"file.mp4", self.root):
            with self.subTest(value=value):
                self.assert_code("invalid_input", lambda: self.media.inspect(value))
        self.assert_code("input_missing", lambda: self.media.inspect(self.root / "missing.mp4"))
        link = self.root / "link.mp4"
        link.symlink_to(self.source)
        self.assert_code("invalid_input", lambda: self.media.inspect(link))
        fifo = self.root / "fifo.mp4"
        os.mkfifo(fifo)
        self.assert_code("invalid_input", lambda: self.media.inspect(fifo))
        self.assertFalse(self.fake.calls)

    def test_size_empty_and_playlist_limits(self):
        self.assert_code("size_limit", lambda: self.media.inspect(self.source, max_bytes=1))
        empty = self.root / "empty.mp4"
        empty.touch()
        self.assert_code("invalid_input", lambda: self.media.inspect(empty))
        for suffix in (".m3u8", ".MPD", ".ffconcat", ".pls"):
            item = self.root / ("list" + suffix)
            item.write_bytes(b"not standalone")
            self.assert_code("invalid_input", lambda: self.media.inspect(item))
        self.assertFalse(self.fake.calls)
        self.media.inspect(self.source, max_bytes=self.source.stat().st_size)

    def test_malformed_probe_json_and_schema(self):
        payloads = [b"not JSON PRIVATE", b"\xff", b"[]", b"null", b"{}", b"{" * 2000]
        for payload in payloads:
            with self.subTest(payload=payload[:30]):
                self.fake.payload = payload
                self.assert_code("invalid_probe", lambda: self.media.inspect(self.source))
        self.fake.payload = None
        for change in ({"streams": None}, {"streams": [5]}, {"format": None},
                       {"format": {"format_name": 5}}, {"format": {"format_name": "hls", "duration": "5"}},
                       {"format": {"format_name": "mov,hls", "duration": "5"}},
                       {"format": {"format_name": "dash", "duration": "5"}},
                       {"format": {"format_name": "concat", "duration": "5"}}):
            self.fake.metadata = probe()
            self.fake.metadata.update(change)
            self.assert_code("invalid_probe", lambda: self.media.inspect(self.source))

    def test_actual_video_required_not_audio_or_attached_picture(self):
        for streams in ([], [{"index": 0, "codec_type": "audio"}],
                        [{"index": 0, "codec_type": "video", "width": 640, "height": 480,
                          "disposition": {"attached_pic": 1}}]):
            self.fake.metadata = probe()
            self.fake.metadata["streams"] = streams
            self.assert_code("invalid_probe", lambda: self.media.inspect(self.source))

    def test_duration_finite_positive_required_and_bounded(self):
        for value in ("NaN", "Infinity", "-inf", "0", "-1", True, {}, "PRIVATE"):
            self.fake.metadata = probe()
            self.fake.metadata["format"]["duration"] = value
            self.assert_code("invalid_probe", lambda: self.media.inspect(self.source))
            self.fake.metadata = probe()
            self.fake.metadata["streams"][0]["duration"] = value
            self.assert_code("invalid_probe", lambda: self.media.inspect(self.source))
        self.fake.metadata = probe(601)
        self.assert_code("duration_limit", lambda: self.media.inspect(self.source))
        self.fake.metadata = probe(600)
        self.assertEqual(self.media.inspect(self.source)["duration"], 600)
        self.fake.metadata = probe()
        del self.fake.metadata["format"]["duration"]
        self.assertEqual(self.media.inspect(self.source)["duration"], 11.5)
        for stream in self.fake.metadata["streams"]:
            stream["duration"] = "N/A"
        self.assert_code("invalid_probe", lambda: self.media.inspect(self.source))

    def test_dimensions_and_stream_indices(self):
        for field, values in (("width", [0, -1, 1.5, True, None]),
                              ("height", [0, "720"]), ("index", [True, -1, "0", None])):
            for value in values:
                self.fake.metadata = probe()
                self.fake.metadata["streams"][0][field] = value
                self.assert_code("invalid_probe", lambda: self.media.inspect(self.source))
        self.fake.metadata = probe()
        self.fake.metadata["streams"][0].update(width=3840, height=2161)
        self.assert_code("resolution_limit", lambda: self.media.inspect(self.source))
        self.fake.metadata["streams"][0].update(width=3840, height=2160)
        self.assertEqual(self.media.inspect(self.source)["width"], 3840)

    def test_extract_contract_permissions_audio_and_commands(self):
        output = self.root / "artifacts"
        result = self.media.extract(self.source, output)
        self.assertEqual(result["artifact_dir"], str(output))
        self.assertEqual(result["sampling"], {"interval": 5.0, "max_frames": 12,
                                             "coverage": "sampled_frames_not_full_video",
                                             "video_duration": 11.5, "media_duration": 11.5,
                                             "unsampled_audio_tail_seconds": 0.0})
        self.assertEqual([frame["timestamp"] for frame in result["frames"]], [0, 5, 10])
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
        self.assertEqual(result["audio_path"], str(output / "audio.wav"))
        for index, frame in enumerate(result["frames"], 1):
            self.assertEqual(Path(frame["path"]).name, "frame_%04d.jpg" % index)
            self.assertEqual(frame["sha256"], hashlib.sha256(Path(frame["path"]).read_bytes()).hexdigest())
        for item in output.iterdir():
            self.assertEqual(stat.S_IMODE(item.stat().st_mode), 0o600)
        calls = [args for args, _ in self.fake.calls]
        frame_args = calls[1]
        self.assertIn("-nostdin", frame_args)
        self.assertLess(frame_args.index("-ss"), frame_args.index("-i"))
        self.assertLess(frame_args.index("-protocol_whitelist"), frame_args.index("-i"))
        self.assertIn("min(640,iw)", frame_args[frame_args.index("-vf") + 1])
        self.assertIn("min(640,ih)", frame_args[frame_args.index("-vf") + 1])
        self.assertEqual(frame_args[frame_args.index("-map") + 1], "0:0")
        self.assertEqual(frame_args[frame_args.index("-frames:v") + 1], "1")
        audio_args = calls[-1]
        self.assertEqual(audio_args[audio_args.index("-ac") + 1], "1")
        self.assertEqual(audio_args[audio_args.index("-ar") + 1], "16000")
        self.assertEqual(audio_args[audio_args.index("-c:a") + 1], "pcm_s16le")
        json.dumps(result, allow_nan=False)
        self.assertTrue(self.source.exists())

    def test_longer_audio_does_not_sample_beyond_video_eof(self):
        self.fake.metadata = probe(4)
        self.fake.metadata['streams'][0]['duration'] = '1'
        result = self.media.extract(self.source, self.root / 'out', interval=1, max_frames=4)
        self.assertEqual(result['media']['duration'], 4)
        self.assertEqual(result['media']['video_duration'], 1)
        self.assertEqual([f['timestamp'] for f in result['frames']], [0])
        self.assertEqual(result['sampling']['unsampled_audio_tail_seconds'], 3)
        self.assertIsNotNone(result['audio_path'])

    def test_missing_video_duration_falls_back_to_container(self):
        self.fake.metadata = probe(4, audio=False)
        del self.fake.metadata['streams'][0]['duration']
        self.assertEqual(self.media.inspect(self.source)['video_duration'], 4)

    def test_no_audio_short_clip_and_video_stream_mapping(self):
        self.fake.metadata = probe(0.25, audio=False)
        self.fake.metadata["streams"][0]["index"] = 2
        result = self.media.extract(self.source, self.root / "out")
        self.assertIsNone(result["audio_path"])
        self.assertEqual([frame["timestamp"] for frame in result["frames"]], [0])
        self.assertEqual(len(self.fake.calls), 2)
        args = self.fake.calls[1][0]
        self.assertEqual(args[args.index("-map") + 1], "0:2")

    def test_long_clip_samples_whole_duration_not_first_twelve_intervals(self):
        self.fake.metadata = probe(600, audio=False)
        result = self.media.extract(self.source, self.root / "out")
        timestamps = [frame["timestamp"] for frame in result["frames"]]
        self.assertEqual(len(timestamps), 12)
        self.assertEqual(timestamps[0], 0)
        self.assertEqual(timestamps[-1], 595)
        self.assertTrue(all(a < b for a, b in zip(timestamps, timestamps[1:])))
        self.assertTrue(all(0 <= t < 600 for t in timestamps))
        gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
        self.assertLess(max(gaps) - min(gaps), 1e-10)

    def test_sampling_edge_cases_single_frame_and_tiny_interval(self):
        for duration, interval, maximum in ((10, 5, 12), (60, 5, 12), (5, 5, 12),
                                            (600, 1e-320, 60), (600, 5, 1),
                                            (1e-320, 1e-320, 12)):
            times = video_media._timestamps(duration, interval, maximum)
            self.assertTrue(1 <= len(times) <= maximum)
            self.assertTrue(all(0 <= t < duration for t in times))
        self.assertEqual(video_media._timestamps(10, 5, 12), [0, 5])
        self.assertEqual(video_media._timestamps(600, 5, 1), [300])

    def test_filename_shell_syntax_is_only_an_argument(self):
        source = self.root / "input ; touch OWNED $(echo nope).mp4"
        source.write_bytes(b"video")
        output = self.root / "output ; echo OWNED"
        self.media.extract(source, output)
        for args, kwargs in self.fake.calls:
            self.assertIs(kwargs["shell"], False)
            self.assertIn(str(source), args)
        self.assertFalse((self.root / "OWNED").exists())

    def test_output_exclusive_including_dangling_symlink(self):
        output = self.root / "exists"
        output.mkdir()
        keep = output / "frame_0001.jpg"
        keep.write_bytes(b"KEEP")
        self.assert_code("output_exists", lambda: self.media.extract(self.source, output))
        self.assertEqual(keep.read_bytes(), b"KEEP")
        self.assertEqual(len(self.fake.calls), 1)  # Inspect first, no extraction.
        link = self.root / "dangling"
        target = self.root / "must-not-create"
        link.symlink_to(target)
        self.assert_code("output_exists", lambda: self.media.extract(self.source, link))
        self.assertFalse(target.exists())
        self.assert_code("output_error", lambda: self.media.extract(self.source, self.root / "missing-parent" / "out"))

    def test_source_changed_during_probe(self):
        self.fake.on_command = lambda args: self.source.write_bytes(b"replacement")
        self.assert_code("input_changed", lambda: self.media.inspect(self.source))

    def test_source_changed_during_extraction_leaves_partial_artifacts(self):
        def mutate(args):
            if "-show_entries" not in args:
                self.source.write_bytes(b"changed video")
        self.fake.on_command = mutate
        output = self.root / "partial"
        self.assert_code("input_changed", lambda: self.media.extract(self.source, output))
        self.assertTrue((output / "frame_0001.jpg").exists())
        self.assertTrue(self.source.exists())

    def test_failure_exit_sanitized_and_partial_files_retained(self):
        self.fake.stderr = b"PRIVATE credentials and ffmpeg error details"
        self.fake.status = 1
        self.assert_code("process_failed", lambda: self.media.inspect(self.source))
        self.fake.status = 0
        def fail_extract(args):
            if "-show_entries" not in args:
                self.fake.status = 1
        self.fake.on_command = fail_extract
        output = self.root / "partial"
        self.assert_code("process_failed", lambda: self.media.extract(self.source, output))
        self.assertTrue(output.exists())
        self.assertTrue((output / "frame_0001.jpg").exists())
        self.assertTrue(self.fake.processes[-1].killed)

    def test_timeout_kills_and_reaps_process(self):
        self.fake.timeout = True
        with patch.object(video_media.time, "monotonic", side_effect=[0, 1]):
            self.assert_code("timeout", lambda: FFmpegMedia(timeout=0.1).inspect(self.source))
        self.assertTrue(self.fake.processes[-1].killed)

    def test_timeout_polling_uses_bounded_wait(self):
        self.fake.timeout = True
        # First bounded wait expires; second clock check reaches deadline.
        with patch.object(video_media.time, "monotonic", side_effect=[0, 0.01, 1]):
            self.assert_code("timeout", lambda: FFmpegMedia(timeout=0.1).inspect(self.source))
        self.assertTrue(self.fake.processes[-1].killed)

    def test_stdout_stderr_caps_and_bounded_read(self):
        self.fake.payload = b"x" * (video_media._MAX_OUTPUT + 1)
        self.assert_code("output_limit", lambda: self.media.inspect(self.source))
        self.fake.payload = None
        self.fake.stderr = b"x" * (video_media._MAX_OUTPUT + 1)
        self.assert_code("output_limit", lambda: self.media.inspect(self.source))

    def test_spawn_errors_are_sanitized(self):
        for error, code in ((FileNotFoundError("PRIVATE"), "missing_binary"),
                            (PermissionError("PRIVATE"), "process_failed"),
                            (ValueError("PRIVATE"), "process_failed")):
            with patch.object(video_media.subprocess, "Popen", side_effect=error):
                self.assert_code(code, lambda: self.media.inspect(self.source))

    def test_missing_or_empty_artifact_rejected(self):
        self.fake.artifacts = False
        self.assert_code("output_error", lambda: self.media.extract(self.source, self.root / "out"))

    def test_hash_detects_signature_change(self):
        signature = video_media._signature(self.source.stat())
        self.source.write_bytes(b"replacement")
        self.assert_code("input_changed", lambda: video_media._hash(self.source, signature))

    def test_final_sha_is_compared_even_when_stat_is_unchanged(self):
        original = video_media._hash
        source_hashes = 0
        def changed_digest(path, expected=None):
            nonlocal source_hashes
            digest = original(path, expected)
            if path == self.source:
                source_hashes += 1
                if source_hashes > 1:
                    return "0" * 64
            return digest
        with patch.object(video_media, "_hash", side_effect=changed_digest):
            self.assert_code("input_changed", lambda: self.media.extract(self.source, self.root / "out"))
        self.assertEqual(source_hashes, 2)

    def test_source_removed_during_probe_and_hash_unavailable(self):
        self.fake.on_command = lambda args: self.source.unlink()
        self.assert_code("input_changed", lambda: self.media.inspect(self.source))
        self.assert_code("input_changed", lambda: video_media._hash(self.source))

    def test_private_artifact_creation_and_completion_errors(self):
        existing = self.root / "existing.jpg"
        existing.write_bytes(b"KEEP")
        self.assert_code("output_error", lambda: self.media._artifact(existing))
        self.assertEqual(existing.read_bytes(), b"KEEP")
        self.assert_code("output_error", lambda: self.media._finish_artifact(self.root / "missing"))
        self.assert_code("output_error", lambda: self.media.extract(self.source, None))

    def test_relative_input_resolves_to_absolute(self):
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            result = self.media.inspect("clip.mp4")
        finally:
            os.chdir(previous)
        self.assertEqual(result["path"], str(self.source))


if __name__ == "__main__":
    unittest.main()
