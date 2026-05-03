# Security / Robustness Audit — 2026-04-19, Seventeenth Extension (#188)

**Parent document:** [`security_audit_2026_04_19.md`](./security_audit_2026_04_19.md) — #1-#18 all ✅ FIXED 2026-04-20.
**Sibling extensions:**
- [`security_audit_2026_04_19_ext4.md`](./security_audit_2026_04_19_ext4.md) — #12-#14 ✅ (folded into parent).
- [`security_audit_2026_04_19_ext5.md`](./security_audit_2026_04_19_ext5.md) — #15-#18 ✅ (folded into parent).
- [`security_audit_2026_04_19_ext6.md`](./security_audit_2026_04_19_ext6.md) — #19-#43 ✅ all FIXED 2026-04-20.
- [`security_audit_2026_04_19_ext7.md`](./security_audit_2026_04_19_ext7.md) — #44-#55 ✅ all FIXED 2026-04-20.
- [`security_audit_2026_04_19_ext8.md`](./security_audit_2026_04_19_ext8.md) — #56-#78 (20 ✅ FIXED + 1 ❎ false-positive + 1 ✅ partial + 2 ❎ acknowledged).
- [`security_audit_2026_04_19_ext9.md`](./security_audit_2026_04_19_ext9.md) — #79-#98 ✅ all FIXED 2026-04-21.
- [`security_audit_2026_04_19_ext10.md`](./security_audit_2026_04_19_ext10.md) — #98-#107 ✅ FIXED; #108 ⚠ latent/documented.
- [`security_audit_2026_04_19_ext11.md`](./security_audit_2026_04_19_ext11.md) — #109-#113 ✅ all FIXED 2026-04-21.
- [`security_audit_2026_04_19_ext12.md`](./security_audit_2026_04_19_ext12.md) — #114-#123 ✅ all FIXED 2026-04-21.
- [`security_audit_2026_04_19_ext13.md`](./security_audit_2026_04_19_ext13.md) — #124-#138 ✅ all FIXED 2026-04-21 (commit `61bf17d`).
- [`security_audit_2026_04_19_ext14.md`](./security_audit_2026_04_19_ext14.md) — #139-#150 ✅ all FIXED 2026-04-21 (commit `891be59`).
- [`security_audit_2026_04_19_ext15.md`](./security_audit_2026_04_19_ext15.md) — #151-#173 ❐ open as of 2026-04-22 (reviewer-driven sweep in 9 bundles covering CAMPAIGN_RUN_IDS selector, LangFuse analyzer coverage gates, asymmetric completeness checks, M4 silent-truncation, Row 17 headline-tables, Row 11 backfill integrity, Row 15 prose-vs-command, foreign-generation attribution, run-id cardinality + longitudinal upper-bound).
- [`security_audit_2026_04_19_ext16.md`](./security_audit_2026_04_19_ext16.md) — #174-#187 ❐ open as of 2026-04-22 (reviewer-driven sweep continuation; bundles A-F on longitudinal runner campaign-isolation, M5 retrofit, Row 13/15 silent-drop, Pillar 1 registry-match inflation, Row 13/14 sub-shape gaps, harness write-path integrity).

**This extension's scope.** One finding surfaced 2026-04-22 via reviewer continuation on the shift-runner axis; reviewer explicitly routed it to ext17.

