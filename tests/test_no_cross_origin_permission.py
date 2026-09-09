"""Приложение НИКОГДА не разрешает чужой странице читать свои ответы (D1).

Предшественник этого файла (`tests/test_cors.py`) пиннил allow-лист
`CORSMiddleware` — «свой источник получает ровно себя, чужой не получает
ничего». Middleware больше нет, и охраняется теперь не список, а
**отсутствие разрешения**.

Почему это строго строже allow-листа:

* страница отдаётся с того же адреса, куда стучится (`const API = '/api'`
  в `frontend/app.js` — относительный путь), поэтому ни один запрос UI не
  кросс-доменный и разрешать CORS-ом было нечего;
* `GET /` НЕ под `/api/`, то есть не под токен-гейтом
  (`job_monitor/security.py::app_token_middleware`), и отдаёт `APP_TOKEN` в
  `<meta name="app-token">` — так его получает сам UI;
* `TrustedHostMiddleware` такой запрос пропускает: для `fetch` из чужой
  вкладки на `http://127.0.0.1:8000/` заголовок `Host` — это
  `127.0.0.1:8000`, он и разрешён;
* значит единственное, что мешает любой открытой в браузере странице
  прочитать токен со `/`, — то, что браузер не отдаёт скрипту тело ответа
  без `Access-Control-Allow-Origin`. С токеном чужой сайт получает
  `/api/auth/messages/{username}` (переписка Telegram),
  `POST /api/auth/messages/{username}` (сообщения от лица пользователя),
  `GET /api/tg/chats` (контакты) и запуск воркеров.

Мутационный аудит показал, что расширение allow-листа до
`allow_origins=["*"]` проходило при полностью зелёном наборе. Пока в
приложении есть хоть какая-то настройка источников, эта мутация
представима; убрав middleware, мы убрали и её. Поэтому здесь проверяется,
что `Access-Control-*` не отдаётся **ни одному** источнику — включая
собственный: любая попытка вернуть allow-лист (даже «безопасный», ровно из
двух loopback-адресов) роняет этот файл.
"""

from __future__ import annotations

import pytest

from job_monitor.security import APP_TOKEN, TOKEN_HEADER

ACAO = "access-control-allow-origin"

# Адреса, на которых приложение обслуживает само себя (uvicorn слушает
# 127.0.0.1:8000 — `job_monitor/cli.py`, Makefile, start_web.bat), и чужой
# сайт. Собственный источник здесь не для того, чтобы что-то разрешить: он
# ловит возврат allow-листа, который чужому источнику по-прежнему не отдал
# бы ничего и потому был бы неотличим от отсутствия middleware.
OWN_ORIGINS = ["http://127.0.0.1:8000", "http://localhost:8000"]
FOREIGN_ORIGINS = ["https://evil.example", "null", "http://127.0.0.1:9000"]
ALL_ORIGINS = OWN_ORIGINS + FOREIGN_ORIGINS

# По одному представителю каждого класса ответа: страница с токеном, ответ
# API под токеном, ответ API без токена (403 от гейта) и статика — файлы
# `/static/*` монтируются отдельным приложением StaticFiles, и middleware у
# них ровно те же, но это стоит проверить, а не предположить.
PATHS = ["/", "/api/state", "/static/app.js"]


def _cors_headers(response) -> dict[str, str]:
    return {
        name: value
        for name, value in response.headers.items()
        if name.lower().startswith("access-control-")
    }


# ── Ни один ответ ни одному источнику не несёт разрешения ─────────────


def test_the_page_that_carries_the_token_still_carries_it(raw_client) -> None:
    """Страховка от вакуумности всего файла.

    Проверки ниже имеют смысл только пока `/` и правда отдаёт токен. Если
    `<meta name="app-token">` когда-нибудь уедет, это должно быть видно
    здесь, а не молча превратить охрану утечки токена в проверку ни о чём.
    """
    response = raw_client.get("/")
    assert response.status_code == 200
    assert APP_TOKEN in response.text, (
        "`GET /` перестал отдавать токен — охрана его утечки через CORS стала "
        "вакуумной, перепишите файл под новый способ передачи токена"
    )


@pytest.mark.parametrize("origin", ALL_ORIGINS)
@pytest.mark.parametrize("path", PATHS)
def test_no_origin_is_ever_granted_permission_to_read_a_response(
    raw_client, path: str, origin: str
) -> None:
    response = raw_client.get(
        path, headers={"Origin": origin, TOKEN_HEADER: APP_TOKEN}
    )
    assert response.status_code == 200, response.text
    assert _cors_headers(response) == {}, (
        f"ответ {path} источнику {origin} несёт разрешения CORS: "
        f"{_cors_headers(response)} — приложение однодоменное, разрешать "
        "нечего, а любое разрешение на `/` отдаёт APP_TOKEN чужой странице"
    )


@pytest.mark.parametrize("origin", ALL_ORIGINS)
def test_an_api_response_stays_unreadable_even_when_the_token_leaked(
    raw_client, origin: str
) -> None:
    """Второй шаг атаки: токен уже добыт (со скриншота, из истории команд).
    Ответ `/api/*` всё равно не должен читаться чужой страницей."""
    response = raw_client.get(
        "/api/tg/chats", headers={"Origin": origin, TOKEN_HEADER: APP_TOKEN}
    )
    assert response.status_code == 200, response.text
    assert ACAO not in response.headers


