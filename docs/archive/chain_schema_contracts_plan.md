# Chain Schema Contracts — Implementation Plan

**Date:** 2026-04-12
**Problem:** Generated artifacts in a multi-step chain don't share a common vocabulary for output/input keys. Step N outputs `{"items_below_threshold": [...]}` but step N+1 expects `{"candidates": [...]}`. The executor passes whatever dict the LLM produced, and the downstream step either silently ignores the mismatched key or produces zero/empty output.
**Root cause:** Each artifact is generated in isolation. The generator sees only its step description and the full task context — it never sees the output key names that upstream or downstream steps will use.

**Proposal mandate:** Thesis proposal Section 3.4 ("Structured State Communication") explicitly requires *"strictly typed JSON schemas representing the 'world state' at each handoff point"* with *"a protocol-buffer-style contract that each agent can parse deterministically."* This is implemented at the orchestration level (Pydantic models in `src/schemas/`) but **missing at the generated-capability chain level**.

---

## Observed failure (T04, 2026-04-12)

```
Step 1 (MCP_TOOL): parse CSV + YAML → {"inventory": [...], "reorder_rules": {...}}
Step 2 (MCP_TOOL): compute days_of_stock → {"inventory": [...]}  (with days_of_stock added)
Step 3 (SKILL):    identify below threshold → {"items_below_threshold": [...], "items_below_threshold_count": 7}
Step 4 (SKILL):    compute reorder qtys → {"items": [...]}
Step 5 (MCP_TOOL): aggregate costs → {"product_count": 0, "items_below_threshold": 0, "total_reorder_cost": 0.0}
```

Step 5's aggregator received `{"items": [...]}` from step 4 but likely expected a key like `"reorder_records"` or `"products_to_reorder"`. It found no matching key and returned zeros.

## Solution: Decomposer-defined output schemas per step

### Design

Add an `output_schema` field to each subtask in the decomposition output. This is a lightweight dict mapping key names to type descriptions — not a full JSON Schema, just enough for generators to align on key names.

```json
{
  "subtasks": [
    {
      "description": "Parse the inventory CSV and reorder rules YAML...",
      "required_capabilities": ["parse-inventory-and-rules"],
      "depends_on": [],
      "output_schema": {
        "inventory": "list[dict] — each dict has product_id, product_name, current_stock, daily_usage, unit_cost, warehouse",
        "reorder_rules": "dict — keys: min_days_of_stock (int), reorder_days_supply (int), max_order_value (float)"
      }
    },
    {
      "description": "Compute days_of_stock for each product...",
      "required_capabilities": ["compute-days-of-stock"],
      "depends_on": [0],
      "output_schema": {
        "inventory": "list[dict] — same as upstream, with days_of_stock (float) added to each record"
      }
    }
  ]
}
```

Each generator then receives:
1. Its step's `output_schema` — "you MUST return a dict with exactly these keys"
2. Its upstream step(s)' `output_schema` — "you will receive a dict with these keys from the previous step"

### Relevant literature

- **ToolGate** (arXiv 2601.04688, Jan 2026): Formalizes tools as Hoare-style contracts with preconditions and postconditions. Each tool invocation is gated by a precondition (input state satisfies requirements) and committed by a postcondition (output updates state). Our `output_schema` is the postcondition.
- **Schema First Tool APIs** (arXiv 2603.13404, Mar 2026): Finds that JSON Schema specifications reduce interface misuse in LLM tool calling vs. free-form documentation. Directly supports our approach of giving generators explicit schemas.
- **Thesis proposal Section 3.4**: "strictly typed JSON schemas representing the world state at each handoff point" — we are implementing this at the chain execution layer.

---

## Changes required

### Phase 1: Schema in decomposition output (~1h)

#### 1a. Schema: extend `SubTaskItem` and `SubTask`

**File:** `src/schemas/task.py`

Add `output_schema` field to both `SubTaskItem` (LLM response) and `SubTask` (runtime):

```python
class SubTaskItem(BaseModel):
    description: str
    required_capabilities: list[str] = Field(min_length=1)
    depends_on: list[int] = Field(default_factory=list)
    output_schema: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Keys the step MUST include in its output dict, "
            "mapped to type descriptions. Downstream steps "
            "use these exact key names as their input contract."
        ),
    )
```

Same for `SubTask`. Default `{}` means backward-compatible — old decompositions without schemas still work.

#### 1b. Decomposer prompt: instruct LLM to produce output_schema

**File:** `src/orchestrator/prompts/decomposition.py`

Add a new rule after the existing rules:

```
## Output Schema Contract

For EVERY subtask, define `output_schema`: a dict mapping output key names to
brief type descriptions. These key names become the binding contract between
steps — downstream steps will reference these exact names.

Guidelines:
- Use descriptive, unambiguous key names (e.g., "products_below_threshold"
  not "items" or "result")
- Include the type and a brief description of what the value contains
- If a step passes through data from an upstream step, reuse the SAME key name
- The last step's output_schema must match the keys requested in the task prompt
```

Update the examples to include `output_schema` in each subtask.

#### 1c. Decomposition node: propagate output_schema

**File:** `src/orchestrator/decomposition.py`

The `SubTaskItem → SubTask` conversion (line 139–145) already copies all fields. Since both models will have `output_schema`, this is automatic. No code change needed here — just verify.

### Phase 2: Thread schemas into generator prompts (~1h)

#### 2a. Generator context assembly

**Files:**
- `src/orchestrator/generators/mcp_tool.py`
- `src/orchestrator/generators/skill.py`
- `src/orchestrator/generators/agent.py`

