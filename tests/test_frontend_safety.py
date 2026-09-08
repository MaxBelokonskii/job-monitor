from pathlib import Path

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
