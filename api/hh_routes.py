import asyncio
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException

from .config_routes import load_config
from job_monitor.db.connection import get_connection
from job_monitor.db.repositories import HhRepo
from job_monitor.presets import active_criteria, active_preset
from job_monitor.logging_setup import log_file
from job_monitor.workers.hh import LoginWindowNotOpen, login
from job_monitor.workers.manager import WorkerAlreadyRunning, WorkerNotRunning, manager

router = APIRouter(prefix="/api/hh", tags=["hh"])


def get_hh_log(lines: int = 100) -> str:
    """Хвост файла, в который пишет HH-сторона приложения.

    Путь вычисляется на каждый вызов, а не один раз на импорте — см.
    комментарий-близнец в api/tg_routes.py::get_system_log.
    """
    target = log_file("hh")
    if not target.exists():
        return ""
    with open(target, "r", encoding="utf-8") as f:
        all_lines = f.readlines()
    return "".join(all_lines[-lines:])


@router.get("/status")
async def hh_status() -> dict[str, Any]:
    repo = HhRepo(get_connection())
    cfg = load_config()
    return {
        "running": manager.status("hh").as_dict()["running"],
        "sent_today": repo.applied_on(date.today()),
        "found_today": repo.found_on(date.today()),
        "total_sent": repo.applied_total(),
        "max_per_day": cfg.get("hh_max_per_day", 20),
        "hh_autostart": cfg.get("hh_autostart", False),
    }


@router.post("/start")
async def hh_start() -> dict[str, str | int]:
    # См. tg_start: отказ со внятным текстом дешевле любой диагностики
    # постфактум.
    conn = get_connection()
    if not active_criteria(conn).professions:
        raise HTTPException(
            status_code=400,
            detail=f"в пресете «{active_preset(conn)['name']}» нет профессий — "
            "по ним строится поисковый запрос на hh.ru",
        )
    try:
        status = await manager.start("hh")
    except WorkerAlreadyRunning:
        raise HTTPException(status_code=400, detail="HH монитор уже запущен")
    # `epoch` — как в tg_start: номер запуска, чтобы фронтенд знал свежий
    # номер сразу и распознавал устаревший ответ на остановку.
    return {"status": "started", "epoch": status.epoch}


@router.post("/stop")
async def hh_stop() -> dict[str, str | int | None]:
    try:
        status = await manager.stop("hh")
    except WorkerNotRunning:
        raise HTTPException(status_code=400, detail="HH монитор не запущен")
    # Возвращаем НАСТОЯЩЕЕ состояние, а не безусловное "stopped": воркер
    # может не уложиться в бюджет остановки (apply_to_vacancy — это
    # WebDriverWait(15) плюс фиксированные паузы), и тогда manager.stop()
    # ставит error и продолжает отслеживать таску. Раньше UI в этом случае
    # показывал «монитор остановлен», пользователь жал «Старт», получал 400
    # «уже запущен» и жал «Стоп» ещё раз — то самое место, где второй стоп
    # раньше бросал живой поток Selenium без присмотра.
    # Форма ответа сохранена: успешная остановка по-прежнему даёт
    # {"status": "stopped"}, на который смотрит frontend/app.js.
    #
    # `epoch` — номер запуска, который останавливал именно этот вызов; см.
    # подробное объяснение в api/tg_routes.py::tg_stop и в
    # WorkerManager.stop().
    return {
        "status": status.state.value,
        "detail": status.last_error,
        "epoch": status.epoch,
    }


@router.post("/login/start")
async def hh_login_start() -> dict:
    return {"state": (await asyncio.to_thread(login.start)).value}


@router.post("/login/confirm")
async def hh_login_confirm() -> dict:
    """«Я вошёл, сохранить сессию».

    Кнопка «Закрыть окно входа» видна всегда и стоит рядом, поэтому
    последовательность «Закрыть» → «Я вошёл» достижима в два клика и
    приводила в `confirm()` без драйвера: необработанный RuntimeError и 500.
    Это ошибка последовательности вызовов, а не сбой сервера, — 400 с
    объяснением. Дизейбл кнопки в UI сюда не годится в одиночку: 500 отдаётся
    любому клиенту (curl, вкладка со старым состоянием). Сам дизейбл теперь
    есть — `login.state` приходит в каждом `GET /api/state`, — но он гасит
    лишь приглашение нажать, а не сам вызов.

    Ловится именно `LoginWindowNotOpen`, а не всякий RuntimeError: упавший
    посреди проверки Selenium — это настоящая поломка, и она должна остаться
    500, а не притвориться ошибкой пользователя.
    """
    try:
        state = await asyncio.to_thread(login.confirm)
    except LoginWindowNotOpen as error:
        raise HTTPException(
            status_code=400,
            detail="Окно входа не открыто — нажмите «Открыть вход в hh.ru»",
        ) from error
    return {"state": state.value}


@router.post("/login/cancel")
async def hh_login_cancel() -> dict:
    """Закрыть окно входа, не подтверждая его.

    Без этого роута единственный способ погасить открытый Chrome — успешный
    `confirm()`: пользователь, у которого вход не удался или который передумал,
    оставался с живым окном без единого контрола в UI. `quit()` блокирующий,
    поэтому — как и `login.start` выше — через `asyncio.to_thread`.
    """
    await asyncio.to_thread(login.close)
    return {"state": login.state.value}


@router.get("/logs")
async def hh_logs(lines: int = 100) -> dict[str, str]:
    return {"log": get_hh_log(lines)}
