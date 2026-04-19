from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import duckdb

from src.schemas.metrics import GranularityMetrics, InvocationRecord

# Cap per-row JSON payload size so DuckDB rows don't bloat on large
# tool outputs (e.g., a multi-MB CSV blob stuffed into a result dict).
# 64 KiB is comfortably above typical kwargs + output shapes and well
# below DuckDB's VARCHAR limits. Truncated payloads keep the row
# queryable and partially replayable; the coverage verifier's
# wrong-output-compare still fires as long as parent + variant
# truncate identically.
_INVOCATION_FIELD_MAX_BYTES = 64 * 1024

# Conservative error-shape key list. A non-empty dict is truthy in
# Python, so ``bool({"error": "..."})`` evaluates True — the old
# ``success=bool(output)`` predicate mis-classified error-shaped
# returns as successes, which then failed the ``success=FALSE``
# filter in ``get_recent_failures`` and never reached the Pillar 3
# Split coverage path. Key list is intentionally narrow (not a
# generic "any key containing 'error'" heuristic) to avoid
# misfiring on legitimate outputs like ``{"error_count": 0}``.
_ERROR_KEY_CANDIDATES: frozenset[str] = frozenset(
    {"error", "exception", "traceback", "sandbox_error"}
)


def serialize_for_invocation(value: Any) -> str | None:
    """Serialize ``value`` to a JSON string for ``InvocationRecord`` fields.

    Returns ``None`` when:
    - ``value`` is ``None``
    - ``value`` is a dict-like but empty (no information to record)
    - JSON serialization raises (unserializable sentinel, cyclic ref)

    Uses ``default=str`` to tolerate non-JSON-native types (``Path``,
    ``datetime``, ``Decimal``, custom objects).

    **Truncation policy (security audit finding #16, 2026-04-20):** when
    the serialized payload exceeds ``_INVOCATION_FIELD_MAX_BYTES``,
    returns a **valid-JSON sentinel** carrying the original size rather
    than a broken string-literal. Pre-fix the function emitted
    ``f'{head}..."__TRUNCATED__"'`` which did not parse and caused every
    downstream ``json.loads`` (Pillar 3 coverage replay in particular)
    to silently treat the case as unreproducible. The sentinel shape
    is ``{"__truncated__": true, "__original_size_bytes__": N}`` — small,
    valid, detectable by consumers via ``parsed.get("__truncated__")``.
    """
    if value is None:
        return None
    if isinstance(value, Mapping) and not value:
        return None
    try:
        serialized = json.dumps(value, default=str, sort_keys=True)
    except (TypeError, ValueError):
        return None
    size = len(serialized.encode("utf-8"))
    if size > _INVOCATION_FIELD_MAX_BYTES:
        return json.dumps(
            {"__truncated__": True, "__original_size_bytes__": size},
            sort_keys=True,
        )
    return serialized


def looks_like_error(output: Any) -> bool:
    """True iff ``output`` is a dict carrying one of the error-shape keys.

    Used by the two production ``InvocationRecord`` writers to distinguish
    genuinely-successful tool outputs from error-shaped returns that
    ``bool(output)`` would otherwise accept as successes.
    """
    if not isinstance(output, Mapping):
        return False
    return any(k in output for k in _ERROR_KEY_CANDIDATES)


