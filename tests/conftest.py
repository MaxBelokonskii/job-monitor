import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def client() -> TestClient:
    from api.main import app

    return TestClient(app, base_url="http://127.0.0.1:8000")
