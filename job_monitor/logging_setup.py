"""Файловое логирование приложения: две вкладки логов в UI — два файла.

Пока воркеры были отдельными процессами (`monitor.py`, `hh_monitor.py`),
каждый сам открывал свой файл: TG писал в `logs/tg_system.log`, HH — в
`logs/hh.log`, и `api/tg_routes.py` / `api/hh_routes.py` просто отдавали
хвост этих файлов в UI. После переезда воркеров внутрь процесса FastAPI
(план 3) писать эти файлы стало некому: воркеры логируют через
`logging.getLogger(__name__)`, а ни одного обработчика в приложении не
настраивалось — обе вкладки логов были пусты навсегда. Этот модуль
восстанавливает связь.

Три вещи, которые здесь важны и легко сломать:

1. **Ничего не происходит на импорте.** `configure_logging()` вызывается из
   `lifespan` в `api/main.py`. Импорт модуля не должен создавать каталогов и
   файлов: на этом стоят тесты (`tests/conftest.py` подменяет
   `$JOB_MONITOR_DATA_DIR` до импорта `api.main`, но не раньше самого
   импорта пакета) и `scripts/check_no_secrets.py`.
2. **Пути — только через `job_monitor.paths`.** Он уважает
   `$JOB_MONITOR_DATA_DIR` и держит каталог данных под `0700`. Логи лежат
   рядом с сессиями Telethon, поэтому файлы создаются с правами `0600`.
3. **Куда попадают сообщения супервизора.** `WorkerManager` не пишет в свой
   модульный логгер то, что относится к конкретному воркеру: падение,
   таймаут остановки и старт/стоп уходят в дочерний логгер
   `job_monitor.workers.manager.<имя>` (см. `worker_logger`). Так «воркер hh
   упал» видно на вкладке HH, а не в файле, которого пользователь не
   открывает. Альтернатива — писать супервизор в оба файла или в третий —
   отвергнута: пользователь смотрит ровно ту вкладку, чью кнопку он нажал, и
   там должна быть причина, по которой воркер не работает.
"""

from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from job_monitor import paths

MAX_BYTES = 1_000_000
BACKUP_COUNT = 3
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
FILE_MODE = 0o600

# Атрибут-маркер на обработчиках, которые создал этот модуль: по нему
# configure_logging() отличает свои обработчики от чужих и остаётся
# идемпотентным (повторный вызов не удваивает записи в файле).
_MARKER = "_job_monitor_channel"

# Логгер → файл. Ключ словаря совпадает с именем воркера в WorkerManager,
# поэтому worker_logger() ниже умеет по имени воркера отдать логгер,
# попадающий в нужный файл.
#
# В таблице только те логгеры, которые кто-то действительно заводит. Раньше
# из семи имён пять (`api.tg_routes`, `api.auth_routes`, `api.hh_routes`,
# `job_monitor.telegram_client`, `job_monitor.workers.hh_steps`) принадлежали
# модулям без единого `getLogger` — конфигурация наполовину описывала
# намерение, а не устройство, и параметризованный по ней тест пиннил сам
# список, а не свойство. Обработчики вешаются на конкретные (листовые)
# логгеры, а не на общего родителя, так что незаписанный сюда модуль в файл
# не попадёт вообще; ровно это и проверяет tests/test_logging_setup.py —
# «модуль, который логирует, обязан быть виден на какой-то вкладке».
#
# `job_monitor.presets` — в ОБА канала: он предупреждает, что часть критериев
# при чтении отброшена как непрошедшая проверку, а критерии в пресете общие
# для обеих сторон, и приписать предупреждение одному воркеру неоткуда.
#
# `job_monitor.db.migrations` — тоже в ОБА канала, по той же причине. Он
# предупреждает, что резервная копия БД перед миграцией не удалась: копия
# best-effort и миграцию не отменяет, но пользователь обязан узнать, что
# необратимый шаг прошёл без страховки. Отнести это к одному из воркеров
# неоткуда — схема общая.
#
# `job_monitor.settings` — в ОБА канала. Он предупреждает о выброшенных при
# чтении неизвестных ключах настроек (`_drop_unknown_fields`), то есть о том,
# что часть сохранённой конфигурации молча не применяется. Ключ мог быть и
# телеграмный, и hh-шный, разделить их источник неоткуда, а невидимое
# предупреждение бесполезно — пусть лучше строчка продублируется на обеих
# вкладках, чем не появится ни на одной.
#
# `job_monitor.paths` — тоже в оба, и по той же причине. Он говорит ровно об
# одном: `chmod` на каталоге данных запрещён (exFAT/SMB/NFS, чужой владелец
# после запуска под `sudo`, иммутабельный флаг), то есть заявленные `0700`/
# `0600` на этом томе не действуют. Каталог данных общий на оба воркера, и
# это сообщение о безопасности состояния, а не о работе одного из них.
#
# `job_monitor.envfile` — в оба по тому же основанию: он сообщает о строке
# `.env`, которую не удалось разобрать, то есть о настройке, которая молча не
# применилась. Разделить «телеграмная строка» и «hh-шная» неоткуда.
CHANNELS: dict[str, tuple[str, tuple[str, ...]]] = {
    "tg": (
        "tg_system.log",
        (
            "job_monitor.workers.telegram",
            "job_monitor.workers.manager.tg",
            "job_monitor.settings",
            "job_monitor.paths",
            "job_monitor.envfile",
            "job_monitor.db.migrations",
            "job_monitor.presets",
        ),
    ),
    "hh": (
        "hh.log",
        (
            "job_monitor.workers.hh",
            "job_monitor.workers.manager.hh",
            "job_monitor.settings",
            "job_monitor.paths",
            "job_monitor.envfile",
            "job_monitor.db.migrations",
            "job_monitor.presets",
        ),
    ),
}


