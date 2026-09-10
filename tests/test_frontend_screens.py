"""Инварианты четырёх экранов.

Интерфейс нарезан по задаче, а не по типу сущности (решение D20): Telegram
и hh.ru перестают быть половинами каждой страницы и становятся фильтром
внутри неё. Эти проверки держат структуру — чтобы «ещё одна страничка» не
вернула нас к семи экранам с повторами.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from conftest import requires_node

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
INDEX_HTML = FRONTEND_DIR / "index.html"
APP_JS = FRONTEND_DIR / "app.js"

SCREENS = ("overview", "found", "sent", "settings")

skip_without_node = requires_node


def _index() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _app() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _extract_function_source(name: str) -> str:
    """Та же конвенция, что в test_frontend_safety.py: функции верхнего
    уровня в app.js закрываются `}` в нулевой колонке."""
    pattern = rf"(?:async )?function {re.escape(name)}\(.*?\) \{{.*?\n\}}"
    match = re.search(pattern, _app(), re.DOTALL)
    assert match, f"{name}() не найдена в app.js"
    return match.group(0)


def _nav_pages() -> list[str]:
    return re.findall(r"""class="nav-item[^"]*"\s+data-page="([^"]+)\"""", _index())


def _page_ids() -> set[str]:
    return set(re.findall(r"""class="page[^"]*"\s+id="page-([^"]+)\"""", _index()))


def test_navigation_has_exactly_the_four_screens() -> None:
    assert _nav_pages() == list(SCREENS), (
        "интерфейс нарезается по задаче: Обзор, Найдено, Отправлено, Настройки"
    )


def test_every_nav_item_has_a_page_and_every_page_has_a_nav_item() -> None:
    """Пункт без экрана — мёртвая кнопка; экран без пункта — недостижимая
    страница. Оба раньше ловились только глазами."""
    assert _page_ids() == set(SCREENS)


def test_the_status_bar_is_outside_every_page() -> None:
    """Полоса состояния видна на любом экране. Внутри `.page` она была бы
    видна на одном — и продублирована на остальных."""
    index = _index()
    bar = index.index('id="statusBar"')
    first_page = index.index('class="page ')
    assert bar < first_page, "полоса состояния должна стоять до первого экрана"


def test_the_status_bar_names_safe_mode_in_words() -> None:
    """Смысл безопасного режима не написан нигде: только галочка, до которой
    надо дойти и вспомнить, что она значит."""
    assert "собираю, не отправляю" in _app()


def test_the_status_bar_text_comes_from_js_not_markup() -> None:
    """Иначе при первом открытии страница секунду врёт: показывает
    «остановлен» для работающего воркера."""
    index = _index()
    bar = index[index.index('id="statusBar"'):index.index('class="page ')]
    for lie in ("остановлен", "работает", "Безопасный режим:"):
        assert lie not in bar, f"полоса состояния содержит зашитый текст {lie!r}"


def test_every_screen_has_a_loader_entry() -> None:
    """`PAGE_LOADERS` — единственное место, где решается, что подгрузить при
    открытии экрана. Забытая запись даёт пустой экран без ошибки."""
    match = re.search(r"const PAGE_LOADERS = \{(.*?)\n\};", _app(), re.DOTALL)
    assert match, "PAGE_LOADERS не найдена в app.js"
    for screen in SCREENS:
        assert re.search(rf"\b{screen}\s*:", match.group(1)), (
            f"экран {screen!r} не назван в PAGE_LOADERS"
        )


def test_the_old_seven_screens_are_gone() -> None:
    """Страховка от вакуумности: без неё проверки выше прошли бы и в том
    случае, если бы старые страницы остались рядом с новыми."""
    for gone in ("page-dashboard", "page-chats", "page-channels",
                 "page-keywords", "page-template", "page-logs"):
        assert f'id="{gone}"' not in _index(), f"{gone} остался в разметке"


@skip_without_node
def test_worker_state_is_rendered_as_a_russian_word() -> None:
    """Полоса состояния говорит словами, а не кодами состояний бэкенда."""
    source = _extract_function_source("workerWord")
    table = re.search(r"const WORKER_STATE_WORDS = \{.*?\n\};", _app(), re.DOTALL)
    assert table, "WORKER_STATE_WORDS не найдена"
    cases = {
        "running": "работает",
        "stopped": "остановлен",
        "starting": "запускается",
        "stopping": "останавливается",
        "error": "ошибка",
    }
    checks = "\n".join(
        f'if (workerWord({state!r}) !== {word!r}) {{ console.error({state!r}); '
        "process.exitCode = 1; }"
        for state, word in cases.items()
    ).replace("'", '"')
    script = f"{table.group(0)}\n{source}\n{checks}"
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


# ── Ничего не живёт вне экранов ───────────────────────────────────────


def _main_block() -> str:
    """Содержимое `<div class="main">` с вырезанными комментариями.

    Комментарии вырезаются заменой на пустые строки, а не удалением, чтобы
    номера строк в сообщении об ошибке оставались настоящими.
    """
    index = _index()
    start = index.index('<div class="main">')
    body = index[start:index.index("\n</div>", start)]
    return re.sub(
        r"<!--.*?-->",
        lambda m: "\n" * m.group(0).count("\n"),
        body,
        flags=re.DOTALL,
    )


def _container_span(lines: list[str], marker: str) -> tuple[int, int]:
    """Границы контейнера, чья открывающая строка содержит `marker`."""
    for start, line in enumerate(lines):
        if marker in line:
            break
    else:
        raise AssertionError(f"контейнер {marker!r} не найден")
    depth = 0
    for end in range(start, len(lines)):
        depth += lines[end].count("<div") - lines[end].count("</div>")
        if depth == 0:
            return start, end
    raise AssertionError(f"контейнер {marker!r} не закрыт")


def _page_spans(text: str) -> list[tuple[str, int, int]]:
    """Границы каждого контейнера `.page` по балансу `div`."""
    lines = text.splitlines()
    spans = []
    for start, line in enumerate(lines):
        if not re.search(r'class="page[ "]', line):
            continue
        page_id = re.search(r'id="([^"]+)"', line).group(1)
        depth = 0
        for end in range(start, len(lines)):
            depth += lines[end].count("<div") - lines[end].count("</div>")
            if depth == 0:
                spans.append((page_id, start, end))
                break
        else:
            raise AssertionError(f"контейнер {page_id} не закрыт")
    return spans


def test_no_card_lives_outside_a_screen() -> None:
    """Настоящий дефект, найденный на живом приложении: у карточки «Тип
    занятости HH» стоял лишний `</div>`, который досрочно закрывал
    `#page-settings`. Всё, что шло дальше — «ID резюме на HH», «Вход в
    hh.ru», редактор сценария Selenium, — оказывалось ВНЕ любого экрана и
    поэтому рисовалось на всех четырёх сразу.

    Это и была та самая «блок с hh появляется практически на каждой
    странице»: не только дублирование в разметке, а буквально лишний
    закрывающий тег. Балансом скобок такое не ловится — каждый `<div>`
    закрыт, просто не там, — поэтому проверяется свойство: внутри
    `<div class="main">` всё содержимое лежит либо в контейнере экрана,
    либо в полосе состояния.
    """
    main = _main_block()
    lines = main.splitlines()

    covered = set()
    for _page_id, start, end in _page_spans(main):
        covered.update(range(start, end + 1))
    bar_start, bar_end = _container_span(lines, 'id="statusBar"')
    covered.update(range(bar_start, bar_end + 1))
    covered.add(0)  # сама строка `<div class="main">`

    stray = [
        (number + 1, line.strip())
        for number, line in enumerate(lines)
        if number not in covered and line.strip()
    ]
    assert not stray, (
        "разметка вне контейнера экрана — она будет видна на всех экранах "
        f"сразу: {stray[:5]}"
    )


def test_the_stray_markup_check_is_not_vacuous() -> None:
    """Проверка обязана срабатывать: если бы `_page_spans` возвращал пустой
    список или охват считался неверно, тест выше проходил бы всегда."""
    main = _main_block()
    spans = _page_spans(main)
    assert len(spans) == len(SCREENS)
    for page_id, start, end in spans:
        assert end > start, f"контейнер {page_id} схлопнулся в одну строку"
    # Полоса состояния существует и НЕ совпадает ни с одним экраном.
    bar = _container_span(main.splitlines(), 'id="statusBar"')
    assert all(bar != (start, end) for _pid, start, end in spans)
