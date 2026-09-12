from datetime import date, datetime

import pytest

from job_monitor.db import connection
from job_monitor.criteria import SearchCriteria
from job_monitor.db.repositories import TgFoundRepo, TgRepo
from job_monitor.settings import GlobalSettings
from job_monitor.workers.telegram import (
    IncomingPost, extract_usernames, is_eligible, post_matches, process_post,
)

NOW = datetime(2026, 9, 8, 12, 0, 0)
# `process_post` берёт время через `clock()` на каждой итерации, а не
# получает один снимок на пост: см. test_a_send_after_midnight_is_dated_today.
FROZEN = lambda: NOW      # noqa: E731 — часы-заглушка, не функция логики


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    connection.reset_connection()
    yield connection.connect()
    connection.reset_connection()


@pytest.fixture
def criteria():
    return SearchCriteria(
        channels=["qajobs"], tg_keywords=["qa"], tg_exclude=["senior"],
        template="привет",
    )


@pytest.fixture
def settings():
    return GlobalSettings(safe_mode=False, max_per_day=2, delay_min=1, delay_max=1)


class Sender:
    def __init__(self):
        self.sent: list[str] = []

    async def __call__(self, username: str, text: str, attachment=None) -> None:
        self.sent.append(username)


def call(post, criteria, settings, repo, conn, sender, clock):
    """Вызов `process_post` со свежим репозиторием найденного.

    Репозиторий здесь, а не в фикстуре, чтобы каждый тест видел его состояние
    явно: дедупликация постов и дедупликация контактов — разные свойства, и
    подменять одно другим нельзя.
    """
    from job_monitor.workers.telegram import process_post as _pp

    return _pp(post, criteria, settings, repo, TgFoundRepo(conn), sender, clock)


def test_extract_usernames():
    # @hr короче четырёх символов — под правила Telegram не подходит и отбрасывается
    assert extract_usernames("пишите @hr_anna или @hr.bob") == ["@hr_anna"]
    assert extract_usernames("@hr_anna и снова @hr_anna") == ["@hr_anna"]


def test_post_matches_returns_the_matched_keyword(criteria):
    """Возвращается само слово, а не `bool`: оно идёт и в подстановку
    `{ключевое_слово}`, и в колонку `matched_keyword`."""
    assert post_matches(IncomingPost("qajobs", "нужен QA", 1), criteria) == "qa"
    assert post_matches(IncomingPost("qajobs", "нужен Senior QA", 2), criteria) is None
    assert post_matches(IncomingPost("qajobs", "нужен повар", 3), criteria) is None
    assert post_matches(IncomingPost("other", "нужен QA", 4), criteria) is None


def test_skips_bots_and_source_channels(criteria):
    assert is_eligible("@hr_anna", criteria, own_username="@me") is True
    assert is_eligible("@some_bot", criteria, own_username="@me") is False
    assert is_eligible("@qajobs", criteria, own_username="@me") is False   # L10
    assert is_eligible("@me", criteria, own_username="@me") is False


async def test_sends_once_per_contact(conn, criteria, settings):
    """L1: дедупликация КОНТАКТОВ.

    Второй пост — с другим `message_id`. Иначе его отсёк бы дедуп ПОСТОВ, и
    проверка стала бы вакуумной: зелёной независимо от того, работает
    дедупликация контактов или нет.
    """
    repo, sender = TgRepo(conn), Sender()
    first = IncomingPost("qajobs", "Нужен QA, пишите @hr_anna", 1)
    same_contact_other_post = IncomingPost("qajobs", "Опять QA: @hr_anna", 2)
    assert await call(first, criteria, settings, repo, conn, sender, FROZEN) == ["@hr_anna"]
    assert await call(
        same_contact_other_post, criteria, settings, repo, conn, sender, FROZEN
    ) == []
    assert sender.sent == ["@hr_anna"]
    assert repo.contacts_total() == 1


async def test_the_same_post_is_processed_only_once(conn, criteria, settings):
    """Дедупликация ПОСТОВ, которой не было вовсе: тот же пост, пришедший
    повторным событием или перечитанный на следующем круге, обрабатывался
    заново."""
    repo, sender = TgRepo(conn), Sender()
    post = IncomingPost("qajobs", "Нужен QA, пишите @hr_anna", 7)
    assert await call(post, criteria, settings, repo, conn, sender, FROZEN) == ["@hr_anna"]
    assert await call(post, criteria, settings, repo, conn, sender, FROZEN) == []
    assert sender.sent == ["@hr_anna"]


