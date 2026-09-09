"""Covers L11 (Telethon client cached forever, never rebuilt when keys
change) and L8 (one shared session instead of two independent logins).

Uses a fake in place of `telethon.TelegramClient` — the real class touches
the filesystem/network on construction — so these stay pure, offline unit
tests of `job_monitor.telegram_client`'s own caching logic.
"""

from __future__ import annotations

import asyncio

import pytest

from job_monitor import paths, telegram_client


class FakeTelegramClient:
    """Captures constructor args instead of touching real telethon internals."""

    def __init__(self, session, api_id, api_hash):
        self.session = session
        self.api_id = api_id
        self.api_hash = api_hash
        self.disconnected = False

    async def disconnect(self):
        self.disconnected = True


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)
    monkeypatch.setattr(telegram_client, "TelegramClient", FakeTelegramClient)
    asyncio.run(telegram_client.reset_client())
    yield
    asyncio.run(telegram_client.reset_client())


def _write_secrets(tmp_path, api_id="123", api_hash="deadbeef"):
    (tmp_path / ".env").write_text(
        f"TG_API_ID={api_id}\nTG_API_HASH={api_hash}\n", encoding="utf-8"
    )


def test_raises_when_secrets_are_incomplete(tmp_path):
    with pytest.raises(RuntimeError):
        telegram_client.get_client()


def test_builds_client_from_secrets_on_a_single_session(tmp_path):
    _write_secrets(tmp_path)
    client = telegram_client.get_client()
    assert isinstance(client, FakeTelegramClient)
    assert client.session == str(paths.tg_session())
    assert client.api_id == 123
    assert client.api_hash == "deadbeef"


def test_get_client_is_cached_while_keys_are_unchanged(tmp_path):
    _write_secrets(tmp_path)
    first = telegram_client.get_client()
    second = telegram_client.get_client()
    assert first is second


def test_get_client_rebuilds_when_keys_change(tmp_path):
    """L11: a stale singleton kept serving the old api_id/api_hash forever,
    even after the user rotated their Telegram API credentials."""
    _write_secrets(tmp_path, api_id="123", api_hash="deadbeef")
    first = telegram_client.get_client()

    _write_secrets(tmp_path, api_id="456", api_hash="cafef00d")
    second = telegram_client.get_client()

    assert second is not first
    assert second.api_id == 456
    assert second.api_hash == "cafef00d"


def test_reset_client_forces_a_rebuild_even_with_unchanged_keys(tmp_path):
    _write_secrets(tmp_path)
    first = telegram_client.get_client()
    asyncio.run(telegram_client.reset_client())
    second = telegram_client.get_client()
    assert second is not first


async def test_reset_client_disconnects_the_old_client(tmp_path):
    """Отпустить клиента — это отключиться, а не только обнулить ссылку.

    Раньше `reset_client()` просто ставила `_client = None`. Старый клиент
    оставался подключённым: Telethon держал сокет и открытый
    `telegram.session`, — а следующий `get_client()` строил нового поверх
    того же файла сессии. Два подключения к одной сессии Telethon — сервер
    видит две сессии одного ключа, апдейты уходят то в одно соединение, то в
    другое, запись в файл идёт из двух мест.
    """
    _write_secrets(tmp_path)
    first = telegram_client.get_client()

    await telegram_client.reset_client()

    assert first.disconnected, "старый клиент остался подключённым к той же сессии"
    assert telegram_client.get_client() is not first


async def test_reset_client_without_a_client_is_a_no_op():
    await telegram_client.reset_client()      # не должно бросать


def test_key_change_also_disconnects(tmp_path):
    """L11 закрывался пересозданием клиента при смене ключей, но старый при
    этом тоже никто не отключал: `get_client()` строит нового, а `PATCH
    /api/config` перед этим зовёт `reset_client()` — именно он и обязан
    закрыть предыдущего."""
    _write_secrets(tmp_path, api_id="123", api_hash="deadbeef")
    first = telegram_client.get_client()

    asyncio.run(telegram_client.reset_client())
    _write_secrets(tmp_path, api_id="456", api_hash="cafef00d")
    second = telegram_client.get_client()

    assert first.disconnected
    assert second is not first
