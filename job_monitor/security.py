from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse

APP_TOKEN = secrets.token_urlsafe(32)
TOKEN_HEADER = "X-App-Token"
TOKEN_PLACEHOLDER = "__APP_TOKEN__"
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]


def _tokens_match(supplied: str, expected: str) -> bool:
    # secrets.compare_digest raises TypeError on non-ASCII str input; comparing
    # as bytes avoids that entirely (utf-8 encoding never fails).
    return secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


async def app_token_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if request.url.path.startswith("/api/"):
        supplied = request.headers.get(TOKEN_HEADER, "")
        if not _tokens_match(supplied, APP_TOKEN):
            return JSONResponse({"detail": "invalid app token"}, status_code=403)
    return await call_next(request)


CSP: str = "; ".join((
    "default-src 'self'",
    # No 'unsafe-inline': frontend/index.html carries no inline <script> and no
    # on*= handler attributes any more (every control goes through the
    # data-action delegation map in frontend/app.js), so an injected
    # `<img onerror=...>` or `javascript:` URL from Telegram/hh.ru content has
    # nothing left to execute with. tests/test_frontend_events.py pins both
    # halves of that: drop one and the other stops being safe.
    "script-src 'self'",
    # 'unsafe-inline' stays here on purpose: the markup uses inline `style`
    # attributes throughout and a style attribute is not a script vector.
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data:",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "object-src 'none'",
))
SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


#: «Кэшируй, но каждый раз спрашивай». Не `no-store`: `ETag` остаётся в
#: силе, и неизменившийся файл отдаётся как 304 — тело не передаётся.
#:
#: Без этого заголовка `StaticFiles` присылает только `ETag` и
#: `Last-Modified`, а браузер вправе применить эвристику и вовсе не
#: обратиться к серверу. Найдено на живом приложении: после обновления
#: кода страница показывала СТАРЫЙ `app.js` — 78351 байт против 79005 на
#: диске, — и правка выглядела недоехавшей. Для инструмента, который
#: правят и тут же перезагружают, это дороже любого трафика: на
#: `127.0.0.1` трафика нет.
REVALIDATE = "no-cache"


async def security_headers_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    for key, value in SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    response.headers.setdefault("Cache-Control", REVALIDATE)
    return response