async def test_respects_daily_limit_from_database(conn, criteria, settings):
    repo, sender = TgRepo(conn), Sender()
    repo.record_send("@old_one", None, None, NOW)
    repo.record_send("@old_two", None, None, NOW)
    post = IncomingPost("qajobs", "QA нужен, @hr_anna", 11)
    assert await call(post, criteria, settings, repo, conn, sender, FROZEN) == []
    assert repo.sent_on(date(2026, 9, 8)) == 2


async def test_limit_resets_on_a_new_day_without_any_reset_job(conn, criteria, settings):
    repo, sender = TgRepo(conn), Sender()
    repo.record_send("@old_one", None, None, datetime(2026, 9, 7, 23, 0))
    repo.record_send("@old_two", None, None, datetime(2026, 9, 7, 23, 30))
    post = IncomingPost("qajobs", "QA нужен, @hr_anna", 11)
    assert await call(post, criteria, settings, repo, conn, sender, FROZEN) == ["@hr_anna"]  # L9


async def test_exclude_word_blocks_post(conn, criteria, settings):
    repo, sender = TgRepo(conn), Sender()
    post = IncomingPost("qajobs", "Senior QA нужен, @hr_anna", 12)
    assert await call(post, criteria, settings, repo, conn, sender, FROZEN) == []


async def test_safe_mode_records_nothing(conn, criteria, settings):
    settings = settings.model_copy(update={"safe_mode": True})
    repo, sender = TgRepo(conn), Sender()
    post = IncomingPost("qajobs", "QA нужен, @hr_anna", 11)
    assert await call(post, criteria, settings, repo, conn, sender, FROZEN) == []
    assert sender.sent == []
    assert repo.contacts_total() == 0


async def test_post_from_foreign_channel_is_ignored(conn, criteria, settings):
    repo, sender = TgRepo(conn), Sender()
    post = IncomingPost("random_channel", "QA нужен, @hr_anna", 13)
    assert await call(post, criteria, settings, repo, conn, sender, FROZEN) == []


async def test_a_send_after_midnight_is_dated_today_not_yesterday(conn, criteria, settings):
    """L9, краевой случай: пост обрабатывается дольше, чем длятся сутки.

    Между отправками стоит пауза до `delay_max` (до 3600 секунд), а хэндлов в
    одном посте бывает несколько. Раньше `now` снимался один раз, в момент
    прихода поста, и уходил во ВСЕ `record_send` цикла: отправка, случившаяся
    после полуночи, ложилась в базу вчерашней датой — и в тот же вчерашний
    дневной лимит. Недосчёт, не перерасход, но дата отправки в `tg_sends`
    просто неверна, а «отправлено сегодня» на дашборде считается по ней.
    """
    before_midnight = datetime(2026, 9, 8, 23, 59, 30)
    after_midnight = datetime(2026, 9, 9, 0, 0, 30)
    # Шесть тиков: первый — запись поста в `tg_found` (время находки), затем
    # по два на каждую отправку (проверка лимита и `record_send`), и
    # последний — отметка «отклик отправлен» на самом посте.
    ticks = iter([
        before_midnight, before_midnight, before_midnight,
        after_midnight, after_midnight, after_midnight,
    ])

    repo, sender = TgRepo(conn), Sender()
    settings = settings.model_copy(update={"max_per_day": 10})
    post = IncomingPost("qajobs", "нужен QA: @hr_anna и @hr_boris", 14)

    assert await call(post, criteria, settings, repo, conn, sender, lambda: next(ticks)) == [
        "@hr_anna", "@hr_boris",
    ]

    assert repo.sent_on(date(2026, 9, 8)) == 1, "первая отправка должна остаться вчерашней"
    assert repo.sent_on(date(2026, 9, 9)) == 1, (
        "вторая отправка случилась после полуночи и обязана считаться сегодняшней"
    )


# ── Очередь найденного ────────────────────────────────────────────────


