# План 3: воркеры и логика

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перевести мониторы в один процесс на asyncio, убрать PID-файлы и парсинг логов, исправить логические дефекты L1–L6 и L8–L13.

**Architecture:** `WorkerManager` держит asyncio-таски и их статус. TG-воркер работает внутри процесса API и использует тот же Telethon-клиент, что и просмотр переписки, — одна сессия на аккаунт. HH-воркер остаётся блокирующим Selenium, но заворачивается в `asyncio.to_thread`, а ручной вход превращается в машину состояний с эндпоинтами вместо `input()`. Вся бизнес-логика вынесена в чистые функции, которые тестируются без сети и браузера.

**Tech Stack:** Python 3.13.3, asyncio, FastAPI lifespan, Telethon, Selenium, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-09-08-hardening-and-rework-spec.md`

**Предпосылка:** планы 1 и 2 выполнены целиком.

## Global Constraints

- Python `>=3.11`, проверка на 3.13.3.
- Зависимости закреплены в `requirements.lock`; новых зависимостей этот план не добавляет.
- Запрещены `eval`, `exec`, `pickle`, `marshal`, `shell=True`, установка пакетов в рантайме.
- Ни один секрет не попадает в БД, в ответы API, в логи, в репозиторий.
- Сервер слушает только `127.0.0.1`.
- Новый код с аннотациями типов, тесты `pytest`, коммиты Conventional Commits.
- `make test` зелёный на каждом коммите.
- Тесты воркеров не ходят в сеть и не открывают браузер: внешние границы подменяются двойниками.

## Структура файлов

| Файл | Ответственность |
|------|-----------------|
| `job_monitor/workers/manager.py` | реестр воркеров, старт/стоп/статус asyncio-тасок |
| `job_monitor/workers/telegram.py` | правила отбора получателей и цикл TG-воркера |
| `job_monitor/workers/hh.py` | цикл HH-воркера и машина состояний входа |
| `job_monitor/workers/hh_steps.py` | исполнитель сценария Selenium по белому списку шагов |
| `job_monitor/telegram_client.py` | единственный Telethon-клиент приложения |
| `api/routes_state.py` | агрегированное состояние для дашборда |

---

### Задача 1: супервизор воркеров

Закрывает L3 (PID-файл, который никто не пишет) и L12 (устаревший `on_event`).

**Files:**
- Create: `job_monitor/workers/__init__.py`, `job_monitor/workers/manager.py`, `tests/test_worker_manager.py`
- Modify: `api/main.py:42-60` (lifespan вместо `on_event`)

**Interfaces:**
- Produces:
  - `WorkerState` — `stopped | starting | running | stopping | error`
  - `WorkerStatus` (`name: str`, `state: WorkerState`, `started_at: datetime | None`, `last_error: str | None`)
  - `WorkerManager.register(name: str, factory: Callable[[], Awaitable[None]]) -> None`
  - `await WorkerManager.start(name: str) -> WorkerStatus` — бросает `WorkerAlreadyRunning`
  - `await WorkerManager.stop(name: str, timeout: float = 10.0) -> WorkerStatus` — бросает `WorkerNotRunning`
  - `WorkerManager.status(name: str) -> WorkerStatus`, `WorkerManager.all() -> dict[str, WorkerStatus]`
  - `job_monitor.workers.manager.manager` — экземпляр приложения

- [ ] **Step 1: Написать падающий тест**

`tests/test_worker_manager.py`:

```python
import asyncio

import pytest

from job_monitor.workers.manager import (
    WorkerAlreadyRunning, WorkerManager, WorkerNotRunning, WorkerState,
)


async def wait_for(manager: WorkerManager, name: str, state: WorkerState) -> None:
    for _ in range(100):
        if manager.status(name).state is state:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"{name} не пришёл в {state}, сейчас {manager.status(name).state}")


async def test_start_moves_to_running():
    manager = WorkerManager()
    manager.register("idle", lambda: asyncio.sleep(3600))
    await manager.start("idle")
    await wait_for(manager, "idle", WorkerState.running)
    assert manager.status("idle").started_at is not None
    await manager.stop("idle")


async def test_stop_moves_to_stopped():
    manager = WorkerManager()
    manager.register("idle", lambda: asyncio.sleep(3600))
    await manager.start("idle")
    await wait_for(manager, "idle", WorkerState.running)
    await manager.stop("idle")
    assert manager.status("idle").state is WorkerState.stopped


async def test_double_start_is_rejected():
    manager = WorkerManager()
    manager.register("idle", lambda: asyncio.sleep(3600))
    await manager.start("idle")
    with pytest.raises(WorkerAlreadyRunning):
        await manager.start("idle")
    await manager.stop("idle")


async def test_stop_when_not_running_is_rejected():
    manager = WorkerManager()
    manager.register("idle", lambda: asyncio.sleep(3600))
    with pytest.raises(WorkerNotRunning):
        await manager.stop("idle")


async def test_crash_is_recorded():
    async def boom() -> None:
        raise RuntimeError("сломалось")

    manager = WorkerManager()
    manager.register("boom", boom)
    await manager.start("boom")
    await wait_for(manager, "boom", WorkerState.error)
    assert "сломалось" in manager.status("boom").last_error


async def test_status_of_unknown_worker_is_stopped():
    assert WorkerManager().status("nope").state is WorkerState.stopped
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_worker_manager.py -v`
Expected: FAIL — нет модуля `job_monitor.workers.manager`.

- [ ] **Step 3: Реализовать `job_monitor/workers/manager.py`**

```python
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

log = logging.getLogger(__name__)

WorkerFactory = Callable[[], Awaitable[None]]


class WorkerState(str, Enum):
    stopped = "stopped"
    starting = "starting"
    running = "running"
    stopping = "stopping"
    error = "error"


class WorkerAlreadyRunning(RuntimeError):
    pass


class WorkerNotRunning(RuntimeError):
    pass


@dataclass
class WorkerStatus:
    name: str
    state: WorkerState = WorkerState.stopped
    started_at: datetime | None = None
    last_error: str | None = None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "started_at": self.started_at.isoformat(timespec="seconds") if self.started_at else None,
            "last_error": self.last_error,
            "running": self.state in (WorkerState.starting, WorkerState.running),
        }


