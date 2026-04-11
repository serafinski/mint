# Pre-Eval Readiness Assessment

**Date:** 2026-04-11 (post-Phase 3.5c)
**Purpose:** Checklist of what needs to be resolved before committing to the 15h 360-record eval run.

---

## Open Issues

### 1. SKILL coverage is weak

**Problem:** No task in the testbed is confidently pure-SKILL. The classifier produces SKILL artifacts only as part of mixed chains (e.g., T04 produces 2 MCP_TOOL + 1 SKILL). The decomposer tends to split tasks into steps that individually look like MCP_TOOL, stripping the domain/packaging signal that triggers SKILL classification.

**Impact on eval:** Pillar 1 claims the system generates three distinct artifact types. If SKILL is barely represented in the 360-record eval, Chapter 6 has weak evidence for the SKILL type's value. The comparison between execution backends (MCP_TOOL: 0 LLM calls, SKILL: max 5, AGENT: unbounded) loses one leg.

**Potential fixes:**
- Strengthen SKILL-signal in existing task prompts (T04, T05, T12, T14, T15) — emphasize the rules/policy/config as domain expertise
- Add 1-2 new tasks that are unambiguously SKILL-shaped (e.g., "apply this style guide to rewrite these paragraphs", "classify these applications against this eligibility policy")
- Both are testbed changes, not system changes

**Decision needed:** strengthen existing prompts, add new tasks, or document as limitation?

---

### 2. Output format is JSON-only

**Problem:** The entire pipeline assumes output is a Python `dict` returned as JSON. Validators check dict keys. Chain executor passes `step_N_output.json`. CLI displays JSON. A user asking `mint run "convert this CSV to Excel"` would get a tool that can't return a file.

**Impact on eval:** The testbed only validates structured dict outputs. Real business analytics users want CSV, Excel, charts, PDF reports. The system can't produce any of these.

**Impact on thesis:** The proposal doesn't discuss output formats. This is a real-world usability gap, not a proposal deviation. But reviewers may ask "can the system produce anything other than JSON dicts?"

