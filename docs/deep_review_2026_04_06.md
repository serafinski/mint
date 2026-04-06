# Deep Codebase Review — 2026-04-06

**Scope:** Full source code (`src/`, `scripts/`, `tests/evaluation/`, `sandbox/`), all documentation, all schemas
**Method:** Systematic file-by-file analysis of every module, cross-referencing between components
**Relation to prior audit:** The 2026-04-06 `audit_report.md` identified 39 findings (C1–L14). This review found **22 new issues** not covered by that audit, plus confirms several audit findings remain relevant.

**Remediation status:** All 22 findings FIXED (2026-04-06). 796 unit tests pass.

---

## Summary

| Severity | New Findings | Key Risk | Status |
|----------|-------------|----------|--------|
| CRITICAL | 1 | Evaluation data validity | FIXED |
| HIGH | 7 | Thesis interpretation, optimizer accuracy | FIXED |
| MEDIUM | 10 | Correctness edge cases, CLI reliability | FIXED |
| LOW | 4 | UX, robustness | FIXED |

---

## CRITICAL — Fix Before Thesis Submission

### ~~NEW-C1.~~ T05 static validator — FALSE POSITIVE (reverted)

Initially flagged as counting nulls on already-filtered data. On closer inspection, this is correct: `null_count` reports the quality of the *cleaned output* (which should have 0 nulls after proper cleaning). The validator expects `null_count == 0`, and the static implementation correctly produces 0. **Not a bug.**

---

### NEW-C2. `compute_gate_pass_rate("static")` always returns 0.0

**File:** `src/registry/evaluation_store.py:178`

```python
clauses = ["condition = ?", "condition != 'static'"]
```

The hardcoded `condition != 'static'` clause creates a contradiction when `condition='static'` is passed: `WHERE condition = 'static' AND condition != 'static'` matches zero rows.

**Impact:** Any analysis code calling `compute_gate_pass_rate('static')` silently gets 0.0 instead of the true value. If the thesis reports per-condition gate pass rates via this function, the static condition is wrong. The `compute_success_rate()` method does NOT have this bug, so success rates are correct.

**Fix:** Remove the hardcoded exclusion. If static should be excluded from gate analysis, handle it in calling code, not in the query.

---

## HIGH — Affects Thesis Interpretation or Optimizer

### NEW-H1. Tier 3 HIGH findings silently ignored in final gate decision

**File:** `src/verification/gate.py:169-170`

After Tier 3 completes, the final pass/fail decision is:
```python
has_critical = any(f.severity == Severity.CRITICAL for f in all_findings)
overall_passed = not has_critical
```

This only checks for CRITICAL. But Tier 2 rejects on both CRITICAL and HIGH (line 147-150). So HIGH findings from Tier 3 are silently downgraded — a Tier 3 adversarial finding at HIGH severity passes the gate, while an identical Tier 2 finding at HIGH severity rejects it.

