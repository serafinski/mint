"""Gap detection — LangGraph node wrapping registry.detect_gaps().

Searches the capability registry for each required capability and populates
``state.matches`` per matched capability. For each required capability that
does NOT match in the registry, produces one enumerated ``GapItem`` so the
downstream per-gap classifier can classify each gap individually within the
context of the whole task.

This is a plain Python node — no LLM calls. As of Stage 1 of the
compositional refactor, matches are NO LONGER recorded as fake invocations
here; honest invocation records are written by the Stage 3 compositional
executor after real chain execution.

As of Stage 2 of the compositional refactor, the previous "single
placeholder gap" collapse is removed: the classifier again receives N
enumerated gaps (one per unmatched required capability) so generators can
produce N artifacts, one per step of the workflow.
"""

from __future__ import annotations

import logging
from typing import Literal

from langgraph.types import Command

from src.registry.metrics import MetricsStore
from src.registry.store import SkillRegistry
from src.schemas.task import GapItem, MatchItem, TaskState, TaskStatus

logger = logging.getLogger(__name__)


def gap_detection_node(
    state: TaskState,
    registry: SkillRegistry,
    metrics_store: MetricsStore | None = None,
) -> Command[Literal["classifier_node"]]:
    """Search the registry for each required capability.

    Populates ``state.matches`` with capabilities found above the similarity
    threshold. For each required capability that was NOT matched in the
    registry, produces one enumerated ``GapItem`` in ``state.gaps``. If all
    required capabilities are matched, ``state.gaps`` is empty and the
    classifier becomes a no-op.

    Always routes to ``classifier_node``.

    Args:
        state: Current pipeline state with ``required_capabilities`` populated
            by ``decomposition_node``.
        registry: The capability registry to search.
        metrics_store: Currently unused. Reserved for future use; the Stage 3
            compositional executor records honest invocation records after
            real chain execution. Kept on the signature for caller stability.

    Returns:
        Command routing to ``classifier_node`` with updated matches and gaps.
    """
    del metrics_store  # Stage 1: matches are no longer recorded as fake invocations.
    errors: list[str] = list(state.errors)

    # Evolutionary taxonomy (B1 Step 3.5f): pick the best existing category
    # for this task's description, then thread into detect_gaps with
    # include_generic=True so generic caps bypass the filter. On any failure
    # (empty taxonomy, LLM error) get_for_query returns None and detect_gaps
    # runs unfiltered — the pre-3.5f behavior.
    selected_domain: str | None = None
    try:
        from langchain.chat_models import init_chat_model

        from src.config import DEFAULT_LLM_MODEL
        from src.orchestrator.taxonomy import (
            DomainTaxonomy,
            QueryDomainChoice,
            get_taxonomy_path,
        )

        taxonomy = DomainTaxonomy(get_taxonomy_path())
        if taxonomy.list_categories():
            query_llm = init_chat_model(DEFAULT_LLM_MODEL, temperature=0)
            structured_query_llm = query_llm.with_structured_output(
                QueryDomainChoice, method="function_calling"
            )
            selected_domain = taxonomy.get_for_query(
                state.task_description, structured_query_llm
            )
            if selected_domain is not None:
                logger.info(
                    "gap_detection: taxonomy filter → domain='%s' (include_generic=True)",
                    selected_domain,
                )
            else:
                logger.info(
                    "gap_detection: taxonomy filter → unfiltered (no matching category)"
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "gap_detection: taxonomy filter setup failed (%s) — running unfiltered",
            exc,
        )
        selected_domain = None

    matches_raw, gaps_raw = registry.detect_gaps(
        state.required_capabilities,
        domain=selected_domain,
        include_generic=True,
    )

    matches = [
        MatchItem(capability=cap, score=score, query=query)
        for cap, score, query in matches_raw
    ]

    # Stage 1 of the compositional refactor: matches are NOT recorded as
    # InvocationRecords here. Doing so inflated reuse counts (e.g., csv-parser
    # in T14) without any real execution. The Stage 3 CompositionalExecutor
    # writes honest invocation records after the chain actually runs.

    # Stage 2 of the compositional refactor: produce N enumerated GapItems,
    # one per unmatched required capability. The per-gap classifier downstream
    # classifies each gap independently within the context of the whole task,
    # which lets generators produce N artifacts (one per workflow step).
    gaps: list[GapItem] = [
        GapItem(
            description=capability_name,
            query=capability_name,
        )
        for capability_name, _reason in gaps_raw
    ]

    logger.info(
        "Gap detection: %d match(es), %d unmatched capability(ies) → %d gap(s) for classification",
        len(matches),
        len(gaps_raw),
        len(gaps),
    )
    for m in matches:
        logger.info("  ✓ matched: %s (score=%.2f)", m.capability.name, m.score)
    for g in gaps:
        logger.info("  ✗ gap: %s", g.query)

    return Command(
        update={
            "matches": matches,
            "gaps": gaps,
            "status": TaskStatus.DETECTING_GAPS,
            "errors": errors,
        },
        goto="classifier_node",
    )
