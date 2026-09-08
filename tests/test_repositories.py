from datetime import date, datetime

import pytest

from job_monitor.db import connection
from job_monitor.db.repositories import EventsRepo, HhRepo, SettingsRepo, TgRepo

DAY = date(2026, 9, 8)
NOW = datetime(2026, 9, 8, 12, 0, 0)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    connection.reset_connection()
    yield connection.connect()
    connection.reset_connection()


def test_settings_roundtrip(conn):
    repo = SettingsRepo(conn)
    assert repo.load() == {}
    repo.save({"max_per_day": 25, "channels": ["a", "b"]})
    assert repo.load() == {"max_per_day": 25, "channels": ["a", "b"]}


def test_settings_save_replaces_not_appends(conn):
    repo = SettingsRepo(conn)
    repo.save({"max_per_day": 25})
    repo.save({"max_per_day": 10})
    assert repo.load() == {"max_per_day": 10}


def test_record_send_dedupes_contacts_but_keeps_history(conn):
    repo = TgRepo(conn)
    assert repo.was_sent("@vasya") is False
    repo.record_send("@vasya", "itvacancykz", "QA junior", NOW)
    repo.record_send("@vasya", "itvacancykz", "QA junior", NOW)
    assert repo.was_sent("@vasya") is True
    assert repo.contacts_total() == 1          # L1: дублей больше нет
    assert repo.sent_on(DAY) == 2              # но история отправок полная


def test_sent_on_counts_only_that_day(conn):
    repo = TgRepo(conn)
    repo.record_send("@a", None, None, datetime(2026, 9, 7, 23, 59))
    repo.record_send("@b", None, None, NOW)
    assert repo.sent_on(DAY) == 1


def test_hh_upsert_updates_existing(conn):
    repo = HhRepo(conn)
    repo.upsert({"vacancy_id": "1", "title": "QA", "found_at": NOW.isoformat(), "status": "найдено"})
    repo.upsert({"vacancy_id": "1", "title": "QA", "found_at": NOW.isoformat(),
                 "status": "отклик отправлен", "applied_at": NOW.isoformat()})
    rows = repo.recent(10)
    assert len(rows) == 1
    assert rows[0]["status"] == "отклик отправлен"
    assert repo.applied_on(DAY) == 1
    assert repo.applied_total() == 1


def test_hh_found_counts_all_statuses(conn):
    repo = HhRepo(conn)
    repo.upsert({"vacancy_id": "1", "title": "QA", "found_at": NOW.isoformat(), "status": "пропущено"})
    repo.upsert({"vacancy_id": "2", "title": "QA", "found_at": NOW.isoformat(),
                 "status": "отклик отправлен", "applied_at": NOW.isoformat()})
    assert repo.found_on(DAY) == 2
    assert repo.applied_on(DAY) == 1


def test_events_are_ordered_newest_first(conn):
    repo = EventsRepo(conn)
    repo.add("tg", "started", None, datetime(2026, 9, 8, 10, 0))
    repo.add("tg", "stopped", "по кнопке", datetime(2026, 9, 8, 11, 0))
    events = repo.recent("tg", 10)
    assert [event["kind"] for event in events] == ["stopped", "started"]
