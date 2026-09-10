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
    else if ((key === 'href' || key === 'src') && !isHttpUrl(value)) {
      // Untrusted data (Telegram/hh.ru content) must never reach a
      // navigable attribute with an unvalidated scheme (javascript:, data:,
      // ...). Validating here, inside el(), means every call site gets this
      // for free by construction — a new href/src call site can't reopen
      // the hole the way a call-site-only check could.
      continue;
    }
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

// A stale APP_TOKEN (server restarted, tab left open) makes every /api/*
// call come back 403. The body is `{detail: "invalid app token"}` — a
// truthy object — so callers that only check "did I get something back"
// would otherwise sail past their guard and render `undefined`/`NaN`
// everywhere. Catch the 403 here, once, for both helpers.
function showTokenExpiredBanner() {
  if (document.getElementById('tokenExpiredBanner')) return;
  const b = el('div', { id: 'tokenExpiredBanner', class: 'restart-banner' }, [
    el('span', { text: '⚠️ Сервер был перезапущен — токен устарел. Обновите страницу.' }),
    el('button', {
      style: 'padding:4px 12px;background:var(--red);color:#fff;border:none;border-radius:5px;cursor:pointer;font-size:12px;font-weight:600',
      text: 'Обновить',
      onclick: () => location.reload(),
    }),
  ]);
  document.body.appendChild(b);
}

async function apiGet(path) {
  try {
    const res = await fetch(API + path, { headers: headers() });
    if (res.status === 403) { showTokenExpiredBanner(); return null; }
    return await res.json();
  } catch { return null; }
}
async function apiSend(method, path, body = {}) {
  try {
    const res = await fetch(API + path, {
      method,
      headers: headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    });
    if (res.status === 403) { showTokenExpiredBanner(); return null; }
    return await res.json();
  } catch { return null; }
}
// Загрузка файла: тело — сами байты, имя — заголовком. Отдельный помощник,
// а не сырой fetch на месте вызова, ровно по той же причине, что apiGet и
// apiSend: 403 обязан поднимать баннер протухшего токена, а не-JSON тело не
// должно бросать исключение. Третий вызывающий `fetch` в этом файле — это
// третья копия этих двух правил, и одна из копий однажды отстанет.
async function apiUpload(path, filename, blob) {
  try {
    const res = await fetch(API + path, {
      method: 'POST',
      headers: headers({ 'X-Filename': encodeURIComponent(filename) }),
      body: blob,
    });
    if (res.status === 403) { showTokenExpiredBanner(); return null; }
    return await res.json().catch(() => null);
  } catch { return null; }
}

const apiPost = (path, body) => apiSend('POST', path, body);
const apiPatch = (path, body) => apiSend('PATCH', path, body);

// PATCH /api/config replies { status: 'saved' } on success. A rejected
// patch (422 validation error, e.g. an empty numeric input serialised as
// null, or an out-of-range value) replies { detail: ... } instead, and a
// 403/network failure comes back as `null` from apiSend above. Every save
// handler below must check this before telling the user it saved — before
// this, apiPatch('/config', ...)'s return value was ignored entirely, so a
// rejected save still showed "сохранено".
function configPatchOk(r) {
  return !!r && r.status === 'saved';
}
function configErrorDetail(r) {
  if (!r) return 'Не удалось сохранить: сервер недоступен или токен устарел';
  if (typeof r.detail === 'string') return r.detail;
  if (Array.isArray(r.detail)) {
    return r.detail
      .map(d => (Array.isArray(d.loc) ? d.loc.join('.') + ': ' : '') + (d.msg || ''))
      .join('; ');
  }
  return 'Ошибка сохранения';
}

// ── Пресеты и справочники ─────────────────────────────────────────────
//
// Критерии поиска живут в активном пресете, а не в /api/config: туда уехало
// только глобальное (лимиты, задержки, безопасный режим). Патч критерия,
// отправленный на /config, теперь отвергается с 422 — поэтому все экраны
// критериев ходят через `patchCriteria`.
const presetState = { list: [], activeId: null, criteria: {} };
let dictionaries = null;

async function patchCriteria(patch) {
  if (presetState.activeId === null) { showToast('Активный пресет не загружен'); return false; }
  const r = await apiPatch(`/presets/${presetState.activeId}`, patch);
  if (!configPatchOk(r)) { showToast(configErrorDetail(r)); return false; }
  Object.assign(presetState.criteria, patch);
  return true;
}

async function loadPresets() {
  const list = await apiGet('/presets');
  if (!Array.isArray(list)) return;
  presetState.list = list;
  const active = list.find(p => p.is_active);
  presetState.activeId = active ? active.id : null;
  renderPresetBar();
}

function renderPresetBar() {
  const bar = document.getElementById('presetBar');
  if (!bar) return;
  fill(bar, presetState.list.map(preset => el('div', {
    style: 'display:flex;flex-direction:column;gap:2px;align-items:flex-start',
  }, [
    el('button', {
      class: 'preset-chip' + (preset.is_active ? ' active' : ''),
      text: preset.name,
      'data-action': 'activatePreset',
      'data-arg': String(preset.id),
      disabled: preset.is_active ? 'disabled' : null,
    }),
    el('span', {
      class: 'preset-meta',
      text: `${preset.channels_count} кан. · ${preset.professions_count} проф.`,
    }),
    preset.is_active ? null : el('button', {
      class: 'btn-del',
      text: 'удалить',
      'data-action': 'deletePreset',
      'data-arg': String(preset.id),
    }),
  ])));
  // Активный пресет назван и в полосе состояния — она обязана меняться
  // вместе с этим списком, а не через три секунды до следующего опроса.
  updateStatusBar();
}

async function activatePreset(id) {
  const r = await apiPost(`/presets/${id}/activate`);
  // 409 — воркер не остановился в отведённый бюджет. Пресет при этом НЕ
  // переключён, и показать обратное значило бы соврать: пользователь думал
  // бы, что ищет по новым критериям, пока воркер работает по старым.
  if (!r || r.activated === undefined) {
    showToast(configErrorDetail(r) || 'Не удалось переключить пресет');
    await loadPresets();
    return;
  }
  const stopped = (r.stopped || []).join(', ');
  showToast(stopped ? `Пресет переключён, остановлено: ${stopped}` : 'Пресет переключён');
  await loadPresets();
  await loadSettings();
}

