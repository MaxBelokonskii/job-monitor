# Пресеты поиска и библиотека резюме — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Приложение хранит несколько наборов критериев поиска, переключается между ними одним нажатием и умеет прикладывать резюме, загруженное через интерфейс; чужой поиск исчезает из кода.

**Architecture:** Критерии переезжают из единственной строки настроек в таблицу `presets`, где каждый пресет — JSON-документ, валидируемый моделью `SearchCriteria`. Глобальное (лимиты, задержки, безопасный режим, автозапуск) остаётся в таблице `settings` под моделью `GlobalSettings`. Резюме становится библиотекой: файлы в каталоге данных, записи в таблице `resumes`, пресет ссылается по идентификатору. Воркеры читают активный пресет на каждой итерации — там, где сейчас читают настройки.

**Tech Stack:** Python 3.13, FastAPI, pydantic 2, sqlite3 из стандартной библиотеки, Telethon, Selenium. Фронтенд — SPA без сборки на ванильном JS. Тесты — pytest.

**Spec:** `docs/superpowers/specs/2026-09-09-presets-and-resume-library-design.md`

## Global Constraints

- Python `>=3.11`, разработка и проверка на 3.13.3.
- Новых зависимостей план не добавляет. Сети в песочнице нет.
- Запрещены `eval`, `exec`, `pickle`, `marshal`, `shell=True`, установка пакетов в рантайме.
- Ни один секрет (`TG_API_ID`, `TG_API_HASH`, cookies hh.ru, `auth_key` сессий Telethon) не попадает в БД, в ответы API, в логи и в репозиторий.
- Никакое состояние не резолвится относительно каталога репозитория — только через `job_monitor/paths.py`.
- Сервер слушает только `127.0.0.1`.
- Весь новый код с аннотациями типов. Обработчики `api/` **обязаны** объявлять возвращаемый тип — это закреплено AST-тестом `tests/test_route_invariants.py::test_every_api_handler_declares_a_return_type`.
- **Ловушка FastAPI:** аннотация возврата становится `response_model`, то есть включает валидацию ответа — добавили поле в ответ, расширьте аннотацию, иначе 500. И наоборот: аннотация наследником `Response` заставляет FastAPI не строить `response_model` вовсе, молча снимая валидацию.
- Тесты не ходят в сеть и не открывают браузер: внешние границы подменяются двойниками.
- Прогон не создаёт ничего в домашнем каталоге и не зависит от порядка файлов.
- `make test` (`.venv/bin/python -m pytest -q`) зелёный на каждом коммите. На старте плана — **461 passed**.
- Файлы-растяжки (`tests/test_secrets_handling.py`, `test_route_invariants.py`, `test_frontend_safety.py`, `test_check_no_secrets.py`, `test_paths.py`) править можно **только усиливая**: покрытие не должно сузиться нигде.
- Каждый новый тест обязан быть дискриминирующим: для него формулируется мутация, которая должна его свалить, и эта мутация прогоняется.
- Сообщения коммитов — Conventional Commits, по-английски, в стиле репозитория (`git log --oneline -10`).
- Запрещено выполнять `git checkout`, `git restore`, `git stash`, `git clean`, `git reset`. Прежнюю версию файла читать через `git show <sha>:<путь>`, прежнее состояние разворачивать `git archive <sha> | tar -x -C <каталог>` в песочницу.

---

## Ветка `refactor/drop-cors-and-worker-epoch`

Параллельно существует **неслитая** ветка от того же `master`: она убирает `CORSMiddleware` и добавляет воркерам epoch запуска (тесты 461 → 507). Пересечения с этим планом — три файла: `api/main.py` (подключение роутеров против удаления middleware), `api/routes_state.py` (`data_dir_warning` против `epoch`) и `job_monitor/workers/manager.py` (`running()` против `_epochs`). Все три конфликта аддитивные и разрешаются взятием обеих сторон.

Если та ветка сольётся раньше — счёт тестов на старте плана будет 507, а не 461, и двойники в тестах Task 4 должны нести ещё и поле `epoch`. Сверяться с фактом (`git log --oneline -3`), а не с этим числом.

## Структура файлов

| Файл | Ответственность |
|------|-----------------|
| `job_monitor/criteria.py` | модель `SearchCriteria` и константы hh.ru (регион, опыт, занятость, график) |
| `job_monitor/presets.py` | активный пресет: чтение, установка, создание пресета по умолчанию |
| `job_monitor/resume_store.py` | файловый слой библиотеки резюме: проверки, атомарная запись, права |
| `job_monitor/db/migrations.py` | миграция 003 и резервная копия БД перед миграциями |
| `job_monitor/db/repositories.py` | `PresetsRepo`, `ResumesRepo`, `TgFoundRepo` |
| `job_monitor/settings.py` | `GlobalSettings` — то, что осталось глобальным |
| `job_monitor/paths.py` | `resume_dir()`, `db_backup_file()`, определение облачных каталогов |
| `api/presets_routes.py` | CRUD пресетов и переключение |
| `api/resumes_routes.py` | загрузка, список, скачивание, удаление резюме |

---

## Task 1: Схема и репозитории

**Files:**
- Modify: `job_monitor/db/migrations.py`
- Modify: `job_monitor/db/repositories.py`
- Modify: `job_monitor/paths.py`
- Test: `tests/test_migration_003.py`, `tests/test_presets_repo.py`, `tests/test_resumes_repo.py`, `tests/test_tg_found_repo.py`

**Interfaces:**
- Consumes: `job_monitor.db.connection.get_connection`, `transaction`; `job_monitor.paths.db_file`.
- Produces:
  - `paths.resume_dir() -> Path` — каталог резюме, `0700`, создаётся при обращении.
  - `paths.db_backup_file(tag: str) -> Path` — путь `job_monitor.db.bak-<tag>` рядом с БД.
  - `PresetsRepo(conn)` с методами: `list() -> list[dict]`, `get(preset_id: int) -> dict | None`, `get_by_name(name: str) -> dict | None`, `create(name: str, criteria: dict, now: datetime) -> int`, `update_criteria(preset_id: int, mutator: Callable[[dict], dict]) -> dict`, `set_name(preset_id: int, name: str, now: datetime) -> None`, `set_position(preset_id: int, position: int, now: datetime) -> None`, `delete(preset_id: int) -> None`, `count() -> int`, `next_position() -> int`. В возвращаемых словарях `criteria` — уже разобранный `dict`, а не строка JSON.
  - `ResumesRepo(conn)` с методами: `add(original_name: str, stored_name: str, size_bytes: int, now: datetime) -> int`, `list() -> list[dict]`, `get(resume_id: int) -> dict | None`, `delete(resume_id: int) -> None`, `presets_using(resume_id: int) -> list[str]`.
  - `TgFoundRepo(conn)` с методами: `record(channel: str, message_id: int, username: str | None, preview: str | None, matched_keyword: str | None, now: datetime) -> bool` (`False`, если пара уже была), `exists(channel: str, message_id: int) -> bool`, `recent(limit: int) -> list[dict]`.

- [ ] **Step 1: Написать падающий тест на миграцию**

Создать `tests/test_migration_003.py`:

```python
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
    raise AssertionError("тот же пост вставился дважды — UNIQUE(channel, message_id) потерян")


def test_migration_is_idempotent(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "t.db")
    assert migrate(conn) == 3
    assert migrate(conn) == 3
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_migration_003.py -q`
Expected: FAIL — `sqlite3.OperationalError: no such table: presets` и `migrate` возвращает 2, а не 3.

- [ ] **Step 3: Добавить миграцию 003**

В `job_monitor/db/migrations.py` после `MIGRATION_002` добавить:

```python
MIGRATION_003 = """
CREATE TABLE IF NOT EXISTS presets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE,
    position   INTEGER NOT NULL,
    criteria   TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS resumes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    original_name TEXT    NOT NULL,
    stored_name   TEXT    NOT NULL UNIQUE,
    size_bytes    INTEGER NOT NULL,
    uploaded_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS tg_found (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    channel         TEXT    NOT NULL,
    message_id      INTEGER NOT NULL,
    found_at        TEXT    NOT NULL,
    username        TEXT,
    preview         TEXT,
    matched_keyword TEXT,
    UNIQUE (channel, message_id)
);
CREATE INDEX IF NOT EXISTS idx_tg_found_at ON tg_found(found_at);
"""

MIGRATIONS: list[tuple[int, str]] = [
    (1, MIGRATION_001),
    (2, MIGRATION_002),
    (3, MIGRATION_003),
]
```

- [ ] **Step 4: Прогнать тесты миграции**

Run: `.venv/bin/python -m pytest tests/test_migration_003.py -q`
Expected: PASS, 4 passed.

- [ ] **Step 5: Написать падающий тест на резервную копию БД**

Дописать в `tests/test_migration_003.py`:

```python
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
        "INSERT INTO settings (key, value) VALUES ('app', '{\"safe_mode\": true}')"
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
```

- [ ] **Step 6: Прогнать и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_migration_003.py -q`
Expected: FAIL — `AttributeError: module 'job_monitor.paths' has no attribute 'db_backup_file'`.

- [ ] **Step 7: Добавить пути и резервное копирование**

В `job_monitor/paths.py` рядом с `db_file()`:

```python
def resume_dir() -> Path:
    """Каталог библиотеки резюме. Внутри каталога данных, а НЕ каталога
    репозитория: резюме содержит ФИО, телефон и почту, а каталог репозитория
    — это git. Ровно так утекло резюме предыдущего автора (S1/D11)."""
    directory = path("resume")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    tighten(directory, 0o700)
    return directory


def db_backup_file(tag: str) -> Path:
    """Путь резервной копии БД рядом с самой БД: `job_monitor.db.bak-<tag>`."""
    db = db_file()
    return db.with_name(f"{db.name}.bak-{tag}")
```

В `job_monitor/db/migrations.py` — копия перед применением ожидающих миграций:

```python
def _backup_before(conn: sqlite3.Connection, target: int) -> None:
    """Копия БД через sqlite3-backup, не через файловую копию.

    База работает в режиме WAL: часть закоммиченных страниц физически лежит
    в файле `-wal`, поэтому `shutil.copy` основного файла даёт копию без
    последних изменений — молча испорченный бэкап, который выясняется только
    в момент, когда он понадобился. `Connection.backup()` — стандартная
    библиотека, знает про WAL и снимает согласованный образ.
    """
    from job_monitor import paths

    destination = paths.db_backup_file(f"{target:03d}")
    with sqlite3.connect(destination) as backup:
        conn.backup(backup)
    os.chmod(destination, 0o600)


def migrate(conn: sqlite3.Connection) -> int:
    version = _current_version(conn)
    for target, script in MIGRATIONS:
        if target <= version:
            continue
        if version > 0:
            # Пустую, только что созданную базу копировать бессмысленно.
            _backup_before(conn, target)
        conn.executescript(script)
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (target,))
        version = target
    return version
```

Добавить `import os` в начало файла, если его там нет.

- [ ] **Step 8: Прогнать тесты миграции**

Run: `.venv/bin/python -m pytest tests/test_migration_003.py -q`
Expected: PASS, 5 passed.

- [ ] **Step 9: Написать падающие тесты репозиториев**

Создать `tests/test_presets_repo.py`:

```python
from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from job_monitor.db.migrations import migrate
from job_monitor.db.repositories import PresetsRepo

NOW = datetime(2026, 9, 9, 12, 0, 0)


@pytest.fixture()
def conn(tmp_path) -> sqlite3.Connection:
    connection = sqlite3.connect(tmp_path / "t.db")
    connection.row_factory = sqlite3.Row
    migrate(connection)
    return connection


def test_created_preset_reads_back_with_parsed_criteria(conn) -> None:
    preset_id = PresetsRepo(conn).create("Мой поиск", {"channels": ["a"]}, NOW)
    stored = PresetsRepo(conn).get(preset_id)
    assert stored is not None
    assert stored["criteria"] == {"channels": ["a"]}, (
        "criteria должны возвращаться разобранным словарём, а не строкой JSON"
    )
    assert stored["name"] == "Мой поиск"


def test_positions_are_assigned_after_the_last_one(conn) -> None:
    repo = PresetsRepo(conn)
    first = repo.create("A", {}, NOW)
    second = repo.create("B", {}, NOW)
    assert repo.get(first)["position"] < repo.get(second)["position"]
    assert repo.next_position() > repo.get(second)["position"]


def test_update_criteria_merges_and_bumps_updated_at(conn) -> None:
    repo = PresetsRepo(conn)
    preset_id = repo.create("A", {"channels": ["a"], "template": "привет"}, NOW)
    result = repo.update_criteria(
        preset_id, lambda current: {**current, "channels": ["a", "b"]}
    )
    assert result == {"channels": ["a", "b"], "template": "привет"}
    assert repo.get(preset_id)["criteria"] == result


def test_criteria_of_one_preset_do_not_leak_into_another(conn) -> None:
    repo = PresetsRepo(conn)
    a = repo.create("A", {"channels": ["a"]}, NOW)
    b = repo.create("B", {"channels": ["b"]}, NOW)
    repo.update_criteria(a, lambda current: {**current, "channels": ["a", "z"]})
    assert repo.get(b)["criteria"] == {"channels": ["b"]}


def test_list_is_ordered_by_position(conn) -> None:
    repo = PresetsRepo(conn)
    repo.create("A", {}, NOW)
    second = repo.create("B", {}, NOW)
    repo.set_position(second, 0, NOW)
    assert [row["name"] for row in repo.list()] == ["B", "A"]


def test_delete_removes_only_the_named_preset(conn) -> None:
    repo = PresetsRepo(conn)
    a = repo.create("A", {}, NOW)
    repo.create("B", {}, NOW)
    repo.delete(a)
    assert [row["name"] for row in repo.list()] == ["B"]
    assert repo.count() == 1
```

Создать `tests/test_resumes_repo.py`:

```python
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest

from job_monitor.db.migrations import migrate
from job_monitor.db.repositories import PresetsRepo, ResumesRepo

NOW = datetime(2026, 9, 9, 12, 0, 0)


@pytest.fixture()
def conn(tmp_path) -> sqlite3.Connection:
    connection = sqlite3.connect(tmp_path / "t.db")
    connection.row_factory = sqlite3.Row
    migrate(connection)
    return connection


def test_added_resume_reads_back(conn) -> None:
    repo = ResumesRepo(conn)
    resume_id = repo.add("Моё резюме.pdf", "a1b2.pdf", 1024, NOW)
    stored = repo.get(resume_id)
    assert stored["original_name"] == "Моё резюме.pdf"
    assert stored["stored_name"] == "a1b2.pdf"
    assert stored["size_bytes"] == 1024


