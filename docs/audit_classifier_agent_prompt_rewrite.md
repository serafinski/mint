# Audit: Classifier & Agent Generator Prompt Rewrite Impact

**Date:** 2026-04-07  
**Scope:** `src/`, `tests/`, `scripts/`, `tools/`, `sandbox/`  
**Context:** Two prompts were rewritten:

1. **Classifier prompt** — changed from complexity-axis model ("can it be one function?" → tool, "multiple stages" → skill, "planning" → agent) to three-layer architectural model (tool = stateless execution, skill = procedural knowledge/domain expertise, agent = autonomous adaptive reasoning).

2. **Agent generator prompt** — removed `tools` field from `AgentGenerationResult` and `node_config`. Now explicitly lists the real available tools (sandbox_execute + DeepAgent built-ins) in the prompt.

---

## Findings Requiring Action

### F1: Seed agent config has stale `tools` key in `node_config`

**File:** `src/registry/store.py:526`  
**Severity:** Must fix  
**Details:** The `_populate_seed_capabilities()` method constructs a hardcoded agent config string that includes `"tools": ["sandbox_execute"]` inside `node_config`. This contradicts the agent generator which no longer produces a `tools` key.

```python
'"node_config": {"name": "data_analysis_node", '
f'"model": "{DEFAULT_LLM_MODEL}", '
'"tools": ["sandbox_execute"], '   # ← stale, must remove
'"description": "Autonomous EDA agent"}, '
```

**Action:** Remove the `'"tools": ["sandbox_execute"], '` line.  
**Status:** RESOLVED (2026-04-07)

---

## Findings Confirmed Safe (No Action Required)

### S1: Agent generator code (`src/orchestrator/generators/agent.py`)

`AgentGenerationResult` Pydantic model correctly has no `tools` field. The `node_config` dict construction at line 254 correctly omits `tools`. **OK.**

### S2: Agent config consumers

All code that reads `agent_config` only accesses `system_prompt`, `is_deep_agent`, `skill_content`, and `node_config["name"/"model"/"description"]`. No consumer reads `node_config["tools"]`.

- `src/orchestrator/loading_node.py` — serializes agent_config without assuming `tools`. **OK.**
- `src/cli/display.py` — renders agent_config without reference to `tools`. **OK.**
- `src/verification/gate.py` — dispatches by capability_type, serializes for scanning. **OK.**
- `tests/evaluation/execution.py` — only reads `system_prompt`, `is_deep_agent`, `skill_content`. **OK.**
- `scripts/run_task.py` — only reads `system_prompt`, `is_deep_agent`. **OK.**
- `scripts/test_verification.py` — only reads `system_prompt`, `is_deep_agent`. **OK.**

### S3: Classifier consumers

All code downstream of the classifier only checks the `CapabilityType` enum value (MCP_TOOL, SKILL, AGENT). No code depends on the rationale text or the classification criteria used. A change in classification distribution (more SKILLs, fewer MCP_TOOLs) will not break any routing logic.

- `src/orchestrator/pipeline.py` — routes by `classified_type` enum. **OK.**
- `src/orchestrator/generators/` — each generator filters by its own type. **OK.**
- `scripts/test_pipeline.py` — checks `classified_type` value, not reason. **OK.**

### S4: Unit tests (`tests/unit/test_agent_generator.py`)

All `AgentGenerationResult` constructions already have `tools` removed. All mock `node_config` dicts already omit `tools`. **OK.**

### S5: Integration tests (`tests/integration/test_agent_generator.py`)

Line 130-133 asserts `node_config` has `name`, `model`, `description` — all still present. The `tools` assertion was already removed. **OK.**

### S6: Classifier unit tests (`tests/unit/test_classifier.py`)

Test rationales use generic terminology ("deterministic function", "multi-step workflow", "needs planning") that aligns with both old and new classifier definitions. No references to old complexity-axis criteria. **OK.**

### S7: Test scripts (`scripts/`)

- `scripts/test_mint.py` — already fixed (inventory reorder relabeled from MCP_TOOL to SKILL). **OK.**
- `scripts/run_pilot.py` — uses task definitions from `tasks.py`, no hardcoded type expectations. **OK.**
- `scripts/run_longitudinal.py` — type-agnostic, loops over results. **OK.**
- `scripts/analyze_results.py` — aggregates by type but doesn't assume distribution. **OK.**

### S8: Tools and sandbox (`tools/`, `sandbox/`)

- `tools/quick_validate.py` — validates SKILL.md structure only, unrelated to classification. **OK.**
- `tools/init_skill.py` — scaffolds skill directories, unrelated. **OK.**
- `sandbox/Dockerfile` — Docker image definition, unrelated. **OK.**

### S9: Other generator prompts

- `src/orchestrator/prompts/mcp_tool_generator.py` — no references to classification criteria or `tools` field. **OK.**
- `src/orchestrator/prompts/skill_generator.py` — no references to classification criteria. **OK.**
- `src/orchestrator/prompts/decomposition.py` — no references to classification criteria. **OK.**

### S10: Evaluation task definitions (`tests/evaluation/tasks.py`)

All 18 task prompts use language that correctly signals target type under the new classifier:
- MCP_TOOL tasks: "parse", "compute", "return" (single-pass)
- SKILL tasks: "build a pipeline", "pipeline stages" (prescribed workflow)
- AGENT tasks: "adapt your investigation", "adaptive" (autonomous reasoning)

**OK.**

### S11: Registry store `add()` method (`src/registry/store.py`)

Accepts and stores `agent_config` as-is without validation or reconstruction. Will correctly store new configs without `tools`. **OK.**

---

## Summary

| Finding | File | Severity | Action |
|---------|------|----------|--------|
| F1 | `src/registry/store.py:526` | Must fix | Remove stale `"tools"` from seed agent config |
| S1–S11 | Various | Safe | No changes needed |

**Total files audited:** 40+  
**Changes required:** 1 (seed data in registry store)
