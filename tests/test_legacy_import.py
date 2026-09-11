import json
from datetime import date

import pytest

from job_monitor.db import connection
from job_monitor.db.repositories import HhRepo, TgRepo
from job_monitor.legacy_import import import_legacy
from job_monitor.presets import active_criteria
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
    # Каналы — критерий: они уезжают в активный пресет, а не в настройки.
    assert active_criteria(conn).channels == ["itvacancykz"]
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


def test_all_sent_users_fallback_creates_contact_but_no_fabricated_send(conn, legacy):
    """I3: `all_sent_users.txt`'s own timestamp is the file's mtime (~now
    for an upgrading user), not a real send date. `@b` only appears in that
    file (not in any sent_log_*.txt), so it must land in tg_contacts for
    dedup, but must NOT get a tg_sends row stamped "today" — that would
    inflate sent_on(today) once the next plan reads daily counts from the
    database, tripping max_per_day on data that isn't a real send."""
    import_legacy(conn, legacy)
    repo = TgRepo(conn)
    assert repo.was_sent("@b") is True
    assert repo.sent_on(date.today()) == 0
    total_sends = conn.execute(
        "SELECT COUNT(*) AS n FROM tg_sends WHERE username = '@b'"
    ).fetchone()["n"]
    assert total_sends == 0


def test_sent_log_path_still_creates_a_real_send_row(conn, legacy):
    """The log-file path carries a real date and must keep creating both a
    contact and a tg_sends row — only the mtime-based fallback changes."""
    import_legacy(conn, legacy)
    repo = TgRepo(conn)
    assert repo.was_sent("@a") is True
    assert repo.sent_on(date(2026, 4, 4)) == 1
    total_sends = conn.execute(
        "SELECT COUNT(*) AS n FROM tg_sends WHERE username = '@a'"
    ).fetchone()["n"]
    assert total_sends == 1


def test_rerunning_migrate_legacy_does_not_overwrite_tuned_settings(conn, legacy):
    """I6: `_import_settings` used to call `save_settings` unconditionally
    on every run. A user who migrates, then tunes settings through the UI,
    then re-runs the documented `make migrate-legacy` recovery step would
    get their tuning silently replaced by the old config.json — with the
    CLI still printing a settings count as if it succeeded."""
    import_legacy(conn, legacy)
    from job_monitor.settings import save_settings
    save_settings(conn, {"max_per_day": 55})

    second = import_legacy(conn, legacy)

    assert load_settings(conn).max_per_day == 55
    assert any("настрой" in note for note in second.skipped)


def test_unparseable_found_at_is_reported_with_vacancy_id(conn, legacy):
    (legacy / "hh_sent.json").write_text(json.dumps({
        "222": {"id": "222", "title": "QA2", "company": "Acme", "url": "https://hh.ru/vacancy/222",
                "found_at": "not-a-date", "applied_at": "2026-04-04 10:05",
                "status": "отклик отправлен"},
    }), encoding="utf-8")
    report = import_legacy(conn, legacy)
    assert any("222" in note and "found_at" in note for note in report.skipped)
    assert HhRepo(conn).exists("222") is True


# ── Одно значение вне границ не должно стоить всей миграции ────────────


def test_out_of_range_setting_is_reported_and_the_rest_is_imported(conn, legacy):
    """У старой версии границ у настроек не было; `AppSettings` их ввела.

    `{"max_per_day": 500}` (потолок теперь 100) роняло `save_settings` с
    `ValidationError`, которую `_import_settings` не ловил: сырая
    трассировка pydantic и `exit=1`. `_import_settings` вызывается первым,
    поэтому вместе с настройками терялись контакты и вакансии — в базе
    после прогона было по нулям.
    """
    (legacy / "config.json").write_text(json.dumps({
        "channels": ["itvacancykz"], "max_per_day": 500, "history_limit": 42,
    }), encoding="utf-8")

    report = import_legacy(conn, legacy)

    assert any("max_per_day" in note for note in report.skipped), report.skipped
    current = load_settings(conn)
    assert current.max_per_day == 25, "отвергнутое поле должно остаться значением по умолчанию"
    assert current.history_limit == 42, "валидные поля обязаны доехать"
    # Каналы — критерий: они уезжают в активный пресет, а не в настройки.
    assert active_criteria(conn).channels == ["itvacancykz"]
    assert report.settings_keys == 1, "в настройках остался только history_limit"
    assert report.criteria_keys == 1, "каналы посчитаны как критерий, а не потеряны"
    # Главное: следующие два блока не должны зависеть от исхода первого.
    assert TgRepo(conn).contacts_total() == 2
    assert HhRepo(conn).exists("111") is True


def test_several_out_of_range_settings_are_all_named(conn, legacy):
    (legacy / "config.json").write_text(json.dumps({
        "max_per_day": 500, "delay_min": 0, "hh_max_per_day": 999, "channels": ["a"],
    }), encoding="utf-8")

    report = import_legacy(conn, legacy)

    notes = " ".join(report.skipped)
    for field in ("max_per_day", "delay_min", "hh_max_per_day"):
        assert field in notes, f"{field} отвергнут молча: {report.skipped}"
    assert active_criteria(conn).channels == ["a"]


