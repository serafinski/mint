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


def _fixtures_to_workspace_files(fixture_contents: dict[str, str]) -> dict:
    """Convert fixtures to DeepAgents ``StateBackend`` workspace-file format.

    Each fixture becomes an entry keyed by its bare filename (no path prefix)
    with ``{"content": [lines]}`` — the format DeepAgents' ``read_file`` tool
    expects when reading from the in-memory workspace.

    This is the fix for the AGENT execution filesystem-isolation bug: before,
    fixtures were only embedded as strings in the user message, so when the
    generated SKILL.md told the agent to ``read_file("t16_system_metrics.csv")``,
    the workspace had no such file and ``read_file`` errored out. With this
    helper, fixtures are placed in the workspace alongside the SKILL.md, and
    ``read_file`` works as the skill instructs. The user message still contains
    inline copies too (for LLMs that prefer inline context), which makes the
    agent robust to either access pattern.
    """
    files: dict = {}
    for filename, content in fixture_contents.items():
        # StateBackend expects splitlines for content. Use keepends=False
        # (default) so the lines don't carry trailing newlines — matches
        # how ``_collect_skill_files`` writes skills.
        files[filename] = {"content": content.splitlines()}
    return files


# ---------------------------------------------------------------------------
# SKILL execution
# ---------------------------------------------------------------------------

def _extract_last_sandbox_json(messages: list) -> dict | None:
    """Try to extract JSON from the last successful sandbox_execute result.

    Tries three strategies in order:
    1. Parse the entire stdout as JSON (handles pretty-printed multi-line
       ``json.dumps(result, indent=2)`` output, which is common).
    2. Parse the last non-empty line of stdout as JSON (handles the case
       where the skill's code prints debug info before a final single-line
       JSON result).
    3. Scan stdout for the last ``{...}`` JSON object via brace matching
       (handles mixed debug + JSON output when the JSON isn't on its own
       line).
    """
    for msg in reversed(messages):
        # ToolMessage contains sandbox_execute results
        content = getattr(msg, "content", None)
        if not content or not isinstance(content, str):
            continue
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            continue
        if not (isinstance(parsed, dict) and parsed.get("success") and parsed.get("stdout")):
            continue
        stdout = parsed["stdout"].strip()
        if not stdout:
            continue

        # Strategy 1: whole stdout is JSON (pretty-printed or single-line)
        try:
            result = json.loads(stdout)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

        # Strategy 2: last non-empty line is JSON (debug + final line)
        lines = [line for line in stdout.splitlines() if line.strip()]
        if lines:
            try:
                result = json.loads(lines[-1])
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        # Strategy 3: last {...} object via brace matching
        last_obj = _extract_last_json_object(stdout)
        if last_obj is not None:
            return last_obj

    return None


