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

import pytest
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


# ── Экран «Найдено» ───────────────────────────────────────────────────


def test_the_found_screen_has_both_sources_and_the_status_filter() -> None:
    index = _index()
    found = index[index.index('id="page-found"'):index.index('id="page-sent"')]
    for arg in ('data-arg="tg"', 'data-arg="hh"'):
        assert arg in found, f"на экране «Найдено» нет переключателя {arg}"
    for arg in ('data-arg="all"', 'data-arg="new"', 'data-arg="decided"'):
        assert arg in found, f"на экране «Найдено» нет фильтра {arg}"


def test_the_found_screen_reads_the_new_endpoints() -> None:
    source = _extract_function_source("loadFound")
    assert "/found/" in source
    assert "/hh/vacancies" not in _app()


def test_the_row_never_builds_its_own_telegram_link() -> None:
    """Ссылку строит бэкенд: фронтенду незачем знать формат чужих URL, а
    гвард схемы в `el()` остаётся единственной точкой проверки."""
    assert "t.me/" not in _app(), (
        "фронтенд собирает ссылку на Telegram сам — это должен делать "
        "api/found_routes.py, там же где и все остальные ссылки"
    )


@skip_without_node
def test_the_status_badge_class_is_a_table_not_a_substring_game() -> None:
    """Раньше класс бейджа выбирался по вхождению подстроки, и «откликнулся
    сам» не совпадал ни с чем — ручной отклик выглядел как ожидание.
    Таблица по точным значениям из job_monitor/statuses.py исключает это
    по построению."""
    table = re.search(r"const STATUS_CLASS = \{.*?\n\};", _app(), re.DOTALL)
    assert table, "STATUS_CLASS не найдена в app.js"
    source = _extract_function_source("vacancyStatusClass")
    cases = {
        "новая": "status-wait",
        "отклик отправлен": "status-sent",
        "откликнулся сам": "status-sent",
        "не подходит": "status-skip",
        "пропущено": "status-skip",
        "ошибка сценария": "status-error",
        "": "status-wait",
    }
    checks = "\n".join(
        f"if (vacancyStatusClass({status!r}) !== {css!r}) "
        f"{{ console.error({status!r}); process.exitCode = 1; }}"
        for status, css in cases.items()
    ).replace("'", '"')
    script = f"{table.group(0)}\n{source}\n{checks}"
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_the_status_vocabulary_matches_the_backend() -> None:
    """Два словаря статусов, которые разошлись, — это бейдж «ожидание» на
    отправленном отклике. Проверяем, что фронтенд знает ровно те строки,
    которые пишет бэкенд."""
    from job_monitor import statuses

    table = re.search(r"const STATUS_CLASS = \{(.*?)\n\};", _app(), re.DOTALL)
    assert table
    known = set(re.findall(r"['\"]([^'\"]+)['\"]\s*:", table.group(1)))
    assert known == set(statuses.ALL), (
        f"фронтенд знает {sorted(known)}, бэкенд пишет {sorted(statuses.ALL)}"
    )


def test_every_found_action_has_a_handler() -> None:
    """Та же связь, что держит `tests/test_frontend_events.py`, но для
    кнопок, которые создаёт сам JS: строки очереди не существуют в
    index.html и статическим сканом разметки не видны."""
    actions = set(re.findall(r"""['"]data-action['"]\s*:\s*['"]([^'"]+)['"]""", _app()))
    for name in ("foundApply", "foundDismiss", "foundReopen"):
        assert name in actions, f"кнопка {name} нигде не создаётся"


def test_the_found_row_never_offers_a_button_the_backend_would_refuse() -> None:
    """Кнопка «Вернуть в очередь» на отправленном отклике вела бы прямо в
    400: бэкенд отвергает такой переход, потому что «новая» означает
    «обработать», то есть второй отклик тому же работодателю. Предлагать
    нажать то, что будет отвергнуто, — это обещание, которого интерфейс не
    может сдержать."""
    from job_monitor import statuses

    source = _extract_function_source("foundButtons")
    offered = set(re.findall(r"row\.status === '([^']+)'", source))
    assert offered <= statuses.REOPENABLE | {statuses.NEW}, (
        f"строка предлагает действие в статусе {sorted(offered - statuses.REOPENABLE - {statuses.NEW})}, "
        "который бэкенд не примет"
    )
    assert statuses.REOPENABLE <= offered, (
        "из «не подходит» и «пропущено» вернуть в очередь МОЖНО — кнопка "
        "должна предлагаться"
    )


@skip_without_node
def test_a_rejected_status_change_is_not_reported_as_success() -> None:
    """Спецификация, раздел 10: отвергнутое сохранение нигде не
    показывается как успешное.

    Бэкенд отвергает часть переходов с 400 — например, возврат в очередь
    уже отправленного отклика. Если очередь просто перерисуется, человек
    увидит прежний статус и решит, что промахнулся мимо кнопки. Здесь
    исполняется настоящая `patchFound` с подменёнными `apiPatch`,
    `showToast` и `loadFound`.
    """
    script = "\n".join((
        "const __toasts = [];",
        "let __reloaded = 0;",
        "function showToast(message) { __toasts.push(message); }",
        "async function loadFound() { __reloaded += 1; }",
        "const foundState = { source: 'hh' };",
        _extract_function_source("configErrorDetail"),
        "let __reply = null;",
        "async function apiPatch() { return __reply; }",
        _extract_function_source("patchFound"),
        """
        (async () => {
          __reply = { detail: 'отклик уже отправлен — вернуть запись в очередь нельзя' };
          await patchFound('1', 'новая');
          if (__reloaded !== 0) {
            console.error('FAIL: отвергнутая правка перерисовала список как успешную');
            process.exitCode = 1;
          }
          if (!__toasts.some(t => t.includes('отклик уже отправлен'))) {
            console.error('FAIL: причина отказа не показана пользователю', __toasts);
            process.exitCode = 1;
          }

          __reply = { status: 'saved' };
          await patchFound('1', 'не подходит');
          if (__reloaded !== 1) {
            console.error('FAIL: успешная правка не обновила список');
            process.exitCode = 1;
          }
          if (__toasts.length !== 1) {
            console.error('FAIL: успех показан как ошибка', __toasts);
            process.exitCode = 1;
          }
        })();
        """,
    ))
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


