# Video cục bộ → bằng chứng → Jev → SQLite

Lệnh `video` nhận **file video có sẵn trên máy**, không tải URL/video Telegram,
không tự thu thập và không tự gọi API có phí khi chạy các lệnh thu thập khác.
Pipeline: FFmpeg tách audio/khung hình → Whisper phiên âm + Tesseract OCR cục bộ
(`--ocr`) + model thị giác tuỳ chọn → Jev phân loại evidence văn bản → SQLite.
OCR không thay hiểu cảnh; xem [ảnh/OCR, cài runtime và smoke thật](<IMAGE_OCR.md>).
`analyze` **chưa gọi Jev**; phân loại văn bản Telegram vẫn là lệnh `classify` riêng.

## Tải video Telegram theo link

Lệnh `video` vẫn chỉ xử lý file cục bộ. Dùng lệnh **`download`** riêng để tải đúng
một video từ link bài đăng, không lấy các tin khác:

```bash
# Dùng phiên Telegram hiện có; không cần Jev key/Gemini key/FFmpeg/Whisper để tải:
.venv-api/bin/python main.py download "https://t.me/TEN_KENH/123"
# Nhóm riêng hoặc topic (tài khoản phải có quyền xem):
.venv-api/bin/python main.py download "https://t.me/c/1234567890/789" --session tk-phu
.venv-api/bin/python main.py download "https://t.me/TEN_NHOM/456/789" --output-dir media/my_videos
```

- Copy **link tin nhắn video**, không dùng link trang cá nhân/kênh, link mời hoặc URL
  video ở trang web khác. Hỗ trợ `t.me`, `telegram.me`, link `/s/kenh/id`, link
  `/c/channel_id/id` và topic `/c/channel_id/topic_id/id` hoặc `/kenh/topic_id/id`.
- Chỉ lấy **message ID cuối cùng**, không lấy toàn album hoặc quét lịch sử. `?single`
  và `?t=...` được chấp nhận; `?comment=...`/`?thread=...` và tham số khác bị từ chối
  để tránh tải nhầm bài gốc thay vì bình luận. Ảnh, audio, text, link preview không
  phải video trực tiếp sẽ báo lỗi, không tải file thay thế.
- Reuse Telegram `api_id/api_hash` từ môi trường/cấu hình hiện có và phiên trong
  [sessions](<../sessions/>). Nếu chưa đăng nhập, chủ động chạy
  `.venv-api/bin/python main.py api --login` (thêm cùng `--session` nếu cần). Lệnh tải
  không hỏi OTP, tự tạo phiên mới, tham gia nhóm, hoặc vượt bảo vệ nội dung.
- Link riêng chỉ dùng được nếu tài khoản có quyền xem và session có thông tin nhóm;
  nếu không, kiểm tra đúng tài khoản/phiên và quyền truy cập. Không bypass restricted media.
- Mặc định `media/telegram_downloads/` neo theo gốc project. `--output-dir` tuỳ chỉnh
  tương đối với cwd của bạn. Tên file dạng `telegram_<peer>_<message_id>.mp4` (đuôi
  video thực tế có thể khác), không dùng tên file tuỳ ý từ Telegram.
- File mới quyền `0600`, không ghi đè file cũ/symlink. Khi tải lỗi, hủy hoặc sai kích
  thước, chỉ dọn file chưa hoàn tất của lượt này. Muốn tải lại file đã có, chọn thư mục
  khác; không tự xoá file người dùng.
- Chỉ tải file: **không** thêm run/analysis vào SQLite, không ghi JSON và không tự gọi
  Whisper/vision/Jev. Lệnh in đường dẫn video; dùng đường dẫn đó với `video analyze`
  theo các mục bên dưới nếu muốn phân tích sau.
- Các ca parser, tải stream, bảo vệ nội dung, chống ghi đè, cleanup và CLI được kiểm
  thử offline; chưa tải video Telegram thật trong lần thêm tính năng vì chưa có link
  cụ thể từ người dùng.

