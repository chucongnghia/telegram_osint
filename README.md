# Telegram Demo — thu thập và sàng lọc dấu hiệu lừa đảo

Thu thập bài đăng Telegram, tìm username, xem hồ sơ và lưu lịch sử SQLite. Bộ chấm điểm
heuristic dùng chung cho backend **web công khai** và **Telegram API (Telethon)**;
phân loại văn bản bằng **Jev** chỉ chạy khi bạn bật `--jev` lúc thu thập hoặc gọi lệnh
`classify` riêng. Mặc định không gửi nội dung tới Jev.

## Dashboard trên trình duyệt

```bash
python3 main.py dashboard --open
python3 main.py dashboard --share           # chia sẻ trong WiFi/LAN kèm khóa truy cập
# http://127.0.0.1:8765
```

**Telegram OSINT System** dùng dữ liệu SQLite thật: xem tin/nguồn/hồ sơ, thu thập và watch, phân loại
Jev, ảnh/OCR, video và nhật ký tác vụ. Logo máy bay giấy riêng, không phải sản phẩm Telegram.
Mở trang chỉ đọc dữ liệu; tác vụ mạng/mô hình cần bạn chủ động xác nhận. Server chỉ
nghe trên máy cục bộ, không cần cài frontend dependencies.

Xem [hướng dẫn dashboard](<docs/DASHBOARD.md>), [thiết kế](<docs/DASHBOARD_DESIGN.md>)
và [báo cáo QA — Taste còn OPEN, không phải PASS](<docs/DASHBOARD_QA.md>).

## Bắt đầu nhanh

Chạy từ thư mục gốc project với Python 3.10+. Web/demo và SQLite chỉ cần thư viện chuẩn.
Tám lệnh mẫu sau gồm cả thao tác offline lẫn thao tác mạng được ghi chú rõ:

```bash
# 1. Xem 11 nhóm lệnh: dashboard, web, api, groups, search, profile, db, classify, image, video, download
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
.venv-api/bin/python main.py profile 93372553 --api --no-photo   # tra cả bằng ID số

# 7. Chỉ cần nếu dùng backend Telegram API
python3 -m venv .venv-api && .venv-api/bin/python -m pip install telethon

# 8. Đăng nhập Telegram API lần đầu (cần api_id/api_hash của bạn, hỏi SĐT + OTP)
.venv-api/bin/python main.py api --login
```

Dùng [điểm vào chung](<main.py>) với cú pháp `COMMAND --help` để xem cờ của từng lệnh.
Với `db`, `classify`, `image` và `video`, đặt `--db PATH` **sau tên lệnh, trước subcommand**.
Hướng dẫn đầy đủ, gồm cấu hình API, group kín, JSON, sao lưu và Jev:
**[docs/USAGE.md](<docs/USAGE.md>)**.
Video cục bộ (FFmpeg → Whisper/thị giác → Jev, tuỳ chọn, không tự tải URL):
**[hướng dẫn video](<docs/VIDEO.md>)**. Không cần API key để inspect/extract;
vision từ xa cần opt-in riêng, phân loại Jev là bước riêng có thể tính phí.

## Phân tích ảnh và OCR video

Tesseract đọc chữ Việt/Anh cục bộ; không cần Pillow/pytesseract và không tự tải weights.
OCR không thay việc hiểu cảnh. Model thị giác và Jev là các bước riêng có opt-in.

```bash
python3 main.py db --db data/image-check.sqlite3 init
python3 main.py image --db data/image-check.sqlite3 analyze media/anh-can-kiem-tra.png

# Video vừa tải: OCR frame + Whisper; chưa gọi Jev, chưa hiểu cảnh nếu skip-vision.
.venv-video/bin/python main.py video --db data/image-check.sqlite3 analyze \
  media/telegram_downloads/VIDEO_DA_TAI.mp4 --ocr --language auto --skip-vision
```

Dashboard có trang **Ảnh**, OCR mặc định bật trong form video, evidence và nút Jev riêng.
Model thị giác khai báo tại **Cài đặt → Kết nối mô hình**: key Gemini lưu file cục bộ 0600,
hoặc URL loopback cho model cục bộ (không cần key).
Xem [cài đặt, lệnh phân loại và giới hạn OCR](<docs/IMAGE_OCR.md>).

## Opt-in Jev ngay khi thu thập văn bản

`web`, `api`, `groups`, `search`, `profile` đều nhận `--jev`; cần SQLite, không dùng cùng
`--no-db`. Các cờ `--jev-*` chỉ hợp lệ khi có `--jev`. Chỉ bài đăng thuộc run vừa lưu
được xét, không quét backlog hay phân loại metadata hồ sơ/username. Các chế độ
`--report`, đăng nhập và setup từ chối `--jev`.

