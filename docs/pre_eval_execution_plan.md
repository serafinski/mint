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

### Phase 2 — Per-gap restoration — ✅ **COMPLETE pending commit** (2026-04-09/10)

**Goal:** restore per-gap classification and multi-artifact generation. Execution is **still single-artifact** at the end of this phase (priority sort picks one). The point is to get the classifier and generators producing the right shape before the compositional executor exists to run them.

Reference: `docs/compositional_synthesis_plan.md` Stage 2.

- [x] `src/orchestrator/gap_detection.py` — produces N enumerated `GapItem`s, one per unmatched required capability. Per-capability registry matching preserved.
- [x] `src/orchestrator/classifier.py` — `classify_gap(gap, task_description, required_capabilities)` added. `classifier_node` iterates `state.gaps` producing N `ClassifiedGap` entries. `classify_task` preserved as legacy wrapper.
- [x] `src/orchestrator/prompts/classifier.py` — rewritten with parent-task-first decision cascade and packaging-signal checklist. Anthropic-aligned broader skill definition (skill = packaged specialized expertise, not just rules). Multiple iterations during the day to remove an over-strict "would a generic LLM get this wrong" test that was biasing classifications toward mcp_tool.
- [x] `src/orchestrator/prompts/{mcp_tool,skill,agent}_generator.py` — frames each artifact as one step of a workflow with the task as context. **Note**: this framing introduced the architectural incoherence with the priority-sort executor described below.
- [x] `src/orchestrator/prompts/decomposition.py` — anti-split clauses from commit `89f2bc5` reverted. Decomposer now produces multi-step structures for multi-step workflows. Domain-language-preservation rule added so subtask names carry the packaging signals the classifier needs. Layer definitions broadened to be Anthropic-spec-aligned.
- [x] All five prompt files cleaned of testbed-specific vocabulary (no fixture filenames, no testbed task titles, no fixture field names). Few-shot examples use loan eligibility / shipping rates / patient triage / image deduplication — all clearly outside the business analytics testbed.
- [x] Execution stays single-artifact: priority sort already implemented in `tests/evaluation/harness.py` and `src/cli/runner.py` (no change needed). **Architectural finding**: this turned out to be more broken than the plan anticipated — see "Architectural finding" below.
- [x] Unit tests updated: `test_gap_detection.py` (3 assertions), `test_classifier.py` (8 assertions + 1 new mixed-type test), `test_pipeline.py` (1 test). 939 unit tests pass (+1 from 938 baseline). Total assertion churn ~9, far under the 40-60 estimate.
- [x] Five CLI bug fixes for the multi-capability output shape:
  - **Bug 1** — `runner.py:1376` verification sidecar `getattr(dict, ...)` was always returning None; replaced with dict-aware `_state_get` pattern. The Phase 1 sidecar feature now actually writes `<cap>_<ts>.verification.json`.
  - **Bug 2** — `display.py` new `_show_multi_capability_summary` helper enumerates `state.generated_artifacts` and marks which one was executed, with type + gap_query per artifact. Phase 2's multi-artifact output is now visible in the CLI run summary.
  - **Bug 3** — `runner.py` execution exception text captured into `exec_error` closure variable and propagated into the `_empty` placeholder dict via `_save_output`. Tool errors are now stored on disk with `_error: "ExceptionType: message"`, not just printed to stderr.
  - **Bug 4** — `runner.py:_save_output` extended with optional `final_state`, `task`, `file_paths`, `exec_error` parameters; writes a per-run manifest `<cap>_<ts>.run.json` containing task, files, all generated capabilities (with type/gap_query/executed flag), registry matches, and classified gaps. Single file to inspect a multi-capability run.
  - **Bug 5c** — `mcp_tool_generator.py` system prompt augmented with explicit "do NOT catch exceptions and return error dicts" instruction. Prevents T09-shape silent failures where the tool catches its own JSON parse error and returns `{"summary": "Failed to parse..."}` instead of raising.
- [x] Three planning doc updates (`pre_eval_execution_plan.md` Phase 3.5 + Phase 7 E4-E8 + Phase 7 F1-F4, `compositional_synthesis_plan.md` Stage 4.5, `todo_drift_and_missing.md` Phase 2 + Phase 3.5 status).

**Verification — what was actually run:**

- ✅ Full unit suite: `uv run pytest tests/unit/` — **939 passed** (+1 from 938 baseline)
- ✅ Pilot spot-check: `uv run scripts/run_pilot.py --tasks T01,T04,T14,T16 --condition full --n-runs 1` — **4/4 full success** (gate=True, output=True for all four). Mixed-type classifications observed: T04 produced 2 skills (the inventory-reorder-rules-applier steps) + 3 mcp_tools (parser, days-of-stock calculator, summary aggregator). T14 produced 1 skill (transactions-cap-enforcer) + 3 mcp_tools (parser, aggregator, comparator). T16 collapsed to all-agent via the over-decomposition rule. T01 stayed mcp_tool. The packaging-signal classifier rebalance is working correctly.
- ✅ `uv run scripts/test_mint.py` (full smoke test, all 7 tests): 6/7 passed. T11 hard-failed, T09 silent-failed. **These failures are diagnostic, not bugs** — see Architectural finding below.

