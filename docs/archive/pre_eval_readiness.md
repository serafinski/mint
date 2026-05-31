# Pre-Eval Readiness Assessment

**Date:** 2026-04-11 (post-Phase 3.5c)
**Purpose:** Checklist of what needs to be resolved before committing to the ~18h 400-record eval run (20 tasks × 4 conditions × 5 runs).

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

**Status:** ✅ Code fix applied and validated. T11 confirmed passing with exact output match (first time ever). 989 unit tests pass.

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

### E. Test suite and scripts audit (~1-2h, added 2026-04-13)

**Motivation:** 1005 unit tests and a `scripts/` directory accumulated across 6 iterations. Almost certainly contains coverage for code paths that no longer exist (pre-Phase-2 single-gap classifier, pre-Phase-3 non-compositional executor, pre-Phase-3.5b single-agent generation, Iter-3 `tool_loader.py` TODO shapes). `scripts/` has similar archaeology: legacy `run_task.py` vs current `mint run`, pilot scripts that predate the 20-task testbed, ad-hoc diagnostic one-offs. Auditing **pre-eval** gives a clean baseline so thesis numbers are anchored to a tidy codebase, and post-eval cleanup doesn't risk invalidating the narrative by touching things after the numbers are locked.

**Scope:** relevance audit, not coverage expansion. The goal is to **delete** stale tests and scripts, not to add new ones.

- [ ] **E1 — Collect test inventory**: `uv run pytest tests/ --collect-only -q > /tmp/test_inventory.txt`. Group by target module.
- [ ] **E2 — Cross-reference against current source**: for each test file, identify which `src/` module it targets. Flag any test that targets:
  - Removed symbols (grep for imports that no longer resolve)
  - Pre-Phase-2 single-gap classifier shape (`classify_task` as primary, not the legacy wrapper)
  - Pre-Phase-3 non-compositional executor paths
  - Pre-Phase-3.5b single-agent generator (before lead-with-subagents)
  - Iter-3 `tool_loader.py` empty-schema TODO assertions (will be obsolete after B1 Gap 1 fix)
- [ ] **E3 — Delete or migrate flagged tests**: for each flagged test, either delete (if the code it exercised is gone) or migrate to the current shape (if the intent is still valid). Commit as its own diff, separate from B1/Iter-4 work, so `git blame` stays clean.
- [ ] **E4 — Scripts inventory**: `ls scripts/` and categorize each file as:
  - `active` — used by CI or eval harness
  - `diagnostic` — one-off investigation still worth keeping (e.g., `test_registry_matching.py`)
  - `legacy` — superseded by CLI or other scripts (e.g., `run_task.py` if fully replaced by `mint run`)
  - `duplicate` — covered by another script
- [ ] **E5 — Delete legacy/duplicate scripts**: anything in those two categories goes. Active and diagnostic stay.
- [ ] **E6 — Re-run `pytest tests/` after deletions**: confirm no accidental removal of still-needed coverage. Expect the count to drop (target: <1000, actual depends on findings).

**Ordering:** run E **after** B1 + Iter-4 fixes land (so the audit sees the final code shape) but **before** Phase 4 smoke tests (so the smoke tests run against a clean, trimmed suite). This is the natural insertion point: B1 changes `loop.py`, `operations.py`, `loading_node.py`, `tool_loader.py` — after which many existing tests for those modules will be stale anyway, making the audit concrete rather than speculative.

**Safety rails:**
- Delete in a separate commit from any code change so reverts are surgical
- Never delete a test that's currently failing without understanding why — a failing test may be catching a real regression, not just stale
- If uncertain about a test, leave it and annotate with a `# TODO: audit relevance` comment instead of deleting

---

## Summary

| Issue | Severity | Action | Effort | Status |
|---|---|---|---|---|
| Testbed rebalance (20 tasks) | High | A1-A5 — seeded spread, SKILL/AGENT coverage, output formats, 2 new tasks | ~2-3h | ✅ DONE (2026-04-12) |
| File output support | High | B3 — executor + CLI + validator (blocks A4) | ~1-2h | ✅ DONE (2026-04-12). **Known limitation:** SKILL/AGENT last steps don't produce files. See `docs/file_output_fix_plan.md`. |
| Network access flag | Low | B1 — sandbox + CLI flag | ~30 min | ✅ DONE (2026-04-12) |
| Tier 1 severity dynamic | Low | B2 — one `if` statement | ~5 min | ✅ DONE (2026-04-12) |
| Documentation | Low | C1-C3 | ~15 min | ✅ DONE (2026-04-12) |
| Chain data flow key mismatch | High | Schema contracts + upstream_data parameter | ~2h | ✅ DONE (2026-04-12). See `docs/chain_schema_contracts_plan.md`. |
| Decomposer non-determinism | Medium | D — document as Ch 6 finding | ~5 min | Accepted — LLM limitation |
| Binary file handling (T11) | — | Fixed — sandbox wrapper name-aware matching | done | ✅ DONE |
| Pilot results (2026-04-12) | — | test_mint 7/7 passed. T04 exact match (first time). T11 5/6 fields. | done | ✅ DONE |