**Bundle A (#188) — Shift-runner protocol identity not structurally encoded in `condition`.** Standalone **P1 confirmed**. `scripts/run_longitudinal_shift.py` writes `condition="longitudinal"` at three sites (lines 489, 519, 557) — **identical to `run_longitudinal.py`**. Row 18 (`analyze_longitudinal.py --run-id $LONGITUDINAL_RUN_ID`) and Row 19 (`analyze_longitudinal.py --db longitudinal_shifted.duckdb --run-id $SHIFT_RUN_ID`) disambiguate only via the `--db` path flag; the rows themselves carry no distinguishing label. Under ext16 Bundle A's reachability family (#175 + #176 no-freshness-guard on both longitudinal runners, ext7 #49 contract violations, accidental cross-DB append), plain longitudinal and shift populations become indistinguishable at the row level — Chapter 6 RQ #2 (convergence on stable workload) vs RQ #3 (adaptation to distribution shift) comparison corrupted silently. Architectural gap: the shift protocol's identity should be first-class in the row, not encoded implicitly by filesystem path.

**Status at time of write (2026-04-22):** one **open**. Total open across audit series: ext8 (2) + ext10 (1 latent) + ext15 (23) + ext16 (14) + ext17 (1) = **~41 findings**.

| # | Severity | Scope | Impact | Status |
|---|---|---|---|---|
| 188 | **P1 (confirmed)** | `scripts/run_longitudinal_shift.py:489, 519, 557` all three sites hardcode `condition="longitudinal"` — identical to `scripts/run_longitudinal.py`. Row 18/19 distinguished only by `--db` path flag | Under DB-boundary slip (#175/#176 no freshness guard, ext7 #49 contract violation, accidental cross-DB append, `--append` misuse), plain longitudinal and shift populations become indistinguishable by row label. RQ #2 convergence-on-stable-workload vs RQ #3 adaptation-to-distribution-shift Chapter 6 analyses conflate silently. Architectural gap: shift protocol identity not first-class in row schema. | ❐ OPEN |

---

## Issue #188 — Shift runner writes `condition="longitudinal"` same as plain runner; shift vs plain populations indistinguishable at the row level under DB-boundary slip (P1, confirmed)

### Summary

`scripts/run_longitudinal_shift.py` writes the same `condition` value as the plain longitudinal runner across every write-site:

```python
# Line 487-503: row construction
record = EvaluationRecord(
    run_id=run_id,
    condition="longitudinal",  # line 489
    ...
)

# Line 514-523: per-task cycle flush
fr = flush_task_cycle_state(
    eval_store=eval_store,
    metrics_store=metrics_store,
    registry=registry,
    run_id=run_id,
    condition="longitudinal",  # line 519
    task_id=task.task_id,
    ...
)

# Line 553-567: shift-marker cycle event
eval_store.record_cycle_events(
    run_id=run_id,
    condition="longitudinal",  # line 557
    task_id=task.task_id,
    events=[{"capability_id": "__system__", "event_type": "distribution_shift", ...}],
    occurred_at=record.timestamp,
)
```

Plain runner `scripts/run_longitudinal.py` also writes `condition="longitudinal"` (per `BaselineCondition.LONGITUDINAL = "longitudinal"` in `src/schemas/evaluation.py:34`). The two runners are distinguished **only** by:

1. **Output DB path** — `data/eval/longitudinal.duckdb` (plain) vs `data/eval/longitudinal_shifted.duckdb` (shift). Per #175 + #176, default paths are fixed per runner with no freshness guard.
2. **Presence of `distribution_shift` cycle_events rows** — shift runner emits one synthetic cycle_event at the shift boundary (line 555-564) with `event_type="distribution_shift"`. Plain runner doesn't emit this.
3. **Task ordering** — shift runner interleaves `--phase-a-tasks` and `--phase-b-tasks` with a mid-run switch at `--shift-at`; plain runner samples from the full testbed uniformly.

Row 18 (`scripts/analyze_longitudinal.py --run-id $LONGITUDINAL_RUN_ID`) and Row 19 (`scripts/analyze_longitudinal.py --db data/eval/longitudinal_shifted.duckdb --run-id $SHIFT_RUN_ID`) differ **only in the `--db` flag**. The analyzer code is shared; it produces "distribution-shift adaptation" output when cycle_events carries a `distribution_shift` marker.

### The "DB-boundary slip" hazard

The current separation depends entirely on filesystem isolation between two DBs. Multiple paths compromise this:

1. **#175 + #176 no-freshness-guard.** Neither runner enforces "one DB = one campaign"; re-invocations silently append. If a user runs plain longitudinal into `longitudinal.duckdb` at time T1, then shift into `longitudinal.duckdb` (with `--db data/eval/longitudinal.duckdb` explicitly, perhaps by mistake or shell-history reuse) at time T2, both campaigns land in the same DB under `condition="longitudinal"`.
2. **ext7 #49 violation on longitudinal.** The ext7 #49 contract exists only for `run_pilot.py`; nothing prevents `--db` pointing longitudinal outputs at arbitrary paths.
3. **`--append` misuse across shift and plain.** If either runner gains an `--append` flag (per #175/#176 proposed fix), append-misuse across the two runners pools populations.
4. **Operator error.** Shift and plain DBs live in the same directory (`data/eval/`); copy-paste, rsync, or backup-restore operations can easily land shift data in the plain DB.

Under any of these, the rows look **identical** to Row 18's analyzer: same `condition="longitudinal"`, same schema, same run_id shape. The only differentiator is whether cycle_events carries the `distribution_shift` marker — which is itself a per-cycle-event row, not a per-run tag.

### Concrete failure modes

**Failure mode A: Plain-in-shift-DB.** Shift DB contains a prior plain run + the shift run. Row 19 selector picks whichever run_id is "latest complete" (per #173); if the plain run is newer, Row 19 reports plain-run convergence metrics labeled as shift-adaptation output. Chapter 6 RQ #3 corrupted.

**Failure mode B: Shift-in-plain-DB.** Plain DB contains a prior shift run + the plain run. Row 18 selector picks newer of the two; if shift run is newer, Row 18 reports shift-adaptation metrics labeled as convergence-on-stable-workload output. Chapter 6 RQ #2 corrupted.

**Failure mode C: Both runs in one DB, post-fix #174 CTE selector.** Even with #174's "pick latest unconditionally then validate completeness" fix applied, the selector can't distinguish plain from shift at row level; it picks whichever is newest and valid, corrupting whichever analysis the operator intended.

None of these failure modes are detected by any downstream analyzer gate. `analyze_longitudinal.py`'s shift-detection (`_detect_shift_point` / `_classify_adaptation`) reads `cycle_events.event_type == "distribution_shift"` but never cross-checks against a `condition` tag — the analyzer trusts the selector's run_id and the `--db` path flag.

### Why P1 (confirmed)

- **Default-workflow-reachable under #175/#176.** Once either plain or shift runners append silently, the distinction relies on operator discipline with `--db` flags.
- **Silent corruption of both RQ #2 and RQ #3.** Chapter 6 "convergence on stable workload" and "adaptation to distribution shift" are distinct research questions; conflating their populations invalidates both.
- **Architectural gap, not a runtime bug.** The schema allows first-class encoding via a dedicated enum value or a sibling `protocol` field; the runners just don't use it.
- **Defense-in-depth alongside #174 + #175 + #176.** Fixing these three (strict selector, fresh-DB guards on both runners) closes the primary reachability paths, but #188 is the schema-level defense that catches the residual architectural gap if any new reachability surfaces.

Reviewer-submitted severity P1 matches. Not escalating to P0 because default workflow under the runners' current write contract + `run_sheet.md` guidance keeps the two DBs separate; #188's failure modes require pre-existing #175/#176/#174 gaps or operator error.

### Proposed fix (two options)

**Option A — dedicated `BaselineCondition` enum member for shift.** Adds a new enum value and routes shift writes through it:

```python
# src/schemas/evaluation.py
class BaselineCondition(StrEnum):
    FULL = "full"
    NO_VERIFICATION = "no_verification"
    NO_OPTIMIZATION = "no_optimization"
    STATIC = "static"
    LONGITUDINAL = "longitudinal"
    LONGITUDINAL_SHIFT = "longitudinal_shift"  # ext17 #188: distinguishable protocol
```

`scripts/run_longitudinal_shift.py` updates three sites (489, 519, 557) to use `"longitudinal_shift"`. `scripts/analyze_longitudinal.py` consumers read the `condition` column and fail-closed if they see a value that doesn't match the runner they expected (Row 18 expects `"longitudinal"`, Row 19 expects `"longitudinal_shift"`).

Pros: simple, symmetric to existing baseline labels, row-level self-identifying.
Cons: enum growth; BaselineCondition was originally for the four ablation baselines + `LONGITUDINAL`; adding a sixth blurs the ablation-vs-protocol distinction.

**Option B — dedicated `protocol` field on `EvaluationRecord`.** Adds a sibling column orthogonal to `condition`:

```python
# src/schemas/evaluation.py
class EvaluationProtocol(StrEnum):
    PHASE_4 = "phase_4"                 # run_pilot.py
    LONGITUDINAL_STABLE = "longitudinal_stable"  # run_longitudinal.py
    LONGITUDINAL_SHIFT = "longitudinal_shift"    # run_longitudinal_shift.py

class EvaluationRecord(BaseModel):
    ...
    condition: BaselineCondition = Field(...)
    protocol: EvaluationProtocol = Field(...)  # ext17 #188: orthogonal to condition
    ...
```

`evaluation_runs` schema gains a `protocol VARCHAR NOT NULL DEFAULT 'phase_4'` column via migration; legacy rows are back-filled based on `condition` (phase_4 for {full, no_verification, no_optimization, static}, longitudinal_stable for `longitudinal` from plain runner DB, longitudinal_shift for `longitudinal` from shifted runner DB — inferred from the DB filename at migration time). Row 18/19 queries gain `WHERE protocol = 'longitudinal_stable'` and `WHERE protocol = 'longitudinal_shift'` filters.

Pros: orthogonal to `condition`, preserves enum semantics, catches all three failure modes at row level.
Cons: schema migration, backfill logic, more intrusive.

**Recommended:** Option A for thesis-timeline minimalism. Option B is the architecturally-clean path if the thesis has time; mention in Chapter 7 future work if deferred.

### Proposed test

```python
def test_shift_runner_writes_distinguishable_condition_label(tmp_path):
    """ext17 #188: shift runner must write a condition value that's
    distinguishable from plain longitudinal runs. Pre-fix both runners
    wrote condition='longitudinal', making shift and plain rows
    indistinguishable under DB-boundary slip (#175/#176/#174 family).
    """
    import subprocess
    db = tmp_path / "shift_test.duckdb"

    # Minimum viable shift run (smoke).
    result = subprocess.run(
        ["uv", "run", "scripts/run_longitudinal_shift.py",
         "--db", str(db),
         "--phase-a-tasks", "T01", "--phase-b-tasks", "T02",
         "--shift-at", "1", "--total", "2",
         "--campaign-budget-hours", "0.5",
         "--per-task-budget-seconds", "60"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    # Read the DB and verify condition label.
    import duckdb
    conn = duckdb.connect(str(db))
    conditions = {r[0] for r in conn.execute(
        "SELECT DISTINCT condition FROM evaluation_runs"
    ).fetchall()}
    assert "longitudinal_shift" in conditions or "longitudinal" not in conditions, (
        "ext17 #188: shift runner must write a distinguishable condition label "
        "(longitudinal_shift) rather than reusing 'longitudinal'. Pre-fix: "
        f"got {conditions}, same as plain longitudinal runner."
    )


def test_analyze_longitudinal_fails_loudly_on_mixed_protocol_db(tmp_path):
    """ext17 #188: when a DB contains rows from both protocols (DB-boundary
    slip under #175/#176/#174), Row 18/19 analyzer must fail loudly rather
    than silently producing mixed-population output.
    """
    # Populate a DB with both plain longitudinal and shift rows
    # (pre-fix state: both as condition='longitudinal'; post-fix: one
    # as 'longitudinal', one as 'longitudinal_shift').
    ...
    # Row 18 analyzer (--run-id for the plain run, but DB has shift rows too)
    result = subprocess.run(
        ["uv", "run", "scripts/analyze_longitudinal.py",
         "--db", str(mixed_db), "--run-id", plain_run_id],
        capture_output=True, text=True,
    )
    # Post-fix: analyzer detects the mixed-protocol DB and exits non-zero.
    assert result.returncode != 0
    assert "mixed protocol" in result.stderr.lower()
```

### Interaction with ext16 bundle A

#188 is a defense-in-depth layer beneath the ext16 #174/#175/#176 fix cluster:

- #174 fixes the selector stale-fallback problem at the DB level.
- #175 + #176 fix the runners' silent-accumulation problem at the write level.
- **#188 is the row-level invariant** that catches populations mixed by any remaining reachability surface after #174/#175/#176 land.

Land order: #175 + #176 first (they block the most common reachability path); #174 next (defends the selector); #188 last (row-level defense-in-depth). Each closes a distinct layer; none fully subsumes the others.

### Structural-antipattern family membership

#188 adds a twentieth instance to the ext15/ext16 "stated-vs-realized diverges, failure silenced" antipattern family — this one on the **protocol-identity-encoded-via-filesystem-not-row** axis:

| Finding | Shape |
|---|---|
| #152 | task identity count-only |
| #157 | condition identity count-only |
| #159 | M2 condition extras missing-only |
| #160 | M4 partial zero max-only |
| #162 | M3 Aggregate cell-vs-pool compute |
| #163 | label-vs-scope desync post-ext6 #17 |
| #164 | missing-metrics retrofit gap (classification) |
| #165 | unmatched-population ungated |
| #166 | classification-confusion blacklist-vs-whitelist |
| #167 | scoping-contract unsatisfiability |
| #168 | prose-vs-command denominator ambiguity |
| #169 + #173 | selector upper-bound asymmetry |
| #170 | foreign-generation attribution via missing scope filter |
| #171 | run-id cardinality never checked |
| #172 | longitudinal analyzer shortfall-only |
| #178 | pre-filter-vs-post-filter ordering gap |
| #179 | partial-failure-as-complete threshold gap |
| #180 | malformed-metadata-as-zero shape-drift gap |
| #181 | classifier-bypass-scored-as-classifier-hit |
| #182 | file-contract-violation-as-generic-failure |
| #183 | model-window-constant-drift |
| #184 | empty-observation-ids-as-legit-zero |
| **#188** | **protocol-identity-encoded-via-filesystem-not-row** |

All instance the shape: "the invariant is stated somewhere (comment, docstring, enum, gate) but the realization relies on something upstream or downstream that can silently diverge without surfacing a signal."

---

## Parent-document integration (pending)

When ext17 lands, fold forward into `security_audit_2026_04_19.md`:

1. Update header: "One hundred eighty-seven findings → One hundred eighty-eight findings; #188 open as of 2026-04-22."
2. Insert TL;DR row #188 after ext16's #187.
3. Tranche 3 (eval-blocking) gains **one P1 confirmed** (#188). Defense-in-depth layer beneath ext16 #174/#175/#176 cluster; land last in the fix sequence.
4. Extend "Behavioral regressions expected":
   - (#188) `src/schemas/evaluation.py::BaselineCondition` enum gains `LONGITUDINAL_SHIFT = "longitudinal_shift"` (Option A) OR `EvaluationRecord` gains `protocol: EvaluationProtocol` sibling field (Option B). `scripts/run_longitudinal_shift.py` updates three sites (489, 519, 557) to write the new value. `scripts/analyze_longitudinal.py` queries filter by the new identifier; mixed-protocol DBs detected and loud-fail at exit code TBD. Run-sheet Rows 18/19 remain unchanged (they're path-scoped + run-id-scoped); the row-level defense is purely schema.
5. End-of-audit note: "Extended 2026-04-22 (seventeenth extension) with finding #188 (detail section in `security_audit_2026_04_19_ext17.md`). One finding, one bundle: #188 shift-runner protocol identity defense-in-depth — P1 confirmed. Adds the twentieth instance to the ext15/ext16 'stated-vs-realized diverges, failure silenced' structural antipattern family, on the 'protocol-identity-encoded-via-filesystem-not-row' axis. Land alongside ext16 #174 (selector stale-fallback) + #175 (plain longitudinal freshness guard) + #176 (shift runner freshness guard) for complete longitudinal-protocol integrity. Ext18 sweep continues to Row 12 `backfill_definition_token_costs.py` + further `analyze_longitudinal.py` compute surfaces + remaining Row 14 + Rows 18-19 sub-shape gaps."

---

**End of extension.**
