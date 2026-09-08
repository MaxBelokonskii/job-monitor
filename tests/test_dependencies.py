"""Каждая объявленная runtime-зависимость должна быть кем-то импортирована.

`python-dotenv` числилась в `[project].dependencies` и была запиннута в
`requirements.lock`, хотя `grep dotenv` по исходникам не давал ни одного
попадания: настройки живут в SQLite, а `.env` читает и пишет
`job_monitor/envfile.py` вручную. Лишняя строка в зависимостях — это лишний
пакет в окружении пользователя и лишняя поверхность обновлений.

Проверяется свойство, а не список: имя каждой зависимости из `pyproject.toml`
должно встречаться в `import`/`from` хотя бы одного исходника приложения.

Обратной проверки («всё импортируемое объявлено») здесь нет намеренно: она
требует полного отображения «имя дистрибутива → имена модулей», то есть
установленного окружения, и в оффлайне разошлась бы с реальностью. Транзитивные
зависимости под этот тест тоже не попадают: `python-dotenv` остаётся в
`requirements.lock`, потому что её тянет `uvicorn[standard]`.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("api", "job_monitor", "scripts")
IMPORT = re.compile(r"^[ \t]*(?:from|import)[ \t]+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)

# Имя дистрибутива на PyPI не обязано совпадать с именем импортируемого пакета.
DISTRIBUTION_TO_MODULE = {
    "telethon": "telethon",
    "python-dotenv": "dotenv",
    "uvicorn": "uvicorn",
}


def _declared_dependencies() -> list[str]:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names = []
    for requirement in data["project"]["dependencies"]:
        name = re.split(r"[\[<>=!;~ ]", requirement, maxsplit=1)[0].strip()
        assert name, f"не разобрано требование {requirement!r}"
        names.append(name)
    assert names, "в pyproject.toml не объявлено ни одной зависимости"
    return names


def _imported_modules() -> set[str]:
    found: set[str] = set()
    for directory in SOURCE_DIRS:
        for source in (REPO_ROOT / directory).rglob("*.py"):
            if "__pycache__" in source.parts:
                continue
            found.update(IMPORT.findall(source.read_text(encoding="utf-8")))
    assert found, "не найдено ни одного импорта — исходники переехали?"
    return found


@pytest.mark.parametrize("distribution", _declared_dependencies())
def test_every_declared_dependency_is_actually_imported(distribution: str) -> None:
    module = DISTRIBUTION_TO_MODULE.get(distribution, distribution.replace("-", "_"))
    assert module in _imported_modules(), (
        f"зависимость {distribution!r} объявлена в pyproject.toml, но модуль "
        f"{module!r} не импортирован ни в одном из {SOURCE_DIRS} — либо она не нужна, "
        "либо её забыли начать использовать"
    )


def test_the_import_scan_is_not_vacuous() -> None:
    """Сканер обязан видеть все формы, которыми пользуется проект: импорт
    верхнего уровня, `from x import y` и импорт внутри функции (так
    `job_monitor/workers/telegram.py` импортирует telethon)."""
    assert IMPORT.findall("import telethon\n") == ["telethon"]
    assert IMPORT.findall("from telethon import TelegramClient\n") == ["telethon"]
    assert IMPORT.findall("def f():\n    from telethon import events\n") == ["telethon"]
    assert not IMPORT.findall("# import dotenv\nx = 'import dotenv'\n".splitlines()[1])
    assert "dotenv" not in _imported_modules(), (
        "модуль dotenv снова импортируется — верните python-dotenv в pyproject.toml"
    )


# ── requirements.lock должен ставиться и на Windows ────────────────────
#
# `start_web.bat` (единственная документированная точка входа на Windows)
# делает `pip install -r requirements.lock` и проверяет errorlevel. Один пин
# без маркера окружения, у которого нет колеса под Windows, роняет установку
# ЦЕЛИКОМ: не ставится ничего, приложение не стартует никогда, а подсказка в
# `:failed` списывает это на сеть. Так и вышло с `uvloop==0.22.1` — лок снят
# на macOS, где маркеры не нужны, и потерял тот, что объявляет сам uvicorn.
#
# Проверяется свойство, а не список имён: если КАКОЙ-НИБУДЬ установленный
# дистрибутив объявляет требование с маркером, исключающим win32, то строка
# лока на тот же дистрибутив обязана нести такой же запрет. Источник истины —
# метаданные апстрима в окружении, а не наше мнение о платформах, и проверка
# работает оффлайн.

LOCK = REPO_ROOT / "requirements.lock"


def _lock_requirements() -> dict[str, "Requirement"]:
    from packaging.requirements import Requirement

    found: dict[str, Requirement] = {}
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        requirement = Requirement(line)
        found[requirement.name.lower().replace("_", "-")] = requirement
    assert found, "requirements.lock пуст — файл переехал?"
    return found


def _upstream_windows_exclusions() -> dict[str, list[str]]:
    """{дистрибутив: [требования апстрима, которые исключают win32]}."""
    import importlib.metadata as metadata

    from packaging.requirements import Requirement

    excluded: dict[str, list[str]] = {}
    for distribution in metadata.distributions():
        # `extra == 'standard'` — часть того же маркера, что и запрет
        # платформы (`uvicorn[standard]` тянет uvloop именно так), поэтому
        # маркер проверяется при каждом объявленном экстре: нас интересует
        # платформенная часть, а не то, под каким экстрой она живёт.
        extras = [""] + list(distribution.metadata.get_all("Provides-Extra") or [])
        for raw in distribution.metadata.get_all("Requires-Dist") or []:
            try:
                requirement = Requirement(raw)
            except Exception:                     # noqa: BLE001 — чужие метаданные
                continue
            if requirement.marker is None:
                continue
            if not _excludes_windows(requirement.marker, extras):
                continue
            name = requirement.name.lower().replace("_", "-")
            excluded.setdefault(name, []).append(f"{distribution.metadata['Name']}: {raw}")
    return excluded


def _excludes_windows(marker, extras: list[str]) -> bool:
    """Маркер требуется на не-Windows и не требуется на Windows."""
    for extra in extras:
        base = {"platform_python_implementation": "CPython", "extra": extra}
        try:
            on_windows = marker.evaluate({**base, "sys_platform": "win32",
                                          "platform_system": "Windows"})
            on_linux = marker.evaluate({**base, "sys_platform": "linux",
                                        "platform_system": "Linux"})
        except Exception:                         # noqa: BLE001 — неполный контекст
            continue
        if on_linux and not on_windows:
            return True
    return False


def test_lock_keeps_the_platform_markers_upstream_declares() -> None:
    pinned = _lock_requirements()
    upstream = _upstream_windows_exclusions()
    offenders = []
    for name, sources in sorted(upstream.items()):
        requirement = pinned.get(name)
        if requirement is None:
            continue                              # не в локе — ставить нечего
        marker = requirement.marker
        installs_on_windows = marker is None or marker.evaluate(
            {"sys_platform": "win32", "platform_system": "Windows",
             "platform_python_implementation": "CPython", "extra": ""}
        )
        if installs_on_windows:  # маркера нет или он Windows не отсекает
            offenders.append(f"{requirement} (апстрим: {'; '.join(sources)})")
    assert not offenders, (
        "в requirements.lock есть пин без маркера платформы, который апстрим "
        "объявляет неустановимым на Windows. `pip install -r requirements.lock` "
        "в start_web.bat упадёт целиком, и Windows-запуск перестанет работать:\n"
        + "\n".join(offenders)
    )


def test_the_marker_check_is_not_vacuous() -> None:
    """Инвариант выше стоит ровно столько, сколько стоит его источник:
    если в окружении не окажется ни одного апстрим-требования с
    Windows-исключением, проверка станет пустой и промолчит навсегда."""
    upstream = _upstream_windows_exclusions()
    assert "uvloop" in upstream, (
        "uvicorn больше не объявляет uvloop как `sys_platform != 'win32'` — "
        "проверка маркеров осталась без источника истины, перепроверьте её вручную"
    )
    assert "uvloop" in _lock_requirements(), "uvloop пропал из лока"
