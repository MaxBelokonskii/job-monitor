import json
import threading
import time

import pytest

from job_monitor.workers.hh import HhLogin, HhLoginState, load_cookies, save_cookies


class FakeDriver:
    def __init__(self, logged_in: bool = False):
        self.logged_in = logged_in
        self.visited: list[str] = []
        self.added: list[dict] = []
        self.quit_called = False

    def get(self, url: str) -> None:
        self.visited.append(url)

    def get_cookies(self) -> list[dict]:
        return [{"name": "sid", "value": "1", "domain": ".hh.ru", "path": "/",
                 "secure": True, "httpOnly": True, "sameSite": "None", "expiry": 1}]

    def add_cookie(self, cookie: dict) -> None:
        self.added.append(cookie)

    def refresh(self) -> None:
        pass

    def quit(self) -> None:
        self.quit_called = True


def test_save_cookies_writes_file(tmp_path):
    target = tmp_path / "hh_cookies.json"
    save_cookies(FakeDriver(), target)
    assert json.loads(target.read_text(encoding="utf-8"))[0]["name"] == "sid"


def test_load_cookies_drops_fields_selenium_rejects(tmp_path):
    target = tmp_path / "hh_cookies.json"
    save_cookies(FakeDriver(), target)
    driver = FakeDriver()
    assert load_cookies(driver, target) is True
    assert set(driver.added[0]) == {"name", "value", "domain", "path", "secure", "httpOnly"}


def test_load_cookies_without_file(tmp_path):
    assert load_cookies(FakeDriver(), tmp_path / "nope.json") is False


def test_login_flow_never_blocks(tmp_path):
    driver = FakeDriver(logged_in=True)
    login = HhLogin(driver_factory=lambda: driver, cookies_path=tmp_path / "c.json",
                    is_logged_in=lambda _driver: _driver.logged_in)
    assert login.state is HhLoginState.logged_out
    assert login.start() is HhLoginState.browser_open
    assert "hh.ru/account/login" in driver.visited[-1]
    assert login.confirm() is HhLoginState.logged_in
    assert (tmp_path / "c.json").exists()
    assert driver.quit_called is True


def test_confirm_without_actual_login_stays_open(tmp_path):
    driver = FakeDriver(logged_in=False)
    login = HhLogin(driver_factory=lambda: driver, cookies_path=tmp_path / "c.json",
                    is_logged_in=lambda _driver: _driver.logged_in)
    login.start()
    assert login.confirm() is HhLoginState.browser_open
    assert not (tmp_path / "c.json").exists()


def test_confirm_before_start_is_an_error(tmp_path):
    login = HhLogin(driver_factory=lambda: FakeDriver(), cookies_path=tmp_path / "c.json",
                    is_logged_in=lambda _driver: True)
    with pytest.raises(RuntimeError):
        login.confirm()


# ── Брошенный Chrome: окно входа тоже должно кем-то гаситься ───────────


class CountingDriver(FakeDriver):
    def __init__(self, logged_in: bool = False, quit_raises: bool = False):
        super().__init__(logged_in=logged_in)
        self.quit_calls = 0
        self._quit_raises = quit_raises

    def quit(self) -> None:
        self.quit_calls += 1
        self.quit_called = True
        if self._quit_raises:
            raise RuntimeError("chromedriver уже мёртв")


def _login(driver, tmp_path, logged_in=None):
    return HhLogin(
        driver_factory=lambda: driver,
        cookies_path=tmp_path / "c.json",
        is_logged_in=(lambda d: d.logged_in) if logged_in is None else logged_in,
    )


def test_close_quits_the_browser_and_is_idempotent(tmp_path):
    driver = CountingDriver()
    login = _login(driver, tmp_path)
    login.start()
    login.close()
    assert driver.quit_calls == 1
    assert login._driver is None
    assert login.state is HhLoginState.logged_out
    login.close()
    login.close()
    assert driver.quit_calls == 1, "повторный close() не должен трогать уже закрытый драйвер"


def test_close_without_a_browser_is_harmless(tmp_path):
    login = _login(CountingDriver(), tmp_path)
    login.close()
    assert login.state is HhLoginState.logged_out


def test_close_keeps_logged_in_state_when_there_is_nothing_to_close(tmp_path):
    """`logged_in` означает «cookies сохранены» — закрытие уже закрытого окна
    не повод врать в GET /api/hh/login/status."""
    driver = CountingDriver(logged_in=True)
    login = _login(driver, tmp_path)
    login.start()
    assert login.confirm() is HhLoginState.logged_in
    login.close()
    assert login.state is HhLoginState.logged_in


