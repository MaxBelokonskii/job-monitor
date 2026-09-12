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


# ── Сценарий ──────────────────────────────────────────────────────────

STEPS_BLOCK = re.compile(r"^const TOUR_STEPS = \[(.*?)^\];", re.DOTALL | re.MULTILINE)
STEP_ENTRY = re.compile(
    r"\{\s*page:\s*(null|'[a-z]+'),"
    r"\s*target:\s*(null|'[^']+'),"
    r"\s*platforms:\s*'([a-z]+)'"
)
ID_ATTR = re.compile(r'\bid="([^"]+)"')
PAGES = {"overview", "settings"}
PLATFORMS = {"all", "tg", "hh"}

# Контейнеры, которые заполняет JS: до ответа сервера это пустые узлы
# нулевой высоты. Подсветить такой значит подсветить полоску в ноль
# пикселей, поэтому тур целится в статическую обёртку вокруг них.
JS_FILLED = {
    "hhLoginButtons", "resumeList", "presetBar", "kwList", "hhKwList",
    "exList", "hhExList", "channelEditList", "seleniumStepsList", "recentLog",
}


def _steps() -> list[tuple[str | None, str | None, str]]:
    block = STEPS_BLOCK.search(_tour_source())
    assert block, "массив TOUR_STEPS не найден — изменилась его форма?"
    parsed = []
    for page, target, platforms in STEP_ENTRY.findall(block.group(1)):
        unquote = lambda v: None if v == "null" else v.strip("'")  # noqa: E731
        parsed.append((unquote(page), unquote(target), platforms))
    return parsed


def test_the_scenario_parsed_at_all() -> None:
    """Непустота разбора. Без неё сломанное регулярное выражение давало бы
    зелёный прогон на пустом множестве шагов — и все проверки ниже
    превратились бы в проверки ничего."""
    steps = _steps()
    assert len(steps) >= 10, f"разобрано всего {len(steps)} шагов"
    assert sum(1 for _page, target, _p in steps if target) >= 8, (
        "почти ни у одного шага нет цели — разбор поля target сломан"
    )


def test_every_target_exists_in_the_markup() -> None:
    """Главный тест файла. Тур — копия знаний о вёрстке; без этой проверки
    переименование `id` оставляет тур подсвечивающим пустоту, и молча."""
    ids = set(ID_ATTR.findall(_index_source()))
    missing = [
        target for _page, target, _p in _steps()
        if target and target.lstrip("#") not in ids
    ]
    assert not missing, f"шаги ведут на элементы, которых нет в разметке: {missing}"


def test_the_existence_check_is_not_vacuous() -> None:
    """Обратная сторона: проверка обязана уметь падать."""
    ids = set(ID_ATTR.findall(_index_source()))
    assert ids, "из разметки не извлечён ни один id — сканер сломан"
    assert "safeModeRow" in ids, "сканер не видит id, который точно есть в разметке"
    assert "заведомо-отсутствующий-id" not in ids, "сканер находит то, чего нет"


def test_no_target_is_a_container_the_javascript_fills() -> None:
    """Решение из раздела 2 спецификации: целиться в статическую обёртку.
    `#hhLoginButtons` до ответа сервера — пустой div нулевой высоты."""
    offenders = [
        target for _page, target, _p in _steps()
        if target and target.lstrip("#") in JS_FILLED
    ]
    assert not offenders, (
        f"цели подсветки заполняются из JS и до ответа сервера пусты: {offenders}"
    )


def test_every_step_names_a_known_page() -> None:
    for page, target, platforms in _steps():
        assert page in PAGES or page is None, f"неизвестный экран: {page}"
        assert platforms in PLATFORMS, f"неизвестная площадка: {platforms}"
        if page is None:
            assert target is None, "шаг без экрана не может иметь цель на экране"


def test_the_safe_mode_step_exists_and_is_not_the_last() -> None:
    """Безопасный режим — развилка «складываю в очередь» против
    «откликаюсь за вас», и она единственная действует без подтверждения.
    После неё обязаны идти сохранение и запуск: закончить тур на настройке,
    которую некуда применить, значит не довести до работающего поиска."""
    steps = _steps()
    targets = [target for _page, target, _p in steps]
    assert "#safeModeRow" in targets, "в сценарии нет шага про безопасный режим"
    assert targets.index("#safeModeRow") < len(steps) - 1, (
        "безопасный режим — последний шаг тура"
    )


def test_both_save_buttons_are_in_the_scenario() -> None:
    """Критерии и настройки сохраняются раздельно, кнопки на разных
    экранах. Забыть вторую — самая дешёвая из возможных ошибок, и тур
    обязан показать обе."""
    targets = {target for _page, target, _p in _steps()}
    assert "#btnSaveSettings" in targets, "тур не показывает сохранение настроек"
    assert "#btnSaveCriteria" in targets, "тур не показывает сохранение критериев"


def test_the_tour_reaches_the_start_buttons() -> None:
    """D25: тур доводит до кнопок запуска — иначе он не доводит до поиска."""
    targets = {target for _page, target, _p in _steps()}
    assert "#workerBar" in targets, "тур не доходит до кнопок запуска воркеров"


@skip_without_node
def test_the_platform_filter_drops_the_other_platform() -> None:
    """D23: выбравшему только hh.ru не предлагается регистрировать
    приложение на my.telegram.org."""
    source = _tour_source()
    block = STEPS_BLOCK.search(source)
    assert block, "массив TOUR_STEPS не найден"
    script = "\n".join((
        block.group(0),
        _extract_function_source(source, "stepsFor"),
        """
        function check(name, cond) {
          if (!cond) { console.error('FAIL:', name); process.exitCode = 1; }
        }
        const both = stepsFor(['tg', 'hh']);
        const tg = stepsFor(['tg']);
        const hh = stepsFor(['hh']);
        check('обе площадки дают весь сценарий', both.length === TOUR_STEPS.length);
        check('только tg короче полного', tg.length < both.length);
        check('только hh короче полного', hh.length < both.length);
        check('в tg нет шагов hh', tg.every(s => s.platforms !== 'hh'));
        check('в hh нет шагов tg', hh.every(s => s.platforms !== 'tg'));
        const common = TOUR_STEPS.filter(s => s.platforms === 'all').length;
        for (const [name, list] of [['tg', tg], ['hh', hh], ['обе', both]]) {
          const got = list.filter(s => s.platforms === 'all').length;
          check(name + ': общие шаги на месте и не задвоены', got === common);
        }
        check('безопасный режим есть в любом выборе',
              [tg, hh, both].every(l => l.some(s => s.target === '#safeModeRow')));
        """,
    ))
    result = _run_node(script)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