**Pre-eval readiness items: ALL COMPLETE** for the original 2026-04-12 checklist.

**⚠️ NEW eval-blocking items discovered 2026-04-13** — see `docs/b1_enriched_embedding_plan.md` for full details.

| Issue | Severity | Action | Effort | Status |
|---|---|---|---|---|
| Seed description drift | High | 5/8 seed descriptions over-promised vs. code. Fixed in `src/registry/store.py`, upserted into `data/cli/chroma`. | ~30 min | ✅ DONE (2026-04-13) |
| T01 registry miss (csv-parser) | High | Fixed as side effect of seed description update — new description aligns better with decomposer query vocabulary | — | ✅ DONE (2026-04-13) |
| Pillar 3 Merge missing "overlapping input schemas" conjunct | High | Iter-4 drift discovered 2026-04-13. Fix in progress as B1. Populate schemas + add Jaccard helper + `[0.5, 1.0)` gate in `identify_candidates_node`. | ~1.5h | 🟡 PARTIAL (schemas done for seeds, AST extractor done, loading-node wiring + Jaccard pending) |
| `merge()` produces metadata-only stubs | **Eval-blocking** | Iter-4 gap discovered 2026-04-13. Retain highest-reuse parent's implementation when creating merged descriptor. Partial fix for proposal's "inherits strongest behavioral patterns". | ~1h | ⏸ PENDING |
| `split()` produces metadata-only stubs | **Eval-blocking** | Iter-4 gap discovered 2026-04-13. Disable via `split_reuse_threshold=1.01` in pipeline factory. Principled fix deferred to Chapter 7. | ~10 min | ⏸ PENDING |
| Phase D reusability measurement | — | Running on 11 generated caps produced thesis-level tension-#2 finding: 73% scenario-bound, 91/9/0 tight/mid/cross reuse gradient. Chapter 6 number. | — | ✅ DONE (2026-04-13) |
| Test suite + scripts audit (1005 tests, archaeology) | Medium | E1-E6 — delete stale tests/scripts so eval baseline is clean. Run AFTER B1/Iter-4 lands, BEFORE Phase 4 smoke tests. | ~1-2h | ⏸ PENDING |

**Additional work done (not in original plan):**
- Chain schema contracts (proposal §3.4 compliance) — decomposer output_schema + generator contracts
- `upstream_data` parameter for robust MCP_TOOL chain data flow
- CLI timing fix (real elapsed times)
- Pipeline metrics + step timings in run.json
- Data path cleanup (mint_output + last_run.json → data/cli/)
- **NEW 2026-04-13:** `scripts/test_registry_matching.py` four-phase diagnostic (naive probe + context audit + self-consistency audit + generalization audit)
- **NEW 2026-04-13:** `src/orchestrator/schema_extraction.py` AST-based MCP tool signature extractor

**Phase 4 smoke tests (validation gate before eval) — BLOCKED on Iter-4 fixes:**
- [x] `uv run pytest tests/unit/` — 1005 pass ✅
- [ ] **⚠️ Re-run after B1 + Iter-4 fixes land** — schema/extractor changes may affect unit tests
- [ ] `uv run scripts/test_pipeline.py` — pipeline integration
- [ ] `uv run scripts/test_verification.py` — three-tier gate
- [ ] `uv run scripts/test_auto_optimizer.py` — optimizer (will exercise the fixed merge/split paths)
- [x] `uv run scripts/test_mint.py` — 7/7 passed ✅ (2026-04-12, pre-B1)
- [ ] `uv run scripts/test_mint.py` re-run after B1 + Iter-4 fixes
- [ ] `uv run scripts/run_pilot.py --all-tasks --condition full --n-runs 1` — 20-task pilot
- [ ] Verify type spread (≥4 of each type), seeded matches (T01/T02/T05/T09/T19)
- [ ] Review 3-4 LangFuse traces
- [ ] Spot-check `data/eval/metrics.duckdb`

**Remaining before eval (updated 2026-04-13):**
1. **Finish B1 + Iter-4 fixes** (~4-5h) — see `docs/b1_enriched_embedding_plan.md` combined fix list
2. File output fix for SKILL/AGENT last steps (~1h) — see `docs/file_output_fix_plan.md`
3. **Test suite + scripts audit** (~1-2h) — section E above. Run after #1/#2 so the audit sees final code shape.
4. Re-run Phase 4 smoke tests after fixes + audit land
5. Run `run_pilot --all-tasks --condition full --n-runs 1` (20-task pilot)
6. Phase 5 eval (20 tasks × 4 conditions × 5 runs, ~18h)
