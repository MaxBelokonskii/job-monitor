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


# ── Замечания ревью подпроекта 1 ──────────────────────────────────────


def test_a_name_must_be_a_string(client) -> None:
    """M-12. `{"name": 123}` принимался и сохранялся строкой `"123"`.

    Молчаливое приведение типа — худший исход из трёх возможных: клиент
    не узнаёт, что отправил не то, а в базе оказывается значение, которого
    он не вводил. Отказ честнее.
    """
    created = client.post("/api/presets", json={"name": "Целое имя"}).json()
    reply = client.patch(f"/api/presets/{created['id']}", json={"name": 123})
    assert reply.status_code == 400
    assert client.get(f"/api/presets/{created['id']}").json()["name"] == "Целое имя"


def test_a_name_must_be_a_string_on_creation_too(client) -> None:
    """Та же гарантия на другом конце: создание не должно быть лазейкой.

    Её даёт pydantic 2, а не наш код — в отличие от первой версии он не
    приводит `int` к `str`. Проверка остаётся, потому что закрепляет
    свойство, а не реализацию: перейдём однажды на сырой словарь, как в
    `patch_preset`, — и лазейка откроется молча.
    """
    assert client.post("/api/presets", json={"name": 123}).status_code == 422


def test_the_summary_names_the_resume_it_uses(client) -> None:
    """M-7. Сводка отдавала `resume_id` — число, по которому интерфейсу
    приходилось идти в библиотеку, чтобы показать «какое резюме». Список
    пресетов и список резюме грузятся независимо, так что до второго
    ответа чип пресета показывал бы число или пустоту."""
    # Имя едет в процентном кодировании: заголовки HTTP латиницей, а имя
    # файла может быть каким угодно (так же его шлёт и фронтенд).
    from urllib.parse import quote

    resume = client.post(
        "/api/resumes", content=b"%PDF-1.4 x",
        headers={"X-Filename": quote("Моё резюме.pdf")},
    ).json()
    created = client.post("/api/presets", json={"name": "С резюме"}).json()
    client.patch(f"/api/presets/{created['id']}", json={"resume_id": resume["id"]})

    row = next(p for p in client.get("/api/presets").json() if p["id"] == created["id"])
    assert row["resume_name"] == "Моё резюме.pdf"


def test_a_preset_without_a_resume_says_so(client) -> None:
    """Обратная сторона: отсутствие вложения — это `None`, а не пустая
    строка и не выдуманное имя."""
    created = client.post("/api/presets", json={"name": "Без резюме"}).json()
    row = next(p for p in client.get("/api/presets").json() if p["id"] == created["id"])
    assert row["resume_id"] is None
    assert row["resume_name"] is None


def test_a_preset_pointing_at_a_deleted_resume_does_not_break_the_list(client) -> None:
    """Ссылка на резюме живёт внутри JSON-документа критериев, а не
    внешним ключом: рассогласование возможно, и список пресетов не должен
    из-за него падать."""
    created = client.post("/api/presets", json={"name": "Битая ссылка"}).json()
    client.patch(f"/api/presets/{created['id']}", json={"resume_id": 999999})

    row = next(p for p in client.get("/api/presets").json() if p["id"] == created["id"])
    assert row["resume_id"] == 999999
    assert row["resume_name"] is None


def test_a_worker_that_died_on_its_own_does_not_break_activation(client, monkeypatch) -> None:
    """M-5. `activate_preset` спрашивает `manager.running()`, потом зовёт
    `stop()` для каждого. Воркер, упавший сам между этими двумя вызовами,
    даёт `WorkerNotRunning` — и активация отвечала 500.

    Но «воркер уже остановился» — это ровно то, чего мы добивались:
    останавливать нечего, можно переключаться.
    """
    from job_monitor.workers import manager as manager_module
    from job_monitor.workers.manager import WorkerNotRunning, manager

    created = client.post("/api/presets", json={"name": "Гонка"}).json()

    monkeypatch.setattr(manager, "running", lambda: ["hh"])

    async def already_gone(name):
        raise WorkerNotRunning(name)

    monkeypatch.setattr(manager, "stop", already_gone)

    reply = client.post(f"/api/presets/{created['id']}/activate")
    assert reply.status_code == 200, f"гонка дала {reply.status_code}: {reply.text}"
    assert reply.json()["activated"] == created["id"]
    assert reply.json()["stopped"] == [], (
        "воркер, умерший сам, не был остановлен нами — в списке ему не место"
    )


def test_a_refused_activation_says_what_was_already_stopped(client, monkeypatch) -> None:
    """M-6. На 409 список уже остановленных воркеров терялся. Человек
    видел «переключить не удалось» и не знал, что Telegram при этом встал:
    возвращался к экрану, где один воркер работает, другой нет, и
    объяснения этому нет нигде."""
    from job_monitor.workers.manager import WorkerState, manager

    created = client.post("/api/presets", json={"name": "Полуостановка"}).json()

    class Status:
        def __init__(self, state, error=None):
            self.state, self.last_error = state, error

    monkeypatch.setattr(manager, "running", lambda: ["tg", "hh"])

    async def stop(name):
        if name == "tg":
            return Status(WorkerState.stopped)
        return Status(WorkerState.error, "таймаут остановки")

    monkeypatch.setattr(manager, "stop", stop)

    reply = client.post(f"/api/presets/{created['id']}/activate")
    assert reply.status_code == 409
    detail = reply.json()["detail"]
    assert "hh" in detail and "таймаут" in detail
    assert "tg" in detail, (
        "не сказано, что Telegram уже остановлен — человек не поймёт, "
        f"почему один воркер стоит: {detail!r}"
    )
