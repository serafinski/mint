"""SKILL and AGENT artifact execution via DeepAgent.

Follows the same invocation pattern as ``src/verification/adversarial.py``:
create a fresh DeepAgent per invocation, invoke in a ThreadPoolExecutor with
timeout, extract structured_response, never raise.

The SKILL.md content is passed as the DeepAgent's *system_prompt* so the
skill instructions carry maximum weight.  For AGENT artifacts, the agent's
own ``system_prompt`` from ``agent_config`` is used.
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
AGENT_EXECUTION_TIMEOUT_S = 300
_RECURSION_LIMIT = 50

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

_SKILL_SYSTEM_PREAMBLE = """\
You are a skill executor. You receive input data files and must process them
according to the skill instructions below.

Use the sandbox_execute tool to run Python code for any computation. When using
sandbox_execute, write the data files to /tmp/ inside the sandbox first, then
process them. The sandbox has pandas and numpy available.

Return your result in the required structured output format. Be precise with
numbers — round floats to 2 decimal places where appropriate.

--- SKILL INSTRUCTIONS ---
"""


def execute_skill_artifact(
    artifact: Any,
    task: Any,
    fixture_contents: dict[str, str],
) -> dict:
    """Execute a SKILL artifact by invoking a DeepAgent with the skill as system prompt.

    Never raises — returns ``{}`` on any failure.
    """
    try:
        return _execute_skill_inner(artifact, task, fixture_contents)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Skill execution failed for %s: %s", getattr(task, "task_id", "?"), exc)
        return {}


def _execute_skill_inner(
    artifact: Any,
    task: Any,
    fixture_contents: dict[str, str],
) -> dict:
    from deepagents import create_deep_agent
    from langchain.chat_models import init_chat_model

    from src.config import DEFAULT_LLM_MODEL
    from src.verification.sandbox_tool import sandbox_execute

    skill_content = getattr(artifact, "skill_content", None) or ""
    system_prompt = _SKILL_SYSTEM_PREAMBLE + skill_content

    result_model = TASK_RESULT_MODELS.get(getattr(task, "task_id", ""), GenericResult)

    agent = create_deep_agent(
        model=init_chat_model(DEFAULT_LLM_MODEL),
        tools=[sandbox_execute],
        system_prompt=system_prompt,
        response_format=result_model,
        name="skill-executor",
    )

    user_message = (
        f"Task: {getattr(task, 'task_prompt', '')}\n\n"
        f"Input data:\n{_format_fixture_message(fixture_contents)}"
    )

    config = {"recursion_limit": _RECURSION_LIMIT}
    try:
        from langfuse.langchain import CallbackHandler as LangFuseCallbackHandler
        config["callbacks"] = [LangFuseCallbackHandler()]
    except Exception as exc:  # noqa: BLE001
        logger.debug("LangFuse handler creation failed (tracing disabled): %s", exc)

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(
            agent.invoke,
            {"messages": [{"role": "user", "content": user_message}]},
            config,
        )
        result = future.result(timeout=SKILL_EXECUTION_TIMEOUT_S)
        pool.shutdown(wait=False)

        structured = result.get("structured_response")
        if structured is not None:
            output = structured.model_dump() if hasattr(structured, "model_dump") else dict(structured)
            logger.info("Skill execution for %s returned: %s", task.task_id, str(output)[:300])
            return output

        logger.warning("Skill executor returned no structured_response for %s", task.task_id)
        return {}

    except concurrent.futures.TimeoutError:
        pool.shutdown(wait=False)
        logger.error("Skill execution timed out after %ds for %s", SKILL_EXECUTION_TIMEOUT_S, task.task_id)
        return {}

    except Exception as exc:  # noqa: BLE001
        pool.shutdown(wait=False)
        logger.exception("Skill execution error for %s: %s", task.task_id, exc)
        return {}


# ---------------------------------------------------------------------------
# AGENT execution
# ---------------------------------------------------------------------------

_AGENT_SYSTEM_FALLBACK = """\
You are an autonomous data analysis agent. You receive input data and must
investigate it, make decisions about what to analyze, and return structured
findings.

Use the sandbox_execute tool to run Python code for any computation. When using
sandbox_execute, write the data files to /tmp/ inside the sandbox first, then
process them. The sandbox has pandas and numpy available.

Return your result in the required structured output format.
"""


def execute_agent_artifact(
    artifact: Any,
    task: Any,
    fixture_contents: dict[str, str],
) -> dict:
    """Execute an AGENT artifact by invoking a DeepAgent with the agent's config.

    Never raises — returns ``{}`` on any failure.
    """
    try:
        return _execute_agent_inner(artifact, task, fixture_contents)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent execution failed for %s: %s", getattr(task, "task_id", "?"), exc)
        return {}


def _execute_agent_inner(
    artifact: Any,
    task: Any,
    fixture_contents: dict[str, str],
) -> dict:
    from deepagents import create_deep_agent
    from langchain.chat_models import init_chat_model

    from src.config import DEFAULT_LLM_MODEL
    from src.verification.sandbox_tool import sandbox_execute

    # Extract system prompt from agent_config
    agent_config = getattr(artifact, "agent_config", None) or {}
    system_prompt = agent_config.get("system_prompt", _AGENT_SYSTEM_FALLBACK)

    # If the agent has skill content, prepend it to the system prompt
    skill_content = getattr(artifact, "skill_content", None)
    if not skill_content and agent_config.get("is_deep_agent"):
        skill_content = agent_config.get("skill_content")
    if skill_content:
        system_prompt = f"--- SKILL INSTRUCTIONS ---\n{skill_content}\n--- END SKILL ---\n\n{system_prompt}"

    result_model = TASK_RESULT_MODELS.get(getattr(task, "task_id", ""), GenericResult)

    agent = create_deep_agent(
        model=init_chat_model(DEFAULT_LLM_MODEL),
        tools=[sandbox_execute],
        system_prompt=system_prompt,
        response_format=result_model,
        name="agent-executor",
    )

    user_message = (
        f"Task: {getattr(task, 'task_prompt', '')}\n\n"
        f"Input data:\n{_format_fixture_message(fixture_contents)}"
    )

    config = {"recursion_limit": _RECURSION_LIMIT}
    try:
        from langfuse.langchain import CallbackHandler as LangFuseCallbackHandler
        config["callbacks"] = [LangFuseCallbackHandler()]
    except Exception as exc:  # noqa: BLE001
        logger.debug("LangFuse handler creation failed (tracing disabled): %s", exc)

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(
            agent.invoke,
            {"messages": [{"role": "user", "content": user_message}]},
            config,
        )
        result = future.result(timeout=AGENT_EXECUTION_TIMEOUT_S)
        pool.shutdown(wait=False)

        structured = result.get("structured_response")
        if structured is not None:
            output = structured.model_dump() if hasattr(structured, "model_dump") else dict(structured)
            logger.info("Agent execution for %s returned: %s", task.task_id, str(output)[:300])
            return output

        logger.warning("Agent executor returned no structured_response for %s", task.task_id)
        return {}

    except concurrent.futures.TimeoutError:
        pool.shutdown(wait=False)
        logger.error("Agent execution timed out after %ds for %s", AGENT_EXECUTION_TIMEOUT_S, task.task_id)
        return {}

    except Exception as exc:  # noqa: BLE001
        pool.shutdown(wait=False)
        logger.exception("Agent execution error for %s: %s", task.task_id, exc)
        return {}
