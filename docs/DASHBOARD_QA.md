# Dashboard QA — Telegram OSINT System

> Báo cáo này ghi lại lần chạy gate ngày 2026-11-17 khi sản phẩm còn tên "TínTrạm";
> trích dẫn trong báo cáo giữ nguyên văn bản lúc đo, tên hiện tại đã đổi thành Telegram OSINT System.

**GATE: OPEN 43 (rounds used up)**

## Phạm vi và bằng chứng

- Bản dashboard chạy thật tại http://127.0.0.1:8765; không thay thế GUI DSH.
- Python hệ thống: 369 test, 1 test Telethon được bỏ qua; venv API: 369 test đều đạt. Chạy với ResourceWarning thành lỗi. Node VM kiểm tra cả 9 route, form allowlist/bounds, XSS escaping, API lỗi, selected DB/CSRF và payload watch hữu hạn.
- Không chạy Telegram hoặc API mô hình có phí thật. Các HTTP/job tests dùng fixture và DB tạm. Smoke worker thực tế chạy CLI web demo: trạng thái `succeeded`, exit 0, 1 run/3 tin/0 nhãn AI, integrity `ok`, không lỗi khóa ngoại. Lượt đầu kiểm chứng nhầm tên trạng thái `completed` thay vì `succeeded` nên assertion kiểm thử lỗi; không phải worker thất bại. Đã sửa phép kiểm và chạy lại đạt. Duyệt UI chỉ đọc dữ liệu có sẵn.
- Screenshot 1440, 1024, 820, 768, 390 và 320px nằm trong `media/dashboard/`; có thêm màn thu thập và video. Đã sửa màu chữ phụ, nhãn tìm kiếm/DB nhìn thấy, snippet không cắt dòng bằng CSS, bản ghi tin nhắn xếp dọc trên điện thoại và menu đóng không nhận focus.
- Copy-lint của HTML shell: 0 FAIL / 0 WARN. Gate cũng kiểm copy render thực tế; các WARN giữ lại ở dưới.
- Perf (3 lần mỗi kích thước, Chromium CPU): desktop/mobile 0 FAIL, tổng 4 WARN; transfer khoảng 294 KiB, 2 font families, không long task, không request lỗi. FCP trung vị 96/88ms, LCP 116/116ms; CLS 0.041/0.000. Đây là phép đo cục bộ trước các chỉnh nhỏ cuối về label/wrapping, không phải tốc độ mạng thực tế.
- Perf WARN giữ lại: 6 font subset files cho Latin/tiếng Việt của Geist + Geist Mono (khoảng 100 KiB); logo sidebar không lazy-load. Không tải font ngoài khi chạy và không lazy-load logo điều hướng có sẵn trên màn hình.
- Full consistency đã chạy đủ 1440/1024/820/768/390 trước lượt polish cuối. Gate quick kiểm lại 1440/390 và overflow 320 sau thay đổi. Bộ layout standalone lỗi nội bộ `Cannot read properties of undefined (reading 'map')` / `r.sections is not iterable`; không coi đây là một lượt layout PASS, không sửa file kit.

## Lựa chọn thiết kế và giới hạn gate

Voice: technical-dry tiếng Việt, nhãn hành động ngắn, số liệu từ SQLite thật, không kết tội. Deal ban đầu: centred hero / blue-black-cornflower / Geist / pinned-chapters. Bản brief yêu cầu ứng dụng vận hành, nên dùng palette brand Telegram-blue qua generator, logo origami plane/check riêng, sidebar + bảng/form/drawer thật thay vì landing page bán hàng hoặc hiệu ứng scroll trang trí. Hai điểm tương tác chính là tìm/lọc → xem bằng chứng, và tạo tác vụ → theo dõi log/dừng. Không có shader, ảnh stock, KPI hoặc chứng thực giả.

Gate còn OPEN, **không tuyên bố Taste PASS**. Không dùng waiver và không thay kit. Các quy tắc hero/section toàn màn hình, footer một màn hình, KPI phải nhỏ hơn h2, mật độ ảnh và anchor phải là section tĩnh không phù hợp hoàn toàn với app hash-router. Các hash link là route được JS xử lý, không phải anchor vào một section marketing. Các nhận xét căn lề/list còn lại được giữ nguyên trong báo cáo, không gọi chúng là đã khắc phục.

## Tất cả FAIL còn mở

