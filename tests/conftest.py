import os
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Секреты Telegram. `job_monitor/settings.py::load_secrets` предпочитает
# os.getenv временному `.env`, поэтому переменные окружения разработчика
# видны тестам насквозь, каким бы ни был JOB_MONITOR_DATA_DIR.
TELEGRAM_SECRET_VARS = ("TG_API_ID", "TG_API_HASH")


@pytest.fixture(scope="session", autouse=True)
def _no_real_telegram_credentials():
    """Ни один тест не должен видеть настоящие ключи Telegram.

    SECURITY.md и решение D5 предписывают держать `TG_API_ID`/`TG_API_HASH`
    в окружении. У разработчика, который так и сделал, они видны и pytest:
    достаточно одного теста, который входит в настоящий `lifespan` с
    включённым `tg_autostart`, чтобы `run_tg_worker()` собрал живой
    `TelegramClient` и **соединился с Telegram** его реальным `api_id` —
    проверено инструментированием Telethon. Исключение при этом глотает
    `WorkerManager._supervise`, так что suite остаётся зелёным и в выводе
    нет ничего.

    Снимать переменные в каждой фикстуре по отдельности (как делают
    test_config_api, test_auth_routes, test_telegram_client) — это защита,
    которую следующий новый тест-файл забудет применить: ровно так дефект и
    возник в test_lifespan_autostart.py. Здесь она одна на весь прогон, а
    tests/test_secrets_handling.py сторожит, что она работает.
    """
    saved = {name: os.environ.pop(name, None) for name in TELEGRAM_SECRET_VARS}
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value


@pytest.fixture(scope="session", autouse=True)
def _job_monitor_data_dir(tmp_path_factory, _no_real_telegram_credentials):
    """Keep the test suite hermetic.

    Importing api.main (which the client fixture below does) reaches
    job_monitor.paths, and GET /api/config reads config.json and .env from
    the data directory — without this fixture that would be the developer's
    real ~/.job-monitor, so a corrupt personal .env could turn a passing
    test into a 500. Logging is the other reason: api/main.py's lifespan
    calls configure_logging(), which opens logs/tg_system.log and
    logs/hh.log for writing; any test that enters TestClient as a context
    manager must land those files in a temporary directory, never in the
    developer's home. Being session-scoped and autouse, this runs before any
    test — including ones that import application modules without using the
    client/raw_client fixtures at all (see test_smoke.py).
    """
    data_dir = tmp_path_factory.mktemp("job-monitor-data")
    os.environ["JOB_MONITOR_DATA_DIR"] = str(data_dir)
    yield data_dir


@pytest.fixture(scope="session", autouse=True)
def _no_state_in_the_real_home():
    """Прогон не создаёт настоящий `~/.job-monitor`.

    `JOB_MONITOR_DATA_DIR` уводит состояние во временный каталог, но
    `paths.DEFAULT_DIR` вычисляется от настоящего `Path.home()` на импорте
    модуля, и любой тест, который снимает переменную (`test_paths.py`), снова
    попадает в домашний каталог разработчика — причём `paths.path()` делает
    по дороге `mkdir`. Проверено `HOME=<tmp> pytest -q`.

    Сторож честно ограничен: если каталог у разработчика уже есть (обычное
    дело — он им пользуется), проверить нечего. На чистой машине и в CI он
    работает; тот же инвариант с другой стороны закрывает
    `test_importing_the_app_creates_nothing_on_disk`.
    """
    from job_monitor import paths

    existed = paths.DEFAULT_DIR.exists()
    yield
    if not existed:
        assert not paths.DEFAULT_DIR.exists(), (
            f"прогон создал {paths.DEFAULT_DIR} в настоящем домашнем каталоге —"
            " тесты должны жить только в JOB_MONITOR_DATA_DIR"
        )


@pytest.fixture
def client(_job_monitor_data_dir) -> TestClient:
    from api.main import app
    from job_monitor.security import APP_TOKEN, TOKEN_HEADER

    return TestClient(
        app,
        base_url="http://127.0.0.1:8000",
        headers={TOKEN_HEADER: APP_TOKEN},
    )


@pytest.fixture
def raw_client(_job_monitor_data_dir) -> TestClient:
    from api.main import app

    return TestClient(app, base_url="http://127.0.0.1:8000")


# ── Тесты, которым нужен Node.js ──────────────────────────────────────
#
# `frontend/app.js` — не собираемый скрипт, и единственный способ проверить
# его ПОВЕДЕНИЕ (а не наличие подстроки) — исполнить настоящую функцию в
# node. Без node такие тесты пропускаются, и это молчаливое сужение защиты:
# среди них поведенческие пины S5/S6 (экранирование недоверенного текста из
# Telegram и hh.ru, запрет `javascript:`-URL) — на машине без node от них
# остаётся только grep по исходнику. Причина пропуска поэтому говорит, что
# именно не проверено, а `pytest_terminal_summary` ниже не даёт этому
# потеряться в общем «14 skipped».

NODE_AVAILABLE = shutil.which("node") is not None
NODE_SKIP_REASON = (
    "Node.js не найден: поведение frontend/app.js не проверено "
    "(экранирование недоверенного текста, запрет javascript:-URL, "
    "разбор ответов API). Установите node для полного прогона."
)
requires_node = pytest.mark.skipif(not NODE_AVAILABLE, reason=NODE_SKIP_REASON)


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    """Пропуск из-за отсутствия Node не должен выглядеть как «всё зелено»."""
    if NODE_AVAILABLE:
        return
    skipped = [
        report
        for report in terminalreporter.stats.get("skipped", [])
        if NODE_SKIP_REASON in str(getattr(report, "longrepr", ""))
    ]
    if not skipped:
        return
    terminalreporter.write_sep("=", "Node.js не найден", yellow=True, bold=True)
    terminalreporter.write_line(
        f"{len(skipped)} тест(ов) поведения frontend/app.js пропущено. Среди них "
        "поведенческие пины XSS и CSP (S5/S6): без node защита фронтенда держится "
        "только грепом по исходнику. Полный прогон требует Node.js — см. README."
    )
