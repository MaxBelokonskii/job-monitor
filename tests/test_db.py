import sqlite3

import pytest

from job_monitor.db import connection, migrations


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    connection.reset_connection()
    yield connection.connect()
    connection.reset_connection()


def test_migrations_create_all_tables(conn):
    names = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"schema_version", "settings", "tg_contacts", "tg_sends",
            "hh_applications", "worker_events"} <= names


def test_migrate_is_idempotent(conn):
    assert migrations.migrate(conn) == migrations.SCHEMA_VERSION
    assert migrations.migrate(conn) == migrations.SCHEMA_VERSION


def test_wal_and_foreign_keys_are_on(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_transaction_rolls_back(conn):
    with pytest.raises(RuntimeError):
        with connection.transaction(conn):
            conn.execute("INSERT INTO settings (key, value) VALUES ('x', '1')")
            raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0


def test_unique_username_in_tg_contacts(conn):
    conn.execute(
        "INSERT INTO tg_contacts (username, first_sent_at, last_sent_at, send_count)"
        " VALUES ('@a', '2026-09-08T10:00:00', '2026-09-08T10:00:00', 1)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO tg_contacts (username, first_sent_at, last_sent_at, send_count)"
            " VALUES ('@a', '2026-09-08T11:00:00', '2026-09-08T11:00:00', 1)"
        )
