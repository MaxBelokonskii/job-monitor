import asyncio
from datetime import date

from fastapi import APIRouter, HTTPException

from .config_routes import load_config
from job_monitor import paths
from job_monitor.db.connection import get_connection
from job_monitor.db.repositories import HhRepo
from job_monitor.workers.hh import login
from job_monitor.workers.manager import WorkerAlreadyRunning, WorkerNotRunning, manager

router = APIRouter(prefix="/api/hh", tags=["hh"])

LOG_DIR = paths.logs_dir()
HH_LOG_PATH = LOG_DIR / "hh.log"


def get_hh_log(lines: int = 100) -> str:
    if not HH_LOG_PATH.exists():
        return ""
    with open(HH_LOG_PATH, "r", encoding="utf-8") as f:
        all_lines = f.readlines()
    return "".join(all_lines[-lines:])


@router.get("/status")
async def hh_status():
    repo = HhRepo(get_connection())
    cfg = load_config()
    return {
        "running": manager.status("hh").as_dict()["running"],
        "sent_today": repo.applied_on(date.today()),
        "found_today": repo.found_on(date.today()),
        "total_sent": repo.applied_total(),
        "max_per_day": cfg.get("hh_max_per_day", 20),
        "hh_autostart": cfg.get("hh_autostart", False),
    }


@router.post("/start")
async def hh_start():
    try:
        await manager.start("hh")
    except WorkerAlreadyRunning:
        raise HTTPException(status_code=400, detail="HH монитор уже запущен")
    return {"status": "started"}


@router.post("/stop")
async def hh_stop():
    try:
        await manager.stop("hh")
    except WorkerNotRunning:
        raise HTTPException(status_code=400, detail="HH монитор не запущен")
    return {"status": "stopped"}


@router.post("/login/start")
async def hh_login_start() -> dict:
    return {"state": (await asyncio.to_thread(login.start)).value}


@router.post("/login/confirm")
async def hh_login_confirm() -> dict:
    return {"state": (await asyncio.to_thread(login.confirm)).value}


@router.get("/login/status")
async def hh_login_status() -> dict:
    return {"state": login.state.value}


@router.get("/vacancies")
async def hh_vacancies():
    return HhRepo(get_connection()).recent(50)


@router.get("/logs")
async def hh_logs(lines: int = 100):
    return {"log": get_hh_log(lines)}
