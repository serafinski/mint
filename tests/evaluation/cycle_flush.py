"""Per-task cycle-state flush helper for longitudinal + harness runs.

Step 18.7 landed the persistent cycle-state tables (``invocation_events``,
``cycle_utility_scores``, ``cycle_events``) + wired ``EvaluationHarness.
_execute_run`` to flush per-task data after each ``pipeline.invoke()``.
Step 18.8 surfaced that ``scripts/run_longitudinal.py`` and the new
``scripts/run_longitudinal_shift.py`` inline their own task loops — they
bypass the harness — so without a shared helper they'd also bypass the
flush hooks. This module factors the flush logic out so both the harness
(``tests.evaluation.harness._execute_run``) and the longitudinal scripts
call the exact same code path.

Never-raises-but-surfaces contract: ext13 #125 (2026-04-21) — the three
persistence writes (`invocation_events`, `cycle_utility_scores`,
`cycle_events`) still each swallow exceptions so a transient DB hiccup
doesn't break the surrounding 5-hour campaign, but the helper now returns
a :class:`FlushResult` reporting per-batch success. Callers (longitudinal
runners, harness) aggregate these and exit non-zero at campaign-end when
any flush batch failed — partial flush is silent no longer.
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

    ext13 #125: the helper used to return ``None``, with broad-except
    logging warnings internally — callers had no mechanism to detect
    partial flush. Every batch now sets its own boolean and collected
    errors; ``all_ok`` drives the runner's exit-code decision.
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
    task_events_cutoff: datetime,
    is_pilot: bool = False,
) -> FlushResult:
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

    Returns:
        A :class:`FlushResult` with per-batch ``*_ok`` flags and an
        ``errors`` list. Never raises; callers must inspect the result
        (ext13 #125).
    """
    result = FlushResult()

    # --- Invocation events (Step 18.5) ---
    # Security audit finding #23 (2026-04-20): pass ``since=task_events_cutoff``
    # so fetch returns only THIS cycle's invocations. Pre-fix the fetch
    # returned all rows for the task_id, duplicating cycle-1 invocations
    # into every subsequent flush when tasks repeated in the sequence
    # (default `--n-tasks=40` over 20 testbed tasks). Gini + invocation-
    # count KL were inflated.
    try:
        task_invocations = metrics_store.fetch_invocations_for_task(
            task_id, since=task_events_cutoff,
        )
        eval_store.record_invocation_events(
            run_id=run_id,
            condition=condition,
            task_id=task_id,
            events=task_invocations,
            is_pilot=is_pilot,  # ext7 #50
        )
        result.invocations_ok = True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to flush invocation_events for task %s: %s", task_id, exc
        )
        result.errors.append(f"invocation_events: {exc}")

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
        result.utility_scores_ok = True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to flush cycle_utility_scores for task %s: %s", task_id, exc
        )
        result.errors.append(f"cycle_utility_scores: {exc}")

    # --- Cycle events (Step 18.7) ---
    try:
        task_events = metrics_store.fetch_events_since(task_events_cutoff)
        eval_store.record_cycle_events(
            run_id=run_id,
            condition=condition,
            task_id=task_id,
            events=task_events,
        )
        result.events_ok = True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to flush cycle_events for task %s: %s", task_id, exc
        )
        result.errors.append(f"cycle_events: {exc}")

    # Step 13.7 Batch C2: emit a LangFuse cross-reference tag linking
    # the active trace to the just-flushed DuckDB row keys. Chapter 5/6
    # reviewers can then navigate LangFuse → DuckDB or vice versa
    # without a timestamp join. Never raises; informational only so
    # failure here doesn't flip any FlushResult flag.
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
