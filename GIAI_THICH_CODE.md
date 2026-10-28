# Giải thích code

Tài liệu này giải thích **từng phần của code** trong project: luồng chạy, công thức chấm
điểm, ý nghĩa các khóa JSON, những lỗi đã sửa và những chỗ **suy luận/di sản** cần biết.

> Các số dòng `:N` / khoảng dòng trong phần tài liệu lịch sử bên dưới chỉ còn là **mốc
> định hướng**, không phải vị trí chính xác sau các thay đổi code. Tra theo tên hàm / lớp
> khi đọc mã. Các số liệu kiểm chứng ghi ngày 2026-10-11 giữ nguyên ý nghĩa lịch sử,
> không phải kết quả hay tổng số test của bản SQLite hiện tại.

## 1. Kiến trúc

```
        analyzer.py  ──── nguồn chân lý dùng chung ────┐
   (từ điển, analyze, group_summary, print_report,   │
    write_json, demo_posts, username_payload,        │
    make_profile)                                    │
            ▲                    ▲                    │
            │ import             │ import             │
   demo_telegram.py      demo_telegram_api.py         │
   (web, chỉ stdlib)     (Telethon: group kín,        │
            ▲             tìm kiếm, media, OCR,       │
            │             chế độ bot, --watch)       │
            │                    ▲                    │
    trang t.me/s/<kênh>   Telegram API        find_private_groups.py
                                              (discover → join → crawl)
            ▲                    ▲
            │ import (muộn)      │ import (muộn)
   keyword_usernames.py   username_info.py
   (chức năng 1: keyword   (chức năng 2: username
    → list username)        → thông tin tài khoản)
```

Lý do tách `analyzer.py`: trước đây khối "từ điển + chấm điểm" bị **copy nguyên** vào cả
hai backend nên hai bản dễ lệch nhau. Nay chỉ còn **một** nơi định nghĩa; sửa từ khoá/ngưỡng
là cả hai backend đổi theo. `analyzer.py` **chỉ dùng thư viện chuẩn** (không cần Telethon),
nên chạy được ở mọi máy và test được offline.

## 1a. Kiến trúc lưu trữ SQLite — [`storage.py`](storage.py) và [`database.py`](database.py)

### Vai trò trong pipeline

[`storage.py`](storage.py) cung cấp lớp `Database`, quản lý **schema v1** bằng `sqlite3`
của thư viện chuẩn. [`database.py`](database.py) là CLI quản trị / tra cứu offline; không
cần Telethon, không cài gói pip cho SQLite, không chạy server DB.

```text
Thu thập web / API / demo / username / keyword
          → payload kết quả thành công
              ├─ xuất JSON theo quy ước cũ (nếu có)
              └─ Database → snapshot run bất biến + các bảng truy vấn
JSON lịch sử → database.py import-json → cùng lớp Database
```

Các CLI tự lưu sau khi thu thập thành công; **không phụ thuộc `--out`**, nên công cụ
username / keyword chỉ in console vẫn có run trong DB. Các cờ chung là `--db PATH` và
`--no-db`; thứ tự chọn đường dẫn là `--db` → `TELEGRAM_DB_PATH` → mặc định
`data/telegram.sqlite3`. Đường dẫn mặc định được chuyển thành tuyệt đối dựa trên **vị trí
script**, không phải thư mục làm việc. JSON export vẫn dùng các helper đường dẫn trong
[`analyzer.py`](analyzer.py): tên trần đi vào [`json_folder/`](json_folder/), đường dẫn có
thư mục / tuyệt đối giữ nguyên. File phiên Telethon vẫn riêng biệt.

### Schema và ranh giới dữ liệu

| Bảng | Vai trò / bất biến |
|---|---|
| `runs` | Metadata lần thu thập / import và **payload JSON gốc bất biến**, đủ để xuất lại toàn bộ trạng thái của run. SHA256 nhận diện payload để import lại cùng dữ liệu không tạo run trùng. |
| `entities` | Định danh thực thể; namespace `demo` tách `telegram`; danh tính ổn định là **loại thực thể + Telegram ID** khi có ID. |
| `entity_aliases` | Username / tên tra cứu gắn với thực thể; không dùng username có thể đổi làm danh tính Telegram vĩnh viễn. |
| `messages` | **Quan sát mới nhất** của bài đăng; ràng buộc duy nhất `(entity_id, message_id)`, không chỉ riêng `message_id` vốn có thể trùng giữa các chat. |
| `run_messages` | Liên kết run với những message được quan sát trong lần đó. |
| `analyses` | Kết quả phân tích **theo run / message**, có provenance và phiên bản analyzer, không chỉ một điểm bị ghi đè trên message. |
| `profile_snapshots` | Hồ sơ quan sát được trong từng run, giữ lịch sử thông tin tài khoản. |
| `common_groups` | Nhóm chung **nhìn thấy từ phiên đang tra cứu** trong snapshot; không phải mọi nhóm mà người đó tham gia. |
| `group_summaries` | Điểm / thống kê group của run tương ứng, giữ nguyên số liệu lịch sử. |
| `search_results` | Kết quả username / keyword cùng ngữ cảnh run, kể cả khi không có bài đăng. |
| `invite_links` | Link mời được quan sát, gắn với nguồn / lần thu thập. |

Bản web không luôn biết Telegram ID, nên thực thể ban đầu có định danh username **tạm
thời**; khi API trả về ID, thực thể được làm giàu / đối chiếu sang danh tính ổn định. Không
trộn hai loại thực thể chỉ vì cùng số ID, không trộn namespace demo với Telegram thật.
Alias hỗ trợ đổi username, kể cả username phụ. Khi hợp nhất định danh web tạm vào ID đã
xác minh, giữ quan sát message thuộc run mới nhất, chuyển đầy đủ phân tích / tham chiếu,
không thay snapshot gốc. Chat thường và supergroup có cùng số ID vẫn khác nhau nhờ
`peer_type`; thu thập API, thống kê group và nhóm chung đều giữ loại peer này.
Username web chưa xác minh không tự chứng minh danh tính thật.

`messages` phục vụ truy vấn trạng thái mới nhất; **không dùng nó để tái dựng nguyên trạng
một run cũ**. Muốn đầy đủ text, views, profile và kết quả tại thời điểm cũ, xuất snapshot
của `runs` bằng `export RUN_ID`. Import chỉ lưu kết quả đã có, **không gọi `analyze()` để
chấm lại** `analysis` / `groups`. Provenance cho phân tích mới là **SHA256 mã nguồn
[`analyzer.py`](analyzer.py)**, không phải semantic version. Khi import, giữ phiên bản
được khai báo trong payload; lịch sử không xác định được phiên bản ghi là `unversioned`,
không lấy hash hiện tại thay thế. Điều này khác với thao tác chủ động chấm lại JSON của
công cụ keyword mô tả ở mục 5.

### CLI quản trị và di chuyển dữ liệu

**`--db` là cờ toàn cục, phải đặt trước subcommand**. Các lệnh sau chỉ thao tác dữ liệu
local, không đăng nhập và không gọi Telegram:

```bash
python3 database.py init
python3 database.py import-json --all
python3 database.py import-json posts_tg.json
python3 database.py stats
python3 database.py runs --limit 20
python3 database.py messages --keyword 'đầu tư' --username dautu.sieuloinhuan --limit 10
python3 database.py profiles durov
python3 database.py results RUN_ID
python3 database.py export RUN_ID --out sqlite_export.json
python3 database.py backup data/backup.sqlite3
python3 database.py --db data/thu_nghiem.sqlite3 stats
```

