"""Исполнитель сценария Selenium, сохранённого пользователем в настройках (L5).

Сценарий — список словарей из `GlobalSettings.hh_selenium_steps`, целиком
пользовательский ввод. Диспетчеризация идёт по фиксированному белому
списку типов шагов (`ALLOWED_TYPES`) — никакого `eval`/`exec` и никакой
интерполяции пользовательских значений в текст скрипта: шаг `scroll`
передаёт число через `arguments[0]` в `execute_script`, а не подставляет
его в строку JS.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

CSS = "css selector"
XPATH = "xpath"
ALLOWED_TYPES = ("click", "input", "wait", "wait_element", "select", "scroll")
DEFAULT_WAIT_SECONDS = 2.0


@dataclass(frozen=True)
class Step:
    type: str
    by: str = CSS
    selector: str = ""
    value: str = ""
    seconds: float = 0.0


def _infer_by(selector: str) -> str:
    return XPATH if selector.startswith(("/", "(", "./")) else CSS


def _parse_finite_seconds(raw: Any) -> float:
    """Секунды ожидания. Отсутствующее/нечисловое значение — не опечатка, а
    просто "не задано": молча превращается в 0.0 (вызывающая сторона сама
    решает дефолт для типа `wait`). Но `inf`/`-inf`/`nan` — валидные
    значения для `float()`, которые проходят эту конвертацию без ошибки, а
    затем валят `time.sleep()` уже во время выполнения сценария:
    `OverflowError`, не `ValueError`, — и не ловится существующим
    обработчиком опечаток на уровне цикла воркера. Ловим здесь и превращаем
    в громкий `ValueError` на этапе разбора, до первого шага сценария."""
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(seconds):
        raise ValueError(f"нечисловое время ожидания в сценарии: {raw!r}")
    return seconds


def _validate_scroll_amount(value: str) -> None:
    """Дистанция прокрутки хранится строкой в `Step.value` (общее поле для
    всех типов шагов), но обязана быть конечным числом — иначе
    `int(float(...))` в `run_steps` падает `OverflowError`/`ValueError` уже
    после клика по кнопке отклика на живом hh.ru. Проверяем на этапе
    разбора, пустое значение (используется дефолт 300) не трогаем."""
    if not value:
        return
    try:
        amount = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"нечисловое значение прокрутки в сценарии: {value!r}") from None
    if not math.isfinite(amount):
        raise ValueError(f"нечисловое значение прокрутки в сценарии: {value!r}")


def parse_steps(raw: list[dict]) -> list[Step]:
    """Парсит сохранённый сценарий. Бросает `ValueError` на неизвестном типе шага
    или на нечисловом (в т.ч. бесконечном) значении времени/прокрутки —
    опечатка в настройках должна упасть здесь, а не выполниться наполовину."""
    steps: list[Step] = []
    for item in raw:
        kind = str(item.get("type", ""))
        if kind not in ALLOWED_TYPES:
            raise ValueError(f"неизвестный тип шага: {kind!r}")
        selector = str(item.get("selector") or "")
        value = str(item.get("value") or "")
        raw_seconds = item.get("seconds", item.get("value") if kind == "wait" else 0)
        seconds = _parse_finite_seconds(raw_seconds)
        if kind == "wait" and seconds <= 0:
            seconds = DEFAULT_WAIT_SECONDS
        if kind == "scroll":
            _validate_scroll_amount(value)
        steps.append(Step(
            type=kind,
            by=str(item.get("by") or _infer_by(selector)),
            selector=selector,
            value=value,
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
