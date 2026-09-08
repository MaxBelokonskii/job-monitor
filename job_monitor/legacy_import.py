"""Однократный импорт данных старой (до-hardening) версии в SQLite."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from job_monitor.db.repositories import HhRepo, TgRepo
from job_monitor.settings import AppSettings, save_settings

SECRET_KEYS = ("api_id", "api_hash")
LEGACY_STAMP = "%Y-%m-%d %H:%M"


@dataclass
class ImportReport:
    settings_keys: int = 0
    contacts: int = 0
    sends: int = 0
    vacancies: int = 0
    skipped: list[str] = field(default_factory=list)


def _parse_stamp(raw: str) -> datetime | None:
    for pattern in (LEGACY_STAMP, "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw.strip(), pattern)
        except ValueError:
            continue
    return None


def _import_settings(conn: sqlite3.Connection, source: Path, report: ImportReport) -> None:
    config = source / "config.json"
    if not config.exists():
        report.skipped.append("config.json не найден")
        return
    try:
        raw = json.loads(config.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        report.skipped.append(f"config.json: не удалось прочитать ({exc})")
        return
    for key in SECRET_KEYS:
        if key in raw:
            raw.pop(key)
            report.skipped.append(f"config.json: {key} не переносится — секреты живут в .env")
    known = set(AppSettings.model_fields)
    patch = {key: value for key, value in raw.items() if key in known}
    for key in set(raw) - known:
        report.skipped.append(f"config.json: неизвестный ключ {key}")
    save_settings(conn, patch)
    report.settings_keys = len(patch)


def _import_contacts(conn: sqlite3.Connection, source: Path, report: ImportReport) -> None:
    repo = TgRepo(conn)
    log_files = sorted((source / "logs").glob("sent_log_*.txt")) if (source / "logs").is_dir() else []
    for log_file in log_files:
        for line in log_file.read_text(encoding="utf-8").splitlines():
            parts = [part.strip() for part in line.split("|")]
            if len(parts) < 2:
                continue
            username, stamp = parts[0], _parse_stamp(parts[1])
            preview = parts[2] if len(parts) > 2 else None
            if not username.startswith("@"):
                continue
            if stamp is None:
                report.skipped.append(
                    f"{log_file.name}: не удалось разобрать дату у {username} ({parts[1]!r})"
                )
                continue
            if repo.was_sent(username) and repo.sent_on(stamp.date()) > 0:
                continue
            repo.record_send(username, None, preview, stamp)
            report.sends += 1

    all_sent = source / "all_sent_users.txt"
    if all_sent.exists():
        fallback = datetime.fromtimestamp(all_sent.stat().st_mtime)
        for line in all_sent.read_text(encoding="utf-8").splitlines():
            username = line.strip()
            if username.startswith("@") and not repo.was_sent(username):
                repo.record_send(username, None, None, fallback)
                report.sends += 1
    else:
        report.skipped.append("all_sent_users.txt не найден")
    report.contacts = repo.contacts_total()


def _import_vacancies(conn: sqlite3.Connection, source: Path, report: ImportReport) -> None:
    hh_sent = source / "hh_sent.json"
    if not hh_sent.exists():
        report.skipped.append("hh_sent.json не найден")
        return
    try:
        data = json.loads(hh_sent.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        report.skipped.append(f"hh_sent.json: не удалось прочитать ({exc})")
        return
    repo = HhRepo(conn)
    for vacancy_id, raw in data.items():
        found = _parse_stamp(raw.get("found_at", ""))
        if found is None:
            found = datetime.now()
            report.skipped.append(
                f"hh_sent.json: вакансия {vacancy_id} — found_at не распознан,"
                " подставлено текущее время"
            )
        applied = _parse_stamp(raw.get("applied_at", ""))
        repo.upsert({
            "vacancy_id": str(vacancy_id),
            "title": raw.get("title") or "без названия",
            "company": raw.get("company"),
            "salary": raw.get("salary"),
            "city": raw.get("city"),
            "url": raw.get("url"),
            "found_at": found.isoformat(timespec="seconds"),
            "applied_at": applied.isoformat(timespec="seconds") if applied else None,
            "status": raw.get("status") or "неизвестно",
            "error": None,
        })
        report.vacancies += 1


def import_legacy(conn: sqlite3.Connection, source: Path) -> ImportReport:
    report = ImportReport()
    if not source.is_dir():
        report.skipped.append(f"каталог {source} не найден")
        return report
    _import_settings(conn, source, report)
    _import_contacts(conn, source, report)
    _import_vacancies(conn, source, report)
    return report
