# LangFuse — Gap Analysis vs. Master Plan §13.7 + §11.15 (2026-04-18)

**Purpose:** cross-reference what we currently instrument (`langfuse_current_usage_2026_04_18.md`) against what LangFuse offers (`langfuse_capabilities_2026_04_18.md`) and what the sharpened master-plan §13.7 spec + §11.15 disclosure require. Produces a prioritized action list for Step 13.7 execution.

**Input references:**
- Current-state inventory: `docs/langfuse_current_usage_2026_04_18.md`
- Capability reference: `docs/langfuse_capabilities_2026_04_18.md`
- Master plan §13.7 (sharpened 2026-04-18): `docs/MASTER_PLAN_2026_04_13.md` lines 1631-1669
- Master plan §11.15: `docs/MASTER_PLAN_2026_04_13.md` lines 2237-2261
- Proposal line 152 ("empirically observed contamination patterns"): `docs/thesis_proposal.md`

---

## 1. Mapping each §13.7 CRITICAL item to current state

### CRITICAL-1 — Per-verification-tier events (Tier 1 / 1.5 / 2 / 3)

**Proposal alignment:** defends Pillar 2 line 152 novelty claim. Chapter 4.1's taxonomy must cite trace IDs per category.

**Current state:**
- `src/verification/gate.py` — **no LangFuse events.** Runs Tier 1 / 1.5 / 2 / 3 in sequence, builds `VerificationReport` with `findings` list; persisted to `state.verification_reports` (DuckDB-native, not LangFuse-native).
- `src/verification/static_scanner.py` (Tier 1) — **no LangFuse events.** Pure AST/regex, no LLM, no tracing.
- `src/verification/import_check.py` (Tier 1.5) — **no LangFuse events.** Runs Docker subprocess OR in-process `compile()`. No LLM at this tier (deterministic).
- `src/verification/behavioral.py` (Tier 2) — **no LangFuse events.** LLM-driven test-case generation; LLM calls auto-traced via LangChain callback inheritance, but no explicit tier-level span.
- `src/verification/adversarial.py` (Tier 3) — ✅ emits `adversarial.{completed,no_structured_response,timeout,error}` events. Timeout NOT at ERROR level (spec says it should be).

**Gap:** **CRITICAL-1 is 20% covered.** Only Tier 3 has tier-identified events, and those don't include `tier_reached`/`severity`/`findings` metadata structured for Chapter 4.1 citation.

**Fix:** instrument `gate.py::_verify_inner` to emit one `verification.tier_result` span per tier with:
```python
lf.update_current_span(
    name=f"tier-{tier_number}",
    output={
        "tier": tier_number,
        "passed": report.passed,
        "findings_count": len(report.findings),
        "severity_distribution": Counter(f.severity for f in report.findings),
        "categories": [f.category for f in report.findings],
    },
    metadata={"artifact_name": artifact.capability_name, "tier_reached": tier_number},
    level="DEFAULT" if report.passed else "WARNING",
)
```
Or `emit_langfuse_event(None, "verification.tier_{n}.{pass|reject}", ...)` — simpler, follows existing pattern.

**Effort:** ~30 min in `gate.py::_verify_inner`.

### CRITICAL-2 — GENERATION spans with token_cost on every LLM call

**Proposal alignment:** defends M4 (token cost per task).

**Current state:**
- ✅ **Auto-traced via LangChain callback inheritance** for every `init_chat_model(...)` call propagated through `config["callbacks"]=[CallbackHandler()]`.
- ⚠️ **Operations path breaks propagation:** `src/optimizer/operations.py::_langfuse_config()` creates a FRESH handler per call rather than inheriting — merge/split LLM traces appear as orphaned traces, not linked to the triggering task's session_id.
- ⚠️ **Adversarial sub-LLM calls:** the probe creates its own CallbackHandler and reads metadata via module-level bridge — traces inherit the original session but via a slightly fragile pattern.

**Gap:** **CRITICAL-2 is 85% covered.** The propagation gap in optimizer operations is the main fix.

