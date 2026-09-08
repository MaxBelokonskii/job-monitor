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

# Задача 7 плана 3 удалила standalone-скрипты `monitor.py` и `hh_monitor.py`
# (их код переехал в job_monitor/workers/). Два теста ниже раньше читали эти
# файлы поимённо и после удаления падали бы на FileNotFoundError, а сам
# инвариант стал бы вакуумным. Список файлов поэтому заменён на обход всех
# живых исходников: покрытие строго шире прежнего (новый модуль больше нельзя
# добавить в обход инварианта, просто не вписав его в список), плюс отдельно
# проверяется, что удалённые скрипты не вернулись.
REMOVED_SCRIPTS = ("monitor.py", "hh_monitor.py")
SCANNED_DIRS = ("api", "job_monitor", "frontend", "scripts")
SCANNED_SUFFIXES = {".py", ".js", ".html", ".css", ".bat", ".toml"}


def _live_sources(suffixes: set[str] = SCANNED_SUFFIXES) -> list[Path]:
    found: list[Path] = []
    for directory in SCANNED_DIRS:
        found.extend((REPO_ROOT / directory).rglob("*"))
    found.extend(REPO_ROOT.glob("*"))
    files = sorted(
        item
        for item in found
        if item.is_file()
        and item.suffix in suffixes
        and "__pycache__" not in item.parts
        and ".venv" not in item.parts
    )
    assert files, "не найдено ни одного исходника — репозиторий переехал?"
    return files


def test_standalone_monitor_scripts_are_gone() -> None:
    """Оба скрипта удалены и не должны вернуться: их логика теперь в
    job_monitor/workers/, а копия в корне репозитория означала бы второй,
    неподнадзорный путь исполнения — тот самый, из-за которого PID-файлы
    расходились с реальностью (L3)."""
    for name in REMOVED_SCRIPTS:
        assert not (REPO_ROOT / name).exists(), (
            f"{name} вернулся в репозиторий: воркеры живут в job_monitor/workers/"
        )


def test_no_state_paths_relative_to_repo():
    for source_file in _live_sources({".py"}):
        source = source_file.read_text(encoding="utf-8")
        assert not STATE_LITERALS.search(source), (
            f"{source_file.relative_to(REPO_ROOT)} всё ещё пишет состояние в репозиторий"
        )


# Собирается из частей, чтобы сам файл теста не содержал имя целиком и не
# срабатывал на себе при обходе дерева.
LEAKED_CV = "Пак_Виталий" + "_Владимирович.pdf"


def test_leaked_cv_filename_is_nowhere_in_the_sources() -> None:
    """monitor.py по умолчанию прикладывал резюме
    os.path.join(BASE_DIR, "<ФИО>.pdf") — CV прошлого автора, один из
    четырёх артефактов исходного утёкшего архива, разрешавшийся
    относительно каталога репозитория. Инвариант задачи 2 — никакое
    состояние не резолвится от BASE_DIR; это был единственный выживший
    случай, не попавший в список STATE_LITERALS. Сам monitor.py удалён, а
    значения по умолчанию у `file_path` больше нет вовсе
    (`job_monitor/settings.py`: `file_path: str = ""`), поэтому проверка
    из «нет в monitor.py» превращается в «нет нигде в исходниках»."""
    for source_file in _live_sources():
        source = source_file.read_text(encoding="utf-8", errors="ignore")
        assert LEAKED_CV not in source, (
            f"{source_file.relative_to(REPO_ROOT)} захардкодил имя утёкшего CV прошлого автора"
        )
