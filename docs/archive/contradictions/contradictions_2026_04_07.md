# Codebase Contradictions Audit — 2026-04-07

Full codebase scan for internal contradictions: stale comments, docstring/implementation mismatches, dead code, logic bugs, and documentation drift.

**Scope:** All files under `src/`, `scripts/`, `tests/`, `docs/`, `tools/`, config files.
**Method:** Parallel agent scan of every module, cross-referenced against schemas, callers, and CLAUDE.md.
**Status:** ALL 25 RESOLVED — 814 unit tests pass.

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 1     |
| HIGH     | 4     |
| MEDIUM   | 11    |
| LOW      | 9     |
| **Total**| **25**|

---

## CRITICAL

### C1. `run_task.py`: Undefined variable `run_optimizer` used before definition
- **File:** `scripts/run_task.py` lines 587 vs 678-683
- **Issue:** Line 587 references `run_optimizer` in a try-except block, but the variable is first defined at lines 678-683. If the LangFuse exception handler fires before reaching the definition (e.g., during `--dry-run` early exit), a `NameError` will crash the script.
- **Root cause:** Code reorganization moved variable definitions without updating the error handler.
- **Fix:** Move `run_optimizer` definition to immediately after registry/loader setup (after line 554), before the LangFuse tracing block.

---

## HIGH

### H1. `loading_node.py`: Docstring says skills/agents are "skipped" but they are registered
- **File:** `src/orchestrator/loading_node.py` lines 40-44 vs lines 104-143
- **Issue:** The function docstring states "Skills and agents are skipped — they are written to disk during generation and the DeepAgents runtime picks them up from `.deepagents/skills/` automatically." But lines 104-143 show skills and agents ARE registered in the ChromaDB capability registry.
- **Root cause:** Docstring was written for an earlier iteration where skills/agents weren't registered. The code was updated but the docstring was not.
- **Fix:** Update docstring to reflect that all three types are registered in the registry.

### H2. `gate.py`: Docstring says Tier 2 is skipped for SKILL/AGENT but code runs it unconditionally
- **File:** `src/verification/gate.py` lines 8-9 vs line 144
- **Issue:** Docstring states "SKILL/AGENT: skipped (no executable source_code)" for Tier 2, but the implementation calls `behavioral.run(artifact)` for all capability types without a type check. The skip logic is hidden inside `behavioral.run()` which returns an INFO finding for non-MCP types.
- **Root cause:** The gate was refactored to delegate type-checking to `behavioral.run()` but the docstring still describes the old explicit-skip design.
- **Fix:** Either update docstring to say "Tier 2 delegates to behavioral.run which returns INFO for non-MCP types" or add an explicit type check in gate.py.

### H3. `loop.py`: `previous_utility_scores` captures unfiltered scores, breaking KL divergence
- **File:** `src/optimizer/loop.py` line 289
- **Issue:** `apply_operations_node` sets `"previous_utility_scores": state.utility_scores` (unfiltered) but lines 276-278 filter scores to remove deactivated capabilities. The comment at lines 286-287 states intent to "carry accurate scores for the next cycle's KL divergence calculation" but captures the wrong value.
- **Root cause:** The filtered scores variable (`filtered_scores`, line 288) should be used instead of the raw `state.utility_scores`.
- **Fix:** Change line 289 to `"previous_utility_scores": filtered_scores`.

### H4. `behavioral.py`: `_test_boundary` silently drops non-timeout failures
- **File:** `src/verification/behavioral.py` lines 398-408
- **Issue:** When `result.get("success")` is False, the function only reports a finding if stderr contains "Timeout", "timed out", or "killed". Other sandbox failures (crashes, segfaults, exit code -1 without timeout keywords) are silently ignored — no finding is returned.
- **Root cause:** The failure handler was written to catch timeouts specifically but never extended to cover other failure modes.
- **Fix:** Add an else-branch that reports non-timeout sandbox failures as a MEDIUM finding.

---

## MEDIUM

### M1. `pipeline.py`: Comment says "register MCP tools" but node registers all three types
- **File:** `src/orchestrator/pipeline.py` line 13
- **Issue:** Topology comment says loading_node performs "Pure Python: load + smoke-test + register MCP tools" but the node also registers SKILL and AGENT artifacts.
- **Fix:** Update comment to "load + smoke-test + register capabilities (all types)".

