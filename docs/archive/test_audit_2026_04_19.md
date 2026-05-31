# Test Suite Audit — 2026-04-19

**Step 13.5 Parts A + B executed as a single deep-read audit across the entire `tests/` tree.**

**Scope:** 1,578 tests across 56 files, ~33,170 LOC. Nine parallel audit passes, each agent reading every test in its batch and cross-referencing against current `src/`, `scripts/`, `tools/`, `sandbox/`.

**Source of truth for per-test verdicts:** the nine batch files under `docs/test_audit_2026_04_19/batch_{01..07}_*.md`. This file is the executive synthesis.

---

## Executive summary

| Verdict | Count | % | Definition |
|---|---:|---:|---|
| **KEEP** | 1,513 | 95.9% | Meaningful, asserts current behavior, non-redundant |
| **DEAD** | **0** | 0.0% | Imports broken, feature removed, or testing deleted code |
| **STALE** | **0** | 0.0% | Asserts old behavior that source has since changed (the §12 finding #13 "tests-codifying-bugs" pattern) |
| **WEAK** | 33 | 2.1% | Vacuous assertions / low-signal / could be tightened |
| **DUPLICATE** | 14 | 0.9% | Redundant coverage pairs |
| **SUSPICIOUS** | 11 | 0.7% | Not clearly broken but worth a human look |

**Headline:** Of 1,578 tests, **zero are dead and zero codify since-fixed bugs.** For a codebase that's had ~30 substantive landings in the last two weeks (§12 findings #10–#18, Steps 12.5 / 13 / 13.7 / 13.8 / 14.6–14.12 / 18.5–18.10), this is a strikingly clean result.

**Step 13.5 Part A (TODO/FIXME/skip scan):** ✅ CLEAN. Only hits in `tests/` are:
- Scanner/validator fixture strings (testing that Tier 1 catches `# TODO: implement` in *generated* code — content-under-test, not test-TODO).
- 2 environmental `pytest.skip()` calls in `test_adversarial.py` (DEEPSEEK_API_KEY + Docker image availability).

**Step 13.5 Part B (necessity audit):** ✅ CLEAN with minor cosmetic fixups. No tests need deletion. ~58 tests flagged for optional polish.

---

## Per-batch summary

| Batch | Area | Tests | KEEP | DEAD | STALE | WEAK | DUPL | SUSP | Report |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 01 | Pillar 1 Generators | 189 | 181 | 0 | 0 | 1 | 6 | 1 | [batch_01](test_audit_2026_04_19/batch_01_generators.md) |
| 02a | Pillar 1 Orchestration | 151 | 147 | 0 | 0 | 3 | 0 | 1 | [batch_02a](test_audit_2026_04_19/batch_02a_orchestration.md) |
| 02b | Composition / Contract / Loading | 142 | 139 | 0 | 0 | 3 | 0 | 0 | [batch_02b](test_audit_2026_04_19/batch_02b_composition.md) |
| 03a | Pillar 2 Tier 1 / 1.5 + Gate + Sandbox | 183 | 171 | 0 | 0 | 6 | 4 | 2 | [batch_03a](test_audit_2026_04_19/batch_03a_verification_tier1.md) |
| 03b | Pillar 2 Tier 2 / 3 + Verification Node | 120 | 117 | 0 | 0 | 1 | 1 | 0 | [batch_03b](test_audit_2026_04_19/batch_03b_verification_tier23.md) |
| 04 | Pillar 3 Optimizer | 148 | 135 | 0 | 0 | 9 | 1 | 3 | [batch_04](test_audit_2026_04_19/batch_04_optimizer.md) |
| 05 | Registry + Metrics + Schemas + Taxonomy | 251 | 240 | 0 | 0 | 8 | 0 | 2 | [batch_05](test_audit_2026_04_19/batch_05_registry_schemas.md) |
| 06 | CLI + Runner + Execution + Tracing | 155 | 146 | 0 | 0 | 0 | 2 | 2 | [batch_06](test_audit_2026_04_19/batch_06_cli_execution.md) |
| 07 | Harness + Analyzers + Eval Tasks | 239 | 237 | 0 | 0 | 2 | 0 | 0 | [batch_07](test_audit_2026_04_19/batch_07_harness_analyzers.md) |
| **Total** | — | **1,578** | **1,513** | **0** | **0** | **33** | **14** | **11** | — |

---

## Critical findings (surfaced beyond per-test verdicts)

### 1. Tier 1.5 `allow_network` production gap (batch 3a)

**This is a production behavior gap, not just a test gap.**

`src/verification/gate.py::verify()` threads `allow_network` into `behavioral.run(allow_network=...)` and `adversarial.probe(allow_network=...)`, but passes NOTHING to `import_check.run_import_check(artifact)`. The Tier 1.5 `_check_mcp_tool` path hardcodes offline semantics via `sandbox_execute(..., allow_network=False)`.

**Consequence:** an MCP_TOOL generated with `needs_network=True` + `allow_network=True` at the task level will be allowed to hit the network at runtime, but its Tier 1.5 import probe will always run offline. Any cap whose import itself requires network connectivity (e.g., `from openai import OpenAI` with API initialization at import time) would fail Tier 1.5 even under a correct task policy.

- **Severity:** MEDIUM — no current eval task declares `needs_network=True` so the gap is latent.
- **Test coverage:** zero tests assert either behavior (offline or forwarded).
- **Fix:** ~15 min. Thread `allow_network` through `run_import_check` + add one unit test each for offline-default and forwarded-true.

### 2. Step 18.5 analyzer helpers have ZERO direct tests (batch 7)

`scripts/analyze_results.py` defines:
- `_gini_coefficient()` — the M3 formula
- `print_gini_summary()` — the M3 report renderer
- `print_classification_accuracy()` — the Pillar 1 RQ #1 proxy report renderer

**None of these have a test anywhere in `tests/`.** Verified via grep — zero hits.

The Step 18.5 commit (`ad4908e`) DID land "+10 tests" as MEMORY.md claims, but they cover the DB infrastructure (`test_evaluation_store.py` +6 tests for `invocation_events` persistence, `test_metrics.py` +4 tests for `fetch_invocations_for_task`) — not the Python functions that compute Gini or cross-tabulate classification accuracy.

**Consequence:** if the Gini formula has a subtle bug (off-by-one, sort-order dependence, zero-handling), Chapter 6 numbers are silently wrong.

- **Severity:** MEDIUM — analyzers are load-bearing for three proposal metrics (M3, Pillar 1 RQ #1 proxy, Core-Work Classification Accuracy).
- **Fix:** ~30 min. Add 5–8 unit tests covering known-value Gini inputs + empty-table behavior + classification accuracy cross-tab shape.

### 3. `TaskDefinition.expected_type` field untested (batch 7)

Step 18.5 added `expected_type: Literal["mcp_tool", "skill", "agent"]` to `TaskDefinition` via `object.__setattr__` on 20 tasks (8 MCP_TOOL / 6 SKILL / 6 AGENT). **No test asserts this field is populated correctly.**

- **Severity:** LOW — drift risk only, not a correctness bug.
- **Fix:** ~5 min. One test: iterate `ALL_TASKS`, assert every task has `expected_type` set + the 20-task distribution matches the claim (8/6/6).

### 4. Stale module docstring (batch 3b — not a test)

`tests/unit/test_verification_node.py` line 6 says *"Fail-open behaviour at MAX_ATTEMPTS"* — but the tests themselves correctly assert **fail-closed** per §12 finding #14. The module docstring pre-dates the §12 #14 landing and was never updated.

- **Severity:** LOW — documentation-only.
- **Fix:** ~1 min. One-line docstring update.

### 5. Stale env var name (batch 3b — conditional-skip)

`tests/unit/test_adversarial.py:870` uses `pytest.skip("DEEPSEEK_API_KEY not set")`. The project standardized on `OPENAI_API_KEY` in `.env`.

- **Severity:** LOW — the test skips cleanly either way; no functional impact. Adversarial agent actually runs against OpenAI now.
- **Fix:** ~1 min. Rename skip marker.

---

## Coverage gaps (not tests-to-delete, but tests-to-consider-adding)

| Area | What's untested | Impact | Effort |
|---|---|---|---|
| `test_schemas.py` — 15+ post-B1 schema fields | `is_generic`, `definition_token_cost`, `pip_packages`, sticky flags (`decomposition_succeeded`, `classification_succeeded`, `verification_succeeded`), `output_contract`, `allow_network`, `failed_step_reason`, `step_timings`, `output_files`, `needs_network` | Drift risk only (fields covered functionally elsewhere) | ~40 LOC (~30 min) |
| `tools/quick_validate.py` — 5 production branches | allowed-frontmatter-keys, 64-char name limit, 1024-char description limit, 500-char compatibility limit, angle-bracket rejection | Tier 0 gate strictness invariants | ~15 min |
| `src/verification/gate.py::_verify_inner` | Tier 2 and Tier 3 exception paths in the fail-open handler (only Tier 1 path is covered) | Fail-open contract correctness under deeper errors | ~15 min |
| `test_evaluation_tasks.py` — T19 + T20 | No validator tests for the last two tasks (18 of 20 are covered) | Chapter 6 reproducibility for those tasks | ~20 min |
| `cycle_flush.flush_task_cycle_state` (Step 18.8 refactor) | No dedicated test; only indirect via `test_tracing.py` | Regression risk for longitudinal + shift runners | ~15 min |
| `_make_probe_sandbox_execute` + `execute_mcp_tool_in_chain` | No direct unit tests; covered indirectly via composition/cli_allow_network | Lower priority — integration-path regression would surface via downstream failures | ~30 min each |
| Tier 3 bridge-mode integration smoke | Unit layer asserts flag forwarding; no end-to-end Docker test that `--network bridge` re-enables HTTP | Chapter 4.5 disclosure on network plumbing depth | ~30 min (requires Docker) |

**Total optional coverage additions: ~2–3 hours.** None blocks Phase 4 eval.

---

## Recommended actions (Step 13.5 Part B closure)

### Immediate — cosmetic fixups (~10 min total, land in one commit)

| File | Line | Change |
|---|---|---|
| `tests/unit/test_verification_node.py` | module docstring | "Fail-open" → "Fail-closed" per §12 finding #14 |
| `tests/unit/test_adversarial.py` | pytest.skip | `DEEPSEEK_API_KEY` → `OPENAI_API_KEY` |
| `tests/unit/test_static_scanner.py::test_exactly_500_lines_no_warning` | `range(499)` | Change to `range(500)` (name says 500 lines) |
| `tests/unit/test_evaluation_tasks.py::TestT04Validator::test_cost_too_far_off` | hardcoded `product_count=10` | Update to match current `_EV["T04"]` |
| `tests/unit/test_optimizer_simulation.py` | `test_merge_candidate_metrics_failure_does_not_crash` docstring | Clarify that merge reads from cache, not try/except |

### High-value — close one genuine production gap (~30 min)

| Area | Action |
|---|---|
| Tier 1.5 `allow_network` forwarding | Thread `allow_network` through `run_import_check()` + `_check_mcp_tool()`; add 2 tests (offline default + bridge-mode forwarded). Production fix + test coverage in one commit. |

### High-value — close analyzer test gap (~30 min)

| Area | Action |
|---|---|
| Step 18.5 helpers | Add 5–8 unit tests for `_gini_coefficient` (known-value inputs, perfect equality, perfect inequality, empty, single value), `print_gini_summary` (table shape), `print_classification_accuracy` (3×3 confusion matrix correctness). New file: `tests/unit/test_analyze_results_gini.py` OR extend existing `test_analyze_results.py`. |

### Optional — consolidate duplicates (~1 h)

| Batch | Count | Action |
|---|---:|---|
| 01 Generators | 6 | Merge near-duplicates in `TestSkipOnRetry` / `TestParallelGeneration` + forced_name/path-unsafe pairs |
| 03a Tier 1 | 4 | Merge `test_missing_external_is_medium` with its `_offline` variant |
| 04 Optimizer | 1 | Parametrize `plot_results` tests into one |
| 06 CLI | 2 | Evaluate whether soft duplicates add unique signal |

**Total consolidation savings: ~13 tests removed, same coverage.** Suite size 1,578 → ~1,565.

### Investigate — 11 SUSPICIOUS items (~30 min to triage)

- **batch 01:** 1 item — regex drift between generator files (source-side, not test bug)
- **batch 02a:** 1 item — `test_classify_gap_unhandled_exception_propagates` documents unreachable behavior
- **batch 03a:** 2 items — `test_exactly_500_lines_no_warning` arithmetic; `test_extra_packages_triggers_install_phase` weak length check
- **batch 04:** 3 items — hardcoded ChromaDB collection names (fragile); two plot-label fragility points
- **batch 05:** 2 items — `SubTaskItem` / `DecompositionResult` have only rejection tests, no happy-path construction
- **batch 06:** 2 items — `test_fixture_does_not_clobber_skill_with_same_name` dict ordering; `test_model_allows_extra_keys` extra=allow on AGENT output_schema (Chapter 4 disclosure candidate)

Each needs 2–5 min of read-and-decide. Mostly no action required.

---

## Cross-cutting patterns observed

### Positive signals (why the suite is this clean)

1. **Every post-2026-04-16 landing has dedicated regression tests for the NEW behavior.** §12 findings #10–#18 all have named tests asserting the fix. No codified-bug fossils.
2. **Defensive regression guards have audit-trail docstrings.** `test_reset_deletes_last_run_at_correct_path` + `test_multi_file_writers_run_in_parallel_off_original` + `test_init_creates_parent_directory_if_missing` all document the bug they were written against with commit/date.
3. **Intentional-redundancy clusters are labelled.** T04 prose-with-mock-word regression guard exists at 3 layers (scanner, quick_validate, gate) — correctly NOT flagged duplicate because each layer regresses independently.
4. **Proxy framings are self-documenting in test code.** Core-Work Classification Accuracy proxy, allow_network precedence, fail-closed contract all have explanatory docstrings in tests.

### Mild friction points

1. **Two Python identifier naming conventions.** `agent.py` accepts leading underscore in names (`_SNAKE_CASE_RE = r"^[a-z_][a-z0-9_]*$"`), `mcp_tool.py` rejects it (`r"^[a-z][a-z0-9_]*$"`). Both have tests asserting the per-file rule — but the inconsistency is source-side. Consider unifying.
2. **Two KL-divergence implementations** (`loop._compute_kl_divergence` point-mass vs `simulation._kl_divergence` histogram). Each correctly tested in isolation, but future readers should know this is deliberate.
3. **Two "same category, slightly different semantics" test pairs** that aren't duplicates but read like them — the kind of thing a future cleanup session might mistakenly merge. Worth a cross-ref comment.

### Zero findings of

- **Tests for deleted modules** — every import in every audited test resolves to current `src/`.
- **Tests for removed CLI subcommands** — the CLI has grown, not shrunk.
- **Tests for archived scripts** — `scripts/migrate_business_domain_labels.py`, `step10_merge_candidates.py`, `test_cli_settings.py` (deleted 2026-04-19) have zero test dependencies.

---

## Verdict

**Test suite is in remarkably good shape for Phase 4.** The audit surfaced:
- **0 deletions required**
- **5 cosmetic fixups** (~10 min)
- **1 production gap worth closing** (~30 min — Tier 1.5 allow_network)
- **1 coverage gap worth closing** (~30 min — Step 18.5 analyzer helpers)
- **13 duplicate consolidations** (~1 h, optional)
- **11 suspicious items** (~30 min triage, mostly no-action)

**Total Step 13.5 Part B effort to green: ~1 hour hands-on for cosmetic fixups + the two gap closures.** The 1-hour consolidation pass is nice-to-have.

Phase 3 smoke is unblocked once the cosmetic fixups land. The Step 18.5 analyzer gap is a Chapter 6 defensibility concern — fixing it before the 400-record eval gives confidence that the reported Gini / classification accuracy numbers are what they claim to be.

---

**Methodology:** 9 parallel audit agents, each reading every test in a thematic batch and cross-referencing `src/` for current behavior. ~30 minutes wall-clock, ~8 minutes per batch. Full per-test verdict tables in the nine `docs/test_audit_2026_04_19/batch_*.md` files.

**Total tests audited: 1,578. Zero DEAD. Zero STALE. 96% KEEP by default.**
