// ── API helpers ───────────────────────────────────────────────────────
const API = '/api';
const APP_TOKEN = document.querySelector('meta[name="app-token"]').content;

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'style') node.style.cssText = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child == null) continue;
    node.append(typeof child === 'string' ? document.createTextNode(child) : child);
  }
  return node;
}

function fill(target, children) {
  target.replaceChildren(...[].concat(children).filter(Boolean));
}

function headers(extra = {}) {
  return { 'X-App-Token': APP_TOKEN, ...extra };
}

async function apiGet(path) {
  try { return await (await fetch(API + path, { headers: headers() })).json(); }
  catch { return null; }
}
async function apiSend(method, path, body = {}) {
  try {
    return await (await fetch(API + path, {
      method,
      headers: headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    })).json();
  } catch { return null; }
}
const apiPost = (path, body) => apiSend('POST', path, body);
const apiPatch = (path, body) => apiSend('PATCH', path, body);

// ── State ─────────────────────────────────────────────────────────────
const tgState = {
  running: false, safeMode: true, parseHistory: false,
  channels: [], keywords: [], exclude: [],
  template: '', maxPerDay: 25, historyLimit: 50,
  sentToday: 0, foundToday: 0, sentTotal: 0,
  apiId: '', apiHashSet: false, autostart: false,
};

const hhState = {
  running: false, keywords: [], exclude: [],
  areaIds: [113], schedule: ['remote', 'fullDay', 'flexible'],
  maxPerDay: 20, sentToday: 0, foundToday: 0, totalSent: 0,
  autostart: false, seleniumSteps: [],
};

// ── Navigation ────────────────────────────────────────────────────────
document.querySelectorAll('.nav-item[data-page]').forEach(item => {
  item.addEventListener('click', async () => {
    const page = item.dataset.page;
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.getElementById('page-' + page).classList.add('active');
    item.classList.add('active');
    if (page === 'channels') renderChannelEdit();
    if (page === 'keywords') renderKeywords();
    if (page === 'template') {
      document.getElementById('templateText').value = tgState.template;
    }
    if (page === 'settings') await loadSettings();
    if (page === 'logs') refreshLogs();
    if (page === 'chats') await renderChats();
  });
});

// ── TG Status UI ──────────────────────────────────────────────────────
function updateTGButton() {
  const btn = document.getElementById('btnToggleTG');
  const dot = document.getElementById('dotTG');
  if (!btn || !dot) return;
  if (tgState.running) {
    btn.className = 'btn-toggle btn-toggle-tg active';
    fill(btn, [el('span', { class: 'status-dot running', id: 'dotTG' }), ' Остановить TG']);
  } else {
    btn.className = 'btn-toggle btn-toggle-tg';
    fill(btn, [el('span', { class: 'status-dot stopped', id: 'dotTG' }), ' Запустить TG']);
  }
}

function updateHHButton() {
  const btn = document.getElementById('btnToggleHH');
  if (!btn) return;
  if (hhState.running) {
    btn.className = 'btn-toggle btn-toggle-hh active';
    fill(btn, [el('span', { class: 'status-dot running', id: 'dotHH' }), ' Остановить HH']);
  } else {
    btn.className = 'btn-toggle btn-toggle-hh';
    fill(btn, [el('span', { class: 'status-dot stopped', id: 'dotHH' }), ' Запустить HH']);
  }
}

function updateMetrics() {
  // TG
  const tgSent = document.getElementById('tgSentToday');
  const tgFound = document.getElementById('tgFoundToday');
  const tgTotal = document.getElementById('tgSentTotal');
  const tgBar = document.getElementById('tgMetricBar');
  const tgSub = document.getElementById('tgMetricSub');
  if (tgSent) tgSent.textContent = tgState.sentToday;
  if (tgFound) tgFound.textContent = tgState.foundToday;
  if (tgTotal) tgTotal.textContent = tgState.sentTotal;
  if (tgBar) tgBar.style.width = tgState.maxPerDay > 0
    ? Math.min(Math.round((tgState.sentToday / tgState.maxPerDay) * 100), 100) + '%' : '0%';
  if (tgSub) tgSub.textContent = `из ${tgState.maxPerDay} в день`;

  // HH
  const hhSent = document.getElementById('hhSentToday');
  const hhFound = document.getElementById('hhFoundToday');
  const hhTotal = document.getElementById('hhTotalSent');
  const hhBar = document.getElementById('hhMetricBar');
  const hhSub = document.getElementById('hhMetricSub');
  if (hhSent) hhSent.textContent = hhState.sentToday;
  if (hhFound) hhFound.textContent = hhState.foundToday;
  if (hhTotal) hhTotal.textContent = hhState.totalSent;
  if (hhBar) hhBar.style.width = hhState.maxPerDay > 0
    ? Math.min(Math.round((hhState.sentToday / hhState.maxPerDay) * 100), 100) + '%' : '0%';
  if (hhSub) hhSub.textContent = `из ${hhState.maxPerDay} в день`;
}

