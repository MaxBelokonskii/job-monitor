"""Подключение к SQLite: PRAGMA, синглтон соединения приложения, транзакции."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from job_monitor import paths
from job_monitor.db.migrations import migrate

_connection: sqlite3.Connection | None = None


def connect(database: str | None = None) -> sqlite3.Connection:
    target = database or str(paths.db_file())
    conn = sqlite3.connect(
        target,
        isolation_level=None,      # управляем транзакциями сами
        check_same_thread=False,   # HH-воркер живёт в отдельном потоке
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    migrate(conn)
    _bootstrap(conn)
    _secure(target)
    return conn


def _bootstrap(conn: sqlite3.Connection) -> None:
    """Перенос критериев в пресет и создание пресета по умолчанию.

    Здесь, а не в `migrate()`, по двум причинам. Первая: это перенос ДАННЫХ,
    а не схемы, и делается он через модели и репозитории, которых SQL не
    знает. Вторая практическая: репозитории пишут через `transaction()`,
    то есть выдают явный `BEGIN IMMEDIATE`, и это работает только на
    соединении с `isolation_level=None` — а `migrate()` зовут и на сыром
    `sqlite3.connect()` в тестах схемы, где неявные транзакции включены и
    явный BEGIN падает с «cannot start a transaction within a transaction».

    Порядок важен: сначала перенос, потом создание пустого пресета по
    умолчанию. Наоборот пустой пресет занял бы место первого, `import`
    увидел бы непустую таблицу и решил, что перенос уже был, — критерии
    пользователя остались бы в старой строке и просто исчезли из виду.
    """
    from datetime import datetime

    from job_monitor.presets import ensure_default, import_legacy_criteria

    now = datetime.now()
    import_legacy_criteria(conn, now)
    ensure_default(conn, now)


# В WAL-режиме рядом с базой живут ещё два файла, и в `-wal` лежат те же
# данные, что и в самой базе, — до тех пор пока их не перенесли в неё. Права
# ставятся всем трём, иначе `job_monitor.db` под `0600` соседствовал бы с
# `job_monitor.db-wal` под `0644`.
DB_SIDECARS = ("", "-wal", "-shm")


def _secure(target: str) -> None:
    """`0600` базе и её спутникам: SQLite создаёт файлы по umask, то есть
    обычно `0644`, — в отличие от `.env`, cookies и логов, которым права
    ставятся явно. В базе лежат переписка и контакты."""
    if target == ":memory:" or target.startswith("file::memory:"):
        return
    for suffix in DB_SIDECARS:
        paths.secure_file(Path(target + suffix))


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
    # BEGIN IMMEDIATE (not the default deferred BEGIN) takes the write lock
    # up front. In WAL mode, a deferred BEGIN lets two writers both start a
    # read snapshot, so the second one to commit gets SQLITE_BUSY_SNAPSHOT —
    # a conflict the busy handler does *not* retry, so busy_timeout is no
    # help and the caller sees a raw exception. BEGIN IMMEDIATE instead
    # serializes writers at BEGIN time, turning that into an ordinary lock
    # wait that busy_timeout does absorb. With a single writer this changes
    # nothing.
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
