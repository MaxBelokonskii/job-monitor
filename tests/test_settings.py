import pytest
from pydantic import ValidationError

from job_monitor import settings as settings_module
from job_monitor.db import connection


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    connection.reset_connection()
    yield connection.connect()
    connection.reset_connection()


def test_defaults_are_returned_for_empty_db(conn):
    current = settings_module.load_settings(conn)
    assert current.max_per_day == 25
    assert "qa" in current.keywords
    assert current.safe_mode is True


def test_patch_updates_only_given_fields(conn):
    settings_module.save_settings(conn, {"max_per_day": 5})
    current = settings_module.load_settings(conn)
    assert current.max_per_day == 5
    assert current.delay_min == 60


def test_rejects_unknown_field(conn):
    with pytest.raises(ValidationError):
        settings_module.save_settings(conn, {"nonexistent": 1})


def test_rejects_out_of_range_limit(conn):
    with pytest.raises(ValidationError):
        settings_module.save_settings(conn, {"max_per_day": 0})


def test_settings_never_hold_secrets(conn):
    with pytest.raises(ValidationError):
        settings_module.save_settings(conn, {"api_hash": "deadbeef"})
    assert "api_hash" not in settings_module.load_settings(conn).model_dump()


def test_secrets_come_from_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)
    (tmp_path / ".env").write_text("TG_API_ID=42\nTG_API_HASH=abc\n", encoding="utf-8")
    secrets = settings_module.load_secrets()
    assert secrets.api_id == 42
    assert secrets.api_hash == "abc"
    assert secrets.is_complete is True


def test_secrets_incomplete_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)
    assert settings_module.load_secrets().is_complete is False
