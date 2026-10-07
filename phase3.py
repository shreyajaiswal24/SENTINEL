"""Phase 3 evaluation: Monitor -> Diagnose on real runs, end to end.

For one representative run of each failure type, this:
  1. runs the Monitor node to detect the failure,
  2. runs the (LLM-backed) Diagnose node for root cause / risk / confidence / fix,
  3. persists the diagnosis to the `incidents` table (so incident-RAG has history),
  4. sanity-checks the structured output.

    python phase3.py            # diagnose one run per failure type
    python phase3.py --all      # diagnose every stored run

Requires GROQ_API_KEY in .env (copy from .env.example). Prints a clear message
and exits if the key is missing — no code change needed once you add it.
"""
from __future__ import annotations

import argparse
import json

from agents.diagnose import _provider_order, diagnose_node
from agents.monitor import monitor_node
from pipelines import PIPELINES
from utils import database
from utils.config import settings
from utils.state import FailureType, RiskLevel


def _state_from_run(run) -> dict:
    return {
        "run_id": run["run_id"],
        "pipeline_name": run["pipeline_name"],
        "pipeline_status": run["status"],
        "rows_expected": run["rows_expected"],
        "rows_actual": run["rows_actual"],
        "columns_actual": json.loads(run["columns_actual"]),
        "columns_expected": PIPELINES[run["pipeline_name"]].columns,
        "execution_time": run["execution_time"],
        "error_message": run["error_message"],
    }


def _representative_runs() -> list:
    """One run per injected failure type (excluding 'none')."""
    runs = []
    with database.get_connection() as conn:
        for failure in FailureType:
            if failure is FailureType.NONE:
                continue
            row = conn.execute(
                "SELECT * FROM pipeline_runs WHERE injected_failure = ? "
                "ORDER BY run_id DESC LIMIT 1",
                (failure.value,),
            ).fetchone()
            if row:
                runs.append(row)
    return runs


def evaluate(diagnose_all: bool) -> bool:
    if not settings.llm_ready:
        print("No LLM key set. Copy .env.example to .env and add OPENAI_API_KEY "
              "(preferred) or GROQ_API_KEY, then re-run `python phase3.py`.")
        return False

    order = _provider_order()
    primary = order[0]
    model = settings.OPENAI_MODEL if primary == "openai" else settings.GROQ_MODEL
    fallback = f" (fallback: {order[1]})" if len(order) > 1 else ""
    print(f"Using {primary} model: {model}{fallback}\n")
    runs = database.recent_runs(limit=10_000) if diagnose_all else _representative_runs()

    valid_risks = {r.value for r in RiskLevel}
    all_ok = True

    for run in runs:
        state = _state_from_run(run)
        # --- Monitor ---
        mon = monitor_node(state)
        state.update(mon)
        if not mon.get("failure_detected"):
            # clean run — Diagnose is correctly skipped
            print(f"#{run['run_id']} {run['pipeline_name']:<9} "
                  f"injected={(run['injected_failure'] or 'none'):<18} -> no failure detected (skip)")
            continue

        # --- Diagnose (LLM) ---
        diag = diagnose_node(state)

        risk = diag.get("risk_level")
        conf = diag.get("confidence")
        ok = (
            risk in valid_risks
            and isinstance(conf, float) and 0.0 <= conf <= 1.0
            and diag.get("root_cause") and diag.get("fix_plan")
        )
        all_ok = all_ok and ok

        # persist as an incident (feeds incident-RAG for later diagnoses)
        database.save_incident(
            pipeline_name=run["pipeline_name"],
            failure_type=mon["failure_type"],
            root_cause=diag["root_cause"],
            risk_level=risk,
            confidence=conf,
            fix_plan=diag["fix_plan"],
            started_at=run["started_at"],
        )

        mark = "OK " if ok else "XX "
        print(f"[{mark}] #{run['run_id']} {run['pipeline_name']:<9} "
              f"detected={mon['failure_type']:<18} risk={risk:<8} conf={conf:.2f}")
        print(f"        cause: {diag['root_cause']}")
        print(f"        fix:   {diag['fix_plan']}")

    print("\nPhase 3", "OK." if all_ok else "FAILED — see XX rows above.")
    return all_ok


def main() -> None:
    parser = argparse.ArgumentParser(description="SENTINEL Phase 3 Diagnose evaluation")
    parser.add_argument("--all", action="store_true", help="diagnose every stored run")
    args = parser.parse_args()
    database.init_db()
    ok = evaluate(args.all)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
