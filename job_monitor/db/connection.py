"""Подключение к SQLite: PRAGMA, синглтон соединения приложения, транзакции."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from job_monitor import paths
from job_monitor.db.migrations import migrate

_connection: sqlite3.Connection | None = None


def connect(database: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(
        database or str(paths.db_file()),
        isolation_level=None,      # управляем транзакциями сами
        check_same_thread=False,   # HH-воркер живёт в отдельном потоке
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    migrate(conn)
    return conn


def get_connection() -> sqlite3.Connection:
    global _connection
    if _connection is None:
        _connection = connect()
    return _connection


def reset_connection() -> None:
    global _connection
    if _connection is not None:
        _connection.close()
    _connection = None


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
