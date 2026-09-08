import os
import stat

import pytest

from job_monitor import envfile
from job_monitor.settings import load_secrets

from conftest import TELEGRAM_SECRET_VARS


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


# ── Сторож: настоящие ключи Telegram не видны из тестов ───────────────


@pytest.mark.parametrize("name", TELEGRAM_SECRET_VARS)
def test_real_telegram_credentials_are_invisible_to_the_suite(name):
    """Падает, если прогон видит настоящие `TG_API_ID`/`TG_API_HASH`.

    Разработчик, следующий SECURITY.md, держит их в окружении. Без
    session-фикстуры `_no_real_telegram_credentials` любой тест, который
    входит в настоящий `lifespan` с `tg_autostart=True`, соединялся бы с
    Telegram его реальным `api_id` — молча, потому что исключение воркера
    глотает супервизор. Этот сторож — то, что делает промах видимым:
    сними фикстуру, запусти `env TG_API_ID=1 TG_API_HASH=x pytest`, и он
    покраснеет.
    """
    assert os.getenv(name) is None, (
        f"{name} виден тестам: фикстура _no_real_telegram_credentials из "
        "tests/conftest.py не сработала, и suite может уйти в сеть Telegram"
    )


def test_load_secrets_is_incomplete_without_credentials(tmp_path, monkeypatch):
    """Следствие сторожа выше на уровне приложения: `get_client()` в
    `job_monitor/telegram_client.py` строит `TelegramClient` только когда
    `Secrets.is_complete`, поэтому «секретов не видно» означает «клиента не
    будет»."""
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    assert load_secrets().is_complete is False


def test_reveal_hash_endpoint_is_gone(client):
    assert client.get("/api/config/reveal-hash").status_code == 404


def test_config_response_never_contains_hash(client):
    body = client.get("/api/config").json()
    assert "api_hash" not in body
    assert body["api_hash_set"] in (True, False)
