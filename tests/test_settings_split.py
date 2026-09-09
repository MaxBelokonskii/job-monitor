from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError

from job_monitor.db.connection import connect
from job_monitor.settings import GlobalSettings, load_settings, save_settings


@pytest.fixture()
def conn(tmp_path) -> sqlite3.Connection:
    return connect(str(tmp_path / "t.db"))


def test_criteria_fields_are_gone_from_global_settings() -> None:
    fields = set(GlobalSettings.model_fields)
    for gone in (
        "channels", "keywords", "exclude", "template", "file_path",
        "hh_keywords", "hh_exclude", "hh_area_ids", "hh_cover_letter",
        "hh_experience", "hh_employment", "hh_schedule", "hh_salary_from",
        "hh_search_period", "hh_resume_id",
    ):
        assert gone not in fields, f"{gone} — критерий, ему место в пресете"


def test_global_settings_keep_the_shared_budget() -> None:
    """Лимиты и задержки общие: банят аккаунт, а не пресет (D7)."""
    fields = set(GlobalSettings.model_fields)
    for kept in (
        "delay_min", "delay_max", "max_per_day", "history_limit", "safe_mode",
        "parse_history", "tg_autostart", "hh_max_per_day", "hh_delay_min",
        "hh_delay_max", "hh_check_interval", "hh_autostart",
        "hh_selenium_steps", "active_preset_id",
    ):
        assert kept in fields, f"{kept} потерялся при разделении"


def test_saving_a_criteria_field_into_settings_is_rejected(conn) -> None:
    with pytest.raises(ValidationError):
        save_settings(conn, {"channels": ["a"]})


def test_safe_defaults_stay_safe(conn) -> None:
    fresh = load_settings(conn)
    assert fresh.safe_mode is True
    assert fresh.parse_history is False
    assert fresh.tg_autostart is False
    assert fresh.hh_autostart is False


def test_active_preset_id_round_trips(conn) -> None:
    """Подключение уже проставило активный пресет (бутстрап), и это часть
    контракта: приложение никогда не остаётся без активного пресета."""
    assert load_settings(conn).active_preset_id is not None
    save_settings(conn, {"active_preset_id": 7})
    assert load_settings(conn).active_preset_id == 7


def test_a_fresh_database_always_has_an_active_preset(bare_conn) -> None:
    """До бутстрапа активного пресета нет — значит его ставит именно он, а не
    значение по умолчанию модели."""
    assert load_settings(bare_conn).active_preset_id is None
