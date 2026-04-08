from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, List
import os
import json

router = APIRouter(prefix="/api/config", tags=["config"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
ENV_PATH = os.path.join(BASE_DIR, ".env")

DEFAULT_CONFIG = {
    # TG
    "channels": ["itvacancykz", "it_interns", "jobfortester", "workitkz", "qajoboffer", "jobforqa"],
    "keywords": ["qa", "тестировщик", "manual qa", "junior", "стажер", "стажировка", "intern", "trainee", "без опыта"],
    "exclude": ["senior", "lead", "middle", "middle 3+", "middle+", "5+ лет", "6+ лет"],
    "template": "",
    "delay_min": 60,
    "delay_max": 120,
    "max_per_day": 25,
    "history_limit": 50,
    "safe_mode": True,
    "parse_history": False,
    "file_path": "",
    "api_id": "",
    "api_hash": "",
    "tg_autostart": False,
    # HH
    "hh_keywords": ["QA", "тестировщик", "Junior QA", "стажировка QA"],
    "hh_exclude": ["senior", "lead", "middle", "5+ лет"],
    "hh_area_ids": [113],
    "hh_salary_from": 0,
    "hh_cover_letter": "",
    "hh_max_per_day": 20,
    "hh_delay_min": 30,
    "hh_delay_max": 90,
    "hh_experience": "noExperience",
    "hh_employment": ["full", "part", "probation"],
    "hh_schedule": ["remote", "fullDay", "flexible"],
    "hh_search_period": 1,
    "hh_resume_id": "",
    "hh_check_interval": 1800,
    "hh_autostart": False,
    # Selenium steps
    "hh_selenium_steps": [],
}

def load_config() -> dict:
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            try:
                cfg.update(json.load(f))
            except Exception:
                pass
    # Подтягиваем API ключи из .env
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
    # Не сохраняем API ключи в config.json
    safe_cfg = {k: v for k, v in cfg.items() if k not in ("api_id", "api_hash")}
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(safe_cfg, f, ensure_ascii=False, indent=2)
    _sync_env(cfg)

def _sync_env(cfg: dict):
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

# ── Models ────────────────────────────────────────────────────────────

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
    tg_autostart: Optional[bool] = None
    hh_keywords: Optional[List[str]] = None
    hh_exclude: Optional[List[str]] = None
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
    hh_autostart: Optional[bool] = None
    hh_selenium_steps: Optional[list] = None

# ── Routes ────────────────────────────────────────────────────────────

@router.get("")
async def get_config():
    cfg = load_config()
    if cfg.get("api_hash"):
        cfg["api_hash_set"] = True
        cfg["api_hash"] = "••••••••••••••••"
    else:
        cfg["api_hash_set"] = False
    return cfg

@router.patch("")
async def update_config(update: ConfigUpdate):
    cfg = load_config()
    data = update.dict(exclude_none=True)
    api_id = data.pop("api_id", None)
    api_hash = data.pop("api_hash", None)
    cfg.update(data)
    if api_id:
        cfg["api_id"] = api_id
    if api_hash:
        cfg["api_hash"] = api_hash
    save_config(cfg)
    return {"status": "saved"}

@router.get("/reveal-hash")
async def reveal_hash():
    cfg = load_config()
    return {"api_hash": cfg.get("api_hash", "")}
