# Telegram OSINT System — dashboard cục bộ

Giao diện của project Telegram, không phải sản phẩm chính thức của Telegram. Mở dữ liệu thật trong SQLite; không tạo tin nhắn hay số liệu mẫu để trang trí.

## Mở dashboard

Từ thư mục gốc project:

```bash
python3 main.py dashboard --open
# Hoặc tự mở http://127.0.0.1:8765
```

Không cần cài Node/React/pip để dùng dashboard. Chọn DB khác hoặc cổng khác:

```bash
python3 main.py dashboard --db data/jev-demo.sqlite3 --port 8766
```

Server mặc định chỉ lắng nghe `127.0.0.1`. Đây là ứng dụng riêng ở cổng 8765, không thay thế giao diện DeepSeek Harness ở cổng 3080. Dừng server bằng Ctrl+C; các tác vụ con do server tạo cũng được dừng. Đóng tab trình duyệt **không** dừng server.

### Chia sẻ trong mạng WiFi/LAN

```bash
# Khóa truy cập tự sinh, in ra link kèm ?key=...
python3 main.py dashboard --share

# Hoặc tự đặt khóa (≥8 ký tự)
python3 main.py dashboard --share --share-key "mat-khau-rieng"
```

Server nghe `0.0.0.0` và in link dạng `http://<ip-lan>:8765/?key=<khóa>`. Mở từ **chính máy này** (`http://127.0.0.1:8765`) vẫn vào thẳng như bản cục bộ cũ, không cần khóa; máy khác trong LAN mới cần khóa. Mở link đó trên điện thoại/máy khác **cùng mạng WiFi**; dashboard đổi link thành cookie phiên (HttpOnly, `SameSite=Lax`) nên các lần sau không cần `?key=`.

Cơ chế bảo vệ: không có khóa thì mọi trang/API trả **401** kèm trang đăng nhập; sai khóa bị giới hạn **10 lần/10 phút**; POST vẫn cần token CSRF; Host header phải là IP hợp lệ (chặn DNS-rebinding bằng tên miền). Tài nguyên tĩnh (`/style.css`, `/assets/…`) mở để trang đăng nhập hiển thị được.

Lưu ý: người có khóa dùng được **phiên Telegram của bạn** (thu thập, tra cứu, tải file) và có thể phát sinh chi phí Jev/thị giác; dữ liệu trong SQLite cũng hiển thị được. Chỉ chia sẻ với người tin cậy, dùng `--share-key` riêng và dừng server khi xong.

#### WSL2 (máy này)

WSL2 dùng IP NAT riêng, thiết bị trong LAN không vào thẳng được. Trên **PowerShell (Administrator)** của Windows:

```powershell
$wsl = (wsl hostname -I).Trim().Split(" ")[0]
netsh interface portproxy add v4tov4 listenport=8765 listenaddress=0.0.0.0 connectport=8765 connectaddress=$wsl
New-NetFirewallRule -DisplayName "Telegram OSINT 8765" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow
```

Sau đó mở `http://<IP-LAN-của-Windows>:8765/?key=<khóa>`. Gỡ khi không dùng nữa:

```powershell
netsh interface portproxy delete v4tov4 listenport=8765 listenaddress=0.0.0.0
Remove-NetFirewallRule -DisplayName "Telegram OSINT 8765"
```

Cách khác: bật `networkingMode=mirrored` trong `%UserProfile%\.wslconfig` rồi `wsl --shutdown`; khi đó IP LAN của Windows dùng trực tiếp, không cần portproxy.

## Các màn hình

