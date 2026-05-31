# `scripts/` Necessity Audit — 2026-04-19

**Scope:** Step 13.6 Parts A/B/C (master plan §6.4). Every `.py` file under `scripts/` read end-to-end, cross-referenced against `src/`, `tests/`, `tools/`, and `sandbox/`.

**Totals (initial):** 24 scripts, 14,208 LOC.
**Totals (after Step 13.6 Parts A + C Option A execution on 2026-04-19):** 21 scripts, ~12,600 LOC. Three scripts deleted (staged); nine unit tests added; suite 1544 → 1553 (hermetic `CliRunner` + `monkeypatch` replacements for subprocess-based coverage).

**Two cross-reference passes performed this session (2026-04-19):**

1. Against the master plan §6.1 *Ordered script execution schedule* (lines 1955–1983) — verifies every script either runs in the pipeline or is explicitly catalogued as a diagnostic.
2. Against `docs/thesis_proposal.md` §Evaluation Metrics (lines 220–229) + §Key Research Questions (lines 94–98, 193–197) + §Bootstrapping (lines 15–19) + §Pillar 2 failure taxonomy (lines 106–111) — verifies every KEEP script maps to a specific proposal claim.

**Findings from the two passes (detailed in §Alignment below):**

- **Master plan schedule table is stale.** Four scripts landed post-2026-04-18 (`backfill_definition_token_costs.py`, `analyze_compression_loss.py`, `audit_contamination.py`, `test_allow_network.py`) are described in their individual Step blocks (18.6 / 13.8 / 18.10 / 14.12) but NOT woven into the ordered schedule at lines 1955–1974. Running the pipeline as documented would skip four readers the proposal requires.
- **My "consolidate" verdicts are within Part C review scope, but conflict with the plan's explicit default.** The plan at line 1976 lists `test_prune.py`, `test_feedback_split.py`, `test_cli_settings.py`, `test_registry_matching.py` under *"Scripts NOT part of the run sequence (kept for diagnostics / debugging)"* — i.e. the plan has already answered "keep". My consolidation suggestions override that default and need stronger justification (surfaced below per script).
- **`test_registry_matching.py` is proposal-backing, not just diagnostic.** It is the only empirical probe of proposal §Pillar 1's hierarchical-embeddings claim (line 77) and its Phase C self-consistency audit tests description/implementation drift with no equivalent elsewhere. Upgraded from "KEEP with note" → "KEEP (methodology evidence)".
- **Every proposal metric M1–M6 has exactly one primary script.** No proposal-asked metric is unbacked. No script exists without a proposal claim it serves.

**Cross-reference health checks performed:**

