"""DuckDB-backed store for evaluation campaign records.

Separate from the operational MetricsStore, this store persists campaign
outcomes and per-cycle telemetry for the evaluation protocol and Chapter 6
analysis artifacts.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from src.schemas.evaluation import (
    BaselineCondition,
    EvaluationRecord,
    FailureReason,
    GeneratedArtifactSnapshot,
    TaskTier,
)

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS evaluation_runs (
    id                        VARCHAR PRIMARY KEY,
    run_id                    VARCHAR NOT NULL,
    condition                 VARCHAR NOT NULL,
    task_id                   VARCHAR NOT NULL,
    task_family_id            VARCHAR NOT NULL,
    task_instance_id          VARCHAR NOT NULL,
    variant_id                VARCHAR,
    benchmark_track           VARCHAR NOT NULL DEFAULT 'breadth',
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
    failed_step_reason        VARCHAR,
    output_shape_mismatch     BOOLEAN NOT NULL DEFAULT FALSE,
    output_shape_missing_keys VARCHAR NOT NULL DEFAULT '[]',
    output_shape_missing_files VARCHAR NOT NULL DEFAULT '[]',
    generated_artifact_sources VARCHAR NOT NULL DEFAULT '[]',
    langfuse_trace_id         VARCHAR,
    timestamp                 TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Persistent per-capability invocation log keyed by run identity and task
-- identity. MetricsStore is in-memory for optimizer accumulation within a
-- run; this table persists flushed invocations so analyzers can compute
-- capability-reuse distributions.
CREATE TABLE IF NOT EXISTS invocation_events (
    id                        VARCHAR PRIMARY KEY,
    run_id                    VARCHAR NOT NULL,
    condition                 VARCHAR NOT NULL,
    task_id                   VARCHAR NOT NULL,
    task_family_id            VARCHAR NOT NULL DEFAULT '',
    task_instance_id          VARCHAR NOT NULL DEFAULT '',
    variant_id                VARCHAR,
    benchmark_track           VARCHAR NOT NULL DEFAULT 'breadth',
    capability_id             VARCHAR NOT NULL,
    capability_type           VARCHAR,
    success                   BOOLEAN NOT NULL,
    token_cost                INTEGER NOT NULL DEFAULT 0,
    latency_ms                INTEGER NOT NULL DEFAULT 0,
    -- Mirror evaluation_runs.is_pilot so M3 Gini can exclude pilot
    -- invocations. DuckDB does not support adding a NOT NULL DEFAULT
    -- column through ALTER, so migrated DBs may contain NULL until the
    -- backfill below runs.
    is_pilot                  BOOLEAN,
    occurred_at               TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_invocation_events_condition
    ON invocation_events(condition);
CREATE INDEX IF NOT EXISTS idx_invocation_events_cap
    ON invocation_events(capability_id);

-- Per-cycle utility-score snapshots for KL convergence analysis. The
-- optimizer computes utility distributions online; this table persists
-- those distributions so post-hoc analyzers can reproduce the same KL
-- series instead of using invocation-count proxies.
CREATE TABLE IF NOT EXISTS cycle_utility_scores (
    id                        VARCHAR PRIMARY KEY,
    run_id                    VARCHAR NOT NULL,
    condition                 VARCHAR NOT NULL,
    task_id                   VARCHAR NOT NULL,
    task_family_id            VARCHAR NOT NULL DEFAULT '',
    task_instance_id          VARCHAR NOT NULL DEFAULT '',
    variant_id                VARCHAR,
    benchmark_track           VARCHAR NOT NULL DEFAULT 'breadth',
    capability_id             VARCHAR NOT NULL,
    utility_score             DOUBLE NOT NULL,
    occurred_at               TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cycle_utility_run
    ON cycle_utility_scores(run_id, condition);

-- Per-cycle capability_events log mirroring MetricsStore.capability_events,
-- which is in-memory per run. Enables operation counts (prune / split /
-- merge / verification_rollback / etc.) per cycle in analyzers.
CREATE TABLE IF NOT EXISTS cycle_events (
    id                        VARCHAR PRIMARY KEY,
    run_id                    VARCHAR NOT NULL,
    condition                 VARCHAR NOT NULL,
    task_id                   VARCHAR NOT NULL,
    task_family_id            VARCHAR NOT NULL DEFAULT '',
    task_instance_id          VARCHAR NOT NULL DEFAULT '',
    variant_id                VARCHAR,
    benchmark_track           VARCHAR NOT NULL DEFAULT 'breadth',
    capability_id             VARCHAR NOT NULL,
    event_type                VARCHAR NOT NULL,
    details                   VARCHAR NOT NULL DEFAULT '',
    occurred_at               TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cycle_events_run
    ON cycle_events(run_id, condition);
CREATE INDEX IF NOT EXISTS idx_cycle_events_type
    ON cycle_events(event_type);
"""