# ── Экран «Обзор» ─────────────────────────────────────────────────────


def test_no_metric_claims_a_value_before_the_data_arrives() -> None:
    """Замечено на живом приложении: подпись «из 0 в день» стояла прямо в
    разметке и долю секунды после открытия утверждала, что суточный лимит
    равен нулю.

    Ровно то же было на кнопках воркеров — там текст убрали, потому что
    зашитая подпись противоречила первому же ответу сервера. Число в
    плитке («0» находок) не в счёт: ноль — правдоподобное значение, а не
    заявление о настройке, которой человек не делал.
    """
    overview = _overview()
    for lie in ("из 0 в день", "из 25 в день", "из 20 в день"):
        assert lie not in overview, (
            f"разметка утверждает {lie!r} до того, как пришёл ответ сервера"
        )


def test_the_metrics_are_three_tiles_not_six() -> None:
    """Шесть плиток с тремя повторяющимися подписями — это и есть
    «загруженность экрана разными блоками», от которой уходим."""
    index = _index()
    overview = index[index.index('id="page-overview"'):index.index('id="page-found"')]
    assert overview.count('class="metric"') == 3, (
        "на «Обзоре» должно быть ровно три плитки метрик с переключателем "
        "источника, а не по тройке на источник"
    )


def test_the_duplicated_metric_ids_are_gone() -> None:
    for gone in ("tgSentToday", "tgFoundToday", "tgSentTotal", "tgMetricBar",
                 "hhSentToday", "hhFoundToday", "hhTotalSent", "hhMetricBar"):
        assert f'id="{gone}"' not in _index(), f"{gone} остался в разметке"
        assert f"'{gone}'" not in _app(), f"{gone} остался в app.js"


def test_the_dashboard_no_longer_duplicates_the_found_screen() -> None:
    """Блок «последние вакансии» на дашборде был предшественником экрана
    «Найдено». Оставить оба — значит держать два списка одного и того же."""
    assert 'id="hhRecentVacancies"' not in _index()
    assert "loadHHVacancies" not in _app()


@skip_without_node
def test_the_combined_source_sums_both_workers() -> None:
    """«Все» — это сумма, а не Telegram по умолчанию. Ошибка тут не видна
    глазом: цифра выглядит правдоподобной ровно до того дня, когда второй
    воркер что-то нашёл."""
    source = _extract_function_source("metricValues")
    script = source + """
const tg = { found: 3, sent: 2, total: 10, limit: 25 };
const hh = { found: 4, sent: 1, total: 20, limit: 20 };
const cases = [
  ['tg',  { found: 3, sent: 2, total: 10, limit: 25 }],
  ['hh',  { found: 4, sent: 1, total: 20, limit: 20 }],
  ['all', { found: 7, sent: 3, total: 30, limit: 45 }],
];
for (const [source, expected] of cases) {
  const actual = metricValues(source, tg, hh);
  for (const key of Object.keys(expected)) {
    if (actual[key] !== expected[key]) {
      console.error(source, key, actual[key], '!=', expected[key]);
      process.exitCode = 1;
    }
  }
}
"""
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


# ── Критерии живут рядом с пресетом ───────────────────────────────────

CRITERIA_FIELDS = (
    "newChannel", "channelEditList",       # каналы Telegram
    "kwList", "exList",                    # ключевые слова и стоп-слова TG
    "hhKwList", "hhExList",                # профессии и стоп-слова hh.ru
    "hhExperience", "hhSalaryFrom", "hhSearchPeriod",
    "hhScheduleBox", "hhEmploymentBox", "hhResumeId",
    "templateText", "hhCoverLetter", "criteriaResume",
)


def _overview() -> str:
    index = _index()
    return index[index.index('id="page-overview"'):index.index('id="page-found"')]


def _settings() -> str:
    index = _index()
    return index[index.index('id="page-settings"'):]


def test_every_criteria_field_lives_on_the_overview() -> None:
    """«Что искать» принадлежит пресету и показывается вместе с ним; в
    «Настройках» остаётся только то, что общее для всех пресетов."""
    overview = _overview()
    for field in CRITERIA_FIELDS:
        assert f'id="{field}"' in overview, f"поле критериев {field} не на «Обзоре»"


def test_no_criteria_field_is_left_in_the_settings() -> None:
    settings = _settings()
    for field in CRITERIA_FIELDS:
        assert f'id="{field}"' not in settings, (
            f"{field} остался в «Настройках» — он принадлежит пресету"
        )


#: Какие поля критериев чьи. Общих нет ни одного — именно поэтому
#: группировка по платформам здесь и уместна: «где искать / что искать /
#: чем отвечать» перемешивала Telegram и hh.ru внутри каждой карточки, и
#: рядом оказывались два списка стоп-слов, отличавшиеся одним словом в
#: заголовке.
TG_FIELDS = ("channelEditList", "newChannel", "kwList", "newKw",
             "exList", "newEx", "templateText", "criteriaResume")
HH_FIELDS = ("hhKwList", "newHHKw", "hhExList", "newHHEx", "hhExperience",
             "hhSalaryFrom", "hhSearchPeriod", "hhScheduleBox",
             "hhEmploymentBox", "hhCoverLetter", "hhResumeId")


