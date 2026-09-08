import pytest

from job_monitor.workers.hh_steps import parse_steps, run_steps


class FakeElement:
    def __init__(self):
        self.clicked = False
        self.typed = ""

    def click(self):
        self.clicked = True

    def clear(self):
        self.typed = ""

    def send_keys(self, text):
        self.typed += text


class FakeDriver:
    def __init__(self):
        self.element = FakeElement()
        self.scripts: list[str] = []
        self.queried: list[tuple[str, str]] = []

    def find_element(self, by, selector):
        self.queried.append((by, selector))
        return self.element

    def execute_script(self, script, *args):
        self.scripts.append(script)


def no_wait(_driver, _timeout=None):
    class _Wait:
        def until(self, condition):
            return condition(_driver)
    return _Wait()


def test_parse_rejects_unknown_step_type():
    with pytest.raises(ValueError, match="неизвестный тип шага"):
        parse_steps([{"type": "execute_script", "value": "alert(1)"}])


def test_parse_infers_locator_kind():
    steps = parse_steps([
        {"type": "click", "selector": "//button[@id='x']"},
        {"type": "click", "selector": ".btn-primary"},
    ])
    assert steps[0].by == "xpath"
    assert steps[1].by == "css selector"


def test_parse_explicit_by_wins_over_inference():
    steps = parse_steps([
        {"type": "click", "selector": "//button[@id='x']", "by": "css selector"},
    ])
    assert steps[0].by == "css selector"


def test_parse_defaults_wait_seconds():
    assert parse_steps([{"type": "wait"}])[0].seconds == 2.0


# ── Finding 1 (review): `float("inf")` passes `float()` without raising, but
# `int(float("inf"))` and `time.sleep(float("inf"))` both raise
# `OverflowError` — which is not a `ValueError`, so it used to slip past
# every existing guard and kill the worker thread. `parse_steps` must now
# reject non-finite numeric fields loudly, at parse time, before any step
# in the scenario executes (and, for a merely non-numeric string, keep
# defaulting gracefully as before — that path never crashed).


def test_parse_rejects_infinite_wait_seconds():
    with pytest.raises(ValueError, match="время ожидания"):
        parse_steps([{"type": "wait", "seconds": "inf"}])


def test_parse_rejects_negative_infinite_wait_seconds():
    with pytest.raises(ValueError, match="время ожидания"):
        parse_steps([{"type": "wait", "seconds": "-inf"}])


def test_parse_survives_non_numeric_wait_seconds():
    # Non-numeric already defaulted gracefully before this fix; proving it
    # still does, and did not silently start raising instead.
    assert parse_steps([{"type": "wait", "seconds": "not-a-number"}])[0].seconds == 2.0


def test_parse_rejects_infinite_scroll_value():
    with pytest.raises(ValueError, match="прокрутки"):
        parse_steps([{"type": "scroll", "value": "inf"}])


def test_parse_rejects_negative_infinite_scroll_value():
    with pytest.raises(ValueError, match="прокрутки"):
        parse_steps([{"type": "scroll", "value": "-inf"}])


def test_parse_rejects_non_numeric_scroll_value():
    # Previously this only failed inside run_steps at execution time (after
    # the response button was already clicked); it must still fail with a
    # catchable ValueError, not crash — now it fails earlier, at parse time.
    with pytest.raises(ValueError, match="прокрутки"):
        parse_steps([{"type": "scroll", "value": "not-a-number"}])


def test_click_step(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    driver = FakeDriver()
    run_steps(driver, parse_steps([{"type": "click", "selector": ".apply"}]), no_wait)
    assert driver.element.clicked is True


def test_input_step(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    driver = FakeDriver()
    run_steps(driver, parse_steps([{"type": "input", "selector": "#letter", "value": "привет"}]), no_wait)
    assert driver.element.typed == "привет"


def test_scroll_step_uses_script_without_user_input(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    driver = FakeDriver()
    run_steps(driver, parse_steps([{"type": "scroll", "value": "500"}]), no_wait)
    assert driver.scripts == ["window.scrollBy(0, arguments[0]);"]


def test_wait_element_step_uses_wait_factory(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    driver = FakeDriver()
    run_steps(
        driver,
        parse_steps([{"type": "wait_element", "selector": "#thing"}]),
        no_wait,
    )
    assert driver.queried == [("css selector", "#thing")]