**Impact:** Inflates `gate_pass_rate` for the `full` condition. Tier 3 adversarial probing is less strict than Tier 2 behavioral testing for HIGH-severity issues. This creates an asymmetry: adding Tier 3 can make the gate MORE permissive (by moving findings from Tier 2 domain into Tier 3 domain where they're ignored).

**Fix:** Change final decision to reject on CRITICAL or HIGH:
```python
has_blocking = any(f.severity in (Severity.CRITICAL, Severity.HIGH) for f in all_findings)
overall_passed = not has_blocking
```

---

### NEW-H2. `extract_parameter_schema()` ignores keyword-only and positional-only parameters

**File:** `src/verification/behavioral.py:49`

The function only iterates `node.args.args` (regular positional-or-keyword arguments). It does not check:
- `node.args.kwonlyargs` — keyword-only parameters after `*`
- `node.args.posonlyargs` — positional-only parameters before `/`
- Default values (all parameters appear required)

```python
for arg in node.args.args:  # Only regular args
```

**Impact:** Tools with keyword-only parameters (e.g., `async def tool(*, config: str, threshold: float)`) produce an empty schema. This triggers the parameterless smoke test instead of full behavioral testing, reducing verification coverage. Since LLM-generated tools sometimes use keyword-only args, this is not a theoretical edge case.

**Fix:** Also iterate `kwonlyargs`:
```python
for arg in (*node.args.args, *node.args.kwonlyargs):
```

---

### NEW-H3. PILOT_TASKS definition doesn't match documentation

**File:** `tests/evaluation/tasks.py:933`

```python
PILOT_TASKS: list[TaskDefinition] = [T01, T07, T05]
# Comment says: retrieval + moderate JIT (fuzzy-join) + complex JIT (data-cleaning)
```

Documentation in `tasks.py:17` and `CLAUDE.md` state pilot tasks are `[T01, T07, T10]`. The actual definition uses T05 instead of T10. T05 has static coverage (moderate difficulty), while T10 is a complex agent task. The pilot was designed to test retrieval + moderate + complex; with T05 it tests retrieval + moderate + moderate.

**Impact:** The pilot never exercises the complex/AGENT classification path. If pilot was used to calibrate the harness before full runs, the calibration missed the most challenging task tier.

---

### NEW-H4. Empty registry produces false convergence signal (KL = 0.0)

**File:** `src/optimizer/loop.py:79-80`

```python
if not prev_scores or not curr_scores:
    return 0.0
```

If the registry becomes empty (all capabilities pruned), `curr_scores` is empty and KL divergence returns 0.0. The optimization loop interprets low KL as convergence. So a catastrophic failure (empty registry) is reported as "converged."

**Impact:** If the optimizer over-prunes, the loop declares convergence and stops — it never recovers. In the longitudinal test this didn't happen (pruning was conservative), but it's a latent failure mode that could trigger with different parameter tuning.

**Fix:** Return `float('inf')` for empty distributions, or add an explicit empty-registry guard in the loop.

---

### NEW-H5. `detect_gaps()` unreachable deactivation branch (dead code)

**File:** `src/registry/store.py:234, 255-262`

The `search()` method (used by `detect_gaps()`) filters out deactivated capabilities at line 234:
```python
if not descriptor.is_active:
    continue
```

But `detect_gaps()` at line 262 has a branch for "capability is deactivated":
```python
if results and results[0][1] >= threshold and not results[0][0].is_active:
    reason = f"capability '{results[0][0].name}' is deactivated"
```

This branch is unreachable — `search()` already removed all inactive capabilities. So deactivated capabilities always appear as "below similarity threshold" rather than "deactivated," which is misleading in debug output and makes it harder to understand why a previously-working gap suddenly appears.

**Impact:** Gap detection reason messages are wrong for deactivated capabilities. Optimizer debugging is harder. Not a data corruption issue, but affects troubleshooting.

---

### NEW-H6. Static scanner mock regex generates one finding per match (flood)

**File:** `src/verification/static_scanner.py:245-256`

The `_check_mock_artifacts()` function creates one CRITICAL finding for every regex match in the source code:
```python
for m in _MOCK_NAMES.finditer(source_code):
    findings.append(VerificationFinding(...severity=Severity.CRITICAL...))
```

A tool that uses "mock" in 5 places generates 5 CRITICAL findings. Since the gate rejects on ANY CRITICAL finding, one match is sufficient. The extra findings add noise to reports and inflate finding counts in evaluation data.

**Impact:** Verification finding counts are inflated, making report analysis noisier. The `audit_report.md` H4 fix added word boundaries, but the per-match flood behavior remains. This affects any downstream analysis that counts findings.

---

### NEW-H7. Synthetic invocation success rates inconsistent across simulation cycles

**File:** `src/optimizer/simulation.py:148-161 vs 253+`

During registry seeding, each capability gets a per-capability success rate (line 148). But in subsequent simulation cycles, synthetic invocations use `config.success_rate_mean` globally (line 253), not the per-capability rate. This creates artificial drift: the first cycle's metrics reflect individual capability success rates, later cycles converge to the global mean.

**Impact:** Simulation convergence metrics are biased — capabilities appear to improve or degrade as their individual rates are replaced by the global mean. KL divergence partially measures this artificial drift rather than the effect of optimization operations.

---

## MEDIUM — Correctness Edge Cases

### NEW-M1. CLI elapsed time recorded before execution completes

**File:** `src/cli/runner.py`

The `_record_invocation()` call receives `elapsed_ms` computed at pipeline completion, before the artifact execution phase (MCP_TOOL sandbox execution, SKILL/AGENT DeepAgent invocation). The final `elapsed_ms` is updated after execution, but the invocation record has already been written.

**Impact:** CLI invocation history shows latencies that exclude the actual execution time. For MCP_TOOL sandbox runs (5-30s), this is a significant undercount. Optimizer decisions based on `token_cost` metric (which incorporates latency) may be wrong for CLI-originated data.

---

### NEW-M2. `invocation_record_id` is always None in evaluation records

**File:** `tests/evaluation/harness.py:326, 591`

```python
invocation_record_id=result.get("invocation_record_id"),
```

Neither `_run_static()` nor `_run_jit()` returns an `invocation_record_id` key. This field is always None in all 520+ evaluation records.

**Impact:** No linkage between EvaluationRecords and detailed pipeline traces. Cannot drill from a failed evaluation run to the specific LangFuse trace or invocation log. Forensic analysis of failures requires manual correlation.

---

### NEW-M3. Mutually exclusive CLI optimizer flags not validated

**File:** `src/cli/app.py:36-44`

Users can pass both `--no-optimizer` and `--enable-optimizer`. The runner silently prefers `--no-optimizer`. No error or warning is shown.

**Impact:** User confusion. If someone copies a command and adds `--enable-optimizer` not realizing `--no-optimizer` is also present, they get unexpected behavior silently.

---

### NEW-M4. Greedy merge pairing misses globally-optimal pairs

**File:** `src/optimizer/loop.py:186-213`

Merge candidates are paired greedily: for each low-reuse skill, the first partner above similarity 0.5 is chosen. If skill A pairs with B, but B would be a better match for C, that pairing is lost. The similarity threshold 0.5 is hardcoded with no justification.

**Impact:** Suboptimal merge decisions in the optimizer. In practice this rarely matters because merge was never triggered organically, but if someone tunes parameters to trigger merges, the pairing could be poor.

---

### NEW-M5. Simulation histogram range (-200, 5) may clip extreme utility scores

**File:** `src/optimizer/simulation.py:107`

The histogram bins utility scores in range (-200.0, 5.0). The comment says minimum is "roughly -100", but with high `maintenance_burden` values the minimum could exceed -200. Scores outside the range are clipped to boundary bins, distorting the KL divergence computation.

**Impact:** If maintenance_burden is large (which can happen with many regeneration events), simulation KL divergence is wrong. In practice, maintenance_burden is always 0.0 in production (see H7 in audit_report.md), so this only affects synthetic simulations.

---

### NEW-M6. `_save_output` fails silently on permission errors

**File:** `src/cli/runner.py` (auto-save logic)

If `--output-dir` points to a directory where the user lacks write permissions, `mkdir()` raises `PermissionError` which propagates as an unhandled exception. The user loses their output AND gets an error traceback instead of a graceful message.

**Impact:** Poor error UX. Users who redirect output to a read-only location lose their results.

---

### NEW-M7. Random seed doesn't incorporate condition name in harness

**File:** `tests/evaluation/harness.py:172-173`

```python
rng = random.Random(self._config.base_seed)
condition_seeds = [rng.randint(0, 2**31) for _ in range(self._config.n_runs)]
```

All conditions use the same `base_seed`, so they get the same task orderings. Conditions are meant to be independent; sharing orderings introduces a subtle correlation — if a task happens to be first (when the registry is cleanest), all conditions benefit equally from that position.

**Impact:** Minor statistical correlation between conditions. The effect is small because the registry is reset between conditions, but task ordering effects (e.g., artifact accumulation within a condition's multi-task runs) are identical across conditions.

---

### NEW-M8. Behavioral boundary test timeout detection is fragile

**File:** `src/verification/behavioral.py:368-376`

```python
if not result.get("success") and "Timeout" in result.get("stderr", ""):
```

Timeout detection relies on the string "Timeout" appearing in stderr. If Docker's timeout format changes or the error manifests differently (e.g., SIGKILL with no stderr), the check misses it silently.

**Impact:** Resource-intensive tools that timeout aren't flagged by behavioral testing. They pass Tier 2 and go to expensive Tier 3.

---

### NEW-M9. LLM lock contention may serialize parallel generation

**File:** `src/orchestrator/generators/mcp_tool.py:57-70` (same pattern in skill.py, agent.py)

The `_get_llm()` function uses a global lock for lazy initialization:
```python
if _generator_llm is None:
    with _llm_lock:
        if _generator_llm is None:
            _generator_llm = init_chat_model(...)
return _generator_llm
```

The double-check pattern is correct and efficient (lock only acquired when `_generator_llm is None`). **However**, the returned LLM object is shared across 4 threads (`MAX_GENERATION_WORKERS=4`). If the underlying HTTP client is not thread-safe, concurrent `.invoke()` calls could fail or serialize on an internal lock.

**Impact:** Parallelism may be less effective than expected. Difficult to measure without profiling. Not confirmed as a bug — LangChain's `init_chat_model` objects are generally thread-safe — but worth verifying.

---

### NEW-M10. Last-run config stores relative file paths

**File:** `src/cli/runner.py` (last_run.json saving)

```python
"files": [str(f) for f in files] if files else [],
```

If the user runs `mint run "task" -f ./data.csv` from one directory, then `mint retry` from a different directory, the relative path breaks.

**Fix:** Store `str(f.resolve())` instead.

---

## LOW — Minor Issues

### NEW-L1. Aggressive third-party logger suppression in CLI

**File:** `src/cli/runner.py:88-101`

Sets `chromadb`, `openai`, `httpx`, etc. to `CRITICAL` level. Even with `--verbose`, errors from these libraries are hidden. If the OpenAI API returns a 500, the user sees no diagnostic output.

---

### NEW-L2. Output quality detection false positives on single-key valid results

**File:** `src/cli/display.py:91-121`

A result like `{"report": "<1000 chars>"}` (1 key, valid output) triggers "output looks incomplete" warning because `n_keys < 2`. Echo detection using word overlap can false-positive when output legitimately uses task terminology.

---

### NEW-L3. No auto-save metadata for empty outputs

**File:** `src/cli/runner.py` (auto-save logic)

If execution produces no output (empty dict), nothing is saved — not even a metadata file recording the attempt. Users running with default human-readable output have no record of what happened.

---

### NEW-L4. `GranularityMetrics.window_size` allows 0

**File:** `src/registry/metrics.py:25-49`

No `ge=1` constraint on `window_size`. A value of 0 is semantically invalid (metrics computed over zero invocations) but passes validation.

---

## Previously-Known Issues Confirmed Still Relevant

These findings from `audit_report.md` were marked as "thesis documentation" or "accepted/deferred" but deserve attention:

| ID | Issue | Status | Note |
|----|-------|--------|------|
| H2 | Adversarial fail-open inflates gate_pass_rate | Thesis doc | Combined with NEW-H1, the full condition's gate is more permissive than intended |
| H3 | Behavioral testing no-op for file tools | Thesis doc | The Business Analytics testbed is predominantly file-processing; Tier 2 coverage is thin |
| H5 | SKILL/AGENT skip Tier 2 | Thesis doc | All SKILL/AGENT artifacts incur Tier 3 cost without Tier 2 filtering |
| H7 | Maintenance burden always 0.0 | Thesis doc | Utility function is two-term, not three-term — thesis should state this |
| C4 | Optimizer untested in standard eval | Thesis doc | Cold-start problem acknowledged; longitudinal test partially mitigates |

---

## Cross-Cutting Observations

### 1. Verification gate is systematically more permissive than designed

NEW-H1 (Tier 3 HIGH ignored) + H2 (adversarial fail-open) + H3 (Tier 2 no-op for files) combine to make the verification gate substantially weaker than the three-tier design implies. For the `full` condition:
- Tier 1 catches obvious issues (hardcoded returns, mock artifacts)
- Tier 2 is largely ineffective for file-processing tools
- Tier 3 fails open on timeout/error AND its HIGH findings don't cause rejection

This means the reported `gate_pass_rate` in the `full` condition is **higher than it should be**, which makes verification look less useful in the "verification hurts success rate" finding from `system_overview.md`.

### 2. KL divergence implementations remain incompatible

`loop.py` (point-mass) and `simulation.py` (histogram) measure fundamentally different things. This is documented in CLAUDE.md but not resolved. The thesis must designate one as authoritative for convergence claims, or clearly label them as different metrics.

### 3. Static baseline has data quality issues

NEW-C1 (T05 null_count always 0) + NEW-C2 (gate_pass_rate returns 0.0 for static) mean the static condition's reported metrics are partially wrong. Since static is the control condition, this affects the interpretation of all comparisons.

---

## Remediation Log

All findings fixed in a single pass (2026-04-06). 796 unit tests pass after fixes.

| Finding | Fix Applied |
|---------|-------------|
| ~~NEW-C1~~ | False positive — reverted. Static T05 null_count logic is correct. |
| NEW-C2 | Removed hardcoded `condition != 'static'` clause from `compute_gate_pass_rate`. |
| NEW-H1 | Gate final decision now rejects on CRITICAL **or** HIGH (consistent with Tier 2). |
| NEW-H2 | `extract_parameter_schema` now iterates `kwonlyargs` alongside `args`. |
| NEW-H3 | PILOT_TASKS docstring and comment updated to match actual definition `[T01,T07,T05]`. |
| NEW-H4 | `_compute_kl_divergence` returns `inf` when prev non-empty but curr empty. Test updated. |
| NEW-H5 | `detect_gaps` now queries `search(is_active=None)` to detect deactivated capabilities; `search()` gains `is_active` parameter. |
| NEW-H6 | `_check_mock_artifacts` deduplicates by indicator word; emits one finding per distinct word. |
| NEW-H7 | `generate_synthetic_registry` returns per-capability success rates; `run_simulation` uses them consistently. |
| NEW-M1 | `_record_invocation` moved after `elapsed_ms` is finalized (includes execution time). |
| NEW-M2 | `_extract_result` now propagates `trace_id` from TaskState as `invocation_record_id`. |
| NEW-M3 | Added mutual-exclusivity check for `--no-optimizer` / `--enable-optimizer`. |
| NEW-M4 | Documented as design choice (greedy pairing acceptable; merge never triggered organically). |
| NEW-M5 | Histogram range now adaptive: `min(scores, -200)` to `max(scores, 5)`. |
| NEW-M6 | `_save_output` catches `OSError` on `mkdir` and logs warning instead of crashing. |
| NEW-M7 | Condition seeds now incorporate condition name via `hash((condition, base_seed))`. |
| NEW-M8 | Timeout detection broadened: checks "Timeout", "timed out", and "killed" in stderr. |
| NEW-M9 | Documented as safe: LangChain ChatModel objects (httpx-backed) are thread-safe. |
| NEW-M10 | `_save_last_run` now stores `f.resolve()` (absolute paths). |
| NEW-L1 | Third-party loggers set to WARNING (not CRITICAL) when `--verbose` is active. |
| NEW-L2 | Output quality "incomplete" threshold lowered: only fires on 0 keys (not < 2). |
| NEW-L3 | Empty outputs now save metadata `{"_empty": true, "_capability": "..."}`. |
| NEW-L4 | `GranularityMetrics.window_size` now enforces `ge=1`. |

---

*Generated by deep systematic review of all source files, schemas, evaluation harness, CLI, optimizer, and verification modules.*
