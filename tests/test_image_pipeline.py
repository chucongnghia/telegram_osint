"""Offline image pipeline/CLI contract tests; no OCR binaries/models/network."""
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
from telegram_demo import image_pipeline as ip
from telegram_demo.image_schema import IMAGE_SCHEMA
from telegram_demo.storage import Database
from telegram_demo.text_classifier import tool_backend
from telegram_demo.cli import image
from telegram_demo.jev_client import JevError


class SafeError(RuntimeError):
    code = 'ocr_failure'
    partial_result = {'provider': 'fake-ocr', 'text': 'completed partial OCR', 'words': []}


class ImagePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'app.sqlite3'
        self.db = Database(self.path)
        self.addCleanup(self.db.close)
        # Schema-only fixture also works while parent owns storage migration.
        self.db.conn.executescript(IMAGE_SCHEMA + 'CREATE TABLE lock_probe(value TEXT);')
        self.store = ip.ImageStore(self.db)
        self.source = self.root / 'source.png'
        self.source.write_bytes(b'original image bytes; offline fake parser')
        self.media = {'path': str(self.source), 'sha256': ip.file_hash(self.source),
                      'size': self.source.stat().st_size, 'width': 800, 'height': 900, 'format': 'PNG'}
        self.ocr_result = {'provider': 'fake-ocr', 'version': 'test', 'languages': ['vie', 'eng'],
            'text': 'Bản tin hôm nay.', 'words': [{'text': 'Bản', 'confidence': 91.123456789,
            'left': 1, 'top': 2, 'width': 3, 'height': 4}],
            'image': copy.deepcopy(self.media), 'preprocessing': {'mode': 'fixture'},
            'limitations': ['OCR may be wrong']}
        self.visual = {'provider': 'fake-vision', 'model': 'fixture', 'resolved_model': 'fixture-1',
            'frames': [{'timestamp': 0.0, 'description': 'Một bản tin.', 'ocr_text': 'DEMO'}],
            'responses': [{'raw_private': 'full original provider result retained'}]}
        self.ocr = Mock()
        self.ocr.inspect.return_value = copy.deepcopy(self.media)
        self.ocr.recognize.return_value = copy.deepcopy(self.ocr_result)
        self.ocr.prepare_for_vision.side_effect = self.prepare_frame
        self.vision = Mock()
        self.vision.describe.return_value = copy.deepcopy(self.visual)

    def prepare_frame(self, source, directory):
        self.assertEqual(ip.file_hash(source), self.media['sha256'])
        directory = Path(directory)
        directory.mkdir(mode=0o700)
        path = directory / 'frame.jpg'
        path.write_bytes(b'normalized JPEG fixture')
        return {'path': str(path), 'sha256': ip.file_hash(path), 'timestamp': 0.0}

    def process(self, vision=True, **kwargs):
        directory = self.root / str(uuid.uuid4())
        return ip.process_image(self.store, self.source, directory, ocr=self.ocr,
                                vision=self.vision if vision else None, **kwargs)

    def save(self, status='ready', message_id=None):
        media = {**self.media, 'artifact_dir': str(self.root), 'original_path': str(self.source)}
        return self.store.save_analysis(media, ocr={**self.ocr_result, 'status': 'success'},
            vision={**self.visual, 'status': 'success'}, status=status,
            options={'fixture': True}, message_row_id=message_id)

    def judgment(self, aid, status='success', backend=None):
        item = self.store.item(aid, True)
        self.store.save_judgment(item, batch_id=str(uuid.uuid4()), provider='fixture',
            model='fixture', backend_key=backend or tool_backend('jev-latest'), status=status,
            label='other' if status == 'success' else None,
            error_code='network' if status == 'error' else None)
        return item

    def cli(self, *args):
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            rc = image.main(['--db', str(self.path), *map(str, args)])
        return rc, out.getvalue(), err.getvalue()

    def assert_unlocked(self):
        with closing(sqlite3.connect(self.path, timeout=0.1)) as other:
            other.execute('INSERT INTO lock_probe VALUES(?)', ('unlocked',))
            other.commit()

    def test_ready_keeps_original_full_ocr_and_raw_vision_provenance(self):
        aid = self.process()
        row = self.store.analysis(aid)
        self.assertEqual(row['status'], 'ready')
        self.assertEqual(row['image_sha256'], self.media['sha256'])
        original = Path(row['media']['original_path'])
        self.assertNotEqual(original, self.source)
        self.assertEqual(ip.file_hash(original), row['image_sha256'])
        self.assertEqual(original.stat().st_mode & 0o777, 0o400)
        self.assertEqual(original.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.source.read_bytes(), original.read_bytes())
        self.assertEqual(self.ocr.recognize.call_args.args[0], str(original))
        self.assertEqual(row['ocr']['words'], self.ocr_result['words'])
        self.assertEqual(row['evidence']['ocr']['text'], self.ocr_result['text'])
        self.assertNotIn('words', row['evidence']['ocr'])
        self.assertEqual(row['evidence']['ocr_projection']['word_count'], 1)
        self.assertEqual(row['evidence']['ocr_confidence']['mean'], 91.123456789)
        self.assertIn('not_probability', row['evidence']['ocr_confidence']['scale'])
        self.assertIn('raw_private', json.dumps(row['vision']))
        self.assertNotIn('raw_private', json.dumps(self.store.item(aid)))
        frame = self.vision.describe.call_args.args[0][0]
        self.assertTrue(frame['path'].endswith('.jpg'))
        self.assertNotEqual(frame['sha256'], self.media['sha256'])
        self.assertEqual(frame, row['media']['vision_frame'])
        self.assertEqual(row['evidence']['visual_observations']['resolved_model'], 'fixture-1')
        # Later edits of the input cannot rewrite already stored evidence/artifact.
        self.source.write_bytes(b'later replacement')
        self.assertEqual(ip.file_hash(original), row['image_sha256'])
        self.assertEqual(self.store.analysis(aid)['ocr'], row['ocr'])

    def test_default_ocr_on_semantic_vision_off_is_partial_and_not_classified(self):
        aid = self.process(vision=False)
        row = self.store.analysis(aid)
        self.ocr.recognize.assert_called_once()
        self.ocr.prepare_for_vision.assert_not_called()
        self.vision.describe.assert_not_called()
        self.assertEqual(row['status'], 'partial')
        self.assertIn('semantic_visual_understanding_not_requested', row['evidence']['gaps'])
        with self.assertRaisesRegex(ValueError, 'allow-partial'):
            self.store.item(aid)
        self.assertEqual(self.store.item(aid, True)['image_analysis_id'], aid)
        self.assertEqual(self.db.conn.execute('SELECT count(*) FROM image_classifications').fetchone()[0], 0)

    def test_ocr_none_constructs_lazy_default(self):
        with patch('telegram_demo.image_ocr.LocalOCR', return_value=self.ocr) as factory:
            aid = ip.process_image(self.store, self.source, self.root / 'default')
        factory.assert_called_once_with()
        self.assertEqual(self.store.analysis(aid)['status'], 'partial')

    def test_empty_ocr_is_not_safety_verdict(self):
        self.ocr.recognize.return_value['text'] = ''
        aid = self.process(vision=False)
        row = self.store.analysis(aid)
        self.assertEqual(row['status'], 'partial')
        self.assertIn('no_legible_text_is_not_a_safety_verdict', row['evidence']['gaps'])
        self.assertIn('empty text is not a safety verdict', ' '.join(row['evidence']['limitations']))
        self.assertEqual(row['classifications'] if 'classifications' in row else [], [])

    def test_explicit_skip_ocr_is_partial_even_with_vision(self):
        aid = self.process(skip_ocr=True)
        self.ocr.recognize.assert_not_called()
        self.assertEqual(self.store.analysis(aid)['status'], 'partial')
        self.assertIn('local_ocr_explicitly_skipped', self.store.analysis(aid)['evidence']['gaps'])

    def test_no_sqlite_lock_during_ocr_and_vision(self):
        def recognize(path):
            self.assert_unlocked()
            return copy.deepcopy(self.ocr_result)
        def describe(frames):
            self.assert_unlocked()
            return copy.deepcopy(self.visual)
        self.ocr.recognize.side_effect = recognize
        self.vision.describe.side_effect = describe
        self.process()
        self.assertEqual(self.db.conn.execute('SELECT count(*) FROM lock_probe').fetchone()[0], 2)

    def test_ocr_failure_persists_partial_output_without_fallback(self):
        self.ocr.recognize.side_effect = SafeError('not persisted secret')
        with self.assertRaisesRegex(RuntimeError, 'ocr_failure'):
            self.process()
        row = self.store.analysis(1)
        self.assertEqual(row['status'], 'error')
        self.assertEqual(row['ocr']['text'], 'completed partial OCR')
        self.assertIn('local_ocr_failed', row['evidence']['gaps'])
        self.assertNotIn('not persisted secret', json.dumps(row))
        self.vision.describe.assert_not_called()
        with self.assertRaises(ValueError): self.store.item(1, True)

    def test_vision_failure_preserves_ocr_and_paid_partial_vision(self):
        error = SafeError('private provider body')
        error.code = 'network_error'
        error.partial_result = self.visual
        self.vision.describe.side_effect = error
        with self.assertRaisesRegex(RuntimeError, 'network_error'): self.process()
        row = self.store.analysis(1)
        self.assertEqual(row['status'], 'error')
        self.assertEqual(row['ocr']['status'], 'success')
        self.assertEqual(row['vision']['frames'], self.visual['frames'])
        self.assertIn('semantic_visual_understanding_failed', row['evidence']['gaps'])
        self.assertNotIn('semantic_visual_understanding_not_requested', row['evidence']['gaps'])

    def test_changed_inspected_source_rejected_before_ocr(self):
        self.source.write_bytes(b'changed source')
        with self.assertRaises(ValueError): self.process()
        self.ocr.recognize.assert_not_called()
        self.assertEqual(self.store.results(), [])

    def test_modified_vision_derivative_is_recorded_error(self):
        def describe(frames):
            Path(frames[0]['path']).write_bytes(b'changed artifact')
            return self.visual
        self.vision.describe.side_effect = describe
        with self.assertRaises(RuntimeError): self.process()
        self.assertEqual(self.store.analysis(1)['status'], 'error')

    def test_evidence_limit_keeps_full_text_and_words_no_silent_truncation(self):
        self.ocr_result['text'] = 'x' * (ip.MAX_EVIDENCE_CHARS + 1)
        self.ocr_result['words'] *= 500
        aid = self.save()
        with self.assertRaisesRegex(ValueError, 'không cắt ngầm'): self.store.item(aid)
        row = self.store.analysis(aid)
        self.assertEqual(row['ocr']['text'], self.ocr_result['text'])
        self.assertEqual(row['ocr']['words'], self.ocr_result['words'])
        self.assertEqual(row['evidence']['ocr']['text'], self.ocr_result['text'])

    def test_failure_retry_success_cache_recheck_and_backend_separation(self):
        aid = self.save()
        self.judgment(aid, status='error')
        self.assertIsNotNone(ip.prepare_manifest(self.store, aid))
        self.judgment(aid)
        self.assertIsNone(ip.prepare_manifest(self.store, aid))
        self.assertIsNotNone(ip.prepare_manifest(self.store, aid, recheck=True))
        self.assertIsNotNone(ip.prepare_manifest(self.store, aid, model='other-model'))

    def test_manifest_source_hash_rubric_and_flags_are_validated(self):
        aid = self.save()
        manifest = ip.prepare_manifest(self.store, aid)
        self.assertEqual(ip.validate_manifest(self.store, manifest), self.store.item(aid))
        mutations = [lambda x: x['item']['state']['evidence']['ocr'].update(text='changed'),
            lambda x: x['item']['source'].update(source_path='changed'),
            lambda x: x['item'].update(evidence_sha256='changed'),
            lambda x: x.update(questions={}), lambda x: x.update(batch_id='not-uuid'),
            lambda x: x.update(allow_partial=1), lambda x: x.update(recheck='false'),
            lambda x: x['item'].update(image_analysis_id=True)]
        for mutate in mutations:
            changed = copy.deepcopy(manifest)
            mutate(changed)
            with self.assertRaises(ValueError): ip.validate_manifest(self.store, changed)
        with self.assertRaises(ValueError): ip.prepare_manifest(self.store, self.save('partial'))

    def test_judgment_validates_item_label_probability_and_status(self):
        aid = self.save()
        item = self.store.item(aid)
        kwargs = dict(batch_id=str(uuid.uuid4()), provider='test', model='fixture', backend_key='test',
                      status='success', label='other')
        changed = copy.deepcopy(item); changed['source']['image_sha256'] = 'fake'
        with self.assertRaises(ValueError): self.store.save_judgment(changed, **kwargs)
        for invalid in ({'label': 'bogus'}, {'status': 'error'}, {'probabilities': {'other': 1}},
                        {'probabilities': {k: float('nan') for k in ip.LABELS}}):
            with self.assertRaises(ValueError): self.store.save_judgment(item, **{**kwargs, **invalid})
        self.assertEqual(self.store.results(analysis_id=aid)[0]['classifications'], [])

    def test_message_validation_before_expensive_work_and_deletion_preserves_history(self):
        for invalid in (999, True, '1', -1):
            with self.assertRaises(ValueError): self.process(message_row_id=invalid)
        self.ocr.inspect.assert_not_called()
        self.db.save_payload({'backend': 'web', 'posts': [{'channel': 'news', 'message_id': 1, 'text': 'hello'}]})
        mid = self.db.conn.execute('SELECT id FROM messages').fetchone()[0]
        aid = self.save(message_id=mid)
        item = self.judgment(aid)
        with self.db.transaction(): self.db.conn.execute('DELETE FROM messages WHERE id=?', (mid,))
        result = self.store.results(analysis_id=aid)[0]
        self.assertIsNone(result['message_row_id'])
        self.assertEqual(result['classifications'][0]['source'], item['source'])
        self.assertEqual(self.db.conn.execute('PRAGMA foreign_key_check').fetchall(), [])
        with self.db.transaction(): self.db.conn.execute('DELETE FROM image_analyses WHERE id=?', (aid,))
        retained = self.db.conn.execute('SELECT * FROM image_classifications').fetchone()
        self.assertIsNone(retained['image_analysis_id'])
        self.assertEqual(json.loads(retained['evidence_json']), item['state']['evidence'])

    def test_cli_help_inspect_never_open_sqlite_or_require_tesseract(self):
        absent = self.root / 'absent.sqlite3'
        with patch.object(image, 'Database') as database, redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as exit_info: image.main(['--help'])
            self.assertEqual(exit_info.exception.code, 0)
            with patch('telegram_demo.image_ocr.LocalOCR') as ocr:
                ocr.return_value.inspect.return_value = self.media
                self.assertEqual(image.main(['--db', str(absent), 'inspect', str(self.source)]), 0)
                ocr.return_value.preflight.assert_not_called()
            database.assert_not_called()
        self.assertFalse(absent.exists())

    def test_cli_analyze_default_does_not_construct_vision_and_is_partial(self):
        with patch('telegram_demo.image_ocr.LocalOCR', return_value=self.ocr) as factory, \
             patch('telegram_demo.video_models.VisionClient') as vision, \
             patch.object(image, 'MEDIA_DIR', self.root / 'new-media'):
            rc, out, err = self.cli('analyze', self.source, '--ocr-language', 'vie+eng')
        self.assertEqual(rc, 0, err)
        self.assertIn('"classified": false', out)
        vision.assert_not_called()
        self.assertEqual(factory.call_args.kwargs['languages'], 'vie+eng')
        self.assertEqual(self.store.analysis(1)['status'], 'partial')

    def test_cli_vision_explicit_and_remote_consent_not_assumed(self):
        with patch('telegram_demo.image_ocr.LocalOCR', return_value=self.ocr), \
             patch('telegram_demo.video_models.VisionClient', return_value=self.vision) as client, \
             patch.object(image, 'MEDIA_DIR', self.root / 'new-media'):
            self.vision.provenance = {'provider': 'openai', 'model': 'local'}
            rc, _, err = self.cli('analyze', self.source, '--vision', '--vision-model', 'local')
        self.assertEqual(rc, 0, err)
        self.assertFalse(client.call_args.kwargs['allow_remote'])
        self.assertEqual(self.store.analysis(1)['status'], 'ready')

    def test_cli_missing_db_and_partial_dry_run(self):
        absent = self.root / 'missing.sqlite3'
        with redirect_stderr(io.StringIO()):
            self.assertEqual(image.main(['--db', str(absent), 'results']), 2)
        self.assertFalse(absent.exists())
        aid = self.process(vision=False)
        self.assertEqual(self.cli('classify', aid, '--dry-run')[0], 2)
        with patch.dict('os.environ', {'TYPESAFE_API_KEY': ''}):
            rc, out, err = self.cli('classify', aid, '--dry-run', '--allow-partial')
        self.assertEqual(rc, 0, err)
        self.assertIn('"network": false', out)
        self.assertEqual(self.store.results(analysis_id=aid)[0]['classifications'], [])

    def test_cli_jev_no_lock_cache_full_precision_and_error_no_fallback(self):
        aid = self.save()
        probs = {label: 0.025 for label in ip.LABELS}
        probs['other'] = 0.9
        answer = {'model': 'jev-fixture', 'answers': {'classification': {
            'choice': 'other', 'probabilities': probs}}}
        def ask(*args):
            self.assert_unlocked()
            return answer
        with patch('telegram_demo.jev_client.JevClient') as client:
            client.return_value.backend_key = 'test-direct'
            client.return_value.model = 'jev-latest'
            client.return_value.ask.side_effect = ask
            self.assertEqual(self.cli('classify', aid)[0], 0)
            self.assertEqual(self.cli('classify', aid)[0], 0)
            self.assertEqual(client.return_value.ask.call_count, 1)
            self.assertEqual(self.cli('classify', aid, '--recheck')[0], 0)
            self.assertEqual(client.return_value.ask.call_count, 2)
        result = self.store.results(analysis_id=aid)[0]['classifications'][0]
        self.assertEqual(result['probabilities'], probs)
        with patch('telegram_demo.jev_client.JevClient') as client:
            client.return_value.backend_key = 'failed-direct'
            client.return_value.model = 'jev-latest'
            client.return_value.ask.side_effect = JevError('auth', 'safe')
            self.assertEqual(self.cli('classify', aid)[0], 1)
        row = self.store.results(analysis_id=aid)[0]['classifications'][0]
        self.assertEqual(row['status'], 'error')
        self.assertIsNone(row['label'])
        self.assertEqual(row['error_code'], 'auth')

    def test_cli_tool_bridge_exclusive_export_import_provenance_and_cache(self):
        aid = self.save()
        request, result = self.root / 'request.json', self.root / 'result.json'
        self.assertEqual(self.cli('prepare', aid, '--out', request)[0], 0)
        self.assertEqual(request.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.cli('prepare', aid, '--out', request)[0], 2)
        manifest = json.loads(request.read_text())
        output = 'Jev jev-1.14.0\nclassification: other (confidence: 0.99)'
        response = {'format': ip.RESULT_FORMAT, 'batch_id': manifest['batch_id'],
            'image_analysis_id': aid, 'evidence_sha256': manifest['item']['evidence_sha256'],
            'tool_output': output}
        result.write_text(json.dumps({**response, 'image_analysis_id': True}))
        self.assertEqual(self.cli('apply', '--requests', request, '--results', result)[0], 2)
        result.write_text(json.dumps(response))
        self.assertEqual(self.cli('apply', '--requests', request, '--results', result)[0], 0)
        self.assertEqual(self.cli('apply', '--requests', request, '--results', result)[0], 0)
        row = self.store.results(analysis_id=aid)[0]['classifications'][0]
        self.assertEqual(row['provider'], 'dsh-jev-tool-import')
        self.assertEqual(row['response']['tool_output'], output)
        self.assertFalse(row['response']['authenticated_http'])
        self.assertIsNone(row['probabilities'])  # Do not promote rounded display probs.
        cached = self.root / 'cached.json'
        self.assertEqual(self.cli('prepare', aid, '--out', cached)[0], 0)
        self.assertFalse(cached.exists())
        self.assertEqual(self.cli('prepare', aid, '--out', cached, '--recheck')[0], 0)
        self.assertEqual(len(self.store.results(analysis_id=aid)[0]['classifications']), 1)

    def test_results_limit_and_ids_strict(self):
        for limit in (0, 1001, True, 1.5):
            with self.assertRaises(ValueError): self.store.results(limit=limit)
        for aid in (0, -1, True, '1'):
            with self.assertRaises(ValueError): self.store.analysis(aid)
        self.save()
        rc, output, err = self.cli('results', '--analysis-id', 1, '--limit', 1)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(output)[0]['id'], 1)


class ImageSchemaTests(unittest.TestCase):
    def test_additive_schema_manual_v3_fixture_preserves_existing_data(self):
        # Parent alone owns Database schema4 migration; this tests only our string.
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.executescript('''PRAGMA foreign_keys=ON; PRAGMA user_version=3;
                CREATE TABLE messages(id INTEGER PRIMARY KEY, text TEXT);
                INSERT INTO messages VALUES(1,'original Telegram text');''')
            before = conn.execute('SELECT * FROM messages').fetchall()
            conn.executescript(IMAGE_SCHEMA)
            conn.executescript(IMAGE_SCHEMA)  # Idempotent additive installation.
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertEqual(conn.execute('SELECT * FROM messages').fetchall(), before)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({'image_analyses', 'image_classifications'} <= tables)
            for table in ('image_analyses', 'image_classifications'):
                self.assertEqual(conn.execute(f'PRAGMA foreign_key_list({table})').fetchone()[6], 'SET NULL')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])


if __name__ == '__main__':
    unittest.main()
