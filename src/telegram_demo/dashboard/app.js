/* Local dashboard client. No data, credentials, or synthetic metrics are persisted here. */
const $ = (selector, scope = document) => scope.querySelector(selector);
const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const number = (value) => Number.isFinite(Number(value)) ? new Intl.NumberFormat('vi-VN').format(Number(value)) : '—';
const list = (value) => Array.isArray(value) ? value : [];
const pretty = (value) => typeof value === 'string' ? value : JSON.stringify(value ?? null, null, 2);
const date = (value) => {
  if (!value) return 'Chưa ghi nhận';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString('vi-VN');
};
const LABELS = Object.freeze({
  scam_indicators: 'Có dấu hiệu lừa đảo',
  advertising_unverified: 'Quảng cáo chưa xác minh',
  reporting_warning: 'Tin tức / cảnh báo',
  other: 'Nội dung khác',
  insufficient_information: 'Thiếu thông tin',
});
const VIEWS = Object.freeze({
  overview: ['Tổng quan', 'Dữ liệu Telegram, trong một góc nhìn.'],
  messages: ['Tin nhắn', 'Tìm nội dung, đối chiếu tín hiệu và xem kết quả phân loại.'],
  sources: ['Nguồn dữ liệu', 'Các kênh và nhóm đã có trong cơ sở dữ liệu.'],
  collection: ['Thu thập dữ liệu', 'Chọn nguồn công khai và giới hạn trước khi bắt đầu.'],
  classify: ['Phân loại với Jev', 'Đánh giá nội dung đã lưu với ngân sách yêu cầu rõ ràng.'],
  video: ['Phân tích video', 'Đối chiếu lời nói, khung hình và các khoảng trống bằng chứng.'],
  profiles: ['Hồ sơ công khai', 'Tra cứu hồ sơ đã lưu hoặc gửi một lần tra cứu mới.'],
  jobs: ['Tác vụ', 'Trạng thái thực thi và nhật ký từ tiến trình cục bộ.'],
  settings: ['Cài đặt', 'Kết nối mô hình và quản lý dữ liệu cục bộ.'],
});
const JOB_NAMES = {
  collect: 'Thu thập tin nhắn', watch: 'Theo dõi nguồn', classify: 'Phân loại tin nhắn',
  lookup: 'Tra cứu hồ sơ', search: 'Tìm hồ sơ', download: 'Tải video',
  video_analyze: 'Phân tích video', video_classify: 'Phân loại video', db_backup: 'Sao lưu dữ liệu',
};
const STATUS_NAMES = {
  queued: 'Đang chờ', pending: 'Đang chờ', running: 'Đang chạy', success: 'Thành công',
  succeeded: 'Hoàn tất', completed: 'Hoàn tất', done: 'Hoàn tất', error: 'Lỗi', failed: 'Thất bại',
  cancelled: 'Đã hủy', canceled: 'Đã hủy', cancelling: 'Đang hủy', skipped: 'Đã bỏ qua', partial: 'Thiếu một phần',
};
const state = {
  bootstrap: null, db: '', view: 'overview', params: new URLSearchParams(),
  controller: null, revision: 0, jobs: [], pollTimer: null, polling: false,
  expandedJobs: new Map(), selectedVideo: '', videos: null, drawerTrigger: null, drawerRevision: 0,
};
let toastTimer;

