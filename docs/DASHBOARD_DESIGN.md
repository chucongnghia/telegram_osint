software
Deal: centred hero; blue-black-cornflower / Geist; pinned-chapters. Adaptation: this deliverable is the working application, not a marketing landing page. Use the client's Telegram-blue reference through Taste's brand generator (#f6f8fb / #ffffff / #192638 / #147bb7, muted corrected to #5e6d84), Geist + Geist Mono, soft radius. The actual workspace replaces the promotional centred hero; persistent sidebar, operational tables, forms and detail dialog are necessary application controls. No shaders, decorative scenes or scroll-pinned chapters: they obstruct the requested restrained dashboard.

# Dashboard design brief

- Product name: **Telegram OSINT System** (đổi tên từ "TínTrạm" ngày 2026-11-19; giữ nguyên logo và bảng màu).
  A project-specific working name, not a claim of affiliation with Telegram. The original mark combines an origami plane and an inspection fold, not a redraw of the Telegram logo.
- Audience: the operator of this local Telegram collection and content-classification project.
- Main action: Thu thập dữ liệu.
- Existing material: real SQLite messages, entities, heuristic analyses, Jev judgments, runs, profiles, local videos and extracted frames. Never seed production to decorate the interface.
- Fact: data counts and charts come from the selected SQLite database. Blank/error/partial states remain explicit. Jev findings indicate content signals, not a legal determination or calibrated fraud probability.
- Enemy: switching between CLI commands and raw SQLite tables without knowing which table holds a result.
- Voice: technical-dry, Vietnamese; precise rather than accusatory; short labels; explain what gets sent to Jev. The product uses 'tin nhắn', 'nguồn', 'nhãn Jev', 'điểm từ khoá' and 'tác vụ' consistently.

# Workspace composition

One application shell, equal content edges, a 232px navigation rail, a 76px top bar, and one active route at a time. At tablet the rail narrows; on mobile it becomes an explicit full-height navigation sheet. The data table's own overflow is bounded, never page-level sideways overflow. Content is readable at rest and with reduced motion.

- overview: four live summary cards; recorded-volume chart and source list; latest messages; evidence coverage and actual task activity. No invented trends or claims of safety.
- messages: paginated text search, Jev and heuristic filters; a detail dialog separates original content, keyword score and Jev history.
- sources: collected sources and their real message counts; direct filtering into messages.
- collection: web/API forms and a separate finite watch configuration; opt-in Jev budget, session selection and task confirmation.
- classify: explicit batch classification, key state and limits; no inferred success on submission.
- video: existing files and real playback, exact-link download, analysis and separate Jev classification; visual/audio gaps remain visible.
- profiles: saved permission-visible profiles, lookup and keyword search forms.
- jobs: queue state, bounded logs, cancellation and real completion/error statuses.
- settings: database information, ephemeral Jev key state and backup action. Never expose session auth keys or persist submitted API keys.

# Interaction and finish checks

Use the unchanged Taste motion layer for button hover/press, focus and the single workspace entrance. No card animation on scroll, no fake live data, no indefinite decorative loops. Functional polling only reflects task state. Keyboard names, visible labels, native dialog focus, error messages and contrast are checked at 1440, 1024, 820, 768 and 390; overflow also checked at 320.

Taste's marketing-only checks (centred sales hero, one promotional section per viewport, footer close and pinned storytelling moment) do not describe an operational dashboard. Record any resulting gate findings honestly; never hide controls from the checker or modify the kit to clear a check.

# Runtime

A separate project dashboard listens only on http://127.0.0.1:8765. It is not a replacement for the DeepSeek Harness GUI at port 3080. Python stdlib backend and local static assets keep the existing CLI dependency-light. Model/network jobs require an explicit action; browsing data does not classify or download anything.
