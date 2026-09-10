# Очередь найденного и переработка интерфейса — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** показать найденные вакансии списком со ссылками, дать откликнуться
вручную и проставить статус руками — и перестроить интерфейс с семи экранов,
нарезанных по типу сущности, на четыре, нарезанных по задаче.

**Architecture:** статусы переезжают в один словарь `job_monitor/statuses.py`
и хранятся в существующих таблицах (`tg_found`, `hh_applications`), которые
миграция 004 расширяет колонками. hh-воркер начинает записывать вакансию в
момент находки, а не после попытки отклика, и дедуплицирует по *решённости*
статуса, а не по наличию строки. Новый роутер `api/found_routes.py` отдаёт две
очереди и принимает ручную смену статуса. Фронтенд перестраивается на четыре
экрана с постоянной полосой состояния.

**Tech Stack:** Python 3.11+, FastAPI, pydantic 2, SQLite (WAL), ванильный
JS без сборки, pytest.

**Spec:** `docs/superpowers/specs/2026-09-10-found-queue-and-ui-design.md`

## Global Constraints

Каждое требование ниже действует во всех задачах — повторять его в задаче не
нужно, нарушить нельзя.

- Python `>=3.11`; зависимости закреплены в `requirements.lock`; **новых
  зависимостей этот подпроект не добавляет**.
- Запрещены `eval`, `exec`, `pickle`, `marshal`, `shell=True`, установка
  пакетов в рантайме.
- Ни один секрет не попадает в БД, в ответы API, в логи и в репозиторий.
- Состояние резолвится только через `job_monitor/paths.py`.
- Сервер слушает только `127.0.0.1`.
- Аннотации типов на новом коде; обработчики `api/` объявляют возвращаемый
  тип. **Ловушка FastAPI:** аннотация возврата становится `response_model`, а
  аннотация наследником `Response` наоборот отключает валидацию. Для роутов
  этого плана правильная аннотация — `list[dict[str, Any]]` или
  `dict[str, Any]`.
- Тесты не ходят в сеть, не открывают браузер, не пишут в домашний каталог и
  не зависят от порядка.
- `make test` зелёный на каждом коммите. На старте подпроекта — **662 passed**.
- Агент **никогда** не выполняет `git checkout`, `git restore`, `git stash`,
  `git clean`, `git reset`. Если правка пошла не туда — правьте вперёд.
- Тесты-ловушки (`tests/test_frontend_safety.py`,
  `tests/test_frontend_events.py`, `tests/test_route_invariants.py`,
  `tests/test_docs_structure.py`, `tests/test_stored_columns_are_read.py`,
  `tests/test_no_foreign_search_terms.py`) можно только **усиливать**.
  Ослабить проверку, чтобы прошла новая правка, — запрещено.
- Каждое сообщение коммита заканчивается строкой:
  `Claude-Session: https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB`
- Каждый новый тест обязан быть **дискриминирующим**: перед коммитом
  сформулируйте мутацию, которая должна его свалить, внесите её, убедитесь,
  что тест красный, откатите правкой вперёд. Проверки, держащиеся за счёт
  соседних, в этом проекте находились четырежды.

### Инварианты фронтенда, которые обязаны уцелеть

Переписывание фронтенда — ровно та работа, в которой их теряют. За них
заплачено тремя планами:

- ноль инлайновых `on*=` обработчиков в разметке; ноль инлайновых `<script>`;
- ноль сборки HTML-строк в `app.js` (`innerHTML`, `insertAdjacentHTML`,
  `document.write`, `new Function`, `eval` — запрещённые приёмники);
- узлы строятся только через `el()`; недоверенный текст — только через
  `text:` (то есть `textContent`);
- гвард схемы URL живёт внутри `el()`, а не на месте вызова;
- баннер протухшего токена на 403 — внутри `apiGet`/`apiSend`/`apiUpload`;
  **четвёртого прямого `fetch` в файле не появляется**;
- один периодический опрос — `GET /api/state`, раз в 3 секунды;
- отвергнутое сохранение нигде не показывается как успешное
  (`configPatchOk` / `configErrorDetail`).

### Три документа описывают дерево проекта

`tests/test_docs_structure.py` требует, чтобы **каждый** новый модуль пакетов
`api/` и `job_monitor/` был назван во всех трёх:

1. `структура.txt` (дерево до первой пустой строки);
2. `README.md`, блок кода под заголовком «Структура проекта»;
3. `docs/superpowers/specs/2026-09-08-hardening-and-rework-spec.md`, первый
   блок кода после заголовка `## 4. `.

Обновление документов входит в ту же задачу, которая создаёт модуль, — иначе
коммит красный.

---

## Структура файлов

**Создаются:**

| Файл | Ответственность |
|------|-----------------|
| `job_monitor/statuses.py` | словарь статусов и множества `DECIDED` / `MANUAL` / `APPLIED` / `REOPENABLE`; ничего не импортирует из пакета |
| `api/found_routes.py` | `GET`/`PATCH /api/found/tg` и `/api/found/hh`: чтение очередей и ручная смена статуса |
| `tests/test_statuses.py` | словарь статусов |
| `tests/test_migration_004.py` | миграция 004 |
| `tests/test_found_repos.py` | чтение и запись очередей в репозиториях |
| `tests/test_found_api.py` | роуты очереди, переходы статусов, побочные эффекты |
| `tests/test_hh_safe_mode.py` | поведение hh-воркера: запись при находке, дедуп по решённости, безопасный режим |
| `tests/test_frontend_screens.py` | инварианты четырёх экранов интерфейса |

**Изменяются:**

| Файл | Что меняется |
|------|--------------|
| `job_monitor/db/migrations.py` | `MIGRATION_004`, запись в `MIGRATIONS` |
| `job_monitor/db/repositories.py` | `HhRepo`: `FIELDS`, `record_found`, `is_decided`, `get`, `list_found`, `set_status`; `TgFoundRepo`: `record` (контакты), `get`, `list`, `set_status` |
| `job_monitor/workers/hh.py` | безопасный режим, запись при находке, дедуп по решённости, `status_source` |
| `job_monitor/workers/telegram.py` | контакты в `tg_found`, статус `AUTO_APPLIED` после отправки |
| `api/main.py` | подключение `found_router` |
| `api/hh_routes.py` | удаление `GET /api/hh/vacancies` |
| `api/presets_routes.py` | долг M-2: `PATCH` перестаёт применяться наполовину |
| `api/resumes_routes.py` | долг M-3: файл не остаётся сиротой |
| `frontend/index.html`, `frontend/app.js`, `frontend/style.css` | четыре экрана, полоса состояния, очередь |
| `структура.txt`, `README.md`, спецификация укрепления §4 | новые модули |

---

## Задача 1: словарь статусов и миграция 004

**Files:**
- Create: `job_monitor/statuses.py`
- Create: `tests/test_statuses.py`
- Create: `tests/test_migration_004.py`
- Modify: `job_monitor/db/migrations.py`
- Modify: `job_monitor/db/repositories.py` (только строка `HH_STATUS_APPLIED`)
- Modify: `job_monitor/workers/hh.py` (только строка `HH_STATUS_SCENARIO_ERROR`)
- Modify: `структура.txt`, `README.md`, `docs/superpowers/specs/2026-09-08-hardening-and-rework-spec.md`

**Interfaces:**
- Consumes: ничего (первая задача).
- Produces:
  - `job_monitor.statuses.NEW / AUTO_APPLIED / MANUAL_APPLIED / DISMISSED /
    SKIPPED / SCENARIO_ERROR: str`
  - `job_monitor.statuses.ALL / DECIDED / MANUAL / APPLIED / REOPENABLE:
    frozenset[str]`
  - `job_monitor.statuses.SOURCE_ROBOT / SOURCE_HUMAN: str`
  - схема БД версии 4: `tg_found.status`, `tg_found.status_at`,
    `tg_found.usernames`, `hh_applications.status_source`, индексы
    `idx_tg_found_status`, `idx_hh_status`.

- [ ] **Шаг 1: напишите падающий тест на словарь статусов**

Создайте `tests/test_statuses.py`:

```python
"""Один словарь статусов на оба списка найденного.

Списки раздельные (решение D14), но статусы общие: иначе «откликнулся сам» в
Telegram и в hh.ru стали бы разными строками и каждый счётчик пришлось бы
писать дважды.
"""

from __future__ import annotations

from job_monitor import statuses


def test_decided_is_everything_except_new() -> None:
    """`DECIDED` — это и есть новая дедупликация (решение D19): воркер
    пропускает решённое и обрабатывает новое."""
    assert statuses.DECIDED == statuses.ALL - {statuses.NEW}
    assert statuses.NEW not in statuses.DECIDED


def test_skipped_counts_as_decided() -> None:
    """Намеренно, а не по недосмотру: так ведёт себя нынешний код —
    пропущенная вакансия больше не берётся в работу. Менять это заодно с
    переездом на статусы значило бы смешать два изменения в одном; вернуть
    вакансию в очередь можно вручную."""
    assert statuses.SKIPPED in statuses.DECIDED


def test_manual_is_the_only_thing_a_human_may_set() -> None:
    """Разрешить ставить «отклик отправлен» руками — значит сделать счётчик
    «отправлено сегодня» неправдой: это слово робота."""
    assert statuses.MANUAL == {statuses.MANUAL_APPLIED, statuses.DISMISSED}
    assert statuses.AUTO_APPLIED not in statuses.MANUAL


def test_nothing_already_applied_can_be_reopened() -> None:
    """Возврат в «новую» означает «обработать заново». Для уже отправленного
    отклика это второй отклик тому же работодателю: для Telegram от него
    защищает дедупликация контактов, для hh.ru — ничто."""
    assert statuses.REOPENABLE == {statuses.DISMISSED, statuses.SKIPPED}
    assert statuses.REOPENABLE & statuses.APPLIED == frozenset()
    assert statuses.APPLIED == {statuses.AUTO_APPLIED, statuses.MANUAL_APPLIED}


def test_every_status_is_distinct() -> None:
    """Страховка от вакуумности всех проверок выше: если бы две константы
    совпали строками, множества сложились бы неотличимо."""
    names = [
        statuses.NEW, statuses.AUTO_APPLIED, statuses.MANUAL_APPLIED,
        statuses.DISMISSED, statuses.SKIPPED, statuses.SCENARIO_ERROR,
    ]
    assert len(set(names)) == len(names)
    assert statuses.ALL == set(names)


def test_the_old_hh_names_still_point_at_the_same_strings() -> None:
    """Прежние имена остаются алиасами, чтобы не править места
    использования разом. Разойдись они — счётчики молча обнулятся."""
    from job_monitor.db.repositories import HH_STATUS_APPLIED
    from job_monitor.workers.hh import HH_STATUS_SCENARIO_ERROR

    assert HH_STATUS_APPLIED == statuses.AUTO_APPLIED
    assert HH_STATUS_SCENARIO_ERROR == statuses.SCENARIO_ERROR


def test_sources_name_who_set_the_status() -> None:
    assert statuses.SOURCE_ROBOT != statuses.SOURCE_HUMAN
```

- [ ] **Шаг 2: прогоните — тесты должны упасть**

Run: `pytest tests/test_statuses.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'job_monitor.statuses'`

- [ ] **Шаг 3: создайте `job_monitor/statuses.py`**

