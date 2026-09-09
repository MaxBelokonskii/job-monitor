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


class PresetsRepo:
    """Пресеты: критерии поиска одним JSON-документом на строку.

    `criteria` хранится строкой, но наружу отдаётся разобранным словарём:
    вызывающему незачем знать про сериализацию, а забытый `json.loads` в
    одном из мест вызова — ровно тот дефект, который потом ищут полдня.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        parsed = dict(row)
        parsed["criteria"] = json.loads(parsed["criteria"])
        return parsed

    def list(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM presets ORDER BY position, id"
        ).fetchall()
        return [self._row(row) for row in rows]

    def get(self, preset_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM presets WHERE id = ?", (preset_id,)
        ).fetchone()
        return self._row(row) if row else None

    def get_by_name(self, name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM presets WHERE name = ?", (name,)
        ).fetchone()
        return self._row(row) if row else None

    def next_position(self) -> int:
        row = self._conn.execute("SELECT MAX(position) AS top FROM presets").fetchone()
        top = row["top"] if row and row["top"] is not None else -1
        return int(top) + 1

    def create(self, name: str, criteria: dict, now: datetime) -> int:
        stamp = now.isoformat(timespec="seconds")
        payload = json.dumps(criteria, ensure_ascii=False)
        with transaction(self._conn):
            cursor = self._conn.execute(
                "INSERT INTO presets (name, position, criteria, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (name, self.next_position(), payload, stamp, stamp),
            )
        return int(cursor.lastrowid)

    def update_criteria(
        self, preset_id: int, mutator: Callable[[dict], dict]
    ) -> dict:
        """Атомарное чтение -> изменение -> запись внутри одной транзакции.

        Та же причина, что у `SettingsRepo.update()`: чтение в autocommit и
        запись отдельной транзакцией дают окно, в котором параллельный
        писатель успевает сохранить своё целиком, и его правка молча
        затирается устаревшим снимком — без исключения где-либо.
        """
        with transaction(self._conn):
            row = self._conn.execute(
                "SELECT criteria FROM presets WHERE id = ?", (preset_id,)
            ).fetchone()
            if row is None:
                raise KeyError(preset_id)
            new_criteria = mutator(json.loads(row["criteria"]))
            self._conn.execute(
                "UPDATE presets SET criteria = ?, updated_at = ? WHERE id = ?",
                (
                    json.dumps(new_criteria, ensure_ascii=False),
                    datetime.now().isoformat(timespec="seconds"),
                    preset_id,
                ),
            )
        return new_criteria

    def set_name(self, preset_id: int, name: str, now: datetime) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE presets SET name = ?, updated_at = ? WHERE id = ?",
                (name, now.isoformat(timespec="seconds"), preset_id),
            )

    def set_position(self, preset_id: int, position: int, now: datetime) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE presets SET position = ?, updated_at = ? WHERE id = ?",
                (position, now.isoformat(timespec="seconds"), preset_id),
            )

    def delete(self, preset_id: int) -> None:
        with transaction(self._conn):
            self._conn.execute("DELETE FROM presets WHERE id = ?", (preset_id,))

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM presets").fetchone()
        return int(row["n"])


class ResumesRepo:
    """Библиотека резюме: записи. Сами файлы — в `job_monitor/resume_store.py`."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(
        self, original_name: str, stored_name: str, size_bytes: int, now: datetime
    ) -> int:
        with transaction(self._conn):
            cursor = self._conn.execute(
                "INSERT INTO resumes (original_name, stored_name, size_bytes, uploaded_at)"
                " VALUES (?, ?, ?, ?)",
                (
                    original_name,
                    stored_name,
                    size_bytes,
                    now.isoformat(timespec="seconds"),
                ),
            )
        return int(cursor.lastrowid)

    def list(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM resumes ORDER BY uploaded_at DESC, id DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def get(self, resume_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM resumes WHERE id = ?", (resume_id,)
        ).fetchone()
        return dict(row) if row else None

    def delete(self, resume_id: int) -> None:
        with transaction(self._conn):
            self._conn.execute("DELETE FROM resumes WHERE id = ?", (resume_id,))

    def presets_using(self, resume_id: int) -> list[str]:
        """Имена пресетов, ссылающихся на это резюме.

        `resume_id` живёт внутри JSON-документа критериев, поэтому связь
        проверяется разбором, а не внешним ключом — и именно разбором, а не
        поиском подстроки: `LIKE '%"resume_id": 1%'` спутал бы 1 с 11.
        Удаление файла, на который ссылается пресет, сломало бы его молча,
        поэтому удаление обязано уметь назвать всех, кто пострадает.
        """
        names: list[str] = []
        for row in self._conn.execute("SELECT name, criteria FROM presets").fetchall():
            if json.loads(row["criteria"]).get("resume_id") == resume_id:
                names.append(row["name"])
        return names


class TgFoundRepo:
    """Найденное в Telegram. Пишется этим подпроектом, читается следующим."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record(
        self,
        channel: str,
        message_id: int,
        username: str | None,
        preview: str | None,
        matched_keyword: str | None,
        now: datetime,
    ) -> bool:
        """`True`, если пост новый; `False`, если такой уже записан.

        `INSERT OR IGNORE` вместо предварительного `SELECT`: проверка и
        вставка одним оператором не оставляют окна между ними.
        """
        with transaction(self._conn):
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO tg_found"
                " (channel, message_id, found_at, username, preview, matched_keyword)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    channel,
                    message_id,
                    now.isoformat(timespec="seconds"),
                    username,
                    preview,
                    matched_keyword,
                ),
            )
        return cursor.rowcount == 1

    def exists(self, channel: str, message_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM tg_found WHERE channel = ? AND message_id = ?",
            (channel, message_id),
        ).fetchone()
        return row is not None

    def recent(self, limit: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM tg_found ORDER BY found_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
