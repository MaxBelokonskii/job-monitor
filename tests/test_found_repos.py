"""Очередь найденного на уровне репозиториев.

Две таблицы, один словарь статусов. Проверяется то, на чём стоит вся
остальная задача: строка появляется в момент находки, повторная находка её
не переписывает, а решённость отвечает за дедупликацию.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from job_monitor import statuses
from job_monitor.db.connection import connect
from job_monitor.db.repositories import HhRepo, TgFoundRepo

NOW = datetime(2026, 9, 10, 12, 0, 0)


@pytest.fixture
def conn():
    return connect(":memory:")


def _vacancy(vacancy_id: str = "1", **extra) -> dict:
    """Форма ровно та, что отдаёт настоящий `get_vacancies_from_page`."""
    return {
        "vacancy_id": vacancy_id,
        "title": "QA Engineer",
        "company": "ООО Ромашка",
        "salary": "Не указана",
        "city": "Алматы",
        "url": f"https://hh.ru/vacancy/{vacancy_id}",
        "found_at": "2026-09-10T10:00:00",
        "status": statuses.NEW,
        "status_source": statuses.SOURCE_ROBOT,
        **extra,
    }


# ── hh.ru ─────────────────────────────────────────────────────────────


def test_record_found_inserts_a_new_vacancy(conn) -> None:
    repo = HhRepo(conn)
    assert repo.record_found(_vacancy()) is True
    row = repo.get("1")
    assert row["status"] == statuses.NEW
    assert row["status_source"] == statuses.SOURCE_ROBOT
    assert row["applied_at"] is None


def test_record_found_does_not_touch_an_existing_row(conn) -> None:
    """Иначе `found_at` переписывался бы на каждом круге поиска, и вакансия,
    найденная вчера и всё ещё новая, каждый день считалась бы найденной
    заново — счётчик «найдено сегодня» перестал бы что-либо значить."""
    repo = HhRepo(conn)
    repo.record_found(_vacancy())
    assert repo.record_found(_vacancy(found_at="2026-09-11T10:00:00")) is False
    assert repo.get("1")["found_at"] == "2026-09-10T10:00:00"


def test_record_found_does_not_resurrect_a_decided_vacancy(conn) -> None:
    """Самое дорогое свойство этого метода: перезапись статуса решённой
    вакансии на «новую» означала бы второй отклик тому же работодателю."""
    repo = HhRepo(conn)
    repo.record_found(_vacancy())
    repo.set_status("1", statuses.AUTO_APPLIED, statuses.SOURCE_ROBOT, NOW)
    repo.record_found(_vacancy())
    assert repo.get("1")["status"] == statuses.AUTO_APPLIED


def test_a_caller_that_knows_nothing_of_the_new_column_still_works(conn) -> None:
    """`status_source` объявлен `NOT NULL`, а умолчание SQLite срабатывает
    только когда колонки нет в списке `INSERT`. Наши запросы перечисляют
    все поля поимённо, поэтому без подстановки каждый существующий
    вызывающий — `legacy_import.py` и десяток тестов — начал бы падать на
    `IntegrityError`."""
    repo = HhRepo(conn)
    repo.upsert({
        "vacancy_id": "9", "title": "QA", "status": statuses.AUTO_APPLIED,
        "found_at": "2026-09-10T10:00:00",
    })
    assert repo.get("9")["status_source"] == statuses.SOURCE_ROBOT


def test_is_decided_is_false_for_a_freshly_found_vacancy(conn) -> None:
    repo = HhRepo(conn)
    repo.record_found(_vacancy())
    assert repo.exists("1") is True, "строка есть"
    assert repo.is_decided("1") is False, "но решения по ней ещё нет"


@pytest.mark.parametrize("status", sorted(statuses.DECIDED))
def test_is_decided_is_true_for_every_decided_status(conn, status: str) -> None:
    repo = HhRepo(conn)
    repo.record_found(_vacancy())
    repo.set_status("1", status, statuses.SOURCE_ROBOT, NOW)
    assert repo.is_decided("1") is True


def test_is_decided_is_false_for_an_unknown_vacancy(conn) -> None:
    assert HhRepo(conn).is_decided("нет такой") is False


def test_set_status_stamps_applied_at_only_for_an_applied_status(conn) -> None:
    """`applied_at` нужен, чтобы список отправленного шёл в правильном
    порядке. На счётчик он не влияет: `applied_on` фильтрует ещё и по
    статусу — именно это делает решение D16 свойством данных, а не
    отдельной проверкой."""
    repo = HhRepo(conn)
    repo.record_found(_vacancy())
    repo.set_status("1", statuses.MANUAL_APPLIED, statuses.SOURCE_HUMAN, NOW)
    row = repo.get("1")
    assert row["applied_at"] == NOW.isoformat(timespec="seconds")
    assert row["status_source"] == statuses.SOURCE_HUMAN

    repo.record_found(_vacancy("2"))
    repo.set_status("2", statuses.DISMISSED, statuses.SOURCE_HUMAN, NOW)
    assert repo.get("2")["applied_at"] is None


def test_a_manual_application_stays_out_of_the_daily_counter(conn) -> None:
    """Прямая проверка решения D16 на самом счётчике, а не на его форме."""
    repo = HhRepo(conn)
    repo.record_found(_vacancy())
    repo.set_status("1", statuses.MANUAL_APPLIED, statuses.SOURCE_HUMAN, NOW)
    assert repo.applied_on(NOW.date()) == 0
    assert repo.applied_total() == 0

    repo.record_found(_vacancy("2"))
    repo.set_status("2", statuses.AUTO_APPLIED, statuses.SOURCE_ROBOT, NOW)
    assert repo.applied_on(NOW.date()) == 1


def test_set_status_reports_whether_it_found_the_row(conn) -> None:
    repo = HhRepo(conn)
    repo.record_found(_vacancy())
    assert repo.set_status("1", statuses.DISMISSED, statuses.SOURCE_HUMAN, NOW) is True
    assert repo.set_status("нет", statuses.DISMISSED, statuses.SOURCE_HUMAN, NOW) is False


def test_list_found_filters_by_decidedness(conn) -> None:
    repo = HhRepo(conn)
    repo.record_found(_vacancy("1"))
    repo.record_found(_vacancy("2"))
    repo.set_status("2", statuses.DISMISSED, statuses.SOURCE_HUMAN, NOW)

    assert [row["vacancy_id"] for row in repo.list_found(decided=False)] == ["1"]
    assert [row["vacancy_id"] for row in repo.list_found(decided=True)] == ["2"]
    assert {row["vacancy_id"] for row in repo.list_found()} == {"1", "2"}


def test_list_found_pages(conn) -> None:
    repo = HhRepo(conn)
    for index in range(5):
        repo.record_found(_vacancy(str(index), found_at=f"2026-09-1{index}T10:00:00"))
    page = repo.list_found(limit=2, offset=2)
    assert len(page) == 2
    assert [row["vacancy_id"] for row in repo.list_found(limit=2)] == ["4", "3"], (
        "список идёт от свежего к старому"
    )


# ── Telegram ──────────────────────────────────────────────────────────


def test_record_stores_every_contact_of_the_post(conn) -> None:
    """Гранулярность статуса — пост целиком, а контактов в нём бывает
    несколько; без них непонятно, кому писать."""
    repo = TgFoundRepo(conn)
    assert repo.record("qajobs", 42, ["@hr_anna", "@lead"], "ищем QA", "qa", NOW) is True
    row = repo.get(1)
    assert row["usernames"] == ["@hr_anna", "@lead"], "список, а не JSON-строка"
    assert row["status"] == statuses.NEW
    assert row["status_at"] is None


def test_record_accepts_a_post_without_contacts(conn) -> None:
    repo = TgFoundRepo(conn)
    repo.record("qajobs", 42, [], "ищем QA", "qa", NOW)
    assert repo.get(1)["usernames"] == []


def test_the_same_post_is_recorded_once(conn) -> None:
    repo = TgFoundRepo(conn)
    assert repo.record("qajobs", 42, ["@a"], "первый", "qa", NOW) is True
    assert repo.record("qajobs", 42, ["@b"], "второй", "qa", NOW) is False
    assert repo.get(1)["preview"] == "первый"


def test_set_status_stamps_the_time(conn) -> None:
    repo = TgFoundRepo(conn)
    repo.record("qajobs", 42, ["@a"], "ищем QA", "qa", NOW)
    assert repo.set_status(1, statuses.DISMISSED, NOW) is True
    row = repo.get(1)
    assert row["status"] == statuses.DISMISSED
    assert row["status_at"] == NOW.isoformat(timespec="seconds")
    assert repo.set_status(999, statuses.DISMISSED, NOW) is False


def test_tg_list_filters_by_decidedness(conn) -> None:
    repo = TgFoundRepo(conn)
    repo.record("qajobs", 1, [], "первый", "qa", NOW)
    repo.record("qajobs", 2, [], "второй", "qa", NOW)
    repo.set_status(2, statuses.MANUAL_APPLIED, NOW)

    assert [row["message_id"] for row in repo.list(decided=False)] == [1]
    assert [row["message_id"] for row in repo.list(decided=True)] == [2]
    assert len(repo.list()) == 2


def test_tg_list_pages_newest_first(conn) -> None:
    repo = TgFoundRepo(conn)
    for index in range(1, 6):
        repo.record("qajobs", index, [], f"пост {index}", "qa", NOW)
    assert [row["message_id"] for row in repo.list(limit=2)] == [5, 4]
    assert [row["message_id"] for row in repo.list(limit=2, offset=2)] == [3, 2]