async def test_the_post_lands_in_the_queue_with_its_contacts(conn, criteria, settings):
    """Колонка контактов до этой задачи всегда была пуста: таблица
    заводилась на запись, и `process_post` передавал туда `None`. Для
    очереди контакт нужен — иначе непонятно, кому писать."""
    from job_monitor import statuses

    repo, sender = TgRepo(conn), Sender()
    post = IncomingPost("qajobs", "Нужен QA, пишите @hr_anna или @lead_qa", 21)
    await call(post, criteria, GlobalSettings(safe_mode=True), repo, conn, sender, FROZEN)

    row = TgFoundRepo(conn).list()[0]
    assert row["usernames"] == ["@hr_anna", "@lead_qa"]
    assert row["matched_keyword"] == "qa"
    assert row["status"] == statuses.NEW


async def test_safe_mode_leaves_the_post_new(conn, criteria):
    """Смысл безопасного режима: пост попал в очередь и ждёт человека."""
    from job_monitor import statuses

    repo, sender = TgRepo(conn), Sender()
    post = IncomingPost("qajobs", "Нужен QA, пишите @hr_anna", 22)
    sent = await call(
        post, criteria, GlobalSettings(safe_mode=True), repo, conn, sender, FROZEN
    )

    assert sent == []
    assert sender.sent == []
    assert TgFoundRepo(conn).list()[0]["status"] == statuses.NEW


async def test_a_sent_post_is_marked_as_answered_by_the_robot(conn, criteria, settings):
    """Без этого пост, на который робот уже написал, остался бы в очереди
    «новым» — и человек написал бы тому же контакту второй раз."""
    from job_monitor import statuses

    repo, sender = TgRepo(conn), Sender()
    post = IncomingPost("qajobs", "Нужен QA, пишите @hr_anna", 23)
    await call(post, criteria, settings, repo, conn, sender, FROZEN)

    assert sender.sent == ["@hr_anna"]
    row = TgFoundRepo(conn).list()[0]
    assert row["status"] == statuses.AUTO_APPLIED
    assert row["status_at"] == NOW.isoformat(timespec="seconds")


async def test_a_post_nobody_could_be_written_to_stays_new(conn, criteria, settings):
    """Все контакты уже написаны раньше — робот ничего не решил, значит
    решать человеку. «Пропущено» здесь было бы неправдой: это слово робота
    о вакансии, которую он рассмотрел и отверг."""
    from job_monitor import statuses

    repo, sender = TgRepo(conn), Sender()
    repo.ensure_contact("@hr_anna", datetime(2026, 9, 1, 12, 0, 0))
    post = IncomingPost("qajobs", "Нужен QA, пишите @hr_anna", 24)

    sent = await call(post, criteria, settings, repo, conn, sender, FROZEN)

    assert sent == []
    assert sender.sent == []
    assert TgFoundRepo(conn).list()[0]["status"] == statuses.NEW, (
        "робот ничего не решил — статус не его дело"
    )


async def test_a_repeated_post_is_not_counted_as_a_second_find(conn, criteria, settings):
    """Метрика «вакансий найдено» пишется ДО `process_post`, который сам
    дедуплицирует пост. Без проверки на повтор один и тот же пост,
    перечитанный на следующем круге, считался бы находкой каждый раз — и за
    сутки метрика состояла бы из одной вакансии, посчитанной сто раз."""
    from job_monitor.workers.telegram import counts_as_a_find

    found_repo = TgFoundRepo(conn)
    post = IncomingPost("qajobs", "Нужен QA, пишите @hr_anna", 31)

    assert counts_as_a_find(post, criteria, found_repo) is True

    await call(post, criteria, settings, TgRepo(conn), conn, Sender(), FROZEN)

    assert counts_as_a_find(post, criteria, found_repo) is False


async def test_a_post_that_does_not_match_is_never_a_find(conn, criteria):
    """Обратная сторона: проверка на повтор не должна подменить собой
    проверку на совпадение — иначе в метрику попал бы любой пост канала."""
    from job_monitor.workers.telegram import counts_as_a_find

    off_topic = IncomingPost("qajobs", "Продам гараж, @seller", 32)
    assert counts_as_a_find(off_topic, criteria, TgFoundRepo(conn)) is False


# ── Чтение истории каналов ────────────────────────────────────────────


