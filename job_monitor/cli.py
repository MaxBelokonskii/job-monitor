"""Точка входа CLI: `run` (веб-приложение) и `migrate-legacy` (импорт старых данных)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from job_monitor.db.connection import get_connection
from job_monitor.legacy_import import import_legacy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="job-monitor")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="запустить веб-приложение")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8000)

    migrate = sub.add_parser("migrate-legacy", help="импортировать данные старой версии")
    migrate.add_argument("--from", dest="source", type=Path, required=True)

    args = parser.parse_args(argv)

    if args.command == "run":
        import uvicorn

        if args.host != "127.0.0.1":
            print("отказ: приложение слушает только 127.0.0.1", file=sys.stderr)
            return 2
        uvicorn.run("api.main:app", host=args.host, port=args.port, reload=False)
        return 0

    report = import_legacy(get_connection(), args.source)
    print(f"настроек: {report.settings_keys}, критериев: {report.criteria_keys}, "
          f"контактов: {report.contacts}, "
          f"отправок: {report.sends}, вакансий: {report.vacancies}")
    for note in report.skipped:
        print(f"  пропущено: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
