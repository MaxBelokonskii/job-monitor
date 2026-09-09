"""Файловый слой библиотеки резюме.

Загрузка файла — новая поверхность атаки, и главное её свойство: имя от
клиента НИКОГДА не становится путём. Имя на диске генерируем мы, клиентское
храним отдельно и только для показа.
"""

from __future__ import annotations

import os
import stat

import pytest

from job_monitor import paths, resume_store


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    return tmp_path


def test_stored_file_lands_in_the_resume_dir_with_0600() -> None:
    stored_name, size = resume_store.store("Моё резюме.pdf", b"%PDF-1.4 ...")
    target = resume_store.path_of(stored_name)
    assert target.parent == paths.resume_dir().resolve()
    assert target.exists()
    assert size == len(b"%PDF-1.4 ...")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600, "резюме — личный документ"


def test_the_client_filename_never_becomes_the_path() -> None:
    """Имя на диске генерируем мы. Клиентское имя может быть чем угодно, в
    том числе попыткой выйти из каталога."""
    stored_name, _ = resume_store.store("Моё резюме.pdf", b"x")
    assert "резюме" not in stored_name
    assert stored_name.endswith(".pdf")


@pytest.mark.parametrize(
    "hostile",
    [
        "../../.env",
        "../../../etc/passwd",
        "/etc/passwd",
        "..\\..\\windows\\system32\\config",
        "резюме\x00.pdf",
        "резюме\n.pdf",
    ],
)
def test_hostile_names_cannot_escape_the_resume_dir(hostile: str) -> None:
    try:
        stored_name, _ = resume_store.store(hostile, b"x")
    except resume_store.ResumeRejected:
        return
    target = resume_store.path_of(stored_name)
    assert target.parent == paths.resume_dir().resolve(), (
        f"имя {hostile!r} вывело файл за пределы каталога резюме: {target}"
    )


def test_extension_outside_the_allowlist_is_rejected() -> None:
    for name in ("payload.exe", "script.sh", "page.html", "noextension"):
        with pytest.raises(resume_store.ResumeRejected):
            resume_store.store(name, b"x")
    assert resume_store.ALLOWED_EXTENSIONS == frozenset(
        {".pdf", ".doc", ".docx", ".rtf", ".odt", ".txt"}
    )


def test_oversized_file_is_rejected() -> None:
    with pytest.raises(resume_store.ResumeRejected):
        resume_store.store("cv.pdf", b"x" * (resume_store.MAX_BYTES + 1))
    assert resume_store.MAX_BYTES == 10 * 1024 * 1024


def test_empty_file_is_rejected() -> None:
    with pytest.raises(resume_store.ResumeRejected):
        resume_store.store("cv.pdf", b"")


def test_two_uploads_of_the_same_name_do_not_collide() -> None:
    first, _ = resume_store.store("cv.pdf", b"a")
    second, _ = resume_store.store("cv.pdf", b"b")
    assert first != second
    assert resume_store.path_of(first).read_bytes() == b"a"
    assert resume_store.path_of(second).read_bytes() == b"b"


def test_path_of_refuses_a_name_that_points_outside() -> None:
    with pytest.raises(resume_store.ResumeRejected):
        resume_store.path_of("../.env")


def test_remove_deletes_the_file_and_tolerates_a_missing_one() -> None:
    stored_name, _ = resume_store.store("cv.pdf", b"x")
    resume_store.remove(stored_name)
    assert not resume_store.path_of(stored_name).exists()
    resume_store.remove(stored_name)  # повторное удаление — не ошибка


def test_no_temporary_file_survives_a_failed_write(monkeypatch) -> None:
    """Запись атомарна: временный файл в том же каталоге, затем `os.replace`.
    Половинного файла на диске не остаётся ни при какой ошибке."""
    def boom(*args, **kwargs):
        raise OSError("диск кончился")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        resume_store.store("cv.pdf", b"x")
    assert list(paths.resume_dir().iterdir()) == [], "остался временный файл"
