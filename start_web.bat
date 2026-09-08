@echo off
chcp 65001 > nul
title Job Monitor
setlocal
set "BASE=%~dp0"
cd /d "%BASE%"

REM Установка выполняется только когда она действительно нужна: пока нет
REM отметки об успешной установке или по явной просьбе `start_web.bat setup`
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
REM
REM Признак «окружение готово» — ОТМЕТКА, которая пишется после того, как оба
REM `pip install` вернули 0, а не существование .venv\Scripts\python.exe.
REM Интерпретатор появляется сразу после `python -m venv`, то есть ДО
REM установки: если она падала (обрыв сети, пин без маркера платформы), то
REM первый запуск честно печатал ошибку, а второй считал окружение готовым,
REM пропускал установку целиком и падал на `No module named uvicorn` — уже
REM без единого объяснения. Отметка лежит внутри .venv: удалили окружение —
REM удалили и её.
set "STAMP=.venv\.install-ok"

set "SETUP="
if "%~1"=="" goto args_ok
if /i "%~1"=="setup" (
  set "SETUP=1"
  goto args_ok
)
echo [ошибка] неизвестный аргумент: %~1
echo Использование:
echo     start_web.bat           обычный запуск
echo     start_web.bat setup     переустановить зависимости и запустить
pause
exit /b 2
:args_ok

if not exist "%STAMP%" set "SETUP=1"

if defined SETUP (
  if not exist ".venv\Scripts\python.exe" (
    echo [setup] создаю виртуальное окружение .venv
    python -m venv .venv
    if errorlevel 1 goto failed
  )
  REM Отметка снимается ПЕРЕД установкой: прерванная на середине попытка не
  REM должна оставить в силе отметку от прошлой удачной.
  if exist "%STAMP%" del /q "%STAMP%"
  echo [setup] ставлю закреплённые версии из requirements.lock
  ".venv\Scripts\python.exe" -m pip install -r requirements.lock -q
  if errorlevel 1 goto failed
  ".venv\Scripts\python.exe" -m pip install -e . --no-deps -q
  if errorlevel 1 goto failed
  > "%STAMP%" echo job-monitor: зависимости установлены, удалите файл чтобы повторить установку
)

start "" "http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m uvicorn api.main:app --host 127.0.0.1 --port 8000
REM Код возврата uvicorn раньше не проверялся: занятый порт 8000 или ошибка
REM импорта выглядели ровно как штатное завершение по Ctrl+C.
if errorlevel 1 goto server_failed
pause
exit /b 0

:failed
echo.
echo [ошибка] установка не удалась. Обычные причины: нет подключения к сети;
echo не установлен Python (проверьте `python --version`); не хватает прав на
echo запись в каталог проекта. Полный вывод pip — выше. Повторить:
echo     start_web.bat setup
pause
exit /b 1

:server_failed
echo.
echo [ошибка] сервер завершился с ошибкой. Обычные причины: порт 8000 уже
echo занят другим экземпляром Job Monitor; окружение установлено не до конца
echo (`start_web.bat setup` переустановит его). Полный вывод — выше.
pause
exit /b 1