| Mục | Dữ liệu và thao tác |
|---|---|
| Tổng quan | Số tin/nguồn, nhãn Jev hiện hành, biểu đồ theo dữ liệu đã lưu, tin gần đây và lịch sử run. |
| Tin nhắn | Tìm kiếm, lọc, phân trang; mở chi tiết nội dung, điểm heuristic và lịch sử Jev. Nhãn cũ sau khi text thay đổi được đánh dấu, không tính như kết quả hiện hành. |
| Nguồn thu thập | Nguồn và số tin thực tế; mở danh sách tin của nguồn. |
| Thu thập dữ liệu | Tìm nguồn theo từ khoá qua Telegram API, chọn nhiều nguồn, đặt số tin tối đa chung hoặc riêng từng nguồn; mỗi nguồn chạy một tác vụ có Jev bật sẵn. Watch chạy được vô hạn hoặc giới hạn theo phút. |
| Phân loại Jev | Chọn giới hạn và chủ động gửi batch văn bản. Nhãn thuộc bộ bảy nhãn kêu gọi/kích động hành vi phạm pháp; kết quả kèm mức và yêu cầu rà soát. Không tự gọi mô hình khi mở trang. |
| Phân tích video | Xem/phát video trong `media/`, tải theo link, Whisper + OCR frame (mặc định bật), chọn ngôn ngữ auto; gọi Jev riêng. |
| Phân tích ảnh | Chọn PNG/JPEG/WebP trong `media/`, OCR Việt/Anh cục bộ, xem original/evidence, Jev riêng với đồng ý partial/key. |
| Tra cứu hồ sơ | Xem hồ sơ đã lưu, lookup hoặc tìm username bằng các CLI hiện có. |
| Nhật ký tác vụ | Trạng thái thật, log có giới hạn, xem lỗi và dừng tác vụ. Một worker chạy tuần tự. |
| Cài đặt | api_id/api_hash cho phiên Telegram, key Jev/thị giác lưu file cục bộ 0600 và sao lưu SQLite. |

DB picker liệt kê DB ứng dụng trong `data/`, bỏ qua bản backup và file không đúng định dạng. DB truyền bằng `--db` được cho phép riêng. Mở/chuyển trang chỉ đọc DB, không tự thu thập hay phân loại. Muốn xem ví dụ Jev văn bản, chọn `jev-demo.sqlite3` rồi mở **Tin nhắn**; bảng heuristic `analyses` không chứa nhãn Jev.

## Telegram, Jev và video