def _card(card_id: str) -> str:
    """Разметка одной карточки — по балансу `div` от её открывающего тега.

    Отсчёт идёт от НАЧАЛА строки с идентификатором, а не от самого
    идентификатора: иначе открывающий `<div` остаётся за срезом, баланс
    сходится сразу же, и помощник возвращает одну обрезанную строку —
    проверка тогда «не находит» ни одного поля и выглядит как поломка
    разметки.
    """
    lines = _overview().splitlines()
    start = next(i for i, line in enumerate(lines) if f'id="{card_id}"' in line)
    depth = 0
    for end in range(start, len(lines)):
        depth += lines[end].count("<div") - lines[end].count("</div>")
        if depth == 0:
            return "\n".join(lines[start:end + 1])
    raise AssertionError(f"карточка {card_id} не закрыта")


def test_the_criteria_are_grouped_by_platform() -> None:
    overview = _overview()
    for group in ("criteriaTg", "criteriaHh"):
        assert f'id="{group}"' in overview, f"нет карточки {group}"


@pytest.mark.parametrize(
    "card_id, свои, чужие",
    [("criteriaTg", TG_FIELDS, HH_FIELDS), ("criteriaHh", HH_FIELDS, TG_FIELDS)],
)
def test_a_platform_card_holds_only_its_own_fields(card_id, свои, чужие) -> None:
    """Смысл группировки именно в этом: карточка платформы не содержит
    чужих полей. Иначе разделение — только в заголовках."""
    разметка = _card(card_id)
    отсутствуют = [f for f in свои if f'id="{f}"' not in разметка]
    assert not отсутствуют, f"{card_id} не содержит своих полей: {отсутствуют}"
    посторонние = [f for f in чужие if f'id="{f}"' in разметка]
    assert not посторонние, f"в {card_id} попали поля другой платформы: {посторонние}"


def test_every_criteria_field_belongs_to_exactly_one_platform() -> None:
    """Страховка от вырождения: списки выше должны покрывать все поля
    критериев и не пересекаться. Иначе проверка молча перестанет что-либо
    доказывать — поле, забытое в обоих списках, не заметит никто."""
    assert set(TG_FIELDS) & set(HH_FIELDS) == set()
    покрыты = set(TG_FIELDS) | set(HH_FIELDS)
    непокрытые = [f for f in CRITERIA_FIELDS if f not in покрыты]
    assert not непокрытые, (
        f"поля критериев не отнесены ни к одной платформе: {непокрытые}"
    )


def test_there_is_one_save_button_for_the_criteria() -> None:
    """Пять кнопок сохранения — это пять частичных сохранений. Одна кнопка
    и один PATCH: с задачи 7 он применяется целиком или никак."""
    overview = _overview()
    assert overview.count('data-action="saveCriteria"') == 1
    for gone in ("saveChannels", "saveKeywords", "saveTemplate",
                 "saveHHCoverLetter", "chooseResume"):
        assert gone not in _index(), f"кнопка {gone} осталась в разметке"
        assert gone not in _app(), f"обработчик {gone} остался в app.js"


def _without_comments(source: str) -> str:
    """Исходник без `//`-комментариев.

    Те же соображения, что у `_app_code_without_comments` в
    tests/test_frontend_events.py: причина, по которой что-то сделано
    именно так, часто называет то самое имя, которое проверка считает.
    """
    return "\n".join(
        re.sub(r"(^|\s)//.*$", "", line) for line in source.splitlines()
    )


def test_the_criteria_save_goes_through_one_patch() -> None:
    """Патч уходит одним запросом на пресет, а не полем за полем: иначе
    отказ на пятом поле оставил бы четыре сохранёнными."""
    source = _without_comments(_extract_function_source("saveCriteria"))
    assert source.count("patchCriteria") == 1


@skip_without_node
def test_collect_criteria_sends_numbers_as_numbers() -> None:
    """Пустое числовое поле сериализуется в null и отвергается с 422 —
    это уже ловили в подпроекте 1. Но у критерия «пусто» имеет смысл:
    «зарплата от» без числа значит «не важно», то есть ноль. Это не то же
    самое, что лимит отправок, где выбрать значение за человека нельзя."""
    source = _extract_function_source("collectCriteria")
    script = """
const fields = {
  hhSalaryFrom: '', hhSearchPeriod: '3', hhResumeId: ' 12345 ',
  templateText: 'привет', hhCoverLetter: '', hhExperience: 'between1And3',
  criteriaResume: '',
};
global.document = {
  getElementById: id => (id in fields ? { value: fields[id] } : null),
};
""" + source + """
const patch = collectCriteria({
  channels: ['qajobs'], tg_keywords: ['qa'], tg_exclude: [],
  professions: ['QA'], hh_exclude: [], hh_schedule: [], hh_employment: [],
});
function check(name, actual, expected) {
  if (actual !== expected) {
    console.error(name, actual, '!=', expected);
    process.exitCode = 1;
  }
}
check('hh_salary_from', patch.hh_salary_from, 0);
check('hh_search_period', patch.hh_search_period, 3);
check('hh_resume_id', patch.hh_resume_id, '12345');
check('resume_id', patch.resume_id, null);
check('channels', patch.channels.join(), 'qajobs');

// Второй заход: пусто ВЕЗДЕ. Период обязан стать единицей, а не нулём:
// ноль не проходит проверку `ge=1`, и сохранение упало бы с 422 на поле,
// которого человек не трогал.
fields.hhSearchPeriod = '';
const empty = collectCriteria({});
check('пустой период', empty.hh_search_period, 1);
check('пустая зарплата', empty.hh_salary_from, 0);
"""
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


# ── Экран «Отправлено» ────────────────────────────────────────────────


