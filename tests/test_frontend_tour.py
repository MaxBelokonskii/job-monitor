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


# ── Геометрия затемнения ──────────────────────────────────────────────
#
# Затемнение собрано из четырёх прямоугольников, а не из одной тени
# (`box-shadow: 0 0 0 9999px`), потому что тень не делает дырки в попадании
# курсора: подсвеченную кнопку было бы нельзя нажать, а сквозной клик
# проходил бы и по затемнённой навигации, уводя страницу из-под тура.

GEOMETRY = ("clampRect", "maskRects", "cardPosition")


def _geometry_source() -> str:
    source = _tour_source()
    constants = re.search(r"^const TOUR_GAP = \d+;", source, re.MULTILINE)
    assert constants, "TOUR_GAP не найдена в tour.js"
    return "\n".join(
        [constants.group(0)]
        + [_extract_function_source(source, name) for name in GEOMETRY]
    )


VIEWPORT = {"width": 1280, "height": 800}
CARD = {"width": 340, "height": 260}

# Цель в середине, у каждого края и в каждом углу: ровно те положения, где
# прямоугольник маски может выродиться в отрицательный размер.
HOLES = {
    "середина": {"x": 500, "y": 350, "width": 280, "height": 90},
    "у левого края": {"x": 0, "y": 350, "width": 280, "height": 90},
    "у правого края": {"x": 1000, "y": 350, "width": 280, "height": 90},
    "у верхнего края": {"x": 500, "y": 0, "width": 280, "height": 90},
    "у нижнего края": {"x": 500, "y": 710, "width": 280, "height": 90},
    "левый верхний угол": {"x": 0, "y": 0, "width": 280, "height": 90},
    "правый нижний угол": {"x": 1000, "y": 710, "width": 280, "height": 90},
    "выходит за экран": {"x": 1200, "y": 760, "width": 400, "height": 200},
}


@skip_without_node
def test_the_four_masks_cover_the_screen_except_the_hole() -> None:
    """Суммарная площадь масок обязана равняться площади экрана минус дырка.

    Проверяется площадью И пересечениями (следующий тест): по одной площади
    сдвинутая маска прошла бы, а именно сдвиг и оставляет светлую полосу.
    """
    script = "\n".join((
        _geometry_source(),
        f"const viewport = {json.dumps(VIEWPORT)};",
        f"const holes = {json.dumps(HOLES, ensure_ascii=False)};",
        """
        function check(name, cond) {
          if (!cond) { console.error('FAIL:', name); process.exitCode = 1; }
        }
        const area = r => r.width * r.height;
        for (const [name, raw] of Object.entries(holes)) {
          const hole = clampRect(raw, viewport);
          const m = maskRects(raw, viewport);
          const parts = [m.top, m.bottom, m.left, m.right];
          for (const p of parts) {
            check(name + ': маска не отрицательна', p.width >= 0 && p.height >= 0);
          }
          const covered = parts.reduce((sum, p) => sum + area(p), 0);
          const expected = viewport.width * viewport.height - area(hole);
          check(name + ': сумма масок = экран минус дырка (' + covered + ' vs ' + expected + ')',
                Math.abs(covered - expected) < 0.5);
        }
        const whole = maskRects(null, viewport);
        check('без цели экран затемнён целиком',
              area(whole.top) === viewport.width * viewport.height);
        """,
    ))
    result = _run_node(script)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


@skip_without_node
def test_no_mask_overlaps_the_hole() -> None:
    """Обратная сторона проверки площадью: ни один прямоугольник затемнения
    не накрывает подсвеченный элемент, иначе он окажется под плёнкой и
    перестанет нажиматься."""
    script = "\n".join((
        _geometry_source(),
        f"const viewport = {json.dumps(VIEWPORT)};",
        f"const holes = {json.dumps(HOLES, ensure_ascii=False)};",
        """
        function check(name, cond) {
          if (!cond) { console.error('FAIL:', name); process.exitCode = 1; }
        }
        function overlaps(a, b) {
          return a.x < b.x + b.width && b.x < a.x + a.width
              && a.y < b.y + b.height && b.y < a.y + a.height;
        }
        for (const [name, raw] of Object.entries(holes)) {
          const hole = clampRect(raw, viewport);
          if (hole.width === 0 || hole.height === 0) continue;
          const m = maskRects(raw, viewport);
          for (const [side, rect] of Object.entries(m)) {
            check(name + ': маска ' + side + ' накрывает дырку', !overlaps(rect, hole));
          }
        }
        """,
    ))
    result = _run_node(script)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


