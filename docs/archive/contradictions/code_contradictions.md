# Code Contradictions Audit

**Date:** 2026-04-06  
**Remediated:** 2026-04-07 — all 17 issues fixed, 814 unit tests passing.  
**Scope:** Full codebase scan for internal contradictions — stale comments, docstring/code mismatches, leftover references from previous implementations.

---

## Critical

### C1. Invalid `failure_reason` Value Not in Enum

**Files:** `tests/evaluation/harness.py:598`, `src/schemas/evaluation.py`

The harness sets `failure_reason = "no_source_code"` when a registry-matched capability has no source code in metadata. However, the `FailureReason` enum only defines: `NONE`, `GENERATION_FAILED`, `TIER1`, `TIER2`, `TIER3`, `OUTPUT_REJECTED`. The value `"no_source_code"` is not a valid member. The test at `tests/unit/test_harness.py:275` asserts this invalid value, confirming the mismatch is intentional but wrong. Will cause Pydantic validation errors at runtime when this code path is hit.

### C2. `compute_utility` Parameter Order Swap

**Files:** `src/optimizer/utility.py:12`, `src/optimizer/simulation.py:353`

- `utility.py::compute_utility()` signature: `(capability_id: str, metrics_store: MetricsStore, ...)`
- `simulation.py::_compute_utility()` signature: `(metrics_store: MetricsStore, capability_id: str, ...)`

The two functions have swapped parameter order. Currently safe because they're called correctly in their respective contexts (line 350 uses the real one, line 353 uses the local one), but the inconsistency is a latent bug — any refactor swapping one for the other will fail silently.

---

## High

### H1. Integration Tests Still Check `DEEPSEEK_API_KEY` After Model Unification

**Files:** `tests/integration/test_skill_generator.py:26`, `test_mcp_tool_generator.py:5-7`, `test_decomposition.py:9,25,29`, `test_agent_generator.py`, `test_classifier.py`

CLAUDE.md states all LLM calls were unified to `openai:gpt-5-mini` in March 2026. But all integration test files still gate on `DEEPSEEK_API_KEY` and reference "the real DeepSeek LLM" in docstrings. Either the tests are checking the wrong key (and would be skipped even with a valid OpenAI key), or the migration was never applied to the test suite.

### H2. Gate Docstring vs Code: Tier 3 Rejection Logic

**Files:** `src/verification/gate.py:14-15` (docstring), `gate.py:169-172` (code)

Docstring rule: "Tier 3 CRITICAL finding → reject."  
Code: `any(f.severity in (Severity.CRITICAL, Severity.HIGH) for f in all_findings)` — checks both CRITICAL **and** HIGH across **all** accumulated findings (Tier 1+2+3), not just Tier 3. The docstring omits HIGH and doesn't describe the cross-tier accumulation behavior.

### H3. `search()` `is_active=False` Behaves Like `is_active=True`

**Files:** `src/registry/store.py:210-221,238`

Parameter `is_active: bool | None = True`. Docstring documents `True` (only active) and `None` (all). Code: `if is_active is not None and not descriptor.is_active: continue` — any non-None value (including `False`) filters out inactive capabilities. Passing `is_active=False` does **not** return only inactive capabilities as the parameter name implies.

### H4. CLI `--inactive` (Plan) vs `--active-only` (Code) — Opposite Semantics

**Files:** `docs/plan_cli_upgrade.md:178`, `src/cli/app.py:104-106`, `docs/system_overview.md:465`

Plan proposed `--inactive` flag (include deactivated items). Code implements `--active-only` (exclude deactivated items). Logically opposite. The plan document was never updated to reflect the final implementation.

---

## Medium

### M1. `loading_node.py` Docstring Contradicts Code

**Files:** `src/orchestrator/loading_node.py:1-11`

Docstring: "Skills and agents are already written to disk during generation and do not require loading. This node handles only MCP_TOOL artifacts." Code (lines 95-137): actively registers SKILL and AGENT artifacts in the ChromaDB registry. The node does handle them — just not via dynamic loading.

### M2. `verification_node.py` Stub Gate Comment

**Files:** `src/orchestrator/verification_node.py:20`

