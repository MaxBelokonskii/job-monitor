from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import subprocess
import sys
import os
import signal
from datetime import date
from .config_routes import load_config

router = APIRouter(prefix="/api/tg", tags=["telegram"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(BASE_DIR, "monitor.py")
LOG_DIR = os.path.join(BASE_DIR, "logs")
PID_FILE = os.path.join(BASE_DIR, "monitor_pid.txt")
SYSTEM_LOG = os.path.join(LOG_DIR, "tg_system.log")
ALL_SENT_FILE = os.path.join(BASE_DIR, "all_sent_users.txt")

monitor_process: Optional[subprocess.Popen] = None

def is_running() -> bool:
    global monitor_process
    if monitor_process and monitor_process.poll() is None:
        return True
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            try:
                os.remove(PID_FILE)
            except Exception:
                pass
    return False

def get_sent_today() -> int:
    log_file = os.path.join(LOG_DIR, f"sent_log_{date.today()}.txt")
    if not os.path.exists(log_file):
        return 0
    with open(log_file, "r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())

def get_sent_total() -> int:
    if not os.path.exists(ALL_SENT_FILE):
        return 0
    with open(ALL_SENT_FILE, "r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())

def get_found_today() -> int:
    if not os.path.exists(SYSTEM_LOG):
        return 0
    today = date.today().strftime("%Y-%m-%d")
    count = 0
    with open(SYSTEM_LOG, "r", encoding="utf-8") as f:
        for line in f:
            if today in line and "[ВАКАНСИЯ]" in line:
                count += 1
    return count

def get_sent_list() -> list:
    log_file = os.path.join(LOG_DIR, f"sent_log_{date.today()}.txt")
    if not os.path.exists(log_file):
        return []
    has_file = bool(load_config().get("file_path"))
    entries = []
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("|")
            if len(parts) >= 3:
                entries.append({
                    "username": parts[0].strip(),
                    "time": parts[1].strip(),
                    "preview": parts[2].strip(),
                    "has_file": has_file,
                    "source": "tg",
                })
    return list(reversed(entries))

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
    return {
        "running": is_running(),
        "safe_mode": cfg.get("safe_mode", True),
        "parse_history": cfg.get("parse_history", False),
        "sent_today": get_sent_today(),
        "sent_total": get_sent_total(),
        "found_today": get_found_today(),
        "max_per_day": cfg.get("max_per_day", 25),
        "channels_count": len(cfg.get("channels", [])),
        "api_id": cfg.get("api_id", ""),
        "api_hash_set": bool(cfg.get("api_hash")),
        "tg_autostart": cfg.get("tg_autostart", False),
    }

@router.post("/start")
async def tg_start():
    global monitor_process
    if is_running():
        raise HTTPException(status_code=400, detail="TG скрипт уже запущен")
    cfg = load_config()
    env = os.environ.copy()
    env["SAFE_MODE"] = "true" if cfg.get("safe_mode") else "false"
    env["PARSE_HISTORY"] = "true" if cfg.get("parse_history") else "false"
    env["HISTORY_LIMIT"] = str(cfg.get("history_limit", 50))
    monitor_process = subprocess.Popen(
        [sys.executable, SCRIPT_PATH],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=BASE_DIR
    )
    return {"status": "started", "pid": monitor_process.pid}

@router.post("/stop")
async def tg_stop():
    global monitor_process
    stopped = False
    if monitor_process and monitor_process.poll() is None:
        monitor_process.terminate()
        try:
            monitor_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            monitor_process.kill()
        monitor_process = None
        stopped = True
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                pid = int(f.read().strip())
            os.kill(pid, signal.SIGTERM)
            stopped = True
        except Exception:
            pass
        try:
            os.remove(PID_FILE)
        except Exception:
            pass
    if not stopped:
        raise HTTPException(status_code=400, detail="TG скрипт не запущен")
    return {"status": "stopped"}

@router.get("/chats")
async def tg_chats():
    return get_sent_list()

@router.get("/logs")
async def tg_logs(lines: int = 100):
    return {"log": get_system_log(lines)}