def _sent() -> str:
    index = _index()
    return index[index.index('id="page-sent"'):index.index('id="page-settings"')]


def test_the_sent_screen_switches_between_sources() -> None:
    sent = _sent()
    assert 'data-action="sentSource"' in sent
    assert 'id="sentTg"' in sent
    assert 'id="sentHh"' in sent


def test_the_sent_screen_asks_the_server_what_counts_as_sent() -> None:
    """Своего словаря статусов у фронтенда быть не должно.

    Раньше экран просил `status=decided` и отсеивал неотправленное сам,
    держа копию списка «что считать откликом». Две беды сразу: копия
    однажды отстаёт от бэкенда, а отбор на клиенте поверх страницы в 50
    строк давал «откликов пока нет» при полной таблице откликов —
    достаточно было отбросить за день шестьдесят вакансий.
    """
    source = _without_comments(_extract_function_source("loadSentHh"))
    assert "status=applied" in source
    assert "APPLIED_STATUSES" not in _app(), (
        "копия списка статусов вернулась на фронтенд — словарь знает "
        "бэкенд, и знать его должен он один"
    )
    assert ".filter(" not in source, (
        "отбор снова делается на клиенте поверх ограниченной страницы"
    )


def test_the_server_filter_the_screen_asks_for_actually_exists() -> None:
    """Обратная сторона: `status=applied` должен быть настоящим значением
    фильтра, а не опечаткой, которую роут отвергнет с 422."""
    from api.found_routes import STATUS_FILTERS
    from job_monitor import statuses

    assert STATUS_FILTERS["applied"] == statuses.APPLIED


# ── Экран «Настройки» ─────────────────────────────────────────────────


#: Настройки каждой платформы. В отличие от критериев, здесь есть и
#: по-настоящему общее — оно в отдельной карточке, а не приписано одной из
#: платформ и не продублировано в обеих.
TG_SETTINGS = ("maxPerDay", "historyLimit", "toggleHistory", "toggleTGAutostart",
               "apiId", "apiHash", "authPhone", "authCode")
HH_SETTINGS = ("hhMaxPerDayInput", "hhCheckInterval", "toggleHHAutostart",
               "hhLoginStatus", "hhLoginButtons", "seleniumStepsList", "newStepType")


def _settings_card(card_id: str) -> str:
    lines = _settings().splitlines()
    start = next(i for i, line in enumerate(lines) if f'id="{card_id}"' in line)
    depth = 0
    for end in range(start, len(lines)):
        depth += lines[end].count("<div") - lines[end].count("</div>")
        if depth == 0:
            return "\n".join(lines[start:end + 1])
    raise AssertionError(f"карточка {card_id} не закрыта")


def test_the_settings_are_grouped_by_platform() -> None:
    settings = _settings()
    for group in ("settingsCommon", "settingsTg", "settingsHh",
                  "settingsResumes", "settingsLog"):
        assert f'id="{group}"' in settings, f"группа {group} не найдена"


@pytest.mark.parametrize(
    "card_id, свои, чужие",
    [("settingsTg", TG_SETTINGS, HH_SETTINGS), ("settingsHh", HH_SETTINGS, TG_SETTINGS)],
)
def test_a_platform_settings_card_holds_only_its_own(card_id, свои, чужие) -> None:
    разметка = _settings_card(card_id)
    отсутствуют = [f for f in свои if f'id="{f}"' not in разметка]
    assert not отсутствуют, f"{card_id} не содержит своих настроек: {отсутствуют}"
    посторонние = [f for f in чужие if f'id="{f}"' in разметка]
    assert not посторонние, f"в {card_id} попали настройки другой платформы: {посторонние}"


def test_the_safe_mode_switch_is_not_duplicated_per_platform() -> None:
    """Решение D17: безопасный режим — ОДИН переключатель на оба воркера.

    Разнести его по платформам значило бы завести два контрола на одну
    настройку — ровно та беда, ради которой две кнопки сохранения были
    слиты в одну: человек меняет один, второй показывает прежнее, и какой
    из них правда, неизвестно. Приписать одной платформе — соврать:
    режим действует на обе.
    """
    settings = _settings()
    assert settings.count('id="toggleSafe"') == 1, "переключатель размножился"
    assert 'id="toggleSafe"' in _settings_card("settingsCommon"), (
        "безопасный режим приписан одной платформе, хотя действует на обе"
    )


def test_the_settings_have_one_save_button() -> None:
    settings = _settings()
    assert settings.count('data-action="saveGlobalSettings"') == 1
    for gone in ("saveTGSettings", "saveHHSettings"):
        assert gone not in _index(), f"кнопка {gone} осталась"
        assert gone not in _app(), f"обработчик {gone} остался"


#: Внутренние артефакты, именами которых нельзя подписывать элементы
#: интерфейса. `session_web` — файл сессии, которого в приложении давно
#: нет, и карточка входа была названа его именем: подпись обещала то, чего
#: не существует. Остальные существуют, но человеку не адресованы.
INTERNAL_ARTEFACTS = ("session_web", "telegram.session", "hh_cookies.json",
                      "job_monitor.db", "stored_name")


def test_no_control_is_named_after_an_internal_file() -> None:
    """Обобщение прежней проверки. Раньше она держалась на буквальной
    строке «Вход в Telegram» и упала, как только карточка стала называться
    «Telegram», а подпись внутри — «Вход в аккаунт»: повторять платформу в
    подписи после группировки незачем. Свойство при этом осталось
    прежним — подпись не называет внутренний файл, — и теперь оно
    проверяется прямо, а не через одну конкретную формулировку.
    """
    index = _index()
    найдены = [name for name in INTERNAL_ARTEFACTS if name in index]
    assert not найдены, f"интерфейс называет внутренние файлы: {найдены}"


