## Registry Match Compatibility Fix Plan

### Goal

Prevent bad registry reuse like `T11` without degrading the normal reuse mechanism.

The fix should:

- keep semantic retrieval as the first pass
- keep direct whole-task registry resolution working
- keep valid substep reuse working
- block only registry matches whose stored contract is incompatible with the decomposed subtask they are being used to satisfy

### Problem Summary

`T11` failed because the pipeline reused the seeded registry skill `sales-integration-report` for an early decomposed subtask that expected intermediate parser outputs.

Observed path:

1. Decomposition produced an early parser-like subtask.
2. Gap detection matched that subtask slug to `sales-integration-report` at score `0.70`.
3. The match was accepted without checking contract compatibility.
4. Composition executed `sales-integration-report` as `Chain step 1/4`.
5. That skill returned terminal report outputs like:
   - `summary_row_count`
   - `region_revenues`
   - `regions_meeting_target`
   - `output_file`
6. The next generated MCP tool expected intermediate upstream keys like:
   - `sales`
   - `products`
   - `targets`
7. Those were `None`, so the chain failed.

This is not just LLM nondeterminism. The LLM made the semantic match likely, but the code accepted an incompatible reuse.

### Why This Happens

Current retrieval is embedding-first and prose-first:

- registry search uses the prose embedding document from `SkillRegistry._build_document()`
- `detect_gaps()` accepts the top hit if it clears the cosine threshold
- schema metadata exists in the registry row, but Stage 1 does not currently use it to reject incompatible matches after retrieval

Relevant code:

- `src/registry/store.py`
  - `SkillRegistry._build_document()`
  - `SkillRegistry.detect_gaps()`
- `src/orchestrator/gap_detection.py`
  - `gap_detection_node()`
- `src/orchestrator/composition.py`
  - `build_resolution_map()`

There is already a late runtime output-schema check in `CompositionalExecutor.run()` for AGENT/SKILL outputs, but that is too late to preserve the intended pipeline behavior because:

- the bad match already bypassed generation
- the classifier/generator never got a chance to fill the missing step

### Design Principle

Do **not** replace semantic retrieval with exact schema equality.

That would be too strict and would damage real reuse.

Instead, add a **post-retrieval compatibility gate** for decomposed subtasks only.

This gate should answer:

> “Is this matched capability plausible for this specific subtask contract?”

If yes:

- keep the registry match

If no:

- demote the match to a gap
- let classifier/generator produce a new artifact for that step

### Scope

Apply the fix only when all of these are true:

- the task has `state.subtasks`
- the matched capability is being used to satisfy a specific decomposed subtask slug
- the subtask has a declared `output_schema`

Do **not** apply the filter to:

- fallback no-subtask flows
- direct whole-task retrieval where there is no decomposed chain
- plain `static` execution path

This keeps direct retrieval tasks like `T02`, `T13`, and `T19` unchanged.

### Proposed Implementation

#### 1. Add a narrow compatibility helper

Add a helper in `src/orchestrator/gap_detection.py` or a small new helper module if that keeps the file cleaner.

Suggested shape:

```python
def _is_match_compatible_with_subtask(
    *,
    subtask: SubTask,
    capability: CapabilityDescriptor,
) -> tuple[bool, str | None]:
    ...
```

Return:

- `(True, None)` when the match is acceptable
- `(False, "...reason...")` when it should be demoted to a gap

#### 2. Use output-schema compatibility as the primary check

For the first version, use a conservative output-side check only.

Required produced keys:

- start with `subtask.output_schema.keys()`

Compatibility rule:

- accept if the capability's stored `output_schema.keys()` are a superset of the subtask's declared `output_schema.keys()`
- otherwise reject

This catches the `T11` failure directly:

- subtask expected keys like `sales`, `products`
- matched capability returned keys like `summary_row_count`, `region_revenues`, `output_file`
- no superset relationship exists

This preserves valid cases like:

- parser reused for parser step
- writer reused for writer step
- whole-task skill reused for a final whole-task-shaped subtask

#### 3. Thread the compatibility gate into `gap_detection_node()`

In `gap_detection_node()`:

1. keep the existing `registry.detect_gaps(...)` call
2. map each primary slug to its `SubTask`
3. iterate through `matches_raw`
4. for each match:
   - if there is no associated subtask, keep it
   - if the subtask has no `output_schema`, keep it
   - otherwise run `_is_match_compatible_with_subtask(...)`
5. split the results into:
   - `compatible_matches`
   - `demoted_matches`
6. convert demoted matches into additional gaps

