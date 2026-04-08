"""Gap detection — LangGraph node wrapping registry.detect_gaps().

Searches the capability registry for each required capability. Matches are
recorded per-capability (populating ``state.matches`` and recording
InvocationRecords for the optimizer). The gap output signals whether the
task needs generation at all — if ANY required capability is unmatched, a
single placeholder gap is produced. The task-level classifier downstream
then classifies the entire task once.

This is a plain Python node — no LLM calls.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Literal

from langgraph.types import Command

from src.registry.metrics import MetricsStore
from src.registry.store import SkillRegistry
from src.schemas.metrics import InvocationRecord
from src.schemas.task import GapItem, MatchItem, TaskState, TaskStatus

logger = logging.getLogger(__name__)


def gap_detection_node(
    state: TaskState,
    registry: SkillRegistry,
    metrics_store: MetricsStore | None = None,
) -> Command[Literal["classifier_node"]]:
    """Search the registry for each required capability.

    Populates ``state.matches`` with capabilities found above the similarity
    threshold. Produces a single placeholder ``GapItem`` in ``state.gaps`` if
    any required capability is unmatched — this signals to the downstream
    classifier that the task needs generation. If all required capabilities
    are matched, ``state.gaps`` is empty and the classifier becomes a no-op.

    Always routes to ``classifier_node``.

    Args:
        state: Current pipeline state with ``required_capabilities`` populated
            by ``decomposition_node``.
        registry: The capability registry to search.
        metrics_store: Optional metrics store for recording match invocations
            (keeps matched capabilities from being pruned as idle).

    Returns:
        Command routing to ``classifier_node`` with updated matches and gaps.
    """
    errors: list[str] = list(state.errors)

    matches_raw, gaps_raw = registry.detect_gaps(state.required_capabilities)

    matches = [
        MatchItem(capability=cap, score=score)
        for cap, score, _query in matches_raw
    ]

    # Record matched capabilities as invocations so they're not pruned as idle.
    # token_cost=0, latency_ms=0.0 are intentional: matches are not executions.
    if metrics_store is not None:
        for m in matches:
            metrics_store.record_invocation(InvocationRecord(
                capability_id=m.capability.name,
                task_id=hashlib.sha256(state.task_description.encode()).hexdigest()[:16],
                success=True,
                token_cost=0,
                latency_ms=0.0,
            ))

    # Produce a single placeholder gap if ANY required capability was unmatched.
    # The task-level classifier downstream will decide the type for the whole
    # task. If gaps_raw is empty, state.gaps stays empty and the classifier is
    # a no-op (all capabilities already in the registry).
    gaps: list[GapItem] = []
    if gaps_raw:
        gaps = [
            GapItem(
                description=state.task_description[:300],
                query="task-level",
            )
        ]

    logger.info(
        "Gap detection: %d match(es), %d unmatched capability(ies) → %d gap(s) for classification",
        len(matches),
        len(gaps_raw),
        len(gaps),
    )
    for m in matches:
        logger.info("  ✓ matched: %s (score=%.2f)", m.capability.name, m.score)
    if gaps:
        logger.info(
            "  → task requires generation (%d unmatched capability(ies))",
            len(gaps_raw),
        )

    return Command(
        update={
            "matches": matches,
            "gaps": gaps,
            "status": TaskStatus.DETECTING_GAPS,
            "errors": errors,
        },
        goto="classifier_node",
    )