## 1. Cài thành phần tuỳ chọn

CLI cơ bản vẫn chỉ dùng thư viện chuẩn; `--help`, `inspect`, `extract`, `results`
không cần faster-whisper hay API key. `inspect` cần ffprobe; `extract`/`analyze`
cần cả ffprobe và FFmpeg. Người vận hành tự cài, ví dụ Debian/Kali/Ubuntu:

```bash
sudo apt update
sudo apt install ffmpeg python3-venv
ffmpeg -version
ffprobe -version
```

Có thể dùng binary tin cậy riêng, tìm từ [trang tải FFmpeg](https://ffmpeg.org/download.html).
Ưu tiên chọn binary: `--ffmpeg`/`--ffprobe` → `FFMPEG_BIN`/`FFPROBE_BIN` → `PATH`.
Ví dụ nếu đã cài trong thư mục cá nhân:

```bash
export FFMPEG_BIN="$HOME/.local/bin/ffmpeg"
export FFPROBE_BIN="$HOME/.local/bin/ffprobe"
```

Ghi nhận máy chuẩn bị demo: binary BtbN trong `~/.local/bin/`, lấy từ nhà cung cấp
build được trang FFmpeg liệt kê, đã chạy được `-version` cho cả hai:
`n9.0.2-37-gae19868753-20261028`. Đây là trạng thái host, không phải cam kết project
tự cài binary hay mọi môi trường đều có sẵn.

Whisper là tuỳ chọn, tách môi trường khỏi Telethon. Chạy từ gốc project:

```bash
python3 -m venv .venv-video
.venv-video/bin/python -m pip install -r config/requirements-video.txt
```

[requirements-video.txt](<../config/requirements-video.txt>) ghim `faster-whisper==1.2.1`
và `av==16.0.1`: PyAV 19 bỏ tham số `metadata_errors` mà bản faster-whisper này dùng,
gây lỗi phiên âm. Không tự nâng PyAV lên mới nhất khi giữ faster-whisper 1.2.1.
Nếu Python thiếu `ensurepip`, có thể cài `python3-venv` như trên, hoặc dùng pip hiện
có hỗ trợ `--python`. Ví dụ sau **chỉ dùng khi môi trường pip API đã tồn tại**:

```bash
python3 -m venv --without-pip .venv-video
.venv-api/bin/python -m pip --python .venv-video install --only-binary=:all: pip -r config/requirements-video.txt
```

Whisper mặc định `tiny` đa ngôn ngữ, `--language vi`, CPU/int8, `--threads 2`.
Cài package **không có nghĩa đã có weights**. Mặc định chỉ nạp weights cục bộ;
`--whisper-model /duong/dan/model` dùng model đã chuẩn bị. Chỉ thêm
`--allow-model-download` khi chủ động đồng ý tải weights lần đầu (có mạng), cache
ở `.cache/whisper/`. Máy chuẩn bị demo đã có weights `tiny` trong cache; máy mới
vẫn cần tự chuẩn bị hoặc opt-in tải. Không tự tải model thị giác hoặc khởi chạy
Qwen/Ollama/server.

## 2. Thử offline an toàn, DB riêng

Thay đường dẫn video bên dưới bằng file của bạn được phép xử lý. Không dùng file
fixture cũ làm đầu ra. DB phải được khởi tạo trước; `--db` đặt **trước subcommand**.
Mặc định DB là `TELEGRAM_DB_PATH` hoặc `data/telegram.sqlite3`; ví dụ dùng DB tách biệt:

```bash
python3 main.py video --help
python3 main.py db --db data/video-demo.sqlite3 init

# Chỉ kiểm tra metadata + SHA-256; không mở/ghi DB, không gọi model/API
python3 main.py video inspect "/duong/dan/video-cua-ban.mp4"

# Chỉ tách JPEG và WAV mono 16 kHz (nếu có audio), lưu analysis trạng thái extracted
python3 main.py video --db data/video-demo.sqlite3 extract "/duong/dan/video-cua-ban.mp4"

# Phân tích thiếu bước có chủ ý; không tải weights, không gọi model/API
python3 main.py video --db data/video-demo.sqlite3 analyze "/duong/dan/video-cua-ban.mp4" \
  --skip-transcript --skip-vision

# Thay 2 bằng video_analysis_id do analyze vừa trả về; không phải ID Telegram
python3 main.py video --db data/video-demo.sqlite3 classify 2 --allow-partial --dry-run
python3 main.py video --db data/video-demo.sqlite3 results --analysis-id 2 --limit 20
```

`extract` không tạo bằng chứng đủ để `classify`/`prepare`; muốn phân loại hãy chạy
`analyze`. `--message-id N` của `extract`/`analyze` chỉ liên kết tới **row ID nội bộ**
của tin đã có trong cùng DB, không tải media từ tin đó. Mỗi lượt tạo analysis và
thư mục artifacts mới; không mặc nhiên tái sử dụng kết quả `extract` trước.

## 3. Phân tích đầy đủ hoặc thiếu bước có khai báo

### Model thị giác local (OpenAI-compatible, ví dụ Qwen-VL/Ollama)

Bạn phải tự chuẩn bị server, model hỗ trợ ảnh và trả JSON đúng cấu trúc.
Chọn **đúng tên model đã có trên server của bạn**, không dùng tên ví dụ làm cam kết
model đã được cài. Ví dụ loopback, không cần `--allow-remote`:

```bash
.venv-video/bin/python main.py video --db data/video-demo.sqlite3 analyze "/duong/dan/video-cua-ban.mp4" \
  --whisper-model "/duong/dan/weights-whisper-da-co" \
  --vision-provider openai --vision-model "TEN_MODEL_THI_GIAC_DA_CAI" \
  --vision-url http://127.0.0.1:11434/v1/chat/completions
```

`--vision-url` nhận base URL hoặc URL `/v1/chat/completions`; biến tương ứng là
`VISION_API_URL`, model là `VISION_MODEL`. OpenAI-compatible từ xa cần HTTPS,
`VISION_API_KEY` và `--allow-remote`. Không mặc định chọn model hay âm thầm fallback.

### Gemini từ xa (có thể tính phí)

Không có API key được cung cấp kèm project. Tự đặt `GEMINI_API_KEY` trong môi trường,
không ghi key vào JSON, tài liệu hay lịch sử shell. Ví dụ sau **gửi ảnh ra ngoài**:

```bash
.venv-video/bin/python main.py video --db data/video-demo.sqlite3 analyze "/duong/dan/video-cua-ban.mp4" \
  --whisper-model "/duong/dan/weights-whisper-da-co" \
  --vision-provider gemini --vision-model gemini-2.5-flash --allow-remote
```

Gemini chỉ dùng endpoint `generateContent` chính thức. Mỗi request thị giác gửi
**một khung hình JPEG đã lấy mẫu**, không gửi nguyên video/audio; nhiều khung hình
là nhiều request. ASR chạy cục bộ. Không theo redirect, không tự retry request thị giác.
`--allow-remote` không thay thế key, cũng không phải cờ cho phép tải weights Whisper.

Muốn bỏ một bước, ghi rõ `--skip-vision` hoặc `--skip-transcript`; cả hai cũng được.
Analysis thiếu bước có trạng thái `partial` và `evidence.gaps`. Khi đó **cả**
`classify` lẫn `prepare` cần `--allow-partial`; đây là chấp nhận giới hạn, không phải
coi dữ liệu đã đầy đủ. Video thật sự không có audio được ghi `no_audio`, không bịa transcript.

### Cờ và giới hạn

- `inspect`/`extract`/`analyze`: `--timeout 120` giây **mỗi** subprocess FFmpeg/ffprobe
  hoặc request thị giác, không phải timeout toàn pipeline hay thời hạn ASR. FFmpeg
  nhận tối đa 600 giây; vision tối đa 300 giây.
- Từ chối file rỗng, URL, playlist, symlink và container không hỗ trợ; mặc định tối đa
  **512 MiB**, **600 giây**, **8.294.400 pixel/khung** (3840×2160). `--max-duration`
  đổi ngưỡng thời lượng, không cắt video ngầm; không có cờ CLI đổi giới hạn byte/pixel.
- `extract`/`analyze`: `--interval 5`, `--max-frames 12` (cho phép 1–60).
  Khi số mẫu bị giới hạn, lấy mẫu trải trên toàn luồng video đã chọn thay vì chỉ đầu
  clip. Nếu audio dài hơn hình, `media.duration` vẫn giữ toàn thời lượng media;
  sampling dùng `video_duration` và ghi `unsampled_audio_tail_seconds` để không
  ngầm coi phần audio đuôi đã có ảnh. JPEG giữ tỷ lệ, tối đa 640×640;
  **không bao phủ mọi thời điểm**.
- Evidence đưa sang Jev tối đa 48.000 ký tự; quá giới hạn thì báo lỗi, không tự cắt.
- `results`: `--analysis-id N` tuỳ chọn, `--limit 20` mặc định (1–1000).

## 4. Jev: chạy trực tiếp hoặc bridge DSH

### HTTP trực tiếp

`classify` **không có `--dry-run`** sẽ gọi mạng/có thể tính phí, cần
`TYPESAFE_API_KEY` do bạn cung cấp. Không dùng `GEMINI_API_KEY` để thay key Jev.
`N` phải là ID analysis `ready`, hoặc
thêm `--allow-partial` nếu chủ động dùng analysis `partial`.

```bash
python3 main.py video --db data/video-demo.sqlite3 classify N --dry-run
python3 main.py video --db data/video-demo.sqlite3 classify N --model jev-latest --timeout 60
```

`--model` mặc định `JEV_MODEL` hoặc `jev-latest`. `--dry-run` chỉ in evidence/rubric,
không gọi API, không lưu nhãn. Kết quả thành công được cache theo analysis ID,
evidence hash, rubric và backend; `--recheck` chủ động gọi lại và tạo lịch sử mới.
Cache HTTP và bridge riêng nhau. Lỗi được lưu là lỗi, **không có nhãn dự phòng**.

### Bridge `prepare` → `jev_ask` → `apply`

Hai lệnh CLI này không gọi API; bước gọi công cụ Jev trong DSH do người vận hành
thực hiện riêng. Tên JSON trần được đưa vào [json_folder](<../json_folder/>).
Nếu đã cache nhãn công cụ thành công cho evidence/rubric/backend này, `prepare`
trả thành công **nhưng không tạo file**; thêm `--recheck` nếu chủ động muốn gọi lại.
Nếu cần request mới, `prepare` tạo file mới, từ chối ghi đè; chọn tên chưa tồn tại:

```bash
python3 main.py video --db data/video-demo.sqlite3 prepare N \
  --model jev-latest --out video-requests-moi.json
```

Request có `format: "telegram-video-jev-requests-v1"`, `batch_id` UUID, `model`,
`rubric_version`, `questions`, `allow_partial`, `recheck` và **một** `item`:

```text
item.video_analysis_id       ID analysis SQLite
item.evidence_sha256         SHA-256 của evidence chuẩn hoá
item.state.evidence         transcript, quan sát/OCR theo timestamp, sampling, gaps, giới hạn
item.source                 video_sha256, source_path, message_row_id,
                            pipeline_version, analysis_status, options
```

Gọi `jev_ask` với `state=request.item.state`, `questions=request.questions` và model
đúng `request.model`; không thay rubric hay nhét chỉ dẫn vào bằng chứng. Giữ nguyên
request. Lưu **nguyên văn output thực của công cụ** vào `tool_output` của JSON kết quả
mới trong [json_folder](<../json_folder/>). Đây là khuôn minh hoạ, chưa phải kết quả thật:

```json
{
  "format": "telegram-video-jev-results-v1",
  "batch_id": "CHEP_batch_id_TU_REQUEST",
  "video_analysis_id": 123,
  "evidence_sha256": "CHEP_item.evidence_sha256_TU_REQUEST",
  "tool_output": "CHEP_NGUYEN_VAN_OUTPUT_THUC_CUA_jev_ask"
}
```

Thay `123` bằng số nguyên `item.video_analysis_id` thật; thay mọi chỗ `CHEP_...`.
Đây **không** phải định dạng text bridge có mảng `results`/`items`. Parser đọc model
ở dòng đầu `Jev jev-...` và đúng một dòng `classification: <nhãn> (...)`; không tự
soạn dòng nhãn để giả kết quả. Sau khi đã có output thật:

```bash
python3 main.py video --db data/video-demo.sqlite3 apply \
  --requests video-requests-moi.json --results video-results-moi.json
python3 main.py video --db data/video-demo.sqlite3 results --analysis-id N
```

`apply` đối chiếu batch, ID/hash, toàn bộ evidence/source và rubric với SQLite;
nhập trùng thành công cùng backend không tạo thêm nếu không có `--recheck` từ request.
Provenance là `dsh-jev-tool-import`, transport `operator-provided-jev-tool-output`:
**artifact người vận hành cung cấp, không phải response HTTP được xác thực**.
Chỉ lưu nhãn/model đọc được; không chuyển xác suất hiển thị làm tròn thành điểm
chính xác. JSON request/result không tự chứng minh công cụ đã thật sự được gọi.

## 5. Lưu trữ, quyền riêng tư và diễn giải

- Schema SQLite **v3** bổ sung `video_analyses` và `video_classifications`. Mở DB ứng
  dụng v1/v2 sẽ nâng cấp cộng thêm (v1 cũng thêm bảng phân loại text); nên backup trước.
  Không đổi điểm heuristic/rubric text hay di chuyển nguồn gốc. Video gốc không bị
  chuyển/xoá; DB lưu đường dẫn, SHA-256, options, evidence và snapshot phân loại.
- Artifact ở `media/video/<UUID>/`: thư mục mới `0700`, JPEG/WAV `0600`. JSON do
  `prepare` tạo `0600`; tất cả JSON xuất/lưu thủ công nên để trong
  [json_folder](<../json_folder/>). Output JSON của `inspect`/`analyze`/`results` mặc định
  chỉ ra stdout. Đường dẫn tuỳ chọn có thư mục theo cwd; mặc định project neo ở gốc.
- `inspect` không ghi DB. Các lệnh còn lại cần DB đã có; `results` đọc lịch sử offline
  nhưng mở DB cũ vẫn có thể nâng cấp schema. `--help`, `inspect`, `extract`, `results`,
  `prepare`, `apply` và `classify --dry-run` không gọi API. Lỗi/ngắt có thể giữ artifacts
  dở dang; lỗi model sau tách giữ evidence đã có và trạng thái `error`. Bước lỗi được
  đánh dấu riêng trong `gaps`; các quan sát khung hình đã hoàn tất vẫn được giữ nếu
  một request thị giác sau đó lỗi. Analysis `error` không được `--allow-partial`
  biến thành dữ liệu hợp lệ để phân loại, không tự gán nhãn.
- DB, transcript, OCR, mô tả, ảnh và JSON có thể chứa dữ liệu cá nhân; quyền file không
  phải mã hoá. Chỉ xử lý media được phép, tự quản lý retention/backup và cân nhắc kỹ
  trước `--allow-remote`. Jev HTTP gửi **evidence văn bản** (gồm transcript/OCR/mô tả)
  ra nhà cung cấp, khác với upload JPEG của vision.
- Bảy nhãn: `call_to_violence` (kêu gọi bạo lực), `call_to_property_damage` (kêu gọi phá
  hoại tài sản), `obstruct_officials` (chống người thi hành công vụ), `call_against_state`
  (kêu gọi chống chính quyền), `threat` (đe doạ), `criticism` (phê bình/chính kiến, không
  phải kêu gọi), `insufficient_information` (thiếu thông tin). Không có nhãn nào là bảo
  đảm an toàn hay kết luận pháp lý.
- Ba mức hiển thị: “Rủi ro cao”, “Cần xem xét”, “Thông thường” (khóa lưu vẫn là
  `High-risk`/`Suspicious`/`Normal`); điểm 0–100 chỉ là tín hiệu, kết quả kèm `evidence`,
  `context` và `requires_review`.
- ASR/OCR/mô tả có thể sai; mẫu ảnh bỏ sót diễn biến. Không nhận diện khuôn mặt/xác minh
  danh tính, không kết luận tội/pháp lý; xác suất/confidence **chưa được hiệu chuẩn**.
  Chất lượng thực tế/độ chính xác: **chưa đo**; không coi ví dụ này là smoke test live
  toàn pipeline hoặc phép đo độ chính xác.

## 6. Phạm vi và giới hạn

- Chỉ xử lý media công khai mà bạn được phép; không tự động báo cáo hay kết luận thay cơ
  quan có thẩm quyền.
- Jev chỉ trả nhãn/mức và evidence; kết quả có `requires_review` và cần người có thẩm quyền
  duyệt. Nhãn `criticism` được tách khỏi kêu gọi; nội dung tường thuật/cảnh báo bị hạ điểm.
- Transcript/OCR/mô tả có thể sai và mẫu khung hình bỏ sót diễn biến; không coi kết quả là
  kết luận pháp lý hay xác suất đã hiệu chuẩn.
- Lịch sử phân loại, rubric và evidence được lưu để audit; `--recheck` tạo bản ghi mới thay
  vì ghi đè kết quả cũ.

## 7. Kiểm chứng bản triển khai

- **495 test** ở lần chạy gần nhất (venv API đạt toàn bộ; Python hệ thống bỏ qua 9 test cần Telethon/thị giác); backend đã thêm test cho
  rubric kêu gọi). Mốc lịch sử trước khi đổi trọng tâm: **243 test**, venv API thành công
  toàn bộ; Python hệ thống bỏ qua 1 test cần Telethon; khi đó có **85 test video** cho
  media/model giả lập, HTTP loopback cục bộ, orchestration, migration, cache, partial/error
  và bridge. Suite không gọi API có phí, tải weights hoặc đụng dữ liệu thật.