export function icon(name, size = 20) {
  const paths = {
    arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
    message: '<path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2v-10A8.5 8.5 0 0 1 10.5 3h2A8.5 8.5 0 0 1 21 11.5Z"/><path d="M7 9h9M7 13h6"/>',
    sources: '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><path d="M14 17.5h7m-3.5-3.5v7"/>',
    scan: '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M8 12l3 3 5-6"/>',
    alert: '<path d="m12 3 10 18H2L12 3Z"/><path d="M12 9v5m0 3h.01"/>',
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    inbox: '<path d="m4 4-2 12v4h20v-4L20 4H4Z"/><path d="M2 15h6l2 3h4l2-3h6"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    play: '<path d="m9 5 11 7-11 7V5Z"/>',
    key: '<circle cx="8" cy="8" r="5"/><path d="m12 12 9 9m-5-5 3-3m-6 0 3-3"/>',
    close: '<path d="m6 6 12 12M6 18 18 6"/>',
    external: '<path d="M14 3h7v7m0-7L10 14m0-11H3v18h18v-7"/>',
    refresh: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a7 7 0 0 1 12-2l2 3M4 16l2 3a7 7 0 0 0 12-2"/>',
  };
  return `<svg width="${Number(size) || 20}" height="${Number(size) || 20}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.inbox}</svg>`;
}
function badge(text, tone = 'neutral') {
  const safeTone = ['neutral', 'danger', 'warning', 'success', 'blue'].includes(tone) ? tone : 'neutral';
  return `<span class="badge ${safeTone}">${esc(text)}</span>`;
}
function statusBadge(status) {
  const tone = ['error', 'failed'].includes(status) ? 'danger' : ['success', 'succeeded', 'completed', 'done'].includes(status) ? 'success' : ['queued', 'pending', 'running', 'partial', 'cancelling'].includes(status) ? 'warning' : 'neutral';
  return badge(STATUS_NAMES[status] || status || 'Chưa có trạng thái', tone);
}
function labelBadge(label, status) {
  if (['error', 'failed'].includes(status)) return badge('Lỗi phân loại', 'danger');
  if (status === 'stale') return badge('Kết quả cũ · cần phân loại lại', 'warning');
  if (!label || !Object.hasOwn(LABELS, label)) return badge(status === 'skipped' ? 'Đã bỏ qua' : 'Chưa phân loại');
  return badge(LABELS[label], label === 'scam_indicators' ? 'danger' : label === 'advertising_unverified' ? 'warning' : label === 'reporting_warning' ? 'blue' : 'neutral');
}
function demoBadge(namespace) { return namespace === 'demo' ? badge('Dữ liệu thử', 'blue') : ''; }
function panel(title, subtitle, content, action = '') {
  return `<section class="panel"><div class="panel-header"><div><h2 class="panel-title">${esc(title)}</h2>${subtitle ? `<p class="panel-subtitle">${esc(subtitle)}</p>` : ''}</div>${action}</div><div class="panel-body">${content}</div></section>`;
}
function button(label, action, extra = '', primary = false) {
  return `<button type="button" class="btn ${primary ? 'btn--primary' : 'btn--secondary'}" data-action="${esc(action)}" ${extra}>${esc(label)}</button>`;
}
function empty(title, description, action = '') {
  return `<div class="empty-state"><div class="empty-icon">${icon('inbox', 28)}</div><h3>${esc(title)}</h3><p>${esc(description)}</p>${action}</div>`;
}
function info(text, tone = '') { return `<div class="info-banner ${tone}" role="note">${icon('alert', 18)}<span>${esc(text)}</span></div>`; }
function externalUrl(value) {
  try { const url = new URL(String(value)); return ['https:', 'http:'].includes(url.protocol) ? url.href : ''; } catch { return ''; }
}
function mediaUrl(path) {
  if (typeof path !== 'string' || !path || /(^[\\/]|(^|[\\/])\.\.([\\/]|$))/.test(path)) return '';
  return `/api/media?path=${encodeURIComponent(path)}`;
}
function safeMediaUrl(value) {
  try {
    const url = new URL(String(value), location.origin);
    return url.origin === location.origin && url.pathname === '/api/media' ? url.href : '';
  } catch { return ''; }
}
function toast(message, error = false) {
  const target = $('#toast');
  if (!target) return;
  clearTimeout(toastTimer);
  target.textContent = message;
  target.classList.toggle('is-error', error);
  target.classList.add('visible');
  target.hidden = false;
  toastTimer = setTimeout(() => { target.classList.remove('visible'); target.hidden = true; }, error ? 9000 : 5000);
}
function connection(text, failed = false) {
  const target = $('#connection-status');
  if (target) { target.textContent = text; target.classList.toggle('is-error', failed); }
}
class ApiError extends Error {
  constructor(message, status, code) { super(message); this.status = status; this.code = code; }
}
async function api(path, { method = 'GET', body, signal, db = true } = {}) {
  const url = new URL(`/api/${path}`, location.origin);
  if (method === 'GET' && db && state.db) url.searchParams.set('db', state.db);
  const headers = { Accept: 'application/json' };
  if (method === 'POST') {
    if (!state.bootstrap?.token) throw new ApiError('Phiên dashboard chưa sẵn sàng. Hãy tải lại trang.', 403, 'missing_token');
    headers['X-Dashboard-Token'] = state.bootstrap.token;
    headers['Content-Type'] = 'application/json';
  }
  let response;
  try {
    response = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal, credentials: 'same-origin', cache: 'no-store' });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    connection('Mất kết nối', true);
    throw new ApiError('Không kết nối được máy chủ cục bộ. Hãy kiểm tra tiến trình dashboard.', 0, 'network_error');
  }
  let data;
  try { data = await response.json(); } catch { throw new ApiError(`Máy chủ trả về dữ liệu không hợp lệ (HTTP ${response.status}).`, response.status, 'invalid_response'); }
  if (!response.ok) {
    const prefix = response.status === 401 ? 'Không được xác thực. ' : response.status === 403 ? 'Yêu cầu bị từ chối. Hãy tải lại trang để lấy phiên mới. ' : response.status >= 500 ? 'Máy chủ gặp lỗi. ' : '';
    throw new ApiError(prefix + (typeof data.error === 'string' ? data.error : `HTTP ${response.status}`), response.status, data.code);
  }
  connection('Kết nối cục bộ');
  return data;
}
function route() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [name, query = ''] = raw.split('?');
  return { view: Object.hasOwn(VIEWS, name) ? name : 'overview', params: new URLSearchParams(query) };
}
export async function navigate(view = 'overview', params = {}) {
  if (!Object.hasOwn(VIEWS, view)) view = 'overview';
  const query = new URLSearchParams(params).toString();
  const hash = `#${view}${query ? `?${query}` : ''}`;
  if (location.hash === hash) return loadView();
  location.hash = hash;
}
function setHeader() {
  const [title, description] = VIEWS[state.view];
  if ($('#page-title')) $('#page-title').textContent = title;
  if ($('#page-description')) $('#page-description').textContent = description;
  document.title = `${title} · TínTrạm`;
  $$('[data-view]').forEach((node) => {
    const active = node.dataset.view === state.view;
    node.classList.toggle('active', active);
    if (active) node.setAttribute('aria-current', 'page'); else node.removeAttribute('aria-current');
  });
  const action = $('#page-action');
  if (action) {
    action.hidden = state.view !== 'overview';
    action.textContent = 'Thu thập dữ liệu';
    action.dataset.action = 'collect';
  }
  document.body.classList.remove('nav-open');
  $('#mobile-menu')?.setAttribute('aria-expanded', 'false');
}
async function bootstrap() {
  const data = await api('bootstrap', { db: false });
  state.bootstrap = data;
  const databases = list(data.databases);
  if (!databases.some((item) => item.name === state.db)) state.db = data.active_database || databases[0]?.name || '';
  const select = $('#database-select');
  if (select) {
    select.innerHTML = databases.map((item) => `<option value="${esc(item.name)}"${item.name === state.db ? ' selected' : ''}>${esc(item.label || item.name)}</option>`).join('');
    select.disabled = !databases.length;
  }
  if (Array.isArray(data.jobs)) updateJobs(data.jobs);
  if ($('#nav-status')) $('#nav-status').textContent = state.db ? `SQLite · ${state.db}` : 'Chưa có cơ sở dữ liệu';
}
export async function refresh() {
  $('#refresh-button')?.setAttribute('aria-busy', 'true');
  try { await bootstrap(); await loadView(); } catch (error) { showPageError(error); }
  finally { $('#refresh-button')?.removeAttribute('aria-busy'); }
}
function showPageError(error) {
  if (error.name === 'AbortError') return;
  const root = $('#view-root');
  if (root) root.innerHTML = panel('Không tải được dữ liệu', '', `<div role="alert">${info(error.message)}${error.code ? `<p class="hint">Mã lỗi: ${esc(error.code)}</p>` : ''}${button('Thử tải lại', 'retry')}</div>`);
  connection(error.status === 401 || error.status === 403 ? 'Phiên không hợp lệ' : 'Không tải được dữ liệu', true);
}
async function loadView({ quiet = false } = {}) {
  state.controller?.abort();
  state.controller = new AbortController();
  const signal = state.controller.signal;
  const revision = ++state.revision;
  const current = route();
  state.view = current.view; state.params = current.params;
  setHeader();
  const root = $('#view-root');
  if (!root) return;
  root.setAttribute('aria-busy', 'true');
  if (!quiet) root.innerHTML = '<div class="section-stack" role="status" aria-label="Đang tải dữ liệu"><div class="skeleton" style="height:140px"></div><div class="skeleton" style="height:320px"></div></div>';
  try {
    if (!state.bootstrap) await bootstrap();
    const renderers = { overview: renderOverview, messages: renderMessages, sources: renderSources, collection: renderCollection, classify: renderClassify, profiles: renderProfiles, video: renderVideo, jobs: renderJobs, settings: renderSettings };
    const html = await renderers[state.view](signal);
    if (revision !== state.revision) return;
    root.innerHTML = html;
    root.dataset.view = state.view;
    syncFormOptions();
    schedulePoll();
  } catch (error) { if (revision === state.revision) showPageError(error); }
  finally { if (revision === state.revision) root.removeAttribute('aria-busy'); }
}
function messageRows(items) {
  return list(items).map((message) => `<tr>
    <td data-label="Nguồn"><div class="source-name">${esc(message.channel || message.username || 'Không rõ nguồn')}</div><div class="source-meta">${esc(message.username ? `@${String(message.username).replace(/^@/, '')}` : '')} ${demoBadge(message.namespace)}</div></td>
    <td data-label="Nội dung" class="text-cell"><button type="button" class="row-button" data-action="message" data-id="${esc(message.id)}">${esc(String(message.text || '(Tin không có nội dung chữ)').slice(0, 150))}${String(message.text || '').length > 150 ? '…' : ''}</button></td>
    <td data-label="Jev">${labelBadge(message.label, message.classification_stale ? 'stale' : message.classification_status)}${message.classification_model ? `<div class="source-meta">${esc(message.classification_model)}</div>` : ''}</td>
    <td data-label="Heuristic"><span class="risk-score">${number(message.score)}</span><div class="source-meta">${esc(message.level || 'Chưa chấm')}</div></td>
    <td data-label="Thời gian" class="source-meta">${esc(date(message.posted_at || message.collected_at))}</td>
    <td data-label="Chi tiết"><button type="button" class="icon-button" data-action="message" data-id="${esc(message.id)}" aria-label="Xem chi tiết tin ${esc(message.id)}">${icon('arrow', 18)}</button></td>
  </tr>`).join('');
}
function messageTable(items) {
  if (!list(items).length) return empty('Chưa có tin nhắn phù hợp', 'Thử bộ lọc khác hoặc thu thập một nguồn công khai.', button('Thu thập dữ liệu', 'collect'));
  return `<div class="table-wrap"><table class="data-table message-table"><thead><tr><th scope="col">Nguồn</th><th scope="col">Nội dung tin nhắn</th><th scope="col">Kết quả Jev</th><th scope="col">Heuristic</th><th scope="col">Thời gian</th><th scope="col"><span class="muted">Chi tiết</span></th></tr></thead><tbody>${messageRows(items)}</tbody></table></div>`;
}
function dailyChart(daily) {
  const values = list(daily).map((item) => ({ day: String(item.day ?? ''), count: Math.max(0, Number(item.count) || 0) }));
  if (!values.length) return empty('Chưa có chuỗi thời gian', 'Biểu đồ xuất hiện khi cơ sở dữ liệu có tin nhắn kèm ngày.');
  const width = 760, height = 220, x0 = 48, y0 = 18, chartWidth = width - 68, chartHeight = height - 55;
  const maximum = Math.max(1, ...values.map((item) => item.count));
  const points = values.map((item, index) => [x0 + (values.length === 1 ? chartWidth / 2 : index / (values.length - 1) * chartWidth), y0 + chartHeight - item.count / maximum * chartHeight]);
  const line = points.map(([x, y], index) => `${index ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)}`).join(' ');
  const base = y0 + chartHeight;
  const ticks = [0, .25, .5, .75, 1].map((ratio) => `<line x1="${x0}" x2="${width - 20}" y1="${base - ratio * chartHeight}" y2="${base - ratio * chartHeight}" stroke="currentColor" opacity=".09"/><text x="${x0 - 10}" y="${base - ratio * chartHeight + 4}" text-anchor="end" fill="currentColor" opacity=".6" font-size="11">${number(Math.round(maximum * ratio))}</text>`).join('');
  const labels = [...new Set([0, Math.floor((values.length - 1) / 2), values.length - 1])].map((index) => `<text x="${points[index][0]}" y="${height - 8}" text-anchor="middle" fill="currentColor" opacity=".6" font-size="11">${esc(values[index].day)}</text>`).join('');
  return `<svg class="chart-svg" viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="daily-title daily-desc"><title id="daily-title">Số tin nhắn theo ngày</title><desc id="daily-desc">${esc(values.map((item) => `${item.day}: ${item.count} tin`).join('; '))}</desc>${ticks}<path d="${line} L${points.at(-1)[0]},${base} L${points[0][0]},${base} Z" fill="currentColor" opacity=".08"/><path d="${line}" fill="none" stroke="currentColor" stroke-width="2.5"/>${points.map(([x, y], index) => `<circle cx="${x}" cy="${y}" r="3.5" fill="currentColor"><title>${esc(values[index].day)}: ${number(values[index].count)} tin</title></circle>`).join('')}${labels}</svg>`;
}
function sourceDisplay(value) {
  const text = String(value || '');
  const pathLike = /^(?:\/|[A-Za-z]:[\\/]|\.{1,2}[\\/]|\\\\)/.test(text)
    || (!/^[A-Za-z][A-Za-z\d+.-]*:\/\//.test(text) && /^[^\s]+[\\/][^\s]+\.(?:db|sqlite3?|jsonl?|ndjson|csv|txt)$/i.test(text));
  return pathLike ? text.split(/[\\/]/).filter(Boolean).at(-1) || text : text;
}
async function renderOverview(signal) {
  const data = await api('overview', { signal });
  const counts = data.counts || {};
  const classified = Math.max(0, Number(counts.classified) || 0), total = Math.max(0, Number(counts.messages) || 0);
  const ratio = total ? Math.min(100, classified / total * 100) : 0;
  const stats = [
    ['Tổng tin nhắn', counts.messages, 'Đã lưu trong SQLite', 'message'],
    ['Nguồn dữ liệu', counts.entities, 'Kênh, nhóm và hồ sơ đã lưu', 'sources'],
    ['Jev đã phân loại', counts.classified, 'Kết quả phân loại đã ghi nhận', 'scan'],
    ['Dấu hiệu lừa đảo', counts.scam_indicators, 'Theo nhãn Jev mới nhất · không phải kết luận', 'alert'],
  ].map(([label, value, meta, name]) => `<article class="stat-card"><div class="stat-label">${esc(label)}<span class="stat-icon">${icon(name)}</span></div><div class="stat-value">${number(value)}</div><div class="stat-meta">${esc(meta)}</div></article>`).join('');
  const sources = list(data.sources).slice(0, 5).map((source, index) => `<div class="source-row"><span class="avatar">${esc(String(source.title || source.username || '?').slice(0, 1).toLocaleUpperCase('vi'))}</span><div><button class="row-button source-name" type="button" data-action="source-messages" data-username="${esc(source.username || source.title || '')}">${esc(source.title || source.username || source.id)}</button><div class="source-meta">${esc(source.username ? `@${String(source.username).replace(/^@/, '')}` : source.kind || 'Nguồn đã lưu')}</div></div><div><strong>${number(source.message_count)}</strong><span class="source-meta"> tin nhắn</span></div><span class="muted">${String(index + 1).padStart(2, '0')}</span></div>`).join('');
  const runs = list(data.recent_runs).slice(0, 4).map((run) => `<div class="activity-row">${icon('clock', 18)}<div><div class="source-name">${esc(run.kind || run.command || sourceDisplay(run.source) || 'Lần thu thập')}</div><div class="source-meta">${esc(date(run.created_at || run.started_at))}</div></div>${run.status ? statusBadge(run.status) : badge(run.backend || 'Bản ghi đã lưu')}</div>`).join('');
  return `<div class="section-stack"><div class="stats-grid">${stats}</div><div class="split-grid">${panel('Nhịp thu thập', 'Số tin nhắn theo ngày trong dữ liệu đã lưu', `<div class="chart-panel">${dailyChart(data.daily)}</div><div class="chart-legend"><span class="legend-dot"></span> Tin nhắn đã lưu</div>`)}${panel('Nguồn nổi bật', 'Xếp theo số tin nhắn đã lưu', `<div class="source-list">${sources || empty('Chưa có nguồn', 'Thêm một kênh công khai để bắt đầu.')}</div>`, button('Xem nguồn', 'sources'))}</div>${panel('Tin nhắn gần đây', 'Nhãn Jev và điểm heuristic được hiển thị độc lập.', messageTable((data.recent_messages || []).slice(0, 5)), button('Xem tin nhắn', 'messages'))}<div class="split-grid">${panel('Mức bao phủ Jev', 'Chỉ tính trên những tin nhắn đã có kết quả.', `<div class="detail-grid"><div><strong class="stat-value">${number(classified)}</strong><span class="muted"> / ${number(total)} tin</span></div>${badge(total ? `${ratio.toLocaleString('vi-VN', { maximumFractionDigits: 1 })}% đã phân loại` : 'Chưa có dữ liệu', 'blue')}</div><div class="progress-track" role="progressbar" aria-label="Tỷ lệ tin nhắn được Jev phân loại" aria-valuenow="${ratio.toFixed(1)}" aria-valuemin="0" aria-valuemax="100"><div class="progress-fill" style="width:${ratio.toFixed(2)}%"></div></div><p class="hint">Tin chưa phân loại không đồng nghĩa an toàn. Nhãn chỉ hỗ trợ sàng lọc nội dung.</p>`)}${panel('Lần chạy gần đây', 'Nhật ký thu thập được lưu trong cơ sở dữ liệu.', `<div class="activity-list">${runs || '<p class="muted">Chưa có lịch sử chạy.</p>'}</div>`, button('Xem tác vụ', 'jobs'))}</div></div>`;
}
function selectOptions(options, selected = '') {
  return options.map(([value, label]) => `<option value="${esc(value)}"${String(value) === String(selected) ? ' selected' : ''}>${esc(label)}</option>`).join('');
}
function field(id, label, input, hint = '', full = false) {
  return `<div class="field${full ? ' field--full' : ''}"><label for="${esc(id)}">${esc(label)}</label>${input}${hint ? `<p class="hint" id="${esc(id)}-hint">${esc(hint)}</p>` : ''}</div>`;
}
function textInput(id, name, value = '', attrs = '') { return `<input id="${esc(id)}" name="${esc(name)}" value="${esc(value)}" ${attrs}>`; }
function check(id, name, label, checked = false) { return `<label class="toggle-row" for="${esc(id)}"><span>${esc(label)}</span><input class="check-input" type="checkbox" id="${esc(id)}" name="${esc(name)}"${checked ? ' checked' : ''}></label>`; }
function formEnd(label, hint = '', primary = true) {
  return `<div class="form-actions"><button class="btn ${primary ? 'btn--primary' : 'btn--secondary'}" type="submit">${esc(label)}</button>${hint ? `<span class="hint">${esc(hint)}</span>` : ''}</div><p class="field-error" data-form-error role="alert" hidden></p><p class="inline-status" data-form-status role="status"></p>`;
}
function keyWarning() {
  return state.bootstrap?.config?.jev_configured ? '' : `<div class="info-banner">${icon('key', 18)}<span>Chưa cấu hình khóa Jev. Tác vụ gọi mô hình cần khóa hợp lệ.</span>${button('Mở cài đặt', 'settings')}</div>`;
}
async function renderMessages(signal) {
  const params = new URLSearchParams(state.params);
  params.set('limit', '20');
  if (!params.has('offset')) params.set('offset', '0');
  const data = await api(`messages?${params}`, { signal });
  const total = Number(data.total) || 0, offset = Number(data.offset ?? params.get('offset')) || 0;
  const currentLabel = state.params.get('label') || '';
  const tabs = [['', 'Tất cả'], ['scam_indicators', 'Dấu hiệu lừa đảo'], ['advertising_unverified', 'Quảng cáo'], ['reporting_warning', 'Cảnh báo']].map(([value, label]) => `<button type="button" class="filter-tab${currentLabel === value ? ' active' : ''}" data-action="label-filter" data-label="${value}" aria-pressed="${currentLabel === value}">${label}</button>`).join('');
  return `<div class="section-stack">${panel('Kho tin nhắn', `${number(total)} tin nhắn phù hợp`, `<form id="messages-filter" class="toolbar" data-form="messages-filter">${field('messages-query', 'Tìm nội dung', textInput('messages-query', 'query', state.params.get('query') || '', 'type="search" maxlength="256" placeholder="Nội dung hoặc từ khóa"'))}${field('messages-username', 'Tên nguồn', textInput('messages-username', 'username', state.params.get('username') || '', 'maxlength="256" placeholder="@kenh"'))}${field('messages-label', 'Nhãn Jev', `<select id="messages-label" name="label">${selectOptions([['', 'Mọi nhãn'], ...Object.entries(LABELS), ['unclassified', 'Chưa phân loại'], ['error', 'Lỗi phân loại']], currentLabel)}</select>`)}${field('messages-level', 'Mức heuristic', `<select id="messages-level" name="level">${selectOptions([['', 'Mọi mức'], ['High-risk', 'Rủi ro cao'], ['Suspicious', 'Đáng chú ý'], ['Normal', 'Thông thường']], state.params.get('level') || '')}</select>`)}<button type="submit" class="btn btn--secondary">${icon('search', 18)} Lọc tin nhắn</button></form><div class="filter-tabs" aria-label="Bộ lọc nhãn Jev">${tabs}</div>${messageTable(data.items)}<div class="pagination"><span class="muted">${total ? `${number(offset + 1)}–${number(Math.min(offset + list(data.items).length, total))}` : '0'} / ${number(total)} tin nhắn</span><div class="quick-actions">${button('Trang trước', 'page', `data-offset="${Math.max(0, offset - 20)}"${offset === 0 ? ' disabled' : ''}`)}${button('Trang sau', 'page', `data-offset="${offset + 20}"${offset + 20 >= total ? ' disabled' : ''}`)}</div></div>`)}${info('Heuristic là điểm theo quy tắc. Nhãn Jev là đánh giá của mô hình. Cả hai không xác nhận một tài khoản hay cá nhân lừa đảo.')}</div>`;
}
async function renderSources(signal) {
  const data = await api('sources', { signal });
  const items = list(data.items);
  const rows = items.map((item) => `<tr><td><span class="source-name">${esc(item.title || item.username || item.id)}</span><div class="source-meta">${esc(item.username ? `@${String(item.username).replace(/^@/, '')}` : '')}</div></td><td>${esc(item.kind || '—')}</td><td>${number(item.message_count)}</td><td>${number(item.flagged)}</td><td>${button('Xem tin nhắn', 'source-messages', `data-username="${esc(item.username || item.title || '')}"`)}</td></tr>`).join('');
  return panel('Danh sách nguồn', `${number(data.total ?? items.length)} nguồn đã lưu · bộ đếm từ SQLite`, items.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Nguồn</th><th>Loại</th><th>Tin nhắn</th><th>Heuristic gắn cờ</th><th>Nội dung</th></tr></thead><tbody>${rows}</tbody></table></div>` : empty('Chưa có nguồn dữ liệu', 'Thu thập tin nhắn từ một kênh công khai.', button('Thu thập dữ liệu', 'collect')));
}
function sessionField(prefix) {
  const sessions = list(state.bootstrap?.config?.sessions).map((item) => typeof item === 'string' ? [item, item] : [item.name, item.label || item.name]);
  return field(`${prefix}-session`, 'Phiên Telegram', `<select id="${prefix}-session" name="session">${selectOptions([['', 'Phiên mặc định đã đăng nhập'], ...sessions])}</select>`, 'API cần phiên CLI đã đăng nhập sẵn; dashboard không thực hiện đăng nhập tương tác.');
}
function backendField(prefix) {
  return field(`${prefix}-backend`, 'Phương thức thu thập', `<select id="${prefix}-backend" name="backend">${selectOptions([['web', 'Web công khai · không cần phiên'], ['api', 'Telegram API · phiên có sẵn']])}</select>`);
}
function collectFields(prefix, watch = false) {
  const channels = field(`${prefix}-channels`, 'Kênh / nhóm công khai',
    `<textarea id="${prefix}-channels" name="channels" rows="3" maxlength="2000" required placeholder="@kenh_cong_khai&#10;@kenh_khac"></textarea>`,
    'Tối đa 20 username, mỗi dòng một nguồn (không nhập URL). Chỉ thu thập nội dung bạn có quyền truy cập.', true);
  const limit = watch ? '' : field(`${prefix}-limit`, 'Số tin tối đa mỗi nguồn',
    textInput(`${prefix}-limit`, 'limit', 20, 'type="number" min="1" max="1000" required'));
  const windowFields = watch ? [
    field('watch-interval', 'Cửa sổ theo dõi (giây)',
      textInput('watch-interval', 'interval', 60, 'type="number" min="10" max="3600" required'),
      'Tự dừng khi hết khoảng thời gian này.'),
    field('watch-cycles', 'Dừng khi đủ số tin mới',
      textInput('watch-cycles', 'cycles', 30, 'type="number" min="1" max="1000" required'),
      'cycles của CLI là số tin mới tối đa, không phải số vòng lặp.'),
    info('Chế độ một cửa sổ hữu hạn: dừng khi hết thời gian hoặc đạt giới hạn tin mới. Không theo dõi vô hạn.'),
  ].join('') : '';
  const budget = field(`${prefix}-budget`, 'Ngân sách yêu cầu Jev',
    textInput(`${prefix}-budget`, 'jev_limit', 10, 'type="number" min="1" max="100" required'),
    'Số tin mới tối đa được gửi đến mô hình trong lần chạy này.');
  return `${watch ? '' : backendField(prefix)}${sessionField(prefix)}${channels}${limit}${windowFields}${check(`${prefix}-jev`, 'jev', 'Phân loại tin mới bằng Jev (tùy chọn)')}${budget}`;
}
async function renderCollection() {
  return `<div class="section-stack">${keyWarning()}${info('Thu thập sử dụng mạng. Khi bật Jev, nội dung tin nhắn có thể được gửi tới nhà cung cấp mô hình và phát sinh chi phí. Bạn sẽ xác nhận trước khi gửi.')}<div class="split-grid">${panel('Thu thập một lần', 'Lưu nguồn, tin nhắn và lịch sử chạy vào cơ sở dữ liệu đang chọn.', `<form data-form="collect" id="collect-form"><div class="form-grid">${collectFields('collect')}</div><div class="info-banner"><span data-budget="collect">Ngân sách Jev: tối đa 10 yêu cầu khi bật phân loại.</span></div>${formEnd('Bắt đầu thu thập', 'Tác vụ chạy nền; theo dõi kết quả tại mục Tác vụ.')}</form>`)}${panel('Theo dõi một cửa sổ', 'Telegram API · tự dừng theo thời gian hoặc số tin mới.', `<form data-form="watch" id="watch-form"><div class="form-grid">${collectFields('watch', true)}</div><div class="info-banner"><span data-budget="watch">Ngân sách Jev: tối đa 10 yêu cầu cho 1 chu kỳ.</span></div>${formEnd('Bắt đầu theo dõi', 'Dừng tác vụ đang chạy trong mục Tác vụ.', false)}</form>`)}</div></div>`;
}
async function renderClassify(signal) {
  const data = await api('messages?limit=8&offset=0', { signal });
  return `<div class="section-stack">${keyWarning()}${panel('Chọn nội dung phân loại', 'Chỉ gửi tin nhắn đã lưu. Dữ liệu thử bị loại trừ nếu chưa bật cho phép.', `<form id="classify-form" data-form="classify"><div class="form-grid">${field('classify-limit', 'Ngân sách yêu cầu tối đa', textInput('classify-limit', 'limit', 10, 'type="number" min="1" max="100" required'), 'Mỗi tin được chọn có thể tạo một yêu cầu mô hình; có thể phát sinh chi phí.')}${field('classify-query', 'Từ khóa nội dung', textInput('classify-query', 'query', '', 'type="search" maxlength="256"'))}${field('classify-username', 'Tên nguồn (tùy chọn)', textInput('classify-username', 'username', '', 'maxlength="100"'))}${check('classify-demo', 'include_demo', 'Cho phép phân loại dữ liệu thử')}${check('classify-recheck', 'recheck', 'Phân loại lại tin đã có kết quả')}</div>${formEnd('Gửi phân loại', 'Cần xác nhận gửi nội dung tới mô hình. Không bảo đảm một kết luận về cá nhân.')}</form>`)}${panel('Nội dung và nhãn mới nhất', 'Mỗi nhãn đi kèm trạng thái thực tế; chưa phân loại không được gán là an toàn.', messageTable(data.items), button('Mở kho tin nhắn', 'messages'))}</div>`;
}
async function renderProfiles(signal) {
  const query = state.params.get('query') || '';
  const data = await api(`profiles?query=${encodeURIComponent(query)}`, { signal });
  const rows = list(data.items).map((item) => `<tr><td><div class="source-name">${esc(item.title || item.username || 'Hồ sơ không có tên')}</div><div class="source-meta">${esc(item.username ? `@${String(item.username).replace(/^@/, '')}` : '')}</div></td><td>${esc(item.kind || '—')}</td><td class="text-cell">${esc(item.bio || 'Chưa có giới thiệu')}</td><td>${number(item.participants_count)}</td><td>${esc(Array.isArray(item.common_groups) ? item.common_groups.map((group) => typeof group === 'string' ? group : group.title || group.username || '').join(', ') : item.common_groups || '—')}</td><td>${esc(item.phone || 'Không công khai')}</td></tr>`).join('');
  return `<div class="section-stack"><div class="split-grid">${panel('Tra cứu username', 'Chỉ dùng thông tin công khai, không đăng nhập mới trong trình duyệt.', `<form data-form="lookup"><div class="form-grid">${field('lookup-users', 'Username cần tra cứu', textInput('lookup-users', 'username', '', 'required maxlength="100" placeholder="@username"'), 'Một username công khai cho mỗi lần tra cứu.', true)}${backendField('lookup')}${sessionField('lookup')}</div>${formEnd('Tra cứu hồ sơ', 'Cần mạng; kết quả sẽ được ghi vào SQLite.')}</form>`)}${panel('Tìm hồ sơ theo từ khóa', 'Gửi tác vụ tìm kiếm, không suy đoán thông tin riêng tư.', `<form data-form="search"><div class="form-grid">${field('profile-search-query', 'Từ khóa tìm kiếm', textInput('profile-search-query', 'keyword', '', 'required maxlength="200"'))}${field('profile-search-limit', 'Giới hạn kết quả', textInput('profile-search-limit', 'limit', 20, 'type="number" min="1" max="100" required'))}${field('profile-search-backend', 'Phương thức tìm kiếm', '<select id="profile-search-backend" name="backend"><option value="api">Telegram API · phiên có sẵn</option></select>')}${sessionField('profile-search')}</div>${formEnd('Tìm hồ sơ', 'Tác vụ có thể dùng mạng.', false)}</form>`)}</div>${panel('Hồ sơ đã lưu', `${number(data.total)} hồ sơ phù hợp`, `<form data-form="profiles-filter" class="toolbar">${field('profiles-filter-query', 'Tìm trong hồ sơ đã lưu', textInput('profiles-filter-query', 'query', query, 'type="search" maxlength="256"'))}<button type="submit" class="btn btn--secondary">Lọc hồ sơ</button></form>${rows ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Hồ sơ</th><th>Loại</th><th>Giới thiệu công khai</th><th>Thành viên</th><th>Nhóm chung đã lưu</th><th>Điện thoại đã lưu</th></tr></thead><tbody>${rows}</tbody></table></div>` : empty('Chưa có hồ sơ phù hợp', 'Tra cứu một username hoặc đổi từ khóa lọc.')}`)}</div>`;
}
function videoDetails(analysis, classifications) {
  const frames = list(analysis.frames).map((frame) => {
    const url = safeMediaUrl(frame.url);
    return url ? `<div class="video-card"><img class="video-thumb" src="${esc(url)}" alt="Khung hình lấy mẫu tại ${esc(frame.timestamp ?? '?')} giây" loading="lazy"><span class="source-meta">${esc(frame.timestamp ?? '?')} giây</span></div>` : '';
  }).join('');
  const matching = list(classifications).filter((item) => String(item.video_analysis_id) === String(analysis.id));
  const verdicts = matching.map((item) => `<div class="activity-row">${labelBadge(item.label, item.status)}${statusBadge(item.status)}<span class="source-meta">${esc(item.model || '')} ${esc(item.error_code || '')}</span></div>`).join('');
  return `<div class="detail-grid"><div class="detail-block"><h3 class="section-heading">Bản phân tích #${esc(analysis.id)}</h3>${statusBadge(analysis.status)}<p class="source-meta">${esc(analysis.source_path)} · ${esc(date(analysis.created_at))}</p>${analysis.error_code ? `<p class="field-error">${esc(analysis.error_code)}</p>` : ''}<dl class="meta-list"><dt>SHA-256</dt><dd class="mono">${esc(analysis.source_sha256 || 'Chưa có')}</dd></dl></div><div class="detail-block"><h3 class="section-heading">Kết quả mô hình Jev</h3>${verdicts || '<p class="muted">Chưa có kết quả phân loại video được lưu.</p>'}</div></div><div class="detail-block"><h3 class="section-heading">Lời nói / bản chép</h3><pre class="detail-text">${esc(analysis.transcript == null ? 'Chưa có bản chép lời nói. Không thể suy ra video không có lời nói.' : pretty(analysis.transcript))}</pre></div><div class="detail-block"><h3 class="section-heading">Quan sát thị giác</h3><pre class="detail-text">${esc(analysis.vision == null ? 'Chưa có quan sát thị giác. Các khung hình không đại diện mọi thời điểm.' : pretty(analysis.vision))}</pre></div><div class="detail-block"><h3 class="section-heading">Bằng chứng và khoảng trống</h3>${info('Lấy mẫu khung hình không bao phủ toàn bộ video. Lời nói, thị giác hoặc thời điểm không được phân tích là khoảng trống, không phải bằng chứng nội dung an toàn.')}<pre class="detail-text">${esc(pretty({ evidence: analysis.evidence, sampling: analysis.sampling }))}</pre></div>${frames ? `<div class="video-grid">${frames}</div>` : '<p class="hint">Không có khung hình có thể hiển thị trong bản phân tích này.</p>'}`;
}
async function renderVideo(signal) {
  const data = await api('videos', { signal });
  state.videos = data;
  const files = list(data.files), analyses = list(data.analyses);
  if (!files.some((file) => file.path === state.selectedVideo)) state.selectedVideo = files[0]?.path || '';
  const selected = files.find((file) => file.path === state.selectedVideo);
  const selectedAnalysisId = state.params.get('analysis') || analyses[0]?.id;
  const selectedAnalysis = analyses.find((analysis) => String(analysis.id) === String(selectedAnalysisId));
  const fileOptions = files.map((file) => [file.path, file.name || file.path]);
  const player = selected && mediaUrl(selected.path) ? `<video class="video-thumb" controls playsinline preload="metadata" src="${esc(mediaUrl(selected.path))}" aria-label="Xem video ${esc(selected.name)}"></video><div class="source-meta">${esc(selected.name)} · ${number(selected.size)} byte · ${esc(date(selected.modified))}</div>` : empty('Chưa có video cục bộ', 'Tải một video từ đường dẫn tin nhắn Telegram để bắt đầu.');
  const config = state.bootstrap?.config || {};
  return `<div class="section-stack">${keyWarning()}${!config.whisper_available ? info('Whisper chưa sẵn sàng trong môi trường máy chủ. Phân tích giọng nói có thể báo lỗi hoặc thiếu bước.') : ''}${!config.vision_configured ? info('Chưa có cấu hình mô hình thị giác. Bạn có thể chọn bỏ qua thị giác; kết quả phải ghi nhận khoảng trống này.') : ''}<div class="split-grid">${panel('Thư viện video', `${number(files.length)} file trong thư mục media được máy chủ cho phép`, `${field('video-file-select', 'Video cục bộ', `<select id="video-file-select" data-action="select-video">${selectOptions(fileOptions.length ? fileOptions : [['', 'Chưa có video']], state.selectedVideo)}</select>`)}<div id="video-player">${player}</div>`)}${panel('Tải video từ Telegram', 'Một liên kết tin nhắn, sử dụng phiên đã đăng nhập.', `<form data-form="download"><div class="form-grid">${field('video-download-url', 'Liên kết tin nhắn chứa video', textInput('video-download-url', 'url', '', 'type="url" required placeholder="https://t.me/kenh/123"'), 'Chỉ một video, không tải playlist.', true)}${sessionField('video-download')}</div>${formEnd('Tải video', 'Dùng mạng; có thể tải file dung lượng lớn.', false)}</form>`)}</div>${panel('Phân tích lời nói và khung hình', 'Chọn file đã lưu. Tối đa 12 khung hình, không tự tải model.', `<form data-form="video_analyze"><div class="form-grid">${field('video-analysis-path', 'File cần phân tích', `<select id="video-analysis-path" name="path" required>${selectOptions(fileOptions.length ? fileOptions : [['', 'Chưa có video']], state.selectedVideo)}</select>`)}${field('video-language', 'Ngôn ngữ lời nói', `<select id="video-language" name="language" required>${selectOptions([['vi', 'Tiếng Việt'], ['en', 'Tiếng Anh']])}</select>`)}${field('video-whisper-model', 'Mô hình Whisper cục bộ', `<select id="video-whisper-model" name="model">${selectOptions([['tiny', 'Whisper tiny'], ['base', 'Whisper base']])}</select>`)}${field('video-max-frames', 'Ngân sách khung hình tối đa', textInput('video-max-frames', 'max_frames', 12, 'type="number" min="1" max="12" required'), 'Mỗi khung hình có thể tạo một yêu cầu thị giác. Chi phí tùy nhà cung cấp.')}${check('video-skip-vision', 'skip_vision', 'Bỏ qua phân tích thị giác', !config.vision_configured)}</div>${formEnd('Phân tích video', 'Cần xác nhận nếu gửi nội dung đến mô hình từ xa.')}</form>`)}<div class="split-grid">${panel('Phân loại một bản phân tích', 'Chỉ dùng analysis ID đã lưu, không gán nhãn từ tên file.', `<form data-form="video_classify"><div class="form-grid">${field('video-analysis-id', 'Bản phân tích đã lưu', `<select id="video-analysis-id" name="analysis_id" required>${selectOptions(analyses.length ? analyses.map((analysis) => [analysis.id, `#${analysis.id} · ${analysis.source_path || analysis.status}`]) : [['', 'Chưa có bản phân tích']], selectedAnalysisId)}</select>`)}${check('video-allow-partial', 'allow_partial', 'Chấp nhận bản phân tích còn khoảng trống')}</div>${formEnd('Phân loại video', 'Ngân sách: 1 yêu cầu Jev. Có thể phát sinh chi phí.', false)}</form>`)}${panel('Phạm vi bằng chứng', 'Một kết quả mô hình không xác nhận danh tính hay hành vi.', `<div class="pipeline-strip"><div class="pipeline-step">Video đã lưu</div><div class="pipeline-step">Lời nói / khung hình</div><div class="pipeline-step">Bằng chứng</div><div class="pipeline-step">Nhãn Jev</div></div><p class="hint">Kiểm tra trạng thái từng bước, lỗi và phần chưa được quan sát trước khi đọc nhãn. Nội dung ngoài các khung lấy mẫu vẫn chưa được kiểm chứng.</p>`)}</div>${panel('Bản phân tích đã lưu', `${number(analyses.length)} bản phân tích · ${number(list(data.classifications).length)} kết quả phân loại`, `${analyses.length ? field('saved-video-analysis', 'Xem bản phân tích', `<select id="saved-video-analysis" data-action="select-analysis">${selectOptions(analyses.map((analysis) => [analysis.id, `#${analysis.id} · ${analysis.source_path || ''} · ${STATUS_NAMES[analysis.status] || analysis.status}`]), selectedAnalysisId)}</select>`) : ''}${selectedAnalysis ? videoDetails(selectedAnalysis, data.classifications) : empty('Chưa có phân tích video', 'Phân tích một file trong thư viện để xem bằng chứng và trạng thái tại đây.')}`)}</div>`;
}
function activeJob(job) { return ['queued', 'pending', 'running', 'cancelling'].includes(job.status); }
function jobCard(job) {
  const opened = state.expandedJobs.has(String(job.id));
  const detail = state.expandedJobs.get(String(job.id));
  const logs = detail?.logs;
  return `<article class="job-card" data-job-id="${esc(job.id)}"><div class="panel-header"><div><h2 class="panel-title">${esc(JOB_NAMES[job.kind] || job.kind || 'Tác vụ')} <span class="source-meta">#${esc(job.id)}</span></h2><p class="panel-subtitle">${esc(date(job.created_at))}${job.params?.db ? ` · ${esc(job.params.db)}` : ''}</p></div>${statusBadge(job.status)}</div><div class="panel-body"><div class="meta-list"><span>Bắt đầu: ${esc(date(job.started_at))}</span><span>Kết thúc: ${esc(date(job.ended_at))}</span><span>Mã thoát: ${job.exit_code === undefined || job.exit_code === null ? '—' : esc(job.exit_code)}</span></div><div class="quick-actions">${button(opened ? 'Ẩn nhật ký' : 'Xem nhật ký', 'job-log', `data-id="${esc(job.id)}" aria-expanded="${opened}" aria-controls="job-log-${esc(job.id)}"`)}${activeJob(job) ? button('Dừng tác vụ', 'cancel-job', `data-id="${esc(job.id)}"`) : ''}</div>${opened ? `<div id="job-log-${esc(job.id)}"><h3 class="section-heading">Tham số</h3><pre class="detail-text">${esc(pretty(detail?.params || job.params || {}))}</pre><h3 class="section-heading">Nhật ký tiến trình</h3><pre class="job-log" tabindex="0">${esc(Array.isArray(logs) ? logs.map((line) => typeof line === 'string' ? line : pretty(line)).join('\n') : logs || 'Chưa có nhật ký được ghi nhận.')}</pre></div>` : ''}</div></article>`;
}
async function renderJobs(signal) {
  const data = await api('jobs', { signal, db: false });
  updateJobs(list(data.items));
  return jobsMarkup();
}
function jobsMarkup() {
  const running = state.jobs.filter(activeJob).length;
  return `<div class="section-stack">${info(running ? `${running} tác vụ đang chờ hoặc đang chạy. Cập nhật mỗi 2 giây khi tab đang hiển thị.` : 'Không có tác vụ đang chạy. Trạng thái và mã thoát dưới đây là kết quả từ máy chủ.')}<div class="activity-list">${state.jobs.length ? state.jobs.map(jobCard).join('') : panel('Lịch sử tác vụ', '', empty('Chưa có tác vụ', 'Bắt đầu một lần thu thập hoặc phân loại để xem tiến trình tại đây.', button('Thu thập dữ liệu', 'collect')))}</div></div>`;
}
async function renderSettings() {
  const config = state.bootstrap?.config || {};
  return `<div class="section-stack">${panel('Kết nối mô hình', 'Dashboard chỉ hiển thị trạng thái; không đọc hoặc hiển thị lại khóa đã lưu.', `<div class="activity-list"><div class="activity-row">${icon('key')}<div><div class="source-name">Jev</div><div class="source-meta">Khóa được giữ trong bộ nhớ tiến trình, không ghi xuống đĩa.</div></div>${badge(config.jev_configured ? 'Đã cấu hình' : 'Chưa cấu hình', config.jev_configured ? 'success' : 'warning')}</div><div class="activity-row">${icon('scan')}<div><div class="source-name">Mô hình thị giác</div><div class="source-meta">Cấu hình tại môi trường máy chủ.</div></div>${badge(config.vision_configured ? 'Đã cấu hình' : 'Chưa cấu hình', config.vision_configured ? 'success' : 'neutral')}</div><div class="activity-row">${icon('message')}<div><div class="source-name">Whisper cục bộ</div><div class="source-meta">Không tự động tải trọng số từ giao diện này.</div></div>${badge(config.whisper_available ? 'Sẵn sàng' : 'Chưa sẵn sàng', config.whisper_available ? 'success' : 'neutral')}</div></div>`)}<div class="split-grid">${panel('Đặt khóa Jev cho phiên này', 'Khóa chỉ được gửi khi bạn xác nhận. Không lưu vào localStorage.', `<form data-form="settings"><div class="form-grid">${field('settings-jev-key', 'Khóa API Jev', '<input id="settings-jev-key" name="jev_key" type="password" autocomplete="off" spellcheck="false" required>', 'Trường khóa được xóa ngay sau khi gửi.', true)}</div>${formEnd('Đặt khóa Jev', 'Cấu hình không đồng nghĩa khóa đã được nhà cung cấp xác thực.')}</form><div class="divider"></div>${button('Xóa khóa phiên', 'clear-key')}`)}${panel('Dữ liệu và sao lưu', 'Bản sao lưu tạo bằng tác vụ backend, không sửa dữ liệu gốc.', `<dl class="meta-list"><dt>Cơ sở dữ liệu đang dùng</dt><dd>${esc(state.db || 'Chưa có')}</dd><dt>Phiên bản dashboard</dt><dd>${esc(state.bootstrap?.version || 'Chưa cung cấp')}</dd><dt>Phiên Telegram có sẵn</dt><dd>${number(list(config.sessions).length)}</dd></dl>${button('Tạo bản sao lưu', 'backup')}<p class="hint">Bạn sẽ xác nhận trước khi ghi một file sao lưu mới. Tên và kết quả thực tế được ghi trong nhật ký tác vụ.</p>`)}</div>${info('Đăng nhập Telegram bằng CLI hiện có trước khi dùng tác vụ API. Dashboard không hỏi mã OTP, mật khẩu Telegram hay thông tin đăng nhập tương tác.')}</div>`;
}
function updateJobs(jobs) {
  const previous = new Map(state.jobs.map((job) => [String(job.id), job.status]));
  state.jobs = jobs;
  const finished = jobs.filter((job) => previous.has(String(job.id)) && ['queued', 'pending', 'running', 'cancelling'].includes(previous.get(String(job.id))) && !activeJob(job));
  for (const job of finished) toast(`${JOB_NAMES[job.kind] || 'Tác vụ'} #${job.id}: ${STATUS_NAMES[job.status] || job.status}`, ['failed', 'error'].includes(job.status));
  return finished.length > 0;
}
function stopPoll() { clearTimeout(state.pollTimer); state.pollTimer = null; }
function schedulePoll() {
  stopPoll();
  if (document.hidden || !state.jobs.some(activeJob) || state.polling) return;
  state.pollTimer = setTimeout(pollJobs, 2000);
}
async function pollJobs() {
  state.pollTimer = null;
  if (document.hidden || state.polling) return;
  state.polling = true;
  const revision = state.revision;
  try {
    const data = await api('jobs', { db: false });
    const finished = updateJobs(list(data.items));
    for (const [id] of state.expandedJobs) {
      const job = state.jobs.find((item) => String(item.id) === id);
      if (job && (activeJob(job) || finished)) {
        const detail = await api(`jobs/${encodeURIComponent(id)}`, { db: false });
        state.expandedJobs.set(id, detail.job || detail);
      }
    }
    if (revision === state.revision && state.view === 'jobs') {
      const focused = document.activeElement?.closest('[data-action]');
      const focusAction = focused?.dataset.action, focusId = focused?.dataset.id;
      $('#view-root').innerHTML = jobsMarkup();
      if (focusAction) $$('[data-action]', $('#view-root')).find((node) => node.dataset.action === focusAction && node.dataset.id === focusId)?.focus({ preventScroll: true });
    } else if (finished && revision === state.revision && ['overview', 'messages', 'sources', 'video', 'profiles', 'classify'].includes(state.view)) {
      await loadView({ quiet: true });
    }
  } catch (error) { toast(`Không cập nhật được tác vụ: ${error.message}`, true); }
  finally { state.polling = false; schedulePoll(); }
}
function closeDrawer() {
  ++state.drawerRevision;
  const drawer = $('#drawer');
  if (drawer?.open) drawer.close();
  state.drawerTrigger?.focus({ preventScroll: true });
}
async function openMessage(id, trigger) {
  const revision = ++state.drawerRevision;
  const db = state.db;
  state.drawerTrigger = trigger;
  const drawer = $('#drawer'), content = $('#drawer-content');
  if (!drawer || !content) return;
  drawer.removeAttribute('aria-labelledby');
  drawer.setAttribute('aria-label', 'Chi tiết tin nhắn');
  content.innerHTML = '<div class="empty-state" role="status">Đang tải chi tiết tin nhắn…</div>';
  if (!drawer.open) drawer.showModal();
  try {
    const data = await api(`messages/${encodeURIComponent(id)}`);
    if (revision !== state.drawerRevision || db !== state.db || !drawer.open) return;
    const message = data.message || {};
    const url = externalUrl(message.url);
    content.innerHTML = `<div class="section-stack"><div><p class="section-heading">Tin nhắn #${esc(message.id || id)}</p><h2 id="message-detail-title">${esc(message.channel || message.username || 'Chi tiết nội dung')}</h2><div class="quick-actions">${demoBadge(message.namespace)}${labelBadge(message.label, message.classification_stale ? 'stale' : message.classification_status)}</div></div><div class="detail-text">${esc(message.text || '(Không có nội dung chữ)')}</div><dl class="meta-list"><dt>Username</dt><dd>${esc(message.username || '—')}</dd><dt>Đăng lúc</dt><dd>${esc(date(message.posted_at))}</dd><dt>Thu thập lúc</dt><dd>${esc(date(message.collected_at))}</dd><dt>Telegram message ID</dt><dd>${esc(message.message_id ?? '—')}</dd><dt>Heuristic (không phải Jev)</dt><dd>${number(message.score)} · ${esc(message.level || 'Chưa chấm')} · ${esc(message.heuristic_category || '')}</dd><dt>Mô hình Jev</dt><dd>${esc(message.classification_model || 'Chưa có')}</dd></dl>${url ? `<a class="btn btn--secondary" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${icon('external', 16)} Mở tin nhắn gốc</a>` : ''}${panel('Lịch sử phân loại Jev', 'Bản ghi từ cơ sở dữ liệu, không phải kết luận danh tính.', list(data.classifications).length ? list(data.classifications).map((classification) => `<div class="detail-block">${statusBadge(classification.status)} ${labelBadge(classification.label, classification.status)}<pre class="detail-text">${esc(pretty(classification))}</pre></div>`).join('') : '<p class="muted">Chưa có lịch sử phân loại.</p>')}${panel('Phân tích heuristic', 'Quy tắc và mô hình được giữ riêng.', `<pre class="detail-text">${esc(pretty(data.analyses || []))}</pre>`)}${data.entity ? panel('Nguồn đã lưu', '', `<pre class="detail-text">${esc(pretty(data.entity))}</pre>`) : ''}</div>`;
    drawer.setAttribute('aria-labelledby', 'message-detail-title');
  } catch (error) { if (revision === state.drawerRevision) content.innerHTML = `<div role="alert">${info(error.message)}</div>`; }
}
function values(form) {
  const result = Object.fromEntries(new FormData(form));
  $$('input[type="checkbox"][name]', form).forEach((input) => { result[input.name] = input.checked; });
  $$('input[type="number"][name]', form).forEach((input) => { result[input.name] = Number(input.value); });
  return result;
}
function lines(value) { return String(value || '').split(/[\n,]+/).map((item) => item.trim()).filter(Boolean); }
function formError(form, message) {
  const target = $('[data-form-error]', form);
  if (target) { target.hidden = false; target.textContent = message; }
}
function confirmJob(kind, params) {
  const costs = params.jev || ['classify', 'video_classify'].includes(kind) || (kind === 'video_analyze' && !params.skip_vision);
  let budget = '';
  if (params.jev) budget = `\nNgân sách Jev: tối đa ${params.jev_limit} yêu cầu.`;
  if (kind === 'classify') budget = `\nNgân sách: tối đa ${params.limit} yêu cầu Jev.`;
  if (kind === 'video_classify') budget = '\nNgân sách: 1 yêu cầu Jev.';
  if (kind === 'video_analyze' && !params.skip_vision) budget = `\nNgân sách thị giác: tối đa ${params.max_frames} khung hình.`;
  const network = ['collect', 'watch', 'lookup', 'search', 'download'].includes(kind);
  if (!costs && !network && kind !== 'db_backup') return true;
  return window.confirm(`${JOB_NAMES[kind] || 'Bắt đầu tác vụ'}?\n${kind === 'db_backup' ? 'Máy chủ sẽ tạo một file sao lưu mới của cơ sở dữ liệu đang chọn.' : network ? 'Tác vụ sẽ truy cập mạng và ghi kết quả vào dữ liệu cục bộ.' : 'Nội dung đã lưu sẽ được gửi đến nhà cung cấp mô hình.'}${costs ? kind === 'video_analyze' ? '\nKhung hình chỉ được gửi đến endpoint thị giác cục bộ đã cấu hình; backend không cho phép endpoint từ xa. Dịch vụ mô hình vẫn có thể tính phí.' : '\nNội dung có thể được gửi đến mô hình bên ngoài và có thể phát sinh chi phí.' : ''}${budget}\nChỉ tiếp tục nếu bạn có quyền xử lý dữ liệu này.`);
}
async function createJob(kind, params) {
  const response = await api('jobs', { method: 'POST', db: false, body: { kind, db: state.db, params } });
  if (!response.job?.id) throw new ApiError('Máy chủ chưa xác nhận mã tác vụ. Hãy kiểm tra danh sách tác vụ trước khi gửi lại.', 0, 'missing_job');
  state.jobs = [response.job, ...state.jobs.filter((job) => String(job.id) !== String(response.job.id))];
  toast(`Đã tiếp nhận tác vụ #${response.job.id}. Kết quả chỉ có sau khi tiến trình hoàn tất.`);
  await navigate('jobs');
  schedulePoll();
}
async function submitForm(event) {
  const form = event.target.closest('form[data-form]');
  if (!form) return;
  event.preventDefault();
  if (form.dataset.busy === 'true' || !form.reportValidity()) return;
  const kind = form.dataset.form;
  const params = values(form);
  if (kind === 'messages-filter' || kind === 'profiles-filter') {
    Object.keys(params).forEach((key) => { if (params[key] === '') delete params[key]; });
    await navigate(kind === 'messages-filter' ? 'messages' : 'profiles', params); return;
  }
  const errorNode = $('[data-form-error]', form);
  if (errorNode) { errorNode.hidden = true; errorNode.textContent = ''; }
  if (kind === 'settings') {
    const input = $('[name="jev_key"]', form);
    const key = String(params.jev_key || '').trim();
    if (!key) { formError(form, 'Nhập khóa API Jev.'); return; }
    input.value = '';
    if (!window.confirm('Gửi khóa Jev đến máy chủ dashboard cục bộ cho phiên này? Khóa được giữ trong bộ nhớ, không lưu xuống đĩa.')) return;
    params.jev_key = key;
  } else {
    if (['collect', 'watch'].includes(kind)) {
      params.channels = String(params.channels || '').trim();
      if (!lines(params.channels).length) { formError(form, 'Nhập ít nhất một kênh hoặc nhóm công khai.'); return; }
      if (kind === 'watch') { delete params.limit; delete params.backend; params.once = true; }
    }
    if (kind === 'download') {
      let url;
      try { url = new URL(params.url); } catch { formError(form, 'Nhập một liên kết HTTPS Telegram hợp lệ.'); return; }
      if (url.protocol !== 'https:' || !['t.me', 'telegram.me'].includes(url.hostname) || url.username || url.password) { formError(form, 'Chỉ chấp nhận liên kết HTTPS từ t.me hoặc telegram.me.'); return; }
    }
    if (kind === 'video_classify') params.analysis_id = Number(params.analysis_id);
    if (!confirmJob(kind, params)) return;
  }
  form.dataset.busy = 'true';
  const submit = $('button[type="submit"]', form);
  if (submit) { submit.disabled = true; submit.setAttribute('aria-busy', 'true'); }
  const status = $('[data-form-status]', form);
  if (status) status.textContent = 'Đang gửi yêu cầu…';
  try {
    if (kind === 'settings') {
      await api('settings', { method: 'POST', db: false, body: { jev_key: params.jev_key } });
      params.jev_key = '';
      await bootstrap();
      toast('Máy chủ đã tiếp nhận cấu hình khóa cho phiên này. Chưa kiểm tra hiệu lực với nhà cung cấp.');
      await loadView();
    } else await createJob(kind, params);
  } catch (error) { params.jev_key = ''; formError(form, error.message); if (status) status.textContent = ''; toast(error.message, true); }
  finally { delete form.dataset.busy; if (submit) { submit.disabled = false; submit.removeAttribute('aria-busy'); } }
}
function syncFormOptions() {
  for (const prefix of ['collect', 'watch']) {
    const form = $(`form[data-form="${prefix}"]`);
    if (!form) continue;
    const enabled = $('[name="jev"]', form)?.checked;
    const budgetInput = $('[name="jev_limit"]', form);
    if (budgetInput) budgetInput.disabled = !enabled;
    if (prefix === 'collect') {
      const session = $('[name="session"]', form);
      if (session) session.disabled = $('[name="backend"]', form)?.value !== 'api';
    }
    const budget = $('[name="jev_limit"]', form)?.value || 10;
    const note = $(`[data-budget="${prefix}"]`, form);
    if (note) note.textContent = enabled ? `Ngân sách Jev: tối đa ${number(budget)} yêu cầu trong lần chạy này. Có thể phát sinh chi phí.` : 'Jev đang tắt. Không gửi yêu cầu phân loại mô hình trong tác vụ này.';
  }
}
async function handleAction(event) {
  const nav = event.target.closest('[data-view]');
  if (nav && !nav.closest('#view-root')) { event.preventDefault(); await navigate(nav.dataset.view); return; }
  const target = event.target.closest('[data-action]');
  if (!target || target.disabled || target.tagName === 'SELECT') return;
  const action = target.dataset.action;
  try {
    if (Object.hasOwn(VIEWS, action)) { await navigate(action); return; }
    if (action === 'collect') { await navigate('collection'); return; }
    if (action === 'retry') { await refresh(); return; }
    if (action === 'message') { await openMessage(target.dataset.id, target); return; }
    if (action === 'source-messages') { await navigate('messages', { username: target.dataset.username }); return; }
    if (action === 'label-filter' || action === 'page') {
      const params = Object.fromEntries(state.params);
      if (action === 'label-filter') { if (target.dataset.label) params.label = target.dataset.label; else delete params.label; params.offset = 0; }
      else params.offset = target.dataset.offset;
      await navigate('messages', params); return;
    }
    if (action === 'job-log') {
      const id = target.dataset.id;
      if (state.expandedJobs.has(id)) state.expandedJobs.delete(id);
      else { const data = await api(`jobs/${encodeURIComponent(id)}`, { db: false }); state.expandedJobs.set(id, data.job || data); }
      if (state.view === 'jobs') { $('#view-root').innerHTML = jobsMarkup(); $$('[data-action="job-log"]').find((node) => node.dataset.id === id)?.focus({ preventScroll: true }); }
      return;
    }
    if (action === 'cancel-job') {
      if (!window.confirm(`Yêu cầu dừng tác vụ #${target.dataset.id}? Kết quả đã ghi trước khi dừng vẫn được giữ lại.`)) return;
      target.disabled = true;
      await api(`jobs/${encodeURIComponent(target.dataset.id)}/cancel`, { method: 'POST', db: false, body: {} });
      toast('Máy chủ đã tiếp nhận yêu cầu dừng. Đang kiểm tra trạng thái tiến trình.');
      await loadView({ quiet: true }); schedulePoll(); return;
    }
    if (action === 'clear-key') {
      if (!window.confirm('Xóa khóa Jev của phiên dashboard hiện tại? Cấu hình môi trường máy chủ, nếu có, do máy chủ quyết định.')) return;
      await api('settings', { method: 'POST', db: false, body: { clear_jev: true } });
      await bootstrap(); await loadView(); toast('Máy chủ đã xử lý yêu cầu xóa khóa phiên.'); return;
    }
    if (action === 'backup' && confirmJob('db_backup', {})) { await createJob('db_backup', {}); }
  } catch (error) { target.disabled = false; toast(error.message, true); }
}
function bindShell() {
  if ($('#global-query')) $('#global-query').maxLength = 256;
  document.addEventListener('click', handleAction);
  document.addEventListener('submit', submitForm);
  document.addEventListener('input', (event) => { if (event.target.closest('form[data-form="collect"],form[data-form="watch"]')) syncFormOptions(); });
  document.addEventListener('change', (event) => {
    const target = event.target;
    if (target.closest('form[data-form="collect"],form[data-form="watch"]')) syncFormOptions();
    if (target.id === 'video-file-select') { state.selectedVideo = target.value; loadView({ quiet: true }); }
    if (target.id === 'saved-video-analysis') navigate('video', { analysis: target.value });
  });
  $('#brand-home')?.addEventListener('click', (event) => { event.preventDefault(); navigate('overview'); });
  $('#refresh-button')?.addEventListener('click', refresh);
  $('#database-select')?.addEventListener('change', async (event) => {
    state.db = event.target.value;
    closeDrawer();
    if ($('#nav-status')) $('#nav-status').textContent = `SQLite · ${state.db}`;
    await loadView();
  });
  $('#global-search')?.addEventListener('submit', (event) => {
    event.preventDefault();
    const query = $('#global-query')?.value.trim() || '';
    navigate('messages', query ? { query } : {});
  });
  $('#mobile-menu')?.addEventListener('click', () => {
    const open = document.body.classList.toggle('nav-open');
    $('#mobile-menu').setAttribute('aria-expanded', String(open));
  });
  $('#drawer-close')?.addEventListener('click', closeDrawer);
  $('#drawer')?.addEventListener('click', (event) => { if (event.target === $('#drawer')) { const bounds = $('#drawer').getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) closeDrawer(); } });
  $('#drawer')?.addEventListener('close', () => { ++state.drawerRevision; state.drawerTrigger?.focus({ preventScroll: true }); });
  window.addEventListener('hashchange', () => { closeDrawer(); loadView(); });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stopPoll();
    else if (state.jobs.some(activeJob)) pollJobs();
  });
  window.addEventListener('pagehide', () => { stopPoll(); state.controller?.abort(); });
}
async function start() {
  bindShell();
  window.dashboard = Object.freeze({ navigate, refresh });
  await loadView();
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
else start();