@skip_without_node
def test_the_card_stays_on_screen_and_off_the_hole() -> None:
    """Карточка целиком внутри экрана при любом положении цели и не
    накрывает подсвеченный элемент.

    Накрыть его значит сделать невозможным то, ради чего всё затевалось:
    читать инструкцию и выполнять её одновременно. Гарантия даётся для
    целей не выше 40% экрана — все цели сценария такие; для вырожденного
    случая «цель во весь экран» проверяется только попадание в экран.
    """
    script = "\n".join((
        _geometry_source(),
        f"const viewport = {json.dumps(VIEWPORT)};",
        f"const card = {json.dumps(CARD)};",
        f"const holes = {json.dumps(HOLES, ensure_ascii=False)};",
        """
        function check(name, cond) {
          if (!cond) { console.error('FAIL:', name); process.exitCode = 1; }
        }
        function overlaps(a, b) {
          return a.x < b.x + b.width && b.x < a.x + a.width
              && a.y < b.y + b.height && b.y < a.y + a.height;
        }
        const cases = Object.entries(holes).concat([['без цели', null]]);
        for (const [name, raw] of cases) {
          const pos = cardPosition(raw, card, viewport);
          const box = { x: pos.x, y: pos.y, width: card.width, height: card.height };
          check(name + ': карточка не левее экрана', box.x >= 0);
          check(name + ': карточка не выше экрана', box.y >= 0);
          check(name + ': карточка не правее экрана', box.x + box.width <= viewport.width);
          check(name + ': карточка не ниже экрана', box.y + box.height <= viewport.height);
          if (raw) {
            const hole = clampRect(raw, viewport);
            check(name + ': карточка не накрывает цель', !overlaps(box, hole));
          }
        }
        const huge = { x: 0, y: 0, width: viewport.width, height: viewport.height };
        const pos = cardPosition(huge, card, viewport);
        check('цель во весь экран: карточка всё равно в экране',
              pos.x >= 0 && pos.y >= 0
              && pos.x + card.width <= viewport.width
              && pos.y + card.height <= viewport.height);
        """,
    ))
    result = _run_node(script)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


def test_the_tour_script_is_versioned_like_the_others() -> None:
    """Отпечаток статики считается по списку файлов в api/main.py. Файл,
    забытый в этом списке, браузер будет отдавать из кэша и после правки —
    ровно тот дефект, из-за которого правки «не приезжали» в app.js."""
    main_py = Path(__file__).resolve().parents[1] / "api" / "main.py"
    source = main_py.read_text(encoding="utf-8")
    versioned = re.search(r"VERSIONED_ASSETS = \(([^)]*)\)", source)
    assert versioned, "VERSIONED_ASSETS не найдена в api/main.py"
    assert '"tour.js"' in versioned.group(1), (
        "tour.js не участвует в отпечатке статики — правки тура не дойдут до браузера"
    )


def test_the_tour_script_loads_before_the_app() -> None:
    """ACTIONS в app.js — объектный литерал сокращённой записи, и имена
    функций тура должны быть объявлены к моменту его вычисления."""
    source = _index_source()
    tour_at = source.find("tour.js")
    app_at = source.find("app.js")
    assert tour_at != -1, "tour.js не подключён в index.html"
    assert app_at != -1, "app.js не подключён в index.html"
    assert tour_at < app_at, (
        "tour.js подключён после app.js — ACTIONS не увидит его функций"
    )
    assert "tour.js?v=__ASSET_VERSION__" in source, (
        "адрес tour.js без версии — браузер отдаст его из кэша после правки"
    )
