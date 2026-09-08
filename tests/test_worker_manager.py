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


async def test_stop_timeout_reports_error_and_refuses_restart():
    """A worker that ignores cancellation entirely — unconditionally, on
    every attempt, not just the first — must not be reported as "stopped":
    that would invite a second instance under the same name, exactly the
    "second Chrome" failure class this module exists to prevent (now for a
    wedged asyncio task, e.g. a stuck Selenium step, instead of a wedged
    subprocess). It must instead surface as `error`, keep the still-alive
    task tracked, and make start() refuse.
    """
    release = asyncio.Event()

    async def stubborn() -> None:
        while True:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                if release.is_set():
                    raise
                # swallow every cancellation unconditionally — this worker
                # never responds to being asked to stop

    manager = WorkerManager()
    manager.register("stubborn", stubborn)
    await manager.start("stubborn")
    await wait_for(manager, "stubborn", WorkerState.running)
    task = manager._tasks["stubborn"]

    status = await manager.stop("stubborn", timeout=0.05)

    assert status.state is WorkerState.error
    assert status.last_error is not None
    assert manager.status("stubborn").state is WorkerState.error
    assert task.done() is False, "stop() must not claim the task finished when it did not"

    with pytest.raises(WorkerAlreadyRunning):
        await manager.start("stubborn")

    # cleanup: let the still-alive task actually exit so it doesn't linger
    # past the end of the test
    release.set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_status_dict_distinguishes_a_crash_from_a_wedged_stop():
    """`error` покрывал два случая, снаружи неразличимых, и UI в обоих
    предлагал «запустить снова»: после самостоятельного падения start()
    действительно сработает, а после зависшей остановки таска осталась под
    наблюдением и start() может только ответить 400. `can_start` отвечает
    на этот вопрос — и отвечает на момент запроса, а не копией, снятой
    когда-то раньше."""
    async def boom() -> None:
        raise RuntimeError("сломалось")

    manager = WorkerManager()
    manager.register("boom", boom)
    await manager.start("boom")
    await wait_for(manager, "boom", WorkerState.error)
    crashed = manager.status_dict("boom")
    assert crashed["state"] == "error"
    assert crashed["can_start"] is True

    release = asyncio.Event()

    async def stubborn() -> None:
        while True:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                if release.is_set():
                    raise

    manager.register("stubborn", stubborn)
    await manager.start("stubborn")
    await wait_for(manager, "stubborn", WorkerState.running)
    assert manager.status_dict("stubborn")["can_start"] is False
    task = manager._tasks["stubborn"]

    wedged = await manager.stop("stubborn", timeout=0.05)
    assert wedged.state is WorkerState.error
    assert manager.status_dict("stubborn")["can_start"] is False, (
        "зависшая таска ещё отслеживается — start() ответит 400, и UI не должен обещать иного"
    )

    # Если зависшая таска всё-таки завершилась, признак обязан стать честным
    # сам, без внешнего вмешательства: он вычисляется, а не хранится.
    release.set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert manager.status_dict("stubborn")["can_start"] is True


async def test_status_dict_of_an_idle_worker_allows_start():
    manager = WorkerManager()
    manager.register("idle", lambda: asyncio.sleep(3600))
    assert manager.status_dict("idle")["can_start"] is True
    assert manager.status_dict("nope")["can_start"] is True


async def test_a_cancelled_stop_does_not_strand_the_worker_in_stopping():
    """Клиент отсоединился, пока `stop()` ждал — статус всё равно обязан
    прийти к терминальному.

    `stop()` ждал до 10 секунд прямо в обработчике HTTP-запроса. Перезагрузка
    вкладки в это окно отменяла корутину, и если воркер игнорировал cancel,
    `_statuses[name].state` навсегда оставался `stopping`: таска не done,
    `/api/state` вечно отдаёт `stopping`, кнопка вечно `disabled`, баннер не
    показывается (он только для `error`), объяснения нет и выхода из
    состояния тоже.
    """
    release = asyncio.Event()

    async def stubborn() -> None:
        while True:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                if release.is_set():
                    raise

    manager = WorkerManager()
    manager.register("stubborn", stubborn)
    await manager.start("stubborn")
    await wait_for(manager, "stubborn", WorkerState.running)
    task = manager._tasks["stubborn"]

    stopping = asyncio.create_task(manager.stop("stubborn", timeout=0.2))
    await wait_for(manager, "stubborn", WorkerState.stopping)
    stopping.cancel()                       # вкладку перезагрузили
    with pytest.raises(asyncio.CancelledError):
        await stopping

    await wait_for(manager, "stubborn", WorkerState.error)
    assert manager.status("stubborn").last_error is not None, (
        "пользователю нужно объяснение: баннер показывается только при error"
    )
    assert manager.status_dict("stubborn")["can_start"] is False

    release.set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_cancelled_stop_still_reaches_stopped_for_a_cooperative_worker():
    manager = WorkerManager()
    started = asyncio.Event()

    async def cooperative() -> None:
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(0.05)      # уборка занимает мгновение
            raise

    manager.register("idle", cooperative)
    await manager.start("idle")
    await started.wait()

    stopping = asyncio.create_task(manager.stop("idle", timeout=5))
    await wait_for(manager, "idle", WorkerState.stopping)
    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping

    await wait_for(manager, "idle", WorkerState.stopped)
    assert manager.status_dict("idle")["can_start"] is True, (
        "воркер честно остановился — start() должен снова стать возможен"
    )


