# LangFuse Tagging Audit — Coverage Before + After Step 13.7 (2026-04-18)

**Purpose:** audit what we tag on LangFuse traces so Chapter 6 analysis-time filtering is effective. Paired with:

- `docs/langfuse_current_usage_2026_04_18.md` — file-by-file integration inventory
- `docs/langfuse_capabilities_2026_04_18.md` — what LangFuse offers
- `docs/langfuse_gap_analysis_2026_04_18.md` — event / span / score gap analysis

This doc focuses specifically on **TAG** coverage (filterable dimensions for the UI + REST API), not event emission.

---

## 1. Why tagging coverage matters for Chapter 6

Chapter 6 (Results) + Chapter 5.3/5.4 (Pillar 3 + convergence) analysis will need to answer queries like:

- *"Show me all **traces under condition=full** where **Tier 3 fired** and **gate rejected**"* → filter by `condition`, `tier:3`, outcome
- *"Compare per-capability-type success rates — **all AGENT traces** vs **all MCP_TOOL traces**"* → filter by `capability_type`
- *"How many traces **per task**, **per condition**, reached **Tier 3**?"* → filter + aggregate
- *"Show me **pilot-excluded** traces only"* → filter by `pilot` (negated)
- *"All traces from the **eval harness** (not CLI / longitudinal)"* → filter by `user_id`
- *"Traces where **network access was enabled**"* → filter by `allow_network`

Every question above ideally resolves via a single LangFuse UI filter or a single `/api/public/traces?tags=...` REST call. Each dimension not tagged either pushes the analysis to (a) metadata-scan (slow, complex), (b) DuckDB join via `EvaluationRecord.langfuse_trace_id` (Batch B6 wired this — works but less ergonomic), or (c) fetching every trace's observations (expensive).

---

## 2. Root-trace tag inventory — BEFORE Step 13.7

| Surface | user_id | session_id | tags set | Chapter 6 gap? |
|---|---|---|---|---|
| **CLI runner** (`src/cli/runner.py::_langfuse_config`) | `mint-cli` | `mint-run-{_run_id}` | `mint-cli`, `allow-network?` (conditional), file-format tags | — (CLI not a Chapter 6 surface) |
| **Eval harness** (`tests/evaluation/harness.py`) | **❌ not set** | `run_id` (e.g. `full_00_abc123`) | `condition`, `task_id`, `tier:{X}`, `format:{X}` | Significant — primary Chapter 6 surface |
| **Longitudinal** (`scripts/run_longitudinal.py`) | **❌ not set** | `longitudinal-{seed}` | `longitudinal`, `task_id`, `tier:{X}`, `step:{N/M}`, `format:{X}` | Significant — Chapter 5.4 surface |
| **Longitudinal-shift** (`scripts/run_longitudinal_shift.py`) | **❌ not set** | `longitudinal-shift-{seed}` | `longitudinal_shift`, `phase:{A/B}`, `task_id`, `tier:{X}`, `cycle:{N/M}`, `shift_at:{X}`, `format:{X}` | Significant — Chapter 6 RQ #3 surface |
| **Composition step span** (nested) | inherits | inherits | inherits | N/A (observation not trace) |

**The pattern:** tag coverage was decent for what's there — but several proposal-critical dimensions were silently missing on the two most important surfaces (eval harness + longitudinal).

---

## 3. Eight tagging gaps identified

### 🔴 Critical — blocks common Chapter 6 queries

| # | Gap | Consequence |
|---|---|---|
| 1 | Eval harness has **no `langfuse_user_id`** | LangFuse UI can't filter "all eval-harness traces" vs "all CLI traces" — everything shows as "unassigned user" |
| 2 | Eval harness has **no `allow_network` tag** | CLI has `"allow-network"` tag; harness doesn't. Inconsistent — can't filter network-enabled runs across both surfaces in one query |
| 3 | Eval harness has **no `pilot` tag** | Pilot exclusion requires session_id prefix matching or fetching `is_pilot` metadata field on every trace |
| 4 | **No `capability_type` tag** on root trace (harness or CLI) | Can't filter "show all AGENT traces" — must inspect each trace's step spans or join DuckDB |
| 5 | **No `static_coverage` tag** (retrieval T01-T03 vs synthesis T04+) | Harness has metadata field but not a tag; retrieval-vs-synthesis splits require metadata scan |

