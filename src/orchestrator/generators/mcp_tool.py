"""MCP tool generator — LLM-based code generation for FastMCP tools.

Generates complete FastMCP Python tool implementations from capability gap
descriptions.  Uses a single LLM call with structured output, a compile-based
validation step, and a one-shot repair pass on failure.

This is a plain LangGraph node (no DeepAgent) because it is a single-pass
code generation task.
"""

from __future__ import annotations

import ast
import concurrent.futures
import logging
import re
import threading
from typing import Literal

from langgraph.types import Command
from pydantic import BaseModel, Field

from src.orchestrator.generators.utils import build_task_context, slugify_gap
from src.orchestrator.prompts.mcp_tool_generator import (
    DOWNSTREAM_STEP_DIRECTIVE,
    FIRST_STEP_DIRECTIVE,
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
    pip_packages: list[str] = Field(
        default_factory=list,
        description=(
            "PyPI package names required by this tool (e.g. ['pyyaml', 'scikit-learn', 'openpyxl']). "
            "Use the pip install name, NOT the Python import name "
            "(e.g. 'pyyaml' not 'yaml', 'scikit-learn' not 'sklearn', 'Pillow' not 'PIL'). "
            "Omit stdlib and packages already in the base image (mcp, pydantic)."
        ),
    )

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Shared LLM instance (lazy initialisation)
# ---------------------------------------------------------------------------

MAX_GENERATION_WORKERS = 4

_generator_llm = None
_llm_lock = threading.Lock()


def _get_llm():
    """Lazily create the generator LLM on first use (thread-safe).

    The returned instance is shared across threads (MAX_GENERATION_WORKERS=4).
    LangChain chat model objects backed by ``httpx`` are thread-safe.
    """
    global _generator_llm  # noqa: PLW0603
    if _generator_llm is None:
        with _llm_lock:
            if _generator_llm is None:
                from langchain.chat_models import init_chat_model

                from src.config import DEFAULT_LLM_MODEL

                _generator_llm = init_chat_model(DEFAULT_LLM_MODEL, request_timeout=300)
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
    # Count only PUBLIC top-level async defs (direct children of the module node,
    # excluding private helpers whose names start with "_").
    # - ast.iter_child_nodes avoids nested defs (e.g. a coroutine defined inside
    #   the tool body) that never appear in dir(module).
    # - The "_" filter mirrors the tool runner's dir(module) filter so that a
    #   module-level private helper (async def _helper()) doesn't trigger a false
    #   positive; the runner skips it alphabetically and always picks the public tool.
    async_def_count = sum(
        1
        for n in ast.iter_child_nodes(tree)
        if isinstance(n, ast.AsyncFunctionDef) and not n.name.startswith("_")
    )
    if async_def_count == 0:
        errors.append("Missing async tool function (expected: async def ...)")
    elif async_def_count > 1:
        errors.append(
            f"Found {async_def_count} public top-level async def functions; exactly 1 is required "
            "(tool runner picks the first non-private async function alphabetically)"
        )
    # Collect all imported names via AST — substring search would match docstrings/comments
    # and produce false positives for code that mentions but does not import these names.
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.asname or alias.name)

    if "Annotated" not in imported_names:
        errors.append(
            "Missing Annotated parameter types (expected: Annotated[type, Field(description=...)])"
        )
    if "Field" not in imported_names:
        errors.append(
            "Missing Field from pydantic (expected: from pydantic import Field)"
        )

    return "; ".join(errors) if errors else None


