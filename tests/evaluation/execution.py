"""SKILL and AGENT artifact execution — three distinct backends.

Each capability type uses a different execution backend:

- **MCP Tool**: sandbox code execution, no LLM at runtime (handled in harness.py)
- **SKILL**: constrained LLM with ``sandbox_execute`` tool calling (max 3 calls,
  no planning loop, no ``write_todos``). Prescribed workflow execution.
- **AGENT**: full DeepAgent with ``sandbox_execute`` + native ``skills=`` parameter
  for progressive disclosure. Adaptive autonomous reasoning.

The skill/agent distinction is intentional — skills follow prescribed steps
with limited retry, while agents plan, adapt, and iterate freely. This makes
the classifier's routing decision have measurable runtime consequences.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Timeouts (seconds) — matching CLAUDE.md operational boundaries
# ---------------------------------------------------------------------------

SKILL_EXECUTION_TIMEOUT_S = 240
AGENT_EXECUTION_TIMEOUT_S = 480
_RECURSION_LIMIT = 100

# ---------------------------------------------------------------------------
# Response models (harness-internal, not pipeline state)
# ---------------------------------------------------------------------------


class T04Result(BaseModel):
    product_count: int
    items_below_threshold: int
    total_reorder_cost: float


class T08Result(BaseModel):
    n_rows: int
    n_columns: int
    quality_issues_found: int
    issue_categories: list[str] = []


class T10Result(BaseModel):
    n_weeks: int
    trend_direction: str
    has_seasonality: bool = False
    forecast: list[float] = []


class T11Result(BaseModel):
    summary_row_count: int
    region_revenues: dict[str, float]
    regions_meeting_target: int


class T12Result(BaseModel):
    clean_row_count: int
    violations_found: int
    rules_applied: int


class T13Result(BaseModel):
    anomaly_count: int
    anomalies: list[dict]
    metrics_analyzed: int


class T14Result(BaseModel):
    department_count: int
    department_spending: dict[str, float]
    over_budget_count: int
    flagged_transactions: int


class T15Result(BaseModel):
    n_days: int
    metrics_aggregated: int
    alert_days: int


class T16Result(BaseModel):
    root_cause_metric: str
    cascade_chain: list[str]
    n_metrics_investigated: int


class T17Result(BaseModel):
    total_alerts: int
    high_severity_alerts: int
    fraud_customer_ids: list


class GenericResult(BaseModel):
    """Fallback for tasks without a dedicated response model."""
    model_config = {"extra": "allow"}
    result: dict = {}


TASK_RESULT_MODELS: dict[str, type[BaseModel]] = {
    "T04": T04Result,
    "T08": T08Result,
    "T10": T10Result,
    "T11": T11Result,
    "T12": T12Result,
    "T13": T13Result,
    "T14": T14Result,
    "T15": T15Result,
    "T16": T16Result,
    "T17": T17Result,
}


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _read_fixture_contents(task: Any) -> dict[str, str]:
    """Read all fixture files for a task into a dict keyed by filename."""
    contents: dict[str, str] = {}
    for fp in getattr(task, "fixture_paths", []):
        path = Path(fp) if not isinstance(fp, Path) else fp
        if path.exists():
            contents[path.name] = path.read_text(encoding="utf-8", errors="replace")
        else:
            logger.warning("Fixture file not found: %s", path)
    return contents


def _format_fixture_message(fixture_contents: dict[str, str]) -> str:
    """Format fixture contents for inclusion in a DeepAgent user message."""
    parts = []
    for filename, content in fixture_contents.items():
        parts.append(f"--- FILE: {filename} ---\n{content}\n--- END FILE ---")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# SKILL execution
# ---------------------------------------------------------------------------

def _extract_last_sandbox_json(messages: list) -> dict | None:
    """Try to extract JSON from the last successful sandbox_execute result."""
    for msg in reversed(messages):
        # ToolMessage contains sandbox_execute results
        content = getattr(msg, "content", None)
        if not content or not isinstance(content, str):
            continue
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict) and parsed.get("success") and parsed.get("stdout"):
                # Parse the stdout — the tool prints JSON there
                stdout = parsed["stdout"].strip()
                # Take the last line (in case of debug output before it)
                last_line = stdout.splitlines()[-1] if stdout else ""
                result = json.loads(last_line)
                if isinstance(result, dict):
                    return result
        except (json.JSONDecodeError, IndexError, KeyError):
            continue
    return None


_SKILL_EXECUTION_SYSTEM_PROMPT = """\
You are a skill executor. You receive a task with input data and must follow \
the skill instructions below to produce the required output.

You have ONE tool available: sandbox_execute(code, timeout=10, extra_packages=None).
It runs Python code in an isolated Docker container and returns \
{"stdout": str, "stderr": str, "exit_code": int, "success": bool}.

