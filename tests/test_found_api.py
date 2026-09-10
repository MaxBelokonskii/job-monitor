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