```bash
# Offline: chỉ lập kế hoạch; vẫn thu thập + lưu JSON/SQLite, không cần key:
# Mỗi lần chạy dùng tên xuất mới, tránh ghi đè fixture/kết quả cũ.
python3 main.py web --demo --jev --jev-include-demo --jev-dry-run \
    --out "jev_demo_plan_$(date +%s%N).json"

# Nhập key kín trong terminal, không dán key vào tài liệu/chat/source:
read -rsp 'TypeSafe API key: ' TYPESAFE_API_KEY; echo
export TYPESAFE_API_KEY
python3 main.py web --channel baomoi --channel tintucvtv --limit 20 \
    --jev --jev-limit 10 --out "jev_channels_$(date +%s%N).json"
unset TYPESAFE_API_KEY
```

`--jev-limit` mặc định **10**, tối đa **1000**: giới hạn **số lần thử HTTP của toàn lệnh**,
không phải mỗi kênh hay mỗi sự kiện watch, và **không phải ngân sách tiền**. Model chọn
bằng `--jev-model` (mặc định `JEV_MODEL` hoặc `jev-latest`); `--jev-timeout` mặc định 60
và tối đa 300 giây. Không tự nạp `.env`. Thiếu `TYPESAFE_API_KEY` sẽ lỗi trước thu thập,
trừ dry-run. API gọi tuần tự, không tự retry; lỗi API đầu tiên mở circuit đến hết lệnh,
giữ dữ liệu JSON/SQLite/heuristic và trả mã khác 0 khi kết thúc. Kết quả Jev chỉ nằm ở
`message_classifications`, không chèn vào JSON gốc hay điểm group. Cache bỏ qua kết quả
thành công cùng text/rubric/backend/model yêu cầu và tin quá dài đã có trạng thái terminal.
Watch dùng worker bất đồng bộ có hàng đợi hữu hạn, HTTP không chặn event loop; lưu/phân
loại tin mới tăng dần và drain worker khi dừng êm, không reset ngân sách giữa các sự kiện.

Xem [cờ, watch/group kín và cách xử lý phần còn lại bằng `classify run --run-id N`](<docs/USAGE.md#opt-in-jev-ngay-trong-lệnh-thu-thập>).

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
│   ├── cli/                      # 11 CLI, gồm image, dashboard và download theo link
│   ├── dashboard/                # HTML/CSS/JS, logo và font cục bộ
│   ├── dashboard_service.py      # HTTP loopback, đọc DB và quản lý tác vụ
│   ├── analyzer.py               # heuristic và xuất báo cáo dùng chung
│   ├── storage.py                # SQLite, snapshot, truy vấn và sao lưu
│   ├── paths.py                  # các đường dẫn mặc định neo ở gốc project
│   ├── text_classifier.py        # rubric, cache và lịch sử Jev
│   ├── jev_client.py             # HTTP client TypeSafe
│   ├── collection_jev.py         # Jev theo run, ngân sách chung và circuit lỗi
│   ├── video_media.py            # FFmpeg/ffprobe, JPEG/WAV có giới hạn
│   ├── video_models.py           # Whisper CPU/int8 và vision tuỳ chọn
│   ├── video_pipeline.py         # evidence, bridge Jev và lịch sử video
│   ├── video_schema.py           # hai bảng video, schema v3 cộng thêm
│   ├── image_ocr.py              # Tesseract cục bộ, chữ/box/hash có giới hạn
│   ├── image_pipeline.py         # ảnh/OCR/vision, evidence và Jev riêng
│   └── image_schema.py           # hai bảng ảnh, schema v4 cộng thêm
├── config/channels.txt           # danh sách kênh cho --seed-file
├── config/requirements-video.txt  # faster-whisper ghim phiên bản, tuỳ chọn
├── docs/USAGE.md                  # tài liệu sử dụng chi tiết
├── docs/VIDEO.md                  # cài đặt, CLI và giới hạn video
├── docs/DASHBOARD.md              # vận hành giao diện, quyền riêng tư và giới hạn
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
- Thu thập chỉ gọi Jev khi bật **`--jev`**; mặc định không gọi. API trực tiếp cần
  **TypeSafe API key riêng**, có thể tốn phí và gửi **chỉ văn bản** tới dịch vụ bên ngoài,
  không tự tải media/video hay gọi vision. Bridge công cụ DSH là nguồn kết quả khác,
  được lưu provenance riêng; không phải response HTTP có xác thực.
- [DB](<data/>) và [JSON](<json_folder/>) có thể chứa dữ liệu cá nhân, không mã hoá.
  Không chia sẻ [credential](<json_folder/tg_api.json>) hay [phiên Telethon](<sessions/>):
  ai có phiên có thể chiếm quyền tài khoản. Chỉ thu thập dữ liệu bạn được phép xử lý.
- **467 test** thành công trong venv API; Python hệ thống chạy cùng suite với 2 test
  cần Telethon được bỏ qua. Thêm 83 test cho OCR/ảnh/video/collector/dashboard;
  contract frontend 10 route được kiểm tra bằng Node VM.
  Suite không gọi API có phí/tải model; CLI → HTTP giả lập cục bộ → SQLite đã kiểm chứng,
  nhưng nhánh thu thập → Jev mới chưa thử với key thật.
  Chất lượng phân loại video thực tế **chưa đo**;
  xem [kiểm thử](<docs/USAGE.md#chạy-test>) và [smoke video](<docs/VIDEO.md#6-kiểm-chứng-bản-triển-khai>).
