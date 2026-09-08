import os
from datetime import date

from fastapi import APIRouter, HTTPException

from .config_routes import load_config
from job_monitor import paths
from job_monitor.db.connection import get_connection
from job_monitor.db.repositories import EventsRepo, TgRepo
from job_monitor.settings import load_secrets
from job_monitor.workers.manager import WorkerAlreadyRunning, WorkerNotRunning, manager

router = APIRouter(prefix="/api/tg", tags=["telegram"])

LOG_DIR = paths.logs_dir()
SYSTEM_LOG = LOG_DIR / "tg_system.log"


def get_system_log(lines: int = 100) -> str:
    if not os.path.exists(SYSTEM_LOG):
        return ""
    with open(SYSTEM_LOG, "r", encoding="utf-8") as f:
        all_lines = f.readlines()
    return "".join(all_lines[-lines:])

# ── Routes ────────────────────────────────────────────────────────────

@router.get("/status")
async def tg_status():
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
        "channels_count": len(cfg.get("channels", [])),
        "api_id": cfg.get("api_id", ""),
        "api_hash_set": bool(secrets.api_hash),
        "tg_autostart": cfg.get("tg_autostart", False),
    }

@router.post("/start")
async def tg_start():
    try:
        await manager.start("tg")
    except WorkerAlreadyRunning:
        raise HTTPException(status_code=400, detail="TG воркер уже запущен")
    return {"status": "started"}

@router.post("/stop")
async def tg_stop():
    try:
        await manager.stop("tg")
    except WorkerNotRunning:
        raise HTTPException(status_code=400, detail="TG воркер не запущен")
    return {"status": "stopped"}

@router.get("/chats")
async def tg_chats():
    has_file = bool(load_config().get("file_path"))
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
async def tg_logs(lines: int = 100):
    return {"log": get_system_log(lines)}
