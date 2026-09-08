import asyncio

import pytest

from api import auth_routes
from job_monitor import telegram_client
from job_monitor.db import connection


class FakeTelegramClient:
    """Captures constructor args instead of touching real telethon internals
    (the real TelegramClient raises ValueError if api_id/api_hash are falsy,
    which would mask rather than reproduce the bug this test pins)."""

    last_args = None

    def __init__(self, session, api_id, api_hash):
        FakeTelegramClient.last_args = (session, api_id, api_hash)
        self.disconnected = False

    async def disconnect(self):
        # `reset_client()` обязан отпустить старого клиента, а не просто
        # забыть про него: два подключения к одной сессии Telethon — источник
        # трудноуловимых отказов.
        self.disconnected = True


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)
    connection.reset_connection()
    asyncio.run(telegram_client.reset_client())
    yield
    asyncio.run(telegram_client.reset_client())
    connection.reset_connection()


def test_uses_the_shared_telegram_client_not_a_local_singleton():
    """L8: auth_routes used to build its own TelegramClient against a second
    session file (telegram_web.session) — a second, independent login to
    the same account. It must now go through job_monitor.telegram_client,
    the one client the TG worker also uses."""
    assert auth_routes.get_client is telegram_client.get_client


def test_get_client_builds_with_api_hash_from_env(tmp_path, monkeypatch):
    """C1: api/auth_routes.py used to build the Telethon client with
    api_hash='' because it read `load_config()["api_hash"]` (a key
    load_config() never sets) and an unloaded-into-this-process os.getenv
    fallback. This pins that the client is built from job_monitor.settings
    .load_secrets(), which actually reads .env."""
    (tmp_path / ".env").write_text(
        "TG_API_ID=12345\nTG_API_HASH=deadbeefcafef00d\n", encoding="utf-8"
    )
    monkeypatch.setattr(telegram_client, "TelegramClient", FakeTelegramClient)

    client = auth_routes.get_client()

    assert isinstance(client, FakeTelegramClient)
    _session, api_id, api_hash = FakeTelegramClient.last_args
    assert api_id == 12345
    assert api_hash == "deadbeefcafef00d"


def test_get_client_is_a_singleton(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "TG_API_ID=1\nTG_API_HASH=abc\n", encoding="utf-8"
    )
    monkeypatch.setattr(telegram_client, "TelegramClient", FakeTelegramClient)

    first = auth_routes.get_client()
    second = auth_routes.get_client()
    assert first is second
