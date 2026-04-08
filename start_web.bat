@echo off
chcp 65001 > nul
title QA Monitor v2.0

set "BASE=%~dp0"
set "PYTHON=C:\Users\vpak9\AppData\Local\Python\bin\python.exe"

echo Installing dependencies...
"%PYTHON%" -m pip install fastapi uvicorn python-dotenv telethon selenium webdriver-manager -q

echo Starting QA Monitor v2.0...
timeout /t 1 > nul
start "" "http://127.0.0.1:8000"
cd /d "%BASE%"
"%PYTHON%" -m uvicorn api.main:app --host 127.0.0.1 --port 8000
pause
