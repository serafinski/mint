"""DuckDB-backed store for evaluation run records (Iteration 5).

Separate from the operational MetricsStore — this table tracks experimental
outcomes across the five baseline conditions for thesis analysis.
"""

from __future__ import annotations

import json
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
    id                        VARCHAR PRIMARY KEY,
    run_id                    VARCHAR NOT NULL,
    condition                 VARCHAR NOT NULL,
    task_id                   VARCHAR NOT NULL,
    task_tier                 VARCHAR NOT NULL,
    attempt_count             INTEGER NOT NULL DEFAULT 0,
    capability_type           VARCHAR,
    verification_tier_reached INTEGER,
    gate_passed               BOOLEAN NOT NULL,
    output_in_range           BOOLEAN NOT NULL,
    token_cost                INTEGER NOT NULL DEFAULT 0,
    attempt_latency_ms        INTEGER NOT NULL DEFAULT 0,
    failure_reason            VARCHAR NOT NULL,
    registry_snapshot_id      VARCHAR NOT NULL,
    invocation_record_id      VARCHAR,
    seed                      INTEGER NOT NULL,
    is_pilot                  BOOLEAN NOT NULL DEFAULT FALSE,
    human_label               VARCHAR,
    required_capability_count INTEGER NOT NULL DEFAULT 0,
    matched_in_registry_count INTEGER NOT NULL DEFAULT 0,
    chain_length              INTEGER NOT NULL DEFAULT 1,
    chain_success             BOOLEAN NOT NULL DEFAULT FALSE,
    matched_and_executed_count INTEGER NOT NULL DEFAULT 0,
    generated_artifact_count  INTEGER NOT NULL DEFAULT 0,
    capability_types          VARCHAR NOT NULL DEFAULT '[]',
    failed_step_index         INTEGER,
    failed_step_name          VARCHAR,
    output_shape_mismatch     BOOLEAN NOT NULL DEFAULT FALSE,
    output_shape_missing_keys VARCHAR NOT NULL DEFAULT '[]',
    output_shape_missing_files VARCHAR NOT NULL DEFAULT '[]',
    timestamp                 TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

# Idempotent migrations for upgrading existing DBs to the Stage 1
# compositional schema. DuckDB does NOT support `ADD COLUMN ... NOT NULL
# DEFAULT ...` via ALTER (Parser Error: "Adding columns with constraints
# not yet supported"), so the ALTER variants omit constraints. New rows
# inserted via record_run() always supply explicit values, and old rows
# with NULL are coerced to safe defaults in _row_to_record().
_MIGRATIONS = [
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS chain_length INTEGER",
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS chain_success BOOLEAN",
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS matched_and_executed_count INTEGER",
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS generated_artifact_count INTEGER",
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS capability_types VARCHAR",
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS failed_step_index INTEGER",
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS failed_step_name VARCHAR",
    # Step 14.6 — output contract check
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS output_shape_mismatch BOOLEAN",
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS output_shape_missing_keys VARCHAR",
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS output_shape_missing_files VARCHAR",
]


class EvaluationStore:
    """DuckDB-backed store for evaluation run records."""

    def __init__(self, *, db_path: str | None = None) -> None:
        self._conn = duckdb.connect(db_path or ":memory:")
        self._conn.execute(_INIT_SQL)
        # Idempotent ALTERs so existing DBs gain the Stage 1 compositional columns.
        for stmt in _MIGRATIONS:
            self._conn.execute(stmt)

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
                seed, is_pilot, human_label,
                required_capability_count, matched_in_registry_count,
                chain_length, chain_success, matched_and_executed_count,
                generated_artifact_count, capability_types,
                failed_step_index, failed_step_name,
                output_shape_mismatch, output_shape_missing_keys, output_shape_missing_files,
                timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                record.required_capability_count,
                record.matched_in_registry_count,
                record.chain_length,
                record.chain_success,
                record.matched_and_executed_count,
                record.generated_artifact_count,
                json.dumps(record.capability_types),
                record.failed_step_index,
                record.failed_step_name,
                record.output_shape_mismatch,
                json.dumps(record.output_shape_missing_keys),
                json.dumps(record.output_shape_missing_files),
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
                   seed, is_pilot, human_label,
                   required_capability_count, matched_in_registry_count,
                   chain_length, chain_success, matched_and_executed_count,
                   generated_artifact_count, capability_types,
                   failed_step_index, failed_step_name,
                   output_shape_mismatch, output_shape_missing_keys, output_shape_missing_files,
                   timestamp
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
        seed, is_pilot, human_label,
        required_capability_count, matched_in_registry_count,
        chain_length, chain_success, matched_and_executed_count,
        generated_artifact_count, capability_types,
        failed_step_index, failed_step_name,
        output_shape_mismatch, output_shape_missing_keys, output_shape_missing_files,
        timestamp,
    ) = row
    # DuckDB may return naive datetimes — normalise to UTC-aware
    if isinstance(timestamp, datetime) and timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    try:
        capability_types_list = json.loads(capability_types) if capability_types else []
    except (ValueError, TypeError):
        capability_types_list = []

    def _decode_str_list(blob: object) -> list[str]:
        if not blob:
            return []
        try:
            value = json.loads(blob) if isinstance(blob, str) else blob
        except (ValueError, TypeError):
            return []
        return [str(x) for x in value] if isinstance(value, list) else []

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
        required_capability_count=required_capability_count or 0,
        matched_in_registry_count=matched_in_registry_count or 0,
        chain_length=chain_length if chain_length is not None else 1,
        chain_success=bool(chain_success) if chain_success is not None else False,
        matched_and_executed_count=matched_and_executed_count or 0,
        generated_artifact_count=generated_artifact_count or 0,
        capability_types=capability_types_list,
        failed_step_index=failed_step_index,
        failed_step_name=failed_step_name,
        output_shape_mismatch=bool(output_shape_mismatch) if output_shape_mismatch is not None else False,
        output_shape_missing_keys=_decode_str_list(output_shape_missing_keys),
        output_shape_missing_files=_decode_str_list(output_shape_missing_files),
        timestamp=timestamp,
    )
