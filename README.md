# Demo lấy bài đăng từ Telegram + phát hiện dấu hiệu lừa đảo

Bộ công cụ nhỏ để: **thu thập bài đăng Telegram → chấm điểm nghi ngờ lừa đảo (0–100) →
tính điểm rủi ro cho cả group/kênh → in báo cáo**. Có hai backend thu thập độc lập dùng
chung một bộ chấm điểm.

## Bản đầu: tin nhắn văn bản → Jev → SQLite

[`classify_messages.py`](<classify_messages.py>) phân loại **văn bản đã thu thập trong DB**
bằng model Jev thật, không phải bộ từ khoá trong analyzer. Bước này chạy **tường minh sau
thu thập**, không tự gọi dịch vụ có phí khi crawl. Chỉ dùng thư viện chuẩn Python; chưa
xử lý âm thanh, ảnh hay video và không truy cập link trong tin nhắn.

### Năm nhãn

| Nhãn | Ý nghĩa |
|---|---|
| `scam_indicators` | Có dấu hiệu cụ thể như xin OTP, phí mở khoá thưởng/rút tiền, lợi nhuận phi thực tế. |
| `advertising_unverified` | Quảng cáo nhưng chưa có dấu hiệu lừa đảo cụ thể; **không có nghĩa đã xác minh an toàn**. |
| `reporting_warning` | Tin tức, cảnh báo, trích dẫn/ví dụ về lừa đảo, không phải lời mời tham gia thực tế. |
| `other` | Nội dung khác đủ rõ. |
| `insufficient_information` | Thiếu bối cảnh, hoặc cần ảnh/video/link chưa được cung cấp. |

Rubric có phiên bản và hướng dẫn không làm theo chỉ dẫn nằm trong tin nhắn. Đây là
**sàng lọc dấu hiệu**, không phải kết luận pháp lý. Xác suất/confidence của Jev **không
được xem là độ chính xác đã hiệu chuẩn**, không dùng một ngưỡng cố định để tự kết tội.

### Chạy độc lập bằng API key TypeSafe