async function createPreset(copy) {
  const input = document.getElementById('newPresetName');
  const name = input.value.trim();
  if (!name) { showToast('Введите название пресета'); return; }
  const body = { name };
  if (copy && presetState.activeId !== null) body.copy_from = presetState.activeId;
  const r = await apiPost('/presets', body);
  if (!r || r.id === undefined) { showToast(configErrorDetail(r)); return; }
  input.value = '';
  showToast('Пресет создан');
  await loadPresets();
}

const copyPreset = () => createPreset(true);

async function deletePreset(id) {
  const r = await apiSend('DELETE', `/presets/${id}`);
  if (!r || r.status !== 'deleted') { showToast(configErrorDetail(r)); return; }
  showToast('Пресет удалён');
  await loadPresets();
}

async function loadDictionaries() {
  if (dictionaries) return dictionaries;
  dictionaries = await apiGet('/dictionaries');
  return dictionaries;
}

function renderChoiceBox(boxId, options, chosen, name) {
  const box = document.getElementById(boxId);
  if (!box || !options) return;
  fill(box, Object.entries(options).map(([code, label]) => {
    const input = el('input', { type: 'checkbox', value: code, class: name });
    if (chosen.includes(code)) input.checked = true;
    return el('label', {
      style: 'display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer',
    }, [input, ' ' + label]);
  }));
}

function chosenCodes(name) {
  return [...document.querySelectorAll(`.${name}:checked`)].map(cb => cb.value);
}

// ── State ─────────────────────────────────────────────────────────────
// `state` mirrors WorkerStatus.state from the backend (stopped / starting /
// running / stopping / error) and is what the toggle buttons render;
// `running` stays as the plain boolean the start/stop handlers branch on.
const tgState = {
  // `epoch` — номер запуска воркера (WorkerManager._epoch на бэкенде).
  // Приходит в каждом GET /api/state и в ответах на start/stop; нужен, чтобы
  // отличить ответ на СВОЮ остановку от ответа про запуск, которого больше
  // нет, — см. isStaleWorkerReply().
  running: false, state: 'stopped', canStart: true, lastError: null, viewSig: null, epoch: 0,
  safeMode: true, parseHistory: false,
  channels: [], keywords: [], exclude: [],
  template: '', maxPerDay: 25, historyLimit: 50,
  sentToday: 0, foundToday: 0, sentTotal: 0,
  apiId: '', apiHashSet: false, autostart: false,
};

const hhState = {
  running: false, state: 'stopped', canStart: true, lastError: null, viewSig: null, epoch: 0,
  keywords: [], exclude: [],
  maxPerDay: 20, sentToday: 0, foundToday: 0, totalSent: 0,
  autostart: false, seleniumSteps: [],
  // HhLoginState из job_monitor/workers/hh.py. Приходит в каждом
  // GET /api/state (hh.login_state), см. applyHHLoginState().
  loginState: 'logged_out',
};

// ── Navigation ────────────────────────────────────────────────────────
// Что подгрузить при открытии экрана. Единственное место, где это
// решается: раньше это была лестница из шести `if (page === ...)`, куда
// новый экран забывали дописать — и он открывался пустым, без ошибки.
const PAGE_LOADERS = {
  overview: async () => {
    renderChannelEdit();
    renderKeywords();
    document.getElementById('templateText').value = tgState.template;
  },
  found: async () => {},
  sent: async () => { await renderChats(); },
  settings: async () => {
    await loadSettings();
    refreshLogs();
  },
};

document.querySelectorAll('.nav-item[data-page]').forEach(item => {
  item.addEventListener('click', async () => {
    const page = item.dataset.page;
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.getElementById('page-' + page).classList.add('active');
    item.classList.add('active');
    const load = PAGE_LOADERS[page];
    if (load) await load();
  });
});

// ── Полоса состояния ──────────────────────────────────────────────────
// Состояние воркеров, режим и активный пресет были разбросаны по дашборду
// и настройкам, а смысл безопасного режима не написан нигде: только
// галочка, до которой надо дойти и вспомнить, что она значит.
const WORKER_STATE_WORDS = {
  stopped: 'остановлен',
  starting: 'запускается',
  running: 'работает',
  stopping: 'останавливается',
  error: 'ошибка',
};

function workerWord(state) {
  return WORKER_STATE_WORDS[state] || WORKER_STATE_WORDS.stopped;
}

function safeModeWords(on) {
  return on
    ? 'Безопасный режим: собираю, не отправляю'
    : 'Отправка включена';
}

function updateStatusBar() {
  const rows = [
    ['sbDotTG', 'sbTG', tgState, 'Telegram'],
    ['sbDotHH', 'sbHH', hhState, 'hh.ru'],
  ];
  for (const [dotId, textId, worker, name] of rows) {
    const dot = document.getElementById(dotId);
    const label = document.getElementById(textId);
    if (dot) dot.className = 'status-dot ' + workerView(worker.state, name, worker.canStart).dot;
    if (label) label.textContent = `${name}: ${workerWord(worker.state)}`;
  }
  const mode = document.getElementById('sbMode');
  if (mode) mode.textContent = safeModeWords(tgState.safeMode);
  const preset = document.getElementById('sbPreset');
  const active = presetState.list.find(item => item.is_active);
  if (preset) preset.textContent = active ? `Пресет: ${active.name}` : 'Пресет не выбран';
}

