# Security / Robustness Audit — 2026-04-19, Fifteenth Extension (#151-#173)

**Parent document:** [`security_audit_2026_04_19.md`](./security_audit_2026_04_19.md) — #1-#18 all ✅ FIXED 2026-04-20.
**Sibling extensions:**
- [`security_audit_2026_04_19_ext4.md`](./security_audit_2026_04_19_ext4.md) — #12-#14 ✅ (folded into parent).
- [`security_audit_2026_04_19_ext5.md`](./security_audit_2026_04_19_ext5.md) — #15-#18 ✅ (folded into parent).
- [`security_audit_2026_04_19_ext6.md`](./security_audit_2026_04_19_ext6.md) — #19-#43 ✅ all FIXED 2026-04-20.
- [`security_audit_2026_04_19_ext7.md`](./security_audit_2026_04_19_ext7.md) — #44-#55 ✅ all FIXED 2026-04-20.
- [`security_audit_2026_04_19_ext8.md`](./security_audit_2026_04_19_ext8.md) — #56-#78 (20 ✅ FIXED + 1 ❎ false-positive (#56) + 1 ✅ partial (#63) + 2 ❎ acknowledged (#61, #70)).
- [`security_audit_2026_04_19_ext9.md`](./security_audit_2026_04_19_ext9.md) — #79-#98 ✅ all FIXED 2026-04-21.
- [`security_audit_2026_04_19_ext10.md`](./security_audit_2026_04_19_ext10.md) — #98-#107 ✅ FIXED; #108 ⚠ latent/documented.
- [`security_audit_2026_04_19_ext11.md`](./security_audit_2026_04_19_ext11.md) — #109-#113 ✅ all FIXED 2026-04-21.
- [`security_audit_2026_04_19_ext12.md`](./security_audit_2026_04_19_ext12.md) — #114-#123 ✅ all FIXED 2026-04-21.
- [`security_audit_2026_04_19_ext13.md`](./security_audit_2026_04_19_ext13.md) — #124-#138 ✅ all FIXED 2026-04-21 (commit `61bf17d`).
- [`security_audit_2026_04_19_ext14.md`](./security_audit_2026_04_19_ext14.md) — #139-#150 ✅ all FIXED 2026-04-21 (commit `891be59`).

**This extension's scope.** Six findings surfaced 2026-04-21 via reviewer-driven triage, in two thematically-distinct bundles:

**Bundle A (#151-#153) — CAMPAIGN_RUN_IDS selector + completeness/identity gates.** All three submitted as P0/P0/P1; **verification downgrades #151 + #152 to HIGH (defense-in-depth)** and **confirms #153 as P1 latent** — detailed assessment in each finding's Verification section.

**Bundle B (#154-#156) — LangFuse-analyzer session-coverage gates vs per-condition emission invariants.** Three findings that all boil down to the same root cause: the ext14 #139 strict-per-session coverage gate was designed for "typo / retention / partial-ingest" semantics and doesn't account for **condition-specific structural emission invariants** in the harness (static runs emit no DeepAgent traces; static + no_verification emit no `verification.tier_2.*` events; a healthy JIT session can emit zero rejects). All three **confirm reviewer severity** (two P0 + one P1) — they fire on canonical campaigns under the default run-sheet workflow, not on contract violations. #154 is especially sharp because it affects even the full + no_optimization JIT conditions when Tier 2 passes all probes (the winning outcome). Fix family: replace strict-per-session set semantics with condition-scoped session subsets, OR switch the coverage probe from event-emission to a lighter signal (DuckDB-side session existence via `langfuse_trace_id` correspondence) that doesn't collide with structural emission zero-states.

**Bundle C (#157-#159) — asymmetric-check completeness gates + filter-scoping mismatches in M2 + Row 17 condition identity.** Three findings surfacing the same structural pattern applied to the condition axis: count-only / missing-only checks that don't close the symmetric "identity drift" or "extra conditions" half. Submitted as P0/P1/P1; **verification downgrades #157 to HIGH (defense-in-depth)** (same reachability profile as #152 task-identity drift — requires contract violation / code edit) and **confirms #158 + #159 as P1**. #158 is a pure filter-scoping mismatch in Row 16's ext14 #143 empty-sample fallback (uses only `run_id + NOT is_pilot` when the main pool query uses six filters); #159 is a pure asymmetric-check bug in M2's per-condition coverage gate (catches missing, misses extra).

**Bundle D (#160-#161) — M4 silent-truncation + asymmetric-gate pair.** Two findings on the M4 axis that compound when co-occurring: #160 is the Bundle-A/B-style asymmetric-check antipattern on the M4 silent-zero gate (`max(jit_ratios) < 0.5` means one healthy JIT condition masks two broken ones); #161 is a silent pagination-truncation gap in `fetch_traces_by_session` that ext14 #141's sweep missed (different function from `fetch_all_generations` which did get the fix). **Confirms reviewer severities** (P0 + P1). #160 is default-workflow-reachable whenever Row 11 partially fails (ext14 #141 pagination on a subset of sessions, ext12 #122 zero-generations on some runs, partial CAMPAIGN_RUN_IDS); #161 is narrower (requires >20k traces in a single session, realistic under AGENT-heavy workloads but not common). Fix family: #160 swaps `max(ratios) < 0.5` → per-condition enforcement; #161 retrofits ext14 #140's `(records, truncated)` return-tuple pattern onto the missed function.

**Bundle E (#162-#164) — Row 17 headline-table correctness: M3 Aggregate math, table-label-vs-scope desync, missing-metrics retrofit gap.** Three findings on the Row 17 output path, mixed severity: #162 is a **P0 CRITICAL** math-vs-label desync on the M3 Aggregate Gini column — header claims "pooled by capability" but the code feeds raw per-(run, capability) counts into the Gini helper, materially distorting a Chapter 6 headline (constructed divergence example: true Gini=0 vs current Gini≈0.375 on a permutation-symmetric workload). #163 is **P1** — ext6 #17 fixed the `range(1,19)` compute bug that silently dropped T19/T20 but left five call-sites (module docstring, cluster print, Table 1 title, two inline comments) labeled with the pre-fix T04-T13 / T04-T18 / T01-T18 ranges; the math is now on T01-T20 / T04-T20 but the printed headers still claim the smaller ranges. #164 is **P1** — `print_classification_accuracy` is the fourth Chapter 6 metric (alongside M2 / M3 / M4) but was not retrofitted into the ext13 #126 / #128 / #132 + ext14 #149 `return bool` + `missing_metrics` exit-7 pattern; its three skip paths (ALL_TASKS import failure, no tasks with `expected_type`, no scoped JIT rows) silently exit 0 without the section. All three findings fire on default workflow. **Severity note on #162:** reviewer's submission carried no explicit severity marker — escalating to P0 on grounds of Chapter 6 headline-integrity corruption matching the #154 / #155 / #160 bar.

**Bundle F (#165-#167) — Row 11 M4 backfill integrity: unmatched-generations gate gap, classification-confusion scope blacklist-vs-whitelist, Row 11 scoping-contract unsatisfiability.** Three Row-11-focused findings, all **P1 confirmed**. #165 is a completeness-gate asymmetry in `map_generations_to_runs`: the per-run "zero matches" gate (ext12 #122) refuses with exit 3, but the complementary "LangFuse generations that matched no run window" gate is absent — `unmatched` count is printed-only. Non-trivial unmatched fraction biases M4 downward while per-run gates pass. #166 extends Bundle C/E's label-vs-compute-scope desync pattern to `print_classification_accuracy`: SQL uses `condition != 'static'` **blacklist** while the confusion-matrix header promises "aggregated over JIT conditions." Non-baseline condition labels (`longitudinal`, future ablations, enum drift) silently enter the Pillar 1 RQ #1 proxy headline. Same structural family as #163 / #164 on the classification-accuracy surface. #167 surfaces that `docs/run_sheet.md:70`'s explicit scoping contract — "every Row reading metrics.duckdb MUST scope by campaign's run_id(s)" — is **structurally unsatisfiable for Row 11**: `backfill_token_costs.py` has no `--run-ids` / `--since` CLI args, all three internal queries use `WHERE is_pilot = FALSE` with no scope, and Row 11's invocation in the run-sheet only passes `--db`. Under ext7 #49 DB-per-campaign contract the impact is latent; under `--append` misuse, out-of-campaign run windows force wider LangFuse fetch and can trigger false ext12 #122 exit 3 on an otherwise-clean current campaign (compounds with #165 on unmatched rate).

**Bundle G (#168-#169) — Row 15 prose-vs-command desync + CAMPAIGN_RUN_IDS upper-bound asymmetry.** Two findings that both add instances to the "stated-vs-realized" antipattern family (see Bundle F end-note structural shape). Both submitted unlabeled / P1; **both confirmed P1**. #168 is a documentation-vs-command divergence on Row 15: the run-sheet prose promises "Reads `verification.tier_2.{pass,reject}` LangFuse events" but the command omits `--include-pass` and the analyzer defaults to reject-only — the same cell's second sentence "of the N Tier 2 rejections, which probe types caught them?" matches the command but contradicts the first sentence. Reader of Chapter 4.3 cannot determine the denominator semantics. Interacts with #154: "add `--include-pass` to Row 15" simultaneously fixes the prose-vs-command divergence AND sidesteps #154's coverage-gate collision (sessions with all-pass outcomes now contribute). #169 is the upper-bound complement to ext14 #145's completeness filter in `CAMPAIGN_RUN_IDS`: the selector admits `COUNT(*) >= :expected_rows` (accepts overfull) and the dropped-report branch only catches `< :expected` (reports underfull). Overfull run_ids (from #153 collisions, duplicate-write retry paths, or `--append` re-runs that reuse a run_id) enter `$CAMPAIGN_RUN_IDS` silently; rows 14/16 inherit them without row 17's per-(condition, task_id) uniformity gate. Symmetric fix: `= :expected_rows` strict equality, bidirectional dropped-report.

**Bundle H (#170) — Row 11 foreign-generation attribution: `fetch_all_generations` has no session/environment filter.** Standalone **P0 CRITICAL** (escalated from reviewer-unlabeled). `scripts/backfill_token_costs.py:69-147` fetches every GENERATION observation from LangFuse in the time window with no `sessionId` / `environment` / `tags` filter; `map_generations_to_runs` at line 224 attributes by temporal proximity (±120s clipped buffer). Any foreign trace landing in the campaign's time window — concurrent CLI runs (default `LANGFUSE_TRACING_ENVIRONMENT="test"` for dev scripts vs `environment="default"` for runner), pilot-run (Row 7) generations, other users on a shared LangFuse instance, unrelated projects — silently ingested. Inflates M4 `token_cost` HIGH on campaign rows. All existing coverage gates (zero_summary_runs / unmatched / pagination truncation) detect UNDER-count; none catch OVER-count from foreign ingestion. Default-workflow-reachable without contract violation; distinct from every other Bundle finding in this axis.

**Bundle I (#171-#173) — Run-id cardinality gate gap + longitudinal upper-bound asymmetries.** Three findings extending the upper-bound-asymmetry family (siblings of #169) across the run-id independence axis and the longitudinal-analyzer surface. #171 is the missing `COUNT(DISTINCT run_id) per condition = :expected_runs_per_condition` check in `analyze_results.py` — a pseudo-replicated DB (1 run_id × 20 tasks × 5 duplicates per task, per condition) passes all four existing gates (total, per-condition, per-condition distinct tasks, per-(condition, task_id) uniformity) while violating the independence assumption behind Fisher / Holm / Clopper-Pearson. Reviewer submitted P0; **downgraded to HIGH defense-in-depth** (consistent with #152 / #157 severity framing) because default workflow produces distinct `run_id`s via `uuid4().hex[:6]` and pseudo-replication is only reachable via #153 collision at scale, #169 overfull passage, `--append` run_id re-use, or a harness-level pseudo-replication bug. #172 + #173 are longitudinal-analyzer + longitudinal selector upper-bound blind spots (`len(cycles) < expected` strict-inequality shortfall gate; `HAVING COUNT(*) >= :expected_rows` selector) — merged longitudinal timelines from #153 collision / duplicate writes silently pass and feed Row 18/19 convergence / adaptation metrics. **Both confirmed P1.** Symmetric fixes: `= :expected_rows` strict equality + bidirectional dropped/overfull reporting.

- **#151 — CAMPAIGN_RUN_IDS selector lacks per-condition recency ranking (HIGH, defense-in-depth):** the `docs/run_sheet.md` Phase-4/5 scoping block exports every complete non-pilot run_id from `$CAMPAIGN_DB` without a per-condition `MAX(timestamp)` recency filter or campaign-identity check. Inconsistent with the ext11 #111 longitudinal selector pattern (which *does* rank by `MAX(timestamp)`). If a DB contains run_ids from two campaigns — requires explicit `--append` misuse against the ext7 #49 DB-per-campaign contract — the selector pools them. Row 17 (`analyze_results.py`) has a total/per-condition/per-(condition,task) uniformity gate that would catch the most common mixing shapes (ext7 #52 + ext13 #131), but rows 14 (`analyze_compression_loss.py`) and 16 (`audit_contamination.py`) inherit whatever `$CAMPAIGN_RUN_IDS` contains without replicating row 17's completeness triple-check. Hardening the selector itself closes the residual defense-in-depth gap for rows 14 + 16 and aligns the Phase-4 selector with its sibling longitudinal selectors.
- **#152 — Completeness gate validates task_id count, not canonical T01..T20 identity (HIGH, defense-in-depth):** `scripts/analyze_results.py:1605-1626` enforces `COUNT(DISTINCT task_id) == expected_tasks` per condition + per-(condition, task_id) uniformity, but never asserts the set of observed task_ids equals the canonical testbed set `{T01..T20}` (sourced from `tests/evaluation/tasks.py:1402-1405` `ALL_TASKS`). A drifted set (e.g., T01..T15 + T21..T25 from a prior-iteration campaign, or a manually-modified harness that swaps T05 for a new task) produces 20 distinct task_ids per condition with 5 rows per (condition, task_id) and silently passes the gate while biasing Chapter 6 aggregates against the proposal's declared testbed. Requires either code edit to `ALL_TASKS` mid-campaign or `--append` of a cross-iteration DB to reach. Row 17's ext7 #52 + ext13 #131 gate is the parent of this check; adding canonical-set assertion extends the same gate to identity.
- **#153 — run_id structural collision risk in `--append` mode (P1 latent):** `tests/evaluation/harness.py:302` generates `run_id = f"{condition}_{run_idx:02d}_{uuid.uuid4().hex[:6]}"` — a 24-bit random suffix per (condition, run_idx) slot. `src/registry/evaluation_store.py:23-27` schema has `id VARCHAR PRIMARY KEY` (row-level UUID) but **no uniqueness constraint on `run_id`**. Birthday collision math: 24 bits = 16.7M values; per-slot collision probability across 10 `--append`-chained campaigns is ~0.0075% per slot × 20 (condition, run_idx) slots ≈ 0.15% per 10-campaign DB. If collision occurs, two distinct logical runs' rows share a `run_id` key; `GROUP BY run_id` in downstream SQL (ext11 #111 longitudinal selector, row 17 per-(condition, task_id) uniformity) pools them silently. Low probability but unguarded structurally. Fix: bump to full `uuid4().hex` (128 bits → collision negligible) or add a uniqueness constraint on `(run_id, condition)` enforced at `insert_record` time.
- **#154 — `analyze_tier2_probes.py` session-coverage gate collides with reject-only default fetch (P0 confirmed):** `scripts/analyze_tier2_probes.py:504` defaults `event_names = (_TIER2_EVENT_REJECT,)`. Line 515-523 fetch returns only sessions that produced ≥1 reject event. Line 544-559 ext14 #139 strict-per-session gate then demands every requested `--session-ids` contribute ≥1 fetched event. **A session with zero rejects is indistinguishable from a missing session in the gate's set semantics.** Healthy JIT sessions (full / no_optimization) where Tier 2 passed every probe — the *desired* outcome per proposal §Pillar 2 — fail the gate at exit 4 despite correct behavior. Workaround `--allow-partial-sessions` forces a Chapter 4.5 `[PARTIAL SESSIONS]` disclosure banner for what is structurally a clean campaign. Fix options: (A) expand the coverage-gate fetch to always include `verification.tier_2.pass` events regardless of `--include-pass` analysis flag; (B) switch the coverage probe from event-emission to a lighter signal (DuckDB `langfuse_trace_id` correspondence to `--session-ids`); (C) relax gate semantics to "session exists in LangFuse trace API" rather than "session emitted ≥1 of the queried event names."
- **#155 — Row 15 CAMPAIGN_SESSION_IDS scope includes conditions that structurally emit no `verification.tier_2.*` events (P0 confirmed):** `docs/run_sheet.md:121` sets `CAMPAIGN_SESSION_IDS="$CAMPAIGN_RUN_IDS"`, pushing all 20 run_ids to Row 15 (`analyze_tier2_probes.py --session-ids ...`). Per `tests/evaluation/harness.py:531-543`, the `static` condition dispatches to `_run_static` — no JIT pipeline invocation, no verification gate calls, **zero Tier 2 event emission possible by design**. Per line 710, `no_verification` sets `verification_enabled=False`, bypassing the gate entirely — also zero Tier 2 emission. 10 of 20 canonical-campaign sessions (5 × static + 5 × no_verification) cannot emit Tier 2 events; under ext14 #139's strict-per-session gate, Row 15 returns exit 4 with `10/20 requested session IDs contributed zero events` on **every canonical campaign**, forcing `--allow-partial-sessions` + Chapter 4.5 disclosure unconditionally. Combined with #154, even the 10 JIT sessions can fail if they all pass Tier 2. Fix: scope `CAMPAIGN_SESSION_IDS` for Row 15 to `condition IN ('full', 'no_optimization')` — the only conditions that structurally emit Tier 2 events. Orthogonal to #154 but stacks: #154 fixes "healthy JIT session with zero rejects"; #155 fixes "session from a no-Tier-2-emission condition."
- **#156 — Row 13 CAMPAIGN_SESSION_IDS scope includes `static` runs that produce no DeepAgent traces (P1 confirmed):** `scripts/analyze_context_utilization.py:55-58` queries four DeepAgent trace names (`task-decomposer`, `adversarial-prober`, `skill-executor`, `agent-executor`). Per `tests/evaluation/harness.py:_run_static` → `_run_static_implementation` (line 630), static invokes no DeepAgents — zero traces under any of the four queried names. Line 679-694 ext14 #139 per-session gate fires on static sessions contributing zero traces. Scope is narrower than #155: `no_verification` still emits `task-decomposer` + `skill-executor` + `agent-executor` traces (counts as "contributed" for gate purposes, missing only `adversarial-prober` which is per-session, not per-family, so the gate still passes). Only `static` fully fails. Under ext14 #139's strict gate, Row 13 exits 4 with `5/20 requested session IDs contributed zero rows` on every canonical campaign. Fix: scope `CAMPAIGN_SESSION_IDS` for Row 13 to `condition != 'static'` (all three JIT conditions), or relax gate to per-(session, agent-family) with a "≥1 family with ≥1 trace per session" invariant that allows no_verification's missing adversarial-prober without blocking the whole session.
- **#157 — Row 17 completeness gate accepts wrong condition *identity* when count is correct (HIGH, defense-in-depth; downgraded from P0):** `scripts/analyze_results.py:1593-1599` guards its missing-condition check with `if len(condition_map) < expected_conditions`. Identity is only verified when count is too low. A campaign with `condition_map = {full, no_verification, no_optimization, weird_new}` (4 conditions, 100 rows each, total 400, static replaced by a non-canonical label) satisfies `len == expected_conditions` → the missing-check is **skipped** → gate passes. Mirror of #152 on the condition axis. `CONDITIONS` constant at `scripts/analyze_results.py:32` already holds the canonical set `{full, no_verification, no_optimization, static}`; fix is a one-line set-equality assertion symmetric to #152's canonical task-identity assertion. Downgraded to HIGH defense-in-depth for the same reason as #152: reachable only under (a) `BaselineCondition` enum code edit (caught by `git diff`, not by the gate) or (b) `--append` cross-iteration with renamed conditions (ext7 #49 contract violation).
- **#158 — Row 16 ext14 #143 empty-sample fallback ignores active audit-scope filters; false-green on filter mismatch (P1 confirmed):** `scripts/audit_contamination.py:681-699` detects "pool empty + filter mismatch" vs "pool empty + genuinely all labeled" by issuing a fallback `SELECT COUNT(*) FROM evaluation_runs WHERE run_id IN (...) AND NOT is_pilot`. This drops the main pool query's six additional filters (`conditions`, `types`, `generated_artifact_count > 0`, `human_label IS NULL`, `since`). A typo'd `--conditions nonexistent` → pool empty → fallback counts ALL rows for the run_ids (ignoring `--conditions`) → returns non-zero → prints "Nothing to audit — all in-scope rows already labeled" → exit 0. Clean P1: the guard is doing exactly the right job for `--run-ids` typos (ext14 #143's original scope) but fails for filter-mismatch on the other five filters. Fix: parametrize the fallback query with the same filter set the pool query uses, not just `run_ids`.
- **#159 — M2 pooled rate aggregates all labeled conditions without whitelist; extra condition labels silently enter pooled numerator/denominator (P1 confirmed):** `scripts/analyze_results.py:1332-1448` `print_contamination_rate`. Initial query at 1332-1345 is `GROUP BY condition` with no whitelist. Pooled aggregation at 1402-1412 sums `total_c + total_x + total_p + total_n` unconditionally across every returned row. Per-condition coverage check at 1437-1448 is asymmetric: `missing_conditions = expected_conditions - present_conditions` catches missing JIT conditions (ext13 #132 + ext14 #146 intent); `extra_conditions = present_conditions - expected_conditions` is never computed → extra labels on `longitudinal` / future-ablation / cross-iteration conditions silently enter the pooled headline. Reachable under the same contract-violation paths as #151/#152/#157 (cross-iteration `--append`, longitudinal DB merged with Phase-4 DB, enum drift). Clean P1: one-line set-diff assertion on the extra-side closes the asymmetry. Headline bias magnitude depends on how many extra-condition labels land in scope — small under realistic misuse, but silent and unbounded in the limit.
- **#160 — M4 silent-zero gate uses `max(jit_ratios) < 0.5`; one healthy JIT condition masks two broken ones (P0 CRITICAL, confirmed):** `scripts/analyze_results.py:785-793` ext14 #149 gate checks `if not jit_ratios or max(jit_ratios) < 0.5`. The gate fires ONLY when every JIT condition is below the 50% non-zero-cost threshold. If Row 11 backfill succeeded for `full` (ratio 1.0) but silently partially-failed for `no_verification` + `no_optimization` (ratios 0.0 + 0.0) — via ext14 #141 pagination truncation on a subset of sessions, ext12 #122 zero-generations on specific runs, ext14 #145 partial CAMPAIGN_RUN_IDS, LangFuse transient retention on some sessions, or #161 per-session pagination truncation — `max = 1.0 ≥ 0.5` → gate passes → Row 17 M4 table prints `full: 100,000 avg / no_verification: 0 avg / no_optimization: 0 avg` → Chapter 6 concludes "full condition has infinite cost-efficiency advantage" on structurally corrupted data. Default-workflow-reachable whenever Row 11 partially succeeds. Same asymmetric-check antipattern as #152/#157/#159 (missing-side checked, extra/partial-side unchecked) applied to the M4 axis. Fix: per-condition enforcement — `any(r < 0.5 for r in jit_ratios)` replaces `max(...) < 0.5` so the gate fires on ANY JIT condition below threshold, not all.
- **#161 — `fetch_traces_by_session` silent pagination truncation at 20k traces per session undercounts per-task M4 token costs (P1 confirmed):** `scripts/backfill_token_costs.py:520-556` `fetch_traces_by_session` breaks at `page > 200` with no warning log and no `truncated` return flag. Contrast with the sibling `fetch_all_generations` (ext14 #141 fix) and the LangFuse analyzers `fetch_traces`/`fetch_tier2_events` (ext14 #140 fix) which all now return `(records, truncated: bool)` tuples with WARNING logs + caller-side gates at exit 5. ext14 #141's sweep fixed only `fetch_all_generations`; this distinct per-session trace-fetch path was missed. In per-task attribution mode (line 615 `run_task_tokens[(rid, task_id)]`), a high-volume session exceeding 20k traces silently drops late traces → affected `(rid, task_id)` tokens under-counted → M4 token_cost written to `evaluation_runs` is biased low. Coverage gate at line 685-690 is **per-key** (`missing = expected - present_keys`) — verifies *presence* of `(rid, task_id)` entries, not *completeness within a key*. Truncated rows have the key present with under-sampled value → slip through the coverage gate. Reachable under AGENT-heavy workloads where DeepAgent subagent fan-out + adversarial probing iteration produces dense per-session trace volume. 20k ceiling = ~50 tasks × 400 traces/task before truncation; T18-T20 AGENT-heavy tasks can approach this under full JIT conditions.
- **#162 — M3 'Aggregate' Gini column is computed on raw per-(run, capability) counts, not pooled-by-capability totals; header promise and compute diverge (P0 CRITICAL, escalated from unlabeled):** `scripts/analyze_results.py:935-982` `print_gini_summary`. Line 937-940 iterates `per_run_rows` and **appends raw `inv_count` values into `by_condition[cond]`** — each entry is the count for ONE `(run_id, capability_id)` pair. Line 981 runs `_gini_coefficient(by_condition[cond])` on that flattened list and prints it under the "Aggregate" column header. The header at line 966-968 explicitly claims "all invocations across runs in that condition treated as one distribution" — but mathematically, "one pooled distribution over capabilities" means summing counts by `capability_id` across runs first. Divergence example: `run1={capA:100, capB:0}`, `run2={capA:0, capB:100}` → true pooled `{capA:100, capB:100}` Gini = 0 (perfect equality); current compute `[100, 0, 0, 100]` Gini ≈ 0.375 (material inequality). On permutation-symmetric workloads (usage of capA in some runs vs capB in others), true pooled Gini approaches 0 while current aggregate stays elevated. Chapter 6 M3 aggregate numbers can therefore be **directionally wrong** even when per-run means are reasonable. Default-workflow-reachable; directly corrupts a headline metric. Fix: aggregate counts by `capability_id` across runs first, then feed to `_gini_coefficient`. Same asymmetric/mislabelled-compute antipattern family as #160 on M4 axis (pre-fix gate's math doesn't match its stated semantics). Reviewer submitted without explicit severity; escalated to P0 on Chapter 6 headline-integrity grounds.
- **#163 — Table 1 / Table 2 / JIT cluster labels still cite T04-T13 / T04-T18 / T01-T18 after ext6 #17 extended compute scope to T01-T20 / T04-T20 (P1 confirmed):** `scripts/analyze_results.py` has five label sites out of sync with actual compute scope. Constants at line 33, 43-44 establish `RETRIEVAL_TASKS = {T01, T02, T03}` and `SYNTHESIS_TASK_IDS = [T04..T20]` (17 tasks, imported from `ALL_TASKS`). Actual queries at 1654-1657 filter by `SYNTHESIS_TASK_IDS` (T04-T20); default query at 1650 has no filter (T01-T20). Stale labels: (a) line 4 module docstring `"synthesis-only T04-T13"`; (b) line 634 cluster print `"Synthesis tasks only (T04-T13):"`; (c) line 1651 `"Table 1: Success Rates — All Tasks (T01-T18)"`; (d) line 1653 comment `"Synthesis tasks only (T04-T13)"`; (e) line 1657 `"Table 2: Success Rates — Synthesis Tasks Only (T04-T18)"`. ext6 #17 (2026-04-20) closed the `range(1,19)` compute bug that silently dropped T19 (AGENT) + T20 (SKILL) from per-task tables but didn't propagate the fix into the cosmetic headers. Chapter 6 reader sees "T04-T18" but math is on T04-T20 (17 tasks): denominators, p-values, cluster gaps all computed on the full SYNTHESIS set while labels misrepresent the population. Fix: replace literal ranges with derived labels — `f"T04-T{max(SYNTHESIS_TASK_IDS)[1:]}"` or `"T04-T20"` hard-coded once, updated alongside `ALL_TASKS` via single-source-of-truth import. Pure label bug, not a compute bug.
- **#164 — `print_classification_accuracy` skipped-path return value is `None` instead of `bool`; Row 17 silently exits 0 on skip, unlike M2 / M3 / M4 (P1 confirmed):** `scripts/analyze_results.py:1047-1145` `print_classification_accuracy` has three early-return paths: (a) line 1111-1119 import failure for `ALL_TASKS`; (b) line 1123-1129 no tasks with `expected_type`; (c) line 1139-1145 no non-pilot JIT-condition rows. All three return `None` implicitly. Line 1696 calls `print_classification_accuracy(conn)` without capturing the return. `missing_metrics` list at 1701 is populated for M2 via `if not print_contamination_rate(conn)` (line 1710), M3 via `if not print_gini_summary(conn)` (line 1704), M4 via `if not m4_ok` (line 1717) — three sibling metrics all retrofitted into the `-> bool` contract by ext13 #126 / #128 / #132 + ext14 #149. Classification Accuracy, introduced by Step 18.5 as the fourth Chapter 6 metric (operational proxy for Pillar 1 RQ #1), was not retrofitted. A Row 17 run where ALL_TASKS import breaks (e.g., pytest collection-side effect) or where no tasks carry `expected_type` silently produces a `"(skipped: ...)"` line and exits 0. Chapter 6 readers see no Pillar 1 RQ #1 evaluation but no loud error either. Fix: signature becomes `-> bool`; all three skip paths return `False`; success path returns `True`; main() threads result into `missing_metrics` via `if not print_classification_accuracy(conn): missing_metrics.append("Classification Accuracy / Pillar 1 RQ #1")`.
- **#165 — `map_generations_to_runs` unmatched-count is logged but never integrity-gated; complement to ext12 #122's per-run zero-match gate is missing (P1 confirmed):** `scripts/backfill_token_costs.py:224-293` returns `(run_to_gens, unmatched)`. Main at line 907-908 prints `"Mapped to {len(run_to_gens)} runs, {unmatched} unmatched generations."` and proceeds unconditionally. The sibling per-run gate at line 944-964 (ext12 #122) refuses with exit 3 when any non-static run had zero matches — but the complementary "LangFuse generations that matched no run window at all" population is un-gated. Unmatched generations arise from (a) ext10 #105's 30s `OVERLAP_GUARD` no-man's-land between adjacent runs; (b) boundary generations at the first/last campaign edges falling outside the ±120s clipped buffer; (c) LangFuse/harness clock skew exceeding the 60s budget baked into the buffer. Non-trivial unmatched fraction biases M4 downward (tokens discarded) while every per-run gate passes because each run retains matches. Chapter 6 M4 shows quietly understated per-task cost with no override banner. Fix: add `unmatched / (unmatched + sum(len(gs) for gs in run_to_gens.values()))` ratio threshold (default 5%) → exit 4 + `--allow-unmatched` override matching the ext12 #122 / ext14 #141 pattern; Chapter 4.5 disclosure on override. Complementary to #161 (pagination truncation at fetch time): #161 truncates the input fed to the mapper; #165 drops post-mapper outputs — both under-count M4 tokens via different silent paths.
- **#166 — `print_classification_accuracy` SQL uses `condition != 'static'` blacklist; confusion matrix labeled "aggregated over JIT conditions" ingests non-baseline conditions silently (P1 confirmed):** `scripts/analyze_results.py:1131-1195`. Line 1135 SQL: `WHERE is_pilot = FALSE AND condition != 'static'{_SCOPE_SQL}` — blacklist, not whitelist. Per-condition iteration at line 1175-1184 uses the explicit JIT whitelist `["no_verification", "no_optimization", "full"]` and displays those three correctly. But the confusion-matrix aggregation at line 1161 (`confusion[(expected, classified_norm)] += 1`) has no JIT filter — it accumulates over every returned row. Line 1194 header: `"Confusion matrix (rows = expected, columns = classified, aggregated over JIT conditions):"` — promise diverges from compute: any non-baseline condition (`longitudinal` from cross-iteration append, future ablations, enum drift) silently enters the per-cell counts, the row totals, the accuracies in the right-margin column. Same label-vs-compute desync family as #159 / #163 / #164 applied to Pillar 1 RQ #1 proxy. Fix: replace SQL filter with explicit whitelist `AND condition IN ('full', 'no_verification', 'no_optimization')`; optionally add a symmetric `extra_conditions` set-diff assertion that refuses on extras via the ext15 #164 `missing_metrics` retrofit. Extension of the Bundle-E antipattern onto the classification-accuracy surface; reachable under same contract-violation paths as #151/#152/#157/#159.
- **#167 — Row 11 scoping contract (`docs/run_sheet.md:70`) structurally unsatisfiable: `backfill_token_costs.py` lacks `--run-ids` / `--since` CLI args and reads all non-pilot runs (P1 confirmed):** `docs/run_sheet.md:70` explicit contract: *"Every Row below that reads `data/eval/metrics.duckdb` or a longitudinal DB MUST scope by the campaign's `run_id`(s). The DBs are append-only across sessions — unscoped queries pool prior campaigns into the current Chapter-6 aggregate."* Row 11 invocation at `docs/run_sheet.md:178`: `uv run scripts/backfill_token_costs.py --db $CAMPAIGN_DB --write` — no `--run-ids`. `scripts/backfill_token_costs.py:762-816` parser has **no `--run-ids` or `--since` CLI args**; all three internal queries (`fetch_run_windows` at line 161-173, the `task_rows` fetch at line 176-178, `get_per_record_info` at line 200-206) use `WHERE is_pilot = FALSE` with no scope. The contract is structurally unsatisfiable: even if the user wanted to scope, the script surface has no accepting parameter. Compound impact: (a) `fetch_run_windows` reads all non-pilot runs → min_start/max_end of the full DB drive the LangFuse fetch window → wider window amplifies #165 unmatched rate (more generations fetched that fall outside any clipped buffer); (b) per-condition coverage gate at line 931-942 iterates ALL runs in DB → appended cross-campaign runs with zero LangFuse retention trigger ext12 #122 exit 3 on an otherwise-clean current campaign. Under ext7 #49 DB-per-campaign contract the impact is latent; under `--append` misuse, Row 11 produces degraded-override outcomes for the current campaign. Fix family: (1) add `--run-ids` + `--since` CLI args to `backfill_token_costs.py`; (2) thread scope into all three queries; (3) update Row 11 in run sheet to pass `--run-ids $CAMPAIGN_RUN_IDS`; (4) extend `tests/unit/test_run_sheet.py` to lint that Row 11 passes the scope flag (symmetric to ext7 #45 / #54 coverage for rows 14/16/17).
- **#168 — Row 15 prose claims `{pass,reject}` scope but command runs reject-only (`analyze_tier2_probes.py` default); Chapter 4.3 denominator semantics ambiguous (P1 confirmed):** `docs/run_sheet.md:182` Row 15 cell carries internally contradictory prose: first sentence promises *"Reads `verification.tier_2.{pass,reject}` LangFuse events, groups by `metadata.categories`, reports per-category hit counts + per-capability-type distribution."* (pass+reject scope); second sentence says *"Answers 'of the N Tier 2 rejections, which probe types caught them?'"* (reject-only semantics). Command has no `--include-pass` flag → `scripts/analyze_tier2_probes.py:504` default `event_names = (_TIER2_EVENT_REJECT,)` runs reject-only. Command matches second sentence; first sentence's `.{pass,reject}` promise is false. Chapter 4.3 reader cannot determine what's in the denominator: "hit counts" of what population? Reject-only computes detection rates per category; pass+reject computes total probe executions per category — materially different numbers. Interacts with #154: adding `--include-pass` to the Row 15 command both (a) makes the prose accurate and (b) populates #154's coverage gate with all-pass sessions, sidestepping its false-fail-on-clean-campaign mode. Dropping `.{pass,reject}` from the prose is the minimal fix but leaves #154 blocking. Same stated-vs-realized desync family as #159 / #163 / #166. Reviewer unlabeled; P1 matches the impact profile — documentation correctness + downstream blocker interaction, not numerical corruption.
- **#169 — CAMPAIGN_RUN_IDS completeness selector is asymmetric: `>= :expected_rows` admits overfull run_ids; dropped-report branch only catches underfull (P1 confirmed):** `docs/run_sheet.md:95-108`. The selector:
   ```sql
   HAVING COUNT(*) >= ${EXPECTED_ROWS_PER_RUN}    -- LOWER BOUND only
      AND COUNT(DISTINCT task_id) = ${EXPECTED_TASKS_PER_RUN}  -- strict equality
   ```
  and the dropped-report branch:
   ```sql
   HAVING COUNT(*) < ${EXPECTED_ROWS_PER_RUN}
       OR COUNT(DISTINCT task_id) < ${EXPECTED_TASKS_PER_RUN}
   ```
  Both use underfull-only semantics. Overfull `run_id`s are admitted silently into `$CAMPAIGN_RUN_IDS` without appearing in the dropped-report. Reachable via: (a) **#153 run_id collision** — two logical runs' rows (40 total) share one `run_id`, task_ids T01..T20 present across both → `COUNT(*)=40 >= 20`, `COUNT(DISTINCT task_id)=20`, admitted; (b) **accidental duplicate writes** from retry logic that re-inserts on transient DB errors; (c) **`--append` re-run that reuses an existing run_id** (same run_id recycled across sessions). Rows 14 + 16 (`analyze_compression_loss.py`, `audit_contamination.py`) inherit overfull run_ids directly — both lack row 17's ext13 #131 per-(condition, task_id) uniformity gate, so merged populations enter their outputs silently. Row 17's ext13 #131 check catches overfull at per-(condition, task_id) level (2 rows per task instead of 1) → exit 2 on that specific path, but only for row 17. Same defense-in-depth family as #151 on the upper-bound side. Symmetric fix: tighten `COUNT(*) >= :expected_rows` → `COUNT(*) = :expected_rows` on both admit and dropped-report branches; add a bidirectional "overfull" category to the dropped-report output so operators see exactly which run_ids are structurally merged. Stacks with #153 fix (full `uuid4().hex`) which reduces collision probability to ≈0 and with #157 (canonical condition identity) which closes the other asymmetric-check gap on the condition axis.
- **#170 — `fetch_all_generations` has no session/environment filter; Row 11 silently attributes foreign LangFuse generations to campaign runs, inflating M4 (P0 CRITICAL, escalated from unlabeled):** `scripts/backfill_token_costs.py:69-147`. Request params at lines 101-110 carry only `type=GENERATION`, `limit`, `page`, `fromStartTime`, `toStartTime` — **no `sessionId`, `environment`, or `tags` filter**. Every GENERATION observation on the LangFuse instance in the campaign's time window is fetched. `map_generations_to_runs` at line 224 then attributes each generation to the run whose clipped ±120s buffer contains the timestamp, with midpoint-distance tie-break. Any foreign generation landing in a run's buffer is silently attributed to that run → `run_summaries[rid]["total_tokens"]` inflated HIGH → `evaluation_runs.token_cost` written with inflated values → Row 17 M4 over-reports per-task cost. Coverage gates (ext12 #122 zero-match, ext13 #129 per-task coverage, ext13 #130 generations-per-record, #161 pagination truncation, #165 unmatched) are all UNDER-count detectors — none fire on OVER-count from foreign ingestion. Sources of foreign generations on a single-project LangFuse: (a) Concurrent CLI invocations (`uv run mint ...`) emit to `environment="default"` (per `src/config.py::LANGFUSE_ENVIRONMENT` default) during the campaign window; (b) Dev scripts (`scripts/test_*.py`) emit to `environment="test"` (per module-level `os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", "test")`) — still fetched; (c) Pilot runs (Row 7) emit non-pilot-indistinguishable generations; `get_run_windows` at line 169 filters `is_pilot=FALSE` for run-window construction but `fetch_all_generations` pulls ALL generations in the window → pilot-emission generations land in adjacent non-pilot runs' buffers; (d) Cross-project / multi-tenant LangFuse instances (if the `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` scopes admit foreign projects). Default-workflow-reachable on any non-single-tenant LangFuse. Fix: add `environment="default"` server-side filter to the REST request (matches runner emission convention, excludes dev-script `environment="test"` traces); add downstream `run_id`-or-tag post-fetch filter if the harness emits `session_id=run_id` metadata (it does — `tests/evaluation/harness.py:396`, and this is the basis for ext15 #156's scope envelope). Proposed change:
   ```python
   params: dict[str, str | int] = {
       "type": "GENERATION",
       "limit": 100,
       "page": page,
       "fromStartTime": start.isoformat(),
       "toStartTime": end.isoformat(),
       "environment": "default",  # ext15 #170: runner emission convention
   }
   ```
   Plus a post-fetch `sessionId` whitelist filter against the campaign's `CAMPAIGN_RUN_IDS` set if `--run-ids` is threaded through per #167. Without both, M4 totals are upper-bounded by foreign tokens. Chapter 4.5 disclosure REQUIRED even with the `environment` filter if foreign traces on `environment="default"` cannot be ruled out.
- **#171 — Row 17 completeness gate never verifies `COUNT(DISTINCT run_id) per condition = expected_runs_per_condition`; pseudo-replicated datasets silently pass and invalidate Fisher / Holm / CI independence assumptions (HIGH, defense-in-depth; downgraded from reviewer P0):** `scripts/analyze_results.py:1573-1626`. The `--expected-runs-per-condition` flag defines the expected number of distinct `run_id`s per condition (default 5) but is only consumed in derived values:
  - `expected_total = expected_conditions × expected_tasks × expected_runs` (line 1576) — total rows check.
  - `expected_per_condition = expected_tasks × expected_runs` (line 1577) — per-condition rows check.
  - Per-(condition, task_id) uniformity check (`HAVING COUNT(*) != expected_runs` at line 1619).
  
  None of those are a **distinct run_id count** check. A DB with `COUNT(DISTINCT run_id) per condition = 1` (one run_id per condition, each row duplicated so per-(condition, task_id) reaches `expected_runs`) satisfies every existing gate: total=400 ✓, per-condition=100 ✓, per-condition distinct tasks=20 ✓, per-(condition, task_id)=5 ✓. But there are only 4 distinct run_ids in the DB, not 20 → the 5-row-per-(condition, task) replication is pseudo-replication. M1 (Fisher's exact + Holm correction + Clopper-Pearson 95% CI) all assume row-level independence; duplicated-not-independent rows invalidate the test statistics and the reported p-values and CI widths. Reachability under default workflow: the harness generates `run_id = f"{condition}_{run_idx:02d}_{uuid4().hex[:6]}"` → five distinct suffixes per condition with ~16.7M-value collision space → pseudo-replication is NOT reachable under the default workflow without a separate bug. Reachable via: (a) #153 collision at scale (all 5 run_ids per condition collide — extremely unlikely, ~(5 choose 2)² / 2²⁴ ≈ 10⁻¹²); (b) #169 overfull passage (#153 collision + selector admits, 1 run_id absorbs multiple logical runs); (c) `--append` re-run with same run_id repeatedly; (d) harness-level pseudo-replication bug (would be surfaced by other checks, but not by this one). Reviewer submitted P0; **downgraded to HIGH defense-in-depth**, consistent with #152 / #157 severity framing (same structural antipattern — count-only completeness gate on the run-id axis, reachable only under contract violations or compounding bugs). Fix: add one assertion pairing the existing `expected_runs` flag with a `COUNT(DISTINCT run_id) per condition` query:
   ```python
   # ext15 #171: distinct run-id cardinality gate. Pseudo-replicated data
   # passes ext7 #52 + ext13 #131 via per-(condition, task_id) uniformity
   # without tripping an independence-assumption check. Land alongside
   # the existing uniformity gate.
   per_cond_distinct_runs = conn.execute(
       f"SELECT condition, COUNT(DISTINCT run_id) "
       f"FROM evaluation_runs WHERE is_pilot = FALSE{_SCOPE_SQL} "
       f"GROUP BY condition"
   ).fetchall()
   for condition, n_runs in per_cond_distinct_runs:
       if n_runs != expected_runs:
           issues.append(
               f"condition '{condition}': {n_runs} distinct run_ids, "
               f"expected {expected_runs} (pseudo-replication risk; "
               f"M1 p-values assume row-level independence)"
           )
   ```
   Closes the gap regardless of cause. Symmetric to #152 + #157 canonical-identity checks.
- **#172 — `analyze_longitudinal.py` completeness gate uses `len(cycles) < expected_cycles` strict inequality (shortfall only); overfull merged timelines silently pass (P1 confirmed):** `scripts/analyze_longitudinal.py:746-757`. The ext12 #116 completeness guard was designed for "aborted stub" detection but uses shortfall-only semantics: `if len(cycles) < args.expected_cycles:` fails loud → exit 3 unless `--allow-incomplete`; `len(cycles) > args.expected_cycles` passes silently. Overfull reachable via (a) #153 run_id collision between two longitudinal runs sharing a `run_id` (~10⁻⁵ at realistic scale), (b) duplicate writes from retry logic, (c) `--append` re-run with same `run_id`. Merged timeline produces invalid convergence curves (KL divergence computed across two logical runs' interleaved cycles), invalid cycles-to-convergence (measured on mixed sequences), invalid distribution-shift adaptation verdict (Phase A/B detection confused by merged cycles). Same asymmetric-check antipattern as #169 on the longitudinal-analyzer surface. Fix: `if len(cycles) != args.expected_cycles` (strict equality) with bidirectional error messaging ("too few" / "too many — possible merged run_id"). Chapter 6 RQ #2 + RQ #3 outputs depend on this gate producing a loud signal when the timeline is corrupted.
- **#173 — Row 18/19 selectors (`LONGITUDINAL_RUN_ID`, `SHIFT_RUN_ID`) use `HAVING COUNT(*) >= EXPECTED_LONGITUDINAL_ROWS` admitting overfull merged run_ids (P1 confirmed):** `docs/run_sheet.md:142-169`. Both longitudinal selectors use `HAVING COUNT(*) >= ? ORDER BY MAX(timestamp) DESC LIMIT 1` — sibling pattern to #169 on the Phase-4 selector, same upper-bound blind spot. Combined with #172 (`analyze_longitudinal.py`'s shortfall-only gate), merged timelines can flow end-to-end from selector → analyzer → Row 18/19 Chapter 6 output without a structural stop. Symmetric fix: `HAVING COUNT(*) = ? ORDER BY MAX(timestamp) DESC LIMIT 1` strict equality; re-use `#169` lint test pattern to enforce. Land #172 + #173 together (selector + analyzer both need the upper bound).

**Status at time of write (2026-04-22):** twenty-three **open**. Total open across audit series: ext8 (2) + ext10 (1 latent) + ext15 (23) = **~26 findings**.

| # | Severity | Scope | Impact | Status |
|---|---|---|---|---|
| 151 | HIGH (defense-in-depth) | `docs/run_sheet.md:87-116` CAMPAIGN_RUN_IDS discovery SQL has a completeness filter (ext14 #145) but no per-condition recency filter and no campaign-identity check; inconsistent with `docs/run_sheet.md:142-155` longitudinal selectors which rank `ORDER BY MAX(timestamp) DESC LIMIT 1` | Rows 14 (`analyze_compression_loss.py`) + 16 (`audit_contamination.py`) inherit whatever `$CAMPAIGN_RUN_IDS` exports and lack row 17's total/per-condition/per-(condition,task) uniformity gate. A DB with two appended 5-run campaigns would silently pool 40 complete run_ids into rows 14/16 without a loud failure. Row 17 uniformity check catches this at exit 2 on the common shape; rows 14/16 silently mix populations. Requires `--append` misuse against ext7 #49's DB-per-campaign contract to reach — not reproducible under the default run-sheet workflow. | ❐ OPEN |
| 152 | HIGH (defense-in-depth) | `scripts/analyze_results.py:1605-1626` completeness gate checks `COUNT(DISTINCT task_id)` + per-(condition, task_id) uniformity but never asserts the observed task_id set equals canonical `{T01..T20}` from `tests/evaluation/tasks.py:1402-1405` | A drifted 20-task_id set (e.g., cross-iteration T01..T15 + T21..T25 appended, or a harness code edit swapping T05) passes the count-and-uniformity gate while silently biasing Chapter 6 aggregates against the proposal-declared testbed. Requires code edit to `ALL_TASKS` mid-campaign or `--append` of a cross-iteration DB to reach. Extends ext7 #52 + ext13 #131 from count semantics to identity semantics. | ❐ OPEN |
| 153 | P1 (latent) | `tests/evaluation/harness.py:302` run_id uses `uuid.uuid4().hex[:6]` (24-bit suffix per condition/run_idx slot); `src/registry/evaluation_store.py:23-27` schema has no uniqueness constraint on `run_id` | Birthday collision probability ~0.15% per 10-campaign `--append`-chained DB. On collision, two logical runs' rows share a `run_id` grouping key; every downstream analyzer that groups by `run_id` (ext11 #111, row 17 uniformity, Gini per-run aggregation) silently pools them. Low probability but unguarded structurally. Fix is local: bump suffix entropy to full `uuid4().hex` or add `(run_id, condition)` uniqueness constraint at `insert_record`. | ❐ OPEN |
| 154 | **P0 CRITICAL (confirmed)** | `scripts/analyze_tier2_probes.py:504` defaults `event_names=(_TIER2_EVENT_REJECT,)`; line 544-559 ext14 #139 gate requires every `--session-ids` entry to contribute ≥1 fetched event | Healthy sessions where Tier 2 passed every probe (the winning outcome) contribute zero reject events → fail the gate at exit 4. Affects **full + no_optimization** (JIT conditions) when they're clean; affects static + no_verification unconditionally (they never emit Tier 2 events at all — see #155). On a canonical 20-session campaign, Row 15 can only succeed today if (a) at least some full/no_optimization session had Tier 2 rejections AND (b) user drops static + no_verification from scope AND (c) gate passes on the survivors. Under default run-sheet scope, Row 15 returns exit 4 on **every** canonical campaign unless `--allow-partial-sessions` is passed, which unconditionally triggers Chapter 4.5 disclosure for what is a clean run. | ❐ OPEN |
| 155 | **P0 CRITICAL (confirmed)** | `docs/run_sheet.md:121` sets `CAMPAIGN_SESSION_IDS="$CAMPAIGN_RUN_IDS"` (all 20 run_ids). `tests/evaluation/harness.py:531-543` + line 710: `static` dispatches to `_run_static` (no JIT, no verification calls); `no_verification` sets `verification_enabled=False` (gate bypassed). Both emit zero `verification.tier_2.*` events by design | Row 15 (`analyze_tier2_probes.py --session-ids $CAMPAIGN_SESSION_IDS`) receives 10 structurally-zero sessions (5 × static + 5 × no_verification) out of 20. Under ext14 #139's strict-per-session gate, exit 4 fires unconditionally on every canonical campaign with `10/20 requested session IDs contributed zero events`. Blocks Row 15 on default workflow; stacks with #154 on remaining 10 JIT sessions. | ❐ OPEN |
| 156 | **P1 (confirmed)** | `scripts/analyze_context_utilization.py:55-58` queries 4 DeepAgent trace names; `tests/evaluation/harness.py:_run_static` invokes `_run_static_implementation` with no DeepAgent calls → static sessions produce 0 traces under any queried name. `docs/run_sheet.md:121` uses `CAMPAIGN_SESSION_IDS="$CAMPAIGN_RUN_IDS"` | Row 13 (`analyze_context_utilization.py --session-ids $CAMPAIGN_SESSION_IDS`) receives 5 static sessions that cannot emit any of the 4 queried trace names. Under ext14 #139's strict-per-session gate, exit 4 fires with `5/20 requested session IDs contributed zero rows` on every canonical campaign. Narrower scope than #155 (no_verification still emits 3 of 4 agent trace names, counts as contributed); only static fully fails. | ❐ OPEN |
| 157 | HIGH (defense-in-depth, downgraded from P0) | `scripts/analyze_results.py:1593-1599` missing-condition check is gated by `if len(condition_map) < expected_conditions` — identity verified only when count is too low; `CONDITIONS` at `scripts/analyze_results.py:32` has canonical set but no set-equality assertion on `condition_map` identities | A campaign with 4 conditions × 100 rows but one canonical condition replaced by a non-canonical label (static → "weird_new") passes the gate silently. Mirror of #152 on the condition axis. Reachable only under contract violation: `BaselineCondition` enum code edit (caught by `git diff`) or `--append` of a cross-iteration DB with renamed conditions. | ❐ OPEN |
| 158 | **P1 (confirmed)** | `scripts/audit_contamination.py:681-699` ext14 #143 empty-sample fallback uses only `run_id IN (...) AND NOT is_pilot`; the main pool query at line 629 uses six additional filters (`conditions`, `types`, `generated_artifact_count > 0`, `human_label IS NULL`, `since`) | A typo'd `--conditions nonexistent` (or `--types wrong_type`, etc.) → pool empty → fallback counts all rows for run_ids ignoring the bad filter → returns non-zero → prints "all in-scope rows already labeled" → exit 0. False-green on filter mismatch. Reachable under default workflow whenever a non-`--run-ids` filter is typo'd. | ❐ OPEN |
| 159 | **P1 (confirmed)** | `scripts/analyze_results.py:1332-1448` `print_contamination_rate` pooled aggregation at 1402-1412 sums across every `GROUP BY condition` row with no whitelist; coverage check at 1437-1448 is asymmetric — computes `missing = expected - present` but never `extra = present - expected` | Extra condition labels (e.g., `longitudinal` from cross-iteration `--append`, future-ablation conditions, enum drift) silently enter pooled M2 numerator/denominator. Reachable under same contract-violation paths as #151/#152/#157. Pure asymmetric-check bug: one set-diff on the extra-side closes it. | ❐ OPEN |
| 160 | **P0 CRITICAL (confirmed)** | `scripts/analyze_results.py:785-793` ext14 #149 M4 silent-zero gate uses `max(jit_ratios) < 0.5` — fires only when every JIT condition is below 50% non-zero-cost threshold | One healthy JIT condition (ratio 1.0) masks two broken ones (ratio 0.0). Row 11 partial failure (ext14 #141 pagination on subset, ext12 #122 zero-generations, #161 per-session truncation, partial CAMPAIGN_RUN_IDS) produces ratios like [1.0, 0.0, 0.0] → `max = 1.0` → gate passes → Chapter 6 M4 table prints `full: X / no_verification: 0 / no_optimization: 0` → concludes infinite cost advantage on corrupted data. Default-workflow-reachable under realistic Row 11 partial-failure. Same asymmetric-check antipattern as #152/#157/#159 applied to M4 axis. | ❐ OPEN |
| 161 | **P1 (confirmed)** | `scripts/backfill_token_costs.py:520-556` `fetch_traces_by_session` breaks at `page > 200` with no warning log, no `truncated` return flag; coverage gate at line 685-690 checks per-key presence not per-key completeness | Silent 20k-traces-per-session pagination truncation undercounts per-task token totals under AGENT-heavy workloads. Coverage gate sees truncated keys as present → slips through. Written `token_cost` values on `evaluation_runs` biased low for affected tasks; Row 17 M4 averages understated. ext14 #141 sweep fixed sibling `fetch_all_generations` but missed this function. Retrofit ext14 #140 pattern (return `(records, truncated: bool)` tuple + WARNING + caller-side exit-5 gate). | ❐ OPEN |
| 162 | **P0 CRITICAL (confirmed, escalated from unlabeled)** | `scripts/analyze_results.py:935-982` `print_gini_summary`. Line 937-940 appends raw per-(run, capability) counts into `by_condition[cond]`; line 981 runs `_gini_coefficient(by_condition[cond])` and prints under "Aggregate" column; header at 966-968 claims "all invocations across runs in that condition treated as one distribution" | M3 Aggregate Gini header and compute diverge: compute treats each (run, cap) as a datapoint, not pooled-by-capability totals. Permutation-symmetric workloads (cap A dominates run 1, cap B dominates run 2) yield true-pooled Gini=0 vs current compute ≈0.375+. Chapter 6 M3 aggregate numbers can be directionally wrong even when per-run means look reasonable. Default-workflow-reachable; headline metric corruption. | ❐ OPEN |
| 163 | **P1 (confirmed)** | `scripts/analyze_results.py` five label sites out of sync with compute scope: line 4 (module docstring), line 634 (cluster print), line 1651 (Table 1 title), line 1653 (comment), line 1657 (Table 2 title) still cite T04-T13 / T04-T18 / T01-T18 after ext6 #17 extended compute to T01-T20 / T04-T20 | Chapter 6 readers see T04-T18 / T04-T13 labels but math is on T04-T20 (17 SYNTHESIS tasks): denominators, p-values, cluster gaps all computed on the full set while labels misrepresent the population. Label bug, not compute bug. Easy to miss post-ext6-#17. | ❐ OPEN |
| 164 | **P1 (confirmed)** | `scripts/analyze_results.py:1047-1145` `print_classification_accuracy` returns `None` on three skip paths (import failure at 1111-1119, no `expected_type` at 1123-1129, no JIT rows at 1139-1145); main() at line 1696 ignores return, `missing_metrics` at 1701 has no entry for it | Row 17 silently exits 0 when Pillar 1 RQ #1 classification-accuracy section is skipped. Three siblings (M2, M3, M4) retrofitted into `-> bool` + missing-metrics exit-7 pattern via ext13 #126/#128/#132 + ext14 #149; Step 18.5's Classification Accuracy was not. Chapter 6 reader sees a `"(skipped: ...)"` line but no loud error. | ❐ OPEN |
| 165 | **P1 (confirmed)** | `scripts/backfill_token_costs.py:224-293, 907-908` `map_generations_to_runs` returns `(run_to_gens, unmatched)`; main prints `unmatched` but never integrity-gates it | Complement to ext12 #122's per-run zero-match gate is missing: generations that matched no run window at all are silently dropped. Non-trivial unmatched fraction biases M4 tokens downward while every per-run gate passes (each run retains matches). Unmatched sources: ext10 #105 30s OVERLAP_GUARD zone, ±120s buffer edges, >60s LangFuse/harness clock skew. Chapter 6 M4 understates per-task cost with no override banner. Compounds with #161 (pagination truncation). | ❐ OPEN |
| 166 | **P1 (confirmed)** | `scripts/analyze_results.py:1131-1195` `print_classification_accuracy` SQL uses `condition != 'static'` blacklist; line 1194 confusion-matrix header says "aggregated over JIT conditions" but aggregation at 1161 has no JIT filter | Any non-baseline condition label (`longitudinal`, future ablations, enum drift) silently enters confusion matrix cells and the Pillar 1 RQ #1 proxy headline. Per-condition table at 1175-1184 displays only canonical JIT trio correctly; confusion-matrix compute diverges from displayed header. Same label-vs-compute desync family as #159/#163/#164. Reachable under same contract-violation paths as #151/#152/#157/#159. | ❐ OPEN |
| 167 | **P1 (confirmed)** | `scripts/backfill_token_costs.py:762-816` parser lacks `--run-ids` / `--since` CLI args; `fetch_run_windows` (161-173), task_rows fetch (176-178), `get_per_record_info` (200-206) all use `WHERE is_pilot = FALSE` with no scope. `docs/run_sheet.md:178` Row 11 invocation passes only `--db` | Run-sheet contract at `docs/run_sheet.md:70` — "every Row reading metrics.duckdb MUST scope by campaign's run_id(s)" — is structurally unsatisfiable by Row 11. Under ext7 #49 DB-per-campaign contract the impact is latent; under `--append` misuse, all non-pilot runs across campaigns drive LangFuse fetch window and per-condition coverage gate, forcing false ext12 #122 exit 3 / degraded-override outcomes on an otherwise-clean current campaign. Compounds with #165 (wider fetch → higher unmatched rate). | ❐ OPEN |
| 168 | **P1 (confirmed)** | `docs/run_sheet.md:182` Row 15 cell: first prose sentence says "Reads `verification.tier_2.{pass,reject}` LangFuse events"; command omits `--include-pass`; `scripts/analyze_tier2_probes.py:504` default `event_names=(reject,)` runs reject-only | Chapter 4.3 denominator semantics ambiguous: reject-only gives detection rate per category; pass+reject gives total probe executions per category — different numbers. Cell's second sentence ("of the N Tier 2 rejections, which probe types caught them?") matches command but contradicts the first sentence. Same label-vs-compute antipattern as #159/#163/#166. Fixing by adding `--include-pass` also sidesteps #154's coverage-gate collision. | ❐ OPEN |
| 169 | **P1 (confirmed)** | `docs/run_sheet.md:95-108` CAMPAIGN_RUN_IDS selector uses `HAVING COUNT(*) >= :expected_rows` (lower bound only) and dropped-report branch uses `COUNT(*) < :expected` (underfull only); both admit overfull run_ids silently | Overfull run_ids from #153 collisions / duplicate writes / `--append` re-use silently enter `$CAMPAIGN_RUN_IDS`. Rows 14 (`analyze_compression_loss.py`) + 16 (`audit_contamination.py`) inherit them — both lack row 17's ext13 #131 uniformity gate. Row 17 catches via per-(condition, task_id) uniformity at exit 2 on that specific path, but rows 14/16 silently merge populations. Symmetric fix: `COUNT(*) = :expected_rows` + bidirectional overfull/underfull dropped-report. | ❐ OPEN |
| 170 | **P0 CRITICAL (confirmed, escalated from unlabeled)** | `scripts/backfill_token_costs.py:69-147` `fetch_all_generations` params at 101-110 carry only `type=GENERATION`, `limit`, `page`, `fromStartTime`, `toStartTime` — no `sessionId` / `environment` / `tags` filter; `map_generations_to_runs` at 224 attributes by temporal proximity (±120s clipped buffer) | Every foreign LangFuse GENERATION landing in the campaign time window is fetched and attributed to the nearest campaign run by temporal proximity: concurrent CLI runs on `environment="default"`, dev-script runs on `environment="test"`, pilot-run (Row 7) generations, other users on shared LangFuse instances. Inflates M4 `token_cost` HIGH on campaign rows → Row 17 M4 over-reports per-task cost. All existing coverage gates are UNDER-count detectors; none catch OVER-count from foreign ingestion. Default-workflow-reachable without contract violation. | ❐ OPEN |
| 171 | HIGH (defense-in-depth, downgraded from P0) | `scripts/analyze_results.py:1573-1626` completeness gate checks total/per-condition row counts + per-condition distinct task_ids + per-(condition, task_id) uniformity, but never `COUNT(DISTINCT run_id) per condition = :expected_runs_per_condition` | Pseudo-replicated DB (1 run_id × 20 tasks × N duplicates per task, per condition) satisfies all four existing gates while violating row-level independence assumption behind Fisher / Holm / Clopper-Pearson. M1 p-values + CI widths invalid. Reachable only via #153-collision-at-scale / #169 overfull passage / `--append` run_id re-use / harness-level pseudo-replication bug. | ❐ OPEN |
| 172 | **P1 (confirmed)** | `scripts/analyze_longitudinal.py:746-757` completeness gate uses `if len(cycles) < args.expected_cycles` — strict inequality on shortfall side only | Overfull merged timelines (from #153 collision / duplicate writes / `--append` re-use of longitudinal `run_id`) pass silently. Convergence / KL / cycles-to-convergence / distribution-shift adaptation metrics computed on merged sequences invalid. Chapter 6 RQ #2 + RQ #3 outputs corrupted without loud signal. | ❐ OPEN |
| 173 | **P1 (confirmed)** | `docs/run_sheet.md:142-169` Row 18/19 selectors (`LONGITUDINAL_RUN_ID`, `SHIFT_RUN_ID`) use `HAVING COUNT(*) >= EXPECTED_LONGITUDINAL_ROWS` lower-bound only | Overfull merged longitudinal run_ids admitted as "latest complete" → Row 18/19 operate on merged timeline. Stacks with #172 (analyzer's shortfall-only gate). Sibling to #169 on longitudinal DB family; same symmetric-fix pattern (`COUNT(*) = :expected_rows`). | ❐ OPEN |

---

## Issue #151 — CAMPAIGN_RUN_IDS selector lacks per-condition recency ranking; rows 14/16 inherit any cross-campaign pooling the selector admits (HIGH, defense-in-depth)

### Summary

`docs/run_sheet.md:87-116` populates `$CAMPAIGN_RUN_IDS` dynamically from `$CAMPAIGN_DB` with a completeness filter (ext14 #145) but no recency or campaign-identity filter:

```sql
SELECT run_id, COUNT(*) AS n_rows, COUNT(DISTINCT task_id) AS n_tasks
FROM evaluation_runs
WHERE NOT is_pilot
GROUP BY run_id
HAVING COUNT(*) >= ${EXPECTED_ROWS_PER_RUN}
   AND COUNT(DISTINCT task_id) = ${EXPECTED_TASKS_PER_RUN}
ORDER BY run_id
```

The run_id format is `{condition}_{run_idx:02d}_{uuid.uuid4().hex[:6]}` (`tests/evaluation/harness.py:302`) — it carries no campaign identifier. If a DB contains complete run_ids from two distinct campaigns (e.g., via `--append` misuse or a pre-ext7-#49 legacy DB), the selector pools them.

### Defense-in-depth context (why HIGH, not P0)

The finding was submitted as P0. Verification against the full defense chain downgrades it to HIGH. Four layers of defense need to fail simultaneously for a campaign-mixing scenario to corrupt Chapter 6 aggregates:

1. **ext7 #49 DB-per-campaign isolation** (`scripts/run_pilot.py:241-262`): the default `--db` path is a fresh timestamped `metrics_<timestamp>.duckdb`; opening an existing non-empty file exits with code 2 unless `--fresh-db` or `--append` is passed explicitly. `--append`'s documented semantics are "resume a campaign with a different `--condition`" — not "pool unrelated campaigns." Under the default workflow, one DB == one campaign.
2. **ext14 #145 completeness filter** (`docs/run_sheet.md:91-97`): `HAVING COUNT(*) >= :expected_rows AND COUNT(DISTINCT task_id) = :expected_tasks` drops partial/aborted run_ids before they enter `$CAMPAIGN_RUN_IDS`.
3. **ext7 #52 + ext13 #131 row 17 uniformity gate** (`scripts/analyze_results.py:1586-1626`): asserts total row count, per-condition count, per-(condition, task_id) uniformity against `--expected-runs-per-condition`. Mixing two `--n-runs 5` campaigns → per-(condition, task_id) = 10, expected 5 → `uneven` fires → exit 2. Row 17 is already defended.
4. **(MISSING)** per-condition recency filter on the selector itself, equivalent to the ext11 #111 longitudinal-selector pattern.

Layers 1-3 are the primary defense. Layer 4 closes a residual defense-in-depth gap:

- **Rows 14 + 16 do not replicate row 17's uniformity triple-check.** `scripts/analyze_compression_loss.py` and `scripts/audit_contamination.py` accept whatever `--run-ids` they're handed and operate on the matching rows. If `$CAMPAIGN_RUN_IDS` ever pooled two campaigns, rows 14/16 would silently emit cross-campaign aggregates while row 17 caught the same input at exit 2.
- **The Phase-4 selector is inconsistent with its own sibling.** The longitudinal selectors at lines 142-155 + 156-169 rank `ORDER BY MAX(timestamp) DESC LIMIT 1` per ext11 #111. The Phase-4 selector at lines 87-116 does not. Harmonizing both to the `MAX(timestamp)` pattern removes a reviewer-facing inconsistency and tightens the defense chain without new dependencies.

### Reproduction (contract-violating scenario required)

The finding is **not reachable** under the default run-sheet workflow. To reproduce the residual risk, the ext7 #49 contract must be explicitly violated:

```bash
# Campaign A — 4 conditions × 5 runs × 20 tasks = 400 rows
export CAMPAIGN_DB_A="data/eval/metrics_$(date +%Y-%m-%d_%H%M%S)_A.duckdb"
uv run scripts/run_pilot.py --db "$CAMPAIGN_DB_A" --all-tasks --all-conditions --n-runs 5
# ... Campaign A completes, 20 complete run_ids ...

# CONTRACT VIOLATION: explicit --append of an unrelated campaign into the same DB
uv run scripts/run_pilot.py --db "$CAMPAIGN_DB_A" --append --all-tasks --all-conditions --n-runs 5
# $CAMPAIGN_DB_A now contains 40 complete run_ids from two campaigns

# Phase-4/5 scoping — ext14 #145 completeness filter passes all 40
export CAMPAIGN_DB="$CAMPAIGN_DB_A"
# ... CAMPAIGN_RUN_IDS = 40 run_ids ...

# Row 17 — ext7 #52 + ext13 #131 CATCH the mix (per-condition count = 200, expected 100):
uv run scripts/analyze_results.py --db "$CAMPAIGN_DB" --run-ids $CAMPAIGN_RUN_IDS
# → exits 2 with "condition 'full': 200 rows, expected 100"

# Rows 14 + 16 — SILENT mixing, no equivalent gate:
uv run scripts/analyze_compression_loss.py --db "$CAMPAIGN_DB" --run-ids $CAMPAIGN_RUN_IDS
# → processes all 40 run_ids, emits cross-campaign aggregate, exit 0
uv run scripts/audit_contamination.py --db "$CAMPAIGN_DB" --run-ids $CAMPAIGN_RUN_IDS
# → samples across all 40 run_ids, exit 0
```

Under the default workflow (fresh timestamped `$CAMPAIGN_DB` per campaign; no `--append`), the reproduction cannot occur. The finding is a defense-in-depth hardening, not an active integrity gap.

### Proposed fix

Replace the Phase-4 selector at `docs/run_sheet.md:87-116` with a per-condition recency-ranked selector that harmonizes with the ext11 #111 longitudinal pattern:

```sql
-- Per-condition recency: latest :expected_runs_per_condition run_ids per condition
-- ranked by MAX(timestamp). Harmonizes with ext11 #111 longitudinal selectors.
-- Defense-in-depth: if a DB ever contains mixed-campaign rows (via --append
-- misuse against ext7 #49), only the most-recent run_ids per condition enter
-- $CAMPAIGN_RUN_IDS. Rows 14 + 16 inherit the sharper scope and no longer need
-- to replicate row 17's uniformity gate for the common mixing shape.
WITH ranked AS (
  SELECT
    run_id,
    condition,
    MAX(timestamp) AS latest,
    COUNT(*) AS n_rows,
    COUNT(DISTINCT task_id) AS n_tasks,
    ROW_NUMBER() OVER (
      PARTITION BY condition ORDER BY MAX(timestamp) DESC
    ) AS rn
  FROM evaluation_runs
  WHERE NOT is_pilot
  GROUP BY run_id, condition
  HAVING COUNT(*) >= :expected_rows_per_run
     AND COUNT(DISTINCT task_id) = :expected_tasks
)
SELECT run_id
FROM ranked
WHERE rn <= :expected_runs_per_condition
ORDER BY condition, rn;
```

Expected-runs-per-condition lands alongside the existing `EXPECTED_ROWS_PER_RUN` / `EXPECTED_TASKS_PER_RUN` env vars (default `5`, matches `--n-runs`). If the DB contains fewer than `expected_conditions × expected_runs_per_condition` complete run_ids, the exit path mirrors ext14 #145: stderr warning + `SystemExit` on zero run_ids.

### Proposed test (run-sheet lint)

Add to `tests/unit/test_run_sheet.py` alongside the existing ext14 #145 `HAVING COUNT(` assertion:

```python
def test_campaign_run_ids_discovery_uses_max_timestamp_recency_ranking():
    """ext15 #151 defense-in-depth: CAMPAIGN_RUN_IDS discovery must rank by
    MAX(timestamp) per condition, matching the ext11 #111 longitudinal selector
    pattern. A DB that ever pools mixed-campaign rows (via --append misuse
    against ext7 #49) must not contribute cross-campaign run_ids to rows 14/16
    which lack row 17's total/per-condition/per-(condition,task) uniformity gate.
    """
    text = RUN_SHEET.read_text()
    # Either the window-function form or an explicit per-condition MAX(timestamp)
    # correlated-subquery form satisfies the invariant.
    assert (
        "PARTITION BY condition ORDER BY MAX(timestamp) DESC" in text
        or "ROW_NUMBER() OVER" in text and "MAX(timestamp) DESC" in text
    ), (
        "ext15 #151: CAMPAIGN_RUN_IDS discovery SQL must rank by MAX(timestamp) "
        "per condition so partial cross-campaign run_ids (from --append misuse) "
        "do not silently enter rows 14/16. Harmonizes with ext11 #111 "
        "longitudinal selectors at docs/run_sheet.md:142-155 + 156-169."
    )
    # Completeness filter (ext14 #145) stays — the two invariants are orthogonal.
    assert "HAVING COUNT(" in text, (
        "ext14 #145 completeness filter regressed — see test_scoping_convention_"
        "uses_dynamic_discovery_not_fixed_literal."
    )
```

### Why HIGH (not P0), restated

The reviewer's severity reasoning — "can silently mix campaigns" — is technically correct at the selector level but does not account for the ext7 #49 contract (primary defense) or the ext7 #52 + ext13 #131 row 17 uniformity gate (secondary defense). P0 in this audit series is reserved for findings that produce corrupted Chapter 6 output under the default workflow. #151 requires an explicit contract violation to reach, and even then, row 17 catches the common mixing shape at exit 2. Rows 14 + 16 are the residual exposure — HIGH is the correct severity for a reviewer-facing consistency gap that hardens two analyzers against a contract-violation scenario.

Upgrade path to P0 would require either: (a) evidence that rows 14/16 can produce biased Chapter 6 output under the default workflow, or (b) removal of the ext7 #49 / ext7 #52 / ext13 #131 defenses.

### Validation against related findings

- **ext7 #46** (`analyze_results.py` per-condition auto-selector): already uses `MAX(timestamp) DESC` when `--run-ids` is not passed. #151 aligns the `$CAMPAIGN_RUN_IDS` path with the auto-selector so the two scope paths produce the same run_id set on a clean campaign DB.
- **ext11 #111** (longitudinal selectors): already uses `GROUP BY run_id ... ORDER BY MAX(timestamp) DESC LIMIT 1`. #151 extends the pattern to the Phase-4 selector.
- **ext12 #117** (longitudinal completeness filter): already uses `HAVING COUNT(*) >= :expected_rows`. #151 preserves the filter in the Phase-4 selector (ext14 #145) and adds per-condition recency on top.
- **ext14 #145** (Phase-4 completeness filter): current baseline. #151 stacks recency ranking on top without removing the completeness filter.

The fix is pure doc-only + one lint test; no production-code changes. Risk: low. Blast radius: one `docs/run_sheet.md` SQL block + one `tests/unit/test_run_sheet.py` test.

---

---

## Issue #152 — Row 17 completeness gate checks task_id count + uniformity but not canonical T01..T20 identity; drifted-set campaigns pass silently (HIGH, defense-in-depth)

### Summary

`scripts/analyze_results.py:1605-1626` (the ext13 #131 extension of the ext7 #52 completeness gate) enforces:

```python
per_cond_task_coverage = conn.execute(
    f"SELECT condition, COUNT(DISTINCT task_id) "
    f"FROM evaluation_runs WHERE is_pilot = FALSE{_SCOPE_SQL} "
    f"GROUP BY condition"
).fetchall()
for condition, distinct_tasks in per_cond_task_coverage:
    if distinct_tasks != expected_tasks:
        issues.append(f"condition '{condition}': {distinct_tasks} distinct tasks, expected {expected_tasks}")

uneven = conn.execute(
    f"SELECT condition, task_id, COUNT(*) AS n "
    f"FROM evaluation_runs WHERE is_pilot = FALSE{_SCOPE_SQL} "
    f"GROUP BY condition, task_id HAVING COUNT(*) != ?",
    [expected_runs],
).fetchall()
```

Both checks are **count-only**. They do not compare the observed task_id set against the canonical Chapter 6 testbed `{T01, T02, …, T20}` defined at `tests/evaluation/tasks.py:1402-1405`:

```python
ALL_TASKS: list[TaskDefinition] = [
    T01, T02, T03, T04, T05, T06, T07, T08, T09, T10,
    T11, T12, T13, T14, T15, T16, T17, T18, T19, T20,
]
```

A drifted set that still has 20 distinct task_ids per condition and 5 rows per (condition, task_id) passes the gate silently — Chapter 6 aggregates are then computed over a non-canonical task distribution while the header reports "complete."

### Defense-in-depth context (why HIGH, not P0)

Two viable drift paths exist; both require contract violation:

1. **Code edit to `ALL_TASKS`**: a thesis reviewer modifying `tests/evaluation/tasks.py` to swap T05 for a new T05b (or adding T21 and removing T20) produces a valid 20-task set that is not the proposal-declared testbed. The completeness gate passes; Chapter 4.5 disclosure never fires because the gate doesn't surface the drift. **Mitigated today by:** `tests/evaluation/tasks.py` is source-controlled; drift would show in `git diff`. Not mitigated by the gate itself.
2. **`--append` of a cross-iteration DB**: a prior iteration's campaign DB used a different task set (iteration 5 rumor: T01..T15 + T21..T25). Appended into the current iteration's DB via ext7 #49 contract violation, the union has >20 distinct task_ids, so the `COUNT(DISTINCT task_id) == expected_tasks` check fails → exit 2. **This path is caught.** Where it slips through: if the appended campaign's task set exactly matches the canonical set in size but not in identity (a hypothetical iteration that used 20 task_ids not overlapping with T01..T20), the gate passes.

The ext7 #49 DB-per-campaign contract + `tests/evaluation/tasks.py` source control together provide primary defense. The residual gap is: **the gate does not encode the canonical testbed identity**, so any future iteration that authors 20 tasks under a different labeling convention would silently pass.

Severity: HIGH defense-in-depth, not P0. Reaching the corruption requires either a code edit (caught by `git diff`, not the gate) or a specific appended-campaign shape (partially caught by the existing gate; edge case slips through). P0 in this series is reserved for findings reachable under the default workflow.

### Proposed fix

Extend the ext7 #52 / ext13 #131 gate with a canonical-set assertion:

```python
# ext15 #152: extend ext13 #131's count + uniformity gate with canonical
# task-identity assertion. A drifted 20-task_id set passes count checks
# while biasing Chapter 6 against the proposal-declared testbed.
from tests.evaluation.tasks import ALL_TASKS
_CANONICAL_TASK_IDS = {t.task_id for t in ALL_TASKS}

per_cond_task_ids = conn.execute(
    f"SELECT condition, task_id FROM evaluation_runs "
    f"WHERE is_pilot = FALSE{_SCOPE_SQL} "
    f"GROUP BY condition, task_id"
).fetchall()
per_cond_observed: dict[str, set[str]] = {}
for condition, task_id in per_cond_task_ids:
    per_cond_observed.setdefault(condition, set()).add(task_id)

for condition, observed in per_cond_observed.items():
    extra = observed - _CANONICAL_TASK_IDS
    missing = _CANONICAL_TASK_IDS - observed
    if extra:
        issues.append(
            f"condition '{condition}': non-canonical task_ids "
            f"{sorted(extra)} (not in ALL_TASKS)"
        )
    if missing:
        issues.append(
            f"condition '{condition}': missing canonical task_ids "
            f"{sorted(missing)} (in ALL_TASKS, not in DB)"
        )
```

Override flag: `--allow-incomplete` (existing, from ext7 #52) covers this too — identity drift is a shape of incompleteness.

### Proposed test

Add to `tests/unit/test_analyze_results.py` (or the sibling e2e file covering ext13 #131):

```python
def test_completeness_gate_rejects_non_canonical_task_ids():
    """ext15 #152: the gate must reject a DB with 20 distinct task_ids
    per condition where the set is not {T01..T20}. Count-only semantics
    pass drifted-set campaigns that bias Chapter 6.
    """
    # Populate 400 rows: 4 conditions × 5 runs × 20 task_ids, but swap
    # T20 for a synthetic "TX1" to simulate cross-iteration drift.
    drifted_task_ids = {f"T{i:02d}" for i in range(1, 20)} | {"TX1"}
    _populate_evaluation_runs(conn, task_ids=drifted_task_ids, ...)

    rc = _run_main(["--run-ids", *all_run_ids])

    assert rc == 2, "drifted task_id set must fail the canonical-identity gate"
    assert "non-canonical task_ids" in captured.err
    assert "TX1" in captured.err
    assert "T20" in captured.err
```

The fix is scoped: one extension to `analyze_results.py::main`'s issue-accumulation loop + one unit test. No run_sheet changes needed.

---

## Issue #153 — run_id uses 24-bit random suffix with no schema uniqueness constraint; `--append`-chained campaigns have ~0.15% per-DB collision probability (P1 latent)

### Summary

Two facts combine to form the latent risk:

**Fact 1 — run_id format** (`tests/evaluation/harness.py:302`):
```python
run_id = f"{condition}_{run_idx:02d}_{uuid.uuid4().hex[:6]}"
```
The random component is 6 hex digits = 24 bits = ~16.7M values. Per (condition, run_idx) slot, the effective collision space is 24 bits.

**Fact 2 — schema has no uniqueness constraint on run_id** (`src/registry/evaluation_store.py:23-57`):
```sql
CREATE TABLE IF NOT EXISTS evaluation_runs (
    id                        VARCHAR PRIMARY KEY,   -- row-level UUID
    run_id                    VARCHAR NOT NULL,      -- no UNIQUE constraint
    condition                 VARCHAR NOT NULL,
    task_id                   VARCHAR NOT NULL,
    ...
);
```
`id` is PK; `run_id` is just `VARCHAR NOT NULL`. Two campaigns that happen to draw the same `hex[:6]` suffix for the same `(condition, run_idx)` slot will insert successfully; downstream `GROUP BY run_id` pools their rows.

### Collision math

Per-campaign shape: 4 conditions × 5 run_idx = 20 distinct (condition, run_idx) slots; each slot draws one 24-bit random suffix. A single campaign fills 20 slots once each.

Across `K` `--append`-chained campaigns on the same DB, each slot receives `K` draws from 2²⁴. Birthday collision probability per slot (ignoring structure):

- `K=5`: ≈ `C(5,2) / 2²⁴` = `10 / 16.7M` ≈ 6e-7 per slot → ~1.2e-5 per 20-slot DB.
- `K=10`: ≈ `C(10,2) / 2²⁴` = `45 / 16.7M` ≈ 2.7e-6 per slot → ~5.4e-5 per 20-slot DB.
- `K=50`: ≈ `1225 / 16.7M` ≈ 7.3e-5 per slot → ~1.5e-3 per 20-slot DB.
- `K=100`: ≈ `4950 / 16.7M` ≈ 3e-4 per slot → ~6e-3 per 20-slot DB.

Reviewer's claim "~0.15% per 10-campaign DB" was an upper-bound approximation; the accurate figure is ~5e-5 (0.005%) for `K=10`. Still non-zero; still unguarded. Reviewer's severity P1 "latent but real" holds even at the corrected probability.

### Impact on collision

If a collision occurs, two logical runs share a `run_id` key. Downstream consequences:

- **`GROUP BY run_id`** pooling in `scripts/analyze_results.py`, `scripts/analyze_longitudinal.py`, ext11 #111 longitudinal selector — two runs' rows aggregate as one.
- **Per-(condition, task_id) uniformity check** (ext13 #131) — `COUNT(*) per (condition, task_id)` would show `2 * expected_runs` rather than `expected_runs` for the colliding slot only, so the existing gate catches exactly this shape at exit 2. **That's an important defense already in place.**
- **Scoping via `--run-ids`** — if a user passes the collided run_id once, both underlying runs enter scope silently.

Summary: **ext13 #131 catches the collision at exit 2 on row 17 via per-(condition, task_id) uniformity**. The unguarded path is rows 14/16 (same blind spot as #151) + cross-campaign analyses that don't pass through row 17's gate.

### Defense-in-depth context (why P1 latent, not higher)

- Probability is low at realistic `K` (≤ 10 campaigns per DB under normal use).
- ext13 #131 uniformity check on row 17 catches the in-campaign collision shape.
- ext7 #49 DB-per-campaign contract reduces `K` to 1 in the default workflow (collision reduces to impossible).
- The latent-but-real severity reflects that the schema has no structural guard; a future code path that skips row 17 (diagnostic scripts, Chapter 7 future-work analyzers) would be unguarded.

### Proposed fix (two viable options)

**Option A — Increase entropy** (minimal, one-LOC):
```python
# tests/evaluation/harness.py:302
run_id = f"{condition}_{run_idx:02d}_{uuid.uuid4().hex}"  # 128 bits, collision negligible
```
Collision probability becomes `~C(K, 2) / 2¹²⁸` ≈ 0 for any realistic `K`. Risk: longer run_ids in CLI output (40 chars vs 16). Trade-off: cosmetic vs structural.

**Option B — Structural uniqueness constraint** (schema migration):
```sql
-- Migration: add UNIQUE (run_id, condition) constraint. On collision,
-- insert raises; harness retries with fresh uuid4 up to N times.
ALTER TABLE evaluation_runs ADD CONSTRAINT uniq_run_condition UNIQUE (run_id, condition);
```
Requires migration for existing DBs + `insert_record` retry-on-conflict loop. More invasive but catches *any* future collision including cross-process races.

**Recommended:** Option A. The one-LOC fix eliminates the probability at no behavioral cost. Option B is the proposal-purity path if the thesis methodology section wants "structurally impossible" language.

### Proposed test

Add to `tests/unit/test_harness.py` (or `tests/unit/test_evaluation_store.py`):

```python
def test_run_id_uses_full_uuid4_entropy():
    """ext15 #153: run_id must carry full uuid4 entropy (32 hex chars),
    not a truncated 6-char suffix. Truncation gives 24-bit collision
    space which is reachable under --append-chained campaigns.
    """
    from tests.evaluation.harness import EvaluationHarness
    # Peek at the run_id format via a stubbed invocation.
    # (Or: match the format string via ast / regex on harness.py line 302.)
    import re, inspect
    src = inspect.getsource(EvaluationHarness._execute_all_runs)
    m = re.search(r'run_id\s*=\s*f["\'][^"\']+["\']', src)
    assert m, "could not locate run_id format string"
    format_str = m.group(0)
    assert ".hex[:6]" not in format_str, (
        "ext15 #153: run_id uses uuid4().hex[:6] (24-bit). Upgrade to "
        "full .hex (128-bit) to eliminate collision in --append-chained DBs."
    )
    assert ".hex" in format_str, "run_id must still derive from uuid4().hex"
```

---

---

## Issue #154 — `analyze_tier2_probes.py` default reject-only fetch collides with ext14 #139 per-session coverage gate; healthy sessions with zero rejects fail exit 4 (P0 CRITICAL, confirmed)

### Summary

`scripts/analyze_tier2_probes.py:504` establishes the default fetch filter:

```python
event_names: tuple[str, ...] = (_TIER2_EVENT_REJECT,)
if args.include_pass:
    event_names = (_TIER2_EVENT_REJECT, _TIER2_EVENT_PASS)
```

`fetch_tier2_events` at line 159-235 iterates over `event_names` and builds `contributed_sessions: set[str]` from session IDs that emitted matching events (line 216-217).

ext14 #139's per-session coverage gate at line 544-559:

```python
if args.session_ids:
    requested = set(args.session_ids)
    missing = requested - contributed_sessions
    if missing and not args.allow_partial_sessions:
        print(f"\nERROR: {len(missing)}/{len(requested)} requested session IDs contributed zero events: {sorted(missing)}\n...")
        return 4
```

**The gate treats "session contributed no rejects" as identical to "session missing from LangFuse."** The collision: a JIT session whose Tier 2 accepted every probe (zero rejections — the *winning* outcome per proposal §Pillar 2) emits only `verification.tier_2.pass` events. With reject-only default fetch, that session's ID never enters `contributed_sessions`. Gate exits 4 with `"Likely causes: (a) typo, (b) LangFuse retention, (c) partial ingestion"` — none of which apply.

### Why this is P0 (confirmed, not downgraded)

Unlike #151 + #152 (defense-in-depth for reviewer-constructed contract violations), #154 **fires on canonical campaigns under the default run-sheet workflow**:

- Row 15 in `docs/run_sheet.md:182` runs with no `--include-pass` → reject-only fetch is in force.
- `CAMPAIGN_SESSION_IDS` contains all 20 canonical run_ids by default (see #155 for the broader scope issue).
- Even setting #155 aside (i.e., scoping to the 10 JIT sessions only): if any of those 10 had all-pass Tier 2 on every capability, that session's `contributed_sessions` entry is absent → exit 4.
- The workaround `--allow-partial-sessions` forces an unconditional Chapter 4.5 `[PARTIAL SESSIONS]` disclosure banner for what is structurally a clean run — polluting the proposal-alignment audit trail.

The semantic error is precise: the gate was imported from the LangFuse-analyzer completeness family (ext14 #139), where "session contributed zero rows" meant "session is absent from the data source." In this analyzer, "session contributed zero events" can *also* mean "session ran and everything passed" — the two states are indistinguishable in the current fetch.

### Proposed fix

Three viable approaches, in rough order of preference:

**Option A — Expand coverage-gate fetch independently of analysis fetch (preferred).**
Decouple the coverage-gate's session-existence probe from the analysis fetch. Always include both `reject` + `pass` events for the purpose of populating `contributed_sessions`; keep `--include-pass` as the control for whether `pass` events enter the analysis aggregates. One additional fetch pass (or a single-query widening of the existing one) adds ~linear cost in the number of `pass` events and eliminates the collision.

```python
# Coverage-gate fetch: always both event types, session IDs only.
_, coverage_sessions, _ = fetch_tier2_events(
    client, start, end,
    event_names=(_TIER2_EVENT_REJECT, _TIER2_EVENT_PASS),
    environment=args.environment,
    session_ids=args.session_ids,
    max_pages=args.max_pages,
)
# Analysis fetch: respects --include-pass.
events, _, truncated = fetch_tier2_events(
    client, start, end,
    event_names=event_names,   # from --include-pass control
    ...
)
# Coverage gate uses coverage_sessions; analysis uses events.
```

**Option B — Switch the coverage probe to a lighter signal.**
The harness writes `langfuse_trace_id` onto `evaluation_runs` (`src/registry/evaluation_store.py:56`). For any session_id in `--session-ids`, "contributed" can be decided by DuckDB existence (`SELECT 1 FROM evaluation_runs WHERE run_id = ? LIMIT 1`) rather than LangFuse event emission. Eliminates LangFuse-retention blind spots entirely and makes the gate condition-independent. Drawback: adds a DB dependency to an otherwise pure-LangFuse analyzer.

**Option C — Relax gate semantics to "session exists in LangFuse trace API."**
Issue a `/api/public/traces` GET per session_id; coverage = "trace exists." Decouples existence from event-emission. Drawback: N extra API calls per session; slower.

**Recommended:** Option A. Scoped to one analyzer; no DB dependency; preserves the ext14 #139 "detect retention / typo" intent.

### Proposed test

```python
def test_coverage_gate_accepts_session_with_only_pass_events(monkeypatch, capsys):
    """ext15 #154: a JIT session where every Tier 2 probe passed emits only
    verification.tier_2.pass events. Under the pre-fix reject-only fetch, the
    gate treated this as 'session missing' and exited 4 on clean campaigns.
    Post-fix, coverage-gate fetch always includes both event types so the
    session counts as contributed.
    """
    # Stub fetch_tier2_events: first call (coverage gate) returns the pass-only
    # session as contributed; second call (analysis, reject-only default) returns
    # empty events.
    calls = []
    def fake_fetch(client, start, end, *, event_names, **kwargs):
        calls.append(event_names)
        if set(event_names) >= {_TIER2_EVENT_PASS}:
            return [], {"full_00_abcdef"}, False
        return [], set(), False
    monkeypatch.setattr("scripts.analyze_tier2_probes.fetch_tier2_events", fake_fetch)

    rc = _run_main(["--session-ids", "full_00_abcdef"])
    # Coverage gate must have seen both event types.
    assert any(_TIER2_EVENT_PASS in names for names in calls)
    # Analysis fetch may be reject-only per --include-pass default.
    # Gate must not fire — session contributed (even if via pass event only).
    assert rc != 4
```

---

## Issue #155 — Row 15 CAMPAIGN_SESSION_IDS includes static + no_verification sessions that structurally emit no `verification.tier_2.*` events; strict gate fires unconditionally (P0 CRITICAL, confirmed)

### Summary

`docs/run_sheet.md:121`:
```bash
export CAMPAIGN_SESSION_IDS="$CAMPAIGN_RUN_IDS"
```

`CAMPAIGN_RUN_IDS` contains all 20 complete run_ids: 5 × full + 5 × no_verification + 5 × no_optimization + 5 × static. Row 15 at `docs/run_sheet.md:182` passes all 20 to `analyze_tier2_probes.py --session-ids ...`.

Harness emission invariants (`tests/evaluation/harness.py`):

- **`static`** (line 531-533): dispatches to `_run_static` → `_run_static_implementation` (line 630, line 1692). No JIT pipeline invocation, no verification gate calls, no `verification.tier_2.*` emission possible.
- **`no_verification`** (line 710): `verification_enabled = condition != "no_verification"` → gate is bypassed at `pipeline.invoke` time (`create_pipeline(..., verification_enabled=False)`). No `verification.tier_2.*` emission.
- **`full`** + **`no_optimization`**: both run `_run_jit` with `verification_enabled=True` → Tier 2 emits.

10 of 20 canonical-campaign sessions (5 × static + 5 × no_verification) **cannot** emit Tier 2 events **by design**. Under ext14 #139's strict-per-session gate (`requested - contributed_sessions`), Row 15 exits 4 with `10/20 requested session IDs contributed zero events` on every canonical campaign.

### Why this is P0 (confirmed, not downgraded)

- Reaches under **default run-sheet workflow**. No contract violation required; the `CAMPAIGN_SESSION_IDS="$CAMPAIGN_RUN_IDS"` assignment is the documented canonical form (see ext7 post-audit `test_campaign_session_ids_mirrors_run_ids_not_single_value` in `tests/unit/test_run_sheet.py:197-219`).
- Forces `--allow-partial-sessions` + Chapter 4.5 disclosure banner unconditionally for every canonical campaign — polluting the proposal-alignment audit trail for what is correctly-behaving data.
- Stacks with #154: even after dropping static + no_verification (fixing #155), the remaining 10 JIT sessions can fail #154 if Tier 2 accepts everything.

### Proposed fix

Scope Row 15's session-id set to conditions that structurally emit Tier 2 events. Add a dedicated Phase-4/5 env var, computed alongside `CAMPAIGN_SESSION_IDS`:

```bash
# ext15 #155: Row 15 (analyze_tier2_probes.py) requires sessions that ran
# the verification gate. static (no JIT) and no_verification (gate bypassed)
# emit zero verification.tier_2.* events; including them in --session-ids
# makes ext14 #139's strict gate fail unconditionally.
export CAMPAIGN_TIER2_SESSION_IDS="$(uv run python -c "
import duckdb
conn = duckdb.connect('$CAMPAIGN_DB')
rows = conn.execute('''
    SELECT DISTINCT run_id FROM evaluation_runs
    WHERE NOT is_pilot
      AND condition IN ('full', 'no_optimization')
''').fetchall()
print(' '.join(r[0] for r in rows))
")"
```

Row 15 becomes:
```bash
uv run scripts/analyze_tier2_probes.py --environment default --session-ids $CAMPAIGN_TIER2_SESSION_IDS
```

Run-sheet lint at `tests/unit/test_run_sheet.py` extension:
```python
def test_row_15_uses_tier2_scoped_session_ids():
    """ext15 #155: Row 15 must scope to conditions that emit Tier 2 events.
    Using $CAMPAIGN_SESSION_IDS (all 20 run_ids) includes static +
    no_verification which structurally emit zero Tier 2 events, causing
    ext14 #139's strict gate to fire unconditionally.
    """
    text = RUN_SHEET.read_text()
    row_15_match = re.search(r"^\|\s*15\s*\|\s*`([^`]+)`", text, re.MULTILINE)
    assert row_15_match, "Row 15 missing from run sheet"
    row_15_cmd = row_15_match.group(1)
    # Must use a Tier-2-scoped session-id env var, not the global one.
    assert "CAMPAIGN_TIER2_SESSION_IDS" in row_15_cmd, (
        "ext15 #155: Row 15 must scope --session-ids to conditions that "
        "emit Tier 2 events (full + no_optimization). Using "
        "$CAMPAIGN_SESSION_IDS (all 20) fails ext14 #139's gate unconditionally."
    )
    assert "CAMPAIGN_TIER2_SESSION_IDS=" in text, (
        "ext15 #155: scoping block must define CAMPAIGN_TIER2_SESSION_IDS "
        "alongside CAMPAIGN_SESSION_IDS."
    )
```

### Interaction with #154

#155 addresses the structural-zero condition (no Tier 2 events emitted); #154 addresses the winning-outcome zero (emitted only pass events). Orthogonal but stacked:

- Without #155 fix: Row 15 fails on 10/20 sessions regardless of #154.
- With #155 fix but not #154: Row 15 reaches 10 JIT sessions, but still fails if any of them had all-pass Tier 2.
- With both fixes: Row 15 succeeds on canonical campaigns; `--allow-partial-sessions` reserved for genuine retention / ingest failures.

Both fixes are required for Row 15 to succeed without override on default workflow. Land together.

---

## Issue #156 — Row 13 CAMPAIGN_SESSION_IDS includes static sessions that produce no DeepAgent traces; strict gate fires on every canonical campaign (P1, confirmed)

### Summary

`scripts/analyze_context_utilization.py:53-58`:
```python
# Agent types we care about (trace names from DeepAgent invocations)
AGENT_TRACE_NAMES = {
    "task-decomposer": "Decomposition",
    "adversarial-prober": "Adversarial Probing",
    "skill-executor": "SKILL Execution",
    "agent-executor": "AGENT Execution",
}
```

`tests/evaluation/harness.py:583-690` `_run_static` invokes `_run_static_implementation` (line 630, line 1692) which calls pre-written static-baseline code with no DeepAgent invocations. **Static sessions emit zero traces under any of the four queried names.**

ext14 #139 per-session coverage gate at `scripts/analyze_context_utilization.py:679-694` checks `requested - contributed_sessions_total` where `contributed_sessions_total` is built from trace fetches across `AGENT_TRACE_NAMES`. Static sessions contribute zero → gate exits 4 with `5/20 requested session IDs contributed zero rows` on every canonical campaign.

### Why P1 (confirmed, narrower than #155)

- Reaches under default run-sheet workflow (same as #155) — reviewer-submitted P1 holds.
- Scope narrower than #155: `no_verification` still runs `_run_jit` → emits `task-decomposer`, `skill-executor`, `agent-executor` traces. ext14 #139 gate is per-session (not per-(session, agent-family)) — "at least one trace from any of the four names" suffices for a session to count as contributed. no_verification sessions contribute 3 of 4 names per session → gate passes. Only `static` (zero contributions) fully fails.
- Quantitatively: 5 failing sessions (static) vs #155's 10 failing sessions. Impact per-campaign is half, hence P1 vs P0.
- Still affects every canonical campaign; still forces `--allow-partial-sessions` + Chapter 4.5 disclosure for correctly-behaving data.

### Proposed fix

Scope Row 13's session-id set to non-static conditions. Add a second Phase-4/5 env var alongside `CAMPAIGN_TIER2_SESSION_IDS` (from #155):

```bash
# ext15 #156: Row 13 (analyze_context_utilization.py) queries DeepAgent
# trace names. Static sessions invoke no DeepAgents; including them in
# --session-ids makes ext14 #139's strict gate fire unconditionally.
export CAMPAIGN_DEEPAGENT_SESSION_IDS="$(uv run python -c "
import duckdb
conn = duckdb.connect('$CAMPAIGN_DB')
rows = conn.execute('''
    SELECT DISTINCT run_id FROM evaluation_runs
    WHERE NOT is_pilot
      AND condition != 'static'
''').fetchall()
print(' '.join(r[0] for r in rows))
")"
```

Row 13 becomes:
```bash
uv run scripts/analyze_context_utilization.py --session-ids $CAMPAIGN_DEEPAGENT_SESSION_IDS
```

Run-sheet lint:
```python
def test_row_13_uses_deepagent_scoped_session_ids():
    """ext15 #156: Row 13 must scope to conditions that invoke DeepAgents.
    Static sessions call _run_static_implementation — no DeepAgent traces,
    ext14 #139 gate fires unconditionally on every canonical campaign.
    """
    text = RUN_SHEET.read_text()
    row_13_match = re.search(r"^\|\s*13\s*\|\s*`([^`]+)`", text, re.MULTILINE)
    assert row_13_match
    assert "CAMPAIGN_DEEPAGENT_SESSION_IDS" in row_13_match.group(1)
```

### Alternative: relaxed-gate design

Rather than fixing in the run-sheet, the analyzer could relax its ext14 #139 gate to "session contributed ≥1 trace OR session's condition is in a documented no-trace set." This would require the analyzer to read `evaluation_runs` for condition metadata — adds a DB dependency to an otherwise pure-LangFuse tool. Preferred path is the run-sheet scoping (symmetric to #155's fix), keeping the analyzer DB-independent.

### Interaction with #155

Same pattern, different analyzer + different emission invariant:

| Analyzer | Emission signal | Static fails? | no_verification fails? |
|---|---|---|---|
| Row 15 (`analyze_tier2_probes.py`) | `verification.tier_2.*` events | Yes (no JIT, no gate) | Yes (gate bypassed) |
| Row 13 (`analyze_context_utilization.py`) | DeepAgent trace names | Yes (no DeepAgents invoked) | No (emits 3 of 4 names) |

Both require run-sheet scoping. #155 filters to `{full, no_optimization}`; #156 filters to `condition != 'static'`. Two distinct env vars; both computed in the Phase-4/5 scoping block.

---

---

## Issue #157 — Row 17 completeness gate checks condition count but not canonical identity; one swapped baseline label passes silently (HIGH, defense-in-depth; downgraded from P0)

### Summary

`scripts/analyze_results.py:1585-1599`:

```python
for condition, count in condition_map.items():
    if count != expected_per_condition:
        issues.append(
            f"condition '{condition}': {count} rows, expected {expected_per_condition}"
        )
if len(condition_map) < expected_conditions:
    missing = (
        {"full", "no_verification", "no_optimization", "static"}
        - set(condition_map)
    )
    for c in sorted(missing):
        issues.append(f"condition '{c}' missing entirely (0 rows)")
```

The missing-condition check is **gated by `len(condition_map) < expected_conditions`** — identity is only verified when count is too low. If a campaign produces 4 distinct conditions with 100 rows each, totalling 400, but one of them is a non-canonical label (e.g., `weird_new` instead of `static`):

- `total == expected_total` ✓
- each condition has 100 rows ✓
- `len(condition_map) == 4 == expected_conditions` → **missing-check skipped**
- `static` is absent; `weird_new` is present; gate passes silently

The coverage map `{full, no_verification, no_optimization, static}` is hardcoded at line 1595 — proof that the author already knew the canonical identity — yet the check only consults it on the count-too-low branch.

### Mirror of #152 on the condition axis

Both findings are instances of the same structural pattern: *a count-only completeness gate that does not close the symmetric identity check.*

| Axis | Finding | Count check | Identity check |
|---|---|---|---|
| task_id | #152 | ✓ (ext13 #131 per-(condition, task_id) uniformity) | ✗ (no `observed == {T01..T20}` assertion) |
| condition | #157 | ✓ (lines 1588-1591) | Partial (only on count-too-low branch at 1593) |

Proposed fix for #157 is symmetric to #152's:

```python
# ext15 #157: extend count check with symmetric identity check on the
# condition axis. Current gate skips identity when len == expected; a
# 4-condition campaign with one swapped canonical label (static →
# "weird_new") passes silently.
_CANONICAL_CONDITIONS = {"full", "no_verification", "no_optimization", "static"}
observed_conditions = set(condition_map)
missing = _CANONICAL_CONDITIONS - observed_conditions
extra = observed_conditions - _CANONICAL_CONDITIONS
if missing:
    for c in sorted(missing):
        issues.append(f"condition '{c}' missing entirely (0 rows)")
if extra:
    for c in sorted(extra):
        count = condition_map[c]
        issues.append(
            f"non-canonical condition '{c}': {count} rows (not in canonical "
            f"baseline set {sorted(_CANONICAL_CONDITIONS)}; see "
            f"src/schemas/evaluation.BaselineCondition)"
        )
```

### Defense-in-depth context (why HIGH, not P0)

Same reachability profile as #152. Requires:

1. **Code edit to `BaselineCondition` enum** (`src/schemas/evaluation.py:17-34`) renaming one of the 4 canonical values — caught by `git diff`, not the gate.
2. **`--append` cross-iteration DB** where a prior iteration used different condition labels — ext7 #49 contract violation.

Under the default workflow, conditions are produced by `EvaluationHarness` using the `BaselineCondition` enum values; drift requires explicit intervention. P0 in this series is reserved for findings reachable under the default workflow; #157 is defense-in-depth hardening.

### Proposed test

Add to `tests/unit/test_analyze_results.py`:

```python
def test_completeness_gate_rejects_non_canonical_condition_label():
    """ext15 #157: the gate must reject a DB with 4 distinct condition
    labels × 100 rows each when one label is non-canonical. The current
    count-only gate (len(condition_map) == expected_conditions) skips
    identity verification silently.
    """
    # Populate 400 rows: 100 × full + 100 × no_verification + 100 ×
    # no_optimization + 100 × "weird_new" (static replaced).
    _populate_evaluation_runs(
        conn,
        conditions=["full", "no_verification", "no_optimization", "weird_new"],
        rows_per_condition=100,
        task_ids={f"T{i:02d}" for i in range(1, 21)},
    )
    rc = _run_main(["--run-ids", *all_run_ids])

    assert rc == 2, "non-canonical condition label must fail the identity gate"
    assert "non-canonical condition 'weird_new'" in captured.err
    assert "missing entirely" in captured.err  # static absent
```

---

## Issue #158 — Row 16 ext14 #143 empty-sample fallback ignores active audit-scope filters; typo in `--conditions` / `--types` / `--since` produces false-green (P1, confirmed)

### Summary

`scripts/audit_contamination.py:629-635` fetches the unlabeled pool with six filters:

```python
pool = fetch_unlabeled_rows(
    store._conn,
    conditions=conditions,
    types=types,
    run_ids=run_ids,
    since=since,
)
```

`fetch_unlabeled_rows` (elsewhere in the file) applies:
- `run_id IN (...)`
- `NOT is_pilot`
- `condition IN (...)`
- `capability_type IN (...)`
- `generated_artifact_count > 0`
- `human_label IS NULL`
- `timestamp >= since` (when provided)

If `pool` is empty, the ext14 #143 fallback at line 681-699 disambiguates "filter dropped everything" (exit 3) from "genuinely all labeled" (exit 0):

```python
if run_ids:
    total_in_scope = store._conn.execute(
        "SELECT COUNT(*) FROM evaluation_runs "
        "WHERE run_id IN ({}) AND NOT is_pilot".format(
            ", ".join("?" * len(run_ids))
        ),
        run_ids,
    ).fetchone()[0]
    if total_in_scope == 0:
        return 3
    print("\nNothing to audit — all in-scope rows already labeled.")
    return 0
```

The fallback query uses **only `run_id IN (...) AND NOT is_pilot`** — drops `conditions`, `types`, `generated_artifact_count > 0`, `human_label IS NULL`, and `since`. Scenario:

- User runs `uv run scripts/audit_contamination.py --run-ids $CAMPAIGN_RUN_IDS --conditions full,no_verficiation`  (typo: `no_verficiation`)
- `fetch_unlabeled_rows` returns empty pool (typo'd condition has zero rows).
- Sample empty → fallback fires.
- `total_in_scope` query ignores `--conditions` filter → counts ALL rows for those run_ids → returns 400.
- Prints `"Nothing to audit — all in-scope rows already labeled."` → exit 0.

Row 16 reports success with zero labels produced. Downstream Row 17 M2 section silently degrades (no labels ingested, `print_contamination_rate` returns False, exit 7 via `--allow-missing-metrics` or ext13 #126 missing-metrics gate — but by then the user already believes Row 16 succeeded).

### Why P1 (confirmed)

Reachable under the default workflow on any `--conditions` / `--types` / `--since` typo. The ext14 #143 guard's scope was narrowly "detect `--run-ids` typo" — its design doesn't extend to the other five filters. Real but narrow: `--run-ids` is the only filter with a dynamic-discovery subshell in the run sheet (CAMPAIGN_RUN_IDS), so `--conditions` / `--types` / `--since` are more exposed to user-typed typos than `--run-ids` is.

### Proposed fix

Parametrize the fallback query with the same filter set the pool query uses:

```python
if not sample:
    # ext15 #158: extend ext14 #143's run_id-only fallback to all six
    # filters that fetch_unlabeled_rows applies. Empty-sample disambiguation
    # must distinguish "filter mismatch" from "genuinely all labeled" under
    # every filter combination, not just --run-ids.
    #
    # Count what's in scope under the FULL filter set (except human_label
    # IS NULL, which is the very thing we're checking coverage on).
    where_clauses = ["NOT is_pilot"]
    params: list = []
    if run_ids:
        where_clauses.append(f"run_id IN ({','.join('?' * len(run_ids))})")
        params.extend(run_ids)
    if conditions:
        where_clauses.append(f"condition IN ({','.join('?' * len(conditions))})")
        params.extend(conditions)
    if types:
        where_clauses.append(f"capability_type IN ({','.join('?' * len(types))})")
        params.extend(types)
    if since:
        where_clauses.append("timestamp >= ?")
        params.append(since)
    where_clauses.append("generated_artifact_count > 0")
    total_in_scope = store._conn.execute(
        f"SELECT COUNT(*) FROM evaluation_runs WHERE {' AND '.join(where_clauses)}",
        params,
    ).fetchone()[0]
    if total_in_scope == 0:
        print(
            f"\nERROR: audit scope (run_ids={run_ids}, conditions={conditions}, "
            f"types={types}, since={since}) matched zero rows in DB. "
            f"Filter dropped everything — verify spelling / scope. "
            f"ext15 #158 (extends ext14 #143). Exit 3.",
            file=sys.stderr,
        )
        return 3
    print("\nNothing to audit — all in-scope rows already labeled.")
    return 0
```

### Proposed test

```python
def test_empty_sample_fallback_detects_wrong_conditions_filter(monkeypatch):
    """ext15 #158: --conditions typo must exit 3, not 0. Pre-fix the
    fallback's run_id-only count returned non-zero and printed 'all
    labeled'.
    """
    _populate_evaluation_runs(conn, condition="full", n_rows=100, run_id="r1")
    rc = _run_audit(
        ["--run-ids", "r1", "--conditions", "no_verficiation"]  # typo
    )
    assert rc == 3
    assert "matched zero rows" in captured.err
    assert "Filter dropped everything" in captured.err
```

---

## Issue #159 — M2 pooled contamination rate aggregates all labeled conditions; extra (non-canonical) condition labels silently enter pooled numerator/denominator (P1, confirmed)

### Summary

`scripts/analyze_results.py:1332-1448` `print_contamination_rate`. Initial query:

```python
rows = conn.execute(
    f"""
    SELECT condition, SUM(...), SUM(...), SUM(...), COUNT(*) AS n_sampled
    FROM evaluation_runs
    WHERE human_label IS NOT NULL AND NOT is_pilot{_SCOPE_SQL}
    GROUP BY condition
    ORDER BY condition
    """
).fetchall()
```

No condition whitelist. Pooled aggregation at 1382-1412:

```python
total_c, total_x, total_p, total_n = 0, 0, 0, 0
for condition, n_correct, n_contam, n_partial, n_sampled in rows:
    ...
    total_c += int(n_correct)
    total_x += int(n_contam)
    total_p += int(n_partial)
    total_n += int(n_sampled)
...
if len(rows) > 1 and total_n > 0:
    strict_rate = 100.0 * total_x / total_n
    proposal_rate = 100.0 * (total_x + total_p) / total_n
    # print the pooled row
```

Every returned `condition` feeds the pooled totals. Per-condition coverage check at 1437-1448:

```python
present_conditions = {r[0] for r in rows}
expected_conditions = set(CONDITIONS) - {"static"}  # ext14 #146
missing_conditions = expected_conditions - present_conditions
if missing_conditions:
    ... return False
```

**Asymmetric:** catches `missing = expected - present` but never computes `extra = present - expected`. Extra condition labels on `longitudinal`, a future ablation condition, or any non-baseline enum value enter the pooled headline silently.

### Reachability

Under default workflow, Row 16's canonical JIT scope (`_JIT_CONDITIONS` at `scripts/audit_contamination.py:74`) limits what gets labeled to `{full, no_verification, no_optimization}`. So in the canonical case, `present_conditions` ⊆ `expected_conditions` and extras don't appear.

Reachable via:
- **`--append` cross-campaign with a different `_JIT_CONDITIONS` set** (e.g., a future iteration adds `no_classification`, appends into the current DB, Row 16 labels the extra rows, Row 17 pools them).
- **Longitudinal DB merged into Phase-4 DB** (`condition = "longitudinal"` labels enter).
- **Enum drift** (same as #157's reachability paths).

Bias magnitude: proportional to the ratio of extra-condition labels to canonical labels. Small under realistic misuse; unbounded in the limit.

### Proposed fix

One-line set-diff assertion on the extra-side:

```python
# ext15 #159: extend ext13 #132 + ext14 #146 missing-check with a
# symmetric extra-check. Pooled M2 aggregates run across every returned
# row; non-canonical condition labels (longitudinal, future ablations,
# enum drift via --append) silently enter the pooled headline.
extra_conditions = present_conditions - expected_conditions
if extra_conditions:
    print(
        f"\n  ⚠ M2 includes non-baseline conditions: "
        f"{sorted(extra_conditions)}. Pooled rate is biased by out-of-scope "
        f"labels. Drop with an explicit WHERE condition IN (...) filter or "
        f"--allow-missing-metrics to proceed with disclosure.",
        file=sys.stderr,
    )
    # Consistent with ext13 #126's missing-metrics exit path.
    return False
```

Paired with a WHERE-clause filter on the initial query so extras never enter the per-condition table either:

```sql
WHERE human_label IS NOT NULL
  AND NOT is_pilot
  AND condition IN ('full', 'no_verification', 'no_optimization')  -- ext15 #159
  {_SCOPE_SQL}
```

Belt-and-braces: the WHERE-clause drops extras from the numerator/denominator at fetch time; the set-diff assertion surfaces the fact that extras *were* in the DB so the operator knows why the row count differs from expectation.

### Proposed test

```python
def test_m2_pooled_rate_excludes_non_baseline_conditions(monkeypatch):
    """ext15 #159: non-canonical condition labels must be dropped from
    the pooled numerator/denominator AND flagged. Pre-fix, extra
    labels entered the pooled headline silently.
    """
    # Populate the expected JIT set + an extra 'longitudinal' row with
    # label='contaminated'.
    _populate_labels(conn, condition="full", human_label="correct", n=20)
    _populate_labels(conn, condition="no_verification", human_label="correct", n=20)
    _populate_labels(conn, condition="no_optimization", human_label="correct", n=20)
    _populate_labels(conn, condition="longitudinal", human_label="contaminated", n=5)

    rc = _run_main(["--run-ids", *all_run_ids])

    # Either exit 7 (missing-metrics path) OR exit 0 with extras dropped
    # from the pooled row — pooled contamination rate must not be 5/65.
    assert "non-baseline conditions" in captured.err
    assert "longitudinal" in captured.err
    # The pooled row, if printed, must reflect the baseline-only denominator.
    pooled_row_match = re.search(r"Pooled\s+(\d+)", captured.out)
    if pooled_row_match:
        assert int(pooled_row_match.group(1)) == 60, (
            "pooled denominator must exclude 'longitudinal' rows"
        )
```

---

---

## Issue #160 — M4 silent-zero gate uses `max(jit_ratios) < 0.5`; one healthy JIT condition masks two broken ones (P0 CRITICAL, confirmed)

### Summary

`scripts/analyze_results.py:780-803` `print_token_cost_summary` ext14 #149 M4 gate:

```python
jit_conditions = {"full", "no_verification", "no_optimization"}
by_cond = {r[0]: (int(r[4] or 0), int(r[5] or 0)) for r in rows}
jit_ratios: list[float] = []
for cond in jit_conditions:
    nonzero, total = by_cond.get(cond, (0, 0))
    if total > 0:
        jit_ratios.append(nonzero / total)
if not jit_ratios or max(jit_ratios) < 0.5:
    ratios_str = ", ".join(f"{r:.2f}" for r in jit_ratios) or "(no JIT rows)"
    print(
        f"\n  ⚠ M4 silent zero: JIT conditions have non-zero ratios "
        f"[{ratios_str}] (max < 0.5). ...",
        file=sys.stderr,
    )
    return False
return True
```

The gate fires **only when every JIT condition** is below the 50% non-zero-cost threshold (`max(jit_ratios) < 0.5` → all three are below). Partial-failure scenario:

| condition | nonzero/total | ratio |
|---|---|---|
| full | 100/100 | 1.00 |
| no_verification | 0/100 | 0.00 |
| no_optimization | 0/100 | 0.00 |

`max([1.00, 0.00, 0.00]) = 1.00 ≥ 0.5` → gate passes. Row 17 M4 table prints:

```
Condition               Total Tokens  Avg/Task  Avg/Nonzero  With Cost
full                       10,000,000   100,000      100,000    100/100
no_verification                     0         0            —      0/100
no_optimization                     0         0            —      0/100
static                              0         0            —      0/100
```

Chapter 6 reads "no_verification and no_optimization have ~0 token cost vs full's 100k" → concludes "full condition has infinite cost-efficiency disadvantage" (or, depending on sign convention, "infinite advantage"). Every M4 JIT-vs-JIT headline is corrupted on this shape.

### Reachability (default workflow)

Row 11 partial failure is a realistic operational scenario, not a contract violation:

1. **ext14 #141 pagination truncation on a subset of sessions**: `fetch_all_generations` hits the 50k ceiling on high-volume time windows. If backfill was run twice with non-overlapping `--since` windows (or in a resumed session after truncation recovery), some sessions are truncated and others are complete.
2. **#161 per-session pagination truncation** (new this extension): `fetch_traces_by_session` silently drops late traces for high-volume sessions without a surfacing signal. Affected sessions write zero tokens; unaffected write full tokens.
3. **ext12 #122 zero-generations for specific runs**: some sessions legitimately have zero LangFuse-attributable generations (e.g., sessions where every task failed before the first LLM call).
4. **ext14 #145 partial CAMPAIGN_RUN_IDS**: different `--run-ids` subsets used for Row 8 → Row 11 → Row 17 can land subsets with uneven backfill coverage.
5. **LangFuse transient retention** on specific sessions mid-campaign.

All five paths produce **partial per-condition zeros** rather than uniform zeros. The ext14 #149 gate, designed for uniform-zero, doesn't fire.

### Why P0 CRITICAL (confirmed)

- Reachable under default workflow (no contract violation required).
- Directly corrupts the Chapter 6 M4 JIT-vs-JIT comparison — a headline metric.
- Same structural antipattern (asymmetric completeness check) as #152/#157/#159, but unlike those three, **this instance is default-workflow-reachable** because Row 11 partial failure is operationally realistic.
- The name ("silent zero") already acknowledged in the ext14 #149 comment is still silent — just shifted from "uniform silent zero" to "partial silent zero."

### Proposed fix

Replace `max(...) < 0.5` with per-condition enforcement:

```python
# ext15 #160: per-condition enforcement. ext14 #149's max() gate fires
# only when every JIT condition is below threshold; partial failure
# (one healthy, others zero) passes silently while the M4 JIT-vs-JIT
# comparison is structurally corrupt. Fail on ANY JIT condition below
# threshold.
jit_conditions = {"full", "no_verification", "no_optimization"}
by_cond = {r[0]: (int(r[4] or 0), int(r[5] or 0)) for r in rows}
failing_conditions: list[tuple[str, float]] = []
for cond in sorted(jit_conditions):
    nonzero, total = by_cond.get(cond, (0, 0))
    if total == 0:
        failing_conditions.append((cond, 0.0))
    else:
        ratio = nonzero / total
        if ratio < 0.5:
            failing_conditions.append((cond, ratio))

if failing_conditions:
    failing_str = ", ".join(
        f"{c}={r:.2f}" for c, r in failing_conditions
    )
    print(
        f"\n  ⚠ M4 partial silent zero: JIT conditions below 50% non-zero "
        f"ratio: [{failing_str}]. Partial Row 11 failure — one healthy "
        f"JIT condition masks the broken ones under the pre-fix max() "
        f"gate. Likely cause: ext14 #141 pagination on subset, #161 per-"
        f"session truncation, ext12 #122 zero-generations on some runs, "
        f"ext14 #145 partial CAMPAIGN_RUN_IDS, transient retention. "
        f"ext15 #160.",
        file=sys.stderr,
    )
    return False
return True
```

Override path unchanged: `--allow-missing-metrics` (ext13 #126) still covers the operator-acknowledged partial-coverage case with Chapter 4.5 disclosure.

### Proposed test

Add to `tests/unit/test_analyze_results.py`:

```python
def test_m4_gate_fires_on_partial_silent_zero(conn):
    """ext15 #160: the gate must fail when ANY JIT condition is below the
    50% non-zero-cost threshold. Pre-fix `max(ratios) < 0.5` gate passed
    when ratios=[1.0, 0.0, 0.0] (one healthy masks two broken).
    """
    # Populate: full → 100/100 non-zero; no_verification → 0/100;
    # no_optimization → 0/100; static → 0/100 (exempt).
    _populate_token_costs(conn, condition="full", n_nonzero=100, n_zero=0)
    _populate_token_costs(conn, condition="no_verification", n_nonzero=0, n_zero=100)
    _populate_token_costs(conn, condition="no_optimization", n_nonzero=0, n_zero=100)
    _populate_token_costs(conn, condition="static", n_nonzero=0, n_zero=100)

    result = print_token_cost_summary(conn)

    assert result is False, (
        "ext15 #160: partial silent zero must fail the gate even when one "
        "JIT condition is healthy"
    )
    assert "M4 partial silent zero" in captured.err
    assert "no_verification=0.00" in captured.err
    assert "no_optimization=0.00" in captured.err
```

---

## Issue #161 — `fetch_traces_by_session` silent pagination truncation at 20k traces per session undercounts per-task M4 token costs (P1, confirmed)

### Summary

`scripts/backfill_token_costs.py:520-556`:

```python
def fetch_traces_by_session(
    base_url: str, auth: httpx.Auth, session_id: str
) -> list[dict]:
    """Fetch all traces for a given session_id (= run_id) via REST API."""
    traces = []
    page = 1
    with httpx.Client(base_url=base_url, auth=auth, timeout=30.0) as client:
        while True:
            resp = client.get(
                "/api/public/traces",
                params={"sessionId": session_id, "limit": 100, "page": page},
            )
            ...
            for t in data:
                ...
                traces.append({...})

            if len(data) < 100:
                break
            page += 1
            if page > 200:
                break  # <-- silent truncation, no warning, no return flag
    return traces
```

Comparison with ext14 #140 / #141 post-fix siblings:

| Function | Max pages | Truncation signal |
|---|---|---|
| `analyze_context_utilization.py::fetch_traces` (ext14 #140 fix) | 200 | WARNING log + `truncated: bool` return flag |
| `analyze_tier2_probes.py::fetch_tier2_events` (ext14 #140 fix) | 200 | WARNING log + `truncated: bool` return flag |
| `backfill_token_costs.py::fetch_all_generations` (ext14 #141 fix) | 500 | WARNING log + `truncated: bool` return flag |
| **`backfill_token_costs.py::fetch_traces_by_session` (MISSED)** | **200** | **silent break, no flag** |

ext14 #140 + #141 were a joint sweep across LangFuse-pagination-truncation silent-drop gaps. The sweep targeted `fetch_all_generations` (the primary Row 11 fetch) but missed `fetch_traces_by_session` (the secondary per-session fetch used in per-task attribution mode at line 622).

### Downstream impact

Per-task mode at `compute_per_task_costs` (line 586-693):

```python
run_task_tokens: dict[tuple[str, str], int] = {}
tagged_run_ids: set[str] = set()

for i, rid in enumerate(sorted(run_ids)):
    ...
    traces = fetch_traces_by_session(base_url, auth, rid)  # <-- truncation here
    task_traces: dict[str, list[dict]] = defaultdict(list)
    for t in traces:
        tid = t.get("task_id")
        if tid:
            task_traces[tid].append(t)
    ...
    for task_id, t_list in task_traces.items():
        total = 0
        for t in t_list:
            total += fetch_observations_for_trace(base_url, auth, t["id"])
        run_task_tokens[(rid, task_id)] = total  # <-- under-counted
```

When a session emits >20k traces, late traces silently drop. If T20 tasks' traces happen to land in the truncated tail, `run_task_tokens[(rid, "T20")]` under-counts. The row in `evaluation_runs` gets a biased-low `token_cost` at the write step (line 719 `total_for_task = run_task_tokens.get(key, 0)`).

### Coverage-gate blind spot

The per-run per-task tag coverage gate at `scripts/backfill_token_costs.py:685-690`:

```python
present_keys = set(run_task_tokens.keys())
per_run_missing_tasks: dict[str, list[str]] = {}
for rid, expected in expected_keys_per_tagged_run.items():
    missing = sorted(tid for (r, tid) in expected - present_keys)
    if missing:
        per_run_missing_tasks[rid] = missing
```

Checks **per-key presence**, not **per-key completeness**. A truncated task's `(rid, task_id)` entry is in `present_keys` (some traces made it in) with a **partially-summed value**. The gate sees "key present" and accepts. The under-sampled value propagates to `evaluation_runs.token_cost`.

ext13 #129's `--allow-partial-task-coverage` + ext13 #130's `--allow-under-coverage` both guard presence/absence shapes — neither guards within-key completeness.

### Reachability

Volumetric threshold: **20k traces per session** (200 pages × 100/page). Canonical campaign shape:

- Per-task AGENT workload: DeepAgent task-decomposer (~20 traces/task) + adversarial probing (~20-200 traces/task) + skill/agent executors (~10-50 traces/task) ≈ **50-400 traces per task** for AGENT-leaning tasks.
- Session = 20 tasks. Per-session trace volume: **1k-8k typical**, **up to 20k under AGENT-heavy full-condition** workloads.
- Ceiling is 20k. T18-T20 AGENT-heavy tasks under `full` condition with deep adversarial loops can approach or cross this threshold.

Not common on trivial / moderate-tier tasks; reachable on AGENT-heavy full-condition sessions. Reviewer-submitted P1 confirmed.

### Proposed fix

Retrofit ext14 #140's pattern (same as sibling fetchers):

```python
def fetch_traces_by_session(
    base_url: str,
    auth: httpx.Auth,
    session_id: str,
    *,
    max_pages: int = 200,
) -> tuple[list[dict], bool]:
    """Fetch all traces for a given session_id (= run_id) via REST API.

    ext15 #161 (2026-04-22): returns ``(traces, truncated: bool)`` tuple
    and logs a WARNING when the pagination ceiling is hit. Aligns with
    the ext14 #140 / #141 sweep which fixed the sibling analyzers and
    ``fetch_all_generations`` but missed this per-session trace fetch.
    """
    import logging as _lg
    traces = []
    truncated = False
    page = 1
    with httpx.Client(base_url=base_url, auth=auth, timeout=30.0) as client:
        while True:
            resp = client.get(
                "/api/public/traces",
                params={"sessionId": session_id, "limit": 100, "page": page},
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if not data:
                break
            for t in data:
                ...  # unchanged extraction
            if len(data) < 100:
                break
            page += 1
            if page > max_pages:
                _lg.getLogger(__name__).warning(
                    "ext15 #161: fetch_traces_by_session hit pagination "
                    "ceiling at page %d (max_pages=%d, ~%d traces, "
                    "session=%s). Late traces silently dropped; per-task "
                    "token_cost for this session is biased low.",
                    page, max_pages, len(traces), session_id,
                )
                truncated = True
                break
    return traces, truncated
```

Caller updates at `compute_per_task_costs`:

```python
# ext15 #161: propagate truncation signal up the stack so main() can
# refuse the write unless --allow-truncated is passed.
truncated_sessions: set[str] = set()
for i, rid in enumerate(sorted(run_ids)):
    ...
    traces, session_truncated = fetch_traces_by_session(base_url, auth, rid)
    if session_truncated:
        truncated_sessions.add(rid)
    ...

compute_per_task_costs.last_truncated_sessions = frozenset(truncated_sessions)  # type: ignore[attr-defined]
```

`main()` gate:

```python
if getattr(compute_per_task_costs, "last_truncated_sessions", frozenset()):
    truncated = compute_per_task_costs.last_truncated_sessions
    if not args.allow_truncated:
        print(
            f"\nERROR: {len(truncated)} session(s) hit the per-session trace-"
            f"fetch pagination ceiling (~20k traces). Affected sessions' "
            f"per-task token_cost is biased low: {sorted(truncated)}. "
            f"Options: (a) raise --max-pages, (b) pass --allow-truncated "
            f"with Chapter 4.5 disclosure. ext15 #161. Exit 5.",
            file=sys.stderr,
        )
        return 5
```

Reuses the existing `--allow-truncated` / exit 5 contract from ext14 #140 / #141.

### Proposed test

```python
def test_fetch_traces_by_session_flags_truncation(monkeypatch):
    """ext15 #161: the fetcher must return a truncated flag when the
    pagination ceiling is hit. Pre-fix the silent break undercounted
    per-task token_cost on high-volume AGENT-heavy sessions.
    """
    # Mock the REST client to return 100 rows per page for 201 pages.
    mock_pages = [[{"id": f"t{p}_{i}", "tags": []} for i in range(100)]
                  for p in range(201)]

    class FakeResp:
        def __init__(self, data): self._data = data
        def raise_for_status(self): pass
        def json(self): return {"data": self._data}

    class FakeClient:
        call_count = 0
        def get(self, *args, **kwargs):
            resp = FakeResp(mock_pages[FakeClient.call_count])
            FakeClient.call_count += 1
            return resp
        def __enter__(self): return self
        def __exit__(self, *_): pass

    monkeypatch.setattr("httpx.Client", lambda *a, **kw: FakeClient())

    traces, truncated = fetch_traces_by_session("http://fake", None, "sess_a")
    assert truncated is True
    assert len(traces) == 20000  # 200 pages × 100, page 201 break
```

### Relationship to #160

#160 and #161 compound. #161 produces partial-condition zeros on individual truncated sessions. #160's `max()` gate fails to detect those as long as at least one JIT condition stays above 50% non-zero. Fixing #161 reduces the *rate* at which partial silent zeros arise; fixing #160 ensures that any remaining partial silent zero (from the other four reachability paths) surfaces loudly. Both land together for M4 integrity.

---

## Issue #162 — M3 'Aggregate' Gini computed on raw per-(run, capability) counts instead of pooled-by-capability totals; header promise and compute diverge (P0 CRITICAL, escalated from unlabeled)

### Summary

`scripts/analyze_results.py:935-982` `print_gini_summary`. The offending data flow:

```python
# Line 937-940: aggregation over per-run rows
by_run: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
by_condition: dict[str, list[int]] = defaultdict(list)
condition_caps: dict[str, set[str]] = defaultdict(set)
for cond, run_id, cap_id, inv_count in per_run_rows:
    by_run[(cond, run_id)][cap_id] = int(inv_count)
    by_condition[cond].append(int(inv_count))  # <-- raw per-(run, cap) count
    condition_caps[cond].add(cap_id)

# ... per-run Ginis computed correctly with zero-padding ...

# Line 981: Aggregate column
aggregate = _gini_coefficient(by_condition[cond])
```

`by_condition[cond]` is a flat list. Each entry is `inv_count` for one `(run_id, capability_id)` pair. For a condition with 5 runs and 10 distinct capabilities ever invoked, `by_condition[cond]` carries up to 50 integers (one per non-zero `(run, cap)` cell).

The header at line 961-968 explicitly tells the reader what the Aggregate column means:

```
  Gini Coefficient of Capability Invocation Counts (proposal §Metrics)
  Per-condition summary: mean Gini across N independent runs,
  plus the pooled aggregate
  (all invocations across runs in that condition treated as one distribution).
```

"One distribution" and "pooled" — but the pooling the reader expects is "sum invocations by `capability_id` across runs." The code does NOT do that. It treats each `(run, cap)` as an independent datapoint.

### Divergence example (constructed)

Two runs, two capabilities, 100 invocations each, permutation-symmetric:

| row | run_id | capability_id | invocations |
|---|---|---|---:|
| 1 | run_1 | cap_A | 100 |
| 2 | run_1 | cap_B | 0 |
| 3 | run_2 | cap_A | 0 |
| 4 | run_2 | cap_B | 100 |

**True pooled-by-capability** (what the header promises):

```
pooled = {cap_A: 100 + 0 = 100, cap_B: 0 + 100 = 100}
Gini([100, 100]) = 0.0  # perfect equality
```

**Current compute** (what the code does):

```
by_condition["full"] = [100, 0, 0, 100]  # or similar ordering
Gini([100, 0, 0, 100]) ≈ 0.375  # material inequality
```

Two runs, each biased toward a single capability, with perfect balance across the pair at the *pool* level. True Gini = 0 (population-level equality). Current compute ≈ 0.375 because zero rows are excluded from `per_run_rows` at the SQL layer (the query is `GROUP BY condition, run_id, capability_id` with implicit `COUNT(*) > 0` filter on non-appearing pairs; actual flattened list may be `[100, 100]` or `[100, 0, 0, 100]` depending on whether zero-padding applies — either way, ≠ 0).

Actually let me re-examine. The SQL at line 891-908 is `GROUP BY ie.condition, ie.run_id, ie.capability_id` — rows with zero invocations aren't in `invocation_events`, so they don't appear. For the constructed example above, `per_run_rows` would be `[(cond, run_1, cap_A, 100), (cond, run_2, cap_B, 100)]` — two rows. `by_condition[cond] = [100, 100]` → Gini = 0.

Hmm, in that specific case the compute accidentally matches pooled. Let me pick a different example where they diverge materially.

| row | run_id | capability_id | invocations |
|---|---|---|---:|
| 1 | run_1 | cap_A | 200 |
| 2 | run_1 | cap_B | 10 |
| 3 | run_2 | cap_A | 10 |
| 4 | run_2 | cap_B | 200 |

**True pooled:** `{cap_A: 210, cap_B: 210}` → Gini = 0.
**Current compute:** `by_condition = [200, 10, 10, 200]` → Gini ≈ 0.45.

Divergence is real whenever the per-run distribution is skewed but the pool is balanced. The reviewer's claim holds.

### Why P0 CRITICAL (escalated from unlabeled)

- Directly corrupts a Chapter 6 M3 headline.
- Reachable on any canonical campaign where different runs exercise different dominant capabilities (a normal outcome of LangGraph's per-run decomposition variability).
- Not a contract violation; default-workflow-reachable.
- Same structural antipattern family as #160 (math doesn't match its own header). Reviewer did not explicitly assign severity; escalated to P0 on grounds of Chapter 6 headline-integrity corruption — same bar as #154/#155/#160.

### Proposed fix

Aggregate by capability_id across runs before feeding to the Gini helper. Compute should match the header:

```python
# ext15 #162: pool by capability_id BEFORE computing aggregate Gini.
# Pre-fix the Aggregate column fed raw per-(run, cap) counts into
# _gini_coefficient, which treats each (run, cap) cell as a datapoint
# rather than summing invocations by capability across runs. On
# permutation-symmetric workloads (cap A dominant in some runs, cap B
# in others), this elevates the reported inequality above true pooled.

pooled_by_cond: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
for cond, _run_id, cap_id, inv_count in per_run_rows:
    pooled_by_cond[cond][cap_id] += int(inv_count)

# ... per-run Ginis unchanged (correctly zero-padded against condition_caps) ...

for cond in CONDITIONS:
    run_ginis = condition_ginis.get(cond, [])
    if not run_ginis:
        print(f"  {CONDITION_LABELS.get(cond, cond):<22} {'—':>8} {'—':>10} {'—':>7} {'—':>7} {'—':>10}")
        continue
    mean = sum(run_ginis) / len(run_ginis)
    # ext15 #162: aggregate over capability_id, not raw per-(run,cap) cells.
    pooled_counts = list(pooled_by_cond[cond].values())
    aggregate = _gini_coefficient(pooled_counts)
    ...
```

Optional diagnostic: keep the raw-cell-flattened number as a secondary column labeled explicitly (e.g., "Cell Gini") for transparency — but the proposal-aligned headline number MUST be the pooled-by-capability one. Chapter 4.5 disclosure of the fix.

### Proposed test

```python
def test_m3_aggregate_pools_by_capability_across_runs(conn):
    """ext15 #162: Aggregate Gini must pool by capability_id across runs,
    matching the header's "all invocations across runs treated as one
    distribution" promise. Pre-fix the compute fed raw per-(run, cap)
    counts into the Gini helper, elevating reported inequality on
    permutation-symmetric workloads.
    """
    # run_1: cap_A=200, cap_B=10; run_2: cap_A=10, cap_B=200
    # True pooled: {cap_A: 210, cap_B: 210} → Gini = 0
    # Pre-fix compute: [200, 10, 10, 200] → Gini ≈ 0.45
    _populate_invocations(conn, run_id="run_1", condition="full", cap_id="cap_A", n=200)
    _populate_invocations(conn, run_id="run_1", condition="full", cap_id="cap_B", n=10)
    _populate_invocations(conn, run_id="run_2", condition="full", cap_id="cap_A", n=10)
    _populate_invocations(conn, run_id="run_2", condition="full", cap_id="cap_B", n=200)

    captured = _capture(print_gini_summary, conn)

    # Parse the Aggregate column for "full"
    match = re.search(r"JIT \+ Verif \+ Opt\s+\d+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+([\d.]+)", captured.out)
    assert match, "couldn't find 'full' row in Gini table"
    aggregate = float(match.group(1))
    assert aggregate < 0.1, (
        f"ext15 #162: Aggregate Gini on permutation-symmetric workload "
        f"should be near 0 (pooled {{A:210, B:210}} is balanced), got {aggregate}. "
        f"Pre-fix compute on raw per-(run,cap) cells [200,10,10,200] gives ~0.45."
    )
```

---

## Issue #163 — Table 1 / Table 2 / JIT cluster headers still cite T04-T13 / T04-T18 / T01-T18 after ext6 #17 extended compute scope to T01-T20 / T04-T20 (P1, confirmed)

### Summary

`scripts/analyze_results.py` constants (line 33, 43-44):

```python
RETRIEVAL_TASKS = {"T01", "T02", "T03"}
# Security audit finding #17 (2026-04-20): pre-fix hard-coded range(1, 19)
# silently dropped T19 and T20. Import from single source of truth.
from tests.evaluation.tasks import ALL_TASKS
ALL_TASK_IDS = [t.task_id for t in ALL_TASKS]  # T01..T20
SYNTHESIS_TASK_IDS = [t for t in ALL_TASK_IDS if t not in RETRIEVAL_TASKS]  # T04..T20
```

Actual compute scope post-ext6 #17:
- Table 1 (no filter): T01-T20
- Table 2 (`SYNTHESIS_TASK_IDS`): T04-T20 (17 tasks)
- Table 3 (`RETRIEVAL_TASKS`): T01-T03 (unchanged)
- Cluster analysis (uses `synth_data`): T04-T20

Label sites still citing pre-fix ranges:

| Line | Site | Stale label | Actual scope |
|---|---|---|---|
| 4 | Module docstring | `"synthesis-only T04-T13"` | T04-T20 |
| 634 | `print_cluster_analysis` print | `"Synthesis tasks only (T04-T13):"` | T04-T20 |
| 1651 | Table 1 title | `"All Tasks (T01-T18)"` | T01-T20 |
| 1653 | Inline comment | `"Synthesis tasks only (T04-T13)"` | T04-T20 |
| 1657 | Table 2 title | `"Synthesis Tasks Only (T04-T18)"` | T04-T20 |

Chapter 6 reader reads "T04-T18" in the Table 2 caption but the Fisher tests, Holm correction, Clopper-Pearson CIs, and cluster gap numbers are all computed on T04-T20 (17 tasks). The labels misrepresent the task population in two overlapping-but-different ways: "T04-T13" (a pre-fix historical range from an earlier iteration) and "T04-T18" (the ext6 #17 predecessor's stale range). Neither matches the current T04-T20 scope.

### Why P1 (confirmed)

- Pure label bug. The math is correct post-ext6 #17; the compute scope matches `SYNTHESIS_TASK_IDS` / `ALL_TASK_IDS`.
- Reachable on every canonical campaign (default workflow): every Row 17 output carries the mislabeled captions.
- Misleads the Chapter 6 reader about denominator coverage — affects interpretation, not compute.
- Severity P1 matches the impact tier: silent mislabeling of a headline table, not numerical corruption.

### Proposed fix

Derive labels from the single source of truth rather than hard-coding ranges. Label helper:

```python
# ext15 #163: derive table range labels from ALL_TASKS / SYNTHESIS_TASK_IDS
# so future testbed extensions don't reintroduce the ext6-#17-style label drift.
def _task_range_label(task_ids: list[str] | set[str]) -> str:
    """Render a sorted task-id list as `T<min>-T<max>` when contiguous, or
    explicit comma-separated list when sparse."""
    ids = sorted(task_ids)
    if not ids:
        return "(empty)"
    # Assume canonical TNN format.
    nums = [int(tid[1:]) for tid in ids]
    if nums == list(range(min(nums), max(nums) + 1)):
        return f"{ids[0]}-{ids[-1]}"
    return ", ".join(ids)

_ALL_LABEL = _task_range_label(ALL_TASK_IDS)       # "T01-T20"
_SYNTH_LABEL = _task_range_label(SYNTHESIS_TASK_IDS)  # "T04-T20"
_RETR_LABEL = _task_range_label(RETRIEVAL_TASKS)    # "T01-T03"
```

Call sites:

```python
# Line 4 docstring: update manually once (stable).
# Line 634:
lines.append(f"  Synthesis tasks only ({_SYNTH_LABEL}):")
# Line 1651:
csv_all = print_condition_table(f"Table 1: Success Rates — All Tasks ({_ALL_LABEL})", all_data)
# Line 1657:
csv_synth = print_condition_table(
    f"Table 2: Success Rates — Synthesis Tasks Only ({_SYNTH_LABEL})", synth_data
)
```

### Proposed test

```python
def test_analyze_results_table_labels_match_compute_scope():
    """ext15 #163: table headers must reflect the actual task set, not
    pre-ext6-#17 literal ranges. Regression guard on label/compute desync.
    """
    from scripts import analyze_results as ar

    # After the fix, derived labels should be T01-T20 / T04-T20.
    assert ar._task_range_label(ar.ALL_TASK_IDS) == "T01-T20"
    assert ar._task_range_label(ar.SYNTHESIS_TASK_IDS) == "T04-T20"

    # No stale literal survives in the module source.
    src = Path(ar.__file__).read_text()
    for stale in ["T04-T13", "T04-T18", "T01-T18"]:
        # Allow these only inside docstrings that EXPLICITLY call them out as historical.
        occurrences = [line for line in src.splitlines() if stale in line]
        for line in occurrences:
            assert "pre-ext6-#17" in line or "historical" in line, (
                f"stale range label {stale!r} still present at: {line}"
            )
```

---

## Issue #164 — `print_classification_accuracy` skip paths return `None` instead of `bool`; Row 17 silently exits 0 on skip while M2 / M3 / M4 fail the missing-metrics gate (P1, confirmed)

### Summary

`scripts/analyze_results.py:1047-1145` `print_classification_accuracy`. The function has three early-return skip paths:

```python
# Line 1111-1119: import failure
try:
    from tests.evaluation.tasks import ALL_TASKS
except Exception as exc:
    ...
    print(f"  (skipped: couldn't import ALL_TASKS — {exc.__class__.__name__})")
    return  # <-- implicit None

# Line 1121-1129: no expected_type assigned
expected_map = {t.task_id: t.expected_type for t in ALL_TASKS}
scorable = {tid for tid, et in expected_map.items() if et is not None}
if not scorable:
    ...
    print("  (skipped: no tasks have expected_type assigned)")
    return  # <-- implicit None

# Line 1139-1145: no JIT rows
rows = conn.execute(...).fetchall()
if not rows:
    ...
    print("  (skipped: no non-pilot JIT-condition rows in evaluation_runs)")
    return  # <-- implicit None
```

Call site at line 1696:

```python
print_classification_accuracy(conn)   # return value discarded
```

`missing_metrics` accumulation at line 1701-1718:

```python
missing_metrics: list[str] = []
if not print_gini_summary(conn):
    missing_metrics.append("M3 (Gini / Pillar 3 RQ #1)")
if not print_contamination_rate(conn):
    missing_metrics.append("M2 (Contamination)")
if not m4_ok:
    missing_metrics.append("M4 (Token Cost / Pillar 3 Context Economy)")
```

No corresponding entry for classification accuracy. Exit-7 gate at 1733 checks `if missing_metrics` — therefore Row 17 exits 0 silently when classification accuracy is skipped.

### The ext13 #126 / #128 / #132 + ext14 #149 pattern

Three siblings follow a uniform `-> bool` + missing-metrics contract:

| Metric | Function | Return type | Exit-7 entry |
|---|---|---|---|
| M2 Contamination | `print_contamination_rate` | `bool` | ext13 #132 |
| M3 Gini | `print_gini_summary` | `bool` | ext13 #126 / #128 |
| M4 Token Cost | `print_token_cost_summary` | `bool` | ext14 #149 |
| **Classification Accuracy** | `print_classification_accuracy` | **`None`** | **MISSING** |

Step 18.5 (2026-04-17) introduced classification accuracy as the fourth Chapter 6 metric — operational proxy for Pillar 1 RQ #1 — but predated or overlooked the ext14 #149 pattern-completion sweep. Classification Accuracy is the fourth metric and therefore should have matched the three siblings.

### Why P1 (confirmed)

- Reachable under default workflow: any Row 17 run where `ALL_TASKS` import triggers an exception (e.g., pytest collection-side effect, `tests/` on a different sys.path) or where the `expected_type` field is unpopulated (legacy DBs, cross-iteration append) silently exits 0 without a Pillar 1 RQ #1 evaluation.
- Pure completeness gap on an already-established pattern.
- Impact is Chapter 6 evidence-absence, not numerical corruption. P1 matches the severity tier.

### Proposed fix

Convert to `-> bool`; thread into `missing_metrics`:

```python
def print_classification_accuracy(conn: duckdb.DuckDBPyConnection) -> bool:
    """...
    ext15 #164: signature returns ``bool``. Returns ``False`` on the
    three skip paths (import failure, no expected_type, no scoped JIT
    rows) so main() can accumulate into missing_metrics and exit 7 —
    consistent with M2 / M3 / M4 per ext13 #126/#128/#132 + ext14 #149.
    """
    try:
        from tests.evaluation.tasks import ALL_TASKS
    except Exception as exc:
        ...
        return False  # <-- was `return`
    ...
    if not scorable:
        ...
        return False  # <-- was `return`
    ...
    if not rows:
        ...
        return False  # <-- was `return`
    ...
    # ... happy path unchanged ...
    return True  # <-- new
```

Main wiring at line 1696:

```python
# ext15 #164: classification accuracy joins M2 / M3 / M4 in the
# missing-metrics exit-7 contract per ext13 #126/#128/#132 + ext14 #149.
if not print_classification_accuracy(conn):
    missing_metrics.append("Classification Accuracy / Pillar 1 RQ #1")
```

Override path unchanged: `--allow-missing-metrics` covers classification accuracy too (same override surface as the three siblings).

### Proposed test

```python
def test_classification_accuracy_skip_fails_missing_metrics_gate(conn):
    """ext15 #164: when print_classification_accuracy skips (no
    expected_type), main() must exit 7 unless --allow-missing-metrics.
    Pre-fix the function returned None and main() ignored it → silent
    exit 0.
    """
    # Populate 400 JIT rows but strip expected_type from TaskDefinitions.
    _populate_evaluation_runs(conn, condition="full", n=100, ...)
    # ... (rest of canonical shape)
    # Monkeypatch ALL_TASKS so no task has expected_type set.
    ...

    rc = _run_main(["--run-ids", *all_run_ids])

    assert rc == 7, "ext15 #164: skipped classification accuracy must fail exit-7 gate"
    assert "Classification Accuracy / Pillar 1 RQ #1" in captured.err
    assert "CHAPTER 6 INCOMPLETE" in captured.err
```

---

## Issue #165 — `map_generations_to_runs` unmatched count logged only; complement to ext12 #122's per-run zero-match gate missing (P1, confirmed)

### Summary

`scripts/backfill_token_costs.py:224-293`:

```python
def map_generations_to_runs(
    generations: list[dict], run_windows: list[dict]
) -> tuple[dict[str, list[dict]], int]:
    """Map each generation to the best-matching run_id."""
    run_to_gens: dict[str, list[dict]] = defaultdict(list)
    unmatched = 0
    ...
    for gen in generations:
        ...
        if best_rw:
            run_to_gens[best_rw["run_id"]].append(gen)
        else:
            unmatched += 1
    return dict(run_to_gens), unmatched
```

Main call site at lines 906-908:

```python
print("Mapping generations to evaluation runs...")
run_to_gens, unmatched = map_generations_to_runs(generations, run_windows)
print(f"  Mapped to {len(run_to_gens)} runs, {unmatched} unmatched generations.")
```

The `unmatched` count is printed-only. No threshold check, no exit code, no override banner.

### Asymmetry with ext12 #122's sibling gate

The per-run sibling at lines 944-964 follows the ext12 #122 pattern:

```python
zero_summary_runs = [
    rid for rid, s in run_summaries.items()
    if s.get("n_generations", 0) == 0 and s.get("condition") != "static"
]
...
if zero_summary_runs:
    ...
    if not args.allow_partial:
        print(
            "  Refusing to write partial backfill. Pass --allow-partial "
            "to proceed with zeros for these runs (document in Chapter "
            "4.5 as an M4 limitation). ext12 #122. Exit 3.",
            file=sys.stderr,
        )
```

Two complementary failure modes:

| Failure mode | Gate | Status |
|---|---|---|
| "A run had zero matched generations" | ext12 #122 (line 944-964) | ✓ gated, exit 3 |
| "Generations matched no run window at all" | — | ✗ un-gated, just printed |

Both represent silent M4 under-counts, but only one has a structural guard.

### Sources of unmatched generations

ext10 #105's buffer-clipping design trades attribution ambiguity for boundary unmatched-ness:

1. **30s `OVERLAP_GUARD`** between adjacent runs — generations in this zone match no clipped buffer (pre-fix they'd have matched both runs with first-match-wins; post-fix they match neither).
2. **Edge-of-campaign boundaries** — the first and last runs have unclipped outer buffers of ±120s, but generations beyond that window are unmatched.
3. **Clock skew >60s** between LangFuse's `startTime` and the harness's `MIN(timestamp) / MAX(timestamp)` — the `BUFFER = timedelta(seconds=120)` absorbs half the skew; overflow leaves generations stranded.

None of these triggers the per-run zero-match gate because every run still has *some* matches inside its clipped window. The unmatched population accumulates quietly.

### Impact

Tokens in the `unmatched` pool are discarded. The per-run sum at `compute_run_costs` only counts matched generations → `run_summaries[rid]["total_tokens"]` is biased low proportional to `unmatched / total_generations`. `compute_backfill_values` writes the biased total → `evaluation_runs.token_cost` is biased low → Row 17 M4 reports lower-than-true per-task cost.

Magnitude depends on ext10 #105's buffer sizing vs campaign timing:

- 5-run campaign with ~1h gaps between runs: `OVERLAP_GUARD` = 30s × 4 gaps = 2 minutes unmatched window vs ~5h run span. Unmatched fraction ~0.7%.
- Dense back-to-back AGENT-heavy campaigns: ~1 minute gaps → guard window ~= actual gap → larger unmatched fraction.

### Proposed fix

Add a ratio threshold gate matching the ext12 #122 pattern:

```python
# ext15 #165: unmatched-generations integrity gate. Complements ext12 #122's
# per-run zero-match gate. Unmatched generations accumulate silently in
# boundary zones (ext10 #105 OVERLAP_GUARD, campaign edges, clock skew)
# and bias M4 downward without tripping any per-run coverage check.
total_generations = unmatched + sum(len(gs) for gs in run_to_gens.values())
unmatched_ratio = (unmatched / total_generations) if total_generations else 0.0
print(
    f"  Unmatched ratio: {unmatched}/{total_generations} "
    f"= {unmatched_ratio*100:.2f}%"
)
if unmatched_ratio > args.max_unmatched_ratio and not args.allow_unmatched:
    print(
        f"\nERROR: {unmatched} / {total_generations} LangFuse generations "
        f"({unmatched_ratio*100:.2f}%) matched no run window, above "
        f"--max-unmatched-ratio={args.max_unmatched_ratio*100:.1f}%. "
        f"Tokens silently discarded → M4 biased low.\n"
        f"  Likely causes: (a) ext10 #105 30s OVERLAP_GUARD zone between "
        f"adjacent runs, (b) generations outside the ±120s campaign edge "
        f"buffer, (c) LangFuse/harness clock skew > 60s.\n"
        f"  Options: raise --max-unmatched-ratio (document in Chapter 4.5), "
        f"pass --allow-unmatched, or investigate the unmatched timestamps. "
        f"ext15 #165. Exit 4.",
        file=sys.stderr,
    )
    return 4
```

New CLI args:

```python
parser.add_argument(
    "--max-unmatched-ratio", type=float, default=0.05,
    help="ext15 #165: maximum allowed unmatched-generations fraction "
         "(default 0.05 = 5%). Above threshold, exit 4 unless "
         "--allow-unmatched is passed.",
)
parser.add_argument(
    "--allow-unmatched", action="store_true",
    help="ext15 #165: proceed when unmatched-generations ratio exceeds "
         "--max-unmatched-ratio. Chapter 4.5 disclosure REQUIRED as an "
         "M4 under-count limitation.",
)
```

### Proposed test

```python
def test_unmatched_generations_above_threshold_fails(monkeypatch, capsys):
    """ext15 #165: unmatched-generations ratio above --max-unmatched-ratio
    must fail exit 4 unless --allow-unmatched is passed.
    """
    # 100 generations: 90 map inside run windows, 10 fall in OVERLAP_GUARD zone.
    fake_generations = _synthesize_generations_with_guard_gaps(...)
    fake_run_windows = _two_runs_with_30s_guard_gap(...)

    monkeypatch.setattr(
        "scripts.backfill_token_costs.fetch_all_generations",
        lambda *a, **kw: (fake_generations, False),
    )
    rc = _run_main_no_write(["--db", test_db_path])  # default --max-unmatched-ratio=0.05

    assert rc == 4, "10% unmatched must exceed default 5% threshold"
    assert "matched no run window" in captured.err
```

### Relationship to other findings

- **#161** (pagination truncation at `fetch_traces_by_session`): truncates the input population to the mapper. Gate position: fetch-time. Combined effect: truncated inputs never reach the mapper so they're not in `unmatched` either — both silently drop tokens but at different pipeline stages.
- **#167** (Row 11 scoping contract): wider DB `WHERE is_pilot = FALSE` drives wider LangFuse fetch window → more boundary generations → higher unmatched rate. #167 amplifies #165's exposure under `--append` contract violations.

---

## Issue #166 — `print_classification_accuracy` SQL uses `condition != 'static'` blacklist; confusion matrix labeled "aggregated over JIT conditions" silently ingests non-baseline labels (P1, confirmed)

### Summary

`scripts/analyze_results.py:1131-1195`. The SQL at line 1131-1137:

```python
rows = conn.execute(
    f"""
    SELECT task_id, condition, capability_type
    FROM evaluation_runs
    WHERE is_pilot = FALSE AND condition != 'static'{_SCOPE_SQL}
    """
).fetchall()
```

Filter is `condition != 'static'` — a **blacklist**. Any non-static condition label passes: `full`, `no_verification`, `no_optimization`, plus `longitudinal` (cross-iteration `--append`), future ablation condition values from enum drift, or any other label that survives ext7 #49 violations.

Per-condition table iteration at line 1175-1184 uses the explicit whitelist `["no_verification", "no_optimization", "full"]` → the tabular display is correct. **But the confusion-matrix aggregation** at line 1151-1161 has no whitelist:

```python
for task_id, cond, classified in rows:
    if task_id not in scorable:
        continue
    expected = expected_map[task_id]
    classified_norm = (classified or "unclassified").lower()
    per_condition[cond]["total"] += 1
    if classified_norm == expected:
        per_condition[cond]["hit"] += 1
    confusion[(expected, classified_norm)] += 1  # <-- no JIT filter
```

Every returned row feeds into `confusion[(expected, classified_norm)]`. The header at line 1194 states the compute semantics:

```
Confusion matrix (rows = expected, columns = classified, aggregated over JIT conditions):
```

**"Aggregated over JIT conditions"** is the promise — but the compute aggregates over every non-static row. Label and compute diverge.

### Relationship to Bundle E + existing findings

Same structural family as #159 (M2 pooled rate includes extras) / #163 (T04-T18 label vs T04-T20 compute) / #164 (M2/M3/M4 retrofit pattern missed for classification accuracy). #166 applies the "label-vs-compute scope divergence" shape to Pillar 1 RQ #1's confusion-matrix headline.

Reachable under the same contract-violation paths as #151/#152/#157/#159:
- `--append` cross-iteration with renamed conditions.
- Longitudinal DB merged into Phase-4 DB (`condition = "longitudinal"` rows enter).
- `BaselineCondition` enum drift.

### Proposed fix

Replace the blacklist with a whitelist in the SQL, mirroring the per-condition table's existing constraint:

```python
# ext15 #166: whitelist the three JIT conditions explicitly. Pre-fix
# `condition != 'static'` admitted longitudinal / future-ablation /
# enum-drift conditions into the confusion matrix silently, contradicting
# the printed "aggregated over JIT conditions" header.
rows = conn.execute(
    f"""
    SELECT task_id, condition, capability_type
    FROM evaluation_runs
    WHERE is_pilot = FALSE
      AND condition IN ('full', 'no_verification', 'no_optimization')
      {_SCOPE_SQL}
    """
).fetchall()
```

Optional: add symmetric extra-detection matching #159's pattern:

```python
# ext15 #166: surface extras even when blacklist is replaced by whitelist.
all_rows = conn.execute(
    f"""
    SELECT DISTINCT condition FROM evaluation_runs
    WHERE is_pilot = FALSE AND condition != 'static'{_SCOPE_SQL}
    """
).fetchall()
observed = {r[0] for r in all_rows}
expected_jit = {"full", "no_verification", "no_optimization"}
extras = observed - expected_jit
if extras:
    print(
        f"\n  ⚠ Classification accuracy excludes {sorted(extras)} "
        f"(non-JIT baseline). If these should be in scope, update the "
        f"whitelist and re-run.",
        file=sys.stderr,
    )
    # Optional exit via missing_metrics (#164 retrofit):
    return False
```

### Proposed test

```python
def test_classification_confusion_matrix_excludes_non_jit_conditions(conn):
    """ext15 #166: confusion-matrix aggregation must restrict to the
    three JIT baseline conditions. Pre-fix `condition != 'static'` blacklist
    admitted longitudinal / enum-drift rows into the Pillar 1 RQ #1 proxy.
    """
    # Canonical JIT rows, all classified correctly
    _populate_classification_rows(conn, condition="full", correct=True, n=20)
    _populate_classification_rows(conn, condition="no_verification", correct=True, n=20)
    _populate_classification_rows(conn, condition="no_optimization", correct=True, n=20)
    # Non-baseline rows, all mis-classified (would distort matrix)
    _populate_classification_rows(conn, condition="longitudinal", correct=False, n=60)

    captured = _capture(print_classification_accuracy, conn)

    # Confusion matrix diagonal should reflect 60 hits / 60 total (pre-fix
    # would be 60 hits / 120 total ≈ 50%).
    match = re.search(r"full\s+(\d+)/(\d+)", captured.out)
    # Per-condition table unchanged.
    # But the off-diagonal "unclassified" etc. cells must NOT have longitudinal
    # rows' contributions.
    assert "longitudinal" not in captured.out  # not mentioned in table
    # Total over confusion matrix diagonal should be 60, not 60+60=120.
```

---

## Issue #167 — Row 11 scoping contract at `docs/run_sheet.md:70` structurally unsatisfiable; `backfill_token_costs.py` lacks `--run-ids` / `--since` args (P1, confirmed)

### Summary

`docs/run_sheet.md:70` (under the "Scoping convention" heading):

```
Every Row below that reads `data/eval/metrics.duckdb` or a longitudinal DB
MUST scope by the campaign's `run_id`(s). The DBs are append-only across
sessions — unscoped queries pool prior campaigns into the current
Chapter-6 aggregate.
```

Row 11 invocation at `docs/run_sheet.md:178`:

```
| 11 | `uv run scripts/backfill_token_costs.py --db $CAMPAIGN_DB --write` | ... |
```

**Only `--db` is passed** — no scoping flag. `scripts/backfill_token_costs.py:760-816` parser definition has **no `--run-ids` or `--since` arguments at all**. Flags present:
- `--db`, `--write`, `--csv`
- `--allow-partial` (ext12 #122)
- `--allow-partial-task-coverage` (ext13 #129)
- `--allow-under-coverage`, `--min-gens-per-jit-record` (ext13 #130)
- `--max-pages`, `--allow-truncated` (ext14 #141)

All three internal queries use `WHERE is_pilot = FALSE` without any scope:

```python
# Line 161-173: fetch_run_windows
SELECT run_id, condition, MIN(timestamp), MAX(timestamp), COUNT(*), COUNT(DISTINCT task_id)
FROM evaluation_runs
WHERE is_pilot = FALSE
GROUP BY run_id, condition
ORDER BY MIN(timestamp)

# Line 176-178: task_ids per run
SELECT DISTINCT run_id, task_id FROM evaluation_runs WHERE is_pilot = FALSE

# Line 200-206: per-record info
SELECT id, run_id, condition, task_id, token_cost, timestamp
FROM evaluation_runs WHERE is_pilot = FALSE
ORDER BY timestamp
```

The run-sheet contract says "MUST scope by run_id(s)." The Row 11 script **has no scope surface**. The contract is structurally unsatisfiable.

### Impact

Under ext7 #49 DB-per-campaign contract (default workflow), each `$CAMPAIGN_DB` has only one campaign's runs → no functional impact. Latent.

Under `--append` misuse or legacy pre-ext7-#49 DB with multiple campaigns:

1. **`fetch_run_windows` reads all non-pilot runs** → MIN/MAX over every campaign ever appended. The LangFuse fetch window spans from the oldest campaign's first run to the newest campaign's last run.
2. **Wider fetch window amplifies #165 unmatched rate** — more generations fetched that fall outside any current-campaign clipped buffer → higher unmatched fraction on the current campaign's token attribution.
3. **Per-condition coverage gate** at line 931-942 iterates all runs in DB → old-campaign runs whose LangFuse data has been retention-aged-out show as `zero_summary_runs` → ext12 #122 exit 3 on an otherwise-clean current campaign.
4. **Row 11 writes `token_cost=0`** to all old-campaign runs' rows (where LangFuse data is missing), overwriting prior backfills if any.

The compound effect: a clean current campaign can be forced into `--allow-partial` + Chapter 4.5 disclosure banners despite the current-campaign data being fine, because Row 11 sees "the whole DB."

### Why P1 (confirmed)

- Reachable only under ext7 #49 contract violation (same surface as #151/#152/#157/#159).
- But the contract violation surface is the same one that ext7 #49 itself, #151 (CAMPAIGN_RUN_IDS recency), and the entire defense-in-depth bundle target.
- The script's lack of `--run-ids` / `--since` is an **architectural omission** relative to the run-sheet contract — even operators who follow the contract correctly have no way to comply for Row 11.
- Low urgency under default workflow, but closing it removes an entire reachability class.

### Proposed fix (four coordinated changes)

**1. Add CLI args to `scripts/backfill_token_costs.py`:**

```python
parser.add_argument(
    "--run-ids", nargs="+", default=None,
    help="ext15 #167: scope to specific run_ids (default: all non-pilot "
         "runs). Matches ext7 #45's scope-flag contract for other Row 11-"
         "17 analyzers. Mutually exclusive with --since.",
)
parser.add_argument(
    "--since", type=str, default=None,
    help="ext15 #167: include only rows with timestamp >= SINCE. "
         "Mutually exclusive with --run-ids.",
)
```

**2. Thread scope into the three queries:**

```python
def _build_scope_sql(run_ids: list[str] | None, since: str | None) -> tuple[str, list]:
    """Build a (WHERE-fragment, params) tuple matching _SCOPE_SQL in
    analyze_results.py. Mutually-exclusive — caller validates."""
    if run_ids:
        placeholders = ",".join("?" * len(run_ids))
        return (f" AND run_id IN ({placeholders})", list(run_ids))
    if since:
        return (" AND timestamp >= ?", [since])
    return ("", [])
```

Apply to all three queries at lines 161-173, 176-178, 200-206.

**3. Update run sheet Row 11:**

```
| 11 | `uv run scripts/backfill_token_costs.py --db $CAMPAIGN_DB --run-ids $CAMPAIGN_RUN_IDS --write` | **M4** — populates `evaluation_runs.token_cost` from LangFuse GENERATION observations | ~5 min |
```

**4. Extend `tests/unit/test_run_sheet.py` lint:**

```python
# Add to _DB_READERS_TO_SCOPE:
"backfill_token_costs.py": {"--run-ids", "--since"},
```

The existing `test_every_db_reader_row_passes_scope_flag` test then enforces Row 11 scope-flag presence, symmetric to rows 14/16/17.

### Proposed unit tests

```python
def test_run_sheet_row_11_passes_scope_flag():
    """ext15 #167: Row 11 must scope backfill to campaign run_ids. The
    run-sheet contract at line 70 is binding; pre-fix Row 11 passed only
    --db, reading all non-pilot runs and forcing degraded-override
    outcomes on a clean campaign under --append misuse.
    """
    text = RUN_SHEET.read_text()
    row_11_match = re.search(r"^\|\s*11\s*\|\s*`([^`]+)`", text, re.MULTILINE)
    assert row_11_match
    row_11_cmd = row_11_match.group(1)
    assert "--run-ids" in row_11_cmd or "--since" in row_11_cmd, (
        "ext15 #167: Row 11 must scope backfill by $CAMPAIGN_RUN_IDS "
        "per docs/run_sheet.md:70 scoping contract. Pre-fix Row 11 passed "
        "only --db, reading all non-pilot runs across campaigns."
    )


def test_backfill_token_costs_accepts_run_ids_arg():
    """ext15 #167: scripts/backfill_token_costs.py must accept --run-ids
    to make the run-sheet Row 11 scoping contract structurally
    satisfiable."""
    import argparse
    from scripts import backfill_token_costs as bf
    parser = bf._build_arg_parser() if hasattr(bf, "_build_arg_parser") else None
    # If no factory, parse a canary invocation:
    import subprocess
    result = subprocess.run(
        ["uv", "run", "scripts/backfill_token_costs.py", "--help"],
        capture_output=True, text=True,
    )
    assert "--run-ids" in result.stdout, (
        "ext15 #167: backfill_token_costs.py --help must document --run-ids"
    )
```

### Relationship to #165

#165 and #167 compound. #167 widens the LangFuse fetch window under `--append` misuse; the widened window admits more boundary generations that fall outside any current-campaign run buffer → #165's unmatched ratio increases. Fixing #167 narrows the fetch window; fixing #165 adds a loud signal when the narrowing isn't sufficient.

---

## Issue #168 — Row 15 prose promises `{pass,reject}` scope but command runs reject-only; Chapter 4.3 denominator semantics ambiguous (P1, confirmed)

### Summary

`docs/run_sheet.md:182` Row 15 cell:

```
| 15 | `uv run scripts/analyze_tier2_probes.py --environment default --session-ids $CAMPAIGN_SESSION_IDS` | **Chapter 4.3 methodology evidence** — per-Tier-2-probe-type detection breakdown. Reads `verification.tier_2.{pass,reject}` LangFuse events, groups by `metadata.categories`, reports per-category hit counts + per-capability-type distribution. Answers "of the N Tier 2 rejections, which probe types caught them?" — proposal §Pillar 2 line 133 grounding. | ~10 s |
```

Two internally contradictory prose claims in the same cell:

1. **First sentence:** *"Reads `verification.tier_2.{pass,reject}` LangFuse events, groups by `metadata.categories`, reports per-category hit counts."*
2. **Second sentence:** *"Answers 'of the N Tier 2 rejections, which probe types caught them?'"*

The command has **no `--include-pass` flag**. `scripts/analyze_tier2_probes.py:504-506`:

```python
event_names: tuple[str, ...] = (_TIER2_EVENT_REJECT,)
if args.include_pass:
    event_names = (_TIER2_EVENT_REJECT, _TIER2_EVENT_PASS)
```

Default = reject-only. Command runs reject-only semantics. Second prose sentence matches; first prose sentence is **false** (no `pass` events are read).

### Chapter 4.3 interpretation hazard

Chapter 4.3 reader sees a per-category table and asks: "what's the denominator?"

- If **reject-only** (actual compute): per-category counts represent how many Tier 2 rejections each probe-type category caught. Detection-rate semantics per category = `(category_rejections / total_rejections)`. Reader should cite as "of the N rejections, P% were caught by probe type X."
- If **pass+reject** (first-sentence promise): per-category counts represent total probe executions per category. Utilization semantics = `(category_executions / total_executions)`. Reader should cite as "probe type X ran Q% of the time."

The two interpretations produce materially different Chapter 4.3 narratives. The current run-sheet prose supports both — the first sentence leads the reader toward the pass+reject interpretation that the command doesn't implement.

### Interaction with #154

#154 (`analyze_tier2_probes.py` reject-only coverage-gate collision) has a direct fix dependency on this prose-vs-command divergence. Two fix paths:

| Fix path | Prose result | #154 impact |
|---|---|---|
| Drop `{pass,reject}` from prose, keep reject-only command | Prose says "reads reject events" | #154 still blocks on clean campaigns |
| Add `--include-pass` to command, keep `{pass,reject}` prose | Prose accurate; command matches | #154's coverage gate populated by pass sessions → no false fail |

Path B dominates: one command edit simultaneously (a) closes #168 documentation drift, (b) sidesteps #154's coverage-gate collision, (c) makes pass+reject denominator available for Chapter 4.3 if needed. The only downside: if Chapter 4.3 wants the detection-rate-only headline (second prose sentence), the analyzer must separate "coverage-gate fetch" (pass+reject for session-presence check) from "analysis fetch" (reject-only for detection breakdown). That's exactly #154's proposed fix Option A.

Land the fixes together: Option A in the analyzer + `--include-pass` added to Row 15 command + first prose sentence clarified to name both populations.

### Proposed fix

`docs/run_sheet.md:182` update:

```
| 15 | `uv run scripts/analyze_tier2_probes.py --environment default --session-ids $CAMPAIGN_TIER2_SESSION_IDS --include-pass` | **Chapter 4.3 methodology evidence** — per-Tier-2-probe-type detection breakdown. Reads `verification.tier_2.{pass,reject}` LangFuse events (pass events populate the #154 coverage gate; reject events drive the per-category detection-rate table). Groups by `metadata.categories`, reports per-category rejections + per-capability-type distribution. Answers "of the N Tier 2 rejections, which probe types caught them?" — proposal §Pillar 2 line 133 grounding. | ~10 s |
```

Also incorporates #155 (Tier-2-scoped session IDs) from Bundle B.

### Proposed test

```python
def test_row_15_command_matches_prose_pass_reject_scope():
    """ext15 #168: Row 15 prose promises {pass,reject} scope. Command must
    include --include-pass to match. Pre-fix the default reject-only
    analyzer filter contradicted the prose's 'reads {pass,reject}' claim
    and collided with ext14 #139's coverage gate (see ext15 #154).
    """
    text = RUN_SHEET.read_text()
    row_15_match = re.search(r"^\|\s*15\s*\|\s*`([^`]+)`", text, re.MULTILINE)
    assert row_15_match
    row_15_cmd = row_15_match.group(1)
    # Prose says {pass,reject} → command must pass --include-pass.
    if "{pass,reject}" in text or "pass+reject" in text.lower():
        assert "--include-pass" in row_15_cmd, (
            "ext15 #168: Row 15 prose claims {pass,reject} scope but "
            "command omits --include-pass. Either add the flag or "
            "remove the {pass,reject} claim from the prose."
        )
```

---

## Issue #169 — CAMPAIGN_RUN_IDS completeness selector admits overfull run_ids; `>=` lower-bound check + underfull-only dropped-report create upper-bound blind spot (P1, confirmed)

### Summary

`docs/run_sheet.md:87-116` CAMPAIGN_RUN_IDS discovery. The admit branch (lines 90-98):

```sql
SELECT run_id, COUNT(*) AS n_rows, COUNT(DISTINCT task_id) AS n_tasks
FROM evaluation_runs
WHERE NOT is_pilot
GROUP BY run_id
HAVING COUNT(*) >= ${EXPECTED_ROWS_PER_RUN}     -- LOWER BOUND only
   AND COUNT(DISTINCT task_id) = ${EXPECTED_TASKS_PER_RUN}   -- strict equality
ORDER BY run_id
```

The dropped-report branch (lines 103-109):

```sql
SELECT run_id, COUNT(*) AS n_rows, COUNT(DISTINCT task_id) AS n_tasks
FROM evaluation_runs WHERE NOT is_pilot
GROUP BY run_id
HAVING COUNT(*) < ${EXPECTED_ROWS_PER_RUN}      -- underfull only
    OR COUNT(DISTINCT task_id) < ${EXPECTED_TASKS_PER_RUN}   -- underfull only
```

**Both branches are underfull-only.** Overfull run_ids — where `COUNT(*) > expected_rows` — pass the admit branch without appearing in the dropped-report:

- `COUNT(*) = 40 >= 20` ✓ admits
- `COUNT(DISTINCT task_id) = 20` ✓ admits (matches strict equality)
- No entry in the dropped-report (neither condition fires)

### Reachability

Three shapes produce overfull run_ids on a canonical campaign:

1. **#153 run_id collision:** two distinct logical runs share a `run_id` via the 24-bit `uuid4().hex[:6]` suffix birthday collision. Each logical run produces 20 rows → merged run_id has 40 rows. Both runs use canonical T01-T20 → `COUNT(DISTINCT task_id) = 20`. Admitted.
2. **Retry-write duplicates:** if a transient DuckDB error during `insert_record` is retried without a guard on `id` uniqueness, the same logical run lands twice. 40 rows, 20 distinct task_ids.
3. **`--append` re-running an existing run_id:** user resumes a run with the same `run_id` (manually specified, or via `--resume` logic that doesn't regenerate). Two partial pieces land under one `run_id`. If they together cover T01-T20, admitted.

### Impact

Overfull run_ids silently enter `$CAMPAIGN_RUN_IDS`. Rows 14/16 inherit:

- **Row 14 (`analyze_compression_loss.py`):** processes all rows with matching `run_id` — merged population silently pools. Chapter 6 T3 compression-loss aggregate biased.
- **Row 16 (`audit_contamination.py`):** stratified sample draws from merged pool. Human auditor labels from mixed population.
- **Row 17 (`analyze_results.py`):** ext13 #131 per-(condition, task_id) uniformity check catches `COUNT(*) per (condition, task_id) != :expected_runs` — 2 rows per task instead of 1 → exit 2 with uneven-uniformity error.

So the existing ext13 #131 gate catches row 17 only. Rows 14 + 16 silently merge. Same defense-in-depth pattern as #151 (recency on the ordering axis) / #157 (condition identity on the condition axis) — this one is the upper-bound complement on the row-count axis.

### Proposed fix

Tighten admit branch to strict equality and extend the dropped-report with bidirectional detection:

```sql
-- ext15 #169: symmetric completeness check. Pre-fix the admit branch's
-- COUNT(*) >= and the dropped-report's < semantics silently passed
-- overfull run_ids through (from #153 collisions, duplicate writes,
-- --append re-runs). Overfull is no more "complete" than underfull —
-- both indicate the run_id is not a 1:1 correspondence with a logical run.

-- Admit branch: strict equality (matches the existing COUNT(DISTINCT task_id) = semantic)
HAVING COUNT(*) = ${EXPECTED_ROWS_PER_RUN}
   AND COUNT(DISTINCT task_id) = ${EXPECTED_TASKS_PER_RUN}

-- Dropped-report branch: both sides (underfull AND overfull)
HAVING COUNT(*) != ${EXPECTED_ROWS_PER_RUN}
    OR COUNT(DISTINCT task_id) != ${EXPECTED_TASKS_PER_RUN}
```

Print the dropped-report with explicit classification:

```python
dropped = conn.execute('''
    SELECT run_id, COUNT(*) AS n_rows, COUNT(DISTINCT task_id) AS n_tasks
    FROM evaluation_runs WHERE NOT is_pilot
    GROUP BY run_id
    HAVING COUNT(*) != ${EXPECTED_ROWS_PER_RUN}
        OR COUNT(DISTINCT task_id) != ${EXPECTED_TASKS_PER_RUN}
''').fetchall()
if dropped:
    underfull = [r for r in dropped if r[1] < EXPECTED_ROWS_PER_RUN or r[2] < EXPECTED_TASKS_PER_RUN]
    overfull = [r for r in dropped if r[1] > EXPECTED_ROWS_PER_RUN]
    if underfull:
        print(f'WARNING: dropped {len(underfull)} partial run_id(s) (ext14 #145):', file=sys.stderr)
        ...
    if overfull:
        print(
            f'WARNING: dropped {len(overfull)} OVERFULL run_id(s) (ext15 #169) — '
            f'likely #153 collision, duplicate-write retry, or --append re-run:',
            file=sys.stderr,
        )
        for rid, n, t in overfull:
            print(f'  {rid}: {n} rows (expected {EXPECTED_ROWS_PER_RUN}), {t} distinct tasks', file=sys.stderr)
```

### Proposed test

Extend `tests/unit/test_run_sheet.py` with a symmetry lint:

```python
def test_campaign_run_ids_selector_rejects_overfull():
    """ext15 #169: selector must use `COUNT(*) = :expected` strict equality
    (not `>=`). Pre-fix, overfull run_ids from #153 collisions / duplicate
    writes / --append re-runs silently entered $CAMPAIGN_RUN_IDS.
    """
    text = RUN_SHEET.read_text()
    # Admit branch must use = not >=.
    admit_eq_match = re.search(
        r"HAVING COUNT\(\*\)\s*=\s*\$\{EXPECTED_ROWS_PER_RUN\}",
        text,
    )
    admit_ge_match = re.search(
        r"HAVING COUNT\(\*\)\s*>=\s*\$\{EXPECTED_ROWS_PER_RUN\}",
        text,
    )
    assert admit_eq_match and not admit_ge_match, (
        "ext15 #169: CAMPAIGN_RUN_IDS admit SQL must use strict "
        "COUNT(*) = :expected, not `>=`. Overfull run_ids are no more "
        "complete than underfull — both indicate run_id is not 1:1 "
        "with logical run."
    )
    # Dropped-report must cover both overfull and underfull.
    assert "OVERFULL" in text or "!=" in text, (
        "ext15 #169: dropped-report SQL must use `!=` (or explicitly "
        "classify overfull) so #153 collisions / duplicate writes / "
        "--append re-uses surface in the WARNING output."
    )
```

### Interaction with #153

#153 (run_id 24-bit collision) + #169 compound. #153 is the *source* of overfull run_ids (collision merges two logical runs under one `run_id`). #169 is the *filter* that should catch them. With #153 fixed (full `uuid4().hex` → collision probability ≈0), the reachability of #169 via path (1) drops to zero; paths (2) + (3) remain (duplicate-write retry, `--append` re-use). Land both: #153 removes the most common source; #169 catches the remaining duplicate-write / re-use shapes loudly. Neither fix subsumes the other.

---

## Issue #170 — `fetch_all_generations` has no sessionId / environment filter; Row 11 attributes foreign LangFuse generations to campaign runs, inflating M4 (P0 CRITICAL, escalated from unlabeled)

### Summary

`scripts/backfill_token_costs.py:69-147`:

```python
def fetch_all_generations(
    base_url: str,
    auth: httpx.Auth,
    start: datetime,
    end: datetime,
    *,
    max_pages: int = 500,
) -> tuple[list[dict], bool]:
    ...
    with httpx.Client(base_url=base_url, auth=auth, timeout=30.0) as client:
        while True:
            resp = client.get(
                "/api/public/observations",
                params={
                    "type": "GENERATION",
                    "limit": 100,
                    "page": page,
                    "fromStartTime": start.isoformat(),
                    "toStartTime": end.isoformat(),
                },
            )
            ...
```

Request parameters:
- `type=GENERATION` — LangFuse observation filter.
- `limit`, `page` — pagination.
- `fromStartTime`, `toStartTime` — server-side time window (ext14 #141).
- **No `sessionId` filter.**
- **No `environment` filter.**
- **No `tags` filter.**

`map_generations_to_runs` at line 224 then assigns each generation to the run whose clipped ±120s buffer contains the `startTime`, with midpoint-distance tie-break. Any generation that lands in a campaign run's buffer — regardless of origin — is silently attributed to that run.

### Reachability

Five distinct sources of foreign generations in the time window:

1. **Concurrent CLI runs (`uv run mint ...`)**. `src/config.py::LANGFUSE_ENVIRONMENT` default for runner emissions is `"default"`. A developer using MINT's CLI while a campaign is running emits GENERATIONs on the same environment, same LangFuse instance, same time window. Attribution: clipped-buffer + midpoint tie-break picks whichever campaign run is temporally closest.
2. **Dev scripts (`scripts/test_*.py`, `scripts/analyze_context_utilization.py`, `scripts/analyze_tier2_probes.py`)**. Per module-level side effect `os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", "test")` these emit on `environment="test"`. `fetch_all_generations` has no environment filter → ingests them.
3. **Pilot runs (Row 7, `run_pilot.py --condition full --n-runs 1`)**. Pilot rows are `is_pilot=TRUE` in `evaluation_runs`; `get_run_windows` at line 169 correctly filters them out for run-window construction. But `fetch_all_generations` has no `is_pilot`-equivalent filter at the LangFuse layer — pilot-emitted generations whose `startTime` falls inside a non-pilot run's buffer get attributed to that non-pilot run. Pilot runs are typically scheduled just before Phase 4 Row 8 → their generations land in Row 8's early-run buffers.
4. **Longitudinal / shift runs (Row 9 / Row 10)**. These write to separate DuckDB files (`data/eval/longitudinal.duckdb`, `data/eval/longitudinal_shifted.duckdb`) but share the same LangFuse instance. If Row 11's `$CAMPAIGN_DB` is `data/eval/metrics_<ts>.duckdb` (Phase-4 campaign) but Rows 9/10 overlap in wall-clock, their generations land in Row 8's time window and get attributed.
5. **Multi-tenant LangFuse**. If `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` grant scope beyond the current project, every foreign project's GENERATIONs in the window are ingested.

### Impact

Foreign tokens add to `run_summaries[rid]["total_tokens"]` → `compute_backfill_values` writes inflated totals → `evaluation_runs.token_cost` bias HIGH → Row 17 M4 over-reports per-task cost across JIT conditions.

Critically: existing coverage gates all detect UNDER-count:

| Gate | Detects |
|---|---|
| ext12 #122 `zero_summary_runs` | Runs with zero matches |
| ext13 #129 `per_run_missing_tasks` | Tasks missing from tagged runs |
| ext13 #130 `--min-gens-per-jit-record` | Generations-per-JIT below threshold |
| #161 pagination truncation | Missed late observations |
| #165 `unmatched` | Generations outside all run buffers |

**No existing gate detects OVER-count** from foreign ingestion. The `run_task_tokens` value for a legitimate task plus a foreign contribution looks exactly like a legitimate task with slightly more tokens — no structural signal.

### Why P0 CRITICAL

- Default-workflow-reachable without any contract violation. A single developer running MINT's CLI during Phase 4 produces sources 1 + 2.
- Sources 3 + 4 are explicitly part of the documented workflow (Row 7 pilot runs always precede Row 8; Rows 9/10 can run in parallel per "rows 12-15 are independent" language in the scoping convention section).
- Source 5 depends on LangFuse tenancy but is common on team-shared LangFuse instances.
- Directly inflates a Chapter 6 headline metric (M4 per-task cost across conditions).
- Exacerbates #160's asymmetric-gate vulnerability: #160 passes when `max(ratios) >= 0.5`; foreign ingestion can push a partially-failed condition's ratio above 0.5, masking a real partial failure.

Reviewer submitted this without an explicit severity marker (title-only), same as #162. Escalated to P0 matching the Chapter 6 headline-integrity bar used for #154 / #155 / #160 / #162.

### Proposed fix

Two-layer defense:

**Layer 1: server-side `environment` filter.** Matches runner emission convention:

```python
params: dict[str, str | int] = {
    "type": "GENERATION",
    "limit": 100,
    "page": page,
    "fromStartTime": start.isoformat(),
    "toStartTime": end.isoformat(),
    "environment": "default",  # ext15 #170: runner emission convention
}
```

`environment="default"` excludes sources 2 (`test`) but keeps sources 1, 3, 4, 5 on `default`. Not sufficient alone — must stack Layer 2.

**Layer 2: post-fetch `sessionId` whitelist.** Paired with #167 (Row 11 `--run-ids` support):

```python
# ext15 #170 Layer 2: filter by sessionId whitelist if --run-ids threaded.
# The harness sets session_id=run_id on every runner trace
# (tests/evaluation/harness.py:396), so campaign-scoping at the
# LangFuse layer mirrors the campaign's DuckDB scope.
for obs in data:
    ...
    sid = (obs.get("trace") or {}).get("sessionId") or obs.get("sessionId")
    if run_ids_whitelist and sid not in run_ids_whitelist:
        continue
    ...
```

Dependency: #167 must land first (adds `--run-ids` to Row 11 CLI). Without that, Layer 2 has no whitelist to enforce.

**Chapter 4.5 disclosure remains REQUIRED** even with both layers: foreign `environment="default"` traces from concurrent CLI users can still evade Layer 1, and session_id drift / tag loss can evade Layer 2. Disclose as an M4 upper-bound-bias limitation.

### Proposed test

```python
def test_fetch_all_generations_filters_foreign_environment(monkeypatch):
    """ext15 #170 Layer 1: server-side environment=default filter excludes
    foreign test/dev traces. Pre-fix the absence of any environment filter
    admitted traces from any emitter into M4 attribution.
    """
    captured_params = []
    class FakeClient:
        def get(self, *args, **kwargs):
            captured_params.append(kwargs["params"])
            class R:
                def raise_for_status(self): pass
                def json(self): return {"data": []}
            return R()
        def __enter__(self): return self
        def __exit__(self, *_): pass

    monkeypatch.setattr("httpx.Client", lambda *a, **kw: FakeClient())

    fetch_all_generations("http://fake", None, start, end)

    assert captured_params, "no request made"
    assert captured_params[0].get("environment") == "default", (
        "ext15 #170: fetch_all_generations must pass environment=default "
        "server-side so foreign test/dev environments don't pollute M4."
    )


def test_fetch_all_generations_filters_foreign_session_ids(monkeypatch):
    """ext15 #170 Layer 2: post-fetch sessionId whitelist when --run-ids
    is threaded. Campaign scope at the LangFuse layer must mirror DuckDB
    scope per harness.py:396 session_id=run_id convention.
    """
    # Fake API returns 3 observations: 2 with campaign session_ids, 1 foreign.
    fake_page = [
        {"id": "a", "startTime": "2026-04-22T10:00:00Z", "usage": {"total": 100}, "trace": {"sessionId": "full_00_abcdef"}},
        {"id": "b", "startTime": "2026-04-22T10:01:00Z", "usage": {"total": 200}, "trace": {"sessionId": "full_01_beefad"}},
        {"id": "c", "startTime": "2026-04-22T10:02:00Z", "usage": {"total": 9999}, "trace": {"sessionId": "foreign_cli_run"}},
    ]
    ... # mock REST client
    gens, _ = fetch_all_generations(
        ..., run_ids={"full_00_abcdef", "full_01_beefad"}
    )
    ids = {g["id"] for g in gens}
    assert ids == {"a", "b"}, "ext15 #170: foreign sessionId must be dropped"
```

### Interaction with other M4 findings

| Finding | Direction | Overlap |
|---|---|---|
| #160 (max-gate) | UNDER-count silencer | Foreign tokens mask partial failures by pushing ratios above 0.5 → compounds into false-pass. |
| #161 (pagination truncation) | UNDER-count | Orthogonal — #161 drops late real tokens; #170 adds early foreign tokens. |
| #165 (unmatched) | UNDER-count | Orthogonal — #165 measures "no match"; #170 is "wrong match." |
| #167 (no --run-ids) | Scope | Dependency — #167 unlocks Layer 2 of #170's fix. |

All four plus #170 compound on M4 integrity. #170 is the only OVER-count source in the cluster.

---

## Issue #171 — Row 17 completeness gate never verifies `COUNT(DISTINCT run_id) per condition = expected_runs_per_condition`; pseudo-replicated datasets pass and invalidate statistical independence assumptions (HIGH, defense-in-depth; downgraded from reviewer P0)

### Summary

`scripts/analyze_results.py:1573-1626`. The `--expected-runs-per-condition` flag (line 1525-1529, default 5) is consumed in exactly three derived values:

```python
expected_total = expected_conditions * expected_tasks * expected_runs  # 4*20*5 = 400
expected_per_condition = expected_tasks * expected_runs                 # 20*5  = 100
# Line 1619: per-(condition, task_id) uniformity
HAVING COUNT(*) != expected_runs  # each (condition, task_id) must have `expected_runs` rows
```

**No distinct-run-id cardinality check.** A DB where `COUNT(DISTINCT run_id) per condition = 1` with each task duplicated `expected_runs` times per (condition, task_id) satisfies every existing gate:

| Check | Pseudo-replicated DB value | Expected | Pass/Fail |
|---|---|---|---|
| total non-pilot rows | 400 | 400 | ✓ |
| per-condition count | 100 each | 100 | ✓ |
| per-condition distinct tasks | 20 each | 20 | ✓ |
| per-(condition, task_id) count | 5 each | 5 | ✓ |
| **per-condition distinct run_ids** | **1 each** | **5** | **not checked** |

Admitted silently.

### Why statistical independence matters

M1's statistical tests assume row-level independence:

- **Fisher's exact test** (line ~580 area of `analyze_results.py`): 2×2 contingency tables assume each row is an independent Bernoulli trial. Duplicated rows under one `run_id` are the same logical trial with perfectly correlated errors → effective sample size is `n_distinct_runs`, not `n_rows`. Pre-fix pseudo-replicated data yields over-precise p-values (falsely narrow).
- **Holm-Bonferroni correction**: family-wise error rate assumes test statistics are computed on independent data. Pseudo-replication inflates test statistic precision, shifts adjusted p-values, changes significance calls.
- **Clopper-Pearson 95% CI**: exact binomial CI on `k/n` assumes `n` independent trials. Pseudo-replicated `n` inflates apparent `n`, narrows CI inappropriately.

Pseudo-replicated data silently produces Chapter 6 M1 claims with invalid statistical foundations.

### Reachability

Under default workflow, `run_id = f"{condition}_{run_idx:02d}_{uuid4().hex[:6]}"` at `tests/evaluation/harness.py:302` produces 5 distinct `run_id`s per condition with ~16.7M-value collision space. Pseudo-replication of the reviewer's example (1 run_id per condition × 5 duplicates per task) is NOT reachable under the default workflow without a separate bug.

Reachable only via:

1. **#153 collision at scale** — all 5 runs in one condition collide onto one `uuid6` suffix. Probability per condition: (5 choose 2) / 2²⁴ ≈ 3×10⁻⁷ per pair × 10 pairs ≈ 3×10⁻⁶. Across 4 conditions: ≈ 10⁻⁵. Negligible.
2. **#169 overfull passage** — one `run_id` absorbs the rows of multiple logical runs (via collision + selector's `>= :expected` admit). Partial case: 2 logical runs under 1 run_id (10 rows per task_id) → violates per-(condition, task_id) uniformity check at line 1619 → existing gate catches. Pseudo-replication only reaches if the per-(condition, task_id) ratio is exactly `expected_runs` (5).
3. **`--append` re-run with same run_id repeatedly** — operator re-uses a `run_id` across sessions. Possible but contract-violating.
4. **Harness pseudo-replication bug** — a new harness feature that writes duplicate rows per logical run. Hypothetical; no current instance.

None of these are common under the default workflow. But the gate's absence is a clean structural gap: the flag `--expected-runs-per-condition` defines the expected number of distinct run_ids, and the gate should verify it.

### Why HIGH defense-in-depth (downgraded from reviewer P0)

Consistent severity framing with #152 (task identity, count-only) and #157 (condition identity, count-only): same structural antipattern applied to the run-id axis. Reviewer submitted P0; downgraded to HIGH on the same grounds — reachable only under contract violations or compounding bugs, but a clean structural-correctness gap worth closing defensively.

### Proposed fix

Add one `COUNT(DISTINCT run_id) per condition` assertion alongside the existing uniformity check:

```python
# ext15 #171: distinct-run-id cardinality gate. Complements ext7 #52 +
# ext13 #131's per-(condition, task_id) uniformity check by verifying
# row-level independence holds at the run-level. Pseudo-replicated DB
# (1 run_id × N duplicates per task) passes the uniformity check and
# silently violates Fisher / Holm / Clopper-Pearson independence
# assumptions. Severity: HIGH defense-in-depth — default workflow
# doesn't produce pseudo-replication without a separate bug (#153
# collision at scale / #169 overfull passage / --append re-use /
# harness pseudo-replication bug).
per_cond_distinct_runs = conn.execute(
    f"SELECT condition, COUNT(DISTINCT run_id) "
    f"FROM evaluation_runs WHERE is_pilot = FALSE{_SCOPE_SQL} "
    f"GROUP BY condition"
).fetchall()
for condition, n_runs in per_cond_distinct_runs:
    if n_runs != expected_runs:
        issues.append(
            f"condition '{condition}': {n_runs} distinct run_ids, "
            f"expected {expected_runs} (pseudo-replication risk — "
            f"M1 Fisher/Holm/Clopper-Pearson assume row-level "
            f"independence; see ext15 #171)"
        )
```

Integrates with existing `issues` list → existing `--allow-incomplete` override covers it.

### Proposed test

```python
def test_completeness_gate_detects_pseudo_replication(conn):
    """ext15 #171: the gate must detect when a condition has fewer distinct
    run_ids than --expected-runs-per-condition. Pseudo-replicated DB (1
    run_id × 5 duplicates per task × 20 tasks per condition) passes ext7
    #52 + ext13 #131 silently pre-fix.
    """
    # Populate: 1 run_id per condition, each task duplicated 5x.
    for cond in ["full", "no_verification", "no_optimization", "static"]:
        rid = f"{cond}_shared_run"
        for task in ALL_TASK_IDS:
            for _ in range(5):
                _insert_evaluation_run(conn, run_id=rid, condition=cond, task_id=task)

    rc = _run_main(["--run-ids", *{...}])

    assert rc == 2, "ext15 #171: pseudo-replicated DB must fail the gate"
    assert "distinct run_ids" in captured.err
    assert "pseudo-replication" in captured.err
```

### Interaction with other findings

- **#153** (run_id collision) — one cause of pseudo-replication shape via collisions.
- **#169** (overfull admit) — the selector admits pseudo-replicated run_ids into `$CAMPAIGN_RUN_IDS`. #171 catches the downstream consequence even if #169 lets the run_id through.
- **#167** (Row 11 no --run-ids) — unrelated axis; #171 is analyzer-layer defense.
- Bundle-family consistency: #152 + #157 + #171 all apply the same "count-only completeness, identity/cardinality not checked" shape to three different axes (task_id, condition, run_id). All three downgraded from reviewer-submitted P0 → HIGH defense-in-depth on the same grounds.

---

## Issue #172 — `analyze_longitudinal.py` completeness gate uses `len(cycles) < expected_cycles` strict inequality (shortfall only); overfull merged timelines silently pass (P1, confirmed)

### Summary

`scripts/analyze_longitudinal.py:743-765`:

```python
# ext12 #116: completeness guard. A 5-cycle stub from an aborted
# campaign must not silently pass as a complete run.
incomplete_banner: str | None = None
if len(cycles) < args.expected_cycles:
    if not args.allow_incomplete:
        print(
            f"ERROR: longitudinal run has {len(cycles)} cycles, expected "
            f"{args.expected_cycles}. Pass --allow-incomplete to proceed "
            f"with an [INCOMPLETE] banner, or --expected-cycles N to "
            f"override the expected count.",
            file=sys.stderr,
        )
        conn.close()
        return 3
    incomplete_banner = (
        f"[INCOMPLETE: {len(cycles)}/{args.expected_cycles} cycles]"
    )
    ...
```

ext12 #116 was designed for "aborted stub detection" — a 5-cycle run where 40 were expected. The guard uses `<`, not `!=`. A timeline with `len(cycles) > expected_cycles` passes silently, produces no `[INCOMPLETE]` banner, and proceeds to compute convergence / KL / adaptation metrics on the overfull data.

### Reachability (longitudinal)

Overfull longitudinal timeline requires one of:

1. **#153 collision between two longitudinal runs.** Longitudinal runners emit their own run_ids (`run_longitudinal.py` + `run_longitudinal_shift.py`); the `uuid4().hex[:6]` suffix is the same 24-bit space. If two campaigns collide on the same longitudinal run_id, their 40+40=80 cycles merge under one key.
2. **Duplicate writes** from retry logic during `cycle_flush` or `compute_all_utilities` writes. Each cycle's persistence goes through `EvaluationStore.record_cycle_utility_scores` + `record_cycle_events`; transient DuckDB errors that are retried can produce duplicate rows.
3. **`--append` re-run with same longitudinal run_id** — operator resumes a crashed campaign by manually re-using the `run_id`.

### Impact

Convergence metrics are cycle-indexed:

- **Utility-score KL convergence** (`_compute_kl_divergence` over cycle_utility_scores): reads `(cycle_index, capability_id, utility)` tuples. Merged timeline has two cycle 1 entries, two cycle 2 entries, etc. — the inner KL computation may divide by two, may double-count, depending on whether downstream aggregation is sum or mean per `cycle_index`.
- **Cycles-to-convergence**: the threshold-crossing cycle depends on KL monotonicity; merged cycles scramble the monotone series.
- **Distribution-shift adaptation verdict** (Phase A/B detection): `_detect_shift_point()` looks for a KL spike at the `--shift-at` index; merged cycles misalign the shift cycle or double-fire the detection.

All three Chapter 6 RQ #2 + RQ #3 outputs silently corrupted without a loud signal.

### Proposed fix

```python
# ext15 #172: symmetric completeness check. Pre-fix ext12 #116's
# `<` admits overfull timelines silently. Merged timelines from
# #153 / duplicate writes / --append re-use produce invalid
# convergence / adaptation metrics on Row 18/19 output.
if len(cycles) != args.expected_cycles:
    direction = "too few" if len(cycles) < args.expected_cycles else "too many"
    if not args.allow_incomplete:
        print(
            f"ERROR: longitudinal run has {len(cycles)} cycles, expected "
            f"{args.expected_cycles} ({direction}). Pass --allow-incomplete "
            f"to proceed with an [INCOMPLETE] banner, or --expected-cycles "
            f"N to override the expected count. {direction.capitalize()} "
            f"cycles indicate possible duplicate writes, #153 run_id "
            f"collision, or --append re-use.",
            file=sys.stderr,
        )
        conn.close()
        return 3
    incomplete_banner = (
        f"[{direction.upper()}: {len(cycles)}/{args.expected_cycles} cycles]"
    )
    ...
```

### Proposed test

```python
def test_longitudinal_analyzer_rejects_overfull_timeline(conn):
    """ext15 #172: analyzer must refuse len(cycles) != expected_cycles
    on BOTH sides. Pre-fix `< expected` passed overfull timelines
    silently.
    """
    # Populate 80 cycles for a single run_id (merged timeline shape)
    _populate_cycle_utility_scores(conn, run_id="r1", n_cycles=80)

    rc = _run_longitudinal_main(["--run-id", "r1", "--expected-cycles", "40"])

    assert rc == 3, (
        "ext15 #172: overfull timeline (80 cycles, 40 expected) must fail "
        "exit 3, not silently proceed to convergence analysis"
    )
    assert "too many" in captured.err
```

---

## Issue #173 — Row 18/19 selectors (`LONGITUDINAL_RUN_ID`, `SHIFT_RUN_ID`) admit overfull merged run_ids via `HAVING COUNT(*) >= EXPECTED_LONGITUDINAL_ROWS` (P1, confirmed)

### Summary

`docs/run_sheet.md:142-169`:

```bash
export LONGITUDINAL_RUN_ID="$(uv run python -c "
import duckdb, sys
conn = duckdb.connect('data/eval/longitudinal.duckdb')
row = conn.execute(
    'SELECT run_id FROM evaluation_runs GROUP BY run_id '
    'HAVING COUNT(*) >= ? ORDER BY MAX(timestamp) DESC LIMIT 1',
    [${EXPECTED_LONGITUDINAL_ROWS}],
).fetchone()
...
")"
export SHIFT_RUN_ID="$(uv run python -c "
import duckdb, sys
conn = duckdb.connect('data/eval/longitudinal_shifted.duckdb')
row = conn.execute(
    'SELECT run_id FROM evaluation_runs GROUP BY run_id '
    'HAVING COUNT(*) >= ? ORDER BY MAX(timestamp) DESC LIMIT 1',
    [${EXPECTED_LONGITUDINAL_ROWS}],
).fetchone()
...
")"
```

Both selectors use `HAVING COUNT(*) >= ?` — identical asymmetric-check pattern as #169 on the Phase-4 selector. An overfull merged longitudinal run_id (80 rows via #153 collision, duplicate writes, or `--append` re-use) passes the `>= 40` threshold, wins `ORDER BY MAX(timestamp) DESC LIMIT 1`, gets exported.

### Stacking with #172

Row 18/19 execution flow:

1. **Selector** (run sheet): picks overfull run_id (admitted by `>=`) as the "latest complete."
2. **Analyzer** (`analyze_longitudinal.py`): receives the overfull run_id via `--run-id`, reads `cycle_utility_scores` + `cycle_events` for that run_id, assembles `cycles` list.
3. **Completeness gate** (#172): `len(cycles) < expected_cycles` doesn't fire on overfull → timeline accepted.
4. **Convergence / KL / adaptation compute**: runs on merged cycles.
5. **Row 18/19 output**: prints corrupted Chapter 6 RQ #2 + RQ #3 numbers with no warning.

Either #172 OR #173 fixed in isolation is insufficient for end-to-end integrity:

- #172 alone (analyzer strict equality, selector still `>=`): selector still picks overfull → analyzer refuses → Row 18/19 exits 3. Loud failure, but in the wrong spot (analyzer blames the operator rather than the selector identifying the merged run_id).
- #173 alone (selector strict equality, analyzer still `<`): selector refuses → exits with "no complete longitudinal run" → operator can still force analyzer with `--run-id <manually-picked>` and hit the shortfall-only analyzer gate.

Land both. Fix family is identical to #169's (strict equality + bidirectional report).

### Proposed fix

```sql
-- ext15 #173: strict equality. Sibling of #169 on longitudinal selectors.
SELECT run_id FROM evaluation_runs
GROUP BY run_id
HAVING COUNT(*) = ?
ORDER BY MAX(timestamp) DESC LIMIT 1
```

And extend the error path to classify overfull as its own cause:

```bash
if row is None:
    # Distinguish underfull (no data) from overfull (merged run_ids) in
    # the error message for operator debugging.
    n_overfull = conn.execute(
        'SELECT COUNT(*) FROM ('
        '  SELECT run_id FROM evaluation_runs GROUP BY run_id '
        '  HAVING COUNT(*) > ?'
        ')', [${EXPECTED_LONGITUDINAL_ROWS}],
    ).fetchone()[0]
    n_underfull = conn.execute(
        'SELECT COUNT(*) FROM ('
        '  SELECT run_id FROM evaluation_runs GROUP BY run_id '
        '  HAVING COUNT(*) < ?'
        ')', [${EXPECTED_LONGITUDINAL_ROWS}],
    ).fetchone()[0]
    sys.exit(
        f'ERROR: no complete longitudinal run in DB '
        f'(expected exactly {{${EXPECTED_LONGITUDINAL_ROWS}}} rows). '
        f'Found {n_overfull} overfull run_ids (possible #153 collision / '
        f'duplicate writes / --append re-use) and {n_underfull} underfull '
        f'run_ids (aborted stubs). Lower EXPECTED_LONGITUDINAL_ROWS for '
        f'underfull diagnosis; run `grep run_id` against evaluation_runs '
        f'for overfull.'
    )
```

### Proposed test

Extend `tests/unit/test_run_sheet.py`:

```python
def test_longitudinal_selectors_use_strict_equality():
    """ext15 #173: LONGITUDINAL_RUN_ID and SHIFT_RUN_ID selectors must use
    COUNT(*) = :expected strict equality, not `>=`. Pre-fix overfull merged
    run_ids from #153 / duplicate writes / --append re-use were admitted
    silently as 'latest complete.'
    """
    text = RUN_SHEET.read_text()
    # Find both selector SQL blocks.
    for label in ["LONGITUDINAL_RUN_ID", "SHIFT_RUN_ID"]:
        block_match = re.search(
            rf"{label}=\"\$\(.*?\)\"", text, re.DOTALL,
        )
        assert block_match, f"{label} selector missing"
        block = block_match.group(0)
        assert "COUNT(*) = ?" in block or "COUNT(*) = ?" in block, (
            f"ext15 #173: {label} selector must use strict COUNT(*) = :expected, "
            f"not `>=`. Matches #169's Phase-4 selector fix."
        )
```

---

## Parent-document integration (pending)

When ext15 lands, fold forward into `security_audit_2026_04_19.md`:

1. Update header: "One hundred fifty findings → One hundred seventy-three findings; #151-#173 open as of 2026-04-22."
2. Insert TL;DR rows #151-#173 after ext14's #150.
3. Tranche 3 (eval-blocking) gains **four HIGH defense-in-depth** (#151, #152, #157, #171) + **five P0 CRITICAL confirmed** (#154, #155, #160, #162, #170) + **thirteen P1 confirmed** (#156, #158, #159, #161, #163, #164, #165, #166, #167, #168, #169, #172, #173) + **one P1 latent** (#153). Coverage map by bundle: (A) #151-#153 CAMPAIGN_RUN_IDS selector + completeness/identity gates; (B) #154-#156 LangFuse-analyzer session-coverage gates vs per-condition emission invariants; (C) #157-#159 asymmetric-check completeness gates + filter-scoping mismatches on the condition axis; (D) #160-#161 M4 silent-truncation + asymmetric-gate pair; (E) #162-#164 Row 17 headline-table correctness; (F) #165-#167 Row 11 M4 backfill integrity; (G) #168-#169 Row 15 prose-vs-command desync + CAMPAIGN_RUN_IDS upper-bound asymmetry; (H) #170 Row 11 foreign-generation attribution (standalone P0 CRITICAL — only OVER-count source in the M4 cluster); (I) #171-#173 run-id cardinality gap + longitudinal upper-bound asymmetries.
4. Extend "Behavioral regressions expected":
   - (#151) `docs/run_sheet.md` CAMPAIGN_RUN_IDS discovery SQL gains `ROW_NUMBER() OVER (PARTITION BY condition ORDER BY MAX(timestamp) DESC)` per-condition recency ranking; keeps ext14 #145's `HAVING COUNT(*) >= N AND COUNT(DISTINCT task_id) = M` completeness filter; new env var `EXPECTED_RUNS_PER_CONDITION` (default 5, matches `--n-runs`); stderr WARNING on dropped cross-campaign run_ids; exits 2 on zero complete run_ids.
   - (#152) `scripts/analyze_results.py::main` issue-accumulation block gains canonical-set assertion against `tests/evaluation/tasks.ALL_TASKS`. Drifted task_id sets fail at exit 2 with `non-canonical task_ids [...]` / `missing canonical task_ids [...]`.
   - (#153) `tests/evaluation/harness.py:302` bumps `uuid.uuid4().hex[:6]` → `uuid.uuid4().hex`.
   - (#154) `scripts/analyze_tier2_probes.py::main` runs a second `fetch_tier2_events` call with `event_names=(reject, pass)` for coverage-gate semantics.
   - (#155) `docs/run_sheet.md` defines `CAMPAIGN_TIER2_SESSION_IDS` filtering to `condition IN ('full', 'no_optimization')`.
   - (#156) `docs/run_sheet.md` defines `CAMPAIGN_DEEPAGENT_SESSION_IDS` filtering to `condition != 'static'`.
   - (#157) `scripts/analyze_results.py::main` gains symmetric condition-identity check. Pre-fix `if len(condition_map) < expected_conditions:` guard removed; missing + extra computed unconditionally.
   - (#158) `scripts/audit_contamination.py` ext14 #143 empty-sample fallback extends filter threading to `conditions` / `types` / `since` / `generated_artifact_count > 0`.
   - (#159) `scripts/analyze_results.py::print_contamination_rate` gains `condition IN (...)` WHERE-clause + symmetric extra-check assertion.
   - (#160) `scripts/analyze_results.py::print_token_cost_summary` swaps `max(jit_ratios) < 0.5` → per-condition enforcement. Any JIT condition below 50% non-zero-cost threshold trips the gate with `M4 partial silent zero: JIT conditions below 50%...: [full=1.00, no_verification=0.00, no_optimization=0.00]` error. Returns False → ext13 #126 missing-metrics path → exit 7 unless `--allow-missing-metrics`.
   - (#161) `scripts/backfill_token_costs.py::fetch_traces_by_session` signature becomes `-> tuple[list[dict], bool]`; WARNING log + `truncated` return flag at max-pages ceiling; new `--max-pages` CLI flag (default 200); main() refuses with exit 5 unless `--allow-truncated` passed (reuses ext14 #140 / #141 contract).
   - (#162) `scripts/analyze_results.py::print_gini_summary` aggregates invocation counts by `capability_id` across runs before computing the Aggregate-column Gini. Pre-fix path fed raw per-(run, cap) cell counts into `_gini_coefficient`; post-fix pools first. Header text at 966-968 now matches compute. Optional "Cell Gini" diagnostic column can be added for transparency; proposal-aligned headline is the pooled-by-capability number.
   - (#163) `scripts/analyze_results.py` label call-sites (module docstring, cluster print at 634, Table 1 title at 1651, inline comment at 1653, Table 2 title at 1657) switch to derived labels via `_task_range_label(task_ids)` helper. Five hard-coded stale ranges (T04-T13 / T04-T18 / T01-T18) replaced by expressions that track `ALL_TASK_IDS` / `SYNTHESIS_TASK_IDS` / `RETRIEVAL_TASKS`.
   - (#164) `scripts/analyze_results.py::print_classification_accuracy` signature becomes `-> bool`; three skip paths return `False`; happy-path returns `True`. main() threads result into `missing_metrics` at 1701-1718 via `if not print_classification_accuracy(conn): missing_metrics.append("Classification Accuracy / Pillar 1 RQ #1")`. Override via existing `--allow-missing-metrics`. Closes the ext13 #126/#128/#132 + ext14 #149 retrofit sweep for the fourth Chapter 6 metric.
   - (#165) `scripts/backfill_token_costs.py::main` adds `--max-unmatched-ratio` (default 0.05) + `--allow-unmatched` CLI args; unmatched-ratio threshold gate at line 908 area refuses with exit 4 unless override. Chapter 4.5 disclosure REQUIRED on override. Complements ext12 #122 per-run zero-match gate with the symmetric "generations matched no run window" population gate.
   - (#166) `scripts/analyze_results.py::print_classification_accuracy` SQL filter swaps `condition != 'static'` → `condition IN ('full', 'no_verification', 'no_optimization')`. Optional extras-detection via sibling query surfaces non-baseline labels in scope (`longitudinal`, enum drift) as a stderr warning, integrable with ext15 #164's `missing_metrics` exit-7 path.
   - (#167) `scripts/backfill_token_costs.py` parser gains `--run-ids` + `--since` args (mutually exclusive); three internal queries (fetch_run_windows, task_rows, get_per_record_info) thread a `_build_scope_sql(args)` WHERE-fragment through parameterized queries. `docs/run_sheet.md` Row 11 invocation gains `--run-ids $CAMPAIGN_RUN_IDS`. `tests/unit/test_run_sheet.py` `_DB_READERS_TO_SCOPE` gains `"backfill_token_costs.py": {"--run-ids", "--since"}` so the existing `test_every_db_reader_row_passes_scope_flag` lint enforces scope on Row 11 (symmetric to rows 14/16/17).
   - (#168) `docs/run_sheet.md:182` Row 15 command gains `--include-pass` flag; prose is clarified to distinguish the coverage-gate population (`pass + reject` for session-presence check, closes #154 interaction) from the analysis population (`reject` for per-category detection-rate table). Run-sheet lint guards that Row 15 passes `--include-pass` whenever the prose mentions `{pass,reject}`.
   - (#169) `docs/run_sheet.md` CAMPAIGN_RUN_IDS admit SQL tightens `HAVING COUNT(*) >= :expected_rows` → `= :expected_rows` (strict equality, matches the existing `COUNT(DISTINCT task_id) = :expected_tasks` semantic). Dropped-report SQL extends to `!=` on both bounds with classified output ("OVERFULL" vs "underfull" section headers). New run-sheet lint in `tests/unit/test_run_sheet.py` enforces the symmetric pattern. Closes the upper-bound sibling to ext14 #145's completeness filter.
   - (#170) `scripts/backfill_token_costs.py::fetch_all_generations` adds server-side `environment="default"` filter (Layer 1); post-fetch `sessionId` whitelist against campaign `run_ids` when `--run-ids` is threaded per #167 (Layer 2). Chapter 4.5 disclosure REQUIRED — foreign `environment="default"` concurrent CLI traces can still evade Layer 1. Dependency: #167 must land first to enable Layer 2. Closes the only OVER-count source in the M4 cluster (#160/#161/#165 are all UNDER-count; #170 is the complement).
   - (#171) `scripts/analyze_results.py::main` issue-accumulation block adds `COUNT(DISTINCT run_id) per condition = :expected_runs_per_condition` assertion alongside the existing per-(condition, task_id) uniformity check. Pseudo-replicated DB (1 run_id × N duplicates per task × 20 tasks per condition) fails at exit 2 with `condition '<c>': <n> distinct run_ids, expected <:expected_runs> (pseudo-replication risk — M1 Fisher/Holm/Clopper-Pearson assume row-level independence)`. Override via existing `--allow-incomplete`. Symmetric to #152 / #157 identity-check additions on the run-id axis.
   - (#172) `scripts/analyze_longitudinal.py` completeness gate at line 746 swaps `if len(cycles) < args.expected_cycles` → `if len(cycles) != args.expected_cycles` with direction-aware error messaging (`too few` / `too many — possible duplicate writes, #153 run_id collision, or --append re-use`). Chapter 6 RQ #2 + RQ #3 integrity protected from merged-timeline corruption.
   - (#173) `docs/run_sheet.md` LONGITUDINAL_RUN_ID + SHIFT_RUN_ID selectors at lines 142-169 tighten `HAVING COUNT(*) >= ?` → `HAVING COUNT(*) = ?` strict equality. Error path extends with overfull-vs-underfull classification for operator debugging. Run-sheet lint in `tests/unit/test_run_sheet.py` enforces the pattern. Sibling to #169 on the longitudinal DB family.
5. End-of-audit note: "Extended 2026-04-22 (fifteenth extension) with findings #151-#173 (detail sections in `security_audit_2026_04_19_ext15.md`). Twenty-three findings, nine thematically-distinct bundles: (A) #151-#153 CAMPAIGN_RUN_IDS selector + completeness/identity gates — two HIGH defense-in-depth + one P1 latent; (B) #154-#156 LangFuse-analyzer session-coverage gates vs per-condition emission invariants — two P0 CRITICAL + one P1; (C) #157-#159 asymmetric-check completeness gates + filter-scoping on condition axis — one HIGH defense-in-depth + two P1; (D) #160-#161 M4 silent-truncation + asymmetric-gate pair — one P0 CRITICAL + one P1; (E) #162-#164 Row 17 headline-table correctness — one P0 CRITICAL + two P1; (F) #165-#167 Row 11 M4 backfill integrity — three P1; (G) #168-#169 Row 15 prose-vs-command desync + CAMPAIGN_RUN_IDS upper-bound asymmetry — two P1; (H) #170 Row 11 foreign-generation attribution — one P0 CRITICAL (default-workflow-reachable; the only OVER-count source in the Bundle D/F/G M4 cluster); (I) #171-#173 run-id cardinality gate gap + longitudinal upper-bound asymmetries — one HIGH defense-in-depth + two P1. Default-workflow blockers: Bundle B (#154+#155 for Row 15, #156 for Row 13), Bundle D (#160 for Row 17 M4), Bundle E (#162 for Row 17 M3, #164 for Row 17 Pillar 1 RQ #1), Bundle H (#170 for Row 11 M4). Contract-violation-reachable (requires ext7 #49 violation or equivalent): Bundles A + C + F-partial + G-partial + I-partial. **Recurring cross-bundle pattern:** the 'stated semantics and realized compute/check diverge, in a direction that systematically silences failure' structural antipattern now has **twelve confirmed instances** across the analyzer family — #152 (task identity, count-only), #157 (condition identity, count-only), #159 (M2 condition extras, missing-only), #160 (M4 partial zero, max-only), #162 (M3 Aggregate, cell-level-vs-pool-level), #163 (label-vs-scope desync post-ext6 #17), #164 (missing-metrics retrofit gap), #165 (unmatched-population ungated complement to ext12 #122), #166 (classification-confusion blacklist-vs-whitelist), #168 (prose-vs-command denominator ambiguity), #169 + #173 (selector upper-bound asymmetry on Phase-4 + longitudinal), #171 (run-id cardinality never checked), #172 (longitudinal analyzer shortfall-only), #167 (scoping-contract unsatisfiability), #170 (foreign-generation attribution via missing scope filter — the OVER-count counterpart to the antipattern family). **Ext16 should continue the sweep on remaining analyzers** (Row 12 `backfill_definition_token_costs.py`, Row 13 `analyze_context_utilization.py` confusion-equivalent surfaces, Row 14 `analyze_compression_loss.py`, further `analyze_longitudinal.py` compute surfaces) — the antipattern likely recurs in per-cycle convergence gates (`max` over cycles), per-agent utility-score gates, per-capability reuse-distribution gates, label-vs-scope desync sites after future `ALL_TASKS` extensions, further prose-vs-command divergences in the remaining row cells, additional upper-bound blind spots in secondary completeness filters, and additional missing-scope-filter OVER-count sources analogous to #170."

---

**End of extension.**