## Rules
- Follow the skill instructions precisely — do not deviate or improvise.
- The input data is provided in the user message below. Embed it directly \
in your Python code as string literals, then write to /tmp/ files inside \
the code using Python's open()/write() before processing.
- Print your final result as JSON to stdout.
- Declare any non-stdlib packages via extra_packages (e.g., extra_packages=["pandas"]).
- Use the pip install name, not the Python import name (e.g., "pyyaml" not "yaml").
- You have at most 5 tool calls. Do not plan — just execute the prescribed steps.
"""

# Maximum sandbox_execute calls in the skill execution loop.
_SKILL_MAX_TOOL_CALLS = 5


def execute_skill_artifact(
    artifact: Any,
    task: Any,
    fixture_contents: dict[str, str],
    *,
    langfuse_tags: list[str] | None = None,
    langfuse_session_id: str | None = None,
) -> dict:
    """Execute a SKILL artifact via constrained LLM with tool calling.

    Uses ``bind_tools([sandbox_execute])`` with a hard cap of 5 tool calls.
    No DeepAgent, no ``write_todos``, no planning loop, no file-based context.

    Never raises — returns ``{}`` on any failure.
    """
    try:
        return _execute_skill_inner(
            artifact, task, fixture_contents,
            langfuse_tags=langfuse_tags,
            langfuse_session_id=langfuse_session_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Skill execution failed for %s: %s", getattr(task, "task_id", "?"), exc)
        return {}


def _execute_skill_inner(
    artifact: Any,
    task: Any,
    fixture_contents: dict[str, str],
    *,
    langfuse_tags: list[str] | None = None,
    langfuse_session_id: str | None = None,
) -> dict:
    from langchain.chat_models import init_chat_model
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

    from src.config import DEFAULT_LLM_MODEL
    from src.verification.sandbox_tool import sandbox_execute

    skill_content = getattr(artifact, "skill_content", None) or ""
    result_model = TASK_RESULT_MODELS.get(getattr(task, "task_id", ""), GenericResult)

    system_prompt = (
        _SKILL_EXECUTION_SYSTEM_PROMPT
        + "\n\n--- SKILL INSTRUCTIONS ---\n"
        + skill_content
    )

    user_message = (
        f"Task: {getattr(task, 'task_prompt', '')}\n\n"
        f"Input data:\n{_format_fixture_message(fixture_contents)}"
    )

    llm = init_chat_model(DEFAULT_LLM_MODEL)
    tool_calling_llm = llm.bind_tools([sandbox_execute])

    messages: list = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_message),
    ]

    # LangFuse tracing with inherited tags
    callbacks: list = []
    try:
        from langfuse.langchain import CallbackHandler as LangFuseCallbackHandler
        callbacks = [LangFuseCallbackHandler()]
    except Exception:  # noqa: BLE001
        pass
    invoke_config: dict = {}
    if callbacks:
        invoke_config["callbacks"] = callbacks
        lf_meta: dict = {}
        if langfuse_tags:
            lf_meta["langfuse_tags"] = langfuse_tags
        if langfuse_session_id:
            lf_meta["langfuse_session_id"] = langfuse_session_id
        if lf_meta:
            invoke_config["metadata"] = lf_meta

    def _run_skill_loop() -> dict:
        """Inner loop — runs in a thread for timeout control."""
        tool_calls_made = 0

        # *2 iterations: each iteration is one LLM turn + tool results
        for _ in range(_SKILL_MAX_TOOL_CALLS * 2):
            response = tool_calling_llm.invoke(messages, config=invoke_config)
            messages.append(response)

            if not response.tool_calls:
                break  # LLM is done

            for tool_call in response.tool_calls:
                tool_calls_made += 1
                result = sandbox_execute(**tool_call["args"])
                messages.append(ToolMessage(
                    content=json.dumps(result),
                    tool_call_id=tool_call["id"],
                ))

            if tool_calls_made >= _SKILL_MAX_TOOL_CALLS:
                break

        # Extract structured result. First, try to parse JSON directly from
        # the last successful sandbox stdout (cheapest — no LLM call needed).
        # Fall back to a structured output LLM call if parsing fails.
        last_sandbox_output = _extract_last_sandbox_json(messages)
        if last_sandbox_output is not None:
            if result_model is not GenericResult:
                try:
                    return result_model.model_validate(last_sandbox_output).model_dump()
                except Exception:  # noqa: BLE001
                    pass  # fall through to LLM extraction
            else:
                return last_sandbox_output

        # LLM extraction fallback — use function_calling method to avoid
        # OpenAI's strict schema requirements for structured output.
        structured_llm = llm.with_structured_output(result_model, method="function_calling")
        messages.append(HumanMessage(content=(
            "Based on the sandbox execution results above, extract the final "
            "answer in the required format."
        )))
        structured = structured_llm.invoke(messages, config=invoke_config)
        return structured.model_dump() if hasattr(structured, "model_dump") else dict(structured)

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(_run_skill_loop)
        output = future.result(timeout=SKILL_EXECUTION_TIMEOUT_S)
        pool.shutdown(wait=False)
        task_id = getattr(task, "task_id", "?")
        logger.info("Skill execution for %s returned: %s", task_id, str(output)[:300])
        return output

    except concurrent.futures.TimeoutError:
        pool.shutdown(wait=False)
        task_id = getattr(task, "task_id", "?")
        logger.error(
            "Skill execution timed out after %ds for %s",
            SKILL_EXECUTION_TIMEOUT_S, task_id,
        )
        return {}

    except Exception as exc:  # noqa: BLE001
        pool.shutdown(wait=False)
        task_id = getattr(task, "task_id", "?")
        logger.exception("Skill execution error for %s: %s", task_id, exc)
        return {}


# ---------------------------------------------------------------------------
# AGENT execution
# ---------------------------------------------------------------------------

_AGENT_SYSTEM_FALLBACK = """\
You are an autonomous data analysis agent. You receive input data and must
investigate it, make decisions about what to analyze, and return structured
findings.

