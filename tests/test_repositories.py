import sqlite3
from datetime import date, datetime

import pytest

from job_monitor.db import connection
from job_monitor.db.repositories import HH_STATUS_APPLIED, EventsRepo, HhRepo, SettingsRepo, TgRepo

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
    repo.record_send("@c", None, None, datetime(2026, 9, 9, 0, 0, 0))
    assert repo.sent_on(DAY) == 1


def test_record_send_rolls_back_contacts_insert_on_failure(tmp_path):
    """`record_send()` is the only two-statement write in the codebase, and
    its atomicity (both rows land, or neither does) is the entire reason
    `transaction()` exists. Force the second statement (the tg_sends
    insert) to fail and assert the first (the tg_contacts upsert) rolled
    back instead of leaving a contact behind with no history.

    `sqlite3.Connection` is an immutable C type — its bound methods can't be
    monkeypatched on an instance — so the failure is injected via a
    `Connection` subclass passed as the `factory` to `sqlite3.connect()`.
    """
    from job_monitor.db.migrations import migrate

    class FlakyConnection(sqlite3.Connection):
        fail_sends = False

        def execute(self, sql, parameters=()):
            if self.fail_sends and sql.strip().startswith("INSERT INTO tg_sends"):
                raise sqlite3.IntegrityError("simulated failure")
            return super().execute(sql, parameters)

    raw_conn = sqlite3.connect(
        str(tmp_path / "flaky.db"),
        isolation_level=None,
        check_same_thread=False,
        factory=FlakyConnection,
    )
    raw_conn.row_factory = sqlite3.Row
    raw_conn.execute("PRAGMA journal_mode=WAL")
    raw_conn.execute("PRAGMA foreign_keys=ON")
    raw_conn.execute("PRAGMA busy_timeout=5000")
    migrate(raw_conn)

    repo = TgRepo(raw_conn)
    raw_conn.fail_sends = True
    with pytest.raises(sqlite3.IntegrityError):
        repo.record_send("@doomed", "itvacancykz", "preview", NOW)
    raw_conn.fail_sends = False

    assert repo.was_sent("@doomed") is False
    assert repo.contacts_total() == 0
    raw_conn.close()


def test_ensure_contact_creates_contact_without_a_send_row(conn):
    """I3: the legacy importer's all_sent_users.txt fallback proves a
    contact was reached at some point, but not when — its own timestamp is
    the file's mtime, not a real send date. ensure_contact() must register
    the contact without fabricating a tg_sends row, so it can't inflate
    sent_on() for whatever day the import happens to run on."""
    repo = TgRepo(conn)
    fallback_seen = datetime(2020, 1, 1, 0, 0, 0)
    repo.ensure_contact("@old_contact", fallback_seen)

    assert repo.was_sent("@old_contact") is True
    assert repo.contacts_total() == 1
    sends = conn.execute("SELECT COUNT(*) AS n FROM tg_sends").fetchone()["n"]
    assert sends == 0
    assert repo.sent_on(date.today()) == 0


def test_ensure_contact_is_a_noop_if_contact_already_exists(conn):
    repo = TgRepo(conn)
    repo.record_send("@known", "itvacancykz", "preview", NOW)
    repo.ensure_contact("@known", datetime(2020, 1, 1))
    assert repo.contacts_total() == 1
    sends = conn.execute("SELECT COUNT(*) AS n FROM tg_sends").fetchone()["n"]
    assert sends == 1


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


def test_hh_daily_counters_use_a_half_open_day_range(conn):
    """found_at/applied_at are compared with >= day and < day+1 rather than
    `LIKE 'YYYY-MM-DD%'` — measured with EXPLAIN QUERY PLAN, the LIKE form
    can't use the indexes on these columns (a full SCAN) while the range
    form gets a SEARCH using them, and the range form doesn't depend on the
    stored string having exactly that prefix shape. Pin the actual boundary
    behaviour: the last second of the previous day and the first instant of
    the next day must both be excluded."""
    repo = HhRepo(conn)
    repo.upsert({
        "vacancy_id": "before", "title": "QA",
        "found_at": datetime(2026, 9, 7, 23, 59, 59).isoformat(timespec="seconds"),
        "applied_at": datetime(2026, 9, 7, 23, 59, 59).isoformat(timespec="seconds"),
        "status": HH_STATUS_APPLIED,
    })
    repo.upsert({
        "vacancy_id": "on_day", "title": "QA",
        "found_at": DAY.isoformat() + "T00:00:00",
        "applied_at": DAY.isoformat() + "T00:00:00",
        "status": HH_STATUS_APPLIED,
    })
    repo.upsert({
        "vacancy_id": "after", "title": "QA",
        "found_at": datetime(2026, 9, 9, 0, 0, 0).isoformat(timespec="seconds"),
        "applied_at": datetime(2026, 9, 9, 0, 0, 0).isoformat(timespec="seconds"),
        "status": HH_STATUS_APPLIED,
    })
    assert repo.found_on(DAY) == 1
    assert repo.applied_on(DAY) == 1


def test_hh_upsert_status_only_update_keeps_title(conn):
    repo = HhRepo(conn)
    repo.upsert({"vacancy_id": "1", "title": "QA", "found_at": NOW.isoformat(), "status": "найдено"})
    repo.upsert({"vacancy_id": "1", "status": "отклик отправлен", "applied_at": NOW.isoformat()})
    rows = repo.recent(10)
    assert len(rows) == 1
    assert rows[0]["title"] == "QA"
    assert rows[0]["status"] == "отклик отправлен"
    assert repo.applied_on(DAY) == 1


def test_hh_upsert_partial_update_keeps_previous_values(conn):
    repo = HhRepo(conn)
    repo.upsert({
        "vacancy_id": "1", "title": "QA", "company": "Acme", "salary": "100k",
        "city": "Almaty", "url": "https://hh.ru/vacancy/1",
        "found_at": NOW.isoformat(), "status": "найдено",
    })
    repo.upsert({
        "vacancy_id": "1", "title": "QA",
        "found_at": NOW.isoformat(), "status": "отклик отправлен",
        "applied_at": NOW.isoformat(),
    })
    rows = repo.recent(10)
    assert len(rows) == 1
    assert rows[0]["company"] == "Acme"
    assert rows[0]["salary"] == "100k"
    assert rows[0]["city"] == "Almaty"
    assert rows[0]["url"] == "https://hh.ru/vacancy/1"


def test_events_are_ordered_newest_first(conn):
    repo = EventsRepo(conn)
    repo.add("tg", "started", None, datetime(2026, 9, 8, 10, 0))
    repo.add("tg", "stopped", "по кнопке", datetime(2026, 9, 8, 11, 0))
    events = repo.recent("tg", 10)
    assert [event["kind"] for event in events] == ["stopped", "started"]
