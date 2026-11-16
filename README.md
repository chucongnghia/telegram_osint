# Telegram Demo — thu thập và sàng lọc dấu hiệu lừa đảo

Thu thập bài đăng Telegram, tìm username, xem hồ sơ và lưu lịch sử SQLite. Bộ chấm điểm
heuristic dùng chung cho backend **web công khai** và **Telegram API (Telethon)**;
phân loại văn bản bằng **Jev** là bước riêng, chỉ chạy khi bạn yêu cầu.

## Bắt đầu nhanh

Chạy từ thư mục gốc project với Python 3.10+. Web/demo và SQLite chỉ cần thư viện chuẩn.
Tám lệnh mẫu sau gồm cả thao tác offline lẫn thao tác mạng được ghi chú rõ:

```bash
# 1. Xem chín nhóm lệnh: web, api, groups, search, profile, db, classify, video, download
python3 main.py --help

# 2. Demo offline; kết quả mặc định vào json_folder/posts_web.json và SQLite
python3 main.py web --demo

# 3. Tìm username trong dữ liệu mẫu, không gọi Telegram
python3 main.py search --demo --keyword "đầu tư"

# 4. Xem thống kê DB cục bộ
python3 main.py db stats

# 5. Xem ứng viên Jev, KHÔNG gọi API có phí / không ghi kết quả phân loại
python3 main.py classify --db data/telegram.sqlite3 run --limit 10 --dry-run

# 6. Tra hồ sơ công khai qua web (có gọi mạng); không tải ảnh ở ví dụ này
python3 main.py profile durov --no-photo

# 7. Chỉ cần nếu dùng backend Telegram API
python3 -m venv .venv-api && .venv-api/bin/python -m pip install telethon

# 8. Đăng nhập Telegram API lần đầu (cần api_id/api_hash của bạn, hỏi SĐT + OTP)
.venv-api/bin/python main.py api --login
```

Dùng [điểm vào chung](<main.py>) với cú pháp `COMMAND --help` để xem cờ của từng lệnh.
Với `db`, `classify` và `video`, đặt `--db PATH` **sau tên lệnh, trước subcommand**.
Hướng dẫn đầy đủ, gồm cấu hình API, group kín, JSON, sao lưu và Jev:
**[docs/USAGE.md](<docs/USAGE.md>)**.
Video cục bộ (FFmpeg → Whisper/thị giác → Jev, tuỳ chọn, không tự tải URL):
**[hướng dẫn video](<docs/VIDEO.md>)**. Không cần API key để inspect/extract;
vision từ xa cần opt-in riêng, phân loại Jev là bước riêng có thể tính phí.

## Tải đúng một video Telegram

Copy **link tin nhắn có video** trong Telegram, rồi chạy (cần phiên đã đăng nhập):

```bash
.venv-api/bin/python main.py download "https://t.me/TEN_KENH/123"
# Chọn tài khoản khác hoặc thư mục đích riêng:
.venv-api/bin/python main.py download "https://t.me/c/1234567890/789" --session tk-phu --output-dir media/telegram_downloads
```

Mặc định lưu ở `media/telegram_downloads/` trong [media](<media/>), tên file do chương trình tạo.
Chỉ lấy message ID trong link, không quét các bài khác/album, không ghi đè file đã có,
không tự tham gia nhóm hay vượt bảo vệ nội dung. Không ghi DB/JSON và không gọi Jev.
Link nhóm riêng cần tài khoản có quyền xem và phiên nhận diện được nhóm đó;
link bình luận `?comment=...` không được hỗ trợ. Xem [chi tiết](<docs/VIDEO.md#tải-video-telegram-theo-link>).

## Bố cục

```text
.
├── main.py                       # dispatcher CLI duy nhất ở gốc
├── src/telegram_demo/
│   ├── cli/                      # 9 CLI; video cục bộ và download theo link
│   ├── analyzer.py               # heuristic và xuất báo cáo dùng chung
│   ├── storage.py                # SQLite, snapshot, truy vấn và sao lưu
│   ├── paths.py                  # các đường dẫn mặc định neo ở gốc project
│   ├── text_classifier.py        # rubric, cache và lịch sử Jev
│   ├── jev_client.py             # HTTP client TypeSafe
│   ├── video_media.py            # FFmpeg/ffprobe, JPEG/WAV có giới hạn
│   ├── video_models.py           # Whisper CPU/int8 và vision tuỳ chọn
│   ├── video_pipeline.py         # evidence, bridge Jev và lịch sử video
│   └── video_schema.py           # hai bảng video, schema v3 cộng thêm
├── config/channels.txt           # danh sách kênh cho --seed-file
├── config/requirements-video.txt  # faster-whisper ghim phiên bản, tuỳ chọn
├── docs/USAGE.md                  # tài liệu sử dụng chi tiết
├── docs/VIDEO.md                  # cài đặt, CLI và giới hạn video
├── tests/                        # kiểm thử offline
├── json_folder/                  # mọi JSON, kể cả tg_api.json nhạy cảm
├── data/                         # SQLite và bản sao lưu cục bộ
├── sessions/                     # phiên Telethon nhạy cảm
└── media/                        # profile_photos/ và artifacts video/<UUID>/
```

Các script cũ không còn ở gốc; mã triển khai nằm trong [gói nguồn](<src/telegram_demo/>).
Mặc định dữ liệu neo theo **gốc project**, không theo thư mục đang đứng. Đường dẫn tuỳ
chọn có thư mục vẫn tương đối với **cwd người gọi**; tên JSON trần được đặt trong
[json_folder](<json_folder/>). Danh sách kênh mẫu ở [channels.txt](<config/channels.txt>).

## Quyền riêng tư và giới hạn

- Điểm heuristic và nhãn Jev chỉ hỗ trợ rà soát, **không phải kết luận lừa đảo/pháp lý**;
  confidence không phải độ chính xác đã hiệu chuẩn.
- Thu thập không tự gọi Jev. API trực tiếp cần **TypeSafe API key riêng**, có thể tốn phí
  và gửi văn bản tin nhắn đến nhà cung cấp. Bridge công cụ DSH là nguồn kết quả khác,
  được lưu provenance riêng; không phải response HTTP có xác thực.
- [DB](<data/>) và [JSON](<json_folder/>) có thể chứa dữ liệu cá nhân, không mã hoá.
  Không chia sẻ [credential](<json_folder/tg_api.json>) hay [phiên Telethon](<sessions/>):
  ai có phiên có thể chiếm quyền tài khoản. Chỉ thu thập dữ liệu bạn được phép xử lý.
- **284 test** thành công trong venv API; Python hệ thống bỏ qua 1 test cần Telethon.
  Suite không gọi API có phí/tải model. Chất lượng phân loại video thực tế **chưa đo**;
  xem [kiểm thử](<docs/USAGE.md#chạy-test>) và [smoke video](<docs/VIDEO.md#6-kiểm-chứng-bản-triển-khai>).
