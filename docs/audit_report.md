# Codebase Audit Report

**Date:** 2026-04-06
**Scope:** Full source code, evaluation harness, CLI, scripts, sandbox
**Purpose:** Identify bugs and issues that could impact thesis results or MINT CLI correctness

**Remediation status (2026-04-06):** C1, C2, C3, H1, H4, H6, M4, M6, M8, M11, M13, L1, L2, L4, L8, L9, L11, L12, L14 — FIXED. C4, H2, H3, H5, H7 — thesis documentation. M1-M3, M5, M7, M9, M10, M12, L3, L5-L7, L10, L13 — accepted/deferred.

---

## Critical Issues (Could Invalidate or Bias Thesis Results)

### C1. T18 silently excluded from all analysis tables

**Files:** `scripts/analyze_results.py:34-35`

`ALL_TASK_IDS = [f"T{i:02d}" for i in range(1, 18)]` generates T01-T17 (17 tasks). But `tests/evaluation/tasks.py` includes T18 (record deduplication). All per-task analysis, synthesis-only aggregation, and Fisher's exact tests silently drop T18 data. If the re-run includes T18 records, they exist in DuckDB but are invisible in the analysis output.

**Impact:** T18 results are collected but never reported. Success/failure rates exclude 1/18 of the data.

**Fix:** Change `range(1, 18)` to `range(1, 19)`.

---

### C2. Token cost backfill has broken retrieval task filter

**File:** `scripts/backfill_token_costs.py:246`

`RETRIEVAL_TASKS & set()` always evaluates to an empty set (intersection with empty set). The retrieval-task exclusion from JIT token estimation never fires. For runs with < 13 tasks, all tasks (including retrieval tasks T01-T03 with ~0 token cost) are counted equally, deflating the per-JIT-task token estimate.

**Impact:** Per-task token costs for conditions with < 13 tasks are systematically underestimated. Run-level totals are correct; only per-task attribution is affected.

**Fix:** Replace `RETRIEVAL_TASKS & set()` with the actual set of retrieval task IDs from the run data.

---

### C3. No multiple testing correction on 51+ Fisher's exact tests

**File:** `scripts/analyze_results.py:263-272, 365-373`

Fisher's exact test is applied per-task (18 tasks x 3 pairwise comparisons = 54 tests) plus condition-level comparisons, with no Bonferroni, Holm, or FDR correction. At alpha=0.05 with 54 tests, ~2.7 false positives are expected by chance.

**Impact:** Some per-task "significant" differences may be spurious. The headline JIT-vs-static comparison (p<0.001) survives any correction, but per-task claims at p<0.05 may not.

**Fix:** Apply Holm-Bonferroni correction and report adjusted p-values.

---

### C4. Optimizer effectively untested in standard evaluation

**File:** `tests/evaluation/harness.py:256, 399`

`MetricsStore()` is created in-memory per run (no persistence). The `full` condition's optimizer sees zero invocation history. Prune requires history to identify unused capabilities, split requires reuse stats -- both are empty. The optimizer is effectively a no-op in the standard evaluation, which means the `full` vs `no_optimization` comparison is measuring overhead (extra node execution), not optimizer value.

This is acknowledged in the thesis text ("cold-start problem") but the evaluation design doesn't address it -- only the separate longitudinal test does. The standard evaluation's `full` condition data point is misleading.

**Impact:** The -10pp performance drop attributed to "the optimizer" is actually measuring optimizer overhead on an empty history, not optimizer decision quality.

**Fix:** Already mitigated by the longitudinal test. Document clearly in thesis that standard-evaluation `full` condition measures cold-start overhead only.

---

## High Severity (Affects Result Interpretation)

### H1. Double-counting deployed artifacts in `loading_node`

**File:** `src/orchestrator/loading_node.py:140`

```python
deployed = registered + len(non_mcp_artifacts)
```

`registered` already includes successfully registered non-MCP artifacts (the loop at lines 101-137 increments it for SKILL/AGENT). So non-MCP artifacts are counted twice. If all non-MCP registrations fail, `deployed = 0 + len(non_mcp_artifacts)` is > 0, falsely reporting COMPLETED instead of FAILED.

**Impact:** Silent false-COMPLETED when all SKILL/AGENT registrations fail. The pipeline reports success when it shouldn't.

**Fix:** `deployed = registered` (remove `+ len(non_mcp_artifacts)`).

