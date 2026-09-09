"""Опечатка в сохранённом сценарии Selenium (hh_selenium_steps) не должна
ронять цикл HH-воркера.

`parse_steps` бросает `ValueError` внутри `apply_to_vacancy`; `_process_one`
обязан поймать его, записать событие в `worker_events`, пометить вакансию
отдельным статусом (не HH_STATUS_APPLIED) и вернуть False, не подняв
исключение дальше. А дедуп в `_blocking_loop` (через `repo.exists()`) не
должен снова кликать по той же вакансии, пока сценарий не поправят.

Второе свойство проверяется на НАСТОЯЩЕМ `_blocking_loop`. Прежняя версия
теста писала цикл сама — `if repo.exists(...): continue` жил в теле теста, —
поэтому удаление той же строки из `job_monitor/workers/hh.py` она бы не
заметила: докстринг обещал пин, которого не было. Проверено: строка снята —
тест падает.
"""

import threading
from datetime import datetime

import pytest

import job_monitor.workers.hh as hh
from job_monitor.db.connection import connect
from job_monitor.db.repositories import HhRepo, SettingsRepo
from job_monitor import presets
from job_monitor.criteria import SearchCriteria
from job_monitor.settings import GlobalSettings


class FakeRepo:
    def __init__(self):
        self.upserted: list[dict] = []
        self._ids: set[str] = set()

    def exists(self, vacancy_id: str) -> bool:
        return vacancy_id in self._ids

    def upsert(self, row: dict) -> None:
        self.upserted.append(row)
        self._ids.add(row["vacancy_id"])


class FakeEvents:
    def __init__(self):
        self.events: list[tuple] = []

    def add(self, worker, kind, detail, now):
        self.events.append((worker, kind, detail, now))


def test_process_one_survives_bad_scenario(monkeypatch):
    def boom(_driver, _vacancy, _criteria, _settings):
        raise ValueError("неизвестный тип шага: 'execute_script'")

    monkeypatch.setattr(hh, "apply_to_vacancy", boom)

    repo = FakeRepo()
    events = FakeEvents()
    vacancy = {"vacancy_id": "1", "title": "QA", "url": "https://hh.ru/vacancy/1"}

    result = hh._process_one(driver=object(), vacancy=vacancy,
                              criteria=SearchCriteria(), settings=GlobalSettings(),
                              repo=repo, events=events)

    assert result is False
    # Finding 2: вакансия ВСЁ ЖЕ отмечается в БД (не пропускается upsert),
    # но не статусом "применено" — чтобы дедуп в _blocking_loop её больше
    # не трогал, а причина была видна на дашборде.
    assert len(repo.upserted) == 1
    assert repo.upserted[0]["status"] == hh.HH_STATUS_SCENARIO_ERROR
    assert repo.upserted[0]["status"] != hh.HH_STATUS_APPLIED
    assert repo.upserted[0]["error"] == "неизвестный тип шага: 'execute_script'"
    assert events.events == [
        ("hh", "steps_invalid", "неизвестный тип шага: 'execute_script'", events.events[0][3]),
    ]
    assert isinstance(events.events[0][3], datetime)


class FakeDriver:
    """Ровно то, что от драйвера просит `_blocking_loop`."""

    def __init__(self) -> None:
        self.visited: list[str] = []
        self.quit_called = False

    def get(self, url: str) -> None:
        self.visited.append(url)

    def quit(self) -> None:
        self.quit_called = True


