# Invocation Recording Gap — Pillar 3 Coverage Verifier Inert on Production Telemetry

**Surfaced:** 2026-04-19 (Ultrareview finding, validated against code 2026-04-19).
**Severity:** P0 for Pillar 3 claims.
**Scope:** All production `InvocationRecord` writers. Every Pillar 3 Split downstream consumer (coverage verifier + failure-informed prompt + feedback-driven trigger).
**Status:** Open — remediation plan below. No code changed yet.

---

## TL;DR

The thesis claims Pillar 3 Split learns from **real recorded failures** — the parent's wrong inputs are replayed against new variants (coverage verifier, Step 7) and are fed into the split LLM prompt (Step 8 `_summarize_failures_for_prompt`). On real CLI and compositional-chain telemetry **neither of these fires**. Both production `InvocationRecord` writers leave `input_args` and `actual_output` as `None`, and the `get_recent_failures` query filters `WHERE input_args IS NOT NULL`. Every real row is silently excluded before it reaches Split.

Unit tests and `scripts/test_split_merge.py` synthetically seed the missing fields, so the tests pass on a path production never exercises. Chapter 6 will read the eval DuckDB and find zero non-vacuous coverage verdicts across the full 400-record run.

---

## Evidence chain

### 1. Schema allows None (no hard constraint)

`src/schemas/metrics.py:27-40`:

```python
input_args:    str | None = Field(default=None, ...)
actual_output: str | None = Field(default=None, ...)
```

Both fields are optional; default `None`. Pydantic does not reject records that omit them.

### 2. Production writer A — CLI

`src/cli/runner.py:1263-1269` (`_record_invocation`):

```python
record = InvocationRecord(
    capability_id=getattr(artifact, "capability_name", "unknown"),
    task_id=task_prompt[:64],
    success=output is not None and bool(output),
    token_cost=0,
    latency_ms=float(elapsed_ms),
)   # no input_args, no actual_output → both default to None
```

### 3. Production writer B — compositional chain

`src/orchestrator/composition.py:389-395` (`_record_step_invocation`):

```python
record = InvocationRecord(
    capability_id=ref.capability_name,
    task_id=task_id[:64],
    success=bool(output),
    token_cost=0,
    latency_ms=latency_ms,
)   # same shape — no input_args, no actual_output
```

Plus a secondary defect on line 392: `success=bool(output)` evaluates `{"error": "..."}` as `True` because a non-empty dict is truthy. Error-shaped returns that don't raise are recorded as `success=True` — they never enter the failure bucket even if we fixed the first issue.

### 4. Query drops every production row

`src/registry/metrics.py:489-503` (`get_recent_failures`):

```sql
WHERE capability_id = ?
  AND success = FALSE
  AND input_args IS NOT NULL        -- every production row fails this
  AND (actual_output IS NOT NULL OR error_message IS NOT NULL)
```

### 5. Coverage verifier vacuously passes on empty set

`src/verification/coverage.py:408-412` (`verify_coverage`):

```python
if not failure_cases:
    return CoverageReport(all_covered=True, status="vacuous")
```

---

## Blast radius — four layers affected

### Layer 1: Coverage verifier (Step 7) is structurally dead on real telemetry

`verify_coverage` is advertised in `docs/verification/04_pillar_3_granularity_optimizer.md` as the "Pillar 2 × Pillar 3 coupling" that replays parent failures against split variants. On real CLI + eval data, `failure_cases=[]` every time → `all_covered=True, status="vacuous"` every time. The verifier rubber-stamps every MCP_TOOL split. The only caller that produces a non-vacuous verdict is `scripts/test_split_merge.py::test_split_mcp_tool`, which synthetically seeds `InvocationRecord(input_args=..., actual_output=...)`.

### Layer 2: Split LLM prompt is uninformed on real telemetry

`src/optimizer/operations.py::_summarize_failures_for_prompt` consumes the same `get_recent_failures` and injects the result into the per-type Split LLM prompt. On real data the injection is empty. The LLM is told "split this capability" with no failure evidence — variants are generated blind. The thesis claim *"variants target the input subspaces where the capability breaks"* reduces to *"variants are whatever the LLM decides."*

### Layer 3: `mint feedback --wrong` is structurally broken

`src/registry/metrics.py::flag_most_recent_invocation_wrong` docstring (line 315-317) claims it "preserves the row's existing `actual_output` so the wrong-output-case branch can compare against it." But production never writes `actual_output`, so there is nothing to preserve. The flagged row gets `success=FALSE` and `error_message=reason` but `input_args IS NULL`, so `get_recent_failures` still drops it. §12 finding #6b's entire motivation was closing this loop; on real telemetry it remains open.

### Layer 4: Yesterday's auto-loop MCP_TOOL unblock hits the same wall

Fix `aa11bb` (2026-04-19) removed the `cap.capability_type != CapabilityType.MCP_TOOL` filter in `src/optimizer/loop.py::identify_candidates_node`. MCP tools can now become split candidates on reuse/success thresholds. But once picked, Layer 1 + Layer 2 apply — the split's prompt is empty and its coverage check is vacuous. The trigger is now correct; what fires after the trigger is uninformed.