def _validate_step_signature(
    source_code: str,
    is_first_step: bool | None,
) -> str | None:
    """Validate the generated function's signature against its chain position.

    Step 14.7 regression guard: catches step-0 functions that declare
    ``upstream_data`` as a required parameter (the executor crashes with
    ``missing 1 required positional argument: 'upstream_data'``), and
    downstream functions that forget ``upstream_data`` entirely (the
    executor falls through to per-key matching, which silently loses data).

    Returns ``None`` when ``is_first_step`` is ``None`` (gap couldn't be
    matched to a subtask — no position information available), when the
    source is unparseable (``_validate_code`` will report the syntax error),
    or when no public async function is found (``_validate_code`` will
    report the missing function).
    """
    if is_first_step is None:
        return None
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return None

    func: ast.AsyncFunctionDef | None = None
    for n in ast.iter_child_nodes(tree):
        if isinstance(n, ast.AsyncFunctionDef) and not n.name.startswith("_"):
            func = n
            break
    if func is None:
        return None

    args = func.args
    positional = list(args.posonlyargs) + list(args.args)
    n_required_positional = len(positional) - len(args.defaults)
    required_names: list[str] = [a.arg for a in positional[:n_required_positional]]
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        if default is None:
            required_names.append(arg.arg)

    has_required_upstream = "upstream_data" in required_names

    if is_first_step and has_required_upstream:
        return (
            "step-0 function declares 'upstream_data' as a required parameter, "
            "but the first step has no upstream data (executor will crash with "
            "'missing 1 required positional argument: upstream_data'). Remove "
            "upstream_data from the signature — step 0 takes only file-path "
            "parameters."
        )
    if is_first_step is False and not has_required_upstream:
        return (
            "downstream step function is missing the required 'upstream_data: "
            "Annotated[dict, Field(...)]' parameter. Downstream steps receive "
            "parsed upstream outputs via upstream_data, not via file paths."
        )
    return None


def _step_position_block(is_first_step: bool | None) -> str:
    """Return the hard-constraint directive for the LLM prompt."""
    if is_first_step is True:
        return FIRST_STEP_DIRECTIVE
    if is_first_step is False:
        return DOWNSTREAM_STEP_DIRECTIVE
    return ""


# ---------------------------------------------------------------------------
# Single tool generation
# ---------------------------------------------------------------------------


