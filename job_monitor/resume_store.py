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
from collections.abc import Iterator
from contextlib import contextmanager
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


class Incoming:
    """Приём файла кусками во временный файл.

    Существует ради того, чтобы тело запроса не держать целиком в памяти:
    десять мегабайт на каждую загрузку — плата, которую незачем вносить,
    когда файл всё равно едет на диск. Предел проверяется по ходу приёма,
    поэтому слишком большой файл отвергается на первом же лишнем куске, а
    не после того, как целиком прочитан.

    Записи наружу не видно, пока не вызван `commit()`: до него файл лежит
    под временным именем с точкой в начале, а `commit()` переносит его
    одним `os.replace` — то есть либо файл есть целиком, либо его нет.
    """

    def __init__(self, extension: str) -> None:
        self._extension = extension
        self._size = 0
        self._directory = paths.resume_dir()
        handle, self._temporary = tempfile.mkstemp(
            dir=self._directory, prefix=".upload-"
        )
        self._stream = os.fdopen(handle, "wb")

    def write(self, chunk: bytes) -> None:
        self._size += len(chunk)
        if self._size > MAX_BYTES:
            raise ResumeRejected(
                f"файл больше {MAX_BYTES // (1024 * 1024)} МБ"
            )
        self._stream.write(chunk)

    def commit(self) -> tuple[str, int]:
        """Переносит принятое под случайным именем. `(имя на диске, размер)`.

        Имя — случайный токен плюс расширение, а не идентификатор из БД:
        иначе имя пришлось бы знать до вставки строки, а угадать чужой
        файл по последовательному номеру было бы можно.
        """
        if self._size == 0:
            raise ResumeRejected("пустой файл")
        self._stream.close()
        # `mkstemp` и так создаёт файл с 0600 независимо от umask, так что
        # это подстраховка, а не несущая конструкция: тест на права
        # проходит и без неё. Строка остаётся на случай, если способ
        # создания файла когда-нибудь сменят на менее строгий.
        os.chmod(self._temporary, FILE_MODE)
        stored_name = f"{secrets.token_hex(16)}{self._extension}"
        os.replace(self._temporary, self._directory / stored_name)
        return stored_name, self._size

    def abort(self) -> None:
        """Убирает за собой. Вызывается и после `commit()` — тогда убирать
        уже нечего, и это нормально."""
        try:
            self._stream.close()
        except OSError:  # pragma: no cover — поток уже закрыт commit'ом
            pass
        Path(self._temporary).unlink(missing_ok=True)


@contextmanager
def receiving(original_name: str) -> Iterator[Incoming]:
    """Приём файла: расширение проверяется ДО первого прочитанного байта.

    Иначе отказ по расширению стоил бы чтения всего тела — и злонамеренному
    клиенту хватило бы неподходящего имени, чтобы заставить приложение
    принять десять мегабайт впустую.
    """
    incoming = Incoming(_extension_of(original_name))
    try:
        yield incoming
    finally:
        incoming.abort()


def store(original_name: str, data: bytes) -> tuple[str, int]:
    """Сохраняет файл целиком из памяти. `(имя на диске, размер)`.

    Тонкая обёртка над `receiving()` для вызывающих, у которых байты уже
    на руках (перенос старых данных, тесты). Сетевой путь идёт через
    `receiving()` напрямую и в память файл не кладёт.
    """
    with receiving(original_name) as incoming:
        incoming.write(data)
        return incoming.commit()


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