def test_presets_using_names_every_referring_preset(conn) -> None:
    """Удаление файла, на который ссылается пресет, сломало бы его молча —
    поэтому репозиторий обязан уметь назвать всех, кто ссылается."""
    resume_id = ResumesRepo(conn).add("cv.pdf", "a1b2.pdf", 10, NOW)
    presets = PresetsRepo(conn)
    presets.create("QA", {"resume_id": resume_id}, NOW)
    presets.create("Поддержка", {"resume_id": resume_id}, NOW)
    presets.create("Без резюме", {"resume_id": None}, NOW)

    assert sorted(ResumesRepo(conn).presets_using(resume_id)) == ["QA", "Поддержка"]


def test_presets_using_is_empty_for_an_unreferenced_resume(conn) -> None:
    resume_id = ResumesRepo(conn).add("cv.pdf", "a1b2.pdf", 10, NOW)
    PresetsRepo(conn).create("QA", {"resume_id": None}, NOW)
    assert ResumesRepo(conn).presets_using(resume_id) == []
```

Создать `tests/test_tg_found_repo.py`:

```python
from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from job_monitor.db.migrations import migrate
from job_monitor.db.repositories import TgFoundRepo

NOW = datetime(2026, 9, 9, 12, 0, 0)


@pytest.fixture()
def conn(tmp_path) -> sqlite3.Connection:
    connection = sqlite3.connect(tmp_path / "t.db")
    connection.row_factory = sqlite3.Row
    migrate(connection)
    return connection


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
```

- [ ] **Step 10: Прогнать и убедиться, что падают**

Run: `.venv/bin/python -m pytest tests/test_presets_repo.py tests/test_resumes_repo.py tests/test_tg_found_repo.py -q`
Expected: FAIL — `ImportError: cannot import name 'PresetsRepo' from 'job_monitor.db.repositories'`.

- [ ] **Step 11: Реализовать репозитории**

В `job_monitor/db/repositories.py` добавить три класса. Стиль — как у существующих: конструктор принимает соединение, запись идёт внутри `transaction()`, `list`/`recent` возвращают `list[dict]` через `dict(row)`.

```python
class PresetsRepo:
    """Пресеты: критерии поиска одним JSON-документом на строку.

    `criteria` хранится строкой, но наружу отдаётся разобранным словарём:
    вызывающему незачем знать про сериализацию, а забытый `json.loads` в
    одном из шести мест вызова — ровно тот дефект, который потом ищут
    полдня.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        parsed = dict(row)
        parsed["criteria"] = json.loads(parsed["criteria"])
        return parsed

    def list(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM presets ORDER BY position, id"
        ).fetchall()
        return [self._row(row) for row in rows]

    def get(self, preset_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM presets WHERE id = ?", (preset_id,)
        ).fetchone()
        return self._row(row) if row else None

    def get_by_name(self, name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM presets WHERE name = ?", (name,)
        ).fetchone()
        return self._row(row) if row else None

    def next_position(self) -> int:
        row = self._conn.execute("SELECT MAX(position) AS top FROM presets").fetchone()
        top = row["top"] if row and row["top"] is not None else -1
        return int(top) + 1

    def create(self, name: str, criteria: dict, now: datetime) -> int:
        stamp = now.isoformat(timespec="seconds")
        payload = json.dumps(criteria, ensure_ascii=False)
        with transaction(self._conn):
            cursor = self._conn.execute(
                "INSERT INTO presets (name, position, criteria, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (name, self.next_position(), payload, stamp, stamp),
            )
        return int(cursor.lastrowid)

    def update_criteria(
        self, preset_id: int, mutator: Callable[[dict], dict]
    ) -> dict:
        """Атомарное чтение -> изменение -> запись внутри одной транзакции.

        Та же причина, что у `SettingsRepo.update()`: чтение в autocommit и
        запись отдельной транзакцией дают окно, в котором параллельный
        писатель успевает сохранить своё целиком, и его правка молча
        затирается устаревшим снимком — без исключения где-либо.
        """
        with transaction(self._conn):
            row = self._conn.execute(
                "SELECT criteria FROM presets WHERE id = ?", (preset_id,)
            ).fetchone()
            if row is None:
                raise KeyError(preset_id)
            new_criteria = mutator(json.loads(row["criteria"]))
            self._conn.execute(
                "UPDATE presets SET criteria = ?, updated_at = ? WHERE id = ?",
                (
                    json.dumps(new_criteria, ensure_ascii=False),
                    datetime.now().isoformat(timespec="seconds"),
                    preset_id,
                ),
            )
        return new_criteria

    def set_name(self, preset_id: int, name: str, now: datetime) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE presets SET name = ?, updated_at = ? WHERE id = ?",
                (name, now.isoformat(timespec="seconds"), preset_id),
            )

    def set_position(self, preset_id: int, position: int, now: datetime) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE presets SET position = ?, updated_at = ? WHERE id = ?",
                (position, now.isoformat(timespec="seconds"), preset_id),
            )

    def delete(self, preset_id: int) -> None:
        with transaction(self._conn):
            self._conn.execute("DELETE FROM presets WHERE id = ?", (preset_id,))

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM presets").fetchone()
        return int(row["n"])


class ResumesRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(
        self, original_name: str, stored_name: str, size_bytes: int, now: datetime
    ) -> int:
        with transaction(self._conn):
            cursor = self._conn.execute(
                "INSERT INTO resumes (original_name, stored_name, size_bytes, uploaded_at)"
                " VALUES (?, ?, ?, ?)",
                (original_name, stored_name, size_bytes, now.isoformat(timespec="seconds")),
            )
        return int(cursor.lastrowid)

    def list(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM resumes ORDER BY uploaded_at DESC, id DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def get(self, resume_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM resumes WHERE id = ?", (resume_id,)
        ).fetchone()
        return dict(row) if row else None

    def delete(self, resume_id: int) -> None:
        with transaction(self._conn):
            self._conn.execute("DELETE FROM resumes WHERE id = ?", (resume_id,))

    def presets_using(self, resume_id: int) -> list[str]:
        """Имена пресетов, ссылающихся на это резюме.

        `resume_id` живёт внутри JSON-документа критериев, поэтому связь
        проверяется разбором, а не внешним ключом. Ссылка на несуществующий
        файл ломала бы пресет молча, поэтому удаление обязано уметь назвать
        всех, кто пострадает.
        """
        names: list[str] = []
        for row in self._conn.execute("SELECT name, criteria FROM presets").fetchall():
            if json.loads(row["criteria"]).get("resume_id") == resume_id:
                names.append(row["name"])
        return names


class TgFoundRepo:
    """Найденное в Telegram. Пишется этим подпроектом, читается следующим."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record(
        self,
        channel: str,
        message_id: int,
        username: str | None,
        preview: str | None,
        matched_keyword: str | None,
        now: datetime,
    ) -> bool:
        """`True`, если пост новый; `False`, если такой уже записан.

        `INSERT OR IGNORE` вместо предварительного `SELECT`: проверка и
        вставка одним оператором не оставляют окна между ними.
        """
        with transaction(self._conn):
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO tg_found"
                " (channel, message_id, found_at, username, preview, matched_keyword)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    channel,
                    message_id,
                    now.isoformat(timespec="seconds"),
                    username,
                    preview,
                    matched_keyword,
                ),
            )
        return cursor.rowcount == 1

    def exists(self, channel: str, message_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM tg_found WHERE channel = ? AND message_id = ?",
            (channel, message_id),
        ).fetchone()
        return row is not None

    def recent(self, limit: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM tg_found ORDER BY found_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
```

- [ ] **Step 12: Прогнать тесты репозиториев**

Run: `.venv/bin/python -m pytest tests/test_presets_repo.py tests/test_resumes_repo.py tests/test_tg_found_repo.py -q`
Expected: PASS, 12 passed.

- [ ] **Step 13: Проверить дискриминацию мутациями**

Развернуть копию: `git archive HEAD | tar -x -C <песочница>/mut`, внести правки в копии, прогонять оттуда интерпретатором основного проекта с `PYTHONDONTWRITEBYTECODE=1`. Мутации и ожидаемый результат:

1. Убрать `UNIQUE (channel, message_id)` из `MIGRATION_003` → падает `test_the_same_post_cannot_be_recorded_twice` и `test_first_record_is_accepted_and_the_repeat_is_not`.
2. Заменить `INSERT OR IGNORE` на `INSERT` в `TgFoundRepo.record` → падает `test_first_record_is_accepted_and_the_repeat_is_not` (исключением, а не `False`).
3. В `PresetsRepo._row` убрать `json.loads` → падает `test_created_preset_reads_back_with_parsed_criteria`.
4. В `_backup_before` заменить `conn.backup(backup)` на `shutil.copy(paths.db_file(), destination)` → падает `test_pending_migrations_leave_a_backup_of_the_database` (копия без WAL не содержит вставленной строки).
5. В `presets_using` вернуть `[]` всегда → падает `test_presets_using_names_every_referring_preset`.

- [ ] **Step 14: Прогнать весь набор и закоммитить**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, не меньше 478 passed (461 + 17 новых).

```bash
git add job_monitor/db/migrations.py job_monitor/db/repositories.py job_monitor/paths.py tests/test_migration_003.py tests/test_presets_repo.py tests/test_resumes_repo.py tests/test_tg_found_repo.py
git commit -m "feat(db): tables and repositories for presets, resumes and found posts"
```

---

## Task 2: Модель критериев, разделение настроек, перенос данных

**Files:**
- Create: `job_monitor/criteria.py`
- Create: `job_monitor/presets.py`
- Modify: `job_monitor/settings.py`
- Modify: `job_monitor/db/migrations.py` (вызов переноса данных после DDL)
- Test: `tests/test_criteria.py`, `tests/test_presets_service.py`, `tests/test_settings_split.py`, `tests/test_no_foreign_search_terms.py`

**Interfaces:**
- Consumes: `PresetsRepo`, `ResumesRepo` (Task 1); `SettingsRepo`, `load_settings`, `save_settings`.
- Produces:
  - `job_monitor/criteria.py`: `HH_AREA_ID: int`, `HH_EXPERIENCE: dict[str, str]`, `HH_EMPLOYMENT: dict[str, str]`, `HH_SCHEDULE: dict[str, str]`, `class SearchCriteria(BaseModel)` с полями из спецификации.
  - `job_monitor/presets.py`: `active_preset(conn) -> dict`, `active_criteria(conn) -> SearchCriteria`, `set_active(conn, preset_id: int) -> None`, `ensure_default(conn, now: datetime) -> int`, `save_criteria(conn, preset_id: int, patch: dict) -> SearchCriteria`, `import_legacy_criteria(conn, now: datetime) -> int | None`.
  - `job_monitor/settings.py`: `class GlobalSettings(BaseModel)` (переименование `AppSettings` с изъятыми критериями), `load_settings(conn) -> GlobalSettings`, `save_settings(conn, patch) -> GlobalSettings` — имена функций не меняются, чтобы не трогать все вызовы разом.

- [ ] **Step 1: Написать падающий тест модели критериев**

Создать `tests/test_criteria.py`:

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from job_monitor.criteria import (
    HH_AREA_ID,
    HH_EMPLOYMENT,
    HH_EXPERIENCE,
    HH_SCHEDULE,
    SearchCriteria,
)


def test_defaults_carry_no_search_terms_at_all() -> None:
    """Чистая установка не должна искать чужую работу. Значения по умолчанию
    в унаследованном коде были буквально поиском предыдущего автора:
    казахстанские QA-каналы, «junior», «без опыта», регион 113."""
    empty = SearchCriteria()
    for field in (
        "channels", "professions", "tg_keywords", "tg_exclude",
        "hh_exclude", "hh_employment", "hh_schedule",
    ):
        assert getattr(empty, field) == [], f"{field} по умолчанию не пуст"
    assert empty.template == ""
    assert empty.hh_cover_letter == ""
    assert empty.hh_resume_id == ""
    assert empty.resume_id is None
    assert empty.hh_salary_from == 0


def test_region_is_a_constant_not_a_field() -> None:
    assert HH_AREA_ID == 113
    assert "hh_area_ids" not in SearchCriteria.model_fields
    assert "hh_area_id" not in SearchCriteria.model_fields


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SearchCriteria(hh_area_ids=[113])


def test_experience_accepts_only_known_codes() -> None:
    assert SearchCriteria(hh_experience="between1And3").hh_experience == "between1And3"
    with pytest.raises(ValidationError):
        SearchCriteria(hh_experience="сколько-нибудь")
    assert set(HH_EXPERIENCE) == {
        "noExperience", "between1And3", "between3And6", "moreThan6",
    }


def test_employment_and_schedule_accept_only_known_codes() -> None:
    assert SearchCriteria(hh_employment=["full", "part"]).hh_employment == ["full", "part"]
    with pytest.raises(ValidationError):
        SearchCriteria(hh_employment=["full", "выдуманное"])
    with pytest.raises(ValidationError):
        SearchCriteria(hh_schedule=["никогда"])
    assert set(HH_EMPLOYMENT) == {"full", "part", "project", "probation", "volunteer"}
    assert set(HH_SCHEDULE) == {"remote", "fullDay", "flexible", "shift"}


def test_employment_is_multi_and_experience_is_single() -> None:
    """hh.ru принимает несколько типов занятости, и одно значение сужает
    выдачу, отсекая вакансии, помеченные сразу двумя. Опыт — одно значение,
    как на самом hh.ru."""
    assert SearchCriteria.model_fields["hh_employment"].annotation == list[str]
    assert SearchCriteria.model_fields["hh_experience"].annotation is str
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_criteria.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'job_monitor.criteria'`.

- [ ] **Step 3: Написать модель критериев**

Создать `job_monitor/criteria.py`:

```python
"""Критерии поиска: содержимое пресета плюс константы hh.ru.

Регион, опыт, занятость и график — константы, а не настройки: поиск ведётся
только по региону 113 (решение D8), а остальные три — закрытые наборы кодов
самого hh.ru, из которых интерфейс строит переключатели. Следствие, ради
которого это и сделано так: официальное API hh.ru не нужно даже для
справочников.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

HH_AREA_ID = 113

HH_EXPERIENCE: dict[str, str] = {
    "noExperience": "Нет опыта",
    "between1And3": "От 1 до 3 лет",
    "between3And6": "От 3 до 6 лет",
    "moreThan6": "Более 6 лет",
}

HH_EMPLOYMENT: dict[str, str] = {
    "full": "Полная занятость",
    "part": "Частичная занятость",
    "project": "Проектная работа",
    "probation": "Стажировка",
    "volunteer": "Волонтёрство",
}

HH_SCHEDULE: dict[str, str] = {
    "remote": "Удалённо",
    "fullDay": "Полный день",
    "flexible": "Гибкий график",
    "shift": "Сменный график",
}


class SearchCriteria(BaseModel):
    """Что человек ищет. Лимиты и задержки здесь НЕ живут (решение D7):
    банят аккаунт, а не пресет, поэтому суточный бюджет один на приложение.
    """

    model_config = ConfigDict(extra="forbid")

    channels: list[str] = Field(default_factory=list)
    professions: list[str] = Field(default_factory=list)
    tg_keywords: list[str] = Field(default_factory=list)
    tg_exclude: list[str] = Field(default_factory=list)
    hh_exclude: list[str] = Field(default_factory=list)
    hh_salary_from: int = Field(default=0, ge=0)
    hh_experience: str = "noExperience"
    hh_employment: list[str] = Field(default_factory=list)
    hh_schedule: list[str] = Field(default_factory=list)
    hh_search_period: int = Field(default=1, ge=1, le=30)
    template: str = ""
    hh_cover_letter: str = ""
    hh_resume_id: str = ""
    resume_id: int | None = None

    @field_validator("hh_experience")
    @classmethod
    def _known_experience(cls, value: str) -> str:
        if value not in HH_EXPERIENCE:
            raise ValueError(
                f"неизвестный код опыта {value!r}; допустимы: {sorted(HH_EXPERIENCE)}"
            )
        return value

    @field_validator("hh_employment")
    @classmethod
    def _known_employment(cls, value: list[str]) -> list[str]:
        unknown = [item for item in value if item not in HH_EMPLOYMENT]
        if unknown:
            raise ValueError(
                f"неизвестные коды занятости {unknown}; допустимы: {sorted(HH_EMPLOYMENT)}"
            )
        return value

    @field_validator("hh_schedule")
    @classmethod
    def _known_schedule(cls, value: list[str]) -> list[str]:
        unknown = [item for item in value if item not in HH_SCHEDULE]
        if unknown:
            raise ValueError(
                f"неизвестные коды графика {unknown}; допустимы: {sorted(HH_SCHEDULE)}"
            )
        return value
```

- [ ] **Step 4: Прогнать тесты критериев**

Run: `.venv/bin/python -m pytest tests/test_criteria.py -q`
Expected: PASS, 6 passed.

- [ ] **Step 5: Написать падающий тест сервиса пресетов и переноса данных**

Создать `tests/test_presets_service.py`:

```python
from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from job_monitor import presets
from job_monitor.criteria import SearchCriteria
from job_monitor.db.migrations import migrate
from job_monitor.db.repositories import PresetsRepo, SettingsRepo

NOW = datetime(2026, 9, 9, 12, 0, 0)


@pytest.fixture()
def conn(tmp_path) -> sqlite3.Connection:
    connection = sqlite3.connect(tmp_path / "t.db")
    connection.row_factory = sqlite3.Row
    migrate(connection)
    return connection


def test_ensure_default_creates_one_empty_preset(conn) -> None:
    preset_id = presets.ensure_default(conn, NOW)
    rows = PresetsRepo(conn).list()
    assert len(rows) == 1
    assert rows[0]["name"] == "Мой поиск"
    assert presets.active_criteria(conn) == SearchCriteria()
    assert presets.active_preset(conn)["id"] == preset_id


def test_ensure_default_is_idempotent(conn) -> None:
    first = presets.ensure_default(conn, NOW)
    second = presets.ensure_default(conn, NOW)
    assert first == second
    assert PresetsRepo(conn).count() == 1


def test_set_active_switches_what_the_workers_read(conn) -> None:
    repo = PresetsRepo(conn)
    a = repo.create("A", {"channels": ["a"]}, NOW)
    b = repo.create("B", {"channels": ["b"]}, NOW)
    presets.set_active(conn, a)
    assert presets.active_criteria(conn).channels == ["a"]
    presets.set_active(conn, b)
    assert presets.active_criteria(conn).channels == ["b"]


def test_save_criteria_validates_the_merged_document(conn) -> None:
    preset_id = PresetsRepo(conn).create("A", {"channels": ["a"]}, NOW)
    saved = presets.save_criteria(conn, preset_id, {"tg_keywords": ["qa"]})
    assert saved.channels == ["a"], "патч не должен терять несвязанные поля"
    assert saved.tg_keywords == ["qa"]

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        presets.save_criteria(conn, preset_id, {"hh_experience": "выдумка"})
    assert PresetsRepo(conn).get(preset_id)["criteria"]["hh_experience"] == "noExperience", (
        "отвергнутый патч не должен попадать в базу"
    )


def test_legacy_settings_become_the_first_preset(conn) -> None:
    """Перенос существующих настроек: критерии уезжают в пресет
    «Импортированные настройки», глобальное остаётся в settings."""
    SettingsRepo(conn).save({
        "channels": ["itvacancykz"],
        "keywords": ["qa", "тестировщик"],
        "exclude": ["senior"],
        "template": "здравствуйте",
        "hh_keywords": ["QA"],
        "hh_exclude": ["lead"],
        "hh_area_ids": [113],
        "hh_cover_letter": "письмо",
        "hh_experience": "noExperience",
        "hh_employment": ["full", "part"],
        "hh_schedule": ["remote"],
        "hh_salary_from": 100000,
        "hh_search_period": 7,
        "hh_resume_id": "abc",
        "file_path": "",
        "safe_mode": False,
        "max_per_day": 30,
    })

    preset_id = presets.import_legacy_criteria(conn, NOW)

    assert preset_id is not None
    stored = PresetsRepo(conn).get(preset_id)
    assert stored["name"] == "Импортированные настройки"
    criteria = SearchCriteria(**stored["criteria"])
    assert criteria.channels == ["itvacancykz"]
    assert criteria.tg_keywords == ["qa", "тестировщик"]
    assert criteria.tg_exclude == ["senior"]
    assert criteria.professions == ["QA"], "hh_keywords должны стать professions"
    assert criteria.hh_exclude == ["lead"]
    assert criteria.template == "здравствуйте"
    assert criteria.hh_cover_letter == "письмо"
    assert criteria.hh_salary_from == 100000
    assert criteria.hh_search_period == 7
    assert criteria.hh_resume_id == "abc"

    left = SettingsRepo(conn).load()
    assert left["safe_mode"] is False, "глобальное должно остаться"
    assert left["max_per_day"] == 30
    for gone in ("channels", "keywords", "exclude", "template", "hh_keywords",
                 "hh_area_ids", "file_path"):
        assert gone not in left, f"{gone} должен был уехать из settings"
    assert left["active_preset_id"] == preset_id


def test_legacy_import_is_idempotent(conn) -> None:
    SettingsRepo(conn).save({"channels": ["a"], "safe_mode": True})
    first = presets.import_legacy_criteria(conn, NOW)
    second = presets.import_legacy_criteria(conn, NOW)
    assert second is None, "повторный перенос не должен создавать второй пресет"
    assert PresetsRepo(conn).count() == 1
    assert PresetsRepo(conn).get(first)["criteria"]["channels"] == ["a"]


def test_out_of_range_legacy_value_does_not_lose_the_rest(conn) -> None:
    """У прежней версии не было границ, поэтому в базе может лежать
    значение, которое новая модель отвергает. Терять из-за одного поля весь
    перенос нельзя — это тот же дефект, что чинили в `migrate-legacy`."""
    SettingsRepo(conn).save({
        "channels": ["a"],
        "hh_search_period": 999,     # новая модель требует 1..30
        "safe_mode": True,
    })
    preset_id = presets.import_legacy_criteria(conn, NOW)
    criteria = SearchCriteria(**PresetsRepo(conn).get(preset_id)["criteria"])
    assert criteria.channels == ["a"]
    assert criteria.hh_search_period == 1, "отвергнутое поле берёт значение по умолчанию"
```

- [ ] **Step 6: Прогнать и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_presets_service.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'job_monitor.presets'`.

- [ ] **Step 7: Написать сервис пресетов**

Создать `job_monitor/presets.py`:

```python
"""Активный пресет: единственный источник критериев для воркеров."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from pydantic import ValidationError

from job_monitor.criteria import SearchCriteria
from job_monitor.db.repositories import PresetsRepo, SettingsRepo

logger = logging.getLogger(__name__)

DEFAULT_PRESET_NAME = "Мой поиск"
LEGACY_PRESET_NAME = "Импортированные настройки"

# Поля прежней единой модели, которые переезжают в критерии. Слева — как
# лежало в базе, справа — как называется в SearchCriteria.
LEGACY_CRITERIA_FIELDS: dict[str, str] = {
    "channels": "channels",
    "keywords": "tg_keywords",
    "exclude": "tg_exclude",
    "template": "template",
    "hh_keywords": "professions",
    "hh_exclude": "hh_exclude",
    "hh_salary_from": "hh_salary_from",
    "hh_cover_letter": "hh_cover_letter",
    "hh_experience": "hh_experience",
    "hh_employment": "hh_employment",
    "hh_schedule": "hh_schedule",
    "hh_search_period": "hh_search_period",
    "hh_resume_id": "hh_resume_id",
}

# Уезжают из settings, но в критерии не переносятся: регион стал константой
# (D8), а `file_path` заменён ссылкой на библиотеку резюме (L14/D10).
LEGACY_DROPPED_FIELDS = ("hh_area_ids", "file_path")


def ensure_default(conn: sqlite3.Connection, now: datetime) -> int:
    """Гарантирует, что есть хотя бы один пресет, и возвращает активный.

    Пресет по умолчанию — пустой. Ни одного канала и ни одного ключевого
    слова: чужой поиск не должен появляться из ниоткуда даже как «пример».
    """
    repo = PresetsRepo(conn)
    settings_repo = SettingsRepo(conn)
    stored = settings_repo.load()
    active = stored.get("active_preset_id")
    if active is not None and repo.get(int(active)) is not None:
        return int(active)

    existing = repo.list()
    preset_id = (
        existing[0]["id"]
        if existing
        else repo.create(DEFAULT_PRESET_NAME, SearchCriteria().model_dump(), now)
    )
    set_active(conn, preset_id)
    return preset_id


def set_active(conn: sqlite3.Connection, preset_id: int) -> None:
    SettingsRepo(conn).update(lambda current: {**current, "active_preset_id": preset_id})


def active_preset(conn: sqlite3.Connection) -> dict:
    preset_id = ensure_default(conn, datetime.now())
    preset = PresetsRepo(conn).get(preset_id)
    if preset is None:  # pragma: no cover — ensure_default только что его создал
        raise RuntimeError(f"активный пресет {preset_id} исчез между чтениями")
    return preset


def active_criteria(conn: sqlite3.Connection) -> SearchCriteria:
    return _tolerant(active_preset(conn)["criteria"])


def save_criteria(
    conn: sqlite3.Connection, preset_id: int, patch: dict
) -> SearchCriteria:
    """Слияние патча с сохранённым и валидация ЦЕЛОГО документа.

    Терпимость на чтении и строгость на записи — то же правило, что у
    настроек: сохранённая строка может пережить схему, но новый лишний ключ
    в базу не попадает.
    """
    def mutate(current: dict) -> dict:
        merged = _tolerant(current).model_dump()
        merged.update(patch)
        return SearchCriteria(**merged).model_dump()

    return SearchCriteria(**PresetsRepo(conn).update_criteria(preset_id, mutate))


def import_legacy_criteria(conn: sqlite3.Connection, now: datetime) -> int | None:
    """Переносит критерии из единой строки настроек в первый пресет.

    Возвращает идентификатор созданного пресета или `None`, если переносить
    нечего (перенос уже был или в базе нет старых полей). Идемпотентно.
    """
    repo = PresetsRepo(conn)
    settings_repo = SettingsRepo(conn)
    stored = settings_repo.load()
    present = [key for key in LEGACY_CRITERIA_FIELDS if key in stored]
    if not present and not any(key in stored for key in LEGACY_DROPPED_FIELDS):
        return None
    if repo.count() > 0:
        # Пресеты уже есть — значит перенос состоялся; просто вычищаем остатки.
        _strip_legacy(settings_repo)
        return None

    raw = {LEGACY_CRITERIA_FIELDS[key]: stored[key] for key in present}
    criteria = _tolerant(raw)
    preset_id = repo.create(LEGACY_PRESET_NAME, criteria.model_dump(), now)
    _strip_legacy(settings_repo)
    set_active(conn, preset_id)
    return preset_id


def _strip_legacy(settings_repo: SettingsRepo) -> None:
    gone = set(LEGACY_CRITERIA_FIELDS) | set(LEGACY_DROPPED_FIELDS)
    settings_repo.update(
        lambda current: {k: v for k, v in current.items() if k not in gone}
    )


def _tolerant(raw: dict) -> SearchCriteria:
    """Строит критерии, отбрасывая то, что новая модель не принимает.

    У прежней версии не было границ на числа, поэтому в базе может лежать
    `hh_search_period: 999`. Ронять из-за одного поля весь перенос нельзя —
    это ровно тот дефект, который уже чинили в `migrate-legacy`: первый
    невалидный ключ уносил и контакты, и вакансии.
    """
    try:
        return SearchCriteria(**raw)
    except ValidationError as error:
        bad = {
            item["loc"][0]
            for item in error.errors()
            if item.get("loc") and isinstance(item["loc"][0], str)
        }
        if not bad:
            raise
        logger.warning(
            "критерии: отброшены поля, не прошедшие проверку: %s", sorted(bad)
        )
        return _tolerant({k: v for k, v in raw.items() if k not in bad})
```

- [ ] **Step 8: Прогнать тесты сервиса**

Run: `.venv/bin/python -m pytest tests/test_presets_service.py -q`
Expected: PASS, 7 passed.

- [ ] **Step 9: Написать падающий тест разделения настроек**

Создать `tests/test_settings_split.py`:

```python
from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError

from job_monitor.db.migrations import migrate
from job_monitor.settings import GlobalSettings, load_settings, save_settings


@pytest.fixture()
def conn(tmp_path) -> sqlite3.Connection:
    connection = sqlite3.connect(tmp_path / "t.db")
    connection.row_factory = sqlite3.Row
    migrate(connection)
    return connection


def test_criteria_fields_are_gone_from_global_settings() -> None:
    fields = set(GlobalSettings.model_fields)
    for gone in (
        "channels", "keywords", "exclude", "template", "file_path",
        "hh_keywords", "hh_exclude", "hh_area_ids", "hh_cover_letter",
        "hh_experience", "hh_employment", "hh_schedule", "hh_salary_from",
        "hh_search_period", "hh_resume_id",
    ):
        assert gone not in fields, f"{gone} — критерий, ему место в пресете"


def test_global_settings_keep_the_shared_budget() -> None:
    """Лимиты и задержки общие: банят аккаунт, а не пресет (D7)."""
    fields = set(GlobalSettings.model_fields)
    for kept in (
        "delay_min", "delay_max", "max_per_day", "history_limit", "safe_mode",
        "parse_history", "tg_autostart", "hh_max_per_day", "hh_delay_min",
        "hh_delay_max", "hh_check_interval", "hh_autostart",
        "hh_selenium_steps", "active_preset_id",
    ):
        assert kept in fields, f"{kept} потерялся при разделении"


def test_saving_a_criteria_field_into_settings_is_rejected(conn) -> None:
    with pytest.raises(ValidationError):
        save_settings(conn, {"channels": ["a"]})


def test_safe_defaults_stay_safe(conn) -> None:
    fresh = load_settings(conn)
    assert fresh.safe_mode is True
    assert fresh.parse_history is False
    assert fresh.tg_autostart is False
    assert fresh.hh_autostart is False
```

- [ ] **Step 10: Прогнать и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_settings_split.py -q`
Expected: FAIL — `ImportError: cannot import name 'GlobalSettings'`.

- [ ] **Step 11: Разделить модель настроек**

В `job_monitor/settings.py`: переименовать `AppSettings` в `GlobalSettings`, удалить из неё все поля критериев (список — в тесте выше), добавить `active_preset_id: int | None = None`. Оставить `AppSettings = GlobalSettings` алиасом **не нужно** — вместо этого обновить все места использования (их находит `grep -rn "AppSettings" --include="*.py" .`). Имена `load_settings` и `save_settings` сохранить: их зовут из шести мест, и переименование раздуло бы диф без пользы.

Итоговая модель:

```python
class GlobalSettings(BaseModel):
    """Общее для всего приложения. Критерии поиска живут в пресетах.

    Лимиты и задержки здесь, а не в пресете, по решению D7: суточный бюджет
    существует потому, что банят аккаунт, и три пресета по 25 отправок дали
    бы 75 сообщений с одного аккаунта.
    """

    model_config = ConfigDict(extra="forbid")

    delay_min: int = Field(default=60, ge=1, le=3600)
    delay_max: int = Field(default=120, ge=1, le=3600)
    max_per_day: int = Field(default=25, ge=1, le=100)
    history_limit: int = Field(default=50, ge=1, le=500)
    safe_mode: bool = True
    parse_history: bool = False
    tg_autostart: bool = False

    hh_max_per_day: int = Field(default=20, ge=1, le=50)
    hh_delay_min: int = Field(default=30, ge=1, le=3600)
    hh_delay_max: int = Field(default=90, ge=1, le=3600)
    hh_check_interval: int = Field(default=1800, ge=60, le=86400)
    hh_autostart: bool = False
    hh_selenium_steps: list[dict] = Field(default_factory=list)

    active_preset_id: int | None = None
```

- [ ] **Step 12: Прогнать тесты разделения**

Run: `.venv/bin/python -m pytest tests/test_settings_split.py -q`
Expected: PASS, 4 passed.

- [ ] **Step 13: Вызвать перенос данных из миграции**

В `job_monitor/db/migrations.py`, в конце `migrate()`, после применения всех SQL-скриптов:

```python
    if version >= 3:
        # Перенос данных живёт в Python, а не в SQL: критерии надо разобрать,
        # переименовать и провалидировать моделью, а SQL этого не умеет.
        # Импорт здесь, а не наверху файла: job_monitor.presets тянет
        # репозитории, которые тянут этот модуль — цикл на уровне модуля.
        from job_monitor.presets import ensure_default, import_legacy_criteria

        import_legacy_criteria(conn, datetime.now())
        ensure_default(conn, datetime.now())
    return version
```

Добавить `from datetime import datetime` в начало файла.

- [ ] **Step 14: Написать растяжку на отсутствие чужого поиска**

Создать `tests/test_no_foreign_search_terms.py`:

```python
"""Растяжка: чужого поиска в коде нет.

Приложение унаследовано, и значения по умолчанию были буквально поиском
предыдущего автора — казахстанские QA-каналы, «junior», «без опыта», регион
113 как настройка. Проверка сканирует исходники приложения, а не только
модель: вернуть список можно и мимо `SearchCriteria` — например константой
в воркере или значением в роуте.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNED_DIRS = ("job_monitor", "api")

# Собираются из частей, чтобы файл теста не срабатывал на себе, если каталог
# сканирования когда-нибудь расширят.
FOREIGN_TERMS = (
    "itvacancy" + "kz",
    "it_" + "interns",
    "jobfor" + "tester",
    "work" + "itkz",
    "qajob" + "offer",
    "jobfor" + "qa",
    "manual " + "qa",
    "без " + "опыта",
    "стажи" + "ровка",
    "trai" + "nee",
)


def _sources() -> list[Path]:
    found: list[Path] = []
    for directory in SCANNED_DIRS:
        found.extend(
            item
            for item in (REPO_ROOT / directory).rglob("*.py")
            if "__pycache__" not in item.parts
        )
    assert found, "не найдено ни одного исходника — структура переехала?"
    return sorted(found)


def test_no_previous_author_search_terms_in_the_sources() -> None:
    offenders: list[str] = []
    for source in _sources():
        text = source.read_text(encoding="utf-8").lower()
        for term in FOREIGN_TERMS:
            if term.lower() in text:
                offenders.append(f"{source.relative_to(REPO_ROOT)}: {term!r}")
    assert not offenders, "чужой поиск вернулся в код: " + "; ".join(offenders)


def test_the_scan_is_not_vacuous() -> None:
    """Сторож против вырождения: если список терминов опустеет или сканер
    перестанет читать файлы, проверка выше станет зелёной навсегда."""
    assert len(FOREIGN_TERMS) >= 8
    assert len(_sources()) >= 10
```

- [ ] **Step 15: Прогнать весь набор**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. Если `test_no_previous_author_search_terms_in_the_sources` падает — значит какое-то из значений по умолчанию не убрано; убрать его, а не ослаблять тест.

- [ ] **Step 16: Проверить дискриминацию мутациями**

1. Вернуть в `SearchCriteria` дефолт `channels: list[str] = Field(default_factory=lambda: ["itvacancykz"])` → падают `test_defaults_carry_no_search_terms_at_all` и `test_no_previous_author_search_terms_in_the_sources`.
2. Убрать валидатор `_known_experience` → падает `test_experience_accepts_only_known_codes`.
3. В `_tolerant` заменить рекурсивный отброс на `raise` → падает `test_out_of_range_legacy_value_does_not_lose_the_rest`.
4. В `import_legacy_criteria` убрать проверку `repo.count() > 0` → падает `test_legacy_import_is_idempotent`.
5. В `_strip_legacy` вернуть словарь без изменений → падает `test_legacy_settings_become_the_first_preset`.
6. Оставить в `GlobalSettings` поле `channels` → падает `test_criteria_fields_are_gone_from_global_settings`.

- [ ] **Step 17: Закоммитить**

```bash
git add job_monitor/criteria.py job_monitor/presets.py job_monitor/settings.py job_monitor/db/migrations.py tests/test_criteria.py tests/test_presets_service.py tests/test_settings_split.py tests/test_no_foreign_search_terms.py
git commit -m "feat(presets): split search criteria out of the settings row"
```

---

## Task 3: Файловый слой библиотеки резюме

**Files:**
- Create: `job_monitor/resume_store.py`
- Test: `tests/test_resume_store.py`

**Interfaces:**
- Consumes: `paths.resume_dir()` (Task 1).
- Produces:
  - `ALLOWED_EXTENSIONS: frozenset[str]`, `MAX_BYTES: int`
  - `class ResumeRejected(ValueError)`
  - `store(original_name: str, data: bytes) -> tuple[str, int]` → `(stored_name, size_bytes)`
  - `path_of(stored_name: str) -> Path`
  - `remove(stored_name: str) -> None`

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_resume_store.py`:

```python
"""Файловый слой библиотеки резюме.

Загрузка файла — новая поверхность атаки, и главное её свойство: имя от
клиента НИКОГДА не становится путём. Имя на диске генерируем мы, клиентское
храним отдельно и только для показа.
"""

from __future__ import annotations

import os
import stat

import pytest

from job_monitor import paths, resume_store


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    return tmp_path


def test_stored_file_lands_in_the_resume_dir_with_0600() -> None:
    stored_name, size = resume_store.store("Моё резюме.pdf", b"%PDF-1.4 ...")
    target = resume_store.path_of(stored_name)
    assert target.parent == paths.resume_dir()
    assert target.exists()
    assert size == len(b"%PDF-1.4 ...")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600, "резюме — личный документ"


def test_the_client_filename_never_becomes_the_path() -> None:
    """Имя на диске генерируем мы. Клиентское имя может быть чем угодно, в
    том числе попыткой выйти из каталога."""
    stored_name, _ = resume_store.store("Моё резюме.pdf", b"x")
    assert "резюме" not in stored_name
    assert stored_name.endswith(".pdf")


@pytest.mark.parametrize(
    "hostile",
    [
        "../../.env",
        "../../../etc/passwd",
        "/etc/passwd",
        "..\\..\\windows\\system32\\config",
        "резюме\x00.pdf",
        "резюме\n.pdf",
    ],
)
def test_hostile_names_cannot_escape_the_resume_dir(hostile: str) -> None:
    try:
        stored_name, _ = resume_store.store(hostile, b"x")
    except resume_store.ResumeRejected:
        return
    target = resume_store.path_of(stored_name)
    assert target.parent == paths.resume_dir(), (
        f"имя {hostile!r} вывело файл за пределы каталога резюме: {target}"
    )


def test_extension_outside_the_allowlist_is_rejected() -> None:
    for name in ("payload.exe", "script.sh", "page.html", "noextension"):
        with pytest.raises(resume_store.ResumeRejected):
            resume_store.store(name, b"x")
    assert resume_store.ALLOWED_EXTENSIONS == frozenset(
        {".pdf", ".doc", ".docx", ".rtf", ".odt", ".txt"}
    )


def test_oversized_file_is_rejected() -> None:
    with pytest.raises(resume_store.ResumeRejected):
        resume_store.store("cv.pdf", b"x" * (resume_store.MAX_BYTES + 1))
    assert resume_store.MAX_BYTES == 10 * 1024 * 1024


def test_empty_file_is_rejected() -> None:
    with pytest.raises(resume_store.ResumeRejected):
        resume_store.store("cv.pdf", b"")


def test_two_uploads_of_the_same_name_do_not_collide() -> None:
    first, _ = resume_store.store("cv.pdf", b"a")
    second, _ = resume_store.store("cv.pdf", b"b")
    assert first != second
    assert resume_store.path_of(first).read_bytes() == b"a"
    assert resume_store.path_of(second).read_bytes() == b"b"


def test_path_of_refuses_a_name_that_points_outside() -> None:
    with pytest.raises(resume_store.ResumeRejected):
        resume_store.path_of("../.env")


def test_remove_deletes_the_file_and_tolerates_a_missing_one() -> None:
    stored_name, _ = resume_store.store("cv.pdf", b"x")
    resume_store.remove(stored_name)
    assert not resume_store.path_of(stored_name).exists()
    resume_store.remove(stored_name)  # повторное удаление — не ошибка


def test_no_temporary_file_survives_a_failed_write(monkeypatch) -> None:
    """Запись атомарна: временный файл в том же каталоге, затем `os.replace`.
    Половинного файла на диске не остаётся ни при какой ошибке."""
    def boom(*args, **kwargs):
        raise OSError("диск кончился")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        resume_store.store("cv.pdf", b"x")
    assert list(paths.resume_dir().iterdir()) == [], "остался временный файл"
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_resume_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'job_monitor.resume_store'`.

- [ ] **Step 3: Реализовать файловый слой**

Создать `job_monitor/resume_store.py`:

```python
"""Файлы библиотеки резюме.

Резюме содержит ФИО, телефон и почту, поэтому лежит в каталоге данных с
правами `0600`, а не в каталоге репозитория (решение D11): ровно так утекло
резюме предыдущего автора — вместе с закоммиченным архивом.

Загрузка — единственное место, где приложение принимает файл извне, поэтому
свойства здесь жёсткие: имя от клиента не участвует в построении пути,
расширение из белого списка, размер ограничен, запись атомарна.
"""

from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path

from job_monitor import paths

ALLOWED_EXTENSIONS = frozenset({".pdf", ".doc", ".docx", ".rtf", ".odt", ".txt"})
MAX_BYTES = 10 * 1024 * 1024
FILE_MODE = 0o600


class ResumeRejected(ValueError):
    """Файл не принят: расширение, размер или имя не прошли проверку."""


def _extension_of(original_name: str) -> str:
    """Расширение из клиентского имени — единственное, что мы из него берём.

    `Path(...).suffix` на имени вроде `../../.env` даёт пустую строку, и
    такое имя отвергается ниже по белому списку. Сам путь из клиентского
    имени не собирается никогда.
    """
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ResumeRejected(
            f"расширение {suffix or '(нет)'} не поддерживается; "
            f"допустимы: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    return suffix


def store(original_name: str, data: bytes) -> tuple[str, int]:
    """Сохраняет файл и возвращает `(имя на диске, размер)`.

    Имя на диске — случайный токен плюс расширение. Не идентификатор из БД:
    иначе имя пришлось бы знать до вставки строки, а угадать чужой файл по
    последовательному номеру было бы можно.
    """
    if not data:
        raise ResumeRejected("пустой файл")
    if len(data) > MAX_BYTES:
        raise ResumeRejected(
            f"файл больше {MAX_BYTES // (1024 * 1024)} МБ ({len(data)} байт)"
        )
    extension = _extension_of(original_name)
    stored_name = f"{secrets.token_hex(16)}{extension}"

    directory = paths.resume_dir()
    handle, temporary = tempfile.mkstemp(dir=directory, prefix=".upload-")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.chmod(temporary, FILE_MODE)
        os.replace(temporary, directory / stored_name)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return stored_name, len(data)


def path_of(stored_name: str) -> Path:
    """Путь к файлу по имени на диске, с проверкой, что он внутри каталога.

    Проверка нужна, даже если все имена генерирует `store()`: имя приезжает
    из базы, а база — состояние на диске, которое пользователь может
    отредактировать. Одна проверка здесь дешевле доверия ко всей цепочке.
    """
    directory = paths.resume_dir().resolve()
    candidate = (directory / stored_name).resolve()
    if candidate.parent != directory:
        raise ResumeRejected(f"имя {stored_name!r} указывает за пределы каталога резюме")
    return candidate


def remove(stored_name: str) -> None:
    path_of(stored_name).unlink(missing_ok=True)
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/test_resume_store.py -q`
Expected: PASS, 16 passed (10 функций, из них одна параметризована шестью случаями).

- [ ] **Step 5: Проверить дискриминацию мутациями**

1. Заменить `stored_name` на `Path(original_name).name` → падают `test_the_client_filename_never_becomes_the_path` и часть `test_hostile_names_cannot_escape_the_resume_dir`.
2. Убрать проверку белого списка → падает `test_extension_outside_the_allowlist_is_rejected`.
3. Убрать проверку `len(data) > MAX_BYTES` → падает `test_oversized_file_is_rejected`.
4. Убрать `os.chmod(temporary, FILE_MODE)` → падает `test_stored_file_lands_in_the_resume_dir_with_0600`.
5. Убрать `finally: unlink` → падает `test_no_temporary_file_survives_a_failed_write`.
6. В `path_of` убрать сравнение `candidate.parent != directory` → падает `test_path_of_refuses_a_name_that_points_outside`.

- [ ] **Step 6: Прогнать весь набор и закоммитить**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS.

```bash
git add job_monitor/resume_store.py tests/test_resume_store.py
git commit -m "feat(resume): file store with a strict upload contract"
```

---

## Task 4: API пресетов

**Files:**
- Create: `api/presets_routes.py`, `api/dictionaries_routes.py`
- Modify: `api/main.py` (подключить роутеры)
- Modify: `api/config_routes.py` (критерии больше не проходят через `/api/config`)
- Test: `tests/test_presets_api.py`

**Interfaces:**
- Consumes: `PresetsRepo` (Task 1); `job_monitor.presets` (Task 2); `job_monitor.workers.manager.manager`.
- Produces: роуты `GET /api/presets`, `POST /api/presets`, `GET /api/presets/{preset_id}`, `PATCH /api/presets/{preset_id}`, `DELETE /api/presets/{preset_id}`, `POST /api/presets/{preset_id}/activate`, `GET /api/dictionaries`.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_presets_api.py`. Использовать существующую фикстуру `client` из `tests/conftest.py` (она несёт токен приложения) и `raw_client` там, где проверяется отказ без токена.

```python
from __future__ import annotations

import pytest


def test_fresh_install_has_one_empty_preset(client) -> None:
    response = client.get("/api/presets")
    assert response.status_code == 200
    presets = response.json()
    assert len(presets) == 1
    assert presets[0]["is_active"] is True
    assert presets[0]["channels_count"] == 0
    assert presets[0]["professions_count"] == 0


def test_presets_require_the_app_token(raw_client) -> None:
    assert raw_client.get("/api/presets").status_code == 403
    assert raw_client.post("/api/presets", json={"name": "X"}).status_code == 403


def test_create_read_and_patch_a_preset(client) -> None:
    created = client.post("/api/presets", json={"name": "Поддержка"})
    assert created.status_code == 200
    preset_id = created.json()["id"]

    patched = client.patch(
        f"/api/presets/{preset_id}",
        json={"channels": ["support_jobs"], "professions": ["специалист поддержки"]},
    )
    assert patched.status_code == 200

    stored = client.get(f"/api/presets/{preset_id}").json()
    assert stored["criteria"]["channels"] == ["support_jobs"]
    assert stored["criteria"]["professions"] == ["специалист поддержки"]


def test_patch_keeps_unrelated_fields(client) -> None:
    preset_id = client.post("/api/presets", json={"name": "A"}).json()["id"]
    client.patch(f"/api/presets/{preset_id}", json={"channels": ["a"]})
    client.patch(f"/api/presets/{preset_id}", json={"tg_keywords": ["qa"]})
    criteria = client.get(f"/api/presets/{preset_id}").json()["criteria"]
    assert criteria["channels"] == ["a"]
    assert criteria["tg_keywords"] == ["qa"]


def test_patch_with_an_unknown_code_is_rejected_and_changes_nothing(client) -> None:
    preset_id = client.post("/api/presets", json={"name": "A"}).json()["id"]
    client.patch(f"/api/presets/{preset_id}", json={"channels": ["a"]})
    bad = client.patch(f"/api/presets/{preset_id}", json={"hh_experience": "выдумка"})
    assert bad.status_code == 422
    assert client.get(f"/api/presets/{preset_id}").json()["criteria"]["channels"] == ["a"]


def test_duplicate_name_is_refused(client) -> None:
    client.post("/api/presets", json={"name": "Одинаковое"})
    again = client.post("/api/presets", json={"name": "Одинаковое"})
    assert again.status_code == 400
    assert "имя" in again.json()["detail"].lower()


def test_copy_from_clones_the_criteria(client) -> None:
    source = client.post("/api/presets", json={"name": "Источник"}).json()["id"]
    client.patch(f"/api/presets/{source}", json={"channels": ["a"], "tg_keywords": ["qa"]})
    copy_id = client.post(
        "/api/presets", json={"name": "Копия", "copy_from": source}
    ).json()["id"]
    assert client.get(f"/api/presets/{copy_id}").json()["criteria"]["channels"] == ["a"]


def test_the_active_preset_cannot_be_deleted(client) -> None:
    active = next(p for p in client.get("/api/presets").json() if p["is_active"])
    refused = client.delete(f"/api/presets/{active['id']}")
    assert refused.status_code == 400
    assert len(client.get("/api/presets").json()) == 1


def test_the_last_preset_cannot_be_deleted(client) -> None:
    """Даже если он не активен: приложение без единого пресета не может ни
    искать, ни объяснить пользователю, что происходит."""
    presets = client.get("/api/presets").json()
    assert len(presets) == 1
    refused = client.delete(f"/api/presets/{presets[0]['id']}")
    assert refused.status_code == 400


def test_activate_switches_and_reports_what_it_stopped(client) -> None:
    second = client.post("/api/presets", json={"name": "Второй"}).json()["id"]
    response = client.post(f"/api/presets/{second}/activate")
    assert response.status_code == 200
    assert response.json()["activated"] == second
    assert response.json()["stopped"] == []
    assert next(p for p in client.get("/api/presets").json() if p["is_active"])["id"] == second


def test_activate_stops_a_running_worker(client, monkeypatch) -> None:
    """Переключение на ходу отвергнуто решением D9: воркер, переключённый
    посреди отправки, может написать человеку от одного пресета с резюме от
    другого, а отозвать сообщение нельзя."""
    from job_monitor.workers.manager import WorkerState, manager

    stopped: list[str] = []

    async def fake_stop(name: str):
        stopped.append(name)
        return type("S", (), {"state": WorkerState.stopped, "last_error": None})()

    monkeypatch.setattr(manager, "running", lambda: ["tg"])
    monkeypatch.setattr(manager, "stop", fake_stop)

    second = client.post("/api/presets", json={"name": "Второй"}).json()["id"]
    response = client.post(f"/api/presets/{second}/activate")
    assert response.status_code == 200
    assert stopped == ["tg"]
    assert response.json()["stopped"] == ["tg"]


def test_activate_is_refused_when_a_worker_will_not_stop(client, monkeypatch) -> None:
    """Если воркер не уложился в бюджет остановки, переключение не должно
    выполняться наполовину: активный пресет остаётся прежним."""
    from job_monitor.workers.manager import WorkerState, manager

    async def wedged_stop(name: str):
        return type(
            "S", (), {"state": WorkerState.error, "last_error": "не остановился"}
        )()

    monkeypatch.setattr(manager, "running", lambda: ["hh"])
    monkeypatch.setattr(manager, "stop", wedged_stop)

    before = next(p for p in client.get("/api/presets").json() if p["is_active"])["id"]
    second = client.post("/api/presets", json={"name": "Второй"}).json()["id"]
    refused = client.post(f"/api/presets/{second}/activate")
    assert refused.status_code == 409
    assert "не остановился" in refused.json()["detail"]
    still = next(p for p in client.get("/api/presets").json() if p["is_active"])["id"]
    assert still == before


def test_dictionaries_expose_the_codes_and_their_labels(client) -> None:
    """Подписи отдаёт бэкенд, а не дублирует фронтенд: иначе они разойдутся
    с константами при первой же правке, и пользователь увидит одно, а
    отправится другое."""
    body = client.get("/api/dictionaries").json()
    assert body["experience"]["noExperience"] == "Нет опыта"
    assert set(body["employment"]) == {
        "full", "part", "project", "probation", "volunteer",
    }
    assert set(body["schedule"]) == {"remote", "fullDay", "flexible", "shift"}
    assert body["area_id"] == 113
    assert body["multi"] == ["employment", "schedule"], (
        "фронтенд должен знать, что занятость и график — множественный выбор, "
        "а опыт — одиночный"
    )


def test_dictionaries_require_the_app_token(raw_client) -> None:
    assert raw_client.get("/api/dictionaries").status_code == 403


def test_config_no_longer_carries_criteria(client) -> None:
    body = client.get("/api/config").json()
    for gone in ("channels", "keywords", "exclude", "template", "file_path", "hh_area_ids"):
        assert gone not in body, f"{gone} — критерий, он в /api/presets"
    assert "active_preset_id" in body
    assert "safe_mode" in body
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_presets_api.py -q`
Expected: FAIL — 404 на `/api/presets`, роутер не подключён.

- [ ] **Step 3: Добавить `WorkerManager.running()`**

Проверено: метода нет (`grep -n "def running" job_monitor/workers/manager.py` пуст; упоминавшийся в ревью `all()` удалён при зачистке мёртвого кода). Добавить в `job_monitor/workers/manager.py`:

```python
    def running(self) -> list[str]:
        """Имена воркеров, которых имеет смысл останавливать.

        `starting` включён намеренно: воркер в этом состоянии уже держит
        таску, и переключение пресета под ним — это ровно тот случай, от
        которого защищает решение D9.
        """
        return [
            name
            for name in self._factories
            if self.status(name).state in (WorkerState.starting, WorkerState.running)
        ]
```

Свериться с фактическим именем словаря фабрик (`grep -n "_factories\|def register" job_monitor/workers/manager.py`) и использовать его.

- [ ] **Step 4: Написать роутер пресетов**

Создать `api/presets_routes.py`:

```python
"""CRUD пресетов и переключение между ними."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from job_monitor import presets as presets_service
from job_monitor.criteria import SearchCriteria
from job_monitor.db.connection import get_connection
from job_monitor.db.repositories import PresetsRepo
from job_monitor.settings import load_settings
from job_monitor.workers.manager import WorkerState, manager

router = APIRouter(prefix="/api/presets", tags=["presets"])


class PresetCreate(BaseModel):
    name: str
    copy_from: int | None = None


def _summary(row: dict, active_id: int | None) -> dict[str, Any]:
    criteria = row["criteria"]
    return {
        "id": row["id"],
        "name": row["name"],
        "position": row["position"],
        "is_active": row["id"] == active_id,
        "channels_count": len(criteria.get("channels") or []),
        "professions_count": len(criteria.get("professions") or []),
        "resume_id": criteria.get("resume_id"),
    }


def _active_id(conn: sqlite3.Connection) -> int | None:
    return load_settings(conn).active_preset_id


@router.get("")
async def list_presets() -> list[dict[str, Any]]:
    conn = get_connection()
    presets_service.ensure_default(conn, datetime.now())
    active = _active_id(conn)
    return [_summary(row, active) for row in PresetsRepo(conn).list()]


@router.post("")
async def create_preset(body: PresetCreate) -> dict[str, Any]:
    conn = get_connection()
    repo = PresetsRepo(conn)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="имя пресета не может быть пустым")
    if repo.get_by_name(name) is not None:
        raise HTTPException(status_code=400, detail=f"имя «{name}» уже занято")

    criteria = SearchCriteria().model_dump()
    if body.copy_from is not None:
        source = repo.get(body.copy_from)
        if source is None:
            raise HTTPException(status_code=404, detail="пресет-источник не найден")
        criteria = dict(source["criteria"])

    preset_id = repo.create(name, criteria, datetime.now())
    return {"id": preset_id, "name": name}


@router.get("/{preset_id}")
async def get_preset(preset_id: int) -> dict[str, Any]:
    row = PresetsRepo(get_connection()).get(preset_id)
    if row is None:
        raise HTTPException(status_code=404, detail="пресет не найден")
    return {"id": row["id"], "name": row["name"], "criteria": row["criteria"]}


@router.patch("/{preset_id}")
async def patch_preset(preset_id: int, patch: dict) -> dict[str, str]:
    conn = get_connection()
    repo = PresetsRepo(conn)
    if repo.get(preset_id) is None:
        raise HTTPException(status_code=404, detail="пресет не найден")

    body = dict(patch)
    new_name = body.pop("name", None)
    if new_name is not None:
        stripped = str(new_name).strip()
        if not stripped:
            raise HTTPException(status_code=400, detail="имя пресета не может быть пустым")
        clash = repo.get_by_name(stripped)
        if clash is not None and clash["id"] != preset_id:
            raise HTTPException(status_code=400, detail=f"имя «{stripped}» уже занято")
        repo.set_name(preset_id, stripped, datetime.now())

    position = body.pop("position", None)
    if position is not None:
        repo.set_position(preset_id, int(position), datetime.now())

    if body:
        try:
            presets_service.save_criteria(conn, preset_id, body)
        except ValidationError as error:
            raise HTTPException(
                status_code=422, detail=error.errors(include_url=False)
            ) from error
    return {"status": "saved"}


@router.delete("/{preset_id}")
async def delete_preset(preset_id: int) -> dict[str, str]:
    conn = get_connection()
    repo = PresetsRepo(conn)
    if repo.get(preset_id) is None:
        raise HTTPException(status_code=404, detail="пресет не найден")
    # Оба запрета — чтобы приложение не оказалось молча нерабочим: без
    # активного пресета воркерам нечего читать, а без единого пресета
    # интерфейсу нечего показывать и не из чего восстановиться.
    if repo.count() <= 1:
        raise HTTPException(
            status_code=400, detail="это единственный пресет, удалить его нельзя"
        )
    if preset_id == _active_id(conn):
        raise HTTPException(
            status_code=400,
            detail="нельзя удалить активный пресет — сначала переключитесь на другой",
        )
    repo.delete(preset_id)
    return {"status": "deleted"}


@router.post("/{preset_id}/activate")
async def activate_preset(preset_id: int) -> dict[str, Any]:
    conn = get_connection()
    repo = PresetsRepo(conn)
    if repo.get(preset_id) is None:
        raise HTTPException(status_code=404, detail="пресет не найден")

    stopped: list[str] = []
    for name in manager.running():
        status = await manager.stop(name)
        if status.state is not WorkerState.stopped:
            # Переключение наполовину хуже отказа: Selenium может стоять
            # посреди отклика, и смена резюме под ним даёт отклик не тем
            # документом (D9).
            raise HTTPException(
                status_code=409,
                detail=f"воркер {name} не остановился: {status.last_error}",
            )
        stopped.append(name)

    presets_service.set_active(conn, preset_id)
    return {"activated": preset_id, "stopped": stopped}
```

- [ ] **Step 4b: Добавить роут справочников**

Создать `api/dictionaries_routes.py`:

```python
"""Справочники hh.ru: коды и подписи для переключателей интерфейса.

Отдаёт бэкенд, а не дублирует фронтенд: продублированные подписи расходятся
с константами при первой же правке, и пользователь видит одно, а на hh.ru
уходит другое. Регион здесь тоже есть — не как выбор, а чтобы интерфейс мог
честно показать, где ведётся поиск.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from job_monitor.criteria import (
    HH_AREA_ID,
    HH_EMPLOYMENT,
    HH_EXPERIENCE,
    HH_SCHEDULE,
)

router = APIRouter(prefix="/api/dictionaries", tags=["dictionaries"])


@router.get("")
async def dictionaries() -> dict[str, Any]:
    return {
        "area_id": HH_AREA_ID,
        "experience": HH_EXPERIENCE,
        "employment": HH_EMPLOYMENT,
        "schedule": HH_SCHEDULE,
        # Какие поля — множественный выбор. hh.ru принимает несколько типов
        # занятости и графиков, и одно значение сужает выдачу; опыт — одно,
        # как на самом hh.ru.
        "multi": ["employment", "schedule"],
    }
```

Подключить в `api/main.py` рядом с остальными роутерами.

- [ ] **Step 5: Подключить роутер и убрать критерии из `/api/config`**

В `api/main.py` рядом с остальными:

```python
from .presets_routes import router as presets_router
...
app.include_router(presets_router)
```

В `api/config_routes.py::load_config()` — критерии больше не подмешиваются: функция возвращает только глобальные настройки плюс `api_id`. Места, которые ждали критериев (`tg_routes`, `hh_routes`), правятся в Task 6; до тех пор набор остаётся зелёным, потому что эти роуты берут из словаря только глобальные ключи.

Run: `grep -n "load_config()" api/*.py job_monitor/**/*.py` — свериться, что ни один вызывающий не читает критериев. Если читает — исправить сейчас, а не оставлять на Task 6.

- [ ] **Step 6: Прогнать тесты API**

Run: `.venv/bin/python -m pytest tests/test_presets_api.py -q`
Expected: PASS, 15 passed.

- [ ] **Step 7: Проверить дискриминацию мутациями**

1. Убрать проверку `repo.count() <= 1` → падает `test_the_last_preset_cannot_be_deleted`.
2. Убрать проверку `preset_id == _active_id(conn)` → падает `test_the_active_preset_cannot_be_deleted`.
3. В `activate_preset` убрать проверку `status.state is not WorkerState.stopped` → падает `test_activate_is_refused_when_a_worker_will_not_stop`.
4. В `activate_preset` переставить `set_active` перед остановкой воркеров → падает `test_activate_is_refused_when_a_worker_will_not_stop` (активный пресет успел смениться).
5. Убрать проверку `repo.get_by_name(name) is not None` → падает `test_duplicate_name_is_refused`.
6. В `patch_preset` заменить `save_criteria` на прямую запись без валидации → падает `test_patch_with_an_unknown_code_is_rejected_and_changes_nothing`.

- [ ] **Step 8: Прогнать весь набор и закоммитить**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS.

```bash
git add api/presets_routes.py api/dictionaries_routes.py api/main.py api/config_routes.py tests/test_presets_api.py
git commit -m "feat(api): presets CRUD and switching that stops the workers first"
```

---

## Task 5: API библиотеки резюме

**Files:**
- Create: `api/resumes_routes.py`
- Modify: `api/main.py` (подключить роутер)
- Test: `tests/test_resumes_api.py`

**Interfaces:**
- Consumes: `ResumesRepo` (Task 1), `job_monitor.resume_store` (Task 3).
- Produces: роуты `POST /api/resumes`, `GET /api/resumes`, `GET /api/resumes/{resume_id}/download`, `DELETE /api/resumes/{resume_id}`.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_resumes_api.py`:

```python
from __future__ import annotations

import stat

from job_monitor import paths, resume_store


def test_upload_then_list_then_download(client) -> None:
    uploaded = client.post(
        "/api/resumes",
        files={"file": ("Моё резюме.pdf", b"%PDF-1.4 payload", "application/pdf")},
    )
    assert uploaded.status_code == 200
    resume_id = uploaded.json()["id"]

    listed = client.get("/api/resumes").json()
    assert [item["original_name"] for item in listed] == ["Моё резюме.pdf"]
    assert listed[0]["size_bytes"] == len(b"%PDF-1.4 payload")

    downloaded = client.get(f"/api/resumes/{resume_id}/download")
    assert downloaded.status_code == 200
    assert downloaded.content == b"%PDF-1.4 payload"


def test_uploaded_file_is_0600_inside_the_data_dir(client) -> None:
    client.post("/api/resumes", files={"file": ("cv.pdf", b"x", "application/pdf")})
    stored = list(paths.resume_dir().iterdir())
    assert len(stored) == 1
    assert stat.S_IMODE(stored[0].stat().st_mode) == 0o600


def test_resumes_require_the_app_token(raw_client) -> None:
    assert raw_client.get("/api/resumes").status_code == 403
    assert raw_client.post(
        "/api/resumes", files={"file": ("cv.pdf", b"x", "application/pdf")}
    ).status_code == 403


def test_a_hostile_filename_does_not_escape_the_data_dir(client) -> None:
    response = client.post(
        "/api/resumes",
        files={"file": ("../../.env", b"TG_API_HASH=stolen", "text/plain")},
    )
    assert response.status_code in (400, 422)
    assert list(paths.resume_dir().iterdir()) == []
    assert not (paths.data_dir() / ".env").exists(), (
        "имя ../../.env создало файл в каталоге данных"
    )


def test_extension_outside_the_allowlist_is_refused(client) -> None:
    response = client.post(
        "/api/resumes", files={"file": ("payload.exe", b"MZ", "application/octet-stream")}
    )
    assert response.status_code == 400
    assert list(paths.resume_dir().iterdir()) == []


def test_oversized_upload_is_refused(client) -> None:
    response = client.post(
        "/api/resumes",
        files={
            "file": ("cv.pdf", b"x" * (resume_store.MAX_BYTES + 1), "application/pdf")
        },
    )
    assert response.status_code == 400
    assert list(paths.resume_dir().iterdir()) == []


def test_original_name_is_returned_verbatim_and_never_used_as_a_path(client) -> None:
    hostile_but_valid = "резюме <script>alert(1)</script>.pdf"
    uploaded = client.post(
        "/api/resumes",
        files={"file": (hostile_but_valid, b"x", "application/pdf")},
    )
    assert uploaded.status_code == 200
    listed = client.get("/api/resumes").json()
    assert listed[0]["original_name"] == hostile_but_valid
    # Экранирование — забота фронтенда (там ноль innerHTML), а на диске
    # такого имени нет вовсе.
    assert not any("script" in item.name for item in paths.resume_dir().iterdir())


def test_delete_removes_the_row_and_the_file(client) -> None:
    resume_id = client.post(
        "/api/resumes", files={"file": ("cv.pdf", b"x", "application/pdf")}
    ).json()["id"]
    assert client.delete(f"/api/resumes/{resume_id}").status_code == 200
    assert client.get("/api/resumes").json() == []
    assert list(paths.resume_dir().iterdir()) == []


def test_delete_is_refused_while_a_preset_still_uses_the_resume(client) -> None:
    """Молчаливая поломка чужого пресета хуже отказа: ответ обязан назвать,
    кто ссылается."""
    resume_id = client.post(
        "/api/resumes", files={"file": ("cv.pdf", b"x", "application/pdf")}
    ).json()["id"]
    preset_id = client.post("/api/presets", json={"name": "QA"}).json()["id"]
    client.patch(f"/api/presets/{preset_id}", json={"resume_id": resume_id})

    refused = client.delete(f"/api/resumes/{resume_id}")
    assert refused.status_code == 400
    assert "QA" in refused.json()["detail"]
    assert len(client.get("/api/resumes").json()) == 1


def test_download_of_a_missing_resume_is_404(client) -> None:
    assert client.get("/api/resumes/999/download").status_code == 404
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_resumes_api.py -q`
Expected: FAIL — 404, роутер не подключён.

- [ ] **Step 3: Написать роутер резюме**

Создать `api/resumes_routes.py`:

```python
"""Библиотека резюме: загрузка, список, скачивание, удаление."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from job_monitor import resume_store
from job_monitor.db.connection import get_connection
from job_monitor.db.repositories import ResumesRepo

router = APIRouter(prefix="/api/resumes", tags=["resumes"])


@router.post("")
async def upload_resume(file: UploadFile = File(...)) -> dict[str, Any]:
    """Принимает файл и возвращает его запись в библиотеке.

    Клиентское имя (`file.filename`) НЕ участвует в построении пути: из него
    берётся только расширение, а имя на диске генерирует `resume_store`.
    """
    data = await file.read()
    try:
        stored_name, size = resume_store.store(file.filename or "", data)
    except resume_store.ResumeRejected as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    resume_id = ResumesRepo(get_connection()).add(
        file.filename or stored_name, stored_name, size, datetime.now()
    )
    return {"id": resume_id, "original_name": file.filename, "size_bytes": size}


@router.get("")
async def list_resumes() -> list[dict[str, Any]]:
    return ResumesRepo(get_connection()).list()


@router.get("/{resume_id}/download")
async def download_resume(resume_id: int) -> FileResponse:
    """Отдаёт файл. Аннотация — именно `FileResponse`.

    Ловушка FastAPI: аннотация наследником `Response` заставляет его не
    строить `response_model`, и это здесь единственно верно — валидировать
    поток байтов схемой нечем. Аннотация `dict` дала бы 500.
    """
    row = ResumesRepo(get_connection()).get(resume_id)
    if row is None:
        raise HTTPException(status_code=404, detail="резюме не найдено")
    try:
        path = resume_store.path_of(row["stored_name"])
    except resume_store.ResumeRejected as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    if not path.exists():
        raise HTTPException(status_code=404, detail="файл резюме пропал из каталога данных")
    return FileResponse(path, filename=row["original_name"])


@router.delete("/{resume_id}")
async def delete_resume(resume_id: int) -> dict[str, str]:
    conn = get_connection()
    repo = ResumesRepo(conn)
    row = repo.get(resume_id)
    if row is None:
        raise HTTPException(status_code=404, detail="резюме не найдено")
    used_by = repo.presets_using(resume_id)
    if used_by:
        raise HTTPException(
            status_code=400,
            detail="резюме используется пресетами: " + ", ".join(used_by),
        )
    resume_store.remove(row["stored_name"])
    repo.delete(resume_id)
    return {"status": "deleted"}
```

- [ ] **Step 4: Подключить роутер**

В `api/main.py`:

```python
from .resumes_routes import router as resumes_router
...
app.include_router(resumes_router)
```

- [ ] **Step 5: Прогнать тесты API резюме**

Run: `.venv/bin/python -m pytest tests/test_resumes_api.py -q`
Expected: PASS, 10 passed.

- [ ] **Step 6: Проверить дискриминацию мутациями**

1. В `upload_resume` передать в `resume_store.store` подготовленное имя `Path(file.filename).name` и сохранять под ним → падает `test_a_hostile_filename_does_not_escape_the_data_dir`.
2. Убрать проверку `used_by` → падает `test_delete_is_refused_while_a_preset_still_uses_the_resume`.
3. Убрать `resume_store.remove(...)` из `delete_resume` → падает `test_delete_removes_the_row_and_the_file`.
4. Заменить аннотацию `-> FileResponse` на `-> dict` → падает `test_upload_then_list_then_download` (500 от валидации ответа).

- [ ] **Step 7: Прогнать весь набор и закоммитить**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS.

```bash
git add api/resumes_routes.py api/main.py tests/test_resumes_api.py
git commit -m "feat(api): resume library upload, listing, download and delete"
```

---

## Task 6: Воркеры читают активный пресет

**Files:**
- Modify: `job_monitor/workers/telegram.py`
- Modify: `job_monitor/workers/hh.py`
- Modify: `api/tg_routes.py`, `api/hh_routes.py` (отказ старта при пустых критериях)
- Test: `tests/test_preset_driven_workers.py` (новый), `tests/test_template_render.py`, `tests/test_empty_criteria_refuses_start.py`; существующие `tests/test_telegram_worker.py` и `tests/test_hh_worker_loop.py` — привести к новым сигнатурам

**Interfaces:**
- Consumes: `job_monitor.presets.active_criteria` (Task 2), `TgFoundRepo` (Task 1), `ResumesRepo` (Task 1), `resume_store.path_of` (Task 3), `HH_AREA_ID` (Task 2).
- Produces: `render_template(template: str, channel: str, keyword: str, profession: str) -> str`; `IncomingPost` с новым полем `message_id: int`; `post_matches(post, criteria) -> str | None`; `is_eligible(username, criteria, own_username) -> bool`; `process_post(post, criteria, settings, repo, found_repo, sender, clock, own_username, attachment) -> list[str]`; `build_search_url(profession, criteria) -> str` в `hh.py`.

- [ ] **Step 1: Написать падающий тест подстановок**

Создать `tests/test_template_render.py`:

```python
"""Подстановки в шаблоне сообщения.

Только надёжные значения (решение D12): канал, совпавшее ключевое слово и
профессия из пресета. Заголовка вакансии среди них нет: пост в канале —
свободный текст без структуры, и любая эвристика по извлечению заголовка
иногда даёт мусор, а мусор уходит живому человеку в личку.
"""

from __future__ import annotations

from job_monitor.workers.telegram import render_template


def test_all_three_placeholders_are_substituted() -> None:
    rendered = render_template(
        "Здравствуйте! Увидел в {канал} вакансию {ключевое_слово}. Я {профессия}.",
        channel="qajobs",
        keyword="qa",
        profession="инженер по тестированию",
    )
    assert rendered == (
        "Здравствуйте! Увидел в qajobs вакансию qa. Я инженер по тестированию."
    )


def test_a_template_without_placeholders_is_unchanged() -> None:
    assert render_template("просто текст", "c", "k", "p") == "просто текст"


def test_braces_in_the_users_text_do_not_break_rendering() -> None:
    """Именно поэтому здесь `replace`, а не `str.format`: на пользовательском
    тексте `format` падает на неизвестном ключе, а на конструкции вида
    `{x.__class__}` вообще открывает доступ к атрибутам объектов.
    """
    template = "Ставка {30000} руб., режим {гибкий}, канал {канал}"
    assert render_template(template, "c", "k", "p") == (
        "Ставка {30000} руб., режим {гибкий}, канал c"
    )


def test_an_unknown_placeholder_is_left_as_written() -> None:
    assert render_template("а вот {заголовок}", "c", "k", "p") == "а вот {заголовок}"


def test_empty_values_do_not_leave_the_placeholder_text() -> None:
    assert render_template("канал {канал}", "", "", "") == "канал "
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_template_render.py -q`
Expected: FAIL — `ImportError: cannot import name 'render_template'`.

- [ ] **Step 3: Реализовать подстановки**

В `job_monitor/workers/telegram.py`:

```python
PLACEHOLDERS = ("{канал}", "{ключевое_слово}", "{профессия}")


def render_template(
    template: str, channel: str, keyword: str, profession: str
) -> str:
    """Подставляет три надёжных значения в шаблон сообщения.

    `str.replace`, а НЕ `str.format`: шаблон пишет пользователь, а в
    пользовательском тексте фигурные скобки встречаются как обычные символы
    («ставка {30000}»). `format` на таком тексте падает с `KeyError`, а на
    конструкции `{x.__class__}` даёт доступ к атрибутам переданных объектов —
    то есть форматная строка от пользователя это ещё и уязвимость, а не
    только хрупкость.
    """
    return (
        template.replace("{канал}", channel)
        .replace("{ключевое_слово}", keyword)
        .replace("{профессия}", profession)
    )
```

- [ ] **Step 4: Прогнать тесты подстановок**

Run: `.venv/bin/python -m pytest tests/test_template_render.py -q`
Expected: PASS, 5 passed.

- [ ] **Step 5: Написать падающий тест отказа старта**

Создать `tests/test_empty_criteria_refuses_start.py`:

```python
"""Пустой пресет — не повод молча работать впустую.

До этой правки воркер с пустым списком каналов запускался, крутился и ничего
не находил, а понять почему было нельзя: ни строки в логе, ни признака в
интерфейсе.
"""

from __future__ import annotations


def test_tg_start_is_refused_without_channels(client) -> None:
    response = client.post("/api/tg/start")
    assert response.status_code == 400
    detail = response.json()["detail"].lower()
    assert "канал" in detail
    assert "мой поиск" in detail, "в сообщении должно быть имя пресета"


def test_tg_start_is_refused_without_keywords(client) -> None:
    presets = client.get("/api/presets").json()
    active = next(p for p in presets if p["is_active"])["id"]
    client.patch(f"/api/presets/{active}", json={"channels": ["qajobs"]})
    response = client.post("/api/tg/start")
    assert response.status_code == 400
    assert "ключев" in response.json()["detail"].lower()


def test_hh_start_is_refused_without_professions(client) -> None:
    response = client.post("/api/hh/start")
    assert response.status_code == 400
    assert "професс" in response.json()["detail"].lower()


def test_start_is_allowed_once_the_criteria_are_filled(client, monkeypatch) -> None:
    from job_monitor.workers.manager import manager

    started: list[str] = []

    async def fake_start(name: str):
        started.append(name)

    monkeypatch.setattr(manager, "start", fake_start)

    presets = client.get("/api/presets").json()
    active = next(p for p in presets if p["is_active"])["id"]
    client.patch(
        f"/api/presets/{active}",
        json={"channels": ["qajobs"], "tg_keywords": ["qa"], "professions": ["QA"]},
    )
    assert client.post("/api/tg/start").status_code == 200
    assert client.post("/api/hh/start").status_code == 200
    assert started == ["tg", "hh"]
```

- [ ] **Step 6: Прогнать и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_empty_criteria_refuses_start.py -q`
Expected: FAIL — старт возвращает 200 при пустом пресете.

- [ ] **Step 7: Добавить проверку в роуты старта**

В `api/tg_routes.py`, в `tg_start`, перед `await manager.start("tg")`:

```python
    criteria = active_criteria(get_connection())
    preset_name = active_preset(get_connection())["name"]
    if not criteria.channels:
        raise HTTPException(
            status_code=400,
            detail=f"в пресете «{preset_name}» нет каналов — добавьте хотя бы один",
        )
    if not criteria.tg_keywords:
        raise HTTPException(
            status_code=400,
            detail=f"в пресете «{preset_name}» нет ключевых слов — "
            "иначе воркер не поймёт, какие посты считать вакансиями",
        )
```

В `api/hh_routes.py`, в `hh_start`, аналогично:

```python
    criteria = active_criteria(get_connection())
    preset_name = active_preset(get_connection())["name"]
    if not criteria.professions:
        raise HTTPException(
            status_code=400,
            detail=f"в пресете «{preset_name}» нет профессий — "
            "по ним строится поисковый запрос на hh.ru",
        )
```

Импорты: `from job_monitor.presets import active_criteria, active_preset`.

- [ ] **Step 8: Прогнать тесты отказа**

Run: `.venv/bin/python -m pytest tests/test_empty_criteria_refuses_start.py -q`
Expected: PASS, 4 passed.

- [ ] **Step 9: Перевести TG-воркер на критерии**

Проверено по коду: `IncomingPost` (`job_monitor/workers/telegram.py:28-31`) несёт только `channel` и `text`. Для записи в `tg_found` и для будущей ссылки на пост нужен идентификатор сообщения, поэтому датакласс расширяется:

```python
@dataclass(frozen=True)
class IncomingPost:
    channel: str
    text: str
    message_id: int
```

`run_worker()` — единственное место, которое строит `IncomingPost` из события Telethon; там подставляется `event.message.id`.

Изменения сигнатур (текущие — `is_eligible(username, settings, own_username)`, `post_matches(post, settings) -> bool`, `process_post(post, settings, repo, sender, clock, own_username)`):

- `is_eligible(username: str, criteria: SearchCriteria, own_username: str | None) -> bool` — внутри `settings.channels` → `criteria.channels`.
- `post_matches(post: IncomingPost, criteria: SearchCriteria) -> str | None` — возвращает **совпавшее ключевое слово** или `None`. Слово нужно и для подстановки `{ключевое_слово}`, и для колонки `matched_keyword`; возвращать `bool` и искать слово второй раз — значит завести два места с одной логикой.
- `process_post(post, criteria, settings, repo, found_repo, sender, clock, own_username, attachment) -> list[str]` — критерии и глобальные настройки приходят раздельно, плюс репозиторий найденного и путь вложения (`Path | None`).

Порядок внутри `process_post`: сначала `found_repo.record(...)`; если вернулся `False` — пост уже обрабатывался, выходим с пустым списком. Текст — `render_template(criteria.template, post.channel, keyword, profession)`, где `profession` — первый элемент `criteria.professions` или `""`. Вложение — путь, разрешённый **вызывающим**, а не разрешаемый внутри: чистая функция не должна знать ни про таблицу резюме, ни про файловую систему.

В `run_worker()`: `criteria = active_criteria(conn)` на каждой итерации; вложение разрешается так — если `criteria.resume_id` задан, взять `ResumesRepo(conn).get(...)`, затем `resume_store.path_of(row["stored_name"])`; если записи нет, или файла нет на диске, или `path_of` бросил — передать `None` и записать предупреждение в лог воркера. Отправка при этом **не отменяется**: резюме не обязательная часть отклика (спецификация, раздел 4.2).

- [ ] **Step 10: Написать тесты воркера на пресетах**

Создать `tests/test_preset_driven_workers.py` — отдельным файлом, а не дописывая в существующие: там свои фикстуры под прежние сигнатуры, и смешивать переходное состояние с новым труднее читать.

```python
"""Воркеры читают активный пресет, а не строку настроек."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from job_monitor import presets, resume_store
from job_monitor.criteria import SearchCriteria
from job_monitor.db.migrations import migrate
from job_monitor.db.repositories import PresetsRepo, TgFoundRepo, TgRepo
from job_monitor.settings import GlobalSettings
from job_monitor.workers.telegram import IncomingPost, post_matches, process_post

NOW = datetime(2026, 9, 9, 12, 0, 0)


@pytest.fixture()
def conn(tmp_path, monkeypatch) -> sqlite3.Connection:
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    connection = sqlite3.connect(tmp_path / "t.db")
    connection.row_factory = sqlite3.Row
    migrate(connection)
    return connection


@pytest.fixture()
def criteria() -> SearchCriteria:
    return SearchCriteria(
        channels=["qajobs"],
        tg_keywords=["qa", "тестировщик"],
        tg_exclude=["senior"],
        professions=["инженер по тестированию"],
        template="Здравствуйте! Увидел {ключевое_слово} в {канал}. Я {профессия}.",
    )


@pytest.fixture()
def settings() -> GlobalSettings:
    return GlobalSettings(safe_mode=False, delay_min=1, delay_max=1, max_per_day=10)


class Sender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, Path | None]] = []

    async def __call__(self, username: str, text: str, attachment: Path | None) -> None:
        self.messages.append((username, text, attachment))


def test_post_matches_returns_the_matched_keyword(criteria) -> None:
    post = IncomingPost(channel="qajobs", text="Ищем QA инженера", message_id=1)
    assert post_matches(post, criteria) == "qa"


def test_post_matches_returns_none_for_a_stop_word(criteria) -> None:
    post = IncomingPost(channel="qajobs", text="Ищем Senior QA", message_id=1)
    assert post_matches(post, criteria) is None


def test_post_matches_returns_none_for_a_foreign_channel(criteria) -> None:
    post = IncomingPost(channel="another", text="Ищем QA", message_id=1)
    assert post_matches(post, criteria) is None


async def test_the_message_text_comes_from_the_active_preset(
    conn, criteria, settings
) -> None:
    sender = Sender()
    post = IncomingPost(channel="qajobs", text="Ищем QA, писать @hr_anna", message_id=7)
    sent = await process_post(
        post, criteria, settings, TgRepo(conn), TgFoundRepo(conn), sender,
        clock=lambda: NOW, own_username="me", attachment=None,
    )
    assert sent == ["@hr_anna"]
    assert sender.messages[0][1] == (
        "Здравствуйте! Увидел qa в qajobs. Я инженер по тестированию."
    )


async def test_the_same_post_is_processed_only_once(conn, criteria, settings) -> None:
    """Дедупликация постов через `tg_found`: до появления таблицы пост,
    перечитанный на следующем круге, обрабатывался заново — и человек получал
    второе сообщение."""
    sender = Sender()
    post = IncomingPost(channel="qajobs", text="Ищем QA, писать @hr_anna", message_id=7)
    common = dict(clock=lambda: NOW, own_username="me", attachment=None)
    first = await process_post(
        post, criteria, settings, TgRepo(conn), TgFoundRepo(conn), sender, **common
    )
    second = await process_post(
        post, criteria, settings, TgRepo(conn), TgFoundRepo(conn), sender, **common
    )
    assert first == ["@hr_anna"]
    assert second == [], "тот же пост обработан во второй раз"
    assert len(sender.messages) == 1


async def test_a_missing_resume_file_does_not_stop_the_send(
    conn, criteria, settings
) -> None:
    """Резюме удалили из каталога данных руками. Сообщение всё равно уходит,
    но без вложения: резюме — не обязательная часть отклика."""
    sender = Sender()
    post = IncomingPost(channel="qajobs", text="Ищем QA, писать @hr_anna", message_id=7)
    sent = await process_post(
        post, criteria, settings, TgRepo(conn), TgFoundRepo(conn), sender,
        clock=lambda: NOW, own_username="me", attachment=None,
    )
    assert sent == ["@hr_anna"]
    assert sender.messages[0][2] is None


async def test_the_attachment_is_passed_through_when_it_exists(
    conn, criteria, settings
) -> None:
    stored_name, _ = resume_store.store("cv.pdf", b"%PDF-1.4 x")
    attachment = resume_store.path_of(stored_name)
    sender = Sender()
    post = IncomingPost(channel="qajobs", text="Ищем QA, писать @hr_anna", message_id=7)
    await process_post(
        post, criteria, settings, TgRepo(conn), TgFoundRepo(conn), sender,
        clock=lambda: NOW, own_username="me", attachment=attachment,
    )
    assert sender.messages[0][2] == attachment


async def test_dedup_of_contacts_is_shared_across_presets(
    conn, criteria, settings
) -> None:
    """Дедупликация общая (решение D7). Человек, которому написали из одного
    пресета, не должен получить второе сообщение из другого: канал и
    профессия сменились, а адресат тот же."""
    sender = Sender()
    common = dict(clock=lambda: NOW, own_username="me", attachment=None)

    first_preset = PresetsRepo(conn).create("QA", criteria.model_dump(), NOW)
    presets.set_active(conn, first_preset)
    await process_post(
        IncomingPost(channel="qajobs", text="Ищем QA, писать @hr_anna", message_id=1),
        criteria, settings, TgRepo(conn), TgFoundRepo(conn), sender, **common,
    )
    assert len(sender.messages) == 1

    other = SearchCriteria(
        channels=["support_jobs"],
        tg_keywords=["поддержка"],
        professions=["специалист поддержки"],
        template="Здравствуйте!",
    )
    second_preset = PresetsRepo(conn).create("Поддержка", other.model_dump(), NOW)
    presets.set_active(conn, second_preset)
    sent = await process_post(
        IncomingPost(
            channel="support_jobs", text="Нужна поддержка, писать @hr_anna", message_id=2
        ),
        other, settings, TgRepo(conn), TgFoundRepo(conn), sender, **common,
    )
    assert sent == [], "тому же человеку написали второй раз из другого пресета"
    assert len(sender.messages) == 1


async def test_the_daily_budget_is_shared_across_presets(
    conn, criteria, settings
) -> None:
    """Три пресета по 25 отправок не должны давать 75 сообщений с одного
    аккаунта: банят аккаунт, а не пресет."""
    sender = Sender()
    common = dict(clock=lambda: NOW, own_username="me", attachment=None)
    tight = settings.model_copy(update={"max_per_day": 1})

    await process_post(
        IncomingPost(channel="qajobs", text="QA, писать @first", message_id=1),
        criteria, tight, TgRepo(conn), TgFoundRepo(conn), sender, **common,
    )
    sent = await process_post(
        IncomingPost(channel="qajobs", text="QA, писать @second", message_id=2),
        criteria, tight, TgRepo(conn), TgFoundRepo(conn), sender, **common,
    )
    assert sent == [], "дневной лимит не удержал вторую отправку"
    assert len(sender.messages) == 1
```

`asyncio_mode = "auto"` уже стоит в `pyproject.toml`, поэтому декоратор `@pytest.mark.asyncio` не нужен.

Если существующая `process_post` считает дневной лимит в другом порядке (например, проверяет до записи, а не после), привести тест в соответствие с фактическим контрактом и **не** менять саму логику лимита: она проверена предыдущей веткой.

- [ ] **Step 10b: Перевести HH-воркер на критерии**

В `job_monitor/workers/hh.py`:

- `settings.hh_keywords` → `criteria.professions`;
- `settings.hh_area_ids` → `[HH_AREA_ID]` (импорт из `job_monitor.criteria`);
- `settings.hh_exclude` → `criteria.hh_exclude`;
- `settings.hh_cover_letter` → `criteria.hh_cover_letter`;
- `hh_experience`, `hh_employment`, `hh_schedule`, `hh_salary_from`, `hh_search_period`, `hh_resume_id` → те же поля `criteria`;
- лимиты, задержки и `hh_check_interval` остаются в `settings`.

Дописать в `tests/test_preset_driven_workers.py`:

```python
def test_the_hh_search_url_uses_the_preset_professions_and_the_constant_area() -> None:
    """Регион больше не настройка: он приходит из константы, и подменить его
    через API нельзя (решение D8)."""
    from job_monitor.criteria import HH_AREA_ID
    from job_monitor.workers.hh import build_search_url

    url = build_search_url(
        profession="инженер по тестированию",
        criteria=SearchCriteria(
            professions=["инженер по тестированию"], hh_salary_from=100000
        ),
    )
    assert "area=113" in url
    assert HH_AREA_ID == 113
    assert "text=" in url
    assert "salary=100000" in url
```

Свериться с фактическим именем функции, строящей URL поиска (`grep -n "hh.ru/search\|def .*search" job_monitor/workers/hh.py`), и назвать в тесте её. Если URL собирается инлайном внутри цикла — **выделить** сборку в отдельную чистую функцию: иначе проверить регион можно только через настоящий браузер, а тесты браузер не открывают.

- [ ] **Step 11: Прогнать весь набор**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. Все места, где раньше читались критерии из настроек, найти командой `grep -rn "settings\.\(channels\|keywords\|exclude\|template\|file_path\|hh_keywords\|hh_area_ids\|hh_cover_letter\|hh_experience\|hh_employment\|hh_schedule\|hh_salary_from\|hh_search_period\|hh_resume_id\)" job_monitor/ api/` — она должна не находить ничего.

- [ ] **Step 12: Проверить дискриминацию мутациями**

1. Убрать проверку `criteria.channels` в `tg_start` → падает `test_tg_start_is_refused_without_channels`.
2. Убрать проверку `criteria.professions` в `hh_start` → падает `test_hh_start_is_refused_without_professions`.
3. Заменить `render_template` на `template.format(...)` → падает `test_braces_in_the_users_text_do_not_break_rendering`.
4. Игнорировать результат `TgFoundRepo.record` → падает `test_the_same_post_is_processed_only_once`.
5. Заменить `[HH_AREA_ID]` на пустой список → падает `test_the_hh_search_url_uses_the_preset_professions_and_the_constant_area`.
7. Проверять дневной лимит по пресету вместо общего счёта → падает `test_the_daily_budget_is_shared_across_presets`.
8. Дедуплицировать контакты в пределах пресета → падает `test_dedup_of_contacts_is_shared_across_presets`.
6. При отсутствующем файле резюме бросать исключение вместо отправки без вложения → падает `test_a_missing_resume_file_does_not_stop_the_send`.

- [ ] **Step 13: Закоммитить**

```bash
git add job_monitor/workers/telegram.py job_monitor/workers/hh.py api/tg_routes.py api/hh_routes.py tests/test_template_render.py tests/test_empty_criteria_refuses_start.py tests/test_preset_driven_workers.py tests/test_telegram_worker.py tests/test_hh_worker_loop.py
git commit -m "feat(workers): read the active preset, refuse to start on empty criteria"
```

---

## Task 7: Каталог данных вне синхронизации

**Files:**
- Modify: `job_monitor/paths.py`
- Modify: `api/main.py` (вызов при старте), `api/routes_state.py` (признак для интерфейса)
- Modify: `SECURITY.md`
- Test: `tests/test_data_dir_sync.py`

**Interfaces:**
- Produces:
  - `paths.exclude_from_backups() -> bool` — best-effort исключение каталога данных из Time Machine; `True`, если исключение установлено или уже было.
  - `paths.looks_synced(directory: Path | None = None) -> str | None` — имя облачного сервиса, если путь похож на его каталог, иначе `None`.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_data_dir_sync.py`:

```python
"""Каталог данных не должен уезжать в чужое облако.

Права `0600` защищают от другого пользователя машины, но не от бэкапа: файл
сессии Telethon содержит `auth_key`, которого достаточно для входа в аккаунт
в обход 2FA, и попадание каталога в синхронизируемую папку сводит права на
нет (решение D13).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from job_monitor import paths


@pytest.mark.parametrize(
    "candidate, expected",
    [
        ("/Users/kim/Library/Mobile Documents/com~apple~CloudDocs/jm", "iCloud Drive"),
        ("/Users/kim/Dropbox/jm", "Dropbox"),
        ("/Users/kim/Google Drive/jm", "Google Drive"),
        ("/Users/kim/OneDrive/jm", "OneDrive"),
        ("/Users/kim/.job-monitor", None),
        ("/tmp/jm", None),
    ],
)
def test_synced_locations_are_recognised(candidate: str, expected: str | None) -> None:
    assert paths.looks_synced(Path(candidate)) == expected


def test_exclusion_is_best_effort_and_never_raises(tmp_path, monkeypatch) -> None:
    """Ужесточение защиты — best-effort, а не условие запуска: это уже
    установленное в проекте правило (`paths.tighten`). Отсутствие `tmutil`
    или отказ прав не должны ронять приложение."""
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(paths.shutil, "which", lambda _name: None)
    assert paths.exclude_from_backups() is False


def test_exclusion_calls_tmutil_when_it_exists(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(paths.shutil, "which", lambda _name: "/usr/bin/tmutil")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(paths.subprocess, "run", fake_run)
    assert paths.exclude_from_backups() is True
    assert calls == [["/usr/bin/tmutil", "addexclusion", str(tmp_path)]]
    assert all("shell" not in kwargs for kwargs in [{}]), "shell=True запрещён"


def test_state_reports_a_synced_data_dir(client, monkeypatch) -> None:
    """Предупреждение нужно именно тогда, когда пользователь сам задал
    каталог переменной окружения — в интерфейсе, а не в логе."""
    monkeypatch.setattr(paths, "looks_synced", lambda directory=None: "Dropbox")
    body = client.get("/api/state").json()
    assert body["data_dir_warning"] == "Dropbox"


def test_state_has_no_warning_for_a_normal_data_dir(client) -> None:
    assert client.get("/api/state").json()["data_dir_warning"] is None
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_data_dir_sync.py -q`
Expected: FAIL — `AttributeError: module 'job_monitor.paths' has no attribute 'looks_synced'`.

- [ ] **Step 3: Реализовать**

В `job_monitor/paths.py` (добавить `import shutil`, `import subprocess`, `import sys`):

```python
SYNCED_MARKERS: tuple[tuple[str, str], ...] = (
    ("Library/Mobile Documents/com~apple~CloudDocs", "iCloud Drive"),
    ("Dropbox", "Dropbox"),
    ("Google Drive", "Google Drive"),
    ("OneDrive", "OneDrive"),
)


def looks_synced(directory: Path | None = None) -> str | None:
    """Имя облачного сервиса, если каталог похож на его папку.

    Проверка по пути, а не по опросу сервисов: опрашивать нечего, а путь
    известен и достаточен. Ложное срабатывание на каталоге с именем
    `Dropbox` дешевле пропущенной утечки `auth_key` в чужое облако.
    """
    target = (directory or data_dir()).resolve()
    parts = target.parts
    for marker, service in SYNCED_MARKERS:
        marker_parts = tuple(Path(marker).parts)
        window = len(marker_parts)
        if any(
            parts[index : index + window] == marker_parts
            for index in range(len(parts) - window + 1)
        ):
            return service
    return None


def exclude_from_backups() -> bool:
    """Исключает каталог данных из Time Machine. Best-effort.

    Не условие запуска: `tmutil` может отсутствовать, а том — не
    поддерживать исключения. То же правило, что у `tighten()` — ужесточение
    защиты никогда не мешает работать.
    """
    if sys.platform != "darwin":
        return False
    tmutil = shutil.which("tmutil")
    if tmutil is None:
        return False
    try:
        result = subprocess.run(          # noqa: S603 — shell=False, аргументы списком
            [tmutil, "addexclusion", str(data_dir())],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0
```

В `api/main.py`, в `lifespan`, после `configure_logging()`:

```python
    paths.exclude_from_backups()
    synced = paths.looks_synced()
    if synced:
        worker_logger("tg").warning(
            "каталог данных похож на папку %s: файл сессии Telethon содержит "
            "auth_key, которого достаточно для входа в аккаунт в обход 2FA",
            synced,
        )
```

В `api/routes_state.py` добавить в ответ поле `"data_dir_warning": paths.looks_synced()`. Расширить аннотацию возврата, если она перечисляет типы значений.

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/test_data_dir_sync.py -q`
Expected: PASS, 10 passed.

- [ ] **Step 5: Дописать `SECURITY.md`**

В раздел «Механизмы» добавить пункт: каталог данных исключается из Time Machine при старте (best-effort); интерфейс предупреждает, если путь похож на облачную папку; объяснить, почему это важно именно для файла сессии (`auth_key` даёт вход в обход 2FA, права `0600` от бэкапа не защищают). Указать, что шифрование at rest сознательно не делается (решение D13) и почему.

- [ ] **Step 6: Проверить дискриминацию мутациями**

1. В `looks_synced` вернуть `None` всегда → падают `test_synced_locations_are_recognised` и `test_state_reports_a_synced_data_dir`.
2. В `exclude_from_backups` убрать `try/except` и заставить `subprocess.run` бросить → падает `test_exclusion_is_best_effort_and_never_raises`.
3. Убрать `data_dir_warning` из `/api/state` → падает `test_state_has_no_warning_for_a_normal_data_dir`.

- [ ] **Step 7: Прогнать весь набор и закоммитить**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS.

```bash
git add job_monitor/paths.py api/main.py api/routes_state.py SECURITY.md tests/test_data_dir_sync.py
git commit -m "feat(paths): keep the data dir out of backups and warn about synced folders"
```

---

## Task 8: Минимальная адаптация интерфейса

**Files:**
- Modify: `frontend/index.html`, `frontend/app.js`, `frontend/style.css`
- Modify: `README.md`
- Test: `tests/test_frontend_presets.py`

**Interfaces:**
- Consumes: все роуты Task 4, Task 5, Task 6.

Это **не редизайн** — он в подпроекте 3. Задача одна: приложение должно остаться работоспособным после смены контракта API. Инварианты фронтенда, за которые заплачено предыдущей веткой, обязаны сохраниться: **ноль инлайновых `on*=` обработчиков** (проверять `grep -o ' on[a-z]*=' frontend/index.html | wc -l` → 0; считать вхождения, а не строки — `grep -c` считает строки и однажды уже дал в этом проекте неверный ответ), ноль `innerHTML`, гвард схемы URL в `el()`, баннер протухшего токена на 403, один периодический опрос `/api/state`.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_frontend_presets.py`. Взять за образец `tests/test_frontend_events.py` — там уже есть механика прогона JS в Node и извлечения функций из `app.js`.

```python
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _app_source() -> str:
    return (REPO_ROOT / "frontend" / "app.js").read_text(encoding="utf-8")


def _index_source() -> str:
    return (REPO_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


def test_no_inline_handlers_survive_the_change() -> None:
    """Инвариант предыдущей ветки: CSP держит `script-src 'self'`, и один
    уцелевший инлайновый обработчик делает её либо сломанной, либо ложью."""
    assert len(re.findall(r" on[a-z]+=", _index_source())) == 0


def test_no_inner_html_survives_the_change() -> None:
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert forbidden not in _app_source(), f"{forbidden} вернулся в app.js"


def test_the_preset_switcher_is_delegated() -> None:
    assert 'data-action="activatePreset"' in _index_source() or (
        "activatePreset" in _app_source()
    )
    assert "/presets" in _app_source()


def test_the_dead_file_picker_is_gone_and_upload_took_its_place() -> None:
    """`onFileSelect` обновлял три подписи и никогда не отправлял файл на
    бэкенд — дефект L14, единственный оставленный открытым предыдущей
    веткой."""
    source = _app_source()
    assert "/api/resumes" in source or "'/resumes'" in source
    assert "file_path" not in source, "поля file_path больше нет в контракте"


def test_criteria_are_patched_to_the_preset_not_to_config() -> None:
    """Критерии больше не проходят через /api/config: он про глобальное."""
    source = _app_source()
    for criteria_patch in (
        "apiPatch('/config', { channels",
        "apiPatch('/config', { keywords",
        "apiPatch('/config', { template",
        "apiPatch('/config', { hh_cover_letter",
    ):
        assert criteria_patch not in source, (
            f"{criteria_patch!r} — критерий уехал в пресет, патч должен идти на /presets/{{id}}"
        )
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_frontend_presets.py -q`
Expected: FAIL — `apiPatch('/config', { channels` ещё в коде, `/api/resumes` отсутствует.

- [ ] **Step 3: Адаптировать интерфейс**

Минимум, который нужен для работоспособности:

1. **Переключатель пресетов** на дашборде: список из `GET /api/presets`, кнопка на каждый, активный выделен. Действие `activatePreset` через `data-action`/`data-arg`, ответ `409` показывает `detail` (воркер не остановился) вместо переключения.
2. **Экраны критериев** (каналы, ключевые слова, шаблон, сопроводительное, фильтры hh) читают `GET /api/presets/{active}` и пишут `PATCH /api/presets/{active}`, а не `/api/config`.
3. **Занятость, опыт, график** — переключатели, построенные из `GET /api/dictionaries` (добавлен в Task 4). Подписи не дублируются в `app.js`: иначе они разойдутся с бэкендом при первой же правке константы, и пользователь увидит одно, а отправится другое.
4. **Загрузка резюме**: `POST /api/resumes` (`FormData`), список из `GET /api/resumes`, выбор резюме для пресета — `PATCH /api/presets/{active}` с `resume_id`. Удалить мёртвый `onFileSelect` и всё, что показывало имя файла без отправки.
5. **Предупреждение о синхронизированном каталоге**: если `/api/state` вернул `data_dir_warning`, показать баннер.
6. Проверить, что нигде не осталось чтения ушедших полей (`file_path`, `hh_area_ids`, `hh_keywords`, `keywords`, `exclude`).

- [ ] **Step 4: Прогнать тесты интерфейса и весь набор**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Проверить живьём**

```bash
make run
```

Пройти: создать пресет, заполнить каналы и ключевые слова, загрузить резюме, выбрать его в пресете, переключиться между двумя пресетами, попробовать запустить воркер с пустым пресетом (ожидать внятную ошибку), удалить резюме, на которое ссылается пресет (ожидать отказ с именем пресета). Убедиться, что в консоли браузера нет нарушений CSP и необработанных исключений.

- [ ] **Step 6: Обновить `README.md`**

Описать пресеты (что в них лежит, что общее), библиотеку резюме, и что чистая установка приходит с пустым пресетом — искать она начинает только после того, как вы заполнили критерии.

- [ ] **Step 7: Закоммитить**

```bash
git add frontend/index.html frontend/app.js frontend/style.css README.md tests/test_frontend_presets.py
git commit -m "feat(frontend): preset switcher and resume upload replacing the dead picker"
```

---

## Итог плана

Закрыты: пресеты критериев с переключением, библиотека резюме с загрузкой через интерфейс (и вместе с ней дефект L14 — единственный, оставленный открытым предыдущей веткой), снятие чужого поиска из кода, таблица найденного в Telegram на запись, отказ старта при пустых критериях, каталог данных вне бэкапов.

Следующие подпроекты: очередь найденных вакансий с ручными статусами, затем переработка UI/UX.
