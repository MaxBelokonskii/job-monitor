"""Очередь найденного через API: чтение, ручные статусы, запреты.

Главное, что здесь проверяется, — не форма ответа, а два запрета. Первый:
«отклик отправлен» руками не ставится, иначе счётчик отправленного
перестаёт быть правдой. Второй: вернуть в очередь уже отправленный отклик
нельзя — «новая» означает «обработать», то есть второй отклик тому же
работодателю.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from job_monitor import statuses
from job_monitor.db.connection import get_connection
from job_monitor.db.repositories import TgFoundRepo, TgRepo

NOW = datetime(2026, 9, 10, 12, 0, 0)


@pytest.fixture
def tg_post(client):
    """Один пост в очереди. `client` уже поднял приложение и базу.

    Соединение берётся тем же `get_connection()`, которым пользуются
    роуты: база у прогона одна (`JOB_MONITOR_DATA_DIR` из conftest), и
    писать в неё в обход приложения незачем.
    """
    conn = get_connection()
    conn.execute("DELETE FROM tg_found")
    conn.execute("DELETE FROM tg_contacts")
    repo = TgFoundRepo(conn)
    repo.record("qajobs", 42, ["@hr_anna", "@lead"], "Ищем QA", "qa", NOW)
    return repo.list()[0]["id"]


def test_the_list_returns_the_post_with_a_ready_made_link(client, tg_post) -> None:
    """Ссылку строит бэкенд: фронтенду незачем знать формат чужих URL, а
    гвард схемы в `el()` остаётся единственной точкой проверки."""
    rows = client.get("/api/found/tg").json()
    assert len(rows) == 1
    assert rows[0]["link"] == "https://t.me/qajobs/42"
    assert rows[0]["usernames"] == ["@hr_anna", "@lead"]
    assert rows[0]["status"] == statuses.NEW
    assert rows[0]["preview"] == "Ищем QA"


def test_the_list_does_not_leak_the_dead_column(client, tg_post) -> None:
    """`tg_found.username` (единственное число) остался от миграции 003 и
    всегда пуст. Отдавать его наружу — значит однажды на него опереться."""
    assert "username" not in client.get("/api/found/tg").json()[0]


def test_the_list_filters_by_status(client, tg_post) -> None:
    assert len(client.get("/api/found/tg?status=new").json()) == 1
    assert client.get("/api/found/tg?status=decided").json() == []

    client.patch(f"/api/found/tg/{tg_post}", json={"status": statuses.DISMISSED})

    assert client.get("/api/found/tg?status=new").json() == []
    assert len(client.get("/api/found/tg?status=decided").json()) == 1


def test_an_unknown_filter_is_refused(client, tg_post) -> None:
    assert client.get("/api/found/tg?status=всякое").status_code == 422


def test_marking_it_dismissed_only_changes_the_status(client, tg_post) -> None:
    """Пост уже дедуплицирован парой (канал, сообщение); контакты в
    `tg_contacts` не попадают — человек может встретиться в другой
    вакансии, которая подойдёт."""
    reply = client.patch(f"/api/found/tg/{tg_post}", json={"status": statuses.DISMISSED})
    assert reply.status_code == 200
    assert reply.json()["status"] == "saved"

    conn = get_connection()
    assert TgFoundRepo(conn).get(tg_post)["status"] == statuses.DISMISSED
    assert TgRepo(conn).contacts_total() == 0


def test_marking_it_answered_registers_every_contact(client, tg_post) -> None:
    """Человек написал сам — воркер больше не должен писать этим людям.
    `ensure_contact` написан ровно для случая «контакт известен, отправки
    не было»."""
    client.patch(f"/api/found/tg/{tg_post}", json={"status": statuses.MANUAL_APPLIED})

    repo = TgRepo(get_connection())
    assert repo.was_sent("@hr_anna") is True
    assert repo.was_sent("@lead") is True


def test_a_manual_answer_does_not_touch_the_daily_counter(client, tg_post) -> None:
    """Решение D16. Записи в `tg_sends` не создаётся, поэтому «отправлено
    сегодня» не растёт — отметка задним числом не должна останавливать
    воркера на весь день."""
    conn = get_connection()
    before = TgRepo(conn).sent_on(datetime.now().date())

    client.patch(f"/api/found/tg/{tg_post}", json={"status": statuses.MANUAL_APPLIED})

    assert TgRepo(conn).sent_on(datetime.now().date()) == before


def test_the_robot_status_cannot_be_set_by_hand(client, tg_post) -> None:
    reply = client.patch(f"/api/found/tg/{tg_post}", json={"status": statuses.AUTO_APPLIED})
    assert reply.status_code == 400
    assert "робот" in reply.json()["detail"]
    assert TgFoundRepo(get_connection()).get(tg_post)["status"] == statuses.NEW


def test_an_unknown_status_is_refused(client, tg_post) -> None:
    reply = client.patch(f"/api/found/tg/{tg_post}", json={"status": "почти откликнулся"})
    assert reply.status_code == 400
    assert TgFoundRepo(get_connection()).get(tg_post)["status"] == statuses.NEW


def test_an_extra_field_is_refused(client, tg_post) -> None:
    """`extra="forbid"`: опечатка в имени поля не должна молча ничего не
    делать и отвечать «сохранено»."""
    reply = client.patch(
        f"/api/found/tg/{tg_post}", json={"status": statuses.DISMISSED, "статус": "ага"}
    )
    assert reply.status_code == 422


def test_a_dismissed_post_can_be_returned_to_the_queue(client, tg_post) -> None:
    """Передумал."""
    client.patch(f"/api/found/tg/{tg_post}", json={"status": statuses.DISMISSED})
    reply = client.patch(f"/api/found/tg/{tg_post}", json={"status": statuses.NEW})
    assert reply.status_code == 200
    assert TgFoundRepo(get_connection()).get(tg_post)["status"] == statuses.NEW


def test_an_answered_post_cannot_be_returned_to_the_queue(client, tg_post) -> None:
    """Самый дорогой запрет подпроекта: «новая» означает «обработать», то
    есть второй отклик тому же работодателю. Для Telegram от этого защищает
    дедупликация контактов, для hh.ru — ничто, поэтому запрет ставится
    здесь, на уровне роута, одинаково для обоих списков."""
    client.patch(f"/api/found/tg/{tg_post}", json={"status": statuses.MANUAL_APPLIED})
    reply = client.patch(f"/api/found/tg/{tg_post}", json={"status": statuses.NEW})
    assert reply.status_code == 400
    assert "отклик уже отправлен" in reply.json()["detail"]
    assert TgFoundRepo(get_connection()).get(tg_post)["status"] == statuses.MANUAL_APPLIED


def test_patching_a_missing_post_is_404(client, tg_post) -> None:
    assert client.patch(
        "/api/found/tg/999999", json={"status": statuses.DISMISSED}
    ).status_code == 404


# ── hh.ru ─────────────────────────────────────────────────────────────

from job_monitor.db.repositories import HhRepo  # noqa: E402


@pytest.fixture
def hh_vacancy(client):
    conn = get_connection()
    conn.execute("DELETE FROM hh_applications")
    HhRepo(conn).record_found({
        "vacancy_id": "1",
        "title": "QA Engineer",
        "company": "ООО Ромашка",
        "salary": "от 400 000 ₸",
        "city": "Алматы",
        "url": "https://hh.ru/vacancy/1",
        "found_at": "2026-09-10T10:00:00",
        "status": statuses.NEW,
        "status_source": statuses.SOURCE_ROBOT,
    })
    return "1"


def test_the_hh_list_carries_everything_the_card_shows(client, hh_vacancy) -> None:
    rows = client.get("/api/found/hh").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["url"] == "https://hh.ru/vacancy/1", "ссылка у hh.ru уже есть в базе"
    assert row["title"] == "QA Engineer"
    assert row["company"] == "ООО Ромашка"
    assert row["city"] == "Алматы"
    assert row["salary"] == "от 400 000 ₸"
    assert row["status"] == statuses.NEW
    assert row["status_source"] == statuses.SOURCE_ROBOT


def test_the_hh_list_filters_by_status(client, hh_vacancy) -> None:
    assert len(client.get("/api/found/hh?status=new").json()) == 1
    client.patch(f"/api/found/hh/{hh_vacancy}", json={"status": statuses.DISMISSED})
    assert client.get("/api/found/hh?status=new").json() == []
    assert len(client.get("/api/found/hh?status=decided").json()) == 1


def test_a_manual_hh_answer_names_the_human(client, hh_vacancy) -> None:
    reply = client.patch(
        f"/api/found/hh/{hh_vacancy}", json={"status": statuses.MANUAL_APPLIED}
    )
    assert reply.status_code == 200
    row = HhRepo(get_connection()).get(hh_vacancy)
    assert row["status"] == statuses.MANUAL_APPLIED
    assert row["status_source"] == statuses.SOURCE_HUMAN


def test_a_manual_hh_answer_stays_out_of_the_counter(client, hh_vacancy) -> None:
    """Решение D16 на самом счётчике. Тест не вакуумен: `set_status`
    ПРОСТАВЛЯЕТ `applied_at` ручному отклику (он нужен для сортировки), так
    что счётчик молчит только благодаря фильтру по статусу."""
    from datetime import date

    client.patch(f"/api/found/hh/{hh_vacancy}", json={"status": statuses.MANUAL_APPLIED})
    repo = HhRepo(get_connection())
    assert repo.get(hh_vacancy)["applied_at"] is not None
    assert repo.applied_on(date.today()) == 0
    assert repo.applied_total() == 0


def test_the_robot_hh_status_cannot_be_set_by_hand(client, hh_vacancy) -> None:
    reply = client.patch(
        f"/api/found/hh/{hh_vacancy}", json={"status": statuses.AUTO_APPLIED}
    )
    assert reply.status_code == 400
    assert HhRepo(get_connection()).get(hh_vacancy)["status"] == statuses.NEW


def test_an_applied_hh_vacancy_cannot_be_returned_to_the_queue(client, hh_vacancy) -> None:
    """Для hh.ru у этого запрета нет второго рубежа: дедупликации контактов,
    которая спасает Telegram, здесь не существует — робот просто кликнул бы
    «Откликнуться» второй раз."""
    HhRepo(get_connection()).set_status(
        hh_vacancy, statuses.AUTO_APPLIED, statuses.SOURCE_ROBOT, datetime.now()
    )
    reply = client.patch(f"/api/found/hh/{hh_vacancy}", json={"status": statuses.NEW})
    assert reply.status_code == 400
    assert HhRepo(get_connection()).get(hh_vacancy)["status"] == statuses.AUTO_APPLIED


def test_a_skipped_hh_vacancy_can_be_returned_to_the_queue(client, hh_vacancy) -> None:
    """«Пропущено» значит «кнопка отклика не нашлась». Клика не было —
    пусть попробует снова."""
    HhRepo(get_connection()).set_status(
        hh_vacancy, statuses.SKIPPED, statuses.SOURCE_ROBOT, datetime.now()
    )
    assert client.patch(
        f"/api/found/hh/{hh_vacancy}", json={"status": statuses.NEW}
    ).status_code == 200
    assert HhRepo(get_connection()).get(hh_vacancy)["status"] == statuses.NEW


def test_a_broken_scenario_cannot_be_returned_to_the_queue(client, hh_vacancy) -> None:
    """Выглядит как «робот не смог», но кнопка уже нажата: сценарий
    ломается ПОСЛЕ клика (см. `_process_one` в workers/hh.py)."""
    HhRepo(get_connection()).set_status(
        hh_vacancy, statuses.SCENARIO_ERROR, statuses.SOURCE_ROBOT, datetime.now()
    )
    reply = client.patch(f"/api/found/hh/{hh_vacancy}", json={"status": statuses.NEW})
    assert reply.status_code == 400
    assert "после клика" in reply.json()["detail"]


def test_patching_a_missing_vacancy_is_404(client, hh_vacancy) -> None:
    assert client.patch(
        "/api/found/hh/нет-такой", json={"status": statuses.DISMISSED}
    ).status_code == 404


def test_the_old_vacancies_route_is_gone(client, hh_vacancy) -> None:
    """Единственным его потребителем был наш же фронтенд. Два роута с одним
    смыслом — это два места, которые разойдутся."""
    assert client.get("/api/hh/vacancies").status_code == 404
