"""POST /api/tg/stop must report what actually happened.

Twin of tests/test_hh_stop_route.py. The TG route kept the defect its HH
counterpart had already been cured of in task 3: it returned
{"status": "stopped"} unconditionally and threw away the WorkerStatus the
manager returns. A stop that timed out — the manager records `error` and
keeps the task tracked, so the worker is still alive — was therefore shown
to the user as a success. The user then clicks «Запустить», gets a 400 «уже
запущен», and clicks «Остановить» again.
"""

from __future__ import annotations

from job_monitor.workers.manager import (
    WorkerNotRunning,
    WorkerState,
    WorkerStatus,
    manager,
)


def test_stop_reports_the_error_state_instead_of_pretending_it_stopped(client, monkeypatch):
    async def fake_stop(name, timeout=10.0):
        return WorkerStatus(
            name=name,
            state=WorkerState.error,
            last_error=f"воркер не остановился за {timeout}s (проигнорировал cancel)",
        )

    monkeypatch.setattr(manager, "stop", fake_stop)

    response = client.post("/api/tg/stop")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error", "a stop that timed out must not be reported as stopped"
    assert "не остановился" in body["detail"]


def test_a_real_stop_still_reports_stopped(client, monkeypatch):
    """Response shape stays compatible: frontend/app.js checks
    `r.status === 'stopped'`."""

    async def fake_stop(name, timeout=10.0):
        return WorkerStatus(name=name, state=WorkerState.stopped)

    monkeypatch.setattr(manager, "stop", fake_stop)

    body = client.post("/api/tg/stop").json()

    assert body["status"] == "stopped"
    assert body["detail"] is None


def test_stop_without_a_running_worker_is_still_a_400(client, monkeypatch):
    async def fake_stop(name, timeout=10.0):
        raise WorkerNotRunning(name)

    monkeypatch.setattr(manager, "stop", fake_stop)

    response = client.post("/api/tg/stop")

    assert response.status_code == 400
    assert response.json()["detail"] == "TG воркер не запущен"


def test_start_still_answers_started(client, monkeypatch):
    """Start, unlike stop, cannot lie: manager.start() either raises
    WorkerAlreadyRunning or returns a `starting` status. The shape stays
    {"status": "started"} — frontend/app.js checks exactly that."""

    async def fake_start(name):
        return WorkerStatus(name=name, state=WorkerState.starting)

    monkeypatch.setattr(manager, "start", fake_start)

    assert client.post("/api/tg/start").json() == {"status": "started"}
