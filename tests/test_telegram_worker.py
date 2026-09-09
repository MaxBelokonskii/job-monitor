from datetime import date, datetime

import pytest

from job_monitor.db import connection
from job_monitor.criteria import SearchCriteria
from job_monitor.db.repositories import TgFoundRepo, TgRepo
from job_monitor.settings import GlobalSettings
from job_monitor.workers.telegram import (
    IncomingPost, extract_usernames, is_eligible, post_matches, process_post,
)

NOW = datetime(2026, 9, 8, 12, 0, 0)
# `process_post` берёт время через `clock()` на каждой итерации, а не
# получает один снимок на пост: см. test_a_send_after_midnight_is_dated_today.
FROZEN = lambda: NOW      # noqa: E731 — часы-заглушка, не функция логики


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    connection.reset_connection()
    yield connection.connect()
    connection.reset_connection()


@pytest.fixture
def criteria():
    return SearchCriteria(
        channels=["itvacancykz"], tg_keywords=["qa"], tg_exclude=["senior"],
        template="привет",
    )


@pytest.fixture
def settings():
    return GlobalSettings(safe_mode=False, max_per_day=2, delay_min=1, delay_max=1)


class Sender:
    def __init__(self):
        self.sent: list[str] = []

    async def __call__(self, username: str, text: str, attachment=None) -> None:
        self.sent.append(username)


def call(post, criteria, settings, repo, conn, sender, clock):
    """Вызов `process_post` со свежим репозиторием найденного.

    Репозиторий здесь, а не в фикстуре, чтобы каждый тест видел его состояние
    явно: дедупликация постов и дедупликация контактов — разные свойства, и
    подменять одно другим нельзя.
    """
    from job_monitor.workers.telegram import process_post as _pp

    return _pp(post, criteria, settings, repo, TgFoundRepo(conn), sender, clock)


def test_extract_usernames():
    # @hr короче четырёх символов — под правила Telegram не подходит и отбрасывается
    assert extract_usernames("пишите @hr_anna или @hr.bob") == ["@hr_anna"]
    assert extract_usernames("@hr_anna и снова @hr_anna") == ["@hr_anna"]


def test_post_matches_returns_the_matched_keyword(criteria):
    """Возвращается само слово, а не `bool`: оно идёт и в подстановку
    `{ключевое_слово}`, и в колонку `matched_keyword`."""
    assert post_matches(IncomingPost("itvacancykz", "нужен QA", 1), criteria) == "qa"
    assert post_matches(IncomingPost("itvacancykz", "нужен Senior QA", 2), criteria) is None
    assert post_matches(IncomingPost("itvacancykz", "нужен повар", 3), criteria) is None
    assert post_matches(IncomingPost("other", "нужен QA", 4), criteria) is None


def test_skips_bots_and_source_channels(criteria):
    assert is_eligible("@hr_anna", criteria, own_username="@me") is True
    assert is_eligible("@some_bot", criteria, own_username="@me") is False
    assert is_eligible("@itvacancykz", criteria, own_username="@me") is False   # L10
    assert is_eligible("@me", criteria, own_username="@me") is False


async def test_sends_once_per_contact(conn, criteria, settings):
    """L1: дедупликация КОНТАКТОВ.

    Второй пост — с другим `message_id`. Иначе его отсёк бы дедуп ПОСТОВ, и
    проверка стала бы вакуумной: зелёной независимо от того, работает
    дедупликация контактов или нет.
    """
    repo, sender = TgRepo(conn), Sender()
    first = IncomingPost("itvacancykz", "Нужен QA, пишите @hr_anna", 1)
    same_contact_other_post = IncomingPost("itvacancykz", "Опять QA: @hr_anna", 2)
    assert await call(first, criteria, settings, repo, conn, sender, FROZEN) == ["@hr_anna"]
    assert await call(
        same_contact_other_post, criteria, settings, repo, conn, sender, FROZEN
    ) == []
    assert sender.sent == ["@hr_anna"]
    assert repo.contacts_total() == 1