@dataclass
class WorkerManager:
    _factories: dict[str, WorkerFactory] = field(default_factory=dict)
    _tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    _statuses: dict[str, WorkerStatus] = field(default_factory=dict)

    def register(self, name: str, factory: WorkerFactory) -> None:
        self._factories[name] = factory
        self._statuses.setdefault(name, WorkerStatus(name=name))

    def status(self, name: str) -> WorkerStatus:
        return self._statuses.get(name, WorkerStatus(name=name))

    def all(self) -> dict[str, WorkerStatus]:
        return dict(self._statuses)

    async def start(self, name: str) -> WorkerStatus:
        if name not in self._factories:
            raise KeyError(f"воркер {name} не зарегистрирован")
        task = self._tasks.get(name)
        if task is not None and not task.done():
            raise WorkerAlreadyRunning(name)
        status = WorkerStatus(name=name, state=WorkerState.starting, started_at=datetime.now())
        self._statuses[name] = status
        self._tasks[name] = asyncio.create_task(self._supervise(name), name=f"worker:{name}")
        return status

    async def stop(self, name: str, timeout: float = 10.0) -> WorkerStatus:
        task = self._tasks.get(name)
        if task is None or task.done():
            raise WorkerNotRunning(name)
        self._statuses[name].state = WorkerState.stopping
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        self._tasks.pop(name, None)
        self._statuses[name] = WorkerStatus(name=name, state=WorkerState.stopped)
        return self._statuses[name]

    async def stop_all(self) -> None:
        for name in list(self._tasks):
            try:
                await self.stop(name)
            except WorkerNotRunning:
                continue

    async def _supervise(self, name: str) -> None:
        status = self._statuses[name]
        status.state = WorkerState.running
        try:
            await self._factories[name]()
        except asyncio.CancelledError:
            status.state = WorkerState.stopped
            raise
        except Exception as error:  # noqa: BLE001 — воркер не должен ронять приложение
            log.exception("воркер %s упал", name)
            status.state = WorkerState.error
            status.last_error = f"{type(error).__name__}: {error}"
        else:
            status.state = WorkerState.stopped


manager = WorkerManager()
```

- [ ] **Step 4: Заменить `on_event` на lifespan в `api/main.py`**

```python
from contextlib import asynccontextmanager

from job_monitor.workers.manager import manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    register_workers()                      # добавится в задачах 2 и 3
    settings = load_settings(get_connection())
    if settings.tg_autostart:
        await manager.start("tg")
    if settings.hh_autostart:
        await manager.start("hh")
    yield
    await manager.stop_all()

app = FastAPI(title="Job Monitor API", version="2.1", lifespan=lifespan)
```

Блок `@app.on_event("startup")` (строки 42-60) удалить целиком.

- [ ] **Step 5: Запустить тесты и закоммитить**

Run: `make test`
Expected: PASS, 37 passed.

```bash
git add job_monitor/workers/ api/main.py tests/test_worker_manager.py
git commit -m "feat: asyncio worker supervisor replaces PID files"
```

---

### Задача 2: TG-воркер внутри процесса

Закрывает L1 (перевёрнутое условие дедупликации), L8 (две сессии на один аккаунт), L9 (сброс счётчиков только при трафике), L10 (отклик в сам канал-источник), L11 (протухший клиент).

**Files:**
- Create: `job_monitor/telegram_client.py`, `job_monitor/workers/telegram.py`, `tests/test_telegram_worker.py`
- Modify: `api/auth_routes.py:12-23`, `api/tg_routes.py` (start/stop через `manager`)

**Interfaces:**
- Consumes: `TgRepo`, `AppSettings`, `load_secrets`, `WorkerManager`
- Produces:
  - `job_monitor.telegram_client.get_client() -> TelegramClient` — единственный клиент на сессии `paths.tg_session()`
  - `job_monitor.telegram_client.reset_client() -> None` — вызывается при смене ключей
  - `job_monitor.workers.telegram.is_eligible(username, settings, own_username) -> bool`
  - `job_monitor.workers.telegram.extract_usernames(text: str) -> list[str]`
  - `job_monitor.workers.telegram.post_matches(post, settings) -> bool`
  - `job_monitor.workers.telegram.process_post(post, settings, repo, sender, now) -> list[str]`
  - `job_monitor.workers.telegram.IncomingPost` (`channel: str`, `text: str`)
  - `job_monitor.workers.telegram.run_worker() -> Awaitable[None]`

- [ ] **Step 1: Написать падающий тест**

`tests/test_telegram_worker.py`:

```python
from datetime import date, datetime

import pytest

from job_monitor.db import connection
from job_monitor.db.repositories import TgRepo
from job_monitor.settings import AppSettings
from job_monitor.workers.telegram import (
    IncomingPost, extract_usernames, is_eligible, post_matches, process_post,
)

NOW = datetime(2026, 9, 8, 12, 0, 0)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    connection.reset_connection()
    yield connection.connect()
    connection.reset_connection()


@pytest.fixture
def settings():
    return AppSettings(
        channels=["itvacancykz"], keywords=["qa"], exclude=["senior"],
        template="привет", safe_mode=False, max_per_day=2, delay_min=1, delay_max=1,
    )


class Sender:
    def __init__(self):
        self.sent: list[str] = []

    async def __call__(self, username: str) -> None:
        self.sent.append(username)


def test_extract_usernames():
    # @hr короче четырёх символов — под правила Telegram не подходит и отбрасывается
    assert extract_usernames("пишите @hr_anna или @hr.bob") == ["@hr_anna"]
    assert extract_usernames("@hr_anna и снова @hr_anna") == ["@hr_anna"]


def test_post_matches_only_on_keyword_without_exclusion(settings):
    assert post_matches(IncomingPost("itvacancykz", "нужен QA"), settings) is True
    assert post_matches(IncomingPost("itvacancykz", "нужен Senior QA"), settings) is False
    assert post_matches(IncomingPost("itvacancykz", "нужен повар"), settings) is False
    assert post_matches(IncomingPost("other", "нужен QA"), settings) is False


def test_skips_bots_and_source_channels(settings):
    assert is_eligible("@hr_anna", settings, own_username="@me") is True
    assert is_eligible("@some_bot", settings, own_username="@me") is False
    assert is_eligible("@itvacancykz", settings, own_username="@me") is False   # L10
    assert is_eligible("@me", settings, own_username="@me") is False


