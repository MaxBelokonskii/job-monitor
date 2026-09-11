"""Активный пресет: единственный источник критериев для воркеров."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from job_monitor.criteria import SearchCriteria
from job_monitor.db.repositories import PresetsRepo, SettingsRepo

logger = logging.getLogger(__name__)

DEFAULT_PRESET_NAME = "Мой поиск"
LEGACY_PRESET_NAME = "Импортированные настройки"

# Поля прежней единой модели, которые переезжают в критерии. Слева — как
# лежало в базе, справа — как называется в `SearchCriteria`.
LEGACY_CRITERIA_FIELDS: dict[str, str] = {
    "channels": "channels",
    "keywords": "tg_keywords",
    "exclude": "tg_exclude",
    "template": "template",
    "hh_keywords": "professions",
    "hh_exclude": "hh_exclude",
    "hh_salary_from": "hh_salary_from",
    "hh_cover_letter": "hh_cover_letter",
    "hh_experience": "hh_experience",
    "hh_employment": "hh_employment",
    "hh_schedule": "hh_schedule",
    "hh_search_period": "hh_search_period",
    "hh_resume_id": "hh_resume_id",
}

# Уезжают из settings, но в критерии напрямую не переносятся: регион стал
# константой (D8), а `file_path` — ссылкой на библиотеку резюме (L14/D10).
# Сам файл при этом не теряется: `_adopt_legacy_resume` забирает его в
# библиотеку и подставляет `resume_id`.
LEGACY_DROPPED_FIELDS = ("hh_area_ids", "file_path")


def ensure_default(conn: sqlite3.Connection, now: datetime) -> int:
    """Гарантирует, что есть хотя бы один пресет, и возвращает активный.

    Пресет по умолчанию — пустой. Ни одного канала и ни одного ключевого
    слова: чужой поиск не должен появляться из ниоткуда даже как «пример».
    """
    repo = PresetsRepo(conn)
    settings_repo = SettingsRepo(conn)
    stored = settings_repo.load()
    active = stored.get("active_preset_id")
    if active is not None and repo.get(int(active)) is not None:
        return int(active)

    existing = repo.list()
    preset_id = (
        existing[0]["id"]
        if existing
        else repo.create(DEFAULT_PRESET_NAME, SearchCriteria().model_dump(), now)
    )
    set_active(conn, preset_id)
    return preset_id


def set_active(conn: sqlite3.Connection, preset_id: int) -> None:
    SettingsRepo(conn).update(
        lambda current: {**current, "active_preset_id": preset_id}
    )


def active_preset(conn: sqlite3.Connection) -> dict:
    preset_id = ensure_default(conn, datetime.now())
    preset = PresetsRepo(conn).get(preset_id)
    if preset is None:  # pragma: no cover — ensure_default только что его создал
        raise RuntimeError(f"активный пресет {preset_id} исчез между чтениями")
    return preset


def active_criteria(conn: sqlite3.Connection) -> SearchCriteria:
    return _tolerant(active_preset(conn)["criteria"])


def save_criteria(
    conn: sqlite3.Connection, preset_id: int, patch: dict
) -> SearchCriteria:
    """Слияние патча с сохранённым и валидация ЦЕЛОГО документа.

    Терпимость на чтении и строгость на записи — то же правило, что у
    настроек: сохранённая строка может пережить схему, но новый лишний ключ
    в базу не попадает. Исключение из `mutate` выходит наружу через
    `update_criteria`, то есть транзакция откатывается и в базе не остаётся
    ни целого патча, ни его половины.
    """
    def mutate(current: dict) -> dict:
        merged = _tolerant(current).model_dump()
        merged.update(patch)
        return SearchCriteria(**merged).model_dump()

    return SearchCriteria(**PresetsRepo(conn).update_criteria(preset_id, mutate))


def parse_criteria(raw: dict) -> SearchCriteria:
    """Разбор сохранённых критериев, терпимый к полям, не прошедшим проверку.

    Публичное имя для `_tolerant`: сухой прогон валидации в
    `api/presets_routes.py` — законный внешний потребитель, и тянуть туда
    подчёркнутое имя через границу модуля не стоит.
    """
    return _tolerant(raw)


def import_legacy_criteria(conn: sqlite3.Connection, now: datetime) -> int | None:
    """Переносит критерии из единой строки настроек в первый пресет.

    Возвращает идентификатор созданного пресета или `None`, если переносить
    нечего (перенос уже был или старых полей в базе нет). Идемпотентно.
    """
    repo = PresetsRepo(conn)
    settings_repo = SettingsRepo(conn)
    stored = settings_repo.load()
    present = [key for key in LEGACY_CRITERIA_FIELDS if key in stored]
    leftovers = [key for key in LEGACY_DROPPED_FIELDS if key in stored]
    if not present and not leftovers:
        return None
    if repo.count() > 0:
        # Пресеты уже есть — значит перенос состоялся; вычищаем остатки.
        _strip_legacy(settings_repo)
        return None

    raw = {LEGACY_CRITERIA_FIELDS[key]: stored[key] for key in present}
    resume_id = _adopt_legacy_resume(conn, stored.get("file_path"), now)
    if resume_id is not None:
        raw["resume_id"] = resume_id
    criteria = _tolerant(raw)
    preset_id = repo.create(LEGACY_PRESET_NAME, criteria.model_dump(), now)
    _strip_legacy(settings_repo)
    set_active(conn, preset_id)
    return preset_id


def _adopt_legacy_resume(
    conn: sqlite3.Connection, file_path: str | None, now: datetime
) -> int | None:
    """Забирает резюме из старого `file_path` в библиотеку.

    Возвращает идентификатор записи или `None`, если брать нечего. В
    `file_path` лежит путь к резюме, которым человек уже откликался, и
    просто отбросить его значило бы: библиотека пуста, пресет без
    вложения, ни одного сообщения об этом — а узнал бы он по молча
    уходящим откликам без резюме.

    Ни один отказ не срывает перенос остального. Путь мог протухнуть
    (файл переименовали, диск переставили), а старая версия не проверяла
    расширение вовсе, так что там может лежать хоть архив. Контакты и
    история вакансий важнее вложения — это тот же урок, что уже усвоен в
    `_tolerant`: один негодный ключ не должен уносить весь перенос.
    """
    if not file_path:
        return None

    from job_monitor import resume_store
    from job_monitor.db.repositories import ResumesRepo

    source = Path(file_path)
    try:
        data = source.read_bytes()
    except OSError as error:
        logger.warning(
            "резюме из старых настроек не прочитать (%s): перенос продолжается "
            "без вложения, загрузите файл через интерфейс", error,
        )
        return None

    try:
        stored_name, size = resume_store.store(source.name, data)
    except resume_store.ResumeRejected as error:
        logger.warning(
            "резюме из старых настроек не принято библиотекой (%s): перенос "
            "продолжается без вложения", error,
        )
        return None

    return ResumesRepo(conn).add(source.name, stored_name, size, now)


def _strip_legacy(settings_repo: SettingsRepo) -> None:
    gone = set(LEGACY_CRITERIA_FIELDS) | set(LEGACY_DROPPED_FIELDS)
    settings_repo.update(
        lambda current: {k: v for k, v in current.items() if k not in gone}
    )


def _tolerant(raw: dict) -> SearchCriteria:
    """Строит критерии, отбрасывая то, что новая модель не принимает.

    У прежней версии не было границ на числа, поэтому в базе может лежать
    `hh_search_period: 999`. Ронять из-за одного поля весь перенос нельзя —
    это ровно тот дефект, который уже чинили в `migrate-legacy`: первый
    невалидный ключ уносил и контакты, и вакансии.

    Рекурсия завершается: каждый проход либо возвращает модель, либо
    выбрасывает хотя бы один ключ из конечного словаря; когда выбрасывать
    нечего (`bad` пуст), исключение пробрасывается наружу.
    """
    try:
        return SearchCriteria(**raw)
    except ValidationError as error:
        bad = {
            item["loc"][0]
            for item in error.errors()
            if item.get("loc") and isinstance(item["loc"][0], str)
        }
        bad &= set(raw)
        if not bad:
            raise
        logger.warning(
            "критерии: отброшены поля, не прошедшие проверку: %s", sorted(bad)
        )
        return _tolerant({k: v for k, v in raw.items() if k not in bad})