`import-json --all` nhập dữ liệu trong [`json_folder/`](json_folder/), **bỏ qua credential**
[`json_folder/tg_api.json`](json_folder/tg_api.json); dạng tên trần [`posts_tg.json`](json_folder/posts_tg.json) cũng theo
quy ước JSON hiện có. Import lại **đúng payload** là idempotent theo SHA256, không phải
so tên file. Import lập chỉ mục cả `found[].history` trong dữ liệu discovery cũ; không
bịa thêm điểm nếu tin nhắn chưa có `analysis`. Snapshot giữ cả kiểu gốc object hoặc list.
`runs` cung cấp `RUN_ID` để dùng cho `results` / `export`. `export` ghi snapshot
JSON với tên trần vào `json_folder/sqlite_export.json`, **từ chối ghi đè**; `backup` tạo
một DB SQLite ở **đích mới**, không ghi đè bản backup cũ.

### Triển khai, đồng thời và bảo vệ dữ liệu

- DB **plaintext, không mã hoá**, quyền `0600`; thư mục `data/` nằm trong git-ignore.
  Không nhập API credential, bot token hay session key vào kho thu thập. DB Telethon
  chứa phiên đăng nhập không phải nguồn import, dù bản thân nó cũng dùng SQLite.
- WAL cho phép đọc trong lúc ghi; `busy_timeout = 10000` ms (10 giây) giúp chờ writer.
  Ghi hàng loạt trong **batch transaction**, không commit từng message riêng lẻ.
  SQLite vẫn chỉ có **một writer tại một thời điểm**: phù hợp triển khai local / tiến
  trình nhỏ, không phải cơ sở dữ liệu nhiều writer không giới hạn.
- Dùng `backup` khi DB đang hoạt động để lấy bản sao nhất quán; chỉ copy file DB chính
  có thể thiếu thay đổi còn trong WAL. Bảo vệ cả backup và dữ liệu cá nhân / link mời.
- `--no-db` tắt persist cho lần chạy đó, không xoá DB cũ hay thay đổi việc xuất JSON.
  Không cần chạy live Telegram để kiểm tra import, truy vấn, export và backup.
- JSON và DB không dùng chung transaction: khi DB lỗi, CLI trả lỗi, nhưng file JSON đã
  xuất trước đó vẫn giữ để phục hồi / nhập lại. Mỗi transaction DB tự rollback toàn bộ.
- Kiểm chứng cuối: 80 test trong venv API thành công; Python hệ thống 79 thành công +
  1 skip cần Telethon. DB thực tế nhập 9 run, 1.138 message duy nhất, 1.155 phân tích;
  `integrity_check = ok`, `foreign_key_check` không có lỗi, import lại không tăng số run.

## 2. `analyzer.py`

### 2.1. Từ điển (`SCAM_GROUPS` :37, `ACTION_SIGNALS` :72)

Mỗi nhóm là `(tên hiển thị tiếng Việt, trọng số, [từ khoá...])`:

| Nhóm | Trọng số | Ghi chú |
|---|---|---|
| Đầu tư / lợi nhuận cao | 22 | `lợi nhuận`, `đầu tư`, `làm giàu`, `sinh lời`… |
| Việc làm online | 18 | `tuyển cộng tác viên`, `ctv`, `chốt đơn`, `hoàn tiền`… |
| Vay tiền nhanh | 20 | `cho vay`, `giải ngân`, `không thẩm định`… |
| Trúng thưởng / quà tặng | 16 | `trúng thưởng`, `nhận quà`, `tri ân`… |
| Mạo danh tổ chức | 20 | `công an`, `toà án`, `cơ quan thuế`, `cán bộ thuế`, `nhân viên thuế`… |
| Mua sắm / bán hàng | 14 | `giảm giá`, `freeship`, `sale sốc`… |

`ACTION_SIGNALS` là 4 nhóm **hành vi** cộng thẳng vào điểm: chuyển tiền (20), hoa hồng
(14), điều hướng sang nền tảng khác / `ib` (18), thúc ép khẩn cấp (10).

