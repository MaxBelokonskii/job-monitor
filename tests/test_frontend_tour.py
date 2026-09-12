"""Инварианты пошагового обучения.

Тур — это копия знаний о вёрстке, живущая отдельно от вёрстки. Такая копия
расходится с оригиналом молча: переименовали `id` — тур подсвечивает пустоту,
и ни один существующий тест этого не заметит. Поэтому связь «шаг → элемент»
проверяется здесь, а геометрия затемнения отделена от DOM и исполняется под
node.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from conftest import requires_node

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
INDEX_HTML = FRONTEND_DIR / "index.html"
APP_JS = FRONTEND_DIR / "app.js"
TOUR_JS = FRONTEND_DIR / "tour.js"

skip_without_node = requires_node


def _index_source() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _app_source() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _tour_source() -> str:
    return TOUR_JS.read_text(encoding="utf-8")


def _extract_function_source(source: str, name: str) -> str:
    """Исходник функции верхнего уровня — по соглашению файлов фронтенда
    такая функция закрывается `}` в нулевой колонке."""
    match = re.search(
        rf"^(?:async )?function {name}\(.*?^\}}", source, re.DOTALL | re.MULTILINE
    )
    assert match, f"функция {name} не найдена"
    return match.group(0)


def _run_node(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )


# ── switchPage: одна реализация на навигацию и на тур ──────────────────


def test_page_switching_is_a_function_not_a_closure() -> None:
    """Тур обязан переключать вкладку той же функцией, что и клик по
    навигации. Пока переключение жило внутри обработчика, вызвать его было
    неоткуда, и тур завёл бы вторую копию — расходящуюся при первой же
    правке навигации."""
    source = _app_source()
    assert re.search(r"^async function switchPage\(page\)", source, re.MULTILINE), (
        "switchPage не объявлена как функция верхнего уровня в app.js"
    )


def test_the_nav_handler_delegates_instead_of_repeating_itself() -> None:
    """Обработчик `.nav-item` не должен сам снимать и ставить `active`:
    иначе правка в switchPage не дойдёт до кликов по навигации."""
    source = _app_source()
    handler = re.search(
        r"document\.querySelectorAll\('\.nav-item\[data-page\]'\)"
        r"\.forEach\(item => \{(.*?)\n\}\);",
        source,
        re.DOTALL,
    )
    assert handler, "обработчик навигации не найден — его форма изменилась?"
    body = handler.group(1)
    assert "switchPage" in body, "обработчик навигации не зовёт switchPage"
    assert "classList" not in body, (
        "обработчик навигации снова сам правит классы — это копия switchPage"
    )
