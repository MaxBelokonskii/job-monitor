from datetime import datetime

import pytest

from job_monitor.db import connection
from job_monitor.db.repositories import EventsRepo, HhRepo, TgRepo
from job_monitor.security import APP_TOKEN, TOKEN_HEADER
from job_monitor.workers.manager import manager

AUTH = {TOKEN_HEADER: APP_TOKEN}


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    connection.reset_connection()
    # `manager` is a process-wide singleton (job_monitor/workers/manager.py),
    # so a worker crash recorded by an earlier test module (e.g.
    # test_lifespan_autostart.py's real, failing manager.start("tg")) leaves
    # its WorkerStatus at `error` for the rest of the pytest session — with
    # no reset, /api/state's `state` field here would depend on test run
    # order instead of on anything this file does.
    manager._statuses.clear()
    yield
    connection.reset_connection()
    manager._statuses.clear()


def test_counts_come_from_database(client):
    conn = connection.get_connection()
    now = datetime.now()
    TgRepo(conn).record_send("@hr_anna", "itvacancykz", "QA", now)
    HhRepo(conn).upsert({"vacancy_id": "1", "title": "QA", "status": "отклик отправлен",
                         "found_at": now.isoformat(), "applied_at": now.isoformat()})
    body = client.get("/api/state", headers=AUTH).json()
    assert body["tg"]["sent_today"] == 1
    assert body["tg"]["sent_total"] == 1
    assert body["hh"]["sent_today"] == 1


def test_worker_state_is_reported(client):
    body = client.get("/api/state", headers=AUTH).json()
    assert body["tg"]["running"] is False
    assert body["tg"]["state"] == "stopped"


def test_worker_state_says_whether_start_can_succeed(client):
    """Фронтенду мало `state`: в `error` он не отличает упавший сам воркер
    (start сработает) от зависшего при остановке (start всегда даст 400).
    `can_start` — этот признак, и без него кнопка обещает невыполнимое."""
    body = client.get("/api/state", headers=AUTH).json()
    assert body["tg"]["can_start"] is True
    assert body["hh"]["can_start"] is True


def test_state_names_the_run_each_worker_status_belongs_to(client, monkeypatch):
    """`epoch` — номер запуска воркера (`WorkerManager._epoch`), и он обязан
    доезжать до фронтенда в общем `GET /api/state`.

    Без него клиент знает только текущее состояние и не может распознать
    устаревший ответ на остановку: ответ `POST /stop` помечен номером СВОЕЙ
    остановки, и сравнивать его не с чем. Мутационный признак — поле,
    выброшенное из `WorkerStatus.as_dict()`.
    """
    from job_monitor.workers.manager import WorkerState, WorkerStatus

    monkeypatch.setitem(
        manager._statuses,
        "tg",
        WorkerStatus(name="tg", state=WorkerState.running, epoch=5),
    )
    monkeypatch.setitem(
        manager._statuses,
        "hh",
        WorkerStatus(name="hh", state=WorkerState.stopped, epoch=6),
    )

    body = client.get("/api/state", headers=AUTH).json()

    assert body["tg"]["epoch"] == 5
    assert body["hh"]["epoch"] == 6


def test_state_reports_a_run_number_even_for_a_worker_never_started(client):
    """`0` — «в этом процессе ещё не запускался». Поле должно быть числом
    всегда: фронтенд сравнивает номера, а не проверяет их наличие."""
    body = client.get("/api/state", headers=AUTH).json()
    for worker in ("tg", "hh"):
        assert isinstance(body[worker]["epoch"], int), body[worker]
        assert body[worker]["epoch"] >= 0


def test_recent_events_are_returned(client):
    EventsRepo(connection.get_connection()).add("tg", "sent", "@hr_anna", datetime.now())
    body = client.get("/api/state", headers=AUTH).json()
    assert body["recent"][0]["kind"] == "sent"


def test_state_requires_token(raw_client):
    assert raw_client.get("/api/state").status_code == 403


def test_log_parsing_helpers_are_gone():
    from api import tg_routes

    assert not hasattr(tg_routes, "get_found_today")
    assert not hasattr(tg_routes, "get_sent_today")


