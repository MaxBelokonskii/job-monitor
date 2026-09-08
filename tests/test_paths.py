import ast
import os
import stat
import subprocess
import sys
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
    # Вопрос теста — «куда указывает путь», а не «создался ли каталог». Без
    # заглушки он был единственным местом, где прогон создавал `~/.job-monitor`
    # в НАСТОЯЩЕМ домашнем каталоге — просто чтобы посмотреть на путь:
    # `paths.path()` делает mkdir по дороге. Проверено `HOME=<tmp> pytest -q`.
    monkeypatch.setattr(Path, "mkdir", lambda *_args, **_kwargs: None)
    for factory in (paths.env_file, paths.db_file, paths.tg_session, paths.hh_cookies):
        assert REPO_ROOT not in factory().parents, f"{factory.__name__} внутри репозитория"


def test_nested_path_creates_parent(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path / "state"))
    target = paths.path("logs", "hh.log")
    assert target.parent.is_dir()


# ── Импорт не создаёт состояние ───────────────────────────────────────


IMPORT_PROBE = """
import os, sys
from pathlib import Path

home = Path(os.environ["HOME"])
before = {p for p in home.rglob("*")}
import api.main            # noqa: F401 — важен сам факт импорта
after = {p for p in home.rglob("*")}
created = sorted(str(p.relative_to(home)) for p in after - before)
print(repr(created))
"""