function updateDashboard() {
  const chanList = document.getElementById('dashChannelList');
  const chanCount = document.getElementById('dashChannelCount');
  if (chanList) fill(chanList, tgState.channels.map(ch => el('div', {
    class: 'channel-row',
    style: 'display:flex;align-items:center;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--border);font-size:12px',
  }, [
    el('span', { style: "font-family:'JetBrains Mono',monospace;font-weight:500", text: '@' + ch }),
  ])));
  if (chanCount) chanCount.textContent = tgState.channels.length;
  const kwTags = document.getElementById('kwTags');
  const exTags = document.getElementById('exTags');
  if (kwTags) fill(kwTags, tgState.keywords.map(k => el('span', { class: 'tag tag-blue', text: k })));
  if (exTags) fill(exTags, tgState.exclude.map(k => el('span', { class: 'tag tag-red', text: k })));
}

// ── TG Script control ─────────────────────────────────────────────────
async function toggleTG() {
  if (tgState.running) {
    const r = await apiPost('/tg/stop');
    if (r && r.status === 'stopped') {
      tgState.running = false; updateTGButton();
      showToast('TG монитор остановлен');
    } else showToast(r?.detail || 'Ошибка');
  } else {
    const r = await apiPost('/tg/start');
    if (r && r.status === 'started') {
      tgState.running = true; updateTGButton();
      showToast('TG монитор запущен');
    } else showToast(r?.detail || 'Ошибка запуска TG');
    hideRestartBanner();
  }
}

// ── HH Script control ─────────────────────────────────────────────────
async function toggleHH() {
  if (hhState.running) {
    const r = await apiPost('/hh/stop');
    if (r && r.status === 'stopped') {
      hhState.running = false; updateHHButton();
      showToast('HH монитор остановлен');
    } else showToast(r?.detail || 'Ошибка');
  } else {
    const r = await apiPost('/hh/start');
    if (r && r.status === 'started') {
      hhState.running = true; updateHHButton();
      showToast('HH монитор запущен — войдите в браузере');
    } else showToast(r?.detail || 'Ошибка запуска HH');
  }
}

// ── Channels ──────────────────────────────────────────────────────────
function renderChannelEdit() {
  fill(document.getElementById('channelEditList'), tgState.channels.map((ch, i) => el('div', { class: 'list-item' }, [
    el('span', { text: '@' + ch }),
    el('button', { class: 'btn-del', text: '×', onclick: () => removeChannel(i) }),
  ])));
}
function addChannel() {
  const inp = document.getElementById('newChannel');
  const val = inp.value.trim().replace('@', '').toLowerCase();
  if (!val) return;
  if (tgState.channels.map(c => c.toLowerCase()).includes(val)) { showToast('Канал уже есть'); return; }
  tgState.channels.push(val); inp.value = '';
  renderChannelEdit(); updateDashboard();
}
function removeChannel(i) { tgState.channels.splice(i, 1); renderChannelEdit(); updateDashboard(); }
async function saveChannels() {
  await apiPatch('/config', { channels: tgState.channels });
  if (tgState.running) { showToast('Сохранено — перезапустите TG'); showRestartBanner(); }
  else showToast('Каналы сохранены');
}

// ── Keywords ──────────────────────────────────────────────────────────
function renderKeywords() {
  fill(document.getElementById('kwList'), tgState.keywords.map((k, i) => el('div', { class: 'list-item' }, [
    el('span', { text: k }),
    el('button', { class: 'btn-del', text: '×', onclick: () => removeKw(i) }),
  ])));
  fill(document.getElementById('exList'), tgState.exclude.map((k, i) => el('div', { class: 'list-item' }, [
    el('span', { text: k }),
    el('button', { class: 'btn-del', text: '×', onclick: () => removeEx(i) }),
  ])));
  updateDashboard();
}
function addKw() { const v = document.getElementById('newKw').value.trim().toLowerCase(); if (!v) return; if (tgState.keywords.includes(v)) { showToast('Уже есть'); return; } tgState.keywords.push(v); document.getElementById('newKw').value = ''; renderKeywords(); }
function removeKw(i) { tgState.keywords.splice(i, 1); renderKeywords(); }
function addEx() { const v = document.getElementById('newEx').value.trim().toLowerCase(); if (!v) return; if (tgState.exclude.includes(v)) { showToast('Уже есть'); return; } tgState.exclude.push(v); document.getElementById('newEx').value = ''; renderKeywords(); }
function removeEx(i) { tgState.exclude.splice(i, 1); renderKeywords(); }
async function saveKeywords() {
  await apiPatch('/config', { keywords: tgState.keywords, exclude: tgState.exclude });
  updateDashboard();
  if (tgState.running) { showToast('Сохранено — перезапустите TG'); showRestartBanner(); }
  else showToast('Ключевые слова сохранены');
}

