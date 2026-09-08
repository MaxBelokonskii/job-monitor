"""Проверяет, что опечатка в сохранённом сценарии Selenium (hh_selenium_steps)
не роняет цикл HH-воркера: `parse_steps` бросает `ValueError` внутри
`apply_to_vacancy`, а `_process_one` обязан поймать его на уровне цикла,
записать событие в `worker_events` и вернуть False, не подняв исключение
дальше и не пометив вакансию как обработанную в БД."""

from datetime import datetime

import job_monitor.workers.hh as hh
from job_monitor.settings import AppSettings


class FakeRepo:
    def __init__(self):
        self.upserted: list[dict] = []

    def upsert(self, row: dict) -> None:
        self.upserted.append(row)


class FakeEvents:
    def __init__(self):
        self.events: list[tuple] = []

    def add(self, worker, kind, detail, now):
        self.events.append((worker, kind, detail, now))


def test_process_one_survives_bad_scenario(monkeypatch):
    def boom(_driver, _vacancy, _settings):
        raise ValueError("неизвестный тип шага: 'execute_script'")

    monkeypatch.setattr(hh, "apply_to_vacancy", boom)

    repo = FakeRepo()
    events = FakeEvents()
    vacancy = {"vacancy_id": "1", "title": "QA", "url": "https://hh.ru/vacancy/1"}

    result = hh._process_one(driver=object(), vacancy=vacancy, settings=AppSettings(),
                              repo=repo, events=events)

    assert result is False
    assert repo.upserted == []  # вакансия не отмечена как обработанная
    assert events.events == [
        ("hh", "steps_invalid", "неизвестный тип шага: 'execute_script'", events.events[0][3]),
    ]
    assert isinstance(events.events[0][3], datetime)
