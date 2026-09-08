"""Единственный Telethon-клиент приложения.

До этого файла у монитора и у веб-чата были две независимые сессии
(`telegram.session` и `telegram_web.session`) — то есть два входа в один и
тот же аккаунт (L8). Теперь обе стороны берут клиент отсюда: одна сессия
`paths.tg_session()` на всё приложение.
"""

from __future__ import annotations

from pathlib import Path

from telethon import TelegramClient

from job_monitor import paths
from job_monitor.settings import Secrets, load_secrets

_client: TelegramClient | None = None
_credentials: Secrets | None = None

# Telethon сам дописывает `.session` к переданному имени; `-journal` он
# создаёт рядом на время записи.
SESSION_SIDECARS = ("", "-journal")


def session_file() -> Path:
    """Файл сессии так, как его называет Telethon."""
    base = paths.tg_session()
    return base.with_name(base.name + ".session")


def get_client() -> TelegramClient:
    global _client, _credentials
    secrets = load_secrets()
    if not secrets.is_complete:
        raise RuntimeError("TG_API_ID и TG_API_HASH не заданы")
    if _client is None or _credentials != secrets:      # L11: ключи сменились — клиент новый
        _client = TelegramClient(str(paths.tg_session()), secrets.api_id, secrets.api_hash)
        # Файл сессии Telethon создаёт прямо здесь, в конструкторе, и по umask
        # — то есть `0644`, в отличие от `.env`, cookies и логов, которым права
        # ставятся явно. В нём лежит `auth_key`: его достаточно, чтобы войти в
        # аккаунт в обход 2FA. `-journal` — тот же файл в момент записи.
        target = session_file()
        for suffix in SESSION_SIDECARS:
            paths.secure_file(target.with_name(target.name + suffix))
        _credentials = secrets
    return _client


def reset_client() -> None:
    global _client, _credentials
    _client = None
    _credentials = None