**Fix:** in `_langfuse_config()` of `src/optimizer/operations.py`, read parent metadata via `get_config()` the same way decomposition does, and thread into the new handler's invoke config:
```python
def _langfuse_config() -> dict:
    try:
        from langgraph.config import get_config as _get_langgraph_config
        parent = _get_langgraph_config() or {}
        parent_meta = parent.get("metadata", {})
        lf_meta = {
            k: v for k, v in parent_meta.items()
            if k.startswith("langfuse_") or k in ("task_id", "condition", "run_id")
        }
        return {"callbacks": [LangfuseHandler()], "metadata": lf_meta}
    except Exception:
        return {"callbacks": [LangfuseHandler()]}
```

**Effort:** ~15 min.

### CRITICAL-3 — Per-Pillar-3-operation events

**Proposal alignment:** defends §Pillar 3 evolutionary operations + Chapter 5.3.

**Current state:**
- ✅ **DuckDB-native:** `metrics_store.record_event(capability_name, event_type, details)` writes to `cycle_events` table. Event types include `"prune"`, `"split_variant"`, `"merged"`, `"verification_rollback"`, `"distribution_shift"`.
- ❌ **No parallel LangFuse events.** `operations.py` emits `emit_langfuse_event(None, ...)` calls but (per grep) those are per-LLM-call events (at lines 349, 378, 399) — not per-operation-result events.

**Gap:** **CRITICAL-3 is DuckDB-only.** Master plan §13.7 says events should land in BOTH stores so LangFuse-UI navigation works.

**Fix:** in `operations.py::verify_and_register_or_rollback` and each `_merge_*` / `_split_*` helper, after the DuckDB record_event call, mirror to LangFuse:
```python
metrics_store.record_event(name, "merged", details)
emit_langfuse_event(
    None,
    f"pillar3.{event_type}",
    "DEFAULT",
    {"capability_name": name, "event_type": event_type, "details": details},
)
```

**Effort:** ~20 min (3-5 sites to touch).

### CRITICAL-4 — Per-cycle KL-divergence value as LangFuse score

**Proposal alignment:** defends M6 (registry convergence) + Chapter 5.4.

**Current state:**
- ✅ **DuckDB-native:** `cycle_utility_scores` table persists per-cycle utility snapshots; `analyze_longitudinal.py` computes KL post-hoc.
- ❌ **No LangFuse side:** `src/optimizer/loop.py::_compute_kl_divergence` is pure Python; never calls into LangFuse.

**Gap:** **CRITICAL-4 not covered in LangFuse.**

**Fix:** at the end of each optimizer cycle in `src/optimizer/loop.py`, after computing KL:
```python
from langfuse import get_client
try:
    lf = get_client()
    lf.score(
        name="m6.kl_divergence",
        value=kl_value,
        data_type="NUMERIC",
        comment=f"Optimizer cycle {cycle_idx}",
        trace_id=state.trace_id,   # or None → orphaned score but still aggregable
    )
except Exception: pass
```

**Effort:** ~15 min.

---

## 2. Mapping §13.7 IMPORTANT items

### IMPORTANT-5 — Handoff schema-violation events (§12 finding #12 signal)

**Current state:**
- ✅ DB-native via Step 13.8 `failed_step_reason` column (committed today).
- ⚠️ Composition.py emits `_end_langfuse_step_span(..., level="WARNING")` when a step fails — so there IS a LangFuse span with failure context, but it does NOT carry the structured `failed_step_reason` string.

**Gap:** partial — the LangFuse span records failure but without the reason string.

**Fix:** in `composition.py::_end_langfuse_step_span`, extend output dict with `failed_step_reason` when step failed.

**Effort:** ~10 min.

### IMPORTANT-6 — Per-pipeline root trace tagged with run_id / condition / task_id

