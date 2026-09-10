"""Колонка, которую пишут и никто не читает, — это молчаливый долг.

Финальное ревью нашло два таких набора.

**`hh_applications.city` и `.error`** доходили до API (`HhRepo.recent()` —
`SELECT *`, `GET /api/hh/vacancies` отдаёт словарь целиком), но фронтенд их
не рисовал: карточка вакансии показывала только бейдж статуса. Для `error`
это была прямая неправда — комментарий в `job_monitor/workers/hh.py` обещал,
что «причина видна в статусе вакансии на дашборде», хотя видно было слово
«ошибка сценария» без указания шага. Оба теперь рисуются, и этот файл держит
свойство: каждая колонка `hh_applications` либо попадает в карточку, либо
названа здесь с объяснением, почему не попадает.

**`tg_contacts.first_sent_at`, `last_sent_at`, `send_count`,
`source_channel`** оставлены как есть — сознательно, а не по недосмотру.
Удаление колонки в SQLite означает пересоздание таблицы с настоящей историей
контактов пользователя (см. докстринг `job_monitor/db/migrations.py`), а
чтение потребовало бы придумать экран, которого в продукте нет. Долг
зафиксирован в докстринге `TgRepo.record_send`; теста на него нет намеренно —
проверять было бы нечего, кроме текста комментария.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from job_monitor.db.repositories import HhRepo

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_JS = REPO_ROOT / "frontend/app.js"

# Колонки, которых в карточке нет и не должно быть, с причиной.
NOT_ON_THE_CARD = {
    "vacancy_id": "внутренний ключ дедупликации, пользователю нечего с ним делать",
    "found_at": "служит счётчику «найдено сегодня», в карточке дублировал бы статус",
    "applied_at": "то же для «откликов сегодня»; момент отклика виден по самому статусу",
}


def _vacancy_card_source() -> str:
    """Тело `hhVacancyCard()` — единственного места, которое строит
    информационную часть карточки вакансии.

    Раньше здесь стояла `loadHHVacancies()`. Карточка переехала в
    собственную функцию, потому что её показывают два экрана — очередь
    «Найдено» и «Обзор», — а кнопки решения нужны только очереди. Набор
    проверок при этом не изменился: каждая колонка `hh_applications`
    по-прежнему обязана либо попасть в карточку, либо быть названной в
    `NOT_ON_THE_CARD`. Разделение к тому же и сохраняет проверку на
    вакуумность: `vacancy_id` уезжает в `data-arg` кнопок, то есть в
    строку очереди, а не в карточку.
    """
    source = APP_JS.read_text(encoding="utf-8")
    match = re.search(r"function hhVacancyCard\(.*?\n\}", source, re.DOTALL)
    assert match, "hhVacancyCard() не найдена в app.js"
    return match.group(0)


@pytest.mark.parametrize(
    "column", [name for name in HhRepo.FIELDS if name not in NOT_ON_THE_CARD]
)
def test_every_stored_vacancy_field_reaches_the_card(column: str) -> None:
    assert re.search(rf"\bv\.{re.escape(column)}\b", _vacancy_card_source()), (
        f"hh_applications.{column} пишется воркером и отдаётся GET /api/hh/vacancies, "
        "но карточка вакансии его не читает. Либо покажите его, либо внесите в "
        "NOT_ON_THE_CARD с объяснением."
    )


def test_the_exemption_list_matches_the_schema() -> None:
    """Список исключений не должен пережить переименование колонки: иначе
    исключение молча начнёт покрывать не то, что задумано."""
    unknown = sorted(set(NOT_ON_THE_CARD) - set(HhRepo.FIELDS))
    assert not unknown, f"NOT_ON_THE_CARD называет несуществующие колонки: {unknown}"


def test_the_card_check_is_not_vacuous() -> None:
    """Проверка обязана падать на колонке, которой в карточке нет."""
    body = _vacancy_card_source()
    assert len(HhRepo.FIELDS) - len(NOT_ON_THE_CARD) >= 5, (
        "почти все колонки объявлены исключениями — проверка выродилась"
    )
    assert not re.search(r"\bv\.vacancy_id\b", body), (
        "vacancy_id вдруг оказался в карточке — обновите NOT_ON_THE_CARD, "
        "иначе тест перестанет что-либо доказывать"
    )