---

## Why the tests didn't catch it

### Unit tests seed the fields manually

`tests/unit/test_coverage.py` and `tests/unit/test_optimizer_operations.py` construct `InvocationRecord(..., input_args='{"x": 1}', actual_output='{"y": 2}')` explicitly. The per-branch coverage semantics are exercised correctly — the tests validate the **algorithm**, not the **plumbing** that feeds it.

### Integration script seeds the fields manually

`scripts/test_split_merge.py::test_split_mcp_tool` creates synthetic invocations with populated fields before triggering `split()`. The LLM integration path is exercised. The data-flow path from the two real production writers is not.

### The gap is a negative space

No existing test asserts "the CLI runner, on a real task, ends up writing a row with `input_args IS NOT NULL`." The absence of such a test means `_record_invocation` could silently stop writing any field without anyone noticing.

---

## Thesis implications

### Chapter 4 (Pillar 2 × Pillar 3 coupling)

Current prose: *"The coverage verifier replays the parent's recorded failure cases against new split variants and asserts each failure mode is fixed."*

Reality on eval data: every row in the `coverage_verification` event stream will carry `status="vacuous"`, `all_covered=True`. A reviewer asking *"show me one real coverage replay from the longitudinal run"* finds zero.

### Chapter 6 (longitudinal analysis)

`scripts/analyze_longitudinal.py` reads `cycle_events` and reports per-type operation counts. Split rows will appear, but the downstream narrative ("split targeted real failure subspaces, coverage verified") has no supporting data. The coverage replay count will be zero on the real DB.

### Reviewer surface

Two honest answers are possible depending on timing:
- **If fixed pre-eval:** *"We identified and fixed a data-plumbing gap where the recording path dropped inputs/outputs before Split consumed them. Post-fix eval numbers demonstrate the coupling."*
- **If not fixed:** *"The coverage verifier is implemented but inactive on this eval's telemetry because of a data-plumbing gap in the invocation recorder. Chapter 7 future work: populate the fields and re-run."*

The first answer is materially better for the thesis. The second is recoverable but concedes a core Pillar 3 claim to Chapter 7.

---

## Remediation plan

### Scope

Two call sites. One query-side assertion. One success-predicate tightening. ~40 LOC total. No schema change (fields already exist). No migration (existing rows stay NULL and are correctly filtered by the query).

### Step 1 — Populate `input_args` and `actual_output` at both writers (~30 min)

**File:** `src/cli/runner.py` around line 1251-1272.
**File:** `src/orchestrator/composition.py` around line 376-400.

Add `input_args` + `actual_output` parameters to `_record_invocation` and `_record_step_invocation`. Serialize with `json.dumps(..., default=str)` to survive non-JSON-native types (Path, datetime, Decimal). Cap each field at a size limit (e.g., 64 KiB) to avoid DuckDB row bloat when tools return large CSV-as-string blobs.

Both call sites already have access to the kwargs dict and the output dict — threading them through is a one-line change at each of the ~4 invocation points.

**Suggested helper** (new, `src/registry/metrics.py` or shared module):

```python
_SERIALIZED_FIELD_MAX_BYTES = 64 * 1024

def _serialize_for_invocation(value: Any) -> str | None:
    if value is None:
        return None
    try:
        s = json.dumps(value, default=str, sort_keys=True)
    except (TypeError, ValueError):
        return None
    if len(s.encode("utf-8")) > _SERIALIZED_FIELD_MAX_BYTES:
        return s[:_SERIALIZED_FIELD_MAX_BYTES // 2] + '..."TRUNCATED"'
    return s
```

### Step 2 — Tighten the success predicate (~15 min)

**File:** `src/orchestrator/composition.py:392` and `src/cli/runner.py:1266`.

Current:
```python
success=bool(output)
success=output is not None and bool(output)
```

Proposed:
```python
success=output is not None and bool(output) and not _looks_like_error(output)
```

Where `_looks_like_error` is a conservative check (shared helper):

```python
_ERROR_KEY_CANDIDATES = {"error", "exception", "traceback", "sandbox_error"}

def _looks_like_error(output: Mapping[str, Any]) -> bool:
    if not isinstance(output, Mapping):
        return False
    return any(k in output for k in _ERROR_KEY_CANDIDATES)
```

Rationale: conservative key-list, not a generic "has key named error" heuristic — some tools legitimately return `{"error_count": 0}`. The listed keys are the shapes our sandbox + executors use for error surfacing.

### Step 3 — Record error details when the success predicate flips (~10 min)

When `_looks_like_error(output)` is True, also populate `error_message` with a short summary (e.g., the value of the `error`/`exception` key). This gives `get_recent_failures` a non-null signal in the error-case bucket even when `input_args` populates independently.

### Step 4 — Regression guard tests (~30 min)

Two new tests, one per writer:

1. `tests/unit/test_cli_runner.py::test_record_invocation_populates_input_args_and_output` — construct a fake artifact, call `_record_invocation(kwargs={"x": 1}, output={"y": 2})`, assert the stored row has both fields non-None and round-trips through `get_recent_failures` when `success=False`.