```python
"""Словарь статусов найденного — один на оба списка.

Списки раздельные (решение D14: вакансия hh.ru и пост в Telegram разной
природы), но статусы общие. Иначе «откликнулся сам» в Telegram и в hh.ru
стали бы разными строками, и каждый счётчик, каждый фильтр и каждый бейдж
пришлось бы писать дважды — с гарантией, что однажды одна из копий отстанет.

Значения — русские слова, а не коды: они уже лежат в базе пользователя
(`hh_applications.status` пишется так с первой версии), и переименование
означало бы миграцию данных ради косметики.
"""

from __future__ import annotations

NEW = "новая"
AUTO_APPLIED = "отклик отправлен"
MANUAL_APPLIED = "откликнулся сам"
DISMISSED = "не подходит"
SKIPPED = "пропущено"
SCENARIO_ERROR = "ошибка сценария"

ALL: frozenset[str] = frozenset(
    {NEW, AUTO_APPLIED, MANUAL_APPLIED, DISMISSED, SKIPPED, SCENARIO_ERROR}
)

#: Всё, кроме «новой». Это и есть дедупликация (решение D19): раз строка
#: теперь появляется в момент находки, проверять «строка есть → пропустить»
#: нельзя — так воркер отбрасывал бы то, что сам только что нашёл.
#:
#: `SKIPPED` входит сюда намеренно: нынешний код пропускает такую вакансию
#: навсегда, и менять это заодно с переездом на статусы значило бы смешать
#: два изменения в одном. Вернуть её в работу можно вручную.
DECIDED: frozenset[str] = ALL - {NEW}

#: Единственные два статуса, которые принимает `PATCH /api/found/*`.
MANUAL: frozenset[str] = frozenset({MANUAL_APPLIED, DISMISSED})

#: Отклик уже ушёл работодателю — из этих состояний возврат в `NEW` запрещён.
APPLIED: frozenset[str] = frozenset({AUTO_APPLIED, MANUAL_APPLIED})

#: Откуда вернуть запись в очередь можно: «передумал» и «робот не смог».
REOPENABLE: frozenset[str] = frozenset({DISMISSED, SKIPPED})

#: Кто поставил статус. Пишется в `hh_applications.status_source`, чтобы в
#: истории было видно, что сделал робот, а что человек.
SOURCE_ROBOT = "робот"
SOURCE_HUMAN = "человек"
```

- [ ] **Шаг 4: переведите прежние имена в алиасы**

В `job_monitor/db/repositories.py` замените объявление константы (сейчас это
строка с комментарием «Status string a completed hh.ru application is
stamped with») на:

```python
from job_monitor import statuses

# Прежнее имя той же строки. Оставлено алиасом, чтобы не править все места
# использования разом; единственный источник значения — job_monitor/statuses.py.
HH_STATUS_APPLIED = statuses.AUTO_APPLIED
```

В `job_monitor/workers/hh.py` замените объявление `HH_STATUS_SCENARIO_ERROR`
на:

```python
from job_monitor import statuses

# L5: статус для вакансии, на которой сценарий Selenium сломался опечаткой
# уже после клика по кнопке отклика. Прежнее имя, единственный источник
# значения — job_monitor/statuses.py.
HH_STATUS_SCENARIO_ERROR = statuses.SCENARIO_ERROR
```

Импорт `statuses` в `hh.py` добавьте к остальным импортам `job_monitor`
вверху файла, не внутри функции.

- [ ] **Шаг 5: прогоните — тесты словаря должны пройти**

Run: `pytest tests/test_statuses.py -q`
Expected: PASS (7 passed)

- [ ] **Шаг 6: напишите падающий тест на миграцию 004**

Создайте `tests/test_migration_004.py`:

```python
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
```

- [ ] **Шаг 7: прогоните — тесты миграции должны упасть**

Run: `pytest tests/test_migration_004.py -q`
Expected: FAIL — `sqlite3.OperationalError: no such column: status` и
`assert 3 >= 4`

- [ ] **Шаг 8: добавьте миграцию**

В `job_monitor/db/migrations.py` после `MIGRATION_003` добавьте:

```python
MIGRATION_004 = """
ALTER TABLE tg_found ADD COLUMN status    TEXT NOT NULL DEFAULT 'новая';
ALTER TABLE tg_found ADD COLUMN status_at TEXT;
ALTER TABLE tg_found ADD COLUMN usernames TEXT;

ALTER TABLE hh_applications ADD COLUMN status_source TEXT NOT NULL DEFAULT 'робот';

CREATE INDEX IF NOT EXISTS idx_tg_found_status ON tg_found(status);
CREATE INDEX IF NOT EXISTS idx_hh_status       ON hh_applications(status);
"""
```

и допишите её в список:

```python
MIGRATIONS: list[tuple[int, str]] = [
    (1, MIGRATION_001),
    (2, MIGRATION_002),
    (3, MIGRATION_003),
    (4, MIGRATION_004),
]
```

Значения по умолчанию написаны литералами, а не через `statuses.NEW`: это
SQL-текст, который уже применён у пользователя, и он обязан остаться
неизменным даже если константу в Python однажды перепишут. Добавьте это
объяснение комментарием над `MIGRATION_004`, а рядом — почему `usernames`
хранит JSON-массив, а не одно имя: гранулярность статуса — пост целиком, и
контактов в посте бывает несколько.

- [ ] **Шаг 9: прогоните — тесты миграции должны пройти**

Run: `pytest tests/test_migration_004.py tests/test_migration_003.py -q`
Expected: PASS

- [ ] **Шаг 10: внесите новый модуль в три документа**

`структура.txt`, строка после `criteria.py`:

```
│   ├── statuses.py             словарь статусов найденного (общий на TG и hh.ru)
```

`README.md`, тот же блок «Структура проекта» — та же строка в том же месте.

`docs/superpowers/specs/2026-09-08-hardening-and-rework-spec.md`, первый блок
кода после заголовка `## 4. ` — та же строка.

- [ ] **Шаг 11: прогоните весь набор**

Run: `make test`
Expected: PASS, 662 + 14 = 676 passed

- [ ] **Шаг 12: проверьте дискриминирующую силу**

Внесите по очереди и убедитесь, что тест краснеет, затем верните правкой вперёд:

1. `DECIDED = ALL` (без вычитания `NEW`) → падает
   `test_decided_is_everything_except_new`.
2. `MANUAL = frozenset({MANUAL_APPLIED, DISMISSED, AUTO_APPLIED})` → падает
   `test_manual_is_the_only_thing_a_human_may_set`.
3. `REOPENABLE = frozenset({DISMISSED, SKIPPED, MANUAL_APPLIED})` → падает
   `test_nothing_already_applied_can_be_reopened`.
4. Уберите `(4, MIGRATION_004)` из `MIGRATIONS` → падает
   `test_schema_version_reached_four` и все проверки колонок.
5. Замените в `MIGRATION_004` умолчание `'робот'` на `'человек'` → падает
   `test_existing_hh_rows_are_attributed_to_the_robot`.

- [ ] **Шаг 13: коммит**

```bash
git add job_monitor/statuses.py job_monitor/db/migrations.py \
        job_monitor/db/repositories.py job_monitor/workers/hh.py \
        tests/test_statuses.py tests/test_migration_004.py \
        структура.txt README.md \
        docs/superpowers/specs/2026-09-08-hardening-and-rework-spec.md
git commit -m "$(cat <<'MSG'
feat(db): словарь статусов найденного и миграция 004

Статусы переезжают в job_monitor/statuses.py — один словарь на оба списка.
Множество DECIDED становится новой дедупликацией (D19), REOPENABLE
запрещает возврат в «новую» из уже отправленного отклика.

Claude-Session: https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB
MSG
)"
```

---

## Задача 2: чтение и запись очередей в репозиториях

**Files:**
- Modify: `job_monitor/db/repositories.py`
- Create: `tests/test_found_repos.py`
- Modify: `tests/test_tg_found_repo.py` (сигнатура `record` изменилась)
- Modify: `tests/test_telegram_worker.py`, `tests/test_preset_driven_workers.py`
  (те же вызовы `record`)

**Interfaces:**
- Consumes: `job_monitor.statuses` (задача 1).
- Produces:
  - `HhRepo.FIELDS` — кортеж, в конце добавлено `"status_source"`
  - `HhRepo.record_found(vacancy: dict) -> bool` — `True`, если строка новая
  - `HhRepo.is_decided(vacancy_id: str) -> bool`
  - `HhRepo.get(vacancy_id: str) -> dict | None`
  - `HhRepo.list_found(*, decided: bool | None = None, limit: int = 50,
    offset: int = 0) -> list[dict]`
  - `HhRepo.set_status(vacancy_id: str, status: str, source: str,
    now: datetime) -> bool`
  - `TgFoundRepo.record(channel: str, message_id: int, usernames: list[str],
    preview: str | None, matched_keyword: str | None, now: datetime) -> bool`
  - `TgFoundRepo.get(found_id: int) -> dict | None`
  - `TgFoundRepo.list(*, decided: bool | None = None, limit: int = 50,
    offset: int = 0) -> list[dict]`
  - `TgFoundRepo.set_status(found_id: int, status: str, now: datetime) -> bool`
  - строки обоих `list`/`get` отдают `usernames` **разобранным списком**,
    а не JSON-строкой.

- [ ] **Шаг 1: напишите падающие тесты**

Создайте `tests/test_found_repos.py`:

```python
"""Очередь найденного на уровне репозиториев.

Две таблицы, один словарь статусов. Проверяется то, на чём стоит вся
остальная задача: строка появляется в момент находки, повторная находка её
не переписывает, а решённость отвечает за дедупликацию.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from job_monitor import statuses
from job_monitor.db.connection import connect
from job_monitor.db.repositories import HhRepo, TgFoundRepo

NOW = datetime(2026, 9, 10, 12, 0, 0)


@pytest.fixture
def conn():
    return connect(":memory:")


def _vacancy(vacancy_id: str = "1", **extra) -> dict:
    """Форма ровно та, что отдаёт настоящий `get_vacancies_from_page`."""
    return {
        "vacancy_id": vacancy_id,
        "title": "QA Engineer",
        "company": "ООО Ромашка",
        "salary": "Не указана",
        "city": "Алматы",
        "url": f"https://hh.ru/vacancy/{vacancy_id}",
        "found_at": "2026-09-10T10:00:00",
        "status": statuses.NEW,
        "status_source": statuses.SOURCE_ROBOT,
        **extra,
    }


# ── hh.ru ─────────────────────────────────────────────────────────────


def test_record_found_inserts_a_new_vacancy(conn) -> None:
    repo = HhRepo(conn)
    assert repo.record_found(_vacancy()) is True
    row = repo.get("1")
    assert row["status"] == statuses.NEW
    assert row["status_source"] == statuses.SOURCE_ROBOT
    assert row["applied_at"] is None


def test_record_found_does_not_touch_an_existing_row(conn) -> None:
    """Иначе `found_at` переписывался бы на каждом круге поиска, и вакансия,
    найденная вчера и всё ещё новая, каждый день считалась бы найденной
    заново — счётчик «найдено сегодня» перестал бы что-либо значить."""
    repo = HhRepo(conn)
    repo.record_found(_vacancy())
    assert repo.record_found(_vacancy(found_at="2026-09-11T10:00:00")) is False
    assert repo.get("1")["found_at"] == "2026-09-10T10:00:00"


def test_record_found_does_not_resurrect_a_decided_vacancy(conn) -> None:
    """Самое дорогое свойство этого метода: перезапись статуса решённой
    вакансии на «новую» означала бы второй отклик тому же работодателю."""
    repo = HhRepo(conn)
    repo.record_found(_vacancy())
    repo.set_status("1", statuses.AUTO_APPLIED, statuses.SOURCE_ROBOT, NOW)
    repo.record_found(_vacancy())
    assert repo.get("1")["status"] == statuses.AUTO_APPLIED


def test_a_caller_that_knows_nothing_of_the_new_column_still_works(conn) -> None:
    """`status_source` объявлен `NOT NULL`, а умолчание SQLite срабатывает
    только когда колонки нет в списке `INSERT`. Наши запросы перечисляют
    все поля поимённо, поэтому без подстановки каждый существующий
    вызывающий — `legacy_import.py` и десяток тестов — начал бы падать на
    `IntegrityError`."""
    repo = HhRepo(conn)
    repo.upsert({
        "vacancy_id": "9", "title": "QA", "status": statuses.AUTO_APPLIED,
        "found_at": "2026-09-10T10:00:00",
    })
    assert repo.get("9")["status_source"] == statuses.SOURCE_ROBOT


def test_is_decided_is_false_for_a_freshly_found_vacancy(conn) -> None:
    repo = HhRepo(conn)
    repo.record_found(_vacancy())
    assert repo.exists("1") is True, "строка есть"
    assert repo.is_decided("1") is False, "но решения по ней ещё нет"


@pytest.mark.parametrize(
    "status", sorted(statuses.DECIDED)
)
def test_is_decided_is_true_for_every_decided_status(conn, status: str) -> None:
    repo = HhRepo(conn)
    repo.record_found(_vacancy())
    repo.set_status("1", status, statuses.SOURCE_ROBOT, NOW)
    assert repo.is_decided("1") is True


def test_is_decided_is_false_for_an_unknown_vacancy(conn) -> None:
    assert HhRepo(conn).is_decided("нет такой") is False


def test_set_status_stamps_applied_at_only_for_an_applied_status(conn) -> None:
    """`applied_at` нужен, чтобы список отправленного шёл в правильном
    порядке. На счётчик он не влияет: `applied_on` фильтрует ещё и по
    статусу — именно это делает решение D16 свойством данных, а не
    отдельной проверкой."""
    repo = HhRepo(conn)
    repo.record_found(_vacancy())
    repo.set_status("1", statuses.MANUAL_APPLIED, statuses.SOURCE_HUMAN, NOW)
    row = repo.get("1")
    assert row["applied_at"] == NOW.isoformat(timespec="seconds")
    assert row["status_source"] == statuses.SOURCE_HUMAN

    repo.record_found(_vacancy("2"))
    repo.set_status("2", statuses.DISMISSED, statuses.SOURCE_HUMAN, NOW)
    assert repo.get("2")["applied_at"] is None


def test_a_manual_application_stays_out_of_the_daily_counter(conn) -> None:
    """Прямая проверка решения D16 на самом счётчике, а не на его форме."""
    repo = HhRepo(conn)
    repo.record_found(_vacancy())
    repo.set_status("1", statuses.MANUAL_APPLIED, statuses.SOURCE_HUMAN, NOW)
    assert repo.applied_on(NOW.date()) == 0
    assert repo.applied_total() == 0

    repo.record_found(_vacancy("2"))
    repo.set_status("2", statuses.AUTO_APPLIED, statuses.SOURCE_ROBOT, NOW)
    assert repo.applied_on(NOW.date()) == 1


def test_set_status_reports_whether_it_found_the_row(conn) -> None:
    repo = HhRepo(conn)
    repo.record_found(_vacancy())
    assert repo.set_status("1", statuses.DISMISSED, statuses.SOURCE_HUMAN, NOW) is True
    assert repo.set_status("нет", statuses.DISMISSED, statuses.SOURCE_HUMAN, NOW) is False


def test_list_found_filters_by_decidedness(conn) -> None:
    repo = HhRepo(conn)
    repo.record_found(_vacancy("1"))
    repo.record_found(_vacancy("2"))
    repo.set_status("2", statuses.DISMISSED, statuses.SOURCE_HUMAN, NOW)

    assert [row["vacancy_id"] for row in repo.list_found(decided=False)] == ["1"]
    assert [row["vacancy_id"] for row in repo.list_found(decided=True)] == ["2"]
    assert {row["vacancy_id"] for row in repo.list_found()} == {"1", "2"}


def test_list_found_pages(conn) -> None:
    repo = HhRepo(conn)
    for index in range(5):
        repo.record_found(_vacancy(str(index), found_at=f"2026-09-1{index}T10:00:00"))
    page = repo.list_found(limit=2, offset=2)
    assert len(page) == 2
    assert [row["vacancy_id"] for row in repo.list_found(limit=2)] == ["4", "3"], (
        "список идёт от свежего к старому"
    )


# ── Telegram ──────────────────────────────────────────────────────────


def test_record_stores_every_contact_of_the_post(conn) -> None:
    """Гранулярность статуса — пост целиком, а контактов в нём бывает
    несколько; без них непонятно, кому писать."""
    repo = TgFoundRepo(conn)
    assert repo.record("qajobs", 42, ["@hr_anna", "@lead"], "ищем QA", "qa", NOW) is True
    row = repo.get(1)
    assert row["usernames"] == ["@hr_anna", "@lead"], "список, а не JSON-строка"
    assert row["status"] == statuses.NEW
    assert row["status_at"] is None


def test_record_accepts_a_post_without_contacts(conn) -> None:
    repo = TgFoundRepo(conn)
    repo.record("qajobs", 42, [], "ищем QA", "qa", NOW)
    assert repo.get(1)["usernames"] == []


def test_the_same_post_is_recorded_once(conn) -> None:
    repo = TgFoundRepo(conn)
    assert repo.record("qajobs", 42, ["@a"], "первый", "qa", NOW) is True
    assert repo.record("qajobs", 42, ["@b"], "второй", "qa", NOW) is False
    assert repo.get(1)["preview"] == "первый"


def test_set_status_stamps_the_time(conn) -> None:
    repo = TgFoundRepo(conn)
    repo.record("qajobs", 42, ["@a"], "ищем QA", "qa", NOW)
    assert repo.set_status(1, statuses.DISMISSED, NOW) is True
    row = repo.get(1)
    assert row["status"] == statuses.DISMISSED
    assert row["status_at"] == NOW.isoformat(timespec="seconds")
    assert repo.set_status(999, statuses.DISMISSED, NOW) is False


def test_tg_list_filters_by_decidedness(conn) -> None:
    repo = TgFoundRepo(conn)
    repo.record("qajobs", 1, [], "первый", "qa", NOW)
    repo.record("qajobs", 2, [], "второй", "qa", NOW)
    repo.set_status(2, statuses.MANUAL_APPLIED, NOW)

    assert [row["message_id"] for row in repo.list(decided=False)] == [1]
    assert [row["message_id"] for row in repo.list(decided=True)] == [2]
    assert len(repo.list()) == 2


def test_tg_list_pages_newest_first(conn) -> None:
    repo = TgFoundRepo(conn)
    for index in range(1, 6):
        repo.record("qajobs", index, [], f"пост {index}", "qa", NOW)
    assert [row["message_id"] for row in repo.list(limit=2)] == [5, 4]
    assert [row["message_id"] for row in repo.list(limit=2, offset=2)] == [3, 2]
```

- [ ] **Шаг 2: прогоните — тесты должны упасть**

Run: `pytest tests/test_found_repos.py -q`
Expected: FAIL — `AttributeError: 'HhRepo' object has no attribute 'record_found'`

- [ ] **Шаг 3: расширьте `HhRepo`**

В `job_monitor/db/repositories.py`:

```python
class HhRepo:
    FIELDS = ("vacancy_id", "title", "company", "salary", "city", "url",
              "found_at", "applied_at", "status", "error", "status_source")
```

В `upsert` оберните собранный словарь тем же умолчанием — иначе каждый
существующий вызывающий (в том числе `job_monitor/legacy_import.py`) начнёт
писать `NULL` в колонку `NOT NULL`:

```python
            merged = self._with_defaults({
                field: (
                    vacancy[field]
                    if field in vacancy
                    else (existing[field] if existing is not None else None)
                )
                for field in self.FIELDS
            })
```

Остальные методы (`applied_on`, `found_on`, `applied_total`, `recent`)
остаются как есть. Добавьте:

```python
    def exists(self, vacancy_id: str) -> bool:
        """Есть ли вообще такая строка.

        **Это НЕ предикат дедупликации.** С тех пор как вакансия попадает в
        базу в момент находки, а не после попытки отклика, «строка есть» и
        «решение принято» — разные вопросы, и подстановка первого вместо
        второго тихо останавливает автоматику: воркер начинает отбрасывать
        то, что сам только что нашёл. Дедуплицирует `is_decided()`.
        """
        row = self._conn.execute(
            "SELECT 1 FROM hh_applications WHERE vacancy_id = ?", (vacancy_id,)
        ).fetchone()
        return row is not None

    def is_decided(self, vacancy_id: str) -> bool:
        """Принято ли по вакансии решение — робота или человека (D19)."""
        row = self._conn.execute(
            "SELECT status FROM hh_applications WHERE vacancy_id = ?", (vacancy_id,)
        ).fetchone()
        return row is not None and row["status"] in statuses.DECIDED

    def get(self, vacancy_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM hh_applications WHERE vacancy_id = ?", (vacancy_id,)
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _with_defaults(values: dict) -> dict:
        """Подставляет значения колонок, объявленных `NOT NULL`.

        `status_source` добавлен миграцией 004 как `NOT NULL DEFAULT
        'робот'`, но умолчание срабатывает только когда колонки НЕТ в
        списке `INSERT`. Наши запросы перечисляют все `FIELDS` поимённо,
        поэтому отсутствующее поле уезжает в базу явным `NULL` — и вставка
        падает на `IntegrityError`. Ловится это не здесь, а у вызывающих,
        которые про новую колонку не знают вовсе: `legacy_import.py` и
        каждый существующий тест, зовущий `upsert` со словарём из четырёх
        ключей.
        """
        filled = dict(values)
        if filled.get("status_source") is None:
            filled["status_source"] = statuses.SOURCE_ROBOT
        if filled.get("status") is None:
            filled["status"] = statuses.NEW
        return filled

    def record_found(self, vacancy: dict) -> bool:
        """Записывает вакансию в момент находки. `True`, если она новая.

        `DO NOTHING`, а не `upsert`: повторный проход по той же странице
        поиска не должен ни переписывать `found_at` (иначе вчерашняя
        находка каждый день считается сегодняшней), ни — что гораздо
        дороже — возвращать решённой вакансии статус «новая». Второе
        означало бы второй отклик тому же работодателю.
        """
        values = self._with_defaults({field: vacancy.get(field) for field in self.FIELDS})
        with transaction(self._conn):
            cursor = self._conn.execute(
                f"INSERT INTO hh_applications ({', '.join(self.FIELDS)})"
                f" VALUES ({', '.join('?' * len(self.FIELDS))})"
                " ON CONFLICT(vacancy_id) DO NOTHING",
                tuple(values[field] for field in self.FIELDS),
            )
        return cursor.rowcount == 1

    def set_status(
        self, vacancy_id: str, status: str, source: str, now: datetime
    ) -> bool:
        """Меняет статус. `applied_at` проставляется только для отклика.

        Ручной отклик тоже получает `applied_at` — он нужен списку
        отправленного для порядка сортировки. На дневной счётчик это не
        влияет: `applied_on()` фильтрует ещё и по статусу, поэтому
        «откликнулся сам» в него не попадает (решение D16 выполняется
        формой данных, а не отдельной проверкой).
        """
        applied_at = (
            now.isoformat(timespec="seconds") if status in statuses.APPLIED else None
        )
        with transaction(self._conn):
            cursor = self._conn.execute(
                "UPDATE hh_applications SET status = ?, status_source = ?,"
                " applied_at = ? WHERE vacancy_id = ?",
                (status, source, applied_at, vacancy_id),
            )
        return cursor.rowcount == 1

    def list_found(
        self, *, decided: bool | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict]:
        """Очередь найденного, от свежего к старому.

        `decided=None` — всё, `False` — только новое, `True` — только
        решённое. Порядок тот же, что у `recent()`: сначала момент отклика,
        а если его нет — момент находки.
        """
        clause, params = "", []
        if decided is not None:
            placeholders = ", ".join("?" * len(statuses.DECIDED))
            operator = "IN" if decided else "NOT IN"
            clause = f" WHERE status {operator} ({placeholders})"
            params = sorted(statuses.DECIDED)
        rows = self._conn.execute(
            "SELECT * FROM hh_applications" + clause
            + " ORDER BY COALESCE(applied_at, found_at) DESC, rowid DESC"
            " LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]
```

Импортируйте `statuses` наверху `repositories.py` (он уже нужен для
`HH_STATUS_APPLIED`).

- [ ] **Шаг 4: расширьте `TgFoundRepo`**

Замените класс целиком:

```python
class TgFoundRepo:
    """Найденное в Telegram: очередь постов, по которым можно откликнуться.

    **Колонка `username` (единственное число) остаётся пустой.** Она
    появилась в миграции 003, когда таблица заводилась только на запись и
    контакт был не нужен; теперь контакты живут в `usernames` JSON-массивом,
    потому что в одном посте их бывает несколько, а решение «не подходит»
    принимается по вакансии, а не по человеку. Удалить старую колонку
    нельзя: `DROP COLUMN` в SQLite означает пересоздание таблицы (см.
    докстринг `job_monitor/db/migrations.py`). Это сознательно оставленный
    долг, а не забытый код.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        """Строка наружу: `usernames` разобран, а не отдан JSON-текстом.

        Та же причина, что у `PresetsRepo._row`: забытый `json.loads` в
        одном из мест вызова — ровно тот дефект, который потом ищут полдня.
        """
        parsed = dict(row)
        raw = parsed.get("usernames")
        parsed["usernames"] = json.loads(raw) if raw else []
        return parsed

    def record(
        self,
        channel: str,
        message_id: int,
        usernames: list[str],
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
                " (channel, message_id, found_at, usernames, preview,"
                "  matched_keyword, status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    channel,
                    message_id,
                    now.isoformat(timespec="seconds"),
                    json.dumps(usernames, ensure_ascii=False),
                    preview,
                    matched_keyword,
                    statuses.NEW,
                ),
            )
        return cursor.rowcount == 1

    def exists(self, channel: str, message_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM tg_found WHERE channel = ? AND message_id = ?",
            (channel, message_id),
        ).fetchone()
        return row is not None

    def get(self, found_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM tg_found WHERE id = ?", (found_id,)
        ).fetchone()
        return self._row(row) if row else None

    def set_status(self, found_id: int, status: str, now: datetime) -> bool:
        with transaction(self._conn):
            cursor = self._conn.execute(
                "UPDATE tg_found SET status = ?, status_at = ? WHERE id = ?",
                (status, now.isoformat(timespec="seconds"), found_id),
            )
        return cursor.rowcount == 1

    def list(
        self, *, decided: bool | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict]:
        clause, params = "", []
        if decided is not None:
            placeholders = ", ".join("?" * len(statuses.DECIDED))
            operator = "IN" if decided else "NOT IN"
            clause = f" WHERE status {operator} ({placeholders})"
            params = sorted(statuses.DECIDED)
        rows = self._conn.execute(
            "SELECT * FROM tg_found" + clause
            + " ORDER BY found_at DESC, id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [self._row(row) for row in rows]

    def recent(self, limit: int) -> list[dict]:
        return self.list(limit=limit)
```

- [ ] **Шаг 5: почините вызовы `record` в существующих тестах**

Три файла передают третьим позиционным аргументом одно имя или `None`:

- `tests/test_tg_found_repo.py` — замените `None`/`"@hr"` на `[]`/`["@hr"]`
  и обновите проверку колонки: было `row["username"]`, стало
  `row["usernames"]`.
- `tests/test_telegram_worker.py`, `tests/test_preset_driven_workers.py` —
  вызовы идут через `process_post`, менять там нечего; проверьте прогоном.

Ничего в этих файлах **не ослабляйте**: если проверка перестала быть
осмысленной после смены формы данных, усильте её, а не удаляйте.

- [ ] **Шаг 6: покажите новую колонку в карточке вакансии**

`tests/test_stored_columns_are_read.py` — ловушка: она параметризована по
`HhRepo.FIELDS` и требует, чтобы каждая колонка либо попадала в карточку
вакансии, либо была внесена в `NOT_ON_THE_CARD` с объяснением. Добавленный
`status_source` без этого шага делает набор красным, и внести его в
исключения нельзя: ослаблять ловушку запрещено, а колонка и правда стоит
показа — это ответ на вопрос «робот откликнулся или я».

В `frontend/app.js`, в `loadHHVacancies`, замените узел бейджа статуса на:

```javascript
        el('span', {
          class: `status-badge ${cls}`,
          style: 'flex-shrink:0',
          text: st + (v.status_source === 'человек' ? ' · вручную' : ''),
        }),
```

Значение приходит из базы, поэтому — через `text:`, как и всё остальное в
этом файле.

Run: `pytest tests/test_stored_columns_are_read.py -q`
Expected: PASS

- [ ] **Шаг 7: прогоните**

Run: `pytest tests/test_found_repos.py tests/test_tg_found_repo.py tests/test_repositories.py -q`
Expected: PASS

- [ ] **Шаг 8: прогоните весь набор**

Run: `make test`
Expected: PASS

- [ ] **Шаг 9: проверьте дискриминирующую силу**

1. В `record_found` замените `DO NOTHING` на `DO UPDATE SET found_at =
   excluded.found_at` → падает `test_record_found_does_not_touch_an_existing_row`.
2. В `record_found` замените `DO NOTHING` на полный `DO UPDATE SET` всех
   полей → падает `test_record_found_does_not_resurrect_a_decided_vacancy`.
3. В `is_decided` замените условие на `row is not None` → падает
   `test_is_decided_is_false_for_a_freshly_found_vacancy`.
4. В `set_status` проставляйте `applied_at` всегда → падает
   `test_set_status_stamps_applied_at_only_for_an_applied_status`.
   **Убедитесь, что `test_a_manual_application_stays_out_of_the_daily_counter`
   при этом остаётся зелёным** — он обязан проверять фильтр по статусу, а не
   отсутствие даты; если он тоже покраснел, значит счётчик держится не на
   том, на чём должен.
5. В `list_found` поменяйте `IN`/`NOT IN` местами → падает
   `test_list_found_filters_by_decidedness`.
6. В `TgFoundRepo._row` верните `parsed` без разбора JSON → падает
   `test_record_stores_every_contact_of_the_post`.
7. Уберите `_with_defaults` из `upsert` → падает
   `test_a_caller_that_knows_nothing_of_the_new_column_still_works`, а с
   ним `tests/test_state_api.py` и `tests/test_legacy_import.py`. Если
   красным стал только последний — значит новый тест держится за счёт
   соседей, и его надо усилить.

- [ ] **Шаг 10: коммит**

```bash
git add job_monitor/db/repositories.py frontend/app.js \
        tests/test_found_repos.py tests/test_tg_found_repo.py
git commit -m "$(cat <<'MSG'
feat(db): чтение и запись очередей найденного

HhRepo.record_found пишет вакансию в момент находки и не трогает уже
существующую строку: иначе found_at переписывался бы каждым кругом поиска,
а решённая вакансия возвращалась бы в статус «новая» — то есть получала бы
второй отклик тому же работодателю.

Claude-Session: https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB
MSG
)"
```

---

## Задача 3: hh-воркер — запись при находке, дедуп по решённости, безопасный режим

**Это самое опасное место подпроекта.** Невнимательность здесь даёт не
падение, а тихую остановку автоматики: воркер начинает отбрасывать то, что
сам нашёл. Поэтому поведение проверяется через настоящий `_blocking_loop`, а
не через самодельный цикл в теле теста.

**Files:**
- Modify: `job_monitor/workers/hh.py:487-600` (`_process_one`, `_blocking_loop`)
- Create: `tests/test_hh_safe_mode.py`
- Modify: `tests/test_hh_worker_loop.py` (докстринги, называющие `repo.exists()`)

**Interfaces:**
- Consumes: `job_monitor.statuses` (задача 1); `HhRepo.record_found`,
  `HhRepo.is_decided` (задача 2).
- Produces: `_blocking_loop` наполняет очередь и уважает `settings.safe_mode`;
  форма записи вакансии — `status=NEW`, `status_source=SOURCE_ROBOT`.

- [ ] **Шаг 1: напишите падающий тест**

Создайте `tests/test_hh_safe_mode.py`:

```python
"""hh-воркер: очередь наполняется, безопасный режим наконец действует.

Три свойства, каждое из которых до этой задачи отсутствовало:

1. Найденная вакансия попадает в базу СРАЗУ, а не после попытки отклика —
   иначе очередь, которую показывает интерфейс, всегда пуста.
2. `safe_mode` читает и hh-воркер (решение D17). До этого он читал его
   только TG-воркер, то есть режима «найти и не откликаться» для hh.ru не
   существовало вовсе, а без него ручной отклик невозможен по построению:
   робот кликает раньше, чем человек увидит вакансию.
3. Дедупликация идёт по РЕШЁННОСТИ статуса, а не по наличию строки
   (решение D19) — прямое следствие пункта 1.

Всё проверяется на настоящем `_blocking_loop`: подменены только Selenium и
сон между вакансиями.
"""

from __future__ import annotations

import threading
from datetime import date, datetime

import pytest

import job_monitor.workers.hh as hh
from job_monitor import presets, statuses
from job_monitor.db.connection import connect
from job_monitor.db.repositories import HhRepo, SettingsRepo
from job_monitor.settings import GlobalSettings


class FakeDriver:
    """Ровно то, что от драйвера просит `_blocking_loop`."""

    def __init__(self) -> None:
        self.visited: list[str] = []
        self.quit_called = False

    def get(self, url: str) -> None:
        self.visited.append(url)

    def quit(self) -> None:
        self.quit_called = True


def _vacancy(vacancy_id: str, title: str) -> dict:
    """Форма ровно та, что отдаёт настоящий `get_vacancies_from_page`."""
    return {
        "vacancy_id": vacancy_id,
        "title": title,
        "company": "ООО Ромашка",
        "salary": "Не указана",
        "city": "Алматы",
        "url": f"https://hh.ru/vacancy/{vacancy_id}",
        "found_at": "2026-09-10T10:00:00",
    }


def _run_loop(monkeypatch, *, safe_mode: bool, vacancies: list[dict],
              professions: list[str]) -> dict:
    conn = connect(":memory:")
    settings = GlobalSettings(hh_delay_min=1, hh_delay_max=1, safe_mode=safe_mode)
    SettingsRepo(conn).save(settings.model_dump())
    presets.save_criteria(
        conn,
        presets.ensure_default(conn, datetime(2026, 9, 10, 12, 0, 0)),
        {"professions": professions},
    )
    monkeypatch.setattr("job_monitor.db.connection.connect", lambda *_a, **_k: conn)

    clicks: list[dict] = []

    def apply(_driver, vacancy, _criteria, _settings) -> bool:
        clicks.append(vacancy)
        return True

    driver = FakeDriver()
    monkeypatch.setattr(hh, "apply_to_vacancy", apply)
    monkeypatch.setattr(hh, "setup_driver", lambda headless=False: driver)
    monkeypatch.setattr(hh, "load_cookies", lambda _driver, _target: True)
    monkeypatch.setattr(hh, "is_logged_in", lambda _driver: True)
    monkeypatch.setattr(
        hh, "build_search_url", lambda profession, *_a, **_k: f"https://hh.ru/{profession}"
    )
    monkeypatch.setattr(
        hh, "get_vacancies_from_page",
        lambda _driver, _criteria: [dict(item) for item in vacancies],
    )

    slept: list[float] = []

    def fake_sleep(stop_event: threading.Event, seconds: float) -> bool:
        slept.append(seconds)
        if seconds == settings.hh_check_interval:
            stop_event.set()
            return False
        return True

    monkeypatch.setattr(hh, "_interruptible_sleep", fake_sleep)
    hh._blocking_loop(threading.Event())
    return {"conn": conn, "clicks": clicks, "driver": driver, "slept": slept}


# ── Безопасный режим ──────────────────────────────────────────────────


@pytest.fixture
def safe_run(monkeypatch):
    return _run_loop(
        monkeypatch, safe_mode=True,
        vacancies=[_vacancy("1", "QA Engineer")], professions=["QA"],
    )


def test_safe_mode_fills_the_queue(safe_run) -> None:
    rows = HhRepo(safe_run["conn"]).list_found()
    assert len(rows) == 1
    assert rows[0]["vacancy_id"] == "1"
    assert rows[0]["status"] == statuses.NEW
    assert rows[0]["status_source"] == statuses.SOURCE_ROBOT


def test_safe_mode_sends_nothing(safe_run) -> None:
    """Смысл режима одной строкой: ищем и складываем, наружу не уходит
    ничего."""
    assert safe_run["clicks"] == []
    assert HhRepo(safe_run["conn"]).applied_on(date.today()) == 0


def test_safe_mode_really_walked_the_search_page(safe_run) -> None:
    """Страховка от вакуумности: если бы цикл не дошёл до страницы поиска,
    обе проверки выше были бы зелёными ни о чём."""
    assert safe_run["driver"].visited == ["https://hh.ru/QA"]
    assert safe_run["driver"].quit_called, "драйвер не закрыт в finally"


def test_safe_mode_does_not_pause_between_vacancies(safe_run) -> None:
    """Пауза существует, чтобы не долбить hh.ru откликами. Отклика не было —
    платить минутами не за что."""
    assert safe_run["slept"] == [GlobalSettings().hh_check_interval]


# ── Обычный режим ─────────────────────────────────────────────────────


@pytest.fixture
def live_run(monkeypatch):
    return _run_loop(
        monkeypatch, safe_mode=False,
        vacancies=[_vacancy("1", "QA Engineer")], professions=["QA"],
    )


def test_without_safe_mode_the_worker_applies(live_run) -> None:
    assert [item["vacancy_id"] for item in live_run["clicks"]] == ["1"]
    row = HhRepo(live_run["conn"]).get("1")
    assert row["status"] == statuses.AUTO_APPLIED
    assert row["status_source"] == statuses.SOURCE_ROBOT


# ── Дедупликация по решённости ────────────────────────────────────────


def test_a_new_vacancy_is_not_skipped_by_its_own_queue_row(monkeypatch) -> None:
    """Сердце решения D19.

    Две профессии — значит цикл проходит по одной и той же вакансии дважды.
    К моменту второго прохода строка в базе уже ЕСТЬ: её положил первый
    проход в момент находки. Дедуп «строка есть → пропустить» отбросил бы
    вакансию, по которой ничего не решено, и отклик не ушёл бы никогда.
    """
    run = _run_loop(
        monkeypatch, safe_mode=True,
        vacancies=[_vacancy("1", "QA Engineer")],
        professions=["QA", "тестировщик"],
    )
    assert run["driver"].visited == ["https://hh.ru/QA", "https://hh.ru/тестировщик"]
    rows = HhRepo(run["conn"]).list_found()
    assert len(rows) == 1, "вакансия записана один раз, а не по разу на профессию"
    assert rows[0]["status"] == statuses.NEW, (
        "статус остался «новой» — второй проход не должен был ничего решать"
    )


def test_a_decided_vacancy_is_skipped(monkeypatch) -> None:
    """Обратная сторона: по вакансии, где решение уже есть, второго клика
    быть не должно."""
    run = _run_loop(
        monkeypatch, safe_mode=False,
        vacancies=[_vacancy("1", "QA Engineer")],
        professions=["QA", "тестировщик"],
    )
    assert len(run["clicks"]) == 1, (
        f"по вакансии кликнули больше одного раза: {run['clicks']}"
    )


def test_a_manually_dismissed_vacancy_is_never_touched(monkeypatch) -> None:
    """Ради чего всё и делается: человек сказал «не подходит», и робот
    больше не откликается."""
    conn = connect(":memory:")
    HhRepo(conn).record_found({
        **_vacancy("1", "QA Engineer"),
        "status": statuses.DISMISSED,
        "status_source": statuses.SOURCE_HUMAN,
    })
    monkeypatch.setattr("job_monitor.db.connection.connect", lambda *_a, **_k: conn)

    run = _run_loop(
        monkeypatch, safe_mode=False,
        vacancies=[_vacancy("1", "QA Engineer")], professions=["QA"],
    )
    assert run["clicks"] == []
    assert HhRepo(run["conn"]).get("1")["status"] == statuses.DISMISSED


def test_the_found_event_fires_once_per_vacancy(monkeypatch) -> None:
    """Событие «найдено» нужно ленте последних действий, особенно в
    безопасном режиме, где больше ничего не происходит. Но повторный проход
    по той же вакансии не должен засорять ленту — иначе за сутки лента
    состоит из одной вакансии, переоткрытой сто раз."""
    run = _run_loop(
        monkeypatch, safe_mode=True,
        vacancies=[_vacancy("1", "QA Engineer")],
        professions=["QA", "тестировщик"],
    )
    kinds = [
        row["kind"]
        for row in run["conn"].execute(
            "SELECT kind FROM worker_events WHERE worker = 'hh'"
        ).fetchall()
    ]
    assert kinds == ["found"]
```

- [ ] **Шаг 2: прогоните — тесты должны упасть**

Run: `pytest tests/test_hh_safe_mode.py -q`
Expected: FAIL — очередь пуста (`test_safe_mode_fills_the_queue`), клик
случился (`test_safe_mode_sends_nothing`)

- [ ] **Шаг 3: научите `_process_one` называть источник**

В `job_monitor/workers/hh.py` в обоих вызовах `repo.upsert` внутри
`_process_one` добавьте `"status_source": statuses.SOURCE_ROBOT`:

```python
        repo.upsert({
            **vacancy,
            "status": HH_STATUS_SCENARIO_ERROR,
            "status_source": statuses.SOURCE_ROBOT,
            "error": str(error),
        })
```

```python
    repo.upsert({
        **vacancy,
        "status": HH_STATUS_APPLIED if applied else statuses.SKIPPED,
        "status_source": statuses.SOURCE_ROBOT,
        "applied_at": datetime.now().isoformat(timespec="seconds") if applied else None,
    })
```

Источник указывается явно, потому что `upsert` — слияние: без этой строки
вакансия, которую человек когда-то вернул в очередь (источник «человек»),
после автоматического отклика осталась бы подписана человеком. Добавьте это
объяснение комментарием.

Литерал `"пропущено"` заменён на `statuses.SKIPPED` — то же значение, но из
одного места.

- [ ] **Шаг 4: перепишите тело внутреннего цикла**

В `_blocking_loop` замените блок от проверки лимита до вызова
`_interruptible_sleep`:

```python
                settings = load_settings(conn)
                criteria = active_criteria(conn)
                # Лимит не мешает СОБИРАТЬ. В безопасном режиме отклик не
                # уходит, `applied_on` не растёт, и уснуть на десять минут
                # означало бы перестать наполнять очередь — то есть ровно
                # то, ради чего режим и включают.
                if (
                    not settings.safe_mode
                    and repo.applied_on(date.today()) >= settings.hh_max_per_day
                ):
                    if not _interruptible_sleep(stop_event, 600):
                        return
                    continue
                for profession in criteria.professions:
                    if stop_event.is_set():
                        return
                    driver.get(build_search_url(profession, criteria))
                    for vacancy in get_vacancies_from_page(driver, criteria):
                        if stop_event.is_set():
                            return
                        # Дедуп по РЕШЁННОСТИ, а не по наличию строки
                        # (решение D19). Строка теперь появляется в момент
                        # находки — строкой ниже, — поэтому `repo.exists()`
                        # здесь отбрасывал бы вакансию, которую воркер сам
                        # только что записал, и отклик не ушёл бы никогда.
                        # Это не падение, а тихая остановка автоматики:
                        # поведение закреплено на настоящем цикле в
                        # tests/test_hh_safe_mode.py.
                        if repo.is_decided(vacancy["vacancy_id"]):
                            continue
                        # Запись ДО всякой попытки отклика — это и есть
                        # наполнение очереди, которую показывает интерфейс.
                        # `record_found` не трогает уже существующую строку,
                        # поэтому повторный проход по той же странице не
                        # переписывает `found_at` и не воскрешает статус.
                        if repo.record_found({
                            **vacancy,
                            "status": statuses.NEW,
                            "status_source": statuses.SOURCE_ROBOT,
                        }):
                            events.add("hh", "found", vacancy["title"], datetime.now())
                        if settings.safe_mode:
                            # Решение D17: один переключатель на оба воркера.
                            # Ищем и складываем, наружу не уходит ничего —
                            # и паузы между вакансиями не платим, платить
                            # не за что.
                            continue
                        _process_one(
                            driver, vacancy, criteria, settings, repo, events
                        )
                        if not _interruptible_sleep(
                            stop_event,
                            random.randint(
                                min(settings.hh_delay_min, settings.hh_delay_max),
                                max(settings.hh_delay_min, settings.hh_delay_max),
                            ),
                        ):
                            return
```

Комментарий про `min`/`max` над `random.randint` сохраните — он объясняет
существующую защиту от `hh_delay_min > hh_delay_max`.

- [ ] **Шаг 5: обновите докстринги, называющие `repo.exists()`**

В `tests/test_hh_worker_loop.py` докстринг модуля и докстринг
`test_the_loop_clicks_a_broken_vacancy_once_and_then_skips_it` обещают
дедуп «через `repo.exists()`». Замените на `repo.is_decided()`. Проверки не
трогайте — они остаются в силе и должны продолжать проходить.

В `job_monitor/workers/hh.py` то же в двух местах: докстринг
`get_vacancies_from_page` (строка про `HhRepo.exists()`/`upsert()`) и
комментарий в `_process_one` про «`repo.exists()` станет True».

В `tests/test_hh_filters.py:151` тот же текст в докстринге.

- [ ] **Шаг 6: прогоните**

Run: `pytest tests/test_hh_safe_mode.py tests/test_hh_worker_loop.py tests/test_hh_worker_resilience.py -q`
Expected: PASS

- [ ] **Шаг 7: прогоните весь набор**

Run: `make test`
Expected: PASS

- [ ] **Шаг 8: проверьте дискриминирующую силу**

1. Верните `if repo.exists(vacancy["vacancy_id"]): continue` вместо
   `is_decided` → падает
   `test_a_new_vacancy_is_not_skipped_by_its_own_queue_row`. **Это главная
   мутация задачи: убедитесь, что тест действительно краснеет.**
2. Уберите `if settings.safe_mode: continue` → падает
   `test_safe_mode_sends_nothing`.
3. Перенесите `record_found` после `_process_one` → падает
   `test_safe_mode_fills_the_queue`.
4. Уберите условие `not settings.safe_mode` из проверки лимита и задайте в
   тесте `hh_max_per_day=1` с одной уже отправленной вакансией — очередь
   перестаёт наполняться. Если ни один тест не покраснел, допишите такой
   тест: свойство «лимит не мешает собирать» иначе не закреплено.
5. Уберите `"status_source": statuses.SOURCE_ROBOT` из `_process_one` →
   падает `test_without_safe_mode_the_worker_applies`.
6. Уберите условие вокруг `events.add("hh", "found", ...)` → падает
   `test_the_found_event_fires_once_per_vacancy`.

- [ ] **Шаг 9: коммит**

```bash
git add job_monitor/workers/hh.py tests/test_hh_safe_mode.py \
        tests/test_hh_worker_loop.py tests/test_hh_filters.py
git commit -m "$(cat <<'MSG'
feat(hh): очередь найденного и безопасный режим у hh-воркера

Вакансия записывается в момент находки, а не после попытки отклика, и
дедупликация идёт по решённости статуса (D19), а не по наличию строки:
иначе воркер отбрасывал бы то, что сам только что нашёл. safe_mode начинает
действовать и на hh.ru (D17) — без него ручной отклик невозможен по
построению.

Claude-Session: https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB
MSG
)"
```

---

## Задача 4: TG-воркер — контакты в очереди и статус после отправки

**Files:**
- Modify: `job_monitor/workers/telegram.py` (`process_post`, `handler`)
- Modify: `job_monitor/db/repositories.py` (`TgFoundRepo.set_post_status`)
- Modify: `tests/test_telegram_worker.py` (новые проверки)

**Interfaces:**
- Consumes: `job_monitor.statuses`; `TgFoundRepo.record(..., usernames, ...)`,
  `TgFoundRepo.get`, `TgFoundRepo.exists` (задача 2).
- Produces:
  - `TgFoundRepo.set_post_status(channel: str, message_id: int, status: str,
    now: datetime) -> bool` — воркер знает пару (канал, сообщение), а не `id`
  - `tg_found.usernames` заполняется реальными контактами поста
  - пост, по которому робот написал хотя бы одному контакту, получает статус
    `AUTO_APPLIED`

- [ ] **Шаг 1: напишите падающие тесты**

Допишите в `tests/test_telegram_worker.py` (файл уже содержит фикстуры
`conn`, `_pp` и обёртку вокруг `process_post` — используйте их; если имена
отличаются, подстройтесь под то, что там есть, ничего не удаляя):

```python
# ── Очередь найденного ────────────────────────────────────────────────


def test_the_post_lands_in_the_queue_with_its_contacts(conn) -> None:
    """Колонка контактов до этой задачи всегда была пуста: таблица
    заводилась на запись, и `process_post` передавал туда `None`. Для
    очереди контакт нужен — иначе непонятно, кому писать."""
    from job_monitor.db.repositories import TgFoundRepo

    post = IncomingPost(channel="qajobs", text="Ищем QA @hr_anna @lead", message_id=42)
    criteria = SearchCriteria(channels=["qajobs"], tg_keywords=["qa"], template="привет")
    settings = GlobalSettings(safe_mode=True)

    asyncio.run(process_post(
        post, criteria, settings, TgRepo(conn), TgFoundRepo(conn), _never_sends
    ))

    row = TgFoundRepo(conn).get(1)
    assert row["usernames"] == ["@hr_anna", "@lead"]
    assert row["matched_keyword"] == "qa"
    assert row["status"] == statuses.NEW


def test_safe_mode_leaves_the_post_new(conn) -> None:
    """Смысл безопасного режима: пост попал в очередь и ждёт человека."""
    from job_monitor.db.repositories import TgFoundRepo

    post = IncomingPost(channel="qajobs", text="Ищем QA @hr_anna", message_id=42)
    criteria = SearchCriteria(channels=["qajobs"], tg_keywords=["qa"], template="привет")

    sent = asyncio.run(process_post(
        post, criteria, GlobalSettings(safe_mode=True),
        TgRepo(conn), TgFoundRepo(conn), _never_sends,
    ))

    assert sent == []
    assert TgFoundRepo(conn).get(1)["status"] == statuses.NEW


def test_a_sent_post_is_marked_as_answered_by_the_robot(conn) -> None:
    """Без этого пост, на который робот уже написал, остался бы в очереди
    «новым» — и человек написал бы тому же контакту второй раз."""
    from job_monitor.db.repositories import TgFoundRepo

    delivered: list[str] = []

    async def sender(username, text, attachment):
        delivered.append(username)

    post = IncomingPost(channel="qajobs", text="Ищем QA @hr_anna", message_id=42)
    criteria = SearchCriteria(channels=["qajobs"], tg_keywords=["qa"], template="привет")

    asyncio.run(process_post(
        post, criteria, GlobalSettings(safe_mode=False, delay_min=1, delay_max=1),
        TgRepo(conn), TgFoundRepo(conn), sender,
    ))

    assert delivered == ["@hr_anna"]
    row = TgFoundRepo(conn).get(1)
    assert row["status"] == statuses.AUTO_APPLIED
    assert row["status_at"] is not None


def test_a_post_nobody_could_be_written_to_stays_new(conn) -> None:
    """Все контакты уже написаны раньше — робот ничего не решил, значит
    решать человеку. `SKIPPED` здесь был бы неправдой: это слово робота о
    вакансии, которую он рассмотрел и отверг."""
    from job_monitor.db.repositories import TgFoundRepo

    TgRepo(conn).ensure_contact("@hr_anna", datetime(2026, 9, 1, 12, 0, 0))
    post = IncomingPost(channel="qajobs", text="Ищем QA @hr_anna", message_id=42)
    criteria = SearchCriteria(channels=["qajobs"], tg_keywords=["qa"], template="привет")

    sent = asyncio.run(process_post(
        post, criteria, GlobalSettings(safe_mode=False, delay_min=1, delay_max=1),
        TgRepo(conn), TgFoundRepo(conn), _never_sends,
    ))

    assert sent == []
    assert TgFoundRepo(conn).get(1)["status"] == statuses.NEW
```

`_never_sends` — вспомогательная корутина, падающая при вызове:

```python
async def _never_sends(username, text, attachment):
    raise AssertionError(f"отправка не должна была случиться: {username}")
```

Импорты (`asyncio`, `datetime`, `statuses`, `IncomingPost`, `process_post`,
`SearchCriteria`, `GlobalSettings`, `TgRepo`) добавьте к уже имеющимся в
файле, не дублируя.

- [ ] **Шаг 2: прогоните — тесты должны упасть**

Run: `pytest tests/test_telegram_worker.py -q`
Expected: FAIL — `assert [] == ['@hr_anna', '@lead']` (контакты не пишутся)

- [ ] **Шаг 3: добавьте `set_post_status` в репозиторий**

В `job_monitor/db/repositories.py`, класс `TgFoundRepo`:

```python
    def set_post_status(
        self, channel: str, message_id: int, status: str, now: datetime
    ) -> bool:
        """Смена статуса по паре (канал, сообщение), а не по `id`.

        Воркер знает пост именно этой парой — она же и есть ключ
        уникальности таблицы. Просить у него `id` означало бы лишний SELECT
        ровно за тем, чтобы тут же сделать UPDATE.
        """
        with transaction(self._conn):
            cursor = self._conn.execute(
                "UPDATE tg_found SET status = ?, status_at = ?"
                " WHERE channel = ? AND message_id = ?",
                (status, now.isoformat(timespec="seconds"), channel, message_id),
            )
        return cursor.rowcount == 1
```

- [ ] **Шаг 4: научите `process_post` писать контакты и статус**

В `job_monitor/workers/telegram.py`. Замените блок дедупликации:

```python
    preview = post.text[:80].strip()
    # Контакты пишутся в очередь, а не только используются для отправки:
    # без них экран «Найдено» не может сказать, кому писать. Гранулярность
    # статуса при этом — пост целиком, а не отдельный контакт: пост это
    # единица находки, и решение «не подходит» принимается по вакансии, а
    # не по человеку.
    contacts = extract_usernames(post.text)
    first_time = found_repo.record(
        post.channel, post.message_id, contacts, preview, keyword, clock()
    )
    if not first_time:
        return []
```

Внутри цикла отправки замените `extract_usernames(post.text)` на `contacts`
(список уже посчитан).

И в самом конце функции, перед `return sent`:

```python
    if sent:
        # Пост, на который робот уже написал, не должен оставаться в
        # очереди «новым»: человек увидел бы его как неотвеченный и написал
        # бы тому же контакту второй раз. Если отправить не удалось никому
        # (все контакты уже написаны раньше, либо ни один не прошёл отбор),
        # статус остаётся «новая» — робот ничего не решил, значит решать
        # человеку. «Пропущено» здесь было бы неправдой: это слово робота о
        # вакансии, которую он рассмотрел и отверг.
        found_repo.set_post_status(
            post.channel, post.message_id, statuses.AUTO_APPLIED, clock()
        )
    return sent
```

Импортируйте `statuses` наверху файла.

- [ ] **Шаг 5: перестаньте считать повторный пост находкой**

В `run_worker`, внутри `handler`, заменa:

```python
        settings = load_settings(conn)
        criteria = active_criteria(conn)
        if post_matches(post, criteria) is not None and not found_repo.exists(
            post.channel, post.message_id
        ):
            # Метрика «вакансий найдено» берётся отсюда, а не из текста логов (L2).
            # Проверка на повтор — потому что событие пишется ДО process_post,
            # который сам дедуплицирует пост: без неё перечитанный на
            # следующем круге пост считался бы найденным заново, и за сутки
            # метрика состояла бы из одной вакансии, посчитанной сто раз.
            EventsRepo(conn).add("tg", "vacancy", post.text[:80].strip(), datetime.now())
```

- [ ] **Шаг 6: прогоните**

Run: `pytest tests/test_telegram_worker.py tests/test_preset_driven_workers.py -q`
Expected: PASS

- [ ] **Шаг 7: прогоните весь набор**

Run: `make test`
Expected: PASS

- [ ] **Шаг 8: проверьте дискриминирующую силу**

1. Передайте в `record` пустой список вместо `contacts` → падает
   `test_the_post_lands_in_the_queue_with_its_contacts`.
2. Уберите `if sent:` и ставьте `AUTO_APPLIED` всегда → падает
   `test_a_post_nobody_could_be_written_to_stays_new`.
3. Уберите вызов `set_post_status` целиком → падает
   `test_a_sent_post_is_marked_as_answered_by_the_robot`.
4. Поставьте `AUTO_APPLIED` до цикла отправки, а не после → падает
   `test_safe_mode_leaves_the_post_new`.

- [ ] **Шаг 9: коммит**

```bash
git add job_monitor/workers/telegram.py job_monitor/db/repositories.py \
        tests/test_telegram_worker.py
git commit -m "$(cat <<'MSG'
feat(tg): контакты поста в очереди и статус после отправки

tg_found.usernames перестаёт быть всегда пустой колонкой, а пост, на
который робот написал, помечается «отклик отправлен» — иначе человек увидел
бы его как неотвеченный и написал бы тому же контакту второй раз.

Claude-Session: https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB
MSG
)"
```

---

## Задача 5: API очереди Telegram

**Files:**
- Create: `api/found_routes.py`
- Create: `tests/test_found_api.py`
- Modify: `api/main.py` (импорт и `include_router`)
- Modify: `структура.txt`, `README.md`, спецификация укрепления §4

**Interfaces:**
- Consumes: `job_monitor.statuses`; `TgFoundRepo.list/get/set_status`
  (задача 2); `TgRepo.ensure_contact` (существует).
- Produces:
  - `api.found_routes.router` с префиксом `/api/found`
  - `api.found_routes.check_transition(current: str, target: str) -> None`
    — общая проверка перехода, поднимает `HTTPException`; задача 6
    переиспользует её для hh.ru
  - `GET /api/found/tg?status=all|new|decided&limit=&offset=`
  - `PATCH /api/found/tg/{found_id}` с телом `{"status": "..."}`

- [ ] **Шаг 1: напишите падающие тесты**

Создайте `tests/test_found_api.py`:

```python
"""Очередь найденного через API: чтение, ручные статусы, запреты.

Главное, что здесь проверяется, — не форма ответа, а два запрета. Первый:
«отклик отправлен» руками не ставится, иначе счётчик отправленного
перестаёт быть правдой. Второй: вернуть в очередь уже отправленный отклик
нельзя — «новая» означает «обработать», то есть второй отклик тому же
работодателю.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from job_monitor import statuses
from job_monitor.db.connection import get_connection
from job_monitor.db.repositories import TgFoundRepo, TgRepo

NOW = datetime(2026, 9, 10, 12, 0, 0)


@pytest.fixture
def tg_post(client):
    """Один пост в очереди. `client` уже поднял приложение и базу.

    Соединение берётся тем же `get_connection()`, которым пользуются
    роуты: база у прогона одна (`JOB_MONITOR_DATA_DIR` из conftest), и
    писать в неё в обход приложения незачем. Если в наборе уже есть
    фикстура соединения — возьмите её вместо этой строки.
    """
    conn = get_connection()
    conn.execute("DELETE FROM tg_found")
    conn.execute("DELETE FROM tg_contacts")
    repo = TgFoundRepo(conn)
    repo.record("qajobs", 42, ["@hr_anna", "@lead"], "Ищем QA", "qa", NOW)
    return repo.list()[0]["id"]


def test_the_list_returns_the_post_with_a_ready_made_link(client, tg_post) -> None:
    """Ссылку строит бэкенд: фронтенду незачем знать формат чужих URL, а
    гвард схемы в `el()` остаётся единственной точкой проверки."""
    rows = client.get("/api/found/tg").json()
    assert len(rows) == 1
    assert rows[0]["link"] == "https://t.me/qajobs/42"
    assert rows[0]["usernames"] == ["@hr_anna", "@lead"]
    assert rows[0]["status"] == statuses.NEW
    assert rows[0]["preview"] == "Ищем QA"


def test_the_list_does_not_leak_the_dead_column(client, tg_post) -> None:
    """`tg_found.username` (единственное число) остался от миграции 003 и
    всегда пуст. Отдавать его наружу — значит однажды на него опереться."""
    assert "username" not in client.get("/api/found/tg").json()[0]


def test_the_list_filters_by_status(client, tg_post) -> None:
    assert len(client.get("/api/found/tg?status=new").json()) == 1
    assert client.get("/api/found/tg?status=decided").json() == []

    client.patch(f"/api/found/tg/{tg_post}", json={"status": statuses.DISMISSED})

    assert client.get("/api/found/tg?status=new").json() == []
    assert len(client.get("/api/found/tg?status=decided").json()) == 1


def test_an_unknown_filter_is_refused(client, tg_post) -> None:
    assert client.get("/api/found/tg?status=всякое").status_code == 422


def test_marking_it_dismissed_only_changes_the_status(client, tg_post) -> None:
    """Пост уже дедуплицирован парой (канал, сообщение); контакты в
    `tg_contacts` не попадают — человек может встретиться в другой
    вакансии, которая подойдёт."""
    reply = client.patch(f"/api/found/tg/{tg_post}", json={"status": statuses.DISMISSED})
    assert reply.status_code == 200
    assert reply.json()["status"] == "saved"

    conn = get_connection()
    assert TgFoundRepo(conn).get(tg_post)["status"] == statuses.DISMISSED
    assert TgRepo(conn).contacts_total() == 0


def test_marking_it_answered_registers_every_contact(client, tg_post) -> None:
    """Человек написал сам — воркер больше не должен писать этим людям.
    `ensure_contact` написан ровно для случая «контакт известен, отправки
    не было»."""
    client.patch(f"/api/found/tg/{tg_post}", json={"status": statuses.MANUAL_APPLIED})

    conn = get_connection()
    repo = TgRepo(conn)
    assert repo.was_sent("@hr_anna") is True
    assert repo.was_sent("@lead") is True


def test_a_manual_answer_does_not_touch_the_daily_counter(client, tg_post) -> None:
    """Решение D16. Записи в `tg_sends` не создаётся, поэтому «отправлено
    сегодня» не растёт — отметка задним числом не должна останавливать
    воркера на весь день."""
    conn = get_connection()
    before = TgRepo(conn).sent_on(datetime.now().date())

    client.patch(f"/api/found/tg/{tg_post}", json={"status": statuses.MANUAL_APPLIED})

    assert TgRepo(conn).sent_on(datetime.now().date()) == before


def test_the_robot_status_cannot_be_set_by_hand(client, tg_post) -> None:
    reply = client.patch(f"/api/found/tg/{tg_post}", json={"status": statuses.AUTO_APPLIED})
    assert reply.status_code == 400
    assert "робот" in reply.json()["detail"]
    assert TgFoundRepo(get_connection()).get(tg_post)["status"] == statuses.NEW


def test_an_unknown_status_is_refused(client, tg_post) -> None:
    reply = client.patch(f"/api/found/tg/{tg_post}", json={"status": "почти откликнулся"})
    assert reply.status_code == 400
    assert TgFoundRepo(get_connection()).get(tg_post)["status"] == statuses.NEW


def test_an_extra_field_is_refused(client, tg_post) -> None:
    """`extra="forbid"`: опечатка в имени поля не должна молча ничего не
    делать и отвечать «сохранено»."""
    reply = client.patch(
        f"/api/found/tg/{tg_post}", json={"status": statuses.DISMISSED, "статус": "ага"}
    )
    assert reply.status_code == 422


def test_a_dismissed_post_can_be_returned_to_the_queue(client, tg_post) -> None:
    """Передумал."""
    client.patch(f"/api/found/tg/{tg_post}", json={"status": statuses.DISMISSED})
    reply = client.patch(f"/api/found/tg/{tg_post}", json={"status": statuses.NEW})
    assert reply.status_code == 200
    assert TgFoundRepo(get_connection()).get(tg_post)["status"] == statuses.NEW


def test_an_answered_post_cannot_be_returned_to_the_queue(client, tg_post) -> None:
    """Самый дорогой запрет подпроекта: «новая» означает «обработать», то
    есть второй отклик тому же работодателю. Для Telegram от этого защищает
    дедупликация контактов, для hh.ru — ничто, поэтому запрет ставится
    здесь, на уровне роута, одинаково для обоих списков."""
    client.patch(f"/api/found/tg/{tg_post}", json={"status": statuses.MANUAL_APPLIED})
    reply = client.patch(f"/api/found/tg/{tg_post}", json={"status": statuses.NEW})
    assert reply.status_code == 400
    assert "отклик уже отправлен" in reply.json()["detail"]
    assert TgFoundRepo(get_connection()).get(tg_post)["status"] == statuses.MANUAL_APPLIED


def test_patching_a_missing_post_is_404(client, tg_post) -> None:
    assert client.patch(
        "/api/found/tg/999999", json={"status": statuses.DISMISSED}
    ).status_code == 404
```

- [ ] **Шаг 2: прогоните — тесты должны упасть**

Run: `pytest tests/test_found_api.py -q`
Expected: FAIL — 404 на `/api/found/tg` (роута нет)

- [ ] **Шаг 3: создайте `api/found_routes.py`**

```python
"""Очередь найденного: два списка и ручная смена статуса.

Списки раздельные (решение D14): у вакансии hh.ru есть название, компания и
зарплата, у поста в Telegram — канал и контакт для лички. Общая форма
записи вынудила бы половину полей держать пустыми.

**Ссылку строит бэкенд.** Для hh.ru она уже лежит в `url`; для Telegram
собирается здесь из канала и идентификатора сообщения. Фронтенду незачем
знать формат чужих URL, а гвард схемы в `el()` остаётся единственной точкой
проверки — и проверять ему проще одно поле, чем правило склейки.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from job_monitor import statuses
from job_monitor.db.connection import get_connection
from job_monitor.db.repositories import TgFoundRepo, TgRepo

router = APIRouter(prefix="/api/found", tags=["found"])

StatusFilter = Literal["all", "new", "decided"]


class StatusPatch(BaseModel):
    """Тело `PATCH`. `extra="forbid"`, чтобы опечатка в имени поля не
    оборачивалась молчаливым «сохранено» без единого изменения."""

    model_config = ConfigDict(extra="forbid")

    status: str


def _decided_flag(status: StatusFilter) -> bool | None:
    return {"all": None, "new": False, "decided": True}[status]


def check_transition(current: str, target: str) -> None:
    """Проверяет, что человек вправе поставить `target` вместо `current`.

    Общая на оба списка: правила не зависят от того, где лежит запись, а
    две копии одного правила расходятся при первой же правке.
    """
    if target == statuses.AUTO_APPLIED:
        raise HTTPException(
            status_code=400,
            detail="«отклик отправлен» ставит только робот: этот статус — "
            "источник счётчика отправленного за день, и рука человека "
            "сделала бы его неправдой",
        )
    if target in statuses.MANUAL:
        return
    if target == statuses.NEW:
        if current in statuses.REOPENABLE or current == statuses.NEW:
            return
        if current in statuses.APPLIED:
            raise HTTPException(
                status_code=400,
                detail="отклик уже отправлен — вернуть запись в очередь нельзя: "
                "«новая» означает «обработать», то есть робот отправил бы "
                "второй отклик тому же работодателю",
            )
        # Единственный оставшийся случай — «ошибка сценария». Он выглядит
        # как «робот не смог, пусть попробует снова», но кнопка
        # «Откликнуться» к тому моменту уже нажата на живом hh.ru: сценарий
        # ломается ПОСЛЕ клика (см. `_process_one` в workers/hh.py).
        raise HTTPException(
            status_code=400,
            detail="сценарий сломался уже после клика по «Откликнуться» — "
            "повторная обработка кликнула бы второй раз; поправьте сценарий "
            "и дождитесь следующей вакансии",
        )
    raise HTTPException(
        status_code=400,
        detail=f"статус {target!r} нельзя поставить вручную; допустимы: "
        f"{', '.join(sorted(statuses.MANUAL | {statuses.NEW}))}",
    )


def _tg_view(row: dict) -> dict[str, Any]:
    """Строка очереди наружу.

    Колонка `username` (единственное число) сюда не попадает намеренно:
    она осталась от миграции 003 и всегда пуста, а отданное наружу поле
    рано или поздно становится полем, на которое опираются.
    """
    return {
        "id": row["id"],
        "channel": row["channel"],
        "message_id": row["message_id"],
        "found_at": row["found_at"],
        "preview": row["preview"],
        "matched_keyword": row["matched_keyword"],
        "usernames": row["usernames"],
        "status": row["status"],
        "status_at": row["status_at"],
        "link": f"https://t.me/{row['channel'].lstrip('@')}/{row['message_id']}",
    }


@router.get("/tg")
async def list_tg_found(
    status: StatusFilter = "all",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    rows = TgFoundRepo(get_connection()).list(
        decided=_decided_flag(status), limit=limit, offset=offset
    )
    return [_tg_view(row) for row in rows]


@router.patch("/tg/{found_id}")
async def patch_tg_found(found_id: int, patch: StatusPatch) -> dict[str, Any]:
    conn = get_connection()
    repo = TgFoundRepo(conn)
    row = repo.get(found_id)
    if row is None:
        raise HTTPException(status_code=404, detail="запись не найдена")

    check_transition(row["status"], patch.status)
    now = datetime.now()

    if patch.status == statuses.MANUAL_APPLIED:
        # Человек написал сам — воркер больше никогда не должен писать этим
        # людям. `ensure_contact` для этого и написан: он регистрирует
        # контакт БЕЗ строки в `tg_sends`, поэтому дневной счётчик
        # («отправлено сегодня» считается по `tg_sends`) не трогается —
        # решение D16 выполняется формой данных.
        tg_repo = TgRepo(conn)
        for username in row["usernames"]:
            tg_repo.ensure_contact(username, now)

    repo.set_status(found_id, patch.status, now)
    return {"status": "saved", "found": _tg_view(repo.get(found_id))}
```

- [ ] **Шаг 4: подключите роутер**

В `api/main.py` рядом с остальными импортами роутеров:

```python
from .found_routes import router as found_router
```

и рядом с остальными `include_router`:

```python
app.include_router(found_router)
```

- [ ] **Шаг 5: внесите модуль в три документа**

Строка после `resumes_routes.py` в блоке `api/`:

```
│   └── found_routes.py         /api/found — очередь найденного и ручные статусы
```

(и поправьте псевдографику предыдущей строки с `└──` на `├──`, если
`resumes_routes.py` была последней). Повторите во всех трёх документах:
`структура.txt`, `README.md`, спецификация укрепления §4.

- [ ] **Шаг 6: прогоните**

Run: `pytest tests/test_found_api.py tests/test_route_invariants.py tests/test_docs_structure.py -q`
Expected: PASS

- [ ] **Шаг 7: прогоните весь набор**

Run: `make test`
Expected: PASS

- [ ] **Шаг 8: проверьте дискриминирующую силу**

1. В `check_transition` уберите ветку `AUTO_APPLIED` → падает
   `test_the_robot_status_cannot_be_set_by_hand`.
2. Разрешите `NEW` из любого статуса (`if target == statuses.NEW: return`)
   → падает `test_an_answered_post_cannot_be_returned_to_the_queue`.
3. Уберите цикл `ensure_contact` → падает
   `test_marking_it_answered_registers_every_contact`.
4. Поставьте `ensure_contact` и для `DISMISSED` → падает
   `test_marking_it_dismissed_only_changes_the_status`.
5. Замените `ensure_contact` на `record_send` → падает
   `test_a_manual_answer_does_not_touch_the_daily_counter`. **Это та самая
   мутация, ради которой тест существует:** проверьте, что он краснеет, а не
   держится за счёт соседа.
6. Добавьте `"username": row.get("username")` в `_tg_view` → падает
   `test_the_list_does_not_leak_the_dead_column`.
7. Уберите `model_config = ConfigDict(extra="forbid")` → падает
   `test_an_extra_field_is_refused`.

- [ ] **Шаг 9: коммит**

```bash
git add api/found_routes.py api/main.py tests/test_found_api.py \
        структура.txt README.md \
        docs/superpowers/specs/2026-09-08-hardening-and-rework-spec.md
git commit -m "$(cat <<'MSG'
feat(api): очередь найденного в Telegram и ручные статусы

Руками ставятся только «откликнулся сам» и «не подходит». Возврат в
«новую» разрешён из «не подходит» и «пропущено» и запрещён из уже
отправленного отклика: иначе робот отправил бы второй отклик тому же
работодателю.

Claude-Session: https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB
MSG
)"
```

---

## Задача 6: API очереди hh.ru и снятие старого роута

**Files:**
- Modify: `api/found_routes.py`
- Modify: `api/hh_routes.py` (удаление `GET /api/hh/vacancies`)
- Modify: `frontend/app.js` (один вызов в `loadHHVacancies`)
- Modify: `tests/test_found_api.py` (дописать раздел hh.ru)
- Modify: `tests/test_frontend_events.py` (усилить инвариант эндпоинтов)

**Interfaces:**
- Consumes: `api.found_routes.check_transition`, `_decided_flag` (задача 5);
  `HhRepo.list_found/get/set_status` (задача 2).
- Produces: `GET /api/found/hh`, `PATCH /api/found/hh/{vacancy_id}`.
  Роут `GET /api/hh/vacancies` перестаёт существовать.

- [ ] **Шаг 1: напишите падающие тесты**

Допишите в `tests/test_found_api.py`:

```python
# ── hh.ru ─────────────────────────────────────────────────────────────

from job_monitor.db.repositories import HhRepo


@pytest.fixture
def hh_vacancy(client):
    conn = get_connection()
    conn.execute("DELETE FROM hh_applications")
    HhRepo(conn).record_found({
        "vacancy_id": "1",
        "title": "QA Engineer",
        "company": "ООО Ромашка",
        "salary": "от 400 000 ₸",
        "city": "Алматы",
        "url": "https://hh.ru/vacancy/1",
        "found_at": "2026-09-10T10:00:00",
        "status": statuses.NEW,
        "status_source": statuses.SOURCE_ROBOT,
    })
    return "1"


def test_the_hh_list_carries_everything_the_card_shows(client, hh_vacancy) -> None:
    rows = client.get("/api/found/hh").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["url"] == "https://hh.ru/vacancy/1", "ссылка у hh.ru уже есть в базе"
    assert row["title"] == "QA Engineer"
    assert row["company"] == "ООО Ромашка"
    assert row["city"] == "Алматы"
    assert row["salary"] == "от 400 000 ₸"
    assert row["status"] == statuses.NEW
    assert row["status_source"] == statuses.SOURCE_ROBOT


def test_the_hh_list_filters_by_status(client, hh_vacancy) -> None:
    assert len(client.get("/api/found/hh?status=new").json()) == 1
    client.patch(f"/api/found/hh/{hh_vacancy}", json={"status": statuses.DISMISSED})
    assert client.get("/api/found/hh?status=new").json() == []
    assert len(client.get("/api/found/hh?status=decided").json()) == 1


def test_a_manual_hh_answer_names_the_human(client, hh_vacancy) -> None:
    reply = client.patch(
        f"/api/found/hh/{hh_vacancy}", json={"status": statuses.MANUAL_APPLIED}
    )
    assert reply.status_code == 200
    row = HhRepo(get_connection()).get(hh_vacancy)
    assert row["status"] == statuses.MANUAL_APPLIED
    assert row["status_source"] == statuses.SOURCE_HUMAN


def test_a_manual_hh_answer_stays_out_of_the_counter(client, hh_vacancy) -> None:
    """Решение D16 на самом счётчике. Тест не вакуумен: `set_status`
    ПРОСТАВЛЯЕТ `applied_at` ручному отклику (он нужен для сортировки), так
    что счётчик молчит только благодаря фильтру по статусу."""
    from datetime import date

    client.patch(f"/api/found/hh/{hh_vacancy}", json={"status": statuses.MANUAL_APPLIED})
    repo = HhRepo(get_connection())
    assert repo.get(hh_vacancy)["applied_at"] is not None
    assert repo.applied_on(date.today()) == 0
    assert repo.applied_total() == 0


def test_the_robot_hh_status_cannot_be_set_by_hand(client, hh_vacancy) -> None:
    reply = client.patch(
        f"/api/found/hh/{hh_vacancy}", json={"status": statuses.AUTO_APPLIED}
    )
    assert reply.status_code == 400
    assert HhRepo(get_connection()).get(hh_vacancy)["status"] == statuses.NEW


def test_an_applied_hh_vacancy_cannot_be_returned_to_the_queue(client, hh_vacancy) -> None:
    """Для hh.ru у этого запрета нет второго рубежа: дедупликации контактов,
    которая спасает Telegram, здесь не существует — робот просто кликнул бы
    «Откликнуться» второй раз."""
    HhRepo(get_connection()).set_status(
        hh_vacancy, statuses.AUTO_APPLIED, statuses.SOURCE_ROBOT, datetime.now()
    )
    reply = client.patch(f"/api/found/hh/{hh_vacancy}", json={"status": statuses.NEW})
    assert reply.status_code == 400
    assert HhRepo(get_connection()).get(hh_vacancy)["status"] == statuses.AUTO_APPLIED


def test_a_skipped_hh_vacancy_can_be_returned_to_the_queue(client, hh_vacancy) -> None:
    """«Пропущено» значит «кнопка отклика не нашлась». Клика не было —
    пусть попробует снова."""
    HhRepo(get_connection()).set_status(
        hh_vacancy, statuses.SKIPPED, statuses.SOURCE_ROBOT, datetime.now()
    )
    assert client.patch(
        f"/api/found/hh/{hh_vacancy}", json={"status": statuses.NEW}
    ).status_code == 200
    assert HhRepo(get_connection()).get(hh_vacancy)["status"] == statuses.NEW


def test_a_broken_scenario_cannot_be_returned_to_the_queue(client, hh_vacancy) -> None:
    """Выглядит как «робот не смог», но кнопка уже нажата: сценарий
    ломается ПОСЛЕ клика (см. `_process_one` в workers/hh.py)."""
    HhRepo(get_connection()).set_status(
        hh_vacancy, statuses.SCENARIO_ERROR, statuses.SOURCE_ROBOT, datetime.now()
    )
    reply = client.patch(f"/api/found/hh/{hh_vacancy}", json={"status": statuses.NEW})
    assert reply.status_code == 400
    assert "после клика" in reply.json()["detail"]


def test_patching_a_missing_vacancy_is_404(client, hh_vacancy) -> None:
    assert client.patch(
        "/api/found/hh/нет-такой", json={"status": statuses.DISMISSED}
    ).status_code == 404


def test_the_old_vacancies_route_is_gone(client, hh_vacancy) -> None:
    """Единственным его потребителем был наш же фронтенд. Два роута с одним
    смыслом — это два места, которые разойдутся."""
    assert client.get("/api/hh/vacancies").status_code == 404
```

- [ ] **Шаг 2: прогоните — тесты должны упасть**

Run: `pytest tests/test_found_api.py -q`
Expected: FAIL — 404 на `/api/found/hh`, 200 на `/api/hh/vacancies`

- [ ] **Шаг 3: добавьте роуты hh.ru**

В `api/found_routes.py` допишите импорт `HhRepo` и:

```python
def _hh_view(row: dict) -> dict[str, Any]:
    """Вакансия наружу. Ключи те же, что в таблице: ссылка у hh.ru уже
    лежит в `url`, собирать нечего."""
    return {
        "vacancy_id": row["vacancy_id"],
        "title": row["title"],
        "company": row["company"],
        "salary": row["salary"],
        "city": row["city"],
        "url": row["url"],
        "found_at": row["found_at"],
        "applied_at": row["applied_at"],
        "status": row["status"],
        "status_source": row["status_source"],
        "error": row["error"],
    }


@router.get("/hh")
async def list_hh_found(
    status: StatusFilter = "all",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    rows = HhRepo(get_connection()).list_found(
        decided=_decided_flag(status), limit=limit, offset=offset
    )
    return [_hh_view(row) for row in rows]


@router.patch("/hh/{vacancy_id}")
async def patch_hh_found(vacancy_id: str, patch: StatusPatch) -> dict[str, Any]:
    """Ручной статус вакансии hh.ru.

    Побочных эффектов, в отличие от Telegram, нет: дедупликация здесь идёт
    по самому статусу — он попадает в `DECIDED`, и воркер вакансию больше
    не тронет.
    """
    repo = HhRepo(get_connection())
    row = repo.get(vacancy_id)
    if row is None:
        raise HTTPException(status_code=404, detail="вакансия не найдена")

    check_transition(row["status"], patch.status)
    source = (
        statuses.SOURCE_ROBOT if patch.status == statuses.NEW else statuses.SOURCE_HUMAN
    )
    repo.set_status(vacancy_id, patch.status, source, datetime.now())
    return {"status": "saved", "found": _hh_view(repo.get(vacancy_id))}
```

Возврат в очередь помечается источником «робот» намеренно: запись снова
принадлежит роботу, и следующая надпись на карточке должна говорить о нём,
а не о человеке, который её туда вернул. Добавьте это комментарием.

- [ ] **Шаг 4: снимите старый роут**

В `api/hh_routes.py` удалите обработчик:

```python
@router.get("/vacancies")
async def hh_vacancies() -> list[dict]:
    return HhRepo(get_connection()).recent(50)
```

Импорт `HhRepo` в этом файле остаётся — его использует `hh_status`.

- [ ] **Шаг 5: переведите фронтенд на новый адрес**

В `frontend/app.js`, в `loadHHVacancies`, замените

```javascript
  const vacs = await apiGet('/hh/vacancies');
```

на

```javascript
  const vacs = await apiGet('/found/hh');
```

Больше в этой функции сейчас ничего не меняется: экран «Найдено» приходит в
задаче 9, а до тех пор карточки вакансий на дашборде должны продолжать
работать.

- [ ] **Шаг 6: усильте инвариант эндпоинтов**

В `tests/test_frontend_events.py`, в
`test_app_js_no_longer_polls_the_per_worker_status_endpoints`, добавьте
строку:

```python
    assert "/hh/vacancies" not in code, (
        "роут снят: очередь читается из GET /api/found/hh"
    )
```

Это усиление ловушки, а не ослабление: проверок становится больше.

- [ ] **Шаг 7: прогоните**

Run: `pytest tests/test_found_api.py tests/test_frontend_events.py tests/test_route_invariants.py -q`
Expected: PASS

- [ ] **Шаг 8: прогоните весь набор**

Run: `make test`
Expected: PASS

- [ ] **Шаг 9: проверьте дискриминирующую силу**

1. В `patch_hh_found` проставляйте `SOURCE_ROBOT` всегда → падает
   `test_a_manual_hh_answer_names_the_human`.
2. Уберите вызов `check_transition` → падают сразу три запрета; проверьте,
   что краснеет и `test_a_broken_scenario_cannot_be_returned_to_the_queue`,
   а не только соседи.
3. В `check_transition` добавьте `SCENARIO_ERROR` в `REOPENABLE` → падает
   `test_a_broken_scenario_cannot_be_returned_to_the_queue`, а
   `test_a_skipped_hh_vacancy_can_be_returned_to_the_queue` остаётся
   зелёным. Если покраснели оба — проверки держатся друг за друга.
4. Верните роут `GET /api/hh/vacancies` → падает
   `test_the_old_vacancies_route_is_gone`.

- [ ] **Шаг 10: коммит**

```bash
git add api/found_routes.py api/hh_routes.py frontend/app.js \
        tests/test_found_api.py tests/test_frontend_events.py
git commit -m "$(cat <<'MSG'
feat(api): очередь найденного на hh.ru, старый роут вакансий снят

GET /api/hh/vacancies заменён на /api/found/hh: два роута с одним смыслом
разошлись бы при первой правке. Ручной отклик проставляет applied_at ради
сортировки, но в счётчик не попадает — тот фильтрует ещё и по статусу.

Claude-Session: https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB
MSG
)"
```

---

## Задача 7: долги подпроекта 1 — M-2 и M-3

Два замечания одного класса: **состояние разошлось с ответом**. Клиент видит
отказ, а часть работы уже сделана и откатить её нечем.

**Files:**
- Modify: `api/presets_routes.py:92-122` (`patch_preset`)
- Modify: `api/resumes_routes.py` (`upload_resume`, `delete_resume`)
- Modify: `tests/test_presets_api.py`, `tests/test_resumes_api.py`

**Interfaces:**
- Consumes: ничего нового.
- Produces: поведение, а не имена. `PATCH /api/presets/{id}` применяется
  целиком или никак; загрузка резюме не оставляет файл-сироту.

- [ ] **Шаг 1: напишите падающие тесты**

В `tests/test_presets_api.py`:

```python
def test_a_rejected_patch_changes_nothing_at_all(client) -> None:
    """M-2: запрос с новым именем и негодным критерием возвращал 422, но имя
    уже было сохранено. Клиент видит отказ и не знает, что переименование
    прошло, — и следующий его запрос идёт к пресету, которого «нет»."""
    created = client.post("/api/presets", json={"name": "До правки"}).json()
    preset_id = created["id"]

    reply = client.patch(
        f"/api/presets/{preset_id}",
        json={"name": "После правки", "hh_experience": "такого кода нет"},
    )
    assert reply.status_code == 422

    assert client.get(f"/api/presets/{preset_id}").json()["name"] == "До правки"


def test_a_rejected_patch_does_not_move_the_preset_either(client) -> None:
    """Позиция — вторая половина той же проблемы: её тоже писали до
    валидации критериев."""
    created = client.post("/api/presets", json={"name": "Порядок"}).json()
    preset_id = created["id"]
    before = next(
        row["position"] for row in client.get("/api/presets").json()
        if row["id"] == preset_id
    )

    client.patch(
        f"/api/presets/{preset_id}",
        json={"position": before + 5, "hh_experience": "такого кода нет"},
    )

    after = next(
        row["position"] for row in client.get("/api/presets").json()
        if row["id"] == preset_id
    )
    assert after == before


def test_a_valid_patch_still_applies_everything(client) -> None:
    """Страховка от вакуумности: если бы отказ стал тотальным, эти
    проверки поймали бы и то, что перестало работать успешное сохранение."""
    created = client.post("/api/presets", json={"name": "Было"}).json()
    preset_id = created["id"]

    reply = client.patch(
        f"/api/presets/{preset_id}",
        json={"name": "Стало", "hh_experience": "between1And3"},
    )
    assert reply.status_code == 200

    preset = client.get(f"/api/presets/{preset_id}").json()
    assert preset["name"] == "Стало"
    assert preset["criteria"]["hh_experience"] == "between1And3"
```

В `tests/test_resumes_api.py`:

```python
def test_a_failed_insert_leaves_no_orphan_file(client, monkeypatch) -> None:
    """M-3: файл писался до вставки строки. Отказ вставки оставлял его в
    каталоге навсегда — в списке его нет, удалить из интерфейса нечем."""
    from job_monitor import paths
    from job_monitor.db import repositories

    def boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repositories.ResumesRepo, "add", boom)

    before = set(paths.resume_dir().iterdir())
    reply = client.post(
        "/api/resumes", content=b"%PDF-1.4 fake", headers={"X-Filename": "cv.pdf"}
    )
    assert reply.status_code == 500

    assert set(paths.resume_dir().iterdir()) == before, (
        "файл остался в каталоге, а строки в базе нет — удалить его из "
        "интерфейса невозможно"
    )


def test_a_failed_row_delete_keeps_the_file(client, monkeypatch) -> None:
    """Зеркальная половина: файл стирался ДО строки, поэтому отказ на
    удалении строки оставлял запись, указывающую в пустоту."""
    from job_monitor import paths, resume_store
    from job_monitor.db import repositories

    created = client.post(
        "/api/resumes", content=b"%PDF-1.4 fake", headers={"X-Filename": "cv.pdf"}
    ).json()

    def boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repositories.ResumesRepo, "delete", boom)
    assert client.delete(f"/api/resumes/{created['id']}").status_code == 500

    row = client.get("/api/resumes").json()[0]
    assert resume_store.path_of(row["stored_name"]).exists(), (
        "строка осталась, а файл стёрт — скачивание такой записи даёт 404 "
        "без единого способа починить"
    )
```

Добавьте `import sqlite3` в начало файла, если его там нет.

- [ ] **Шаг 2: прогоните — тесты должны упасть**

Run: `pytest tests/test_presets_api.py tests/test_resumes_api.py -q`
Expected: FAIL — имя сохранилось, файл остался сиротой

- [ ] **Шаг 3: почините M-2 — сначала проверяем всё, потом пишем**

В `api/presets_routes.py::patch_preset` перенесите валидацию критериев ДО
записи имени и позиции:

```python
@router.patch("/{preset_id}")
async def patch_preset(preset_id: int, patch: dict) -> dict[str, str]:
    """Применяется целиком или никак.

    Раньше имя и позиция писались сразу, а критерии валидировались после:
    запрос с новым именем и негодным критерием возвращал 422, оставив
    переименование сделанным. Клиент видел отказ и не знал, что половина
    прошла, — и следующий его запрос шёл к пресету, которого «нет».

    Сначала проверяется ВСЁ (имя, позиция, критерии), и только потом идёт
    первая запись. Валидация критериев — `SearchCriteria` над слиянием
    патча с сохранённым, то есть ровно то, что сделает `save_criteria`,
    но без записи.
    """
    conn = get_connection()
    repo = PresetsRepo(conn)
    stored = repo.get(preset_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="пресет не найден")

    body = dict(patch)
    new_name = body.pop("name", None)
    position = body.pop("position", None)

    stripped: str | None = None
    if new_name is not None:
        stripped = str(new_name).strip()
        if not stripped:
            raise HTTPException(
                status_code=400, detail="имя пресета не может быть пустым"
            )
        clash = repo.get_by_name(stripped)
        if clash is not None and clash["id"] != preset_id:
            raise HTTPException(status_code=400, detail=f"имя «{stripped}» уже занято")

    if position is not None:
        try:
            position = int(position)
        except (TypeError, ValueError) as error:
            raise HTTPException(
                status_code=400, detail="позиция должна быть числом"
            ) from error

    if body:
        # Сухой прогон той же проверки, которую сделает save_criteria.
        # Дублирование здесь дешевле полусохранения: слияние — чистая
        # функция, а вот откатить уже записанное имя нечем.
        merged = presets_service._tolerant(stored["criteria"]).model_dump()
        merged.update(body)
        try:
            SearchCriteria(**merged)
        except ValidationError as error:
            raise HTTPException(
                status_code=422, detail=_validation_detail(error)
            ) from error

    now = datetime.now()
    if stripped is not None:
        repo.set_name(preset_id, stripped, now)
    if position is not None:
        repo.set_position(preset_id, position, now)
    if body:
        presets_service.save_criteria(conn, preset_id, body)
    return {"status": "saved"}
```

`_tolerant` — приватная функция модуля `presets`; чтобы не тянуть подчёркнутое
имя через границу модуля, добавьте в `job_monitor/presets.py` публичную
обёртку и используйте её:

```python
def parse_criteria(raw: dict) -> SearchCriteria:
    """Разбор сохранённых критериев, терпимый к полям, не прошедшим
    проверку. Публичное имя для `_tolerant`: сухой прогон валидации в
    `api/presets_routes.py` — законный внешний потребитель."""
    return _tolerant(raw)
```

и в роуте вызывайте `presets_service.parse_criteria(stored["criteria"])`.

- [ ] **Шаг 4: почините M-3 — файл и строка появляются и исчезают вместе**

В `api/resumes_routes.py::upload_resume` оберните вставку:

```python
    stored_name, size = resume_store.store(original_name, data)
    try:
        resume_id = ResumesRepo(get_connection()).add(
            original_name, stored_name, size, datetime.now()
        )
    except Exception:
        # Строки нет — значит файла быть не должно. Иначе он остаётся в
        # каталоге навсегда: в списке его нет, а удалить из интерфейса
        # нечем, потому что интерфейс ходит по идентификаторам строк.
        resume_store.remove(stored_name)
        raise
    return {"id": resume_id, "original_name": original_name, "size_bytes": size}
```

В `delete_resume` поменяйте порядок местами:

```python
    # Сначала строка, потом файл. Обратный порядок оставлял запись,
    # указывающую в пустоту: скачивание такой записи даёт 404, и починить
    # её нечем. Отказ на удалении файла (права, занятость) оставляет файл
    # без строки — это тоже мусор, но безвредный и невидимый, тогда как
    # запись без файла ломает интерфейс.
    repo.delete(resume_id)
    resume_store.remove(row["stored_name"])
```

- [ ] **Шаг 5: прогоните**

Run: `pytest tests/test_presets_api.py tests/test_resumes_api.py tests/test_presets_service.py -q`
Expected: PASS

- [ ] **Шаг 6: прогоните весь набор**

Run: `make test`
Expected: PASS

- [ ] **Шаг 7: проверьте дискриминирующую силу**

1. Верните `repo.set_name(...)` наверх, до сухого прогона → падает
   `test_a_rejected_patch_changes_nothing_at_all`.
2. Верните `repo.set_position(...)` наверх → падает
   `test_a_rejected_patch_does_not_move_the_preset_either`.
3. Уберите `resume_store.remove(stored_name)` из `except` → падает
   `test_a_failed_insert_leaves_no_orphan_file`.
4. Верните прежний порядок в `delete_resume` → падает
   `test_a_failed_row_delete_keeps_the_file`.
5. Уберите сухой прогон целиком (`if body:` блок валидации) → падает
   `test_a_rejected_patch_changes_nothing_at_all`, но **должен остаться
   зелёным** `test_a_valid_patch_still_applies_everything`.

- [ ] **Шаг 8: коммит**

```bash
git add api/presets_routes.py api/resumes_routes.py job_monitor/presets.py \
        tests/test_presets_api.py tests/test_resumes_api.py
git commit -m "$(cat <<'MSG'
fix: PATCH пресета больше не применяется наполовину, резюме не сиротеет

M-2: имя и позиция писались до валидации критериев — 422 возвращался уже
после переименования. M-3: файл писался до строки, а стирался до неё же.
Оба дефекта одного класса: клиент видит отказ, а часть работы сделана.

Claude-Session: https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB
MSG
)"
```

---

## Задача 8: каркас интерфейса — четыре экрана и полоса состояния

С этой задачи начинается переработка фронтенда. **Прочитайте раздел
«Инварианты фронтенда» в начале плана ещё раз.** Переписывание фронтенда —
ровно та работа, в которой их теряют.

Здесь только каркас: навигация, полоса состояния и перенос существующих
блоков в новые контейнеры **без изменения их содержимого**. Содержимое
перерабатывают задачи 9–12. Разделение сделано намеренно: правка разметки на
пятьсот строк, смешанная с правкой логики, не поддаётся ревью.

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `frontend/style.css`
- Create: `tests/test_frontend_screens.py`

**Interfaces:**
- Consumes: ничего с бэкенда.
- Produces:
  - четыре экрана: `#page-overview`, `#page-found`, `#page-sent`,
    `#page-settings`; `data-page` в навигации — `overview`, `found`, `sent`,
    `settings`
  - `PAGE_LOADERS` — карта «экран → что подгрузить при открытии»; задачи
    9–12 дописывают в неё свои функции
  - `workerWord(state) -> string`, `safeModeWords(on) -> string`,
    `updateStatusBar()`

### Как переносить разметку, чтобы не сломать её молча

Прошлый раз кусок разметки продублировался: в качестве конца фрагмента был
взят `section-split`, который встречается 27 раз, и срез пошёл назад.
Поэтому:

1. Перед каждым переносом убедитесь, что якорь уникален:
   `grep -c 'id="page-channels"' frontend/index.html` → должно быть `1`.
2. После всех переносов сверьте, что ничего не потерялось и не удвоилось:
   `grep -c 'data-action=' frontend/index.html` до и после — число должно
   совпасть (кроме случаев, где задача явно удаляет кнопку).
3. Никогда не режьте по имени класса — только по `id`.

- [ ] **Шаг 1: напишите падающие тесты**

Создайте `tests/test_frontend_screens.py`:

```python
"""Инварианты четырёх экранов.

Интерфейс нарезан по задаче, а не по типу сущности (решение D20): Telegram
и hh.ru перестают быть половинами каждой страницы и становятся фильтром
внутри неё. Эти проверки держат структуру — чтобы «ещё одна страничка» не
вернула нас к семи экранам с повторами.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from conftest import requires_node

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
INDEX_HTML = FRONTEND_DIR / "index.html"
APP_JS = FRONTEND_DIR / "app.js"

SCREENS = ("overview", "found", "sent", "settings")

skip_without_node = requires_node


def _index() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _app() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _extract_function_source(name: str) -> str:
    """Та же конвенция, что в test_frontend_safety.py: функции верхнего
    уровня в app.js закрываются `}` в нулевой колонке."""
    pattern = rf"(?:async )?function {re.escape(name)}\(.*?\) \{{.*?\n\}}"
    match = re.search(pattern, _app(), re.DOTALL)
    assert match, f"{name}() не найдена в app.js"
    return match.group(0)


def _nav_pages() -> list[str]:
    return re.findall(r"""class="nav-item[^"]*"\s+data-page="([^"]+)\"""", _index())


def _page_ids() -> set[str]:
    return set(re.findall(r"""class="page[^"]*"\s+id="page-([^"]+)\"""", _index()))


def test_navigation_has_exactly_the_four_screens() -> None:
    assert _nav_pages() == list(SCREENS), (
        "интерфейс нарезается по задаче: Обзор, Найдено, Отправлено, Настройки"
    )


def test_every_nav_item_has_a_page_and_every_page_has_a_nav_item() -> None:
    """Пункт без экрана — мёртвая кнопка; экран без пункта — недостижимая
    страница. Оба раньше ловились только глазами."""
    assert _page_ids() == set(SCREENS)


def test_the_status_bar_is_outside_every_page() -> None:
    """Полоса состояния видна на любом экране. Внутри `.page` она была бы
    видна на одном — и продублирована на остальных."""
    index = _index()
    bar = index.index('id="statusBar"')
    first_page = index.index('class="page ')
    assert bar < first_page, "полоса состояния должна стоять до первого экрана"


def test_the_status_bar_names_safe_mode_in_words() -> None:
    """Смысл безопасного режима не написан нигде: только галочка, до которой
    надо дойти и вспомнить, что она значит."""
    assert "собираю, не отправляю" in _app()


def test_the_status_bar_text_comes_from_js_not_markup() -> None:
    """Иначе при первом открытии страница секунду врёт: показывает
    «остановлен» для работающего воркера."""
    index = _index()
    bar = index[index.index('id="statusBar"'):index.index('class="page ')]
    for lie in ("остановлен", "работает", "Безопасный режим:"):
        assert lie not in bar, f"полоса состояния содержит зашитый текст {lie!r}"


def test_every_screen_has_a_loader_entry() -> None:
    """`PAGE_LOADERS` — единственное место, где решается, что подгрузить при
    открытии экрана. Забытая запись даёт пустой экран без ошибки."""
    match = re.search(r"const PAGE_LOADERS = \{(.*?)\n\};", _app(), re.DOTALL)
    assert match, "PAGE_LOADERS не найдена в app.js"
    for screen in SCREENS:
        assert re.search(rf"\b{screen}\s*:", match.group(1)), (
            f"экран {screen!r} не назван в PAGE_LOADERS"
        )


def test_the_old_seven_screens_are_gone() -> None:
    """Страховка от вакуумности: без неё проверки выше прошли бы и в том
    случае, если бы старые страницы остались рядом с новыми."""
    for gone in ("page-dashboard", "page-chats", "page-channels",
                 "page-keywords", "page-template", "page-logs"):
        assert f'id="{gone}"' not in _index(), f"{gone} остался в разметке"


@skip_without_node
def test_worker_state_is_rendered_as_a_russian_word() -> None:
    """Полоса состояния говорит словами, а не кодами состояний бэкенда."""
    source = _extract_function_source("workerWord")
    table = re.search(r"const WORKER_STATE_WORDS = \{.*?\n\};", _app(), re.DOTALL)
    assert table, "WORKER_STATE_WORDS не найдена"
    cases = {
        "running": "работает",
        "stopped": "остановлен",
        "starting": "запускается",
        "stopping": "останавливается",
        "error": "ошибка",
    }
    checks = "\n".join(
        f"if (workerWord({state!r}) !== {word!r}) {{ console.error({state!r}); "
        "process.exitCode = 1; }"
        for state, word in cases.items()
    ).replace("'", '"')
    script = f"{table.group(0)}\n{source}\n{checks}"
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
```

- [ ] **Шаг 2: прогоните — тесты должны упасть**

Run: `pytest tests/test_frontend_screens.py -q`
Expected: FAIL — навигация содержит семь пунктов

- [ ] **Шаг 3: перепишите навигацию**

В `frontend/index.html` замените весь блок `<nav class="nav"> … </nav>` на:

```html
  <nav class="nav">
    <div class="nav-item active" data-page="overview"><div class="nav-dot"></div>Обзор</div>
    <div class="nav-item" data-page="found"><div class="nav-dot"></div>Найдено</div>
    <div class="nav-item" data-page="sent"><div class="nav-dot"></div>Отправлено</div>
    <div class="nav-item" data-page="settings"><div class="nav-dot"></div>Настройки</div>
  </nav>
```

Разделители `nav-sep` уходят: четыре пункта в группировке не нуждаются.

- [ ] **Шаг 4: добавьте полосу состояния**

Сразу после `<div class="main">` (якорь уникален — проверьте `grep -c`):

```html
  <div class="statusbar" id="statusBar">
    <span class="statusbar-item">
      <span class="status-dot stopped" id="sbDotTG"></span>
      <span id="sbTG"></span>
    </span>
    <span class="statusbar-item">
      <span class="status-dot stopped" id="sbDotHH"></span>
      <span id="sbHH"></span>
    </span>
    <span class="statusbar-item statusbar-mode" id="sbMode"></span>
    <span class="statusbar-item statusbar-preset" id="sbPreset"></span>
  </div>
```

В `frontend/style.css` добавьте:

```css
/* ── Полоса состояния ─────────────────────────────────────────────── */
.statusbar { display: flex; align-items: center; gap: 18px; flex-wrap: wrap;
  padding: 8px 22px; background: var(--sidebar-bg); border-bottom: 1px solid var(--border);
  font-size: 12px; color: var(--text); flex-shrink: 0; }
