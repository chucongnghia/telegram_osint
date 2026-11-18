"""Offline local frame-OCR integration and additive schema-v3 migration."""
from contextlib import closing, redirect_stdout, redirect_stderr
import copy
import io
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from telegram_demo import storage
from telegram_demo.image_ocr import OCRError
from telegram_demo.video_pipeline import VideoStore, process_video
from telegram_demo.video_models import VideoModelError
from telegram_demo.cli import video


class LocalFrameOCRTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = storage.Database(self.root / 'app.sqlite3')
        self.addCleanup(self.db.close)
        self.store = VideoStore(self.db)
        self.extraction = {'media': {'path': 'clip.mp4', 'sha256': 'a' * 64,
            'duration': 4, 'has_audio': False}, 'artifact_dir': str(self.root),
            'frames': [{'path': 'frame.jpg', 'sha256': 'b' * 64, 'timestamp': 1.5}],
            'audio_path': None, 'sampling': {'coverage': 'sampled_frames_not_full_video'}}
        self.ocr = {'provider': 'tesseract', 'version': 'fixture', 'languages': ['vie'],
            'text': 'Lãi 30%', 'frames': [{'timestamp': 1.5, 'sha256': 'b' * 64,
            'text': 'Lãi 30%', 'words': [{'text': 'Lãi', 'confidence': 88,
            'bbox': {'left': 1, 'top': 2, 'width': 20, 'height': 10}}]}]}

    def process(self, **kwargs):
        media = Mock()
        media.extract.return_value = copy.deepcopy(self.extraction)
        return process_video(self.store, 'clip.mp4', self.root, media=media, **kwargs)

    def test_local_ocr_timestamp_and_boxes_retained_but_not_sent_as_model_evidence(self):
        ocr = Mock(); ocr.recognize_frames.return_value = self.ocr
        aid = self.process(ocr=ocr)
        row = self.store.analysis(aid)
        self.assertEqual(row['status'], 'partial')  # OCR is not scene understanding.
        self.assertEqual(row['media']['local_ocr']['frames'][0]['words'][0]['confidence'], 88)
        projected = row['evidence']['local_ocr']['frames'][0]
        self.assertEqual(projected, {'timestamp': 1.5, 'sha256': 'b' * 64, 'text': 'Lãi 30%'})
        self.assertIn('visual_understanding_explicitly_skipped', row['evidence']['gaps'])
        self.assertEqual(row['evidence']['transcript']['status'], 'no_audio')
        with self.assertRaises(ValueError): self.store.item(aid)
        self.assertEqual(self.db.stats()['video_classifications'], 0)

    def test_empty_ocr_does_not_produce_safe_label(self):
        result = copy.deepcopy(self.ocr)
        result['text'] = ''; result['frames'][0]['text'] = ''; result['frames'][0]['words'] = []
        ocr = Mock(); ocr.recognize_frames.return_value = result
        row = self.store.analysis(self.process(ocr=ocr))
        self.assertEqual(row['media']['local_ocr']['status'], 'success')
        self.assertEqual(row['status'], 'partial')
        self.assertEqual(self.db.stats()['video_classifications'], 0)

    def test_partial_ocr_failure_keeps_completed_frames_and_rejects_classification(self):
        error = OCRError('timeout', 'safe message')
        error.partial_result = self.ocr
        ocr = Mock(); ocr.recognize_frames.side_effect = error
        with self.assertRaisesRegex(RuntimeError, 'timeout'): self.process(ocr=ocr)
        row = self.store.analysis(1)
        self.assertEqual(row['status'], 'error')
        self.assertEqual(row['media']['local_ocr']['frames'][0]['text'], 'Lãi 30%')
        self.assertIn('local_frame_ocr_failed', row['evidence']['gaps'])
        with self.assertRaises(ValueError): self.store.item(1, True)
        self.assertEqual(self.db.stats()['video_classifications'], 0)

    def test_ocr_error_without_partial_result_still_persists_error_analysis(self):
        ocr = Mock(); ocr.recognize_frames.side_effect = OCRError('frame_limit', 'safe')
        with self.assertRaisesRegex(RuntimeError, 'frame_limit'): self.process(ocr=ocr)
        row = self.store.analysis(1)
        self.assertEqual(row['status'], 'error')
        self.assertEqual(row['media']['local_ocr']['status'], 'error')
        self.assertEqual(self.db.stats()['video_classifications'], 0)

    def test_later_asr_failure_retains_successful_ocr(self):
        audio = self.root / 'audio.wav'; audio.write_bytes(b'RIFF fixture')
        self.extraction['media']['has_audio'] = True
        self.extraction['audio_path'] = str(audio)
        ocr = Mock(); ocr.recognize_frames.return_value = self.ocr
        whisper = Mock(); whisper.transcribe.side_effect = VideoModelError('transcription_error', 'safe')
        with self.assertRaises(RuntimeError): self.process(ocr=ocr, whisper=whisper)
        row = self.store.analysis(1)
        self.assertEqual(row['media']['local_ocr']['status'], 'success')
        self.assertEqual(row['transcript']['status'], 'error')

    def test_extract_only_does_not_call_ocr(self):
        ocr = Mock()
        row = self.store.analysis(self.process(ocr=ocr, extract_only=True))
        ocr.recognize_frames.assert_not_called()
        self.assertEqual(row['status'], 'extracted')
        self.assertNotIn('local_ocr', row['media'])

    def test_cli_auto_language_maps_to_none_without_model_download(self):
        media = Mock(); media.extract.return_value = self.extraction
        with patch('telegram_demo.video_media.FFmpegMedia', return_value=media), \
             patch('telegram_demo.video_models.WhisperTranscriber') as whisper, \
             patch.object(video, 'MEDIA_DIR', self.root), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = video.main(['--db', self.db.path, 'analyze', 'clip.mp4',
                             '--skip-vision', '--language', 'auto'])
        self.assertEqual(rc, 0)
        self.assertIsNone(whisper.call_args.kwargs['language'])
        self.assertFalse(whisper.call_args.kwargs['allow_download'])

    def test_cli_explicit_ffmpeg_tools_are_forwarded_to_local_ocr(self):
        media = Mock(); media.extract.return_value = self.extraction
        adapter = Mock(); adapter.recognize_frames.return_value = self.ocr
        with patch('telegram_demo.video_media.FFmpegMedia', return_value=media), \
             patch('telegram_demo.image_ocr.LocalOCR', return_value=adapter) as constructor, \
             patch.object(video, 'MEDIA_DIR', self.root), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = video.main(['--db', self.db.path, 'analyze', 'clip.mp4', '--ocr',
                '--skip-vision', '--skip-transcript', '--ffmpeg', '/explicit/ffmpeg',
                '--ffprobe', '/explicit/ffprobe'])
        self.assertEqual(rc, 0)
        self.assertEqual(constructor.call_args.kwargs['ffmpeg'], '/explicit/ffmpeg')
        self.assertEqual(constructor.call_args.kwargs['ffprobe'], '/explicit/ffprobe')

    def test_v3_additive_migration_preserves_every_original_row(self):
        target = self.root / 'v3.sqlite3'
        with closing(sqlite3.connect(target)) as conn:
            conn.executescript(storage.SCHEMA.replace(storage.IMAGE_SCHEMA, '') +
                f'PRAGMA user_version=3; PRAGMA application_id={storage.APPLICATION_ID};')
            conn.execute("INSERT INTO runs VALUES(1,'old','old','web','fixture','telegram',NULL,'{}')")
            conn.commit()
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            before = {t: conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid').fetchall() for t in tables}
        with storage.Database(target) as upgraded:
            after = {t: [tuple(r) for r in upgraded.conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid')] for t in tables}
            self.assertEqual(before, after)
            self.assertEqual(upgraded.conn.execute('PRAGMA user_version').fetchone()[0], 4)
            self.assertEqual(upgraded.conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(upgraded.stats()['image_analyses'], 0)
            self.assertEqual(upgraded.stats()['image_classifications'], 0)


if __name__ == '__main__':
    unittest.main()
