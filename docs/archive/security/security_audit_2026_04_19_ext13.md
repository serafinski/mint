# Security / Robustness Audit — 2026-04-19, Thirteenth Extension (#124-#138)

**Parent document:** [`security_audit_2026_04_19.md`](./security_audit_2026_04_19.md) — #1-#18 all ✅ FIXED 2026-04-20.
**Sibling extensions:**
- [`security_audit_2026_04_19_ext4.md`](./security_audit_2026_04_19_ext4.md) — #12-#14 ✅ (folded into parent).
- [`security_audit_2026_04_19_ext5.md`](./security_audit_2026_04_19_ext5.md) — #15-#18 ✅ (folded into parent).
- [`security_audit_2026_04_19_ext6.md`](./security_audit_2026_04_19_ext6.md) — #19-#43 ✅ all FIXED 2026-04-20.
- [`security_audit_2026_04_19_ext7.md`](./security_audit_2026_04_19_ext7.md) — #44-#55 ✅ all FIXED 2026-04-20.
- [`security_audit_2026_04_19_ext8.md`](./security_audit_2026_04_19_ext8.md) — #56-#78 (20 ✅ FIXED + 1 ❎ false-positive (#56) + 1 ✅ partial (#63, Chapter 7 remainder) + 2 ❎ acknowledged (#61 MEDIUM latent, #70 MEDIUM methodology). Genuinely-code-open: 2 — #61, #63).
- [`security_audit_2026_04_19_ext9.md`](./security_audit_2026_04_19_ext9.md) — #79-#98 ✅ **all FIXED** (re-verified 2026-04-21 by direct code-comment cross-reference; every finding carries a pinned fix).
- [`security_audit_2026_04_19_ext10.md`](./security_audit_2026_04_19_ext10.md) — #98-#108 (re-verified 2026-04-21, second pass: #98-#107 ✅ FIXED; #108 ⚠ latent/documented — list still in execution order but `ChainResult.final_output` property + docstring document the hazard; all current consumers use the safe access path, audit-classified MEDIUM latent).
- [`security_audit_2026_04_19_ext11.md`](./security_audit_2026_04_19_ext11.md) — #109-#113 ✅ all FIXED 2026-04-21.
- [`security_audit_2026_04_19_ext12.md`](./security_audit_2026_04_19_ext12.md) — #114-#123 ✅ all FIXED 2026-04-21 (commit `7c8455d`).

**This extension's scope.** Three **CRITICAL** findings surfaced 2026-04-21 via reviewer-run direct reproduction after ext12 landed. All three are silent-metric-degradation / silent-success bugs that corrupt Chapter 6's thesis-core metrics while the pipeline appears to succeed. Each is orthogonal to ext12's findings (not duplicates, not covered by ext12 remediations):

- **#124** — `analyze_longitudinal.py::_detect_kl_source` silently downgrades from proposal-literal utility-score KL to the invocation-count proxy when `cycle_utility_scores` is absent. Prints a "(cycle_utility_scores empty — DB predates Step 18.7 follow-up wiring)" note and exits 0 on convergence. ext12 #118's data-presence gate catches the "ALL three tables empty" case but not "utility scores empty but invocations populated" — exactly the production failure shape when ext12 #71/#113 persistent-store isolation is imperfect or when Step 18.7 flush partially lands.
- **#125** — `tests/evaluation/cycle_flush.py` wraps ALL THREE persistence writes (`invocation_events`, `cycle_utility_scores`, `cycle_events`) in broad `try/except Exception` at lines 73, 99, 113; each logs a warning and proceeds. The docstring explicitly names "never-raises contract" as a design decision. Both longitudinal runners call this helper at the per-task boundary and have no way to detect partial flush failure. Rows 9/10 finish with silently-missing telemetry and feed #124 / #116 / #118 directly.
- **#126** — `scripts/analyze_results.py::print_gini_summary` treats an empty `invocation_events` table as "skipped" (line 887-889), returns normally, and the main flow at line 1500 continues through the remaining sections. Line 1519-1520 `if __name__ == "__main__": main()` → exit 0 with M3 (Gini, Pillar 3 RQ #1 headline) silently missing from Chapter 6 output. No loud-fail, no exit-code escalation, no banner.

**Status update 2026-04-21 (ext13 remediation sweep):** all fifteen findings **✅ FIXED** in a single pass. Every finding carries a pinned `ext13 #N` comment at the fix site, every new exit code / CLI flag is documented in the affected script's docstring, and the targeted unit-test suite (1955 tests) passes post-fix. Two pre-existing ext12 e2e tests (`test_completeness_guard_refuses_partial_dataset`, `test_analyze_longitudinal_exits_5_on_shift_db_without_marker`) were updated to match the new contracts (`main()` returns code rather than calling `sys.exit()`; `--allow-proxy-kl` added where tests pre-seed an empty `cycle_utility_scores` table that now rightly trips #124's gate).

