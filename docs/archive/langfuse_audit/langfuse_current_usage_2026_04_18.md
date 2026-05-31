# LangFuse — Current Usage Inventory (2026-04-18)

**Purpose:** file-by-file accounting of every LangFuse integration point across `src/`, `scripts/`, `sandbox/`, `tools/`. Paired with `langfuse_capabilities_2026_04_18.md` (what the platform offers) and `langfuse_gap_analysis_2026_04_18.md` (what we're missing against master plan §13.7).

**Scope of inspection:** 14 files in `src/` + 7 files in `scripts/`. `sandbox/` (Dockerfile only) and `tools/` (init_skill.py, quick_validate.py) do NOT use LangFuse.

---

## 1. Central helpers

### 1.1 `src/orchestrator/tracing.py` (~82 LOC)

**Role:** single shared event-emission helper for orchestrator nodes. Never-raises.

**API surface:**
```python
def emit_langfuse_event(
    langfuse_handler: object | None,  # CallbackHandler or trace_id str
    name: str,                         # e.g. "decomposition.timeout"
    level: str,                        # "DEFAULT" | "WARNING" | "ERROR"
    metadata: dict,
) -> None
```

**Implementation behavior:**
- Accepts either a `CallbackHandler` (reads `.last_trace_id`) or a raw trace-id string.
- Inherits tags + `session_id` from the active LangGraph config via `get_config()` in the parent metadata dict (`langfuse_tags`, `langfuse_session_id` keys).
- If trace_id is known → `lf.create_event(trace_context=TraceContext(trace_id=...), name=, level=, metadata=)`.
- If no trace_id → creates a standalone `lf.trace()` then calls `.event(...)` on it.
- Calls `lf.flush()` before returning.
- Silent no-op on any exception.

### 1.2 `src/cli/runner.py::_langfuse_config()`

**Role:** CLI-side config builder. Used by every `mint run` / `mint plan` / `mint retry` invocation.

**Builds:**
```python
{
    "callbacks": [LangFuseCallbackHandler()],
    "metadata": {
        "langfuse_session_id": "mint-run-{_run_id}",
        "langfuse_tags": ["mint-cli", ...dynamic...],
        "langfuse_user_id": "mint-cli",
        ...task-level metadata...
    },
}
```

**Propagation path:** `pipeline.invoke(config=config)` → LangGraph contextvar propagation → LangChain auto-traces every LLM call as a child span of the root trace.

### 1.3 `src/verification/adversarial.py::_langfuse_metadata` + `set_langfuse_metadata()`

**Role:** bridge for passing tags/session_id INTO the ThreadPoolExecutor-spawned adversarial DeepAgent probe (where `get_config()` doesn't work because it's thread-local).

**Pattern:**
```python
# Before spawning thread pool (in verification_node):
set_langfuse_metadata(lf_meta)

# Inside probe() worker thread:
langfuse_metadata = dict(_langfuse_metadata)  # module-level copy
config["callbacks"] = [LangFuseCallbackHandler()]
config["metadata"] = langfuse_metadata
```

**Why module-level state rather than a kwarg:** `probe()` is called via `ThreadPoolExecutor.submit(probe, artifact)` and adding a meta kwarg would require threading it through every call site. Module-level state is pragmatic.

---

## 2. Schemas — trace-id surface

### 2.1 `src/schemas/task.py::TaskState.trace_id`

```python
trace_id: str | None = Field(
    default=None,
    description="LangFuse trace ID for observability",
)
```

**Usage:** populated by verification_node from the active trace (via `langfuse_handler.last_trace_id`), threaded into downstream `emit_langfuse_event(state.trace_id, ...)` calls.

### 2.2 `src/schemas/evaluation.py`

**Status:** **no LangFuse field.** `EvaluationRecord` does NOT carry `trace_id`. This means post-eval DuckDB rows cannot be jump-linked to their LangFuse traces without a timestamp join.

---

## 3. Entry points (session + callback initialisation)

### 3.1 `scripts/run_pilot.py` — the main eval runner

```python
langfuse_handler = None
try:
    from langfuse.langchain import CallbackHandler
    langfuse_handler = CallbackHandler()
except Exception: ...

config = HarnessConfig(..., langfuse_handler=langfuse_handler)

# After eval completes:
if langfuse_handler is not None:
    from langfuse import get_client
    get_client().flush()
```

**Observations:**
- Handler is created but **NO session_id / tags set at the outer loop.** Per-task tags are set by the harness (`tests/evaluation/harness.py` lines 459-473 per CLAUDE.md). No grep hit here because run_pilot doesn't build the invoke_config itself — the harness does.
- Flush on exit.

### 3.2 `scripts/run_longitudinal.py`

```python
invoke_config["callbacks"] = [langfuse_handler]
invoke_config["metadata"] = {
    "langfuse_session_id": f"longitudinal-{args.seed}",
    "langfuse_tags": [
        "longitudinal", f"task:{task.task_id}",
        f"cycle:{cycle_idx}", ...
    ],
}
```

**Observations:**
- Session per longitudinal run.
- Per-task/cycle tags.
- Flush at exit.

### 3.3 `scripts/run_longitudinal_shift.py`

Near-identical to `run_longitudinal.py`; session = `f"longitudinal-shift-{args.seed}"`; additional tag `"longitudinal_shift"`.

### 3.4 `src/cli/runner.py`

- `_langfuse_config()` called at every user-facing command (`run`, `plan`, `retry`).
- Session = `f"mint-run-{_run_id}"`.
- Tags include: `"mint-cli"`, `"allow-network"` (if opted in), file-format tags.

---

## 4. Per-node event emission

### 4.1 `src/orchestrator/decomposition.py`

**Handler:** creates its OWN `LangFuseCallbackHandler()` because the DeepAgent is invoked as a child graph. Inherits parent metadata via `get_config()` before spawning.

**Events emitted:**
| Event | Level | Triggered by |
|---|---|---|
| `decomposition.no_structured_response` | WARNING | LLM returned without structured output |
| `decomposition.empty_subtasks` | WARNING | Decomposer returned 0 subtasks |
| `decomposition.timeout` | ERROR | 120s timeout hit |
| `decomposition.error` | ERROR | Uncaught exception |
| `decomposition.over_warn_limit` | WARNING | >N subtasks (soft warning) |
| `decomposition.hard_truncation` | CRITICAL | Hard-capped at MAX_CAPS |
| `decomposition.writer_appended` | INFO | File-writer subtask appended per Rule 15 |
| `decomposition.incoherent_chain` | ERROR | Chain coherence check failed |
| `decomposition.upstream_key_collision` | WARNING | Two predecessors produce same key |

**Sticky-flag events:** on 3-strike failure, emits ERROR event with `decomposition_succeeded=False` semantics (though the event name isn't `decomposition.sticky_fail` — it's one of the above, and the sticky flag is DB-native only).

### 4.2 `src/orchestrator/classifier.py`

**Events:**
| Event | Level | Triggered by |
|---|---|---|
| `classifier.llm_failure` | (default) | LLM call failed (raised exception) |
| `classifier.gap_classified` | (default) | Successful classification (per gap) |
| `classifier.fail_closed` | (default) | 3-strike exhausted → sticky flag |

**Handler:** `None` passed to `emit_langfuse_event()` — relies on `get_config()` inheritance. This works because classifier runs inside the LangGraph node context.

### 4.3 `src/orchestrator/verification_node.py`

**Handler:** reads `state.trace_id` directly.

**Events:**
| Event | Level | Triggered by |
|---|---|---|
| `verification.fail_closed` | ERROR | 3-strike exhausted (sticky `verification_succeeded=False`) |

**Bridge to adversarial:** calls `set_langfuse_metadata(lf_meta)` before spawning thread pool for adversarial probe.

### 4.4 `src/orchestrator/generators/mcp_tool.py`, `skill.py`, `agent.py`

**Handler:** inherits from pipeline-level CallbackHandler via LangChain contextvar propagation. The generators do NOT create new handlers.

**Events:**
- `*.llm_failure` / `*.repair_attempt` / `*.repair_failed` / `*.success` (or similar per-type variants — grep showed ~3-4 emit sites each).

### 4.5 `src/verification/adversarial.py`

**Handler:** creates new `CallbackHandler` (worker thread); inherits metadata via module-level `_langfuse_metadata`.

**Events:**
| Event | Level | Triggered by |
|---|---|---|
| `adversarial.agent_creation_error` | (default) | DeepAgent instantiation failed |
| `adversarial.completed` | (default) | Probe ran to completion, structured output returned |
| `adversarial.no_structured_response` | (default) | Probe completed but no structured output |
| `adversarial.timeout` | (default) | 480s timeout (Step 14.11 fail-closed) |
| `adversarial.error` | (default) | Uncaught exception |

**Note:** timeout event is NOT emitted at ERROR level despite being fail-closed. Mismatch with decomposition/verification sticky pattern.

### 4.6 `src/orchestrator/composition.py` — the RICHEST tracing

**`CompositionalExecutor` manually creates nested spans via `Langfuse().start_as_current_span()` context managers.**

**Span hierarchy:**
```
chain-executor (root trace span)
├── step-1/N: <cap_name> (step span)
│   └── [LLM calls auto-traced if SKILL/AGENT]
├── step-2/N: <cap_name>
│   └── ...
└── step-N/N: <cap_name>
```

**Chain-level span (`_start_langfuse_chain_trace`):**
```python
lf = Langfuse()
span_cm = lf.start_as_current_span(name="chain-executor")
span.update(
    input={"task_id": ..., "chain_length": ..., "steps": [...]},
    metadata={"chain_length": ...},
)
lf.update_current_trace(
    name="chain-executor",
    user_id="mint-cli",
    session_id=self.langfuse_session_id,
    tags=clean_tags,
)
```

**Step-level span (`_start_langfuse_step_span`):**
```python
step_cm = lf.start_as_current_span(name=f"step-{position+1}/{total}: {cap_name}")
span.update(
    input={"capability_name", "capability_type", "source", "subtask_description", "depends_on"},
    metadata={"capability_type", "source", "step_position", "step_total"},
)
```

**Step-end (`_end_langfuse_step_span`):**
```python
lf.update_current_span(
    output={"success", "latency_ms", "output_keys"},
    level="DEFAULT" if success else "WARNING",
)
```

**Chain-end (`_end_langfuse_chain_trace`):**
```python
lf.update_current_span(output={
    "success", "steps_completed", "failed_step_index", "failed_step_name",
})
lf.flush()
```

**Tag sanitisation:** LangGraph metadata serialization sometimes turns `list[str]` into `{"arrayValue": {}}` — composition strips these via `clean_tags = [str(t) for t in (tags or []) if isinstance(t, str)]`.

### 4.7 `src/optimizer/operations.py`

**Handler:** builds own via `_langfuse_config()` helper (local to this file):
```python
def _langfuse_config() -> dict:
    try:
        return {"callbacks": [LangfuseHandler()]}
    except Exception: return {}
```

**Used by:** `_merge_tools`, `_merge_skills`, `_merge_agents`, `_split_tools`, `_split_skills`, `_split_agents` — each builds a fresh `lf_config = _langfuse_config()` and threads into LLM calls.

**Events emitted (via `emit_langfuse_event(None, ...)` fallback):**
| Event |
|---|
| (3-4 events per merge/split — grep showed emit_langfuse_event calls at lines 349, 378, 399) |

**What's NOT there:**
- No explicit `pillar3.split_variant_generated` / `pillar3.merged` / `pillar3.pruned` events.
- No `pillar3.verification_rollback` event (this DOES get recorded via `metrics_store.record_event(name, "verification_rollback", reason)` → DuckDB `cycle_events`, but NOT as a LangFuse event).

---

## 5. Silent surfaces (NO LangFuse instrumentation)

Grepped and confirmed — these files have **zero** langfuse references:

| File | Role | What would be useful to trace |
|---|---|---|
| `src/orchestrator/loading_node.py` | Registers generated caps into registry | Registration events with capability_id + domain + is_generic |
| `src/verification/gate.py` | Three-tier gate orchestrator | Tier 0 / 1 / 1.5 / 2 / 3 pass/reject events with tier_reached + findings |
| `src/verification/static_scanner.py` | Tier 1 AST + regex scanner | Per-artifact scan event with findings list (severity distribution) |
| `src/verification/import_check.py` | Tier 1.5 import probe (Step 12.5, fresh) | Per-artifact import_check events (wrong_pip_package / import_error / fence_compile_error) |
| `src/verification/behavioral.py` | Tier 2 behavioral testing | Per-probe event with test case + pass/fail |
| `src/verification/coverage.py` | Step 7 MCP_TOOL coverage replay | Per-variant coverage event |
| `src/optimizer/loop.py` | Optimizer main loop (KL divergence compute) | Per-cycle KL value as LangFuse score |

**Why this matters:** master plan §13.7 calls out several of these (especially Tier 1.5, coverage, optimizer KL) as CRITICAL-band gaps against the Pillar 2 novelty claim and Chapter 5/6 analysis needs.

---

## 6. Downstream consumers (REST API readers)

### 6.1 `scripts/backfill_token_costs.py`

**Pattern:** `httpx` + LangFuse REST API (no SDK).

**Endpoints used:**
- `GET /api/public/observations?type=GENERATION&limit=100&page=N` — fetch all GENERATION spans in a window
- `GET /api/public/traces?sessionId=...` — fetch traces by session
- `GET /api/public/observations?traceId=...&type=GENERATION` — fetch observations per trace

**Writes:** `evaluation_runs.token_cost` (Step 18.4 scope — token-cost backfill per record).

### 6.2 `scripts/analyze_context_utilization.py`

**Pattern:** `httpx` + LangFuse REST API.

**Reads:** traces with per-observation `input` field; computes peak `input_tokens / context_window` per agent/task. This measures T3 under-compression half (proposal §Bootstrapping T3 "hits token limits and introduces noise").

**Consumer of:** every LLM call's GENERATION span with populated input-token counts.

### 6.3 `scripts/analyze_results.py`

**NO LangFuse usage.** Reads only from DuckDB (`evaluation_runs`, `invocation_events`, `cycle_utility_scores`, `cycle_events`). Output: Chapter 6 metric tables (M1 success, M3 Gini, M4 token cost via backfilled column, M5 latency via Step 18.9, M2 contamination via Step 18.10).

### 6.4 `scripts/analyze_longitudinal.py`

**NO LangFuse usage.** Reads only from DuckDB (`cycle_utility_scores`, `cycle_events`). Output: KL convergence, per-type op counts, distribution-shift verdict.

### 6.5 `scripts/analyze_compression_loss.py` (Step 13.8, new)

**NO LangFuse usage.** Reads only from DuckDB (`evaluation_runs.failed_step_reason`).

---

## 7. What's captured today — summary by proposal-critical surface

| Surface | LangFuse? | DuckDB? | Notes |
|---|---|---|---|
| Per-pipeline invocation (root trace) | ✅ via CLI/harness | — | Tagged with run_id, condition, task_id |
| Per-LLM-call GENERATION span | ✅ auto (LangChain callback) | — | Token costs inferred |
| Chain-executor span + per-step spans | ✅ rich (composition.py) | — | Best-instrumented surface |
| Decomposition events (9 categories) | ✅ | `errors` list | Sticky flag is DB-only |
| Classifier events (3 categories) | ✅ | — | — |
| Per-generator events | ✅ | — | ~3-4 per type |
| Adversarial events (5 categories) | ✅ | — | Timeout NOT at ERROR level |
| Verification fail-closed | ✅ | sticky flag | |
| **Tier 1/2/3 results** | ❌ | `VerificationReport` → state.verification_reports | No per-tier LangFuse event |
| **Tier 1.5 import-probe results** | ❌ | findings list | Fresh Step 12.5, never LangFuse-wired |
| **verify_coverage results** | ❌ | `cycle_events` `verification_rollback` only | |
| **Pillar 3 operations (prune/split/merge)** | ❌ | `cycle_events` | DuckDB-only |
| **Per-cycle KL value** | ❌ | `cycle_utility_scores` | DuckDB-only |
| **distribution_shift event** | ❌ | `cycle_events` | DuckDB-only |
| **loading_node register_tool** | ❌ | `invocation_events`, registry | |
| **User feedback (mint feedback --wrong)** | ⚠️ | `InvocationRecord.actual_output` | Unclear if LangFuse span carries `user_judgment=wrong` tag |
| **EvaluationRecord ↔ LangFuse trace_id cross-reference** | ❌ | — | `EvaluationRecord` has no trace_id field |

---

## 8. Metadata propagation audit

### 8.1 What the pipeline SHOULD see

When CLI invokes `pipeline.invoke(state, config=_langfuse_config(session_id=..., tags=..., user_id=...))`, LangFuse automatically sets:
- Root trace tagged with all metadata
- All child LLM calls (decomposer, classifier, generators, verifier) inherit the root trace's session/user/tags
- Auto-emitted GENERATION spans for every `init_chat_model(...)` call

### 8.2 Where inheritance BREAKS

1. **DeepAgent sub-invocations** (decomposition, adversarial) — they're invoked via separate `.invoke()` calls inside the node, so `config={"callbacks": [...]}` must be explicitly passed. Both files handle this: decomposition reads parent metadata via `get_config()`; adversarial reads via module-level `set_langfuse_metadata()` bridge.

2. **ThreadPoolExecutor-wrapped calls** (adversarial probe) — `get_config()` is thread-local; hence the module-level bridge.

3. **CompositionalExecutor** — manages its own spans via `start_as_current_span` + explicitly reads `self.langfuse_session_id` passed in via constructor.

4. **Operations (optimizer merge/split)** — creates a NEW `LangfuseHandler()` for each LLM call. This means the resulting traces do NOT inherit the outer pipeline's session_id / tags. **This is a propagation gap** — merge/split LLM traces appear as orphaned traces in LangFuse, not linked to the triggering task.

### 8.3 Tag / session propagation cheat-sheet

| Surface | How metadata flows |
|---|---|
| CLI → pipeline | `_langfuse_config()` builds metadata dict; LangChain callback reads it |
| Pipeline → decomposition | DeepAgent reads via `get_config()` before thread pool |
| Pipeline → generators | Auto-inherit via LangChain contextvars |
| Pipeline → verification_node → adversarial | Module-level `set_langfuse_metadata()` bridge |
| Pipeline → composition (inside artifact execution) | Passed explicitly via `langfuse_tags` / `langfuse_session_id` constructor kwargs |
| Pipeline → optimizer operations | ❌ NOT propagated — merge/split traces are orphaned |

---

## 9. Open questions (answered in gap-analysis doc)

1. Is `emit_langfuse_event` actually attaching events to the parent trace in all cases, or is it creating standalone traces when `trace_id` is None? (Grep: it falls back to `lf.trace(...)` + `.event(...)` — orphaned trace, discoverable via tags only.)
2. Are classifier events attached to anything? (Grep: `emit_langfuse_event(None, ...)` with no handler → relies on `get_config()` inheritance in tracing.py; if that fails, event is emitted on a fresh trace tagged with `langfuse_tags`.)
3. Is there per-tier event data or is tier info only in `VerificationReport.findings[].tier`? (Grep: only in findings, not as LangFuse events.)

These are consolidated in `langfuse_gap_analysis_2026_04_18.md`.

---

**Last updated:** 2026-04-18, pre-Step-13.7.
