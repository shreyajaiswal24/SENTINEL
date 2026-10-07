"""The refine engine: verify + clean an uploaded sales file.

Four checks (the ones agreed with the user), each producing a report entry and,
where safe, an automatic fix on the actual rows:

  1. Column / schema issues — compare columns to the canonical sales schema;
       rename known drift (total_amount -> amount) back; report missing / extra.
  2. Duplicates            — drop exact duplicate rows (keep first).
  3. Null / missing values — drop rows missing a CRITICAL column value; report
       nulls in non-critical columns (kept, not dropped).
  4. Row-count / volume    — flag when the cleaned file is suspiciously small
       vs the expected volume (flag only — we never invent real rows).

Design notes:
  * We CLEAN, we never FABRICATE. Missing columns and low volume are flagged,
    not synthesised — SENTINEL can't invent a customer's real data.
  * Anchored to SalesPipeline so the schema, critical columns, and known drift
    match the rest of the system (dashboard, agents).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import pandas as pd

from pipelines.sales import SalesPipeline

# Canonical expectations, borrowed from the sales pipeline so BYO-data is judged
# against the same schema the synthetic pipeline uses.
EXPECTED_COLUMNS: list[str] = list(SalesPipeline.columns)
CRITICAL_COLUMNS: list[str] = list(SalesPipeline.critical_columns)
EXPECTED_ROWS: int = SalesPipeline.expected_rows
# Known column drifts -> canonical name (extend as more aliases are seen).
DRIFT_ALIASES: dict[str, str] = {
    SalesPipeline.drift_new_name: SalesPipeline.drift_column,  # total_amount -> amount
}
# Flag "low volume" when the cleaned file is below this fraction of expected.
LOW_VOLUME_FRACTION = 0.5


@dataclass
class Issue:
    check: str            # duplicates | nulls | schema | volume
    severity: str         # info | warning | high
    detail: str           # human-readable description
    action: str           # what the refiner did about it (or "flagged only")
    count: int = 0        # rows/columns affected, where meaningful


@dataclass
class RefineReport:
    filename: str
    original_rows: int
    original_columns: list[str]
    refined_rows: int
    refined_columns: list[str]
    issues: list[Issue] = field(default_factory=list)

    @property
    def rows_removed(self) -> int:
        return self.original_rows - self.refined_rows

    @property
    def clean(self) -> bool:
        """True when nothing more serious than an info note was found."""
        return all(i.severity == "info" for i in self.issues)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["rows_removed"] = self.rows_removed
        d["clean"] = self.clean
        return d


def _blank(v: Any) -> bool:
    """A cell counts as missing if it's NaN/None or an empty/whitespace string."""
    if v is None:
        return True
    if isinstance(v, float) and pd.isna(v):
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    return pd.isna(v) if not isinstance(v, (list, dict)) else False


def refine_dataframe(df: pd.DataFrame, filename: str = "upload") -> tuple[pd.DataFrame, RefineReport]:
    """Run the four checks on `df`; return (refined_df, report). Pure, no I/O."""
    original_rows = len(df)
    original_columns = [str(c) for c in df.columns]
    issues: list[Issue] = []

    # --- 1. Column / schema issues -----------------------------------------
    # a) rename known drift aliases back to canonical
    renames = {c: DRIFT_ALIASES[c] for c in df.columns if c in DRIFT_ALIASES}
    if renames:
        df = df.rename(columns=renames)
        for old, new in renames.items():
            issues.append(Issue(
                "schema", "warning",
                f"Column '{old}' looks like drifted '{new}' — renamed back to canonical.",
                f"renamed '{old}' -> '{new}'", 1,
            ))
    cols_now = [str(c) for c in df.columns]
    # b) missing expected columns (can't fabricate — flag)
    missing = [c for c in EXPECTED_COLUMNS if c not in cols_now]
    if missing:
        issues.append(Issue(
            "schema", "high",
            f"Missing expected column(s): {', '.join(missing)}. Cannot invent these — please add them.",
            "flagged only", len(missing),
        ))
    # c) extra/unexpected columns (kept, just noted)
    extra = [c for c in cols_now if c not in EXPECTED_COLUMNS]
    if extra:
        issues.append(Issue(
            "schema", "info",
            f"Unexpected column(s) kept as-is: {', '.join(extra)}.",
            "kept", len(extra),
        ))

    # --- 2. Duplicates ------------------------------------------------------
    dup_mask = df.duplicated(keep="first")
    dup_count = int(dup_mask.sum())
    if dup_count:
        df = df[~dup_mask].reset_index(drop=True)
        issues.append(Issue(
            "duplicates", "warning",
            f"Found {dup_count} exact duplicate row(s).",
            f"removed {dup_count} duplicate row(s)", dup_count,
        ))

    # --- 3. Null / missing values ------------------------------------------
    present_critical = [c for c in CRITICAL_COLUMNS if c in df.columns]
    if present_critical:
        blank_mask = df[present_critical].map(_blank).any(axis=1)
        n_bad = int(blank_mask.sum())
        if n_bad:
            df = df[~blank_mask].reset_index(drop=True)
            issues.append(Issue(
                "nulls", "warning",
                f"{n_bad} row(s) missing a value in critical column(s) "
                f"({', '.join(present_critical)}).",
                f"dropped {n_bad} row(s) missing critical values", n_bad,
            ))
    # non-critical nulls: report only, don't drop
    noncritical = [c for c in df.columns if c not in CRITICAL_COLUMNS]
    if noncritical:
        nc_nulls = int(df[noncritical].map(_blank).sum().sum())
        if nc_nulls:
            issues.append(Issue(
                "nulls", "info",
                f"{nc_nulls} missing value(s) in non-critical columns (left as-is).",
                "kept (non-critical)", nc_nulls,
            ))

    # --- 4. Row-count / volume ---------------------------------------------
    refined_rows = len(df)
    if refined_rows < LOW_VOLUME_FRACTION * EXPECTED_ROWS:
        issues.append(Issue(
            "volume", "warning",
            f"Only {refined_rows} rows after cleaning — well below the expected "
            f"~{EXPECTED_ROWS}. Data may be incomplete.",
            "flagged only", refined_rows,
        ))

    report = RefineReport(
        filename=filename,
        original_rows=original_rows,
        original_columns=original_columns,
        refined_rows=refined_rows,
        refined_columns=[str(c) for c in df.columns],
        issues=issues,
    )
    return df, report


def _read_any(path_or_buf: Any, filename: str) -> pd.DataFrame:
    """Load CSV or Excel by file extension. `path_or_buf` may be a path or bytes buffer."""
    name = filename.lower()
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(path_or_buf, dtype=object)
    if name.endswith(".csv"):
        return pd.read_csv(path_or_buf, dtype=object)
    raise ValueError(f"Unsupported file type: {filename!r} (use .csv, .xlsx, or .xls)")


def refine_file(path_or_buf: Any, filename: str) -> tuple[pd.DataFrame, RefineReport]:
    """Load a CSV/Excel file (path or buffer) and refine it."""
    df = _read_any(path_or_buf, filename)
    return refine_dataframe(df, filename=filename)


def to_bytes(df: pd.DataFrame, filename: str) -> bytes:
    """Serialize the refined DataFrame back to the same format as the input."""
    import io
    if filename.lower().endswith((".xlsx", ".xls")):
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        return buf.getvalue()
    return df.to_csv(index=False).encode("utf-8")