async def test_sends_once_per_contact(conn, settings):
    repo, sender = TgRepo(conn), Sender()
    post = IncomingPost(channel="itvacancykz", text="Нужен QA, пишите @hr_anna")
    assert await process_post(post, settings, repo, sender, NOW) == ["@hr_anna"]
    assert await process_post(post, settings, repo, sender, NOW) == []          # L1
    assert sender.sent == ["@hr_anna"]
    assert repo.contacts_total() == 1


async def test_respects_daily_limit_from_database(conn, settings):
    repo, sender = TgRepo(conn), Sender()
    repo.record_send("@old_one", None, None, NOW)
    repo.record_send("@old_two", None, None, NOW)
    post = IncomingPost(channel="itvacancykz", text="QA нужен, @hr_anna")
    assert await process_post(post, settings, repo, sender, NOW) == []
    assert repo.sent_on(date(2026, 9, 8)) == 2


async def test_limit_resets_on_a_new_day_without_any_reset_job(conn, settings):
    repo, sender = TgRepo(conn), Sender()
    repo.record_send("@old_one", None, None, datetime(2026, 9, 7, 23, 0))
    repo.record_send("@old_two", None, None, datetime(2026, 9, 7, 23, 30))
    post = IncomingPost(channel="itvacancykz", text="QA нужен, @hr_anna")
    assert await process_post(post, settings, repo, sender, NOW) == ["@hr_anna"]  # L9


async def test_exclude_word_blocks_post(conn, settings):
    repo, sender = TgRepo(conn), Sender()
    post = IncomingPost(channel="itvacancykz", text="Senior QA нужен, @hr_anna")
    assert await process_post(post, settings, repo, sender, NOW) == []


async def test_safe_mode_records_nothing(conn, settings):
    settings = settings.model_copy(update={"safe_mode": True})
    repo, sender = TgRepo(conn), Sender()
    post = IncomingPost(channel="itvacancykz", text="QA нужен, @hr_anna")
    assert await process_post(post, settings, repo, sender, NOW) == []
    assert sender.sent == []
    assert repo.contacts_total() == 0


async def test_post_from_foreign_channel_is_ignored(conn, settings):
    repo, sender = TgRepo(conn), Sender()
    post = IncomingPost(channel="random_channel", text="QA нужен, @hr_anna")
    assert await process_post(post, settings, repo, sender, NOW) == []
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_telegram_worker.py -v`
Expected: FAIL — нет модуля `job_monitor.workers.telegram`.

- [ ] **Step 3: Реализовать `job_monitor/workers/telegram.py`**

```python
from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from job_monitor.db.repositories import TgRepo
from job_monitor.settings import AppSettings

log = logging.getLogger(__name__)
USERNAME_RE = re.compile(r"@[A-Za-z0-9_]{4,32}")
Sender = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class IncomingPost:
    channel: str
    text: str


def extract_usernames(text: str) -> list[str]:
    seen: list[str] = []
    for match in USERNAME_RE.findall(text):
        if match not in seen:
            seen.append(match)
    return seen


def is_eligible(username: str, settings: AppSettings, own_username: str | None) -> bool:
    handle = username.lstrip("@").lower()
    if handle.endswith("bot"):
        return False
    if own_username and handle == own_username.lstrip("@").lower():
        return False
    if handle in {channel.lstrip("@").lower() for channel in settings.channels}:
        return False
    return True


def post_matches(post: IncomingPost, settings: AppSettings) -> bool:
    """Пост из нужного канала, с ключевым словом и без стоп-слова."""
    channels = {channel.lstrip("@").lower() for channel in settings.channels}
    if post.channel.lstrip("@").lower() not in channels:
        return False
    text = post.text.lower()
    if not any(keyword.lower() in text for keyword in settings.keywords):
        return False
    return not any(bad.lower() in text for bad in settings.exclude)


async def process_post(
    post: IncomingPost,
    settings: AppSettings,
    repo: TgRepo,
    sender: Sender,
    now: datetime,
    own_username: str | None = None,
) -> list[str]:
    """Обрабатывает один пост. Возвращает список username, которым отправили."""
    if not post_matches(post, settings):
        return []
    if settings.safe_mode:
        for username in extract_usernames(post.text):
            log.info("[SAFE MODE] найден контакт: %s", username)
        return []
    if not settings.template:
        log.warning("шаблон сообщения пуст — отправка пропущена")
        return []

    sent: list[str] = []
    for username in extract_usernames(post.text):
        # Лимит перечитывается из БД на каждой итерации: смена суток
        # обрабатывается сама собой, отдельная задача сброса не нужна.
        if repo.sent_on(now.date()) >= settings.max_per_day:
            log.warning("дневной лимит %s достигнут", settings.max_per_day)
            break
        if not is_eligible(username, settings, own_username):
            continue
        if repo.was_sent(username):
            continue
        await sender(username)
        repo.record_send(username, post.channel, post.text[:80].strip(), now)
        sent.append(username)
        await asyncio.sleep(random.randint(settings.delay_min, settings.delay_max))
    return sent
```

`USERNAME_RE` ужесточён до правил Telegram (4–32 символа, только латиница, цифры и `_`): прежний `@[\w\d_]+` матчил кириллицу и однобуквенные хвосты.

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/python -m pytest tests/test_telegram_worker.py -v`
Expected: PASS, 9 passed.

- [ ] **Step 5: Реализовать общий Telethon-клиент**

`job_monitor/telegram_client.py`:

```python
from __future__ import annotations

from telethon import TelegramClient

from job_monitor import paths
from job_monitor.settings import Secrets, load_secrets

_client: TelegramClient | None = None
_credentials: Secrets | None = None


def get_client() -> TelegramClient:
    global _client, _credentials
    secrets = load_secrets()
    if not secrets.is_complete:
        raise RuntimeError("TG_API_ID и TG_API_HASH не заданы")
    if _client is None or _credentials != secrets:      # L11: ключи сменились — клиент новый
        _client = TelegramClient(str(paths.tg_session()), secrets.api_id, secrets.api_hash)
        _credentials = secrets
    return _client


def reset_client() -> None:
    global _client, _credentials
    _client = None
    _credentials = None
```

Одна сессия `telegram.session` и для воркера, и для просмотра переписки — файла `telegram_web.session` больше нет (L8). В `api/auth_routes.py` заменить `get_web_client` на `get_client`, удалить локальный синглтон (строки 12-23). В `api/config_routes.py::update_config` после записи новых ключей вызвать `reset_client()`.

