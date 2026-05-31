"""Per-task cycle-state flush helper for evaluation runs.

The eval harness and longitudinal runner both need to persist telemetry
after each task: invocation events, utility-score snapshots, and optimizer
capability events. This module keeps those writes on one shared path so the
analyzers see the same tables across breadth, recurrence, and longitudinal
tracks.

Contract: never raise while a task campaign is running. Each persistence
write is isolated, and the returned :class:`FlushResult` reports which
batches landed so runners can fail the campaign at the end if telemetry is
partial.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class FlushResult:
    """Per-batch success/failure tally for one ``flush_task_cycle_state`` call.

    Every batch sets its own boolean and appends any error message. Callers
    aggregate these results and use ``all_ok`` to decide whether a campaign
    completed with complete telemetry.
    """

    invocations_ok: bool = False
    utility_scores_ok: bool = False
    events_ok: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return self.invocations_ok and self.utility_scores_ok and self.events_ok


def flush_task_cycle_state(
    *,
    eval_store: Any,
    metrics_store: Any,
    registry: Any,
    run_id: str,
    condition: str,
    task_id: str,
    task_family_id: str | None = None,
    task_instance_id: str | None = None,
    variant_id: str | None = None,
    benchmark_track: str = "breadth",
    task_events_cutoff: datetime,
    is_pilot: bool = False,
) -> FlushResult:
    """Flush per-task cycle state to EvaluationStore.

    Copies three batches from the in-memory ``metrics_store`` + fresh
    ``compute_all_utilities(registry, metrics_store)`` to the persistent
    ``eval_store``, keyed by run/condition/task identity:

    - ``invocation_events`` — all invocations recorded against ``task_id``
      during this task's pipeline run.
    - ``cycle_utility_scores`` — per-capability utility snapshot computed
      now; this is the primary input for KL convergence analysis.
    - ``cycle_events`` — ``capability_events`` rows written during this
      task (prune / split / merged / verification_rollback / etc.), filtered
      via ``fetch_events_since(task_events_cutoff)``. Used for per-type
      operation-count analysis.

    All three flushes are individually try/except-wrapped so one failure
    doesn't cascade. Caller must capture ``task_events_cutoff`` BEFORE the
    task's pipeline invocation so only this task's events are picked up.

    Args:
        eval_store: EvaluationStore instance (persistent DB connection).
        metrics_store: MetricsStore instance (in-memory per run).
        registry: CapabilityRegistry instance (passed to compute_all_utilities).
        run_id: The run ID for this longitudinal/harness run.
        condition: Condition label (e.g. "full", "static", "longitudinal").
        task_id: Canonical task family ID for the just-completed task.
        task_family_id: Stable family-level identifier. Defaults to ``task_id``.
        task_instance_id: Concrete task-instance identifier. Defaults to ``task_id``.
        variant_id: Optional recurrence variant identifier.
        benchmark_track: Benchmark track label (``breadth`` / ``recurrence``).
        task_events_cutoff: datetime captured BEFORE pipeline.invoke —
            used to filter capability_events to just this task's events.

    Returns:
        A :class:`FlushResult` with per-batch ``*_ok`` flags and an
        ``errors`` list. Never raises; callers must inspect the result
        before declaring a campaign clean.
    """
    result = FlushResult()

    task_family_id = task_family_id or task_id
    task_instance_id = task_instance_id or task_id

    # --- Invocation events ---
    # Scope by cutoff so repeated task families in longitudinal/recurrence
    # runs do not copy earlier invocations into later cycles.
    try:
        task_invocations = metrics_store.fetch_invocations_for_task(
            task_id, since=task_events_cutoff,
        )
        eval_store.record_invocation_events(
            run_id=run_id,
            condition=condition,
            task_id=task_id,
            task_family_id=task_family_id,
            task_instance_id=task_instance_id,
            variant_id=variant_id,
            benchmark_track=benchmark_track,
            events=task_invocations,
            is_pilot=is_pilot,
        )
        result.invocations_ok = True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to flush invocation_events for task %s: %s", task_id, exc
        )
        result.errors.append(f"invocation_events: {exc}")

    # --- Cycle utility scores ---
    try:
        from src.optimizer.utility import compute_all_utilities
        utilities = compute_all_utilities(registry, metrics_store)
        eval_store.record_cycle_utility_scores(
            run_id=run_id,
            condition=condition,
            task_id=task_id,
            task_family_id=task_family_id,
            task_instance_id=task_instance_id,
            variant_id=variant_id,
            benchmark_track=benchmark_track,
            scores=utilities,
        )
        result.utility_scores_ok = True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to flush cycle_utility_scores for task %s: %s", task_id, exc
        )
        result.errors.append(f"cycle_utility_scores: {exc}")

    # --- Cycle events ---
    try:
        task_events = metrics_store.fetch_events_since(task_events_cutoff)
        eval_store.record_cycle_events(
            run_id=run_id,
            condition=condition,
            task_id=task_id,
            task_family_id=task_family_id,
            task_instance_id=task_instance_id,
            variant_id=variant_id,
            benchmark_track=benchmark_track,
            events=task_events,
        )
        result.events_ok = True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to flush cycle_events for task %s: %s", task_id, exc
        )
        result.errors.append(f"cycle_events: {exc}")

    # Emit a best-effort LangFuse cross-reference linking the active trace to
    # the flushed DuckDB row keys. This is audit metadata only, so failure here
    # does not affect any FlushResult flag.
    try:
        from src.orchestrator.tracing import emit_langfuse_event
        emit_langfuse_event(
            None,
            "cycle_flush.completed",
            "DEFAULT",
            {
                "run_id": run_id,
                "condition": condition,
                "task_id": task_id,
                "task_family_id": task_family_id,
                "task_instance_id": task_instance_id,
                "variant_id": variant_id,
                "benchmark_track": benchmark_track,
                "cutoff_iso": task_events_cutoff.isoformat(),
            },
        )
    except Exception:  # noqa: BLE001
        pass

    return result


def now_utc() -> datetime:
    """Wall-clock cutoff suitable for the ``task_events_cutoff`` arg.

    Thin wrapper so callers don't need to import ``datetime`` just for
    this one use; keeps the flush-helper module self-contained.
    """
    return datetime.now(UTC)
