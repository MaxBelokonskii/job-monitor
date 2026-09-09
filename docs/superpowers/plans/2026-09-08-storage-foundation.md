# План 2: фундамент хранилища

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перевести состояние приложения с россыпи JSON/TXT/PID-файлов на SQLite со слоем репозиториев и сделать настройки единым источником правды (устраняет L7).

**Architecture:** Одна БД в каталоге данных, схема версионируется SQL-миграциями, доступ только через репозитории. Настройки приложения — pydantic-модель, лежащая одной JSON-строкой в таблице `settings`; секреты остаются исключительно в окружении и в БД не появляются. Старые файлы импортируются один раз отдельной командой CLI. HTTP-контракт API не меняется — фронтенд не трогаем.

**Tech Stack:** Python 3.13.3, `sqlite3` из стандартной библиотеки (без ORM), pydantic 2, pytest.

**Spec:** `docs/superpowers/specs/2026-09-08-hardening-and-rework-spec.md`

**Предпосылка:** план 1 выполнен целиком. Задачи ниже опираются на `job_monitor/paths.py`, `job_monitor/envfile.py` и фикстуру `client`.

## Global Constraints

- Python `>=3.11`, проверка на 3.13.3.
- Зависимости закреплены в `requirements.lock`; новых зависимостей этот план не добавляет.
- Запрещены `eval`, `exec`, `pickle`, `marshal`, `shell=True`, установка пакетов в рантайме.
- Ни один секрет не попадает в БД, в ответы API, в логи, в репозиторий.
- Сервер слушает только `127.0.0.1`.
- Новый код с аннотациями типов, тесты `pytest`, коммиты Conventional Commits.
- `make test` зелёный на каждом коммите.
- SQL — только параметризованный; конкатенация значений в запрос запрещена.

## Структура файлов

| Файл | Ответственность |
|------|-----------------|
| `job_monitor/db/connection.py` | подключение, PRAGMA, транзакции, синглтон соединения приложения |
| `job_monitor/db/migrations.py` | список версионированных миграций и раннер |
| `job_monitor/db/repositories.py` | `SettingsRepo`, `TgRepo`, `HhRepo`, `EventsRepo` |
| `job_monitor/settings.py` | `AppSettings` (модель настроек) и `secrets()` (ключи из окружения) |
| `job_monitor/legacy_import.py` | однократный импорт старых файлов |
| `job_monitor/cli.py` | точка входа: `run`, `migrate-legacy` |

---

### Задача 1: подключение к БД и миграции

**Files:**
- Create: `job_monitor/db/__init__.py`, `job_monitor/db/connection.py`, `job_monitor/db/migrations.py`, `tests/test_db.py`

**Interfaces:**
- Consumes: `job_monitor.paths.db_file`
- Produces:
  - `job_monitor.db.connection.connect(database: str | None = None) -> sqlite3.Connection` — открывает соединение, применяет PRAGMA и миграции
  - `job_monitor.db.connection.get_connection() -> sqlite3.Connection` — соединение приложения (создаётся один раз)
  - `job_monitor.db.connection.reset_connection() -> None` — сбрасывает синглтон, нужен тестам
  - `job_monitor.db.connection.transaction(conn) -> ContextManager[sqlite3.Connection]`
  - `job_monitor.db.migrations.migrate(conn) -> int` — возвращает итоговую версию схемы
  - `job_monitor.db.migrations.SCHEMA_VERSION: int`

- [ ] **Step 1: Написать падающий тест**

`tests/test_db.py`:

```python
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
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'job_monitor.db'`.

- [ ] **Step 3: Реализовать `job_monitor/db/migrations.py`**

```python
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

MIGRATIONS: list[tuple[int, str]] = [(1, MIGRATION_001)]
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
```

- [ ] **Step 4: Реализовать `job_monitor/db/connection.py`**

```python
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
```

`job_monitor/db/__init__.py` оставить пустым.

- [ ] **Step 5: Запустить тест**

Run: `.venv/bin/python -m pytest tests/test_db.py -v`
Expected: PASS, 5 passed.