- [ ] **Step 6: Добавить цикл воркера и подключить к менеджеру**

В `job_monitor/workers/telegram.py`:

```python
async def run_worker() -> None:
    from telethon import events

    from job_monitor.db.connection import get_connection
    from job_monitor.settings import load_settings
    from job_monitor.telegram_client import get_client

    client = get_client()
    await client.start()
    me = await client.get_me()
    own_username = getattr(me, "username", None)
    conn = get_connection()
    repo = TgRepo(conn)

    async def sender(username: str) -> None:
        settings = load_settings(conn)
        await client.send_message(username, settings.template)
        if settings.file_path:
            await client.send_file(username, settings.file_path)

    @client.on(events.NewMessage())
    async def handler(event) -> None:  # адаптер Telethon → чистая логика
        chat = await event.get_chat()
        channel = getattr(chat, "username", None)
        if not channel or not event.message.message:
            return
        post = IncomingPost(channel=channel, text=event.message.message)
        settings = load_settings(conn)
        now = datetime.now()
        if post_matches(post, settings):
            # Метрика «вакансий найдено» берётся отсюда, а не из текста логов (L2).
            EventsRepo(conn).add("tg", "vacancy", post.text[:80].strip(), now)
        for username in await process_post(post, settings, repo, sender, now, own_username):
            EventsRepo(conn).add("tg", "sent", username, now)

    log.info("TG-воркер запущен")
    await client.run_until_disconnected()
```

В `api/main.py::register_workers`: `manager.register("tg", run_worker)`.

- [ ] **Step 7: Переписать `api/tg_routes.py` на менеджер**

`POST /api/tg/start` → `await manager.start("tg")`, `WorkerAlreadyRunning` → HTTP 400. `POST /api/tg/stop` → `await manager.stop("tg")`, `WorkerNotRunning` → HTTP 400. Удалить `subprocess`, `signal`, `PID_FILE`, `is_running`, `monitor_process`.

- [ ] **Step 8: Прогнать всё и закоммитить**

Run: `make test`
Expected: PASS, 46 passed.

```bash
git add job_monitor/ api/ tests/test_telegram_worker.py
git commit -m "feat: run telegram monitor in-process with one shared session"
```

---

### Задача 3: HH-воркер и вход без блокировки

Закрывает L4 (`input()` в подпроцессе без stdin) и L6 (мёртвый код с доменом cookie).

**Files:**
- Create: `job_monitor/workers/hh.py`, `tests/test_hh_login.py`
- Modify: `api/hh_routes.py` (целиком), `frontend/index.html` (кнопки входа), `frontend/app.js`

**Interfaces:**
- Consumes: `HhRepo`, `AppSettings`, `WorkerManager`, `job_monitor.paths.hh_cookies`
- Produces:
  - `HhLoginState` — `logged_out | browser_open | logged_in`
  - `HhLogin.start() -> HhLoginState` — открывает окно Chrome на странице входа
  - `HhLogin.confirm() -> HhLoginState` — проверяет вход, сохраняет cookies, закрывает окно
  - `HhLogin.state -> HhLoginState`
  - `job_monitor.workers.hh.run_worker() -> Awaitable[None]`
  - `save_cookies(driver, target) -> None`, `load_cookies(driver, target) -> bool`

- [ ] **Step 1: Написать падающий тест**

`tests/test_hh_login.py`:

```python
import json

import pytest

from job_monitor.workers.hh import HhLogin, HhLoginState, load_cookies, save_cookies


class FakeDriver:
    def __init__(self, logged_in: bool = False):
        self.logged_in = logged_in
        self.visited: list[str] = []
        self.added: list[dict] = []
        self.quit_called = False

    def get(self, url: str) -> None:
        self.visited.append(url)

    def get_cookies(self) -> list[dict]:
        return [{"name": "sid", "value": "1", "domain": ".hh.ru", "path": "/",
                 "secure": True, "httpOnly": True, "sameSite": "None", "expiry": 1}]

    def add_cookie(self, cookie: dict) -> None:
        self.added.append(cookie)

    def refresh(self) -> None:
        pass

    def quit(self) -> None:
        self.quit_called = True


def test_save_cookies_writes_file(tmp_path):
    target = tmp_path / "hh_cookies.json"
    save_cookies(FakeDriver(), target)
    assert json.loads(target.read_text(encoding="utf-8"))[0]["name"] == "sid"


def test_load_cookies_drops_fields_selenium_rejects(tmp_path):
    target = tmp_path / "hh_cookies.json"
    save_cookies(FakeDriver(), target)
    driver = FakeDriver()
    assert load_cookies(driver, target) is True
    assert set(driver.added[0]) == {"name", "value", "domain", "path", "secure", "httpOnly"}


def test_load_cookies_without_file(tmp_path):
    assert load_cookies(FakeDriver(), tmp_path / "nope.json") is False


def test_login_flow_never_blocks(tmp_path):
    driver = FakeDriver(logged_in=True)
    login = HhLogin(driver_factory=lambda: driver, cookies_path=tmp_path / "c.json",
                    is_logged_in=lambda _driver: _driver.logged_in)
    assert login.state is HhLoginState.logged_out
    assert login.start() is HhLoginState.browser_open
    assert "hh.ru/account/login" in driver.visited[-1]
    assert login.confirm() is HhLoginState.logged_in
    assert (tmp_path / "c.json").exists()
    assert driver.quit_called is True


def test_confirm_without_actual_login_stays_open(tmp_path):
    driver = FakeDriver(logged_in=False)
    login = HhLogin(driver_factory=lambda: driver, cookies_path=tmp_path / "c.json",
                    is_logged_in=lambda _driver: _driver.logged_in)
    login.start()
    assert login.confirm() is HhLoginState.browser_open
    assert not (tmp_path / "c.json").exists()


def test_confirm_before_start_is_an_error(tmp_path):
    login = HhLogin(driver_factory=lambda: FakeDriver(), cookies_path=tmp_path / "c.json",
                    is_logged_in=lambda _driver: True)
    with pytest.raises(RuntimeError):
        login.confirm()
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_hh_login.py -v`
Expected: FAIL — нет модуля `job_monitor.workers.hh`.

- [ ] **Step 3: Реализовать вход в `job_monitor/workers/hh.py`**