# ── Лента «последние события» и флаг api_hash_set ─────────────────────
#
# `test_recent_events_are_returned` выше добавляет ОДНО событие и берёт
# `recent[0]`, поэтому сортировка в `api/routes_state.py` тавтологична:
# мутация `reverse=True` → `reverse=False` проходила зелёной, а лента на
# дашборде показывала бы 8 самых СТАРЫХ записей вместо свежих — то есть
# выглядела бы работающей и при этом никогда не менялась.
#
# Флаг `api_hash_set` — та же история с другого конца. Копия этого флага в
# `GET /api/config` закреплена (`test_config_api.py`), а ту, что читает
# фронтенд (`app.js`: `tg.api_hash_set`), не проверял никто: мутация
# `is not None` → `is None` проходила зелёной.


def test_recent_events_are_newest_first(client):
    events = EventsRepo(connection.get_connection())
    events.add("tg", "старое", "@first", datetime(2024, 1, 1, 10, 0))
    events.add("hh", "свежее", "вакансия", datetime(2024, 3, 1, 10, 0))
    events.add("tg", "среднее", "@second", datetime(2024, 2, 1, 10, 0))

    recent = client.get("/api/state", headers=AUTH).json()["recent"]

    assert [event["kind"] for event in recent] == ["свежее", "среднее", "старое"], (
        "лента «последние события» отсортирована не от новых к старым — на "
        "дашборде она показывает самые старые записи и не меняется"
    )


def test_the_recent_feed_mixes_both_workers_and_is_capped(client):
    """Лента собирается из двух источников по 8 записей и режется до 8.
    Без среза дашборд получил бы 16, а срез без сортировки отрезал бы
    именно свежие."""
    events = EventsRepo(connection.get_connection())
    for minute in range(9):
        events.add("tg", f"tg-{minute}", None, datetime(2024, 1, 1, 10, minute))
        events.add("hh", f"hh-{minute}", None, datetime(2024, 1, 1, 11, minute))

    recent = client.get("/api/state", headers=AUTH).json()["recent"]

    assert len(recent) == 8
    # Самые свежие — записи hh (11 часов), и восьми хватает ровно на них.
    assert {event["worker"] for event in recent} == {"hh"}
    assert recent[0]["kind"] == "hh-8"


def test_api_hash_set_is_false_without_credentials(client):
    body = client.get("/api/state", headers=AUTH).json()
    assert body["tg"]["api_hash_set"] is False


def test_api_hash_set_is_true_once_the_hash_is_stored(client, tmp_path):
    (tmp_path / ".env").write_text(
        "TG_API_ID=42\nTG_API_HASH=f00dcafef00dcafef00dcafef00dcafe\n", encoding="utf-8"
    )
    body = client.get("/api/state", headers=AUTH).json()
    assert body["tg"]["api_hash_set"] is True, (
        "флаг «ключи заданы» в /api/state инвертирован — фронтенд читает именно "
        "его и покажет предупреждение о незаданных ключах на настроенном "
        "приложении (или наоборот)"
    )
    assert "f00dcafe" not in client.get("/api/state", headers=AUTH).text, (
        "сам api_hash не должен уезжать в /api/state ни при каком виде флага"
    )


# ── `GET /api/tg/chats` — роут без единого теста ──────────────────────
#
# Мутация `row["preview"] or ""` → `and` проходила зелёной, а ломает она
# оба случая сразу: `preview and ""` даёт `""` там, где превью ЕСТЬ (текст
# теряется), и `None` там, где его нет (`null` вместо строки, а фронтенд
# рисует превью текстом). Значение необязательное по построению —
# `TgRepo.record_send` принимает `preview=None`.


def test_tg_chats_returns_an_empty_string_when_there_is_no_preview(client):
    conn = connection.get_connection()
    TgRepo(conn).record_send("@no_preview", "itvacancykz", None, datetime(2024, 1, 2, 3, 4))
    TgRepo(conn).record_send("@with_preview", "itvacancykz", "Ищем QA",
                             datetime(2024, 1, 2, 3, 5))

    rows = client.get("/api/tg/chats", headers=AUTH).json()
    by_name = {row["username"]: row for row in rows}

    assert by_name["@no_preview"]["preview"] == "", (
        "вместо пустой строки в список чатов уехал null — фронтенд рисует "
        f"превью текстом: {by_name['@no_preview']['preview']!r}"
    )
    assert by_name["@with_preview"]["preview"] == "Ищем QA", (
        "текст превью потерялся — проверка выше стала бы вакуумной"
    )
    assert by_name["@with_preview"]["source"] == "tg"
    assert by_name["@with_preview"]["has_file"] is False


def test_tg_chats_requires_the_token(raw_client):
    assert raw_client.get("/api/tg/chats").status_code == 403