**Architectural finding from Phase 2 verification (the reason the test_mint failures are not blocking):**

Phase 2 made two changes that work at cross purposes:

1. **Generators now produce chain-step artifacts**, framed as "perform ONE STEP of a larger workflow" with the task description as context. This is per the compositional plan's intent — it sets up Phase 3's compositional executor to run the artifacts in dependency order with workspace state passing.
2. **Executor still picks ONE artifact via priority sort and runs it standalone**, passing only the raw input files. This is the single-artifact regression that the compositional plan explicitly accepted as a temporary intermediate state.

The two are incoherent for **multi-step MCP_TOOL chains**: the generator produces "step 3 of 5" expecting upstream state, the executor runs "step 3 of 5" with raw files as input, the tool errors. Test_mint exposed this:

| Test | Picked artifact | Outcome | Why |
|---|---|---|---|
| **T01** | csv-parser registry hit | ✓ works | No generation, existing standalone tool |
| **T04** | SKILL `days-of-stock-threshold-evaluator` | ✓ works | SKILL backend is flexible (constrained LLM with sandbox_execute) |
| **T11** | MCP_TOOL `region_revenue_aggregator` (chain stage 3 of 4) | **✗ hard fail** | Expected `joined_records` parameter, got raw `sales.csv` paths |
| **T16** | AGENT `baseline-aware-metric-onset-detector` | ✓ works | AGENT backend is very flexible (full DeepAgent) |
| **T09** | MCP_TOOL `anomalies_output_dict_formatter` (last stage) | ✗ silent fail | Caught its own JSON parse error and returned `{"summary": "Failed to parse..."}` instead of raising |
| **T14** | MCP_TOOL `department_spend_aggregator` (last stage) | ✓ works | Happened to be self-sufficient |

Pattern: Phase 2 single-artifact execution works when (a) the task is simple, OR (b) the picked artifact is a SKILL/AGENT (flexible backends), OR (c) the picked MCP_TOOL happens to be the last stage AND happens to be standalone-runnable. It **fails** when an MCP_TOOL chain step is picked. ~25-33% of the testbed is at risk under this pattern.

**This is intentional broken intermediate state, not a bug.** The compositional plan explicitly notes this trade-off. The fix is the Phase 3 compositional executor which runs all N artifacts in dependency order via the workspace. Until Phase 3 lands, the Phase 2 codebase produces correct output for SKILL/AGENT/last-stage-MCP_TOOL tasks and broken output for intermediate-stage MCP_TOOL tasks. The Phase 3 fallback option in the original plan ("ship the eval on Phase 2") is **NOT VIABLE** because of this — see the Phase 3 fallback note below for the alternatives if HP4 doesn't solve.

**Commit:** `feat(phase2): per-gap classification, packaging-signal classifier, multi-artifact generation, CLI multi-cap support`

**Decision gate:** ✅ **Phase 2 is complete pending commit.** Per-gap classification produces the right shape in state, mixed-type classifications work for skill-tasks, AGENT-tasks collapse correctly, the CLI now displays the multi-artifact output and writes a per-run manifest. The intermediate-state breakage on T11/T09 is documented, expected, and resolved by Phase 3. **The full `test_mint.py` re-run is moved from Phase 2 verification to Phase 3 verification** — it can only fully pass after the compositional executor runs the chains end-to-end.

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

**Decision gate:** if Phase 3 works end-to-end for T14 and at least one mixed-type chain, proceed to **Phase 3.5** (context-engineering hardening + subagents + decomposer rule 10 fix). **If HP4 is not solvable in ~5 days, stop and reassess** — but see the "Fallback NOT viable" note below.

**Fallback option — NO LONGER VIABLE (discovered 2026-04-09 during Phase 2 verification):** the original plan said the fallback was "the codebase stays at the end of Phase 2 (per-gap classification, multi-artifact generation, priority-sort execution)". This fallback **does not work** for the testbed. Phase 2's `test_mint.py` run on 2026-04-09 exposed a real architectural incoherence: the new generator prompts frame each artifact as *"one step of a workflow"* expecting upstream state, but the priority-sort executor runs that artifact in isolation passing only the raw input files. The two are incompatible, with the following observed pattern across the 7 test_mint cases:

- **Works**: T01 (registry hit, no generation), T04 (priority sort picked a SKILL — flexible execution backend), T16 (priority sort picked an AGENT — very flexible backend), T14 (priority sort happened to pick the last-stage MCP_TOOL alphabetically)
- **Hard fail**: T11 (`region_revenue_aggregator` MCP_TOOL — chain stage 3 of 4 — errored with `missing 1 required positional argument: 'joined_records'` because the executor passed raw input files instead of upstream state)
- **Silent fail**: T09 (`anomalies_output_dict_formatter` MCP_TOOL — last stage — caught its own JSON parse error and returned `{"summary": "Failed to parse..."}`, so the pipeline reported success despite producing a degenerate output)

