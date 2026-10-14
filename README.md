# Demo lấy bài đăng từ Telegram + phát hiện dấu hiệu lừa đảo

Bộ công cụ nhỏ để: **thu thập bài đăng Telegram → chấm điểm nghi ngờ lừa đảo (0–100) →
tính điểm rủi ro cho cả group/kênh → in báo cáo**. Có hai backend thu thập độc lập dùng
chung một bộ chấm điểm.

## File

| File | Việc |
|---|---|
| [`keyword_usernames.py`](keyword_usernames.py) | **Chức năng 1, tách riêng**: nhập keyword → in danh sách username liên quan. |
| [`username_info.py`](username_info.py) | **Chức năng 2, tách riêng**: nhập username → in thông tin tài khoản đó. |
| [`analyzer.py`](analyzer.py) | **Nguồn chân lý dùng chung**: từ điển, trọng số, regex, `analyze()`, `group_summary()`, `print_report()`, `write_json()`, `username_payload()` / `related_usernames()` (`--keyword`), `make_profile()` / `print_profile()` (`--user`), dữ liệu mẫu `demo_posts()`. Chỉ thư viện chuẩn. |
| [`demo_telegram.py`](demo_telegram.py) | Backend **web**: đọc trang công khai `https://t.me/s/<kênh>` (và `https://t.me/<username>` cho `--user`). **Chỉ thư viện chuẩn** — không cần cài gì, không cần API key, không cần đăng nhập. |
| [`demo_telegram_api.py`](demo_telegram_api.py) | Backend **Telegram API (Telethon)**: group kín, tìm kiếm toàn cục, tra cứu username/ID, tìm chat theo từ khoá, tải media, OCR, chế độ bot, `--watch`. |
| [`find_private_groups.py`](find_private_groups.py) | Tìm & đọc **group kín** qua link mời, chia 3 bước `--discover` / `--join` / `--crawl`. |
| [`tests/test_regression.py`](tests/test_regression.py) | Test hồi quy (39 test, không cần mạng/Telethon). |
| [`GIAI_THICH_CODE.md`](GIAI_THICH_CODE.md) | Giải thích chi tiết từng phần của code + công thức + các lỗi đã sửa. |
| [`posts_tg.json`](posts_tg.json) | Kết quả `--demo` của bản web (17 bài mẫu, có `groups`). |
| [`posts_api.json`](posts_api.json) | Kết quả bản API (100 bài, `backend: telethon`). |
| [`nhom1.json`](nhom1.json) | Dữ liệu **thật** 1000 bài của group `@aiquochientrang` (`backend: telethon-user`). |
| [`real_tg.json`](real_tg.json) / [`real_tg_vn.json`](real_tg_vn.json) | Dữ liệu thật `@durov` / `@baomoi`, `@tintucvtv`. |
| [`found.json`](found.json), [`found_groups.json`](found_groups.json) | Kết quả bước `--discover` của `find_private_groups.py`. |
| [`channels.txt`](channels.txt) | Danh sách kênh mẫu cho `--seed-file`. |

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
(mặc định 20). `--out` mặc định là `posts_web.json` (cố ý khác `posts_tg.json` để không
ghi đè file kết quả mẫu).

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
- Nếu đối tượng là **kênh/nhóm**, script còn lấy luôn bài gần đây và in thống kê
  (số bài, điểm TB, cao nhất, mức rủi ro) — tức mục `[0] THÔNG TIN TÀI KHOẢN TRA CỨU`.
- Kết quả được lưu vào khóa `profiles` trong file JSON, `--report` đọc lại và in tiếp.

> Chỉ cần đúng việc "username → thông tin"? Dùng bản gọn
> [`username_info.py`](username_info.py) (nhận nhiều username, `--api`, `--from-json`).

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

### Chức năng 2 — `username_info.py`: username → thông tin tài khoản

```bash
python3 username_info.py durov
python3 username_info.py durov @BotFather pythonchat vitalikbuterin
python3 username_info.py durov --limit 25 --out profile.json     # kèm thống kê bài đăng
.venv-api/bin/python username_info.py durov pythonchat --api     # thêm ID/verified
python3 username_info.py --from-json usernames.json              # nối tiếp chức năng 1
```

In loại tài khoản (Kênh / Nhóm / Bot / Người dùng), tên hiển thị, giới thiệu, quy mô,
`--api` thì thêm **ID, verified, is_bot** và tra được **group kín** đã tham gia… Nếu tài
khoản là kênh/nhóm, `--limit N` lấy thêm N bài gần đây để in thống kê (điểm TB, cao nhất,
số bài cần rà soát, mức rủi ro). `--from-json` đọc danh sách username do chức năng 1 xuất ra.

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
| `username_info.py durov @BotFather pythonchat vitalikbuterin` | Kênh ✔ / Bot / Nhóm 107 / Người dùng — phân loại đúng |
| `python3 -m unittest discover -s tests` | **39 test PASS** (cả system Python lẫn `.venv-api/bin/python`) |

## Chạy test

```bash
python3 -m unittest discover -s tests -v      # không cần mạng, không cần Telethon
```

Test khoá lại: điểm 17 bài demo khớp `posts_tg.json`; công thức group tái tạo đúng 8 nhóm
demo + nhóm thật 1000 bài; các báo động giả nổi tiếng không còn; tin tức thật chỉ còn
1/1000 bài bị gắn cờ; bộ đọc HTML tách đúng bài/views/media/link preview; phân loại trang
`t.me/<username>` (kênh/nhóm/bot/người dùng/không tồn tại); `--keyword` → `usernames` +
`invite_links` và `--report` đọc lại được.

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
| Tra cứu `--user` (kênh/nhóm/bot/người dùng) | ✅ (trang `t.me/<username>`) | ✅ (thêm ID, verified, group kín) |
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

Sau `--setup-bot`, token được lưu vào `tg_api.json` nên các lần sau chỉ cần
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
hoặc file `tg_api.json`. **Bot token** qua `--bot-token`, `TG_BOT_TOKEN`, hoặc khóa
`bot_token` trong `tg_api.json`. `tg_api.json` và file phiên **không được commit** (đã có
trong `.gitignore`).

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

Sau khi `--login` thành công, script **tự ghi `api_id`/`api_hash` vào `tg_api.json`**
(quyền 600), nên các lệnh sau không phải nhập lại. Nếu chưa từng đăng nhập, có thể tự tạo file:

```json
{"api_id": 1234567, "api_hash": "0123456789abcdef0123456789abcdef"}
```

**Phiên không phụ thuộc vào `api_id`**: đã kiểm chứng cùng một `tg_session_*.session` chạy
được với cả `api_id` công khai 2040 lẫn một `api_id` bất kỳ khác — vì `api_id`/`api_hash`
định danh **ứng dụng**, không phải tài khoản. Tuy vậy nên dùng `api_id` của chính bạn.

### Phiên lưu ở đâu, và phân biệt BOT với TÀI KHOẢN NGƯỜI DÙNG

`--session <tên>` đặt tên phiên; file luôn nằm cạnh script:

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
Project đặt `chmod 600` cho `tg_api.json` và các file phiên, và đã `.gitignore` chúng.
Kiểm tra lại bằng `ls -l *.session tg_api.json` (phải là `-rw-------`).

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
