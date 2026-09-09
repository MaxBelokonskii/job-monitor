"""Invariants for the delegated-event frontend (L13 + strict script CSP).

These assertions belong with `tests/test_frontend_safety.py` by subject, but
that file is a tripwire from the security plan that this task must not
touch, so the new pins live here instead. Together the two modules cover:
safety.py pins "untrusted content can never become markup or a navigable
scheme"; this module pins "no script ever executes from an attribute", which
is what lets the CSP drop `script-src 'unsafe-inline'`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import requires_node

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
APP_JS = FRONTEND_DIR / "app.js"
INDEX_HTML = FRONTEND_DIR / "index.html"
STYLE_CSS = FRONTEND_DIR / "style.css"

# Маркер и его причина — общие на весь набор, в tests/conftest.py: там же
# живёт итоговое предупреждение о том, что именно осталось непроверенным.
skip_without_node = requires_node

# `\son` and not just `on`: `<div data-on...>` or a word ending in "on" must
# not match, but every real handler attribute is preceded by whitespace.
INLINE_HANDLER = re.compile(r"""\son[a-z]+\s*=\s*["']""", re.IGNORECASE)
SCRIPT_TAG = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.IGNORECASE | re.DOTALL)
DATA_ACTION_ATTR = re.compile(
    r"""\bdata-(?:action|change-action|enter-action)\s*=\s*["']([^"']+)["']"""
)


def _index_source() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _app_source() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _extract_function_source(name: str) -> str:
    """Extract a top-level function's source verbatim from app.js.

    Same convention as tests/test_frontend_safety.py: top-level functions in
    this file close with a `}` alone at column 0.
    """
    pattern = rf"(?:async )?function {re.escape(name)}\(.*?\) \{{.*?\n\}}"
    match = re.search(pattern, _app_source(), re.DOTALL)
    assert match, f"{name}() not found in app.js"
    return match.group(0)


def _run_node(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)


# ── No script may execute from an attribute or an inline <script> ──────


def test_index_has_no_inline_event_handlers() -> None:
    found = INLINE_HANDLER.findall(_index_source())
    assert not found, (
        f"inline handler attributes still in index.html: {found} — handlers must be "
        "attached with addEventListener via the data-action map, otherwise "
        "script-src 'unsafe-inline' cannot be dropped from the CSP."
    )


def test_index_has_no_inline_script_bodies() -> None:
    for attrs, body in SCRIPT_TAG.findall(_index_source()):
        assert "src=" in attrs, "inline <script> body found — CSP forbids it"
        assert not body.strip(), "a <script src=...> must not also carry a body"


def test_csp_script_src_forbids_unsafe_inline(client) -> None:
    csp = client.get("/").headers["content-security-policy"]
    script_src = next(p for p in csp.split("; ") if p.startswith("script-src"))
    assert "'unsafe-inline'" not in script_src, script_src
    assert "'unsafe-eval'" not in script_src, script_src
    assert "'self'" in script_src, script_src


def test_csp_style_src_still_allows_inline(client) -> None:
    """Deliberate exception, pinned so nobody "tightens" it and breaks the UI:
    the markup uses inline `style` attributes throughout and a style attribute
    is not a script-execution vector."""
    csp = client.get("/").headers["content-security-policy"]
    style_src = next(p for p in csp.split("; ") if p.startswith("style-src"))
    assert "'unsafe-inline'" in style_src, style_src


# ── One poll request per tick (L13) ───────────────────────────────────


def test_poll_uses_the_single_state_endpoint() -> None:
    poll = _extract_function_source("pollStatus")
    calls = re.findall(r"apiGet\(\s*'([^']+)'", poll)
    assert calls == ["/state"], (
        f"pollStatus() issues {len(calls)} requests per tick ({calls}); the dashboard "
        "must refresh from the single aggregated GET /api/state."
    )


def _app_code_without_comments() -> str:
    """app.js minus its `//` comments.

    The endpoints removed here are named in the comments that explain why
    they were removed — worth keeping, so the invariant is asserted against
    code only.
    """
    return "\n".join(
        re.sub(r"(^|\s)//.*$", "", line) for line in _app_source().splitlines()
    )


def test_app_js_no_longer_polls_the_per_worker_status_endpoints() -> None:
    code = _app_code_without_comments()
    assert "/state" in code
    assert "/tg/status" not in code
    assert "/hh/status" not in code
    assert "/tg/logs?lines=200" not in code, "the dashboard log feed comes from /api/state"
    assert "/hh/logs?lines=50" not in code, "the dashboard log feed comes from /api/state"


# ── Delegation map and markup agree ───────────────────────────────────


def _action_map_keys() -> set[str]:
    match = re.search(r"const ACTIONS = \{\n(.*?)\n\};", _app_source(), re.DOTALL)
    assert match, "ACTIONS map not found in app.js"
    keys = set()
    for line in match.group(1).splitlines():
        entry = re.match(r"\s*([A-Za-z_$][\w$]*)\s*,\s*(?://.*)?$", line)
        if entry:
            keys.add(entry.group(1))
    assert keys, "ACTIONS map parsed as empty — did its formatting change?"
    return keys


def _markup_action_names() -> set[str]:
    return set(DATA_ACTION_ATTR.findall(_index_source()))


def test_every_markup_action_has_a_handler() -> None:
    missing = _markup_action_names() - _action_map_keys()
    assert not missing, f"data-action(s) with no entry in ACTIONS: {sorted(missing)} — dead buttons"


def test_every_handler_is_reachable_from_markup() -> None:
    unused = _action_map_keys() - _markup_action_names()
    assert not unused, f"ACTIONS entries no markup can reach: {sorted(unused)}"


def test_markup_carries_the_converted_handlers() -> None:
    """The 30 attributes removed from index.html must reappear as data-actions,
    not just vanish."""
    expected = {
        "showAbout", "hideAbout", "toggleTG", "toggleHH",
        "reloadChat", "sendChatMessage",
        "saveChannels", "addChannel", "saveKeywords", "addKw", "addEx",
        "saveTemplate", "pickFile", "onFileSelect", "saveHHCoverLetter",
        "saveTGSettings", "saveApiKeys", "sendAuthCode", "verifyAuthCode",
        "saveHHSettings", "addHHKw", "addHHEx", "addStep",
        "showLog", "clearConsole", "refreshLogs",
    }
    assert expected <= _markup_action_names(), sorted(expected - _markup_action_names())