// ── Templates ─────────────────────────────────────────────────────────
async function saveTemplate() {
  tgState.template = document.getElementById('templateText').value;
  await apiPatch('/config', { template: tgState.template });
  if (tgState.running) { showToast('Сохранено — перезапустите TG'); showRestartBanner(); }
  else showToast('Шаблон сохранён');
}
async function saveHHCoverLetter() {
  const letter = document.getElementById('hhCoverLetter').value;
  await apiPatch('/config', { hh_cover_letter: letter });
  showToast('Сопроводительное письмо сохранено');
}
function onFileSelect(input) {
  const file = input.files[0]; if (!file) return;
  document.getElementById('fileZone').classList.add('has-file');
  document.getElementById('fileZoneLabel').textContent = 'Файл выбран';
  document.getElementById('fileName').textContent = file.name;
  document.getElementById('currentFile').textContent = file.name;
  showToast('Файл выбран: ' + file.name);
}

// ── Settings ──────────────────────────────────────────────────────────
async function loadSettings() {
  const cfg = await apiGet('/config');
  if (!cfg) { showToast('Не удалось загрузить конфиг'); return; }

  // TG
  tgState.safeMode = cfg.safe_mode;
  tgState.parseHistory = cfg.parse_history;
  tgState.maxPerDay = cfg.max_per_day;
  tgState.historyLimit = cfg.history_limit;
  tgState.apiId = cfg.api_id || '';
  tgState.apiHashSet = cfg.api_hash_set || false;
  tgState.autostart = cfg.tg_autostart || false;

  document.getElementById('toggleSafe').checked = tgState.safeMode;
  document.getElementById('toggleHistory').checked = tgState.parseHistory;
  document.getElementById('toggleTGAutostart').checked = tgState.autostart;
  document.getElementById('maxPerDay').value = tgState.maxPerDay;
  document.getElementById('historyLimit').value = tgState.historyLimit;
  document.getElementById('apiId').value = tgState.apiId;
  if (tgState.apiHashSet) {
    document.getElementById('apiHash').value = '••••••••••••••••';
    document.getElementById('apiHash').placeholder = 'Hash сохранён — введите новый для изменения';
  } else {
    document.getElementById('apiHash').value = '';
    document.getElementById('apiHash').placeholder = 'abcdef1234567890abcdef1234567890';
  }

  // HH
  if (cfg.hh_keywords) { hhState.keywords = cfg.hh_keywords; renderHHKeywords(); }
  if (cfg.hh_exclude) { hhState.exclude = cfg.hh_exclude; renderHHKeywords(); }
  if (cfg.hh_area_ids) {
    hhState.areaIds = cfg.hh_area_ids;
    document.querySelectorAll('.hh-region').forEach(cb => { cb.checked = hhState.areaIds.includes(parseInt(cb.value)); });
  }
  if (cfg.hh_experience) document.getElementById('hhExperience').value = cfg.hh_experience;
  if (cfg.hh_salary_from !== undefined) document.getElementById('hhSalaryFrom').value = cfg.hh_salary_from;
  if (cfg.hh_search_period) document.getElementById('hhSearchPeriod').value = cfg.hh_search_period;
  if (cfg.hh_max_per_day) document.getElementById('hhMaxPerDayInput').value = cfg.hh_max_per_day;
  if (cfg.hh_check_interval) document.getElementById('hhCheckInterval').value = cfg.hh_check_interval / 60;
  if (cfg.hh_schedule) document.querySelectorAll('.hh-schedule').forEach(cb => { cb.checked = cfg.hh_schedule.includes(cb.value); });
  if (cfg.hh_resume_id) document.getElementById('hhResumeId').value = cfg.hh_resume_id;
  if (cfg.hh_cover_letter) document.getElementById('hhCoverLetter').value = cfg.hh_cover_letter;
  if (cfg.hh_autostart !== undefined) document.getElementById('toggleHHAutostart').checked = cfg.hh_autostart;
  if (cfg.hh_selenium_steps) { hhState.seleniumSteps = cfg.hh_selenium_steps; renderSeleniumSteps(); }

  await checkWebAuth();
}

async function saveTGSettings() {
  const data = {
    safe_mode: document.getElementById('toggleSafe').checked,
    parse_history: document.getElementById('toggleHistory').checked,
    max_per_day: parseInt(document.getElementById('maxPerDay').value),
    history_limit: parseInt(document.getElementById('historyLimit').value),
    tg_autostart: document.getElementById('toggleTGAutostart').checked,
  };
  tgState.safeMode = data.safe_mode;
  tgState.parseHistory = data.parse_history;
  tgState.maxPerDay = data.max_per_day;
  await apiPatch('/config', data);
  updateMetrics();
  if (tgState.running) { showToast('Сохранено — перезапустите TG'); showRestartBanner(); }
  else showToast('TG настройки сохранены');
}

