#!/usr/bin/env python3
"""Не даёт закоммитить секреты и архивы. Проверяет и имя, и содержимое."""

from __future__ import annotations

import sys
from fnmatch import fnmatch
from pathlib import Path

FORBIDDEN_NAMES = (
    ".env", "*.session", "*.session-journal", "*.sqlite", "*.db",
    "*cookies*.json", "config.json", "all_sent_users.txt", "sent_log_*.txt",
    "*.pdf", "*.doc", "*.docx",
)
ARCHIVE_MAGIC = {
    b"Rar!\x1a\x07": "RAR",
    b"PK\x03\x04": "ZIP",
    b"7z\xbc\xaf\x27\x1c": "7z",
    b"\x1f\x8b": "GZIP",
}


def is_archive(file: Path) -> str | None:
    try:
        head = file.open("rb").read(8)
    except OSError:
        return None
    for magic, label in ARCHIVE_MAGIC.items():
        if head.startswith(magic):
            return label
    return None


def main(argv: list[str]) -> int:
    problems: list[str] = []
    for raw in argv:
        file = Path(raw)
        if any(fnmatch(file.name, pattern) for pattern in FORBIDDEN_NAMES):
            problems.append(f"{raw}: запрещённое имя файла — состояние и секреты живут в $JOB_MONITOR_DATA_DIR")
            continue
        label = is_archive(file)
        if label:
            problems.append(f"{raw}: это архив {label}. Архивы не коммитим — .gitignore не видит их содержимое")
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
