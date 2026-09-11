"""Интерфейс после переезда критериев в пресеты.

Это не редизайн — он в подпроекте 3. Задача одна: приложение должно остаться
работоспособным после смены контракта API, не потеряв инвариантов, за которые
заплачено предыдущей веткой.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_frontend_events import (
    NODE_CHECK_HELPER,
    _maybe_extract,
    _run_node,
)
from tests.conftest import requires_node as skip_without_node

REPO_ROOT = Path(__file__).resolve().parents[1]


def _app_source() -> str:
    return (REPO_ROOT / "frontend" / "app.js").read_text(encoding="utf-8")


def _index_source() -> str:
    return (REPO_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


# ── Инварианты, за которые заплачено предыдущей веткой ────────────────


def test_no_inline_handlers_survive_the_change() -> None:
    """CSP держит `script-src 'self'`, и один уцелевший инлайновый обработчик
    делает её либо сломанной, либо ложью. Считаются вхождения, а не строки:
    `grep -c` однажды уже дал в этом проекте неверный ответ."""
    assert len(re.findall(r"\son[a-z]+=", _index_source())) == 0


def test_no_inner_html_survives_the_change() -> None:
    source = _app_source()
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert forbidden not in source, f"{forbidden} вернулся в app.js"


# ── Пресеты ───────────────────────────────────────────────────────────


def test_the_preset_switcher_is_delegated_and_talks_to_the_preset_api() -> None:
    source = _app_source()
    assert "'/presets'" in source or '"/presets"' in source
    assert "activatePreset" in source
    assert "activatePreset" in _index_source() or "data-arg" in _index_source()


def test_criteria_are_patched_to_the_preset_not_to_config() -> None:
    """`/api/config` теперь про глобальное. Критерий, ушедший туда, будет
    отвергнут с 422 — но молча для пользователя, если не проверять."""
    source = _app_source()
    for gone in (
        "channels", "keywords:", "exclude:", "template:", "hh_keywords",
        "hh_area_ids", "hh_cover_letter", "hh_experience", "hh_schedule",
        "hh_salary_from", "hh_search_period", "hh_resume_id",
    ):
        assert f"apiPatch('/config', {{ {gone}" not in source, (
            f"{gone} — критерий, он должен идти в /presets/<id>"
        )
    assert re.search(r"apiPatch\(`?/presets/", source), (
        "критерии никуда не сохраняются — экраны настроек стали мёртвыми"
    )


@skip_without_node
def test_the_activation_conflict_is_shown_and_not_swallowed() -> None:
    """Отказ активации приходит как 409 с текстом причины.

    Прежняя версия этой проверки была вакуумной: `assert "409" in source or
    "detail" in source`, а слово `detail` встречается в `app.js` полтора
    десятка раз по другим поводам — удаление всей обработки конфликта
    проходило зелёным. Мутационный аудит это и поймал.

    Цена ровно та, что запрещает решение D9: на экране «Пресет переключён»,
    а воркер продолжает работать по старым критериям и со старым резюме.
    Поэтому проверяется поведение: что показано пользователю и обновился ли
    список пресетов.

    Здесь же закреплено перечитывание критериев на успешном переключении —
    без него форма показывала бы значения предыдущего пресета, а
    сохранение записало бы их поверх нового.
    """
    script = _maybe_extract("activatePreset") + NODE_CHECK_HELPER + """
    let reloaded = 0;
    let criteriaReloaded = 0;
    const toasts = [];
    globalThis.showToast = (text) => toasts.push(text);
    globalThis.loadPresets = async () => { reloaded += 1; };
    globalThis.loadSettings = async () => {};
    globalThis.loadActiveCriteria = async () => { criteriaReloaded += 1; };
    globalThis.configErrorDetail = (r) => (r && r.detail) || '';

    // 409: воркер не остановился, пресет НЕ переключён.
    globalThis.apiPost = async () => ({ detail: 'воркер hh не остановился: таймаут' });
    await activatePreset(2);
    check('конфликт показан пользователю',
          toasts.some(t => t.includes('не остановился')));
    check('нет ложного «переключено»',
          !toasts.some(t => t.includes('Пресет переключён')));
    check('список пресетов перечитан', reloaded === 1);

    // Успех: пресет переключён, показано что остановлено.
    toasts.length = 0;
    globalThis.apiPost = async () => ({ activated: 2, stopped: ['tg'] });
    await activatePreset(2);
    check('успех показан', toasts.some(t => t.includes('Пресет переключён')));
    check('названо остановленное', toasts.some(t => t.includes('tg')));
    // Критерии обязаны перечитаться: чипы пресетов живут НА «Обзоре»,
    // перехода между экранами нет, и без этого в форме остались бы
    // значения предыдущего пресета — а «Сохранить критерии» записало бы
    // их поверх нового, уничтожив его настройку.
    check('критерии нового пресета перечитаны', criteriaReloaded === 1);
    """
    result = _run_node(f"(async () => {{\n{script}\n}})();")
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


# ── Справочники hh.ru ─────────────────────────────────────────────────


def test_the_pickers_are_built_from_the_dictionaries_endpoint() -> None:
    """Подписи не дублируются в разметке: иначе они разойдутся с
    константами бэкенда при первой же правке."""
    source = _app_source()
    assert "/dictionaries" in source
    index = _index_source()
    assert "Нет опыта" not in index, "подпись опыта захардкожена в разметке"
    assert 'class="hh-region"' not in index, (
        "регион больше не выбирается: он константа (решение D8)"
    )


def test_the_region_is_not_sent_from_the_frontend() -> None:
    source = _app_source()
    assert "hh_area_ids" not in source
    assert "areaIds" not in source


# ── Библиотека резюме ─────────────────────────────────────────────────


def test_the_dead_file_picker_is_gone_and_upload_took_its_place() -> None:
    """`onFileSelect` обновлял три подписи и никогда не отправлял файл на
    бэкенд — дефект L14, единственный оставленный открытым предыдущей
    веткой."""
    source = _app_source()
    assert "file_path" not in source, "поля file_path больше нет в контракте"
    assert "onFileSelect" not in source, "мёртвый обработчик остался"
    assert "/resumes" in source
    assert "X-Filename" in source, (
        "загрузка идёт сырым телом с именем в заголовке — см. api/resumes_routes.py"
    )


def test_the_resume_upload_does_not_use_form_data() -> None:
    """`multipart` требует `python-multipart`, которого в окружении нет, и
    заводит лишний разборщик недоверенного ввода."""
    source = _app_source()
    assert "FormData" not in source


# ── Предупреждение о синхронизируемом каталоге ────────────────────────


def test_the_synced_data_dir_warning_is_rendered() -> None:
    source = _app_source()
    assert "data_dir_warning" in source


@skip_without_node
def test_a_rejected_criteria_save_is_not_reported_as_saved() -> None:
    """Рецидив дефекта, который чинил коммит `8a2938d`, — только на новом пути.

    Тогда `apiPatch('/config', …)` возвращал результат, который никто не
    проверял, и отвергнутое сохранение показывало «сохранено». Теперь через
    `patchCriteria` идут ВСЕ критерии, то есть цена та же, а проверялось это
    только грепом: удаление `if (!configPatchOk(r))` проходило зелёным.
    """
    script = _maybe_extract("patchCriteria") + NODE_CHECK_HELPER + """
    const toasts = [];
    globalThis.showToast = (t) => toasts.push(t);
    globalThis.configPatchOk = (r) => !!r && r.status === 'saved';
    globalThis.configErrorDetail = (r) => (r && r.detail) || 'ошибка';
    globalThis.presetState = { activeId: 1, criteria: {} };

    globalThis.apiPatch = async () => ({ detail: 'значение вне границ' });
    const rejected = await patchCriteria({ hh_search_period: 999 });
    check('отвергнутое сохранение возвращает false', rejected === false);
    check('пользователю показана причина',
          toasts.some(t => t.includes('вне границ')));
    same('состояние не обновлено отвергнутым значением',
         globalThis.presetState.criteria, {});

    toasts.length = 0;
    globalThis.apiPatch = async () => ({ status: 'saved' });
    const ok = await patchCriteria({ channels: ['a'] });
    check('успешное сохранение возвращает true', ok === true);
    same('состояние обновлено', globalThis.presetState.criteria, { channels: ['a'] });
    """
    result = _run_node(f"(async () => {{\n{script}\n}})();")
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


# ── M-10: подстановки шаблона не дублируются руками ───────────────────


def test_the_template_placeholders_come_from_the_backend() -> None:
    """M-10. `PLACEHOLDERS` в `workers/telegram.py` не читал никто, а
    подсказка в интерфейсе была написана руками.

    Два списка одного и того же: добавим четвёртую подстановку в
    `render_template` — интерфейс о ней не узнает, а уберём третью — он
    продолжит её предлагать, и человек вставит в шаблон текст, который
    уйдёт живому человеку как есть, с фигурными скобками.
    """
    index = _index_source()
    assert "{канал}" not in index, (
        "подстановки перечислены прямо в разметке — они разойдутся с "
        "render_template при первой правке"
    )
    assert "placeholders" in _app_source(), (
        "интерфейс не запрашивает подстановки у бэкенда"
    )


def test_the_dictionaries_endpoint_serves_the_placeholders(client) -> None:
    """Обратная сторона: список обязан приходить, и приходить настоящий —
    тот же кортеж, который подставляет `render_template`."""
    from job_monitor.workers.telegram import PLACEHOLDERS

    served = client.get("/api/dictionaries").json()["placeholders"]
    assert served == list(PLACEHOLDERS)


def test_every_served_placeholder_is_actually_substituted() -> None:
    """Самая дорогая проверка из трёх: подсказка не должна обещать
    подстановку, которой нет. Обещанная и не выполненная подстановка
    уходит в личку живому человеку вместе с фигурными скобками."""
    from job_monitor.workers.telegram import PLACEHOLDERS, render_template

    rendered = render_template(
        " ".join(PLACEHOLDERS), "qajobs", "qa", "QA Engineer"
    )
    assert "{" not in rendered, (
        f"подстановка обещана, но не выполняется: {rendered}"
    )
