"""Атомарная, безопасная работа с .env: без newline-инъекций, права 0600."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from job_monitor import paths

logger = logging.getLogger(__name__)

FORBIDDEN = ("\n", "\r", "\x00")

# Ключи, которые приложение когда-то само писало в `.env` и больше не пишет.
# Читателя у них нет и не было: `.env` читает только
# `job_monitor/settings.py::load_secrets`, и берёт он оттуда ровно TG_API_ID и
# TG_API_HASH, а прикладные настройки живут в SQLite. Опасна не сама мёртвая
# строка, а её вид: пользователь открывает `~/.job-monitor/.env`, видит
# `SAFE_MODE=false`, правит на `true`, перезапускает приложение и считает себя
# в безопасном режиме, пока воркер продолжает писать людям.
#
# Удаляются именно эти три, а не «все незнакомые ключи»: `.env` лежит в
# каталоге пользователя, и приложение убирает за собой, а не наводит порядок в
# чужом файле. Пользователь мог дописать туда что-то осмысленное для себя —
# `JOB_MONITOR_DATA_DIR`, переменную для прокси, — и молча стереть это было бы
# ровно тем сюрпризом, от которого мы его и защищаем.
RETIRED_KEYS = ("SAFE_MODE", "PARSE_HISTORY", "HISTORY_LIMIT")

# Структурно битые строки, о которых уже сказано. `read_env()` зовётся из
# `load_secrets()`, а его дёргает `GET /api/state` — то есть раз в три секунды
# на каждый открытый дашборд. Без этого одна битая строка дописывала бы
# предупреждение в оба файла логов при каждом опросе, а файлы логов отдаются в
# UI. Ключ множества — сама строка, поэтому исправленный файл перестаёт
# ругаться, а новая битая строка снова будет названа.
_REPORTED_BAD_LINES: set[str] = set()


def _validate(key: str, value: str) -> None:
    if not key or "=" in key or any(bad in key for bad in FORBIDDEN):
        raise ValueError(f"недопустимое имя переменной: {key!r}")
    if any(bad in value for bad in FORBIDDEN):
        raise ValueError(f"значение {key} содержит перевод строки")


def _key_of(line: str) -> str | None:
    """Имя переменной, которую задаёт строка. None — не задаёт никакой.

    Одна и та же разметка строк нужна и `read_env()`, и `write_env()`:
    первый решает, что прочитать, второй — что переписать на месте, а что
    оставить как есть. Расхождение между ними означало бы, что запись портит
    то, чего чтение не видит.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key = stripped.split("=", 1)[0].strip()
    return key or None


def _report_broken(line: str, number: int) -> None:
    if line in _REPORTED_BAD_LINES:
        return
    _REPORTED_BAD_LINES.add(line)
    # В сообщении только номер строки: в самой строке может лежать секрет
    # (`.env` для этого и существует), а логи отдаются в UI через
    # `GET /api/{tg,hh}/logs`.
    logger.warning(
        ".env: строка %d не задаёт переменной (нет имени или нет `=`) и "
        "пропущена — проверьте файл",
        number,
    )


def read_env() -> dict[str, str]:
    target = paths.env_file()
    if not target.exists():
        return {}
    result: dict[str, str] = {}
    for number, raw in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if _key_of(line) is None:
            # Строка, поправленная руками: `TG_API_ID` без `=`, `=значение`
            # без имени. Вторая форма доводила `_validate("")` до ValueError,
            # `read_env()` бросал, и PATCH /api/config отвечал 500 — то есть
            # пользователь не мог сохранить НИЧЕГО, пока не починит файл
            # руками, и узнавал об этом из «ошибка сервера». Такую строку
            # правильно пропустить, сказав о ней вслух.
            #
            # Запрещённые символы (`\n`, `\r`, `\x00`) — другой случай, и
            # `_validate` по-прежнему бросает на них: это не опечатка, а
            # признак того, что файл писали не руками, и продолжать чтение
            # молча нельзя (S7). Ни на чтении, ни на записи такое значение
            # не порождает переменной.
            _report_broken(line, number)
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        _validate(key, value)
        result[key] = value
    return result


def _render(existing: list[str], merged: dict[str, str]) -> list[str]:
    """Строки нового `.env`: своё обновляется на месте, чужое сохраняется.

    Раньше `write_env()` писал файл заново из словаря, а `read_env()`
    выбрасывает `#`-строки, — то есть первое же сохранение съедало
    `# мой комментарий` вместе с порядком строк. Это тот же класс сюрприза,
    от которого защищает список RETIRED_KEYS выше: приложение убирает за
    собой, а не наводит порядок в чужом файле. Комментарий пользователя,
    пустая строка и даже структурно битая строка (о ней уже сказано в лог)
    поэтому переносятся как есть.

    Дубликат ключа переписывается в ПОСЛЕДНЕМ вхождении, а прежние
    выбрасываются: действующим `read_env()` считает именно последнее, и
    оставить рядом строку с другим значением значило бы записать в файл
    неправду.
    """
    keys = [_key_of(line) for line in existing]
    effective = {key: index for index, key in enumerate(keys) if key is not None}
    rendered: list[str] = []
    written: set[str] = set()
    for index, line in enumerate(existing):
        key = keys[index]
        if key is None:
            rendered.append(line)
            continue
        if effective[key] != index:
            continue                      # не последнее вхождение — не действует
        if key not in merged:
            continue                      # мёртвый ключ: вычищен
        rendered.append(f"{key}={merged[key]}")
        written.add(key)
    rendered.extend(f"{key}={value}" for key, value in merged.items() if key not in written)
    return rendered


def write_env(values: dict[str, str]) -> None:
    # Проверка ДО чтения файла: недопустимое значение не должно приводить ни
    # к чтению, ни к созданию временного файла.
    for key, value in values.items():
        _validate(key, value)
    target = paths.env_file()
    existing = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
    merged = read_env()
    merged.update(values)
    # Вычистка при первой же записи: у пользователя, обновившегося с версии,
    # которая эти ключи писала, файл иначе носил бы их вечно. Удаление стоит
    # ПОСЛЕ слияния, поэтому мёртвый ключ не вернуть и через сам write_env().
    for retired in RETIRED_KEYS:
        merged.pop(retired, None)

    handle, temporary = tempfile.mkstemp(dir=str(target.parent))
    replaced = False
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            for line in _render(existing, merged):
                stream.write(f"{line}\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        replaced = True
    finally:
        # A failure anywhere between mkstemp() and the rename above must not
        # leave a 0600 temp file sitting in the data directory forever.
        if not replaced:
            Path(temporary).unlink(missing_ok=True)
