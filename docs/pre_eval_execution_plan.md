# Pre-Eval Execution Plan

**Scope:** everything that has to happen between now and the full 360-record eval, in the order it should happen.

**Sibling docs:**
- `docs/proposal_vs_implementation.md` — full audit, every row with tags and justifications
- `docs/todo_drift_and_missing.md` — focused list of remaining drift/missing items
- `docs/compositional_synthesis_plan.md` — concrete implementation plan for Stages 1-3 of the compositional refactor (referenced from Phase 1-3 below)

This doc is the **sequencing and validation** layer. It does not duplicate the implementation detail in the compositional plan — it decides **what order** to do things in, **where to stop and check**, and **what to watch for**.

---

## State right now

- Audit complete. Every deviation from the proposal has been categorised (✅ / ⭐ / 🟢 / 🔴 / ❌).
- C1 (compositional sequencing) decided: **ship the full compositional plan pre-eval**. Rationale in `docs/proposal_vs_implementation.md` TL;DR.
- C2 (KL headline metric) decided: point-mass primary, histogram sensitivity check.
- CLI display bugs (`-1 artifact(s)`, `0.0s Tier 3`, phantom `268.3s` on `generate_agent`, label misattribution on classifier) — ✅ **already fixed** in commit `781dc4e`. 12 regression tests in `tests/unit/test_cli_runner_gen_stage.py` all pass.
- Unit-test baseline: **887 passing** as of Phase 0 verification (2026-04-09). Any phase that drops below this is a blocker.
- Pre-audit eval data is stale; `data/eval/` will be purged before the full eval.

## Ordering principles

1. **Lowest risk first.** Build momentum with changes that can't destabilise the pipeline.
2. **Validate between phases, not at the end.** Each phase has an explicit gate. If the gate fails, stop and fix before the next phase.
3. **Parallelise where the files don't overlap.** Tier 1/2 hardening touches `src/verification/` and `tools/quick_validate.py`; compositional work touches `src/orchestrator/` and `src/verification/sandbox_tool.py`. Minimal merge surface.
4. **Commit at every phase boundary.** Small commits with clear scopes, not one mega-PR.
5. **Do not touch `thesis_proposal.md`.** It is the contract we're measuring against.

---

## Phases

### Phase 0 — Pre-work verification — ✅ **COMPLETE** (2026-04-09)

**Goal:** confirm the baseline state is what we think it is before starting real work.

- [x] `uv run pytest tests/unit/` — **887 passed in 155.92s**. Baseline is higher than the 814 figure in CLAUDE.md because 73 additional tests have landed since (including the 12 from the CLI display fix).
- [x] `uv run pytest tests/unit/test_cli_runner_gen_stage.py -v` — all 12 regression tests pass (verified twice in this phase).
- [x] `docker build -t jit-sandbox sandbox/` — clean build, 5 layers, `jit-sandbox:latest` tagged successfully.
- [x] `git status` — working tree clean of code changes (only in-progress doc edits and pre-existing untracked dirs `.claude/`, `data/`).

**Gate to Phase 1:** ✅ baseline green. Proceed to Phase 1.

---

### Phase 1 — Low-risk foundation — ✅ **COMPLETE** (2026-04-09)

**Commits:** `13b1ecd` (honest metrics + Tier 1/2 hardening) and `8ab68b0` (CLI runner verification sidecar follow-up).

**Verified:** 938 unit tests pass (+51 net new); full `test_mint.py` 7/7 across all three capability types; `run_pilot.py T14 --condition full --n-runs 1` writes a correct row to `data/eval/metrics.duckdb` with all 7 new compositional columns populated and round-trippable via `_row_to_record`. Stage 1 fake-write purge verified directly via SQL: zero rows in `data/cli/metrics.duckdb` `invocations` table match the `latency_ms=0` signature. Zero false positives from any new check across 8 real generations; one true positive caught — **T11 attempt 1 had a `placeholder` literal inside a Python code fence in the generated SKILL.md, the new code-fence scanner caught it (CRITICAL `mock_artifact` finding with location `code_fence[0]:'placeholder' at position 47`), the gate hard-rejected, and attempt 2 produced a clean SKILL with no findings**. T04 prose-false-positive scoping is preserved (the prose regex is still scoped to MCP_TOOL inside `static_scanner.scan()`).

**Goal:** land the three cheapest, lowest-risk items so Phase 2/3 start from a cleaner footing. These are independent of each other and can be committed in any order (or interleaved).