- Smoke riêng đã dùng FFmpeg thật tạo clip 11 giây từ [mẫu JFK của faster-whisper](https://github.com/SYSTRAN/faster-whisper/blob/master/tests/data/jfk.flac),
  lấy 3 JPEG và WAV mono 16 kHz. Whisper `tiny` CPU/int8 phiên âm thành công, có đoạn
  `[0, 11]`: “And so my fellow Americans, ask not what your country can do for you,
  ask what you can do for your country.” Đây là mẫu tiếng Anh kiểm tra đường xử lý,
  **không phải benchmark tiếng Việt**.
- Bằng chứng này được gửi thật tới công cụ Jev của DSH (`jev-1.14.0`), nhận `other` (nhãn
  cũ, trước khi đổi trọng tâm), lưu và replay qua bridge thành công mà không nhân đôi.
  Vision trong smoke này **được bỏ qua tường minh** và analysis là `partial`; không tuyên bố
  đã chạy Gemini/Qwen-VL thật. Các adapter vision đã được kiểm thử bằng mock và HTTP loopback;
  cần API key hoặc server local của bạn để chạy inference thị giác thực tế.
- DB production được backup trước nâng v2 → v3 tại
  [pre-video-v3-backup.sqlite3](<../data/pre-video-v3-backup.sqlite3>) (artifact cục bộ).
  Counts + SHA-256 của mọi hàng trong 12 bảng cũ giữ nguyên, `integrity_check = ok`,
  không lỗi khóa ngoại. Không tự phân tích video hoặc gửi dữ liệu production lên model.