- [ ] **Step 6: Закоммитить**

```bash
git add job_monitor/db/ tests/test_db.py
git commit -m "feat: add SQLite connection layer and versioned migrations"
```

---

### Задача 2: репозитории

**Files:**
- Create: `job_monitor/db/repositories.py`, `tests/test_repositories.py`

**Interfaces:**
- Consumes: `job_monitor.db.connection.transaction`
- Produces (все репозитории конструируются как `Repo(conn)`):
  - `SettingsRepo.load() -> dict`, `SettingsRepo.save(values: dict) -> None`
  - `TgRepo.was_sent(username: str) -> bool`
  - `TgRepo.record_send(username: str, channel: str | None, preview: str | None, now: datetime) -> None`
  - `TgRepo.sent_on(day: date) -> int`, `TgRepo.contacts_total() -> int`, `TgRepo.recent(limit: int) -> list[dict]`
  - `HhRepo.upsert(vacancy: dict) -> None` (ключ `vacancy_id`), `HhRepo.exists(vacancy_id: str) -> bool`
  - `HhRepo.applied_on(day: date) -> int`, `HhRepo.found_on(day: date) -> int`, `HhRepo.applied_total() -> int`, `HhRepo.recent(limit: int) -> list[dict]`
  - `EventsRepo.add(worker: str, kind: str, detail: str | None, now: datetime) -> None`, `EventsRepo.recent(worker: str, limit: int) -> list[dict]`

- [ ] **Step 1: Написать падающий тест**

`tests/test_repositories.py`:

```python
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
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_repositories.py -v`
Expected: FAIL — нет модуля `job_monitor.db.repositories`.

- [ ] **Step 3: Реализовать репозитории**

```python
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

from job_monitor.db.connection import transaction

SETTINGS_KEY = "app"


class SettingsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def load(self) -> dict:
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (SETTINGS_KEY,)
        ).fetchone()
        return json.loads(row["value"]) if row else {}

    def save(self, values: dict) -> None:
        payload = json.dumps(values, ensure_ascii=False)
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (SETTINGS_KEY, payload),
            )


class TgRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def was_sent(self, username: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM tg_contacts WHERE username = ?", (username,)
        ).fetchone()
        return row is not None

    def record_send(
        self, username: str, channel: str | None, preview: str | None, now: datetime
    ) -> None:
        stamp = now.isoformat(timespec="seconds")
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO tg_contacts (username, first_sent_at, last_sent_at,"
                " send_count, source_channel) VALUES (?, ?, ?, 1, ?)"
                " ON CONFLICT(username) DO UPDATE SET"
                " last_sent_at = excluded.last_sent_at,"
                " send_count = tg_contacts.send_count + 1",
                (username, stamp, stamp, channel),
            )
            self._conn.execute(
                "INSERT INTO tg_sends (username, sent_at, channel, preview)"
                " VALUES (?, ?, ?, ?)",
                (username, stamp, channel, preview),
            )

    def sent_on(self, day: date) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM tg_sends WHERE sent_at LIKE ?",
            (f"{day.isoformat()}%",),
        ).fetchone()
        return int(row["n"])

    def contacts_total(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM tg_contacts").fetchone()["n"])

    def recent(self, limit: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT username, sent_at, channel, preview FROM tg_sends"
            " ORDER BY sent_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


class HhRepo:
    FIELDS = ("vacancy_id", "title", "company", "salary", "city", "url",
              "found_at", "applied_at", "status", "error")

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def exists(self, vacancy_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM hh_applications WHERE vacancy_id = ?", (vacancy_id,)
        ).fetchone()
        return row is not None

    def upsert(self, vacancy: dict) -> None:
        values = {field: vacancy.get(field) for field in self.FIELDS}
        updates = ", ".join(
            f"{field} = excluded.{field}" for field in self.FIELDS if field != "vacancy_id"
        )
        with transaction(self._conn):
            self._conn.execute(
                f"INSERT INTO hh_applications ({', '.join(self.FIELDS)})"
                f" VALUES ({', '.join('?' * len(self.FIELDS))})"
                f" ON CONFLICT(vacancy_id) DO UPDATE SET {updates}",
                tuple(values[field] for field in self.FIELDS),
            )

    def applied_on(self, day: date) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM hh_applications"
            " WHERE applied_at LIKE ? AND status = 'отклик отправлен'",
            (f"{day.isoformat()}%",),
        ).fetchone()
        return int(row["n"])

    def found_on(self, day: date) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM hh_applications WHERE found_at LIKE ?",
            (f"{day.isoformat()}%",),
        ).fetchone()
        return int(row["n"])

    def applied_total(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM hh_applications WHERE status = 'отклик отправлен'"
        ).fetchone()
        return int(row["n"])

    def recent(self, limit: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM hh_applications"
            " ORDER BY COALESCE(applied_at, found_at) DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


class EventsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, worker: str, kind: str, detail: str | None, now: datetime) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO worker_events (worker, at, kind, detail) VALUES (?, ?, ?, ?)",
                (worker, now.isoformat(timespec="seconds"), kind, detail),
            )

    def recent(self, worker: str, limit: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT worker, at, kind, detail FROM worker_events"
            " WHERE worker = ? ORDER BY at DESC, id DESC LIMIT ?",
            (worker, limit),
        ).fetchall()
        return [dict(row) for row in rows]
```

