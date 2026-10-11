# Giải thích code

Tài liệu này giải thích **từng phần của code** trong project: luồng chạy, công thức chấm
điểm, ý nghĩa các khóa JSON, những lỗi đã sửa và những chỗ **suy luận/di sản** cần biết.

## 1. Kiến trúc

```
        analyzer.py  ──── nguồn chân lý dùng chung ────┐
   (từ điển, analyze, group_summary,                 │
    print_report, write_json, demo_posts)            │
            ▲                    ▲                    │
            │ import             │ import             │
   demo_telegram.py      demo_telegram_api.py         │
   (web, chỉ stdlib)     (Telethon: group kín,        │
            ▲             tìm kiếm, media, OCR,       │
            │             chế độ bot, --watch)       │
            │                    ▲                    │
    trang t.me/s/<kênh>   Telegram API        find_private_groups.py
                                              (discover → join → crawl)
```

Lý do tách `analyzer.py`: trước đây khối "từ điển + chấm điểm" bị **copy nguyên** vào cả
hai backend nên hai bản dễ lệch nhau. Nay chỉ còn **một** nơi định nghĩa; sửa từ khoá/ngưỡng
là cả hai backend đổi theo. `analyzer.py` **chỉ dùng thư viện chuẩn** (không cần Telethon),
nên chạy được ở mọi máy và test được offline.

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

### 2.2. Ngưỡng và cờ cấu hình (:107-121)

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

### 2.7. Mức rủi ro (:285-306)

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

### 2.9. Trình bày và ghi JSON (:432-580)

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

### 2.10. Dữ liệu mẫu (:592-670)

`DEMO_ITEMS` = 17 bài của 8 kênh (5 kênh "xấu" + 3 kênh "sạch") dùng cho `--demo`.
`demo_posts(limit, dialect)` sinh record; `dialect="api"` thêm `forwards`/`replies`
(bản web **không** có vì trang `t.me/s/` không hiển thị). Mọi bản ghi demo có
`_note = "DỮ LIỆU MẪU offline - không phải bài đăng thật"` và `source = "demo"`.

## 3. `demo_telegram.py` — backend web (chỉ stdlib)

| Hàm | Dòng | Việc |
|---|---|---|
| `fetch(url)` | :56 | tải HTML (`User-Agent` Chrome, timeout 25s), báo lỗi rõ cho HTTPError/URLError/timeout |
| `channel_name(raw)` | :78 | chuẩn hoá `durov`, `@durov`, `t.me/durov`, `https://t.me/s/durov`; **từ chối** link mời `+`/`joinchat` kèm gợi ý dùng bản API |
| `_text_of(fragment)` | :108 | bỏ thẻ HTML, `<br>` → xuống dòng, giải mã entity |
| `parse_count(text)` | :120 | `"10.4M"` → `10400000` (hỗ trợ K/M/B) |
| `parse_channel_info(page, fallback)` | :135 | tên, username, mô tả, số người đăng ký |
| `media_types_of(block)` | :159 | nhận `photo`/`video`/`document`… trong một bài |
| `parse_messages(page, info)` | :170 | tách từng `<div class="tgme_widget_message" data-post="kênh/id">` thành record |
| `scrape_channel(name, limit, progress)` | :226 | phân trang `?before=<data-before cuối>`, chống trùng theo `kênh/id`, dừng khi hết trang hoặc đủ `limit` |
| `read_seed_file(path)` | :281 | đọc danh sách kênh, bỏ dòng trống và dòng `#` |
| `main()` | :292 | argparse + gọi 2 hàm trên + `write_json`/`print_report` |

CLI: `--demo`, `--channel` (lặp được), `--seed-file`, `--limit` (**số bài mỗi kênh**, mặc
định 20), `--out` (mặc định `posts_web.json`), `--no-report`, `--report <json>`.
Bản web **không** có `forwards`/`replies`; có `views`, `link_preview`, `has_media`,
`media_types`, `subscribers`. Kênh riêng tư/tắt preview → báo
`"Kênh '@x' không có bản xem trước công khai (hoặc không tồn tại)."` và liệt kê kênh thử được.

