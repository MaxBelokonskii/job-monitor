"""Guards the token-gate invariant: every route is either under /api/
(token-gated by app_token_middleware) or explicitly allowlisted as public.

This is the guard the next two plans need before they add routers — a new
router mounted outside /api/ by mistake would otherwise ship unauthenticated
by default and nothing would catch it.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

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


API_DIR = Path(__file__).resolve().parents[1] / "api"


def _handlers_without_a_return_type(directory: Path) -> list[str]:
    """Функции без аннотации возвращаемого типа во всём дереве `directory`.

    Обход рекурсивный: `glob("*.py")` смотрел только в корень пакета, и
    первый же `api/routers/tg.py` выпал бы из-под сторожа целиком — вместе с
    рантайм-последствием, описанным в докстринге теста ниже.
    """
    import ast

    missing: list[str] = []
    for source_file in sorted(directory.rglob("*.py")):
        if "__pycache__" in source_file.parts:
            continue
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.returns is None:
                relative = source_file.relative_to(directory).as_posix()
                missing.append(f"{relative}:{node.lineno} {node.name}")
    return missing


def test_every_api_handler_declares_a_return_type() -> None:
    """Глобальное ограничение ветки — «весь новый код с аннотациями типов», —
    но обработчики роутов долго оставались исключением: в `api/` они писались
    до перехода на пакет и возвращаемого типа не объявляли. Проверка держится
    на AST, а не на импорте: FastAPI оборачивает функции, и у обёртки
    аннотации уже не те, что у исходника. Функции модуля, не являющиеся
    обработчиками (помощники вроде `get_system_log`), тоже обязаны их иметь —
    правило одно для всего пакета.

    **Это не стилевое требование.** У обработчика FastAPI аннотация возврата
    становится `response_model`, то есть включает РАНТАЙМ-ВАЛИДАЦИЮ ответа.
    Проверено на отдельном приложении (fastapi 0.141.1): обработчик с
    `-> dict[str, str]`, вернувший `{"x": 1}`, отдаёт **500**, а не ответ с
    числом. Сегодня все аннотации в `api/` честные, и живой обход всех
    эндпоинтов даёт ноль 500, но следующий автор, чей ответ разойдётся с
    аннотацией, узнает об этом из 500-ки — поэтому сказано здесь.
    Практическое следствие: тип надо расширять вместе с ответом
    (`dict[str, str | None]`, а не `dict[str, str]`, если в поле бывает
    `None`), а не наоборот — сузить аннотацию значит поломать эндпоинт.

    **Обработчик, возвращающий `Response`/`FileResponse`, должен
    аннотироваться именно этим классом.** Здесь та же механика работает
    наизнанку: аннотация-наследник `Response` заставляет FastAPI НЕ строить
    `response_model` вовсе (проверено: `route.response_model is None`), а
    объект `Response` проходит мимо валидации в любом случае. То есть
    `-> dict[str, str]` у обработчика, возвращающего `FileResponse`, ошибкой
    не станет — он просто соврёт про тип и молча выключит проверку, которую
    эта аннотация должна была включить. Обратное так же верно: аннотация
    класса-ответа у обработчика, возвращающего словарь, снимает валидацию с
    этого эндпоинта целиком.
    """
    missing = _handlers_without_a_return_type(API_DIR)
    assert not missing, (
        "без аннотации возвращаемого типа: " + ", ".join(missing)
        + " (аннотация здесь — это response_model, то есть валидация ответа "
        "на рантайме; см. докстринг теста)"
    )


def test_the_return_type_check_looks_inside_subpackages(tmp_path) -> None:
    """`api_dir.glob("*.py")` был нерекурсивен: обработчики в `api/routers/`
    — самая ожидаемая форма роста этого пакета — не проверялись бы вовсе.
    Мутация проверяет и обход вложенных каталогов, и то, что `__pycache__`
    из него исключён."""
    (tmp_path / "routers").mkdir()
    (tmp_path / "routers" / "tg.py").write_text(
        "async def tg_start():\n    return {}\n", encoding="utf-8"
    )
    (tmp_path / "routers" / "__pycache__").mkdir()
    (tmp_path / "routers" / "__pycache__" / "stale.py").write_text(
        "def gone():\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "main.py").write_text(
        "async def root() -> str:\n    return ''\n", encoding="utf-8"
    )

    assert _handlers_without_a_return_type(tmp_path) == ["routers/tg.py:1 tg_start"]
