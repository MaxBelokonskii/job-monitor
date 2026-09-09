"""Кто имеет право читать ответы приложения из браузера (модель угроз D1).

До этого файла в наборе не было ни одного теста на CORS, и мутация
`allow_origins=["*"]` в `api/main.py` проходила зелёной. Цена именно этой
строки выше, чем «ослабленный заголовок»:

* `GET /` НЕ под `/api/`, то есть не под токен-гейтом (`job_monitor/
  security.py::app_token_middleware`), и отдаёт `APP_TOKEN` в
  `<meta name="app-token">` — так его получает сам UI;
* `TrustedHostMiddleware` такой запрос пропускает: для `fetch` из чужой
  вкладки на `http://127.0.0.1:8000/` заголовок `Host` — это `127.0.0.1:8000`,
  он и разрешён;
* значит единственное, что мешает любой открытой в браузере странице
  прочитать токен со `/`, — отсутствие `Access-Control-Allow-Origin` в
  ответе. С `allow_origins=["*"]` токен читается, а вместе с ним чужой сайт
  получает `/api/auth/messages/{username}` (переписка Telegram),
  `POST /api/auth/messages/{username}` (сообщения от лица пользователя),
  `GET /api/tg/chats` (контакты) и запуск воркеров.

Поэтому здесь проверяется не наличие заголовка, а РАЗРЕШЕНИЕ: чужой
`Origin` не получает `ACAO` ни на странице с токеном, ни на `/api/*`;
свой — получает ровно себя, а не `*`.
"""

from __future__ import annotations

from fastapi.middleware.cors import CORSMiddleware

from job_monitor.security import APP_TOKEN, TOKEN_HEADER

ACAO = "access-control-allow-origin"

# Ровно те источники, на которых приложение само себя обслуживает: uvicorn
# слушает 127.0.0.1:8000 (job_monitor/cli.py, Makefile, start_web.bat), а
# `http://localhost:8000` — тот же сервер, набранный именем.
EXPECTED_ORIGINS = ["http://127.0.0.1:8000", "http://localhost:8000"]
FOREIGN_ORIGIN = "https://evil.example"


def _cors_kwargs() -> dict:
    from api.main import app

    declared = [entry for entry in app.user_middleware if entry.cls is CORSMiddleware]
    assert len(declared) == 1, (
        f"ожидался ровно один CORSMiddleware, найдено {len(declared)} — "
        "разрешения источников больше не читаются из одного места"
    )
    return dict(declared[0].kwargs)


# ── Поведение: чужой источник не получает ничего ──────────────────────


def test_a_foreign_origin_cannot_read_the_page_that_carries_the_token(raw_client):
    """Главный сценарий находки 9, целиком."""
    response = raw_client.get("/", headers={"Origin": FOREIGN_ORIGIN})

    # Страховка от вакуумности: проверка имеет смысл только пока `/` и
    # правда отдаёт токен. Если `<meta name="app-token">` когда-нибудь
    # уедет, это должно быть видно здесь, а не молча ослабить тест.
    assert response.status_code == 200
    assert APP_TOKEN in response.text, (
        "`GET /` перестал отдавать токен — проверка утечки токена через CORS "
        "стала вакуумной, перепишите её под новый способ передачи токена"
    )
    assert ACAO not in response.headers, (
        f"страница с APP_TOKEN отдаётся источнику {FOREIGN_ORIGIN}: "
        f"{response.headers.get(ACAO)!r} — любой открытый в браузере сайт "
        "читает токен и получает полный доступ к приложению"
    )


def test_a_foreign_origin_cannot_read_an_api_response_even_with_a_valid_token(raw_client):
    """Второй шаг той же атаки: токен уже добыт (например из скриншота или
    из истории команд) — ответ `/api/*` всё равно не должен читаться чужой
    страницей."""
    response = raw_client.get(
        "/api/state", headers={"Origin": FOREIGN_ORIGIN, TOKEN_HEADER: APP_TOKEN}
    )
    assert response.status_code == 200, response.text
    assert ACAO not in response.headers


def test_a_foreign_origin_is_refused_at_the_preflight(raw_client):
    response = raw_client.options(
        "/", headers={"Origin": FOREIGN_ORIGIN, "Access-Control-Request-Method": "GET"}
    )
    assert response.status_code == 400, response.text
    assert response.headers.get(ACAO) is None


