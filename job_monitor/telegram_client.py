"""Единственный Telethon-клиент приложения.

До этого файла у монитора и у веб-чата были две независимые сессии
(`telegram.session` и `telegram_web.session`) — то есть два входа в один и
тот же аккаунт (L8). Теперь обе стороны берут клиент отсюда: одна сессия
`paths.tg_session()` на всё приложение.
"""

from __future__ import annotations

import inspect
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


async def reset_client() -> None:
    """Отпустить текущего клиента: отключиться и забыть.

    Раньше функция просто обнуляла ссылку. Старый клиент при этом оставался
    ПОДКЛЮЧЁННЫМ — Telethon держит сокет и открытый `telegram.session`, — а
    следующий `get_client()` строил нового поверх того же файла сессии. Два
    подключения к одной сессии Telethon — источник трудноуловимых отказов:
    сервер видит две сессии одного авторизационного ключа, апдейты уходят то
    в одно соединение, то в другое, а запись в файл сессии идёт из двух мест.

    Функция сделана асинхронной, а не «отключаемся отдельной задачей»:
    единственный прикладной вызывающий (`api/config_routes.py::update_config`)
    и так асинхронный, а фоновая задача означала бы, что новый клиент может
    родиться раньше, чем старый отпустил сессию, — то есть ровно то состояние,
    от которого мы избавляемся, только реже и невоспроизводимо. Исключение из
    незамеченной задачи вдобавок никто бы не увидел.
    """
    global _client, _credentials
    client, _client, _credentials = _client, None, None
    if client is None:
        return
    # Telethon возвращает корутину, когда цикл событий работает, и None,
    # когда он всё сделал синхронно.
    closing = client.disconnect()
    if inspect.isawaitable(closing):
        await closing
