"""MCP tool generator — LLM-based code generation for FastMCP tools.

Generates complete FastMCP Python tool implementations from capability gap
descriptions.  Uses a single LLM call with structured output, a compile-based
validation step, and a one-shot repair pass on failure.

This is a plain LangGraph node (no DeepAgent) because it is a single-pass
code generation task.
"""

from __future__ import annotations

import ast
import logging
import re
from typing import Literal

from langfuse.langchain import CallbackHandler as LangFuseCallbackHandler
from langgraph.types import Command
from pydantic import BaseModel, Field

from src.orchestrator.prompts.mcp_tool_generator import (
    MCP_TOOL_REPAIR_PROMPT,
    MCP_TOOL_SYSTEM_PROMPT,
    MCP_TOOL_USER_PROMPT,
)
from src.orchestrator.tracing import emit_langfuse_event
from src.schemas.capability import CapabilityType
from src.schemas.task import ClassifiedGap, GeneratedArtifact, TaskState, TaskStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structured output model
# ---------------------------------------------------------------------------


class GeneratedToolCode(BaseModel):
    """LLM response containing a generated MCP tool."""

    tool_name: str = Field(..., description="Snake_case name for the tool function")
    source_code: str = Field(..., description="Complete Python source file for the MCP tool")
    description: str = Field(..., description="Brief description of what the tool does")

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Shared LLM instance (lazy initialisation)
# ---------------------------------------------------------------------------

_generator_llm = None


def _get_llm():
    """Lazily create the generator LLM on first use."""
    global _generator_llm  # noqa: PLW0603
    if _generator_llm is None:
        from langchain.chat_models import init_chat_model

        _generator_llm = init_chat_model("deepseek:deepseek-chat")
    return _generator_llm


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _validate_tool_name(tool_name: str) -> str | None:
    """Validate the tool_name field from the LLM response.

    Returns an error string or None if valid.  Invalid names break
    ``tool_loader.load_tool`` because the name is used as a Python module
    name in the sandbox runner script.
    """
    if not tool_name.strip():
        return "tool_name must be non-empty"
    if not _SNAKE_CASE_RE.match(tool_name):
        return (
            f"tool_name '{tool_name}' must be valid snake_case "
            "(lowercase letters, digits, underscores only; no spaces or hyphens)"
        )
    if len(tool_name) > 64:
        return f"tool_name too long ({len(tool_name)} chars, max 64)"
    return None


def _validate_code(source_code: str) -> str | None:
    """Validate the source code: syntax check then structural FastMCP check.

    Collects ALL structural errors (not just the first) so the repair prompt
    can fix everything in one pass, consistent with how ``_validate_result``
    works in ``agent.py``.

    Returns a semicolon-joined error string, or None if valid.
    """
    # Syntax check — stop here because structural checks may be unreliable
    try:
        compile(source_code, "<generated>", "exec")
    except SyntaxError as exc:
        return f"SyntaxError at line {exc.lineno}: {exc.msg}"

    # Structural check: collect all missing elements
    errors: list[str] = []
    if "FastMCP" not in source_code:
        errors.append("Missing FastMCP instance (expected: mcp = FastMCP(...))")
    if "@mcp.tool" not in source_code:
        errors.append("Missing @mcp.tool() decorator")
    # Count async function definitions via AST (not substring search) to avoid
    # false positives from "async def" appearing in string literals or comments.
    tree = ast.parse(source_code)  # safe — syntax is already valid at this point
    # Count only TOP-LEVEL async defs (direct children of the module node).
    # ast.walk() traverses all nested nodes and would false-positively reject
    # a tool that defines a helper coroutine inside the tool body — such a
    # function never appears in dir(module) so the runner would still pick
    # the correct top-level tool function.
    async_def_count = sum(
        1 for n in ast.iter_child_nodes(tree) if isinstance(n, ast.AsyncFunctionDef)
    )
    if async_def_count == 0:
        errors.append("Missing async tool function (expected: async def ...)")
    elif async_def_count > 1:
        errors.append(
            f"Found {async_def_count} top-level async def functions; exactly 1 is required "
            "(tool runner picks the first async function alphabetically)"
        )
    if "Annotated" not in source_code:
        errors.append(
            "Missing Annotated parameter types (expected: Annotated[type, Field(description=...)])"
        )
    if "Field" not in source_code:
        errors.append(
            "Missing Field from pydantic (expected: from pydantic import Field)"
        )

    return "; ".join(errors) if errors else None


# ---------------------------------------------------------------------------
# Single tool generation
# ---------------------------------------------------------------------------


