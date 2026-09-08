"""Covers Finding 1 from the task 1 review: an autostart flag for a worker
that isn't registered yet must not fail application startup.

`api/main.py`'s `lifespan` calls `manager.start("tg")` / `manager.start("hh")`
when the matching settings flag is on. `register_workers()` is intentionally
still empty at this stage of the plan (tasks 2 and 3 register the real
factories), so with a persisted autostart flag turned on, `manager.start()`
raises `KeyError` for the unregistered name. An exception raised inside a
lifespan startup context fails the whole ASGI app boot (uvicorn logs
"Application startup failed" and exits) — exactly the failure mode the old,
removed `@app.on_event("startup")` handler avoided by wrapping each autostart
attempt in its own `try/except`.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from job_monitor.db.connection import get_connection
from job_monitor.security import APP_TOKEN, TOKEN_HEADER
from job_monitor.settings import save_settings


def test_autostart_for_unregistered_worker_does_not_prevent_boot() -> None:
    save_settings(get_connection(), {"tg_autostart": True})
    try:
        from api.main import app

        # TestClient must be used as a context manager to actually drive
        # the ASGI lifespan protocol (see task-1-report.md for why the
        # plain `client`/`raw_client` fixtures in conftest.py do not).
        # If lifespan startup let the KeyError from manager.start("tg")
        # propagate, entering this `with` block would raise it here.
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            response = client.get("/api/config", headers={TOKEN_HEADER: APP_TOKEN})
            assert response.status_code == 200
    finally:
        save_settings(get_connection(), {"tg_autostart": False})