Estimated impact on the full 18-task testbed: ~25-33% of tasks would fail because they decompose into multi-step MCP_TOOL chains where priority sort picks an intermediate-stage tool that can't run standalone.

**This means Phase 3 is no longer optional — it is the only correct execution path for multi-step MCP_TOOL chains.** If HP4 (mixed-type workspace passing) does not solve in 5 days, the alternatives are:

1. **Revert today's generator prompt "one step of a workflow" framing** (~30 min, see `src/orchestrator/prompts/{mcp_tool,skill,agent}_generator.py`). Each generator goes back to "solve the entire task end-to-end". Per-gap classification metric is preserved (still classify all N gaps) but every generated artifact becomes a self-contained whole-task monolith. Cost: per-gap classification becomes architecturally meaningless because the classifications don't drive different generator behavior. Defeats the proposal's RQ1 intent.
2. **Single-artifact pre-selection with topological-tail picking** (~half day). Restructure the pipeline so priority sort happens BEFORE generation, picks the topological-tail gap (the one no other subtask depends on), and only generates that one artifact framed as "the whole task". Cheaper than option 1 and preserves per-gap classification, but bigger pipeline restructuring and still doesn't actually exercise multi-artifact generation.
3. **Accept the testbed limitation and ship the eval with ~25-30% MCP_TOOL chain-step task failures, documented as a Chapter 6 finding.** Defensible only if the failures are clean (`gate=True, output=False, type=mcp_tool`) and the failure attribution is to the architectural-incoherence finding rather than to a system bug.

The compositional plan's expectation that Phase 2 would "preserve the pilot pass/fail semantics while the compositional executor is being built" turned out to be optimistic — it works for SKILL/AGENT/last-stage-MCP_TOOL but breaks for intermediate-stage MCP_TOOL chain steps. This finding is captured in the Phase 2 commit message and motivates Phase 3 as load-bearing rather than payoff.

**Important: Phase 3 is intentionally scoped to the compositional executor only.** Three additions discussed during Phase 2 work — context-engineering Bucket A, DeepAgents subagents for AGENT execution, and decomposer rule 10 tightening — are split out into a separate Phase 3.5 below to keep Phase 3's risk bounded around HP4. Phase 3's commit ships when the compositional executor works for at least one mixed-type chain, regardless of whether Phase 3.5 is complete.

---

### Phase 3.5 — Context engineering, subagents, decomposer hardening (~2-3 days)

**Goal:** address the third proposal tension (context economy in a MAS where agents pass context to each other) at the generated-capability layer, plus tighten the decomposer's adaptive-investigation collapse rule. This phase exists because the Phase 2 pilot revealed three follow-on issues that the original execution plan didn't anticipate but that are load-bearing for the thesis's context-engineering story.

**Background — why this phase exists:**

During Phase 2 verification (T01/T04/T14/T16 pilot, 4/4 success), three observations led to this phase being scoped:

1. **T16 over-decomposition.** The decomposer produced 4 subtasks for the cascade investigation task even though `decomposition.py` rule 10 says "adaptive investigations stay as ONE capability". The classifier handled it correctly via the agent-task collapse rule (all 4 → agent), but the over-decomposition wastes classifier calls and signals that rule 10 needs tightening.

2. **Context-engineering audit gap.** A re-read of `docs/thesis_proposal.md` against the implementation revealed that the third tension — *"context economy in a MAS where agents pass context to each other"* — is the most under-exercised at the generated-capability layer. We have Write (Pydantic schemas), Select (ChromaDB hierarchical search + DeepAgents progressive disclosure for skills), Token-cost-as-optimiser-pressure (β·token_cost in the utility function), and orchestration-level Isolate (separate `create_deep_agent()` invocations per pipeline node). We are missing Compress entirely and we are missing generated-capability-level Isolate (subagents for AGENT execution). The thesis's domain literally is *"Context Engineering in LLM-Powered Multi-Agent Systems"* and the third tension is one of three headline tensions; closing these gaps moves the thesis from "MAS at the orchestration level only" to "MAS at all levels".