# Idempotent migrations for opening older evaluation DBs with the current
# schema. DuckDB does not support `ADD COLUMN ... NOT NULL DEFAULT ...` through
# ALTER, so migrated columns are nullable. New rows inserted by record_run()
# supply explicit values, and old NULLs are coerced in _row_to_record().
_MIGRATIONS = [
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS task_family_id VARCHAR",
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS task_instance_id VARCHAR",
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS variant_id VARCHAR",
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS benchmark_track VARCHAR",
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS chain_length INTEGER",
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS chain_success BOOLEAN",
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS matched_and_executed_count INTEGER",
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS generated_artifact_count INTEGER",
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS capability_types VARCHAR",
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS failed_step_index INTEGER",
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS failed_step_name VARCHAR",
    # Free-text reason string from CompositionalExecutor; lets analysis
    # distinguish chain-step failures from validation failures.
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS failed_step_reason VARCHAR",
    # Output contract check fields.
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS output_shape_mismatch BOOLEAN",
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS output_shape_missing_keys VARCHAR",
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS output_shape_missing_files VARCHAR",
    # Per-record source-body snapshots for optional manual contamination audit.
    # Snapshot isolation resets registry state between runs, so persisted source
    # bodies keep old DBs auditable without reconstructing ChromaDB state.
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS generated_artifact_sources VARCHAR",
    # LangFuse root trace ID cross-reference for debugging without a
    # timestamp/session join.
    "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS langfuse_trace_id VARCHAR",
    # Mirror is_pilot onto invocation_events so M3 Gini can exclude pilot rows.
    # Nullable on migrate; backfilled below.
    "ALTER TABLE invocation_events ADD COLUMN IF NOT EXISTS is_pilot BOOLEAN",
    "ALTER TABLE invocation_events ADD COLUMN IF NOT EXISTS task_family_id VARCHAR",
    "ALTER TABLE invocation_events ADD COLUMN IF NOT EXISTS task_instance_id VARCHAR",
    "ALTER TABLE invocation_events ADD COLUMN IF NOT EXISTS variant_id VARCHAR",
    "ALTER TABLE invocation_events ADD COLUMN IF NOT EXISTS benchmark_track VARCHAR",
    "ALTER TABLE cycle_utility_scores ADD COLUMN IF NOT EXISTS task_family_id VARCHAR",
    "ALTER TABLE cycle_utility_scores ADD COLUMN IF NOT EXISTS task_instance_id VARCHAR",
    "ALTER TABLE cycle_utility_scores ADD COLUMN IF NOT EXISTS variant_id VARCHAR",
    "ALTER TABLE cycle_utility_scores ADD COLUMN IF NOT EXISTS benchmark_track VARCHAR",
    "ALTER TABLE cycle_events ADD COLUMN IF NOT EXISTS task_family_id VARCHAR",
    "ALTER TABLE cycle_events ADD COLUMN IF NOT EXISTS task_instance_id VARCHAR",
    "ALTER TABLE cycle_events ADD COLUMN IF NOT EXISTS variant_id VARCHAR",
    "ALTER TABLE cycle_events ADD COLUMN IF NOT EXISTS benchmark_track VARCHAR",
]

# Backfill is_pilot on older invocation_events rows by joining to the
# evaluation_runs row that produced them. Safe on fresh DBs and idempotent:
# only rows where is_pilot IS NULL are touched.
_INVOCATION_IS_PILOT_BACKFILL_SQL = """
UPDATE invocation_events AS ie
SET is_pilot = COALESCE(er.is_pilot, FALSE)
FROM evaluation_runs AS er
WHERE ie.run_id = er.run_id
  AND ie.task_id = er.task_id
  AND ie.is_pilot IS NULL
"""

