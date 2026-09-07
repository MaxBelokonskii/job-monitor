# План 1: безопасность и гигиена

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Закрыть дефекты S1–S10 из спецификации, не меняя архитектуру приложения.

**Architecture:** Правки точечные и обратимые: состояние переезжает в каталог данных вне репозитория, к API добавляются проверка `Host` и токен приложения, фронтенд перестаёт собирать HTML строками, а git получает хук, который физически не даёт закоммитить секрет. Структура модулей остаётся прежней — её меняют планы 2 и 3.

**Tech Stack:** Python 3.13.3, FastAPI, Starlette middleware, pytest + httpx, pre-commit, detect-secrets, Node 22 (не используется, зафиксирован как доступный).

**Spec:** `docs/superpowers/specs/2026-09-08-hardening-and-rework-spec.md`

## Global Constraints

- Python `>=3.11`, проверка на 3.13.3.
- Зависимости закреплены точными версиями в `requirements.lock`, диапазоны — в `pyproject.toml`.
- Запрещены `eval`, `exec`, `pickle`, `marshal`, `shell=True`, установка пакетов в рантайме.
- Ни один секрет не попадает в БД, в ответы API, в логи, в репозиторий.
- Сервер слушает только `127.0.0.1`.
- Новый код с аннотациями типов, тесты `pytest`, коммиты Conventional Commits.
- `make test` зелёный на каждом коммите.

---

### Задача 1: рабочее окружение и закреплённые зависимости

Закрывает S9, S10. Без этого шага `pip install -r requirements.txt` падает на маркерах merge-конфликта, а `start_web.bat` тянет незакреплённые версии в системный Python.

**Files:**
- Create: `pyproject.toml`, `Makefile`, `requirements.lock`, `tests/conftest.py`, `tests/test_smoke.py`
- Modify: `.gitignore` (убрать маркеры конфликта), `hh_monitor.py:16,145-146` (убрать `webdriver-manager`)
- Delete: `requirements.txt`

**Interfaces:**
- Produces: `tests/conftest.py::client` — фикстура `fastapi.testclient.TestClient` для `api.main:app`, используется всеми последующими задачами.

- [ ] **Step 1: Создать venv и поставить зависимости**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
```

- [ ] **Step 2: Написать `pyproject.toml`**

```toml
[project]
name = "job-monitor"
version = "2.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115,<1.0",
    "uvicorn[standard]>=0.30,<1.0",
    "telethon>=1.36,<2.0",
    "selenium>=4.20,<5.0",
    "python-dotenv>=1.0,<2.0",
    "pydantic>=2.7,<3.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8,<9",
    "pytest-asyncio>=0.23,<1.0",
    "httpx>=0.27,<1.0",
    "pre-commit>=3.7,<5.0",
    "detect-secrets>=1.5,<2.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.setuptools]
py-modules = ["monitor", "hh_monitor"]
packages = ["api"]
```

`webdriver-manager` в список не входит намеренно: Selenium 4.6+ содержит Selenium Manager и сам находит chromedriver. Это убирает зависимость, которая на каждом старте качала бинарник из сети.

- [ ] **Step 3: Установить и зафиксировать точные версии**

```bash
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pip freeze --exclude-editable > requirements.lock
```

Если какой-то диапазон не разрешается — возьми версию, которую выбрал резолвер, и подставь её границы в `pyproject.toml`. Содержимое `requirements.lock` определяется резолвером, руками его не правь.

- [ ] **Step 4: Написать `Makefile`**

```makefile
PY := .venv/bin/python

.PHONY: install test run lint
install:
	$(PY) -m pip install -e ".[dev]"
	$(PY) -m pip install -r requirements.lock

test:
	$(PY) -m pytest -q

run:
	$(PY) -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

- [ ] **Step 5: Написать падающий тест на отсутствие `webdriver-manager`**

`tests/conftest.py`:

```python
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def client() -> TestClient:
    from api.main import app

    return TestClient(app)
```

`tests/test_smoke.py`:

```python
import importlib
import sys


def test_hh_monitor_does_not_use_webdriver_manager():
    sys.modules.pop("hh_monitor", None)
    importlib.import_module("hh_monitor")
    assert "webdriver_manager" not in sys.modules


def test_index_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Job Monitor" in response.text
```

- [ ] **Step 6: Запустить тесты и убедиться, что первый падает**