def test_log_tabs_pass_their_argument_through_data_arg() -> None:
    source = _index_source()
    assert re.search(r'data-action="showLog"\s+data-arg="tg"', source)
    assert re.search(r'data-action="showLog"\s+data-arg="hh"', source)


def test_chat_input_sends_on_enter_via_a_delegated_keydown() -> None:
    assert re.search(
        r'id="chatInput"[^>]*data-enter-action="sendChatMessage"'
        r'|data-enter-action="sendChatMessage"[^>]*id="chatInput"',
        _index_source(),
        re.DOTALL,
    ), "#chatInput lost its Enter-to-send binding"


def test_about_button_hover_moved_to_css() -> None:
    assert ".btn-about:hover" in STYLE_CSS.read_text(encoding="utf-8"), (
        "the two onmouseover/onmouseout attributes must become a CSS :hover rule"
    )


# ── Carried-over fix 1: the "ошибка сценария" vacancy status ───────────


def test_error_vacancy_status_has_its_own_css_class() -> None:
    assert ".status-error" in STYLE_CSS.read_text(encoding="utf-8")


@skip_without_node
def test_vacancy_status_class_marks_scenario_errors_as_errors() -> None:
    """job_monitor/workers/hh.py marks a vacancy "ошибка сценария" when a
    broken Selenium scenario is made terminal. Before this, the badge mapper
    knew only "отправлен"/"пропущено", so a failed vacancy rendered in the
    neutral waiting colour."""
    cases = [
        ("отклик отправлен", "status-sent"),
        ("пропущено", "status-skip"),
        ("ошибка сценария", "status-error"),
        ("", "status-wait"),
        (None, "status-wait"),
    ]
    checks = "\n".join(
        f"if (vacancyStatusClass({json.dumps(status)}) !== {json.dumps(cls)}) "
        f"{{ console.error('FAIL', {json.dumps(status)}, vacancyStatusClass({json.dumps(status)})); "
        "process.exitCode = 1; }"
        for status, cls in cases
    )
    result = _run_node(f"{_extract_function_source('vacancyStatusClass')}\n{checks}")
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


# ── Carried-over fix 2: an `error` worker must not read as "stopped" ───


@skip_without_node
def test_worker_view_distinguishes_error_from_stopped() -> None:
    """/api/state reports `state` next to `running`; a wedged worker is
    state=error with running=false, and start() then refuses with a 400 the
    user cannot interpret. The button must show the error instead of an
    ordinary "Запустить"."""
    script = "\n".join((
        _extract_function_source("workerView"),
        """
        const stopped = workerView('stopped', 'TG');
        const running = workerView('running', 'TG');
        const failed  = workerView('error', 'TG');
        function check(name, cond) {
          if (!cond) { console.error('FAIL:', name); process.exitCode = 1; }
        }
        check('stopped label', stopped.label === 'Запустить TG');
        check('stopped dot', stopped.dot === 'stopped');
        check('stopped not active', stopped.active === false);
        check('running label', running.label === 'Остановить TG');
        check('running dot', running.dot === 'running');
        check('running active', running.active === true);
        check('error dot differs from stopped', failed.dot === 'error');
        check('error label mentions the error', /Ошибк/.test(failed.label));
        check('error label differs from stopped', failed.label !== stopped.label);
        check('starting counts as busy', workerView('starting', 'HH').active === true);
        check('stopping counts as busy', workerView('stopping', 'HH').active === true);
        """,
    ))
    result = _run_node(script)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


def test_worker_error_banner_exists_in_markup() -> None:
    source = _index_source()
    assert 'id="tgWorkerAlert"' in source
    assert 'id="hhWorkerAlert"' in source


def test_error_dot_has_its_own_css() -> None:
    assert ".status-dot.error" in STYLE_CSS.read_text(encoding="utf-8")


# ── The recent-activity feed reads worker_events, not log text ─────────


@skip_without_node
def test_recent_event_badges_cover_every_kind_the_workers_emit() -> None:
    """Kinds written by job_monitor/workers/{telegram,hh}.py."""
    kinds = ["sent", "vacancy", "applied", "skipped", "steps_invalid",
             "login_required", "error", "something_new"]
    checks = "\n".join(
        f"{{ const v = eventBadge({json.dumps(kind)}); "
        "if (!v || !v.badge || !v.cls) { console.error('FAIL', " + json.dumps(kind) + "); "
        "process.exitCode = 1; } }"
        for kind in kinds
    )
    extra = """
    if (eventBadge('error').cls !== 'badge-error') { console.error('FAIL error cls'); process.exitCode = 1; }
    if (eventBadge('steps_invalid').cls !== 'badge-error') { console.error('FAIL steps cls'); process.exitCode = 1; }
    if (eventBadge('sent').cls !== 'badge-ok') { console.error('FAIL sent cls'); process.exitCode = 1; }
    if (eventBadge('applied').cls !== 'badge-ok') { console.error('FAIL applied cls'); process.exitCode = 1; }
    """
    result = _run_node(f"{_extract_function_source('eventBadge')}\n{checks}\n{extra}")
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


def test_dead_log_text_parser_is_gone() -> None:
    assert "parseLogLine" not in _app_source(), (
        "the dashboard feed now comes from worker_events via /api/state; the "
        "log-text scraper it replaced must not linger"
    )


# ── Каждый вызов API идёт через общий помощник ────────────────────────


def test_only_the_api_helpers_call_fetch_directly() -> None:
    """`sendChatMessage` и `verifyAuthCode` ходили сырым `fetch`.

    Два следствия, оба реальные: `.json()` на не-JSON теле ответа 500
    бросало исключение в консоль, а 403 протухшего токена проходил мимо
    баннера, который план 1 построил ровно для этого случая (см.
    `showTokenExpiredBanner`). Единственные законные места вызова `fetch` —
    сами помощники `apiGet` и `apiSend`.
    """
    source = _app_source()
    assert source.count("fetch(") == 2, (
        "fetch() вызывается вне apiGet/apiSend — 403 пройдёт мимо баннера "
        "протухшего токена, а не-JSON тело ответа бросит исключение"
    )
    for helper in ("apiGet", "apiSend"):
        assert "fetch(" in _extract_function_source(helper)
    for caller in ("sendChatMessage", "verifyAuthCode"):
        assert "fetch(" not in _extract_function_source(caller)


