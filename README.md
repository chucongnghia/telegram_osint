# Demo lấy bài đăng từ Telegram + phát hiện dấu hiệu lừa đảo

## File
- [`demo_telegram.py`](demo_telegram.py) — file Python duy nhất: thu thập bài đăng + chấm điểm nghi ngờ.
  **Chỉ dùng thư viện chuẩn của Python** — không cần cài gì, không cần API key, không cần đăng nhập.
- [`posts_tg.json`](posts_tg.json) — kết quả chế độ `--demo` (dữ liệu mẫu, 17 bài).
- [`real_tg.json`](real_tg.json) — kết quả thu thập **thật** từ kênh công khai `@durov`.
- [`real_tg_vn.json`](real_tg_vn.json) — kết quả thu thập **thật** từ kênh tiếng Việt `@baomoi`, `@tintucvtv`.
- [`channels.txt`](channels.txt) — danh sách kênh mẫu cho `--seed-file`.
- [`demo_telegram_api.py`](demo_telegram_api.py) — bản dùng **Telegram API (Telethon)**: group kín,
  tìm kiếm toàn cục, tải media, OCR, và **chế độ bot** (`--bot-token`) không cần số điện thoại.
- [`analyzer.py`](analyzer.py) — bộ từ điển + chấm điểm tách riêng để tái sử dụng.

## Chạy
```bash
python3 demo_telegram.py --demo                       # offline, dữ liệu mẫu (không cần mạng)
python3 demo_telegram.py --channel durov --limit 25   # kênh công khai (chạy thật)
python3 demo_telegram.py --channel baomoi --channel tintucvtv --limit 20
python3 demo_telegram.py --seed-file channels.txt     # mỗi dòng 1 kênh hoặc link t.me
python3 demo_telegram.py --report posts_tg.json       # in lại báo cáo từ JSON đã lưu
```
Không truyền nguồn nào thì mặc định chạy `--demo`. `--channel` nhận `durov`, `@durov`, `t.me/durov`, `https://t.me/s/durov`.

## Lấy dữ liệu bằng cách nào
Dùng trang xem trước công khai `https://t.me/s/<kênh>` (HTML tĩnh, không cần token):
- Thông tin kênh: tên, mô tả, số người đăng ký (10.6M → 10600000).
- Mỗi bài: `message_id`, `url`, `text`, `hashtags`, `posted_at`, `views`, `author`,
  `forwarded_from`, `has_media`, `media_types`, `media_count`, `link_preview`.
- Tự **phân trang** bằng `?before=<message_id>` để lấy nhiều hơn 20 bài (mỗi trang ~20 bài).
- Chống trùng lặp theo khóa `kênh/message_id`.

## Chấm điểm
- 6 nhóm lừa đảo (Investment, Job, Loan, Giveaway, Impersonation, Shopping)
  + 4 nhóm tín hiệu (chuyển tiền, hoa hồng, điều hướng sang kênh/nhóm khác, thúc ép).
- Trích xuất URL, số điện thoại, số tài khoản, `@mention`, **link mời nhóm** `t.me/+...`,
  và link preview. Thêm tín hiệu đặc thù Telegram: bài **forward** từ nguồn khác,
  ảnh/video kèm nội dung tài chính.
- Chuẩn hóa chữ thường + **bỏ dấu**, nên bắt được cả nội dung viết không dấu (`dautu`, `loinhuan`).
- Điểm 0–100 → mức 1: `Normal` (<25), `Suspicious` (25–59), `High-risk` (≥60);
  mức 2: nhóm lừa đảo điểm cao nhất. Điểm chỉ là **tín hiệu hỗ trợ rà soát**.

## Kết quả đã kiểm chứng
| Nguồn | Kết quả |
|---|---|
| `--demo` | 17 bài → 7 Normal / 8 Suspicious / 2 High-risk |
| `--channel durov --limit 25` | 25 bài thật, phân trang 2 trang, 10.6M người đăng ký, view 3.4M–24.3M |
| `--channel baomoi --channel tintucvtv` | 16 bài thật tiếng Việt, lấy được link preview |
| `--channel vnexpress` | báo lỗi rõ ràng (kênh không có bản xem trước công khai) |


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
| Lượt xem / forward / reply | ✅ | ✅ |


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
Giới hạn của bot (Telegram quy định, không phải lỗi code): **chỉ nhận tin nhắn mới** kể từ khi
được thêm vào nhóm, **không đọc được lịch sử cũ**, và **không dùng được** `contacts.Search`
(tìm kiếm toàn cục) — script sẽ tự cảnh báo và bỏ qua nếu bạn truyền `--search` ở chế độ bot.
Bot vẫn cần `api_id`/`api_hash`; token không thay thế cặp này.

Chạy bản API:
```bash
pip install telethon
python3 demo_telegram_api.py --login                     # đăng nhập 1 lần (hỏi OTP)
python3 demo_telegram_api.py --channel durov --limit 30  # kênh công khai
python3 demo_telegram_api.py --search "đầu tư lợi nhuận" # tìm kiếm toàn cục
python3 demo_telegram_api.py --demo                      # offline, không cần credential
```
Credential truyền qua `--api-id/--api-hash`, biến môi trường `TG_API_ID`/`TG_API_HASH`,
hoặc file `tg_api.json`. Phiên lưu ở `tg_session.session`; `tg_api.json` và file phiên
**không nên** commit lên git.


