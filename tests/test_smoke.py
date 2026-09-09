import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_hh_worker_does_not_use_webdriver_manager() -> None:
    """S9/S10 residual: importing the HH side must not pull webdriver-manager.

    The check used to import the standalone `hh_monitor` module; that script
    is gone (its Selenium code moved into job_monitor/workers/hh.py, task 7
    of the workers plan removed the file), so the same invariant is now
    asserted against the module that actually runs. webdriver-manager
    downloads a chromedriver binary from the network at import/setup time —
    Selenium ≥ 4.6 finds the driver itself, and an unpinned download is
    exactly the supply-chain surface the security plan closed.

    **Why a subprocess.** This test used to do
    `sys.modules.pop("job_monitor.workers.hh")` plus a re-import, which left
    a SECOND instance of the module in `sys.modules` while `api.main`,
    `job_monitor/workers/hh_steps.py` and the already-built `login`
    singleton kept holding the first. Alphabetical collection order hid it
    (`test_hh_login` < `test_smoke`); run the files in any other order and
    seven tests in `tests/test_hh_login.py` failed with the signature of a
    doubly-imported module — `assert <HhLoginState.logged_in> is
    <HhLoginState.logged_in>`. That blocked `pytest -n`, `pytest-randomly`
    and simply passing the files in a different order, and the reverse
    failure was possible too: a `test_hh_login` exercising a different
    module instance than the application stops checking the subject.

    A fresh interpreter answers the actual question — "does importing this
    module pull webdriver-manager" — better than an in-process re-import
    does (nothing can already be in `sys.modules`), and it leaves this
    process untouched. Same approach as
    `test_paths::test_importing_the_app_creates_nothing_on_disk`.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, json;"
            " import job_monitor.workers.hh;"
            " print(json.dumps('webdriver_manager' in sys.modules))",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        "импорт job_monitor.workers.hh в чистом процессе не удался — если это "
        f"ModuleNotFoundError на webdriver_manager, дефект как раз тот самый:\n{result.stderr}"
    )
    assert result.stdout.strip() == "false", (
        "импорт job_monitor.workers.hh тянет webdriver-manager: он скачивает "
        "chromedriver из сети на импорте, а Selenium >= 4.6 находит драйвер сам"
    )


def test_index_is_served(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Job Monitor" in response.text