Run: `make test`
Expected: `test_hh_monitor_does_not_use_webdriver_manager` — FAIL (`webdriver_manager` в `sys.modules`), `test_index_is_served` — PASS.

- [ ] **Step 7: Убрать `webdriver-manager` из `hh_monitor.py`**

Удалить строку 16 (`from webdriver_manager.chrome import ChromeDriverManager`) и заменить строки 145-146:

```python
    driver = webdriver.Chrome(options=options)
```

Импорт `Service` со строки 11 тоже становится не нужен — удалить.

- [ ] **Step 8: Убрать маркеры конфликта из `.gitignore` и удалить `requirements.txt`**

В `.gitignore` оставить объединение обеих сторон конфликта без строк `<<<<<<<`, `=======`, `>>>>>>>`: `hh_cookies.json`, `all_sent_users.txt`, `hh_sent.json`, `config.json`, `monitor_pid.txt`, `hh_pid.txt`, `*.egg-info/`, `desktop.ini`, `.vscode/`, `.idea/`, `*.swp`, `*.doc`, `*.docx` — плюс всё, что там уже перечислено вне маркеров.

```bash
git rm requirements.txt
```

- [ ] **Step 9: Прогнать тесты и закоммитить**

Run: `make test`
Expected: PASS, 2 passed.

```bash
git add pyproject.toml Makefile requirements.lock tests/ .gitignore hh_monitor.py
git commit -m "build: pin dependencies, add venv/pytest tooling, drop webdriver-manager"
```

---

### Задача 2: состояние вне репозитория

Закрывает S8 — первопричину утечки S1. Пока сессии и `.env` лежат в рабочем каталоге, любой архив проекта снова унесёт их наружу.

**Files:**
- Create: `job_monitor/__init__.py`, `job_monitor/paths.py`, `tests/test_paths.py`
- Modify: `monitor.py:14-15,59,71,83,113`, `hh_monitor.py:18-24`, `api/config_routes.py:9-11`, `api/tg_routes.py:13-18`, `api/hh_routes.py:12-16`, `api/auth_routes.py:10,20`

**Interfaces:**
- Produces:
  - `job_monitor.paths.data_dir() -> pathlib.Path` — каталог данных, создаётся с правами `0700`
  - `job_monitor.paths.path(*parts: str) -> pathlib.Path`
  - `job_monitor.paths.env_file() -> Path`, `logs_dir() -> Path`, `db_file() -> Path`, `tg_session() -> Path`, `hh_cookies() -> Path`, `config_file() -> Path`

- [ ] **Step 1: Написать падающий тест**

`tests/test_paths.py`:

```python
import stat
from pathlib import Path

from job_monitor import paths

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_data_dir_follows_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path / "state"))
    assert paths.data_dir() == tmp_path / "state"


def test_data_dir_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path / "state"))
    mode = stat.S_IMODE(paths.data_dir().stat().st_mode)
    assert mode == 0o700


def test_default_is_outside_repository(monkeypatch):
    monkeypatch.delenv("JOB_MONITOR_DATA_DIR", raising=False)
    for factory in (paths.env_file, paths.db_file, paths.tg_session, paths.hh_cookies):
        assert REPO_ROOT not in factory().parents, f"{factory.__name__} внутри репозитория"


def test_nested_path_creates_parent(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path / "state"))
    target = paths.path("logs", "hh.log")
    assert target.parent.is_dir()
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'job_monitor'`.

- [ ] **Step 3: Реализовать `job_monitor/paths.py`**

```python
"""Единственное место, где вычисляются пути к состоянию приложения."""

from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "JOB_MONITOR_DATA_DIR"
DEFAULT_DIR = Path.home() / ".job-monitor"


def data_dir() -> Path:
    raw = os.getenv(ENV_VAR)
    base = Path(raw).expanduser() if raw else DEFAULT_DIR
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    return base


def path(*parts: str) -> Path:
    target = data_dir().joinpath(*parts)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return target


def env_file() -> Path:
    return path(".env")


def config_file() -> Path:
    return path("config.json")


def db_file() -> Path:
    return path("job_monitor.db")


def logs_dir() -> Path:
    directory = path("logs")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


def tg_session() -> Path:
    """Telethon сам добавляет расширение .session."""
    return path("telegram")


def hh_cookies() -> Path:
    return path("hh_cookies.json")
```

`job_monitor/__init__.py` оставить пустым.

- [ ] **Step 4: Запустить тест**

