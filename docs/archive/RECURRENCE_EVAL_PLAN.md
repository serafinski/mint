# Recurrence Eval Plan

## Purpose

The current 20-task Stage 1 eval is good for:

- Pillar 1: JIT capability synthesis breadth
- Pillar 2: verification value
- compositional execution robustness

It is weaker for Pillar 3 because the task set is deliberately heterogeneous. Many tasks are one-off enough that reuse-driven split/merge/prune pressure is sparse.

This plan keeps the **same existing task definitions** and adds a **recurrence track** using fixture variants, so Pillar 3 can be measured without inventing new task types.

## Proposal Alignment Goal

Target semantics:

- `static`: fixed curated library, no JIT
- `no_verification`: JIT + optimizer, no verification gate
- `no_optimization`: JIT + verification, no optimizer
- `full`: JIT + verification + optimizer

For the recurrence track, the default matrix should include all four baselines:

- `full`
- `no_verification`
- `no_optimization`
- `static`

The primary Pillar 3 comparison is still:

- `full` vs `no_optimization`

But the default recurrence study should retain the full ablation matrix so the baseline story stays aligned with the proposal.

## Preconditions

Before running a recurrence eval, patch the eval semantics:

1. `no_verification` should keep optimizer enabled.
2. `full` should use optimizer config `auto/auto/auto`, not forced `on/on/on`.
3. `MetricsStore` should persist for the whole recurrence campaign or at least for each condition-run, so optimizer state spans multiple task cycles.

Without those changes, recurrence still gives signal, but it is not fully aligned with the proposal's Pillar 3 framing.

## Recommended Task Families

Use the current tasks only, grouped into recurring families.

### Policy / Rules Family

- `T04` — inventory reorder pipeline
- `T20` — eligibility classification

Why:

- both are policy-application workflows
- both tend to produce parse + apply + aggregate style capabilities
- good for reuse, specialization, and policy-shaped skill evolution

### Monitoring / Incident Family

- `T13` — anomaly investigation
- `T15` — monitoring aggregation
- `T16` — root cause diagnosis

Why:

- strong overlap in time-series parsing, anomaly handling, policy thresholds, causal reasoning
- best existing family for testing reuse and optimizer effects on agents/skills

### Tabular Integration / Reconciliation Family

- `T11` — multi-source integration
- `T14` — budget reconciliation

Why:

- repeated parse/join/aggregate/report patterns
- useful for testing granularity pressure on MCP tools and workflow skills

## Default Option

### Design

- Tasks: `T04, T11, T13, T14, T15, T20`
- Variants per task: `3`
- Conditions: `full`, `no_verification`, `no_optimization`, `static`
- Runs per condition: `3`

### Row Count

- `6 tasks × 3 variants × 4 conditions × 3 runs = 216 rows`

### Runtime Estimate

Using current Stage 1 observed average:

- `240 rows ≈ 18h`
- `1 row ≈ 4.5 min`

Estimated recurrence runtime:

- `216 × 4.5 min ≈ 972 min ≈ 16.2h`

### Why this is the best minimal choice

- still fits in one extra overnight block
- preserves the full proposal-aligned baseline matrix
- uses only existing tasks
- still supports the clean Pillar 3 readout via `full` vs `no_optimization`

## Minimal Option

### Design

- Tasks: `T04, T11, T13, T14, T15, T20`
- Variants per task: `3`
- Conditions: `full`, `no_optimization`
- Runs per condition: `3`

### Row Count

- `6 tasks × 3 variants × 2 conditions × 3 runs = 108 rows`

### Runtime Estimate

- `108 × 4.5 min ≈ 486 min ≈ 8.1h`

### Why this is stronger

- cheapest recurrence study that still gives Pillar 3 repeated pressure
- isolates Pillar 3 directly
- useful as a fast smoke or fallback if the full 4-condition recurrence matrix is too expensive

## Not Recommended

Mirroring the full Stage 1 matrix for the recurrence track:

- `8 tasks × 3 variants × 4 conditions × 3 runs = 288 rows`
- estimated runtime: about `21.6h`

This is expensive and usually unnecessary unless Chapter 6 needs a fully symmetric matrix.

## Static Baseline Caveat

`static` was the hardest condition to align with the proposal, because the original code only covered five tasks.

That gap is now closed for the recurrence families. Authored static coverage exists for:

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

So the recurrence families now have a real fixed-library baseline available. That means the recurrence stage can now default to the full proposal-aligned four-condition matrix instead of treating `static` as a special override.

## Fixture Variant Strategy

Do not invent new task types. Reuse the same task schema and validator, but generate multiple fixture realizations per task.

Examples:

- `T04`: vary inventory levels, daily usage, reorder thresholds, caps
- `T11`: vary sales/product/target tables while preserving the same workflow shape
- `T13`: vary anomaly windows, anomaly density, correlated metrics
- `T14`: vary department budgets, transaction patterns, rule thresholds
- `T15`: vary threshold levels and metric dynamics
- `T16`: vary onset time, root-cause metric, and cascade order
- `T20`: vary eligibility rules and applicant distributions

Important rule:

- keep the **task contract** fixed
- vary only the fixture contents and gold outputs

That preserves comparability while creating recurrence pressure.

## Recommended Reporting Split

### Chapter 6 Breadth Eval

Keep the current 20-task heterogeneous Stage 1 as the main breadth study for:

- end-to-end success
- verification value
- compositional execution coverage

### Chapter 6 Pillar 3 Eval

Add the recurrence track as a separate subsection:

- same task framework
- repeated fixture variants
- all four baselines available
- `full` vs `no_optimization` as the primary Pillar 3 comparison

This lets the thesis say:

- the broad testbed measures synthesis and verification under heterogeneity
- the recurrence track measures optimizer effects under repeated capability demand

## Recommendation

If time is constrained but you still want proposal alignment, run:

- the current Stage 1 breadth eval
- plus the **Default Option** recurrence eval

That is the best cost/signal tradeoff:

- extra runtime: about `16-18h`
- no new task types
- proposal-aligned baseline matrix for the recurrence families
- strong improvement in Pillar 3 interpretability

## Concrete Next Step

Implement the recurrence track as:

- `tasks = [T04, T11, T13, T14, T15, T20]`
- `variants_per_task = 3`
- `conditions = ["full", "no_verification", "no_optimization", "static"]`
- `n_runs = 3`

Expected total:

- `216 rows`
- about `16.2h` of runtime at current observed speed