Currently, each generator receives `task_context` (full task description) and `gap_description` (step description). We need to add:
- `output_schema`: this step's required output keys
- `input_schema`: the upstream step(s)' output keys (what this step receives)

The generator node has access to `state.subtasks` and `state.classified_gaps`. We need to look up the subtask for the current gap, find its `output_schema`, and find the `output_schema` of each step in its `depends_on`.

**Approach:** Add a helper function `_build_schema_context(gap, subtasks)` that returns `(input_schema_str, output_schema_str)` for use in the prompt.

#### 2b. MCP tool generator prompt update

**File:** `src/orchestrator/prompts/mcp_tool_generator.py`

Add after the Chain Data Flow Contract section:

```
## Input/Output Schema Contract

{schema_context}

Your tool MUST return a dict with EXACTLY these keys: {output_keys}.
If you receive data from an upstream step, it will have these keys: {input_keys}.
Use the upstream key names directly — do NOT rename them.
```

Add `schema_context` as a new format variable in `MCP_TOOL_USER_PROMPT`.

#### 2c. Skill generator prompt update

**File:** `src/orchestrator/prompts/skill_generator.py`

Same pattern — add schema context to the prompt.

#### 2d. Agent generator prompt update

**File:** `src/orchestrator/prompts/agent_generator.py`

Same pattern for standalone agent generation. For lead-with-subagents, the lead's prompt should include the full schema chain.

### Phase 3: Executor-level schema validation (optional, low priority)

#### 3a. Post-step output key check

**File:** `src/orchestrator/composition.py`

After each step executes, check whether the output dict contains the expected keys from `output_schema`. If keys are missing, log a warning with the expected vs actual keys. This is diagnostic only — don't fail the chain, since the LLM may use equivalent-but-different key names.

```python
def _check_output_schema(self, step_idx: int, output: dict, subtask: SubTask) -> None:
    expected = set(subtask.output_schema.keys())
    if not expected:
        return  # No schema defined
    actual = set(output.keys())
    missing = expected - actual
    if missing:
        logger.warning(
            "Step %d output missing expected keys: %s (got: %s)",
            step_idx, missing, actual,
        )
```

#### 3b. Key remapping fallback (future work)

If a downstream step's expected input keys don't match the upstream step's actual output keys, attempt fuzzy key matching (e.g., `"items_below_threshold"` ≈ `"products_below_threshold"`). This is a safety net for LLM non-compliance.

**Decision:** Defer to post-eval. The schema contract should handle 90%+ of cases. Key remapping is a Ch 7 future work item.

---

## Files modified

| File | Change | Effort |
|---|---|---|
| `src/schemas/task.py` | Add `output_schema` to `SubTaskItem` and `SubTask` | 5 min |
| `src/orchestrator/prompts/decomposition.py` | Add output_schema rule + update examples | 20 min |
| `src/orchestrator/generators/mcp_tool.py` | Build schema context, pass to prompt | 15 min |
| `src/orchestrator/generators/skill.py` | Build schema context, pass to prompt | 15 min |
| `src/orchestrator/generators/agent.py` | Build schema context, pass to prompt | 15 min |
| `src/orchestrator/prompts/mcp_tool_generator.py` | Add schema contract section to prompt | 10 min |
| `src/orchestrator/prompts/skill_generator.py` | Add schema contract section to prompt | 10 min |
| `src/orchestrator/prompts/agent_generator.py` | Add schema contract section to prompt | 10 min |
| `src/orchestrator/composition.py` | Optional: post-step schema check (diagnostic) | 10 min |
| `tests/unit/test_decomposition.py` | Update tests for new field | 10 min |
| `tests/unit/test_composition.py` | Add schema check tests | 10 min |

**Total estimated effort: ~2h**

---

## Risks

1. **Decomposer compliance:** The LLM may not produce `output_schema` consistently. Mitigation: default `{}` is safe (generators work as before). The few-shot examples will strongly guide compliance.

2. **Schema quality:** The decomposer may produce vague schemas (`"result": "dict"`) that don't help. Mitigation: prompt instructs descriptive key names with type annotations. The contract is better than nothing even when imperfect.

3. **Generator compliance:** Even with explicit schemas, the LLM may still use different key names. Mitigation: Phase 3a diagnostic logging tells us how often this happens. If frequent, Phase 3b (key remapping) becomes urgent.

4. **Prompt length:** Adding schema context to generator prompts adds ~200-400 tokens per step. This is within budget (total generator prompt is ~2000 tokens).

---

## Success criteria

- T04 (inventory reorder) produces correct output with matching key names across all 5 steps
- No regression on T01 (single-step, no schema needed)
- Unit tests pass for new `output_schema` field
- Chain executor diagnostic log shows 0 missing-key warnings on successful runs

---

## Relationship to proposal

This implementation directly fulfills thesis proposal Section 3.4:

> *"Agents within the pipeline communicate via strictly typed JSON schemas representing the 'world state' at each handoff point."*

The `output_schema` field is the "typed JSON schema" at each handoff point. The decomposer defines the contract, the generators implement it, and the executor enforces it (diagnostically in Phase 3a, structurally in future Phase 3b).

For `docs/proposal_vs_implementation_v2.md`, this changes the "Chain Data Flow Contract" row from 🟡 Refined to ✅ Faithful — we now have typed schemas at the chain execution layer, not just at the orchestration layer.

## References

- [ToolGate: Contract-Grounded and Verified Tool Execution for LLMs](https://arxiv.org/abs/2601.04688) — Hoare-style pre/postconditions for tool contracts
- [Schema First Tool APIs for LLM Agents](https://arxiv.org/abs/2603.13404) — JSON Schema reduces interface misuse
- Thesis proposal Section 3.4 — "Structured State Communication Protocol"