async function saveApiKeys() {
  const api_id = document.getElementById('apiId').value.trim();
  const api_hash = document.getElementById('apiHash').value.trim();
  if (!api_id) { showToast('Введите API ID'); return; }
  const body = { api_id };
  if (api_hash && !api_hash.startsWith('••')) body.api_hash = api_hash;
  await apiPatch('/config', body);
  showToast('API ключи сохранены');
  if (tgState.running) showRestartBanner();
}

// ── Web Auth ──────────────────────────────────────────────────────────
let _phoneHash = '';

async function checkWebAuth() {
  const r = await apiGet('/auth/status');
  const statusEl = document.getElementById('authStatus');
  const form = document.getElementById('authForm');
  if (!statusEl) return;
  if (r && r.authorized) {
    fill(statusEl, el('span', { style: 'color:var(--green);font-weight:600', text: '✓ Авторизован — чаты доступны' }));
    if (form) form.style.display = 'none';
  } else {
    fill(statusEl, el('span', { style: 'color:var(--red)', text: '✗ Не авторизован' }));
    if (form) form.style.display = 'block';
  }
}

async function sendAuthCode() {
  const phone = document.getElementById('authPhone').value.trim();
  if (!phone) { showToast('Введите номер'); return; }
  const r = await apiPost('/auth/send-code', { phone });
  if (!r) { showToast('Ошибка'); return; }
  if (r.status === 'already_authorized') { showToast('Уже авторизован!'); checkWebAuth(); return; }
  if (r.phone_hash) { _phoneHash = r.phone_hash; document.getElementById('authCodeRow').style.display = 'block'; showToast('Код отправлен'); }
  else showToast('Ошибка: ' + (r.detail || ''));
}

async function verifyAuthCode() {
  const phone = document.getElementById('authPhone').value.trim();
  const code = document.getElementById('authCode').value.trim();
  const password = document.getElementById('auth2fa').value.trim();
  if (!code) { showToast('Введите код'); return; }
  const body = { phone, code, phone_hash: _phoneHash };
  if (password) body.password = password;
  const resp = await fetch(API + '/auth/verify-code', { method: 'POST', headers: headers({ 'Content-Type': 'application/json' }), body: JSON.stringify(body) });
  const r = await resp.json();
  if (resp.status === 428 && r.detail === '2FA_REQUIRED') {
    document.getElementById('auth2faRow').style.display = 'block';
    document.getElementById('auth2fa').focus();
    showToast('Введите облачный пароль (2FA)');
    return;
  }
  if (r && r.status === 'authorized') { showToast('Авторизация успешна!'); checkWebAuth(); document.getElementById('authCodeRow').style.display = 'none'; }
  else showToast('Ошибка: ' + (r?.detail || 'неверный код'));
}

// ── HH Settings ───────────────────────────────────────────────────────
function renderHHKeywords() {
  const kl = document.getElementById('hhKwList');
  const exListEl = document.getElementById('hhExList');
  if (kl) fill(kl, hhState.keywords.map((k, i) => el('div', { class: 'list-item' }, [
    el('span', { text: k }),
    el('button', { class: 'btn-del', text: '×', onclick: () => removeHHKw(i) }),
  ])));
  if (exListEl) fill(exListEl, hhState.exclude.map((k, i) => el('div', { class: 'list-item' }, [
    el('span', { text: k }),
    el('button', { class: 'btn-del', text: '×', onclick: () => removeHHEx(i) }),
  ])));
}
function addHHKw() { const v = document.getElementById('newHHKw').value.trim(); if (!v || hhState.keywords.includes(v)) { if (v) showToast('Уже есть'); return; } hhState.keywords.push(v); document.getElementById('newHHKw').value = ''; renderHHKeywords(); }
function removeHHKw(i) { hhState.keywords.splice(i, 1); renderHHKeywords(); }
function addHHEx() { const v = document.getElementById('newHHEx').value.trim().toLowerCase(); if (!v || hhState.exclude.includes(v)) { if (v) showToast('Уже есть'); return; } hhState.exclude.push(v); document.getElementById('newHHEx').value = ''; renderHHKeywords(); }
function removeHHEx(i) { hhState.exclude.splice(i, 1); renderHHKeywords(); }

