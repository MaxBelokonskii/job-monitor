"""Миграция 004: колонки очереди найденного.

Только добавление колонок — таблицы не пересоздаются. Причина в докстринге
`job_monitor/db/migrations.py`: `DROP COLUMN` в SQLite означает пересоздание
таблицы, а в этих двух лежит настоящая переписка и история откликов.
"""

from __future__ import annotations

import sqlite3

import pytest

from job_monitor import paths, statuses
from job_monitor.db import migrations
from job_monitor.db.migrations import migrate


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _connect(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        tmp_path / "t.db", isolation_level=None, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def upgraded_from_003(tmp_path, monkeypatch):
    """База, доведённая до версии 3, наполненная, и потом домигрированная.

    Именно так выглядит апгрейд у пользователя: строки уже лежат, и вопрос
    в том, какие значения они получат. Свежесозданная база этого не
    проверяет вовсе — в ней нечего наполнять.
    """
    conn = _connect(tmp_path)
    monkeypatch.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS[:3])
    migrate(conn)
    conn.execute(
        "INSERT INTO hh_applications (vacancy_id, title, found_at, status)"
        " VALUES ('1', 'QA', '2026-09-01T10:00:00', ?)",
        (statuses.AUTO_APPLIED,),
    )
    conn.execute(
        "INSERT INTO tg_found (channel, message_id, found_at, preview)"
        " VALUES ('qajobs', 42, '2026-09-01T10:00:00', 'ищем QA')"
    )
    monkeypatch.undo()
    migrate(conn)
    return conn


def test_migration_adds_the_queue_columns(tmp_path) -> None:
    conn = _connect(tmp_path)
    migrate(conn)
    assert {"status", "status_at", "usernames"} <= _columns(conn, "tg_found")
    assert "status_source" in _columns(conn, "hh_applications")


def test_the_new_indexes_exist(tmp_path) -> None:
    """Оба списка читаются с фильтром по статусу — без индексов это скан."""
    conn = _connect(tmp_path)
    migrate(conn)
    names = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    assert {"idx_tg_found_status", "idx_hh_status"} <= names


def test_existing_hh_rows_are_attributed_to_the_robot(upgraded_from_003) -> None:
    """Их все писал он: ручных откликов до этой миграции не существовало."""
    row = upgraded_from_003.execute(
        "SELECT status, status_source FROM hh_applications WHERE vacancy_id = '1'"
    ).fetchone()
    assert row["status_source"] == statuses.SOURCE_ROBOT
    assert row["status"] == statuses.AUTO_APPLIED, "миграция не должна трогать статус"


def test_existing_tg_rows_start_as_new(upgraded_from_003) -> None:
    """Знать, был ли по посту отклик, неоткуда. Дедупликацию контактов это
    не трогает: она живёт в `tg_contacts`, а не здесь."""
    row = upgraded_from_003.execute(
        "SELECT status, status_at, usernames FROM tg_found WHERE message_id = 42"
    ).fetchone()
    assert row["status"] == statuses.NEW
    assert row["status_at"] is None
    assert row["usernames"] is None


def test_migration_is_idempotent(tmp_path) -> None:
    conn = _connect(tmp_path)
    assert migrate(conn) == migrations.SCHEMA_VERSION
    assert migrate(conn) == migrations.SCHEMA_VERSION
    assert {"status", "status_at", "usernames"} <= _columns(conn, "tg_found")


def test_the_migration_survives_being_applied_twice(tmp_path) -> None:
    """Сбой на середине миграции не должен запирать приложение навсегда.

    `executescript` коммитит по ходу дела, поэтому обрыв между третьим
    `ALTER` и подъёмом версии оставляет часть колонок добавленными, а
    версию — прежней. Голый `ALTER TABLE ADD COLUMN` в этом состоянии
    падает на «duplicate column name», и падает так каждый следующий
    запуск: починить это изнутри приложения нечем. Все предыдущие миграции
    этого файла переживают повтор по построению (`CREATE … IF NOT
    EXISTS`); четвёртая обязана вести себя так же.
    """
    conn = _connect(tmp_path)
    migrate(conn)
    # Имитируем именно тот отказ: колонки на месте, версия откачена.
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (3)")

    assert migrate(conn) == migrations.SCHEMA_VERSION
    assert {"status", "status_at", "usernames"} <= _columns(conn, "tg_found")


def test_the_repeat_check_is_not_vacuous(tmp_path) -> None:
    """Обратная сторона: колонка, которой ещё нет, обязана добавиться —
    иначе `_add_column_if_missing` могла бы просто ничего не делать."""
    conn = _connect(tmp_path)
    monkeyless = migrations.MIGRATIONS[:3]
    original, migrations.MIGRATIONS = migrations.MIGRATIONS, monkeyless
    try:
        migrate(conn)
    finally:
        migrations.MIGRATIONS = original
    assert "status" not in _columns(conn, "tg_found")

    migrate(conn)
    assert "status" in _columns(conn, "tg_found")


def test_schema_version_reached_four() -> None:
    """Страховка от вакуумности: без записи в MIGRATIONS все проверки выше
    прошли бы на базе версии 3, где новых колонок просто нет."""
    assert migrations.SCHEMA_VERSION >= 4
    assert [target for target, _ in migrations.MIGRATIONS] == list(
        range(1, migrations.SCHEMA_VERSION + 1)
    )


def test_upgrading_from_003_leaves_a_backup(tmp_path, monkeypatch) -> None:
    """Копия снимается существующим механизмом — один раз за прогон, до
    первого DML. Проверяется, что новая миграция его не обошла."""
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path / "data"))
    conn = _connect(tmp_path)
    monkeypatch.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS[:3])
    migrate(conn)
    monkeypatch.undo()
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path / "data"))
    migrate(conn)
    assert paths.db_backup_file("004").exists(), "миграция не оставила копию БД"
    assert conn.in_transaction is False, (
        "после миграции осталась открытая транзакция — следующий "
        "Connection.backup() повиснет в неограниченном retry-цикле"
    )