> **Vì sao `"thuế"` bị thay bằng `"cơ quan thuế", "cán bộ thuế", "nhân viên thuế"`?**
> Từ khoá trần `"thuế"` khớp trong 2 bài **tin tức thật** (`nhom1.json` id 157476 "người
> nộp thuế châu Âu", id 158067 "mức thuế 1-2 đô la"), đẩy chúng lên đúng 25 = ngưỡng
> Suspicious. Siết lại thì 17 bài demo **không đổi** (0 bài khác), còn dữ liệu thật
> 1000 bài giảm từ 3 bài bị gắn cờ xuống **1** (id 157495: `công an` + URL + forward).

### 2.2. Ngưỡng và cờ cấu hình (:95-121)

| Tên | Giá trị | Ý nghĩa |
|---|---|---|
| `THRESHOLD_SUSPICIOUS` / `THRESHOLD_HIGH_RISK` | 25 / 60 | ngưỡng mức cho **một bài** |
| `GROUP_THRESHOLD_SUSPICIOUS` / `..._HIGH_RISK` | 25 / 60 | ngưỡng mức cho **group** |
| `REASON_DENSITY_MIN` | 0.5 | tỷ lệ bài bị gắn cờ tối thiểu để in lý do "N/M … cần rà soát" |
| `MATCH_WORD_BOUNDARY` | `True` | khớp từ khoá theo **ranh giới từ** |
| `NOUN_WEB` / `NOUN_API` | `"bài"` / `"tin"` | danh từ dùng trong câu lý do (theo dữ liệu lịch sử) |

`REASON_DENSITY_MIN = 0.5` được chọn bằng **dò ngược dữ liệu**: nhóm `posts_tg.json` có tỷ
lệ 1.0 **có** câu lý do, nhóm `nhom1.json` tỷ lệ 0.008 **không** có ⇒ mọi ngưỡng trong
khoảng (0.008, 1.0] đều tái tạo đúng; 0.5 là giữa khoảng.

Regex (:113-121):

| Regex | Bắt gì |
|---|---|
| `RE_URL` | link http(s), `www.`, hoặc tên miền thông dụng (`com/net/vn/org/io/xyz/top/info/site/online/me/app/pro`) |
| `RE_PHONE` | SĐT Việt Nam: `+84` hoặc `0` + đầu 3/5/7/8/9 + 8 số |
| `RE_BANK_ACCOUNT` | 9–16 chữ số **đứng tách khỏi** chữ, dấu chấm, dấu phẩy, gạch nối |
| `RE_HASHTAG` | `#` + chữ/số/tiếng Việt (≥2 ký tự) — lưu **không** kèm dấu `#` |
| `RE_MENTION` | `@username` (4–32 ký tự, bắt đầu bằng chữ) |
| `RE_TG_INVITE` | link mời nhóm `t.me/+hash` và `t.me/joinchat/hash` |
| `RE_FORWARD_SOURCE` | nguồn forward dạng `t.me/<username>` (loại trừ link mời) |

`RE_BANK_ACCOUNT` là chỗ **sửa lỗi**: bản cũ `(?<!\d)\d{9,16}(?!\d)` nhận cả số tiền viết
liền, ví dụ `1500000` (7 số thì không, nhưng `1234567890` trong "giá 1234567890đ" thì có).
Bản mới chặn thêm `\w`, `.` và `,` ở hai đầu nên `1.500.000` và `12,345,678` bị loại, còn
số tài khoản thật `1234567890123` vẫn nhận.

### 2.3. Chuẩn hoá (`normalize` :128, `no_accent` :135)

`normalize()`: chuẩn hoá Unicode NFC → bỏ ký tự vô hình (`\u200b`, `\ufeff`) → gộp mọi
khoảng trắng (kể cả xuống dòng) thành **một** dấu cách → chữ thường → cắt hai đầu.
`no_accent()`: bỏ dấu tiếng Việt và `đ → d`, để bắt được nội dung viết không dấu
(`dautu`, `loinhuan`, `chuyen tien`).

`analyze()` chấm điểm trên **haystack** = `text` + `ocr_text` + `channel_title` + `channel`.
Việc đưa tên kênh vào haystack là **hành vi cũ, giữ nguyên có chủ ý**: tên kênh như
`vieclam.ctv.hoantien` hay `dautu.sieuloinhuan` tự nó đã là tín hiệu.

### 2.4. Khớp từ khoá theo ranh giới từ (`kw_regex` :149, `hits` :158)

```python
kw_regex("ib")  # -> re.compile(r"(?<!\w)ib(?!\w)")   (cache bằng functools.lru_cache)
```

Trước đây phép khớp là `keyword in haystack` (khớp **chuỗi con**), gây báo động giả đã
kiểm chứng trên `nhom1.json`:

| Từ khoá | Khớp nhầm trong | Hậu quả cũ |
|---|---|---|
| `ib` | "herm**ib**ooks", "anaq**ib**", "respons**ib**le", "ghal**ib**af" | +10.8 điểm "điều hướng sang nền tảng khác" |
| `stk` | chuỗi base64 `fbclid=…mEstkkkjvi73…` | +12 điểm "chuyển tiền" |
| `công an` | "phân **công an**h ta" | +12 điểm "mạo danh" |
| `thuế` | "người nộp **thuế**", "mức **thuế** 1-2 đô" | +12 điểm "mạo danh" |

`hits()` kiểm tra **cả** haystack có dấu (`(?<!\w)kw(?!\w)`) **lẫn** bản `no_accent`, và trả
danh sách từ khoá khớp (đã loại trùng, giữ thứ tự). Kết quả sau khi sửa: dữ liệu thật
1000 bài từ **8** bài bị gắn cờ xuống **3**, rồi còn **1** sau khi siết `"thuế"`; 17 bài
demo **không đổi một bài nào** (nên các số liệu README vẫn đúng).

### 2.5. `extract_entities` (:179)

Trả `{"urls", "phones", "bank_accounts", "invite_links"}`. `analyze()` còn trộn thêm
`link_preview` của bài (nếu có) và mọi thực thể đọc được từ OCR.

### 2.6. `analyze` (:201) — công thức điểm một bài

```
điểm = Σ nhóm lừa đảo (lấy nhóm cao nhất + 0.5×nhóm 2 + 0.5×nhóm 3)
     + Σ tín hiệu hành vi (cộng thẳng)
     + tín hiệu thực thể / Telegram
```

- Điểm mỗi nhóm: `weight * min(1.0, 0.6 + 0.2 * (số từ khoá khớp - 1))`
  → khớp 1 từ = 60% trọng số, khớp 3 từ trở lên = 100% trọng số (tối đa 22 điểm/nhóm).
- Nhóm lừa đảo: **chỉ nhóm cao nhất tính đủ**, nhóm 2 và 3 tính **một nửa** — tránh việc
  một bài chỉ cần dính nhiều nhóm là vọt điểm.
- Tín hiệu cộng thêm: URL **+12**, SĐT/STK **+15**, link mời nhóm **+12**,
  bài forward **+6**, ảnh/video kèm nội dung tài chính **+5**, có chữ OCR **+5**.
- Điểm cuối: `int(round(min(100, điểm)))`; `category` = nhóm lừa đảo cao nhất (chỉ khi
  mức ≠ Normal). Chuỗi `summary` mô tả 3 tín hiệu mạnh nhất.

Kết quả `analysis` gồm: `scam_score`, `level`, `scam_category`, `scam_category_vi`,
`signals` (mỗi phần tử có `label_vi`, `points`, `matched`), `entities`, `summary`.

Ví dụ đã kiểm tay: bài có Job khớp 3 từ (18 đủ) + offplatform (10.8) + URL (12) + forward
(6) = 46.8 → **47**.

### 2.7. Mức rủi ro (:285-325)

`level_of`: `<25` Normal, `25–59` Suspicious, `≥60` High-risk.
`verdict_of`: "Có dấu hiệu lừa đảo — cần kiểm tra kỹ" / "Nghi ngờ" / "Chưa thấy dấu hiệu bất thường".
`icon_of`: 🔴 / 🟠 / 🟢. `_pct`: 0.008 → `"0.8"`, 1.0 → `"100"`.

### 2.8. `group_summary` (:337) — công thức điểm group

```
group_score = min(100, round( 0.6  * avg_score
                            + 25   * flagged_ratio
                            + 10   * (số bài của loại hình nổi bật / TỔNG số bài)
                            + 15   * (max_score / 100) ))
```

- `round()` là làm tròn kiểu Python (chẵn), không phải cắt `int`: nhóm `vayvon.nhanh365`
  có `0.6*40.5 + 25 + 10 + 15*0.49 = 66.65 → 67`.
- Mẫu số của "độ tập trung loại hình" là **tổng số bài**, không phải số bài bị gắn cờ:
  nhóm `aiquochientrang` (1000 bài, 8 bài gắn cờ, 3 bài Đầu tư)
  = `0.6*4.9 + 25*0.008 + 10*3/1000 + 15*0.36 = 8.57 → 9` ✔ (nếu chia cho 8 thì ra 12 ✘).
- Các khóa mỗi nhóm: `channel`, `channel_title`, `total_messages`, `avg_score` (làm tròn
  1 chữ số), `max_score`, `flagged`, `flagged_ratio` (làm tròn 3 chữ số), `levels`,
  `categories`, `repeat_top_text` (**chỉ ghi khi > 0**), `group_score`, `group_level`,
  `verdict`, `reasons`.
- `reasons` được ghép theo thứ tự: mật độ bài cần rà soát (nếu ≥ `REASON_DENSITY_MIN`) →
  loại hình nổi bật → có bài đạt ngưỡng High-risk → điểm trung bình (nếu group ≠ Normal).
- Danh sách trả về sắp xếp **giảm dần** theo `group_score` (đồng hạng thì theo thứ tự gặp).

**Hai chỗ phải sửa để khớp dữ liệu lịch sử:**

1. Câu lý do điểm trung bình dùng `round(avg)` về **số nguyên**, không in 1 chữ số thập
   phân — dữ liệu đã lưu cho thấy `59.7 → "60"`, `40.5 → "40"`, `45.5 → "46"`.
2. `repeat_top_text` = **số nội dung khác nhau bị lặp từ 3 lần trở lên** (không phải tổng
   số lần lặp, cũng không phải số nội dung lặp ≥2 lần).
   ⚠️ Ngữ nghĩa này chỉ **suy ra từ một mẫu lịch sử duy nhất** còn lưu (`nhom1.json` = 2,
   trong khi nhóm đó có 3 nội dung lặp ≥2 lần và 1 nội dung lặp 4 lần). Nếu có thêm dữ
   liệu khác, cần kiểm chứng lại.

### 2.9. Trình bày và ghi JSON (:432-643)

- `group_lines()` / `_wrap()` / `print_group_section()`: in mục `[5]`. Ngắt dòng ở
  `width=100` cho ra **đúng** cách xuống dòng của ví dụ trong `README.md`.
  Lưu ý: câu lý do trong JSON viết `"3/3 bài (100%) cần rà soát"`, còn khi in ra console
  đổi thành `"3/3 bài (100%) ở mức cần rà soát"` — chữ khác nhau là **có chủ ý** (khớp
  nguyên văn bản cũ).
- `_public(record)`: bản ghi để ghi ra JSON — **chỉ** bỏ khóa nội bộ `_msg_ref`, **giữ**
  `_note` (vì `posts_tg.json` lịch sử có `_note`).
- `print_report(records, sources, title, noun, width)`: mục [1] tổng quan
  (Normal/Suspicious/High-risk + "Cần rà soát : X%"), [2] NHÓM LỪA ĐẢO, [3] TOP KÊNH (8),
  [4] TOP BÀI (5), rồi mục [5] group + dòng "Lưu ý: điểm số là TÍN HIỆU HỖ TRỢ RÀ SOÁT".
- `print_history()`: in gọn danh sách bài đã thu thập (dùng khi `--no-report`).
- `write_json(path, records, backend, noun)`: ghi
  `{"generated_at", "total", "backend", "groups", "posts"}` và in
  `[i] Đã lưu N bài đăng vào <path>`. Nhiều hàm nhận JSON cũ **không có `groups`** —
  `--report` sẽ **tự tính lại** mục [5] từ `analysis` của từng bài.

### 2.10. Dữ liệu mẫu (:596-694)

`DEMO_ITEMS` = 17 bài của 8 kênh (5 kênh "xấu" + 3 kênh "sạch") dùng cho `--demo`.
`demo_posts(limit, dialect)` sinh record; `dialect="api"` thêm `forwards`/`replies`
(bản web **không** có vì trang `t.me/s/` không hiển thị). Mọi bản ghi demo có
`_note = "DỮ LIỆU MẪU offline - không phải bài đăng thật"` và `source = "demo"`.

### 2.11. `--keyword` → username liên quan (:678-966)

Mục đích: "nhập keyword rồi liệt kê username có thể liên quan".

- `RE_USERNAME` (:695) bắt `t.me/<username>` (loại `+`, `joinchat/`, `s/`); lớp ký tự
  **có dấu `.`** nên `dautu.sieuloinhuan` không bị cắt thành `dautu` (lỗi đã gặp & đã sửa).
- `USERNAME_RE` (:697) kiểm tra một chuỗi có phải username hợp lệ.
- `_username_of(value, strict=False)` (:714): nhận `t.me/x`, `@x`, `x`; `strict=True` chỉ
  nhận dạng **có tiền tố** — dùng cho `forwarded_from` vì trường đó thường là tên hiển thị
  tự do ("Some Display Name" không được coi là username).
- `keywords_in()` (:708) khớp từ khoá trên cả text có dấu lẫn bản `no_accent`.
- `_record_text()` (:734) = text + link_preview + channel_title + channel (URL của chính bài
  **không** tính là "link được nhắc").
- `related_usernames()` (:746): gom theo 5 vai trò — `channel` (bài đăng trong kênh),
  `mention` (@nhắc), `link` (link t.me trong bài), `forward` (nguồn forward), `search`
  (kết quả tìm chat, chỉ bản API qua tham số `extra_usernames`). Sắp xếp
  `(-max_score, -posts, username)`; mỗi dòng giữ `keywords`, `roles`, `examples`.
- `related_invite_links()` (:851): link `t.me/+hash` không có username để tra → tách riêng.
- `username_payload()` / `print_username_payload()` (:939, :951): gói
  `{"keywords", "usernames", "invite_links"}` để cả hai backend vừa in mục `[6]` vừa ghi JSON.

### 2.12. `--user` → tra cứu một username (:968-1080)

Chuẩn hoá kết quả tra cứu của hai backend về **cùng một cấu trúc**:

- `PROFILE_KIND_VI` (:968): `channel/group/bot/user/unknown` → Kênh/Nhóm/Bot/Người dùng/Không rõ.
- `make_profile(username, kind, **fields)` (:977): tự thêm `profile_url`, `kind_vi`,
  `fetched_at`; các khóa cơ bản: `exists, title, about, subscribers, members,
  monthly_users, account_id, verified, is_bot, source`; các khóa **chi tiết** (mặc định rỗng,
  chỉ có dữ liệu khi backend lấy được): `phone, photo_url, photo_path, has_photo, last_seen,
  created_at, lang_code, usernames, premium, scam, fake, restricted, mutual_contact,
  is_contact, bot_commands, common_groups, common_chats_count`.
- `_format_count()` (:1000) `1000` → `"1 000"`; `profile_scale()` (:1007) → "N người đăng ký" /
  "N thành viên" / "N người dùng/tháng" (chỉ hiện khi có số).
- Bốn helper in chi tiết: `_phone_line()` (:1018) — có số thì in số kèm chú thích "danh bạ/công
  khai"; nguồn `telethon` mà rỗng thì "(không có — Telegram chỉ hiện khi là danh bạ…)"; nguồn web
  thì "(cần --api…)"; `_photo_line()` (:1027) — ưu tiên `photo_path`, rồi "có ảnh đại diện (thêm
  --photo-dir DIR để tải về)", rồi `photo_url`; `_flags_line()` (:1039) — bot/Premium/bị gắn cờ
  lừa đảo/giả mạo/bị hạn chế/liên hệ hai chiều/trong danh bạ; `_other_usernames()` (:1049).
