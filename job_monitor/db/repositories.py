"""Репозитории поверх SQLite: settings, telegram, hh, worker events."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

from job_monitor.db.connection import transaction

SETTINGS_KEY = "app"


class SettingsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def load(self) -> dict:
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (SETTINGS_KEY,)
        ).fetchone()
        return json.loads(row["value"]) if row else {}

    def save(self, values: dict) -> None:
        payload = json.dumps(values, ensure_ascii=False)
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (SETTINGS_KEY, payload),
            )


class TgRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def was_sent(self, username: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM tg_contacts WHERE username = ?", (username,)
        ).fetchone()
        return row is not None

    def record_send(
        self, username: str, channel: str | None, preview: str | None, now: datetime
    ) -> None:
        stamp = now.isoformat(timespec="seconds")
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO tg_contacts (username, first_sent_at, last_sent_at,"
                " send_count, source_channel) VALUES (?, ?, ?, 1, ?)"
                " ON CONFLICT(username) DO UPDATE SET"
                " last_sent_at = excluded.last_sent_at,"
                " send_count = tg_contacts.send_count + 1",
                (username, stamp, stamp, channel),
            )
            self._conn.execute(
                "INSERT INTO tg_sends (username, sent_at, channel, preview)"
                " VALUES (?, ?, ?, ?)",
                (username, stamp, channel, preview),
            )

    def sent_on(self, day: date) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM tg_sends WHERE sent_at LIKE ?",
            (f"{day.isoformat()}%",),
        ).fetchone()
        return int(row["n"])

    def contacts_total(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM tg_contacts").fetchone()["n"])

    def recent(self, limit: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT username, sent_at, channel, preview FROM tg_sends"
            " ORDER BY sent_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


class HhRepo:
    FIELDS = ("vacancy_id", "title", "company", "salary", "city", "url",
              "found_at", "applied_at", "status", "error")

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def exists(self, vacancy_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM hh_applications WHERE vacancy_id = ?", (vacancy_id,)
        ).fetchone()
        return row is not None

    def upsert(self, vacancy: dict) -> None:
        values = {field: vacancy.get(field) for field in self.FIELDS}
        updates = ", ".join(
            f"{field} = excluded.{field}" for field in self.FIELDS if field != "vacancy_id"
        )
        with transaction(self._conn):
            self._conn.execute(
                f"INSERT INTO hh_applications ({', '.join(self.FIELDS)})"
                f" VALUES ({', '.join('?' * len(self.FIELDS))})"
                f" ON CONFLICT(vacancy_id) DO UPDATE SET {updates}",
                tuple(values[field] for field in self.FIELDS),
            )

    def applied_on(self, day: date) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM hh_applications"
            " WHERE applied_at LIKE ? AND status = 'отклик отправлен'",
            (f"{day.isoformat()}%",),
        ).fetchone()
        return int(row["n"])

    def found_on(self, day: date) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM hh_applications WHERE found_at LIKE ?",
            (f"{day.isoformat()}%",),
        ).fetchone()
        return int(row["n"])

    def applied_total(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM hh_applications WHERE status = 'отклик отправлен'"
        ).fetchone()
        return int(row["n"])

    def recent(self, limit: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM hh_applications"
            " ORDER BY COALESCE(applied_at, found_at) DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


class EventsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, worker: str, kind: str, detail: str | None, now: datetime) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO worker_events (worker, at, kind, detail) VALUES (?, ?, ?, ?)",
                (worker, now.isoformat(timespec="seconds"), kind, detail),
            )

    def recent(self, worker: str, limit: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT worker, at, kind, detail FROM worker_events"
            " WHERE worker = ? ORDER BY at DESC, id DESC LIMIT ?",
            (worker, limit),
        ).fetchall()
        return [dict(row) for row in rows]
