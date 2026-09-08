"""Обе вкладки логов в UI должны показывать что-то, кроме пустоты.

`GET /api/tg/logs` и `GET /api/hh/logs` отдают хвост файлов
`logs/tg_system.log` и `logs/hh.log`. Пока воркеры были отдельными
процессами, эти файлы писали `monitor.py` и `hh_monitor.py`; после переезда
воркеров внутрь процесса FastAPI (план 3) их не писал никто — в приложении
не настраивалось ни одного обработчика logging, и обе вкладки были пусты
навсегда. `job_monitor/logging_setup.py` это чинит, а этот файл пинает
свойства, которые легко потерять при следующей правке.
"""

from __future__ import annotations

import logging
import re
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from job_monitor import paths
from job_monitor.logging_setup import (
    CHANNELS,
    configure_logging,
    log_file,
    reset_logging,
    worker_logger,
)
from job_monitor.security import APP_TOKEN, TOKEN_HEADER


@pytest.fixture
def isolated_logging(tmp_path, monkeypatch):
    """Свой каталог данных на тест и чистые обработчики до и после.

    Обработчики висят на глобальных логгерах, поэтому без снятия они
    пережили бы тест и продолжили писать в tmp_path уже удалённого теста.
    """
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path / "state"))
    reset_logging()
    yield tmp_path / "state"
    reset_logging()


def test_log_files_live_in_the_data_dir(isolated_logging) -> None:
    targets = configure_logging()

    for channel, target in targets.items():
        assert target.parent == isolated_logging / "logs"
        assert target.name == CHANNELS[channel][0]
        assert target.exists(), "обработчик должен создать файл сразу, а не при первой записи"


def test_nothing_is_written_to_the_repository(isolated_logging) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    for target in configure_logging().values():
        assert repo_root not in target.parents, "лог не должен лежать в каталоге репозитория"


def test_log_file_is_private(isolated_logging) -> None:
    """Логи лежат в каталоге данных рядом с сессиями Telethon (`0700`),
    поэтому сам файл — `0600`."""
    for target in configure_logging().values():
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_configure_logging_is_idempotent(isolated_logging) -> None:
    configure_logging()
    logger = logging.getLogger("job_monitor.workers.telegram")
    after_first = len(logger.handlers)

    configure_logging()
    configure_logging()

    assert len(logger.handlers) == after_first, "повторный вызов удвоил обработчики"

    logger.info("однажды")
    for handler in logger.handlers:
        handler.flush()
    text = log_file("tg").read_text(encoding="utf-8")
    assert text.count("однажды") == 1, "запись продублировалась в файле"


def test_rotation_is_bounded(isolated_logging) -> None:
    """Раньше рост файла ограничивал `monitor.py`, обрезая лог до 5000
    строк; этого кода больше нет, поэтому границу держит сам обработчик."""
    configure_logging()
    logger = logging.getLogger("job_monitor.workers.hh")
    handler = next(h for h in logger.handlers if getattr(h, "maxBytes", 0))
    assert handler.maxBytes > 0
    assert handler.backupCount > 0


# ── Кто логирует, тот и виден на вкладке ──────────────────────────────
#
# Прежняя версия этих тестов была параметризована списком из семи имён,
# скопированным из `CHANNELS`, и сама же создавала запись в каждом. Пять из
# семи имён принадлежали модулям без единого `getLogger` — то есть проверка
# пиннила таблицу конфигурации, а не свойство: она осталась бы зелёной и
# если бы модуль перестал логировать, и если бы новый модуль начал логировать
# мимо таблицы. Ниже список берётся из ИСХОДНИКОВ, а таблица проверяется на
# соответствие им в обе стороны.

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNED_PACKAGES = ("api", "job_monitor")
MODULE_LOGGER = re.compile(r"^\s*\w+\s*=\s*logging\.getLogger\(__name__\)", re.MULTILINE)

# Модули, которые логируют, но сознательно не подключены ни к одной вкладке.
UNROUTED = {
    # Пишет только через дочерние логгеры `…manager.<имя воркера>` (см.
    # `_worker_log`), и они в таблице есть. Сам модульный логгер молчит:
    # сообщение «про какой-то воркер» без указания, про какой, попало бы не
    # на ту вкладку.
    "job_monitor.workers.manager",
}


def _modules_that_log() -> set[str]:
    """Имена логгеров всех модулей с `logging.getLogger(__name__)`."""
    found: set[str] = set()
    for package in SCANNED_PACKAGES:
        for source in (REPO_ROOT / package).rglob("*.py"):
            if "__pycache__" in source.parts:
                continue
            if not MODULE_LOGGER.search(source.read_text(encoding="utf-8")):
                continue
            relative = source.relative_to(REPO_ROOT).with_suffix("")
            parts = [part for part in relative.parts if part != "__init__"]
            found.add(".".join(parts))
    assert found, "не найдено ни одного модуля с getLogger(__name__) — дерево переехало?"
    return found


def _routed() -> dict[str, str]:
    return {name: channel for channel, (_file, names) in CHANNELS.items() for name in names}


def test_every_module_that_logs_is_visible_on_some_tab() -> None:
    """Единственный способ увидеть сообщение приложения — вкладка логов в UI,
    а обработчики вешаются на конкретные логгеры, а не на общего родителя.
    Модуль, начавший логировать и не вписанный в CHANNELS, поэтому пишет в
    пустоту."""
    missing = sorted(_modules_that_log() - set(_routed()) - UNROUTED)
    assert not missing, (
        "модуль логирует, но его логгера нет ни в одном канале — его записи "
        "не попадут ни в один файл и не будут видны в UI:\n" + "\n".join(missing)
    )


