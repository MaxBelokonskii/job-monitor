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
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from job_monitor import statuses
from job_monitor.criteria import SearchCriteria
from job_monitor.db.repositories import EventsRepo, TgFoundRepo, TgRepo
from job_monitor.settings import GlobalSettings

log = logging.getLogger(__name__)
USERNAME_RE = re.compile(r"@[A-Za-z0-9_]{4,32}")
Sender = Callable[[str, str, "Path | None"], Awaitable[None]]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class IncomingPost:
    channel: str
    text: str
    message_id: int


def extract_usernames(text: str) -> list[str]:
    seen: list[str] = []
    for match in USERNAME_RE.findall(text):
        if match not in seen:
            seen.append(match)
    return seen


def is_eligible(
    username: str, criteria: SearchCriteria, own_username: str | None
) -> bool:
    handle = username.lstrip("@").lower()
    if handle.endswith("bot"):
        return False
    if own_username and handle == own_username.lstrip("@").lower():
        return False
    if handle in {channel.lstrip("@").lower() for channel in criteria.channels}:
        return False
    return True


def post_matches(post: IncomingPost, criteria: SearchCriteria) -> str | None:
    """Совпавшее ключевое слово, либо `None`, если пост не подходит.

    Возвращается именно слово, а не `bool`: оно нужно и для подстановки
    `{ключевое_слово}` в шаблон, и для колонки `matched_keyword`. Вернуть
    `bool` и искать слово второй раз — значит завести два места с одной
    логикой, которые разойдутся при первой же правке.
    """
    channels = {channel.lstrip("@").lower() for channel in criteria.channels}
    if post.channel.lstrip("@").lower() not in channels:
        return None
    text = post.text.lower()
    matched = next(
        (kw for kw in criteria.tg_keywords if kw.lower() in text), None
    )
    if matched is None:
        return None
    if any(bad.lower() in text for bad in criteria.tg_exclude):
        return None
    return matched


PLACEHOLDERS = ("{канал}", "{ключевое_слово}", "{профессия}")


def render_template(
    template: str, channel: str, keyword: str, profession: str
) -> str:
    """Подставляет три надёжных значения в шаблон сообщения.

    `str.replace`, а НЕ `str.format`: шаблон пишет пользователь, а в
    пользовательском тексте фигурные скобки встречаются как обычные символы
    («ставка {30000}»). `format` на таком тексте падает с `KeyError`, а на
    конструкции `{x.__class__}` даёт доступ к атрибутам переданных объектов —
    то есть форматная строка от пользователя это не только хрупкость, но и
    уязвимость.

    Заголовка вакансии среди подстановок нет намеренно (решение D12): пост в
    канале — свободный текст без структуры, и любая эвристика по извлечению
    заголовка иногда даёт мусор, а мусор уходит живому человеку в личку.
    """
    return (
        template.replace("{канал}", channel)
        .replace("{ключевое_слово}", keyword)
        .replace("{профессия}", profession)
    )


def counts_as_a_find(
    post: IncomingPost, criteria: SearchCriteria, found_repo: TgFoundRepo
) -> bool:
    """Считать ли этот пост находкой для метрики «вакансий найдено».

    Отдельная функция, а не два условия в адаптере Telethon: адаптеру
    нужен живой клиент, поэтому проверить его в тесте нельзя, а свойство
    здесь нетривиальное. Событие пишется ДО `process_post`, который сам
    дедуплицирует пост, — без проверки на повтор пост, перечитанный на
    следующем круге или пришедший повторным событием, считался бы
    найденным заново, и за сутки метрика состояла бы из одной вакансии,
    посчитанной сто раз.
    """
    if post_matches(post, criteria) is None:
        return False
    return not found_repo.exists(post.channel, post.message_id)