**Current state:**
- ✅ For `run_pilot.py` / harness — tags propagated via `_langfuse_config()` in the harness per CLAUDE.md + Step 14.12.
- ✅ For `run_longitudinal.py` / `run_longitudinal_shift.py` — explicit session_id + tags.
- ✅ For `mint` CLI — session_id + tags via `_langfuse_config()`.
- ⚠️ **But** the CompositionalExecutor overrides `user_id="mint-cli"` even when called from the eval harness (`composition.py:892`). Should inherit the harness's user_id or accept a kwarg.

**Gap:** a hardcode issue in composition.py:892 overriding user_id.

**Fix:** plumb `user_id` through CompositionalExecutor constructor kwarg (or read from handler parent metadata).

**Effort:** ~10 min.

### IMPORTANT-7 — Per-retry / sticky-flag ERROR events

**Current state:**
- ✅ DB-native via `failure_reason` enum values (`agent_execution_timeout`, `agent_execution_error`, `chain_step_failed`).
- ✅ LangFuse events emitted with level=ERROR (decomposition.timeout, verification.fail_closed).
- ⚠️ Classifier's `classifier.fail_closed` event is NOT at ERROR level (default level string).

**Gap:** classifier event-level consistency.

**Fix:** bump `classifier.fail_closed` to ERROR level; likewise `adversarial.timeout` per the Step 14.11 fail-closed semantics.

**Effort:** ~5 min.

---

## 3. Mapping §13.7 NICE-TO-HAVE items

### NTH-8 — loading_node / register_tool tracing

**Current state:** zero LangFuse.

**Fix (if we do it):** wrap `register_tool` + `loading_node` in `@observe` decorators with `as_type="span"` — gives span visibility without manual plumbing.

**Effort:** ~15 min. **Defer to post-eval.**

### NTH-9 — LangFuse↔DuckDB cross-reference tags on cycle_events flush

**Current state:** not wired.

**Fix:** in `tests/evaluation/cycle_flush.py::flush_task_cycle_state`, call `lf.update_current_trace(tags=[..., f"cycle_events:flushed-{N}"])` after the DB flush.

**Effort:** ~5 min. **Defer to post-eval.**

### NTH-10 — Per-generated-capability-runtime child spans

**Current state:** ✅ for AGENT + SKILL (LangChain auto-traces LLM calls); ❌ for MCP_TOOL (sandbox_execute is a Docker subprocess, no LLM → no auto-trace).

**Gap:** MCP_TOOL runtime invocations not visible in LangFuse at all. `analyze_context_utilization.py` relies on LLM input-token spans, so this gap is already absorbed: MCP_TOOL has no input-token utilization to measure.

**Fix:** emit a LangFuse SPAN wrapping `sandbox_execute` with success/failure + latency — would enable per-backend success rate in the LangFuse UI.

**Effort:** ~15 min. **Defer to post-eval** — Chapter 6 metrics don't need this.

---

## 4. Known-or-suspected gaps the §13.7 spec flagged specifically

From master plan §13.7:

| Spec-flagged gap | Severity (per §13.7) | Actual state | Fix size |
|---|---|---|---|
| `verify_coverage` LangFuse event | 🟠 Important | ❌ Not emitted (only `cycle_events` row via `record_event`) | ~10 min |
| `_compute_kl_divergence` as LangFuse score | 🔴 Critical | ❌ Not emitted | ~15 min (same as CRITICAL-4) |
| `flush_task_cycle_state` LangFuse-↔-DuckDB tag | 🟡 Nice-to-have | ❌ Not emitted | ~5 min |
| `run_longitudinal_shift.py` `distribution_shift` event in BOTH stores | 🔴 Critical | ⚠️ Only DuckDB `cycle_events` row (`record_event(distribution_shift_marker, "distribution_shift", ...)`) — no LangFuse event | ~10 min |
| Tier 1.5 events (brand new from Step 12.5) | 🔴 Critical | ❌ Zero instrumentation (deterministic, no LLM, no callback inheritance) | ~15 min (subsumed by CRITICAL-1 fix) |
| Adversarial timeout detail | 🟠 Important | ✅ emits `adversarial.timeout` but not at ERROR level; no per-sub-probe count | ~10 min |
| `register_tool` tracing | 🟡 Nice-to-have | ❌ Not emitted | ~15 min |

