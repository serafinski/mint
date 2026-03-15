# Pipeline Performance Fix Plan

**Date:** 2026-03-11 (original), updated 2026-03-16
**Branch:** `fixes/iter5`
**Goal:** Reduce pilot runtime from 2+ hours to ~15–25 minutes
**Test baseline:** 707 passing at plan time → 787 after all fixes.
**Status:** All fixes implemented. Targeted verification confirms T08 (100%), T11/SKILL (100%), T13/AGENT (100%). Full 13-task re-run pending.

---

## Implementation Order

```
Fix 0   dynamic LLM-based import→PyPI resolution                (unblocking, do first)
Fix 1   decomposition prompt + two-stage circuit breaker
Fix 2   pin seeded capabilities against pruning
Fix 3   track registry matches as invocations
Fix 5   deterministic naming with hash suffix                    (must precede Fix 4)
Fix 4   skip already-passing gaps on retry
Fix 6   parallelize verification
Fix 7   parallelize generation
```

Note: Fix 5 is numbered before Fix 4 intentionally — Fix 5 must be implemented first because Fix 4's replace-by-name deduplication relies on stable names to work correctly across retries.

---

## Fix 0: Sandbox Dependencies

**Why first:** Generated tools fail behavioral testing (Tier 2) with `No module named 'pandas'`. This is a hard runtime error that causes every tool importing pandas to fail verification unconditionally, independently of correctness.

### Original plan

Hardcode `pandas` and `numpy` in Dockerfile alongside `mcp` and `pydantic`. Update the generator prompt to list allowed imports. Simple, fast, but limited to a fixed set of packages.

### What was actually implemented

The hardcoded approach was rejected during development — it doesn't emulate a real scenario where generated tools can import arbitrary packages. Instead, all hardcoded package lists were replaced with **dynamic LLM-based import→PyPI resolution**. This handles any third-party package (sklearn, scipy, requests, etc.) without maintaining a mapping.

**Changes:**

1. **`sandbox/Dockerfile`** — Stripped to minimal base: only `mcp` + `pydantic` (required by FastMCP tool pattern). No data-processing packages.

2. **`src/verification/sandbox_tool.py`** — New dynamic resolution pipeline:
   - `extract_imports(source_code)` — AST-based import extraction from generated source
   - `resolve_extra_packages(source_code)` — Filters stdlib via `sys.stdlib_module_names` (no hardcoded list), then asks `deepseek-chat` to map remaining import names to PyPI package names (e.g., `sklearn` → `scikit-learn`). Session-level `_pip_resolution_cache` avoids repeated LLM calls. Falls back to identity mapping (import name = pip name) on LLM failure.
   - `sandbox_execute(code, timeout, extra_packages)` — Two-phase Docker execution: Phase 1 runs `pip install` with `--network bridge` and commits to an ephemeral image; Phase 2 runs code with `--network none` against that image. Ephemeral image cleaned up after execution.

3. **`src/verification/behavioral.py`** — All 6 `_test_*` functions gained a `_sandbox` parameter. `run()` creates a `functools.partial(sandbox_execute, extra_packages=...)` from the artifact's source code so behavioral tests install the same dependencies.

4. **`src/orchestrator/prompts/mcp_tool_generator.py`** — Updated to allow any third-party imports (resolved dynamically), not just stdlib + mcp + pydantic.

---

## Fix 1: Constrain Decomposition Granularity

**Why critical:** T07 produces 14 capabilities instead of 1–2. Every downstream phase scales linearly with capability count. This is the single highest-leverage change — fixing decomposition shrinks the cost of every other phase automatically.

### Change 1: `src/orchestrator/prompts/decomposition.py`

Four targeted changes to `DECOMPOSITION_SYSTEM_PROMPT`:

**1. Replace instruction 3** (the root cause of over-decomposition):
```python
# Old:
"3. Break the task into the smallest meaningful subtasks that can be executed "
"   independently or with clear dependencies."

# New:
"3. Break the task into the FEWEST subtasks needed to cover the full job. "
"   Prefer one coarse-grained capability that does the whole job over many "
"   fine-grained primitives. If the task is a single coherent operation "
"   (e.g. fuzzy-join two CSVs), produce exactly 1 subtask with 1 capability."
```

**2. Replace instruction 7** (soft guideline → hard limit):
```python
# Old:
"7. Aim for 2-8 subtasks. Very simple tasks may have just 1-2 subtasks."

# New:
"7. HARD LIMIT: produce at most 3 subtasks and at most 5 total "
"   required_capabilities across all subtasks combined. A single subtask "
"   with 1 capability is the correct output for most tasks."
```

**3. Add new instruction 8:**
```python
"8. NEVER list generic infrastructure operations as separate required "
"   capabilities. File I/O, string manipulation, lowercasing, whitespace "
"   trimming, and regex are implementation details that belong inside a "
"   capability's code — not separate capabilities in their own right."
```

**4. Replace the few-shot example** — this is as important as the instructions. The current example shows `["parse CSV file", "file I/O"]` as separate capabilities, which actively teaches the model to enumerate primitives regardless of the instruction text. Replace the entire example JSON:

```json
{
  "subtasks": [
    {
      "description": "Fuzzy-join two CSV files on the company_name column, applying normalisation and similarity threshold internally",
      "required_capabilities": ["fuzzy-join-csvs"],
      "depends_on": []
    }
  ],
  "reasoning": "The entire task is a single coherent operation. Normalisation (lowercase, trim, regex), similarity scoring, and joining are all implementation details of one fuzzy-join capability — not separate capabilities."
}
```

### Change 2: `src/orchestrator/decomposition.py`

Add two-stage circuit breaker immediately after the deduplication line (`required_caps = list(dict.fromkeys(required_caps))`):

```python
# Two-stage circuit breaker: warn at WARN_CAPS, hard truncate at MAX_CAPS_HARD.
# WARN: emit visibility signal but proceed intact — genuine 6-7 step tasks survive.
# TRUNCATE: pathological 14-step decompositions are caught before they explode
#           generation and verification time.
WARN_CAPS = 5
MAX_CAPS_HARD = 8

if len(required_caps) > WARN_CAPS:
    logger.warning(
        "Decomposition produced %d required capabilities (warn threshold %d). "
        "Prompt constraints may not have been followed.",
        len(required_caps), WARN_CAPS,
    )
    emit_langfuse_event(
        langfuse_handler,
        "decomposition.over_warn_limit",
        "WARNING",
        {"cap_count": len(required_caps), "warn_limit": WARN_CAPS, "caps": required_caps},
    )

if len(required_caps) > MAX_CAPS_HARD:
    logger.error(
        "Decomposition produced %d capabilities — truncating to %d (hard limit). "
        "This is a pathological decomposition; the prompt may not be working.",
        len(required_caps), MAX_CAPS_HARD,
    )
    emit_langfuse_event(
        langfuse_handler,
        "decomposition.hard_truncation",
        "CRITICAL",
        {
            "original_count": len(required_caps),
            "truncated_to": MAX_CAPS_HARD,
            "dropped": required_caps[MAX_CAPS_HARD:],
        },
    )
    required_caps = required_caps[:MAX_CAPS_HARD]
```

`WARN_CAPS` and `MAX_CAPS_HARD` are module-level constants so the harness can override them per evaluation condition if needed.

**New tests:**
- `test_caps_under_warn_limit_pass_through_unchanged()` — 3 caps, no warning, all 3 returned.
- `test_over_warn_limit_logs_warning_but_does_not_truncate()` — patch agent to return 6 caps, assert all 6 come through and warning logged.
- `test_hard_truncation_at_max_caps()` — patch agent to return 10 caps, assert exactly 8 returned and CRITICAL LangFuse event emitted with `"dropped"` key containing the 2 removed caps.