Run: `.venv/bin/python -m pytest tests/test_paths.py -v`
Expected: PASS, 4 passed.

- [ ] **Step 5: Перевести существующие модули на `paths`**

`monitor.py`: заменить `CONFIG_PATH`, `PID_FILE`, `LOG_DIR`, `ALL_SENT_FILE` и путь сессии.

```python
from job_monitor import paths

CONFIG_PATH = paths.config_file()
PID_FILE = paths.path("monitor_pid.txt")
LOG_DIR = paths.logs_dir()
ALL_SENT_FILE = paths.path("all_sent_users.txt")
client = TelegramClient(str(paths.tg_session()), API_ID, API_HASH)
load_dotenv(paths.env_file())
```

`hh_monitor.py`: `CONFIG_PATH = paths.config_file()`, `HH_SENT_PATH = paths.path("hh_sent.json")`, `HH_LOG_PATH = paths.logs_dir() / "hh.log"`, `HH_COOKIES_PATH = paths.hh_cookies()`; убрать `os.makedirs(os.path.join(BASE_DIR, "logs"), ...)` — это делает `paths.logs_dir()`.

`api/config_routes.py`: `CONFIG_PATH = paths.config_file()`, `ENV_PATH = paths.env_file()`.

`api/tg_routes.py`: `PID_FILE`, `LOG_DIR`, `SYSTEM_LOG`, `ALL_SENT_FILE` через `paths`. `SCRIPT_PATH` остаётся привязанным к `BASE_DIR` — это код, не состояние.

`api/hh_routes.py`: `HH_SENT_PATH`, `HH_LOG_PATH`, `HH_PID_FILE` через `paths`; `HH_SCRIPT_PATH` остаётся.

`api/auth_routes.py:20`: `session_path = str(paths.path("telegram_web"))`.

- [ ] **Step 6: Дописать тест, запрещающий пути состояния в коде**

Добавить в `tests/test_paths.py`:

```python
import re

STATE_LITERALS = re.compile(
    r"os\.path\.join\(\s*BASE_DIR\s*,\s*[\"'](?:\.env|config\.json|session|"
    r"session_web|hh_cookies\.json|hh_sent\.json|all_sent_users\.txt|logs)"
)


def test_no_state_paths_relative_to_repo():
    for name in ("monitor.py", "hh_monitor.py", "api/config_routes.py",
                 "api/tg_routes.py", "api/hh_routes.py", "api/auth_routes.py"):
        source = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert not STATE_LITERALS.search(source), f"{name} всё ещё пишет состояние в репозиторий"
```

- [ ] **Step 7: Прогнать всё и закоммитить**

Run: `make test`
Expected: PASS, 7 passed.

```bash
git add job_monitor/ tests/test_paths.py monitor.py hh_monitor.py api/
git commit -m "refactor: move all runtime state to \$JOB_MONITOR_DATA_DIR"
```

---

### Задача 3: git не даёт закоммитить секрет

Закрывает S1 процессно. `.gitignore` не смотрит внутрь архивов — именно так утекли `.env` и `.session`, поэтому нужен хук, проверяющий содержимое.

**Files:**
- Create: `scripts/check_no_secrets.py`, `.pre-commit-config.yaml`, `.secrets.baseline`, `tests/test_check_no_secrets.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `scripts/check_no_secrets.py` — CLI, принимает пути файлами-аргументами, код возврата `1` при находке. Вызывается pre-commit и напрямую из тестов.

- [ ] **Step 1: Написать падающий тест**

`tests/test_check_no_secrets.py`:

```python
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_no_secrets.py"


def run(*files: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, files)],
        capture_output=True, text=True,
    )


def test_rejects_session_file(tmp_path):
    victim = tmp_path / "telegram.session"
    victim.write_bytes(b"SQLite format 3\x00")
    result = run(victim)
    assert result.returncode == 1
    assert "telegram.session" in result.stdout


def test_rejects_env_file(tmp_path):
    victim = tmp_path / ".env"
    victim.write_text("TG_API_HASH=deadbeef\n")
    assert run(victim).returncode == 1


def test_rejects_rar_by_magic_bytes_despite_harmless_name(tmp_path):
    victim = tmp_path / "backup.bin"
    victim.write_bytes(b"Rar!\x1a\x07\x01\x00rest")
    result = run(victim)
    assert result.returncode == 1
    assert "архив" in result.stdout


