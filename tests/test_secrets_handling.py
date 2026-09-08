import stat

import pytest

from job_monitor import envfile


def test_rejects_newline_injection(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        envfile.write_env({"TG_API_ID": "123\nSAFE_MODE=false"})


def test_env_file_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    envfile.write_env({"TG_API_ID": "123", "TG_API_HASH": "abc"})
    mode = stat.S_IMODE((tmp_path / ".env").stat().st_mode)
    assert mode == 0o600


def test_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    envfile.write_env({"TG_API_ID": "123", "SAFE_MODE": "true"})
    assert envfile.read_env() == {"TG_API_ID": "123", "SAFE_MODE": "true"}


def test_read_env_rejects_null_byte_in_existing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("TG_API_HASH=abc\x00def\n", encoding="utf-8")
    with pytest.raises(ValueError):
        envfile.read_env()


def test_write_env_preserves_previously_written_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    envfile.write_env({"TG_API_HASH": "abc"})
    envfile.write_env({"SAFE_MODE": "false"})
    assert envfile.read_env() == {"TG_API_HASH": "abc", "SAFE_MODE": "false"}


def test_write_env_cleans_up_temp_file_on_failure(tmp_path, monkeypatch):
    """write_env() writes through a temp file (mkstemp) then os.replace()s it
    onto .env. A failure anywhere in between used to leave that 0600 temp
    file sitting in the data directory forever — no try/finally around the
    sequence."""
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(envfile.os, "replace", boom)
    with pytest.raises(OSError):
        envfile.write_env({"TG_API_ID": "123"})

    leftover = list(tmp_path.iterdir())
    assert leftover == [], f"temp file(s) left behind after a failed write: {leftover}"


def test_reveal_hash_endpoint_is_gone(client):
    assert client.get("/api/config/reveal-hash").status_code == 404


def test_config_response_never_contains_hash(client):
    body = client.get("/api/config").json()
    assert "api_hash" not in body
    assert body["api_hash_set"] in (True, False)
