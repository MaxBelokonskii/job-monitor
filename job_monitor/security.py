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
