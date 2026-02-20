# Plan: Agent Generator (`src/orchestrator/generators/agent.py`)

## Overview

Implement a plain LangGraph node that generates agent configurations (system prompt + node config dict) for gaps classified as `CapabilityType.AGENT`. Follows the same patterns as `classifier.py` (lazy LLM singleton, LangFuse tracing, Command API return).

## Key Design Decisions

1. **Storage in `GeneratedArtifact`**: The schema has `agent_config: dict | None`. I will store all agent generation output in this single dict with keys: `system_prompt` (str), `node_config` (dict with name/model/tools/description), and optionally `skill_content` (str, SKILL.md content for DeepAgent-type agents). No schema modifications needed.

2. **DeepAgent detection**: The LLM prompt will instruct the model to assess whether the gap requires multi-step autonomous reasoning. If so, it should set `is_deep_agent: true` in the output and provide a SKILL.md string. The structured output Pydantic model (`AgentGenerationResult`) is internal to this module only — not added to `src/schemas/`.

3. **Prompt design**: A system prompt with category definitions (plain LangGraph node agent vs. DeepAgent), a complete few-shot example showing both types, and the gap description/query as input. Based on the decomposition.py reference for what a DeepAgent config looks like.

## Files to Create

### 1. `src/orchestrator/prompts/agent_generator.py`

Prompt template with:
- Instructions explaining what agent configs look like
- One few-shot example of a plain agent (e.g., "data quality monitor agent" -> system prompt + node config)
- One few-shot example of a DeepAgent (e.g., "autonomous anomaly investigator" -> system prompt + node config + SKILL.md)
- Placeholder for `{description}` and `{query}`

### 2. `src/orchestrator/generators/agent.py`

Module structure:
- **Internal Pydantic model** `AgentGenerationResult` (used only for `with_structured_output`):
  - `agent_name: str` — hyphen-case name
  - `system_prompt: str` — markdown system prompt for the agent
  - `node_name: str` — LangGraph node function name
  - `model: str` — model identifier (e.g., `deepseek:deepseek-chat`)
  - `tools: list[str]` — tool names the agent needs
  - `description: str` — what this agent does
  - `is_deep_agent: bool` — whether this needs DeepAgent wrapping
  - `skill_content: str | None` — SKILL.md content (only if is_deep_agent)

- **Lazy LLM singleton** `_get_llm()` — `init_chat_model("deepseek:deepseek-chat")`, same pattern as classifier.py

- **`_generate_single_agent(gap: ClassifiedGap, task_context: str) -> GeneratedArtifact`** — core generation logic:
  1. Format the prompt with gap description + query + task_context
  2. Call LLM with `with_structured_output(AgentGenerationResult)`
  3. Validate: system_prompt non-empty, node_name non-empty, etc.
  4. If validation fails: repair pass (send raw output + error back to LLM once)
  5. If is_deep_agent and skill_content provided: validate skill_content has valid YAML frontmatter (basic check, not full quick_validate since it expects a file on disk)
  6. Build and return `GeneratedArtifact(capability_name=..., capability_type=AGENT, agent_config={system_prompt, node_config, skill_content})`
  7. On any LLM failure: log to LangFuse, return None

- **`generate_agent(state: TaskState) -> Command`** — LangGraph node:
  1. Filter `state.classified_gaps` for `classified_type == CapabilityType.AGENT`
  2. For each, call `_generate_single_agent(gap, state.task_description)`
  3. Collect artifacts and errors
  4. Return `Command(update={"generated_artifacts": artifacts, "errors": errors}, goto="next_node")`

### 3. `tests/unit/test_agent_generator.py`

Unit tests (all LLM calls mocked):

1. **`test_generate_agent_basic`** — Mock LLM returns valid `AgentGenerationResult` for a plain agent. Assert `GeneratedArtifact` has correct `capability_name`, `capability_type=AGENT`, `agent_config` with `system_prompt`, `node_config`.

2. **`test_generate_agent_deep_agent`** — Mock LLM returns result with `is_deep_agent=True` and `skill_content`. Assert `agent_config` includes `skill_content`.

3. **`test_generate_agent_system_prompt_nonempty`** — Assert generated system prompt is a non-empty string.

4. **`test_generate_agent_node_config_keys`** — Assert node_config dict has keys: `name`, `model`, `description`.

5. **`test_generate_agent_repair_pass`** — First LLM call raises validation error, second call succeeds. Assert repair was attempted and final artifact is valid.

6. **`test_generate_agent_llm_failure`** — LLM raises exception. Assert error is recorded, no artifact produced, node returns gracefully.

7. **`test_generate_agent_skips_non_agent_gaps`** — State has MCP_TOOL and SKILL gaps. Assert they are ignored.

8. **`test_generate_agent_empty_gaps`** — No AGENT gaps in state. Assert empty artifacts, no errors.

## Constraints Verified

- Uses `src/schemas/` types only (no raw dicts for state)
- `init_chat_model("deepseek:deepseek-chat")` for LLM
- LangFuse tracing via `CallbackHandler`
- Few-shot example in prompt
- Repair pass on validation failure
- No schema modifications
- Command API for routing