Important: a demoted match must become a real gap so the classifier/generator path still runs.

Suggested reason format:

```text
registry match 'sales-integration-report' incompatible with subtask output_schema:
expected keys ['products', 'sales'], got ['output_file', 'region_revenues', 'regions_meeting_target', 'summary_row_count']
```

#### 4. Keep the classifier and composition code unchanged initially

Do not patch `build_resolution_map()` first.

Reason:

- if `build_resolution_map()` rejects a match after gap detection, the subtask may end up unresolved with no generated artifact
- that turns a recoverable mismatch into a chain failure

The right place to reject the match is before classification/generation has been skipped.

#### 5. Add explicit logging

When a match is demoted, log it clearly in `gap_detection_node()`:

```text
gap_detection: demoting registry match sales-integration-report for parse-sales-csv-and-products-xlsx — incompatible output schema
```

This makes future audits easy in the Stage 1 log and Langfuse traces.

### Optional Phase 2

If Phase 1 lands cleanly, a later tightening can add an input-side plausibility check:

- if a subtask is a root step with no `requires_upstream_keys`, prefer capabilities whose stored `input_schema` looks file/input shaped
- if a subtask is downstream and consumes `requires_upstream_keys`, prefer capabilities whose inputs look compatible with `upstream_data`

This should be phase 2, not phase 1.

Reason:

- output compatibility is enough to fix `T11`
- input-side checks are more heuristic and easier to overfit

### Why This Should Not Break Reuse

This proposal does **not** require exact contract equality.

It only blocks reuse when the matched capability clearly cannot satisfy the subtask's declared produced keys.

That means:

- extra output keys are fine
- final whole-task matches still work for final whole-task-shaped subtasks
- direct whole-task retrieval still works
- valid parser/writer/policy reuse still works

What it removes is only false-positive reuse of the form:

- “semantic match is high”
- but “produced keys do not satisfy the subtask contract”

### Known Current Examples

#### Confirmed hard failure

- `T11`
  - bad match: `sales-integration-report`
  - root cause: terminal workflow skill reused as an intermediate parser step

#### Same structural issue, currently masked

- `T04`
  - repeated reuse of `inventory-reorder-policy`
- `T15`
  - repeated reuse of `monitoring-aggregation-policy`
- `T20`
  - repeated reuse of `eligibility-policy-classifier`

These have not failed because the seeded capability outputs still satisfy the task validator, but they show the same granularity drift.

### Test Plan

#### Unit tests: `tests/unit/test_gap_detection.py`

Add:

1. `test_incompatible_registry_match_demoted_to_gap`
   - construct a subtask with `output_schema={"sales": "...", "products": "..."}`
   - return a registry match whose capability output schema is terminal report-shaped
   - assert:
     - `cmd.update["matches"]` is empty
     - the capability query appears in `cmd.update["gaps"]`

2. `test_compatible_registry_match_preserved`
   - construct a subtask whose `output_schema` matches the capability's stored output schema
   - assert the match remains in `state.matches`

3. `test_no_subtasks_preserves_legacy_behavior`
   - ensure direct retrieval without subtasks still accepts the match

#### Unit tests: `tests/unit/test_composition.py`

No major behavior change should be needed here for phase 1, but add one regression-oriented test if useful:

4. `test_t11_style_bad_match_is_filtered_before_resolution`
   - optional higher-level test that exercises the demotion before composition runs

#### Targeted eval smoke

After implementation, run:

```bash
uv run pytest tests/unit/test_gap_detection.py tests/unit/test_composition.py -q
```

Then run a targeted `T11` breadth smoke:

```bash
uv run scripts/run_pilot.py \
  --db data/eval/metrics_t11_compat_smoke.duckdb \
  --metrics-store data/eval/metrics_t11_compat_smoke_metrics.duckdb \
  --tasks T11 --conditions full --n-runs 1 --fresh-db
```

Expected result:

- `sales-integration-report` should be demoted for the parser slot
- the parser step should be generated instead of reused
- the chain should no longer fail for the specific `NoneType` reason seen in the current run

### Rollout Order

1. implement the compatibility helper
2. wire it into `gap_detection_node()`
3. add unit tests
4. run targeted T11 smoke
5. rerun broader Stage 0 or focused eval smoke if needed

### Success Criteria

The fix is successful if:

- `T11` no longer reuses `sales-integration-report` for the intermediate parser step
- valid whole-task retrieval still works on non-compositional tasks
- valid substep reuse still works where the stored contract actually matches the subtask contract
- no broad regression appears in `tests/unit/test_gap_detection.py` or composition tests