def generate_single_tool(
    gap: ClassifiedGap,
    task_context: str = "",
    schema_context: str = "",
    is_first_step: bool | None = None,
) -> tuple[GeneratedArtifact | None, str | None]:
    """Generate a single MCP tool from a classified gap.

    Args:
        gap: The classified capability gap to generate a tool for.
        task_context: Full task description (with retry feedback appended
            on attempt > 0).
        schema_context: Per-step I/O schema contract derived from the
            decomposer's ``output_schema`` fields.
        is_first_step: ``True`` if the gap's subtask has no upstream
            dependencies (``depends_on == []``), ``False`` for a downstream
            step, ``None`` when the gap cannot be matched to a subtask. When
            not ``None``, a hard-constraint step-position block is injected
            into the user prompt AND a post-generation AST check validates
            the function signature against the chain position.

    Returns:
        (artifact, None) on success, (None, error_message) on failure.
    """
    forced_name = slugify_gap(gap.query)

    llm = _get_llm()
    structured_llm = llm.with_structured_output(GeneratedToolCode)

    # No per-node CallbackHandler: this function is invoked from within a LangGraph
    # node, so the pipeline-level CallbackHandler (when present) is automatically
    # inherited via LangChain's contextvar-based callback propagation.  Creating an
    # additional handler here would generate duplicate traces.

    # Build schema contract section — empty string if no schema available
    _schema_section = ""
    if schema_context:
        _schema_section = (
            "## Input/Output Schema Contract\n\n" + schema_context
        )

    user_prompt = MCP_TOOL_USER_PROMPT.format(
        task_context=task_context or gap.description,
        gap_description=gap.description or gap.query,
        forced_name=forced_name,
        schema_context=_schema_section,
        step_position=_step_position_block(is_first_step),
    )

    messages = [
        {"role": "system", "content": MCP_TOOL_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    # --- First attempt ---
    try:
        result: GeneratedToolCode = structured_llm.invoke(messages)
    except Exception as exc:  # noqa: BLE001
        error_msg = f"LLM generation failed for {gap.query!r}: {exc}"
        logger.warning(error_msg)
        return None, error_msg

    # --- Validate source code and tool name (collect all so the repair prompt is complete) ---
    _code_err = _validate_code(result.source_code)
    _name_err = _validate_tool_name(result.tool_name)
    _sig_err = _validate_step_signature(result.source_code, is_first_step)
    validation_error = "; ".join(e for e in [_code_err, _name_err, _sig_err] if e) or None
    if validation_error is None:
        # Success on first attempt — override whatever name the LLM chose
        if result.tool_name != forced_name:
            logger.debug(
                "Overriding LLM name '%s' with stable slug '%s'",
                result.tool_name, forced_name,
            )
        return (
            GeneratedArtifact(
                capability_name=forced_name,
                capability_type=CapabilityType.MCP_TOOL,
                source_code=result.source_code,
                description=result.description,
                pip_packages=result.pip_packages,
                gap_query=gap.query,
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
        None,
        "mcp_tool.validation_failure",
        "WARNING",
        {"gap_query": gap.query, "error": validation_error},
    )

    repair_prompt = MCP_TOOL_REPAIR_PROMPT.format(
        task_context=task_context or gap.description,
        gap_description=gap.description or gap.query,
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
        repaired: GeneratedToolCode = structured_llm.invoke(repair_messages)
    except Exception as exc:  # noqa: BLE001
        error_msg = (
            f"Repair pass LLM call failed for {gap.query!r}: {exc}"
            f" (original error: {validation_error})"
        )
        logger.warning(error_msg)
        emit_langfuse_event(
            None,
            "mcp_tool.repair_llm_failure",
            "ERROR",
            {"gap_query": gap.query, "error": str(exc), "original_error": validation_error},
        )
        return None, error_msg

    _repair_code_err = _validate_code(repaired.source_code)
    _repair_name_err = _validate_tool_name(repaired.tool_name)
    _repair_sig_err = _validate_step_signature(repaired.source_code, is_first_step)
    repair_validation = (
        "; ".join(e for e in [_repair_code_err, _repair_name_err, _repair_sig_err] if e)
        or None
    )
    if repair_validation is not None:
        error_msg = (
            f"Repair pass failed for {gap.query!r}: {repair_validation}"
            f" (original error: {validation_error})"
        )
        logger.warning(error_msg)
        emit_langfuse_event(
            None,
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
            capability_name=forced_name,
            capability_type=CapabilityType.MCP_TOOL,
            source_code=repaired.source_code,
            description=repaired.description,
            pip_packages=repaired.pip_packages,
            gap_query=gap.query,
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

    # On retry, state.generated_artifacts contains only passing artifacts
    # (verification_node strips failed ones). Skip any gap already covered.
    existing_gap_queries = {a.gap_query for a in artifacts if a.gap_query is not None}

    # Filter before submitting — don't waste a thread on a gap already covered
    gaps_to_generate = [g for g in mcp_gaps if g.query not in existing_gap_queries]
    for skipped in mcp_gaps:
        if skipped.query in existing_gap_queries:
            logger.info("  → skipping (already passed verification): %s", skipped.query)

    logger.info("generate_mcp_tool: %d gap(s) to generate (%d skipped)", len(gaps_to_generate), len(mcp_gaps) - len(gaps_to_generate))
    task_context = build_task_context(state)

    # Build per-gap schema context from decomposer output_schema
    from src.orchestrator.generators.utils import (
        build_schema_context,
        is_first_step_for_gap,
    )
    subtasks = list(state.subtasks) if state.subtasks else []

    # Parallel generation — collect results then merge sequentially
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_GENERATION_WORKERS) as pool:
        futures = {
            pool.submit(
                generate_single_tool, gap,
                task_context=task_context,
                schema_context=build_schema_context(gap, subtasks),
                is_first_step=is_first_step_for_gap(gap, subtasks),
            ): gap
            for gap in gaps_to_generate
        }
        raw_results: list[tuple[GeneratedArtifact | None, str | None]] = []
        for future in concurrent.futures.as_completed(futures):
            try:
                raw_results.append(future.result())
            except Exception as exc:
                gap = futures[future]
                raw_results.append((None, f"Generation failed for {gap.query!r}: {exc}"))

    # Merge after pool closes — sequential, no concurrent mutation
    n_succeeded = 0
    failed_queries: list[str] = []
    for artifact, error in raw_results:
        if artifact is not None:
            artifacts = [a for a in artifacts if a.capability_name != artifact.capability_name]
            artifacts.append(artifact)
            n_succeeded += 1
        if error is not None:
            errors.append(error)
            failed_queries.append(error[:80])

    if failed_queries:
        logger.warning(
            "generate_mcp_tool: %d/%d gaps failed generation: %s",
            len(failed_queries), len(gaps_to_generate), failed_queries,
        )

    # Evolutionary taxonomy classification (B1 Step 3.5f). Tags each newly
    # generated artifact with domain + is_generic so gap_detection can apply
    # the domain filter with generic-cap bypass on subsequent runs. Never
    # raises — failures leave artifact.domain=None and downstream code falls
    # back to "generated".
    from src.orchestrator.taxonomy import classify_and_tag_artifacts
    classify_and_tag_artifacts(artifacts)

    return Command(
        update={
            "generated_artifacts": artifacts,
            "errors": errors,
            "status": TaskStatus.GENERATING,
        },
        goto="generate_skill",
    )