Единственное место с интерполяцией в SQL — список полей `HhRepo.FIELDS`, он константа в коде; все значения идут параметрами.

- [ ] **Step 4: Запустить тест**

Run: `.venv/bin/python -m pytest tests/test_repositories.py -v`
Expected: PASS, 7 passed.

- [ ] **Step 5: Закоммитить**

```bash
git add job_monitor/db/repositories.py tests/test_repositories.py
git commit -m "feat: add settings, telegram, hh and events repositories"
```

---

### Задача 3: единый источник настроек

Устраняет L7: настройки перестают жить одновременно в `.env` и `config.json`. Прикладные настройки — в БД, секреты — только в окружении.

**Files:**
- Create: `job_monitor/settings.py`, `tests/test_settings.py`

**Interfaces:**
- Consumes: `SettingsRepo`, `job_monitor.envfile.read_env`
- Produces:
  - `job_monitor.settings.AppSettings` — pydantic-модель со всеми прикладными настройками и дефолтами
  - `job_monitor.settings.load_settings(conn) -> AppSettings`
  - `job_monitor.settings.save_settings(conn, patch: dict) -> AppSettings` — частичное обновление с валидацией
  - `job_monitor.settings.Secrets` (`api_id: int | None`, `api_hash: str | None`, `is_complete: bool`)
  - `job_monitor.settings.load_secrets() -> Secrets`

- [ ] **Step 1: Написать падающий тест**

`tests/test_settings.py`:

```python
import pytest
from pydantic import ValidationError

from job_monitor import settings as settings_module
from job_monitor.db import connection


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    connection.reset_connection()
    yield connection.connect()
    connection.reset_connection()


def test_defaults_are_returned_for_empty_db(conn):
    current = settings_module.load_settings(conn)
    assert current.max_per_day == 25
    assert "qa" in current.keywords
    assert current.safe_mode is True


def test_patch_updates_only_given_fields(conn):
    settings_module.save_settings(conn, {"max_per_day": 5})
    current = settings_module.load_settings(conn)
    assert current.max_per_day == 5
    assert current.delay_min == 60


def test_rejects_unknown_field(conn):
    with pytest.raises(ValidationError):
        settings_module.save_settings(conn, {"nonexistent": 1})


def test_rejects_out_of_range_limit(conn):
    with pytest.raises(ValidationError):
        settings_module.save_settings(conn, {"max_per_day": 0})


def test_settings_never_hold_secrets(conn):
    with pytest.raises(ValidationError):
        settings_module.save_settings(conn, {"api_hash": "deadbeef"})
    assert "api_hash" not in settings_module.load_settings(conn).model_dump()


def test_secrets_come_from_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)
    (tmp_path / ".env").write_text("TG_API_ID=42\nTG_API_HASH=abc\n", encoding="utf-8")
    secrets = settings_module.load_secrets()
    assert secrets.api_id == 42
    assert secrets.api_hash == "abc"
    assert secrets.is_complete is True


def test_secrets_incomplete_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)
    assert settings_module.load_secrets().is_complete is False
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_settings.py -v`
Expected: FAIL — нет модуля `job_monitor.settings`.

