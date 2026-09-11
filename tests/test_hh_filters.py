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


# ── Разметка hh.ru: разбор не должен зависеть от тега ─────────────────

import re as _re


class TagStrictElement:
    """Элемент, который знает свой тег и сопоставляет XPath как Selenium.

    Прежний двойник (`FakeElement` выше) искал по вхождению подстроки и тег
    игнорировал — то есть был снисходительнее настоящего браузера ровно в
    том измерении, где потом всё и сломалось: hh.ru сменил `div` на
    `section` и `article`, XPath `//div[@data-qa='…']` перестал совпадать
    на живом сайте, а в тестах продолжал «находить» элемент.

    Двойник обязан быть строг там, где строга реальность.
    """

    _XPATH = _re.compile(r"\.?//(?P<tag>[\w*]+)\[@data-qa='(?P<qa>[^']+)'\]")

    def __init__(self, tag: str, data_qa: str = "", text: str = "",
                 attributes: dict | None = None, children: list | None = None):
        self.tag = tag
        self.data_qa = data_qa
        self._own_text = text
        self._attributes = attributes or {}
        self.children = children or []

    @property
    def text(self) -> str:
        """Текст поддерева, как у настоящего Selenium, а не только свой.

        Важно для разбора зарплаты: у hh.ru её узел остался без единого
        `data-qa`, и добыть её можно только из текста карточки.
        """
        parts = [self._own_text] + [c.text for c in self.children]
        return "\n".join(p for p in parts if p)

    def get_attribute(self, name: str):
        return self._attributes.get(name)

    def _matches(self, selector: str) -> bool:
        found = self._XPATH.search(selector)
        if not found:
            return False
        tag, qa = found.group("tag"), found.group("qa")
        return self.data_qa == qa and tag in ("*", self.tag)

    def _walk(self):
        for child in self.children:
            yield child
            yield from child._walk()

    def find_element(self, by, selector):
        for node in self._walk():
            if node._matches(selector):
                return node
        raise NoSuchElementException(selector)

    def find_elements(self, by, selector):
        return [node for node in self._walk() if node._matches(selector)]


def _hh_page_as_it_is_today() -> TagStrictElement:
    """Разметка страницы поиска hh.ru на сентябрь 2026.

    Теги проверены в браузере на живом сайте: контейнер результатов —
    `section`, карточка вакансии — `article`, адрес — `span`. Раньше все
    три были `div`.
    """
    card = TagStrictElement("article", "vacancy-serp__vacancy", children=[
        TagStrictElement("a", "serp-item__title", text="Frontend-разработчик",
                         attributes={"href": "https://hh.ru/vacancy/42?from=serp"}),
        TagStrictElement("a", "vacancy-serp__vacancy-employer", text="ООО Ромашка"),
        TagStrictElement("span", "vacancy-serp__vacancy-address", text="Москва"),
    ])
    return TagStrictElement("html", children=[
        TagStrictElement("section", "vacancy-serp__results", children=[card]),
    ])


class TodaysHhDriver:
    def __init__(self):
        self._root = _hh_page_as_it_is_today()
        self.visited: list[str] = []

    def get(self, url):
        self.visited.append(url)

    def find_element(self, by, selector):
        return self._root.find_element(by, selector)

    def find_elements(self, by, selector):
        return self._root.find_elements(by, selector)


def test_the_parser_reads_todays_hh_markup():
    """Регресс на дефект, найденный при первом живом запуске воркера.

    Журнал показывал «Результаты поиска не загрузились» по разу на каждую
    профессию, а на самой странице было 49 вакансий: `h1` их называл.
    Причина — XPath `//div[@data-qa='vacancy-serp__results']`, тогда как
    hh.ru перешёл на семантическую разметку и контейнер стал `section`, а
    карточка — `article`.

    Дефект стоил всего поиска: воркер отчитывался «работает» и не находил
    ничего.
    """
    from job_monitor.criteria import SearchCriteria
    from job_monitor.workers.hh import get_vacancies_from_page

    found = get_vacancies_from_page(TodaysHhDriver(), SearchCriteria())

    assert len(found) == 1, "страница сегодняшней разметки прочитана как пустая"
    assert found[0]["vacancy_id"] == "42"
    assert found[0]["title"] == "Frontend-разработчик"
    assert found[0]["company"] == "ООО Ромашка"
    assert found[0]["city"] == "Москва"


def test_the_parser_does_not_depend_on_the_tag():
    """Свойство, а не конкретные теги: `data-qa` — собственный крючок
    hh.ru для тестов, он переживает редизайн; тег относится к оформлению и
    уже поменялся под нами один раз.

    Поэтому проверка не «контейнер это section», а «разбор работает при
    любом теге»: следующая смена не должна ничего ломать.
    """
    from job_monitor.criteria import SearchCriteria
    from job_monitor.workers.hh import get_vacancies_from_page

    for tag_results, tag_card in (("div", "div"), ("section", "article"), ("main", "li")):
        driver = TodaysHhDriver()
        driver._root = TagStrictElement("html", children=[
            TagStrictElement(tag_results, "vacancy-serp__results", children=[
                TagStrictElement(tag_card, "vacancy-serp__vacancy", children=[
                    TagStrictElement("a", "serp-item__title", text="QA",
                                     attributes={"href": "https://hh.ru/vacancy/7"}),
                ]),
            ]),
        ])
        found = get_vacancies_from_page(driver, SearchCriteria())
        assert len(found) == 1, f"разбор сломался на тегах {tag_results}/{tag_card}"


