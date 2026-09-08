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
    second = import_legacy(conn, legacy)
    assert TgRepo(conn).contacts_total() == 2
    assert HhRepo(conn).applied_total() == 1
    # The guard in _import_contacts must actually skip already-imported rows,
    # not just rely on the repositories' own unique-key upserts.
    assert TgRepo(conn).sent_on(date(2026, 4, 4)) == 1
    assert second.sends == 0


def test_missing_source_reports_but_does_not_crash(conn, tmp_path):
    report = import_legacy(conn, tmp_path / "nope")
    assert report.contacts == 0
    assert report.skipped


def test_malformed_config_json_is_skipped_not_fatal(conn, legacy):
    (legacy / "config.json").write_text("{not valid json", encoding="utf-8")
    report = import_legacy(conn, legacy)
    assert any("config.json" in note for note in report.skipped)
    # the rest of the migration must still run despite the broken config.json
    assert TgRepo(conn).contacts_total() == 2
    assert HhRepo(conn).exists("111") is True


def test_malformed_hh_sent_json_is_skipped_not_fatal(conn, legacy):
    (legacy / "hh_sent.json").write_text("{not valid json", encoding="utf-8")
    report = import_legacy(conn, legacy)
    assert any("hh_sent.json" in note for note in report.skipped)
    assert HhRepo(conn).applied_total() == 0
    # settings and contacts must still have been imported
    current = load_settings(conn)
    assert current.max_per_day == 7
    assert TgRepo(conn).contacts_total() == 2


def test_unparseable_send_log_timestamp_is_reported(conn, legacy):
    (legacy / "logs" / "sent_log_2026-04-04.txt").write_text(
        "@c | not-a-date | QA junior...\n", encoding="utf-8")
    report = import_legacy(conn, legacy)
    assert any("не удалось разобрать дату" in note and "@c" in note for note in report.skipped)
    assert TgRepo(conn).was_sent("@c") is False


def test_unparseable_found_at_is_reported_with_vacancy_id(conn, legacy):
    (legacy / "hh_sent.json").write_text(json.dumps({
        "222": {"id": "222", "title": "QA2", "company": "Acme", "url": "https://hh.ru/vacancy/222",
                "found_at": "not-a-date", "applied_at": "2026-04-04 10:05",
                "status": "отклик отправлен"},
    }), encoding="utf-8")
    report = import_legacy(conn, legacy)
    assert any("222" in note and "found_at" in note for note in report.skipped)
    assert HhRepo(conn).exists("222") is True
