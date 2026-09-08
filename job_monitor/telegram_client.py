"""Единственный Telethon-клиент приложения.

До этого файла у монитора и у веб-чата были две независимые сессии
(`telegram.session` и `telegram_web.session`) — то есть два входа в один и
тот же аккаунт (L8). Теперь обе стороны берут клиент отсюда: одна сессия
`paths.tg_session()` на всё приложение.
"""

from __future__ import annotations

from telethon import TelegramClient

from job_monitor import paths
from job_monitor.settings import Secrets, load_secrets

_client: TelegramClient | None = None
_credentials: Secrets | None = None


def get_client() -> TelegramClient:
    global _client, _credentials
    secrets = load_secrets()
    if not secrets.is_complete:
        raise RuntimeError("TG_API_ID и TG_API_HASH не заданы")
    if _client is None or _credentials != secrets:      # L11: ключи сменились — клиент новый
        _client = TelegramClient(str(paths.tg_session()), secrets.api_id, secrets.api_hash)
        _credentials = secrets
    return _client


def reset_client() -> None:
    global _client, _credentials
    _client = None
    _credentials = None