def test_close_swallows_a_failing_quit(tmp_path):
    driver = CountingDriver(quit_raises=True)
    login = _login(driver, tmp_path)
    login.start()
    login.close()  # не должно бросить: это путь остановки приложения
    assert login._driver is None


def test_confirm_does_not_leak_the_driver_when_saving_cookies_fails(tmp_path, monkeypatch):
    """`save_cookies` пишет файл и может бросить (недоступный каталог данных).
    Раньше `driver.quit()` стоял строкой ниже и до него дело не доходило."""
    driver = CountingDriver(logged_in=True)
    login = _login(driver, tmp_path)
    login.start()
    monkeypatch.setattr(
        "job_monitor.workers.hh.save_cookies",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("нет места")),
    )
    with pytest.raises(OSError):
        login.confirm()
    assert driver.quit_calls == 1, "окно Chrome осталось висеть после неудачного confirm()"
    assert login._driver is None


def test_confirm_does_not_leak_the_driver_when_the_login_check_fails(tmp_path):
    driver = CountingDriver()
    login = _login(driver, tmp_path, logged_in=lambda _d: (_ for _ in ()).throw(RuntimeError("driver умер")))
    login.start()
    with pytest.raises(RuntimeError):
        login.confirm()
    assert driver.quit_calls == 1
    assert login._driver is None


def test_login_cancel_route_closes_the_window(client):
    """Роут отмены — единственный способ закрыть окно из UI, не подтверждая вход."""
    from job_monitor.workers.hh import login as app_login

    driver = CountingDriver()
    app_login._driver = driver
    app_login.state = HhLoginState.browser_open
    try:
        response = client.post("/api/hh/login/cancel")
        assert response.status_code == 200
        assert response.json() == {"state": "logged_out"}
        assert driver.quit_calls == 1
        assert app_login._driver is None
    finally:
        app_login._driver = None
        app_login.state = HhLoginState.logged_out


# ── Путь к cookies вычисляется лениво ──────────────────────────────────


def test_cookies_path_is_resolved_at_use_not_at_construction(tmp_path, monkeypatch):
    """`HhLogin` должен уважать `JOB_MONITOR_DATA_DIR`, выставленный после
    того, как модуль уже импортирован.

    Синглтон `job_monitor.workers.hh.login` собирается на импорте, и раньше
    получал готовый `paths.hh_cookies()`. Значит путь был заморожен на
    момент импорта: любой, кто переставил каталог данных позже (autouse-
    фикстура тестов; пользователь, запускающий приложение с переменной в
    окружении обёртки), получал cookies hh.ru в старом каталоге —
    молча."""
    from job_monitor import paths
    from job_monitor.workers.hh import login as app_login

    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path / "early"))
    frozen = app_login.cookies_path
    assert frozen == tmp_path / "early" / "hh_cookies.json"

    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path / "late"))
    assert app_login.cookies_path == tmp_path / "late" / "hh_cookies.json"
    assert app_login.cookies_path == paths.hh_cookies()


def test_confirm_writes_cookies_to_the_current_data_dir(tmp_path, monkeypatch):
    """То же свойство, но проверенное записью, а не сравнением путей."""
    from job_monitor.workers.hh import login as app_login

    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path / "current"))
    driver = CountingDriver(logged_in=True)
    saved_factory, saved_check = app_login._driver_factory, app_login._is_logged_in
    app_login._driver_factory = lambda: driver
    app_login._is_logged_in = lambda d: d.logged_in
    try:
        app_login.start()
        assert app_login.confirm() is HhLoginState.logged_in
    finally:
        app_login._driver_factory, app_login._is_logged_in = saved_factory, saved_check
        app_login._driver = None
        app_login.state = HhLoginState.logged_out
    assert (tmp_path / "current" / "hh_cookies.json").exists()


def test_lifespan_shutdown_closes_an_abandoned_login_window():
    """`lifespan` знал только про manager.stop_all(); окно входа переживало
    остановку приложения живым процессом Chrome."""
    from fastapi.testclient import TestClient

    from api.main import app
    from job_monitor.workers.hh import login as app_login

    driver = CountingDriver()
    try:
        with TestClient(app, base_url="http://127.0.0.1:8000"):
            app_login._driver = driver
            app_login.state = HhLoginState.browser_open
        assert driver.quit_calls == 1, "выход из lifespan оставил окно входа открытым"
        assert app_login._driver is None
    finally:
        app_login._driver = None
        app_login.state = HhLoginState.logged_out


# ── Выход приложения не может зависнуть на driver.quit() ───────────────


