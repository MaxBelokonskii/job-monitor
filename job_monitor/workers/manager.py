"""Asyncio-based supervisor for in-process workers.

Replaces the old subprocess + PID-file scheme: each worker is now an
asyncio task supervised inside the API process, so a crash is recorded in
its status instead of silently leaving a stale PID file behind, and the
API can always see the true state of a worker it started.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


def _worker_log(name: str) -> logging.Logger:
    """Логгер, чей вывод попадает на вкладку логов этого воркера.

    Сообщения супервизора о конкретном воркере (упал, не остановился в срок)
    пишутся не в `job_monitor.workers.manager`, а в его дочерний логгер
    `job_monitor.workers.manager.<имя>`. `job_monitor/logging_setup.py`
    подключает этот дочерний логгер к тому же файлу, что и сам воркер, — так
    причина, по которой воркер не работает, лежит там, куда пользователь
    смотрит: на вкладке именно этого воркера.
    """
    return logging.getLogger(f"{__name__}.{name}")


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
        _worker_log(name).info("воркер %s запускается", name)
        return status

    async def stop(self, name: str, timeout: float = 10.0) -> WorkerStatus:
        task = self._tasks.get(name)
        if task is None or task.done():
            raise WorkerNotRunning(name)
        self._statuses[name].state = WorkerState.stopping
        task.cancel()
        # asyncio.wait_for(task, timeout) cannot be used here: once its
        # internal deadline fires it cancels the task *again* and then
        # waits for it to actually finish with NO further bound at all —
        # confirmed by hand: a task that swallows every CancelledError in
        # a loop makes wait_for hang forever, regardless of `timeout`.
        # asyncio.wait() is the primitive that genuinely honours the
        # timeout: it returns the task in `pending` instead of blocking,
        # so stop() itself can never hang on an uncooperative worker.
        _done, pending = await asyncio.wait({task}, timeout=timeout)
        if pending:
            # The task ignored cancellation within the timeout window and
            # is still alive. Do NOT report "stopped": a caller that
            # believed that and called start() again would end up running
            # a second instance under the same name — exactly the "second
            # Chrome" failure class this module exists to prevent, now for
            # a wedged asyncio task (e.g. a stuck Selenium step) instead of
            # a wedged subprocess. Keep the task tracked so start() keeps
            # refusing, and surface the wedged state honestly as `error`
            # instead. The consequence — this worker cannot be restarted
            # without restarting the process — is the lesser evil, and it
            # is visible in the status rather than silently wrong.
            status = WorkerStatus(
                name=name,
                state=WorkerState.error,
                last_error=f"воркер не остановился за {timeout}s (проигнорировал cancel)",
            )
            self._statuses[name] = status
            _worker_log(name).error("%s", status.last_error)
            return status
        self._tasks.pop(name, None)
        self._statuses[name] = WorkerStatus(name=name, state=WorkerState.stopped)
        _worker_log(name).info("воркер %s остановлен", name)
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
            _worker_log(name).exception("воркер %s упал", name)
            status.state = WorkerState.error
            status.last_error = f"{type(error).__name__}: {error}"
        else:
            status.state = WorkerState.stopped


manager = WorkerManager()