### 🟠 Important — recoverable via DuckDB join but awkward

| # | Gap | Consequence |
|---|---|---|
| 6 | Longitudinal scripts set no `langfuse_user_id` | Same as harness — all traces appear unassigned |
| 7 | Longitudinal scripts don't tag a consistent **`condition`** value | Treats longitudinal/longitudinal_shift as its own implicit condition via session_id but no tag to filter it natively |
| 8 | **No `chain_length`** tag on chain-executor trace | Can't filter "show only multi-step chain traces" without metadata scan |

### 🟡 Deferred — require post-hoc API (not pre-eval scope)

| Item | Why deferred |
|---|---|
| Outcome tags (`failure:{reason}`, `tier_reached:{N}`, gate pass/reject) on root trace | Post-hoc trace-tag update in SDK v3 requires active trace context; after `pipeline.invoke()` returns the trace is closed. Workaround: REST API `PUT /traces/{id}` — not wired, not necessary given Chapter 6 can join via `EvaluationRecord.langfuse_trace_id` (Batch B6). |
| Pillar 3 op tags (`pillar3:split` etc.) on root trace when operations fire | Same constraint. Plus the `pillar3.*` events (Batch A3) already let Chapter 5.3 filter "traces that emitted a pillar3 event" by event name. |
| Contamination category tags (M2 audit) | Post-audit annotation — can be attached via REST API or via LangFuse annotation queue UI. Chapter 7 polish, not pre-eval. |

---

## 4. What Step 13.7 Batch A/B/C already fixed implicitly

Before this audit, several of the "no tag on outcome" concerns were mitigated by the **events** shipped in Batch A/B/C:

| Chapter 6 query | Pre-13.7 | Post-13.7 |
|---|---|---|
| *"Where did verification stop?"* (tier_reached) | Metadata scan | **✅ filter for `verification.tier_N.{pass,reject}` events** (A1) |
| *"Which traces had Pillar 3 operations fire?"* | Metadata scan | **✅ filter for `pillar3.*` events** (A3) |
| *"Which traces had M6 KL values?"* | N/A | **✅ `m6.kl_divergence` score attached** (A4) |
| *"Which traces crossed the distribution-shift boundary?"* | N/A | **✅ `longitudinal.distribution_shift` event** (A5) |
| *"Which traces registered new capabilities?"* | N/A | **✅ `registry.register_ok` events** (C1) |

So **outcome tags** on the root trace would be convenience-only — the event-based queries already work. Deferring them is defensible.

---

## 5. Fixes landed 2026-04-18

### Harness (`tests/evaluation/harness.py` lines 523–570)

Added:
- `langfuse_user_id = "eval-harness"` — differentiates eval traces from CLI (`mint-cli`) + longitudinal (`longitudinal-runner` / `longitudinal-shift-runner`) in LangFuse user dashboards.
- `f"allow_network:{true|false}"` tag — mirrors CLI's `allow-network` tag. Consistent network-policy filtering across all surfaces.
- `"pilot"` tag (conditional — only set when `is_pilot=True`) — fast pilot exclusion.
- `f"static_coverage:{true|false}"` tag — retrieval-T01-T03 vs synthesis-T04+ filtering.
- `"is_pilot": is_pilot` metadata field — for programmatic access via REST API.
- `"allow_network": self._config.allow_network` metadata field — ditto.

### Longitudinal scripts