Docstring: "Stub gate (Iteration 2 default): `gate_fn=None` uses `_stub_gate`." In reality, `pipeline.py` wires the real `gate_verify` function as the default. The stub is only used when `verification_enabled=False`. The comment reflects the Iteration 2 state, not the current implementation.

### M3. `behavioral.py` Docstring Says "Four" Test Categories, Code Runs Five

**Files:** `src/verification/behavioral.py:6` (docstring), `behavioral.py:486-490` (code)

Docstring lists four test categories (nonexistent entity, consistency, boundary, latency). The `run()` function actually calls five tests — `_test_output_variation()` was added later (for B2.3 bypass fix) and never reflected in the docstring.

### M4. `loop.py` False Defensive Comment

**Files:** `src/optimizer/loop.py:210`

Comment: "defensive: search() does not filter by is_active." But `search()` **does** filter by `is_active` (default `True`). The `and similar_cap.is_active` check on line 210 is redundant. The comment misleads about the registry's behavior.

### M5. "Four" vs "Five" Baseline Conditions in Docstrings

**Files:** `src/registry/evaluation_store.py:4`, `src/schemas/evaluation.py:4`

`evaluation_store.py` says "four baseline conditions." `evaluation.py` says "five baseline conditions." The `BaselineCondition` enum has 5 values (FULL, NO_VERIFICATION, NO_OPTIMIZATION, STATIC, LONGITUDINAL). The evaluation store docstring is stale from before LONGITUDINAL was added.

### M6. `agent.py` Name Override Comment Misleading

**Files:** `src/orchestrator/generators/agent.py:283-290`

Comment says "Override whatever name the LLM chose with the deterministic slug." But the skill file is written to disk at line 271 using `result.agent_name` (the LLM's choice). The override only applies to the artifact's `capability_name` field (metadata), not the actual disk file or SKILL.md frontmatter.

### M7. Enums Defined But Literals Used in `EvaluationRecord`

**Files:** `src/schemas/evaluation.py:16-23,61,65,96,117`

`BaselineCondition`, `TaskTier`, `FailureReason`, `HumanLabel` are defined as StrEnums. But `EvaluationRecord` uses hardcoded `Literal[...]` types for the corresponding fields instead of referencing the enums. If values change, they must be updated in two places.

---

## Low

### L1. Stale Verification Tier Reference in `skill.py`

**Files:** `src/orchestrator/generators/skill.py:163-170`

Comment references "Tier 2 behavioral testing (Iteration 3)" when describing the `validate_skill` call, which is actually Tier 0 structural validation (`quick_validate.py`).

### L2. StrEnum vs String Literal Comparison Inconsistency

**Files:** `src/orchestrator/generators/utils.py:51` vs `src/orchestrator/verification_node.py:138`

`utils.py` compares `finding.severity in ("critical", "high")` using string literals. `verification_node.py` uses `Severity.HIGH` and `Severity.CRITICAL` enum values. Both work (StrEnum supports string comparison), but the inconsistent style suggests the utils.py code predates the enum adoption.

### L3. `_metrics_store` vs `metrics_store` Variable Naming

**Files:** `src/cli/runner.py:1258,1276`

Variable created as `_metrics_store` (underscore prefix = unused convention) but referenced as `metrics_store` (no underscore) in a lambda on line 1276. Suggests copy-paste or incomplete rename.

### L4. Misleading Test Comment About T04

**Files:** `tests/unit/test_harness.py:309`

Comment: `# T04 has no static coverage → immediate failure`. But the test runs under the `"full"` condition (default), not `"static"`, so the static coverage property is irrelevant to why the test fails.

### L5. Missing T14-T17 Validator Tests

**Files:** `tests/unit/test_evaluation_tasks.py`

Tasks T14-T17 are defined in `tests/evaluation/tasks.py` and included in `ALL_TASKS`, but `test_evaluation_tasks.py` only imports and tests T01-T13 and T18. No `TestT14Validator` through `TestT17Validator` classes exist.

### L6. Stale TBD Note in Plan Doc

**Files:** `docs/plan_cli_upgrade.md:283`

Contains: "This allows (command name TBD — `jit` is a placeholder)". The name was finalized as `mint` and is implemented. Planning language should be cleaned up.
