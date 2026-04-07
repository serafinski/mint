# Codebase Contradictions Report — 2026-04-06

**Scope:** Full codebase scan for contradictions between code, comments, prompts, and documentation — focusing on stale assumptions from earlier implementations that were never updated.

**Auditor:** Claude Opus 4.6

---

## CRITICAL — Actively Hurting System Behavior

### CR1. MCP tool generator forbids packages the sandbox can install

**File:** `src/orchestrator/prompts/mcp_tool_generator.py:76-77`

```python
- Allowed imports: Python stdlib, `mcp`, `pydantic`, `pandas`, `numpy`
- Do NOT import any other external packages (no requests, httpx, scikit-learn, etc.)
```

**Reality:** The sandbox (`src/verification/sandbox_tool.py:43-84`) has full dynamic LLM-based import-to-PyPI resolution. It can install ANY package via two-phase Docker execution (Phase 1: `pip install` with network, Phase 2: run with `--network none`). CLAUDE.md explicitly states: "The sandbox resolves dependencies dynamically at runtime — no hardcoded package lists."

**Origin:** This restriction predates Fix 0 (dynamic dependency resolution), which was implemented in March 2026 to unblock the pilot run. The prompt was never updated.

**Impact:** Tools that should use `scikit-learn`, `scipy`, `statsmodels`, `requests`, `beautifulsoup4` etc. are generated without them or with hacky workarounds. This directly reduces generated tool quality for analytics tasks (T10, T13, T15, T16).

---

### CR2. "Four baseline conditions" docstring vs. five-member enum

**File:** `src/schemas/evaluation.py:1-4` vs `src/schemas/evaluation.py:15-22`

Docstring says "four baseline conditions" but `BaselineCondition` has five members: `FULL`, `NO_VERIFICATION`, `NO_OPTIMIZATION`, `STATIC`, `LONGITUDINAL`.

**Origin:** `LONGITUDINAL` was added later (2026-03-19) for the longitudinal optimizer test. The docstring was never updated.

---

## HIGH — Contradicts Documentation or Creates Maintenance Risk

### H1. Model name hardcoded in 10+ files instead of centralized constant

The string `"openai:gpt-5-mini"` is hardcoded in:

| File | Context |
|---|---|
| `src/orchestrator/classifier.py:43` | Classification LLM |
| `src/orchestrator/decomposition.py:60` | DeepAgent model |
| `src/orchestrator/generators/mcp_tool.py:~120` | Tool generation |
| `src/orchestrator/generators/skill.py:~160` | Skill generation |
| `src/orchestrator/generators/agent.py:~180` | Agent generation |
| `src/verification/sandbox_tool.py:55` | Import→PyPI resolution |
| `src/verification/adversarial.py:~115` | Adversarial probing |
| `src/optimizer/operations.py:107,217` | Split/merge defaults |
| `src/optimizer/loop.py:116` | Optimization loop |
| `tests/evaluation/execution.py:194,300` | SKILL/AGENT execution |
| `src/registry/store.py:523` | Seeded agent config JSON |

CLAUDE.md says "Provider-agnostic via `init_chat_model`" but switching models requires grep-replace across 10+ files. Should be a single constant in `src/config.py` or similar.

**Origin:** Model was unified from hybrid DeepSeek/GPT-5-mini in March 2026. Each file was updated individually rather than extracting a constant.

### H2. Duplicate thresholds and constants across files

| Constant | Location 1 | Location 2 |
|---|---|---|
| `_FILE_SIZE_LIMIT_BYTES = 10MB` | `src/cli/runner.py:21` | `scripts/run_task.py:100` |
| `AUTO_OPTIMIZER_THRESHOLD = 20` | `src/cli/runner.py:22` | `scripts/run_task.py:537` |
| `_BINARY_EXTENSIONS` (27 items) | `src/cli/runner.py:121-154` | `scripts/run_task.py:180-200` |
| Split/merge thresholds (0.6/0.7/0.05) | `src/optimizer/loop.py:113-115` | `src/optimizer/simulation.py:283-290` |
| Docker check logic | `src/cli/runner.py:249-261` | Missing from `scripts/run_task.py` |

**Origin:** `scripts/run_task.py` was the Level 1 CLI runner built before the Level 2 MINT CLI (`src/cli/`). Constants were copied rather than shared.

### H3. Prompt says "hyphen-case in FastMCP()" but code forces snake_case

**File:** `src/orchestrator/prompts/mcp_tool_generator.py:80`

```
- Tool name should be hyphen-case in FastMCP() and snake_case for the function
```

**Reality:** The code (`src/orchestrator/generators/mcp_tool.py:183`) uses `slugify_gap(gap.query)` which produces snake_case names. Line 219-223 overrides whatever the LLM chose with the forced snake_case name. The hyphen-case guidance is ignored.

**Origin:** Early prompt text from before Fix 5 (deterministic naming with hash suffix).

### H4. `_DOCKER_PREINSTALLED` hardcodes Dockerfile contents

**File:** `src/verification/sandbox_tool.py:24-25`

```python
_DOCKER_PREINSTALLED = frozenset({"mcp", "pydantic"})
```

This set must be manually synchronized with `sandbox/Dockerfile`. If the Dockerfile gains packages (e.g., `numpy` for performance), this set goes stale and dynamic resolution will `pip install` them again unnecessarily.

### H5. Iteration plan still references DeepSeek as current model

**File:** `docs/iteration_plan.md:199,212,552`