Added to both `run_longitudinal.py` and `run_longitudinal_shift.py`:
- `langfuse_user_id = "longitudinal-runner"` / `"longitudinal-shift-runner"` — per-surface differentiation.
- `"condition:longitudinal"` / `"condition:longitudinal_shift"` tag — treats longitudinal as its own proposal-baseline-level condition for filter symmetry.
- `f"static_coverage:{true|false}"` tag — retrieval vs synthesis distinction also applies under longitudinal.

### Composition (`src/orchestrator/composition.py::_start_langfuse_chain_trace`)

Added per-capability-type tagging at chain-executor start:
- One `capability_type:X` tag per distinct type present in the chain's resolution dict (built from `self.resolution.values()`).
- `chain_length:N` tag.

**This means a trace for a multi-step chain involving both MCP_TOOL and AGENT steps gets BOTH tags** — so a filter "capability_type:agent" matches all traces that touched an AGENT, regardless of whether they also touched an MCP_TOOL.

---

## 6. Post-13.7 root-trace tag inventory

| Surface | user_id | tags (expanded) |
|---|---|---|
| CLI runner | `mint-cli` | `mint-cli`, `allow-network?`, `format:{X}...` |
| Eval harness | **`eval-harness`** | `condition`, `task_id`, `tier:{X}`, `format:{X}...`, **`allow_network:{true/false}`**, **`static_coverage:{true/false}`**, `pilot` (conditional) |
| Longitudinal | **`longitudinal-runner`** | `longitudinal`, **`condition:longitudinal`**, `task_id`, `tier:{X}`, `step:{N/M}`, **`static_coverage:{true/false}`**, `format:{X}...` |
| Longitudinal-shift | **`longitudinal-shift-runner`** | `longitudinal_shift`, **`condition:longitudinal_shift`**, `phase:{A/B}`, `task_id`, `tier:{X}`, `cycle:{N/M}`, `shift_at:{X}`, **`static_coverage:{true/false}`**, `format:{X}...` |
| Composition (chain-executor span) | inherits | inherits + **`capability_type:{X}`** (per distinct type) + **`chain_length:{N}`** |

Bolded entries are additions from Step 13.7 tagging audit.

---

## 7. Chapter 6 query recipes (with fixed tag coverage)

Now feasible via single-call LangFuse filters:

| Query | Filter |
|---|---|
| All eval-harness traces | `user_id = "eval-harness"` |
| All full-condition traces | `tags CONTAINS "full"` |
| All T04 traces under no_verification | `tags CONTAINS "no_verification" AND tags CONTAINS "T04"` |
| All AGENT-touching traces | `tags CONTAINS "capability_type:agent"` |
| All traces with Tier 3 firing | `observations HAS event "verification.tier_3.pass" OR "verification.tier_3.reject"` |
| All traces where Pillar 3 operation fired | `observations HAS event "pillar3.*"` |
| All non-pilot traces | `tags NOT CONTAINS "pilot"` |
| All network-enabled traces | `tags CONTAINS "allow_network:true"` OR `tags CONTAINS "allow-network"` |
| All retrieval-task traces (T01-T03) | `tags CONTAINS "static_coverage:true"` |
| All multi-step compositional traces | `tags NOT CONTAINS "chain_length:1"` |
| All distribution-shift runs | `tags CONTAINS "longitudinal_shift"` |
| Phase-B-only shift traces | `tags CONTAINS "longitudinal_shift" AND tags CONTAINS "phase:B"` |

---

## 8. What's still DuckDB-native-only (by design)

Queries that legitimately require DuckDB + `langfuse_trace_id` join (Batch B6) rather than pure LangFuse filters:

| Query | Why DuckDB-only |
|---|---|
| M2 contamination rate per condition | Human labels live on `evaluation_runs.human_label`; LangFuse annotation queue is Chapter 7 |
| M3 Gini coefficient of invocation counts | Computed from `invocation_events` table; LangFuse doesn't aggregate Gini |
| Per-artifact source bodies for M2 audit | `generated_artifact_sources` JSON column (Step 18.10 Option B) |
| Chain failure classification (compression-loss) | `failed_step_reason` column (Step 13.8) + classifier in `analyze_compression_loss.py` |