def test_importing_the_app_creates_nothing_on_disk(tmp_path):
    """Импорт модулей не должен создавать каталог данных.

    `job_monitor/workers/hh.py` собирал синглтон `login` с
    `cookies_path=paths.hh_cookies()` — вызовом НА ИМПОРТЕ, а
    `paths.path()` делает `mkdir`. Проверено:
    `HOME=/tmp/hometest pytest -q` создавал `/tmp/hometest/.job-monitor`.
    Autouse-фикстура tests/conftest.py ставит `JOB_MONITOR_DATA_DIR` в
    setup первого теста, то есть уже после импортов на коллекции, — её
    докстринг обещал герметичность, которой не было.

    Каталог оставался пустым и 0700, так что утечки не было; настоящая цена
    — замороженный на момент импорта путь (см. тест-близнец в
    tests/test_hh_login.py).
    """
    home = tmp_path / "home"
    home.mkdir()
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("HOME", "JOB_MONITOR_DATA_DIR")
    }
    environment["HOME"] = str(home)
    environment["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", IMPORT_PROBE],
        capture_output=True, text=True, env=environment, cwd=str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    created = result.stdout.strip().splitlines()[-1]
    assert created == "[]", (
        f"импорт api.main создал в HOME: {created} — состояние должно появляться"
        " только когда его действительно просят записать"
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


# ── Ни одно состояние не резолвится мимо job_monitor/paths.py ─────────
#
# Инвариант, охраняющий первопричину S1/S8: в исходном репозитории был
# закоммичен архив с `.env`, двумя файлами сессии Telethon, логами и резюме
# автора — всё это писалось рядом с кодом, потому что пути к состоянию
# считались от каталога репозитория.
#
# Прежняя проверка была регулярным выражением по одной синтаксической форме:
#     os\.path\.join\(\s*BASE_DIR\s*,\s*["'](?:\.env|config\.json|session|…)
# Проверено подстановкой: `os.path.join(BASE_DIR, "telegram.session")` — не
# матчится (список имён остался до-переименования), `Path(__file__).parent /
# ".env"` — не матчится, `open("hh_cookies.json", "w")` — не матчится. К тому
# же `BASE_DIR` во всём дереве определён ровно один раз (api/main.py) и
# используется только для FRONTEND_DIR, так что искомый шаблон в 26
# сканируемых файлах появиться не мог в принципе: проверка была вакуумной.
#
# Ниже проверяется СВОЙСТВО: любое выражение, строящее путь к файлу
# состояния, должно начинаться либо от `job_monitor.paths`, либо от значения,
# пришедшего снаружи (параметр функции — так работает `migrate-legacy`,
# которому каталог называет пользователь). Корень, привязанный к репозиторию
# (`__file__`, `BASE_DIR` и всё, что из них выведено, `os.getcwd()`,
# `Path.cwd()`) или к текущему каталогу процесса (голый относительный
# литерал), — нарушение. Плюс любая ЗАПИСЬ по такому корню, даже под именем,
# которого ещё нет в списке: новый файл состояния не должен уметь появиться
# в обход инварианта. То, что проверка ловит все эти формы, само по себе
# закреплено тестом ниже (`test_the_state_path_check_is_not_vacuous`).
#
# Ревью первой версии сканера прогнало через него 20 форм и нашло шесть
# щелей: `logging.FileHandler`, `os.makedirs`, `shutil.copy`, `shutil.rmtree`,
# `tempfile.mkstemp(dir=…)` и `sys.argv[0]` как корень. Важна была первая —
# именно так проект и пишет логи, а `logs` давно стоит в STATE_MARKERS.
# Все шесть добавлены в MUTATIONS ниже; ALLOWED заодно расширен близкими
# законными формами (`d.copy()`, `str.replace`, `mkstemp(dir=…)` рядом с
# целью), чтобы расширение списка имён не купило покрытие ложными
# срабатываниями.

STATE_MARKERS = (
    ".env", "config.json", ".db", ".session", "session", "cookies",
    "hh_sent.json", "all_sent_users.txt", "sent_log_", ".pdf", ".log", "logs",
    "telegram", "resume",
)
CWD_CALLS = {"getcwd", "cwd"}
PATH_BUILDERS = {"join", "abspath", "dirname", "expanduser", "realpath"}
PATH_OPENERS = {"open", "connect", "TelegramClient"}
WRITE_METHODS = {"write_text", "write_bytes", "mkdir", "touch", "chmod", "unlink", "replace"}
# Вызовы, у которых путь — ПЕРВЫЙ позиционный аргумент, а сам вызов создаёт
# или переписывает файл/каталог. Имена здесь однозначные: `d.copy()` или
# `text.replace(a, b)` под них не попадают, поэтому ложных срабатываний на
# обычном коде они не дают. Ради `logging.FileHandler` список и заведён:
# проект пишет логи именно так, а `logs` уже стоит в STATE_MARKERS.
WRITE_CALLS = {
    "makedirs", "rmtree", "copytree", "copyfile",
    "FileHandler", "WatchedFileHandler", "RotatingFileHandler",
    "TimedRotatingFileHandler",
}
# `shutil.copy(src, dst)` и `shutil.move(src, dst)`: путь и в первом, и во
# втором аргументе. Требуется именно `shutil.`, потому что голые `copy` и
# `move` — слишком частые имена методов (`dict.copy`, `deque.move`).
COPY_MODULE = "shutil"
COPY_CALLS = {"copy", "copy2", "copyfile", "copytree", "move"}
# Путь, переданный именованным аргументом: `tempfile.mkstemp(dir=…)`,
# `logging.basicConfig(filename=…)`, `NamedTemporaryFile(dir=…)`. Режим
# оставлен читающим: сюда попадает и безобидное `open(file=…)`, поэтому
# нарушением такой путь становится, только если он ведёт к состоянию
# (STATE_MARKERS) — этого хватает, чтобы `dir="logs"` не прошёл.
PATH_KEYWORDS = {"dir", "filename", "path", "file"}
PATHS_MODULE_EXEMPT = "paths.py"


class _StatePathScan:
    """Ищет пути к состоянию, чей корень не идёт через job_monitor.paths."""

    def __init__(self, source: str, label: str) -> None:
        self.source = source
        self.label = label
        self.tree = ast.parse(source)
        self.repo_anchored_names: set[str] = set()
        self._collect_repo_anchored_names()

    def _is_repo_anchored(self, node: ast.AST) -> bool:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and (sub.id == "__file__" or sub.id in self.repo_anchored_names):
                return True
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr in CWD_CALLS:
                return True
            # `sys.argv[0]` — путь к запущенному скрипту, то есть тот же
            # корень «рядом с кодом», что и `__file__`, только через чёрный ход.
            if (
                isinstance(sub, ast.Attribute)
                and sub.attr == "argv"
                and isinstance(sub.value, ast.Name)
                and sub.value.id == "sys"
            ):
                return True
        return False

    def _collect_repo_anchored_names(self) -> None:
        """`BASE_DIR = os.path.dirname(__file__)` заражает и BASE_DIR, и всё,
        что из него собрано (FRONTEND_DIR = os.path.join(BASE_DIR, …))."""
        changed = True
        while changed:
            changed = False
            for statement in self.tree.body:
                if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                if statement.value is None or not self._is_repo_anchored(statement.value):
                    continue
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in self.repo_anchored_names:
                        self.repo_anchored_names.add(target.id)
                        changed = True

    @staticmethod
    def _root(node: ast.AST) -> ast.AST:
        """Крайний левый элемент выражения-пути: `Path(x).parent / "y"` → x."""
        while True:
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                node = node.left
            elif isinstance(node, ast.Call):
                function = node.func
                builder = (
                    (isinstance(function, ast.Name) and function.id in ("Path", "PurePath", "str"))
                    or (isinstance(function, ast.Attribute) and function.attr in PATH_BUILDERS)
                )
                if not builder or not node.args:
                    return node
                node = node.args[0]
            elif isinstance(node, ast.Attribute) and node.attr in ("parent", "parents"):
                node = node.value
            elif isinstance(node, ast.Subscript):
                node = node.value
            else:
                return node

    @staticmethod
    def _goes_through_paths_module(node: ast.AST) -> bool:
        return any(
            isinstance(sub, ast.Name) and sub.id == "paths" for sub in ast.walk(node)
        )

    @staticmethod
    def _mode(call: ast.Call) -> str:
        for argument in call.args[1:]:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                return argument.value
        for keyword in call.keywords:
            if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                return str(keyword.value.value)
        return "r"

    @staticmethod
    def _keyword_paths(call: ast.Call) -> list:
        return [
            (call, keyword.value, "r")
            for keyword in call.keywords
            if keyword.arg in PATH_KEYWORDS
        ]

    def _candidates(self, node: ast.AST):
        """(всё выражение, выражение-путь, режим доступа)."""
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return [(node, node, "r")]
        if not isinstance(node, ast.Call):
            return []
        function = node.func
        called = function.id if isinstance(function, ast.Name) else getattr(function, "attr", "")
        keyword_paths = self._keyword_paths(node)
        if called in PATH_OPENERS and node.args:
            return [(node, node.args[0], self._mode(node))] + keyword_paths
        if (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id == COPY_MODULE
            and function.attr in COPY_CALLS
        ):
            # И источник, и назначение: копия состояния «рядом с кодом» —
            # такая же утечка, как оригинал.
            return [(node, argument, "w") for argument in node.args[:2]] + keyword_paths
        if called in WRITE_CALLS and node.args:
            return [(node, node.args[0], "w")] + keyword_paths
        if isinstance(function, ast.Attribute) and function.attr == "join":
            return [(node, node, "r")]
        if isinstance(function, ast.Name) and function.id == "Path":
            return [(node, node, "r")]
        if isinstance(function, ast.Attribute) and function.attr in WRITE_METHODS:
            return [(node, function.value, "w")] + keyword_paths
        return keyword_paths

    def violations(self) -> list[str]:
        found: list[str] = []
        for node in ast.walk(self.tree):
            for whole, target, mode in self._candidates(node):
                if self._goes_through_paths_module(target):
                    continue
                root = self._root(target)
                relative_literal = (
                    isinstance(root, ast.Constant)
                    and isinstance(root.value, str)
                    and not root.value.startswith(("/", "~"))
                    and ":" not in root.value[:3]
                )
                if not (self._is_repo_anchored(root) or relative_literal):
                    continue
                segment = ast.get_source_segment(self.source, whole) or ""
                if any(marker in segment.lower() for marker in STATE_MARKERS) or "w" in mode:
                    found.append(f"{self.label}:{whole.lineno}: {segment}")
        return found


def _state_path_violations() -> list[str]:
    found: list[str] = []
    for source_file in _live_sources({".py"}):
        if source_file.name == PATHS_MODULE_EXEMPT:
            continue          # единственное место, где путям и положено рождаться
        found.extend(
            _StatePathScan(
                source_file.read_text(encoding="utf-8"),
                str(source_file.relative_to(REPO_ROOT)),
            ).violations()
        )
    return found


def test_no_state_paths_relative_to_repo():
    violations = _state_path_violations()
    assert not violations, (
        "путь к состоянию строится мимо job_monitor/paths.py — именно так в "
        "репозиторий попали .env, сессии Telethon, логи и резюме (S1/S8):\n"
        + "\n".join(violations)
    )


MUTATIONS = {
    "os.path.join(BASE_DIR, …) с актуальным именем файла": """
import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION = os.path.join(BASE_DIR, "telegram.session")
""",
    "Path(__file__).parent / '.env'": """
from pathlib import Path
ENV = Path(__file__).parent / ".env"
""",
    "относительный open(..., 'w')": """
def dump(data):
    with open("hh_cookies.json", "w", encoding="utf-8") as handle:
        handle.write(data)
""",
    "os.path.dirname(__file__) внутри join": """
import os
DB = os.path.join(os.path.dirname(__file__), "job_monitor.db")
""",
    "запись по относительному пути под новым именем": """
from pathlib import Path
def save(text):
    Path("report.txt").write_text(text, encoding="utf-8")
""",
    "sqlite3.connect по относительному имени": """
import sqlite3
conn = sqlite3.connect("job_monitor.db")
""",
    "os.getcwd() как корень": """
import os
COOKIES = os.path.join(os.getcwd(), "hh_cookies.json")
""",
    "logging.FileHandler по относительному пути": """
import logging
handler = logging.FileHandler("logs/hh.log")
""",
    "RotatingFileHandler от __file__": """
import os
from logging.handlers import RotatingFileHandler
handler = RotatingFileHandler(os.path.join(os.path.dirname(__file__), "logs", "hh.log"))
""",
    "os.makedirs по относительному пути": """
import os
os.makedirs("logs", exist_ok=True)
""",
    "shutil.copy состояния рядом с кодом": """
import shutil
def backup(source):
    shutil.copy(source, "job_monitor.db.bak")
""",
    "shutil.rmtree по относительному пути": """
import shutil
shutil.rmtree("logs")
""",
    "tempfile.mkstemp(dir=...) в каталоге репозитория": """
import tempfile
handle, name = tempfile.mkstemp(dir="logs")
""",
    "sys.argv[0] как корень": """
import os, sys
ENV = os.path.join(os.path.dirname(sys.argv[0]), ".env")
""",
}

ALLOWED = {
    "через paths": """
from job_monitor import paths
target = paths.env_file()
target.write_text("TG_API_ID=1", encoding="utf-8")
""",
    "каталог, названный пользователем (migrate-legacy)": """
def import_legacy(source):
    config = source / "config.json"
    return config.read_text(encoding="utf-8")
""",
    "статика репозитория, не состояние": """
import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
with open(os.path.join(FRONTEND_DIR, "index.html"), "r", encoding="utf-8") as f:
    html = f.read()
""",
    "обработчик лога по пути из paths": """
import logging
from job_monitor import paths
handler = logging.FileHandler(paths.logs_dir() / "hh.log")
""",
    "временный файл рядом с целью, а не с кодом": """
import tempfile
from job_monitor import paths
target = paths.env_file()
handle, name = tempfile.mkstemp(dir=str(target.parent))
""",
    "makedirs по каталогу, названному пользователем": """
import os
def prepare(destination):
    os.makedirs(destination, exist_ok=True)
""",
    "copy — это метод словаря, а не shutil": """
def snapshot(settings):
    return settings.copy()
""",
    "str.replace, а не Path.replace": """
def escape(text):
    return text.replace("\\n", "\\\\n")
""",
    "shutil.copy между путями, названными снаружи": """
import shutil
def backup(source, destination):
    shutil.copy(source, destination)
""",
    "open(file=...) по абсолютному пути": """
def read(handle_path="/etc/hosts"):
    with open(file=handle_path, mode="r", encoding="utf-8") as handle:
        return handle.read()
""",
}


def test_the_state_path_check_is_not_vacuous():
    """Доказательство мутациями, вшитое в набор тестов.

    Прежний инвариант был регуляркой по одной форме записи и пропускал
    буквально всё, включая актуальные имена файлов, — и обнаружить это можно
    было только руками. Здесь и «ловит нарушения», и «не ругается на
    законное» проверяются явно, так что следующая переформулировка
    инварианта не может тихо снова стать вакуумной.
    """
    for name, snippet in MUTATIONS.items():
        assert _StatePathScan(snippet, "mutation").violations(), (
            f"проверка не заметила нарушение «{name}» — инвариант вакуумный"
        )
    for name, snippet in ALLOWED.items():
        assert not _StatePathScan(snippet, "allowed").violations(), (
            f"ложное срабатывание на «{name}»"
        )


def test_the_check_runs_on_a_meaningful_number_of_files():
    """Проверка обходит живое дерево, а не пустой список."""
    scanned = [f for f in _live_sources({".py"}) if f.name != PATHS_MODULE_EXEMPT]
    assert len(scanned) >= 15, f"просканировано всего {len(scanned)} файлов — дерево переехало?"


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


# ── Права файлов состояния ────────────────────────────────────────────
#
# `.env` (0600), cookies hh.ru (0600) и логи (0600) права получали явно, а
# `job_monitor.db` и `telegram.session` создавались по umask, то есть обычно
# 0644, и были защищены только режимом каталога. Асимметрия ничем не
# оправдана: в файле сессии Telethon лежит `auth_key`, которого достаточно
# для входа в аккаунт в обход 2FA, а в базе — переписка и контакты.


def test_tighten_never_grants_access(tmp_path):
    """Права только снимаются. Пользователь, у которого каталог строже
    нашего, должен остаться при своём."""
    strict = tmp_path / "strict"
    strict.mkdir(mode=0o500)
    paths.tighten(strict, 0o700)
    assert stat.S_IMODE(strict.stat().st_mode) == 0o500

    loose = tmp_path / "loose"
    loose.mkdir(mode=0o777)
    paths.tighten(loose, 0o700)
    assert stat.S_IMODE(loose.stat().st_mode) == 0o700


def test_tighten_is_silent_about_what_is_not_there(tmp_path):
    paths.tighten(tmp_path / "нет-такого", 0o600)      # не должно бросать


def test_data_dir_fixes_an_existing_loose_directory(tmp_path, monkeypatch):
    """Раньше режим ставился только в момент создания (`mkdir(mode=0o700)`),
    поэтому каталог, доставшийся от версии до 2.1.0 или распакованный из
    архива (tar сохраняет режим), оставался как есть: `chmod 777 <dir>` и
    следующий запуск — всё ещё 0o777."""
    target = tmp_path / "state"
    target.mkdir(mode=0o777)
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(target))

    assert stat.S_IMODE(paths.data_dir().stat().st_mode) == 0o700


