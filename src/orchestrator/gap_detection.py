"""Gap detection - LangGraph node wrapping registry.detect_gaps().

Searches the capability registry for each required capability and populates
``state.matches`` per matched capability. For each required capability that
does NOT match in the registry, produces one enumerated ``GapItem`` so the
downstream per-gap classifier can classify each gap individually within the
context of the whole task.

The node uses deterministic registry lookup for ordinary matching. If the
file-backed taxonomy already contains categories, it may use an LLM to pick a
query domain before calling the registry. Match accounting intentionally stays
out of this node: invocation records are written only after real chain
execution.
"""

from __future__ import annotations

import logging
from typing import Literal

from langgraph.types import Command

from src.registry.metrics import MetricsStore
from src.registry.store import CapabilityRegistry
from src.schemas.capability import CapabilityDescriptor, CapabilityType
from src.schemas.task import GapItem, MatchItem, SubTask, TaskState, TaskStatus

logger = logging.getLogger(__name__)


def _is_match_compatible_with_subtask(
    *,
    subtask: SubTask,
    capability: CapabilityDescriptor,
) -> tuple[bool, str | None]:
    """Return whether a registry match is compatible with a decomposed subtask.

    The guard has two compatibility checks:

    - if either side has no ``output_schema``, allow the match
    - otherwise, require the capability's output keys to cover the subtask's
      declared output keys
    - for downstream MCP tools, require an input call-shape that the chain
      executor can actually satisfy:
      - either the tool accepts ``upstream_data``
      - or it accepts the subtask's declared ``requires_upstream_keys`` as
        named parameters

    This blocks terminal whole-workflow capabilities from being reused as
    intermediate parser/adapter subtasks while preserving valid whole-task
    retrieval and substep reuse.
    """
    expected = set(subtask.output_schema.keys())
    if not expected:
        output_reason = None
    else:
        actual = set((capability.output_schema or {}).keys())
        if not actual:
            output_reason = None
        else:
            missing = expected - actual
            if missing:
                output_reason = (
                    f"registry match '{capability.name}' incompatible with subtask "
                    f"output_schema: expected keys {sorted(expected)}, "
                    f"got {sorted(actual)}"
                )
            else:
                output_reason = None

    if output_reason is not None:
        return False, output_reason

    # Later-chain MCP tools have a second compatibility dimension: can the
    # chain executor actually call them? Non-initial steps pass either:
    # - a single ``upstream_data`` dict, or
    # - named kwargs that match ``requires_upstream_keys`` exactly.
    #
    # Skills/agents do not use the same call-shape constraints, so keep this
    # gate MCP_TOOL-only.
    required_inputs = set(subtask.requires_upstream_keys or [])
    if capability.capability_type is not CapabilityType.MCP_TOOL:
        return True, None
    if not required_inputs:
        return True, None

    actual_inputs = set((capability.input_schema or {}).keys())
    if not actual_inputs:
        return True, None
    if "upstream_data" in actual_inputs:
        return True, None

    missing_inputs = required_inputs - actual_inputs
    if not missing_inputs:
        return True, None

    reason = (
        f"registry match '{capability.name}' incompatible with subtask "
        f"input call shape: requires upstream keys {sorted(required_inputs)}, "
        f"but tool inputs are {sorted(actual_inputs)}"
    )
    return False, reason


