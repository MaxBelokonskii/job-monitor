"""Единый источник настроек: прикладные настройки — в БД, секреты — только в окружении."""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from job_monitor import envfile
from job_monitor.db.repositories import SettingsRepo

logger = logging.getLogger(__name__)


class GlobalSettings(BaseModel):
    """Общее для всего приложения. Критерии поиска живут в пресетах.

    Лимиты и задержки здесь, а не в пресете, по решению D7: суточный бюджет
    существует потому, что банят аккаунт, а не пресет, — три пресета по 25
    отправок дали бы 75 сообщений с одного аккаунта.

    `hh_selenium_steps` тоже здесь: сценарий описывает, КАК управлять сайтом,
    а не ЧТО искать, и от смены профессии не зависит.
    """

    model_config = ConfigDict(extra="forbid")

    delay_min: int = Field(default=60, ge=1, le=3600)
    delay_max: int = Field(default=120, ge=1, le=3600)
    max_per_day: int = Field(default=25, ge=1, le=100)
    history_limit: int = Field(default=50, ge=1, le=500)
    safe_mode: bool = True
    parse_history: bool = False
    tg_autostart: bool = False

    hh_max_per_day: int = Field(default=20, ge=1, le=50)
    hh_delay_min: int = Field(default=30, ge=1, le=3600)
    hh_delay_max: int = Field(default=90, ge=1, le=3600)
    hh_check_interval: int = Field(default=1800, ge=60, le=86400)
    hh_autostart: bool = False
    hh_selenium_steps: list[dict] = Field(default_factory=list)

    active_preset_id: int | None = None


@dataclass(frozen=True)
class Secrets:
    api_id: int | None
    api_hash: str | None

    @property
    def is_complete(self) -> bool:
        return self.api_id is not None and bool(self.api_hash)


def _drop_unknown_fields(raw: dict) -> dict:
    """Tolerant read-side filter: keep only keys GlobalSettings still knows
    about. A stored row can outlive the schema (a field removed/renamed
    after it was written, or hand-edited), and without this,
    `GlobalSettings(**raw)` — extra="forbid" — raises for every reader with no
    in-app way to recover: `GET /api/config` 500s, both workers die on
    their first `load_config()`, and `save_settings()` can't PATCH past it
    either, because its own read of the stored row hits the same error
    first. Recovery would need manual sqlite3 surgery on the user's
    database. Filtering here means one unknown key degrades to "ignored",
    not "bricked"."""
    known = set(GlobalSettings.model_fields)
    dropped = set(raw) - known
    if dropped:
        logger.warning("settings: dropping unknown stored keys: %s", sorted(dropped))
    return {key: value for key, value in raw.items() if key in known}


def load_settings(conn: sqlite3.Connection) -> GlobalSettings:
    return GlobalSettings(**_drop_unknown_fields(SettingsRepo(conn).load()))


def save_settings(conn: sqlite3.Connection, patch: dict) -> GlobalSettings:
    """Read-modify-write the settings row as one atomic unit.

    The base read tolerates unknown keys already in the stored row (see
    `_drop_unknown_fields`) — that is what keeps a stale/foreign key from
    bricking recovery. The *merged* dict (stored fields + `patch`) is still
    validated with `GlobalSettings`'s full `extra="forbid"` strictness: that is
    what keeps `api_hash` (or any other unknown key) out of the database in
    the first place, enforced right where the write happens, so this change
    does not weaken the secret-exclusion property at all.

    The whole read -> merge -> validate -> write sequence runs inside one
    `SettingsRepo.update()` transaction (BEGIN IMMEDIATE), so a concurrent
    writer on another connection can't complete a full save in the gap
    between this read and this write and have its change silently
    overwritten — see `SettingsRepo.update()`'s docstring.
    """
    repo = SettingsRepo(conn)

    def mutate(current: dict) -> dict:
        merged = _drop_unknown_fields(current)
        merged.update(patch)
        validated = GlobalSettings(**merged)      # extra="forbid" ловит лишние ключи патча
        return validated.model_dump()

    return GlobalSettings(**repo.update(mutate))


def load_secrets() -> Secrets:
    from_file = envfile.read_env()
    raw_id = os.getenv("TG_API_ID") or from_file.get("TG_API_ID") or ""
    raw_hash = os.getenv("TG_API_HASH") or from_file.get("TG_API_HASH") or ""
    api_id = int(raw_id) if raw_id.strip().isdigit() else None
    return Secrets(api_id=api_id, api_hash=raw_hash.strip() or None)