Các đoạn nội dung tin nhắn trong finding được lược bỏ; selector, mã kiểm tra, kích thước và hướng sửa vẫn được giữ.

### 1. `hero-fill` — 1440, 390px

- Vị trí: `div#view-root "Nhịp thu thập"`
- Finding: the next section shows on the first screen: it starts at 242px on a 900px screen (the hero ends at 214px)
- Hướng sửa do checker đề nghị: let the hero fill the first screen alone (min-height: 100svh, less above it): nothing of the next section on load

### 2. `flush` — 1440, 390px

- Vị trí: `div#view-root > div.section-stack > div.stats-grid > article.stat-card:nth-of-type(1) "Tổng tin nhắn 1.188 Đã lưu trong SQLite"`
- Finding: its first line sits 0px under the top edge of div#view-root, where the ground changes
- Hướng sửa do checker đề nghị: give the section its padding at the edge where the ground changes (the closing call keeps a clear gap above the footer)

### 3. `flush` — 1440px

- Vị trí: `div#view-root > div.section-stack > div.split-grid:nth-of-type(3) > section.panel:nth-of-type(1) "Mức bao phủ Jev Chỉ tính trên những tin "`
- Finding: its last line sits 0px above the lower edge of div#view-root, where the ground changes to the footer
- Hướng sửa do checker đề nghị: give the section its padding at the edge where the ground changes (the closing call keeps a clear gap above the footer)

### 4. `nav` — 1440px

- Vị trí: `div.app-shell > div.workspace-shell > header.topbar`
- Finding: the nav has no bar, and its pieces sit past the content's edges (1328-1408px against 264-1176px)
- Hướng sửa do checker đề nghị: make the nav one bar from the content's left edge to its right edge, with every link and action inside it

### 5. `no-visual` — 1440, 390px

- Vị trí: `div#view-root "Nhịp thu thập"`
- Finding: pictures, effects and mocks cover 7% of this section; a feature section needs a visual (about 25% or more)
- Hướng sửa do checker đề nghị: give the section a visual: a UI fragment on a ground, real icons, or a photograph that fits

### 6. `card-visual` — 1440, 390px

- Vị trí: `div#view-root > div.section-stack > div.stats-grid "Tổng tin nhắn 1.188 Đã lưu trong SQLite "`
- Finding: 4 cards hold only text: no picture, icon, UI fragment or effect in any of them
- Hướng sửa do checker đề nghị: give the cards a visual: an inset panel with a UI fragment, a photograph or real icons

### 7. `button-styles` — 1440px

- Vị trí: `(one screen)`
- Finding: 3 button styles on one screen: soft, outline #d5dde8 ("Xem nguồn"); soft, fill #eaf3fc ("Tổng quan"); soft, fill #147bb7, outline #147bb7 ("Thu thập dữ liệu"); the odd one out: "Thu thập dữ liệu"
- Hướng sửa do checker đề nghị: one primary and one secondary button: one shape, one height per size, one label size

### 8. `button-styles` — 1440px

- Vị trí: `(the page)`
- Finding: 3 button styles on the page: soft, fill #eaf3fc x1 ("Tổng quan"); soft, fill #147bb7, outline #147bb7 x1 ("Thu thập dữ liệu"); soft, outline #d5dde8 x3 ("Xem nguồn")
- Hướng sửa do checker đề nghị: one primary and one secondary button: one shape, one height per size, one label size

### 9. `copy-headline` — 1440px

- Vị trí: `h1#page-title "Tổng quan"`
- Finding: "Tổng quan" does not name what is sold (bang ieu khien phan tich telegram, bang, ieu, khien, from flags); only the line under it does
- Hướng sửa do checker đề nghị: put the category noun in the h1 (and who it is for, or where), in 2 to 8 words

### 10. `copy-headings` — 1440px

- Vị trí: `section.panel > div.panel-header > div > h2.panel-title "Tin nhắn gần đây"`
- Finding: "Tin nhắn gần đây" names no product, part, place or section (3 headings like it; at most one brand line per page) (and 2 more like it: "Mức bao phủ Jev", "Lần chạy gần đây")
- Hướng sửa do checker đề nghị: name the thing in every heading: the product, a part, the place or a section noun

### 11. `heading-scale` — 1440, 390px

