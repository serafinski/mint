# Recurrence Eval Implementation Plan

## Goal

Add a **proposal-aligned Pillar 3 recurrence evaluation** using the existing taskbed, while keeping the current Stage 1 breadth eval intact.

This plan assumes:

- no new task types
- same existing task contracts
- recurrence created via **fixture variants**
- all four baselines are present by default
- primary Pillar 3 comparison: `full` vs `no_optimization`

## What Must Be True At The End

1. `full`, `no_verification`, and `no_optimization` differ by **one pillar at a time**.
2. `full` uses **metrics-driven** optimizer behavior (`auto/auto/auto`), not forced `on/on/on`.
3. optimizer state persists for the intended campaign unit instead of resetting invisibly.
4. recurrence rows are first-class in DuckDB and Langfuse, not hidden behind fake task IDs.
5. the recurrence runner can execute repeated variants of the same base task in one run without corrupting `cycle_flush`, Gini, or KL analyses.

## Recommended Scope

### Keep Current Stage 1

Keep the current 20-task Stage 1 as the **breadth** study for:

- Pillar 1 synthesis breadth
- Pillar 2 verification value
- end-to-end composition robustness

### Add A Separate Recurrence Study

Run a second campaign using:

- tasks: `T04`, `T11`, `T13`, `T14`, `T15`, `T20`
- `3` fixture variants per task
- conditions: `full`, `no_verification`, `no_optimization`, `static`
- `3` runs per condition

Expected size:

- `6 × 3 × 4 × 3 = 216 rows`
- about `16-18h` at the current observed throughput

This is the smallest proposal-aligned design that gives Pillar 3 real repeated pressure while retaining the full ablation matrix.

## Why Not Overload Stage 1

Do **not** mutate `scripts/eval_protocol/stage1_main_eval.sh` into a mixed breadth+recurrence run.

Reasons:

- current analyzers and protocol checks assume the canonical Stage 1 shape: `20 tasks × 4 conditions × 3 runs = 240 rows`
- current `task_id` semantics assume one row family per `Txx`
- current Chapter 6 tables already map to the breadth campaign

Instead:

- keep Stage 1 unchanged as the breadth eval
- add a dedicated recurrence runner and analysis path

## Design Decisions

### 1. Fix Condition Semantics Centrally

Current behavior in `tests/evaluation/harness.py` is:

- `full` -> optimizer on
- `no_verification` -> verification off **and optimizer off**
- `no_optimization` -> optimizer off

That is not proposal-aligned.

Implement a single source of truth for condition profiles, for example:

- new module: `tests/evaluation/conditions.py`

Suggested profile shape:

- `jit_enabled: bool`
- `verification_enabled: bool`
- `optimizer_config: OptimizerConfig`
- `label: str`

Target semantics:

- `full`: JIT + verification + optimizer `auto/auto/auto`
- `no_verification`: JIT + optimizer `auto/auto/auto`, verification off
- `no_optimization`: JIT + verification, optimizer `off/off/off`
- `static`: no JIT

Files to change:

- `tests/evaluation/harness.py`
- optionally `scripts/run_pilot.py` labels/help text

### 2. Make Optimizer Persistence Explicit

The harness already supports `HarnessConfig.metrics_store_path`, but `scripts/run_pilot.py` does not pass it.

Proposal-aligned recurrence should treat each `(condition, run_index)` as one independent evolutionary campaign:

- state persists across all repeated task cycles **within that run**
- state does **not** leak across conditions
- state does **not** leak across replicate runs

Recommended path pattern:

- `data/eval/recurrence_metrics/<campaign>/<condition>/<run_idx>.duckdb`

For the breadth runner, decide separately:

- either keep current behavior for continuity
- or also wire `metrics_store_path` there and disclose the semantic change

Files to change:

- `scripts/run_pilot.py`
- new recurrence runner

### 3. Add First-Class Variant Identity

Current persistent tables assume `(run_id, condition, task_id)` is sufficient identity.

That breaks for recurrence because `T11` appears multiple times in one run.

Add the following fields:

- `task_family_id`
- `task_instance_id`
- `variant_id`
- `benchmark_track`

Recommended semantics:

- `task_family_id`: base task, e.g. `T11`
- `task_instance_id`: stable variantized instance, e.g. `T11.v02`
- `variant_id`: `v01`, `v02`, `v03`
- `benchmark_track`: `breadth` or `recurrence`

These should exist in:

- `src/schemas/evaluation.py`
- `src/registry/evaluation_store.py`
- `tests/evaluation/cycle_flush.py`
- any helper that writes `invocation_events`, `cycle_utility_scores`, `cycle_events`

This is important for both analysis and Langfuse cross-reference.

### 4. Do Not Encode Variants In `task_id`

Avoid making recurrence tasks look like `T11_V01`.

Why:

