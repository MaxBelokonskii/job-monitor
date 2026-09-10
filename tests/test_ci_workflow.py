"""CI существует ради одного свойства: «зелёный» значит одно и то же.

Без Node.js набор пропускает 27 тестов — среди них поведенческие пины XSS
и CSP — и всё равно рапортует «759 passed». Тесты ниже сторожат ровно то,
ради чего workflow заведён: чтобы никто не убрал из него Node, не сузил
матрицу до одной версии Python и не начал ставить зависимости мимо лока.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github/workflows/tests.yml"


def _workflow() -> dict:
    assert WORKFLOW.exists(), (
        f"{WORKFLOW.relative_to(REPO_ROOT)} исчез — прогон снова держится "
        "только на том, что кто-то запустил его руками"
    )
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _workflow()["jobs"]["test"]["steps"]


def test_the_workflow_installs_node() -> None:
    """Главная причина, по которой CI вообще заведён."""
    uses = [step.get("uses", "") for step in _steps()]
    assert any(u.startswith("actions/setup-node@") for u in uses), (
        "без Node.js прогон пропускает поведенческие пины XSS и CSP и "
        "всё равно рапортует «passed» — CI перестаёт что-либо доказывать"
    )


def test_the_workflow_fails_when_anything_is_skipped() -> None:
    """Обратная сторона: даже с Node пропуск означает, что набор
    доказывает меньше, чем показывает."""
    scripts = "\n".join(step.get("run", "") for step in _steps())
    assert "skipped" in scripts, "прогон не проверяет, что пропусков нет"
    assert "pipefail" in scripts, (
        "без pipefail код возврата берётся у `tee`, и упавший прогон "
        "проезжает зелёным — ровно тот молчаливый успех, против которого "
        "этот workflow и написан"
    )


def test_the_workflow_installs_from_the_lock_file() -> None:
    """Второй смысл CI: сам лок иначе не проверяется никогда — все
    работают в готовом `.venv`. Дефект с `uvloop` без платформенного
    маркера, ломавший документированный запуск под Windows, был ровно
    этого класса."""
    scripts = "\n".join(step.get("run", "") for step in _steps())
    assert "requirements.lock" in scripts


def test_the_matrix_covers_the_declared_python_floor() -> None:
    """`requires-python = ">=3.11"` — это обещание. Прогон только на той
    версии, что стоит у разработчика, оставляет его непроверенным."""
    import tomllib

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    floor = pyproject["project"]["requires-python"].lstrip(">=").strip()
    versions = _workflow()["jobs"]["test"]["strategy"]["matrix"]["python"]
    assert floor in versions, (
        f"pyproject обещает Python {floor}, а матрица его не проверяет: {versions}"
    )
    assert len(versions) >= 2, "матрица из одной версии не проверяет диапазон"


@pytest.mark.parametrize("event", ["push", "pull_request"])
def test_the_workflow_runs_on_the_events_that_matter(event: str) -> None:
    """`on` в YAML разбирается в ключ `True`: `on` — булев литерал YAML 1.1.
    Обращаемся по обоим именам, чтобы проверка не сломалась от версии
    парсера."""
    workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True))
    assert triggers is not None, "у workflow нет секции on"
    assert event in triggers