def test_rejects_zip_by_magic_bytes(tmp_path):
    victim = tmp_path / "files.dat"
    victim.write_bytes(b"PK\x03\x04rest")
    assert run(victim).returncode == 1


def test_allows_source_file(tmp_path):
    ok = tmp_path / "monitor.py"
    ok.write_text("print('hello')\n")
    assert run(ok).returncode == 0
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_check_no_secrets.py -v`
Expected: FAIL — файла `scripts/check_no_secrets.py` нет.

- [ ] **Step 3: Реализовать `scripts/check_no_secrets.py`**

```python
#!/usr/bin/env python3
"""Не даёт закоммитить секреты и архивы. Проверяет и имя, и содержимое."""

from __future__ import annotations

import sys
from fnmatch import fnmatch
from pathlib import Path

FORBIDDEN_NAMES = (
    ".env", "*.session", "*.session-journal", "*.sqlite", "*.db",
    "*cookies*.json", "config.json", "all_sent_users.txt", "sent_log_*.txt",
    "*.pdf", "*.doc", "*.docx",
)
ARCHIVE_MAGIC = {
    b"Rar!\x1a\x07": "RAR",
    b"PK\x03\x04": "ZIP",
    b"7z\xbc\xaf\x27\x1c": "7z",
    b"\x1f\x8b": "GZIP",
}


def is_archive(file: Path) -> str | None:
    try:
        head = file.open("rb").read(8)
    except OSError:
        return None
    for magic, label in ARCHIVE_MAGIC.items():
        if head.startswith(magic):
            return label
    return None


def main(argv: list[str]) -> int:
    problems: list[str] = []
    for raw in argv:
        file = Path(raw)
        if any(fnmatch(file.name, pattern) for pattern in FORBIDDEN_NAMES):
            problems.append(f"{raw}: запрещённое имя файла — состояние и секреты живут в $JOB_MONITOR_DATA_DIR")
            continue
        label = is_archive(file)
        if label:
            problems.append(f"{raw}: это архив {label}. Архивы не коммитим — .gitignore не видит их содержимое")
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Запустить тест**

Run: `.venv/bin/python -m pytest tests/test_check_no_secrets.py -v`
Expected: PASS, 5 passed.

- [ ] **Step 5: Подключить pre-commit**

`.pre-commit-config.yaml`:

```yaml
repos:
  - repo: local
    hooks:
      - id: check-no-secrets
        name: no secrets, no archives
        entry: .venv/bin/python scripts/check_no_secrets.py
        language: system
        stages: [pre-commit]
  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.5.0
    hooks:
      - id: detect-secrets
        args: ["--baseline", ".secrets.baseline"]
```

```bash
.venv/bin/detect-secrets scan > .secrets.baseline
.venv/bin/pre-commit install
```

- [ ] **Step 6: Проверить хук вживую**

```bash
printf 'TG_API_HASH=deadbeef\n' > .env.tmp && mv .env.tmp .env
git add -f .env
git commit -m "should be blocked" || echo "хук сработал"
git restore --staged .env && rm .env
```
Expected: коммит отклонён с сообщением про запрещённое имя.

- [ ] **Step 7: Дополнить `.gitignore` и закоммитить**

Добавить блок:

```gitignore
# Архивы: .gitignore не видит их содержимое
*.rar
*.zip
*.7z
*.tar
*.tar.gz
*.tgz
# Состояние приложения (дублирует $JOB_MONITOR_DATA_DIR на случай локального оверрайда)
*.sqlite
*.db
.secrets.baseline.local
```

```bash
git add scripts/ .pre-commit-config.yaml .secrets.baseline .gitignore tests/test_check_no_secrets.py
git commit -m "chore: block secrets and archives in pre-commit"
```

---

### Задача 4: секреты не покидают сервер

Закрывает S2 и S7.

**Files:**
- Modify: `api/config_routes.py:70-95,133-160`, `frontend/app.js:299-310`
- Create: `job_monitor/envfile.py`, `tests/test_secrets_handling.py`

**Interfaces:**
- Consumes: `job_monitor.paths.env_file` (Задача 2)
- Produces: `job_monitor.envfile.write_env(values: dict[str, str]) -> None` — атомарная запись `.env` с правами `0600`; `job_monitor.envfile.read_env() -> dict[str, str]`. Обе бросают `ValueError` на значении с `\n` или `\r`.

- [ ] **Step 1: Написать падающий тест**

`tests/test_secrets_handling.py`:

```python
import stat

import pytest

from job_monitor import envfile


def test_rejects_newline_injection(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        envfile.write_env({"TG_API_ID": "123\nSAFE_MODE=false"})


def test_env_file_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    envfile.write_env({"TG_API_ID": "123", "TG_API_HASH": "abc"})
    mode = stat.S_IMODE((tmp_path / ".env").stat().st_mode)
    assert mode == 0o600


def test_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    envfile.write_env({"TG_API_ID": "123", "SAFE_MODE": "true"})
    assert envfile.read_env() == {"TG_API_ID": "123", "SAFE_MODE": "true"}


def test_reveal_hash_endpoint_is_gone(client):
    assert client.get("/api/config/reveal-hash").status_code == 404


def test_config_response_never_contains_hash(client):
    body = client.get("/api/config").json()
    assert "api_hash" not in body
    assert body["api_hash_set"] in (True, False)
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_secrets_handling.py -v`
Expected: FAIL — нет модуля `job_monitor.envfile`, `reveal-hash` отвечает 200.

- [ ] **Step 3: Реализовать `job_monitor/envfile.py`**

```python
from __future__ import annotations

import os
import tempfile

from job_monitor import paths

FORBIDDEN = ("\n", "\r", "\x00")


def _validate(key: str, value: str) -> None:
    if not key or "=" in key or any(bad in key for bad in FORBIDDEN):
        raise ValueError(f"недопустимое имя переменной: {key!r}")
    if any(bad in value for bad in FORBIDDEN):
        raise ValueError(f"значение {key} содержит перевод строки")


def read_env() -> dict[str, str]:
    target = paths.env_file()
    if not target.exists():
        return {}
    result: dict[str, str] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def write_env(values: dict[str, str]) -> None:
    merged = read_env()
    for key, value in values.items():
        _validate(key, value)
        merged[key] = value
    target = paths.env_file()
    handle, temporary = tempfile.mkstemp(dir=str(target.parent))
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        for key, value in merged.items():
            stream.write(f"{key}={value}\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
```

- [ ] **Step 4: Переписать `api/config_routes.py`**

Удалить функцию `_sync_env` (строки 77-95) и эндпоинт `reveal_hash` (строки 157-160). В `save_config` вызывать `envfile.write_env`, в `load_config` — `envfile.read_env`. В `get_config` не возвращать `api_hash` вообще:

```python
@router.get("")
async def get_config():
    cfg = load_config()
    cfg.pop("api_hash", None)
    cfg["api_hash_set"] = bool(envfile.read_env().get("TG_API_HASH"))
    return cfg
```

```python
def save_config(cfg: dict) -> None:
    safe_cfg = {k: v for k, v in cfg.items() if k not in ("api_id", "api_hash")}
    paths.config_file().write_text(
        json.dumps(safe_cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    secrets_to_write = {}
    if cfg.get("api_id"):
        secrets_to_write["TG_API_ID"] = str(cfg["api_id"])
    if cfg.get("api_hash"):
        secrets_to_write["TG_API_HASH"] = str(cfg["api_hash"])
    secrets_to_write["SAFE_MODE"] = "true" if cfg.get("safe_mode") else "false"
    secrets_to_write["PARSE_HISTORY"] = "true" if cfg.get("parse_history") else "false"
    secrets_to_write["HISTORY_LIMIT"] = str(cfg.get("history_limit", 50))
    envfile.write_env(secrets_to_write)
```

- [ ] **Step 5: Убрать кнопку показа хеша из фронтенда**

В `frontend/index.html:293` удалить кнопку `👁 Показать hash`. В `frontend/app.js` удалить функцию `toggleApiHash` (строки 299-310) целиком: раскрывать секрет больше нечем и незачем.

- [ ] **Step 6: Прогнать тесты и закоммитить**

Run: `make test`
Expected: PASS, 12 passed.

```bash
git add job_monitor/envfile.py api/config_routes.py frontend/ tests/test_secrets_handling.py
git commit -m "fix: never return api_hash from the API, harden .env writes"
```

---

### Задача 5: токен приложения и проверка Host

Закрывает S3 и S4. Нужны оба механизма, и вот почему: токен в обязательном заголовке убивает CSRF (кросс-доменный запрос с кастомным заголовком требует preflight, который CORS не пропустит), но не спасает от DNS rebinding — там страница считается «своим» origin и может сама вычитать токен из HTML. От rebinding защищает проверка `Host`. Убрав любой из двух, дыру открываешь заново.

