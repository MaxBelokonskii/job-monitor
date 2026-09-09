from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest
from pydantic import ValidationError

from job_monitor import presets
from job_monitor.criteria import SearchCriteria
from job_monitor.db.repositories import PresetsRepo, SettingsRepo

NOW = datetime(2026, 9, 9, 12, 0, 0)


@pytest.fixture()
def conn(bare_conn) -> sqlite3.Connection:
    """Схема без бутстрапа: эти тесты проверяют сам бутстрап, поэтому база
    должна быть до него — см. `bare_conn` в conftest."""
    return bare_conn


def test_ensure_default_creates_one_empty_preset(conn) -> None:
    preset_id = presets.ensure_default(conn, NOW)
    rows = PresetsRepo(conn).list()
    assert len(rows) == 1
    assert rows[0]["name"] == "Мой поиск"
    assert presets.active_criteria(conn) == SearchCriteria()
    assert presets.active_preset(conn)["id"] == preset_id


def test_ensure_default_is_idempotent(conn) -> None:
    first = presets.ensure_default(conn, NOW)
    second = presets.ensure_default(conn, NOW)
    assert first == second
    assert PresetsRepo(conn).count() == 1


def test_set_active_switches_what_the_workers_read(conn) -> None:
    repo = PresetsRepo(conn)
    a = repo.create("A", {"channels": ["a"]}, NOW)
    b = repo.create("B", {"channels": ["b"]}, NOW)
    presets.set_active(conn, a)
    assert presets.active_criteria(conn).channels == ["a"]
    presets.set_active(conn, b)
    assert presets.active_criteria(conn).channels == ["b"]


def test_save_criteria_keeps_unrelated_fields(conn) -> None:
    preset_id = PresetsRepo(conn).create("A", {"channels": ["a"]}, NOW)
    saved = presets.save_criteria(conn, preset_id, {"tg_keywords": ["qa"]})
    assert saved.channels == ["a"], "патч не должен терять несвязанные поля"
    assert saved.tg_keywords == ["qa"]


def test_save_criteria_rejects_a_bad_patch_without_touching_the_row(conn) -> None:
    preset_id = PresetsRepo(conn).create("A", {"channels": ["a"]}, NOW)
    with pytest.raises(ValidationError):
        presets.save_criteria(conn, preset_id, {"hh_experience": "выдумка"})
    stored = PresetsRepo(conn).get(preset_id)["criteria"]
    assert stored == {"channels": ["a"]}, (
        "отвергнутый патч не должен попадать в базу — ни целиком, ни частично"
    )


def test_legacy_settings_become_the_first_preset(conn) -> None:
    """Перенос существующих настроек: критерии уезжают в пресет
    «Импортированные настройки», глобальное остаётся в settings."""
    SettingsRepo(conn).save({
        "channels": ["itvacancykz"],
        "keywords": ["qa", "тестировщик"],
        "exclude": ["senior"],
        "template": "здравствуйте",
        "hh_keywords": ["QA"],
        "hh_exclude": ["lead"],
        "hh_area_ids": [113],
        "hh_cover_letter": "письмо",
        "hh_experience": "noExperience",
        "hh_employment": ["full", "part"],
        "hh_schedule": ["remote"],
        "hh_salary_from": 100000,
        "hh_search_period": 7,
        "hh_resume_id": "abc",
        "file_path": "",
        "safe_mode": False,
        "max_per_day": 30,
    })

    preset_id = presets.import_legacy_criteria(conn, NOW)

    assert preset_id is not None
    stored = PresetsRepo(conn).get(preset_id)
    assert stored["name"] == "Импортированные настройки"
    criteria = SearchCriteria(**stored["criteria"])
    assert criteria.channels == ["itvacancykz"]
    assert criteria.tg_keywords == ["qa", "тестировщик"]
    assert criteria.tg_exclude == ["senior"]
    assert criteria.professions == ["QA"], "hh_keywords должны стать professions"
    assert criteria.hh_exclude == ["lead"]
    assert criteria.template == "здравствуйте"
    assert criteria.hh_cover_letter == "письмо"
    assert criteria.hh_salary_from == 100000
    assert criteria.hh_search_period == 7
    assert criteria.hh_resume_id == "abc"
    assert criteria.hh_employment == ["full", "part"]
    assert criteria.hh_schedule == ["remote"]

    left = SettingsRepo(conn).load()
    assert left["safe_mode"] is False, "глобальное должно остаться"
    assert left["max_per_day"] == 30
    for gone in ("channels", "keywords", "exclude", "template", "hh_keywords",
                 "hh_area_ids", "file_path"):
        assert gone not in left, f"{gone} должен был уехать из settings"
    assert left["active_preset_id"] == preset_id


def test_legacy_import_is_idempotent(conn) -> None:
    SettingsRepo(conn).save({"channels": ["a"], "safe_mode": True})
    first = presets.import_legacy_criteria(conn, NOW)
    second = presets.import_legacy_criteria(conn, NOW)
    assert second is None, "повторный перенос не должен создавать второй пресет"
    assert PresetsRepo(conn).count() == 1
    assert PresetsRepo(conn).get(first)["criteria"]["channels"] == ["a"]


def test_nothing_to_import_leaves_the_database_alone(conn) -> None:
    SettingsRepo(conn).save({"safe_mode": True, "max_per_day": 10})
    assert presets.import_legacy_criteria(conn, NOW) is None
    assert PresetsRepo(conn).count() == 0


def test_out_of_range_legacy_value_does_not_lose_the_rest(conn) -> None:
    """У прежней версии не было границ, поэтому в базе может лежать
    значение, которое новая модель отвергает. Терять из-за одного поля весь
    перенос нельзя — это тот же дефект, что чинили в `migrate-legacy`: первый
    невалидный ключ уносил и контакты, и вакансии."""
    SettingsRepo(conn).save({
        "channels": ["a"],
        "hh_search_period": 999,     # новая модель требует 1..30
        "safe_mode": True,
    })
    preset_id = presets.import_legacy_criteria(conn, NOW)
    criteria = SearchCriteria(**PresetsRepo(conn).get(preset_id)["criteria"])
    assert criteria.channels == ["a"]
    assert criteria.hh_search_period == 1, "отвергнутое поле берёт значение по умолчанию"


def test_several_bad_legacy_values_are_all_dropped(conn) -> None:
    SettingsRepo(conn).save({
        "channels": ["a"],
        "hh_search_period": 999,
        "hh_salary_from": -5,
        "hh_experience": "выдумка",
        "safe_mode": True,
    })
    preset_id = presets.import_legacy_criteria(conn, NOW)
    criteria = SearchCriteria(**PresetsRepo(conn).get(preset_id)["criteria"])
    assert criteria.channels == ["a"]
    assert criteria.hh_search_period == 1
    assert criteria.hh_salary_from == 0
    assert criteria.hh_experience == "noExperience"
