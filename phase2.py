"""Phase 2 evaluation: score the Monitor agent against Phase 1 ground truth.

Replays every stored pipeline_run through the Monitor — WITHOUT showing it the
`injected_failure` column — and compares its verdict to that hidden label.

    python phase2.py            # confusion matrix + per-class accuracy
    python phase2.py --graph    # also run one case through the LangGraph node

Baseline handling: get_baseline() averages all 'success' runs, which here include
injected anomalies (low-row, duplicate) that would skew it. For an honest test we
feed Monitor a CLEAN baseline computed from the `none` runs only — mirroring real
operation, where the baseline accrues from normal runs and anomalies are rare.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict

from agents.monitor import build_monitor_graph, detect
from pipelines import PIPELINES
from utils import database
from utils.state import FailureType


def clean_baselines() -> dict[str, float]:
    """Average rows_actual over the clean (`none`) runs, per pipeline."""
    baselines: dict[str, float] = {}
    with database.get_connection() as conn:
        for name in PIPELINES:
            row = conn.execute(
                "SELECT AVG(rows_actual) a FROM pipeline_runs "
                "WHERE pipeline_name = ? AND injected_failure = 'none' AND status = 'success'",
                (name,),
            ).fetchone()
            if row and row["a"] is not None:
                baselines[name] = row["a"]
    return baselines


def evaluate() -> bool:
    baselines = clean_baselines()
    rows = database.recent_runs(limit=10_000)
    if not rows:
        print("No runs in DB. Run `python main.py --failures --include-timeout` first.")
        return False

    confusion: dict[tuple[str, str], int] = defaultdict(int)
    per_class_total: dict[str, int] = defaultdict(int)
    per_class_correct: dict[str, int] = defaultdict(int)
    mistakes = []

    for r in rows:
        expected_cols = PIPELINES[r["pipeline_name"]].columns
        data = database.get_run_data(r["run_id"])
        verdict = detect(
            pipeline_name=r["pipeline_name"],
            status=r["status"],
            rows_actual=r["rows_actual"],
            columns_actual=json.loads(r["columns_actual"]),
            columns_expected=expected_cols,
            execution_time=r["execution_time"],
            baseline_rows=baselines.get(r["pipeline_name"]),
            data=data,
        )
        truth = r["injected_failure"] or FailureType.NONE.value
        pred = verdict["failure_type"]
        confusion[(truth, pred)] += 1
        per_class_total[truth] += 1
        if truth == pred:
            per_class_correct[truth] += 1
        else:
            mistakes.append((r["run_id"], r["pipeline_name"], truth, pred, verdict["reason"]))

    # --- report ---
    print("\n=== Monitor vs ground truth (per failure class) ===")
    all_ok = True
    for failure in FailureType:
        label = failure.value
        total = per_class_total.get(label, 0)
        if total == 0:
            continue
        correct = per_class_correct.get(label, 0)
        ok = correct == total
        all_ok = all_ok and ok
        mark = "OK " if ok else "XX "
        print(f"  [{mark}] {label:<18} {correct}/{total}")

    if mistakes:
        print("\n=== Misclassifications ===")
        for run_id, name, truth, pred, reason in mistakes:
            print(f"  #{run_id} {name}: truth={truth} predicted={pred}  ({reason})")

    total_runs = sum(per_class_total.values())
    total_correct = sum(per_class_correct.values())
    print(f"\nOverall: {total_correct}/{total_runs} correct "
          f"({100*total_correct/total_runs:.0f}%)")
    print("Phase 2", "OK." if all_ok else "FAILED — see misclassifications above.")
    return all_ok


def demo_graph() -> None:
    """Push one stored run through the compiled LangGraph node end-to-end."""
    print("\n=== LangGraph node smoke test ===")
    app = build_monitor_graph()
    # grab a schema_drift run to show the node classifying it
    with database.get_connection() as conn:
        r = conn.execute(
            "SELECT * FROM pipeline_runs WHERE injected_failure='schema_drift' LIMIT 1"
        ).fetchone()
    if r is None:
        print("  (no schema_drift run found; run main.py --failures first)")
        return
    initial = {
        "run_id": r["run_id"],
        "pipeline_name": r["pipeline_name"],
        "pipeline_status": r["status"],
        "rows_actual": r["rows_actual"],
        "columns_actual": json.loads(r["columns_actual"]),
        "columns_expected": PIPELINES[r["pipeline_name"]].columns,
        "execution_time": r["execution_time"],
    }
    out = app.invoke(initial)
    print(f"  input run #{r['run_id']} ({r['pipeline_name']}, injected=schema_drift)")
    print(f"  node output -> failure_detected={out['failure_detected']} "
          f"failure_type={out['failure_type']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="SENTINEL Phase 2 Monitor evaluation")
    parser.add_argument("--graph", action="store_true", help="also run the LangGraph node")
    args = parser.parse_args()

    ok = evaluate()
    if args.graph:
        demo_graph()
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
