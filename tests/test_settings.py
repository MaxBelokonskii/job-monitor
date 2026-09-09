import threading
import time

import pytest
from pydantic import ValidationError

from job_monitor import settings as settings_module
from job_monitor.db import connection
from job_monitor.presets import active_criteria
from job_monitor.db.repositories import SettingsRepo


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    connection.reset_connection()
    yield connection.connect()
    connection.reset_connection()


def test_defaults_are_returned_for_empty_db(conn):
    current = settings_module.load_settings(conn)
    assert current.max_per_day == 25
    assert current.safe_mode is True
    # Ключевые слова сюда больше не входят: это критерий, он живёт в пресете —
    # и у чистой установки он ПУСТ, чтобы приложение не искало чужую работу.
    assert active_criteria(conn).tg_keywords == []


def test_patch_updates_only_given_fields(conn):
    settings_module.save_settings(conn, {"max_per_day": 5})
    current = settings_module.load_settings(conn)
    assert current.max_per_day == 5
    assert current.delay_min == 60


def test_rejects_unknown_field(conn):
    with pytest.raises(ValidationError):
        settings_module.save_settings(conn, {"nonexistent": 1})


def test_rejects_out_of_range_limit(conn):
    with pytest.raises(ValidationError):
        settings_module.save_settings(conn, {"max_per_day": 0})


def test_settings_never_hold_secrets(conn):
    # `AppSettings` has no `api_hash` field at all, so asserting it's absent
    # from `load_settings(...).model_dump()` can never fail regardless of
    # what is in the database — a tautology, not a regression test. Assert
    # against the raw stored row instead, so this would actually catch a
    # future change that let a secret slip into the JSON blob.
    with pytest.raises(ValidationError):
        settings_module.save_settings(conn, {"api_hash": "deadbeef"})
    settings_module.save_settings(conn, {"max_per_day": 30})
    row = conn.execute("SELECT value FROM settings WHERE key = 'app'").fetchone()
    assert row is not None
    assert "api_hash" not in row["value"]


def test_load_settings_tolerates_unknown_stored_key(conn):
    """I2: a stored row with one key AppSettings no longer knows about (a
    field from a future/older schema, or a hand edit) used to brick every
    reader with a ValidationError (extra='forbid'). load_settings() must
    drop the unknown key and still return the rest."""
    SettingsRepo(conn).save({"max_per_day": 30, "totally_unknown_future_key": "x"})
    current = settings_module.load_settings(conn)
    assert current.max_per_day == 30


def test_save_settings_recovers_from_unknown_stored_key(conn):
    """I2 continued: save_settings()'s own initial read of the stored row
    used to re-run the same strict AppSettings(**...) parse, so a patch
    could never repair a bricked row either. It must still succeed, and the
    merged/patched dict must still be validated strictly (extra='forbid'):
    this call's own attempt to smuggle a secret through must still fail."""
    SettingsRepo(conn).save({"max_per_day": 30, "totally_unknown_future_key": "x"})
    updated = settings_module.save_settings(conn, {"max_per_day": 40})
    assert updated.max_per_day == 40
    assert settings_module.load_settings(conn).max_per_day == 40
    with pytest.raises(ValidationError):
        settings_module.save_settings(conn, {"api_hash": "deadbeef"})


def test_secrets_come_from_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)
    (tmp_path / ".env").write_text("TG_API_ID=42\nTG_API_HASH=abc\n", encoding="utf-8")
    secrets = settings_module.load_secrets()
    assert secrets.api_id == 42
    assert secrets.api_hash == "abc"
    assert secrets.is_complete is True


def test_secrets_incomplete_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)
    assert settings_module.load_secrets().is_complete is False


