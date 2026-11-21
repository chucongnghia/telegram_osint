/** Isolated client contract tests: built-in Node APIs only; no browser or network. */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../src/telegram_demo/dashboard/app.js', import.meta.url), 'utf8');
const nodes = new Map();
const requests = [];
const timers = new Map();
let nextTimer = 0;
let responseStatus = 200;
let responseError = 'Fixture failure';
let confirmed = true;
let confirmations = 0;

function element() {
  const attrs = new Map();
  return {
    innerHTML: '', textContent: '', value: '', dataset: {}, hidden: false,
    classList: { toggle() {}, add() {}, remove() {} },
    setAttribute(name, value) { attrs.set(name, value); },
    removeAttribute(name) { attrs.delete(name); },
    getAttribute(name) { return attrs.get(name); },
    addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
  };
}
const document = {
  readyState: 'loading', hidden: false, body: element(), addEventListener() {},
  querySelector(selector) {
    if (!nodes.has(selector)) nodes.set(selector, element());
    return nodes.get(selector);
  },
  querySelectorAll() { return []; },
};
const hostile = '\"><script id=pwn>alert(1)</script><img src=x onerror=alert(1)>';
const message = {
  id: 1, channel: hostile, username: 'fixture', text: hostile, namespace: 'demo',
  label: 'criticism', classification_status: 'success', classification_stale: true,
  classification_model: hostile, score: 29, level: 'High-risk', url: 'javascript:alert(1)',
};
const fixtures = {
  bootstrap: {
    token: 'fixture-dashboard-token', databases: [{ name: 'fixture.db', label: hostile }],
    active_database: 'fixture.db', config: { jev_configured: false, sessions: [] }, jobs: [],
  },
  overview: {
    counts: { messages: 1, entities: 1, classified: 0, flagged: 0 },
    daily: [{ day: '2026-11-17', count: 1 }],
    sources: [{ title: hostile, username: 'fixture', message_count: 1 }],
    recent_messages: [message], recent_runs: [{ source: hostile, backend: 'web' }],
  },
  messages: { items: [message], total: 1, offset: 0 },
  sources: { items: [{ title: hostile, username: 'fixture', kind: hostile, message_count: 1 }], total: 1 },
  profiles: { items: [{ title: hostile, bio: hostile, common_groups: [hostile], phone: hostile }], total: 1 },
  images: {
    files: [{ path: 'poster.png', name: hostile, size: 10 }],
    analyses: [{ id: 1, status: 'partial', source_path: hostile, source_sha256: hostile,
      source_url: 'https://evil.invalid/api/media?path=x',
      ocr: { status: 'success', provider: 'tesseract', version: hostile, languages: ['vie', 'eng'], text: hostile,
        words: [{ text: hostile, confidence: 91.2, bbox: { left: 2, top: 3, width: 4, height: 5 } }], limitations: [hostile] },
      vision: { status: 'skipped' }, evidence: { gaps: ['vision_skipped', hostile] } }],
    classifications: [{ id: 2, image_analysis_id: 1, status: 'error', error_code: hostile }],
  },
  videos: {
    files: [{ path: 'clip.mp4', name: hostile, size: 1 }],
    analyses: [{ id: 1, status: 'partial', source_path: hostile, transcript: null, vision: null,
      local_ocr: { status: 'success', provider: 'tesseract', text: hostile, frames: [{ timestamp: 1.2, text: hostile, words: [{ text: hostile }] }] },
      evidence: hostile, frames: [{ url: 'https://evil.invalid/api/media?path=x', timestamp: hostile }] }],
    classifications: [{ id: 2, video_analysis_id: 1, status: 'error', error_code: hostile }],
  },
  jobs: { items: [{ id: 'saved', kind: 'collect', status: 'failed', logs: [hostile], params: { channels: hostile } }] },
  'search-results': { items: [{ run_id: 9, username: 'from-search', title: 'Từ khóa', kind: 'channel', telegram_id: 900, keywords: ['k'] }], total: 1 },
};
const location = { origin: 'http://127.0.0.1:9999', hash: '#overview' };
const context = vm.createContext({
  document, location, URL, URLSearchParams, AbortController, Intl, console,
  window: { addEventListener() {}, confirm() { confirmations += 1; return confirmed; } },
  FormData: class { constructor(form) { return form.entries; } },
  setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, { callback, delay }); return id; },
  clearTimeout(id) { timers.delete(id); },
  fetch: async (url, options) => {
    requests.push({ url: new URL(url), options });
    const key = new URL(url).pathname.split('/')[2];
    const payload = options.method === 'POST' ? { job: { id: 'new-job', status: 'queued', kind: 'collect' } } : fixtures[key];
    return {
      ok: responseStatus === 200, status: responseStatus,
      json: async () => responseStatus === 200 ? payload : { error: responseError, code: 'fixture_error' },
    };
  },
});
// Remove module declaration keywords only in the isolated copy. Production source is unchanged.
vm.runInContext(source.replace(/^export (?=(?:async )?function)/gm, '') + `
  globalThis.clientTest = {
    state, api, showPageError, renderOverview, renderMessages, renderSources,
    renderCollection, renderClassify, renderProfiles, renderVideo, renderImage, ocrDetails, confirmJob, renderJobs,
    renderSettings, externalUrl, mediaUrl, safeMediaUrl, labelBadge, messageRows,
    jobDetail, jobParamsShort, jobsMarkup, submitForm, schedulePoll, stopPoll, sourceDisplay,
    syncFormOptions, syncVisionForm, handleAction, sourceCollectHandle, sourceCollectParams, sourcesRows,
    addCollectSources, applyCollectLimit, collectLimit, sourceKindLabel, startSelectedCollection,
    telegramApiPanel, telegramApiStatus, loadSourceResults, jobResultTarget, firstChannel,
  };`, context, { filename: 'dashboard/app.js' });
const client = context.clientTest;
client.state.bootstrap = fixtures.bootstrap;
client.state.db = 'fixture.db';

const rendered = {};
for (const name of ['Overview', 'Messages', 'Sources', 'Collection', 'Classify', 'Profiles', 'Video', 'Image', 'Jobs', 'Settings']) {
  const html = await client[`render${name}`]();
  rendered[name] = html;
  assert.equal(typeof html, 'string', `${name} renders HTML`);
  assert(!html.includes('<script id=pwn>'), `${name} escapes malicious markup`);
  const attributeNamesOnly = html.replace(/"[^"]*"|'[^']*'/g, '""');
  assert(!/<[^>]*\son(?:click|error)\s*=/i.test(attributeNamesOnly), `${name} has no inline event attributes`);
  assert(!html.includes('https://evil.invalid'), `${name} rejects external media`);
  assert(!html.includes('scam_indicators'), `${name} does not expose the legacy scam label key`);
  console.log(`PASS render ${name}`);
}
assert(rendered.Messages.includes('class="data-table message-table"'), 'message table exposes mobile-specific class');
const newLabels = [
  'Kêu gọi bạo lực',
  'Kêu gọi phá hoại tài sản',
  'Chống người thi hành công vụ',
  'Kêu gọi chống chính quyền',
  'Đe doạ',
  'Phê bình / chính kiến (không phải kêu gọi)',
  'Không đủ thông tin',
];
const labelSelectHtml = rendered.Messages.match(/<select id="messages-label"[^>]*>([\s\S]*?)<\/select>/)?.[1] || '';
const labelTabsHtml = rendered.Messages.match(/<div class="filter-tabs" aria-label="Bộ lọc nhãn Jev">([\s\S]*?)<\/div>/)?.[1] || '';
assert(labelSelectHtml && labelTabsHtml, 'Jev label filter controls render');
for (const label of newLabels) {
  assert(labelSelectHtml.includes(label), `Jev label select contains ${label}`);
  assert(labelTabsHtml.includes(label), `Jev label tabs contain ${label}`);
}
assert(!labelSelectHtml.includes('scam_indicators') && !labelTabsHtml.includes('scam_indicators'),
  'legacy scam label is absent from the Jev label filters');
assert(rendered.Overview.includes('Kết quả Jev') && rendered.Overview.includes('Cần xem xét'),
  'overview uses the new Jev card labels');