@pytest.fixture
def broken_scenario_loop(monkeypatch):
    """Настоящий `_blocking_loop` со всем, что ходит наружу, заменённым.

    Настройки, база, репозитории и сам порядок обхода — живые: подменены
    только Selenium (`setup_driver`, `load_cookies`, `is_logged_in`,
    `build_search_url`, `get_vacancies_from_page`, `apply_to_vacancy`) и сон
    между вакансиями.
    """
    conn = connect(":memory:")
    settings = GlobalSettings(hh_delay_min=1, hh_delay_max=1)
    SettingsRepo(conn).save(settings.model_dump())
    # Профессии переехали в пресет: две — чтобы цикл прошёл по одной и той же
    # вакансии дважды и дедуп было на чём проверить.
    presets.save_criteria(
        conn,
        presets.ensure_default(conn, datetime(2026, 9, 9, 12, 0, 0)),
        {"professions": ["QA", "тестировщик"]},
    )
    monkeypatch.setattr("job_monitor.db.connection.connect", lambda *_a, **_k: conn)

    clicks: list[dict] = []

    def boom(_driver, vacancy, _criteria, _settings):
        clicks.append(vacancy)
        raise ValueError("неизвестный тип шага: 'execute_script'")

    # Форма ровно та, что отдаёт настоящий `get_vacancies_from_page`, включая
    # `found_at`: колонка NOT NULL, и без неё upsert падает на IntegrityError
    # прямо в `_process_one` — вакансия не попадает в базу, дедуп не
    # срабатывает, и цикл кликает по ней снова каждую минуту. Прежний тест с
    # самодельным FakeRepo этого не увидел бы вовсе.
    vacancy = {
        "vacancy_id": "1",
        "title": "QA Engineer",
        "company": "ООО Ромашка",
        "salary": "Не указана",
        "city": "Алматы",
        "url": "https://hh.ru/vacancy/1",
        "found_at": "2026-09-09T10:00:00",
    }
    driver = FakeDriver()

    monkeypatch.setattr(hh, "apply_to_vacancy", boom)
    monkeypatch.setattr(hh, "setup_driver", lambda headless=False: driver)
    monkeypatch.setattr(hh, "load_cookies", lambda _driver, _target: True)
    monkeypatch.setattr(hh, "is_logged_in", lambda _driver: True)
    monkeypatch.setattr(
        hh, "build_search_url", lambda profession, *_a, **_k: f"https://hh.ru/{profession}"
    )
    monkeypatch.setattr(
        hh, "get_vacancies_from_page", lambda _driver, _criteria: [dict(vacancy)]
    )

    slept: list[float] = []

    def fake_sleep(stop_event: threading.Event, seconds: float) -> bool:
        slept.append(seconds)
        # Пауза длиной с hh_check_interval — конец полного круга поиска:
        # дальше цикл пошёл бы на второй заход, а нам хватит одного.
        if seconds == settings.hh_check_interval:
            stop_event.set()
            return False
        return True

    monkeypatch.setattr(hh, "_interruptible_sleep", fake_sleep)

    hh._blocking_loop(threading.Event())

    return {"conn": conn, "clicks": clicks, "driver": driver, "slept": slept}


def test_the_loop_clicks_a_broken_vacancy_once_and_then_skips_it(broken_scenario_loop):
    """Дедуп `repo.exists()` живёт в `_blocking_loop`, до вызова
    `_process_one`. Первый провал уже записан в базу, поэтому второй проход
    по той же вакансии обязан пропустить её целиком: ни повторного клика по
    «Откликнуться» на живом hh.ru, ни второй записи в worker_events."""
    assert len(broken_scenario_loop["clicks"]) == 1, (
        "по вакансии кликнули больше одного раза — дедуп в _blocking_loop не сработал: "
        f"{broken_scenario_loop['clicks']}"
    )
    assert broken_scenario_loop["clicks"][0]["vacancy_id"] == "1"

    conn = broken_scenario_loop["conn"]
    events = conn.execute(
        "SELECT kind, detail FROM worker_events WHERE worker = 'hh'"
    ).fetchall()
    assert [row["kind"] for row in events] == ["steps_invalid"]
    assert events[0]["detail"] == "неизвестный тип шага: 'execute_script'"

    stored = HhRepo(conn).recent(10)
    assert len(stored) == 1
    assert stored[0]["status"] == hh.HH_STATUS_SCENARIO_ERROR


def test_the_loop_really_walked_both_keywords(broken_scenario_loop):
    """Страховка от вакуумности: если бы второй проход не состоялся,
    предыдущий тест был бы зелёным ни о чём."""
    assert broken_scenario_loop["driver"].visited == [
        "https://hh.ru/QA", "https://hh.ru/тестировщик",
    ]
    assert broken_scenario_loop["driver"].quit_called, "драйвер не закрыт в finally"


def test_the_loop_pauses_only_after_a_vacancy_it_actually_touched(broken_scenario_loop):
    """Пропущенная по дедупу вакансия не должна стоить паузы между
    откликами: иначе воркер тратил бы минуты на уже обработанное."""
    settings_interval = GlobalSettings().hh_check_interval
    assert broken_scenario_loop["slept"] == [1, settings_interval]
