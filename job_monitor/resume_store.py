"""Файлы библиотеки резюме.

Резюме содержит ФИО, телефон и почту, поэтому лежит в каталоге данных с
правами `0600`, а не в каталоге репозитория (решение D11): ровно так утекло
резюме предыдущего автора — вместе с закоммиченным архивом.

Загрузка — единственное место, где приложение принимает файл извне, поэтому
свойства здесь жёсткие: имя от клиента не участвует в построении пути,
расширение из белого списка, размер ограничен, запись атомарна.
"""

from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path

from job_monitor import paths

# Набор совпадает с тем, что блокирует `scripts/check_no_secrets.py`, и это
# не совпадение, а требование: расширение, которое приложение принимает как
# резюме, но хук не блокирует, — это повторение исходной утечки S1 с другим
# расширением (в закоммиченном архиве лежало резюме предыдущего автора).
# Связь закреплена тестом `tests/test_check_no_secrets.py`.
#
# `.txt` исключён намеренно: заблокировать `*.txt` в хуке нельзя — в
# репозитории есть законные текстовые файлы, — а значит резюме в таком
# формате осталось бы единственным, которое хук не поймает. Резюме
# простым текстом — случай редкий, дыра в защите — постоянная.
ALLOWED_EXTENSIONS = frozenset({".pdf", ".doc", ".docx", ".rtf", ".odt"})
MAX_BYTES = 10 * 1024 * 1024
FILE_MODE = 0o600


class ResumeRejected(ValueError):
    """Файл не принят: расширение, размер или имя не прошли проверку."""


def _extension_of(original_name: str) -> str:
    """Расширение из клиентского имени — единственное, что мы из него берём.

    `Path("../../.env").suffix` даёт `.env`, а `Path("/etc/passwd").suffix` —
    пустую строку; и то и другое отсекается белым списком ниже. Сам путь из
    клиентского имени не собирается никогда, поэтому выйти из каталога
    именем невозможно в принципе, а не только для перечисленных форм.
    """
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ResumeRejected(
            f"расширение {suffix or '(нет)'} не поддерживается; "
            f"допустимы: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    return suffix


def store(original_name: str, data: bytes) -> tuple[str, int]:
    """Сохраняет файл и возвращает `(имя на диске, размер)`.

    Имя на диске — случайный токен плюс расширение. Не идентификатор из БД:
    иначе имя пришлось бы знать до вставки строки, а угадать чужой файл по
    последовательному номеру было бы можно.
    """
    if not data:
        raise ResumeRejected("пустой файл")
    if len(data) > MAX_BYTES:
        raise ResumeRejected(
            f"файл больше {MAX_BYTES // (1024 * 1024)} МБ ({len(data)} байт)"
        )
    extension = _extension_of(original_name)
    stored_name = f"{secrets.token_hex(16)}{extension}"

    directory = paths.resume_dir()
    handle, temporary = tempfile.mkstemp(dir=directory, prefix=".upload-")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        # `mkstemp` и так создаёт файл с 0600 независимо от umask, так что
        # это подстраховка, а не несущая конструкция: тест на права
        # проходит и без неё. Строка остаётся на случай, если способ
        # создания файла когда-нибудь сменят на менее строгий.
        os.chmod(temporary, FILE_MODE)
        os.replace(temporary, directory / stored_name)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return stored_name, len(data)


def path_of(stored_name: str) -> Path:
    """Путь к файлу по имени на диске, с проверкой, что он внутри каталога.

    Проверка нужна, даже если все имена генерирует `store()`: имя приезжает
    из базы, а база — состояние на диске, которое пользователь может
    отредактировать. Одна проверка здесь дешевле доверия ко всей цепочке.
    """
    directory = paths.resume_dir().resolve()
    candidate = (directory / stored_name).resolve()
    if candidate.parent != directory:
        raise ResumeRejected(
            f"имя {stored_name!r} указывает за пределы каталога резюме"
        )
    return candidate


def remove(stored_name: str) -> None:
    path_of(stored_name).unlink(missing_ok=True)