def _card_with_salary(salary_line: str) -> TagStrictElement:
    """Карточка, где зарплата написана, но крючка `data-qa` у неё нет."""
    return TagStrictElement("article", "vacancy-serp__vacancy", children=[
        TagStrictElement("a", "serp-item__title", text="Frontend-разработчик",
                         attributes={"href": "https://hh.ru/vacancy/42"}),
        TagStrictElement("div", children=[TagStrictElement("span", text=salary_line)]),
    ])


def _page_of(card: TagStrictElement) -> TodaysHhDriver:
    driver = TodaysHhDriver()
    driver._root = TagStrictElement("html", children=[
        TagStrictElement("section", "vacancy-serp__results", children=[card]),
    ])
    return driver


@pytest.mark.parametrize("строка, ожидается", [
    ("от 150 000 ₽ за месяц, на руки", "от 150 000 ₽ за месяц, на руки"),
    ("100 000 – 180 000 ₽ за месяц", "100 000 – 180 000 ₽ за месяц"),
    ("до 4 500 $ за месяц", "до 4 500 $ за месяц"),
])
def test_the_salary_is_read_from_the_card_text(строка, ожидается):
    """У узла зарплаты не осталось ни одного `data-qa` — проверено в
    браузере на живом hh.ru. Раньше он назывался
    `vacancy-serp__vacancy-compensation`, теперь это голый `span` внутри
    безымянных `div`.

    Зарплата не влияет на поведение (фильтр по ней уходит в адрес поиска),
    но по карточке человек решает, откликаться ли руками. Писать «Не
    указана» там, где стоит «от 150 000 ₽», — врать в том единственном
    месте, ради которого весь список и существует.
    """
    from job_monitor.criteria import SearchCriteria
    from job_monitor.workers.hh import get_vacancies_from_page

    found = get_vacancies_from_page(_page_of(_card_with_salary(строка)), SearchCriteria())
    assert found[0]["salary"] == ожидается


def test_a_card_without_a_salary_says_so_and_does_not_invent_one():
    """Обратная сторона: разбор по тексту не должен принимать за деньги
    случайное число — «Сейчас смотрят 6 человек», «4.7 • 10829 отзывов»,
    номер вакансии. Такие строки в карточке есть всегда."""
    from job_monitor.criteria import SearchCriteria
    from job_monitor.workers.hh import get_vacancies_from_page

    card = TagStrictElement("article", "vacancy-serp__vacancy", children=[
        TagStrictElement("a", "serp-item__title", text="QA",
                         attributes={"href": "https://hh.ru/vacancy/7"}),
        TagStrictElement("span", text="Сейчас смотрят 6 человек"),
        TagStrictElement("span", text="4.7 • 10829 отзывов"),
        TagStrictElement("span", text="Опыт 3-6 лет"),
    ])
    found = get_vacancies_from_page(_page_of(card), SearchCriteria())
    assert found[0]["salary"] == "Не указана", (
        f"выдумана зарплата из постороннего числа: {found[0]['salary']!r}"
    )


# ── Поиск по названию вакансии, а не по всему тексту ──────────────────


def test_the_search_matches_the_job_title_only():
    """Измерено на живом hh.ru с критериями партнёра.

    Без `search_field=name` по запросу «Frontend-разработчик» приходили
    Senior PHP Developer, Java-разработчик, DevOps, системный аналитик,
    менеджер интернет-проектов: hh.ru ищет по всему тексту вакансии, и
    достаточно упоминания фронтенда где-нибудь в описании. В очереди
    оказалось 39 посторонних записей из 64 — шестьдесят один процент.

    С параметром — 19 вакансий, все девятнадцать про фронтенд.

    Очередь существует, чтобы человек её ПРОЧИТАЛ и решил, откликаться ли.
    Список, где три записи из пяти чужие, эту работу не облегчает, а
    создаёт: проще открыть hh.ru самому.
    """
    from job_monitor.criteria import SearchCriteria
    from job_monitor.workers.hh import build_search_url

    url = build_search_url("Frontend-разработчик", SearchCriteria())
    assert "search_field=name" in url


def test_the_search_field_is_not_a_setting():
    """Как и регион (решение D8): поле «Профессии на hh.ru» и означает
    название должности. Искать по описанию — другой инструмент, а не
    другая галочка; расширяется это добавлением вариантов названия, что
    интерфейс и предлагает списком."""
    from job_monitor.criteria import SearchCriteria

    assert not hasattr(SearchCriteria(), "hh_search_field")
