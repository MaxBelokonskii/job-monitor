"""Справочники hh.ru: коды и подписи для переключателей интерфейса.

Отдаёт бэкенд, а не дублирует фронтенд: продублированные подписи расходятся с
константами при первой же правке, и пользователь видит одно, а на hh.ru
уходит другое. Регион здесь тоже есть — не как выбор, а чтобы интерфейс мог
честно показать, где ведётся поиск.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from job_monitor.criteria import (
    HH_AREA_ID,
    HH_EMPLOYMENT,
    HH_EXPERIENCE,
    HH_SCHEDULE,
)

router = APIRouter(prefix="/api/dictionaries", tags=["dictionaries"])


@router.get("")
async def dictionaries() -> dict[str, Any]:
    return {
        "area_id": HH_AREA_ID,
        "experience": HH_EXPERIENCE,
        "employment": HH_EMPLOYMENT,
        "schedule": HH_SCHEDULE,
        # Какие поля — множественный выбор. hh.ru принимает несколько типов
        # занятости и графиков, и одно значение сужает выдачу, отсекая
        # вакансии, помеченные сразу двумя; опыт — одно, как на самом hh.ru.
        "multi": ["employment", "schedule"],
    }