```python
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
LOGIN_URL = "https://hh.ru/account/login"
HOME_URL = "https://hh.ru"
SELENIUM_COOKIE_FIELDS = ("name", "value", "domain", "path", "secure", "httpOnly")


class HhLoginState(str, Enum):
    logged_out = "logged_out"
    browser_open = "browser_open"
    logged_in = "logged_in"


def save_cookies(driver: Any, target: Path) -> None:
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.write_text(json.dumps(driver.get_cookies()), encoding="utf-8")
    target.chmod(0o600)


def load_cookies(driver: Any, target: Path) -> bool:
    if not target.exists():
        return False
    driver.get(HOME_URL)
    for cookie in json.loads(target.read_text(encoding="utf-8")):
        trimmed = {key: value for key, value in cookie.items() if key in SELENIUM_COOKIE_FIELDS}
        try:
            driver.add_cookie(trimmed)
        except Exception as error:  # noqa: BLE001 — один плохой cookie не должен ронять вход
            log.debug("cookie %s отклонён: %s", trimmed.get("name"), error)
    driver.refresh()
    return True


class HhLogin:
    """Ручной вход в hh.ru без блокировки процесса: два вызова вместо input()."""

    def __init__(
        self,
        driver_factory: Callable[[], Any],
        cookies_path: Path,
        is_logged_in: Callable[[Any], bool],
    ) -> None:
        self._driver_factory = driver_factory
        self._cookies_path = cookies_path
        self._is_logged_in = is_logged_in
        self._driver: Any | None = None
        self.state = HhLoginState.logged_out

    def start(self) -> HhLoginState:
        if self._driver is None:
            self._driver = self._driver_factory()
        self._driver.get(LOGIN_URL)
        self.state = HhLoginState.browser_open
        return self.state

    def confirm(self) -> HhLoginState:
        if self._driver is None:
            raise RuntimeError("сначала вызови start()")
        if not self._is_logged_in(self._driver):
            return self.state
        save_cookies(self._driver, self._cookies_path)
        self._driver.quit()
        self._driver = None
        self.state = HhLoginState.logged_in
        return self.state
```

Мёртвая ветка `if clean["domain"].startswith("."): clean["domain"] = clean["domain"]` не переносится (L6).

- [ ] **Step 4: Перенести цикл HH в воркер**

Функции `setup_driver`, `build_search_url`, `get_vacancies_from_page`, `apply_to_vacancy`, `is_logged_in` перенести из `hh_monitor.py` в `job_monitor/workers/hh.py` без изменения логики, заменив только источники данных: настройки — `load_settings(conn)`, история — `HhRepo` вместо `hh_sent.json`.

Одна правка обязательна: `get_vacancies_from_page` возвращала словарь с ключом `id`, а `HhRepo` ждёт `vacancy_id` — переименуй ключ в самой функции, иначе `repo.exists()` будет молча получать `None` и приложение начнёт откликаться на одни и те же вакансии по кругу. `build_search_url` принимает `AppSettings`, а не `dict`, — обращения `cfg.get("hh_search_period", 1)` заменяются на `settings.hh_search_period`.

Selenium блокирующий, поэтому цикл живёт в отдельном потоке. Поток нельзя отменить как asyncio-таску — им управляет `threading.Event`, который проверяется после каждой вакансии, а сон разбит на секундные интервалы. Без этого `manager.stop("hh")` ждал бы конца `hh_check_interval`, то есть до 30 минут:

```python
import asyncio
import threading

_stop_event = threading.Event()
login = HhLogin(
    driver_factory=lambda: setup_driver(headless=False),
    cookies_path=paths.hh_cookies(),
    is_logged_in=is_logged_in,
)


def _interruptible_sleep(seconds: float) -> bool:
    """Спит по секунде. Возвращает False, если попросили остановиться."""
    for _ in range(int(seconds)):
        if _stop_event.is_set():
            return False
        time.sleep(1)
    return True


def _blocking_loop(conn: sqlite3.Connection) -> None:
    repo = HhRepo(conn)
    events = EventsRepo(conn)
    driver = setup_driver(headless=False)
    try:
        load_cookies(driver, paths.hh_cookies())
        if not is_logged_in(driver):
            events.add("hh", "login_required", "нужен вход через настройки", datetime.now())
            return
        while not _stop_event.is_set():
            settings = load_settings(conn)
            if repo.applied_on(date.today()) >= settings.hh_max_per_day:
                if not _interruptible_sleep(600):
                    return
                continue
            for keyword in settings.hh_keywords:
                for area_id in settings.hh_area_ids:
                    if _stop_event.is_set():
                        return
                    driver.get(build_search_url(keyword, settings, area_id))
                    for vacancy in get_vacancies_from_page(driver, settings):
                        if _stop_event.is_set():
                            return
                        if repo.exists(vacancy["vacancy_id"]):
                            continue
                        _process_one(driver, vacancy, settings, repo, events)
                        if not _interruptible_sleep(
                            random.randint(settings.hh_delay_min, settings.hh_delay_max)
                        ):
                            return
            if not _interruptible_sleep(settings.hh_check_interval):
                return
    finally:
        driver.quit()


async def run_worker() -> None:
    from job_monitor.db.connection import get_connection

    _stop_event.clear()
    try:
        await asyncio.to_thread(_blocking_loop, get_connection())
    except asyncio.CancelledError:
        _stop_event.set()      # поток увидит флаг и выйдет сам
        raise
```

`_process_one` — вынесенная из старого цикла запись результата: вызывает `apply_to_vacancy`, затем `repo.upsert({**vacancy, "status": "отклик отправлен" | "пропущено", "applied_at": ...})` и `events.add("hh", "applied" | "skipped", vacancy["title"], datetime.now())`.

- [ ] **Step 5: Переписать `api/hh_routes.py`**

`POST /api/hh/start` и `/stop` — через `manager`, как в задаче 2. Добавить:

```python
@router.post("/login/start")
async def hh_login_start() -> dict:
    return {"state": (await asyncio.to_thread(login.start)).value}


@router.post("/login/confirm")
async def hh_login_confirm() -> dict:
    return {"state": (await asyncio.to_thread(login.confirm)).value}


@router.get("/login/status")
async def hh_login_status() -> dict:
    return {"state": login.state.value}
```

Удалить `subprocess`, `HH_PID_FILE`, `hh_is_running`, `load_hh_sent`; вакансии отдавать через `HhRepo(get_connection()).recent(50)`.

