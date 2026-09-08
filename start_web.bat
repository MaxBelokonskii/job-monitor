@echo off
chcp 65001 > nul
title Job Monitor
setlocal
set "BASE=%~dp0"
cd /d "%BASE%"

REM Установка выполняется только когда она действительно нужна: при первом
REM запуске (окружения ещё нет) или по явной просьбе `start_web.bat setup`
REM (например после обновления requirements.lock). Раньше два `pip install`
REM шли на КАЖДОМ запуске — обычный старт требовал сети и мог занять минуты,
REM а без сети падал на ровном месте.
REM
REM Порядок тоже важен: первым шёл `pip install -e ".[dev]"` с диапазонами
REM версий, ДО применения лока, поэтому pip разрешал их «на сегодня», и
REM закреплённые версии оказывались всего лишь вторым мнением. Теперь
REM сначала применяется requirements.lock, а сам пакет ставится с
REM --no-deps: зависимости уже зафиксированы, и диапазонам из pyproject.toml
REM нечего сдвигать. Лок включает и инструменты разработки (pytest,
REM pre-commit), так что отдельный экстра [dev] здесь не нужен.
set "SETUP="
if /i "%~1"=="setup" set "SETUP=1"
if not exist ".venv\Scripts\python.exe" set "SETUP=1"

if defined SETUP (
  if not exist ".venv\Scripts\python.exe" (
    echo [setup] создаю виртуальное окружение .venv
    python -m venv .venv
    if errorlevel 1 goto failed
  )
  echo [setup] ставлю закреплённые версии из requirements.lock
  ".venv\Scripts\python.exe" -m pip install -r requirements.lock -q
  if errorlevel 1 goto failed
  ".venv\Scripts\python.exe" -m pip install -e . --no-deps -q
  if errorlevel 1 goto failed
)

start "" "http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m uvicorn api.main:app --host 127.0.0.1 --port 8000
pause
exit /b 0

:failed
echo.
echo [ошибка] установка не удалась — проверьте подключение к сети и запустите:
echo     start_web.bat setup
pause
exit /b 1