async def _tick(times: int = 1) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


async def test_a_restart_during_a_pending_stop_is_not_reported_as_stopped():
    """Сторож остановки не имеет права хоронить чужой запуск.

    `_await_stop` живёт отдельной таской и переживает отмену ожидающего — в
    этом весь смысл выноса ожидания из `stop()`. Но пока сторож спит на
    `asyncio.wait`, отменённая таска успевает завершиться, и `start()`
    кладёт в `_tasks` НОВУЮ: между «воркер завершился» и «сторож проснулся»
    проходит ещё один тик цикла, а клиенту, который бьёт в API напрямую
    (в UI кнопка разблокируется только следующим опросом), этого хватает.

    Проснувшийся сторож делал `_tasks.pop(name)` и писал `stopped`, не
    глядя, чья таска лежит в словаре. Менеджер терял живого воркера —
    `state` рассинхронизирован, `can_start=True`, таска работает, — и
    следующий `start()` поднимал ВТОРОЙ экземпляр под тем же именем. Это тот
    самый класс «второй Chrome», ради которого модуль существует.

    Воспроизведение на 2156332: `tracked=False`, `can_start=True`,
    `new_task_alive=True`, а второй `start()` проходит.
    """
    manager = WorkerManager()
    manager.register("hh", lambda: asyncio.sleep(3600))

    await manager.start("hh")
    await wait_for(manager, "hh", WorkerState.running)
    first_task = manager._tasks["hh"]

    # Клиент нажал «стоп» и отсоединился: ожидание отменено, сторож жив.
    # Ожидание тиками, а не `wait_for` со сном: всё окно гонки укладывается
    # в один-два тика цикла, и сон в 10 мс его просто проспал бы.
    stopping = asyncio.create_task(manager.stop("hh", timeout=5))
    await _tick()
    assert manager.status("hh").state is WorkerState.stopping
    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping

    # ...и тут же нажал «старт». Воркер уже завершился, так что start()
    # проходит, но сторож старого запуска ещё не просыпался.
    assert first_task.done(), "отменённая таска ещё жива — start() и не должен проходить"
    assert not manager._stop_waiters["hh"].done(), (
        "сторож уже проснулся: в этом порядке тиков гонку не воспроизвести,"
        " тест перестал что-либо проверять"
    )
    await manager.start("hh")
    second_task = manager._tasks["hh"]
    assert second_task is not first_task

    await _tick(20)      # сторож просыпается и делает всё, что собирался

    assert manager._tasks.get("hh") is second_task, (
        "сторож старого запуска выкинул из-под наблюдения ЖИВУЮ таску нового"
    )
    assert not second_task.done(), "второй запуск не должен был пострадать"
    assert manager.status_dict("hh")["can_start"] is False, (
        "менеджер разрешает ещё один start() поверх работающего воркера"
    )
    with pytest.raises(WorkerAlreadyRunning):
        await manager.start("hh")

    second_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second_task


async def test_a_superseded_stop_waiter_leaves_the_status_alone():
    """Тот же инвариант без гонки, напрямую: сторож, чья таска больше не
    числится в `_tasks`, обязан вернуть текущий статус и не тронуть ни
    словарь тасок, ни статус. Гоночный тест выше зависит от порядка тиков
    цикла; этот проверяет сам контракт."""
    manager = WorkerManager()
    manager.register("hh", lambda: asyncio.sleep(3600))

    stale = asyncio.create_task(asyncio.sleep(0))
    await stale
    await manager.start("hh")
    live = manager._tasks["hh"]
    live_status = manager.status("hh")

    returned = await manager._await_stop("hh", stale, timeout=0)

    assert manager._tasks.get("hh") is live
    assert manager.status("hh") is live_status
    assert returned is live_status

    live.cancel()
    with pytest.raises(asyncio.CancelledError):
        await live