# ── Кнопка воркера: подпись и ветка приходят из одного места ──────────


def _maybe_extract(name: str) -> str:
    """Как `_extract_function_source`, но пустая строка, если функции нет.

    Нужно, чтобы тест на поведение падал на СТАРОМ коде с содержательным
    сообщением («счётчики обнулились»), а не с «функции нет в app.js»:
    исчезновение помощника не должно превращать проверку свойства в
    проверку присутствия имени.
    """
    pattern = rf"(?:async )?function {re.escape(name)}\(.*?\) \{{.*?\n\}}"
    match = re.search(pattern, _app_source(), re.DOTALL)
    return match.group(0) if match else ""


NODE_CHECK_HELPER = """
function check(name, cond) {
  if (!cond) { console.error('FAIL:', name); process.exitCode = 1; }
}
function same(name, got, want) {
  check(name + ' (got ' + JSON.stringify(got) + ', want ' + JSON.stringify(want) + ')',
        JSON.stringify(got) === JSON.stringify(want));
}
"""


@skip_without_node
def test_worker_view_label_and_action_come_from_the_same_arm() -> None:
    """Задача 6 перевела на настоящий `state` только РЕНДЕР. Решение «жать
    start или stop» осталось на булеве `running`, ложном и для `stopping`, и
    для `error`, — кнопка «Остановка HH…» отправляла POST /api/hh/start.
    `workerView` теперь отдаёт и подпись, и действие, так что разойтись им
    больше негде."""
    script = "\n".join((
        _maybe_extract("workerView"),
        NODE_CHECK_HELPER,
        """
        same('stopped',  workerView('stopped', 'TG').action, 'start');
        same('running',  workerView('running', 'TG').action, 'stop');
        same('starting', workerView('starting', 'TG').action, 'stop');
        same('stopping', workerView('stopping', 'TG').action, 'none');
        same('error, task gone',   workerView('error', 'TG', true).action, 'start');
        same('error, task wedged', workerView('error', 'TG', false).action, 'none');
        same('error, unknown',     workerView('error', 'TG').action, 'start');

        // Обещание подписи должно совпадать с действием.
        for (const state of ['stopped', 'running', 'starting', 'stopping', 'error']) {
          for (const canStart of [true, false, undefined]) {
            const v = workerView(state, 'HH', canStart);
            check(state + '/' + canStart + ': есть действие',
                  ['start', 'stop', 'none'].includes(v.action));
            if (/Остановить/.test(v.label)) check(state + ': «Остановить» = stop', v.action === 'stop');
            if (/^Запустить/.test(v.label)) check(state + ': «Запустить» = start', v.action === 'start');
            if (/Остановка/.test(v.label)) check(state + ': «Остановка…» ничего не делает', v.action === 'none');
            if (/запустить снова/.test(v.label)) check(state + ': «снова» = start', v.action === 'start');
            if (/перезапуск приложения/.test(v.label))
              check(state + ': «перезапуск» ничего не делает', v.action === 'none');
          }
        }
        """,
    ))
    result = _run_node(script)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


@skip_without_node
def test_toggle_branches_on_state_not_on_the_running_boolean() -> None:
    """Сценарий отказа целиком: пользователь жмёт «Остановить HH»,
    `manager.stop()` ставит `stopping` и ждёт до 10 секунд, опрос через 3
    секунды рисует «Остановка HH…», пользователь жмёт ещё раз — и получает
    400 «HH монитор уже запущен», потому что ветка бралась из `running`.
    То же в `error` с ещё живой таской: «запустить снова» всегда даёт 400.

    Проверяется НЕ форма кода, а какие запросы уходят на каждый клик."""
    sources = "\n".join(
        _maybe_extract(name) for name in
        ("workerView", "workerToggleAction", "setWorkerState", "stopResultText",
         "toggleTG", "toggleHH")
    )
    harness = """
    let posted = [];
    async function apiPost(path) {
      posted.push(path);
      return path.endsWith('/stop') ? { status: 'stopped' } : { status: 'started' };
    }
    function updateTGButton() {}
    function updateHHButton() {}
    function showToast() {}
    function hideRestartBanner() {}
    const tgState = {};
    const hhState = {};

    const cases = [
      ['stopped',  false, true,  ['/stop_or_start:start']],
      ['running',  true,  true,  ['/stop_or_start:stop']],
      ['starting', true,  true,  ['/stop_or_start:stop']],
      ['stopping', false, true,  []],
      ['error',    false, true,  ['/stop_or_start:start']],
      ['error',    false, false, []],
    ];

    (async () => {
      for (const [worker, state, toggle, prefix] of
           [[tgState, null, toggleTG, '/tg'], [hhState, null, toggleHH, '/hh']]) {
        for (const [st, running, canStart, want] of cases) {
          posted = [];
          worker.state = st; worker.running = running; worker.canStart = canStart;
          await toggle();
          const expected = want.map(w => prefix + (w.endsWith(':stop') ? '/stop' : '/start'));
          same(prefix + ' ' + st + ' canStart=' + canStart, posted, expected);
        }
      }
    })();
    """
    result = _run_node(f"{sources}\n{NODE_CHECK_HELPER}\n{harness}")
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


# ── Опрос не принимает тело ошибки за состояние ───────────────────────