### M2. `static_scanner.py`: `scan_text_only()` is dead code
- **File:** `src/verification/static_scanner.py` lines 366-384
- **Issue:** The public function `scan_text_only()` is defined and documented in CLAUDE.md stable interfaces but is never called anywhere in the codebase. The gate uses `scan()` for all artifact types.
- **Root cause:** Was likely planned for SKILL/AGENT text-only scanning but the gate delegates type handling to `scan()` directly.
- **Fix:** Remove `scan_text_only()` or wire it into the gate for SKILL/AGENT types.

### M3. `static_scanner.py`: Inconsistent capability_type comparison pattern
- **File:** `src/verification/static_scanner.py` lines 311 vs 326
- **Issue:** Line 311 checks `capability_type == CapabilityType.SKILL` (enum only), but line 326 checks `capability_type == CapabilityType.MCP_TOOL or capability_type == "mcp_tool"` (both enum and string). Since `CapabilityType` is a `StrEnum`, both work, but the pattern is inconsistent.
- **Root cause:** The string fallback was added for MCP_TOOL at some point but not for SKILL.
- **Fix:** Use only the enum comparison for both (StrEnum handles string equality).

### M4. `behavioral.py`: `_test_nonexistent_entity` has a missing case
- **File:** `src/verification/behavioral.py` lines 303-318
- **Issue:** If `parsed is None` AND `result.get("success") is True`, no finding is reported. This means the sandbox returned success (exit code 0) but produced unparseable output — a suspicious condition that goes undetected.
- **Fix:** Add a finding for the case where sandbox succeeds but output is unparseable.

### M5. `simulation.py`: Invalid Pydantic v2 syntax for `extra="forbid"`
- **File:** `src/optimizer/simulation.py` lines 31, 49
- **Issue:** Uses `class SimConfig(BaseModel, extra="forbid"):` which is Pydantic v1 syntax. The rest of the codebase uses Pydantic v2 `model_config = {"extra": "forbid"}` (e.g., `OptimizerState` in `loop.py` line 61).
- **Root cause:** These classes were written before the v2 migration and not updated.
- **Fix:** Replace with `model_config = {"extra": "forbid"}` attribute.

### M6. `evaluation_store.py`: Missing enum conversion when loading from DuckDB
- **File:** `src/registry/evaluation_store.py` lines 237, 247
- **Issue:** `_row_to_record` passes raw strings from DuckDB directly to `EvaluationRecord` constructor for `condition` (expects `BaselineCondition`), `failure_reason` (expects `FailureReason`), and `task_tier` (expects `TaskTier`). Pydantic auto-coerces StrEnum, but if a DB value doesn't match, the error message will be confusing.
- **Fix:** Explicitly convert: `condition=BaselineCondition(condition)`, etc.

### M7. `simulation.py`: Missing explicit `invoked_at` timestamps in simulation loop
- **File:** `src/optimizer/simulation.py` lines 265-271
- **Issue:** `InvocationRecord` objects in the per-cycle task loop use the auto-generated `now(UTC)` timestamp, while `generate_synthetic_registry` (line 166) uses deterministic timestamps (`base_time + timedelta`). This inconsistency means simulation results are not fully reproducible.
- **Fix:** Add explicit `invoked_at` parameter derived from cycle number.

### M8. `test_pipeline.py`: Incorrect task reference in docstring
- **File:** `scripts/test_pipeline.py` line 8
- **Issue:** Module docstring says "MCP_TOOL: T04 (pipe-delimited CSV parsing)" but the test actually exercises T05 (dirty CSV cleaning) with fixture `t05_dirty.csv`.
- **Fix:** Change to "MCP_TOOL: T05 (dirty CSV cleaning)".

### M9. `run_pilot.py`: Docstring says T10 but code uses T05
- **File:** `scripts/run_pilot.py` lines 3, 13, 57 vs `tests/evaluation/tasks.py` line 932
- **Issue:** Docstring and help text state pilot runs "T01, T07, T10" but `PILOT_TASKS` is defined as `[T01, T07, T05]`. Code uses `PILOT_TASKS` directly so it actually runs T05.
- **Root cause:** PILOT_TASKS was changed from T10 to T05 but the docstrings weren't updated.
- **Fix:** Update all docstrings in run_pilot.py to reference T05.

### M10. `loading_node.py`: COMPLETED status docstring is misleading
- **File:** `src/orchestrator/loading_node.py` lines 47-49
- **Issue:** Docstring says COMPLETED is set when "an MCP tool was registered OR a skill/agent artifact was written to disk during generation." The actual condition (line 151) is `if state.gaps and deployed == 0: FAILED else: COMPLETED` — it checks the `deployed` counter (registry registration), not disk writes.
- **Fix:** Update docstring to match actual condition.