# ── Поведение: свой источник получает ровно себя ──────────────────────


def test_the_own_origin_is_allowed_and_never_answered_with_a_wildcard(raw_client):
    """Обратная сторона проверок выше: они не должны держаться на том, что
    CORS просто выключен целиком."""
    for origin in EXPECTED_ORIGINS:
        response = raw_client.get("/", headers={"Origin": origin})
        assert response.headers.get(ACAO) == origin, (
            f"источник {origin} — это само приложение, и ответ должен нести "
            f"его же: {response.headers.get(ACAO)!r}"
        )
        assert response.headers.get(ACAO) != "*"


def test_the_own_origin_passes_the_preflight(raw_client):
    response = raw_client.options(
        "/",
        headers={
            "Origin": EXPECTED_ORIGINS[0],
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers.get(ACAO) == EXPECTED_ORIGINS[0]


def test_credentials_are_not_allowed_cross_origin(raw_client):
    """`allow_credentials=True` вместе с чужим источником означал бы, что
    браузер шлёт туда cookies и Authorization. Приложение на cookies не
    держится (доступ — заголовок `X-App-Token`), так что разрешать нечего."""
    assert _cors_kwargs().get("allow_credentials", False) is False, (
        "включён allow_credentials: браузер начнёт прикладывать к кросс-"
        "доменным запросам учётные данные, которых приложению не требуется"
    )
    response = raw_client.get("/", headers={"Origin": EXPECTED_ORIGINS[0]})
    assert "access-control-allow-credentials" not in response.headers


# ── Сам список источников ─────────────────────────────────────────────


def test_the_origin_allow_list_is_exactly_the_two_loopback_origins():
    kwargs = _cors_kwargs()
    assert sorted(kwargs.get("allow_origins", [])) == sorted(EXPECTED_ORIGINS), (
        f"список источников изменился: {kwargs.get('allow_origins')!r}. Приложение "
        "обслуживает себя только на loopback:8000; любой другой источник — это "
        "чужая страница в браузере пользователя"
    )
    assert "*" not in kwargs.get("allow_origins", [])
    assert kwargs.get("allow_origin_regex") is None, (
        "allow_origin_regex обходит список источников целиком — образец вида "
        f"{kwargs.get('allow_origin_regex')!r} может совпасть с чужим доменом"
    )


def test_wildcard_methods_and_headers_are_bounded_by_the_origin_list(raw_client):
    """`allow_methods=["*"]` и `allow_headers=["*"]` в `api/main.py` стоят
    осознанно и сами по себе безопасны — но ровно потому, что действуют
    только для разрешённых источников. Здесь закрепляется именно эта
    зависимость: широкий список методов не должен превращаться в
    разрешение для чужого источника."""
    allowed = raw_client.options(
        "/",
        headers={
            "Origin": EXPECTED_ORIGINS[0],
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": TOKEN_HEADER.lower(),
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers.get(ACAO) == EXPECTED_ORIGINS[0]

    foreign = raw_client.options(
        "/",
        headers={
            "Origin": FOREIGN_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": TOKEN_HEADER.lower(),
        },
    )
    assert foreign.status_code == 400
    assert foreign.headers.get(ACAO) is None


def test_a_cross_origin_preflight_to_the_api_is_refused_by_the_token_gate(raw_client):
    """Порядок middleware — тоже часть защиты, и он неочевиден.

    `app_token_middleware` зарегистрирован ПОСЛЕ CORSMiddleware, а
    Starlette оборачивает стек в обратном порядке — то есть токен-гейт
    стоит СНАРУЖИ CORS. Браузер не прикладывает `X-App-Token` к preflight
    (это запрос самого браузера, не страницы), поэтому предполётный
    `OPTIONS /api/*` всегда получает 403 и ни один кросс-доменный запрос к
    `/api/*` с этим заголовком не доходит до отправки — даже с
    разрешённого источника. Поменяйте порядок регистрации, и preflight
    начнёт отвечать 200 без токена.
    """
    for origin in (EXPECTED_ORIGINS[0], FOREIGN_ORIGIN):
        response = raw_client.options(
            "/api/config",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": TOKEN_HEADER.lower(),
            },
        )
        assert response.status_code == 403, (
            f"предполётный запрос к /api/* от {origin} прошёл токен-гейт: "
            f"{response.status_code}"
        )
        assert response.headers.get(ACAO) is None