Three parallel tracks:

#### Track 1a — Compositional Stage 1 (~1-2 days) — ✅ **DONE** in `13b1ecd`

Reference: `docs/compositional_synthesis_plan.md` Stage 1.

- [x] Remove fake `InvocationRecord` writes from `src/orchestrator/gap_detection.py:65-73` (explicit comment marks the block)
- [x] Add new `EvaluationRecord` fields with safe defaults: `chain_length`, `chain_success`, `matched_and_executed_count`, `generated_artifact_count`, `capability_types: list[str]`, `failed_step_index: int | None`, `failed_step_name: str | None`
- [x] Add `CapabilityReference` and `ChainResult` schemas to `src/schemas/task.py` (used later in Stage 3; adding schemas now keeps Stage 3 lighter)
- [x] DuckDB schema migration in `src/registry/evaluation_store.py` — `_INIT_SQL` declares the new columns; `_MIGRATIONS` runs `ALTER TABLE ADD COLUMN IF NOT EXISTS` (DuckDB doesn't support `NOT NULL DEFAULT` on ALTER, so old rows get NULL and `_row_to_record` coerces them via `or 0` / `is not None else default`)
- [x] Update `tests/unit/test_gap_detection.py` — assertions inverted to `metrics_store.record_invocation.assert_not_called()`
- [x] Update `tests/evaluation/harness.py` `_extract_result` to populate the new fields with single-artifact defaults (`chain_length=1`, `chain_success = gate_passed and output_in_range`, `matched_and_executed_count = 1 if matches and not artifacts else 0`, etc.)

**Verification:** full unit suite passes. Run `uv run scripts/test_mint.py --mcp-only` smoke test — T14 should produce correct output, and the `csv-parser` invocation count in `data/cli/metrics.duckdb` should be **0** (not 1 from the fake write).

#### Track 1b — Tier 1 hardening bundle A1 (~1 day) — ✅ **DONE** in `13b1ecd`

Reference: `docs/todo_drift_and_missing.md` section A1.

- [x] Part A — MCP_TOOL Tier 1 additions: `_check_mock_imports` (covers `unittest.mock`, `mock`, `faker` via both `Import` and `ImportFrom` AST nodes — CRITICAL), `_check_random_seed` (catches `random.seed(<literal>)`, `np.random.seed(...)`, `numpy.random.seed(...)` — HIGH), `_check_lorem_ipsum` (regex on source — HIGH). All wired only inside the `if capability_type == CapabilityType.MCP_TOOL:` branch in `static_scanner.scan()`.
- [x] Part B — SKILL/AGENT code-fence scanner: `_extract_python_fences()` regex helper in `gate.py`, plus layered scan in `_static_scan` that extracts ` ```python ` fences from `skill_content` (SKILL) and from `system_prompt` + `skill_content` (AGENT with `is_deep_agent=True`), runs the full MCP_TOOL `static_scanner.scan()` on each fenced snippet, and merges findings with `code_fence[N]:...` location prefix. Re-evaluates `overall_passed` after merge so fence CRITICALs hard-reject.
- [x] Part C — Tier 0 prose checks in `tools/quick_validate.py`: three literal hard-fails on `\blorem ipsum\b` (case-insensitive), `TODO: implement`, and `# placeholder return` against the SKILL.md body (frontmatter is stripped first via the same regex used for parsing).
- [x] Unit tests: 15 new in `test_static_scanner.py`, 7 new in `test_gate.py` (including the `test_skill_prose_placeholder_not_flagged_t04_regression` case that explicitly verifies prose-scoping is preserved), 10 in the new `test_quick_validate.py`.

**Verification:** full unit suite still passes. Run `uv run scripts/test_verification.py` to confirm three-tier gate works end-to-end with the new checks.

#### Track 1c — Tier 2 latency rename + variance bundle A2 (~0.5 day) — ✅ **DONE** in `13b1ecd`

Reference: `docs/todo_drift_and_missing.md` section A2.

- [x] Renamed `LATENCY_THRESHOLD_S` → `TIMEOUT_THRESHOLD_S`, `_test_latency` (old) → `_test_timeout`, and `category="latency"` → `"timeout"` for the wall-time-based check. `src/cli/display.py:305` uses generic substring matching that handles both labels unchanged.
- [x] New `_test_latency` (variance check): collects 3 distinct boundary input variants via `_generate_{boundary,negative,large}_inputs`, dedupes by `repr(sorted(...))`, samples elapsed times via the existing sandbox helper, computes mean and stdev via stdlib `statistics`, flags MEDIUM only if `mean < 0.001` AND `cv < 0.10` (both required). Returns empty list if `< 2` successful samples.
- [x] Unit tests: 9 new across two new test classes in `test_behavioral.py` (positive case, slow case, variable case, insufficient-samples case, empty-schema edge case).

**Verification:** behavioral test suite passes. Spot-check that a legitimate fast tool (e.g. seeded `fibonacci`) does not trip the new variance check.

#### Phase 1 combined gate — ✅ **PASSED** (2026-04-09)

What was actually run (deviates from the original mechanical list — see note below):

- [x] Full unit suite passes — **938 passed** in ~130s (was 887 baseline; +51 net new tests across the three tracks)
- [x] **Full** `uv run scripts/test_mint.py` (not just `--quick`) — **7/7 tasks passed** across all three capability types and 6 file formats. ~$0.20, ~25 min. Strict superset of `--quick` and exercises every new gate path through real LLM generations.
- [x] `uv run scripts/run_pilot.py --tasks T14 --condition full --n-runs 1` — wrote a clean row to `data/eval/metrics.duckdb` with all 7 new compositional columns populated and round-trippable via `EvaluationStore._row_to_record`. ~$0.10, ~7 min. **Only smoke that exercises the harness path** (`tests/evaluation/harness.py:_extract_result`) and the file-backed `EvaluationStore` migration.
- [x] Stage 1 fake-write purge confirmed via SQL on `data/cli/metrics.duckdb`: `SELECT COUNT(*) WHERE latency_ms = 0` returns 0.
- [x] **Commit:** `13b1ecd feat(phase1): honest metrics + Tier 1/2 hardening bundles` + follow-up `8ab68b0 feat(cli): persist verification report sidecar alongside mint_output`. Both pushed to `origin/iter/6-execution-backends`.

**Tests deliberately skipped from the original mechanical list, with rationale:**

- `test_mint.py --quick`: superseded by full `test_mint.py`. The quick variant only checks CLI format detection ($0, 30s); the full variant exercises every code path the quick variant does *plus* end-to-end generation through all three capability types. Running both is redundant.
- `scripts/test_pipeline.py`: full `test_mint.py` is a strict superset for Phase 1 coverage purposes — both invoke the same 9-node pipeline graph and `test_mint` adds more variety across capability types and file formats.
- `scripts/test_verification.py`: the unit tests already cover the new gate paths (15 new tests in `test_static_scanner.py`, 7 new in `test_gate.py`, 10 in the new `test_quick_validate.py`, 9 new in `test_behavioral.py`). `test_verification.py` would only add Docker integration coverage, which isn't part of the Phase 1 changeset (we didn't touch `sandbox_tool.py` or the Docker layer).

