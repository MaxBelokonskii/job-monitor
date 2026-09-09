"""Единственное место, где вычисляются пути к состоянию приложения."""

from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ENV_VAR = "JOB_MONITOR_DATA_DIR"
DEFAULT_DIR = Path.home() / ".job-monitor"

DIR_MODE = 0o700
FILE_MODE = 0o600

# Цели, про которые уже сказано, что chmod им запрещён. `tighten()` зовётся на
# КАЖДОМ обращении к пути (`log_file()` → `logs_dir()` → `path()`, то есть на
# каждый запрос к `/api/*/logs`), поэтому предупреждение выдаётся один раз на
# цель за процесс: иначе оно само раздувало бы файл лога, который отдаётся в
# UI.
_CHMOD_REFUSED: set[tuple[str, int]] = set()


def _report_refusal(target: Path, mode: int, error: OSError) -> None:
    key = (str(target), mode)
    if key in _CHMOD_REFUSED:
        return
    _CHMOD_REFUSED.add(key)
    # Логгер берётся здесь, а не на импорте модуля: `paths` — самый нижний
    # слой, его импортирует и `logging_setup`, и порядок инициализации не
    # должен зависеть от того, кто у кого лежит в атрибутах. Само сообщение
    # до `configure_logging()` уйдёт в `logging.lastResort`, то есть в stderr
    # консоли, а после — на обе вкладки логов (`CHANNELS`).
    #
    # В сообщении только имя файла: полный путь — это домашний каталог
    # пользователя, а лог отдаётся через HTTP в UI.
    logging.getLogger(__name__).warning(
        "не удалось сузить права %s до %o (%s): на этом томе права доступа не "
        "действуют — состояние приложения не защищено режимом файла",
        target.name, mode, error.strerror or error,
    )


def tighten(target: Path, mode: int) -> None:
    """Снимает лишние биты доступа. Никогда не добавляет новых.

    Именно `&`, а не присваивание режима: пользователь, у которого каталог
    данных строже нашего (`0500`), должен остаться при своём — мы вправе
    только убрать чужой доступ, но не выдать его.

    Best-effort по построению: отказ `chmod` — не повод не работать.
    Каталог данных живёт там, куда его поставил пользователь
    (`$JOB_MONITOR_DATA_DIR`), и на exFAT/SMB/NFS, у файла с чужим владельцем
    (однократный запуск под `sudo`) или под иммутабельным флагом `chmod`
    запрещён. Раньше `os.chmod` стоял ВНЕ `try`, и такой отказ ронял
    `data_dir()` → `configure_logging()` → `lifespan`: сервер не поднимался
    вообще и печатал голый `PermissionError`. Права на чужом томе мы всё
    равно выставить не можем, поэтому отказ только фиксируется в логе.
    """
    try:
        current = stat.S_IMODE(target.stat().st_mode)
    except OSError:
        return                      # файла нет или он недоступен — не наше дело
    tightened = current & mode
    if tightened == current:
        return
    try:
        os.chmod(target, tightened)
    except OSError as error:
        _report_refusal(target, tightened, error)


def secure_file(target: Path) -> None:
    """`0600` для файла состояния.

    SQLite и Telethon создают свои файлы с учётом umask, то есть обычно
    `0644`, — в отличие от `.env`, cookies hh.ru и логов, которым права
    ставятся явно. Асимметрия ничем не оправдана: в `telegram.session` лежит
    `auth_key`, которого достаточно для входа в аккаунт в обход 2FA, а в
    `job_monitor.db` — переписка и контакты.
    """
    tighten(target, FILE_MODE)


def data_dir() -> Path:
    raw = os.getenv(ENV_VAR)
    base = Path(raw).expanduser() if raw else DEFAULT_DIR
    base.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    # Режим правится и у уже существующего каталога, а не только у только что
    # созданного. Каталог мог достаться от версии, которая ещё не ставила
    # `0700`, или быть распакован из архива (tar сохраняет режим). Каталог
    # данных существует ровно для того, чтобы держать состояние приложения, и
    # `0700` — заявленный контракт этого каталога, а не пожелание. Права при
    # этом только снимаются: `tighten()` не выдаёт доступа, которого не было.
    tighten(base, DIR_MODE)
    return base


