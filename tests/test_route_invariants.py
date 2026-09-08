"""Guards the token-gate invariant: every route is either under /api/
(token-gated by app_token_middleware) or explicitly allowlisted as public.

This is the guard the next two plans need before they add routers — a new
router mounted outside /api/ by mistake would otherwise ship unauthenticated
by default and nothing would catch it.
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest

ALLOWED_PUBLIC_PATHS = {"/", "/static"}


def _iter_route_paths(routes: Iterable[object]) -> Iterable[str]:
    """Yield every concrete path in a FastAPI/Starlette route tree.

    Routers included via app.include_router() may be wrapped (the wrapper
    type and attribute name are a FastAPI implementation detail that has
    changed across versions), so this walks generically via duck typing
    instead of importing a specific private class.
    """
    for route in routes:
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            yield from _iter_route_paths(original_router.routes)
            continue
        nested_routes = getattr(route, "routes", None)
        if nested_routes:
            yield from _iter_route_paths(nested_routes)
            continue
        path = getattr(route, "path", None)
        if path is not None:
            yield path


def test_all_routes_are_under_api_or_allowlisted() -> None:
    from api.main import app

    paths = list(_iter_route_paths(app.routes))
    assert paths, "no routes found — did app.routes change shape?"
    for path in paths:
        assert path.startswith("/api/") or path in ALLOWED_PUBLIC_PATHS, (
            f"route {path!r} is neither under /api/ (token-gated) nor in the "
            f"explicit public allowlist {ALLOWED_PUBLIC_PATHS} — a route "
            "outside /api/ ships unauthenticated by default."
        )


@pytest.mark.parametrize(
    "path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"]
)
def test_docs_and_openapi_endpoints_are_disabled(raw_client, path: str) -> None:
    """FastAPI's own docs/openapi routes aren't under /api/, so they used to
    return 200 with no token at all — unnecessary surface on a tool whose
    premise is a token-gated API."""
    assert raw_client.get(path).status_code == 404
