# JustInTimeMAS — Consolidated Plan to Phase 5 Eval

**Date:** 2026-04-13
**Branch:** `iter/6-execution-backends`
**Status:** Pre-B1, pre-eval. Nothing committed today yet.
**Purpose:** This is the single source of truth from now until the Phase 5 evaluation runs. It consolidates roughly fifteen fragmentary plans (`b1_enriched_embedding_plan.md`, `pre_eval_readiness.md`, `file_output_fix_plan.md`, `test_suite_behavioral_audit_2026_04_13.md`, the four cluster audits, and several others) into a single readable narrative. When this document and any earlier plan disagree, this one wins.

---

## 1. Where we are

The project has been running for six iterations. Iterations 0–5 built all three pillars end-to-end. Iteration 6 is the evaluation phase, and it has been running for about a week of pre-eval hardening. At the start of today the pipeline was waiting for three things to land before the Phase 5 eval (20 tasks × 4 conditions × 5 runs = 400 records, ~18 hours unattended):

1. **B1 — schema-aware merge.** Originally an embedding-enrichment experiment, pivoted mid-day after the enrichment regressed recall. The pivoted plan populates I/O schemas on capabilities (via an AST extractor that already exists but is not yet wired in) and adds a Jaccard-overlap gate to merge candidate selection so the proposal's second conjunct — *"overlapping input schemas"* — is actually checked.
2. **Iter-4 optimizer drift.** While verifying how Merge would consume the new schemas, I discovered that `merge()` and `split()` in `src/optimizer/operations.py` both produce metadata-only stubs with no executable content (no `source_code`, no `skill_content`, no `agent_config`). Parents are deactivated anyway, so the merged/split "capabilities" in the registry are unusable. If left unfixed, running the optimizer in the `full` baseline condition would poison the measurement — the `full` baseline would show *more* failures than `no_optimization` because the optimizer actively destroys working capabilities and replaces them with empty shells. This drift was not visible in the test suite because the tests assert only metadata-level fields (name, type, domain, tags) — they passed against stubs because they never checked for the fields the stubs were missing. The code comments in `operations.py` went further and *self-justified* the stub behavior, stating *"variants contain no generated code and therefore do not go through the three-tier verification gate."* Code, tests, and comments all drifted together from the same original incomplete reading of the proposal. This is a Chapter 4 methodology finding worth preserving.
3. **File output for SKILL/AGENT last steps.** The 2026-04-12 B3 work made MCP_TOOL last steps able to produce files (CSV, Excel, Markdown). SKILL and AGENT last steps still can't, because they don't write to the Docker workspace. Several testbed tasks (T06, T07, T13, T15 after rebalance) now expect non-JSON outputs, and if they terminate in a SKILL or AGENT step, they fail on output format rather than on capability logic. Tracked in `docs/file_output_fix_plan.md`.

Everything else in the pre-eval readiness checklist is already done: the 20-task testbed redesign, chain schema contracts, the `--allow-network` flag and dynamic Tier 1 severity, the anti-monolith generator prompts, the compositional executor with workspace-based chain execution, the lead-with-subagents agent generation (Phase 3.5b), the binary fixture handling fix (Phase 3.5c). Today's conversation did not reverse any of that — it built on top.

## 2. What's uncommitted in-tree right now

Everything today's work produced is sitting in the working copy:

- **`src/registry/store.py`** — all 8 seed descriptions corrected for description/implementation drift, all 8 seeds now carry concrete `input_schema`/`output_schema`, the `_build_document` function reverted to prose-only format after three enrichment variants regressed Phase A recall.
- **`src/orchestrator/schema_extraction.py`** — AST-based MCP tool signature extractor. Walks the generated source, extracts input_schema from parameter annotations, extracts output_schema from return-dict literals. Smoke-tested on all 8 generated MCP tools in `data/cli/chroma`. **Done but not yet wired into the loading pipeline.**
- **`src/schemas/task.py`** — `GeneratedArtifact` gained `input_schema` and `output_schema` fields (empty-dict defaults).
- **`scripts/test_registry_matching.py`** — four-phase diagnostic tool built today (naive semantic probe, LLM compatibility audit, self-consistency audit, reusability audit with LLM-generated paraphrase probes).
- **`scripts/run_task.py`** — deleted today. Its `_format_json` helper was moved into `src/cli/runner.py` and the stale docstring in `CLAUDE.md` was updated. This was a scripts-directory cleanup pass.
- **Test suite audits** — `test_suite_audit_2026_04_13.md` (import-level), `test_suite_behavioral_audit_2026_04_13.md` (behavioral), and four cluster reports (A/B/C/D). They identify five critical "tests that pass for the wrong reason" findings but recommend zero deletions.
- **Data side effect** — `data/cli/chroma` has been upserted with the fixed seed descriptions, preserving the 11 generated capabilities unchanged.