class HangingDriver(FakeDriver):
    """chromedriver, который перестал отвечать: `quit()` не возвращается.

    Именно этот отказ — причина, по которой `HhLogin.close()` глотает
    исключения из `quit()`. Но зависание не исключение, и `except` от него
    не спасает: нужен бюджет времени.
    """

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def quit(self) -> None:
        self.entered.set()
        self.quit_called = True
        self.release.wait(60)


def test_lifespan_shutdown_is_not_held_hostage_by_a_wedged_quit(monkeypatch, caplog):
    """Ctrl+C при неотвечающем chromedriver должен завершать приложение.

    Раньше здесь стоял `await asyncio.to_thread(login.close)` без бюджета —
    рядом с `manager.stop(timeout=10.0)`, у которого развёрнуто объяснено,
    почему бюджет обязателен. Воспроизведение: `quit()`, который не
    возвращается, → выход из lifespan не наступал никогда.
    """
    from fastapi.testclient import TestClient

    from api import main
    from job_monitor.workers.hh import login as app_login

    monkeypatch.setattr(main, "LOGIN_CLOSE_TIMEOUT", 0.2)
    driver = HangingDriver()
    try:
        with caplog.at_level("ERROR"):
            started = time.monotonic()
            with TestClient(main.app, base_url="http://127.0.0.1:8000"):
                app_login._driver = driver
                app_login.state = HhLoginState.browser_open
            elapsed = time.monotonic() - started
        assert driver.entered.wait(5), "close() даже не дошёл до quit()"
        assert elapsed < 5, f"выход из lifespan занял {elapsed:.1f}s — бюджет не сработал"
        assert any("не закрылось" in record.message for record in caplog.records), (
            "оставшийся живым Chrome должен быть назван в логе, а не проглочен"
        )
    finally:
        driver.release.set()
        app_login._driver = None
        app_login.state = HhLoginState.logged_out


def test_the_close_thread_is_a_daemon(monkeypatch):
    """Бюджета мало: поток отменить нельзя, и non-daemon поток удержал бы
    процесс на выходе интерпретатора, даже если lifespan перестал его ждать.
    `asyncio.to_thread` даёт ровно такой поток (общий ThreadPoolExecutor,
    threads=non-daemon, джойнятся atexit), поэтому close идёт своим потоком.
    """
    import asyncio

    from api import main
    from job_monitor.workers.hh import login as app_login

    seen: list[bool] = []

    def spying_close() -> None:
        seen.append(threading.current_thread().daemon)

    monkeypatch.setattr(app_login, "close", spying_close)
    asyncio.run(main.close_login_window(timeout=5))
    assert seen == [True], f"close() выполнился в потоке daemon={seen}"


# ── «Закрыть окно входа» → «Я вошёл» — это 400, а не 500 ───────────────


def test_confirm_without_an_open_window_is_a_400_not_a_500(client):
    """Кнопка «Закрыть окно входа» видна всегда и стоит рядом с «Я вошёл»,
    так что последовательность достижима в два клика. Раньше она приводила в
    `confirm()` без драйвера, и необработанный RuntimeError давал 500."""
    from job_monitor.workers.hh import login as app_login

    app_login._driver = None
    app_login.state = HhLoginState.logged_out
    response = client.post("/api/hh/login/confirm")
    assert response.status_code == 400
    assert "Открыть вход" in response.json()["detail"]


def test_close_then_confirm_is_the_reproduction(client):
    """Тот же дефект, воспроизведённый последовательностью кликов из UI."""
    from job_monitor.workers.hh import login as app_login

    driver = CountingDriver()
    saved_factory = app_login._driver_factory
    app_login._driver_factory = lambda: driver
    try:
        assert client.post("/api/hh/login/start").status_code == 200
        assert client.post("/api/hh/login/cancel").status_code == 200
        assert client.post("/api/hh/login/confirm").status_code == 400
    finally:
        app_login._driver_factory = saved_factory
        app_login._driver = None
        app_login.state = HhLoginState.logged_out


def test_a_real_selenium_failure_inside_confirm_is_not_disguised_as_a_400(client):
    """400 предназначен ровно для ошибки последовательности вызовов. Упавший
    посреди проверки драйвер — настоящая поломка, и превращать её в «нажмите
    Открыть вход» значило бы врать пользователю."""
    from job_monitor.workers.hh import login as app_login

    driver = CountingDriver()
    saved_check = app_login._is_logged_in
    app_login._driver = driver
    app_login.state = HhLoginState.browser_open
    app_login._is_logged_in = lambda _d: (_ for _ in ()).throw(RuntimeError("driver умер"))
    try:
        with pytest.raises(RuntimeError, match="driver умер"):
            client.post("/api/hh/login/confirm")
    finally:
        app_login._is_logged_in = saved_check
        app_login._driver = None
        app_login.state = HhLoginState.logged_out