- Line 199: "Use `deepseek-chat` as the model"
- Line 212: "Use Claude API (or DeepSeek) for code generation"
- Line 552: "The adversarial agent uses `deepseek-chat`"

All code uses `openai:gpt-5-mini` since the March 2026 unification. These are stale plan entries.

### H6. system_overview.md has unresolved TODO for evaluation re-run

**File:** `docs/system_overview.md:158`

```
> **TODO: UPDATE AFTER RE-RUN** — A new evaluation is pending...
```

The preparation work (deepagents 0.4.12, timeout increases) is complete. The TODO should be resolved or explicitly marked as blocked on batch 2.

---

## MEDIUM — Inconsistent but Not Actively Harmful

### M1. Docker resource limits undocumented in CLAUDE.md

**File:** `src/verification/sandbox_tool.py:157-159,217-219`

Hardcoded `--memory 128m` (execution) / `256m` (install) / `--cpus 0.5`. CLAUDE.md documents all operational boundaries (recursion limits, timeouts) but omits Docker resource limits entirely.

### M2. Behavioral test timeout (15s) differs from sandbox default (10s)

**File:** `src/verification/behavioral.py:200` vs `src/verification/sandbox_tool.py:127`

All behavioral tests use `timeout=15` but the sandbox default is `timeout=10`. Neither value is documented in CLAUDE.md's operational boundaries table. The 15s choice appears arbitrary.

### M3. `LATENCY_THRESHOLD_S = 5.0` undocumented

**File:** `src/verification/behavioral.py:24`

This threshold (which includes ~1-2s Docker startup overhead) is not in CLAUDE.md's operational boundaries table, making it hard to adjust.

### M4. Sandbox image name hardcoded with no override

**File:** `src/verification/sandbox_tool.py:20`

```python
SANDBOX_IMAGE = "jit-sandbox"
```

No environment variable or config override. If someone builds a different image (e.g., for testing), they must edit source code.

### M5. Pip install timeout hardcoded at 60s

**File:** `src/verification/sandbox_tool.py:226`

Large packages requiring compilation (scipy, numpy from source) may exceed 60s in slow environments. Not configurable or documented.

### M6. LLM package resolution fallback undocumented

**File:** `src/verification/sandbox_tool.py:74-81`

On LLM failure, falls back to identity mapping (import name = pip name). CLAUDE.md mentions "On LLM failure, falls back to identity mapping" but doesn't document when this triggers or what the user sees.

### M7. Agent generator prompt hardcodes model name in examples

**File:** `src/orchestrator/prompts/agent_generator.py:15,51,90`

Examples show `"model": "openai:gpt-5-mini"` as the only option. No mention that other providers work through `init_chat_model()`.

### M8. KL divergence metric duality still unresolved

**File:** `src/optimizer/simulation.py:74-88`

Comment explicitly says "The thesis evaluation should specify which metric is the primary convergence indicator" — this decision appears deferred but directly affects thesis figures.

### M9. Verification finding categories are bare strings, not enum

Finding categories like `"hardcoded_data"`, `"mock_artifact"`, `"deception"`, `"silent_except"` are scattered across `static_scanner.py`, `behavioral.py`, and `gate.py` as raw strings. No central enum to prevent typos or aid discovery.

### M10. `scripts/run_task.py` lacks proactive Docker check

**File:** `scripts/run_task.py`

The CLI runner (`src/cli/runner.py:249-261`) checks Docker upfront with `_check_docker()`. The script runner only detects Docker failures AFTER attempting sandbox execution — user waits through decomposition before learning Docker is unavailable.

---

## LOW — Cosmetic or Minor Inconsistencies

### L1. SKILL frontmatter `allowed-tools` key in prompt without implementation

**File:** `src/orchestrator/prompts/skill_generator.py:23-24`

Prompt lists `allowed-tools` as a valid frontmatter key, but no code reads or enforces this field.

### L2. Seeded capabilities hardcoded in `store.py`

**File:** `src/registry/store.py:281-316`

The 8 seeded capabilities are hardcoded in Python. Adding a new seed requires code change rather than config file update.

### L3. Decomposition hard limit (3 subtasks, 5 capabilities) only in prompt

**File:** `src/orchestrator/prompts/decomposition.py:25-27`

The hard limit is enforced only by prompt instruction, not by code. The code circuit breaker (`decomposition.py:190-202`) has separate limits (`MAX_CAPS_WARN=5`, `MAX_CAPS_HARD=8`) that don't match the prompt's "at most 5 total".

### L4. Evaluation record schema vs. invocation record — relationship undocumented

`EvaluationRecord` (18 fields) and `InvocationRecord` (from MetricsStore) both track execution outcomes but for different purposes. Their relationship is not documented.

---

## Summary

| Severity | Count | Primary Theme |
|---|---|---|
| CRITICAL | 2 | Package restrictions contradict dynamic resolution; docstring/enum mismatch |
| HIGH | 6 | Hardcoded model names, duplicate constants, stale documentation |
| MEDIUM | 10 | Undocumented thresholds, missing config centralization, deferred decisions |
| LOW | 4 | Minor inconsistencies, cosmetic issues |

**Root cause pattern:** Most contradictions stem from incremental implementation (Iterations 0-5) where earlier restrictions were never relaxed after later features made them obsolete. The package restriction (CR1) is the highest-impact example — it was appropriate when the sandbox had no dynamic resolution, but now actively limits tool quality.

---

*Report generated by Claude Opus 4.6 — 2026-04-06*
