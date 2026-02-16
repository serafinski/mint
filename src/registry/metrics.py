from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import duckdb

from src.schemas.metrics import GranularityMetrics, InvocationRecord

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS invocations (
    id VARCHAR PRIMARY KEY,
    capability_id VARCHAR NOT NULL,
    task_id VARCHAR NOT NULL,
    success BOOLEAN NOT NULL,
    token_cost INTEGER NOT NULL DEFAULT 0,
    latency_ms DOUBLE NOT NULL DEFAULT 0.0,
    error_message VARCHAR,
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


class MetricsStore:
    """DuckDB-backed metrics store for capability invocations and granularity metrics."""

    def __init__(self, *, db_path: str | None = None) -> None:
        """Initialize DuckDB connection. If *db_path* is ``None``, use in-memory."""
        self._conn = duckdb.connect(db_path or ":memory:")
        self._conn.execute(_INIT_SQL)

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
                                     token_cost, latency_ms, error_message, invoked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                uuid.uuid4().hex,
                record.capability_id,
                record.task_id,
                record.success,
                record.token_cost,
                record.latency_ms,
                record.error_message,
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

    def invocation_count(self, capability_id: str) -> int:
        """Total invocations of this capability."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM invocations WHERE capability_id = ?",
            [capability_id],
        ).fetchone()
        return row[0] if row else 0

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
                   token_cost, latency_ms, error_message, invoked_at
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
                invoked_at=r[6],
            )
            for r in rows
        ]