**What the verification actually caught (real-world signal, not just unit-test green):**

- Zero false positives from any new check (`_check_mock_imports`, `_check_random_seed`, `_check_lorem_ipsum`, code-fence scanner, variance latency) across **8 real LLM generations** (7 from test_mint + 1 from pilot T14, including 2 attempts on T11 and 2 on T14).
- **One true positive caught:** T11 attempt 1 generated a SKILL.md with a Python code fence containing a literal `placeholder`, the new code-fence scanner extracted the fence and ran the existing `_check_mock_artifacts` regex on it, fired CRITICAL `mock_artifact` with `location="code_fence[0]:'placeholder' at position 47"`, gate hard-rejected, retry produced a clean SKILL on attempt 2. **The T04 prose-false-positive scoping was preserved** — verified by the explicit `test_skill_prose_placeholder_not_flagged_t04_regression` test.
- **One unrelated true positive caught on the pilot:** T14 attempt 1 generated MCP_TOOL code with two `except Exception: pass` blocks (lines 157 and 175). Existing `_check_silent_except` (NOT a new check) flagged both as HIGH, gate aggregated to reject after running all three tiers, retry produced clean code on attempt 2. Same pattern — retry loop is doing real work, not creating false friction.

**Decision gate:** ✅ Phase 1 is clean. Phase 2 (per-gap restoration) is unblocked.

---

### Phase 2 — Per-gap restoration (~2-3 days)

**Goal:** restore per-gap classification and multi-artifact generation. Execution is **still single-artifact** at the end of this phase (priority sort picks one). The point is to get the classifier and generators producing the right shape before the compositional executor exists to run them.

Reference: `docs/compositional_synthesis_plan.md` Stage 2.

