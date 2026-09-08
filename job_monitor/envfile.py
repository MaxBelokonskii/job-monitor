"""Атомарная, безопасная работа с .env: без newline-инъекций, права 0600."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from job_monitor import paths

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


def _validate(key: str, value: str) -> None:
    if not key or "=" in key or any(bad in key for bad in FORBIDDEN):
        raise ValueError(f"недопустимое имя переменной: {key!r}")
    if any(bad in value for bad in FORBIDDEN):
        raise ValueError(f"значение {key} содержит перевод строки")


def read_env() -> dict[str, str]:
    target = paths.env_file()
    if not target.exists():
        return {}
    result: dict[str, str] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        _validate(key, value)
        result[key] = value
    return result


def write_env(values: dict[str, str]) -> None:
    merged = read_env()
    for key, value in values.items():
        _validate(key, value)
        merged[key] = value
    # Вычистка при первой же записи: у пользователя, обновившегося с версии,
    # которая эти ключи писала, файл иначе носил бы их вечно. Удаление стоит
    # ПОСЛЕ слияния, поэтому мёртвый ключ не вернуть и через сам write_env().
    for retired in RETIRED_KEYS:
        merged.pop(retired, None)
    target = paths.env_file()
    handle, temporary = tempfile.mkstemp(dir=str(target.parent))
    replaced = False
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            for key, value in merged.items():
                stream.write(f"{key}={value}\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        replaced = True
    finally:
        # A failure anywhere between mkstemp() and the rename above must not
        # leave a 0600 temp file sitting in the data directory forever.
        if not replaced:
            Path(temporary).unlink(missing_ok=True)
