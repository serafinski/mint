"""Per-task cycle-state flush helper for longitudinal + harness runs.

Step 18.7 landed the persistent cycle-state tables (``invocation_events``,
``cycle_utility_scores``, ``cycle_events``) + wired ``EvaluationHarness.
_execute_run`` to flush per-task data after each ``pipeline.invoke()``.
Step 18.8 surfaced that ``scripts/run_longitudinal.py`` and the new
``scripts/run_longitudinal_shift.py`` inline their own task loops — they
bypass the harness — so without a shared helper they'd also bypass the
flush hooks. This module factors the flush logic out so both the harness
(``tests.evaluation.harness._execute_run``) and the longitudinal scripts
call the exact same code path. Never-raises contract: any flush failure
emits a warning log and proceeds — flush failure must not break the
surrounding eval/longitudinal loop.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def flush_task_cycle_state(
    *,
    eval_store: Any,
    metrics_store: Any,
    registry: Any,
    run_id: str,
    condition: str,
    task_id: str,
    task_events_cutoff: datetime,
) -> None:
    """Flush per-task cycle state to EvaluationStore.

    Copies three batches from the in-memory ``metrics_store`` + fresh
    ``compute_all_utilities(registry, metrics_store)`` to the persistent
    ``eval_store``, keyed by ``(run_id, condition, task_id)``:

    - ``invocation_events`` — all invocations recorded against ``task_id``
      during this task's pipeline run (Step 18.5).
    - ``cycle_utility_scores`` — per-capability utility snapshot computed
      now; mirrors the online ``src.optimizer.loop._compute_kl_divergence``
      input, enabling proposal-literal KL convergence analysis (Step 18.7).
    - ``cycle_events`` — ``capability_events`` rows written during this
      task (prune / split / merged / verification_rollback / etc.), filtered
      via ``fetch_events_since(task_events_cutoff)``. Used for per-type
      operation-count analysis (Step 18.7).

    All three flushes are individually try/except-wrapped so one failure
    doesn't cascade. Caller must capture ``task_events_cutoff`` BEFORE the
    task's pipeline invocation so only this task's events are picked up.

    Args:
        eval_store: EvaluationStore instance (persistent DB connection).
        metrics_store: MetricsStore instance (in-memory per run).
        registry: SkillRegistry instance (passed to compute_all_utilities).
        run_id: The run ID for this longitudinal/harness run.
        condition: BaselineCondition value (e.g. "full", "longitudinal").
        task_id: The task_id of the just-completed task.
        task_events_cutoff: datetime captured BEFORE pipeline.invoke —
            used to filter capability_events to just this task's events.
    """
    # --- Invocation events (Step 18.5) ---
    try:
        task_invocations = metrics_store.fetch_invocations_for_task(task_id)
        eval_store.record_invocation_events(
            run_id=run_id,
            condition=condition,
            task_id=task_id,
            events=task_invocations,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to flush invocation_events for task %s: %s", task_id, exc
        )

    # --- Cycle utility scores (Step 18.7) ---
    try:
        from src.optimizer.utility import compute_all_utilities
        utilities = compute_all_utilities(registry, metrics_store)
        eval_store.record_cycle_utility_scores(
            run_id=run_id,
            condition=condition,
            task_id=task_id,
            scores=utilities,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to flush cycle_utility_scores for task %s: %s", task_id, exc
        )

    # --- Cycle events (Step 18.7) ---
    try:
        task_events = metrics_store.fetch_events_since(task_events_cutoff)
        eval_store.record_cycle_events(
            run_id=run_id,
            condition=condition,
            task_id=task_id,
            events=task_events,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to flush cycle_events for task %s: %s", task_id, exc
        )


def now_utc() -> datetime:
    """Wall-clock cutoff suitable for the ``task_events_cutoff`` arg.

    Thin wrapper so callers don't need to import ``datetime`` just for
    this one use; keeps the flush-helper module self-contained.
    """
    return datetime.now(UTC)
