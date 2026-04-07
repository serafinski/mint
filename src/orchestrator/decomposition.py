"""Task decomposition via DeepAgent.

Wraps a DeepAgent (``create_deep_agent``) in a LangGraph node function.
The agent uses ``write_todos`` for planning and ``response_format`` for
structured subtask output.

Operational boundaries (from CLAUDE.md):
- ``recursion_limit=50`` (max steps)
- Node-level timeout: 120 s
- Graph is a stateless CompiledStateGraph; state is fresh per ``.invoke()`` call
- On timeout / error: return partial results with error flag
"""

from __future__ import annotations

import concurrent.futures
import logging
from typing import Literal

from deepagents import create_deep_agent
from langfuse.langchain import CallbackHandler as LangFuseCallbackHandler
from langgraph.types import Command

from src.orchestrator.prompts.decomposition import DECOMPOSITION_SYSTEM_PROMPT
from src.orchestrator.tracing import emit_langfuse_event
from src.schemas.task import DecompositionResult, SubTask, TaskState, TaskStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Operational boundaries
# ---------------------------------------------------------------------------

DECOMPOSITION_TIMEOUT_S = 120
DECOMPOSITION_RECURSION_LIMIT = 50

# Two-stage circuit breaker for capability count.
# WARN: emit visibility signal but proceed intact — genuine 6-7 step tasks survive.
# TRUNCATE: pathological 14-step decompositions are caught before they explode
#           generation and verification time.
WARN_CAPS = 5
MAX_CAPS_HARD = 8


# ---------------------------------------------------------------------------
# Agent factory — creates a fresh agent per invocation
# ---------------------------------------------------------------------------


def _get_agent():
    """Create a fresh decomposition DeepAgent for each invocation.

    A new ``CompiledStateGraph`` is returned on every call so no state is
    carried between pipeline runs (CLAUDE.md: "Fresh every invocation —
    No state carried between calls").  ``create_deep_agent()`` compiles a
    LangGraph graph without making any API calls, so the overhead is minimal.
    """
    from src.config import DEFAULT_LLM_MODEL

    return create_deep_agent(
        model=DEFAULT_LLM_MODEL,
        system_prompt=DECOMPOSITION_SYSTEM_PROMPT,
        response_format=DecompositionResult,
        name="task-decomposer",
    )


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------


def decomposition_node(
    state: TaskState,
) -> Command[Literal["gap_detection"]]:
    """Decompose a task into subtasks using a DeepAgent.

    The agent is invoked with a 120 s timeout and a recursion limit of 50.
    On timeout or error the node returns partial results (empty subtasks)
    with the error recorded in ``state.errors``.
    """
    agent = _get_agent()

    langfuse_handler = None
    try:
        langfuse_handler = LangFuseCallbackHandler()
        callbacks = [langfuse_handler]
    except Exception as exc:  # noqa: BLE001
        logger.debug("LangFuse handler creation failed (tracing disabled): %s", exc)
        callbacks = []

    config = {
        "recursion_limit": DECOMPOSITION_RECURSION_LIMIT,
        "callbacks": callbacks,
    }

    subtasks: list[SubTask] = []
    required_caps: list[str] = []
    errors: list[str] = list(state.errors)

    # Use manual executor management so we can call shutdown(wait=False) on
    # timeout — the context-manager form calls shutdown(wait=True) from __exit__,
    # which blocks until the thread finishes even after TimeoutError is raised.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(
            agent.invoke,
            {"messages": [{"role": "user", "content": state.task_description}]},
            config,
        )
        result = future.result(timeout=DECOMPOSITION_TIMEOUT_S)
        pool.shutdown(wait=False)

        structured: DecompositionResult | None = result.get("structured_response")

        if structured is not None:
            for item in structured.subtasks:
                subtasks.append(
                    SubTask(
                        description=item.description,
                        required_capabilities=item.required_capabilities,
                        depends_on=item.depends_on,
                    )
                )
                required_caps.extend(item.required_capabilities)
            if not subtasks:
                errors.append(
                    "Decomposition agent returned 0 subtasks; treating whole task as one capability"
                )
                logger.warning("DecompositionAgent: structured response contained 0 subtasks")
                emit_langfuse_event(
                    langfuse_handler,
                    "decomposition.empty_subtasks",
                    "WARNING",
                    {"task_description": state.task_description[:200]},
                )
        else:
            errors.append("Decomposition agent returned no structured response")
            logger.warning("DecompositionAgent: no structured_response in result")
            emit_langfuse_event(
                langfuse_handler,
                "decomposition.no_structured_response",
                "WARNING",
                {"task_description": state.task_description[:200]},
            )

    except concurrent.futures.TimeoutError:
        pool.shutdown(wait=False)
        errors.append(f"Decomposition agent timed out after {DECOMPOSITION_TIMEOUT_S}s")
        logger.error("DecompositionAgent: timed out after %ds", DECOMPOSITION_TIMEOUT_S)
        emit_langfuse_event(
            langfuse_handler,
            "decomposition.timeout",
            "ERROR",
            {"timeout_s": DECOMPOSITION_TIMEOUT_S},
        )
    except Exception as exc:  # noqa: BLE001
        pool.shutdown(wait=False)
        errors.append(f"Decomposition agent error: {exc}")
        logger.exception("DecompositionAgent: unexpected error")
        emit_langfuse_event(
            langfuse_handler,
            "decomposition.error",
            "ERROR",
            {"error": str(exc)},
        )

    # Deduplicate required_caps while preserving insertion order.
    # Multiple subtasks may reference the same capability; searching/classifying
    # duplicates wastes tokens and can produce duplicate artifacts.
    required_caps = list(dict.fromkeys(required_caps))

    # Two-stage circuit breaker: warn at WARN_CAPS, hard truncate at MAX_CAPS_HARD.
    if len(required_caps) > WARN_CAPS:
        logger.warning(
            "Decomposition produced %d required capabilities (warn threshold %d). "
            "Prompt constraints may not have been followed.",
            len(required_caps), WARN_CAPS,
        )
        emit_langfuse_event(
            langfuse_handler,
            "decomposition.over_warn_limit",
            "WARNING",
            {"cap_count": len(required_caps), "warn_limit": WARN_CAPS, "caps": required_caps},
        )

    if len(required_caps) > MAX_CAPS_HARD:
        logger.error(
            "Decomposition produced %d capabilities — truncating to %d (hard limit). "
            "This is a pathological decomposition; the prompt may not be working.",
            len(required_caps), MAX_CAPS_HARD,
        )
        emit_langfuse_event(
            langfuse_handler,
            "decomposition.hard_truncation",
            "CRITICAL",
            {
                "original_count": len(required_caps),
                "truncated_to": MAX_CAPS_HARD,
                "dropped": required_caps[MAX_CAPS_HARD:],
            },
        )
        required_caps = required_caps[:MAX_CAPS_HARD]

    # Fallback: if no subtasks were produced, use the first sentence of the task
    # description as a capability query (the full description is too long for
    # effective semantic search in the registry).
    if not subtasks:
        first_sentence = state.task_description.split(".")[0].strip()
        required_caps = [first_sentence[:200] if first_sentence else state.task_description[:200]]

    return Command(
        update={
            "subtasks": subtasks,
            "required_capabilities": required_caps,
            "status": TaskStatus.DECOMPOSING,
            "errors": errors,
        },
        goto="gap_detection",
    )
