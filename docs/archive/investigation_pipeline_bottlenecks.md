# Pipeline Performance Investigation Report

**Date:** 2026-03-11
**Branch:** `iter/5-Integration-and-ETE-Testing`
**Context:** Pilot evaluation (T01, T07, T10) under `full` condition takes 85+ minutes for a single task (T07). Expected total pilot time ~15-20 min. Switching from DeepSeek to GPT-5-mini for DeepAgent components improved decomposition (120s -> 30s) but did not materially reduce total runtime because the bottleneck is architectural, not model speed.

---

## Executive Summary

Six compounding architectural issues cause the pilot to take 2+ hours instead of ~20 minutes. The root cause is **over-decomposition** (Issue 1), which multiplies the cost of every downstream issue. A single task (T07 fuzzy join) gets decomposed into 14 fine-grained capabilities, each requiring generation, classification, and three-tier verification. Combined with full regeneration on retry, sequential processing, and optimizer pruning, the pipeline spends ~85 minutes on T07 alone before even starting T01 or T10.

**Estimated time breakdown for T07 (single task, `full` condition):**

| Phase | Time per attempt | Attempts | Total |
|-------|-----------------|----------|-------|
| Decomposition | 30s | 1 | 0.5 min |
| Gap detection + Classification | 45s | 3 | 2.3 min |
| Generation (9 MCP + 2 SKILL, sequential) | 5 min | 3 | 15 min |
| Verification (10-14 artifacts, sequential, ~2 min each) | 20-28 min | 3 | 60-84 min |
| Loading + Optimizer | 1 min | 1 | 1 min |
| **Total for T07 alone** | | | **~80-100 min** |

---

## Issue 1: Over-Decomposition (CRITICAL)

**Location:** `src/orchestrator/prompts/decomposition.py:23`, `src/orchestrator/decomposition.py:65-176`

**What happens:** The decomposition prompt instructs the DeepAgent to "break the task into the smallest meaningful subtasks" with a soft guideline of "aim for 2-8 subtasks." For T07 (fuzzy join two CSVs on company_name), the decomposer produces 4 subtasks with **14 required capabilities**: file I/O, lowercase text, trim whitespace, remove business suffixes (regex), create normalized key column, fuzzy string matching, compute similarity scores, join datasets based on best match, apply similarity threshold / tie-breaking, construct joined result rows, count matched rows, count null/empty values in a column, read CSV file, parse CSV.

**Why it matters:** This is the root of all performance problems. Every downstream phase (classification, generation, verification) scales linearly or worse with the number of capabilities. The task should produce 1-2 capabilities (e.g., a single `fuzzy-join` MCP tool), not 14.

**Root cause:** The decomposition prompt optimizes for "smallest meaningful" granularity without considering pipeline cost. The DeepAgent (now GPT-5-mini) tends to enumerate every logical step as a separate capability. The few-shot example in the prompt shows 3 subtasks for a simpler task, but there's no hard upper bound or cost signal.

**Evidence from logs:**
```
Gap detection: 3 matches, 11 gaps from 14 required capabilities
  ✗ gap: file I/O
  ✗ gap: lowercase text
  ✗ gap: trim whitespace
  ✗ gap: remove business suffixes (regex)
  ...
```

---

## Issue 2: Full Regeneration on Retry (CRITICAL)

**Location:** `src/orchestrator/verification_node.py:150-162`, `src/orchestrator/generators/mcp_tool.py:306-325`

**What happens:** When verification fails for 2 out of 10 artifacts, the pipeline routes back to the generator nodes. The generators iterate over **all** `state.classified_gaps` (not just the failed ones) and regenerate every artifact. The "replace by capability_name" logic (mcp_tool.py:324) is meant to deduplicate, but since all gaps are regenerated, every LLM call is repeated.

**Flow:**
1. Attempt 1: Generate 11 artifacts -> Verify 10 -> 2 fail, 8 pass
2. Attempt 2: Regenerate ALL 11 gaps (not just 2 failed) -> Verify 13 (accumulated) -> 3 fail
3. Attempt 3: Regenerate ALL 11 gaps again -> Verify 14 -> 3 still fail -> fail open

