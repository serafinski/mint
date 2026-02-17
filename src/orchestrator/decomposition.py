"""Task decomposition via DeepAgent.

Wraps a DeepAgent (``create_deep_agent``) in a LangGraph node function.
The agent uses ``write_todos`` for planning and ``response_format`` for
structured subtask output.

Operational boundaries (from CLAUDE.md):
- ``recursion_limit=25`` (max steps)
- Node-level timeout: 120 s
- Fresh initialization per invocation (stateless)
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
from src.schemas.task import DecompositionResult, SubTask, TaskState, TaskStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Operational boundaries
# ---------------------------------------------------------------------------

DECOMPOSITION_TIMEOUT_S = 120
DECOMPOSITION_RECURSION_LIMIT = 25


# ---------------------------------------------------------------------------
# Agent factory (lazy initialisation to avoid import-time API key checks)
# ---------------------------------------------------------------------------

_decomposition_agent = None


def _get_agent():
    """Lazily create the decomposition DeepAgent on first use."""
    global _decomposition_agent  # noqa: PLW0603
    if _decomposition_agent is None:
        _decomposition_agent = create_deep_agent(
            model="deepseek:deepseek-chat",
            system_prompt=DECOMPOSITION_SYSTEM_PROMPT,
            response_format=DecompositionResult,
            name="task-decomposer",
        )
    return _decomposition_agent


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------


def decomposition_node(
    state: TaskState,
) -> Command[Literal["gap_detection"]]:
    """Decompose a task into subtasks using a DeepAgent.

    The agent is invoked with a 120 s timeout and a recursion limit of 25.
    On timeout or error the node returns partial results (empty subtasks)
    with the error recorded in ``state.errors``.
    """
    agent = _get_agent()

    try:
        langfuse_handler = LangFuseCallbackHandler()
        callbacks = [langfuse_handler]
    except Exception:  # noqa: BLE001
        callbacks = []

    config = {
        "recursion_limit": DECOMPOSITION_RECURSION_LIMIT,
        "callbacks": callbacks,
    }

    subtasks: list[SubTask] = []
    required_caps: list[str] = []
    errors: list[str] = list(state.errors)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                agent.invoke,
                {"messages": [{"role": "user", "content": state.task_description}]},
                config,
            )
            result = future.result(timeout=DECOMPOSITION_TIMEOUT_S)

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
        else:
            errors.append("Decomposition agent returned no structured response")
            logger.warning("DecompositionAgent: no structured_response in result")

    except concurrent.futures.TimeoutError:
        errors.append(f"Decomposition agent timed out after {DECOMPOSITION_TIMEOUT_S}s")
        logger.error("DecompositionAgent: timed out after %ds", DECOMPOSITION_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Decomposition agent error: {exc}")
        logger.exception("DecompositionAgent: unexpected error")

    # Fallback: if no subtasks were produced, treat the whole task as one subtask
    if not subtasks:
        required_caps = [state.task_description]

    return Command(
        update={
            "subtasks": subtasks,
            "required_capabilities": required_caps,
            "status": TaskStatus.DECOMPOSING,
            "errors": errors,
        },
        goto="gap_detection",
    )