async function saveHHSettings() {
  const schedule = [...document.querySelectorAll('.hh-schedule:checked')].map(cb => cb.value);
  const areaIds = [...document.querySelectorAll('.hh-region:checked')].map(cb => parseInt(cb.value));
  const data = {
    hh_keywords: hhState.keywords,
    hh_exclude: hhState.exclude,
    hh_area_ids: areaIds,
    hh_experience: document.getElementById('hhExperience').value,
    hh_salary_from: parseInt(document.getElementById('hhSalaryFrom').value) || 0,
    hh_search_period: parseInt(document.getElementById('hhSearchPeriod').value),
    hh_max_per_day: parseInt(document.getElementById('hhMaxPerDayInput').value),
    hh_check_interval: parseInt(document.getElementById('hhCheckInterval').value) * 60,
    hh_schedule: schedule,
    hh_resume_id: document.getElementById('hhResumeId').value.trim(),
    hh_cover_letter: document.getElementById('hhCoverLetter').value,
    hh_autostart: document.getElementById('toggleHHAutostart').checked,
    hh_selenium_steps: hhState.seleniumSteps,
  };
  await apiPatch('/config', data);
  showToast('HH настройки сохранены');
}

// ── Selenium Steps Editor ─────────────────────────────────────────────
const STEP_TYPES = {
  click: 'Клик',
  input: 'Ввод текста',
  wait: 'Ожидание (сек)',
  wait_element: 'Ждать элемент',
  select: 'Выбрать значение',
  scroll: 'Прокрутить',
};

function renderSeleniumSteps() {
  const listEl = document.getElementById('seleniumStepsList');
  if (!listEl) return;
  if (!hhState.seleniumSteps.length) {
    fill(listEl, el('div', {
      style: 'text-align:center;padding:16px;color:var(--muted);font-size:13px',
      text: 'Нет шагов — нажмите + чтобы добавить',
    }));
    return;
  }
  fill(listEl, hhState.seleniumSteps.map((step, i) => el('div', { class: 'step-item' }, [
    el('span', { class: 'step-num', text: (i + 1) + '.' }),
    el('span', { class: 'step-type', text: STEP_TYPES[step.type] || step.type }),
    el('span', { class: 'step-desc', text: step.selector || step.value || step.seconds || '' }),
    el('button', { class: 'btn-del', title: 'Вверх', text: '↑', onclick: () => moveStep(i, -1) }),
    el('button', { class: 'btn-del', title: 'Вниз', text: '↓', onclick: () => moveStep(i, 1) }),
    el('button', { class: 'btn-del', title: 'Изменить', text: '✎', onclick: () => editStep(i) }),
    el('button', { class: 'btn-del', title: 'Удалить', text: '×', onclick: () => removeStep(i) }),
  ])));
}

function moveStep(i, dir) {
  const j = i + dir;
  if (j < 0 || j >= hhState.seleniumSteps.length) return;
  [hhState.seleniumSteps[i], hhState.seleniumSteps[j]] = [hhState.seleniumSteps[j], hhState.seleniumSteps[i]];
  renderSeleniumSteps();
}

function removeStep(i) { hhState.seleniumSteps.splice(i, 1); renderSeleniumSteps(); }

function addStep() {
  const type = document.getElementById('newStepType').value;
  const selector = document.getElementById('newStepSelector').value.trim();
  const value = document.getElementById('newStepValue').value.trim();
  if (!type) return;
  const step = { type };
  if (selector) step.selector = selector;
  if (value) step.value = value;
  if (type === 'wait') step.seconds = parseInt(value) || 2;
  hhState.seleniumSteps.push(step);
  document.getElementById('newStepSelector').value = '';
  document.getElementById('newStepValue').value = '';
  renderSeleniumSteps();
}

function editStep(i) {
  const step = hhState.seleniumSteps[i];
  document.getElementById('newStepType').value = step.type;
  document.getElementById('newStepSelector').value = step.selector || '';
  document.getElementById('newStepValue').value = step.value || step.seconds || '';
  removeStep(i);
}

// ── Chats ─────────────────────────────────────────────────────────────
let currentChat = null;

async function renderChats() {
  const chats = await apiGet('/tg/chats');
  const listEl = document.getElementById('chatList');
  const count = document.getElementById('chatCount');
  if (chats) {
    if (count) count.textContent = chats.length;
    if (!chats.length) {
      fill(listEl, el('div', {
        style: 'text-align:center;padding:32px;color:var(--muted);font-size:13px',
        text: 'Список пуст',
      }));
      return;
    }
    fill(listEl, chats.map(c => {
      const name = c.username || '';
      return el('div', {
        class: 'chat-contact',
        id: 'contact-' + name.replace('@', ''),
        onclick: () => openChat(name),
      }, [
        el('div', { class: 'chat-avatar', text: name.replace('@', '').slice(0, 2).toUpperCase() }),
        el('div', { style: 'flex:1;min-width:0' }, [
          el('div', { class: 'chat-name', text: name }),
          el('div', { class: 'chat-preview', text: c.preview || '' }),
        ]),
        el('div', { class: 'chat-time', text: c.time || '' }),
      ]);
    }));
  }
}

