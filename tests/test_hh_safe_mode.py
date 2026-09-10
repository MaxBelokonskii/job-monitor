"""hh-воркер: очередь наполняется, безопасный режим наконец действует.

Три свойства, каждое из которых до этой задачи отсутствовало:

1. Найденная вакансия попадает в базу СРАЗУ, а не после попытки отклика —
   иначе очередь, которую показывает интерфейс, всегда пуста.
2. `safe_mode` читает и hh-воркер (решение D17). До этого он читал его
   только TG-воркер, то есть режима «найти и не откликаться» для hh.ru не
   существовало вовсе, а без него ручной отклик невозможен по построению:
   робот кликает раньше, чем человек увидит вакансию.
3. Дедупликация идёт по РЕШЁННОСТИ статуса, а не по наличию строки
   (решение D19) — прямое следствие пункта 1.

Всё проверяется на настоящем `_blocking_loop`: подменены только Selenium и
сон между вакансиями.
"""

from __future__ import annotations

import threading
from datetime import date, datetime

import pytest

import job_monitor.workers.hh as hh
from job_monitor import presets, statuses
from job_monitor.db.connection import connect
from job_monitor.db.repositories import HhRepo, SettingsRepo
from job_monitor.settings import GlobalSettings


class FakeDriver:
    """Ровно то, что от драйвера просит `_blocking_loop`."""

    def __init__(self) -> None:
        self.visited: list[str] = []
        self.quit_called = False

    def get(self, url: str) -> None:
        self.visited.append(url)

    def quit(self) -> None:
        self.quit_called = True


def _vacancy(vacancy_id: str, title: str) -> dict:
    """Форма ровно та, что отдаёт настоящий `get_vacancies_from_page`."""
    return {
        "vacancy_id": vacancy_id,
        "title": title,
        "company": "ООО Ромашка",
        "salary": "Не указана",
        "city": "Алматы",
        "url": f"https://hh.ru/vacancy/{vacancy_id}",
        "found_at": "2026-09-10T10:00:00",
    }


def _run_loop(monkeypatch, *, safe_mode: bool, vacancies: list[dict],
              professions: list[str], conn=None, settings_patch: dict | None = None) -> dict:
    conn = conn if conn is not None else connect(":memory:")
    settings = GlobalSettings(
        hh_delay_min=1, hh_delay_max=1, safe_mode=safe_mode, **(settings_patch or {})
    )
    SettingsRepo(conn).save(settings.model_dump())
    presets.save_criteria(
        conn,
        presets.ensure_default(conn, datetime(2026, 9, 10, 12, 0, 0)),
        {"professions": professions},
    )
    monkeypatch.setattr("job_monitor.db.connection.connect", lambda *_a, **_k: conn)

    clicks: list[dict] = []

    def apply(_driver, vacancy, _criteria, _settings) -> bool:
        clicks.append(vacancy)
        return True

    driver = FakeDriver()
    monkeypatch.setattr(hh, "apply_to_vacancy", apply)
    monkeypatch.setattr(hh, "setup_driver", lambda headless=False: driver)
    monkeypatch.setattr(hh, "load_cookies", lambda _driver, _target: True)
    monkeypatch.setattr(hh, "is_logged_in", lambda _driver: True)
    monkeypatch.setattr(
        hh, "build_search_url", lambda profession, *_a, **_k: f"https://hh.ru/{profession}"
    )
    monkeypatch.setattr(
        hh, "get_vacancies_from_page",
        lambda _driver, _criteria: [dict(item) for item in vacancies],
    )

    slept: list[float] = []

    def fake_sleep(stop_event: threading.Event, seconds: float) -> bool:
        slept.append(seconds)
        # Пауза длиной с hh_check_interval — конец полного круга поиска.
        # Пауза лимита (600) тоже завершает прогон: дальше цикл крутился бы
        # вечно, ничего не меняя.
        if seconds in (settings.hh_check_interval, 600):
            stop_event.set()
            return False
        return True

    monkeypatch.setattr(hh, "_interruptible_sleep", fake_sleep)
    hh._blocking_loop(threading.Event())
    return {"conn": conn, "clicks": clicks, "driver": driver, "slept": slept}


# ── Безопасный режим ──────────────────────────────────────────────────


@pytest.fixture
def safe_run(monkeypatch):
    return _run_loop(
        monkeypatch, safe_mode=True,
        vacancies=[_vacancy("1", "QA Engineer")], professions=["QA"],
    )


def test_safe_mode_fills_the_queue(safe_run) -> None:
    rows = HhRepo(safe_run["conn"]).list_found()
    assert len(rows) == 1
    assert rows[0]["vacancy_id"] == "1"
    assert rows[0]["status"] == statuses.NEW
    assert rows[0]["status_source"] == statuses.SOURCE_ROBOT


