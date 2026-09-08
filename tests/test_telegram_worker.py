from datetime import date, datetime

import pytest

from job_monitor.db import connection
from job_monitor.db.repositories import TgRepo
from job_monitor.settings import AppSettings
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
def settings():
    return AppSettings(
        channels=["itvacancykz"], keywords=["qa"], exclude=["senior"],
        template="привет", safe_mode=False, max_per_day=2, delay_min=1, delay_max=1,
    )


class Sender:
    def __init__(self):
        self.sent: list[str] = []

    async def __call__(self, username: str) -> None:
        self.sent.append(username)


def test_extract_usernames():
    # @hr короче четырёх символов — под правила Telegram не подходит и отбрасывается
    assert extract_usernames("пишите @hr_anna или @hr.bob") == ["@hr_anna"]
    assert extract_usernames("@hr_anna и снова @hr_anna") == ["@hr_anna"]


def test_post_matches_only_on_keyword_without_exclusion(settings):
    assert post_matches(IncomingPost("itvacancykz", "нужен QA"), settings) is True
    assert post_matches(IncomingPost("itvacancykz", "нужен Senior QA"), settings) is False
    assert post_matches(IncomingPost("itvacancykz", "нужен повар"), settings) is False
    assert post_matches(IncomingPost("other", "нужен QA"), settings) is False


def test_skips_bots_and_source_channels(settings):
    assert is_eligible("@hr_anna", settings, own_username="@me") is True
    assert is_eligible("@some_bot", settings, own_username="@me") is False
    assert is_eligible("@itvacancykz", settings, own_username="@me") is False   # L10
    assert is_eligible("@me", settings, own_username="@me") is False


async def test_sends_once_per_contact(conn, settings):
    repo, sender = TgRepo(conn), Sender()
    post = IncomingPost(channel="itvacancykz", text="Нужен QA, пишите @hr_anna")
    assert await process_post(post, settings, repo, sender, FROZEN) == ["@hr_anna"]
    assert await process_post(post, settings, repo, sender, FROZEN) == []          # L1
    assert sender.sent == ["@hr_anna"]
    assert repo.contacts_total() == 1


async def test_respects_daily_limit_from_database(conn, settings):
    repo, sender = TgRepo(conn), Sender()
    repo.record_send("@old_one", None, None, NOW)
    repo.record_send("@old_two", None, None, NOW)
    post = IncomingPost(channel="itvacancykz", text="QA нужен, @hr_anna")
    assert await process_post(post, settings, repo, sender, FROZEN) == []
    assert repo.sent_on(date(2026, 9, 8)) == 2


async def test_limit_resets_on_a_new_day_without_any_reset_job(conn, settings):
    repo, sender = TgRepo(conn), Sender()
    repo.record_send("@old_one", None, None, datetime(2026, 9, 7, 23, 0))
    repo.record_send("@old_two", None, None, datetime(2026, 9, 7, 23, 30))
    post = IncomingPost(channel="itvacancykz", text="QA нужен, @hr_anna")
    assert await process_post(post, settings, repo, sender, FROZEN) == ["@hr_anna"]  # L9


async def test_exclude_word_blocks_post(conn, settings):
    repo, sender = TgRepo(conn), Sender()
    post = IncomingPost(channel="itvacancykz", text="Senior QA нужен, @hr_anna")
    assert await process_post(post, settings, repo, sender, FROZEN) == []


async def test_safe_mode_records_nothing(conn, settings):
    settings = settings.model_copy(update={"safe_mode": True})
    repo, sender = TgRepo(conn), Sender()
    post = IncomingPost(channel="itvacancykz", text="QA нужен, @hr_anna")
    assert await process_post(post, settings, repo, sender, FROZEN) == []
    assert sender.sent == []
    assert repo.contacts_total() == 0


async def test_post_from_foreign_channel_is_ignored(conn, settings):
    repo, sender = TgRepo(conn), Sender()
    post = IncomingPost(channel="random_channel", text="QA нужен, @hr_anna")
    assert await process_post(post, settings, repo, sender, FROZEN) == []


async def test_a_send_after_midnight_is_dated_today_not_yesterday(conn, settings):
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
    ticks = iter([before_midnight, before_midnight, after_midnight, after_midnight])

    repo, sender = TgRepo(conn), Sender()
    settings.max_per_day = 10
    post = IncomingPost("itvacancykz", "нужен QA: @hr_anna и @hr_boris")

    assert await process_post(post, settings, repo, sender, lambda: next(ticks)) == [
        "@hr_anna", "@hr_boris",
    ]

    assert repo.sent_on(date(2026, 9, 8)) == 1, "первая отправка должна остаться вчерашней"
    assert repo.sent_on(date(2026, 9, 9)) == 1, (
        "вторая отправка случилась после полуночи и обязана считаться сегодняшней"
    )