def test_no_channel_lists_a_module_that_never_logs() -> None:
    """Обратная сторона: имя в таблице, за которым никто не стоит, — это
    обещание вкладки, которое никогда не исполнится."""
    logging_modules = _modules_that_log()
    dead = sorted(
        name
        for name in _routed()
        if name not in logging_modules
        # дочерние логгеры супервизора (`…manager.tg`) заводятся динамически
        and not name.startswith("job_monitor.workers.manager.")
    )
    assert not dead, (
        "канал перечисляет логгер, которого никто не заводит — таблица описывает "
        "намерение, а не устройство:\n" + "\n".join(dead)
    )


@pytest.mark.parametrize("logger_name, channel", sorted(_routed().items()))
def test_records_land_in_the_right_file(isolated_logging, logger_name, channel) -> None:
    configure_logging()
    logging.getLogger(logger_name).info("метка %s", logger_name)
    for handler in logging.getLogger(logger_name).handlers:
        handler.flush()

    assert logger_name in log_file(channel).read_text(encoding="utf-8")
    other = "hh" if channel == "tg" else "tg"
    if logger_name in _routed_to_both():
        assert logger_name in log_file(other).read_text(encoding="utf-8")
    else:
        assert logger_name not in log_file(other).read_text(encoding="utf-8")


def _routed_to_both() -> set[str]:
    channels_of: dict[str, set[str]] = {}
    for channel, (_file, names) in CHANNELS.items():
        for name in names:
            channels_of.setdefault(name, set()).add(channel)
    return {name for name, channels in channels_of.items() if len(channels) > 1}


def test_a_dropped_settings_key_is_reported_on_both_tabs(isolated_logging) -> None:
    """Настройки, которые молча не применились, — сообщение для пользователя,
    а не для разработчика.

    `job_monitor/settings.py::_drop_unknown_fields` предупреждает о ключах,
    выброшенных при чтении сохранённой конфигурации: часть настроек не
    действует, и объяснение этому есть только здесь. Логгер `job_monitor.
    settings` не был подключён ни к одному файлу, то есть предупреждение не
    было видно НИГДЕ. Разделить «телеграмный ключ» и «hh-шный» неоткуда,
    поэтому оно идёт на обе вкладки.

    Проверяется настоящий путь: неизвестный ключ кладётся в базу, читается
    штатным `load_settings()`, и предупреждение ищется в файлах логов.
    """
    from job_monitor.db.connection import connect
    from job_monitor.db.repositories import SettingsRepo
    from job_monitor.settings import load_settings

    configure_logging()
    conn = connect(":memory:")
    SettingsRepo(conn).save({"safe_mode": True, "unicorn_mode": "yes"})

    load_settings(conn)

    for handler in logging.getLogger("job_monitor.settings").handlers:
        handler.flush()
    for channel in CHANNELS:
        text = log_file(channel).read_text(encoding="utf-8")
        assert "unicorn_mode" in text, (
            f"предупреждение о выброшенном ключе не видно на вкладке {channel}"
        )


@pytest.mark.parametrize("worker", ["tg", "hh"])
def test_supervisor_messages_land_on_that_workers_tab(isolated_logging, worker) -> None:
    """Падение воркера должно быть видно на вкладке ИМЕННО ЭТОГО воркера.

    Поэтому `WorkerManager` пишет сообщения про конкретный воркер не в свой
    модульный логгер, а в дочерний `job_monitor.workers.manager.<имя>`.
    """
    configure_logging()
    logger = worker_logger(worker)
    logger.error("воркер %s упал", worker)
    for handler in logger.handlers:
        handler.flush()

    assert f"воркер {worker} упал" in log_file(worker).read_text(encoding="utf-8")
    other = "hh" if worker == "tg" else "tg"
    assert f"воркер {worker} упал" not in log_file(other).read_text(encoding="utf-8")


def test_importing_the_module_creates_nothing(tmp_path, monkeypatch) -> None:
    """Импорт не должен создавать каталог данных: на этом стоят
    `tests/conftest.py` и `scripts/check_no_secrets.py`."""
    import importlib

    target = tmp_path / "untouched"
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(target))
    importlib.reload(importlib.import_module("job_monitor.logging_setup"))

    assert not target.exists()


def test_log_endpoints_return_something_after_the_app_starts() -> None:
    """Сквозная проверка: поднять приложение целиком (lifespan настраивает
    логирование), написать в логгеры воркеров и убедиться, что оба
    эндпоинта отдают непустой `log`."""
    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        logging.getLogger("job_monitor.workers.telegram").info("TG-воркер запущен")
        logging.getLogger("job_monitor.workers.hh").info("[HH] цикл запущен")
        for channel in CHANNELS:
            for handler in logging.getLogger(f"job_monitor.workers.manager.{channel}").handlers:
                handler.flush()

        headers = {TOKEN_HEADER: APP_TOKEN}
        tg = client.get("/api/tg/logs", headers=headers).json()["log"]
        hh = client.get("/api/hh/logs", headers=headers).json()["log"]

    assert "TG-воркер запущен" in tg
    assert "[HH] цикл запущен" in hh
    assert paths.logs_dir() / "tg_system.log" == log_file("tg")