**Potential fixes:**
- Add file-output support to the chain executor (tool writes to `/workspace/output.*`, CLI copies to user's directory)
- Add 1-2 testbed tasks with file-based expected outputs
- Or document as Chapter 7 future work

**Decision needed:** implement file output support, or document as limitation?

---

### 3. Decomposer non-determinism

**Problem:** The decomposer sometimes ignores rules 10 and 11 (investigation collapse, simple-task collapse). T09 decomposed into 4 subtasks despite rule 11 saying it should be 1. T16 sometimes produces 1 subtask, sometimes 4-5. The same prompt, same model, different runs produce different decompositions.

**Impact on eval:** With 5 runs per task, some runs will over-decompose and fail (monolith tools, over-parameterized tools), while others will collapse correctly and pass. The success rate will be noisy — potentially 40-80% per task depending on decomposition luck.

**Impact on thesis:** This is an honest LLM limitation, not a bug. The 5 runs average it out. But if a task passes 2/5 times, is that a "working" capability or a "broken" one? Chapter 6 needs a clear framework for interpreting non-deterministic results.

**Potential fixes:** None that don't involve hardcoded heuristics (which we decided against). Document as a finding.

**Decision needed:** accept and document, or invest more in structural validation?

---

### 4. Binary file handling (T11) — ✅ FIXED

**Problem (was):** MCP_TOOL steps crash on `.xlsx` files in the workspace with `UnicodeDecodeError`. T11 failed on every test_mint run.

**Root cause found:** NOT an LLM compliance issue. The sandbox code wrapper (`execute_mcp_tool_in_chain` in `execution.py`) blindly added ALL fixture file paths to `_fixture_paths` for every step, then the fuzzy path-matching assigned binary fixtures to unrelated tool parameters. The generated tool code was fine — our wrapper fed it the wrong file.

**Fix applied (2026-04-11):**
- Step 0: all fixtures in `_all_inputs` (generic) AND `_fixture_paths` (path matching) — broad access for the parser step
- Steps 1+: fixtures in `_fixture_paths` only, with **name-aware matching** — a param `xlsx_path` matches `products.xlsx` (word overlap), but `csv_path` does NOT match `products.xlsx` (no overlap). Prevents binary fixtures from being blindly assigned to unrelated params.
- Later steps that legitimately need a fixture can still access it via a parameter whose name overlaps with the fixture filename.

**Status:** code fix applied, 989 unit tests pass. Needs test_mint validation.

---

### 5. AGENT coverage depth

**Problem:** Of the 5 AGENT-targeted tasks, only 2 (T16, T17) genuinely require adaptive reasoning. 2 (T08, T13) are borderline — linear steps with thin "adaptive" framing. 1 (T10) could be handled by SKILL.

**Impact on eval:** The classifier may correctly route T10 as MCP_TOOL, which reduces AGENT coverage to 4 tasks. The eval would show AGENT success rates based on 2 genuine + 2 borderline tasks.

**Impact on thesis:** Reviewers may question whether the AGENT type adds value if most "AGENT" tasks are linear pipelines.

**Potential fixes:**
- Strengthen T10 prompt to require genuine adaptive method selection
- Strengthen T08/T13 to require iterative hypothesis testing
- Small prompt edits, no fixture changes

**Decision needed:** strengthen AGENT tasks, or document as limitation?

---

### 6. Pilot run results (2026-04-11 20:49–21:21)

**Run:** T01, T04, T14, T16 with softened C1/C6 prompts. **2/4 passed.**

| Task | Result | Details |
|---|---|---|
| T01 | ✅ | 1 subtask (C2 worked), correct output |
| T04 | ❌ `chain_step_failed` | 5 subtasks. Steps 3+4 (SKILLs) were monoliths — both returned the full answer. Step 5 (aggregator) failed on empty upstream JSON. Anti-monolith SKILL execution prompt didn't prevent this. |
| T14 | ❌ `chain_step_failed` | 5 subtasks. Step 2 over-parameterized (`missing 1 required positional argument: 'transactions_tsv'`). C6 softening (removed column-name hardcoding) made this worse. |
| T16 | ✅ | 4 AGENT gaps → lead + 4 subagents. Correct answer (`db_connections`). Softened rule 10 did NOT collapse to 1 subtask (4 gaps), but subagents finished within 480s timeout. |

**Key findings:**
- **C2 (simple-task collapse):** works for T01 ✅
- **C1 (rule 10 softened):** weaker — T16 got 4 gaps instead of 1, but still passed via subagents
- **C6 (softened):** T14 regressed — over-parameterized tools returned without column-name hardcoding instruction
- **SKILL monolith:** T04 steps 3+4 computed full answer despite anti-monolith SKILL execution prompt
- **Note:** this pilot ran BEFORE the binary file wrapper fix (fixture paths no longer blindly assigned to downstream steps)

**Assessment:** T01 and T16 are stable passes. T04 and T14 are non-deterministic — pass rates depend on LLM decomposition and generation quality. The 360-record eval with 5 runs per task will capture this variance.

---

### 7. Network access for generated tools

**Problem:** The Docker sandbox runs with `--network none` — no generated tool can make network requests. The proposal implies network-capable tools are expected: Tier 1 checks for "missing external calls" (functions that claim to fetch but don't import network libraries), and Tier 3 tests "whether the tool behaves differently when network connectivity is simulated as unavailable." Both checks are meaningless if no tool can ever access the network.

**Impact on eval:** None — all 18 testbed tasks are file-based. No task requires network access. The testbed should NOT be changed (network APIs are non-deterministic, require credentials, can't be reproduced across 5 runs).

**Impact on thesis:** Reviewers may ask "can the system generate tools that call APIs?" Without the flag, the answer is "no, by design." With the flag, the answer is "yes, opt-in via `--allow-network`, but not evaluated in this testbed."

**Potential fix:**
- Add `--allow-network` / `allow_network=True` parameter to `sandbox_execute()` that skips the `--network none` Docker flag
- Plumb through the CLI as `mint run --allow-network "fetch exchange rates from ..."`
- Default remains `--network none` (safe)
- ~30 min of code, no testbed changes
- Document in Ch 4 (security constraint), Ch 7 (API-tool evaluation as future work)

**Decision needed:** implement the flag before eval (demonstrates capability exists) or defer entirely?

---

### 8. Tier 1 severity mismatches

**Problem:** Two Tier 1 check severities don't match the proposal:
- `missing_external_calls`: MEDIUM in code, proposal says High
- `random_seed` / `lorem_ipsum`: HIGH in code, proposal says Medium

**Assessment:**
- `missing_external_calls` at MEDIUM is **correct when `--network none`** (default) — no tool can make network requests regardless, so the check is informational. However, with the `--allow-network` flag (item 7), a tool claiming to fetch but missing network imports is a real red flag — it could be faking data. The severity should be **dynamic**: MEDIUM when network is disabled (default), HIGH when network is enabled (per proposal intent).
- Synthetic data at HIGH is **stricter than proposal, deliberately kept** — LLM generators reach for `random.seed` and `faker` more frequently than the proposal anticipated. Escalating from Medium to HIGH is a strengthening, not a deviation. Document in Ch 4: *"Synthetic data indicators are flagged as HIGH (the proposal specified Medium) based on empirical observation that LLM generators frequently use fixed random seeds and faker libraries when fabricating data."*

**Fix:** Make `missing_external_calls` severity context-dependent (one `if` in `static_scanner.py`). Synthetic data stays HIGH — no code change, document as deliberate strengthening. Implement `missing_external_calls` severity change alongside item 7 (`--allow-network` flag).

---

---

## Pre-Eval Action Plan

These items should be completed before the 15h 360-record eval.

### A. Testbed redesign (~2-3h total)

**Target layout: 20 tasks with balanced spread:**
- 5 seeded (registry match): 2 MCP_TOOL, 2 SKILL, 1 AGENT
- 5 JIT MCP_TOOL
- 5 JIT SKILL
- 5 JIT AGENT
- At least 5 tasks with non-JSON output formats (CSV, Excel, JSON array, Markdown)

**A1 — Rebalance seeded capabilities:**
- [ ] Keep T01 (csv-parser, MCP_TOOL) and T02 (json-validator, MCP_TOOL) as seeded MCP_TOOL
- [ ] Keep T05 (data-cleaning-workflow, SKILL) and T09 (report-generation, SKILL) as seeded SKILL
- [ ] Demote T03 (text-statistics) from seeded to JIT MCP_TOOL — remove from `has_static_coverage`
- [ ] Add 1 new AGENT seeded task — write a task that matches the existing `data-analysis-agent` seed (already in registry). Requires new fixture + expected values + validator.

**A2 — Strengthen SKILL coverage (5 JIT SKILL tasks):**
- [ ] Keep T04 (inventory reorder) — strengthen prompt for SKILL packaging signal
- [ ] Keep T12 (config-driven quality) — rewrite prompt to emphasize rules JSON as domain policy
- [ ] Reassign T14 (budget reconciliation) as SKILL-dominant — strengthen rules/policy signal
- [ ] Reassign T15 (time-series ETL) as SKILL-dominant — strengthen config-driven signal
- [ ] Add 1 new pure-SKILL task — e.g., "apply this grading rubric" or "classify against eligibility policy." Requires new fixtures + expected values + validator.

**A3 — Strengthen AGENT coverage (5 JIT AGENT tasks):**
- [ ] Keep T16 (root cause diagnosis) and T17 (fraud investigation) — genuinely adaptive
- [ ] Keep T08 (data quality) — add iterative element: "investigate root cause of unexpected patterns"
- [ ] Keep T13 (anomaly investigation) — add iterative element: "if one metric has many anomalies, investigate correlations with other metrics"
- [ ] Keep T10 (trend detection) — add adaptive element: "try multiple methods, compare errors, select best with justification"

**A4 — Output format diversity (at least 5 non-JSON outputs):**
- [ ] Modify T06 (KNN imputation) or T07 (fuzzy join) → output as **CSV file**
- [ ] Modify T12 or new SKILL task → output as **JSON array** (list of records, not dict)
- [ ] Modify T15 (time-series ETL) → output as **Markdown report**
- [ ] Modify T13 (anomaly investigation) → output as **Excel file** (multi-sheet)
- [ ] Modify one MCP_TOOL task → output as **plain text** summary
- Requires B3 (file output support) to be implemented first

**A5 — JIT MCP_TOOL (5 tasks):**
- [ ] T03 (text stats) — demoted from seeded
- [ ] T06 (KNN imputation)
- [ ] T07 (fuzzy join)
- [ ] T11 (multi-source integration)
- [ ] T18 (dedup by hash)

**Dependencies:** A4 requires B3 (file output support). A1 and A2 require new fixtures/validators for new tasks. A3 is prompt-only edits.

### B. Infrastructure additions (~1-2h code)

- [ ] **B1 — `--allow-network` flag**: add `allow_network` parameter to `sandbox_execute()` that skips `--network none`. Plumb through CLI as `mint run --allow-network`. Default remains offline. No testbed changes.
- [ ] **B2 — Dynamic `missing_external_calls` severity**: MEDIUM when network disabled (default), HIGH when `--allow-network` is enabled. One `if` in `static_scanner.py`.
- [ ] **B3 — File output support**: allow the last step of a chain to produce file outputs (CSV, Excel, Markdown, etc.) in addition to JSON dicts. The chain data flow contract is already structurally enforced:
  - **First step:** reads raw files (any format) → outputs JSON. **Enforced:** only step 0 gets fixture paths in `_all_inputs` (binary fix).
  - **Middle steps:** receives JSON → outputs JSON. **Enforced:** chain executor only passes `step_N_output.json` between steps. Steps 1+ don't get raw fixture paths.
  - **Last step:** receives JSON → outputs JSON by default. **B3 adds:** support for non-JSON output when the task explicitly requests it.
  - MCP_TOOL generator prompt describes this contract explicitly (updated in Phase 3.5c).
  - Changes needed (B3 only — the enforcement is already done):
    - Chain executor: detect when last step writes a file to `/workspace/output.*`, copy to user's output directory
    - CLI: display file path instead of JSON content when output is a file
    - Validator framework: add file-based validators (e.g., "output CSV has N rows and columns X, Y, Z")
  - Testbed: at least 5 tasks with non-JSON output formats (see A4)

### C. Documentation (~15 min)

- [ ] **C1 — Tier 1 severity rationale**: document synthetic data at HIGH as deliberate strengthening (Ch 4 note).
- [ ] **C2 — Network constraint**: document `--network none` default with `--allow-network` opt-in in Ch 4, sandbox limitation + future API-tool evaluation in Ch 7.
- [ ] **C3 — Update `proposal_vs_implementation_v2.md`** with any changes from A/B above.

### D. Accept and document (no fix)

- Decomposer non-determinism — LLM limitation, 5 runs average it out. Ch 6 finding.
- Binary file handling (T11) — prompt-only fix, LLM compliance dependent. Ch 6 limitation.

---

## Summary

| Issue | Severity | Action | Effort | Status |
|---|---|---|---|---|
| Testbed rebalance (20 tasks) | High | A1-A5 — seeded spread, SKILL/AGENT coverage, output formats, 2 new tasks | ~2-3h | TODO |
| File output support | High | B3 — executor + CLI + validator (blocks A4) | ~1-2h | TODO |
| Network access flag | Low | B1 — sandbox + CLI flag | ~30 min | TODO |
| Tier 1 severity dynamic | Low | B2 — one `if` statement | ~5 min | TODO |
| Documentation | Low | C1-C3 | ~15 min | TODO |
| Decomposer non-determinism | Medium | D — document as Ch 6 finding | ~5 min | TODO |
| Binary file handling (T11) | — | Fixed — sandbox wrapper name-aware matching | done | ✅ DONE |
| Pilot results | — | 2/4 passed (T01, T16). T04/T14 non-deterministic. | done | ✅ DONE |

**Execution order:**
1. B3 (file output support) — unblocks A4
2. A1-A5 (testbed redesign) — new tasks, prompt edits, output formats
3. B1-B2 (network flag + severity)
4. C1-C3 (documentation)
5. Phase 4 smoke tests (validation gate)
6. Phase 5 (15h eval)

**Total remaining pre-eval work: ~5-6 hours.**

**Recommended priority:**
1. Wait for pilot results (blocking)
2. Strengthen SKILL coverage (high impact, low effort)
3. Strengthen AGENT task prompts (medium impact, low effort)
4. Decide on JSON-only output (medium impact, higher effort)
5. Accept decomposer non-determinism and T11 as documented limitations