---

### H2. Adversarial probing fail-open inflates gate_pass_rate

**File:** `src/verification/adversarial.py:195-228`

Both `_timeout_report` and `_error_report` set `overall_passed=True`. Any Tier 3 failure (LLM error, timeout, DeepAgent crash) results in the artifact passing. The thesis reports `gate_pass_rate` without distinguishing "actually passed Tier 3" from "Tier 3 errored out."

**Impact:** `gate_pass_rate` is an upper bound, not a true pass rate. Artifacts that were never properly adversarially tested are counted as passed.

**Fix:** Report Tier 3 completion rate as a separate metric. Track timeout/error counts in LangFuse.

---

### H3. Behavioral testing is a no-op for file-processing tools

**File:** `src/verification/behavioral.py` (general design)

Tier 2 generates synthetic scalar inputs (strings, ints, floats) but cannot provide actual files. For the Business Analytics Pipeline testbed where most tools process CSV/JSON files, the tool either errors (file not found) or returns trivially. Tier 2 cannot exercise the core file-processing logic.

**Impact:** Tier 2 adds cost but provides minimal verification value for the dominant task type. The thesis should quantify how many tasks had meaningful Tier 2 coverage vs. trivial pass-through.

---

### H4. `_MOCK_NAMES` regex lacks word boundaries

**File:** `src/verification/static_scanner.py` (the `_MOCK_NAMES` pattern)

`DEMO` (case-insensitive) matches inside `demographics`, `remediation`, etc. `sample` matches inside `resampler`. This causes false-positive CRITICAL findings that reject valid code.

**Impact:** False rejections of valid tools. Known to affect T04 and T07 in evaluation (documented in system_overview.md). Inflates the verification "false positive" count.

**Fix:** Add word boundaries `\b` to the regex pattern.

---

### H5. SKILL/AGENT artifacts always skip Tier 2, go straight to expensive Tier 3

**File:** `src/verification/gate.py:144` and `src/verification/behavioral.py`

`behavioral.run(artifact)` checks `artifact.source_code`. SKILL artifacts have `source_code=None` (content is in `skill_content`). So Tier 2 returns INFO "No source code to test" for all non-MCP_TOOL artifacts. The tiered cost optimization (skip Tier 3 if Tier 2 catches issues) never applies to SKILLs or AGENTs.

**Impact:** SKILLs and AGENTs always incur full Tier 3 cost. The thesis's per-artifact verification cost analysis should separate by type.

---

### H6. Simulation KL divergence direction is reversed vs online optimizer

**Files:** `src/optimizer/loop.py` vs `src/optimizer/simulation.py`

`loop.py` computes `KL(current || previous)` -- measures surprise of new data given old expectations. `simulation.py` computes `KL(previous || current)` via `np.histogram` -- measures the reverse. These are not interchangeable. They have different asymptotic properties and different sensitivities to tail events.

**Impact:** Convergence criteria are not directly comparable between simulation and production. Claims about simulation predicting real convergence are weakened.

**Fix:** Align both to use the same KL direction and document the choice.

---

### H7. Maintenance burden is always 0.0 in production

**File:** `src/registry/metrics.py`, `src/optimizer/utility.py`

Only test files record `"regenerated"` or `"patched"` events. No production code path ever records these event types. The gamma term in the utility function is dead weight:

```
U(c) = alpha * reuse * success - beta * token_cost - gamma * 0.0
```

**Impact:** The utility function in practice is two-term, not three-term. The thesis should acknowledge this or add event recording.

---

## Medium Severity (Correctness Risks)

### M1. Optimizer scores dict not thread-safe across concurrent pipeline invocations

**File:** `src/orchestrator/pipeline.py:127-138`

`_prev_optimizer_scores` is a `nonlocal` dict in the pipeline closure. Concurrent `pipeline.invoke()` calls share it without locking. Safe in current sequential usage but would break under concurrent MINT CLI usage.

---

### M2. Decomposition timeout leaks background threads

**File:** `src/orchestrator/decomposition.py:103-111, 147`

`pool.shutdown(wait=False)` after timeout does NOT cancel the running DeepAgent thread. The thread persists until the LLM `request_timeout=300s` expires. Multiple timeouts accumulate orphaned threads.

---

### M3. `_VALIDATE_MODULE` and classifier LLM singletons lack thread safety

**Files:** `src/orchestrator/generators/skill.py:41-63`, `src/orchestrator/classifier.py:30-44`

