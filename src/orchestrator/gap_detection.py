"""Gap detection — LangGraph node wrapping registry.detect_gaps().

Searches the capability registry for each required capability and
classifies results as matches (above threshold) or gaps (below threshold).
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
    threshold and ``state.gaps`` with those below.  Always routes to
    ``classifier_node`` — if there are no gaps the classifier produces an empty
    ``classified_gaps`` list and all generator nodes become no-ops.

    Args:
        state: Current pipeline state with ``required_capabilities`` populated
            by ``decomposition_node``.
        registry: The capability registry to search.

    Returns:
        Command routing to ``classifier_node`` with updated matches and gaps.
    """
    errors: list[str] = list(state.errors)

    matches_raw, gaps_raw = registry.detect_gaps(state.required_capabilities)

    matches = [
        MatchItem(capability=cap, score=score)
        for cap, score, _query in matches_raw
    ]
    gaps = []
    for query, reason in gaps_raw:
        gaps.append(GapItem(description=reason or query, query=query))

    logger.info(
        "Gap detection: %d matches, %d gaps from %d required capabilities",
        len(matches),
        len(gaps),
        len(state.required_capabilities),
    )
    for m in matches:
        logger.info("  ✓ matched: %s (score=%.2f)", m.capability.name, m.score)
    for g in gaps:
        logger.info("  ✗ gap: %s", g.description)

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

    return Command(
        update={
            "matches": matches,
            "gaps": gaps,
            "status": TaskStatus.DETECTING_GAPS,
            "errors": errors,
        },
        goto="classifier_node",
    )
