"""L15 (log injection), после переезда воркеров внутрь процесса.

Исходный дефект: `monitor.py` писал текст сообщения из Telegram в
`logs/sent_log_*.txt` строкой `f"{username} | {ts} | {text[:80]}...\\n"` без
экранирования переводов строк, а `api/tg_routes.py::get_sent_list()`
парсил этот файл через `split("|")` в список контактов, который UI
показывал как «кому писали». `\\n` внутри недоверенного текста дорисовывал
записи, которых не было. План 1 закрыл это функцией
`monitor.py::sanitize_log_field`, и её пинал этот файл, вытаскивая функцию
из исходника `monitor.py` регуляркой.

Что изменилось в плане 3:

1. Разбор лога как источника данных исчез — контакты и вакансии живут в
   SQLite (`TgRepo`, `EventsRepo`), `get_sent_list()` больше нет, и
   `sanitize_log_field` вместе с `monitor.py` удалён (задача 7).
2. Но недоверенный ввод в файлы логов по-прежнему попадает: заголовки
   вакансий с hh.ru (`job_monitor/workers/hh.py`) и тексты постов из
   Telegram. `GET /api/hh/logs` и `GET /api/tg/logs` отдают файл в UI как
   есть, построчно — то есть `\\n` внутри значения по-прежнему рисует
   строки, которых не было.

Поэтому экранирование переехало туда, где теперь единственная граница
записи в лог: `job_monitor/logging_setup.SingleLineFormatter`. Этот файл
пинает уже её — на настоящем `LogRecord`, а не на скопированном исходнике.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from job_monitor.logging_setup import (
    LOG_FORMAT,
    SingleLineFormatter,
    configure_logging,
    log_file,
    reset_logging,
)
from job_monitor.security import APP_TOKEN, TOKEN_HEADER


def _format(message: str, *args: object) -> str:
    record = logging.LogRecord(
        name="job_monitor.workers.hh",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )
    return SingleLineFormatter(LOG_FORMAT).format(record)


def test_formatter_collapses_newlines_and_carriage_returns() -> None:
    assert "hello world" in _format("hello world")
    assert "\n" not in _format("hello\nworld")
    assert "\r" not in _format("hello\r\nworld")


def test_untrusted_argument_cannot_forge_a_log_line() -> None:
    """Настоящая атака: заголовок вакансии, подделывающий вторую строку лога."""
    malicious = (
        "legit vacancy\n"
        "2020-01-01 00:00:00 [ERROR] job_monitor.workers.hh: forged entry"
    )
    formatted = _format("[HH][OK] Отклик отправлен: %s", malicious)
    assert formatted.count("\n") == 0
    # Подделанные поля остались текстом, но на той же строке — отдельной
    # записью лога их больше не прочитать.
    assert "forged entry" in formatted


def test_traceback_stays_multiline() -> None:
    """Экранируется только сообщение.

    Трассировка от `log.exception` — наш собственный текст, не ввод
    пользователя, и её многострочность как раз и нужна для чтения. Схлопни
    мы и её, вкладка логов стала бы нечитаемой ровно в тот момент, когда в
    неё смотрят.
    """
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            name="job_monitor.workers.hh",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="воркер %s упал",
            args=("hh",),
            exc_info=__import__("sys").exc_info(),
        )
    formatted = SingleLineFormatter(LOG_FORMAT).format(record)
    assert "Traceback (most recent call last):" in formatted
    assert formatted.count("\n") > 1


# ── Санитайзер должен быть ПОДКЛЮЧЁН, а не только существовать ────────
#
# Всё выше конструирует `SingleLineFormatter` руками. Это проверяет
# двойник, а не предмет: мутация одной строки в
# `job_monitor/logging_setup.py::_make_handler`
#
#     handler.setFormatter(SingleLineFormatter(LOG_FORMAT, DATE_FORMAT))
#  →  handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
#
# проходила зелёной на всём наборе, и дефект L15 возвращался целиком:
# `\n` из заголовка вакансии hh.ru снова дорисовывает в `logs/hh.log`
# строки, которых не было, а `GET /api/hh/logs` отдаёт файл в UI построчно.
#
# Ниже поэтому проверяется свойство сквозь НАСТОЯЩУЮ настройку логирования:
# запись уходит через тот логгер, которым пользуется воркер, и читается из
# того файла, который отдаёт эндпоинт.

# Заголовок вакансии, каким его может прислать hh.ru: первая строка
# безобидна, вторая подделывает запись лога целиком — с временем, уровнем и
# именем логгера.
FORGED_LINE = "2020-01-01 00:00:00 [ERROR] job_monitor.workers.hh: отклик отправлен всем"
MALICIOUS_TITLE = f"QA инженер\n{FORGED_LINE}"


@pytest.fixture
def isolated_logging(tmp_path, monkeypatch):
    """Свой каталог данных и чистые обработчики до и после.

    Обработчики висят на глобальных логгерах: без снятия они пережили бы
    тест и продолжили писать в уже удалённый tmp_path.
    """
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path / "state"))
    reset_logging()
    yield tmp_path / "state"
    reset_logging()


def test_a_newline_from_hh_cannot_forge_a_line_in_the_real_log_file(isolated_logging) -> None:
    configure_logging()
    logger = logging.getLogger("job_monitor.workers.hh")
    logger.info("[HH][OK] Отклик отправлен: %s", MALICIOUS_TITLE)
    for handler in logger.handlers:
        handler.flush()

    lines = [line for line in log_file("hh").read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 1, (
        "одна запись воркера дала в файле несколько строк — обработчик, который "
        f"вешает configure_logging(), не несёт SingleLineFormatter: {lines}"
    )
    # Страховка от вакуумности: проверка выше имела бы смысл и при пустом
    # файле. Недоверенный текст должен дойти до лога — просто одной строкой.
    assert "отклик отправлен всем" in lines[0]


def test_a_newline_from_telegram_cannot_forge_a_line_in_the_log_endpoint(isolated_logging) -> None:
    """Тот же инвариант на границе, где его видит пользователь.

    `GET /api/{tg,hh}/logs` отдаёт хвост файла в UI как есть, построчно, —
    то есть подделанная запись выглядела бы в интерфейсе настоящей.
    """
    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        logging.getLogger("job_monitor.workers.telegram").info(
            "отправлено %s", "@hr_anna\n2020-01-01 00:00:00 [INFO] отправлено @всем"
        )
        for handler in logging.getLogger("job_monitor.workers.telegram").handlers:
            handler.flush()
        body = client.get("/api/tg/logs", headers={TOKEN_HEADER: APP_TOKEN}).json()["log"]

    assert "@hr_anna" in body, "запись не дошла до эндпоинта — проверка стала вакуумной"
    forged = [line for line in body.splitlines() if line.startswith("2020-01-01")]
    assert not forged, (
        f"в отдаваемом UI логе появились записи, которых не было: {forged}"
    )
