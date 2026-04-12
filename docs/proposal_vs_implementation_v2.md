# Proposal vs. Implementation — Fresh Audit (v2)

**Date:** 2026-04-11 (post-Phase 3.5c)
**Method:** Fresh codebase audit without referencing the original `proposal_vs_implementation.md`.
**Purpose:** Catch any gaps the original audit missed, verify all previously-flagged items are resolved.

Legend:
- ✅ **Faithful** — implementation matches the proposal
- ⭐ **Strengthened** — goes beyond the proposal in a way that improves thesis claims
- 🟢 **Kept deviation** — deliberate divergence with justification
- 🟡 **Refined** — additive/defensive engineering choice, no thesis impact
- 🔴 **Drift** — deviation needing explicit decision (none remaining)
- ❌ **Missing** — proposed item not implemented

---

## Pillar 1 — JIT Capability Pipeline

| Stage | Proposal | Implementation | Status |
|---|---|---|---|
| **1. Decomposition** | Deep-thinking model with large token budget | `create_deep_agent()` with `gpt-5-mini` (small fast model + DeepAgents harness for `write_todos`, file context, summarization). 120s timeout, recursion_limit=50. | 🟢 Deliberate substitution — harness-on-small-model strategy. Same outcome, different cost/latency profile. |
| **2. Gap detection** | Semantic registry with hierarchical embeddings, confidence threshold | ChromaDB with `"{domain} > {type} > {name}: {summary}"` document format. Cosine similarity, threshold=0.6. Per-capability search. 50-word embedding summary. | ✅ Faithful. Hierarchy via text prefix, not nested collections — proposal silent on implementation. |
| **3. Classification** | Per-gap classification into MCP_TOOL / SKILL / AGENT | Per-gap classification via `classify_gap()` at temperature=0. Packaging-signal checklist. Agent-task collapse rule. | ✅ Faithful. Per-gap restored in Phase 2. |
| **4. Generation** | Code-gen agent produces artifact per type | Three sequential generators (MCP_TOOL, SKILL, AGENT). Structured output + few-shot + repair pass. Lead-with-subagents mode for ≥2 AGENT gaps. Parallel generation (4 workers each). | ✅ Faithful + extended (subagents not in proposal). |
| **5. Sandboxed integration** | Sandboxed MCP server, skill folder, contained agent session | importlib dynamic load + Docker execution (no persistent MCP servers). Skills to `data/*/skills/`. Agents via DeepAgent invocation. | 🟢 Kept deviation — avoids subprocess lifecycle complexity. |
| **State communication** | Strictly typed JSON schemas | 18 Pydantic models in `src/schemas/` with `extra="forbid"`. All inter-node state typed. `Command[T]` routing. | ✅ Faithful. |

**Extensions not in proposal:**
- Compositional executor (`composition.py`) — runs N artifacts in dependency order with workspace state passing
- Lead-with-subagents for AGENT chains — context isolation via DeepAgents `task()` tool
- Resolution map — links subtask required_capabilities to registry matches or generated artifacts
- LangFuse chain-executor traces with per-step spans

---

## Pillar 2 — Verification Layer

### Tier 1 — Static Analysis

| Pattern | Proposal | Implementation | Status |
|---|---|---|---|
| Hardcoded data (Critical) | String literals, embedded JSON, inline arrays | AST: `return` of list ≥3 constants or dict ≥3 pairs. One level of variable indirection. | ✅ |
| Silent exception handling (High) | Bare except, except Exception return default | AST: bare/broad excepts that pass/return default. Permits re-raise/logging. | ✅ |
| Mock artifacts (Critical) | unittest.mock imports, @patch, MagicMock | AST: `import unittest.mock`, `import mock`, `import faker`. Regex: "mock", "fake", "dummy", "placeholder", "DEMO", "TEST_DATA". | ✅ Covers imports; `@patch`/`MagicMock` caught transitively via import check. |
| Synthetic data (Medium) | random.seed(fixed), faker, lorem ipsum | AST: `random.seed(<literal>)`, `np.random.seed(...)`. Regex: lorem ipsum. | ⭐ Strengthened — severity escalated to HIGH (stricter than proposal). LLM generators use these patterns more often than anticipated. |
| Missing external calls (High) | Functions declaring API params without network requests | AST: fetch/query/call/get-named functions without network imports. Dynamic severity: MEDIUM when `--network none` (default), HIGH when `--allow-network`. | ✅ Faithful — severity now matches proposal intent contextually. MEDIUM offline (informational), HIGH online (per proposal). |

