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
        return WorkerStatus(name=name, state=WorkerState.stopped, epoch=4)

    monkeypatch.setattr(manager, "stop", fake_stop)

    body = client.post("/api/hh/stop").json()

    assert body["status"] == "stopped"
    assert body["detail"] is None
    assert body["epoch"] == 4, (
        "ответ на остановку обязан нести номер запуска, который она "
        "останавливала — см. api/hh_routes.py::hh_stop"
    )


def test_start_answers_started_and_names_the_run(client, monkeypatch):
    """Близнец теста в tests/test_tg_stop_route.py: `epoch` в ответе на
    старт даёт UI свежий номер запуска сразу, не дожидаясь опроса."""
    from job_monitor.workers.manager import WorkerAlreadyRunning

    async def fake_start(name):
        return WorkerStatus(name=name, state=WorkerState.starting, epoch=9)

    monkeypatch.setattr(manager, "start", fake_start)

    # Пресет должен быть непустым: `POST /api/hh/start` теперь отказывает
    # при пустых критериях.
    active = next(
        p for p in client.get("/api/presets").json() if p["is_active"]
    )["id"]
    client.patch(f"/api/presets/{active}", json={"professions": ["QA"]})

    body = client.post("/api/hh/start").json()

    assert body == {"status": "started", "epoch": 9}
    assert isinstance(body["epoch"], int)

    async def already(name):
        raise WorkerAlreadyRunning(name)

    monkeypatch.setattr(manager, "start", already)
    refused = client.post("/api/hh/start")
    assert refused.status_code == 400
    assert refused.json()["detail"] == "HH монитор уже запущен"


def test_stop_without_a_running_worker_is_still_a_400(client, monkeypatch):
    async def fake_stop(name, timeout=10.0):
        raise WorkerNotRunning(name)

    monkeypatch.setattr(manager, "stop", fake_stop)

    response = client.post("/api/hh/stop")

    assert response.status_code == 400
    assert response.json()["detail"] == "HH монитор не запущен"
