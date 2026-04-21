# LangFuse — Capabilities Reference (2026-04-18)

**Purpose:** distilled reference of what LangFuse's Python SDK v3 + REST API make available. Cross-checked against `https://langfuse.com/llms-docs.txt` and linked sub-pages on 2026-04-18. Paired with `langfuse_current_usage_2026_04_18.md` (what we use) and `langfuse_gap_analysis_2026_04_18.md` (what to add).

**Scope:** only features relevant to a MINT-style eval-harness use case. Omits OpenTelemetry wire-format details, multi-tenancy, self-hosting ops, and JS/TS SDK.

---

## 1. The core data model (what lives in LangFuse)

```
Session  (group of related traces)
  └── Trace (one request / task invocation)
        └── Observation  (step within a trace; nestable)
              ├── type: SPAN        — arbitrary scoped work
              ├── type: GENERATION  — LLM call (has token + cost fields)
              ├── type: EVENT       — point-in-time discrete event
              └── type: AGENT / TOOL / RETRIEVER / EMBEDDING / GUARDRAIL
                         (newer typed observations, Python SDK v3)
        └── Score (attached to trace OR observation OR session)
```

**Key relationships:**
- Session ↔ Trace: 1-to-many. Group identifier is the free-form `session_id` string (≤200 chars US-ASCII).
- Trace ↔ Observation: 1-to-many. Observations nest within each other (parent-child).
- Trace ↔ Score: many-to-many. Scores attach at any level.

---

## 2. Trace-level attributes

Every trace can carry:

| Attribute | Type | Use |
|---|---|---|
| `id` | str (auto or user-supplied) | unique trace ID |
| `name` | str | trace label (e.g. `"chain-executor"`) |
| `session_id` | str | groups traces into sessions |
| `user_id` | str | groups traces by end user |
| `tags` | list[str] | filterable labels, aggregated across observations |
| `metadata` | dict | arbitrary JSON-serialisable context |
| `version` | str | release/build identifier |
| `release` | str | longer-form version |
| `public` | bool | make trace URL shareable |
| `input` | any | trace input payload |
| `output` | any | trace output payload |

**Tag aggregation quirk:** *"The full set of tags applied across all observations in a trace are automatically aggregated and added to the trace object in Langfuse."* — you can tag an observation and the parent trace picks it up.

---

## 3. Observation types + attributes

All observation types share:
| Attribute | Type |
|---|---|
| `id` | str |
| `trace_id` | str |
| `parent_observation_id` | str | null |
| `type` | enum: SPAN / GENERATION / EVENT / AGENT / TOOL / RETRIEVER / EMBEDDING / GUARDRAIL |
| `name` | str |
| `start_time` / `end_time` | datetime |
| `input` / `output` | any |
| `metadata` | dict |
| `level` | enum: `DEBUG` / `DEFAULT` / `WARNING` / `ERROR` |
| `status_message` | str |
| `version` | str |

**GENERATION-specific** (LLM calls):
- `model` — model name
- `model_parameters` — temp/top_p/etc
- `usage_details` — `{input, output, total}` token counts + extended keys: `cached_tokens`, `audio_tokens`, `image_tokens`, `reasoning_tokens`
- `cost_details` — `{input, output, total}` in USD
- `prompt_name` + `prompt_version` — link to LangFuse prompt management
- `completion_start_time` — for time-to-first-token

**EVENT-specific** (point-in-time markers):
- Otherwise same shape as SPAN, just no duration.

**Newer v3 typed observations** (AGENT / TOOL / RETRIEVER / EMBEDDING / GUARDRAIL):
- Same shape as SPAN but surface differently in the UI + aggregate differently.
- AGENT type is specifically for "LLM agent orchestration" — fits our DeepAgent invocations.

---

## 4. Python SDK v3 — core methods

### 4.1 Initialisation

```python
from langfuse import get_client, Langfuse

lf = get_client()    # uses env vars LANGFUSE_{PUBLIC,SECRET}_KEY + LANGFUSE_HOST
# or
lf = Langfuse(public_key=..., secret_key=..., host=...)
```

### 4.2 Context-manager API (recommended pattern)

```python
with lf.start_as_current_span(name="operation") as span:
    span.update(input={...}, output={...}, metadata={...})

    with lf.start_as_current_observation(as_type="generation", name="llm") as gen:
        gen.update(
            model="gpt-5-mini",
            input=[...],
            output="...",
            usage_details={"input": 100, "output": 50, "total": 150},
        )
```