def test_each_platform_card_offers_a_login() -> None:
    """Вход — половина того, ради чего в настройки заходят вообще. После
    перегруппировки он должен быть у каждой платформы свой, а не потерян
    при переносе."""
    assert 'id="authStatus"' in _settings_card("settingsTg")
    assert 'id="hhLoginStatus"' in _settings_card("settingsHh")


def test_no_tg_hh_dividers_are_left() -> None:
    """Шесть разделителей «TG / HH» были следствием нарезки по источнику.
    Источник теперь переключатель, делить пополам нечего."""
    assert "section-split" not in _index()


def test_the_safe_mode_label_covers_both_workers() -> None:
    """Решение D17: один переключатель на оба воркера. Подпись, говорящая
    только про Telegram, была бы прямой неправдой — hh.ru теперь тоже его
    слушает."""
    index = _index()
    safe = index[index.index('id="toggleSafe"') - 900:index.index('id="toggleSafe"')]
    assert "hh.ru" in safe, (
        "подпись безопасного режима не говорит, что он действует и на hh.ru"
    )


@skip_without_node
def test_an_emptied_limit_is_not_silently_replaced_with_a_default() -> None:
    """Пин дефекта I4 в новой форме. У критерия «пусто» имеет смысл
    («зарплата от» без числа = не важно), у лимита отправок — нет: он
    существует, чтобы не забанили аккаунт, и выбрать его за человека
    молча значит соврать ему о том, сколько сообщений уйдёт сегодня.
    Пустое поле обязано уехать как null и получить 422 с объяснением."""
    source = _extract_function_source("saveGlobalSettings")
    script = """
const fields = {
  toggleSafe: { checked: true }, toggleHistory: { checked: false },
  toggleTGAutostart: { checked: false }, toggleHHAutostart: { checked: false },
  maxPerDay: { value: '' }, historyLimit: { value: '50' },
  hhMaxPerDayInput: { value: '20' }, hhCheckInterval: { value: '30' },
};
global.document = { getElementById: id => fields[id] || null };
const hhState = { seleniumSteps: [], running: false, maxPerDay: 20 };
const tgState = { safeMode: true, running: false, maxPerDay: 25 };
let __sent = null;
async function apiPatch(path, body) { __sent = body; return { status: 'saved' }; }
function configPatchOk(r) { return !!r && r.status === 'saved'; }
function configErrorDetail() { return 'ошибка'; }
function showToast() {}
function updateStatusBar() {}
function updateMetrics() {}
function showRestartBanner() {}
""" + source + """
(async () => {
  await saveGlobalSettings();
  if (__sent.max_per_day !== null) {
    console.error('очищенный лимит подменён значением', __sent.max_per_day);
    process.exitCode = 1;
  }
  if (__sent.history_limit !== 50) {
    console.error('заполненное поле не доехало', __sent.history_limit);
    process.exitCode = 1;
  }
})();
"""
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


# ── Переключение пресета ──────────────────────────────────────────────


def test_switching_a_preset_reloads_its_criteria() -> None:
    """Найдено ревью, и это самый дорогой дефект переработки интерфейса.

    Критерии переехали из `loadSettings()` в `loadActiveCriteria()`, а
    `activatePreset()` продолжал звать `loadSettings()`. Чипы пресетов
    живут НА «Обзоре», так что перехода между экранами не случается и
    перезагрузить поля некому: после переключения на пресете Б в форме
    остаются значения пресета А. Нажатие «Сохранить критерии» записывает
    их поверх Б — конфигурация пресета уничтожена без единого
    предупреждения.
    """
    source = _without_comments(_extract_function_source("activatePreset"))
    assert "loadActiveCriteria" in source, (
        "переключение пресета не перечитывает его критерии — форма покажет "
        "чужие значения, а сохранение их запишет"
    )


@skip_without_node
def test_the_overview_does_not_discard_unsaved_edits_on_every_visit() -> None:
    """`PAGE_LOADERS.overview` перечитывал критерии при каждом клике по
    пункту навигации. Человек, набравший длинное сопроводительное, уходил
    на «Найдено» посмотреть вакансию, возвращался — и текста нет.

    Проверяется поведение, а не наличие имени в тексте: первая версия
    этой проверки искала подстроку `loadedId`, и снятие самого условия её
    не роняло — присваивание-то оставалось.
    """
    script = "\n".join((
        "const fields = {};",
        "global.document = { getElementById: id => (fields[id] ||= { value: '' }) };",
        "const presetState = { list: [], activeId: 7, loadedId: null, criteria: {} };",
        "const tgState = {}; const hhState = {};",
        "let fetched = 0;",
        "async function apiGet() { fetched += 1; return { criteria: {} }; }",
        "function fill() {} function el() { return {}; }",
        "function renderChannelEdit() {} function renderKeywords() {}",
        "function renderHHKeywords() {} function renderChoiceBox() {}",
        "async function loadDictionaries() { return null; }",
        "async function loadResumes() {}",
        "function updateFieldCounts() {}",
        _extract_function_source("loadActiveCriteria"),
        """
        (async () => {
          await loadActiveCriteria();
          if (fetched !== 1) { console.error('первый заход не загрузил критерии'); process.exitCode = 1; }

          // Человек печатает сопроводительное и уходит на другой экран.
          fields.hhCoverLetter.value = 'черновик, который нельзя терять';
          await loadActiveCriteria();
          if (fetched !== 1) { console.error('повторный заход полез в сеть'); process.exitCode = 1; }
          if (fields.hhCoverLetter.value !== 'черновик, который нельзя терять') {
            console.error('несохранённая правка затёрта:', fields.hhCoverLetter.value);
            process.exitCode = 1;
          }

          // Переключение пресета обязано перечитать всё, даже поверх правки.
          // Именно так его и видит `activatePreset`: `loadPresets()`
          // обновляет `activeId`, и только потом зовётся эта функция.
          presetState.activeId = 9;
          await loadActiveCriteria();
          if (fetched !== 2) { console.error('переключение пресета не перечитало критерии'); process.exitCode = 1; }
          if (fields.hhCoverLetter.value !== '') {
            console.error('поля нового пресета не заполнены:', fields.hhCoverLetter.value);
            process.exitCode = 1;
          }
        })();
        """,
    ))
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_the_sent_screen_remembers_which_source_is_open() -> None:
    """Загрузчик всегда обновлял панель Telegram. Выбрав hh.ru, уйдя и
    вернувшись, человек видел ту же панель hh.ru со СТАРЫМ содержимым,
    пока обновлялась скрытая соседняя."""
    match = re.search(r"const PAGE_LOADERS = \{(.*?)\n\};", _app(), re.DOTALL)
    assert match
    sent = re.search(r"sent:\s*(.*)", match.group(1)).group(1)
    assert "sentSource" in sent, f"загрузчик «Отправлено» игнорирует выбранный источник: {sent}"