def test_save_settings_does_not_lose_a_concurrent_writer(tmp_path, monkeypatch):
    """I1: save_settings() used to read the current row in autocommit and
    write it back in a separate transaction. Reproduced with two
    connections: while connection A is between its read and its write,
    connection B completes a full save_settings() of its own — under the
    old code B's change is silently overwritten by A's stale snapshot, with
    no exception anywhere. This forces that exact interleaving via a delay
    injected into SettingsRepo.load() (which save_settings() now calls from
    inside one BEGIN IMMEDIATE transaction), so B's save must now block on
    A's write lock instead of racing it, and neither patch is lost.
    """
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    connection.reset_connection()
    conn_a = connection.connect()
    conn_b = connection.connect()
    settings_module.save_settings(conn_a, {"delay_min": 1, "max_per_day": 1})

    real_load = SettingsRepo.load
    reached = threading.Event()
    release = threading.Event()

    def delayed_load(self):
        result = real_load(self)
        if self._conn is conn_a:
            reached.set()
            assert release.wait(timeout=5), "release was never signalled"
        return result

    monkeypatch.setattr(SettingsRepo, "load", delayed_load)

    thread_a = threading.Thread(
        target=settings_module.save_settings, args=(conn_a, {"max_per_day": 99})
    )
    thread_a.start()
    assert reached.wait(timeout=5), "writer A never reached its read"

    thread_b = threading.Thread(
        target=settings_module.save_settings, args=(conn_b, {"delay_min": 42})
    )
    thread_b.start()
    time.sleep(0.3)  # give B's BEGIN IMMEDIATE time to queue behind A's lock
    release.set()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)
    assert not thread_a.is_alive() and not thread_b.is_alive()

    monkeypatch.setattr(SettingsRepo, "load", real_load)
    final = settings_module.load_settings(conn_a)
    assert final.max_per_day == 99, "A's own patch must survive"
    assert final.delay_min == 42, "B's concurrent write must not be lost"
    connection.reset_connection()


# ── «Безопасно по умолчанию» — целиком, а не выборочно ────────────────
#
# `test_defaults_are_returned_for_empty_db` выше пиннит `safe_mode is True`,
# и мутация этого значения ловится. Соседние флаги, у которых цена неверного
# значения не ниже, не пиннил никто, и мутации проходили зелёными:
#
# * `parse_history: bool = False` → `True` — на первом же запуске воркер
#   разбирает историю каналов и пишет ВСЕМ найденным контактам, а не только
#   авторам новых постов;
# * `tg_autostart: bool = False` → `True` — свежая установка сама поднимает
#   TG-воркер на старте приложения, до того как пользователь что-либо
#   настроил;
# * `hh_autostart: bool = False` → `True` — то же для Selenium: приложение
#   само открывает браузер и начинает откликаться.
#
# Все три — действия, которых пользователь не просил, и все три необратимы:
# отправленное сообщение не отозвать, отклик на hh.ru не отменить.
# `test_autostart_is_skipped_when_the_flags_are_off` из
# test_lifespan_autostart.py к значениям по умолчанию отношения не имеет —
# он сам записывает `False` в базу перед проверкой.

SAFE_DEFAULTS = {
    "safe_mode": True,          # пишем ли по-настоящему
    "parse_history": False,     # трогаем ли историю канала
    "tg_autostart": False,      # поднимаем ли воркер без просьбы
    "hh_autostart": False,
}


@pytest.mark.parametrize("field, expected", sorted(SAFE_DEFAULTS.items()))
def test_the_safe_default_is_pinned(conn, field, expected):
    value = getattr(settings_module.load_settings(conn), field)
    assert value is expected, (
        f"{field} по умолчанию {value!r}: на свежей установке приложение начинает "
        "действовать от лица пользователя без его участия"
    )


def test_the_safe_defaults_are_the_model_defaults_too(conn):
    """Ассерты выше читают базу; тот же вопрос к самой модели — на случай,
    если значение по умолчанию появится не в `GlobalSettings`, а в записи,
    которую кто-нибудь заведёт при первом запуске."""
    fresh = settings_module.GlobalSettings()
    for field, expected in SAFE_DEFAULTS.items():
        assert getattr(fresh, field) is expected, f"GlobalSettings.{field}"


# ── `Secrets.is_complete` — половина ключей это не «готово» ───────────
#
# Именно `is_complete` решает, собирать ли `TelegramClient`
# (job_monitor/telegram_client.py). Мутация `and` → `or` проходила зелёной:
# единственный закреплённый случай — «нет ни одного ключа», а там `or` даёт
# тот же `False`. Наполовину заполненные секреты — обычное состояние: ключи
# вводятся двумя полями и сохраняются одной кнопкой.


@pytest.mark.parametrize("api_id, api_hash, complete", [
    (12345, "deadbeefcafef00d", True),
    (None, "deadbeefcafef00d", False),
    (12345, None, False),
    (12345, "", False),
    (None, None, False),
])
def test_secrets_are_complete_only_with_both_halves(api_id, api_hash, complete):
    assert settings_module.Secrets(api_id=api_id, api_hash=api_hash).is_complete is complete
