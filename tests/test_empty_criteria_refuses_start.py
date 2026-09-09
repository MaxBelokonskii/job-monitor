"""Пустой пресет — не повод молча работать впустую.

До этой правки воркер с пустым списком каналов запускался, крутился и ничего
не находил, а понять почему было нельзя: ни строки в логе, ни признака в
интерфейсе. Отказ со внятным текстом дешевле любой диагностики постфактум.
"""

from __future__ import annotations

import pytest

from job_monitor.db import connection


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    connection.reset_connection()
    yield
    connection.reset_connection()


def _active(client) -> int:
    return next(p for p in client.get("/api/presets").json() if p["is_active"])["id"]


def test_tg_start_is_refused_without_channels(client) -> None:
    response = client.post("/api/tg/start")
    assert response.status_code == 400
    detail = response.json()["detail"].lower()
    assert "канал" in detail
    assert "мой поиск" in detail, "в сообщении должно быть имя пресета"


def test_tg_start_is_refused_without_keywords(client) -> None:
    client.patch(f"/api/presets/{_active(client)}", json={"channels": ["qajobs"]})
    response = client.post("/api/tg/start")
    assert response.status_code == 400
    assert "ключев" in response.json()["detail"].lower()


def test_hh_start_is_refused_without_professions(client) -> None:
    response = client.post("/api/hh/start")
    assert response.status_code == 400
    assert "професс" in response.json()["detail"].lower()


def test_start_is_allowed_once_the_criteria_are_filled(client, monkeypatch) -> None:
    from job_monitor.workers.manager import WorkerState, manager

    started: list[str] = []

    async def fake_start(name: str):
        started.append(name)
        # Роут читает `epoch` из статуса — двойник обязан его нести, иначе
        # тест упадёт на нашей же подмене, а не на проверяемом поведении.
        return type("S", (), {"state": WorkerState.starting, "epoch": 1})()

    monkeypatch.setattr(manager, "start", fake_start)

    client.patch(
        f"/api/presets/{_active(client)}",
        json={"channels": ["qajobs"], "tg_keywords": ["qa"], "professions": ["QA"]},
    )
    assert client.post("/api/tg/start").status_code == 200
    assert client.post("/api/hh/start").status_code == 200
    assert started == ["tg", "hh"]


def test_the_refusal_names_the_preset_that_is_actually_active(client) -> None:
    """Сообщение должно указывать на тот пресет, который сейчас выбран, —
    иначе пользователь пойдёт править не тот."""
    second = client.post("/api/presets", json={"name": "Поддержка"}).json()["id"]
    client.post(f"/api/presets/{second}/activate")
    response = client.post("/api/tg/start")
    assert response.status_code == 400
    assert "Поддержка" in response.json()["detail"]
