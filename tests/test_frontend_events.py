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

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
APP_JS = FRONTEND_DIR / "app.js"
INDEX_HTML = FRONTEND_DIR / "index.html"
STYLE_CSS = FRONTEND_DIR / "style.css"

NODE_AVAILABLE = shutil.which("node") is not None
skip_without_node = pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js not available")

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
