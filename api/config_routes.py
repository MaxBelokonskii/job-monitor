from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from job_monitor import envfile
from job_monitor.db.connection import get_connection
from job_monitor.settings import AppSettings, load_secrets, load_settings, save_settings
from job_monitor.telegram_client import reset_client

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
async def get_config() -> dict:
    current = load_settings(get_connection()).model_dump()
    secrets = load_secrets()
    current["api_id"] = str(secrets.api_id) if secrets.api_id else ""
    current["api_hash_set"] = bool(secrets.api_hash)
    return current


@router.patch("")
async def update_config(patch: dict) -> dict:
    patch = dict(patch)
    api_id = patch.pop("api_id", None)
    api_hash = patch.pop("api_hash", None)
    patch.pop("api_hash_set", None)

    secrets_to_write: dict[str, str] = {}
    if api_id:
        if not str(api_id).strip().isdigit():
            raise HTTPException(status_code=422, detail="api_id должен быть числом")
        secrets_to_write["TG_API_ID"] = str(api_id).strip()
    if api_hash and not str(api_hash).startswith("•"):
        secrets_to_write["TG_API_HASH"] = str(api_hash).strip()

    try:
        updated = save_settings(get_connection(), patch)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors(include_url=False)) from error

    if secrets_to_write:
        # В `.env` живут ТОЛЬКО секреты. Раньше сюда же дописывались
        # SAFE_MODE / PARSE_HISTORY / HISTORY_LIMIT — копия без единого
        # читателя: `.env` читает только `job_monitor/settings.py::
        # load_secrets`, и берёт он оттуда исключительно TG_API_ID и
        # TG_API_HASH. Прикладные настройки живут в SQLite. Копия к тому же
        # устаревала по построению — она обновлялась лишь тогда, когда было
        # что писать из секретов, — так что пользователь, открывший
        # `~/.job-monitor/.env`, видел `SAFE_MODE=false`, правил на `true`,
        # перезапускал приложение и считал себя в безопасном режиме, пока
        # воркер читал `safe_mode` из базы и продолжал реально писать людям.
        envfile.write_env(secrets_to_write)
        # L11: ключи сменились — следующий get_client() соберёт клиента заново.
        # Ждём отключения: старый клиент должен отпустить telegram.session
        # прежде, чем новый его откроет.
        await reset_client()
    return {"status": "saved"}


def load_config() -> dict:
    """Совместимость: tg_routes и hh_routes ждут словарь."""
    return {**load_settings(get_connection()).model_dump(),
            "api_id": load_secrets().api_id or ""}
