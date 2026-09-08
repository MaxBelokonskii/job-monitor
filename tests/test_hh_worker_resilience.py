"""Review fixes for task 3: the stop mechanism must be real, and a transient
Selenium/browser error must not permanently kill the worker.

Finding 1 — `asyncio.to_thread` does not wait for its thread on cancellation:
a task awaiting it reports `cancelled` within milliseconds regardless of
whether the thread function ever checks a stop flag, while the thread keeps
running. Combined with a module-level `threading.Event` that `run_worker()`
used to `.clear()` on every start, a stop-then-restart cycle could silently
orphan the previous cycle's thread — with its own Chrome and its own
database connection — untracked by `WorkerManager`. Fixed by giving each
`run_worker()` invocation its own `threading.Event` (never a module
singleton) and by wrapping the background thread in `asyncio.shield()`,
awaiting it directly (not just reacting to `CancelledError`) once the stop
flag is set, so `run_worker()` only returns once the thread has actually
exited.

Finding 2 — `hh_monitor.py`'s original loop caught `WebDriverException`
around its whole body and retried after a pause; the port to
`job_monitor/workers/hh.py` dropped that, so any Selenium/browser hiccup
(e.g. `driver.get()` failing) propagated out of `_blocking_loop`, through
`run_worker`, into `WorkerManager._supervise`, and permanently marked the
worker `error` — requiring a manual restart, with nothing recorded to
`worker_events`. Fixed by wrapping the per-cycle Selenium calls in
`try/except WebDriverException`, recording an `events.add("hh", "error", ...)`
row, and pausing (interruptibly) before retrying instead of dying.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from selenium.common.exceptions import WebDriverException

import job_monitor.workers.hh as hh
from job_monitor.db import connection as db_connection
from job_monitor.db.repositories import EventsRepo
from job_monitor.settings import save_settings
from job_monitor.workers.manager import WorkerManager


async def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.01) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("condition not met in time")


# ── Finding 1: the stop mechanism must be real ──────────────────────────


async def test_run_worker_waits_for_the_thread_to_actually_finish(monkeypatch):
    """Pins the fix: run_worker() must not return from cancellation until the
    background thread has actually stopped, not merely been asked to.

    Before the fix, `except asyncio.CancelledError: _stop_event.set(); raise`
    returned instantly — `asyncio.to_thread` does not wait for its thread on
    cancellation — so `finished` below would still be unset (and elapsed
    time near zero) by the time `await task` completes.
    """
    started = threading.Event()
    finished = threading.Event()
    cleanup_seconds = 0.25

    def fake_loop(stop_event: threading.Event) -> None:
        started.set()
        while not stop_event.wait(0.01):
            pass
        # Imitates a slow-but-real shutdown (driver.quit(), closing the DB
        # connection) — exactly the kind of work `asyncio.to_thread` cannot
        # wait for by itself.
        time.sleep(cleanup_seconds)
        finished.set()

    monkeypatch.setattr(hh, "_blocking_loop", fake_loop)

    task = asyncio.create_task(hh.run_worker())
    await asyncio.to_thread(started.wait, 2.0)
    assert started.is_set()

    t0 = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    elapsed = time.monotonic() - t0

    assert finished.is_set(), "run_worker() returned before its thread actually exited"
    assert elapsed >= cleanup_seconds * 0.8


async def test_stop_then_start_cannot_leave_the_previous_thread_running(monkeypatch):
    """Pins the fix: WorkerManager.stop() must only report success once the
    previous cycle's thread has truly exited, so a following start() can
    never run a second thread concurrently with it (the "second Chrome"
    class of bug this plan exists to eliminate).

    Before the fix, a shared module-level `_stop_event` meant `run_worker()`
    calling `.clear()` on a fresh start could erase a still-running previous
    thread's stop signal, and `run_worker()` returned from cancellation
    without waiting — so a stale thread could keep running, untracked,
    after `manager.stop()` had already reported "stopped".
    """
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_loop(stop_event: threading.Event) -> None:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            while not stop_event.wait(0.01):
                pass
            time.sleep(0.2)  # slow shutdown — where a "second Chrome" used to sneak in
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(hh, "_blocking_loop", fake_loop)

    mgr = WorkerManager()
    mgr.register("hh", hh.run_worker)

    await mgr.start("hh")
    await _wait_until(lambda: active == 1)

    await mgr.stop("hh")
    # By the time stop() has returned, the previous thread must have truly
    # exited — otherwise the next start() below runs concurrently with it.
    assert active == 0, "stop() returned while the previous thread was still running"

    await mgr.start("hh")
    await _wait_until(lambda: active == 1)
    await mgr.stop("hh")

    assert active == 0
    assert max_active == 1, "two worker threads were alive at the same time"


# ── Finding 2: a transient Selenium error must not kill the worker ─────


def test_blocking_loop_survives_a_webdriver_exception(monkeypatch, tmp_path):
    """Pins the fix: a WebDriverException raised while navigating/scraping
    must be caught inside `_blocking_loop`, recorded to `worker_events`, and
    retried after a pause — not propagate and kill the worker.
    """
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    db_connection.reset_connection()
    conn = db_connection.connect()
    save_settings(conn, {
        "hh_keywords": ["qa"], "hh_area_ids": [1],
        "hh_max_per_day": 50, "hh_check_interval": 60,
    })

    # Make every _interruptible_sleep (including the 60s error-recovery
    # pause) resolve instantly; the test only cares that it happens and is
    # interruptible, not that it takes real wall-clock time.
    monkeypatch.setattr(hh.time, "sleep", lambda _seconds: None)

    class FakeDriver:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, url: str) -> None:
            self.calls += 1
            if self.calls == 1:
                raise WebDriverException("net::ERR_CONNECTION_RESET")

        def quit(self) -> None:
            pass

    stop_event = threading.Event()
    scrape_calls = {"n": 0}

    def fake_get_vacancies(driver, settings):
        scrape_calls["n"] += 1
        stop_event.set()  # one successful pass after the recorded error is enough
        return []

    monkeypatch.setattr(hh, "setup_driver", lambda headless=False: FakeDriver())
    monkeypatch.setattr(hh, "load_cookies", lambda driver, target: True)
    monkeypatch.setattr(hh, "is_logged_in", lambda driver: True)
    monkeypatch.setattr(hh, "get_vacancies_from_page", fake_get_vacancies)

    try:
        hh._blocking_loop(stop_event)  # must return normally, not raise
    finally:
        db_connection.reset_connection()

    events = EventsRepo(conn).recent("hh", 10)
    error_events = [e for e in events if e["kind"] == "error"]
    assert len(error_events) == 1
    assert "net::ERR_CONNECTION_RESET" in error_events[0]["detail"]
    assert scrape_calls["n"] >= 1, "the loop never recovered to retry after the error"