None of this is committed. The first commit in this session should probably be the low-risk housekeeping (seeds, scripts cleanup, audit docs) before B1 starts touching `operations.py` and `loop.py`.

## 3. What the thesis proposal says about Pillar 3

Earlier today I was reading a version of `docs/thesis_proposal.md` that contained explicit type-scoping and deferral language in Pillar 3 — phrases like *"split applies to Skills and Agent configurations"*, *"for MCP tools, splitting would require automated code refactoring — a problem beyond reliable LLM capability — and is deferred to future work"*, and *"merging MCP tool source code is excluded from the implementation for the same reason."* You then rolled the proposal back to its original form, and those narrowings are **not in the original**.

What the rolled-back (original) proposal actually says about Pillar 3, quoted verbatim:

> *"These metrics drive three evolutionary operations:*
>
> ***Split (capability is too general).** When a capability shows high reuse but mediocre success (say, >60% reuse, <70% success), it is decomposed into two or more specialized variants. The optimizer agent analyzes failure cases to identify the input subspaces where the capability breaks, then generates targeted variants for each subspace.*
>
> ***Merge (capabilities are too specific).** When two or more capabilities show very low reuse (<5%) but high similarity (measured by embedding distance of their descriptions and overlapping input schemas), they are merged into a single, broader capability. The merged version inherits the strongest behavioral patterns from each parent.*
>
> ***Prune (capability is dead weight).** Capabilities with zero invocations over a configurable window and no declared dependencies are retired from the active registry (archived, not deleted, for potential resurrection)."*

Note what's **not** there: no *"applies to Skills only"*, no *"excluded from the implementation"*, no *"formal framework vs. scoped implementation"* disclaimer. The operations are described abstractly, as if they apply uniformly to the full capability space (MCP_TOOL, SKILL, AGENT). The only language in the whole proposal that separates the three types is in Pillar 1 classification (at generation time): deterministic+stateless becomes a tool, procedural+domain-specific becomes a skill, stateful+planning becomes an agent. Once a capability is born, the proposal doesn't give Pillar 3 any language to narrow its operations by type.

The consequence for this plan: **any type-scoping we introduce now is a self-imposed constraint, not a proposal requirement.** If we narrow Pillar 3 to a subset of type×operation cells, we have to explain in Chapter 5 why we narrowed — the proposal doesn't hand us a free deferral.

## 4. What the code currently does (the drift)

Reading `src/optimizer/operations.py` directly, here is the actual state of each operation:

**Prune** works correctly for all three capability types. It iterates every active capability in the registry, skips any marked `pinned` (seeded entries), counts invocations in the last 30-day window via `MetricsStore.invocations_since()`, and deactivates capabilities with zero recent invocations. There is no type filter. This is the one Pillar 3 operation that matches the proposal faithfully.

**Merge** raises `ValueError` on any non-SKILL capability. Within SKILL, it calls the LLM with a structured-output schema (`MergeResult`) that contains only `name`, `description`, and `rationale` — no `skill_content` field — then builds a `CapabilityDescriptor` from those three fields plus the tags and domain inherited from the parents, registers the new (empty) descriptor, and deactivates both parents. The merged capability has no executable content because the schema never asked the LLM to produce any. A code comment at line 294 explicitly frames this as a feature: *"the merged CapabilityDescriptor is metadata-only (name + description). No generated code is produced, so the three-tier verification gate is not invoked here."* The comment is self-justifying the bug.

**Split** has the same pattern. It raises `ValueError` for MCP_TOOL, calls the LLM with `SplitResult` which contains only an analysis string and a list of `SplitVariant` objects (each with just a name and description — no code), builds metadata-only variant descriptors, registers them, and deactivates the parent. Same "no verification needed because no code" comment at line 176.

**Merge candidate selection** in `src/optimizer/loop.py identify_candidates_node` only checks description cosine similarity (≥ 0.5) within the low-reuse set. It does not check schema overlap, so the proposal's second conjunct (*"overlapping input schemas"*) is never evaluated — the fact that generated capabilities have empty I/O schemas today would make any Jaccard check vacuous anyway, so the missing conjunct and the missing schemas hide each other.

In summary, the Pillar 3 code has four compounding forms of drift:

1. **Schemas are empty** for generated capabilities (Iter-3 TODO at `tool_loader.py:310`), making the proposal's schema-overlap conjunct impossible to check.
2. **Merge and split produce metadata-only stubs** because their LLM-output schemas have no field for executable content, and the code comments frame this as intentional rather than as a missing feature.
3. **Type constraints in `operations.py` are narrower than the proposal** — `merge` raises for non-SKILL, `split` raises for MCP_TOOL — and this narrowing was never written down or justified. It appears to be a quiet Iter-4 implementation decision that hardened into the test suite (which only exercises the allowed types) and into the type guards (which raise on the disallowed ones).
4. **The integration scripts (`scripts/test_split_merge.py` and `scripts/test_auto_optimizer.py`) pass for the wrong reason** — they seed synthetic registries, trigger merge/split with real LLM calls, and assert that the parent is deactivated and the new capabilities appear in the registry, but they **never check whether the outputs contain executable content**. Against today's stub-producing `operations.py`, both scripts have been reporting "PASS" for months while the capabilities they were testing were empty descriptors. This means the drift propagated to **four** distinct places — source code, unit tests, integration scripts, and code comments — every one of them independently reinforcing the same original incomplete reading. This is a stronger form of the methodology finding than "code and tests drifted together": it's *"every artifact that touched the same mental model inherited the same unstated assumption, and each one made the others look correct."* A test suite alone is not sufficient to catch this kind of drift; even a multi-layer test suite with unit tests AND integration scripts AND code review is not sufficient if every layer was written from the same incomplete understanding. The only thing that caught it in the end was a direct read of the proposal text side-by-side with the `operations.py` source, which is what happened today. This is a Chapter 4 methodology contribution worth writing up in its own right.

## 5. The plan, in prose

There are three threads of work to finish before the Phase 5 eval can run. I'll describe each thread in plain language, then give the ordered fix list in section 6.

### Thread A — Pillar 3 proper (the main event)

The goal is to implement the three evolutionary operations for all three capability types, matching what the rolled-back proposal describes. Prune is already done. Merge and split need real implementations that produce executable content, verified through the same gate that Pillar 1 uses for generated capabilities.

**How merge will work, in prose.** Merge takes two or more low-reuse capabilities whose descriptions and I/O schemas overlap. It calls an LLM with a structured-output schema that is now extended to include the executable content field appropriate to the type being merged: a `combined_source_code` string for MCP tools, a `combined_skill_content` string (full SKILL.md including embedded code blocks) for skills, or a `combined_system_prompt` plus `combined_subagents` list for agents. The LLM receives the parents' executable content in the prompt and is asked to produce a single combined version that handles the union of the parents' input spaces — same as what the Pillar 1 generator does at initial creation time, just with multiple inputs instead of one gap specification.

The LLM's output is then built into a `GeneratedArtifact` (the same shape Pillar 1 generators produce) and routed through the existing three-tier verification gate: static scanner, behavioral testing, adversarial probing. If verification passes, the merged capability is registered and both parents are deactivated atomically. If verification fails, the merged capability is discarded and both parents stay active. Nothing enters the production registry without passing the same safety bar as freshly generated capabilities.

The only type-specific wrinkle is AGENT merge, because an agent's `agent_config` is composite (lead system prompt plus a subagent list plus tools plus skills). When two agents have overlapping subagent names, the LLM is instructed to recursively merge those subagents' prompts via text synthesis. When subagent names are disjoint, the merged agent just gets the union of both parents' subagents. If the combined subagent count exceeds a budget ceiling (around eight subagents), the merge is rejected up front and the parents stay active.

**How split will work, in prose.** Split takes one capability that has high reuse but mediocre success — meaning it fires often but fails often. It pulls a handful of recent failure cases from the metrics store (requires extending `InvocationRecord` to persist input arguments and outputs — a small schema migration). It then calls an LLM with a structured-output schema listing two to four variants, each with its own executable content and a description of the input subspace it's meant to handle. The LLM's job is to analyze the failure cases, identify distinct subspaces where the parent breaks, and produce narrower variants that each handle one subspace reliably.

Each variant is then verified two ways. First, it passes through the same three-tier gate as merge outputs — static scanner, behavioral, adversarial. Second, and this is new, all the variants collectively pass through a **coverage verifier** ("Tier 4") that actually runs them against the parent's recorded failure cases and checks that at least one variant now produces correct output for each failure case. If any variant fails the three-tier gate, or if the coverage verifier finds failure cases that none of the variants can handle, the entire split is rejected — none of the variants are registered and the parent stays active. Splits only commit when they demonstrably specialize where the parent was weak.

The coverage verifier is the only genuinely new piece of verification infrastructure. It reuses the existing execution backends (sandbox for MCP tools, constrained-LLM loop for skills, DeepAgent for agents) to actually run variants; it doesn't invent a new execution path. What's new is the combination of (a) pulling recorded failure cases from the metrics store and (b) using them as test inputs for an optimizer output. That combination is the "Pillar 2 meets Pillar 3" contribution of this thread, and it's worth a short section in Chapter 4.

