"""Единый источник настроек: прикладные настройки — в БД, секреты — только в окружении."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from job_monitor import envfile
from job_monitor.db.repositories import SettingsRepo


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Telegram
    channels: list[str] = Field(default_factory=lambda: [
        "itvacancykz", "it_interns", "jobfortester", "workitkz", "qajoboffer", "jobforqa",
    ])
    keywords: list[str] = Field(default_factory=lambda: [
        "qa", "тестировщик", "manual qa", "junior", "стажер", "стажировка",
        "intern", "trainee", "без опыта",
    ])
    exclude: list[str] = Field(default_factory=lambda: [
        "senior", "lead", "middle", "middle 3+", "middle+", "5+ лет", "6+ лет",
    ])
    template: str = ""
    delay_min: int = Field(default=60, ge=1, le=3600)
    delay_max: int = Field(default=120, ge=1, le=3600)
    max_per_day: int = Field(default=25, ge=1, le=100)
    history_limit: int = Field(default=50, ge=1, le=500)
    safe_mode: bool = True
    parse_history: bool = False
    file_path: str = ""
    tg_autostart: bool = False

    # hh.ru
    hh_keywords: list[str] = Field(default_factory=lambda: [
        "QA", "тестировщик", "Junior QA", "стажировка QA",
    ])
    hh_exclude: list[str] = Field(default_factory=lambda: ["senior", "lead", "middle", "5+ лет"])
    hh_area_ids: list[int] = Field(default_factory=lambda: [113])
    hh_salary_from: int = Field(default=0, ge=0)
    hh_cover_letter: str = ""
    hh_max_per_day: int = Field(default=20, ge=1, le=50)
    hh_delay_min: int = Field(default=30, ge=1, le=3600)
    hh_delay_max: int = Field(default=90, ge=1, le=3600)
    hh_experience: str = "noExperience"
    hh_employment: list[str] = Field(default_factory=lambda: ["full", "part", "probation"])
    hh_schedule: list[str] = Field(default_factory=lambda: ["remote", "fullDay", "flexible"])
    hh_search_period: int = Field(default=1, ge=1, le=30)
    hh_resume_id: str = ""
    hh_check_interval: int = Field(default=1800, ge=60, le=86400)
    hh_autostart: bool = False
    hh_selenium_steps: list[dict] = Field(default_factory=list)


@dataclass(frozen=True)
class Secrets:
    api_id: int | None
    api_hash: str | None

    @property
    def is_complete(self) -> bool:
        return self.api_id is not None and bool(self.api_hash)


def load_settings(conn: sqlite3.Connection) -> AppSettings:
    return AppSettings(**SettingsRepo(conn).load())


def save_settings(conn: sqlite3.Connection, patch: dict) -> AppSettings:
    repo = SettingsRepo(conn)
    merged = AppSettings(**repo.load()).model_dump()
    merged.update(patch)
    validated = AppSettings(**merged)          # extra="forbid" ловит лишние ключи
    repo.save(validated.model_dump())
    return validated


def load_secrets() -> Secrets:
    from_file = envfile.read_env()
    raw_id = os.getenv("TG_API_ID") or from_file.get("TG_API_ID") or ""
    raw_hash = os.getenv("TG_API_HASH") or from_file.get("TG_API_HASH") or ""
    api_id = int(raw_id) if raw_id.strip().isdigit() else None
    return Secrets(api_id=api_id, api_hash=raw_hash.strip() or None)
