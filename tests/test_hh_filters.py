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

import re

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


# ── Страница вакансии: отклик ─────────────────────────────────────────
#
# Двойник различает селекторы. Прежний отдавал ОДНУ И ТУ ЖЕ кнопку на
# любой из них — «нарочно, чтобы сценарий доходил до конца быстро», — и
# именно поэтому не мог отличить настоящий отклик (клик по кнопке, затем
# клик по кнопке отправки в открывшемся окне) от вырожденного случая, где
# окно не открылось и вторым кликом нажали ту же самую кнопку. Второе
# `apply_to_vacancy` засчитывала как успешный отклик: в базу шёл статус
# «отклик отправлен», расходовался суточный лимит, а на hh.ru отклика не
# было. Двойник, добрее реальности, — ровно тот способ, которым этот
# дефект прожил до живой проверки.

TAG_OF = re.compile(r"^//(\*|[a-z]+)\[")
QA_EXACT = re.compile(r"@data-qa='([^']+)'")
QA_PART = re.compile(r"contains\(@data-qa, '([^']+)'\)")
TEXT_PART = re.compile(r"contains\(text\(\), '([^']+)'\)")
CLASS_PART = re.compile(r"contains\(@class, '([^']+)'\)")
#: Предикат «атрибут просто есть»: `//textarea[@placeholder]`.
BARE_ATTR = re.compile(r"\[@([a-z-]+)\]$")


class Node(FakeElement):
    """Узел страницы: тег, `data-qa`, текст — то, по чему его ищут XPath'ы."""

    def __init__(self, tag: str, qa: str | None = None, text: str = "",
                 css_class: str = "", html: str = ""):
        super().__init__(text=text, attributes={"innerHTML": html})
        self.tag = tag
        self.qa = qa
        self.css_class = css_class
        self.typed = ""

    def clear(self):
        self.typed = ""

    def send_keys(self, chunk):
        self.typed += chunk


class VacancyPage:
    """Страница, описанная тем, что на ней ЕСТЬ.

    Тег проверяется наравне с атрибутами: разбор hh.ru уже ломался о то,
    что `div` стал `section`, и двойник, закрывающий на тег глаза, этого
    класса поломок не показывает.
    """

    def __init__(self, *nodes: Node):
        self.nodes = list(nodes)
        self.visited: list[str] = []

    def get(self, url):
        self.visited.append(url)

    @classmethod
    def _matches(cls, node: Node, selector: str) -> bool:
        # XPath'ы бывают объединением: `//textarea[@data-qa='…'] |
        # //textarea[@placeholder]`. Двойник обязан понимать эту форму —
        # именно ей был записан прежний селектор письма, и без неё
        # проверка «письмо не уезжает в чужое поле» проходила бы на любом
        # коде.
        if " | " in selector:
            return any(cls._matches(node, part) for part in selector.split(" | "))
        return cls._branch_matches(node, selector)

    @staticmethod
    def _branch_matches(node: Node, selector: str) -> bool:
        tag = TAG_OF.match(selector)
        if tag and tag.group(1) != "*" and tag.group(1) != node.tag:
            return False
        exact = QA_EXACT.search(selector)
        if exact:
            return node.qa == exact.group(1)
        part = QA_PART.search(selector)
        if part:
            return bool(node.qa) and part.group(1) in node.qa
        text = TEXT_PART.search(selector)
        if text:
            return text.group(1).lower() in node.text.lower()
        css = CLASS_PART.search(selector)
        if css:
            return css.group(1) in node.css_class
        bare = BARE_ATTR.search(selector)
        if bare:
            return node.get_attribute(bare.group(1)) is not None
        return False

    def find_elements(self, by, selector):
        return [node for node in self.nodes if self._matches(node, selector)]

    def find_element(self, by, selector):
        found = self.find_elements(by, selector)
        if not found:
            raise NoSuchElementException(selector)
        return found[0]


def _apply_button(text: str = "Откликнуться") -> Node:
    # Именно `button`, а не `a`. С тегом `a` проверка «окно отклика не
    # открылось» проходила и на СТАРОМ коде: двойник отбраковывал
    # `//button[contains(text(), 'Откликнуться')]` по тегу и тем самым
    # спасал код от его собственного дефекта. Кнопка отклика на hh.ru
    # бывала и тем, и другим — в исходном коде на каждое имя стояло по
    # два селектора, `//a[…]` и `//button[…]`.
    return Node("button", qa="vacancy-response-link-top", text=text)


