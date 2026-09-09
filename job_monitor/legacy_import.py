"""Однократный импорт данных старой (до-hardening) версии в SQLite."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from job_monitor.db.repositories import HhRepo, SettingsRepo, TgRepo
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
    if SettingsRepo(conn).load():
        # `_import_settings` calls `save_settings` unconditionally; without
        # this guard a second `migrate-legacy` run (the documented recovery
        # step, re-run by mistake or on purpose) silently overwrites
        # settings the user has since tuned through the UI, while the CLI
        # output claims success ("настроек: 7"). Contacts and vacancies are
        # naturally idempotent (keyed by username/vacancy_id) — settings are
        # not, so they need an explicit "already migrated" check.
        report.skipped.append(
            "настройки уже импортированы ранее — пропускаю, чтобы не затереть"
            " изменения, сделанные через UI"
        )
        return
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
    report.settings_keys = _save_valid_fields(conn, patch, report)


def _save_valid_fields(conn: sqlite3.Connection, patch: dict, report: ImportReport) -> int:
    """Сохранить то, что проходит валидацию; отвергнутые поля — в отчёт.

    У старой версии границ у настроек не было, а `AppSettings` их ввела
    (`max_per_day: ge=1, le=100` и подобные). Одно значение вне границ —
    например `{"max_per_day": 500}` — роняло `save_settings` с
    `ValidationError`, которую здесь никто не ловил: сырая трассировка
    pydantic и `exit=1`. А поскольку `_import_settings` идёт первым, вместе с
    настройками терялись контакты и вакансии: в базе после прогона всё по
    нулям.

    Выброшено ровно отвергнутое поле, а не весь блок настроек: миграция
    одноразовая, и «перенесли 11 полей из 12, двенадцатое вот» полезнее
    пользователю, чем «настройки не перенесены» без указания, какое именно
    значение мешает. Каждое отвергнутое поле названо в отчёте с его
    причиной, так что молчаливой потери нет.
    """
    remaining = dict(patch)
    # Pydantic сообщает обо всех полях сразу, так что одной итерации обычно
    # хватает; цикл ограничен на случай ошибок, всплывающих по очереди.
    for _attempt in range(len(patch) + 1):
        if not remaining:
            return 0
        try:
            save_settings(conn, remaining)
        except ValidationError as error:
            rejected = False
            for detail in error.errors(include_url=False):
                field = str(detail["loc"][0]) if detail["loc"] else ""
                if field in remaining:
                    report.skipped.append(
                        f"config.json: {field}={remaining.pop(field)!r} отвергнуто"
                        f" ({detail['msg']}) — оставлено значение по умолчанию"
                    )
                    rejected = True
            if not rejected:
                # Ошибка не привязана ни к одному полю патча (например,
                # испорченная строка настроек в самой базе). Дальше
                # пробовать нечего — сообщаем и не теряем остальной импорт.
                report.skipped.append(f"config.json: настройки не перенесены ({error.error_count()} ошибок)")
                return 0
        else:
            return len(remaining)
    return 0


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
        # `all_sent_users.txt`'s own timestamp is the file's mtime — roughly
        # "now" for an upgrading user, since it's the file the live old app
        # keeps appending to — not the date any of these contacts were
        # actually reached. Recording it as a real send (record_send) would
        # stamp every contact from this file as sent "today", which
        # `TgRepo.sent_on(date.today())` would then count: harmless while
        # nothing reads it, but the next plan serves `sent_today` from the
        # database, and the worker would refuse to send for the rest of the
        # day on a fabricated spike. `ensure_contact` registers the contact
        # (so dedup and `contacts_total()` still work) without fabricating
        # a `tg_sends` row.
        fallback = datetime.fromtimestamp(all_sent.stat().st_mtime)
        for line in all_sent.read_text(encoding="utf-8").splitlines():
            username = line.strip()
            if username.startswith("@") and not repo.was_sent(username):
                repo.ensure_contact(username, fallback)
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
    # Три независимых блока, и они действительно независимы: пока это была
    # просто последовательность вызовов, любое исключение в первом уносило с
    # собой два остальных — на одном значении вне границ в config.json база
    # после `migrate-legacy` оставалась пустой целиком. Миграция одноразовая
    # и запускается на данных неизвестного возраста, поэтому «блок не
    # перенёсся и сказал почему» здесь строго лучше, чем «не перенеслось
    # ничего».
    for step, label in (
        (_import_settings, "настройки"),
        (_import_contacts, "контакты"),
        (_import_vacancies, "вакансии"),
    ):
        try:
            step(conn, source, report)
        except Exception as error:  # noqa: BLE001 — один блок не должен уносить остальные
            report.skipped.append(f"{label}: не перенесены ({type(error).__name__}: {error})")
    return report
