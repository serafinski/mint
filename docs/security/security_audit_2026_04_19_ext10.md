# Security / Robustness Audit — 2026-04-19, Tenth Extension (#98-#108)

**Parent document:** [`security_audit_2026_04_19.md`](./security_audit_2026_04_19.md) — #1-#18 all ✅ FIXED 2026-04-20.
**Sibling extensions:**
- [`security_audit_2026_04_19_ext4.md`](./security_audit_2026_04_19_ext4.md) — #12-#14 ✅ (folded into parent).
- [`security_audit_2026_04_19_ext5.md`](./security_audit_2026_04_19_ext5.md) — #15-#18 ✅ (folded into parent).
- [`security_audit_2026_04_19_ext6.md`](./security_audit_2026_04_19_ext6.md) — #19-#43 ✅ all FIXED 2026-04-20.
- [`security_audit_2026_04_19_ext7.md`](./security_audit_2026_04_19_ext7.md) — #44-#55 ✅ all FIXED 2026-04-20.
- [`security_audit_2026_04_19_ext8.md`](./security_audit_2026_04_19_ext8.md) — #56-#78 open.
- [`security_audit_2026_04_19_ext9.md`](./security_audit_2026_04_19_ext9.md) — #79-#97 open.

**This extension's scope.** A reviewer submitted 40 claims (their local numbering #101-#140). **6 confirmed** as genuine new findings and logged as #98-#103 below. **34 are duplicates of prior extensions, not-reproducible, documented design choices, or overstated** — logged in §"Claims not added" with specific evidence.

**Status at time of write (2026-04-20):** all eleven **open** (first batch 6 + second batch 2 + third batch 3). ext8 remains 23 open; ext9 remains 19 open. **Open total after ext10 lands: 53 findings (ext8 + ext9 + ext10).**

| # | User's # | Severity | Scope | Impact |
|---|---|---|---|---|
| 98 | 110 | MEDIUM | `behavioral.py::_generate_negative_inputs` only tests `-1`/`-1.0` | Large-negative, NaN, and inf not tested; common numeric crash-reasons in generated tools escape Tier 2 probes |
| 99 | 111 | MEDIUM | `adversarial.py` hardcoded `reasoning_effort="low"` | Red-team probes may miss sophisticated deceptive behavior; Pillar 2 Tier 3 coverage quality is capped |
| 100 | 117 | **P0** | `analyze_results.py::print_token_cost_summary` excludes zero-cost tasks from avg | `avg_per_jit_task` is inflated; Chapter 6 per-task cost systematically over-reported |
| 101 | 128 | MEDIUM | `generators/utils.py::find_subtask_for_gap` returns first-matching subtask only | Capabilities reused in multiple subtasks generate without upstream_data support for non-first positions |
| 102 | 129 | MEDIUM | `generators/utils.py::build_schema_context` doesn't deduplicate upstream schema-key collisions | Two predecessors with same output key → duplicate listing in generator prompt → code-generator confusion |
| 103 | 136 | **P0** | `run_pilot.py` calls `harness.run_condition` with no timeout | A single hanging Docker container blocks the 18-hour campaign indefinitely with no visible signal |
| 104 | 142+143+144 | **P0** | `tracing.py` global trace context shared across threads; `composition.py` singleton trace_id lookup | ThreadPool workers read the global dict after other threads wipe-and-replace it; generation events cross-attributed between concurrent tasks |
| 105 | 146 | MEDIUM | `backfill_token_costs.py` ±5-minute window buffer collides between back-to-back runs | Main eval + longitudinal runs started within the buffer have token costs cross-attributed |
| 106 | 161+164 | **P0** | `_sandbox_execute_with_install` creates fresh ephemeral image + pip install PER PROBE | 6 Tier 2 probes per task × 30-60s install each = 3-6 min/task pure install overhead; drives Tier 2 timeouts |
| 107 | 168 | MEDIUM | `analyze_longitudinal.py` computes Gini on CUMULATIVE counts per cycle | Cumulative anchoring artificially stabilizes Gini over time; hides per-cycle instability signal |
| 108 | 182 | MEDIUM | `step_results` list ordered by execution (topological), not by subtask index | Current consumers use `[-1]` (safe), but positional access `step_results[0]` on non-linear DAGs would misattribute |

**Numbering.** Continues sequentially from ext9's #97. User's local numbering preserved in "User's #" column. Three same-day batches: #98-#103, #104-#105, #106-#108.

---

## Issue #98 — `_generate_negative_inputs` only tests `-1`/`-1.0`; Tier 2 misses large-negative, NaN, inf (MEDIUM)

### Summary

`src/verification/behavioral.py:205-215` generates negative/edge-case inputs for numeric parameters:

```python
def _generate_negative_inputs(schema: dict[str, dict]) -> dict[str, Any]:
    """Generate negative / edge-case inputs for numeric params."""
    inputs = {}
    for name, info in schema.items():
        type_str = info.get("type", "unknown")
        if type_str == "int":
            inputs[name] = -1
        elif type_str == "float":
            inputs[name] = -1.0
        ...
```

Only probes `-1` / `-1.0`. Missing:
- **Very large negatives** (e.g., `-2**31`, `-sys.maxsize`) — common crash for `int` tools that assume non-negative
- **NaN** (`float("nan")`) — a classic source of silent-propagation bugs in float-heavy tools
- **Infinity** (`float("inf")`, `float("-inf")`) — distinct failure mode from bounded numbers

Tier 2's negative-input probe tests only the mildest edge. Tools that happen to handle `-1` gracefully but crash on `-2**31` or NaN slip through Tier 2 and get registered.

### Where

`src/verification/behavioral.py:205-215` (`_generate_negative_inputs`).

### Remediation

**A. Expand the negative-input generator with additional edge cases.**

```python
import math
def _generate_negative_inputs(schema: dict[str, dict]) -> dict[str, Any]:
    """Generate multiple negative / edge-case input variants."""
    variants = []
    for name, info in schema.items():
        type_str = info.get("type", "unknown")
        if type_str == "int":
            variants.append({name: -1})
            variants.append({name: -(2**31)})
            variants.append({name: -(2**62)})
        elif type_str == "float":
            variants.append({name: -1.0})
            variants.append({name: float("-inf")})
            variants.append({name: float("nan")})
            variants.append({name: -1e308})  # near-underflow
    return variants  # return list of variants; caller runs each
```

Change the caller to iterate variants and aggregate findings.

**B. Regression test.**

```python
def test_negative_input_probe_includes_nan_and_inf():
    variants = _generate_negative_inputs({"x": {"type": "float"}})
    assert any(math.isnan(v["x"]) for v in variants)
    assert any(math.isinf(v["x"]) for v in variants)
```

### Recommendation

**MEDIUM — Tier 2 coverage polish.** ~20 LOC + 1 test. ~30 min. Post-fix, Chapter 4.5 probe-coverage disclosure can include "tested against {-1, -2³¹, -2⁶², NaN, inf, -inf}" as a proposal-strengthening addendum.

---

## Issue #99 — `adversarial.py` hardcodes `reasoning_effort="low"`; Tier 3 red-team quality capped (MEDIUM)

### Summary

`src/verification/adversarial.py:354`:

```python
model=init_chat_model(DEFAULT_LLM_MODEL, reasoning_effort="low"),
```

The adversarial DeepAgent's model always uses `reasoning_effort="low"`. No per-artifact escalation, no cost-quality tradeoff. This was presumably chosen to keep Tier 3 costs bounded (~$0.01-0.02 per artifact vs ~$0.05-0.10 at "high"), but it means:

- Sophisticated deceptive behavior (contamination patterns that require multi-step reasoning to detect) may slip through.
- Per line 317 docstring, the hardcoded setting is deliberate for cost reasons — but there's no knob to override for critical artifacts.
- The user-submitted concern: "may produce low-quality probes that miss sophisticated deceptive behavior (mocks)."

Chapter 6's Pillar 2 M2 contamination rate reflects Tier 3's detection ceiling. If "low" reasoning misses contamination that "medium"/"high" would catch, M2 is systematically understated.

### Where