## Đánh giá RỦI RO ở mức GROUP (không chỉ từng bài đăng)

Yêu cầu chính khi thu thập lịch sử chat là trả lời câu hỏi *"cả nhóm này có phải lừa đảo không?"*,
nên cả hai bản đều tính **điểm rủi ro cho từng group/kênh**, in ở mục **[5]** của báo cáo và lưu
trong khóa `groups` của file JSON:

```
[5] ĐIỂM RỦI RO THEO GROUP/KÊNH
    🔴 [ 81/100 | High-risk ] Đầu tư tài chính - Lợi nhuận 30%/tháng (@dautu.sieuloinhuan)
        Có dấu hiệu lừa đảo — cần kiểm tra kỹ — 3 tin, điểm TB 59.7, cao nhất 71,
        3 tin cần rà soát (100%)
        Lý do: 3/3 tin (100%) ở mức cần rà soát; loại hình nổi bật: Đầu tư / lợi nhuận cao (3 tin);
               có tin đạt 71/100 (High-risk); điểm trung bình 60/100
```

Công thức điểm group (0–100):

| Thành phần | Trọng số | Ý nghĩa |
|---|---|---|
| Điểm trung bình các tin nhắn | 60% | mức độ nặng trung bình của cả nhóm |
| Mật độ tin cần rà soát | tối đa 25 | tỷ lệ Suspicious + High-risk trên tổng số tin |
| Độ tập trung loại hình | tối đa 10 | cùng một loại lừa đảo lặp lại nhiều lần |
| Tin nặng nhất | tối đa 15 | bằng chứng nặng nhất trong nhóm |

Kèm theo **lý do** (bao nhiêu tin bị gắn cờ, loại hình nổi bật, có spam lặp nội dung không) và
`verdict`: *Có dấu hiệu lừa đảo* / *Nghi ngờ* / *Chưa thấy dấu hiệu bất thường*.

Kết quả trên dữ liệu thật: `@durov` 16/100, `@baomoi` 15/100, `@tintucvtv` 2/100 (đều Normal —
đúng, vì đây là kênh tin tức); còn nhóm mẫu `@dautu.sieuloinhuan` đạt 81/100 High-risk.


### Credential được lưu tự động sau khi đăng nhập

Sau khi `--login` thành công, script **tự ghi `api_id`/`api_hash` vào `tg_api.json`** (quyền 600),
nên các lệnh sau không phải nhập lại. Nếu chưa từng đăng nhập, có thể tự tạo file:

```json
{"api_id": 1234567, "api_hash": "0123456789abcdef0123456789abcdef"}
```

**Phiên không phụ thuộc vào `api_id`**: đã kiểm chứng cùng một `tg_session_*.session` chạy được với
cả `api_id` công khai 2040 lẫn một `api_id` bất kỳ khác — vì `api_id`/`api_hash` định danh **ứng dụng**,
không phải tài khoản. Tuy vậy nên dùng `api_id` của chính bạn để tránh rủi ro bị chặn.

### Phân biệt phiên BOT và phiên TÀI KHOẢN NGƯỜI DÙNG (lỗi hay gặp)

Hai loại phiên lưu ở hai tên file khác nhau và **chỉ phiên người dùng mới đọc được lịch sử**:

| Loại phiên | Tên file | Đọc lịch sử? | Lệnh tạo |
|---|---|---|---|
| Người dùng | `tg_session.session` hoặc `tg_session_<tên>.session` | ✅ | `--login` |
| Bot | `tg_bot_session.session` hoặc `tg_bot_<tên>.session` | ❌ | `--login-bot --bot-token <token>` |

**Quan trọng:** nếu file phiên **đã tồn tại**, script sẽ **tái sử dụng** và không hỏi đăng nhập nữa —
kể cả khi bạn gõ `--login`. Vì vậy nếu bạn từng đăng nhập bằng bot rồi muốn chuyển sang tài khoản
người dùng, phải **xoá file phiên cũ trước**:

```bash
rm tg_session_tk-phu.session                      # xoá phiên cũ
.venv-api/bin/python demo_telegram_api.py --login --session tk-phu \
    --api-id <id> --api-hash <hash>                # hỏi SĐT + OTP → tạo phiên người dùng
```
Khi tái sử dụng phiên, script luôn in rõ loại phiên (`BOT` hay `TÀI KHOẢN NGƯỜI DÙNG`) và cảnh
báo nếu phiên bot không đọc được lịch sử.

## Giới hạn & hướng mở rộng
- Trang `/s/` chỉ có ~20 bài gần nhất mỗi trang và **chỉ kênh công khai**; group riêng tư
  hoặc kênh tắt preview thì không lấy được (script báo lỗi, không sập).
- **Tìm kiếm toàn cục theo từ khóa** hoặc đọc group riêng tư cần Telegram API
  (`api_id`/`api_hash` tại my.telegram.org + thư viện `Telethon`/`Pyrogram`); phần này
  chưa làm trong demo, có thể ghép vào cùng hàm `analyze()`.
- Các bước sau của pipeline (tải media → tách audio → Whisper STT → OCR frame) để trống
  trong `pipeline_status`, đúng lộ trình trong báo cáo.

## Tài liệu liên quan
- [`README.md`](README.md) — cách chạy nhanh và kết quả đã kiểm chứng.
- [`GIAI_THICH_CODE.md`](GIAI_THICH_CODE.md) — giải thích chi tiết từng phần của code.
