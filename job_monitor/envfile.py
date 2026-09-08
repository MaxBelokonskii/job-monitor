"""Атомарная, безопасная работа с .env: без newline-инъекций, права 0600."""

from __future__ import annotations

import os
import tempfile

from job_monitor import paths

FORBIDDEN = ("\n", "\r", "\x00")


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
    target = paths.env_file()
    handle, temporary = tempfile.mkstemp(dir=str(target.parent))
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        for key, value in merged.items():
            stream.write(f"{key}={value}\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