def gap_detection_node(
    state: TaskState,
    registry: CapabilityRegistry,
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
        metrics_store: Currently unused. Kept on the signature for caller
            stability; match accounting belongs to real execution, not
            registry lookup.

    Returns:
        Command routing to ``classifier_node`` with updated matches and gaps.
    """
    del metrics_store  # Registry lookup must not record synthetic invocations.
    errors: list[str] = list(state.errors)

    # Evolutionary taxonomy: pick the best existing category for this task's
    # description, then thread it into detect_gaps with include_generic=True so
    # generic capabilities bypass the filter. On empty taxonomy or classifier
    # failure, selected_domain remains None and retrieval runs unfiltered.
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
            # reasoning_effort="minimal" — same rationale as taxonomy.py
            # classify_and_tag_artifacts: picking one category from a short
            # list is pattern matching, not reasoning. Drops ~8s → ~1s.
            query_llm = init_chat_model(
                DEFAULT_LLM_MODEL, temperature=0, reasoning_effort="minimal"
            )
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

    # Dual-query retrieval. The decomposer produces short hyphen-case slugs in
    # ``required_capabilities`` which can fall below the similarity threshold
    # against the registry's prose-style ``_build_document()`` output. Build an
    # auxiliary-query list using ``SubTask.description`` for each slug;
    # ``detect_gaps`` runs both searches and takes the max-scoring hit.
    #
    # Association: each slug belongs to exactly one subtask. For each
    # subtask, every slug in its ``required_capabilities`` list uses the
    # SAME subtask description as its auxiliary. On the fallback path
    # (decomposition failed, ``state.subtasks`` is empty), every slug gets
    # None as its auxiliary, preserving single-query behaviour.
    #
    # See ``docs/archive/registry_retrieval_fix_plan.md`` for the empirical
    # evidence, ranked options, and methodology notes.
    slug_to_description: dict[str, str] = {}
    slug_to_subtask: dict[str, SubTask] = {}
    for subtask in state.subtasks:
        for slug in subtask.required_capabilities:
            # First subtask that claims a slug wins if the decomposer
            # happens to put the same slug under multiple subtasks — rare
            # but possible. Same-slug-across-subtasks is a decomposition
            # quality issue caught elsewhere.
            slug_to_description.setdefault(slug, subtask.description)
            slug_to_subtask.setdefault(slug, subtask)

    auxiliary_queries: list[str | None] = [
        slug_to_description.get(slug) for slug in state.required_capabilities
    ]

    matches_raw, gaps_raw = registry.detect_gaps(
        state.required_capabilities,
        auxiliary_queries=auxiliary_queries,
        domain=selected_domain,
        include_generic=True,
    )

    compatible_matches: list[MatchItem] = []
    demoted_gaps: list[tuple[str, str]] = []
    for cap, score, query in matches_raw:
        subtask = slug_to_subtask.get(query)
        if subtask is not None and subtask.output_schema:
            compatible, reason = _is_match_compatible_with_subtask(
                subtask=subtask,
                capability=cap,
            )
            if not compatible:
                demoted_gaps.append((query, reason or "incompatible registry match"))
                logger.info(
                    "gap_detection: demoting registry match %s for %s — %s",
                    cap.name,
                    query,
                    reason or "incompatible registry match",
                )
                continue

        compatible_matches.append(
            MatchItem(capability=cap, score=score, query=query)
        )

    # Matches are not recorded as InvocationRecords here. Recording registry
    # hits before execution would inflate reuse counts without any real call.
    #
    # Produce one enumerated GapItem per unmatched required capability. The
    # per-gap classifier downstream classifies each gap independently within
    # the context of the whole task, letting generators produce one artifact
    # per workflow step.
    all_gaps_raw = [*gaps_raw, *demoted_gaps]
    gaps: list[GapItem] = [
        GapItem(
            description=capability_name,
            query=capability_name,
        )
        for capability_name, _reason in all_gaps_raw
    ]

    logger.info(
        "Gap detection: %d match(es), %d unmatched capability(ies) → %d gap(s) for classification",
        len(compatible_matches),
        len(all_gaps_raw),
        len(gaps),
    )
    for m in compatible_matches:
        logger.info("  ✓ matched: %s (score=%.2f)", m.capability.name, m.score)
    for g in gaps:
        logger.info("  ✗ gap: %s", g.query)

    return Command(
        update={
            "matches": compatible_matches,
            "gaps": gaps,
            "status": TaskStatus.DETECTING_GAPS,
            "errors": errors,
        },
        goto="classifier_node",
    )
