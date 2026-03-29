# Implementation Plan: Closing Research Question Gaps

**Date:** 2026-03-23
**Status:** Gap 2 and Gap 3 DONE, Gap 1 PLANNED (requires eval re-run)
**Context:** The 340-record evaluation partially answers the research question. Three data gaps were identified; two have been closed with existing infrastructure.

---

## Research Question

> How can we balance autonomy vs correctness, specificity vs generality, and context economy in self-developing multi-agent systems?

### What the evaluation already answers

| Dimension | Evidence | Strength |
|---|---|---|
| Autonomy vs Correctness | Verification tradeoff: +95% gate pass but -13pp success rate; semantic correctness gap (T06: 100% gate, 0% success) | Strong |
| Specificity vs Generality | Type selection matters (T17: AGENT 100% vs SKILL 9%); optimizer prune works in longitudinal test (69.2% -> 100%) | Moderate |
| Context Economy | Run-level token costs exist (~33k-96k tokens/JIT task by condition) | Weak |

### Three gaps and how to close them

---

## Gap 1: Per-Task Token Attribution

**Problem:** Token costs are run-level averages, not per-task measurements. Every task in a run gets the same number (e.g., 119,280 tokens). We cannot compare the cost of generating an MCP_TOOL vs a SKILL vs an AGENT.

**Root cause:** The pipeline creates one LangFuse callback handler per run. All LLM calls in a run share the same trace context, making it impossible to attribute calls to individual tasks after the fact.

**What exists in LangFuse:** Each DeepAgent invocation (decomposition, adversarial probing, skill execution) creates a named trace (`task-decomposer`, `adversarial-prober`, `skill-executor`) with per-step `usage_metadata` including `input_tokens`, `output_tokens`, `total_tokens`, and `calculated_total_cost`. The data is there but not tagged by task.

**Fix:**

1. In `tests/evaluation/harness.py` (`_run_one_task`), create a per-task LangFuse handler:
   ```python
   from langfuse.langchain import CallbackHandler
   handler = CallbackHandler(
       trace_name=f"{condition}_{task.task_id}",
       session_id=run_id,
       metadata={"task_id": task.task_id, "condition": condition},
   )
   invoke_config = {"callbacks": [handler]}
   ```

2. In `scripts/backfill_token_costs.py`, query by `session_id` (= `run_id`) and `metadata.task_id` instead of timestamp matching. Sum `totalCost` per trace group.

3. In `scripts/run_task.py`, the CLI already runs one task per invocation, so one trace = one task. Tag with task description hash.

**Effort:** ~1 hour (harness + backfill changes). **Requires re-running evaluation** for properly tagged traces.

**What it enables:** Per-task and per-type cost comparison:
- "MCP_TOOL generation costs ~X tokens on average, SKILL costs ~Y, AGENT costs ~Z"
- "Verification adds ~N tokens for MCP_TOOL vs ~M for SKILL"
- Directly answers context economy dimension of the research question

---

## Gap 2: Empirical Split/Merge Validation — DONE (2026-03-29)

**Problem:** The optimizer's split and merge operations are validated through unit tests (19 tests) and synthetic simulation, but never triggered in the evaluation or longitudinal test. Prune is the only empirically demonstrated operation.

**Root cause:** Split requires `reuse_rate > 0.6` (a capability used by 60%+ of task types). Merge requires two semantically similar Skills with low reuse. The 17-task testbed produces task-specific capabilities that don't meet these thresholds.

**What exists:** `src/optimizer/operations.py` has working `split()` and `merge()` implementations. `split()` calls GPT-5-mini to generate specialized variants. `merge()` calls GPT-5-mini to combine two similar Skills. Both are tested in `tests/unit/test_operations.py`.

**Fix — Integration test with seeded data:**

1. Create `scripts/test_split_merge.py` that:
   - Sets up a fresh ChromaDB registry
   - Registers a "data-parser" SKILL with broad description ("parse and transform any structured data file")
   - Seeds MetricsStore with synthetic invocations:
     - 50 invocations across 10 different task types (reuse_rate > 0.6)
     - 30% success rate (low enough to trigger split)
   - Calls `split(registry, metrics_store, "data-parser", "openai:gpt-5-mini")`
   - Verifies: 2+ specialized variants created, orignal deactivated

2. For merge:
   - Register two similar Skills: "csv-data-cleaner" and "tsv-data-cleaner"
   - Seed each with 5 invocations across 1 task type (low reuse)
   - Verify `registry.search()` similarity >= 0.5 between them
   - Call `merge(registry, metrics_store, ["csv-data-cleaner", "tsv-data-cleaner"], "openai:gpt-5-mini")`
   - Verify: merged capability created, parents deactivated

3. Run the full optimizer loop with the seeded data to verify the operations integrate correctly with `loop.py`.

**Effort:** ~2 hours (script + verification). Makes ~2-4 LLM calls ($0.01). Does NOT require re-running the evaluation.

**What it enables:** "Split and merge operations were validated end-to-end with real LLM generation" in the thesis, instead of "validated through unit tests only." Strengthens the specificity vs generality dimension.

**Results (2026-03-29):** Both operations pass end-to-end with real LLM calls via `scripts/test_split_merge.py`:

- **Split:** "data-file-processor" (SKILL, reuse=0.80, success=0.60) → split into 2 specialized variants ("csv-batch-cleaner" + "json-schema-validator"). Parent deactivated, variants registered, events logged in MetricsStore.
- **Merge:** "csv-data-cleaner" + "tsv-data-cleaner" (SKILLs, reuse=0.04, semantic similarity=0.66) → merged into "delimited-data-cleaner". Parents deactivated, combined tags preserved, events logged.
- Total cost: ~$0.001, ~15 seconds. Uses temp ChromaDB + in-memory DuckDB (no production data touched).

