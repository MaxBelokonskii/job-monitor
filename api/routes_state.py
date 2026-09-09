"""Aggregated dashboard state, computed from the database instead of logs.

Closes L2: the old tg/hh counters were derived by grepping log text, which
silently drifted once `sent_log_*.txt` rotated at 5000 lines. Every number
here instead comes from the tables the workers already write to (see
job_monitor/workers/telegram.py, job_monitor/workers/hh.py).
"""

from datetime import date

from fastapi import APIRouter

from job_monitor.db.connection import get_connection
from job_monitor.db.repositories import EventsRepo, HhRepo, TgRepo
from job_monitor.presets import active_criteria
from job_monitor.settings import load_secrets, load_settings
from job_monitor.workers.hh import login
from job_monitor.workers.manager import manager

router = APIRouter(prefix="/api", tags=["state"])


@router.get("/state")
async def get_state() -> dict:
    conn = get_connection()
    today = date.today()
    settings = load_settings(conn)
    criteria = active_criteria(conn)
    tg_repo, hh_repo, events = TgRepo(conn), HhRepo(conn), EventsRepo(conn)

    recent = events.recent("tg", 8) + events.recent("hh", 8)
    recent.sort(key=lambda event: event["at"], reverse=True)

    return {
        "tg": {
            **manager.status_dict("tg"),
            "sent_today": tg_repo.sent_on(today),
            "sent_total": tg_repo.contacts_total(),
            "found_today": events.count_on("tg", "vacancy", today),
            "max_per_day": settings.max_per_day,
            "safe_mode": settings.safe_mode,
            "channels_count": len(criteria.channels),
            "api_hash_set": load_secrets().api_hash is not None,
        },
        "hh": {
            **manager.status_dict("hh"),
            "sent_today": hh_repo.applied_on(today),
            "found_today": hh_repo.found_on(today),
            "total_sent": hh_repo.applied_total(),
            "max_per_day": settings.hh_max_per_day,
            "login_state": login.state.value,
        },
        "recent": recent[:8],
    }