def extract_error_message(output: Any) -> str | None:
    """Pull a short error string from an error-shaped output for ``error_message``.

    Returns the first error-key value as a string, truncated to 500
    chars. ``None`` when the output has no error key.
    """
    if not isinstance(output, Mapping):
        return None
    for key in _ERROR_KEY_CANDIDATES:
        if key in output:
            raw = output[key]
            text = str(raw) if raw is not None else ""
            return text[:500] if text else None
    return None

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS invocations (
    id VARCHAR PRIMARY KEY,
    capability_id VARCHAR NOT NULL,
    task_id VARCHAR NOT NULL,
    success BOOLEAN NOT NULL,
    token_cost INTEGER NOT NULL DEFAULT 0,
    latency_ms DOUBLE NOT NULL DEFAULT 0.0,
    error_message VARCHAR,
    input_args VARCHAR,
    actual_output VARCHAR,
    invoked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS capability_events (
    id VARCHAR PRIMARY KEY,
    capability_id VARCHAR NOT NULL,
    event_type VARCHAR NOT NULL,
    details VARCHAR,
    occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

# Step 4 (B1 Pillar 3, 2026-04-15): forward-migrate existing invocation
# tables by adding ``input_args`` and ``actual_output`` as nullable
# columns. ADD COLUMN IF NOT EXISTS is idempotent — no-op on fresh
# databases (the columns are already in _INIT_SQL) and safe to run
# on every MetricsStore construction. Old rows get NULL; new rows get
# whatever the caller serialized. Needed by Step 8's split operation
# which reads recent failures and replays their inputs against new
# variants in the Tier 4 coverage verifier.
_MIGRATION_SQL = """
ALTER TABLE invocations ADD COLUMN IF NOT EXISTS input_args VARCHAR;
ALTER TABLE invocations ADD COLUMN IF NOT EXISTS actual_output VARCHAR;
"""


class MetricsStore:
    """DuckDB-backed metrics store for capability invocations and granularity metrics."""

    def __init__(self, *, db_path: str | None = None) -> None:
        """Initialize DuckDB connection. If *db_path* is ``None``, use in-memory."""
        if db_path and db_path != ":memory:":
            from pathlib import Path
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(db_path or ":memory:")
        self._conn.execute(_INIT_SQL)
        # Forward-migrate existing invocation tables (Step 4). Idempotent
        # via ADD COLUMN IF NOT EXISTS — safe to run on fresh databases
        # and on databases created before 2026-04-15.
        self._conn.execute(_MIGRATION_SQL)

    # -- lifecycle --------------------------------------------------------

    def close(self) -> None:
        """Close the DuckDB connection."""
        self._conn.close()

    def __enter__(self) -> MetricsStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass

    # -- recording --------------------------------------------------------

    def record_invocation(self, record: InvocationRecord) -> None:
        """Insert an invocation row from the Pydantic model."""
        self._conn.execute(
            """
            INSERT INTO invocations (id, capability_id, task_id, success,
                                     token_cost, latency_ms, error_message,
                                     input_args, actual_output, invoked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                uuid.uuid4().hex,
                record.capability_id,
                record.task_id,
                record.success,
                record.token_cost,
                record.latency_ms,
                record.error_message,
                record.input_args,
                record.actual_output,
                record.invoked_at,
            ],
        )

    def record_event(
        self, capability_id: str, event_type: str, details: str = ""
    ) -> None:
        """Insert a capability lifecycle event."""
        self._conn.execute(
            """
            INSERT INTO capability_events (id, capability_id, event_type, details, occurred_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [uuid.uuid4().hex, capability_id, event_type, details, datetime.now(UTC)],
        )

    # -- four granularity metrics -----------------------------------------

    def reuse_rate(self, capability_id: str, *, window: int = 100) -> float:
        """Among the last *window* unique tasks, what fraction invoked this capability?"""
        row = self._conn.execute(
            """
            WITH recent_tasks AS (
                SELECT DISTINCT task_id, MAX(invoked_at) AS latest
                FROM invocations
                GROUP BY task_id
                ORDER BY latest DESC
                LIMIT ?
            )
            SELECT
                COUNT(DISTINCT CASE
                    WHEN i.capability_id = ? THEN rt.task_id
                END) * 1.0 / GREATEST(COUNT(DISTINCT rt.task_id), 1)
            FROM recent_tasks rt
            LEFT JOIN invocations i
                ON i.task_id = rt.task_id AND i.capability_id = ?
            """,
            [window, capability_id, capability_id],
        ).fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0

    def success_rate(self, capability_id: str, *, window: int = 100) -> float:
        """Among the last *window* invocations of this capability, what fraction succeeded?"""
        row = self._conn.execute(
            """
            WITH recent AS (
                SELECT success
                FROM invocations
                WHERE capability_id = ?
                ORDER BY invoked_at DESC
                LIMIT ?
            )
            SELECT AVG(CAST(success AS DOUBLE)) FROM recent
            """,
            [capability_id, window],
        ).fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0

    def avg_token_cost(self, capability_id: str, *, window: int = 100) -> float:
        """Average token cost of the last *window* invocations of this capability."""
        row = self._conn.execute(
            """
            WITH recent AS (
                SELECT token_cost
                FROM invocations
                WHERE capability_id = ?
                ORDER BY invoked_at DESC
                LIMIT ?
            )
            SELECT AVG(token_cost) FROM recent
            """,
            [capability_id, window],
        ).fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0

    def maintenance_burden(self, capability_id: str, *, window_days: int = 30) -> float:
        """Maintenance events (regenerated, patched) per day over the window."""
        cutoff = datetime.now(UTC) - timedelta(days=window_days)
        row = self._conn.execute(
            """
            SELECT COUNT(*)
            FROM capability_events
            WHERE capability_id = ?
              AND event_type IN ('regenerated', 'patched')
              AND occurred_at >= ?
            """,
            [capability_id, cutoff],
        ).fetchone()
        count = row[0] if row else 0
        return count / max(window_days, 1)

    # -- aggregate --------------------------------------------------------

    def compute_granularity_metrics(
        self,
        capability_id: str,
        *,
        window: int = 100,
        window_days: int = 30,
    ) -> GranularityMetrics:
        """Compute all four metrics and return as a GranularityMetrics schema."""
        return GranularityMetrics(
            capability_id=capability_id,
            reuse_rate=self.reuse_rate(capability_id, window=window),
            success_rate=self.success_rate(capability_id, window=window),
            token_cost=self.avg_token_cost(capability_id, window=window),
            maintenance_burden=self.maintenance_burden(
                capability_id, window_days=window_days
            ),
            window_size=window,
        )

    # -- utility helpers --------------------------------------------------

    def invocations_since(self, capability_id: str, cutoff: datetime) -> int:
        """Count invocations of a capability since the given cutoff datetime."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM invocations WHERE capability_id = ? AND invoked_at >= ?",
            [capability_id, cutoff],
        ).fetchone()
        return row[0] if row else 0

    def invocation_count(self, capability_id: str) -> int:
        """Total invocations of this capability."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM invocations WHERE capability_id = ?",
            [capability_id],
        ).fetchone()
        return row[0] if row else 0

    def fetch_events_since(self, cutoff: datetime) -> list[dict]:
        """Return all capability_events with occurred_at >= cutoff.

        Used by the evaluation harness to flush per-task capability_events
        (prune / split / merged / verification_rollback / etc.) to the
        persistent EvaluationStore's cycle_events table after each task's
        pipeline.invoke (Step 18.7 follow-up). Caller passes a pre-task
        timestamp snapshot so only events from this task's pipeline run
        are returned.

        Each returned dict matches the shape
        ``EvaluationStore.record_cycle_events`` expects:
        ``{capability_id, event_type, details}``.

        Returns an empty list if no events have occurred since cutoff.
        """
        rows = self._conn.execute(
            """
            SELECT capability_id, event_type, details
            FROM capability_events
            WHERE occurred_at >= ?
            ORDER BY occurred_at
            """,
            [cutoff],
        ).fetchall()
        return [
            {
                "capability_id": row[0],
                "event_type": row[1],
                "details": row[2] or "",
            }
            for row in rows
        ]

    def fetch_invocations_for_task(self, task_id: str) -> list[dict]:
        """Return all invocations recorded against a given task_id.

        Used by the evaluation harness to flush per-task invocations to
        the persistent EvaluationStore's ``invocation_events`` table
        after each task's chain execution completes (Step 18.5 Gini
        analysis pipeline). Each returned dict matches the shape
        ``EvaluationStore.record_invocation_events`` expects.

        Returns an empty list if no invocations exist for the task.
        """
        rows = self._conn.execute(
            """
            SELECT capability_id, success, token_cost, latency_ms
            FROM invocations
            WHERE task_id = ?
            ORDER BY invoked_at
            """,
            [task_id],
        ).fetchall()
        return [
            {
                "capability_id": row[0],
                "success": bool(row[1]),
                "token_cost": int(row[2] or 0),
                "latency_ms": int(row[3] or 0),
            }
            for row in rows
        ]

    def invocation_count_total(self) -> int:
        """Total invocations across all capabilities."""
        row = self._conn.execute("SELECT COUNT(*) FROM invocations").fetchone()
        return row[0] if row else 0

    def flag_most_recent_invocation_wrong(
        self,
        reason: str,
        *,
        capability_id: str | None = None,
    ) -> InvocationRecord | None:
        """Mark the most recent invocation as ``success=False`` with a user reason.

        Implements §12 finding #6b — the CLI feedback loop so Step 8 split's
        wrong-output-case semantics can fire on subsequent splits. Without
        this, CLI ``InvocationRecord``s only ever get ``success=False`` from
        crashes (``error_message`` populated by the executor); user-judgment
        wrong-output-flagging had no path into the metrics store.

        The flagged row's ``error_message`` is set to ``reason`` (so the
        coverage verifier's error-case branch can read it) while preserving
        the row's existing ``actual_output`` (so the wrong-output-case branch
        can compare against it).

        Args:
            reason: User-provided reason the output was wrong. Stored as
                ``error_message`` so downstream queries can distinguish
                user-flagged-wrong rows from crashes by the reason text.
            capability_id: When provided, flag the most recent invocation of
                THIS capability. When ``None``, flag the most recent
                invocation across all capabilities.

        Returns:
            The flagged ``InvocationRecord`` (post-update), or ``None`` when
            no matching invocation exists (nothing to flag).

        Also records a ``user_flagged_wrong`` capability event via
        :meth:`record_event` so Chapter 6 can count CLI user-feedback signal.
        """
        if capability_id is not None:
            query = (
                "SELECT id, capability_id, task_id, success, token_cost, "
                "latency_ms, error_message, input_args, actual_output, "
                "invoked_at FROM invocations WHERE capability_id = ? "
                "ORDER BY invoked_at DESC LIMIT 1"
            )
            params: list = [capability_id]
        else:
            query = (
                "SELECT id, capability_id, task_id, success, token_cost, "
                "latency_ms, error_message, input_args, actual_output, "
                "invoked_at FROM invocations ORDER BY invoked_at DESC LIMIT 1"
            )
            params = []

        row = self._conn.execute(query, params).fetchone()
        if row is None:
            return None

        row_id = row[0]
        self._conn.execute(
            """
            UPDATE invocations
            SET success = FALSE, error_message = ?
            WHERE id = ?
            """,
            [reason, row_id],
        )

        self.record_event(row[1], "user_flagged_wrong", reason)

        return InvocationRecord(
            capability_id=row[1],
            task_id=row[2],
            success=False,
            token_cost=row[4],
            latency_ms=row[5],
            error_message=reason,
            input_args=row[7],
            actual_output=row[8],
            invoked_at=row[9],
        )

    def list_capabilities(self) -> list[str]:
        """All unique capability IDs that have been invoked."""
        rows = self._conn.execute(
            "SELECT DISTINCT capability_id FROM invocations ORDER BY capability_id"
        ).fetchall()
        return [r[0] for r in rows]

    def recent_invocations(
        self, capability_id: str, *, limit: int = 10
    ) -> list[InvocationRecord]:
        """Most recent invocations, newest first."""
        rows = self._conn.execute(
            """
            SELECT capability_id, task_id, success,
                   token_cost, latency_ms, error_message,
                   input_args, actual_output, invoked_at
            FROM invocations
            WHERE capability_id = ?
            ORDER BY invoked_at DESC
            LIMIT ?
            """,
            [capability_id, limit],
        ).fetchall()
        return [
            InvocationRecord(
                capability_id=r[0],
                task_id=r[1],
                success=r[2],
                token_cost=r[3],
                latency_ms=r[4],
                error_message=r[5],
                input_args=r[6],
                actual_output=r[7],
                invoked_at=r[8],
            )
            for r in rows
        ]

    def recent_invocations_all(self, *, limit: int = 20) -> list[InvocationRecord]:
        """Most recent invocations across all capabilities, newest first."""
        rows = self._conn.execute(
            """
            SELECT capability_id, task_id, success,
                   token_cost, latency_ms, error_message,
                   input_args, actual_output, invoked_at
            FROM invocations
            ORDER BY invoked_at DESC
            LIMIT ?
            """,
            [limit],
        ).fetchall()
        return [
            InvocationRecord(
                capability_id=r[0],
                task_id=r[1],
                success=r[2],
                token_cost=r[3],
                latency_ms=r[4],
                error_message=r[5],
                input_args=r[6],
                actual_output=r[7],
                invoked_at=r[8],
            )
            for r in rows
        ]

    def get_recent_failures(
        self, capability_id: str, *, limit: int = 10
    ) -> list[InvocationRecord]:
        """Most recent FAILED invocations for a capability, newest first.

        Used by Step 8's split operation to pull recorded failure cases
        for a high-reuse / low-success capability, which the
        ``verify_coverage`` helper then replays against newly-generated
        split variants. A failure case is an invocation with
        ``success=False``; ``error_message``, ``input_args``, and
        ``actual_output`` are all available on the returned records for
        downstream use.

        Returns records where ``input_args`` is populated AND at least
        one of ``actual_output`` or ``error_message`` is populated:

        - **Error-case** (parent raised): ``error_message`` is set,
          ``actual_output`` is typically None because the parent raised
          before producing output. The coverage verifier's error-case
          branch (:func:`~src.verification.coverage._variant_handles_case`)
          checks whether the variant runs on ``input_args`` without
          raising.
        - **Wrong-output-case** (parent didn't raise but was classified
          as failed): ``actual_output`` is populated with the wrong
          output, ``error_message`` is typically None. The coverage
          verifier's wrong-output branch checks whether the variant
          produces a different output.

        Rows missing ``input_args`` entirely are silently skipped — a
        failure case without a captured input is useless as a replay
        probe. Rows missing BOTH ``actual_output`` AND ``error_message``
        are also skipped — we can't verify a failure we have no record
        of. Pre-Step-4 rows (all three fields NULL) are skipped for
        the same reason.

        **2026-04-16 fix:** an earlier version of this query also
        required ``actual_output IS NOT NULL``, which silently excluded
        every error-case (where parent raised before producing output).
        That made Step 8's error-case coverage branch CLI-dead-code. The
        bug was empirically caught by ``scripts/test_split_merge.py
        --split-mcp-only``'s "Seeded N failure case(s) for replay" log
        line not matching the expected count. See §12 pre-registered
        finding #5 (coverage verifier semantics) for why error-cases
        are the stronger signal anyway.
        """
        rows = self._conn.execute(
            """
            SELECT capability_id, task_id, success,
                   token_cost, latency_ms, error_message,
                   input_args, actual_output, invoked_at
            FROM invocations
            WHERE capability_id = ?
              AND success = FALSE
              AND input_args IS NOT NULL
              AND (actual_output IS NOT NULL OR error_message IS NOT NULL)
            ORDER BY invoked_at DESC
            LIMIT ?
            """,
            [capability_id, limit],
        ).fetchall()
        return [
            InvocationRecord(
                capability_id=r[0],
                task_id=r[1],
                success=r[2],
                token_cost=r[3],
                latency_ms=r[4],
                error_message=r[5],
                input_args=r[6],
                actual_output=r[7],
                invoked_at=r[8],
            )
            for r in rows
        ]
