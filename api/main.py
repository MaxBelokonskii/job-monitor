from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
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
from job_monitor.workers.hh import run_worker as run_hh_worker
from job_monitor.workers.manager import manager
from job_monitor.workers.telegram import run_worker as run_tg_worker

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")


def register_workers() -> None:
    """Register worker factories with the module-level manager."""
    manager.register("tg", run_tg_worker)
    manager.register("hh", run_hh_worker)


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    await manager.stop_all()


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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
async def serve_ui():
    html_path = os.path.join(FRONTEND_DIR, "index.html")
    if not os.path.exists(html_path):
        return "<h1>index.html not found in frontend/</h1>"
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read().replace(TOKEN_PLACEHOLDER, APP_TOKEN)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="127.0.0.1", port=8000, reload=False)
