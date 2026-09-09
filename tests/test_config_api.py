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


# ── В .env живут только секреты ────────────────────────────────────────

ALLOWED_ENV_KEYS = {"TG_API_ID", "TG_API_HASH"}


def _env_keys(tmp_path) -> set[str]:
    target = tmp_path / ".env"
    if not target.exists():
        return set()
    return {
        line.split("=", 1)[0].strip()
        for line in target.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.strip().startswith("#")
    }


def test_env_never_gets_application_settings(client, tmp_path):
    """`.env` не должен получать копию прикладных настроек.

    PATCH дописывал туда SAFE_MODE / PARSE_HISTORY / HISTORY_LIMIT, которых
    никто не читает: единственный читатель `.env` — `load_secrets()` в
    job_monitor/settings.py, и он берёт оттуда только два ключа. Копия к
    тому же устаревала по построению — она обновлялась лишь тогда, когда
    было что писать из секретов. Пользователь, открывший
    `~/.job-monitor/.env` и поправивший `SAFE_MODE=false` на `true`, считал
    себя в безопасном режиме, пока воркер читал `safe_mode` из SQLite и
    продолжал реально писать людям.
    """
    client.patch("/api/config", json={"api_id": "42", "api_hash": "abc"}, headers=AUTH)
    client.patch("/api/config", json={"safe_mode": False}, headers=AUTH)
    client.patch("/api/config", json={"api_hash": "def", "safe_mode": True,
                                      "parse_history": True, "history_limit": 7}, headers=AUTH)

    keys = _env_keys(tmp_path)
    assert keys <= ALLOWED_ENV_KEYS, f"в .env попали лишние ключи: {sorted(keys - ALLOWED_ENV_KEYS)}"
    assert "TG_API_HASH" in keys, "секреты писаться перестали — проверка стала вакуумной"
    # А настройки при этом сохранены — просто в базе, единственном их месте.
    assert client.get("/api/config", headers=AUTH).json()["history_limit"] == 7


# ── Битый руками `.env` не должен ронять сохранение настроек ───────────


def test_patch_survives_a_hand_broken_env_file(client, tmp_path):
    """Сквозной случай: строка `.env` без имени переменной (`=значение`)
    доводила `_validate("")` до ValueError внутри `read_env()`, а тот
    зовётся и из `GET /api/config`, и из `PATCH /api/config`. Пользователь
    получал 500 и не мог сохранить НИЧЕГО, включая исправление, — только
    правка файла руками, о которой ответ не сообщал."""
    (tmp_path / ".env").write_text("TG_API_ID=123\n=забытое-имя\n", encoding="utf-8")

    assert client.get("/api/config", headers=AUTH).status_code == 200
    response = client.patch("/api/config", json={"max_per_day": 9}, headers=AUTH)

    assert response.status_code == 200, response.text
    assert client.get("/api/config", headers=AUTH).json()["max_per_day"] == 9
