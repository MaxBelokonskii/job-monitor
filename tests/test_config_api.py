import pytest

from job_monitor.db import connection
from job_monitor.security import APP_TOKEN, TOKEN_HEADER

AUTH = {TOKEN_HEADER: APP_TOKEN}


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)
    connection.reset_connection()
    yield
    connection.reset_connection()


def test_get_returns_defaults(client):
    body = client.get("/api/config", headers=AUTH).json()
    assert body["max_per_day"] == 25
    assert body["api_hash_set"] is False
    assert "api_hash" not in body


def test_patch_persists_to_database(client):
    client.patch("/api/config", json={"max_per_day": 9}, headers=AUTH)
    assert client.get("/api/config", headers=AUTH).json()["max_per_day"] == 9


def test_patch_rejects_invalid_value(client):
    response = client.patch("/api/config", json={"max_per_day": 0}, headers=AUTH)
    assert response.status_code == 422


def test_patch_stores_secrets_in_env_not_db(client, tmp_path):
    client.patch("/api/config", json={"api_id": "42", "api_hash": "abc"}, headers=AUTH)
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "TG_API_HASH=abc" in env_text
    body = client.get("/api/config", headers=AUTH).json()
    assert body["api_hash_set"] is True
    assert "api_hash" not in body


def test_config_json_is_not_created(client, tmp_path):
    client.patch("/api/config", json={"max_per_day": 9}, headers=AUTH)
    assert not (tmp_path / "config.json").exists()