## 4. `demo_telegram_api.py` — backend Telethon

### 4.1. Phiên và credential (:75-215)

- `session_path(name, bot)` (:81): tên rỗng → `tg_session` / `tg_bot_session`;
  `"tk-phu"` → `tg_session_tk-phu` / `tg_bot_tk-phu`; có dấu `/` → dùng nguyên đường dẫn;
  tự cắt đuôi `.session`. **Luôn trả đường dẫn tuyệt đối cạnh script** (`SCRIPT_DIR`), nên
  chạy từ thư mục nào cũng tìm đúng phiên.
- `session_kind(path)` (:98): tên file bắt đầu bằng `tg_bot` → `BOT`, còn lại
  `TÀI KHOẢN NGƯỜI DÙNG`.
- `load_credentials` (:171) / `load_bot_token` (:182): thứ tự `--api-id/--api-hash` (và
  `--bot-token`) → biến môi trường `TG_API_ID`/`TG_API_HASH`/`TG_BOT_TOKEN` → `tg_api.json`.
- `save_credentials` (:198): ghi `tg_api.json` rồi `os.chmod(..., 0o600)` (bản cũ **không**
  có `chmod`, dù README nói có).
- `credentials_help()` (:217): hướng dẫn lấy api_id/api_hash khi thiếu.

### 4.2. OCR và media (:121-350)

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

### 4.3. Thu thập (:352-465)

`fetch_history()` đọc lịch sử kênh/nhóm, `search_global()` tìm toàn cục, `download_media()`
tải file. **Lỗi đã sửa:** `search_global()` cũ có dòng sắp xếp theo
`r["analysis"]["scam_score"]` nhưng lúc đó `analysis` **chưa tồn tại** → sắp xếp vô hiệu và
có thể `KeyError`; dòng đó đã bỏ, việc sắp xếp chuyển về `run_async()` **sau khi chấm điểm**.
`report()` nay là wrapper gọi `print_report(..., noun=NOUN_API, title="BÁO CÁO THU THẬP
TELEGRAM QUA API (Telethon)")`, chịu được JSON thiếu `analysis`/`groups`.

### 4.4. Chế độ bot và `--watch` (:469-736)