- **Telegram API:** dùng phiên đã đăng nhập bằng CLI; chọn `tg_session` hoặc `tg_session_*`. Dashboard không hỏi OTP, không tạo phiên trắng, không tự tham gia nhóm. Phiên hết hạn cần đăng nhập lại trong terminal.
- **Jev:** nhập TypeSafe key tại Cài đặt, hoặc export `TYPESAFE_API_KEY` trong đúng terminal khởi động server. Key nhập qua form được lưu vào `json_folder/dashboard_secrets.json` (0600, plaintext, git-ignored), không vào DB/localStorage, không trả lại cho trình duyệt. Xoá key trong form chỉ xoá bản nhập này, không xoá biến môi trường của server. Có key không đồng nghĩa key đã được nhà cung cấp xác thực.
- **Thị giác:** cùng panel Cài đặt có provider (`gemini` hoặc `openai`), model, key và URL. Key Gemini lưu trong file cục bộ 0600; chọn model cục bộ thì dùng URL loopback và không cần key. Endpoint từ xa cần tick đồng ý gửi ảnh ở **từng lần chạy**; thiếu consent trả `consent_required`. Lưu cấu hình không xác thực key. Chi tiết: [hướng dẫn OCR](<IMAGE_OCR.md>).
- **Chi phí:** form xác nhận trước tác vụ mạng/mô hình. Jev mặc định tắt; giới hạn UI tối đa 100 lần thử request, không phải ngân sách tiền. Không tự retry lỗi key/rate limit. Dữ liệu text có thể được gửi ra nhà cung cấp khi bạn bật Jev.
- **Phiên Telegram (api_id/api_hash):** nhập ở Cài đặt; ghi vào `json_folder/tg_api.json` (0600, plaintext — cùng file CLI đọc) và giữ nguyên `bot_token` nếu đã có. `api_id` là số Telegram cấp cho ứng dụng, `api_hash` 32 ký tự hex. Lưu cấu hình **không** tạo phiên và **không** đăng nhập: bước nhập SĐT/OTP/mật khẩu vẫn phải chạy CLI, ví dụ `.venv-api/bin/python main.py api --login`. Dashboard chỉ dùng các phiên đã có trong `sessions/`.
- **Theo dõi nguồn:** tick **Chạy vô hạn** để nghe mãi (chỉ dừng khi huỷ tác vụ hoặc đủ số tin mới); bỏ tick thì **Cửa sổ theo dõi (phút)** giới hạn 1..1440 phút. Ô **Dừng khi đủ số tin mới** nhận 0..1000, trong đó 0 = không giới hạn số tin. Dashboard luôn thêm `--watch-once` khi có cửa sổ và bỏ nó khi chạy vô hạn; ngân sách Jev (1..1000) dùng chung cho cả lần chạy.
- **Thu thập thêm từ một nguồn:** mỗi dòng ở màn hình **Nguồn dữ liệu** có nút **Thu thập thêm**: hỏi số tin cần lấy (1–1000, mặc định 20), rồi gửi một tác vụ `collect` cho đúng nguồn đó qua Telegram API với Jev bật (ngân sách bằng đúng số tin đã chọn, tối đa 1000) sau hộp xác nhận chi phí. Nguồn không có username dùng ID số (kênh/nhóm ở dạng `-100…`); nguồn không xác định được handle thì nút bị thay bằng dấu —.
- **Thu thập theo nguồn:** (1) nhập từ khoá, chạy **Tìm nguồn** — tác vụ `search` ghi kết quả vào `search_results`; danh sách chỉ hiện **nguồn mới**: lấy lượt tìm gần nhất và loại nguồn đã có tin nhắn trong DB (đã thu thập), nên không trộn kết quả cũ; (2) danh sách nguồn hiện ra với loại (Người dùng/Nhóm/Kênh) và từ khoá khớp, tick rồi **Thêm nguồn đã chọn** — hoặc dán trực tiếp `@kênh`/`@group`/ID `-100…` vào ô **Thêm nguồn thủ công**; (3) đặt **Số tin tối đa mỗi nguồn** chung, bấm **Áp dụng cho tất cả**, hoặc sửa từng dòng; (4) **Thu thập đã chọn** gửi mỗi nguồn một tác vụ `collect` với Jev bật sẵn. Sau khi có kết quả tìm nguồn, tick các dòng loại **Kênh** rồi bấm **Tìm group của kênh đã chọn**: tác vụ `linked_groups` dò group thảo luận gắn với từng kênh và ghi kết quả thành một lượt nguồn mới; danh sách chuyển sang các group tìm được (kể cả group đã từng thu thập) để bạn tick và thêm vào nguồn.

Tùy chọn **Thu cả group thảo luận của kênh** (bật mặc định) khiến mỗi kênh được lấy thêm tin từ group thảo luận gắn với nó — nhãn nguồn của các tin đó là group, kèm trường `linked_group_of` cho biết group thuộc kênh nào. Ngân sách Jev của mỗi nguồn là `min(1000, số tin)` request — 1000 là mức trần CLI, không phải chi phí tiền tệ; mỗi tác vụ vẫn xác nhận trước khi chạy và có thể phát sinh chi phí.

- **Video:** cần FFmpeg và runtime Whisper riêng đã mô tả trong [VIDEO](<VIDEO.md>). Dashboard không tự tải model. Bản giao diện này hỗ trợ bỏ qua vision hoặc cấu hình vision cục bộ qua môi trường; không tự bật quyền gửi frame tới dịch vụ từ xa. Thiếu âm thanh/thị giác phải hiện rõ; `partial` không phải kết quả kiểm tra đầy đủ.
- Điểm từ khoá, nhãn Jev và trạng thái xử lý là ba khái niệm riêng. Tin chưa phân loại không đồng nghĩa an toàn; nhãn không phải kết luận pháp lý hay xác suất đã hiệu chuẩn về hành vi phạm pháp.

## Phạm vi và giới hạn

- Dashboard làm việc trên dữ liệu công khai đã thu thập hoặc tác vụ do bạn chủ động chạy; không tự báo cáo, không gửi cảnh báo và không kết luận thay cơ quan có thẩm quyền.
- Mức “Rủi ro cao”/“Cần xem xét” chỉ là tín hiệu hỗ trợ rà soát; mọi kết quả có `requires_review` và cần người có thẩm quyền duyệt trước khi dùng.
- Phê bình hợp pháp (`criticism`) khác với kêu gọi hành động; nội dung tường thuật/cảnh báo bị hạ điểm và gắn `context="reporting"`, nên đọc `evidence`/`context` thay vì chỉ nhìn điểm.
- Lịch sử phân loại và nhật ký tác vụ được lưu để audit; chạy lại tạo bản ghi mới thay vì ghi đè kết quả cũ.