assert(!rendered.Overview.includes('Dấu hiệu lừa đảo'), 'overview no longer shows the old scam card');
console.log('PASS Jev label filters expose all seven incitement labels');
assert.deepEqual([...client.messageRows([message]).matchAll(/<td\b[^>]*data-label="([^"]+)"/g)].map(match => match[1]),
  ['Nguồn', 'Nội dung', 'Jev', 'Heuristic', 'Thời gian', 'Chi tiết']);
for (const [value, expected] of [
  ['/home/kali/recon/demo/data/telegram.sqlite', 'telegram.sqlite'],
  ['C:\\Users\\local\\runs.json', 'runs.json'],
  ['./data/runs.json', 'runs.json'],
  ['data/runs.json', 'runs.json'],
  ['Tin tức / cảnh báo', 'Tin tức / cảnh báo'],
  ['https://t.me/example/1', 'https://t.me/example/1'],
  ['web', 'web'],
]) assert.equal(client.sourceDisplay(value), expected);
const originalRuns = fixtures.overview.recent_runs;
fixtures.overview.recent_runs = [{ source: '/home/kali/private/runs.sqlite', backend: 'web' }, { source: 'Tin tức / cảnh báo' }];
const compactRuns = await client.renderOverview();
assert(compactRuns.includes('runs.sqlite'));
assert(!compactRuns.includes('/home/kali/private/'));
assert(compactRuns.includes('Tin tức / cảnh báo'));
fixtures.overview.recent_runs = originalRuns;
console.log('PASS mobile message cell labels and compact filesystem source names');
const allowedJobFields = {
  collect: ['backend', 'channels', 'limit', 'session', 'jev', 'jev_limit', 'demo'],
  watch: ['channels', 'session', 'jev', 'jev_limit', 'minutes', 'cycles', 'once', 'infinite'],
  classify: ['limit', 'query', 'username', 'include_demo', 'recheck'],
  lookup: ['username', 'limit', 'backend', 'session'],
  search: ['keyword', 'limit', 'backend', 'session'],
  'collect-search': ['keyword', 'limit', 'backend', 'session'],
  download: ['url', 'session'],
  video_analyze: ['path', 'skip_vision', 'language', 'model', 'max_frames', 'ocr', 'ocr_language', 'allow_remote'],
  image_analyze: ['path', 'ocr_language', 'vision', 'allow_remote'],
  image_classify: ['image_id', 'allow_partial', 'recheck'],
  video_classify: ['analysis_id', 'allow_partial', 'recheck'],
};
for (const html of Object.values(rendered)) {
  for (const [, kind, contents] of html.matchAll(/<form\b[^>]*data-form="([^"]+)"[^>]*>([\s\S]*?)<\/form>/g)) {
    if (!allowedJobFields[kind]) continue;
    for (const [, name] of contents.matchAll(/<(?:input|select|textarea)\b[^>]*\bname="([^"]+)"/g)) {
      assert(allowedJobFields[kind].includes(name), `${kind} renders only backend-supported parameter ${name}`);
    }
  }
}
function control(html, id) {
  const match = html.match(new RegExp(`<(?:input|select|textarea)\\b[^>]*\\bid="${id}"[^>]*>`));
  assert(match, `rendered control ${id} exists`);
  return match[0];
}
assert(!rendered.Collection.includes('id="watch-limit"'), 'watch has no history limit control');
for (const id of ['watch-budget']) assert(control(rendered.Collection, id).includes('max="1000"'));
assert(control(rendered.Classify, 'classify-limit').includes('max="100"'));
assert(control(rendered.Collection, 'watch-minutes').includes('max="1440"'));
assert(control(rendered.Collection, 'watch-cycles').includes('max="1000"'));
assert(control(rendered.Collection, 'watch-cycles').includes('min="0"'));
assert(control(rendered.Collection, 'watch-infinite').includes('name="infinite"'));
assert(!control(rendered.Collection, 'watch-budget').includes('max="100"'), 'watch Jev budget allows 1000');
assert(control(rendered.Collection, 'collect-global-limit').includes('max="1000"'));
assert(control(rendered.Video, 'video-max-frames').includes('max="12"'));
assert(rendered.Video.includes('Tối đa 12 khung hình'));
assert(!rendered.Collection.includes('https://t.me/kenh_khac'));
for (const [view, id] of [['Messages', 'messages-query'], ['Classify', 'classify-query'], ['Profiles', 'profiles-filter-query']]) {
  assert(control(rendered[view], id).includes('maxlength="256"'), `${id} respects backend text cap`);
}
const language = rendered.Video.match(/<select id="video-language"[^>]*>([\s\S]*?)<\/select>/);
assert(language, 'language uses constrained native select');
assert.deepEqual([...language[1].matchAll(/<option value="([^"]+)"/g)].map(match => match[1]), ['vi', 'en', 'auto']);
for (const [view, prefix] of [['Image', 'image'], ['Video', 'video']]) {
  const ocrLanguage = rendered[view].match(new RegExp(`<select id="${prefix}-ocr-language"[^>]*>([\\s\\S]*?)<\\/select>`));
  assert(ocrLanguage, `${view} has an OCR language select`);
  assert.deepEqual([...ocrLanguage[1].matchAll(/<option value="([^"]+)"/g)].map(match => match[1]), ['vie+eng', 'vie', 'eng']);
  assert(ocrLanguage[1].includes('value="vie+eng" selected'), 'bilingual OCR is default');
}
assert(control(rendered.Video, 'video-ocr').includes('checked'), 'new dashboard video jobs default OCR ON');
assert(!control(rendered.Image, 'image-vision').includes('checked'), 'semantic image vision remains explicit opt-in');
assert(rendered.Image.includes('class="image-preview"'));
assert(rendered.Image.includes('tesseract'), 'OCR engine provenance renders');
assert(!rendered.Image.includes('Vị trí pixel') && !rendered.Image.includes('"left"'),
  'raw word boxes with pixel coordinates are not rendered');
assert(rendered.Image.includes('Tesseract chỉ nhận diện chữ'));
assert(rendered.Image.includes('vision_skipped'));
assert(rendered.Video.includes('Khung hình tại 1.2 giây'));
assert(client.ocrDetails(null).includes('không đồng nghĩa nội dung an toàn'));
assert(client.ocrDetails({ status: 'success', text: '', words: [] }).includes('không có chữ hay nội dung an toàn'));
console.log('PASS image/video OCR details, partial gaps, defaults, field allowlists, and constrained languages');
assert(client.messageRows([message]).includes('&lt;script id=pwn&gt;'));
assert(client.messageRows([message]).includes('Kết quả cũ'));
assert(client.messageRows([message]).includes('Rủi ro cao'), 'heuristic level is translated for display');
assert(!client.messageRows([message]).includes('scam_indicators'));
assert(!client.labelBadge('scam_indicators', 'success').includes('scam_indicators'), 'legacy label keys do not leak');
assert(client.labelBadge('criticism', 'success').includes('Phê bình / chính kiến (không phải kêu gọi)'),
  'criticism label renders with its Vietnamese name');
assert(client.messageRows([{ ...message, classification_stale: false }]).includes('Phê bình / chính kiến (không phải kêu gọi)'),
  'criticism badge appears in message rows');
assert(client.labelBadge(null, 'error').includes('Lỗi phân loại'));
assert(client.labelBadge(null).includes('Chưa phân loại'));
assert(!client.labelBadge(null).includes('an toàn'));
client.state.expandedJobs.set('saved', fixtures.jobs.items[0]);
const hostileJob = fixtures.jobs.items[0];
client.state.expandedJobs.set(String(hostileJob.id), { logs: Array.isArray(hostileJob.logs) ? hostileJob.logs : ['[!] <script id=pwn>'] });
assert(client.jobDetail(hostileJob).includes('&lt;script id=pwn&gt;'));
assert(!client.jobDetail(hostileJob).includes('<script id=pwn>'));
client.state.expandedJobs.clear();
assert.equal(client.externalUrl('javascript:alert(1)'), '');
assert.equal(client.externalUrl('data:text/html,hello'), '');
assert.equal(client.externalUrl('https://t.me/example/1'), 'https://t.me/example/1');
assert.equal(client.safeMediaUrl('https://evil.invalid/api/media?path=x'), '');
assert.equal(client.safeMediaUrl('/other?path=x'), '');
assert.equal(client.mediaUrl('../private'), '');
assert.equal(client.mediaUrl('/etc/passwd'), '');
assert(client.safeMediaUrl('/api/media?path=clip.mp4').startsWith(location.origin));
assert.equal(client.mediaUrl('clip "&.mp4'), '/api/media?path=clip%20%22%26.mp4');
console.log('PASS malicious DB fields, stale labels, job logs, and external/media URL restrictions');

for (const path of ['jobs', 'settings', 'jobs/fixture/cancel']) {
  await client.api(path, { method: 'POST', body: {} });
  assert.equal(requests.at(-1).options.headers['X-Dashboard-Token'], 'fixture-dashboard-token');
  assert.equal(requests.at(-1).url.search, '', 'POST endpoints reject every query parameter');
}
await client.api('messages?query=fixture');
assert.equal(requests.at(-1).url.searchParams.get('db'), 'fixture.db', 'GET data endpoints use selected database');
assert.equal(requests.at(-1).url.searchParams.get('query'), 'fixture');
const savedBootstrap = client.state.bootstrap;
client.state.bootstrap = null;
await assert.rejects(() => client.api('jobs', { method: 'POST', body: {} }), error => error.status === 403);
client.state.bootstrap = savedBootstrap;
for (const status of [401, 403, 500]) {
  responseStatus = status;
  responseError = hostile;
  await assert.rejects(() => client.api('overview'), error => {
    assert.equal(error.status, status);
    assert(error.message.includes(hostile));
    client.showPageError(error);
    const html = document.querySelector('#view-root').innerHTML;
    assert(html.includes('role="alert"'));
    assert(!html.includes('<script id=pwn>'));
    return true;
  });
  console.log(`PASS visible escaped API error ${status}`);
}
responseStatus = 200;
console.log('PASS POST token enforcement and selected database');

function form(kind, entries, extra = {}) {
  const error = element(), status = element(), submit = element();
  return {
    entries, dataset: { form: kind }, reportValidity() { return true; },
    closest() { return this; },
    querySelector(selector) {
      if (selector === '[data-form-error]') return error;
      if (selector === '[data-form-status]') return status;
      if (selector === 'button[type="submit"]') return submit;
      return extra[selector] || null;
    },
    querySelectorAll() { return []; },
  };
}
const watch = form('watch', [['channels', '@fixture\n@second'], ['cycles', 3], ['minutes', 5], ['limit', 10], ['backend', 'web']]);
await client.submitForm({ target: watch, preventDefault() {} });
const created = requests.findLast(request => request.options.method === 'POST' && request.options.body.includes('"kind":"watch"'));
assert(created, 'watch sends an actual job request');
const body = JSON.parse(created.options.body);
assert.equal(created.url.search, '', 'job creation has no query parameters');
assert.equal(body.db, 'fixture.db', 'job creation sends selected database in JSON body');
assert.equal(body.params.channels, '@fixture\n@second');
assert.equal(body.params.once, true, 'watch defaults to a finite window');
assert.equal(body.params.minutes, 5, 'finite window is in minutes');
assert(!Object.hasOwn(body.params, 'limit'), 'watch drops unsupported history limit defensively');
assert(!Object.hasOwn(body.params, 'backend'), 'watch lets backend choose API internally');
for (const name of Object.keys(body.params)) assert(allowedJobFields.watch.includes(name), `watch payload supports ${name}`);
assert.equal(body.params.cycles, 3, 'cycles remains max-new-message cap');
const infiniteWatch = form('watch', [['channels', '@fixture'], ['cycles', 0], ['minutes', 5]], { '[name="infinite"]': { checked: true } });
await client.submitForm({ target: infiniteWatch, preventDefault() {} });
const infiniteBody = JSON.parse(requests.findLast(request => request.options.method === 'POST' && request.options.body.includes('"kind":"watch"')).options.body);
assert.equal(infiniteBody.params.once, false, 'infinite watch drops --watch-once');
assert(!Object.hasOwn(infiniteBody.params, 'minutes'), 'infinite watch sends no window');
assert.equal(infiniteBody.params.cycles, 0, 'cycles 0 means unlimited new messages');
assert(confirmations > 0, 'network job requires browser confirmation');
const mediaPosts = () => requests.filter((request) => request.options.method === 'POST' && /"kind":"(?:image|video)_/.test(request.options.body));
const submit = (item) => client.submitForm({ target: item, preventDefault() {} });
let mediaCount = mediaPosts().length;
const localOcr = form('image_analyze', [['path', 'poster.png'], ['ocr_language', 'vie+eng'], ['vision', false]]);
const confirmsBeforeOcr = confirmations;
await submit(localOcr);
assert.equal(mediaPosts().length, ++mediaCount);
assert.equal(confirmations, confirmsBeforeOcr, 'local OCR does not trigger remote-model confirmation');
assert.deepEqual(JSON.parse(mediaPosts().at(-1).options.body).params, { path: 'poster.png', ocr_language: 'vie+eng', vision: false });
await submit(form('image_classify', [['image_id', '1'], ['allow_partial', true], ['recheck', false]]));
assert.equal(mediaPosts().length, mediaCount, 'missing Jev key blocks classification before send');
client.state.bootstrap.config.jev_configured = true;
const partial = form('image_classify', [['image_id', '1'], ['allow_partial', false], ['recheck', false]]);
await submit(partial);
assert.equal(mediaPosts().length, mediaCount, 'partial evidence requires explicit consent');
assert(partial.querySelector('[data-form-error]').textContent.includes('chấp nhận'));
confirmed = false;
await submit(form('image_classify', [['image_id', '1'], ['allow_partial', true], ['recheck', false]]));
assert.equal(mediaPosts().length, mediaCount, 'cancelled model confirmation sends no classification');
confirmed = true;
await submit(form('image_classify', [['image_id', '1'], ['allow_partial', true], ['recheck', false]]));
assert.equal(mediaPosts().length, ++mediaCount);
assert.deepEqual(JSON.parse(mediaPosts().at(-1).options.body).params, { image_id: 1, allow_partial: true, recheck: false });
await submit(form('video_analyze', [['path', 'clip.mp4'], ['language', 'auto'], ['model', 'tiny'], ['max_frames', 6], ['skip_vision', true], ['ocr', true], ['ocr_language', 'eng']]));
assert.equal(mediaPosts().length, ++mediaCount);
assert.equal(JSON.parse(mediaPosts().at(-1).options.body).params.ocr, true);
assert.equal(JSON.parse(mediaPosts().at(-1).options.body).params.language, 'auto');
fixtures.images.analyses[0].status = 'ready';
await submit(form('image_classify', [['image_id', '1'], ['allow_partial', false], ['recheck', false]]));
assert.equal(mediaPosts().length, ++mediaCount, 'complete ready evidence does not require partial consent');
fixtures.images.analyses[0].status = 'partial';
client.state.bootstrap.config.jev_configured = false;
console.log('PASS local image OCR payload and separate keyed/consented/confirmed Jev and video OCR jobs');
const password = element();
password.value = 'fixture-secret';
const keyForm = form('settings', [['jev_key', 'fixture-secret']], { '[name="jev_key"]': password });
confirmed = false;
const requestCount = requests.length;
await client.submitForm({ target: keyForm, preventDefault() {} });
assert.equal(password.value, '', 'password is cleared even when confirmation is cancelled');
assert.equal(requests.length, requestCount, 'cancelled confirmation sends no key');
assert(!/\blocalStorage\s*(?:\.\s*(?:setItem|getItem|removeItem|clear)\b|\[)/.test(source), 'client never persists secrets in localStorage');
console.log('PASS finite watch job payload, confirmation, and cleared password');

// Vision settings: fields, labels, placement, status rows, and provider behavior.
const visionSettings = rendered.Settings;
assert(visionSettings.includes('<form data-form="vision">'), 'settings renders a vision form');
assert(visionSettings.indexOf('Xóa khóa phiên') < visionSettings.indexOf('data-form="vision"'), 'vision panel sits below the Jev key panel');
assert(visionSettings.indexOf('data-form="vision"') < visionSettings.indexOf('Dữ liệu và sao lưu'), 'vision panel sits above backups');
assert(control(rendered.Image, 'image-vision').includes('disabled'), 'unconfigured vision checkbox is disabled');
const visionProviderSelect = visionSettings.match(/<select id="vision-provider"[^>]*>([\s\S]*?)<\/select>/);
assert(visionProviderSelect, 'vision provider select exists');
assert.deepEqual([...visionProviderSelect[1].matchAll(/<option value="([^"]+)"/g)].map((match) => match[1]), ['gemini', 'openai']);
assert(visionProviderSelect[1].includes('value="gemini" selected'), 'gemini is the default provider');
assert(visionSettings.includes('Gemini API') && visionSettings.includes('OpenAI-compatible / Qwen-VL cục bộ'));
const visionModelTag = control(visionSettings, 'vision-model');
assert(visionModelTag.includes('name="vision_model"') && visionModelTag.includes('required'), 'model is required');
assert(visionModelTag.includes('placeholder="vd: gemini-2.5-flash"'));
const visionKeyTag = control(visionSettings, 'vision-key');
assert(visionKeyTag.includes('name="vision_key"') && visionKeyTag.includes('type="password"'));
assert(visionKeyTag.includes('autocomplete="off"') && visionKeyTag.includes('spellcheck="false"'));
assert(!visionKeyTag.includes('value='), 'vision key is never prefilled');
assert(visionSettings.includes('file cục bộ 0600') && visionSettings.includes('dashboard_secrets.json'));
const visionUrlTag = control(visionSettings, 'vision-url');
assert(visionUrlTag.includes('name="vision_url"'), 'vision url field exists');
assert(visionUrlTag.includes('placeholder="http://127.0.0.1:11434/v1/chat/completions"'));
assert(visionUrlTag.includes('disabled'), 'gemini disables the endpoint field');
assert(visionSettings.includes('loopback') && visionSettings.includes('HTTPS'));
assert(visionSettings.includes('Lưu cấu hình thị giác'));
const visionFormHtml = visionSettings.match(/<form data-form="vision">([\s\S]*?)<\/form>/)[1];
assert.deepEqual([...visionFormHtml.matchAll(/<(?:input|select|textarea)\b[^>]*\bname="([^"]+)"/g)].map((match) => match[1]).sort(),
  ['vision_key', 'vision_model', 'vision_provider', 'vision_url'], 'vision form sends only settings fields');
assert(visionSettings.includes('data-action="clear-vision"') && visionSettings.includes('Xóa cấu hình thị giác'));
assert(visionSettings.includes('không xác thực khóa'), 'panel warns that saving does not validate the key');
assert(visionSettings.includes('Chưa cấu hình'));
const baseVisionConfig = client.state.bootstrap.config;
client.state.bootstrap.config = { ...baseVisionConfig, vision_configured: true, vision_provider: 'gemini', vision_model: 'gemini-2.5-flash', vision_remote: true, vision_key_set: true };
const remoteGeminiSettings = await client.renderSettings();
assert(remoteGeminiSettings.includes('Gemini · gemini-2.5-flash'), 'gemini status names the model');
assert(remoteGeminiSettings.includes('gửi ảnh ra ngoài, có thể tính phí'), 'remote status warns about upload cost');
assert(remoteGeminiSettings.includes('Đã cấu hình'));
client.state.bootstrap.config = { ...baseVisionConfig, vision_configured: true, vision_provider: 'openai', vision_model: 'qwen2.5-vl', vision_remote: false, vision_key_set: true, vision_endpoint_set: true };
const localOpenaiSettings = await client.renderSettings();
assert(localOpenaiSettings.includes('OpenAI-compatible · qwen2.5-vl'), 'openai status names the model');
assert(!localOpenaiSettings.includes('gửi ảnh ra ngoài'));
assert(!control(localOpenaiSettings, 'vision-url').includes('disabled'), 'openai keeps the endpoint field editable');
client.state.bootstrap.config = baseVisionConfig;
const providerField = element();
providerField.value = 'gemini';
const urlField = element();
urlField.value = 'http://127.0.0.1:11434/v1/chat/completions';
urlField.disabled = false;
const syncableVisionForm = form('vision', [], { '[name="vision_provider"]': providerField, '[name="vision_url"]': urlField });
client.syncVisionForm(syncableVisionForm);
assert.equal(urlField.disabled, true, 'gemini disables the url input');
assert.equal(urlField.value, '', 'gemini clears the url input');
providerField.value = 'openai';
client.syncVisionForm(syncableVisionForm);
assert.equal(urlField.disabled, false, 'openai enables the url input');
const imageFormNode = document.querySelector('form[data-form="image_analyze"]');
const imageConsentNode = element();
const imageVisionNode = { checked: false };
imageFormNode.querySelector = (selector) => selector === '[name="vision"]' ? imageVisionNode : selector === '[name="allow_remote"]' ? imageConsentNode : null;
client.syncFormOptions();
assert.equal(imageConsentNode.disabled, true, 'image consent starts disabled while vision is off');
imageVisionNode.checked = true;
client.syncFormOptions();
assert.equal(imageConsentNode.disabled, false, 'image consent enables with the vision checkbox');
const videoFormNode = document.querySelector('form[data-form="video_analyze"]');
const videoConsentNode = element();
const videoSkipNode = { checked: true };
videoFormNode.querySelector = (selector) => selector === '[name="skip_vision"]' ? videoSkipNode : selector === '[name="allow_remote"]' ? videoConsentNode : null;
client.syncFormOptions();
assert.equal(videoConsentNode.disabled, true, 'video consent is disabled while vision is skipped');
videoSkipNode.checked = false;
client.syncFormOptions();
assert.equal(videoConsentNode.disabled, false, 'video consent enables when vision will run');
imageFormNode.querySelector = () => null;
videoFormNode.querySelector = () => null;
console.log('PASS vision settings fields, labels, provider behavior, and status rows');

// Vision settings POST bodies: gemini and openai variants omit empty fields.
const lastSettingsPost = () => requests.findLast((request) => request.options.method === 'POST' && request.url.pathname === '/api/settings');
const geminiKeyValue = 'AIza-fixture-key-0123456789';
const geminiKeyInput = element();
geminiKeyInput.value = geminiKeyValue;
const geminiVision = form('vision', [
  ['vision_provider', 'gemini'], ['vision_model', ' gemini-2.5-flash '], ['vision_key', ` ${geminiKeyValue} `], ['vision_url', ''],
], { '[name="vision_key"]': geminiKeyInput });
confirmed = true;
const postsBeforeGemini = requests.length;
await submit(geminiVision);
const geminiVisionPost = requests.slice(postsBeforeGemini).find((request) => request.options.method === 'POST' && request.url.pathname === '/api/settings');
assert(geminiVisionPost, 'gemini vision settings are posted');
assert.equal(geminiKeyInput.value, '', 'vision key input is cleared immediately');
assert.deepEqual(JSON.parse(geminiVisionPost.options.body), { vision_provider: 'gemini', vision_model: 'gemini-2.5-flash', vision_key: geminiKeyValue });
assert(!Object.hasOwn(JSON.parse(geminiVisionPost.options.body), 'vision_url'), 'gemini omits the empty url');
await submit(form('vision', [['vision_provider', 'openai'], ['vision_model', 'qwen2.5-vl'], ['vision_key', ''], ['vision_url', ' http://127.0.0.1:11434/v1/chat/completions ']]));
const loopbackVisionBody = JSON.parse(lastSettingsPost().options.body);
assert.deepEqual(loopbackVisionBody, { vision_provider: 'openai', vision_model: 'qwen2.5-vl', vision_url: 'http://127.0.0.1:11434/v1/chat/completions' });
assert(!Object.hasOwn(loopbackVisionBody, 'vision_key'), 'loopback openai may omit the key');
await submit(form('vision', [['vision_provider', 'openai'], ['vision_model', 'qwen2.5-vl'], ['vision_key', 'sk-fixture'], ['vision_url', 'https://vision.example.test/v1/chat/completions']]));
const remoteVisionBody = JSON.parse(lastSettingsPost().options.body);
assert.equal(remoteVisionBody.vision_key, 'sk-fixture');
assert.equal(remoteVisionBody.vision_url, 'https://vision.example.test/v1/chat/completions');
const postsBeforeInvalid = requests.length;
const geminiNoKey = form('vision', [['vision_provider', 'gemini'], ['vision_model', 'gemini-2.5-flash']]);
await submit(geminiNoKey);
assert.equal(requests.length, postsBeforeInvalid, 'gemini without a key is blocked client-side');
assert(geminiNoKey.querySelector('[data-form-error]').textContent.includes('API key'));
const openaiNoUrl = form('vision', [['vision_provider', 'openai'], ['vision_model', 'qwen2.5-vl']]);
await submit(openaiNoUrl);
assert.equal(requests.length, postsBeforeInvalid, 'openai without a url is blocked client-side');
assert(openaiNoUrl.querySelector('[data-form-error]').textContent.includes('URL endpoint'));
const insecureRemote = form('vision', [['vision_provider', 'openai'], ['vision_model', 'qwen2.5-vl'], ['vision_url', 'http://vision.example.test/v1/chat/completions']]);
await submit(insecureRemote);
assert.equal(requests.length, postsBeforeInvalid, 'remote HTTP is blocked client-side');
assert(insecureRemote.querySelector('[data-form-error]').textContent.includes('HTTPS'));
const cancelKeyInput = element();
cancelKeyInput.value = 'cancel-secret';
const cancelVision = form('vision', [['vision_provider', 'gemini'], ['vision_model', 'gemini-2.5-flash'], ['vision_key', 'cancel-secret']], { '[name="vision_key"]': cancelKeyInput });
confirmed = false;
const postsBeforeCancel = requests.length;
await submit(cancelVision);
assert.equal(cancelKeyInput.value, '', 'vision key is cleared even when confirmation is cancelled');
assert.equal(requests.length, postsBeforeCancel, 'cancelled vision confirmation sends nothing');
confirmed = true;
const rejectedKeyInput = element();
rejectedKeyInput.value = 'invalid-secret';
const rejectedVision = form('vision', [['vision_provider', 'gemini'], ['vision_model', 'gemini-2.5-flash'], ['vision_key', 'invalid-secret']], { '[name="vision_key"]': rejectedKeyInput });
responseStatus = 400;
responseError = 'Khóa thị giác không hợp lệ.';
await submit(rejectedVision);
assert.equal(rejectedKeyInput.value, '', 'vision key is cleared after a server rejection');
assert(rejectedVision.querySelector('[data-form-error]').textContent.includes('Khóa thị giác không hợp lệ.'));
responseStatus = 200;
console.log('PASS vision settings POST groups, trimming, validation, and cleared password');

client.state.bootstrap.config = { ...baseVisionConfig, vision_configured: true, vision_provider: 'gemini', vision_model: 'gemini-2.5-flash', vision_remote: true, vision_key_set: true };
const configuredSettings = await client.renderSettings();
assert(!configuredSettings.includes(geminiKeyValue), 'vision key is never rendered');
assert(!configuredSettings.includes('sk-fixture'), 'remote vision key is never rendered');
assert(!requests.some((request) => request.url.href.includes(geminiKeyValue)), 'vision key never appears in a URL');
const remoteImageHtml = await client.renderImage();
assert(remoteImageHtml.includes('name="allow_remote"'), 'remote image form renders consent');
assert(remoteImageHtml.includes('Tôi đồng ý gửi ảnh tới model từ xa (có thể tính phí)'));
assert(control(remoteImageHtml, 'image-allow-remote').includes('disabled'), 'image consent starts disabled');
const remoteVideoHtml = await client.renderVideo();
assert(remoteVideoHtml.includes('Tôi đồng ý gửi khung hình tới model từ xa (có thể tính phí)'));
assert(!control(remoteVideoHtml, 'video-allow-remote').includes('disabled'), 'video consent is enabled when vision runs by default');
assert(!remoteImageHtml.includes(geminiKeyValue));
console.log('PASS vision key never rendered and consent controls render for remote models');

mediaCount = mediaPosts().length;
const imageNeedsConsent = form('image_analyze', [['path', 'poster.png'], ['ocr_language', 'vie+eng'], ['vision', true]]);
await submit(imageNeedsConsent);
assert.equal(mediaPosts().length, mediaCount, 'remote image vision requires consent');
assert(imageNeedsConsent.querySelector('[data-form-error]').textContent.includes('xác nhận'));
await submit(form('image_analyze', [['path', 'poster.png'], ['ocr_language', 'vie+eng'], ['vision', true], ['allow_remote', true]]));
assert.equal(mediaPosts().length, ++mediaCount);
assert.deepEqual(JSON.parse(mediaPosts().at(-1).options.body).params, { path: 'poster.png', ocr_language: 'vie+eng', vision: true, allow_remote: true });
const imageRemoteParams = JSON.parse(mediaPosts().at(-1).options.body).params;
assert(!JSON.stringify(imageRemoteParams).includes(geminiKeyValue), 'vision key never enters job params');
await submit(form('image_analyze', [['path', 'poster.png'], ['ocr_language', 'vie+eng'], ['vision', false], ['allow_remote', true]]));
assert.equal(mediaPosts().length, ++mediaCount);
const localImageParams = JSON.parse(mediaPosts().at(-1).options.body).params;
assert.equal(localImageParams.vision, false);
assert(!Object.hasOwn(localImageParams, 'allow_remote'), 'consent is dropped when image vision is off');
const videoNeedsConsent = form('video_analyze', [['path', 'clip.mp4'], ['skip_vision', false], ['language', 'auto'], ['model', 'tiny'], ['max_frames', 6], ['ocr', true], ['ocr_language', 'eng']]);
await submit(videoNeedsConsent);
assert.equal(mediaPosts().length, mediaCount, 'remote video vision requires consent');
assert(videoNeedsConsent.querySelector('[data-form-error]').textContent.includes('xác nhận'));
await submit(form('video_analyze', [['path', 'clip.mp4'], ['skip_vision', false], ['language', 'auto'], ['model', 'tiny'], ['max_frames', 6], ['ocr', true], ['ocr_language', 'eng'], ['allow_remote', true]]));
assert.equal(mediaPosts().length, ++mediaCount);
assert.deepEqual(JSON.parse(mediaPosts().at(-1).options.body).params, { path: 'clip.mp4', skip_vision: false, language: 'auto', model: 'tiny', max_frames: 6, ocr: true, ocr_language: 'eng', allow_remote: true });
await submit(form('video_analyze', [['path', 'clip.mp4'], ['skip_vision', true], ['language', 'vi'], ['model', 'tiny'], ['max_frames', 6], ['ocr', true], ['ocr_language', 'vie+eng'], ['allow_remote', true]]));
assert.equal(mediaPosts().length, ++mediaCount);
assert(!Object.hasOwn(JSON.parse(mediaPosts().at(-1).options.body).params, 'allow_remote'), 'consent is dropped when video skips vision');
console.log('PASS remote consent gating for image and video jobs');

client.state.view = 'settings';
const clearVisionTarget = { dataset: { action: 'clear-vision' }, disabled: false, tagName: 'BUTTON' };
clearVisionTarget.closest = (selector) => selector === '[data-action]' ? clearVisionTarget : null;
const postsBeforeClear = requests.length;
confirmed = false;
await client.handleAction({ target: clearVisionTarget });
assert.equal(requests.length, postsBeforeClear, 'cancelled clear-vision sends nothing');
confirmed = true;
await client.handleAction({ target: clearVisionTarget });
const clearVisionPost = requests.slice(postsBeforeClear).find((request) => request.options.method === 'POST' && request.options.body.includes('clear_vision'));
assert(clearVisionPost, 'clear-vision posts to settings');
assert.deepEqual(JSON.parse(clearVisionPost.options.body), { clear_vision: true });
assert(requests.slice(requests.indexOf(clearVisionPost)).some((request) => request.options.method === 'GET' && request.url.pathname === '/api/bootstrap'), 'clear-vision refreshes bootstrap');
client.state.bootstrap.config = baseVisionConfig;
console.log('PASS clear-vision action posts only clear_vision and refreshes state');

// Telegram API settings: status, fields, validation, exact POST body, cleared hash.
const telegramConfig = { ...baseVisionConfig, api_configured: true, api_id: 123456789 };
client.state.bootstrap.config = telegramConfig;
const telegramSettings = await client.renderSettings();
assert(telegramSettings.includes('<form data-form="telegram-api"'), 'settings renders the Telegram API form');
assert(telegramSettings.includes('Cấu hình phiên Telegram'));
assert(telegramSettings.indexOf('data-form="vision"') < telegramSettings.indexOf('data-form="telegram-api"'), 'telegram panel follows the vision panel');
assert(telegramSettings.indexOf('data-form="telegram-api"') < telegramSettings.indexOf('Dữ liệu và sao lưu'), 'telegram panel sits above backups');
const apiIdTag = control(telegramSettings, 'telegram-api-id');
assert(apiIdTag.includes('name="api_id"') && apiIdTag.includes('type="number"'));
assert(apiIdTag.includes('min="1"') && apiIdTag.includes('max="2147483647"') && apiIdTag.includes('step="1"'));
assert(apiIdTag.includes('inputmode="numeric"') && apiIdTag.includes('required'));
assert(apiIdTag.includes('value="123456789"'), 'configured api_id is prefilled');
assert(telegramSettings.includes('api_id 123456789'));
assert(telegramSettings.includes('json_folder/tg_api.json') && telegramSettings.includes('0600'));
assert(telegramSettings.includes('không mã hoá'));
const apiHashTag = control(telegramSettings, 'telegram-api-hash');
assert(apiHashTag.includes('type="password"') && apiHashTag.includes('maxlength="32"'));
assert(apiHashTag.includes('pattern="[0-9a-fA-F]{32}"'));
assert(apiHashTag.includes('autocomplete="off"') && apiHashTag.includes('required'));
assert(!apiHashTag.includes('value='), 'api_hash is never prefilled');
assert(telegramSettings.includes('.venv-api/bin/python main.py api --login'), 'login remains CLI-only');
const telegramFormHtml = telegramSettings.match(/<form data-form="telegram-api"[^>]*>([\s\S]*?)<\/form>/)[1];
assert.deepEqual([...telegramFormHtml.matchAll(/<(?:input|select|textarea)\b[^>]*\bname="([^"]+)"/g)].map((match) => match[1]).sort(), ['api_hash', 'api_id']);
client.state.bootstrap.config = { ...baseVisionConfig };
const unconfiguredTelegram = await client.renderSettings();
assert(unconfiguredTelegram.includes('Chưa cấu hình — API cần api_id/api_hash.'));
assert(control(unconfiguredTelegram, 'telegram-api-id').includes('value=""'), 'unconfigured api_id is blank');
console.log('PASS telegram api settings form, configured api_id, status, and CLI-only login hint');

const telegramHashInput = element();
telegramHashInput.value = 'abcdef0123456789abcdef0123456789';
const telegramForm = form('telegram-api', [['api_id', ' 123456789 '], ['api_hash', ' ABCDEF0123456789ABCDEF0123456789 ']], { '[name="api_hash"]': telegramHashInput });
client.state.bootstrap.config = telegramConfig;
confirmed = true;
const postsBeforeTelegram = requests.length;
const confirmationsBeforeTelegram = confirmations;
await submit(telegramForm);
const telegramPost = requests.slice(postsBeforeTelegram).find((request) => request.options.method === 'POST' && request.url.pathname === '/api/settings');
assert(telegramPost, 'telegram settings are posted');
assert.equal(telegramPost.url.search, '', 'settings POST has no query parameters');
assert.equal(telegramHashInput.value, '', 'api_hash input is cleared immediately');
assert.equal(confirmations, confirmationsBeforeTelegram + 1, 'plaintext file write is confirmed');
const telegramPostedBody = JSON.parse(telegramPost.options.body);
assert.deepEqual(telegramPostedBody, { api_id: 123456789, api_hash: 'ABCDEF0123456789ABCDEF0123456789' });
assert.equal(typeof telegramPostedBody.api_id, 'number');
assert(requests.slice(postsBeforeTelegram).some((request) => request.options.method === 'GET' && request.url.pathname === '/api/bootstrap'), 'saving api credentials refreshes bootstrap');
const postsBeforeTelegramInvalid = requests.length;
const badIdForm = form('telegram-api', [['api_id', '0'], ['api_hash', 'abcdef0123456789abcdef0123456789']], { '[name="api_hash"]': element() });
await submit(badIdForm);
assert.equal(requests.length, postsBeforeTelegramInvalid, 'api_id range is blocked client-side');
assert(badIdForm.querySelector('[data-form-error]').textContent.includes('1..2147483647'));
const badHashForm = form('telegram-api', [['api_id', '123'], ['api_hash', 'not-hex']], { '[name="api_hash"]': element() });
await submit(badHashForm);
assert.equal(requests.length, postsBeforeTelegramInvalid, 'api_hash format is blocked client-side');
assert(badHashForm.querySelector('[data-form-error]').textContent.includes('32 ký tự hex'));
const cancelHashInput = element();
const cancelledTelegram = form('telegram-api', [['api_id', '123'], ['api_hash', 'abcdef0123456789abcdef0123456789']], { '[name="api_hash"]': cancelHashInput });
confirmed = false;
await submit(cancelledTelegram);
assert.equal(requests.length, postsBeforeTelegramInvalid, 'cancelled telegram save sends nothing');
assert.equal(cancelHashInput.value, '', 'api_hash is cleared even when confirmation is cancelled');
confirmed = true;
console.log('PASS telegram api settings POST body, validation, confirmation, and cleared password');

// Collection flow: search sources by keyword, select them, then collect each source.
client.state.bootstrap.config = { ...baseVisionConfig, api_configured: true, api_id: 123456789 };
location.hash = '#collection';
client.state.view = 'collection';
client.state.searchResults = [];
client.state.searchJobId = '';
client.state.collectSources = [];
const collectionHtml = await client.renderCollection();
assert(collectionHtml.includes('Tìm nguồn theo từ khóa'));
assert(collectionHtml.includes('Nguồn đã chọn'));
assert(collectionHtml.includes('Thu thập &amp; phân loại'));
assert(collectionHtml.includes('<form data-form="collect-search"'));
assert(collectionHtml.includes('data-form="watch"'), 'watch panel survives the redesign');
assert(control(collectionHtml, 'collect-search-keyword').includes('maxlength="200"'));
assert(control(collectionHtml, 'collect-search-keyword').includes('required'));
assert(control(collectionHtml, 'collect-search-limit').includes('max="100"'));
assert(control(collectionHtml, 'collect-search-limit').includes('value="20"'));
const searchBackendSelect = collectionHtml.match(/<select id="collect-search-backend"[^>]*>([\s\S]*?)<\/select>/);
assert(searchBackendSelect, 'search backend is a constrained select');
assert(searchBackendSelect[1].includes('value="api" selected'));
assert(!searchBackendSelect[1].includes('value="web"'), 'search backend is fixed to the API');
assert(control(collectionHtml, 'collect-session').includes('name="session"'));
assert(control(collectionHtml, 'collect-global-limit').includes('max="1000"'));
assert(collectionHtml.includes('Không có nguồn mới phù hợp'));
assert(collectionHtml.includes('chỉ hiện nguồn mới'));
assert(collectionHtml.includes('Chưa chọn nguồn nào — tìm theo từ khóa rồi thêm.'));
assert(collectionHtml.includes('data-action="collect-selected" disabled'));
assert(!collectionHtml.includes('Chưa cấu hình api_id/api_hash'), 'configured API hides the settings note');
const searchForm = form('collect-search', [['keyword', ' tin tức '], ['limit', '25'], ['backend', 'api'], ['session', 'tg_session']]);
const requestsBeforeSearch = requests.length;
const confirmationsBeforeSearch = confirmations;
await submit(searchForm);
const searchPost = requests.slice(requestsBeforeSearch).find((request) => request.options.method === 'POST' && request.options.body.includes('"kind":"search"'));
assert(searchPost, 'search form creates a search job');
const searchPayload = JSON.parse(searchPost.options.body);
assert.equal(searchPayload.kind, 'search');
assert.equal(searchPayload.db, 'fixture.db');
assert.deepEqual(searchPayload.params, { keyword: 'tin tức', limit: 25, backend: 'api', session: 'tg_session' });
assert.equal(confirmations, confirmationsBeforeSearch + 1, 'search requires confirmation before network use');
assert.equal(client.state.searchJobId, 'new-job');
client.state.searchJobId = '';
client.state.jobs = [];
client.stopPoll();

// The search-success path replaces the table from the persisted result store.
location.hash = '#collection';
client.state.view = 'collection';
const requestsBeforeResults = requests.length;
await client.loadSourceResults();
const resultsRequest = requests.slice(requestsBeforeResults).find((request) => request.options.method === 'GET' && request.url.pathname === '/api/search-results');
assert(resultsRequest, 'completed search fetches stored results');
assert.equal(resultsRequest.url.searchParams.get('limit'), '100');
assert.equal(resultsRequest.url.searchParams.get('db'), 'fixture.db');
assert.equal(client.state.searchResults.length, 1, 'fetched results replace the result table');
assert.equal(client.state.searchResults[0].username, 'from-search');
client.state.searchResults = [];

client.state.collectSources = [{ username: 'alpha', title: 'Alpha', kind: 'user', limit: 5 }, { username: 'beta', title: 'Beta', kind: 'channel', limit: 9 }];
const globalLimitInput = document.querySelector('#collect-global-limit');
globalLimitInput.value = '777';
const applyLimitTarget = { dataset: { action: 'collect-apply-limit' }, disabled: false, tagName: 'BUTTON' };
applyLimitTarget.closest = (selector) => selector === '[data-action]' ? applyLimitTarget : null;
await client.handleAction({ target: applyLimitTarget });
assert.deepEqual([...client.state.collectSources.map((source) => source.limit)], [777, 777], 'global limit overwrites every source');
const sourcesHtml = await client.renderCollection();
assert(sourcesHtml.includes('class="collect-source-limit"'));
assert(sourcesHtml.includes('data-collect-source="alpha"') && sourcesHtml.includes('data-collect-source="beta"'));
assert(control(sourcesHtml, 'collect-source-limit-0').includes('value="777"'));
assert(control(sourcesHtml, 'collect-source-limit-1').includes('value="777"'));
assert(sourcesHtml.includes('Thu thập đã chọn (2)'));
assert(!sourcesHtml.includes('data-action="collect-selected" disabled'));
assert(sourcesHtml.includes('data-action="collect-remove"') && sourcesHtml.includes('data-username="alpha"'));
assert(sourcesHtml.includes('id="collect-manual-source"'), 'manual source field is available');
assert(sourcesHtml.includes('data-action="collect-add-manual"'), 'manual add button is available');
assert(sourcesHtml.includes('id="collect-linked-groups"') && sourcesHtml.includes('name="linked_groups" checked'), 'linked groups are collected by default');
console.log('PASS collection panels, search job payload, and global per-source limits');

client.state.collectSources = [{ username: 'alpha', title: 'Alpha', kind: 'user', limit: 999 }, { username: 'beta', title: 'Beta', kind: 'channel', limit: 1000 }];
const sessionSelectNode = document.querySelector('form[data-form="collect-search"] [name="session"]');
sessionSelectNode.value = 'tg_session';
client.state.view = 'collection';
const requestsBeforeCollect = requests.length;
const collectTarget = { dataset: { action: 'collect-selected' }, disabled: false, tagName: 'BUTTON' };
collectTarget.closest = (selector) => selector === '[data-action]' ? collectTarget : null;
await client.handleAction({ target: collectTarget });
const collectPosts = requests.slice(requestsBeforeCollect).filter((request) => request.options.method === 'POST' && request.options.body.includes('"kind":"collect"'));
assert.equal(collectPosts.length, 2, 'one collect job per selected source');
const collectBodies = collectPosts.map((request) => JSON.parse(request.options.body));
assert.deepEqual(collectBodies.map((body) => body.params.channels), ['alpha', 'beta']);
assert.deepEqual(collectBodies.map((body) => body.params.limit), [999, 1000]);
for (const collectBody of collectBodies) {
  assert.equal(collectBody.db, 'fixture.db');
  assert.equal(collectBody.params.backend, 'api');
  assert.equal(collectBody.params.session, 'tg_session');
  assert.equal(collectBody.params.jev, true);
  assert.equal(collectBody.params.jev_limit, Math.min(1000, collectBody.params.limit));
  assert.equal(collectBody.params.linked_groups, true, 'discussion groups are collected by default');
  assert(!Object.hasOwn(collectBody.params, 'demo'));
}
assert.equal(client.state.collectSources.length, 0, 'selection clears after all jobs are accepted');
assert.equal(location.hash, '#jobs', 'successful submission navigates to jobs');
console.log('PASS two selected sources submit two collect jobs with capped Jev budgets');

client.state.collectSources = [];
client.state.searchResults = [
  { run_id: 1, username: 'evil', title: hostile, kind: 'channel', telegram_id: 111, keywords: [hostile, 'kích động'] },
  { run_id: 1, username: '', title: 'ID only', kind: 'chat', telegram_id: 222, keywords: [] },
  { run_id: 1, username: 'unknown-kind', title: 'Other', kind: 'mystery', telegram_id: 333, keywords: ['a'] },
  { run_id: 1, username: 'EVIL', title: 'Bản trùng', kind: 'channel', telegram_id: 444, keywords: [] },
];
client.state.searchJobId = '';
const hostileResults = await client.renderCollection();
assert(hostileResults.includes('&lt;script id=pwn&gt;'), 'hostile title and keywords are escaped');
assert(!hostileResults.includes('<script id=pwn>'));
assert(!hostileResults.includes('<img src=x'), 'hostile image markup never becomes markup');
assert(hostileResults.includes('Chọn tất cả'));
assert(hostileResults.includes('name="source-pick"'));
assert(hostileResults.includes('@evil'));
assert(hostileResults.includes('#222'), 'missing username falls back to the telegram id');
assert(hostileResults.includes('Kênh') && hostileResults.includes('Nhóm') && hostileResults.includes('Không rõ'));
assert(hostileResults.includes('kích động'), 'keywords are joined for display');
assert(hostileResults.includes('data-action="collect-add"'));
const dedupeAdded = client.addCollectSources([{ username: '@Same', title: 'first' }, { username: 'same', title: 'second' }, { username: 'other', title: 'third' }], 42);
assert.equal(dedupeAdded, 2, 'username dedupe keeps the first occurrence');
assert.deepEqual([...client.state.collectSources.map((source) => source.username)], ['Same', 'other']);
assert.equal(client.state.collectSources[0].title, 'first');
assert.deepEqual([...client.state.collectSources.map((source) => source.limit)], [42, 42]);
client.state.collectSources = [];
const manualInput = document.querySelector('#collect-manual-source');
const manualGlobalLimit = document.querySelector('#collect-global-limit');
manualGlobalLimit.value = '15';
const addManualTarget = { dataset: { action: 'collect-add-manual' }, disabled: false, tagName: 'BUTTON' };
addManualTarget.closest = (selector) => selector === '[data-action]' ? addManualTarget : null;
manualInput.value = '@newgroup, -1001234567890';
const requestsBeforeManual = requests.length;
await client.handleAction({ target: addManualTarget });
assert.equal(client.state.collectSources.length, 2, 'manual add accepts username and numeric id');
assert.deepEqual([...client.state.collectSources.map((source) => source.username)], ['newgroup', '-1001234567890']);
assert.deepEqual([...client.state.collectSources.map((source) => source.limit)], [15, 15]);
assert.equal(manualInput.value, '', 'manual input is cleared after adding');
assert.equal(requests.slice(requestsBeforeManual).filter((request) => request.options?.method === 'POST').length, 0, 'manual add does not start a job');
client.state.collectSources = [];
const pickedNodes = [{ dataset: { index: '0' }, checked: true }, { dataset: { index: '3' }, checked: true }];
const originalQuerySelectorAll = document.querySelectorAll;
document.querySelectorAll = (selector) => selector.startsWith('#view-root input[name="source-pick"]') ? pickedNodes : [];
const addTarget = { dataset: { action: 'collect-add' }, disabled: false, tagName: 'BUTTON' };
addTarget.closest = (selector) => selector === '[data-action]' ? addTarget : null;
await client.handleAction({ target: addTarget });
document.querySelectorAll = originalQuerySelectorAll;
assert.equal(client.state.collectSources.length, 1, 'collect-add dedupes checked usernames');
assert.equal(client.state.collectSources[0].username, 'evil');
assert(pickedNodes.every((node) => node.checked === false), 'checked result rows are cleared after adding');
client.state.collectSources = [];
client.state.searchResults = [];
location.hash = '#overview';
console.log('PASS hostile search result escaping and username dedupe');

client.state.searchResults = [
  { run_id: 1, username: 'chanA', title: 'A', kind: 'channel', telegram_id: 111, keywords: [] },
  { run_id: 1, username: 'userB', title: 'B', kind: 'user', telegram_id: 222, keywords: [] },
  { run_id: 1, username: '', title: 'NoName', kind: 'channel', telegram_id: 333, keywords: [] },
];
const findPicks = [{ dataset: { index: '0' }, checked: true }, { dataset: { index: '1' }, checked: true }, { dataset: { index: '2' }, checked: true }];
const oldFindQSA = document.querySelectorAll;
document.querySelectorAll = (selector) => selector.startsWith('#view-root input[name="source-pick"]') ? findPicks : [];
document.querySelector('form[data-form="collect-search"] [name="session"]').value = 'tg_session';
const requestsBeforeFind = requests.length;
const findTarget = { dataset: { action: 'collect-find-groups' }, disabled: false, tagName: 'BUTTON' };
findTarget.closest = (selector) => selector === '[data-action]' ? findTarget : null;
await client.handleAction({ target: findTarget });
document.querySelectorAll = oldFindQSA;
const findPosts = requests.slice(requestsBeforeFind).filter((request) => request.options?.method === 'POST' && request.options.body.includes('"kind":"linked_groups"'));
assert.equal(findPosts.length, 1, 'one discovery job is posted');
const findBody = JSON.parse(findPosts[0].options.body);
assert.equal(findBody.params.channels, 'chanA,-1000000000333', 'only channel rows are used, with marked ids as fallback');
assert.equal(findBody.params.session, 'tg_session');
assert.equal(client.state.linkedJobId, 'new-job', 'the client tracks the discovery job');
client.state.linkedJobId = '';
client.state.searchResults = [];
console.log('PASS find discussion groups for selected channels');


client.state.jobs = [{ id: 'active', status: 'running' }];
client.schedulePoll();
assert.equal(timers.get(client.state.pollTimer).delay, 2000);
document.hidden = true;
client.schedulePoll();
assert.equal(client.state.pollTimer, null, 'hidden document has no polling timer');
document.hidden = false;
client.state.jobs = [];
client.schedulePoll();
assert.equal(client.state.pollTimer, null, 'no active jobs means no polling timer');
client.stopPoll();
assert.equal(JSON.stringify(client.jobResultTarget({ kind: 'collect', params: { channels: '@alpha, beta' } })),
  JSON.stringify({ view: 'messages', params: { username: 'alpha' }, label: 'Xem tin đã thu thập' }));
assert.equal(client.jobResultTarget({ kind: 'watch', params: { channels: '' } }).params.username, undefined);
assert.equal(client.jobResultTarget({ kind: 'classify', params: {} }).view, 'messages');
assert.equal(client.jobResultTarget({ kind: 'search', params: {} }).view, 'collection');
assert.equal(JSON.stringify(client.jobResultTarget({ kind: 'lookup', params: { username: '@durov' } }).params), JSON.stringify({ query: 'durov' }));
assert.equal(client.jobResultTarget({ kind: 'download', params: {} }).view, 'video');
assert.equal(JSON.stringify(client.jobResultTarget({ kind: 'image_classify', params: { image_id: 5 } }).params), JSON.stringify({ analysis: 5 }));
assert.equal(JSON.stringify(client.jobResultTarget({ kind: 'video_analyze', params: { analysis_id: 7 } }).params), JSON.stringify({ analysis: 7 }));
assert.equal(client.jobResultTarget({ kind: 'db_backup', params: {} }).view, 'settings');
assert.equal(client.jobResultTarget({ kind: 'unknown', params: {} }), null);
const jobsHtml = await client.renderJobs();
assert(jobsHtml.includes('data-action="job-result"'), 'jobs table offers result navigation');
assert(jobsHtml.includes('Điều hướng'), 'navigation column is visible');
client.state.expandedJobs.set('saved', { logs: [] });
assert(client.jobsMarkup().includes('Xem tin đã thu thập'), 'collect detail names the messages view');
client.state.expandedJobs.clear();
const resultTarget = { dataset: { action: 'job-result', id: 'saved' }, disabled: false, tagName: 'BUTTON' };
resultTarget.closest = (selector) => selector === '[data-action]' ? resultTarget : null;
location.hash = '#jobs';
await client.handleAction({ target: resultTarget });
assert(location.hash.startsWith('#messages'), 'result link navigates to the messages view');
location.hash = '#overview';
console.log('PASS job cards link to the result view of each task');
console.log('PASS opening job details scrolls the detail panel into view');
client.state.jobs = [{ id: 'job-scroll1', kind: 'collect', status: 'succeeded', created_at: '2026-11-20T14:05:00Z', params: { channels: '@x' } }];
client.state.expandedJobs.clear();
client.state.view = 'jobs';
let scrollArgs = null;
const detailNode = document.querySelector('[id="job-log-job-scroll1"]');
detailNode.scrollIntoView = (options) => { scrollArgs = options; };
const logTarget = { dataset: { action: 'job-log', id: 'job-scroll1' }, disabled: false, tagName: 'BUTTON' };
logTarget.closest = (selector) => selector === '[data-action]' ? logTarget : null;
await client.handleAction({ target: logTarget });
assert.equal(scrollArgs?.block, 'start', 'detail panel is scrolled into view when opened');
assert.equal(scrollArgs?.behavior, 'smooth', 'scroll animates unless reduced motion is requested');
assert(client.state.expandedJobs.has('job-scroll1'), 'detail stays open after scrolling');
scrollArgs = null;
await client.handleAction({ target: logTarget });
assert.equal(scrollArgs, null, 'closing the detail does not scroll');
client.state.expandedJobs.clear();
client.state.jobs = [];
client.state.view = 'overview';
console.log('PASS jobs table lists tasks with result navigation and a raw detail log');
client.state.jobs = [{ id: 'job-abc12345', kind: 'collect', status: 'failed', created_at: '2026-11-20T14:05:00Z',
  started_at: '2026-11-20T14:05:01Z', ended_at: '2026-11-20T14:05:19Z', exit_code: 1,
  params: { channels: '@alpha, @beta', limit: 5, jev: true, jev_limit: 5 } }];
client.state.expandedJobs.clear();
const jobsTable = client.jobsMarkup();
for (const heading of ['Tác vụ', 'Thời gian', 'Tham số', 'Chi tiết', 'Điều hướng']) {
  assert(jobsTable.includes(`<th scope="col">${heading}</th>`), `jobs table has the ${heading} column`);
}
assert(jobsTable.includes('@alpha, @beta'), 'channels are summarised in the params column');
assert(jobsTable.includes('tối đa: 5'), 'limit is summarised in the params column');
assert(jobsTable.includes('data-action="job-result"'), 'row offers result navigation');
assert(jobsTable.includes('Xem chi tiết'), 'row offers a detail button');
assert(!jobsTable.includes('<pre class="job-log"'), 'log stays closed until details are requested');
client.state.expandedJobs.set('job-abc12345', { logs: ['[i] Dòng 1', '[!] lỗi <script id=pwn>'] });
const jobsDetail = client.jobsMarkup();
assert(jobsDetail.includes('<pre class="job-log"'), 'details keep the original raw log block');
assert(jobsDetail.includes('Tham số') && jobsDetail.includes('Nhật ký tiến trình'));
assert(jobsDetail.includes('&lt;script id=pwn&gt;') && !jobsDetail.includes('<script id=pwn>'), 'detail log is escaped');
assert(jobsDetail.includes('Ẩn chi tiết'));
client.state.expandedJobs.clear();
client.state.jobs = [];
console.log('PASS sources page offers per-source collection');
const sourceRows = client.sourcesRows([
  { id: 1, title: 'Kênh A', username: 'kenh_a', kind: 'channel', message_count: 5, flagged: 2 },
  { id: 2, title: 'Nhóm B', kind: 'group', telegram_id: 42 },
  { id: 3, title: 'Không ID' },
]);
assert(sourceRows.includes('data-action="source-collect"'), 'row has a collect button');
assert(sourceRows.includes('Thu thập thêm'));
assert(sourceRows.includes('data-handle="@kenh_a"'));
assert(sourceRows.includes('data-handle="-1000000000042"'), 'channel without username uses a marked id');
assert(JSON.stringify(client.sourceCollectParams('@kenh_a')) === JSON.stringify({ channels: '@kenh_a', backend: 'api', limit: 20, jev: true, jev_limit: 20 }));
assert(client.sourceCollectHandle({ kind: 'user', telegram_id: 7 }) === '7');
assert(client.sourceCollectHandle({}) === '');
assert(!/data-handle=""/.test(sourceRows), 'no empty handle button');

console.log('PASS visible-only 2-second job polling lifecycle');
console.log('All dashboard client contract tests passed.');