- `bot_mode(args)` (:469): dùng phiên bot khi có token (`--bot-token` / `TG_BOT_TOKEN` /
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
- `run_async()` (:640): chọn `run_watch` / bot / tài khoản người dùng; in rõ loại phiên
  (`BOT`/`TÀI KHOẢN NGƯỜI DÙNG`); `--login`/`--login-bot` chỉ xác nhận phiên rồi thoát;
  chống trùng theo `kênh/message_id`; **sắp xếp sau khi chấm điểm**;
- `main()` (:737): argparse với `--demo`, `--login`, `--api-id`, `--api-hash`, `--channel`,
  `--search`, `--media-dir`, `--ocr`, `--limit 30`, `--out posts_api.json`, `--no-report`,
  `--report`, `--login-bot`, `--setup-bot`, `--bot-token`, `--session`, `--watch`,
  `--watch-once`, `--interval 60`, `--cycles 0`. Không chọn nguồn nào → chạy `--demo`.

## 5. `find_private_groups.py` — nhóm kín

Ba bước tách rời (`--discover` / `--join` / `--crawl`), `--session` riêng, và `Found` (:134)
lưu kết quả bước trước cho bước sau. `RE_INVITE` (:75) nhận cả `t.me/+hash` và
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

## 6. `tests/test_regression.py`

23 test, **không cần mạng/Telethon**, chạy `python3 -m unittest discover -s tests -v`:

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
- `TestBackendsEndToEnd`: chạy thật `--demo --limit 17` của **cả hai** backend và so bản
  web với `posts_tg.json` (bỏ qua các khóa thay đổi theo thời gian:
  `collected_at`/`posted_at`/`backend`/`generated_at`).

## 7. Các file JSON

| File | `backend` | Có `groups`? | Ghi chú |
|---|---|---|---|
| `posts_tg.json` | `demo (offline)` | ✅ | 17 bài mẫu, tạo lại bằng code hiện tại |
| `posts_api.json` | `telethon` | ❌ | 100 bài thật qua API, có `forwards`/`replies` |
| `nhom1.json` | `telethon-user` | ✅ | 1000 bài thật `@aiquochientrang` |
| `real_tg.json` | — | ❌ | 5 bài `@durov`, có `subscribers` |
| `real_tg_vn.json` | — | ❌ | 16 bài `@baomoi`/`@tintucvtv` |
| `found.json`, `found_groups.json` | — | — | kết quả `--discover` (link mời, chưa join) |

Record bài đăng (schema chung): `channel`, `channel_title`, `message_id`, `url`, `text`,
`hashtags`, `posted_at`, `collected_at`, `views`, `has_media`, `media_types`, `media_count`,
`link_preview`, `forwarded_from`, `author`, `subscribers`, `source`, `analysis`
(`scam_score`, `level`, `scam_category`, `scam_category_vi`, `signals`, `entities`, `summary`).
Bản API thêm `forwards`, `replies`.

⚠️ `nhom1.json` là dữ liệu **lịch sử**: `analysis`/`groups` bên trong được tính bằng bộ từ
khoá **cũ** (khớp chuỗi con, còn `"thuế"` trần), nên **chấm lại sẽ ra số hơi khác**. File
`posts_tg.json` đã được tạo lại bằng code hiện tại nên có `backend` và không còn bài 1100 = 47.

## 8. Di sản không khôi phục được (ghi để minh bạch)

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

## 9. Kiểm chứng (chạy thật 2026-10-11)

| Kiểm chứng | Kết quả |
|---|---|
| `analyze()` mới vs bản cũ trên 17 bài demo | **0 bài khác** |
| `group_summary()` vs `posts_tg.json` + `nhom1.json` | khớp **100%** mọi khóa (9 nhóm) |
| Dữ liệu thật 1000 bài (`nhom1.json`) chấm lại | 8 bài bị gắn cờ → **1** (0 High-risk) |
| 17 bài demo | 7 Normal / 8 Suspicious / 2 High-risk (58.8%) |
| `@durov` 25 bài (web) | group **11/100 Normal**, 10.4M subs, views 325K–6.87M |
| `@baomoi` + `@tintucvtv` (web, 20 bài/kênh) | 40 bài → **11/100** và **2/100**, đều Normal |
| Test hồi quy | 23/23 PASS (system python3 và `.venv-api/bin/python`) |
| Ghi đè `api.py` trùng lặp | đã xoá `demo_telegram_api_FIXED.py` |

## 10. Muốn thêm từ khoá / đổi trọng số thì sửa ở đâu

1. Mở `analyzer.py`, thêm từ khoá vào `SCAM_GROUPS[...]` hoặc `ACTION_SIGNALS[...]` (:37/:72).
2. Chạy `python3 -m unittest discover -s tests -v` — nếu test dữ liệu thật/demo đổi số, cân
   nhắc lại (đặc biệt là các mẫu báo động giả ở `TestScoring`).
3. Cập nhật lại các số liệu trong `README.md` nếu kết quả `--demo` đổi.
4. Thêm một ca mới vào `tests/test_regression.py` để khoá hành vi vừa sửa.

Quy tắc rút ra từ chính project này: **đừng dùng từ khoá ngắn/trần** (`ib`, `stk`, `bot`,
`thuế`) — chúng khớp vào giữa từ tiếng Anh, chuỗi base64 hoặc từ ghép tiếng Việt. Hãy dùng
cụm từ đủ đặc trưng, và luôn kiểm chứng trên dữ liệu thật trước khi commit.
