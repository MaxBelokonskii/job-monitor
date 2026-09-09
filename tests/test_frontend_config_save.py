"""Pins the fix for I4: a rejected PATCH /api/config used to report success.

AppSettings' ge/le bounds and extra="forbid" mean the endpoint now returns
422 in cases the deleted ConfigUpdate model let through as 200 (an empty
numeric input serialised by the frontend as `parseInt(...)` -> NaN ->
`null`, an out-of-range value, or an unknown key). Every save handler in
frontend/app.js called `apiPatch('/config', ...)` and unconditionally
showed a "сохранено" toast, ignoring the response entirely. This does not
duplicate tests/test_frontend_safety.py (which is a fix-plan-1 tripwire
this plan must not touch) — it exercises the new configPatchOk/
configErrorDetail helpers and the save handlers built on them, not
apiSend/apiGet's 403 handling.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import requires_node

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
APP_JS = FRONTEND_DIR / "app.js"

# Маркер и его причина — общие на весь набор, в tests/conftest.py: там же
# живёт итоговое предупреждение о том, что именно осталось непроверенным.
skip_without_node = requires_node


def _extract_function_source(name: str) -> str:
    source = APP_JS.read_text(encoding="utf-8")
    pattern = rf"(?:async )?function {re.escape(name)}\(.*?\) \{{.*?\n\}}"
    match = re.search(pattern, source, re.DOTALL)
    assert match, f"{name}() not found in app.js"
    return match.group(0)


def _run_node(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)


@skip_without_node
def test_config_patch_ok_and_error_detail_helpers() -> None:
    script = "\n".join((
        _extract_function_source("configPatchOk"),
        _extract_function_source("configErrorDetail"),
        """
        const checks = [];
        checks.push(configPatchOk({ status: 'saved' }) === true);
        checks.push(configPatchOk({ detail: [{ loc: ['body', 'max_per_day'], msg: 'x' }] }) === false);
        checks.push(configPatchOk(null) === false);
        checks.push(configPatchOk({}) === false);

        checks.push(configErrorDetail({ detail: 'api_id должен быть числом' }) === 'api_id должен быть числом');
        checks.push(configErrorDetail(null).length > 0);
        const listDetail = configErrorDetail({
          detail: [{ loc: ['body', 'max_per_day'], msg: 'Input should be a valid integer' }],
        });
        checks.push(listDetail.includes('max_per_day') && listDetail.includes('valid integer'));

        if (!checks.every(Boolean)) {
          console.error('FAIL', checks);
          process.exitCode = 1;
        }
        """,
    ))
    result = _run_node(script)
    assert result.returncode == 0, (
        "configPatchOk/configErrorDetail failed one of the checks.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


_DOM_STUB = """
const __toasts = [];
const document = {
  createElement(tag) {
    return {
      tag, className: '', textContent: '',
      style: { cssText: '' },
      attrs: {},
      setAttribute(k, v) { this.attrs[k] = v; },
      getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; },
      addEventListener() {},
      appendChild(node) { if (this === document.body) __toasts.push(node.textContent); },
      append() {},
    };
  },
  createTextNode(text) { return { text }; },
  getElementById(id) {
    const store = document.__elements[id];
    return store || null;
  },
  querySelector() { return { content: 'test-token' }; },
  querySelectorAll() { return []; },
  body: { appendChild(node) { __toasts.push(node.textContent); } },
  __elements: {},
};
const location = { reload() {} };
const API = '/api';
const APP_TOKEN = 'test-token';
function requestAnimationFrame(fn) { fn(); }
function setTimeout() {}
"""


@skip_without_node
def test_save_tg_settings_reports_the_rejection_not_success() -> None:
    """Reproduces the exact I4 scenario: an empty 'max_per_day' input
    serialises as NaN -> null, the API rejects it with 422, and the save
    handler must surface that instead of claiming success."""
    tg_state_stub = "const tgState = { running: false, safeMode: true, parseHistory: false, maxPerDay: 25 };"
    script = "\n".join((
        _DOM_STUB,
        tg_state_stub,
        "function updateMetrics() {}",
        "function showRestartBanner() {}",
        _extract_function_source("headers"),
        _extract_function_source("showToast"),
        _extract_function_source("apiSend"),
        "const apiPatch = (path, body) => apiSend('PATCH', path, body);",
        _extract_function_source("configPatchOk"),
        _extract_function_source("configErrorDetail"),
        # Stub the DOM elements saveTGSettings() reads from.
        """
        document.__elements = {
          toggleSafe: { checked: true },
          toggleHistory: { checked: false },
          maxPerDay: { value: '' },       // emptied by the user -> parseInt -> NaN -> JSON null
          historyLimit: { value: '50' },
          toggleTGAutostart: { checked: false },
        };
        """,
        _extract_function_source("saveTGSettings"),
        """
        (async () => {
          globalThis.fetch = async () => ({
            status: 422,
            ok: false,
            json: async () => ({ detail: [{ loc: ['body', 'max_per_day'], msg: 'Input should be a valid integer' }] }),
          });

          await saveTGSettings();

          if (tgState.maxPerDay !== 25) {
            console.error('FAIL: local state was updated despite the rejected save', tgState.maxPerDay);
            process.exitCode = 1;
          }
          const sawSuccessToast = __toasts.some(t => t.includes('сохранены'));
          if (sawSuccessToast) {
            console.error('FAIL: a success toast was shown for a rejected save', __toasts);
            process.exitCode = 1;
          }
          const sawErrorToast = __toasts.some(t => t.toLowerCase().includes('max_per_day') || t.includes('integer'));
          if (!sawErrorToast) {
            console.error('FAIL: no error detail was surfaced to the user', __toasts);
            process.exitCode = 1;
          }
        })();
        """,
    ))
    result = _run_node(script)
    assert result.returncode == 0, (
        "saveTGSettings() did not surface a rejected PATCH /api/config.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


@skip_without_node
def test_save_tg_settings_still_reports_success_on_a_healthy_save() -> None:
    tg_state_stub = "const tgState = { running: false, safeMode: true, parseHistory: false, maxPerDay: 25 };"
    script = "\n".join((
        _DOM_STUB,
        tg_state_stub,
        "function updateMetrics() {}",
        "function showRestartBanner() {}",
        _extract_function_source("headers"),
        _extract_function_source("showToast"),
        _extract_function_source("apiSend"),
        "const apiPatch = (path, body) => apiSend('PATCH', path, body);",
        _extract_function_source("configPatchOk"),
        _extract_function_source("configErrorDetail"),
        """
        document.__elements = {
          toggleSafe: { checked: true },
          toggleHistory: { checked: false },
          maxPerDay: { value: '30' },
          historyLimit: { value: '50' },
          toggleTGAutostart: { checked: false },
        };
        """,
        _extract_function_source("saveTGSettings"),
        """
        (async () => {
          globalThis.fetch = async () => ({ status: 200, ok: true, json: async () => ({ status: 'saved' }) });
          await saveTGSettings();
          if (tgState.maxPerDay !== 30) {
            console.error('FAIL: local state was not updated on a healthy save', tgState.maxPerDay);
            process.exitCode = 1;
          }
          if (!__toasts.some(t => t.includes('сохранены'))) {
            console.error('FAIL: no success toast was shown for a healthy save', __toasts);
            process.exitCode = 1;
          }
        })();
        """,
    ))
    result = _run_node(script)
    assert result.returncode == 0, (
        "saveTGSettings() regressed the healthy-save path.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ── Маска `••` в поле api_hash не должна уезжать на сервер ────────────
#
# `loadConfig()` не может подставить в поле сам ключ (иначе секрет уехал бы
# в DOM), поэтому при `api_hash_set` там стоит маска из bullet-ов. Гард
# `if (api_hash && !api_hash.startsWith('••'))` в `saveApiKeys()` — то, что
# не даёт этой маске уехать в `.env` при сохранении любого другого поля.
# Мутационный аудит показал, что гард не покрыт ничем: его снятие проходило
# зелёным. Парный гард на бэкенде закреплён в tests/test_config_api.py; UI
# всё равно не должен отправлять мусор — сервер отбивает bullet-ы, но не
# отбивал бы, скажем, маску другого вида.


@skip_without_node
def test_save_api_keys_does_not_send_the_mask_back() -> None:
    script = "\n".join((
        _DOM_STUB,
        "const tgState = { running: false };",
        "function showRestartBanner() {}",
        _extract_function_source("headers"),
        _extract_function_source("showToast"),
        _extract_function_source("apiSend"),
        "const apiPatch = (path, body) => apiSend('PATCH', path, body);",
        _extract_function_source("configPatchOk"),
        _extract_function_source("configErrorDetail"),
        """
        document.__elements = {
          apiId: { value: '42' },
          apiHash: { value: '••••••••••••••••' },   // маска, поставленная loadConfig()
        };
        """,
        _extract_function_source("saveApiKeys"),
        """
        (async () => {
          const sent = [];
          globalThis.fetch = async (url, options) => {
            sent.push(JSON.parse(options.body));
            return { status: 200, ok: true, json: async () => ({ status: 'saved' }) };
          };

          await saveApiKeys();

          if (sent.length !== 1) {
            console.error('FAIL: expected exactly one PATCH', sent);
            process.exitCode = 1;
          } else if ('api_hash' in sent[0]) {
            console.error('FAIL: the mask was sent to the server', sent[0]);
            process.exitCode = 1;
          } else if (sent[0].api_id !== '42') {
            console.error('FAIL: api_id was not sent', sent[0]);
            process.exitCode = 1;
          }
        })();
        """,
    ))
    result = _run_node(script)
    assert result.returncode == 0, (
        "saveApiKeys() отправил маску вместо ключа — при сохранении API ID "
        f"перезаписался бы и api_hash.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


@skip_without_node
def test_save_api_keys_still_sends_a_real_hash() -> None:
    """Обратная сторона: гард не должен глотать настоящий ключ — иначе
    пользователь не смог бы задать его вообще."""
    script = "\n".join((
        _DOM_STUB,
        "const tgState = { running: false };",
        "function showRestartBanner() {}",
        _extract_function_source("headers"),
        _extract_function_source("showToast"),
        _extract_function_source("apiSend"),
        "const apiPatch = (path, body) => apiSend('PATCH', path, body);",
        _extract_function_source("configPatchOk"),
        _extract_function_source("configErrorDetail"),
        """
        document.__elements = {
          apiId: { value: '42' },
          apiHash: { value: 'f00dcafef00dcafef00dcafef00dcafe' },
        };
        """,
        _extract_function_source("saveApiKeys"),
        """
        (async () => {
          const sent = [];
          globalThis.fetch = async (url, options) => {
            sent.push(JSON.parse(options.body));
            return { status: 200, ok: true, json: async () => ({ status: 'saved' }) };
          };

          await saveApiKeys();

          if (sent.length !== 1 || sent[0].api_hash !== 'f00dcafef00dcafef00dcafef00dcafe') {
            console.error('FAIL: a real api_hash did not reach the server', sent);
            process.exitCode = 1;
          }
        })();
        """,
    ))
    result = _run_node(script)
    assert result.returncode == 0, (
        f"saveApiKeys() не отправил настоящий ключ.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
