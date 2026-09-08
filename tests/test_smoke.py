import importlib
import sys


def test_hh_worker_does_not_use_webdriver_manager() -> None:
    """S9/S10 residual: importing the HH side must not pull webdriver-manager.

    The check used to import the standalone `hh_monitor` module; that script
    is gone (its Selenium code moved into job_monitor/workers/hh.py, task 7
    of the workers plan removed the file), so the same invariant is now
    asserted against the module that actually runs. webdriver-manager
    downloads a chromedriver binary from the network at import/setup time —
    Selenium ≥ 4.6 finds the driver itself, and an unpinned download is
    exactly the supply-chain surface the security plan closed.
    """
    sys.modules.pop("job_monitor.workers.hh", None)
    importlib.import_module("job_monitor.workers.hh")
    assert "webdriver_manager" not in sys.modules


def test_index_is_served(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Job Monitor" in response.text
