# Codebase Audit — Internal Contradictions (2026-04-07)

Full-codebase audit covering `src/`, `tests/`, `scripts/`, `docs/`, and all config files.
Methodology: 7 parallel auditors each read every file in their assigned module, then findings were cross-referenced and verified.

---

## Severity Legend

| Level | Meaning |
|-------|---------|
| **CRITICAL** | Will cause runtime failures or corrupt evaluation data |
| **HIGH** | Produces incorrect results silently or violates documented contracts |
| **MEDIUM** | Inconsistency that degrades quality but doesn't break execution |
| **LOW** | Documentation drift or dead code; no runtime impact |

---

## C1 — Agent skill_content frontmatter name vs directory name mismatch [CRITICAL]

**Files:** `src/orchestrator/generators/agent.py:148-158, 268-293`

**Description:**
When the LLM generates an agent with `is_deep_agent=True`, the validation at line 154 checks that the SKILL.md frontmatter `name` matches `result.agent_name`. However, there is **no validation** that `result.agent_name == forced_name`. The code then:

1. Writes the SKILL.md to disk at `forced_name/SKILL.md` (line 273)
2. Sets `artifact.capability_name = forced_name` (line 293)
3. But the SKILL.md frontmatter still contains `name: result.agent_name`

If the LLM ignores the prompt instruction and chooses a different `agent_name` than `forced_name`, the directory name won't match the frontmatter `name`. DeepAgents expects these to match.

**Example:**
- `forced_name = "pipeline-investigator-a1b2c3"`
- LLM generates `agent_name = "pipeline-investigator"` (ignored the hash suffix)
- Validation passes: `content_name == result.agent_name` (both `"pipeline-investigator"`)
- Disk: `.deepagents/skills/pipeline-investigator-a1b2c3/SKILL.md` contains `name: pipeline-investigator`
- Mismatch: directory name != frontmatter name

**Fix:** Add explicit validation that `result.agent_name == forced_name`, or rewrite the frontmatter `name` field to `forced_name` before writing to disk.

---

## C2 — Documentation claims only T01-T03 have static coverage; code has 5 [HIGH]

**Files:**
- `docs/system_overview.md:170` — "T01-T03 have matching seeded capabilities (testing retrieval). T04-T18 require the system to generate new capabilities (testing synthesis)."
- `CLAUDE.md:310` — "8 MCP_TOOL (3 retrieval + 5 synthesis)"
- `tests/evaluation/tasks.py:251,428` — T05 and T09 also have `has_static_coverage=True`

**Description:**
Documentation in three places (system_overview.md, CLAUDE.md, iteration_plan.md) claims only 3 tasks (T01-T03) have seeded capabilities and T04-T18 all require synthesis. In reality:

| Task | `has_static_coverage` | `static_capability_name` | Seeded Type |
|------|----------------------|--------------------------|-------------|
| T01 | True | `csv-parser` | MCP_TOOL |
| T02 | True | `json-validator` | MCP_TOOL |
| T03 | True | `text-statistics` | MCP_TOOL |
| T05 | True | `data-cleaning-workflow` | SKILL |
| T09 | True | `report-generation` | SKILL |

The actual breakdown is **5 tasks with static coverage** (3 MCP_TOOL + 2 SKILL), not 3. The `static` baseline condition will retrieve seeded capabilities for T05 and T09 as well, which changes the expected static success rate from 3/18 (16.7%) to 5/18 (27.8%).

**Impact:** Evaluation analysis that assumes 3 retrieval tasks will miscalculate retrieval vs synthesis success rates. The PILOT_TASKS docstring (line 17) even acknowledges this contradiction: "T05 (moderate JIT, static coverage)" — calling it both JIT and static.

**Fix:** Update system_overview.md, CLAUDE.md, and iteration_plan.md to document all 5 seeded tasks and distinguish type-matched retrieval (T01-T03) from cross-type coverage (T05, T09).

---