- `print_profile(..., common_limit=20)` (:1054): in khối `[0]`; nếu truyền `records` thì in thêm
  thống kê qua `group_risk_for_channel()`; in thêm các dòng Số điện thoại / Ảnh đại diện / Hoạt
  động / Ngày tạo / Username khác / Cờ tài khoản / Ngôn ngữ, khối `Lệnh bot` (tối đa 12 lệnh) và
  khối `Nhóm chung` (`N nhóm/kênh dùng chung`, mỗi dòng `- Tên  (Loại, X thành viên)  @username`);
  `exists=False` → in "KHÔNG tra được (không tồn tại, riêng tư, hoặc không có bản xem trước)".

## 3. `demo_telegram.py` — backend web (chỉ stdlib)

| Hàm | Dòng | Việc |
|---|---|---|
| `fetch(url)` | :60 | tải HTML (`User-Agent` Chrome, timeout 25s), báo lỗi rõ cho HTTPError/URLError/timeout |
| `fetch_bytes(url)` | :78 | tải nội dung nhị phân (dùng cho ảnh đại diện) |
| `save_photo(username, photo_url, photo_dir)` | :92 | ghi `<photo_dir>/<username>.jpg`; trả `""` nếu thiếu URL/thư mục |
| `channel_name(raw)` | :107 | chuẩn hoá `durov`, `@durov`, `t.me/durov`, `https://t.me/s/durov`; **từ chối** link mời `+`/`joinchat` kèm gợi ý dùng bản API |
| `_text_of(fragment)` | :137 | bỏ thẻ HTML, `<br>` → xuống dòng, giải mã entity |
| `parse_count(text)` | :149 | `"10.4M"` → `10400000` (hỗ trợ K/M/B) |
| `parse_channel_info(page, fallback)` | :164 | tên, username, mô tả, số người đăng ký |
| `_clean_text(raw)` | :191 | bỏ thẻ HTML nhưng **giữ nguyên chữ hoa/thường** (khác `_text_of` — dùng cho tên/mô tả tài khoản) |
| `parse_profile(page, username)` | :196 | phân loại trang `t.me/<username>`: `subscribers`→Kênh, `members`→Nhóm, `monthly users`/`Start Bot`→Bot, `You can contact @x`→Người dùng, rỗng+mất khối info→`exists=False`; lấy thêm **ảnh đại diện** từ `tgme_page_photo_image`/`og:image` (`photo_url`, `has_photo`) |
| `lookup_user(username, photo_dir='', save=True)` | :266 | chuẩn hoá qua `channel_name()` rồi `fetch` + `parse_profile`; nếu có `photo_dir` thì tải ảnh về |
| `media_types_of(block)` | :281 | nhận `photo`/`video`/`document`… trong một bài |
| `parse_messages(page, info)` | :292 | tách từng `<div class="tgme_widget_message" data-post="kênh/id">` thành record |
| `scrape_channel(name, limit, progress)` | :348 | phân trang `?before=<data-before cuối>`, chống trùng theo `kênh/id`, dừng khi hết trang hoặc đủ `limit` |
| `read_seed_file(path)` | :403 | đọc danh sách kênh, bỏ dòng trống và dòng `#` |
| `main()` | :414 | argparse + gọi 2 hàm trên + `write_json`/`print_report` |

CLI: `--demo`, `--channel` (lặp được), `--seed-file`, `--keyword` (lặp được), `--user`
(lặp được), `--limit` (**số bài mỗi kênh**, mặc định 20), `--out` (mặc định
`json_folder/posts_web.json`), `--no-report`, `--report <json>`, `--photo-dir DIR` (mặc định
`profile_photos`), `--no-photo`. Kênh/nhóm tra bằng `--user` mà tồn tại
thì được lấy luôn bài gần đây; kết quả tra cứu lưu ở khóa `profiles`.
Bản web **không** có `forwards`/`replies`; có `views`, `link_preview`, `has_media`,
`media_types`, `subscribers`. Kênh riêng tư/tắt preview → báo
`"Kênh '@x' không có bản xem trước công khai (hoặc không tồn tại)."` và liệt kê kênh thử được.

