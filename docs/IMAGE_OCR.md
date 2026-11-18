# Ảnh, OCR và chữ trong video

OCR đọc chữ, không hiểu trọn vẹn bối cảnh hình ảnh. Project tách ba bước:

1. **OCR cục bộ:** Tesseract đọc chữ tiếng Việt/Anh, giữ văn bản thô, từng từ, vị trí và điểm engine.
2. **Thị giác ngữ nghĩa, tuỳ chọn:** Gemini hoặc API OpenAI-compatible/local Qwen-VL mô tả cảnh. Phải cấu hình model; API từ xa cần đồng ý gửi ảnh riêng.
3. **Jev, tuỳ chọn:** phân loại evidence văn bản thành năm nhãn. Không trực tiếp nhận file ảnh/MP4 và không tự chạy chỉ vì bạn bật OCR.

Không có chữ, OCR rỗng hoặc `partial` **không có nghĩa nội dung an toàn**. Điểm Tesseract 0–100 không phải xác suất chính xác hay điểm rủi ro.

## Cài runtime

CLI/SQLite không thêm thư viện pip cho OCR. Cần `tesseract`, bộ ngôn ngữ `vie`/`eng`, `ffmpeg` và `ffprobe` trên PATH. Kiểm tra:

```bash
tesseract --version
tesseract --list-langs
ffmpeg -version
ffprobe -version
```

Trên Debian/Kali có quyền quản trị, có thể cài bằng package manager:

```bash
sudo apt install tesseract-ocr tesseract-ocr-vie tesseract-ocr-eng ffmpeg
```

Máy đang triển khai đã có Tesseract **5.5.0**, Leptonica **1.86.0**, `vie`/`eng`/`osd`, cài trong thư mục riêng của người dùng. Wrapper `~/.local/bin/tesseract` chỉ đặt thư viện/traineddata cho tiến trình OCR; không sửa thư viện hệ thống và không cần sudo. Máy khác cần tự cài runtime, không có binary được nhúng trong Git. Có thể chỉ định executable bằng `TESSERACT_COMMAND` hoặc `--tesseract`.

Không dùng Pillow/pytesseract/EasyOCR, không tự đổi engine hoặc tải weights. Whisper vẫn là dependency video tuỳ chọn; xem [VIDEO](<VIDEO.md>).

## Phân tích một ảnh từ đường dẫn

Đặt `--db` sau `image`, trước subcommand. Chạy từ gốc project; thay đường dẫn ảnh theo máy của bạn:

```bash
# Tạo DB ứng dụng riêng nếu chưa có. Không dùng file session Telethon làm DB.
python3 main.py db --db data/image-check.sqlite3 init

# Xem metadata/hash, không ghi DB, không gọi OCR/Jev/vision.
python3 main.py image inspect media/anh-can-kiem-tra.png

# OCR mặc định bật, ngôn ngữ mặc định vie+eng; chưa gọi Jev hoặc model thị giác.
python3 main.py image --db data/image-check.sqlite3 analyze media/anh-can-kiem-tra.png

# Xem evidence, raw OCR và lịch sử; ID là image_analysis_id vừa in ra.
python3 main.py image --db data/image-check.sqlite3 results --analysis-id 1
```

Đường dẫn Windows `\\wsl.localhost\kali-linux\home\kali\recon\demo\media\anh.png` tương ứng `/home/kali/recon/demo/media/anh.png` khi chạy terminal Kali.

Nếu chỉ OCR, trạng thái là `partial` với gap `semantic_visual_understanding_not_requested`. Original được sao chép riêng tư, có SHA-256 và giữ nguyên; JPEG thu nhỏ cho vision là derivative riêng, không thay original. Dữ liệu phân tích ở `image_analyses`, kết quả Jev ở `image_classifications`.

### Gọi Jev sau OCR

Nhập key trong terminal hoặc dùng ô key trong dashboard (lưu file cục bộ 0600); không dán key vào source/chat:

```bash
read -rsp 'TypeSafe API key: ' TYPESAFE_API_KEY; echo
export TYPESAFE_API_KEY

# Chấp nhận rõ rằng OCR chưa hiểu toàn bộ hình ảnh; thay 1 bằng ID thực tế.
python3 main.py image --db data/image-check.sqlite3 classify 1 --allow-partial
unset TYPESAFE_API_KEY
```

Đây là request có thể tính phí và gửi evidence văn bản. Lỗi key/mạng được lưu `error`, nhãn NULL, không đổi sang heuristic. `--dry-run` không gọi Jev. Cache bỏ qua cùng evidence/rubric/backend; `--recheck` chủ động gọi lại và lưu lịch sử mới.

Bridge DSH cũng có `image prepare ... --out TEN_MOI.json` / `image apply --requests ... --results ...`. Chỉ nhập output công cụ có thật; provenance operator-import khác HTTP có xác thực. Key plugin DSH khác key được export trong terminal. Không suy ra key hợp lệ từ việc chỉ lưu key vào ô cài đặt.