- [ ] **Step 3: Реализовать `job_monitor/settings.py`**

```python
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from job_monitor import envfile
from job_monitor.db.repositories import SettingsRepo


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Telegram
    channels: list[str] = Field(default_factory=lambda: [
        "itvacancykz", "it_interns", "jobfortester", "workitkz", "qajoboffer", "jobforqa",
    ])
    keywords: list[str] = Field(default_factory=lambda: [
        "qa", "тестировщик", "manual qa", "junior", "стажер", "стажировка",
        "intern", "trainee", "без опыта",
    ])
    exclude: list[str] = Field(default_factory=lambda: [
        "senior", "lead", "middle", "middle 3+", "middle+", "5+ лет", "6+ лет",
    ])
    template: str = ""
    delay_min: int = Field(default=60, ge=1, le=3600)
    delay_max: int = Field(default=120, ge=1, le=3600)
    max_per_day: int = Field(default=25, ge=1, le=100)
    history_limit: int = Field(default=50, ge=1, le=500)
    safe_mode: bool = True
    parse_history: bool = False
    file_path: str = ""
    tg_autostart: bool = False

    # hh.ru
    hh_keywords: list[str] = Field(default_factory=lambda: [
        "QA", "тестировщик", "Junior QA", "стажировка QA",
    ])
    hh_exclude: list[str] = Field(default_factory=lambda: ["senior", "lead", "middle", "5+ лет"])
    hh_area_ids: list[int] = Field(default_factory=lambda: [113])
    hh_salary_from: int = Field(default=0, ge=0)
    hh_cover_letter: str = ""
    hh_max_per_day: int = Field(default=20, ge=1, le=50)
    hh_delay_min: int = Field(default=30, ge=1, le=3600)
    hh_delay_max: int = Field(default=90, ge=1, le=3600)
    hh_experience: str = "noExperience"
    hh_employment: list[str] = Field(default_factory=lambda: ["full", "part", "probation"])
    hh_schedule: list[str] = Field(default_factory=lambda: ["remote", "fullDay", "flexible"])
    hh_search_period: int = Field(default=1, ge=1, le=30)
    hh_resume_id: str = ""
    hh_check_interval: int = Field(default=1800, ge=60, le=86400)
    hh_autostart: bool = False
    hh_selenium_steps: list[dict] = Field(default_factory=list)


@dataclass(frozen=True)
class Secrets:
    api_id: int | None
    api_hash: str | None

    @property
    def is_complete(self) -> bool:
        return self.api_id is not None and bool(self.api_hash)


def load_settings(conn: sqlite3.Connection) -> AppSettings:
    return AppSettings(**SettingsRepo(conn).load())


def save_settings(conn: sqlite3.Connection, patch: dict) -> AppSettings:
    repo = SettingsRepo(conn)
    merged = AppSettings(**repo.load()).model_dump()
    merged.update(patch)
    validated = AppSettings(**merged)          # extra="forbid" ловит лишние ключи
    repo.save(validated.model_dump())
    return validated


def load_secrets() -> Secrets:
    from_file = envfile.read_env()
    raw_id = os.getenv("TG_API_ID") or from_file.get("TG_API_ID") or ""
    raw_hash = os.getenv("TG_API_HASH") or from_file.get("TG_API_HASH") or ""
    api_id = int(raw_id) if raw_id.strip().isdigit() else None
    return Secrets(api_id=api_id, api_hash=raw_hash.strip() or None)
```

`extra="forbid"` — не косметика: именно она гарантирует, что `api_hash` не просочится в настройки и в БД.

- [ ] **Step 4: Запустить тест**

Run: `.venv/bin/python -m pytest tests/test_settings.py -v`
Expected: PASS, 8 passed.

