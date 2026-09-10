from __future__ import annotations

import pytest

from job_monitor.db import connection


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Своя база на каждый тест.

    Каталог данных в conftest сессионный, поэтому пресеты, созданные одним
    тестом, видны следующему: проверки «пресет ровно один» и «удалили —
    остался один» ловили бы чужие строки. Соединение — синглтон, приложение
    берёт его на каждый запрос, поэтому достаточно сбросить его вокруг теста.
    """
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    connection.reset_connection()
    yield
    connection.reset_connection()


def test_fresh_install_has_one_empty_preset(client) -> None:
    response = client.get("/api/presets")
    assert response.status_code == 200
    presets = response.json()
    assert len(presets) == 1
    assert presets[0]["is_active"] is True
    assert presets[0]["channels_count"] == 0
    assert presets[0]["professions_count"] == 0


def test_presets_require_the_app_token(raw_client) -> None:
    assert raw_client.get("/api/presets").status_code == 403
    assert raw_client.post("/api/presets", json={"name": "X"}).status_code == 403


def test_create_read_and_patch_a_preset(client) -> None:
    created = client.post("/api/presets", json={"name": "Поддержка"})
    assert created.status_code == 200
    preset_id = created.json()["id"]

    patched = client.patch(
        f"/api/presets/{preset_id}",
        json={"channels": ["support_jobs"], "professions": ["специалист поддержки"]},
    )
    assert patched.status_code == 200

    stored = client.get(f"/api/presets/{preset_id}").json()
    assert stored["criteria"]["channels"] == ["support_jobs"]
    assert stored["criteria"]["professions"] == ["специалист поддержки"]


def test_patch_keeps_unrelated_fields(client) -> None:
    preset_id = client.post("/api/presets", json={"name": "A"}).json()["id"]
    client.patch(f"/api/presets/{preset_id}", json={"channels": ["a"]})
    client.patch(f"/api/presets/{preset_id}", json={"tg_keywords": ["qa"]})
    criteria = client.get(f"/api/presets/{preset_id}").json()["criteria"]
    assert criteria["channels"] == ["a"]
    assert criteria["tg_keywords"] == ["qa"]


def test_patch_with_an_unknown_code_is_rejected_and_changes_nothing(client) -> None:
    preset_id = client.post("/api/presets", json={"name": "A"}).json()["id"]
    client.patch(f"/api/presets/{preset_id}", json={"channels": ["a"]})
    bad = client.patch(f"/api/presets/{preset_id}", json={"hh_experience": "выдумка"})
    assert bad.status_code == 422
    assert client.get(f"/api/presets/{preset_id}").json()["criteria"]["channels"] == ["a"]


def test_duplicate_name_is_refused(client) -> None:
    client.post("/api/presets", json={"name": "Одинаковое"})
    again = client.post("/api/presets", json={"name": "Одинаковое"})
    assert again.status_code == 400
    assert "имя" in again.json()["detail"].lower()


def test_rename_into_a_taken_name_is_refused(client) -> None:
    first = client.post("/api/presets", json={"name": "Первый"}).json()["id"]
    client.post("/api/presets", json={"name": "Второй"})
    refused = client.patch(f"/api/presets/{first}", json={"name": "Второй"})
    assert refused.status_code == 400
    assert client.get(f"/api/presets/{first}").json()["name"] == "Первый"


def test_copy_from_clones_the_criteria(client) -> None:
    source = client.post("/api/presets", json={"name": "Источник"}).json()["id"]
    client.patch(
        f"/api/presets/{source}", json={"channels": ["a"], "tg_keywords": ["qa"]}
    )
    copy_id = client.post(
        "/api/presets", json={"name": "Копия", "copy_from": source}
    ).json()["id"]
    assert client.get(f"/api/presets/{copy_id}").json()["criteria"]["channels"] == ["a"]


def test_the_active_preset_cannot_be_deleted(client) -> None:
    """Без активного пресета воркерам нечего читать, а интерфейсу нечего
    показывать — приложение оказалось бы молча нерабочим."""
    client.post("/api/presets", json={"name": "Запасной"})
    active = next(p for p in client.get("/api/presets").json() if p["is_active"])
    refused = client.delete(f"/api/presets/{active['id']}")
    assert refused.status_code == 400
    assert len(client.get("/api/presets").json()) == 2


def test_the_last_preset_cannot_be_deleted(client) -> None:
    presets = client.get("/api/presets").json()
    assert len(presets) == 1
    refused = client.delete(f"/api/presets/{presets[0]['id']}")
    assert refused.status_code == 400


def test_a_non_active_preset_is_deleted(client) -> None:
    spare = client.post("/api/presets", json={"name": "Лишний"}).json()["id"]
    assert client.delete(f"/api/presets/{spare}").status_code == 200
    assert [p["name"] for p in client.get("/api/presets").json()] == ["Мой поиск"]


def test_activate_switches_and_reports_what_it_stopped(client) -> None:
    second = client.post("/api/presets", json={"name": "Второй"}).json()["id"]
    response = client.post(f"/api/presets/{second}/activate")
    assert response.status_code == 200
    assert response.json()["activated"] == second
    assert response.json()["stopped"] == []
    active = next(p for p in client.get("/api/presets").json() if p["is_active"])
    assert active["id"] == second


def test_activate_stops_a_running_worker(client, monkeypatch) -> None:
    """Горячая замена отвергнута решением D9: воркер, переключённый посреди
    отправки, может написать человеку от одного пресета с резюме от другого,
    а отозвать сообщение нельзя."""
    from job_monitor.workers.manager import WorkerState, manager

    stopped: list[str] = []

    async def fake_stop(name: str):
        stopped.append(name)
        return type(
            "S", (), {"state": WorkerState.stopped, "last_error": None, "epoch": 1}
        )()

    monkeypatch.setattr(manager, "running", lambda: ["tg"])
    monkeypatch.setattr(manager, "stop", fake_stop)

    second = client.post("/api/presets", json={"name": "Второй"}).json()["id"]
    response = client.post(f"/api/presets/{second}/activate")
    assert response.status_code == 200
    assert stopped == ["tg"]
    assert response.json()["stopped"] == ["tg"]


def test_activate_is_refused_when_a_worker_will_not_stop(client, monkeypatch) -> None:
    """Переключение наполовину хуже отказа: Selenium может стоять посреди
    отклика, и смена резюме под ним даёт отклик не тем документом."""
    from job_monitor.workers.manager import WorkerState, manager

    async def wedged_stop(name: str):
        return type(
            "S",
            (),
            {"state": WorkerState.error, "last_error": "не остановился", "epoch": 1},
        )()

    monkeypatch.setattr(manager, "running", lambda: ["hh"])
    monkeypatch.setattr(manager, "stop", wedged_stop)

    before = next(p for p in client.get("/api/presets").json() if p["is_active"])["id"]
    second = client.post("/api/presets", json={"name": "Второй"}).json()["id"]
    refused = client.post(f"/api/presets/{second}/activate")
    assert refused.status_code == 409
    assert "не остановился" in refused.json()["detail"]
    still = next(p for p in client.get("/api/presets").json() if p["is_active"])["id"]
    assert still == before


def test_dictionaries_expose_the_codes_and_their_labels(client) -> None:
    """Подписи отдаёт бэкенд, а не дублирует фронтенд: иначе они разойдутся с
    константами при первой же правке, и пользователь увидит одно, а
    отправится другое."""
    body = client.get("/api/dictionaries").json()
    assert body["experience"]["noExperience"] == "Нет опыта"
    assert set(body["employment"]) == {
        "full", "part", "project", "probation", "volunteer",
    }
    assert set(body["schedule"]) == {"remote", "fullDay", "flexible", "shift"}
    assert body["area_id"] == 113
    assert body["multi"] == ["employment", "schedule"], (
        "фронтенд должен знать, что занятость и график — множественный выбор, "
        "а опыт — одиночный"
    )


def test_dictionaries_require_the_app_token(raw_client) -> None:
    assert raw_client.get("/api/dictionaries").status_code == 403


def test_config_no_longer_carries_criteria(client) -> None:
    body = client.get("/api/config").json()
    for gone in ("channels", "keywords", "exclude", "template", "file_path",
                 "hh_area_ids"):
        assert gone not in body, f"{gone} — критерий, он в /api/presets"
    assert "active_preset_id" in body
    assert "safe_mode" in body


def test_the_last_preset_is_protected_even_when_it_is_not_the_active_one(client) -> None:
    """Разделяет две проверки, которые иначе прикрывают друг друга.

    Мутация «снять запрет на удаление последнего» проходила зелёной: у
    единственного пресета срабатывал соседний запрет «нельзя удалить
    активный», и тест держался за чужой счёт. Состояние «единственный пресет
    не активен» достижимо: `active_preset_id` может указывать на пресет,
    которого уже нет, — например после ручной правки базы.
    """
    from job_monitor.db.connection import get_connection
    from job_monitor.settings import save_settings

    presets = client.get("/api/presets").json()
    assert len(presets) == 1
    only = presets[0]["id"]
    save_settings(get_connection(), {"active_preset_id": only + 999})

    refused = client.delete(f"/api/presets/{only}")
    assert refused.status_code == 400
    assert "единственный" in refused.json()["detail"]
    assert len(client.get("/api/presets").json()) == 1


def test_the_active_preset_cannot_be_switched_through_the_config_route(
    client, monkeypatch
) -> None:
    """Дыра, найденная финальным ревью: `PATCH /api/config` со значением
    `active_preset_id` обходил решение D9 целиком.

    `POST /api/presets/{id}/activate` сначала останавливает воркеров и
    отказывается переключаться, если воркер не уложился в бюджет. Смена поля
    напрямую оставляла воркер работающим, и его следующая итерация читала
    чужие критерии и прикладывала чужое резюме — то есть человек получал
    сообщение от одного пресета с резюме от другого.
    """
    from job_monitor.workers.manager import manager

    monkeypatch.setattr(manager, "running", lambda: ["tg"])

    second = client.post("/api/presets", json={"name": "Второй"}).json()["id"]
    before = next(p for p in client.get("/api/presets").json() if p["is_active"])["id"]

    refused = client.patch("/api/config", json={"active_preset_id": second})
    assert refused.status_code == 400
    assert "activate" in refused.json()["detail"]

    still = next(p for p in client.get("/api/presets").json() if p["is_active"])["id"]
    assert still == before, "активный пресет сменился в обход остановки воркеров"


# ── M-2: PATCH применяется целиком или никак ──────────────────────────


def test_a_rejected_patch_changes_nothing_at_all(client) -> None:
    """M-2: запрос с новым именем и негодным критерием возвращал 422, но имя
    уже было сохранено. Клиент видит отказ и не знает, что переименование
    прошло, — и следующий его запрос идёт к пресету, которого «нет»."""
    created = client.post("/api/presets", json={"name": "До правки"}).json()
    preset_id = created["id"]

    reply = client.patch(
        f"/api/presets/{preset_id}",
        json={"name": "После правки", "hh_experience": "такого кода нет"},
    )
    assert reply.status_code == 422

    assert client.get(f"/api/presets/{preset_id}").json()["name"] == "До правки"


def test_a_rejected_patch_does_not_move_the_preset_either(client) -> None:
    """Позиция — вторая половина той же проблемы: её тоже писали до
    валидации критериев."""
    created = client.post("/api/presets", json={"name": "Порядок"}).json()
    preset_id = created["id"]
    before = next(
        row["position"] for row in client.get("/api/presets").json()
        if row["id"] == preset_id
    )

    client.patch(
        f"/api/presets/{preset_id}",
        json={"position": before + 5, "hh_experience": "такого кода нет"},
    )

    after = next(
        row["position"] for row in client.get("/api/presets").json()
        if row["id"] == preset_id
    )
    assert after == before


def test_a_valid_patch_still_applies_everything(client) -> None:
    """Страховка от вакуумности: если бы отказ стал тотальным, эта
    проверка поймала бы и то, что перестало работать успешное сохранение."""
    created = client.post("/api/presets", json={"name": "Было"}).json()
    preset_id = created["id"]

    reply = client.patch(
        f"/api/presets/{preset_id}",
        json={"name": "Стало", "hh_experience": "between1And3"},
    )
    assert reply.status_code == 200

    preset = client.get(f"/api/presets/{preset_id}").json()
    assert preset["name"] == "Стало"
    assert preset["criteria"]["hh_experience"] == "between1And3"