- `task_id` is used as the canonical taskbed key across analyzers
- `ALL_TASKS` and classification-accuracy logic assume the current `T01..T20` universe
- hiding variant identity inside `task_id` creates avoidable drift in analysis code

Keep `task_id` semantics clean:

- base task stays `T11`
- variant identity lives in dedicated fields

### 5. Build Recurrence Tasks As Clones, Not As Edits To Canonical Tasks

Do not rewrite `tests/evaluation/tasks.py` into a giant variant matrix.

Instead:

- leave canonical breadth tasks untouched
- add a recurrence task builder that clones selected tasks with new fixture paths, task prompts, and validators

Recommended new module:

- `tests/evaluation/recurrence_tasks.py`

Implementation note:

- `TaskDefinition` is frozen, but `dataclasses.replace(...)` works for cloning
- prompts currently hardcode fixture paths, so recurrence builder must rewrite those paths

## Fixture / Validator Strategy

### New Recurrence Fixture Root

Create a separate fixture root:

- `tests/evaluation/fixtures_recurrence/`

Structure:

- `tests/evaluation/fixtures_recurrence/T04/v01/...`
- `tests/evaluation/fixtures_recurrence/T04/v02/...`
- `tests/evaluation/fixtures_recurrence/T04/v03/...`
- etc.

Also write a manifest, for example:

- `tests/evaluation/fixtures_recurrence/manifest.json`

Manifest contents per instance:

- `task_family_id`
- `variant_id`
- `task_instance_id`
- fixture file paths
- expected values payload
- seed used

### Reuse Existing Generators Where Possible

`tests/evaluation/fixtures/generate.py` is already task-scoped and deterministic.

For these selected tasks:

- `T04`
- `T11`
- `T13`
- `T14`
- `T15`

the cheapest path is:

- call the existing `_gen_tXX(...)` into a variant directory with a deterministic per-variant seed

### Special Case: `T20`

`generate.py` currently stops at `T18`.

`T19` and `T20` exist only as static fixture files plus entries in `expected_values.json`.

So recurrence needs a first-class generator for `T20`, not just directory copying.

Recommended:

- add `_gen_t20(...)` to `tests/evaluation/fixtures/generate.py`
- optionally add `_gen_t19(...)` for consistency, even if not used in the minimal recurrence pack

### Parameterize Validators For Recurrence

Current validators in `tests/evaluation/tasks.py` close over `_EV["Txx"]`.

That does not work for recurrence variants.

Recommended refactor:

- move selected recurrence-task validators to factory functions
- canonical tasks call them with `expected_values.json`
- recurrence builder calls them with per-variant manifest values

For example:

- `_make_t04_validator(expected)`
- `_make_t11_validator(expected)`
- `_make_t13_validator(expected)`
- `_make_t14_validator(expected)`
- `_make_t15_validator(expected)`
- `_make_t20_validator(expected)`

Do the same for T11's file validator if needed.

This is better than duplicating validator logic in two places.

## Runner Architecture

### Add A Dedicated Runner

Create:

- `scripts/run_recurrence_eval.py`

It should:

- accept a recurrence task list and variant count
- materialize/load recurrence task instances
- run selected conditions and `n_runs`
- write to a dedicated DB
- use a dedicated metrics-store root
- stamp `benchmark_track='recurrence'`

Suggested default CLI:

```bash
uv run scripts/run_recurrence_eval.py \
  --tasks T04,T11,T13,T14,T15,T20 \
  --variants 3 \
  --conditions full,no_verification,no_optimization,static \
  --n-runs 3 \
  --db data/eval/metrics_recurrence_<timestamp>.duckdb
```

### Reuse The Harness, But Not Blindly

Prefer reusing `EvaluationHarness`, but extend it to accept recurrence-aware task metadata.

Minimal harness changes:

- allow task identity fields beyond `task_id`
- thread those fields into `EvaluationRecord`
- thread them into `cycle_flush` persistence
- include them in Langfuse metadata/tags

## Analysis Changes

Create:

- `scripts/analyze_recurrence.py`

Do not overload `scripts/analyze_results.py` initially.

Recurrence analysis should answer:

- does `full` beat `no_optimization` on success under repeated demand?
- does capability reuse become less skewed or more stable?
- do prune/split/merge events correlate with later success?
- does KL divergence between utility distributions stabilize within a run?

Recommended outputs:

- success by condition and task family
- success by variant index
- per-run reuse Gini
- per-run utility KL trajectory
- operation counts by type (`pruned`, `split`, `merged`, rollback)

After the recurrence path is stable, shared helper functions can be factored into `analyze_results.py`.

## Langfuse Changes

Add recurrence identity into trace metadata and tags:

- `benchmark_track: recurrence`
- `task_family_id: T11`
- `task_instance_id: T11.v02`
- `variant_id: v02`

This matters for:

- audit sampling
- comparing same-family variants
- separating breadth traces from recurrence traces

Main writer sites:

- `tests/evaluation/harness.py`
- `tests/evaluation/cycle_flush.py`
- any execution helper that emits sub-traces from task metadata

## Static Baseline Decision

This was the last expensive alignment gap, but it is now closed for the recurrence families.

Current authored static coverage includes:

- `T01`
- `T02`
- `T04`
- `T05`
- `T09`
- `T11`
- `T13`
- `T14`
- `T15`
- `T19`
- `T20`

That means recurrence can now include `static` by default without inventing any new task types or using a degraded fallback baseline.

## Tests To Add

### Harness / Semantics

- `tests/unit/test_harness.py`
  - `no_verification` keeps optimizer on
  - `full` resolves to `auto/auto/auto`
  - `no_optimization` resolves to `off/off/off`

### Store / Schema

- new `tests/unit/test_evaluation_store_variants.py`
  - recurrence rows persist `task_family_id`, `task_instance_id`, `variant_id`, `benchmark_track`
  - invocation/utility/event tables also persist variant identity

### Recurrence Task Builder

- new `tests/unit/test_recurrence_tasks.py`
  - prompt path rewrite is correct
  - fixture paths point at recurrence fixture root
  - validators use variant manifest values, not canonical `_EV`

### Fixture Generation

- new `tests/unit/test_generate_recurrence_fixtures.py`
  - deterministic per seed
  - all expected values present
  - `T20` recurrence generation works

### Analysis

- new `tests/unit/test_analyze_recurrence.py`
  - grouped by task family and condition
  - KL/Gini calculations stay scoped by variant-aware identity

## Implementation Order

### Phase 1 — Semantic Alignment

Change:

- condition-profile abstraction
- recurrence-safe optimizer config selection
- metrics-store path threading

Files:

- `tests/evaluation/harness.py`
- `scripts/run_pilot.py`
- new `tests/evaluation/conditions.py`

Acceptance:

- existing breadth runner still works
- `full` and `no_verification` differ only by verification gate

### Phase 2 — Variant Identity Plumbing

Change:

- add recurrence identity fields to schemas and tables
- thread through `cycle_flush`

Files:

- `src/schemas/evaluation.py`
- `src/registry/evaluation_store.py`
- `tests/evaluation/cycle_flush.py`
- `tests/evaluation/harness.py`

Acceptance:

- repeated `T11` in the same run produces distinct rows and distinct flushed cycle-state records

### Phase 3 — Fixture And Task Infrastructure

Change:

- recurrence fixture generator
- recurrence task builder
- validator factories for selected tasks

Files:

- `tests/evaluation/fixtures/generate.py`
- new `tests/evaluation/fixtures/generate_recurrence.py`
- `tests/evaluation/tasks.py`
- new `tests/evaluation/recurrence_tasks.py`

Acceptance:

- one command generates the 18 recurrence instances and a manifest

### Phase 4 — Runner And Analysis

Change:

- recurrence runner
- recurrence analyzer

Files:

- new `scripts/run_recurrence_eval.py`
- new `scripts/analyze_recurrence.py`

Acceptance:

- can run the 216-row recurrence campaign end-to-end
- analysis outputs the full four-condition matrix, with a clean `full` vs `no_optimization` Pillar 3 comparison inside it

### Phase 5 — Protocol Wrapper

The recurrence protocol wrapper should stay separate from `stage1_main_eval.sh`.

Implemented path:

- `scripts/eval_protocol/stage3_recurrence.sh`

## Engineering Estimate

### Minimum Viable Recurrence Implementation

- semantic alignment + persistence wiring: `0.5-1 day`
- variant identity + store/analyzer plumbing: `0.5-1 day`
- fixture/task infrastructure: `1-1.5 days`
- recurrence runner + analyzer + tests: `1 day`

Total:

- about `3-4.5 focused days`

## Runtime Estimate After Implementation

### Breadth Eval

Unchanged shape:

- `240 rows`
- about `18h`

### Recurrence Eval

Default recurrence pack:

- `216 rows`
- about `16-18h`

## Recommendation

Implement this in two commits/PRs:

1. **Eval semantics + identity plumbing**
   - condition profiles
   - optimizer persistence
   - recurrence row identity

2. **Recurrence track**
   - fixture generation
   - task builder
   - runner
   - analyzer

That splits the risky methodological fixes from the new benchmark path and makes debugging much easier.

## Exit Criteria

Ship the recurrence track when all of the following are true:

- `no_verification` keeps optimizer enabled
- `full` uses `OptimizerConfig(prune="auto", split="auto", merge="auto")`
- recurrence rows carry `task_family_id`, `task_instance_id`, `variant_id`
- repeated same-family tasks in one run do not collide in cycle-state persistence
- `run_recurrence_eval.py` completes the 216-row campaign
- `analyze_recurrence.py` produces a stable four-condition report with a clear `full` vs `no_optimization` slice
- Chapter 6 can describe Stage 1 as breadth and recurrence as Pillar 3-specific evidence without hand-waving