- [ ] **Step 5: Закоммитить**

```bash
git add job_monitor/settings.py tests/test_settings.py
git commit -m "feat: single source of truth for settings, secrets only from env"
```

---

### Задача 4: импорт старых данных и CLI

**Files:**
- Create: `job_monitor/legacy_import.py`, `job_monitor/cli.py`, `tests/test_legacy_import.py`
- Modify: `pyproject.toml` (console script), `Makefile`

**Interfaces:**
- Consumes: `SettingsRepo`, `TgRepo`, `HhRepo`, `AppSettings`
- Produces:
  - `job_monitor.legacy_import.ImportReport` (`settings_keys: int`, `contacts: int`, `sends: int`, `vacancies: int`, `skipped: list[str]`)
  - `job_monitor.legacy_import.import_legacy(conn, source: Path) -> ImportReport`
  - `job_monitor.cli.main(argv: list[str] | None = None) -> int` — подкоманды `run` и `migrate-legacy`

- [ ] **Step 1: Написать падающий тест**

`tests/test_legacy_import.py`:

```python
import json
from datetime import date

import pytest

from job_monitor.db import connection
from job_monitor.db.repositories import HhRepo, TgRepo
from job_monitor.legacy_import import import_legacy
from job_monitor.settings import load_settings


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path / "state"))
    connection.reset_connection()
    yield connection.connect()
    connection.reset_connection()


@pytest.fixture
def legacy(tmp_path):
    source = tmp_path / "legacy"
    (source / "logs").mkdir(parents=True)
    (source / "config.json").write_text(json.dumps({
        "channels": ["itvacancykz"], "max_per_day": 7, "api_hash": "must-be-dropped",
    }), encoding="utf-8")
    (source / "all_sent_users.txt").write_text("@a\n@b\n@a\n", encoding="utf-8")
    (source / "logs" / "sent_log_2026-04-04.txt").write_text(
        "@a | 2026-04-04 10:00:00 | QA junior...\n", encoding="utf-8")
    (source / "hh_sent.json").write_text(json.dumps({
        "111": {"id": "111", "title": "QA", "company": "Acme", "url": "https://hh.ru/vacancy/111",
                "found_at": "2026-04-04 10:00", "applied_at": "2026-04-04 10:05",
                "status": "отклик отправлен"},
    }), encoding="utf-8")
    return source


def test_imports_settings_without_secrets(conn, legacy):
    report = import_legacy(conn, legacy)
    current = load_settings(conn)
    assert current.max_per_day == 7
    assert current.channels == ["itvacancykz"]
    assert "api_hash" in " ".join(report.skipped)


def test_deduplicates_contacts(conn, legacy):
    import_legacy(conn, legacy)
    repo = TgRepo(conn)
    assert repo.contacts_total() == 2      # @a встречался дважды
    assert repo.was_sent("@a") is True


def test_imports_send_history_with_dates(conn, legacy):
    import_legacy(conn, legacy)
    assert TgRepo(conn).sent_on(date(2026, 4, 4)) == 1


def test_imports_hh_applications(conn, legacy):
    import_legacy(conn, legacy)
    repo = HhRepo(conn)
    assert repo.exists("111") is True
    assert repo.applied_total() == 1


def test_is_idempotent(conn, legacy):
    import_legacy(conn, legacy)
    import_legacy(conn, legacy)
    assert TgRepo(conn).contacts_total() == 2
    assert HhRepo(conn).applied_total() == 1


def test_missing_source_reports_but_does_not_crash(conn, tmp_path):
    report = import_legacy(conn, tmp_path / "nope")
    assert report.contacts == 0
    assert report.skipped
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_legacy_import.py -v`
Expected: FAIL — нет модуля `job_monitor.legacy_import`.

- [ ] **Step 3: Реализовать `job_monitor/legacy_import.py`**