- [ ] `src/orchestrator/gap_detection.py` — revert placeholder-gap behaviour. Produce N enumerated `GapItem`s, one per unmatched required capability. Keep per-capability registry matching. (The collapse-to-placeholder block is the opposite of what we want.)
- [ ] `src/orchestrator/classifier.py` — add `classify_gap(gap, task_context)`. `classifier_node` iterates `state.gaps`, produces N `ClassifiedGap` entries. `classify_task` becomes a thin wrapper preserved for backwards compat in the single-gap case.
- [ ] `src/orchestrator/prompts/classifier.py` — rewrite prompt to classify a single gap within the context of the whole task. Three-layer framing preserved. Few-shot examples updated.
- [ ] `src/orchestrator/prompts/{mcp_tool,skill,agent}_generator.py` — update to frame each artifact as **one step of a workflow**, not the whole task. Include full task description as context.
- [ ] Generators already support multi-artifact mode (they filter by type and iterate) — no code change to `src/orchestrator/generators/*.py` expected beyond confirming retry dedup works correctly with N artifacts
- [ ] Execution stays single-artifact: priority sort in `tests/evaluation/harness.py` and `src/cli/runner.py` picks one artifact to actually run. **This is a temporary accepted regression** — Phase 3 replaces the priority sort with compositional execution
- [ ] Update unit tests: `test_gap_detection.py` (N gaps), `test_classifier.py` (per-gap assertions), `test_pipeline.py` (multi-classified_gaps), generator tests (multi-artifact)

**Verification:** 
- Full unit suite passes (expect ~40-60 test assertions to need updating)
- `uv run scripts/test_mint.py` — full suite with all three types
- **Spot-check T14 via LangFuse**: should now show **2 gaps**, **2 classified_gaps**, **2 generated artifacts** (csv-parser as match + one aggregator as generated), and **1 executed artifact** (priority sort picks one). This is the intermediate state before Phase 3 makes the chain actually run.
- **Spot-check T11 via LangFuse**: should show **3 gaps**, **3 classified_gaps**, **N generated artifacts** (mixed types are OK)

**Commit:** `feat(phase2): per-gap classification and multi-artifact generation`

**Decision gate:** if per-gap classification is producing the right shape in state but only one artifact runs, proceed to Phase 3. The intermediate state is OK.

---

### Phase 3 — Compositional executor (~3-5 days, possibly longer if HP4 bites)

**Goal:** the payoff phase. Matched capabilities and generated artifacts actually run in dependency order, with workspace passing.

Reference: `docs/compositional_synthesis_plan.md` Stage 3 — see HP1 (output passing), HP4 (mixed-type execution), HP5 (validator redesign). This is the highest-risk phase.

- [ ] New `src/orchestrator/composition.py` with `CompositionalExecutor` class. Takes `subtasks`, a `resolution` dict (map from required_capability → `CapabilityReference`), and fixture paths. Builds a DAG via `graphlib.TopologicalSorter`, executes in order, passes workspace state.
- [ ] Workspace structure: `/tmp/mint_workspace_<task_id>/` with `fixtures/` subdirectory for input files and `step_<N>_output.json` for intermediate outputs
- [ ] `src/verification/sandbox_tool.py` — add optional `workspace_path` parameter to `sandbox_execute()`. When provided, mount the host workspace into the Docker container at `/workspace` via `-v` flag
- [ ] `tests/evaluation/execution.py` — add `execute_mcp_tool_in_chain()` helper. Refactor `execute_skill_artifact` and `execute_agent_artifact` to accept a `workspace_path` parameter
- [ ] **HP4 risk checkpoint**: verify that a chain with `MCP_TOOL → SKILL` actually works. The MCP_TOOL writes to `/workspace/step_0_output.json` in Docker; the SKILL (in-process DeepAgent via `StateBackend`) must be able to read that file. If the SKILL backend cannot see Docker-mounted volumes, we need to inject the step-0 output as a parameter or pre-populate the DeepAgent's `StateBackend`. **Flag this as a risk and test it early in Phase 3.**
- [ ] `tests/evaluation/harness.py` `_extract_result` — branch on chain length. Length 1 → existing single-artifact path (backwards compat). Length >1 → `CompositionalExecutor`. Populate chain-level fields in `EvaluationRecord`
- [ ] `src/cli/runner.py` — same branching. Update `src/cli/display.py` with chain visualisation: `Step 1/3: csv-parser (registry match) → Step 2/3: ...`
- [ ] New test file `tests/unit/test_composition.py` — DAG building, topological sort, cycle detection, workspace management, **mixed-type chain execution (the risky test)**, chain failure handling
- [ ] New test file `tests/integration/test_composition.py` — end-to-end T14-style run, verify `csv-parser` is actually invoked (non-zero latency_ms in metrics store), verify output is correct

