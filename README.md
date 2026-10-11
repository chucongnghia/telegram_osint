# Demo lấy bài đăng từ Telegram + phát hiện dấu hiệu lừa đảo

Bộ công cụ nhỏ để: **thu thập bài đăng Telegram → chấm điểm nghi ngờ lừa đảo (0–100) →
tính điểm rủi ro cho cả group/kênh → in báo cáo**. Có hai backend thu thập độc lập dùng
chung một bộ chấm điểm.

## File

| File | Việc |
|---|---|
| [`analyzer.py`](analyzer.py) | **Nguồn chân lý dùng chung**: từ điển, trọng số, regex, `analyze()`, `group_summary()`, `print_report()`, `write_json()`, dữ liệu mẫu `demo_posts()`. Chỉ thư viện chuẩn. |
| [`demo_telegram.py`](demo_telegram.py) | Backend **web**: đọc trang công khai `https://t.me/s/<kênh>`. **Chỉ thư viện chuẩn** — không cần cài gì, không cần API key, không cần đăng nhập. |
| [`demo_telegram_api.py`](demo_telegram_api.py) | Backend **Telegram API (Telethon)**: group kín, tìm kiếm toàn cục, tải media, OCR, chế độ bot, `--watch`. |
| [`find_private_groups.py`](find_private_groups.py) | Tìm & đọc **group kín** qua link mời, chia 3 bước `--discover` / `--join` / `--crawl`. |
| [`tests/test_regression.py`](tests/test_regression.py) | Test hồi quy (23 test, không cần mạng/Telethon). |
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
| `python3 -m unittest discover -s tests` | 23 test PASS |

## Chạy test

```bash
python3 -m unittest discover -s tests -v      # không cần mạng, không cần Telethon
```

Test khoá lại: điểm 17 bài demo khớp `posts_tg.json`; công thức group tái tạo đúng 8 nhóm
demo + nhóm thật 1000 bài; các báo động giả nổi tiếng không còn; tin tức thật chỉ còn
1/1000 bài bị gắn cờ; bộ đọc HTML tách đúng bài/views/media/link preview.

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