**Status update 2026-04-21 (cross-audit re-verification sweep, post-ext12 commit `7c8455d`):** ext9 + ext10 + ext11 + ext12 re-verified against current code; stale "open" claims in prior headers corrected. Genuinely-open count OUTSIDE this extension: **2** (ext8 #61 MEDIUM latent intentional; ext8 #63 P0 PARTIAL — WARN-log only, audit §A full AST-extract NOT implemented). Latent/documented: **2** (ext10 #108 MEDIUM; ext8 #70 methodology-disclosure-only).

| # | Severity | Scope | Impact | Status |
|---|---|---|---|---|
| 124 | **P0 CRITICAL** | `scripts/analyze_longitudinal.py` `primary_kl_source` fallback to invocation-counts proxy | Missing `cycle_utility_scores` → silent downgrade to proxy KL; analyzer reports convergence + exits 0. Chapter 6 Pillar 3 RQ #2 convergence claim computed on proxy data while appearing proposal-literal | ✅ FIXED |
| 125 | **P0 CRITICAL** | `tests/evaluation/cycle_flush.py:73, 99, 113` three independent try/except broad-catch; never-raises contract | Per-task flush failures logged as warnings and discarded. Runners at `run_longitudinal.py:353` + `run_longitudinal_shift.py:442` invoke the helper with no success check → Rows 9/10 finish with silently-missing telemetry; directly feeds #124 / #116 / #118 | ✅ FIXED |
| 126 | **P0 CRITICAL** | `scripts/analyze_results.py::print_gini_summary` empty `invocation_events` → skip message → return; no exit-code propagation | Row 17 prints "(skipped: invocation_events table is empty)" and exits 0 with M3 missing. Chapter 6 Pillar 3 RQ #1 evidence absent while pipeline reports success | ✅ FIXED |
| 127 | **P0 CRITICAL** | `analyze_longitudinal.py:732-738` data-presence gate is global `any(...)` not per-cycle completeness | A 40-cycle DB with ONE invocation event total passes the ext12 #118 gate; remaining cycles have empty-vs-empty KL (NaN) except where the single event lands. Detector still finds a sub-threshold streak, reports convergence at cycle 2, exits 0. Thesis-critical convergence can derive from a single row of signal | ✅ FIXED |
| 128 | **P0 CRITICAL** | `scripts/analyze_results.py::print_gini_summary` partial-condition M3 coverage silently dashed | When `invocation_events` has rows only for SOME conditions (e.g., only `full`), the per-condition loop at line 940-943 prints dashes for missing conditions and the script exits 0. Chapter 6 M3 table has per-condition gaps with no loud-fail. ext13 #126's fix covers ALL-empty; partial-per-condition slips through | ✅ FIXED |
| 129 | **P0 CRITICAL** | `scripts/backfill_token_costs.py:652-656` tagged-run path doesn't validate per-task tag coverage | ext12 #122's `zero_summary_runs` check is run-level only (`n_generations > 0`). For a tagged run where only a subset of `(run_id, task_id)` pairs have traces, `run_task_tokens.get(key, 0)` silently returns 0 for missing tasks. Row 11 writes token_cost=0 for those tasks and exits 0 | ✅ FIXED |
| 130 | **P0 CRITICAL** | `scripts/backfill_token_costs.py:780-783` + per-condition coverage display at line 785-796 treat any `n_generations > 0` as "covered"; no expected-task-count denominator | A 20-task run with a single matched LangFuse generation passes ext12 #122's gate, prints "1/1 covered" in the per-condition breakdown, and writes `total_tokens / n_jit_tasks ≈ (small) / 17` as per-task cost across every record. Row 11 exits 0; Chapter 6 M4 silently under-reports the whole run's cost while the log looks like a green coverage line | ✅ FIXED |
| 131 | **P0 CRITICAL** | `scripts/analyze_results.py:1411-1431` ext7 #52 completeness gate is row-count-only; ignores distinct-task coverage per condition | A 400-row DB where every row is T01 (same task repeated 100× per condition) passes the gate. Chapter 6 tables aggregate 400 rows but cover 1/20 of the testbed. M1/M4/M5 all silently compute on the single-task subset | ✅ FIXED |
| 132 | **P0 CRITICAL** | `scripts/analyze_results.py:1246-1272` `print_contamination_rate` has no per-condition completeness guard for M2 labels | Only loud-fails when `human_label` is globally unpopulated. A DB with labels in only one condition prints the M2 table for that condition and exits 0. Chapter 6's JIT-vs-static contamination comparison has no baseline to compare against; exit-code layer says "complete" | ✅ FIXED |
| 133 | **P0 CRITICAL** | `scripts/analyze_context_utilization.py:607-617` gate tests `total_traces == 0` across ALL agents | If task-decomposer has traces and the other three agent families are empty, the gate passes. Row 13 prints dashes for missing agents and exits 0. Chapter 6 Context Economy per-agent comparison has missing rows with no loud-fail | ✅ FIXED |
| 134 | **P0 CRITICAL** | `scripts/test_registry_matching.py:441-450` judge-exception handler sets `verdict=None`; `scripts/test_registry_matching.py:1768-1781` pass computation filters `verdict is not None` | A full LLM-judge outage (every Phase B/C call raises) produces `pos_audited=[]`, `all_pass_b=True`. Row 6a prints "OVERALL: PASS" and exits 0 with zero actual audits performed | ✅ FIXED |
| 135 | **P0 CRITICAL** | `scripts/test_registry_fn.py:273-275` `continue` skips capabilities with no paraphrases; `scripts/test_registry_fn.py:301-304` only fails when `total_queries == 0` | Paraphrase-generation failures silently skip capabilities from the audit. A run with 1/8 capabilities probed and 7 skipped passes `total_queries > 0`, computes recall on the 1-cap subset, reports "100% recall" and exits 0 | ✅ FIXED |
| 136 | **P0 CRITICAL** | `scripts/test_pipeline.py:187` empty `verification_results` doesn't flip `all_pass`; line 212-217 "Gaps existed but no new capabilities registered" is WARN only | Row 2 smoke test is fail-open on core pipeline stages. A patched pipeline returning `gaps>0, artifacts>0, verification_results=[], registry delta 0` prints WARN and returns `all_pass=True`. Row 2 reports PASS on a pipeline that skipped verification entirely and registered nothing | ✅ FIXED |
| 137 | **P0 CRITICAL** | `scripts/audit_contamination.py:667-680` three auto-skip branches (no `cap_name`, seeded, source=None) `continue` without counter; line 709 `return 0` unconditionally | Row 16 can "succeed" with zero labels produced. A DB whose unlabeled rows all resolve to legacy-path source-missing (common post-snapshot-reset) silently skips every row without incrementing `counts["skip"]` (different branch than user-manual skip). Final summary shows `correct=0 contaminated=0 partial=0`, exit 0; Chapter 6 M2 audit silently didn't happen | ✅ FIXED |
| 138 | **P0 CRITICAL** | `scripts/analyze_tier2_probes.py:471` prints `Fetched 0 events.` without gate; line 479 `return 0` unconditionally | Row 15 evidence integrity bypassable by any session_id filter typo or window mismatch. `analyze_tier2_probes.py --session-ids <nonexistent>` prints "Fetched 0 events" + empty table, exits 0. Chapter 6 Tier 2 probe-finding evidence silently absent while Row 15 reports success | ✅ FIXED |

---

## Issue #124 — `analyze_longitudinal.py` silently downgrades convergence metric from proposal-literal utility-score KL to invocation-count proxy; still exits 0 (P0 CRITICAL) — ✅ FIXED 2026-04-21

**Fix landed:** `scripts/analyze_longitudinal.py` — new `--allow-proxy-kl` CLI flag; when `cycle_utility_scores` is empty the analyzer refuses with exit 6 unless the override is set, and every section header is prefixed `[PROXY KL]` when proceeding under the override. Module-level `_PROXY_BANNER` interpolated in `_print_header`. Updated exit-code table in the trailing docstring.

### Summary

Step 18.7 established utility-score KL as the proposal-literal convergence metric (mirroring `src.optimizer.loop._compute_kl_divergence`), with invocation-count KL as a "sensitivity series" fallback. `analyze_longitudinal.py` carries this distinction in a `primary_kl_source` variable:

```python
has_utility_scores = any(c.get("utility_scores") for c in cycles)
has_events = any(c.get("events") for c in cycles)
primary_kl_source = "utility_scores" if has_utility_scores else "invocation_counts"
```

When `cycle_utility_scores` is empty but `invocation_events` is populated (the most likely partial-flush failure mode — utility scores are computed via `compute_all_utilities(registry, metrics_store)` which depends on registry + metrics_store being in sync; invocations are copied directly from metrics_store), the analyzer silently downgrades:

1. Prints a one-line note: `"(cycle_utility_scores empty — DB predates Step 18.7 follow-up wiring)"`.
2. Uses the invocation-count KL series for convergence detection.
3. Reports "✅ Cycles-to-convergence: N" as if this were the primary metric.
4. Returns exit 0 from `main()` via the convergence branch.

ext12 #118's data-presence gate at `analyze_longitudinal.py:725-738` fires ONLY when ALL THREE sources (`cumulative_invocations`, `utility_scores`, `events`) are empty. The case where invocations are present but utility scores are missing passes the gate cleanly — exactly the partial-flush failure mode that #125 enables.

Reviewer-confirmed reproduction: "40-cycle DB with evaluation_runs + invocation_events, no cycle_utility_scores → analyzer reported convergence and exited 0." Chapter 6 Pillar 3 RQ #2 numbers can thus derive from the proxy KL while presenting as the proposal-literal utility-score KL — a thesis integrity hazard.

### Where

- **Downgrade assignment:** `scripts/analyze_longitudinal.py` — `primary_kl_source = "utility_scores" if has_utility_scores else "invocation_counts"` (the `_detect_kl_source` logic, in the `main()` body after `_load_cycle_data`).
- **Silent "note, don't fail" path:** `scripts/analyze_longitudinal.py:746-749`:
  ```python
  if not has_utility_scores:
      print("  (cycle_utility_scores empty — DB predates Step 18.7 follow-up wiring)")
  if not has_events:
      print("  (cycle_events empty — per-type op counts unavailable)")
  ```
- **Convergence detection uses the downgraded series:** `scripts/analyze_longitudinal.py:730`:
  ```python
  convergence_kls = kls_utility if primary_kl_source == "utility_scores" else kls_invocations
  ```
- **Exit-code only gates on convergence:** `scripts/analyze_longitudinal.py:993`:
  ```python
  return 0 if convergence_cycle is not None else 2
  ```
  No branch distinguishes "converged on proposal-literal KL" from "converged on proxy KL."

### Reproduction

```bash
# Seed: 40-cycle longitudinal DB with evaluation_runs + invocation_events populated,
# but cycle_utility_scores empty (simulates cycle_flush.py utility-flush failure — #125).

$ uv run scripts/analyze_longitudinal.py --db /tmp/partial_flush.duckdb
  Total cycles:    40
  KL threshold:    0.05
  Min consecutive: 3
  Primary KL:      invocation_counts (invocation-count proxy)
  (cycle_utility_scores empty — DB predates Step 18.7 follow-up wiring)
  ...
  ✅ Cycles-to-convergence: 12

$ echo $?
0
```

Reviewer's observation matches verbatim.

### Threat model / impact

- **Chapter 6 Pillar 3 RQ #2 silently derived from proxy data.** The thesis's "registry converges in K cycles" claim depends on utility-score KL being the computed signal. A Chapter 6 table that says "Cycles-to-convergence: 12" is indistinguishable in the output whether that 12 came from utility-score KL or invocation-count KL — except for a single parenthetical note that easy to skim past.
- **Direction of bias: unpredictable, but likely pro-convergence.** Invocation-count distributions are typically less sensitive to mid-cycle utility fluctuations than utility-score distributions; the proxy KL often smooths toward sub-threshold earlier than the proposal-literal metric would. The reported convergence cycle is likely optimistic.
- **Compounds with #125.** #125 enables silent utility-flush failure; #124 then silently consumes the partial data as if it were complete. Together, a flushing-subsystem hiccup at cycle 3 can produce a Chapter 6 table claiming proposal-literal convergence at cycle 5.
- **Compounds with ext11 #113 (metrics_store persistence) and ext8 #71 (fresh MetricsStore).** If `compute_all_utilities` raises at registry/store sync boundaries (e.g., a prune that fires during flush leaves `registry.count` < `metrics_store`'s tracked caps), the utility-flush raises → #125 swallows → #124 falls back silently.
- **Proposal-alignment violation.** The proposal §Metrics literally specifies utility-score KL as the convergence signal. Running on the proxy and reporting "converged" is a methodology violation Chapter 4.5 cannot paper over with a disclosure line — the reader needs to know WHICH signal computed the cited number.

### Remediation

**A. Loud-fail when utility-source downgrade happens on a longitudinal run.**

```python
# scripts/analyze_longitudinal.py — after primary_kl_source assignment:
if primary_kl_source == "invocation_counts":
    if not args.allow_proxy_kl:
        print(
            "ERROR: cycle_utility_scores is empty. The proposal-literal "
            "convergence metric (utility-score KL) cannot be computed. "
            "The invocation-count KL is a SENSITIVITY proxy, not the "
            "primary metric. Refusing to proceed.\n"
            "  Likely causes:\n"
            "    (a) cycle_flush.py utility-flush failure (see ext13 #125)\n"
            "    (b) DB predates Step 18.7 follow-up wiring (pre-2026-04-17)\n"
            "    (c) metrics_store / registry sync broken at flush time\n"
            "  Pass --allow-proxy-kl to proceed with the proxy + a\n"
            "  [PROXY] banner in every output section (Chapter 4.5\n"
            "  disclosure required).",
            file=sys.stderr,
        )
        conn.close()
        return 6  # new exit code: proposal-literal metric unavailable
    print(
        "WARNING: cycle_utility_scores empty. Proceeding with "
        "invocation-count KL proxy per --allow-proxy-kl. Every "
        "section header below will be prefixed [PROXY].",
        file=sys.stderr,
    )
    _PROXY_BANNER = "[PROXY KL]"
else:
    _PROXY_BANNER = ""
```

**B. Banner every downstream section with `[PROXY KL]` when `--allow-proxy-kl` is passed.**

Extend `_print_header` and every per-section header to include the banner:

```python
def _print_header(title: str) -> None:
    prefix = f"{_PROXY_BANNER} " if _PROXY_BANNER else ""
    print(f"\n{'='*80}")
    print(f"  {prefix}{title}")
    print(f"{'='*80}\n")
```

Chapter 6 reviewer cannot miss the downgrade; every section title carries the tag.

**C. New CLI flag + exit-code semantics.**

```python
parser.add_argument(
    "--allow-proxy-kl", action="store_true",
    help="Proceed with invocation-count KL proxy when cycle_utility_scores "
         "is empty. Every section output will be prefixed [PROXY KL]. "
         "Chapter 4.5 disclosure REQUIRED. Default: refuse with exit 6. "
         "ext13 #124.",
)
```

Exit code table:
- 0 — converged on proposal-literal utility-score KL
- 2 — did not converge within N cycles (existing)
- 3 — incomplete campaign, --allow-incomplete required (ext12 #116)
- 4 — zero-evidence DB (ext12 #118)
- 5 — shift campaign missing marker (ext12 #123)
- 6 — proposal-literal KL unavailable, --allow-proxy-kl required (this)

**D. Regression test.**

```python
def test_analyzer_refuses_downgrade_to_proxy_kl(tmp_path):
    db = _seed_populated_invocations_empty_utility_scores(tmp_path, n_cycles=40)
    rc, stderr = run_analyze_longitudinal(db)
    assert rc == 6, "expected exit 6 on utility-score absence"
    assert "proposal-literal" in stderr
    assert "--allow-proxy-kl" in stderr

def test_allow_proxy_kl_emits_banner_on_every_section(tmp_path):
    db = _seed_populated_invocations_empty_utility_scores(tmp_path, n_cycles=40)
    rc, stdout = run_analyze_longitudinal(db, allow_proxy_kl=True)
    # Every ======== section header in stdout should carry [PROXY KL]
    for section in _extract_section_headers(stdout):
        assert "[PROXY KL]" in section, f"section {section!r} missing proxy banner"
```

### Recommendation

**P0 — blocks Chapter 6 Pillar 3 RQ #2 trustworthiness.** ~40 LOC (loud-fail gate + banner wiring + CLI flag + exit-code) + 2 regression tests. ~45 min. Must ship with #125 because #125 is the primary failure mode that exposes #124.

### Legacy data

Any prior Row 18/19 output on a proxy-KL-downgraded DB is unreliable for proposal-literal RQ #2 claims. Options:
- (a) Re-run rows 9/10 to regenerate the DB with utility scores intact (requires #125 fix landed first, plus ext11 #113 cross-invocation isolation).
- (b) Disclose the downgrade in Chapter 4.5 and present results as "proxy-KL convergence" rather than "utility-score KL convergence."

(a) is cleaner; (b) is the escape hatch.

---

## Issue #125 — `cycle_flush.py` persistence writes are fail-open across all three batches; rows 9/10 can finish with silently-missing telemetry (P0 CRITICAL) — ✅ FIXED 2026-04-21

**Fix landed:** new `FlushResult` dataclass in `tests/evaluation/cycle_flush.py` with per-batch `invocations_ok` / `utility_scores_ok` / `events_ok` flags + `errors` list + `all_ok` property. `flush_task_cycle_state` now returns `FlushResult` instead of `None`. Both longitudinal runners (`run_longitudinal.py` and `run_longitudinal_shift.py`) accumulate per-task results into a `flush_results` list and exit 5 when any batch failed. The harness logs a warning per failed flush but leaves exit-code decisions to the runners.

### Summary

`tests/evaluation/cycle_flush.py::flush_task_cycle_state` is the per-task flush helper called by both `tests/evaluation/harness.py::_execute_run` and the longitudinal runners. Its docstring explicitly states the **never-raises contract** (line 11-13):

> "Never-raises contract: any flush failure emits a warning log and proceeds — flush failure must not break the surrounding eval/longitudinal loop."

This contract is implemented via three independent `try/except Exception` blocks at lines 73-87, 89-102, 104-116:

```python
# --- Invocation events (Step 18.5) ---
try:
    task_invocations = metrics_store.fetch_invocations_for_task(
        task_id, since=task_events_cutoff,
    )
    eval_store.record_invocation_events(
        run_id=run_id, condition=condition, task_id=task_id,
        events=task_invocations, is_pilot=is_pilot,
    )
except Exception as exc:  # noqa: BLE001
    logger.warning("Failed to flush invocation_events for task %s: %s", task_id, exc)

# --- Cycle utility scores (Step 18.7) ---
try:
    from src.optimizer.utility import compute_all_utilities
    utilities = compute_all_utilities(registry, metrics_store)
    eval_store.record_cycle_utility_scores(
        run_id=run_id, condition=condition, task_id=task_id,
        scores=utilities,
    )
except Exception as exc:  # noqa: BLE001
    logger.warning("Failed to flush cycle_utility_scores for task %s: %s", task_id, exc)

# --- Cycle events (Step 18.7) ---
try:
    task_events = metrics_store.fetch_events_since(task_events_cutoff)
    eval_store.record_cycle_events(
        run_id=run_id, condition=condition, task_id=task_id,
        events=task_events,
    )
except Exception as exc:  # noqa: BLE001
    logger.warning("Failed to flush cycle_events for task %s: %s", task_id, exc)
```

Callers at `scripts/run_longitudinal.py` (around line 353's `budget_watchdog` block — flush is invoked further down at the post-pipeline boundary) and `scripts/run_longitudinal_shift.py:442`-boundary both invoke the helper without capturing return value (the function returns None). The helper has no mechanism to signal partial or total flush failure upstream:

- No return value indicating success count.
- No exception re-raised.
- No per-task counter accumulated on `eval_store` or `metrics_store`.
- No end-of-campaign verification pass.

Reviewer-confirmed reproduction: "forced all three write methods to raise; flush returned normally (no failure signal)." A Docker-hosted DuckDB lock, a transient `record_cycle_utility_scores` schema mismatch, a `compute_all_utilities` import failure (ImportError on a renamed symbol), a disk-full condition, or any other transient in any of the three stages silently drops that flush's telemetry.

Net effect: Rows 9/10 complete with all `evaluation_runs` rows present but with systematically-missing `invocation_events` / `cycle_utility_scores` / `cycle_events` entries. #124 then silently downgrades; #116 / #118 (ext12) see partial data and may fire their completeness gates if the gap is large — but a per-task partial-flush (e.g., only utility scores fail for half the tasks) produces a DB that looks plausible row-count-wise and passes all existing gates.

### Where

- **Three broad-except wrappers:** `tests/evaluation/cycle_flush.py:73-87, 89-102, 104-116`.
- **Docstring cementing the never-raises contract:** `tests/evaluation/cycle_flush.py:11-13`.
- **Caller 1 (longitudinal runner):** `scripts/run_longitudinal.py` per-task post-pipeline boundary — helper invoked with no success-capture; the runner continues unconditionally.
- **Caller 2 (shift runner):** `scripts/run_longitudinal_shift.py` — same shape.
- **Caller 3 (harness):** `tests/evaluation/harness.py::_execute_run` — same shape.

### Reproduction

```python
# Patch all three eval_store methods to raise:
monkeypatch.setattr(
    "src.registry.evaluation_store.EvaluationStore.record_invocation_events",
    Mock(side_effect=RuntimeError("simulated transient")),
)
monkeypatch.setattr(
    "src.registry.evaluation_store.EvaluationStore.record_cycle_utility_scores",
    Mock(side_effect=RuntimeError("simulated transient")),
)
monkeypatch.setattr(
    "src.registry.evaluation_store.EvaluationStore.record_cycle_events",
    Mock(side_effect=RuntimeError("simulated transient")),
)

flush_task_cycle_state(
    eval_store=eval_store, metrics_store=metrics_store, registry=registry,
    run_id="test_run", condition="longitudinal", task_id="T01",
    task_events_cutoff=datetime.now(UTC),
)
# Returns None. Three warning log lines emitted. No exception raised.
# Caller has no way to know nothing was flushed.
```

Confirmed by direct invocation replay.

### Threat model / impact

- **Rows 9/10 silently emit partial telemetry.** A campaign that logs-as-successful (ext12 #119 watchdog didn't fire, all tasks completed `pipeline.invoke`) can finish with any subset of flushes missing. No surface signal in the final summary.
- **Directly enables #124.** Utility-flush failure → `cycle_utility_scores` empty → #124 silently downgrades to proxy KL.
- **Indirectly enables #118** (ext12 — zero-evidence KL = 0 reads as convergence). If ALL THREE tables miss flushes for most tasks, the per-cycle signal collapses to empty-vs-empty, #118 catches it via the data-presence gate. But a partial per-table failure (only `cycle_utility_scores` or only `cycle_events`) passes ext12 #118 cleanly and reaches #124 / #117 territory.
- **Direction of bias: pro-thesis.** Partial flush reduces the signal the analyzer sees; with less signal, KL values are smaller, convergence appears faster. Missing `cycle_events` hides optimizer activity (prune/split/merge); Chapter 6 under-reports Pillar 3 operation counts.
- **Methodology ambiguity.** The never-raises contract's docstring justifies the pattern as "flush failure must not break the surrounding loop." That's a defensible design for the RUNNER (we don't want to lose 5 hours of eval work to a transient DuckDB hiccup), but it leaves the DETECTION of partial flush at the runner level with no mechanism. The fix is not to make flushes fatal — it's to surface the partial state so the runner can exit non-zero, re-flush, or disclose.

### Remediation

**A. Return a success/failure tally from the helper; let the runner decide.**

```python
# tests/evaluation/cycle_flush.py — change signature:
from dataclasses import dataclass, field

@dataclass
class FlushResult:
    invocations_ok: bool = False
    utility_scores_ok: bool = False
    events_ok: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return self.invocations_ok and self.utility_scores_ok and self.events_ok


def flush_task_cycle_state(...) -> FlushResult:
    result = FlushResult()
    try:
        ...record_invocation_events(...)
        result.invocations_ok = True
    except Exception as exc:
        logger.warning("Failed to flush invocation_events for task %s: %s", task_id, exc)
        result.errors.append(f"invocation_events: {exc}")

    try:
        ...record_cycle_utility_scores(...)
        result.utility_scores_ok = True
    except Exception as exc:
        logger.warning("Failed to flush cycle_utility_scores for task %s: %s", task_id, exc)
        result.errors.append(f"cycle_utility_scores: {exc}")

    try:
        ...record_cycle_events(...)
        result.events_ok = True
    except Exception as exc:
        logger.warning("Failed to flush cycle_events for task %s: %s", task_id, exc)
        result.errors.append(f"cycle_events: {exc}")

    # Keep the LangFuse cross-ref emit (lines 118-136) unchanged — truly best-effort.
    ...
    return result
```

**B. Runners accumulate the per-task results and exit non-zero on campaign-level threshold.**

```python
# scripts/run_longitudinal.py — inside the per-task loop, replace:
flush_task_cycle_state(eval_store=..., ...)

# with:
flush_results: list[FlushResult] = []
for task in task_sequence:
    ...
    fr = flush_task_cycle_state(eval_store=..., ...)
    flush_results.append(fr)

# After the loop:
n_bad_invocations = sum(1 for r in flush_results if not r.invocations_ok)
n_bad_utility = sum(1 for r in flush_results if not r.utility_scores_ok)
n_bad_events = sum(1 for r in flush_results if not r.events_ok)

if any((n_bad_invocations, n_bad_utility, n_bad_events)):
    logger.error(
        "Per-task flush failures: invocations=%d/%d utility=%d/%d events=%d/%d. "
        "Row 18/19 analysis will see partial telemetry; #124 may silently "
        "downgrade to proxy KL. Re-run or investigate transient causes.",
        n_bad_invocations, len(flush_results),
        n_bad_utility, len(flush_results),
        n_bad_events, len(flush_results),
    )
    return 5  # partial-flush exit code
```

Exit code 5 lets orchestrators / run-sheet lint detect "campaign completed but telemetry is incomplete" vs "campaign failed outright" vs "campaign succeeded cleanly."

**C. End-of-campaign retry pass for transient failures.**

```python
# After the main loop, before the summary:
retryable = [fr for fr in flush_results if not fr.all_ok]
if retryable:
    logger.info("Retrying %d failed flushes (once)...", len(retryable))
    for fr in retryable:
        # Re-attempt the failed batches; log remaining failures.
        ...
```

One retry is a reasonable trade between "absorb transients" and "not burn more wall-clock on systematic failures."

**D. Regression test.**

```python
def test_flush_helper_reports_per_batch_success(monkeypatch):
    eval_store = Mock()
    eval_store.record_invocation_events.side_effect = None
    eval_store.record_cycle_utility_scores.side_effect = RuntimeError("fail")
    eval_store.record_cycle_events.side_effect = None

    result = flush_task_cycle_state(
        eval_store=eval_store, metrics_store=..., registry=...,
        run_id="r", condition="longitudinal", task_id="T01",
        task_events_cutoff=datetime.now(UTC),
    )
    assert result.invocations_ok is True
    assert result.utility_scores_ok is False
    assert result.events_ok is True
    assert len(result.errors) == 1
    assert "cycle_utility_scores" in result.errors[0]

def test_longitudinal_runner_exits_nonzero_on_partial_flush(monkeypatch):
    # Patch utility flush to always fail; assert runner exits 5.
    ...
```

### Recommendation

**P0 — ship with #124; they must land together.** ~80 LOC across `cycle_flush.py` (signature change) + both runners + harness + 2 regression tests. ~1.5h. The signature change is backward-incompatible but the helper has only 3 callers.

### Legacy data

Any prior longitudinal DB produced pre-fix may have silently-missing telemetry. Detection is post-hoc: compare per-task row counts in `evaluation_runs` vs `invocation_events` / `cycle_utility_scores` / `cycle_events`; mismatches indicate partial-flush events. Decision per legacy DB:
- (a) Re-run affected campaigns.
- (b) Disclose in Chapter 4.5 with the mismatch statistics.

---

## Issue #126 — `analyze_results.py::print_gini_summary` treats empty `invocation_events` as "skipped" and returns; main flow continues + exits 0 with M3 silently missing from Chapter 6 (P0 CRITICAL) — ✅ FIXED 2026-04-21

**Fix landed:** `print_gini_summary` + `print_contamination_rate` now return `bool` (True = computed, False = skipped/partial). `main()` now returns `int` instead of calling `sys.exit()` inline; accumulates a `missing_metrics` list from each analyzer's return value; exits 7 when any thesis-critical metric is absent, unless `--allow-missing-metrics` is passed. Entrypoint wrapped with `sys.exit(main() or 0)`.

### Summary

`scripts/analyze_results.py::print_gini_summary` handles the "no invocation data" case at line 887-889 by printing a short skip message and returning normally:

```python
if not per_run_rows:
    print(f"\n{'='*80}")
    print("  Gini Coefficient of Capability Invocation Counts")
    print(f"{'='*80}\n")
    print("  (skipped: invocation_events table is empty)")
    return
```

The outer `main()` at line 1500 calls `print_gini_summary(conn)` in a sequential list with nine other analysis functions. No return-value check, no sticky flag for "M3 absent," no exit-code escalation.

Line 1519-1520:

```python
if __name__ == "__main__":
    main()
```

Bare `main()` call. `main()` has no explicit `sys.exit(code)` / `return` — Python defaults to exit 0 on natural completion.

Net effect: Row 17 on a canonical 400-record DB with empty `invocation_events` (the ext7 #50 `is_pilot` column fix plus ext11 #109's prune-exclusion plus #125's partial-flush could all independently produce this) prints every other Chapter-6 section normally AND prints "(skipped: invocation_events table is empty)" for M3, AND exits 0.

Reviewer-confirmed reproduction: "canonical 400-row eval DB with empty invocation_events → script exit 0, output included `(skipped: invocation_events table is empty)`." Chapter 6's Pillar 3 RQ #1 headline (Gini coefficient) is silently absent while the rest of the document looks complete.

### Where

- **Skip-and-return on empty:** `scripts/analyze_results.py:887-892`:
  ```python
  if not per_run_rows:
      print(f"\n{'='*80}")
      print("  Gini Coefficient of Capability Invocation Counts")
      print(f"{'='*80}\n")
      print("  (skipped: invocation_events table is empty)")
      return
  ```
- **`main()` call site (no status capture):** `scripts/analyze_results.py:1500`:
  ```python
  print_gini_summary(conn)
  ```
- **Bare entrypoint:** `scripts/analyze_results.py:1519-1520`:
  ```python
  if __name__ == "__main__":
      main()
  ```

### Reproduction

```bash
# Seed: canonical 400-row eval DB (4 conditions × 5 runs × 20 tasks), but
# invocation_events table is empty (simulates partial-flush failure from #125
# OR pre-ext7 #50 legacy DB).

$ uv run scripts/analyze_results.py --db /tmp/no_m3.duckdb --run-ids ...
# ... all normal sections print ...
================================================================================
  Gini Coefficient of Capability Invocation Counts
================================================================================

  (skipped: invocation_events table is empty)

# ... remaining sections print ...

$ echo $?
0
```

### Threat model / impact

- **Chapter 6 Pillar 3 RQ #1 silently absent while pipeline reports success.** The thesis's capability-distribution concentration claim has no evidence in Row 17's output, but the exit 0 signals "analysis complete" to any orchestrator or human reviewer skimming the stdout.
- **Compounds with #125 directly.** #125 enables empty `invocation_events` via partial-flush failure; #126 then silently suppresses the M3 analysis without surfacing the absence at exit-code level.
- **Compounds with ext7 #50 + ext11 #109.** ext7 #50's `is_pilot` backfill + ext11 #109's prune-exclusion both filter `invocation_events` rows; a DB where ALL non-pilot rows got pruned (pathological but possible) produces an empty post-filter rowset. #126 silently reports "skipped" for that case too.
- **Direction of bias: pro-thesis completeness appearance.** Every other section prints; exit 0. Reader assumes all metrics landed. No [MISSING: M3] banner in the final summary.
- **Inconsistent with sibling metrics.** `print_contamination_rate` (M2) at the analogous line also handles "audit not performed" gracefully but prints a more visible message; `print_token_cost_summary` (M4) does not skip — it reports zeros with per-condition breakdown. M3's skip-and-return is the most silent of the three.
- **Violates ext12 findings #115-#118, #121-#123 pattern discipline.** Those remediations established the principle "distinct exit code per failure class." M3-missing is a thesis-critical failure class with no exit-code coverage.

### Remediation

**A. Track a `missing_metrics` list in `main()`; exit non-zero on any thesis-critical absence.**

```python
# scripts/analyze_results.py — before the sequential analysis calls:
missing_metrics: list[str] = []

# Replace each print_X call with:
if not print_gini_summary(conn):
    missing_metrics.append("M3 (Gini / Pillar 3 RQ #1)")
if not print_contamination_rate(conn):
    missing_metrics.append("M2 (Contamination)")
# ... for each section that can silently skip ...

# At end of main(), before CSV export:
if missing_metrics:
    print(
        f"\n{'!'*80}\n"
        f"  CHAPTER 6 INCOMPLETE — the following metrics could not be computed:\n"
        + "\n".join(f"    - {m}" for m in missing_metrics)
        + f"\n{'!'*80}\n",
        file=sys.stderr,
    )
    conn.close()
    return 7  # new exit code: thesis-critical metric absent

return 0
```

And change each section function to return `bool` (True = computed, False = skipped):

```python
def print_gini_summary(conn) -> bool:
    ...
    if not per_run_rows:
        print("(skipped: invocation_events table is empty)")
        return False
    ...
    return True
```

**B. `sys.exit(main() or 0)` wrapper on entrypoint.**

```python
# scripts/analyze_results.py:1519-1520 — before:
if __name__ == "__main__":
    main()

# after:
if __name__ == "__main__":
    sys.exit(main() or 0)
```

Mirrors ext12 #115 + #122 + #123 fix pattern.

**C. CLI flag for explicit override.**

```python
parser.add_argument(
    "--allow-missing-metrics", action="store_true",
    help="Proceed with exit 0 even when thesis-critical metrics "
         "(M2, M3, M4) cannot be computed. Chapter 4.5 disclosure "
         "REQUIRED. Default: refuse with exit 7. ext13 #126.",
)
# In the missing_metrics branch:
if missing_metrics and not args.allow_missing_metrics:
    return 7
```

**D. Visible in-band banner when proceeding under override.**

```python
if missing_metrics and args.allow_missing_metrics:
    # Prefix every Chapter-6 section title with [MISSING: M3] etc.
    _MISSING_BANNER = "[MISSING: " + ", ".join(missing_metrics) + "] "
```

Chapter 6 reviewer sees the banner on every section header; the absence is unmistakable.

**E. Regression test.**

```python
def test_analyze_results_exits_nonzero_when_m3_missing(tmp_path):
    db = _seed_full_eval_db_empty_invocation_events(tmp_path)
    rc, stderr = run_analyze_results(db)
    assert rc == 7, "expected exit 7 on missing M3"
    assert "M3 (Gini" in stderr

def test_analyze_results_allow_missing_prints_banner(tmp_path):
    db = _seed_full_eval_db_empty_invocation_events(tmp_path)
    rc, stdout = run_analyze_results(db, allow_missing_metrics=True)
    assert rc == 0
    # Every section header should carry [MISSING: M3 (Gini ...)]
    for section in _extract_section_headers(stdout):
        assert "[MISSING" in section
```

### Recommendation

**P0 — blocks Chapter 6 completeness verification.** ~40 LOC (status capture + banner + CLI flag + exit-code) + 2 regression tests. ~45 min. Ship with #124 + #125 as a single "longitudinal telemetry integrity" bundle.

### Legacy data

Prior Row 17 outputs on legacy DBs with empty `invocation_events` lack M3. Re-run post-fix on populated DBs (requires #125 landed first if the cause was partial flush) OR disclose in Chapter 4.5 with `--allow-missing-metrics` annotation.

---

## Issue #127 — `analyze_longitudinal.py` data-presence gate is a global `any()`, not per-cycle completeness; a single invocation event across 40 cycles passes the gate and yields false convergence (P0 CRITICAL) — ✅ FIXED 2026-04-21

**Fix landed:** replaced the global `any(...)` gate with a per-cycle coverage ratio (`cycles_with_signal / len(cycles)`) enforced against `--min-cycle-coverage` (default 0.5). Below threshold, exit 8 unless `--allow-sparse-signal` is passed; when the override is set every section header is prefixed `[SPARSE: N/M]` via the module-level `_SPARSE_BANNER`. The existing ext12 #118 all-zero exit 4 branch is preserved for DBs with literally no signal.

### Summary

ext12 #118 added a data-presence gate at `analyze_longitudinal.py:732-738`:

```python
has_any_signal = any(
    c.get("cumulative_invocations")
    or c.get("utility_scores")
    or c.get("events")
    for c in cycles
)
if not has_any_signal:
    print("ERROR: ... zero-evidence ... Exit 4.", file=sys.stderr)
    return 4
```

The gate is a **global `any()`** across all cycles: if ANY cycle has ANY signal in ANY of the three fields, the gate passes. It does not require per-cycle coverage. A 40-cycle DB with exactly ONE `invocation_events` row (placed in any cycle) passes the gate; the remaining 39 cycles carry no signal.

Downstream:
1. `primary_kl_source` assignment (line 754) — `has_utility_scores` is False (no utility rows) → falls back to invocation-count KL (this is #124's territory; exit 0 on convergence in the proxy path until #124 is fixed).
2. Per-cycle KL computation — cycles with empty cumulative_invocations compared against prior empty cycles yield NaN via ext12 #118's NaN sentinel. BUT cycles surrounding the single-event cycle produce real (non-NaN) KL values: cycle N (has the event) vs cycle N-1 (empty) yields `float('inf')` per the ext12 #118 branch `if prev_dist and not curr_dist: return float('inf')`; cycle N+1 (empty) vs cycle N (has event) yields 0.0 per the `if curr_dist and not prev_dist: return 0.0` branch; cycle N+2 vs N+1 is NaN.
3. `_detect_convergence` (line 816-823) sees a series that has NaN (streak-reset per ext12 #118 fix), a 0.0 at N+1, then NaN again. Depending on `min_consecutive` tuning and where the event lands, the detector can fire on the 0.0 value plus whatever sub-threshold values follow from near-empty comparisons.
4. Even with the NaN-reset behavior, the reviewer observed `Cycles-to-convergence: 2, exit 0` on the 1-event 40-cycle repro — the sub-threshold streak lands early enough in the sequence for the detector to return a cycle index.
5. Line 1114 `return 0 if convergence_cycle is not None else 2` — convergence detected → exit 0.

Reviewer-confirmed reproduction: "40 evaluation_runs, 1 invocation_events row, no utility/events → output showed `Primary KL: invocation_counts, Cycles-to-convergence: 2, exit 0`." This is distinct from ext13 #126 (empty invocation_events → Row 17 skip) and distinct from ext12 #118 (ALL tables empty → exit 4). It's a new failure surface: ext12 #118 named "zero-evidence" as an eval-blocker but the gate's threshold is "ANY evidence, ANYWHERE" rather than "enough evidence to compute the metric honestly."

### Where

- **Global `any()` gate:** `scripts/analyze_longitudinal.py:732-738`.
- **Downstream convergence detection doesn't care about per-cycle data density:** `scripts/analyze_longitudinal.py:816-823`.
- **Exit 0 on any detected convergence cycle:** `scripts/analyze_longitudinal.py:1114`.

### Reproduction

```bash
# Seed: 40 evaluation_runs rows, condition='longitudinal'. Exactly 1 row
# inserted into invocation_events. Zero rows in cycle_utility_scores + cycle_events.

$ uv run scripts/analyze_longitudinal.py --db /tmp/one_event.duckdb
  Total cycles:    40
  Primary KL:      invocation_counts (invocation-count proxy)
  (cycle_utility_scores empty — DB predates Step 18.7 follow-up wiring)
  ...
  ✅ Cycles-to-convergence: 2

$ echo $?
0
```

Reviewer's observation matches verbatim.

### Threat model / impact

- **Thesis-core convergence metric derivable from a single row of signal.** Chapter 6 Pillar 3 RQ #2's "converged in K cycles" claim can arise from arbitrarily-partial telemetry. No threshold on "we saw enough events to trust this number."
- **Direction of bias: pro-convergence.** Sparse signal yields uniformly-small KL values (most comparisons are NaN; the few non-NaN are small). The detector fires earlier than real-signal cycles would permit.
- **Compounds with #124 AND #125 AND #126.** #125 (partial flush) is the upstream source. #124 (proxy KL downgrade) fires because utility scores are missing. #126 (M3 skip) fires if invocation_events is globally empty. #127 is the surface where invocation_events is NON-empty but SO PARTIAL that any-signal passes while per-cycle data is vacuous.
- **Ext12 #118 fix is necessary but not sufficient.** #118 correctly catches the "nothing anywhere" case and returns NaN on empty-vs-empty. It doesn't protect against "trivially-small signal leaks through the gate."

### Remediation

**A. Replace global `any()` with per-cycle coverage threshold.**

```python
# scripts/analyze_longitudinal.py:732-738 — before:
has_any_signal = any(
    c.get("cumulative_invocations") or c.get("utility_scores") or c.get("events")
    for c in cycles
)
if not has_any_signal:
    ...exit 4...

# after — per-cycle coverage threshold:
_MIN_PER_CYCLE_COVERAGE = 0.5  # at least 50% of cycles must have signal
cycles_with_signal = sum(
    1 for c in cycles
    if c.get("cumulative_invocations") or c.get("utility_scores") or c.get("events")
)
coverage_ratio = cycles_with_signal / len(cycles) if cycles else 0.0

if coverage_ratio == 0.0:
    print("ERROR: zero-evidence DB ... Exit 4.", file=sys.stderr)
    return 4

if coverage_ratio < _MIN_PER_CYCLE_COVERAGE:
    if not args.allow_sparse_signal:
        print(
            f"ERROR: longitudinal DB has signal in only {cycles_with_signal}/"
            f"{len(cycles)} cycles ({coverage_ratio:.0%}). Convergence analysis "
            f"on sparse telemetry is unreliable — a single invocation event can "
            f"pass the global presence gate while 39/40 cycles are empty.\n"
            f"  Likely causes:\n"
            f"    (a) cycle_flush.py partial-flush failures (see #125)\n"
            f"    (b) metrics_store lost state mid-campaign\n"
            f"  Pass --allow-sparse-signal to proceed with a [SPARSE: N/M] banner\n"
            f"  (Chapter 4.5 disclosure REQUIRED). Exit 8.",
            file=sys.stderr,
        )
        return 8  # new exit code: sparse telemetry
    _SPARSE_BANNER = f"[SPARSE: {cycles_with_signal}/{len(cycles)}]"
```

**B. CLI flag.**

```python
parser.add_argument(
    "--allow-sparse-signal", action="store_true",
    help="Proceed with convergence analysis when per-cycle telemetry coverage "
         "is below 50%. Every section header will be prefixed with "
         "[SPARSE: N/M]. Chapter 4.5 disclosure REQUIRED. Default: refuse "
         "with exit 8. ext13 #127.",
)
parser.add_argument(
    "--min-cycle-coverage", type=float, default=0.5,
    help="Minimum fraction of cycles that must carry signal (default: 0.5). "
         "Below this, exit 8 unless --allow-sparse-signal is passed.",
)
```

**C. Banner propagates to every section header.**

Same pattern as #124's `[PROXY KL]` banner — every `_print_header` call interpolates `_SPARSE_BANNER` when set.

**D. Regression test.**

```python
def test_sparse_signal_is_refused_with_exit_8(tmp_path):
    # 40 cycles, 1 invocation_events row.
    db = _seed_sparse_longitudinal(tmp_path, n_cycles=40, n_events=1)
    rc, stderr = run_analyze_longitudinal(db)
    assert rc == 8, f"expected exit 8 on 1/40 coverage, got {rc}"
    assert "sparse telemetry" in stderr

def test_allow_sparse_signal_emits_banner(tmp_path):
    db = _seed_sparse_longitudinal(tmp_path, n_cycles=40, n_events=1)
    rc, stdout = run_analyze_longitudinal(db, allow_sparse_signal=True)
    # Every section header should carry [SPARSE: 1/40]
    for section in _extract_section_headers(stdout):
        assert "[SPARSE" in section

def test_full_coverage_passes_gate(tmp_path):
    db = _seed_full_longitudinal(tmp_path, n_cycles=40)  # every cycle has signal
    rc, _ = run_analyze_longitudinal(db)
    assert rc in (0, 2)  # 0 on convergence, 2 on no-convergence; neither is sparse
```

### Recommendation

**P0 — ship with #124 / #125 / #126 as a single longitudinal-telemetry-integrity bundle.** ~30 LOC (coverage computation + gate + banner + CLI flags) + 3 regression tests. ~45 min.

### Legacy data

Any prior Row 18/19 output on a sparse-signal DB is unreliable — re-run post-fix (requires #125 landed to ensure flush integrity) or disclose with `--allow-sparse-signal`.

---

## Issue #128 — `print_gini_summary` silently prints dashes for conditions missing from `invocation_events`; Row 17 exits 0 with per-condition M3 coverage gaps (P0 CRITICAL) — ✅ FIXED 2026-04-21

**Fix landed:** after the per-condition print loop, `print_gini_summary` enumerates `missing_conditions = [c for c in CONDITIONS if not condition_ginis.get(c)]` and returns `False` (threading into #126's `missing_metrics`) with an `⚠ M3 partial coverage` stderr message listing the missing conditions. Exit 7 reused from #126.

### Summary

ext13 #126 established that `print_gini_summary` should loud-fail when `invocation_events` is GLOBALLY empty. The sibling case — `invocation_events` has rows for SOME conditions but not others — slips the same fix. At `scripts/analyze_results.py:940-943`:

```python
for cond in CONDITIONS:
    run_ginis = condition_ginis.get(cond, [])
    if not run_ginis:
        print(f"  {CONDITION_LABELS.get(cond, cond):<22} {'—':>8} {'—':>10} {'—':>7} {'—':>7} {'—':>10}")
        continue
    ...
```

A condition with zero rows prints dashes and the loop continues. `print_gini_summary` returns normally; `main()` at line 1500 continues through the remaining sections; line 1519-1520 bare `main()` call → exit 0.

When `invocation_events` has rows ONLY for `full` (e.g., static condition invocations weren't flushed, or a subset of runs had #125-style partial-flush failures), the M3 table prints real numbers for `full` and dashes for `static` / `no_verification` / `no_optimization`. Chapter 6 reader sees a table with 3 of 4 rows dashed — which LOOKS like deliberate missing data but has no surface signal that this is unintended.

Reviewer-confirmed reproduction: "canonical 400-row evaluation_runs, invocation_events only for full → Gini table had data for one condition + dashes for others, exit 0." Chapter 6's JIT-vs-static M3 comparison is the Pillar 3 RQ #1 headline; a per-condition coverage gap silently removes the baseline (static) or the ablation conditions while the thesis claims a comparative result.

### Where

- **Per-condition dash fallback:** `scripts/analyze_results.py:940-943`.
- **`print_gini_summary` returns without flagging partial coverage:** `scripts/analyze_results.py:887` — the global `if not per_run_rows:` covers ALL-empty (ext13 #126), but there's no sibling check for "per_run_rows non-empty but incomplete per condition."
- **Main flow continues:** `scripts/analyze_results.py:1500`.
- **Bare entrypoint → exit 0:** `scripts/analyze_results.py:1519-1520`.

### Reproduction

```bash
# Seed: canonical 400-row evaluation_runs (4 conditions × 5 runs × 20 tasks),
# invocation_events populated ONLY for condition='full' runs.

$ uv run scripts/analyze_results.py --db /tmp/partial_m3.duckdb --run-ids ...
  ...
================================================================================
  Gini Coefficient of Capability Invocation Counts
================================================================================

  Condition              N runs  Mean Gini     Min     Max  Aggregate
  --------------------- ------- ---------- ------- ------- ----------
  static                     —          —       —       —          —
  no_verification            —          —       —       —          —
  no_optimization            —          —       —       —          —
  full                       5      0.520   0.490   0.550      0.521

$ echo $?
0
```

Three of four condition rows are dashes. Chapter 6 JIT-vs-static M3 comparison has no static baseline to compare against. Exit 0 says "analysis complete."

### Threat model / impact

- **Chapter 6 Pillar 3 RQ #1 partial coverage is invisible to the exit-code layer.** Any orchestrator / run-sheet-lint that decides "did row 17 succeed?" gets yes; the comparison the thesis wants to make is impossible but the pipeline says "go ahead and publish."
- **Direction of bias: depends on which conditions are present.** If only `full` has data (the most favorable JIT configuration), the reader sees M3=0.52 with no ablation counterfactual — the comparative framing is destroyed.
- **Compounds with ext8 #65 (static never recorded invocations) pre-fix.** That specific ext8 bug was the natural source of "static has no invocation_events"; it's fixed, but the same shape is reproducible from #125 partial-flush, ext11 #113 metrics_store bleed, or any other mechanism that drops one condition's flushes.
- **Inconsistent with #126's fix discipline.** #126 shipped the all-empty loud-fail; the partial-condition case slips because `per_run_rows` is non-empty when ANY condition has data. The gate's granularity is wrong.

### Remediation

**A. Per-condition completeness check within `print_gini_summary`.**

```python
# scripts/analyze_results.py — inside print_gini_summary, after building condition_ginis:
missing_conditions = [
    cond for cond in CONDITIONS
    if not condition_ginis.get(cond)
]
if missing_conditions:
    print(
        f"\n  ⚠ M3 partial coverage: no invocation_events for "
        f"{len(missing_conditions)}/{len(CONDITIONS)} conditions: "
        f"{', '.join(missing_conditions)}",
        file=sys.stderr,
    )
    # Return an explicit partial-coverage sentinel.
    return False  # completes-with-warning; main() treats as missing

return True
```

**B. `main()` tracks partial-coverage conditions separately from missing-metrics.**

Extend the `missing_metrics` pattern from #126:

```python
# main():
ok = print_gini_summary(conn)
if not ok:
    # print_gini_summary returned False → some conditions were dashed
    missing_metrics.append(
        f"M3 partial (missing conditions; see stderr above)"
    )
```

Exit code 7 (ext13 #126) now covers BOTH "M3 entirely absent" and "M3 per-condition gaps" without needing a new code — both are "thesis-critical metric missing in whole or in part."

**C. Override flag (same `--allow-missing-metrics` as #126).**

Existing #126 flag handles both cases. Banner text includes the partial-condition list.

**D. Regression test.**

```python
def test_gini_partial_condition_coverage_flags_missing(tmp_path):
    db = _seed_400_row_db_with_invocations_only_for_full(tmp_path)
    rc, stderr = run_analyze_results(db)
    assert rc == 7, "expected exit 7 on M3 per-condition gaps"
    assert "M3 partial coverage" in stderr
    assert "static" in stderr
    assert "no_verification" in stderr

def test_gini_all_conditions_present_passes(tmp_path):
    db = _seed_400_row_db_with_full_invocation_coverage(tmp_path)
    rc, _ = run_analyze_results(db)
    assert rc == 0
```

### Recommendation

**P0 — ship with #126 as a single M3-integrity bundle.** ~20 LOC (per-condition check + bool return + main() threading) + 2 regression tests. ~30 min. #126's fix discipline already established the pattern; #128 just extends the granularity from "all-or-nothing" to "per-condition."

### Legacy data

Any prior Row 17 output with dashed M3 conditions silently suppressed the thesis-core JIT-vs-static comparison on that metric. Re-run post-fix once upstream #125 / ext11 #113 ensure invocation_events coverage is complete.

---

## Issue #129 — `backfill_token_costs.py` tagged-run path silently writes `token_cost=0` for tasks missing from LangFuse tag coverage; ext12 #122's guard is run-level only (P0 CRITICAL) — ✅ FIXED 2026-04-21

**Fix landed:** `compute_per_task_costs` now enumerates `expected_keys_per_tagged_run` against `present_keys = set(run_task_tokens.keys())` and stashes `per_run_missing_tasks` on the function object. `main()` reads the diagnostic after the call and refuses the write with exit 9 unless `--allow-partial-task-coverage` is set; stderr lists the first 5 affected runs + missing tasks.

### Summary

ext12 #122 added a run-level completeness check: if a run has zero matched LangFuse generations globally, refuse with exit 3 unless `--allow-partial` is passed. The fix covers "an entire run's traces are missing" but does NOT cover "a run has SOME traces tagged, but a subset of its tasks lack `task_id` tags."

At `scripts/backfill_token_costs.py:651-656`:

```python
rid = rec["run_id"]
if rid in tagged_run_ids:
    key = (rid, rec["task_id"])
    total_for_task = run_task_tokens.get(key, 0)   # ← silent 0 on missing key
    n_cycles = cycle_counts.get(key, 1) or 1
    tokens = total_for_task // n_cycles
```

When `(rid, rec["task_id"])` is not in `run_task_tokens` (the task's LangFuse traces never emitted a `task_id` tag, or the tagging thread lost a write), `.get(key, 0)` returns 0 silently. The record receives `token_cost=0` and is written unconditionally.

The zero-summary-runs gate at line 780-783:

```python
zero_summary_runs = [
    rid for rid, s in run_summaries.items()
    if s.get("n_generations", 0) == 0 and s.get("condition") != "static"
]
```

Only flags runs where `n_generations == 0` globally. A tagged run with 5 tasks where 3 are tagged and 2 are missing has `n_generations > 0` (summed across the 3 tagged tasks) → passes the gate; the 2 un-tagged tasks silently get `token_cost=0` written in the UPDATE loop.

Reviewer-confirmed reproduction: "one run with 2 tasks, only one task tagged → updates were `('T04', 400)` and `('T05', 0)`, script completed successfully." Chapter 6 M4's per-task cost is under-reported for the un-tagged tasks while their sibling tasks in the same run report real costs — a within-run inconsistency that survives #122's gate.

### Where

- **Silent `.get(key, 0)` on missing per-task trace:** `scripts/backfill_token_costs.py:652-656`.
- **ext12 #122 gate is run-level only:** `scripts/backfill_token_costs.py:780-783`.
- **UPDATE loop writes zeros unconditionally after the gate:** `scripts/backfill_token_costs.py` (the write loop that iterates `updates`).

### Reproduction

```bash
# Seed: one run with 2 tasks (T04, T05). LangFuse has generations tagged with
# run_id + task_id for T04 only. T05's traces either never tagged or have
# run_id tag but missing task_id.

$ uv run scripts/backfill_token_costs.py --db /tmp/partial_tag.duckdb --write
# Per-condition coverage: 1/1 runs have non-zero LangFuse generations ✓
# (ext12 #122 passes — the run has generations)

# Backfill updates:
#   ('T04', 400)
#   ('T05', 0)         ← silent zero
# Written: 2 records.
# Done.
$ echo $?
0
```

User's observation matches.

### Threat model / impact

- **Chapter 6 M4 per-task cost has silent within-run zeros** for tasks whose traces lost task_id tags. Direction of bias: anti-JIT cost (under-report) for the affected tasks specifically; aggregate M4 means pull toward zero proportional to tag-coverage gap.
- **Fix-induced regression against ext12 #122.** #122 solved the all-run-missing case; the partial-task-within-run case was the natural sibling and wasn't covered. Ninth fix-induced regression in the series.
- **Compounds with ext10 #104 (LangFuse global trace context shared across threads).** #104 is a genuine cross-thread attribution bug; in its pathological case, task_id tags can cross-attribute. A partial fix to #104 might leave some tasks' tags intact and others' wiped — exactly #129's trigger condition.
- **Detectability from backfill output is poor.** The per-condition coverage printout shows "1/1 runs have generations" — green. No per-task coverage breakdown.

### Remediation

**A. Per-task tag coverage gate for tagged runs.**

```python
# scripts/backfill_token_costs.py — before the write loop:
# For each tagged run, enumerate (run_id, task_id) pairs expected vs present.
expected_keys_per_run: dict[str, set[tuple[str, str]]] = {}
for rec in records:
    rid = rec["run_id"]
    if rid in tagged_run_ids:
        expected_keys_per_run.setdefault(rid, set()).add((rid, rec["task_id"]))

present_keys = set(run_task_tokens.keys())

per_run_missing_tasks: dict[str, list[str]] = {}
for rid, expected in expected_keys_per_run.items():
    missing = [tid for (r, tid) in expected - present_keys]
    if missing:
        per_run_missing_tasks[rid] = sorted(missing)

if per_run_missing_tasks:
    print(
        f"\nERROR: {len(per_run_missing_tasks)} tagged run(s) have missing "
        f"per-task LangFuse traces. Affected tasks would be silently written "
        f"as token_cost=0:",
        file=sys.stderr,
    )
    for rid, tasks in sorted(per_run_missing_tasks.items()):
        print(f"  {rid}: missing {tasks}", file=sys.stderr)
    print(
        f"  Likely causes:\n"
        f"    (a) ThreadPool trace-context bleed (see ext10 #104)\n"
        f"    (b) task_id tag dropped at emit time\n"
        f"    (c) LangFuse retention aged out per-task traces\n"
        f"  Pass --allow-partial-task-coverage to proceed with zeros for\n"
        f"  affected tasks (Chapter 4.5 disclosure REQUIRED).",
        file=sys.stderr,
    )
    if not args.allow_partial_task_coverage:
        conn.close()
        return 9  # new exit code: per-task tag coverage gap
```

**B. CLI flag.**

```python
parser.add_argument(
    "--allow-partial-task-coverage", action="store_true",
    help="Proceed with token_cost=0 for tasks within tagged runs that lack "
         "LangFuse per-task traces. Default: refuse with exit 9. "
         "Chapter 4.5 disclosure REQUIRED. ext13 #129.",
)
```

**C. Per-run per-task coverage breakdown in the pre-write summary.**

Extend the ext12 #122 per-condition breakdown with a per-run per-task line when coverage is partial:

```python
print("\n  Per-run per-task tag coverage (tagged runs only):")
for rid in sorted(tagged_run_ids):
    expected = expected_keys_per_run.get(rid, set())
    present = expected & present_keys
    marker = "⚠" if len(present) < len(expected) else " "
    print(f"  {marker} {rid}: {len(present)}/{len(expected)} tasks tagged")
```

**D. Regression test.**

```python
def test_backfill_refuses_partial_task_coverage_in_tagged_run(monkeypatch, tmp_path):
    db = _seed_one_tagged_run_with_missing_task(tmp_path, tasks=["T04", "T05"], tagged=["T04"])
    rc = backfill_token_costs.main(["--db", str(db), "--write"])
    assert rc == 9, "expected exit 9 on partial per-task tag coverage"

def test_allow_partial_task_coverage_writes_zeros(monkeypatch, tmp_path):
    db = _seed_one_tagged_run_with_missing_task(tmp_path, tasks=["T04", "T05"], tagged=["T04"])
    rc = backfill_token_costs.main([
        "--db", str(db), "--write", "--allow-partial-task-coverage",
    ])
    assert rc == 0
    # Verify T05 got token_cost=0 and T04 got non-zero.
    ...
```

### Recommendation

**P0 — ship with ext12 #122 as an amendment / fix-induced remediation.** ~50 LOC (per-task coverage computation + gate + CLI flag + per-run breakdown in summary) + 2 regression tests. ~1h. Must ship before any Row 11 → Row 17 M4 is trusted for Chapter 6.

### Legacy data

Any prior Row 11 output on a mixed-tag-coverage DB has silent within-run zero-task leaks. Re-run post-fix after confirming LangFuse task_id tag coverage via a diagnostic query. #129's per-run per-task breakdown in the pre-write summary surfaces the coverage gap at run-time going forward.

---

## Issue #130 — `backfill_token_costs.py` per-run "coverage" check only tests `n_generations > 0`; a single matched generation for a 20-task run passes the gate and writes unrealistically-low per-task costs across the whole run (P0 CRITICAL) — ✅ FIXED 2026-04-21

**Fix landed:** new quantitative per-run threshold — `main()` computes per-run `jit_records_per_run` and compares `n_generations / jit_records` against `--min-gens-per-jit-record` (default 0.5). Under-covered non-static runs accumulate into `under_covered_runs`; main() prints a per-condition `generations-per-JIT-record` breakdown with `⚠` markers on below-threshold conditions and refuses the write with exit 10 unless `--allow-under-coverage` is set.

### Summary

ext12 #122 added a `zero_summary_runs` guard that refuses writes when a run has zero matched LangFuse generations (`n_generations == 0`). #129 (this extension) extended the guard to per-task tag coverage WITHIN tagged runs. Neither addresses the case where a run has `n_generations > 0` but far below the expected per-task count — i.e., severe under-coverage where some generations survived (so the gate passes) but most are missing.

The relevant code paths:

- **Zero-count gate:** `scripts/backfill_token_costs.py:780-783` — `zero_summary_runs = [rid for rid, s in run_summaries.items() if s.get("n_generations", 0) == 0 and s.get("condition") != "static"]`. Any positive `n_generations` passes.
- **Per-condition display:** `scripts/backfill_token_costs.py:785-796` — reports `has/total` where `has = 1 if n_generations > 0 else 0`. A run with 1 generation counts as "1 covered" out of "1 run"; display shows "1/1" green.
- **Per-run cost division:** `scripts/backfill_token_costs.py:665-672` — for the untagged-run fallback, `tokens = run_total // jit_records_in_run`. When `run_total` is tiny (1 generation's worth of tokens) and `jit_records_in_run` is ~17, per-task tokens collapse to a small integer; that value is written as every JIT record's `token_cost`.
- **Tagged-run path** also affected (separately from #129): for a tagged run, only 1 of 20 `(run_id, task_id)` keys has data; that task gets a real value, 19 tasks get 0 via #129's silent-miss. The REMAINING issue unique to #130 is that the run-level gate looks green even if every task got 0.

Reviewer-confirmed reproduction: "20-task full run, 1 matched generation → coverage shown 1/1, backfill writes low costs, exits 0." Chapter 6 M4 per-task cost for this run is ~1/20 of reality — silently.

### Where

- **Binary-only zero check:** `scripts/backfill_token_costs.py:780-783`.
- **Binary-only per-condition display:** `scripts/backfill_token_costs.py:785-796`.
- **No generations-vs-expected-tasks ratio at gate or display:** same region.
- **Compute-and-write path proceeds unconditionally after gate:** `scripts/backfill_token_costs.py:665-672` and the UPDATE loop downstream.

### Reproduction

```bash
# Seed: one 'full'-condition run with 20 evaluation_runs rows (17 JIT + 3
# retrieval tasks). LangFuse has exactly ONE generation matched to this run.

$ uv run scripts/backfill_token_costs.py --db /tmp/under_cov.duckdb --write
Mapped to 1 runs, 0 unmatched generations.

  Per-condition coverage (runs with >=1 matched LangFuse generation):
    full               1/1                                    ← green "1/1"

  Backfill: 17/20 records would get non-zero token costs
  (T01/T02/T03 retrieval correctly 0)
Writing to DuckDB...
  Updated 20 records.
Done.

$ echo $?
0

$ # Inspect: the single generation had ~800 tokens.
$ # Each of 17 JIT records got token_cost ≈ 800 // 17 = 47.
$ # Real per-task cost (if all 17 had traces) would be ~800 tokens EACH.
$ # Row 17 M4 under-reports by ~17x for this run.
```

### Threat model / impact

- **Chapter 6 M4 silently under-reports by the tag-coverage gap ratio.** A run where 19/20 generations were lost still passes the gate; the run's per-task cost is (tokens-from-1-generation / 17). Direction of bias: anti-JIT cost, favorable to the thesis — worst direction.
- **Per-condition "coverage" display is misleading.** The printout "1/1" counts runs not records. A reviewer reading the log sees green and moves on. No "generations-vs-expected" ratio anywhere.
- **Compounds with #129.** #129 addresses the within-tagged-run case (some tasks missing tags); #130 addresses the quantity case (all tasks tagged, but only a small fraction of expected generations landed — e.g., a LangFuse rate-limit mid-run dropped 19/20 trace writes). Both fail the gate differently.
- **Fix-induced regression against ext12 #122 (redux).** #122 landed the zero-vs-nonzero gate; #129 extended per-task; #130 exposes that the gate's threshold is still too permissive — `> 0` is a weak predicate when the expected count is ~N_tasks per run.
- **Detectability is poor.** The per-condition log line shows "1/1"; the pre-write "Backfill: N/M records would get non-zero token costs" shows most records will get values; the written values are small but nonzero. No surface signal that "most expected generations didn't land."

### Remediation

**A. Quantitative coverage gate tied to expected generation count.**

Each run has an expected generations-per-task floor (empirically: at least ~1 generation per JIT task for a normal pipeline invocation; typically more due to decomposition + classification + verification LLM calls). Use a conservative lower bound — at least 1 generation per JIT-record — as the minimum-coverage threshold:

```python
# scripts/backfill_token_costs.py — after run_summaries / record counts are available:
_MIN_GENS_PER_JIT_RECORD = 0.5  # conservative floor; tune per campaign

under_covered_runs: list[tuple[str, int, int]] = []
for rid, s in run_summaries.items():
    if s.get("condition") == "static":
        continue
    n_gens = s.get("n_generations", 0)
    jit_records = run_record_counts.get(rid, 0) - run_retrieval_record_counts.get(rid, 0)
    if jit_records <= 0:
        continue
    expected_gens = jit_records * _MIN_GENS_PER_JIT_RECORD
    if n_gens < expected_gens:
        under_covered_runs.append((rid, n_gens, jit_records))

if under_covered_runs:
    print(
        f"\nERROR: {len(under_covered_runs)} non-static run(s) have FEWER "
        f"matched LangFuse generations than expected for their JIT-record "
        f"count (threshold: {_MIN_GENS_PER_JIT_RECORD:.1f} gens per JIT record).",
        file=sys.stderr,
    )
    for rid, n_gens, jit_records in under_covered_runs[:5]:
        print(
            f"  {rid}: {n_gens} generations / {jit_records} JIT records "
            f"= {n_gens / jit_records:.2f} (expected ≥ {_MIN_GENS_PER_JIT_RECORD:.1f})",
            file=sys.stderr,
        )
    if not args.allow_under_coverage:
        print(
            "  Refusing to write severely under-covered backfill. Pass "
            "--allow-under-coverage to proceed (document in Chapter 4.5 "
            "as an M4 limitation). ext13 #130. Exit 10.",
            file=sys.stderr,
        )
        conn.close()
        return 10
```

**B. Redesign the per-condition display to show generations-vs-expected ratio, not runs-with-≥1.**

```python
# Replace the binary "1/1" with a quantitative readout:
print("\n  Per-condition LangFuse coverage (generations-per-JIT-record):")
for cond in sorted({s.get("condition", "unknown") for s in run_summaries.values()}):
    cond_runs = [(rid, s) for rid, s in run_summaries.items() if s.get("condition") == cond]
    total_gens = sum(s.get("n_generations", 0) for _, s in cond_runs)
    total_jit = sum(
        run_record_counts.get(rid, 0) - run_retrieval_record_counts.get(rid, 0)
        for rid, _ in cond_runs
    )
    ratio = total_gens / total_jit if total_jit > 0 else 0.0
    marker = "⚠" if ratio < _MIN_GENS_PER_JIT_RECORD and cond != "static" else " "
    print(f"  {marker} {cond:<18} {total_gens} gens / {total_jit} JIT records = {ratio:.2f}/record")
```

Reader sees "full: 1 gens / 17 JIT records = 0.06/record" and the `⚠` marker rather than a deceptive "1/1".

**C. CLI flag.**

```python
parser.add_argument(
    "--allow-under-coverage", action="store_true",
    help="Proceed when LangFuse generation count per JIT record is below "
         f"threshold (default {_MIN_GENS_PER_JIT_RECORD:.1f}/record). "
         "Default: refuse with exit 10. Chapter 4.5 disclosure REQUIRED. "
         "ext13 #130.",
)
parser.add_argument(
    "--min-gens-per-jit-record", type=float, default=_MIN_GENS_PER_JIT_RECORD,
    help="Minimum LangFuse generations per JIT record to consider a run "
         f"adequately covered (default: {_MIN_GENS_PER_JIT_RECORD:.1f}). "
         "Below this, exit 10 unless --allow-under-coverage is passed.",
)
```

**D. Regression test.**

```python
def test_backfill_refuses_severely_under_covered_run(monkeypatch, tmp_path):
    """A 20-task run with 1 matched generation → exit 10."""
    db = _seed_20_task_run_with_n_generations(tmp_path, n=1)
    rc = backfill_token_costs.main(["--db", str(db), "--write"])
    assert rc == 10, f"expected exit 10, got {rc}"

def test_allow_under_coverage_proceeds_with_warning(tmp_path):
    db = _seed_20_task_run_with_n_generations(tmp_path, n=1)
    rc = backfill_token_costs.main([
        "--db", str(db), "--write", "--allow-under-coverage",
    ])
    assert rc == 0  # override succeeded

def test_per_condition_display_shows_quantitative_ratio(tmp_path, capsys):
    db = _seed_20_task_run_with_n_generations(tmp_path, n=1)
    backfill_token_costs.main(["--db", str(db)])  # dry run
    captured = capsys.readouterr()
    # Expect "gens / JIT records" framing, not binary "1/1"
    assert "/record" in captured.out
    assert "⚠" in captured.out  # warning marker fires at 0.06 < threshold
```

### Recommendation

**P0 — ship with #129 as an amendment / fix-induced follow-up to ext12 #122.** ~40 LOC (threshold computation + gate + display rewrite + CLI flags) + 3 regression tests. ~1h. Must ship before Row 11 feeds Row 17 M4 in Chapter 6.

### Legacy data

Any prior Row 11 output on a LangFuse-throttled / retention-aged campaign has silent per-task under-reporting proportional to the generations-per-JIT-record ratio. Re-run post-fix after confirming LangFuse generation-count health OR re-use `--allow-under-coverage` with Chapter 4.5 disclosure of the coverage ratio per run.

### Relationship to #129

#129 and #130 address the same root surface — "tagged run, incomplete generation coverage" — but at different granularities:

| Finding | Granularity | Symptom | Gate |
|---|---|---|---|
| #129 | Per-(run_id, task_id) tag presence | Specific tasks get `token_cost=0` within an otherwise-tagged run | Check expected keys in `run_task_tokens` |
| #130 | Per-run generations-vs-expected ratio | Whole run gets tiny per-task cost because total generations << expected | Check `n_gens / jit_records >= threshold` |

A tagged run can be partial in BOTH senses: some tasks missing tags (#129) AND the remaining tagged tasks have fewer generations than expected per-task (#130). The two gates are complementary and should both land.

---

## Issue #131 — `analyze_results.py` completeness gate is row-count-only; a 400-row DB of T01 repeated 100× per condition passes the gate (P0 CRITICAL) — ✅ FIXED 2026-04-21

**Fix landed:** two new SQL-driven checks after the existing row-count check: (1) per-condition `COUNT(DISTINCT task_id) != expected_tasks`; (2) per-(condition, task_id) `COUNT(*) != expected_runs` via `HAVING`. Both feed into the `issues` list and inherit ext7 #52's exit 2 / `--allow-incomplete` contract.

### Summary

ext7 #52's `--expected-conditions`/`--expected-tasks`/`--expected-runs-per-condition` completeness gate at `scripts/analyze_results.py:1411-1431` checks:

1. `total` non-pilot row count equals `expected_conditions × expected_tasks × expected_runs` (default 4 × 20 × 5 = 400).
2. Each condition's row count equals `expected_tasks × expected_runs` (default 100 per condition).
3. All `expected_conditions` are present.

**It does not check distinct-task coverage per condition.** A DB with 400 rows where every row has `task_id='T01'` (100 repetitions per condition of the same task) satisfies all three criteria — 400 total, 100 per condition, 4 conditions present — and the gate exits cleanly. Chapter 6 tables then aggregate 400 rows that cover 1/20 of the testbed.

All downstream metrics (M1 success rate, M4 token cost, M5 latency, core-work classification accuracy) silently compute on the single-task subset. The per-task breakdown table (Table 3) reports T01 rows and prints dashes for T02-T20 — but exit 0 at the top-level says "complete campaign."

Reviewer-confirmed reproduction: "synthetic DB with 400 rows but only T01 across all conditions still exits 0 (Total non-pilot records: 400)."

### Where

- **Row-count-only gate:** `scripts/analyze_results.py:1411-1431`.
- **No COUNT(DISTINCT task_id) check:** same region — only `SELECT condition, COUNT(*) FROM evaluation_runs GROUP BY condition`.
- **No per-(condition, task_id) count check:** same region.

### Reproduction

```sql
-- Seed 400 rows: 4 conditions × 5 runs × 20 "tasks" where every task_id='T01'.
INSERT INTO evaluation_runs (condition, task_id, run_id, is_pilot, ...) VALUES
    ('full', 'T01', 'run_full_0', FALSE, ...) × 100,
    ('no_verification', 'T01', 'run_nv_0', FALSE, ...) × 100,
    ...  -- all 400 rows have task_id='T01'
```

```bash
$ uv run scripts/analyze_results.py --db /tmp/t01_only.duckdb
  Total non-pilot records:    400  ← gate passes
  ...Table 3 (per-task breakdown): T01 has all 400 rows; T02-T20 all dashed...
$ echo $?
0
```

### Threat model / impact

- **Chapter 6 Tables 1/4/5/6 silently compute on a 1-task subset** when the DB is genuinely T01-repeated (pathological but possible under #125 partial-flush + a re-run that duplicates the first task's rows). Direction of bias: all over the map depending on whether T01 is representative.
- **Fix-induced-on-fix pattern (meta).** ext7 #52 correctly solved "refuse incomplete row counts"; #131 is the row-count's-granularity-vs-testbed-coverage granularity mismatch — same pattern as ext13 #127 / #128 / #129 / #130 (completeness gate at one granularity, failure mode at finer granularity).
- **Detection from output is poor.** A reviewer reading Table 1 sees 4 conditions × real numbers; they'd need to scroll to the per-task detail table to notice the T01-only anomaly.

### Remediation

**A. Per-condition distinct-task-id coverage check.**

```python
# scripts/analyze_results.py — after the existing per-condition row-count check:
per_cond_task_coverage = conn.execute(
    f"SELECT condition, COUNT(DISTINCT task_id) "
    f"FROM evaluation_runs WHERE is_pilot = FALSE{_SCOPE_SQL} "
    f"GROUP BY condition"
).fetchall()

for condition, distinct_tasks in per_cond_task_coverage:
    if distinct_tasks != expected_tasks:
        issues.append(
            f"condition '{condition}': {distinct_tasks} distinct tasks, "
            f"expected {expected_tasks} (T01..T{expected_tasks:02d})"
        )
```

**B. Per-(condition, task_id) row-count uniformity check.**

```python
uneven = conn.execute(
    f"SELECT condition, task_id, COUNT(*) "
    f"FROM evaluation_runs WHERE is_pilot = FALSE{_SCOPE_SQL} "
    f"GROUP BY condition, task_id HAVING COUNT(*) != ?",
    [expected_runs],
).fetchall()

for condition, task_id, n in uneven:
    issues.append(
        f"condition '{condition}', task '{task_id}': {n} rows, expected {expected_runs}"
    )
```

**C. Regression test.**

```python
def test_completeness_gate_rejects_single_task_inflation(tmp_path):
    db = _seed_400_rows_all_t01(tmp_path)
    rc, stderr = run_analyze_results(db)
    assert rc == 2, "expected exit 2 on distinct-task coverage gap"
    assert "distinct tasks" in stderr

def test_completeness_gate_accepts_full_20task_coverage(tmp_path):
    db = _seed_400_rows_t01_through_t20(tmp_path)
    rc, _ = run_analyze_results(db)
    assert rc == 0
```

### Recommendation

**P0 — ship as a ext7 #52 follow-up / amendment.** ~20 LOC (two SQL queries + issue accumulation) + 2 regression tests. ~30 min. Inherits the existing exit 2 + `--allow-incomplete` contract.

---

## Issue #132 — `print_contamination_rate` has no per-condition completeness guard; partial M2 labels treated as valid (P0 CRITICAL) — ✅ FIXED 2026-04-21

**Fix landed:** `print_contamination_rate` now returns `bool`; after the data print loop it computes `missing_conditions = set(CONDITIONS) - present_conditions` and returns `False` with an `⚠ M2 partial coverage` stderr message when any expected condition is missing labels. Threaded into #126's `missing_metrics` list; exit 7 shared with M3.

### Summary

`scripts/analyze_results.py:1246-1272` computes M2 contamination by grouping `evaluation_runs` rows that have `human_label IS NOT NULL AND NOT is_pilot`. The only completeness check is line 1266-1273:

```python
if not rows:
    print(
        "  Audit not performed — `human_label` is unpopulated. "
        ...
        "Chapter 6 §M2 should report as *deferred* until the audit lands."
    )
    return
```

This covers the GLOBAL "no labels anywhere" case. It does NOT cover "labels exist in some conditions but not others" — exactly the failure mode #121 (ext12 sample-plan persistence) or ext8 #57 resume failures produce: labels land for one campaign or one condition, later campaigns' labels never get written.

When labels exist only for `condition='full'`, the GROUP BY returns 1 row; `if not rows:` is False; the function prints an M2 table with one data row and no sibling rows for `static` / `no_verification` / `no_optimization`. No loud-fail, no exit-code escalation. `main()` continues; `if __name__ == "__main__": main()` → exit 0.

Reviewer-confirmed reproduction: "DB with labels only in one condition prints M2 table for that single condition and exits 0."

Direction of bias: destroys the JIT-vs-static contamination comparison. Chapter 6 M2's thesis purpose is the comparative framing; a one-condition M2 is publishable-looking but counterfactual-missing.

### Where

- **Single global completeness check (no per-condition):** `scripts/analyze_results.py:1266-1273`.
- **Main flow continues on partial M2:** `scripts/analyze_results.py:1505` (the `print_contamination_rate(conn)` call site).
- **Bare entrypoint → exit 0:** `scripts/analyze_results.py:1519-1520`.

### Remediation

**A. Per-condition audit-coverage gate.**

```python
# Inside print_contamination_rate, after the query:
present_conditions = {r[0] for r in rows}
expected_conditions = {"full", "no_verification", "no_optimization", "static"}
missing_conditions = expected_conditions - present_conditions

if missing_conditions:
    print(
        f"\n  ⚠ M2 partial coverage: labels missing for "
        f"{len(missing_conditions)}/{len(expected_conditions)} conditions: "
        f"{sorted(missing_conditions)}",
        file=sys.stderr,
    )
    return False  # reuses ext13 #126's missing-metrics pattern

return True
```

**B. `main()` threads the return value into `missing_metrics`** (same pattern as ext13 #128):

```python
if not print_contamination_rate(conn):
    missing_metrics.append("M2 partial (missing conditions; see stderr above)")
```

Exit 7 (ext13 #126) covers both "M2 globally absent" and "M2 per-condition gaps."

**C. Regression test.**

```python
def test_m2_partial_condition_coverage_flags_missing(tmp_path):
    db = _seed_400_row_db_labels_only_in_full(tmp_path)
    rc, stderr = run_analyze_results(db)
    assert rc == 7
    assert "M2 partial coverage" in stderr
    assert "static" in stderr
```

### Recommendation

**P0 — ship alongside #128 as M2's sibling of M3's partial-coverage gate.** ~15 LOC + 1 test. ~20 min.

---

## Issue #133 — `analyze_context_utilization.py` gate only tests "any traces at any agent"; missing agent families print dashes and exit 0 (P0 CRITICAL) — ✅ FIXED 2026-04-21

**Fix landed:** after ext12 #120's degraded-filter block, check `missing_agents = [name for name in AGENT_TRACE_NAMES if not results.get(name)]`. Non-empty list → exit 4 unless `--allow-empty` is passed; stderr lists the missing families. Exit 4 distinct from the existing exit 2 (global-empty) and exit 3 (all-degraded).

### Summary

`scripts/analyze_context_utilization.py:607-617` gates on the global trace count:

```python
total_traces = sum(len(tlist) for tlist in results.values())
if total_traces == 0 and not args.allow_empty:
    print(f"\nERROR: zero traces found for any agent ...", file=sys.stderr)
    return 2
```

If ANY agent has ANY traces, the gate passes. The script iterates `results` which is keyed on agent name (`task-decomposer`, `adversarial-probe`, `failure-analyst`, `lead-agent` — the four target families). Families with `len(trace_list) == 0` print dashes in Row 13's output table and the function proceeds.

ext12 #120 added post-fetch filtering of degraded traces and a loud-fail when "every trace for every agent is degraded." That fix covers the "all agents present but all traces fetch-failed" case. It does NOT cover "one or more agent families have zero traces to begin with" — exactly the failure mode that occurs when a campaign's LangFuse tagging dropped one agent's session_id or when the harness invoked only a subset of agents.

Reviewer-confirmed reproduction: "only task-decomposer traces present, other three families empty → script prints dashes and exits 0."

### Where

- **Gate on global trace count:** `scripts/analyze_context_utilization.py:607-617`.
- **ext12 #120 filters degraded but doesn't require per-agent presence:** `scripts/analyze_context_utilization.py:619+`.
- **Per-agent zero-count prints dashes:** the downstream aggregation tables iterate `results` and print dashes when a family is empty.

### Remediation

**A. Per-agent-family presence gate.**

```python
# scripts/analyze_context_utilization.py — after the global total_traces check:
_EXPECTED_AGENTS = {"task-decomposer", "adversarial-probe", "failure-analyst", "lead-agent"}
missing_agents = [a for a in _EXPECTED_AGENTS if len(results.get(a, [])) == 0]

if missing_agents:
    print(
        f"\nERROR: {len(missing_agents)}/{len(_EXPECTED_AGENTS)} agent "
        f"families have zero traces: {missing_agents}",
        file=sys.stderr,
    )
    if not args.allow_empty:
        print(
            "  Chapter 6 Context Economy requires per-agent comparison. "
            "Pass --allow-empty to proceed with dashed rows. Exit 4.",
            file=sys.stderr,
        )
        return 4
```

New exit code 4 distinguishes "some agents missing" from the existing "zero traces globally" (exit 2) and "all degraded" (exit 3 per ext12 #120).

**B. Regression test.**

```python
def test_missing_agent_family_refused_with_exit_4(monkeypatch):
    monkeypatch.setattr(
        "analyze_context_utilization.fetch_traces",
        lambda *a, **kw: [{"id": "t1", ...}] if kw.get("agent") == "task-decomposer" else [],
    )
    rc = analyze_context_utilization.main([])
    assert rc == 4
```

### Recommendation

**P0 — ship as a ext12 #120 follow-up.** ~15 LOC + 1 test. ~20 min.

---

## Issue #134 — Row 6a Phase B/C judge outages produce `verdict=None`; summary filters None and treats 0/0 as PASS (P0 CRITICAL) — ✅ FIXED 2026-04-21

**Fix landed:** `_print_summary` now computes `eligible_pos + eligible_trap` vs `pos_audited + trap_audited` → `audited_ratio` and gates on `_MIN_AUDIT_COVERAGE = 0.8`. Below threshold, prints an error-category breakdown (first token of each row's `error` field) and flips `all_pass_b = False` — the existing `return 0 if all_pass else 1` entrypoint then exits 1 instead of 0.

### Summary

`scripts/test_registry_matching.py:441-450` wraps each Phase B judge call in a broad `try/except Exception` and stores `PhaseBResult(..., verdict=None, error=...)` on any failure. The Phase-B pass computation at `scripts/test_registry_matching.py:1768-1781` filters to `r.verdict is not None` before computing `pos_kept` / `pos_lost`:

```python
pos_audited = [
    r for r in phase_b
    if r.probe.expected_seed is not None
    and r.probe.context_compatible
    and r.verdict is not None   # ← excludes every failed-judge row
]
...
pos_kept = sum(1 for r in pos_audited if r.verdict.compatible)
pos_lost = len(pos_audited) - pos_kept
```

When the LLM judge is fully unavailable (API outage, auth rotation, rate-limit blanket), every call raises → every `verdict=None` → `pos_audited == []` → `pos_kept == 0, pos_lost == 0`. The `all_pass_b` computation at line 1766+ yields True (no failures counted because no audits happened). Row 6a prints "OVERALL: PASS" and the return code path exits 0.

Reviewer-confirmed reproduction: "forced judge errors for all Phase B/C items → OVERALL: PASS, return code 0." Row 6a's purpose is to validate that registry matching doesn't hallucinate — a judge outage turning that into "PASS" means the whole validation step is bypassable by a transient LLM failure.

### Where

- **Broad-except sets verdict=None:** `scripts/test_registry_matching.py:441-450`.
- **Filter excludes None verdicts from pass computation:** `scripts/test_registry_matching.py:1768-1781`.
- **No "minimum N audited" guard:** same region.

### Remediation

**A. Minimum-audit-coverage gate.**

```python
# scripts/test_registry_matching.py — before computing all_pass_b:
_MIN_AUDIT_COVERAGE = 0.8  # at least 80% of eligible rows must have a verdict

eligible_pos = [r for r in phase_b if r.probe.expected_seed is not None and r.probe.context_compatible]
audited_ratio = len(pos_audited) / len(eligible_pos) if eligible_pos else 1.0

if audited_ratio < _MIN_AUDIT_COVERAGE and eligible_pos:
    print(
        f"\nERROR: judge audit coverage is {audited_ratio:.1%} "
        f"({len(pos_audited)}/{len(eligible_pos)}). Below {_MIN_AUDIT_COVERAGE:.0%} "
        f"threshold — likely LLM judge outage. Refusing to report PASS.",
        file=sys.stderr,
    )
    # Enumerate error categories for diagnosis:
    error_counts: dict[str, int] = {}
    for r in phase_b:
        if r.verdict is None and r.error:
            category = r.error.split(":")[0]
            error_counts[category] = error_counts.get(category, 0) + 1
    for cat, n in sorted(error_counts.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {n}", file=sys.stderr)
    return 2  # distinct from argparse-error (1)
```

**B. Regression test.**

```python
def test_judge_outage_refuses_overall_pass(monkeypatch):
    monkeypatch.setattr(
        "scripts.test_registry_matching._run_phase_b_judge",
        Mock(side_effect=RuntimeError("simulated judge outage")),
    )
    rc = test_registry_matching.main([])
    assert rc == 2, "expected exit 2 on judge outage"
```

### Recommendation

**P0 — ship as a Row 6a integrity fix.** ~25 LOC + 1 regression test. ~30 min.

---

## Issue #135 — Row 6b silently skips capabilities with paraphrase-generation failures; recall computed on non-skipped subset (P0 CRITICAL) — ✅ FIXED 2026-04-21

**Fix landed:** `scripts/test_registry_fn.py` now tracks `skipped_caps` inside the main loop, runs a one-shot retry pass over that list, and after the retry enforces a minimum capability-coverage threshold (`--min-cap-coverage`, default 0.9). Below threshold, exit 2 with stderr listing the still-skipped capabilities; above threshold, the existing top-3 recall pass/fail contract runs unchanged.

### Summary

`scripts/test_registry_fn.py:265-275`:

```python
paraphrases = generate_paraphrases(
    cap, args.paraphrases_per_cap, structured_llm
)
if not paraphrases:
    print(f"  WARNING: skipped {cap.name} (no paraphrases generated)")
    continue

result = probe_cap(registry, cap, paraphrases, ...)
results.append(result)
```

Skipped capabilities never enter `results`. At line 301-304:

```python
total_queries = sum(r.total for r in results)
if total_queries == 0:
    print("\nNo queries were probed. Exiting with failure.")
    return 1
```

The only failure condition is "zero capabilities probed globally." A campaign where 1/8 capabilities had paraphrases generated and 7/8 got skipped has `total_queries > 0` (from the 1 probed capability); recall is computed on that 1-cap subset; if the 1 capability's paraphrases all matched, recall reports 100%.

Reviewer-confirmed reproduction: "1 capability probed, 7 skipped → script still returns 0 with 100% recall." Row 6b's false-negative audit is the proposal's primary retrieval-quality evidence; skipping 87.5% of capabilities and reporting "100% recall" on the remainder is thesis-destroying silent evidence manufacturing.

### Where

- **Silent skip on paraphrase generation failure:** `scripts/test_registry_fn.py:273-275`.
- **Final gate tests `total_queries == 0` globally:** `scripts/test_registry_fn.py:301-304`.
- **No "minimum capabilities probed" threshold:** same region.

### Remediation

**A. Minimum-capability-coverage gate.**

```python
# scripts/test_registry_fn.py — after the probing loop:
n_skipped = len(caps) - len(results)
coverage = len(results) / len(caps) if caps else 1.0
_MIN_CAP_COVERAGE = 0.9

if coverage < _MIN_CAP_COVERAGE:
    print(
        f"\nERROR: paraphrase-generation coverage is {coverage:.1%} "
        f"({len(results)}/{len(caps)} capabilities probed, {n_skipped} skipped). "
        f"Below {_MIN_CAP_COVERAGE:.0%} threshold — refusing to report "
        f"recall on a biased subset.",
        file=sys.stderr,
    )
    return 2  # distinct from "no queries at all" (1)
```

**B. Retry pass for transient failures.**

```python
# Before declaring failure, retry each skipped cap once:
retryable = [cap for cap in caps if cap.name not in {r.cap_name for r in results}]
if retryable:
    print(f"\nRetrying {len(retryable)} skipped capabilities once...")
    for cap in retryable:
        paraphrases = generate_paraphrases(cap, args.paraphrases_per_cap, structured_llm)
        if paraphrases:
            results.append(probe_cap(registry, cap, paraphrases, ...))
```

**C. Regression test.**

```python
def test_row_6b_refuses_report_on_low_capability_coverage(monkeypatch):
    # Mock generate_paraphrases to return [] for 7/8 caps, [...] for 1.
    call_count = [0]
    def stub(cap, n, llm):
        call_count[0] += 1
        return ["paraphrase"] if call_count[0] == 1 else []
    monkeypatch.setattr("scripts.test_registry_fn.generate_paraphrases", stub)
    rc = test_registry_fn.main([])
    assert rc == 2
```

### Recommendation

**P0 — ship as a Row 6b integrity fix.** ~25 LOC + 1 regression test. ~30 min.

---

## Issue #136 — Row 2 `test_pipeline.py` smoke test is fail-open on verification + registration stages (P0 CRITICAL) — ✅ FIXED 2026-04-21

**Fix landed:** stage 5 now flips `all_pass=False` when `verification_results` is empty AND `artifacts` were generated ("Pillar 2 gate was skipped entirely"). Stage 6 now flips `all_pass=False` when `gaps and artifacts and new_caps == 0` ("loader no-op") rather than printing WARN and continuing.

### Summary

`scripts/test_pipeline.py` is Row 2 of the run sheet — the integration smoke test that validates the JIT pipeline's stages end-to-end. Its pass/fail decision is driven by the `all_pass` boolean. Two stage-level checks never flip `all_pass=False` even when the stage fails:

**Stage 5 — Verification (line 187-200):**
```python
verif_results = _get("verification_results", []) or []
print(f"\n  5. Verification: {len(verif_results)} report(s)")
for vr in verif_results:
    ...
if verif_results:
    print("     OK")
```
Empty `verif_results` → loop doesn't iterate → `if verif_results` is False → no "OK" print — and no "FAIL" either. `all_pass` stays True. A pipeline that skipped verification entirely (Pillar 2 gate bypassed) passes stage 5.

**Stage 6 — Loading + registration (line 202-217):**
```python
if artifacts and new_caps > 0:
    print("     OK")
elif gaps and new_caps == 0:
    print("     WARN: Gaps existed but no new capabilities registered")
else:
    print("     OK")
```
The `elif gaps and new_caps == 0` branch prints WARN without flipping `all_pass`. A pipeline that detected gaps, generated artifacts, but registered nothing (registration silently failed) passes stage 6.

Reviewer-confirmed reproduction: "patched pipeline returning gaps>0, artifacts>0, verification_results=[], registry delta 0; function still returned True and printed PASS." Row 2's smoke-test purpose — early warning that the pipeline's core stages are working — is defeated whenever the specific stages it's supposed to validate silently no-op.

### Where

- **Stage 5 verification-empty no-fail:** `scripts/test_pipeline.py:187-200`.
- **Stage 6 registration-empty downgrades to WARN:** `scripts/test_pipeline.py:212-217`.
- **`all_pass` never flipped in either branch.**

### Reproduction

```python
# Patched pipeline state stub:
state = {
    "gaps": [Gap(...), Gap(...)],               # gaps>0
    "artifacts": [Artifact(...), Artifact(...)],  # artifacts>0
    "verification_results": [],                 # EMPTY — Pillar 2 skipped
    "status": "complete",
    "attempt_count": 1,
    "errors": [],
}
# Registry delta 0 (registration no-op).

# Run test_pipeline._run_one_test(state, ...) → returns True, prints PASS.
$ echo $?
0
```

### Threat model / impact

- **Row 2 is the early-warning step** for Pillar 1/2/3 wiring. Fail-open at stages 5 + 6 means the whole run sheet can proceed on a pipeline where verification never fires and capabilities never register — the very failure modes Pillar 2 and the registry are supposed to validate against.
- **Compounds with ext8 #63.** #63's partial fix (WARN on missing SKILL/AGENT schemas) is the exact anti-pattern #136 generalizes: silent WARN without a fail flag.
- **Compounds with the ext12/ext13 compounding failure chains.** A Row 2 false-PASS means downstream Row-11/17/18/19 analyzers proceed on the assumption the pipeline is healthy. Every other fail-open surface (ext13 #125 partial flush, ext12 #123 missing shift marker) lands on data generated by a pipeline Row 2 said was OK.
- **Direction of bias: pro-pipeline-health.** Exactly the direction that delays detection of real regressions.

### Remediation

**A. Flip `all_pass` on empty verification_results when artifacts exist.**

```python
# scripts/test_pipeline.py:187-200 — after:
verif_results = _get("verification_results", []) or []
print(f"\n  5. Verification: {len(verif_results)} report(s)")
if not verif_results and artifacts:
    print("     FAIL: artifacts generated but no verification reports — Pillar 2 gate skipped")
    all_pass = False
for vr in verif_results:
    ...
```

**B. Flip `all_pass` on registration no-op.**

```python
# scripts/test_pipeline.py:212-217 — after:
if artifacts and new_caps > 0:
    print("     OK")
elif gaps and new_caps == 0:
    print("     FAIL: Gaps existed and artifacts generated but no new capabilities registered")
    all_pass = False
elif not gaps:
    print("     OK (no gaps — nothing to register)")
```

**C. Regression test.**

```python
def test_row_2_fails_on_empty_verification_results(monkeypatch):
    state_stub = _make_state(gaps=2, artifacts=2, verif=[], registry_delta=0)
    monkeypatch.setattr(..., state_stub)
    rc = test_pipeline.main([])
    assert rc != 0

def test_row_2_fails_on_registration_noop():
    ...
```

### Recommendation

**P0 — blocks run-sheet integrity.** ~15 LOC + 2 regression tests. ~20 min.

---

## Issue #137 — `audit_contamination.py` silently skips unresolvable rows without counter; Row 16 exits 0 with zero labels produced (P0 CRITICAL) — ✅ FIXED 2026-04-21

**Fix landed:** the `counts` dict gained three explicit auto-skip counters (`auto_skip_no_invocation`, `auto_skip_seeded`, `auto_skip_no_source`), each incremented from the corresponding legacy-fallback branch. End-of-session summary prints the breakdown; when `total_labeled == 0`, exit 3 with a stderr message naming the likely cause (legacy DB without Option B snapshot column or mid-campaign snapshot reset).

### Summary

`scripts/audit_contamination.py:660-680` has a legacy-DB fallback path for rows that lack the Step 18.10 Option B `source_snapshots` field. The fallback resolves the capability name from `invocation_events` and the source from the current registry. Three branches silently `continue`:

```python
cap_name = resolve_capability_name(store._conn, row)
_print_header(row, idx, len(sample), cap_name)

if cap_name is None:
    print("  (no invocation_events row matches this run; skipping)")
    continue

if is_seeded(registry, cap_name):
    print(f"  (capability '{cap_name}' is a pinned seed, not generated; skipping)")
    continue

source = resolve_source(registry, cap_name)
if source is None:
    print(f"  (capability '{cap_name}' not in registry — "
          f"snapshot may have been reset; skipping — "
          f"legacy DB without Step 18.10 Option B snapshot column)")
    continue
```

None of the three branches increment `counts["skip"]` (that counter is for the user-selected "skip" keypress at line 690-692). Rows auto-skipped by the fallback are invisible in the final summary at line 697-707. The function proceeds past the loop and unconditionally returns 0 at line 709.

A DB where every unlabeled row hits one of these branches — common when snapshots were reset between the campaign and the audit, OR when the DB has `generated_artifact_count > 0` but no `generated_artifact_sources` (pre-Option-B legacy shape) — produces zero human labels. Final summary: `correct=0, contaminated=0, partial=0, skipped=0`; exit 0.

Reviewer-confirmed reproduction: "synthetic DB with 3 unlabeled rows (generated_artifact_count>0) but no generated_artifact_sources and no matching invocation_events → all 3 rows auto-skipped, summary correct=0 contaminated=0 partial=0, return code 0."

Row 16's purpose is to populate `human_label` values that feed Row 17's M2 contamination rate. A Row 16 that silently produces zero labels means Row 17's M2 is either stale (reuses prior labels) or absent (ext12 #121 / ext13 #132 territory). Either way, Chapter 6 M2 has no fresh evidence from this campaign.

### Where

- **Three auto-skip branches:** `scripts/audit_contamination.py:667-680`.
- **No counter increment:** same region — `continue` without `counts[...] += 1`.
- **Unconditional `return 0`:** `scripts/audit_contamination.py:709`.
- **Summary doesn't surface auto-skips:** `scripts/audit_contamination.py:697-707`.

### Reproduction

```bash
# Seed: 3 unlabeled evaluation_runs rows with generated_artifact_count=1, no
# source_snapshots column populated, no matching invocation_events rows.

$ uv run scripts/audit_contamination.py --db /tmp/legacy.duckdb
  (no invocation_events row matches this run; skipping)
  (no invocation_events row matches this run; skipping)
  (no invocation_events row matches this run; skipping)

════════════════════════════════════════════════════════════════════════════
Audit complete.
  correct:      0
  contaminated: 0
  partial:      0
  skipped:      0        ← auto-skips not counted
════════════════════════════════════════════════════════════════════════════

$ echo $?
0
```

### Threat model / impact

- **Chapter 6 M2 silently un-populated** while Row 16 reports success. Downstream Row 17 `print_contamination_rate` either re-reads stale labels or fires ext13 #126's "audit not performed" branch — both land badly.
- **Fix-induced against Step 18.10 Option B.** The Option B remediation (in-DB source snapshots) was meant to eliminate the legacy-path fallback; but the fallback was kept for backward compat AND didn't get a completeness gate. Partial remediation + uncounted silent skips = same pattern as ext13 #127 / #128 / #129 / #130.
- **Direction of bias: pro-thesis.** Zero labels → no evidence of contamination → Chapter 6 M2 either absent or reflects only pre-campaign legacy data.

### Remediation

**A. Separate auto-skip counter from user-skip counter.**

```python
# Add to counts dict:
counts = {"correct": 0, "contaminated": 0, "partial": 0, "skip": 0,
          "auto_skip_no_invocation": 0, "auto_skip_seeded": 0, "auto_skip_no_source": 0}

# In each branch:
if cap_name is None:
    print("  (no invocation_events row matches this run; skipping)")
    counts["auto_skip_no_invocation"] += 1
    continue
if is_seeded(registry, cap_name):
    counts["auto_skip_seeded"] += 1
    continue
if source is None:
    counts["auto_skip_no_source"] += 1
    continue
```

**B. Loud-fail when no labels landed.**

```python
# scripts/audit_contamination.py — before line 709:
total_labeled = counts["correct"] + counts["contaminated"] + counts["partial"]
total_auto_skipped = sum(counts[k] for k in counts if k.startswith("auto_skip_"))

if total_labeled == 0:
    print(
        f"\nERROR: zero labels produced. {total_auto_skipped} rows auto-skipped:\n"
        f"  no matching invocation_events:    {counts['auto_skip_no_invocation']}\n"
        f"  seeded capability:                {counts['auto_skip_seeded']}\n"
        f"  not in registry (snapshot reset): {counts['auto_skip_no_source']}\n"
        f"Likely cause: legacy DB without Step 18.10 Option B snapshot column, OR\n"
        f"snapshot reset between campaign and audit. Row 17 M2 will have no fresh\n"
        f"labels from this session.",
        file=sys.stderr,
    )
    return 3  # new exit code: zero-labels-produced
```

**C. Regression test.**

```python
def test_row_16_exits_nonzero_on_zero_labels(tmp_path):
    db = _seed_3_unlabeled_legacy_rows(tmp_path)  # all hit auto-skip branches
    rc, stderr = run_audit_contamination(db)
    assert rc == 3
    assert "zero labels produced" in stderr
```

### Recommendation

**P0 — blocks Row 16 → Row 17 M2 integrity.** ~30 LOC + 1 regression test. ~30 min.

---

## Issue #138 — `analyze_tier2_probes.py` prints `Fetched 0 events` and exits 0; any filter typo or window mismatch silently empties Row 15 evidence (P0 CRITICAL) — ✅ FIXED 2026-04-21

**Fix landed:** after the fetch, `if not events and not args.allow_empty` returns exit 2 with a diagnostic message listing `environment`, window bounds, and (optionally) `session_ids` plus the four likely causes. New `--allow-empty` CLI flag mirrors ext7 #47's contract on the sibling analyzer.

### Summary

`scripts/analyze_tier2_probes.py:461-479`:

```python
with httpx.Client(base_url=host, auth=auth, timeout=30.0) as client:
    events = fetch_tier2_events(
        client, start, end,
        event_names=event_names,
        environment=args.environment,
        session_ids=args.session_ids,
    )

print(f"Fetched {len(events)} events.\n")

bd = aggregate(events)
render_table(bd, include_pass=args.include_pass)

if args.csv:
    render_csv(bd, args.output_dir)

return 0
```

No gate on `len(events) == 0`. A zero-event fetch prints "Fetched 0 events.", renders an empty table, and exits 0. Any cause of an empty fetch — `--session-ids <typo>`, `--environment` mismatch, window outside the data's timestamps, LangFuse retention aged out, event-name drift — silently produces "Row 15 complete with no Tier 2 evidence."

Reviewer-confirmed reproduction: "non-existent session id filter: got Fetched 0 events and RC=0."

Row 15 is the Pillar 2 Tier 2 probe-finding harvest step. Empty output means Chapter 6's Tier 2 probe-coverage evidence is absent; downstream Row 17's Tier 2 claim has no supporting data. ext7 #47 landed a loud-fail for `analyze_context_utilization.py` (Row 13's sibling analyzer) when zero traces land; `analyze_tier2_probes.py` is the same analyzer family and should have the same gate.

### Where

- **Zero-events-printed-as-success:** `scripts/analyze_tier2_probes.py:471`.
- **Unconditional `return 0`:** `scripts/analyze_tier2_probes.py:479`.
- **No `--allow-empty` flag analogous to ext7 #47's pattern.**

### Reproduction

```bash
$ uv run scripts/analyze_tier2_probes.py --session-ids nonexistent_session_id
Fetched 0 events.

<empty table rendered>

$ echo $?
0
```

### Threat model / impact

- **Row 15 fail-open on filter/window misconfiguration.** The exact class of bugs ext7 #47 solved for `analyze_context_utilization.py` — runner emission convention mismatch, session_id drift — produces zero-event fetches here too, and Row 15 reports success.
- **Compounds with ext7 #42 / #54** (session-id scoping fixes). Those fixes made scoping possible; they didn't add a "zero results = likely misconfiguration" gate. #138 is the exact ext7 #47 sibling for this analyzer.
- **Compounds with ext12 #120 / ext13 #133.** Those address per-agent / degraded traces in `analyze_context_utilization.py`; `analyze_tier2_probes.py` lacks both gates.
- **Direction of bias: pro-thesis-completeness appearance.** Chapter 6 Tier 2 section silently empty while Row 15 is green.

### Remediation

**A. Mirror ext7 #47's loud-fail pattern.**

```python
# scripts/analyze_tier2_probes.py:471 — replace:
print(f"Fetched {len(events)} events.\n")

if len(events) == 0 and not args.allow_empty:
    print(
        f"\nERROR: zero Tier 2 probe events fetched for "
        f"environment={args.environment!r}, "
        f"window=[{start}..{end}]"
        + (f", session_ids={args.session_ids!r}" if args.session_ids else "")
        + ". Check: (a) runner emission convention "
        "(runners use environment='default'), (b) --session-ids filter, "
        "(c) --days / --start / --end window. Pass --allow-empty to suppress.",
        file=sys.stderr,
    )
    return 2

print(f"Fetched {len(events)} events.\n")
```

**B. CLI flag.**

```python
parser.add_argument(
    "--allow-empty", action="store_true",
    help="Proceed with exit 0 when zero events match. Default: refuse with "
         "exit 2 (mirrors ext7 #47 / ext12 #120 contract for "
         "analyze_context_utilization.py). ext13 #138.",
)
```

**C. Regression test.**

```python
def test_tier2_probes_refuses_zero_events(monkeypatch):
    monkeypatch.setattr(
        "analyze_tier2_probes.fetch_tier2_events",
        lambda *a, **kw: [],
    )
    rc = analyze_tier2_probes.main(["--session-ids", "nonexistent"])
    assert rc == 2

def test_tier2_probes_allow_empty_exits_0(monkeypatch):
    monkeypatch.setattr(
        "analyze_tier2_probes.fetch_tier2_events",
        lambda *a, **kw: [],
    )
    rc = analyze_tier2_probes.main(["--allow-empty"])
    assert rc == 0
```

### Recommendation

**P0 — blocks Row 15 → Chapter 6 Tier 2 integrity.** ~15 LOC + 2 regression tests. ~20 min. Direct mirror of ext7 #47's fix for the sibling analyzer.

---

## Pattern notes

All fifteen findings share the same underlying pattern — **silent metric degradation with exit 0** — and extend the failure surface mapped by the initial three:

```
 Row 9/10                Row 18/19                   Row 17
 (longitudinal runner)   (analyze_longitudinal.py)   (analyze_results.py)
       │                       │                           │
       │ #125: partial flush   │                           │
       │ (utility / events /   │                           │
       │  invocations)         │                           │
       ▼                       ▼                           ▼
 partial DB ──────────► #124: silent KL ──────────► proxy-KL number
                         downgrade                   lands in Ch6
                                                           │
                                                           ▼
 partial DB ──────────────────────────────────────► #126: M3 silently
 (empty invocations)                                  skipped; other
                                                      sections print;
                                                      exit 0
```

#125 is the **upstream source** of silent-data-loss. #124 and #126 are the two **downstream consumers** that fail to loud-fail on the missing data. Both downstream consumers predate ext12 but slipped ext12's exit-code-per-failure-class pattern because the failure modes are data-shape-specific rather than operation-outcome-specific:

- ext12 #115-#118, #121-#123 remediated "operation outcomes that silently exit 0" (watchdog abort, partial campaign, marker missing, etc.).
- ext13 #124, #126 remediate "metric outcomes that silently exit 0 when upstream data is partial" — the same pattern class but scoped to analyzer internals rather than runner outcomes.

**Fix-induced bug count now 15 across the series.** #131 is fix-induced against ext7 #52 (row-count-only gate passes single-task inflation); #132 is fix-induced against ext7 #44 / #121 / #126 (global-empty loud-fail is correct but per-condition M2 coverage wasn't covered); #133 is fix-induced against ext7 #47 / ext12 #120 (global-zero gate is correct but per-agent-family presence wasn't covered); #134 + #135 aren't strictly fix-induced — they're pre-existing silent-success bugs in Row 6a / 6b that slip the same way as #124 / #126 slipped the ext12 discipline: they live in test-runner internals rather than runner-or-analyzer surfaces. Still, #134 + #135 follow the same pattern: a gate tests "any output exists" instead of "enough output exists." Counting them conservatively: the explicit fix-induced axis has 13 instances; the pattern-class axis (completeness-gate coarser than failure-mode) has 15. #127 is fix-induced against ext12 #118 (global `any()` gate is correct at the "all-empty" boundary but breaks down at the "trivially-sparse" boundary); #128 is fix-induced against ext13 #126 (all-empty loud-fail is correct but per-condition coverage wasn't covered by the same granularity); #129 is fix-induced against ext12 #122 (run-level tag coverage is correct but per-task-within-run tag coverage wasn't covered); #130 is ALSO fix-induced against ext12 #122 but at a different axis — the zero-vs-nonzero threshold is correct for "all traces missing" but breaks at "severely-under-covered" (e.g., 1 generation for 20 tasks). #130 + #129 demonstrate that a single fix can leak in multiple orthogonal granularity dimensions simultaneously.

Pattern is now unambiguous: **every completeness-gate remediation in this audit series has needed at least one follow-up because the gate's granularity was coarser than the failure-mode surface; sometimes more than one follow-up because multiple granularity axes are relevant simultaneously.** Chapter 4.5 should cite this explicitly as a methodology lesson: granularity of a completeness gate must match the granularity of the metric it guards, AND every gate should be audited along every axis the guarded metric can partially-fail (temporal, per-entity, per-(entity, sub-entity), quantitative threshold, etc.).

Conservatively, #124 is NOT fix-induced; it is a pre-existing silent-success bug that ext12's discipline should have caught but missed because it lives in analyzer internals rather than runner surfaces. #125 is arguably fix-induced against Step 18.7's "flush must not break the loop" design decision but that was a deliberate design trade, not a regression.

**Chapter 4.5 graduation proposal — codify the exit-code contract.** With 10 distinct P0 / CRITICAL findings across ext12 + ext13 all sharing the "silent success on partial/missing data" shape, Chapter 4.5 should document a single pipeline-wide table:

| Exit code | Meaning | Introduced by |
|---|---|---|
| 0 | success — every thesis-critical metric computed on complete data | — |
| 1 | argparse / generic CLI error | parent |
| 2 | runtime failure at a documented layer (decomp/gate/output-contract/convergence) | parent #10, ext6/7/8 |
| 3 | incomplete data, override available | ext12 #116 + #122 + #126 |
| 4 | zero-evidence DB | ext12 #118 |
| 5 | thesis-critical marker or flush missing | ext12 #123 + ext13 #125 |
| 6 | proposal-literal metric unavailable, proxy available | ext13 #124 |
| 7 | thesis-critical metric silently absent from output | ext13 #126 + #128 |
| 8 | sparse per-cycle telemetry (global `any()` passes but coverage is trivially small) | ext13 #127 |
| 9 | per-task tag coverage gap within a tagged run | ext13 #129 |
| 10 | generations-per-JIT-record below threshold (quantitative under-coverage) | ext13 #130 |
| 11 | judge/LLM-audit coverage below threshold (Row 6a/6b); completeness gate passes single-task / single-agent / single-condition inflation | ext13 #131, #132, #133, #134, #135 (reused across analyzers; per-script exit codes may extend this) |
| 12 | Row 2 smoke-test stage fail-open (verification skipped; registration no-op silently WARN) | ext13 #136 |
| 13 | zero-label audit produced (Row 16); zero-event Tier 2 fetch (Row 15) | ext13 #137, #138 (mirrors ext7 #47's pattern for the sibling analyzer) |

Every analyzer + runner in `scripts/` must adhere to this table. A run-sheet lint (extending ext7 #45 / #54) can verify every row's command-exit contract matches the documented semantics.

**Compounding failure chain now 5 independent chains, reinforced by the new findings:**
1. Rows 9/10 → 18/19: #119 → #117 → #116 → #118 → #123 (ext12) + #125 → #124 → #127 (ext13)
2. Rows 11 + 13 + 16 → 17: #120 + #121 + #122 (ext12) + #129 + #130 + #131 + #132 + #133 (ext13)
3. Rows 9/10 → 17: #125 → #126 → #128 + #131 (ext13)
4. Row 6a registry-matching: #134 (ext13) — LLM-judge outage produces PASS with zero audits
5. Row 6b registry-FN: #135 (ext13) — paraphrase-generation failure produces high-recall report on tiny subset
6. Row 2 smoke test: #136 (ext13) — fail-open on verification + registration stages; pipeline can silently skip Pillar 2 and still PASS
7. Row 16 M2 labeling: #137 (ext13) — auto-skip branches silently drop unresolvable rows without counter; zero-label audit exits 0
8. Row 15 Tier 2 probes: #138 (ext13) — zero-events fetch silently produces empty Chapter 6 section with exit 0

#127 extends chain 1 at the Row 18/19 end. #128 extends chain 3 at the Row 17 end. #129 + #130 extend chain 2 at the Row 11 end along two distinct granularity axes. #131 adds single-task inflation as a gate-slip on top of ext7 #52; #132 adds partial-condition M2 gaps; #133 adds per-agent-family gaps. #134 + #135 are new standalone chains — Row 6a / 6b are earlier in the run-sheet than the data-collection rows the other chains corrupt; their silent-PASS means the registry-matching validation step itself is bypassable before any campaign data is collected.

**Meta-pattern: granularity escalation.** The sequence ext12 #118 → ext13 #127 → future follow-up mirrors a class of bug where each remediation closes the failure mode it was aimed at but leaves the next-finer-granularity slice open. #118 (all-empty) → #127 (coverage < 50%) → (future?) per-cycle density, per-metric density, per-(condition, cycle) density. Chapter 4.5 should disclose that this is a finite-depth recursion: there's a natural floor where the gate's granularity matches the metric's computation granularity, and every audit round narrows toward it. Ship the current floor; document the remaining depth.

---

## Parent-document integration (pending)

When ext13 lands, fold forward into `security_audit_2026_04_19.md`:

1. Update header: "One hundred twenty-three findings → One hundred thirty-eight findings; #56-#138 open as of 2026-04-21."
2. Insert TL;DR rows #124-#138 after ext12's #123.
3. Tranche 3 (eval-blocking) gains **fifteen P0 CRITICAL** (#124-#138). Coverage map: #125 blocks Rows 9/10 telemetry integrity; #124 + #127 block Rows 18/19 Pillar 3 RQ #2; #126 + #128 + #131 block Row 17 M1/M3/M4/M5 across the row-count and distinct-task axes; #129 + #130 block Row 11 → Row 17 M4; #132 blocks Row 17 M2 per-condition; #133 blocks Row 13 per-agent Context Economy; #134 blocks Row 6a registry-matching validation; #135 blocks Row 6b false-negative audit; #136 blocks Row 2 smoke-test integrity; #137 blocks Row 16 → Row 17 M2 labeling; #138 blocks Row 15 Tier 2 probe evidence.
4. Extend "Behavioral regressions expected":
   - (#124) `analyze_longitudinal.py` loud-fails with exit 6 when `cycle_utility_scores` is empty; `--allow-proxy-kl` CLI flag lets the user opt into the proxy KL with a `[PROXY KL]` banner on every section header.
   - (#125) `flush_task_cycle_state` returns a `FlushResult` struct; both longitudinal runners accumulate per-task results and return exit 5 on campaign-level partial-flush; harness inherits the same pattern; optional end-of-campaign one-shot retry for transient failures.
   - (#126) Every `print_*` analysis function returns `bool` (computed vs skipped); `main()` accumulates `missing_metrics`; returns exit 7 when any thesis-critical metric absent; `--allow-missing-metrics` override with in-band `[MISSING: ...]` banner on every Chapter-6 section header.
   - (#127) `analyze_longitudinal.py` gate changes from global `any()` to per-cycle coverage threshold (default 50%); exit 8 on sparse telemetry; `--allow-sparse-signal` + `--min-cycle-coverage` CLI flags; `[SPARSE: N/M]` banner on every section header when override is used.
   - (#128) `print_gini_summary` returns False when any CONDITION row is dashed; `main()` treats that as a missing-metric and inherits #126's exit 7 path; stderr message enumerates the missing conditions.
   - (#129) `backfill_token_costs.py` gains a per-run per-task tag coverage gate; exit 9 on missing `(run_id, task_id)` keys in tagged runs; `--allow-partial-task-coverage` CLI flag; pre-write summary prints per-run-per-task coverage breakdown.
   - (#130) `backfill_token_costs.py` gains a generations-per-JIT-record threshold gate (default 0.5/record); exit 10 when any non-static run's ratio is below threshold; `--allow-under-coverage` + `--min-gens-per-jit-record` CLI flags; per-condition display rewritten from binary "runs-with-≥1" to quantitative "gens / JIT records = ratio/record" with `⚠` marker on under-covered conditions.
   - (#131) `analyze_results.py` ext7 #52 gate extended with per-condition `COUNT(DISTINCT task_id)` check (must equal `expected_tasks`) and per-`(condition, task_id)` uniformity check (each must have exactly `expected_runs` rows). Inherits ext7 #52's exit 2 / `--allow-incomplete` contract.
   - (#132) `print_contamination_rate` returns False when any expected condition is missing from M2 labels; `main()` threads through ext13 #126's `missing_metrics` list; inherits exit 7 and `--allow-missing-metrics` override.
   - (#133) `analyze_context_utilization.py` gains per-agent-family presence gate; exit 4 when any of `{task-decomposer, adversarial-probe, failure-analyst, lead-agent}` has zero traces; `--allow-empty` override extended to cover this case with `[MISSING: <agent list>]` banner.
   - (#134) `test_registry_matching.py` gains minimum-audit-coverage threshold (default 80% of eligible rows must have non-None verdict); exit 2 when judge outage drops audit coverage below threshold; error-category breakdown printed to stderr for diagnosis.
   - (#135) `test_registry_fn.py` gains minimum-capability-coverage threshold (default 90%); exit 2 when paraphrase-generation failures skip >10% of capabilities; optional single-retry pass for transient failures before declaring failure.
   - (#136) `test_pipeline.py` stage 5 (verification) flips `all_pass=False` when `verification_results=[]` AND `artifacts` were generated; stage 6 (registration) flips `all_pass=False` when `gaps and artifacts and new_caps==0` (previously WARN only).
   - (#137) `audit_contamination.py` gains per-branch auto-skip counters (`auto_skip_no_invocation`, `auto_skip_seeded`, `auto_skip_no_source`); end-of-session loud-fail with exit 3 when `total_labeled == 0` listing the auto-skip breakdown and likely cause.
   - (#138) `analyze_tier2_probes.py` mirrors ext7 #47's pattern: loud-fail with exit 2 when `len(events) == 0`; `--allow-empty` CLI flag for explicit override.
5. End-of-audit note: "Extended 2026-04-21 (thirteenth extension) with findings #124-#138 (detail sections in `security_audit_2026_04_19_ext13.md`). All six P0 CRITICAL; all six silent-metric-degradation bugs that produce exit 0 with incomplete Chapter 6 output. #125 is the upstream source (longitudinal cycle-flush never-raises contract); #124 + #127 are downstream Row 18/19 analyzer consumers; #126 + #128 are downstream Row 17 M3 consumers; #129 is a Row 11 backfill consumer. #127 / #128 / #129 are the tenth / eleventh / twelfth fix-induced regressions in the series (against ext12 #118 / ext13 #126 / ext12 #122 respectively); they establish the meta-pattern that **completeness gates need follow-up at finer granularity than their initial landing** — a pattern Chapter 4.5 should disclose as a methodology lesson. Pipeline-wide exit-code contract now spans 0-9 (parent through ext13). Three compounding failure chains (Rows 9/10 → 18/19; Rows 11 + 13 + 16 → 17; Rows 9/10 → 17) all reinforced by the new findings."

---

**End of extension.**