**Verification:**
- Full unit suite passes
- `uv run scripts/test_mint.py` — all tests
- **T14 LangFuse trace should now show**: csv-parser invocation with real `latency_ms > 0` in metrics, chain_length=2, matched_and_executed_count=1, generated_artifact_count=1
- `mint list` should show csv-parser with real invocation counts (not decorative)

**Commit:** `feat(phase3): compositional executor with dependency-ordered chain execution`

**Decision gate:** if Phase 3 works end-to-end for T14 and at least one mixed-type chain, proceed to Phase 4. **If HP4 is not solvable in ~5 days, stop and reassess** — fall back to Phase 2's single-artifact execution and treat Stage 3 as follow-up work, with Ch 6 explicitly noting the limitation.

**Fallback option:** the codebase stays at the end of Phase 2 (per-gap classification, multi-artifact generation, priority-sort execution). Ch 6 reports per-gap classification as working and notes compositional execution as "implemented in `docs/compositional_synthesis_plan.md` but pending integration" — a much narrower caveat than the current state.

---

### Phase 4 — Pre-eval smoke tests (~2 hours)

**Goal:** catch integration bugs on cheap fast runs before committing to the full 12h eval.

- [ ] `rm -rf data/eval/` — purge stale pilot state
- [ ] `uv run scripts/test_pipeline.py` — full 9-node pipeline integration
- [ ] `uv run scripts/test_verification.py` — three-tier gate
- [ ] `uv run scripts/test_auto_optimizer.py` — prune/split/merge
- [ ] `uv run scripts/test_split_merge.py` — optimizer operations
- [ ] `uv run scripts/run_pilot.py --tasks T01,T04,T14 --condition full --n-runs 1` — 3-task pilot on the new pipeline
- [ ] **B1 empirical check** (~45 min): run T08, T10, T13, T16, T17 under compositional pipeline, check whether subtasks that classify as AGENT actually need AGENT
- [ ] Review 2-3 LangFuse traces manually to confirm chains are running end-to-end
- [ ] Spot-check `data/eval/metrics.duckdb` — verify invocation counts are honest (csv-parser should have `latency_ms > 0` from real execution, not 0 from fake writes)

**Gate to Phase 5:** all smoke tests green. If any fail, loop back to the relevant phase.

---

### Phase 5 — Full eval (~15h wall time)

**Goal:** run the 360-record eval on the clean pipeline.

- [ ] `rm -rf data/eval/` — clean slate
- [ ] `uv run scripts/run_pilot.py --all-tasks --all-conditions --n-runs 5` — 18 × 4 × 5 = 360 records, ~12h with 2 conditions in parallel
- [ ] `uv run scripts/run_longitudinal.py` — 40 tasks, no reset, ~3h

**Gate to Phase 6:** eval completes without crashes. Spot-check 3-4 records in LangFuse for sanity.

---

### Phase 6 — Post-eval aggregation (~7 hours)

**Goal:** produce the three missing metrics and backfill token costs.

- [ ] `uv run scripts/backfill_token_costs.py --write` — LangFuse → DuckDB token cost extraction
- [ ] **D1 — Mock-contamination audit** (~5h):
  - Build small audit CLI (~30 min) — loops unaudited records, prints artifact, prompts for `CORRECT` / `CONTAMINATED` / `PARTIAL`, writes back
  - Sample 30-50 records stratified by condition × capability_type
  - Sit and label (~3h)
  - Add aggregation to `scripts/analyze_results.py` (~30 min): contamination rate by condition with bootstrap CI
- [ ] **D2 — Gini coefficient** (~1h):
  - Add `_compute_gini()` helper to `analyze_results.py`
  - Query invocation counts from `data/eval/longitudinal.duckdb` (longitudinal only — cross-sectional snapshot resets make it meaningless)
  - Report Gini at end of longitudinal run + Gini-over-time curve
- [ ] **D3 — KL divergence surfacing + function rename** (~1h):
  - Rename `_compute_kl_divergence` → `_compute_kl_pointmass` in `src/optimizer/loop.py`
  - Rename `_kl_divergence` → `_compute_kl_histogram` in `src/optimizer/simulation.py`
  - Load `data/longitudinal_optimizer_log.json`, plot KL curve over 40 tasks
  - Compute convergence cycle (first cycle where KL drops below 0.01 for ≥5 consecutive cycles)
  - Report point-mass KL primarily, histogram KL as sensitivity check column
