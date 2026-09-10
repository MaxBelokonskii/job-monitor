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
from job_monitor.db.repositories import PresetsRepo
from job_monitor.settings import load_settings
from job_monitor.workers.manager import WorkerState, manager

router = APIRouter(prefix="/api/presets", tags=["presets"])


class PresetCreate(BaseModel):
    name: str
    copy_from: int | None = None


def _summary(row: dict, active_id: int | None) -> dict[str, Any]:
    criteria = row["criteria"]
    return {
        "id": row["id"],
        "name": row["name"],
        "position": row["position"],
        "is_active": row["id"] == active_id,
        "channels_count": len(criteria.get("channels") or []),
        "professions_count": len(criteria.get("professions") or []),
        "resume_id": criteria.get("resume_id"),
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
    return [_summary(row, active) for row in PresetsRepo(conn).list()]


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
    conn = get_connection()
    repo = PresetsRepo(conn)
    if repo.get(preset_id) is None:
        raise HTTPException(status_code=404, detail="пресет не найден")

    body = dict(patch)
    new_name = body.pop("name", None)
    if new_name is not None:
        stripped = str(new_name).strip()
        if not stripped:
            raise HTTPException(
                status_code=400, detail="имя пресета не может быть пустым"
            )
        clash = repo.get_by_name(stripped)
        if clash is not None and clash["id"] != preset_id:
            raise HTTPException(status_code=400, detail=f"имя «{stripped}» уже занято")
        repo.set_name(preset_id, stripped, datetime.now())

    position = body.pop("position", None)
    if position is not None:
        repo.set_position(preset_id, int(position), datetime.now())

    if body:
        try:
            presets_service.save_criteria(conn, preset_id, body)
        except ValidationError as error:
            raise HTTPException(
                status_code=422, detail=_validation_detail(error)
            ) from error
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
        status = await manager.stop(name)
        if status.state is not WorkerState.stopped:
            # Переключение наполовину хуже отказа: Selenium может стоять
            # посреди отклика, и смена резюме под ним даёт отклик не тем
            # документом (решение D9). Останавливаемся ДО смены активного
            # пресета, поэтому отказ не оставляет полусостояния.
            raise HTTPException(
                status_code=409,
                detail=f"воркер {name} не остановился: {status.last_error}",
            )
        stopped.append(name)

    presets_service.set_active(conn, preset_id)
    return {"activated": preset_id, "stopped": stopped}
