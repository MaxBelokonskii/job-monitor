PY := .venv/bin/python

.PHONY: install test run lint migrate-legacy
install:
	test -d .venv || python3 -m venv .venv
	$(PY) -m pip install -e ".[dev]"
	$(PY) -m pip install -r requirements.lock

test:
	@command -v node >/dev/null || echo "[внимание] Node.js не найден: поведенческие тесты frontend/app.js будут пропущены (XSS/CSP — только грепом)"
	$(PY) -m pytest -q

run:
	$(PY) -m uvicorn api.main:app --host 127.0.0.1 --port 8000

migrate-legacy:
	$(PY) -m job_monitor.cli migrate-legacy --from .