**Key methods on the context-manager span:**
- `.update(input=, output=, metadata=, level=, status_message=, version=)` — mutate the span
- `.update_trace(session_id=, user_id=, tags=, metadata=, name=)` — mutate the PARENT trace
- `.score(name=, value=, data_type=, comment=)` — attach a score to THIS observation
- `.score_trace(name=, value=, ...)` — attach a score to the parent trace

### 4.3 `@observe` decorator

```python
from langfuse import observe

@observe(as_type="generation")
def call_llm(prompt: str) -> str:
    ...
```

Auto-wraps the function call in a trace/observation.

### 4.4 Ambient updates to the active trace/span

```python
lf.update_current_span(output=..., metadata=..., level="WARNING")
lf.update_current_trace(tags=[...], session_id=..., user_id=..., metadata=...)
```

**Thread-locality caveat:** the "current" span/trace is tracked via contextvars — doesn't cross ThreadPoolExecutor boundaries without manual propagation (OpenTelemetry context APIs or explicit trace_id threading).

### 4.5 `create_event` for discrete markers

```python
from langfuse.types import TraceContext

lf.create_event(
    trace_context=TraceContext(trace_id="..."),  # optional parent
    name="my.event",
    level="WARNING",
    metadata={"key": "value"},
)
```

Used for one-shot markers (errors, thresholds crossed, state transitions).

### 4.6 `propagate_attributes()` context manager

```python
from langfuse import propagate_attributes

with propagate_attributes(session_id="sess-123", user_id="u42", tags=["foo"]):
    # Any child spans inside this block inherit the attributes.
    ...
```

**Useful for** scope-based metadata propagation without having to touch every `.invoke()` call site.

### 4.7 Scores (evaluation signals)

```python
lf.score(
    name="contamination_rate",
    value=0.15,
    data_type="NUMERIC",   # "NUMERIC" | "CATEGORICAL" | "BOOLEAN"
    comment="Step 18.10 M2 audit result",
    trace_id="...",          # or observation_id="..." or session_id="..."
    id="audit-2026-04-18",   # idempotency key — re-submitting updates instead of duplicating
    config_id="...",         # optional link to a ScoreConfig for validation
)
```

**Four data types:**
- **NUMERIC** — float (e.g., KL divergence, contamination rate, latency)
- **CATEGORICAL** — string (e.g., "graceful" / "sluggish" / "collapse" adaptation verdict)
- **BOOLEAN** — 0 or 1 (e.g., gate passed)
- **TEXT** — 1-500 chars (e.g., human audit comment)

**Attachment levels:** trace, observation, session, dataset-run.

**Score sources** (surfaces in UI): `API` (SDK), `ANNOTATION` (human), `EVAL` (LLM-as-judge), `HUMAN` (feedback).

**Why scores matter:** they're first-class aggregation units in the LangFuse UI — dashboards can plot "avg contamination rate by condition over time" if we push M2 results as scores.

### 4.8 `flush()` — critical for short-lived processes

```python
lf.flush()   # ensure async buffer flushed before exit
```

**Without flushing**, eval processes exiting quickly may lose the last N traces. All our entry points call this.

---

## 5. LangChain / LangGraph integration

### 5.1 CallbackHandler (the vehicle we use)

```python
from langfuse.langchain import CallbackHandler

handler = CallbackHandler()
chain.invoke(
    {...},
    config={
        "callbacks": [handler],
        "metadata": {
            "langfuse_user_id": "...",
            "langfuse_session_id": "...",
            "langfuse_tags": ["tag1", "tag2"],
        },
    },
)
```

**Auto-behavior:**
- Every LLM call → GENERATION observation
- Every chain step → SPAN observation
- Root chain → TRACE
- `langfuse_user_id` / `langfuse_session_id` / `langfuse_tags` metadata keys are read from the `config["metadata"]` dict and applied to the trace

