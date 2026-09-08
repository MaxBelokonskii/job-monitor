@echo off
chcp 65001 > nul
title Job Monitor
set "BASE=%~dp0"
cd /d "%BASE%"
if not exist ".venv" python -m venv .venv
".venv\Scripts\python.exe" -m pip install -e ".[dev]" -q
".venv\Scripts\python.exe" -m pip install -r requirements.lock -q
start "" "http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m uvicorn api.main:app --host 127.0.0.1 --port 8000
pause
