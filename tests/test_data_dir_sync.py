"""Каталог данных не должен уезжать в чужое облако.

Права `0600` защищают от другого пользователя машины, но не от бэкапа: файл
сессии Telethon содержит `auth_key`, которого достаточно для входа в аккаунт
в обход 2FA, и попадание каталога в синхронизируемую папку сводит права на
нет (решение D13).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from job_monitor import paths


@pytest.mark.parametrize(
    "candidate, expected",
    [
        ("/Users/kim/Library/Mobile Documents/com~apple~CloudDocs/jm", "iCloud Drive"),
        ("/Users/kim/Dropbox/jm", "Dropbox"),
        ("/Users/kim/Google Drive/jm", "Google Drive"),
        ("/Users/kim/OneDrive/jm", "OneDrive"),
        ("/Users/kim/Yandex.Disk/jm", "Яндекс.Диск"),
        ("/Users/kim/.job-monitor", None),
        ("/tmp/jm", None),
        ("/Users/kim/Documents/dropbox-notes/jm", None),
    ],
)
def test_synced_locations_are_recognised(candidate: str, expected: str | None) -> None:
    assert paths.looks_synced(Path(candidate)) == expected


def test_the_marker_matches_a_whole_path_segment_only() -> None:
    """`Dropbox` в имени файла или внутри другого слова — не облако.

    Ложное срабатывание здесь дороже пропуска: пользователь увидит
    предупреждение, которого не заслужил, перестанет ему верить и пропустит
    настоящее.
    """
    assert paths.looks_synced(Path("/Users/kim/MyDropboxBackups/jm")) is None
    assert paths.looks_synced(Path("/Users/kim/notes/Dropbox.md")) is None


def test_exclusion_is_best_effort_and_never_raises(tmp_path, monkeypatch, real_exclude_from_backups) -> None:
    """Ужесточение защиты — best-effort, а не условие запуска: это уже
    установленное в проекте правило (`paths.tighten`). Отсутствие `tmutil`
    или отказ прав не должны ронять приложение."""
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(paths.shutil, "which", lambda _name: None)
    assert real_exclude_from_backups() is False


def test_exclusion_survives_a_failing_tmutil(tmp_path, monkeypatch, real_exclude_from_backups) -> None:
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    monkeypatch.setattr(paths.shutil, "which", lambda _name: "/usr/bin/tmutil")

    def boom(*_args, **_kwargs):
        raise OSError("нет прав")

    monkeypatch.setattr(paths.subprocess, "run", boom)
    assert real_exclude_from_backups() is False


def test_exclusion_calls_tmutil_without_a_shell(tmp_path, monkeypatch, real_exclude_from_backups) -> None:
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    monkeypatch.setattr(paths.shutil, "which", lambda _name: "/usr/bin/tmutil")
    calls: list[tuple[list[str], dict]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(paths.subprocess, "run", fake_run)
    assert real_exclude_from_backups() is True

    command, kwargs = calls[0]
    assert command == ["/usr/bin/tmutil", "addexclusion", str(tmp_path)]
    assert kwargs.get("shell") in (None, False), (
        "shell=True запрещён глобальным ограничением: аргументы передаются списком"
    )


def test_state_reports_a_synced_data_dir(client, monkeypatch) -> None:
    """Предупреждение нужно именно тогда, когда пользователь сам задал
    каталог переменной окружения — в интерфейсе, а не в логе."""
    import api.routes_state as routes_state

    monkeypatch.setattr(routes_state.paths, "looks_synced", lambda: "Dropbox")
    assert client.get("/api/state").json()["data_dir_warning"] == "Dropbox"


def test_state_has_no_warning_for_a_normal_data_dir(client) -> None:
    assert client.get("/api/state").json()["data_dir_warning"] is None


def test_the_exclusion_runs_once_and_not_on_every_start(
    tmp_path, monkeypatch, real_exclude_from_backups
) -> None:
    """`tmutil` — сторонний процесс с бюджетом в десять секунд.

    Звать его на каждом старте значит поставить запуск приложения в
    зависимость от чужой утилиты; на этой самой машине прогон набора из-за
    такого вызова вырос с 16 до 86 секунд, пока отметки не было.
    """
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    monkeypatch.setattr(paths.shutil, "which", lambda _name: "/usr/bin/tmutil")
    runs: list[list[str]] = []

    def fake_run(command, **_kwargs):
        runs.append(command)
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(paths.subprocess, "run", fake_run)

    assert real_exclude_from_backups() is True
    assert real_exclude_from_backups() is True
    assert real_exclude_from_backups() is True
    assert len(runs) == 1, f"tmutil позван {len(runs)} раз(а) вместо одного"
    assert (tmp_path / paths.EXCLUSION_MARKER).exists()


def test_a_failed_exclusion_leaves_no_marker(
    tmp_path, monkeypatch, real_exclude_from_backups
) -> None:
    """Иначе одна неудачная попытка навсегда убедила бы приложение, что
    каталог исключён."""
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    monkeypatch.setattr(paths.shutil, "which", lambda _name: "/usr/bin/tmutil")
    monkeypatch.setattr(
        paths.subprocess, "run",
        lambda *_a, **_k: type("R", (), {"returncode": 1})(),
    )

    assert real_exclude_from_backups() is False
    assert not (tmp_path / paths.EXCLUSION_MARKER).exists()
