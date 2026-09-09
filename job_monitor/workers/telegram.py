"""Правила отбора получателей и цикл TG-воркера.

Чистая логика (`is_eligible`, `extract_usernames`, `post_matches`,
`process_post`) не знает про Telethon и не ходит в сеть — тестируется
напрямую. `run_worker()` — тонкий адаптер: переводит события Telethon в
`IncomingPost` и делегирует решения чистым функциям.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from job_monitor.db.repositories import EventsRepo, TgRepo
from job_monitor.settings import AppSettings

log = logging.getLogger(__name__)
USERNAME_RE = re.compile(r"@[A-Za-z0-9_]{4,32}")
Sender = Callable[[str], Awaitable[None]]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class IncomingPost:
    channel: str
    text: str


def extract_usernames(text: str) -> list[str]:
    seen: list[str] = []
    for match in USERNAME_RE.findall(text):
        if match not in seen:
            seen.append(match)
    return seen


def is_eligible(username: str, settings: AppSettings, own_username: str | None) -> bool:
    handle = username.lstrip("@").lower()
    if handle.endswith("bot"):
        return False
    if own_username and handle == own_username.lstrip("@").lower():
        return False
    if handle in {channel.lstrip("@").lower() for channel in settings.channels}:
        return False
    return True


def post_matches(post: IncomingPost, settings: AppSettings) -> bool:
    """Пост из нужного канала, с ключевым словом и без стоп-слова."""
    channels = {channel.lstrip("@").lower() for channel in settings.channels}
    if post.channel.lstrip("@").lower() not in channels:
        return False
    text = post.text.lower()
    if not any(keyword.lower() in text for keyword in settings.keywords):
        return False
    return not any(bad.lower() in text for bad in settings.exclude)


async def process_post(
    post: IncomingPost,
    settings: AppSettings,
    repo: TgRepo,
    sender: Sender,
    clock: Clock = datetime.now,
    own_username: str | None = None,
) -> list[str]:
    """Обрабатывает один пост. Возвращает список username, которым отправили.

    Время берётся `clock()` на каждой итерации, а не один раз на пост.
    Раньше сюда приходил один `now`, снятый в момент прихода поста, и он же
    уходил во все `record_send` цикла. Между отправками стоит пауза до
    `delay_max` (до 3600 секунд), а хэндлов в одном посте бывает несколько,
    поэтому отправка, случившаяся после полуночи, попадала в базу вчерашней
    датой — и в тот же вчерашний дневной лимит. Недосчёт, не перерасход, но
    дата отправки в `tg_sends` при этом просто неверна, а «отправлено
    сегодня» на дашборде считается именно по ней.

    `clock` параметром, а не прямым вызовом `datetime.now()`: без него
    проверить смену суток можно было бы только подменой системного времени.
    """
    if not post_matches(post, settings):
        return []
    if settings.safe_mode:
        for username in extract_usernames(post.text):
            log.info("[SAFE MODE] найден контакт: %s", username)
        return []
    if not settings.template:
        log.warning("шаблон сообщения пуст — отправка пропущена")
        return []

    sent: list[str] = []
    for username in extract_usernames(post.text):
        # Лимит перечитывается из БД на каждой итерации: смена суток
        # обрабатывается сама собой, отдельная задача сброса не нужна.
        if repo.sent_on(clock().date()) >= settings.max_per_day:
            log.warning("дневной лимит %s достигнут", settings.max_per_day)
            break
        if not is_eligible(username, settings, own_username):
            continue
        if repo.was_sent(username):
            continue
        await sender(username)
        repo.record_send(username, post.channel, post.text[:80].strip(), clock())
        sent.append(username)
        await asyncio.sleep(random.randint(settings.delay_min, settings.delay_max))
    return sent


async def run_worker() -> None:
    from telethon import events

    from job_monitor.db.connection import get_connection
    from job_monitor.settings import load_settings
    from job_monitor.telegram_client import get_client

    client = get_client()
    await client.start()
    me = await client.get_me()
    own_username = getattr(me, "username", None)
    conn = get_connection()
    repo = TgRepo(conn)

    async def sender(username: str) -> None:
        settings = load_settings(conn)
        await client.send_message(username, settings.template)
        if settings.file_path:
            await client.send_file(username, settings.file_path)

    @client.on(events.NewMessage())
    async def handler(event) -> None:  # адаптер Telethon → чистая логика
        chat = await event.get_chat()
        channel = getattr(chat, "username", None)
        if not channel or not event.message.message:
            return
        post = IncomingPost(channel=channel, text=event.message.message)
        settings = load_settings(conn)
        if post_matches(post, settings):
            # Метрика «вакансий найдено» берётся отсюда, а не из текста логов (L2).
            EventsRepo(conn).add("tg", "vacancy", post.text[:80].strip(), datetime.now())
        for username in await process_post(post, settings, repo, sender, datetime.now, own_username):
            # Тоже `datetime.now()`, а не время прихода поста: обработка
            # одного поста растягивается на паузы между отправками и может
            # перейти за полночь. Остаточная неточность в пределах одного
            # поста (все события получают время конца обработки, а не своей
            # отправки) — цена того, что process_post возвращает только имена;
            # точная дата отправки лежит в `tg_sends`, куда её кладёт
            # `record_send`, и именно она отвечает за дневной лимит.
            EventsRepo(conn).add("tg", "sent", username, datetime.now())

    log.info("TG-воркер запущен")
    await client.run_until_disconnected()
