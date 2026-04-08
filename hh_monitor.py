import time
import json
import os
import logging
import random
from datetime import datetime, date
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException,
    ElementClickInterceptedException, WebDriverException
)
from webdriver_manager.chrome import ChromeDriverManager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
HH_SENT_PATH = os.path.join(BASE_DIR, "hh_sent.json")
HH_LOG_PATH = os.path.join(BASE_DIR, "logs", "hh.log")
HH_COOKIES_PATH = os.path.join(BASE_DIR, "hh_cookies.json")

os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)

# --- Логирование ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(HH_LOG_PATH, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("hh_monitor")

# --- Дефолтные HH настройки ---
HH_DEFAULTS = {
    "hh_enabled": False,
    "hh_keywords": ["QA", "тестировщик", "Junior QA", "стажировка QA"],
    "hh_exclude": ["senior", "lead", "middle", "5+ лет"],
    "hh_regions": ["Казахстан", "Россия", "Беларусь", "Украина", "Узбекистан"],
    "hh_area_ids": [40, 113, 16, 5, 275],  # HH area IDs
    "hh_salary_from": 0,
    "hh_cover_letter": "",
    "hh_max_per_day": 20,
    "hh_delay_min": 30,
    "hh_delay_max": 90,
    "hh_experience": "noExperience",  # noExperience, between1And3, between3And6, moreThan6
    "hh_employment": ["full", "part", "probation"],
    "hh_schedule": ["remote", "fullDay", "flexible"],
    "hh_search_period": 1,  # дней (1, 3, 7, 14, 30)
    "hh_resume_id": "",  # ID резюме на HH
}

def load_config() -> dict:
    cfg = HH_DEFAULTS.copy()
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            try:
                cfg.update(json.load(f))
            except Exception:
                pass
    return cfg

