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
  label: 'scam_indicators', classification_status: 'success', classification_stale: true,
  classification_model: hostile, score: 29, level: 'High-risk', url: 'javascript:alert(1)',
};
const fixtures = {
  bootstrap: {
    token: 'fixture-dashboard-token', databases: [{ name: 'fixture.db', label: hostile }],
    active_database: 'fixture.db', config: { jev_configured: false, sessions: [] }, jobs: [],
  },
  overview: {
    counts: { messages: 1, entities: 1, classified: 0, scam_indicators: 0 },
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
    jobCard, submitForm, schedulePoll, stopPoll, sourceDisplay,
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
  console.log(`PASS render ${name}`);
}
assert(rendered.Messages.includes('class="data-table message-table"'), 'message table exposes mobile-specific class');
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
  watch: ['channels', 'session', 'jev', 'jev_limit', 'interval', 'cycles', 'once'],
  classify: ['limit', 'query', 'username', 'include_demo', 'recheck'],
  lookup: ['username', 'limit', 'backend', 'session'],
  search: ['keyword', 'limit', 'backend', 'session'],
  download: ['url', 'session'],
  video_analyze: ['path', 'skip_vision', 'language', 'model', 'max_frames', 'ocr', 'ocr_language'],
  image_analyze: ['path', 'ocr_language', 'vision'],
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
for (const id of ['collect-budget', 'watch-budget']) assert(control(rendered.Collection, id).includes('max="100"'));
assert(control(rendered.Classify, 'classify-limit').includes('max="100"'));
assert(control(rendered.Collection, 'watch-interval').includes('max="3600"'));
assert(control(rendered.Collection, 'watch-cycles').includes('max="1000"'));
assert(control(rendered.Collection, 'collect-limit').includes('max="1000"'));
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
assert(rendered.Image.includes('91.2'));
assert(rendered.Image.includes('Tesseract chỉ nhận diện chữ'));
assert(rendered.Image.includes('vision_skipped'));
assert(rendered.Video.includes('Khung hình tại 1.2 giây'));
assert(client.ocrDetails(null).includes('không đồng nghĩa nội dung an toàn'));
assert(client.ocrDetails({ status: 'success', text: '', words: [] }).includes('không có chữ hay nội dung an toàn'));
console.log('PASS image/video OCR details, partial gaps, defaults, field allowlists, and constrained languages');
assert(client.messageRows([message]).includes('&lt;script id=pwn&gt;'));
assert(client.messageRows([message]).includes('Kết quả cũ'));
assert(!client.messageRows([message]).includes('Có dấu hiệu lừa đảo'));
assert(!client.labelBadge('scam_indicators', 'stale').includes('Có dấu hiệu lừa đảo'));
assert(client.labelBadge(null, 'error').includes('Lỗi phân loại'));
assert(client.labelBadge(null).includes('Chưa phân loại'));
assert(!client.labelBadge(null).includes('an toàn'));
client.state.expandedJobs.set('saved', fixtures.jobs.items[0]);
assert(client.jobCard(fixtures.jobs.items[0]).includes('&lt;script id=pwn&gt;'));
assert(!client.jobCard(fixtures.jobs.items[0]).includes('<script id=pwn>'));
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
const watch = form('watch', [['channels', '@fixture\n@second'], ['cycles', 3], ['interval', 60], ['limit', 10], ['backend', 'web']]);
await client.submitForm({ target: watch, preventDefault() {} });
const created = requests.findLast(request => request.options.method === 'POST' && request.options.body.includes('"kind":"watch"'));
assert(created, 'watch sends an actual job request');
const body = JSON.parse(created.options.body);
assert.equal(created.url.search, '', 'job creation has no query parameters');
assert.equal(body.db, 'fixture.db', 'job creation sends selected database in JSON body');
assert.equal(body.params.channels, '@fixture\n@second');
assert.equal(body.params.once, true, 'watch always has finite time window');
assert(!Object.hasOwn(body.params, 'limit'), 'watch drops unsupported history limit defensively');
assert(!Object.hasOwn(body.params, 'backend'), 'watch lets backend choose API internally');
for (const name of Object.keys(body.params)) assert(allowedJobFields.watch.includes(name), `watch payload supports ${name}`);
assert.equal(body.params.cycles, 3, 'cycles remains max-new-message cap');
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
console.log('PASS visible-only 2-second job polling lifecycle');
console.log('All dashboard client contract tests passed.');
