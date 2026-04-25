# Recurrence Post-Run Fix Plan — 2026-04-25

## Status

This plan is intentionally **post-run only**.

The current Stage 3 recurrence matrix was launched via:

- `scripts/eval_protocol/stage3_recurrence.sh`
- capture dir: `docs/eval_runs/20260423_recurrence_r1`

Do **not** patch the code mid-run. The current recurrence campaign is executing inside a long-lived Python process that built its recurrence task list at startup. For the main recurrence bug, editing now would not fix the already-materialized prompt rewrites. For some other runtime paths, editing now could affect later spawned child processes and produce a mixed-code dataset.

The goal of this document is to lock in what will be fixed after the run completes, why, and how it will be verified.

---

## Scope

### Committed post-run fixes

1. Recurrence prompt-rewrite / fixture-order mismatch
2. Schema-less merged AGENT registration

### Required post-run investigation

3. T13 registry-match static-execution failure path

### Explicitly deferred

4. Dynamic agent response-schema descriptions

This fourth item remains an investigation item, not a committed fix, because the current evidence does not yet show that it is a meaningful causal lever for the observed failures.

---

## Observed Evidence

The plan below is based on all four completed recurrence conditions:

- `full`
- `no_verification`
- `no_optimization`
- `static`

### Confirmed failed rows by condition

| Condition | Task | Persisted failure shape | Best current read |
| --- | --- | --- | --- |
| `full` | `T11.v02` | `output_rejected`, `chain_success=true` | Strongly associated with Fix 1. LangFuse shows swapped file-role prompt text for this recurrence task. |
| `full` | `T20.v01` | `output_rejected`, `chain_success=true` | Ordinary bad output / model-quality failure. Not a recurrence prompt bug. |
| `no_verification` | `T13.v01` | `output_rejected`, `chain_success=false`, registry match | Co-occurs with Fix 1 prompt swap, but the direct failing surface is the registry/static-execution path. Causation not yet proven. |
| `no_verification` | `T13.v02` | `output_rejected`, `chain_success=false`, registry match | Same as `T13.v01`. |
| `no_verification` | `T15.v02` | `chain_step_failed` at step 0 | Concrete generated MCP-tool bug (`read_csv(... infer_datetime_format=...)`). Not a recurrence-framework issue. |
| `no_optimization` | `T13.v01` | `output_rejected`, `chain_success=true` | Strong Fix 1 evidence. LangFuse shows swapped file-role prompt text, and the chain completes before the final output is rejected. |
| `no_optimization` | `T20.v02` | `chain_step_failed` at step 0 | Generated parser expected policy rules normalized as `field/operator/value`, but the real fixture uses `threshold`. Recurring parser-schema mismatch, not a recurrence-framework bug. |
| `no_optimization` | `T20.v03` | `chain_step_failed` at step 0 | Same parser-schema family as `T20.v02`, but with `target_field/operator/operand` instead of `field/operator/value`. Still a recurring rule-schema normalization miss, not a recurrence-framework bug. |
| `static` | `T11.v01-v03`, `T13.v01-v03`, `T14.v01-v03`, `T15.v01-v03` | `generation_failed` | Deterministic failures in every family where canonical fixture-role order differs from alphabetical recurrence order. No JIT/LLM activity in this condition. Strongest empirical confirmation of Fix 1. |

### Interpretation discipline

Two things are already proven:

- the recurrence fixture-role binding bug is real
- the `T13` registry/static-execution failure path is real

What is **not** yet proven:

- that the prompt-rewrite bug directly causes the `T13` registry/static-execution JSON parse failures

For now, treat those as two co-occurring failure surfaces until the `T13` registry/static path is traced directly.

---

## Fix 1: Recurrence Fixture-Role Binding Mismatch

### Problem

Recurrence task construction currently binds recurrence fixture files to semantic roles **positionally** while the recurrence manifest stores fixture files in **alphabetical filename order**.

That is unsafe whenever the canonical task fixture order is not alphabetical.

In JIT conditions, that corruption is visible first as bad prompt prose. In the static condition, the same underlying bug appears as positional misbinding of seeded implementation arguments.

### Where

- `tests/evaluation/fixtures/generate_recurrence.py`
- `tests/evaluation/recurrence_tasks.py`
- canonical task definitions in `tests/evaluation/tasks.py`

### Confirmed impact

This affects recurrence variants for at least:

- `T11`
- `T13`
- `T14`
- `T15`

Observed effect in JIT conditions:

- execution can still succeed because runtime binding uses actual files
- but the prompt prose shown to the decomposer / classifier / agent can describe the wrong file-role mapping
- LangFuse traces already showed swapped role text, e.g. CSV-vs-JSON roles inverted in recurrence prompts

Confirmed examples from completed runs:

- `T11.v02` in `full`
  - prompt text labels `t11_products.xlsx` as the sales CSV
  - prompt text labels `t11_sales.csv` as the product catalog Excel
- `T13.v01` and `T13.v02` in `no_verification`
  - prompt text labels `t13_baselines.json` as the time-series CSV
  - prompt text labels `t13_metrics.csv` as the baselines JSON
- `T13.v01` in `no_optimization`
  - prompt text again labels `t13_baselines.json` as the time-series CSV
  - prompt text again labels `t13_metrics.csv` as the baselines JSON

Observed effect in `static`:

- no LangGraph / generation / chain-executor traces are present
- the static harness calls seeded implementations positionally with `fixture_paths[0]`, `[1]`, `[2]`
- every family whose canonical fixture-role order differs from alphabetical recurrence order fails deterministically
- every family whose canonical order already matches alphabetical order succeeds deterministically

Static-condition family-level result:

- failures: `T11`, `T13`, `T14`, `T15` (`12/12`)
- successes: `T04`, `T20` (`6/6`)

This is direct evidence that recurrence fixture-role binding is corrupted independently of LLM behavior.

### Reuse amplification risk

This bug can do more than damage a single task prompt.

If a buggy artifact is generated from swapped recurrence prompt prose and then survives into the registry, later recurrence tasks may reuse that artifact instead of regenerating it. That creates the possibility of **registry-amplified recurrence failures**.

This amplification pattern is currently a **plausible hypothesis**, not a proven causal chain, for the `T13` failures in `no_verification`. The prompt swap is confirmed, and the repeated `T13` registry/static failure is confirmed, but the exact link between them still needs direct investigation.

The completed `no_optimization` run also shows that downstream manifestation is not fully deterministic at the artifact level:

- `T13.v01` still fails in a way strongly associated with Fix 1
- but a different `T13` run can survive the same prompt corruption if the generated runtime strategy is more resilient

That does not weaken Fix 1. It only means downstream manifestation depends on how the generated artifact chooses to consume inputs.

### Why this is the top priority

This is the strongest currently confirmed recurrence-specific code bug because it corrupts the planning/input-binding layer upstream of generation and execution:

- decomposition
- classification
- agent prompting
- static seeded execution

It has a larger blast radius than the other currently confirmed issues and directly affects the validity of Chapter 6 recurrence claims.

### Fix direction

Primary implementation target:

- `tests/evaluation/recurrence_tasks.py::_rewrite_prompt`

Primary fix:

1. Match canonical fixture references to recurrence files by basename / semantic role instead of by positional `zip()`

Why this is preferred:

- it fixes the bug at the point where the bad positional assumption currently lives
- it leaves the manifest format untouched
- it is robust to manifest ordering
- it should allow the existing recurrence fixtures on disk to be reused for the rerun

Fallback / future-hardening options:

2. Preserve canonical fixture-role order explicitly during recurrence task construction
3. Carry explicit per-file role labels and rewrite prompts by role instead of by index

The key invariant is:

> recurrence prompt substitution must map each canonical fixture reference to the correct recurrence file for that same semantic role

And more generally:

> recurrence fixture ordering must never be allowed to change the semantic role passed to either JIT or static execution paths

### Minimum acceptance criteria

- recurrence task prompts for `T11`, `T13`, `T14`, and `T15` preserve the intended file-role mapping
- no positional swap remains when canonical order differs from manifest filename order
- regression tests cover the affected families

### Planned tests

Add unit coverage asserting that recurrence prompt substitution preserves the intended role mapping for:

- `T11`
- `T13`
- `T14`
- `T15`

The tests should fail if alphabetical recurrence ordering silently reintroduces swapped prose.

Add one integration-style assertion for the rewritten prompt text itself:

- for affected recurrence families, verify that the rewritten prompt still describes each file with the same semantic role as the canonical task definition

Suggested minimum cases:

- `T11.v01`
- `T13.v01`
- `T14.v01`
- `T15.v01`

Add one static-path regression test asserting positional static dispatch still receives the correct semantic fixture order after recurrence task materialization for at least one mismatched family.

---

## Fix 2: Schema-Less Merged AGENT Registration