- [ ] **Step 6: Добавить кнопки входа во фронтенд**

В карточку «HH.ru» на странице настроек добавить блок с двумя кнопками — «Открыть вход в hh.ru» (`POST /api/hh/login/start`) и «Я вошёл, сохранить сессию» (`POST /api/hh/login/confirm`) — и строкой статуса из `GET /api/hh/login/status`. Обработчики вешать через `addEventListener`, узлы собирать через `el()` (инвариант «нет `innerHTML`» держится тестом из плана 1).

- [ ] **Step 7: Прогнать тесты и проверить вживую**

Run: `make test`
Expected: PASS, 52 passed.

```bash
make run
```
Нажать «Открыть вход в hh.ru» — открывается Chrome на странице входа, интерфейс при этом отзывчив. Войти, нажать «Я вошёл» — статус меняется на `logged_in`, окно закрывается, `~/.job-monitor/hh_cookies.json` создан с правами `0600`.

- [ ] **Step 8: Закоммитить**

```bash
git add job_monitor/workers/hh.py api/hh_routes.py frontend/ tests/test_hh_login.py
git commit -m "feat: non-blocking hh.ru login flow, hh worker inside the app process"
```

---

### Задача 4: исполнитель сценария Selenium

Закрывает L5: UI сохраняет `hh_selenium_steps` с самого начала, но их никто никогда не выполнял.

**Files:**
- Create: `job_monitor/workers/hh_steps.py`, `tests/test_hh_steps.py`
- Modify: `job_monitor/workers/hh.py` (вызов после клика по «Откликнуться»)

**Interfaces:**
- Produces:
  - `Step` (`type: str`, `by: str`, `selector: str`, `value: str`, `seconds: float`)
  - `parse_steps(raw: list[dict]) -> list[Step]` — бросает `ValueError` на неизвестном типе шага
  - `run_steps(driver, steps: list[Step], wait_factory) -> None`

- [ ] **Step 1: Написать падающий тест**

`tests/test_hh_steps.py`:

```python
import pytest

from job_monitor.workers.hh_steps import parse_steps, run_steps


class FakeElement:
    def __init__(self):
        self.clicked = False
        self.typed = ""

    def click(self):
        self.clicked = True

    def clear(self):
        self.typed = ""

    def send_keys(self, text):
        self.typed += text


class FakeDriver:
    def __init__(self):
        self.element = FakeElement()
        self.scripts: list[str] = []
        self.queried: list[tuple[str, str]] = []

    def find_element(self, by, selector):
        self.queried.append((by, selector))
        return self.element

    def execute_script(self, script, *args):
        self.scripts.append(script)


def no_wait(_driver, _timeout=None):
    class _Wait:
        def until(self, condition):
            return condition(_driver)
    return _Wait()


def test_parse_rejects_unknown_step_type():
    with pytest.raises(ValueError, match="неизвестный тип шага"):
        parse_steps([{"type": "execute_script", "value": "alert(1)"}])


def test_parse_infers_locator_kind():
    steps = parse_steps([
        {"type": "click", "selector": "//button[@id='x']"},
        {"type": "click", "selector": ".btn-primary"},
    ])
    assert steps[0].by == "xpath"
    assert steps[1].by == "css selector"


def test_parse_defaults_wait_seconds():
    assert parse_steps([{"type": "wait"}])[0].seconds == 2.0


def test_click_step(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    driver = FakeDriver()
    run_steps(driver, parse_steps([{"type": "click", "selector": ".apply"}]), no_wait)
    assert driver.element.clicked is True


def test_input_step(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    driver = FakeDriver()
    run_steps(driver, parse_steps([{"type": "input", "selector": "#letter", "value": "привет"}]), no_wait)
    assert driver.element.typed == "привет"


def test_scroll_step_uses_script_without_user_input(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    driver = FakeDriver()
    run_steps(driver, parse_steps([{"type": "scroll", "value": "500"}]), no_wait)
    assert driver.scripts == ["window.scrollBy(0, arguments[0]);"]
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_hh_steps.py -v`
Expected: FAIL — нет модуля `job_monitor.workers.hh_steps`.

- [ ] **Step 3: Реализовать `job_monitor/workers/hh_steps.py`**

```python
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

CSS = "css selector"
XPATH = "xpath"
ALLOWED_TYPES = ("click", "input", "wait", "wait_element", "select", "scroll")


@dataclass(frozen=True)
class Step:
    type: str
    by: str = CSS
    selector: str = ""
    value: str = ""
    seconds: float = 0.0


def _infer_by(selector: str) -> str:
    return XPATH if selector.startswith(("/", "(", "./")) else CSS


def parse_steps(raw: list[dict]) -> list[Step]:
    steps: list[Step] = []
    for item in raw:
        kind = str(item.get("type", ""))
        if kind not in ALLOWED_TYPES:
            raise ValueError(f"неизвестный тип шага: {kind!r}")
        selector = str(item.get("selector") or "")
        seconds = item.get("seconds", item.get("value") if kind == "wait" else 0)
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            seconds = 0.0
        if kind == "wait" and seconds <= 0:
            seconds = 2.0
        steps.append(Step(
            type=kind,
            by=str(item.get("by") or _infer_by(selector)),
            selector=selector,
            value=str(item.get("value") or ""),
            seconds=seconds,
        ))
    return steps


def run_steps(driver: Any, steps: list[Step], wait_factory: Callable[..., Any]) -> None:
    """Исполняет сценарий. Диспетчеризация по белому списку — никакого eval."""
    for step in steps:
        if step.type == "wait":
            time.sleep(step.seconds)
            continue
        if step.type == "scroll":
            amount = int(float(step.value or 300))
            driver.execute_script("window.scrollBy(0, arguments[0]);", amount)
            time.sleep(0.3)
            continue
        if step.type == "wait_element":
            wait_factory(driver, 10).until(
                lambda d: d.find_element(step.by, step.selector)
            )
            continue

        element = driver.find_element(step.by, step.selector)
        if step.type == "click":
            element.click()
        elif step.type == "input":
            element.clear()
            element.send_keys(step.value)
        elif step.type == "select":
            from selenium.webdriver.support.ui import Select

            Select(element).select_by_visible_text(step.value)
        time.sleep(0.5)
```