async function openChat(username) {
  currentChat = username;
  document.querySelectorAll('.chat-contact').forEach(el => el.classList.remove('active'));
  const el = document.getElementById('contact-' + username.replace('@', ''));
  if (el) el.classList.add('active');
  document.getElementById('chatEmpty').style.display = 'none';
  const view = document.getElementById('chatView');
  view.style.display = 'flex';
  document.getElementById('chatViewAvatar').textContent = username.replace('@', '').slice(0, 2).toUpperCase();
  document.getElementById('chatViewName').textContent = username;
  await loadChatMessages(username);
}

async function loadChatMessages(username) {
  if (!username) return;
  const msgsEl = document.getElementById('chatMessages');
  fill(msgsEl, el('div', { style: 'text-align:center;color:var(--muted);font-size:13px;padding:20px', text: 'Загрузка...' }));
  const msgs = await apiGet('/auth/messages/' + encodeURIComponent(username.replace('@', '')));
  if (!msgs) {
    fill(msgsEl, el('div', { style: 'text-align:center;color:var(--red);font-size:13px;padding:20px', text: 'Ошибка. Проверьте авторизацию.' }));
    return;
  }
  if (!msgs.length) {
    fill(msgsEl, el('div', { style: 'text-align:center;color:var(--muted);font-size:13px;padding:20px', text: 'Сообщений нет' }));
    return;
  }
  fill(msgsEl, msgs.map(m => el('div', { class: `msg-wrap ${m.out ? 'out' : 'in'}` }, [
    el('div', { class: `msg-bubble ${m.out ? 'msg-out' : 'msg-in'}`, text: m.text }),
    el('div', {
      style: `font-size:10px;opacity:.6;margin-top:2px;text-align:${m.out ? 'right' : 'left'}`,
      text: m.date,
    }),
  ])));
  msgsEl.scrollTop = msgsEl.scrollHeight;
}

async function sendChatMessage() {
  if (!currentChat) return;
  const inp = document.getElementById('chatInput');
  const text = inp.value.trim(); if (!text) return;
  inp.value = ''; inp.disabled = true;
  const r = await fetch(API + '/auth/messages/' + encodeURIComponent(currentChat.replace('@', '')), {
    method: 'POST', headers: headers({ 'Content-Type': 'application/json' }), body: JSON.stringify({ text })
  });
  inp.disabled = false; inp.focus();
  if (r.ok) await loadChatMessages(currentChat);
  else { const e = await r.json(); showToast('Ошибка: ' + (e.detail || '')); }
}

// ── HH Vacancies ──────────────────────────────────────────────────────
async function loadHHVacancies() {
  const vacs = await apiGet('/hh/vacancies');
  const vacEl = document.getElementById('hhRecentVacancies');
  if (!vacEl) return;
  if (!vacs || !vacs.length) {
    fill(vacEl, el('div', {
      style: 'grid-column:1/-1;text-align:center;padding:16px;color:var(--muted);font-size:13px',
      text: 'Вакансий пока нет',
    }));
    return;
  }
  fill(vacEl, vacs.slice(0, 4).map(v => {
    let cls = 'status-wait'; const st = v.status || '';
    if (st.includes('отправлен')) cls = 'status-sent';
    else if (st.includes('пропущено')) cls = 'status-skip';
    return el('div', { class: 'vac-card' }, [
      el('div', { style: 'display:flex;align-items:flex-start;justify-content:space-between;gap:8px' }, [
        el('div', {}, [
          el('div', { style: 'font-size:13px;font-weight:600', text: v.title || '' }),
          el('div', { style: 'font-size:11px;color:var(--muted);margin-top:2px', text: `${v.company || ''} · ${v.salary || 'з/п не указана'}` }),
        ]),
        el('span', { class: `status-badge ${cls}`, style: 'flex-shrink:0', text: st || 'ожидание' }),
      ]),
      v.url ? el('div', { style: 'margin-top:8px' }, [
        el('a', { href: v.url, target: '_blank', style: 'font-size:11px;color:var(--hh);text-decoration:none', text: 'Открыть на HH →' }),
      ]) : null,
    ]);
  }));
}

// ── Logs ──────────────────────────────────────────────────────────────
let currentLogType = 'tg';

function showLog(type) {
  currentLogType = type;
  document.querySelectorAll('.log-tab').forEach(t => t.className = 'log-tab');
  const btn = document.getElementById('logTab' + type.toUpperCase());
  if (btn) btn.className = `log-tab active-${type}`;
  refreshLogs();
}

