"""Единственное место, где вычисляются пути к состоянию приложения."""

from __future__ import annotations

import logging
import os
import stat
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