def load_sent() -> dict:
    """Загружаем историю откликов"""
    if os.path.exists(HH_SENT_PATH):
        with open(HH_SENT_PATH, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except Exception:
                pass
    return {}

def save_sent(sent: dict):
    with open(HH_SENT_PATH, "w", encoding="utf-8") as f:
        json.dump(sent, f, ensure_ascii=False, indent=2)

def save_cookies(driver):
    cookies = driver.get_cookies()
    with open(HH_COOKIES_PATH, "w", encoding="utf-8") as f:
        json.dump(cookies, f)
    log.info("[COOKIES] Сохранены")

def load_cookies(driver):
    if not os.path.exists(HH_COOKIES_PATH):
        return False
    with open(HH_COOKIES_PATH, "r", encoding="utf-8") as f:
        cookies = json.load(f)
    # Сначала открываем домен чтобы можно было добавить cookies
    driver.get("https://hh.ru")
    time.sleep(2)
    for cookie in cookies:
        try:
            # Убираем поля которые вызывают ошибки в Selenium
            clean = {k: v for k, v in cookie.items()
                     if k in ("name", "value", "domain", "path", "secure", "httpOnly")}
            # Фиксим домен
            if "domain" in clean and clean["domain"].startswith("."):
                clean["domain"] = clean["domain"]
            driver.add_cookie(clean)
        except Exception:
            pass
    driver.refresh()
    time.sleep(3)
    return True

def is_logged_in(driver) -> bool:
    try:
        driver.get("https://hh.ru")
        time.sleep(3)
        # Проверяем наличие аватара или имени пользователя
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

def setup_driver(headless: bool = False) -> webdriver.Chrome:
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
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver

def build_search_url(keyword: str, cfg: dict, area_id: int) -> str:
    """Строим URL поиска с фильтрами"""
    params = [
        f"text={keyword.replace(' ', '+')}",
        f"area={area_id}",
        f"search_period={cfg.get('hh_search_period', 1)}",
        "per_page=20",
        "order_by=publication_time",
    ]
    experience = cfg.get("hh_experience", "noExperience")
    if experience:
        params.append(f"experience={experience}")
    salary_from = cfg.get("hh_salary_from", 0)
    if salary_from:
        params.append(f"salary={salary_from}")
        params.append("only_with_salary=true")
    for emp in cfg.get("hh_employment", []):
        params.append(f"employment={emp}")
    for sch in cfg.get("hh_schedule", []):
        params.append(f"schedule={sch}")
    return "https://hh.ru/search/vacancy?" + "&".join(params)

def get_vacancies_from_page(driver, cfg: dict) -> list:
    """Собираем вакансии со страницы поиска"""
    vacancies = []
    wait = WebDriverWait(driver, 10)
    try:
        wait.until(EC.presence_of_element_located(
            (By.XPATH, "//div[@data-qa='vacancy-serp__results']")
        ))
    except TimeoutException:
        log.warning("[HH] Результаты поиска не загрузились")
        return []

    items = driver.find_elements(
        By.XPATH, "//div[@data-qa='vacancy-serp__vacancy']"
    )
    exclude = [w.lower() for w in cfg.get("hh_exclude", [])]

    for item in items:
        try:
            title_el = item.find_element(
                By.XPATH, ".//a[@data-qa='serp-item__title']"
            )
            title = title_el.text.strip()
            url = title_el.get_attribute("href").split("?")[0]
            vacancy_id = url.split("/")[-1]

            # Проверяем исключения
            title_lower = title.lower()
            if any(ex in title_lower for ex in exclude):
                continue

            # Компания
            try:
                company = item.find_element(
                    By.XPATH, ".//a[@data-qa='vacancy-serp__vacancy-employer']"
                ).text.strip()
            except NoSuchElementException:
                company = "Не указана"

            # Зарплата
            try:
                salary = item.find_element(
                    By.XPATH, ".//span[@data-qa='vacancy-serp__vacancy-compensation']"
                ).text.strip()
            except NoSuchElementException:
                salary = "Не указана"

            # Город
            try:
                city = item.find_element(
                    By.XPATH, ".//div[@data-qa='vacancy-serp__vacancy-address']"
                ).text.strip()
            except NoSuchElementException:
                city = ""

            vacancies.append({
                "id": vacancy_id,
                "title": title,
                "company": company,
                "salary": salary,
                "city": city,
                "url": url,
                "found_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
        except Exception as e:
            log.debug(f"[HH] Ошибка парсинга вакансии: {e}")
            continue

    return vacancies

def apply_to_vacancy(driver, vacancy: dict, cfg: dict) -> bool:
    """Откликаемся на вакансию"""
    wait = WebDriverWait(driver, 15)
    cover_letter = cfg.get("hh_cover_letter", "")
    resume_id = cfg.get("hh_resume_id", "")

    try:
        driver.get(vacancy["url"])
        time.sleep(random.uniform(2, 4))

        # Ищем кнопку отклика
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
            log.warning(f"[HH] Кнопка отклика не найдена: {vacancy['title']}")
            return False

        # Проверяем что ещё не откликались
        btn_text = apply_btn.text.lower()
        if "откликнулись" in btn_text or "отклик отправлен" in btn_text:
            log.info(f"[HH][SKIP] Уже откликались: {vacancy['title']}")
            return False

        apply_btn.click()
        time.sleep(random.uniform(1.5, 3))

        # Выбираем резюме если есть несколько
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

        # Сопроводительное письмо
        if cover_letter:
            try:
                letter_area = driver.find_element(
                    By.XPATH,
                    "//textarea[@data-qa='vacancy-response-letter-textarea'] | "
                    "//textarea[@placeholder]"
                )
                letter_area.clear()
                # Печатаем как человек
                for char in cover_letter[:500]:
                    letter_area.send_keys(char)
                    if random.random() < 0.05:
                        time.sleep(random.uniform(0.05, 0.15))
                time.sleep(1)
            except NoSuchElementException:
                log.warning(f"[HH] Поле письма не найдено для: {vacancy['title']}")

        # Кнопка отправки отклика
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
            log.warning(f"[HH] Не удалось отправить отклик: {vacancy['title']}")
            return False

        time.sleep(random.uniform(2, 4))
        log.info(f"[HH][OK] Отклик отправлен: {vacancy['title']} — {vacancy['company']}")
        return True

    except WebDriverException as e:
        log.error(f"[HH][ERROR] {vacancy['title']}: {e}")
        return False

def get_my_responses(driver) -> list:
    """Получаем список откликов с HH"""
    responses = []
    try:
        driver.get("https://hh.ru/applicant/negotiations")
        time.sleep(3)
        wait = WebDriverWait(driver, 10)
        wait.until(EC.presence_of_element_located(
            (By.XPATH, "//div[@class='negotiations-list']|//div[contains(@class,'negotiations')]")
        ))
        items = driver.find_elements(
            By.XPATH, "//div[@data-qa='negotiations-list-item']"
        )
        for item in items:
            try:
                title = item.find_element(
                    By.XPATH, ".//a[@data-qa='negotiations-vacancy-title']"
                ).text.strip()
                url = item.find_element(
                    By.XPATH, ".//a[@data-qa='negotiations-vacancy-title']"
                ).get_attribute("href")
                try:
                    status = item.find_element(
                        By.XPATH, ".//span[@data-qa='negotiations-item-status']"
                    ).text.strip()
                except NoSuchElementException:
                    status = "Ожидание"
                try:
                    company = item.find_element(
                        By.XPATH, ".//a[@data-qa='negotiations-company-name']"
                    ).text.strip()
                except NoSuchElementException:
                    company = ""
                responses.append({
                    "title": title,
                    "company": company,
                    "status": status,
                    "url": url,
                })
            except Exception:
                continue
    except Exception as e:
        log.error(f"[HH] Ошибка получения откликов: {e}")
    return responses

class HHMonitor:
    def __init__(self):
        self.driver = None
        self.sent = load_sent()
        self.sent_today = 0
        self.last_date = date.today()
        self.running = False
        self.found_today = 0

    def start(self, headless: bool = False):
        self.running = True
        self.driver = setup_driver(headless)
        log.info("[HH] Браузер запущен")

        # Пробуем загрузить cookies
        if load_cookies(self.driver):
            if is_logged_in(self.driver):
                log.info("[HH] Авторизация через cookies успешна")
            else:
                log.warning("[HH] Cookies устарели — требуется ручная авторизация")
                self._manual_login()
        else:
            log.info("[HH] Cookies не найдены — требуется авторизация")
            self._manual_login()

        self._run_loop()

    def _manual_login(self):
        """Открываем страницу входа и ждём пока пользователь авторизуется"""
        log.info("[HH] Открываю страницу входа — войдите вручную в браузере")
        self.driver.get("https://hh.ru/account/login")
        print("\n" + "="*50)
        print("Войдите в аккаунт HH в открывшемся браузере.")
        print("После входа нажмите Enter здесь...")
        print("="*50)
        input()
        # Сохраняем cookies с обоих доменов
        save_cookies(self.driver)
        # Также сохраняем сессию через localStorage
        try:
            self.driver.get("https://hh.ru")
            time.sleep(2)
            save_cookies(self.driver)
        except Exception:
            pass
        log.info("[HH] Авторизация завершена, cookies сохранены")

    def _reset_daily(self):
        self.sent_today = 0
        self.found_today = 0
        self.last_date = date.today()
        log.info("[HH][DAILY RESET] Новый день — счётчики сброшены")

    def _run_loop(self):
        cfg = load_config()
        log.info(f"[HH] Старт мониторинга. Ключевые слова: {cfg.get('hh_keywords')}")
        log.info(f"[HH] Регионы: {cfg.get('hh_regions')}")
        log.info(f"[HH] Макс. откликов в день: {cfg.get('hh_max_per_day', 20)}")

        while self.running:
            try:
                if date.today() != self.last_date:
                    self._reset_daily()

                cfg = load_config()
                max_per_day = cfg.get("hh_max_per_day", 20)

                if self.sent_today >= max_per_day:
                    log.info(f"[HH] Дневной лимит достигнут ({max_per_day}). Жду до следующего дня...")
                    time.sleep(3600)
                    continue

                keywords = cfg.get("hh_keywords", [])
                area_ids = cfg.get("hh_area_ids", [113])

                for keyword in keywords:
                    if not self.running:
                        break
                    for area_id in area_ids:
                        if not self.running:
                            break
                        if self.sent_today >= max_per_day:
                            break

                        url = build_search_url(keyword, cfg, area_id)
                        log.info(f"[HH] Поиск: '{keyword}' в регионе {area_id}")
                        self.driver.get(url)
                        time.sleep(random.uniform(2, 4))

                        vacancies = get_vacancies_from_page(self.driver, cfg)
                        new_vacancies = [
                            v for v in vacancies
                            if v["id"] not in self.sent
                        ]
                        self.found_today += len(new_vacancies)
                        log.info(f"[HH] Найдено новых: {len(new_vacancies)}")

                        for vacancy in new_vacancies:
                            if not self.running:
                                break
                            if self.sent_today >= max_per_day:
                                break

                            success = apply_to_vacancy(self.driver, vacancy, cfg)

                            if success:
                                self.sent[vacancy["id"]] = {
                                    **vacancy,
                                    "applied_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                    "status": "отклик отправлен",
                                    "cover_letter": cfg.get("hh_cover_letter", "")[:100],
                                }
                                save_sent(self.sent)
                                self.sent_today += 1

                                delay = random.randint(
                                    cfg.get("hh_delay_min", 30),
                                    cfg.get("hh_delay_max", 90)
                                )
                                log.info(f"[HH] Пауза {delay} сек...")
                                time.sleep(delay)
                            else:
                                # Пропускаем но помечаем чтобы не возвращаться
                                self.sent[vacancy["id"]] = {
                                    **vacancy,
                                    "applied_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                    "status": "пропущено",
                                }
                                save_sent(self.sent)

                            time.sleep(random.uniform(1, 3))

                # Пауза между циклами
                interval = cfg.get("hh_check_interval", 1800)
                log.info(f"[HH] Цикл завершён. Следующий через {interval//60} мин.")
                time.sleep(interval)

            except WebDriverException as e:
                log.error(f"[HH][ERROR] Ошибка браузера: {e}")
                time.sleep(60)
            except Exception as e:
                log.error(f"[HH][ERROR] {e}")
                time.sleep(60)

    def stop(self):
        self.running = False
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
        log.info("[HH] Монитор остановлен")

if __name__ == "__main__":
    monitor = HHMonitor()
    try:
        monitor.start(headless=False)
    except KeyboardInterrupt:
        monitor.stop()
