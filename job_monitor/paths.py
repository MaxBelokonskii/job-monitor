"""Единственное место, где вычисляются пути к состоянию приложения."""

from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "JOB_MONITOR_DATA_DIR"
DEFAULT_DIR = Path.home() / ".job-monitor"


def data_dir() -> Path:
    raw = os.getenv(ENV_VAR)
    base = Path(raw).expanduser() if raw else DEFAULT_DIR
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    return base


def path(*parts: str) -> Path:
    target = data_dir().joinpath(*parts)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return target


def env_file() -> Path:
    return path(".env")


def db_file() -> Path:
    return path("job_monitor.db")


def logs_dir() -> Path:
    directory = path("logs")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


def tg_session() -> Path:
    """Telethon сам добавляет расширение .session."""
    return path("telegram")


def hh_cookies() -> Path:
    return path("hh_cookies.json")
