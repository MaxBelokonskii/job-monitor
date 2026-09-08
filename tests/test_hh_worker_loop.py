"""Проверяет, что опечатка в сохранённом сценарии Selenium (hh_selenium_steps)
не роняет цикл HH-воркера: `parse_steps` бросает `ValueError` внутри
`apply_to_vacancy`, а `_process_one` обязан поймать его на уровне цикла,
записать событие в `worker_events`, пометить вакансию отдельным статусом
(не HH_STATUS_APPLIED) и вернуть False, не подняв исключение дальше — а
дедуп в `_blocking_loop` (через `repo.exists()`) не должен снова кликать
по той же вакансии на следующем цикле, пока сценарий не поправят."""

from datetime import datetime

import job_monitor.workers.hh as hh
from job_monitor.settings import AppSettings


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
    def boom(_driver, _vacancy, _settings):
        raise ValueError("неизвестный тип шага: 'execute_script'")

    monkeypatch.setattr(hh, "apply_to_vacancy", boom)

    repo = FakeRepo()
    events = FakeEvents()
    vacancy = {"vacancy_id": "1", "title": "QA", "url": "https://hh.ru/vacancy/1"}

    result = hh._process_one(driver=object(), vacancy=vacancy, settings=AppSettings(),
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


def test_second_pass_over_still_broken_scenario_does_not_reclick(monkeypatch):
    """Finding 2: цикл воркера (`_blocking_loop`) дедуплицирует вакансии через
    `repo.exists()` до вызова `_process_one`. Раз первый провал теперь
    записывается в repo, второй проход по той же вакансии должен пропустить
    её целиком — ни повторного клика по «Откликнуться» на живом hh.ru, ни
    второй записи в worker_events."""
    calls: list[int] = []

    def boom(_driver, _vacancy, _settings):
        calls.append(1)
        raise ValueError("неизвестный тип шага: 'execute_script'")

    monkeypatch.setattr(hh, "apply_to_vacancy", boom)

    repo = FakeRepo()
    events = FakeEvents()
    vacancy = {"vacancy_id": "1", "title": "QA", "url": "https://hh.ru/vacancy/1"}
    settings = AppSettings()

    for _ in range(2):  # имитируем два прохода _blocking_loop за один и тот же цикл поиска
        if repo.exists(vacancy["vacancy_id"]):
            continue
        hh._process_one(driver=object(), vacancy=vacancy, settings=settings,
                         repo=repo, events=events)

    assert calls == [1]  # apply_to_vacancy (клик по кнопке) вызван только один раз
    assert len(events.events) == 1  # второй проход не добавил ещё один steps_invalid
    assert repo.upserted[0]["status"] != hh.HH_STATUS_APPLIED