// ── Worker status UI ──────────────────────────────────────────────────
// A worker is not simply running-or-not. `error` means its task either
// crashed or ignored cancellation; in the second case the manager keeps
// tracking the task, so POST /api/{tg,hh}/start answers 400 "уже запущен".
// Rendering `error` as a plain "Запустить" made a wedged worker look like a
// stopped one and turned that 400 into a mystery — so `state` from
// GET /api/state drives the button, not the running boolean alone.
//
// The same switch also decides WHAT A CLICK DOES (`action`). Splitting those
// two decisions is what produced the bug this function now prevents: the
// label came from `state`, the branch in toggleTG/toggleHH came from the
// `running` boolean, and `running` is false for both `stopping` and `error`.
// A button reading «Остановка HH…» therefore fired POST /api/hh/start, the
// manager saw the still-live task and answered 400 «HH монитор уже запущен».
// Keeping label and action in one arm makes that class of mismatch
// unrepresentable: whatever the button promises is what the click does.
//
// `canStart` comes from GET /api/state (`can_start`, computed by
// WorkerManager.status_dict): in `error` it says whether the task is really
// gone (crashed on its own — start() will work) or still tracked (wedged on
// stop — start() can only 400). `undefined` is treated as "start is worth a
// try", so an older payload without the field keeps the previous behaviour.
function workerView(state, name, canStart) {
  switch (state) {
    case 'running':
      return { dot: 'running', active: true, label: 'Остановить ' + name, action: 'stop' };
    case 'starting':
      return { dot: 'running', active: true, label: 'Запуск ' + name + '…', action: 'stop' };
    case 'stopping':
      // Никакой ветки: stop() уже идёт и ждёт до 10 секунд, повторный stop
      // получит 400 «не запущен», а start — 400 «уже запущен».
      return { dot: 'running', active: true, label: 'Остановка ' + name + '…', action: 'none' };
    case 'error':
      return canStart === false
        ? {
            dot: 'error', active: false, action: 'none',
            label: 'Ошибка ' + name + ' — нужен перезапуск приложения',
          }
        : {
            dot: 'error', active: false, action: 'start',
            label: 'Ошибка ' + name + ' — запустить снова',
          };
    default:
      return { dot: 'stopped', active: false, label: 'Запустить ' + name, action: 'start' };
  }
}

function workerSignature(worker) {
  return `${worker.state}|${worker.canStart === false ? 'wedged' : 'ok'}|${worker.lastError || ''}`;
}

function updateWorkerButton(worker, name, btnId, dotId, alertId, toggleClass) {
  const btn = document.getElementById(btnId);
  if (!btn) return;
  const view = workerView(worker.state, name, worker.canStart);
  btn.className = `btn-toggle ${toggleClass}`
    + (view.active ? ' active' : '')
    + (worker.state === 'error' ? ' worker-error' : '');
  // A click that does nothing must look like it: `stopping` (a stop is
  // already in flight) and a wedged `error` (the task is still tracked, so
  // start() can only 400) leave the button inert, and disabling it is the
  // only way the promise on the button matches what pressing it does.
  btn.disabled = view.action === 'none';
  fill(btn, [el('span', { class: `status-dot ${view.dot}`, id: dotId }), ' ' + view.label]);

  const alert = document.getElementById(alertId);
  if (alert) {
    const failed = worker.state === 'error';
    const wedged = failed && worker.canStart === false;
    alert.textContent = failed
      ? `⚠️ ${name}: ${worker.lastError || 'воркер остановлен с ошибкой'}`
        + (wedged ? ' — задача не отвечает на отмену, помочь может только перезапуск приложения' : '')
      : '';
    alert.style.display = failed ? 'block' : 'none';
  }
  worker.viewSig = workerSignature(worker);
}

function updateTGButton() {
  updateWorkerButton(tgState, 'TG', 'btnToggleTG', 'dotTG', 'tgWorkerAlert', 'btn-toggle-tg');
  updateStatusBar();
}

