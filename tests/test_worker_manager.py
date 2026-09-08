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


async def test_stop_timeout_leaves_consistent_state():
    """A worker that ignores cancellation for longer than the timeout must
    not leave the manager stuck in "stopping", and a fresh start() for the
    same name must not be blocked by the orphaned task."""

    async def stubborn() -> None:
        while True:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                # swallow the first cancellation and keep going, simulating
                # a worker that takes a while to actually shut down
                await asyncio.sleep(3600)

    manager = WorkerManager()
    manager.register("stubborn", stubborn)
    await manager.start("stubborn")
    await wait_for(manager, "stubborn", WorkerState.running)

    status = await manager.stop("stubborn", timeout=0.05)
    assert status.state is WorkerState.stopped
    assert manager.status("stubborn").state is WorkerState.stopped

    # bookkeeping must allow a new start even though the orphaned task
    # is technically still alive in the background
    await manager.start("stubborn")
    assert manager.status("stubborn").state in (WorkerState.starting, WorkerState.running)
    await manager.stop("stubborn", timeout=0.05)