- Vị trí: `div.section-stack > div.stats-grid > article.stat-card:nth-of-type(1) > div.stat-value "1.188"`
- Finding: the figure "1.188" is set at 32px, larger than the section headings (h2 15px); 4 more like it
- Hướng sửa do checker đề nghị: set figures at the h2 size or under: a stat reads as data, not as the heading

### 12. `off-grid` — 1440, 390px

- Vị trí: `div.page-heading.hero > div.heading-copy > div.breadcrumb > span:nth-of-type(2) "Không gian làm việc"`
- Finding: left edge at 278px, 14px off the page margin at 264px (7 elements, like h1#page-title)
- Hướng sửa do checker đề nghị: move it onto the page margin, or the column edge its section uses

### 13. `links` — 1440px

- Vị trí: `a#brand-home "TínTrạm Telegram workspace"`
- Finding: "TínTrạm Telegram workspace" goes to #overview, which is not on this page
- Hướng sửa do checker đề nghị: point every link at a real section, page or file

### 14. `links` — 1440px

- Vị trí: `aside#sidebar > nav.side-nav:nth-of-type(1) > a.nav-link.active "Tổng quan"`
- Finding: "Tổng quan" goes to #overview, which is not on this page
- Hướng sửa do checker đề nghị: point every link at a real section, page or file

### 15. `links` — 1440px

- Vị trí: `aside#sidebar > nav.side-nav:nth-of-type(1) > a.nav-link:nth-of-type(2) "Tin nhắn"`
- Finding: "Tin nhắn" goes to #messages, which is not on this page
- Hướng sửa do checker đề nghị: point every link at a real section, page or file

### 16. `links` — 1440px

- Vị trí: `aside#sidebar > nav.side-nav:nth-of-type(1) > a.nav-link:nth-of-type(3) "Nguồn thu thập"`
- Finding: "Nguồn thu thập" goes to #sources, which is not on this page
- Hướng sửa do checker đề nghị: point every link at a real section, page or file

### 17. `links` — 1440px

- Vị trí: `aside#sidebar > nav.side-nav:nth-of-type(1) > a.nav-link:nth-of-type(4) "Tra cứu hồ sơ"`
- Finding: "Tra cứu hồ sơ" goes to #profiles, which is not on this page
- Hướng sửa do checker đề nghị: point every link at a real section, page or file

### 18. `links` — 1440px

- Vị trí: `aside#sidebar > nav.side-nav:nth-of-type(2) > a.nav-link:nth-of-type(1) "Thu thập dữ liệu"`
- Finding: "Thu thập dữ liệu" goes to #collection, which is not on this page
- Hướng sửa do checker đề nghị: point every link at a real section, page or file

### 19. `links` — 1440px

- Vị trí: `aside#sidebar > nav.side-nav:nth-of-type(2) > a.nav-link:nth-of-type(2) "Phân loại Jev AI"`
- Finding: "Phân loại Jev AI" goes to #classify, which is not on this page
- Hướng sửa do checker đề nghị: point every link at a real section, page or file

### 20. `links` — 1440px

- Vị trí: `aside#sidebar > nav.side-nav:nth-of-type(2) > a.nav-link:nth-of-type(3) "Phân tích video"`
- Finding: "Phân tích video" goes to #video, which is not on this page
- Hướng sửa do checker đề nghị: point every link at a real section, page or file

### 21. `links` — 1440px

- Vị trí: `aside#sidebar > nav.side-nav:nth-of-type(2) > a.nav-link:nth-of-type(4) "Nhật ký tác vụ"`
- Finding: "Nhật ký tác vụ" goes to #jobs, which is not on this page
- Hướng sửa do checker đề nghị: point every link at a real section, page or file

### 22. `links` — 1440px

- Vị trí: `aside#sidebar > div.sidebar-bottom > a.nav-link "Cài đặt"`
- Finding: "Cài đặt" goes to #settings, which is not on this page
- Hướng sửa do checker đề nghị: point every link at a real section, page or file

### 23. `links` — 1440px

- Vị trí: `div.workspace-shell > header.topbar > div.topbar-actions > a.account-button "Mở cài đặt không gian làm việc"`
- Finding: "Mở cài đặt không gian làm việc" goes to #settings, which is not on this page
- Hướng sửa do checker đề nghị: point every link at a real section, page or file

### 24. `moment-none` — 1440px

- Vị trí: `(the page)`
- Finding: a software page (--type) deals no moment: every kind carries one, two on a long page (moments.md)
- Hướng sửa do checker đề nghị: deal one (moments.mjs pick --page software) and mount it in its own section

### 25. `hero-screen` — 1440px

- Vị trí: `main#workspace > div.page-heading.hero "Không gian làm việc/Kết nối cục bộTổng q"`
- Finding: the hero ends at 214px on a 900px screen, 686px above the fold: it ends at the fold, 1px either way (SR-1)
- Hướng sửa do checker đề nghị: make the hero exactly one screen (height: 100svh, its content fitted inside), never taller or shorter

### 26. `hero-screen` — 1440px

- Vị trí: `div#view-root "Tổng tin nhắn1.188Đã lưu trong SQLiteNgu"`
- Finding: 658px of the next section shows on the first screen (from 242px on a 900px screen): nothing below the hero shows at load (SR-1)
- Hướng sửa do checker đề nghị: end the hero at the fold so nothing of the next section shows at load (hero height: 100svh)

### 27. `section-height` — 1440px

- Vị trí: `main#workspace > footer.workspace-footer "TínTrạm · Telegram analysis workspaceĐán"`
- Finding: the section is 61px tall (0.1 screens), 839px short of one screen (900px): every section after the hero is one screen or two, 2px either way (SR-2)
- Hướng sửa do checker đề nghị: set the section to one screen or two (height: calc(var(--h,1) * 100vh), --h 1 or 2) and cut or split its content to fit

### 28. `section-height` — 1440px

- Vị trí: `div#view-root "Tổng tin nhắn1.188Đã lưu trong SQLiteNgu"`
- Finding: the section is 1788px tall (2 screens), 12px short of two screens (1800px): every section after the hero is one screen or two, 2px either way (SR-2)
- Hướng sửa do checker đề nghị: set the section to one screen or two (height: calc(var(--h,1) * 100vh), --h 1 or 2) and cut or split its content to fit

### 29. `one-section` — 1440px

- Vị trí: `h1#page-title "Tổng quan"`
- Finding: the screen at scroll 0px (stride 0) shows 98px of section 1 and the heading of section 1 whole and the content of 2 sections, over 24px each beside section 2: a screen shows one section (another may peek 8px, 24px WARN) (SR-4)
- Hướng sửa do checker đề nghị: make each section exactly one screen or two, so a screen shows one section and no neighbour peeks

### 30. `one-section` — 1440px

- Vị trí: `main#workspace > footer.workspace-footer "TínTrạm · Telegram analysis workspaceĐán"`
- Finding: the screen at scroll 1224px (the top of section 3) shows 61px of section 3 beside section 2: a screen shows one section (another may peek 8px, 24px WARN) (SR-4)
- Hướng sửa do checker đề nghị: make each section exactly one screen or two, so a screen shows one section and no neighbour peeks

### 31. `one-section` — 1440px

- Vị trí: `div#view-root "Tổng tin nhắn1.188Đã lưu trong SQLiteNgu"`
- Finding: a <div> sits directly in <main>: every part of <main> is a <section> (SR-4)
- Hướng sửa do checker đề nghị: put every block of <main> in a <section>, and give a band its own section instead of a ground inside one

### 32. `one-heading` — 1440px

- Vị trí: `section.panel:nth-of-type(2) > div.panel-header > div > h2.panel-title "Nguồn nổi bật"`
- Finding: the section holds 5 h2 headings: one per section, first in its .wrap (SR-5)
- Hướng sửa do checker đề nghị: give each section one h2, first in its .wrap, and the hero only its h1

### 33. `container-edges` — 1440px

- Vị trí: `div.app-shell > div.workspace-shell > header.topbar "Tìm tin nhắnEnter ↵ Cơ sở dữ liệujev-dem"`
- Finding: the nav's content runs 292px from the left and 32px from the right (read from its outermost blocks: no .wrap): the page's container starts at 264px; 292 and 32px are not one margin; the margin is 64-120px at 1440px (one container, equal margins, 2px either way) (SR-6)
- Hướng sửa do checker đề nghị: use one container on every section and the nav (.wrap): one margin left and right, 64-120px on a desktop, 16-24px on a phone

### 34. `container-edges` — 1440px

- Vị trí: `main#workspace > div.page-heading.hero "Không gian làm việc/Kết nối cục bộTổng q"`
- Finding: the content of section 1 runs 264px from the left and 32px from the right (read from its outermost blocks: no .wrap): 264 and 32px are not one margin; the margin is 64-120px at 1440px (one container, equal margins, 2px either way) (SR-6)
- Hướng sửa do checker đề nghị: use one container on every section and the nav (.wrap): one margin left and right, 64-120px on a desktop, 16-24px on a phone

### 35. `container-edges` — 1440px

- Vị trí: `div#view-root "Tổng tin nhắn1.188Đã lưu trong SQLiteNgu"`
- Finding: the content of section 2 runs 264px from the left and 32px from the right (read from its outermost blocks: no .wrap): 264 and 32px are not one margin; the margin is 64-120px at 1440px (one container, equal margins, 2px either way) (and 1 more like it: "TínTrạm · Telegram analy") (SR-6)
- Hướng sửa do checker đề nghị: use one container on every section and the nav (.wrap): one margin left and right, 64-120px on a desktop, 16-24px on a phone

### 36. `container-edges` — 1440px

- Vị trí: `a.skip-link "Bỏ qua điều hướng"`
- Finding: a <a> at the top of the page, outside header.nav, main, footer.closing and nav.actionbar (SR-6)
- Hướng sửa do checker đề nghị: build the page as header.nav, a main of sections (each one in-flow .wrap plus background layers) and footer.closing

### 37. `container-edges` — 1440px

- Vị trí: `div.app-shell "TínTrạmTelegram workspace TDự án của bạn"`
- Finding: a <div> at the top of the page, outside header.nav, main, footer.closing and nav.actionbar (SR-6)
- Hướng sửa do checker đề nghị: build the page as header.nav, a main of sections (each one in-flow .wrap plus background layers) and footer.closing

### 38. `container-edges` — 1440px

- Vị trí: `section.panel:nth-of-type(2) > div.panel-header > div > h2.panel-title "Nguồn nổi bật"`
- Finding: the h2 starts 754px right of the container's left: every h1 and h2 starts on it, 2px either way (SR-6)
- Hướng sửa do checker đề nghị: start the text on the container's left, or on the one split line every split shares

### 39. `container-edges` — 1440px

- Vị trí: `section.panel > div.panel-header > div > h2.panel-title "Tin nhắn gần đây"`
- Finding: 2 text blocks start on a left line at x 289, 25px from the container's left: text starts on the container's left, or on one split line (x 751, 848, 945) (also "Nhãn Jev và điểm heuristic được ") (and 3 more like it: "Nhịp thu thập", "Tin nhắn gần đây", x 278) (SR-6)
- Hướng sửa do checker đề nghị: start the text on the container's left, or on the one split line every split shares

### 40. `flush` — 390px

- Vị trí: `button#page-action "Thu thập dữ liệu"`
- Finding: its button sits 0px above the lower edge of div.page-heading.hero, where the ground changes to div#view-root
- Hướng sửa do checker đề nghị: give the section its padding at the edge where the ground changes (the closing call keeps a clear gap above the footer)

### 41. `flush` — 390px

- Vị trí: `div#view-root > div.section-stack > div.split-grid:nth-of-type(3) > section.panel:nth-of-type(2) "Lần chạy gần đây Nhật ký thu thập được l"`
- Finding: its last line sits 0px above the lower edge of div#view-root, where the ground changes to the footer
- Hướng sửa do checker đề nghị: give the section its padding at the edge where the ground changes (the closing call keeps a clear gap above the footer)

### 42. `stagger` — 390px

- Vị trí: `div.table-wrap > table.data-table.message-table > tbody > tr:nth-of-type(4)`
- Finding: 4 rows start at 3 different left offsets (+0, +8, +0, +12px)
- Hướng sửa do checker đề nghị: start every row of the list on one left edge

### 43. `container` — 1440, 390px

- Vị trí: `page`
- Finding: no .wrap
- Hướng sửa do checker đề nghị: put the content in one .wrap

## WARN giữ lại

- `fold-card` — `div#view-root > div.section-stack > div.split-grid:nth-of-type(2) > section.panel:nth-of-type(1) "Nhịp thu thập Số tin nhắn theo ngày tron"`: the 705x554 card starts on the first screen and runs 56px past its bottom
- `fold-card` — `div#view-root > div.section-stack > div.split-grid:nth-of-type(2) > section.panel:nth-of-type(2) "Nguồn nổi bật Xếp theo số tin nhắn đã lư"`: the 415x554 card starts on the first screen and runs 56px past its bottom
- `cut-text` — `div.panel-body > div.activity-list > div.activity-row:nth-of-type(2) > span.badge.neutral "usernames-by-keyword"`: shortened with an ellipsis
- `copy-buttons` — `aside#sidebar > nav.side-nav:nth-of-type(1) > a.nav-link.active "Tổng quan"`: "[đoạn dữ liệu/UI]" does not start with a verb (book, buy, try, start, get, see...) (and 4 more like it: "[đoạn dữ liệu/UI]", "[đoạn dữ liệu/UI]", "[đoạn dữ liệu/UI]", "[đoạn dữ liệu/UI]")
- `copy-actions` — `(the page)`: the main action "[đoạn dữ liệu/UI]" does not read word for word the same in the closing section
- `body-size` — `tbody > tr:nth-of-type(1) > td:nth-of-type(1) > div.source-name`: 5 of 5 paragraphs of running text are set under 16px (13px)
- `section-gaps` — `main#workspace > div.page-heading.hero / div#view-root`: 31px between these sections, -26% against the median 42px
- `section-gaps` — `div#view-root / main#workspace > footer.workspace-footer`: 53px between these sections, +26% against the median 42px
- `label-centre` — `aside#sidebar > nav.side-nav:nth-of-type(1) > a.nav-link.active "Tổng quan"`: its label sits 74px off-centre (12px of room at the left, 86px at the right)
- `stranded-action` — `button#page-action "Thu thập dữ liệu"`: stands 792px across from the nearest text of its block ("[đoạn dữ liệu/UI]") (and 1 more like it: "[đoạn dữ liệu/UI]")
- `copy-length` — `div#view-root "Nhịp thu thập"`: 1 section over 90 words a screen outside the UI mocks ("[đoạn dữ liệu/UI]" 235); 163 words a screen across the page (490 words in 3 screens): a tech page says it in about 80 or fewer
- `progress-bar` — `div.split-grid:nth-of-type(3) > section.panel:nth-of-type(1) > div.panel-body > div.progress-track "10 / 1.188 tin 0,8% đã phân loại Tin chư"`: a progress or meter bar on the page, like the one in "[đoạn dữ liệu/UI]": the house rules have no drag or progress bars
- `button-contrast` — `aside#sidebar > nav.side-nav:nth-of-type(1) > a.nav-link.active "Tổng quan"`: the button "[đoạn dữ liệu/UI]" (#eaf3fc) stands only 1.12:1 off the ground around it: it reads as part of the ground, or as switched off
- `skip-link` — `a.skip-link "Bỏ qua điều hướng"`: the first Tab stop is "[đoạn dữ liệu/UI]", not a skip link: a keyboard user tabs through the whole nav on every visit
- `form-fields` — `form#global-search`: the form has no status line (role="[đoạn dữ liệu/UI]"): sending, sent and errors are never read out
- `stagger` — `div.table-wrap > table.data-table.message-table > tbody > tr:nth-of-type(1)`: 4 rows start at 2 different left offsets (+0, +0, +0, +12px)
- `stagger` — `div.table-wrap > table.data-table.message-table > tbody > tr:nth-of-type(2)`: 4 rows start at 2 different left offsets (+0, +0, +0, +12px)
- `stagger` — `div.table-wrap > table.data-table.message-table > tbody > tr:nth-of-type(3)`: 4 rows start at 2 different left offsets (+0, +0, +0, +12px)
- `stagger` — `div.table-wrap > table.data-table.message-table > tbody > tr:nth-of-type(5)`: 4 rows start at 2 different left offsets (+0, +0, +0, +12px)
- `visual-empty` — `section.panel:nth-of-type(1) > div.panel-body > div.chart-panel > svg.chart-svg "drawing (svg)"`: the drawing (svg) is nearly empty at 390px: 3 colours in its 316x210px, 96.8% one colour (a thin line drawing or a logo can be this plain: look at it)
- `fold-cut` — `section.panel:nth-of-type(1) > div.panel-header > div > h2.panel-title "Nhịp thu thập"`: a 14px line is sliced by the bottom of the first screen (13px of it below the fold)
- `copy-stat-strip` — `copy x2: "1.188 / 22 / 10 / 0", "1.150 / 8 / 8 / 5 / 3"`: stat strip x2

Không có waiver. Nguồn máy đầy đủ: `.taste-skill/gate.json` và các report consistency/perf cục bộ (git-ignore).
