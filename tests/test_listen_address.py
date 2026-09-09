"""«Слушаем только 127.0.0.1» — центральная посылка модели угроз (D1).

На ней держится всё остальное: `TrustedHostMiddleware` защищает от DNS
rebinding, а не от прямого запроса (`Host: 127.0.0.1` подделывается
тривиально), а `APP_TOKEN` печатается в HTML главной страницы. То есть
приложение с живой сессией Telegram и cookies hh.ru рассчитывает на то, что
до сокета вообще нельзя дотянуться извне машины.

Мутационный аудит нашёл, что этот инвариант объявлен в коде и в SECURITY.md,
но не закреплён нигде: `0.0.0.0` проходил зелёным во всех четырёх точках
запуска, а у `job_monitor/cli.py`, где лежит ЕДИНСТВЕННЫЙ настоящий гард,
выжили 6 мутантов из 6 — на модуль не было ни одного теста, и три строки
отказа можно было удалить целиком.

Гард здесь проверяется поведенчески (вызовом `main()`), а остальные точки
запуска — по тексту: `Makefile` и `start_web.bat` исполнить в этом прогоне
нечем, а `api/main.py::__main__` не исполним по построению.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from job_monitor import cli

REPO_ROOT = Path(__file__).resolve().parents[1]
LOOPBACK = "127.0.0.1"

# Адреса, каждый из которых означает «доступно из локальной сети»:
# IPv4-любой, IPv6-любой, конкретный адрес в Wi-Fi-подсети и имя, которое
# резолвится в loopback, но гардом не разрешено (см. докстринг теста ниже).
NON_LOOPBACK_HOSTS = ["0.0.0.0", "::", "192.168.1.5", "10.0.0.7", "localhost", ""]


# ── Гард в CLI: проверяется вызовом, а не грепом ──────────────────────


@pytest.mark.parametrize("host", NON_LOOPBACK_HOSTS)
def test_the_cli_refuses_to_run_on_anything_but_loopback(host, capsys, monkeypatch):
    """`localhost` в списке — не придирка.

    Имя резолвится и в `127.0.0.1`, и в `::1`, а в файле hosts его можно
    указать на что угодно; гард поэтому сравнивает со строкой адреса, и
    ослабление до «а ещё разрешим localhost» — это уже другой инвариант.
    Пустая строка — то, что uvicorn понимает как «все интерфейсы».
    """
    import uvicorn

    started: list[dict] = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: started.append(kwargs))

    assert cli.main(["run", "--host", host]) == 2, (
        f"CLI согласился слушать {host!r} — приложение становится доступно "
        "из локальной сети"
    )
    assert not started, f"uvicorn всё-таки запущен на {host!r}: {started}"
    assert "127.0.0.1" in capsys.readouterr().err, (
        "отказ должен объяснять причину: без сообщения код 2 выглядит как поломка"
    )


def test_the_cli_runs_on_loopback():
    """Обратная сторона: гард не должен отказывать всегда — иначе `make run`
    и `start_web.bat` перестали бы работать, а тесты выше остались бы
    зелёными (мутация «инвертировать `!=` на `==`» именно так и выглядит).
    """
    import uvicorn

    started: list[dict] = []

    def fake_run(app, **kwargs):
        started.append({"app": app, **kwargs})

    original = uvicorn.run
    uvicorn.run = fake_run
    try:
        assert cli.main(["run", "--host", LOOPBACK]) == 0
    finally:
        uvicorn.run = original

    assert len(started) == 1, "uvicorn не запущен на разрешённом адресе"
    assert started[0]["host"] == LOOPBACK
    assert started[0]["app"] == "api.main:app"
    assert started[0]["reload"] is False, (
        "reload=True поднимает наблюдателя за файлами и перезапускает процесс — "
        "не то, что нужно пользователю локального инструмента"
    )


def test_the_default_host_is_loopback():
    """Пользователь запускает `job-monitor run` без аргументов чаще, чем с
    ними, поэтому значение по умолчанию — это и есть действующий адрес."""
    import uvicorn

    started: list[dict] = []
    original = uvicorn.run
    uvicorn.run = lambda app, **kwargs: started.append(kwargs)
    try:
        assert cli.main(["run"]) == 0
    finally:
        uvicorn.run = original

    assert started and started[0]["host"] == LOOPBACK, (
        f"по умолчанию CLI слушает {started[0]['host'] if started else '<не запущен>'!r}"
    )
    assert started[0]["port"] == 8000


# ── Остальные три точки запуска ───────────────────────────────────────


def test_api_main_binds_uvicorn_to_loopback():
    """`python -m api.main` — документированная точка запуска, и её ветку
    `__main__` нельзя исполнить из теста, не поднимая сервер. Читается
    поэтому дерево разбора, а не текст: `host="0.0.0.0"` должен быть виден
    и в том случае, если строку переформатируют."""
    source = (REPO_ROOT / "api" / "main.py").read_text(encoding="utf-8")
    calls = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "uvicorn"
    ]
    assert len(calls) == 1, f"ожидался ровно один uvicorn.run в api/main.py, найдено {len(calls)}"
    hosts = [
        keyword.value.value
        for keyword in calls[0].keywords
        if keyword.arg == "host" and isinstance(keyword.value, ast.Constant)
    ]
    assert hosts == [LOOPBACK], (
        f"api/main.py поднимает uvicorn на {hosts!r} — ожидался ровно [{LOOPBACK!r}]"
    )


def test_the_makefile_run_target_passes_loopback():
    lines = (REPO_ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
    run_lines = [line for line in lines if "uvicorn" in line]
    assert run_lines, "в Makefile нет цели, поднимающей uvicorn"
    for line in run_lines:
        assert f"--host {LOOPBACK}" in line, (
            f"цель Makefile поднимает сервер без явного loopback: {line.strip()!r}"
        )


def test_start_web_bat_passes_loopback():
    lines = (REPO_ROOT / "start_web.bat").read_text(encoding="utf-8").splitlines()
    run_lines = [line for line in lines if "-m uvicorn" in line]
    assert run_lines, "в start_web.bat нет запуска uvicorn"
    for line in run_lines:
        assert f"--host {LOOPBACK}" in line, (
            f"батник поднимает сервер без явного loopback: {line.strip()!r}"
        )


# ── И ни одной точки запуска, о которой мы не знаем ───────────────────

# Всё, что может оказаться командой запуска сервера. `.md` не читается: в
# SECURITY.md `0.0.0.0` упомянут как раз в списке того, чего делать нельзя.
LAUNCH_FILES = ("Makefile", "start_web.bat", "api/main.py", "job_monitor/cli.py",
                "pyproject.toml")
ANY_INTERFACE = re.compile(r"\b(0\.0\.0\.0|\[::\]|\*:\d)")


@pytest.mark.parametrize("relative", LAUNCH_FILES)
def test_no_launch_point_mentions_an_any_interface_address(relative):
    """Отдельная проверка от точных: адрес может появиться не в той строке,
    которую разбирают тесты выше — например вторым запуском в батнике или
    в новой цели Makefile."""
    target = REPO_ROOT / relative
    assert target.exists(), f"{relative} исчез — точки запуска переехали, обновите список"
    text = target.read_text(encoding="utf-8")
    lines = [
        line for line in text.splitlines()
        if ANY_INTERFACE.search(line)
        # Строка про то, чего делать нельзя, — не команда запуска. В
        # `job_monitor/cli.py` такой строки нет и быть не должно, но
        # исключение общее: сообщение об отказе имеет право назвать адрес.
        and "отказ" not in line.lower()
    ]
    assert not lines, (
        f"{relative} упоминает адрес «все интерфейсы»: {lines} — приложение слушает "
        f"только {LOOPBACK}, см. SECURITY.md (D1)"
    )