---

## Fix 2: Pin Seeded Capabilities Against Pruning

**Why:** After T07 completes, `prune()` deactivates all seeded capabilities (csv-parser, json-validator, etc.) because they have zero invocations in the metrics store. T01 — which should trivially reuse csv-parser — then generates everything from scratch, destroying the reuse cluster benefit the thesis depends on.

### Change 1: `src/registry/store.py`

In `seed_registry()`, add `metadata={"pinned": "true"}` to every seeded capability. `CapabilityDescriptor.metadata: dict[str, str]` already exists on the schema and is already serialized into ChromaDB's `extra_metadata` column and deserialized back by `_descriptor_from_metadata()` — confirmed by reading `store.py`. The `_cap()` helper needs one new parameter:

```python
def _cap(
    name: str,
    description: str,
    capability_type: CapabilityType,
    domain: str = "general",
    tags: list[str] | None = None,
) -> CapabilityDescriptor:
    kwargs: dict = {
        "name": name,
        "description": description,
        "capability_type": capability_type,
        "metadata": {"pinned": "true"},  # all seeded caps are pinned
    }
    if has_domain:
        kwargs["domain"] = domain
    if has_tags and tags:
        kwargs["tags"] = tags
    return CapabilityDescriptor(**kwargs)
```

All 8 existing seed entries in the `seeds` list pick up `pinned` automatically.

### Change 2: `src/optimizer/operations.py`

In `prune()`, add the pin guard before the invocation check:

```python
def prune(registry, metrics_store, *, window_days=30) -> list[str]:
    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    pruned = []
    for cap in registry.list_all(is_active=True):
        # Never prune pinned capabilities (seeded entries that should survive
        # optimizer cycles regardless of invocation count)
        if cap.metadata.get("pinned") == "true":
            logger.debug("Skipping pinned capability '%s'", cap.name)
            continue
        if metrics_store.invocations_since(cap.name, cutoff) == 0:
            registry.deactivate(cap.name)
            metrics_store.record_event(
                cap.name, "pruned", f"zero invocations in {window_days}d"
            )
            pruned.append(cap.name)
    return pruned
```

**New tests:**
- `test_prune_skips_pinned_capability()` — register cap with `metadata={"pinned": "true"}`, zero invocations, assert not in pruned list and `registry.deactivate` not called.
- `test_prune_deactivates_unpinned_zero_invocation()` — existing behaviour confirmed, no regression.
- `test_prune_mixed_registry_only_deactivates_unpinned()` — 3 caps: 1 pinned, 1 unpinned with invocations, 1 unpinned without; only the last gets deactivated.

---

## Fix 3: Track Registry Matches as Invocations

**Why:** Gap detection finds and uses seeded capabilities but never records the activity in `MetricsStore`. Even after Fix 2 (pinning), this leaves the metrics picture inaccurate — capabilities that are frequently matched appear idle. Fix 3 makes the system self-correcting: capabilities being actively used stay alive through real metrics, not just the static pin.

**Metric distortion note:** Recording matches with `token_cost=0, latency_ms=0.0` will drag down average cost/latency for matched capabilities. This is intentional — document in the thesis as a design choice. Registry matches represent "availability cost", not "execution cost", and the distortion is small relative to the benefit of accurate activity tracking.

### Change 1: `src/orchestrator/gap_detection.py`

Add optional `metrics_store` parameter with `None` default — all existing callers remain unaffected:

```python
from src.registry.metrics import MetricsStore
from src.schemas.metrics import InvocationRecord

def gap_detection_node(
    state: TaskState,
    registry: SkillRegistry,
    metrics_store: MetricsStore | None = None,
) -> Command[Literal["classifier_node"]]:
    ...
    # [existing match/gap detection logic — unchanged]
    ...

    # Record matched capabilities as invocations so they're not pruned as idle.
    # token_cost=0, latency_ms=0.0 are intentional: matches are not executions.
    # TODO: replace state.task_description[:100] with state.task_id once that
    #       field is added to TaskState.
    if metrics_store is not None:
        for m in matches:
            metrics_store.record_invocation(InvocationRecord(
                capability_id=m.capability.name,
                task_id=state.task_description[:100],
                success=True,
                token_cost=0,
                latency_ms=0.0,
            ))

    return Command(update={...}, goto="classifier_node")
```

