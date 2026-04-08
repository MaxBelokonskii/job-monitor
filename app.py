from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import subprocess
import sys
import os
import json
import signal
from datetime import date
from typing import Optional, List
import asyncio
from telethon import TelegramClient
from telethon.tl.types import User
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_PATH = os.path.join(BASE_DIR, "monitor.py")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
ENV_PATH = os.path.join(BASE_DIR, ".env")
LOG_DIR = os.path.join(BASE_DIR, "logs")
PID_FILE = os.path.join(BASE_DIR, "monitor_pid.txt")
SYSTEM_LOG = os.path.join(LOG_DIR, "system.log")

app = FastAPI(title="QA Monitor API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Процесс скрипта ---
monitor_process: Optional[subprocess.Popen] = None

# --- Дефолтный конфиг ---
DEFAULT_CONFIG = {
    "channels": ["itvacancykz", "it_interns", "jobfortester", "workitkz", "qajoboffer", "jobforqa"],
    "keywords": ["qa", "тестировщик", "manual qa", "junior", "стажер", "стажировка", "intern", "trainee", "без опыта"],
    "exclude": ["senior", "lead", "middle", "middle 3+", "middle+", "5+ лет", "6+ лет", "3+ года", "4+ года"],
    "template": "Приветствую!\n\nИщу честный старт в профессии QA...",
    "delay_min": 60,
    "delay_max": 120,
    "max_per_day": 25,
    "history_limit": 50,
    "safe_mode": True,
    "parse_history": False,
    "file_path": "",
    "api_id": "",
    "api_hash": "",
}

def load_config() -> dict:
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    # Подтягиваем API ключи из .env если не в config
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() == "TG_API_ID" and not cfg.get("api_id"):
                        cfg["api_id"] = v.strip()
                    if k.strip() == "TG_API_HASH" and not cfg.get("api_hash"):
                        cfg["api_hash"] = v.strip()
    return cfg

def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    # Синхронизируем .env с API ключами и режимом
    update_env(cfg)

def update_env(cfg: dict):
    env_vars = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env_vars[k.strip()] = v.strip()
    if cfg.get("api_id"):
        env_vars["TG_API_ID"] = str(cfg["api_id"])
    if cfg.get("api_hash"):
        env_vars["TG_API_HASH"] = str(cfg["api_hash"])
    env_vars["SAFE_MODE"] = "true" if cfg.get("safe_mode") else "false"
    env_vars["PARSE_HISTORY"] = "true" if cfg.get("parse_history") else "false"
    env_vars["HISTORY_LIMIT"] = str(cfg.get("history_limit", 50))
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        for k, v in env_vars.items():
            f.write(f"{k}={v}\n")

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
            # PID файл есть но процесс мёртв — чистим
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

def get_found_today() -> int:
    """Считаем вакансии найденные сегодня из system.log"""
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
    has_file = bool(load_config().get("file_path"))  # один раз, не в цикле
    entries = []
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("|")
            if len(parts) >= 3:
                entries.append({
                    "username": parts[0].strip(),
                    "time": parts[1].strip(),
                    "preview": parts[2].strip(),
                    "has_file": has_file
                })
    return list(reversed(entries))

def get_system_log(lines: int = 100) -> str:
    if not os.path.exists(SYSTEM_LOG):
        return ""
    with open(SYSTEM_LOG, "r", encoding="utf-8") as f:
        all_lines = f.readlines()
    return "".join(all_lines[-lines:])

# ── Models ──────────────────────────────────────────────────────────────

class ConfigUpdate(BaseModel):
    channels: Optional[List[str]] = None
    keywords: Optional[List[str]] = None
    exclude: Optional[List[str]] = None
    template: Optional[str] = None
    delay_min: Optional[int] = None
    delay_max: Optional[int] = None
    max_per_day: Optional[int] = None
    history_limit: Optional[int] = None
    safe_mode: Optional[bool] = None
    parse_history: Optional[bool] = None
    file_path: Optional[str] = None
    api_id: Optional[str] = None
    api_hash: Optional[str] = None

# ── Routes ──────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    html_path = os.path.join(BASE_DIR, "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>index.html not found</h1>"

@app.get("/api/status")
async def get_status():
    cfg = load_config()
    return {
        "running": is_running(),
        "safe_mode": cfg.get("safe_mode", True),
        "parse_history": cfg.get("parse_history", False),
        "sent_today": get_sent_today(),
        "found_today": get_found_today(),
        "max_per_day": cfg.get("max_per_day", 25),
        "channels_count": len(cfg.get("channels", [])),
        "api_id": cfg.get("api_id", ""),
        "api_hash_set": bool(cfg.get("api_hash")),
    }

@app.post("/api/start")
async def start_script():
    global monitor_process
    if is_running():
        raise HTTPException(status_code=400, detail="Скрипт уже запущен")
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

@app.post("/api/stop")
async def stop_script():
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
        raise HTTPException(status_code=400, detail="Скрипт не запущен")
    return {"status": "stopped"}

@app.get("/api/config")
async def get_config():
    cfg = load_config()
    # Маскируем hash — показываем что он есть, но не раскрываем
    if cfg.get("api_hash"):
        cfg["api_hash_set"] = True
        cfg["api_hash"] = "••••••••••••••••"
    else:
        cfg["api_hash_set"] = False
    return cfg

@app.patch("/api/config")
async def update_config(update: ConfigUpdate):
    cfg = load_config()
    data = update.dict(exclude_none=True)
    # API ключи пишем только в .env, не в config.json
    api_id = data.pop("api_id", None)
    api_hash = data.pop("api_hash", None)
    cfg.update(data)
    # Убираем ключи из config перед сохранением
    cfg.pop("api_id", None)
    cfg.pop("api_hash", None)
    save_config(cfg)
    # Отдельно обновляем .env если ключи переданы
    if api_id or api_hash:
        env_patch = {}
        if api_id:
            env_patch["api_id"] = api_id
            cfg["api_id"] = api_id
        if api_hash:
            env_patch["api_hash"] = api_hash
            cfg["api_hash"] = api_hash
        update_env(cfg)
    return {"status": "saved"}

@app.get("/api/reveal-hash")
async def reveal_hash():
    cfg = load_config()
    return {"api_hash": cfg.get("api_hash", "")}

@app.get("/api/chats")
async def get_chats():
    return get_sent_list()

@app.get("/api/logs")
async def get_logs(lines: int = 100):
    return {"log": get_system_log(lines)}

@app.get("/api/stats")
async def get_stats():
    cfg = load_config()
    sent = get_sent_list()
    return {
        "sent_today": len(sent),
        "chats": sent[:20],
        "channels": cfg.get("channels", []),
    }

# ── Telethon helpers ────────────────────────────────────────────────────

_tg_client: Optional[TelegramClient] = None
_tg_lock = asyncio.Lock()

async def get_tg_client() -> TelegramClient:
    """Возвращает один экземпляр клиента (singleton) чтобы избежать конфликтов"""
    global _tg_client
    cfg = load_config()
    api_id = int(cfg.get("api_id") or os.getenv("TG_API_ID", 0))
    api_hash = cfg.get("api_hash") or os.getenv("TG_API_HASH", "")
    session_path = os.path.join(BASE_DIR, "session_web")
    if _tg_client is None:
        _tg_client = TelegramClient(session_path, api_id, api_hash)
    return _tg_client

class SendMessage(BaseModel):
    text: str

class AuthRequest(BaseModel):
    phone: str

# Временное хранилище phone_hash между запросами
_auth_state: dict = {}

@app.post("/api/auth/send-code")
async def send_auth_code(body: AuthRequest):
    """Отправить код авторизации на телефон"""
    client = await get_tg_client()
    try:
        await client.connect()
        if await client.is_user_authorized():
            return {"status": "already_authorized"}
        result = await client.send_code_request(body.phone)
        _auth_state["phone"] = body.phone
        _auth_state["phone_hash"] = result.phone_code_hash
        return {"status": "code_sent", "phone_hash": result.phone_code_hash}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        pass  # singleton — не отключаем

class AuthCode(BaseModel):
    phone: str
    code: str
    phone_hash: str
    password: Optional[str] = None

@app.post("/api/auth/verify-code")
async def verify_auth_code(body: AuthCode):
    """Подтвердить код и сохранить сессию"""
    from telethon.errors import SessionPasswordNeededError
    client = await get_tg_client()
    try:
        await client.connect()
        try:
            await client.sign_in(body.phone, body.code, phone_code_hash=body.phone_hash)
        except SessionPasswordNeededError:
            if not body.password:
                raise HTTPException(status_code=428, detail="2FA_REQUIRED")
            await client.sign_in(password=body.password)
        return {"status": "authorized"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        pass  # singleton — не отключаем

@app.get("/api/auth/status")
async def auth_status():
    """Проверить авторизована ли web-сессия"""
    client = await get_tg_client()
    try:
        await client.connect()
        authorized = await client.is_user_authorized()
        return {"authorized": authorized}
    except Exception as e:
        return {"authorized": False, "error": str(e)}
    finally:
        pass  # singleton — не отключаем

@app.get("/api/messages/{username}")
async def get_messages(username: str, limit: int = 30):
    client = await get_tg_client()
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise HTTPException(status_code=401, detail="Telegram не авторизован")
        messages = []
        async for msg in client.iter_messages(username.lstrip("@"), limit=limit):
            if not msg.text:
                continue
            sender = "me" if msg.out else username
            messages.append({
                "id": msg.id,
                "text": msg.text,
                "date": msg.date.strftime("%Y-%m-%d %H:%M"),
                "sender": sender,
                "out": msg.out,
            })
        return list(reversed(messages))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        pass  # singleton — не отключаем

@app.post("/api/messages/{username}")
async def send_message(username: str, body: SendMessage):
    client = await get_tg_client()
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise HTTPException(status_code=401, detail="Telegram не авторизован")
        await client.send_message(username.lstrip("@"), body.text)
        return {"status": "sent"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        pass  # singleton — не отключаем

# ── HH Monitor ──────────────────────────────────────────────────────────

hh_process: Optional[subprocess.Popen] = None
HH_SCRIPT_PATH = os.path.join(BASE_DIR, "hh_monitor.py")
HH_SENT_PATH = os.path.join(BASE_DIR, "hh_sent.json")
HH_LOG_PATH = os.path.join(LOG_DIR, "hh.log")
HH_PID_FILE = os.path.join(BASE_DIR, "hh_pid.txt")

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
            try: os.remove(HH_PID_FILE)
            except: pass
    return False

def load_hh_sent() -> dict:
    if not os.path.exists(HH_SENT_PATH):
        return {}
    with open(HH_SENT_PATH, "r", encoding="utf-8") as f:
        try: return json.load(f)
        except: return {}

def get_hh_log(lines: int = 100) -> str:
    if not os.path.exists(HH_LOG_PATH):
        return ""
    with open(HH_LOG_PATH, "r", encoding="utf-8") as f:
        all_lines = f.readlines()
    return "".join(all_lines[-lines:])

@app.get("/api/hh/status")
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
    return {
        "running": hh_is_running(),
        "sent_today": sent_today,
        "found_today": found_today,
        "total_sent": sum(1 for v in sent.values() if v.get("status") == "отклик отправлен"),
    }

@app.post("/api/hh/start")
async def hh_start():
    global hh_process
    if hh_is_running():
        raise HTTPException(status_code=400, detail="HH монитор уже запущен")
    hh_process = subprocess.Popen(
        [sys.executable, HH_SCRIPT_PATH],
        cwd=BASE_DIR
    )
    return {"status": "started", "pid": hh_process.pid}

@app.post("/api/hh/stop")
async def hh_stop():
    global hh_process
    stopped = False
    if hh_process and hh_process.poll() is None:
        hh_process.terminate()
        try: hh_process.wait(timeout=5)
        except: hh_process.kill()
        hh_process = None
        stopped = True
    if not stopped:
        raise HTTPException(status_code=400, detail="HH монитор не запущен")
    return {"status": "stopped"}

@app.get("/api/hh/vacancies")
async def hh_vacancies():
    sent = load_hh_sent()
    vacancies = list(sent.values())
    vacancies.sort(key=lambda x: x.get("applied_at", ""), reverse=True)
    return vacancies[:50]

@app.get("/api/hh/logs")
async def hh_logs(lines: int = 100):
    return {"log": get_hh_log(lines)}

class HHConfigUpdate(BaseModel):
    hh_keywords: Optional[List[str]] = None
    hh_exclude: Optional[List[str]] = None
    hh_regions: Optional[List[str]] = None
    hh_area_ids: Optional[List[int]] = None
    hh_salary_from: Optional[int] = None
    hh_cover_letter: Optional[str] = None
    hh_max_per_day: Optional[int] = None
    hh_delay_min: Optional[int] = None
    hh_delay_max: Optional[int] = None
    hh_experience: Optional[str] = None
    hh_employment: Optional[List[str]] = None
    hh_schedule: Optional[List[str]] = None
    hh_search_period: Optional[int] = None
    hh_resume_id: Optional[str] = None
    hh_check_interval: Optional[int] = None

@app.patch("/api/hh/config")
async def hh_update_config(update: HHConfigUpdate):
    cfg = load_config()
    data = update.dict(exclude_none=True)
    cfg.update(data)
    save_config(cfg)
    return {"status": "saved"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)