@pytest.mark.parametrize("origin", ALL_ORIGINS)
def test_a_403_from_the_token_gate_carries_no_permission_either(
    raw_client, origin: str
) -> None:
    """Ответ гейта — тоже ответ. `CORSMiddleware` добавлял заголовки и к
    нему (он стоял внутри токен-гейта), а сам факт «403, а не ошибка сети»
    — это уже информация о том, что по этому адресу кто-то слушает."""
    response = raw_client.get("/api/state", headers={"Origin": origin})
    assert response.status_code == 403
    assert _cors_headers(response) == {}


# ── Preflight ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("origin", ALL_ORIGINS)
@pytest.mark.parametrize("path", PATHS)
def test_a_preflight_never_gets_an_allowing_answer(
    raw_client, path: str, origin: str
) -> None:
    """Без middleware `OPTIONS` обслуживать некому: маршруты объявляют GET,
    и Starlette отвечает `405 Method Not Allowed` без единого
    `Access-Control-*`. Браузер трактует такой предполёт как отказ и не
    отправляет сам запрос.
    """
    response = raw_client.options(
        path,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": TOKEN_HEADER.lower(),
        },
    )
    assert response.status_code != 200, (
        f"предполётный OPTIONS {path} от {origin} получил 200 — кто-то вернул "
        "обработку preflight, то есть и разрешение источников"
    )
    assert _cors_headers(response) == {}, (
        f"предполёт {path} от {origin} разрешён: {_cors_headers(response)}"
    )


@pytest.mark.parametrize("origin", ALL_ORIGINS)
def test_a_preflight_to_the_api_is_refused_by_the_token_gate(
    raw_client, origin: str
) -> None:
    """Порядок middleware — тоже часть защиты, и он неочевиден.

    Браузер не прикладывает `X-App-Token` к предполёту (это запрос самого
    браузера, не страницы), а `app_token_middleware` завёрнут снаружи всех
    маршрутов, поэтому `OPTIONS /api/*` получает 403 раньше, чем дело
    доходит до маршрутизации. Ни один кросс-доменный запрос к `/api/*` с
    этим заголовком не доходит даже до отправки.
    """
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
    assert _cors_headers(response) == {}


# ── Middleware не должен вернуться незамеченным ────────────────────────


def test_no_cors_middleware_is_installed() -> None:
    """Прямая проверка структуры, в дополнение к поведенческим выше.

    Поведение — главное, но одна форма возврата middleware поведением
    почти не ловится: `CORSMiddleware(allow_origins=[])` не отдаёт
    заголовков никому. Сам по себе он безвреден; опасен он тем, что это
    готовое место, куда следующая правка допишет источник, а `["*"]` — на
    один символ от пустого списка. Ошибку дешевле не пустить обратно
    целиком.
    """
    from api.main import app

    installed = [entry.cls.__name__ for entry in app.user_middleware]
    assert not any("CORS" in name for name in installed), (
        f"CORSMiddleware вернулся в стек: {installed}. Приложение однодоменное "
        "(страница и API на одном адресе, `const API = '/api'`), поэтому "
        "разрешать источники нечего, а строка `allow_origins` — готовое место "
        "для мутации `[\"*\"]`, которая отдаёт APP_TOKEN со `/` любому "
        "открытому в браузере сайту"
    )


def _code_tokens(source_file) -> set[str]:
    """Имена и строковые литералы КОДА файла, без комментариев.

    Комментарии выбрасываются намеренно: `api/main.py` объясняет отсутствие
    middleware словами «никакого CORSMiddleware здесь нет» и прямо называет
    мутацию `allow_origins=["*"]`, от которой охраняет. Проверка запрета не
    должна запрещать объяснение запрета.
    """
    import tokenize

    with open(source_file, "rb") as handle:
        tokens = list(tokenize.tokenize(handle.readline))
    return {
        token.string
        for token in tokens
        if token.type in (tokenize.NAME, tokenize.STRING, tokenize.OP)
    } | {
        # Строковые литералы сравниваются ещё и по содержимому: заголовок
        # можно выставить руками, `response.headers["Access-Control-..."]`.
        token.string.strip("'\"")
        for token in tokens
        if token.type == tokenize.STRING
    }


def test_the_application_code_never_names_an_origin_permission() -> None:
    """Разрешение можно завести и в обход `app.add_middleware`.

    `app.user_middleware` не увидит ни `Starlette(middleware=[...])`, ни
    собственный middleware, который сам выставляет
    `Access-Control-Allow-Origin`. Поведенческие проверки выше ловят такие
    формы только для перечисленных источников, а `Origin`-ов бесконечно
    много — поэтому здесь запрещены сами имена, без которых разрешение
    выдать нельзя.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    forbidden = {"allow_origins", "allow_origin_regex", "CORSMiddleware"}
    offenders: list[str] = []
    for package in ("api", "job_monitor"):
        for source in sorted((root / package).rglob("*.py")):
            if "__pycache__" in source.parts:
                continue
            tokens = _code_tokens(source)
            named = sorted(forbidden & tokens) + sorted(
                token for token in tokens if token.lower().startswith("access-control")
            )
            offenders += [
                f"{source.relative_to(root).as_posix()}: {name}" for name in named
            ]
    assert not offenders, (
        "в коде приложения снова заведено разрешение источников: "
        + ", ".join(offenders)
    )
