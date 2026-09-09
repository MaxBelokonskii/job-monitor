"""Автозапуск воркера не может уронить старт приложения.

`api/main.py`'s `lifespan` вызывает `manager.start("tg")` / `manager.start("hh")`
по флагам настроек. Исключение, поднятое внутри lifespan startup, валит весь
ASGI-бут (uvicorn пишет "Application startup failed" и выходит), поэтому каждый
автозапуск обёрнут в собственный `try/except`.

Прежняя версия этого файла пиннила частный случай — `KeyError` от
незарегистрированного воркера — и опиралась на то, что `register_workers()`
«ещё пустая». Задачи 2 и 3 планов зарегистрировали обе настоящие фабрики
(`api/main.py:34-37`), путь `KeyError` стал недостижим, и от теста остался
`assert response.status_code == 200`, который проходил бы и без единого
`try/except` в lifespan. Хуже того: тест входил в настоящий lifespan с
`tg_autostart=True`, то есть запускал настоящий `run_tg_worker` — с ключами из
окружения разработчика это означало живое соединение с Telegram на каждом
`make test` (см. `_no_real_telegram_credentials` в conftest.py).

Здесь инвариант шире и не зависит от того, что зарегистрировано: ЛЮБОЙ отказ
`manager.start()` при автозапуске (1) не мешает приложению подняться и (2) не
отменяет автозапуск второго воркера. Подмена `manager.start` заодно означает,
что настоящие воркеры в тесте не стартуют вовсе.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from job_monitor.db.connection import get_connection
from job_monitor.security import APP_TOKEN, TOKEN_HEADER
from job_monitor.settings import save_settings
from job_monitor.workers.manager import manager


@pytest.fixture
def both_autostarts_on():
    save_settings(get_connection(), {"tg_autostart": True, "hh_autostart": True})
    yield
    save_settings(get_connection(), {"tg_autostart": False, "hh_autostart": False})


def _boot_and_call_api() -> int:
    from api.main import app

    # TestClient обязан использоваться как контекстный менеджер, иначе
    # протокол lifespan не проигрывается вовсе (см. task-1-report.md, почему
    # обычные фикстуры client/raw_client из conftest.py его не запускают).
    # Если бы lifespan выпускал исключение автозапуска наружу, оно вылетело
    # бы прямо на входе в `with`.
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        return client.get("/api/config", headers={TOKEN_HEADER: APP_TOKEN}).status_code


def test_failing_autostart_does_not_prevent_boot(both_autostarts_on, monkeypatch) -> None:
    attempted: list[str] = []

    async def exploding_start(name: str):
        attempted.append(name)
        raise KeyError(f"воркер {name} не зарегистрирован")

    monkeypatch.setattr(manager, "start", exploding_start)

    assert _boot_and_call_api() == 200
    assert attempted == ["tg", "hh"], (
        "упавший автозапуск TG не должен отменять автозапуск HH: каждый обёрнут"
        f" в собственный try/except, а попыток было {attempted}"
    )


def test_autostart_failure_is_logged_on_the_workers_own_tab(
    both_autostarts_on, monkeypatch, caplog
) -> None:
    """Причина, по которой пользователь видит «остановлен», должна лежать в
    логгере именно этого воркера, а не в общем потоке: `job_monitor/
    logging_setup.py` подключает `job_monitor.workers.<имя>` к тому файлу,
    который показывает вкладка логов воркера."""

    async def exploding_start(name: str):
        raise RuntimeError(f"boom {name}")

    monkeypatch.setattr(manager, "start", exploding_start)

    with caplog.at_level("ERROR"):
        assert _boot_and_call_api() == 200

    loggers = {record.name for record in caplog.records if record.levelname == "ERROR"}
    assert "job_monitor.workers.manager.tg" in loggers, loggers
    assert "job_monitor.workers.manager.hh" in loggers, loggers


def test_autostart_is_skipped_when_the_flags_are_off(monkeypatch) -> None:
    """Обратная сторона инварианта: без флагов lifespan не трогает воркеров
    вовсе — иначе `make test` поднимал бы настоящий TG-воркер (и настоящий
    Chrome) на каждом тесте, который входит в lifespan."""
    save_settings(get_connection(), {"tg_autostart": False, "hh_autostart": False})
    attempted: list[str] = []

    async def recording_start(name: str):
        attempted.append(name)

    monkeypatch.setattr(manager, "start", recording_start)

    assert _boot_and_call_api() == 200
    assert attempted == []
