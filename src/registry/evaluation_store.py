"""DuckDB-backed store for evaluation run records (Iteration 5).

Separate from the operational MetricsStore — this table tracks experimental
outcomes across the five baseline conditions for thesis analysis.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import duckdb

from src.schemas.evaluation import (
    BaselineCondition,
    EvaluationRecord,
    FailureReason,
    TaskTier,
)

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS evaluation_runs (
    id                       VARCHAR PRIMARY KEY,
    run_id                   VARCHAR NOT NULL,
    condition                VARCHAR NOT NULL,
    task_id                  VARCHAR NOT NULL,
    task_tier                VARCHAR NOT NULL,
    attempt_count            INTEGER NOT NULL DEFAULT 0,
    capability_type          VARCHAR,
    verification_tier_reached INTEGER,
    gate_passed              BOOLEAN NOT NULL,
    output_in_range          BOOLEAN NOT NULL,
    token_cost               INTEGER NOT NULL DEFAULT 0,
    attempt_latency_ms       INTEGER NOT NULL DEFAULT 0,
    failure_reason           VARCHAR NOT NULL,
    registry_snapshot_id     VARCHAR NOT NULL,
    invocation_record_id     VARCHAR,
    seed                     INTEGER NOT NULL,
    is_pilot                 BOOLEAN NOT NULL DEFAULT FALSE,
    human_label              VARCHAR,
    timestamp                TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class EvaluationStore:
    """DuckDB-backed store for evaluation run records."""

    def __init__(self, *, db_path: str | None = None) -> None:
        self._conn = duckdb.connect(db_path or ":memory:")
        self._conn.execute(_INIT_SQL)

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> EvaluationStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass

    # -- writing -------------------------------------------------------------

    def record_run(self, record: EvaluationRecord) -> str:
        """Insert one evaluation run record. Returns the generated row ID."""
        row_id = uuid.uuid4().hex
        self._conn.execute(
            """
            INSERT INTO evaluation_runs (
                id, run_id, condition, task_id, task_tier,
                attempt_count, capability_type, verification_tier_reached,
                gate_passed, output_in_range, token_cost, attempt_latency_ms,
                failure_reason, registry_snapshot_id, invocation_record_id,
                seed, is_pilot, human_label, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row_id,
                record.run_id,
                record.condition,
                record.task_id,
                record.task_tier,
                record.attempt_count,
                record.capability_type,
                record.verification_tier_reached,
                record.gate_passed,
                record.output_in_range,
                record.token_cost,
                record.attempt_latency_ms,
                record.failure_reason,
                record.registry_snapshot_id,
                record.invocation_record_id,
                record.seed,
                record.is_pilot,
                record.human_label,
                record.timestamp,
            ],
        )
        return row_id

    # -- reading -------------------------------------------------------------

    def get_runs(
        self,
        *,
        condition: str | None = None,
        task_id: str | None = None,
        is_pilot: bool | None = None,
        run_id: str | None = None,
    ) -> list[EvaluationRecord]:
        """Return matching records; all filters are optional."""
        clauses: list[str] = []
        params: list[object] = []
        if condition is not None:
            clauses.append("condition = ?")
            params.append(condition)
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if is_pilot is not None:
            clauses.append("is_pilot = ?")
            params.append(is_pilot)
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._conn.execute(
            f"""
            SELECT run_id, condition, task_id, task_tier, attempt_count,
                   capability_type, verification_tier_reached, gate_passed,
                   output_in_range, token_cost, attempt_latency_ms,
                   failure_reason, registry_snapshot_id, invocation_record_id,
                   seed, is_pilot, human_label, timestamp
            FROM evaluation_runs
            {where}
            ORDER BY timestamp
            """,
            params,
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    # -- analytics -----------------------------------------------------------

    def compute_success_rate(
        self,
        condition: str,
        *,
        task_tier: str | None = None,
        exclude_pilot: bool = True,
    ) -> float:
        """Fraction of runs where gate_passed AND output_in_range."""
        clauses = ["condition = ?"]
        params: list[object] = [condition]
        if task_tier is not None:
            clauses.append("task_tier = ?")
            params.append(task_tier)
        if exclude_pilot:
            clauses.append("is_pilot = FALSE")
        where = "WHERE " + " AND ".join(clauses)
        row = self._conn.execute(
            f"""
            SELECT
                AVG(CAST(gate_passed AND output_in_range AS DOUBLE))
            FROM evaluation_runs
            {where}
            """,
            params,
        ).fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0

    def compute_gate_pass_rate(
        self, condition: str, *, exclude_pilot: bool = True
    ) -> float:
        """Fraction of runs where the verification gate passed."""
        clauses = ["condition = ?"]
        params: list[object] = [condition]
        if exclude_pilot:
            clauses.append("is_pilot = FALSE")
        where = "WHERE " + " AND ".join(clauses)
        row = self._conn.execute(
            f"SELECT AVG(CAST(gate_passed AS DOUBLE)) FROM evaluation_runs {where}",
            params,
        ).fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0

    def run_count(self, *, exclude_pilot: bool = True) -> int:
        """Total number of evaluation records."""
        clause = "WHERE is_pilot = FALSE" if exclude_pilot else ""
        row = self._conn.execute(
            f"SELECT COUNT(*) FROM evaluation_runs {clause}"
        ).fetchone()
        return row[0] if row else 0

    def failure_breakdown(
        self, condition: str, *, exclude_pilot: bool = True
    ) -> dict[str, int]:
        """Count of each failure_reason for a given condition."""
        clauses = ["condition = ?"]
        params: list[object] = [condition]
        if exclude_pilot:
            clauses.append("is_pilot = FALSE")
        where = "WHERE " + " AND ".join(clauses)
        rows = self._conn.execute(
            f"""
            SELECT failure_reason, COUNT(*) AS n
            FROM evaluation_runs
            {where}
            GROUP BY failure_reason
            ORDER BY n DESC
            """,
            params,
        ).fetchall()
        return {r[0]: r[1] for r in rows}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _row_to_record(row: tuple) -> EvaluationRecord:
    (
        run_id, condition, task_id, task_tier, attempt_count,
        capability_type, verification_tier_reached, gate_passed,
        output_in_range, token_cost, attempt_latency_ms,
        failure_reason, registry_snapshot_id, invocation_record_id,
        seed, is_pilot, human_label, timestamp,
    ) = row
    # DuckDB may return naive datetimes — normalise to UTC-aware
    if isinstance(timestamp, datetime) and timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return EvaluationRecord(
        run_id=run_id,
        condition=BaselineCondition(condition),
        task_id=task_id,
        task_tier=TaskTier(task_tier),
        attempt_count=attempt_count,
        capability_type=capability_type,
        verification_tier_reached=verification_tier_reached,
        gate_passed=gate_passed,
        output_in_range=output_in_range,
        token_cost=token_cost,
        attempt_latency_ms=attempt_latency_ms,
        failure_reason=FailureReason(failure_reason),
        registry_snapshot_id=registry_snapshot_id,
        invocation_record_id=invocation_record_id,
        seed=seed,
        is_pilot=is_pilot,
        human_label=human_label,
        timestamp=timestamp,
    )