@skip_without_node
def test_poll_ignores_a_json_error_body() -> None:
    """`if (state)` истинно и для `{"detail": ...}`: `state.tg || {}` давало
    пустой объект, работающий воркер рисовался остановленным, а счётчики
    обнулялись. Проверяется поведение pollStatus, а не наличие проверки."""
    sources = "\n".join(_maybe_extract(name) for name in ("isStatePayload", "pollStatus"))
    harness = """
    globalThis.setTimeout = () => {};
    let applied = 0;
    let rendered = 0;
    function applyWorkerState() { applied++; }
    function updateTGButton() {}
    function updateHHButton() {}
    function updateMetrics() {}
    function renderRecent() { rendered++; }
    const tgState = { state: 'running', running: true, canStart: true,
                      sentToday: 7, foundToday: 3, sentTotal: 9, maxPerDay: 25 };
    const hhState = { state: 'running', running: true, canStart: true,
                      sentToday: 5, foundToday: 2, totalSent: 8, maxPerDay: 20 };
    let reply = null;
    async function apiGet() { return reply; }

    (async () => {
      for (const bad of [{ detail: 'HH монитор уже запущен' }, { tg: null, hh: null }, 'boom', null]) {
        reply = bad;
        await pollStatus();
      }
      same('applyWorkerState не вызывался', applied, 0);
      same('счётчик TG не тронут', tgState.sentToday, 7);
      same('счётчик HH не тронут', hhState.totalSent, 8);
      same('состояние TG не тронуто', tgState.state, 'running');
      same('лента не перерисована', rendered, 0);

      reply = { tg: { state: 'stopped', running: false, sent_today: 1 },
                hh: { state: 'running', running: true, total_sent: 2 }, recent: [] };
      await pollStatus();
      same('нормальный ответ применён', applied, 2);
      same('счётчик HH обновлён', hhState.totalSent, 2);
    })();
    """
    result = _run_node(f"{sources}\n{NODE_CHECK_HELPER}\n{harness}")
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


@skip_without_node
def test_a_throwing_renderer_does_not_kill_the_poll_loop() -> None:
    """Перепланировка обязана стоять в `finally`.

    `setTimeout(pollStatus, 3000)` был последней строкой тела: apiGet() свои
    сбои глотает сам, но applyWorkerState / updateMetrics / renderRecent —
    нет, и одно исключение в любой из них останавливало опрос НАВСЕГДА.
    Раньше ценой были устаревшие цифры; с тех пор как кнопка воркера
    дизейблится по `state` из этого же ответа, ценой стала ещё и кнопка,
    залипшая в `disabled` — без баннера и без объяснения.
    """
    sources = "\n".join(_maybe_extract(name) for name in ("isStatePayload", "pollStatus"))
    harness = """
    let scheduled = 0;
    globalThis.setTimeout = () => { scheduled++; };
    let boom = null;
    function applyWorkerState() { if (boom === 'apply') throw new Error('apply boom'); }
    function updateTGButton() {}
    function updateHHButton() {}
    function updateMetrics() { if (boom === 'metrics') throw new Error('metrics boom'); }
    function renderRecent() { if (boom === 'recent') throw new Error('recent boom'); }
    const tgState = {}; const hhState = {};
    const payload = { tg: { state: 'running', running: true },
                      hh: { state: 'running', running: true }, recent: [] };
    async function apiGet() { return payload; }

    (async () => {
      for (const which of ['apply', 'metrics', 'recent', null]) {
        boom = which;
        await pollStatus();
      }
      same('опрос перепланирован после каждого тика', scheduled, 4);
    })();
    """
    result = _run_node(f"{sources}\n{NODE_CHECK_HELPER}\n{harness}")
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


# ── Ровно один периодический таймер во всём app.js (L13) ──────────────


FUNCTION_DEF = re.compile(r"^(?:async )?function ([A-Za-z_$][\w$]*)\(", re.MULTILINE)
SET_TIMEOUT_CALL = re.compile(r"setTimeout\(([^;]*?),\s*[\d_]+\s*\)", re.DOTALL)


def _self_rescheduling_functions() -> set[str]:
    """Функции, которые перезапускают сами себя через setTimeout.

    Такая пара — это периодический таймер, просто записанный не через
    setInterval.
    """
    found = set()
    for name in set(FUNCTION_DEF.findall(_app_source())):
        body = "\n".join(
            re.sub(r"(^|\s)//.*$", "", line)
            for line in _extract_function_source(name).splitlines()
        )
        for callback in SET_TIMEOUT_CALL.findall(body):
            if re.search(rf"\b{re.escape(name)}\b", callback):
                found.add(name)
    return found


def test_app_js_has_exactly_one_periodic_timer() -> None:
    """Глобальный инвариант вместо пересказа текущего кода.

    Прежние два пина смотрели только внутрь `pollStatus` и на четыре
    конкретных пути, поэтому новый `setInterval(refreshLogs, 3000)` в любом
    другом месте файла проходил мимо них — а именно возврат к нескольким
    независимым опросам и есть дефект L13. Здесь считается ВЕСЬ файл:
    периодических таймеров ровно один, и это опрос /api/state.
    """
    code = _app_code_without_comments()
    intervals = re.findall(r"\bsetInterval\s*\(", code)
    recurring = _self_rescheduling_functions()
    assert not intervals, (
        f"в app.js появился setInterval ({len(intervals)} шт.) — дашборд обновляется "
        "единственным циклом pollStatus(), второй таймер вернёт L13"
    )
    assert recurring == {"pollStatus"}, (
        f"периодические таймеры в app.js: {sorted(recurring) or 'ни одного'}; должен быть "
        "ровно один — pollStatus(), пересоздающий себя после каждого GET /api/state"
    )


# ── Доступность ───────────────────────────────────────────────────────


def test_file_zone_is_operable_from_the_keyboard() -> None:
    zone = re.search(r"<div class=\"file-zone\"[^>]*>", _index_source(), re.DOTALL)
    assert zone, "#fileZone не найден"
    markup = zone.group(0)
    assert 'tabindex="0"' in markup, "в зону выбора файла нельзя попасть табом"
    assert 'role="button"' in markup, "скринридер не объявит зону кнопкой"
    assert 'data-enter-action="pickFile"' in markup, (
        "Enter на зоне ничего не делает — механизм data-enter-action уже есть в проекте"
    )


def test_space_activation_is_limited_to_role_button() -> None:
    """Пробел не должен активировать `#chatInput`, у которого тоже есть
    data-enter-action, — иначе в поле чата нельзя набрать пробел."""
    source = _app_source()
    assert "role" in source and "event.key === ' '" in source
    assert re.search(r"event\.key === ' '.*?role.*?===\s*'button'", source, re.DOTALL), (
        "активация пробелом должна быть ограничена элементами с role=\"button\""
    )