def worker_logger(name: str) -> logging.Logger:
    """Логгер, чьи записи видны на вкладке логов воркера `name`."""
    return logging.getLogger(f"job_monitor.workers.manager.{name}")


def log_file(channel: str) -> Path:
    """Путь к файлу лога канала. Каталог создаётся при обращении."""
    return paths.logs_dir() / CHANNELS[channel][0]


def _escape(text: str) -> str:
    return text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\r")


# Значения, которые не должны попасть в лог ни при какой ошибке
# программиста. Лог отдаётся в UI через `GET /api/{tg,hh}/logs`, а `api_hash`
# — это полный доступ к Telegram-приложению; `auth_key` из сессии даёт вход в
# аккаунт в обход 2FA.
#
# Это последний рубеж, а не основная защита: секреты не кладутся в лог
# осознанно, и роут настроек вырезает их из патча до всякого логирования.
# Но мутационный аудит показал, что одна строка `log.warning("patch=%s",
# patch)`, добавленная по невнимательности выше по коду, отправила бы
# `api_hash` прямо на вкладку логов — и ни один тест этого не заметил бы.
# Рубеж стоит здесь, потому что здесь он один на все места записи.
SECRET_ENV_NAMES = ("TG_API_HASH", "TG_API_ID")
REDACTED = "***"

# Хеш Telegram — 32 шестнадцатеричных символа. Ловится по форме, а не по
# имени переменной: в лог значение попадает без ярлыка.
_HEX32 = re.compile(r"\b[0-9a-fA-F]{32}\b")
_NAMED_SECRET = re.compile(
    r"(?i)\b(api[_-]?hash|auth[_-]?key|tg[_-]?api[_-]?hash)\b\s*[:=]\s*['\"]?([^\s'\",}]+)"
)


def redact_secrets(text: str) -> str:
    """Заменяет секреты в строке лога на `***`.

    Две проверки, потому что секрет попадает в текст двумя путями: с ярлыком
    (`api_hash: abc…`, как при печати словаря настроек) и без него — просто
    значением. Форма хеша достаточно узнаваема, чтобы ловить второй случай:
    ровно 32 шестнадцатеричных символа.
    """
    redacted = _NAMED_SECRET.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
    return _HEX32.sub(REDACTED, redacted)


class SingleLineFormatter(logging.Formatter):
    """Схлопывает переводы строк в тексте сообщения.

    Наследник дефекта L15: в лог попадает недоверенный ввод — заголовки
    вакансий с hh.ru (`job_monitor/workers/hh.py`) и тексты постов из
    Telegram. `\\n` внутри такого значения дорисовывает в файл строки,
    которых не было, а `GET /api/hh/logs` отдаёт файл в UI как есть.
    Экранируется только сообщение: трассировка от `log.exception` — наш
    собственный текст, и её многострочность как раз и нужна для чтения.
    """

    def formatMessage(self, record: logging.LogRecord) -> str:
        original = record.message
        record.message = redact_secrets(_escape(original))
        try:
            return super().formatMessage(record)
        finally:
            record.message = original


class PrivateRotatingFileHandler(RotatingFileHandler):
    """`RotatingFileHandler`, который держит файл лога под `0600`.

    `chmod` в `_open`, а не один раз после создания: при ротации хендлер
    открывает новый `baseFilename` тем же методом, поэтому права переживают
    ротацию, а не только первый запуск.
    """

    def _open(self):  # type: ignore[no-untyped-def]
        stream = super()._open()
        os.chmod(self.baseFilename, FILE_MODE)
        return stream


def _make_handler(target: Path, channel: str, level: int) -> logging.Handler:
    handler = PrivateRotatingFileHandler(
        target,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(SingleLineFormatter(LOG_FORMAT, DATE_FORMAT))
    handler.setLevel(level)
    setattr(handler, _MARKER, channel)
    return handler


def configure_logging(level: int = logging.INFO) -> dict[str, Path]:
    """Подключает файловые обработчики. Идемпотентна.

    Возвращает {канал: путь к файлу} — удобно для тестов и для диагностики.
    """
    targets: dict[str, Path] = {}
    for channel, (filename, logger_names) in CHANNELS.items():
        target = paths.logs_dir() / filename
        targets[channel] = target
        handler: logging.Handler | None = None
        for logger_name in logger_names:
            logger = logging.getLogger(logger_name)
            if any(getattr(h, _MARKER, None) == channel for h in logger.handlers):
                continue
            if handler is None:
                handler = _make_handler(target, channel, level)
            logger.addHandler(handler)
            if logger.level == logging.NOTSET or logger.level > level:
                logger.setLevel(level)
    return targets


def reset_logging() -> None:
    """Снимает и закрывает обработчики этого модуля.

    Нужна тестам, которые меняют каталог данных между прогонами: без этого
    маркер идемпотентности заставил бы configure_logging() промолчать и
    записи ушли бы в файл из прошлого каталога.
    """
    for _channel, (_filename, logger_names) in CHANNELS.items():
        for logger_name in logger_names:
            logger = logging.getLogger(logger_name)
            for handler in list(logger.handlers):
                if getattr(handler, _MARKER, None) is not None:
                    logger.removeHandler(handler)
                    handler.close()
