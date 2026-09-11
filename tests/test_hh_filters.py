"""Фильтры отбора вакансий на стороне hh.ru.

Симметричные правила для Telegram покрыты образцово: `test_telegram_worker.py`
гоняет настоящие `post_matches`/`is_eligible`/`process_post`, и та же мутация
(инверсия «исключить») даёт там пять красных. На стороне hh.ru предикаты живут
внутри Selenium-функций, и мутационный аудит нашёл, что их не проверяет
ничего:

    hh.py: `if any(ex in title_lower for ex in exclude)` → `if not any(...)`
    hh.py: пропуск «уже откликнулись / отклик отправлен» удалён

Обе проходили зелёными на всём наборе. Цена конкретна и не про интерфейс:
первая заставляет воркер откликаться РОВНО на те вакансии, которые
пользователь внёс в «исключить» (`senior`, `lead`, `middle`), и игнорировать
все остальные; вторая — повторно откликаться там, где отклик уже отправлен,
то есть поведение, за которое hh.ru ограничивает аккаунт.

Браузер тут не нужен: сами предикаты — чистая логика над строкой, а Selenium
вокруг них подменяется двойником, который отвечает ровно на те вызовы, что
функции делают. Ни один тест здесь не поднимает Chrome и не ходит в сеть.
"""

from __future__ import annotations

import pytest
from selenium.common.exceptions import NoSuchElementException

import job_monitor.workers.hh as hh
from job_monitor.criteria import SearchCriteria
from job_monitor.settings import GlobalSettings


class FakeElement:
    """Элемент страницы: текст, атрибуты и вложенные элементы по XPath."""

    def __init__(self, text: str = "", attributes: dict | None = None,
                 children: dict | None = None):
        self.text = text
        self._attributes = attributes or {}
        self._children = children or {}
        self.clicks = 0

    def get_attribute(self, name: str):
        return self._attributes.get(name)

    def find_element(self, by, selector):
        for fragment, element in self._children.items():
            if fragment in selector:
                return element
        raise NoSuchElementException(selector)

    def click(self):
        self.clicks += 1

    def clear(self):
        pass

    def send_keys(self, *_args):
        pass

    def is_displayed(self):
        return True

    def is_enabled(self):
        return True


class FakeSearchDriver:
    """Страница результатов поиска hh.ru."""

    def __init__(self, items: list[FakeElement]):
        self._items = items
        self.visited: list[str] = []

    def get(self, url):
        self.visited.append(url)

    def find_element(self, by, selector):
        # `EC.presence_of_element_located` на контейнере результатов.
        if "vacancy-serp__results" in selector:
            return FakeElement()
        raise NoSuchElementException(selector)

    def find_elements(self, by, selector):
        if "vacancy-serp__vacancy" in selector:
            return self._items
        return []


def _vacancy_card(title: str, vacancy_id: str = "1") -> FakeElement:
    return FakeElement(children={
        "serp-item__title": FakeElement(
            text=title,
            attributes={"href": f"https://hh.ru/vacancy/{vacancy_id}?from=serp"},
        ),
    })


# ── Фильтр «исключить» ────────────────────────────────────────────────


def test_an_excluded_title_is_dropped_and_a_matching_one_is_kept():
    settings = SearchCriteria(hh_exclude=["senior", "lead", "middle"])
    driver = FakeSearchDriver([
        _vacancy_card("Senior QA Engineer", "10"),
        _vacancy_card("QA инженер (junior)", "11"),
        _vacancy_card("Team Lead QA", "12"),
        _vacancy_card("Тестировщик ПО", "13"),
    ])

    found = hh.get_vacancies_from_page(driver, settings)

    assert [vacancy["vacancy_id"] for vacancy in found] == ["11", "13"], (
        "фильтр «исключить» отбирает не то: воркер начнёт откликаться ровно на "
        f"вакансии из списка исключений — {[v['title'] for v in found]}"
    )


@pytest.mark.parametrize("excluded, title", [
    ("senior", "SENIOR QA"),        # регистр приходит со стороны hh.ru
    ("Senior", "senior qa"),        # регистр приходит со стороны пользователя
    ("SENIOR", "Senior QA"),
])
def test_the_exclusion_is_case_insensitive_from_both_sides(excluded, title):
    """Слово в настройки пользователь вписывает руками — как получится
    («Senior», «senior», «SENIOR»), — а заголовок приходит от hh.ru в его
    собственном регистре. Приводить к нижнему нужно ОБЕ стороны: приведи
    только заголовок, и список «исключить», набранный с большой буквы,
    молча перестанет действовать.
    """
    settings = SearchCriteria(hh_exclude=[excluded])
    driver = FakeSearchDriver([_vacancy_card(title, "20")])
    assert hh.get_vacancies_from_page(driver, settings) == []


