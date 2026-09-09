"""Критерии поиска: содержимое пресета плюс константы hh.ru.

Регион, опыт, занятость и график — константы, а не настройки: поиск ведётся
только по региону 113 (решение D8), а остальные три — закрытые наборы кодов
самого hh.ru, из которых интерфейс строит переключатели. Следствие, ради
которого это и сделано так: официальное API hh.ru не нужно даже для
справочников.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

HH_AREA_ID = 113

HH_EXPERIENCE: dict[str, str] = {
    "noExperience": "Нет опыта",
    "between1And3": "От 1 до 3 лет",
    "between3And6": "От 3 до 6 лет",
    "moreThan6": "Более 6 лет",
}

HH_EMPLOYMENT: dict[str, str] = {
    "full": "Полная занятость",
    "part": "Частичная занятость",
    "project": "Проектная работа",
    "probation": "Стажировка",
    "volunteer": "Волонтёрство",
}

HH_SCHEDULE: dict[str, str] = {
    "remote": "Удалённо",
    "fullDay": "Полный день",
    "flexible": "Гибкий график",
    "shift": "Сменный график",
}


class SearchCriteria(BaseModel):
    """Что человек ищет.

    Лимиты и задержки здесь НЕ живут (решение D7): банят аккаунт, а не
    пресет, поэтому суточный бюджет один на приложение — иначе три пресета
    по 25 отправок дали бы 75 сообщений с одного аккаунта.

    Все списки пусты по умолчанию. Это не забывчивость: унаследованный код
    приходил с поиском предыдущего автора внутри, и «пример из коробки» —
    ровно тот механизм, которым чужой поиск возвращается назад.
    """

    model_config = ConfigDict(extra="forbid")

    channels: list[str] = Field(default_factory=list)
    professions: list[str] = Field(default_factory=list)
    tg_keywords: list[str] = Field(default_factory=list)
    tg_exclude: list[str] = Field(default_factory=list)
    hh_exclude: list[str] = Field(default_factory=list)
    hh_salary_from: int = Field(default=0, ge=0)
    hh_experience: str = "noExperience"
    hh_employment: list[str] = Field(default_factory=list)
    hh_schedule: list[str] = Field(default_factory=list)
    hh_search_period: int = Field(default=1, ge=1, le=30)
    template: str = ""
    hh_cover_letter: str = ""
    hh_resume_id: str = ""
    resume_id: int | None = None

    @field_validator("hh_experience")
    @classmethod
    def _known_experience(cls, value: str) -> str:
        if value not in HH_EXPERIENCE:
            raise ValueError(
                f"неизвестный код опыта {value!r}; допустимы: {sorted(HH_EXPERIENCE)}"
            )
        return value

    @field_validator("hh_employment")
    @classmethod
    def _known_employment(cls, value: list[str]) -> list[str]:
        unknown = [item for item in value if item not in HH_EMPLOYMENT]
        if unknown:
            raise ValueError(
                f"неизвестные коды занятости {unknown}; "
                f"допустимы: {sorted(HH_EMPLOYMENT)}"
            )
        return value

    @field_validator("hh_schedule")
    @classmethod
    def _known_schedule(cls, value: list[str]) -> list[str]:
        unknown = [item for item in value if item not in HH_SCHEDULE]
        if unknown:
            raise ValueError(
                f"неизвестные коды графика {unknown}; допустимы: {sorted(HH_SCHEDULE)}"
            )
        return value