You have skills available that provide domain expertise for specific tasks.
Check your available skills — if one matches your task, activate and follow it.

## Tools

You have two types of tools with SEPARATE filesystems:

### sandbox_execute (Docker sandbox — for all computation)
Use sandbox_execute(code, timeout=10, extra_packages=None) to run Python code
in an isolated Docker container. The sandbox has NO access to your workspace
files. To pass data to the sandbox, embed it directly in the Python code
string (e.g., as inline CSV strings or JSON literals). Declare any non-stdlib
packages via extra_packages (e.g., extra_packages=["pandas", "pyyaml"]).

### Built-in workspace tools (agent memory — for planning and notes)
write_todos, read_file, write_file, edit_file, ls, glob, grep operate on your
in-memory workspace. Use write_file for analysis notes and intermediate
reasoning. These files are NOT accessible inside sandbox_execute.

IMPORTANT: Do NOT write data with write_file and then try to read it inside
sandbox_execute — they are separate filesystems. Instead, take the input data
from the user message and embed it directly in your sandbox_execute code.

Return your result in the required structured output format.
"""

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _collect_skill_files(skills_dir: Path | None = None) -> dict:
    """Read all skills from a skills directory for StateBackend.

    Args:
        skills_dir: Directory containing skill subdirectories. Defaults to
            ``<project_root>/data/eval/skills/``.

    Returns dict mapping relative file paths to ``{"content": [lines]}``
    format required by DeepAgents ``StateBackend``.
    """
    skill_files: dict = {}
    if skills_dir is None:
        skills_dir = _PROJECT_ROOT / "data" / "eval" / "skills"
    if not skills_dir.exists():
        return skill_files
    # Use the directory name as the skills= prefix for StateBackend lookup
    skills_prefix = str(skills_dir).rstrip("/")
    for skill_dir in skills_dir.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            rel_path = f"{skills_prefix}/{skill_dir.name}/SKILL.md"
            content = skill_md.read_text(encoding="utf-8")
            skill_files[rel_path] = {"content": content.splitlines()}
    return skill_files


def _ensure_skill_on_disk(
    artifact: Any,
    skill_content: str,
    skill_files: dict,
    skills_dir: Path | None = None,
) -> None:
    """Write agent's ``skill_content`` to disk if not already present.

    Also adds the skill to *skill_files* so the current invocation can
    discover it via ``StateBackend``.
    """
    if skills_dir is None:
        skills_dir = _PROJECT_ROOT / "data" / "eval" / "skills"
    name = getattr(artifact, "capability_name", "unknown-skill")
    skill_dir = skills_dir / name
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md.write_text(skill_content, encoding="utf-8")
    skills_prefix = str(skills_dir).rstrip("/")
    rel_path = f"{skills_prefix}/{name}/SKILL.md"
    if rel_path not in skill_files:
        skill_files[rel_path] = {"content": skill_content.splitlines()}


def execute_agent_artifact(
    artifact: Any,
    task: Any,
    fixture_contents: dict[str, str],
    *,
    langfuse_tags: list[str] | None = None,
    langfuse_session_id: str | None = None,
) -> dict:
    """Execute an AGENT artifact via DeepAgent with progressive skill disclosure.

    Uses ``create_deep_agent()`` with ``skills=`` parameter so the agent
    discovers available skills via progressive disclosure (metadata first,
    full content on activation).

    Never raises — returns ``{}`` on any failure.
    """
    try:
        return _execute_agent_inner(
            artifact, task, fixture_contents,
            langfuse_tags=langfuse_tags,
            langfuse_session_id=langfuse_session_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent execution failed for %s: %s", getattr(task, "task_id", "?"), exc)
        return {}


def _execute_agent_inner(
    artifact: Any,
    task: Any,
    fixture_contents: dict[str, str],
    *,
    langfuse_tags: list[str] | None = None,
    langfuse_session_id: str | None = None,
) -> dict:
    from deepagents import create_deep_agent
    from langchain.chat_models import init_chat_model

    from src.config import DEFAULT_LLM_MODEL
    from src.verification.sandbox_tool import sandbox_execute

    agent_config = getattr(artifact, "agent_config", None) or {}
    system_prompt = agent_config.get("system_prompt", _AGENT_SYSTEM_FALLBACK)

    # NOTE: skill_content is NO LONGER prepended to system_prompt.
    # Skills are loaded via progressive disclosure through the skills= parameter.

    # Determine skills directory — CLI sets SKILLS_OUTPUT_DIR, eval uses default
    from src.orchestrator.generators.skill import SKILLS_OUTPUT_DIR
    skills_dir = SKILLS_OUTPUT_DIR

    # If agent has skill_content, ensure it's on disk and pass ONLY that skill
    # for progressive disclosure. In evaluation, passing all skills from disk
    # causes noise (irrelevant skills from other tasks in the same run).
    # In CLI mode, _collect_skill_files() returns all accumulated skills,
    # which is the intended progressive discovery behavior.
    skill_files: dict = {}
    skill_content = getattr(artifact, "skill_content", None)
    if not skill_content and agent_config.get("is_deep_agent"):
        skill_content = agent_config.get("skill_content")
    if skill_content:
        _ensure_skill_on_disk(artifact, skill_content, skill_files, skills_dir)

    # In CLI mode (skills accumulate), discover all skills on disk.
    # In eval mode (this agent's skill is already in skill_files), skip
    # to avoid loading irrelevant skills from earlier tasks in the same run.
    if not skill_files:
        skill_files = _collect_skill_files(skills_dir)

    skills_prefix = str(skills_dir).rstrip("/") + "/"
    skills_param = [skills_prefix] if skill_files else None

    result_model = TASK_RESULT_MODELS.get(getattr(task, "task_id", ""), GenericResult)

    agent = create_deep_agent(
        model=init_chat_model(DEFAULT_LLM_MODEL),
        tools=[sandbox_execute],
        system_prompt=system_prompt,
        skills=skills_param,
        response_format=result_model,
        name="agent-executor",
    )

    user_message = (
        f"Task: {getattr(task, 'task_prompt', '')}\n\n"
        f"Input data:\n{_format_fixture_message(fixture_contents)}"
    )

    config: dict = {"recursion_limit": _RECURSION_LIMIT}
    try:
        from langfuse.langchain import CallbackHandler as LangFuseCallbackHandler
        config["callbacks"] = [LangFuseCallbackHandler()]
    except Exception as exc:  # noqa: BLE001
        logger.debug("LangFuse handler creation failed (tracing disabled): %s", exc)

    # Inherit LangFuse tags from caller
    lf_meta: dict = {}
    if langfuse_tags:
        lf_meta["langfuse_tags"] = langfuse_tags
    if langfuse_session_id:
        lf_meta["langfuse_session_id"] = langfuse_session_id
    if lf_meta:
        config["metadata"] = lf_meta

    invoke_input: dict = {"messages": [{"role": "user", "content": user_message}]}
    if skill_files:
        invoke_input["files"] = skill_files

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(agent.invoke, invoke_input, config)
        result = future.result(timeout=AGENT_EXECUTION_TIMEOUT_S)
        pool.shutdown(wait=False)

        task_id = getattr(task, "task_id", "?")
        structured = result.get("structured_response")
        if structured is not None:
            output = (
                structured.model_dump()
                if hasattr(structured, "model_dump")
                else dict(structured)
            )
            logger.info("Agent execution for %s returned: %s", task_id, str(output)[:300])
            return output

        logger.warning("Agent executor returned no structured_response for %s", task_id)
        return {}

    except concurrent.futures.TimeoutError:
        pool.shutdown(wait=False)
        task_id = getattr(task, "task_id", "?")
        logger.error(
            "Agent execution timed out after %ds for %s",
            AGENT_EXECUTION_TIMEOUT_S, task_id,
        )
        return {}

    except Exception as exc:  # noqa: BLE001
        pool.shutdown(wait=False)
        task_id = getattr(task, "task_id", "?")
        logger.exception("Agent execution error for %s: %s", task_id, exc)
        return {}
