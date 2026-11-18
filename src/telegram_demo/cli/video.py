"""Local video -> FFmpeg -> Whisper + visual observations -> Jev -> SQLite."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

from ..paths import MEDIA_DIR, PROJECT_ROOT
from ..storage import Database, DEFAULT_DB
from ..text_classifier import LABELS, tool_backend
from ..video_pipeline import (VideoStore, process_video, prepare_manifest, validate_manifest,
                              QUESTIONS, RUBRIC_VERSION, RESULT_FORMAT)
from .classify_messages import read_json, write_new_json, parse_tool_output, positive


def finite_positive(value):
    import math
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('phải là số hữu hạn > 0')
    return number


def analyze(store, args):
    from ..video_media import FFmpegMedia
    from ..video_models import WhisperTranscriber, VisionClient
    media = FFmpegMedia(ffmpeg=args.ffmpeg, ffprobe=args.ffprobe, timeout=args.timeout)
    whisper = vision = ocr = None
    extract_only = args.command == 'extract'
    if not extract_only:
        # All opt-in/config checks occur before any paid call or expensive ASR.
        if not args.skip_vision:
            vision = VisionClient(provider=args.vision_provider, model=args.vision_model,
                                  endpoint=args.vision_url, timeout=args.timeout,
                                  allow_remote=args.allow_remote)
            vision.preflight()
        if getattr(args, 'ocr', False):
            from ..image_ocr import LocalOCR
            ocr = LocalOCR(languages=args.ocr_language, timeout=args.ocr_timeout,
                           tesseract=args.tesseract, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)
            ocr.preflight()
        if not args.skip_transcript:
            whisper = WhisperTranscriber(model=args.whisper_model,
                                          language=None if args.language == 'auto' else args.language,
                                         download_root=str(PROJECT_ROOT / '.cache' / 'whisper'),
                                         allow_download=args.allow_model_download, threads=args.threads)
            whisper.preflight()
    options = {
        'interval': args.interval, 'max_frames': args.max_frames,
        'max_duration': args.max_duration, 'extract_only': extract_only,
        'local_ocr': None if ocr is None else {'provider': 'tesseract', 'languages': args.ocr_language},
        'vision': None if vision is None else vision.provenance,
        'whisper': None if whisper is None else {'model': args.whisper_model, 'language': args.language,
                                                'compute_type': 'int8', 'device': 'cpu'},
    }
    artifact_parent = MEDIA_DIR / 'video'
    artifact_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    artifact_dir = artifact_parent / str(uuid.uuid4())
    aid = process_video(store, args.file, artifact_dir, media=media, whisper=whisper, vision=vision, ocr=ocr,
                        extract_only=extract_only, interval=args.interval, max_frames=args.max_frames,
                        max_duration=args.max_duration, message_row_id=args.message_id, options=options)
    row = store.analysis(aid)
    print(json.dumps({'video_analysis_id': aid, 'status': row['status'],
                      'video_sha256': row['video_sha256'], 'artifact_dir': row['artifact_dir'],
                      'frames': len(row['media']['frames']), 'has_audio': row['media']['media']['has_audio'],
                      'gaps': row['evidence']['gaps'], 'classified': False}, ensure_ascii=False, indent=2))
    return 0


def classify(store, args):
    from ..jev_client import JevClient, JevError
    item = store.item(args.analysis_id, args.allow_partial)
    client = JevClient(model=args.model, timeout=args.timeout)
    if args.dry_run:
        print(json.dumps({'model': args.model, 'rubric_version': RUBRIC_VERSION,
                          'item': item, 'network': False}, ensure_ascii=False, indent=2))
        return 0
    if not args.recheck and store.cached(item, client.backend_key):
        print('[i] Video đã có nhãn thành công cho bộ tiêu chí/backend này; không gọi lại')
        return 0
    batch = str(uuid.uuid4())
    try:
        response = client.ask(item['state'], QUESTIONS)
    except JevError as exc:
        store.save_judgment(item, batch_id=batch, provider='typesafe-jev', model=client.model,
                            backend_key=client.backend_key, status='error', error_code=exc.code)
        print(f'[!] Jev {exc.code}; đã lưu lỗi, không có nhãn dự phòng', file=sys.stderr)
        return 1
    answer = response['answers']['classification']
    jid = store.save_judgment(item, batch_id=batch, provider='typesafe-jev', model=response['model'],
                              backend_key=client.backend_key, status='success', label=answer['choice'],
                              probabilities=answer['probabilities'],
                              response={'requested_model': client.model, 'response': response})
    print(f"[i] Video #{args.analysis_id}, judgment #{jid}: {answer['choice']} — {LABELS[answer['choice']]}")
    return 0


def apply(store, args):
    request, response = read_json(args.requests), read_json(args.results)
    item = validate_manifest(store, request)
    if not isinstance(response, dict) or response.get('format') != RESULT_FORMAT:
        raise ValueError('Không đúng định dạng kết quả video Jev')
    if response.get('batch_id') != request['batch_id']:
        raise ValueError('Kết quả không thuộc batch này')
    if (type(response.get('video_analysis_id')) is not int or
            response['video_analysis_id'] != item['video_analysis_id'] or
            response.get('evidence_sha256') != item['evidence_sha256']):
        raise ValueError('Kết quả không khớp ID/hash bằng chứng video')
    model, label = parse_tool_output(response.get('tool_output'))
    backend = tool_backend(request['model'])
    if not request['recheck'] and store.cached(item, backend):
        print('[i] Kết quả này đã có; không tạo bản trùng')
        return 0
    jid = store.save_judgment(item, batch_id=request['batch_id'], provider='dsh-jev-tool-import',
                              model=model, backend_key=backend, status='success', label=label,
                              response={'transport': 'operator-provided-jev-tool-output',
                                        'requested_model': request['model'],
                                        'tool_output': response['tool_output']})
    print(f'[i] Video judgment #{jid}: {label} — {LABELS[label]}')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Phân tích video cục bộ; không tự tải video/link Telegram.')
    parser.add_argument('--db', default=os.environ.get('TELEGRAM_DB_PATH') or str(DEFAULT_DB))
    subs = parser.add_subparsers(dest='command', required=True)
    for name in ('inspect', 'extract', 'analyze'):
        sub = subs.add_parser(name, help={'inspect': 'xem metadata, không ghi DB',
                              'extract': 'chỉ tách audio/khung hình, không model',
                              'analyze': 'FFmpeg + Whisper + model thị giác; chưa gọi Jev'}[name])
        sub.add_argument('file', help='file video cục bộ, không URL/playlist')
        sub.add_argument('--ffmpeg', default=None)
        sub.add_argument('--ffprobe', default=None)
        sub.add_argument('--timeout', type=finite_positive, default=120, help='timeout từng bước FFmpeg/vision, giây')
        sub.add_argument('--max-duration', type=finite_positive, default=600, help='giới hạn từ chối video dài, không cắt ngầm')
        if name != 'inspect':
            sub.add_argument('--interval', type=finite_positive, default=5)
            sub.add_argument('--max-frames', type=positive, default=12, help='1..60, lấy mẫu trải đều toàn video')
            sub.add_argument('--message-id', type=positive, help='row ID nội bộ SQLite của tin Telegram, tùy chọn')
        if name == 'analyze':
            sub.add_argument('--skip-transcript', action='store_true')
            sub.add_argument('--skip-vision', action='store_true')
            sub.add_argument('--whisper-model', default='tiny', help='tên model multilingual hoặc đường dẫn local')
            sub.add_argument('--language', default='vi', help='mã ngôn ngữ Whisper; auto tự nhận dạng')
            sub.add_argument('--ocr', action='store_true', help='đọc chữ trong khung hình bằng Tesseract cục bộ')
            sub.add_argument('--ocr-language', default='vie+eng', help='ngôn ngữ Tesseract, ví dụ vie+eng')
            sub.add_argument('--ocr-timeout', type=finite_positive, default=30)
            sub.add_argument('--tesseract', default=None, help='binary Tesseract; mặc định TESSERACT_COMMAND hoặc PATH')
            sub.add_argument('--threads', type=positive, default=2)
            sub.add_argument('--allow-model-download', action='store_true', help='cho phép tải weights Whisper lần đầu')
            sub.add_argument('--vision-provider', choices=('openai', 'gemini'), default='openai')
            sub.add_argument('--vision-model', default=os.environ.get('VISION_MODEL'))
            sub.add_argument('--vision-url', default=None, help='OpenAI-compatible /v1/chat/completions; hỗ trợ Qwen-VL local')
            sub.add_argument('--allow-remote', action='store_true', help='đồng ý gửi khung hình đến API thị giác HTTPS có thể tính phí')
    for name in ('classify', 'prepare'):
        sub = subs.add_parser(name, help='Jev HTTP' if name == 'classify' else 'xuất bằng chứng cho công cụ Jev của DSH')
        sub.add_argument('analysis_id', type=positive)
        sub.add_argument('--model', default=os.environ.get('JEV_MODEL') or 'jev-latest')
        sub.add_argument('--allow-partial', action='store_true', help='chấp nhận thiếu bước đã khai báo trong gaps')
        sub.add_argument('--recheck', action='store_true')
        if name == 'classify':
            sub.add_argument('--dry-run', action='store_true')
            sub.add_argument('--timeout', type=finite_positive, default=60)
        else:
            sub.add_argument('--out', required=True, help='JSON mới, tên trần vào json_folder/')
    sub = subs.add_parser('apply', help='nhập kết quả Jev do người vận hành cung cấp; không gọi API')
    sub.add_argument('--requests', required=True)
    sub.add_argument('--results', required=True)
    sub = subs.add_parser('results', help='đọc bằng chứng và lịch sử offline')
    sub.add_argument('--analysis-id', type=positive)
    sub.add_argument('--limit', type=positive, default=20)
    args = parser.parse_args(argv)
    try:
        if args.command == 'inspect':
            from ..video_media import FFmpegMedia
            result = FFmpegMedia(args.ffmpeg, args.ffprobe, args.timeout).inspect(
                args.file, max_duration=args.max_duration)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        # DB must already be an application database; no accidental typo-created DB.
        if not Path(args.db).expanduser().is_file():
            raise ValueError('Database chưa tồn tại; chạy main.py db --db PATH init trước')
        with Database(args.db) as db:
            store = VideoStore(db)
            if args.command in ('extract', 'analyze'):
                return analyze(store, args)
            if args.command == 'classify':
                return classify(store, args)
            if args.command == 'prepare':
                manifest = prepare_manifest(store, args.analysis_id, args.model,
                                            allow_partial=args.allow_partial, recheck=args.recheck)
                if manifest is None:
                    print('[i] Video đã có nhãn công cụ Jev cho bộ tiêu chí/backend này; không xuất request gọi lại')
                    return 0
                target = write_new_json(args.out, manifest)
                print(f'[i] Đã chuẩn bị {target}; chưa gọi Jev, chưa có nhãn')
                return 0
            if args.command == 'apply':
                return apply(store, args)
            print(json.dumps(store.results(args.limit, args.analysis_id), ensure_ascii=False, indent=2))
    except (OSError, ValueError, TypeError, RuntimeError, sqlite3.Error) as exc:
        # Adapter exceptions are intentionally sanitized before reaching here.
        print(f'[!] Video: {exc}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('[!] Đã dừng; giữ artifacts và kết quả đã commit, không tự gán nhãn', file=sys.stderr)
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
