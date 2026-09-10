"""Цикл HH-воркера и машина состояний входа.

`HhLogin` заменяет блокирующий `input()` из старого `hh_monitor.py` (L4):
вместо одного вызова, который ждёт Enter в терминале, который у воркера,
запущенного из UI как процесс без stdin, никогда не придёт, вход разбит на
два независимых HTTP-вызова — `start()` открывает окно браузера,
`confirm()` проверяет, что пользователь там действительно вошёл, и
сохраняет cookies. Ни один из них не блокирует event loop дольше времени
одной Selenium-команды (оба вызываются через `asyncio.to_thread` в
`api/hh_routes.py`).

Цикл поиска и откликов (`_blocking_loop`) — синхронный и живёт в отдельном
потоке, потому что Selenium сам по себе блокирующий. Поток нельзя отменить
как asyncio-таску, поэтому кооперативная отмена — через `threading.Event`,
проверяемый на каждом шаге, который может занять время (между вакансиями,
внутри задержки после отклика, во время ожидания дневного лимита).

Этот `Event` создаётся заново на каждый вызов `run_worker()` и передаётся в
`_blocking_loop`/`_interruptible_sleep` явным параметром — не хранится в
модуле. Общий на всё приложение `Event` (как было раньше) означал бы, что
`clear()` при рестарте стирает сигнал остановки ещё не завершившегося
старого потока: тот продолжал бы жить со своим Chrome и своим соединением,
не отслеживаемый менеджером — тот самый класс "второй Chrome", ради
устранения которого существует этот план.

`run_worker()` также не может просто ждать `asyncio.to_thread(...)` и
ловить `CancelledError` вокруг него: `to_thread` не ждёт поток при отмене —
awaiting-корутина репортит `cancelled` за миллисекунды независимо от того,
проверяет ли поток флаг, а сам поток продолжает работать. Поэтому фоновая
задача оборачивается в `asyncio.shield()` (стандартный приём из документации
asyncio) — отмена `run_worker()` не отменяет фоновую задачу, а после того
как флаг остановки выставлен, код дожидается её напрямую, так что
`WorkerManager.stop()`'s ограниченный по времени `asyncio.wait()` видит
настоящее состояние потока, а не мгновенно принятую отмену.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import threading
import time
from collections.abc import Callable
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from job_monitor import paths, statuses
from job_monitor.db.repositories import EventsRepo, HH_STATUS_APPLIED, HhRepo
from job_monitor.criteria import HH_AREA_ID, SearchCriteria
from job_monitor.presets import active_criteria
from job_monitor.settings import GlobalSettings, load_settings
from job_monitor.workers.hh_steps import parse_steps, run_steps

log = logging.getLogger(__name__)

LOGIN_URL = "https://hh.ru/account/login"
HOME_URL = "https://hh.ru"
SELENIUM_COOKIE_FIELDS = ("name", "value", "domain", "path", "secure", "httpOnly")
# L5: статус для вакансии, на которой сценарий Selenium сломался опечаткой
# уже после клика по кнопке отклика. Не HH_STATUS_APPLIED — не в счётчик
# отправленных; отдельно от "пропущено" — видно в дашборде, что причина
# именно в сценарии, а не в том, что кнопка отклика не нашлась.
# Прежнее имя, единственный источник значения — job_monitor/statuses.py.
HH_STATUS_SCENARIO_ERROR = statuses.SCENARIO_ERROR


# ── Вход без блокировки (L4) ────────────────────────────────────────────


class HhLoginState(str, Enum):
    logged_out = "logged_out"
    browser_open = "browser_open"
    logged_in = "logged_in"


def save_cookies(driver: Any, target: Path) -> None:
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.write_text(json.dumps(driver.get_cookies()), encoding="utf-8")
    target.chmod(0o600)


def load_cookies(driver: Any, target: Path) -> bool:
    if not target.exists():
        return False
    driver.get(HOME_URL)
    for cookie in json.loads(target.read_text(encoding="utf-8")):
        trimmed = {key: value for key, value in cookie.items() if key in SELENIUM_COOKIE_FIELDS}
        try:
            driver.add_cookie(trimmed)
        except Exception as error:  # noqa: BLE001 — один плохой cookie не должен ронять вход
            log.debug("cookie %s отклонён: %s", trimmed.get("name"), error)
    driver.refresh()
    return True


class LoginWindowNotOpen(RuntimeError):
    """`confirm()` вызван, когда окна входа нет.

    Отдельный тип, а не голый `RuntimeError`: это ошибка последовательности
    вызовов (клиент нажал «Я вошёл» раньше «Открыть вход» — или после
    «Закрыть окно входа»), а не сбой драйвера. Роут переводит её в 400, тогда
    как настоящие поломки Selenium внутри `confirm()` должны оставаться 500.
    Наследуется от RuntimeError, чтобы прежний контракт («confirm без start —
    это ошибка») продолжал выполняться для всех, кто ловил RuntimeError.
    """


class HhLogin:
    """Ручной вход в hh.ru без блокировки процесса: два вызова вместо input()."""

    def __init__(
        self,
        driver_factory: Callable[[], Any],
        cookies_path: Path | Callable[[], Path],
        is_logged_in: Callable[[Any], bool],
    ) -> None:
        self._driver_factory = driver_factory
        self._cookies_source = cookies_path
        self._is_logged_in = is_logged_in
        self._driver: Any | None = None
        self.state = HhLoginState.logged_out

    @property
    def cookies_path(self) -> Path:
        """Путь к cookies, вычисляемый на момент обращения.

        Модульный синглтон `login` ниже собирается на импорте, а
        `paths.hh_cookies()` не только вычисляет путь, но и создаёт каталог
        данных (`paths.path()` делает `mkdir`). Передавать сюда готовый
        `Path` означало: (1) `import api.main` создаёт `~/.job-monitor` до
        того, как хоть кто-то попросил что-нибудь сохранить, и (2) путь
        заморожен на момент импорта — `JOB_MONITOR_DATA_DIR`, выставленный
        позже (например, autouse-фикстурой tests/conftest.py, которая
        отрабатывает уже ПОСЛЕ импорта модулей на коллекции), молча
        игнорируется, и cookies уходят не туда. Поэтому источником может
        быть и функция; тестам по-прежнему можно передать обычный `Path`.
        """
        source = self._cookies_source
        return source() if callable(source) else source

    def start(self) -> HhLoginState:
        if self._driver is None:
            self._driver = self._driver_factory()
        self._driver.get(LOGIN_URL)
        self.state = HhLoginState.browser_open
        return self.state

    def confirm(self) -> HhLoginState:
        if self._driver is None:
            raise LoginWindowNotOpen("сначала вызови start()")
        try:
            if not self._is_logged_in(self._driver):
                # Окно намеренно остаётся открытым: пользователь ещё не вошёл
                # и сейчас как раз этим и занят.
                return self.state
            save_cookies(self._driver, self.cookies_path)
        except Exception:
            # `_is_logged_in` — это Selenium-вызовы, а `save_cookies` пишет
            # файл: оба могут бросить (упавший драйвер, недоступный каталог
            # данных). Без этого окно Chrome оставалось висеть и на пути,
            # который выглядит успешным.
            self.close()
            raise
        self.close()
        self.state = HhLoginState.logged_in
        return self.state

    def close(self) -> None:
        """Закрыть окно входа, если оно открыто. Идемпотентно.

        Драйвер раньше гасился только в `confirm()` и только при успешной
        проверке входа. Пользователь, который нажал «Открыть вход в hh.ru» и
        не подтвердил — вход не удался, передумал, закрыл вкладку UI, —
        оставлял живой Chrome, переживающий остановку приложения: `lifespan`
        в api/main.py знает только про `manager.stop_all()`. Это тот же класс
        отказа «брошенный неподнадзорный браузер», ради которого существует
        `WorkerManager`, только окно входа менеджеру не принадлежит, — значит
        закрывать его должен кто-то ещё: выход из `lifespan` и роут
        POST /api/hh/login/cancel.

        Исключение из `driver.quit()` наружу не выпускается: это вызывается на
        пути остановки приложения, где упавший quit не должен ломать выход, а
        ссылка на драйвер всё равно уже сброшена.
        """
        driver, self._driver = self._driver, None
        if driver is None:
            # Состояние не трогаем: `logged_in` означает «cookies сохранены»,
            # и сбрасывать его в `logged_out` из-за закрытия уже закрытого
            # окна значило бы врать в GET /api/hh/login/status.
            return
        self.state = HhLoginState.logged_out
        try:
            driver.quit()
        except Exception as error:  # noqa: BLE001 — quit на пути выхода не должен ронять приложение
            log.warning("не удалось закрыть окно входа hh.ru: %s", error)


# ── Selenium-функции, перенесённые из hh_monitor.py без изменения логики ──


def is_logged_in(driver: Any) -> bool:
    try:
        driver.get("https://hh.ru")
        time.sleep(3)
        indicators = [
            "//div[@data-qa='mainmenu-userBlock']",
            "//a[@data-qa='account-personal-link']",
            "//span[@data-qa='bloko-header-1']",
        ]
        for xpath in indicators:
            try:
                driver.find_element(By.XPATH, xpath)
                return True
            except NoSuchElementException:
                continue
        return False
    except Exception:
        return False


def setup_driver(headless: bool = False) -> "webdriver.Chrome":
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--window-size=1280,900")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=options)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


def build_search_url(profession: str, criteria: SearchCriteria) -> str:
    """URL поиска по одной профессии из пресета.

    Регион не параметр, а константа `HH_AREA_ID` (решение D8): поиск ведётся
    только по нему, подменить его через API нельзя, и справочник регионов
    hh.ru приложению поэтому не нужен вовсе.
    """
    params = [
        f"text={profession.replace(' ', '+')}",
        f"area={HH_AREA_ID}",
        f"search_period={criteria.hh_search_period}",
        "per_page=20",
        "order_by=publication_time",
    ]
    if criteria.hh_experience:
        params.append(f"experience={criteria.hh_experience}")
    if criteria.hh_salary_from:
        params.append(f"salary={criteria.hh_salary_from}")
        params.append("only_with_salary=true")
    for emp in criteria.hh_employment:
        params.append(f"employment={emp}")
    for sch in criteria.hh_schedule:
        params.append(f"schedule={sch}")
    return "https://hh.ru/search/vacancy?" + "&".join(params)


def get_vacancies_from_page(driver: Any, criteria: SearchCriteria) -> list[dict]:
    """Собираем вакансии со страницы поиска.

    Ключ переименован с `id` на `vacancy_id` (перенос из hh_monitor.py):
    `HhRepo.exists()`/`upsert()` ждут `vacancy_id`, иначе получат `None`
    молча и приложение начнёт откликаться на одни и те же вакансии по кругу.
    """
    vacancies: list[dict] = []
    wait = WebDriverWait(driver, 10)
    try:
        wait.until(EC.presence_of_element_located(
            (By.XPATH, "//div[@data-qa='vacancy-serp__results']")
        ))
    except TimeoutException:
        log.warning("[HH] Результаты поиска не загрузились")
        return []

    items = driver.find_elements(By.XPATH, "//div[@data-qa='vacancy-serp__vacancy']")
    exclude = [w.lower() for w in criteria.hh_exclude]

    for item in items:
        try:
            title_el = item.find_element(By.XPATH, ".//a[@data-qa='serp-item__title']")
            title = title_el.text.strip()
            url = title_el.get_attribute("href").split("?")[0]
            vacancy_id = url.split("/")[-1]

            title_lower = title.lower()
            if any(ex in title_lower for ex in exclude):
                continue

            try:
                company = item.find_element(
                    By.XPATH, ".//a[@data-qa='vacancy-serp__vacancy-employer']"
                ).text.strip()
            except NoSuchElementException:
                company = "Не указана"

            try:
                salary = item.find_element(
                    By.XPATH, ".//span[@data-qa='vacancy-serp__vacancy-compensation']"
                ).text.strip()
            except NoSuchElementException:
                salary = "Не указана"

            try:
                city = item.find_element(
                    By.XPATH, ".//div[@data-qa='vacancy-serp__vacancy-address']"
                ).text.strip()
            except NoSuchElementException:
                city = ""

            vacancies.append({
                "vacancy_id": vacancy_id,
                "title": title,
                "company": company,
                "salary": salary,
                "city": city,
                "url": url,
                "found_at": datetime.now().isoformat(timespec="seconds"),
            })
        except Exception as error:
            log.debug("[HH] Ошибка парсинга вакансии: %s", error)
            continue

    return vacancies


def apply_to_vacancy(
    driver: Any, vacancy: dict, criteria: SearchCriteria, settings: GlobalSettings
) -> bool:
    """Откликаемся на вакансию.

    Критерии и глобальные настройки приходят раздельно: сопроводительное
    письмо и резюме на стороне hh.ru принадлежат пресету, а сценарий
    Selenium описывает, КАК управлять сайтом, и от смены профессии не
    зависит.
    """
    wait = WebDriverWait(driver, 15)
    cover_letter = criteria.hh_cover_letter
    resume_id = criteria.hh_resume_id

    try:
        driver.get(vacancy["url"])
        time.sleep(random.uniform(2, 4))

        apply_btn = None
        selectors = [
            "//a[@data-qa='vacancy-response-link-top']",
            "//button[@data-qa='vacancy-response-link-top']",
            "//a[@data-qa='vacancy-response-link-bottom']",
            "//button[contains(@class, 'vacancy-response')]",
        ]
        for sel in selectors:
            try:
                apply_btn = wait.until(EC.element_to_be_clickable((By.XPATH, sel)))
                break
            except TimeoutException:
                continue

        if not apply_btn:
            log.warning("[HH] Кнопка отклика не найдена: %s", vacancy["title"])
            return False

        btn_text = apply_btn.text.lower()
        if "откликнулись" in btn_text or "отклик отправлен" in btn_text:
            log.info("[HH][SKIP] Уже откликались: %s", vacancy["title"])
            return False

        apply_btn.click()
        time.sleep(random.uniform(1.5, 3))

        # L5: пользовательский сценарий из настроек (hh_selenium_steps).
        # `parse_steps` может бросить ValueError на неизвестном типе шага —
        # это не ловим здесь: пусть поднимется к вызывающему циклу воркера,
        # который запишет событие и не даст опечатке в сценарии уронить монитор.
        steps = parse_steps(settings.hh_selenium_steps)
        if steps:
            run_steps(driver, steps, WebDriverWait)

        if resume_id:
            try:
                resume_items = driver.find_elements(
                    By.XPATH, "//div[@data-qa='resume-negotiations-list__resume']"
                )
                for item in resume_items:
                    if resume_id in item.get_attribute("innerHTML"):
                        item.click()
                        time.sleep(1)
                        break
            except Exception:
                pass

        if cover_letter:
            try:
                letter_area = driver.find_element(
                    By.XPATH,
                    "//textarea[@data-qa='vacancy-response-letter-textarea'] | "
                    "//textarea[@placeholder]"
                )
                letter_area.clear()
                for char in cover_letter[:500]:
                    letter_area.send_keys(char)
                    if random.random() < 0.05:
                        time.sleep(random.uniform(0.05, 0.15))
                time.sleep(1)
            except NoSuchElementException:
                log.warning("[HH] Поле письма не найдено для: %s", vacancy["title"])

        submit_selectors = [
            "//button[@data-qa='vacancy-response-letter-submit']",
            "//button[@data-qa='vacancy-response-submit-popup']",
            "//button[contains(text(), 'Откликнуться')]",
            "//button[contains(text(), 'Отправить')]",
        ]
        submitted = False
        for sel in submit_selectors:
            try:
                submit_btn = wait.until(EC.element_to_be_clickable((By.XPATH, sel)))
                submit_btn.click()
                submitted = True
                break
            except (TimeoutException, ElementClickInterceptedException):
                continue

        if not submitted:
            log.warning("[HH] Не удалось отправить отклик: %s", vacancy["title"])
            return False

        time.sleep(random.uniform(2, 4))
        log.info("[HH][OK] Отклик отправлен: %s — %s", vacancy["title"], vacancy["company"])
        return True

    except WebDriverException as error:
        log.error("[HH][ERROR] %s: %s", vacancy["title"], error)
        return False


# ── Цикл воркера в отдельном потоке ─────────────────────────────────────

# `cookies_path` — функция, а не значение: вызов `paths.hh_cookies()` прямо
# здесь выполнялся бы на импорте модуля и создавал каталог данных (см.
# HhLogin.cookies_path).
login = HhLogin(
    driver_factory=lambda: setup_driver(headless=False),
    cookies_path=paths.hh_cookies,
    is_logged_in=is_logged_in,
)


def _interruptible_sleep(stop_event: threading.Event, seconds: float) -> bool:
    """Спит по секунде. Возвращает False, если попросили остановиться.

    `stop_event` — параметр, а не модульный синглтон: у каждого запуска
    воркера свой Event (см. `run_worker`), иначе рестарт во время ещё не
    завершившегося старого цикла стирал бы его сигнал остановки.
    """
    for _ in range(int(seconds)):
        if stop_event.is_set():
            return False
        time.sleep(1)
    return True


def _process_one(
    driver: Any,
    vacancy: dict,
    criteria: SearchCriteria,
    settings: GlobalSettings,
    repo: HhRepo,
    events: EventsRepo,
) -> bool:
    """Откликается на одну вакансию и записывает результат. True — отклик отправлен."""
    try:
        applied = apply_to_vacancy(driver, vacancy, criteria, settings)
    except ValueError as error:
        # L5: опечатка в сохранённом сценарии Selenium (hh_selenium_steps) не
        # должна ронять монитор. Кнопка «Откликнуться» к этому моменту уже
        # нажата на живом hh.ru (parse_steps/run_steps вызываются в
        # apply_to_vacancy после клика) — повторный клик на следующем цикле
        # воркера, пока пользователь не поправит настройки, хуже, чем одна
        # непереотправленная вакансия. Поэтому НЕ пропускаем upsert (как
        # было раньше): помечаем вакансию отдельным статусом, не
        # HH_STATUS_APPLIED, — repo.exists() станет True, дедуп в
        # _blocking_loop больше её не тронет, а причина видна и в
        # worker_events, и в карточке вакансии на дашборде: бейдж статуса
        # плюс сам текст `error` (frontend/app.js::loadHHVacancies). Без
        # текста бейдж сообщал бы «ошибка сценария», не говоря какой шаг не
        # разобрался, — то есть не давал бы её починить.
        log.warning("[HH] Некорректный сценарий Selenium: %s", error)
        repo.upsert({
            **vacancy,
            "status": HH_STATUS_SCENARIO_ERROR,
            "error": str(error),
        })
        events.add("hh", "steps_invalid", str(error), datetime.now())
        return False
    repo.upsert({
        **vacancy,
        "status": HH_STATUS_APPLIED if applied else "пропущено",
        "applied_at": datetime.now().isoformat(timespec="seconds") if applied else None,
    })
    events.add("hh", "applied" if applied else "skipped", vacancy["title"], datetime.now())
    return applied


def _blocking_loop(stop_event: threading.Event) -> None:
    # R4: open a connection dedicated to this thread rather than sharing the
    # request-handling event loop's get_connection() singleton. sqlite3
    # connections are not meant for concurrent use from two threads even
    # with check_same_thread=False — with an explicit BEGIN issued from both
    # this thread and a request handler at the same time, the second would
    # hit "OperationalError: cannot start a transaction within a
    # transaction" instead of simply waiting its turn.
    from job_monitor.db.connection import connect

    conn = connect()
    repo = HhRepo(conn)
    events = EventsRepo(conn)
    driver = setup_driver(headless=False)
    try:
        load_cookies(driver, paths.hh_cookies())
        if not is_logged_in(driver):
            events.add("hh", "login_required", "нужен вход через настройки", datetime.now())
            return
        while not stop_event.is_set():
            # Весь цикл — включая load_settings()/applied_on() — под общей
            # защитой. Раньше эти два вызова стояли ВНЕ try, а ловился только
            # WebDriverException, поэтому воркер насмерть убивал не только
            # баг Selenium: sqlite3.OperationalError («database is locked»
            # при двух писателях и busy_timeout=5000) из applied_on/exists/
            # upsert/events.add или ValueError из random.randint при
            # hh_delay_min > hh_delay_max — и ничего не попадало в
            # worker_events. Ловим Exception целиком (так и делал исходный
            # hh_monitor.py вокруг всего тела цикла); последним рубежом
            # остаётся WorkerManager._supervise, так что ничего не теряется
            # безвозвратно, а тип исключения пишем в событие, чтобы даже
            # программная ошибка была видна в журнале.
            try:
                settings = load_settings(conn)
                criteria = active_criteria(conn)
                if repo.applied_on(date.today()) >= settings.hh_max_per_day:
                    if not _interruptible_sleep(stop_event, 600):
                        return
                    continue
                for profession in criteria.professions:
                    if stop_event.is_set():
                        return
                    driver.get(build_search_url(profession, criteria))
                    for vacancy in get_vacancies_from_page(driver, criteria):
                        if stop_event.is_set():
                            return
                        if repo.exists(vacancy["vacancy_id"]):
                            continue
                        _process_one(
                            driver, vacancy, criteria, settings, repo, events
                        )
                        if not _interruptible_sleep(
                            stop_event,
                            # min/max, а не как есть: настройки не запрещают
                            # hh_delay_min > hh_delay_max, а random.randint
                            # на пустом диапазоне бросает ValueError и
                            # обрывал бы цикл.
                            random.randint(
                                min(settings.hh_delay_min, settings.hh_delay_max),
                                max(settings.hh_delay_min, settings.hh_delay_max),
                            ),
                        ):
                            return
            except Exception as error:  # noqa: BLE001 — см. комментарий выше
                # Перенос из hh_monitor.py: браузер/сеть иногда моргают
                # (таймаут, потеря соединения, временно упавшая страница
                # поиска) — старый цикл логировал и спал минуту вместо того,
                # чтобы падать насовсем. Порт этого файла потерял эту
                # устойчивость (осталось только driver.quit() в finally),
                # из-за чего необработанное исключение из
                # driver.get()/get_vacancies_from_page() поднималось в
                # run_worker() и помечало воркер как error() навсегда, требуя
                # ручного перезапуска, и ничего не попадало в worker_events.
                log.exception("[HH] цикл прерван ошибкой")
                events.add("hh", "error", f"{type(error).__name__}: {error}", datetime.now())
                if not _interruptible_sleep(stop_event, 60):
                    return
                continue
            if not _interruptible_sleep(stop_event, settings.hh_check_interval):
                return
    finally:
        driver.quit()


async def run_worker() -> None:
    stop_event = threading.Event()
    # Стандартный приём из документации asyncio для «не дать отмене убить
    # фоновую задачу, но дождаться её на самом деле»: shield() защищает
    # `inner` от отмены снаружи; если она всё же случилась, мы выставляем
    # флаг остановки и await'им `inner` напрямую (не через shield), пока
    # поток реально не завершится, и только потом поднимаем CancelledError
    # дальше. Тем самым `WorkerManager.stop()`'s asyncio.wait(timeout=...)
    # видит настоящее завершение потока, а не факт, что кто-то попросил
    # остановиться.
    inner = asyncio.ensure_future(asyncio.to_thread(_blocking_loop, stop_event))
    try:
        await asyncio.shield(inner)
    except asyncio.CancelledError as cancelled:
        stop_event.set()
        # Ждать поток нужно ТОЖЕ под shield и столько раз, сколько потребуется.
        # Голый `await inner` переживал только первую отмену: после того как
        # stop() истёк по таймауту (менеджер ставит error и продолжает
        # отслеживать таску), второй stop() отменяет таску снова, отмена
        # приходит прямо в `await inner` и отменяет сам `inner` — run_worker()
        # завершался мгновенно, stop() рапортовал "stopped" и забывал таску,
        # хотя поток Selenium был жив. Следующий start() открывал второй
        # Chrome. Сценарий не экзотический: apply_to_vacancy — это
        # WebDriverWait(15) плюс ~7–13 секунд фиксированных пауз, так что даже
        # послушный воркер регулярно не укладывается в 10-секундный бюджет
        # менеджера, а пользователь жмёт «Стоп» второй раз.
        while not inner.done():
            try:
                await asyncio.shield(inner)
            except asyncio.CancelledError:
                continue  # повторный stop(): проглатываем, поток ещё не вышел
        raise cancelled