**Files:**
- Create: `job_monitor/security.py`, `tests/test_local_access.py`
- Modify: `api/main.py:15-40`, `frontend/index.html:9` (мета-тег), `frontend/app.js:1-22`

**Interfaces:**
- Produces:
  - `job_monitor.security.APP_TOKEN: str` — случайный токен, генерируется один раз при импорте
  - `job_monitor.security.TOKEN_HEADER: str` = `"X-App-Token"`
  - `job_monitor.security.app_token_middleware(request, call_next)` — 403 для `/api/*` без верного токена

- [ ] **Step 1: Написать падающий тест**

`tests/test_local_access.py`:

```python
from job_monitor.security import APP_TOKEN, TOKEN_HEADER


def test_api_without_token_is_forbidden(client):
    assert client.get("/api/config").status_code == 403


def test_api_with_token_works(client):
    response = client.get("/api/config", headers={TOKEN_HEADER: APP_TOKEN})
    assert response.status_code == 200


def test_api_with_wrong_token_is_forbidden(client):
    response = client.get("/api/config", headers={TOKEN_HEADER: "wrong"})
    assert response.status_code == 403


def test_foreign_host_is_rejected(client):
    response = client.get("/", headers={"Host": "attacker.example"})
    assert response.status_code == 400


def test_index_carries_the_token(client):
    body = client.get("/").text
    assert APP_TOKEN in body


def test_static_files_need_no_token(client):
    assert client.get("/static/app.js").status_code == 200
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_local_access.py -v`
Expected: FAIL — нет модуля `job_monitor.security`.

- [ ] **Step 3: Реализовать `job_monitor/security.py`**

```python
from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse

APP_TOKEN = secrets.token_urlsafe(32)
TOKEN_HEADER = "X-App-Token"
TOKEN_PLACEHOLDER = "__APP_TOKEN__"
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]


async def app_token_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if request.url.path.startswith("/api/"):
        supplied = request.headers.get(TOKEN_HEADER, "")
        if not secrets.compare_digest(supplied, APP_TOKEN):
            return JSONResponse({"detail": "invalid app token"}, status_code=403)
    return await call_next(request)
```

- [ ] **Step 4: Подключить middleware в `api/main.py`**

```python
from starlette.middleware.trustedhost import TrustedHostMiddleware

from job_monitor.security import ALLOWED_HOSTS, APP_TOKEN, TOKEN_PLACEHOLDER, app_token_middleware

app.middleware("http")(app_token_middleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
```

`TrustedHostMiddleware` добавляется последним, чтобы выполниться первым: `Host` проверяется раньше токена. Starlette сам отрезает порт, поэтому `127.0.0.1:8000` попадает под `127.0.0.1`.

`serve_ui` подставляет токен в отдаваемый HTML:

```python
@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    html_path = os.path.join(FRONTEND_DIR, "index.html")
    if not os.path.exists(html_path):
        return "<h1>index.html not found in frontend/</h1>"
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read().replace(TOKEN_PLACEHOLDER, APP_TOKEN)
```

- [ ] **Step 5: Научить фронтенд отправлять токен**

`frontend/index.html`, в `<head>` после строки 6:

```html
<meta name="app-token" content="__APP_TOKEN__">
```

`frontend/app.js`, заменить блок 1-22:

```javascript
const API = '/api';
const APP_TOKEN = document.querySelector('meta[name="app-token"]').content;

function headers(extra = {}) {
  return { 'X-App-Token': APP_TOKEN, ...extra };
}

async function apiGet(path) {
  try { return await (await fetch(API + path, { headers: headers() })).json(); }
  catch { return null; }
}
async function apiSend(method, path, body = {}) {
  try {
    return await (await fetch(API + path, {
      method,
      headers: headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    })).json();
  } catch { return null; }
}
const apiPost = (path, body) => apiSend('POST', path, body);
const apiPatch = (path, body) => apiSend('PATCH', path, body);
```

Абсолютный адрес `http://127.0.0.1:8000/api` заменён на относительный `/api` — иначе страница, открытая как `http://localhost:8000`, шлёт кросс-доменные запросы сама себе.

Две оставшиеся ручные `fetch` (строки 346 и 511) тоже перевести на `headers({ 'Content-Type': 'application/json' })`.

- [ ] **Step 6: Прогнать тесты и закоммитить**

Run: `make test`
Expected: PASS, 18 passed.

- [ ] **Step 7: Проверить руками**

