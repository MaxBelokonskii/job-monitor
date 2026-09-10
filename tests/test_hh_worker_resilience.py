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

Round 2 added two more:

Finding A — the round-1 wait survived only the FIRST cancellation. A bare
`await inner` inside the CancelledError handler is itself a cancellation
point: once a stop() had timed out (manager records `error`, keeps the task),
a second stop() cancelled `inner`, run_worker() returned instantly, stop()
reported "stopped" and dropped the task while the Selenium thread lived on —
and the next start() opened a second Chrome. Fixed by re-awaiting under
`asyncio.shield` until `inner.done()`, swallowing repeated cancellations.

Finding B — `except WebDriverException` was too narrow and `load_settings()` /
`applied_on()` sat outside the try entirely, so a sqlite3.OperationalError or
a ValueError from `random.randint(min, max)` with reversed bounds still killed
the worker permanently with nothing in `worker_events`.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from datetime import datetime

import pytest
from selenium.common.exceptions import WebDriverException

import job_monitor.workers.hh as hh
from job_monitor.db import connection as db_connection
from job_monitor.db.repositories import EventsRepo
from job_monitor.presets import ensure_default, save_criteria
from job_monitor.settings import save_settings
from job_monitor.workers.manager import WorkerAlreadyRunning, WorkerManager, WorkerState


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
    save_settings(conn, {"hh_max_per_day": 50, "hh_check_interval": 60})
    # Профессия — критерий, она в пресете; регион больше не настройка вовсе.
    save_criteria(conn, ensure_default(conn, datetime.now()), {"professions": ["qa"]})

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


# ── Finding A (round 2): a SECOND stop must not orphan the thread ───────


async def test_a_second_stop_does_not_orphan_the_worker_thread(monkeypatch):
    """Pins the round-2 fix: the wait for the Selenium thread must survive
    *repeated* cancellations, not just the first one.

    Round 1 wrote `except CancelledError: stop_event.set(); await inner`.
    That bare `await inner` only survives one cancellation: after a stop()
    times out (the manager records `error` and keeps the task tracked), a
    second stop() cancels the task again, the cancellation is delivered at
    `await inner` and cancels `inner` itself, so run_worker() returns at
    once, stop() reports "stopped" and pops the task while the Selenium
    thread is still alive — and the next start() opens a second Chrome.

    This is routine, not exotic: apply_to_vacancy is a WebDriverWait(15)
    plus ~7-13s of fixed sleeps, so even a cooperative worker regularly
    overruns the manager's 10s budget, and the user clicks Stop again.
    """
    active = 0
    max_active = 0
    lock = threading.Lock()
    release = threading.Event()

    def wedged_loop(stop_event: threading.Event) -> None:
        """A worker stuck inside a long Selenium call: it does not look at
        stop_event until the call returns."""
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            release.wait(10.0)  # hard bound so the test can never hang
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(hh, "_blocking_loop", wedged_loop)

    mgr = WorkerManager()
    mgr.register("hh", hh.run_worker)
    try:
        await mgr.start("hh")
        await _wait_until(lambda: active == 1)

        first = await mgr.stop("hh", timeout=0.2)
        assert first.state is WorkerState.error
        assert active == 1

        second = await mgr.stop("hh", timeout=0.2)
        assert second.state is WorkerState.error, (
            "the second stop() reported success while the Selenium thread was "
            "still running — the next start() would open a second Chrome"
        )
        assert active == 1

        with pytest.raises(WorkerAlreadyRunning):
            await mgr.start("hh")
        assert max_active == 1, "two worker threads were alive at the same time"
    finally:
        release.set()

    # Once the thread really exits, run_worker() finishes on its own and the
    # worker becomes restartable again — without ever having run twice.
    await _wait_until(lambda: active == 0)
    assert max_active == 1


# ── Finding B (round 2): the guard must not be Selenium-only ───────────


def _prepare_db(monkeypatch, tmp_path, **settings_overrides):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    db_connection.reset_connection()
    conn = db_connection.connect()
    # `safe_mode=False` явно: с решения D17 безопасный режим действует и на
    # hh.ru, а по умолчанию он ВКЛЮЧЁН — воркер в нём только собирает и до
    # отклика не доходит. Все тесты этого файла про устойчивость ЦИКЛА
    # ОТКЛИКОВ, значит отклик должен быть разрешён.
    save_settings(conn, {
        "hh_max_per_day": 50, "hh_check_interval": 60, "safe_mode": False,
        **settings_overrides,
    })
    # Профессия — критерий, она в пресете; регион больше не настройка вовсе.
    save_criteria(conn, ensure_default(conn, datetime.now()), {"professions": ["qa"]})
    # Skip the real waits without touching the global time module: the
    # cancellation semantics of _interruptible_sleep are pinned by the tests
    # above, here only the control flow around it matters.
    monkeypatch.setattr(
        hh, "_interruptible_sleep",
        lambda stop_event, seconds: not stop_event.is_set(),
    )
    return conn