def test_a_broken_settings_block_does_not_cost_contacts_and_vacancies(conn, legacy, monkeypatch):
    """Инвариант структурный, а не про конкретный тип исключения: любой отказ
    блока настроек оставляет два остальных блока в живых."""
    import job_monitor.legacy_import as module

    def boom(*_args, **_kwargs):
        raise MemoryError("что угодно неожиданное")

    monkeypatch.setattr(module, "_import_settings", boom)
    report = import_legacy(conn, legacy)

    assert any("настройки" in note for note in report.skipped), report.skipped
    assert TgRepo(conn).contacts_total() == 2
    assert HhRepo(conn).exists("111") is True


# ── M-8: file_path из старых настроек ─────────────────────────────────


def test_the_old_resume_file_is_taken_into_the_library(tmp_path, monkeypatch) -> None:
    """M-8. `file_path` из старых настроек просто отбрасывался.

    В нём лежал путь к резюме, которое человек уже выбрал и которым уже
    откликался. После обновления оно исчезало без следа: библиотека
    пуста, пресет без вложения, и никакого сообщения об этом. Человек
    узнавал бы об этом по молчаливо уходящим откликам без резюме.
    """
    from datetime import datetime

    from job_monitor import paths, resume_store
    from job_monitor.db.connection import connect
    from job_monitor.db.repositories import ResumesRepo, SettingsRepo
    from job_monitor.presets import import_legacy_criteria

    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path / "data"))
    legacy_file = tmp_path / "Резюме Иванов.pdf"
    legacy_file.write_bytes("%PDF-1.4 старое резюме".encode())

    conn = connect(str(tmp_path / "t.db"))
    conn.execute("DELETE FROM presets")
    SettingsRepo(conn).save({"channels": ["qajobs"], "file_path": str(legacy_file)})

    preset_id = import_legacy_criteria(conn, datetime(2026, 9, 11, 12, 0, 0))
    assert preset_id is not None

    library = ResumesRepo(conn).list()
    assert len(library) == 1, "старое резюме не попало в библиотеку"
    assert library[0]["original_name"] == "Резюме Иванов.pdf"
    assert resume_store.path_of(library[0]["stored_name"]).read_bytes() == (
        "%PDF-1.4 старое резюме".encode()
    )

    from job_monitor.db.repositories import PresetsRepo

    criteria = PresetsRepo(conn).get(preset_id)["criteria"]
    assert criteria["resume_id"] == library[0]["id"], (
        "резюме перенесено, но пресет на него не ссылается — отклики уйдут "
        "без вложения"
    )


def test_a_missing_old_resume_does_not_break_the_import(tmp_path, monkeypatch) -> None:
    """Путь мог протухнуть: файл переименовали, диск переставили. Перенос
    контактов и вакансий из-за этого падать не должен — это ровно тот
    дефект, который уже чинили в `migrate-legacy`."""
    from datetime import datetime

    from job_monitor.db.connection import connect
    from job_monitor.db.repositories import PresetsRepo, ResumesRepo, SettingsRepo
    from job_monitor.presets import import_legacy_criteria

    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path / "data"))
    conn = connect(str(tmp_path / "t.db"))
    conn.execute("DELETE FROM presets")
    SettingsRepo(conn).save({
        "channels": ["qajobs"], "file_path": str(tmp_path / "которого нет.pdf"),
    })

    preset_id = import_legacy_criteria(conn, datetime(2026, 9, 11, 12, 0, 0))
    assert preset_id is not None, "перенос сорвался из-за пропавшего файла"
    assert ResumesRepo(conn).list() == []
    assert PresetsRepo(conn).get(preset_id)["criteria"]["resume_id"] is None
    assert PresetsRepo(conn).get(preset_id)["criteria"]["channels"] == ["qajobs"]


def test_an_unsupported_old_resume_does_not_break_the_import(tmp_path, monkeypatch) -> None:
    """Старая версия не проверяла расширение вовсе, так что в `file_path`
    может лежать что угодно — вплоть до `.zip`. Библиотека такое не
    принимает, и это не повод срывать перенос остального."""
    from datetime import datetime

    from job_monitor.db.connection import connect
    from job_monitor.db.repositories import ResumesRepo, SettingsRepo
    from job_monitor.presets import import_legacy_criteria

    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path / "data"))
    archive = tmp_path / "резюме.zip"
    archive.write_bytes(b"PK\x03\x04")

    conn = connect(str(tmp_path / "t.db"))
    conn.execute("DELETE FROM presets")
    SettingsRepo(conn).save({"channels": ["qajobs"], "file_path": str(archive)})

    assert import_legacy_criteria(conn, datetime(2026, 9, 11, 12, 0, 0)) is not None
    assert ResumesRepo(conn).list() == []