def _extract_last_json_object(text: str) -> dict | None:
    """Find the last balanced ``{...}`` JSON object in ``text`` and parse it.

    Uses a simple brace counter scanning backwards from the last ``}``.
    Returns ``None`` if no valid JSON object is found.
    """
    end = text.rfind("}")
    if end == -1:
        return None
    depth = 0
    for i in range(end, -1, -1):
        ch = text[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            depth -= 1
            if depth == 0:
                candidate = text[i : end + 1]
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    return None
    return None


def _extract_last_ai_json(messages: list) -> dict | None:
    """Fallback: parse the final AI message content as JSON.

    Used when the skill's sandbox output cannot be parsed but the LLM wrote
    the answer directly in its final AIMessage content as a JSON string.
    """
    for msg in reversed(messages):
        msg_type = getattr(msg, "type", None)
        if msg_type != "ai":
            continue
        content = getattr(msg, "content", None)
        if not content or not isinstance(content, str):
            continue
        stripped = content.strip()
        if not stripped:
            continue
        # Try parsing the whole content as JSON first
        try:
            result = json.loads(stripped)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
        # Fall back to brace-matched extraction
        result = _extract_last_json_object(stripped)
        if result is not None:
            return result
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

## CRITICAL: Output Only What the Skill Asks For
The task description in the user message is provided for CONTEXT ONLY — it \
tells you what the overall pipeline does. You must NOT compute or return the \
final task answer. Produce ONLY the output described in the skill instructions \
above. If the skill says "return filtered products", return filtered products \
— do NOT also compute reorder costs, aggregates, or any other fields that \
belong to later steps in the pipeline. Later steps will handle those.
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

        # Extract structured result. Tiered fallback chain, cheapest first:
        #   1. Parse JSON from the last sandbox_execute stdout (free)
        #   2. Parse JSON from the final AIMessage content (free — handles
        #      cases where the LLM wrote the answer as its final text reply)
        #   3. Only if both fail, do a structured-output LLM call
        #
        # For GenericResult (CLI path, no task_id), the LLM extraction has no
        # useful schema to guide it and will produce an empty dict — so we
        # skip it entirely and return whatever the non-LLM fallbacks found,
        # even if that's an empty dict.
        last_sandbox_output = _extract_last_sandbox_json(messages)
        if last_sandbox_output is not None:
            if result_model is not GenericResult:
                try:
                    return result_model.model_validate(last_sandbox_output).model_dump()
                except Exception:  # noqa: BLE001
                    pass  # fall through to LLM extraction
            else:
                return last_sandbox_output

        last_ai_output = _extract_last_ai_json(messages)
        if last_ai_output is not None:
            if result_model is not GenericResult:
                try:
                    return result_model.model_validate(last_ai_output).model_dump()
                except Exception:  # noqa: BLE001
                    pass
            else:
                return last_ai_output

        # Structured-output LLM fallback is only useful for task-specific
        # models (T04Result, T11Result, etc.) where the LLM has schema to
        # fill. For GenericResult, this produces {"result": {}} — worse than
        # returning the raw sandbox output — so we skip it.
        if result_model is GenericResult:
            logger.warning(
                "Skill execution produced no extractable JSON output "
                "(CLI path, GenericResult); returning empty dict"
            )
            return {}

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
# MCP_TOOL chain execution (Phase 3 — compositional executor)
# ---------------------------------------------------------------------------


def execute_mcp_tool_in_chain(
    source_code: str,
    step_index: int,
    workspace_path: Path,
    pip_packages: list[str] | None = None,
    capability_name: str = "tool",
) -> dict:
    """Execute an MCP_TOOL as one step of a chain with workspace state passing.

    Reads inputs from ``/workspace/fixtures/`` and ``/workspace/step_*_output.json``,
    runs the tool function, writes output to ``/workspace/step_<N>_output.json``.

    Never raises — returns ``{}`` on any failure.
    """
    from src.verification.behavioral import _extract_function_name
    from src.verification.sandbox_tool import (
        resolve_extra_packages,
        sandbox_execute_in_workspace,
    )

    func_name = _extract_function_name(source_code) or capability_name.replace("-", "_")

    sandbox_code = f"""\
import asyncio, json, inspect, os, glob

__name__ = "not_main"

{source_code}

async def _run():
    try:
        _all_inputs = {{}}
        _fixture_paths = []

        # Read upstream step outputs FIRST (higher priority for later steps).
        # These are the structured JSON handoffs from previous chain steps —
        # the proposal's "world state at each handoff point".
        for _sf in sorted(glob.glob("/workspace/step_*_output.json")):
            _step_name = os.path.basename(_sf).replace("_output.json", "")
            try:
                with open(_sf) as _f:
                    _data = json.load(_f)
                if isinstance(_data, dict):
                    _all_inputs.update(_data)
                _all_inputs[_step_name] = _data
            except Exception:
                pass

        # Read fixture files. For step 0, fixture filenames are added to
        # _all_inputs as generic keys (the parser step needs broad access).
        # For later steps, fixtures are only in _fixture_paths for explicit
        # parameter-name matching — they won't be assigned to unrelated params
        # via the generic fuzzy matcher below.
        _fixtures_dir = "/workspace/fixtures"
        if os.path.isdir(_fixtures_dir):
            for _fp in sorted(os.listdir(_fixtures_dir)):
                _full = os.path.join(_fixtures_dir, _fp)
                if os.path.isfile(_full):
                    _fixture_paths.append(_full)
                    if {step_index} == 0:
                        _all_inputs[_fp] = _full

        # Match available inputs to function parameters.
        # For steps 1+ (middle/last), prefer passing all upstream data as
        # a single `upstream_data` dict — this is robust against key name
        # mismatches. Falls back to per-key matching if the function does
        # not accept `upstream_data`.
        _sig = inspect.signature({func_name})
        _valid = set(_sig.parameters.keys())

        if {step_index} > 0 and "upstream_data" in _valid:
            # Collect only upstream step outputs (not fixtures) into one dict
            _upstream = {{}}
            for _sf in sorted(glob.glob("/workspace/step_*_output.json")):
                try:
                    with open(_sf) as _uf:
                        _ud = json.load(_uf)
                    if isinstance(_ud, dict):
                        _upstream.update(_ud)
                except Exception:
                    pass
            _kwargs = {{"upstream_data": _upstream}}
        else:
            _kwargs = {{k: v for k, v in _all_inputs.items() if k in _valid}}

        # Path-like parameter matching: assign fixture paths to params
        # whose names suggest they expect a file path (file_path, csv_path, etc.)
        # For step 0: any path-like param gets the first unused fixture (broad).
        # For later steps: only match when the fixture filename overlaps with
        # the parameter name (e.g. param "xlsx_path" matches "products.xlsx").
        # This prevents binary fixtures from being blindly assigned to unrelated
        # params in downstream steps (T11 .xlsx crash reproducer).
        _PATH_KEYWORDS = {{"path", "file", "csv", "log", "json", "txt", "xml",
                          "yaml", "yml", "tsv", "xlsx", "doc", "input"}}
        _required = {{
            name for name, p in _sig.parameters.items()
            if p.default is inspect.Parameter.empty
        }}
        _unmatched = _required - set(_kwargs)
        _used_fixtures = set()
        for _p in sorted(_unmatched):
            _p_lower = _p.lower()
            if any(kw in _p_lower for kw in _PATH_KEYWORDS):
                for _fp in _fixture_paths:
                    if _fp in _used_fixtures:
                        continue
                    if {step_index} == 0:
                        # Step 0: broad matching — first unused fixture
                        _kwargs[_p] = _fp
                        _used_fixtures.add(_fp)
                        break
                    else:
                        # Later steps: name-aware matching only
                        _fp_words = set(os.path.splitext(os.path.basename(_fp))[0].lower().replace("_", " ").replace("-", " ").split())
                        _p_words = set(_p_lower.replace("_", " ").replace("-", " ").split())
                        if _fp_words & _p_words:
                            _kwargs[_p] = _fp
                            _used_fixtures.add(_fp)
                            break

        # Fuzzy word-overlap match for remaining unmatched params
        _still_unmatched = _required - set(_kwargs)
        _unused = {{k: v for k, v in _all_inputs.items() if k not in _kwargs}}
        def _words(s):
            return set(w.rstrip("s") for w in s.lower().replace("_", " ").split() if len(w) > 2)
        for _p in _still_unmatched:
            _pw = _words(_p)
            best, best_n = None, 0
            for _ek, _ev in _unused.items():
                n = len(_pw & _words(_ek))
                if n > best_n:
                    best, best_n = (_ek, _ev), n
            if best_n > 0:
                _kwargs[_p] = best[1]

        # Diagnostic: log what the tool receives and produces (stderr)
        import sys
        print(f"[CHAIN STEP {step_index}] Function: {func_name}", file=sys.stderr)
        print(f"[CHAIN STEP {step_index}] _all_inputs keys: {{list(_all_inputs.keys())}}", file=sys.stderr)
        print(f"[CHAIN STEP {step_index}] _fixture_paths: {{_fixture_paths}}", file=sys.stderr)
        print(f"[CHAIN STEP {step_index}] _kwargs keys: {{list(_kwargs.keys())}}", file=sys.stderr)
        for _k, _v in _kwargs.items():
            _v_str = str(_v)[:200] if not isinstance(_v, str) else _v[:200]
            print(f"[CHAIN STEP {step_index}]   {{_k}} = {{_v_str}}", file=sys.stderr)

        result = await {func_name}(**_kwargs)

        print(f"[CHAIN STEP {step_index}] result keys: {{list(result.keys()) if isinstance(result, dict) else type(result).__name__}}", file=sys.stderr)

        # Write output to workspace for downstream steps
        with open(f"/workspace/step_{step_index}_output.json", "w") as _out:
            json.dump(result, _out, default=str)

        print(json.dumps({{"result": result}}, default=str))
    except Exception as e:
        print(json.dumps({{"error": str(e)}}))

asyncio.run(_run())
"""
    extra_pkgs = pip_packages if pip_packages else resolve_extra_packages(source_code)
    exec_timeout = 60 if extra_pkgs else 30

    result = sandbox_execute_in_workspace(
        sandbox_code,
        workspace_path=workspace_path,
        timeout=exec_timeout,
        extra_packages=extra_pkgs or None,
    )

    # Log chain step diagnostics from stderr (always, not just on failure)
    stderr = result.get("stderr") or ""
    chain_diag = [l for l in stderr.splitlines() if "[CHAIN STEP" in l]
    if chain_diag:
        for line in chain_diag:
            logger.info("  %s", line)

    if not result.get("success") or not result.get("stdout"):
        logger.warning(
            "Chain MCP_TOOL step %d (%s) failed: exit=%s stderr=%s",
            step_index,
            capability_name,
            result.get("exit_code"),
            stderr[:500],
        )
        return {}

    try:
        parsed = json.loads(result["stdout"].strip().splitlines()[-1])
        if "error" in parsed:
            logger.warning(
                "Chain MCP_TOOL %s raised: %s", capability_name, str(parsed["error"])[:500]
            )
            return {}
        return parsed.get("result", {}) or {}
    except (json.JSONDecodeError, KeyError, IndexError):
        logger.warning("Chain MCP_TOOL output parse failed for %s", capability_name)
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

    # Build subagents list if this is a lead-with-subagents artifact.
    # Each subagent config is a dict with name, description, system_prompt, tools.
    # The tool names are resolved to actual tool objects (only sandbox_execute).
    subagents_config = agent_config.get("subagents")
    subagents_param = None
    if subagents_config:
        subagents_param = []
        for sub in subagents_config:
            sub_tools = []
            for tool_name in sub.get("tools", []):
                if tool_name == "sandbox_execute":
                    sub_tools.append(sandbox_execute)
            subagents_param.append({
                "name": sub["name"],
                "description": sub["description"],
                "system_prompt": sub["system_prompt"],
                "tools": sub_tools,
            })
        logger.info(
            "Agent executor using lead-with-subagents: %d subagent(s)",
            len(subagents_param),
        )

    agent_kwargs: dict = {
        "model": init_chat_model(DEFAULT_LLM_MODEL),
        "tools": [sandbox_execute],
        "system_prompt": system_prompt,
        "skills": skills_param,
        "response_format": result_model,
        "name": "agent-executor",
    }
    if subagents_param:
        agent_kwargs["subagents"] = subagents_param

    agent = create_deep_agent(**agent_kwargs)

    # Build the workspace files dict: skills (for progressive disclosure) +
    # fixtures (so `read_file("filename")` works from the agent's workspace).
    # Without the fixtures in the workspace, any generated SKILL.md that
    # tells the agent to `read_file(...)` the input files will fail —
    # previously the agent sometimes recovered by falling back to
    # `sandbox_execute` with inline data, but that was LLM non-determinism,
    # not correct behavior (see T16 MINT CLI failure on 2026-04-08).
    workspace_files: dict = dict(skill_files)  # start with skills
    fixture_workspace = _fixtures_to_workspace_files(fixture_contents)
    for filename, entry in fixture_workspace.items():
        # Don't clobber a skill with the same name (unlikely but safe).
        if filename not in workspace_files:
            workspace_files[filename] = entry

    # Update the user message to explicitly list the workspace filenames so
    # the LLM knows exactly what to pass to `read_file`. Inline copies of the
    # file contents are still included below for LLMs that prefer inline
    # context or that want to skip the read_file round-trip.
    fixture_filenames = list(fixture_contents.keys())
    if fixture_filenames:
        workspace_note = (
            "The following input files are available in your workspace "
            "(readable via `read_file(\"<filename>\")`):\n"
            + "\n".join(f"  - {name}" for name in fixture_filenames)
            + "\n\nInline copies of the files are also provided below for "
            "convenience."
        )
    else:
        workspace_note = ""

    user_message = (
        f"Task: {getattr(task, 'task_prompt', '')}\n\n"
        + (f"{workspace_note}\n\n" if workspace_note else "")
        + f"Input data:\n{_format_fixture_message(fixture_contents)}"
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
    if workspace_files:
        invoke_input["files"] = workspace_files

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