---

## 5. Features LangFuse offers that we should adopt for the eval

Beyond the §13.7 checklist, the capability reference surfaced these high-leverage patterns worth adopting:

### 5.1 Push M1-M6 as LangFuse scores (HIGH leverage)

**Current:** all metrics computed post-hoc from DuckDB; surfaced only via `analyze_results.py` stdout tables.

**Opportunity:** push each metric as a score attached to the relevant trace (for per-record metrics) or session (for aggregate):
```python
# Per-record after each task completes:
lf.score(name="m1.success", value=float(gate_passed and output_in_range),
         data_type="BOOLEAN", trace_id=state.trace_id)
lf.score(name="m5.latency_ms", value=elapsed_ms, data_type="NUMERIC",
         trace_id=state.trace_id)

# Post-eval after M2 audit:
lf.score(name="m2.contamination", value="contaminated",
         data_type="CATEGORICAL", trace_id=...)
```

**Benefit:** LangFuse dashboards become Chapter-6-ready without additional tooling. Trace-level views show success + cost + latency together. Score filters let us build "all traces where m5 > 5000ms" ad-hoc.

**Effort:** ~45 min if done thoroughly.

**Recommendation:** add 4-5 lines to the harness where each metric is computed. Easy win. **Defer to Step 13.7 scope consideration.**

### 5.2 `propagate_attributes()` context manager instead of module-level bridge (MEDIUM leverage)

**Current:** `src/verification/adversarial.py::_langfuse_metadata` is a module-level global set by `set_langfuse_metadata()`. Works but fragile.

**Opportunity:** replace with LangFuse's native `propagate_attributes()` context manager from `langfuse` package — automatically inherits across thread boundaries via OpenTelemetry context propagation.

**Benefit:** removes the module-level state hack; more robust against concurrent runs.

**Effort:** ~30 min + tests.

**Recommendation:** nice-to-have. Defer.

### 5.3 Typed observations (AGENT / TOOL) for richer UI differentiation (LOW leverage)

**Current:** every span is a plain SPAN type.

**Opportunity:** mark adversarial DeepAgent spans as `as_type="agent"`, `sandbox_execute` wrappers as `as_type="tool"`, etc. — LangFuse UI differentiates visually.

**Benefit:** nicer UI. No data difference.

**Recommendation:** defer. Cosmetic.

### 5.4 Score configs (validation schemas for scores) (MEDIUM leverage)

**Current:** if we push scores, no validation enforced.

**Opportunity:** define ScoreConfig in LangFuse UI for each metric (e.g., `m2.contamination` categorical with values in {correct, contaminated, partial}); subsequent score submissions are validated.

**Benefit:** catches typos (e.g., `"contaminate"` → validator rejects).

**Recommendation:** useful if adopting §5.1. Skip otherwise.

---

## 6. Consolidated fix list for Step 13.7 execution

Grouped by severity + cost:

### Batch A — CRITICAL (must land pre-Step-18)

| # | Fix | File | Effort | Proposal alignment |
|---|---|---|---|---|
| A1 | Emit per-tier verification events in `gate.py::_verify_inner` | `src/verification/gate.py` | 30 min | Defends Pillar 2 line 152 |
| A2 | Fix operations.py propagation — parent metadata inherit | `src/optimizer/operations.py::_langfuse_config()` | 15 min | M4 |
| A3 | Mirror Pillar 3 operation events into LangFuse | `src/optimizer/operations.py` (3-5 sites) | 20 min | Chapter 5.3 |
| A4 | Emit per-cycle KL divergence as LangFuse score | `src/optimizer/loop.py` | 15 min | M6 |
| A5 | Emit `distribution_shift` in LangFuse too | `scripts/run_longitudinal_shift.py` or the underlying recorder | 10 min | Chapter 6 RQ #3 |
| A6 | Tier 1.5 events (subsumed by A1 if tier-event pattern catches it) | included in A1 | (0) | Step 12.5 observability |