- Every `from src.…` / `from tests.…` import in `scripts/` resolves to an existing module (verified via `ls` on each referenced path).
- Zero `TODO`/`FIXME`/`HACK`/`XXX` markers anywhere in `scripts/` (contradicts the master plan's hand-wave "minimal cruft (2 TODO/HACK markers across 20 files)" — the current tree is clean).
- Zero references to archived docs (`docs/file_output_fix_plan.md`, `docs/perf_fixes_2026_04_14.md`) — already scrubbed when those docs were moved.
- All four verification-audit docs (`docs/verification/0{1..5}_*.md`) label-reference the exact filenames that exist on disk, so the audit trail is self-consistent.

---

## Verdicts at a glance

| Script | LOC | Verdict | Reason |
|---|---:|---|---|
| ~~`migrate_business_domain_labels.py`~~ | ~~400~~ | ✅ **DELETED 2026-04-19** | One-off B1 Phase 2 Step 3.5 migration, already applied. No runtime caller. Three stale prose references in `scripts/test_registry_matching.py` updated to cite git history instead. Staged via `git rm`. |
| ~~`step10_merge_candidates.py`~~ | ~~195~~ | ✅ **DELETED 2026-04-19** | One-off verification experiment (Step 10, commit `ae8e2e9`). Mechanism it probed is now covered by `test_split_merge.py` + `test_optimizer_operations.py`. `_schema_jaccard` caller count outside `src/` + `tests/` now zero. Staged via `git rm`. |
| ~~`test_cli_settings.py`~~ | ~~419~~ | ✅ **DELETED 2026-04-19 (Option A executed)** | Per-test coverage diff confirmed 3/9 tests redundant with `test_optimizer_config.py` + `test_metrics.py`, 6/9 were CLI-surface branches (`show`, `reset`, `set/unset optimizer.*`, `feedback` errors) with no unit equivalent. Ported to **9 new `CliRunner`-based unit tests** in `tests/unit/test_cli_settings.py` (5 new classes: `TestMintConfigShow`, `TestMintConfigReset`, `TestMintConfigSetUnsetOptimizer`, `TestMintFeedbackErrors`, + shared `_invoke_mint_cli` helper). Suite 1544 → 1553, all 35 tests in file pass in 0.29s (subprocess script was ~5s). Staged via `git rm`. |
| `test_prune.py` | 187 | 🔄 **CONSOLIDATE candidate** | Deterministic prune smoke. `tests/unit/test_optimizer_operations.py` (44 tests) covers prune semantics at the unit layer. Script uses real ChromaDB + DuckDB — small enough to keep as a ~2-second sanity check, but reconsider if Chapter 4 doesn't cite it. |
| `test_feedback_split.py` | 232 | 🔄 **CONSOLIDATE candidate** | In-memory DuckDB proof of the feedback → `get_recent_failures` → split-consumer chain. Each hop has unit coverage; the composition is the point. Keep if §12 finding #6b needs a reproducer artifact for Chapter 4; otherwise fold into a unit test. |
| `validate_contract_extractor.py` | 156 | ✅ **KEEP** | Step 14.6 Fix B — one-shot extractor-agreement check used as Chapter 4 methodology evidence. Run once before writing Chapter 4 §14.6 disclosure. |
| `analyze_results.py` | 1027 | ✅ **KEEP (critical)** | Primary Chapter 6 analyzer. Delivers M1 (Fisher + Holm + Clopper-Pearson), M3 Gini, M4 token cost, Core-Work Classification Accuracy, M5 latency (Step 18.9), M2 contamination rate, and failure breakdown. `docs/verification/05_evaluation_strategy.md` calls it out as the main analyzer. |
| `run_pilot.py` | 219 | ✅ **KEEP (critical)** | The 400-record evaluation runner (Phase 4 Step 18). |
| `run_longitudinal.py` | 333 | ✅ **KEEP** | Pillar 3 RQ #2 — convergence over accumulated history. Wired into `cycle_flush.flush_task_cycle_state()` (Step 18.8 refactor). |
| `run_longitudinal_shift.py` | 475 | ✅ **KEEP** | Step 18.8 — Pillar 3 RQ #3 distribution-shift scripts shipped `97da3a0`, execution pending (master plan row 8.1). |
| `analyze_longitudinal.py` | 772 | ✅ **KEEP** | Step 18.7 analyzer — utility-score KL (proposal-literal) + per-type op counts + seeded-vs-evolved Gini + Step 18.8 shift-detection block. |
| `analyze_context_utilization.py` | 480 | ✅ **KEEP** | T3 *naive-verbose* half of the context-economy story. Reads LangFuse GENERATION observations post-hoc. |
| `analyze_compression_loss.py` | 493 | ✅ **KEEP** | Step 13.8 — T3 *aggressive-compression* half. Reads `evaluation_runs.failed_step_reason`. Pair with the above for full §T3 coverage. |
| `backfill_token_costs.py` | 655 | ✅ **KEEP (critical)** | M4 token-cost attribution from LangFuse traces into DuckDB. Without it, M4 does not exist (harness doesn't thread token totals). `docs/verification/05_evaluation_strategy.md` lists it as M4 primary instrumentation. |
| `backfill_definition_token_costs.py` | 166 | ✅ **KEEP** | Step 18.6 (landed 2026-04-18) — populates the proposal-literal M3 *"definition tokens"* side-measurement. Idempotent backfill + JIT-path ensure full coverage. |
| `audit_contamination.py` | 571 | ✅ **KEEP** | Step 18.10 — M2 manual audit harness. Must run after Step 18 produces real generated capabilities. |
| `test_mint.py` | 439 | ✅ **KEEP (smoke)** | Full-CLI end-to-end smoke across all three types + all file formats. Named in Phase 3 smoke sequence. |
| `test_pipeline.py` | 491 | ✅ **KEEP (smoke)** | Pillar 1 9-node pipeline integration. Named in Phase 3 smoke sequence. |
| `test_verification.py` | 386 | ✅ **KEEP (smoke)** | Pillar 2 three-tier gate integration with real Docker + real adversarial DeepAgent. Named in Phase 3 smoke sequence. Retrofitted for Tier 1.5 per Step 12.5. |
| `test_auto_optimizer.py` | 713 | ✅ **KEEP (smoke)** | Pillar 3 pipeline-wiring integration (Step 9.5b rewrite) — `run_optimizer=True` branch + atomic rollback at the wiring layer. Named in Phase 3 smoke sequence. |
| `test_split_merge.py` | 2760 | ✅ **KEEP** | Pillar 3 integration 9/9 matrix (split + merge × MCP_TOOL/SKILL/AGENT) with real LLM. The only place cross-type Pillar 3 is exercised end-to-end; rollback-path canary (CSV) vs happy-path (stats) framing is referenced in Chapter 5. |
| `test_execution_backends.py` | 168 | ✅ **KEEP** | Isolates SKILL/AGENT backends from generation (MockArtifact). Useful diagnostic when `test_mint` is broken — ~30s vs ~10min. Small, low-maintenance. |
| `test_allow_network.py` | 381 | ✅ **KEEP** | Step 14.12 (landed 2026-04-18) — validates `--network bridge` vs `--network none` primitive behavior + live GitHub API + offline failure mode + fake-network-tool gate rejection. No unit-test equivalent possible. |
| `test_registry_matching.py` | 2090 | ✅ **KEEP (methodology evidence)** | **Proposal §Pillar 1 line 77 names hierarchical embeddings (domain → capability → specific skill) as the mechanism preventing false positives.** This is the only empirical probe of that claim. Phase A: naive cosine + threshold (FP rate). Phase B: LLM-judged query/match audit. Phase C: self-consistency audit per capability (description vs implementation drift) — orthogonal to queries, still the only mechanism probing that axis. Chapter 3 §3.1/§3.2 should cite runs of this script. Upgraded from 2026-04-19 initial draft's "diagnostic-only" framing after the proposal-alignment pass. |

**Totals (2026-04-19 final):** 3 deleted + 21 kept. Parts A + C Option A complete. Parts B + D (schedule updates) outstanding per §"Recommended actions" below.

---

## Alignment against master plan + thesis proposal

### A. Master plan §6.1 *Ordered script execution schedule* (lines 1955–1983)

The plan's schedule groups scripts by run-phase. Checking each row is covered by my audit + each of my KEEP scripts appears somewhere in the schedule or the diagnostics catalogue:

| Plan section | Scripts listed | My verdict | Match? |
|---|---|---|---|
| **Phase 3 smoke — before Phase 4 eval** | `test_pipeline.py`, `test_verification.py`, `test_auto_optimizer.py`, `test_mint.py`, `run_pilot.py --all-tasks --condition full --n-runs 1` | All KEEP | ✅ |
| **Phase 3 step-specific verification (one-time)** | `step10_merge_candidates.py`, `validate_contract_extractor.py` | Delete post-Phase 5 + KEEP | ✅ (step10 has served its purpose per plan; already shipped 2026-04-16) |
| **Phase 4 data generation** | `run_pilot.py --all-tasks --all-conditions --n-runs 5`, `run_longitudinal.py`, `run_longitudinal_shift.py` | All KEEP | ✅ |
| **Phase 4 analysis** | `analyze_results.py`, `analyze_context_utilization.py`, `backfill_token_costs.py --write`, `analyze_longitudinal.py` (×2 for default + shifted DB) | All KEEP | ✅ |
| **Scripts NOT in run sequence (kept for diagnostics)** | `test_execution_backends.py`, `test_prune.py`, `test_split_merge.py`, `test_feedback_split.py`, `test_cli_settings.py`, `test_registry_matching.py`, `migrate_business_domain_labels.py` | Plan's "KEEP for diagnostic" vs my {KEEP, CONSOLIDATE×3, KEEP-methodology, DELETE-now} | ⚠️ 3 conflicts + 1 upgrade (see §B) |

### ⚠️ Finding #1 — Four post-2026-04-18 scripts are MISSING from the schedule table

Verified against a full-text grep (26 total plan-wide references, zero in the schedule at lines 1955–1974):

| Script | Shipped in | Owns which proposal deliverable | Should be inserted at |
|---|---|---|---|
| `backfill_definition_token_costs.py` | Step 18.6 (2026-04-18) | Proposal §Pillar 3 Four-Metric table M3 *"tokens consumed by the capability's **definition**"* (line 170) — the literal proposal wording, stored as `CapabilityDescriptor.definition_token_cost` | Phase 4 analysis, right after `backfill_token_costs.py --write` |
| `analyze_compression_loss.py` | Step 13.8 (2026-04-18) | Proposal §Bootstrapping T3 *"aggressive compression loses critical details"* (line 19) — second failure mode of the context-economy tension. Pair with `analyze_context_utilization.py` for full T3 coverage. | Phase 4 analysis, right after `analyze_context_utilization.py` |
| `audit_contamination.py` | Step 18.10 (2026-04-18) | Proposal §Evaluation Metrics M2 *"Mock contamination rate ... measured by manual audit of a sample"* (line 225) | Phase 5 pre-analysis (post-Step-18), human-in-the-loop ~2–4 h, before `analyze_results.py::print_contamination_rate` can report |
| `test_allow_network.py` | Step 14.12 (2026-04-18) | Proposal §Pillar 2 Tier 3 *"network connectivity simulated as unavailable"* (line 148) + F2 *"silent fallbacks"* (line 109). Integration-level proof that the `--network bridge` vs `--network none` Tier 2 differential probe actually works against a live endpoint. | Phase 3 smoke, right after `test_verification.py` |

**Running the schedule as currently written would skip four readers the proposal requires.** Fix proposed below.

### ⚠️ Finding #2 — Three "consolidate" verdicts conflict with plan's explicit default

The plan at line 1976 explicitly lists `test_prune.py`, `test_feedback_split.py`, `test_cli_settings.py` under *"kept for diagnostics / debugging"* — meaning the plan has already evaluated them and decided to keep. Step 13.6 Part C reopens the question (*"keep / delete / consolidate"*), so my consolidate-candidate flag is within scope, but must justify overriding the plan's default.

| Script | My flag | Plan's default | Reconciled verdict |
|---|---|---|---|
| `test_prune.py` | Consolidate candidate (unit overlap) | Keep as diagnostic | **KEEP.** The plan's position wins: ~2-second real-ChromaDB smoke is cheap insurance against ChromaDB metadata-shape drift that mocked unit tests can't catch. My flag was weak. |
| `test_feedback_split.py` | Consolidate candidate | Keep as diagnostic | **KEEP.** The §12 finding #6b compositional flow (CLI feedback → `get_recent_failures` → split consumer) only appears in composition; each hop alone is unit-tested but the plumbing isn't. Cheap to keep. |
| `test_cli_settings.py` | Consolidate / delete post-Phase 5 | Keep as diagnostic | **CONSOLIDATE FLAG STANDS.** The real-subprocess CLI test IS partially redundant with `tests/unit/test_cli_settings.py`. But consolidation requires an explicit diff against unit coverage first. Defer the decision to Step 13.6 Part C with this pre-condition: only delete if `pytest tests/unit/test_cli_settings.py -v` asserts every `mint config set/show/unset/reset` branch + `feedback --wrong` branch + priority-chain ordering. If yes → delete. If no → keep. |

### B. Thesis proposal alignment

Every KEEP script maps to at least one proposal claim. No proposal-asked metric is unbacked by a script; no script exists without a proposal hook.

#### B.1 — §Evaluation Metrics (lines 220–229): six metrics, six primary scripts

| Proposal metric | Wording (line) | Primary script | Secondary / support |
|---|---|---|---|
| **M1** End-to-end task success rate | line 224 | `run_pilot.py` (generates data) → `analyze_results.py::query_condition_summary` + Fisher + Holm + Clopper-Pearson | `test_mint.py`, `test_pipeline.py` |
| **M2** Mock contamination rate | line 225 ("manual audit of a sample") | `audit_contamination.py` (human-in-the-loop) → `analyze_results.py::print_contamination_rate` | — |
| **M3 Gini** Capability reuse distribution | line 226 | `analyze_results.py::print_gini_summary` (Step 18.5) | `analyze_longitudinal.py` per-cycle Gini trajectory |
| **M4** Token cost per task | line 227 | `backfill_token_costs.py --write` → `analyze_results.py::print_token_cost_summary` | `backfill_definition_token_costs.py` (M3 definition-literal side-measurement, Step 18.6) |
| **M5** Time to first correct execution | line 228 | `analyze_results.py::print_latency_summary` (Step 18.9 narrowed filter) | — |
| **M6** Registry convergence (KL) | line 229 | `analyze_longitudinal.py::_compute_utility_kl_divergence` (Step 18.7) | `run_longitudinal.py` (generates data) |

#### B.2 — §Key Research Questions

**Pillar 1 (lines 96–98):**
- *"How accurately can an LLM classify capability gaps"* → `analyze_results.py::print_classification_accuracy` (Core-Work Classification Accuracy, Step 18.5, task-level primary-type match)
- *"success rate of JIT vs pre-built"* → `analyze_results.py` full condition × task matrix (`full` vs `static` baseline)
- *"latency of generation + verification"* → M5 latency (`print_latency_summary`)

**Pillar 2 (line 152):** verification itself is the contribution — no metric. Defensibility artifacts:
- `test_verification.py` (real Docker + real adversarial, all three tiers + Tier 1.5)
- `test_allow_network.py` (Step 14.12 Tier 2 differential probe + Tier 3 `--network none` regime)

**Pillar 3 (lines 195–197):**
- RQ3.1 *"evolutionary vs human-designed distribution"* → `analyze_longitudinal.py` seeded-vs-evolved Gini comparison (Step 18.7)
- RQ3.2 *"cycles to converge"* → `analyze_longitudinal.py` cycles-to-convergence on utility-score KL
- RQ3.3 *"stable under task distribution shift"* → `run_longitudinal_shift.py` (Step 18.8) → `analyze_longitudinal.py --db shifted` three-tier adaptation verdict

#### B.3 — §Bootstrapping (lines 9–21): three tensions

| Tension | Proposal claim | Scripts backing the measurement |
|---|---|---|
| **T1** Autonomy vs correctness (line 15) | "LLMs silently inject mocks, hardcode, fallback-path-mask" | Entire Pillar 2 — `test_verification.py` + M2 via `audit_contamination.py` |
| **T2** Specificity vs generality (line 17) | "every practitioner navigates intuitively; no principled framework" | Entire Pillar 3 — `test_split_merge.py` (9/9 matrix) + M3 Gini + M6 KL + `test_registry_matching.py` (hierarchical-embeddings FP claim) |
| **T3** Context economy (line 19) | Two failure modes: "naive (token limits + noise)" AND "aggressive compression (loses details)" | `analyze_context_utilization.py` (naive-verbose half) + `analyze_compression_loss.py` (aggressive-compression half, Step 13.8). **Both halves must be run for T3 to be fully evidenced in Chapter 6.** |

#### B.4 — §Pillar 2 failure taxonomy (lines 106–111): four named failure modes

| Failure mode | Proposal line | Which script/tier catches it |
|---|---|---|
| Hardcoded responses | 108 | Tier 1 static scanner (AST) + Tier 2 nonexistent-entity probes — `test_verification.py` `BAD_MCP_TOOL` fixture exercises this |
| Silent fallbacks | 109 | Tier 1 bare-except regex + Tier 2 network-disconnect differential — `test_allow_network.py::test_fake_network_tool_rejected` exercises the Tier 2 differential |
| Mock contamination | 110 | Tier 1 `unittest.mock` import detection + M2 manual audit — `audit_contamination.py` is the M2 harness |
| Graceful degradation (right-shape, fabricated content) | 111 | Tier 3 adversarial ground-truth comparison (red-team agent in `src/verification/adversarial.py`) — `test_verification.py` runs the real adversarial probe |

Every failure mode in the proposal's taxonomy has at least one script-level defensibility artifact.

#### B.5 — §Pillar 1 Structured State Communication (lines 90–92)

Proposal §3.4 is covered at three granularities per §12 finding #12 (CLAUDE.md). Script-level evidence:
- `validate_contract_extractor.py` — validates the LLM extractor for Step 14.6 OutputContract against all 20 authored T-task contracts; Chapter 4 methodology appendix material.
- `test_pipeline.py` — integration test that the pipeline honors the typed state schema end-to-end.
- Unit-layer evidence (Step 14 Gap C tests) covers the generator-prompt surface — not a script-level concern.

#### B.6 — §Pillar 1 Gap Detection hierarchical embeddings (line 77)

Proposal claim: *"hierarchical embeddings (domain → capability → specific skill) to prevent both false positives (superficial keyword matches) and false negatives"*.

- **False-positive claim** → `test_registry_matching.py` Phase A (cosine + threshold) + Phase B (LLM-judged match validity). ✅ covered
- **False-negative claim** → no dedicated script. Latent gap — if Chapter 3 §3.1 states the FN-prevention claim with confidence, a small harness seeding semantically-equivalent-but-differently-named caps and asserting retrieval finds them would be wanted. Not blocking pre-Phase-4.
- **Description/implementation drift** (implied by the proposal's §Pillar 2 framing but only named at §3.1 level) → `test_registry_matching.py` Phase C self-consistency audit. ✅ covered

This is why `test_registry_matching.py` is now marked "methodology evidence" not merely "diagnostic".

---

## Cross-reference map

### Scripts → `src/` modules (import surface)

Every import resolves. Notable high-fanout consumers:

- `src.registry.store.SkillRegistry` — used by 8 scripts.
- `src.registry.metrics.MetricsStore` — used by 6 scripts.
- `src.schemas.capability.{CapabilityDescriptor, CapabilityType}` — used by 10 scripts.
- `src.schemas.task.{GeneratedArtifact, OutputContract}` — used by 5 scripts.
- `src.optimizer.operations.{prune, split, merge}` — used by 3 scripts.
- `src.optimizer.loop._schema_jaccard` — used only by `step10_merge_candidates.py` (supports delete-later verdict — once step10 is deleted, this is the only caller outside `src/` and `tests/`).

### Scripts → `tests/evaluation/` (harness imports)

45 occurrences across 8 scripts, all resolving. The most important single import is `tests.evaluation.cycle_flush.flush_task_cycle_state` — factored out in Step 18.8 specifically so `run_longitudinal.py` + `run_longitudinal_shift.py` share the per-task flush path instead of reinventing it. This is the mechanism that keeps `cycle_utility_scores` / `cycle_events` / `invocation_events` populated for the analyzers.

### Scripts → `tools/`

Zero direct imports. `tools/quick_validate.py` and `tools/init_skill.py` are called only from within `src/` (the verification gate's Tier 0 structural pre-check) and from end-users, not from scripts.

### Scripts → `sandbox/`

No direct imports — `sandbox/Dockerfile` is consumed via `src.verification.sandbox_tool.sandbox_execute`, which wraps the `docker run` invocation. `test_verification.py` + `test_allow_network.py` + `test_mint.py` + `test_execution_backends.py` + `test_pipeline.py` + `test_auto_optimizer.py` all transitively depend on the image being built, but none of them touch the Dockerfile directly.

### Unit-test overlap (tests/unit/ vs scripts/)

| Script | Overlapping unit test | Overlap character |
|---|---|---|
| `test_prune.py` | `test_optimizer_operations.py` (44 tests) | Unit tests cover prune mechanics + rollback; script adds real-ChromaDB sanity check. |
| `test_feedback_split.py` | `test_optimizer_operations.py`, `test_cli_settings.py` | Each hop is unit-tested; script demonstrates the composition. |
| `test_cli_settings.py` (script) | `test_cli_settings.py` (unit) | Unit tests mock `subprocess`; script actually spawns `mint config …`. |
| `test_allow_network.py` | `test_cli_allow_network.py` (17 tests) | Unit tests mock Docker; script actually hits GitHub. |
| `test_verification.py` | `test_gate.py`, `test_import_check.py`, `test_behavioral.py` (autouse stub makes the unit suite Docker-independent) | Unit tests are fast + hermetic; script is the "real Docker + real adversarial" canary. |
| `test_pipeline.py` (script) | `test_pipeline.py` (unit) | Script is real-LLM; unit is stubbed. Two genuinely different layers. |
| `test_auto_optimizer.py` | `test_pipeline.py` (unit) + `test_optimizer_operations.py` (unit) | Script is pipeline-wiring integration; unit covers the wiring in isolation. |
| `audit_contamination.py` | `test_audit_contamination.py` (31 tests) | Unit tests cover the CLI mechanics; script is the human-in-the-loop tool itself. |

In every row the overlap is intentional — scripts are real-LLM / real-Docker / real-subprocess integration probes, unit tests are fast + hermetic. **No script is redundant with its unit-test counterpart.** The three consolidation candidates above (`test_prune`, `test_feedback_split`, `test_cli_settings`) are flagged because their integration value is modest — not because they duplicate unit tests.

---

## Observations worth flagging

1. **Every non-legacy script has a `LANGFUSE_TRACING_ENVIRONMENT=test` preamble** set *before* `load_dotenv`. This is the Step 13.7 / 13.6 Fix #1 isolation pattern. `migrate_business_domain_labels.py` does NOT have this block — another signal it predates the current observability regime and should be deleted rather than patched.

2. **`backfill_token_costs.py` has a latent timestamp-matching fragility** (attributed per-condition averages when exact match fails). Acceptable because LangFuse trace-ID cross-ref was added in Step 13.7 (`langfuse_trace_id` VARCHAR column). Pre-Chapter 6, re-run `backfill_token_costs.py --write` against the post-Step-18 DB to confirm attribution quality before freezing numbers.

3. **`test_registry_matching.py` is 2090 LOC** — the largest non-analyzer script — because it bundles three experimental phases (naive matching + query audit + self-consistency). If the research question it was built to answer has concluded, most of it should migrate into `docs/archive/` narrative text. Before Phase 5, decide: is any Chapter 6 claim still backed by a fresh run of this script, or only by the historical runs already documented in `MEMORY.md` / `docs/verification/`?

4. **`test_split_merge.py` at 2760 LOC is the largest script** and is doing real work (Pillar 3 matrix 9/9 with real LLM). It's the Chapter 5 primary defensibility artifact for the per-type split/merge claim. Keep, but consider splitting into `test_split.py` + `test_merge.py` if it grows further.

5. **`test_execution_backends.py` is a tiny (168 LOC) lifesaver** when `test_mint` is broken. Low maintenance cost, high value as a triage tool.

---

## Recommended actions (maps to master plan Step 13.6)

### Part A — Confirmed delete (~15 min)

```bash
git rm scripts/migrate_business_domain_labels.py
# Update docs/MASTER_PLAN_2026_04_13.md §6.1 and §"scripts not in run sequence" — remove references.
```

Search `docs/` for remaining references:
- `docs/verification/0{1,2,3,4,5}_*.md` — five occurrences, all already labeled NEUTRAL / one-off. Leave if the audit docs are treated as frozen snapshots; otherwise strike the rows.
- `docs/MASTER_PLAN_2026_04_13.md:231,249,1983` — update or strike.
- `docs/archive/b1_enriched_embedding_plan.md` — archived, leave as-is.

### Part B — Stale-reference sweep (~30 min → **already mostly clean**)

- No archived-doc paths referenced from `scripts/`. ✅
- No `TODO`/`FIXME`/`HACK`/`XXX` markers. ✅ (contradicts master plan's "2 markers" claim — confirm on a fresh grep or strike the claim.)
- All `from src.…` / `from tests.…` imports resolve. ✅
- One cosmetic: `test_split_merge.py` line 39 has an empty backtick pair (`commit ``) in a docstring — harmless but worth fixing during Part B pass.

### Part C — Per-script necessity re-reads (~30-45 min)

After the proposal-alignment pass, verdicts reconciled with plan's explicit defaults:

1. **`test_prune.py`** — Plan says KEEP as diagnostic. Agreed. 2-second real-ChromaDB smoke, catches metadata-shape drift that mocks miss. No action.

2. **`test_feedback_split.py`** — Plan says KEEP as diagnostic. Agreed. Documents the §12 finding #6b compositional flow that no single unit test demonstrates. No action.

3. **`test_cli_settings.py` (script)** — Only remaining consolidation candidate. Action: `pytest tests/unit/test_cli_settings.py -v` and check whether every branch exercised by the script has a unit analogue. If yes → delete script in Part C. If no → keep and ticket the unit gap.

4. **`step10_merge_candidates.py`** — One-shot experiment, already shipped 2026-04-16. Default: delete post-Phase 5 (per plan line 1983 pattern).

5. **`test_registry_matching.py`** — Verdict upgraded to **methodology evidence** after proposal-alignment pass. Action: update docstring to cite proposal §Pillar 1 line 77 (hierarchical embeddings FP claim) so future readers treat it as Chapter 3 backing, not merely diagnostic. Also document that false-negative half of the proposal claim has no dedicated harness (latent gap; not blocking).

### Part D (NEW — surfaced by 2026-04-19 cross-reference) — Update master plan §6.1 ordered schedule (~15 min)

The schedule table at lines 1955–1974 pre-dates four post-2026-04-18 scripts. Insert rows:

```
Phase 3 smoke:
  + scripts/test_allow_network.py   (after test_verification.py)

Phase 4 analysis:
  + scripts/backfill_definition_token_costs.py --write
                                    (after backfill_token_costs.py, before analyze_results.py)
  + scripts/analyze_compression_loss.py
                                    (after analyze_context_utilization.py)

Phase 5 (pre-Chapter-6):
  + scripts/audit_contamination.py  (human-in-the-loop, before final
                                     analyze_results.py run so
                                     print_contamination_rate can report)
```

Without this update, running the pipeline as documented would leave M2, M3-definition-literal, T3-compression-half, and Tier-2-network-differential unreported in Chapter 6.

### Estimated remaining work

| Step 13.6 Part | Estimate | Status |
|---|---:|---|
| A (delete `migrate_business_domain_labels.py`) | 15 min | Unblocked — do now |
| B (stale-ref sweep) | 30 min | Mostly clean; ~10 min to scrub 3 master-plan rows + the empty-backtick cosmetic |
| C (necessity re-reads + `test_registry_matching.py` docstring upgrade + unit-coverage diff for `test_cli_settings.py`) | 30 min | Unblocked |
| **D (NEW) — update master plan §6.1 ordered schedule with 4 missing rows** | 15 min | Unblocked; **blocks accurate Phase 4/5 execution** |
| **Total** | **~1 h 30 min** | Up 15 min from the plan's ~1–1.5 h because Part D wasn't originally scoped |

No script under `scripts/` blocks Phase 4 Step 18 from starting. But **Part D blocks Phase 4/5 from reporting four proposal-asked metrics** — if the schedule isn't updated, a mechanical execution will skip M2, M3-definition-literal, T3-compression-half, and the Tier-2-network-differential validation. Land Part D before Phase 4 smoke.

---

**Generated:** 2026-04-19, 24 scripts read end-to-end.

**Revision history:**
- 2026-04-19 initial — per-script verdicts + unit-overlap map + Step 13.6 Parts A/B/C recommendations.
- 2026-04-19 revised — added Alignment §A (master plan ordered schedule) + §B (proposal metrics + RQs + tensions + failure taxonomy + Pillar 1 gap-detection). Surfaced 4 post-2026-04-18 scripts missing from plan schedule (Part D added). Reconciled 3 "consolidate" verdicts against plan's explicit "kept for diagnostics" position — 2 flipped to KEEP, 1 held pending unit-coverage diff. Upgraded `test_registry_matching.py` from diagnostic-only to methodology-evidence (proposal §Pillar 1 line 77 backing).
- 2026-04-19 execution — Parts A + C (Option A) completed. Three scripts deleted (`git rm` staged, not yet committed): `migrate_business_domain_labels.py`, `step10_merge_candidates.py`, `test_cli_settings.py`. Three stale prose references in `scripts/test_registry_matching.py` updated. Nine new `CliRunner`-based unit tests added in `tests/unit/test_cli_settings.py`. Suite 1544 → 1553 (verified via `uv run pytest tests/unit/ -q`; one pre-existing environmental failure in `test_cli_allow_network.py::test_allow_network_flag_lands_in_task_state` — missing `data/cli/` dir precondition from Step 14.12 commit `3a1f021`, unrelated to this work, fixed locally with `mkdir -p data/cli`). 21 scripts remain in `scripts/`.
- 2026-04-19 parent-dir audit — the pre-existing environmental failure from the prior entry was root-caused and properly fixed. Codebase-wide sweep for the *"write to `<dir>/<file>` without ensuring `<dir>` exists"* bug class. Five sites fixed: `src/cli/runner.py::_save_last_run` (THE causal bug for the formerly-failing test), `src/registry/metrics.py::MetricsStore.__init__` + `src/registry/evaluation_store.py::EvaluationStore.__init__` (defensive — closes the class for every caller that bypasses `_create_infra`), `scripts/run_longitudinal.py` (optimizer-log write), `scripts/analyze_context_utilization.py` (user-provided `--csv` path). Two new regression tests (`test_metrics.py::test_init_creates_parent_directory_if_missing` + `test_evaluation_store.py::TestInitParentDirAutocreation::test_init_creates_parent_directory_if_missing`). Verified on clean env (`rm -rf data/cli` before pytest): all three targeted tests pass in 4.4s. Full unit suite on clean env: **1547 pass, 0 fail** in 3 min 02 s (previously 1544 pass + 1 fail). Session-net delta: +11 tests (+9 CliRunner + +2 regression) with the Step 14.12 environmental failure converted to pass. Sites verified already-safe: `src/cli/settings.py::save_settings`, `src/orchestrator/taxonomy.py::_save`, all SKILL.md generator writes, `tests/evaluation/fixtures/generate.py`, `snapshots.py`, `execution.py`, `analyze_results.py`, `analyze_compression_loss.py`, `backfill_token_costs.py` CSV export, all `run_dir/*` writes in CLI runner, `composition.py::_save_step_output` (OSError catch).