## Giới hạn bản đầu

- Không hỗ trợ đăng nhập Telegram/bot, tìm kiếm username bằng backend web, discover/join nhóm riêng hoặc cấu hình nâng cao ngay trên form. OCR ảnh/frame video cục bộ đã có; đọc [hướng dẫn OCR](<IMAGE_OCR.md>). Các chức năng CLI vẫn giữ nguyên; xem [USAGE](<USAGE.md>).
- Không upload video từ trình duyệt; chọn file đang có trong `media/` hoặc tải đúng link tin nhắn có video. Không nhận đường dẫn file tuỳ ý ngoài vùng cho phép.
- Chỉ dành cho một người dùng cục bộ, không có tài khoản đăng nhập/phân quyền nhiều người. Không dùng reverse proxy mở ứng dụng ra Internet.
- **Danh sách tác vụ** hiển thị dạng bảng: tên tác vụ, thời gian + trạng thái, tham số rút gọn, nút **Xem chi tiết** (mở đúng khối chi tiết cũ: tham số đầy đủ và nhật ký tiến trình) và nút **Điều hướng** mở thẳng trang kết quả liên quan.
- **Nhật ký tác vụ được lưu bền** trong `data/dashboard.sqlite3` (mode 0600, tách khỏi DB phân tích): mỗi tác vụ gồm loại, trạng thái, thời gian, mã thoát, tham số và toàn bộ dòng log (dòng bí mật được ẩn trước khi ghi). Giữ 500 tác vụ gần nhất; giao diện nạp 40 tác vụ mới nhất kèm 200 dòng log gần nhất mỗi tác vụ, nên mở lại server vẫn xem được lịch sử và chi tiết. Xoá file này chỉ mất lịch sử tác vụ, không ảnh hưởng dữ liệu Telegram.
- Hàng đợi và tiến trình đang chạy vẫn ở RAM; khởi động lại server sẽ mất phần này, nhưng key đã lưu trong file cục bộ thì còn. Nội dung/kết quả đã được CLI ghi vào SQLite vẫn còn. Dừng một tác vụ không hoàn tác dữ liệu đã lưu hoặc request đã gửi.
- Log, hàng đợi, kích thước batch và tham số đều có giới hạn. Các bảng SQLite/JSON chứa dữ liệu cá nhân dạng rõ; không chia sẻ `sessions/`, credential hoặc DB ra ngoài.

## Mã nguồn và kiểm thử

- [HTTP service](<../src/telegram_demo/dashboard_service.py>) dùng Python stdlib, đọc SQLite riêng và tạo argv CLI cố định, không nhận lệnh shell.
- [UI](<../src/telegram_demo/dashboard/>) dùng HTML/CSS/JavaScript, logo riêng và Geist/Geist Mono tự host. Không có CDN/analytics khi chạy; license font ở [OFL](<../src/telegram_demo/dashboard/assets/fonts/OFL.txt>).
- Test HTTP, CSRF/Host/Origin, query/filter, job và media trên fixture tạm: [backend tests](<../tests/test_dashboard_service.py>).
- Test form contract và escaping bằng Node VM, không cần browser/npm: [client tests](<../tests/test_dashboard_client.mjs>).
- [Launcher](<../tests/test_dashboard_launcher.py>) và [Telegram không tương tác](<../tests/test_dashboard_noninteractive.py>) có regression riêng.

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests
.venv-api/bin/python -W error::ResourceWarning -m unittest discover -s tests
node tests/test_dashboard_client.mjs
```

Suite không gọi Telegram hay API mô hình thật, không ghi DB/session thật. Đánh giá giao diện và giới hạn bộ kiểm tra Taste được ghi riêng trong [DASHBOARD_QA](<DASHBOARD_QA.md>).