### Change 2: `src/orchestrator/pipeline.py`

Wire `metrics_store` into the gap detection closure. It is already a parameter on `create_pipeline` — it just isn't being passed to `gap_detection_node` yet:

```python
builder.add_node(
    "gap_detection",
    lambda state: gap_detection_node(state, registry, metrics_store=metrics_store),
)
```

**New tests:**
- `test_records_match_invocations_when_metrics_store_provided()` — real `MetricsStore`, matched cap, call `gap_detection_node`, assert `metrics_store.invocations_since(cap_name, cutoff) > 0`.
- `test_no_metrics_store_does_not_crash()` — `metrics_store=None`, runs without error.
- `test_gaps_not_recorded_as_invocations()` — gap detected (no match), assert no invocation records written.

---

## Fix 5: Deterministic Capability Naming with Hash Suffix

**Why before Fix 4:** Without stable naming, the replace-by-name deduplication in all three generators (`artifacts = [a for a in artifacts if a.capability_name != artifact.capability_name]`) can silently fail — two retry attempts for the same gap produce `fuzzy_matcher` and `fuzzy_matching_tool`, and both survive in state as duplicates, inflating artifact count and verification time.

**Why hash suffix:** A naive slugify maps `"parse CSV file"` and `"parse CSV files"` to the same `parse_csv_file`. A 6-character sha256 suffix is deterministic (same query → same name across retries) and collision-free (different queries → different names).

### Change 1: `src/orchestrator/generators/utils.py`

Add `slugify_gap` alongside the existing `build_task_context`:

```python
import hashlib
import re

def slugify_gap(query: str) -> str:
    """Derive a stable, collision-free snake_case name from a gap query string.

    The base is a truncated slug of the query text. A 6-character sha256
    suffix ensures two similar queries (e.g. "parse CSV file" vs "parse CSV
    files") never collide, while the same query always produces the same name
    across retry attempts.

    Examples:
        "fuzzy string matching"  ->  "fuzzy_string_matching_a3b2c1"
        "parse CSV file"         ->  "parse_csv_file_9f4d2e"
        "parse CSV files"        ->  "parse_csv_files_71c3a8"
    """
    base = re.sub(r"[^a-z0-9]+", "_", query.lower().strip()).strip("_")[:40]
    suffix = hashlib.sha256(query.encode()).hexdigest()[:6]
    return f"{base}_{suffix}"
```

### Change 2: `src/orchestrator/generators/mcp_tool.py`

In `generate_single_tool()`, derive `forced_name` before the LLM call and override post-generation:

```python
from src.orchestrator.generators.utils import slugify_gap

def generate_single_tool(gap: ClassifiedGap, task_context: str = "") -> tuple[...]:
    forced_name = slugify_gap(gap.query)
    
    user_prompt = MCP_TOOL_USER_PROMPT.format(
        description=gap.description,
        query=gap.query,
        task_context=task_context or "No additional context",
        forced_name=forced_name,
    )
    ...
    # After successful generation — override whatever name the LLM chose:
    if result.tool_name != forced_name:
        logger.debug(
            "Overriding LLM name '%s' with stable slug '%s'",
            result.tool_name, forced_name,
        )
    return GeneratedArtifact(
        capability_name=forced_name,   # always the slug, never LLM-chosen
        capability_type=CapabilityType.MCP_TOOL,
        source_code=result.source_code,
        description=result.description,
    ), None
```

Also update `MCP_TOOL_USER_PROMPT` in `src/orchestrator/prompts/mcp_tool_generator.py` to include `{forced_name}`:

```python
MCP_TOOL_USER_PROMPT = """\
Generate a complete MCP tool implementation for the following capability:

Description: {description}
Search query: {query}
Task context: {task_context}

The tool function MUST be named exactly `{forced_name}` (snake_case). \
Do not choose a different name.

Generate a complete, working Python file following the FastMCP pattern shown \
in the system prompt.\
"""
```

### Change 3: `src/orchestrator/generators/skill.py` — same pattern

In `_generate_single_skill()`, derive `forced_name = slugify_gap(gap.query)` before the LLM call and override the `capability_name` on the returned artifact with `forced_name`.

Also update `SKILL_GENERATOR_PROMPT` in `src/orchestrator/prompts/skill_generator.py` to include `{forced_name}` in the prompt and pass it in the `.format()` call:

```python
# Add to SKILL_GENERATOR_PROMPT after the capability block:
"The SKILL.md `name` frontmatter field MUST be exactly `{forced_name}` \
(hyphen-case). Do not choose a different name."

# In _generate_single_skill():
forced_name = slugify_gap(gap.query)
prompt = SKILL_GENERATOR_PROMPT.format(
    description=gap.description,
    query=gap.query,
    task_context=task_context,
    forced_name=forced_name,
)
```

Note: skill names are hyphen-case, so `slugify_gap` output (snake_case with underscores) needs to be converted: `forced_name.replace("_", "-")` for skills specifically. Add a `slugify_gap_hyphen(query)` variant in `utils.py` or do the conversion inline.

### Change 4: `src/orchestrator/generators/agent.py` — same pattern

In `_generate_single_agent()`, derive `forced_name = slugify_gap(gap.query).replace("_", "-")` (agents use hyphen-case names, same as skills) and override `capability_name` on the returned artifact.

Also update `AGENT_GENERATOR_PROMPT` in `src/orchestrator/prompts/agent_generator.py` to include `{forced_name}`:

```python
# Add to AGENT_GENERATOR_PROMPT:
"The `agent_name` field MUST be exactly `{forced_name}` (hyphen-case). \
Do not choose a different name."

# In _generate_single_agent():
forced_name = slugify_gap(gap.query).replace("_", "-")
prompt = AGENT_GENERATOR_PROMPT.format(
    description=gap.description,
    query=gap.query,
    task_context=task_context,
    forced_name=forced_name,
)
```

**New tests:**
- `test_slugify_gap_stable_across_calls()` — same query produces same slug on repeated calls.
- `test_slugify_gap_collision_free()` — `"parse CSV file"` and `"parse CSV files"` produce different slugs.
- `test_slugify_gap_handles_special_chars()` — punctuation, mixed case, trailing spaces all normalised.
- `test_generate_single_tool_uses_forced_name()` — mock LLM returning `tool_name="something_else"`, assert artifact uses `slugify_gap(gap.query)`.
- `test_generate_single_skill_uses_forced_name()` — same for skill generator.
- `test_generate_single_agent_uses_forced_name()` — same for agent generator.

---

## Fix 4: Skip Already-Passing Gaps on Retry

**Why:** When 2/10 artifacts fail verification, all 11 classified gaps are regenerated (11 LLM calls × ~25s = ~4.5 min) instead of just the 2 failed ones. Fix 4 cuts retry regeneration cost proportionally.

**Why this is self-contained from Fix 5:** Fix 4 compares `gap.query` strings directly via `a.gap_query` — it does not depend on capability names at all. Fix 5 is a prerequisite for the *replace-by-name deduplication* (a separate concern), not for the skip logic itself.

### Change 1: `src/schemas/task.py`

Add `gap_query` to `GeneratedArtifact` — optional with `None` default, no existing code breaks:

```python
gap_query: str | None = Field(
    default=None,
    description=(
        "The gap query string that produced this artifact. "
        "Set at generation time to enable skip logic on retry — "
        "gaps whose query matches an existing passing artifact are skipped."
    ),
)
```