@skip_without_node
def test_an_unloaded_resume_picker_does_not_detach_the_resume() -> None:
    """Найдено ревью. `renderResumePicker` вызывается только после
    успешного `GET /api/resumes`. Если запрос не удался, список пуст,
    `collectCriteria` читает пустую строку и отправляет `resume_id: null`
    — следующее «Сохранить критерии» молча отвязывает резюме от пресета и
    рапортует «сохранено». Пустой список означает «не знаю», а не «без
    вложения»."""
    source = _extract_function_source("collectCriteria")
    script = """
const fields = {
  hhSalaryFrom: '0', hhSearchPeriod: '1', hhResumeId: '',
  templateText: '', hhCoverLetter: '', hhExperience: 'noExperience',
};
global.document = { getElementById: id => (id in fields ? { value: fields[id] } : null) };
""" + source + """
// Селекта резюме в документе нет вовсе — как если бы список не загрузился.
const patch = collectCriteria({});
if ('resume_id' in patch) {
  console.error('resume_id ушёл в патч при незагруженном списке:', patch.resume_id);
  process.exitCode = 1;
}

// А когда список есть и в нём выбрано «без вложения» — поле обязано уехать.
fields.criteriaResume = '';
const chosen = collectCriteria({});
if (chosen.resume_id !== null) {
  console.error('явный выбор «без вложения» не доехал:', chosen.resume_id);
  process.exitCode = 1;
}
"""
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


# ── Аккордеоны, ровный ряд пресетов, заметный переключатель ───────────


def test_long_lists_are_collapsible() -> None:
    """Семь каналов, восемь профессий, восемь ключевых слов и два набора
    стоп-слов разворачивались во весь экран сразу — «Обзор» получался в
    несколько экранов прокрутки.

    `<details>`, а не самодельный аккордеон: раскрытие, клавиатура и
    доступность достаются даром, обработчик не нужен вовсе — что важно при
    `script-src 'self'` без `'unsafe-inline'`.
    """
    overview = _overview()
    assert overview.count('<details class="field-group">') >= 5, (
        "длинные списки критериев не сворачиваются"
    )
    for поле in ("channelEditList", "hhKwList", "kwList", "exList", "hhExList"):
        assert f'data-count-for="{поле}"' in overview, (
            f"у раздела {поле} нет счётчика — свёрнутым он прячет и сам факт, "
            "что в нём что-то есть"
        )


def test_the_accordions_are_closed_by_default() -> None:
    """Смысл правки — чтобы экран не выглядел громоздким при открытии.
    Раздел, открытый по умолчанию, эту задачу не решает."""
    overview = _overview()
    assert "<details class=\"field-group\" open>" not in overview
    assert "<details open" not in overview


def test_a_collapsed_section_still_holds_its_values() -> None:
    """Свёрнутый `<details>` прячет содержимое, но не удаляет его из
    документа: поля остаются доступны `collectCriteria`. Проверяется, что
    поля именно ВНУТРИ аккордеонов, а не вынесены наружу ради сохранения —
    иначе смысл сворачивания теряется."""
    overview = _overview()
    for поле in ("newChannel", "newHHKw", "newKw", "templateText", "hhCoverLetter"):
        начало = overview.index(f'id="{поле}"')
        открыт = overview.rfind("<details", 0, начало)
        закрыт = overview.rfind("</details>", 0, начало)
        assert открыт > закрыт, f"поле {поле} осталось вне аккордеона"


def test_the_source_switcher_leads_the_metrics() -> None:
    """Переключатель стоял справа в шапке страницы — маленький и
    незаметный, и было неочевидно, чем он управляет. Теперь он вплотную
    над плитками, которые переключает."""
    overview = _overview()
    переключатель = overview.index('id="metricsSource"')
    плитки = overview.index('class="metrics-grid"')
    заголовок = overview.index('class="page-header"')
    шапка_конец = overview.index("</div>", overview.index('page-title">Обзор'))

    assert переключатель > шапка_конец, "переключатель всё ещё в шапке страницы"
    assert переключатель < плитки, "переключатель оторван от плиток, которыми управляет"


