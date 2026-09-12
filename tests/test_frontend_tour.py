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
    # Высокая цель — единственное, что заставляет карточку уйти вбок:
    # снизу и сверху места нет. Без этого случая ветку бокового
    # размещения можно было удалить целиком, и прогон оставался зелёным.
    "высокая": {"x": 300, "y": 60, "width": 600, "height": 680},
    # Высокая и сдвинутая вправо: справа карточка уже не помещается, и
    # единственное оставшееся место — слева от цели.
    "высокая справа": {"x": 400, "y": 60, "width": 850, "height": 680},
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

    Чего тест намеренно НЕ закрепляет: какое именно из четырёх мест
    выбрано. Под целью, над ней, справа и слева одинаково удовлетворяют
    всем трём свойствам — в экране, не поверх цели, вплотную к ней, — и
    порядок предпочтений в `cardPosition` остаётся вкусовым. Мутация,
    меняющая его местами, здесь проходит зелёной, и это правильно:
    закреплять надо свойство, а не первую попавшуюся реализацию.
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
            // Вплотную к цели, а не «где-то в экране». Без этого условия
            // ветки бокового размещения можно удалить: запасной угол тоже
            // не накрывает цель, и проверка непересечения его пропускает —
            // оставляя подсказку в противоположном конце экрана от того,
            // что она объясняет.
            const gapX = Math.max(0, hole.x - (box.x + box.width), box.x - (hole.x + hole.width));
            const gapY = Math.max(0, hole.y - (box.y + box.height), box.y - (hole.y + hole.height));
            // Ровно TOUR_GAP, а не «не больше»: нулевой зазор посадил бы
            // карточку на рамку подсветки, которая сама занимает 2px рамки
            // и 3px свечения. Проверка «не больше 12» это пропускала.
            check(name + ': карточка вплотную к цели (gapX=' + gapX + ' gapY=' + gapY + ')',
                  Math.max(gapX, gapY) === 12 && Math.min(gapX, gapY) === 0);
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
    # По тегам, а не по именам файлов где угодно в документе: комментарии
    # к разметке тура называют и tour.js, и app.js, и поиск подстроки
    # начал бы мерить порядок слов в прозе вместо порядка загрузки.
    scripts = re.findall(r'<script\b[^>]*\bsrc="([^"]+)"', source)
    assert scripts, "в index.html нет ни одного подключённого скрипта"
    names = [src.split("/")[-1].split("?")[0] for src in scripts]
    assert "tour.js" in names, f"tour.js не подключён, подключены: {names}"
    assert "app.js" in names, f"app.js не подключён, подключены: {names}"
    assert names.index("tour.js") < names.index("app.js"), (
        f"tour.js подключён после app.js — ACTIONS не увидит его функций: {names}"
    )
    assert "/static/tour.js?v=__ASSET_VERSION__" in scripts, (
        f"адрес tour.js без версии — браузер отдаст его из кэша после правки: {scripts}"
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


# ── Слой затемнения ───────────────────────────────────────────────────

TOUR_LAYER_IDS = (
    "tourLayer", "tourMaskTop", "tourMaskBottom", "tourMaskLeft", "tourMaskRight",
    "tourRing", "tourCard", "tourStepCounter", "tourCardTitle", "tourCardText",
    "tourCardChoices", "tourBtnSkip", "tourBtnBack", "tourBtnNext",
)


def test_the_layer_markup_is_complete() -> None:
    ids = set(ID_ATTR.findall(_index_source()))
    missing = [name for name in TOUR_LAYER_IDS if name not in ids]
    assert not missing, f"в разметке нет узлов слоя обучения: {missing}"


def test_the_dimming_blocks_clicks_and_the_hole_does_not() -> None:
    """D24 целиком держится на трёх правилах `pointer-events`.

    Слой накрывает весь экран, поэтому сам обязан быть прозрачным для
    курсора; клики ловят маски; рамка вокруг дырки — не ловит, иначе она
    съела бы клик по краю подсвеченной кнопки.
    """
    css = (FRONTEND_DIR / "style.css").read_text(encoding="utf-8")

    def rule(selector: str) -> str:
        match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", css)
        assert match, f"в style.css нет правила для {selector}"
        return match.group(1)

    assert "pointer-events: none" in rule("#tourLayer"), (
        "слой обучения ловит клики сам — дырка не будет нажиматься"
    )
    assert "pointer-events: auto" in rule(".tour-mask"), (
        "затемнение пропускает клики — можно уйти на другую страницу из-под тура"
    )
    assert "pointer-events: none" in rule(".tour-ring"), (
        "рамка вокруг дырки ловит клики по краю подсвеченного элемента"
    )
    assert "pointer-events: auto" in rule(".tour-card"), (
        "кнопки карточки не нажимаются"
    )


def test_the_tour_never_builds_markup_from_strings() -> None:
    """Тот же инвариант, что держит app.js: содержимое приходит из
    Telegram и hh.ru, и путь «строка → разметка» не должен существовать
    вовсе, даже там, где сегодня подставляются только свои тексты."""
    source = _tour_source()
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert forbidden not in source, f"tour.js собирает разметку через {forbidden}"
    assert not re.search(r"""\son[a-z]+\s*=\s*["']""", source), (
        "tour.js проставляет атрибут-обработчик — это вернёт 'unsafe-inline' в CSP"
    )


def test_the_tour_does_not_duplicate_page_switching() -> None:
    """Вторая копия логики вкладок разойдётся с первой при первой правке."""
    source = _tour_source()
    assert "switchPage" in source, "тур не переключает страницы"
    assert ".nav-item" not in source, "tour.js сам лезет в навигацию"
    assert "classList.add('active')" not in source, (
        "tour.js сам ставит active — это копия switchPage"
    )


def test_the_tour_opens_the_accordions_it_points_at() -> None:
    """Пять из одиннадцати целей — свёрнутые <details>. У скрытого
    содержимого getBoundingClientRect даёт нули, и подсветка встала бы в
    угол экрана нулевого размера."""
    source = _tour_source()
    assert "details" in source.lower(), "тур не раскрывает аккордеоны"
    assert re.search(r"\.open\s*=\s*true", source), (
        "ни один <details> не раскрывается принудительно"
    )


def test_the_position_is_recomputed_on_scroll_and_resize() -> None:
    """Раскрытие аккордеона и прокрутка сдвигают цель. Без пересчёта
    подсветка остаётся там, где элемент был."""
    source = _tour_source()
    assert "'scroll'" in source, "позиция не пересчитывается при прокрутке"
    assert "'resize'" in source, "позиция не пересчитывается при смене размера окна"
    assert "positionTour" in source


# ── Управление ────────────────────────────────────────────────────────

TOUR_ACTIONS = ("startTour", "tourChoose", "tourNext", "tourBack", "tourSkip")


def test_every_tour_action_is_registered() -> None:
    """Незарегистрированное действие — мёртвая кнопка: runAction берёт
    ACTIONS[name], получает undefined и молча возвращается. Ни ошибки в
    консоли, ни следа."""
    actions = re.search(r"const ACTIONS = \{(.*?)\n\};", _app_source(), re.DOTALL)
    assert actions, "ACTIONS не найдена в app.js"
    registered = set(re.findall(r"^\s*([A-Za-z_$][\w$]*),", actions.group(1), re.MULTILINE))
    missing = [name for name in TOUR_ACTIONS if name not in registered]
    assert not missing, f"действия тура не зарегистрированы в ACTIONS: {missing}"


def test_the_tour_can_be_reopened_from_the_header() -> None:
    """Тур показывается один раз сам; дальше он нужен по требованию."""
    source = _index_source()
    assert 'data-action="startTour"' in source, (
        "тур нельзя открыть повторно — кнопки вызова нет в разметке"
    )


def test_the_tour_is_started_from_init_not_from_its_own_listener() -> None:
    """init() грузит пресеты, критерии и настройки. Тур, запущенный
    раньше, переключил бы вкладку и стал мерить элементы на странице,
    которую в этот момент ещё перерисовывают. Своего DOMContentLoaded у
    тура нет ещё и потому, что tour.js подключён раньше app.js — его
    обработчик сработал бы до init() гарантированно."""
    init = _extract_function_source(_app_source(), "init")
    assert "maybeAutoStartTour" in init, "init() не запускает обучение"
    assert "DOMContentLoaded" not in _tour_source(), (
        "tour.js вешает свой DOMContentLoaded — он сработает до init()"
    )


def test_the_seen_flag_is_written_on_every_exit() -> None:
    """Флаг ставится и на «Пропустить», и на Escape, и на последнем шаге:
    иначе тур возвращался бы при каждой перезагрузке к человеку, который
    его уже закрыл. Поэтому запись живёт в endTour, через который проходят
    все три выхода."""
    source = _tour_source()
    end = _extract_function_source(source, "endTour")
    assert "localStorage.setItem" in end, "endTour не запоминает, что тур показан"
    assert source.count("localStorage.setItem") == 1, (
        "флаг пишется не только в endTour — появился второй выход мимо него"
    )
    assert "try" in end, (
        "обращение к localStorage не защищено: в приватном режиме оно бросает, "
        "и тур упал бы на кнопке «Пропустить»"
    )


def test_enter_is_left_to_the_lists() -> None:
    """Enter уже занят: data-enter-action добавляет значение в список, и
    подсвеченное поле ввода обязано продолжать работать через дырку."""
    source = _tour_source()
    assert "'Enter'" not in source, (
        "тур перехватывает Enter — ввод в подсвеченном поле перестанет добавлять значения"
    )
    assert "'Escape'" in source, "тур не закрывается по Escape"


def test_the_arrows_are_ignored_inside_fields() -> None:
    """В поле ввода стрелки двигают курсор, а не листают тур."""
    source = _tour_source()
    assert "ArrowRight" in source and "ArrowLeft" in source, "стрелки не листают тур"
    assert re.search(r"INPUT|TEXTAREA|tagName", source), (
        "стрелки перехватываются и внутри полей ввода"
    )


@skip_without_node
def test_a_missing_target_is_skipped_in_the_direction_of_travel() -> None:
    """D27 в обе стороны. Если пропуск всегда вёл бы вперёд, «Назад» через
    отсутствующий шаг возвращал бы туда, откуда только что ушли, и выйти
    назад стало бы невозможно.

    Проверяется на подделке DOM: третий шаг «не найден», и обход идёт
    сначала вперёд, потом назад.
    """
    source = _tour_source()
    script = "\n".join((
        "const missing = '#нет-такого';",
        """
        globalThis.window = { addEventListener() {}, innerWidth: 1280, innerHeight: 800 };
        globalThis.document = {
          querySelector: sel => (sel === missing ? null : {
            id: sel,
            scrollIntoView() {},
            getBoundingClientRect: () => ({ left: 10, top: 10, width: 100, height: 40 }),
          }),
          getElementById: () => ({
            style: {}, hidden: false, textContent: '', offsetWidth: 340, offsetHeight: 260,
          }),
        };
        globalThis.switchPage = async () => {};
        globalThis.el = () => ({});
        globalThis.fill = () => {};
        globalThis.localStorage = { setItem() {}, getItem: () => null };
        globalThis.renderTourCard = () => {};
        """,
        "const TOUR_PADDING = 6; const TOUR_GAP = 12;",
        _extract_function_source(source, "clampRect"),
        _extract_function_source(source, "maskRects"),
        _extract_function_source(source, "cardPosition"),
        _extract_function_source(source, "padRect"),
        _extract_function_source(source, "placeRect"),
        _extract_function_source(source, "openAncestorDetails"),
        _extract_function_source(source, "positionTour"),
        _extract_function_source(source, "showStep"),
        _extract_function_source(source, "tourMove"),
        _extract_function_source(source, "endTour"),
        """
        const TOUR_MASK_IDS = { top: 'a', bottom: 'b', left: 'c', right: 'd' };
        const TOUR_SEEN_KEY = 'тест';
        const tourState = {
          steps: [
            { page: null, target: null, platforms: 'all', title: 'a', text: [] },
            { page: null, target: '#есть', platforms: 'all', title: 'b', text: [] },
            { page: null, target: missing, platforms: 'all', title: 'c', text: [] },
            { page: null, target: '#тоже-есть', platforms: 'all', title: 'd', text: [] },
          ],
          index: 0, platforms: ['tg'], active: true, node: null,
        };
        function check(name, cond) {
          if (!cond) { console.error('FAIL:', name, 'index=' + tourState.index); process.exitCode = 1; }
        }
        (async () => {
          await showStep(1, 1);
          check('исходный шаг показан', tourState.index === 1);
          await tourMove(1);
          check('вперёд отсутствующий шаг пропущен', tourState.index === 3);
          await tourMove(-1);
          check('назад отсутствующий шаг пропущен назад, а не вперёд', tourState.index === 1);
        })();
        """,
    ))
    result = _run_node(script)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


@skip_without_node
def test_going_back_from_the_first_step_does_nothing() -> None:
    """Без отсечки отрицательного индекса `tourMove(-1)` на нулевом шаге
    уходит в `steps[-1]`, получает `undefined` и закрывает тур — вместе с
    записью «показан». Стрелка влево на первом же экране выкидывала бы из
    обучения навсегда."""
    source = _tour_source()
    script = "\n".join((
        """
        globalThis.window = { addEventListener() {}, innerWidth: 1280, innerHeight: 800 };
        globalThis.document = {
          querySelector: () => null,
          getElementById: () => ({
            style: {}, hidden: false, textContent: '', offsetWidth: 340, offsetHeight: 260,
          }),
        };
        globalThis.switchPage = async () => {};
        globalThis.el = () => ({});
        globalThis.fill = () => {};
        let written = 0;
        globalThis.localStorage = { setItem() { written += 1; }, getItem: () => null };
        globalThis.renderTourCard = () => {};
        globalThis.positionTour = () => {};
        const TOUR_SEEN_KEY = 'тест';
        """,
        _extract_function_source(source, "showStep"),
        _extract_function_source(source, "tourMove"),
        _extract_function_source(source, "endTour"),
        """
        const tourState = {
          steps: [
            { page: null, target: null, platforms: 'all', title: 'a', text: [] },
            { page: null, target: null, platforms: 'all', title: 'b', text: [] },
          ],
          index: 0, platforms: ['tg'], active: true, node: null,
        };
        function check(name, cond) {
          if (!cond) { console.error('FAIL:', name, 'index=' + tourState.index, 'written=' + written); process.exitCode = 1; }
        }
        (async () => {
          await tourMove(-1);
          check('тур не закрылся', tourState.active === true);
          check('остались на нулевом шаге', tourState.index === 0);
          check('флаг «показан» не записан', written === 0);
        })();
        """,
    ))
    result = _run_node(script)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


def test_hiding_a_tour_node_actually_hides_it() -> None:
    """`node.hidden = true` полагается на правило браузера
    `[hidden] { display: none }`, у которого нулевая специфичность: любое
    собственное правило с `display` его перебивает.

    Найдено в браузере. `.tour-card-choices` объявляет `display: flex`, и
    три кнопки развилки — «Только Telegram», «Только hh.ru», «Обе
    площадки» — оставались на карточке ВСЕХ последующих шагов, хотя
    `renderTourCard` честно выставлял `hidden`. Атрибут стоял, элемент был
    виден; ни один тест этого не показывал, потому что смотрели они на
    атрибут, а не на то, что из него следует.
    """
    css = (FRONTEND_DIR / "style.css").read_text(encoding="utf-8")
    with_display = re.findall(r"(\.tour-[\w-]+|#tourLayer)[^{]*\{[^}]*\bdisplay:", css)
    assert with_display, (
        "ни одно правило тура не задаёт display — проверка стала бы пустой"
    )
    assert re.search(
        r"#tourLayer\s+\[hidden\]\s*\{[^}]*display:\s*none\s*!important", css
    ), (
        "в слое обучения нет правила, которое делает атрибут hidden сильнее "
        f"собственных display-правил; а они есть: {sorted(set(with_display))}"
    )
