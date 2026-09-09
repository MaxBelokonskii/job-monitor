"""Интерфейс после переезда критериев в пресеты.

Это не редизайн — он в подпроекте 3. Задача одна: приложение должно остаться
работоспособным после смены контракта API, не потеряв инвариантов, за которые
заплачено предыдущей веткой.
"""

from __future__ import annotations

import re
from pathlib import Path

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


def test_the_activation_conflict_is_shown_and_not_swallowed(client) -> None:
    """Отказ активации (воркер не остановился) приходит как 409 с текстом.
    Проглотить его — значит показать переключённый пресет там, где он не
    переключился."""
    source = _app_source()
    assert "409" in source or "detail" in source


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