.statusbar-item { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
.statusbar-mode { font-weight: 600; }
.statusbar-preset { margin-left: auto; color: var(--muted); }
```

- [ ] **Шаг 5: перенесите содержимое старых страниц**

Порядок ровно такой; после каждого переноса прогоняйте
`pytest tests/test_frontend_events.py -q` — он держит связь разметки с
картой обработчиков и падает сразу, если кнопка потерялась.

1. `id="page-dashboard"` → `id="page-overview"`, заголовок «Дашборд» →
   «Обзор». Содержимое не трогаем.
2. Карточки из `page-channels`, `page-keywords`, `page-template` перенесите
   **целиком, вместе с их кнопками сохранения**, внутрь `page-overview`, в
   конец. Сами контейнеры `<div class="page" id="page-channels">` и их
   `page-header` удалите. Критерии живут на «Обзоре» (решение D21) —
   раскладку им даст задача 10.
3. `id="page-chats"` → `id="page-sent"`, заголовок — «Отправлено».
4. Карточку с `id="logConsole"` вместе с её панелью вкладок перенесите в
   конец `page-settings`; контейнер `page-logs` удалите.
5. Добавьте пустой экран очереди перед `page-sent`:

```html
  <div class="page" id="page-found">
    <div class="page-header"><div class="page-title">Найдено</div></div>
    <div id="foundBody"></div>
  </div>
```

- [ ] **Шаг 6: перепишите навигацию в `app.js`**

Замените блок `// ── Navigation ──` целиком:

```javascript
// ── Navigation ────────────────────────────────────────────────────────
// Что подгрузить при открытии экрана. Единственное место, где это
// решается: раньше это была лестница из шести `if (page === ...)`, куда
// новый экран забывали дописать — и он открывался пустым, без ошибки.
const PAGE_LOADERS = {
  overview: async () => {
    renderChannelEdit();
    renderKeywords();
  },
  found: async () => {},
  sent: async () => { await renderChats(); },
  settings: async () => {
    await loadSettings();
    refreshLogs();
  },
};

document.querySelectorAll('.nav-item[data-page]').forEach(item => {
  item.addEventListener('click', async () => {
    const page = item.dataset.page;
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.getElementById('page-' + page).classList.add('active');
    item.classList.add('active');
    const load = PAGE_LOADERS[page];
    if (load) await load();
  });
});
```

Строка `document.getElementById('templateText').value = tgState.template;`
из старого обработчика больше не нужна: шаблон живёт на «Обзоре», который
заполняет `loadActiveCriteria()`.

- [ ] **Шаг 7: добавьте отрисовку полосы состояния**

Рядом с `workerView` в `app.js`:

```javascript
// ── Полоса состояния ──────────────────────────────────────────────────
// Состояние воркеров, режим и активный пресет были разбросаны по дашборду
// и настройкам, а смысл безопасного режима не написан нигде: только
// галочка, до которой надо дойти и вспомнить, что она значит.
const WORKER_STATE_WORDS = {
  stopped: 'остановлен',
  starting: 'запускается',
  running: 'работает',
  stopping: 'останавливается',
  error: 'ошибка',
};

function workerWord(state) {
  return WORKER_STATE_WORDS[state] || WORKER_STATE_WORDS.stopped;
}

function safeModeWords(on) {
  return on
    ? 'Безопасный режим: собираю, не отправляю'
    : 'Отправка включена';
}

function updateStatusBar() {
  const rows = [
    ['sbDotTG', 'sbTG', tgState, 'Telegram'],
    ['sbDotHH', 'sbHH', hhState, 'hh.ru'],
  ];
  for (const [dotId, textId, worker, name] of rows) {
    const dot = document.getElementById(dotId);
    const label = document.getElementById(textId);
    if (dot) dot.className = 'status-dot ' + workerView(worker.state, name, worker.canStart).dot;
    if (label) label.textContent = `${name}: ${workerWord(worker.state)}`;
  }
  const mode = document.getElementById('sbMode');
  if (mode) mode.textContent = safeModeWords(tgState.safeMode);
  const preset = document.getElementById('sbPreset');
  const active = presetState.list.find(item => item.is_active);
  if (preset) preset.textContent = active ? `Пресет: ${active.name}` : 'Пресет не выбран';
}
```

Вызовите `updateStatusBar()` в трёх местах: в конце `updateTGButton()`, в
конце `updateHHButton()` и в конце `renderPresetBar()`. Этого достаточно —
все три вызываются и при опросе, и при переключении пресета.

- [ ] **Шаг 8: прогоните**

Run: `pytest tests/test_frontend_screens.py tests/test_frontend_events.py tests/test_frontend_safety.py -q`
Expected: PASS

- [ ] **Шаг 9: прогоните весь набор и посмотрите глазами**

Run: `make test`
Expected: PASS

Затем поднимите приложение (`make run`), откройте `http://127.0.0.1:8000` и
проверьте руками: четыре пункта переключаются, полоса состояния показывает
оба воркера, режим и пресет, ни один экран не пустой (кроме «Найдено»),
консоль браузера чистая.

- [ ] **Шаг 10: проверьте дискриминирующую силу**

1. Добавьте пятый `nav-item` → падает
   `test_navigation_has_exactly_the_four_screens`.
2. Уберите `found: async () => {},` из `PAGE_LOADERS` → падает
   `test_every_screen_has_a_loader_entry`.
3. Впишите `остановлен` прямо в `#sbTG` в разметке → падает
   `test_the_status_bar_text_comes_from_js_not_markup`.
4. Перенесите полосу состояния внутрь `#page-overview` → падает
   `test_the_status_bar_is_outside_every_page`.
5. Замените `'работает'` на `'running'` в `WORKER_STATE_WORDS` → падает
   `test_worker_state_is_rendered_as_a_russian_word`.

- [ ] **Шаг 11: коммит**

```bash
git add frontend/index.html frontend/app.js frontend/style.css \
        tests/test_frontend_screens.py
git commit -m "$(cat <<'MSG'
feat(ui): четыре экрана вместо семи и постоянная полоса состояния

Интерфейс нарезается по задаче, а не по типу сущности (D20). Полоса
состояния называет безопасный режим словами: до сих пор его смысл не был
написан нигде — только галочка в настройках.

Claude-Session: https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB
MSG
)"
```

---

## Задача 9: экран «Найдено»

Ради чего весь подпроект: список найденного со ссылкой, ручным откликом и
ручной простановкой статуса.

**Files:**
- Modify: `frontend/index.html` (наполнение `#page-found`)
- Modify: `frontend/app.js`
- Modify: `frontend/style.css`
- Modify: `tests/test_frontend_screens.py`
- Modify: `tests/test_stored_columns_are_read.py` (перенацеливание ловушки)

**Interfaces:**
- Consumes: `GET/PATCH /api/found/tg` (задача 5), `GET/PATCH /api/found/hh`
  (задача 6); `PAGE_LOADERS` (задача 8).
- Produces:
  - `hhVacancyCard(v) -> Node` — информационная часть карточки вакансии
    hh.ru; переиспользуется «Обзором» в задаче 10
  - `loadFound()`, `foundState = { source, filter }`
  - действия `foundSource`, `foundFilter`, `foundApply`, `foundDismiss`,
    `foundReopen`

- [ ] **Шаг 1: напишите падающие тесты**

Допишите в `tests/test_frontend_screens.py`:

```python
# ── Экран «Найдено» ───────────────────────────────────────────────────


def test_the_found_screen_has_both_sources_and_the_status_filter() -> None:
    index = _index()
    found = index[index.index('id="page-found"'):index.index('id="page-sent"')]
    for arg in ('data-arg="tg"', 'data-arg="hh"'):
        assert arg in found, f"на экране «Найдено» нет переключателя {arg}"
    for arg in ('data-arg="all"', 'data-arg="new"', 'data-arg="decided"'):
        assert arg in found, f"на экране «Найдено» нет фильтра {arg}"


def test_the_found_screen_reads_the_new_endpoints() -> None:
    source = _extract_function_source("loadFound")
    assert "/found/" in source
    assert "/hh/vacancies" not in _app()


def test_the_row_never_builds_its_own_telegram_link() -> None:
    """Ссылку строит бэкенд: фронтенду незачем знать формат чужих URL, а
    гвард схемы в `el()` остаётся единственной точкой проверки."""
    assert "t.me/" not in _app(), (
        "фронтенд собирает ссылку на Telegram сам — это должен делать "
        "api/found_routes.py, там же где и все остальные ссылки"
    )


@skip_without_node
def test_the_status_badge_class_is_a_table_not_a_substring_game() -> None:
    """Раньше класс бейджа выбирался по вхождению подстроки, и «откликнулся
    сам» не совпадал ни с чем — ручной отклик выглядел как ожидание.
    Таблица по точным значениям из job_monitor/statuses.py исключает это
    по построению."""
    table = re.search(r"const STATUS_CLASS = \{.*?\n\};", _app(), re.DOTALL)
    assert table, "STATUS_CLASS не найдена в app.js"
    source = _extract_function_source("vacancyStatusClass")
    cases = {
        "новая": "status-wait",
        "отклик отправлен": "status-sent",
        "откликнулся сам": "status-sent",
        "не подходит": "status-skip",
        "пропущено": "status-skip",
        "ошибка сценария": "status-error",
        "": "status-wait",
    }
    checks = "\n".join(
        f'if (vacancyStatusClass({status!r}) !== {css!r}) '
        f'{{ console.error({status!r}); process.exitCode = 1; }}'
        for status, css in cases.items()
    ).replace("'", '"')
    script = f"{table.group(0)}\n{source}\n{checks}"
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_the_status_vocabulary_matches_the_backend() -> None:
    """Два словаря статусов, которые разошлись, — это бейдж «ожидание» на
    отправленном отклике. Проверяем, что фронтенд знает ровно те строки,
    которые пишет бэкенд."""
    from job_monitor import statuses

    table = re.search(r"const STATUS_CLASS = \{(.*?)\n\};", _app(), re.DOTALL)
    assert table
    known = set(re.findall(r"['\"]([^'\"]+)['\"]\s*:", table.group(1)))
    assert known == set(statuses.ALL), (
        f"фронтенд знает {sorted(known)}, бэкенд пишет {sorted(statuses.ALL)}"
    )


def test_every_found_action_has_a_handler() -> None:
    """Та же связь, что держит `tests/test_frontend_events.py`, но для
    кнопок, которые создаёт сам JS: строки очереди не существуют в
    index.html и статическим сканом разметки не видны."""
    actions = set(re.findall(r"""['"]data-action['"]\s*:\s*['"]([^'"]+)['"]""", _app()))
    for name in ("foundApply", "foundDismiss", "foundReopen"):
        assert name in actions, f"кнопка {name} нигде не создаётся"
```

- [ ] **Шаг 2: прогоните — тесты должны упасть**

Run: `pytest tests/test_frontend_screens.py -q`
Expected: FAIL — `loadFound()` не найдена

- [ ] **Шаг 3: наполните экран в разметке**

Замените заглушку `#page-found` из задачи 8:

```html
  <div class="page" id="page-found">
    <div class="page-header">
      <div class="page-title">Найдено</div>
      <div class="seg" id="foundSource">
        <button class="seg-btn active" data-action="foundSource" data-arg="tg">Telegram</button>
        <button class="seg-btn" data-action="foundSource" data-arg="hh">hh.ru</button>
      </div>
    </div>
    <div class="seg seg-quiet" id="foundFilter">
      <button class="seg-btn" data-action="foundFilter" data-arg="all">Все</button>
      <button class="seg-btn active" data-action="foundFilter" data-arg="new">Новые</button>
      <button class="seg-btn" data-action="foundFilter" data-arg="decided">Решённые</button>
    </div>
    <div class="card" style="padding:0;overflow:hidden">
      <div id="foundList"></div>
    </div>
  </div>
```

В `frontend/style.css`:

```css
/* ── Переключатели и очередь найденного ───────────────────────────── */
.seg { display: inline-flex; gap: 2px; background: var(--bg); border: 1px solid var(--border);
  border-radius: var(--radius-sm); padding: 2px; }
.seg-btn { padding: 5px 12px; border: none; background: none; border-radius: 4px;
  font-size: 12.5px; font-weight: 600; font-family: 'Manrope', sans-serif;
  color: var(--muted); cursor: pointer; }
.seg-btn.active { background: var(--card-bg); color: var(--text); box-shadow: var(--shadow); }
.seg-quiet { align-self: flex-start; }

.found-row { display: flex; align-items: flex-start; gap: 12px; padding: 12px 14px;
  border-bottom: 1px solid var(--border); }
.found-row:last-child { border-bottom: none; }
.found-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.found-title { font-size: 13px; font-weight: 600; }
.found-meta { font-size: 11px; color: var(--muted); }
.found-link { font-size: 11px; color: var(--accent); text-decoration: none; }
.found-actions { display: flex; gap: 6px; flex-shrink: 0; align-items: center; }
.found-empty { padding: 24px; text-align: center; color: var(--muted); font-size: 13px; }
```

- [ ] **Шаг 4: перепишите выбор класса бейджа**

В `frontend/app.js` замените `vacancyStatusClass` целиком:

```javascript
// Словарь статусов зеркалит job_monitor/statuses.py. Раньше класс бейджа
// выбирался вхождением подстроки — и «откликнулся сам» не совпадал ни с
// чем, поэтому ручной отклик рисовался как ожидание. Таблица по точным
// значениям исключает это по построению, а tests/test_frontend_screens.py
// сторожит, что она не разошлась с бэкендом.
const STATUS_CLASS = {
  'новая': 'status-wait',
  'отклик отправлен': 'status-sent',
  'откликнулся сам': 'status-sent',
  'не подходит': 'status-skip',
  'пропущено': 'status-skip',
  'ошибка сценария': 'status-error',
};

function vacancyStatusClass(status) {
  return STATUS_CLASS[status] || 'status-wait';
}
```

- [ ] **Шаг 5: выделите карточку вакансии**

Замените тело `loadHHVacancies` так, чтобы информационная часть карточки
жила отдельной функцией. Саму `loadHHVacancies` пока не удаляйте — блок
последних вакансий на «Обзоре» уходит только в задаче 10.

```javascript
// Информационная часть карточки вакансии — без кнопок. Отдельно от них
// потому, что кнопки нужны только в очереди, а карточка нужна и там, и на
// «Обзоре»; а ещё потому, что `tests/test_stored_columns_are_read.py`
// сторожит именно её: каждая колонка hh_applications обязана либо попасть
// сюда, либо быть названной в NOT_ON_THE_CARD с объяснением.
function hhVacancyCard(v) {
  const st = v.status || '';
  const meta = [v.company, v.city, v.salary || 'з/п не указана'].filter(Boolean).join(' · ');
  return el('div', { class: 'found-main' }, [
    el('div', { style: 'display:flex;align-items:flex-start;justify-content:space-between;gap:8px' }, [
      el('div', {}, [
        el('div', { class: 'found-title', text: v.title || '' }),
        el('div', { class: 'found-meta', text: meta }),
      ]),
      el('span', {
        class: `status-badge ${vacancyStatusClass(st)}`,
        style: 'flex-shrink:0',
        text: st + (v.status_source === 'человек' ? ' · вручную' : ''),
      }),
    ]),
    // Текст причины, а не только красный бейдж: для «ошибки сценария»
    // бейдж говорит, ЧТО случилось, а починить сценарий можно, только
    // зная, КАКОЙ шаг не разобрался. Значение недоверенное — только `text:`.
    v.error ? el('div', { class: 'vac-error', text: v.error }) : null,
    // Проверки схемы здесь нет намеренно: el() валидирует href сам, по
    // построению, поэтому плохая схема в v.url просто не прикрепится.
    v.url ? el('a', {
      href: v.url, target: '_blank', rel: 'noopener noreferrer',
      class: 'found-link', text: 'Открыть на hh.ru →',
    }) : null,
  ]);
}
```

и внутри `loadHHVacancies` стройте карточку через неё:

```javascript
  fill(vacEl, vacs.slice(0, 4).map(v => el('div', { class: 'vac-card' }, [hhVacancyCard(v)])));
```

- [ ] **Шаг 6: перенацельте ловушку мёртвых колонок**

В `tests/test_stored_columns_are_read.py` замените `_vacancy_card_source()`:

```python
def _vacancy_card_source() -> str:
    """Тело `hhVacancyCard()` — единственного места, которое строит
    информационную часть карточки вакансии.

    Раньше здесь стояла `loadHHVacancies()`. Карточка переехала в
    собственную функцию, потому что её показывают два экрана — очередь и
    «Обзор», — а кнопки решения нужны только очереди. Набор проверок при
    этом не изменился: каждая колонка `hh_applications` по-прежнему обязана
    либо попасть в карточку, либо быть названной в `NOT_ON_THE_CARD`.
    """
    source = APP_JS.read_text(encoding="utf-8")
    match = re.search(r"function hhVacancyCard\(.*?\n\}", source, re.DOTALL)
    assert match, "hhVacancyCard() не найдена в app.js"
    return match.group(0)
```

Список `NOT_ON_THE_CARD` **не трогайте**: `vacancy_id`, `found_at` и
`applied_at` в карточку по-прежнему не попадают, а `vacancy_id` уезжает в
`data-arg` кнопок — то есть в строку очереди, а не в карточку. Именно
поэтому карточка и кнопки разделены: иначе `data-arg` сломал бы проверку на
вакуумность, и её пришлось бы ослаблять.

- [ ] **Шаг 7: напишите очередь**

Добавьте в `app.js` (рядом с `loadHHVacancies`):

```javascript
// ── Очередь найденного ────────────────────────────────────────────────
const foundState = { source: 'tg', filter: 'new', rows: [] };

function setSegActive(containerId, value) {
  const box = document.getElementById(containerId);
  if (!box) return;
  for (const btn of box.querySelectorAll('.seg-btn')) {
    btn.classList.toggle('active', btn.dataset.arg === value);
  }
}

async function foundSource(source) {
  foundState.source = source;
  setSegActive('foundSource', source);
  await loadFound();
}

async function foundFilter(filter) {
  foundState.filter = filter;
  setSegActive('foundFilter', filter);
  await loadFound();
}

async function loadFound() {
  const rows = await apiGet(`/found/${foundState.source}?status=${foundState.filter}`);
  foundState.rows = Array.isArray(rows) ? rows : [];
  renderFound();
}

function foundKey(row) {
  return foundState.source === 'tg' ? String(row.id) : row.vacancy_id;
}

function foundButtons(row) {
  const key = foundKey(row);
  if (row.status === 'новая') {
    return [
      el('button', {
        class: 'btn btn-primary', style: 'font-size:11.5px',
        text: 'Откликнулся', 'data-action': 'foundApply', 'data-arg': key,
      }),
      el('button', {
        class: 'btn btn-secondary', style: 'font-size:11.5px',
        text: 'Не подходит', 'data-action': 'foundDismiss', 'data-arg': key,
      }),
    ];
  }
  // Вернуть в очередь можно только оттуда, откуда это безопасно: «не
  // подходит» (передумал) и «пропущено» (клика не было). Бэкенд отвергает
  // остальное с 400 — здесь мы просто не предлагаем нажать то, что будет
  // отвергнуто.
  if (row.status === 'не подходит' || row.status === 'пропущено') {
    return [el('button', {
      class: 'btn btn-secondary', style: 'font-size:11.5px',
      text: 'Вернуть в очередь', 'data-action': 'foundReopen', 'data-arg': key,
    })];
  }
  return [];
}

function tgFoundCard(row) {
  const meta = ['@' + row.channel, row.matched_keyword, (row.usernames || []).join(' ')]
    .filter(Boolean).join(' · ');
  return el('div', { class: 'found-main' }, [
    el('div', { style: 'display:flex;align-items:flex-start;justify-content:space-between;gap:8px' }, [
      el('div', {}, [
        el('div', { class: 'found-title', text: row.preview || '(без текста)' }),
        el('div', { class: 'found-meta', text: meta }),
      ]),
      el('span', {
        class: `status-badge ${vacancyStatusClass(row.status)}`,
        style: 'flex-shrink:0', text: row.status || '',
      }),
    ]),
    // Ссылку собрал бэкенд (api/found_routes.py). el() всё равно проверит
    // схему — это его работа, а не работа места вызова.
    row.link ? el('a', {
      href: row.link, target: '_blank', rel: 'noopener noreferrer',
      class: 'found-link', text: 'Открыть в Telegram →',
    }) : null,
  ]);
}

function renderFound() {
  const box = document.getElementById('foundList');
  if (!box) return;
  if (!foundState.rows.length) {
    fill(box, el('div', { class: 'found-empty', text: 'Здесь пока пусто' }));
    return;
  }
  const card = foundState.source === 'tg' ? tgFoundCard : hhVacancyCard;
  fill(box, foundState.rows.map(row => el('div', { class: 'found-row' }, [
    card(row),
    el('div', { class: 'found-actions' }, foundButtons(row)),
  ])));
}

async function patchFound(key, status) {
  const reply = await apiPatch(`/found/${foundState.source}/${encodeURIComponent(key)}`, { status });
  if (!reply || reply.status !== 'saved') {
    // Отвергнутое сохранение нигде не показывается как успешное — то же
    // правило, что у configPatchOk во всех остальных экранах.
    showToast(configErrorDetail(reply));
    return;
  }
  await loadFound();
}

const foundApply = key => patchFound(key, 'откликнулся сам');
const foundDismiss = key => patchFound(key, 'не подходит');
const foundReopen = key => patchFound(key, 'новая');
```

Допишите в `ACTIONS`: `foundSource`, `foundFilter`, `foundApply`,
`foundDismiss`, `foundReopen`.

Замените запись в `PAGE_LOADERS`:

```javascript
  found: async () => { await loadFound(); },
```

- [ ] **Шаг 8: прогоните**

Run: `pytest tests/test_frontend_screens.py tests/test_frontend_events.py tests/test_frontend_safety.py tests/test_stored_columns_are_read.py -q`
Expected: PASS

- [ ] **Шаг 9: прогоните весь набор и проверьте руками**

Run: `make test`

Затем `make run`: положите в базу по одной записи каждого статуса (проще
всего — через `curl` к `/api/found/*` после ручной вставки строк, либо
просто прогоните воркер в безопасном режиме), откройте «Найдено» и
убедитесь: переключатели работают, ссылки открываются, «Откликнулся»
меняет бейдж, у отправленного отклика кнопок нет.

- [ ] **Шаг 10: проверьте дискриминирующую силу**

1. Верните `vacancyStatusClass` на подстроки → падает
   `test_the_status_badge_class_is_a_table_not_a_substring_game` (на строке
   «откликнулся сам»).
2. Уберите из `STATUS_CLASS` любую запись → падает
   `test_the_status_vocabulary_matches_the_backend`.
3. Соберите ссылку Telegram на фронтенде (`https://t.me/${row.channel}/...`)
   → падает `test_the_row_never_builds_its_own_telegram_link`.
4. Уберите проверку `reply.status !== 'saved'` в `patchFound` → тестом не
   ловится; допишите тест, исполняющий `patchFound` под node с подменённым
   `apiPatch`, либо, если это окажется слишком громоздко, зафиксируйте
   свойство статически: `assert "configErrorDetail" in
   _extract_function_source("patchFound")`.
5. Уберите `hhVacancyCard` и верните всё в `loadHHVacancies` → падает
   `test_stored_columns_are_read`.

- [ ] **Шаг 11: коммит**

```bash
git add frontend/index.html frontend/app.js frontend/style.css \
        tests/test_frontend_screens.py tests/test_stored_columns_are_read.py
git commit -m "$(cat <<'MSG'
feat(ui): экран «Найдено» — очередь со ссылками и ручными статусами

Ради чего весь подпроект: между «робот нашёл» и «я решил» появилось место.
Класс бейджа выбирается таблицей точных статусов, а не вхождением
подстроки: «откликнулся сам» не совпадал ни с чем и рисовался ожиданием.

Claude-Session: https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB
MSG
)"
```

---

## Задача 10: «Обзор» — метрики с переключателем источника

Сейчас на дашборде шесть плиток с тремя повторяющимися подписями:
«Откликов сегодня», «Вакансий найдено», «Всего откликов» — по два раза, для
каждого источника. Становится одна тройка с переключателем и общим итогом.

**Files:**
- Modify: `frontend/index.html` (`#page-overview`)
- Modify: `frontend/app.js`
- Modify: `frontend/style.css`
- Modify: `tests/test_frontend_screens.py`

**Interfaces:**
- Consumes: `GET /api/state` (существует); `setSegActive` (задача 9).
- Produces:
  - `metricValues(source, tg, hh) -> {found, sent, total, limit}` — чистая
    функция, поэтому проверяется исполнением под node
  - действие `metricsSource`
  - `loadHHVacancies` и блок `#hhRecentVacancies` **удаляются**: их место
    занял экран «Найдено»

- [ ] **Шаг 1: напишите падающие тесты**

Допишите в `tests/test_frontend_screens.py`:

```python
# ── Экран «Обзор» ─────────────────────────────────────────────────────


def test_the_metrics_are_three_tiles_not_six() -> None:
    """Шесть плиток с тремя повторяющимися подписями — это и есть
    «загруженность экрана разными блоками», от которой уходим."""
    index = _index()
    overview = index[index.index('id="page-overview"'):index.index('id="page-found"')]
    assert overview.count('class="metric"') == 3, (
        "на «Обзоре» должно быть ровно три плитки метрик с переключателем "
        "источника, а не по тройке на источник"
    )


def test_the_duplicated_metric_ids_are_gone() -> None:
    for gone in ("tgSentToday", "tgFoundToday", "tgSentTotal", "tgMetricBar",
                 "hhSentToday", "hhFoundToday", "hhTotalSent", "hhMetricBar"):
        assert f'id="{gone}"' not in _index(), f"{gone} остался в разметке"
        assert f"'{gone}'" not in _app(), f"{gone} остался в app.js"


def test_the_dashboard_no_longer_duplicates_the_found_screen() -> None:
    """Блок «последние вакансии» на дашборде был предшественником экрана
    «Найдено». Оставить оба — значит держать два списка одного и того же."""
    assert 'id="hhRecentVacancies"' not in _index()
    assert "loadHHVacancies" not in _app()


@skip_without_node
def test_the_combined_source_sums_both_workers() -> None:
    """«Все» — это сумма, а не Telegram по умолчанию. Ошибка тут не видна
    глазом: цифра выглядит правдоподобной ровно до того дня, когда второй
    воркер что-то нашёл."""
    source = _extract_function_source("metricValues")
    script = source + """
const tg = { found: 3, sent: 2, total: 10, limit: 25 };
const hh = { found: 4, sent: 1, total: 20, limit: 20 };
const cases = [
  ['tg',  { found: 3, sent: 2, total: 10, limit: 25 }],
  ['hh',  { found: 4, sent: 1, total: 20, limit: 20 }],
  ['all', { found: 7, sent: 3, total: 30, limit: 45 }],
];
for (const [source, expected] of cases) {
  const actual = metricValues(source, tg, hh);
  for (const key of Object.keys(expected)) {
    if (actual[key] !== expected[key]) {
      console.error(source, key, actual[key], '!=', expected[key]);
      process.exitCode = 1;
    }
  }
}
"""
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
```

- [ ] **Шаг 2: прогоните — тесты должны упасть**

Run: `pytest tests/test_frontend_screens.py -q`
Expected: FAIL — шесть плиток, `metricValues` не найдена

- [ ] **Шаг 3: перепишите блок метрик в разметке**

В `#page-overview` замените оба блока метрик (`metrics-grid` для TG и для
HH), оба заголовка-разделителя `section-split` и блок
`#hhRecentVacancies` на:

```html
    <div class="page-header">
      <div class="page-title">Обзор</div>
      <div class="seg" id="metricsSource">
        <button class="seg-btn active" data-action="metricsSource" data-arg="all">Все</button>
        <button class="seg-btn" data-action="metricsSource" data-arg="tg">Telegram</button>
        <button class="seg-btn" data-action="metricsSource" data-arg="hh">hh.ru</button>
      </div>
    </div>

    <div id="dataDirWarning" class="restart-banner" style="display:none"></div>

    <div class="worker-bar">
      <button class="btn-toggle btn-toggle-tg" id="btnToggleTG" data-action="toggleTG"></button>
      <button class="btn-toggle btn-toggle-hh" id="btnToggleHH" data-action="toggleHH"></button>
    </div>
    <div class="worker-alert" id="tgWorkerAlert" style="display:none"></div>
    <div class="worker-alert" id="hhWorkerAlert" style="display:none"></div>

    <div class="metrics-grid">
      <div class="metric">
        <div class="metric-label">Найдено сегодня</div>
        <div class="metric-value" id="mFound">0</div>
        <div class="metric-sub" id="mFoundSub"></div>
      </div>
      <div class="metric">
        <div class="metric-label">Откликов сегодня</div>
        <div class="metric-value" id="mSent">0</div>
        <div class="metric-bar"><div class="metric-bar-fill tg" id="mBar" style="width:0%"></div></div>
        <div class="metric-sub" id="mSub">из 0 в день</div>
      </div>
      <div class="metric">
        <div class="metric-label">Всего откликов</div>
        <div class="metric-value" id="mTotal">0</div>
      </div>
    </div>
```

Кнопки воркеров теперь пустые: их подпись целиком рисует
`updateWorkerButton` через `fill()` — она делала это и раньше, а текст в
разметке был мёртвым дублем, который показывался долю секунды до первого
опроса.

В `frontend/style.css` добавьте `.worker-bar { display: flex; gap: 10px;
flex-wrap: wrap; }` и удалите правила `.vac-card` (единственный
потребитель ушёл); `.vac-error` оставьте — её использует `hhVacancyCard`.

- [ ] **Шаг 4: перепишите метрики в `app.js`**

Замените `updateMetrics()` целиком:

```javascript
// ── Метрики ───────────────────────────────────────────────────────────
const metricsState = { source: 'all' };

// Чистая функция от трёх аргументов, а не от модульного состояния: только
// так её можно исполнить в тесте. «Все» — это сумма обоих источников;
// ошибка здесь не видна глазом, потому что цифра выглядит правдоподобной
// ровно до того дня, когда второй воркер что-то нашёл.
function metricValues(source, tg, hh) {
  if (source === 'tg') return tg;
  if (source === 'hh') return hh;
  return {
    found: tg.found + hh.found,
    sent: tg.sent + hh.sent,
    total: tg.total + hh.total,
    limit: tg.limit + hh.limit,
  };
}

function metricsSource(source) {
  metricsState.source = source;
  setSegActive('metricsSource', source);
  updateMetrics();
}

function updateMetrics() {
  const values = metricValues(
    metricsState.source,
    {
      found: tgState.foundToday, sent: tgState.sentToday,
      total: tgState.sentTotal, limit: tgState.maxPerDay,
    },
    {
      found: hhState.foundToday, sent: hhState.sentToday,
      total: hhState.totalSent, limit: hhState.maxPerDay,
    },
  );
  const put = (id, text) => {
    const node = document.getElementById(id);
    if (node) node.textContent = text;
  };
  put('mFound', values.found);
  put('mSent', values.sent);
  put('mTotal', values.total);
  put('mSub', `из ${values.limit} в день`);
  put('mFoundSub', metricsState.source === 'all' ? 'Telegram и hh.ru' : '');
  const bar = document.getElementById('mBar');
  if (bar) {
    bar.style.width = values.limit > 0
      ? Math.min(Math.round((values.sent / values.limit) * 100), 100) + '%'
      : '0%';
  }
}
```

Удалите `loadHHVacancies` целиком и её вызов из `init()`. Удалите
`updateDashboard()` и её вызовы: каналы, ключевые слова и стоп-слова
переезжают в редактор критериев (задача 11), и показывать их дважды на
одном экране незачем.

Допишите `metricsSource` в `ACTIONS`.

- [ ] **Шаг 5: прогоните**

Run: `pytest tests/test_frontend_screens.py tests/test_frontend_events.py tests/test_stored_columns_are_read.py -q`
Expected: PASS

- [ ] **Шаг 6: прогоните весь набор и проверьте руками**

Run: `make test`

`make run`: переключатель «Все / Telegram / hh.ru» меняет три цифры,
полоса лимита не выходит за 100%, кнопки запуска подписаны.

- [ ] **Шаг 7: проверьте дискриминирующую силу**

1. В `metricValues` для `'all'` верните `tg` → падает
   `test_the_combined_source_sums_both_workers`.
2. Верните вторую тройку плиток в разметку → падает
   `test_the_metrics_are_three_tiles_not_six`.
3. Оставьте `loadHHVacancies` → падает
   `test_the_dashboard_no_longer_duplicates_the_found_screen`.

- [ ] **Шаг 8: коммит**

```bash
git add frontend/index.html frontend/app.js frontend/style.css \
        tests/test_frontend_screens.py
git commit -m "$(cat <<'MSG'
feat(ui): три плитки метрик с переключателем источника вместо шести

Шесть плиток с тремя повторяющимися подписями — это и была та самая
загруженность экрана. Блок последних вакансий на дашборде удалён: его
место занял экран «Найдено», а два списка одного и того же расходятся.

Claude-Session: https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB
MSG
)"
```

---

## Задача 11: «Обзор» — критерии активного пресета тремя группами

Бэкенд разделил критерии поиска и глобальные настройки ещё в подпроекте 1;
интерфейс за этим не пошёл. Семь карточек из тринадцати в «Настройках» —
это критерии, принадлежащие пресету, а ещё четыре живут на трёх отдельных
страницах. Всё это собирается на «Обзоре» **тремя** карточками рядом с
пресетом, к которому относится (решение D21).

Пять кнопок «Сохранить» (каналы, ключевые слова, шаблон, сопроводительное,
настройки HH) становятся **одной**. Это не только про плотность: пять
кнопок — пять частичных сохранений, а с задачи 7 один `PATCH` пресета
применяется целиком или никак.

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `frontend/style.css`
- Modify: `tests/test_frontend_screens.py`
- Modify: `tests/test_frontend_events.py` (список обработчиков)
- Modify: `tests/test_frontend_presets.py`, `tests/test_frontend_config_save.py`
  (если они ссылаются на удалённые действия)

**Interfaces:**
- Consumes: `patchCriteria` (существует), `renderChoiceBox`, `chosenCodes`,
  `loadDictionaries` (существуют); `GET /api/resumes`.
- Produces:
  - три карточки: `#criteriaWhere`, `#criteriaWhat`, `#criteriaHow`
  - `collectCriteria() -> object` — чистая сборка патча из полей формы
  - действие `saveCriteria`
  - `#criteriaResume` — выбор резюме из библиотеки для этого пресета
  - удалены действия `saveChannels`, `saveKeywords`, `saveTemplate`,
    `saveHHCoverLetter`, `chooseResume`

- [ ] **Шаг 1: напишите падающие тесты**

Допишите в `tests/test_frontend_screens.py`:

```python
# ── Критерии живут рядом с пресетом ───────────────────────────────────

CRITERIA_FIELDS = (
    "newChannel", "channelEditList",       # каналы Telegram
    "kwList", "exList",                    # ключевые слова и стоп-слова TG
    "hhKwList", "hhExList",                # профессии и стоп-слова hh.ru
    "hhExperience", "hhSalaryFrom", "hhSearchPeriod",
    "hhScheduleBox", "hhEmploymentBox", "hhResumeId",
    "templateText", "hhCoverLetter", "criteriaResume",
)


def _overview() -> str:
    index = _index()
    return index[index.index('id="page-overview"'):index.index('id="page-found"')]


def _settings() -> str:
    index = _index()
    return index[index.index('id="page-settings"'):]


def test_every_criteria_field_lives_on_the_overview() -> None:
    """«Что искать» принадлежит пресету и показывается вместе с ним; в
    «Настройках» остаётся только то, что общее для всех пресетов."""
    overview = _overview()
    for field in CRITERIA_FIELDS:
        assert f'id="{field}"' in overview, f"поле критериев {field} не на «Обзоре»"


def test_no_criteria_field_is_left_in_the_settings() -> None:
    settings = _settings()
    for field in CRITERIA_FIELDS:
        assert f'id="{field}"' not in settings, (
            f"{field} остался в «Настройках» — он принадлежит пресету"
        )


def test_the_criteria_are_grouped_into_three_cards() -> None:
    overview = _overview()
    for group in ("criteriaWhere", "criteriaWhat", "criteriaHow"):
        assert f'id="{group}"' in overview


def test_there_is_one_save_button_for_the_criteria() -> None:
    """Пять кнопок сохранения — это пять частичных сохранений. Одна кнопка
    и один PATCH: с задачи 7 он применяется целиком или никак."""
    overview = _overview()
    assert overview.count('data-action="saveCriteria"') == 1
    for gone in ("saveChannels", "saveKeywords", "saveTemplate",
                 "saveHHCoverLetter", "chooseResume"):
        assert gone not in _index(), f"кнопка {gone} осталась в разметке"
        assert gone not in _app(), f"обработчик {gone} остался в app.js"


def test_the_criteria_save_goes_through_one_patch() -> None:
    """Патч уходит одним запросом на пресет, а не полем за полем: иначе
    отказ на пятом поле оставил бы четыре сохранёнными."""
    source = _extract_function_source("saveCriteria")
    assert source.count("patchCriteria") == 1


@skip_without_node
def test_collect_criteria_sends_numbers_as_numbers() -> None:
    """Пустое числовое поле сериализуется в null и отвергается с 422 —
    это уже ловили в подпроекте 1. Пустое значит «ноль»."""
    source = _extract_function_source("collectCriteria")
    script = """
const fields = {
  hhSalaryFrom: '', hhSearchPeriod: '3', hhResumeId: ' 12345 ',
  templateText: 'привет', hhCoverLetter: '', hhExperience: 'between1And3',
  criteriaResume: '',
};
global.document = {
  getElementById: id => (id in fields ? { value: fields[id] } : null),
};
""" + source + """
const patch = collectCriteria({
  channels: ['qajobs'], tg_keywords: ['qa'], tg_exclude: [],
  professions: ['QA'], hh_exclude: [], hh_schedule: [], hh_employment: [],
});
if (patch.hh_salary_from !== 0) { console.error('salary', patch.hh_salary_from); process.exitCode = 1; }
if (patch.hh_search_period !== 3) { console.error('period', patch.hh_search_period); process.exitCode = 1; }
if (patch.hh_resume_id !== '12345') { console.error('resume id', patch.hh_resume_id); process.exitCode = 1; }
if (patch.resume_id !== null) { console.error('resume', patch.resume_id); process.exitCode = 1; }
"""
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
```

- [ ] **Шаг 2: прогоните — тесты должны упасть**

Run: `pytest tests/test_frontend_screens.py -q`
Expected: FAIL — поля критериев лежат в «Настройках»

- [ ] **Шаг 3: соберите три карточки в разметке**

В `#page-overview`, после блока пресетов и перед «Последними действиями»,
поставьте:

```html
    <div class="page-header" style="margin-top:6px">
      <div class="card-title" style="margin:0">Критерии активного пресета</div>
      <button class="btn btn-primary" data-action="saveCriteria">Сохранить критерии</button>
    </div>

    <div class="three-col">
      <div class="card" id="criteriaWhere">
        <div class="card-title">Где искать</div>
        <div class="field-label">Каналы Telegram</div>
        <div id="channelEditList"></div>
        <div class="add-row">
          <input type="text" id="newChannel" placeholder="@username или username">
          <button class="btn btn-secondary" data-action="addChannel">+</button>
        </div>
        <div class="field-label">Профессии на hh.ru</div>
        <div id="hhKwList"></div>
        <div class="add-row">
          <input type="text" id="newHHKw" placeholder="Название профессии">
          <button class="btn btn-secondary" data-action="addHHKw">+</button>
        </div>
      </div>

      <div class="card" id="criteriaWhat">
        <div class="card-title">Что искать</div>
        <div class="field-label">Ключевые слова Telegram</div>
        <div id="kwList"></div>
        <div class="add-row">
          <input type="text" id="newKw" placeholder="Ключевое слово">
          <button class="btn btn-secondary" data-action="addKw">+</button>
        </div>
        <div class="field-label">Стоп-слова Telegram</div>
        <div id="exList"></div>
        <div class="add-row">
          <input type="text" id="newEx" placeholder="Слово для исключения">
          <button class="btn btn-secondary" data-action="addEx">+</button>
        </div>
        <div class="field-label">Стоп-слова hh.ru</div>
        <div id="hhExList"></div>
        <div class="add-row">
          <input type="text" id="newHHEx" placeholder="senior, lead...">
          <button class="btn btn-secondary" data-action="addHHEx">+</button>
        </div>
        <div class="field-label">Опыт</div>
        <select id="hhExperience" class="field-select"></select>
        <div class="field-label">Зарплата от</div>
        <input type="number" id="hhSalaryFrom" value="0" min="0" step="10000" class="num-input">
        <div class="field-label">Период поиска, дней</div>
        <input type="number" id="hhSearchPeriod" value="1" min="1" max="30" class="num-input">
        <div class="field-label">График работы</div>
        <div id="hhScheduleBox" class="choice-box"></div>
        <div class="field-label">Тип занятости</div>
        <div id="hhEmploymentBox" class="choice-box"></div>
      </div>

      <div class="card" id="criteriaHow">
        <div class="card-title">Чем отвечать</div>
        <div class="field-label">Текст отклика в Telegram</div>
        <textarea class="template-area" id="templateText" rows="7"></textarea>
        <div class="field-label">Сопроводительное письмо на hh.ru</div>
        <textarea class="template-area" id="hhCoverLetter" rows="7"
                  placeholder="Здравствуйте! Откликаюсь на вашу вакансию..."></textarea>
        <div class="field-label">Резюме из библиотеки</div>
        <select id="criteriaResume" class="field-select"></select>
        <div class="field-label">ID резюме на hh.ru</div>
        <input class="api-input" type="text" id="hhResumeId"
               placeholder="Найти в URL: hh.ru/resume/XXXXXXXX">
      </div>
    </div>
```

Исходные карточки этих полей в `#page-settings` (и перенесённые задачей 8
карточки каналов, ключевых слов и шаблона) удалите — все поля теперь здесь,
по одному разу. Карточку «Регион поиска» удалите совсем: с решения D8 там
константа, выбирать нечего.

Подпись `{канал}`, `{ключевое_слово}`, `{профессия}` над полем шаблона
сохраните — она объясняет, что можно подставить.

В `frontend/style.css`:

```css
.field-label { font-size: 11px; font-weight: 600; color: var(--muted);
  margin: 12px 0 5px; }
.field-label:first-of-type { margin-top: 0; }
.field-select { width: 100%; padding: 6px; border: 1px solid var(--border);
  border-radius: var(--radius-sm); font-size: 12px; background: var(--bg); color: var(--text); }
.add-row { display: flex; gap: 6px; margin-top: 6px; }
.add-row input { flex: 1; min-width: 0; }
.choice-box { display: flex; gap: 12px; flex-wrap: wrap; }
```

- [ ] **Шаг 4: соберите сохранение в одну функцию**

В `app.js` удалите `saveChannels`, `saveKeywords`, `saveTemplate`,
`saveHHCoverLetter` и `chooseResume`; на их место:

```javascript
// Сборка патча критериев из полей формы. Чистая функция от документа и
// уже собранных списков — поэтому её можно исполнить в тесте.
//
// Числа приводятся здесь, а не на бэкенде: пустое числовое поле даёт
// пустую строку, `JSON.stringify` кладёт в тело `null`, и pydantic
// отвечает 422 на попытку сохранить «ничего не ввёл». Пустое значит
// «ноль» — это уже чинили в подпроекте 1.
function collectCriteria(lists) {
  const value = id => {
    const node = document.getElementById(id);
    return node ? node.value : '';
  };
  const resume = value('criteriaResume');
  return {
    ...lists,
    hh_experience: value('hhExperience'),
    hh_salary_from: Number(value('hhSalaryFrom')) || 0,
    hh_search_period: Number(value('hhSearchPeriod')) || 1,
    hh_resume_id: value('hhResumeId').trim(),
    template: value('templateText'),
    hh_cover_letter: value('hhCoverLetter'),
    resume_id: resume === '' ? null : Number(resume),
  };
}

async function saveCriteria() {
  const patch = collectCriteria({
    channels: tgState.channels,
    tg_keywords: tgState.keywords,
    tg_exclude: tgState.exclude,
    professions: hhState.keywords,
    hh_exclude: hhState.exclude,
    hh_schedule: chosenCodes('hh-schedule'),
    hh_employment: chosenCodes('hh-employment'),
  });
  // Один PATCH на всё: с задачи 7 он применяется целиком или никак,
  // поэтому отказ на любом поле не оставляет остальные сохранёнными.
  // `patchCriteria` сам покажет причину отказа и вернёт false — говорить
  // «сохранено» можно только по его слову.
  if (await patchCriteria(patch)) showToast('Критерии сохранены');
}
```

`patchCriteria` уже существует и уже проверяет ответ через
`configPatchOk`/`configErrorDetail` — ничего дублировать не нужно.

- [ ] **Шаг 5: наполните выбор резюме**

Замените `loadResumes()` так, чтобы список библиотеки остался в
«Настройках» (задача 13 его туда положит), а выпадающий список на «Обзоре»
наполнялся из того же ответа:

```javascript
function renderResumePicker(rows) {
  const box = document.getElementById('criteriaResume');
  if (!box) return;
  const chosen = presetState.criteria.resume_id;
  fill(box, [
    el('option', { value: '', text: '— без вложения —' }),
    ...rows.map(row => {
      const option = el('option', { value: String(row.id), text: row.original_name });
      if (chosen === row.id) option.selected = true;
      return option;
    }),
  ]);
}
```

и вызовите `renderResumePicker(rows)` из `loadResumes()` рядом с отрисовкой
списка библиотеки.

Из строк самой библиотеки уберите кнопку «Выбрать» (`data-action:
'chooseResume'`): выбор переехал в выпадающий список и сохраняется вместе
с остальными критериями. Кнопка «Удалить» остаётся. Если этого не сделать,
`tests/test_frontend_events.py::test_every_markup_action_has_a_handler`
покажет мёртвую кнопку — обработчика у неё уже нет.

- [ ] **Шаг 6: заполняйте поля критериев при загрузке**

В `loadActiveCriteria()` после присвоения `presetState.criteria` добавьте
заполнение всех полей группы (`hhExperience`, `hhSalaryFrom`,
`hhSearchPeriod`, `hhResumeId`, `templateText`, `hhCoverLetter`), отрисовку
`renderChoiceBox('hhScheduleBox', …, 'hh-schedule')` и
`renderChoiceBox('hhEmploymentBox', …, 'hh-employment')` через
`loadDictionaries()`, и `renderResumePicker`. Сейчас часть этого делает
`loadSettings()` — уберите оттуда всё, что относится к критериям, и
перенесите сюда: `loadSettings()` остаётся только про глобальное.

Пропишите `loadActiveCriteria()` в `PAGE_LOADERS.overview`.

- [ ] **Шаг 7: обновите список обработчиков в ловушке**

В `tests/test_frontend_events.py::test_markup_carries_the_converted_handlers`
уберите из множества `expected` пять имён и допишите в докстринг, почему:

```python
    `saveChannels`, `saveKeywords`, `saveTemplate`, `saveHHCoverLetter` и
    `chooseResume` ушли осознанно: пять кнопок сохранения критериев стали
    одной (`saveCriteria`), а выбор резюме из библиотеки — выпадающим
    списком, который сохраняется вместе с остальными критериями. Пять
    кнопок означали пять частичных сохранений; один PATCH пресета с
    задачи 7 применяется целиком или никак.
```

и добавьте `"saveCriteria"` в `expected`. Это не ослабление: множество
по-прежнему требует, чтобы каждое перечисленное имя присутствовало в
разметке, а `test_every_handler_is_reachable` по-прежнему запрещает
мёртвые записи в `ACTIONS`.

- [ ] **Шаг 8: прогоните**

Run: `pytest tests/test_frontend_screens.py tests/test_frontend_events.py tests/test_frontend_presets.py tests/test_frontend_config_save.py -q`
Expected: PASS

- [ ] **Шаг 9: прогоните весь набор и проверьте руками**

Run: `make test`

`make run`: заполните все три группы, нажмите «Сохранить критерии»,
перезагрузите страницу — значения на месте. Введите в «Опыт» несуществующий
код через консоль браузера и сохраните: должен появиться тост с причиной, а
не «сохранено».

- [ ] **Шаг 10: проверьте дискриминирующую силу**

1. Верните любое поле критериев в `#page-settings` → падает
   `test_no_criteria_field_is_left_in_the_settings`.
2. Разбейте `saveCriteria` на два `patchCriteria` → падает
   `test_the_criteria_save_goes_through_one_patch`.
3. Уберите `|| 0` из `hh_salary_from` → падает
   `test_collect_criteria_sends_numbers_as_numbers`.
4. Отдавайте `resume_id: Number(resume)` без проверки на пустую строку →
   падает тот же тест (`Number('')` это `0`, а не `null`).
5. Верните кнопку `saveChannels` → падает
   `test_there_is_one_save_button_for_the_criteria`.

- [ ] **Шаг 11: коммит**

```bash
git add frontend/index.html frontend/app.js frontend/style.css \
        tests/test_frontend_screens.py tests/test_frontend_events.py
git commit -m "$(cat <<'MSG'
feat(ui): критерии пресета тремя группами на «Обзоре», одна кнопка сохранения

Бэкенд разделил критерии и глобальные настройки в подпроекте 1; интерфейс
приводится в соответствие (D21). Пять кнопок сохранения стали одной: пять
кнопок означали пять частичных сохранений.

Claude-Session: https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB
MSG
)"
```

---

## Задача 12: экран «Отправлено»

Переписка в Telegram (нынешняя вкладка «Чаты») и отправленные отклики
hh.ru — в одном месте, источник переключателем.

**Files:**
- Modify: `frontend/index.html` (`#page-sent`)
- Modify: `frontend/app.js`
- Modify: `frontend/style.css`
- Modify: `tests/test_frontend_screens.py`

**Interfaces:**
- Consumes: `GET /api/found/hh` (задача 6); `hhVacancyCard` (задача 9);
  `setSegActive` (задача 9); `renderChats`, `openChat` (существуют).
- Produces:
  - `APPLIED_STATUSES` — массив статусов «отклик ушёл», зеркалит
    `job_monitor.statuses.APPLIED`
  - `loadSentHh()`, действие `sentSource`

- [ ] **Шаг 1: напишите падающие тесты**

Допишите в `tests/test_frontend_screens.py`:

```python
# ── Экран «Отправлено» ────────────────────────────────────────────────


def _sent() -> str:
    index = _index()
    return index[index.index('id="page-sent"'):index.index('id="page-settings"')]


def test_the_sent_screen_switches_between_sources() -> None:
    sent = _sent()
    assert 'data-action="sentSource"' in sent
    assert 'id="sentTg"' in sent
    assert 'id="sentHh"' in sent


def test_the_applied_vocabulary_matches_the_backend() -> None:
    """Список «что считать отправленным» есть и на бэкенде, и здесь. Два
    списка, которые разошлись, — это пустой экран «Отправлено» при полной
    базе откликов."""
    from job_monitor import statuses

    match = re.search(r"const APPLIED_STATUSES = \[(.*?)\];", _app(), re.DOTALL)
    assert match, "APPLIED_STATUSES не найдена в app.js"
    known = set(re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)))
    assert known == set(statuses.APPLIED), (
        f"фронтенд считает отправленным {sorted(known)}, "
        f"бэкенд — {sorted(statuses.APPLIED)}"
    )


def test_the_sent_screen_shows_manual_answers_too() -> None:
    """Ручной отклик — тоже отклик. Показывать только робота значило бы
    прятать половину истории от человека, который её и создал."""
    source = _extract_function_source("loadSentHh")
    assert "APPLIED_STATUSES" in source
```

- [ ] **Шаг 2: прогоните — тесты должны упасть**

Run: `pytest tests/test_frontend_screens.py -q`
Expected: FAIL — `sentSource` не найден

- [ ] **Шаг 3: перестройте экран в разметке**

Оберните перенесённую задачей 8 разметку чатов и добавьте вторую панель:

```html
  <div class="page" id="page-sent">
    <div class="page-header">
      <div class="page-title">Отправлено</div>
      <div class="seg" id="sentSource">
        <button class="seg-btn active" data-action="sentSource" data-arg="tg">Telegram</button>
        <button class="seg-btn" data-action="sentSource" data-arg="hh">hh.ru</button>
      </div>
    </div>

    <div id="sentTg" class="sent-pane">
      <!-- сюда целиком переезжает существующая разметка чатов:
           #chatList, #chatEmpty, #chatView, #chatMessages, #chatInput -->
    </div>

    <div id="sentHh" class="sent-pane" style="display:none">
      <div class="card" style="padding:0;overflow:hidden">
        <div id="sentHhList"></div>
      </div>
    </div>
  </div>
```

Прежние инлайновые стили контейнера чатов (`padding:0;flex-direction:row;
height:100vh;gap:0`) переезжают в CSS, потому что теперь это не страница, а
панель внутри страницы:

```css
.sent-pane { flex: 1; min-height: 0; }
.sent-pane .chat-layout { display: flex; height: calc(100vh - 160px);
  border: 1px solid var(--border); border-radius: var(--radius);
  overflow: hidden; background: var(--card-bg); }
```

Внешний контейнер списка и окна переписки оберните в
`<div class="chat-layout"> … </div>`.

- [ ] **Шаг 4: добавьте панель hh.ru в `app.js`**

```javascript
// ── Отправленное ──────────────────────────────────────────────────────
// Что считать отправленным. Зеркалит job_monitor/statuses.py::APPLIED;
// tests/test_frontend_screens.py сторожит, что списки не разошлись —
// разойдись они, экран был бы пуст при полной базе откликов.
const APPLIED_STATUSES = ['отклик отправлен', 'откликнулся сам'];

function sentSource(source) {
  setSegActive('sentSource', source);
  const tg = document.getElementById('sentTg');
  const hh = document.getElementById('sentHh');
  if (tg) tg.style.display = source === 'tg' ? '' : 'none';
  if (hh) hh.style.display = source === 'hh' ? '' : 'none';
  return source === 'tg' ? renderChats() : loadSentHh();
}

async function loadSentHh() {
  const rows = await apiGet('/found/hh?status=decided');
  const box = document.getElementById('sentHhList');
  if (!box) return;
  // Ручной отклик — тоже отклик: показывать только робота значило бы
  // прятать от человека половину его собственной истории.
  const applied = (Array.isArray(rows) ? rows : [])
    .filter(row => APPLIED_STATUSES.includes(row.status));
  if (!applied.length) {
    fill(box, el('div', { class: 'found-empty', text: 'Откликов пока нет' }));
    return;
  }
  fill(box, applied.map(row => el('div', { class: 'found-row' }, [hhVacancyCard(row)])));
}
```

Допишите `sentSource` в `ACTIONS` и обновите загрузчик экрана:

```javascript
  sent: async () => { await renderChats(); },
```

оставьте как есть — панель Telegram открыта по умолчанию, вторая грузится
при переключении.

- [ ] **Шаг 5: прогоните**

Run: `pytest tests/test_frontend_screens.py tests/test_frontend_events.py -q`
Expected: PASS

- [ ] **Шаг 6: прогоните весь набор и проверьте руками**

Run: `make test`

`make run`: обе панели переключаются, переписка открывается и
прокручивается, список откликов hh.ru показывает и роботные, и ручные.

- [ ] **Шаг 7: проверьте дискриминирующую силу**

1. Уберите `'откликнулся сам'` из `APPLIED_STATUSES` → падает
   `test_the_applied_vocabulary_matches_the_backend`.
2. Отрисуйте в `loadSentHh` все `decided` без фильтра → падает
   `test_the_sent_screen_shows_manual_answers_too`.
3. Уберите панель `#sentHh` → падает
   `test_the_sent_screen_switches_between_sources`.

- [ ] **Шаг 8: коммит**

```bash
git add frontend/index.html frontend/app.js frontend/style.css \
        tests/test_frontend_screens.py
git commit -m "$(cat <<'MSG'
feat(ui): экран «Отправлено» — переписка Telegram и отклики hh.ru

Ручной отклик показывается наравне с роботным: прятать его значило бы
скрывать от человека половину его собственной истории.

Claude-Session: https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB
MSG
)"
```

---

## Задача 13: «Настройки» — четыре группы и вынос мусора

Тринадцать карточек становятся четырьмя группами. Семь из тринадцати уже
уехали на «Обзор» задачей 11; остаётся привести в порядок то, что общее для
всех пресетов, и убрать то, что лжёт.

**Files:**
- Modify: `frontend/index.html` (`#page-settings`)
- Modify: `frontend/app.js`
- Modify: `frontend/style.css`
- Modify: `tests/test_frontend_screens.py`
- Modify: `tests/test_frontend_events.py` (список обработчиков)
- Modify: `tests/test_frontend_config_save.py` (node-пины I4 перенацеливаются)
- Modify: `README.md` (описание экранов и вкладок логов)

**Interfaces:**
- Consumes: `PATCH /api/config` (существует).
- Produces:
  - четыре группы: `#settingsMode`, `#settingsAccess`, `#settingsResumes`,
    `#settingsAdvanced`
  - `saveGlobalSettings` вместо `saveTGSettings` и `saveHHSettings`

- [ ] **Шаг 1: напишите падающие тесты**

Допишите в `tests/test_frontend_screens.py`:

```python
# ── Экран «Настройки» ─────────────────────────────────────────────────


def test_the_settings_are_four_groups() -> None:
    settings = _settings()
    for group in ("settingsMode", "settingsAccess",
                  "settingsResumes", "settingsAdvanced"):
        assert f'id="{group}"' in settings, f"группа {group} не найдена"


def test_the_settings_have_one_save_button() -> None:
    settings = _settings()
    assert settings.count('data-action="saveGlobalSettings"') == 1
    for gone in ("saveTGSettings", "saveHHSettings"):
        assert gone not in _index(), f"кнопка {gone} осталась"
        assert gone not in _app(), f"обработчик {gone} остался"


def test_the_region_card_is_gone() -> None:
    """С решения D8 регион — константа: карточка предлагала выбор там, где
    выбирать нечего."""
    assert "hhAreaLabel" not in _index()
    assert "hhAreaLabel" not in _app()


def test_the_telegram_login_card_is_named_after_what_it_does() -> None:
    """«Авторизация чатов (session_web)» названа по файлу сессии, которого
    в приложении больше нет: имя обещало то, чего не существует."""
    index = _index()
    assert "session_web" not in index
    assert "Вход в Telegram" in index


def test_no_tg_hh_dividers_are_left() -> None:
    """Шесть разделителей «TG / HH» были следствием нарезки по источнику.
    Источник теперь переключатель, делить пополам нечего."""
    assert "section-split" not in _index()


def test_the_safe_mode_label_covers_both_workers() -> None:
    """Решение D17: один переключатель на оба воркера. Подпись, говорящая
    только про Telegram, была бы прямой неправдой — hh.ru теперь тоже его
    слушает."""
    index = _index()
    safe = index[index.index('id="toggleSafe"') - 900:index.index('id="toggleSafe"')]
    assert "hh.ru" in safe or "оба" in safe.lower(), (
        "подпись безопасного режима не говорит, что он действует и на hh.ru"
    )
```

- [ ] **Шаг 2: прогоните — тесты должны упасть**

Run: `pytest tests/test_frontend_screens.py -q`
Expected: FAIL — групп нет, `section-split` на месте

- [ ] **Шаг 3: перестройте экран**

`#page-settings` целиком:

```html
  <div class="page" id="page-settings">
    <div class="page-header">
      <div class="page-title">Настройки</div>
      <button class="btn btn-primary" data-action="saveGlobalSettings">Сохранить</button>
    </div>
    <p class="page-hint">
      Здесь только то, что общее для всех пресетов. Что искать — на «Обзоре»,
      рядом с пресетом, к которому относится.
    </p>

    <div class="card" id="settingsMode">
      <div class="card-title">Режим и лимиты</div>
      <!-- toggleSafe, toggleHistory, toggleTGAutostart, toggleHHAutostart,
           maxPerDay, historyLimit, hhMaxPerDayInput, hhCheckInterval -->
    </div>

    <div class="card" id="settingsAccess">
      <div class="card-title">Доступы</div>
      <!-- apiId, apiHash, saveApiKeys; вход в Telegram (authStatus, authForm,
           authPhone, authCode, auth2fa); вход в hh.ru (hhLoginStatus,
           hhLoginButtons, btnHhLoginCancel) -->
    </div>

    <div class="card" id="settingsResumes">
      <div class="card-title">Библиотека резюме</div>
      <!-- fileZone, fileInput, fileZoneLabel, resumeList -->
    </div>

    <div class="card" id="settingsAdvanced">
      <div class="card-title">Продвинутое</div>
      <!-- редактор сценария Selenium: seleniumStepsList, newStepType,
           newStepSelector, newStepValue, addStep;
           затем консоль логов: logTabTG, logTabHH, logConsole -->
    </div>
  </div>
```

Комментарии в разметке заменяются перенесённым содержимым существующих
карточек — переносите блоки целиком, вместе с их `data-action`.
Изменения по существу, а не переносом, ровно три:

1. Заголовок «Авторизация чатов (session_web)» → «Вход в Telegram».
   Второго файла сессии в приложении больше нет; имя обещало то, чего не
   существует.
2. Подпись безопасного режима: «Ищу вакансии и складываю в очередь, но
   ничего не отправляю — ни в Telegram, ни на hh.ru». Раньше галочка
   касалась только Telegram, теперь (решение D17) — обоих.
3. Карточка «Регион поиска» удалена (была удалена задачей 11 — проверьте,
   что её нет).

Добавьте в CSS `.page-hint { font-size: 12px; color: var(--muted); margin: -6px 0 4px; }`.

- [ ] **Шаг 4: слейте два сохранения в одно**

В `app.js` замените `saveTGSettings` и `saveHHSettings` одной функцией.
Критериев в них больше нет — задача 11 их вынесла, — так что обе патчили
`/api/config` одинаковым способом:

```javascript
async function saveGlobalSettings() {
  // Пустое поле уезжает как null и получает 422 с объяснением — умолчание
  // сюда НЕ подставляется. Это разница между лимитом и критерием: у
  // критерия «пусто» имеет смысл («зарплата от» без числа значит «не
  // важно», см. collectCriteria), а лимит существует, чтобы не забанили
  // аккаунт, и выбрать его за человека молча — значит соврать ему о том,
  // сколько сообщений уйдёт сегодня. Это пин дефекта I4: до него
  // очищенное поле давало «сохранено» и не сохранялось.
  const number = id => {
    const node = document.getElementById(id);
    const value = parseInt(node ? node.value : '', 10);
    return Number.isNaN(value) ? null : value;
  };
  const checked = id => !!document.getElementById(id)?.checked;
  const patch = {
    safe_mode: checked('toggleSafe'),
    parse_history: checked('toggleHistory'),
    tg_autostart: checked('toggleTGAutostart'),
    hh_autostart: checked('toggleHHAutostart'),
    max_per_day: number('maxPerDay'),
    history_limit: number('historyLimit'),
    hh_max_per_day: number('hhMaxPerDayInput'),
    hh_check_interval: number('hhCheckInterval'),
    hh_selenium_steps: hhState.seleniumSteps,
  };
  const reply = await apiPatch('/config', patch);
  // Отвергнутое сохранение нигде не показывается как успешное.
  if (!configPatchOk(reply)) { showToast(configErrorDetail(reply)); return; }
  tgState.safeMode = patch.safe_mode;
  updateStatusBar();
  showToast('Настройки сохранены');
}
```

`updateStatusBar()` вызывается сразу: полоса состояния — единственное
место, где написано, отправляет приложение что-то наружу или нет, и она
обязана перестать врать в тот же миг, а не через три секунды до
следующего опроса.

Допишите `saveGlobalSettings` в `ACTIONS`, уберите оттуда `saveTGSettings`
и `saveHHSettings`.

- [ ] **Шаг 5: перенацельте node-тесты сохранения**

`tests/test_frontend_config_save.py` исполняет `saveTGSettings` под node —
это пин дефекта I4: очищенное числовое поле отвергается сервером, и
обработчик обязан показать причину, а не сказать «сохранено». Функции
больше нет, поэтому оба теста
(`test_save_tg_settings_reports_the_rejection_not_success` и
`test_save_tg_settings_still_reports_success_on_a_healthy_save`) переводятся
на `saveGlobalSettings`:

1. `_extract_function_source("saveTGSettings")` →
   `_extract_function_source("saveGlobalSettings")`, вызов
   `await saveTGSettings();` → `await saveGlobalSettings();`.
2. Дополните заглушку DOM полями, которые читает новая функция:

```javascript
        document.__elements = {
          toggleSafe: { checked: true },
          toggleHistory: { checked: false },
          toggleTGAutostart: { checked: false },
          toggleHHAutostart: { checked: false },
          maxPerDay: { value: '' },       // очищено пользователем -> null -> 422
          historyLimit: { value: '50' },
          hhMaxPerDayInput: { value: '20' },
          hhCheckInterval: { value: '1800' },
        };
```

3. Рядом с заглушкой состояния добавьте `const hhState = { seleniumSteps: [] };`
   и `function updateStatusBar() {}` — новая функция их трогает.
4. Переименуйте оба теста в `test_save_global_settings_*` и поправьте
   докстринги: сценарий тот же, функция другая.

Проверки **не ослабляйте**: и «не сказал «сохранено» на отказ», и «сказал
на успех» обязаны остаться. Убедитесь, что первый тест краснеет, если
вернуть в `number()` подстановку умолчания вместо `null`.

Run: `pytest tests/test_frontend_config_save.py -q`
Expected: PASS

- [ ] **Шаг 6: обновите список обработчиков в ловушке**

В `tests/test_frontend_events.py::test_markup_carries_the_converted_handlers`
замените `"saveTGSettings", "saveHHSettings"` на `"saveGlobalSettings"` и
допишите в докстринг причину: критерии уехали на «Обзор», и обе кнопки
патчили `/api/config` одинаково — две кнопки для одного запроса это
приглашение сохранить половину.

- [ ] **Шаг 7: обновите README**

В README поправьте:

- список возможностей (строка «Единый дашборд для TG и HH с метриками») —
  назовите четыре экрана и очередь найденного с ручным откликом;
- таблицу файлов логов: «вкладка «Логи TG» в UI» → «Настройки →
  Продвинутое»;
- если где-то описан порядок работы через старые страницы — приведите к
  новым названиям.

Блок «Структура проекта» не трогайте — модулей эта задача не добавляет.

- [ ] **Шаг 8: прогоните весь набор**

Run: `make test`
Expected: PASS

- [ ] **Шаг 9: полный ручной проход**

`make run` и пройдите весь сценарий целиком, на живом приложении:

1. Создайте пресет, заполните критерии, сохраните, перезагрузите — на месте.
2. Включите безопасный режим, запустите оба воркера — полоса состояния
   говорит «собираю, не отправляю».
3. Дождитесь находок, откройте «Найдено»: обе вкладки, ссылки открываются.
4. Нажмите «Откликнулся» на записи Telegram — контакт больше не получит
   сообщение от воркера; проверьте по `GET /api/tg/chats` или в базе.
5. Нажмите «Не подходит» на вакансии hh.ru, затем «Вернуть в очередь» —
   получается; на отправленном отклике кнопки возврата нет.
6. «Отправлено» показывает и роботные, и ручные отклики.
7. Настройки: сохранение работает, отказ показывается тостом с причиной.
8. Консоль браузера чистая на всех четырёх экранах.

- [ ] **Шаг 10: проверьте, что чужого поиска и чужих данных не появилось**

Разметку переписывали целиком — ровно так в прошлый раз в `frontend/`
пережили три плана данные предыдущего автора.

Run: `pytest tests/test_no_foreign_search_terms.py tests/test_check_no_secrets.py -q`
Run: `python scripts/check_no_secrets.py $(git diff --name-only master...HEAD)`
Expected: чисто

- [ ] **Шаг 11: проверьте дискриминирующую силу**

1. Верните заголовок «Авторизация чатов (session_web)» → падает
   `test_the_telegram_login_card_is_named_after_what_it_does`.
2. Оставьте один `section-split` → падает `test_no_tg_hh_dividers_are_left`.
3. Верните вторую кнопку сохранения → падает
   `test_the_settings_have_one_save_button`.
4. Верните подпись безопасного режима, говорящую только про Telegram →
   падает `test_the_safe_mode_label_covers_both_workers`.

- [ ] **Шаг 12: коммит**

```bash
git add frontend/index.html frontend/app.js frontend/style.css \
        tests/test_frontend_screens.py tests/test_frontend_events.py \
        tests/test_frontend_config_save.py README.md
git commit -m "$(cat <<'MSG'
feat(ui): «Настройки» — четыре группы вместо тринадцати карточек

Убраны два места, которые лгали: карточка «Регион поиска» предлагала выбор
там, где с решения D8 константа, а «Авторизация чатов (session_web)» была
названа по файлу сессии, которого в приложении больше нет. Подпись
безопасного режима теперь говорит и про hh.ru — с решения D17 он слушает
тот же переключатель.

Claude-Session: https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB
MSG
)"
```

---

## Завершение работы

После задачи 13:

1. `make test` — зелёный.
2. **REQUIRED SUB-SKILL:** `superpowers:finishing-a-development-branch` —
   он сам предложит слияние, PR или сохранение ветки. Базовая ветка —
   `master`.
3. В описании PR последней строкой:
   `https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB`

### Что этот план осознанно НЕ делает

- Адаптивная вёрстка под узкие экраны и горячие клавиши — вне области
  (спецификация, §11).
- Смена визуального стиля: цвета, шрифты и карточки остаются, меняется
  структура и плотность.
- Политика хранения истории: `tg_found` и `worker_events` растут
  неограниченно — признанный долг.
- Одиннадцать оставшихся замечаний ревью подпроекта 1 (M-1, M-4 … M-13) и
  отсутствие CI.