async def test_history_is_scanned_channel_by_channel():
    """Настройка «Читать историю каналов» не делала НИЧЕГО.

    Переключатель был в интерфейсе, значение сохранялось в базу, рядом
    стояла вторая настройка «сколько сообщений читать» — и ни одну из них
    не читала ни одна строка воркера. Человек включал историю, перезапускал
    воркер и получал ту же пустую очередь, без единого объяснения.

    Это обещание интерфейса, за которым не было кода, — тот же класс, что
    карточка «Регион поиска» с выбором там, где константа.
    """
    from job_monitor.workers.telegram import scan_history

    прочитано = []

    async def fetch(channel, limit):
        прочитано.append((channel, limit))
        for message_id in range(1, 3):
            yield message_id, f"пост {message_id} из {channel}"

    обработано = []

    async def handle(post):
        обработано.append((post.channel, post.message_id, post.text))

    await scan_history(["qajobs", "itjobs"], fetch, handle, limit=50)

    assert прочитано == [("qajobs", 50), ("itjobs", 50)]
    assert len(обработано) == 4
    assert обработано[0] == ("qajobs", 1, "пост 1 из qajobs")


async def test_an_unreachable_channel_does_not_stop_the_scan():
    """Канал мог быть переименован, удалён или закрыт для этого аккаунта.
    Терять из-за него остальные шесть — значит наказывать человека за
    опечатку в одном названии."""
    from job_monitor.workers.telegram import scan_history

    async def fetch(channel, limit):
        if channel == "битый":
            raise ValueError("Cannot find any entity corresponding to \"битый\"")
        yield 1, "вакансия"

    обработано = []

    async def handle(post):
        обработано.append(post.channel)

    await scan_history(["битый", "живой"], fetch, handle, limit=10)

    assert обработано == ["живой"], "сканирование оборвалось на первом же отказе"


async def test_an_empty_message_is_skipped():
    """У поста может не быть текста вовсе — одна картинка или файл.
    Живой обработчик такие пропускает, история обязана вести себя так же."""
    from job_monitor.workers.telegram import scan_history

    async def fetch(channel, limit):
        yield 1, ""
        yield 2, None
        yield 3, "настоящая вакансия"

    обработано = []

    async def handle(post):
        обработано.append(post.message_id)

    await scan_history(["qajobs"], fetch, handle, limit=10)
    assert обработано == [3]


async def test_the_worker_reads_the_setting_it_offers(conn):
    """Связь настройки с поведением. Проверяется на уровне модуля: сам
    `run_worker` требует живого Telethon, а вот то, что он СМОТРИТ на
    `parse_history` и `history_limit`, проверить можно и нужно — именно
    отсутствие этой связи и было дефектом."""
    import inspect

    from job_monitor.workers import telegram

    source = inspect.getsource(telegram.run_worker)
    assert "parse_history" in source, "воркер не смотрит на «читать историю»"
    assert "history_limit" in source, "воркер не смотрит на «сколько сообщений»"
    assert "scan_history" in source, "история не сканируется при запуске"


# ── Отправка через Telethon: пауза на FloodWait и сбой вложения ────────
#
# Эти три строки — единственное место воркера, которое говорит с Telethon
# напрямую, и до сих пор они не проверялись ничем: `process_post` получает
# `sender` параметром, а сам `sender` жил замыканием внутри `run_worker`,
# куда без живого клиента не добраться. Аудит нашёл в этих трёх строках
# две дыры, обе — про потерю, а не про падение.

from telethon.errors import FloodWaitError  # noqa: E402

from job_monitor.workers.telegram import (  # noqa: E402
    FLOOD_MAX_WAIT, send_with_resume,
)


class FakeClient:
    def __init__(self, message_errors=None, file_errors=None):
        self.messages: list[tuple[str, str]] = []
        self.files: list[tuple[str, str]] = []
        self._message_errors = list(message_errors or [])
        self._file_errors = list(file_errors or [])

    async def send_message(self, username, text):
        if self._message_errors:
            raise self._message_errors.pop(0)
        self.messages.append((username, text))

    async def send_file(self, username, path):
        if self._file_errors:
            raise self._file_errors.pop(0)
        self.files.append((username, path))


