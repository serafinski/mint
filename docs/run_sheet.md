# MINT — Execution Run Sheet

At-a-glance command sequence for the thesis evaluation pipeline (Phase 3 smoke → Phase 4 eval → Phase 4/5 analysis).

**Source of truth:** `docs/MASTER_PLAN_2026_04_13.md` §6.1. This file is the distilled run sheet — if anything here disagrees with the master plan, the plan wins.

**Last updated:** 2026-04-21 (after security audit ext12 — 10 P0/HIGH findings #114-#123 closed; #114 balanced-per-condition CTE fix, #115/#119/#122 watchdog + exit-code contracts, #116/#118 longitudinal completeness + zero-evidence guards, #117 run-sheet completeness filter below, #120 Context Economy fetch-degraded flag, #121 audit sample-plan scope-hash, #123 distribution-shift marker retry + analyzer refusal). Unit suite: **1,974 passing** (1,925 → 1,974; +49 ext12 regression + e2e tests).

---

## Prerequisites (check before starting)

| Prereq | Why |
|---|---|
| Docker running | Phases 3 smoke and 4 eval (sandboxed execution + Tier 2/3 verification) |
| `.env` populated | `OPENAI_API_KEY` + `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` + `LANGFUSE_HOST` |
| Live internet | `test_allow_network.py` hits `api.github.com/zen`; skips cleanly if offline |
| `uv` installed, repo checked out | All commands run via `uv run …` from repo root |
| ~~**Step 13.5 test hygiene pass done**~~ | ✅ **DONE 2026-04-19** — full 1,578-test audit + cleanup pass completed. 0 WEAK / 0 DUPLICATE / 0 SUSPICIOUS. No remaining pre-eval blocker. |
| ~~**Security audit ext12 (#114-#123) closed**~~ | ✅ **DONE 2026-04-21** — 10 P0/HIGH findings fixed: result-destroying bugs in rows 8/9/10/11/13/16/17/18/19 all closed. Only ext8 #61 (latent, Chapter-7) + ext8 #63 (partial — WARN + gate rollback) remain, both Chapter-4.5-disclosure, not eval-corrupting. |

---

## Phase 3 — Smoke (~57 min hands-on, ~$0.50–1)

Run sequentially; each gate must pass before the next. If any fails, **stop** and diagnose.

| # | Command | Why | Time |
|---|---|---|---:|
| 1 | `uv run pytest tests/unit/ -q` | Hermetic baseline — **1,974 unit tests** (post-ext12 +49 regression/e2e). Must be 100% pass. | ~4 min |
| 2 | `uv run scripts/test_pipeline.py` | Pillar 1 pipeline e2e (decompose → classify → generate → verify → register), real LLM | ~5 min |
| 3 | `uv run scripts/test_verification.py` | Pillar 2 three-tier gate (Tier 1 / 1.5 / 2 / 3) on known-good + known-bad, real Docker + real adversarial | ~3 min |
| 4 | `uv run scripts/test_allow_network.py` | Pillar 2 Tier 2 network-disconnect differential; `--network bridge` vs `--network none` Docker plumbing (Step 14.12) | ~2 min |
| 5 | `uv run scripts/test_auto_optimizer.py` | Pillar 3 (prune/split/merge) wired through pipeline; atomic-rollback invariant | ~90 s |
| 6 | `uv run scripts/test_mint.py` | Full CLI e2e across all three backends + all supported file formats | ~10 min |
| 6a | `uv run scripts/test_registry_matching.py` | **Chapter 3 hierarchical-embeddings FP evidence** — proposal §Pillar 1 line 77 "prevent false positives (superficial keyword matches)". Diagnostic-only prior to ext7; elevated to the main path so a Chapter 3 writer doesn't miss the claim's evidence. | ~1 min |
| 6b | `uv run scripts/test_registry_fn.py` | **Chapter 3 hierarchical-embeddings FN evidence** — proposal §Pillar 1 line 77 "prevent ... false negatives (semantically equivalent but differently named skills)". Symmetric to 6a. | ~1 min |
| 7 | `uv run scripts/run_pilot.py --all-tasks --condition full --n-runs 1` | 20-task pilot (1 run each). Last gate before committing ~$6 / 18 h to the 400-record run | ~30 min |

---

## Phase 4 — Data generation (unattended, SERIAL per ext7 #51)

Rows 8/9/10 share the `data/eval/chroma`, `src/mcp_tools`, `data/eval/skills`, and `tests/evaluation/snapshots` directories. Running them concurrently **will** corrupt one or more of the runs (via registry `rmtree` or snapshot-hash mismatch). Run them **strictly in sequence**: row 8 → row 9 → row 10. Wall-clock sum: **~30–38 h** (18 h row 8 + 6–10 h row 9 + 6–10 h row 10).

### Campaign DB isolation (ext7 #49)

Every Phase-4 run MUST write to its own DuckDB. `run_pilot.py --db` now defaults to a fresh timestamped path (`data/eval/metrics_<timestamp>.duckdb`); passing an existing DB without `--fresh-db` or `--append` exits with code 2. Set this at session start so every row references the same campaign DB:

```bash
export CAMPAIGN_DB="data/eval/metrics_$(date +%Y-%m-%d_%H%M%S)_full.duckdb"
```

Then pass `--db $CAMPAIGN_DB` to row 8 below and to every analyzer row (11, 17). Legacy `data/eval/metrics.duckdb` is retained only when `--db legacy` is passed explicitly.

| # | Command | Why | Time / $ |
|---|---|---|---:|
| 8 | `uv run scripts/run_pilot.py --db $CAMPAIGN_DB --all-tasks --all-conditions --n-runs 5` | **THE eval.** 20 tasks × 4 conditions × 5 runs = 400 records → `$CAMPAIGN_DB` | ~18 h / ~$6 |
| 9 | `uv run scripts/run_longitudinal.py` | **Pillar 3 RQ #2** — 40 sequential tasks, shared registry, no per-run reset → `data/eval/longitudinal.duckdb` | ~6–10 h |
| 10 | `uv run scripts/run_longitudinal_shift.py --phase-a-tasks T01,T02,T03,T04,T05 --phase-b-tasks T11,T12,T13,T14,T15 --shift-at 20 --total 40` | **Pillar 3 RQ #3** — two-phase with distribution shift → `data/eval/longitudinal_shifted.duckdb` | ~6–10 h |

---

## Phase 4/5 — Analysis (rows 11 + 16 MUST precede row 17; rows 12–15 are independent)

Row 11 backfills M4 `token_cost` → consumed by row 17. Row 16 populates `human_label` → consumed by row 17 for M2. Both are blocking. Rows 12, 13, 14, 15 produce standalone reports and can run any time post-eval — in parallel with row 16's human audit if you want to save wall-clock.

### Scoping convention (ext6 #38/#40/#41 + ext7 #45)

Every Row below that reads `data/eval/metrics.duckdb` or a longitudinal DB MUST scope by the campaign's `run_id`(s). The DBs are append-only across sessions — unscoped queries pool prior campaigns into the current Chapter-6 aggregate.

**The canonical Phase-4 run (`--all-tasks --all-conditions --n-runs 5`) produces 20 distinct `run_id`s** (4 conditions × 5 runs). Each of those 20 run_ids is also a LangFuse session_id (`tests/evaluation/harness.py:396` — the harness sets `session_id=run_id` on every trace). So `CAMPAIGN_RUN_IDS` and `CAMPAIGN_SESSION_IDS` both need 20 values. A fixed literal example would go stale on every campaign — populate them dynamically from the DB + LangFuse after Row 8 finishes:

```bash
# 20 run_ids from this Phase-4 campaign (4 conditions × 5 runs).
# Runs AFTER Row 8 completes; reads $CAMPAIGN_DB set in the isolation block above.
export CAMPAIGN_RUN_IDS="$(uv run python -c "
import duckdb
conn = duckdb.connect('$CAMPAIGN_DB')
ids = [r[0] for r in conn.execute(
    'SELECT DISTINCT run_id FROM evaluation_runs '
    'WHERE NOT is_pilot ORDER BY run_id'
).fetchall()]
print(' '.join(ids))
")"

# LangFuse session_id == DuckDB run_id (harness.py:396) — reuse the run_ids.
# Rows 13 + 15 take these as --session-ids; rows 14, 16, 17 take the run_ids
# themselves as --run-ids against $CAMPAIGN_DB.
export CAMPAIGN_SESSION_IDS="$CAMPAIGN_RUN_IDS"

# Longitudinal runs emit ONE run_id per invocation (uuid-based). Capture
# after Rows 9 / 10 complete by tailing the runner log or querying the DB.
# ext11 #111 (2026-04-20): use GROUP BY run_id ORDER BY MAX(timestamp) — the
# prior ``SELECT DISTINCT run_id ... ORDER BY timestamp DESC LIMIT 1`` form
# was a DuckDB hazard: DISTINCT collapses rows to one per run_id with an
# arbitrarily-picked timestamp, so ORDER BY on the non-selected timestamp
# column could return a non-latest run_id. With multiple rows per run_id
# (the normal append-only case) this silently picked the wrong campaign.
# ext12 #117 (2026-04-21): the ext11 #111 rewrite was correct at the SQL
# level but incomplete at the campaign level — a partial/aborted 5-row
# run written AFTER a complete 40-row run won by MAX(timestamp). The
# selector now carries a ``HAVING COUNT(*) >= :expected_rows`` completeness
# filter so stub runs are rejected and the SystemExit fires if no complete
# run exists in the DB. Pass the expected cycle count explicitly — the
# default matches the canonical ``run_longitudinal.py --n-tasks 40``.
#   SELECT run_id FROM evaluation_runs GROUP BY run_id
#   HAVING COUNT(*) >= :expected_rows
#   ORDER BY MAX(timestamp) DESC LIMIT 1;
export EXPECTED_LONGITUDINAL_ROWS=40  # matches run_longitudinal.py --n-tasks
export LONGITUDINAL_RUN_ID="$(uv run python -c "
import duckdb, sys
conn = duckdb.connect('data/eval/longitudinal.duckdb')
row = conn.execute(
    'SELECT run_id FROM evaluation_runs GROUP BY run_id '
    'HAVING COUNT(*) >= ? ORDER BY MAX(timestamp) DESC LIMIT 1',
    [${EXPECTED_LONGITUDINAL_ROWS}],
).fetchone()
if row is None:
    sys.exit('ERROR: no complete longitudinal run in DB '
             '(expected >= ${EXPECTED_LONGITUDINAL_ROWS} rows). '
             'Lower EXPECTED_LONGITUDINAL_ROWS, or re-run row 9.')
print(row[0])
")"
export SHIFT_RUN_ID="$(uv run python -c "
import duckdb, sys
conn = duckdb.connect('data/eval/longitudinal_shifted.duckdb')
row = conn.execute(
    'SELECT run_id FROM evaluation_runs GROUP BY run_id '
    'HAVING COUNT(*) >= ? ORDER BY MAX(timestamp) DESC LIMIT 1',
    [${EXPECTED_LONGITUDINAL_ROWS}],
).fetchone()
if row is None:
    sys.exit('ERROR: no complete longitudinal_shifted run in DB '
             '(expected >= ${EXPECTED_LONGITUDINAL_ROWS} rows). '
             'Lower EXPECTED_LONGITUDINAL_ROWS, or re-run row 10.')
print(row[0])
")"
```

**Why dynamic discovery, not a literal example:** every campaign generates fresh uuid-hash suffixes, and `--n-runs` is configurable — a literal list in the doc goes stale on every invocation. An earlier doc draft (pre-fix) carried a 4-value literal list that, if followed verbatim on the canonical 20-run campaign, would silently scope analyses to 20% of the data — the exact bug ext7 #46 was meant to prevent, re-opened through a doc mismatch.

Every analyzer row below passes the appropriate scope flag. Forgetting the flag is not safe: `analyze_results.py` will auto-select with a WARN (ext6 #38 + ext7 #46 balanced per-condition), but the other analyzers will silently pool cross-campaign data.

| # | Command | Why (proposal metric) | Time |
|---|---|---|---:|
| 11 | `uv run scripts/backfill_token_costs.py --db $CAMPAIGN_DB --write` | **M4** — populates `evaluation_runs.token_cost` from LangFuse GENERATION observations | ~5 min |
| 12 | `uv run scripts/backfill_definition_token_costs.py --write --stores eval` | **Pillar 3 U(c) token-cost input** — per-capability definition-string tokens (proposal §Pillar 3 four-metric table, line 170). Distinct from row 11's M4 (total per-task). Idempotent. ext11 #112: ``--stores eval`` is required so the backfill does not silently mutate ``data/cli/chroma`` during an eval campaign (the script defaults to ``--stores all``). | ~1 min |
| 13 | `uv run scripts/analyze_context_utilization.py --session-ids $CAMPAIGN_SESSION_IDS` | **T3 naive-verbose half** — peak input-token % of 128k context window per agent type | ~1 min |
| 14 | `uv run scripts/analyze_compression_loss.py --db $CAMPAIGN_DB --run-ids $CAMPAIGN_RUN_IDS` | **T3 aggressive-compression half** — 9-bucket failure taxonomy + most-dropped-keys table | ~1 min |
| 15 | `uv run scripts/analyze_tier2_probes.py --environment default --session-ids $CAMPAIGN_SESSION_IDS` | **Chapter 4.3 methodology evidence** — per-Tier-2-probe-type detection breakdown. Reads `verification.tier_2.{pass,reject}` LangFuse events, groups by `metadata.categories`, reports per-category hit counts + per-capability-type distribution. Answers "of the N Tier 2 rejections, which probe types caught them?" — proposal §Pillar 2 line 133 grounding. | ~10 s |
| 16 | `uv run scripts/audit_contamination.py --db $CAMPAIGN_DB --run-ids $CAMPAIGN_RUN_IDS` | **M2** — manual contamination audit, stratified random sample (default 90). **Interactive — human labels each sampled capability as clean / contaminated / partial.** Resumable. | **~2–4 h HUMAN** |
| 17 | `uv run scripts/analyze_results.py --db $CAMPAIGN_DB --run-ids $CAMPAIGN_RUN_IDS` | **THE Chapter 6 analyzer** — M1 (Fisher + Holm + Clopper-Pearson CI) + M3 Gini of invocations + M4 total token cost + M5 latency + M2 contamination + Core-Work Classification Accuracy *(Pillar 1 RQ #1 proxy — task-level primary-type match; see Step 18.5 disclosure)* + failure breakdown | ~2 min |
| 18 | `uv run scripts/analyze_longitudinal.py --run-id $LONGITUDINAL_RUN_ID` | **M6 + Pillar 3 RQ #1/#2** — utility-score KL convergence + cycles-to-converge + seeded-vs-evolved Gini | ~1 min |
| 19 | `uv run scripts/analyze_longitudinal.py --db data/eval/longitudinal_shifted.duckdb --run-id $SHIFT_RUN_ID` | **Pillar 3 RQ #3** — three-tier adaptation verdict (graceful / sluggish / collapse) | ~1 min |

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
| Writing Chapter 4 §14.6 appendix | `uv run scripts/validate_contract_extractor.py` | 20/20 extractor agreement check against authored T-task contracts |

**Note (ext7 remediation 2026-04-20):** `test_registry_matching.py` (Chapter 3 FP) + `test_registry_fn.py` (Chapter 3 FN) were previously in this diagnostic list. They are now elevated to main-path rows 6a + 6b because the proposal's §Pillar 1 line 77 claim ("hierarchical embeddings prevent both false positives and false negatives") needs numbered evidence in Chapter 3.

---

## Totals

| Phase | Hands-on | Unattended | $ |
|---|---:|---:|---:|
| Phase 3 smoke | ~57 min | — | ~$0.50–1 |
| Phase 4 data gen | kickoff only | ~30–38 h (SERIAL per ext7 #51) | ~$6 |
| Phase 4/5 analysis | ~2–4 h (row 16 human audit) | trivial | $0 |
| **Total** | **~4–6 h** | **~30–38 h** | **~$7** |

---

## Outstanding pre-eval work (per master plan TBD)

**Nothing.** All pre-eval blockers closed as of 2026-04-21:
- Step 13.5 (test suite hygiene) ✅ — full audit + cleanup done 2026-04-19
- Step 13.6 (scripts hygiene + Part D schedule update) ✅
- Step 13.7 (LangFuse audit) ✅
- All analyzer scripts ✅
- Security audit ext12 (#114-#123) ✅ — 10 P0/HIGH findings closed 2026-04-21; every result-destroying bug on the run-sheet critical path is fixed. Remaining ext8 #61 (latent) + #63 (partial) are Chapter-4.5-disclosure only, not eval-corrupting.

**Phase 3 smoke is immediately runnable.** Step 18 (the 400-record eval) runs once Phase 3 smoke passes.

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
| Pillar 1 line 77 — hierarchical embeddings prevent **FP** | line 77 | Row 6a (`test_registry_matching.py`) |
| Pillar 1 line 77 — hierarchical embeddings prevent **FN** | line 77 | Row 6b (`test_registry_fn.py`) |
| Pillar 3 RQ #1 — evolutionary vs human distribution | line 195 | Row 18 seeded-vs-evolved Gini comparison |
| Pillar 3 RQ #2 — cycles to converge | line 196 | Row 18 cycles-to-convergence on utility-score KL |
| Pillar 3 RQ #3 — stability under distribution shift | line 197 | Row 19 three-tier adaptation verdict |

### §Bootstrapping three tensions (lines 15–19)

| Tension | Row |
|---|---|
| T1 — Autonomy vs correctness | Implicit via entire Pillar 2 pipeline — defensibility at row 3 (`test_verification.py`) + row 4 (`test_allow_network.py`) + row 15 (`analyze_tier2_probes.py`) + M2 at row 16 |
| T2 — Specificity vs generality | Pillar 3 at rows 5 + 18 + 19; hierarchical-embeddings FP+FN claims at rows 6a + 6b |
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
