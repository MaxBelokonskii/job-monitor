"""Воркеры читают активный пресет, а лимиты и дедупликация остаются общими."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from job_monitor import presets, resume_store
from job_monitor.criteria import HH_AREA_ID, SearchCriteria
from job_monitor.db.repositories import PresetsRepo, TgFoundRepo, TgRepo
from job_monitor.settings import GlobalSettings
from job_monitor.workers.hh import build_search_url
from job_monitor.workers.telegram import IncomingPost, process_post

NOW = datetime(2026, 9, 9, 12, 0, 0)


@pytest.fixture()
def conn(bare_conn, tmp_path, monkeypatch) -> sqlite3.Connection:
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    return bare_conn


@pytest.fixture()
def criteria() -> SearchCriteria:
    return SearchCriteria(
        channels=["qajobs"],
        tg_keywords=["qa", "тестировщик"],
        tg_exclude=["senior"],
        professions=["инженер по тестированию"],
        template="Здравствуйте! Увидел {ключевое_слово} в {канал}. Я {профессия}.",
    )


@pytest.fixture()
def settings() -> GlobalSettings:
    return GlobalSettings(safe_mode=False, delay_min=1, delay_max=1, max_per_day=10)


class Sender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, Path | None]] = []

    async def __call__(self, username: str, text: str, attachment: Path | None) -> None:
        self.messages.append((username, text, attachment))


def call(post, criteria, settings, conn, sender, attachment=None):
    return process_post(
        post,
        criteria,
        settings,
        TgRepo(conn),
        TgFoundRepo(conn),
        sender,
        lambda: NOW,
        "me",
        attachment,
    )


async def test_the_message_text_comes_from_the_active_preset(
    conn, criteria, settings
) -> None:
    sender = Sender()
    post = IncomingPost("qajobs", "Ищем QA, писать @hr_anna", 7)
    assert await call(post, criteria, settings, conn, sender) == ["@hr_anna"]
    assert sender.messages[0][1] == (
        "Здравствуйте! Увидел qa в qajobs. Я инженер по тестированию."
    )


async def test_a_missing_resume_does_not_stop_the_send(conn, criteria, settings) -> None:
    """Резюме не выбрано или файл удалён руками — сообщение всё равно уходит,
    но без вложения: резюме не обязательная часть отклика."""
    sender = Sender()
    post = IncomingPost("qajobs", "Ищем QA, писать @hr_anna", 7)
    assert await call(post, criteria, settings, conn, sender, None) == ["@hr_anna"]
    assert sender.messages[0][2] is None


async def test_the_attachment_is_passed_through_when_it_exists(
    conn, criteria, settings
) -> None:
    stored_name, _ = resume_store.store("cv.pdf", b"%PDF-1.4 x")
    attachment = resume_store.path_of(stored_name)
    sender = Sender()
    post = IncomingPost("qajobs", "Ищем QA, писать @hr_anna", 7)
    await call(post, criteria, settings, conn, sender, attachment)
    assert sender.messages[0][2] == attachment


async def test_dedup_of_contacts_is_shared_across_presets(
    conn, criteria, settings
) -> None:
    """Дедупликация общая (решение D7). Человек, которому написали из одного
    пресета, не должен получить второе сообщение из другого: канал и
    профессия сменились, а адресат тот же."""
    sender = Sender()

    first = PresetsRepo(conn).create("QA", criteria.model_dump(), NOW)
    presets.set_active(conn, first)
    await call(
        IncomingPost("qajobs", "Ищем QA, писать @hr_anna", 1),
        criteria, settings, conn, sender,
    )
    assert len(sender.messages) == 1

    other = SearchCriteria(
        channels=["support_jobs"],
        tg_keywords=["поддержка"],
        professions=["специалист поддержки"],
        template="Здравствуйте!",
    )
    second = PresetsRepo(conn).create("Поддержка", other.model_dump(), NOW)
    presets.set_active(conn, second)
    sent = await call(
        IncomingPost("support_jobs", "Нужна поддержка, писать @hr_anna", 2),
        other, settings, conn, sender,
    )
    assert sent == [], "тому же человеку написали второй раз из другого пресета"
    assert len(sender.messages) == 1


async def test_the_daily_budget_is_shared_across_presets(
    conn, criteria, settings
) -> None:
    """Три пресета по 25 отправок не должны давать 75 сообщений с одного
    аккаунта: банят аккаунт, а не пресет."""
    sender = Sender()
    tight = settings.model_copy(update={"max_per_day": 1})

    await call(
        IncomingPost("qajobs", "QA, писать @first", 1), criteria, tight, conn, sender
    )
    sent = await call(
        IncomingPost("qajobs", "QA, писать @second", 2), criteria, tight, conn, sender
    )
    assert sent == [], "дневной лимит не удержал вторую отправку"
    assert len(sender.messages) == 1


def test_the_hh_search_url_uses_the_preset_and_the_constant_area() -> None:
    """Регион больше не настройка: он приходит из константы, и подменить его
    через API нельзя (решение D8)."""
    url = build_search_url(
        "инженер по тестированию",
        SearchCriteria(
            professions=["инженер по тестированию"],
            hh_salary_from=100000,
            hh_employment=["full", "part"],
            hh_schedule=["remote"],
            hh_search_period=7,
        ),
    )
    assert f"area={HH_AREA_ID}" in url
    assert HH_AREA_ID == 113
    assert "text=инженер+по+тестированию" in url
    assert "salary=100000" in url
    assert "employment=full" in url and "employment=part" in url
    assert "schedule=remote" in url
    assert "search_period=7" in url


def test_the_hh_search_url_takes_the_area_from_nowhere_else() -> None:
    """Ни одно поле критериев не должно уметь повлиять на регион."""
    url = build_search_url("QA", SearchCriteria(hh_salary_from=1))
    assert url.count("area=") == 1
    assert f"area={HH_AREA_ID}" in url


async def test_post_dedup_holds_on_its_own_without_the_contact_dedup(
    conn, criteria, settings
) -> None:
    """Дедупликация ПОСТОВ проверяется отдельно от дедупликации контактов.

    Найдено мутационным аудитом: снятие проверки `tg_found` проходило
    зелёным, потому что второй проход по тому же посту всё равно отсекался
    дедупликацией контактов — проверка держалась за счёт соседней. Здесь
    каждый проход несёт СВОЙ контакт, поэтому уцелеть может только дедуп
    постов.
    """
    sender = Sender()
    post = IncomingPost("qajobs", "Ищем QA, писать @first", 7)
    assert await call(post, criteria, settings, conn, sender) == ["@first"]

    # Тот же пост (тот же channel+message_id), но текст с другим контактом:
    # так бывает при редактировании поста в канале.
    edited = IncomingPost("qajobs", "Ищем QA, писать @second", 7)
    assert await call(edited, criteria, settings, conn, sender) == [], (
        "пост с тем же идентификатором обработан второй раз"
    )
    assert [m[0] for m in sender.messages] == ["@first"]


async def test_the_post_dedup_does_not_depend_on_the_timestamp(
    conn, criteria, settings
) -> None:
    """Ограничение уникальности можно было сузить до тройки с `found_at`
    незаметно: все остальные тесты пишут одно и то же замороженное время."""
    from job_monitor.db.repositories import TgFoundRepo

    repo = TgFoundRepo(conn)
    assert repo.record("qajobs", 7, None, None, None, datetime(2026, 9, 9, 12, 0)) is True
    assert repo.record("qajobs", 7, None, None, None, datetime(2026, 9, 10, 18, 30)) is False, (
        "тот же пост, записанный в другое время, принят как новый — "
        "уникальность включает метку времени"
    )


async def test_the_hh_daily_budget_stops_the_loop(conn) -> None:
    """Суточный лимит hh.ru не применялся нигде в тестах — его выключение
    проходило зелёным, хотя для Telegram то же свойство закрыто тремя
    проверками. Лимит существует по той же причине: банят аккаунт."""
    from datetime import date

    from job_monitor.db.repositories import HH_STATUS_APPLIED, HhRepo
    from job_monitor.settings import GlobalSettings

    repo = HhRepo(conn)
    for index in range(3):
        repo.upsert({
            "vacancy_id": str(index),
            "title": "QA",
            "found_at": "2026-09-09T10:00:00",
            "applied_at": datetime.combine(date.today(), datetime.min.time())
            .isoformat(timespec="seconds"),
            "status": HH_STATUS_APPLIED,
        })

    settings = GlobalSettings(hh_max_per_day=3)
    assert repo.applied_on(date.today()) >= settings.hh_max_per_day, (
        "дневной лимит hh не достигается — цикл продолжит откликаться"
    )

    generous = GlobalSettings(hh_max_per_day=10)
    assert repo.applied_on(date.today()) < generous.hh_max_per_day