### Bật thị giác khi đã có model

Ví dụ model OpenAI-compatible **đã chạy cục bộ** (không tự cài hoặc tải model):

```bash
python3 main.py image --db data/image-check.sqlite3 analyze media/anh-can-kiem-tra.png \
  --vision --vision-model TEN_MODEL_THUC_TE \
  --vision-url http://127.0.0.1:8000/v1/chat/completions
```

Với Gemini cần `GEMINI_API_KEY`, `--vision-provider gemini --vision-model TEN_MODEL_THUC_TE --allow-remote`. API OpenAI-compatible từ xa dùng `VISION_API_KEY`, endpoint HTTPS và `--allow-remote`. Chỉ cấp key cho endpoint tin cậy. Không coi `--vision` là đồng ý upload từ xa ngầm. CLI cho phép `--skip-ocr`, nhưng bước bỏ qua vẫn được ghi gap và cần `--allow-partial` khi phân loại.

## Đọc chữ trong video

```bash
python3 main.py db --db data/video-check.sqlite3 init

# Venv video có faster-whisper; local OCR không cần pip package OCR.
.venv-video/bin/python main.py video --db data/video-check.sqlite3 analyze \
  media/telegram_downloads/VIDEO_DA_TAI.mp4 \
  --ocr --ocr-language vie+eng --language auto --skip-vision

# Thay 1 bằng video_analysis_id vừa in ra; chỉ khi bạn chấp nhận thiếu hiểu cảnh.
python3 main.py video --db data/video-check.sqlite3 classify 1 --allow-partial
```

OCR video CLI **opt-in `--ocr`** để không thay chi phí/thời gian lệnh cũ. Dashboard bật checkbox OCR mặc định. Mỗi frame có timestamp, SHA-256 và raw OCR. Từ/box nằm trong `video_analyses.media_json.local_ocr`; evidence gửi Jev chỉ giữ text/provenance/timestamp/hash, không gửi hàng nghìn box. `--language auto` là ngôn ngữ lời nói Whisper, không phải bộ ngôn ngữ OCR (`vie+eng`).

Chọn frame là lấy mẫu, không xem từng khung hình. Chữ nhỏ/mờ/xiên hoặc xuất hiện giữa các mốc có thể bị bỏ sót. `--skip-vision` vẫn ghi gap; Tesseract không thay mô tả cảnh. Không có audio là trạng thái hợp lệ, không giả transcript. Lỗi OCR/ASR/vision giữ phần evidence đã hoàn tất và không tự gán nhãn.

## OCR khi thu thập Telegram

```bash
.venv-api/bin/python main.py api --channel TEN_KENH --limit 10 \
  --media-dir media/collected --ocr
```

Cần phiên đã đăng nhập/quyền truy cập. `--ocr` phải đi cùng `--media-dir` và nguồn thu thập thật; report/demo/login không chạy OCR. Preflight kiểm tra binary/ngôn ngữ trước thu thập. Batch offload OCR ra thread; watch dùng tối đa hai OCR đồng thời để không chặn event loop. Raw `ocr_text`/provenance được giữ riêng với text gốc; heuristic dùng thêm OCR. Empty OCR là thành công rỗng, khác lỗi/chưa chạy. Không tự gửi ảnh-only/OCR sang Jev text collector: luồng phân loại ảnh/evidence là lệnh `image` riêng.

## Dashboard

Chạy `python3 main.py dashboard --open`, chọn đúng DB trên thanh đầu trang:

- **Ảnh:** chọn file PNG/JPEG/WebP đã có trong `media`, chạy OCR, xem original và evidence; Jev là nút riêng có xác nhận key/partial.
- **Video:** bật OCR frame, chọn `vie`/`eng`/`vie+eng`; ngôn ngữ lời nói có `auto`.
- **Cài đặt:** panel "Kết nối mô hình" có ô nhập key Gemini (và provider OpenAI-compatible), xem bên dưới.
- Điều hướng chỉ đọc DB; tác vụ OCR/video cần xác nhận chạy, không tự scan toàn ổ đĩa.

### Nhập key Gemini ngay trong dashboard

Panel **Cài đặt → Kết nối mô hình**: chọn provider `gemini`, nhập model (ví dụ `gemini-2.5-flash`) và key, bấm **Lưu cấu hình**. Với model cục bộ kiểu Qwen-VL chọn `openai`, nhập model và URL loopback (ví dụ `http://127.0.0.1:11434/v1/chat/completions`) — không cần key. Nút **Xoá cấu hình** quên cấu hình đang giữ.

