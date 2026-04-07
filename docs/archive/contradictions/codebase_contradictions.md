# Codebase Internal Contradictions Audit

**Date:** 2026-04-07  
**Scope:** Full codebase audit of `src/`, `tests/`, `scripts/`  
**Focus:** Internal contradictions from implementation evolution (not style issues)  
**Status:** All 16 contradictions resolved. 814 unit tests pass.

---

## Summary

| # | Severity | Module | Description | Status |
|---|----------|--------|-------------|--------|
| C1 | CRITICAL | cli/runner.py | Hardcoded binary extensions shadow config constant | FIXED |
| C2 | HIGH | generators/skill.py | Skill directory name vs artifact capability_name mismatch | FIXED |
| C3 | HIGH | generators/agent.py | Agent skill directory name vs artifact capability_name mismatch | FIXED |
| C4 | HIGH | optimizer/loop.py | `utility_scores` not explicitly returned by final node | FIXED |
| C5 | HIGH | optimizer/loop.py | Utility scores stale after prune/split/merge operations | FIXED |
| C6 | HIGH | verification/gate.py | Tier 1 documented as "AST + regex for all" but only MCP_TOOL gets AST | FIXED |
| C7 | MEDIUM | optimizer/simulation.py | Hard-coded thresholds in simulation vs parameterized in loop | FIXED |
| C8 | MEDIUM | optimizer/loop.py | `previous_utility_scores` comment misleading | FIXED |
| C9 | MEDIUM | optimizer/operations.py | Split returns `[]` on collision, merge raises `ValueError` — inconsistent | FIXED |
| C10 | MEDIUM | verification/static_scanner.py | `scan()` attempts AST on non-Python artifacts; gate works around it | FIXED |
| C11 | MEDIUM | verification/behavioral.py | Docstring says "four" behavioral tests, code runs five | FIXED |
| C12 | MEDIUM | schemas/task.py | `SubTaskItem` enforces `min_length=1`, `SubTask` does not | FIXED |
| C13 | LOW | verification/gate.py | Tier 2 documented as "All: Behavioral testing" but SKILL/AGENT skip | FIXED |
| C14 | LOW | schemas/evaluation.py | `LONGITUDINAL` baseline condition defined but never used | FIXED |
| C15 | LOW | cli/runner.py | No explicit `_extract_stage_detail()` case for `optimizer_node` | FIXED |
| C16 | LOW | cli/display.py | `STAGE_MAP` unconditionally includes conditional `optimizer_node` | FIXED |

---

## CRITICAL

### C1: Hardcoded Binary Extensions in CLI Runner

**Files:** `src/cli/runner.py`, `src/config.py`

**Issue:** `runner.py` defined a local `_BINARY_FILE_EXTENSIONS = {".xlsx", ".xls", ".parquet", ".pickle", ".pkl"}` (5 extensions) that shadowed the imported `BINARY_EXTENSIONS` from config (30+ extensions). Images, PDFs, videos, and most binary formats were NOT base64-encoded when passed to the sandbox, causing silent data corruption.

**Root cause:** The local constant predated the centralized config addition.

**Fix:** Removed the local constant; now uses the already-imported `_BINARY_EXTENSIONS` from config.

---

## HIGH

### C2: Skill Directory Name vs Artifact Capability Name Mismatch

**Files:** `src/orchestrator/generators/skill.py`

**Issue:** When the LLM didn't produce proper YAML frontmatter, the fallback `skill_name` was derived via simple regex (no hash suffix), but the artifact's `capability_name` always used `forced_name` (with hash). Result: skill directory was `.deepagents/skills/parse-csv-file/` but artifact registered as `parse-csv-file-a1b2c3`.

**Root cause:** `forced_name` (Fix 5 — deterministic naming with hash) was added to artifacts but the directory path still used `skill_name`.

**Fix:** Changed `target_dir` to always use `forced_name` for the directory path. Updated test in `test_skill_generator.py`.

### C3: Agent Skill Directory Name vs Artifact Capability Name Mismatch

**Files:** `src/orchestrator/generators/agent.py`

**Issue:** Same pattern as C2. Agent skills were written to `.deepagents/skills/<result.agent_name>/` but registered with `forced_name`.

**Fix:** Changed `skill_dir` to use `forced_name`. Updated log messages and tests in `test_agent_generator.py`.

### C4: `utility_scores` Not Explicitly Returned by Final Optimizer Node

**Files:** `src/optimizer/loop.py`, `src/orchestrator/pipeline.py`

**Issue:** `apply_operations_node` didn't include `utility_scores` in its return dict. Pipeline relied on implicit LangGraph state merging.

**Fix:** `apply_operations_node` now explicitly returns filtered `utility_scores` (active capabilities only). This also addresses C5.

### C5: Utility Scores Stale After Optimizer Operations

**Files:** `src/optimizer/loop.py`

**Issue:** After prune/split/merge, the `utility_scores` dict still contained entries for deactivated capabilities and was missing entries for newly created ones. This corrupted KL divergence calculations in subsequent cycles.