### Problem

Merged AGENT artifacts can be registered without `input_schema` / `output_schema`.

That leaves the registry with an artifact that exists nominally but cannot participate cleanly in later compatibility-aware reuse decisions.

### Where

- `src/optimizer/operations.py`

Two relevant surfaces:

- AGENT merge construction
- direct artifact registration behavior when schemas are missing

### Confirmed impact

The live recurrence run emitted the expected warning for a merged AGENT with no schemas.

Practical consequence:

- merged AGENTs are harder to credit cleanly in Pillar 3 reuse analysis
- schema-based compatibility filtering cannot reason about them properly
- this weakens the Chapter 6 merge-credit story

Observed live evidence:

- the recurrence run emitted the expected warning for a merged AGENT registered without `input_schema` / `output_schema`
- merge reuse credit is therefore not as cleanly interpretable as it should be

### Why this is the second priority

This is a real codebase weakness, but it is narrower than Fix 1.

It does not appear to be the main cause of the recurrence execution oddities. It is primarily about making the registry state and Pillar 3 reuse evidence defensible.

### Fix direction

Primary implementation target:

- `src/optimizer/operations.py::_merge_agents`

Primary fix:

1. Propagate merged `input_schema` / `output_schema` from parent AGENT capabilities before constructing the merged `GeneratedArtifact`

This should mirror the existing merge pattern already used for SKILL merges:

- union parent `input_schema`
- union parent `output_schema`
- attach both to the merged AGENT artifact before verification / registration

Backstop:

2. Reject registration of SKILL/AGENT artifacts that contain executable content or agent config but no schemas

The preferred behavior is fail-closed. If an artifact cannot be registered in a schema-aware form, the system should not silently accept it as if it were reusable.

### Minimum acceptance criteria

- merged AGENT artifacts are never registered in schema-less form
- registration either:
  - carries valid schemas, or
  - fails clearly and visibly
- registry reuse analysis no longer has to treat schema-less merged AGENTs as ambiguous evidence

### Planned tests

Add unit coverage for:

- merged AGENT registration succeeds when schemas are present
- merged AGENT registration fails or is rejected when schemas are absent

Add one regression test that mirrors the existing SKILL-merge behavior:

- when multiple parent AGENTs each carry schemas, `_merge_agents` must emit a merged artifact with the union of parent schemas before registration

---

## Investigation: T13 Registry-Match Static-Execution Failure Path

### Problem

In `no_verification`, both `T13.v01` and `T13.v02` failed after a registry match with:

- `Task T13 resolved via registry match`
- `Static execution for registry-matched task T13 failed: Expecting value: line 1 column 1 (char 0)`

This is a separate failing surface from normal chain execution.

### Why this needs its own item

The recurrence prompt-rewrite bug is present in those same runs, but the log prefix and persisted rows show that the direct failure happened in the harness's static-execution-for-registry-match path.

So there are two possibilities:

1. the static-execution branch invokes the cached matched agent, and the prompt-rewrite bug poisoned that artifact
2. the static-execution branch has an independent bug of its own

The current evidence does not distinguish those cleanly enough to claim causation either way.

### Investigation target

Trace the harness path that begins after:

- `Task T13 resolved via registry match`

and ends at:

- `Static execution for registry-matched task T13 failed`

### Questions to answer

1. What code path is used for registry-matched static execution?
2. Does it invoke the matched capability directly, or does it take a separate shortcut path?
3. Where does the JSON parse error originate?
4. Is the failing parser acting on task fixtures, cached files, or agent-produced intermediate state?
5. Is this a separate code bug, or is it an effect of Fix 1 poisoning reused artifacts?

### Acceptance criteria for closing the investigation

- the exact failing function / branch is identified
- the data source that triggers the JSON parse error is identified
- the issue is classified as either:
  - downstream consequence of Fix 1, or
  - a separate codebase defect

If it is a separate code defect, create a dedicated follow-up fix item rather than folding it vaguely into Fix 1.

---

## Design Note: Decomposition Coverage Fail-Open

This is **not** a committed fix item.

The completed recurrence runs surfaced cases where decomposition emitted:

- `Decomposition does not cover output_contract.required_keys`

and the pipeline still proceeded through classification / generation / execution.

This behavior is intentional. The decomposition node flips the sticky
`decomposition_succeeded=False` flag and emits an error-level LangFuse event,
but keeps the subtasks so the pipeline can continue and produce partial
diagnostic output.

