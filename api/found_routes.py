"""Очередь найденного: два списка и ручная смена статуса.

Списки раздельные (решение D14): у вакансии hh.ru есть название, компания и
зарплата, у поста в Telegram — канал и контакт для лички. Общая форма
записи вынудила бы половину полей держать пустыми.

**Ссылку строит бэкенд.** Для hh.ru она уже лежит в `url`; для Telegram
собирается здесь из канала и идентификатора сообщения. Фронтенду незачем
знать формат чужих URL, а гвард схемы в `el()` остаётся единственной точкой
проверки — и проверять ему проще одно поле, чем правило склейки.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from job_monitor import statuses
from job_monitor.db.connection import get_connection
from job_monitor.db.repositories import HhRepo, TgFoundRepo, TgRepo

router = APIRouter(prefix="/api/found", tags=["found"])

StatusFilter = Literal["all", "new", "decided", "applied"]

#: Что показывает каждый фильтр. `applied` — не подмножество `decided` по
#: смыслу, а самостоятельный вопрос «куда я уже написал»: «не подходит»
#: тоже решение, но не отправка.
STATUS_FILTERS: dict[str, frozenset[str] | None] = {
    "all": None,
    "new": frozenset({statuses.NEW}),
    "decided": statuses.DECIDED,
    "applied": statuses.APPLIED,
}


class StatusPatch(BaseModel):
    """Тело `PATCH`. `extra="forbid"`, чтобы опечатка в имени поля не
    оборачивалась молчаливым «сохранено» без единого изменения."""

    model_config = ConfigDict(extra="forbid")

    status: str


def _wanted(status: StatusFilter) -> frozenset[str] | None:
    return STATUS_FILTERS[status]


def check_transition(current: str, target: str) -> None:
    """Проверяет, что человек вправе поставить `target` вместо `current`.

    Общая на оба списка: правила не зависят от того, где лежит запись, а
    две копии одного правила расходятся при первой же правке.

    Правил ровно два, и оба про одно: **отправленный отклик — не решение,
    а запись о факте.** Письмо ушло работодателю, и переименованием этого
    не отменить. Поэтому из `APPLIED` нельзя выйти никуда, а войти в
    `AUTO_APPLIED` нельзя вообще — это слово робота.
    """
    if target == statuses.AUTO_APPLIED:
        raise HTTPException(
            status_code=400,
            detail="«отклик отправлен» ставит только робот: этот статус — "
            "источник счётчика отправленного за день, и рука человека "
            "сделала бы его неправдой",
        )
    if current in statuses.APPLIED and target != current:
        # Найдено ревью, и цена бреши считается в отправленных письмах.
        # `applied_on()` считает строки со статусом «отклик отправлен», и
        # по нему воркер сверяет суточный лимит. Пометив пять отправленных
        # откликов как «не подходит», человек опускал счётчик с 20 до 15 —
        # и воркер, спавший на достигнутом лимите, просыпался и досылал
        # ещё пять. Лимит существует, чтобы не забанили аккаунт.
        raise HTTPException(
            status_code=400,
            detail="отклик уже отправлен — сменить статус нельзя: это не "
            "решение, а запись о том, что письмо ушло работодателю, и "
            "по ней считается суточный лимит отправок",
        )
    if target in statuses.MANUAL:
        return
    if target == statuses.NEW:
        if current in statuses.REOPENABLE or current == statuses.NEW:
            return
        if current in statuses.APPLIED:
            raise HTTPException(
                status_code=400,
                detail="отклик уже отправлен — вернуть запись в очередь нельзя: "
                "«новая» означает «обработать», то есть робот отправил бы "
                "второй отклик тому же работодателю",
            )
        # Единственный оставшийся случай — «ошибка сценария». Он выглядит
        # как «робот не смог, пусть попробует снова», но кнопка
        # «Откликнуться» к тому моменту уже нажата на живом hh.ru: сценарий
        # ломается ПОСЛЕ клика (см. `_process_one` в workers/hh.py).
        raise HTTPException(
            status_code=400,
            detail="сценарий сломался уже после клика по «Откликнуться» — "
            "повторная обработка кликнула бы второй раз; поправьте сценарий "
            "и дождитесь следующей вакансии",
        )
    raise HTTPException(
        status_code=400,
        detail=f"статус {target!r} нельзя поставить вручную; допустимы: "
        f"{', '.join(sorted(statuses.MANUAL | {statuses.NEW}))}",
    )


def _tg_view(row: dict) -> dict[str, Any]:
    """Строка очереди наружу.

    Колонка `username` (единственное число) сюда не попадает намеренно:
    она осталась от миграции 003 и всегда пуста, а отданное наружу поле
    рано или поздно становится полем, на которое опираются.
    """
    return {
        "id": row["id"],
        "channel": row["channel"],
        "message_id": row["message_id"],
        "found_at": row["found_at"],
        "preview": row["preview"],
        "matched_keyword": row["matched_keyword"],
        "usernames": row["usernames"],
        "status": row["status"],
        "status_at": row["status_at"],
        "link": f"https://t.me/{row['channel'].lstrip('@')}/{row['message_id']}",
    }


@router.get("/tg")
async def list_tg_found(
    status: StatusFilter = "all",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    rows = TgFoundRepo(get_connection()).list(
        only=_wanted(status), limit=limit, offset=offset
    )
    return [_tg_view(row) for row in rows]


@router.patch("/tg/{found_id}")
async def patch_tg_found(found_id: int, patch: StatusPatch) -> dict[str, Any]:
    conn = get_connection()
    repo = TgFoundRepo(conn)
    row = repo.get(found_id)
    if row is None:
        raise HTTPException(status_code=404, detail="запись не найдена")

    check_transition(row["status"], patch.status)
    now = datetime.now()

    if patch.status == statuses.MANUAL_APPLIED:
        # Человек написал сам — воркер больше никогда не должен писать этим
        # людям. `ensure_contact` для этого и написан: он регистрирует
        # контакт БЕЗ строки в `tg_sends`, поэтому дневной счётчик
        # («отправлено сегодня» считается по `tg_sends`) не трогается —
        # решение D16 выполняется формой данных.
        tg_repo = TgRepo(conn)
        for username in row["usernames"]:
            tg_repo.ensure_contact(username, now)

    repo.set_status(found_id, patch.status, now)
    return {"status": "saved", "found": _tg_view(repo.get(found_id))}


def _hh_view(row: dict) -> dict[str, Any]:
    """Вакансия наружу. Ключи те же, что в таблице: ссылка у hh.ru уже
    лежит в `url`, собирать нечего."""
    return {
        "vacancy_id": row["vacancy_id"],
        "title": row["title"],
        "company": row["company"],
        "salary": row["salary"],
        "city": row["city"],
        "url": row["url"],
        "found_at": row["found_at"],
        "applied_at": row["applied_at"],
        "status": row["status"],
        "status_source": row["status_source"],
        "error": row["error"],
    }


@router.get("/hh")
async def list_hh_found(
    status: StatusFilter = "all",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    rows = HhRepo(get_connection()).list_found(
        only=_wanted(status), limit=limit, offset=offset
    )
    return [_hh_view(row) for row in rows]


@router.patch("/hh/{vacancy_id}")
async def patch_hh_found(vacancy_id: str, patch: StatusPatch) -> dict[str, Any]:
    """Ручной статус вакансии hh.ru.

    Побочных эффектов, в отличие от Telegram, нет: дедупликация здесь идёт
    по самому статусу — он попадает в `DECIDED`, и воркер вакансию больше
    не тронет.
    """
    repo = HhRepo(get_connection())
    row = repo.get(vacancy_id)
    if row is None:
        raise HTTPException(status_code=404, detail="вакансия не найдена")

    check_transition(row["status"], patch.status)
    # Возврат в очередь подписывается роботом намеренно: запись снова
    # принадлежит ему, и следующая надпись на карточке должна говорить о
    # роботе, а не о человеке, который её туда вернул.
    source = (
        statuses.SOURCE_ROBOT if patch.status == statuses.NEW else statuses.SOURCE_HUMAN
    )
    repo.set_status(vacancy_id, patch.status, source, datetime.now())
    return {"status": "saved", "found": _hh_view(repo.get(vacancy_id))}