function updateHHButton() {
  updateWorkerButton(hhState, 'HH', 'btnToggleHH', 'dotHH', 'hhWorkerAlert', 'btn-toggle-hh');
  updateStatusBar();
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
function setWorkerState(worker, state, lastError, canStart) {
  worker.state = state;
  worker.running = state === 'running' || state === 'starting';
  worker.lastError = lastError || null;
  // Optimistic local transitions know their own answer: a fresh `error` we
  // just got back from POST /stop is by definition the wedged kind (the
  // manager kept the task), everything else can be started.
  worker.canStart = canStart === undefined ? state !== 'error' : canStart;
}

// The branch is taken from workerView(), i.e. from exactly the object that
// drew the label — never from `worker.running`, which is false for both
// `stopping` and `error` and used to send those two states down the start
// branch straight into a 400.
function workerToggleAction(worker, name) {
  return workerView(worker.state, name, worker.canStart).action;
}

// Номер запуска из любого ответа бэкенда (GET /api/state, POST /start,
// POST /stop). Отсутствие поля — старый ответ; тогда прежнее знание
// сохраняется, а не обнуляется.
function rememberEpoch(worker, reply) {
  if (reply && typeof reply.epoch === 'number') worker.epoch = reply.epoch;
}

// Устарел ли ответ на остановку.
//
// `POST /api/{tg,hh}/stop` помечен номером запуска, который останавливал
// ИМЕННО ЭТОТ вызов (`WorkerManager.stop()` запоминает epoch на входе, до
// всякого ожидания). Если наш номер уже больше — значит опрос /api/state
// успел увидеть чужой перезапуск, пока мы ждали ответа, и ответ относится
// к запуску, которого больше нет.
//
// Перерисовывать по такому ответу нельзя, и это не косметика: сторож
// остановки, успевший записать `stopped` до чужого `start()`, отдаёт
// именно `{"status":"stopped"}` — поверх уже работающего нового воркера.
// Экран показал бы «остановлен», кнопка предложила бы «Запустить», а она
// получила бы 400 «уже запущен». Тост по такому ответу врёт по той же
// причине. Свежее состояние придёт следующим тиком опроса (раз в 3 с).
function isStaleWorkerReply(worker, reply) {
  return !!reply && typeof reply.epoch === 'number'
    && typeof worker.epoch === 'number'
    && reply.epoch < worker.epoch;
}

// Что сказать пользователю, когда POST /api/{tg,hh}/stop ответил не
// `stopped`. Ответ несёт НАСТОЯЩЕЕ состояние воркера, и не всякое «не
// stopped» — отказ.
//
// Живое состояние в ответе на остановку — как раз не отказ. Сторож остановки
// (`job_monitor/workers/manager.py::_await_stop`) живёт отдельной таской и
// переживает отмену ожидающего; проснувшись, он молчит, если таской уже
// владеет ДРУГОЙ запуск. Сценарий целиком: клиент A жмёт «Остановить» и
// ждёт, воркер гаснет, клиент B успевает нажать «Запустить», сторож
// просыпается и отдаёт A состояние нового воркера —
// {"status":"running","detail":null}. Состояние на экране при этом
// выставляется верно, а тост показывал «Ошибка» — ровно потому, что
// `detail` пуст, а не потому что что-то сломалось.
function stopResultText(reply, name) {
  if (!reply || !reply.status) return 'Ошибка';
  if (reply.status === 'running' || reply.status === 'starting') {
    return `${name} монитор снова работает — его запустили заново`;
  }
  return reply.detail || 'Ошибка';
}

async function toggleTG() {
  const action = workerToggleAction(tgState, 'TG');
  if (action === 'none') return;
  if (action === 'stop') {
    const r = await apiPost('/tg/stop');
    // Ответ про прошлый запуск — не наш ответ: ни состояния, ни тоста.
    if (isStaleWorkerReply(tgState, r)) return;
    if (r && r.status === 'stopped') {
      setWorkerState(tgState, 'stopped'); updateTGButton();
      showToast('TG монитор остановлен');
    } else {
      // Как в toggleHH: POST /api/tg/stop отвечает НАСТОЯЩИМ состоянием.
      // Остановка с истёкшим бюджетом оставляет воркер в `error`, таска
      // остаётся под наблюдением, и следующий start() ответит 400 — покажем
      // это, а не оставим на экране устаревшую кнопку «работает».
      if (r && r.status) { setWorkerState(tgState, r.status, r.detail); updateTGButton(); }
      showToast(stopResultText(r, 'TG'));
    }
  } else {
    const r = await apiPost('/tg/start');
    if (r && r.status === 'started') {
      rememberEpoch(tgState, r);
      setWorkerState(tgState, 'starting'); updateTGButton();
      showToast('TG монитор запущен');
    } else showToast(r?.detail || 'Ошибка запуска TG');
    hideRestartBanner();
  }
}

// ── HH Script control ─────────────────────────────────────────────────
async function toggleHH() {
  const action = workerToggleAction(hhState, 'HH');
  if (action === 'none') return;
  if (action === 'stop') {
    const r = await apiPost('/hh/stop');
    // Ответ про прошлый запуск — не наш ответ: ни состояния, ни тоста.
    if (isStaleWorkerReply(hhState, r)) return;
    if (r && r.status === 'stopped') {
      setWorkerState(hhState, 'stopped'); updateHHButton();
      showToast('HH монитор остановлен');
    } else {
      // POST /api/hh/stop answers with the worker's REAL state: a Selenium
      // step that ignored cancellation leaves it `error` with the task still
      // tracked, so the next start() will refuse. Show that instead of
      // leaving the stale "running" button on screen.
      if (r && r.status) { setWorkerState(hhState, r.status, r.detail); updateHHButton(); }
      showToast(stopResultText(r, 'HH'));
    }
  } else {
    const r = await apiPost('/hh/start');
    if (r && r.status === 'started') {
      rememberEpoch(hhState, r);
      setWorkerState(hhState, 'starting'); updateHHButton();
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
  if (!await patchCriteria({ channels: tgState.channels })) return;
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
  if (!await patchCriteria({
    tg_keywords: tgState.keywords, tg_exclude: tgState.exclude,
  })) return;
  updateDashboard();
  if (tgState.running) { showToast('Сохранено — перезапустите TG'); showRestartBanner(); }
  else showToast('Ключевые слова сохранены');
}

// ── Templates ─────────────────────────────────────────────────────────
async function saveTemplate() {
  tgState.template = document.getElementById('templateText').value;
  if (!await patchCriteria({ template: tgState.template })) return;
  if (tgState.running) { showToast('Сохранено — перезапустите TG'); showRestartBanner(); }
  else showToast('Шаблон сохранён');
}
async function saveHHCoverLetter() {
  const letter = document.getElementById('hhCoverLetter').value;
  if (!await patchCriteria({ hh_cover_letter: letter })) return;
  showToast('Сопроводительное письмо сохранено');
}
// ── Библиотека резюме ─────────────────────────────────────────────────
//
// Раньше здесь жил мёртвый обработчик выбора файла: он обновлял три
// подписи и НИКОГДА не отправлял файл на бэкенд — резюме нельзя было
// приложить вообще (дефект L14). Теперь файл действительно загружается и
// живёт в каталоге данных.
//
// Тело запроса — сами байты файла, имя — в заголовке. Форма multipart не
// используется намеренно: она требует пакета `python-multipart` и заводит
// лишний разборщик недоверенного ввода ради одного поля.

// Delegated `change` handler: every action is called as action(arg, event).
async function uploadResume(_arg, event) {
  const file = event.target.files[0];
  if (!file) return;
  event.target.value = '';   // тот же файл можно выбрать повторно
  const label = document.getElementById('fileZoneLabel');
  if (label) label.textContent = 'Загружаю…';
  const body = await apiUpload('/resumes', file.name, file);
  if (label) label.textContent = 'Нажмите чтобы загрузить файл';
  if (!body || body.id === undefined) {
    showToast(configErrorDetail(body) || 'Не удалось загрузить резюме');
    return;
  }
  showToast('Резюме загружено: ' + file.name);
  await loadResumes();
}

async function loadResumes() {
  const box = document.getElementById('resumeList');
  if (!box) return;
  const items = await apiGet('/resumes');
  if (!Array.isArray(items)) return;
  const chosen = presetState.criteria.resume_id ?? null;
  if (items.length === 0) {
    fill(box, el('div', {
      style: 'font-size:12px;color:var(--muted)',
      text: 'Пока ничего не загружено.',
    }));
    return;
  }
  fill(box, items.map(item => el('div', {
    class: 'resume-row' + (item.id === chosen ? ' chosen' : ''),
  }, [
    // `original_name` — имя, которое дал пользователь; на диске его нет.
    // Рисуется через textContent (el), поэтому разметка в имени безопасна.
    el('span', { class: 'resume-name', text: item.original_name }),
    el('span', {
      class: 'preset-meta',
      text: `${Math.max(1, Math.round(item.size_bytes / 1024))} КБ`,
    }),
    item.id === chosen
      ? el('span', { class: 'preset-meta', text: 'выбрано' })
      : el('button', {
          class: 'btn btn-secondary',
          text: 'Выбрать',
          'data-action': 'chooseResume',
          'data-arg': String(item.id),
        }),
    el('button', {
      class: 'btn-del',
      text: '×',
      'data-action': 'deleteResume',
      'data-arg': String(item.id),
    }),
  ])));
}

async function chooseResume(id) {
  if (!await patchCriteria({ resume_id: parseInt(id, 10) })) return;
  showToast('Резюме выбрано для активного пресета');
  await loadResumes();
}

async function deleteResume(id) {
  const r = await apiSend('DELETE', `/resumes/${id}`);
  // Отказ здесь осмысленный: резюме используется пресетами, и ответ их
  // называет. Показать его дословно полезнее, чем «не удалось».
  if (!r || r.status !== 'deleted') { showToast(configErrorDetail(r)); return; }
  showToast('Резюме удалено');
  await loadResumes();
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

  // HH: критерии — из активного пресета, лимиты и расписание — из настроек.
  const criteria = presetState.criteria;
  hhState.keywords = criteria.professions || [];
  hhState.exclude = criteria.hh_exclude || [];
  renderHHKeywords();

  const dict = await loadDictionaries();
  if (dict) {
    const areaLabel = document.getElementById('hhAreaLabel');
    if (areaLabel) areaLabel.textContent = `код ${dict.area_id}`;
    const experience = document.getElementById('hhExperience');
    if (experience) {
      fill(experience, Object.entries(dict.experience).map(
        ([code, label]) => el('option', { value: code, text: label }),
      ));
      experience.value = criteria.hh_experience || 'noExperience';
    }
    renderChoiceBox('hhScheduleBox', dict.schedule, criteria.hh_schedule || [], 'hh-schedule');
    renderChoiceBox('hhEmploymentBox', dict.employment, criteria.hh_employment || [], 'hh-employment');
  }

  document.getElementById('hhSalaryFrom').value = criteria.hh_salary_from || 0;
  if (criteria.hh_search_period) document.getElementById('hhSearchPeriod').value = criteria.hh_search_period;
  document.getElementById('hhResumeId').value = criteria.hh_resume_id || '';
  document.getElementById('hhCoverLetter').value = criteria.hh_cover_letter || '';
  if (cfg.hh_max_per_day) document.getElementById('hhMaxPerDayInput').value = cfg.hh_max_per_day;
  if (cfg.hh_check_interval) document.getElementById('hhCheckInterval').value = cfg.hh_check_interval / 60;
  if (cfg.hh_autostart !== undefined) document.getElementById('toggleHHAutostart').checked = cfg.hh_autostart;
  if (cfg.hh_selenium_steps) { hhState.seleniumSteps = cfg.hh_selenium_steps; renderSeleniumSteps(); }
  await loadResumes();
  renderHHLoginControls();
  renderHHLoginStatus(hhState.loginState);

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
  const r = await apiPatch('/config', data);
  if (!configPatchOk(r)) { showToast(configErrorDetail(r)); return; }
  tgState.safeMode = data.safe_mode;
  tgState.parseHistory = data.parse_history;
  tgState.maxPerDay = data.max_per_day;
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
  const r = await apiPatch('/config', body);
  if (!configPatchOk(r)) { showToast(configErrorDetail(r)); return; }
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
  // Через apiPost, а не сырым fetch: помощник ловит 403 протухшего токена
  // (баннер вместо молчаливого «неверный код») и не бросает исключение на
  // не-JSON теле ответа 500. HTTP-статус 428 при этом не теряется: ответ
  // на «нужен 2FA» — это ровно {detail: '2FA_REQUIRED'}, различить его
  // можно по телу (api/auth_routes.py::verify_code).
  const r = await apiPost('/auth/verify-code', body);
  if (r && r.detail === '2FA_REQUIRED') {
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
  // Сохранение расходится по двум адресатам, потому что настройки теперь
  // тоже двух видов: критерии поиска — в активный пресет, лимиты и
  // расписание проверок — в глобальные настройки. Регион не отправляется
  // вовсе: он константа приложения (решение D8).
  const criteria = {
    professions: hhState.keywords,
    hh_exclude: hhState.exclude,
    hh_experience: document.getElementById('hhExperience').value,
    hh_salary_from: parseInt(document.getElementById('hhSalaryFrom').value) || 0,
    hh_search_period: parseInt(document.getElementById('hhSearchPeriod').value),
    hh_schedule: chosenCodes('hh-schedule'),
    hh_employment: chosenCodes('hh-employment'),
    hh_resume_id: document.getElementById('hhResumeId').value.trim(),
    hh_cover_letter: document.getElementById('hhCoverLetter').value,
  };
  const global = {
    hh_max_per_day: parseInt(document.getElementById('hhMaxPerDayInput').value),
    hh_check_interval: parseInt(document.getElementById('hhCheckInterval').value) * 60,
    hh_autostart: document.getElementById('toggleHHAutostart').checked,
    hh_selenium_steps: hhState.seleniumSteps,
  };
  if (!await patchCriteria(criteria)) return;
  const r = await apiPatch('/config', global);
  if (!configPatchOk(r)) { showToast(configErrorDetail(r)); return; }
  await loadPresets();
  showToast('HH настройки сохранены');
}

// ── HH Login (L4: два вызова вместо блокирующего input()) ──────────────
const HH_LOGIN_LABELS = {
  logged_out: 'Вход не выполнен',
  browser_open: 'Окно открыто — войдите в hh.ru и нажмите «Я вошёл»',
  logged_in: 'Вход выполнен, сессия сохранена',
};

function renderHHLoginControls() {
  const box = document.getElementById('hhLoginButtons');
  if (!box) return;
  // «Я вошёл» имеет смысл только при открытом окне: без драйвера
  // POST /api/hh/login/confirm честно отвечает 400 (см. его докстринг), и
  // до этого дизейбла последовательность «Закрыть окно» → «Я вошёл» была
  // достижима в два клика. 400 никуда не делся — он нужен любому клиенту,
  // не только этой вкладке, — но кнопка больше не приглашает в него нажать.
  const canConfirm = hhState.loginState === 'browser_open';
  const confirmProps = {
    class: 'btn btn-primary', style: 'background:var(--hh)',
    text: 'Я вошёл, сохранить сессию', onclick: hhLoginConfirm,
  };
  // Только когда true: el() кладёт неизвестные ключи через setAttribute, а
  // `disabled="false"` в HTML — это всё равно disabled.
  if (!canConfirm) confirmProps.disabled = true;
  fill(box, [
    el('button', { class: 'btn btn-secondary', text: 'Открыть вход в hh.ru', onclick: hhLoginStart }),
    el('button', confirmProps),
  ]);
}

function renderHHLoginStatus(state) {
  const statusEl = document.getElementById('hhLoginStatus');
  if (!statusEl) return;
  fill(statusEl, el('span', { text: HH_LOGIN_LABELS[state] || state }));
}

// Единственная точка входа для состояния входа в hh.ru, откуда бы оно ни
// пришло: из периодического GET /api/state или из ответа самой кнопки.
// Раньше состояние читалось отдельным GET /api/hh/login/status ровно один
// раз — при открытии страницы настроек, — поэтому `/api/state.hh.login_state`
// не читал никто, а показанная строка устаревала молча: приложение
// перезапустили, окна нет, а на экране «Окно открыто».
function applyHHLoginState(state) {
  if (!state || state === hhState.loginState) return;
  hhState.loginState = state;
  renderHHLoginStatus(state);
  renderHHLoginControls();
}

async function hhLoginStart() {
  const r = await apiPost('/hh/login/start');
  if (r && r.state) { applyHHLoginState(r.state); showToast('Открываю окно входа в hh.ru'); }
  else showToast(r?.detail || 'Не удалось открыть окно входа');
}

async function hhLoginConfirm() {
  const r = await apiPost('/hh/login/confirm');
  if (!r || !r.state) { showToast(r?.detail || 'Ошибка проверки входа'); return; }
  applyHHLoginState(r.state);
  if (r.state === 'logged_in') showToast('Сессия сохранена');
  else showToast('Вход ещё не подтверждён — войдите в открывшемся окне');
}

async function hhLoginCancel() {
  // Единственный способ погасить окно входа, не подтверждая его: до этого
  // роута Chrome, открытый «Открыть вход в hh.ru», жил до конца сессии
  // пользователя и переживал остановку приложения.
  const r = await apiPost('/hh/login/cancel');
  if (r && r.state) { applyHHLoginState(r.state); showToast('Окно входа закрыто'); }
  else showToast(r?.detail || 'Не удалось закрыть окно входа');
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
        el('div', { class: 'chat-time', text: stampText(c.time) }),
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
  // Через apiPost, а не сырым fetch: 403 протухшего токена поднимает баннер,
  // а не-JSON тело ответа 500 возвращается как null вместо исключения в
  // консоли. Успех — {status: 'sent'} (api/auth_routes.py::send_message).
  const r = await apiPost('/auth/messages/' + encodeURIComponent(currentChat.replace('@', '')), { text });
  inp.disabled = false; inp.focus();
  if (r && r.status === 'sent') await loadChatMessages(currentChat);
  else showToast('Ошибка: ' + ((r && r.detail) || 'не удалось отправить'));
}

// ── HH Vacancies ──────────────────────────────────────────────────────
// hh.ru-scraped data must never drive an <a href>: an unvalidated scheme
// (javascript:, data:, ...) would execute on click with the app token in
// scope. Only allow the schemes a "view on hh.ru" link ever legitimately
// needs. el() itself enforces this for every href/src it sets (see above);
// this predicate is what it calls.
function isHttpUrl(url) {
  return /^https?:\/\//i.test(url || '');
}

// hh.ru vacancy statuses are written by job_monitor/workers/hh.py:
// HH_STATUS_APPLIED ("отклик отправлен"), "пропущено", and
// HH_STATUS_SCENARIO_ERROR ("ошибка сценария") — the last one added when a
// broken Selenium scenario is made terminal. It used to fall through to the
// neutral "waiting" badge, so a permanently failed vacancy looked like one
// still in the queue.
function vacancyStatusClass(status) {
  const st = status || '';
  if (st.includes('ошибка')) return 'status-error';
  if (st.includes('отправлен')) return 'status-sent';
  if (st.includes('пропущено')) return 'status-skip';
  return 'status-wait';
}

async function loadHHVacancies() {
  const vacs = await apiGet('/found/hh');
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
    const st = v.status || '';
    const cls = vacancyStatusClass(st);
    // Всё, что бэкенд знает о вакансии и что помещается в карточку. `city`
    // писался в hh_applications с самого начала и не показывался нигде —
    // одна из «мёртвых колонок» финального ревью.
    const meta = [v.company, v.city, v.salary || 'з/п не указана'].filter(Boolean).join(' · ');
    // No isHttpUrl() check here on purpose: el() validates href/src itself,
    // by construction, so a bad scheme in v.url just never gets attached.
    return el('div', { class: 'vac-card' }, [
      el('div', { style: 'display:flex;align-items:flex-start;justify-content:space-between;gap:8px' }, [
        el('div', {}, [
          el('div', { style: 'font-size:13px;font-weight:600', text: v.title || '' }),
          el('div', { style: 'font-size:11px;color:var(--muted);margin-top:2px', text: meta }),
        ]),
        // `status_source` отвечает на вопрос «это я откликнулся или робот»:
        // с появлением ручных статусов бейдж без него врал бы наполовину.
        el('span', {
          class: `status-badge ${cls}`,
          style: 'flex-shrink:0',
          text: (st || 'ожидание') + (v.status_source === 'человек' ? ' · вручную' : ''),
        }),
      ]),
      // Текст причины, а не только красный бейдж: для HH_STATUS_SCENARIO_ERROR
      // («ошибка сценария») бейдж говорит, ЧТО случилось, а починить сценарий
      // можно, только зная, КАКОЙ шаг не разобрался. Значение недоверенное
      // (в него попадает содержимое hh_selenium_steps), поэтому — `text:`,
      // то есть textContent, как и всё остальное в этом файле.
      v.error ? el('div', { class: 'vac-error', text: v.error }) : null,
      v.url ? el('div', { style: 'margin-top:8px' }, [
        el('a', { href: v.url, target: '_blank', rel: 'noopener noreferrer', style: 'font-size:11px;color:var(--hh);text-decoration:none', text: 'Открыть на HH →' }),
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

// ── Recent activity feed ──────────────────────────────────────────────
// GET /api/state returns `recent`: rows straight from the worker_events
// table (worker, at, kind, detail) written by
// job_monitor/workers/{telegram,hh}.py. This replaces the old approach of
// re-downloading both log files every 3s and reverse-engineering their text.
function eventBadge(kind) {
  switch (kind) {
    case 'sent': return { badge: 'OK', cls: 'badge-ok' };
    case 'applied': return { badge: 'HH', cls: 'badge-ok' };
    case 'vacancy': return { badge: 'VAC', cls: 'badge-skip' };
    case 'skipped': return { badge: 'SKIP', cls: 'badge-skip' };
    case 'steps_invalid': return { badge: 'STEP', cls: 'badge-error' };
    case 'error': return { badge: 'ERR', cls: 'badge-error' };
    case 'login_required': return { badge: 'AUTH', cls: 'badge-safe' };
    default: return { badge: 'SYS', cls: 'badge-skip' };
  }
}

function eventTime(at) {
  const m = /T(\d{2}:\d{2})/.exec(at || '');
  return m ? m[1] : '—';
}

// The API stores and returns timestamps as ISO ("2026-09-08T14:33:12") —
// machine-readable, and that is right for a payload. Rendering it raw is not:
// the chat list showed the full ISO string in a narrow column, seconds and
// all, while the recent-events list next to it showed HH:MM through
// eventTime(). Same database, same field shape, two different looks. Before
// this branch the backend formatted it as "%Y-%m-%d %H:%M"; formatting now
// lives here, on the one side that decides how things look.
function stampText(at) {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(at || '');
  return m ? `${m[1]} ${m[2]}` : (at || '');
}

function renderRecent(events) {
  const recentEl = document.getElementById('recentLog');
  // No events yet: leave the "Ожидание данных..." placeholder from
  // index.html in place rather than blanking the card.
  if (!recentEl || !events.length) return;
  fill(recentEl, events.slice(0, 6).map(e => {
    const view = eventBadge(e.kind);
    const source = e.worker === 'hh' ? 'hh' : 'tg';
    return el('div', { class: 'log-row' }, [
      el('span', { class: 'log-time', text: eventTime(e.at) }),
      el('span', { class: `log-source ${source}`, text: source.toUpperCase() }),
      el('span', { class: `log-badge ${view.cls}`, text: view.badge }),
      el('span', { style: 'font-size:12px', text: (e.detail || '').slice(0, 90) }),
    ]);
  }));
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
// L13: the dashboard used to fire four requests every three seconds
// (/tg/status, /hh/status, /tg/logs, /hh/logs) for one screen. GET
// /api/state (api/routes_state.py) aggregates all of it into one response.
function applyWorkerState(worker, payload, update) {
  // Номер запуска — первым делом: именно он делает следующий ответ на
  // остановку узнаваемо устаревшим (см. isStaleWorkerReply).
  rememberEpoch(worker, payload);
  worker.state = payload.state || (payload.running ? 'running' : 'stopped');
  worker.running = !!payload.running;
  worker.lastError = payload.last_error || null;
  // Absent (older payload) means "assume a start is possible" — the previous
  // behaviour. Only an explicit false marks the wedged worker whose task the
  // manager is still tracking.
  worker.canStart = payload.can_start !== false;
  // Re-render only when what the button shows actually changed: this runs
  // every 3s and rebuilding the node each tick would fight the :active and
  // :hover states of a button the user is pressing.
  if (workerSignature(worker) !== worker.viewSig) update();
}

// `apiGet` returns whatever JSON came back, and a 4xx/5xx with a JSON body
// is `{detail: "..."}` — truthy. A bare `if (state)` therefore sailed past
// the guard, `state.tg || {}` gave an empty object, and the dashboard drew a
// running worker as stopped with every counter reset to 0. Check the SHAPE,
// not the truthiness. (The common Starlette 500 is plain text, so `res.json()`
// throws and apiGet already returns null — this closes the JSON-bodied case.)
function isStatePayload(value) {
  return !!value && typeof value === 'object'
    && !!value.tg && typeof value.tg === 'object'
    && !!value.hh && typeof value.hh === 'object';
}

// Перепланировка стоит в `finally`, а не последней строкой тела. apiGet() свои
// сбои уже глотает, но applyWorkerState / updateMetrics / renderRecent — нет:
// одно исключение в любой из них останавливало опрос НАВСЕГДА. Раньше ценой
// были устаревшие цифры; с тех пор как кнопка воркера дизейблится по
// `state` из этого же ответа, ценой стала ещё и кнопка, залипшая в
// `disabled` без единого объяснения на экране. Ошибка при этом не глотается
// молча — она уходит в консоль, — но цикл жизни опроса от неё не зависит.
async function pollStatus() {
  try {
    const state = await apiGet('/state');
    if (isStatePayload(state)) {
      const tg = state.tg;
      tgState.safeMode = tg.safe_mode;
      tgState.sentToday = tg.sent_today || 0;
      tgState.foundToday = tg.found_today || 0;
      tgState.sentTotal = tg.sent_total || 0;
      if (tg.max_per_day) tgState.maxPerDay = tg.max_per_day;
      if (typeof tg.api_hash_set !== 'undefined') tgState.apiHashSet = tg.api_hash_set;
      applyWorkerState(tgState, tg, updateTGButton);

      const hh = state.hh;
      hhState.sentToday = hh.sent_today || 0;
      hhState.foundToday = hh.found_today || 0;
      hhState.totalSent = hh.total_sent || 0;
      hhState.maxPerDay = hh.max_per_day || 20;
      applyWorkerState(hhState, hh, updateHHButton);
      applyHHLoginState(hh.login_state);

      updateMetrics();
      renderRecent(state.recent || []);
      renderDataDirWarning(state.data_dir_warning || null);
    }
  } catch (error) {
    console.error('pollStatus:', error);
  } finally {
    setTimeout(pollStatus, 3000);
  }
}

function renderDataDirWarning(service) {
  const box = document.getElementById('dataDirWarning');
  if (!box) return;
  if (!service) { box.style.display = 'none'; fill(box, []); return; }
  box.style.display = '';
  // Через textContent, как и всё остальное: имя сервиса приходит с
  // бэкенда, но правило «строим узлы, а не HTML-строки» исключений не
  // знает — иначе оно перестаёт быть правилом.
  fill(box, el('span', {
    text: `⚠️ Каталог данных лежит в папке ${service}. В нём файл сессии `
      + 'Telegram: его достаточно для входа в аккаунт в обход 2FA. '
      + 'Перенесите каталог за пределы синхронизируемой папки '
      + '($JOB_MONITOR_DATA_DIR).',
  }));
}

// ── Init ──────────────────────────────────────────────────────────────
async function init() {
  const cfg = await apiGet('/config');
  if (!cfg) { showToast('Бэкенд недоступен — запустите start_web.bat'); return; }

  // Пресеты грузятся ДО критериев: без активного пресета читать нечего.
  await loadPresets();
  await loadActiveCriteria();

  tgState.safeMode = cfg.safe_mode;
  tgState.parseHistory = cfg.parse_history;
  tgState.maxPerDay = cfg.max_per_day;
  tgState.historyLimit = cfg.history_limit;
  tgState.apiId = cfg.api_id || '';
  tgState.apiHashSet = cfg.api_hash_set || false;
  if (cfg.hh_max_per_day) hhState.maxPerDay = cfg.hh_max_per_day;
  if (cfg.hh_selenium_steps) hhState.seleniumSteps = cfg.hh_selenium_steps;

  updateTGButton();
  updateHHButton();
  updateMetrics();
  updateDashboard();
  loadResumes();
  loadHHVacancies();
  pollStatus();
}

async function loadActiveCriteria() {
  if (presetState.activeId === null) return;
  const preset = await apiGet(`/presets/${presetState.activeId}`);
  if (!preset || !preset.criteria) return;
  presetState.criteria = preset.criteria;

  tgState.channels = preset.criteria.channels || [];
  tgState.keywords = preset.criteria.tg_keywords || [];
  tgState.exclude = preset.criteria.tg_exclude || [];
  tgState.template = preset.criteria.template || '';
  hhState.keywords = preset.criteria.professions || [];
  hhState.exclude = preset.criteria.hh_exclude || [];

  const tpl = document.getElementById('templateText');
  if (tpl) tpl.value = tgState.template;
  const letter = document.getElementById('hhCoverLetter');
  if (letter) letter.value = preset.criteria.hh_cover_letter || '';
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

// ── Event delegation ──────────────────────────────────────────────────
// index.html carries no on*= attributes any more: every control declares
// data-action / data-change-action / data-enter-action and the three
// listeners below dispatch it. That is precisely what lets
// job_monitor/security.py ship `script-src 'self'` with no 'unsafe-inline'
// — a single surviving handler attribute would force the policy back open.
// Contract: every action is invoked as action(arg, event), where `arg` is
// the element's data-arg attribute (undefined when absent).

function pickFile(_arg, event) {
  const input = document.getElementById('fileInput');
  // #fileInput lives inside #fileZone, so the synthetic click from
  // input.click() bubbles straight back into this handler. Measured in
  // Chrome 152: without this guard pickFile runs twice per user click and
  // input.click() is called a second time — harmless only because the DOM
  // spec's "click in progress" flag makes that second call a no-op, which is
  // what stops it being unbounded recursion. Returning early keeps the
  // dispatch honest instead of relying on that flag.
  if (!input || event.target === input) return;
  input.click();
}

function reloadChat() {
  return loadChatMessages(currentChat);
}

const ACTIONS = {
  showAbout,
  hideAbout,
  toggleTG,
  toggleHH,
  reloadChat,
  sendChatMessage,
  saveChannels,
  addChannel,
  saveKeywords,
  addKw,
  addEx,
  saveTemplate,
  pickFile,
  uploadResume,
  chooseResume,
  deleteResume,
  activatePreset,
  createPreset,
  copyPreset,
  deletePreset,
  hhLoginCancel,
  saveHHCoverLetter,
  saveTGSettings,
  saveApiKeys,
  sendAuthCode,
  verifyAuthCode,
  saveHHSettings,
  addHHKw,
  addHHEx,
  addStep,
  showLog,
  clearConsole,
  refreshLogs,
};

function runAction(name, target, event) {
  const action = ACTIONS[name];
  if (!action) return;
  action(target.dataset.arg, event);
}

document.addEventListener('click', event => {
  const target = event.target.closest?.('[data-action]');
  if (target) runAction(target.dataset.action, target, event);
});

document.addEventListener('change', event => {
  const target = event.target.closest?.('[data-change-action]');
  if (target) runAction(target.dataset.changeAction, target, event);
});

document.addEventListener('keydown', event => {
  // Space activates only an element that declares role="button" (the file
  // drop zone). Accepting it everywhere would swallow the space bar inside
  // #chatInput, which carries data-enter-action too.
  const activates = event.key === 'Enter'
    || (event.key === ' ' && event.target.getAttribute?.('role') === 'button');
  if (!activates) return;
  const target = event.target.closest?.('[data-enter-action]');
  if (!target) return;
  event.preventDefault();
  runAction(target.dataset.enterAction, target, event);
});

document.addEventListener('DOMContentLoaded', init);