Confirm: `EvaluationStore` stores `EvaluationRecord` objects, not `GeneratedArtifact` — no serialization impact.

### Change 2: All three generators — `mcp_tool.py`, `skill.py`, `agent.py`

**Part A:** In each single-generation function, set `gap_query=gap.query` on the returned artifact:

```python
# mcp_tool.py — in generate_single_tool():
return GeneratedArtifact(
    capability_name=forced_name,
    capability_type=CapabilityType.MCP_TOOL,
    source_code=result.source_code,
    description=result.description,
    gap_query=gap.query,   # ADD
), None

# skill.py — in _generate_single_skill():
return GeneratedArtifact(
    capability_name=forced_name,
    capability_type=CapabilityType.SKILL,
    skill_content=content,
    description=...,
    gap_query=gap.query,   # ADD
), None

# agent.py — in _generate_single_agent():
return GeneratedArtifact(
    capability_name=forced_name,
    capability_type=CapabilityType.AGENT,
    agent_config=agent_config,
    description=result.description,
    gap_query=gap.query,   # ADD
), None
```

**Part B:** In each generator node, build the skip set before the loop:

```python
# mcp_tool.py — in generate_mcp_tool():
mcp_gaps = [g for g in state.classified_gaps if g.classified_type == CapabilityType.MCP_TOOL]
artifacts = list(state.generated_artifacts)
errors = list(state.errors)

# On retry, state.generated_artifacts contains only passing artifacts
# (verification_node strips failed ones). Skip any gap already covered.
existing_gap_queries = {a.gap_query for a in artifacts if a.gap_query is not None}

task_context = build_task_context(state)
for gap in mcp_gaps:
    if gap.query in existing_gap_queries:
        logger.info("  → skipping (already passed verification): %s", gap.query)
        continue
    # ... rest of generation loop unchanged
```

Apply the same `existing_gap_queries` skip pattern in `generate_skill()` and `generate_agent()`.

**New tests:**
- `test_skip_gaps_with_existing_passing_artifact()` — state with pre-existing artifact with `gap_query="fuzzy join csvs"`, assert mock `generate_single_tool` is not called for that gap.
- `test_gap_query_set_on_generated_artifact()` — assert `artifact.gap_query == gap.query` after generation.
- `test_first_attempt_processes_all_gaps()` — no existing artifacts, `existing_gap_queries` is empty, all gaps processed.
- `test_gap_query_none_does_not_crash_skip_set()` — artifact with `gap_query=None` in state, skip set handles it gracefully.

---

## Fix 6: Parallelize Verification

**Why:** Verification runs sequentially. 14 artifacts × ~2 min each = 28 min per attempt × 3 attempts = ~84 min. Each `gate_fn` call is independent (isolated Docker sandbox). With 4 workers: 14 artifacts → ~8 min. After Fix 1 reduces artifact count to 1–2, this remains a safety net for any task that still generates multiple artifacts.

### Change: `src/orchestrator/verification_node.py`

Replace the sequential `for` loop with `ThreadPoolExecutor`. Two critical correctness requirements:

1. **Fail-safe on exception:** a gate exception means the artifact is *unverified*. Set `overall_passed=False`. Never let a crashed verification gate silently pass an untested artifact into the registry.

2. **No concurrent state mutation:** collect all results then process sequentially after the pool closes — `as_completed` returns in non-deterministic order, which is fine since no existing code depends on report ordering.

