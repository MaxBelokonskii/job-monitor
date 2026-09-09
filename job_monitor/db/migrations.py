"""Версионированные миграции схемы SQLite и раннер, который их применяет.

**Колонку отсюда не удаляют.** В SQLite `DROP COLUMN` появился только в
3.35 и не работает для колонки под индексом или под ограничением, поэтому в
общем случае удаление означает пересоздание таблицы: новая схема, `INSERT
… SELECT`, `DROP`, `RENAME`. Делать это ради чистоты на таблицах, где лежит
настоящая переписка и история откликов пользователя, — плохой размен: цена
ошибки в миграции здесь измеряется потерянными данными, а выигрыш чисто
косметический.

Отсюда `tg_contacts.first_sent_at`, `last_sent_at`, `send_count` и
`source_channel`: они пишутся, но ни один SELECT в приложении их не читает
(подробности и семантика — в докстринге `TgRepo.record_send`). Это
сознательно оставленный долг, а не забытый код.
"""

from __future__ import annotations

import sqlite3

MIGRATION_001 = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tg_contacts (
    username       TEXT PRIMARY KEY,
    first_sent_at  TEXT NOT NULL,
    last_sent_at   TEXT NOT NULL,
    send_count     INTEGER NOT NULL DEFAULT 1,
    source_channel TEXT
);

CREATE TABLE IF NOT EXISTS tg_sends (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL REFERENCES tg_contacts(username),
    sent_at  TEXT NOT NULL,
    channel  TEXT,
    preview  TEXT
);
CREATE INDEX IF NOT EXISTS idx_tg_sends_sent_at ON tg_sends(sent_at);

CREATE TABLE IF NOT EXISTS hh_applications (
    vacancy_id TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    company    TEXT,
    salary     TEXT,
    city       TEXT,
    url        TEXT,
    found_at   TEXT NOT NULL,
    applied_at TEXT,
    status     TEXT NOT NULL,
    error      TEXT
);
CREATE INDEX IF NOT EXISTS idx_hh_applied_at ON hh_applications(applied_at);

CREATE TABLE IF NOT EXISTS worker_events (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    worker TEXT NOT NULL,
    at     TEXT NOT NULL,
    kind   TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_worker_events ON worker_events(worker, at);
"""

MIGRATION_002 = """
CREATE INDEX IF NOT EXISTS idx_hh_found_at ON hh_applications(found_at);
"""

MIGRATIONS: list[tuple[int, str]] = [(1, MIGRATION_001), (2, MIGRATION_002)]
SCHEMA_VERSION = MIGRATIONS[-1][0]


def _current_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    return int(row[0]) if row else 0


def migrate(conn: sqlite3.Connection) -> int:
    version = _current_version(conn)
    for target, script in MIGRATIONS:
        if target <= version:
            continue
        conn.executescript(script)
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (target,))
        version = target
    return version
