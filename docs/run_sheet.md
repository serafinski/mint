# MINT — Execution Run Sheet

At-a-glance command sequence for the thesis evaluation pipeline (Phase 3 smoke → Phase 4 eval → Phase 4/5 analysis).

**Source of truth:** `docs/MASTER_PLAN_2026_04_13.md` §6.1. This file is the distilled run sheet — if anything here disagrees with the master plan, the plan wins.

**Last updated:** 2026-04-19 (after Step 13.6 Parts A+B+C+D + gap closure (registry FN audit) + nice-to-have #1 (Tier 2 probe breakdown)).

---

## Prerequisites (check before starting)

| Prereq | Why |
|---|---|
| Docker running | Phases 3 smoke and 4 eval (sandboxed execution + Tier 2/3 verification) |
| `.env` populated | `OPENAI_API_KEY` + `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` + `LANGFUSE_HOST` |
| Live internet | `test_allow_network.py` hits `api.github.com/zen`; skips cleanly if offline |
| `uv` installed, repo checked out | All commands run via `uv run …` from repo root |
| **Step 13.5 test hygiene pass done** | Master plan blocker for Phase 3 smoke — do this first (~2–3 h) |

---

## Phase 3 — Smoke (~55 min hands-on, ~$0.50–1)

Run sequentially; each gate must pass before the next. If any fails, **stop** and diagnose.

| # | Command | Why | Time |
|---|---|---|---:|
| 1 | `uv run pytest tests/unit/ -q` | Hermetic baseline — 1547 unit tests. Must be 100% pass. | ~3 min |
| 2 | `uv run scripts/test_pipeline.py` | Pillar 1 pipeline e2e (decompose → classify → generate → verify → register), real LLM | ~5 min |
| 3 | `uv run scripts/test_verification.py` | Pillar 2 three-tier gate (Tier 1 / 1.5 / 2 / 3) on known-good + known-bad, real Docker + real adversarial | ~3 min |
| 4 | `uv run scripts/test_allow_network.py` | Pillar 2 Tier 2 network-disconnect differential; `--network bridge` vs `--network none` Docker plumbing (Step 14.12) | ~2 min |
| 5 | `uv run scripts/test_auto_optimizer.py` | Pillar 3 (prune/split/merge) wired through pipeline; atomic-rollback invariant | ~90 s |
| 6 | `uv run scripts/test_mint.py` | Full CLI e2e across all three backends + all supported file formats | ~10 min |
| 7 | `uv run scripts/run_pilot.py --all-tasks --condition full --n-runs 1` | 20-task pilot (1 run each). Last gate before committing ~$6 / 18 h to the 400-record run | ~30 min |

---

## Phase 4 — Data generation (unattended, parallelizable)

Each row writes to a **different** DuckDB; all three can run concurrently in separate terminals. Wall-clock lower bound if parallel: **~18 h** (dominated by row 8).

| # | Command | Why | Time / $ |
|---|---|---|---:|
| 8 | `uv run scripts/run_pilot.py --all-tasks --all-conditions --n-runs 5` | **THE eval.** 20 tasks × 4 conditions × 5 runs = 400 records → `data/eval/metrics.duckdb` | ~18 h / ~$6 |
| 9 | `uv run scripts/run_longitudinal.py` | **Pillar 3 RQ #2** — 40 sequential tasks, shared registry, no per-run reset → `data/eval/longitudinal.duckdb` | ~6–10 h |
| 10 | `uv run scripts/run_longitudinal_shift.py --phase-a-tasks T01,T02,T03,T04,T05 --phase-b-tasks T11,T12,T13,T14,T15 --shift-at 20 --total 40` | **Pillar 3 RQ #3** — two-phase with distribution shift → `data/eval/longitudinal_shifted.duckdb` | ~6–10 h |

---

## Phase 4/5 — Analysis (rows 11 + 16 MUST precede row 17; rows 12–15 are independent)

Row 11 backfills M4 `token_cost` → consumed by row 17. Row 16 populates `human_label` → consumed by row 17 for M2. Both are blocking. Rows 12, 13, 14, 15 produce standalone reports and can run any time post-eval — in parallel with row 16's human audit if you want to save wall-clock.

| # | Command | Why (proposal metric) | Time |
|---|---|---|---:|
| 11 | `uv run scripts/backfill_token_costs.py --write` | **M4** — populates `evaluation_runs.token_cost` from LangFuse GENERATION observations | ~5 min |
| 12 | `uv run scripts/backfill_definition_token_costs.py --write` | **Pillar 3 U(c) token-cost input** — per-capability definition-string tokens (proposal §Pillar 3 four-metric table, line 170). Distinct from row 11's M4 (total per-task). Idempotent. | ~1 min |
| 13 | `uv run scripts/analyze_context_utilization.py` | **T3 naive-verbose half** — peak input-token % of 128k context window per agent type | ~1 min |
| 14 | `uv run scripts/analyze_compression_loss.py` | **T3 aggressive-compression half** — 9-bucket failure taxonomy + most-dropped-keys table | ~1 min |
| 15 | `uv run scripts/analyze_tier2_probes.py --environment default` | **Chapter 4.3 methodology evidence** — per-Tier-2-probe-type detection breakdown. Reads `verification.tier_2.{pass,reject}` LangFuse events, groups by `metadata.categories`, reports per-category hit counts + per-capability-type distribution. Answers "of the N Tier 2 rejections, which probe types caught them?" — proposal §Pillar 2 line 133 grounding. | ~10 s |
| 16 | `uv run scripts/audit_contamination.py` | **M2** — manual contamination audit, stratified random sample (default 90). **Interactive — human labels each sampled capability as clean / contaminated / partial.** Resumable. | **~2–4 h HUMAN** |
| 17 | `uv run scripts/analyze_results.py` | **THE Chapter 6 analyzer** — M1 (Fisher + Holm + Clopper-Pearson CI) + M3 Gini of invocations + M4 total token cost + M5 latency + M2 contamination + Core-Work Classification Accuracy *(Pillar 1 RQ #1 proxy — task-level primary-type match; see Step 18.5 disclosure)* + failure breakdown | ~2 min |
| 18 | `uv run scripts/analyze_longitudinal.py` | **M6 + Pillar 3 RQ #1/#2** — utility-score KL convergence + cycles-to-converge + seeded-vs-evolved Gini | ~1 min |
| 19 | `uv run scripts/analyze_longitudinal.py --db data/eval/longitudinal_shifted.duckdb` | **Pillar 3 RQ #3** — three-tier adaptation verdict (graceful / sluggish / collapse) | ~1 min |

**Tip:** Row 17 (`analyze_results.py`) can be run earlier for a headline preview — the M2 section falls back to *"audit not performed"* when `human_label` is empty. The FINAL Chapter-6-ready pass must come after rows 11 + 16.

---

## Diagnostic scripts (run only when something breaks)

Not on the main path. Call these to debug a specific regression.

| When you see … | Run this | What it tells you |
|---|---|---|
| `test_mint.py` fails mysteriously | `uv run scripts/test_execution_backends.py` | Isolates SKILL/AGENT runtime from generation (~30 s) |
| Prune misbehaving | `uv run scripts/test_prune.py` | 2-second real-ChromaDB prune mechanics smoke |
| Pillar 3 split/merge regression | `uv run scripts/test_split_merge.py` | 9/9 matrix (MCP_TOOL × SKILL × AGENT × {split, merge}) with real LLM (~5–10 min) |
| `mint feedback --wrong` flow broken | `uv run scripts/test_feedback_split.py` | In-memory demo of §12 finding #6b: feedback → `get_recent_failures` → split consumer |
| Chapter 3 needs hierarchical-embeddings FP evidence | `uv run scripts/test_registry_matching.py` | **False-positive** audit + description/implementation self-consistency (cite in §3.1/§3.2) |
| Chapter 3 needs hierarchical-embeddings FN evidence | `uv run scripts/test_registry_fn.py` | **False-negative** audit — symmetric to the above; paraphrased-query recall. ✅ shipped 2026-04-19 (closes proposal §Pillar 1 line 77 gap) |
| Writing Chapter 4 §14.6 appendix | `uv run scripts/validate_contract_extractor.py` | 20/20 extractor agreement check against authored T-task contracts |

---

## Totals

| Phase | Hands-on | Unattended | $ |
|---|---:|---:|---:|
| Phase 3 smoke | ~55 min | — | ~$0.50–1 |
| Phase 4 data gen | kickoff only | ~18 h (parallel) | ~$6 |
| Phase 4/5 analysis | ~2–4 h (row 15 human audit) | trivial | $0 |
| **Total** | **~4–6 h** | **~18 h** | **~$7** |

---

## Outstanding pre-eval work (per master plan TBD)

- **Step 13.5** — Test suite hygiene pass (~2–3 h). Blocks Phase 3 smoke.
- That's it. Step 13.6 (scripts hygiene + Part D schedule update), Step 13.7 (LangFuse audit), and all analyzer scripts are closed as of 2026-04-19.

---

## Mental model (one-liner)

**Smoke** proves the plumbing. **Eval** generates the numbers. **Backfills + audit** populate the DuckDB columns. **Analyzers** turn columns into Chapter 6 tables. Diagnostics are triage tools, not on the critical path.

---

## Proposal → Run-sheet row coverage

Cross-reference against `docs/thesis_proposal.md`. Every proposal-asked deliverable has a numbered row that produces it.

### §Evaluation Metrics (lines 220–229)

| # | Proposal metric | Row producing it |
|---|---|---|
| M1 | End-to-end task success rate | Row 8 generates data → row 17 reports (Fisher + Holm + Clopper-Pearson CI) |
| M2 | Mock contamination rate (manual audit) | Row 16 (HUMAN) → row 17 `print_contamination_rate` |
| M3 | Capability reuse distribution (Gini of invocations) | Row 17 `print_gini_summary` |
| M4 | Token cost per task (total, incl. definitions + generation + verification + execution) | Row 11 backfill → row 17 `print_token_cost_summary` |
| M5 | Time to first correct execution | Row 17 `print_latency_summary` (Step 18.9 narrowed filter) |
| M6 | Registry convergence (KL divergence) | Row 18 (utility-score KL per Step 18.7) |

### §Key Research Questions

| Pillar / RQ | Proposal line | Row producing it |
|---|---|---|
| Pillar 1 RQ #1 — classification accuracy | line 96 | Row 17 Core-Work Classification Accuracy *(proxy, task-level match)* |
| Pillar 1 RQ #2 — JIT vs human-designed success | line 97 | Row 17 Fisher test: each JIT condition vs `static` baseline |
| Pillar 1 RQ #3 — latency of generation + verification | line 98 | Row 17 M5 latency |
| Pillar 1 line 77 — hierarchical embeddings prevent **FP** | line 77 | `test_registry_matching.py` (diagnostic) |
| Pillar 1 line 77 — hierarchical embeddings prevent **FN** | line 77 | `test_registry_fn.py` (diagnostic, ✅ shipped 2026-04-19) |
| Pillar 3 RQ #1 — evolutionary vs human distribution | line 195 | Row 18 seeded-vs-evolved Gini comparison |
| Pillar 3 RQ #2 — cycles to converge | line 196 | Row 18 cycles-to-convergence on utility-score KL |
| Pillar 3 RQ #3 — stability under distribution shift | line 197 | Row 19 three-tier adaptation verdict |

### §Bootstrapping three tensions (lines 15–19)

| Tension | Row |
|---|---|
| T1 — Autonomy vs correctness | Implicit via entire Pillar 2 pipeline — defensibility at row 3 (`test_verification.py`) + row 4 (`test_allow_network.py`) + row 15 (`analyze_tier2_probes.py`) + M2 at row 16 |
| T2 — Specificity vs generality | Pillar 3 at rows 5 + 18 + 19; hierarchical-embeddings FP+FN claims via `test_registry_matching.py` + `test_registry_fn.py` (diagnostic, both shipped) |
| T3 — Context economy (two failure modes) | Row 13 naive-verbose half + row 14 aggressive-compression half |

### §Pillar 2 failure-mode taxonomy (lines 106–111)

| Failure mode | Row catching it |
|---|---|
| Hardcoded responses (line 108) | Row 3 Tier 1 static scanner + row 16 M2 audit |
| Silent fallbacks (line 109) | Row 4 Tier 2 network-disconnect differential + row 15 per-probe breakdown |
| Mock contamination (line 110) | Row 3 Tier 1 unittest.mock detection + row 16 M2 audit |
| Graceful-looking degradation (line 111) | Row 3 Tier 3 adversarial ground-truth comparison |

### §Pillar 3 four-metric table (lines 164–171) — operational inputs to U(c), not eval-time outputs

| Four-metric entry | Where it's measured |
|---|---|
| Reuse rate | Runtime: `MetricsStore.reuse_rate` populated by every invocation |
| Success rate | Runtime: `MetricsStore.success_rate` populated by every invocation + `mint feedback --wrong` |
| Token cost (definition-string size) | Row 12 (`backfill_definition_token_costs.py`, Step 18.6) — **this is distinct from M4** |
| Maintenance burden | Runtime: `maintenance_count` field, incremented on split/merge/regenerate events |

### §Baselines (lines 213–218)

All four (`static`, `no_verification`, `no_optimization`, `full`) exercised in a single invocation at **row 8** via `--all-conditions`. Each produces 20 × 5 = 100 records; 400 records total.