def _flood(seconds: int) -> FloodWaitError:
    """Настоящий FloodWaitError, а не двойник: ловится он по типу, и
    подделка проверяла бы не тот `except`."""
    return FloodWaitError(request=None, capture=seconds)


async def test_a_flood_wait_is_waited_out_and_the_message_still_goes():
    """Telegram отвечает «подожди N секунд» на слишком частую отправку.

    Раньше это исключение не обрабатывалось нигде — я искал по всему
    репозиторию, ноль совпадений, — хотя README обещал «обработку
    FloodWaitError с автопаузой». Что происходило на самом деле: на живых
    постах Telethon глотал исключение и логировал, при чтении истории
    `scan_history` ловил его и переходил к следующему каналу. Сообщение не
    уходило, контакт не записывался, и человек видел тишину.
    """
    client = FakeClient(message_errors=[_flood(5)])
    slept: list[float] = []

    await send_with_resume(client, "@hr", "привет", None, sleep=_record(slept))

    assert client.messages == [("@hr", "привет")], "сообщение так и не ушло"
    assert slept == [5], f"пауза не выдержана или не та: {slept}"


def _record(into: list):
    async def sleep(seconds):
        into.append(seconds)
    return sleep


async def test_an_endless_flood_wait_is_raised_instead_of_slept_through():
    """Telegram умеет попросить подождать сутки. Спать столько внутри
    воркера — значит молча заблокировать его до завтра: ни в интерфейсе,
    ни в журнале не будет видно, почему ничего не происходит. Такое
    исключение поднимается наверх, где менеджер пометит воркер ошибкой с
    текстом причины."""
    client = FakeClient(message_errors=[_flood(FLOOD_MAX_WAIT + 1)])
    slept: list[float] = []

    with pytest.raises(FloodWaitError):
        await send_with_resume(client, "@hr", "привет", None, sleep=_record(slept))

    assert slept == [], "воркер всё-таки уснул на срок, который назвал Telegram"


async def test_a_second_flood_wait_is_not_slept_through_again():
    """Повтор один. Иначе воркер мог бы ходить по кругу «подожди —
    повтори» неограниченно, не отдавая управления и не сообщая об этом."""
    client = FakeClient(message_errors=[_flood(5), _flood(5)])
    slept: list[float] = []

    with pytest.raises(FloodWaitError):
        await send_with_resume(client, "@hr", "привет", None, sleep=_record(slept))

    assert slept == [5], f"повторов больше одного: {slept}"


async def test_a_failed_attachment_does_not_cancel_a_delivered_message(tmp_path):
    """Сообщение доставлено, файл не ушёл — это не повод терять контакт.

    Раньше исключение из `send_file` поднималось выше `record_send`, и
    контакт не записывался: следующий пост с тем же хэндлом писал человеку
    ВТОРОЙ раз. Резюме — не обязательная часть отклика (спецификация 4.2),
    и его пропажа не отменяет отправленного сообщения.
    """
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4")
    client = FakeClient(file_errors=[OSError("диск отвалился")])

    await send_with_resume(client, "@hr", "привет", resume)

    assert client.messages == [("@hr", "привет")]
    assert client.files == [], "файл всё-таки ушёл — проверка не о том"


async def test_the_attachment_goes_when_there_is_one(tmp_path):
    """Обратная сторона: терпимость к сбою не должна означать, что файл не
    отправляется вовсе."""
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4")
    client = FakeClient()

    await send_with_resume(client, "@hr", "привет", resume)

    assert client.files == [("@hr", str(resume))]


def test_the_worker_sends_through_the_tested_function_not_its_own_copy() -> None:
    """`sender` внутри `run_worker` обязан делегировать `send_with_resume`.

    Иначе проверки выше проверяют функцию, которой воркер не пользуется:
    ровно так эти три строки и прожили без единого теста — логика была
    вписана прямо в замыкание, куда без живого Telethon не добраться.
    """
    import inspect

    from job_monitor.workers import telegram

    source = inspect.getsource(telegram.run_worker)
    closure = source[source.index("async def sender"):]
    closure = closure[:closure.index("\n    async def ")]
    assert "send_with_resume" in closure, "воркер шлёт мимо проверенной функции"
    assert "client.send_message" not in closure, (
        "в замыкании снова своя отправка — это вторая копия правил про "
        "FloodWait и про сбой вложения"
    )