## C3 — Fixture file count inconsistency across docs [MEDIUM]

**Files:**
- `docs/system_overview.md:172` — "33 fixture files"
- `docs/iteration_plan.md:765` — "35 fixture files"
- `CLAUDE.md:310` — "33 fixture files"
- Actual filesystem: **32 files** (31 data fixtures + `expected_values.json`)

**Description:**
Three different numbers across three documents, none matching reality. The actual fixture files:

```
31 data files (t01_standard.csv through t18_orders_with_dupes.csv)
 1 metadata file (expected_values.json)
---
32 total
```

**Fix:** Update all three documents to say "32 fixture files" (or "31 data fixtures + 1 expected-values manifest").

---

## C4 — FailureReason enum test coverage incomplete [MEDIUM]

**Files:**
- `tests/unit/test_evaluation_schema.py:109` — `test_all_failure_reasons_valid()`
- `tests/unit/test_evaluation_schema.py:131-134` — `test_failure_reason_values()`
- `src/schemas/evaluation.py:34-43` — `FailureReason` enum (7 members)

**Description:**
The `FailureReason` enum has 7 members: `none`, `generation_failed`, `tier1`, `tier2`, `tier3`, `output_rejected`, `no_source_code`.

- `test_all_failure_reasons_valid()` (line 109) only tests 6 values — **`no_source_code` is missing** from the loop.
- `test_failure_reason_values()` (line 131-134) only asserts 3 of 7 enum value mappings — missing `tier1`, `tier2`, `tier3`, `no_source_code`.

**Impact:** The `NO_SOURCE_CODE` failure reason was added later (referenced in `tests/unit/test_harness.py:155` and `tests/evaluation/harness.py`) but the enum coverage test was never updated.

**Fix:** Add `"no_source_code"` to the test_all_failure_reasons_valid loop, and add the 4 missing assertions to test_failure_reason_values.

---

## C5 — Empty decomposition fallback uses full task description as capability query [MEDIUM]

**Files:** `src/orchestrator/decomposition.py:206-208`

**Description:**
When decomposition produces 0 subtasks (timeout, malformed LLM output, etc.), the fallback at line 208 sets:
```python
required_caps = [state.task_description]
```

This passes the **entire task description** (potentially hundreds of characters) as a single capability search query to gap detection. ChromaDB semantic search degrades with very long queries, reducing match quality.

**Example:** Task "Build an inventory reorder pipeline using two data sources: t04_inventory.csv and t04_reorder_rules.yaml. Cross-reference stock levels..." becomes one 200+ character search query instead of being decomposed into `["inventory_parser", "reorder_calculator"]`.

**Impact:** Gap detection accuracy degrades for complex tasks when decomposition fails. The pipeline still functions, but generates less-targeted capabilities.

**Fix:** Either truncate to first sentence, or extract key noun phrases as separate queries.

---

## C6 — Snapshot restore mismatch is warning-only, doesn't fail evaluation run [MEDIUM]

**Files:** `tests/evaluation/harness.py:237-244`

**Description:**
When `restore_clean_snapshot()` returns a snapshot ID that doesn't match the expected ID, the harness only logs a warning:
```python
if self._snap_id and snap_id != self._snap_id:
    logger.warning("Snapshot ID mismatch: expected %s, got %s — restore may be broken", ...)
```

The evaluation continues with potentially corrupted registry state. This can produce invalid evaluation records that look correct but operate on the wrong baseline.

**Impact:** Silent data integrity violation in research evaluation. Any run where the snapshot was corrupted produces unreliable results.

**Fix:** Raise an exception (or mark the run as tainted) when snapshot IDs don't match.

---

## C7 — T10 validator accepts undocumented output field names [LOW]

**Files:** `tests/evaluation/tasks.py:441-451`

**Description:**
T10's task prompt specifies the output key should be `'forecast'` (list of float). But the validator also silently accepts `'forecast_values'` and `'next_4_weeks'`:

```python
forecast = output.get("forecast") or output.get("forecast_values") or output.get("next_4_weeks")
```

This leniency means different runs may produce different key names that all pass validation, making cross-run comparison unreliable. The LLM has no guidance that alternatives are acceptable.

**Impact:** Inconsistent output structure across runs; minor impact on evaluation integrity.

**Fix:** Either document the acceptable alternatives in the task prompt, or restrict to the single documented key.

---

## C8 — Skill execution system prompt is data-analytics-specific [LOW]

**Files:** `tests/evaluation/execution.py:148-160`

**Description:**
The `_SKILL_SYSTEM_PREAMBLE` hardcodes:
```
You are a skill executor for a data analytics pipeline.
```

SKILL artifacts are supposed to be domain-agnostic (SKILL.md is a general instruction format). This preamble biases execution toward data tasks, which is fine for the current evaluation testbed (Business Analytics Pipeline) but would need changing for any other domain.

**Impact:** No immediate impact since the testbed IS data analytics. Documents as a future portability concern.

---

## C9 — GenericResult fallback silently wraps output in `{"result": {...}}` [LOW]

**Files:** `tests/evaluation/execution.py:100-102, 193-194`

**Description:**
Tasks without an entry in `TASK_RESULT_MODELS` fall back to `GenericResult`:
```python
class GenericResult(BaseModel):
    result: dict = {}
```

This wraps all LLM output into `{"result": {...}}`, but validators for those tasks expect flat keys (e.g., `output.get("row_count")`). If a task that should be in `TASK_RESULT_MODELS` is accidentally missing, its validator will always fail.

**Impact:** Currently all tasks that need structured execution (T04, T08, T10-T17) have entries. The risk is if a new task is added without a result model.

---

## C10 — Skill name fallback derivation is dead code [LOW]

**Files:** `src/orchestrator/generators/skill.py:240-247`

**Description:**
When `extract_skill_name(content)` returns None, the code derives a fallback name from `gap.query`. However, line 314 always overrides with `forced_name` regardless:
```python
artifact = GeneratedArtifact(capability_name=forced_name, ...)
```

The fallback derivation at lines 240-247 is computed but never actually used in the final artifact. The `skill_name` variable is only used for the `validate_skill_content()` call, not for the artifact name.

**Impact:** No runtime impact; just unused computation.

---

## Summary

| ID | Severity | Category | Status |
|----|----------|----------|--------|
| C1 | CRITICAL | Agent generator: frontmatter/directory name mismatch | **FIXED** — rewrite frontmatter name to forced_name before disk write |
| C2 | HIGH | Documentation: static coverage task count wrong | **FIXED** — updated system_overview.md, iteration_plan.md, CLAUDE.md |
| C3 | MEDIUM | Documentation: fixture file count wrong (3 different numbers) | **FIXED** — corrected to 32 in all three docs |
| C4 | MEDIUM | Tests: FailureReason enum coverage incomplete | **FIXED** — added NO_SOURCE_CODE + 4 missing enum assertions |
| C5 | MEDIUM | Decomposition: full task description as fallback query | **FIXED** — truncate to first sentence, max 200 chars |
| C6 | MEDIUM | Evaluation: snapshot mismatch is warning-only | **FIXED** — now raises RuntimeError |
| C7 | LOW | Task T10: undocumented alternative output keys | **FIXED** — removed undocumented fallback keys |
| C8 | LOW | Execution: domain-specific system preamble | **FIXED** — made generic ("skill executor" not "data analytics pipeline") |
| C9 | LOW | Execution: GenericResult fallback wrapping | **FIXED** — added `extra="allow"` so flat keys pass through |
| C10 | LOW | Skill generator: dead fallback code path | **FIXED** — replaced with `forced_name` fallback (no dead derivation) |

**Totals:** 1 CRITICAL, 1 HIGH, 4 MEDIUM, 4 LOW — **all 10 resolved**

All 814 unit tests pass after fixes.
