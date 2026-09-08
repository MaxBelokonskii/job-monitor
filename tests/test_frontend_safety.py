import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
APP_JS = FRONTEND_DIR / "app.js"

NODE_AVAILABLE = shutil.which("node") is not None
skip_without_node = pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js not available")

# Verified by hand during the security review: none of these ever appear in
# frontend/*.js. Each is a way untrusted Telegram/hh.ru content could turn
# into script execution if it ever fed one of these sinks instead of
# el()/textContent.
FORBIDDEN_SINKS = (
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "document.write",
    "createContextualFragment",
    "new Function",
    "eval",
)


def _frontend_js_files() -> list[Path]:
    files = sorted(FRONTEND_DIR.glob("*.js"))
    assert files, "no frontend/*.js files found — did the frontend move?"
    return files


@pytest.mark.parametrize("sink", FORBIDDEN_SINKS)
def test_frontend_js_never_uses_dangerous_sink(sink: str) -> None:
    for js_file in _frontend_js_files():
        source = js_file.read_text(encoding="utf-8")
        assert sink not in source, (
            f"{sink!r} found in {js_file.relative_to(FRONTEND_DIR.parent)}: Telegram/hh.ru "
            "content is untrusted — build nodes with el()/textContent, never this sink."
        )


def test_csp_header_is_sent(client) -> None:
    headers = client.get("/").headers
    assert "content-security-policy" in headers
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"


def _extract_function_source(name: str) -> str:
    """Extract a top-level function's source verbatim from app.js.

    Relies on the file's existing convention of closing top-level functions
    with a `}` alone at column 0 — every nested block in this file is
    indented, so the first `\\n}` after the signature is the real end.
    """
    source = APP_JS.read_text(encoding="utf-8")
    pattern = rf"(?:async )?function {re.escape(name)}\(.*?\) \{{.*?\n\}}"
    match = re.search(pattern, source, re.DOTALL)
    assert match, f"{name}() not found in app.js"
    return match.group(0)


def _run_node(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)


