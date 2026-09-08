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
        # Whether the task finished cleanly, was cancelled, or ignored the
        # cancellation until the timeout fired, the manager's bookkeeping
        # must not stay half-stopped: drop the (possibly still-orphaned)
        # task and replace the status wholesale with a fresh "stopped"
        # object, so a subsequent start() is never blocked by a stale
        # entry and status() never reports a "stopping" state forever.
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
