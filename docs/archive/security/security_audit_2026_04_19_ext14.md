# Security / Robustness Audit — 2026-04-19, Fourteenth Extension (#139-#150)

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

**This extension's scope.** Seven findings across two analyzer families surfaced 2026-04-21 via reviewer-run direct reproduction:

LangFuse-analyzer family (#139-#141):
- **#139 — partial-session fail-open** (Row 13/15): both analyzers accept `--session-ids` but never verify each requested ID contributed ≥1 row. Finer granularity than ext13 #138.
- **#140 — silent pagination truncation (Row 13/15):** 20k ceiling with bare `break`, no warning, no return flag.
- **#141 — silent pagination truncation + missing server-side time filter (Row 11):** 50k ceiling AND request not time-scoped server-side; compounds with ext12 #122 which misattributes the cause to retention.

Run_id-scoping family (#142-#145):
- **#142 — Row 14 `analyze_compression_loss.py` fail-open on run_ids coverage:** typo/missing run_id silently drops; empty result → exit 0.
- **#143 — Row 16 `audit_contamination.py` fail-open on run_ids coverage:** selection-time empty pool → "Nothing to audit" → exit 0. Distinct from ext13 #137 (auto-skip-without-counter branch) and ext12 #121 (sample-plan persistence).
- **#144 — Row 17 M2 per-condition gate too coarse:** ext13 #132's gate passes if every condition has ≥1 label; per-run_id gaps within conditions slip through.
- **#145 — `run_sheet.md` CAMPAIGN_RUN_IDS discovery has no completeness filter:** exports every non-pilot run_id without `HAVING COUNT(*) >= N`, feeding partial/aborted run_ids into rows 14/16/17 silently. Contrast with ext11 #111's longitudinal selectors which were strengthened with GROUP BY.

**Status at time of write (2026-04-21):** all twelve **open**. Total open across audit series: ext8 (2) + ext10 (1 latent) + ext13 (15) + ext14 (12) = **~30 findings**.

**Status update 2026-04-21 (ext14 remediation sweep):** all twelve findings **✅ FIXED** in a single pass. Every finding carries a pinned `ext14 #N` comment at the fix site, every new exit code / CLI flag is documented in the affected script's docstring or header, and the full unit suite (1994 tests) + integration suite (32 tests) passes post-fix. Three pre-existing unit tests were updated to match the new contracts:

1. `tests/unit/test_analyze_context_utilization.py` — `fetch_traces` mock return-value updated from `[]` to `([], set(), False)` to match the `(traces, contributed_sessions, truncated)` signature introduced by ext14 #139 / #140.
2. `tests/unit/test_security_audit_ext13_e2e.py` — `fetch_tier2_events` mock updated analogously to `([], set(), False)` per ext14 #139 / #140.
3. `tests/unit/test_run_sheet.py::test_scoping_convention_uses_dynamic_discovery_not_fixed_literal` — accepts both the pre-fix `SELECT DISTINCT run_id` form and the post-#145 `GROUP BY run_id HAVING COUNT(*) >= N` form so the completeness filter doesn't break the dynamic-discovery assertion; added a new `HAVING COUNT(` sub-assertion that enforces the #145 completeness filter is present.
4. `tests/unit/test_security_audit_ext12_e2e.py::test_analyze_longitudinal_exits_5_on_shift_db_without_marker` — gained `--allow-sparse-signal` so the test stays narrowly focused on ext12 #123's shift-marker gate rather than the sparse-signal gate that now fires correctly post-ext14 #150. Pre-fix the gate read `cumulative_invocations` which carried cycle-1's single event forward for 40 cycles, letting the test pass through to ext12 #123's exit 5. Post-fix the gate correctly rejects 1/40 sparse coverage at exit 8; the test explicitly opts out with `--allow-sparse-signal` so its narrow focus survives.

| # | Severity | Scope | Impact | Status |
|---|---|---|---|---|
| 139 | **P0 CRITICAL** | `analyze_context_utilization.py:114` + `analyze_tier2_probes.py:198-206` filter non-matching sessions silently; no per-session-id presence gate in `main()` (lines 607, 657, 471, 479) | Row 13 / Row 15 aggregate output from a subset of requested sessions when one typo / retention-aged / partial-ingest session contributes zero rows. Exit 0. Chapter 6 Context Economy + Tier 2 probe tables silently undersample without surface signal | ✅ FIXED |
| 140 | **P0 CRITICAL** | `analyze_context_utilization.py:130-131` + `analyze_tier2_probes.py:211-212` hard-break at `page > 200`; no warning | Silent pagination truncation at 20k rows (100 records/page × 200 pages). Large campaigns exceeding this ceiling silently drop every row past the cutoff. Direction of bias: biases toward whatever the LangFuse API returns first (typically newest), under-representing older history in Chapter 6 aggregates | ✅ FIXED |
| 141 | **P0 CRITICAL** | `scripts/backfill_token_costs.py:107-108` hard-break at `page > 500` with no warning; line 86-88 client-side time filters AFTER pagination | Row 11 silently truncates LangFuse generation observations at 50k rows. Compounds: the fetch is NOT time-scoped server-side (`params` at line 79 has no `fromStartTime`/`toStartTime`); unrelated observations in the same window consume the page budget before relevant ones are reached. Large or busy LangFuse instances silently truncate M4 generations → Chapter 6 per-task cost biased low | ✅ FIXED |
| 142 | **P0** | `scripts/analyze_compression_loss.py:232-236` applies `--run-ids` as SQL filter only, no requested-vs-contributed check; line 507-509 exits 0 on empty result | Row 14 silently succeeds with subset / empty T3 aggressive-compression evidence. A typo in one run_id drops it from the filter silently; empty result returns exit 0 with "No failure rows — nothing to classify." Chapter 6 compression-loss table looks complete. | ✅ FIXED |
| 143 | **P0** | `scripts/audit_contamination.py:143-147` applies `--run-ids` as SQL filter only, no requested-vs-contributed check; line 635-637 exits 0 on empty sample | Row 16 silently succeeds with subset / empty M2 sample. Distinct from ext13 #137 (auto-skip branches at line 667-680 lose rows without counter) — this is the selection-time empty-pool path. Partial/typo run_id set silently under-samples M2 or skips entirely. | ✅ FIXED |
| 144 | **P0** | `scripts/analyze_results.py:1360-1375` ext13 #132's M2 completeness gate is per-condition only; no per-(condition, run_id) check | Row 17 M2 passes if every condition has ≥1 label anywhere, even when individual run_ids within each condition lack labels entirely. Row 16 can produce partial within-condition labeling (some run_ids covered, others dropped via #143) that silently passes ext13 #132's "per-condition present" gate at Row 17. | ✅ FIXED |
| 145 | **P0** | `docs/run_sheet.md:77-85` CAMPAIGN_RUN_IDS discovery SQL has no completeness filter | `SELECT DISTINCT run_id FROM evaluation_runs WHERE NOT is_pilot ORDER BY run_id` exports every non-pilot run_id with no `HAVING COUNT(*)` gate (unlike ext11 #111's longitudinal selector). Partial / aborted campaign run_ids silently enter `$CAMPAIGN_RUN_IDS` and feed rows 14/16/17 as if they were complete. | ✅ FIXED |
| 146 | **P0 CRITICAL (pipeline-breaker)** | M2 contract is structurally unsatisfiable with default Row 16 flow | `analyze_results.py:32` + line 1365 include `"static"` in `CONDITIONS`. `audit_contamination.py:74` defaults to JIT-only `_JIT_CONDITIONS`; line 140 filters rows with `generated_artifact_count > 0`. `harness.py:1323, 1326` sets `generated_artifact_count=0` for static-path rows. Static is unlabelable by default → Row 17 ext13 #132 gate returns exit 7 on `M2 partial coverage ['static']` on EVERY canonical campaign | ✅ FIXED |
| 147 | **P0** | `analyze_results.py::print_gini_summary` M3 per-run coverage gate absent | `analyze_results.py:857` fetches per-(condition, run_id) invocation rows; line 942-952 iterates CONDITIONS and prints mean/aggregate; line 967-974 ext13 #128 checks missing conditions but NOT missing run_ids within conditions. Complete `evaluation_runs` shape (2+ runs/condition) with invocations for only 1 run/condition silently passes; Chapter 6 M3 mean computed over `N runs = 1 per condition` while the gate reports success | ✅ FIXED |
| 148 | HIGH (latent) | Harness + `run_pilot.py` ignore `FlushResult.all_ok` signal from `cycle_flush.py` | `cycle_flush.py:55-62` returns `FlushResult` per ext13 #125; `harness.py:451-460` logs WARNING on `not fr.all_ok` but continues without setting a sticky flag; `run_pilot.py:442-449` only gates on `watchdog_aborted`. Row 8 can exit 0 with partial telemetry flush failures → downstream M3 biased, #147 gate slips, exit-code layer says success. ext13 #125's fix landed for longitudinal runners but the harness path was not wired to propagate `FlushResult` | ✅ FIXED |
| 149 | **P0 CRITICAL** | Row 17 has no M4 completeness gate; silent all-zero `token_cost` campaigns pass with exit 0 | `analyze_results.py:717` `print_token_cost_summary` prints only with no return-value; main flow at line 1580-1590 tracks only M3 + M2 in `missing_metrics`. `evaluation_store.py:34` `token_cost INTEGER NOT NULL DEFAULT 0`; `harness.py:1356` writes `token_cost=0` at row creation (backfilled by Row 11 later). A campaign where Row 11 was skipped / zeroed by #141 / #142 / #145 silent-data-loss chains produces 400 rows of `token_cost=0`; Row 17 prints M4 zeros and exits 0 without surfacing the absence | ✅ FIXED |
| 150 | **P0 CRITICAL** | ext13 #127 sparse-signal gate bypassed by `cumulative_invocations` carry-forward | `analyze_longitudinal.py:797-804` ext13 #127 gate includes `cumulative_invocations` in the per-cycle signal check. Line 390-398 shows cumulative is a running snapshot: once any invocation lands in any cycle, the cumulative dict stays non-empty for every subsequent cycle's snapshot copy. A 40-cycle DB with one cycle-1 invocation shows `cycles_with_signal=40/40` and passes #127's `min_cycle_coverage` threshold despite having trivially-sparse real per-cycle data. Reviewer-reproduced: "one invocation event in cycle 1, no utility/events → analyzer reported Cycles-to-convergence: 2, exited 0 (with --allow-proxy-kl)." | ✅ FIXED |

---

## Issue #139 — Row 13 / Row 15 analyzers accept `--session-ids` but never verify every requested session contributed data; partial-session runs exit 0 (P0 CRITICAL)

### Summary

Both LangFuse analyzers accept a comma-separated `--session-ids` CLI flag that scopes their fetch. Inside the fetcher, non-matching rows are silently dropped:

**`analyze_context_utilization.py:114`:**
```python
for t in data:
    ts = _parse_dt(t.get("timestamp"))
    if ts and start <= ts <= end:
        if session_ids and t.get("sessionId") not in session_ids:
            continue
        traces.append({...})
```

**`analyze_tier2_probes.py:198-206`:**
```python
for obs in data:
    ts = _parse_dt(obs.get("startTime") or obs.get("timestamp"))
    if ts and start <= ts <= end:
        if session_ids:
            sid = (obs.get("trace") or {}).get("sessionId") or obs.get("sessionId")
            if sid not in session_ids:
                continue
        events.append(obs)
```

Neither fetcher tracks which of the N requested session IDs actually produced ≥1 row. The main functions at `analyze_context_utilization.py:607, 657` and `analyze_tier2_probes.py:471, 479` check only the GLOBAL count:

- ext7 #47 (for Row 13) + ext13 #138 (for Row 15) loud-fail when `total_events == 0`.
- ext12 #120 filters degraded traces and loud-fails when all-agents-all-degraded.
- ext13 #133 checks per-agent-family presence (for Row 13).

None of those gates catch the "4 session IDs requested, 3 contributed rows, 1 contributed zero" case. As long as at least one session produced data and at least one agent family has rows, the gates pass and the analyzer proceeds with aggregates computed from the surviving subset.

Reviewer's observation: "If only some sessions are present (retention lag, typo in one id, partial ingestion), they still exit 0 and report aggregates from a subset."

This is the same finer-granularity-gate-slip pattern as ext13 #127 / #128 / #129 / #130 — the existing gate catches "all empty" or "any empty" but not "expected-vs-actual presence per requested key."

### Where

- **Silent filter-drop (Row 13):** `scripts/analyze_context_utilization.py:114`.
- **Silent filter-drop (Row 15):** `scripts/analyze_tier2_probes.py:198-206`.
- **Global-count gate only (Row 13):** `scripts/analyze_context_utilization.py:607` (ext7 #47) + line 657 unconditional `return 0`.
- **Global-count gate only (Row 15):** `scripts/analyze_tier2_probes.py:471` (ext13 #138) + line 479 unconditional `return 0`.

### Reproduction

```bash
# Four session IDs requested; only three exist in LangFuse (one typo).
$ uv run scripts/analyze_context_utilization.py \
    --session-ids 'sess_a,sess_b,sess_c,sess_TYPO'

# Fetcher silently drops sess_TYPO's (empty) contribution.
# ext7 #47 gate: total_traces > 0 (from sess_a/b/c) → passes.
# ext12 #120 gate: some agents have valid traces → passes.
# ext13 #133 gate: per-agent-family presence → passes if all agents have
#                  traces from ANY of the three real sessions.

Fetched N traces.
...  (aggregate output over 3 sessions, not 4)
$ echo $?
0
```

Reviewer-reproduced. Analogous for `analyze_tier2_probes.py`.

### Threat model / impact

- **Chapter 6 Context Economy + Tier 2 probe aggregates silently undersample.** A user running the run sheet with `$CAMPAIGN_SESSION_IDS` (per ext7 #45's scoping convention) has no surface signal when one session silently produces zero rows.
- **Direction of bias: depends on which session(s) go missing.** If the missing session is the "worst" (most contaminated / highest-context-pressure) session, Chapter 6 under-reports; if the "best" goes missing, over-reports. Unpredictable but never signaled.
- **Compounds with #140 below.** Silent pagination truncation PLUS silent partial-session acceptance = two orthogonal data-loss surfaces on the same two analyzers.
- **Compounds with ext12 #120 + ext13 #133 + ext13 #138.** All three added gates to these analyzers at different granularities (all-degraded, per-agent-presence, zero-events-globally). None of them catch per-requested-session-id presence.
- **Fix-induced against ext7 #42 + ext7 #54** (session-id scoping fixes). Those made scoping possible; they did not add a "requested-vs-contributed" verifier.

### Remediation

**A. Track contributed session IDs at fetch time and verify against requested set.**

```python
# scripts/analyze_context_utilization.py::fetch_traces — replace the session_ids
# check with tracked-set accumulation:
def fetch_traces(client, start, end, *, name, environment, session_ids):
    traces = []
    contributed_sessions: set[str] = set()
    requested_sessions = set(session_ids) if session_ids else None

    page = 1
    while True:
        ...
        for t in data:
            ts = _parse_dt(t.get("timestamp"))
            if ts and start <= ts <= end:
                sid = t.get("sessionId")
                if requested_sessions is not None and sid not in requested_sessions:
                    continue
                if sid is not None:
                    contributed_sessions.add(sid)
                traces.append({..., "session_id": sid, ...})
        ...
    return traces, contributed_sessions  # signature change
```

**B. Loud-fail in `main()` when any requested session contributed zero rows.**

```python
# scripts/analyze_context_utilization.py::main — after the per-agent fetch loop:
traces_per_agent, contributed_per_agent = {}, {}
for agent_name in agent_names:
    traces, contributed = fetch_traces(
        client, start, end, name=agent_name,
        environment=args.environment, session_ids=args.session_ids,
    )
    traces_per_agent[agent_name] = traces
    contributed_per_agent[agent_name] = contributed

if args.session_ids:
    requested = set(args.session_ids)
    all_contributed = set().union(*contributed_per_agent.values())
    missing = requested - all_contributed
    if missing:
        print(
            f"\nERROR: {len(missing)}/{len(requested)} requested session IDs "
            f"contributed zero rows: {sorted(missing)}\n"
            f"  Likely causes: (a) typo in --session-ids, (b) LangFuse retention "
            f"aged out the session's traces, (c) partial ingestion mid-campaign.\n"
            f"  Pass --allow-partial-sessions to proceed with the surviving "
            f"subset (Chapter 4.5 disclosure REQUIRED). Exit 4.",
            file=sys.stderr,
        )
        if not args.allow_partial_sessions:
            return 4
```

Exit code 4 (mirrors ext13 #133's exit-4 contract for the sibling Row-13 per-agent case). Same block replicated in `analyze_tier2_probes.py` with per-event accumulation instead of per-agent.

**C. CLI flag.**

```python
parser.add_argument(
    "--allow-partial-sessions", action="store_true",
    help="Proceed when some --session-ids contributed zero rows. Default: "
         "refuse with exit 4. Chapter 4.5 disclosure REQUIRED. ext14 #139.",
)
```

**D. In-band banner when override is used** (mirror ext13 #127 / #133 pattern):

```python
if missing and args.allow_partial_sessions:
    _SESSION_BANNER = f"[PARTIAL SESSIONS: {len(requested - missing)}/{len(requested)} contributed]"
    # Prefix every section header with _SESSION_BANNER.
```

**E. Regression test.**

```python
def test_partial_session_coverage_refused(monkeypatch):
    # Mock fetch_traces to return data for only 3 of 4 requested sessions.
    monkeypatch.setattr(
        "analyze_context_utilization.fetch_traces",
        lambda *a, **kw: ([trace for trace in _stub_traces if trace["session_id"] in {"a", "b", "c"}],
                          {"a", "b", "c"}),
    )
    rc = analyze_context_utilization.main(["--session-ids", "a,b,c,d"])
    assert rc == 4

def test_partial_session_with_allow_flag_emits_banner(...):
    ...
```

Same test pattern for `analyze_tier2_probes.py`.

### Recommendation

**P0 — blocks Row 13 + Row 15 → Chapter 6 trustworthiness when `$CAMPAIGN_SESSION_IDS` has any typo / missing session.** ~40 LOC per analyzer (signature change + tracked-set accumulation + gate + CLI flag + banner) + 2 regression tests per analyzer = ~100 LOC + 4 tests. ~1.5h. Must ship alongside ext13 #133 / #138 as the per-session-id completion of the same gate family.

### Legacy data

Any prior Row 13 / Row 15 output where `$CAMPAIGN_SESSION_IDS` had a silent missing session is undersampled. Re-run post-fix after verifying the session list matches LangFuse. No data migration needed.

---

## Issue #140 — `analyze_context_utilization.py` + `analyze_tier2_probes.py` silently truncate LangFuse pagination at 20k rows; data loss with no warning (P0 CRITICAL)

### Summary

Both analyzers paginate LangFuse API responses with 100-record pages. Both enforce a hard-stop at `page > 200`:

**`analyze_context_utilization.py:127-131`:**
```python
if len(data) < 100:
    break
page += 1
if page > 200:
    break
```

**`analyze_tier2_probes.py:208-212`:**
```python
if len(data) < 100:
    break
page += 1
if page > 200:
    break
```

Both use a bare `break` at the ceiling — no warning log, no error, no flag set on the return value. The fetcher returns whatever accumulated in the first 200 pages (20,000 records maximum). Any data beyond that is silently dropped.

Downstream consumers have no way to know truncation occurred. The final summary print in both analyzers shows a count (e.g., "Fetched 20000 events") that looks normal; a reviewer would need to notice the count equals exactly 20000 AND check whether the true event count for the scoped window might exceed that AND know the analyzer's ceiling is 20000 to infer truncation. All three conditions are unlikely to coincide during a routine review.

Pagination ceilings like this are defensive (prevent runaway fetches on misconfigured filters), but they need to surface the truncation so the caller can react. Current code treats the ceiling as a hard contract and hides the contract breach.

### Where

- **Row 13 truncation:** `scripts/analyze_context_utilization.py:130-131`.
- **Row 15 truncation:** `scripts/analyze_tier2_probes.py:211-212`.
- **No truncation signal to caller:** both functions return `traces` / `events` with no `truncated: bool` companion.
- **No caller-side check:** `main()` in both scripts prints the count and proceeds.

### Reproduction

**Synthetic-side reproduction** (direct replay of the ceiling logic):

```python
# Simulate a LangFuse endpoint that has 25,000 matching records.
# fetch_traces reads pages 1..200 (100 records each) → returns 20,000 records.
# Pages 201..250 are silently dropped.

$ uv run scripts/analyze_context_utilization.py --days 365
Fetched 20000 traces.        ← no warning, no truncation indicator
...
$ echo $?
0

# Chapter 6 aggregates computed on 20k of 25k records.
# Direction of bias: LangFuse default ordering is typically newest-first, so
# older history gets silently dropped.
```

### Threat model / impact

- **Chapter 6 Context Economy + Tier 2 probe aggregates silently undersample.** A wide `--days` window + a healthy campaign can exceed 20k events easily (Tier 2 probes fire per-capability per-variant across 400 evaluations × multiple probes = 4-8k events; 40 longitudinal cycles × 20 tasks × 5 generators = 4k+ context-utilization traces; a multi-month campaign compounds).
- **Direction of bias: pro-recent.** LangFuse API default sort is newest-first. Truncation drops the oldest records first. Chapter 6's window-averaged metrics therefore over-represent recent data relative to the declared `--days` window.
- **Compounds with ext13 #133 + ext14 #139.** #133 gates per-agent presence (false when a family has zero rows POST-truncation but had rows PRE-truncation if those fell past page 200). #139 gates per-session presence (same hazard). Neither gate flags truncation as the cause of a "missing" family/session.
- **Compounds with ext7 #47 / ext13 #138 zero-event gates.** Those fire only when total is zero; non-zero-but-truncated passes cleanly.
- **Silent over time.** As the campaign DB grows, the bug's exposure grows. A Phase 5 eval that fits cleanly in 20k events today might silently truncate after 18 months of longitudinal runs.

### Remediation

**A. Surface truncation at the fetcher return boundary.**

```python
# scripts/analyze_context_utilization.py::fetch_traces:
def fetch_traces(client, start, end, *, name, environment, session_ids,
                 max_pages: int = 200) -> tuple[list[dict], bool]:
    traces = []
    contributed_sessions: set[str] = set()
    truncated = False

    page = 1
    while True:
        ...
        if len(data) < 100:
            break
        page += 1
        if page > max_pages:
            truncated = True
            break
    return traces, contributed_sessions, truncated  # signature change
```

(Combine with #139's `contributed_sessions` return.)

**B. Loud-fail or warn-and-continue in `main()`.**

```python
# Option B.1 — loud-fail unless override:
if any_truncated:
    print(
        f"\nERROR: fetcher hit the {args.max_pages}-page ceiling "
        f"(~{args.max_pages * 100:,} records). Data beyond the ceiling "
        f"was silently dropped. Options:\n"
        f"  (a) narrow --days / --start / --end window\n"
        f"  (b) narrow --session-ids to the specific campaign\n"
        f"  (c) raise --max-pages (tradeoff: longer fetch time + higher "
        f"LangFuse API cost)\n"
        f"  (d) pass --allow-truncated to proceed with undersampled aggregates "
        f"(Chapter 4.5 disclosure REQUIRED).",
        file=sys.stderr,
    )
    if not args.allow_truncated:
        return 5  # new exit code: pagination truncation

# Option B.2 — in-band banner when override is used:
_TRUNCATION_BANNER = f"[TRUNCATED: {args.max_pages * 100:,}+ records]"
```

Exit code 5 distinct from #133's exit 4 (missing agents) and #139's exit 4 (missing sessions) and ext13 #138's exit 2 (zero events). Each failure class has its own code per the ext12/ext13 exit-code contract.

**C. CLI flags.**

```python
parser.add_argument(
    "--max-pages", type=int, default=200,
    help="LangFuse pagination ceiling (default: 200 = 20k records at 100/page). "
         "Raise for larger campaigns; expect longer fetch and higher API cost.",
)
parser.add_argument(
    "--allow-truncated", action="store_true",
    help="Proceed when the pagination ceiling was hit. Default: refuse with "
         "exit 5. Chapter 4.5 disclosure REQUIRED. ext14 #140.",
)
```

**D. Log a WARNING at truncation boundary (not just the end-of-main loud-fail).**

```python
# Inside the fetcher, at the ceiling break:
if page > max_pages:
    logger.warning(
        "Pagination ceiling hit at page %d (max_pages=%d, ~%d records fetched). "
        "Data beyond this ceiling was silently dropped. See ext14 #140.",
        page, max_pages, len(traces),
    )
    truncated = True
    break
```

The log line is the first early signal; the end-of-main gate is the authoritative stop.

**E. Regression test.**

```python
def test_fetcher_returns_truncated_flag_at_ceiling(monkeypatch):
    # Mock httpx client to return a full 100-row page for 201 pages.
    page_counter = [0]
    def fake_get(*a, **kw):
        page_counter[0] += 1
        return _make_response([_stub_row()] * 100)
    monkeypatch.setattr(..., fake_get)

    traces, contributed, truncated = fetch_traces(
        client, start, end, name="x", environment="default",
        session_ids=None, max_pages=200,
    )
    assert truncated is True
    assert len(traces) == 20_000

def test_main_refuses_truncated_fetch_without_override(...):
    rc = analyze_context_utilization.main(["--days", "365"])
    assert rc == 5

def test_allow_truncated_exits_0_with_banner(...):
    rc, stdout = analyze_context_utilization.main(["--days", "365", "--allow-truncated"])
    assert rc == 0
    for section in _extract_section_headers(stdout):
        assert "[TRUNCATED" in section
```

Same pattern in `analyze_tier2_probes.py`.

### Recommendation

**P0 — blocks Row 13 + Row 15 trustworthiness on any campaign approaching or exceeding the 20k-record ceiling.** ~40 LOC per analyzer (signature change + truncated flag + WARNING log + main-side gate + CLI flags + banner) + 3 regression tests per analyzer = ~100 LOC + 6 tests. ~1.5h.

### Legacy data

Any prior Row 13 / Row 15 output on a window that exceeded 20k records silently undersampled. Post-fix, re-run with a narrower window or `--max-pages` raise. The ceiling hit is silent today but will be flagged in future runs.

---

## Issue #141 — `backfill_token_costs.py` silently truncates LangFuse generation pagination at 50k rows; client-side time filter compounds the hazard (P0 CRITICAL)

### Summary

`scripts/backfill_token_costs.py::fetch_all_generations` paginates LangFuse's `/api/public/observations` endpoint with 100-record pages. It enforces a hard cap at `page > 500` with a bare `break`:

```python
# scripts/backfill_token_costs.py:104-108
if len(data) < 100:
    break
page += 1
if page > 500:  # safety limit
    break
```

The ceiling is 500 pages × 100 records = **50,000 observations** (5× the ext14 #140 ceiling for the Row 13/15 analyzers, but 5× a larger haystack because `backfill_token_costs.py` fetches ALL generations across all time, not just the campaign window). No warning log, no return-value flag, no signal to the caller. Line 109 `return generations` returns whatever accumulated.

Compounding hazard — the request is NOT time-scoped server-side. Line 77-80:

```python
resp = client.get(
    "/api/public/observations",
    params={"type": "GENERATION", "limit": 100, "page": page},
)
```

No `fromStartTime` / `toStartTime` parameter. The LangFuse API returns observations in default order (typically newest-first across the entire LangFuse project, not just the eval campaign). The client-side time filter at line 86-88:

```python
for obs in data:
    ts = _parse_dt(obs.get("startTime"))
    if ts and start <= ts <= end:
        ...
```

discards out-of-window rows AFTER they've already consumed a page slot. On a LangFuse instance that also hosts other projects, CLI-invocation traces, longitudinal runs, or prior campaigns, the 50,000-observation budget can be burned on unrelated data before any in-window generations are reached.

Downstream consumers (`map_generations_to_runs` at line 240 and subsequent aggregation) have no way to know truncation occurred. The `Per-condition coverage` printout from ext12 #122 + ext13 #130 shows per-run generation counts, but those are counts of what WAS fetched — a run whose generations all fell past the 50k ceiling shows as "0 generations matched" and fires ext12 #122's loud-fail BUT misattributes the root cause ("LangFuse retention aged out" per the existing error text). The real cause ("pagination ceiling truncated the fetch") is never surfaced.

Direction of bias: pro-thesis cost story (M4 per-task cost biased low when truncation drops a subset of generations). Worst direction for Chapter 6 Pillar 3 Context Economy claims.

### Where

- **Pagination hard-break:** `scripts/backfill_token_costs.py:107-108`.
- **Client-side-only time filter:** `scripts/backfill_token_costs.py:86-88`.
- **Unfiltered server-side fetch:** `scripts/backfill_token_costs.py:77-80` — `params` has no time-range key.
- **No truncation signal returned:** `scripts/backfill_token_costs.py:109` returns bare list.
- **Caller has no hook to detect truncation:** `scripts/backfill_token_costs.py::main` calls `fetch_all_generations` and immediately maps-to-runs; no `truncated` or `page_count` output available.

### Reproduction

**Synthetic-side reproduction:**

```bash
# LangFuse instance with 60,000 generation observations total
# (could be: prior campaign + current campaign + CLI traces + unrelated projects).
# Current eval campaign window covers ~5,000 of those observations.

$ uv run scripts/backfill_token_costs.py --db data/eval/metrics.duckdb
Fetched 50000 GENERATION observations across 500 pages (ceiling hit, not flagged).
# Of those 50000, only 3,200 fall within the current campaign's window.
# The other 1,800 campaign observations were in pages 501-600 — silently dropped.

# ext12 #122 gate: some runs have generations → passes.
# ext13 #130 gate: per-run gen/JIT-record ratio → runs that lost all their
#                  generations past the ceiling fire as "0 generations", but
#                  the error message blames retention, not truncation.

Mapped to M runs, X unmatched generations.
  Per-condition coverage (runs with >=1 matched LangFuse generation):
    full               4/5                                    ← 1 run lost to truncation, blamed on retention
...
```

### Threat model / impact

- **Row 11 → Row 17 M4 silently under-reports** on any campaign where the LangFuse project has accumulated history exceeding the 50k ceiling. Phase 5 eval alone generates ~50+ generations per task × 400 tasks = 20k+ campaign observations; add CLI-invocation traces + prior campaigns + longitudinal runs and a production LangFuse instance easily exceeds 50k.
- **ext12 #122 gate fires but misattributes.** The loud-fail at line 798-822 says "LangFuse retention aged out" when the real cause is pagination truncation. A user attempting the #122 remediation (narrow window, verify retention) won't find the problem.
- **Compounds with #140 (Row 13/15 pagination).** Same class of silent-data-loss bug on a sibling analyzer family. Fix pattern should be uniform across all three.
- **Compounds with ext13 #129 + ext13 #130.** Per-task tag coverage + quantitative coverage gates can't distinguish truncation from genuine absence. Both fire correctly for genuine absence; both fire spuriously for truncation-induced absence.
- **Direction of bias: anti-JIT cost / pro-thesis.** Dropped generations reduce the reported total token cost per run → per-task cost lower → JIT-vs-static cost comparison looks more favorable to JIT than reality. Thesis-favorable silent manufacturing.
- **LangFuse default pagination ordering is not contractually documented.** Server-side may change order across versions; truncation direction may flip between "oldest dropped" and "newest dropped" without notice.

### Remediation

**A. Server-side time scoping.**

LangFuse API supports `fromStartTime` and `toStartTime` query parameters. Apply them:

```python
# scripts/backfill_token_costs.py:77-80 — before:
resp = client.get(
    "/api/public/observations",
    params={"type": "GENERATION", "limit": 100, "page": page},
)

# after:
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
```

Drops the client-side filter redundancy and ensures the 50k-page budget is spent on in-window observations only. Reduces false-positive truncation by whatever fraction of the LangFuse haystack is out-of-window (typically >90% on a long-lived instance).

**B. Surface truncation at the fetcher boundary.**

```python
def fetch_all_generations(
    base_url, auth, start, end, *, max_pages: int = 500,
) -> tuple[list[dict], bool]:
    generations = []
    truncated = False
    page = 1
    with httpx.Client(base_url=base_url, auth=auth, timeout=30.0) as client:
        while True:
            resp = client.get(...)
            ...
            if len(data) < 100:
                break
            page += 1
            if page > max_pages:
                logger.warning(
                    "fetch_all_generations hit pagination ceiling at page %d "
                    "(max_pages=%d, ~%d observations fetched). Data beyond the "
                    "ceiling was silently dropped. See ext14 #141.",
                    page, max_pages, len(generations),
                )
                truncated = True
                break
    return generations, truncated
```

**C. Loud-fail or warn-and-continue in `main()`.**

```python
# scripts/backfill_token_costs.py::main — after fetch:
generations, truncated = fetch_all_generations(
    base_url, auth, window_start, window_end,
    max_pages=args.max_pages,
)

if truncated:
    print(
        f"\nERROR: fetch_all_generations hit the {args.max_pages}-page ceiling "
        f"(~{args.max_pages * 100:,} observations). Data beyond the ceiling was "
        f"silently dropped. This can produce spurious 'zero generations for run X' "
        f"errors (ext12 #122) and per-task under-coverage (ext13 #130) when the "
        f"real cause is truncation, NOT retention.\n"
        f"  Options:\n"
        f"    (a) narrow the eval window (--since / shorter campaign)\n"
        f"    (b) raise --max-pages (tradeoff: longer fetch + higher API cost)\n"
        f"    (c) verify server-side time filter is applied (fromStartTime/toStartTime)\n"
        f"    (d) pass --allow-truncated to proceed with undersampled M4\n"
        f"       (Chapter 4.5 disclosure REQUIRED).",
        file=sys.stderr,
    )
    if not args.allow_truncated:
        conn.close()
        return 5  # reuses ext14 #140's pagination-truncation exit code
```

**D. CLI flags.**

```python
parser.add_argument(
    "--max-pages", type=int, default=500,
    help="LangFuse pagination ceiling (default: 500 = 50k observations at 100/page). "
         "Raise for larger campaigns; expect longer fetch and higher API cost.",
)
parser.add_argument(
    "--allow-truncated", action="store_true",
    help="Proceed with Row 11 backfill when the pagination ceiling was hit. "
         "Default: refuse with exit 5. Chapter 4.5 disclosure REQUIRED. ext14 #141.",
)
```

**E. In-band banner when override is used.**

```python
# Propagate to the per-condition coverage display and the final summary:
if truncated and args.allow_truncated:
    print(f"\n  ⚠ [TRUNCATED: {args.max_pages * 100:,}+ observations] — M4 numbers "
          f"under-report. Chapter 4.5 disclosure REQUIRED.\n")
```

**F. Regression test.**

```python
def test_fetch_all_generations_returns_truncated_flag_at_ceiling(monkeypatch):
    page_counter = [0]
    def fake_get(*a, **kw):
        page_counter[0] += 1
        return _make_response([_stub_generation()] * 100)
    monkeypatch.setattr("httpx.Client.get", fake_get)

    generations, truncated = fetch_all_generations(
        "http://lf", _auth, _start, _end, max_pages=500,
    )
    assert truncated is True
    assert len(generations) == 50_000

def test_main_refuses_truncated_fetch_without_override(monkeypatch):
    monkeypatch.setattr(
        "scripts.backfill_token_costs.fetch_all_generations",
        lambda *a, **kw: ([{"id": "o1", ...}], True),
    )
    rc = backfill_token_costs.main(["--db", str(_db)])
    assert rc == 5

def test_allow_truncated_exits_0_with_banner(monkeypatch, capsys):
    monkeypatch.setattr(...)
    rc = backfill_token_costs.main(["--db", str(_db), "--allow-truncated"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "[TRUNCATED" in captured.out
```

**G. Verify server-side time filter is supported.**

Before landing (A), spot-check that the LangFuse version in use actually honors `fromStartTime` / `toStartTime`. If the deployed version doesn't, fall back to client-side-only with a widened ceiling and more explicit truncation signaling.

### Recommendation

**P0 — blocks Row 11 → Row 17 M4 trustworthiness on any campaign sharing a LangFuse instance with other traffic.** ~50 LOC (fetcher signature change + WARNING log + server-side time filter + main() gate + CLI flags + banner) + 3 regression tests. ~1h. Ship as a natural companion to ext14 #140 (same pattern, same exit code 5).

### Legacy data

Any prior Row 11 output on a LangFuse instance with >50k observations silently under-sampled generations. Post-fix options:
- (a) Re-run with server-side time filter + narrowed window.
- (b) Raise `--max-pages` and re-run.
- (c) Disclose in Chapter 4.5 with the `--allow-truncated` override.

(a) is cleanest; (c) is the escape hatch.

---

## Issue #142 — `analyze_compression_loss.py` fail-open on `--run-ids` coverage; typo/missing run_id silently drops from Row 14 T3 evidence (P0)

### Summary

`scripts/analyze_compression_loss.py` is Row 14 — the Tier 3 aggressive-compression evidence analyzer. It accepts `--run-ids` per ext6 #40's scoping fix. The fix applies run_ids as a SQL `IN (...)` filter at line 232-236:

```python
if run_ids:
    clauses.append(
        "run_id IN ({})".format(", ".join("?" * len(run_ids)))
    )
    params.extend(run_ids)
```

No verification that every requested `run_id` produced ≥1 row. A typo in one run_id drops that run from the filter silently (no match → no rows, no error). If the filtered result set is empty (all requested run_ids had typos, or none of the requested runs had failure rows), line 504-509:

```python
print(f"Loaded {len(rows)} failure rows from {db_path}")
...
if not rows:
    print("No failure rows found — nothing to classify.")
    return 0
```

Exit 0 with "nothing to classify." No distinction between "this campaign genuinely had no Tier 3 compression failures" (a legitimate result) and "I misconfigured the filter" (a silent misconfiguration).

Row 14 is the Pillar 2 Tier 3 aggressive-compression-detection evidence step. Silent empty output means Chapter 6's T3 claims have no supporting data.

### Where

- **run_ids applied as filter only:** `scripts/analyze_compression_loss.py:232-236`.
- **No requested-vs-contributed check:** same region — no accumulation of which run_ids actually appeared.
- **Empty-result exit 0:** `scripts/analyze_compression_loss.py:504-509`.

### Remediation

**A. Track contributed run_ids at fetch time; loud-fail on gap.**

```python
# After fetch:
present_run_ids = {r["run_id"] for r in rows}
if run_ids:
    requested = set(run_ids)
    missing = requested - present_run_ids
    if missing:
        print(
            f"\nERROR: {len(missing)}/{len(requested)} requested run_ids contributed "
            f"zero rows: {sorted(missing)}",
            file=sys.stderr,
        )
        if not args.allow_partial_runs:
            return 4  # reuses ext13 #133 / ext14 #139 per-entity-presence exit code
```

**B. Distinguish "no failures" from "filter-empty" at the empty-result branch.**

```python
if not rows:
    if run_ids:
        print(
            f"No failure rows for --run-ids {run_ids}. Either the filter "
            f"dropped all matching runs (typo?) or the campaign genuinely "
            f"had no Tier 3 failures. Pass --allow-empty to treat this as "
            f"success; otherwise exit 2.",
            file=sys.stderr,
        )
        if not args.allow_empty:
            return 2
    print("No failure rows found — nothing to classify.")
    return 0
```

**C. CLI flags + regression tests.** Mirror ext14 #139's pattern.

### Recommendation

**P0 — blocks Row 14 → Chapter 6 T3 evidence trustworthiness.** ~25 LOC + 2 regression tests. ~30 min.

---

## Issue #143 — `audit_contamination.py` fail-open on `--run-ids` coverage; empty sample after filter exits 0 with "Nothing to audit" (P0)

### Summary

`scripts/audit_contamination.py:143-147` applies `--run-ids` as SQL filter in `fetch_unlabeled_rows`:

```python
if run_ids:
    where_parts.append(
        "run_id IN ({})".format(", ".join("?" * len(run_ids)))
    )
    params.extend(run_ids)
```

Same first-order failure mode as #142: typo / missing run_id silently drops from the pool. Then at line 635-637:

```python
if not sample:
    print("\nNothing to audit — all strata empty or already labeled.")
    return 0
```

Empty sample → exit 0. No distinction between "every row in scope is already labeled" (a legitimate resume state) and "the filter dropped everything" (misconfiguration).

This is a DISTINCT finding from:
- **ext13 #137** — which addresses auto-skip branches at line 667-680 (`no invocation_events`, seeded, source=None); those `continue` without counter. #143 is the EARLIER branch at line 635 where the selection sample is empty BEFORE the labeling loop begins.
- **ext12 #121** — which addresses sample-plan persistence cross-scope leaks; #143 fires when the scope resolves to an empty pool FIRST, before any plan exists.

### Where

- **run_ids applied as filter only:** `scripts/audit_contamination.py:143-147`.
- **No requested-vs-contributed check:** same region.
- **Empty-sample exit 0:** `scripts/audit_contamination.py:635-637`.

### Remediation

**A. Track contributed run_ids at pool-fetch time; loud-fail on gap.**

```python
# In fetch_unlabeled_rows, after query:
present_run_ids = {row.run_id for row in rows}
# Return (rows, present_run_ids) or stash on a module var.

# In main, after fetch:
if run_ids:
    requested = set(run_ids)
    missing = requested - present_run_ids
    if missing:
        print(
            f"\nERROR: {len(missing)}/{len(requested)} requested run_ids contributed "
            f"zero unlabeled rows: {sorted(missing)}\n"
            f"  Possible causes: (a) typo in --run-ids, (b) all rows for these runs "
            f"were already labeled (check --resume), (c) run_ids don't exist in the DB.",
            file=sys.stderr,
        )
        if not args.allow_partial_runs:
            return 4
```

**B. Distinguish "all-already-labeled" from "filter-empty."**

```python
if not sample:
    if run_ids:
        # Check: is the scope's total row count > 0? If yes, "all already labeled"
        # is the real cause. If zero, "filter dropped everything" is likely.
        total_in_scope = conn.execute(
            "SELECT COUNT(*) FROM evaluation_runs "
            "WHERE run_id IN ({}) AND NOT is_pilot".format(...),
            run_ids,
        ).fetchone()[0]
        if total_in_scope == 0:
            print(f"\nERROR: --run-ids {run_ids} matched zero rows in DB.", file=sys.stderr)
            return 3
        print("\nNothing to audit — all in-scope rows already labeled.")
        return 0
    print("\nNothing to audit — all strata empty or already labeled.")
    return 0
```

### Recommendation

**P0 — blocks Row 16 → Row 17 M2 trustworthiness alongside ext13 #137.** ~30 LOC + 2 regression tests. ~30 min.

---

## Issue #144 — Row 17 M2 completeness gate protects per-condition but not per-run_id; partial within-condition M2 labeling silently passes (P0)

### Summary

ext13 #132 added a per-condition M2 completeness check at `scripts/analyze_results.py:1360-1375`:

```python
present_conditions = {r[0] for r in rows}
expected_conditions = set(CONDITIONS)
missing_conditions = expected_conditions - present_conditions
if missing_conditions:
    print(f"\n  ⚠ M2 partial coverage: labels missing for ...", file=sys.stderr)
    return False
```

The gate fires when an ENTIRE CONDITION has no labels. It does NOT fire when a condition has labels for some run_ids but not others. With the canonical campaign shape (4 conditions × 5 runs = 20 run_ids), a user who audits only 1 of 5 runs per condition still passes ext13 #132's gate — every condition has ≥1 labeled row.

Combined with #143 (`audit_contamination.py` fail-open on per-run_id coverage), the silent-data-loss chain is:
1. User passes `--run-ids $CAMPAIGN_RUN_IDS` to Row 16 with a typo; half the run_ids silently drop at the SQL filter.
2. Row 16 labels the surviving runs' samples; exits 0.
3. Row 17 checks per-condition presence → each condition still has ≥1 labeled row (from the surviving runs) → gate passes.
4. Chapter 6 M2 computed on half the campaign's run_ids; JIT-vs-static comparison sampled at 1/2 expected coverage.

### Where

- **Per-condition-only gate:** `scripts/analyze_results.py:1360-1375`.
- **No per-(condition, run_id) check:** same region.
- **Compounds with #143 at Row 16** + **ext13 #132's original completeness granularity.**

### Remediation

**A. Per-(condition, run_id) M2 completeness check.**

```python
# After the per-condition check:
# Query per-condition-per-run label counts:
per_cond_run = conn.execute(
    f"""
    SELECT condition, run_id, COUNT(*) AS n_labeled
    FROM evaluation_runs
    WHERE human_label IS NOT NULL AND NOT is_pilot{_SCOPE_SQL}
    GROUP BY condition, run_id
    """
).fetchall()

# Get expected run_ids per condition from scope:
expected_per_condition = conn.execute(
    f"""
    SELECT condition, COUNT(DISTINCT run_id)
    FROM evaluation_runs
    WHERE NOT is_pilot{_SCOPE_SQL}
    GROUP BY condition
    """
).fetchall()

expected_map = {cond: n for cond, n in expected_per_condition}
present_per_cond = {}
for cond, rid, n in per_cond_run:
    present_per_cond.setdefault(cond, set()).add(rid)

gaps = []
for cond, expected_n in expected_map.items():
    present_n = len(present_per_cond.get(cond, set()))
    if present_n < expected_n:
        gaps.append((cond, present_n, expected_n))

if gaps:
    print(
        f"\n  ⚠ M2 partial per-run coverage: "
        + "; ".join(f"{c}: {p}/{e} run_ids labeled" for c, p, e in gaps),
        file=sys.stderr,
    )
    return False
```

### Recommendation

**P0 — ship as ext13 #132 follow-up.** ~25 LOC + 1 regression test. ~20 min. Reuses ext13 #126's exit-7 path.

---

## Issue #145 — `run_sheet.md` CAMPAIGN_RUN_IDS discovery SQL has no completeness gate; partial/aborted campaigns silently enter scope (P0)

### Summary

`docs/run_sheet.md:77-85`:

```bash
export CAMPAIGN_RUN_IDS="$(uv run python -c "
import duckdb
conn = duckdb.connect('$CAMPAIGN_DB')
ids = [r[0] for r in conn.execute(
    'SELECT DISTINCT run_id FROM evaluation_runs '
    'WHERE NOT is_pilot ORDER BY run_id'
).fetchall()]
print(' '.join(ids))
")"
```

No `HAVING COUNT(*) >= N` filter, no cross-check against `--expected-tasks × --expected-runs`. Every non-pilot run_id in the DB enters `$CAMPAIGN_RUN_IDS`, regardless of whether it was a complete campaign run or a 1-row abort.

Contrast with **ext11 #111**'s longitudinal selector (fixed 2026-04-20):

```sql
SELECT run_id FROM evaluation_runs
GROUP BY run_id
ORDER BY MAX(timestamp) DESC LIMIT 1
```

The longitudinal selector was rewritten to use GROUP BY, but completeness filtering (`HAVING COUNT(*) >= expected`) was only added conceptually via ext12 #117 for the longitudinal run_id discovery. The Phase-4 `CAMPAIGN_RUN_IDS` discovery was NOT updated — it still exports every non-pilot run_id.

Scenario:
1. User runs Row 8 → `run_pilot.py` hits ext12 #115's watchdog abort at condition 2 of 4; partial data (condition 1 complete + condition 2 partial) written to `$CAMPAIGN_DB`.
2. User retries Row 8 → complete 4-condition campaign written to `$CAMPAIGN_DB` (append mode if ext7 #49's isolation wasn't used).
3. Row 8.5 discovery: `$CAMPAIGN_RUN_IDS` now contains 1 run from the aborted attempt PLUS 20 runs from the retry = 21 run_ids total.
4. Rows 14, 16, 17 each run against 21 run_ids. The aborted run's 1-row entry contributes as if it were a full 20-task run. Per-condition averages get dragged by the aborted run.

Even with ext7 #49's isolation (`--fresh-db`), a single user mistake (`--append` for debugging) introduces partial run_ids into `$CAMPAIGN_RUN_IDS` silently.

### Where

- **Discovery SQL (no completeness filter):** `docs/run_sheet.md:77-85`.
- **Downstream consumers:** rows 14, 16, 17 all pass `--run-ids $CAMPAIGN_RUN_IDS`.
- **Contrast:** `docs/run_sheet.md:94-99` (longitudinal selectors) use `GROUP BY run_id ORDER BY MAX(timestamp) DESC LIMIT 1` — better SQL semantics but still no explicit completeness filter per ext12 #117.

### Remediation

**A. Add `HAVING COUNT(*) >= :expected_rows_per_run` + `COUNT(DISTINCT task_id) = :expected_tasks`.**

```bash
# Canonical Phase-4 shape: 20 tasks × 1 row-per-task-run = 20 rows per run_id.
export EXPECTED_ROWS_PER_RUN=20
export EXPECTED_TASKS_PER_RUN=20

export CAMPAIGN_RUN_IDS="$(uv run python -c "
import duckdb, sys
conn = duckdb.connect('$CAMPAIGN_DB')
rows = conn.execute('''
    SELECT run_id, COUNT(*) AS n_rows, COUNT(DISTINCT task_id) AS n_tasks
    FROM evaluation_runs
    WHERE NOT is_pilot
    GROUP BY run_id
    HAVING COUNT(*) >= ${EXPECTED_ROWS_PER_RUN}
       AND COUNT(DISTINCT task_id) = ${EXPECTED_TASKS_PER_RUN}
    ORDER BY run_id
''').fetchall()
if not rows:
    print('ERROR: zero complete run_ids in \$CAMPAIGN_DB', file=sys.stderr)
    sys.exit(2)

# Sanity-report any runs that were dropped for incompleteness:
dropped = conn.execute('''
    SELECT run_id, COUNT(*), COUNT(DISTINCT task_id)
    FROM evaluation_runs WHERE NOT is_pilot
    GROUP BY run_id
    HAVING COUNT(*) < ${EXPECTED_ROWS_PER_RUN}
        OR COUNT(DISTINCT task_id) < ${EXPECTED_TASKS_PER_RUN}
''').fetchall()
if dropped:
    print(f'WARNING: dropped {len(dropped)} partial run_id(s) from CAMPAIGN_RUN_IDS:',
          file=sys.stderr)
    for rid, n, t in dropped:
        print(f'  {rid}: {n} rows, {t} distinct tasks', file=sys.stderr)

print(' '.join(r[0] for r in rows))
")"
```

**B. Extend the run-sheet lint introduced in ext7 #45 / ext7 #54.**

```python
def test_campaign_run_ids_discovery_has_completeness_filter():
    content = Path("docs/run_sheet.md").read_text()
    block = _extract_block("CAMPAIGN_RUN_IDS", content)
    assert "HAVING COUNT" in block, (
        "CAMPAIGN_RUN_IDS discovery SQL missing completeness filter; "
        "partial/aborted run_ids will silently enter downstream scope"
    )
```

### Recommendation

**P0 — ship alongside #142, #143, #144 as a Row-8 → Rows-14/16/17 trust-contract bundle.** ~15 lines in run_sheet.md + 1 lint test. ~20 min.

---

## Issue #146 — M2 contract is structurally unsatisfiable with default Row 16 flow; Row 17 ext13 #132 gate fires on EVERY canonical campaign (P0 CRITICAL, pipeline-breaker)

### Summary

Three independent code paths combine into a contract the default pipeline cannot satisfy:

1. **Row 17 expects M2 labels for all 4 conditions.** `scripts/analyze_results.py:32`:
   ```python
   CONDITIONS = ["full", "no_verification", "no_optimization", "static"]
   ```
   ext13 #132's per-condition completeness gate at line 1365 checks `expected_conditions = set(CONDITIONS)` — including `"static"`. A campaign without labels in all four conditions triggers exit 7.

2. **Row 16 defaults to JIT-only conditions.** `scripts/audit_contamination.py:74`:
   ```python
   _JIT_CONDITIONS = ["full", "no_verification", "no_optimization"]
   ```
   And line 140 filters the labelable pool with:
   ```python
   "COALESCE(generated_artifact_count, 0) > 0",
   ```
   Only rows with generated artifacts are surfaced for labeling. JIT-only default + generated-artifact filter = static rows never enter the labeling pool.

3. **Static path sets `generated_artifact_count=0`.** `tests/evaluation/harness.py:1323, 1326`:
   ```python
   elif matches:
       # Registry-match path: one matched capability was executed.
       generated_artifact_count = 0
       matched_and_executed_count = 1
   else:
       generated_artifact_count = 0
       matched_and_executed_count = 0
   ```
   Static condition rows always have `generated_artifact_count=0` (no synthesis occurred — a pre-seeded capability handled the task). Line 546 in `harness.py` writes this field to `evaluation_runs`. Every static row has `generated_artifact_count=0` by design.

**Contract intersection is empty:** Row 17 requires labels for "static"; Row 16 defaults to JIT-only AND filters-to-generated-artifacts-only; static rows have `generated_artifact_count=0`. No default-flow path produces static labels. Every canonical campaign's Row 17 exits 7 with `M2 partial coverage ['static']`.

Reviewer-confirmed reproduction: "JIT labels present, static unlabeled → Row 17 exits 7 with M2 partial coverage... ['static']."

### Where

- **Row 17 expected-conditions set:** `scripts/analyze_results.py:32` + line 1365.
- **Row 16 default conditions:** `scripts/audit_contamination.py:74`.
- **Row 16 labelable-pool filter:** `scripts/audit_contamination.py:140`.
- **Static-path generated_artifact_count=0:** `tests/evaluation/harness.py:1323, 1326, 546`.

### Why this is a pipeline-breaker (vs. a normal P0)

Prior P0s in this audit series can be bypassed via `--allow-*` flags (Chapter 4.5 disclosure required but publishable). #146 has no default-flow escape: the user cannot pass any existing flag to make Row 16 label static rows, because static rows are filtered out at the SQL level. Either:
- M2's contract must change (drop "static" from expected conditions), OR
- Row 16's default must change (include static by removing the `generated_artifact_count > 0` filter for static rows specifically), OR
- The static-path harness code must change (write `generated_artifact_count=1` for static rows, treating the matched seed capability as the "artifact").

None of these are one-line fixes; each is a design decision that affects Chapter 6's M2 interpretation.

### Proposal-alignment question

The proposal §M2 defines contamination rate as "fraction of generated artifacts that contain fabricated elements." Static rows have no generated artifacts by definition — the proposal's M2 is JIT-only. ext13 #132's inclusion of "static" in the expected-conditions gate is NOT proposal-mandated; it was added to force a JIT-vs-static comparison in Chapter 6's output table. That choice is defensible but not proposal-literal.

### Remediation

**A. (Preferred) Drop "static" from M2's expected-conditions gate.**

```python
# scripts/analyze_results.py::print_contamination_rate — after line 1365:
expected_conditions = set(CONDITIONS) - {"static"}  # M2 is JIT-only per proposal §M2
```

Chapter 6 M2 table still renders a row for static (with dashes or "N/A — static has no generated artifacts") but the gate doesn't fire on static absence. Consistent with proposal §M2; requires Chapter 4.5 disclosure that static is structurally excluded from contamination auditing.

**B. (Alternative) Include static in Row 16 labeling by synthesizing a "static pseudo-artifact."**

```python
# scripts/audit_contamination.py::fetch_unlabeled_rows — relax the filter:
where_parts = [
    "human_label IS NULL",
    "NOT is_pilot",
    f"condition IN ({placeholders_c})",
    f"capability_type IN ({placeholders_t})",
    # Allow static rows even with generated_artifact_count=0 — they have a
    # matched_in_registry_count >= 1 (the seed capability handled the task).
    "(COALESCE(generated_artifact_count, 0) > 0 OR condition = 'static')",
]
```

Labeling static rows means auditing whether the matched seed capability is contaminated — a different methodological question (is the seed capability fabricating?) than the JIT M2 question (is the synthesized capability fabricating?). Requires a separate labeling rubric. Not recommended for the Phase 5 eval timeline.

**C. (Alternative) Change `_JIT_CONDITIONS` default to include static.**

Orthogonal to (B); doesn't help unless (B) also lands because the SQL filter still excludes static rows even with the expanded conditions list.

**D. Regression test for whichever path lands.**

```python
def test_m2_contract_satisfiable_with_default_row_16_flow():
    """Synthesize a canonical campaign DB → run Row 16 default → run Row 17.
    Must exit 0, not 7."""
    ...
```

### Recommendation

**P0 CRITICAL — pipeline-breaker; must ship option (A) before Phase 5 Row 17 is trusted.** ~3 LOC + 1 regression test + Chapter 4.5 disclosure paragraph. ~20 min.

### Legacy data

Any prior Row 17 output that exited 7 with `M2 partial coverage ['static']` was not a real finding — the error message was a false positive on the contract. Post-(A)-fix, re-run Row 17; the gate will pass cleanly.

---

## Issue #147 — `print_gini_summary` M3 per-run coverage gate absent; complete `evaluation_runs` with partial `invocation_events` silently passes (P0)

### Summary

`scripts/analyze_results.py::print_gini_summary` fetches per-(condition, run_id, capability_id) invocation counts at line 857-875. It aggregates per-run Gini coefficients per condition and prints mean / min / max / aggregate columns at line 942-952. The completeness gate at line 967-974 (ext13 #128) checks only `missing_conditions`:

```python
missing_conditions = [
    cond for cond in CONDITIONS
    if not condition_ginis.get(cond)
]
```

No check against expected-runs-per-condition. If the canonical campaign has 5 runs/condition in `evaluation_runs` (ext7 #52's gate catches row-count mismatches) but `invocation_events` has entries for only 1 run/condition (ext13 #125 partial flush; ext14 #141 pagination truncation on the relevant subset), every condition's `condition_ginis` dict has at least one entry → ext13 #128 passes → Chapter 6 M3 mean computed over "N runs = 1 per condition" silently.

Reviewer-confirmed reproduction: "complete evaluation_runs (2 runs/condition) but invocation_events only for 1 run/condition → analyzer exited 0 and printed N runs = 1 per condition, with no fail."

This is the M3 sibling of ext14 #144 (M2 per-run coverage) — same pattern: ext13 added a per-condition gate; the per-run_id granularity within each condition is unguarded.

### Where

- **Per-run aggregation:** `scripts/analyze_results.py:857-875, 942-952`.
- **Per-condition-only gate:** `scripts/analyze_results.py:967-974`.
- **Compounds with ext13 #125** (partial flush dropping specific per-run invocations) + **ext14 #141** (pagination truncation) + **ext7 #52** (evaluation_runs completeness check doesn't cross-check invocation_events).

### Remediation

**A. Per-(condition, run_id) M3 completeness check (sibling of ext14 #144 for M2).**

```python
# scripts/analyze_results.py::print_gini_summary — after computing condition_ginis:
# Get expected run_ids per condition from evaluation_runs scope:
expected_per_condition = dict(conn.execute(
    f"""
    SELECT condition, COUNT(DISTINCT run_id)
    FROM evaluation_runs
    WHERE NOT is_pilot{_SCOPE_SQL}
    GROUP BY condition
    """
).fetchall())

per_cond_run_gaps = []
for cond in CONDITIONS:
    expected = expected_per_condition.get(cond, 0)
    present = len(condition_ginis.get(cond, []))
    if expected > 0 and present < expected:
        per_cond_run_gaps.append((cond, present, expected))

if per_cond_run_gaps:
    print(
        f"\n  ⚠ M3 partial per-run coverage: "
        + "; ".join(f"{c}: {p}/{e} run_ids have invocations" for c, p, e in per_cond_run_gaps),
        file=sys.stderr,
    )
    return False  # reuses ext13 #126's exit-7 missing-metrics path
```

**B. Regression test.**

```python
def test_m3_per_run_coverage_gate_fires_on_partial_invocation_events(tmp_path):
    """2 runs/condition in evaluation_runs, invocation_events only for 1 run/condition.
    Expected: exit 7 with 'M3 partial per-run coverage' in stderr."""
    db = _seed_partial_invocation_events(tmp_path, runs_per_condition=2, invocations_for=1)
    rc, stderr = run_analyze_results(db)
    assert rc == 7
    assert "M3 partial per-run coverage" in stderr
```

### Recommendation

**P0 — ship alongside ext14 #144 as the M2+M3 pair of ext13 #128/#132 per-run follow-ups.** ~25 LOC + 1 regression test. ~25 min. Reuses ext13 #126 exit 7.

---

## Issue #148 — Harness + `run_pilot.py` ignore `FlushResult.all_ok` signal; Row 8 exits 0 on partial telemetry flush (HIGH, latent)

### Summary

ext13 #125 landed the `FlushResult` struct + `all_ok` property in `cycle_flush.py`. Both longitudinal runners (`run_longitudinal.py`, `run_longitudinal_shift.py`) were wired to accumulate per-task `FlushResult`s and return a non-zero exit code on campaign-level partial-flush. The harness path used by Row 8 (`tests/evaluation/harness.py::_execute_run`) received only half the fix:

`tests/evaluation/harness.py:445-460`:
```python
fr = flush_task_cycle_state(
    ...
    task_events_cutoff=task_events_cutoff,
    is_pilot=is_pilot,  # ext7 #50
)
if not fr.all_ok:
    import logging as _lg
    _lg.getLogger(__name__).warning(
        "ext13 #125: cycle_flush partial failure for task=%s "
        "condition=%s run_id=%s: invocations_ok=%s "
        "utility_scores_ok=%s events_ok=%s errors=%s",
        ...
    )
```

The harness logs the warning but does NOT:
- Set a sticky flag on the `EvaluationHarness` instance.
- Propagate the failure signal back to the caller (`_execute_run` returns no `flush_ok` status).
- Accumulate per-task `FlushResult` objects for end-of-run aggregation.

Downstream, `scripts/run_pilot.py:442-449` gates only on `watchdog_aborted`:
```python
if watchdog_aborted:
    ...
    return 7
```

No flush-health gate. Row 8 can complete a full campaign, write every `evaluation_runs` row, log a long stream of "cycle_flush partial failure" warnings for task after task, and still exit 0 because the harness never told `run_pilot.py` that telemetry was degraded.

Downstream consumers (Row 17 M3 per #147, Row 18/19 longitudinal analysis per ext13 #127) fire their own gates on the partial data, but those gates blame the analyzer-side telemetry (sparse signal, missing utility scores) without attribution back to Row 8's flush failures. A user debugging a failed Row 17 has no surface signal that Row 8 was the upstream cause.

### Where

- **`cycle_flush.FlushResult` return:** `tests/evaluation/cycle_flush.py:55-62` (ext13 #125 landed).
- **Harness warning-without-propagation:** `tests/evaluation/harness.py:445-460`.
- **`run_pilot.py` exit-code logic (no flush gate):** `scripts/run_pilot.py:442-449`.
- **Sibling fix for longitudinal runners (landed):** `scripts/run_longitudinal.py` + `scripts/run_longitudinal_shift.py` accumulate results and exit 5 on campaign-level partial flush (ext13 #125 remediation).

### Why this is "latent HIGH" not P0

The warning log IS emitted per task, so a careful operator tailing the log can see partial-flush events in real time. Downstream analyzer gates (#147, ext13 #127, ext14 #144) DO fire on the resulting partial data — the silent-success-with-false-PASS pattern is broken at the analyzer layer, just not at the Row 8 layer.

BUT if the operator runs Row 8 unattended overnight (the Phase 5 design intent) and only sees Row 8's exit code before proceeding to Row 11/13/14/15/16/17, the partial-flush signal is lost. Downstream gate failures look like their respective scripts' problems, not a Row 8 problem.

### Remediation

**A. Harness accumulates per-task `FlushResult` and exposes an aggregate flag.**

```python
# tests/evaluation/harness.py:_execute_run — accumulate:
flush_results: list[FlushResult] = []
for task in tasks:
    ...
    fr = flush_task_cycle_state(...)
    flush_results.append(fr)
    if not fr.all_ok:
        logger.warning(...)  # keep existing

# Return / stash an aggregate:
self._flush_results = flush_results  # instance attribute
```

**B. `run_pilot.py` checks aggregate flush health.**

```python
# scripts/run_pilot.py — after the main loop, before the watchdog_aborted check:
partial_flushes = sum(
    1 for fr in harness._flush_results if not fr.all_ok
)
if partial_flushes > 0:
    logger.error(
        "Row 8: %d/%d per-task cycle_flush operations partially failed. "
        "Downstream M3 (Row 17), Pillar 3 RQ #2 (Row 18/19) will see partial "
        "telemetry. Consider re-running or inspecting flush errors in the log.",
        partial_flushes, len(harness._flush_results),
    )
    if not args.allow_partial_flush:
        return 5  # reuses ext13 #125 exit code
```

**C. CLI flag.**

```python
parser.add_argument(
    "--allow-partial-flush", action="store_true",
    help="Proceed with exit 0 even when per-task cycle_flush partially failed. "
         "Default: refuse with exit 5. Chapter 4.5 disclosure REQUIRED. ext14 #148.",
)
```

**D. Regression test.**

```python
def test_row_8_exits_nonzero_on_partial_flush(monkeypatch):
    # Patch eval_store.record_cycle_utility_scores to raise on every call.
    # Run run_pilot.main with a tiny task set.
    # Assert rc == 5.
    ...
```

### Recommendation

**HIGH — ship as a Row 8 integrity amendment to ext13 #125.** ~25 LOC in harness + ~15 LOC in run_pilot + 1 regression test. ~30 min. Reuses ext13 #125 exit code 5.

### Legacy data

Prior Row 8 runs may have produced silent partial-flush DBs. Post-fix, re-run with fresh harness output; downstream analyzer gates will fire correctly on any residual partial data that wasn't re-generated.

---

## Issue #149 — Row 17 has no M4 completeness gate; campaigns with all-zero `token_cost` pass with exit 0 (P0 CRITICAL)

### Summary

ext13 #126 + #128 + #132 landed a `missing_metrics` accumulator in `analyze_results.py::main` that tracks M3 (Gini) and M2 (contamination) absence and exits 7 if either is missing. M4 (token cost, Pillar 3 Context Economy headline) is NOT threaded through this accumulator.

At `scripts/analyze_results.py:717`:

```python
def print_token_cost_summary(conn: duckdb.DuckDBPyConnection) -> None:
    """Token cost per condition (from backfilled LangFuse data)."""
    rows = conn.execute(...)
    ...
```

Return type is `None`. The function prints the M4 table and returns. Line 1580-1590:

```python
missing_metrics: list[str] = []

if not print_gini_summary(conn):
    missing_metrics.append("M3 (Gini / Pillar 3 RQ #1)")

if not print_contamination_rate(conn):
    missing_metrics.append("M2 (Contamination)")
```

No `if not print_token_cost_summary(conn): missing_metrics.append("M4 ...")` line. `print_token_cost_summary` is called earlier in `main()` (around line 1560 region) with no status capture. M4 is treated as always-present.

Upstream data path:
- `src/registry/evaluation_store.py:34`: `token_cost INTEGER NOT NULL DEFAULT 0`.
- `tests/evaluation/harness.py:1356`: `"token_cost": 0` at row creation time.
- `scripts/backfill_token_costs.py` (Row 11) backfills non-zero values by UPDATE after the campaign completes.

If Row 11 is skipped entirely, or fails for the whole campaign (ext12 #122 all-empty exit 2 path), or succeeds but writes zeros via the ext14 #141 / #142 / #145 silent-data-loss chains, the DB ends up with every non-static row carrying `token_cost=0`. Row 17 prints:

```
  Condition           Total Tokens   Avg/Task   Avg/Nonzero  N with cost
  full                           0      0.00           N/A          0/100
  no_verification                0      0.00           N/A          0/100
  no_optimization                0      0.00           N/A          0/100
  static                         0      0.00           N/A          0/100
```

And exits 0. Chapter 6 M4 silently reports zero token cost across the JIT arm — thesis-destroying pro-JIT manufacturing of evidence while the pipeline reports success.

Reviewer-confirmed reproduction: "full 400-row synthetic campaign with valid M1/M2/M3 coverage and token_cost=0 everywhere → analyzer printed M4 zeros and exited RC=0."

### Where

- **`print_token_cost_summary` no-return-value signature:** `scripts/analyze_results.py:717`.
- **`missing_metrics` tracks M3 + M2 only:** `scripts/analyze_results.py:1580-1590`.
- **No M4 append anywhere in main flow.**
- **Schema default zero:** `src/registry/evaluation_store.py:34`.
- **Harness writes zero at creation:** `tests/evaluation/harness.py:1356`.

### Threat model / impact

- **Chapter 6 M4 silently zero while Row 17 passes.** Direction of bias: pro-JIT (infinite cost-efficiency). Worst direction.
- **Compounds with #141 + #142 + #145.** Each individual upstream silent-data-loss bug can produce the zero-M4 state that #149 fails to catch.
- **Compounds with ext10 #100** (now FIXED — avg_per_jit_task column renamed to avg_per_nonzero_task). #149 is independent: the column-rename made the per-nonzero count visible, but no gate checks whether `n_with_cost > 0`.
- **Direct structural match with ext13 #126 / #128 / #132** — same pattern class, different metric. M4 was overlooked during the ext13 missing-metrics gate rollout.

### Remediation

**A. Change `print_token_cost_summary` signature to return `bool`.**

```python
def print_token_cost_summary(conn: duckdb.DuckDBPyConnection) -> bool:
    """Return True when M4 coverage is usable; False when silently zero."""
    rows = conn.execute(...).fetchall()

    # Compute per-condition coverage:
    _nonzero_by_condition = {r[0]: r[4] for r in rows}  # n_with_cost
    _total_by_condition = {r[0]: r[5] for r in rows}    # n_total

    # ... existing print logic ...

    # Coverage gate: at minimum, the JIT conditions must have non-zero cost
    # for a meaningful fraction of their rows. Static is exempt (retrieval
    # path writes token_cost=0 legitimately).
    jit_conditions = {"full", "no_verification", "no_optimization"}
    jit_nonzero_ratio = []
    for cond in jit_conditions:
        total = _total_by_condition.get(cond, 0)
        nonzero = _nonzero_by_condition.get(cond, 0)
        if total > 0:
            jit_nonzero_ratio.append(nonzero / total)

    if not jit_nonzero_ratio or max(jit_nonzero_ratio) < 0.5:
        # Every JIT condition has <50% non-zero cost → backfill likely didn't run.
        print(
            f"\n  ⚠ M4 silent zero: JIT conditions have {jit_nonzero_ratio} "
            f"non-zero ratios. Likely cause: Row 11 not run, or ext14 #141 "
            f"pagination-truncation / ext12 #122 partial-runs / ext14 #145 "
            f"partial CAMPAIGN_RUN_IDS silently zeroed this campaign's costs.",
            file=sys.stderr,
        )
        return False

    return True
```

**B. Thread M4 through `missing_metrics`.**

```python
# scripts/analyze_results.py::main — extend line 1580-1590:
if not print_token_cost_summary(conn):
    missing_metrics.append("M4 (Token Cost / Pillar 3 Context Economy)")
```

Exit 7 fires when M4 is silently zero on JIT conditions. Reuses ext13 #126 path.

**C. CLI flag override.**

Existing `--allow-missing-metrics` from ext13 #126 covers #149 automatically.

**D. Regression test.**

```python
def test_m4_silent_zero_campaign_exits_7(tmp_path):
    """400-row DB with token_cost=0 everywhere on JIT conditions → exit 7."""
    db = _seed_400_rows_zero_token_cost(tmp_path)
    rc, stderr = run_analyze_results(db)
    assert rc == 7
    assert "M4 silent zero" in stderr

def test_m4_with_realistic_costs_passes(tmp_path):
    db = _seed_400_rows_realistic_token_cost(tmp_path)
    rc, _ = run_analyze_results(db)
    assert rc == 0
```

### Recommendation

**P0 CRITICAL — ship as the missing sibling of ext13 #126 / #128 / #132 for M4.** ~30 LOC + 2 regression tests. ~30 min. Must ship before any Chapter 6 M4 table is trusted; pairs cleanly with #141 / #142 / #145 upstream fixes.

---

## Issue #150 — ext13 #127's sparse-signal gate is bypassed by `cumulative_invocations` carry-forward (P0 CRITICAL)

### Summary

ext13 #127 added a per-cycle coverage gate at `scripts/analyze_longitudinal.py:797-804`:

```python
cycles_with_signal = sum(
    1 for c in cycles
    if c.get("cumulative_invocations")
    or c.get("utility_scores")
    or c.get("events")
)
coverage_ratio = cycles_with_signal / len(cycles) if cycles else 0.0
if coverage_ratio < args.min_cycle_coverage:
    ...exit 8 unless --allow-sparse-signal...
```

The gate uses `cumulative_invocations` as one of three OR-signals. Looking at how `cumulative_invocations` is populated, at `scripts/analyze_longitudinal.py:390-403`:

```python
for i in range(len(task_sequence)):
    # Add this cycle's increments
    for cap_id, n in cycle_increment[i].items():
        cumulative[cap_id] += n
    cycles.append({
        "cycle": i + 1,
        ...
        "cumulative_invocations": dict(cumulative),  # snapshot copy
        "incremental_invocations": dict(cycle_increment[i]),
        ...
    })
```

`cumulative` is a running dict. Once ANY invocation lands in cycle 1, `cumulative` has `{cap_id: 1}` and stays non-empty for every subsequent cycle's snapshot copy. On a 40-cycle DB with a single cycle-1 invocation event and nothing thereafter:

- Cycle 1: `cumulative_invocations = {cap_x: 1}` → truthy.
- Cycle 2: no new increments, but `cumulative_invocations = {cap_x: 1}` (snapshot copy) → truthy.
- ...
- Cycle 40: still `{cap_x: 1}` → truthy.

`cycles_with_signal = 40/40`; `coverage_ratio = 1.0`; #127's gate passes with `min_cycle_coverage=0.5`. #127's protection — "refuse analysis on sparse telemetry" — is defeated.

The fix intent of #127 was to catch "a 40-cycle DB with 1 event total" (ext13 #127's own reproduction text). The implementation uses `cumulative_invocations` which carries the 1 event forward indefinitely, producing the exact outcome #127 was designed to prevent.

Reviewer-confirmed reproduction: "40-cycle DB with one invocation event in cycle 1, no utility/events → analyzer reported Cycles-to-convergence: 2 and exited RC=0 (with --allow-proxy-kl) instead of tripping sparse-signal protection."

### Where

- **Gate using cumulative_invocations:** `scripts/analyze_longitudinal.py:797-804`.
- **Cumulative-snapshot population:** `scripts/analyze_longitudinal.py:390-403`.
- **Intent-vs-implementation mismatch.** #127's docstring says "per-cycle coverage" but the implementation uses cumulative.

### Threat model / impact

- **ext13 #127's protection is silently defective.** The gate appears to fire on sparse data but actually requires every cycle's `incremental_invocations` to be empty AND every cycle's `utility_scores` to be empty AND every cycle's `events` to be empty simultaneously. Since cumulative carries forward, the realistic threshold for triggering the gate is "zero events in the entire campaign" — which is already caught by ext12 #118's global-any() gate.
- **Compounds with #124 (proxy-KL downgrade).** Together: a campaign with 1 early invocation + no utility scores + no events downgrades to invocation-count KL proxy (#124 warns), passes the sparse gate (#150 bypass), and reports convergence on trivial data.
- **Direction of bias: pro-convergence.** Cumulative carry-forward smooths KL deltas toward zero across mostly-empty cycles; convergence detection fires early; exit 0 with "Cycles-to-convergence: 2" on effectively zero evidence.
- **Fix-induced regression against ext13 #127** — the fix's implementation inadvertently undoes its own intent.

### Remediation

**A. Replace `cumulative_invocations` with `incremental_invocations` in the gate.**

```python
# scripts/analyze_longitudinal.py:797-804 — before:
cycles_with_signal = sum(
    1 for c in cycles
    if c.get("cumulative_invocations")
    or c.get("utility_scores")
    or c.get("events")
)

# after:
cycles_with_signal = sum(
    1 for c in cycles
    if c.get("incremental_invocations")   # NEW: per-cycle increments only
    or c.get("utility_scores")
    or c.get("events")
)
```

`incremental_invocations` is already present in the snapshot dict (line 399) and contains only this cycle's new invocations. A cycle with no new invocations has `incremental_invocations = {}` → falsy → not counted as "signal." The gate now correctly measures per-cycle density.

**B. Regression test (post-fix should match ext13 #127's original repro).**

```python
def test_sparse_signal_gate_trips_on_cycle1_only_invocation(tmp_path):
    """40-cycle DB with 1 event in cycle 1, no utility/events → exit 8."""
    db = _seed_40_cycle_single_event_cycle1(tmp_path)
    rc, stderr = run_analyze_longitudinal(db)
    assert rc == 8, "sparse-signal gate must fire on 1/40 incremental coverage"
    assert "sparse" in stderr.lower()

def test_sparse_signal_gate_passes_on_every_cycle_has_signal(tmp_path):
    """40 cycles, each with ≥1 invocation → passes."""
    db = _seed_40_cycle_every_cycle_has_event(tmp_path)
    rc, _ = run_analyze_longitudinal(db)
    assert rc in (0, 2)  # convergence or no-convergence, not sparse-refused
```

**C. Consider separate cumulative-derived metrics that are ALLOWED to carry forward.**

Some downstream consumers (e.g., Gini of cumulative concentration) intentionally want cumulative state. Those should read from `cumulative_invocations` explicitly; the sparse-signal GATE should read from `incremental_invocations`. Rename the gate's variable to make intent clear:

```python
incremental_signal_cycles = sum(
    1 for c in cycles
    if c.get("incremental_invocations")
    or c.get("utility_scores")
    or c.get("events")
)
```

### Recommendation

**P0 CRITICAL — one-line fix that restores ext13 #127's intended protection.** 1 LOC + 2 regression tests. ~15 min. Must ship to close the sparse-signal bypass before any Row 18/19 output is trusted.

### Legacy data

Any prior Row 18/19 output that reported convergence may have passed through this bypass. Post-fix, re-run Row 18/19; any output that previously said "Cycles-to-convergence: N" on a near-empty DB will now correctly fire exit 8.

---

## Pattern notes

All twelve findings (#139-#150) follow variants of the now-well-documented **completeness-gate-coarser-than-failure-mode** pattern established by ext12 #118 → ext13 #127 → the ext13 cluster of granularity-escalation findings, with #146 being a distinct meta-pattern class (**contract-unsatisfiable-by-default-flow**) and #150 being a distinct sub-class (**gate-implementation-undoes-its-own-intent**). Specifically:

- **#139 is per-requested-session granularity.** Existing gates check "zero total" (ext7 #47, ext13 #138), "zero per agent" (ext13 #133), "all degraded" (ext12 #120). None check "each of N requested session IDs contributed ≥1."
- **#140 is pagination-ceiling granularity (Row 13/15).** Existing gates check result-set emptiness post-fetch, not whether the fetch itself hit a hard cap. A truncated fetch can pass every existing gate because it returns 20k non-empty records.
- **#141 is pagination-ceiling granularity (Row 11).** Same root pattern as #140 but on a different analyzer (`backfill_token_costs.py`) with a 50k ceiling AND an additional compounding hazard: the server-side request is not time-scoped, so the 50k budget can be burned on out-of-window observations before any relevant ones are fetched. ext12 #122 + ext13 #130's per-run coverage gates fire spuriously on truncation (misattributing the cause to retention).

These are two additional axes on top of the axes named in ext13's pattern notes:
- per-cycle (ext12 #118 → ext13 #127)
- per-condition (ext13 #126 → #128, #132)
- per-(run_id, task_id) (ext12 #122 → ext13 #129 → #130)
- per-agent-family (ext12 #120 → ext13 #133)
- per-requested-session-id (**ext14 #139**)
- per-pagination-completeness — Row 13/15 analyzers (**ext14 #140**)
- per-pagination-completeness — Row 11 backfill (**ext14 #141**)
- server-side-vs-client-side time scoping (**ext14 #141** compounding sub-axis)
- per-requested-run_id presence at analyzer scope (**ext14 #142, #143**)
- per-(condition, run_id) labeling coverage at Row 17 gate (**ext14 #144**)
- completeness-at-discovery-time for `$CAMPAIGN_RUN_IDS` (**ext14 #145**)
- per-(condition, run_id) M3 invocation coverage at Row 17 gate (**ext14 #147**)
- end-of-run flush-health aggregate at Row 8 runner (**ext14 #148**)
- per-metric completeness gate for M4 at Row 17 (**ext14 #149** — sibling of ext13 #126/#128/#132 that was missed during the rollout)
- incremental-vs-cumulative signal semantic at longitudinal sparse-gate (**ext14 #150** — the gate mistakenly reads carry-forward state where per-cycle density was intended)

And two distinct meta-pattern classes (not a granularity axis):

- **contract-unsatisfiable-by-default-flow (ext14 #146)** — three independent subsystems (Row 17 expected-conditions, Row 16 default filter, harness static-path generated_artifact_count) combine into a default flow that cannot satisfy the contract. No `--allow-*` flag bypass exists because the filter is SQL-level, not Python-level. Fix requires a design decision (drop static from M2, relax Row 16 filter, or relabel static as pseudo-artifact), not a new gate.
- **gate-implementation-undoes-its-own-intent (ext14 #150)** — ext13 #127's docstring and design intent said "per-cycle coverage," but the implementation reads `cumulative_invocations` which carries any single-cycle event forward indefinitely. A 40-cycle DB with 1 cycle-1 invocation shows `cycles_with_signal=40/40` and passes the gate — the exact case the gate was built to reject. Fix is a 1-line swap to `incremental_invocations`. This meta-pattern is worth flagging separately: it's not a granularity-axis gap (the granularity was correctly chosen in the design) but an implementation-vs-design drift that made the gate structurally decorative.

Each granularity axis needs its own gate; each gate's regression test must seed a fixture that fails at that specific granularity.

**Fix-induced bug count now 23 across the series** (ext9 #86 pre-fix; ext11 #109, #110, #113; ext12 #114, #117, #121; ext13 #127, #128, #129, #130; ext14 #139-#150 except #146 — #146 is the first instance of the distinct contract-unsatisfiable-by-default meta-pattern). #147 is fix-induced against ext13 #128; #148 is fix-induced against ext13 #125 (fix landed for longitudinal runners, never wired into harness path). #149 is fix-induced against ext13 #126/#128/#132 (M4 missed during the missing-metrics rollout). **#150 is the cleanest fix-induced regression in the series** — same file and docstring as ext13 #127's own fix, but implementation reads the wrong field; the gate's own behavior contradicts its own intent.

**Exit-code contract extended:**

| Code | Meaning | Introduced by |
|---|---|---|
| 0-3 | (pre-existing) | parent, ext6/7/8 |
| 4 | zero-evidence DB / missing-agent-family / missing-session-id | ext12 #118, ext13 #133, ext14 #139 |
| 5 | thesis-critical marker missing / pagination truncation | ext12 #123, ext13 #125, ext14 #140 |
| 6 | proposal-literal metric unavailable, proxy available | ext13 #124 |
| 7 | thesis-critical metric silently absent | ext13 #126, #128, #132 |
| 8 | sparse per-cycle telemetry | ext13 #127 |
| 9 | per-task tag coverage gap | ext13 #129 |
| 10 | quantitative under-coverage (generations-per-JIT-record) | ext13 #130 |
| 11 | judge/LLM-audit coverage below threshold | ext13 #134, #135 |
| 12 | Row 2 smoke-test stage fail-open | ext13 #136 |
| 13 | zero-label audit / zero-event Tier 2 fetch | ext13 #137, #138 |

#139 reuses code 4 (ext13 #133's per-agent exit code — same semantic class). #140 and #141 both reuse code 5 (ext12 #123's "thesis-critical-thing-missing" — pagination truncation is the cousin of shift-marker-missing) since both are pagination-ceiling truncation in the same analyzer family. #142 and #143 reuse code 4 (per-entity-presence gap at analyzer scope). #144 reuses code 7 (ext13 #126 missing-metric — per-(condition, run_id) gap is a M2 metric-incompleteness). #145 is a new discovery-time gate (exit 2 at SQL-discovery time before any analyzer runs). #146 is a DESIGN FIX, not an exit-code addition — the existing exit 7 from ext13 #132 is currently false-positive-firing; post-fix, exit 7 keeps its meaning but fires on real partial coverage rather than on every default campaign. #147 reuses code 7 (same class as #144). #148 reuses code 5 (ext13 #125's partial-flush exit for longitudinal runners; #148 extends the same code to Row 8 harness path). #149 reuses code 7 (M4 missing-metric sibling of M3/M2 at ext13 #126/#128/#132). #150 reuses code 8 (ext13 #127's sparse-signal exit code — the fix repairs the gate so the existing code fires correctly on sparse data).

---

## Parent-document integration (pending)

When ext14 lands, fold forward into `security_audit_2026_04_19.md`:

1. Update header: "One hundred thirty-eight findings → One hundred fifty findings; #139-#150 open as of 2026-04-21."
2. Insert TL;DR rows #139-#150 after ext13's #138.
3. Tranche 3 (eval-blocking) gains **six P0 CRITICAL** (#139, #140, #141, #146, #149, #150) + **five P0** (#142, #143, #144, #145, #147) + **one HIGH latent** (#148). Coverage map: #139 + #140 block Row 13 + Row 15 Pillar 2 Tier 2 + Context Economy; #141 blocks Row 11 → Row 17 M4; #142 blocks Row 14 T3 compression-loss evidence; #143 blocks Row 16 → Row 17 M2 selection-time coverage; #144 + #147 extend ext13 #132 / #128 to per-run coverage at Row 17 M2 + M3; #145 blocks `$CAMPAIGN_RUN_IDS` discovery; **#146 is a pipeline-breaker — M2 contract is unsatisfiable by default**; #148 extends ext13 #125's flush-health signal to the Row 8 harness path; **#149 closes the M4 gap in the ext13 missing-metrics family (every canonical campaign with skipped/failed Row 11 passes Row 17 with silent zero M4)**; **#150 restores ext13 #127's sparse-signal gate to its intended behavior (1-LOC fix to swap cumulative → incremental)**.
4. Extend "Behavioral regressions expected":
   - (#139) Both Row 13/15 analyzers' fetchers return `(records, contributed_session_ids)` tuples; `main()` verifies `requested_session_ids ⊆ contributed_session_ids`; exit 4 on per-session-id gap unless `--allow-partial-sessions` is passed with `[PARTIAL SESSIONS: N/M]` banner.
   - (#140) Both Row 13/15 fetchers return `(records, contributed, truncated: bool)` triples; WARNING log at ceiling hit; main() refuses with exit 5 unless `--allow-truncated` is passed with `[TRUNCATED: 20k+ records]` banner; new `--max-pages` CLI flag (default 200).
   - (#141) `backfill_token_costs.py::fetch_all_generations` applies server-side `fromStartTime`/`toStartTime` filters (eliminates out-of-window page-budget burn); returns `(records, truncated: bool)` tuple; WARNING log at ceiling hit; main() refuses with exit 5 unless `--allow-truncated` is passed; new `--max-pages` CLI flag (default 500); per-condition coverage banner carries `[TRUNCATED: 50k+]` when override is used.
   - (#142) `analyze_compression_loss.py` tracks contributed run_ids at fetch time; loud-fails with exit 4 on requested-vs-contributed gap unless `--allow-partial-runs` passed; empty-result branch distinguishes "filter dropped everything" (exit 2) from "campaign genuinely had no Tier 3 failures" (exit 0).
   - (#143) `audit_contamination.py::fetch_unlabeled_rows` returns contributed run_ids; main() loud-fails with exit 4 on gap unless `--allow-partial-runs` passed; empty-sample branch distinguishes "all in-scope already labeled" (exit 0) from "filter matched zero rows" (exit 3).
   - (#144) `analyze_results.py::print_contamination_rate` extends ext13 #132's per-condition gate with per-(condition, run_id) labeling coverage check; returns False when any in-scope run_id lacks labels; reuses ext13 #126's exit-7 path; override via `--allow-missing-metrics`.
   - (#145) `docs/run_sheet.md` CAMPAIGN_RUN_IDS discovery SQL gains `HAVING COUNT(*) >= :expected_rows_per_run AND COUNT(DISTINCT task_id) = :expected_tasks`; drops partial/aborted run_ids with a stderr WARNING listing them; exits 2 when zero complete run_ids are available. Run-sheet lint (ext7 #45 / #54 extension) guards the completeness filter.
   - (#146) `analyze_results.py::print_contamination_rate` drops `"static"` from the expected-conditions set (M2 is JIT-only per proposal §M2); Chapter 4.5 discloses static is structurally excluded from contamination auditing because static rows have no generated artifacts. (Alternative design choices — relaxing Row 16's filter or relabeling static as pseudo-artifact — require larger changes and are Chapter 7 future work.)
   - (#147) `analyze_results.py::print_gini_summary` extends ext13 #128's per-condition M3 gate with a per-(condition, run_id) coverage check (sibling of #144 for M3); reuses exit 7.
   - (#148) `tests/evaluation/harness.py::_execute_run` accumulates per-task `FlushResult` objects onto an instance attribute; `scripts/run_pilot.py` checks the aggregate after the main loop and returns exit 5 when any per-task flush partially failed (unless `--allow-partial-flush` is passed). Reuses ext13 #125 exit 5; extends the longitudinal-only fix to the harness path.
   - (#149) `print_token_cost_summary` signature becomes `-> bool`; returns False when any JIT condition has <50% non-zero token_cost ratio; `main()` threads this into `missing_metrics` exit-7 path. CLI flag `--allow-missing-metrics` (existing) covers override. Closes the M4-missing gap in the ext13 #126/#128/#132 missing-metrics rollout.
   - (#150) `analyze_longitudinal.py::main` sparse-signal gate swaps `c.get("cumulative_invocations")` → `c.get("incremental_invocations")` (1 LOC). Cumulative carry-forward no longer masks per-cycle sparsity. Gate now fires as ext13 #127's docstring originally intended.
5. End-of-audit note: "Extended 2026-04-21 (fourteenth extension) with findings #139-#150 (detail sections in `security_audit_2026_04_19_ext14.md`). #139-#141 extend the LangFuse-analyzer completeness-gate family; #142-#145 + #147 extend the run_id-scoping completeness-gate family; #146 is a distinct pipeline-breaker (M2 contract-unsatisfiable-by-default); #148 extends the ext13 #125 flush-health signal to Row 8. Ten findings total; #146 is the first contract-unsatisfiable meta-pattern and requires a design decision rather than a new gate. All ten eval-blocking; #146 must land before Row 17 can be trusted on ANY canonical campaign."

---

**End of extension.**
