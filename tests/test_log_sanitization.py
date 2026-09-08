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

from job_monitor.logging_setup import LOG_FORMAT, SingleLineFormatter


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
