PY := .venv/bin/python

.PHONY: install test run lint
install:
	test -d .venv || python3 -m venv .venv
	$(PY) -m pip install -e ".[dev]"
	$(PY) -m pip install -r requirements.lock

test:
	$(PY) -m pytest -q

run:
	$(PY) -m uvicorn api.main:app --host 127.0.0.1 --port 8000