```python
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from job_monitor.db.repositories import HhRepo, TgRepo
from job_monitor.settings import AppSettings, save_settings

SECRET_KEYS = ("api_id", "api_hash")
LEGACY_STAMP = "%Y-%m-%d %H:%M"


@dataclass
class ImportReport:
    settings_keys: int = 0
    contacts: int = 0
    sends: int = 0
    vacancies: int = 0
    skipped: list[str] = field(default_factory=list)


def _parse_stamp(raw: str) -> datetime | None:
    for pattern in (LEGACY_STAMP, "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw.strip(), pattern)
        except ValueError:
            continue
    return None


def _import_settings(conn: sqlite3.Connection, source: Path, report: ImportReport) -> None:
    config = source / "config.json"
    if not config.exists():
        report.skipped.append("config.json не найден")
        return
    raw = json.loads(config.read_text(encoding="utf-8"))
    for key in SECRET_KEYS:
        if key in raw:
            raw.pop(key)
            report.skipped.append(f"config.json: {key} не переносится — секреты живут в .env")
    known = set(AppSettings.model_fields)
    patch = {key: value for key, value in raw.items() if key in known}
    for key in set(raw) - known:
        report.skipped.append(f"config.json: неизвестный ключ {key}")
    save_settings(conn, patch)
    report.settings_keys = len(patch)


def _import_contacts(conn: sqlite3.Connection, source: Path, report: ImportReport) -> None:
    repo = TgRepo(conn)
    log_files = sorted((source / "logs").glob("sent_log_*.txt")) if (source / "logs").is_dir() else []
    for log_file in log_files:
        for line in log_file.read_text(encoding="utf-8").splitlines():
            parts = [part.strip() for part in line.split("|")]
            if len(parts) < 2:
                continue
            username, stamp = parts[0], _parse_stamp(parts[1])
            preview = parts[2] if len(parts) > 2 else None
            if not username.startswith("@") or stamp is None:
                continue
            if repo.was_sent(username) and repo.sent_on(stamp.date()) > 0:
                continue
            repo.record_send(username, None, preview, stamp)
            report.sends += 1

    all_sent = source / "all_sent_users.txt"
    if all_sent.exists():
        fallback = datetime.fromtimestamp(all_sent.stat().st_mtime)
        for line in all_sent.read_text(encoding="utf-8").splitlines():
            username = line.strip()
            if username.startswith("@") and not repo.was_sent(username):
                repo.record_send(username, None, None, fallback)
                report.sends += 1
    else:
        report.skipped.append("all_sent_users.txt не найден")
    report.contacts = repo.contacts_total()


def _import_vacancies(conn: sqlite3.Connection, source: Path, report: ImportReport) -> None:
    hh_sent = source / "hh_sent.json"
    if not hh_sent.exists():
        report.skipped.append("hh_sent.json не найден")
        return
    repo = HhRepo(conn)
    for vacancy_id, raw in json.loads(hh_sent.read_text(encoding="utf-8")).items():
        found = _parse_stamp(raw.get("found_at", "")) or datetime.now()
        applied = _parse_stamp(raw.get("applied_at", ""))
        repo.upsert({
            "vacancy_id": str(vacancy_id),
            "title": raw.get("title") or "без названия",
            "company": raw.get("company"),
            "salary": raw.get("salary"),
            "city": raw.get("city"),
            "url": raw.get("url"),
            "found_at": found.isoformat(timespec="seconds"),
            "applied_at": applied.isoformat(timespec="seconds") if applied else None,
            "status": raw.get("status") or "неизвестно",
            "error": None,
        })
        report.vacancies += 1


def import_legacy(conn: sqlite3.Connection, source: Path) -> ImportReport:
    report = ImportReport()
    if not source.is_dir():
        report.skipped.append(f"каталог {source} не найден")
        return report
    _import_settings(conn, source, report)
    _import_contacts(conn, source, report)
    _import_vacancies(conn, source, report)
    return report
```

- [ ] **Step 4: Реализовать `job_monitor/cli.py`**