### M11. `docs/plan_cli_upgrade.md`: Status says PLANNED but CLI is fully implemented
- **File:** `docs/plan_cli_upgrade.md` lines 1-9
- **Issue:** Document header states `Status: PLANNED` and `Date: 2026-03-30`, but the MINT CLI has been fully built with all 11 subcommands. Additionally, sections describe `mint export`, `mint import`, `--force-regenerate` and `mint retry --improve` which were never implemented.
- **Fix:** Archive this document or mark as COMPLETED with notes on which features were descoped.

---

## LOW

### L1. CLAUDE.md says "All 18 types" but 23 schema classes exist
- **File:** `CLAUDE.md` line 338
- **Issue:** The stable interfaces section lists "All 18 types" in `src/schemas/` but evaluation.py adds 5 more: `BaselineCondition`, `EvaluationRecord`, `FailureReason`, `HumanLabel`, `TaskTier`. The "18" count is stale.
- **Fix:** Update to "All 23 types" or clarify "18 core types + 5 evaluation types".

### L2. `EvaluationRecord.capability_type` uses `Literal` instead of `CapabilityType` enum
- **File:** `src/schemas/evaluation.py` line 73
- **Issue:** Uses `Literal["mcp_tool", "skill", "agent"]` while the rest of the codebase uses the `CapabilityType` StrEnum. Works at runtime due to StrEnum compatibility but violates type consistency.
- **Fix:** Change to `CapabilityType | None`.

### L3. Evaluation schema classes not exported from `src/schemas/__init__.py`
- **File:** `src/schemas/__init__.py`
- **Issue:** Does not export `EvaluationRecord`, `BaselineCondition`, `TaskTier`, `FailureReason`, `HumanLabel`. All callers import directly from `src.schemas.evaluation`.
- **Fix:** Add exports for consistency, or document the intentional split.

### L4. `TaskStatus.EXECUTING` is never used in the pipeline
- **File:** `src/schemas/task.py` line 19
- **Issue:** The enum value exists but is never set by any orchestrator node. Only appears in unit tests.
- **Fix:** Remove if truly unused, or document as reserved for future use.

### L5. Verification findings: inconsistent `location` field usage across tiers
- **File:** `src/verification/static_scanner.py` vs `behavioral.py` vs `adversarial.py`
- **Issue:** Tier 1 (static_scanner) consistently sets the `location` field (line numbers, positions). Tiers 2 and 3 never set it, always leaving `None`.
- **Fix:** Add location info to Tier 2/3 findings where applicable (e.g., test category name, attack pattern).

### L6. `gate.py`: Error handling always assigns `tier=1` regardless of actual tier
- **File:** `src/verification/gate.py` lines 104-113
- **Issue:** When `_verify_inner` catches an unexpected exception, it creates a finding with `tier=1` and `tier_reached=1` even if the error occurred in Tier 2 or 3.
- **Fix:** Track the current tier in `_verify_inner` and use it in the error finding.

### L7. `utility.py`: Docstring says "capability_id" but returns `cap.name`
- **File:** `src/optimizer/utility.py` lines 44-47
- **Issue:** `compute_all_utilities` docstring says "Mapping of capability_id to utility score" but return dict keys are `cap.name`. Semantically equivalent but inconsistent terminology.
- **Fix:** Update docstring to "Mapping of capability name to utility score."

### L8. `store.py`: `list_all` default `is_active=True` not documented in docstring
- **File:** `src/registry/store.py` lines 165-178
- **Issue:** Docstring says "is_active: Filter by active status. None to include all." but doesn't mention the default is `True` (active only). Users may expect all capabilities by default.
- **Fix:** Update docstring to "is_active: Filter by active status (default True = active only). Pass None to include all."

### L9. `static_scanner.py`: `_MOCK_NAMES` regex doesn't include "sample" despite docs discussing it
- **File:** `src/verification/static_scanner.py` lines 27-30 vs `docs/iteration_plan.md` lines 463-490
- **Issue:** The devil's advocate findings discuss `sample` as a mock name indicator, but the actual regex is `r"\b(mock|fake|dummy|placeholder|DEMO|TEST_DATA)\b"` — `sample` was intentionally excluded to avoid `random.sample()` false positives but this isn't documented in the code.
- **Fix:** Add a comment explaining why `sample` is excluded.
