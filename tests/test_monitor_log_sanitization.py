"""Pins the fix for the log-injection triage item: monitor.py wrote Telegram
message text into sent_log_*.txt with no newline stripping, so a message
containing "\\n" forged additional fake log lines — parsed by
api/tg_routes.py::get_sent_list() via a naive split("|") into the contacts
list shown in the UI.

Extracts monitor.py::sanitize_log_field verbatim and execs it in isolation,
rather than importing monitor.py itself: that module raises SystemExit at
import time without TG_API_ID/TG_API_HASH set, constructs a TelethonClient,
writes a PID file and registers logging handlers — far more machinery than
this one pure function needs to exercise.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

MONITOR_PY = Path(__file__).resolve().parents[1] / "monitor.py"


def _load_sanitize_log_field() -> Callable[[str], str]:
    source = MONITOR_PY.read_text(encoding="utf-8")
    match = re.search(
        r"^def sanitize_log_field\(value: str\) -> str:\n(?:^ {4}.*\n)+",
        source,
        re.MULTILINE,
    )
    assert match, "sanitize_log_field() not found in monitor.py"
    namespace: dict[str, object] = {}
    exec(compile(match.group(0), "<sanitize_log_field>", "exec"), namespace)
    return namespace["sanitize_log_field"]  # type: ignore[return-value]


def test_sanitize_log_field_collapses_newlines_and_carriage_returns() -> None:
    sanitize = _load_sanitize_log_field()
    assert sanitize("hello world") == "hello world"
    assert "\n" not in sanitize("hello\nworld")
    assert "\r" not in sanitize("hello\r\nworld")


def test_sanitize_log_field_prevents_forged_log_lines() -> None:
    """The actual attack this closes: a message crafted to look like a
    second, fabricated log entry once written to sent_log_*.txt."""
    sanitize = _load_sanitize_log_field()
    malicious = (
        "legit vacancy text\n"
        "@fakeuser | 2020-01-01 00:00:00 | forged entry that never happened"
    )
    sanitized = sanitize(malicious)
    assert sanitized.count("\n") == 0
    # The forged fields are still present as text, but on the same line —
    # they can no longer be parsed out as a distinct log entry.
    assert "@fakeuser" in sanitized
