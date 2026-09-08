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