**Total Batch A:** ~90 min code + ~15 min probe + ~15 min diff = **~2 h** — matches the original §13.7 2-3 h estimate.

### Batch B — IMPORTANT (land if time; otherwise disclose)

| # | Fix | File | Effort |
|---|---|---|---|
| B1 | Add `failed_step_reason` to step-span output | `composition.py::_end_langfuse_step_span` | 10 min |
| B2 | Plumb `user_id` kwarg through CompositionalExecutor | `composition.py` constructor | 10 min |
| B3 | Upgrade classifier/adversarial fail-closed events to ERROR level | `classifier.py` + `adversarial.py` | 5 min |
| B4 | Add `verify_coverage` LangFuse event | `src/verification/coverage.py` | 10 min |
| B5 | Push M1-M6 as LangFuse scores from harness | `tests/evaluation/harness.py` | 45 min |
| B6 | Persist LangFuse `trace_id` on `EvaluationRecord` | schema + DB column + harness | 20 min |
| B7 | Upgrade `adversarial.timeout` event metadata (sub-probe count) | `adversarial.py` | 10 min |

**Total Batch B:** ~110 min — tack onto Batch A if scope permits, or disclose as Chapter 6 methodology caveats.

### Batch C — NICE-TO-HAVE (post-eval)

| # | Fix |
|---|---|
| C1 | `register_tool` / `loading_node` tracing |
| C2 | `flush_task_cycle_state` LangFuse-↔-DuckDB tag |
| C3 | `sandbox_execute` wrapper spans for MCP_TOOL runtime |
| C4 | `propagate_attributes()` migration from module-level bridge |
| C5 | Typed observations (AGENT / TOOL) for UI differentiation |
| C6 | LangFuse prompt management migration |
| C7 | LangFuse datasets for testbed tasks |

**Defer all.** No Chapter 6 claim depends on any of these.

---

## 7. Propagation-gap specific remediation

The single most important issue discovered in the audit is **the optimizer path propagation break** (A2 above). Every merge/split operation today creates a LangFuse trace that is NOT linked to the triggering task. This means:

- Chapter 5.3 can't say *"merge operation X was triggered by task Y"* directly from LangFuse — must join through DuckDB.
- Token costs accumulated during merge/split appear under orphaned traces, not the originating task's session.

**Fix is one file, ~15 min.** High impact / low cost. Should be prioritized at the top of Batch A.

---

## 8. Answer to master plan §13.7 "Known-or-suspected gaps" list

Matching the spec's bullet list against actual code:

| Spec's gap | Verdict after audit |
|---|---|
| `verify_coverage` LangFuse event | **Confirmed missing.** DuckDB-only via `record_event(..., "verification_rollback", ...)` |
| `_compute_kl_divergence` as LangFuse score | **Confirmed missing.** Batch A4. |
| `flush_task_cycle_state` cross-reference tag | **Confirmed missing.** Batch C2. |
| `run_longitudinal_shift.py` `distribution_shift` | **Confirmed missing from LangFuse.** Batch A5. |
| Tier 1.5 events | **Confirmed missing.** Subsumed by Batch A1. |
| Adversarial timeout detail | **Partial.** Event exists but under-specified. Batch B7. |
| `register_tool` tracing | **Confirmed missing.** Batch C1. |

**All seven spec-flagged gaps confirmed.** No surprises from the §13.7 author's expectations.

---

## 9. Per-pillar audit doc updates (Step 13.7 output routing)

Per master plan §13.7 deliverables + §11.15, Step 13.7's findings should update:

### 9.1 `docs/verification/03_pillar_2_verification.md`

Add rows under "Findings / Drift" section:

| # | Finding | Severity | Status |
|---|---|---|---|
| (new) | Per-tier verification events not emitted to LangFuse prior to Step 13.7 | Critical (novelty-claim defensibility) | Batch A1 — to be fixed |
| (new) | Tier 3 adversarial timeout event at default level (should be ERROR per Step 14.11 fail-closed semantics) | Important | Batch B3 — to be fixed or disclosed |