**Attribute hoisting:** the handler hoists the special `langfuse_*` metadata keys OUT of the metadata dict (they don't appear in the trace metadata; they appear as first-class trace attributes). Other keys stay as metadata.

### 5.2 Adding metadata mid-chain

Once inside a LangChain run, you can still reach the current trace:

```python
from langfuse import get_client

lf = get_client()
lf.update_current_trace(tags=[...], metadata={...})
```

This works because the LangChain callback sets the trace as "current" via contextvars for the duration of the invoke.

### 5.3 `last_trace_id` on the handler

```python
handler = CallbackHandler()
chain.invoke(..., config={"callbacks": [handler]})
print(handler.last_trace_id)  # ID of the just-completed trace
```

**Useful for** post-hoc attaching events/scores to the specific trace just produced.

---

## 6. Tags — propagation and aggregation

**Tag-level features:**
- Tags are per-observation, then auto-aggregated up to the trace.
- Tag strings max 200 chars (truncated silently).
- No limit on number of tags per observation.
- Filterable in UI: `trace.tags CONTAINS "foo"`.
- Filterable in REST API: `GET /api/public/traces?tags=...`.

**Propagation patterns:**
1. **Explicit in `langfuse_tags` metadata** — applied to the root trace only.
2. **`propagate_attributes(tags=[...])` context** — applied to every child observation inside the block.
3. **Manual `span.update(metadata={"tags": [...]})`** — attaches to a specific observation; auto-aggregates to the trace.

**Use cases in a MINT-style eval:**
- Per-task tag: `"task:T04"`
- Per-condition tag: `"condition:full"`
- Per-tier tag: `"tier:3"` on Tier 3 spans
- Per-pillar-3-op tag: `"pillar3:merge"`
- Per-failure-mode tag (for Chapter 4.1 citation): `"contamination:hardcoded_data"`

---

## 7. Sessions — grouping traces

**Session features:**
- Free-form `session_id` string — opaque to LangFuse, used for grouping.
- UI: "Session Replay" view shows the full interaction timeline across traces in the session.
- Aggregated metrics: total cost, total tokens, total latency, trace count, success rate per session.
- Shareable URLs: `https://<host>/project/<id>/sessions/<session_id>`.

**Design implications for MINT:**
- Currently: `session_id = "mint-run-{run_id}"` for CLI; `"longitudinal-{seed}"` for longitudinal. Per-CLI-invocation / per-longitudinal-sweep granularity.
- Could be finer: `session_id = "{run_id}:{task_id}"` for per-task groupings within a run. Would let Chapter 6 cite "session X has all Tier 1/2/3 + retry events for T04 under condition=full".

---

## 8. Users — per-user aggregation

**User features:**
- `user_id` string — any unique identifier.
- UI: per-user view with cost / token / trace count aggregations.
- Metrics API: programmatic per-user cost queries.

**Currently:** all MINT traces tagged `user_id="mint-cli"` — essentially single-user from LangFuse's POV. Could differentiate (e.g., `user_id="eval-harness"` vs `"cli-user"`) for aggregate-by-source reporting.

---

## 9. Scores + dashboards

**Score surfaces in LangFuse UI:**
- Trace-level score tab (histogram by score name).
- Session-level aggregation (mean/min/max per score per session).
- Dashboard widgets ("avg score over time", "score distribution by tag").
- Annotation queue UI (route traces to a human for manual scoring — relevant for M2 audit).

**Score use cases for MINT Chapter 6:**
- `m2.contamination_rate` (NUMERIC, per trace) — attach audit result.
- `m6.kl_divergence` (NUMERIC, per cycle span) — attach KL value.
- `m1.success` (BOOLEAN, per trace) — gate + output_in_range.
- `m5.time_to_first_correct_ms` (NUMERIC, per trace) — successful-run latency.
- `pillar3.operation_type` (CATEGORICAL, per optimizer cycle span) — "prune" / "split" / "merge".
- `tier3.contamination_found` (BOOLEAN, per verification span) — Tier 3 rejection signal.

---

## 10. Prompt management (not currently used, worth flagging)

LangFuse Prompt Management lets you:
- Store prompts versioned in LangFuse (outside the codebase).
- Fetch via SDK: `lf.get_prompt("name", version="production")`.
- Automatically link each LLM call to the prompt version used via `prompt_name` + `prompt_version` on the GENERATION observation.
- A/B test prompts with traffic splitting.

**Relevance to MINT:** the project has many prompts (`src/orchestrator/prompts/*.py`). Currently stored in code — version-controlled via git. Moving to LangFuse prompts would enable runtime A/B testing (not useful pre-thesis) but also trace-time prompt linkage (useful for Chapter 4 debugging). **Post-eval / Chapter 7 consideration; not pre-eval work.**

---

## 11. Datasets + experiments (not currently used, worth flagging)

LangFuse Datasets:
- Input/expected-output pairs, JSON-schema validated, versioned.
- Folder organisation via name slashes.

LangFuse Experiments:
- Run dataset through application → compare outputs → attach scores → compare across runs.
- UI: side-by-side experiment comparison.

**Relevance to MINT:** the 20-task testbed (`tests/evaluation/tasks.py`) IS effectively a dataset. Could be pushed to LangFuse as a first-class dataset; each eval run becomes a LangFuse experiment with M1-M6 scores per task. Would give Chapter 6 a native UI for "compare full vs no_verification across all 400 records".

**Trade-off:** requires synchronising tasks.py → LangFuse dataset at eval-setup time; adds a dependency on LangFuse for experiment tracking that we currently handle in DuckDB. **Chapter 7 future work, not pre-eval.**

---

## 12. REST API — what `scripts/backfill_token_costs.py` already uses, and what else exists

**Auth:** HTTP Basic with Public Key (user) + Secret Key (password).

**Observability endpoints:**

| Endpoint | Purpose |
|---|---|
| `GET /api/public/traces` | Query traces; filter by `sessionId`, `userId`, `tags`, `name`, `fromTimestamp`, `toTimestamp` |
| `GET /api/public/traces/{id}` | Single trace + children |
| `GET /api/public/observations` | Query observations; filter by `traceId`, `type`, `name`, `level` |
| `GET /api/public/observations/{id}` | Single observation |
| `GET /api/public/sessions` | Sessions list + metadata |
| `POST /api/public/scores` | Create scores (alternative to SDK) |
| `GET /api/public/scores` | Query scores; filter by `name`, `dataType`, `traceId`, `fromTimestamp` |
| `GET /api/public/metrics` | **Aggregated analytics** — cost, tokens, trace count per dimension |

**Currently used in scripts:**
- `backfill_token_costs.py`: `GET /observations?type=GENERATION`, `GET /traces?sessionId=...`, `GET /observations?traceId=...`
- `analyze_context_utilization.py`: same endpoints, different aggregation

**Not currently used but relevant:**
- `POST /scores` — push M2 audit results as scores (alternative to DuckDB human_label column).
- `GET /metrics` — per-condition cost/token aggregation without custom SQL.
- Filter by `level=ERROR` — fetch all sticky-flag / fail-closed events in one call.

---

## 13. Observation levels — practical uses

| Level | When to use | Currently used for |
|---|---|---|
| `DEBUG` | Verbose tracing | ❌ Not used |
| `DEFAULT` | Normal operation | All successful events |
| `WARNING` | Soft failures, degraded behavior | `decomposition.over_warn_limit`, `decomposition.empty_subtasks`, step span failures |
| `ERROR` | Hard failures | `decomposition.timeout`, `decomposition.incoherent_chain`, `verification.fail_closed` |
| `CRITICAL` (inferred from code) | Catastrophic | `decomposition.hard_truncation` |

**UI behavior:** filter traces by level → quickly find failures. Level filters feed dashboard error-rate widgets.

---

## 14. Features we currently DON'T use (candidate adoptions)

| Feature | What it offers | Relevance |
|---|---|---|
| **`.score()` on spans/traces** | First-class numeric/categorical/boolean scores | Push M1-M6 results as scores for LangFuse-native dashboards |
| **`@observe` decorator** | Auto-instrument functions | Could auto-trace `static_scanner.scan()`, `import_check.run_import_check()`, etc. with 1 line |
| **`propagate_attributes()` context** | Scope-based tag/session propagation | Clean alternative to threading `langfuse_tags=` kwargs everywhere |
| **Typed observations (AGENT / TOOL)** | Semantic span types for UI differentiation | Mark adversarial DeepAgent as AGENT, composition step as TOOL |
| **Prompt management** | Version-controlled prompts in LangFuse | Post-eval consideration |
| **Datasets + experiments** | First-class eval tracking | Post-eval consideration |
| **Annotation queues** | Human-in-the-loop score UI | Could replace custom `audit_contamination.py` CLI with LangFuse-native audit workflow — but CLI already works |
| **Metrics API** (`GET /metrics`) | Pre-aggregated analytics | Could simplify `backfill_token_costs.py` |
| **Score configs** (validation schemas for scores) | Enforce value ranges / categories | Useful for M2 label validation |
| **Public/shareable traces** | Link traces in thesis write-up | **Very useful** — Chapter 4.1 cites live trace URLs |

---

## 15. Observations the LangFuse llms-docs.txt surfaced

- **The llms-docs.txt itself is an INDEX**, not full content. Deep pages (like `/docs/observability/data-model`, `/docs/scores/custom`, `/integrations/frameworks/langchain`) carry the actual API details.
- Deep-fetches revealed that several of the v3 SDK features (typed observations, `start_as_current_observation` with `as_type=`) are newer than the v2 patterns in a lot of online tutorials. Composition.py already uses v3 idioms (`start_as_current_span`); the tracing.py helper uses v2-compatible `Langfuse().create_event()` which still works.
- `propagate_attributes()` as a context manager is a clean pattern we haven't adopted — would replace the module-level `_langfuse_metadata` bridge in `adversarial.py`.
- Score submission via SDK OR REST is dual-homed — can submit from Python OR post-hoc from a script without touching the eval harness.

---

**Last updated:** 2026-04-18, via `langfuse.com/llms-docs.txt` + 8 deep-page fetches.
