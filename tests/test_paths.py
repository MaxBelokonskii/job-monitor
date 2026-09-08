import re
import stat
from pathlib import Path

from job_monitor import paths

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_data_dir_follows_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path / "state"))
    assert paths.data_dir() == tmp_path / "state"


def test_data_dir_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path / "state"))
    mode = stat.S_IMODE(paths.data_dir().stat().st_mode)
    assert mode == 0o700


def test_default_is_outside_repository(monkeypatch):
    monkeypatch.delenv("JOB_MONITOR_DATA_DIR", raising=False)
    for factory in (paths.env_file, paths.db_file, paths.tg_session, paths.hh_cookies):
        assert REPO_ROOT not in factory().parents, f"{factory.__name__} внутри репозитория"


def test_nested_path_creates_parent(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path / "state"))
    target = paths.path("logs", "hh.log")
    assert target.parent.is_dir()


STATE_LITERALS = re.compile(
    r"os\.path\.join\(\s*BASE_DIR\s*,\s*[\"'](?:\.env|config\.json|session|"
    r"session_web|hh_cookies\.json|hh_sent\.json|all_sent_users\.txt|logs|"
    r"resume\.pdf)"
)


def test_no_state_paths_relative_to_repo():
    for name in ("monitor.py", "hh_monitor.py", "api/config_routes.py",
                 "api/tg_routes.py", "api/hh_routes.py", "api/auth_routes.py"):
        source = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert not STATE_LITERALS.search(source), f"{name} всё ещё пишет состояние в репозиторий"


def test_monitor_default_resume_path_is_not_the_leaked_cv() -> None:
    """monitor.py used to default the résumé attachment to
    os.path.join(BASE_DIR, "Пак_Виталий_Владимирович.pdf") — the previous
    author's CV, one of the four artifacts in the original leaked archive,
    resolved against the repository directory. Task 2's invariant was that
    no runtime state resolves against BASE_DIR; this was the one survivor,
    missed because STATE_LITERALS' literal list didn't include the
    filename."""
    source = (REPO_ROOT / "monitor.py").read_text(encoding="utf-8")
    assert "Пак_Виталий_Владимирович.pdf" not in source, (
        "monitor.py still hardcodes the previous author's leaked CV filename"
    )
    assert 'paths.path("resume.pdf")' in source, (
        "monitor.py should default the résumé path via job_monitor.paths, not BASE_DIR"
    )