3. **Subagents are the canonical fix for AGENT context economy.** Per the [DeepAgents subagents documentation](https://docs.langchain.com/oss/python/deepagents/subagents.md), *"Subagents solve the context bloat problem... the main agent receives only the final result, not the dozens of tool calls that produced it."* Per the [LangChain multi-agent guide](https://docs.langchain.com/oss/python/langchain/multi-agent/index.md), subagents process *"67% fewer tokens than skills"* in multi-domain scenarios due to context isolation. CLAUDE.md previously listed compositional agents as Chapter 7 future work for "evaluation tractability" reasons; that objection turned out to be weaker than it sounded (subagent composition does not break any baseline ablation in the eval design — it just shifts the absolute headline number for the full system, which all four conditions see uniformly).

**Three sub-stages, in order:**

#### Stage 3.5a — Context engineering Bucket A (~half day)

Cheap context-engineering interventions for SKILL and MCP_TOOL. These are prompt and validator changes — no architectural restructuring.

- [ ] **MCP_TOOL Tier 1 token-budget check** in `src/verification/static_scanner.py`: emit MEDIUM finding when generated tool source exceeds ~1500 tokens. Does not hard-reject; contributes to the optimiser's β·token_cost penalty over time.
- [ ] **MCP_TOOL generator prompt discipline** in `src/orchestrator/prompts/mcp_tool_generator.py`: add a line to the system prompt instructing the generator to keep tool source under ~150 lines and to avoid long docstrings, redundant comments, or extensive type annotations beyond `Annotated[type, Field(description=...)]`. Brief rationale: the registry stores the source and other agents pay the token cost on every discovery.
- [ ] **MCP_TOOL `description` field length cap** in `src/orchestrator/generators/mcp_tool.py` `GeneratedToolCode`: add `max_length=200` to the `description` Pydantic field so the LLM can't produce a 200-word description.
- [ ] **SKILL body length check** in `tools/quick_validate.py`: warn when SKILL.md body exceeds ~500 lines (Anthropic's recommended ideal). Same shape as the existing prose checks (Tier 0 hard-fails).
- [ ] **SKILL generator prompt discipline** in `src/orchestrator/prompts/skill_generator.py`: instruct the generator to keep SKILL.md body under 500 lines and to move long examples or reference data into `references/<name>.md` files within the skill directory.
- [ ] **Cross-cutting registry description compression**: when registering a capability in ChromaDB (`src/registry/store.py`), compute a 50-word `embedding_summary` from the full description (truncate or LLM-summarise) and embed that, while keeping the full description for display. Saves embedding tokens and search-time injection costs.

**Verification:** unit suite passes. Spot-check a fresh pilot run on T04+T14 — generated tools should be under 1500 tokens of source, generated SKILL.md bodies should be under 500 lines.

**Commit:** `feat(phase3.5a): context-engineering Bucket A — token budgets and registry compression`

#### Stage 3.5b — DeepAgents subagents for AGENT execution (~1-2 days)

The architecturally significant change. When the decomposer produces a multi-step structure for an agent-task, the generator now produces ONE lead agent config with N subagent dicts, instead of N independent standalone agents. The compositional executor invokes the lead via `create_deep_agent(..., subagents=[...])`, and the lead uses its built-in `task` tool to delegate to subagents during the investigation.

- [ ] **Schema change** in `src/schemas/task.py`: add `subagents: list[dict] | None = None` to whatever holds `agent_config` (likely as a nested field within `GeneratedArtifact.agent_config` rather than a top-level field, to avoid breaking the existing `agent_config` shape). Add `CapabilitySubAgent` Pydantic model (name, description, system_prompt, tools, model — matching the DeepAgents `SubAgent` spec).
- [ ] **Generator change** in `src/orchestrator/generators/agent.py` + `src/orchestrator/prompts/agent_generator.py`: detect when the parent task is an agent-task with multiple subtasks (i.e. multiple gaps classified as agent in `state.classified_gaps`). In that case, generate ONE coordinated lead config + N subagent dicts in a single LLM call, instead of N independent agent configs. Generator prompt updated with a "lead-with-subagents" mode and a few-shot example.
- [ ] **Execution change** in `tests/evaluation/execution.py:_execute_agent_inner`: when `agent_config.get("subagents")` is non-empty, pass them to `create_deep_agent(..., subagents=[...])`. The DeepAgents library wires up the `task` tool automatically; the lead agent can then delegate to subagents during execution.
- [ ] **Compositional executor branch** in `src/orchestrator/composition.py` (the new module from Phase 3): when handling an agent-typed chain, route to the lead-with-subagents path instead of running standalone agents in sequence. For the agent-task case, the "chain" is just one lead agent invocation with subagents pre-bound.
- [ ] **Loading change** in `src/orchestrator/loading_node.py`: when registering a lead-with-subagents agent capability, the registry entry represents the whole composition (lead + subagents). Consider whether subagents should be separately searchable (probably no — they're internal to the lead).
- [ ] **Tests** in `tests/unit/test_agent_generator.py`: add multi-gap-agent-task case verifying the generator produces lead+subagents instead of N independent agents. In `tests/integration/test_pipeline_e2e.py` (or new file): end-to-end T16-style run, verify the lead agent's LangFuse trace contains subagent invocations via the `task` tool.

**Verification:**
- Unit suite passes
- `uv run scripts/run_pilot.py --tasks T16 --condition full --n-runs 1` — T16 should now produce 1 lead agent + N subagents in the generated_artifacts list
- LangFuse trace for T16 should show the lead's `task` tool calls invoking the subagents (not 4 standalone DeepAgent processes)
- Token cost per agent task should drop relative to the Phase 2 baseline (subagent context quarantine = ~67% fewer tokens per the multi-agent guide)

**Commit:** `feat(phase3.5b): DeepAgents subagents for compositional agent-task execution`

#### Stage 3.5c — Decomposer rule 10 tightening (~30 min)

Small fix. T16 over-decomposition in the Phase 2 pilot showed that rule 10 in `decomposition.py` ("adaptive investigations stay as ONE capability") doesn't always fire. Tighten the language so cascade-investigation-shape tasks reliably collapse to 1 subtask. With Stage 3.5b implemented this becomes less critical (over-decomposed agent-tasks get wrapped in a lead+subagents composition anyway), but it's still cheap and reduces wasted classifier calls on the over-decomposed input.

- [ ] **Strengthen rule 10** in `src/orchestrator/prompts/decomposition.py`: be more explicit and absolute. *"If the task uses any of the following words — investigate, diagnose, trace, triage, troubleshoot, examine, explore, form hypotheses, root cause, cascade — produce EXACTLY ONE subtask. Do not split the investigation into 'load data' + 'analyse data' + 'report findings'. The agent reads its own data as part of the investigation."* Add Example 4's reasoning text to be more emphatic about *why* over-decomposition is wrong.
- [ ] **Spot-check** with a single T16 run after the change. Should now produce 1 subtask, not 4.

**Commit:** `fix(decomposition): tighten rule 10 to reliably collapse adaptive investigations`

#### Phase 3.5 verification gate

- Full unit suite passes
- Pilot spot-check: `uv run scripts/run_pilot.py --tasks T01,T04,T14,T16,T11 --condition full --n-runs 1` — five tasks covering all three capability types and a registry-reuse case
- LangFuse spot-checks:
  - **T16**: 1 subtask (rule 10 fix), 1 lead agent + N subagents (Stage 3.5b)
  - **T04 / T14**: at least one SKILL classification per task, with the skill artifact under 500 lines (Stage 3.5a body length check)
  - **MCP_TOOL outputs**: source code under 1500 tokens (Stage 3.5a Tier 1 check)
- Token cost per task should be measurably lower than Phase 3 baseline for agent tasks (subagent quarantine) and comparable-or-lower for skill/tool tasks (Bucket A budgets)

**Decision gate:** if all three sub-stages land, proceed to Phase 4. Each sub-stage can be skipped independently if its budget overruns — Stage 3.5a is the most independent, Stage 3.5b is the most thesis-load-bearing, Stage 3.5c is a 30-minute fix that's rarely worth deferring.

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
- [ ] **D4 — Counterfactual hypothetical-budget analysis** (~1h):
  - **What it does**: takes the per-task token costs from the full eval (already populated by `backfill_token_costs.py`) and computes, for several hypothetical `PIPELINE_TOKEN_BUDGET` values (e.g., 50th / 75th / 90th / 95th percentile of observed token costs), how many tasks would have been aborted under each budget. Pure post-hoc analysis on existing eval data — no re-runs, no changes to the running system.
  - **Why it exists**: per-task hard token budget enforcement is explicitly deferred to Chapter 7 future work (item F2) because (a) the proposal doesn't ask for hard enforcement, only for token cost as a metric and an optimiser pressure (both already implemented), (b) setting a budget value without empirical data is guessing, and (c) hard budgets create generator-quality regression risk and pollute the eval failure-mode analysis. **But** the eval data is enough to do *counterfactual* budget analysis without ever enforcing one — and that gives Chapter 6 a measurement-based context-engineering story without paying the engineering cost or the failure-pollution cost.
  - **Implementation** (~30 min of code in `analyze_results.py`, ~30 min of writing in Chapter 6):
    - Add `_hypothetical_budget_analysis(records, percentiles=[50, 75, 90, 95])` helper to `analyze_results.py`
    - For each percentile, compute the corresponding token-count threshold across the 360-record eval (or per-condition if useful)
    - For each threshold, count: (i) tasks that would have aborted, (ii) of those, how many *also* failed the validator (so the budget would have caught a real problem early), (iii) of those, how many *succeeded* under the actual run (so the budget would have wrongly killed a working task)
    - Report as a table: `budget value | tasks aborted | aborted-and-correct | aborted-and-failed | precision (would-have-caught-a-real-problem rate)`
  - **What this gives Chapter 6**: a quantitative answer to "what would happen if we enforced a budget?" without ever enforcing one. The narrative becomes: *"We measured per-task token cost as proposal metric #4 and applied it as an optimiser pressure via the β·token_cost penalty. As an additional analysis, we computed counterfactually what would have happened if a hard `PIPELINE_TOKEN_BUDGET` had been enforced at several percentile thresholds. At the 90th-percentile budget, X tasks would have been aborted; of those, Y were tasks that also failed the validator (so the budget would have provided early-fail signal); Z were tasks that succeeded under the actual run (so the budget would have introduced false negatives). This analysis motivates Chapter 7 future work item F2 — hard budget enforcement is well-defined and tunable from this measurement, and is deferred from the current implementation because (a) the proposal calls for soft pressure rather than hard gates, and (b) the false-negative rate at intermediate percentiles suggests the soft β penalty is sufficient context-engineering pressure for the testbed's token-cost distribution."*
  - **Status**: pure analysis; no code in the running system, no eval re-run, no failure-mode pollution.
- [ ] `uv run scripts/analyze_results.py --csv` — full statistical analysis with all six proposal metrics
- [ ] `uv run scripts/analyze_context_utilization.py --csv` — context window metrics

**Gate to Phase 7:** all six proposal metrics are now produced. Ch 6 has its data.

---

### Phase 7 — Chapter drafting (no code, happens during thesis writing)

- [ ] **E1** — Ch 6 testbed description: replace "ML model training, inference" with "statistical analysis, anomaly detection, root-cause inference, reporting"
- [ ] **E2** — Ch 3 §3.5 + tech-stack table: drop Google ADK / A2A / TypeScript MCP / TOML / Claude-Code-as-runtime; add DeepAgents (harness-on-small-model strategy), DuckDB (⭐ metrics backbone), LangFuse (⭐ delivers token cost metric), three execution backends (⭐ classifier has runtime consequences)
- [ ] **E3** — Ch 4 Tier 1 framing: "Tier 1 scopes its checks to executable portions of each artifact (Python source for MCP_TOOL, code-fenced snippets for SKILL/AGENT); prose-level cheating is out of scope by design and caught by Tier 2/3"
- [ ] **E4** — Ch 3 / Ch 4 / Ch 5 / Ch 6 explicit Write/Select/Compress/Isolate mapping. The thesis's domain is *"Context Engineering in LLM-Powered Multi-Agent Systems"*; the four primitives from Martin (2025) and the ACE framework (Zhang et al. 2025) are cited in §2.2 of the proposal. Map our implementation to each primitive concretely:
  - **Write**: Pydantic state schemas (`src/schemas/`), SKILL.md files on disk, DeepAgent `StateBackend` workspace, optional `references/` for skills (deferred to Ch 7)
  - **Select**: ChromaDB hierarchical embeddings with `domain → capability → specific skill` levels, DeepAgents progressive disclosure for skills via `skills=` parameter, registry `detect_gaps()` above-threshold filtering
  - **Compress**: Phase 3.5a token budgets (MCP_TOOL ~1500 tokens, SKILL.md body ~500 lines), `description max_length=200` cap, registry 50-word `embedding_summary` for ChromaDB, optimiser β·token_cost penalty
  - **Isolate**: orchestration-level isolation via separate `create_deep_agent()` calls per pipeline node (decomposer / adversarial-prober) with their own `write_todos` and file scratchpads, **generated-capability-level isolation via DeepAgents subagents (Phase 3.5b)** so the AGENT type's lead agent receives only structured subagent return values rather than dozens of intermediate tool calls, sandbox isolation via `--network none` Docker for MCP_TOOL execution
- [ ] **E5** — Ch 4 §3.3 (skill generation) explicit defense of the SKILL.md-only choice. Cite the proposal's exact wording: *"a SKILL.md file with instructions, templates, and **optional** scripts that teach an agent how to perform a specialized workflow"* (line 81) and the Anthropic spec's *"at its simplest, a skill is a directory that contains a SKILL.md file"*. Note that bundled resources (`references/`, `scripts/`, `assets/`) are permitted by the spec but are not required by the proposal; defer to Ch 7 future work. Justify with the testbed observation: the skill-shaped tasks (T04, T11, T12, T14) all fit within Anthropic's recommended <500-line SKILL.md guideline once the Phase 3.5a body-length budget is enforced, so bundled resources would be optional richness rather than a structural necessity.
- [ ] **E6** — Ch 6 subagent context-economy reporting. After Phase 5 eval data is in, report: (a) absolute token costs for the 5 agent-targeted tasks (T08, T10, T13, T16, T17) under the Phase 3.5b lead-with-subagents implementation, (b) qualitative comparison against the LangChain multi-agent guide's *"67% fewer tokens than skills"* claim for similar context-isolation patterns, (c) per-task chain length and subagent invocation count from LangFuse traces. If schedule allows, run a counterfactual baseline with subagents disabled and report the delta — but this is nice-to-have, not load-bearing.
- [ ] **E7** — Ch 6 SKILL.md body size measurement. Aggregate the line count of every generated SKILL.md across the 360-record full eval. Plot a distribution. If any are over Anthropic's <500-line ideal, flag as a finding and motivate bundled resources for Ch 7. If all are well under 500, report this as evidence that the Phase 3.5a body-length budget is comfortable rather than constraining.
- [ ] **E8** — Ch 6 counterfactual hypothetical-budget context-engineering story. Built on Phase 6 D4. The Chapter 6 narrative paragraph should:
  - State that token cost is implemented as a metric (proposal eval metric #4) and as an optimiser pressure (β·token_cost in the utility function), per the proposal text
  - Report the empirical token-cost distribution per condition × capability_type from the full eval
  - Present the D4 hypothetical-budget table (50/75/90/95 percentile thresholds, tasks aborted, would-have-caught-a-real-problem precision)
  - Defend the choice not to enforce a hard budget by citing: (i) the proposal asks for soft pressure rather than hard gates, (ii) the false-negative rate at intermediate percentiles in the D4 analysis indicates the soft β penalty is sufficient context-engineering pressure for the testbed's actual cost distribution, (iii) hard budget enforcement creates a generator-quality regression risk (generators might produce worse output to fit) and a failure-mode analysis complication that the soft penalty avoids
  - Bridge to Chapter 7 future work item F2: *"the D4 measurements provide a calibrated starting point for hard budget enforcement, deferred to future work specifically because the implementation choice should be data-driven and the data was not available before the eval ran"*
  - This positions the deferral as a methodologically-justified design choice, not as a missing feature
- [ ] Cite B1.2, B2.1, B2.3 bypass findings as examples of iterative verification hardening
- [ ] Cite `compositional_synthesis_plan.md` as the Pillar 1 implementation path
- [ ] Cite `proposal_vs_implementation.md` as the audit reference

**New Ch 7 future work items** (in addition to those already listed in `docs/thesis_proposal.md` Chapter 7):

- [ ] **F1** — Bundled resources for skills (`references/`, `scripts/`, `assets/`). The Anthropic Agent Skills spec's third level of progressive disclosure. Becomes valuable when SKILL.md bodies routinely exceed Anthropic's <500-line guideline, which the Phase 6 measurement (E7) will quantify. Implementation cost: ~half a day for the generator + ~1 day for the SKILL execution backend to load bundled files on demand.
- [ ] **F2** — Per-task hard token budget enforcement. Currently the system uses whatever tokens it needs and the optimiser's β·token_cost penalty applies *post hoc*. A hard pre-execution cap (`PIPELINE_TOKEN_BUDGET`) would abort the pipeline with a clear error if the projected cost exceeds the cap, providing context-engineering pressure at the strongest possible point. **Deferred from Phase 3.5** for four reasons: (1) the proposal asks for token cost as a metric and an optimiser signal, not as a hard gate — soft enforcement (β·token_cost penalty) is the proposal's prescribed mechanism and is already implemented; (2) setting a budget value without empirical data would be guessing, and the eval token-cost data is needed first to pick the value intelligently; (3) hard budgets create a generator-quality regression risk (generators might produce worse output to fit) which the soft β penalty avoids; (4) failures from a hard budget pollute the eval analysis with `budget_exceeded` failure modes that complicate attribution. **Bridge to the deferral**: Phase 6 item D4 (counterfactual hypothetical-budget analysis) and Phase 7 item E8 (Ch 6 narrative paragraph) provide the measurement-based defence of the deferral and supply the calibrated starting point for any future implementation. The data this future work needs to set its budget value will be in the Chapter 6 D4 table.
- [ ] **F3** — Within-body progressive disclosure for the SKILL execution backend. Currently the SKILL backend prepends the full SKILL.md body to system_prompt every invocation. Anthropic's level-2 progressive disclosure pattern would load body sections on demand via a `read_skill_section(section_name)` tool. Most valuable when SKILL.md bodies grow large (paired with F1).
- [ ] **F4** — Counterfactual subagent ablation. Run the eval with subagents disabled (forcing N standalone agents in the chain via the Phase 3 compositional executor) and compare against the Phase 3.5b lead-with-subagents implementation. Reports the actual context-economy delta on the testbed rather than relying on the LangChain multi-agent guide's general "67%" figure.
- [ ] **F5** — `mint export` / `mint import` for user-editable generated code. (Originally Step 6 of the now-archived `docs/archive/plan_cli_upgrade.md`.) **`mint export`** writes a generated capability's source code to a local file (Python source for MCP_TOOL, SKILL.md for SKILL, agent config JSON for AGENT) by reading from `registry.get(name).metadata["source_code"]`. **`mint import`** registers a local file as a capability, replacing the generated version: creates a `GeneratedArtifact`, runs it through `verify()`, then `registry.add()` with `metadata={"source": "user-edited", "imported_from": path}`. **Why it matters**: the generate → fail → rephrase → regenerate loop is slow (~2-5 min per attempt). Export → edit → import lets users fix one-line bugs in seconds and enables a hybrid workflow where JIT generates the scaffold and the user polishes the logic. **Out of scope for the thesis deliverable** — this is a user-experience feature for the CLI that doesn't move any of the proposal's six metrics; valuable for post-thesis users of the MINT CLI. Implementation cost: ~half a day. Must handle all three capability types.
- [ ] **F6** — `mint run --improve` for proactive prompt improvement. (Originally Step 9 of the now-archived `docs/archive/plan_cli_upgrade.md`.) Pre-pipeline LLM call that rewrites the user's task into a more specific, actionable version *before* decomposition runs. Input: user task + file structure summaries from `_describe_file()` + parameter list. System prompt: *"You are a task specification assistant. Rewrite the user's vague task into a precise, actionable specification using the file structure information. Keep the original intent."*. User confirmation flow with `Y / n / edit` (open in `$EDITOR`). **Why it matters**: the gap between "what the user typed" and "what the pipeline needs" is the single biggest source of failures. Catches the underspecified-prompt failure mode at the front of the pipeline rather than reactively via `mint retry --hint`. **Out of scope for the thesis evaluation specifically** — using `--improve` during the eval would alter the controlled testbed prompts in `tests/evaluation/tasks.py`, breaking the unit-of-measurement assumption. **Out of scope for the thesis deliverable in general** — the proposal's context-engineering claims are about *inter-agent* context (Pillar 1 §3.2 "Structured State Communication") not *user input* prompt rewriting. Implementation cost: ~1 day. Could become a separate post-thesis UX experiment.

---

## What to watch

**Risks worth tracking during the work:**

1. **HP4 (mixed-type workspace passing)** — the biggest technical unknown. Surface it early in Phase 3; don't leave it until the end.
2. **Test rewrite volume in Phase 2** — estimate is 40-60 assertions across `test_gap_detection`, `test_classifier`, `test_pipeline`. (Resolved in Phase 2: actual rewrite volume was ~9 assertions, well under estimate.)
3. **Phase 3 timeline slippage** — if Phase 3 is not done in 5 working days, stop and use the Phase 2 fallback. Do not let Phase 3 consume the eval deadline.
4. **Optimizer coupling after Stage 1** — removing fake invocation records changes what `prune()` sees. Verify in Phase 1 that prune correctly identifies unused capabilities without false positives.
5. **SKILL `network_disconnect` Tier 3 finding (T11, stale data)** — seen once in a pre-fix run, likely a prober false positive on a skill without source code. Watch for it in Phase 4 smoke tests; if it reproduces, investigate whether Tier 3 should skip SKILLs without source code.
6. **Phase 3.5b subagent generator complexity** — the generator now has two modes (single-agent vs lead-with-subagents) and has to decide which to produce based on classified_gaps content. The mode-selection logic is the new sharp edge; if it gets wrong (produces lead+subagents when single-agent would suffice, or vice versa) the eval numbers shift in ways that require generator-prompt iteration. Mitigate by adding a clear few-shot example for both modes and unit tests covering both branches.
7. **Phase 3.5a token-budget false positives** — the SKILL.md body length check (>500 lines warns) and the MCP_TOOL source token budget (>1500 tokens emits MEDIUM finding) might fire on legitimate generations during the eval and inflate the optimiser's penalty. Watch the Phase 4 smoke test for unexpectedly-high MEDIUM finding rates from these new checks; if rates are elevated, soften the budgets.

**Metrics worth monitoring at each phase gate:**

- Unit test count (baseline 887, post-Phase-1: 938, post-Phase-2: 939)
- Full-suite runtime (should not degrade meaningfully)
- LangFuse trace sanity on 1-2 representative tasks per phase
- After Phase 3.5b: token cost per agent task (should drop from Phase 3 baseline due to subagent context quarantine)
- After Phase 3.5a: SKILL.md body line counts (should stay under 500 ideally) and MCP_TOOL source token counts (should stay under 1500 ideally)

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
| Phase 3 — compositional executor (Stage 3a only) | 3-5 days (10 days worst case) | ~$0.50 (pilot) | **high — HP4 is the unknown** |
| Phase 3.5 — context engineering + subagents + decomposer rule 10 | 2-3 days | ~$0.30 (pilot) | medium (Stage 3.5b is the new sharp edge) |
| Phase 4 — pre-eval smoke tests | 2 hours | ~$1 | low |
| Phase 5 — full eval + longitudinal | ~15 hours wall | ~$5 | low (just waiting) |
| Phase 6 — post-eval aggregations + mock audit | ~7 hours | ~$0 | low |
| Phase 7 — chapter drafting | — | $0 | — |

**Total pre-eval engineering: 9-15 working days (was 7-12 before Phase 3.5 was added). Total pre-eval compute: ~$2.30. Total eval compute: ~$5. Total post-eval: ~$0.**

The dominant risk is Phase 3 timeline. The dominant cost is the eval itself. Phase 3.5 adds 2-3 days of engineering and ~$0.30 of compute on top of the original plan; the addition is justified by directly addressing the proposal's third tension (context economy in a MAS where agents pass context to each other) at the generated-capability level — this was previously listed as Chapter 7 future work but the Phase 2 retrospective concluded it should be promoted into the implementation because it is the only un-implemented thing that touches the thesis's domain-level concern.