```python
import concurrent.futures

MAX_VERIFICATION_WORKERS = 4

with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_VERIFICATION_WORKERS) as pool:
    future_to_artifact = {
        pool.submit(gate_fn, artifact): artifact
        for artifact in state.generated_artifacts
    }
    for future in concurrent.futures.as_completed(future_to_artifact):
        artifact = future_to_artifact[future]
        try:
            report = future.result()
        except Exception as exc:
            logger.error(
                "gate_fn raised unexpectedly for '%s': %s — treating as "
                "verification failure (fail-safe)",
                artifact.capability_name, exc,
            )
            report = VerificationReport(
                capability_name=artifact.capability_name,
                findings=[VerificationFinding(
                    tier=1,
                    category="gate_error",
                    severity=Severity.HIGH,
                    message=f"Verification gate raised exception: {exc}",
                )],
                overall_passed=False,  # fail-safe: unverified != passing
                tier_reached=0,
            )
        new_reports.append(report)
        if report.overall_passed:
            passed_artifacts.append(artifact)
        else:
            failed_artifacts.append(artifact)
            for finding in report.findings:
                if finding.severity in (Severity.CRITICAL, Severity.HIGH):
                    errors.append(
                        f"[{artifact.capability_name}] "
                        f"{finding.category}: {finding.message}"
                    )
```

Thread-safety: `gate_fn` calls `sandbox_execute` (subprocess to Docker — thread-safe) and `adversarial.probe()` (creates a fresh `DeepAgent` per call — thread-safe). Safe to parallelise.

**New tests:**
- `test_parallel_verification_processes_all_artifacts()` — 4 artifacts with stub gate, all 4 processed and in `passed_artifacts`.
- `test_gate_exception_treated_as_failure_not_pass()` — gate raises for one artifact, that artifact ends up in `failed_artifacts` not `passed_artifacts`, error in `state.errors`.
- `test_parallel_preserves_prior_verification_results()` — prior reports already in `state.verification_results` are not clobbered by the parallel run.

---

## Fix 7: Parallelize Generation

**Why:** Generator nodes process gaps sequentially. 9 MCP gaps × ~25–30s each = ~5 min per cycle × 3 retry attempts = ~15 min total sequential generation. Parallelising to 4 workers reduces this to ~2 min total.

### Change: `src/orchestrator/generators/mcp_tool.py`, `skill.py`, `agent.py`

Three correctness requirements:

1. **Collect results before merging:** the replace-by-name deduplication mutates the `artifacts` list. If this runs concurrently from multiple threads it's a race condition. Collect all `(artifact, error)` pairs first, merge sequentially after the pool closes.

2. **Thread-safe LLM init:** add double-checked locking to each `_get_llm()`.

3. **Correct model name:** `deepseek:deepseek-chat` — confirmed from `CLAUDE.md` and `agent_generator.py`. Do not use `gpt-4o-mini`.

4. **Integrate Fix 4's skip logic in the futures comprehension** — filter out already-passing gaps before submitting futures, so no threads are wasted on work that will be skipped:

```python
import concurrent.futures
import threading

MAX_GENERATION_WORKERS = 4
_llm_lock = threading.Lock()

def _get_llm():
    global _generator_llm
    if _generator_llm is None:
        with _llm_lock:
            if _generator_llm is None:
                from langchain.chat_models import init_chat_model
                _generator_llm = init_chat_model(
                    "deepseek:deepseek-chat", request_timeout=300
                )
    return _generator_llm


# In generate_mcp_tool():
existing_gap_queries = {a.gap_query for a in artifacts if a.gap_query is not None}

# Filter before submitting — don't waste a thread on a gap already covered
gaps_to_generate = [
    gap for gap in mcp_gaps
    if gap.query not in existing_gap_queries
]

for skipped in mcp_gaps:
    if skipped.query in existing_gap_queries:
        logger.info("  → skipping (already passed verification): %s", skipped.query)

with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_GENERATION_WORKERS) as pool:
    futures = {
        pool.submit(generate_single_tool, gap, task_context=task_context): gap
        for gap in gaps_to_generate
    }
    raw_results: list[tuple[GeneratedArtifact | None, str | None]] = []
    for future in concurrent.futures.as_completed(futures):
        try:
            raw_results.append(future.result())
        except Exception as exc:
            gap = futures[future]
            raw_results.append(
                (None, f"Generation failed for {gap.query!r}: {exc}")
            )

# Merge after pool closes — sequential, no concurrent mutation of artifacts list
for artifact, error in raw_results:
    if artifact is not None:
        artifacts = [
            a for a in artifacts if a.capability_name != artifact.capability_name
        ]
        artifacts.append(artifact)
    if error is not None:
        errors.append(error)
```