Значения шагов никогда не попадают в `execute_script` как код: прокрутка передаёт число через `arguments[0]`.

- [ ] **Step 4: Вызвать сценарий в HH-воркере**

В `apply_to_vacancy`, сразу после клика по кнопке отклика и до поиска кнопки отправки:

```python
    steps = parse_steps(settings.hh_selenium_steps)
    if steps:
        run_steps(driver, steps, WebDriverWait)
```

Ошибку `ValueError` от `parse_steps` ловить на уровне цикла воркера и писать в `worker_events` — сценарий с опечаткой не должен ронять монитор.

- [ ] **Step 5: Прогнать тесты и закоммитить**

Run: `make test`
Expected: PASS, 58 passed.

```bash
git add job_monitor/workers/hh_steps.py job_monitor/workers/hh.py tests/test_hh_steps.py
git commit -m "feat: execute saved selenium scenarios from a whitelist"
```

---

### Задача 5: метрики из БД

Закрывает L2: счётчики перестают зависеть от текста логов и от ротации.

**Files:**
- Create: `api/routes_state.py`, `tests/test_state_api.py`
- Modify: `api/main.py` (подключить роутер), `api/tg_routes.py` и `api/hh_routes.py` (удалить подсчёт по логам), `job_monitor/db/repositories.py` (добавить `count_on`)

**Interfaces:**
- Consumes: `TgRepo`, `HhRepo`, `EventsRepo`, `WorkerManager`
- Produces: `EventsRepo.count_on(worker: str, kind: str, day: date) -> int`

```python
    def count_on(self, worker: str, kind: str, day: date) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM worker_events"
            " WHERE worker = ? AND kind = ? AND at LIKE ?",
            (worker, kind, f"{day.isoformat()}%"),
        ).fetchone()
        return int(row["n"])
```
- Produces: `GET /api/state` →

```json
{
  "tg": {"running": false, "state": "stopped", "last_error": null,
         "sent_today": 0, "sent_total": 0, "found_today": 0, "max_per_day": 25,
         "safe_mode": true, "channels_count": 6, "api_hash_set": false},
  "hh": {"running": false, "state": "stopped", "last_error": null,
         "sent_today": 0, "found_today": 0, "total_sent": 0, "max_per_day": 20,
         "login_state": "logged_out"},
  "recent": [{"at": "2026-09-08T12:00:00", "worker": "tg", "kind": "sent", "detail": "@hr_anna"}]
}
```

- [ ] **Step 1: Написать падающий тест**

`tests/test_state_api.py`:

```python
from datetime import datetime

import pytest

from job_monitor.db import connection
from job_monitor.db.repositories import EventsRepo, HhRepo, TgRepo
from job_monitor.security import APP_TOKEN, TOKEN_HEADER

AUTH = {TOKEN_HEADER: APP_TOKEN}


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    connection.reset_connection()
    yield
    connection.reset_connection()


def test_counts_come_from_database(client):
    conn = connection.get_connection()
    now = datetime.now()
    TgRepo(conn).record_send("@hr_anna", "itvacancykz", "QA", now)
    HhRepo(conn).upsert({"vacancy_id": "1", "title": "QA", "status": "отклик отправлен",
                         "found_at": now.isoformat(), "applied_at": now.isoformat()})
    body = client.get("/api/state", headers=AUTH).json()
    assert body["tg"]["sent_today"] == 1
    assert body["tg"]["sent_total"] == 1
    assert body["hh"]["sent_today"] == 1


def test_worker_state_is_reported(client):
    body = client.get("/api/state", headers=AUTH).json()
    assert body["tg"]["running"] is False
    assert body["tg"]["state"] == "stopped"


def test_recent_events_are_returned(client):
    EventsRepo(connection.get_connection()).add("tg", "sent", "@hr_anna", datetime.now())
    body = client.get("/api/state", headers=AUTH).json()
    assert body["recent"][0]["kind"] == "sent"


def test_state_requires_token(client):
    assert client.get("/api/state").status_code == 403


def test_log_parsing_helpers_are_gone():
    from api import tg_routes

    assert not hasattr(tg_routes, "get_found_today")
    assert not hasattr(tg_routes, "get_sent_today")
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_state_api.py -v`
Expected: FAIL — нет роутера `/api/state`, старые функции ещё на месте.

- [ ] **Step 3: Реализовать `api/routes_state.py`**

```python
from datetime import date

from fastapi import APIRouter

from job_monitor.db.connection import get_connection
from job_monitor.db.repositories import EventsRepo, HhRepo, TgRepo
from job_monitor.settings import load_secrets, load_settings
from job_monitor.workers.hh import login
from job_monitor.workers.manager import manager

router = APIRouter(prefix="/api", tags=["state"])


@router.get("/state")
async def get_state() -> dict:
    conn = get_connection()
    today = date.today()
    settings = load_settings(conn)
    tg_repo, hh_repo, events = TgRepo(conn), HhRepo(conn), EventsRepo(conn)

    recent = events.recent("tg", 8) + events.recent("hh", 8)
    recent.sort(key=lambda event: event["at"], reverse=True)

    return {
        "tg": {
            **manager.status("tg").as_dict(),
            "sent_today": tg_repo.sent_on(today),
            "sent_total": tg_repo.contacts_total(),
            "found_today": events.count_on("tg", "vacancy", today),
            "max_per_day": settings.max_per_day,
            "safe_mode": settings.safe_mode,
            "channels_count": len(settings.channels),
            "api_hash_set": load_secrets().api_hash is not None,
        },
        "hh": {
            **manager.status("hh").as_dict(),
            "sent_today": hh_repo.applied_on(today),
            "found_today": hh_repo.found_on(today),
            "total_sent": hh_repo.applied_total(),
            "max_per_day": settings.hh_max_per_day,
            "login_state": login.state.value,
        },
        "recent": recent[:8],
    }
```

`as_dict()` из задачи 1 уже отдаёт `name`, `state`, `started_at`, `last_error` и `running` — фронтенду достаточно `running` и `state`.

- [ ] **Step 4: Удалить парсинг логов**

Из `api/tg_routes.py` удалить `get_sent_today`, `get_sent_total`, `get_found_today`, `get_sent_list`. Эндпоинт `/api/tg/chats` переводится на `TgRepo.recent(50)`. Эндпоинты `/api/tg/logs` и `/api/hh/logs` остаются — они отдают текст логов для вкладки «Логи», и это единственное, для чего логи теперь нужны.

