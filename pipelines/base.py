"""BasePipeline: generate fake data with Faker, optionally inject a failure, persist.

Design decision (agreed in planning): failures are **injectable** so every later
phase can be tested deterministically:

    SalesPipeline().run(force_failure=FailureType.SCHEMA_DRIFT)

A random mode is also available for live demos:

    SalesPipeline().run(fail_probability=0.4)

The pipeline reports raw facts only (rows, columns, time, crash). It does NOT
decide whether a SUCCESS run is "anomalous" — that's the Monitor agent's job
(Phase 2). Ground truth is recorded as `injected_failure` for test assertions.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from faker import Faker

from utils import database
from utils.state import FailureType, RunStatus

# Timeout failure sleeps past this many seconds (Monitor's budget is 10s, Phase 2).
TIMEOUT_SLEEP_SECONDS = 11.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PipelineResult:
    pipeline_name: str
    status: str
    rows_expected: int
    rows_actual: int
    columns_actual: list[str]
    execution_time: float
    error_message: Optional[str]
    injected_failure: str
    started_at: str
    finished_at: str
    run_id: Optional[int] = None
    data: list[dict[str, Any]] = field(default_factory=list)

    def __str__(self) -> str:
        tag = "OK " if self.status == RunStatus.SUCCESS.value else "ERR"
        return (
            f"[{tag}] {self.pipeline_name:<9} "
            f"rows={self.rows_actual}/{self.rows_expected} "
            f"cols={len(self.columns_actual)} "
            f"time={self.execution_time:.2f}s "
            f"injected={self.injected_failure}"
        )


class BasePipeline:
    # --- subclasses must set these ---
    name: str = "base"
    expected_rows: int = 1000
    columns: list[str] = []          # canonical/expected column order
    critical_columns: list[str] = []  # columns whose NULLs matter
    drift_column: str = ""            # column renamed on SCHEMA_DRIFT
    drift_new_name: str = ""

    def __init__(self, seed: Optional[int] = None):
        self.faker = Faker()
        if seed is not None:
            Faker.seed(seed)
            random.seed(seed)

    # subclasses implement this
    def generate_row(self) -> dict[str, Any]:
        raise NotImplementedError

    def _pick_failure(
        self, force_failure: Optional[FailureType], fail_probability: float
    ) -> FailureType:
        if force_failure is not None:
            return force_failure
        if fail_probability > 0 and random.random() < fail_probability:
            return random.choice([f for f in FailureType if f is not FailureType.NONE])
        return FailureType.NONE

    def run(
        self,
        force_failure: Optional[FailureType] = None,
        fail_probability: float = 0.0,
        persist: bool = True,
    ) -> PipelineResult:
        failure = self._pick_failure(force_failure, fail_probability)
        started_at = _now_iso()
        start = time.perf_counter()

        status = RunStatus.SUCCESS
        error_message: Optional[str] = None
        rows: list[dict[str, Any]] = []
        columns_actual = list(self.columns)

        try:
            # --- CRASH: blow up before anything is produced ---
            if failure is FailureType.CRASH:
                raise RuntimeError(
                    f"{self.name} pipeline crashed: unhandled exception during extract"
                )

            # --- TIMEOUT: take too long ---
            if failure is FailureType.TIMEOUT:
                time.sleep(TIMEOUT_SLEEP_SECONDS)

            n_rows = self.expected_rows
            if failure is FailureType.ROW_COUNT_ANOMALY:
                # well outside the +/-20% threshold the Monitor uses
                n_rows = int(self.expected_rows * random.uniform(0.4, 0.65))

            rows = [self.generate_row() for _ in range(n_rows)]

            if failure is FailureType.SCHEMA_DRIFT:
                rows = self._apply_schema_drift(rows)
                columns_actual = list(rows[0].keys()) if rows else columns_actual

            if failure is FailureType.NULL_VALUES:
                rows = self._apply_null_values(rows)

            if failure is FailureType.DUPLICATE_DATA:
                rows = self._apply_duplicates(rows)

        except Exception as exc:  # CRASH path
            status = RunStatus.CRASHED
            error_message = str(exc)
            rows = []
            columns_actual = []

        execution_time = time.perf_counter() - start
        finished_at = _now_iso()

        result = PipelineResult(
            pipeline_name=self.name,
            status=status.value,
            rows_expected=self.expected_rows,
            rows_actual=len(rows),
            columns_actual=columns_actual,
            execution_time=round(execution_time, 3),
            error_message=error_message,
            injected_failure=failure.value,
            started_at=started_at,
            finished_at=finished_at,
            data=rows,
        )

        if persist:
            result.run_id = database.save_run(
                pipeline_name=result.pipeline_name,
                status=result.status,
                rows_expected=result.rows_expected,
                rows_actual=result.rows_actual,
                columns_actual=result.columns_actual,
                execution_time=result.execution_time,
                error_message=result.error_message,
                injected_failure=result.injected_failure,
                started_at=result.started_at,
                finished_at=result.finished_at,
                data=result.data,
            )
        return result

    # --- failure transformations ---
    def _apply_schema_drift(self, rows: list[dict]) -> list[dict]:
        if not self.drift_column:
            return rows
        return [
            {
                (self.drift_new_name if k == self.drift_column else k): v
                for k, v in row.items()
            }
            for row in rows
        ]

    def _apply_null_values(self, rows: list[dict], fraction: float = 0.3) -> list[dict]:
        if not self.critical_columns:
            return rows
        n_null = int(len(rows) * fraction)
        for row in random.sample(rows, k=min(n_null, len(rows))):
            for col in self.critical_columns:
                row[col] = None
        return rows

    def _apply_duplicates(self, rows: list[dict], fraction: float = 0.25) -> list[dict]:
        if not rows:
            return rows
        n_dup = int(len(rows) * fraction)
        return rows + random.choices(rows, k=n_dup)
