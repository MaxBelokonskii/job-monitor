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
from dataclasses import dataclass, field, replace
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
    """Состояние воркера ПЛЮС номер запуска, к которому оно относится.

    `epoch` — не украшение и не время: это ответ на вопрос «о каком запуске
    этот статус». Без него ответ на остановку было невозможно отличить от
    ответа о текущем состоянии, см. `WorkerManager.stop()`.
    """

    name: str
    state: WorkerState = WorkerState.stopped
    started_at: datetime | None = None
    last_error: str | None = None
    epoch: int = 0

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "started_at": self.started_at.isoformat(timespec="seconds") if self.started_at else None,
            "last_error": self.last_error,
            "running": self.state in (WorkerState.starting, WorkerState.running),
            "epoch": self.epoch,
        }


@dataclass
class WorkerManager:
    _factories: dict[str, WorkerFactory] = field(default_factory=dict)
    _tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    _statuses: dict[str, WorkerStatus] = field(default_factory=dict)
    _stop_waiters: dict[str, asyncio.Task] = field(default_factory=dict)
    _epochs: dict[str, int] = field(default_factory=dict)

    def register(self, name: str, factory: WorkerFactory) -> None:
        self._factories[name] = factory
        self._statuses.setdefault(name, WorkerStatus(name=name))

    def running(self) -> list[str]:
        """Имена воркеров, которых имеет смысл останавливать.

        `starting` включён намеренно: воркер в этом состоянии уже держит
        таску, и переключение пресета под ним — ровно тот случай, от которого
        защищает решение D9 (сообщение от одного пресета с резюме от
        другого).
        """
        return [
            name
            for name in self._factories
            if self.status(name).state in (WorkerState.starting, WorkerState.running)
        ]

    def _epoch(self, name: str) -> int:
        """Номер текущего (последнего) запуска воркера `name`.

        Монотонный счётчик, увеличиваемый на каждом успешном `start()`;
        `0` означает «этот воркер в этом процессе ещё не запускался».
        Существует, чтобы ответ на остановку можно было отличить от ответа
        о текущем состоянии, — см. `stop()`.

        **Счётчик живёт только в памяти процесса, и это правильно, а не
        забытая миграция.** Он нумерует запуски ВНУТРИ жизни процесса, а
        нумеровать больше нечего: сам реестр воркеров — `_tasks`,
        `_statuses` — тоже в памяти (`docs/adr/0001-in-process-workers.md`:
        единственный источник правды о работающем воркере — таска, которой
        владеет этот же процесс). После перезапуска процесса не существует
        ни одной таски, ни одного ожидающего клиента и ни одного ответа на
        остановку, чей epoch можно было бы с чем-то сравнить: всё, что
        epoch различал, умерло вместе с процессом. Положить его в БД
        значило бы завести число, которое переживает то, о чём оно
        рассказывает.
        """
        return self._epochs.get(name, 0)

    def status(self, name: str) -> WorkerStatus:
        return self._statuses.get(name, WorkerStatus(name=name, epoch=self._epoch(name)))

    def status_dict(self, name: str) -> dict:
        """`WorkerStatus.as_dict()` плюс живой признак `can_start`.

        `state == "error"` покрывает два разных случая, которые снаружи
        выглядели одинаково:

        * воркер упал сам (`_supervise` поймал исключение) — таска завершена,
          `start()` сработает;
        * воркер завис при остановке (`stop()` не дождался за timeout) — таска
          осталась под наблюдением (см. комментарий в `stop()`), и `start()`
          всегда ответит `WorkerAlreadyRunning`.

        UI не мог их различить и в обоих случаях предлагал «запустить снова»,
        то есть в половине случаев обещал то, чего сделать нельзя. Признак
        считается здесь, а не хранится в `WorkerStatus`: только менеджер видит
        таски, и только вычисление на момент запроса остаётся правдой, если
        зависшая таска всё-таки завершилась позже — тогда `can_start` честно
        станет `True` без внешнего вмешательства.
        """
        task = self._tasks.get(name)
        return {**self.status(name).as_dict(), "can_start": task is None or task.done()}

    async def start(self, name: str) -> WorkerStatus:
        if name not in self._factories:
            raise KeyError(f"воркер {name} не зарегистрирован")
        task = self._tasks.get(name)
        if task is not None and not task.done():
            raise WorkerAlreadyRunning(name)
        # Успешный запуск — единственное место, где счётчик двигается: с
        # этого момента любой ответ, помеченный прежним номером, относится
        # к запуску, которого больше нет.
        self._epochs[name] = self._epoch(name) + 1
        status = WorkerStatus(
            name=name,
            state=WorkerState.starting,
            started_at=datetime.now(),
            epoch=self._epoch(name),
        )
        self._statuses[name] = status
        self._tasks[name] = asyncio.create_task(self._supervise(name), name=f"worker:{name}")
        _worker_log(name).info("воркер %s запускается", name)
        return status

    async def stop(self, name: str, timeout: float = 10.0) -> WorkerStatus:
        """Остановить воркер и вернуть статус, помеченный ЕГО номером запуска.

        Пометка нужна из-за гонки двух клиентов, которую сторож остановки
        закрывает только наполовину. Последовательность: клиент A вызывает
        `stop()` и ждёт; таска гаснет; клиент B успевает вызвать `start()`;
        сторож просыпается, видит в `_tasks` ЧУЖУЮ таску и — правильно —
        молчит, возвращая текущий статус. Раньше A получал из этого
        `{"state": "running"}` и не мог отличить «мою остановку отменили»
        от «воркер уже перезапустили без меня»: в ответе не было ни слова о
        том, к какому запуску он относится.

        Поэтому `epoch` запоминается ЗДЕСЬ, на входе, до всякого ожидания, и
        возвращается вместе со статусом. Ответ A несёт номер СВОЕЙ
        остановки, а не номер текущего состояния, и клиент, чьё знание уже
        свежее (он успел опросить `/api/state`), видит по номеру, что ответ
        устарел, и не перерисовывает по нему экран.
        """
        task = self._tasks.get(name)
        if task is None or task.done():
            raise WorkerNotRunning(name)
        stopping_epoch = self._epoch(name)
        self._statuses[name].state = WorkerState.stopping
        task.cancel()
        # `stopping` — состояние ожидания, и кто-то обязан довести его до
        # конца. Раньше ждал сам `stop()`, то есть обработчик HTTP-запроса:
        # клиент, отсоединившийся за время десятисекундного ожидания
        # (перезагрузка вкладки — обычное дело), отменял корутину, и если
        # воркер при этом игнорировал cancel, статус навсегда оставался
        # `stopping`. Кнопка была вечно `disabled`, баннера нет (он только
        # для `error`), выхода из состояния — тоже. Сторож живёт отдельной
        # таской и переживает отмену ожидающего: `shield` отменяет только
        # ожидание, но не сторожа, поэтому терминальный статус будет
        # выставлен в любом случае.
        waiter = self._stop_waiters.get(name)
        if waiter is None or waiter.done():
            waiter = asyncio.create_task(
                self._await_stop(name, task, timeout), name=f"stop:{name}"
            )
            self._stop_waiters[name] = waiter
        status = await asyncio.shield(waiter)
        if status.epoch == stopping_epoch:
            return status
        # Сторож вернул статус чужого, более нового запуска. Копия, а не
        # правка на месте: `_await_stop` в этой ветке отдаёт сам объект из
        # `_statuses`, и переписать в нём epoch значило бы соврать про
        # живого воркера всем остальным читателям.
        return replace(status, epoch=stopping_epoch)

    async def _await_stop(self, name: str, task: asyncio.Task, timeout: float) -> WorkerStatus:
        # asyncio.wait_for(task, timeout) cannot be used here: once its
        # internal deadline fires it cancels the task *again* and then
        # waits for it to actually finish with NO further bound at all —
        # confirmed by hand: a task that swallows every CancelledError in
        # a loop makes wait_for hang forever, regardless of `timeout`.
        # asyncio.wait() is the primitive that genuinely honours the
        # timeout: it returns the task in `pending` instead of blocking,
        # so stop() itself can never hang on an uncooperative worker.
        _done, pending = await asyncio.wait({task}, timeout=timeout)
        # Сторож переживает отмену ожидающего (в этом весь смысл отдельной
        # таски), поэтому к моменту пробуждения `_tasks[name]` может уже
        # принадлежать ДРУГОМУ запуску: клиент, бьющий в API напрямую, успевает
        # получить `stopped`/`can_start` и вызвать start() за один-два тика
        # цикла. Записать тогда терминальный статус или, того хуже, выкинуть
        # чужую таску из `_tasks` — значит потерять живого воркера: менеджер
        # рапортует «остановлен», start() соглашается ещё раз, и появляется
        # второй (третий) экземпляр под тем же именем — ровно тот «второй
        # Chrome», ради которого этот модуль существует. Сторож устаревшего
        # запуска обязан промолчать: статус принадлежит тому, кто владеет
        # таской сейчас.
        if self._tasks.get(name) is not task:
            return self.status(name)
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
                # Сюда мы попадаем только владея `task` (проверка выше), то
                # есть текущий номер запуска — это номер именно этой таски.
                epoch=self._epoch(name),
            )
            self._statuses[name] = status
            _worker_log(name).error("%s", status.last_error)
            return status
        self._tasks.pop(name, None)
        self._statuses[name] = WorkerStatus(
            name=name, state=WorkerState.stopped, epoch=self._epoch(name)
        )
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