```python
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from job_monitor.db.connection import get_connection
from job_monitor.legacy_import import import_legacy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="job-monitor")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="запустить веб-приложение")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8000)

    migrate = sub.add_parser("migrate-legacy", help="импортировать данные старой версии")
    migrate.add_argument("--from", dest="source", type=Path, required=True)

    args = parser.parse_args(argv)

    if args.command == "run":
        import uvicorn

        if args.host != "127.0.0.1":
            print("отказ: приложение слушает только 127.0.0.1", file=sys.stderr)
            return 2
        uvicorn.run("api.main:app", host=args.host, port=args.port, reload=False)
        return 0

    report = import_legacy(get_connection(), args.source)
    print(f"настроек: {report.settings_keys}, контактов: {report.contacts}, "
          f"отправок: {report.sends}, вакансий: {report.vacancies}")
    for note in report.skipped:
        print(f"  пропущено: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Проверка `--host` — не паранойя, а ограничение из спецификации: единственный способ запустить сервер не на локальном интерфейсе должен отсутствовать.

- [ ] **Step 5: Зарегистрировать команду и цель Makefile**

В `pyproject.toml`:

```toml
[project.scripts]
job-monitor = "job_monitor.cli:main"
```

В `Makefile`:

```makefile
migrate-legacy:
	$(PY) -m job_monitor.cli migrate-legacy --from .
```

- [ ] **Step 6: Прогнать тесты**

Run: `make test`
Expected: PASS, 26 passed.

- [ ] **Step 7: Импортировать реальные данные, если они есть**

```bash
.venv/bin/python -m job_monitor.cli migrate-legacy --from ~/.job-monitor
```
Если старых файлов нет — вывод покажет пропуски, это нормально.

- [ ] **Step 8: Закоммитить**

```bash
git add job_monitor/legacy_import.py job_monitor/cli.py pyproject.toml Makefile tests/test_legacy_import.py
git commit -m "feat: import legacy json/txt state into SQLite via CLI"
```

---

### Задача 5: API и воркеры читают настройки из БД

Здесь исчезает `config.json` как источник правды. HTTP-контракт остаётся прежним, чтобы фронтенд не пришлось менять в этом плане.

**Files:**
- Modify: `api/config_routes.py` (переписать целиком), `monitor.py:23-47`, `hh_monitor.py:57-65`, `api/tg_routes.py:9`, `api/hh_routes.py:8`
- Create: `tests/test_config_api.py`

**Interfaces:**
- Consumes: `load_settings`, `save_settings`, `load_secrets`, `envfile.write_env`, `job_monitor.security.APP_TOKEN`
- Produces: контракт `/api/config` без изменений: `GET` отдаёт все поля `AppSettings` плюс `api_id` и `api_hash_set`; `PATCH` принимает частичный объект, включая `api_id`/`api_hash` (они уходят в `.env`, а не в БД).

- [ ] **Step 1: Написать падающий тест**

`tests/test_config_api.py`:

```python
import pytest

from job_monitor.db import connection
from job_monitor.security import APP_TOKEN, TOKEN_HEADER

AUTH = {TOKEN_HEADER: APP_TOKEN}


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)
    connection.reset_connection()
    yield
    connection.reset_connection()


def test_get_returns_defaults(client):
    body = client.get("/api/config", headers=AUTH).json()
    assert body["max_per_day"] == 25
    assert body["api_hash_set"] is False
    assert "api_hash" not in body


def test_patch_persists_to_database(client):
    client.patch("/api/config", json={"max_per_day": 9}, headers=AUTH)
    assert client.get("/api/config", headers=AUTH).json()["max_per_day"] == 9


def test_patch_rejects_invalid_value(client):
    response = client.patch("/api/config", json={"max_per_day": 0}, headers=AUTH)
    assert response.status_code == 422


def test_patch_stores_secrets_in_env_not_db(client, tmp_path):
    client.patch("/api/config", json={"api_id": "42", "api_hash": "abc"}, headers=AUTH)
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "TG_API_HASH=abc" in env_text
    body = client.get("/api/config", headers=AUTH).json()
    assert body["api_hash_set"] is True
    assert "api_hash" not in body