API theo [tài liệu chính thức](https://docs.typesafe.ai/introduction/quickstart.md),
mặc định `https://api.typesafe.ai/v1/systemone`. Key này **khác** Telegram API ID/hash:

```bash
# Thu thập trước bằng các CLI hiện có, hoặc nhập JSON lịch sử:
python3 database.py import-json --all

# Xem ứng viên, không gọi mạng / không ghi kết quả phân loại:
python3 classify_messages.py run --limit 10 --dry-run

# Nhập kín vào biến môi trường, không ghi key vào source/JSON:
read -rsp 'TypeSafe API key: ' TYPESAFE_API_KEY; echo
export TYPESAFE_API_KEY
python3 classify_messages.py run --limit 10
unset TYPESAFE_API_KEY

# Xem lịch sử offline:
python3 classify_messages.py results --limit 20
python3 classify_messages.py results --label scam_indicators
```

Cờ toàn cục `--db PATH` đứng **trước** subcommand; mặc định dùng DB chung và
`TELEGRAM_DB_PATH`. Các bộ lọc `--keyword`, `--username`, `--run-id`, `--message-id`
giới hạn ứng viên. `--message-id` là **row ID nội bộ SQLite**, không phải ID Telegram.
Mặc định tối đa 10 tin/lần, bỏ tin rỗng và namespace demo; thêm `--include-demo` để thử
fixture. Tin quá 16.000 ký tự được bỏ qua có báo lỗi, **không cắt ngầm**. `run` và
`prepare` ghi trạng thái terminal `skipped/text_too_long` để lần sau vượt qua tin đó,
không bị kẹt trước `--limit`; `--recheck` cho phép xét lại cả các tin đã bỏ qua.

Lần sau bỏ qua kết quả thành công cùng nội dung/rubric/backend/model yêu cầu; tin sửa
nội dung được phân loại lại. Dùng `--recheck` khi muốn đánh giá lại. `--model` hoặc
`JEV_MODEL` chọn model; nên pin phiên bản cho thí nghiệm tái lập. Alias `jev-latest` có
thể đổi ở nhà cung cấp mà cache không tự biết: dùng `--recheck` sau khi đổi phiên bản.
HTTP không tự retry; lỗi key/rate limit/mạng được lưu với trạng thái `error`, dừng batch
và trả mã khác 0, **không lấy heuristic làm nhãn AI dự phòng**. Chạy lại để tiếp tục.
`JEV_API_URL` cho phép cấu hình endpoint HTTPS khác; chỉ đặt khi tin cậy nơi nhận key.

**Quyền riêng tư:** API chỉ nhận `state.text` và rubric/model, không gửi toàn bộ hồ sơ
hay session Telegram. Văn bản vẫn có thể chứa số điện thoại/thông tin nhạy cảm; chỉ gửi
nội dung bạn có quyền xử lý. DB lưu nguyên văn để truy vết và **không mã hoá**.

### Dùng công cụ Jev có sẵn trong phiên DSH

CLI độc lập không tự dùng credential nội bộ DSH. Nếu chưa có `TYPESAFE_API_KEY`, agent
có thể gọi công cụ `jev_ask` của phiên rồi nhập kết quả qua bridge tường minh:

```bash
python3 classify_messages.py prepare --limit 10 --out jev_requests.json
# Agent gọi jev_ask cho từng item với đúng state, questions và model trong manifest;
# chép NGUYÊN VĂN output công cụ vào results, không tự tạo nhãn.
python3 classify_messages.py apply --requests jev_requests.json --results jev_results.json
python3 classify_messages.py results
```

Manifest và results có tên trần được đặt trong [`json_folder/`](<json_folder/>), tạo mới
với quyền `0600`, không ghi đè. Manifest/results giới hạn **16 MiB UTF-8**; `prepare`
từ chối tạo manifest quá lớn, cần giảm `--limit` và chia batch. Results dùng cấu trúc
sau (các giá trị là placeholder):

```json
{
  "format": "telegram-jev-results-v1",
  "batch_id": "<batch_id từ manifest>",
  "results": [{
    "message_row_id": 123,
    "input_sha256": "<hash từ item>",
    "tool_output": "<toàn bộ output Jev, bắt đầu bằng Jev jev-...>"
  }]
}
```

Bridge kiểm tra batch/hash/rubric và nội dung/nguồn còn khớp DB trước khi lưu. Thiếu
kết quả thì báo thiếu, không đoán nhãn; replay bình thường không nhân đôi. Đây là
**artifact do người vận hành cung cấp, không có chữ ký xác thực từ dịch vụ**: lưu provider
`dsh-jev-tool-import`, tách khỏi API trực tiếp. Output hiển thị đã làm tròn được giữ
nguyên văn; bridge **không biến nó thành xác suất số giả chính xác**. API trực tiếp giữ
response JSON và xác suất gốc. Không gửi toàn bộ manifest (chứa nguồn) làm `state`.

### Lưu trữ, kiểm thử và giới hạn

Schema v2 thêm bảng `message_classifications`, giữ lịch sử riêng: text/hash, snapshot
nguồn, model thực tế, rubric/version, provider/backend, nhãn, response, lỗi và thời điểm.
**Không ghi đè điểm heuristic hoặc snapshot lịch sử.** Kết quả cũ được đánh dấu `stale`
nếu text hiện tại thay đổi. Mỗi kết quả commit riêng; không giữ transaction trong lúc
chờ API. Không chạy nhiều classifier cùng lúc nếu cần tránh gọi model trùng nhau.
Schema v1 được nâng cấp cộng thêm bảng khi mở DB; nên dùng lệnh backup trước nâng cấp.
Các artifact `jev_*.json` và DB bị git-ignore vì chứa nội dung tin nhắn.

Đã smoke-test Jev thật qua công cụ DSH trên **6 tin tổng hợp**, bao phủ đủ năm nhãn,
cảnh báo có trích dẫn và thử chèn chỉ dẫn: kết quả lưu/replay thành công với model
`jev-1.14.0`. Đây **không phải phép đo độ chính xác trên dữ liệu thực tế**. Nhánh HTTP
được test offline bằng mock; chạy HTTP có xác thực cần API key của bạn.

Kiểm thử bản Jev: bộ **139 test** chạy bằng venv API đều thành công; cùng bộ test
trên Python hệ thống thành công với 1 test cần Telethon được bỏ qua. Các test không
gọi dịch vụ có phí. Test hồi quy bao gồm tiến độ sau tin quá dài và giới hạn byte
UTF-8 của manifest.
DB hiện có đã được sao lưu tại [`pre-jev-v2-backup.sqlite3`](<data/pre-jev-v2-backup.sqlite3>)
trước khi nâng cấp; các bảng dữ liệu cũ giữ nguyên, không tự gửi tin lịch sử lên Jev.

## Lưu trữ SQLite: triển khai và tra cứu

Kết quả thu thập thành công được **tự lưu vào SQLite**, kể cả các lệnh tra username / tìm
keyword **không có `--out`**. JSON vẫn được xuất theo cách cũ, với quy ước tên trần nằm
trong [`json_folder/`](json_folder/); SQLite không thay thế hay xoá các bản xuất đó.
SQLite dùng `sqlite3` của thư viện chuẩn Python: **không cần pip, không cần máy chủ DB**.
Telethon chỉ cần khi dùng backend API như trước.

### Đường dẫn và vận hành

- Mọi CLI thu thập / tra cứu dùng chung `--db PATH`, biến môi trường `TELEGRAM_DB_PATH`,
  và `--no-db` để tắt lưu DB cho lần chạy đó. `--db` ưu tiên hơn biến môi trường.
- Mặc định là `data/telegram.sqlite3`, được dựng thành **đường dẫn tuyệt đối từ vị trí
  script**, không phụ thuộc thư mục đang đứng khi gọi lệnh.
- DB là **plaintext, không mã hoá**, đặt quyền `0600`; thư mục `data/` được git-ignore.
  Dữ liệu bài đăng, hồ sơ và link mời có thể nhạy cảm: bảo vệ cả bản sao lưu, không công
  khai thư mục dữ liệu. Không nhập credential / session key vào kho dữ liệu này;
  [`json_folder/tg_api.json`](json_folder/tg_api.json) bị bỏ qua khi import hàng loạt.
- File phiên Telethon là **kho riêng**, không phải DB thu thập và không được nhập vào đó.
- Cấu hình SQLite dùng **WAL**, `busy_timeout = 10000` ms (10 giây), ghi theo **batch
  transaction**. SQLite chỉ có **một writer tại một thời điểm**; tránh nhiều tiến trình
  ghi kéo dài cùng lúc. Sao lưu DB đang dùng bằng lệnh `backup`, không chỉ chép file DB
  chính khi còn dữ liệu trong WAL.

```bash
python3 demo_telegram.py --demo --db data/thu_nghiem.sqlite3
python3 keyword_usernames.py --demo --keyword 'đầu tư'  # tự lưu dù không có --out
python3 username_info.py durov --no-db                 # không lưu DB lần này
TELEGRAM_DB_PATH=/tmp/telegram.sqlite3 python3 demo_telegram.py --demo
```

### Quản trị bằng [`database.py`](database.py) (offline, không gọi Telegram)

**Cờ toàn cục `--db` phải đứng trước subcommand**; các ví dụ không ghi cờ này dùng DB
mặc định (hoặc `TELEGRAM_DB_PATH`). Chạy từ thư mục project:

```bash
python3 database.py init
python3 database.py import-json --all                   # quét JSON, bỏ qua tg_api.json
python3 database.py import-json posts_tg.json            # đọc json_folder/posts_tg.json
python3 database.py stats
python3 database.py runs --limit 20
python3 database.py messages --keyword 'đầu tư' --username dautu.sieuloinhuan --limit 10
python3 database.py profiles durov
python3 database.py results RUN_ID
python3 database.py export RUN_ID --out sqlite_export.json
python3 database.py backup data/backup.sqlite3
python3 database.py --db data/thu_nghiem.sqlite3 stats
```

Thay `RUN_ID` bằng ID lấy từ `runs`. `export` giữ quy ước JSON: tên trần trong ví dụ là
`json_folder/sqlite_export.json`; **không ghi đè** file có sẵn. `backup` yêu cầu **đích
mới**, không dùng lại file backup đã tồn tại. Import lại **đúng cùng payload** là idempotent
nhờ SHA256, không tạo bản run trùng và **không chấm lại điểm lịch sử**.

### Hiểu dữ liệu đã lưu

[`storage.py`](storage.py) quản lý `Database` schema v2; [`database.py`](database.py) là CLI
quản trị. Mỗi `run` giữ **snapshot JSON gốc bất biến**. Bảng `messages` là quan sát mới
nhất, duy nhất theo `(entity_id, message_id)`; trạng thái cũ đầy đủ vẫn lấy được bằng
`export RUN_ID`. Phân tích được giữ **theo từng run / message**, có nguồn gốc và phiên bản
analyzer. Phân tích mới dùng **SHA256 mã nguồn analyzer** làm provenance (không phải
semantic version); JSON import giữ phiên bản đã khai báo, hoặc `unversioned` nếu không
biết phiên bản, không gán giả phiên bản hiện tại. Import cũng lập chỉ mục tin nhắn trong
`found[].history` của bản xuất discovery cũ, nếu có, mà không sửa snapshot gốc.

JSON và SQLite là hai lần ghi riêng, **không phải một transaction chung**. Nếu SQLite báo
lỗi sau khi xuất JSON, file JSON vẫn còn để nhập lại; CLI trả mã lỗi thay vì báo lưu DB
thành công. Snapshot mỗi run được giữ đầy đủ nên dung lượng DB tăng theo số lần thu thập.

Định danh ổn định dùng **loại thực thể + Telegram ID**, còn username chỉ là alias có thể
đổi. Dữ liệu web chưa có ID dùng định danh username **tạm thời**, được làm giàu khi API
phân giải được ID sau đó. Namespace `demo` tách khỏi `telegram`, tránh trộn bài mẫu với
bài thật. Hồ sơ, kết quả tìm kiếm, link mời và điểm group được lưu theo ngữ cảnh run.
`common_groups` chỉ là **những nhóm chung nhìn thấy được từ phiên đang tra cứu**, không
phải toàn bộ danh sách nhóm mà một tài khoản tham gia.

**Kiểm chứng đợt SQLite ban đầu (trước Jev):** 80 test offline chạy thành công bằng Python trong venv API;
Python hệ thống chạy 79 test thành công, bỏ qua 1 test cần Telethon. DB đã nhập 9 snapshot,
1.138 bài đăng duy nhất và 1.155 bản phân tích; `integrity_check = ok`, không có lỗi khóa
ngoại. Import lại không nhân đôi dữ liệu. Bản sao lưu ban đầu được giữ cục bộ tại
[`initial-import-backup.sqlite3`](data/initial-import-backup.sqlite3).

## File

| File | Việc |
|---|---|
| [`keyword_usernames.py`](keyword_usernames.py) | **Chức năng 1, tách riêng**: nhập keyword → in danh sách username liên quan. |
| [`username_info.py`](username_info.py) | **Chức năng 2, tách riêng**: nhập username → in thông tin tài khoản đó. |
| [`analyzer.py`](analyzer.py) | **Nguồn chân lý dùng chung**: từ điển, trọng số, regex, `analyze()`, `group_summary()`, `print_report()`, `write_json()`, `username_payload()` / `related_usernames()` (`--keyword`), `make_profile()` / `print_profile()` (`--user`), dữ liệu mẫu `demo_posts()`. Chỉ thư viện chuẩn. |
| [`demo_telegram.py`](demo_telegram.py) | Backend **web**: đọc trang công khai `https://t.me/s/<kênh>` (và `https://t.me/<username>` cho `--user`). **Chỉ thư viện chuẩn** — không cần cài gì, không cần API key, không cần đăng nhập. |
| [`demo_telegram_api.py`](demo_telegram_api.py) | Backend **Telegram API (Telethon)**: group kín, tìm kiếm toàn cục, tra cứu username/ID, tìm chat theo từ khoá, tải media, OCR, chế độ bot, `--watch`. |
| [`find_private_groups.py`](find_private_groups.py) | Tìm & đọc **group kín** qua link mời, chia 3 bước `--discover` / `--join` / `--crawl`. |
| [`storage.py`](storage.py) | `Database` schema v2: snapshot run, thực thể / alias, message, phân tích và kết quả tra cứu. |
| [`database.py`](database.py) | CLI SQLite: init, import JSON, thống kê, truy vấn, export run và backup. |
| [`classify_messages.py`](<classify_messages.py>) | CLI văn bản → Jev → SQLite: run / prepare / apply / results. |
| [`text_classifier.py`](<text_classifier.py>) | Rubric có phiên bản, chọn ứng viên/cache, lưu bằng chứng và lịch sử phân loại. |
| [`jev_client.py`](<jev_client.py>) | HTTP client stdlib cho API TypeSafe; xác thực response và che lỗi nhạy cảm. |
| [`tests/test_text_classifier.py`](<tests/test_text_classifier.py>) / [`tests/test_jev_client.py`](<tests/test_jev_client.py>) | Test classifier, migration/cache/bridge và HTTP offline, không cần key. |
| [`tests/test_regression.py`](tests/test_regression.py) | Test hồi quy offline; mốc 50 test là số liệu kiểm chứng lịch sử 2026-10-11, không phải tổng hiện tại. |
| [`tests/test_storage.py`](tests/test_storage.py) | Test SQLite offline: schema, snapshot / identity, import / truy vấn / export / backup và persist từ CLI. |
| [`GIAI_THICH_CODE.md`](GIAI_THICH_CODE.md) | Giải thích chi tiết từng phần của code + công thức + các lỗi đã sửa. |
| [`json_folder/`](json_folder) | **Mọi file `.json` của project** — dữ liệu mẫu, kết quả chạy và cả `tg_api.json` (xem "Quy ước file JSON"). |
| [`json_folder/posts_tg.json`](json_folder/posts_tg.json) | Kết quả `--demo` của bản web (17 bài mẫu, có `groups`). |
| [`json_folder/posts_api.json`](json_folder/posts_api.json) | Kết quả bản API (100 bài, `backend: telethon`). |
| [`json_folder/nhom1.json`](json_folder/nhom1.json) | Dữ liệu **thật** 1000 bài của group `@aiquochientrang` (`backend: telethon-user`). |
| [`json_folder/real_tg.json`](json_folder/real_tg.json) / [`json_folder/real_tg_vn.json`](json_folder/real_tg_vn.json) | Dữ liệu thật `@durov` / `@baomoi`, `@tintucvtv`. |
| [`json_folder/found.json`](json_folder/found.json), [`json_folder/found_groups.json`](json_folder/found_groups.json) | Kết quả bước `--discover` của `find_private_groups.py`. |
| [`channels.txt`](channels.txt) | Danh sách kênh mẫu cho `--seed-file`. |

## Quy ước file JSON

**Từ nay mọi file `.json` nằm trong `json_folder/`** — kể cả `tg_api.json` (credential, quyền 600):

```
json_folder/
├── tg_api.json          # credential (api_id/api_hash/bot_token)
├── nhom1.json           # dữ liệu mẫu / lịch sử
├── posts_tg.json  posts_api.json  posts_web.json
├── real_tg.json   real_tg_vn.json
└── found.json     found_groups.json
```

Các cờ `--out`, `--report`, `--json`, `--in`, `--from-json` nhận **tên trần** và tự hiểu là
file trong `json_folder/`, nên mọi lệnh dưới đây vẫn gõ ngắn như cũ:

```bash
python3 demo_telegram.py --report posts_tg.json        # = json_folder/posts_tg.json
python3 keyword_usernames.py --json nhom1.json --keyword "chiến sự"
python3 username_info.py durov --out profile.json      # = json_folder/profile.json
```

Muốn ghi ra chỗ khác thì truyền đường dẫn có thư mục (hoặc tuyệt đối) — script giữ nguyên:
`--out /tmp/ketqua.json`, `--out baocao/posts.json`. Hàm dùng chung nằm ở cuối
[`analyzer.py`](analyzer.py): `json_dir()`, `json_path(name)`, `resolve_json(path)`.

## Cài đặt

Bản **web** chạy được ngay bằng Python 3.10+ (không cần cài gì):

```bash
python3 demo_telegram.py --demo
```

Bản **API** cần Telethon:

```bash
python3 -m venv .venv-api && .venv-api/bin/pip install telethon
# hoặc chỉ: pip install telethon
```

> **Máy thiếu `python3-venv`?** Nếu `python3 -m venv` báo *"ensurepip is not available"*,
> tạo venv rồi tự cài pip:
> ```bash
> python3 -m venv --without-pip .venv-api
> curl -fsSL https://bootstrap.pypa.io/get-pip.py | .venv-api/bin/python
> .venv-api/bin/python -m pip install telethon
> ```
> Đây chính là cách `.venv-api/` trong project được dựng (Python 3.13.12 + Telethon 1.45.0).

OCR (`--ocr`) cần thêm **Pillow**, **pytesseract** (pip) và **tesseract** (gói hệ thống,
ví dụ `sudo apt install tesseract-ocr`); thiếu thì `--ocr` tự bỏ qua chứ không sập.

## Chạy bản web (không cần đăng nhập)

```bash
python3 demo_telegram.py --demo                       # offline, dữ liệu mẫu (không cần mạng)
python3 demo_telegram.py --channel durov --limit 25   # kênh công khai (chạy thật)
python3 demo_telegram.py --channel baomoi --channel tintucvtv --limit 20
python3 demo_telegram.py --seed-file channels.txt     # mỗi dòng 1 kênh hoặc link t.me
python3 demo_telegram.py --user durov --user BotFather  # tra cứu theo username
python3 demo_telegram.py --channel durov --keyword "telegram"   # từ khoá -> username liên quan
python3 demo_telegram.py --report posts_tg.json       # in lại báo cáo từ JSON đã lưu
```

Không truyền nguồn nào thì mặc định chạy `--demo`. `--channel` nhận `durov`, `@durov`,
`t.me/durov`, `https://t.me/s/durov`; `--limit` là số bài tối đa **cho mỗi kênh**
(mặc định 20). `--out` mặc định là `json_folder/posts_web.json` (cố ý khác
`posts_tg.json` để không ghi đè file kết quả mẫu).

### Lấy dữ liệu bằng cách nào

Đọc HTML tĩnh của `https://t.me/s/<kênh>`:

- Thông tin kênh: tên, mô tả, số người đăng ký (`10.4M` → `10400000`).
- Mỗi bài: `message_id`, `url`, `text`, `hashtags`, `posted_at`, `views`, `author`,
  `forwarded_from`, `has_media`, `media_types`, `media_count`, `link_preview`, `subscribers`.
- Tự **phân trang** bằng `?before=<message_id>` (mỗi trang ~20 bài) và chống trùng theo
  khóa `kênh/message_id`.
- Link mời nhóm kín (`t.me/+...`, `t.me/joinchat/...`) bị **từ chối kèm hướng dẫn** —
  bản web không xem được group kín.

## Tra cứu username: `--user` (tài khoản này là ai?)

Nhập một username (kênh / nhóm / bot / người dùng) để xem thông tin công khai:

```bash
python3 demo_telegram.py --user durov --user BotFather --user vitalikbuterin
python3 demo_telegram.py --user pythonchat --limit 5      # nhóm: chỉ tra thông tin
python3 demo_telegram_api.py --user durov --user @tennhom # bản API: thêm ID/verified
```

- Bản **web** đọc `https://t.me/<username>` và tự phân loại: `subscribers` → **Kênh**,
  `members` → **Nhóm**, `monthly users`/`Start Bot` → **Bot**, `You can contact @x` →
  **Người dùng**. Không có mô tả + không có khối thông tin → báo **không tra được**
  (không tồn tại, riêng tư, hoặc không có bản xem trước).
- Bản **API** dùng `get_entity()` nên tra được cả **ID**, `verified`, `is_bot`,
  `participants_count`, và group kín mà tài khoản đã tham gia.
- **Chi tiết hơn ở bản API** (`--api`): **số điện thoại** (chỉ hiện khi đối tượng là danh bạ
  hoặc họ để công khai), **trạng thái hoạt động** ("đang online", "truy cập lần cuối …"),
  **ngày tạo** kênh/nhóm, **các username khác**, **cờ tài khoản** (Premium / bị gắn cờ lừa đảo
  / giả mạo / bị hạn chế / liên hệ hai chiều), **lệnh bot**, và **nhóm/kênh dùng chung**
  (`GetCommonChats`) — ví dụ `@BOSSDZAIGK88` dùng chung nhóm *VÀO CÓ 1 CHIỆU 💴💶* (65 784 thành viên).
- **Ảnh đại diện** được tải về `profile_photos/<username>.jpg` (bản web lấy từ `og:image`,
  bản API qua `download_profile_photo`). Đổi thư mục bằng `--photo-dir DIR`, tắt bằng
  `--no-photo`. Giới hạn danh sách nhóm chung bằng `--common-limit N`.
- Nếu đối tượng là **kênh/nhóm**, script còn lấy luôn bài gần đây và in thống kê
  (số bài, điểm TB, cao nhất, mức rủi ro) — tức mục `[0] THÔNG TIN TÀI KHOẢN TRA CỨU`.
- Kết quả được lưu vào khóa `profiles` trong file JSON, `--report` đọc lại và in tiếp.

> Chỉ cần đúng việc "username → thông tin"? Dùng bản gọn
> [`username_info.py`](username_info.py) (nhận nhiều username, `--api`, `--from-json`,
> `--photo-dir`, `--no-photo`, `--common-limit`).

## Nhập keyword → liệt kê username liên quan: `--keyword`

Nhập một hoặc nhiều từ khoá, script quét các bài đã thu thập rồi liệt kê **username có
thể liên quan** (mục `[6]`), kèm link mời nhóm kín gặp trong các bài đó:

```bash
python3 demo_telegram.py --channel durov --keyword "telegram"
python3 demo_telegram.py --demo --keyword "đầu tư" --keyword "hoàn tiền"
python3 demo_telegram.py --report posts_web.json --keyword "hoàn tiền"   # tra trong JSON đã lưu
python3 demo_telegram_api.py --keyword "chiến sự"          # thêm: tìm chat theo từ khoá
```

Mỗi dòng cho biết username được gặp **vì lý do gì**: `@nhắc` (được mention trong bài),
`link t.me` (link trong bài), `nguồn forward`, `bài đăng trong kênh`, `kết quả tìm chat`
(chỉ bản API). Kèm **điểm nghi ngờ cao nhất** của các bài chứa username đó, nên dễ ưu tiên
rà soát. Mục `[6]` chỉ hiện khi bạn truyền `--keyword`, hoặc khi mở lại file JSON đã lưu
sẵn kết quả (dùng `--report`). Nếu chỉ truyền `--keyword` mà không có nguồn nào, bản web
chạy dữ liệu mẫu offline để minh hoạ.

- Bản **API** còn dùng `contacts.SearchRequest` để tìm **chat** theo từ khoá (chỉ trong
  các chat tài khoản đã tham gia; tài khoản bot không dùng được, script tự cảnh báo).
- Kết quả nằm trong các khóa `keywords`, `usernames` (và `invite_links`) của file JSON.

> Chỉ cần đúng việc "keyword → username"? Dùng bản gọn
> [`keyword_usernames.py`](keyword_usernames.py) (có thêm `--text-only`, `--min-score`, `--top`).

## Hai công cụ tách riêng (mỗi file một chức năng)

Hai file dưới đây làm đúng **một việc**, gọn hơn `demo_telegram.py`/`demo_telegram_api.py`
(vốn thu thập + chấm điểm + in báo cáo đầy đủ):

### Chức năng 1 — `keyword_usernames.py`: keyword → danh sách username

Cách dùng ngắn nhất: **chỉ cần từ khoá**, script tự tìm trên Telegram rồi in danh sách gọn
`@username — Tên (Loại)`:

```bash
.venv-api/bin/python keyword_usernames.py --keyword "hathanh"
```
```
    1. @hathanh  —  Thanh Hà  (Người dùng)
    2. @hathanh7  —  Thanh Phong  (Người dùng)
    3. @hathanh_0808  —  ☘️ 𝓣𝓱𝓪𝓷𝓱 𝓗à  (Kênh, 606 người đăng ký)
    4. @hathanhcrypto  —  Hà Thành Trading ( Chat )  (Nhóm, 114 thành viên)
    5. @hathanhgrbot  —  QuanLyNhanVien  (Bot)
    6. @hathanhtrading  —  Hà Thành Trading  (Kênh, 213 người đăng ký)
    ...
```

Không chọn nguồn nào ⇒ tự bật `--api` (`contacts.Search`, cần Telethon + phiên đã đăng nhập,
tức chạy bằng `.venv-api/bin/python`). Muốn xem định dạng kết quả mà không cần mạng thì
thêm `--demo`. Cách này tìm được cả **người dùng** lẫn kênh/nhóm/bot, và xếp username khớp
ngay đầu từ khoá lên trước; đây là cách duy nhất tìm được tài khoản khi **chưa biết đúng**
username (trang `t.me` chỉ tra được khi biết chính xác).

Các nguồn dữ liệu khác:

```bash
python3 keyword_usernames.py --keyword "đầu tư" --keyword "hoàn tiền" --demo
python3 keyword_usernames.py --keyword "telegram" --channel durov --limit 25
python3 keyword_usernames.py --keyword "chiến sự" --json nhom1.json --min-score 25
python3 keyword_usernames.py --keyword "chiến sự" --api          # + tìm chat theo từ khoá
python3 keyword_usernames.py --keyword "đầu tư" --demo --out usernames.json
```

Nguồn dữ liệu chọn được: `--demo`, `--channel`, `--seed-file`, `--json`, `--api`
(trộn nhiều nguồn cũng được). Kết quả xếp theo điểm nghi ngờ cao nhất, mỗi dòng ghi rõ
username được gặp vì `@nhắc` / `link t.me` / `nguồn forward` / `bài đăng trong kênh` /
`kết quả tìm chat`, kèm link mời nhóm kín nếu có. Lọc thêm bằng `--min-score`, `--top`.

| Cờ | Việc |
|---|---|
| `--text-only` | chỉ khớp từ khoá trong **nội dung bài**, bỏ tên/mã kênh (mặc định có tính tên kênh, nên từ khoá trùng tên kênh sẽ khớp mọi bài) |
| `--loose` | khớp **chuỗi con** thay vì ranh giới từ (`ib` khớp cả "herm**ib**ooks") — rộng nhưng nhiễu |
| `--record-noun bài\|tin` | cách gọi đơn vị trong câu thống kê, để in lại dữ liệu cũ dùng chữ "tin" |
| `--compact` / `--full` | in gọn mỗi username một dòng, hoặc in đầy đủ chi tiết (mặc định: gọn khi chỉ có kết quả tìm kiếm, đầy đủ khi có bài để chấm điểm) |

### Chức năng 2 — `username_info.py`: username → thông tin tài khoản

```bash
python3 username_info.py durov
python3 username_info.py durov @BotFather pythonchat vitalikbuterin
python3 username_info.py durov --limit 25 --out profile.json     # kèm thống kê bài đăng
.venv-api/bin/python username_info.py durov pythonchat --api     # thêm ID/điện thoại/nhóm chung
.venv-api/bin/python username_info.py hathanh --api --photo-dir anh  # tải ảnh đại diện
python3 username_info.py --from-json usernames.json              # nối tiếp chức năng 1
```

In loại tài khoản (Kênh / Nhóm / Bot / Người dùng), tên hiển thị, giới thiệu, quy mô, và
**ảnh đại diện** tải về `profile_photos/<username>.jpg` (`--photo-dir DIR`, tắt bằng
`--no-photo`). `--api` thì thêm **ID, verified, is_bot**, **số điện thoại** (chỉ khi là danh
bạ/công khai), **trạng thái hoạt động**, **ngày tạo**, **username khác**, **cờ tài khoản**
(Premium/lừa đảo/giả mạo/bị hạn chế), **lệnh bot**, **nhóm/kênh dùng chung** (`--common-limit N`)
và tra được **group kín** đã tham gia. Nếu tài khoản là kênh/nhóm, `--limit N` lấy thêm N bài
gần đây để in thống kê (điểm TB, cao nhất, số bài cần rà soát, mức rủi ro). `--from-json` đọc
danh sách username do chức năng 1 xuất ra.

## Chấm điểm

- 6 nhóm lừa đảo (Investment, Job, Loan, Giveaway, Impersonation, Shopping)
  + 4 nhóm tín hiệu (chuyển tiền, hoa hồng, điều hướng sang kênh/nhóm khác, thúc ép).
- Trích xuất URL, số điện thoại, số tài khoản, `@mention`, **link mời nhóm** `t.me/+...`,
  link preview. Thêm tín hiệu đặc thù Telegram: bài **forward** từ nguồn khác, ảnh/video
  kèm nội dung tài chính.
- Chuẩn hóa chữ thường + **bỏ dấu**, nên bắt được cả nội dung viết không dấu (`dautu`, `loinhuan`).
- Từ khoá khớp theo **ranh giới từ** (`(?<!\w)...(?!\w)`), nên `ib` không còn khớp trong
  "herm**ib**ooks", `stk` không khớp trong chuỗi base64, `công an` không khớp trong
  "phân công anh ta". Số tài khoản phải đứng tách khỏi dấu chấm/phẩy nên số tiền
  (`1.500.000`) không bị nhận nhầm.
- Điểm 0–100 → mức 1: `Normal` (<25), `Suspicious` (25–59), `High-risk` (≥60);
  mức 2: nhóm lừa đảo điểm cao nhất. Điểm chỉ là **tín hiệu hỗ trợ rà soát**.

## Kết quả đã kiểm chứng (chạy thật 2026-10-11)

| Lệnh | Kết quả |
|---|---|
| `--demo` | 17 bài → 7 Normal / 8 Suspicious / 2 High-risk (10/17 = 58.8% cần rà soát) |
| `--channel durov --limit 25` | 25 bài thật (2 trang), 10.4M người đăng ký, view 325K–6.87M, 11 bài có media, 6 link preview → group `@durov` **11/100 Normal** |
| `--channel baomoi --channel tintucvtv --limit 20` | 40 bài thật tiếng Việt (20 + 20) → `@baomoi` **11/100**, `@tintucvtv` **2/100** (đều Normal) |
| `--channel vnexpress` | báo lỗi rõ ràng (kênh không có bản xem trước công khai) |
| `--report posts_tg.json` / `--report posts_api.json` | in lại báo cáo + tự tính mục [5] (JSON cũ không có `groups` vẫn chạy) |
| `--user durov --user BotFather --user vitalikbuterin --user pythonchat --user khong_ton_tai_xyz_999` | phân loại đúng **Kênh / Bot / Người dùng / Nhóm / không tra được**; `@durov` 10 397 039 người đăng ký + ✔ đã xác minh, `@BotFather` 7 560 507 người dùng/tháng, `@pythonchat` 107 thành viên |
| `--keyword "chiến sự"` (bản API) | tìm được 7 username chat liên quan, ví dụ `@aiquochientrang`, `@bantronchiensu`, `@tintucchiensu247` |
| `--channel aiquochientrang --keyword "chiến sự"` | 5 bài lấy về + 7 username; `@aiquochientrang` gộp 3 vai trò (link t.me, bài đăng trong kênh, kết quả tìm chat) |
| `keyword_usernames.py --keyword "chiến sự" --json nhom1.json` | 1000 bài → `@aiquochientrang`; thêm `--text-only` → 0 username (không bài nào nhắc t.me) |
| `.venv-api/bin/python keyword_usernames.py --keyword "hathanh"` | 10 username kèm tên/loại: `@hathanh` Thanh Hà, `@hathanhtrading` Hà Thành Trading (Kênh, 213 người đăng ký), `@hathanhgrbot` (Bot)… |
| `username_info.py durov pythonchat` (web) | tải được ảnh thật (`durov.jpg` 10 425 byte, `pythonchat.jpg` 15 472 byte) + dòng "Số điện thoại: (cần --api…)" |
| `.venv-api/bin/python username_info.py BotFather sizedevchill hathanh --api` | BotFather: 12 lệnh bot; self: `Số điện thoại: 84981726384`, "truy cập lần cuối 10/10/2026 12:39 UTC"; `@hathanh`: "truy cập lần cuối 13/03/2026 14:07 UTC" |
| `.venv-api/bin/python username_info.py BOSSDZAIGK88 --api --limit 0` | khối **Nhóm chung**: "1 nhóm/kênh dùng chung — VÀO CÓ 1 CHIỆU 💴💶 (Nhóm, 65 784 thành viên)" |
| `python3 -m unittest discover -s tests` | **50 test PASS** (cả system Python lẫn `.venv-api/bin/python`) |
| `username_info.py durov @BotFather pythonchat vitalikbuterin` | Kênh ✔ / Bot / Nhóm 107 / Người dùng — phân loại đúng |

## Chạy test

```bash
python3 -m unittest discover -s tests -v      # không cần mạng, không cần Telethon
```

Test khoá lại: điểm 17 bài demo khớp `posts_tg.json`; công thức group tái tạo đúng 8 nhóm
demo + nhóm thật 1000 bài; các báo động giả nổi tiếng không còn; tin tức thật chỉ còn
1/1000 bài bị gắn cờ; bộ đọc HTML tách đúng bài/views/media/link preview; phân loại trang
`t.me/<username>` (kênh/nhóm/bot/người dùng/không tồn tại); chi tiết hồ sơ (`photo_url`,
`save_photo`, dòng số điện thoại/ảnh/nhóm chung/lệnh bot, helper `_status_line` /
`_usernames_of` / `_chat_row`); `--keyword` → `usernames` + `invite_links` và `--report`
đọc lại được.

Các test bổ sung trong [`tests/test_storage.py`](tests/test_storage.py) dùng DB tạm và
nguồn giả lập, không dùng phiên Telegram / DB thật: schema v2, WAL / khóa ngoại / quyền
`0600`, snapshot roundtrip, dedupe / lịch sử điểm, demo tách dữ liệu thật, ID ổn định /
alias / nâng cấp username web, hồ sơ / nhóm chung / kết quả / link mời, tìm kiếm Unicode
an toàn với SQL, rollback, import idempotent không chấm lại, từ chối credential / DB lạ /
schema tương lai, backup kể cả WAL, `--no-db` và tự persist từ các CLI. Đây là phần phủ
kiểm thử thêm, **không phải công bố tổng test mới hoặc lần chạy live mới**.

## Hai cách thu thập (chọn 1 hoặc dùng cả hai)

| | [`demo_telegram.py`](demo_telegram.py) | [`demo_telegram_api.py`](demo_telegram_api.py) |
|---|---|---|
| Kỹ thuật | đọc trang web công khai `t.me/s/<kênh>` | **Telegram API** qua thư viện **Telethon** |
| Cài thêm | không cần gì | `pip install telethon` |
| Đăng nhập | không | có (`--login` một lần) |
| api_id/api_hash | không | **có** (lấy tại my.telegram.org) |
| Kênh công khai | ✅ | ✅ |
| Group kín / kênh riêng tư | ❌ | ✅ (phải là thành viên) |
| Tìm kiếm toàn cục theo từ khóa | ❌ | ✅ |
| Tải ảnh/video | ❌ | ✅ (`--media-dir`) |
| OCR chữ trong ảnh | ❌ | ✅ (`--ocr`, tuỳ chọn) |
| Lượt xem | ✅ | ✅ |
| Forward / reply | ❌ (trang web không có) | ✅ |
| Chế độ bot (không cần SĐT) / `--watch` | ❌ | ✅ |
| Tra cứu `--user` (kênh/nhóm/bot/người dùng) | ✅ (trang `t.me/<username>`) | ✅ (thêm ID, verified, số điện thoại, trạng thái, nhóm chung, group kín) |
| Ảnh đại diện | ✅ (`og:image` → `--photo-dir`) | ✅ (`download_profile_photo`) |
| `--keyword` → liệt kê username liên quan | ✅ (quét bài đã lấy) | ✅ + tìm chat theo từ khoá |

### Ba cách đăng nhập của bản API

| Cách | Cần số điện thoại? | Cần OTP? | Làm được gì |
|---|---|---|---|
| `--login` (tài khoản người dùng) | có | có | đầy đủ: group kín, tìm kiếm toàn cục, tải media |
| `--bot-token` + `--watch` (**tài khoản bot**) | **không** | **không** | theo dõi tin nhắn **mới** trong nhóm bot đã được thêm |
| `--demo` | không | không | chạy thử toàn bộ pipeline bằng dữ liệu mẫu |

**Chế độ bot** — không cần số điện thoại, chỉ cần token từ @BotFather:

```bash
# 1. chat với @BotFather -> /newbot -> nhận token (dạng 123456789:AAH...)
# 2. thêm bot vào group cần theo dõi
python3 demo_telegram_api.py --setup-bot <token> --api-id <id> --api-hash <hash>  # kiểm tra + lưu + liệt kê group
python3 demo_telegram_api.py --watch @tennhom --watch-once                        # quét 1 lượt
python3 demo_telegram_api.py --bot-token <token> --watch @tennhom --interval 30 --cycles 10
```

Sau `--setup-bot`, token được lưu vào `json_folder/tg_api.json` nên các lần sau chỉ cần
`--watch @tennhom`. Giới hạn của bot (Telegram quy định, không phải lỗi code): **chỉ nhận
tin nhắn mới** kể từ khi được thêm vào nhóm, **không đọc được lịch sử cũ**, và **không
dùng được** `contacts.Search` — script tự cảnh báo và bỏ qua nếu truyền `--search` ở chế
độ bot. Bot vẫn cần `api_id`/`api_hash`; token không thay thế cặp này.

Các cờ khác của `--watch`: `--cycles N` (dừng sau N tin), `--interval` (giây, dùng cho
`--watch-once`), `--media-dir` (tải media kèm), `--ocr`.

Chạy bản API:

```bash
pip install telethon
python3 demo_telegram_api.py --login                     # đăng nhập 1 lần (hỏi OTP)
python3 demo_telegram_api.py --channel durov --limit 30  # kênh công khai
python3 demo_telegram_api.py --search "đầu tư lợi nhuận" # tìm kiếm toàn cục
python3 demo_telegram_api.py --demo                      # offline, không cần credential
```

Credential truyền qua `--api-id/--api-hash`, biến môi trường `TG_API_ID`/`TG_API_HASH`,
hoặc file `json_folder/tg_api.json`. **Bot token** qua `--bot-token`, `TG_BOT_TOKEN`,
hoặc khóa `bot_token` trong `json_folder/tg_api.json`. `tg_api.json` và file phiên
**không được commit** (đã có trong `.gitignore`).

## Đánh giá RỦI RO ở mức GROUP (không chỉ từng bài đăng)

Yêu cầu chính khi thu thập lịch sử chat là trả lời câu hỏi *"cả nhóm này có phải lừa đảo
không?"*, nên cả hai bản đều tính **điểm rủi ro cho từng group/kênh**, in ở mục **[5]**
của báo cáo và lưu trong khóa `groups` của file JSON:

```
[5] ĐIỂM RỦI RO THEO GROUP/KÊNH
    🔴 [ 81/100 | High-risk ] Đầu tư tài chính - Lợi nhuận 30%/tháng (@dautu.sieuloinhuan)
        Có dấu hiệu lừa đảo — cần kiểm tra kỹ — 3 bài, điểm TB 59.7, cao nhất 71,
        3 bài cần rà soát (100%)
        Lý do: 3/3 bài (100%) ở mức cần rà soát; loại hình nổi bật: Đầu tư / lợi nhuận cao (3 bài);
               có bài đạt 71/100 (High-risk); điểm trung bình 60/100
```

Công thức điểm group (0–100):

| Thành phần | Trọng số | Ý nghĩa |
|---|---|---|
| Điểm trung bình các bài | 60% | mức độ nặng trung bình của cả nhóm |
| Mật độ bài cần rà soát | tối đa 25 | tỷ lệ Suspicious + High-risk trên tổng số bài |
| Độ tập trung loại hình | tối đa 10 | cùng một loại lừa đảo lặp lại nhiều lần (mẫu số = **tổng số bài**) |
| Bài nặng nhất | tối đa 15 | bằng chứng nặng nhất trong nhóm |

Kèm theo **lý do** (bao nhiêu bài bị gắn cờ, loại hình nổi bật, có spam lặp nội dung
không) và `verdict`: *Có dấu hiệu lừa đảo* / *Nghi ngờ* / *Chưa thấy dấu hiệu bất thường*.
Trong JSON còn có `levels`, `categories`, `flagged_ratio`, `repeat_top_text` (số nội dung
khác nhau bị lặp ≥3 lần). Thứ tự `groups` là giảm dần theo `group_score`.

Ghi chú về chữ "bài"/"tin": bản web dùng "bài", bản API/`nhom1.json` dùng "tin" — đúng
theo dữ liệu lịch sử đã lưu, đổi được qua tham số `noun`.

### Credential được lưu tự động sau khi đăng nhập

Sau khi `--login` thành công, script **tự ghi `api_id`/`api_hash` vào `json_folder/tg_api.json`**
(quyền 600), nên các lệnh sau không phải nhập lại. Nếu chưa từng đăng nhập, có thể tự tạo
`json_folder/tg_api.json`:

```json
{"api_id": 1234567, "api_hash": "0123456789abcdef0123456789abcdef"}
```

**Phiên không phụ thuộc vào `api_id`**: đã kiểm chứng cùng một `tg_session_*.session` chạy
được với cả `api_id` công khai 2040 lẫn một `api_id` bất kỳ khác — vì `api_id`/`api_hash`
định danh **ứng dụng**, không phải tài khoản. Tuy vậy nên dùng `api_id` của chính bạn.

### Phiên lưu ở đâu, và phân biệt BOT với TÀI KHOẢN NGƯỜI DÙNG

`--session <tên>` đặt tên phiên; file luôn nằm cạnh script. Ghi tên ngắn (`tk-phu`)
thì script ghép tiền tố thành `tg_session_tk-phu`; ghi tên đầy đủ (`tg_session_tk-phu`) thì
dùng nguyên tên (không ghép thành `tg_session_tg_session_tk-phu`):

| Loại phiên | Tên file | Đọc lịch sử? | Lệnh tạo |
|---|---|---|---|
| Người dùng | `tg_session.session` hoặc `tg_session_<tên>.session` | ✅ | `--login` |
| Bot | `tg_bot_session.session` hoặc `tg_bot_<tên>.session` | ❌ | `--login-bot --bot-token <token>` |

**Quan trọng:** nếu file phiên **đã tồn tại**, script sẽ **tái sử dụng** và không hỏi đăng
nhập nữa — kể cả khi bạn gõ `--login`. Vì vậy nếu bạn từng đăng nhập bằng bot rồi muốn
chuyển sang tài khoản người dùng, phải **xoá file phiên cũ trước**:

```bash
rm tg_session_tk-phu.session                      # xoá phiên cũ
.venv-api/bin/python demo_telegram_api.py --login --session tk-phu \
    --api-id <id> --api-hash <hash>                # hỏi SĐT + OTP → tạo phiên người dùng
```

Khi tái sử dụng phiên, script luôn in rõ loại phiên (`BOT` hay `TÀI KHOẢN NGƯỜI DÙNG`).

**Bảo mật:** file `.session` chứa `auth_key` — ai có file là **chiếm được tài khoản**.
Project đặt `chmod 600` cho `json_folder/tg_api.json` và các file phiên, và đã `.gitignore` chúng.
Kiểm tra lại bằng `ls -l *.session json_folder/tg_api.json` (phải là `-rw-------`).

## Tìm group kín: `find_private_groups.py`

Group kín không có username thì Telegram không cho vào chỉ mục tìm kiếm, nên cách thực tế
là: kênh công khai (quảng cáo) → bóc link mời `t.me/+abc` → join → đọc lịch sử. Script chia
3 bước tách rời:

```bash
python3 find_private_groups.py --discover --channel @kenh_cong_khai --limit 500 --out found.json
python3 find_private_groups.py --discover --search "đầu tư lợi nhuận" --out found.json
python3 find_private_groups.py --discover --in-my-chats --out found.json
python3 find_private_groups.py --join --in found.json --max-join 5 --delay 20
python3 find_private_groups.py --crawl --in found.json --limit 1000 --out groups.json
```

⚠️ Join nhiều group lạ có thể khiến Telegram **hạn chế hoặc khoá tài khoản**. Mặc định
script rất dè dặt: tối đa **5 link**, nghỉ **20 giây** mỗi lần, dừng ngay khi gặp
`FloodWaitError`. Nên dùng tài khoản phụ. Bước `--discover` không join gì nên an toàn.
Hỗ trợ cả link mời mới (`t.me/+hash`) và cũ (`t.me/joinchat/hash`).

## Giới hạn & hướng mở rộng

- Trang `/s/` chỉ có ~20 bài mỗi trang và **chỉ kênh công khai**; group riêng tư hoặc kênh
  tắt preview thì không lấy được (script báo lỗi rõ ràng, không sập).
- Tìm kiếm toàn cục, group kín, tải media, OCR, `--watch` đã có ở backend API; các bước
  **audio → Whisper STT → OCR frame video** vẫn để trống trong `pipeline_status`.
- Điểm số là heuristic theo từ khoá: bài tin tức thật vẫn có thể bị gắn cờ (chấm lại 1000
  bài của `nhom1.json` bằng code hiện tại còn **1 bài**, id 157495, 30 điểm Suspicious do
  `công an` + link + forward) — luôn đọc `signals`/`reasons` để rà soát thủ công.
- `nhom1.json` là dữ liệu **lịch sử**: `analysis`/`groups` trong đó được tính bằng bộ từ
  khoá cũ (trước khi siết `thuế` và khớp ranh giới từ), nên chấm lại sẽ ra số hơi khác.
  `posts_tg.json` đã được tạo lại bằng code hiện tại.

## Tài liệu liên quan

- [`GIAI_THICH_CODE.md`](GIAI_THICH_CODE.md) — giải thích chi tiết từng phần của code,
  công thức chấm điểm, schema JSON và danh sách lỗi đã sửa.