Apply the same pattern with the same `_llm_lock` pattern to `skill.py` (using `_skill_gen_llm` / `_skill_llm_lock`) and `agent.py` (using `_agent_gen_llm` / `_agent_llm_lock`).

**New tests:**
- `test_parallel_generation_produces_all_artifacts()` — 3 gaps, mock `generate_single_tool`, all 3 artifacts in output.
- `test_parallel_generation_exception_recorded_as_error()` — one gap raises, other artifacts still produced, error in `state.errors`.
- `test_parallel_generation_no_duplicate_artifacts()` — two gaps produce same capability_name (via slug collision scenario), only one survives after merge.
- `test_already_passing_gaps_not_submitted_to_pool()` — pre-existing artifact with `gap_query` set, assert mock `generate_single_tool` not called for that gap.

---

## Pilot Infrastructure Verification

Before running the pilot, confirm two things in `tests/evaluation/harness.py` and `scripts/run_pilot.py`:

1. **Persistent DuckDB:** `MetricsStore` must be initialised with a file path, not `:memory:`. If in-memory, Fix 3's invocation records don't survive between tasks within a single pilot run (T07's matches never persist to inform T01's optimizer run). Check `HarnessConfig` for the `db_path` parameter.

2. **Sandbox image:** run `docker images jit-sandbox` and confirm the image was rebuilt after Fix 0. If the old image is still cached, `sandbox_execute` will still fail on pandas imports despite the Dockerfile change.

---

## Expected Runtime After All Fixes

| Phase | T07 Before | T07 After |
|-------|-----------|-----------|
| Decomposition | 30s → 14 caps | 30s → 1–2 caps |
| Gap detection + classification | 45s (14 caps) | ~10s (2 caps) |
| Generation | 5 min (11 sequential) | ~30s (2 parallel) |
| Verification | 28 min (14 × 2 min sequential) | 2–4 min (2 × 2 min parallel) |
| Retry probability | High (14 artifacts, 2+ likely fail) | Low (1–2 artifacts) |
| Optimizer spurious pruning (T01, T10) | +20 min | ~0 min |
| **Total per task** | **~80–100 min** | **~5–8 min** |
| **Total pilot (3 tasks)** | **2+ hours** | **~15–25 min** |

---

## Complete Test Summary

**707 tests currently passing. Expected breakage: zero.**

| Fix | Files changed | Schema changes | New tests | Breakage risk |
|-----|--------------|---------------|-----------|---------------|
| 0. Dockerfile + prompt | `sandbox/Dockerfile`, `mcp_tool_generator.py` | None | 1 integration | None |
| 1. Decomposition | `decomposition.py` (prompt + logic) | None | 3 unit | None |
| 2. Pin seeded caps | `store.py`, `operations.py` | None (uses existing `metadata`) | 3 unit | None |
| 3. Track matches | `gap_detection.py`, `pipeline.py` | None (optional param, `None` default) | 3 unit | None |
| 5. Stable naming | `utils.py`, `mcp_tool.py`, `skill.py`, `agent.py`, all 3 prompt files | None | 6 unit | None |
| 4. Skip on retry | `task.py`, `mcp_tool.py`, `skill.py`, `agent.py` | `gap_query: str\|None = None` on `GeneratedArtifact` | 4 unit | None |
| 6. Parallel verification | `verification_node.py` | None | 3 unit | Low (ordering) |
| 7. Parallel generation | `mcp_tool.py`, `skill.py`, `agent.py` | None | 4 unit | Low (ordering) |

**Total new tests: ~27**