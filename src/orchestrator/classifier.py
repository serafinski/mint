"""Task-level capability classifier (LLM-based, temperature=0).

Classifies an entire task into ONE ``CapabilityType`` (MCP_TOOL, SKILL, or
AGENT) and produces a single ``ClassifiedGap``.  This is a plain LangGraph
node (no DeepAgent) because classification is a single-step task.

Classification uses a single LLM call with ``temperature=0`` for near-
deterministic results and ``with_structured_output`` for validated responses.

Task-level classification (replaces earlier per-gap classifier): the classifier
receives the full task description plus the decomposition-produced
``required_capabilities`` as context and returns ONE classification for the
whole task. This matches the testbed's single-type-per-task ground truth and
eliminates multi-artifact waste on multi-gap tasks.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Literal

from langgraph.types import Command

from src.orchestrator.prompts.classifier import TASK_CLASSIFICATION_PROMPT
from src.orchestrator.tracing import emit_langfuse_event
from src.schemas.capability import CapabilityType
from src.schemas.task import ClassificationResult, ClassifiedGap, TaskState, TaskStatus

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


def classify_task(
    task_description: str,
    required_capabilities: list[str],
) -> ClassifiedGap:
    """Classify an entire task into one capability type.

    Uses a single LLM call at ``temperature=0`` with structured output.
    Returns a ``ClassifiedGap`` whose ``description`` is a truncated task
    description and whose ``query`` is a deterministic slug.

    On LLM failure, returns a fallback classification matching the existing
    classifier convention: ``MCP_TOOL`` with confidence ``0.3`` and the marker
    ``"LLM classification failed"`` in the rationale (so ``classifier_node``
    can detect the fallback and surface it on ``state.errors``).

    Args:
        task_description: The full natural-language task description.
        required_capabilities: Capability names produced by decomposition;
            passed to the prompt as contextual information only (not a
            routing rule).
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

    # No per-node CallbackHandler: invoked from a LangGraph node so the
    # pipeline-level handler propagates automatically via LangChain's
    # contextvar inheritance.

    try:
        result: ClassificationResult = structured_llm.invoke(prompt)
        # Use LLM-chosen semantic name (normalized + hash-suffixed for
        # collision safety). Falls back to slug-from-task if the LLM's
        # chosen name fails validation.
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
    """Classify the whole task into one capability type.

    Skips classification if ``state.gaps`` is empty — meaning every required
    capability was matched in the registry by ``gap_detection_node``. In that
    case the pipeline proceeds through the (no-op) generator chain directly
    to the loading node.

    Otherwise, produces exactly ONE ``ClassifiedGap`` representing the
    task-level classification. Any pre-existing classified gaps in state are
    preserved (for retry scenarios) and the new task-level classification is
    appended.

    Records a warning in ``state.errors`` when the LLM falls back to the
    default classification (detectable via the ``rationale`` marker
    ``"LLM classification failed"``).

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
        "Classifier: task-level classification (%d required capabilities, %d gap(s))",
        len(state.required_capabilities),
        len(state.gaps),
    )

    classified = classify_task(
        task_description=state.task_description,
        required_capabilities=state.required_capabilities,
    )

    logger.info(
        "Classifier: task classified as %s (confidence=%.2f)",
        classified.classified_type.value,
        classified.confidence,
    )
    emit_langfuse_event(
        None,
        "classifier.task_classified",
        "DEFAULT",
        {
            "classified_type": classified.classified_type.value,
            "confidence": classified.confidence,
            "n_required_capabilities": len(state.required_capabilities),
            "n_gaps_before": len(state.gaps),
        },
    )

    # Preserve any pre-existing classified gaps (retry scenarios) and append
    # the new task-level classification.
    classified_gaps = list(state.classified_gaps)
    classified_gaps.append(classified)

    errors: list[str] = list(state.errors)
    # Match the existing convention: "LLM classification failed" marker in rationale
    if "LLM classification failed" in classified.rationale:
        errors.append(f"Classification fallback: {classified.rationale}")

    return Command(
        update={
            "classified_gaps": classified_gaps,
            "status": TaskStatus.CLASSIFYING,
            "errors": errors,
        },
        goto="generate_mcp_tool",
    )