@skip_without_node
def test_hh_vacancy_url_scheme_guard_rejects_script_schemes() -> None:
    """Pins the fix for the javascript:-URL sink in loadHHVacancies (S6 residual).

    hh.ru-scraped vacancy data feeds an <a href> built with el(); a value
    like "javascript:alert(1)" would execute on click with the app token in
    scope, and the CSP's 'unsafe-inline' script-src does not block it. This
    runs the real isHttpUrl() predicate — extracted verbatim from app.js —
    under Node, so the assertion exercises the actual guard rather than a
    Python re-implementation of it.
    """
    fn_source = _extract_function_source("isHttpUrl")
    cases = [
        ("https://hh.ru/vacancy/123", True),
        ("http://hh.ru/vacancy/123", True),
        ("HTTPS://HH.RU/VACANCY/123", True),
        ("javascript:alert(1)", False),
        ("JavaScript:alert(1)", False),
        ("data:text/html,<script>alert(1)</script>", False),
        ("vbscript:msgbox(1)", False),
        ("  javascript:alert(1)", False),
        ("", False),
        (None, False),
    ]
    checks = "\n".join(
        f"if (isHttpUrl({json.dumps(url)}) !== {str(expected).lower()}) "
        f"{{ console.error('FAIL for', {json.dumps(url)}); process.exitCode = 1; }}"
        for url, expected in cases
    )
    script = f"{fn_source}\n{checks}"
    result = _run_node(script)
    assert result.returncode == 0, (
        "isHttpUrl() failed one of the scheme checks.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


_DOM_STUB = """
const __state = { bodyAppended: [] };
const document = {
  createElement(tag) {
    return {
      tag,
      className: '',
      textContent: '',
      style: { cssText: '' },
      attrs: {},
      listeners: {},
      setAttribute(k, v) { this.attrs[k] = v; },
      getAttribute(k) {
        return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null;
      },
      addEventListener(evt, fn) { this.listeners[evt] = fn; },
      append() {},
    };
  },
  createTextNode(text) { return { text }; },
  getElementById() { return null; },
  body: { appendChild(node) { __state.bodyAppended.push(node); } },
};
const location = { reload() {} };
const API = '/api';
const APP_TOKEN = 'test-token';
"""


@skip_without_node
def test_el_gates_href_and_src_by_construction() -> None:
    """Pins the fix moving the scheme guard from the loadHHVacancies call
    site into el() itself (residual of S6): any href/src el() is asked to
    set — not just the one in loadHHVacancies — must be scheme-validated,
    so a future call site can't reopen the javascript:-URL hole.
    """
    script = "\n".join((
        _DOM_STUB,
        _extract_function_source("isHttpUrl"),
        _extract_function_source("el"),
        """
        const safe = el('a', { href: 'https://hh.ru/vacancy/1', text: 'ok' });
        if (safe.getAttribute('href') !== 'https://hh.ru/vacancy/1') {
          console.error('FAIL: safe href was not set', safe.getAttribute('href'));
          process.exitCode = 1;
        }
        const bad = el('a', { href: 'javascript:alert(1)', text: 'bad' });
        if (bad.getAttribute('href') !== null) {
          console.error('FAIL: javascript: href leaked through el()', bad.getAttribute('href'));
          process.exitCode = 1;
        }
        const badSrc = el('img', { src: 'data:text/html,<script>alert(1)</script>' });
        if (badSrc.getAttribute('src') !== null) {
          console.error('FAIL: data: src leaked through el()', badSrc.getAttribute('src'));
          process.exitCode = 1;
        }
        """,
    ))
    result = _run_node(script)
    assert result.returncode == 0, (
        "el() failed to gate an href/src by scheme.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


@skip_without_node
def test_stale_token_403_surfaces_a_banner_instead_of_silent_undefined() -> None:
    """Pins the fix for finding 3: APP_TOKEN is regenerated on every process
    restart, so a stale tab gets 403 on every /api/* call. The 403 body is
    `{detail: "invalid app token"}` — truthy — so a caller that only checks
    "did I get something back" sails past its guard and renders
    undefined/NaN everywhere. apiGet/apiSend must detect 403 specifically,
    return null (so existing `if (!cfg)` guards still fire), and surface a
    persistent banner.
    """
    script = "\n".join((
        _DOM_STUB,
        _extract_function_source("isHttpUrl"),
        _extract_function_source("el"),
        _extract_function_source("headers"),
        _extract_function_source("showTokenExpiredBanner"),
        _extract_function_source("apiGet"),
        _extract_function_source("apiSend"),
        """
        (async () => {
          globalThis.fetch = async () => ({
            status: 403,
            json: async () => ({ detail: 'invalid app token' }),
          });

          const getResult = await apiGet('/config');
          if (getResult !== null) {
            console.error('FAIL: apiGet did not return null on 403', getResult);
            process.exitCode = 1;
          }
          if (__state.bodyAppended.length !== 1) {
            console.error('FAIL: apiGet 403 did not surface exactly one banner', __state.bodyAppended.length);
            process.exitCode = 1;
          }

          const sendResult = await apiSend('POST', '/tg/start');
          if (sendResult !== null) {
            console.error('FAIL: apiSend did not return null on 403', sendResult);
            process.exitCode = 1;
          }
          if (__state.bodyAppended.length !== 2) {
            console.error('FAIL: apiSend 403 did not surface a banner', __state.bodyAppended.length);
            process.exitCode = 1;
          }

          // A healthy response must still work exactly as before.
          globalThis.fetch = async () => ({ status: 200, json: async () => ({ ok: true }) });
          const healthy = await apiGet('/config');
          if (!healthy || healthy.ok !== true) {
            console.error('FAIL: healthy 200 response was not passed through', healthy);
            process.exitCode = 1;
          }
          if (__state.bodyAppended.length !== 2) {
            console.error('FAIL: a healthy response must not add another banner', __state.bodyAppended.length);
            process.exitCode = 1;
          }
        })();
        """,
    ))
    result = _run_node(script)
    assert result.returncode == 0, (
        "apiGet/apiSend did not handle a stale-token 403 correctly.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