## 4. `demo_telegram_api.py` — backend Telethon

### 4.1. Phiên và credential (:78-237)

- `session_path(name, bot)` (:84): tên rỗng → `tg_session` / `tg_bot_session`;
  `"tk-phu"` → `tg_session_tk-phu` / `tg_bot_tk-phu`; tên **đã đầy đủ** (`tg_session_tk-phu`,
  `tg_bot_x`) → dùng nguyên tên (không ghép tiền tố hai lần); có dấu `/` → dùng nguyên đường dẫn;
  tự cắt đuôi `.session`. **Luôn trả đường dẫn tuyệt đối cạnh script** (`SCRIPT_DIR`), nên
  chạy từ thư mục nào cũng tìm đúng phiên.
- `session_kind(path)` (:106): tên file bắt đầu bằng `tg_bot` → `BOT`, còn lại
  `TÀI KHOẢN NGƯỜI DÙNG`.
- `load_credentials` (:179) / `load_bot_token` (:190): thứ tự `--api-id/--api-hash` (và
  `--bot-token`) → biến môi trường `TG_API_ID`/`TG_API_HASH`/`TG_BOT_TOKEN` → `tg_api.json`.
- `save_credentials` (:206): ghi `json_folder/tg_api.json` rồi `os.chmod(..., 0o600)` (bản cũ **không**
  có `chmod`, dù README nói có).
- `credentials_help()` (:225): hướng dẫn lấy api_id/api_hash khi thiếu.

### 4.2. OCR và media (:129-358)

`ocr_available()` kiểm tra `PIL` + `pytesseract` + binary `tesseract`; thiếu thì `--ocr`
**tự bỏ qua** (in cảnh báo) chứ không sập. `media_kind(msg)` → `(has_media, media_types, kind)`;
`extract_link_preview(msg)` lấy URL trong preview; `to_record(msg, entity)` chuyển message
Telethon thành record cùng schema với bản web.

**Lỗi đã sửa trong `to_record`:** biểu thức cũ

```python
fwd_name = getattr(fwd, "from_name", None) or getattr(chat, "title", None) \
           and getattr(chat, "username", None) or "nguồn ẩn"
```

mắc bẫy **độ ưu tiên toán tử** (`and` cao hơn `or`), nên **mọi** bài forward không có
`from_name` đều thành `"nguồn ẩn"` — và `"nguồn ẩn"` lại kích hoạt tín hiệu forward **+6
điểm cho mọi bài**. Bản mới dùng ngoặc rõ ràng:

```python
fwd_name = (getattr(fwd, "from_name", None) or getattr(chat, "title", None)
            or getattr(chat, "username", None) or "nguồn ẩn")
```

### 4.3. Thu thập (:360-657)

`fetch_history()` đọc lịch sử kênh/nhóm, `search_global()` tìm toàn cục, `download_media()`
tải file.
- `lookup_entity(client, target, limit=0, photo_dir='', common_limit=20, save_photo=True)`
  (:440): `get_entity()` + `GetFullUserRequest` /
  `GetFullChannelRequest` / `GetFullChatRequest` → `make_profile()`; `get_entity()` có thể trả
  về đối tượng **"min" chỉ có id**, nên username được lấy thêm từ full info hoặc từ chính
  tham số người dùng nhập (nếu là username hợp lệ). Kênh/nhóm thì lấy tiếp tin gần đây qua
  `fetch_history(..., prefer_username=...)` để nhãn kênh/link trong báo cáo không bị thành ID.
  Phần **chi tiết hồ sơ** (yêu cầu m01206) lấy thêm: `phone` (từ `entity.phone` **trước**, vì
  `GetFullUserRequest(me).full_user.phone` rỗng — rồi mới tới `full_user.phone`),
  `common_chats_count`, `bot_commands` (từ `full_user.bot_info.commands`), `usernames` (gồm cả
  username phụ), `created_at` (`entity.date`), cờ `premium/scam/fake/restricted/mutual_contact/
  contact`, `lang_code`, và `last_seen` diễn giải từ `UserStatus*`. Ba helper tách riêng để test
  offline: `_usernames_of()` (:394), `_chat_row()` (:407), `_status_line()` (:421).
  `common_limit > 0` thì gọi `functions.messages.GetCommonChatsRequest(user_id=..., max_id=0,
  limit=...)` → danh sách **nhóm/kênh dùng chung**; `photo_dir` khác rỗng thì tải ảnh đại diện
  qua `client.download_profile_photo(entity, file=<photo_dir>/<username>.jpg)` (ảnh chỉ tải khi
  `getattr(entity, 'photo', None)` có dữ liệu). **Lỗi đã sửa:** `target` kiểu số (id) làm
  `target.lstrip()` ném `AttributeError: 'int' object has no attribute 'lstrip'` → nay dùng
  `str(target or "").lstrip("@")`.
- `search_chats(client, keyword, limit)` (:591): `contacts.SearchRequest` → **cả `.chats` lẫn `.users`**
  (bản đầu chỉ đọc `.chats` nên bỏ sót toàn bộ người dùng — đúng chỗ có `@hathanh`), mỗi dòng trả
  `{"username", "keywords", "title", "name", "kind", "scale"}` với `kind` theo mã của
  `PROFILE_KIND_VI` (kênh/nhóm/người dùng/bot) và `scale` kiểu "213 người đăng ký"; các dòng này
  được đưa vào `related_usernames(..., extra_usernames=...)` với vai trò `search`. **Lỗi đã sửa:** `search_global()` cũ có dòng sắp xếp theo
`r["analysis"]["scam_score"]` nhưng lúc đó `analysis` **chưa tồn tại** → sắp xếp vô hiệu và
có thể `KeyError`; dòng đó đã bỏ, việc sắp xếp chuyển về `run_async()` **sau khi chấm điểm**.
`report()` nay là wrapper gọi `print_report(..., noun=NOUN_API, title="BÁO CÁO THU THẬP
TELEGRAM QUA API (Telethon)")`, chịu được JSON thiếu `analysis`/`groups`.

### 4.4. Chế độ bot và `--watch` (:679-1098)

- `bot_mode(args)` (:683): dùng phiên bot khi có token (`--bot-token` / `TG_BOT_TOKEN` /
  khóa `bot_token` đã lưu) — **trừ khi** có `--login` hoặc `--session <tên>` không bắt đầu
  bằng `tg_bot` (khi đó người dùng muốn phiên tài khoản).
- `run_setup_bot()`: kiểm tra token, cảnh báo giới hạn của bot (chỉ thấy tin **mới**,
  không đọc lịch sử, không `--search`), liệt kê các nhóm/kênh bot đang ở, rồi lưu credential.
- `run_bot_mode()`: xử lý `--setup-bot` và `--login-bot`.
- `run_watch()`: `@client.on(events.NewMessage(chats=targets or None))` → mỗi tin mới được
  chuyển thành record → tải media/OCR (nếu bật) → `analyze()` → in
  `[score/100 | level] @kênh/id  category`. `--cycles N` dừng sau N tin (ngắt client);
  `--watch-once` chờ tối đa `--interval` giây rồi thoát. Kết thúc ghi JSON backend
  `"telethon-bot"`/`"telethon-watch"` và in báo cáo.
- `run_async()` (:856): chọn `run_watch` / bot / tài khoản người dùng; in rõ loại phiên
  (`BOT`/`TÀI KHOẢN NGƯỜI DÙNG`); `--login`/`--login-bot` chỉ xác nhận phiên rồi thoát;
  chống trùng theo `kênh/message_id`; **sắp xếp sau khi chấm điểm**; xử lý `--user` (mục
  `[0]`, truyền `photo_dir`/`common_limit` xuống `lookup_entity`) và `--keyword` (mục `[6]`,
  kèm tìm chat), ghi tất cả vào JSON qua `write_json(..., extra=...)`.