2. `tests/unit/test_composition.py::test_record_step_invocation_populates_input_args_and_output` — same shape for the chain writer.

Plus one tightening test:

3. `tests/unit/test_composition.py::test_error_shaped_output_recorded_as_failure` — `_record_step_invocation` called with `output={"error": "oops"}` records `success=False`, not `True`.

These tests guard against future silent regression — if anyone removes the field assignment, the test fires immediately.

### Step 5 — End-to-end assertion in the integration script (~15 min)

`scripts/test_split_merge.py::test_split_mcp_tool` currently seeds synthetic records. Add an explicit check near the top:

```python
# Proves the production writers populate the fields that Split depends on.
# Without this, the whole test is a synthetic-only path that doesn't reflect
# what CLI and chain execution actually do. See docs/invocation_recording_gap_2026_04_19.md.
real_record = InvocationRecord(
    capability_id="test-cap",
    task_id="test",
    success=False,
    token_cost=0,
    latency_ms=1.0,
    input_args='{"x": 1}',
    actual_output='{"y": 2}',
)
metrics.record_invocation(real_record)
failures = metrics.get_recent_failures("test-cap")
assert len(failures) == 1 and failures[0].input_args is not None
```

Minor — documents the invariant in the script that integration-tests the flow.

### Step 6 — Doc updates (~15 min)

- `docs/verification/04_pillar_3_granularity_optimizer.md` — no change needed if fix lands pre-eval. If fix slips to Chapter 7, add a §7 "Open gap" row describing the inert-coverage-verifier state on this eval's data.
- `docs/MASTER_PLAN_2026_04_13.md` §12 — add a new finding (#19 or next free number) linking to this doc. Mark DONE when Steps 1-5 land.
- `MEMORY.md` — add a one-line reference pointing to this doc.

### Total effort estimate

~1.5-2 hours hands-on. All changes are local, small, and well-bounded. No LLM, no Docker, no eval re-runs required to validate the fix itself — the unit tests are sufficient.

**Post-fix eval re-run required** if fix lands before Phase 5 runs. The eval numbers from before the fix are misleading for any Pillar 3 coverage claim.

---

## Out of scope for this finding

- **Truncation policy for oversized outputs.** A CSV-as-string blob in a tool output dict can be multi-MB. Default truncation at 64 KiB is a starting point — revisit if Chapter 6 needs full outputs for some analysis. Orthogonal to the recording gap itself.
- **Evaluation-harness invocation writer.** `tests/evaluation/execution.py` has its own `InvocationRecord` construction path — verify it already populates the fields (it does, via `_run_one_task` wiring), but double-check when implementing Step 1.
- **Token cost.** Both production writers also hard-code `token_cost=0`. Not this finding's scope; tracked separately as a known gap in §11.12 runtime-vs-definition cost framing.

---

## Decision log for the fix

| Decision | Choice | Rationale |
|---|---|---|
| Serialize inputs as JSON-string or structured column? | String | Matches existing schema + avoids DuckDB array/struct complexity. Deserialize at read time. |
| Truncate oversized fields or drop them? | Truncate with marker | Keeps the row in the failure set even when full output is huge — coverage verifier's wrong-output compare can still fire on the truncated representation as long as both parent + variant truncate identically. |
| Error-key check hard-coded or plugin? | Hard-coded conservative list | Generic heuristics (any key containing "error") misfire on legitimate outputs like `{"error_count": 0}`. Keep it explicit. |
| Where does `_looks_like_error` live? | `src/registry/metrics.py` as a module-level helper | One writer is in `src/cli/`, the other in `src/orchestrator/`. Shared helper in `src/registry/metrics.py` imports cleanly from both. |
| Gate the fix on a config flag? | No | The old behavior is strictly worse on every thesis axis. No reason to keep it reachable. |

---

## Cross-references

- **Proposal:** `docs/thesis_proposal.md` §Pillar 3 Split — "specialized variants that target input subspaces where the capability breaks" presupposes that input subspaces are observable.
- **Upstream:** `docs/MASTER_PLAN_2026_04_13.md` §12 finding #6b (CLI feedback loop), §12 finding #5 (coverage verifier semantics), Step 4 (InvocationRecord failure-replay fields).
- **Sibling doc:** yesterday's loop-filter fix (removal of `!= MCP_TOOL` guard at `src/optimizer/loop.py:247`) — that was the trigger-layer gap; this is the data-layer gap. Together they close the two undocumented scope holes in Pillar 3 Split.
- **Proposal-alignment audit:** `docs/proposal_vs_implementation_v2.md` scorecard row "Split" currently reads ✅ proposal-faithful. Post-fix that remains true; pre-fix the claim is technically aspirational on real telemetry.
- **Tests-codifying-bugs pattern:** this is the same anti-pattern noted in `MEMORY.md::feedback_audit_tests_when_fixing_bugs` — the tests that exist exercise the synthetic-seeded path, so they can't detect the production plumbing gap.