def generate_single_tool(
    gap: ClassifiedGap,
    task_context: str = "",
) -> tuple[GeneratedArtifact | None, str | None]:
    """Generate a single MCP tool from a classified gap.

    Returns:
        (artifact, None) on success, (None, error_message) on failure.
    """
    llm = _get_llm()
    structured_llm = llm.with_structured_output(GeneratedToolCode)

    # LangFuse callback
    langfuse_handler = None
    try:
        langfuse_handler = LangFuseCallbackHandler()
        callbacks = [langfuse_handler]
    except Exception:  # noqa: BLE001
        callbacks = []

    config = {"callbacks": callbacks} if callbacks else {}

    user_prompt = MCP_TOOL_USER_PROMPT.format(
        description=gap.description,
        query=gap.query,
        task_context=task_context or "No additional context",
    )

    messages = [
        {"role": "system", "content": MCP_TOOL_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    # --- First attempt ---
    try:
        result: GeneratedToolCode = structured_llm.invoke(messages, config=config)
    except Exception as exc:  # noqa: BLE001
        error_msg = f"LLM generation failed for {gap.query!r}: {exc}"
        logger.warning(error_msg)
        return None, error_msg

    # --- Validate source code and tool name (collect both so the repair prompt is complete) ---
    _code_err = _validate_code(result.source_code)
    _name_err = _validate_tool_name(result.tool_name)
    validation_error = "; ".join(e for e in [_code_err, _name_err] if e) or None
    if validation_error is None:
        # Success on first attempt
        return (
            GeneratedArtifact(
                capability_name=result.tool_name,
                capability_type=CapabilityType.MCP_TOOL,
                source_code=result.source_code,
            ),
            None,
        )

    # --- Repair pass ---
    logger.warning(
        "Validation failed for %r — triggering repair pass. Error: %s. Malformed output: %s",
        gap.query,
        validation_error,
        result.model_dump_json(),
    )
    emit_langfuse_event(
        langfuse_handler,
        "mcp_tool.validation_failure",
        "WARNING",
        {"gap_query": gap.query, "error": validation_error},
    )

    repair_prompt = MCP_TOOL_REPAIR_PROMPT.format(
        gap_description=gap.description,
        gap_query=gap.query,
        task_context=task_context or "No additional context",
        tool_name=result.tool_name,
        description=result.description,
        source_code=result.source_code,
        error=validation_error,
    )

    repair_messages = [
        {"role": "system", "content": MCP_TOOL_SYSTEM_PROMPT},
        {"role": "user", "content": repair_prompt},
    ]

    try:
        repaired: GeneratedToolCode = structured_llm.invoke(repair_messages, config=config)
    except Exception as exc:  # noqa: BLE001
        error_msg = (
            f"Repair pass LLM call failed for {gap.query!r}: {exc}"
            f" (original error: {validation_error})"
        )
        logger.warning(error_msg)
        emit_langfuse_event(
            langfuse_handler,
            "mcp_tool.repair_llm_failure",
            "ERROR",
            {"gap_query": gap.query, "error": str(exc), "original_error": validation_error},
        )
        return None, error_msg

    _repair_code_err = _validate_code(repaired.source_code)
    _repair_name_err = _validate_tool_name(repaired.tool_name)
    repair_validation = "; ".join(e for e in [_repair_code_err, _repair_name_err] if e) or None
    if repair_validation is not None:
        error_msg = (
            f"Repair pass failed for {gap.query!r}: {repair_validation}"
            f" (original error: {validation_error})"
        )
        logger.warning(error_msg)
        emit_langfuse_event(
            langfuse_handler,
            "mcp_tool.repair_validation_failure",
            "ERROR",
            {
                "gap_query": gap.query,
                "error": repair_validation,
                "original_error": validation_error,
            },
        )
        return None, error_msg

    return (
        GeneratedArtifact(
            capability_name=repaired.tool_name,
            capability_type=CapabilityType.MCP_TOOL,
            source_code=repaired.source_code,
        ),
        None,
    )


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------


def generate_mcp_tool(
    state: TaskState,
) -> Command[Literal["generate_skill"]]:
    """Generate MCP tools for all classified gaps of type MCP_TOOL.

    Filters ``state.classified_gaps`` to MCP_TOOL type, generates a tool for
    each, and hands off to ``generate_skill`` for the next stage of the
    sequential generator chain.
    """
    mcp_gaps = [
        gap
        for gap in state.classified_gaps
        if gap.classified_type == CapabilityType.MCP_TOOL
    ]

    artifacts: list[GeneratedArtifact] = list(state.generated_artifacts)
    errors: list[str] = list(state.errors)

    for gap in mcp_gaps:
        artifact, error = generate_single_tool(gap, task_context=state.task_description)
        if artifact is not None:
            artifacts.append(artifact)
        if error is not None:
            errors.append(error)

    return Command(
        update={
            "generated_artifacts": artifacts,
            "errors": errors,
            "status": TaskStatus.GENERATING,
        },
        goto="generate_skill",
    )