def _submit_button() -> Node:
    return Node("button", qa="vacancy-response-letter-submit", text="Отправить")


VACANCY = {"vacancy_id": "1", "title": "QA инженер", "company": "ООО",
           "url": "https://hh.ru/vacancy/1"}


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """Снимает только время, но не логику ожидания.

    `apply_to_vacancy` выдерживает случайные паузы «под человека»
    (`time.sleep(random.uniform(2, 4))`) — тесту они не нужны.

    `WebDriverWait` остаётся НАСТОЯЩИМ, ему обнуляется лишь бюджет:
    `until()` всё равно опрашивает предикат хотя бы раз и всё равно бросает
    `TimeoutException`, когда элемента нет, — то есть путь «кнопка не
    найдена» проверяется тот же самый, только без пятнадцатисекундных
    ожиданий подряд.
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
    button = _apply_button(button_text)
    page = VacancyPage(button, _submit_button())

    applied = hh.apply_to_vacancy(page, VACANCY, SearchCriteria(), GlobalSettings())

    assert applied is False, "повторный отклик отмечен как отправленный"
    assert button.clicks == 0, (
        "воркер нажал «Откликнуться» на вакансии, где отклик уже отправлен — "
        "за такое поведение hh.ru ограничивает аккаунт"
    )


def test_a_fresh_vacancy_is_applied_to():
    """Обратная сторона пропуска: он не должен срабатывать всегда — иначе
    воркер не откликался бы вообще, а проверки выше остались бы зелёными."""
    apply_btn, submit = _apply_button(), _submit_button()
    page = VacancyPage(apply_btn, submit)

    applied = hh.apply_to_vacancy(page, VACANCY, SearchCriteria(), GlobalSettings())

    assert applied is True
    assert apply_btn.clicks == 1, "кнопка отклика нажата не один раз"
    assert submit.clicks == 1, "кнопка отправки не нажата"
    assert page.visited == [VACANCY["url"]]


def test_a_vacancy_without_an_apply_button_is_not_counted_as_applied():
    applied = hh.apply_to_vacancy(
        VacancyPage(), VACANCY, SearchCriteria(), GlobalSettings()
    )
    assert applied is False


def test_a_response_form_that_never_opened_is_not_reported_as_applied():
    """Главный дефект этого файла, найденный аудитом.

    Окно отклика не открылось — на странице осталась только исходная
    кнопка «Откликнуться». Третий селектор отправки ищет кнопку ПО ТЕКСТУ
    «Откликнуться», находил её же, нажимал второй раз, и функция
    возвращала успех. В базу шёл статус «отклик отправлен» с `applied_at`,
    расходовался суточный лимит, в журнал — событие `applied`; на hh.ru
    отклика не было, и узнать об этом было неоткуда.
    """
    apply_btn = _apply_button()
    page = VacancyPage(apply_btn)   # кнопки отправки на странице нет вовсе

    applied = hh.apply_to_vacancy(page, VACANCY, SearchCriteria(), GlobalSettings())

    assert applied is False, (
        "несостоявшийся отклик засчитан как отправленный — дашборд покажет "
        "отклик, которого на hh.ru нет"
    )
    assert apply_btn.clicks == 1, (
        f"по кнопке отклика кликнули {apply_btn.clicks} раз(а) вместо одного — "
        "второй клик и был тем, что выдавалось за отправку"
    )


def test_the_cover_letter_is_typed_into_the_letter_field():
    letter = Node("textarea", qa="vacancy-response-letter-textarea")
    # Поле не пустое: hh.ru подставляет туда черновик, да и прошлая
    # неудачная попытка оставляет текст. Без `clear()` письмо дописалось бы
    # к чужому — а с пустым полем эту разницу не увидеть, и мутация,
    # убиравшая очистку, проходила зелёной.
    letter.typed = "старый черновик"
    page = VacancyPage(_apply_button(), letter, _submit_button())

    applied = hh.apply_to_vacancy(
        page, VACANCY, SearchCriteria(hh_cover_letter="Здравствуйте!"), GlobalSettings()
    )

    assert applied is True
    assert letter.typed == "Здравствуйте!", (
        "поле письма не очищено перед вводом — отклик уйдёт с чужим текстом"
    )


def test_the_letter_field_survives_a_renamed_data_qa():
    """Запасной селектор ищет по части `data-qa`, а не по точному
    совпадению: переименование `…-letter-textarea` в `…-letter-input` не
    должно оставлять отклик без письма."""
    letter = Node("textarea", qa="vacancy-response-popup-form-letter-input")
    page = VacancyPage(_apply_button(), letter, _submit_button())

    hh.apply_to_vacancy(
        page, VACANCY, SearchCriteria(hh_cover_letter="Здравствуйте!"), GlobalSettings()
    )

    assert letter.typed == "Здравствуйте!"


def test_the_letter_never_goes_into_an_unrelated_field():
    """Запасным вариантом здесь стояло `//textarea[@placeholder]` — «любая
    область ввода с подсказкой». `find_element` возвращает первое
    совпадение в порядке документа, поэтому письмо уезжало в чужое поле:
    на странице вакансии их хватает, а отклик при этом уходил пустым."""
    # placeholder обязателен: прежний запасной селектор искал именно
    # `//textarea[@placeholder]`, и поле без него он бы не нашёл — проверка
    # стала бы зелёной сама собой.
    чужое = Node("textarea", qa="vacancy-search-query", text="")
    чужое._attributes["placeholder"] = "Профессия, должность"
    page = VacancyPage(_apply_button(), чужое, _submit_button())

    applied = hh.apply_to_vacancy(
        page, VACANCY, SearchCriteria(hh_cover_letter="Здравствуйте!"), GlobalSettings()
    )

    assert applied is True, "отклик без письма — всё ещё отклик"
    assert чужое.typed == "", (
        f"сопроводительное письмо напечатано в поле «{чужое.qa}», "
        "которое к отклику отношения не имеет"
    )


def test_only_the_first_500_characters_of_the_letter_are_typed():
    letter = Node("textarea", qa="vacancy-response-letter-textarea")
    page = VacancyPage(_apply_button(), letter, _submit_button())

    hh.apply_to_vacancy(
        page, VACANCY, SearchCriteria(hh_cover_letter="я" * 800), GlobalSettings()
    )

    assert len(letter.typed) == 500


def test_the_resume_the_user_chose_is_the_one_selected():
    нужное = Node("div", qa="resume-negotiations-list__resume", html="<b>resume-777</b>")
    другое = Node("div", qa="resume-negotiations-list__resume", html="<b>resume-111</b>")
    page = VacancyPage(_apply_button(), другое, нужное, _submit_button())

    applied = hh.apply_to_vacancy(
        page, VACANCY, SearchCriteria(hh_resume_id="resume-777"), GlobalSettings()
    )

    assert applied is True
    assert нужное.clicks == 1, "выбрано не то резюме, которое назвал пользователь"
    assert другое.clicks == 0


def test_a_missing_resume_cancels_the_application_instead_of_sending_another():
    """Промах выбора был обёрнут в голый `except Exception: pass`, и отклик
    уходил ТЕМ резюме, что стояло у hh.ru по умолчанию, — молча.

    Это не ослабленный успех, а другое действие: работодателю уходит не то
    резюме, которое выбрал человек, и отозвать это нельзя. Вакансия
    остаётся в очереди, откликнуться на неё руками можно в любой момент.
    """
    submit = _submit_button()
    page = VacancyPage(_apply_button(), submit)   # списка резюме на странице нет

    applied = hh.apply_to_vacancy(
        page, VACANCY, SearchCriteria(hh_resume_id="resume-777"), GlobalSettings()
    )

    assert applied is False
    assert submit.clicks == 0, "отклик отправлен с не тем резюме"


def test_an_empty_resume_id_does_not_block_the_application():
    """Обратная сторона: проверка резюме не должна срабатывать у тех, кто
    его не выбирал, — иначе воркер перестанет откликаться вообще."""
    submit = _submit_button()
    page = VacancyPage(_apply_button(), submit)

    applied = hh.apply_to_vacancy(
        page, VACANCY, SearchCriteria(hh_resume_id=""), GlobalSettings()
    )

    assert applied is True
    assert submit.clicks == 1


# ── Разметка hh.ru: разбор не должен зависеть от тега ─────────────────



class TagStrictElement:
    """Элемент, который знает свой тег и сопоставляет XPath как Selenium.

    Прежний двойник (`FakeElement` выше) искал по вхождению подстроки и тег
    игнорировал — то есть был снисходительнее настоящего браузера ровно в
    том измерении, где потом всё и сломалось: hh.ru сменил `div` на
    `section` и `article`, XPath `//div[@data-qa='…']` перестал совпадать
    на живом сайте, а в тестах продолжал «находить» элемент.

    Двойник обязан быть строг там, где строга реальность.
    """

    _XPATH = re.compile(r"\.?//(?P<tag>[\w*]+)\[@data-qa='(?P<qa>[^']+)'\]")

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