async function refreshLogs() {
  const path = currentLogType === 'hh' ? '/hh/logs?lines=150' : '/tg/logs?lines=150';
  const r = await apiGet(path);
  const logEl = document.getElementById('logConsole');
  if (!r || !r.log) { fill(logEl, el('span', { class: 'info', text: '// Лог пуст' })); return; }
  const lines = r.log.trim().split('\n').filter(Boolean);
  const nodes = [];
  lines.forEach((line, i) => {
    let cls = 'info';
    if (line.includes('[OK]') || line.includes('Отправлено')) cls = 'ok';
    else if (line.includes('[ERROR]')) cls = 'err';
    else if (line.includes('[WARNING]') || line.includes('RESET')) cls = 'warn';
    else if (line.includes('[HISTORY]')) cls = 'hist';
    else if (line.includes('[HH]')) cls = 'hh';
    if (i > 0) nodes.push('\n');
    nodes.push(el('span', { class: cls, text: line }));
  });
  fill(logEl, nodes);
  logEl.scrollTop = logEl.scrollHeight;
}

function clearConsole() { fill(document.getElementById('logConsole'), el('span', { class: 'info', text: '// Очищено' })); }

// ── Recent log parser ─────────────────────────────────────────────────
function parseLogLine(line, source) {
  const tm = line.match(/(\d{2}:\d{2}):\d{2}/); const time = tm ? tm[1] : '—';
  let badge, cls, text;
  if (line.includes('[OK]') || line.includes('Отправлено')) {
    badge = 'OK'; cls = 'badge-ok';
    const m = line.match(/Отправлено:\s*(@\S+)/);
    text = m ? 'Отправлено ' + m[1] : line.split(']').slice(1).join(']').trim();
  } else if (line.includes('[ERROR]')) {
    badge = 'ERR'; cls = 'badge-error'; text = line.split('[ERROR]').slice(1).join('').trim();
  } else if (line.includes('[SAFE MODE]')) {
    badge = 'SAFE'; cls = 'badge-safe'; const m = line.match(/(@\S+)/); text = m ? 'Найден: ' + m[1] : 'Найден контакт';
  } else if (line.includes('[SKIP]')) {
    badge = 'SKIP'; cls = 'badge-skip'; text = line.split('[SKIP]').slice(1).join('').trim();
  } else if (line.includes('[ВАКАНСИЯ]')) {
    badge = 'VAC'; cls = 'badge-skip';
    const parts = line.split('[ВАКАНСИЯ]');
    text = parts.length > 1 ? parts[1].trim().slice(0, 80) : 'Найдена вакансия';
  } else if (line.includes('[HH][OK]')) {
    badge = 'HH'; cls = 'badge-ok'; text = line.split('[HH][OK]').slice(1).join('').trim().slice(0, 80);
  } else if (line.includes('[START]')) {
    badge = 'SYS'; cls = 'badge-skip'; text = 'Скрипт запущен, мониторинг активен';
  } else if (line.includes('[DAILY RESET]')) {
    badge = 'SYS'; cls = 'badge-skip'; text = 'Новый день — счётчики сброшены';
  } else if (line.includes('Got difference')) {
    badge = 'UPD'; cls = 'badge-skip'; text = 'Получены обновления из каналов';
  } else if (line.includes('Connecting to')) {
    badge = 'NET'; cls = 'badge-skip'; text = 'Подключение к Telegram...';
  } else if (line.includes('Connection to') && line.includes('complete')) {
    badge = 'NET'; cls = 'badge-ok'; text = 'Подключение установлено';
  } else {
    return null;
  }
  return { time, badge, cls, text: text ? text.slice(0, 90) : '', source };
}

// ── Restart banner ────────────────────────────────────────────────────
function showRestartBanner() {
  if (document.getElementById('restartBanner')) return;
  const b = el('div', { id: 'restartBanner', class: 'restart-banner' }, [
    el('span', { text: '⚠️ Настройки изменены — перезапустите TG скрипт' }),
    el('div', { style: 'display:flex;gap:8px' }, [
      el('button', {
        style: 'padding:4px 12px;background:var(--tg);color:#fff;border:none;border-radius:5px;cursor:pointer;font-size:12px;font-weight:600',
        text: 'Перезапустить',
        onclick: () => { toggleTG(); hideRestartBanner(); },
      }),
      el('button', {
        style: 'padding:4px 8px;background:none;border:none;cursor:pointer;color:#92400e;font-size:16px',
        text: '×',
        onclick: () => hideRestartBanner(),
      }),
    ]),
  ]);
  document.body.appendChild(b);
}
function hideRestartBanner() { const b = document.getElementById('restartBanner'); if (b) b.remove(); }

// ── Toast ─────────────────────────────────────────────────────────────
function showToast(msg) {
  const t = document.createElement('div');
  t.textContent = msg;
  t.style.cssText = 'position:fixed;bottom:22px;right:22px;background:var(--text);color:#fff;padding:9px 16px;border-radius:8px;font-size:12.5px;font-weight:500;z-index:9999;opacity:0;transition:opacity .2s';
  document.body.appendChild(t);
  requestAnimationFrame(() => t.style.opacity = '1');
  setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 200); }, 2500);
}

