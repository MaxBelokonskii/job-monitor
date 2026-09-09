"""`start_web.bat` — единственная документированная точка входа на Windows.

Исполнить его в этом прогоне нельзя (нет cmd.exe), поэтому проверяются
структурные свойства текста. Это слабее исполнения, но не бесполезно: каждое
свойство здесь — уже случившийся отказ, а не гипотеза.

**Отметка об установке вместо интерпретатора.** Скрипт считал окружение
готовым по существованию `.venv\\Scripts\\python.exe`, который появляется
сразу после `python -m venv`, то есть ДО `pip install`. Упавшая установка
(обрыв сети; пин без маркера платформы — см. tests/test_dependencies.py)
оставляла мёртвое окружение: первый запуск честно печатал ошибку, второй
считал установку выполненной, пропускал её целиком и падал на
`No module named uvicorn` уже без единого объяснения. Состояние не
самовосстанавливалось.

**Неизвестный аргумент.** `start_web.bat stup` молча трактовался как обычный
запуск: `if /i "%~1"=="setup"` не совпадало, и опечатка в единственной
поддерживаемой команде выглядела как успех.

**Код возврата uvicorn.** Не проверялся: занятый порт 8000 и ошибка импорта
выглядели ровно как штатный выход по Ctrl+C.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "start_web.bat"
GITATTRIBUTES = REPO_ROOT / ".gitattributes"


def _text() -> str:
    source = SCRIPT.read_text(encoding="utf-8")
    assert source.strip(), "start_web.bat пуст"
    return source


def _code_lines() -> list[str]:
    """Строки без комментариев `REM` и без пустых."""
    lines = []
    for line in _text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.upper().startswith("REM "):
            continue
        lines.append(stripped)
    return lines


def _index_of(pattern: str) -> int:
    lines = _code_lines()
    for number, line in enumerate(lines):
        if re.search(pattern, line, re.IGNORECASE):
            return number
    raise AssertionError(f"в start_web.bat нет строки по образцу {pattern!r}")


def _indexes_of(pattern: str) -> list[int]:
    return [
        number
        for number, line in enumerate(_code_lines())
        if re.search(pattern, line, re.IGNORECASE)
    ]


def test_install_is_gated_on_a_success_stamp_not_on_the_interpreter() -> None:
    lines = _code_lines()
    stamp = [line for line in lines if "STAMP" in line]
    assert stamp, "нет отметки об успешной установке — окружение снова определяется по python.exe"

    # Условие «нужна установка» смотрит на отметку, а не на интерпретатор.
    gates = [line for line in lines if re.match(r"if not exist ", line, re.IGNORECASE)
             and "SETUP=1" in line]
    assert gates, "не найдено условие, включающее установку"
    for gate in gates:
        assert "%STAMP%" in gate, (
            f"установка включается по {gate!r} — интерпретатор появляется до pip install, "
            "и упавшая установка второй раз пропускается молча"
        )


def test_the_stamp_is_written_only_after_both_installs_succeed() -> None:
    installs = _indexes_of(r"pip install")
    assert len(installs) == 2, f"ожидались два pip install, найдено {len(installs)}"
    checks = _indexes_of(r"if errorlevel 1 goto failed")
    write = _index_of(r">\s*\"%STAMP%\"")

    for install in installs:
        assert any(install < check < write for check in checks), (
            "после каждого pip install должна идти проверка errorlevel, и только "
            "потом — запись отметки"
        )
    assert write > max(installs), "отметка пишется раньше, чем заканчивается установка"

    clear = _index_of(r"del /q \"%STAMP%\"")
    assert clear < min(installs), (
        "отметка должна сниматься ДО установки: прерванная попытка не имеет права "
        "оставить в силе отметку от прошлой удачной"
    )


def test_an_unknown_argument_is_an_error() -> None:
    lines = _code_lines()
    assert any(re.search(r"exit /b [2-9]", line) for line in lines), (
        "неизвестный аргумент по-прежнему трактуется как обычный запуск: у скрипта "
        "нет ни одного выхода с кодом, отличным от 0 и 1"
    )
    assert any("неизвестный аргумент" in line for line in lines), (
        "нет сообщения про неизвестный аргумент — опечатка в `setup` останется незамеченной"
    )


def test_the_server_exit_code_is_checked() -> None:
    server = _index_of(r"-m uvicorn")
    checks = _indexes_of(r"if errorlevel 1")
    assert any(check > server for check in checks), (
        "код возврата uvicorn не проверяется: занятый порт и ошибка импорта выглядят "
        "как штатный выход по Ctrl+C"
    )


def test_the_failure_hint_does_not_blame_the_network_alone() -> None:
    """Подсказка `:failed` указывала ровно одну причину — сеть, — и именно
    поэтому регрессия с `uvloop` (пин без маркера платформы) выглядела как
    проблема у пользователя, а не в репозитории."""
    text = _text().lower()
    assert text.count("[ошибка]") >= 2, "нет отдельного сообщения для отказа сервера"
    hint = text[text.index(":failed"):]
    assert "python" in hint, "подсказка не предлагает проверить сам Python"


# ── Батник обязан выгружаться с CRLF ──────────────────────────────────
#
# cmd.exe разбирает файл построчно, на ходу, и многострочный `if (...)` с
# `goto` изнутри блока — самая хрупкая к LF конструкция интерпретатора. В
# `start_web.bat` таких блоков два, и оба появились в этой ветке.
#
# Сам файл лежит в репозитории с LF (ноль CR), и клонирующего это не
# задевает: Git for Windows по умолчанию ставит `core.autocrlf=true`. Ровно
# поэтому дефект и не всплывал — маскирует его настройка на стороне
# пользователя, которой у скачавшего ZIP с форджа нет вовсе.


def test_the_repository_declares_crlf_for_batch_files() -> None:
    assert GITATTRIBUTES.exists(), (
        ".gitattributes нет: батник выгружается с теми переводами строк, что лежат "
        "в репозитории (LF), и на Windows это ломает многострочные блоки cmd.exe"
    )
    rules = [
        line.strip()
        for line in GITATTRIBUTES.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    bat = [rule for rule in rules if rule.split()[0] in ("*.bat", "start_web.bat")]
    assert bat, f"в .gitattributes нет правила для .bat: {rules}"
    assert any("eol=crlf" in rule for rule in bat), (
        f"правило для .bat есть, но CRLF оно не требует: {bat}"
    )


def test_git_agrees_that_the_batch_file_gets_crlf() -> None:
    """Проверка не текста правила, а его действия: маску можно написать так,
    что она не совпадёт с самим файлом (`*.BAT`, `bat/*`)."""
    if not (REPO_ROOT / ".git").exists() or shutil.which("git") is None:
        pytest.skip("нет git-репозитория — правило проверено только по тексту файла")
    result = subprocess.run(
        ["git", "check-attr", "eol", "--", SCRIPT.name],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "eol: crlf" in result.stdout, (
        f"git считает переводы строк {SCRIPT.name} неопределёнными: {result.stdout.strip()}"
    )


def test_the_repository_itself_stays_on_lf() -> None:
    """`eol=crlf` — свойство ВЫГРУЗКИ. Внутри репозитория текст остаётся с
    LF, иначе `.gitattributes` перенормализовал бы всё дерево, и диф этой
    правки был бы размером с репозиторий."""
    if not (REPO_ROOT / ".git").exists() or shutil.which("git") is None:
        pytest.skip("нет git-репозитория")
    blob = subprocess.run(
        ["git", "show", f"HEAD:{SCRIPT.name}"],
        cwd=REPO_ROOT, capture_output=True,
    )
    assert blob.returncode == 0, blob.stderr
    assert b"\r" not in blob.stdout, "в репозитории батник уже лежит с CRLF"
