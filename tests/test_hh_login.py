import json

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