def test_safe_mode_sends_nothing(safe_run) -> None:
    """Смысл режима одной строкой: ищем и складываем, наружу не уходит
    ничего."""
    assert safe_run["clicks"] == []
    assert HhRepo(safe_run["conn"]).applied_on(date.today()) == 0


def test_safe_mode_really_walked_the_search_page(safe_run) -> None:
    """Страховка от вакуумности: если бы цикл не дошёл до страницы поиска,
    обе проверки выше были бы зелёными ни о чём."""
    assert safe_run["driver"].visited == ["https://hh.ru/QA"]
    assert safe_run["driver"].quit_called, "драйвер не закрыт в finally"


def test_safe_mode_does_not_pause_between_vacancies(safe_run) -> None:
    """Пауза существует, чтобы не долбить hh.ru откликами. Отклика не было —
    платить минутами не за что."""
    assert safe_run["slept"] == [GlobalSettings().hh_check_interval]


def test_safe_mode_keeps_collecting_after_the_daily_limit(monkeypatch) -> None:
    """Лимит защищает аккаунт от бана за ОТПРАВКУ. В безопасном режиме
    отправки нет, и уснуть на десять минут значило бы перестать наполнять
    очередь — то есть отменить ровно то, ради чего режим включают."""
    conn = connect(":memory:")
    HhRepo(conn).record_found({
        "vacancy_id": "уже-отправлена", "title": "QA", "found_at": "2026-09-10T09:00:00",
        "applied_at": datetime.now().isoformat(timespec="seconds"),
        "status": statuses.AUTO_APPLIED, "status_source": statuses.SOURCE_ROBOT,
    })
    run = _run_loop(
        monkeypatch, safe_mode=True, conn=conn,
        vacancies=[_vacancy("1", "QA Engineer")], professions=["QA"],
        settings_patch={"hh_max_per_day": 1},
    )
    assert HhRepo(run["conn"]).get("1") is not None, (
        "воркер уснул на лимите вместо того, чтобы собирать"
    )
    assert 600 not in run["slept"], "сон ожидания лимита в безопасном режиме не нужен"


def test_the_limit_still_stops_a_sending_worker(monkeypatch) -> None:
    """Обратная сторона: без безопасного режима лимит обязан работать —
    иначе предыдущая проверка означала бы, что его просто сняли."""
    conn = connect(":memory:")
    HhRepo(conn).record_found({
        "vacancy_id": "уже-отправлена", "title": "QA", "found_at": "2026-09-10T09:00:00",
        "applied_at": datetime.now().isoformat(timespec="seconds"),
        "status": statuses.AUTO_APPLIED, "status_source": statuses.SOURCE_ROBOT,
    })
    run = _run_loop(
        monkeypatch, safe_mode=False, conn=conn,
        vacancies=[_vacancy("1", "QA Engineer")], professions=["QA"],
        settings_patch={"hh_max_per_day": 1},
    )
    assert run["clicks"] == []
    assert 600 in run["slept"], "лимит достигнут, а воркер не ушёл ждать"


# ── Обычный режим ─────────────────────────────────────────────────────


@pytest.fixture
def live_run(monkeypatch):
    return _run_loop(
        monkeypatch, safe_mode=False,
        vacancies=[_vacancy("1", "QA Engineer")], professions=["QA"],
    )


def test_without_safe_mode_the_worker_applies(live_run) -> None:
    assert [item["vacancy_id"] for item in live_run["clicks"]] == ["1"]
    row = HhRepo(live_run["conn"]).get("1")
    assert row["status"] == statuses.AUTO_APPLIED
    assert row["status_source"] == statuses.SOURCE_ROBOT


# ── Дедупликация по решённости ────────────────────────────────────────


def test_a_new_vacancy_is_not_skipped_by_its_own_queue_row(monkeypatch) -> None:
    """Сердце решения D19.

    Две профессии — значит цикл проходит по одной и той же вакансии дважды.
    К моменту второго прохода строка в базе уже ЕСТЬ: её положил первый
    проход в момент находки. Дедуп «строка есть → пропустить» отбросил бы
    вакансию, по которой ничего не решено, и отклик не ушёл бы никогда.
    """
    run = _run_loop(
        monkeypatch, safe_mode=True,
        vacancies=[_vacancy("1", "QA Engineer")],
        professions=["QA", "тестировщик"],
    )
    assert run["driver"].visited == ["https://hh.ru/QA", "https://hh.ru/тестировщик"]
    rows = HhRepo(run["conn"]).list_found()
    assert len(rows) == 1, "вакансия записана один раз, а не по разу на профессию"
    assert rows[0]["status"] == statuses.NEW, (
        "статус остался «новой» — второй проход не должен был ничего решать"
    )