### 9.2 `docs/verification/04_pillar_3_granularity_optimizer.md`

Add rows:

| # | Finding | Severity | Status |
|---|---|---|---|
| (new) | Pillar 3 operation events DuckDB-only, not mirrored to LangFuse | Critical (Chapter 5.3 navigation) | Batch A3 — to be fixed |
| (new) | Per-cycle KL value not emitted as LangFuse score | Critical (Chapter 5.4 dashboard) | Batch A4 — to be fixed |
| (new) | Optimizer operations path doesn't propagate parent session_id/tags — traces orphaned | Critical (M4 accuracy + Chapter 5.3 cross-ref) | Batch A2 — to be fixed |
| (new) | `verify_coverage` LangFuse event missing | Important | Batch B4 |

### 9.3 `docs/verification/05_evaluation_strategy.md`

Add rows:

| # | Finding | Severity | Status |
|---|---|---|---|
| (new) | M1-M6 not pushed as LangFuse scores — Chapter 6 must build own dashboards | Important (cosmetic, not correctness) | Batch B5 — consider |
| (new) | `EvaluationRecord.trace_id` field absent — no DuckDB↔LangFuse cross-reference | Important (cross-nav) | Batch B6 |
| (new) | `distribution_shift` event not in LangFuse | Critical (Chapter 6 RQ #3) | Batch A5 |
| (new) | Metrics API (`GET /api/public/metrics`) unused — `backfill_token_costs.py` could be simpler | Nice-to-have | Post-eval |

---

## 10. §11.15 alignment check

Master plan §11.15 claims:

> *"a dual-layer observability instrumentation: (a) LangFuse traces... (b) DuckDB tables..."*

**Is this claim true today?** PARTIALLY. The dual-layer design exists for some surfaces (decomposition events, verification fail-closed) but not others (Pillar 3 operations, KL, distribution_shift, tier results). **Post-Batch-A, the dual-layer claim becomes fully true.** Before Batch A, §11.15's claim over-states current state slightly.

**Recommendation:** §11.15's Status line should read *"Step 13.7 pre-eval audit pending; Batch A fixes will bring dual-layer instrumentation to the state §11.15 describes"* — OR update §11.15 to say *"partial dual-layer, with CRITICAL gaps scheduled via Step 13.7 Batch A."*

Second option is more honest. See consolidated recommendation below.

---

## 11. Recommended execution plan

**When 13.7 runs:**

1. **Commit Batch A (CRITICAL)** — 2 h code + test. **Blocks Step 18.**
2. **Decide Batch B (IMPORTANT)** — bundle with Batch A if time; otherwise pick B1-B3 (trivial) and defer the rest to disclosure.
3. **Skip Batch C (NICE-TO-HAVE)** — post-eval.
4. **Run minimal probe** per §13.7 spec — 6 traces + 3 longitudinal — verify zero CRITICAL gaps.
5. **Write `docs/langfuse_audit_2026_04_17.md`** (the standalone snapshot per §13.7 deliverables).
6. **Update `docs/verification/03_*`, `04_*`, `05_*`** per §9 above.
7. **Tighten §11.15 Status line** to reflect actual state after Batch A lands.

**Estimated 13.7 total:** **2-3 h** for Batch A only; **3-4 h** if Batch B1-B3 bundled; **4-5 h** if full Batch A+B.

Fits within §13.7's original 2-3 h estimate IF we scope to Batch A only. Batch B1-B3 adds 20 min and is the sensible minimum bundle.

---

**Bottom-line answers to "what should we add":**

✅ **Add (Batch A):** per-tier events, operations path propagation, Pillar 3 operation mirroring, KL score emission, `distribution_shift` LangFuse event.
🟡 **Consider (Batch B):** step-span reason string, user_id plumbing, ERROR level consistency, `verify_coverage` event, M1-M6 as scores, `trace_id` on EvaluationRecord.
❌ **Defer (Batch C):** everything else — no Chapter 6 claim depends on it.

---

**Last updated:** 2026-04-18. Prepared as input for Step 13.7 execution.