**What this thread costs.** Roughly 14 to 16 hours of focused implementation, including: extending the merge and split Pydantic schemas with type-specific content fields (one pair per type, six schemas total), writing the per-type merge and split functions in `operations.py`, writing the shared `verify_and_register_or_rollback` helper, writing the Tier 4 coverage verifier, extending `InvocationRecord` with input/output fields and the DuckDB migration for it, adding `get_recent_failures()` to `MetricsStore`, removing the overly narrow type guards in `operations.py`, removing the SKILL-only filter in `identify_candidates_node`, rewriting the existing merge/split tests to assert on real executable content (they currently only check metadata), and writing tests for the coverage verifier.

### Thread B — B1 scaffolding (the originally-planned work)

This is the work that was queued before today started. It's unchanged by the Pillar 3 conversation except that some of it became prerequisites for Thread A.

**Wire the AST signature extractor into the loading pipeline.** The extractor (`src/orchestrator/schema_extraction.py`) already exists. It needs to be called at registration time in `loading_node.py` or `tool_loader.py` so that freshly generated MCP tools get their `input_schema` and `output_schema` populated from their source code. For SKILL and AGENT capabilities, the schemas come from the decomposer's `SubTask.output_schema` (which Phase 3.5c already produces) propagated through the generator onto the artifact. Effort: about 45 minutes.

**Add the schema Jaccard helper and the second merge-candidate conjunct.** A small helper function computes Jaccard similarity over the union of input-schema and output-schema keys between two capabilities. The merge candidate selector in `identify_candidates_node` gets a new gate: candidates must have description cosine at least 0.5 AND schema Jaccard in the range [0.5, 1.0). The exclusive upper bound is the safety rule you raised earlier — identical-schema capabilities may have different internal logic, and blocking their merge preserves diversity at the cost of some registry bloat. Effort: about 30 minutes.

**Snapshot and migrate `data/cli/chroma`.** Before running the verification experiment, tarball the current ChromaDB directory as a rollback point. Then run a one-off migration script that walks the registry, pulls `source_code` from each generated MCP tool, runs the AST extractor over it, and upserts the capability with populated schemas. Idempotent (skip capabilities that already have non-empty schemas). No migration needed for `data/eval/chroma` because the evaluation harness re-seeds per run. Effort: about one hour including the snapshot.

**Verification experiment.** Run merge-candidate identification against the migrated registry and count clusters. Minimum success criterion: the merge candidate count goes from zero (current state) to at least one. The expected candidates on the current 11-generated-capability CLI registry are the roughly three "parse CSV plus XLSX plus CSV" tools that share schema keys like `{row_count, column_names}`. This produces a Chapter 6 number. Effort: about 30 minutes.

**Unit tests for the new helpers.** Tests for `extract_mcp_tool_signature` covering the main syntactic cases, tests for `_schema_jaccard` covering the corner cases (both empty, one empty, full overlap, partial overlap, symmetry). Effort: about 45 minutes.

### Thread C — The remaining pre-eval items

**File output fix for SKILL/AGENT last steps.** Per `docs/file_output_fix_plan.md`. The shortest path is to restrict file output to MCP_TOOL last steps structurally: when the decomposer produces a chain whose last step is a SKILL or AGENT, the chain executor inserts a tiny "writer" MCP_TOOL step after it to convert the final JSON dict to the requested file format. This avoids plumbing workspace write access into the SKILL and AGENT execution backends, which would change their architecture. Effort: about one hour.

**Test augmentations from the behavioral audit.** Three clusters of findings from `docs/test_suite_behavioral_audit_2026_04_13.md` matter here: a small set of fail-open-contract tests (three tests in `test_gate.py`, `test_adversarial.py`, `test_behavioral.py` covering the never-raises paths that currently have no exception-path coverage), a composition per-type fixture routing test (one test verifying MCP_TOOL gets the workspace mount and SKILL/AGENT get the `fixture_contents` dict), and schema_context generator tests (one per generator, verifying Phase 3.5c chain contracts propagate into the prompt). Effort: about 80 minutes total.

**Phase 4 smoke tests.** After Threads A, B, and C all land: re-run `pytest tests/unit/`, `scripts/test_pipeline.py`, `scripts/test_verification.py`, `scripts/test_auto_optimizer.py` (this is the first real end-to-end test of the fixed merge/split paths through the pipeline), `scripts/test_mint.py`, and finally `scripts/run_pilot.py --all-tasks --condition full --n-runs 1` as a 20-task pilot with one run per task. Verify type spread, seeded matches, review a few LangFuse traces, spot-check `data/eval/metrics.duckdb`. Effort: about 1-2 hours of hands-on time plus wait time.

### Thread D — The actual evaluation

