"""Local image -> OCR (default) + explicit semantic vision -> separate Jev triage."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import uuid

from ..paths import MEDIA_DIR
from ..storage import Database, DEFAULT_DB
from ..text_classifier import LABELS, tool_backend
from ..image_pipeline import (ImageStore, process_image, prepare_manifest, validate_manifest,
                              QUESTIONS, RUBRIC_VERSION, RESULT_FORMAT)
from .classify_messages import read_json, write_new_json, parse_tool_output, positive


def finite_positive(value):
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 300:
        raise argparse.ArgumentTypeError('phải là số hữu hạn > 0 và <= 300')
    return number


def analyze(store, args):
    from ..image_ocr import LocalOCR
    ocr = LocalOCR(languages=args.ocr_language, timeout=args.timeout,
                   tesseract=args.tesseract, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)
    vision = None
    if args.vision:
        from ..video_models import VisionClient
        vision = VisionClient(provider=args.vision_provider, model=args.vision_model,
                              endpoint=args.vision_url, timeout=args.timeout,
                              allow_remote=args.allow_remote)
        vision.preflight()  # Config/consent failures precede OCR and paid calls.
    options = {'ocr_language': args.ocr_language, 'skip_ocr': args.skip_ocr,
               'vision_requested': args.vision,
               'vision': None if vision is None else vision.provenance}
    parent = MEDIA_DIR / 'images'
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    aid = process_image(store, args.file, parent / str(uuid.uuid4()), ocr=ocr, vision=vision,
                        skip_ocr=args.skip_ocr, message_row_id=args.message_id, options=options)
    row = store.analysis(aid)
    print(json.dumps({'image_analysis_id': aid, 'status': row['status'],
        'image_sha256': row['image_sha256'], 'artifact_dir': row['artifact_dir'],
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
        print('[i] Ảnh đã có nhãn thành công cho bộ tiêu chí/backend này; không gọi lại')
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
    print(f"[i] Ảnh #{args.analysis_id}, judgment #{jid}: {answer['choice']} — {LABELS[answer['choice']]}")
    return 0


def apply(store, args):
    request, response = read_json(args.requests), read_json(args.results)
    item = validate_manifest(store, request)
    if not isinstance(response, dict) or response.get('format') != RESULT_FORMAT:
        raise ValueError('Không đúng định dạng kết quả image Jev')
    if response.get('batch_id') != request['batch_id']:
        raise ValueError('Kết quả không thuộc batch này')
    if (type(response.get('image_analysis_id')) is not int or
            response['image_analysis_id'] != item['image_analysis_id'] or
            response.get('evidence_sha256') != item['evidence_sha256']):
        raise ValueError('Kết quả không khớp ID/hash bằng chứng ảnh')
    model, label = parse_tool_output(response.get('tool_output'))
    backend = tool_backend(request['model'])
    if not request['recheck'] and store.cached(item, backend):
        print('[i] Kết quả này đã có; không tạo bản trùng')
        return 0
    jid = store.save_judgment(item, batch_id=request['batch_id'], provider='dsh-jev-tool-import',
        model=model, backend_key=backend, status='success', label=label,
        response={'transport': 'operator-provided-jev-tool-output',
                  'authenticated_http': False, 'requested_model': request['model'],
                  'tool_output': response['tool_output']})
    print(f'[i] Image judgment #{jid}: {label} — {LABELS[label]}')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Phân tích ảnh cục bộ; OCR mặc định, vision và Jev chỉ khi yêu cầu.')
    parser.add_argument('--db', default=os.environ.get('TELEGRAM_DB_PATH') or str(DEFAULT_DB))
    subs = parser.add_subparsers(dest='command', required=True)
    for name in ('inspect', 'analyze'):
        sub = subs.add_parser(name, help='metadata, không ghi DB' if name == 'inspect' else
                              'OCR cục bộ; chỉ mô tả ngữ nghĩa khi --vision, chưa gọi Jev')
        sub.add_argument('file', help='file PNG/JPEG/WebP cục bộ, không URL hay ảnh động')
        sub.add_argument('--ocr-language', default='vie+eng')
        sub.add_argument('--tesseract', default=None)
        sub.add_argument('--ffmpeg', default=None)
        sub.add_argument('--ffprobe', default=None)
        sub.add_argument('--timeout', type=finite_positive, default=30)
        if name == 'analyze':
            sub.add_argument('--message-id', type=positive, help='row ID nội bộ SQLite của tin Telegram')
            sub.add_argument('--skip-ocr', action='store_true', help='bỏ OCR, trạng thái partial')
            sub.add_argument('--vision', action='store_true', help='bật mô tả ngữ nghĩa bằng vision API/model')
            sub.add_argument('--vision-provider', choices=('openai', 'gemini'), default='openai')
            sub.add_argument('--vision-model', default=os.environ.get('VISION_MODEL'))
            sub.add_argument('--vision-url', default=None, help='OpenAI-compatible endpoint, gồm model local')
            sub.add_argument('--allow-remote', action='store_true', help='đồng ý gửi ảnh JPEG thu nhỏ tới API HTTPS có thể tính phí')
    for name in ('classify', 'prepare'):
        sub = subs.add_parser(name, help='Jev HTTP' if name == 'classify' else 'xuất yêu cầu cho công cụ Jev DSH')
        sub.add_argument('analysis_id', type=positive)
        sub.add_argument('--model', default=os.environ.get('JEV_MODEL') or 'jev-latest')
        sub.add_argument('--allow-partial', action='store_true', help='chấp nhận OCR không hiểu toàn bộ ảnh/bước bỏ qua')
        sub.add_argument('--recheck', action='store_true')
        if name == 'classify':
            sub.add_argument('--dry-run', action='store_true')
            sub.add_argument('--timeout', type=finite_positive, default=60)
        else:
            sub.add_argument('--out', required=True, help='JSON mới; tên trần vào json_folder/, không ghi đè')
    sub = subs.add_parser('apply', help='nhập output Jev do người vận hành cung cấp, không xác thực HTTP')
    sub.add_argument('--requests', required=True)
    sub.add_argument('--results', required=True)
    sub = subs.add_parser('results', help='đọc toàn bộ bằng chứng và lịch sử offline')
    sub.add_argument('--analysis-id', type=positive)
    sub.add_argument('--limit', type=positive, default=20)
    args = parser.parse_args(argv)
    try:
        if args.command == 'inspect':
            from ..image_ocr import LocalOCR
            result = LocalOCR(languages=args.ocr_language, timeout=args.timeout,
                tesseract=args.tesseract, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe).inspect(args.file)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        # Help/inspect never open SQLite; all other operations require existing app DB.
        if not Path(args.db).expanduser().is_file():
            raise ValueError('Database chưa tồn tại; chạy main.py db --db PATH init trước')
        with Database(args.db) as db:
            store = ImageStore(db)
            if args.command == 'analyze':
                return analyze(store, args)
            if args.command == 'classify':
                return classify(store, args)
            if args.command == 'prepare':
                manifest = prepare_manifest(store, args.analysis_id, args.model,
                    allow_partial=args.allow_partial, recheck=args.recheck)
                if manifest is None:
                    print('[i] Ảnh đã có nhãn công cụ Jev cho bộ tiêu chí/backend này; không xuất lại')
                    return 0
                target = write_new_json(args.out, manifest)
                print(f'[i] Đã chuẩn bị {target}; chưa gọi Jev, chưa có nhãn')
                return 0
            if args.command == 'apply':
                return apply(store, args)
            print(json.dumps(store.results(args.limit, args.analysis_id), ensure_ascii=False, indent=2))
    except (OSError, ValueError, TypeError, RuntimeError, sqlite3.Error) as exc:
        print(f'[!] Image: {exc}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('[!] Đã dừng; giữ artifacts và kết quả đã commit, không tự gán nhãn', file=sys.stderr)
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