These are expected — DuckDB is the primary persistence for these; LangFuse is observability over the runtime. The `langfuse_trace_id` cross-reference (Batch B6) lets Chapter 6 link from any DuckDB row to its LangFuse trace for debugging/citation.

---

## 9. What we're still NOT tagging (and why that's OK)

| Item | Why not tagged |
|---|---|
| Outcome (success/fail) on root trace | Deferred — post-hoc tag update requires REST API; DuckDB `gate_passed AND output_in_range` field answers the same question. |
| `tier_reached:N` on root trace | Deferred — the per-tier events (A1) already let you filter by "observations HAS event verification.tier_N.*"; DuckDB `verification_tier_reached` field also works. |
| `failure:{reason}` on root trace | Deferred — same as above. DuckDB `failure_reason` enum field answers. |
| `pillar3:{op}` on root trace | Deferred — pillar3.* events (A3) make this observable already. |
| Per-artifact generator LLM call tags (decomposer/classifier/generator) | Auto-traced by LangChain callback contextvar propagation — no action needed, just don't inspect whether the auto-tagging labels are proposal-critical (they aren't). |
| Sandbox-execute spans marked with `sandbox:mcp_tool` tag | `sandbox.execute` events (Batch C3) are named distinctively already. |

---

## 10. One-line summary

**Before Step 13.7:** 8 tagging gaps across 3 primary surfaces (harness, longitudinal, composition).
**After Step 13.7:** all 8 fixed via pre-pipeline tag additions + per-capability-type chain-executor tagging. 4 deferred outcome-tag items recoverable via events (A1/A3/A5) + DuckDB join (B6). Full coverage for the Chapter 6 query recipes in §7.

**Test baseline:** 1514 passing, zero regressions.

---

---

## 11. Test-emission isolation (addendum, 2026-04-18)

### 11.1 The problem

Running `uv run pytest` against the unit suite (1514 tests) with `.env` loaded was landing thousands of events per invocation into the same LangFuse project Phase 4 eval will write to. Examples caught by the initial audit:

- `verification.fail_closed` events with fixture metadata (`"failed": ["bad"]`, `attempt_count: 3`) from `test_verification_node.py`
- `skill.repair_validation_failure` with `skill_name: "csv-loader-ad584a"` from `test_skill_generator.py`
- `sandbox.execute` events with fixture code (`"code_bytes": 4`, `"extra_packages": ["numpy"]`) from `test_sandbox_tool.py`

These were indistinguishable from real eval traces at the field level. A Chapter 6 filter for `verification.tier_3.reject` would catch both real runs and any test that happened to exercise that path.

### 11.2 Inventory — which tests emit

Grepped every test for LangFuse-touching code paths. 14 test files actively emit:

| Test file | Event types emitted | Patches emission? |
|---|---|---|
| `tests/unit/test_gate.py` | `verification.tier_{1,1_5,2,3}.*` + `sandbox.execute` | ❌ |
| `tests/unit/test_optimizer_operations.py` | `pillar3.{pruned,merged,split_variant,verification_rollback,split_rollback}` | ❌ |
| `tests/unit/test_coverage.py` | `verification.coverage.*` + `sandbox.execute` | ❌ |
| `tests/unit/test_adversarial.py` | `adversarial.{completed,timeout,no_structured_response,error}` | ❌ |
| `tests/unit/test_behavioral.py` | `sandbox.execute` | ❌ |
| `tests/unit/test_sandbox_tool.py` | `sandbox.execute` | ❌ |
| `tests/unit/test_import_check.py` | `sandbox.execute` (Tier 1.5) | ❌ |
| `tests/unit/test_cli_allow_network.py` | CLI root traces + `sandbox.execute` | ❌ |
| `tests/unit/test_classifier.py` | `classifier.{gap_classified,llm_failure,fail_closed}` | ❌ |
| `tests/unit/test_agent_generator.py` | GENERATION spans + `agent.*` | ❌ |
| `tests/unit/test_composition.py` | `chain-executor` spans | ❌ |
| `tests/unit/test_execution.py` | Composition step spans | ❌ |
| `tests/integration/test_sandbox_tool.py` | Real Docker `sandbox.execute` | ❌ |
| `tests/unit/test_decomposition.py` | `decomposition.*` | ✅ (patches `emit_langfuse_event`) |

