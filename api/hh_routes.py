from fastapi import APIRouter, HTTPException
from typing import Optional
import subprocess
import sys
import os
import json
from datetime import date
from .config_routes import load_config

router = APIRouter(prefix="/api/hh", tags=["hh"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HH_SCRIPT_PATH = os.path.join(BASE_DIR, "hh_monitor.py")
HH_SENT_PATH = os.path.join(BASE_DIR, "hh_sent.json")
HH_LOG_PATH = os.path.join(BASE_DIR, "logs", "hh.log")
HH_PID_FILE = os.path.join(BASE_DIR, "hh_pid.txt")

hh_process: Optional[subprocess.Popen] = None

def hh_is_running() -> bool:
    global hh_process
    if hh_process and hh_process.poll() is None:
        return True
    if os.path.exists(HH_PID_FILE):
        try:
            with open(HH_PID_FILE) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            try:
                os.remove(HH_PID_FILE)
            except Exception:
                pass
    return False

def load_hh_sent() -> dict:
    if not os.path.exists(HH_SENT_PATH):
        return {}
    with open(HH_SENT_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return {}

def get_hh_log(lines: int = 100) -> str:
    if not os.path.exists(HH_LOG_PATH):
        return ""
    with open(HH_LOG_PATH, "r", encoding="utf-8") as f:
        all_lines = f.readlines()
    return "".join(all_lines[-lines:])

@router.get("/status")
async def hh_status():
    sent = load_hh_sent()
    today = date.today().strftime("%Y-%m-%d")
    sent_today = sum(
        1 for v in sent.values()
        if v.get("applied_at", "").startswith(today)
        and v.get("status") == "отклик отправлен"
    )
    found_today = sum(
        1 for v in sent.values()
        if v.get("applied_at", "").startswith(today)
    )
    cfg = load_config()
    return {
        "running": hh_is_running(),
        "sent_today": sent_today,
        "found_today": found_today,
        "total_sent": sum(1 for v in sent.values() if v.get("status") == "отклик отправлен"),
        "max_per_day": cfg.get("hh_max_per_day", 20),
        "hh_autostart": cfg.get("hh_autostart", False),
    }

@router.post("/start")
async def hh_start():
    global hh_process
    if hh_is_running():
        raise HTTPException(status_code=400, detail="HH монитор уже запущен")
    if not os.path.exists(HH_SCRIPT_PATH):
        raise HTTPException(status_code=404, detail="hh_monitor.py не найден")
    hh_process = subprocess.Popen(
        [sys.executable, HH_SCRIPT_PATH],
        cwd=BASE_DIR
    )
    return {"status": "started", "pid": hh_process.pid}

@router.post("/stop")
async def hh_stop():
    global hh_process
    stopped = False
    if hh_process and hh_process.poll() is None:
        hh_process.terminate()
        try:
            hh_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            hh_process.kill()
        hh_process = None
        stopped = True
    if os.path.exists(HH_PID_FILE):
        try:
            os.remove(HH_PID_FILE)
        except Exception:
            pass
    if not stopped:
        raise HTTPException(status_code=400, detail="HH монитор не запущен")
    return {"status": "stopped"}

@router.get("/vacancies")
async def hh_vacancies():
    sent = load_hh_sent()
    vacancies = list(sent.values())
    vacancies.sort(key=lambda x: x.get("applied_at", ""), reverse=True)
    return vacancies[:50]

@router.get("/logs")
async def hh_logs(lines: int = 100):
    return {"log": get_hh_log(lines)}
