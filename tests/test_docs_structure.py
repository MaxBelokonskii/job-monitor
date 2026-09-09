"""Три документа описывают дерево проекта — и все три обязаны его описывать.

`структура.txt` пережил удаление `monitor.py`/`hh_monitor.py` (задача 7 плана 3)
и до финальной волны продолжал перечислять оба скрипта в корне, хотя
`tests/test_paths.py::test_standalone_monitor_scripts_are_gone` прямо запрещает
им вернуться: два теста одного репозитория утверждали противоположное.
Раздел 4 спецификации расходился с деревом иначе — он описывал структуру,
которую реализация не приняла (`api/app.py`, `routes_config.py`, …).

Проверяется свойство, а не текст: множество имён модулей в документе и
множество модулей на диске должны совпадать. Поэтому ни новый модуль нельзя
добавить, не упомянув его, ни удалить, забыв вычеркнуть.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("api", "job_monitor")
MODULE_TOKEN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*\.py)\b")


def _actual_modules() -> set[str]:
    """Имена всех модулей пакетов приложения, кроме `__init__.py`."""
    found = {
        item.name
        for package in PACKAGES
        for item in (REPO_ROOT / package).rglob("*.py")
        if "__pycache__" not in item.parts and item.name != "__init__.py"
    }
    assert found, "не найдено ни одного модуля — пакеты переехали?"
    return found


def _existing_python_files() -> set[str]:
    """Все имена `*.py` в дереве проекта — по ним проверяется, что документ не
    упоминает файла, которого нет."""
    return {
        item.name
        for item in REPO_ROOT.rglob("*.py")
        if "__pycache__" not in item.parts and ".venv" not in item.parts
    }


def _tree_block(text: str) -> str:
    """Само дерево из `структура.txt`: до первой пустой строки.

    Проза под деревом намеренно называет удалённые `monitor.py` и
    `hh_monitor.py` — «их больше нет». Это объяснение, а не опись каталога,
    и попадать под проверку «упомянутый файл существует» оно не должно.
    """
    lines: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            break
        lines.append(line)
    assert lines, "дерево в структура.txt не найдено"
    return "\n".join(lines)


def _fenced_block_after(text: str, heading: str) -> str:
    """Первый ```-блок после заголовка."""
    start = text.find(heading)
    assert start != -1, f"заголовок {heading!r} не найден"
    match = re.search(r"```[^\n]*\n(.*?)```", text[start:], re.DOTALL)
    assert match, f"после {heading!r} нет блока кода"
    return match.group(1)


def _structura() -> str:
    return _tree_block((REPO_ROOT / "структура.txt").read_text(encoding="utf-8"))


def _readme() -> str:
    return _fenced_block_after(
        (REPO_ROOT / "README.md").read_text(encoding="utf-8"), "Структура проекта"
    )


def _spec() -> str:
    spec = REPO_ROOT / "docs/superpowers/specs/2026-09-08-hardening-and-rework-spec.md"
    return _fenced_block_after(spec.read_text(encoding="utf-8"), "## 4. ")


DOCUMENTS = {
    "структура.txt": _structura,
    "README.md": _readme,
    "specs/2026-09-08-hardening-and-rework-spec.md": _spec,
}


@pytest.mark.parametrize("label", sorted(DOCUMENTS))
def test_document_lists_every_module_of_the_application(label: str) -> None:
    mentioned = set(MODULE_TOKEN.findall(DOCUMENTS[label]()))
    missing = sorted(_actual_modules() - mentioned)
    assert not missing, (
        f"{label} не упоминает модули, которые есть в дереве: {missing}"
    )


@pytest.mark.parametrize("label", sorted(DOCUMENTS))
def test_document_mentions_no_module_that_does_not_exist(label: str) -> None:
    mentioned = set(MODULE_TOKEN.findall(DOCUMENTS[label]()))
    phantom = sorted(mentioned - _existing_python_files())
    assert not phantom, (
        f"{label} описывает файлы, которых в дереве нет: {phantom}"
    )


def test_the_removed_standalone_scripts_are_not_advertised_anywhere() -> None:
    """Обратная сторона `test_standalone_monitor_scripts_are_gone`: документ
    не должен обещать пользователю скрипт, который удалён и не вернётся."""
    for label, source in DOCUMENTS.items():
        mentioned = set(MODULE_TOKEN.findall(source()))
        assert not mentioned & {"monitor.py", "hh_monitor.py"}, (
            f"{label} перечисляет удалённые standalone-скрипты в описании дерева"
        )


def test_the_structure_check_is_not_vacuous() -> None:
    """Обе половины проверки должны срабатывать: и «забыли упомянуть», и
    «упомянули несуществующее». Иначе документ снова разойдётся с деревом
    молча."""
    real = next(iter(sorted(_actual_modules())))
    assert real not in MODULE_TOKEN.findall("api/\n  прочерк\n"), "токенайзер сломан"
    assert MODULE_TOKEN.findall("  routes_config.py  app.py") == [
        "routes_config.py",
        "app.py",
    ], "имена модулей из документа перестали извлекаться"
    assert {"routes_config.py", "app.py"} - _existing_python_files() == {
        "routes_config.py",
        "app.py",
    }, "проверка «файла нет в дереве» не сработала бы на именах из старой версии спецификации"
    assert real in _actual_modules() and real in _existing_python_files()


# ── README не пересказывает числа, которые называет прогон ────────────

README = REPO_ROOT / "README.md"
COUNTED_TESTS = re.compile(r"\b(\d+)\s+тест", re.IGNORECASE)


def _paragraphs_about_node() -> list[str]:
    paragraphs = [
        paragraph
        for paragraph in README.read_text(encoding="utf-8").split("\n\n")
        if "Node" in paragraph
    ]
    assert paragraphs, "в README нет абзаца про Node.js — раздел про прогон переехал?"
    return paragraphs


def test_the_readme_does_not_restate_how_many_tests_node_skips() -> None:
    """Число пропущенных тестов в README держалось руками и разошлось с
    фактом на первом же добавленном тесте: было написано «14 тестов», а
    прогон без node давал 15.

    Источник у этого числа один и он не в документации:
    `pytest_terminal_summary` в tests/conftest.py считает пропуски с нашей
    причиной и печатает их количество в конце вывода. Дублировать его в
    README — значит завести второе место, которое надо помнить обновлять, и
    оно уже один раз не обновилось.
    """
    offenders = [
        (number, paragraph.strip())
        for paragraph in _paragraphs_about_node()
        for number in COUNTED_TESTS.findall(paragraph)
    ]
    assert not offenders, (
        "README снова называет количество тестов, пропущенных без Node.js: "
        + ", ".join(f"«{number} тест...»" for number, _paragraph in offenders)
        + " — это число печатает сам прогон (tests/conftest.py::"
        "pytest_terminal_summary), и руками оно уже расходилось с фактом"
    )


def test_the_run_still_reports_that_number_itself() -> None:
    """Обратная сторона: убрать число из README можно только потому, что его
    называет прогон. Если предупреждение перестанет считать пропуски, README
    останется единственным местом — и там будет пусто."""
    import conftest

    assert "{len(skipped)}" in inspect.getsource(conftest.pytest_terminal_summary), (
        "предупреждение о ненайденном Node.js больше не называет количество "
        "пропущенных тестов — тогда его надо вернуть в README"
    )
