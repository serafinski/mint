# DeepAgents Upgrade: Investigation Results

**Date:** 2026-03-29
**Current version:** 0.4.1
**Recommendation:** Bump to 0.4.12 for bug fixes. Do NOT invest in middleware wiring.

---

## Problem

DeepAgent invocations time out frequently during evaluation:

| Timeout type | Count (across all eval logs) | Limit |
|---|---|---|
| Adversarial probing | 118 | 180s |
| SKILL/AGENT execution | 47 | 120-180s |

Timeouts are silent degradation — adversarial probing fails open (incomplete verification), SKILL/AGENT execution returns empty output (`output_rejected`).

## Investigation

### Hypothesis: context growth causes timeouts
**Disproved.** Context utilization analysis (1,255 traces) shows max 28.8% of 128K window. Agents use 2-5 steps on average. The automatic `SummarizationMiddleware` in deepagents triggers at 85% context — never reached.

### Hypothesis: compaction/summarization middleware would help
**Disproved.** The compaction hook (0.4.5) and SummarizationToolMiddleware (0.4.8) address context pressure. Our agents don't have context pressure — they time out because individual LLM calls are slow, not because there are too many steps or too much context.

### Root cause: LLM response latency + Docker overhead
GPT-5-mini with function calling takes 5-15s per call (P50) with tail latency of 30-60s (P95). An adversarial probe with 4 LLM calls + 2 sandbox runs:
- Best case: 4×5s + 2×10s = 40s
- Typical: 4×10s + 2×20s = 80s
- Worst case (P95 tail): 4×30s + 2×30s = 180s (exactly at the limit)

The 180s timeout has zero margin for LLM latency spikes.

## Recommended fixes

### 1. Bump to deepagents 0.4.12 (5 min)

For bug fixes only:
- ToolMessage artifact preservation during eviction (0.4.4, #1482)
- Mixed content eviction fix (0.4.6, #1673)
- Parallel summarization offload (0.4.4, #1488)

No code changes needed — backward compatible.

### 2. Increase timeouts (5 min)

Based on research (dasroot.net, Augment Code, OpenClaw) — set timeouts at 2-3x P95 latency:

| Component | Current | Proposed | Rationale |
|---|---|---|---|
| Adversarial probing | 180s | 300s | Worst-case math = 180s; need headroom for LLM tail latency |
| SKILL execution | 120s | 240s | 3-5 LLM calls + sandbox; 120s is tight |
| AGENT execution | 180s | 300s | Same reasoning as adversarial |

Files to change:
- `src/verification/adversarial.py`: `ADVERSARIAL_TIMEOUT_S = 300`
- `tests/evaluation/execution.py`: `SKILL_EXECUTION_TIMEOUT_S = 240`, `AGENT_EXECUTION_TIMEOUT_S = 300`

### 3. Document as thesis finding

The timeout data (118 + 47 = 165 timeouts) and the root cause (LLM response latency, not context pressure) is itself a finding worth noting in the thesis. It demonstrates that wall-clock latency — not context window or step count — is the binding constraint on agent autonomy in the current system.