def test_file_zone_has_a_visible_focus_ring() -> None:
    assert ".file-zone:focus-visible" in STYLE_CSS.read_text(encoding="utf-8")


# ── Контраст текста (WCAG AA) ─────────────────────────────────────────
#
# Проверка считает коэффициент формулой WCAG по РЕАЛЬНОМУ style.css, а не
# сверяет строку правила с хардкодом: прежняя версия смотрела ровно на
# `.worker-alert` и на то, что цвет взят из переменной, поэтому целый класс
# регрессий проходил мимо неё. `.btn-toggle:disabled { opacity: .65 }`,
# добавленный тем же кругом правок, который поднял `.worker-alert` с 2.84:1
# до 8.38:1, уронил подпись «Остановка TG…» до 1.86:1 — и тест этого не
# заметил, потому что смотрел не туда и не так.
#
# Ниже — маленький резолвер каскада: он раскрывает `var(--…)`, применяет
# правила по специфичности и порядку и отвечает на вопрос «какой цвет и на
# каком фоне реально увидит пользователь». Состояния кнопок при этом не
# переписаны заново, а получены запуском настоящего `updateWorkerButton()`
# из app.js в Node — иначе список состояний разошёлся бы с кодом.


def _css_source() -> str:
    return STYLE_CSS.read_text(encoding="utf-8")


def _strip_comments_and_at_rules(css: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    out, index = [], 0
    while index < len(css):
        at = css.find("@", index)
        if at == -1:
            out.append(css[index:])
            break
        out.append(css[index:at])
        brace = css.find("{", at)
        assert brace != -1, "at-правило без блока"
        depth, position = 0, brace
        while position < len(css):
            if css[position] == "{":
                depth += 1
            elif css[position] == "}":
                depth -= 1
                if depth == 0:
                    break
            position += 1
        index = position + 1
    return "".join(out)


RULE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.DOTALL)
SELECTOR_TOKEN = re.compile(r"\.([\w-]+)|:not\(([^)]*)\)|(::?[\w-]+)|(\S)")


