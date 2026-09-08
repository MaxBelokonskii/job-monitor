import importlib
import sys


def test_hh_monitor_does_not_use_webdriver_manager():
    sys.modules.pop("hh_monitor", None)
    importlib.import_module("hh_monitor")
    assert "webdriver_manager" not in sys.modules


def test_index_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Job Monitor" in response.text