_TASK_IDENTITY_BACKFILL_SQL = [
    """
    UPDATE evaluation_runs
    SET task_family_id = task_id
    WHERE task_family_id IS NULL
    """,
    """
    UPDATE evaluation_runs
    SET task_instance_id = task_id
    WHERE task_instance_id IS NULL
    """,
    """
    UPDATE evaluation_runs
    SET benchmark_track = 'breadth'
    WHERE benchmark_track IS NULL
    """,
    """
    UPDATE invocation_events
    SET task_family_id = task_id
    WHERE task_family_id IS NULL
    """,
    """
    UPDATE invocation_events
    SET task_instance_id = task_id
    WHERE task_instance_id IS NULL
    """,
    """
    UPDATE invocation_events
    SET benchmark_track = 'breadth'
    WHERE benchmark_track IS NULL
    """,
    """
    UPDATE cycle_utility_scores
    SET task_family_id = task_id
    WHERE task_family_id IS NULL
    """,
    """
    UPDATE cycle_utility_scores
    SET task_instance_id = task_id
    WHERE task_instance_id IS NULL
    """,
    """
    UPDATE cycle_utility_scores
    SET benchmark_track = 'breadth'
    WHERE benchmark_track IS NULL
    """,
    """
    UPDATE cycle_events
    SET task_family_id = task_id
    WHERE task_family_id IS NULL
    """,
    """
    UPDATE cycle_events
    SET task_instance_id = task_id
    WHERE task_instance_id IS NULL
    """,
    """
    UPDATE cycle_events
    SET benchmark_track = 'breadth'
    WHERE benchmark_track IS NULL
    """,
]