def test_logs_dir_fixes_an_existing_loose_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path / "state"))
    logs = paths.data_dir() / "logs"
    logs.mkdir(mode=0o777)

    assert stat.S_IMODE(paths.logs_dir().stat().st_mode) == 0o700


def test_secure_file_makes_a_state_file_private(tmp_path):
    target = tmp_path / "telegram.session"
    target.write_text("", encoding="utf-8")
    target.chmod(0o644)

    paths.secure_file(target)

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_the_database_and_its_wal_sidecars_are_private(tmp_path, monkeypatch):
    """В WAL-режиме рядом с базой живут `-wal` и `-shm`, и в `-wal` лежат те
    же данные, пока их не перенесли в базу."""
    from job_monitor.db.connection import connect

    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path / "state"))
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO settings (key, value) VALUES ('probe', '{}')")
        conn.execute("COMMIT")
        database = paths.db_file()
        present = [database] + [
            database.with_name(database.name + suffix) for suffix in ("-wal", "-shm")
        ]
        existing = [item for item in present if item.exists()]
        assert len(existing) == 3, f"WAL-спутники не созданы: {existing}"
        for item in existing:
            assert stat.S_IMODE(item.stat().st_mode) == 0o600, item.name
    finally:
        conn.close()


def test_the_telethon_session_file_is_private(tmp_path, monkeypatch):
    """`auth_key` из этого файла достаточно, чтобы войти в аккаунт в обход
    2FA. Telethon создаёт файл прямо в конструкторе клиента и по umask."""
    from job_monitor import telegram_client

    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("TG_API_ID", "1234567")
    monkeypatch.setenv("TG_API_HASH", "deadbeefdeadbeefdeadbeefdeadbeef")
    monkeypatch.setattr(telegram_client, "_client", None)
    monkeypatch.setattr(telegram_client, "_credentials", None)

    client = telegram_client.get_client()
    try:
        target = telegram_client.session_file()
        assert target.exists(), "Telethon больше не создаёт файл сессии в конструкторе"
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    finally:
        client.session.close()
        telegram_client.reset_client()
