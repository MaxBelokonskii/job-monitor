from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from .config_routes import router as config_router
from .tg_routes import router as tg_router, is_running as tg_is_running
from .hh_routes import router as hh_router, hh_is_running
from .auth_routes import router as auth_router

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

app = FastAPI(title="QA Monitor API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Подключаем роутеры
app.include_router(config_router)
app.include_router(tg_router)
app.include_router(hh_router)
app.include_router(auth_router)

# Статика (JS, CSS)
if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    html_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>index.html not found in frontend/</h1>"

@app.on_event("startup")
async def on_startup():
    """Автозапуск скриптов если включено в настройках"""
    from .config_routes import load_config
    from .tg_routes import tg_start
    from .hh_routes import hh_start
    cfg = load_config()
    if cfg.get("tg_autostart") and not tg_is_running():
        try:
            await tg_start()
            print("[AUTOSTART] TG монитор запущен")
        except Exception as e:
            print(f"[AUTOSTART] Ошибка запуска TG: {e}")
    if cfg.get("hh_autostart") and not hh_is_running():
        try:
            await hh_start()
            print("[AUTOSTART] HH монитор запущен")
        except Exception as e:
            print(f"[AUTOSTART] Ошибка запуска HH: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="127.0.0.1", port=8000, reload=False)