### 11.3 Resolution — `LANGFUSE_TRACING_ENVIRONMENT=test`

Set in `tests/conftest.py` via `os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", "test")` **BEFORE** `load_dotenv()`. The LangFuse SDK picks up the environment env var at initialization and emits every observation with `"environment": "test"` as a top-level first-class field (verified via `mcp__langfuse__fetch_observations`).

**Why environment over tag or credential-clearing:**

| Approach | Test coverage of LangFuse code | Chapter 6 pollution | LangFuse UI support |
|---|---|---|---|
| Do nothing | Full | ❌ Contaminates Chapter 6 | Mixed with real data |
| Clear credentials | ❌ SDK disabled — zero integration test coverage | None (no emission) | N/A |
| Inject `test` tag into every emission | Full | Filterable via tag | Tags not primary filter axis |
| **`LANGFUSE_TRACING_ENVIRONMENT=test`** ✅ | **Full** | **None (filtered at env field)** | **Dedicated environment selector** |

The environment field is LangFuse's first-class separation mechanism. Tests continue to exercise every LangFuse code path (catches SDK regressions, verifies `emit_langfuse_event` / `create_event` / `update_current_trace` integration), while Phase 4 eval traces land in `environment="default"` and are queryable without any test noise.

### 11.4 Opt-out for offline test runs

Added `LANGFUSE_DISABLE_DURING_TESTS=true` env-var escape hatch — set this before invoking pytest to fall back to "clear credentials and disable SDK entirely." Useful for:
- CI environments without LangFuse reachable
- Local dev when LangFuse instance is down
- Reproducing the test pollution issue historically

### 11.5 Verification

After setting `LANGFUSE_TRACING_ENVIRONMENT=test` and running `uv run pytest tests/unit/test_sandbox_tool.py`:

- `mcp__langfuse__fetch_observations` returned events with `"environment": "test"` as the first-class field
- Zero events hit `environment="default"` from the test run
- Full suite still passes (1514/1514)

### 11.6 Chapter 6 query pattern

All Chapter 6 / 4 / 5 analysis queries should filter by `environment="default"` (or whatever the eval harness uses):

```
GET /api/public/traces?environment=default&tags=full
GET /api/public/observations?environment=default&type=EVENT&name=verification.tier_3.reject
```

This is cleaner than tag-based exclusion — one filter parameter, first-class semantic.

**Proposal alignment note:** Pillar 2 line 152 ("grounded in empirically observed contamination patterns") requires that Chapter 4.1's taxonomy cite trace IDs from REAL eval runs, not test fixtures. The environment separation makes this structurally enforceable — if a citation comes from `environment="test"`, it's by definition not empirical evidence.

---

**Last updated:** 2026-04-19, tag-propagation fix addendum (§12).

---

## 12. Tag-propagation fix (addendum, 2026-04-19)

### 12.1 The residual gap

Post-Batch A/B/C live verification surfaced an additional systematic issue: events emitted via `emit_langfuse_event(None, ...)` from contexts where `langgraph.config.get_config()` returned None landed in the LangFuse UI as **top-level traces with no tags**, even though the parent `pipeline.invoke` had full tag metadata. Screenshot evidence from the 4/18 longitudinal_shift probe (6 cycles) showed dozens of untagged `sandbox.execute` / `verification.tier_*.pass` / `cycle_flush.completed` / `adversarial.completed` rows alongside properly-tagged `task-decomposer` / `chain-executor` / `adversarial-prober` traces.