async def test_the_same_post_is_processed_only_once(conn, criteria, settings):
    """Дедупликация ПОСТОВ, которой не было вовсе: тот же пост, пришедший
    повторным событием или перечитанный на следующем круге, обрабатывался
    заново."""
    repo, sender = TgRepo(conn), Sender()
    post = IncomingPost("itvacancykz", "Нужен QA, пишите @hr_anna", 7)
    assert await call(post, criteria, settings, repo, conn, sender, FROZEN) == ["@hr_anna"]
    assert await call(post, criteria, settings, repo, conn, sender, FROZEN) == []
    assert sender.sent == ["@hr_anna"]


async def test_respects_daily_limit_from_database(conn, criteria, settings):
    repo, sender = TgRepo(conn), Sender()
    repo.record_send("@old_one", None, None, NOW)
    repo.record_send("@old_two", None, None, NOW)
    post = IncomingPost("itvacancykz", "QA нужен, @hr_anna", 11)
    assert await call(post, criteria, settings, repo, conn, sender, FROZEN) == []
    assert repo.sent_on(date(2026, 9, 8)) == 2


async def test_limit_resets_on_a_new_day_without_any_reset_job(conn, criteria, settings):
    repo, sender = TgRepo(conn), Sender()
    repo.record_send("@old_one", None, None, datetime(2026, 9, 7, 23, 0))
    repo.record_send("@old_two", None, None, datetime(2026, 9, 7, 23, 30))
    post = IncomingPost("itvacancykz", "QA нужен, @hr_anna", 11)
    assert await call(post, criteria, settings, repo, conn, sender, FROZEN) == ["@hr_anna"]  # L9


async def test_exclude_word_blocks_post(conn, criteria, settings):
    repo, sender = TgRepo(conn), Sender()
    post = IncomingPost("itvacancykz", "Senior QA нужен, @hr_anna", 12)
    assert await call(post, criteria, settings, repo, conn, sender, FROZEN) == []


async def test_safe_mode_records_nothing(conn, criteria, settings):
    settings = settings.model_copy(update={"safe_mode": True})
    repo, sender = TgRepo(conn), Sender()
    post = IncomingPost("itvacancykz", "QA нужен, @hr_anna", 11)
    assert await call(post, criteria, settings, repo, conn, sender, FROZEN) == []
    assert sender.sent == []
    assert repo.contacts_total() == 0


async def test_post_from_foreign_channel_is_ignored(conn, criteria, settings):
    repo, sender = TgRepo(conn), Sender()
    post = IncomingPost("random_channel", "QA нужен, @hr_anna", 13)
    assert await call(post, criteria, settings, repo, conn, sender, FROZEN) == []


async def test_a_send_after_midnight_is_dated_today_not_yesterday(conn, criteria, settings):
    """L9, краевой случай: пост обрабатывается дольше, чем длятся сутки.

    Между отправками стоит пауза до `delay_max` (до 3600 секунд), а хэндлов в
    одном посте бывает несколько. Раньше `now` снимался один раз, в момент
    прихода поста, и уходил во ВСЕ `record_send` цикла: отправка, случившаяся
    после полуночи, ложилась в базу вчерашней датой — и в тот же вчерашний
    дневной лимит. Недосчёт, не перерасход, но дата отправки в `tg_sends`
    просто неверна, а «отправлено сегодня» на дашборде считается по ней.
    """
    before_midnight = datetime(2026, 9, 8, 23, 59, 30)
    after_midnight = datetime(2026, 9, 9, 0, 0, 30)
    # Пять тиков, а не четыре: первый уходит на запись поста в `tg_found`
    # (время находки), остальные четыре — по два на каждую отправку.
    ticks = iter([
        before_midnight, before_midnight, before_midnight,
        after_midnight, after_midnight,
    ])

    repo, sender = TgRepo(conn), Sender()
    settings = settings.model_copy(update={"max_per_day": 10})
    post = IncomingPost("itvacancykz", "нужен QA: @hr_anna и @hr_boris", 14)

    assert await call(post, criteria, settings, repo, conn, sender, lambda: next(ticks)) == [
        "@hr_anna", "@hr_boris",
    ]

    assert repo.sent_on(date(2026, 9, 8)) == 1, "первая отправка должна остаться вчерашней"
    assert repo.sent_on(date(2026, 9, 9)) == 1, (
        "вторая отправка случилась после полуночи и обязана считаться сегодняшней"
    )
