import json
from datetime import date

import pytest

from job_monitor.db import connection
from job_monitor.db.repositories import HhRepo, TgRepo
from job_monitor.legacy_import import import_legacy
from job_monitor.settings import load_settings


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path / "state"))
    connection.reset_connection()
    yield connection.connect()
    connection.reset_connection()


@pytest.fixture
def legacy(tmp_path):
    source = tmp_path / "legacy"
    (source / "logs").mkdir(parents=True)
    (source / "config.json").write_text(json.dumps({
        "channels": ["itvacancykz"], "max_per_day": 7, "api_hash": "must-be-dropped",
    }), encoding="utf-8")
    (source / "all_sent_users.txt").write_text("@a\n@b\n@a\n", encoding="utf-8")
    (source / "logs" / "sent_log_2026-04-04.txt").write_text(
        "@a | 2026-04-04 10:00:00 | QA junior...\n", encoding="utf-8")
    (source / "hh_sent.json").write_text(json.dumps({
        "111": {"id": "111", "title": "QA", "company": "Acme", "url": "https://hh.ru/vacancy/111",
                "found_at": "2026-04-04 10:00", "applied_at": "2026-04-04 10:05",
                "status": "отклик отправлен"},
    }), encoding="utf-8")
    return source


def test_imports_settings_without_secrets(conn, legacy):
    report = import_legacy(conn, legacy)
    current = load_settings(conn)
    assert current.max_per_day == 7
    assert current.channels == ["itvacancykz"]
    assert "api_hash" in " ".join(report.skipped)


def test_deduplicates_contacts(conn, legacy):
    import_legacy(conn, legacy)
    repo = TgRepo(conn)
    assert repo.contacts_total() == 2      # @a встречался дважды
    assert repo.was_sent("@a") is True


def test_imports_send_history_with_dates(conn, legacy):
    import_legacy(conn, legacy)
    assert TgRepo(conn).sent_on(date(2026, 4, 4)) == 1


def test_imports_hh_applications(conn, legacy):
    import_legacy(conn, legacy)
    repo = HhRepo(conn)
    assert repo.exists("111") is True
    assert repo.applied_total() == 1


def test_is_idempotent(conn, legacy):
    import_legacy(conn, legacy)
    import_legacy(conn, legacy)
    assert TgRepo(conn).contacts_total() == 2
    assert HhRepo(conn).applied_total() == 1


def test_missing_source_reports_but_does_not_crash(conn, tmp_path):
    report = import_legacy(conn, tmp_path / "nope")
    assert report.contacts == 0
    assert report.skipped
