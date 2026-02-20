"""Gap detection — LangGraph node wrapping registry.detect_gaps().

Searches the capability registry for each required capability and
classifies results as matches (above threshold) or gaps (below threshold).
This is a plain Python node — no LLM calls.
"""

from __future__ import annotations

import logging
from typing import Literal

from langgraph.types import Command

from src.registry.store import SkillRegistry
from src.schemas.task import GapItem, MatchItem, TaskState, TaskStatus

logger = logging.getLogger(__name__)


def gap_detection_node(
    state: TaskState,
    registry: SkillRegistry,
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
    matches_raw, gaps_raw = registry.detect_gaps(state.required_capabilities)

    matches = [
        MatchItem(capability=cap, score=score)
        for cap, score, _query in matches_raw
    ]
    gaps = [
        GapItem(description=query, query=query)
        for query, _reason in gaps_raw
    ]

    logger.info(
        "Gap detection: %d matches, %d gaps from %d required capabilities",
        len(matches),
        len(gaps),
        len(state.required_capabilities),
    )

    return Command(
        update={
            "matches": matches,
            "gaps": gaps,
            "status": TaskStatus.DETECTING_GAPS,
        },
        goto="classifier_node",
    )