- `main()` (:989): argparse với `--demo`, `--login`, `--api-id`, `--api-hash`, `--channel`,
  `--search`, `--media-dir`, `--ocr`, `--limit 30`, `--out posts_api.json`, `--no-report`,
  `--report`, `--login-bot`, `--setup-bot`, `--bot-token`, `--session`, `--watch`,
  `--watch-once`, `--interval 60`, `--cycles 0`, `--user`, `--keyword`, `--photo-dir`,
  `--no-photo`, `--common-limit 20`. Không chọn nguồn nào
  (kể cả `--keyword`) → chạy `--demo`; `--keyword` vẫn chạy offline được với `--demo`.

## 5. `keyword_usernames.py` + `username_info.py` — hai công cụ tách riêng

Hai file này **chỉ làm một việc** (người dùng yêu cầu tách khỏi bản demo đầy đủ), dùng lại
toàn bộ lõi ở `analyzer.py`:

| File | Việc | Hàm chính |
|---|---|---|
| `keyword_usernames.py` | keyword → danh sách username liên quan | `records_from_json()` :69, `records_from_web()` :81, `_collect_api()` :112, `filter_rows()` :156, `run()` :162 |
| `username_info.py` | username → thông tin tài khoản | `usernames_from_json()` :48, `lookup_web()` :66, `_lookup_api()` :87, `run()` :136, `main()` :197 |

- Nguồn dữ liệu của chức năng 1 trộn được: `--demo`, `--channel` (qua `demo_telegram`),
  `--seed-file`, `--json` (`records_from_json()` **chấm lại** bằng `analyze()` hiện tại),
  `--api` (`demo_telegram_api.fetch_history` + `search_chats`). Chỉ khi dùng `--api` mới
  `import demo_telegram_api` (nạp muộn trong `_import_api()`), nên mặc định vẫn thuần stdlib.
- `--min-score` / `--top` lọc bằng `filter_rows()`; `--out` ghi JSON qua `write_json(..., extra=...)`
  với các khóa `keywords`/`usernames`/`invite_links`.
- Chức năng 2: bản web dùng `demo_telegram.lookup_user(name, photo_dir=..., save=...)` +
  `scrape_channel()` (kênh, nếu `--limit > 0`); bản `--api` dùng
  `demo_telegram_api.lookup_entity(..., photo_dir=..., common_limit=...)` để có thêm ID/verified,
  số điện thoại, trạng thái, nhóm chung. Cờ riêng của công cụ này: `--photo-dir DIR` (mặc định
  `profile_photos`), `--no-photo` (đặt `args.photo_dir = ""` ở đầu `run()` nên cả hai nhánh đều
  không tải ảnh), `--common-limit N`.
  `--from-json` đọc thẳng file do chức năng 1 xuất ra (`usernames_from_json()`), nên hai công
  cụ nối được thành một pipeline: tìm username → tra từng username.

**Mặc định khi không chọn nguồn dữ liệu (yêu cầu m01082)** — `run()` kiểm tra
`not (args.demo or args.json or channels or args.api)`: nếu đúng thì tự đặt `args.api = True` và in
`[i] Không chọn nguồn dữ liệu → tìm trực tiếp trên Telegram bằng API (contacts.Search; ...)`. Nhờ vậy
lệnh ngắn nhất `.venv-api/bin/python keyword_usernames.py --keyword "hathanh"` chạy được ngay; nếu
thiếu từ khoá thì báo `[!] Cần ít nhất một từ khoá: --keyword TUKHOA` và trả 1. Đây là cách **duy
nhất** tìm được tài khoản khi chưa biết đúng username (trang công khai `t.me/<x>` chỉ tra đúng
username, `contacts.Search` mới tìm "hơi giống").

**In gọn vs in đầy đủ** — `print_username_section(..., compact=False)` (:891) và helper
`_entry_label(row)` (:876) in hậu tố `— Tên (Loại, quy mô)` từ `name`/`kind`/`scale` mà
`search_chats` cung cấp (đã mang qua `related_usernames()` trong vòng lặp `extra_usernames`).
`keyword_usernames.py` chọn `compact = args.compact or (not args.full and not records)`: chỉ có kết
quả tìm kiếm thì in mỗi username một dòng, có bài để chấm điểm thì in khối chi tiết như cũ.

**Xếp hạng** — `related_usernames()` sắp bằng `rank()`: `(-max_score, prefix, -posts, username)`,
trong đó `prefix = 0` nếu username (hoặc bản bỏ dấu) **bắt đầu bằng** từ khoá. Nhờ vậy
`--keyword "hathanh"` cho `@hathanh`, `@hathanh7`… lên trước `@NoloveShopbot` thay vì thuần alphabet.

**Cờ `--text-only` (chức năng 1)** — sửa trong `analyzer.py`:
`_record_text(record, only_text)` (:734), `related_usernames(..., only_text=False)` (:746),
`related_invite_links(..., only_text=False)` (:851), `username_payload(..., only_text=False)` (:939).
Khi bật: chỉ khớp từ khoá trong `text` + `link_preview`, **không** lấy `url` của bài làm nguồn
link và **không** tính vai trò "bài đăng trong kênh". Lý do cần cờ này: `nhom1.json` có
`channel_title = "Sảnh Chat GNCT : Tin tức chiến sự Iran - Mỹ - Israel"`, nên từ khoá
`"chiến sự"` khớp **cả 1000 bài** và trả về đúng kênh đó — đúng về mặt "username liên quan"
nhưng gây nhiễu khi người dùng muốn tìm trong nội dung. Đã kiểm chứng: bật `--text-only` thì
`"chiến sự"` trên `nhom1.json` ra **0 username** (không bài nào nhắc t.me), còn mặc định ra
`@aiquochientrang` (link 1000, bài đăng trong kênh 1000).

## 6. `find_private_groups.py` — nhóm kín

Ba bước tách rời (`--discover` / `--join` / `--crawl`), `--session` riêng, và `Found` (:138)
lưu kết quả bước trước cho bước sau. `RE_INVITE` (:79) nhận cả `t.me/+hash` và
`t.me/joinchat/hash`.

**Hai lỗi đã sửa:**

1. Dòng cũ `getattr(msg, "reply_markup, None", None)` — **dấu phẩy nằm trong chuỗi tên
   thuộc tính**, nên `getattr` luôn trả `None` và URL trong nút inline **không bao giờ**
   được bóc. Đúng phải là `getattr(msg, "reply_markup", None)`.
2. Bước join cũ chỉ coi link chứa `"/+"` là link mời; `t.me/joinchat/xxx` (dạng cũ) bị đưa
   vào `JoinChannelRequest()` → sai API. Nay dùng
   `re.search(r"(?:\+|joinchat/)([\w-]{8,})", link)`: có hash thì gọi
   `ImportChatInviteRequest(hash)`, chỉ `@username` công khai mới gọi `JoinChannelRequest`.

Mặc định an toàn: tối đa **5** link, nghỉ **20** giây, dừng ngay khi gặp `FloodWaitError`.
Thông báo khi chưa đăng nhập in đúng lệnh cần chạy
(`python3 demo_telegram_api.py --login --session <tên>`).

## 7. `tests/test_regression.py`

Mốc kiểm chứng **50 test** dưới đây là của ngày **2026-10-11**, không phải tổng test sau
khi thêm SQLite. Các ca hồi quy **không cần mạng** (một số ca dùng Telethon giả lập sẽ
skip nếu thiếu thư viện); chạy `python3 -m unittest discover -s tests -v`:

