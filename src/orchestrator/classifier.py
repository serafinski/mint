"""Capability classifier — LLM-based semantic classification.

Classifies each ``GapItem`` into a ``CapabilityType`` (MCP_TOOL, SKILL, or
AGENT) and produces ``ClassifiedGap`` objects.  The classifier is a plain
LangGraph node (no DeepAgent) because it is a single-step classification task.

Classification uses a single LLM call with ``temperature=0`` for near-
deterministic results and ``with_structured_output`` for validated responses.
"""

from __future__ import annotations

import logging
from typing import Literal

from langfuse.langchain import CallbackHandler as LangFuseCallbackHandler
from langgraph.graph import END
from langgraph.types import Command

from src.orchestrator.prompts.classifier import CLASSIFICATION_PROMPT
from src.schemas.capability import CapabilityType
from src.schemas.task import ClassificationResult, ClassifiedGap, GapItem, TaskState, TaskStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LangFuse event helper
# ---------------------------------------------------------------------------


def _emit_langfuse_event(
    langfuse_handler: object | None,
    name: str,
    level: str,
    metadata: dict,
) -> None:
    """Emit a named event to LangFuse, linked to the active trace if available.

    Silently no-ops if LangFuse is unavailable.
    """
    try:
        from langfuse import Langfuse
        from langfuse.types import TraceContext

        trace_id: str | None = getattr(langfuse_handler, "last_trace_id", None)
        lf = Langfuse()
        lf.create_event(
            trace_context=TraceContext(trace_id=trace_id) if trace_id else None,
            name=name,
            level=level,  # type: ignore[arg-type]
            metadata=metadata,
        )
        lf.flush()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Shared LLM instance (lazy initialisation)
# ---------------------------------------------------------------------------

_classifier_llm = None


def _get_llm():
    """Lazily create the classifier LLM on first use.

    Reuses the same instance across ``classify_gap`` calls to avoid
    re-initialising the model for every gap in a batch.
    """
    global _classifier_llm  # noqa: PLW0603
    if _classifier_llm is None:
        from langchain.chat_models import init_chat_model

        _classifier_llm = init_chat_model("deepseek:deepseek-chat", temperature=0)
    return _classifier_llm


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_gap(gap: GapItem) -> ClassifiedGap:
    """Classify a single gap into a ``CapabilityType``.

    Uses a single LLM call at ``temperature=0`` with structured output.
    On LLM failure, defaults to ``MCP_TOOL`` with low confidence.
    """
    llm = _get_llm()
    structured_llm = llm.with_structured_output(ClassificationResult)

    prompt = CLASSIFICATION_PROMPT.format(
        description=gap.description,
        query=gap.query,
    )

    langfuse_handler = None
    try:
        langfuse_handler = LangFuseCallbackHandler()
        callbacks = [langfuse_handler]
    except Exception:  # noqa: BLE001
        callbacks = []

    config = {"callbacks": callbacks} if callbacks else {}

    try:
        result: ClassificationResult = structured_llm.invoke(prompt, config=config)
        return ClassifiedGap(
            description=gap.description,
            query=gap.query,
            classified_type=result.classified_type,
            confidence=result.confidence,
            rationale=result.rationale,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM classification failed for %r: %s", gap.query, exc)
        _emit_langfuse_event(
            langfuse_handler,
            "classifier.llm_failure",
            "ERROR",
            {"gap_query": gap.query, "error": str(exc)},
        )
        return ClassifiedGap(
            description=gap.description,
            query=gap.query,
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.3,
            rationale=f"LLM classification failed ({exc}); defaulting to MCP_TOOL",
        )


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------


def classifier_node(
    state: TaskState,
) -> Command[Literal["__end__"]]:
    """Classify all gaps in the task state.

    Records a warning in ``state.errors`` for every gap where the LLM fell
    back to the default classification (detectable via the ``rationale`` marker
    "LLM classification failed").

    Routes to ``END`` for now.  In Phase C this node will use the LangGraph
    ``Send`` API to fan out each classified gap to its type-specific generator
    (``generate_mcp_tool``, ``generate_skill``, ``generate_agent``).  Generator
    nodes should accept a ``GeneratorInput`` schema as their input.
    """
    classified: list[ClassifiedGap] = list(state.classified_gaps)
    errors: list[str] = list(state.errors)

    for gap in state.gaps:
        result = classify_gap(gap)
        classified.append(result)
        # Detect the LLM-failure fallback and surface it in state.errors so
        # downstream nodes and operators can observe classification failures.
        if "LLM classification failed" in result.rationale:
            errors.append(f"Classification fallback for {gap.query!r}: {result.rationale}")

    return Command(
        update={
            "classified_gaps": classified,
            "errors": errors,
            "status": TaskStatus.CLASSIFYING,
        },
        goto=END,
    )
