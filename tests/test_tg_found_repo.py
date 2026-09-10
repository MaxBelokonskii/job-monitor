from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from job_monitor.db.connection import connect
from job_monitor.db.repositories import TgFoundRepo

NOW = datetime(2026, 9, 9, 12, 0, 0)


@pytest.fixture()
def conn(tmp_path) -> sqlite3.Connection:
    # Соединение приложения, а не сырое `sqlite3.connect`: `transaction()`
    # выдаёт явный BEGIN IMMEDIATE и требует `isolation_level=None`, иначе
    # получается «cannot start a transaction within a transaction».
    return connect(str(tmp_path / "t.db"))


def test_first_record_is_accepted_and_the_repeat_is_not(conn) -> None:
    repo = TgFoundRepo(conn)
    assert repo.record("qajobs", 42, "@hr", "текст", "qa", NOW) is True
    assert repo.record("qajobs", 42, "@hr", "текст", "qa", NOW) is False, (
        "повторная запись того же поста должна отвергаться: это и есть "
        "дедупликация постов"
    )
    assert repo.exists("qajobs", 42) is True
    assert repo.exists("qajobs", 43) is False


def test_the_same_message_id_in_another_channel_is_a_different_post(conn) -> None:
    repo = TgFoundRepo(conn)
    assert repo.record("a", 1, None, None, None, NOW) is True
    assert repo.record("b", 1, None, None, None, NOW) is True


def test_recent_returns_newest_first(conn) -> None:
    repo = TgFoundRepo(conn)
    repo.record("a", 1, None, "старый", None, datetime(2026, 9, 1, 10, 0, 0))
    repo.record("a", 2, None, "новый", None, datetime(2026, 9, 2, 10, 0, 0))
    assert [row["preview"] for row in repo.recent(10)] == ["новый", "старый"]


def test_recorded_fields_read_back(conn) -> None:
    repo = TgFoundRepo(conn)
    repo.record("qajobs", 7, "@hr_anna", "Ищем QA", "qa", NOW)
    row = repo.recent(1)[0]
    assert row["channel"] == "qajobs"
    assert row["message_id"] == 7
    assert row["username"] == "@hr_anna"
    assert row["preview"] == "Ищем QA"
    assert row["matched_keyword"] == "qa"
    assert row["found_at"] == "2026-09-09T12:00:00"
