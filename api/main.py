import asyncio
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
import os

from .config_routes import router as config_router
from .tg_routes import router as tg_router
from .hh_routes import router as hh_router
from .auth_routes import router as auth_router
from .routes_state import router as state_router
from job_monitor.db.connection import get_connection
from job_monitor.security import (
    ALLOWED_HOSTS,
    APP_TOKEN,
    TOKEN_PLACEHOLDER,
    app_token_middleware,
    security_headers_middleware,
)
from job_monitor.logging_setup import configure_logging, worker_logger
from job_monitor.settings import load_settings
from job_monitor.workers.hh import login, run_worker as run_hh_worker
from job_monitor.workers.manager import manager
from job_monitor.workers.telegram import run_worker as run_tg_worker

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")


def register_workers() -> None:
    """Register worker factories with the module-level manager."""
    manager.register("tg", run_tg_worker)
    manager.register("hh", run_hh_worker)


# Бюджет на закрытие окна входа hh.ru при остановке приложения — тот же
# аргумент, что и у `manager.stop(timeout=10.0)`.
LOGIN_CLOSE_TIMEOUT = 10.0


async def close_login_window(timeout: float | None = None) -> None:
    """Погасить окно входа hh.ru, но не дольше `timeout` секунд.

    `login.close()` вызывает `driver.quit()`, который ходит по HTTP в
    chromedriver и может не ответить НИКОГДА — ровно тот отказ, ради которого
    `HhLogin.close()` вообще глотает исключения из `quit()`. Но зависание не
    исключение: `except` от него не спасает. Без бюджета времени Ctrl+C
    оставлял uvicorn висеть в `to_thread(login.close)` навсегда.

    Поток нельзя отменить, поэтому одного бюджета мало: `asyncio.to_thread`
    исполняется в общем ThreadPoolExecutor, чьи потоки не daemon, и
    интерпретатор джойнит их на выходе — процесс всё равно не завершился бы,
    даже если бы `lifespan` перестал ждать. Здесь поэтому свой daemon-поток:
    бюджет ограничивает ожидание, а daemon гарантирует, что застрявший
    `quit()` не удержит процесс.
    """
    budget = LOGIN_CLOSE_TIMEOUT if timeout is None else timeout
    loop = asyncio.get_running_loop()
    finished = asyncio.Event()

    def close_in_thread() -> None:
        try:
            login.close()
        finally:
            try:
                loop.call_soon_threadsafe(finished.set)
            except RuntimeError:
                # Бюджет уже истёк, приложение ушло дальше и закрыло цикл:
                # будить некого. Это нормальный исход зависшего `quit()`,
                # который всё-таки вернулся, а не ошибка потока.
                pass

    threading.Thread(target=close_in_thread, name="hh-login-close", daemon=True).start()
    try:
        await asyncio.wait_for(finished.wait(), budget)
    except (asyncio.TimeoutError, TimeoutError):
        worker_logger("hh").error(
            "окно входа hh.ru не закрылось за %ss — chromedriver не отвечает;"
            " приложение завершается, процесс Chrome может остаться живым",
            budget,
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Логирование настраивается здесь, а не на импорте: импорт модуля не
    # должен создавать каталог данных и файлы (см. job_monitor/logging_setup.py).
    configure_logging()
    register_workers()
    settings = load_settings(get_connection())
    # Each autostart is isolated in its own try/except, matching the
    # resilience of the on_event handler this replaces: an exception
    # raised inside a lifespan startup context fails the whole app boot
    # (uvicorn logs "Application startup failed" and exits), so one
    # worker that is unregistered or fails to start must not be allowed
    # to take the other worker — or the entire API — down with it.
    if settings.tg_autostart:
        try:
            await manager.start("tg")
        except Exception:
            # В логгер именно этого воркера: несостоявшийся автозапуск —
            # причина, по которой пользователь видит «остановлен», и она
            # должна лежать на его вкладке логов, а не в общем потоке.
            worker_logger("tg").exception("автозапуск TG воркера не удался")
    if settings.hh_autostart:
        try:
            await manager.start("hh")
        except Exception:
            worker_logger("hh").exception("автозапуск HH воркера не удался")
    yield
    try:
        await manager.stop_all()
    finally:
        # Окно входа в hh.ru не принадлежит менеджеру воркеров: его открывает
        # POST /api/hh/login/start, а гасил до сих пор только успешный
        # confirm(). Незавершённый вход поэтому переживал остановку
        # приложения живым процессом Chrome — тот самый брошенный браузер,
        # ради которого существует супервизор. `quit()` блокирующий, так что
        # здесь он идёт в отдельном потоке — и с бюджетом времени, см.
        # close_login_window(). В `finally`, чтобы падение stop_all() не
        # съело закрытие окна.
        await close_login_window()


app = FastAPI(
    title="QA Monitor API",
    version="2.1.0",
    # /api/* is token-gated (see app_token_middleware below), but FastAPI's
    # own /docs, /redoc, /openapi.json and /docs/oauth2-redirect are not
    # under /api/ and would otherwise return 200 with no token at all —
    # unnecessary surface on a tool whose premise is a token-gated API.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

# Никакого CORSMiddleware здесь нет, и это осознанно. Страница отдаётся с
# того же адреса, куда стучится относительными путями (`const API = '/api'`
# в frontend/app.js), поэтому все её запросы однодоменные и разрешать
# нечего. Allow-лист источников был не разрешением, а поверхностью для
# ошибки: мутационный аудит показал, что расширение до `allow_origins=["*"]`
# проходило при полностью зелёном наборе тестов, а цена этого — `GET /`
# отдаёт APP_TOKEN внутри HTML первому же открытому в браузере сайту.
# Без middleware `Access-Control-Allow-Origin` не отдаётся НИКОГДА, и
# браузер не даёт чужому источнику прочитать ответ вообще — строго строже
# любого allow-листа. Инвариант закреплён в
# tests/test_no_cross_origin_permission.py.

app.middleware("http")(app_token_middleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
app.middleware("http")(security_headers_middleware)

# Подключаем роутеры
app.include_router(config_router)
app.include_router(tg_router)
app.include_router(hh_router)
app.include_router(auth_router)
app.include_router(state_router)

# Статика (JS, CSS)
if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

@app.get("/", response_class=HTMLResponse)
async def serve_ui() -> str:
    html_path = os.path.join(FRONTEND_DIR, "index.html")
    if not os.path.exists(html_path):
        return "<h1>index.html not found in frontend/</h1>"
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read().replace(TOKEN_PLACEHOLDER, APP_TOKEN)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="127.0.0.1", port=8000, reload=False)
