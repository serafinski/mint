"""Per-gap capability classifier (LLM-based, temperature=0).

Classifies capability gaps into ``CapabilityType`` values (MCP_TOOL, SKILL,
or AGENT). This is a plain LangGraph node (no DeepAgent) because each
classification is a single-step LLM call.

As of Stage 2 of the compositional refactor, the classifier operates
per-gap: ``classifier_node`` iterates over ``state.gaps`` and calls
``classify_gap`` once for each, producing N entries in ``state.classified_gaps``
(one per workflow step). The legacy ``classify_task`` wrapper is preserved
for whole-task classification fallback.

Each classification uses a single LLM call with ``temperature=0`` for
near-deterministic results and ``with_structured_output`` for validated
responses.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Literal

from langgraph.types import Command

from src.orchestrator.prompts.classifier import (
    GAP_CLASSIFICATION_PROMPT,
    TASK_CLASSIFICATION_PROMPT,
)
from src.orchestrator.tracing import emit_langfuse_event
from src.schemas.capability import CapabilityType
from src.schemas.task import (
    ClassificationResult,
    ClassifiedGap,
    GapItem,
    TaskState,
    TaskStatus,
)

logger = logging.getLogger(__name__)


# Pattern for kebab-case names (lowercase letters, digits, hyphens)
_VALID_KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


# ---------------------------------------------------------------------------
# Shared LLM instance (lazy initialisation)
# ---------------------------------------------------------------------------

_classifier_llm = None


def _get_llm():
    """Lazily create the classifier LLM on first use.

    Reuses the same instance across ``classify_task`` calls to avoid
    re-initialising the model for every pipeline invocation.
    """
    global _classifier_llm  # noqa: PLW0603
    if _classifier_llm is None:
        from langchain.chat_models import init_chat_model

        from src.config import DEFAULT_LLM_MODEL

        _classifier_llm = init_chat_model(DEFAULT_LLM_MODEL, temperature=0, request_timeout=300)
    return _classifier_llm


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _slug_from_task(task_description: str) -> str:
    """Derive a deterministic fallback slug from the task description.

    Used only when the LLM fails to provide a valid ``capability_name`` in
    its structured output (e.g., LLM call failure, schema validation error).
    Normal classifier runs use the LLM-chosen ``capability_name`` instead —
    this is intentionally ugly to make LLM failures visible in the registry.

    The slug takes the first sentence (up to 40 chars) plus a short SHA-256
    hash suffix for determinism across runs.
    """
    first_sentence = task_description.split(".")[0].strip()[:40]
    slug_base = "-".join(first_sentence.lower().split())
    # Keep only alphanumerics and hyphens
    slug_base = "".join(c if c.isalnum() or c == "-" else "" for c in slug_base)
    slug_base = slug_base.strip("-")[:40] or "task"
    hash_suffix = hashlib.sha256(task_description.encode()).hexdigest()[:6]
    return f"{slug_base}-{hash_suffix}"


def _normalize_capability_name(name: str, task_description: str) -> str:
    """Validate and normalize an LLM-chosen capability name.

    Returns a clean kebab-case name with a 6-char hash suffix appended for
    collision safety (two tasks can reasonably pick the same base name; the
    hash keeps them distinguishable while still making the name readable).

    If the LLM's name fails validation (empty, too long, wrong format),
    falls back to ``_slug_from_task`` so the pipeline still produces a valid
    identifier.
    """
    # Normalize: lowercase, replace underscores/spaces/non-alnum with hyphens,
    # collapse multiple hyphens, strip leading/trailing hyphens
    cleaned = re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")
    if not cleaned:
        logger.warning(
            "LLM capability_name was empty after normalization; "
            "falling back to slug-from-task"
        )
        return _slug_from_task(task_description)

    # Truncate if over 40 chars (but at a hyphen boundary if possible)
    if len(cleaned) > 40:
        truncated = cleaned[:40]
        last_hyphen = truncated.rfind("-")
        if last_hyphen > 20:  # don't truncate too aggressively
            cleaned = truncated[:last_hyphen]
        else:
            cleaned = truncated.rstrip("-")

    # Final validation — should be valid kebab-case after normalization
    if not _VALID_KEBAB.match(cleaned):
        logger.warning(
            "LLM capability_name %r failed kebab-case validation after "
            "normalization; falling back to slug-from-task",
            name,
        )
        return _slug_from_task(task_description)

    # Append 6-char hash suffix for collision safety
    hash_suffix = hashlib.sha256(task_description.encode()).hexdigest()[:6]
    return f"{cleaned}-{hash_suffix}"


def classify_gap(
    gap: GapItem,
    task_description: str,
    required_capabilities: list[str],
) -> ClassifiedGap:
    """Classify a single gap within the context of the whole task.

    Uses a single LLM call at ``temperature=0`` with structured output.
    Returns a ``ClassifiedGap`` whose ``description`` carries the gap's own
    description and whose ``query`` is the LLM-chosen semantic name with a
    collision-safe hash suffix.

    On LLM failure, returns a fallback classification: ``MCP_TOOL`` with
    confidence ``0.3`` and the marker ``"LLM classification failed"`` in the
    rationale (so ``classifier_node`` can detect the fallback and surface it
    on ``state.errors``). The fallback's ``query`` is a deterministic slug
    derived from the gap query (not the task description) so multiple
    failing gaps in the same task don't collide.

    Args:
        gap: The single ``GapItem`` to classify (one step of the workflow).
        task_description: Full natural-language task description, supplied
            as context so the classifier can reason about the gap's role
            within the larger workflow.
        required_capabilities: Capability names produced by decomposition;
            passed to the prompt as contextual information only.
    """
    llm = _get_llm()
    structured_llm = llm.with_structured_output(ClassificationResult)

    caps_text = (
        ", ".join(required_capabilities)
        if required_capabilities
        else "(none identified)"
    )

    prompt = GAP_CLASSIFICATION_PROMPT.format(
        task_description=task_description[:1500],
        required_capabilities=caps_text,
        gap_query=gap.query,
        gap_description=gap.description[:500],
    )

    # No per-node CallbackHandler: invoked from a LangGraph node so the
    # pipeline-level handler propagates automatically via LangChain's
    # contextvar inheritance.

    # Hash-suffix seed: combine task description + gap query so two gaps
    # within the same task get different suffixes, and the same gap on
    # retry gets the same suffix (idempotent re-generation).
    name_seed = f"{task_description}||{gap.query}"

    try:
        result: ClassificationResult = structured_llm.invoke(prompt)
        capability_name = _normalize_capability_name(
            result.capability_name, name_seed
        )
        return ClassifiedGap(
            description=gap.description[:500] or gap.query,
            query=capability_name,
            classified_type=result.classified_type,
            confidence=result.confidence,
            rationale=result.rationale,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Gap classification LLM call failed for %r: %s", gap.query, exc
        )
        emit_langfuse_event(
            None,
            "classifier.llm_failure",
            "ERROR",
            {
                "gap_query": gap.query,
                "task_description": task_description[:200],
                "error": str(exc),
            },
        )
        # Fallback slug derived from the gap query (not the task), so that
        # multiple failing gaps in the same task produce distinct slugs.
        fallback_slug = _slug_from_task(gap.query or task_description)
        return ClassifiedGap(
            description=gap.description[:500] or gap.query,
            query=fallback_slug,
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.3,
            rationale=f"LLM classification failed ({exc}); defaulting to MCP_TOOL",
        )


def classify_task(
    task_description: str,
    required_capabilities: list[str],
) -> ClassifiedGap:
    """Classify an entire task into one capability type (legacy wrapper).

    Preserved as a thin wrapper over ``classify_gap`` for callers that want
    whole-task classification (tests, single-gap fallback paths). Internally
    uses the legacy ``TASK_CLASSIFICATION_PROMPT`` so the behaviour is
    identical to the pre-Stage-2 classifier on a single call.

    Prefer ``classify_gap`` for new code — the per-gap classifier handles
    multi-step tasks correctly and becomes the primary entry point in
    ``classifier_node``.
    """
    llm = _get_llm()
    structured_llm = llm.with_structured_output(ClassificationResult)

    caps_text = (
        ", ".join(required_capabilities)
        if required_capabilities
        else "(none identified)"
    )

    prompt = TASK_CLASSIFICATION_PROMPT.format(
        task_description=task_description[:1500],
        required_capabilities=caps_text,
    )

    try:
        result: ClassificationResult = structured_llm.invoke(prompt)
        capability_name = _normalize_capability_name(
            result.capability_name, task_description
        )
        return ClassifiedGap(
            description=task_description[:500],
            query=capability_name,
            classified_type=result.classified_type,
            confidence=result.confidence,
            rationale=result.rationale,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Task classification LLM call failed: %s", exc)
        emit_langfuse_event(
            None,
            "classifier.llm_failure",
            "ERROR",
            {"task_description": task_description[:200], "error": str(exc)},
        )
        return ClassifiedGap(
            description=task_description[:500],
            query=_slug_from_task(task_description),
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.3,
            rationale=f"LLM classification failed ({exc}); defaulting to MCP_TOOL",
        )


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------


def classifier_node(
    state: TaskState,
) -> Command[Literal["generate_mcp_tool"]]:
    """Classify each gap in ``state.gaps`` individually.

    Skips classification if ``state.gaps`` is empty — meaning every required
    capability was matched in the registry by ``gap_detection_node``. In that
    case the pipeline proceeds through the (no-op) generator chain directly
    to the loading node.

    Otherwise, iterates over ``state.gaps`` and calls ``classify_gap`` once
    per gap, producing N ``ClassifiedGap`` entries in ``state.classified_gaps``
    (one per workflow step). Two gaps in the same task can be classified as
    different types — generators filter ``classified_gaps`` by type and
    produce one artifact per gap of their type.

    On retry, any gap whose query matches an already-classified entry in
    ``state.classified_gaps`` is skipped (so retries don't duplicate work).
    Pre-existing classified gaps are preserved.

    Records a warning in ``state.errors`` for each gap where the LLM falls
    back to the default classification (detectable via the ``rationale``
    marker ``"LLM classification failed"``).

    Routes to ``generate_mcp_tool`` which begins the sequential generator
    chain: generate_mcp_tool → generate_skill → generate_agent → verification.
    """
    if not state.gaps:
        # All required capabilities matched in registry — nothing to classify.
        logger.info("Classifier: no gaps (all matched in registry), skipping")
        return Command(
            update={"status": TaskStatus.CLASSIFYING},
            goto="generate_mcp_tool",
        )

    logger.info(
        "Classifier: per-gap classification (%d gap(s), %d required capabilities)",
        len(state.gaps),
        len(state.required_capabilities),
    )

    # Preserve any pre-existing classified gaps (retry scenarios). Use the
    # pre-existing gap_query (raw, not the LLM-chosen capability_name) to
    # detect which gaps have already been classified. We stash the raw gap
    # query in a side-set because ClassifiedGap.query holds the normalized
    # capability name, not the original gap query.
    classified_gaps = list(state.classified_gaps)

    # On retry, state.classified_gaps may contain entries from a previous
    # attempt. Skip gaps whose description matches an already-classified
    # entry's description (which is set to gap.description in classify_gap).
    already_classified_descriptions = {cg.description for cg in classified_gaps}

    errors: list[str] = list(state.errors)
    n_fallbacks = 0

    for gap in state.gaps:
        gap_desc_key = (gap.description[:500] or gap.query)
        if gap_desc_key in already_classified_descriptions:
            logger.info("  → skipping (already classified): %s", gap.query)
            continue

        classified = classify_gap(
            gap=gap,
            task_description=state.task_description,
            required_capabilities=state.required_capabilities,
        )

        logger.info(
            "  classified %s → %s (confidence=%.2f, name=%s)",
            gap.query,
            classified.classified_type.value,
            classified.confidence,
            classified.query,
        )
        emit_langfuse_event(
            None,
            "classifier.gap_classified",
            "DEFAULT",
            {
                "gap_query": gap.query,
                "classified_type": classified.classified_type.value,
                "confidence": classified.confidence,
                "capability_name": classified.query,
            },
        )

        classified_gaps.append(classified)

        # Match the existing convention: "LLM classification failed" marker
        # in rationale signals an LLM fallback — record as a warning error.
        if "LLM classification failed" in classified.rationale:
            errors.append(f"Classification fallback: {classified.rationale}")
            n_fallbacks += 1

    if n_fallbacks:
        logger.warning(
            "Classifier: %d/%d gap(s) fell back to MCP_TOOL due to LLM failure",
            n_fallbacks,
            len(state.gaps),
        )

    return Command(
        update={
            "classified_gaps": classified_gaps,
            "status": TaskStatus.CLASSIFYING,
            "errors": errors,
        },
        goto="generate_mcp_tool",
    )