class _OkDriver:
    def get(self, url: str) -> None:
        pass

    def quit(self) -> None:
        pass


def test_blocking_loop_survives_an_error_outside_the_selenium_calls(monkeypatch, tmp_path):
    """Pins the round-2 fix: load_settings()/applied_on() must be inside the
    guarded region, and the guard must not be `except WebDriverException`.

    Before the fix those two calls sat outside the try entirely, so a
    sqlite3.OperationalError ("database is locked" is plausible with WAL,
    two writers and busy_timeout=5000) killed the worker permanently with
    nothing written to worker_events.
    """
    conn = _prepare_db(monkeypatch, tmp_path)

    real_load_settings = hh.load_settings
    calls = {"n": 0}

    def flaky_load_settings(connection):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_load_settings(connection)

    stop_event = threading.Event()

    def fake_get_vacancies(driver, settings):
        stop_event.set()  # one successful pass after the recorded error is enough
        return []

    monkeypatch.setattr(hh, "load_settings", flaky_load_settings)
    monkeypatch.setattr(hh, "setup_driver", lambda headless=False: _OkDriver())
    monkeypatch.setattr(hh, "load_cookies", lambda driver, target: True)
    monkeypatch.setattr(hh, "is_logged_in", lambda driver: True)
    monkeypatch.setattr(hh, "get_vacancies_from_page", fake_get_vacancies)

    try:
        hh._blocking_loop(stop_event)  # must return normally, not raise
    finally:
        db_connection.reset_connection()

    assert calls["n"] >= 2, "the loop never recovered to retry after the error"
    error_events = [e for e in EventsRepo(conn).recent("hh", 10) if e["kind"] == "error"]
    assert len(error_events) == 1
    # The exception type is logged too, so a programming error stays legible.
    assert "OperationalError" in error_events[0]["detail"]
    assert "database is locked" in error_events[0]["detail"]


def test_reversed_delay_bounds_do_not_break_the_cycle(monkeypatch, tmp_path):
    """Pins the round-2 fix: nothing forces hh_delay_min <= hh_delay_max
    (GlobalSettings validates each field on its own), and
    random.randint(90, 30) raises ValueError — which used to escape the
    loop and kill the worker. min()/max() removes the crash path instead of
    merely logging it, so no error event may be recorded either.
    """
    conn = _prepare_db(monkeypatch, tmp_path, hh_delay_min=90, hh_delay_max=30)

    stop_event = threading.Event()
    scrapes = {"n": 0}
    processed: list[dict] = []
    # `found_at` обязателен: колонка NOT NULL, и настоящий
    # `get_vacancies_from_page` её всегда заполняет. Пока цикл записывал
    # вакансию только через подменённый здесь `_process_one`, заготовка без
    # неё проходила; теперь цикл кладёт находку в очередь сам, до отклика.
    vacancy = {
        "vacancy_id": "42", "title": "QA", "url": "https://hh.ru/vacancy/42",
        "found_at": "2026-09-10T10:00:00",
    }

    def fake_get_vacancies(driver, settings):
        scrapes["n"] += 1
        if scrapes["n"] == 1:
            return [vacancy]
        stop_event.set()
        return []

    monkeypatch.setattr(hh, "setup_driver", lambda headless=False: _OkDriver())
    monkeypatch.setattr(hh, "load_cookies", lambda driver, target: True)
    monkeypatch.setattr(hh, "is_logged_in", lambda driver: True)
    monkeypatch.setattr(hh, "get_vacancies_from_page", fake_get_vacancies)
    monkeypatch.setattr(
        hh, "_process_one",
        lambda driver, vacancy, criteria, settings, repo, events: (
            processed.append(vacancy) or True
        ),
    )

    try:
        hh._blocking_loop(stop_event)  # must return normally, not raise
    finally:
        db_connection.reset_connection()

    assert processed == [vacancy]
    assert [e for e in EventsRepo(conn).recent("hh", 10) if e["kind"] == "error"] == []
