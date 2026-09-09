"""Миграция 003: таблицы пресетов, библиотеки резюме и найденного в Telegram."""

from __future__ import annotations

import sqlite3

from job_monitor.db.migrations import migrate


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_migration_creates_the_three_new_tables(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.row_factory = sqlite3.Row
    migrate(conn)

    assert _columns(conn, "presets") == {
        "id", "name", "position", "criteria", "created_at", "updated_at",
    }
    assert _columns(conn, "resumes") == {
        "id", "original_name", "stored_name", "size_bytes", "uploaded_at",
    }
    assert _columns(conn, "tg_found") == {
        "id", "channel", "message_id", "found_at", "username", "preview",
        "matched_keyword",
    }


def test_preset_names_are_unique(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "t.db")
    migrate(conn)
    conn.execute(
        "INSERT INTO presets (name, position, criteria, created_at, updated_at)"
        " VALUES ('Мой поиск', 1, '{}', 'now', 'now')"
    )
    try:
        conn.execute(
            "INSERT INTO presets (name, position, criteria, created_at, updated_at)"
            " VALUES ('Мой поиск', 2, '{}', 'now', 'now')"
        )
    except sqlite3.IntegrityError:
        return
    raise AssertionError("одноимённый пресет вставился — UNIQUE(name) потерян")


def test_the_same_post_cannot_be_recorded_twice(tmp_path) -> None:
    """Уникальность пары канал+сообщение — это и есть дедупликация постов,
    которой в приложении не было вовсе: пост, перечитанный на следующем
    круге, обрабатывался заново."""
    conn = sqlite3.connect(tmp_path / "t.db")
    migrate(conn)
    conn.execute(
        "INSERT INTO tg_found (channel, message_id, found_at) VALUES ('c', 7, 'now')"
    )
    try:
        conn.execute(
            "INSERT INTO tg_found (channel, message_id, found_at) VALUES ('c', 7, 'now')"
        )
    except sqlite3.IntegrityError:
        return
    raise AssertionError(
        "тот же пост вставился дважды — UNIQUE(channel, message_id) потерян"
    )


def test_migration_is_idempotent(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "t.db")
    assert migrate(conn) == 3
    assert migrate(conn) == 3


def test_the_backup_runs_once_per_run_and_outside_a_transaction(
    tmp_path, monkeypatch
) -> None:
    """Регресс на подвисание, найденное при реализации.

    Копия снималась внутри цикла миграций — то есть уже после
    `DELETE FROM schema_version`, который неявно открывает пишущую
    транзакцию. `Connection.backup()` в этом случае получает `SQLITE_BUSY`, а
    его внутренний retry-цикл не имеет таймаута и крутится бесконечно:
    процесс висел намертво, не напечатав ни строки, — отладить такое
    сложнее, чем упавший тест.

    Здесь проверяется свойство, а не место в коде: копия снимается ровно один
    раз за прогон и в момент, когда на источнике нет открытой транзакции.
    """
    from job_monitor.db import migrations

    calls: list[tuple[int, bool]] = []

    def spy(conn: sqlite3.Connection, target: int) -> None:
        calls.append((target, conn.in_transaction))

    conn = sqlite3.connect(tmp_path / "t.db")
    migrations.migrate(conn)
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.commit()

    monkeypatch.setattr(migrations, "_backup_before", spy)
    migrations.migrate(conn)

    assert len(calls) == 1, f"копия снята {len(calls)} раз(а) вместо одного: {calls}"
    target, in_transaction = calls[0]
    assert target == 3, "копия должна быть помечена итоговой версией прогона"
    assert in_transaction is False, (
        "копия снимается при открытой транзакции — Connection.backup() "
        "получит SQLITE_BUSY и зациклится без таймаута"
    )


def test_pending_migrations_leave_a_backup_of_the_database(tmp_path, monkeypatch) -> None:
    """Единственный необратимый шаг подпроекта — миграция строки настроек, где
    лежат реальные данные пользователя. Копия делается ДО применения и через
    sqlite3-backup, а не `shutil.copy`: база работает в режиме WAL, и часть
    свежих страниц лежит в файле `-wal`, поэтому побайтовая копия основного
    файла — это молча испорченный бэкап."""
    from job_monitor import paths
    from job_monitor.db import migrations

    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))

    conn = sqlite3.connect(paths.db_file())
    conn.execute("PRAGMA journal_mode=WAL")
    migrations.migrate(conn)          # доводим до 3
    conn.execute(
        'INSERT INTO settings (key, value) VALUES (\'app\', \'{"safe_mode": true}\')'
    )
    conn.commit()

    # Откатываем версию, чтобы миграции снова стали ожидающими.
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (2)")
    conn.commit()

    migrations.migrate(conn)

    backup = paths.db_backup_file("003")
    assert backup.exists(), "миграция не оставила копию БД"
    restored = sqlite3.connect(backup)
    restored.row_factory = sqlite3.Row
    row = restored.execute("SELECT value FROM settings WHERE key = 'app'").fetchone()
    assert row is not None and "safe_mode" in row["value"], (
        "копия не содержит данных — вероятно, скопирован только основной файл без WAL"
    )
