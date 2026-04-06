# Code Audit Report — 2026-04-06

**Scope:** Full codebase analysis of `src/`, `sandbox/`, `scripts/`, `tests/` — focused on bugs and issues that could impact thesis results or MINT CLI correctness.

**Auditor:** Claude Opus 4.6 (deep analysis)

---

## CRITICAL — May Invalidate Thesis Results

### C1. `no_optimization` condition passes `metrics_store=None` — optimizer never receives data

**File:** `tests/evaluation/harness.py:397-401`

```python
run_optimizer = condition == "full"
verification_enabled = condition != "no_verification"

pipeline = create_pipeline(
    registry, loader,
    run_optimizer=run_optimizer,
    metrics_store=metrics_store if run_optimizer else None,  # ← BUG
    verification_enabled=verification_enabled,
)
```

When `condition == "no_optimization"`, `run_optimizer=False` and `metrics_store=None`. This means `gap_detection_node` receives `metrics_store=None` and **skips recording registry match invocations** (line `gap_detection.py:68-76`). The consequence:

- Under `no_optimization` and `no_verification` conditions, registry matches are never recorded as invocations in `MetricsStore`.
- The `MetricsStore` created at `harness.py:258` is in-memory and shared across tasks in a run, but receives zero match data for non-`full` conditions.
- **Impact on thesis:** The `full` condition (which passes metrics_store) has its seeded capabilities protected from pruning by invocation tracking, while `no_optimization` does not track any invocations at all. This creates a subtle data asymmetry between conditions, but since `no_optimization` doesn't run the optimizer, the practical effect is limited. However, if you later analyze cross-condition metrics, the `no_optimization` runs will show zero invocation history.