async def process_post(
    post: IncomingPost,
    criteria: SearchCriteria,
    settings: GlobalSettings,
    repo: TgRepo,
    found_repo: TgFoundRepo,
    sender: Sender,
    clock: Clock = datetime.now,
    own_username: str | None = None,
    attachment: Path | None = None,
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
    keyword = post_matches(post, criteria)
    if keyword is None:
        return []

    # Дедупликация ПОСТОВ, которой не было вовсе: один и тот же пост,
    # перечитанный на следующем круге или пришедший повторным событием,
    # обрабатывался заново — и человек получал второе сообщение.
    preview = post.text[:80].strip()
    # Контакты пишутся в очередь, а не только используются для отправки:
    # без них экран «Найдено» не может сказать, кому писать. Гранулярность
    # статуса при этом — пост целиком, а не отдельный контакт: пост это
    # единица находки, и решение «не подходит» принимается по вакансии, а
    # не по человеку.
    contacts = extract_usernames(post.text)
    first_time = found_repo.record(
        post.channel, post.message_id, contacts, preview, keyword, clock()
    )
    if not first_time:
        return []

    if settings.safe_mode:
        for username in contacts:
            log.info("[SAFE MODE] найден контакт: %s", username)
        return []
    if not criteria.template:
        log.warning("шаблон сообщения пуст — отправка пропущена")
        return []

    profession = criteria.professions[0] if criteria.professions else ""
    text = render_template(criteria.template, post.channel, keyword, profession)

    sent: list[str] = []
    for username in contacts:
        # Лимит перечитывается из БД на каждой итерации: смена суток
        # обрабатывается сама собой, отдельная задача сброса не нужна.
        if repo.sent_on(clock().date()) >= settings.max_per_day:
            log.warning("дневной лимит %s достигнут", settings.max_per_day)
            break
        if not is_eligible(username, criteria, own_username):
            continue
        if repo.was_sent(username):
            continue
        await sender(username, text, attachment)
        repo.record_send(username, post.channel, post.text[:80].strip(), clock())
        sent.append(username)
        await asyncio.sleep(random.randint(settings.delay_min, settings.delay_max))

    if sent:
        # Пост, на который робот уже написал, не должен оставаться в
        # очереди «новым»: человек увидел бы его как неотвеченный и написал
        # бы тому же контакту второй раз. Если отправить не удалось никому
        # (все контакты уже написаны раньше, либо ни один не прошёл отбор),
        # статус остаётся «новая» — робот ничего не решил, значит решать
        # человеку. «Пропущено» здесь было бы неправдой: это слово робота о
        # вакансии, которую он рассмотрел и отверг.
        found_repo.set_post_status(
            post.channel, post.message_id, statuses.AUTO_APPLIED, clock()
        )
    return sent


#: Как достать прошлые посты канала. Параметром, а не прямым вызовом
#: Telethon: иначе сканирование истории нельзя проверить, не поднимая
#: живого клиента, — а именно непроверяемость и оставила эту настройку
#: ненаписанной.
HistoryFetch = Callable[[str, int], "AsyncIterator[tuple[int, str | None]]"]
PostHandler = Callable[[IncomingPost], Awaitable[None]]


async def scan_history(
    channels: list[str], fetch: HistoryFetch, handle: PostHandler, limit: int
) -> None:
    """Прогоняет прошлые посты каналов через ту же обработку, что и новые.

    Настройка «Читать историю каналов» существовала в интерфейсе, значение
    сохранялось в базу, рядом стояла вторая — «сколько сообщений читать», —
    и ни одну из них не читала ни одна строка воркера. Человек включал
    историю, перезапускал воркер и получал ту же пустую очередь, без
    единого объяснения.

    Обработчик тот же, что у живых постов, и это не экономия строк: всё,
    за что заплачено — дедупликация постов и контактов, безопасный режим,
    суточный лимит, — живёт в нём. Отдельный путь для истории означал бы
    вторую копию этих правил, которая однажды отстанет.

    Отказ на одном канале не останавливает остальные: канал могли
    переименовать, удалить или закрыть для этого аккаунта, и терять из-за
    него шесть других — значит наказывать за опечатку в одном названии.
    """
    for channel in channels:
        прочитано = 0
        try:
            async for message_id, text in fetch(channel, limit):
                if not text:
                    # Пост без текста — картинка или файл. Живой обработчик
                    # такие пропускает, история ведёт себя так же.
                    continue
                await handle(IncomingPost(channel=channel, text=text,
                                          message_id=message_id))
                прочитано += 1
        except Exception as error:  # noqa: BLE001 — см. докстринг
            log.warning("историю канала @%s прочитать не удалось: %s", channel, error)
            continue
        log.info("история @%s: просмотрено %s постов", channel, прочитано)


async def run_worker() -> None:
    from telethon import events

    from job_monitor import resume_store
    from job_monitor.db.connection import get_connection
    from job_monitor.db.repositories import ResumesRepo
    from job_monitor.presets import active_criteria
    from job_monitor.settings import load_settings
    from job_monitor.telegram_client import get_client

    client = get_client()
    await client.start()
    me = await client.get_me()
    own_username = getattr(me, "username", None)
    conn = get_connection()
    repo = TgRepo(conn)
    found_repo = TgFoundRepo(conn)

    def resolve_attachment(criteria: SearchCriteria) -> Path | None:
        """Путь к резюме из библиотеки, либо `None`.

        Отсутствие файла НЕ отменяет отправку: резюме — не обязательная часть
        отклика, и человек вправе писать только текстом (спецификация, 4.2).
        Пропавший файл — повод для строчки в логе, а не для молчания воркера.
        """
        if criteria.resume_id is None:
            return None
        row = ResumesRepo(conn).get(criteria.resume_id)
        if row is None:
            log.warning(
                "резюме %s выбрано в пресете, но его нет в библиотеке — "
                "отправляю без вложения", criteria.resume_id,
            )
            return None
        try:
            path = resume_store.path_of(row["stored_name"])
        except resume_store.ResumeRejected as error:
            log.warning("резюме недоступно (%s) — отправляю без вложения", error)
            return None
        if not path.exists():
            log.warning(
                "файл резюме «%s» пропал из каталога данных — отправляю без "
                "вложения", row["original_name"],
            )
            return None
        return path

    async def sender(username: str, text: str, attachment: Path | None) -> None:
        await client.send_message(username, text)
        if attachment is not None:
            await client.send_file(username, str(attachment))

    async def handle_post(post: IncomingPost) -> None:
        """Обработка одного поста — общая для живых и для истории.

        Одна на оба пути намеренно: всё, за что заплачено — дедупликация
        постов и контактов, безопасный режим, суточный лимит, — живёт
        здесь. Отдельный путь для истории означал бы вторую копию этих
        правил, которая однажды отстанет.
        """
        settings = load_settings(conn)
        criteria = active_criteria(conn)
        # Метрика «вакансий найдено» берётся отсюда, а не из текста логов (L2).
        # Правило «что считать находкой» живёт в `counts_as_a_find`, потому
        # что здесь его не проверить: адаптеру нужен живой Telethon.
        if counts_as_a_find(post, criteria, found_repo):
            EventsRepo(conn).add("tg", "vacancy", post.text[:80].strip(), datetime.now())
        for username in await process_post(
            post,
            criteria,
            settings,
            repo,
            found_repo,
            sender,
            datetime.now,
            own_username,
            resolve_attachment(criteria),
        ):
            # Тоже `datetime.now()`, а не время прихода поста: обработка
            # одного поста растягивается на паузы между отправками и может
            # перейти за полночь. Остаточная неточность в пределах одного
            # поста (все события получают время конца обработки, а не своей
            # отправки) — цена того, что process_post возвращает только имена;
            # точная дата отправки лежит в `tg_sends`, куда её кладёт
            # `record_send`, и именно она отвечает за дневной лимит.
            EventsRepo(conn).add("tg", "sent", username, datetime.now())

    @client.on(events.NewMessage())
    async def handler(event) -> None:  # адаптер Telethon → чистая логика
        chat = await event.get_chat()
        channel = getattr(chat, "username", None)
        if not channel or not event.message.message:
            return
        await handle_post(IncomingPost(
            channel=channel,
            text=event.message.message,
            message_id=event.message.id,
        ))

    log.info("TG-воркер запущен")

    # Настройки читаются здесь, на старте: «читать историю» — это решение
    # о том, что делать ПРИ ЗАПУСКЕ, и менять его на ходу нечему.
    startup = load_settings(conn)
    if startup.parse_history:
        async def fetch(channel: str, limit: int):
            async for message in client.iter_messages(channel, limit=limit):
                yield message.id, message.message

        log.info(
            "читаю историю каналов: по %s последних сообщений", startup.history_limit
        )
        await scan_history(
            active_criteria(conn).channels, fetch, handle_post, startup.history_limit
        )
        log.info("история просмотрена, перехожу к новым постам")

    await client.run_until_disconnected()
