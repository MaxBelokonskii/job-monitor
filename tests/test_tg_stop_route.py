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
        return WorkerStatus(name=name, state=WorkerState.stopped, epoch=4)

    monkeypatch.setattr(manager, "stop", fake_stop)

    body = client.post("/api/tg/stop").json()

    assert body["status"] == "stopped"
    assert body["detail"] is None
    assert body["epoch"] == 4, (
        "ответ на остановку обязан нести номер запуска, который она "
        "останавливала: без него клиент не отличит «мою остановку отменили» "
        "от «воркер уже перезапустили без меня»"
    )


def test_stop_without_a_running_worker_is_still_a_400(client, monkeypatch):
    async def fake_stop(name, timeout=10.0):
        raise WorkerNotRunning(name)

    monkeypatch.setattr(manager, "stop", fake_stop)

    response = client.post("/api/tg/stop")

    assert response.status_code == 400
    assert response.json()["detail"] == "TG воркер не запущен"


def test_start_still_answers_started_and_names_the_run(client, monkeypatch):
    """Start, unlike stop, cannot lie: manager.start() either raises
    WorkerAlreadyRunning or returns a `starting` status. `status` stays
    `"started"` — frontend/app.js checks exactly that — and `epoch` names
    the run that just began, so the UI knows the fresh number without
    waiting for the next /api/state poll."""

    async def fake_start(name):
        return WorkerStatus(name=name, state=WorkerState.starting, epoch=7)

    monkeypatch.setattr(manager, "start", fake_start)

    body = client.post("/api/tg/start").json()

    assert body == {"status": "started", "epoch": 7}
    assert isinstance(body["epoch"], int), (
        "epoch приехал строкой — аннотация обработчика становится "
        "response_model, и `dict[str, str]` превратила бы число в текст или "
        "в 500; фронтенд сравнивает номера как числа"
    )