- Cấu hình được lưu vào **`json_folder/dashboard_secrets.json`** (quyền 0600, git-ignored) để mở lại dashboard không phải nhập key. File là **plaintext, không mã hoá** — cùng mức bảo vệ như `json_folder/tg_api.json`; ai đọc được tài khoản máy này thì đọc được key.
- Model từ xa (Gemini hoặc endpoint HTTPS) **bắt buộc tick "cho phép gửi ảnh ra ngoài"** ở từng lần chạy; thiếu consent server trả `consent_required` trước khi gọi mạng.
- Lưu thành công **không chứng minh key hợp lệ**: dashboard chỉ kiểm tra định dạng cấu hình. Sai key sẽ hiện lỗi trong log job và lưu `status=error`, nhãn NULL.
- Ô key để trống nghĩa là **giữ key đang có** (file đã lưu hoặc biến môi trường); muốn xoá hẳn dùng **Xoá cấu hình thị giác** (xoá cả phần trong file).
- Nếu server đã có env `VISION_MODEL`, `VISION_API_URL`, `GEMINI_API_KEY`/`VISION_API_KEY` thì panel hiện trạng thái đó và vẫn cho ghi đè bằng file cục bộ.
- Khởi động lại dashboard bằng env vẫn dùng được nếu không muốn nhập trong UI.

UI không nhận đường dẫn tuỳ ý ngoài `media`, không upload file từ trình duyệt, không tự tải model hoặc cấp quyền API thị giác từ xa. Xem [dashboard](<DASHBOARD.md>).

## Giới hạn và lưu trữ

- Ảnh tĩnh PNG/JPEG/WebP, tối đa 20 MiB/20 megapixel; từ chối ảnh động, symlink, URL, PDF, GIF/BMP/TIFF. Xác minh hash và source không đổi; EXIF rotation được chuẩn hoá.
- OCR: mặc định timeout 30 giây, tối đa 32.000 ký tự/4.000 từ mỗi ảnh; vượt ngưỡng là lỗi, không cắt ngầm. Video tối đa 60 frame/48.000 ký tự OCR gộp.
- Word boxes nằm trong toạ độ ảnh **đã tiền xử lý**, không mặc nhiên trùng pixel original. Engine confidence là số chưa hiệu chuẩn.
- SQLite schema **v4** thêm hai bảng ảnh; migration v1/v2/v3 cộng thêm, không tính lại scores/labels/evidence lịch sử. DB production được backup trước nâng cấp; 14 bảng cũ được đối chiếu số dòng + SHA-256 toàn bộ giá trị, không đổi.
- Rubric video mới có local OCR; manifest video cũ cần prepare lại theo phiên bản hiện hành, không sửa evidence cũ. JSON output mới vẫn nằm `json_folder/` và không ghi đè.
- DB/artifacts chứa dữ liệu nhạy cảm, không mã hoá; chỉ xử lý media bạn được phép dùng. OCR sai chữ/dấu là bình thường; không dùng một nhãn làm kết luận pháp lý.

## Kiểm chứng đã chạy

- Tesseract thật trên ảnh Việt giả lập 1400×700: đọc 28 từ, nhận `OCR-2026`, `30%`, câu tiếng Việt; **một số dấu bị sai**, không phải phép đo độ chính xác.
- Video Telegram đã tải dài 22,13 giây: FFmpeg 5 frame (0/5/10/15/20 giây), Tesseract thật đọc được một phần `A sky ladder ... 60-storey building` và `CHINADAILY`; nhiều chữ nhiễu. Whisper thật trả `Thank you.`. OCR thành công nhưng analysis **partial** vì chưa có model hiểu cảnh.
- Bằng chứng thật ở DB riêng [ocr-verification.sqlite3](<../data/ocr-verification.sqlite3>), không chèn dữ liệu test vào DB production. Không tạo nhãn Jev giả; không gọi lại plugin đang lỗi 401. Vision API/Gemini chưa chạy thật do chưa có cấu hình/key.
- Dashboard chạy thật qua worker CLI: `image_analyze` trả `succeeded`/exit 0, OCR Tesseract thật và `/api/images` đọc được text; `video_analyze` (OCR + Whisper auto, skip vision) cũng `succeeded`/exit 0 và lưu `partial`. Lần chạy đầu phát hiện lệch cờ `--ocr-timeout` (CLI chỉ có `--timeout`), đã sửa và thêm test đưa argv dashboard qua parser `image` thật.
- Cấu hình vision qua HTTP: provider OpenAI-compatible loopback (fixture `127.0.0.1:18765`) → `image_analyze` với `vision: true` trả `succeeded`, analysis `ready`, có `description`/`ocr_text`; **không cần key**. Lưu Gemini (key giả) → 200, `vision_remote true`; job thiếu consent bị chặn `409 consent_required`; `clear_vision` xoá cấu hình. Không có request nào tới Google, không dùng key thật.
- Toàn bộ suite: **452 test** đạt trong venv API; Python hệ thống bỏ qua 1 test Telethon. Test gồm mock subprocess/HTTP, migration cũ, frame/hash/EXIF, giới hạn, lỗi giữ evidence, batch prevalidation, collector nonblocking và dashboard. Node kiểm tra 10 route/forms/escaping/consent; không đo chất lượng OCR/ASR/vision/Jev.
