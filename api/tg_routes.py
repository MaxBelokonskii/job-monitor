from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException

from .config_routes import load_config
from job_monitor.db.connection import get_connection
from job_monitor.db.repositories import EventsRepo, TgRepo
from job_monitor.logging_setup import log_file
from job_monitor.presets import active_criteria
from job_monitor.settings import load_secrets
from job_monitor.workers.manager import WorkerAlreadyRunning, WorkerNotRunning, manager

router = APIRouter(prefix="/api/tg", tags=["telegram"])


def get_system_log(lines: int = 100) -> str:
    """Хвост файла, в который пишет TG-сторона приложения.

    Путь вычисляется на каждый вызов, а не один раз на импорте: импорт
    модуля не должен создавать каталог данных, и путь обязан следовать за
    $JOB_MONITOR_DATA_DIR. Файл наполняет job_monitor/logging_setup.py.
    """
    target = log_file("tg")
    if not target.exists():
        return ""
    with open(target, "r", encoding="utf-8") as f:
        all_lines = f.readlines()
    return "".join(all_lines[-lines:])

# ── Routes ────────────────────────────────────────────────────────────

@router.get("/status")
async def tg_status() -> dict[str, Any]:
    cfg = load_config()
    secrets = load_secrets()
    conn = get_connection()
    today = date.today()
    return {
        "running": manager.status("tg").as_dict()["running"],
        "safe_mode": cfg.get("safe_mode", True),
        "parse_history": cfg.get("parse_history", False),
        "sent_today": TgRepo(conn).sent_on(today),
        "sent_total": TgRepo(conn).contacts_total(),
        "found_today": EventsRepo(conn).count_on("tg", "vacancy", today),
        "max_per_day": cfg.get("max_per_day", 25),
        "channels_count": len(active_criteria(conn).channels),
        "api_id": cfg.get("api_id", ""),
        "api_hash_set": bool(secrets.api_hash),
        "tg_autostart": cfg.get("tg_autostart", False),
    }

@router.post("/start")
async def tg_start() -> dict[str, str | int]:
    try:
        status = await manager.start("tg")
    except WorkerAlreadyRunning:
        raise HTTPException(status_code=400, detail="TG воркер уже запущен")
    # Безусловное "started" здесь честно и совпадает с hh_start: в отличие
    # от stop, у start нет бюджета времени — manager.start() либо бросает
    # WorkerAlreadyRunning, либо возвращает статус `starting`, третьего нет.
    # `epoch` — номер этого запуска (WorkerManager._epoch): с ним фронтенд
    # знает свежий номер сразу, не дожидаясь следующего опроса, и потому
    # умеет распознать устаревший ответ на остановку. Аннотация расширена
    # до `str | int` не для красоты: у обработчика FastAPI она становится
    # `response_model`, и `dict[str, str]` отдало бы 500 на числовом поле.
    return {"status": "started", "epoch": status.epoch}

@router.post("/stop")
async def tg_stop() -> dict[str, str | int | None]:
    try:
        status = await manager.stop("tg")
    except WorkerNotRunning:
        raise HTTPException(status_code=400, detail="TG воркер не запущен")
    # Возвращаем НАСТОЯЩЕЕ состояние, а не безусловное "stopped" — тот же
    # дефект, что был вылечен в hh_stop (см. подробный комментарий там).
    # Остановка воркера имеет бюджет времени; если воркер его не уложился,
    # manager.stop() ставит error и продолжает отслеживать таску. Раньше UI
    # в этом случае показывал «остановлено», пользователь жал «Запустить»,
    # получал 400 «уже запущен», жал «Остановить» ещё раз.
    # Форма ответа сохранена: успешная остановка по-прежнему даёт
    # {"status": "stopped"}, на который смотрит frontend/app.js.
    #
    # `epoch` — номер запуска, который останавливал ИМЕННО ЭТОТ вызов
    # (manager.stop() запоминает его на входе). Без него ответ не говорил, к
    # какому запуску относится: клиент, чья остановка догнала перезапуск
    # другого клиента, получал `{"status": "running"}` и не мог отличить
    # «мою остановку отменили» от «воркер уже перезапущен без меня».
    return {
        "status": status.state.value,
        "detail": status.last_error,
        "epoch": status.epoch,
    }

@router.get("/chats")
async def tg_chats() -> list[dict[str, Any]]:
    # Резюме больше не путь в настройках, а ссылка на библиотеку (L14/D10).
    has_file = active_criteria(get_connection()).resume_id is not None
    return [
        {
            "username": row["username"],
            "time": row["sent_at"],
            "preview": row["preview"] or "",
            "has_file": has_file,
            "source": "tg",
        }
        for row in TgRepo(get_connection()).recent(50)
    ]

@router.get("/logs")
async def tg_logs(lines: int = 100) -> dict[str, str]:
    return {"log": get_system_log(lines)}