- **Hardcoded setting:** `src/verification/adversarial.py:354` + comment at line 720 (`"reasoning_effort": "low", # hardcoded in _create_adversarial_agent`).
- **Docstring acknowledgement:** line 317 — explicitly notes the three-attack-pattern design assumes low reasoning.

### Remediation

**A. Parameterize via config.**

```python
# src/config.py — add:
ADVERSARIAL_REASONING_EFFORT = os.environ.get("JIT_ADVERSARIAL_REASONING", "low")

# adversarial.py:354 — use it:
model=init_chat_model(DEFAULT_LLM_MODEL, reasoning_effort=ADVERSARIAL_REASONING_EFFORT),
```

Default stays "low" (cost-bounded Phase 5 eval); researchers can override for ablation studies.

**B. Ablation during Phase 5.**

Run a small subset (~20 artifacts) at both "low" and "high" reasoning; measure the delta in M2 findings. Chapter 4.5 reports "reasoning-effort ablation: X% detection gap between low and high." Strengthens the methodology disclosure.

### Recommendation

**MEDIUM — land with Phase 5 if researcher wants the ablation data.** ~5 LOC + env var. ~15 min. Ablation adds ~$0.10 / 20 artifacts.

---

## Issue #100 — `print_token_cost_summary` excludes zero-cost tasks from `avg_per_jit_task`; Chapter 6 per-task cost over-reported (P0)

### Summary

`scripts/analyze_results.py:583-585`:

```sql
SELECT condition,
       SUM(token_cost) AS total_tokens,
       AVG(CASE WHEN token_cost > 0 THEN token_cost ELSE NULL END) AS avg_per_jit_task,
       COUNT(CASE WHEN token_cost > 0 THEN 1 END) AS n_with_cost,
       ...
```

The `avg_per_jit_task` aggregate ONLY averages across tasks with `token_cost > 0`. Tasks with `token_cost = 0` (retrieval tasks, static-matched tasks, tasks that failed before any LLM call) are EXCLUDED from the denominator.

Result: the reported "average JIT task cost" is the average of ONLY the tasks that cost something. Real per-task-across-the-condition cost is lower than reported. For a condition with 20 tasks where 10 cost money and 10 are free (retrieval), reported avg is 2× reality.

### Where

- **Biased AVG filter:** `scripts/analyze_results.py:584`.
- **Related `n_with_cost` column:** line 585 reports the filter count, so a careful reader CAN back it out — but the headline avg column is misleading.

### Reproduction

```
20 tasks × "full" condition:
  10 tasks cost 1000 tokens each → total = 10000
  10 tasks cost 0 tokens → total = 0
  
Total run tokens = 10000
Actual avg per task = 10000 / 20 = 500
Current reported avg_per_jit_task = AVG(WHEN token_cost > 0) = 10000 / 10 = 1000  ← 2× real
```

### Threat model / impact

- **Chapter 6 M4 per-task cost over-reported.** A reviewer comparing JIT vs static on per-task cost sees JIT as 2× more expensive than reality.
- **Direction of bias: anti-JIT.** Static condition has more zero-cost rows (all retrieval-task-via-registry-match → 0); excluding them inflates static's per-task cost too, but not as much as JIT's.
- **Compounds with ext8 #58** (retrieval-task exclusion gaps in backfill): #58 is about backfill populating token_cost incorrectly; #100 is about analyzer averaging over the wrong subset.

### Remediation

**A. Use `AVG(token_cost)` over all rows, not conditional.**

```sql
-- Before:
AVG(CASE WHEN token_cost > 0 THEN token_cost ELSE NULL END) AS avg_per_jit_task,

-- After:
AVG(token_cost) AS avg_per_task,  -- averages over all rows, including zero-cost
AVG(CASE WHEN token_cost > 0 THEN token_cost ELSE NULL END) AS avg_per_nonzero_task,  -- current, renamed
```

Report BOTH columns so the reader can see the difference.

**B. Regression test.**

```python
def test_avg_per_jit_task_counts_zero_cost_rows():
    # Seed DB with 10 zero-cost + 10 thousand-cost.
    # Assert avg_per_task == 500.0 (not 1000.0).
    ...
```

### Recommendation

**P0 — land before Chapter 6 M4 table is generated.** 3-line SQL change + 1 test. ~10 min.

### Legacy data

Re-run `analyze_results.py` post-fix against the same DB → corrected M4 numbers. No data migration needed.

---

## Issue #101 — `find_subtask_for_gap` returns the first matching subtask only; capabilities reused across multiple subtasks generate without proper upstream_data support (MEDIUM)

### Summary

`src/orchestrator/generators/utils.py:84-96`:

```python
def find_subtask_for_gap(gap, subtasks) -> SubTask | None:
    for st in subtasks:
        if gap.query in st.required_capabilities:
            return st
    return None
```

Returns the FIRST subtask containing the gap's query. If a capability (gap) is referenced by MULTIPLE subtasks (e.g., a file-reader reused across steps), only the first subtask's context is used by the generator.

Downstream consumers (`is_first_step_for_gap`, `build_schema_context`) rely on this function. Consequence: when a capability is reused across step positions (Step 0 AND Step 3), the generator sees ONLY Step 0's context — producing a tool signature that doesn't accept `upstream_data`. The Step 3 invocation fails.

### Where

- **First-only lookup:** `generators/utils.py:84-96`.
- **Consumer (depends on correct match):** `is_first_step_for_gap` at line 99-113.
- **Consumer (depends on correct match):** `build_schema_context` at line 116+.

### Reproduction

```python
subtasks = [
    SubTask(required_capabilities=["read_csv"], depends_on=[], ...),  # Step 0
    SubTask(required_capabilities=["filter_rows"], depends_on=[0], ...),  # Step 1
    SubTask(required_capabilities=["read_csv"], depends_on=[1], ...),  # Step 2 — REUSES read_csv
]

gap = ClassifiedGap(query="read_csv", ...)
find_subtask_for_gap(gap, subtasks)  # → returns subtasks[0] (Step 0)
is_first_step_for_gap(gap, subtasks)  # → True (first match has empty depends_on)

# Generator builds "read_csv" as a Step 0 tool — no upstream_data support.
# Step 2's invocation calls read_csv(upstream_data=step1_output) → TypeError.
```

### Threat model / impact

- **Capability reuse across step positions fails silently at generation time.** The LLM generates a Step 0-shaped tool that can't handle Step N's upstream kwargs.
- **Direction of bias: anti-reuse.** Pillar 1 "generate once, reuse many" claim weakened by capability-position-dependent generation.

### Remediation

**A. Return all matching subtasks; let callers reason about position conflicts.**

```python
def find_all_subtasks_for_gap(gap, subtasks) -> list[SubTask]:
    return [st for st in subtasks if gap.query in st.required_capabilities]
```

Callers:

```python
def is_first_step_for_gap(gap, subtasks):
    matches = find_all_subtasks_for_gap(gap, subtasks)
    if not matches:
        return None
    # If ANY subtask using this cap has no depends_on, treat as first-step generator;
    # the generator must be capable of handling both first-step and later-step inputs.
    if any(len(st.depends_on) == 0 for st in matches) and any(st.depends_on for st in matches):
        # Mixed-position reuse — generator should support both shapes.
        return None  # Signal "position-agnostic" to the prompt.
    return all(len(st.depends_on) == 0 for st in matches)
```

**B. Or: emit a warning when a cap is reused across mixed positions.**

```python
def is_first_step_for_gap(gap, subtasks):
    matches = find_all_subtasks_for_gap(gap, subtasks)
    positions = {len(st.depends_on) == 0 for st in matches}
    if len(positions) > 1:
        logger.warning(
            "Capability %s reused across first-step and downstream positions; "
            "generator prompt will produce Step-0 signature.",
            gap.query,
        )
    return next(iter(positions)) if positions else None
```

(A) is the correctness fix; (B) is the disclosure-only minimum.

### Recommendation

**MEDIUM.** ~30 LOC + 1 test. ~30 min.

---

## Issue #102 — `build_schema_context` doesn't deduplicate upstream schema-key collisions (MEDIUM)

### Summary

`generators/utils.py:136-150`:

```python
for dep_idx in target_subtask.depends_on:
    if 0 <= dep_idx < len(subtasks):
        dep = subtasks[dep_idx]
        if dep.output_schema:
            for key, desc in dep.output_schema.items():
                input_parts.append(f"  - \"{key}\": {desc}")
```

Iterates ALL upstream dependencies' `output_schema` entries and appends `key: desc` to a flat list. If two predecessors produce the SAME key (e.g., both produce `"count": int`), the generator prompt lists the same key twice with potentially different descriptions.

The downstream generator LLM sees:
```
Your step receives a dict from the previous step with these keys:
  - "count": number of rows
  - "count": number of errors
```

Confusing; the LLM may pick one description arbitrarily or (worse) produce code assuming both are available.

### Where

`src/orchestrator/generators/utils.py:136-150`.

### Reproduction

```python
subtasks = [
    SubTask(output_schema={"count": "number of rows"}, ...),  # Step 0
    SubTask(output_schema={"count": "number of errors"}, ...),  # Step 1
    SubTask(depends_on=[0, 1], output_schema={...}, ...),  # Step 2 depends on both
]

build_schema_context(gap_for_step_2, subtasks)
# Prompt includes:
#   - "count": number of rows
#   - "count": number of errors
# LLM confused.
```

### Threat model / impact

- **Generator produces code referencing ambiguous upstream keys.** Silent correctness risk.
- **Related to ext6 #13 finding** (multi-file writers chained serially bug — same class of upstream-key-collision gap).
- **Direction of bias: unpredictable.** Depends on which description the LLM prefers.

### Remediation

**A. Detect collisions and either dedupe or warn in the prompt.**

```python
seen_keys: dict[str, str] = {}
for dep_idx in target_subtask.depends_on:
    if 0 <= dep_idx < len(subtasks):
        dep = subtasks[dep_idx]
        if dep.output_schema:
            for key, desc in dep.output_schema.items():
                if key in seen_keys:
                    # Collision — use qualified name in prompt:
                    input_parts.append(
                        f"  - \"{key}\": **COLLISION** — "
                        f"step {dep_idx}: {desc} | earlier step: {seen_keys[key]}"
                    )
                    logger.warning(
                        "Upstream schema-key collision on '%s' in subtask %d",
                        key, target_subtask.required_capabilities,
                    )
                else:
                    input_parts.append(f"  - \"{key}\" (from step {dep_idx}): {desc}")
                    seen_keys[key] = desc
```

Generator prompt now shows provenance (step N) and flags collisions explicitly.

**B. Regression test.**

```python
def test_build_schema_context_deduplicates_or_flags_upstream_collisions():
    ...
```

### Recommendation

**MEDIUM — prompt-clarity polish.** ~20 LOC + 1 test. ~30 min.

---

## Issue #103 — `run_pilot.py::harness.run_condition` call has no timeout; a single hanging Docker container blocks the 18-hour campaign indefinitely (P0)

### Summary

`scripts/run_pilot.py:244`:

```python
records = harness.run_condition(condition, tasks)
```

No timeout wrapping. `run_condition` iterates tasks; each task's pipeline invocation may involve Docker `sandbox_execute` calls. If a Docker container hangs (OS-level issue, zombie process, network deadlock), the surrounding Python call blocks waiting for `subprocess.run` / `docker.run`.

For a single task, the per-task timeout inside `sandbox_execute` (typically 30s or 60s) bounds individual calls. But if multiple timeouts or a cascading hang stalls the harness outer loop, the 18-hour campaign grinds to a halt with no per-task progress and no per-task timeout boundary visible at the pilot-runner level.

### Where

- **Unbounded call:** `scripts/run_pilot.py:244`.
- **Per-sandbox timeouts** exist inside `sandbox_execute` but don't cap the total run_condition duration.

### Remediation

**A. Wrap with a total-budget watchdog.**

```python
import signal

TASK_BUDGET_SECONDS = 600  # 10 min per task max
TOTAL_BUDGET_SECONDS = 18 * 3600  # 18 hr total

with _total_budget_watchdog(TOTAL_BUDGET_SECONDS):
    records = harness.run_condition(condition, tasks)

def _total_budget_watchdog(seconds):
    # Simple signal-based or threading-based watchdog that raises on timeout.
    ...
```

**B. Log per-task progress.**

Harness should emit a LangFuse event + stderr log before each task: "starting T{i} at {timestamp}." If the campaign stalls, the last logged timestamp points at the hanging task.

**C. Regression test: harness times out hanging task.**

```python
def test_run_condition_kills_hanging_task(monkeypatch):
    """Simulate a hanging task; harness should skip it and continue."""
    ...
```

### Recommendation

**P0 — land before any 18-hour campaign.** ~40 LOC for watchdog + logging. ~1h. Optional: persistent progress log to disk so campaign interruption leaves a breadcrumb for restart.

---

## Issue #104 — Tracing subsystem thread-safety gaps: global dict + per-thread ContextVar + singleton LangFuse trace_id cause cross-task attribution (P0)

### Summary

Three interrelated issues in the tracing subsystem combine to produce cross-task event attribution under concurrent execution:

**Layer A — global dict wipe/restore under threads (#142):** `src/orchestrator/tracing.py:85-96` — `trace_context` acquires `_trace_context_lock`, `_trace_context_global.clear()`, updates with new context, and on exit restores the prior state. Thread-safe for the acquire/release boundary, but any thread that READS the global (via `_read_trace_context`'s fallback path at line 104-105) DURING the critical section sees the transient wipe.

**Layer B — ContextVar doesn't propagate to ThreadPool workers (#143):** Python's `contextvars.ContextVar` is thread-local by default. A `ThreadPoolExecutor` worker spawned INSIDE a `trace_context(A)` block doesn't automatically inherit thread A's contextvar unless the caller explicitly runs the work item via `contextvars.copy_context().run(work)`. Without that, the worker reads `_trace_context_var.get()` → `None`, falls through to the GLOBAL fallback (lines 104-105). If concurrent thread B has entered `trace_context(B)` and wiped-and-replaced the global, worker A sees B's metadata.

**Layer C — `lf.current_trace_id` is a singleton (#144):** `src/orchestrator/composition.py:749` does `_chain_trace_id = getattr(lf, "current_trace_id", None)`. `lf` is the LangFuse singleton client (via `get_client()`). `current_trace_id` reflects whichever trace was MOST RECENTLY activated on the client — not which trace the CURRENT caller initiated. Under parallel execution, this is racy: thread A's chain captures thread B's trace_id.

Net effect: in concurrent environments (e.g., ThreadPoolExecutor inside the verification gate, parallel test_pipeline runs, any future concurrent eval config), generation events get attached to the wrong parent trace — Task A's span becomes a child of Task B's trace, or vice versa. The corruption is silent, transient, and hard to reproduce deterministically.

### Where

- **Global state:** `src/orchestrator/tracing.py:30-34`:
  ```python
  _trace_context_var: ContextVar[dict | None] = ContextVar(...)
  _trace_context_global: dict = {}
  _trace_context_lock = threading.Lock()
  ```
- **Wipe-and-replace critical section:** `src/orchestrator/tracing.py:85-96`:
  ```python
  with _trace_context_lock:
      prev_global = dict(_trace_context_global)
      _trace_context_global.clear()
      _trace_context_global.update(merged)
  ```
- **Global fallback read (no lock in contextvar path):** `src/orchestrator/tracing.py:99-105`:
  ```python
  def _read_trace_context() -> dict:
      ctx = _trace_context_var.get()
      if ctx:
          return dict(ctx)
      with _trace_context_lock:
          return dict(_trace_context_global)
  ```
- **Singleton trace_id lookup:** `src/orchestrator/composition.py:749`.

### Reproduction

Deterministic reproduction requires forcing thread interleaving, but the race pattern is classic TOCTOU:

```python
# Thread A:
with trace_context(tags=["task_A"]):
    # global = {"tags": ["task_A"]}
    pool.submit(work_item_A)   # worker's ContextVar is empty; will read global
    time.sleep(...)            # interleaving window

# Thread B (concurrent):
with trace_context(tags=["task_B"]):
    # global cleared and replaced with {"tags": ["task_B"]}
    # worker_A now reads global → sees ["task_B"]
    work_item_B_logic()

# Thread A's worker emits LangFuse event → tagged with ["task_B"]
# Chapter 6 observability: Task A's generation span appears in Task B's trace.
```

### Threat model / impact

- **Chapter 6 trace-based analysis (Row 13, Row 15, M4 per-task token cost) corrupted under concurrency.** If any future harness config uses ThreadPoolExecutor concurrency for task execution (currently serial per ext7 #51), events cross-attribute.
- **Current serial-per-task execution masks the bug today.** Ext7 #51's fix requires rows 8/9/10 to be serial — the harness processes one task at a time. Within-task concurrency (verification ThreadPool, LangChain @tool callbacks) is still exposed.
- **Direction of bias: unpredictable cross-task contamination.** Not a consistent direction; random misattribution distorts per-task aggregates.
- **Hard to detect in post-hoc analysis.** Wrong-trace attribution looks like "this task used more LLM calls than expected" rather than "this task's LLM calls got attributed to a different task." No obvious signal.

### Remediation

**A. Use `contextvars.copy_context()` for ThreadPool work items.**

Every submission to a ThreadPoolExecutor inside a `trace_context` should wrap the callable:

```python
import contextvars
with ThreadPoolExecutor() as pool:
    ctx = contextvars.copy_context()
    future = pool.submit(ctx.run, work_item, arg1, arg2)
```

This explicitly propagates the contextvar into the worker. Applies to every ThreadPoolExecutor in the codebase — audit all `pool.submit` sites.

**B. Remove the global-dict fallback; use only ContextVar.**

Once (A) is in place for every work-item submission, the global-dict fallback becomes unnecessary. Remove it to eliminate the cross-thread-wipe race entirely:

```python
# tracing.py — remove:
# _trace_context_global: dict = {}
# _trace_context_lock = threading.Lock()
#
# trace_context: only set/reset contextvar, no global state.
# _read_trace_context: only read contextvar; return {} if absent.
```

Safer but requires confirming no legacy callers depend on the global fallback.

**C. Thread-safe LangFuse trace_id capture.**

```python
# composition.py:749 — before:
_chain_trace_id = getattr(lf, "current_trace_id", None)

# after: capture at the START of the chain's span, not later:
with lf.start_span(name="chain_run") as _chain_span:
    _chain_trace_id = _chain_span.trace_id
    # ... rest of run ...
```

Explicit span-scoped capture avoids the singleton-client race.

**D. Regression tests.**

```python
def test_trace_context_propagates_to_threadpool_workers():
    """Worker launched inside trace_context(A) sees A's tags, not another thread's."""
    ...

def test_trace_context_global_fallback_is_not_race_vulnerable():
    # After remediation B, no global fallback; this test verifies its absence.
    ...
```

### Recommendation

**P0 — land before any future concurrent eval configuration.** Currently masked by ext7 #51's SERIAL enforcement; if any team member re-introduces within-task or cross-task concurrency, this corruption fires immediately. ~60 LOC + 2-3 regression tests. ~1-1.5h.

### Legacy data

Historical LangFuse data from serial runs is unaffected (each trace is unambiguous because only one task runs at a time). If the codebase ever enabled concurrent execution, those traces may have cross-attribution. No migration possible post-hoc.

---

## Issue #105 — `backfill_token_costs.py` ±5-minute window buffer collides between back-to-back runs (MEDIUM)

### Summary

`scripts/backfill_token_costs.py:677-678`:

```python
start = min(rw["start_ts"] for rw in run_windows) - timedelta(minutes=5)
end = max(rw["end_ts"] for rw in run_windows) + timedelta(minutes=5)
```

The script fetches LangFuse generations in a ±5-minute buffer window around the earliest and latest run timestamps. Per-run attribution inside the fetched list relies on `start_ts` / `end_ts` per run (with a 120-second buffer at line 210-211).

**Collision scenario:** if two eval runs execute back-to-back within 5 minutes (e.g., run A ends at 14:00, run B starts at 14:02), the buffers overlap. A LangFuse generation from run B's first task (at 14:02:30) falls inside run A's [14:00:00-5min, 14:00:00+5min] buffer AND inside run B's own window. The script's per-run attribution picks whichever run matches first — which depends on iteration order, not truth.

The ±5-minute and ±2-minute buffers exist for legitimate reasons (clock skew between the harness and LangFuse, write latency for end-of-task generations). But they create a collision zone between closely-spaced runs.

### Where

- **Outer buffer (±5 min):** `scripts/backfill_token_costs.py:677-678`.
- **Inner buffer (±2 min):** line 210-211:
  ```python
  buf_start = rw["start_ts"] - timedelta(seconds=120)
  buf_end = rw["end_ts"] + timedelta(seconds=120)
  ```

### Reproduction

```python
# Two runs recorded in the DB:
#   Run A: start=14:00:00, end=14:02:00
#   Run B: start=14:03:00, end=14:05:00
# Gap between runs: 1 minute.

# Outer buffer: [13:55:00, 14:10:00] — includes both runs.
# Per-run buffers:
#   A: [13:58:00, 14:04:00]
#   B: [14:01:00, 14:07:00]
# Overlap: [14:01:00, 14:04:00] — 3 minutes where a generation matches BOTH windows.
```

A generation at 14:03:30 matches both windows. The script's attribution logic at line 199-213 picks based on iteration order (first-match-wins). If run A is first in the sorted list, the generation is attributed to A — even though it chronologically belongs to B.

### Threat model / impact

- **M4 cross-run token cost attribution errors under close temporal proximity.** The user's concern cites main eval + longitudinal as the back-to-back scenario.
- **Runbook-level mitigation exists.** If the team waits 10+ minutes between runs, collision is avoided. But this is procedure-dependent and easy to forget.
- **Direction of bias: unpredictable.** Depends on iteration order + generation timestamps + run time alignment.
- **Compounds with ext8 #58 + #28** (retrieval exclusion, run_level divisor), ext9 #97 (backfill overall). Another layer of cross-run attribution concern.

### Remediation

**A. Enforce hard boundaries — no buffer overlap between runs.**

```python
# Sort runs by start_ts; each run's buffer is clipped by the previous run's end + 30s.
sorted_rws = sorted(run_windows, key=lambda rw: rw["start_ts"])
for i, rw in enumerate(sorted_rws):
    lo = rw["start_ts"] - timedelta(seconds=120)
    hi = rw["end_ts"] + timedelta(seconds=120)
    if i > 0:
        prev_end = sorted_rws[i-1]["end_ts"]
        lo = max(lo, prev_end + timedelta(seconds=30))  # clip to prevent overlap
    if i + 1 < len(sorted_rws):
        next_start = sorted_rws[i+1]["start_ts"]
        hi = min(hi, next_start - timedelta(seconds=30))  # clip
    rw["buf_start"] = lo
    rw["buf_end"] = hi
```

**B. Or: require explicit run_id tags on every LangFuse trace.**

Step 13.7 already tags traces with run_id. If every generation is queryable by run_id directly, timestamp-window attribution is bypassed entirely. The per-task-tagged path in `compute_per_task_costs` already does this; the legacy-run-level path could be updated similarly.

**C. Regression test.**

```python
def test_backfill_attributes_close_back_to_back_runs_correctly():
    # Seed DB with two runs 1 minute apart; seed LangFuse with generations tied to each run.
    # Assert each generation goes to the correct run.
    ...
```

### Recommendation

**MEDIUM — land before any same-day back-to-back eval campaigns.** (A) is ~30 LOC; (B) requires broader LangFuse query refactor. Pick (A) for the Phase 5 timeline. ~45 min.

---

## Issue #106 — `_sandbox_execute_with_install` creates a fresh ephemeral image + runs pip install for EVERY probe; Tier 2 pays 3-6× install cost per task (P0)

### Summary

`src/verification/sandbox_tool.py:338-341`:

```python
def _sandbox_execute_with_install(code, timeout, packages, ...):
    """Install extra packages (with network), then run code (without network)."""
    import uuid
    ephemeral_tag = f"jit-sandbox-eph-{uuid.uuid4().hex[:8]}"
```

Every call to `_sandbox_execute_with_install` creates a **NEW UUID-tagged ephemeral image**, runs `uv pip install` for the declared packages, uses the image, and removes it (line 498 `docker rmi`). No caching across calls within a task.

Tier 2 behavioral testing runs ~6 sandbox invocations per task that needs dependencies:
- `_test_parameterless_smoke`
- `_test_output_variation` (2 invocations: boundary + large inputs)
- `_check_latency_variance` (N invocations for jitter samples)
- `_check_network_disconnect_differential` (2 invocations: online + offline)

For a MCP_TOOL that declares `pip_packages=["pandas", "openpyxl"]`, each invocation pays the full `uv pip install` cost — typically 30-60 seconds on warm PyPI cache, longer on cold. 6 × 45s = 4.5 minutes of pure install overhead per task.

Combined with ext10 #74's 60-second pip install timeout: if the install exceeds 60s on any one of the 6 invocations, that probe fails → Tier 2 rejects the artifact → M1 drops.

For the 400-record Phase 4 eval: assuming average 4 min/task × 400 tasks = 26 hours of install overhead on top of the documented 18-hour wall-clock. Potentially doubling campaign duration or timing out enough probes to materially depress M1.

### Where

- **Ephemeral image per call:** `src/verification/sandbox_tool.py:341` — `ephemeral_tag = f"jit-sandbox-eph-{uuid.uuid4().hex[:8]}"`.
- **Pip install inside the call:** `:395-425` — `uv pip install` phase.
- **Image cleanup after call:** `:498` — `docker rmi -f` at the end.
- **Tier 2 probe sites (6 invocations):** `src/verification/behavioral.py` — `_test_parameterless_smoke`, `_test_output_variation`, `_check_latency_variance`, `_check_network_disconnect_differential`.

### Reproduction

```bash
# Monitor Docker image churn during a Tier 2 run:
$ docker images | grep jit-sandbox-eph | wc -l
# Observe: new images created and destroyed rapidly during each artifact's Tier 2 verification.
# Time profile via LangFuse spans: each Tier 2 probe shows ~30-60s in pip install phase.
```

### Threat model / impact

- **Phase 5 eval wall-clock 2-3× documented estimate.** 18h budget → 40-60h actual for dependency-heavy artifacts.
- **Tier 2 timeouts cascade into M1 depression.** Any pip install that exceeds 60s (per ext10 #74) causes the probe to fail → artifact rejected → task failure → M1 miscount.
- **Direction of bias: anti-JIT on dependency-heavy tasks.** T07 (pandas CSV), T08 (data quality), T11 (pandas + openpyxl) disproportionately affected. These are the Pillar 1 synthesis tasks Chapter 6 cites.
- **Compounds with ext10 #74 + ext8 #58.** #74 timeout too tight; #58 untagged-run cost attribution; #106 repeated installs. Three layers of dependency-handling issues.

### Remediation

**A. Image-level caching across probes within a task.**

```python
# src/verification/behavioral.py — at the start of Tier 2 for a given artifact:
from src.verification.sandbox_tool import prepare_cached_image

cached_image = prepare_cached_image(pip_packages=artifact.pip_packages)
try:
    # All 6 probes use the cached image; pip install runs ONCE.
    findings.extend(_test_parameterless_smoke(source_code, func_name, cached_image=cached_image))
    findings.extend(_test_output_variation(source_code, func_name, schema, cached_image=cached_image))
    findings.extend(_check_latency_variance(..., cached_image=cached_image))
    findings.extend(_check_network_disconnect_differential(..., cached_image=cached_image))
finally:
    cleanup_cached_image(cached_image)
```

`prepare_cached_image` runs pip install once, commits the image, returns its tag. Each probe runs `docker run` against the tagged image (no reinstall). Cleanup at the end of Tier 2.

Amortizes install cost: 1 install per task × 400 tasks ≈ 5-6 hours install overhead total (vs 26h under current code).

**B. Base image pre-warmed with common dependencies.**

Bake pandas, numpy, scipy, scikit-learn, openpyxl into the sandbox base image. For artifacts declaring only those common packages, pip install is a no-op or near-no-op. For novel packages, still pays the install cost once.

Combines with (A) for best effect.

**C. Regression test.**

```python
def test_tier2_reuses_ephemeral_image_across_probes():
    """Pip install runs ONCE per task, not per probe."""
    install_count = []
    # Monkey-patch uv pip install to count invocations.
    with mock.patch("sandbox_tool._pip_install", side_effect=lambda *a: install_count.append(1)):
        verify_behavioral(artifact_with_dependencies)
    assert len(install_count) == 1  # not 6
```

### Recommendation

**P0 — blocks Phase 5 on reasonable wall-clock.** ~60 LOC + 1 regression test. ~1-2h. Bundle with ext10 #74 (pip timeout raise to 180s) — together they close the dependency-install efficiency gap.

---

## Issue #107 — `analyze_longitudinal.py` computes Gini on cumulative invocation counts; metric artificially stabilizes over time (MEDIUM)

### Summary

`scripts/analyze_longitudinal.py:378-389`:

```python
cumulative: dict[str, int] = defaultdict(int)
cycles: list[dict] = []
for i, (run_id, task_id, ts) in enumerate(task_rows):
    for cap_id, n in cycle_increment[i].items():
        cumulative[cap_id] += n
    cycles.append({
        ...,
        "cumulative_invocations": dict(cumulative),   # snapshot
        "incremental_invocations": dict(cycle_increment[i]),
        ...,
    })
```

The per-cycle snapshot persists CUMULATIVE invocation counts (all invocations through cycle N). If Gini is computed from this cumulative dict, the metric naturally stabilizes over time because:
- Early cycles have small N → high per-cycle variance → Gini varies widely
- Late cycles have large cumulative N → per-cycle incremental changes have diminishing impact on the cumulative distribution → Gini stabilizes

Pillar 3 RQ #1 asks: "does the optimizer produce a more balanced / concentrated distribution over time?" The signal the question seeks is CHANGE in per-cycle behavior — which cumulative-Gini smooths out.

Per-cycle INCREMENTAL Gini would answer the question directly: "at cycle N, what's the Gini of this cycle's fresh invocations?" If incremental Gini is high throughout, the optimizer isn't stabilizing the distribution. Cumulative Gini hides this signal.

### Where

- **Cumulative tracking:** `analyze_longitudinal.py:378-389`.
- **Incremental tracking (present but apparently under-used):** `"incremental_invocations"` field at line 390.
- **Gini computation** (in `analyze_results.py::print_gini_summary`, called via harness flush) — consumes from `invocation_events`. Per-run Gini is per-run cumulative, not per-cycle.

### Threat model / impact

- **Pillar 3 RQ #1 answer biased toward "stable."** Any registry that started imbalanced AND stayed imbalanced would report "Gini trajectory converges" because cumulative smoothing → flattening.
- **Direction of bias: pro-optimizer.** Makes the optimizer look like it's achieving convergence when the underlying instability is hidden.
- **Compounds with ext9 #96 + ext8 #78** (Gini population scope issues). Three Gini-related correctness concerns: population-scope (active vs historical), zero-exclusion (active zero-count caps), and cumulative-vs-incremental (temporal smoothing).

### Remediation

**A. Report both incremental and cumulative Gini per cycle.**

```python
# analyze_longitudinal.py — report both:
for cycle in cycles:
    inc_counts = list(cycle["incremental_invocations"].values())
    cum_counts = list(cycle["cumulative_invocations"].values())
    cycle["gini_incremental"] = _gini_coefficient(inc_counts)
    cycle["gini_cumulative"] = _gini_coefficient(cum_counts)
```

Chapter 6 figure shows both trajectories. If incremental converges toward cumulative, optimizer is stabilizing. If incremental stays high while cumulative looks smooth, the smoothing is an artifact.

**B. Windowed Gini.**

Report Gini over a rolling window (e.g., last 5 cycles' incremental counts). Smoother than purely-incremental but captures recent behavior rather than all-historical.

**C. Chapter 4.5 disclosure.**

Regardless of fix: document which Gini is reported ("cumulative" or "incremental" or both). Current doc strings mention "per-cycle Gini" ambiguously.

### Recommendation

**MEDIUM — bundle with ext9 #96 + ext8 #78 Gini integrity cluster.** ~20 LOC. ~30 min.

---

## Issue #108 — `step_results` list ordered by execution (topological), not by subtask index; positional access on non-linear DAGs would misattribute (MEDIUM, latent)

### Summary

`src/orchestrator/composition.py:866`:

```python
step_results.append(output)
```

Inside the `for position, step_idx in enumerate(execution_order)` loop at line 767. `step_results[i]` is the i-th step IN TOPOLOGICAL ORDER, NOT the i-th subtask in the decomposition's original order.

Current consumers (grep confirmed):
- `tests/evaluation/harness.py:964` → `final_output = chain_result.step_results[-1]` (uses `[-1]`, topologically last)
- `src/orchestrator/composition.py:962` → `last_output = step_results[-1]` (same pattern)

Both use `[-1]` (topologically-last), which is safe for getting the final-output semantic.

**Latent risk:** any future consumer that accesses `step_results[N]` expecting "subtask N's output" will get the wrong data on non-linear DAGs. The decomposer's subtask list and `execution_order` can diverge if the LLM emits subtasks in non-dependency order.

Example: decomposer emits:
```
subtasks[0] = SubTask(depends_on=[1], ...)  # logically second
subtasks[1] = SubTask(depends_on=[], ...)   # logically first
```

`build_execution_order` sorts topologically → `execution_order = [1, 0]`. Step results:
- `step_results[0]` = output of subtask 1 (executed first)
- `step_results[1]` = output of subtask 0 (executed second)

A future consumer like `step_results[subtask_index_in_decomposition]` gets the wrong subtask's output.

### Where

- **Append site (topological order):** `src/orchestrator/composition.py:866`.
- **Subtask-index-keyed alternative (safe):** `src/orchestrator/composition.py:867` — `executed_outputs[step_idx] = output` (dict keyed by subtask index, not list).
- **Current consumers (safe via `[-1]`):** `harness.py:964`, `composition.py:962`.

### Reproduction

```python
# Decomposer produces reverse-ordered subtasks:
subtasks = [
    SubTask(description="step B", depends_on=[1], ...),  # subtask 0
    SubTask(description="step A (foundation)", depends_on=[], ...),  # subtask 1
]

executor.run()
# execution_order = [1, 0] (topological)
# step_results = [out_A, out_B]   (in execution order)
# executed_outputs = {1: out_A, 0: out_B}  (keyed by subtask index)

# Current code:
chain_result.step_results[-1]  # → out_B (subtask 0's output, the logical FINAL step) ✓

# Hypothetical future consumer:
for i, st in enumerate(subtasks):
    output = chain_result.step_results[i]  # ← WRONG on non-linear DAGs
    # i=0 (subtask B) would get out_A (subtask A's actual output)
```

### Threat model / impact

- **Latent only.** No current consumer is bitten.
- **Structural fragility.** A reviewer-auditor writing "Chapter 6 per-step output analysis" that iterates `step_results` positionally gets wrong data.
- **Could manifest via LangFuse dashboard queries** that assume "position N in step_results == subtask N" — the `step_timings` list DOES carry `step_index=step_idx` so LangFuse attribution via step_timings is safe, but if someone joins step_results + subtasks by position, they'd misattribute.

### Remediation

**A. Change `step_results` to a dict keyed by subtask index.**

```python
# src/schemas/composition.py (or wherever ChainResult is defined):
class ChainResult(BaseModel):
    step_results: dict[int, dict] = Field(default_factory=dict)  # was list[dict]
    ...

# composition.py — updated append:
step_results[step_idx] = output
```

Semantic: `chain_result.step_results[N]` now unambiguously means "subtask N's output (or KeyError if not executed)."

Consumers change: `step_results[-1]` → `step_results[execution_order[-1]]` or a new `chain_result.final_output` property.

**B. Or: keep list, add explicit property for final output.**

```python
# Keep step_results as list (topological). Add a named accessor:
@property
def final_output(self) -> dict | None:
    return self.step_results[-1] if self.step_results else None
```

Deprecate positional access to step_results in favor of `final_output` or `step_timings[i].step_index`-lookup. Minimal change; backward-compatible.

**C. Regression test.**

```python
def test_step_results_ordering_is_topological():
    """Document the invariant: step_results is in execution (topological) order."""
    # Reverse-ordered subtasks, assert step_results[-1] is the decomposition's logical-last subtask.
```

### Recommendation

**MEDIUM — land as architectural hygiene.** (B) is 5 LOC + doc. ~15 min. (A) is a larger schema change — defer to Chapter 7 unless a new consumer needs positional access.

---

## Claims submitted but NOT added (duplicates / not-reproducible / design / overstated)

### Batch-level summary

Of 40 submitted claims, **34 were not added** as distinct findings:

**Duplicates of prior extensions:**
- **#104** (registry leak: src/mcp_tools wipe) → ext9 **#86**
- **#119** (shift O-B-1 marker off-by-one) → ext9 **#82**
- **#120** (DB cross-pollination, metrics store sharing) → ext7 **#49** + ext8 **#71**
- **#125** (snapshot pollution, src/mcp_tools leftover) → ext9 **#86** + ext6 **#31**
- **#126** (permission race on rmtree) → matches ext8 §"Claims submitted but NOT added" user's #18 / #20
- **#127** (run_id fragmentation) → ext7 **#46**
- **#135** (Gini zero-blindness) → ext9 **#96**
- **#139** (metadata amnesia, pip_packages) → ext9 **#83**
- **#140** (cost division subtract-3) → ext8 **#58** Bug B
- **#109** (host load jitter on mock smell) → ext8 **#73**

**Not confirmed against current code:**
- **#105** (pilot exclusion): analyzer's `is_pilot = FALSE` filter is **by design**. Pilot rows are Row 7 smoke; excluding them from Row 17 is the correct methodology.
- **#107** (backfill history pollution): grep confirms `is_pilot = FALSE` filter IS present at `backfill_token_costs.py:131/140/166`. User's claim doesn't match current code.
- **#115** (hash unstability via str(agent_config)): `compute_artifact_hash` at `gate.py:45` uses `json.dumps(..., sort_keys=True, default=str)`. Sorted-keys + JSON encoding produces stable hash for dict-of-primitives. Memory-address leakage via `default=str` would only affect non-JSON-serializable values, which `agent_config` doesn't contain per the schema.
- **#121** (KL translation flaw): shifting by min_val before normalization is a **documented standard technique** for handling negative-valued utility scores. KL numerical value is deliberately shift-dependent because the shift is part of the softmax-like normalization, not an oversight.
- **#138** (similarity 0.6 vs 0.7 "fraudulent"): previously verified — the proposal doesn't specify 0.7. Implementation uses 0.6. User's "0.7 in Chapter 3" citation cannot be reproduced.
- **#116** (sync fence crash): grep for await patterns shows the only `await {func_name}(...)` call is at `behavioral.py:264` which is for MCP tools (inside an `async def _run()`). SKILL code fences are executed via a different path (`_run_skill_loop` in `execution.py`). User's specific claim doesn't match the code paths.

**Documented design choices:**
- **#106** (strawman baseline for synthesis tasks): the static baseline's behavior for synthesis tasks (no static impl → `generation_failed`) is the correct static-condition measurement — static HAS no implementation for those tasks, so 0% success IS the honest baseline. Framing as "strawman" overstates; "limited static coverage, disclosed in methodology" is accurate.
- **#112** (FS isolation: agent workspace vs Docker FS): this is **by design** — the adversarial agent system prompt at `adversarial.py:173-177` explicitly documents the split. The agent is instructed to inline tool code into `sandbox_execute` calls rather than expect cross-call FS persistence.
- **#113** (mock clearance): `gate.py:373-387` downgrades Tier 1 HIGH to MEDIUM when Tier 2 is clean. Docstring at line 376 explicitly cites the thesis proposal: "HIGH findings require Tier 2 clearance." User calls this a bug; design says it's proposal-faithful. **Chapter 4.5 discloses this as the "Tier 1 false-positive recovery" mechanism.**
- **#123** (lambda state hazard in pipeline): LangGraph's node functions execute in the invoker's thread; shared module-level metadata is controlled via `langgraph.config.get_config()`. Standard LangGraph pattern; not a concurrency bug.
- **#124** (scipy landmine): scipy is a declared dependency in `pyproject.toml`; not a runtime crash risk.
- **#131** (SKILL schema gap): SKILLs don't strictly need `output_schema` passed to them at execution; the schema is enforced at generation time and checked at output_contract time. **Design choice, not a bug.**
- **#132** (SKILL output loss): `_extract_last_sandbox_json` returning the last message is **by design** — SKILLs produce one final answer, not intermediate state. Intermediate tool calls are scratch.
- **#133** (message pattern: second HumanMessage): DeepAgents' message pattern uses HumanMessage as a termination signal. Design consistent with the DeepAgents SDK.
- **#134** (GenericResult empty dict for CLI): fallback when no typed result model is known. For the eval path, typed models are always provided per ext6's output_schema plumbing. CLI path uses GenericResult by design — that's the "shape-agnostic" fallback, not a bug.
- **#137** (trace truncation, first-step detachment): LangFuse trace-ID capture is best-effort; documented fallback paths in `pipeline.py`. Not a correctness bug; observability polish.

**Overstated / narrow / partial:**
- **#101** (subtask doubling): requires an exception mid-for-loop to manifest. Narrow scenario. Not reproducible in practice.
- **#102** (step-0 hallucination): related to ext9 #83 (pip_packages persistence chain). Schema-side: if signature is frozen at first-generation and later contexts need different kwargs, the reuse fails. But this is the same ecosystem as ext9 #83, not a distinct correctness bug.
- **#103** (schema poisoning: first-subtask schema only): overlaps with ext10 #101 (find_subtask_for_gap first-only) — captured there.
- **#108** (JSON ensure_ascii not set): default `ensure_ascii=True` produces escaped-ASCII output which IS valid JSON, round-trippable via `json.loads`. Not a corruption bug.
- **#110** (negative boundary gap): **CAPTURED as ext10 #98.**
- **#111** (reasoning_effort): **CAPTURED as ext10 #99.**
- **#114** (import_check wrong-blame): the distinction between `wrong_pip_package` and `import_error` is based on the presence of `IMPORT_PROBE_ERROR` in stderr. It does distinguish cases; user's claim that it "attributes all failures to wrong package name" is overstated. Network failures propagate as stderr containing neither marker and would fall into a third category (generic error). Narrow concern; could be refined but not a headline bug.
- **#117** (avg cost bias): **CAPTURED as ext10 #100.**
- **#118** (DB handle leak on crash): Python's OS-level file-closure on process exit handles this in most cases. DuckDB's own lock file handling is robust. Narrow concern.
- **#122** (search crowding, top-K similarity): ChromaDB's top-K retrieval is standard; the merge-partner search uses the full pool with Jaccard overlap as the secondary filter. User's claim doesn't match the existing dual-filter (cosine + Jaccard) design.
- **#128** (first-step collision in find_subtask_for_gap): **CAPTURED as ext10 #101.**
- **#129** (schema collision in build_schema_context): **CAPTURED as ext10 #102.**
- **#130** (quadratic context via fixture_contents): valid scaling concern but chain lengths in testbed are ≤5 steps. Not thesis-blocking; Chapter 7 scalability note.
- **#136** (timeout silence on run_condition): **CAPTURED as ext10 #103.**

---

### Second-batch claims that did NOT become findings (#141-#160)

**Repeats of previously-rejected claims (from earlier batches):**
- **#151** (subtask doubling) = prior batch's #101 — narrow scenario, not reproducible. No new evidence.
- **#152** (signature hallucination) = prior batch's #102 — overlap with ext9 #83 pip_packages chain.
- **#153** (quadratic context) = prior batch's #130 — scalability concern, Chapter 7 scope.
- **#154** (SKILL output loss) = prior batch's #132 — by design; SKILLs produce a single final answer.
- **#155** (message pattern) = prior batch's #133 — DeepAgents SDK pattern.
- **#156** (generic CLI silence) = prior batch's #134 — design fallback.
- **#157** (similarity 0.6 vs 0.7) = prior batch's #138 — **not reproducible**. Proposal does not specify 0.7. `docs/proposal_vs_implementation_v2.md:25` correctly documents the implementation's 0.6 choice. Re-raising the same claim does not change its status.
- **#158** (metadata amnesia: pip_packages) → **DUPLICATE of ext9 #83** (three-layer persistence chain).
- **#159** (cost division subtract-3) → **DUPLICATE of ext8 #58 Bug B**.
- **#160** (dependency erasure: registry reuse hardcodes `pip_packages=[]`) → **DUPLICATE of ext9 #83** (same chain, third layer — composition.py).

**New second-batch claims also not added:**
- **#141** (test_registry_matching env poisoning via `os.environ.setdefault`): same mechanism as prior batch's user's #189 (test_mint env) which was logged as partial/within-process-only. `scripts/test_registry_matching.py:65` uses `setdefault` not direct assignment — respects pre-existing env. Cross-process poisoning isn't possible; within-process is narrow. Same classification.
- **#142/#143/#144** (tracing subsystem): **CAPTURED as ext10 #104** (consolidated into a single finding covering all three aspects — global-dict race, ContextVar-ThreadPool desync, LangFuse singleton trace_id leakage).
- **#145** (CAMPAIGN_RUN_IDS discovery race): the run sheet's discovery SQL at line 80 selects DISTINCT run_id from evaluation_runs for the active DB. If `$CAMPAIGN_DB` points at a DB with aborted-run rows, those rows get discovered. Heavily overlaps with ext7 #49 (DB isolation) — the remediation for #49 (use fresh timestamped DB) precludes aborted-run pollution by construction. Not a distinct finding; covered by #49's "don't reuse DB between campaigns" discipline.
- **#146** (backfill window collision): **CAPTURED as ext10 #105.**
- **#147** (ephemeral DB conflict in test_registry_fn.py): **NOT CONFIRMED**. `SkillRegistry(persist_directory=None)` uses `chromadb.EphemeralClient()` (`src/registry/store.py:24`) which is **in-memory only**, no filesystem. No lock contention possible with a real persistent registry.
- **#148** (fixed-seed bias in test_registry_matching): framing/documentation concern, not a code bug. The test IS a regression test against deterministic seeds; that's its purpose. Chapter 3's FP-prevention evidence should cite this as "deterministic regression test" rather than "scientific FP-rate measurement." Documentation clarification, not remediation.
- **#149** (Gini NaN crash): **NOT CONFIRMED**. `_gini_coefficient` at `analyze_results.py:625-630` explicitly guards against `not counts`, `total == 0`, `n == 0` by returning 0.0. The caller at line 748 computes `mean = sum(run_ginis) / len(run_ginis)` but only after guarding `if not run_ginis: continue` at line 719. No crash path exists.
- **#150** (LaTeX table overflow): **NOT A REAL BUG**. The tables in `analyze_results.py` are **terminal-output plain-text tables** (fixed-width `f"{label:<22}..."` formatting), not LaTeX source. Chapter 6's LaTeX tables are authored separately; the analyzer output is for terminal inspection + CSV export. The "LaTeX" framing mislabels the concern. Long condition names could misalign terminal output — cosmetic, not publication-blocking.

---

### Third-batch claims that did NOT become findings (#161-#182)

- **#161 + #164** (redundant pip installs): **CAPTURED as ext10 #106.**
- **#162** (host-dependent latency threshold): **DUPLICATE of ext8 #73**. User re-frames the cv-threshold concern as "200µs stdev threshold"; same root issue already logged with the same remediation direction.
- **#167** (KL divergence shifting bias): **REPEAT of prior batch's #121** (rejected as documented design). Shifting by min_val is a deliberate technique for handling negative utility scores in the softmax-like normalization. Re-raising without new evidence.
- **#168** (cumulative Gini stiffening): **CAPTURED as ext10 #107.**
- **#169 + #170** (multi-formula KL discrepancy): **NOT CONFIRMED AS FRAMED.** User's claim of "clipped histogram (-200 to 5)" is factually wrong. Actual code at `src/optimizer/simulation.py:107-108` uses `lo = min(min(scores), -200.0); hi = max(max(scores), 5.0)` — **adaptive range with floor**, expanding to include outliers. Docstring at line 101-103 explicitly says "no scores are clipped while maintaining a consistent baseline range across cycles." The multi-implementation concern (3 formulas coexist) is real and **already disclosed in `docs/verification/04_pillar_3_granularity_optimizer.md:93`** as "dual-KL disclosure." Simulation KL is visualization-only (ext6 confirmed); production Chapter 6 uses the analyzer formula. No new finding.
- **#171** (token cost truncation via `//`): **REPEAT of prior batch's #182** (rejected as not material). For realistic token counts (1000s-10000s per task), integer division precision loss is 1-digit at worst, never truncates to zero.
- **#176 + #177** (decomposition retry doesn't trigger on malformed DAG): **DUPLICATE of ext9 #90**. Same code path, same concern, already confirmed and remediated.
- **#180** (lead agent cache collision): **REPEAT of prior batch's #186** (rejected). Cache population at `composition.py:843-848` is gated on `agent_config.get("subagents")` — only lead-with-subagents artifacts are cached. Regular AGENT artifacts are re-invoked fresh. User's "different inputs" scenario doesn't apply because lead-with-subagents receives all subtask descriptions at initial invocation.
- **#181** (global tracing contention): **CAPTURED as ext10 #104** (consolidated tracing thread-safety finding).
- **#182** (step-result index desync): **CAPTURED as ext10 #108.**

---

## Relationship to ext6/ext7/ext8/ext9 findings

ext10's eleven findings span several patterns:

| # | Pattern | Notes |
|---|---|---|
| 98 | **Probe-input-coverage gap** | Related to ext8 #72 (variation probe equality) and ext8 #73 (mock-smell threshold) — all Pillar 2 probe-coverage refinements |
| 99 | **LLM-quality configuration** | Novel — not covered by prior extensions. Pairs with ablation studies Chapter 4.5 might cite. |
| 100 | **Aggregation bias (filter the wrong rows)** | Related to ext9 #84 (M2 partial excluded) and ext9 #85 (failure breakdown denominator) — all "what population is this aggregate over?" clarifications |
| 101 | **First-matching lookup hazard** | Sibling of ext9 #92 (CompositionalExecutor first-capability only) — same class: first-only resolution where multi-instance matters |
| 102 | **Schema-key upstream collision** | Extends ext6 #13 (multi-file writers) — same family of upstream-key collision concerns |
| 103 | **Missing watchdog on long-running loop** | Novel — not previously covered. Chapter 4.5 operations-discipline note. |
| 104 | **Concurrency-unsafe tracing primitives** | Global-dict + ContextVar mixed pattern; ThreadPool worker inheritance broken; singleton trace_id races. Masked today by serial execution (ext7 #51) — uncovered if concurrency is re-enabled. |
| 105 | **Overlapping time-window attribution** | Back-to-back runs within the backfill buffer cross-attribute. Procedure-mitigable but structurally fragile. |
| 106 | **Per-probe image lifecycle** | Ephemeral image + pip install per probe; 6× redundant install cost per task drives Tier 2 timeouts |
| 107 | **Metric-smoothing via cumulative aggregation** | Gini on cumulative counts flattens the per-cycle instability signal Pillar 3 RQ #1 is designed to measure |
| 108 | **Positional-vs-keyed semantic mismatch** | `step_results` list-indexed semantics depend on execution order; current consumers safe via `[-1]`, latent for future positional accessors |

Guard patterns added by this extension:

- **Probe-input taxonomy audit** (#98): for each numeric-input probe, a list of documented edge cases the generator is tested against (negative, large-negative, NaN, inf). Adding a new edge case is a test-file change, not a code change.
- **LLM-quality configuration knob** (#99): any hardcoded LLM parameter that affects the downstream metric (reasoning_effort, temperature, max_tokens) should be env-var-overridable so researcher ablations can quantify its effect.
- **Conditional-AVG interrogation** (#100): any SQL AVG aggregate with a CASE WHEN filter in the VALUE expression should be examined for "does this filter match the population I'm averaging?" A simple heuristic: if the filter can be expressed in WHERE, prefer WHERE-based filtering so the N-column matches.
- **First-match resolution warning** (#101): any function that returns the first element of a collection-of-matches should document the semantics and warn when multiple matches exist in a context where that matters.
- **Collision-aware dict merging** (#102): when merging dicts from multiple sources into a flat prompt context, preserve provenance on key collisions.
- **Watchdog on multi-hour loops** (#103): any run loop bounded by wall-clock time (not by N iterations) should have a total-budget watchdog and per-iteration progress log.
- **ContextVar propagation on ThreadPool submission** (#104): every `pool.submit(...)` inside a `trace_context` or similar ContextVar scope must wrap the work item via `contextvars.copy_context().run(work)` — otherwise workers silently fall back to global state.
- **Temporal-window boundary clipping** (#105): any time-window-based attribution scheme should clip each window's boundaries by adjacent-window end/start to prevent overlap. Runs close in time shouldn't need a manual "wait 10 minutes" procedural workaround.
- **Amortize expensive setup across reusable probe batches** (#106): when N probes share the same dependency set, setup (pip install, image build) should run once and the probes reuse the prepared environment. Per-probe setup is an O(N) cost multiplier for no gain.
- **Incremental vs cumulative in time-series metrics** (#107): for convergence/stability metrics, the incremental-per-window signal is what the RQ typically asks about; cumulative smoothing answers a different question. Report both or clearly document which is cited.
- **Ordered-list semantics disclosure** (#108): any list field where the ordering is non-obvious (topological, time-sorted, caller-dependent) should either (a) use a dict keyed by the semantically-meaningful key, or (b) carry a property/helper for the common access pattern so positional access becomes unnecessary.

---

## Parent-document integration (pending)

When ext10 lands, fold forward into `security_audit_2026_04_19.md`:

1. Update header: "Ninety-seven findings → One hundred eight findings; #56-#108 open as of 2026-04-20."
2. Insert TL;DR rows #98-#108 after ext9's #97.
3. Tranche 3 (eval-blocking) gains **four P0** (#100, #103, #104, #106), **seven MEDIUM** (#98, #99, #101, #102, #105, #107, #108).
4. Extend "Behavioral regressions expected":
   - (#98) `_generate_negative_inputs` generates a list of variants including large-negative, NaN, inf; caller runs each.
   - (#99) `ADVERSARIAL_REASONING_EFFORT` env var; defaults "low"; researcher can override.
   - (#100) `print_token_cost_summary` reports both `avg_per_task` (all rows) and `avg_per_nonzero_task` (current behavior, renamed).
   - (#101) `find_subtask_for_gap` returns list; callers handle multi-match.
   - (#102) `build_schema_context` emits provenance-tagged upstream keys; flags collisions.
   - (#103) `run_pilot.py` wraps `run_condition` in a total-budget watchdog + per-task progress log.
   - (#104) Every `ThreadPoolExecutor.submit` inside a trace_context wraps the work item via `copy_context().run(...)`. Global-dict fallback removed. LangFuse trace_id captured at span-start time.
   - (#105) Backfill per-run buffer windows clipped by adjacent-run boundaries to prevent overlap attribution.
   - (#106) `prepare_cached_image` + probe-batch reuse; pip install runs once per task, not per probe.
   - (#107) `analyze_longitudinal.py` reports both incremental-Gini and cumulative-Gini per cycle; Chapter 4.5 discloses which is cited.
   - (#108) `ChainResult.final_output` property added; positional access to `step_results` documented as topological-order.
5. End-of-audit note: "Extended 2026-04-20 (tenth extension) with findings #98 - #108 (detail sections in `security_audit_2026_04_19_ext10.md`). Three reviewer batches submitted 71 total claims; 11 distinct findings confirmed (#98-#108), 60 logged as duplicates/not-reproducible/design-choice in §Claims not added."

---

**End of extension.**
