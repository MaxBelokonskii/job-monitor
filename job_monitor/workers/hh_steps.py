"""Исполнитель сценария Selenium, сохранённого пользователем в настройках (L5).

Сценарий — список словарей из `AppSettings.hh_selenium_steps`, целиком
пользовательский ввод. Диспетчеризация идёт по фиксированному белому
списку типов шагов (`ALLOWED_TYPES`) — никакого `eval`/`exec` и никакой
интерполяции пользовательских значений в текст скрипта: шаг `scroll`
передаёт число через `arguments[0]` в `execute_script`, а не подставляет
его в строку JS.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

CSS = "css selector"
XPATH = "xpath"
ALLOWED_TYPES = ("click", "input", "wait", "wait_element", "select", "scroll")


@dataclass(frozen=True)
class Step:
    type: str
    by: str = CSS
    selector: str = ""
    value: str = ""
    seconds: float = 0.0


def _infer_by(selector: str) -> str:
    return XPATH if selector.startswith(("/", "(", "./")) else CSS


def parse_steps(raw: list[dict]) -> list[Step]:
    """Парсит сохранённый сценарий. Бросает `ValueError` на неизвестном типе шага —
    опечатка в настройках должна упасть здесь, а не выполниться наполовину."""
    steps: list[Step] = []
    for item in raw:
        kind = str(item.get("type", ""))
        if kind not in ALLOWED_TYPES:
            raise ValueError(f"неизвестный тип шага: {kind!r}")
        selector = str(item.get("selector") or "")
        seconds = item.get("seconds", item.get("value") if kind == "wait" else 0)
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            seconds = 0.0
        if kind == "wait" and seconds <= 0:
            seconds = 2.0
        steps.append(Step(
            type=kind,
            by=str(item.get("by") or _infer_by(selector)),
            selector=selector,
            value=str(item.get("value") or ""),
            seconds=seconds,
        ))
    return steps


def run_steps(driver: Any, steps: list[Step], wait_factory: Callable[..., Any]) -> None:
    """Исполняет сценарий. Диспетчеризация по белому списку — никакого eval."""
    for step in steps:
        if step.type == "wait":
            time.sleep(step.seconds)
            continue
        if step.type == "scroll":
            amount = int(float(step.value or 300))
            driver.execute_script("window.scrollBy(0, arguments[0]);", amount)
            time.sleep(0.3)
            continue
        if step.type == "wait_element":
            wait_factory(driver, 10).until(
                lambda d, by=step.by, selector=step.selector: d.find_element(by, selector)
            )
            continue

        element = driver.find_element(step.by, step.selector)
        if step.type == "click":
            element.click()
        elif step.type == "input":
            element.clear()
            element.send_keys(step.value)
        elif step.type == "select":
            from selenium.webdriver.support.ui import Select

            Select(element).select_by_visible_text(step.value)
        time.sleep(0.5)