This should be documented in analysis / Chapter 4.5 as a condition of the
current design:

- decomposition coverage failure is visible, not silent
- the pipeline remains fail-open for diagnostic value
- this can spend tokens on a chain that is already known to be under-covered

Note: the canonical M1 success predicate is
`gate_passed ∧ output_in_range ∧ ¬output_shape_mismatch`
(`src/schemas/evaluation.py:379`). The `decomposition_succeeded` sticky flag is
diagnostic state used for CLI exit codes and harness-side failure attribution; it
is not part of the M1 predicate. Tasks with uncovered decompositions still fail at
the M1 boundary because their output does not satisfy the task contract, not
because M1 consults the sticky flag directly.

Current decision:

- do not promote this to a committed code fix in the post-run patch
- treat it as an intentional design choice unless a later redesign is explicitly scoped

---

## Deferred: Dynamic Agent Response-Schema Descriptions

### Item

Potential improvement in:

- `tests/evaluation/execution.py`

Specifically, the dynamic result model currently drops output-schema descriptions instead of passing them through as `Field(..., description=...)`.

### Current decision

Do not include this in the recurrence cleanup patch by default.

### Reason

Current evidence is insufficient to claim that this is a material cause of the failures under discussion. It may be a worthwhile quality improvement, especially for solo-agent main-eval paths, but it is not yet justified as a recurrence-track fix.

### Follow-up condition

Investigate later only if trace evidence shows that structured-response under-specification is materially contributing to solo-agent failures.

---

## Sequence

1. Let the current recurrence matrix finish unchanged
2. Trace the `T13` registry/static-execution path and classify that issue cleanly
3. Implement Fix 1
4. Add regression tests for Fix 1
5. Implement Fix 2
6. Add regression tests for Fix 2
7. Run targeted tests
8. Rerun the recurrence sweep on the patched code

---

## Verification Plan

### Local verification

Before any rerun:

- run targeted unit tests for recurrence task construction and optimizer registration behavior
- run targeted tests for the `T13` registry/static-execution path once the failing branch is identified
- run the full test suite if the delta is small enough to justify it

### Recurrence rerun

After the fixes land:

- rerun the recurrence sweep on a clean code version
- use the rerun as the chapter-quality recurrence dataset

Expected improvements:

- no recurrence prompt role swaps
- no recurrence static-position binding mismatches
- cleaner interpretation of reuse vs regeneration
- cleaner merge-credit story for Pillar 3
- a clean answer on whether the `T13` registry/static failure is part of Fix 1 fallout or a separate bug

### Chapter 4.5 note

The completed `no_verification` condition also showed that merge-time verification / gate behavior still fires under the `no_verification` label.

That is not currently treated as a committed code fix, but it should be documented as a condition-semantics caveat in analysis:

- “no_verification” does not mean “no quality control anywhere”
- at minimum, merge outputs still pass through verification/gate logic

Documentation refresh (low priority, can defer):

- `src/cli/runner.py:2203` still describes `decomposition_succeeded=False` in
  pre-§12 timeout / single-sentence-fallback terms. Update that copy to reflect
  the broader modern meaning, including coherence violations and uncovered
  output-contract decompositions.

### Chapter 6 note

Do not treat the current recurrence matrix as chapter-quality comparative data.

In particular, the `static` result (`6/18`) is heavily contaminated by Fix 1:

- `12/12` failures occurred in families with canonical/alphabetical
  fixture-role mismatch (`T11`, `T13`, `T14`, `T15`)
- `6/6` successes occurred in families without mismatch (`T04`, `T20`)

So the current `static` baseline is not a defensible basis for any JIT-vs-static comparison. The rerun after Fix 1 is the first recurrence dataset suitable for that comparison.

---

## Not In Scope For This Patch

- changing the current run mid-flight
- redesigning the `T11` last-write-wins collision hazard
- generalized LLM quality tuning
- broad agent behavior changes unrelated to the two confirmed code issues above
- speculative claims that the `T13` static failure is already explained before tracing the code path

---

## Short Version

After this run finishes, fix:

1. recurrence fixture-role binding so file roles are preserved correctly in both prompt text and static positional dispatch
2. merged AGENT registration so schema-less AGENTs are not silently accepted

Also investigate:

3. the `T13` registry-match static-execution failure path, and classify whether it is downstream fallout from Fix 1 or a separate bug

Do not bundle speculative solo-agent polish into the recurrence cleanup pass unless later evidence justifies it.
