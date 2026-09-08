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
    envfile.write_env({"TG_API_ID": "123", "HTTPS_PROXY": "http://127.0.0.1:3128"})
    assert envfile.read_env() == {"TG_API_ID": "123", "HTTPS_PROXY": "http://127.0.0.1:3128"}


def test_read_env_rejects_null_byte_in_existing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("TG_API_HASH=abc\x00def\n", encoding="utf-8")
    with pytest.raises(ValueError):
        envfile.read_env()


def test_write_env_preserves_previously_written_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    envfile.write_env({"TG_API_HASH": "abc"})
    envfile.write_env({"HTTPS_PROXY": "http://127.0.0.1:3128"})
    assert envfile.read_env() == {
        "TG_API_HASH": "abc", "HTTPS_PROXY": "http://127.0.0.1:3128",
    }


# Ключи перечислены здесь буквально, а не взяты из `envfile.RETIRED_KEYS`:
# тесты ниже должны падать на СВОЙСТВЕ («мёртвый ключ остался в файле»), а не
# на отсутствии константы. Синхронность списков держит отдельная проверка.
RETIRED_IN_ENV = ("SAFE_MODE", "PARSE_HISTORY", "HISTORY_LIMIT")


def test_the_retired_key_list_matches_the_module() -> None:
    assert set(envfile.RETIRED_KEYS) == set(RETIRED_IN_ENV), (
        "список мёртвых ключей разошёлся с job_monitor/envfile.py — тесты ниже "
        "перестали покрывать часть из них"
    )


@pytest.mark.parametrize("retired", RETIRED_IN_ENV)
def test_a_retired_key_is_swept_out_on_the_next_write(tmp_path, monkeypatch, retired):
    """`.env` пользователя, обновившегося с версии, которая писала туда копию
    прикладных настроек, носил бы `SAFE_MODE=false` вечно: `write_env`
    сливается со старым содержимым. Читателя у ключа нет, но его ВИД и был
    первопричиной находки — пользователь правит `SAFE_MODE` на `true` и
    считает себя в безопасном режиме, пока воркер читает `safe_mode` из базы
    и продолжает писать людям."""
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text(
        f"TG_API_ID=123\n{retired}=false\nHTTPS_PROXY=http://127.0.0.1:3128\n",
        encoding="utf-8",
    )

    envfile.write_env({"TG_API_HASH": "abc"})

    assert envfile.read_env() == {
        "TG_API_ID": "123",
        "TG_API_HASH": "abc",
        "HTTPS_PROXY": "http://127.0.0.1:3128",
    }, "мёртвый ключ остался, либо вместе с ним снесло что-то пользовательское"


@pytest.mark.parametrize("retired", RETIRED_IN_ENV)
def test_a_retired_key_cannot_be_written_back(tmp_path, monkeypatch, retired):
    """Вычистка стоит после слияния, поэтому мёртвый ключ не вернуть даже
    прямым вызовом: в `.env` живут только секреты (D5)."""
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    envfile.write_env({"TG_API_ID": "123", retired: "false"})
    assert envfile.read_env() == {"TG_API_ID": "123"}


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
