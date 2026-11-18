"""Offline video orchestration/SQLite/CLI regressions; no installed models required."""
from contextlib import closing, redirect_stdout, redirect_stderr
import copy
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from telegram_demo import storage, video_pipeline as vp
from telegram_demo.storage import Database
from telegram_demo.text_classifier import tool_backend
from telegram_demo.video_models import VideoModelError
from telegram_demo.cli import video


class VideoPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'app.sqlite3'
        self.db = Database(self.path)
        self.addCleanup(self.db.close)
        self.store = vp.VideoStore(self.db)
        audio = self.root / 'audio.wav'
        audio.write_bytes(b'RIFF offline audio')
        self.extraction = {
            'media': {'path': str(self.root / 'clip.mp4'), 'sha256': 'a' * 64,
                      'duration': 11.0, 'video_duration': 11.0, 'has_audio': True},
            'artifact_dir': str(self.root / 'artifacts'), 'audio_path': str(audio),
            'frames': [{'path': str(self.root / 'frame.jpg'), 'timestamp': 0.0, 'sha256': 'b' * 64}],
            'sampling': {'coverage': 'sampled_frames_not_full_video'},
        }
        self.transcript = {'provider': 'fake-asr', 'model': 'tiny', 'language': 'vi',
                           'text': 'Bản tin hôm nay.', 'segments': [{'start': 0, 'end': 2, 'text': 'Bản tin hôm nay.'}]}
        self.visual = {'provider': 'fake-vision', 'model': 'fixture', 'frames': [
            {'timestamp': 0, 'description': 'Một bản tin.', 'ocr_text': 'DEMO'}],
            'responses': [{'raw_private': 'not part of Jev state'}]}

    def save(self, status='ready', message_id=None):
        return self.store.save_analysis(self.extraction,
            transcript={'status': 'success', **self.transcript},
            vision={'status': 'success', **self.visual}, status=status,
            options={'sampling': 'fixture'}, message_row_id=message_id)

    def process(self, **kwargs):
        media = Mock()
        media.extract.return_value = copy.deepcopy(self.extraction)
        whisper, vision = Mock(), Mock()
        whisper.transcribe.return_value = copy.deepcopy(self.transcript)
        vision.describe.return_value = copy.deepcopy(self.visual)
        return vp.process_video(self.store, 'local.mp4', self.root / 'out', media=media,
                                whisper=whisper, vision=vision, **kwargs)

    def judgment(self, aid, backend=None, status='success'):
        item = self.store.item(aid, True)
        self.store.save_judgment(item, batch_id=str(uuid.uuid4()), provider='test', model='fixture',
            backend_key=backend or tool_backend('jev-latest'), status=status,
            label='other' if status == 'success' else None,
            error_code='network' if status == 'error' else None)
        return item

    def cli(self, *args):
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            rc = video.main(['--db', str(self.path), *map(str, args)])
        return rc, out.getvalue(), err.getvalue()

    def test_ready_evidence_is_timestamped_and_excludes_raw_vision_response(self):
        aid = self.process()
        row = self.store.analysis(aid)
        self.assertEqual(row['status'], 'ready')
        self.assertEqual(row['evidence']['transcript']['segments'][0]['start'], 0)
        self.assertEqual(row['evidence']['visual_observations']['frames'][0]['timestamp'], 0)
        self.assertNotIn('raw_private', json.dumps(self.store.item(aid)))
        self.assertIn('raw_private', json.dumps(row['vision']))

    def test_no_audio_valid_vision_ready_without_asr(self):
        self.extraction['media']['has_audio'] = False
        self.extraction['audio_path'] = None
        row = self.store.analysis(self.process())
        self.assertEqual(row['status'], 'ready')
        self.assertEqual(row['transcript']['status'], 'no_audio')
        self.assertEqual(row['evidence']['gaps'], [])

    def test_explicit_skip_requires_partial_opt_in(self):
        media = Mock(); media.extract.return_value = self.extraction
        aid = vp.process_video(self.store, 'clip', 'out', media=media)
        self.assertEqual(self.store.analysis(aid)['status'], 'partial')
        with self.assertRaisesRegex(ValueError, 'allow-partial'):
            self.store.item(aid)
        self.assertEqual(len(self.store.item(aid, True)['state']['evidence']['gaps']), 2)

    def test_extract_only_has_no_model_or_label(self):
        aid = self.process(extract_only=True)
        self.assertEqual(self.store.analysis(aid)['status'], 'extracted')
        with self.assertRaises(ValueError): self.store.item(aid, True)
        self.assertEqual(self.db.stats()['video_classifications'], 0)

    def test_failure_records_actual_stage_and_completed_paid_frames(self):
        media = Mock(); media.extract.return_value = self.extraction
        whisper = Mock(); whisper.transcribe.return_value = self.transcript
        vision = Mock()
        error = VideoModelError('network_error', 'safe error')
        error.partial_result = self.visual
        vision.describe.side_effect = error
        with self.assertRaisesRegex(RuntimeError, 'network_error'):
            vp.process_video(self.store, 'clip', 'out', media=media, whisper=whisper, vision=vision)
        row = self.store.analysis(1)
        self.assertEqual(row['status'], 'error')
        self.assertEqual(row['transcript']['status'], 'success')
        self.assertEqual(row['vision']['status'], 'error')
        self.assertEqual(row['vision']['frames'], self.visual['frames'])
        self.assertIn('visual_understanding_failed', row['evidence']['gaps'])
        self.assertNotIn('visual_understanding_explicitly_skipped', row['evidence']['gaps'])
        with self.assertRaises(ValueError): self.store.item(1, True)

    def test_asr_error_not_misrepresented_as_explicit_skip(self):
        media = Mock(); media.extract.return_value = self.extraction
        whisper = Mock(); whisper.transcribe.side_effect = VideoModelError('transcription_error', 'safe')
        with self.assertRaises(RuntimeError):
            vp.process_video(self.store, 'clip', 'out', media=media, whisper=whisper)
        row = self.store.analysis(1)
        self.assertEqual(row['transcript'], {'status': 'error', 'reason': 'transcription_error'})
        self.assertIn('speech_transcription_failed', row['evidence']['gaps'])

    def test_evidence_limit_no_silent_truncation(self):
        self.transcript['text'] = 'x' * (vp.MAX_EVIDENCE_CHARS + 1)
        aid = self.save()
        with self.assertRaisesRegex(ValueError, 'không cắt ngầm'): self.store.item(aid)
        self.assertEqual(len(self.store.analysis(aid)['transcript']['text']), len(self.transcript['text']))

    def test_failure_cache_retryable_success_cache_and_recheck(self):
        aid = self.save()
        self.judgment(aid, status='error')
        self.assertIsNotNone(vp.prepare_manifest(self.store, aid))
        self.judgment(aid)
        self.assertIsNone(vp.prepare_manifest(self.store, aid))
        self.assertIsNotNone(vp.prepare_manifest(self.store, aid, recheck=True))
        self.assertIsNotNone(vp.prepare_manifest(self.store, aid, model='another-model'))

    def test_manifest_tamper_and_partial_gate(self):
        aid = self.save()
        manifest = vp.prepare_manifest(self.store, aid)
        self.assertEqual(vp.validate_manifest(self.store, manifest), self.store.item(aid))
        for mutate in (lambda x: x['item']['state']['evidence'].update(has_audio=False),
                       lambda x: x['item']['source'].update(source_path='different'),
                       lambda x: x['item'].update(evidence_sha256='fake'),
                       lambda x: x.update(questions={}), lambda x: x.update(batch_id='invalid')):
            changed = copy.deepcopy(manifest); mutate(changed)
            with self.assertRaises(ValueError): vp.validate_manifest(self.store, changed)
        partial = self.save('partial')
        with self.assertRaises(ValueError): vp.prepare_manifest(self.store, partial)
        self.assertIsNotNone(vp.prepare_manifest(self.store, partial, allow_partial=True))

    def test_source_message_validation_before_expensive_work(self):
        media = Mock()
        with self.assertRaises(ValueError):
            vp.process_video(self.store, 'clip', 'out', media=media, message_row_id=999)
        media.extract.assert_not_called()

    def test_message_delete_keeps_immutable_video_evidence(self):
        self.db.save_payload({'backend': 'web', 'posts': [{'channel': 'news', 'message_id': 1, 'text': 'hello'}]})
        mid = self.db.conn.execute('SELECT id FROM messages').fetchone()[0]
        aid = self.save(message_id=mid); item = self.judgment(aid)
        with self.db.transaction(): self.db.conn.execute('DELETE FROM messages WHERE id=?', (mid,))
        self.assertIsNone(self.store.analysis(aid)['message_row_id'])
        old_source = self.store.results(analysis_id=aid)[0]['classifications'][0]['source']
        self.assertEqual(old_source, item['source'])
        self.assertEqual(self.db.conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_cli_inspect_no_db_or_models(self):
        absent = self.root / 'no.sqlite3'
        with patch('telegram_demo.video_media.FFmpegMedia') as media:
            media.return_value.inspect.return_value = {'duration': 1}
            with redirect_stdout(io.StringIO()):
                self.assertEqual(video.main(['--db', str(absent), 'inspect', 'clip.mp4']), 0)
        self.assertFalse(absent.exists())

    def test_cli_fresh_media_parent_and_no_models_for_extract(self):
        with patch.object(video, 'MEDIA_DIR', self.root / 'new-media'), \
             patch('telegram_demo.video_media.FFmpegMedia') as media:
            def extract(path, out, **kwargs):
                self.assertTrue(out.parent.is_dir())
                result = copy.deepcopy(self.extraction); result['artifact_dir'] = str(out)
                return result
            media.return_value.extract.side_effect = extract
            rc, _, err = self.cli('extract', 'clip.mp4')
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.store.analysis(1)['status'], 'extracted')

    def test_cli_skip_both_and_partial_dry_run_no_judgment(self):
        with patch('telegram_demo.video_media.FFmpegMedia') as media, \
             patch.object(video, 'MEDIA_DIR', self.root / 'media'):
            media.return_value.extract.return_value = self.extraction
            self.assertEqual(self.cli('analyze', 'clip', '--skip-transcript', '--skip-vision')[0], 0)
        self.assertEqual(self.cli('classify', 1, '--dry-run')[0], 2)
        rc, out, _ = self.cli('classify', 1, '--dry-run', '--allow-partial')
        self.assertEqual(rc, 0); self.assertIn('"network": false', out)
        self.assertEqual(self.db.stats()['video_classifications'], 0)

    def test_cli_missing_database_does_not_create_it(self):
        absent = self.root / 'missing.sqlite3'
        with redirect_stderr(io.StringIO()):
            self.assertEqual(video.main(['--db', str(absent), 'results']), 2)
        self.assertFalse(absent.exists())

    def test_cli_direct_judgment_cache_and_error_without_fallback(self):
        aid = self.save()
        answer = {'model': 'jev-fixture', 'answers': {'classification': {
            'choice': 'other', 'probabilities': {label: float(label == 'other') for label in vp.LABELS}}}}
        with patch('telegram_demo.jev_client.JevClient') as client:
            client.return_value.backend_key = 'test-direct'
            client.return_value.model = 'jev-latest'
            client.return_value.ask.return_value = answer
            self.assertEqual(self.cli('classify', aid)[0], 0)
            self.assertEqual(self.cli('classify', aid)[0], 0)
            self.assertEqual(client.return_value.ask.call_count, 1)
        from telegram_demo.jev_client import JevError
        with patch('telegram_demo.jev_client.JevClient') as client:
            client.return_value.backend_key = 'failed-direct'; client.return_value.model = 'jev-latest'
            client.return_value.ask.side_effect = JevError('configuration', 'safe')
            self.assertEqual(self.cli('classify', aid)[0], 1)
        row = self.store.results(analysis_id=aid)[0]['classifications'][0]
        self.assertEqual(row['status'], 'error'); self.assertIsNone(row['label'])

    def test_cli_bridge_exclusive_permissions_replay_prepare_cache(self):
        aid = self.save()
        request = self.root / 'request.json'; result = self.root / 'result.json'
        self.assertEqual(self.cli('prepare', aid, '--out', request)[0], 0)
        self.assertEqual(request.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.cli('prepare', aid, '--out', request)[0], 2)
        manifest = json.loads(request.read_text())
        result.write_text(json.dumps({'format': vp.RESULT_FORMAT, 'batch_id': manifest['batch_id'],
            'video_analysis_id': aid, 'evidence_sha256': manifest['item']['evidence_sha256'],
            'tool_output': 'Jev jev-1.14.0\nclassification: other (confidence: 0.99)'}))
        self.assertEqual(self.cli('apply', '--requests', request, '--results', result)[0], 0)
        self.assertEqual(self.cli('apply', '--requests', request, '--results', result)[0], 0)
        self.assertEqual(self.db.stats()['video_classifications'], 1)
        cached = self.root / 'cached.json'
        self.assertEqual(self.cli('prepare', aid, '--out', cached)[0], 0)
        self.assertFalse(cached.exists())
        self.assertEqual(self.cli('prepare', aid, '--out', cached, '--recheck')[0], 0)
        judgment = self.store.results(analysis_id=aid)[0]['classifications'][0]
        self.assertEqual(judgment['provider'], 'dsh-jev-tool-import')
        self.assertIsNone(judgment['probabilities'])
        self.assertEqual(judgment['response']['transport'], 'operator-provided-jev-tool-output')

    def test_legacy_v1_v2_migrations_preserve_every_table(self):
        for version in (1, 2):
            path = self.root / f'legacy{version}.sqlite3'
            schema = storage.SCHEMA.replace(storage.VIDEO_SCHEMA, '').replace(storage.IMAGE_SCHEMA, '')
            if version == 1: schema = schema.replace(storage.CLASSIFICATION_SCHEMA, '')
            with closing(sqlite3.connect(path)) as conn:
                conn.executescript(schema + f'PRAGMA user_version={version}; PRAGMA application_id={storage.APPLICATION_ID};')
            with Database(path) as migrated:
                self.assertEqual(migrated.conn.execute('PRAGMA user_version').fetchone()[0], storage.SCHEMA_VERSION)
                migrated.save_payload({'backend': 'web', 'posts': [{'channel': 'news', 'message_id': 2, 'text': 'immutable'}]})
                if version == 2:
                    from telegram_demo.text_classifier import ClassifierStore
                    text_store = ClassifierStore(migrated)
                    item = text_store.candidates(backend_key='legacy', limit=1)[0]
                    text_store.save(item, batch_id=str(uuid.uuid4()), provider='fixture', model='fixture',
                                    backend_key='legacy', status='success', label='other')
                migrated.conn.execute('DROP TABLE video_classifications')
                migrated.conn.execute('DROP TABLE video_analyses')
                if version == 1:
                    migrated.conn.execute('DROP TABLE message_classifications')
                migrated.conn.execute(f'PRAGMA user_version={version}')
                migrated.conn.commit()
                tables = [r[0] for r in migrated.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                before = {t: [tuple(r) for r in migrated.conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid')] for t in tables}
            with Database(path) as upgraded:
                after = {t: [tuple(r) for r in upgraded.conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid')] for t in tables}
                self.assertEqual(before, after)
                self.assertEqual(upgraded.conn.execute('PRAGMA foreign_key_check').fetchall(), [])
                self.assertEqual(upgraded.stats()['video_analyses'], 0)


if __name__ == '__main__':
    unittest.main()
