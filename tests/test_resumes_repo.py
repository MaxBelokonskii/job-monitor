from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from job_monitor.db.connection import connect
from job_monitor.db.repositories import PresetsRepo, ResumesRepo

NOW = datetime(2026, 9, 9, 12, 0, 0)


@pytest.fixture()
def conn(tmp_path) -> sqlite3.Connection:
    # Соединение приложения, а не сырое `sqlite3.connect`: `transaction()`
    # выдаёт явный BEGIN IMMEDIATE и требует `isolation_level=None`, иначе
    # получается «cannot start a transaction within a transaction».
    return connect(str(tmp_path / "t.db"))


def test_added_resume_reads_back(conn) -> None:
    repo = ResumesRepo(conn)
    resume_id = repo.add("Моё резюме.pdf", "a1b2.pdf", 1024, NOW)
    stored = repo.get(resume_id)
    assert stored["original_name"] == "Моё резюме.pdf"
    assert stored["stored_name"] == "a1b2.pdf"
    assert stored["size_bytes"] == 1024


def test_missing_resume_reads_back_as_none(conn) -> None:
    assert ResumesRepo(conn).get(999) is None


def test_presets_using_names_every_referring_preset(conn) -> None:
    """Удаление файла, на который ссылается пресет, сломало бы его молча —
    поэтому репозиторий обязан уметь назвать всех, кто ссылается."""
    resume_id = ResumesRepo(conn).add("cv.pdf", "a1b2.pdf", 10, NOW)
    presets = PresetsRepo(conn)
    presets.create("QA", {"resume_id": resume_id}, NOW)
    presets.create("Поддержка", {"resume_id": resume_id}, NOW)
    presets.create("Без резюме", {"resume_id": None}, NOW)

    assert sorted(ResumesRepo(conn).presets_using(resume_id)) == ["QA", "Поддержка"]


def test_presets_using_is_empty_for_an_unreferenced_resume(conn) -> None:
    resume_id = ResumesRepo(conn).add("cv.pdf", "a1b2.pdf", 10, NOW)
    PresetsRepo(conn).create("QA", {"resume_id": None}, NOW)
    assert ResumesRepo(conn).presets_using(resume_id) == []


def test_presets_using_does_not_confuse_neighbouring_ids(conn) -> None:
    """`resume_id` лежит внутри JSON-документа, и сравнение по подстроке
    спутало бы 1 с 11. Сравнение должно быть по значению."""
    repo = ResumesRepo(conn)
    first = repo.add("a.pdf", "a.pdf", 1, NOW)
    for _ in range(9):
        repo.add("x.pdf", f"x{_}.pdf", 1, NOW)
    eleventh = repo.add("k.pdf", "k.pdf", 1, NOW)
    PresetsRepo(conn).create("Одиннадцатый", {"resume_id": eleventh}, NOW)

    assert repo.presets_using(eleventh) == ["Одиннадцатый"]
    assert repo.presets_using(first) == []


def test_delete_removes_the_row(conn) -> None:
    repo = ResumesRepo(conn)
    resume_id = repo.add("cv.pdf", "a1b2.pdf", 10, NOW)
    repo.delete(resume_id)
    assert repo.get(resume_id) is None
    assert repo.list() == []
