from __future__ import annotations

import stat

import pytest

from job_monitor import paths, resume_store
from job_monitor.db import connection

from urllib.parse import quote


def upload(client, name: str, data: bytes):
    """Загрузка сырым телом: имя — в заголовке, файл — телом запроса.

    Форма `multipart` не используется намеренно (см. докстринг
    `api/resumes_routes.py`): она требует отдельного разборщика
    недоверенного ввода ради одного поля.
    """
    return client.post(
        "/api/resumes", content=data, headers={"X-Filename": quote(name)}
    )


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Своя база и свой каталог данных на каждый тест — см. пояснение в
    `tests/test_presets_api.py`."""
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    connection.reset_connection()
    yield
    connection.reset_connection()


def test_upload_then_list_then_download(client) -> None:
    uploaded = upload(client, "Моё резюме.pdf", b"%PDF-1.4 payload")
    assert uploaded.status_code == 200
    resume_id = uploaded.json()["id"]

    listed = client.get("/api/resumes").json()
    assert [item["original_name"] for item in listed] == ["Моё резюме.pdf"]
    assert listed[0]["size_bytes"] == len(b"%PDF-1.4 payload")

    downloaded = client.get(f"/api/resumes/{resume_id}/download")
    assert downloaded.status_code == 200
    assert downloaded.content == b"%PDF-1.4 payload"


def test_uploaded_file_is_0600_inside_the_data_dir(client) -> None:
    upload(client, "cv.pdf", b"x")
    stored = list(paths.resume_dir().iterdir())
    assert len(stored) == 1
    assert stat.S_IMODE(stored[0].stat().st_mode) == 0o600


def test_resumes_require_the_app_token(raw_client) -> None:
    assert raw_client.get("/api/resumes").status_code == 403
    assert upload(raw_client, "cv.pdf", b"x").status_code == 403


def test_a_hostile_filename_does_not_escape_the_data_dir(client) -> None:
    """Имя от клиента не участвует в построении пути — ни как есть, ни после
    «очистки». Файл с таким именем не должен появиться нигде."""
    response = upload(client, "../../.env", b"TG_API_HASH=stolen")
    assert response.status_code in (400, 422)
    assert list(paths.resume_dir().iterdir()) == []
    assert not (paths.data_dir() / ".env").exists(), (
        "имя ../../.env создало файл в каталоге данных"
    )


def test_extension_outside_the_allowlist_is_refused(client) -> None:
    response = upload(client, "payload.exe", b"MZ")
    assert response.status_code == 400
    assert list(paths.resume_dir().iterdir()) == []


def test_oversized_upload_is_refused(client) -> None:
    response = upload(client, "cv.pdf", b"x" * (resume_store.MAX_BYTES + 1))
    assert response.status_code == 400
    assert list(paths.resume_dir().iterdir()) == []


def test_original_name_is_returned_verbatim_and_never_used_as_a_path(client) -> None:
    hostile_but_valid = "резюме <script>alert(1)</script>.pdf"
    uploaded = upload(client, hostile_but_valid, b"x")
    assert uploaded.status_code == 200
    listed = client.get("/api/resumes").json()
    assert listed[0]["original_name"] == hostile_but_valid
    # Экранирование — забота фронтенда (там ноль innerHTML), а на диске такого
    # имени нет вовсе.
    assert not any("script" in item.name for item in paths.resume_dir().iterdir())


def test_delete_removes_the_row_and_the_file(client) -> None:
    resume_id = upload(client, "cv.pdf", b"x").json()["id"]
    assert client.delete(f"/api/resumes/{resume_id}").status_code == 200
    assert client.get("/api/resumes").json() == []
    assert list(paths.resume_dir().iterdir()) == []


def test_delete_is_refused_while_a_preset_still_uses_the_resume(client) -> None:
    """Молчаливая поломка чужого пресета хуже отказа: ответ обязан назвать,
    кто ссылается."""
    resume_id = upload(client, "cv.pdf", b"x").json()["id"]
    preset_id = client.post("/api/presets", json={"name": "QA"}).json()["id"]
    client.patch(f"/api/presets/{preset_id}", json={"resume_id": resume_id})

    refused = client.delete(f"/api/resumes/{resume_id}")
    assert refused.status_code == 400
    assert "QA" in refused.json()["detail"]
    assert len(client.get("/api/resumes").json()) == 1
    assert len(list(paths.resume_dir().iterdir())) == 1, "файл тоже должен уцелеть"


def test_download_of_a_missing_resume_is_404(client) -> None:
    assert client.get("/api/resumes/999/download").status_code == 404


def test_download_reports_a_file_that_vanished_from_disk(client) -> None:
    """Запись в базе есть, файла нет — пользователь удалил его руками. Это
    404 с внятным текстом, а не 500 и не пустой ответ."""
    resume_id = upload(client, "cv.pdf", b"x").json()["id"]
    for item in paths.resume_dir().iterdir():
        item.unlink()

    response = client.get(f"/api/resumes/{resume_id}/download")
    assert response.status_code == 404
    assert "пропал" in response.json()["detail"]


def test_a_preset_can_point_at_an_uploaded_resume(client) -> None:
    resume_id = upload(client, "cv.pdf", b"x").json()["id"]
    active = next(p for p in client.get("/api/presets").json() if p["is_active"])["id"]
    assert client.patch(
        f"/api/presets/{active}", json={"resume_id": resume_id}
    ).status_code == 200
    assert client.get(f"/api/presets/{active}").json()["criteria"]["resume_id"] == resume_id


# ── M-3: файл и строка появляются и исчезают вместе ───────────────────


def test_a_failed_insert_leaves_no_orphan_file(client, monkeypatch) -> None:
    """M-3: файл писался до вставки строки. Отказ вставки оставлял его в
    каталоге навсегда — в списке его нет, удалить из интерфейса нечем."""
    import sqlite3

    from job_monitor import paths
    from job_monitor.db import repositories

    def boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repositories.ResumesRepo, "add", boom)

    before = set(paths.resume_dir().iterdir())
    with pytest.raises(sqlite3.OperationalError):
        client.post(
            "/api/resumes", content=b"%PDF-1.4 fake", headers={"X-Filename": "cv.pdf"}
        )

    assert set(paths.resume_dir().iterdir()) == before, (
        "файл остался в каталоге, а строки в базе нет — удалить его из "
        "интерфейса невозможно"
    )


def test_a_failed_row_delete_keeps_the_file(client, monkeypatch) -> None:
    """Зеркальная половина: файл стирался ДО строки, поэтому отказ на
    удалении строки оставлял запись, указывающую в пустоту."""
    import sqlite3

    from job_monitor import resume_store
    from job_monitor.db import repositories

    created = client.post(
        "/api/resumes", content=b"%PDF-1.4 fake", headers={"X-Filename": "cv.pdf"}
    ).json()

    def boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repositories.ResumesRepo, "delete", boom)
    with pytest.raises(sqlite3.OperationalError):
        client.delete(f"/api/resumes/{created['id']}")

    row = next(
        item for item in client.get("/api/resumes").json() if item["id"] == created["id"]
    )
    assert resume_store.path_of(row["stored_name"]).exists(), (
        "строка осталась, а файл стёрт — скачивание такой записи даёт 404 "
        "без единого способа починить"
    )
