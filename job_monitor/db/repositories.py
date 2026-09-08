"""Репозитории поверх SQLite: settings, telegram, hh, worker events."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import date, datetime, timedelta

from job_monitor.db.connection import transaction

SETTINGS_KEY = "app"

# Status string a completed hh.ru application is stamped with. Hoisted here
# so the daily/total counters below and the hh_monitor.py writer can never
# drift apart — a reworded status in only one of the two places would
# silently zero the counters instead of raising anywhere.
HH_STATUS_APPLIED = "отклик отправлен"


def _day_bounds(day: date) -> tuple[str, str]:
    """Half-open [start, end) bounds for `day`, in the same
    isoformat(timespec="seconds") shape the *_at columns are stored in.

    A `col LIKE 'YYYY-MM-DD%'` filter reads like the obvious way to match
    "this calendar day", but measured with EXPLAIN QUERY PLAN it forces a
    full SCAN even when an index exists on the column — while the
    equivalent half-open range gets a SEARCH using the index. The range
    form also stops depending on the stored string having exactly this
    prefix shape.
    """
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


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

    def update(self, mutator: Callable[[dict], dict]) -> dict:
        """Atomic load -> mutator -> save, all inside one BEGIN IMMEDIATE
        transaction (see `transaction()` in db/connection.py).

        `save_settings()`'s old read-modify-write ran the read in autocommit
        and the write in its own separate transaction, so a concurrent
        writer could complete a full save in between: the second writer's
        change would be silently overwritten by the first writer's stale
        snapshot, with no exception anywhere. Holding one transaction across
        both the read and the write closes that window — a concurrent
        `update()`/`save()` on the same connection-pair now serializes
        (blocked by the write lock, absorbed by `busy_timeout`) instead of
        racing.
        """
        with transaction(self._conn):
            current = self.load()
            new_values = mutator(current)
            payload = json.dumps(new_values, ensure_ascii=False)
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (SETTINGS_KEY, payload),
            )
        return new_values


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
        """Record a send: upsert the contact, insert a history row, atomically.

        **`first_sent_at`, `last_sent_at`, `send_count` and `source_channel`
        are written and not read.** No SELECT in this module touches them:
        `was_sent()` asks only whether the row exists, `contacts_total()`
        counts rows, and everything date-shaped is answered from `tg_sends`,
        which is the table with the index for it. Nothing in the API or the
        UI exposes them either.

        They are kept rather than dropped because dropping a column in
        SQLite means rebuilding the table, and the table holds the user's
        real contact history — see the note in `job_monitor/db/migrations.py`.
        If a reader ever appears, this is the shape it will find:
        `send_count` counts calls to this method (so contacts registered by
        `ensure_contact()` from the legacy importer sit at 0), and
        `source_channel` is never updated on conflict, i.e. it is the FIRST
        channel a contact was reached through, not the latest.
        """
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

    def ensure_contact(self, username: str, first_seen: datetime) -> None:
        """Register a contact with a known first-seen date but *no* send
        history, without fabricating a `tg_sends` row.

        Used by the legacy importer for `all_sent_users.txt`: that file
        proves the contact was reached at some point in the past, but its
        own timestamp is the file's mtime (roughly "now" for an upgrading
        user), not the actual send date. Recording that as a real
        `tg_sends` row would stamp hundreds of historical contacts as sent
        "today", inflating `sent_on(today)`. This only touches
        `tg_contacts`, leaving daily-count queries over `tg_sends`
        unaffected. A no-op if the contact already exists.
        """
        stamp = first_seen.isoformat(timespec="seconds")
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO tg_contacts (username, first_sent_at, last_sent_at,"
                " send_count, source_channel) VALUES (?, ?, ?, 0, NULL)"
                " ON CONFLICT(username) DO NOTHING",
                (username, stamp, stamp),
            )

    def sent_on(self, day: date) -> int:
        start, end = _day_bounds(day)
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM tg_sends WHERE sent_at >= ? AND sent_at < ?",
            (start, end),
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
        """Merge-upsert: fields the caller omits keep their previously stored
        value instead of being overwritten with NULL. Only fields present as
        keys in `vacancy` (even if their value is None) are treated as supplied."""
        updates = ", ".join(
            f"{field} = excluded.{field}" for field in self.FIELDS if field != "vacancy_id"
        )
        with transaction(self._conn):
            existing = self._conn.execute(
                "SELECT * FROM hh_applications WHERE vacancy_id = ?",
                (vacancy["vacancy_id"],),
            ).fetchone()
            merged = {
                field: (
                    vacancy[field]
                    if field in vacancy
                    else (existing[field] if existing is not None else None)
                )
                for field in self.FIELDS
            }
            self._conn.execute(
                f"INSERT INTO hh_applications ({', '.join(self.FIELDS)})"
                f" VALUES ({', '.join('?' * len(self.FIELDS))})"
                f" ON CONFLICT(vacancy_id) DO UPDATE SET {updates}",
                tuple(merged[field] for field in self.FIELDS),
            )

    def applied_on(self, day: date) -> int:
        start, end = _day_bounds(day)
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM hh_applications"
            " WHERE applied_at >= ? AND applied_at < ? AND status = ?",
            (start, end, HH_STATUS_APPLIED),
        ).fetchone()
        return int(row["n"])

    def found_on(self, day: date) -> int:
        start, end = _day_bounds(day)
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM hh_applications WHERE found_at >= ? AND found_at < ?",
            (start, end),
        ).fetchone()
        return int(row["n"])

    def applied_total(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM hh_applications WHERE status = ?",
            (HH_STATUS_APPLIED,),
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

    def count_on(self, worker: str, kind: str, day: date) -> int:
        # Half-open range on `at`, matching `_day_bounds()` above (used by
        # TgRepo/HhRepo's own day counters) rather than a `LIKE 'YYYY-MM-DD%'`
        # prefix match: both give the same result against the
        # isoformat(timespec="seconds") strings the workers write, but the
        # range form is what actually uses idx_worker_events(worker, at) —
        # EXPLAIN QUERY PLAN shows LIKE forcing a full SCAN here too.
        start, end = _day_bounds(day)
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM worker_events"
            " WHERE worker = ? AND kind = ? AND at >= ? AND at < ?",
            (worker, kind, start, end),
        ).fetchone()
        return int(row["n"])
