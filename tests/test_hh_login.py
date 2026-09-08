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