@skip_without_node
def test_every_preset_card_has_the_same_shape() -> None:
    """Ряд пресетов был рваным: у активного нет кнопки «удалить», поэтому
    его колонка короче соседних, и при выравнивании по центру чипы
    разъезжались по вертикали.

    Место под крестик занято всегда — у активного он просто невидим.
    """
    source = _extract_function_source("renderPresetBar")
    script = "\n".join((
        "const узлы = [];",
        """
        global.document = {
          getElementById: () => ({ className: '', querySelectorAll: () => [] }),
          createElement: (tag) => {
            const node = { tag, attributes: {}, children: [], style: {},
              className: '', append(c) { this.children.push(c); },
              setAttribute(k, v) { this.attributes[k] = v; },
              addEventListener() {} };
            узлы.push(node);
            return node;
          },
        };
        """,
        "const presetState = { list: [",
        "  { id: 1, name: 'Мой поиск', is_active: true, channels_count: 7, professions_count: 8 },",
        "  { id: 2, name: 'Базовый', is_active: false, channels_count: 0, professions_count: 0 },",
        "] };",
        "function fill(t, c) { t.children = [].concat(c); }",
        "function updateStatusBar() {}",
        _extract_function_source("isHttpUrl"),
        _extract_function_source("el"),
        source,
        """
        renderPresetBar();
        const карточки = узлы.filter(n => (n.className || '').includes('preset-card'));
        if (карточки.length !== 2) {
          console.error('карточек', карточки.length); process.exitCode = 1;
        }
        const крестики = узлы.filter(n => (n.className || '').includes('preset-drop'));
        if (крестики.length !== 2) {
          console.error('место под крестик занято не у всех:', крестики.length);
          process.exitCode = 1;
        }
        const скрытых = крестики.filter(n => n.className.includes('hidden'));
        if (скрытых.length !== 1) {
          console.error('крестик скрыт не ровно у активного:', скрытых.length);
          process.exitCode = 1;
        }
        """,
    ))
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


# ── Ввод и правка значений в списках ──────────────────────────────────

LIST_INPUTS = ("newChannel", "newKw", "newEx", "newHHKw", "newHHEx")


@pytest.mark.parametrize("field", LIST_INPUTS)
def test_enter_adds_the_value_to_the_list(field: str) -> None:
    """Набирать значение и тянуться мышью к «+» — по разу на каждое из
    восьми ключевых слов. Enter делает то же самое.

    Отдельного кода это не потребовало: механизм `data-enter-action` уже
    существовал ради поля чата, и достаточно объявить действие.
    """
    overview = _overview()
    начало = overview.index(f'id="{field}"')
    объявление = overview[начало:overview.index(">", начало)]
    # Атрибут целиком, а не подстрокой: проверка на вхождение
    # «data-enter-action» проходила и для `data-enter-action-x`, то есть
    # для опечатки, которая ничего не делает. Поймано мутацией.
    assert re.search(r'\bdata-enter-action\s*=\s*"[^"]+"', объявление), (
        f"поле {field} не добавляет значение по Enter: {объявление}"
    )


@pytest.mark.parametrize("field", LIST_INPUTS)
def test_the_enter_action_matches_the_button(field: str) -> None:
    """Enter и «+» обязаны делать одно и то же. Разные действия на двух
    способах одного ввода — это два места, которые разойдутся."""
    overview = _overview()
    начало = overview.index(f'id="{field}"')
    хвост = overview[начало:начало + 600]
    по_enter = re.search(r'data-enter-action="([^"]+)"', хвост).group(1)
    по_кнопке = re.search(r'data-action="(add[^"]+)"', хвост).group(1)
    assert по_enter == по_кнопке, (
        f"Enter вызывает {по_enter}, а кнопка {по_кнопке}"
    )


@skip_without_node
def test_a_list_value_can_be_edited_in_place() -> None:
    """Правка прямо в строке, а не «удалить и набрать заново».

    Здесь же закреплено то, чего не видно из разметки: список НЕ
    перерисовывается на каждом нажатии. Перерисовка уносит фокус и
    каретку — дописать слово становится нельзя, а поймать это можно
    только руками.
    """
    script = "\n".join((
        "const списки = {};",
        """
        let перерисовок = 0;
        const узлы = [];
        global.document = {
          getElementById: (id) => списки[id],
          createElement: (tag) => {
            const node = { tag, attributes: {}, children: [], style: {}, className: '',
              handlers: {}, value: '',
              append(c) { this.children.push(c); },
              setAttribute(k, v) { this.attributes[k] = v; if (k === 'value') this.value = v; },
              addEventListener(name, fn) { this.handlers[name] = fn; },
              blur() { this.handlers.blur && this.handlers.blur({ target: this }); } };
            узлы.push(node);
            return node;
          },
        };
        списки.список = { children: [], value: '' };
        function fill(t, c) { t.children = [].concat(c); }
        function showToast() {}
        """,
        _extract_function_source("isHttpUrl"),
        _extract_function_source("el"),
        _extract_function_source("renderEditableList"),
        """
        const слова = ['qa', 'тестировщик'];
        const рисуем = () => { перерисовок += 1; renderEditableList('список', слова, { rerender: рисуем }); };
        рисуем();

        const поле = узлы.find(n => n.tag === 'input' && n.value === 'qa');
        if (!поле) { console.error('строка не редактируемая — значение не в поле ввода'); process.exitCode = 1; }

        // Печатаем по букве: перерисовки быть не должно, иначе уйдёт фокус.
        const было = перерисовок;
        поле.handlers.input({ target: { value: 'frontend' } });
        поле.handlers.input({ target: { value: 'frontend-разработчик' } });
        if (перерисовок !== было) {
          console.error('список перерисован во время набора — фокус потерян');
          process.exitCode = 1;
        }
        if (слова[0] !== 'frontend-разработчик') {
          console.error('правка не доехала:', слова[0]); process.exitCode = 1;
        }

        // Опустошили строку — значит удалили.
        поле.handlers.input({ target: { value: '   ' } });
        поле.handlers.blur({ target: { value: '   ' } });
        if (слова.length !== 1 || слова[0] !== 'тестировщик') {
          console.error('пустая строка осталась в списке:', JSON.stringify(слова));
          process.exitCode = 1;
        }
        """,
    ))
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