def _declarations(body: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for piece in body.split(";"):
        if ":" not in piece:
            continue
        name, _, value = piece.partition(":")
        result[name.strip()] = value.strip()
    return result


def _rules() -> list[tuple[str, dict[str, str], int]]:
    return [
        (selector.strip(), _declarations(body), order)
        for order, (selectors, body) in enumerate(RULE.findall(_strip_comments_and_at_rules(_css_source())))
        for selector in selectors.split(",")
    ]


def _root_variables() -> dict[str, str]:
    variables: dict[str, str] = {}
    for selector, declarations, _order in _rules():
        if selector == ":root":
            variables.update({k: v for k, v in declarations.items() if k.startswith("--")})
    assert variables, ":root с переменными не найден в style.css"
    return variables


VAR_CALL = re.compile(r"var\(\s*(--[\w-]+)\s*\)")


def _resolve(value: str, variables: dict[str, str], depth: int = 0) -> str:
    assert depth < 10, f"циклическая переменная в {value!r}"
    match = VAR_CALL.search(value)
    if not match:
        return value.strip()
    name = match.group(1)
    assert name in variables, f"переменная {name} не объявлена в :root"
    return _resolve(value.replace(match.group(0), variables[name]), variables, depth + 1)


def _parse_selector(selector: str):
    """`.a.b:disabled` → (классы, псевдоклассы, отрицания). None — не поддержано."""
    classes, pseudos, negations = set(), set(), set()
    for klass, negated, pseudo, other in SELECTOR_TOKEN.findall(selector):
        if klass:
            classes.add(klass)
        elif negated:
            inner = negated.strip()
            if not inner.startswith(":"):
                return None
            negations.add(inner)
        elif pseudo:
            pseudos.add(pseudo)
        elif other:
            return None            # комбинаторы, теги, атрибуты — не наш случай
    return classes, pseudos, negations


def _specificity(classes, pseudos, negations) -> int:
    return len(classes) + len(pseudos) + len(negations)


def _computed(classes: set[str], pseudos: set[str]) -> dict[str, str]:
    """Значения свойств для элемента с этими классами и псевдоклассами."""
    variables = _root_variables()
    winners: dict[str, tuple[int, int, str]] = {}
    for selector, declarations, order in _rules():
        parsed = _parse_selector(selector)
        if parsed is None:
            continue
        needed_classes, needed_pseudos, negations = parsed
        if not needed_classes or not needed_classes <= classes:
            continue
        if not needed_pseudos <= pseudos:
            continue
        if any(negated in pseudos for negated in negations):
            continue
        rank = (_specificity(needed_classes, needed_pseudos, negations), order)
        for name, value in declarations.items():
            if name.startswith("--"):
                continue
            previous = winners.get(name)
            if previous is None or rank >= previous[:2]:
                winners[name] = (*rank, _resolve(value, variables))
    return {name: value for name, (_s, _o, value) in winners.items()}


HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")


def _as_hex(value: str) -> str:
    match = HEX.search(value)
    assert match, f"не цвет: {value!r}"
    raw = match.group(0)
    if len(raw) == 4:
        raw = "#" + "".join(channel * 2 for channel in raw[1:])
    return raw.lower()


def _relative_luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(foreground: str, background: str) -> float:
    a, b = _relative_luminance(foreground), _relative_luminance(background)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


PAGE_BACKGROUND = "--bg"


def _blend(colour: str, backdrop: str, alpha: float) -> str:
    top = [int(colour[i:i + 2], 16) for i in (1, 3, 5)]
    bottom = [int(backdrop[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(a * alpha + b * (1 - alpha)):02x}" for a, b in zip(top, bottom))


def _text_contrast(classes: set[str], pseudos: set[str] = frozenset()) -> float:
    """Контраст подписи элемента с фоном, который он реально получает.

    `opacity` учитывается: она композитит ВЕСЬ элемент — и подпись, и
    заливку — поверх фона страницы, и именно так `.btn-toggle:disabled
    { opacity: .65 }` уводил «Остановка TG…» с 2.57:1 на 1.86:1. Без этого
    шага проверка не увидела бы ровно тот дефект, ради которого написана.
    """
    computed = _computed(set(classes), set(pseudos))
    variables = _root_variables()
    colour = computed.get("color")
    assert colour, f"у {sorted(classes)} нет цвета текста"
    background = computed.get("background") or computed.get("background-color")
    if background is None or "transparent" in background or background == "none":
        background = variables[PAGE_BACKGROUND]
    foreground, backdrop = _as_hex(colour), _as_hex(background)
    alpha = float(computed.get("opacity", "1"))
    if alpha < 1:
        page = _as_hex(variables[PAGE_BACKGROUND])
        foreground = _blend(foreground, page, alpha)
        backdrop = _blend(backdrop, page, alpha)
    return _contrast(foreground, backdrop)


def _css_var(name: str) -> str:
    """Оставлено для совместимости с проверками, которым нужен сам цвет."""
    return _as_hex(_resolve(f"var({name})", _root_variables()))


def test_the_cascade_resolver_agrees_with_the_stylesheet() -> None:
    """Сначала — доверие к самому инструменту: он обязан видеть и
    специфичность, и переменные, иначе всё, что он «проверяет», ничего не
    стоит."""
    assert _computed({"btn-toggle", "btn-toggle-tg"}, set())["color"] == _css_var("--text")
    assert _computed({"btn-toggle", "btn-toggle-tg", "active"}, set())["background"] == _css_var("--tg-fill")
    disabled = _computed({"btn-toggle", "btn-toggle-tg", "active"}, {":disabled"})
    assert disabled["background"] == _css_var("--tg-light")
    assert disabled["cursor"] == "not-allowed"
    hovered = _computed({"btn-toggle", "btn-toggle-tg", "active"}, {":hover", ":disabled"})
    assert hovered["background"] == _css_var("--tg-light"), (
        ":hover не должен переигрывать :disabled — иначе наведение возвращает"
        " нечитаемую заливку"
    )


# Множество состояний берётся из самого `workerView()`, а не перечисляется
# здесь. Ревью показало, почему это не педантизм: пока список был захардкожен
# в питоновском харнессе, новую ветку `case 'paused':` с контрастом 2.84:1
# можно было добавить в app.js, и прогон оставался зелёным — докстринг ниже
# обещал обратное. Реализацию (`updateWorkerButton`) тест и раньше брал
# настоящую; теперь настоящий и перебор состояний.
#
# Сама регулярка была уже, чем язык: `case\s+'([a-z_]+)'\s*:` видела только
# одинарные кавычки и только нижний регистр, поэтому `case "paused":` или
# `case 'Paused':` возвращали ровно ту дыру, которую этот тест и закрывал —
# состояние есть в app.js, а перебор его не знает. Все три кавычки JS
# (`'`, `"`, backtick) и любой регистр теперь учитываются; что регулярка
# действительно их видит, закреплено тестом ниже.
WORKER_VIEW_CASE = re.compile(r"""case\s+['"`]([A-Za-z_][\w-]*)['"`]\s*:""")


def _worker_view_states() -> list[str]:
    source = _maybe_extract("workerView")
    assert source, "workerView() не найдена в app.js"
    states = WORKER_VIEW_CASE.findall(source)
    assert states, "в workerView() не осталось ни одной ветки case — разбор сломался"
    assert "default:" in source, (
        "у workerView() нет ветки default — состояние `stopped` берётся именно из неё"
    )
    # `default:` отвечает за `stopped` (и за всё незнакомое) — своей `case` у
    # него нет, поэтому имя добавляется явно.
    return sorted({*states, "stopped"})


WORKER_VIEW_CASE_FORMS = {
    "одинарные кавычки": ("case 'paused':", ["paused"]),
    "двойные кавычки": ('case "paused":', ["paused"]),
    "шаблонная строка": ("case `paused`:", ["paused"]),
    "верхний регистр": ("case 'Paused':", ["Paused"]),
    "пробел перед двоеточием": ("case 'paused' :", ["paused"]),
    "подчёркивание и цифры": ("case 'half_open2':", ["half_open2"]),
    "дефис": ("case 'login-required':", ["login-required"]),
    "несколько ветвей подряд": (
        "switch (s) {\n  case 'a':\n  case \"b\":\n    return 1;\n}", ["a", "b"],
    ),
}


def test_the_worker_state_scan_sees_every_way_a_case_can_be_written() -> None:
    """Перебор состояний кнопки берётся из `switch` в `workerView()`, и
    ровно поэтому его разбор обязан понимать язык, а не одну манеру записи:
    состояние, записанное в другой кавычке, иначе тихо выпадет из проверки
    контраста — то есть вернётся дефект, ради которого перебор и стали брать
    из app.js."""
    for name, (snippet, expected) in WORKER_VIEW_CASE_FORMS.items():
        assert WORKER_VIEW_CASE.findall(snippet) == expected, (
            f"форма «{name}» ({snippet!r}) разобрана как "
            f"{WORKER_VIEW_CASE.findall(snippet)}"
        )
    assert not WORKER_VIEW_CASE.findall("caseless = 'paused';"), "ложное срабатывание"


def _worker_button_states_script() -> str:
    return WORKER_BUTTON_STATES.replace("__STATES__", json.dumps(_worker_view_states()))


WORKER_BUTTON_STATES = """
const nodes = {};
globalThis.document = { getElementById: (id) => nodes[id] || null };
function el() { return {}; }
function fill() {}

const rows = [];
for (const state of __STATES__) {
  for (const canStart of [true, false]) {
    for (const [name, toggleClass] of [['TG', 'btn-toggle-tg'], ['HH', 'btn-toggle-hh']]) {
      const button = { className: '', disabled: false };
      nodes.button = button;
      nodes.alert = { style: {}, textContent: '' };
      updateWorkerButton(
        { state, canStart, lastError: 'воркер не остановился' },
        name, 'button', 'dot', 'alert', toggleClass,
      );
      rows.push({ state, canStart, name, className: button.className, disabled: button.disabled });
    }
  }
}
console.log(JSON.stringify(rows));
"""


def _worker_button_states() -> list[dict]:
    sources = "\n".join(
        _maybe_extract(name) for name in ("workerView", "workerSignature", "updateWorkerButton")
    )
    result = _run_node(f"{sources}\n{_worker_button_states_script()}")
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    rows = json.loads(result.stdout.strip().splitlines()[-1])
    assert len(rows) == len(_worker_view_states()) * 4, rows   # × canStart × TG/HH
    assert any(row["disabled"] for row in rows), "ни одного disabled — состояния собраны неверно"
    return rows


@skip_without_node
def test_every_worker_button_label_meets_wcag_aa() -> None:
    """Подпись кнопки воркера читаема в КАЖДОМ состоянии, включая выключенное.

    В состоянии `stopping` подпись — единственный индикатор: баннер
    `.worker-alert` показывается только при `error`. Сплошная
    `.btn-toggle:disabled { opacity: .65 }` роняла её до 1.86:1 («Остановка
    TG…»), 3.57:1 («Остановка HH…») и 1.87:1 («Ошибка HH — нужен перезапуск
    приложения»). И реализация (`updateWorkerButton`), и сам перебор
    состояний берутся из app.js — ветки `switch` в `workerView()`, — поэтому
    новое состояние кнопки нельзя добавить в обход этой проверки.
    """
    failures = []
    for row in _worker_button_states():
        classes = set(row["className"].split())
        variants = [frozenset(), frozenset({":hover"})]
        if row["disabled"]:
            variants = [frozenset({":disabled"}), frozenset({":disabled", ":hover"})]
        for pseudos in variants:
            ratio = _text_contrast(classes, pseudos)
            if ratio < 4.5:
                failures.append(
                    f"{row['name']} state={row['state']} can_start={row['canStart']}"
                    f" {sorted(pseudos) or 'обычная'}: {ratio:.2f}:1"
                )
    assert not failures, "подписи кнопок ниже WCAG AA 4.5:1:\n" + "\n".join(failures)


def test_worker_alert_text_meets_wcag_aa() -> None:
    """`.worker-alert` — единственное место, где показывается `last_error`
    воркера, и он был `--yellow` (#ca8a04) на `--yellow-light` (#fefce8):
    2.84:1 при 12px, норма AA — 4.5:1."""
    ratio = _text_contrast({"worker-alert"})
    assert ratio >= 4.5, f"контраст текста предупреждения {ratio:.2f}:1 — ниже WCAG AA 4.5:1"


# Классы плашек берутся из app.js, а не перечисляются здесь: `eventBadge()` и
# `vacancyStatusClass()` — единственные места, которые их назначают, поэтому
# новую плашку нельзя завести в обход этой проверки. Базовый класс
# (`.log-badge` / `.status-badge`) добавляется потому, что размер шрифта живёт
# в нём, а сами `.badge-*`/`.status-*` задают только цвета.
BADGE_LITERAL = re.compile(r"'((?:badge|status)-[a-z-]+)'")
BADGE_CONTAINERS = {"badge": "log-badge", "status": "status-badge"}


def _badge_classes() -> list[str]:
    found = sorted(
        name
        for name in set(BADGE_LITERAL.findall(_app_source()))
        if name not in BADGE_CONTAINERS.values()
    )
    assert len(found) >= 8, f"найдено всего {len(found)} плашек: {found} — app.js переехал?"
    return found


@pytest.mark.parametrize("badge", _badge_classes())
def test_every_status_badge_meets_wcag_aa(badge: str) -> None:
    """Все статусные плашки читаемы, а не только жёлтые.

    Резолвер каскада нашёл ещё три пары ниже нормы, каждая — решение о
    палитре, а не опечатка: `.badge-ok`/`.status-sent` (`--green` на
    `--green-light`) 3.15:1, `.badge-error`/`.status-error` (`--red` на
    `--red-light`) 4.41:1, `.badge-skip`/`.status-skip` (`--muted` на `--bg`)
    3.23:1. Норма AA — 4.5:1; шрифт плашки 10px, то есть послабление для
    крупного текста (3:1) к ним не относится.
    """
    classes = {badge, BADGE_CONTAINERS[badge.split("-", 1)[0]]}
    ratio = _text_contrast(classes)
    assert ratio >= 4.5, f".{badge}: {ratio:.2f}:1 — ниже WCAG AA 4.5:1"


# Тон бренда → его светлый фон. Пара существует ровно потому, что «сырой» тон
# на своём светлом фоне нечитаем, и рядом заведён более тёмный `<тон>-text`.
BRAND_ON_LIGHT = (
    ("--yellow", "--yellow-light"),
    ("--green", "--green-light"),
    ("--red", "--red-light"),
    ("--tg", "--tg-light"),
)


@pytest.mark.parametrize("raw, light", BRAND_ON_LIGHT)
def test_the_darker_text_tokens_earn_their_existence(raw: str, light: str) -> None:
    """Почему в палитре есть `--yellow-text`, `--green-text`, `--red-text`,
    `--tg-text`, а не просто `--yellow`, `--green`, `--red`, `--tg`.

    Прежняя версия этой проверки сравнивала два литерала (`#ca8a04` и
    `#fefce8`), записанных в самом тесте: она не читала `style.css` вовсе и
    осталась бы зелёной, даже если бы палитра поменялась целиком, — то есть
    проверяла формулу, а не файл. Здесь оба цвета берутся из настоящего
    `:root`, поэтому «упростить» подпись обратно к сырому тону нельзя молча.
    """
    assert _contrast(_css_var(raw), _css_var(light)) < 4.5, (
        f"{raw} на {light} перестал быть проблемой — тогда {raw}-text больше не нужен"
    )
    ratio = _contrast(_css_var(f"{raw}-text"), _css_var(light))
    assert ratio >= 4.5, f"{raw}-text на {light}: {ratio:.2f}:1 — ниже WCAG AA 4.5:1"


@pytest.mark.parametrize("background", ["--bg", "--card-bg"])
def test_secondary_text_meets_wcag_aa(background: str) -> None:
    """`--muted` — вторичный текст всего интерфейса: подписи, время, счётчики,
    подсказки под переключателями, `.badge-skip`. Все они 10–12.5px, так что
    послабление AA для крупного текста (3:1) неприменимо, а прежние 3.23:1 на
    `--bg` не проходили и его."""
    ratio = _contrast(_css_var("--muted"), _css_var(background))
    assert ratio >= 4.5, f"--muted на {background}: {ratio:.2f}:1 — ниже WCAG AA 4.5:1"


# ── Отмена входа в hh.ru доступна из UI ───────────────────────────────


def test_login_cancel_control_exists_and_is_delegated() -> None:
    """Брошенное окно Chrome закрывается только успешным confirm(); из UI
    нужен способ закрыть его, не подтверждая вход."""
    assert 'data-action="hhLoginCancel"' in _index_source()
    assert "hhLoginCancel" in _action_map_keys()
    assert "/hh/login/cancel" in _app_source()


# ── Один формат времени на весь экран ─────────────────────────────────


@skip_without_node
def test_the_chat_list_and_the_recent_list_format_a_timestamp_the_same_way() -> None:
    """`GET /api/tg/chats` отдаёт `sent_at` как ISO — `2026-09-08T14:33:12`, —
    и это правильная форма для полезной нагрузки. Рисовать её как есть —
    нет: список чатов показывал полную ISO-строку, с секундами и `T`, в
    узкой колонке, а соседний список последних событий той же базы рисовал
    `HH:MM` через `eventTime()`. Один формат хранения, два разных вида. До
    этой ветки бэкенд отдавал `%Y-%m-%d %H:%M`; форматирование переехало на
    фронтенд — на ту сторону, которая и решает, как это выглядит.
    """
    sources = "\n".join(_maybe_extract(name) for name in ("stampText", "eventTime"))
    checks = """
    same('ISO с секундами', stampText('2026-09-08T14:33:12'), '2026-09-08 14:33');
    same('ISO без секунд',  stampText('2026-09-08T14:33'),    '2026-09-08 14:33');
    check('нет T в выводе', !stampText('2026-09-08T14:33:12').includes('T'));
    check('нет секунд',     !stampText('2026-09-08T14:33:12').endsWith(':12'));
    same('пусто',   stampText(''),        '');
    same('null',    stampText(null),      '');
    same('мусор не теряется', stampText('когда-то'), 'когда-то');
    // Часы и минуты в обоих списках — одни и те же символы.
    check('часы совпадают с eventTime',
          stampText('2026-09-08T14:33:12').endsWith(eventTime('2026-09-08T14:33:12')));
    """
    result = _run_node(f"{sources}\n{NODE_CHECK_HELPER}\n{checks}")
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


def test_the_chat_list_does_not_render_the_raw_timestamp() -> None:
    """Грепом, чтобы проверка работала и без Node: колонка времени в списке
    чатов должна идти через форматирование, а не подставлять `c.time`."""
    source = _app_source()
    match = re.search(r"class: 'chat-time'[^)]*\)", source)
    assert match, "колонка времени в списке чатов не найдена — renderChats переехал?"
    assert "stampText(" in match.group(0), (
        f"время рисуется как есть: {match.group(0)} — в узкой колонке это ISO с "
        "секундами и латинской T"
    )


# ── Живой воркер в ответе на остановку — не ошибка ────────────────────


@skip_without_node
def test_a_worker_restarted_by_someone_else_is_not_reported_as_an_error() -> None:
    """Гонка двух клиентов, целиком.

    Сторож остановки (`job_monitor/workers/manager.py::_await_stop`) живёт
    отдельной таской и переживает отмену ожидающего; проснувшись, он молчит,
    если `_tasks[name]` уже принадлежит ДРУГОМУ запуску — иначе он объявил бы
    «остановлен» про живого воркера, и следующий start() поднял бы второй
    экземпляр. Плата за это: клиент A, попросивший стоп, получает состояние
    воркера, запущенного клиентом B, — `{"status":"running","detail":null}`.

    Строго лучше прежнего (тогда A получал `stopped` при живом воркере), но
    тост врал: ветка показывала `r.detail || 'Ошибка'`, а `detail` у живого
    состояния пуст. Проверяется, ЧТО видит пользователь, а не наличие
    проверки.
    """
    sources = "\n".join(
        _maybe_extract(name) for name in
        ("workerView", "workerToggleAction", "setWorkerState", "stopResultText",
         "toggleTG", "toggleHH")
    )
    harness = """
    let toasts = [];
    let reply = null;
    async function apiPost() { return reply; }
    function updateTGButton() {}
    function updateHHButton() {}
    function showToast(text) { toasts.push(text); }
    function hideRestartBanner() {}
    const tgState = {};
    const hhState = {};

    (async () => {
      for (const [worker, toggle, name] of
           [[tgState, toggleTG, 'TG'], [hhState, toggleHH, 'HH']]) {
        // Воркер работает, пользователь жмёт «Остановить».
        worker.state = 'running'; worker.running = true; worker.canStart = true;
        toasts = [];
        reply = { status: 'running', detail: null };
        await toggle();
        check(name + ': тост не «Ошибка»', toasts.length === 1 && toasts[0] !== 'Ошибка');
        check(name + ': тост говорит про запуск, а не про сбой',
              !/[Оо]шибк/.test(toasts[0]));
        check(name + ': состояние осталось живым', worker.state === 'running');
        check(name + ': кнопка снова умеет останавливать',
              workerToggleAction(worker, name) === 'stop');

        // `starting` — та же гонка, только сторож проснулся ещё раньше.
        worker.state = 'running'; worker.running = true; worker.canStart = true;
        toasts = [];
        reply = { status: 'starting', detail: null };
        await toggle();
        check(name + ': starting тоже не ошибка', !/[Оо]шибк/.test(toasts[0]));

        // А вот зависший воркер — ошибка, и её текст обязан дойти.
        worker.state = 'running'; worker.running = true; worker.canStart = true;
        toasts = [];
        reply = { status: 'error', detail: 'воркер не остановился за 10.0s' };
        await toggle();
        same(name + ': причина зависания показана', toasts,
             ['воркер не остановился за 10.0s']);
        check(name + ': состояние стало error', worker.state === 'error');

        // Ответ вообще без состояния (сеть, 500) — по-прежнему «Ошибка».
        worker.state = 'running'; worker.running = true; worker.canStart = true;
        toasts = [];
        reply = null;
        await toggle();
        same(name + ': пустой ответ остался ошибкой', toasts, ['Ошибка']);
      }
    })();
    """
    result = _run_node(f"{sources}\n{NODE_CHECK_HELPER}\n{harness}")
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