- `TestScoring`: `normalize`/`no_accent`; ngưỡng 25/60; 5 mẫu **báo động giả** nổi tiếng
  (`hermibooks`, `fbclid`, "phân công anh ta", "người nộp thuế", "mức thuế") chỉ được có
  tín hiệu URL và điểm ≤ 12; bài scam thật vẫn ≥ 60/High-risk; `extract_entities` loại số
  tiền `1.500.000`, `12,345,678` nhưng nhận STK `1234567890123`.
- `TestGroupScoring`: tái tạo **chính xác** 8 nhóm của `posts_tg.json` (noun `"bài"`) và
  nhóm thật trong `nhom1.json` (phải truyền `noun=analyzer.NOUN_API` vì dữ liệu lịch sử
  dùng chữ "tin"); kiểm tay một công thức group; dữ liệu thật chỉ còn 1 bài bị gắn cờ,
  0 High-risk.
- `TestWebParser`: `channel_name`, `parse_count`, `_text_of`, `parse_channel_info`,
  `parse_messages` trên một trang HTML giả (2 bài, 2 ảnh, forward `"nguồn ẩn"`,
  link preview vnexpress, hashtag).
- `TestUsernameFeatures`: `parse_profile` với 5 trang HTML giả (kênh/nhóm/bot/người dùng/
  không tồn tại); `RE_USERNAME` giữ dấu `.`; `_username_of(strict=True)` không nhận tên
  hiển thị; `username_payload` trên 17 bài demo (điểm 71 High-risk, đủ 5 vai trò, link mời
  `t.me/+abcdef123`); `--help` có `--user`/`--keyword`; `--report` in lại được mục `[6]`.
- `TestBackendsEndToEnd`: chạy thật `--demo --limit 17` của **cả hai** backend (kể cả biến
  thể `--keyword`) và so bản web với `posts_tg.json` (bỏ qua các khóa thay đổi theo thời
  gian: `collected_at`/`posted_at`/`backend`/`generated_at`).
- `TestSplitTools` (11 ca): hai file tách riêng — chạy thật `keyword_usernames.py --demo` (4 username,
  `invite_links` `t.me/+abcdef123`) và `--json posts_tg.json --min-score 40` (chỉ còn
  `vieclam.ctv.hoantien`); `only_text=True` loại trường hợp từ khoá chỉ có trong TÊN KÊNH;
  `--loose` khớp `ib` trong "hermibooks" còn mặc định thì không; `filter_rows`; đọc danh sách
  username từ JSON; `--help` và trường hợp thiếu đầu vào của `username_info.py`; không nguồn +
  không từ khoá → "Cần ít nhất một từ khoá"; `_entry_label`/in gọn ra đúng dạng
  `@hathanh  —  Thanh Hà  (Người dùng)`; `rank()` xếp khớp-đầu-từ-khoá trước alphabet; và
  `search_chats()` với client giả đọc **cả `.chats` lẫn `.users`** (ca này `skipTest` nếu máy
  không có Telethon, nên bộ test vẫn chạy được offline ở máy khác).
- `TestProfileDetails` (8 ca, yêu cầu m01206): `parse_profile` lấy `photo_url`/`has_photo` và
  **không** gán ảnh cho trang không tồn tại; `save_photo()` ghi đúng byte tải được (thay
  `web.fetch_bytes` bằng lambda, không cần mạng); ba biến thể của `_phone_line()` (có số / rỗng ở
  bản API / bản web "cần --api"); `print_profile()` in đủ dòng số điện thoại, ảnh đại diện, hoạt
  động, ngày tạo, username khác, cờ Premium, lệnh bot, và khối `Nhóm chung` (`65 784 thành viên`); `_photo_line()`/`_flags_line()`; các helper thô của bản API không cần kết
  nối (`_status_line()` với lớp giả `UserStatusOffline`, `_usernames_of()`, `_chat_row()`); và
  `session_path()` không ghép tiền tố hai lần khi người dùng ghi tên phiên đầy đủ.

### Test bổ sung cho SQLite

[`tests/test_storage.py`](tests/test_storage.py) kiểm tra bằng DB tạm, không dùng DB sản
xuất hay phiên đăng nhập Telegram: schema v1 / WAL / khóa ngoại / `busy_timeout` / quyền
`0600`; roundtrip JSON giữ Unicode và trường chưa biết; chống trùng message / payload,
lịch sử phân tích; demo tách dữ liệu thật; đổi username theo ID ổn định, peer type cùng
số ID, alias bị gán lại và làm giàu thực thể web khi API có ID; snapshot hồ sơ / nhóm
chung / kết quả tìm kiếm / link mời; truy vấn không dấu và ký tự đặc biệt SQL; rollback;
import idempotent không chấm lại; từ chối credential, DB không phải kho dự án và schema
mới hơn; backup nhất quán có WAL; `--no-db` và persist từ các CLI với nguồn offline /
hồ sơ giả lập. Không suy ra tổng test mới từ mốc 50 test lịch sử ở trên.

## 8. Các file JSON — tất cả nằm trong `json_folder/`

**Quy ước (m01591):** mọi file `.json` của project nằm trong `json_folder/`, kể cả
`json_folder/tg_api.json` (credential, `chmod 600`). Các cờ `--out`, `--report`, `--json`,
`--in`, `--from-json` nhận **tên trần** và tự hiểu là file trong `json_folder/`; truyền
đường dẫn có thư mục (hoặc tuyệt đối) thì script giữ nguyên.
Hàm dùng chung ở **cuối** [`analyzer.py`](analyzer.py) (mục "13. Đường dẫn JSON"):
`json_dir()`, `json_path(name)`, `resolve_json(path)`, `_ensure_dir(path)` — trước đây đặt ở
cuối file để hạn chế dịch số dòng tài liệu; các mốc dòng cũ nay chỉ dùng để định hướng.

| File (trong `json_folder/`) | `backend` | Có `groups`? | Ghi chú |
|---|---|---|---|
| `posts_tg.json` | `demo (offline)` | ✅ | 17 bài mẫu, tạo lại bằng code hiện tại |
| `posts_api.json` | `telethon` | ❌ | 100 bài thật qua API, có `forwards`/`replies` |
| `nhom1.json` | `telethon-user` | ✅ | 1000 bài thật `@aiquochientrang` |
| `real_tg.json` | — | ❌ | 5 bài `@durov`, có `subscribers` |
| `real_tg_vn.json` | — | ❌ | 16 bài `@baomoi`/`@tintucvtv` |
| `found.json`, `found_groups.json` | — | — | kết quả `--discover` (link mời, chưa join) |

Hai công cụ tách riêng có thể tự ghi file: `keyword_usernames.py --out` (cùng khóa
`keywords`/`usernames`/`invite_links`) và `username_info.py --out` (khóa `profiles`).

Khóa **tuỳ chọn** xuất hiện khi dùng tính năng mới: `profiles` (kết quả `--user`),
`keywords` + `usernames` + `invite_links` (kết quả `--keyword`).

Record bài đăng (schema chung): `channel`, `channel_title`, `message_id`, `url`, `text`,
`hashtags`, `posted_at`, `collected_at`, `views`, `has_media`, `media_types`, `media_count`,
`link_preview`, `forwarded_from`, `author`, `subscribers`, `source`, `analysis`
(`scam_score`, `level`, `scam_category`, `scam_category_vi`, `signals`, `entities`, `summary`).
Bản API thêm `forwards`, `replies`.

⚠️ `nhom1.json` là dữ liệu **lịch sử**: `analysis`/`groups` bên trong được tính bằng bộ từ
khoá **cũ** (khớp chuỗi con, còn `"thuế"` trần), nên **chấm lại sẽ ra số hơi khác**. File
`posts_tg.json` đã được tạo lại bằng code hiện tại nên có `backend` và không còn bài 1100 = 47.

## 9. Di sản không khôi phục được (ghi để minh bạch)