def test_config_json_is_not_created(client, tmp_path):
    client.patch("/api/config", json={"max_per_day": 9}, headers=AUTH)
    assert not (tmp_path / "config.json").exists()
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_config_api.py -v`
Expected: FAIL — `config.json` создаётся, `max_per_day: 0` принимается.

- [ ] **Step 3: Переписать `api/config_routes.py`**

```python
from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from job_monitor import envfile
from job_monitor.db.connection import get_connection
from job_monitor.settings import AppSettings, load_secrets, load_settings, save_settings

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
async def get_config() -> dict:
    current = load_settings(get_connection()).model_dump()
    secrets = load_secrets()
    current["api_id"] = str(secrets.api_id) if secrets.api_id else ""
    current["api_hash_set"] = bool(secrets.api_hash)
    return current


@router.patch("")
async def update_config(patch: dict) -> dict:
    patch = dict(patch)
    api_id = patch.pop("api_id", None)
    api_hash = patch.pop("api_hash", None)
    patch.pop("api_hash_set", None)

    secrets_to_write: dict[str, str] = {}
    if api_id:
        if not str(api_id).strip().isdigit():
            raise HTTPException(status_code=422, detail="api_id должен быть числом")
        secrets_to_write["TG_API_ID"] = str(api_id).strip()
    if api_hash and not str(api_hash).startswith("•"):
        secrets_to_write["TG_API_HASH"] = str(api_hash).strip()

    try:
        updated = save_settings(get_connection(), patch)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors(include_url=False)) from error

    if secrets_to_write:
        secrets_to_write["SAFE_MODE"] = "true" if updated.safe_mode else "false"
        secrets_to_write["PARSE_HISTORY"] = "true" if updated.parse_history else "false"
        secrets_to_write["HISTORY_LIMIT"] = str(updated.history_limit)
        envfile.write_env(secrets_to_write)
    return {"status": "saved"}


def load_config() -> dict:
    """Совместимость: tg_routes и hh_routes ждут словарь."""
    return {**load_settings(get_connection()).model_dump(),
            "api_id": load_secrets().api_id or ""}
```

Модель `ConfigUpdate` с 30 `Optional`-полями удаляется: валидацию теперь делает `AppSettings` в одном месте.

- [ ] **Step 4: Перевести воркеры на БД**

`monitor.py`: заменить `load_config` (строки 17-47) на

```python
from job_monitor.db.connection import get_connection
from job_monitor.settings import load_settings

def load_config() -> dict:
    return load_settings(get_connection()).model_dump()
```

Кеш с TTL 30 секунд удалить: чтение из SQLite стоит микросекунды, а кеш давал расхождение между тем, что показывает UI, и тем, по чему работает воркер.

`hh_monitor.py`: `HH_DEFAULTS` и `load_config` (строки 39-65) заменить на тот же вызов `load_settings`.

- [ ] **Step 5: Запустить тесты**

Run: `make test`
Expected: PASS, 31 passed.

- [ ] **Step 6: Проверить руками**

```bash
make run
```
Открыть настройки, поменять «Макс. откликов в день», сохранить, перезагрузить страницу — значение сохранилось. Проверить, что `config.json` в каталоге данных не появился и что `sqlite3 ~/.job-monitor/job_monitor.db "select * from settings"` показывает JSON без `api_hash`.

- [ ] **Step 7: Закоммитить**

```bash
git add api/config_routes.py monitor.py hh_monitor.py tests/test_config_api.py
git commit -m "refactor: read and write settings through SQLite repositories"
```

---

## Итог плана 2

Настройки читаются и пишутся через SQLite (единый источник правды, схема версионируется, секреты остались в окружении, `config.json` больше не используется — L7 закрыт). Таблицы контактов и вакансий тоже существуют и версионируются той же схемой, и `migrate-legacy` умеет наполнять их из старых файлов, — но это только слепок на момент импорта: `monitor.py` по-прежнему пишет `all_sent_users.txt`/`sent_log_*.txt`, а `hh_monitor.py` — `hh_sent.json`, ни один воркер в БД не пишет. Метрики по-прежнему считаются парсингом логов, а воркеры остаются подпроцессами — перевод воркеров на БД и удаление старых файлов это план 3.
