from datetime import datetime

import pytest

from job_monitor.db import connection
from job_monitor.db.repositories import EventsRepo, HhRepo, TgRepo
from job_monitor.security import APP_TOKEN, TOKEN_HEADER
from job_monitor.workers.manager import manager

AUTH = {TOKEN_HEADER: APP_TOKEN}


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    connection.reset_connection()
    # `manager` is a process-wide singleton (job_monitor/workers/manager.py),
    # so a worker crash recorded by an earlier test module (e.g.
    # test_lifespan_autostart.py's real, failing manager.start("tg")) leaves
    # its WorkerStatus at `error` for the rest of the pytest session — with
    # no reset, /api/state's `state` field here would depend on test run
    # order instead of on anything this file does.
    manager._statuses.clear()
    yield
    connection.reset_connection()
    manager._statuses.clear()


def test_counts_come_from_database(client):
    conn = connection.get_connection()
    now = datetime.now()
    TgRepo(conn).record_send("@hr_anna", "itvacancykz", "QA", now)
    HhRepo(conn).upsert({"vacancy_id": "1", "title": "QA", "status": "отклик отправлен",
                         "found_at": now.isoformat(), "applied_at": now.isoformat()})
    body = client.get("/api/state", headers=AUTH).json()
    assert body["tg"]["sent_today"] == 1
    assert body["tg"]["sent_total"] == 1
    assert body["hh"]["sent_today"] == 1


def test_worker_state_is_reported(client):
    body = client.get("/api/state", headers=AUTH).json()
    assert body["tg"]["running"] is False
    assert body["tg"]["state"] == "stopped"


def test_worker_state_says_whether_start_can_succeed(client):
    """Фронтенду мало `state`: в `error` он не отличает упавший сам воркер
    (start сработает) от зависшего при остановке (start всегда даст 400).
    `can_start` — этот признак, и без него кнопка обещает невыполнимое."""
    body = client.get("/api/state", headers=AUTH).json()
    assert body["tg"]["can_start"] is True
    assert body["hh"]["can_start"] is True


def test_recent_events_are_returned(client):
    EventsRepo(connection.get_connection()).add("tg", "sent", "@hr_anna", datetime.now())
    body = client.get("/api/state", headers=AUTH).json()
    assert body["recent"][0]["kind"] == "sent"


def test_state_requires_token(raw_client):
    assert raw_client.get("/api/state").status_code == 403


def test_log_parsing_helpers_are_gone():
    from api import tg_routes

    assert not hasattr(tg_routes, "get_found_today")
    assert not hasattr(tg_routes, "get_sent_today")