**Type scoping:**
- MCP_TOOL: full AST + regex scan ✅
- SKILL: code-fence extraction (`_extract_python_fences`) → full AST on each fence. Prose excluded. ✅
- AGENT: code-fence extraction from `system_prompt` + `skill_content`. ✅
- Tier 0 (`quick_validate.py`): lorem ipsum, `TODO: implement`, `# placeholder return` hard-fails. ✅

**Severity mismatches vs proposal:**
- Synthetic data indicators: implementation HIGH, proposal says Medium — ⭐ **Strengthened**. LLM generators reach for `random.seed` and `faker` more frequently than the proposal anticipated. Escalating to HIGH ensures these patterns require Tier 2 behavioral clearance.
- Missing external calls: now **dynamic** — MEDIUM when network disabled (default), HIGH when `--allow-network` is enabled. ✅ **Resolved**. Matches proposal intent: severity is HIGH when tools can actually reach the network (per proposal), MEDIUM when they can't (informational only).

### Tier 2 — Behavioral Testing

| Test | Proposal | Implementation | Status |
|---|---|---|---|
| Nonexistent entity | Request data for entities that cannot exist | Empty/invalid inputs, flag silent success | ✅ |
| Consistency | Same input twice → same output | Identical-input replay, HIGH on mismatch | ✅ |
| Boundary | Extreme edges, empty strings, max-length | Zero/empty, negative, large inputs | ✅ |
| Latency analysis | Fast AND uniform = mock smell | Mean < 1ms AND cv < 0.10 (both required) | ✅ |

**Extras (not in proposal, added from bypass exercise):**
- Parameterless smoke test (B2.1 fix) ⭐
- Output variation test — different inputs → different outputs (B2.3 fix) ⭐
- Upfront dependency check — HIGH if pip install fails ⭐
- Timeout check (renamed from old `_test_latency`) — flags > 5s execution ⭐

### Tier 3 — Adversarial Probing

| Attack | Proposal | Implementation | Status |
|---|---|---|---|
| Hidden demo mode | Inputs with "sample"/"test" tokens | DeepAgent tests with demo vs neutral inputs | ✅ |
| Network disconnect | Behavior without network | Sandbox runs `--network none`; flags tools that claim API success | ✅ |
| Ground truth comparison | Compare against known correct output | Reference implementation comparison for deterministic tools | ✅ |

**Gate cost ladder:** Tier 0 → Tier 1 → Tier 2 → Tier 3. Critical blocks immediately. ✅
**Tier 2 clearance:** Tier 1 HIGH findings downgraded to MEDIUM if Tier 2 passes. ⭐ Enhancement not in proposal.
**Never-raises contract:** `verify()`, `sandbox_execute()`, `behavioral.run()`, `adversarial.probe()` all return results, never raise. ✅

---

## Pillar 3 — Granularity Optimizer

| Item | Proposal | Implementation | Status |
|---|---|---|---|
| Four metrics | reuse, success, token_cost, maintenance | All four in `MetricsStore.compute_granularity_metrics()` | ✅ |
| Utility formula | U(c) = α·reuse·success − β·token_cost − γ·maintenance | Exact match. α=1.0, β=0.1, γ=0.1 defaults. | ✅ |
| Split | >60% reuse, <70% success, SKILL+AGENT | Exact thresholds. MCP_TOOL raises ValueError. | ✅ |
| Merge | <5% reuse + similarity, SKILL only | Exact threshold. MCP_TOOL/AGENT raise ValueError. | ✅ |
| Prune | Zero invocations, all types | `deactivate()` (soft-delete). Respects pinned capabilities. | ✅ |
| KL convergence | KL divergence between consecutive snapshots | Point-mass version (primary, in optimizer loop) + histogram version (sensitivity, in simulation). | ✅ Computed. ❌ Not surfaced in `analyze_results.py`. |

---

## Evaluation Strategy

### Testbed

