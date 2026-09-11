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
    for name in ("payload.exe", "script.sh", "page.html", "noextension", "cv.txt"):
        with pytest.raises(resume_store.ResumeRejected):
            resume_store.store(name, b"x")
    # `.txt` намеренно не в списке: заблокировать `*.txt` в хуке защиты от
    # коммита нельзя (в репозитории есть законные текстовые файлы), и резюме
    # в таком формате стало бы единственным, которое хук не поймает.
    assert resume_store.ALLOWED_EXTENSIONS == frozenset(
        {".pdf", ".doc", ".docx", ".rtf", ".odt"}
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


# ── M-4: тело не держится в памяти целиком ────────────────────────────


def test_reception_stops_at_the_first_chunk_over_the_limit() -> None:
    """Предел проверяется по ходу приёма, а не после него.

    Раньше роут делал `await request.body()`: десять мегабайт оказывались
    в памяти процесса целиком, и только потом отвергались. Теперь кусок,
    на котором сумма перевалила за предел, — последний, что мы вообще
    прочитали.
    """
    chunk = b"x" * (1024 * 1024)
    written = 0
    with pytest.raises(resume_store.ResumeRejected, match="больше"):
        with resume_store.receiving("cv.pdf") as incoming:
            for _ in range(20):          # двадцать мегабайт при пределе в десять
                incoming.write(chunk)
                written += 1
    assert written == 10, (
        f"прочитано {written} МБ вместо 10 — приём не останавливается на пределе"
    )


def test_an_aborted_reception_leaves_nothing_behind() -> None:
    """Временный файл убирается и при отказе: иначе каталог данных копил
    бы недогруженные обрывки, невидимые ни в одном списке."""
    with pytest.raises(resume_store.ResumeRejected):
        with resume_store.receiving("cv.pdf") as incoming:
            incoming.write(b"x" * (11 * 1024 * 1024))
    assert list(paths.resume_dir().iterdir()) == []


def test_the_extension_is_checked_before_a_single_byte_is_read() -> None:
    """Иначе отказ по расширению стоил бы чтения всего тела — и клиенту
    хватило бы неподходящего имени, чтобы заставить приложение принять
    десять мегабайт впустую."""
    with pytest.raises(resume_store.ResumeRejected, match="расширение"):
        with resume_store.receiving("cv.zip"):
            pytest.fail("приём открылся для неподдерживаемого расширения")


def test_nothing_is_visible_until_commit() -> None:
    """До `commit()` файл лежит под временным именем: наружу он появляется
    одним `os.replace`, то есть либо целиком, либо никак."""
    with resume_store.receiving("cv.pdf") as incoming:
        incoming.write(b"%PDF-1.4 x")
        visible = [p.name for p in paths.resume_dir().iterdir()
                   if not p.name.startswith(".")]
        assert visible == [], f"недописанный файл уже виден: {visible}"
        stored_name, size = incoming.commit()
    assert size == len(b"%PDF-1.4 x")
    assert resume_store.path_of(stored_name).exists()
