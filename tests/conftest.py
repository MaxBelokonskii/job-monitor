import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="session", autouse=True)
def _job_monitor_data_dir(tmp_path_factory):
    """Keep the test suite hermetic.

    api/tg_routes.py and api/hh_routes.py call paths.logs_dir()/paths.path()
    at *import* time, so merely importing api.main (which the client fixture
    below does) would otherwise create directories under the developer's
    real ~/.job-monitor — and GET /api/config would read their real
    config.json and .env, so a corrupt personal .env could turn a passing
    test into a 500. This must be set before api.main (or monitor.py /
    hh_monitor.py) is ever imported; being session-scoped and autouse, it
    runs before any test — including ones that import those modules without
    using the client/raw_client fixtures at all (see test_smoke.py).
    """
    data_dir = tmp_path_factory.mktemp("job-monitor-data")
    os.environ["JOB_MONITOR_DATA_DIR"] = str(data_dir)
    yield data_dir


@pytest.fixture
def client(_job_monitor_data_dir) -> TestClient:
    from api.main import app
    from job_monitor.security import APP_TOKEN, TOKEN_HEADER

    return TestClient(
        app,
        base_url="http://127.0.0.1:8000",
        headers={TOKEN_HEADER: APP_TOKEN},
    )


@pytest.fixture
def raw_client(_job_monitor_data_dir) -> TestClient:
    from api.main import app

    return TestClient(app, base_url="http://127.0.0.1:8000")