Both use lazy-initialized module-level globals without `threading.Lock`. Called from `ThreadPoolExecutor` workers. Could cause double-initialization under concurrent first-call scenarios. Other generators (`mcp_tool.py`, `agent.py`) correctly use double-checked locking.

---

### M4. Sandbox Phase 2 may run as root

**File:** `src/verification/sandbox_tool.py:211, 230`

Phase 1 (`pip install`) runs as `--user root`. `docker commit` captures that user context. Phase 2 does NOT specify `--user sandbox`, so untrusted code may execute as root inside the container. The `--network none` flag mitigates external exploitation, but root access could abuse `/proc` or kernel capabilities.

---

### M5. LLM import-to-PyPI cache poisoning persists per session

**File:** `src/verification/sandbox_tool.py:28`

`_pip_resolution_cache` is module-level. If the LLM incorrectly maps an import name (e.g., `sklearn` -> `sklearn` instead of `scikit-learn`), the wrong mapping persists for all subsequent sandbox executions in the session. Evaluation runs process many tasks sequentially, so one bad mapping can cascade.

---

### M6. `search()` returns deactivated capabilities without filtering

**File:** `src/registry/store.py:219`

ChromaDB search has no `where` filter for `is_active`. Deactivated capabilities appear in search results. `detect_gaps` handles this, but other callers (optimizer merge candidate search at `loop.py:204`) must manually filter.

---

### M7. Streaming mode in CLI may miss Command updates

**File:** `src/cli/runner.py:922`

`if isinstance(update, dict): accumulated.update(update)` -- LangGraph stream events can contain Command objects, not just dicts. If a node returns a Command, the update isn't accumulated. The non-streaming `pipeline.invoke()` path does not have this issue.

---

### M8. `verbose` flag has no effect in MINT CLI runner

**File:** `src/cli/runner.py:82-106`

`_setup_logging(verbose)` always sets the base level to `logging.WARNING` regardless of the `verbose` parameter. The parameter is accepted but never used. Compare with `scripts/run_task.py:47` which correctly branches on verbose.

---

### M9. Snapshot restore never verified post-restore

**File:** `tests/evaluation/snapshots.py:59-95`

`restore_clean_snapshot()` returns the original `snap_id` from `meta.json` without re-hashing the restored state. If `shutil.copytree` silently fails on any file, the restored state is corrupted but the harness proceeds with the stale snap_id, unaware of the corruption.

---

### M10. Split parent could also be a merge candidate in the same cycle

**File:** `src/optimizer/loop.py`

The optimizer's `apply_operations_node` prunes first, then splits, then merges. But there's no guard preventing a capability that was just split from also appearing as a merge candidate in the same cycle (if the similarity search finds it). This could lead to a split immediately followed by a merge of the new variants.

**Verification result: NOT A BUG.** Candidates are identified in `identify_candidates_node` *before* `apply_operations_node` runs. The candidate lists are frozen — newly created split variants don't appear in `merge_candidates` because the list was built before the split happened. Same-cycle overlap is impossible. In the *next* cycle, freshly split variants with zero reuse would correctly qualify as merge candidates — that's intended behavior (unused variants should be merge-eligible).

---

### M11. `except Exception: return None` not detected by static scanner

**File:** `src/verification/static_scanner.py`

The silent-exception check only flags `except: pass` and `except Exception: pass`. It does not detect `except Exception: return None`, `except Exception: return {}`, or `except Exception: return "default"`. These are common LLM patterns for silent failure.

---

### M12. Double-seeding after snapshot restore

**File:** `tests/evaluation/harness.py:249`

After restoring a snapshot (which already contains seeded capabilities), `seed_registry(registry)` is called again. If `seed_registry` is not idempotent, this creates duplicates.

---

### M13. `_extract_result` mislabels descriptor-only matches as `output_rejected`

**File:** `tests/evaluation/harness.py:582-589`

When a registry match has no `source_code` in metadata, the failure reason is `"output_rejected"`. The actual cause is "descriptor-only entry, no executable code." This could confuse failure-reason analysis.

---

## Low Severity (Minor / Documentation)

### L1. Stale docstrings: `recursion_limit=35` in code, actually 50

**Files:** `src/orchestrator/decomposition.py:9,77`, `src/verification/adversarial.py:15`

Constants are correctly 50; docstrings say 35. Misleading for thesis readers who inspect code.

