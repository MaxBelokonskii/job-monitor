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
    r"session_web|hh_cookies\.json|hh_sent\.json|all_sent_users\.txt|logs)"
)


def test_no_state_paths_relative_to_repo():
    for name in ("monitor.py", "hh_monitor.py", "api/config_routes.py",
                 "api/tg_routes.py", "api/hh_routes.py", "api/auth_routes.py"):
        source = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert not STATE_LITERALS.search(source), f"{name} всё ещё пишет состояние в репозиторий"