| Proposal | Implementation | Status |
|---|---|---|
| Business Analytics Pipeline | 20 tasks (T01-T20), 7 file formats, 34 fixtures, 3 tiers. 5 seeded (2 MCP_TOOL, 2 SKILL, 1 AGENT), 5 JIT MCP_TOOL, 5 JIT SKILL, 5 JIT AGENT. | ✅ |
| ML model training, inference | No actual ML. Statistical analysis, anomaly detection, root-cause inference. | 🟢 Kept — proposal language was loose. Reword Ch 6. |
| Diverse capability types | ~8 MCP_TOOL, ~3 SKILL (weak), ~5 AGENT | 🟢 Kept — all three types present. See coverage analysis below. |

**Capability type coverage analysis (2026-04-11):**

*SKILL coverage (weak):* No task is pure-SKILL. SKILL artifacts appear as part of mixed chains (e.g., T04 produces 2 MCP_TOOL + 1 SKILL). The decomposer strips packaging signals at the step level, making individual steps look like MCP_TOOL. Tasks T04, T05, T12 are SKILL-dominant but produce mixed outputs. Document as Ch 6 limitation.

*AGENT coverage assessment (item #11 from v1):*

| Task | Genuinely needs AGENT? | Rationale |
|---|---|---|
| T16 | ✅ Yes | Hypothesis formation + testing, causal reasoning about cascade order. Cannot be linearized. |
| T17 | ✅ Yes | Cross-referencing 3 data sources with conditional branching based on alert details. |
| T08 | 🟡 Borderline | Steps are linear (profile → categorize → rank). Prompt says "adaptive" but a SKILL with 5 sandbox calls could handle the mechanical version. |
| T13 | 🟡 Borderline | Linear (profile → compare to baselines → list anomalies). Prompt says "adapt based on findings" but the adaptation is thin. |
| T10 | 🟢 Could be SKILL | Deterministic pipeline (load → trend → seasonality → forecast). "Choose method based on data" is a one-shot decision, not iterative. |

**Verdict:** 2/5 genuine AGENT tasks, 2/5 borderline, 1/5 could be SKILL. The classifier may correctly route T10 as MCP_TOOL, which is the system working as designed. Document the 2+2+1 split in Ch 6 as a testbed observation, not a limitation — the eval measures what the classifier actually does, not what we expect.

### Baselines

| Baseline | Implementation | Status |
|---|---|---|
| Static skills | `static` condition — registry lookup only | ✅ |
| JIT without verification | `no_verification` — `gate_fn=None` | ✅ |
| JIT without optimization | `no_optimization` — `run_optimizer=False` | ✅ |
| Full system | `full` — all three pillars | ✅ |

### Metrics

| Metric | Status | Notes |
|---|---|---|
| Task success rate | ✅ Implemented | `gate_passed AND output_in_range` in EvaluationRecord |
| Mock contamination rate | ❌ Missing aggregation | `human_label` field exists in schema. Audit CLI + labelling needed post-eval. |
| Capability reuse (Gini) | ❌ Missing aggregation | Invocation data in DuckDB. `_compute_gini()` needed in `analyze_results.py`. |
| Token cost per task | ✅ Implemented | Via LangFuse backfill to DuckDB. |
| Time to first correct execution | ✅ Implemented | `attempt_latency_ms` in EvaluationRecord. |
| Registry convergence (KL) | ❌ Missing aggregation | KL computed in optimizer loop. JSON loader + plot needed in `analyze_results.py`. |

**3 of 6 metrics missing aggregation** — all decided as post-eval work (~6h total).

---

## Tech Stack

| Proposal | Implementation | Status |
|---|---|---|
| LangGraph / Google ADK | LangGraph only | ✅ (proposal listed as alternatives) |
| Claude Code or equivalent | `gpt-5-mini` via `init_chat_model()` | ✅ (proposal allowed "or equivalent") |
| MCP SDK Python/TypeScript | Python `mcp>=1.26.0` only | ✅ (picked one of two) |
| A2A protocol / LangGraph messaging | LangGraph messaging only | ✅ (picked one of two) |
| TOML config | Python constants + env vars in `src/config.py` | 🟢 Kept — functionality equivalent at testbed scale. |
| Agent Skills spec | DeepAgents-flavoured SKILL.md | ✅ |
| Docker sandbox | Docker with `--network none`, two-phase install, `uv pip` | ✅ |
| ChromaDB registry | ChromaDB persistent client, cosine similarity | ✅ |
| — (not in proposal) | DeepAgents harness | ⭐ Strengthened |
| — (not in proposal) | DuckDB metrics store | ⭐ Strengthened — load-bearing for Pillar 3 |
| — (not in proposal) | LangFuse observability | ⭐ Strengthened — delivers token-cost metric |
| — (not in proposal) | Three execution backends | ⭐ Strengthened — makes classifier routing measurable |

---

## Cross-Cutting: Things the Proposal Did Not Anticipate

These came out of building and testing the system. They are not in the proposal but matter for the thesis.

| Item | Description | Verdict |
|---|---|---|
| **Compositional Executor** | `CompositionalExecutor` in `composition.py` runs all N artifacts in dependency order via workspace at `/tmp/mint_workspace_<id>/`. MCP_TOOL via Docker mount, SKILL/AGENT via `fixture_contents` injection. Makes per-gap generation actually usable — without it, only one artifact could execute per task. | ⭐ Strengthened — proposal's Stage 5 ("connected for immediate use") is now multi-artifact, not single-artifact. |
| **Lead-with-Subagents** | When ≥2 AGENT gaps exist, generates ONE lead + N subagent dicts. Lead delegates via `task()` tool. Each subagent runs in isolated context, returns only final result. | ⭐ Strengthened — directly addresses proposal's third tension (context economy) at the generated-capability level. |
| **Anti-Monolith Prompts** | Generator prompts include explicit instructions to produce step-specific artifacts, not monoliths that solve the entire task. SKILL execution prompt tells LLM to "produce ONLY the output described in the skill instructions." | 🟡 Refined — engineering response to observed LLM behavior (generators compute full answer in step 1, making decomposition pointless). |
| **Registry Threshold 0.6** | Proposal says "confidence threshold" without specifying a value. Implementation uses 0.6 (raised from 0.5 after false-match analysis causing T09/T14 failures). Based on best practices research (0.6-0.7 recommended for balanced precision/recall). | 🟡 Refined — proposal silent on value; we chose based on empirical data. |
| **Retry Loop with Feedback** | Proposal silent on retry mechanism. Implementation: MAX_ATTEMPTS=3, verification feedback injected into next generation prompt. Failed artifacts are regenerated with error context. | 🟢 Kept deviation — materially affects success rate. Without retry, single Tier 1 hit terminates generation. |
| **Three Execution Backends** | Proposal treats execution as undifferentiated. Implementation: MCP_TOOL → Docker no-LLM (0 calls), SKILL → constrained LLM (max 5 calls), AGENT → full DeepAgent (unbounded). Makes classifier routing decision have measurable runtime consequences. | ⭐ Strengthened — converts classifier from metadata tag to load-bearing routing decision. |
| **Network-Disabled Sandbox** | Proposal implies tools can make network requests (Tier 1 "missing external calls" check, Tier 3 "network disconnect" test). Implementation runs `--network none` by default; `--allow-network` flag enables `--network bridge` for opt-in network access. | ✅ Faithful — default is secure (`--network none`); `--allow-network` enables network when needed (opt-in via CLI `mint run --allow-network`). Tier 1 severity is dynamic (MEDIUM offline, HIGH online). |
| **`FailureReason.CHAIN_STEP_FAILED`** | Post-Phase-3 addition to evaluation schema for compositional chain failures. Original `FailureReason` enum didn't anticipate multi-step chain execution. | 🟡 Refined — schema evolution to support compositional execution. |
| **Context Engineering Mapping** | `docs/context_engineering_mapping.md` maps Write/Select/Compress/Isolate primitives to all three artifact types across orchestration and execution layers. Not in proposal but directly addresses the thesis domain ("Context Engineering in LLM-Powered Multi-Agent Systems"). | ⭐ Strengthened — makes the proposal's third tension (context economy) concrete and measurable. |
| **DuckDB Metrics Store** | Proposal silent on metrics storage. Implementation uses DuckDB for invocation records, evaluation records, and longitudinal optimizer state. Load-bearing for Pillar 3. | ⭐ Strengthened — without it, no prune decisions, no convergence curves, no Chapter 6 analysis. |
| **LangFuse Observability** | Proposal silent on tracing. Implementation traces every LLM call. `backfill_token_costs.py` reads traces post-hoc → delivers the "token cost per task" metric directly. | ⭐ Strengthened — delivers one of six proposal metrics. |
| **Chain Data Flow Contract** | Proposal says "strictly typed JSON schemas representing the world state at each handoff point." Implementation enforces: first step reads raw files → JSON, middle steps JSON → JSON, last step JSON → requested format (default JSON). MCP_TOOL generator prompt describes this explicitly. | ✅ Faithful — directly implements proposal §3.4. |
| **Binary File Structural Fix** | Proposal silent on binary file handling. Implementation: step 0 gets all fixtures (broad matching), steps 1+ use name-aware matching only (fixture filename must overlap with parameter name). Prevents `.xlsx` being blindly assigned to unrelated params in downstream steps. | 🟡 Refined — engineering fix for observed failure mode (T11 `.xlsx` crash). |
| **Chain Step Diagnostic Logging** | Not in proposal. Sandbox wrapper logs `_all_inputs` keys, `_fixture_paths`, `_kwargs`, and result keys to stderr for each chain step. Enables debugging data flow issues without accessing ephemeral Docker containers. | 🟡 Refined — debugging infrastructure. |

---

## Summary: Verdict Counts

| Verdict | Count | Description |
|---|---|---|
| ✅ Faithful | 28 | Matches proposal |
| ⭐ Strengthened | 9 | Goes beyond proposal, improves thesis claims |
| 🟢 Kept deviation | 4 | Deliberate divergence with justification |
| 🟡 Refined | 6 | Additive/defensive, no thesis impact |
| 🔴 Drift | 0 | All previously-flagged drift items are resolved |
| ❌ Missing | 3 | Post-eval aggregation work (Gini, KL, mock audit) |

### ✅ Done (no further code work before eval):
- ✅ All 5 pipeline stages (decomposition → gap detection → classification → generation → integration)
- ✅ Per-gap classification and generation
- ✅ Compositional execution with workspace state passing
- ✅ Three-tier verification gate with cost ladder
- ✅ Granularity optimizer with exact proposal formula/thresholds
- ✅ Four baselines implemented and isolated
- ✅ 3 of 6 eval metrics implemented
- ⭐ Lead-with-subagents for AGENT context isolation
- ⭐ Compositional executor for multi-artifact chain execution
- ⭐ Context engineering mapping (Write/Select/Compress/Isolate)
- 🟡 Registry threshold tuned to 0.6
- 🟡 Anti-monolith prompts across all generator types

### ❌ Missing (post-eval, decided):
- ❌ Gini coefficient aggregation (~1h)
- ❌ KL divergence surfacing + function rename (~1h)
- ❌ Mock contamination audit (~5h, mostly human labelling)

### ⚠️ Open questions (pre-eval) — see `docs/pre_eval_readiness.md`:
- ~~SKILL testbed coverage is weak~~ → ✅ Resolved: T04, T12, T14, T15 prompts strengthened with policy/rules language; T20 added as pure-SKILL task. 5 JIT SKILL tasks total.
- ~~AGENT coverage: 2/5 genuine, 2/5 borderline~~ → ✅ Resolved: T08, T10, T13 prompts strengthened with iterative/adaptive investigation requirements. 5 JIT AGENT tasks total.
- ~~JSON-only output format~~ → ✅ Resolved: B3 file output support implemented (ChainResult.output_files, CLI file saving, file_validator on TaskDefinition).
- Decomposer non-determinism (rules 10/11 ignored ~50% of the time) — accepted, document as Ch 6 finding
- Binary file handling depends on LLM compliance (T11 fails) — accepted, document as limitation
- ~~Network-disabled sandbox~~ → ✅ Resolved: `--allow-network` flag implemented on sandbox_execute, sandbox_execute_in_workspace, and CLI.
- ~~Tier 1 `missing_external_calls` severity~~ → ✅ Resolved: dynamic severity (MEDIUM offline, HIGH with `--allow-network`).