---

## Gap 3: Context Window Utilization — DONE (2026-03-29)

**Problem:** We measure token *cost* (how many tokens were spent) but not context *pressure* (how close the agent gets to its context window limit). A DeepAgent that uses 50k tokens across 5 steps is different from one that accumulates 50k tokens in a single growing context.

**Root cause:** Context utilization was never instrumented. The `recursion_limit=50` was set as a safety bound, but actual step counts and peak context sizes were never extracted.

**What exists in LangFuse:** Each DeepAgent trace contains the full message history. Each GENERATION observation has `usage_metadata.input_tokens` which represents the cumulative context size at that step (because the full conversation history is sent with each call). The last GENERATION observation's `input_tokens` = peak context size.

From existing traces we can already see:
- Adversarial prober (simple tool): 3 LLM steps, peak 8,978 input tokens (~7% of 128K)
- Adversarial prober (pandas tool): 4 LLM steps, peak 16,193 input tokens (~12.6% of 128K)
- Task decomposer: 2-5 LLM steps, peak ~6,000-8,000 input tokens

**Fix — Extraction script:**

1. Create `scripts/analyze_context_utilization.py` that:
   - Fetches all traces from LangFuse within the evaluation + longitudinal windows
   - For each trace, fetches observations with `include_observations=True`
   - Filters to GENERATION observations (LLM calls)
   - Extracts per-step: `input_tokens`, `output_tokens`, `reasoning_tokens`
   - Computes:
     - **Step count**: number of GENERATION observations per trace
     - **Peak context**: max `input_tokens` across steps (= cumulative context at final step)
     - **Context utilization**: peak context / 128,000 (GPT-5-mini window)
     - **Context growth rate**: how `input_tokens` increases per step
   - Groups by agent type (`task-decomposer`, `adversarial-prober`, `skill-executor`, `agent-executor`)
   - Outputs summary table + per-trace detail

2. Expected output format:
   ```
   Agent Type          Traces  Avg Steps  Peak Context  Utilization  Max Utilization
   task-decomposer        340       2.3        6,400        5.0%          8.2%
   adversarial-prober     210       3.5       12,300        9.6%         16.1%
   skill-executor          45       4.1       18,500       14.5%         22.0%
   agent-executor          15       5.8       24,000       18.8%         31.0%
   ```

3. This can also extract `output_token_details.reasoning_tokens` to quantify how much of GPT-5-mini's output is chain-of-thought reasoning vs actual content.

**Effort:** ~1-2 hours (script only). Read-only against LangFuse — no LLM calls, no pipeline runs, no evaluation re-run needed.

**What it enables:**
- "DeepAgent context utilization never exceeds X% of the 128K window" — proves the system has headroom for more complex tasks
- "Adversarial probing uses ~Y% more context than decomposition" — quantifies the cost of verification in context terms, not just tokens
- Directly answers the context economy dimension: the system is not context-bound, it is cost-bound

**Results (2026-03-29):** Extracted from 1,255 LangFuse traces via `scripts/analyze_context_utilization.py`:

| Agent Type | Traces | Avg Steps | Avg Peak Context | Avg Utilization | Max Utilization |
|---|---|---|---|---|---|
| Decomposition | 709 | 2.2 | 6,313 | 4.9% | 7.4% |
| Adversarial Probing | 353 | 5.2 | 13,352 | 10.4% | 28.8% |
| SKILL Execution | 147 | 3.5 | 15,352 | 12.0% | 27.6% |
| AGENT Execution | 46 | 1.4 | 10,441 | 8.2% | 10.8% |
| **ALL** | **1,255** | **3.2** | **9,503** | **7.4%** | **28.8%** |

Key findings:
- The system **never exceeds 28.8%** of the 128K context window (even in the most complex adversarial probing trace with 10 steps).
- Context grows ~2,000-3,000 tokens per DeepAgent step (tool call results accumulating in message history).
- Adversarial probing uses **2.1x more context** than decomposition (10.4% vs 4.9%), quantifying the verification cost in context terms.
- SKILL execution has the highest average peak (15,352 tokens, 12.0%) because it processes fixture data inside the context.
- At the observed growth rate (~2.5K/step), the system would need ~40 steps to reach 50% utilization — well beyond the `recursion_limit=50` safety bound.
- **The system is cost-bound, not context-bound.** Reducing token spend is more impactful than increasing context window size.

---

## Implementation Priority

| Gap | Effort | Re-run needed? | Research value | Status |
|---|---|---|---|---|
| Gap 3: Context utilization | 1-2h | No | High — directly answers context economy | **DONE** |
| Gap 2: Split/merge validation | 2h | No | High — strengthens specificity vs generality | **DONE** |
| Gap 1: Per-task token attribution | 1h + re-run | Yes (~14h, ~$5) | Medium — improves cost precision | PLANNED |

Recommended order: Gap 3 -> Gap 2 -> Gap 1 (if time permits for re-run).
Gap 3 and Gap 2 completed on 2026-03-29.

---

## Notes

- All LangFuse data has a retention window. Check `LANGFUSE_HOST` retention settings to ensure evaluation traces (March 18-20) are still available before writing extraction scripts.
- The LangFuse MCP tools (`fetch_traces`, `fetch_observations`) can be used interactively to prototype queries before writing scripts.
- Gap 1 and Gap 3 scripts can share the LangFuse fetching infrastructure — consider a shared `scripts/langfuse_utils.py` module.