def test_what_safe_mode_collected_is_applied_to_once_it_is_turned_off(monkeypatch) -> None:
    """Главный сценарий решения D19, и самый дорогой.

    Человек включает безопасный режим, за день накапливает очередь, потом
    выключает его и ждёт откликов. Дедуп «строка есть → пропустить» в этот
    момент отбрасывает ВСЁ собранное — вакансии уже лежат в базе, — и робот
    не отправляет ни одного отклика. Никогда. Молча: ни исключения, ни
    строчки в журнале, просто ничего не происходит.

    Проверка идёт двумя прогонами настоящего цикла на одном соединении:
    первый собирает, второй отправляет. Один прогон это свойство не ловит —
    в пределах одного прогона `exists()` и `is_decided()` неотличимы.
    """
    conn = connect(":memory:")
    collected = _run_loop(
        monkeypatch, safe_mode=True, conn=conn,
        vacancies=[_vacancy("1", "QA Engineer")], professions=["QA"],
    )
    assert collected["clicks"] == []
    assert HhRepo(conn).get("1")["status"] == statuses.NEW

    sending = _run_loop(
        monkeypatch, safe_mode=False, conn=conn,
        vacancies=[_vacancy("1", "QA Engineer")], professions=["QA"],
    )
    assert [item["vacancy_id"] for item in sending["clicks"]] == ["1"], (
        "собранное в безопасном режиме не было отправлено после его "
        "выключения — дедуп идёт по наличию строки вместо решённости"
    )
    assert HhRepo(conn).get("1")["status"] == statuses.AUTO_APPLIED


def test_the_robot_takes_the_credit_back_when_it_applies(monkeypatch) -> None:
    """`upsert` — слияние, поэтому источник надо проставлять явно.

    Человек вернул вакансию в очередь: статус «новая», источник «человек».
    Робот её обрабатывает и отправляет отклик. Без явной подписи в
    `_process_one` строка осталась бы помеченной человеком — и карточка
    сказала бы «отклик отправлен · вручную» про то, что отправил робот.
    """
    conn = connect(":memory:")
    repo = HhRepo(conn)
    repo.record_found(_vacancy("1", "QA Engineer"))
    repo.set_status("1", statuses.NEW, statuses.SOURCE_HUMAN, datetime.now())
    assert repo.get("1")["status_source"] == statuses.SOURCE_HUMAN

    run = _run_loop(
        monkeypatch, safe_mode=False, conn=conn,
        vacancies=[_vacancy("1", "QA Engineer")], professions=["QA"],
    )
    assert len(run["clicks"]) == 1
    row = HhRepo(conn).get("1")
    assert row["status"] == statuses.AUTO_APPLIED
    assert row["status_source"] == statuses.SOURCE_ROBOT, (
        "отклик отправил робот, а строка подписана человеком"
    )


def test_a_decided_vacancy_is_skipped(monkeypatch) -> None:
    """Обратная сторона: по вакансии, где решение уже есть, второго клика
    быть не должно."""
    run = _run_loop(
        monkeypatch, safe_mode=False,
        vacancies=[_vacancy("1", "QA Engineer")],
        professions=["QA", "тестировщик"],
    )
    assert len(run["clicks"]) == 1, (
        f"по вакансии кликнули больше одного раза: {run['clicks']}"
    )


def test_a_manually_dismissed_vacancy_is_never_touched(monkeypatch) -> None:
    """Ради чего всё и делается: человек сказал «не подходит», и робот
    больше не откликается."""
    conn = connect(":memory:")
    HhRepo(conn).record_found({
        **_vacancy("1", "QA Engineer"),
        "status": statuses.DISMISSED,
        "status_source": statuses.SOURCE_HUMAN,
    })
    run = _run_loop(
        monkeypatch, safe_mode=False, conn=conn,
        vacancies=[_vacancy("1", "QA Engineer")], professions=["QA"],
    )
    assert run["clicks"] == []
    assert HhRepo(run["conn"]).get("1")["status"] == statuses.DISMISSED


def test_the_found_event_fires_once_per_vacancy(monkeypatch) -> None:
    """Событие «найдено» нужно ленте последних действий, особенно в
    безопасном режиме, где больше ничего не происходит. Но повторный проход
    по той же вакансии не должен засорять ленту — иначе за сутки лента
    состоит из одной вакансии, переоткрытой сто раз."""
    run = _run_loop(
        monkeypatch, safe_mode=True,
        vacancies=[_vacancy("1", "QA Engineer")],
        professions=["QA", "тестировщик"],
    )
    kinds = [
        row["kind"]
        for row in run["conn"].execute(
            "SELECT kind FROM worker_events WHERE worker = 'hh'"
        ).fetchall()
    ]
    assert kinds == ["found"]