def path(*parts: str) -> Path:
    target = data_dir().joinpath(*parts)
    target.parent.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    tighten(target.parent, DIR_MODE)
    return target


def env_file() -> Path:
    return path(".env")


def db_file() -> Path:
    return path("job_monitor.db")


def resume_dir() -> Path:
    """Каталог библиотеки резюме.

    Внутри каталога данных, а НЕ каталога репозитория: резюме содержит ФИО,
    телефон и почту, а каталог репозитория — это git. Ровно так утекло резюме
    предыдущего автора: вместе с закоммиченным архивом (S1, решение D11).
    """
    directory = path("resume")
    directory.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    tighten(directory, DIR_MODE)
    return directory


def db_backup_file(tag: str) -> Path:
    """Путь резервной копии БД рядом с самой БД: `job_monitor.db.bak-<tag>`."""
    db = db_file()
    return db.with_name(f"{db.name}.bak-{tag}")


def logs_dir() -> Path:
    directory = path("logs")
    directory.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    tighten(directory, DIR_MODE)
    return directory


def tg_session() -> Path:
    """Telethon сам добавляет расширение .session."""
    return path("telegram")


def hh_cookies() -> Path:
    return path("hh_cookies.json")

# Каталоги, попадание в которые сводит права `0600` на нет: файл уезжает в
# чужое облако целиком. Сравнение идёт по СЕГМЕНТАМ пути, а не по подстроке:
# `~/MyDropboxBackups` — не Dropbox, и ложное предупреждение здесь дороже
# пропуска, потому что ему перестают верить.
SYNCED_MARKERS: tuple[tuple[str, str], ...] = (
    ("Library/Mobile Documents/com~apple~CloudDocs", "iCloud Drive"),
    ("Dropbox", "Dropbox"),
    ("Google Drive", "Google Drive"),
    ("OneDrive", "OneDrive"),
    ("Yandex.Disk", "Яндекс.Диск"),
)


def looks_synced(directory: Path | None = None) -> str | None:
    """Имя облачного сервиса, если каталог похож на его папку.

    Проверка по пути, а не опросом сервисов: опрашивать нечего, а путь
    известен и достаточен. Нужна ровно тогда, когда каталог данных задан
    пользователем через `$JOB_MONITOR_DATA_DIR`, — по умолчанию он лежит в
    домашнем каталоге и никуда не синхронизируется.
    """
    target = (directory or data_dir()).resolve()
    parts = target.parts
    for marker, service in SYNCED_MARKERS:
        marker_parts = tuple(Path(marker).parts)
        window = len(marker_parts)
        if any(
            parts[index : index + window] == marker_parts
            for index in range(len(parts) - window + 1)
        ):
            return service
    return None


EXCLUSION_MARKER = ".backup-excluded"


def exclude_from_backups() -> bool:
    """Исключает каталог данных из Time Machine. Best-effort и ОДИН раз.

    Не условие запуска: `tmutil` может отсутствовать, том — не поддерживать
    исключения, а прав может не хватить. То же правило, что у `tighten()`:
    ужесточение защиты никогда не мешает работать.

    Отметка о выполненном исключении лежит в самом каталоге данных, и
    повторный запуск приложения внешнюю утилиту уже не зовёт. Причина не в
    экономии: `tmutil` — сторонний процесс с бюджетом в десять секунд, и
    вызывать его на КАЖДОМ старте значит поставить запуск приложения в
    зависимость от чужой утилиты. Удалили каталог данных — удалили и отметку,
    исключение сделается заново.
    """
    if sys.platform != "darwin":
        return False
    marker = data_dir() / EXCLUSION_MARKER
    if marker.exists():
        return True
    tmutil = shutil.which("tmutil")
    if tmutil is None:
        return False
    try:
        result = subprocess.run(  # noqa: S603 — shell=False, аргументы списком
            [tmutil, "addexclusion", str(data_dir())],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    try:
        marker.touch(mode=FILE_MODE)
    except OSError:
        # Отметку не записали — просто позовём `tmutil` в следующий раз.
        pass
    return True