```bash
make run
```
Открыть `http://127.0.0.1:8000`, убедиться, что дашборд загружается и метрики обновляются. Затем:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/config
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: attacker.example' http://127.0.0.1:8000/
```
Expected: `403` и `400`.

```bash
git add job_monitor/security.py api/main.py frontend/ tests/test_local_access.py
git commit -m "feat: require app token and validate Host for local API"
```

---

### Задача 6: фронтенд перестаёт собирать HTML строками

Закрывает S5 и S6. Текст сообщения Telegram и поля вакансий с hh.ru — данные, которыми управляет посторонний, а попадают они прямо в `innerHTML`.

**Files:**
- Modify: `frontend/app.js` (13 мест с `innerHTML`), `api/main.py` (заголовки безопасности)
- Create: `tests/test_frontend_safety.py`

**Interfaces:**
- Consumes: `job_monitor.security.APP_TOKEN` (Задача 5)
- Produces: `job_monitor.security.SECURITY_HEADERS: dict[str, str]`; `job_monitor.security.security_headers_middleware`

- [ ] **Step 1: Написать падающий тест**

`tests/test_frontend_safety.py`:

```python
from pathlib import Path

APP_JS = Path(__file__).resolve().parents[1] / "frontend" / "app.js"


def test_app_js_never_uses_inner_html():
    source = APP_JS.read_text(encoding="utf-8")
    assert "innerHTML" not in source, (
        "innerHTML запрещён: содержимое Telegram и hh.ru недоверенное. "
        "Собирай узлы через el()/textContent."
    )


def test_csp_header_is_sent(client):
    headers = client.get("/").headers
    assert "content-security-policy" in headers
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
```

Проверка строкой, а не JS-раннером, сознательно: она держит инвариант «нет `innerHTML`» дешево и не тянет в проект браузерное окружение. Не заменяй её на что-то «умнее».

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_frontend_safety.py -v`
Expected: FAIL — 13 вхождений `innerHTML`, заголовка CSP нет.

- [ ] **Step 3: Добавить заголовки безопасности**

В `job_monitor/security.py`:

```python
CSP = "; ".join((
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data:",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "object-src 'none'",
))
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


async def security_headers_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    for key, value in SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    return response
```

`script-src 'unsafe-inline'` пока обязателен: `index.html` держит обработчики в атрибутах `onclick`. План 3 убирает их и ужесточает директиву.

В `api/main.py`: `app.middleware("http")(security_headers_middleware)`.

- [ ] **Step 4: Добавить во фронтенд конструктор узлов**

В начало `frontend/app.js` после определения `APP_TOKEN`:

```javascript
function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'style') node.style.cssText = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child == null) continue;
    node.append(typeof child === 'string' ? document.createTextNode(child) : child);
  }
  return node;
}

function fill(target, children) {
  target.replaceChildren(...[].concat(children).filter(Boolean));
}
```

- [ ] **Step 5: Переписать все 13 мест**

Полный список, ничего не пропускать: `updateTGButton` (66, 69), `updateHHButton` (78, 81), `updateDashboard` (116, 122, 123), `renderChannelEdit` (163), `renderKeywords` (184, 186), `checkWebAuth` (321, 324), `renderHHKeywords` (362, 363), `renderSeleniumSteps` (406, 409), `renderChats` (462, 463), `loadChatMessages` (494, 496, 497, 498), `loadHHVacancies` (525, 528), `refreshLogs` (560, 562), `clearConsole` (574), `showRestartBanner` (617), `pollStatus` (687).

Два образца, по которым делаются остальные.

Сообщения чата (было строка 498, самое опасное место — текст пишет посторонний):

```javascript
  fill(el, msgs.map(m => el('div', { class: `msg-wrap ${m.out ? 'out' : 'in'}` }, [
    el('div', { class: `msg-bubble ${m.out ? 'msg-out' : 'msg-in'}`, text: m.text }),
    el('div', {
      style: `font-size:10px;opacity:.6;margin-top:2px;text-align:${m.out ? 'right' : 'left'}`,
      text: m.date,
    }),
  ])));
```

`textContent` сам покажет переводы строк как есть; чтобы сохранить прежний вид, добавь в `style.css` для `.msg-bubble` правило `white-space: pre-wrap;` вместо замены `\n` на `<br>`.

Список контактов (было строка 463, здесь же умирает инъекция в атрибут из S6 — обработчик вешается функцией, а не строкой):