**Why it matters:** Instead of 11 + 2 + 3 = 16 generation calls, the pipeline makes 11 + 11 + 11 = 33. Combined with sequential verification of the full artifact list each time, this triples the runtime.

**Root cause:** `verification_node` sets `state.generated_artifacts = passed_artifacts` on retry but leaves `state.classified_gaps` unchanged. Generator nodes filter gaps by `classified_type` only, not by whether the corresponding artifact already passed verification.

---

## Issue 3: Sequential Verification (HIGH)

**Location:** `src/orchestrator/verification_node.py:102-109`, `src/verification/gate.py:84-179`

**What happens:** The verification node processes artifacts in a sequential `for` loop. Each artifact goes through Tier 1 (static scan, ~0s), Tier 2 (behavioral testing, ~10-30s), and Tier 3 (adversarial probing, ~100-180s). With 14 artifacts, one verification round takes 20-28 minutes.

```python
# verification_node.py:102-109
for artifact in state.generated_artifacts:
    report = gate_fn(artifact)  # Blocks until complete
    new_reports.append(report)
    if report.overall_passed:
        passed_artifacts.append(artifact)
    else:
        failed_artifacts.append(artifact)
```

**Why it matters:** Verification is the dominant time cost. 14 artifacts x 2 min x 3 attempts = ~84 minutes. Parallelizing even partially (e.g., 4 workers) would cut this to ~21 minutes.

**Root cause:** No `concurrent.futures` parallelization within the verification node. The decomposition node already uses `ThreadPoolExecutor` for its agent call — the same pattern could be applied here. Each `gate_fn(artifact)` call is independent and could run in parallel (Docker sandbox calls are isolated).

**Caveat:** Parallel Docker sandbox executions may contend for resources, so the speedup won't be perfectly linear. But even 3-4x improvement is significant.

---

## Issue 4: Optimizer Prunes Seeded Capabilities (HIGH)

**Location:** `src/optimizer/operations.py:69-94`, `src/registry/metrics.py` (invocations_since), `src/orchestrator/pipeline.py:111-143`

**What happens:** After Task 1 completes, the optimizer runs `prune()` which deactivates all capabilities with zero invocations in the last 30 days. This includes **all seeded capabilities** (csv-parser, json-validator, text-statistics, data-cleaning-workflow, report-generation, date-formatter, data-analysis-agent) because the metrics store only tracks MCP tool invocations via `loading_node`, not registry matches from `gap_detection`.

**Evidence from logs:**
```
Pruned capability 'csv-parser' (zero invocations in 30d)
Pruned capability 'json-validator' (zero invocations in 30d)
Pruned capability 'text-statistics' (zero invocations in 30d)
Pruned capability 'data-cleaning-workflow' (zero invocations in 30d)
Pruned capability 'report-generation' (zero invocations in 30d)
```

**Why it matters:** After T07 finishes, the registry is empty. T01 (which should be trivial — seeded csv-parser exists) now has to generate everything from scratch. The entire reuse cluster benefit (T01, T04, T05, T07 sharing csv-parser) is destroyed.

**Root cause:** The metrics store distinguishes between "invocations" (MCP tool executions via loader) and "matches" (registry searches in gap_detection). Only invocations are tracked. Seeded capabilities that are matched but not invoked appear idle and get pruned. Additionally, newly generated capabilities are pruned immediately after registration because they've never been invoked.

---

## Issue 5: Sequential Generation (MEDIUM)

**Location:** `src/orchestrator/generators/mcp_tool.py:317-327`, `src/orchestrator/generators/skill.py:319-329`

**What happens:** Both generators process gaps in a sequential for loop, making one LLM call at a time (~25-30s each). For 9 MCP tool gaps + 2 skill gaps = 11 sequential calls = ~5 minutes per generation cycle.

**Why it matters:** With 3 attempts, that's ~15 minutes of sequential generation. Parallelizing to 4-8 workers could reduce this to ~2-4 minutes total.

**Root cause:** Same as Issue 3 — no `concurrent.futures` within the node. Each `generate_single_tool()` call is independent (different gap, different LLM call) and could run in parallel.

---

## Issue 6: Artifact Accumulation Across Retries (MEDIUM)