def test_an_empty_exclude_list_drops_nothing():
    """Обратная сторона: фильтр не должен выбрасывать всё подряд — иначе
    проверки выше остались бы зелёными и при пустом результате."""
    settings = SearchCriteria(hh_exclude=[])
    driver = FakeSearchDriver([
        _vacancy_card("Senior QA", "30"),
        _vacancy_card("QA", "31"),
    ])
    assert [vacancy["vacancy_id"] for vacancy in hh.get_vacancies_from_page(driver, settings)] \
        == ["30", "31"]


def test_the_vacancy_id_comes_from_the_url_without_query(monkeypatch):
    """Соседний инвариант, который иначе некому проверить: ключ дедупа.

    `HhRepo.is_decided()`/`record_found()` ждут `vacancy_id`; ошибись здесь — и
    приложение начнёт откликаться на одни и те же вакансии по кругу.
    """
    driver = FakeSearchDriver([_vacancy_card("QA", "987654")])
    found = hh.get_vacancies_from_page(driver, SearchCriteria(hh_exclude=[]))
    assert found[0]["vacancy_id"] == "987654"
    assert found[0]["url"] == "https://hh.ru/vacancy/987654"


# ── Пропуск «уже откликались» ─────────────────────────────────────────


class FakeVacancyDriver:
    """Страница одной вакансии: одна и та же кнопка на все селекторы.

    Кнопка одна нарочно. Она отвечает и на селекторы отклика, и на
    селекторы отправки, поэтому со снятым пропуском сценарий доходит до
    конца быстро и тест краснеет на утверждении, а не по таймауту
    `WebDriverWait`.
    """

    def __init__(self, button: FakeElement):
        self.button = button
        self.visited: list[str] = []

    def get(self, url):
        self.visited.append(url)

    def find_element(self, by, selector):
        if "textarea" in selector:
            raise NoSuchElementException(selector)
        return self.button

    def find_elements(self, by, selector):
        return []


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """Снимает только время, но не логику ожидания.

    `apply_to_vacancy` выдерживает случайные паузы «под человека»
    (`time.sleep(random.uniform(2, 4))`) — тесту они не нужны.

    `WebDriverWait` остаётся НАСТОЯЩИМ, ему обнуляется лишь бюджет:
    `until()` всё равно опрашивает предикат хотя бы раз и всё равно бросает
    `TimeoutException`, когда элемента нет, — то есть путь «кнопка не
    найдена» проверяется тот же самый, только без четырёх пятнадцати-
    секундных ожиданий подряд.
    """
    monkeypatch.setattr(hh.time, "sleep", lambda *_args: None)
    real_wait = hh.WebDriverWait
    monkeypatch.setattr(
        hh, "WebDriverWait",
        lambda driver, _timeout, **kwargs: real_wait(driver, 0, poll_frequency=0.001),
    )


@pytest.mark.parametrize("button_text", [
    "Вы уже откликнулись",
    "Отклик отправлен",
    "ОТКЛИКНУЛИСЬ",
])
def test_a_vacancy_we_already_applied_to_is_not_clicked_again(button_text):
    button = FakeElement(text=button_text)
    driver = FakeVacancyDriver(button)
    vacancy = {"vacancy_id": "1", "title": "QA", "company": "ООО",
               "url": "https://hh.ru/vacancy/1"}

    applied = hh.apply_to_vacancy(driver, vacancy, SearchCriteria(), GlobalSettings())

    assert applied is False, "повторный отклик отмечен как отправленный"
    assert button.clicks == 0, (
        "воркер нажал «Откликнуться» на вакансии, где отклик уже отправлен — "
        "за такое поведение hh.ru ограничивает аккаунт"
    )


def test_a_fresh_vacancy_is_applied_to():
    """Обратная сторона пропуска: он не должен срабатывать всегда — иначе
    воркер не откликался бы вообще, а проверки выше остались бы зелёными."""
    button = FakeElement(text="Откликнуться")
    driver = FakeVacancyDriver(button)
    vacancy = {"vacancy_id": "2", "title": "QA инженер", "company": "ООО",
               "url": "https://hh.ru/vacancy/2"}

    applied = hh.apply_to_vacancy(driver, vacancy, SearchCriteria(), GlobalSettings())

    assert applied is True
    assert button.clicks >= 2, (
        "ожидались два клика — по кнопке отклика и по кнопке отправки: "
        f"{button.clicks}"
    )
    assert driver.visited == [vacancy["url"]]


def test_a_vacancy_without_an_apply_button_is_not_counted_as_applied():
    class NoButtonDriver(FakeVacancyDriver):
        def find_element(self, by, selector):
            raise NoSuchElementException(selector)

    driver = NoButtonDriver(FakeElement())
    applied = hh.apply_to_vacancy(
        driver, {"vacancy_id": "3", "title": "QA", "company": "ООО",
                 "url": "https://hh.ru/vacancy/3"},
        SearchCriteria(),
        GlobalSettings(),
    )
    assert applied is False