```javascript
    fill(el, chats.map(c => {
      const name = c.username || '';
      return el('div', {
        class: 'chat-contact',
        id: 'contact-' + name.replace('@', ''),
        onclick: () => openChat(name),
      }, [
        el('div', { class: 'chat-avatar', text: name.replace('@', '').slice(0, 2).toUpperCase() }),
        el('div', { style: 'flex:1;min-width:0' }, [
          el('div', { class: 'chat-name', text: name }),
          el('div', { class: 'chat-preview', text: c.preview || '' }),
        ]),
        el('div', { class: 'chat-time', text: c.time || '' }),
      ]);
    }));
```

Классы `chat-avatar`, `chat-name`, `chat-preview`, `chat-time` перенести в `style.css` из бывших инлайновых `style`-строк — так короче и не мешает будущему ужесточению CSP.

- [ ] **Step 6: Прогнать тесты**

Run: `make test`
Expected: PASS, 20 passed.

- [ ] **Step 7: Проверить, что XSS действительно не срабатывает**

```bash
make run
```
Открыть настройки, ввести в поле «Ключевое слово» строку `<img src=x onerror=alert(1)>`, сохранить, вернуться на дашборд. Ожидается, что строка видна как текст в теге ключевого слова и никакого `alert` нет.

- [ ] **Step 8: Закоммитить**

```bash
git add frontend/ job_monitor/security.py api/main.py tests/test_frontend_safety.py
git commit -m "fix: build DOM nodes instead of HTML strings, add CSP and security headers"
```

---

### Задача 7: документация и запуск без хардкода

**Files:**
- Create: `SECURITY.md`
- Modify: `README.md`, `start_web.bat`, `Makefile`

- [ ] **Step 1: Написать `SECURITY.md`**

Разделы, каждый — по факту из спецификации:

- **Модель угроз.** От чего защищаемся (враждебная страница в браузере: CSRF и DNS rebinding; недоверенный контент из Telegram и hh.ru: XSS; утечка секретов в git) и от чего нет (локальный пользователь с доступом к `~`, физический доступ).
- **Где лежат секреты.** `$JOB_MONITOR_DATA_DIR` (по умолчанию `~/.job-monitor`), права `0700` на каталог и `0600` на `.env`. Внутри: `.env`, `telegram.session`, `telegram_web.session`, `hh_cookies.json`, `job_monitor.db`, `logs/`. В репозитории состояния нет и быть не должно.
- **Механизмы.** Токен `X-App-Token` на все `/api/*`, `TrustedHostMiddleware` на `127.0.0.1`/`localhost`, CSP и заголовки, запрет `innerHTML` в `app.js` (держится тестом `tests/test_frontend_safety.py`), pre-commit `scripts/check_no_secrets.py`.
- **Что делать при утечке.** Порядок: 1) Telegram → Настройки → Устройства → завершить все сеансы (это обнуляет `auth_key`, только после этого остальное имеет смысл); 2) перевыпустить `api_hash` на my.telegram.org/apps; 3) `git filter-repo --path <файл> --invert-paths` и force-push; 4) попросить GitHub очистить кэш; 5) уведомить людей, чьи данные были в утечке.
- **Чего никогда не делать.** Коммитить архивы проекта; отдавать секрет через API; ставить пакеты в рантайме; слушать адрес кроме `127.0.0.1`.

- [ ] **Step 2: Переписать `start_web.bat` как тонкую обёртку**

```bat
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
```

Ни абсолютных путей к интерпретатору, ни установки в системный Python.

- [ ] **Step 3: Обновить `README.md`**

Заменить раздел установки: `make install` и `make run` для macOS/Linux, `start_web.bat` для Windows. Добавить абзац про `JOB_MONITOR_DATA_DIR` и ссылку на `SECURITY.md`. Убрать упоминания `requirements.txt`.

- [ ] **Step 4: Прогнать тесты и закоммитить**

Run: `make test`
Expected: PASS, 20 passed.

```bash
git add SECURITY.md README.md start_web.bat Makefile
git commit -m "docs: add threat model, cross-platform launch without hardcoded paths"
```

---

## Итог плана 1

Закрыты S1 (процессно — хук + каталог данных), S2, S3, S4, S5, S6, S7, S8, S9, S10. Появилась тестовая база, на которую опираются планы 2 и 3. Архитектурные дефекты L1–L13 остаются — ими занимаются следующие планы.