- [ ] **Step 5: Прогнать тесты и закоммитить**

Run: `make test`
Expected: PASS, 63 passed.

```bash
git add api/ tests/test_state_api.py
git commit -m "refactor: compute dashboard metrics from the database"
```

---

### Задача 6: фронтенд — один опрос и делегирование событий

Закрывает L13 и снимает `'unsafe-inline'` для скриптов из CSP.

**Files:**
- Modify: `frontend/app.js` (`pollStatus`, обработчики), `frontend/index.html` (убрать `onclick`), `job_monitor/security.py` (CSP)
- Modify: `tests/test_frontend_safety.py` (добавить проверки)

- [ ] **Step 1: Дописать падающие тесты**

В `tests/test_frontend_safety.py`:

```python
import re

INDEX = Path(__file__).resolve().parents[1] / "frontend" / "index.html"
INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=\s*[\"']", re.I)


def test_index_has_no_inline_handlers():
    assert not INLINE_HANDLER.search(INDEX.read_text(encoding="utf-8")), (
        "обработчики вешаются через addEventListener — иначе CSP не ужесточить"
    )


def test_csp_forbids_inline_scripts(client):
    csp = client.get("/").headers["content-security-policy"]
    script_src = next(part for part in csp.split("; ") if part.startswith("script-src"))
    assert "'unsafe-inline'" not in script_src


def test_single_poll_endpoint_is_used():
    source = APP_JS.read_text(encoding="utf-8")
    assert "/api/state" in source
    assert "/tg/status" not in source
    assert "/hh/status" not in source
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_frontend_safety.py -v`
Expected: FAIL — в `index.html` 24 инлайновых `onclick`, CSP разрешает inline-скрипты, `pollStatus` дёргает 4 эндпоинта.

- [ ] **Step 3: Перевести разметку на делегирование**

В `index.html` каждый `onclick="foo()"` заменить на `data-action="foo"`; там, где нужен аргумент, — `data-action="openChat" data-arg="@user"`. В `app.js` один слушатель на документ:

```javascript
const ACTIONS = {
  toggleTG, toggleHH, saveChannels, addChannel, saveKeywords, addKw, addEx,
  saveTemplate, saveHHCoverLetter, saveTGSettings, saveApiKeys, sendAuthCode,
  verifyAuthCode, saveHHSettings, addHHKw, addHHEx, addStep, showLog,
  clearConsole, refreshLogs, showAbout, hideAbout, openChat, sendChatMessage,
  hhLoginStart, hhLoginConfirm,
};

document.addEventListener('click', event => {
  const target = event.target.closest('[data-action]');
  if (!target) return;
  const action = ACTIONS[target.dataset.action];
  if (action) action(target.dataset.arg);
});
```

Инлайновые `onmouseover`/`onmouseout` на кнопке «О приложении» (`index.html:19`) заменить на CSS `:hover`.

- [ ] **Step 4: Свести опрос к одному запросу**

`pollStatus` вызывает только `apiGet('/state')` и раскладывает ответ по `tgState`/`hhState`/`recentLog`. Было 4 запроса каждые 3 секунды, стало 1.

- [ ] **Step 5: Ужесточить CSP**

В `job_monitor/security.py` заменить `"script-src 'self' 'unsafe-inline'"` на `"script-src 'self'"`. `style-src 'unsafe-inline'` остаётся: инлайновые `style` в разметке никуда не делись и опасности не представляют.

- [ ] **Step 6: Прогнать тесты и проверить вживую**

Run: `make test`
Expected: PASS, 66 passed.

```bash
make run
```
Пройти по всем вкладкам, нажать каждую кнопку, открыть консоль браузера — ошибок CSP быть не должно.

- [ ] **Step 7: Закоммитить**

```bash
git add frontend/ job_monitor/security.py tests/test_frontend_safety.py
git commit -m "refactor: single state poll, delegated events, strict script CSP"
```

---

### Задача 7: снос лесов

**Files:**
- Delete: `monitor.py`, `hh_monitor.py`
- Modify: `pyproject.toml` (`py-modules`), `README.md`, `SECURITY.md`, `docs/superpowers/specs/2026-09-08-hardening-and-rework-spec.md`
- Create: `docs/adr/0001-in-process-workers.md`

- [ ] **Step 1: Убедиться, что старые точки входа никем не используются**

Run: `grep -rn "monitor.py\|hh_monitor\|subprocess\|monitor_pid\|hh_pid" --include="*.py" --include="*.js" --include="*.bat" --include="*.toml" .`
Expected: совпадения только в документации и в этом плане.

- [ ] **Step 2: Удалить старые скрипты**

```bash
git rm monitor.py hh_monitor.py
```

В `pyproject.toml` убрать `py-modules = ["monitor", "hh_monitor"]`, оставить `packages = ["api", "job_monitor"]`.

- [ ] **Step 3: Записать ADR**

`docs/adr/0001-in-process-workers.md`: контекст (подпроцессы + PID-файлы + парсинг логов), решение (asyncio-воркеры в одном процессе, статус в памяти и в БД), последствия (падение воркера не роняет API благодаря `_supervise`; Selenium требует отдельного потока; несколько экземпляров приложения одновременно запускать нельзя — БД и сессия одни).

- [ ] **Step 4: Обновить документацию**

В `README.md` — новая схема запуска (`make run` / `job-monitor run`) и описание вкладки входа в hh.ru. В `SECURITY.md` — актуальный список файлов в каталоге данных (`telegram_web.session` больше нет). В спецификации отметить L1–L13 как закрытые.

- [ ] **Step 5: Финальная проверка и коммит**

Run: `make test`
Expected: PASS, 66 passed.

```bash
make run
```
Полный прогон: запустить TG-воркер в safe mode, убедиться, что в логах появляются найденные контакты и метрики на дашборде растут; остановить; запустить HH, пройти вход, остановить.

```bash
git add -A
git commit -m "chore: remove standalone monitor scripts, document the new architecture"
```

---

## Итог плана 3

Закрыты L1, L2, L3, L4, L5, L6, L8, L9, L10, L11, L12, L13. Приложение — один процесс с супервизором воркеров, метрики считаются из БД, бизнес-логика покрыта тестами без сети и браузера. После этого можно браться за функциональные доработки: фундамент под них готов.