### 12.2 Root cause

`emit_langfuse_event` read tags via:

```python
parent_config = langgraph.config.get_config()
parent_meta = (parent_config or {}).get("metadata", {})
tags = parent_meta.get("langfuse_tags")
```

`get_config()` only returns populated values when the calling frame is inside an active LangGraph pipeline node. Four emission sites systematically escape that scope:

| Site | Why get_config() is empty |
|---|---|
| `tests/evaluation/cycle_flush.py::flush_task_cycle_state` | Called post-`pipeline.invoke()` — LangGraph context torn down |
| `scripts/run_longitudinal_shift.py` distribution_shift event | Emitted in runner loop, outside any pipeline |
| `src/verification/gate.py::_emit_tier_event` + `src/verification/adversarial.py` + `src/verification/coverage.py` | `verification_node` runs gate in a `ThreadPoolExecutor`; ContextVars don't propagate across threads |
| `src/verification/sandbox_tool.py::sandbox_execute` | Called as LangChain `@tool` inside `bind_tools` LLMs + DeepAgents; `langgraph.config` contextvar not set inside nested LangChain tool-call runtime |

Additionally, `sandbox_tool.py` used **direct `lf.create_event(...)`** (bypassing `emit_langfuse_event` entirely), so it wouldn't even have benefited from a fix to the shared helper.

### 12.3 Fix — `trace_context` ContextVar + module-global fallback

Shipped in `src/orchestrator/tracing.py`:

```python
_trace_context_var: ContextVar[dict | None] = ContextVar(...)
_trace_context_global: dict = {}
_trace_context_lock = threading.Lock()

@contextmanager
def trace_context(*, tags=None, session_id=None, user_id=None):
    ...
    # sets BOTH contextvar (same-thread) AND module-global (cross-thread)
```

`emit_langfuse_event` reads from both layers as a fallback when `get_config()` returns nothing. `sandbox_tool.py` switched from direct `lf.create_event` to `emit_langfuse_event(None, "sandbox.execute", ...)` so it picks up the fallback too.

**Why both layers:** ContextVars don't propagate to `ThreadPoolExecutor` workers by default (Python stdlib behavior — need to manually `copy_context().run()`). The module-global dict guarded by a lock gives worker threads visibility into the tag set. Inside the main thread (async / sync same-frame), the ContextVar path is preferred for correctness under potential parallel invocations.

### 12.4 Call-site wraps

Each `pipeline.invoke` call site now wraps in `trace_context` so fallback is populated:

| Site | File | Covers |
|---|---|---|
| Eval harness | `tests/evaluation/harness.py` (outer task loop) | pipeline.invoke + cycle_flush |
| Longitudinal | `scripts/run_longitudinal.py` | pipeline.invoke + cycle_flush |
| Longitudinal-shift | `scripts/run_longitudinal_shift.py` | pipeline.invoke + cycle_flush + distribution_shift emit |
| CLI `mint run` | `src/cli/runner.py::execute_task` | pipeline.invoke + pipeline.stream |
| CLI `mint plan` | `src/cli/runner.py::plan_task` | plan_pipeline.invoke |

### 12.5 Verification

Re-ran `scripts/run_longitudinal_shift.py --phase-a-tasks T04 --phase-b-tasks T11 --shift-at 2 --total 4 --seed 43`. LangFuse UI now shows **every** `sandbox.execute` / `verification.tier_*.pass` / `cycle_flush.completed` / `adversarial.completed` / `longitudinal.distribution_shift` row with the same tag set as the parent `task-decomposer` trace: `T04`, `condition:longitudinal_shift`, `cycle:N/4`, `format:csv`, `format:yaml`, `longitudinal_shift`, `phase:A`/`B`, `shift_at:2`, `static_coverage:false`, `tier:complex`.

**Test baseline:** 1514 passing, zero regressions.