**Location:** `src/orchestrator/generators/mcp_tool.py:319-325`, `src/orchestrator/verification_node.py:150-162`

**What happens:** The logs show increasing artifact counts: attempt 1 verifies 10, attempt 2 verifies 13, attempt 3 verifies 14. This happens because the LLM generates different names for the same capability on different attempts. The "replace by capability_name" logic only deduplicates exact name matches.

**Example:** Gap "fuzzy string matching" might produce:
- Attempt 1: `fuzzy_string_matching` (name A)
- Attempt 2: `fuzzy_string_matcher` (name B) — different name, so both kept
- Attempt 3: `fuzzy_match_strings` (name C) — all three kept

**Why it matters:** More artifacts = more verification calls = more time. By attempt 3, the verification node may be verifying 14+ artifacts instead of just the 2-3 that originally failed.

**Root cause:** LLM name generation is non-deterministic. The generators don't enforce stable naming based on the gap query. The `capability_name` in the LLM prompt is a suggestion, not a constraint.

---

## Impact Matrix

| Issue | Per-task impact | Multiplier across 3 tasks | Fix complexity |
|-------|----------------|---------------------------|----------------|
| 1. Over-decomposition | +60-70 min | x3 = 180-210 min | Low (prompt tuning) |
| 2. Full regeneration | +30-40 min | x3 = 90-120 min | Medium (state tracking) |
| 3. Sequential verification | +40-50 min | x3 = 120-150 min | Medium (ThreadPoolExecutor) |
| 4. Optimizer pruning | +20-30 min (on T01, T10) | x2 tasks affected | Low (invocation tracking or prune exemption) |
| 5. Sequential generation | +10-15 min | x3 = 30-45 min | Low (ThreadPoolExecutor) |
| 6. Artifact accumulation | +5-10 min | x3 = 15-30 min | Medium (stable naming) |

---

## Recommended Fix Priority

### Phase 1: Quick wins (could reduce pilot from 2h+ to ~30-40 min)

1. **Fix decomposition prompt** — Add hard upper bound: "Produce at most 3 subtasks and at most 5 required capabilities total. Prefer one capability that does the whole job over many small ones." This alone could reduce T07 from 14 capabilities to 1-2.

2. **Exempt seeded capabilities from pruning** — Add a `pinned: bool` field or check `invocations_since == 0 AND registered_at < snapshot_time` to skip pruning for seeded entries.

3. **Track registry matches as invocations** — In `gap_detection_node`, call `metrics_store.record_invocation()` for matched capabilities so they're not pruned.

### Phase 2: Structural improvements (could reduce pilot to ~15-20 min)

4. **Only regenerate failed artifacts on retry** — In `verification_node`, track which gaps failed and pass only those to generators. Keep passed artifacts unchanged.

5. **Parallelize verification within node** — Use `concurrent.futures.ThreadPoolExecutor(max_workers=4)` in `verification_node` to run up to 4 `gate_fn()` calls concurrently.

6. **Parallelize generation within node** — Same pattern for `generate_mcp_tool` and `generate_skill`.

### Phase 3: Refinements

7. **Enforce stable capability naming** — Derive name deterministically from gap query (e.g., slugify the query) rather than letting the LLM choose.

8. **Add reasoning_effort="low" to GPT-5-mini** — Reduce reasoning token overhead in adversarial prober (~10-20s savings per probe).

---

## Thesis Implications

These findings are relevant for the thesis evaluation:

- The **`full` condition** (all three pillars active) is disproportionately expensive due to the verification + optimizer interaction. This is a legitimate finding about the cost of autonomous verification.
- The **`no_verification` condition** would skip Issues 3, 6, and part of Issue 2, making it significantly faster. This contrast between conditions is itself a thesis result.
- The **`static` condition** (pre-built capabilities, no generation) avoids Issues 1-6 entirely and serves as the performance baseline.
- The over-decomposition problem (Issue 1) is a finding about **granularity control** — directly relevant to Pillar 3 (optimizer). The system generates too many fine-grained capabilities when it should produce fewer coarse-grained ones.

---

*Report prepared from codebase analysis and LangFuse trace inspection. All file references are relative to project root.*
