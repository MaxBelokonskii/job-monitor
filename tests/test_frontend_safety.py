import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parents[1] / "frontend" / "app.js"


def test_app_js_never_uses_inner_html():
    source = APP_JS.read_text(encoding="utf-8")
    assert "innerHTML" not in source, (
        "innerHTML запрещён: содержимое Telegram и hh.ru недоверенное. "
        "Собирай узлы через el()/textContent."
    )


def test_csp_header_is_sent(client):
    headers = client.get("/").headers
    assert "content-security-policy" in headers
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"


def _extract_is_http_url_source() -> str:
    source = APP_JS.read_text(encoding="utf-8")
    match = re.search(r"function isHttpUrl\(url\) \{.*?\n\}", source, re.DOTALL)
    assert match, (
        "isHttpUrl() not found in app.js — loadHHVacancies must validate "
        "v.url's scheme before it ever reaches an <a href>."
    )
    return match.group(0)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not available")
def test_hh_vacancy_url_scheme_guard_rejects_script_schemes():
    """Pins the fix for the javascript:-URL sink in loadHHVacancies (S6 residual).

    hh.ru-scraped vacancy data feeds an <a href> built with el(); a value
    like "javascript:alert(1)" would execute on click with the app token in
    scope, and the CSP's 'unsafe-inline' script-src does not block it. This
    runs the real isHttpUrl() predicate — extracted verbatim from app.js —
    under Node, so the assertion exercises the actual guard rather than a
    Python re-implementation of it.
    """
    fn_source = _extract_is_http_url_source()
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
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, (
        "isHttpUrl() failed one of the scheme checks.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