- **Bài `vieclam.ctv.hoantien` id 1100**: bản lưu cũ ghi 47 điểm, code hiện tại cho **36**.
  Chênh lệch 11 điểm = `0.6 * 18` — tức bản cũ có nhóm `offplatform` (18) với **một** từ
  khoá khớp, và từ khoá đó là `"bot"` khớp chuỗi con trong `https://t.me/vieclam_bot`.
  Từ khoá `"bot"` **không tồn tại** trong bộ từ điển hiện tại (đã kiểm chứng: ép tắt khớp
  ranh giới từ vẫn ra 36) và không có cơ sở để thêm lại ⇒ **giữ nguyên, ghi tài liệu**.
- **`repeat_top_text`**: chỉ có một mẫu lịch sử (`nhom1.json`) nên ngữ nghĩa ở mục 2.8 là
  **suy luận**, không phải chắc chắn 100%.
- `demo_telegram_api_FIXED.py` từng là bản **trùng byte-for-byte** của `demo_telegram_api.py`;
  đã xoá khỏi repo (vẫn xem lại được ở commit baseline `8c1bf63`).
- Phiên bản "đã lớn" trước đây của `demo_telegram_api.py` (sinh ra `nhom1.json`) đã mất cùng
  `analyzer.py`, `demo_telegram.py` cũ và `GIAI_THICH_CODE.md` cũ. Bản hiện tại là bản viết
  lại, kiểm chứng bằng cách **dò ngược** công thức group trên 9 nhóm dữ liệu còn lưu.

## 10. Kiểm chứng (chạy thật 2026-10-11)

| Kiểm chứng | Kết quả |
|---|---|
| `analyze()` mới vs bản cũ trên 17 bài demo | **0 bài khác** |
| `group_summary()` vs `posts_tg.json` + `nhom1.json` | khớp **100%** mọi khóa (9 nhóm) |
| Dữ liệu thật 1000 bài (`nhom1.json`) chấm lại | 8 bài bị gắn cờ → **1** (0 High-risk) |
| 17 bài demo | 7 Normal / 8 Suspicious / 2 High-risk (58.8%) |
| `@durov` 25 bài (web) | group **11/100 Normal**, 10.4M subs, views 325K–6.87M |
| `@baomoi` + `@tintucvtv` (web, 20 bài/kênh) | 40 bài → **11/100** và **2/100**, đều Normal |
| `--user durov/BotFather/vitalikbuterin/pythonchat/khong_ton_tai_xyz_999` (web) | phân loại đúng Kênh ✔ / Bot ✔ / Người dùng / Nhóm / không tra được |
| `--user durov/BotFather/pythonchat` (API) | id 1006503122 / 93372553 / 1247302826, verified đúng, `@durov` 10 397 039 subs |
| `--keyword "chiến sự"` (API, chỉ tìm chat) | 7 username: `@aiquochientrang`, `@bantronchiensu`, `@hongchiensu247`, `@ngavaukraine`, `@tinnongchiensu`, `@tintucchiensu247`, `@tintuctoanthegioi` |
| `--channel aiquochientrang --keyword "chiến sự"` (API) | 5 bài + 7 username; `@aiquochientrang` gộp 3 vai trò (link t.me 5, bài đăng trong kênh 5, kết quả tìm chat 1) |
| `--keyword "hoàn tiền"` (web, chế độ `--demo`) | 2 username (`@vieclam.ctv.hoantien`, `@vieclam_bot`) + link mời `t.me/+abcdef123` |
| `keyword_usernames.py --demo --keyword "đầu tư" --keyword "hoàn tiền"` | 4 username, top `@dautu.sieuloinhuan` 71 High-risk; link mời `t.me/+abcdef123` |
| `keyword_usernames.py --channel durov --keyword "telegram" --limit 5` | 5 bài → 1 username `@durov` (11/100 Normal) |
| `keyword_usernames.py --json nhom1.json --keyword "chiến sự"` | 1000 bài → `@aiquochientrang`; thêm `--text-only` → **0 username** (đúng: không bài nào nhắc t.me) |
| `keyword_usernames.py --api --keyword "chiến sự"` | 7 chat từ `contacts.Search` (không cần kênh nguồn) |
| `username_info.py durov @BotFather pythonchat vitalikbuterin khong_ton_tai_xyz_999` | Kênh ✔ / Bot (7 560 507 người dùng/tháng) / Nhóm 107 / Người dùng / không tra được |
| `username_info.py durov --limit 25` | kèm thống kê: 5 bài, điểm TB 8.8, mức 7/100 Normal |
| `username_info.py durov pythonchat --api --limit 3` | id 1006503122 / 1247302826, verified, group kín tra được |
| `username_info.py --from-json usernames.json` | đọc thẳng danh sách do chức năng 1 xuất ra |
| `.venv-api/bin/python keyword_usernames.py --keyword "hathanh"` | không cần cờ nguồn → tự dùng API, 10 username kèm tên/loại, `@hathanh` Thanh Hà đứng đầu |
| `username_info.py durov pythonchat --photo-dir /tmp/pp` (web) | tải ảnh thật `durov.jpg` (10 425 byte) và `pythonchat.jpg` (15 472 byte), đều là JPEG hợp lệ (`FFD8…FFD9`); dòng "Số điện thoại: (cần --api…)" |
| `.venv-api/bin/python username_info.py BotFather sizedevchill hathanh --api --photo-dir /tmp/pp` | BotFather: **12 lệnh bot** + ảnh 99 537 byte; `@sizedevchill`: `84981726384`, "truy cập lần cuối 10/10/2026 12:39 UTC", "trong danh bạ"; `@hathanh`: "truy cập lần cuối 13/03/2026 14:07 UTC", không ảnh |
| `.venv-api/bin/python username_info.py BOSSDZAIGK88 --api --limit 0` | `Nhóm chung: 1 nhóm/kênh dùng chung — VÀO CÓ 1 CHIỆU 💴💶 (Nhóm, 65 784 thành viên)` |
| Test hồi quy | **50/50 PASS** ở `.venv-api/bin/python` (system python3: 49 PASS + 1 skip vì thiếu Telethon) |
| Ghi đè `api.py` trùng lặp | đã xoá `demo_telegram_api_FIXED.py` |

## 11. Muốn thêm từ khoá / đổi trọng số thì sửa ở đâu

1. Mở `analyzer.py`, thêm từ khoá vào `SCAM_GROUPS[...]` hoặc `ACTION_SIGNALS[...]` (:37/:72).
2. Chạy `python3 -m unittest discover -s tests -v` — nếu test dữ liệu thật/demo đổi số, cân
   nhắc lại (đặc biệt là các mẫu báo động giả ở `TestScoring`).
3. Cập nhật lại các số liệu trong `README.md` nếu kết quả `--demo` đổi.
4. Thêm một ca mới vào `tests/test_regression.py` để khoá hành vi vừa sửa.

Muốn thêm **cách nhận diện username mới** (ví dụ tra cả `t.me/c/...`): sửa `RE_USERNAME` (:695)
rồi bổ sung ca trong `TestUsernameFeatures`. Muốn thêm **dấu hiệu phân loại trang tài khoản**:
sửa `parse_profile()` (`demo_telegram.py:196`) — nhớ kiểm tra bằng HTML thật vì Telegram đổi giao diện theo thời gian.
Muốn thêm **chi tiết hồ sơ** (ví dụ story, quà tặng, doanh nghiệp): thêm khóa vào `make_profile()`
(`analyzer.py:977`), điền ở `lookup_entity()` (`demo_telegram_api.py:440`) và/hoặc
`parse_profile()` (`demo_telegram.py:196`) tuỳ backend, rồi in trong `print_profile()`
(`analyzer.py:1054`) — nhớ cập nhật cả `TestProfileDetails`.

Quy tắc rút ra từ chính project này: **đừng dùng từ khoá ngắn/trần** (`ib`, `stk`, `bot`,
`thuế`) — chúng khớp vào giữa từ tiếng Anh, chuỗi base64 hoặc từ ghép tiếng Việt. Hãy dùng
cụm từ đủ đặc trưng, và luôn kiểm chứng trên dữ liệu thật trước khi commit.
