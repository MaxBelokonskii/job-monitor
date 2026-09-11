"""CRUD пресетов и переключение между ними."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from job_monitor import presets as presets_service
from job_monitor.criteria import SearchCriteria
from job_monitor.db.connection import get_connection
from job_monitor.db.repositories import PresetsRepo, ResumesRepo
from job_monitor.settings import load_settings
from job_monitor.workers.manager import WorkerNotRunning, WorkerState, manager

router = APIRouter(prefix="/api/presets", tags=["presets"])


class PresetCreate(BaseModel):
    # Число сюда не пройдёт: pydantic 2, в отличие от первой версии, не
    # приводит `int` к `str` — 422 отдаётся без нашего участия. Проверено
    # мутацией: `StrictStr` здесь не добавлял ничего и убран как мёртвый
    # вес. Приведение, из-за которого пресет получал имя "123", жило не
    # тут, а в `patch_preset` ниже, где тело — сырой словарь.
    name: str
    copy_from: int | None = None


def _summary(
    row: dict, active_id: int | None, resume_names: dict[int, str]
) -> dict[str, Any]:
    """Сводка пресета для списка.

    `resume_name` рядом с `resume_id`, потому что показать надо имя, а
    списки пресетов и резюме грузятся независимо: без имени чип пресета
    до второго ответа показывал бы число или пустоту. `None`, если
    вложения нет или ссылка указывает на удалённое резюме — связь живёт
    внутри JSON-документа критериев, а не внешним ключом, так что
    рассогласование возможно, и список из-за него падать не должен.
    """
    criteria = row["criteria"]
    resume_id = criteria.get("resume_id")
    return {
        "id": row["id"],
        "name": row["name"],
        "position": row["position"],
        "is_active": row["id"] == active_id,
        "channels_count": len(criteria.get("channels") or []),
        "professions_count": len(criteria.get("professions") or []),
        "resume_id": resume_id,
        "resume_name": resume_names.get(resume_id) if resume_id is not None else None,
    }


def _validation_detail(error: ValidationError) -> list[dict[str, Any]]:
    """Ошибки валидации в виде, который переживает сериализацию в JSON.

    `include_context=False` здесь обязателен, а не косметика: пользовательский
    валидатор бросает `ValueError`, и pydantic кладёт САМ объект исключения в
    `ctx["error"]`. FastAPI пытается сериализовать его и падает с
    `TypeError: Object of type ValueError is not JSON serializable` — отказ
    валидации превращается в 500 вместо 422, и пользователь вместо
    «неизвестный код опыта» видит «внутренняя ошибка сервера».
    """
    return error.errors(include_url=False, include_context=False)


def _active_id(conn: sqlite3.Connection) -> int | None:
    return load_settings(conn).active_preset_id


@router.get("")
async def list_presets() -> list[dict[str, Any]]:
    conn = get_connection()
    presets_service.ensure_default(conn, datetime.now())
    active = _active_id(conn)
    names = {row["id"]: row["original_name"] for row in ResumesRepo(conn).list()}
    return [_summary(row, active, names) for row in PresetsRepo(conn).list()]


@router.post("")
async def create_preset(body: PresetCreate) -> dict[str, Any]:
    conn = get_connection()
    repo = PresetsRepo(conn)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="имя пресета не может быть пустым")
    if repo.get_by_name(name) is not None:
        raise HTTPException(status_code=400, detail=f"имя «{name}» уже занято")

    criteria = SearchCriteria().model_dump()
    if body.copy_from is not None:
        source = repo.get(body.copy_from)
        if source is None:
            raise HTTPException(status_code=404, detail="пресет-источник не найден")
        criteria = dict(source["criteria"])

    preset_id = repo.create(name, criteria, datetime.now())
    return {"id": preset_id, "name": name}


@router.get("/{preset_id}")
async def get_preset(preset_id: int) -> dict[str, Any]:
    row = PresetsRepo(get_connection()).get(preset_id)
    if row is None:
        raise HTTPException(status_code=404, detail="пресет не найден")
    return {"id": row["id"], "name": row["name"], "criteria": row["criteria"]}


@router.patch("/{preset_id}")
async def patch_preset(preset_id: int, patch: dict) -> dict[str, str]:
    """Применяется целиком или никак.

    Раньше имя и позиция писались сразу, а критерии валидировались после:
    запрос с новым именем и негодным критерием возвращал 422, оставив
    переименование сделанным. Клиент видел отказ и не знал, что половина
    прошла, — и следующий его запрос шёл к пресету, которого «нет».

    Сначала проверяется ВСЁ (имя, позиция, критерии), и только потом идёт
    первая запись. Валидация критериев — `SearchCriteria` над слиянием
    патча с сохранённым, то есть ровно то, что сделает `save_criteria`,
    но без записи. Дублирование дешевле полусохранения: слияние — чистая
    функция, а откатить уже записанное имя нечем.
    """
    conn = get_connection()
    repo = PresetsRepo(conn)
    stored = repo.get(preset_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="пресет не найден")

    body = dict(patch)
    new_name = body.pop("name", None)
    position = body.pop("position", None)

    stripped: str | None = None
    if new_name is not None:
        if not isinstance(new_name, str):
            # См. `PresetCreate.name`: приводить число к строке молча —
            # значит записать в базу то, чего человек не вводил.
            raise HTTPException(
                status_code=400, detail="имя пресета должно быть строкой"
            )
        stripped = new_name.strip()
        if not stripped:
            raise HTTPException(
                status_code=400, detail="имя пресета не может быть пустым"
            )
        clash = repo.get_by_name(stripped)
        if clash is not None and clash["id"] != preset_id:
            raise HTTPException(status_code=400, detail=f"имя «{stripped}» уже занято")

    if position is not None:
        try:
            position = int(position)
        except (TypeError, ValueError) as error:
            raise HTTPException(
                status_code=400, detail="позиция должна быть числом"
            ) from error

    if body:
        merged = presets_service.parse_criteria(stored["criteria"]).model_dump()
        merged.update(body)
        try:
            SearchCriteria(**merged)
        except ValidationError as error:
            raise HTTPException(
                status_code=422, detail=_validation_detail(error)
            ) from error

    now = datetime.now()
    if stripped is not None:
        repo.set_name(preset_id, stripped, now)
    if position is not None:
        repo.set_position(preset_id, position, now)
    if body:
        presets_service.save_criteria(conn, preset_id, body)
    return {"status": "saved"}


@router.delete("/{preset_id}")
async def delete_preset(preset_id: int) -> dict[str, str]:
    conn = get_connection()
    repo = PresetsRepo(conn)
    if repo.get(preset_id) is None:
        raise HTTPException(status_code=404, detail="пресет не найден")
    # Оба запрета — чтобы приложение не оказалось молча нерабочим: без
    # активного пресета воркерам нечего читать, а без единого пресета
    # интерфейсу нечего показывать и не из чего восстановиться.
    if repo.count() <= 1:
        raise HTTPException(
            status_code=400, detail="это единственный пресет, удалить его нельзя"
        )
    if preset_id == _active_id(conn):
        raise HTTPException(
            status_code=400,
            detail="нельзя удалить активный пресет — сначала переключитесь на другой",
        )
    repo.delete(preset_id)
    return {"status": "deleted"}


@router.post("/{preset_id}/activate")
async def activate_preset(preset_id: int) -> dict[str, Any]:
    conn = get_connection()
    repo = PresetsRepo(conn)
    if repo.get(preset_id) is None:
        raise HTTPException(status_code=404, detail="пресет не найден")

    stopped: list[str] = []
    for name in manager.running():
        try:
            status = await manager.stop(name)
        except WorkerNotRunning:
            # Воркер успел упасть сам между `running()` и `stop()`. Это не
            # ошибка: останавливать нечего, а именно остановки мы и
            # добивались. В `stopped` он не попадает — мы его не
            # останавливали.
            continue
        if status.state is not WorkerState.stopped:
            # Переключение наполовину хуже отказа: Selenium может стоять
            # посреди отклика, и смена резюме под ним даёт отклик не тем
            # документом (решение D9). Останавливаемся ДО смены активного
            # пресета, поэтому отказ не оставляет полусостояния.
            #
            # В тексте отказа названо и то, что УЖЕ остановлено: иначе
            # человек видит «переключить не удалось» и не понимает, почему
            # один воркер стоит, а другой работает — объяснения этому нет
            # больше нигде.
            detail = f"воркер {name} не остановился: {status.last_error}"
            if stopped:
                detail += f". Уже остановлены: {', '.join(stopped)}"
            raise HTTPException(status_code=409, detail=detail)
        stopped.append(name)

    presets_service.set_active(conn, preset_id)
    return {"activated": preset_id, "stopped": stopped}