**Phase 5.** `uv run scripts/run_pilot.py --all-tasks --all-conditions --n-runs 5` produces the 400-record core eval. About 18 hours of wall time, about six dollars in API spend, unattended. Optionally followed by `scripts/run_longitudinal.py` for the optimizer convergence numbers, which is where Pillar 3 operations actually fire (the 400-record single-run eval resets the registry per run, so merge and split rarely trigger there).

## 6. The ordered fix list

Here is the actual dependency-ordered sequence. I've grouped it into three phases because different phases can commit separately and the commit history stays clean.

**Phase 1 — Housekeeping commit (no logic changes, ~0 hours — just commit what's in-tree).**
1. Commit today's in-tree work as low-risk baseline: seeds description fixes, schema field additions to `GeneratedArtifact`, AST extractor module (not yet wired), `scripts/test_registry_matching.py`, `scripts/run_task.py` deletion with `_format_json` refactor, the scripts cleanup docstring fixes, and the audit documents (`test_suite_audit`, `test_suite_behavioral_audit`, the four cluster reports, and this master plan). This gives B1 a clean starting line.

**Phase 2 — B1 scaffolding and Pillar 3 proper (~21–25 hours, this is the main block of work — includes the Step 3.5 domain-filter experiment).**

2. Wire the AST extractor into `loading_node.py` so freshly generated MCP tools get populated schemas, and propagate the decomposer's `output_schema` onto SKILL and AGENT artifacts. (~45 min)
3. Add the `_schema_jaccard` helper and the second merge-candidate conjunct with the [0.5, 1.0) gate in `loop.py`. Remove the SKILL-only filter. (~30 min)
3.5. **Domain-filtered hierarchical search experiment (contained, rollback-insured).** Tests whether adding a domain filter to `registry.search()` reduces cross-sub-domain false positives without regressing recall — directly addresses the Phase D silent-poisoning finding where `parse_sales_csv_and_products_xlsx_and_ta_a39982` fired at 0.634 on a transactions-domain paraphrase. Structured as an experiment, not a production change: infrastructure first (pure pass-through), diagnostic measurement second, production integration only if the numbers justify it. **Two distinct taxonomy layers** — the experiment uses a hardcoded taxonomy for measurement reproducibility; production uses an evolutionary taxonomy for open-vocabulary coverage across verticals.

  Substeps:
  - **(a)** Add `domain: str | None` parameter to `registry.search()` and `detect_gaps()` — pass-through with no effect when None. **DONE** — Commit 14.
  - **(a.5)** One-off migration: `scripts/migrate_business_domain_labels.py` classifies the 11 generated caps in `data/cli/chroma` into a hardcoded 13-category taxonomy (`sales`, `inventory`, `transactions`, `finance`, `fraud`, `operations`, `hr` + functional `data-processing`, `text-analysis`, `reporting`, `analytics`, `mathematics`, `general`). **Testbed-scoped for experiment reproducibility only — NOT a production mechanism.** DONE — migration applied, backup tarball created, sales/inventory/operations/finance/data-processing distribution confirmed.
  - **(b)** Add `infer_query_domain(query)` to `scripts/test_registry_matching.py` using the same hardcoded taxonomy. LLM call per unique query with structured output.
  - **(c)** Add Phase E to the diagnostic that re-runs Phase A + Phase D WITH and WITHOUT domain filtering per probe and compares the delta per probe (recall-with-filter vs recall-without, false-positive-rate delta).
  - **(d)** Run Phase E on `data/cli/chroma` (~20 min wall time, ~$0.50 LLM cost).
  - **(e)** **Decision gate**: if Phase A recall stays ≥ 95% of baseline AND Phase D cross-sub-domain false positives drop ≥ 50%, productionize via (3.5f); otherwise document as (3.5g).

  **(3.5f) — Production integration via evolutionary taxonomy (~4 hours, only if 3.5e passes).** The hardcoded taxonomy is discarded for production. In its place:
  - New module `src/orchestrator/taxonomy.py` implements `DomainTaxonomy(path)`: loads/saves a JSON file at `data/cli/domains.json` (CLI mode) or `data/eval/domains.json` (evaluation mode). Starts empty. Atomic writes via write-to-temp + rename. Locking via `fcntl.flock` for multi-process safety.
  - `DomainTaxonomy.get_or_create_for_capability(artifact, llm)`: LLM checks if any existing category fits; if yes returns it, if no proposes a new category and appends to the taxonomy file. Structured output forces the "prefer existing over new" discipline to prevent vocabulary drift.
  - `DomainTaxonomy.get_for_query(query, llm)`: LLM picks the best-matching existing category, or returns None (filter bypassed) if no category fits — open-vocabulary safe.
  - Generator integration in `mcp_tool.py`, `skill.py`, `agent.py`: after structured-output generation, call `get_or_create_for_capability` and set `artifact.domain`.
  - `gap_detection_node` integration: call `get_for_query(state.description)` and pass to `detect_gaps(queries, domain=...)`. If None, falls through to existing unfiltered behavior (no filter applied).
  - Tests: `tests/unit/test_taxonomy.py` covering empty-state behavior, add-new-category, reuse-existing-category, atomic write, concurrent write handling.
  - The `scripts/migrate_business_domain_labels.py` script is kept as a diagnostic for future experiment re-runs on fixed taxonomies, with docstring updated to reflect its diagnostic-only scope.

  Why evolutionary taxonomy: hardcoded business-analytics vocabulary does not generalize to other verticals MINT might be used for (medical research, legal document processing, factory simulation, etc.). The evolutionary mechanism grows the taxonomy organically as MINT encounters new kinds of tasks — the first capability adds the first category, new categories are added only when existing ones don't fit. Starts empty, stays coherent across sessions. Stronger thesis claim than a fixed list: the taxonomy itself is a second evolutionary mechanism alongside Pillar 3.

  **(3.5g) — Chapter 7 deferral (~30 min, only if 3.5e fails).** Keep `domain` parameter on `registry.search()` / `detect_gaps()` as forward-ready infrastructure. Keep the diagnostic Phase E for reproducibility. Keep the migration script as a one-off measurement tool. Document in Chapter 7 that the mechanism was evaluated on the Business Analytics testbed with a hardcoded taxonomy and did not improve precision by enough to productionize; the evolutionary taxonomy design is described as the intended production mechanism but deferred.

  **Total Step 3.5 effort:** ~3-5 hours for (b)-(e); +4 hours for (3.5f) if it passes; +30 min for (3.5g) if it fails. Net ~4-9 hours.
4. Extend `InvocationRecord` with `input_args`, `actual_output`, and `error_message` fields; write the DuckDB migration; add `get_recent_failures()` to `MetricsStore`. (~45 min)
5. Write the shared `verify_and_register_or_rollback` helper that routes a `GeneratedArtifact` through the existing three-tier gate and handles atomic register-or-rollback. (~45 min)
6. Write the per-type merge schemas and the three per-type merge functions (`_merge_tools`, `_merge_skills`, `_merge_agents`). Remove the type-narrowing `ValueError` guards. (~5 hours)
7. Write the Tier 4 coverage verifier (`src/verification/coverage.py`) including its integration with the three execution backends. (~1.5–2 hours)
8. Write the per-type split schemas and the three per-type split functions (`_split_tools`, `_split_skills`, `_split_agents`). (~5.5 hours)
9. Rewrite `test_optimizer_operations.py` to assert on real executable content and cover the rollback-on-verification-failure paths. Write `test_coverage.py` for the Tier 4 verifier. (~3 hours)
9.5. **Rewrite `scripts/test_split_merge.py` and `scripts/test_auto_optimizer.py` to assert on real content.** Both scripts currently seed synthetic registries, trigger merge/split with real LLM calls, and assert only that the parent is deactivated and new capabilities appear in the registry — they never check whether the outputs contain `source_code`, `skill_content`, or `agent_config`. Against today's stub-producing `operations.py` they report "PASS" while the outputs are empty. The rewrite: seed the test registries with capabilities that have **real executable content** (working SKILL.md with embedded Python, working MCP tool source, working agent config with subagents), for split also seed the metrics store with **real failure cases** using the new `InvocationRecord` schema, trigger the operations, and assert on the content of the outputs — non-empty executable fields, variants differing from the parent and from each other, merge outputs containing elements from both parents, verification-gate events recorded in metrics, Tier 4 coverage events for split. **Add a new rollback scenario** in each script that mocks the LLM to return a deliberately broken output, triggers the operation, and asserts the verification gate rejects it AND both parents stay active in the registry AND a `verification_rollback` event was recorded. This protects the atomicity claim that nothing enters the registry without passing the gate. These two scripts are the integration-level counterpart to the unit-test rewrite in step 9 — they're what exercises the full path through the LLM + verification gate + registry + metrics store, and they're what you'd run before Phase 4 smoke tests to sanity-check that Phase 2 didn't break anything end-to-end. (~2 hours)
10. Snapshot `data/cli/chroma` as a tarball, run the migration script to populate schemas on the existing 11 generated capabilities, run the verification experiment to confirm merge-candidate count increased from zero. (~1.5 hours)
11. Update `docs/proposal_vs_implementation_v2.md` flipping Pillar 3 Merge and Split back to ✅ and recording the methodology nugget about code, **unit tests, integration scripts, and comments** drifting together from the same incomplete reading. (~30 min)

**Phase 3 — Remaining pre-eval and validation (~3–4 hours).**

12. File output fix for SKILL/AGENT last steps per `docs/file_output_fix_plan.md`. (~1 hour)
13. Test augmentations from the behavioral audit: fail-open contract tests, composition per-type fixture routing test, schema_context generator tests. (~80 min)
14. Run Phase 4 smoke tests end to end: full `pytest tests/unit/`, `test_pipeline`, `test_verification`, `test_auto_optimizer`, `test_mint`, 20-task pilot at one run per task. (~1–2 hours hands-on plus wait time)

**Phase 4 — The actual evaluation (~18 hours unattended).**

15. `uv run scripts/run_pilot.py --all-tasks --all-conditions --n-runs 5` for the 400-record eval.
16. Optional: `uv run scripts/run_longitudinal.py` for optimizer convergence numbers over accumulated history.

**Phase 5 — Post-eval analysis (happens after eval lands).** Covered by `docs/pre_eval_execution_plan.md` and not re-specified here.

**Total hands-on time through Phase 3: roughly 21–24 hours, or about three focused working days.** Plus the Phase 4 evaluation wall time, which is unattended.

## 7. Open decisions that need your input

Three decisions are still yours to make. I am not going to guess on any of them.

**Time budget.** The 9/9 Pillar 3 plan described above is the most work of any version we discussed today — about three focused days before the eval. If your deadline is tighter, there are two honest fall-back versions: (a) implement merge for all three types but defer split entirely to Chapter 7, which saves roughly six to eight hours and requires re-titling Chapter 5.3 to *"Evolutionary Operations: Merge, Prune, and the Formal Framework for Split"*, or (b) implement merge for SKILL and AGENT only and defer both MCP_TOOL merge and all split, which saves another two hours but leaves Pillar 3 visibly narrower than the proposal describes. Option (a) is reasonable if time is tight; option (b) only makes sense if the deadline is very tight, because it means telling a half-story in Chapter 6.

**Thesis writing appetite for the Tier 4 coverage verifier.** The 9/9 plan introduces a new small piece of verification infrastructure that would need about a page of Chapter 4 writing to describe. If you don't want the writing burden, the fallback is "ship merge for all types, defer split" — which avoids Tier 4 entirely. If you're fine with the writing, Tier 4 is arguably the most interesting new thesis contribution of this whole B1 session (the Pillar 2 meets Pillar 3 integration story).

**Longitudinal run.** Pillar 3 operations mostly fire on accumulated history, which the single-run 400-record eval doesn't provide (the registry resets per run per condition). The longitudinal run is where merge and split actually trigger. It's optional in the current `pre_eval_execution_plan.md`. If you want real Chapter 6 numbers for Pillar 3, running it is worth about 30–50 additional tasks sequentially without a snapshot reset, which takes several more hours of wall time. If you skip it, Chapter 6 reports Pillar 3 operations as *"implemented and tested in isolation but rarely triggered in the 400-record eval due to per-run registry resets; longitudinal evaluation is deferred to future work."* That framing is honest but weaker.

## 8. Document map — what's in which existing doc

To help you find things without re-reading everything, here is what each existing doc in `docs/` currently covers. Docs marked **superseded** are preserved for git history but should be treated as frozen — use this master plan instead.

- **`thesis_proposal.md`** — The contract. The rolled-back original. Do not modify.
- **`proposal_vs_implementation_v2.md`** — Living audit of what each proposal claim maps to in the code. Will be updated in Phase 2 step 11 after the Pillar 3 fixes land.
- **`pre_eval_execution_plan.md`** — The long-form pre-eval checklist written before today. Phases 0–3.5c are already marked done there. Phases 4–7 (smoke tests, eval, post-eval analysis, chapter drafting) are still the authoritative reference for those later phases. This master plan doesn't replicate it.
- **`pre_eval_readiness.md`** — The short-form readiness checklist. Section E (test suite audit) was added earlier today and is now **superseded** by this master plan's Phase 2 and Phase 3 sections. The rest of the doc is still accurate for items that were already marked done.
- **`b1_enriched_embedding_plan.md`** — The B1 plan with today's pivot history and the Iter-4 gap discovery. **Superseded** by this master plan for the fix list, but still the best place to read about the embedding-enrichment experiment that got reverted and why.
- **`file_output_fix_plan.md`** — The SKILL/AGENT file output plan. Still live. Referenced from Phase 3 step 12.
- **`chain_schema_contracts_plan.md`** — Already implemented in Phase 3.5c. Historical.
- **`compositional_synthesis_plan.md`** — Already implemented in Phase 3. Historical.
- **`test_suite_audit_2026_04_13.md`** — Import-level test audit from today. Findings: no deletions needed. Superseded by the behavioral audit below. **Caveat noted 2026-04-13 post-audit:** this doc also contains the `scripts/` categorization where `test_auto_optimizer.py` was labeled "Critical — exercises merge/split via pipeline; directly tests Iter-4 Gap 2/3 fixes." That label was forward-looking (it described what the script *will* do once Phase 2 lands) and did not flag that the script *currently* passes for the wrong reason against today's stub-producing `operations.py`. The same blind spot applies to `scripts/test_split_merge.py`, which the earlier audit categorized as healthy. Both scripts are now known to suffer from the same false-pass bug as the unit tests, and both are rewritten in Phase 2 step 9.5. Consider the scripts-directory categorization in this doc **amended** by Section 4 point 4 of this master plan.
- **`test_suite_behavioral_audit_2026_04_13.md`** — Behavioral test audit from today. Five critical findings, drove the test-augmentation list in Phase 3 step 13. Note: this audit covered `tests/unit/` and `tests/integration/` but did not extend to `scripts/`, which is why the false-pass bug in `test_split_merge.py` and `test_auto_optimizer.py` was not caught here either. The behavioral audit's conclusion that *"the suite is testing the right things"* applies to the pytest suite; the integration scripts were out of scope.
- **`test_audit_cluster_{A,B,C,D}_2026_04_13.md`** — Detailed per-cluster findings that back the synthesis in the behavioral audit. Reference material; no action items come from them directly.
- **`test_mint_20260412_analysis.md`** — The 2026-04-12 test_mint run analysis (pre-B1). Historical.
- **`context_engineering_mapping.md`** — Reference mapping for the context engineering chapter. No action items.
- **`CLAUDE.md`** (project root, not in docs/) — Project context for AI assistants. Was edited earlier today to remove stale `run_task.py` references.
- **This document (`MASTER_PLAN_2026_04_13.md`)** — The single source of truth for the pre-eval fix sequence. Supersedes the sections noted above until Phase 5 eval lands.

## 9. What NOT to worry about right now

A few topics came up during today's conversation that are real but are not pre-eval actions. I'm listing them here so you know they're tracked but out of scope:

- **Cross-capability operations** (merging a SKILL with an AGENT, splitting a SKILL into a SKILL plus an MCP_TOOL, reclassifying a capability from one type to another at optimization time). Not in the proposal, not in the current plan. Goes into Chapter 7 future work with a brief sketch.
- **Behavioral differential as a merge gate.** An alternative to the schema-Jaccard gate where merge candidates are validated by running both parents on test inputs and checking whether their outputs diverge behaviorally. More rigorous than Jaccard, but roughly four additional hours of work that uses Pillar 2 Tier 2 infrastructure. Chapter 7 future work.
- **Full "inherits the strongest behavioral patterns from each parent" merge.** The proposal's wording implies an LLM that picks the best pieces from each parent's implementation. Our merge fix is close to this — the LLM sees all parents' content and produces a synthesized combination — but it doesn't explicitly run a behavioral voting mechanism. Chapter 7.
- **Principled split with broader LLM involvement.** The current split design uses one LLM call per split operation. A more ambitious version would iteratively refine variants across multiple LLM calls, potentially using the coverage verifier's results as feedback to improve variant coverage. Chapter 7.
- **MCP server deployment as real subprocess servers.** The current system uses importlib dynamic import + sandbox execution. Full MCP server deployment with subprocess lifecycle is Chapter 7.
- **The `test_optimizer_simulation.py` orphan.** Flagged in the import audit, still flagged in the behavioral audit. The decision about whether to keep `src/optimizer/simulation.py` and its tests depends on whether Chapter 6 cites the simulation harness. Defer to post-eval when the chapter is being drafted.
- **Chapter drafting itself (Phases 6–7 in `pre_eval_execution_plan.md`).** Not pre-eval work. Happens after the 400 records land.

---

## 10. What I'm asking you

The only thing I need from you before I start executing is the answer to the time-budget decision in Section 7. Concretely:

- **Do you have approximately three focused working days** (≈19–22 hands-on hours) for Phase 2 plus Phase 3 before starting the 18-hour eval? If yes, we ship the 9/9 Pillar 3 plan as described. I start by committing the Phase 1 housekeeping, then move into Phase 2 step 2.
- **If not, how many hours do you have?** I'll map that to one of the two fall-back options (merge-all-three-defer-split, or merge-SKILL-AGENT-only-defer-everything-else) and rewrite Phase 2 accordingly.

I won't touch `operations.py` or `loop.py` or start any Phase 2 work until you answer. I also won't write any more planning docs until the current plan is executed — this one is the ceiling.