---

### L2. `gap_detection_node` discards `reason` from `detect_gaps`

**File:** `src/orchestrator/gap_detection.py:52-53`

The `reason` string (e.g., "no match above threshold") is unpacked but never stored. Both `GapItem.description` and `GapItem.query` get the same `query` value.

---

### L3. Timezone-naive DuckDB vs timezone-aware Python datetimes

**File:** `src/registry/metrics.py`

DuckDB stores timestamps as timezone-naive. `InvocationRecord.invoked_at` defaults to `datetime.now(UTC)` (timezone-aware). The evaluation store explicitly handles this (`timestamp.replace(tzinfo=UTC)`) but the metrics store does not.

---

### L4. PILOT_TASKS comment is stale

**File:** `tests/evaluation/tasks.py:926`

`PILOT_TASKS = [T01, T07, T05]` with comment "retrieval + moderate JIT + complex JIT". Original pilot per CLAUDE.md was T01, T07, T10. The comment doesn't match the actual task set.

---

### L5. Pipeline closure captures `_gate_fn` by reference

**File:** `src/orchestrator/pipeline.py:99`

The lambda captures `_gate_fn` by reference. Currently safe because the variable isn't reassigned after the closure, but fragile -- any future code inserted between assignment and lambda usage would silently change behavior.

---

### L6. Non-deterministic error ordering from parallel generation

**File:** `src/orchestrator/generators/mcp_tool.py:351-357`

`as_completed` returns futures in completion order. Error messages in the state appear in non-deterministic order across runs, affecting error message reproducibility.

---

### L7. Skills/agent SKILL.md written to disk before verification

**Files:** `src/orchestrator/generators/skill.py:286-293`, `src/orchestrator/generators/agent.py:266-276`

Unverified skills temporarily exist on disk. If the pipeline crashes between generation and verification, stale files remain. By design per CLAUDE.md.

---

### L8. T17 validator doesn't handle stringified integer IDs

**File:** `tests/evaluation/tasks.py:826-838`

`isinstance(x, int)` check would fail on `"1042"`. Should also try `int(x)` conversion.

---

### L9. T04 validator uses `max(expected_cost, 1)` normalization

**File:** `tests/evaluation/tasks.py:187`

If `expected_cost` is between 0 and 1, normalization is incorrect. Should use `max(abs(expected_cost), 1)`.

---

### L10. `display.py` accesses `metrics_store._conn` directly

**File:** `src/cli/display.py:572`, `src/cli/runner.py:1045-1049`

Accesses private `_conn` attribute, breaking encapsulation. Would break silently if `MetricsStore` internals change.

---

### L11. `_save_output` CSV detection heuristic is unreliable

**File:** `src/cli/runner.py:649-664`

Detects CSV by checking for comma in first line. Matches any string with a comma ("Hello, world" -> saved as `.csv`).

---

### L12. `retry` command cannot override `enable_optimizer`

**File:** `src/cli/app.py:338-416`

No `--enable-optimizer` flag on retry. User must use `mint run` again to change optimizer setting.

---

### L13. Docker ephemeral image cleanup is silent on failure

**File:** `src/verification/sandbox_tool.py:281`

`docker rmi -f` in `finally` silently ignores failures. Accumulated ephemeral images waste disk over many runs.

---

### L14. Pooled JIT-vs-Static test inflates significance

**File:** `scripts/analyze_results.py:377-389`

Pooling three JIT conditions into one group triples the sample size and hides within-JIT variance. The resulting p-value is more significant than any single condition's comparison warrants.

---

## Thesis-Specific Recommendations

1. **Report Tier 3 completion rate separately** from gate_pass_rate (H2)
2. **Acknowledge two-term utility function** in practice (H7)
3. **Apply Holm-Bonferroni correction** to per-task significance tests (C3)
4. **Fix T18 exclusion** before reporting final numbers (C1)
5. **Separate verification cost analysis by artifact type** since SKILL/AGENT always skip Tier 2 (H5)
6. **Clarify KL divergence direction** and why simulation uses a different one (H6)
7. **Document behavioral testing limitation** for file-processing tools (H3)
8. **Acknowledge optimizer cold-start** in standard evaluation design (C4)
9. **Fix the loading_node double-count** before final evaluation re-run (H1)
10. **Add word boundaries to `_MOCK_NAMES`** to reduce false positives (H4)
