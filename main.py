"""SENTINEL entry point.

Phase 1: prove all three pipelines run — both clean and with each injected
failure — and that everything persists to SQLite.

    python main.py              # clean run of all 3 pipelines
    python main.py --failures   # clean + every injected failure (skips slow timeout)
    python main.py --failures --include-timeout   # also exercise the 11s timeout

Later phases add: monitor loop, agent graph, voice, dashboard.
"""
from __future__ import annotations

import argparse

from pipelines import PIPELINES
from utils import database
from utils.state import FailureType


def run_clean() -> None:
    print("\n=== Clean run (all pipelines, no injected failure) ===")
    for name, cls in PIPELINES.items():
        result = cls().run()
        print(" ", result)


def run_failures(include_timeout: bool) -> None:
    print("\n=== Injected-failure runs ===")
    skip = set() if include_timeout else {FailureType.TIMEOUT}
    for name, cls in PIPELINES.items():
        print(f"\n-- {name} --")
        for failure in FailureType:
            if failure is FailureType.NONE or failure in skip:
                continue
            result = cls().run(force_failure=failure)
            print(" ", result)
            if result.error_message:
                print(f"      error: {result.error_message}")
    if not include_timeout:
        print("\n(timeout skipped — pass --include-timeout to exercise the 11s sleep)")


def main() -> None:
    parser = argparse.ArgumentParser(description="SENTINEL Phase 1 test runner")
    parser.add_argument("--failures", action="store_true", help="run every injected failure")
    parser.add_argument("--include-timeout", action="store_true", help="include the slow timeout failure")
    args = parser.parse_args()

    database.init_db()
    print("Database ready at", database.DB_PATH)

    run_clean()
    if args.failures:
        run_failures(args.include_timeout)

    print("\n=== Recent runs in DB ===")
    for row in database.recent_runs(limit=12):
        print(
            f"  #{row['run_id']:<3} {row['pipeline_name']:<9} "
            f"{row['status']:<8} rows={row['rows_actual']:<5} "
            f"injected={row['injected_failure']}"
        )
    print("\nPhase 1 OK.")


if __name__ == "__main__":
    main()
