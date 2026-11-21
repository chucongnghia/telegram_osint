# Hướng dẫn đầy đủ: Telegram + sàng lọc nội dung kêu gọi hành vi phạm pháp

Bộ công cụ nhỏ để: **thu thập bài đăng Telegram → chấm mức kêu gọi/kích động hành vi phạm
pháp (0–100, ba mức) → tính mức cho cả group/kênh → in báo cáo**. Có hai backend thu thập
độc lập dùng chung một bộ chấm điểm. Điểm chỉ là **tín hiệu hỗ trợ rà soát**, không phải kết
luận pháp lý; người có thẩm quyền phải duyệt.

> Bắt đầu nhanh tại [README](<../README.md>). Các lệnh dưới đây chạy từ thư mục gốc
> project, qua [điểm vào chung](<../main.py>): `web`, `api`, `groups`, `search`,
> `profile`, `db`, `classify`, `video`, `image`, `download`, `dashboard`. Dùng [`python3 main.py COMMAND --help`](<../main.py>) để xem cờ.
> Không còn script CLI ở thư mục gốc; mã triển khai nằm trong [gói nguồn](<../src/telegram_demo/>).

## Mục lục

- [Dashboard trình duyệt: `python3 main.py dashboard --open`](<DASHBOARD.md>)
- [Phân tích video: FFmpeg → Whisper/thị giác → Jev](<VIDEO.md>)
- [Bảy nhãn Jev và ba mức heuristic](#bảy-nhãn-jev-và-ba-mức-heuristic)
- [Phạm vi và giới hạn](#phạm-vi-và-giới-hạn)
- [Phân loại văn bản bằng Jev](#bản-đầu-tin-nhắn-văn-bản--jev--sqlite)
  - [Opt-in Jev trong collector: cờ, ngân sách và watch](#opt-in-jev-ngay-trong-lệnh-thu-thập)
- [SQLite: lưu trữ, truy vấn và sao lưu](#lưu-trữ-sqlite-triển-khai-và-tra-cứu)
- [Bố cục mã và dữ liệu](#file)
- [Quy ước đường dẫn JSON](#quy-ước-file-json)
- [Cài đặt](#cài-đặt)
- [Thu thập web](#chạy-bản-web-không-cần-đăng-nhập)
- [Tìm username và xem hồ sơ](#hai-công-cụ-tách-riêng-mỗi-file-một-chức-năng)
- [Kiểm thử](#chạy-test)
- [Telegram API và đăng nhập](#hai-cách-thu-thập-chọn-1-hoặc-dùng-cả-hai)
- [Phiên đăng nhập](#phiên-lưu-ở-đâu-và-phân-biệt-bot-với-tài-khoản-người-dùng)
- [Tìm group kín](#tìm-group-kín-groups)

## Bản đầu: tin nhắn văn bản → Jev → SQLite

Phân loại **văn bản bài đăng** bằng model Jev thật, không phải bộ từ khoá trong analyzer.
Có hai cách opt-in: bật `--jev` trên lệnh thu thập, hoặc chạy `classify` riêng trên dữ liệu
đã lưu trong DB ([mã CLI](<../src/telegram_demo/cli/classify_messages.py>)). Không có opt-in
thì thu thập không gọi Jev. Chỉ dùng thư viện chuẩn Python; không xử lý âm thanh, ảnh hay
video trong bước text này, không tự tải video/media, không gọi vision và không truy cập
link trong tin nhắn.
Video cục bộ dùng lệnh `video` riêng; xem [hướng dẫn video](<VIDEO.md>).
Tải đúng một video từ link Telegram bằng `download`; xem [cú pháp và giới hạn](<VIDEO.md#tải-video-telegram-theo-link>).

### Opt-in Jev ngay trong lệnh thu thập

Năm collector `web`, `api`, `groups`, `search`, `profile` dùng chung các cờ Jev qua
`add_storage_arguments` trong [storage.py](<../src/telegram_demo/storage.py>). Mặc định vẫn
chỉ thu thập/lưu/chấm heuristic; phải truyền **`--jev`** mới bật bước phân loại văn bản.

| Cờ | Ý nghĩa |
|---|---|
| `--jev` | Opt-in bắt buộc để dùng Jev lúc thu thập; cần SQLite, không đi cùng `--no-db`. |
| `--jev-limit N` | Mặc định **10**, tối đa **1000** lần thử HTTP cho **toàn bộ một lần gọi lệnh**. |
| `--jev-model ALIAS` | Alias model, mặc định biến môi trường `JEV_MODEL`, nếu không có thì `jev-latest`. |
| `--jev-timeout SECONDS` | Timeout mỗi request, mặc định **60 giây**, tối đa **300 giây**. |
| `--jev-include-demo` | Cho phép xét cả bài thuộc namespace demo; mặc định bỏ qua demo. |
| `--jev-dry-run` | Chỉ lập kế hoạch: không gọi API, không ghi bất kỳ kết quả/trạng thái phân loại nào; **vẫn thu thập và persist JSON/SQLite**. |

Các modifier `--jev-*` **bị từ chối nếu thiếu `--jev`**. Chế độ `--report`, login và setup
(ví dụ `--login`, `--login-bot`, `--setup-bot`) không phân loại và **từ chối `--jev`**.
Thiếu `TYPESAFE_API_KEY` sẽ lỗi **trước khi thu thập**, trừ `--jev-dry-run`. Chương trình
đọc biến môi trường terminal, **không tự nạp `.env`**; key không phải credential Telegram.
Không ghi key vào tài liệu/chat/source/JSON, không truyền key thật trong dòng lệnh.

Ví dụ offline, không cần key hay mạng; dùng tên xuất mới cho mỗi lần chạy để không đụng
fixture/kết quả lịch sử:

```bash
python3 main.py web --demo --jev --jev-include-demo --jev-dry-run \
    --out "jev_demo_plan_$(date +%s%N).json"
```

Các ví dụ bên dưới **gọi Telegram và Jev thật, có thể tính phí**; chỉ chạy trên nguồn bạn
được phép xử lý. Nhập kín key vào môi trường terminal như hướng dẫn độc lập:

```bash
read -rsp 'TypeSafe API key: ' TYPESAFE_API_KEY; echo
export TYPESAFE_API_KEY

# Một kênh: tối đa 25 bài thu thập, nhưng chỉ tối đa 5 lần thử HTTP Jev.
python3 main.py web --channel durov --limit 25 --jev --jev-limit 5 \
    --out "jev_one_channel_$(date +%s%N).json"

# Nhiều kênh: --limit cho mỗi kênh, --jev-limit là tổng chung, KHÔNG phải 10/kênh.
python3 main.py web --channel baomoi --channel tintucvtv --limit 20 \
    --jev --jev-limit 10 --out "jev_many_channels_$(date +%s%N).json"

# Watch có giới hạn thời gian nhận tin (60 giây), rồi drain worker trước khi thoát.
# Cần Telethon + phiên Telegram có quyền đọc nhóm; không tải media trong ví dụ này.
.venv-api/bin/python main.py api --watch @tennhom --watch-once --interval 60 \
    --jev --jev-limit 5 --out "jev_watch_once_$(date +%s%N).json"

# Crawl group kín đã có quyền truy cập; đầu vào từ bước discovery trước đó.
# --crawl KHÔNG tự join: muốn tham gia phải là bước --join riêng, có chủ ý.
.venv-api/bin/python main.py groups --crawl --in found.json --limit 100 \
    --jev --jev-limit 10 --out "jev_private_groups_$(date +%s%N).json"

# Hai collector gọn cũng hỗ trợ; chỉ các bài thực sự lấy được mới được xét.
python3 main.py search --keyword "telegram" --channel durov --limit 10 --jev --jev-limit 3
python3 main.py profile durov --limit 5 --no-photo --jev --jev-limit 3

unset TYPESAFE_API_KEY
```

**Phạm vi và chi phí:** chỉ xét bài thuộc **run vừa lưu bởi lần gọi lệnh hiện tại** (có
thể có nhiều run), không tự lấy backlog cũ trong DB. Hồ sơ, mô tả tài khoản hay danh sách
username thuần metadata không được phân loại; `profile`/`search` phải có **bài đăng thật
trong dữ liệu thu thập** mới có ứng viên. `--jev-limit` đếm **lần thử request HTTP**, không
phải số kênh, số bài đọc hay số đô la. Cache/tin bị bỏ qua không tiêu tốn request. Cùng
text/rubric/backend/model yêu cầu đã thành công và tin quá dài có trạng thái terminal
được bỏ qua; không có cờ recheck trên collector. Các request tuần tự, **không tự retry**.

**Watch:** khi bật `--jev`, worker bất đồng bộ có hàng đợi hữu hạn xử lý HTTP ngoài event
loop, lưu/phân loại tin mới tăng dần thay vì chờ đến cuối. Dừng êm sẽ drain worker;
`--watch-once --interval 60` giới hạn thời gian nhận sự kiện, không hứa toàn tiến trình
thoát đúng giây thứ 60 vì còn chờ worker/request. Một ngân sách `--jev-limit` dùng chung
cho mọi nguồn và mọi sự kiện **suốt thời gian watch**, không reset mỗi vòng/sự kiện.

**Khi API lỗi:** lỗi đầu tiên được lưu ở `message_classifications` với trạng thái lỗi và
**nhãn `NULL`**, không giả nhãn bằng heuristic. Circuit mở cho **phần còn lại của lần gọi
lệnh**: ngừng gọi Jev, vẫn giữ dữ liệu JSON/SQLite và phân tích heuristic; lệnh trả mã
khác 0 lúc kết thúc. Log lỗi không in key hay thân nội dung nguồn. Kết quả Jev nằm riêng
trong `message_classifications`, **không nhúng vào JSON gốc, không sửa heuristic hay
điểm group**. Chỉ văn bản được gửi tới dịch vụ ngoài; không tự tải video/media hoặc bật
vision. Xác suất của model không được hiệu chuẩn (xem giới hạn dưới đây).

Phần chưa gọi được do hết ngân sách/circuit có thể xử lý sau bằng CLI độc lập, vẫn dùng
key trong môi trường. Lấy ID bằng `db runs`, thay `N` bằng ID run vừa lưu (nếu có nhiều
run, chọn từng ID); dùng cùng `--db` và model như lúc thu thập nếu có tuỳ chỉnh:

```bash
python3 main.py db runs --limit 10
python3 main.py classify run --run-id N --limit 10
# Muốn cố ý đánh giá lại cache/trạng thái terminal: chỉ CLI độc lập có --recheck.
python3 main.py classify run --run-id N --limit 10 --recheck
```

Kiểm chứng sau tích hợp (mốc lịch sử trước khi đổi trọng tâm): **344 test** trên venv API đều đạt; Python hệ thống đạt cùng suite
với 1 test cần Telethon được bỏ qua. Gồm [32 test runner](<../tests/test_collection_jev.py>)
và [28 test tích hợp](<../tests/test_collection_integration.py>): 5 collector, ngân sách
chung, cache, không giữ writer lock khi chờ HTTP, backlog 80+ sự kiện, worker lỗi và Ctrl+C.
Một smoke CLI → HTTP giả lập loopback → SQLite xác nhận giới hạn 2 request/lần gọi:
giữ 4 tin gốc và 4 kết quả sau 3 lần thu thập, lần thứ ba không gọi API nhờ cache;
không dùng dịch vụ thật.
Không có key hợp lệ trong môi trường kiểm chứng của agent nên **chưa thử tích hợp này
với API Jev thật**; không suy ra chất lượng hoặc độ chính xác từ test mock/offline.

### Bảy nhãn Jev và ba mức heuristic

Jev dùng đúng bảy nhãn sau (rubric văn bản `text-v2:sha256:…`; video `video-v3:sha256:…`;
ảnh `image-v2:sha256:…`). Hash trong tên rubric đổi khi câu hỏi/nhãn thay đổi, nên manifest
cũ phải `prepare` lại; không sửa evidence cũ.

| Nhãn | Ý nghĩa |
|---|---|
| `call_to_violence` | Kêu gọi/xúi giục tấn công, đánh, giết, đốt người hoặc dùng vũ khí. |
| `call_to_property_damage` | Kêu gọi đập phá, đốt tài sản, cướp hoặc phá hoại công trình. |
| `obstruct_officials` | Kêu gọi chặn, bao vây, tấn công người thi hành công vụ; cản trở cơ quan nhà nước. |
| `call_against_state` | Kêu gọi lật đổ chính quyền, đảo chính, chiếm cơ quan nhà nước. |
| `threat` | Đe doạ trực tiếp một người/tổ chức: giết, đánh, trả thù, biết nhà. |
| `criticism` | Phê bình chính sách, bày tỏ quan điểm, phản đối ôn hoà, tường thuật/cảnh báo/lên án. |
| `insufficient_information` | Thiếu ngữ cảnh để xác định có phải kêu gọi hay không. |

Ba mức heuristic giữ khoá cũ `High-risk` / `Suspicious` / `Normal` cho SQLite/dashboard,
nhưng hiển thị là **“Rủi ro cao”** (≥60), **“Cần xem xét”** (25–59) và **“Thông thường”**
(0–24). Điểm 0–100 chỉ là tín hiệu xếp hạng; mỗi kết quả kèm `evidence`, `requires_review`
và `context` (`reporting`/`neutral`). Nội dung tường thuật/cảnh báo bị hạ điểm, còn phê bình
hợp pháp phải rơi vào `criticism`, không bị coi là kêu gọi.

Rubric có phiên bản và hướng dẫn không làm theo chỉ dẫn nằm trong tin nhắn. Đây là
**sàng lọc tín hiệu**, không phải kết luận pháp lý. Xác suất/confidence của Jev **không
được xem là độ chính xác đã hiệu chuẩn**, không dùng một ngưỡng cố định để tự kết tội.

### Chạy độc lập bằng API key TypeSafe

API theo [tài liệu chính thức](https://docs.typesafe.ai/introduction/quickstart.md),
mặc định `https://api.typesafe.ai/v1/systemone`. Key này **khác** Telegram API ID/hash:

```bash
# Thu thập trước bằng các CLI hiện có, hoặc nhập JSON lịch sử:
python3 main.py db import-json --all

# Xem ứng viên, không gọi mạng / không ghi kết quả phân loại:
python3 main.py classify run --limit 10 --dry-run

# Nhập kín vào biến môi trường, không ghi key vào source/JSON:
read -rsp 'TypeSafe API key: ' TYPESAFE_API_KEY; echo
export TYPESAFE_API_KEY
python3 main.py classify run --limit 10
unset TYPESAFE_API_KEY

# Xem lịch sử offline:
python3 main.py classify results --limit 20
python3 main.py classify results --label call_to_violence
python3 main.py classify --db data/thu_nghiem.sqlite3 run --limit 10 --dry-run
```

Với `classify`, cờ `--db PATH` đứng **sau `classify`, trước subcommand** (ví dụ `run`); mặc định dùng DB chung và
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
python3 main.py classify prepare --limit 10 --out jev_requests.json
# Agent gọi jev_ask cho từng item với đúng state, questions và model trong manifest;
# chép NGUYÊN VĂN output công cụ vào results, không tự tạo nhãn.
python3 main.py classify apply --requests jev_requests.json --results jev_results.json
python3 main.py classify results
```

Manifest và results có tên trần được đặt trong [`json_folder/`](<../json_folder/>), tạo mới
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

Mốc lịch sử trước khi đổi trọng tâm: đã smoke-test Jev thật qua công cụ DSH trên **6 tin
tổng hợp** theo rubric cũ (khi đó còn năm nhãn), gồm cảnh báo có trích dẫn và thử chèn chỉ
dẫn; kết quả lưu/replay thành công với model `jev-1.14.0`. **Chưa smoke lại bộ bảy nhãn mới
với Jev thật**; đây không phải phép đo độ chính xác trên dữ liệu thực tế. Nhánh HTTP được
test offline bằng mock; chạy HTTP có xác thực cần API key của bạn.

Mốc kiểm thử trước khi sắp xếp thư mục: bộ **139 test** bản Jev chạy bằng venv API đều thành công; cùng bộ test
trên Python hệ thống thành công với 1 test cần Telethon được bỏ qua. Các test không
gọi dịch vụ có phí. Test hồi quy bao gồm tiến độ sau tin quá dài và giới hạn byte
UTF-8 của manifest.
DB hiện có đã được sao lưu tại [`pre-jev-v2-backup.sqlite3`](<../data/pre-jev-v2-backup.sqlite3>)
trước khi nâng cấp (artifact cục bộ, không đi kèm bản clone mới); các bảng dữ liệu cũ giữ nguyên, không tự gửi tin lịch sử lên Jev.

## Lưu trữ SQLite: triển khai và tra cứu

Kết quả thu thập thành công được **tự lưu vào SQLite**, kể cả các lệnh tra username / tìm
keyword **không có `--out`**. JSON vẫn được xuất theo cách cũ, với quy ước tên trần nằm
trong [`json_folder/`](<../json_folder/>); SQLite không thay thế hay xoá các bản xuất đó.
SQLite dùng `sqlite3` của thư viện chuẩn Python: **không cần pip, không cần máy chủ DB**.
Telethon chỉ cần khi dùng backend API như trước.

### Đường dẫn và vận hành

- Mọi CLI thu thập / tra cứu dùng chung `--db PATH`, biến môi trường `TELEGRAM_DB_PATH`,
  và `--no-db` để tắt lưu DB cho lần chạy đó. `--db` ưu tiên hơn biến môi trường.
  Ngoại lệ: bật `--jev` bắt buộc lưu SQLite, nên kết hợp `--jev --no-db` bị từ chối.
- Mặc định là [`data/telegram.sqlite3`](<../data/telegram.sqlite3>), được dựng thành **đường dẫn tuyệt đối từ gốc
  project (`PROJECT_ROOT`)**, không phụ thuộc thư mục đang đứng khi gọi lệnh.
- Tương tự, đường dẫn mặc định của JSON, cấu hình, phiên và ảnh đại diện đều neo ở gốc
  project. Đường dẫn tuỳ chọn có thư mục do bạn truyền vẫn tương đối với **cwd của người
  gọi**, hoặc dùng nguyên đường dẫn tuyệt đối; riêng tên JSON trần theo quy ước bên dưới.
- DB là **plaintext, không mã hoá**, đặt quyền `0600`; thư mục [`data/`](<../data/>) được git-ignore.
  Dữ liệu bài đăng, hồ sơ và link mời có thể nhạy cảm: bảo vệ cả bản sao lưu, không công
  khai thư mục dữ liệu. Không nhập credential / session key vào kho dữ liệu này;
  [`json_folder/tg_api.json`](<../json_folder/tg_api.json>) bị bỏ qua khi import hàng loạt.
- File phiên Telethon là **kho riêng**, không phải DB thu thập và không được nhập vào đó.
- Cấu hình SQLite dùng **WAL**, `busy_timeout = 10000` ms (10 giây), ghi theo **batch
  transaction**. SQLite chỉ có **một writer tại một thời điểm**; tránh nhiều tiến trình
  ghi kéo dài cùng lúc. Sao lưu DB đang dùng bằng lệnh `backup`, không chỉ chép file DB
  chính khi còn dữ liệu trong WAL.

```bash
python3 main.py web --demo --db data/thu_nghiem.sqlite3
python3 main.py search --demo --keyword 'đầu tư'  # tự lưu dù không có --out
python3 main.py profile durov --no-db                 # không lưu DB lần này
TELEGRAM_DB_PATH=/tmp/telegram.sqlite3 python3 main.py web --demo
```

### Quản trị bằng `db` (offline, không gọi Telegram)

**Cờ `--db` phải đứng sau `db`, trước subcommand** (ví dụ `stats`); các ví dụ không ghi cờ này dùng DB
mặc định (hoặc `TELEGRAM_DB_PATH`). Chạy từ thư mục project:

```bash
python3 main.py db init
python3 main.py db import-json --all                   # quét JSON, bỏ qua tg_api.json
python3 main.py db import-json posts_tg.json            # đọc json_folder/posts_tg.json
python3 main.py db stats
python3 main.py db runs --limit 20
python3 main.py db messages --keyword 'đầu tư' --username dautu.sieuloinhuan --limit 10
python3 main.py db profiles durov
python3 main.py db results RUN_ID
python3 main.py db export RUN_ID --out sqlite_export.json
python3 main.py db backup data/backup.sqlite3
python3 main.py db --db data/thu_nghiem.sqlite3 stats
```

Thay `RUN_ID` bằng ID lấy từ `runs`. `export` giữ quy ước JSON: tên trần trong ví dụ là
`json_folder/sqlite_export.json`; **không ghi đè** file có sẵn. `backup` yêu cầu **đích
mới**, không dùng lại file backup đã tồn tại. Import lại **đúng cùng payload** là idempotent
nhờ SHA256, không tạo bản run trùng và **không chấm lại điểm lịch sử**.

### Hiểu dữ liệu đã lưu

[`storage.py`](<../src/telegram_demo/storage.py>) quản lý `Database` schema v4; [`database.py`](<../src/telegram_demo/cli/database.py>) là CLI
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
[`initial-import-backup.sqlite3`](<../data/initial-import-backup.sqlite3>).

## File

Các liên kết dưới đây trỏ tới mã triển khai, **không phải lệnh gọi trực tiếp**.
Chạy qua dispatcher với tên lệnh ở cột mô tả.

| File | Việc |
|---|---|
| [main.py](<../main.py>) | Điểm vào chung: `web`, `api`, `groups`, `search`, `profile`, `db`, `classify`, `video`, `image`, `download`, `dashboard`. |
| [paths.py](<../src/telegram_demo/paths.py>) | Neo đường dẫn mặc định theo `PROJECT_ROOT`. |
| [`keyword_usernames.py`](<../src/telegram_demo/cli/keyword_usernames.py>) | `search` — **Chức năng 1, tách riêng**: nhập keyword → in danh sách username liên quan. |
| [`username_info.py`](<../src/telegram_demo/cli/username_info.py>) | `profile` — **Chức năng 2, tách riêng**: nhập username → in thông tin tài khoản đó. |
| [`analyzer.py`](<../src/telegram_demo/analyzer.py>) | **Nguồn chân lý dùng chung**: từ điển, trọng số, regex, `analyze()`, `group_summary()`, `print_report()`, `write_json()`, `username_payload()` / `related_usernames()` (`--keyword`), `make_profile()` / `print_profile()` (`--user`), dữ liệu mẫu `demo_posts()`. Chỉ thư viện chuẩn. |
| [`incitement.py`](<../src/telegram_demo/incitement.py>) | Rubric `incitement-v1:2026-11-21`, bảy nhãn và ngưỡng ba mức kêu gọi/kích động hành vi phạm pháp; trích `evidence`, `context`, `requires_review`. |
| [`demo_telegram.py`](<../src/telegram_demo/cli/demo_telegram.py>) | `web` — Backend **web**: đọc trang công khai `https://t.me/s/<kênh>` (và `https://t.me/<username>` cho `--user`). **Chỉ thư viện chuẩn** — không cần cài gì, không cần API key, không cần đăng nhập. |
| [`demo_telegram_api.py`](<../src/telegram_demo/cli/demo_telegram_api.py>) | `api` — Backend **Telegram API (Telethon)**: group kín, tìm kiếm toàn cục, tra cứu username/ID, tìm chat theo từ khoá, tải media, OCR, chế độ bot, `--watch`. |
| [`find_private_groups.py`](<../src/telegram_demo/cli/find_private_groups.py>) | `groups` — Tìm & đọc **group kín** qua link mời, chia 3 bước `--discover` / `--join` / `--crawl`. |
| [`storage.py`](<../src/telegram_demo/storage.py>) | `Database` schema v4: snapshot run, thực thể / alias, message, phân tích và kết quả tra cứu. |
| [`database.py`](<../src/telegram_demo/cli/database.py>) | `db` — CLI SQLite: init, import JSON, thống kê, truy vấn, export run và backup. |
| [`classify_messages.py`](<../src/telegram_demo/cli/classify_messages.py>) | `classify` — CLI văn bản → Jev → SQLite: run / prepare / apply / results. |
| [`text_classifier.py`](<../src/telegram_demo/text_classifier.py>) | Rubric có phiên bản, chọn ứng viên/cache, lưu bằng chứng và lịch sử phân loại. |
| [`jev_client.py`](<../src/telegram_demo/jev_client.py>) | HTTP client stdlib cho API TypeSafe; xác thực response và che lỗi nhạy cảm. |
| [`collection_jev.py`](<../src/telegram_demo/collection_jev.py>) | Hook phân loại theo run cho collector/watch; cache, ngân sách chung, dry-run và ngừng gọi khi lỗi. |
| [`tests/test_text_classifier.py`](<../tests/test_text_classifier.py>) / [`tests/test_jev_client.py`](<../tests/test_jev_client.py>) | Test classifier, migration/cache/bridge và HTTP offline, không cần key. |
| [`tests/test_regression.py`](<../tests/test_regression.py>) | Test hồi quy offline; mốc 50 test là số liệu kiểm chứng lịch sử 2026-10-11, không phải tổng hiện tại. |
| [`tests/test_storage.py`](<../tests/test_storage.py>) | Test SQLite offline: schema, snapshot / identity, import / truy vấn / export / backup và persist từ CLI. |
| [`json_folder/`](<../json_folder>) | **Mọi file `.json` của project** — dữ liệu mẫu, kết quả chạy và cả [`tg_api.json`](<../json_folder/tg_api.json>) (xem "Quy ước file JSON"). |
| [`json_folder/posts_tg.json`](<../json_folder/posts_tg.json>) | Kết quả `--demo` của bản web (17 bài mẫu, có `groups`). |
| [`json_folder/posts_api.json`](<../json_folder/posts_api.json>) | Kết quả bản API (100 bài, `backend: telethon`). |
| [`json_folder/nhom1.json`](<../json_folder/nhom1.json>) | Dữ liệu **thật** 1000 bài của group `@aiquochientrang` (`backend: telethon-user`). |
| [`json_folder/real_tg.json`](<../json_folder/real_tg.json>) / [`json_folder/real_tg_vn.json`](<../json_folder/real_tg_vn.json>) | Dữ liệu thật `@durov` / `@baomoi`, `@tintucvtv`. |
| [`json_folder/found.json`](<../json_folder/found.json>), [`json_folder/found_groups.json`](<../json_folder/found_groups.json>) | Kết quả bước `--discover` của lệnh `groups`. |
| [`channels.txt`](<../config/channels.txt>) | Danh sách kênh mẫu cho `--seed-file`. |

## Quy ước file JSON

**Từ nay mọi file `.json` nằm trong [`json_folder/`](<../json_folder/>)** — kể cả [`tg_api.json`](<../json_folder/tg_api.json>) (credential, quyền 600):

```
json_folder/
├── tg_api.json          # credential (api_id/api_hash/bot_token)
├── nhom1.json           # dữ liệu mẫu / lịch sử
├── posts_tg.json  posts_api.json  posts_web.json
├── real_tg.json   real_tg_vn.json
└── found.json     found_groups.json
```

Các cờ `--out`, `--report`, `--json`, `--in`, `--from-json` nhận **tên trần** và tự hiểu là
file trong [`json_folder/`](<../json_folder/>). Quy ước này cũng áp dụng cho manifest/results
của `classify` và file credential mặc định. Ví dụ:

```bash
python3 main.py web --report posts_tg.json        # = json_folder/posts_tg.json
python3 main.py search --json nhom1.json --keyword "chiến sự"
python3 main.py profile durov --out profile.json      # = json_folder/profile.json
```

Muốn ghi ra chỗ khác thì truyền đường dẫn có thư mục (hoặc tuyệt đối) — script giữ nguyên:
`--out /tmp/ketqua.json`, `--out baocao/posts.json`. Hàm dùng chung nằm ở cuối
[`analyzer.py`](<../src/telegram_demo/analyzer.py>): `json_dir()`, `json_path(name)`, `resolve_json(path)`.

## Cài đặt

Bản **web** chạy được ngay bằng Python 3.10+ (không cần cài gì):

```bash
python3 main.py web --demo
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
> Đây là cách venv cục bộ từng được dựng (Python 3.13.12 + Telethon 1.45.0);
> không phải yêu cầu pin phiên bản cho mọi máy. Chỉ chạy script cài đặt từ nguồn bạn tin cậy.

OCR dùng **Tesseract + vie/eng + FFmpeg/ffprobe** cục bộ, không cần Pillow/pytesseract,
không tự đổi engine/tải weights. `api --ocr` cần `--media-dir` và nguồn thật; thiếu runtime
báo lỗi preflight thay vì bỏ qua ngầm. Có lệnh `image` riêng và `video analyze --ocr`.
Xem [hướng dẫn ảnh/OCR, dashboard và kiểm chứng](<IMAGE_OCR.md>).

## Chạy bản web (không cần đăng nhập)

```bash
python3 main.py web --demo                       # offline, dữ liệu mẫu (không cần mạng)
python3 main.py web --channel durov --limit 25   # kênh công khai (chạy thật)
python3 main.py web --channel baomoi --channel tintucvtv --limit 20
python3 main.py web --seed-file config/channels.txt     # mỗi dòng 1 kênh hoặc link t.me
python3 main.py web --user durov --user BotFather  # tra cứu theo username
python3 main.py web --channel durov --keyword "telegram"   # từ khoá -> username liên quan
python3 main.py web --report posts_tg.json       # in lại báo cáo từ JSON đã lưu
```

Không truyền nguồn nào thì mặc định chạy `--demo`. `--channel` nhận `durov`, `@durov`,
`t.me/durov`, `https://t.me/s/durov` hoặc ID số (`-100…`); `--limit` là số bài tối đa **cho mỗi kênh**
(mặc định 20). Thêm `--include-linked-groups` để lấy cả tin mới nhất của **group thảo luận gắn với
kênh** (Telegram gọi là linked chat): mỗi kênh có group sẽ được lấy thêm `--limit` tin từ group, nhãn
nguồn là username/tên group và trường `linked_group_of` cho biết group thuộc kênh nào. Kênh không có
group, hoặc tài khoản không truy cập được, chỉ in cảnh báo và bỏ qua. `--out` mặc định là
[`json_folder/posts_web.json`](<../json_folder/posts_web.json>) (cố ý khác
[`posts_tg.json`](<../json_folder/posts_tg.json>) để không ghi đè file kết quả mẫu).

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
python3 main.py web --user durov --user BotFather --user vitalikbuterin
python3 main.py web --user pythonchat --limit 5      # nhóm: chỉ tra thông tin
python3 main.py api --user durov --user @tennhom # bản API: thêm ID/verified
```

- Bản **web** đọc `https://t.me/<username>` và tự phân loại: `subscribers` → **Kênh**,
  `members` → **Nhóm**, `monthly users`/`Start Bot` → **Bot**, `You can contact @x` →
  **Người dùng**. Không có mô tả + không có khối thông tin → báo **không tra được**
  (không tồn tại, riêng tư, hoặc không có bản xem trước).
- Bản **API** dùng `get_entity()` nên tra được cả **ID**, `verified`, `is_bot`,
  `participants_count`, và group kín mà tài khoản đã tham gia.
- **Chi tiết hơn ở bản API** (`--api`): **số điện thoại** (chỉ hiện khi đối tượng là danh bạ
  hoặc họ để công khai), **trạng thái hoạt động** ("đang online", "truy cập lần cuối …"),
  **ngày tạo** kênh/nhóm, **các username khác**, **cờ tài khoản** (Premium / **cờ do Telegram
  gắn** như lừa đảo, giả mạo, bị hạn chế / liên hệ hai chiều), **lệnh bot**, và **nhóm/kênh dùng chung**
  (`GetCommonChats`), chỉ trong phạm vi phiên đang đăng nhập nhìn thấy được.
- **Ảnh đại diện** được tải về [media/profile_photos](<../media/profile_photos/>), theo mẫu `<username>.jpg` (bản web lấy từ `og:image`,
  bản API qua `download_profile_photo`). Đổi thư mục bằng `--photo-dir DIR`, tắt bằng
  `--no-photo`. Giới hạn danh sách nhóm chung bằng `--common-limit N`.
- Nếu đối tượng là **kênh/nhóm**, script còn lấy luôn bài gần đây và in thống kê
  (số bài, điểm TB, cao nhất, mức rủi ro) — tức mục `[0] THÔNG TIN TÀI KHOẢN TRA CỨU`.
- Kết quả được lưu vào khóa `profiles` trong file JSON, `--report` đọc lại và in tiếp.

> Chỉ cần đúng việc "username → thông tin"? Dùng bản gọn
> lệnh `profile` ([mã triển khai](<../src/telegram_demo/cli/username_info.py>); nhận nhiều username, `--api`, `--from-json`,
> `--photo-dir`, `--no-photo`, `--common-limit`).

## Nhập keyword → liệt kê username liên quan: `--keyword`

Nhập một hoặc nhiều từ khoá, script quét các bài đã thu thập rồi liệt kê **username có
thể liên quan** (mục `[6]`), kèm link mời nhóm kín gặp trong các bài đó:

```bash
python3 main.py web --channel durov --keyword "telegram"
python3 main.py web --demo --keyword "đầu tư" --keyword "hoàn tiền"
python3 main.py web --report posts_web.json --keyword "hoàn tiền"   # tra trong JSON đã lưu
python3 main.py api --keyword "chiến sự"          # thêm: tìm chat theo từ khoá
```

Mỗi dòng cho biết username được gặp **vì lý do gì**: `@nhắc` (được mention trong bài),
`link t.me` (link trong bài), `nguồn forward`, `bài đăng trong kênh`, `kết quả tìm chat`
(chỉ bản API). Kèm **mức kêu gọi cao nhất** của các bài chứa username đó, nên dễ ưu tiên
rà soát. Mục `[6]` chỉ hiện khi bạn truyền `--keyword`, hoặc khi mở lại file JSON đã lưu
sẵn kết quả (dùng `--report`). Nếu chỉ truyền `--keyword` mà không có nguồn nào, bản web
chạy dữ liệu mẫu offline để minh hoạ.

- Bản **API** còn dùng `contacts.SearchRequest` để tìm **chat** theo từ khoá (chỉ trong
  các chat tài khoản đã tham gia; tài khoản bot không dùng được, script tự cảnh báo).
- Kết quả nằm trong các khóa `keywords`, `usernames` (và `invite_links`) của file JSON.

> Chỉ cần đúng việc "keyword → username"? Dùng bản gọn
> lệnh `search` ([mã triển khai](<../src/telegram_demo/cli/keyword_usernames.py>); có thêm `--text-only`, `--min-score`, `--top`).

## Hai công cụ tách riêng (mỗi file một chức năng)

Hai lệnh `search` và `profile` làm đúng **một việc**, gọn hơn `web`/`api`
(vốn thu thập + chấm điểm + in báo cáo đầy đủ):

### Chức năng 1 — `search`: keyword → danh sách username

Cách dùng ngắn nhất: **chỉ cần từ khoá**, script tự tìm trên Telegram rồi in danh sách gọn
`@username — Tên (Loại)`:

```bash
.venv-api/bin/python main.py search --keyword "hathanh"
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
tức chạy bằng [Python trong venv API](<../.venv-api/bin/python>)). Muốn xem định dạng kết quả mà không cần mạng thì
thêm `--demo`. Cách này tìm được cả **người dùng** lẫn kênh/nhóm/bot, và xếp username khớp
ngay đầu từ khoá lên trước; đây là cách duy nhất tìm được tài khoản khi **chưa biết đúng**
username (trang `t.me` chỉ tra được khi biết chính xác).

Các nguồn dữ liệu khác:

```bash
python3 main.py search --keyword "đầu tư" --keyword "hoàn tiền" --demo
python3 main.py search --keyword "telegram" --channel durov --limit 25
python3 main.py search --keyword "chiến sự" --json nhom1.json --min-score 25
python3 main.py search --keyword "chiến sự" --api          # + tìm chat theo từ khoá
python3 main.py search --keyword "đầu tư" --demo --out usernames.json
```

Nguồn dữ liệu chọn được: `--demo`, `--channel`, `--seed-file`, `--json`, `--api`
(trộn nhiều nguồn cũng được). Kết quả xếp theo mức kêu gọi cao nhất, mỗi dòng ghi rõ
username được gặp vì `@nhắc` / `link t.me` / `nguồn forward` / `bài đăng trong kênh` /
`kết quả tìm chat`, kèm link mời nhóm kín nếu có. Lọc thêm bằng `--min-score`, `--top`.

| Cờ | Việc |
|---|---|
| `--text-only` | chỉ khớp từ khoá trong **nội dung bài**, bỏ tên/mã kênh (mặc định có tính tên kênh, nên từ khoá trùng tên kênh sẽ khớp mọi bài) |
| `--loose` | khớp **chuỗi con** thay vì ranh giới từ (`ib` khớp cả "herm**ib**ooks") — rộng nhưng nhiễu |
| `--record-noun bài\|tin` | cách gọi đơn vị trong câu thống kê, để in lại dữ liệu cũ dùng chữ "tin" |
| `--compact` / `--full` | in gọn mỗi username một dòng, hoặc in đầy đủ chi tiết (mặc định: gọn khi chỉ có kết quả tìm kiếm, đầy đủ khi có bài để chấm điểm) |

### Chức năng 2 — `profile`: username hoặc ID → thông tin tài khoản

```bash
python3 main.py profile durov
python3 main.py profile durov @BotFather pythonchat vitalikbuterin
python3 main.py profile durov --limit 25 --out profile.json     # kèm thống kê bài đăng
.venv-api/bin/python main.py profile durov pythonchat --api     # thêm ID/điện thoại/nhóm chung
.venv-api/bin/python main.py profile hathanh --api --photo-dir anh  # tải ảnh đại diện
python3 main.py profile --from-json usernames.json              # nối tiếp chức năng 1

# Tra bằng ID số (Telegram API): ID người dùng/bot, hoặc marked ID kênh/nhóm (-100...)
.venv-api/bin/python main.py profile 93372553 --api --limit 0 --no-photo
.venv-api/bin/python main.py profile -1002505512450 --api --limit 0 --no-photo
```

In loại tài khoản (Kênh / Nhóm / Bot / Người dùng), tên hiển thị, giới thiệu, quy mô, và
**ảnh đại diện** tải về [media/profile_photos](<../media/profile_photos/>), theo mẫu `<username>.jpg` (`--photo-dir DIR`, tắt bằng
`--no-photo`). `--api` thì thêm **ID, verified, is_bot**, **số điện thoại** (chỉ khi là danh
bạ/công khai), **trạng thái hoạt động**, **ngày tạo**, **username khác**, **cờ tài khoản**
(Premium / cờ do Telegram gắn: lừa đảo, giả mạo, bị hạn chế), **lệnh bot**, **nhóm/kênh dùng chung** (`--common-limit N`)
và tra được **group kín** đã tham gia. Nếu tài khoản là kênh/nhóm, `--limit N` lấy thêm N bài
gần đây để in thống kê (điểm TB, cao nhất, số bài cần rà soát, mức rủi ro). `--from-json` đọc
danh sách username do chức năng 1 xuất ra.

**Tra bằng ID:** mục chỉ gồm chữ số (hoặc dạng `-100...` của kênh/nhóm) được hiểu là ID
Telegram, không phải username (username luôn bắt đầu bằng chữ cái). ID chỉ tra được bằng
`--api` vì trang `t.me` không có route theo ID; chế độ web sẽ báo rõ và bỏ qua mục đó. ID
hữu ích khi tài khoản **không có username công khai**, hoặc khi bạn có ID từ dữ liệu khác.
Tài khoản chưa từng tương tác và không công khai vẫn có thể báo "Không tra được" — đó là
giới hạn quyền truy cập của Telegram, không phải lỗi cú pháp.

## Chấm điểm

Heuristic (`incitement.py`, rubric `incitement-v1:2026-11-21`) chỉ xếp hạng và trích bằng
chứng; kết luận pháp lý do con người quyết.

- Bốn nhóm hành động: kêu gọi bạo lực, kêu gọi phá hoại tài sản, chống người thi hành công
  vụ, kêu gọi chống chính quyền — cộng tín hiệu **đe doạ** có cấu trúc.
- Tín hiệu ngữ cảnh chỉ cộng điểm khi đã có nhóm hành động: lời kêu gọi/hiệu triệu, dấu hiệu
  chuẩn bị vũ khí/công cụ, thời điểm, địa điểm, mục tiêu là lực lượng/cơ quan cụ thể.
- Ngữ cảnh tường thuật/cảnh báo (tin giả, điều tra, lên án, cảnh báo) bị **hạ điểm** và đánh
  dấu `context="reporting"`; câu phủ định hành vi cũng bị trừ điểm. Nhờ vậy bài phân tích về
  hành vi không bị chấm như lời kêu gọi thật.
- Trích xuất URL, số điện thoại, số tài khoản, `@mention`, **link mời nhóm** `t.me/+...`,
  link preview. Thêm tín hiệu đặc thù Telegram: bài **forward** từ nguồn khác, ảnh/video.
- Chuẩn hóa chữ thường + **bỏ dấu**, nên bắt được cả nội dung viết không dấu (`keu goi`, `dap pha`).
- Từ khoá khớp theo **ranh giới từ** (`(?<!\w)...(?!\w)`), nên `ib` không còn khớp trong
  "herm**ib**ooks", `stk` không khớp trong chuỗi base64, `công an` không khớp trong
  "phân công anh ta". Số tài khoản phải đứng tách khỏi dấu chấm/phẩy nên số tiền
  (`1.500.000`) không bị nhận nhầm.
- Điểm 0–100 → ba mức: `Normal` (“Thông thường”, <25), `Suspicious` (“Cần xem xét”, 25–59),
  `High-risk` (“Rủi ro cao”, ≥60); nhóm chính là nhóm hành động điểm cao nhất. Kết quả kèm
  `evidence`, `requires_review`, `context` và phiên bản rubric. Khoá cũ `scam_score` /
  `scam_category_vi` vẫn được ghi để SQLite/dashboard cũ đọc được, nhưng ý nghĩa là **mức
  kêu gọi**, không phải mức lừa đảo. Điểm chỉ là **tín hiệu hỗ trợ rà soát**.

## Phạm vi và giới hạn

- Chỉ dữ liệu công khai mà bạn có quyền xử lý; không tự động báo cáo, không gửi cảnh báo và
  không kết luận thay cơ quan có thẩm quyền.
- Máy chỉ xếp hạng và trích `evidence`; mọi kết quả có `requires_review`. Người có thẩm quyền
  duyệt và có thể sửa/ghi đè quyết định khi xử lý.
- Phân biệt phê bình hợp pháp (`criticism`) với kêu gọi hành động; nội dung tường thuật/cảnh
  báo bị hạ điểm và gắn `context="reporting"`, không bị coi là kêu gọi.
- Lịch sử phân loại (batch, rubric, model, thời điểm) được lưu để audit; `--recheck` tạo bản
  ghi mới thay vì ghi đè kết quả cũ.

## Kết quả lịch sử (chạy thật 2026-10-11, trước khi sắp xếp thư mục)

Các ví dụ đã đổi sang cú pháp dispatcher mới để dễ tra cứu; số liệu bên dưới là mốc
cũ, không phải tuyên bố đã chạy lại CLI hay kiểm chứng live sau lần tổ chức này. Riêng
dòng `--demo` đã đo lại offline bằng rubric kêu gọi hiện hành (không gọi mạng).

| Lệnh | Kết quả |
|---|---|
| `--demo` | 17 bài, rubric kêu gọi hiện hành → 9 Normal / 3 Suspicious / 5 High-risk (8/17 = 47.1% cần rà soát); bản lịch sử trước đổi trọng tâm là 7/8/2 |
| `--channel durov --limit 25` | 25 bài thật (2 trang), 10.4M người đăng ký, view 325K–6.87M, 11 bài có media, 6 link preview → group `@durov` **11/100 Normal** |
| `--channel baomoi --channel tintucvtv --limit 20` | 40 bài thật tiếng Việt (20 + 20) → `@baomoi` **11/100**, `@tintucvtv` **2/100** (đều Normal) |
| `--channel vnexpress` | báo lỗi rõ ràng (kênh không có bản xem trước công khai) |
| `--report posts_tg.json` / `--report posts_api.json` | in lại báo cáo + tự tính mục [5] (JSON cũ không có `groups` vẫn chạy) |
| `--user durov --user BotFather --user vitalikbuterin --user pythonchat --user khong_ton_tai_xyz_999` | phân loại đúng **Kênh / Bot / Người dùng / Nhóm / không tra được**; `@durov` 10 397 039 người đăng ký + ✔ đã xác minh, `@BotFather` 7 560 507 người dùng/tháng, `@pythonchat` 107 thành viên |
| `--keyword "chiến sự"` (bản API) | tìm được 7 username chat liên quan, ví dụ `@aiquochientrang`, `@bantronchiensu`, `@tintucchiensu247` |
| `--channel aiquochientrang --keyword "chiến sự"` | 5 bài lấy về + 7 username; `@aiquochientrang` gộp 3 vai trò (link t.me, bài đăng trong kênh, kết quả tìm chat) |
| [`python3 main.py search --keyword "chiến sự" --json nhom1.json`](<../main.py>) | 1000 bài → `@aiquochientrang`; thêm `--text-only` → 0 username (không bài nào nhắc t.me) |
| [`.venv-api/bin/python main.py search --keyword "hathanh"`](<../main.py>) | 10 username kèm tên/loại: `@hathanh` Thanh Hà, `@hathanhtrading` Hà Thành Trading (Kênh, 213 người đăng ký), `@hathanhgrbot` (Bot)… |
| [`python3 main.py profile durov pythonchat`](<../main.py>) (web) | tải được ảnh thật (`durov.jpg` 10 425 byte, `pythonchat.jpg` 15 472 byte) + dòng "Số điện thoại: (cần --api…)" |
| [`.venv-api/bin/python main.py profile BotFather sizedevchill hathanh --api`](<../main.py>) | BotFather: 12 lệnh bot; self: số điện thoại tự khai (đã lược bỏ khỏi tài liệu), "truy cập lần cuối 10/10/2026 12:39 UTC"; `@hathanh`: "truy cập lần cuối 13/03/2026 14:07 UTC" |
| [`.venv-api/bin/python main.py profile BOSSDZAIGK88 --api --limit 0`](<../main.py>) | khối **Nhóm chung**: "1 nhóm/kênh dùng chung — VÀO CÓ 1 CHIỆU 💴💶 (Nhóm, 65 784 thành viên)" |
| `python3 -m unittest discover -s tests` | **50 test PASS** (cả system Python lẫn [`.venv-api/bin/python`](<../.venv-api/bin/python>)) |
| [`python3 main.py profile durov @BotFather pythonchat vitalikbuterin`](<../main.py>) | Kênh ✔ / Bot / Nhóm 107 / Người dùng — phân loại đúng |

## Chạy test


**Cấu hình phiên & thu thập theo nguồn (dashboard):** Cài đặt → *Cấu hình phiên Telegram* nhận
`api_id`/`api_hash`, ghi `json_folder/tg_api.json` (0600) đúng file CLI đọc; đăng nhập/OTP vẫn
qua CLI. Trang *Thu thập dữ liệu*: tìm nguồn theo từ khoá (tác vụ `search`, đọc từ bảng
`search_results`), tick nhiều nguồn, đặt số tin tối đa chung hoặc riêng từng nguồn, rồi thu thập —
mỗi nguồn một tác vụ `collect` với `--jev` luôn bật và ngân sách `min(1000, số tin)` request.
Xem [hướng dẫn dashboard](<DASHBOARD.md>).
Backend đã thêm test cho rubric kêu gọi; suite hiện tại có **495 test** (venv API đạt toàn bộ,
Python hệ thống bỏ qua 9 test cần Telethon/thị giác). Mốc lịch sử **467 test** trong venv API đều đạt; Python hệ thống chạy cùng suite và bỏ qua 1 test cần
Telethon. Khi đó có thêm 83 test cho ảnh/OCR/video/collector/dashboard: 31 OCR, 23 image
pipeline, 9 video OCR/migration, 16 collector OCR và các test HTTP dashboard (kể cả ca argv
thật qua parser `image`). Các contract UI kiểm cả 10 màn hình, allowlist/bounds của form,
OCR/partial consent, escaping, lỗi API và polling.
Không gọi API có phí hoặc ghi DB/session thật; có smoke worker thực tế trên DB tạm,
chạy CLI demo offline → SQLite. Xem [dashboard QA và giới hạn Taste](<DASHBOARD_QA.md>).

```bash
python3 -m unittest discover -s tests -v      # không cần mạng, không cần Telethon
.venv-api/bin/python -m unittest discover -s tests -v
```

Mốc sau thêm tải video theo link, **trước tích hợp Jev vào collector**: **284 test thành công**
trong venv API; Python hệ thống chạy cùng 284 test với 1 test cần Telethon được bỏ qua.
Đây là số liệu lịch sử, không phải tổng suite sau tích hợp mới. Trong đó [19 test bố cục](<../tests/test_layout.py>)
kiểm tra launcher (hiện gồm 11 CLI), import động, thư mục làm việc khác, mặc định DB/JSON/ảnh/
phiên đăng nhập và cách giữ nguyên đường dẫn tuỳ chỉnh. Các test không gọi API có phí.
[85 test video](<../tests/test_video_pipeline.py>) bao gồm FFmpeg/Whisper/vision giả lập,
HTTP loopback cục bộ, migration v1/v2 → v3, cache/partial/error, evidence và bridge.
[41 test tải một video](<../tests/test_telegram_download.py>) kiểm tra link công khai/riêng/topic,
chọn đúng message, private channel ID theo Telethon, file riêng tư/chống ghi đè,
lỗi/hủy/kích thước và phiên đăng nhập giả lập. Không tải Telegram thật trong suite.
Không tải model trong test suite. Smoke FFmpeg + Whisper + Jev thực tế được ghi riêng
ở [hướng dẫn video](<VIDEO.md#7-kiểm-chứng-bản-triển-khai>).

Test khoá lại (mốc lịch sử trước khi đổi trọng tâm): điểm 17 bài demo khớp [`posts_tg.json`](<../json_folder/posts_tg.json>); công thức group tái tạo đúng 8 nhóm
demo + nhóm thật 1000 bài; các báo động giả nổi tiếng không còn; tin tức thật chỉ còn
1/1000 bài bị gắn cờ; bộ đọc HTML tách đúng bài/views/media/link preview; phân loại trang
`t.me/<username>` (kênh/nhóm/bot/người dùng/không tồn tại); chi tiết hồ sơ (`photo_url`,
`save_photo`, dòng số điện thoại/ảnh/nhóm chung/lệnh bot, helper `_status_line` /
`_usernames_of` / `_chat_row`); `--keyword` → `usernames` + `invite_links` và `--report`
đọc lại được. Các kỳ vọng điểm/group này thuộc rubric cũ; rubric kêu gọi hiện hành cho
9 Normal / 3 Suspicious / 5 High-risk trên cùng 17 bài, nên fixture cần tạo lại khi cập
nhật test, còn các test parser/HTML/profile vẫn giữ nguyên.

Các test bổ sung trong [`tests/test_storage.py`](<../tests/test_storage.py>) dùng DB tạm và
nguồn giả lập, không dùng phiên Telegram / DB thật: schema v4, WAL / khóa ngoại / quyền
`0600`, snapshot roundtrip, dedupe / lịch sử điểm, demo tách dữ liệu thật, ID ổn định /
alias / nâng cấp username web, hồ sơ / nhóm chung / kết quả / link mời, tìm kiếm Unicode
an toàn với SQL, rollback, import idempotent không chấm lại, từ chối credential / DB lạ /
schema tương lai, backup kể cả WAL, `--no-db` và tự persist từ các CLI. Tất cả dùng
nguồn giả lập/offline, không phải lần chạy live Telegram hay Jev.

## Hai cách thu thập (chọn 1 hoặc dùng cả hai)

| | [`demo_telegram.py`](<../src/telegram_demo/cli/demo_telegram.py>) | `web` — [`demo_telegram_api.py`](<../src/telegram_demo/cli/demo_telegram_api.py>) |
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
python3 main.py api --setup-bot "YOUR_BOT_TOKEN" --api-id "YOUR_API_ID" --api-hash "YOUR_API_HASH"  # kiểm tra + lưu + liệt kê group
python3 main.py api --watch @tennhom --watch-once                        # quét 1 lượt
python3 main.py api --bot-token "YOUR_BOT_TOKEN" --watch @tennhom --interval 30 --cycles 10
```

Sau `--setup-bot`, token được lưu vào [`json_folder/tg_api.json`](<../json_folder/tg_api.json>) nên các lần sau chỉ cần
`--watch @tennhom`. Giới hạn của bot (Telegram quy định, không phải lỗi code): **chỉ nhận
tin nhắn mới** kể từ khi được thêm vào nhóm, **không đọc được lịch sử cũ**, và **không
dùng được** `contacts.Search` — script tự cảnh báo và bỏ qua nếu truyền `--search` ở chế
độ bot. Bot vẫn cần `api_id`/`api_hash`; token không thay thế cặp này.

Các cờ khác của `--watch`: `--cycles N` (dừng sau N tin), `--interval` (giây, dùng cho
`--watch-once`), `--media-dir` (tải media kèm), `--ocr`.

Chạy bản API:

```bash
pip install telethon
python3 main.py api --login                     # đăng nhập 1 lần (hỏi OTP)
python3 main.py api --channel durov --limit 30  # kênh công khai
python3 main.py api --search "đầu tư lợi nhuận" # tìm kiếm toàn cục
python3 main.py api --demo                      # offline, không cần credential
```

Credential truyền qua `--api-id/--api-hash`, biến môi trường `TG_API_ID`/`TG_API_HASH`,
hoặc file [`json_folder/tg_api.json`](<../json_folder/tg_api.json>). **Bot token** qua `--bot-token`, `TG_BOT_TOKEN`,
hoặc khóa `bot_token` trong [`json_folder/tg_api.json`](<../json_folder/tg_api.json>). [`tg_api.json`](<../json_folder/tg_api.json>) và file phiên
**không được commit** (đã có trong [`.gitignore`](<../.gitignore>)).

## Đánh giá mức KÊU GỌI ở mức GROUP (không chỉ từng bài đăng)

Yêu cầu chính khi thu thập lịch sử chat là trả lời câu hỏi *"cả nhóm này có kêu gọi/kích
động hành vi phạm pháp không?"*, nên cả hai bản đều tính **mức cho từng group/kênh**, in ở
mục **[5]** của báo cáo và lưu trong khóa `groups` của file JSON. Ví dụ dưới đây lấy từ dữ
liệu demo hiện hành, chỉ minh hoạ cấu trúc — không phải kết luận về nhóm thật:

```
[5] ĐIỂM RỦI RO THEO GROUP/KÊNH
    🔴 [ 100/100 | High-risk ] Nhóm kêu gọi hành động - Biên Hoà (@kichdong.bienhoa)
        Có lời kêu gọi/kích động hành vi phạm pháp — cần xử lý ngay — 3 bài, điểm TB 96, cao nhất
        100, 3 bài cần rà soát (100%)
        Lý do: 3/3 bài (100%) ở mức cần rà soát; loại hình nổi bật: Kêu gọi bạo lực (2 bài); có bài
               đạt 100/100 (High-risk); điểm trung bình 96/100
```

Công thức điểm group (0–100):

| Thành phần | Trọng số | Ý nghĩa |
|---|---|---|
| Điểm trung bình các bài | 60% | mức độ nặng trung bình của cả nhóm |
| Mật độ bài cần rà soát | tối đa 25 | tỷ lệ Suspicious + High-risk trên tổng số bài |
| Độ tập trung loại hình | tối đa 10 | cùng một nhóm hành vi kêu gọi lặp lại nhiều lần (mẫu số = **tổng số bài**) |
| Bài nặng nhất | tối đa 15 | bằng chứng nặng nhất trong nhóm |

Kèm theo **lý do** (bao nhiêu bài bị gắn cờ, nhóm hành vi nổi bật, có spam lặp nội dung
không) và `verdict`: *Có lời kêu gọi/kích động hành vi phạm pháp — cần xử lý ngay* /
*Cần xem xét: có dấu hiệu kêu gọi hành vi phạm pháp* / *Chưa thấy dấu hiệu kêu gọi hành vi
phạm pháp*. Trong JSON còn có `levels`, `categories`, `flagged_ratio`, `repeat_top_text` (số
nội dung khác nhau bị lặp ≥3 lần). Thứ tự `groups` là giảm dần theo `group_score`.

Ghi chú về chữ "bài"/"tin": bản web dùng "bài", bản API/[`nhom1.json`](<../json_folder/nhom1.json>) dùng "tin" — đúng
theo dữ liệu lịch sử đã lưu, đổi được qua tham số `noun`.

### Credential được lưu tự động sau khi đăng nhập

Sau khi `--login` thành công, script **tự ghi `api_id`/`api_hash` vào [`json_folder/tg_api.json`](<../json_folder/tg_api.json>)**
(quyền 600), nên các lệnh sau không phải nhập lại. Nếu chưa từng đăng nhập, có thể tự tạo
[`json_folder/tg_api.json`](<../json_folder/tg_api.json>):

```json
{"api_id": 1234567, "api_hash": "0123456789abcdef0123456789abcdef"}
```

`api_id`/`api_hash` định danh **ứng dụng**, không phải tài khoản Telegram. Dùng cặp
credential ứng dụng của chính bạn; không coi việc đổi chúng là cách thu hồi một phiên bị lộ.

### Phiên lưu ở đâu, và phân biệt BOT với TÀI KHOẢN NGƯỜI DÙNG

`--session <tên>` đặt tên phiên; tên ngắn được lưu trong thư mục [`sessions/`](<../sessions/>) ở gốc project.
Đường dẫn có thư mục (hoặc tuyệt đối) do bạn truyền được giữ nguyên, tương đối với cwd nếu chưa tuyệt đối. Ghi tên ngắn (`tk-phu`)
thì script ghép tiền tố thành `tg_session_tk-phu`; ghi tên đầy đủ (`tg_session_tk-phu`) thì
dùng nguyên tên (không ghép thành `tg_session_tg_session_tk-phu`):

| Loại phiên | Tên file | Đọc lịch sử? | Lệnh tạo |
|---|---|---|---|
| Người dùng | [`sessions/tg_session.session`](<../sessions/tg_session.session>) hoặc `sessions/tg_session_<tên>.session` | ✅ | `--login` |
| Bot | `sessions/tg_bot_session.session` hoặc `sessions/tg_bot_<tên>.session` | ❌ | `--login-bot --bot-token "YOUR_BOT_TOKEN"` |

**Quan trọng:** nếu file phiên **đã tồn tại**, script sẽ **tái sử dụng** và không hỏi đăng
nhập nữa — kể cả khi bạn gõ `--login`. Vì vậy nếu bạn từng đăng nhập bằng bot rồi muốn
chuyển sang tài khoản người dùng, nên **chọn tên phiên mới** để không phá phiên cũ:

```bash
.venv-api/bin/python main.py api --login --session nguoi-dung-moi \
    --api-id "YOUR_API_ID" --api-hash "YOUR_API_HASH"                # hỏi SĐT + OTP → tạo phiên người dùng
```

Khi tái sử dụng phiên, script luôn in rõ loại phiên (`BOT` hay `TÀI KHOẢN NGƯỜI DÙNG`).

**Bảo mật:** file `.session` chứa `auth_key` — ai có file là **chiếm được tài khoản**.
Project đặt `chmod 600` cho [`json_folder/tg_api.json`](<../json_folder/tg_api.json>) và các file phiên, và đã [`.gitignore`](<../.gitignore>) chúng.
Kiểm tra quyền cục bộ (phải là `-rw-------`):

```bash
ls -l sessions/*.session json_folder/tg_api.json
```

## Tìm group kín: `groups`

Group kín không có username thì Telegram không cho vào chỉ mục tìm kiếm, nên cách thực tế
là: kênh công khai (quảng cáo) → bóc link mời `t.me/+abc` → join → đọc lịch sử. Script chia
3 bước tách rời:

```bash
python3 main.py groups --discover --channel @kenh_cong_khai --limit 500 --out found.json
python3 main.py groups --discover --search "đầu tư lợi nhuận" --out found.json
python3 main.py groups --discover --in-my-chats --out found.json
python3 main.py groups --join --in found.json --max-join 5 --delay 20
python3 main.py groups --crawl --in found.json --limit 1000 --out groups.json
```

⚠️ Join nhiều group lạ có thể khiến Telegram **hạn chế hoặc khoá tài khoản**. Mặc định
script rất dè dặt: tối đa **5 link**, nghỉ **20 giây** mỗi lần, dừng ngay khi gặp
`FloodWaitError`. Nên dùng tài khoản phụ. Bước `--discover` không tự join, nhưng vẫn gọi Telegram và lưu dữ liệu nhạy cảm; chỉ thu thập trong phạm vi được phép.
Hỗ trợ cả link mời mới (`t.me/+hash`) và cũ (`t.me/joinchat/hash`).

## Giới hạn & hướng mở rộng

- Trang `/s/` chỉ có ~20 bài mỗi trang và **chỉ kênh công khai**; group riêng tư hoặc kênh
  tắt preview thì không lấy được (script báo lỗi rõ ràng, không sập).
- Tìm kiếm toàn cục, group kín, tải media, OCR, `--watch` đã có ở backend API.
  **Ảnh → OCR** và **video → Whisper + OCR frame** có pipeline/evidence riêng, không
  tự bật khi thu thập hoặc ghi đè `pipeline_status` lịch sử. Hiểu cảnh cần model thị giác
  được cấu hình; OCR không thay vision. Xem [IMAGE_OCR](<IMAGE_OCR.md>).
- Điểm số là heuristic theo từ khoá và ngữ cảnh, nên vẫn có thể bỏ sót hoặc gắn cờ nhầm;
  luôn đọc `signals`, `evidence`, `context` và `requires_review` để rà soát thủ công.
  Mốc lịch sử trước khi đổi trọng tâm: chấm lại 1000 bài của
  [`nhom1.json`](<../json_folder/nhom1.json>) bằng bộ từ khoá cũ còn 1 bài (id 157495,
  30 điểm Suspicious do `công an` + link + forward); rubric kêu gọi chỉ cộng điểm mục tiêu
  khi đã có nhóm hành động nên con số này không còn đại diện.
- [`nhom1.json`](<../json_folder/nhom1.json>) là dữ liệu **lịch sử**: `analysis`/`groups` trong đó được tính bằng bộ từ
  khoá cũ và rubric lừa đảo trước đây, nên chấm lại sẽ ra số khác. [`posts_tg.json`](<../json_folder/posts_tg.json>)
  cũng giữ `analysis` lịch sử; rubric kêu gọi hiện hành cho 9 Normal / 3 Suspicious /
  5 High-risk trên cùng 17 bài, nên fixture cần được tạo lại khi cập nhật test.

## Tài liệu liên quan

- [Tổng quan và bắt đầu nhanh](<../README.md>).
- [Điểm vào CLI chung](<../main.py>) và [các đường dẫn mặc định](<../src/telegram_demo/paths.py>).