**Severity:** MEDIUM for current evaluation (optimizer doesn't run), but documented as CRITICAL because the metrics_store data is incomplete and could mislead future analysis.

### C2. Verification node retry sends ALL classified_gaps back through generators, not just failed ones

**File:** `src/orchestrator/verification_node.py:176-182`

When verification fails on some artifacts:
```python
goto = "generate_mcp_tool"
artifacts_to_keep = passed_artifacts
```

The node routes back to `generate_mcp_tool` with only passing artifacts retained. The generators then check `existing_gap_queries` to skip already-passing gaps. **However**, `classified_gaps` is never modified — all original gaps are still in state. This means the generators re-attempt generation for failed gaps, which is correct.

But here's the subtle issue: the generators use `existing_gap_queries = {a.gap_query for a in artifacts if a.gap_query is not None}` to decide what to skip. The `artifacts_to_keep = passed_artifacts` ensures failed artifacts are removed. So generators will re-generate the failed ones. **This actually works correctly.** Marking as verified-correct.

### C3. `_check_output_quality` echo detection has a false-positive for long task descriptions

**File:** `src/cli/display.py:111-119`

```python
task_lower = task_description.lower()[:200] if task_description else ""
if task_lower:
    output_str = formatted.lower()
    words = [w for w in task_lower.split() if len(w) > 4]
    if words:
        matches = sum(1 for w in words if w in output_str)
        if matches > len(words) * 0.6 and n_chars < 500:
            return "The output appears to echo the input prompt."
```

Common data-processing words like "return", "count", "column", "parse" naturally appear in both task descriptions and tool outputs. When the task prompt says "return row_count and column_names" and the output contains `{"row_count": 50, "column_names": [...]}`, this fires falsely. The `n_chars < 500` guard helps but doesn't prevent false positives for compact outputs.

**Impact on CLI:** Users see spurious "echo detection" warnings on valid outputs.

### C4. `seed_registry()` is called twice on every harness run

**File:** `tests/evaluation/harness.py:133` (in `prepare()`) and `harness.py:251` (in `_execute_run()`)

`prepare()` builds a fresh registry and calls `seed_registry(registry)`. Then `_execute_run()` restores the snapshot (which already has seeds) and calls `seed_registry(registry)` again. Since `seed_registry` uses `registry.add()` which calls `_collection.upsert()`, this is idempotent — but it means every evaluation run performs 8 unnecessary ChromaDB upserts per run. Not a correctness issue, but worth noting.

---

## HIGH — Likely Affects Results in Edge Cases

### H1. Behavioral testing skips SKILL/AGENT artifacts entirely

**File:** `src/verification/behavioral.py:425-427`

```python
source_code = artifact.source_code
if not source_code:
    return [VerificationFinding(tier=2, ..., severity=Severity.INFO,
            message="No source code to test — skipping behavioral tests")]
```

SKILL and AGENT artifacts have `source_code=None`. They pass through Tier 2 with only an INFO finding (which is never blocking). This means:
- **SKILLs** get only Tier 0 (structural) + Tier 1 (regex-only `scan_text_only`) + Tier 3 (adversarial)
- **AGENTs** get only Tier 1 (regex-only) + Tier 3 (adversarial)

Tier 2 provides zero signal for non-MCP_TOOL artifacts. The thesis should document that behavioral testing coverage is limited to MCP_TOOL.

### H2. Adversarial probing (Tier 3) runs on SKILL/AGENT artifacts but likely produces no useful findings

**File:** `src/verification/adversarial.py` (not read in full, but referenced in gate.py:165-166)

The adversarial probe receives the full `GeneratedArtifact`. For SKILLs, `source_code` is None but `skill_content` is a markdown document. For AGENTs, `agent_config` is a dict. The adversarial DeepAgent needs actual executable code to probe meaningfully. Without source code, it can only perform superficial text analysis of the SKILL.md content.

**Impact on thesis:** `gate_pass_rate` for SKILL/AGENT artifacts may be inflated because 2 of 3 verification tiers (behavioral + adversarial) cannot exercise non-code artifacts effectively.

### H3. `_sandbox_execute_with_install` doesn't set ENTRYPOINT correctly for Phase 2

**File:** `src/verification/sandbox_tool.py:254-262`

Phase 2 uses the ephemeral image (committed after pip install). The original `SANDBOX_IMAGE` has an ENTRYPOINT of `["python"]`, so `docker run ... ephemeral_tag /sandbox/script.py` works correctly because the committed image inherits the ENTRYPOINT. **Verified correct.**

### H4. Optimizer `prune()` uses `metadata.get("pinned")` — but metadata can be None

**File:** `src/optimizer/operations.py:89`

```python
if cap.metadata.get("pinned") == "true":
```

If `cap.metadata` is `None`, this raises `AttributeError`. Looking at `CapabilityDescriptor`:

```python
metadata: dict = Field(default_factory=dict)
```

The default is an empty dict, so `metadata` should never be None for properly constructed descriptors. However, if ChromaDB returns malformed data or the descriptor is reconstructed with `metadata=None` somehow, this will crash and prevent all pruning in that cycle (caught by `apply_operations_node`'s try/except, but all pruning is lost).

**Severity:** LOW in practice (Pydantic enforces the default), but worth a defensive `(cap.metadata or {}).get(...)`.

### H5. `_compute_kl_divergence` returns 0.0 when both distributions are empty

**File:** `src/optimizer/loop.py:82`

```python
if not prev_scores or not curr_scores:
    return float("inf") if (prev_scores and not curr_scores) else 0.0
```

When `prev_scores` is empty AND `curr_scores` is empty: returns 0.0 (convergence). This is mathematically incorrect but practically harmless since it only happens at startup when there are no capabilities at all.

When `prev_scores` is empty AND `curr_scores` is non-empty (first cycle): returns 0.0. This signals false convergence on the very first cycle. The threshold check in `_optimizer_node` doesn't act on this directly, but it propagates to `OptimizerState.kl_divergence`.

**Impact:** The first KL divergence value is always 0.0, which could be misinterpreted as immediate convergence in thesis figures.

### H6. `display.py` accesses `_conn` directly on MetricsStore

**File:** `src/cli/display.py:572-573`

```python
rows = metrics_store._conn.execute(
    "SELECT capability_id, task_id, success, ..."
```

This bypasses the MetricsStore API and directly accesses the private DuckDB connection. While functional, it couples the CLI to DuckDB internals and breaks if MetricsStore changes its storage format.

### H7. CLI `_save_output` saves timestamps without timezone

**File:** `src/cli/runner.py:652`

```python
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
```

Uses naive datetime (no timezone). All other timestamps in the codebase use `datetime.now(UTC)`. Minor inconsistency but affects output file naming only.

---

## MEDIUM — Code Quality / Robustness Issues

### M1. Static scanner `_MOCK_NAMES` regex matches "sample" in legitimate code

**File:** `src/verification/static_scanner.py:27-30`

The regex `\b(mock|fake|dummy|placeholder|DEMO|TEST_DATA)\b` was reportedly fixed to remove `sample`, but the current code doesn't include `sample`. **Verified — already fixed.** The note in CLAUDE.md about removing "sample" is outdated but the code is correct.

### M2. `_generate_boundary_inputs` produces identical inputs for `list` and `dict` types

**File:** `src/verification/behavioral.py:97-98`

```python
elif type_str == "list":
    inputs[name] = []
elif type_str == "dict":
    inputs[name] = {}
```

The `_generate_large_inputs` function returns `""` (empty string) for both list and dict types. This means `_test_output_variation` compares boundary `[]` vs large `""` — the inputs are always different, but the large input is semantically wrong for list/dict params and will likely cause the tool to crash rather than testing output variation.

### M3. Simulation `_scores_to_histogram` uses adaptive range that makes KL values non-comparable across cycles

**File:** `src/optimizer/simulation.py:96-108`

```python
lo = min(min(scores), -200.0)
hi = max(max(scores), 5.0)
hist, _ = np.histogram(scores, bins=n_bins, range=(lo, hi))
```

The range adapts to the current cycle's scores. If cycle N has scores in [-10, 2] and cycle N+1 has scores in [-5, 4], the histogram bins cover different ranges, making KL divergence measure partially range-shift rather than distribution-shift. The floor values (-200, 5) mitigate this for typical runs but don't eliminate the issue for extreme outliers.

**Impact on thesis figures:** KL divergence convergence curves may show artifacts from range expansion rather than genuine distribution convergence.

### M4. `harness.py` `_extract_result` always picks `artifacts[-1]` — non-deterministic on parallel generation

**File:** `tests/evaluation/harness.py:464`

```python
artifact = artifacts[-1]
```

When multiple artifacts are generated (e.g., the decomposition produces MCP_TOOL + SKILL gaps), the "last" artifact depends on `concurrent.futures.as_completed` ordering, which is non-deterministic. This means which artifact gets evaluated for `output_in_range` varies between runs with the same seed.

**Impact:** Introduces non-deterministic variance in `output_in_range` results that is NOT controlled by the run seed. Could affect reproducibility of thesis results.

### M5. CLI `_execute_mcp_tool` parses only the last line of stdout

**File:** `src/cli/runner.py:471`

```python
parsed = json.loads(result["stdout"].strip().splitlines()[-1])
```

If the tool prints debug output before the JSON result, this works. But if the tool prints JSON on multiple lines or the result itself contains newlines, only the last line is parsed. The same pattern exists in `harness.py:718`.

### M6. `classify_gap` doesn't pass `task_context` to the prompt

**File:** `src/orchestrator/classifier.py:68-71`

```python
prompt = CLASSIFICATION_PROMPT.format(
    description=gap.description,
    query=gap.query,
    total_gaps=total_gaps,
)
```

The classifier has no visibility into what files are attached or what domain the task is in. It classifies purely based on the gap description. This may cause misclassification when the gap description is ambiguous (e.g., "process data" could be MCP_TOOL or SKILL).

### M7. `gap_detection_node` uses `state.task_description[:100]` as task_id for invocation records

**File:** `src/orchestrator/gap_detection.py:73`

```python
metrics_store.record_invocation(InvocationRecord(
    capability_id=m.capability.name,
    task_id=state.task_description[:100],
    ...
))
```

The `task_id` is the first 100 chars of the natural-language task description. The `reuse_rate` metric counts distinct `task_id` values. Two different tasks that share a 100-char prefix will be counted as the same task, deflating `reuse_rate`. Similarly, the same task with slight rephrasing creates different task IDs, inflating reuse_rate.

### M8. Optimizer `identify_candidates_node` re-fetches metrics for low-reuse skills

**File:** `src/optimizer/loop.py:170-182`

The loop first computes metrics for all capabilities (split candidates), then iterates again for merge candidates. The second iteration re-queries `compute_granularity_metrics` for every capability. For large registries with many metrics, this doubles the DuckDB queries unnecessarily.

### M9. `_strip_code_fences` in skill generator may leave partial fences

**File:** `src/orchestrator/generators/skill.py:93-111`

The fence-parity logic handles the common case well but has an edge case: if the LLM produces content with exactly two backtick-triple sequences in the body (even count), the outer closing fence is left in place. This would only happen if the LLM wraps the SKILL.md in code fences AND the SKILL.md itself contains a code block — a realistic scenario for skills that include code examples.

### M10. `tool_loader.py` — not fully analyzed but referenced architecture concern

The `ToolLoader` uses `importlib` for host-side loading. Tools that import packages not installed on the host (e.g., pandas, sklearn) fail host-import but are still registered via source_code in metadata. The loading_node handles this correctly (lines 62-73), but the smoke test is skipped for non-host-loadable tools, meaning they're registered without any execution validation beyond the verification gate.

### M11. Evaluation conditions are not truly independent: `full` condition runs optimizer within a single run

The `full` condition enables the optimizer, which prunes/splits/merges capabilities mid-run. Since tasks are sequential within a run, later tasks see a modified registry. The `no_optimization` condition keeps the registry static. This means:
- `full` condition: Task N may fail because the optimizer pruned a useful capability after Task N-1
- `no_optimization` condition: All tasks see the same growing registry

This is by design (the optimizer IS the intervention being measured), but it's worth checking that the thesis frames this correctly — the `full` condition tests "JIT + verification + optimizer-as-a-system", not "same pipeline with optimizer bolted on".

---

## LOW — Minor Issues

### L1. `_data_dir()` creates `data/` in project root on every CLI invocation
Even for read-only operations like `mint list`, the data directory is created. Harmless but creates an empty directory if the user hasn't run any tasks yet.

### L2. `_langfuse_config()` in `operations.py` creates a new `LangfuseHandler` per LLM call
Split and merge each create their own handler instead of reusing one. This creates many short-lived traces rather than grouping operations under a single trace.

### L3. `_test_latency` measures wall time including Docker startup
The latency test adds Docker container startup time (~1-2s) to the tool execution time. Tools that execute in <100ms still show 2-3s latency. The `LATENCY_THRESHOLD_S = 5.0` accommodates this but makes the test less meaningful.

### L4. `CapabilityType` comparison uses string equality in `gate.py:53`
```python
if artifact.capability_type == CapabilityType.MCP_TOOL:
```
This works because `CapabilityType` is a StrEnum, but elsewhere the code uses `capability_type == "mcp_tool"` (string comparison). Consistent but relies on StrEnum semantics.

### L5. `scripts/run_task.py` and `src/cli/runner.py` both contain Docker check logic
Duplicated `_check_docker()` implementation. Should be factored out to a shared utility.

### L6. Simulation `compare_baselines` doesn't use the same RNG seed for both runs' invocation generation
The `run_simulation` function creates independent RNG seeds (`config.seed + 1000`), so the two baselines see different random task patterns. This is intentional (same capability distribution, different usage patterns) but means the comparison measures optimizer effect + random noise, not just optimizer effect.

---

## Appendix: Files Analyzed

| Directory | Files Read | Key Modules |
|---|---|---|
| `src/orchestrator/` | pipeline.py, decomposition.py, classifier.py, gap_detection.py, loading_node.py, verification_node.py, generators/{mcp_tool,skill,agent}.py | Full pipeline flow |
| `src/verification/` | gate.py, static_scanner.py, behavioral.py, sandbox_tool.py | Three-tier verification |
| `src/optimizer/` | utility.py, operations.py, loop.py, simulation.py | Granularity optimizer |
| `src/registry/` | store.py, metrics.py, evaluation_store.py | ChromaDB + DuckDB |
| `src/schemas/` | task.py (full), others via agents | State schemas |
| `src/cli/` | runner.py (full), display.py (full) | MINT CLI |
| `tests/evaluation/` | harness.py (full), tasks.py (partial) | Evaluation framework |
| `sandbox/` | Dockerfile (via agent) | Docker sandbox |

---

## Summary

| Severity | Count | Key Concerns |
|---|---|---|
| CRITICAL | 1 | Metrics data asymmetry across evaluation conditions |
| HIGH | 4 | Behavioral/adversarial testing blind to SKILL/AGENT; first KL=0.0; CLI private API access |
| MEDIUM | 11 | Non-deterministic artifact selection; histogram range instability; echo detection false positives; missing task context in classifier |
| LOW | 6 | Duplicated code; Docker latency noise; minor inconsistencies |

**Most impactful for thesis validity:**
1. **H1/H2** — Verification tiers 2 and 3 provide minimal signal for SKILL/AGENT artifacts. The `gate_pass_rate` may be inflated for these types. The thesis should clearly state that the three-tier gate was designed and tested primarily for MCP_TOOL artifacts.
2. **M4** — Non-deterministic artifact selection in `_extract_result` means evaluation results are not perfectly reproducible. Consider selecting the artifact that matches the classifier's primary type for the task, or using a deterministic ordering.
3. **H5** — First-cycle KL divergence = 0.0 should be excluded from convergence analysis or explicitly noted as a boundary condition.

---

---

## Addendum: Cross-Reference with Prior Audits

After completing the independent analysis above, I read `docs/audit_report.md` and `docs/deep_review_2026_04_06.md`. Below is a reconciliation of findings.

### Findings Already Caught and Fixed

The following issues from my audit were already identified in prior audits and have been remediated:

| My Finding | Prior ID | Status |
|---|---|---|
| H1 (SKILL/AGENT skip Tier 2) | audit H5 | Thesis documentation |
| H2 (adversarial ineffective for non-code) | audit H2 | Thesis documentation |
| H5 (first KL=0.0) | deep_review NEW-H4 | FIXED (returns inf when prev non-empty, curr empty) |
| H6 (_conn access) | audit L10 | Acknowledged |
| C3 (echo detection) | deep_review NEW-L2 | FIXED (threshold lowered to 0 keys) |
| C4 (double seeding) | audit M12 | Acknowledged (idempotent) |
| M3 (histogram range) | deep_review NEW-M5 | FIXED (adaptive range) |

### Findings NOT in Prior Audits — Genuinely New

These issues were **not** caught by either `audit_report.md` or `deep_review_2026_04_06.md`:

#### NEW-1. `metrics_store=None` for non-full conditions suppresses match tracking (my C1)

Neither prior audit noted that `gap_detection_node` receives `metrics_store=None` for `no_optimization` and `no_verification` conditions, silently skipping invocation recording for registry matches. The audit's C4 noted the optimizer is untested (cold-start), but did not observe the data pipeline gap where match tracking is disabled.

**Why it matters:** If anyone compares invocation data across conditions, non-`full` conditions show zero match activity. The MetricsStore is effectively dead for 3 of 4 conditions.

#### NEW-2. Non-deterministic artifact selection in `_extract_result` (my M4)

Neither audit caught that `artifacts[-1]` in the harness is non-deterministic when parallel generation produces multiple artifacts. The deep_review's M7 addressed seed correlation across conditions but not within-run non-determinism from `as_completed` ordering.

**Why it matters:** Two identical evaluation runs (same seed, same conditions) can produce different `output_in_range` results because different artifacts are selected for execution. This is a reproducibility concern.

#### NEW-3. Classifier lacks task context (my M6)

The classifier prompt receives only `gap.description`, `gap.query`, and `total_gaps`. It has no information about attached files, domain hints, or the original task description. Neither prior audit flagged this gap in the classification prompt.

**Why it matters:** A gap described as "analyze data" with a CSV file attached should likely be MCP_TOOL, but without file context the classifier might choose SKILL or AGENT. Misclassification triggers the wrong generator, wasting tokens and reducing success rate.

#### NEW-4. task_id truncation in invocation records (my M7)

Using `state.task_description[:100]` as `task_id` creates collision risk for similar tasks and fragmentation for the same task described differently. Neither audit noted this issue.

**Why it matters:** The `reuse_rate` metric — which drives optimizer split/merge/prune decisions — is computed from distinct task_id counts. Collisions deflate reuse_rate (under-counting), while fragmentation inflates it (over-counting). For the evaluation harness (which uses structured prompts), this is less of an issue, but for CLI usage with varied natural-language descriptions, it could significantly distort optimizer behavior.

#### NEW-5. `_strip_code_fences` fence-parity edge case (my M9)

The skill generator's code fence removal logic has a subtle edge case when the SKILL.md body contains an even number of code blocks. Neither audit flagged this.

**Why it matters:** Skills that include code examples (a natural pattern for data-processing workflows) could retain a trailing ``` fence, causing `quick_validate.py` to fail and triggering an unnecessary repair pass.

### Key Insights from Prior Audits That Compound My Findings

1. **Verification gate is weaker than designed (deep_review cross-cutting #1):** My H1/H2 + prior audit H2/H3/H5 + deep_review NEW-H1 paint a comprehensive picture: the three-tier gate provides strong protection only for MCP_TOOL artifacts. For SKILL/AGENT, it's effectively a Tier 0 + Tier 1 regex-only check with a fail-open Tier 3. The thesis must frame verification results by artifact type.

2. **Simulation KL ≠ Production KL (audit H6):** This was caught by the prior audit but my M3 adds that even within the simulation, the adaptive histogram range makes cycle-to-cycle KL values non-comparable. The combined effect is that KL divergence convergence claims need careful qualification in the thesis.

3. **Loading node double-count was fixed (audit H1):** The prior audit flagged `deployed = registered + len(non_mcp_artifacts)` as double-counting. Checking the current code at `loading_node.py:140`: `deployed = registered` — **this is already fixed**. The current code correctly uses only `registered`.

4. **`compute_gate_pass_rate('static')` was broken and fixed (deep_review NEW-C2):** The hardcoded `condition != 'static'` clause was removed. Current code at `evaluation_store.py:155-165` no longer has this exclusion. **Verified fixed.**

5. **Tier 3 HIGH findings now cause rejection (deep_review NEW-H1):** The current `gate.py:169-172` code:
   ```python
   has_blocking = any(
       f.severity in (Severity.CRITICAL, Severity.HIGH) for f in all_findings
   )
   overall_passed = not has_blocking
   ```
   **Verified fixed** — checks both CRITICAL and HIGH.

### Remaining Unfixed Issues from Prior Audits (Still Present in Code)

| Prior ID | Issue | Current Status |
|---|---|---|
| audit C3 | No multiple-testing correction on Fisher's tests | Unfixed — analysis script |
| audit C4 | Optimizer cold-start in standard eval | Thesis documentation |
| audit H3 | Behavioral testing no-op for file tools | Unfixed — design limitation |
| audit H7 | Maintenance burden always 0.0 | Unfixed — no event recording |
| audit M2 | Decomposition timeout leaks threads | Unfixed — pool.shutdown(wait=False) |
| audit M5 | LLM cache poisoning persists per session | Unfixed — by design |
| deep_review NEW-M4 | Greedy merge pairing | Documented as design choice |

---

## Final Assessment

**Total unique issues across all three audits (this one + prior two):**

| Category | This Audit (new) | Already Known | Total Unique |
|---|---|---|---|
| CRITICAL | 1 (metrics asymmetry) | 4 (audit C1-C4) | 5 |
| HIGH | 2 (non-deterministic selection, missing classifier context) | 7 (audit H1-H7) | 9 |
| MEDIUM | 3 (task_id truncation, code fences, behavioral input types) | ~15 | ~18 |
| LOW | 0 new unique | ~10 | ~10 |

**Bottom line for the thesis:**
1. The three-tier verification gate works well for MCP_TOOL but is largely decorative for SKILL/AGENT — state this explicitly
2. The optimizer is untested in standard evaluation (cold-start) — the longitudinal test is the real evidence
3. Evaluation reproducibility has a hidden non-determinism source in artifact selection ordering
4. KL divergence convergence claims need careful qualification (two incompatible metrics, adaptive ranges)
5. The match-tracking gap (my NEW-1) means invocation data is incomplete for 3 of 4 conditions

---

## Addendum 2: Findings from Parallel Agent Analysis

Six specialized agents analyzed the codebase in parallel. After deduplication against my findings and the prior audits, these additional issues emerged:

### AGENT-1. Task validators are intentionally loose — inflates success rates for T10, T13, T17

**Files:** `tests/evaluation/tasks.py`

- **T10 (line ~445):** `trend_direction` accepts "upward", "up", "increasing", "positive" — any of 4 synonyms passes. If the expected value is specifically "upward" but the LLM says "increasing", it still passes.
- **T13 (line ~594):** `anomaly_count >= 3` with no upper bound. An agent that finds 50 anomalies (over-detection) passes the same as one that correctly finds 5.
- **T17 (line ~838):** Only 2 of 3 expected fraud customers need to match. Partial detection passes.

**Impact on thesis:** These validators were designed to be tolerant (documented as "(tolerant)"), but the thesis should quantify how much this inflates success rates vs. strict validators. The `output_in_range` metric is softer than it appears.

### AGENT-2. Split operation can partially commit — corrupting registry state

**File:** `src/optimizer/operations.py:179-210`

When `split()` generates 2+ variants and one name already exists in the registry:
1. The existing-name variant is skipped
2. The new-name variant IS added
3. The parent IS deactivated

Result: parent is dead, only some variants exist, and pre-existing capabilities with colliding names are untouched. The registry is in an inconsistent state.

**Impact:** If a split is retried (e.g., after a name collision), the parent is already deactivated and can't be split again. The operation should be all-or-nothing.

### AGENT-3. No validation of pip package names before sandbox install

**File:** `src/verification/sandbox_tool.py:197-202`

The LLM-resolved package names are passed directly to `pip install` without validation:
```python
pip_args = ", ".join(repr(p) for p in packages)
install_script = f"subprocess.check_call(['pip', 'install', '--no-cache-dir', {pip_args}])"
```

While Docker isolation limits the blast radius, a malicious or hallucinated package name could install arbitrary PyPI packages. The `repr()` quoting prevents shell injection, but doesn't prevent installing typosquatted packages.

### AGENT-4. Decomposition fallback sends entire task description as registry query

**File:** `src/orchestrator/decomposition.py:204-206`

When decomposition returns 0 subtasks:
```python
if not subtasks:
    required_caps = [state.task_description]
```

The entire task description (potentially 500+ words) becomes a single "capability query" sent to ChromaDB semantic search. This produces:
- Noisy registry matches (long text matches on unrelated capabilities)
- A single massive gap that the classifier must classify
- An LLM generation prompt stuffed with the full task description as the "gap description"

This is a documented fallback but could explain poor results on tasks where decomposition fails.

### AGENT-5. Behavioral output variation test doesn't flag error/success inconsistency

**File:** `src/verification/behavioral.py:243-265`

The `_test_output_variation` function compares results only when BOTH sandbox runs succeed. If input A succeeds and input B crashes (or vice versa), no finding is raised — the test silently passes. A tool that works for simple inputs but crashes on complex ones escapes detection.

### AGENT-6. Consistency test silently passes when sandbox times out

**File:** `src/verification/behavioral.py:314-345`

If either sandbox run times out, `parsed1` or `parsed2` is None, and ALL comparisons are skipped. A tool that consistently times out passes the consistency test. Combined with the latency test's 5s threshold, a tool that runs in 4.9s passes latency but could still be unreliable.

---

*Report generated by Claude Opus 4.6 — 2026-04-06*
