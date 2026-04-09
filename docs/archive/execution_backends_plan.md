# Execution Backends: Three Distinct Runtime Paths

> **🗄️ ARCHIVED 2026-04-10 — IMPLEMENTED 2026-04-08, canonical reference is now `CLAUDE.md` "Three Distinct Execution Backends" section.**
>
> This plan proposed differentiating SKILL and AGENT execution so that misclassification has measurable runtime consequences (the proposal's claim that "a skill that should have been an agent will lack the autonomy to handle state" was previously false in our implementation because both ran as identical DeepAgents). The plan was fully implemented in commit on 2026-04-08 and is now the architecture documented in `CLAUDE.md` and `tests/evaluation/execution.py`.
>
> **What was implemented** (live in the codebase):
> - **MCP_TOOL backend**: `sandbox_execute()` — Docker, no LLM at runtime, deterministic code execution. 0 LLM calls per invocation.
> - **SKILL backend**: Constrained LLM with `init_chat_model().bind_tools([sandbox_execute])`, hard-capped at 3 tool calls. No `write_todos`, no planning loop, no file-based context management. The LLM reads SKILL.md instructions, generates code, executes via `sandbox_execute`, returns the result. 1-3 LLM calls per invocation.
> - **AGENT backend**: Full DeepAgent via `create_deep_agent(model, tools=[sandbox_execute], skills=skills_param, ...)` with progressive skill disclosure via the `skills=` parameter. The agent discovers skills via metadata-first loading and gets `write_todos` planning, file-based context management, and adaptive reasoning. 5-15+ LLM calls per invocation.
>
> All three backends are verified via the eval pilot and CLI smoke tests. The classification decision now has genuine runtime consequences — misclassifying SKILL as MCP_TOOL fails because the LLM-driven SKILL backend isn't invoked; misclassifying SKILL as AGENT works but is more expensive than necessary.
>
> **Canonical reference**: `CLAUDE.md` § "Three Distinct Execution Backends" (the table summarising backend / LLM calls / character per type).
>
> **Implementation reference**: `tests/evaluation/execution.py` (`execute_skill_artifact`, `execute_agent_artifact`, `execute_mcp_tool_in_chain`).
>
> **Original document below preserved for historical context.** The architectural rationale and the discussion of "why constrained LLM + tool calling, not pure LLM → code → sandbox" is still useful reading for anyone modifying the SKILL backend.

---

**Date:** 2026-04-07
**Branch:** `iter/6-execution-backends`
**Parent:** `iter/6-evaluation`
**Status:** ARCHIVED 2026-04-10 — IMPLEMENTED 2026-04-08, all backends verified via eval pilot and CLI smoke tests

---

## Problem

Skills and agents execute identically — both create a DeepAgent with `sandbox_execute` and a system prompt. The only difference is prompt tone (prescribed vs adaptive). This undermines two core thesis claims:

1. **Classification matters.** The proposal claims misclassification produces worse results ("a skill that should have been an agent will lack the autonomy to handle state"). But since DeepAgent is always adaptive, a skill has the same autonomy as an agent. Misclassifying SKILL as AGENT (or vice versa) has no execution consequence.

2. **Context engineering.** The proposal lists context economy as a fundamental tension. The Agent Skills spec solves this via progressive disclosure. Our implementation bypasses it entirely — SKILL.md content is pasted into system prompts.

## Solution

Change `tests/evaluation/execution.py` to implement three genuinely different execution backends. No changes to the pipeline, generators, verification, registry, or CLI.

### Backend 1: MCP Tool (unchanged)

`sandbox_execute()` runs the generated Python code directly in Docker. No LLM at runtime. Deterministic.

```
Input → sandbox_execute(generated_code, inputs) → Output
```

- **LLM calls at runtime:** 0
- **Character:** Deterministic code execution
- **Cost:** Minimal (Docker only)

### Backend 2: Skill (new — constrained LLM with tool calling, no planning)

A constrained LLM agent with `sandbox_execute` as its only tool, hard-capped at 3 tool calls. No `write_todos`, no planning loop, no file-based context management, no adaptive reasoning. The LLM reads the SKILL.md instructions, generates code, executes it via `sandbox_execute`, and returns the result.

```
Input → constrained LLM(system=SKILL.md, tool=sandbox_execute, max_calls=3) → Output
```

- **LLM calls at runtime:** 1-3 tool-calling turns (generate code → execute → optional retry on error)
- **Character:** Prescribed workflow — follow instructions, execute, limited retry
- **Cost:** Moderate (1-3 LLM calls + Docker)

#### Why constrained LLM + tool calling, not pure LLM → code → sandbox

The initial plan proposed a pure approach: single LLM call generates code, we extract it, run it in sandbox. This has critical problems:

1. **Data passing.** Fixture data isn't in the sandbox. The LLM would need to embed all data as string literals in the generated code. For large fixtures this produces enormous code blocks. For binary fixtures (T11 `.xlsx`) it's impossible.
2. **Code extraction is fragile.** Parsing Python from free-form LLM output (mixed with explanations, multiple code blocks, etc.) is unreliable.
3. **Package resolution is orphaned.** No mechanism for the LLM to declare `extra_packages` without structured tool calling.
4. **Zero retry means hard failures.** Any syntax error, wrong import name, or data parsing issue is permanent. DeepAgent naturally retries; a single LLM call doesn't.

Using `sandbox_execute` as a tool solves all four: the LLM writes data to `/tmp/` inside the code (existing pattern), packages are declared via `extra_packages` parameter, and the tool call returns stdout/stderr so the LLM can retry once on error. All existing sandbox infrastructure is reused.

#### Implementation approach

Use `init_chat_model().bind_tools([sandbox_execute])` with a tight system prompt and a hard recursion limit of 6 (≈3 tool calls). No `create_deep_agent()` — no `write_todos`, no file-based context, no summarization middleware, no subagent delegation.

```python
def _execute_skill_inner(artifact, task, fixture_contents):
    from langchain.chat_models import init_chat_model
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    from src.config import DEFAULT_LLM_MODEL
    from src.verification.sandbox_tool import sandbox_execute

    skill_content = getattr(artifact, "skill_content", None) or ""
    result_model = TASK_RESULT_MODELS.get(getattr(task, "task_id", ""), GenericResult)

    system_prompt = (
        SKILL_EXECUTION_SYSTEM_PROMPT
        + "\n\n--- SKILL INSTRUCTIONS ---\n"
        + skill_content
    )

    user_message = (
        f"Task: {getattr(task, 'task_prompt', '')}\n\n"
        f"Input data:\n{_format_fixture_message(fixture_contents)}"
    )

    # Build a minimal tool-calling loop
    # (NOT DeepAgent — no write_todos, no planning, no file context)
    llm = init_chat_model(DEFAULT_LLM_MODEL)
    tool_calling_llm = llm.bind_tools([sandbox_execute])

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_message),
    ]

    # LangFuse tracing (same pattern as current implementation)
    callbacks = []
    try:
        from langfuse.langchain import CallbackHandler as LangFuseCallbackHandler
        callbacks = [LangFuseCallbackHandler()]
    except Exception:  # noqa: BLE001
        pass
    invoke_config = {"callbacks": callbacks} if callbacks else {}

    # Tight loop: max 3 tool calls (generate code, retry on error, final answer)
    MAX_TOOL_CALLS = 3
    tool_calls_made = 0

    for _ in range(MAX_TOOL_CALLS * 2):  # *2 for LLM turn + tool result turn
        response = tool_calling_llm.invoke(messages, config=invoke_config)
        messages.append(response)

        if not response.tool_calls:
            break  # LLM is done, extract final answer

        for tool_call in response.tool_calls:
            tool_calls_made += 1
            result = sandbox_execute(**tool_call["args"])
            messages.append(ToolMessage(
                content=json.dumps(result),
                tool_call_id=tool_call["id"],
            ))

        if tool_calls_made >= MAX_TOOL_CALLS:
            break

    # Parse structured result from final LLM response.
    # Use a follow-up structured output call to extract the result model.
    # This adds one LLM call but is more reliable than parsing JSON from
    # free-form text or sandbox stdout.
    structured_llm = llm.with_structured_output(result_model)
    final_prompt = (
        "Based on the sandbox execution results above, extract the final "
        "answer in the required format."
    )
    messages.append(HumanMessage(content=final_prompt))
    return structured_llm.invoke(messages, config=invoke_config).model_dump()
```

> **Timeout:** The skill execution loop above must be wrapped in the same
> `ThreadPoolExecutor` + `future.result(timeout=SKILL_EXECUTION_TIMEOUT_S)`
> pattern used by the current implementation and by the agent backend.
> The outer `execute_skill_artifact()` already catches all exceptions and
> returns `{}`, but the timeout prevents the LLM loop from hanging
> indefinitely.

> **Note on binary fixtures:** `_read_fixture_contents` reads ALL files via
> `read_text(errors="replace")`, including `.xlsx`. This is a pre-existing
> limitation — Excel content becomes garbled text in the user message. The
> current DeepAgent implementation has the same issue. The constrained LLM
> skill backend inherits it. The LLM must compensate by using the CSV files
> that accompany the Excel file (e.g., T11 has 2 CSVs + 1 Excel). Fixing
> binary fixture handling is orthogonal to this change.

#### SKILL_EXECUTION_SYSTEM_PROMPT

```python
SKILL_EXECUTION_SYSTEM_PROMPT = """\
You are a skill executor. You receive a task with input data and must follow \
the skill instructions below to produce the required output.

You have ONE tool available: sandbox_execute(code, timeout=10, extra_packages=None).
It runs Python code in an isolated Docker container and returns \
{"stdout": str, "stderr": str, "exit_code": int, "success": bool}.

## Rules
- Follow the skill instructions precisely — do not deviate or improvise.
- Write the input data to /tmp/ files inside the sandbox, then process them.
- Print your final result as JSON to stdout.
- Declare any non-stdlib packages via extra_packages (e.g., extra_packages=["pandas"]).
- Use the pip install name, not the Python import name (e.g., "pyyaml" not "yaml").
- You have at most 3 tool calls. Do not plan — just execute the prescribed steps.
"""
```

#### Why this is different from Agent (DeepAgent)

| Dimension | Skill (constrained LLM) | Agent (DeepAgent) |
|---|---|---|
| Planning | None — no `write_todos` | Full planning loop |
| Tool calls | Hard cap: 3 | Soft cap: ~50 (recursion_limit) |
| File context | None — no `read_file`/`write_file` | Full file-based context management |
| Summarization | None | Auto-summarizes at 85% context |
| Subagent delegation | None | Available (though unused) |
| Adaptive reasoning | Minimal — 1 retry on error | Full — observes, adapts, re-plans |
| Skill discovery | None — skill injected in system prompt | Progressive disclosure via `skills=` |
| Middleware stack | None | TodoList, Filesystem, Summarization, etc. |

#### When misclassification hurts

- A task classified as SKILL but needing adaptive reasoning (e.g., anomaly investigation) fails because the 3-call cap prevents exploration, hypothesis formation, and strategy adaptation.
- A task classified as AGENT but having a prescribed workflow wastes tokens on planning, file management, and summarization that add no value.

### Backend 3: Agent (enhanced — proper skill loading via progressive disclosure)

DeepAgent with `tools=[sandbox_execute]` plus native `skills=` parameter for progressive disclosure. The agent discovers available skills from the registry, loads them on demand, and follows them while maintaining adaptive reasoning.

```
Input → DeepAgent(skills=[registry_skills], tools=[sandbox_execute]) → adaptive loop → Output
```

- **LLM calls at runtime:** 5-15 (planning + iterative tool use)
- **Character:** Autonomous — plans approach, executes code, observes results, adapts
- **Cost:** High (full DeepAgent loop)

#### Implementation changes

```python
def _execute_agent_inner(artifact, task, fixture_contents):
    from deepagents import create_deep_agent
    from langchain.chat_models import init_chat_model
    from src.config import DEFAULT_LLM_MODEL
    from src.verification.sandbox_tool import sandbox_execute

    agent_config = getattr(artifact, "agent_config", None) or {}
    system_prompt = agent_config.get("system_prompt", _AGENT_SYSTEM_FALLBACK)

    # NOTE: Do NOT prepend skill_content to system_prompt anymore.
    # Skills are loaded via progressive disclosure below.

    result_model = TASK_RESULT_MODELS.get(getattr(task, "task_id", ""), GenericResult)

    # Collect skills from disk for progressive disclosure
    skill_files = _collect_skill_files()

    # If agent has skill_content but it's not on disk yet, write it
    skill_content = getattr(artifact, "skill_content", None)
    if not skill_content and agent_config.get("is_deep_agent"):
        skill_content = agent_config.get("skill_content")
    if skill_content:
        _ensure_skill_on_disk(artifact, skill_content, skill_files)

    skills_param = [".deepagents/skills/"] if skill_files else None

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

    config = {"recursion_limit": _RECURSION_LIMIT}
    try:
        from langfuse.langchain import CallbackHandler as LangFuseCallbackHandler
        config["callbacks"] = [LangFuseCallbackHandler()]
    except Exception:  # noqa: BLE001
        pass

    invoke_input = {"messages": [{"role": "user", "content": user_message}]}
    if skill_files:
        invoke_input["files"] = skill_files

    # ThreadPoolExecutor timeout pattern (unchanged from current implementation)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(agent.invoke, invoke_input, config)
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
```

#### Key changes from current implementation

1. **Removed `skill_content` injection into system prompt.** Skills are now loaded via `skills=` parameter, not pasted into the system prompt. This is the progressive disclosure fix.
2. **Added `skills=` parameter** pointing to `.deepagents/skills/` directory.
3. **Added `files=` in invoke** providing skill file content to `StateBackend`.
4. **Use `_PROJECT_ROOT`** to compute absolute disk paths for skill file reading, but relative paths in the `skills=` and `files=` keys (must match for `StateBackend` lookup).
5. **Preserved `skill_content` extraction logic** from `artifact.skill_content` and `agent_config.get("skill_content")` — same guard as current code, but routes to `_ensure_skill_on_disk` instead of system prompt injection.
6. **Preserved LangFuse callback setup** and ThreadPoolExecutor timeout pattern from current implementation.

#### Skill file collection

```python
_PROJECT_ROOT = Path(__file__).resolve().parents[2]  # tests/evaluation/ → project root

def _collect_skill_files() -> dict:
    """Read all skills from .deepagents/skills/ for StateBackend.

    Returns dict mapping file paths to {"content": [lines]} format
    required by DeepAgents StateBackend.
    """
    skill_files = {}
    skills_dir = _PROJECT_ROOT / ".deepagents" / "skills"
    if not skills_dir.exists():
        return skill_files
    for skill_dir in skills_dir.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            # Use relative path matching the skills= prefix
            rel_path = f".deepagents/skills/{skill_dir.name}/SKILL.md"
            content = skill_md.read_text(encoding="utf-8")
            skill_files[rel_path] = {"content": content.splitlines()}
    return skill_files


def _ensure_skill_on_disk(artifact, skill_content, skill_files):
    """Write agent's skill_content to disk if not already present."""
    name = artifact.capability_name
    skill_dir = _PROJECT_ROOT / ".deepagents" / "skills" / name
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md.write_text(skill_content, encoding="utf-8")
    # Also add to skill_files for this invocation
    rel_path = f".deepagents/skills/{name}/SKILL.md"
    if rel_path not in skill_files:
        skill_files[rel_path] = {"content": skill_content.splitlines()}
```

#### Progressive disclosure flow

1. Agent starts with metadata only (name + description of each skill, ~100 tokens each)
2. When the task matches a skill's description, agent activates it (loads full SKILL.md)
3. Agent follows skill instructions while maintaining adaptive reasoning
4. Agent can use `sandbox_execute` for computation at any point

#### Agent system prompt update

The agent's system prompt should mention skill availability. Add to `_AGENT_SYSTEM_FALLBACK`:

```python
_AGENT_SYSTEM_FALLBACK = """\
You are an autonomous data analysis agent. You receive input data and must
investigate it, make decisions about what to analyze, and return structured
findings.

You have skills available that provide domain expertise for specific tasks.
Check your available skills — if one matches your task, activate and follow it.

Use the sandbox_execute tool to run Python code for any computation. When using
sandbox_execute, write the data files to /tmp/ inside the sandbox first, then
process them. Declare any needed packages via extra_packages.

Return your result in the required structured output format.
"""
```

## What changes

### Code changes

| File | Change |
|---|---|
| `tests/evaluation/execution.py` | Rewrite `_execute_skill_inner()` to constrained LLM with tool calling (max 3 calls). Enhance `_execute_agent_inner()` with `skills=` parameter, skill file collection, remove `skill_content` system prompt injection. Add `_collect_skill_files()`, `_ensure_skill_on_disk()`, `SKILL_EXECUTION_SYSTEM_PROMPT`. Update `_AGENT_SYSTEM_FALLBACK` to mention skill discovery. |

### Documentation updates (after implementation)

| File | Change |
|---|---|
| `CLAUDE.md` | Update "Skill Execution" section (no longer a limitation). Update stable interfaces for changed execution semantics. |
| `.claude/skills/deep-agents/SKILL.md` | Update execution patterns to reflect three distinct backends. |
| `docs/audit_classifier_agent_prompt_rewrite.md` | Note that skill/agent execution gap is resolved. |

### No changes needed

| File | Why |
|---|---|
| `src/orchestrator/pipeline.py` | Pipeline generates artifacts — execution is downstream |
| `src/orchestrator/generators/` | Generators produce artifacts — execution is separate |
| `src/verification/` | Verification checks artifacts before execution |
| `src/registry/` | Registry stores/retrieves — doesn't execute |
| `src/cli/` | CLI uses pipeline — evaluation execution is separate |
| `tests/evaluation/harness.py` | Calls `execute_skill_artifact()` / `execute_agent_artifact()` — interface unchanged (both still return `dict`, never raise) |
| `tests/evaluation/tasks.py` | Task definitions unchanged |
| `scripts/` | Scripts use harness — interface unchanged |

## Issues identified during critique (all addressed)

| # | Issue | Resolution |
|---|---|---|
| 1 | Data passing: fixture data not in sandbox | Solved — `sandbox_execute` as a tool lets LLM write data to `/tmp/` inside code (existing pattern) |
| 2 | Code extraction fragile | Solved — no code extraction needed; LLM calls `sandbox_execute` directly via tool calling |
| 3 | Package resolution orphaned | Solved — LLM passes `extra_packages` in `sandbox_execute` tool call |
| 4 | No retry = hard failures | Solved — max 3 tool calls allows 1 retry on sandbox error |
| 5 | Structured output mismatch | Solved — final structured output call after tool loop extracts result model |
| 6 | Double skill injection in agent | Solved — removed `skill_content` prepend to system prompt; skills loaded via `skills=` only |
| 7 | Agent ignores skills | Solved — `_AGENT_SYSTEM_FALLBACK` updated to mention skill availability |
| 8 | Path alignment fragile | Solved — use `_PROJECT_ROOT` for absolute paths, relative paths in `skills=` and `files=` keys |
| 9 | Binary fixtures (Excel) | Solved — `sandbox_execute` tool call handles binary data via existing base64 encoding in sandbox |
| 10 | Harness interface change | Verified — interface unchanged: both functions still return `dict`, never raise |

## Verification plan

1. Run existing unit tests — must still pass (814)
2. Pilot spot-check: one SKILL task (T04 inventory reorder) to confirm constrained LLM backend works
3. Pilot spot-check: one AGENT task (T16 root cause diagnosis) to confirm progressive disclosure works
4. Compare token costs: skill execution should use ~3-5x fewer tokens than agent execution
5. Confirm in LangFuse traces:
   - Skill traces show 1-3 LLM calls (not 5-15)
   - Agent traces show skill discovery + activation via progressive disclosure
6. Confirm misclassification impact: manually reclassify one SKILL task as AGENT and one AGENT task as SKILL, compare success + cost

## Thesis implications

### What this enables

- **Chapter 4:** Three genuinely different execution backends, each with different runtime characteristics. Classification decisions have measurable consequences (tool: 0 LLM calls, skill: 1-3 LLM calls, agent: 5-15 LLM calls).
- **Chapter 6:** Token cost comparison across types becomes meaningful. Misclassification impact can be measured via cross-classification experiment.
- **Context engineering claim:** Progressive disclosure demonstrated at two levels:
  1. Gap detection — semantic registry search retrieves only relevant capabilities
  2. Agent skill loading — DeepAgents native progressive disclosure mechanism (metadata → full content on activation)

### Risks

- **Skill backend may be too constrained.** 3 tool calls may not suffice for complex prescribed workflows with multiple data sources (e.g., T14 with 3 files and 4 pipeline stages). Mitigation: monitor T04/T11/T12/T14/T15 success rates; if consistently below 50%, consider raising cap to 5.
- **Agent skill discovery may add noise.** If the agent sees irrelevant skills, it may waste tokens activating and reading them. Mitigation: in evaluation snapshots, `.deepagents/skills/` starts clean — only skills generated during the current run are present.
- **Evaluation results will change.** SKILL tasks may have different (possibly lower) success rates with the constrained backend. This is desirable — it validates that classification accuracy matters. If skill success rates are comparable to agent rates, it suggests the constrained backend is sufficient for prescribed workflows.