// ── Poll ──────────────────────────────────────────────────────────────
async function pollStatus() {
  // TG
  const tg = await apiGet('/tg/status');
  if (tg) {
    const wasRunning = tgState.running;
    tgState.running = tg.running;
    tgState.safeMode = tg.safe_mode;
    tgState.sentToday = tg.sent_today;
    tgState.foundToday = tg.found_today || 0;
    tgState.sentTotal = tg.sent_total || 0;
    tgState.maxPerDay = tg.max_per_day;
    if (tg.api_id) tgState.apiId = tg.api_id;
    if (typeof tg.api_hash_set !== 'undefined') tgState.apiHashSet = tg.api_hash_set;
    if (wasRunning !== tgState.running) updateTGButton();
    updateMetrics();
  }

  // HH
  const hh = await apiGet('/hh/status');
  if (hh) {
    const wasRunning = hhState.running;
    hhState.running = hh.running;
    hhState.sentToday = hh.sent_today || 0;
    hhState.foundToday = hh.found_today || 0;
    hhState.totalSent = hh.total_sent || 0;
    hhState.maxPerDay = hh.max_per_day || 20;
    if (wasRunning !== hhState.running) updateHHButton();
    updateMetrics();
  }

  // Recent log
  const l = await apiGet('/tg/logs?lines=200');
  if (l && l.log) {
    const lines = l.log.trim().split('\n').filter(Boolean).reverse();
    const entries = [];
    for (const line of lines) {
      const p = parseLogLine(line, 'tg');
      if (p) entries.push(p);
      if (entries.length >= 6) break;
    }
    // Also check HH log
    const hl = await apiGet('/hh/logs?lines=50');
    if (hl && hl.log) {
      const hlines = hl.log.trim().split('\n').filter(Boolean).reverse();
      for (const line of hlines) {
        const p = parseLogLine(line, 'hh');
        if (p) { entries.push(p); }
        if (entries.length >= 8) break;
      }
      entries.sort((a, b) => b.time.localeCompare(a.time));
    }

    const recentEl = document.getElementById('recentLog');
    if (entries.length && recentEl) {
      fill(recentEl, entries.slice(0, 6).map(e => el('div', { class: 'log-row' }, [
        el('span', { class: 'log-time', text: e.time }),
        el('span', { class: `log-source ${e.source}`, text: e.source.toUpperCase() }),
        el('span', { class: `log-badge ${e.cls}`, text: e.badge }),
        el('span', { style: 'font-size:12px', text: e.text }),
      ])));
    }
  }

  setTimeout(pollStatus, 3000);
}

// ── Init ──────────────────────────────────────────────────────────────
async function init() {
  const cfg = await apiGet('/config');
  if (!cfg) { showToast('Бэкенд недоступен — запустите start_web.bat'); return; }

  tgState.channels = cfg.channels || [];
  tgState.keywords = cfg.keywords || [];
  tgState.exclude = cfg.exclude || [];
  tgState.template = cfg.template || '';
  tgState.safeMode = cfg.safe_mode;
  tgState.parseHistory = cfg.parse_history;
  tgState.maxPerDay = cfg.max_per_day;
  tgState.historyLimit = cfg.history_limit;
  tgState.apiId = cfg.api_id || '';
  tgState.apiHashSet = cfg.api_hash_set || false;

  if (cfg.hh_keywords) hhState.keywords = cfg.hh_keywords;
  if (cfg.hh_exclude) hhState.exclude = cfg.hh_exclude;
  if (cfg.hh_area_ids) hhState.areaIds = cfg.hh_area_ids;
  if (cfg.hh_max_per_day) hhState.maxPerDay = cfg.hh_max_per_day;
  if (cfg.hh_selenium_steps) hhState.seleniumSteps = cfg.hh_selenium_steps;
  if (cfg.hh_cover_letter) {
    const el = document.getElementById('hhCoverLetter');
    if (el) el.value = cfg.hh_cover_letter;
  }
  if (cfg.file_path) {
    const el = document.getElementById('currentFile');
    if (el) el.textContent = cfg.file_path.split(/[\/\\]/).pop();
  }

  const tpl = document.getElementById('templateText');
  if (tpl) tpl.value = tgState.template;

  updateTGButton();
  updateHHButton();
  updateMetrics();
  updateDashboard();
  loadHHVacancies();
  pollStatus();
}

// ── About modal ───────────────────────────────────────────────────────
function showAbout() {
  const m = document.getElementById('aboutModal');
  m.style.display = 'flex';
  m.addEventListener('click', e => { if (e.target === m) hideAbout(); }, { once: true });
}
function hideAbout() {
  document.getElementById('aboutModal').style.display = 'none';
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') hideAbout(); });

document.addEventListener('DOMContentLoaded', init);
