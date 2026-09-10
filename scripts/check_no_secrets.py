#!/usr/bin/env python3
"""Не даёт закоммитить секреты и архивы. Проверяет и имя, и содержимое."""

from __future__ import annotations

import sys
from fnmatch import fnmatch
from pathlib import Path

FORBIDDEN_NAMES = (
    ".env", "*.session", "*.session-journal", "*.sqlite", "*.db",
    "*cookies*.json", "config.json", "all_sent_users.txt", "sent_log_*.txt",
    # Форматы резюме. Список обязан покрывать весь
    # `resume_store.ALLOWED_EXTENSIONS` — см. тест связи в
    # tests/test_check_no_secrets.py.
    "*.pdf", "*.doc", "*.docx", "*.rtf", "*.odt",
    # Архивы — .gitignore не видит их содержимое, поэтому запрещаем и по имени.
    "*.rar", "*.zip", "*.7z",
    "*.tar", "*.tar.gz", "*.tgz", "*.tar.bz2", "*.tar.xz", "*.txz", "*.zst",
)
ARCHIVE_MAGIC = {
    b"Rar!\x1a\x07": "RAR",
    b"PK\x03\x04": "ZIP",
    b"7z\xbc\xaf\x27\x1c": "7z",
    b"\x1f\x8b": "GZIP",
    b"BZh": "BZIP2",
    b"\xfd7zXZ\x00": "XZ",
    b"\x28\xb5\x2f\xfd": "ZSTD",
}
# Uncompressed (ustar) tar has no leading magic — its signature sits at
# offset 257, as 6 bytes ("ustar\0" for POSIX, "ustar  \0" for GNU tar).
# Reading the shared 5-byte prefix at that offset catches both variants.
USTAR_OFFSET = 257
USTAR_MAGIC = b"ustar"
READ_SIZE = max(USTAR_OFFSET + len(USTAR_MAGIC), max(len(m) for m in ARCHIVE_MAGIC))


def is_archive(file: Path) -> str | None:
    """Метка формата архива по сигнатуре, либо None, если это точно не архив.

    Не читаемый файл — не то же самое, что «не архив»: пусть OSError долетает
    до вызывающего кода как есть. Единственный механизм, защищающий от
    повторения исходной утечки, не должен молча пропускать то, что не смог
    прочитать (fail closed, не fail open).
    """
    with file.open("rb") as stream:
        head = stream.read(READ_SIZE)
    for magic, label in ARCHIVE_MAGIC.items():
        if head.startswith(magic):
            return label
    if len(head) >= USTAR_OFFSET + len(USTAR_MAGIC):
        if head[USTAR_OFFSET:USTAR_OFFSET + len(USTAR_MAGIC)] == USTAR_MAGIC:
            return "TAR"
    return None


def main(argv: list[str]) -> int:
    problems: list[str] = []
    for raw in argv:
        file = Path(raw)
        if any(fnmatch(file.name, pattern) for pattern in FORBIDDEN_NAMES):
            problems.append(f"{raw}: запрещённое имя файла — состояние и секреты живут в $JOB_MONITOR_DATA_DIR")
            continue
        try:
            label = is_archive(file)
        except OSError as exc:
            problems.append(f"{raw}: не удалось прочитать файл ({exc}) — считаем потенциальным архивом")
            continue
        if label:
            problems.append(f"{raw}: это архив {label}. Архивы не коммитим — .gitignore не видит их содержимое")
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
