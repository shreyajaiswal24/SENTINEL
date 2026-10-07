"""Phase 7 (Reporter enrichment) + Phase 9 (Dashboard data) — offline tests.

Phase 7: drive the graph and check the enriched report (severity glyph, human MTTR,
fix journey, outcome) and the spoken summary; unit-test the duration formatter.
Phase 9: check the dashboard's streamlit-free data layer returns coherent metrics
and tables, and that app.py is importable/compilable.

    python phase7_9.py
"""
from __future__ import annotations

from typing import Any

from agents.diagnose import DEFAULT_RISK
from agents.graph import build_sentinel_graph, initial_state_from_run
from agents.reporter import _human_duration
from dashboard import data
from pipelines import PIPELINES
from utils import database
from utils.state import FailureType, RiskLevel


def stub_diagnose(state: dict) -> dict[str, Any]:
    if not state.get("failure_detected"):
        return {"node_trace": ["diagnose:skipped"]}
    ftype = state["failure_type"]
    return {
        "root_cause": f"(stub) {ftype}",
        "risk_level": DEFAULT_RISK.get(ftype, RiskLevel.MEDIUM).value,
        "confidence": 0.88,
        "fix_plan": f"(stub) remediate {ftype}",
        "similar_incidents": [],
        "node_trace": ["diagnose"],
    }


def test_duration() -> bool:
    print("=== MTTR formatter ===")
    cases = [(5.0, "5.0s"), (75, "1m 15s"), (3725, "1h 2m"), (None, "n/a")]
    ok = True
    for secs, expected in cases:
        got = _human_duration(secs)
        good = got == expected
        ok &= good
        print(f"  [{'OK ' if good else 'XX '}] {secs} -> {got}")
    return ok


def test_report() -> bool:
    print("\n=== Enriched report ===")
    app = build_sentinel_graph(diagnose=stub_diagnose)
    row = database.get_run(
        PIPELINES["sales"]().run(force_failure=FailureType.DUPLICATE_DATA).run_id
    )
    out = app.invoke(initial_state_from_run(row))
    report = out.get("report", "")
    spoken = out.get("spoken_summary", "")
    print(report)
    print(f"  spoken: {spoken}")
    checks = {
        "has glyph": any(g in report for g in "🟢🟡🟠🔴⚪"),
        "has root cause": "root cause:" in report,
        "has strategy": "strategy:" in report,
        "has outcome": "outcome:" in report,
        "has MTTR": "MTTR:" in report,
        "spoken non-empty": bool(spoken),
        "incident saved": out.get("incident_id") is not None,
    }
    ok = all(checks.values())
    for k, v in checks.items():
        print(f"  [{'OK ' if v else 'XX '}] {k}")
    return ok


def test_dashboard_data() -> bool:
    print("\n=== Dashboard data layer ===")
    stats = data.metrics()
    runs = data.runs_table(limit=5)
    incidents = data.incidents_table(limit=5)
    checks = {
        "stats has total": "total" in stats and stats["total"] >= 0,
        "resolution_rate in [0,1]": 0.0 <= stats["resolution_rate"] <= 1.0,
        "by_type is dict": isinstance(stats["by_type"], dict),
        "runs_table shaped": all("pipeline" in r and "rows" in r for r in runs),
        "incidents_table shaped": all("risk" in r and "resolved" in r for r in incidents),
    }
    ok = all(checks.values())
    for k, v in checks.items():
        print(f"  [{'OK ' if v else 'XX '}] {k}")
    print(f"  metrics: total={stats['total']} resolved={stats['resolved']} "
          f"rate={stats['resolution_rate']*100:.0f}% avg_mttr={stats['avg_mttr']}")

    # FastAPI backend must serve all three endpoints
    from fastapi.testclient import TestClient

    from backend.main import app
    client = TestClient(app)
    api_checks = {
        "/api/health": client.get("/api/health").status_code == 200,
        "/api/metrics": isinstance(client.get("/api/metrics").json(), dict),
        "/api/runs": isinstance(client.get("/api/runs?limit=3").json(), list),
        "/api/incidents": isinstance(client.get("/api/incidents?limit=3").json(), list),
    }
    for path, good in api_checks.items():
        print(f"  [{'OK ' if good else 'XX '}] API {path}")
    ok = ok and all(api_checks.values())
    return ok


def main() -> None:
    database.init_db()
    all_ok = test_duration() and test_report() and test_dashboard_data()
    print("\nPhase 7 + 9", "OK." if all_ok else "FAILED — see XX rows above.")
    raise SystemExit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