- [ ] `uv run scripts/analyze_results.py --csv` — full statistical analysis with all six proposal metrics
- [ ] `uv run scripts/analyze_context_utilization.py --csv` — context window metrics

**Gate to Phase 7:** all six proposal metrics are now produced. Ch 6 has its data.

---

### Phase 7 — Chapter drafting (no code, happens during thesis writing)

- [ ] **E1** — Ch 6 testbed description: replace "ML model training, inference" with "statistical analysis, anomaly detection, root-cause inference, reporting"
- [ ] **E2** — Ch 3 §3.5 + tech-stack table: drop Google ADK / A2A / TypeScript MCP / TOML / Claude-Code-as-runtime; add DeepAgents (harness-on-small-model strategy), DuckDB (⭐ metrics backbone), LangFuse (⭐ delivers token cost metric), three execution backends (⭐ classifier has runtime consequences)
- [ ] **E3** — Ch 4 Tier 1 framing: "Tier 1 scopes its checks to executable portions of each artifact (Python source for MCP_TOOL, code-fenced snippets for SKILL/AGENT); prose-level cheating is out of scope by design and caught by Tier 2/3"
- [ ] Cite B1.2, B2.1, B2.3 bypass findings as examples of iterative verification hardening
- [ ] Cite `compositional_synthesis_plan.md` as the Pillar 1 implementation path
- [ ] Cite `proposal_vs_implementation.md` as the audit reference

---

## What to watch

**Risks worth tracking during the work:**

1. **HP4 (mixed-type workspace passing)** — the biggest technical unknown. Surface it early in Phase 3; don't leave it until the end.
2. **Test rewrite volume in Phase 2** — estimate is 40-60 assertions across `test_gap_detection`, `test_classifier`, `test_pipeline`. If it balloons to 100+, something about the revert is more invasive than the plan expects.
3. **Phase 3 timeline slippage** — if Phase 3 is not done in 5 working days, stop and use the Phase 2 fallback. Do not let Phase 3 consume the eval deadline.
4. **Optimizer coupling after Stage 1** — removing fake invocation records changes what `prune()` sees. Verify in Phase 1 that prune correctly identifies unused capabilities without false positives.
5. **SKILL `network_disconnect` Tier 3 finding (T11, stale data)** — seen once in a pre-fix run, likely a prober false positive on a skill without source code. Watch for it in Phase 4 smoke tests; if it reproduces, investigate whether Tier 3 should skip SKILLs without source code.

**Metrics worth monitoring at each phase gate:**

- Unit test count (baseline 887)
- Full-suite runtime (should not degrade meaningfully)
- LangFuse trace sanity on 1-2 representative tasks per phase

---

## Non-goals for this plan

- `thesis_proposal.md` content changes (the proposal is the contract, not a working doc)
- Optimizer redesign (Pillar 3 is the most faithful subsystem and does not need work)
- New MCP tool features (we're not adding capability-side features, we're fixing the loop)
- Performance optimisation beyond what's already done (uv sandbox, LangFuse tag propagation)

---

## Cost summary

| Phase | Wall time | Dollar cost | Risk |
|---|---|---|---|
| Phase 0 — baseline verification | 15 min | $0 | none |
| Phase 1 — foundation (Stage 1 + A1 + A2) | 2-3 days | ~$0 (local tests) | low |
| Phase 2 — per-gap restoration (Stage 2) | 2-3 days | ~$0.20 (smoke tests) | medium |
| Phase 3 — compositional executor (Stage 3) | 3-5 days (10 days worst case) | ~$0.50 (pilot) | **high — HP4 is the unknown** |
| Phase 4 — pre-eval smoke tests | 2 hours | ~$1 | low |
| Phase 5 — full eval + longitudinal | ~15 hours wall | ~$5 | low (just waiting) |
| Phase 6 — post-eval aggregations + mock audit | ~7 hours | ~$0 | low |
| Phase 7 — chapter drafting | — | $0 | — |

**Total pre-eval engineering: 7-12 working days. Total pre-eval compute: ~$2. Total eval compute: ~$5. Total post-eval: ~$0.**

The dominant risk is Phase 3 timeline. The dominant cost is the eval itself. Everything else is cheap.