@skip_without_node
def test_editing_a_value_into_a_duplicate_does_not_keep_both() -> None:
    """Два одинаковых ключевых слова — не ошибка, но и не то, что человек
    имел в виду: поиск от повтора не изменится, а список станет длиннее.
    Повтор убирается, и об этом говорится вслух."""
    script = "\n".join((
        "const списки = { список: { children: [] } };",
        "const узлы = []; const сообщения = [];",
        """
        global.document = {
          getElementById: (id) => списки[id],
          createElement: (tag) => {
            const node = { tag, attributes: {}, children: [], style: {}, className: '',
              handlers: {}, value: '',
              append(c) { this.children.push(c); },
              setAttribute(k, v) { this.attributes[k] = v; if (k === 'value') this.value = v; },
              addEventListener(n, f) { this.handlers[n] = f; }, blur() {} };
            узлы.push(node); return node;
          },
        };
        function fill(t, c) { t.children = [].concat(c); }
        function showToast(m) { сообщения.push(m); }
        """,
        _extract_function_source("isHttpUrl"),
        _extract_function_source("el"),
        _extract_function_source("renderEditableList"),
        """
        const слова = ['qa', 'тестировщик'];
        const рисуем = () => renderEditableList('список', слова, { rerender: рисуем });
        рисуем();
        const первое = узлы.find(n => n.tag === 'input' && n.value === 'qa');
        первое.handlers.blur({ target: { value: 'тестировщик' } });
        if (слова.length !== 1 || слова[0] !== 'тестировщик') {
          console.error('повтор остался в списке:', JSON.stringify(слова)); process.exitCode = 1;
        }
        if (!сообщения.some(m => m.includes('уже есть'))) {
          console.error('про повтор не сказано ни слова:', сообщения); process.exitCode = 1;
        }
        """,
    ))
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


# ── Перенос списка между платформами ──────────────────────────────────


def test_both_platforms_offer_the_import() -> None:
    """Перенос нужен в обе стороны: человек может начать с профессий на
    hh.ru или с ключевых слов Telegram — заранее неизвестно, с чего."""
    overview = _overview()
    assert 'data-action="importKeywordsFromHh"' in _card("criteriaTg")
    assert 'data-action="importProfessionsFromTg"' in _card("criteriaHh")


@skip_without_node
def test_the_import_adds_and_never_replaces() -> None:
    """Главное свойство переноса.

    Замена стёрла бы то, что человек уже набрал руками, — и молча:
    отменить это нечем, кнопки «вернуть» нет. Поэтому перенос ДОБАВЛЯЕТ,
    совпадения пропускает, и повторное нажатие ничего не портит.
    """
    script = "\n".join((
        _extract_function_source("mergeInto"),
        """
        // У человека уже есть свои ключевые слова, среди них одно
        // совпадает с профессией — регистр при этом разный.
        const ключевые = ['вёрстка', 'react'];
        const профессии = ['Frontend-разработчик', 'React', 'Vue-разработчик'];

        const добавлено = mergeInto(ключевые, профессии, { lower: true });

        if (добавлено !== 2) { console.error('добавлено', добавлено, 'вместо 2'); process.exitCode = 1; }
        if (!ключевые.includes('вёрстка')) {
          console.error('своё значение стёрто переносом'); process.exitCode = 1;
        }
        if (ключевые.filter(k => k.toLowerCase() === 'react').length !== 1) {
          console.error('совпадение задвоилось:', JSON.stringify(ключевые)); process.exitCode = 1;
        }
        if (!ключевые.includes('frontend-разработчик')) {
          console.error('перенос не привёл к нижнему регистру:', JSON.stringify(ключевые));
          process.exitCode = 1;
        }

        // Повторное нажатие — ничего не меняет.
        const снимок = JSON.stringify(ключевые);
        const ещё = mergeInto(ключевые, профессии, { lower: true });
        if (ещё !== 0 || JSON.stringify(ключевые) !== снимок) {
          console.error('повторный перенос изменил список'); process.exitCode = 1;
        }

        // И источник не тронут: перенос копирует, а не перемещает.
        if (профессии.length !== 3) {
          console.error('источник изменён:', JSON.stringify(профессии)); process.exitCode = 1;
        }
        """,
    ))
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


@skip_without_node
def test_the_import_keeps_the_case_professions_need() -> None:
    """Обратное направление: ключевые слова Telegram хранятся в нижнем
    регистре, а профессия уезжает в поисковый запрос hh.ru как есть.
    Приводить её к нижнему регистру при переносе незачем — но и ломать
    то, что человек написал с большой буквы, тоже нельзя."""
    script = "\n".join((
        _extract_function_source("mergeInto"),
        """
        const профессии = ['Frontend-разработчик'];
        // Совпадение распознаётся без учёта регистра...
        mergeInto(профессии, ['frontend-разработчик'], {});
        if (профессии.length !== 1) {
          console.error('регистр помешал распознать совпадение:', JSON.stringify(профессии));
          process.exitCode = 1;
        }
        if (профессии[0] !== 'Frontend-разработчик') {
          console.error('исходное написание испорчено:', профессии[0]); process.exitCode = 1;
        }

        // ...но переносимое значение записывается КАК ЕСТЬ. Источник с
        // заглавными буквами здесь обязателен: прежние данные состояли из
        // одних строчных, и ветка «не приводить к нижнему регистру» была
        // неотличима от «приводить». Мутация это и показала.
        mergeInto(профессии, ['React Developer'], {});
        if (!профессии.includes('React Developer')) {
          console.error('написание переносимой профессии испорчено:', JSON.stringify(профессии));
          process.exitCode = 1;
        }
        """,
    ))
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