class EvaluationStore:
    """DuckDB-backed store for evaluation run records."""

    def __init__(self, *, db_path: str | None = None) -> None:
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(db_path or ":memory:")
        self._conn.execute(_INIT_SQL)
        # Idempotent ALTERs so existing DBs gain current schema columns.
        for stmt in _MIGRATIONS:
            self._conn.execute(stmt)
        # Backfill invocation_events.is_pilot from evaluation_runs for older
        # rows. No-op on fresh DBs.
        self._conn.execute(_INVOCATION_IS_PILOT_BACKFILL_SQL)
        for stmt in _TASK_IDENTITY_BACKFILL_SQL:
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
                id, run_id, condition, task_id, task_family_id, task_instance_id,
                variant_id, benchmark_track, task_tier,
                attempt_count, capability_type, verification_tier_reached,
                gate_passed, output_in_range, token_cost, attempt_latency_ms,
                failure_reason, registry_snapshot_id, invocation_record_id,
                seed, is_pilot, human_label,
                required_capability_count, matched_in_registry_count,
                chain_length, chain_success, matched_and_executed_count,
                generated_artifact_count, capability_types,
                failed_step_index, failed_step_name, failed_step_reason,
                output_shape_mismatch, output_shape_missing_keys, output_shape_missing_files,
                generated_artifact_sources, langfuse_trace_id,
                timestamp
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                row_id,
                record.run_id,
                record.condition,
                record.task_id,
                record.task_family_id,
                record.task_instance_id,
                record.variant_id,
                record.benchmark_track,
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
                record.failed_step_reason,
                record.output_shape_mismatch,
                json.dumps(record.output_shape_missing_keys),
                json.dumps(record.output_shape_missing_files),
                json.dumps(
                    [s.model_dump(mode="json") for s in record.generated_artifact_sources]
                ),
                record.langfuse_trace_id,
                record.timestamp,
            ],
        )
        return row_id

    def update_human_label(
        self,
        row_id: str,
        label: str | None,
        *,
        allow_overwrite: bool = False,
    ) -> bool:
        """Set ``human_label`` on one evaluation_runs row.

        This helper supports the optional manual contamination-audit workflow.
        That workflow was deferred out of the Chapter 6 evidence set because
        there was not enough project time to run and cross-check a human audit
        sample. The DB column remains for compatibility and for future audit
        work, so this method keeps a guarded write path instead of requiring
        ad-hoc SQL updates.

        Returns True iff the row existed AND was either unlabeled or
        ``allow_overwrite=True``. Returns False when the row is missing
        OR when another audit session already labeled it (optimistic
        concurrency check).

        The default UPDATE is guarded by ``AND human_label IS NULL`` so two
        audit sessions cannot silently overwrite one another. ``allow_overwrite``
        keeps the ability to clear or relabel when explicitly invoked.

        Raises ``ValueError`` if ``label`` is not a known HumanLabel
        value and not None; this catches typos before a bad value hits
        the DB (the column is VARCHAR and would accept anything).
        """
        valid = {"correct", "contaminated", "partial", None}
        if label not in valid:
            raise ValueError(
                f"label must be one of {sorted(v for v in valid if v)} "
                f"or None; got {label!r}"
            )
        # Check existence first.
        existed = self._conn.execute(
            "SELECT human_label FROM evaluation_runs WHERE id = ? LIMIT 1",
            [row_id],
        ).fetchone()
        if existed is None:
            return False
        prior_label = existed[0]
        if (
            prior_label is not None
            and label is not None
            and not allow_overwrite
        ):
            # Optimistic concurrency: another session already labeled.
            return False
        if allow_overwrite:
            self._conn.execute(
                "UPDATE evaluation_runs SET human_label = ? WHERE id = ?",
                [label, row_id],
            )
        else:
            self._conn.execute(
                "UPDATE evaluation_runs SET human_label = ? "
                "WHERE id = ? AND (human_label IS NULL OR ? IS NULL)",
                [label, row_id, label],
            )
        return True

    def record_invocation_events(
        self,
        *,
        run_id: str,
        condition: str,
        task_id: str,
        task_family_id: str | None = None,
        task_instance_id: str | None = None,
        variant_id: str | None = None,
        benchmark_track: str = "breadth",
        events: list[dict],
        is_pilot: bool = False,
    ) -> int:
        """Persist a batch of capability invocation events for one task-run.

        Each event is a dict with keys: ``capability_id`` (required, str),
        ``capability_type`` (optional str), ``success`` (required bool),
        ``token_cost`` (optional int), ``latency_ms`` (optional int).
        All rows share the same ``run_id`` / ``condition`` / ``task_id``
        / ``is_pilot`` context — that's the unit the harness flushes after
        each task's chain execution completes.

        ``is_pilot`` threads through from the harness so M3 Gini can exclude
        pilot rows via a direct ``WHERE NOT is_pilot`` clause, symmetric with
        evaluation_runs. The default False keeps helper callers simple; the
        harness supplies the real flag during eval runs.

        Returns the number of rows inserted. Zero on empty ``events``.

        This is the persistence layer for capability-reuse Gini analysis. The
        in-memory MetricsStore that the optimizer shares across tasks within
        one run is flushed here to disk so post-eval analysis can compute
        per-run and aggregate reuse distributions.
        """
        if not events:
            return 0
        task_family_id = task_family_id or task_id
        task_instance_id = task_instance_id or task_id
        rows = []
        now = datetime.now(UTC)
        for ev in events:
            rows.append([
                uuid.uuid4().hex,
                run_id,
                condition,
                task_id,
                task_family_id,
                task_instance_id,
                variant_id,
                benchmark_track,
                ev["capability_id"],
                ev.get("capability_type"),
                bool(ev["success"]),
                int(ev.get("token_cost", 0) or 0),
                int(ev.get("latency_ms", 0) or 0),
                bool(is_pilot),
                now,
            ])
        self._conn.executemany(
            """
            INSERT INTO invocation_events (
                id, run_id, condition, task_id, task_family_id, task_instance_id,
                variant_id, benchmark_track, capability_id,
                capability_type, success, token_cost, latency_ms,
                is_pilot, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        return len(rows)

    def record_cycle_utility_scores(
        self,
        *,
        run_id: str,
        condition: str,
        task_id: str,
        task_family_id: str | None = None,
        task_instance_id: str | None = None,
        variant_id: str | None = None,
        benchmark_track: str = "breadth",
        scores: dict[str, float],
        occurred_at: datetime | None = None,
    ) -> int:
        """Persist per-capability utility scores after one task cycle.

        Expected caller: the harness, after ``pipeline.invoke()`` returns,
        calls ``src.optimizer.utility.compute_all_utilities(registry,
        metrics_store)`` and passes the result here. Enables post-hoc KL
        convergence analysis using the actual utility-score distribution, not
        an invocation-count proxy.

        ``occurred_at`` defaults to ``datetime.now(UTC)`` at insert time.
        Tests that need deterministic per-cycle bucketing can pass an explicit
        value to ensure the row lands in its intended cycle.

        Returns number of rows inserted; 0 on empty input.
        """
        if not scores:
            return 0
        task_family_id = task_family_id or task_id
        task_instance_id = task_instance_id or task_id
        now = occurred_at if occurred_at is not None else datetime.now(UTC)
        rows = [
            [
                uuid.uuid4().hex,
                run_id,
                condition,
                task_id,
                task_family_id,
                task_instance_id,
                variant_id,
                benchmark_track,
                cap_name,
                float(score),
                now,
            ]
            for cap_name, score in scores.items()
        ]
        self._conn.executemany(
            """
            INSERT INTO cycle_utility_scores (
                id, run_id, condition, task_id, task_family_id, task_instance_id,
                variant_id, benchmark_track, capability_id,
                utility_score, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        return len(rows)

    def record_cycle_events(
        self,
        *,
        run_id: str,
        condition: str,
        task_id: str,
        task_family_id: str | None = None,
        task_instance_id: str | None = None,
        variant_id: str | None = None,
        benchmark_track: str = "breadth",
        events: list[dict],
        occurred_at: datetime | None = None,
    ) -> int:
        """Persist a batch of capability_events for one task cycle.

        Each event dict: ``capability_id`` (required str), ``event_type``
        (required str — e.g. "pruned", "split", "merged",
        "verification_rollback"), ``details`` (optional str).

        Expected caller: the harness, after ``pipeline.invoke()``, fetches
        MetricsStore.capability_events entries that occurred during that
        task's pipeline invocation (typically via a timestamp cursor) and
        passes them here. Enables true per-type operation counts per cycle.

        ``occurred_at`` defaults to ``datetime.now(UTC)``. Tests that need
        deterministic per-cycle bucketing can pass an explicit value.

        Returns number of rows inserted; 0 on empty input.
        """
        if not events:
            return 0
        task_family_id = task_family_id or task_id
        task_instance_id = task_instance_id or task_id
        now = occurred_at if occurred_at is not None else datetime.now(UTC)
        rows = []
        for ev in events:
            rows.append([
                uuid.uuid4().hex,
                run_id,
                condition,
                task_id,
                task_family_id,
                task_instance_id,
                variant_id,
                benchmark_track,
                ev["capability_id"],
                ev["event_type"],
                ev.get("details", "") or "",
                now,
            ])
        self._conn.executemany(
            """
            INSERT INTO cycle_events (
                id, run_id, condition, task_id, task_family_id, task_instance_id,
                variant_id, benchmark_track, capability_id,
                event_type, details, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        return len(rows)

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
            SELECT run_id, condition, task_id, task_family_id, task_instance_id,
                   variant_id, benchmark_track, task_tier, attempt_count,
                   capability_type, verification_tier_reached, gate_passed,
                   output_in_range, token_cost, attempt_latency_ms,
                   failure_reason, registry_snapshot_id, invocation_record_id,
                   seed, is_pilot, human_label,
                   required_capability_count, matched_in_registry_count,
                   chain_length, chain_success, matched_and_executed_count,
                   generated_artifact_count, capability_types,
                   failed_step_index, failed_step_name, failed_step_reason,
                   output_shape_mismatch, output_shape_missing_keys, output_shape_missing_files,
                   generated_artifact_sources, langfuse_trace_id,
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
        run_id, condition, task_id, task_family_id, task_instance_id,
        variant_id, benchmark_track, task_tier, attempt_count,
        capability_type, verification_tier_reached, gate_passed,
        output_in_range, token_cost, attempt_latency_ms,
        failure_reason, registry_snapshot_id, invocation_record_id,
        seed, is_pilot, human_label,
        required_capability_count, matched_in_registry_count,
        chain_length, chain_success, matched_and_executed_count,
        generated_artifact_count, capability_types,
        failed_step_index, failed_step_name, failed_step_reason,
        output_shape_mismatch, output_shape_missing_keys, output_shape_missing_files,
        generated_artifact_sources, langfuse_trace_id,
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

    def _decode_artifact_sources(blob: object) -> list:
        """Parse the generated_artifact_sources JSON column into snapshots.

        Never raises: malformed JSON or older rows decode to an empty list.
        """
        if not blob:
            return []
        try:
            value = json.loads(blob) if isinstance(blob, str) else blob
        except (ValueError, TypeError):
            return []
        if not isinstance(value, list):
            return []
        out = []
        for item in value:
            if not isinstance(item, dict):
                continue
            try:
                out.append(GeneratedArtifactSnapshot(**item))
            except Exception:  # noqa: BLE001 — never-raises contract
                continue
        return out

    return EvaluationRecord(
        run_id=run_id,
        condition=BaselineCondition(condition),
        task_id=task_id,
        task_family_id=task_family_id or task_id,
        task_instance_id=task_instance_id or task_id,
        variant_id=variant_id,
        benchmark_track=benchmark_track or "breadth",
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
        failed_step_reason=failed_step_reason,
        output_shape_mismatch=(
            bool(output_shape_mismatch)
            if output_shape_mismatch is not None
            else False
        ),
        output_shape_missing_keys=_decode_str_list(output_shape_missing_keys),
        output_shape_missing_files=_decode_str_list(output_shape_missing_files),
        generated_artifact_sources=_decode_artifact_sources(generated_artifact_sources),
        langfuse_trace_id=langfuse_trace_id,
        timestamp=timestamp,
    )
