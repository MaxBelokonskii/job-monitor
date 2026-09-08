"""POST /api/hh/stop must report what actually happened.

The route used to return {"status": "stopped"} unconditionally, discarding
the WorkerStatus the manager returns. A stop that timed out — the manager
records `error` and keeps the task tracked, so the worker is still alive —
was therefore shown to the UI as a success. The user then clicks Start, gets
a 400 "уже запущен", and clicks Stop again: exactly the double-stop path
that used to orphan the Selenium thread and open a second Chrome.
"""

from __future__ import annotations

import pytest

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

    response = client.post("/api/hh/stop")

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

    body = client.post("/api/hh/stop").json()

    assert body["status"] == "stopped"
    assert body["detail"] is None


def test_stop_without_a_running_worker_is_still_a_400(client, monkeypatch):
    async def fake_stop(name, timeout=10.0):
        raise WorkerNotRunning(name)

    monkeypatch.setattr(manager, "stop", fake_stop)

    response = client.post("/api/hh/stop")

    assert response.status_code == 400
    assert response.json()["detail"] == "HH монитор не запущен"
