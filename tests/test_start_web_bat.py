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
        # И `REM текст`, и одинокий `REM` — разделитель абзаца в комментарии.
        if not stripped or stripped.upper() == "REM" or stripped.upper().startswith("REM "):
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


# ── Что батник делает с кодом возврата uvicorn ────────────────────────
#
# Первая версия проверки была `if errorlevel 1`, то есть «код >= 1». Падение
# по access violation (0xC0000005 — например в драйвере Chrome) даёт
# errorlevel = -1073741819, условие ложно, и выполнялся `pause` из ветки
# успеха: аварийное завершение выглядело нормальным. Занятый порт и
# ModuleNotFoundError дают ровно 1 и ловились — это был остаток, а не полный
# отказ проверки.
#
# Ниже проверяется ПОВЕДЕНИЕ: куда уйдёт управление при конкретном коде. Для
# этого хвост батника после запуска uvicorn прогоняется крошечным
# интерпретатором, понимающим ровно четыре конструкции; всё остальное он
# отвергает с внятным сообщением, а не молча трактует по-своему.

SET_FROM_ERRORLEVEL = re.compile(r'^set\s+"?(\w+)=%ERRORLEVEL%"?$', re.IGNORECASE)
IF_STRING_EQUALS = re.compile(
    r'^if\s+(not\s+)?"%(\w+)%"\s*==\s*"(-?\d+)"\s+goto\s+(\w+)$', re.IGNORECASE
)
IF_ERRORLEVEL = re.compile(r"^if\s+(not\s+)?errorlevel\s+(-?\d+)\s+goto\s+(\w+)$", re.IGNORECASE)
GOTO = re.compile(r"^goto\s+(\w+)$", re.IGNORECASE)
EXIT_CODE = re.compile(r"^exit\s+/b\s+(-?\d+)$", re.IGNORECASE)
IGNORED = re.compile(r"^(echo|pause|title|chcp|start|set\s|setlocal|cd\s)", re.IGNORECASE)

# 0xC000013A. Единственный код, который может прийти от штатного закрытия
# приложения: Ctrl+C убил python жёстко, а на «Terminate batch job (Y/N)»
# пользователь ответил «N», так что батник дожил до проверки.
CTRL_C_EXIT = -1073741510
CRASH_CODES = {
    "0xC0000005 access violation (например в драйвере Chrome)": -1073741819,
    "0xC0000374 heap corruption": -1073740940,
    "0xC000041D unhandled exception in callback": -1073741283,
}


def _batch_exit_code_for(server_return_code: int) -> int:
    """Код, с которым завершится батник, если uvicorn вернул этот код."""
    lines = _code_lines()
    labels = {
        line[1:].lower(): number
        for number, line in enumerate(lines)
        if line.startswith(":")
    }
    variables: dict[str, int] = {}
    index = _index_of(r"-m uvicorn") + 1
    for _step in range(len(lines) * 2):
        assert index < len(lines), "хвост батника кончился без `exit /b`"
        line = lines[index]
        index += 1

        if line.startswith(":"):
            continue                                  # метка — просто отметка в тексте

        jump = None
        captured = SET_FROM_ERRORLEVEL.match(line)
        equals = IF_STRING_EQUALS.match(line)
        threshold = IF_ERRORLEVEL.match(line)
        goto = GOTO.match(line)
        finish = EXIT_CODE.match(line)
        if captured:
            variables[captured.group(1)] = server_return_code
        elif equals:
            negated, name, expected, target = equals.groups()
            assert name in variables, f"сравнение с необъявленной переменной: {line!r}"
            matched = variables[name] == int(expected)
            jump = target if matched != bool(negated) else None
        elif threshold:
            negated, level, target = threshold.groups()
            # Именно в этом семантика `if errorlevel N`: «код >= N».
            matched = server_return_code >= int(level)
            jump = target if matched != bool(negated) else None
        elif goto:
            jump = goto.group(1)
        elif finish:
            return int(finish.group(1))
        else:
            assert IGNORED.match(line), (
                f"строка {line!r} записана формой, которую этот тест не понимает — "
                "обновите интерпретатор в tests/test_start_web_bat.py, иначе проверка "
                "кода возврата держится на догадке"
            )

        if jump is not None:
            assert jump.lower() in labels, f"переход на несуществующую метку: {line!r}"
            index = labels[jump.lower()] + 1
    raise AssertionError("разбор хвоста батника зациклился")


def test_a_clean_shutdown_is_not_reported_as_a_failure() -> None:
    assert _batch_exit_code_for(0) == 0, "штатное завершение uvicorn считается ошибкой"
    assert _batch_exit_code_for(CTRL_C_EXIT) == 0, (
        "жёсткое убийство по Ctrl+C (0xC000013A) — это закрытие приложения "
        "пользователем, а не отказ сервера"
    )


@pytest.mark.parametrize("reason, code", sorted(CRASH_CODES.items()))
def test_a_negative_exit_code_is_reported_as_a_failure(reason: str, code: int) -> None:
    """`if errorlevel 1` означает «код >= 1» и ни один из этих кодов не
    ловит: после падения выполнялся `pause` из ветки успеха."""
    assert _batch_exit_code_for(code) != 0, f"{reason} ({code}) выглядит как штатный выход"


@pytest.mark.parametrize("code", [1, 2, 3, 8, 3221225477])
def test_a_positive_exit_code_is_still_reported_as_a_failure(code: int) -> None:
    """То, ради чего проверка заводилась (занятый порт 8000 и
    `ModuleNotFoundError` дают 1), не должно потеряться при переписывании."""
    assert _batch_exit_code_for(code) != 0


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
