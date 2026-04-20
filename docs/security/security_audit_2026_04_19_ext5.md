# Security / Robustness Audit — 2026-04-19, Fifth Extension (#15-#18)

**Parent document:** [`security_audit_2026_04_19.md`](./security_audit_2026_04_19.md).
**Sibling extension:** [`security_audit_2026_04_19_ext4.md`](./security_audit_2026_04_19_ext4.md) (#12-#14).

**Why this file exists.** Four more findings surfaced 2026-04-20 in the coverage-verifier, metrics-serialization, and longitudinal-analyzer layers. Each one distorts thesis telemetry — #15 inflates reported split-coverage rates, #16 biases split verification via unreplayable inputs, #17 drops 10% of tasks from Chapter 6 tables, #18 corrupts per-cycle utility/event snapshots whenever tasks repeat (which is always, under the default `--n-tasks=40`). All four reproduced against the tip.

**Status at time of write (2026-04-20):** all four **open**. #15 and #18 are **P0**s; #16 is **P1** (biases but doesn't invert conclusions); #17 is **P0 for Chapter 6** (straightforward to fix).

**Numbering.** Continues from the parent audit's #1-#11 + ext4's #12-#14.

---

## Issue #15 — Coverage verifier marks semantically-identical outputs as "handled" due to dict key-order asymmetry (P0)

### Summary

`verify_coverage` (the Pillar 3 Split post-check) decides whether a variant "handled" a parent failure case by comparing two JSON strings. The parent's stored `actual_output` is serialized with `sort_keys=True`; the variant's replay output is serialized **without** `sort_keys`. A variant that returns exactly the same dict as the parent — just with Python's dict-iteration order differing — serializes to a different string, compares unequal, and `_variant_handles_case` returns `handled=True`. Split's coverage gate passes; the parent is deactivated; the variant took the parent's place despite fixing nothing.

### Where

- **Parent side (symmetric `sort_keys=True`):** `src/registry/metrics.py:53` — `json.dumps(value, default=str, sort_keys=True)` inside `serialize_for_invocation`, which writes `actual_output`.
- **Variant side (missing `sort_keys`):** `src/verification/coverage.py:228` — inside `_build_replay_code`:
  ```python
  print(json.dumps({{"result": json.dumps(result, default=str), "error": None}}))
  ```
  The inner `json.dumps(result, default=str)` has no `sort_keys=True`.
- **Comparison site:** `src/verification/coverage.py:358` — `if replay_result == parent_output:` returns `False, None` (not handled) on string equality; otherwise line 365 returns `True, None` (handled). Semantically-identical dicts fall to line 365.

### Reproduction

```python
import json

# Parent side (src/registry/metrics.py:53)
parent_actual_output = json.dumps({"a": 1, "b": 2}, default=str, sort_keys=True)
# → '{"a": 1, "b": 2}'

# Variant side (src/verification/coverage.py:228) — same dict, insertion order varies
variant_result_str = json.dumps({"b": 2, "a": 1}, default=str)
# → '{"b": 2, "a": 1}'

assert parent_actual_output != variant_result_str  # CRASHES the coverage check's intent
# _variant_handles_case(...) → handled=True (line 365)
```

Confirmed by direct replay.

### Why this slips the test suite

`tests/unit/test_coverage.py` (29 tests from Step 7) constructs parent and variant fixtures with dicts whose keys happen to be in the same Python-literal order. The bug manifests only when the variant's code returns a dict built via a different insertion path (kwargs vs `dict(**)`, `json.loads` of a re-ordered payload, dict comprehension from an unordered set, etc.) — common in real generator LLM output, absent from the unit fixtures.

### Threat model / impact

- **Pillar 3 Split false-positive.** The Split operator's entire thesis claim is "variants collectively cover the parent's failure modes, verified by replay." A false-positive coverage result deactivates the parent and registers variants that didn't actually fix anything. Chapter 6's Pillar 3 conclusions depend on this not happening.
- **Direction of bias is pro-optimizer.** The bug makes coverage verification **more permissive**, so the reported Split success rate overstates genuine improvement. This is the worst direction of bias for the thesis — it manufactures evidence for the optimizer's effectiveness.
- **Compounds with #16 (truncated payloads).** A large-input failure case whose `input_args` was truncated can't replay at all (`json.loads` fails on the truncated JSON, replay returns error, line 344 returns `(False, None)` = not handled). Combine with #15: small-input cases falsely pass, large-input cases falsely fail. Net direction is dominated by whichever the input distribution weights.

### Remediation

**A. Symmetric sorting on both sides.**

`src/verification/coverage.py:228`:
```python
# Before:
print(json.dumps({{"result": json.dumps(result, default=str), "error": None}}))
# After:
print(json.dumps({{"result": json.dumps(result, default=str, sort_keys=True), "error": None}}))
```

One-line fix. Matches `serialize_for_invocation`'s policy at the call site that's supposed to mirror it.

**B. Defence-in-depth: compare parsed values, not strings.**

Replace the string comparison at `coverage.py:358` with a parse-then-compare:
```python
def _semantic_equal(a: str, b: str) -> bool:
    try:
        return json.loads(a) == json.loads(b)
    except (json.JSONDecodeError, TypeError):
        return a == b  # Fall back to string compare on parse failure.

if _semantic_equal(replay_result, parent_output):
    return False, None
```

This closes key-order differences, whitespace differences, integer-vs-float (`1` vs `1.0` — JSON parser yields `int` vs `float`, `==` returns True in Python), and any future `default=str` drift. Adds ~10 LOC plus a parametrized test.

**C. Regression test.**

```python
@pytest.mark.parametrize("parent,variant", [
    ({"a": 1, "b": 2}, {"b": 2, "a": 1}),           # key order
    ({"x": 1.0}, {"x": 1}),                          # int vs float
    ({"nested": {"a": 1, "b": 2}}, {"nested": {"b": 2, "a": 1}}),  # nested
])
def test_variant_handles_case_rejects_semantically_equal_outputs(parent, variant, ...):
    # Construct a failure with actual_output = json.dumps(parent, sort_keys=True)
    # Construct a variant whose sandboxed replay returns `variant`
    # Assert _variant_handles_case returns (False, None) — NOT (True, None)
    ...
```

### Recommendation

**Land (A) + (B) + (C) before any Phase 5 eval run that exercises Split.** (A) is a one-line fix; (B) is the robust version; (C) is ~15 LOC. Total ~30 min. Must ship before the 400-record eval because Pillar 3 Split is one of the thesis's four headline claims and the current telemetry is manufacturing-evidence-positive.

---

## Issue #16 — `serialize_for_invocation` truncation produces invalid JSON (P1)

### Summary

When an invocation payload (`input_args` or `actual_output`) exceeds `_INVOCATION_FIELD_MAX_BYTES`, `serialize_for_invocation` string-slices the JSON at the half-way point and appends the literal `..."__TRUNCATED__"`. The result is not valid JSON. Coverage replay reads `input_args` via `json.loads(...)` inside the sandboxed replay script and fails with `JSONDecodeError` on every truncated failure case — the replay can never reproduce the parent's conditions, so the case is silently counted as "not handled" regardless of whether the variant would have fixed it.

### Where

`src/registry/metrics.py:56-58` — inside `serialize_for_invocation`:
```python
if len(serialized.encode("utf-8")) > _INVOCATION_FIELD_MAX_BYTES:
    head = serialized[: _INVOCATION_FIELD_MAX_BYTES // 2]
    return f'{head}..."__TRUNCATED__"'
```

Consumed by `src/verification/coverage.py:216`:
```python
_kwargs = json.loads({input_args_json!r})
```

### Reproduction

```python
from src.registry.metrics import serialize_for_invocation, _INVOCATION_FIELD_MAX_BYTES
import json

big = {"payload": "x" * (_INVOCATION_FIELD_MAX_BYTES * 2)}
s = serialize_for_invocation(big)
# → '{"payload": "xxx...xxx..."__TRUNCATED__"'
json.loads(s)
# → json.decoder.JSONDecodeError: Expecting ',' delimiter: ...
```

Confirmed by direct Python execution.

### Why the docstring calling it "lossy-but-usable for replay" is wrong

The docstring at line 45-46 says "callers should treat truncated JSON as lossy-but-usable for replay." It isn't. Replay calls `json.loads` on the stored string; invalid JSON produces a `JSONDecodeError` which the sandboxed replay script catches and reports as an error. `_variant_handles_case` treats replay errors as regressions (line 341-344 in `coverage.py`). The case is effectively unreproducible.

### Threat model / impact

- **Pillar 3 Split false-negative on large-input failure cases.** Exactly the opposite direction of #15 — #16 suppresses evidence that Split is working, on the subset of failure cases with big inputs. Variants that would fix large-input bugs can never demonstrate it, so Split's verified-coverage rate is understated on that subset.
- **Pillar 2 Tier 3 adversarial replay similarly affected.** Any code path that reconstructs the parent's invocation from `input_args` inherits this bug. A scan of call sites is needed alongside the fix.
- **Thesis statistics interpretation.** Combined with #15 (false-positive on key-order), the direction of the Split-coverage bias is a function of the input-size distribution of failure cases. Neither bias is cleanly "pro-" or "anti-" optimizer; together they make Chapter 6's Split metrics effectively uninterpretable without landing both fixes.

### Remediation

**A. Truncation that preserves JSON validity.**

Option A1 — truncate to a valid JSON sentinel:
```python
if len(serialized.encode("utf-8")) > _INVOCATION_FIELD_MAX_BYTES:
    return json.dumps({"__truncated__": True, "__original_size_bytes__": len(serialized)})
```
The stored value is always valid JSON; `json.loads` yields a small dict; downstream consumers can check for `__truncated__` and skip replay explicitly (rather than implicitly via a parse error).

Option A2 — keep the original envelope when truncation happens, store the raw payload to a sidecar file:
```python
# Store: {"__truncated__": True, "__sidecar_path__": "/data/invocations/<uuid>.json"}
# Sidecar file holds the full payload.
```
More complex but preserves replay-ability for large inputs.

A1 is the pragmatic minimum; A2 is the proposal-aligned version if Chapter 4's "track all invocations for later analysis" claim is to stay load-bearing.

**B. Coverage replay checks for the truncation sentinel.**

`src/verification/coverage.py::_variant_handles_case`:
```python
try:
    parsed = json.loads(failure.input_args)
except json.JSONDecodeError:
    return False, f"input_args for task_id={failure.task_id} is invalid JSON (legacy truncation)"
if isinstance(parsed, dict) and parsed.get("__truncated__"):
    return False, f"input_args for task_id={failure.task_id} was truncated; case unreproducible"
```

So the coverage report distinguishes "variant failed" from "case unreproducible," and the CoverageReport's `execution_errors` list carries the accurate reason.

**C. Backward-compatibility for rows written pre-fix.**

Existing DuckDB rows may have the `..."__TRUNCATED__"` sentinel. The coverage-replay code should treat any unparseable `input_args` as unreproducible (already falls out of (B)'s except branch). No migration needed; just tolerate.

### Recommendation

**Land (A1) + (B) before Phase 5 eval.** Optional (A2) is Chapter 7 scope. Estimated effort: ~20 LOC + ~5 tests, ~30 min.

---

## Issue #17 — `analyze_results.py` drops T19 and T20 from task-level analysis (P0 for Chapter 6)

### Summary

The per-task analysis constant in `scripts/analyze_results.py` is:
```python
ALL_TASK_IDS = [f"T{i:02d}" for i in range(1, 19)]
```
`range(1, 19)` yields `1..18`, not `1..20`. T19 (exploratory data analysis AGENT) and T20 (eligibility classification SKILL) are silently excluded from every per-task and synthesis table the analyzer emits. `SYNTHESIS_TASK_IDS` derives from `ALL_TASK_IDS`, so the omission propagates to every downstream group.

### Where

`scripts/analyze_results.py:34`.

Cross-reference: `tests/evaluation/tasks.py:1246` — `ALL_TASKS` tuple includes T01..T20. `tests/evaluation/tasks.py:1234-1235` — `_EXPECTED_TYPES["T19"]="agent"`, `_EXPECTED_TYPES["T20"]="skill"`. The testbed has 20 tasks; the analyzer consumes 18.

### Reproduction

```python
ALL_TASK_IDS = [f"T{i:02d}" for i in range(1, 19)]
assert len(ALL_TASK_IDS) == 18           # ← 18, not 20
assert "T19" not in ALL_TASK_IDS         # ← silently dropped
assert "T20" not in ALL_TASK_IDS         # ← silently dropped
```

### Threat model / impact

- **Chapter 6 task-level tables are missing 10% of the testbed.** Every per-task success-rate row, every synthesis-task grouping, every retrieval-vs-synthesis comparison excludes T19 and T20. The `full` vs `static` aggregates silently sample 18/20 = 90% of the testbed.
- **Thesis-specific harm.** T19 is the only exploratory-AGENT task; T20 is the policy-driven SKILL task. Both are balancing points in the Pillar 1 type-distribution argument. Dropping them **under-samples exactly the two tasks Chapter 6's per-type analysis most depends on** — the agent-heavy open-ended task and the skill-heavy policy task. This is a direction-of-bias issue toward over-representing MCP_TOOL workloads.
- **Latent since Phase 1.** Likely a copy-paste from when the testbed was 18 tasks (the B1 pilot set). Testbed grew to 20 around Step 18.5's `expected_type` labeling; the analyzer constant was not updated.
- **Step 18.5 Core-Work Classification Accuracy metric is also affected.** `analyze_results.py::print_classification_accuracy` iterates `ALL_TASK_IDS`, so the per-type accuracy rates in Chapter 6 exclude T19/T20 — and T19/T20 are exactly the two labeled AGENT/SKILL ground-truth points most relevant to Pillar 1 RQ #1.

### Remediation

```python
# Before:
ALL_TASK_IDS = [f"T{i:02d}" for i in range(1, 19)]

# After:
from tests.evaluation.tasks import ALL_TASKS
ALL_TASK_IDS = [t.task_id for t in ALL_TASKS]
```

Import from the single source of truth; eliminate the hardcoded range that drifts when the testbed grows. No further code changes needed — every downstream construct (`SYNTHESIS_TASK_IDS`, iteration loops, table headers) re-derives from `ALL_TASK_IDS`.

### Regression guard

```python
def test_analyze_results_task_ids_match_testbed():
    from scripts.analyze_results import ALL_TASK_IDS
    from tests.evaluation.tasks import ALL_TASKS
    assert sorted(ALL_TASK_IDS) == sorted(t.task_id for t in ALL_TASKS)
    assert len(ALL_TASK_IDS) == 20
```

### Recommendation

**Land immediately.** Two-line fix + one regression test. ~5 min. Chapter 6 tables must be regenerated post-fix. Any prior analyzer runs on the current data should be re-run.

---

## Issue #18 — Longitudinal analyzer keys utility/events by `task_id` only, corrupting per-cycle snapshots when tasks repeat (P0)

### Summary

`scripts/analyze_longitudinal.py::_load_cycle_snapshots` loads per-cycle utility scores and operation events into two dicts keyed by `task_id`:

```python
utility_by_task_id: dict[str, dict[str, float]] = defaultdict(dict)
events_by_task_id: dict[str, list[dict]] = defaultdict(list)
```

When the task sequence repeats a task_id (the default `run_longitudinal.py --n-tasks=40` run hits each of the 20 testbed tasks at least twice), the dict assignment at line 258 **overwrites** earlier utility scores with later ones for the same task_id, and the list-append at line 271 **merges** events from all repetitions into a single list. Then at lines 295-296, every cycle snapshot looks up the same dict keys and receives the same merged result:

```python
cycles.append({
    ...
    "utility_scores": dict(utility_by_task_id.get(task_id, {})),  # ← merged across repetitions
    "events": list(events_by_task_id.get(task_id, [])),            # ← merged across repetitions
})
```

The per-cycle time series collapses: every repetition of T01 reports T01's final-run utility and T01's full event history, not the per-cycle deltas.

### Where

`scripts/analyze_longitudinal.py`:
- Line 247 — `utility_by_task_id` dict declaration.
- Line 258 — assignment overwrites on repetition.
- Line 260 — `events_by_task_id` dict declaration.
- Line 271 — append merges all repetitions.
- Line 280-282 — comment acknowledges the merge but defends it as "fine for cumulative KL + per-type op counts."
- Line 295, 296 — cycle snapshot reads the merged state.

### Reproduction

```python
from collections import defaultdict

# Simulated cycle_utility_scores rows, ordered by occurred_at:
util_rows = [
    ("T01", "cap_x", 0.1),    # cycle 1
    ("T01", "cap_x", 0.9),    # cycle 5 (task_id repeats)
]

utility_by_task_id = defaultdict(dict)
for task_id, cap_id, score in util_rows:
    utility_by_task_id[task_id][cap_id] = float(score)

# Both cycles read the same lookup:
cycle_1_util = dict(utility_by_task_id.get("T01", {}))  # → {"cap_x": 0.9}  ← WRONG, should be 0.1
cycle_5_util = dict(utility_by_task_id.get("T01", {}))  # → {"cap_x": 0.9}  ← correct for cycle 5
```

Confirmed by direct replay. Cycle 1 reports cycle 5's utility score.

### Why the inline comment's defence is insufficient

The comment at `analyze_longitudinal.py:280-282` says:
> "when a task_id repeats across the sequence, utility/events lookup returns the merged result — for the analyzer's purposes (cumulative KL + per-type operation counts) that's fine."

This is true for the **cumulative KL curve** (which compares distributions, not per-cycle snapshots) and for the **per-type operation counts** (which sum events across all cycles anyway). It is **not true** for:

1. **`adaptation_analysis` / shift-detection (Step 18.8).** The three-tier "graceful / sluggish / collapse" verdict reads per-cycle utility distributions to detect the shift point and measure re-convergence time. Merged-across-repetitions utilities make Phase A's last cycle and Phase B's first cycle look artificially similar (both read the same merged dict), compressing the reported KL spike at the shift and overstating the "graceful adaptation" verdict.
2. **Per-cycle utility time series in any Chapter 6 chart.** A chart plotting "utility of cap_x over time" will show a flat line at the final value for every cycle where T01 ran, instead of the genuine trajectory.
3. **Event attribution in per-cycle narratives.** If the thesis text says "at cycle 17, a split fired," the reader expects the event dict for cycle 17 to contain only that split — not every split/merge/prune that T01's runs have ever produced across the whole longitudinal sequence.

The comment defends the cumulative case only. Two other consumers of the same dict (Step 18.8 shift analyzer, any per-cycle narrative) are silently wrong.

### Threat model / impact

- **Step 18.8 distribution-shift verdict is unreliable.** The "graceful / sluggish / collapse" classification from `_classify_adaptation` depends on accurate per-cycle utility readings around the shift boundary. The current merge biases toward "graceful" because Phase A's last cycle snapshot reads Phase B-era utility values (later assignments win).
- **Pillar 3 RQ #2 (cycles-to-converge) is unreliable.** The convergence detector reads per-cycle utility to compute KL changes; merged utilities suppress the per-cycle signal and make convergence look artificially fast.
- **Default run configuration guarantees the bug fires.** `run_longitudinal.py --n-tasks=40` (default) with 20 unique tasks means every task repeats at least once. There is no "safe" configuration that avoids the bug with a realistic n_tasks.

### Remediation

**A. Key by `(task_id, run_id)` or `(task_id, occurred_at)` — unique per cycle.**

Both tables have a per-row timestamp (`occurred_at`). The `task_rows` iteration provides `run_id` per task invocation. Use either as the unique key:

```python
# cycle_utility_scores rows
util_rows = conn.execute(
    """
    SELECT task_id, capability_id, utility_score, occurred_at
    FROM cycle_utility_scores
    WHERE condition = 'longitudinal'
    ORDER BY occurred_at
    """
).fetchall()

utility_by_task_occurrence: dict[tuple[str, Any], dict[str, float]] = defaultdict(dict)
for task_id, cap_id, score, occurred_at in util_rows:
    utility_by_task_occurrence[(task_id, occurred_at)][cap_id] = float(score)
```

Then at the per-cycle snapshot site (line 283-297), match each cycle to its `task_boundaries` timestamp and look up the per-occurrence dict. The timestamp from `task_rows` (line 283) already uniquely identifies the cycle; use it as the second key.

**B. Same fix for events.**

```python
events_by_task_occurrence: dict[tuple[str, Any], list[dict]] = defaultdict(list)
for task_id, cap_id, event_type, details, occurred_at in event_rows:
    events_by_task_occurrence[(task_id, occurred_at)].append(...)
```

**C. Match cycle-to-occurrence via timestamp window.**

The `task_boundaries` list already pairs `(ts, task_id)` per cycle. Use the boundary timestamp as the lookup key:

```python
for i, (run_id, task_id, ts) in enumerate(task_rows):
    key = (task_id, ts)
    cycles.append({
        ...
        "utility_scores": dict(utility_by_task_occurrence.get(key, {})),
        "events": list(events_by_task_occurrence.get(key, [])),
    })
```

If the utility/event row timestamps don't exactly match the task_row timestamps (they're flushed slightly later), use the cycle window `[task_boundaries[i][0], task_boundaries[i+1][0])` to bucket rows — same technique the `cycle_increment` loop already uses at line 231-238.

### Regression guard

```python
def test_per_cycle_utility_snapshots_isolated_across_task_repetitions(tmp_path):
    """Two cycles with the same task_id must report their own utility, not the merged result."""
    db = EvaluationStore(tmp_path / "test.duckdb")
    # Insert: task T01 at t=1, utility 0.1; task T01 at t=2, utility 0.9
    # Run _load_cycle_snapshots and assert cycles[0]["utility_scores"]["cap_x"] == 0.1
    # (pre-fix: both cycles report 0.9)
    ...

def test_per_cycle_events_isolated_across_task_repetitions(tmp_path):
    """Events from different cycles of the same task_id must not bleed across."""
    # Insert: T01 at t=1 with event "split"; T01 at t=2 with event "merge"
    # Assert cycles[0]["events"] == [split_event], cycles[1]["events"] == [merge_event]
    # (pre-fix: both cycles report [split_event, merge_event])
    ...
```

### Recommendation

**Land before any longitudinal or shift-detection analysis run.** Step 18.8's shift-detection output is actively misleading without this fix — it will classify runs as "graceful adaptation" when the underlying utility series does not support that claim. Estimated effort: ~40 LOC in the analyzer + ~2 unit tests, ~45 min. Must ship before the optional Step 19 longitudinal eval and before any Chapter 6 per-cycle charts are drawn.

---

## Summary: combined bias directions

| Finding | Direction of bias | Chapter affected |
|---|---|---|
| #15 | **Pro-optimizer** (false-positive Split coverage) | Chapter 6 Pillar 3 |
| #16 | **Anti-optimizer** on large-input subset (false-negative Split coverage) | Chapter 6 Pillar 3 |
| #17 | **Pro-MCP_TOOL** (under-samples AGENT + SKILL tasks) | Chapter 6 Pillars 1 + 2 + 3 |
| #18 | **Pro-"graceful"-adaptation** (compresses shift-detected KL spike) | Chapter 6 Pillar 3 RQ #3, Step 18.8 |

#15 + #18 are the thesis's most dangerous direction — both manufacture positive evidence. #16 + #17 suppress evidence in different sub-populations. Landing all four is the only way Chapter 6's numbers can be trusted.

---

## Parent-document integration (still pending)

When #12-#18 are folded back into `security_audit_2026_04_19.md`:

1. Update the header "Fourteen findings" → "Eighteen findings".
2. Rewrite the opening priority line to split P0/P1 tranches clearly given eight now.
3. Insert rows #15-#18 into the TL;DR table.
4. Insert #15-#18 into Tranche 1 (all four are eval-blocking).
5. Extend "Behavioral regressions expected" with (a) coverage sort-keys behavior change, (b) truncation sentinel in new invocation rows, (c) T19/T20 now in all tables, (d) per-cycle time series keyed per-occurrence.
6. End-of-audit note: append "Extended 2026-04-20 (fifth extension) with findings #15, #16, #17, #18 (detail sections in `security_audit_2026_04_19_ext5.md`)."

---

**End of extension.**
