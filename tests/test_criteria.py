from __future__ import annotations

import pytest
from pydantic import ValidationError

from job_monitor.criteria import (
    HH_AREA_ID,
    HH_EMPLOYMENT,
    HH_EXPERIENCE,
    HH_SCHEDULE,
    SearchCriteria,
)


def test_defaults_carry_no_search_terms_at_all() -> None:
    """Чистая установка не должна искать чужую работу. Значения по умолчанию
    в унаследованном коде были буквально поиском предыдущего автора:
    казахстанские QA-каналы, «junior», «без опыта», регион 113."""
    empty = SearchCriteria()
    for field in (
        "channels", "professions", "tg_keywords", "tg_exclude",
        "hh_exclude", "hh_employment", "hh_schedule",
    ):
        assert getattr(empty, field) == [], f"{field} по умолчанию не пуст"
    assert empty.template == ""
    assert empty.hh_cover_letter == ""
    assert empty.hh_resume_id == ""
    assert empty.resume_id is None
    assert empty.hh_salary_from == 0


def test_region_is_a_constant_not_a_field() -> None:
    assert HH_AREA_ID == 113
    assert "hh_area_ids" not in SearchCriteria.model_fields
    assert "hh_area_id" not in SearchCriteria.model_fields


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SearchCriteria(hh_area_ids=[113])


def test_experience_accepts_only_known_codes() -> None:
    assert SearchCriteria(hh_experience="between1And3").hh_experience == "between1And3"
    with pytest.raises(ValidationError):
        SearchCriteria(hh_experience="сколько-нибудь")
    assert set(HH_EXPERIENCE) == {
        "noExperience", "between1And3", "between3And6", "moreThan6",
    }


def test_employment_and_schedule_accept_only_known_codes() -> None:
    assert SearchCriteria(hh_employment=["full", "part"]).hh_employment == [
        "full", "part",
    ]
    with pytest.raises(ValidationError):
        SearchCriteria(hh_employment=["full", "выдуманное"])
    with pytest.raises(ValidationError):
        SearchCriteria(hh_schedule=["никогда"])
    assert set(HH_EMPLOYMENT) == {"full", "part", "project", "probation", "volunteer"}
    assert set(HH_SCHEDULE) == {"remote", "fullDay", "flexible", "shift"}


def test_employment_is_multi_and_experience_is_single() -> None:
    """hh.ru принимает несколько типов занятости, и одно значение сужает
    выдачу, отсекая вакансии, помеченные сразу двумя. Опыт — одно значение,
    как на самом hh.ru."""
    assert SearchCriteria.model_fields["hh_employment"].annotation == list[str]
    assert SearchCriteria.model_fields["hh_experience"].annotation is str


def test_numeric_bounds_are_enforced() -> None:
    with pytest.raises(ValidationError):
        SearchCriteria(hh_salary_from=-1)
    with pytest.raises(ValidationError):
        SearchCriteria(hh_search_period=0)
    with pytest.raises(ValidationError):
        SearchCriteria(hh_search_period=31)
    assert SearchCriteria(hh_search_period=30).hh_search_period == 30