**Fix:** `apply_operations_node` now filters `utility_scores` against `registry.list_all(is_active=True)` before returning. Stale entries for pruned/deactivated capabilities are removed.

### C6: Tier 1 Documentation Claims "AST + regex for All Types"

**Files:** `src/verification/gate.py`, `src/verification/static_scanner.py`

**Issue:** Module docstring claimed Tier 1 applies "AST + regex" to all artifact types, but only MCP_TOOL got AST analysis.

**Fix:** Updated gate.py docstring to accurately document per-type behavior: MCP_TOOL gets AST + regex, SKILL/AGENT get regex-only. Also updated `static_scanner.scan()` to skip AST for non-MCP_TOOL types (see C10), so gate.py no longer needs the `scan_text_only` workaround — all types now go through `scan()` directly.

---

## MEDIUM

### C7: Hard-Coded Thresholds in Simulation vs Parameterized in Loop

**Files:** `src/optimizer/simulation.py`, `src/optimizer/loop.py`

**Issue:** Simulation hardcoded `0.6`, `0.7`, `0.05` while loop accepted them as parameters.

**Fix:** Added `split_reuse_threshold`, `split_success_threshold`, and `merge_reuse_threshold` parameters to `run_simulation()` with the same defaults as `create_optimization_loop()`.

### C8: `previous_utility_scores` Comment Misleading

**Files:** `src/optimizer/loop.py`

**Issue:** Comment said "stored for reference only" but the field was actively overwritten. Misleading about the field's purpose.

**Fix:** The comment is now accurate: `previous_utility_scores` captures the pre-operation scores, while the new explicit `utility_scores` return (from C4 fix) carries the filtered post-operation scores. The comment was left in place since it correctly distinguishes this field from the pipeline-level persistence mechanism.

### C9: Split Returns `[]` on Collision, Merge Raises `ValueError`

**Files:** `src/optimizer/operations.py`

**Issue:** Identical failure condition (name collision) was handled differently: `split()` returned `[]`, `merge()` raised `ValueError`.

**Fix:** Changed `split()` to raise `ValueError` on name collision, matching `merge()`. Both callers (loop.py and simulation.py) already have try/except around these calls. Updated two tests in `test_optimizer_operations.py` to expect `pytest.raises(ValueError)`.

### C10: `scan()` Attempts AST on Non-Python Artifacts

**Files:** `src/verification/static_scanner.py`, `src/verification/gate.py`

**Issue:** `static_scanner.scan()` attempted `ast.parse()` on SKILL (YAML/markdown) and AGENT (JSON) content, producing spurious SyntaxError findings. Gate.py worked around this by discarding the scan result and calling `scan_text_only()` separately.

**Fix:** `static_scanner.scan()` now guards AST checks with `if capability_type == MCP_TOOL`. SKILL/AGENT go through regex checks only. Gate.py now calls `scan()` for all types (removed `scan_text_only` workaround and its import).

### C11: Docstring Says "Four" Behavioral Tests, Code Runs Five

**Files:** `src/verification/behavioral.py`

**Issue:** `run()` docstring said "four" but code runs five test categories (output variation was added as Fix B2.3).

**Fix:** Updated docstring to say "five" and list all categories. Also clarified that SKILL/AGENT artifacts are skipped.

### C12: `SubTaskItem` Enforces `min_length=1`, `SubTask` Does Not

**Files:** `src/schemas/task.py`

**Issue:** `SubTaskItem.required_capabilities` had `min_length=1` but `SubTask.required_capabilities` had no constraint. After conversion, the validation was lost.

**Fix:** Added `min_length=1` to `SubTask.required_capabilities`.

---

## LOW

### C13: Tier 2 Documented as "All: Behavioral Testing" but SKILL/AGENT Skip

**Files:** `src/verification/gate.py`, `src/verification/behavioral.py`

**Issue:** Gate docstring claimed Tier 2 tests "All" types, but SKILL/AGENT have no `source_code` and are skipped.

**Fix:** Updated gate.py docstring (as part of C6 fix) to show Tier 2 applies to MCP_TOOL only, with SKILL/AGENT explicitly noted as skipped.

### C14: `LONGITUDINAL` Baseline Condition Defined but Never Used

**Files:** `src/schemas/evaluation.py`

**Issue:** Enum value `LONGITUDINAL = "longitudinal"` existed but no code referenced it.

**Fix:** Removed the unused enum value.

### C15: No `_extract_stage_detail()` Case for `optimizer_node`

**Files:** `src/cli/runner.py`

**Issue:** `_extract_stage_detail()` had no explicit case for `optimizer_node`, falling back to empty string.

**Fix:** Added explicit case returning `"prune/split/merge"`.

### C16: `STAGE_MAP` Includes Conditional Node Unconditionally

**Files:** `src/cli/display.py`

**Issue:** `STAGE_MAP` always included `"optimizer_node"` even though the node only exists when `run_optimizer=True`.

**Fix:** Added a comment explaining this is intentional — the map is a superset of all possible nodes, and extra entries are harmless since only nodes that emit stream events get looked up.
