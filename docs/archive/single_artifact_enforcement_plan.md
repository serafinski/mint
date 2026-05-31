# Task-Level Classification: One Classification Per Task

> **🗄️ ARCHIVED 2026-04-10 — SUPERSEDED by `docs/compositional_synthesis_plan.md` and Phase 2 of `docs/pre_eval_execution_plan.md` (commit `cab813a`).**
>
> This plan proposed collapsing per-gap classification to a single per-task classification ("one classification per task, one artifact per task") to align with the testbed's task-level ground truth. After implementation it was reverted in Phase 2 because:
>
> 1. The proposal explicitly describes per-gap classification (Pillar 1 §3): *"Each gap is classified to determine what kind of artifact should be generated"*. The deviation here was openly acknowledged as a deviation, but the eventual decision was to follow the proposal text rather than the testbed-fit shortcut.
> 2. Empirical finding on 2026-04-08 (T14 reproducer): the single-artifact pipeline produced **decorative registry matches** — capabilities matched and recorded as "invocations" but never actually executed. The metrics store had to write fake `(token_cost=0, latency_ms=0)` records to prevent matched capabilities from being pruned as idle, which corrupted the Pillar 1 reuse narrative.
> 3. Per-gap classification + multi-artifact generation + compositional execution (the path Phase 2 took, building on `docs/compositional_synthesis_plan.md`) restores both proposal-fidelity AND honest reuse metrics.
>
> **The Phase 2 commit (`cab813a`) reverses everything this plan proposed**:
> - Per-gap classifier (`classify_gap` iterating `state.gaps`) instead of single task-level classification
> - N enumerated `GapItem`s instead of one placeholder gap
> - Multi-artifact generation per task instead of single-artifact
> - Honest invocation records via Phase 1's removal of fake writes (commit `13b1ecd`)
>
> **Why kept (not deleted)**: Chapter 3 of the thesis will reference this plan for the methodology story — *"an earlier per-gap classifier was implemented but found to be misaligned with the testbed's measurement, then reverted to per-gap when the deviation's downstream cost (decorative matches, broken reuse metric) became visible"*. This is a real iterative-design narrative worth preserving as part of the implementation history.
>
> **Original document below preserved for historical context. The "Status: Planned" line is now wrong — this plan was implemented, ran for ~1 week, then reverted.**

---

**Date:** 2026-04-08
**Branch:** `iter/6-execution-backends` (or new branch)
**Status:** ARCHIVED 2026-04-10 — implemented circa 2026-04-08, reverted in Phase 2 (`cab813a`) on 2026-04-09/10. (Originally: Planned with critique-driven fixes appended)

---

## Relationship to the Thesis Proposal (Honest Acknowledgment)

The original thesis proposal (`docs/thesis_proposal.md`, Pillar 1) explicitly describes per-gap classification:

> "**Skill gap detection.** Each required capability is matched against a skill registry... **Gaps** are identified as subtasks with no match"
>
> "**Capability classification.** Each gap is classified to determine what kind of artifact should be generated"

This plan **deviates from the proposal's literal text** by switching to task-level classification. The deviation is justified by the testbed design: every task in `tests/evaluation/tasks.py` has exactly ONE target capability type. The per-gap algorithm described in the proposal cannot be meaningfully measured against single-type ground truth because:

1. Decomposition is non-deterministic — the same task may produce 1 gap on one run and 3 gaps on another
2. Per-gap classification accuracy can only be measured if there are per-gap ground truth labels (there aren't)
3. The proposal's research question is about classification accuracy, but the only thing the testbed can measure is task-level accuracy

**This deviation is documented openly.** Chapter 3 of the thesis must acknowledge that an earlier per-gap classifier was implemented but found to be misaligned with the testbed's measurement, and that task-level classification was adopted as the correct unit for the research question.

The proposal's other commitments are preserved:
- **Pillar 1 Step 1 (decomposition)**: Still produces subtasks and required_capabilities. Used for registry matching and as classifier context.
- **Pillar 1 Step 2 (gap detection)**: Still searches the registry. The per-capability matching is preserved (see Critique Fix #1 below).
- **Pillar 1 Step 4 (generation)**: Unchanged in concept; one artifact per task instead of N.
- **Pillar 1 Step 5 (sandboxed integration)**: Unchanged.
- **Pillar 2 (verification)**: Unchanged.
- **Pillar 3 (granularity optimizer)**: Unchanged.

---

## Problem

The pipeline currently classifies **each gap** independently. When decomposition over-splits (e.g., T16 → 3 gaps: "read-yaml", "analyze-metrics", "cascade-investigation"), the classifier produces 3 classifications, the pipeline generates 3 artifacts, verifies all 3, registers all 3, and executes only 1 via priority sort.

This is wasteful **and misaligned with the thesis research question**.

The proposal's Research Question 1:
> "How accurately can an LLM classify capability gaps into the three artifact types (MCP tool vs. Skill vs. Agent)?"

The testbed (`docs/system_overview.md`):
> "Each of the 18 tasks targets exactly ONE capability type (T04 = SKILL, T16 = AGENT, etc.)"

These two statements commit the thesis to **task-level classification with single-type ground truth**. The current per-gap classifier is answering the wrong question:
- The classifier says "gap_1 = mcp_tool, gap_2 = mcp_tool, gap_3 = agent" for T16
- The ground truth says "T16 = agent"
- The evaluation silently picks the executed artifact's type (via harness priority sort) and compares it to ground truth
- Per-gap classifications are decoration — they're never directly measured against anything meaningful

**Measured waste per multi-gap task**: ~6-10K tokens (3x classification + 3x generation + 3x verification) and ~60-120s verification overhead.

## Scope: Both Evaluation and CLI

This plan affects **both the evaluation harness and the MINT CLI**, because all changes are in `src/orchestrator/` (the shared pipeline). Specifically:

- **Evaluation harness** (`tests/evaluation/harness.py` → invokes `pipeline.invoke(...)`) — gets task-level classification automatically
- **CLI** (`src/cli/runner.py` → invokes `pipeline.invoke(...)`) — gets task-level classification automatically

Both consumers of the pipeline see the same behavior change: one classified gap, one generated artifact, predictable execution. No separate CLI work is needed beyond what the pipeline change provides.

**Implication for the user-facing CLI**: end users will see exactly one capability generated per `mint run` invocation, with a clear type (MCP_TOOL, SKILL, or AGENT) reported in the status. No more "generated 3 artifacts but executed 1" confusion.

## Approach: Task-Level Classification

Replace per-gap classification with **one classification per task**. The classifier takes the full task description (plus the required_capabilities list as context) and returns a single `ClassifiedGap` representing the task's primary capability type.

### Why this is better than gap selection

| | Per-Gap + Selector (alternative) | **Task-Level (this plan)** |
|---|---|---|
| LLM calls per multi-gap task | N (classifier) + 1 (selector) | **1** (task classifier) |
| Matches research question | Indirectly (selector picks primary) | **Directly** (classifier picks type) |
| Matches testbed ground truth | Via selector's choice | **Via classifier's choice** |
| Chapter 3 explanation | Two-step: classify then select | **One-step: classify task** |
| Pipeline nodes added | 1 (gap_selection_node) | **0** (modified existing classifier_node) |
| Classifier prompt rewrite | No | **Yes** (major) |
| Test updates | Add selector tests | Rewrite classifier tests |
| Token cost per multi-gap task | ~500-800 (selector) + ~N × 200 (classifier) | **~400-600** (one classification) |

**Task-level classification is the architecturally cleaner choice.** It directly aligns with the thesis's research question and testbed design. The per-gap approach was vestigial — the pipeline was classifying pieces that were never meaningfully used.

## What Changes

### 1. Classifier becomes task-level

The classifier stops receiving individual gaps and starts receiving the **whole task**. Input:
- Full task description (from `state.task_description`)
- Required capabilities list (from `state.required_capabilities`, as context)
- Registry matches (from `state.matches`, to avoid re-classifying already-covered capabilities)

Output:
- One `ClassifiedGap` representing the task's primary capability type
- The gap's `description` field becomes a synthesized task-level capability description
- The gap's `query` field becomes a deterministic slug derived from the task description

### 2. Gap detection preserves per-capability matching, signals task-level need

`gap_detection_node` keeps its full per-capability matching behavior (this is critical for Pillar 1 reuse — see Critique Fix #1 below):
- It still iterates `state.required_capabilities` and matches each against the registry
- It still records each match as an `InvocationRecord` for the optimizer
- It still populates `state.matches` with all individual matches

The only change: when ANY required_capability is unmatched, `gap_detection_node` produces a **single placeholder gap** representing "this task needs generation" rather than N specific gaps. This placeholder triggers the task-level classifier downstream.

If ALL required_capabilities are matched in the registry, `state.gaps` is empty, the classifier is a no-op, and the harness's existing registry match path executes the matched capabilities directly (unchanged behavior).

### 3. Generators receive one classified gap

Each generator filters `state.classified_gaps` for its type. With task-level classification, `classified_gaps` has at most 1 entry. At most one generator produces an artifact. No waste.

### 4. Generator prompts MUST be updated (not optional)

The current generator prompts take a gap description + task context as separate fields. With task-level classification, both fields will contain the same content (the task description). This redundancy can confuse the LLM.

**Required prompt updates** (concrete, not optional):
- `src/orchestrator/prompts/mcp_tool_generator.py`: clarify that `{description}` now describes the whole task and `{task_context}` is redundant. Either remove `{task_context}` or explicitly note "this is the same as the description."
- `src/orchestrator/prompts/skill_generator.py`: same fix. The skill generator should produce a SKILL.md that solves the entire task, not a sub-step.
- `src/orchestrator/prompts/agent_generator.py`: review for any sub-task framing. The agent generator already operates at task level (generates full agent system prompts).

**Validation gate**: before declaring the implementation done, run a side-by-side comparison on T04 (SKILL), T01 (MCP_TOOL), and T16 (AGENT). Inspect generated artifacts for quality regressions vs. the current per-gap output.

## What Does NOT Change

- **Decomposition unchanged**: still produces subtasks and required_capabilities. These become **contextual input** to the task-level classifier rather than the unit of classification.
- **Gap detection's registry-match role unchanged**: still searches the registry for existing capabilities.
- **Generators unchanged** (except prompt review): each still filters by type and generates one artifact.
- **Verification unchanged**: still runs Tier 1-3 on generated artifacts. With at most 1 artifact, no waste.
- **Optimizer unchanged**: Pillar 3 still operates at the registry level across tasks.
- **Execution backends unchanged**: MCP tool / skill / agent execution paths still work as before.
- **`SubTask.depends_on` unchanged**: remains unused for execution; preserved for Chapter 7 future work on compositional synthesis.

## Files Affected

| File | Change |
|---|---|
| `src/orchestrator/prompts/classifier.py` | **Major rewrite**. Change from "classify this gap" to "classify this task". New few-shot examples at task level. Remove the `{description}` vs `{query}` distinction. |
| `src/orchestrator/classifier.py` | Rewrite `classifier_node` to classify the whole task once. Remove the per-gap loop. Keep the LLM-based classification logic (`classify_gap` → `classify_task`). |
| `src/orchestrator/gap_detection.py` | **Per-capability registry matching loop is preserved** (still iterates `required_capabilities` and populates `state.matches`). Only the gap output format changes: instead of producing N enumerated `GapItem` objects (one per unmatched capability), produces a single placeholder `GapItem` if any capability is unmatched. |
| `src/orchestrator/prompts/mcp_tool_generator.py` | Resolve `{description}` vs `{task_context}` redundancy (both will contain the task description). Either remove one or note the duplication. |
| `src/orchestrator/prompts/skill_generator.py` | Same fix. Ensure prompt instructs the SKILL.md to solve the entire task. |
| `src/orchestrator/prompts/agent_generator.py` | Review for sub-task framing (mostly task-level already). |
| `src/schemas/task.py` | `ClassifiedGap` semantics clarified: now represents a task-level classification. No schema change required. (`ClassificationResult` already exists — reused, not duplicated.) |
| `src/schemas/evaluation.py` | Add `required_capability_count: int` and `matched_in_registry_count: int` for thesis analysis. Wired up in `tests/evaluation/harness.py` `_extract_result()`. |
| `tests/evaluation/harness.py` | `_extract_result()` populates the new evaluation fields from `state.required_capabilities` and `state.matches`. **Note**: `_extract_result()` currently only reads `matches` from the final state — it must be updated to also read `required_capabilities = _get("required_capabilities", []) or []` at the top, then add the two new fields (`required_capability_count=len(required_capabilities)`, `matched_in_registry_count=len(matches)`) when constructing the `EvaluationRecord`. |
| `src/orchestrator/pipeline.py` | No structural change — same nodes, same graph. |
| `src/cli/runner.py` | No code change required, but note a minor cosmetic improvement: the classifier display branch at `src/cli/runner.py:873-876` currently joins multiple classified types with `", ".join(types)`. Under task-level classification, `classified_gaps` will have at most 1 entry, so this automatically produces a single clean type string instead of a comma-separated list. Still works correctly (no crash), just cleaner output. |
| `tests/unit/test_classifier.py` | **Rewrite** all 21 existing tests for task-level classification. |
| `tests/unit/test_gap_detection.py` | Update 12 existing tests for new gap output format (placeholder gap when any unmatched). |
| `tests/unit/test_pipeline.py` | Add assertions that classifier produces at most one classified gap. |
| `tests/integration/test_classifier.py` | Rewrite integration tests to use task descriptions. |
| `CLAUDE.md` | Document task-level classification in the pipeline section. Remove references to per-gap classification. |

## Implementation Details

### New Classifier Prompt

**Location**: `src/orchestrator/prompts/classifier.py`

```python
"""Prompt for the task-level capability classifier (LLM-based, temperature=0).

Classification is based on three architectural layers (industry consensus):
- MCP Tool = execution layer (atomic action, stateless, deterministic)
- Skill = knowledge layer (procedural expertise, prescribed workflow)
- Agent = reasoning layer (autonomous, adaptive, unpredictable path)

The classifier decides the PRIMARY capability type for an entire task,
not for individual decomposition steps. This matches the testbed's
single-type-per-task ground truth labels.

References: MCP spec (modelcontextprotocol.io), Agent Skills spec
(agentskills.io), Anthropic "Building Effective Agents", LangGraph docs.
"""

TASK_CLASSIFICATION_PROMPT = """\
You are a capability classifier for a JIT synthesis pipeline. Your job is to \
classify an entire task into ONE architectural layer based on what kind of \
capability best solves the task end-to-end.

## The Three Layers

These are complementary architectural layers, not points on a complexity axis. \
Each solves a fundamentally different problem.

### mcp_tool — The Execution Layer
An MCP tool is a **single, stateless, well-defined operation** with clear \
inputs and outputs. It performs an action and returns a result.

The key test: **can the entire task be solved by a single deterministic \
function?** Even if that function internally reads files, joins data, and \
computes statistics — if the procedure is deterministic and self-contained, \
the task is an MCP tool.

Examples: parsing a CSV, computing a hash, validating a schema, joining two \
datasets, deduplicating records via hashing, fuzzy-matching strings.

### skill — The Knowledge Layer
A skill is **packaged procedural knowledge** that teaches an agent how to \
approach a domain-specific task. It encodes a prescribed, multi-step workflow \
with domain expertise, validation steps, and guardrails.

The key test: **does the task require a prescribed workflow with domain \
expertise where the steps are known but non-trivial?** If an expert would \
write a standard operating procedure for the task — and that procedure \
involves applying domain rules, validating intermediate results, or \
integrating multiple data sources — it is a skill.

Examples: inventory reorder pipeline (check stock, apply reorder rules, \
compute costs with caps), configurable data quality pipeline (apply rules \
from config to clean a dataset), multi-source revenue integration (join \
sources, aggregate, compare against targets).

### agent — The Reasoning Layer
An agent is an **autonomous entity that dynamically directs its own process**. \
It makes decisions, adjusts strategy based on intermediate findings, and \
pursues open-ended goals where the path to completion is unpredictable.

The key test: **is the path to completion unpredictable, requiring adaptive \
reasoning?** The task cannot be reduced to a prescribed sequence of steps \
because intermediate results change what should happen next.

Examples: root cause diagnosis (trace cascading failures, adapt strategy as \
evidence accumulates), anomaly investigation (profile data, decide what to \
investigate based on what anomalies appear), fraud detection (cross-reference \
patterns, follow leads).

## Decision Heuristics (apply in order)

1. **Is the task a single deterministic computation?** → mcp_tool
   Even complex computations (statistical analysis, fuzzy joins, multi-file \
   parsing) are mcp_tools if the full procedure can be specified in advance \
   and implemented as one function.

2. **Is there a known, prescribable workflow with domain rules?** → skill
   If the task has a defined sequence of steps with validation rules, \
   thresholds, or configurable parameters, and the "how to approach it" \
   knowledge is domain-specific — it is a skill.

3. **Is the goal open-ended with an unpredictable path?** → agent
   If the task requires exploration, hypothesis formation, or strategy \
   adjustment based on intermediate findings — it is an agent.

## Important Distinctions

The domain vocabulary (e.g., "JSON", "CSV", "data", "pipeline") does NOT \
determine the type. What matters is the nature of the task:

- Multi-step ≠ skill. A task with multiple internal steps can still be an \
  mcp_tool if those steps are deterministic and self-contained.
- Complex ≠ agent. A complex task with a known workflow is a skill, not an \
  agent. Agents are reserved for genuinely adaptive reasoning.
- "Workflow" language ≠ skill. Many tasks describe a workflow but can be \
  implemented as a single function.

## Decomposition Context

The task decomposition produced these required capabilities as guidance:
{required_capabilities}

This is contextual information about what the decomposer thought was needed. \
It is **not a routing rule**. Classify the task based on what single \
capability type best solves it end-to-end, even if the decomposer suggested \
multiple sub-steps (sub-steps are implementation details of one capability).

## Few-Shot Examples

Task: "Parse a CSV file and return row count and column names."
Answer: classified_type=mcp_tool, confidence=0.95, rationale="Single \
deterministic computation — parse file, extract metadata, return result."

Task: "Clean a dirty CSV by removing duplicates and rows with null values."
Answer: classified_type=mcp_tool, confidence=0.9, rationale="Deterministic \
data cleaning operation — filter and dedupe in a single pass."

Task: "Build an inventory reorder pipeline using inventory CSV and reorder \
rules YAML. Compute days_of_stock, identify products below threshold, \
compute reorder quantities capped at max_order_value, sum total cost."
Answer: classified_type=skill, confidence=0.9, rationale="Prescribed domain \
workflow with reorder rules, thresholds, and capping logic. The 'how to \
approach inventory reorder' knowledge is the value."

Task: "Build a data integration pipeline joining sales CSV with product \
Excel and regional targets. Compute per-region revenue and compare against \
targets."
Answer: classified_type=skill, confidence=0.9, rationale="Multi-source \
integration following a known procedure: join, aggregate, compare. Domain \
knowledge of how to combine these sources is prescriptive."

Task: "Investigate a service degradation incident. Examine metrics around \
the onset time, identify which metric changed first (root cause), and \
determine the cascade chain by comparing onset times across metrics. Requires \
adaptive investigation."
Answer: classified_type=agent, confidence=0.95, rationale="Open-ended \
investigation — the path depends on what anomalies are found. Cannot be \
prescribed because hypothesis testing drives the next steps."

Task: "Autonomously profile a messy dataset, identify data quality issues, \
investigate clusters and correlations, and report findings."
Answer: classified_type=agent, confidence=0.9, rationale="Adaptive \
investigation where the analysis path depends on what the profiling reveals."

## Task to Classify

Full task description:
{task_description}

Classify this task. Respond with the type, your confidence (0-1), and a \
brief rationale explaining why this type fits based on the architectural \
layer the task requires.\
"""
```

### Classifier Node Rewrite

**Location**: `src/orchestrator/classifier.py`

```python
"""Task-level capability classifier (LLM-based, temperature=0).

Changed from per-gap classification to task-level classification. Takes the
full task description (plus required_capabilities as context) and returns
ONE classification for the task. This matches the testbed's single-type
ground truth and eliminates per-gap classification waste.
"""

from __future__ import annotations

import logging
from typing import Literal

from langgraph.types import Command

from src.orchestrator.prompts.classifier import TASK_CLASSIFICATION_PROMPT
from src.orchestrator.tracing import emit_langfuse_event
from src.schemas.capability import CapabilityType
# ClassificationResult already exists in src/schemas/task.py — reuse it
from src.schemas.task import ClassificationResult, ClassifiedGap, TaskState, TaskStatus

logger = logging.getLogger(__name__)

# Shared LLM instance (lazy init — same pattern as current classifier.py)
_classifier_llm = None


def _get_llm():
    """Lazily create the classifier LLM (same pattern as current code)."""
    global _classifier_llm  # noqa: PLW0603
    if _classifier_llm is None:
        from langchain.chat_models import init_chat_model

        from src.config import DEFAULT_LLM_MODEL

        # Preserve temperature=0 and request_timeout=300 from current code
        _classifier_llm = init_chat_model(
            DEFAULT_LLM_MODEL, temperature=0, request_timeout=300
        )
    return _classifier_llm


def classify_task(
    task_description: str,
    required_capabilities: list[str],
) -> ClassifiedGap:
    """Classify an entire task into one capability type.

    Returns a ClassifiedGap with the task-level classification result.
    On LLM failure, returns a fallback classification matching the current
    classifier's convention: mcp_tool with confidence 0.3 and the marker
    "LLM classification failed" in the rationale (so classifier_node can
    detect the fallback and surface it on state.errors).
    """
    llm = _get_llm()
    structured_llm = llm.with_structured_output(ClassificationResult)

    caps_text = (
        ", ".join(required_capabilities)
        if required_capabilities
        else "(none identified)"
    )

    prompt = TASK_CLASSIFICATION_PROMPT.format(
        task_description=task_description[:1500],
        required_capabilities=caps_text,  # injected into {required_capabilities} placeholder
    )

    try:
        result: ClassificationResult = structured_llm.invoke(prompt)
        return ClassifiedGap(
            description=task_description[:500],
            query=_slug_from_task(task_description),
            classified_type=result.classified_type,
            confidence=result.confidence,
            rationale=result.rationale,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Task classification LLM call failed: %s", exc)
        emit_langfuse_event(
            None,
            "classifier.llm_failure",
            "ERROR",
            {"task_description": task_description[:200], "error": str(exc)},
        )
        return ClassifiedGap(
            description=task_description[:500],
            query=_slug_from_task(task_description),
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.3,
            rationale=f"LLM classification failed ({exc}); defaulting to MCP_TOOL",
        )


def _slug_from_task(task_description: str) -> str:
    """Derive a deterministic slug from the task description.

    Uses the first sentence (up to 40 chars) plus a short SHA-256 hash
    suffix to ensure deterministic naming across runs.
    """
    import hashlib

    first_sentence = task_description.split(".")[0].strip()[:40]
    slug_base = "-".join(first_sentence.lower().split())
    # Keep only alphanumerics and hyphens
    slug_base = "".join(c if c.isalnum() or c == "-" else "" for c in slug_base)
    slug_base = slug_base.strip("-")[:40] or "task"
    hash_suffix = hashlib.sha256(task_description.encode()).hexdigest()[:6]
    return f"{slug_base}-{hash_suffix}"


def classifier_node(
    state: TaskState,
) -> Command[Literal["generate_mcp_tool"]]:
    """Classify the task into one capability type.

    Skips classification if all required capabilities are already matched
    in the registry (state.gaps is empty). Otherwise, produces exactly one
    ClassifiedGap representing the task-level classification.
    """
    if not state.gaps:
        # All capabilities matched in registry — nothing to classify.
        logger.info("Classifier: no gaps (all matched in registry), skipping")
        return Command(
            update={"status": TaskStatus.CLASSIFYING},
            goto="generate_mcp_tool",
        )

    logger.info(
        "Classifier: task-level classification (%d required capabilities, %d gaps)",
        len(state.required_capabilities),
        len(state.gaps),
    )

    classified = classify_task(
        task_description=state.task_description,
        required_capabilities=state.required_capabilities,
    )

    logger.info(
        "Classifier: task classified as %s (confidence=%.2f)",
        classified.classified_type.value,
        classified.confidence,
    )
    emit_langfuse_event(
        None,
        "classifier.task_classified",
        "DEFAULT",
        {
            "classified_type": classified.classified_type.value,
            "confidence": classified.confidence,
            "n_required_capabilities": len(state.required_capabilities),
            "n_gaps_before": len(state.gaps),
        },
    )

    errors = list(state.errors)
    # Match the existing convention: "LLM classification failed" marker in rationale
    if "LLM classification failed" in classified.rationale:
        errors.append(f"Classification fallback: {classified.rationale}")

    return Command(
        update={
            "classified_gaps": [classified],
            "status": TaskStatus.CLASSIFYING,
            "errors": errors,
        },
        goto="generate_mcp_tool",
    )
```

### Gap Detection Update

**Location**: `src/orchestrator/gap_detection.py`

The per-capability registry matching loop is preserved (still iterates `required_capabilities`, populates `state.matches`, records InvocationRecords for the optimizer). The only change is the gap output format: instead of N enumerated `GapItem` objects, produce a single placeholder `GapItem` if any capability is unmatched.

```python
def gap_detection_node(
    state: TaskState,
    registry: SkillRegistry,
    metrics_store: MetricsStore | None = None,
) -> Command[Literal["classifier_node"]]:
    """Search registry for matches per required_capability; produce a single task-level gap if any are unmatched.

    Per-capability registry matching is preserved: still iterates
    state.required_capabilities, populates state.matches with each match,
    and records InvocationRecords for the optimizer.

    The only change vs. the previous version is the gap output format:
    instead of N enumerated GapItem objects (one per unmatched capability),
    produce a single placeholder GapItem if ANY capability is unmatched.
    The task-level classifier downstream will decide the type for the
    whole task.
    """
    errors: list[str] = list(state.errors)

    matches_raw, gaps_raw = registry.detect_gaps(state.required_capabilities)

    matches = [
        MatchItem(capability=cap, score=score)
        for cap, score, _query in matches_raw
    ]

    # Record matches as invocations (unchanged)
    if metrics_store is not None:
        for m in matches:
            metrics_store.record_invocation(InvocationRecord(
                capability_id=m.capability.name,
                task_id=hashlib.sha256(state.task_description.encode()).hexdigest()[:16],
                success=True,
                token_cost=0,
                latency_ms=0.0,
            ))

    # Produce a single placeholder gap if any capabilities were unmatched
    gaps: list[GapItem] = []
    if gaps_raw:
        # At least one capability needs generation — produce one task-level gap
        gaps = [
            GapItem(
                description=state.task_description[:300],
                query="task-level",  # Placeholder; classifier will generate real slug
            )
        ]

    logger.info(
        "Gap detection: %d matches, %d unmatched capabilities → %d gap(s) for classification",
        len(matches),
        len(gaps_raw),
        len(gaps),
    )
    for m in matches:
        logger.info("  ✓ matched: %s (score=%.2f)", m.capability.name, m.score)
    if gaps:
        logger.info("  → task requires generation (%d unmatched capabilities)", len(gaps_raw))

    return Command(
        update={
            "matches": matches,
            "gaps": gaps,
            "status": TaskStatus.DETECTING_GAPS,
            "errors": errors,
        },
        goto="classifier_node",
    )
```

### Schema Additions

**Location**: `src/schemas/evaluation.py`

```python
class EvaluationRecord(BaseModel):
    # ... existing fields ...

    required_capability_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of required_capabilities produced by decomposition. "
            "Useful for analyzing decomposition splitting behavior."
        ),
    )
    matched_in_registry_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of required_capabilities matched in the registry. "
            "If equal to required_capability_count, classification was skipped."
        ),
    )
```

**Wiring**: `tests/evaluation/harness.py` `_extract_result()` reads from the final `TaskState`:
- `required_capability_count = len(state.required_capabilities)`
- `matched_in_registry_count = len(state.matches)`

These fields populate every `EvaluationRecord` written to `data/eval/metrics.duckdb`. They are not used by the pipeline itself — only for Chapter 6 analysis ("how often does decomposition over-split?", "how often is the registry hit?").

### Generator Prompt Updates (Required)

After task-level classification, the generators receive a task description (via `classified_gaps[0].description`) rather than a fragment description. The current generator prompts have a `{description}` field (per-gap) and a `{task_context}` field (task-level) — which under task-level classification will contain the same content. This redundancy can confuse the LLM.

**Required updates to each generator prompt**:

1. **MCP tool generator** (`src/orchestrator/prompts/mcp_tool_generator.py`):
   - Either remove `{task_context}` from the prompt template (since it duplicates `{description}`)
   - OR clarify in the prompt: "The task description below is the full task — generate a single MCP tool that solves it end-to-end."
   - Update the few-shot examples if any reference "this gap" or "this sub-step" framing.

2. **Skill generator** (`src/orchestrator/prompts/skill_generator.py`):
   - Same redundancy fix.
   - Ensure the prompt instructs: "Generate a SKILL.md that teaches an agent to solve the entire task, not a single step."

3. **Agent generator** (`src/orchestrator/prompts/agent_generator.py`):
   - Already operates at task level (generates full agent system prompts). Review for any sub-task framing in few-shot examples.

**Validation gate** (mandatory before merging):
- Run T01 (MCP_TOOL), T04 (SKILL), and T16 (AGENT) through the new pipeline
- Inspect generated artifacts side-by-side against current per-gap output
- If ANY artifact quality regression is observed, fix the relevant generator prompt before declaring done

**Note**: This is the same fix described in "What Changes" Section 4. Restated here for implementation specificity.

## Pipeline Graph

**No structural changes**. Same nodes, same edges. Only the node behavior changes:

```
START → decomposition → gap_detection → classifier_node
     → generate_mcp_tool → generate_skill → generate_agent
     → verification_node → loading_node → [optimizer] → END
```

The difference: `gap_detection` now produces 0 or 1 gaps (not N), and `classifier_node` produces 0 or 1 classified_gaps (not N). Everything downstream is unchanged.

## Edge Cases

### Edge Case 1: All capabilities matched in registry
`state.gaps` is empty. `classifier_node` is a no-op. No LLM call. Pipeline proceeds to generators (which produce nothing). Loading node registers nothing new. Same as current "all matched" behavior.

### Edge Case 2: No decomposition output (decomposition timeout)
Decomposition returns empty `required_capabilities`. The fallback in `decomposition_node` uses the first sentence of the task description as a single capability. Classifier receives the task description directly. Proceeds normally.

### Edge Case 3: Classifier LLM failure
`classify_task` catches all exceptions and returns a fallback `ClassifiedGap` with `classified_type=mcp_tool` and `confidence=0.3` (matching the existing classifier convention). The rationale includes the marker "LLM classification failed" so `classifier_node` can detect the fallback and surface it on `state.errors`. Pipeline continues.

### Edge Case 4: Decomposition produces many required_capabilities but classifier still picks one
This is the entire point. The classifier ignores the splitting and returns one type. Downstream generators produce exactly one artifact.

### Edge Case 5: Registry match for SOME capabilities, generation for OTHERS

`gap_detection_node` populates `state.matches` for the matched capabilities (unchanged from current behavior). It also produces a single placeholder gap because at least one capability is unmatched. The task-level classifier then classifies the entire task and generation produces ONE new artifact.

**Important nuance**: this means the task-level generated artifact may duplicate functionality of the matched registry capabilities. For example, if the registry has `csv-parser` and the task needs CSV parsing + ML training, the task-level artifact might re-implement CSV parsing internally instead of calling the registered tool.

**Why we accept this**:
1. The testbed's success criterion is "task completes correctly with the right output", not "all matched capabilities are reused"
2. The 18-task testbed mostly has tasks that fully match or fully need generation; partial matches are rare
3. Pillar 3's prune operation will eventually deactivate any capabilities that don't get reused
4. Building artifact composition (calling matched capabilities from inside generated code) is Chapter 7 future work

**The matched capabilities are still in the registry for future tasks** that semantically search for them. They just aren't executed inline as part of the partial-match task.

### Edge Case 6: Classifier confidence is low (< 0.5)
Logged but not acted on. The pipeline trusts the classifier's decision. Low confidence is reported in the evaluation record for analysis.

## Critique Fixes Applied

This plan was critiqued and the following fixes were incorporated:

### Fix #1: Registry matches stay executable

**Original critique**: The earlier draft said "registry matches become informational context." This would have broken Pillar 1's reuse value proposition — the harness would generate new artifacts even when matched capabilities exist.

**Fix**: `gap_detection_node` keeps its full per-capability matching behavior. `state.matches` is populated as before. The harness's existing registry-match execution path (`_try_registry_match` in CLI, `_extract_result` matched-capabilities branch in harness) is unchanged. The ONLY difference: when ANY required_capability is unmatched, instead of producing N specific gaps, gap_detection produces 1 placeholder gap that triggers task-level classification for the missing piece(s).

**Net result**: registry matches still execute directly. Task-level classification only kicks in when generation is genuinely needed. The "all matched" path is unchanged.

### Fix #2: Generator prompts must be updated (not handwaved)

**Original critique**: The plan said "review generator prompts" but didn't commit to changes. Generator prompts currently expect gap-focused descriptions; feeding them task descriptions could produce worse output.

**Fix**: Concrete prompt updates are now part of the implementation (not optional):
- `mcp_tool_generator.py`: clarify `{description}` vs `{task_context}` (will be the same content; either consolidate or make the duplication explicit)
- `skill_generator.py`: ensure SKILL.md is generated for the whole task, not a sub-step
- `agent_generator.py`: review (mostly task-level already)

**Validation gate**: side-by-side comparison on T01, T04, T16 before declaring the implementation complete.

### Fix #3: Decomposition is acknowledged as advisory

**Original critique**: Decomposition becomes vestigial — it costs ~3-10K tokens per task to produce a list that the classifier mostly ignores.

**Fix**: Acknowledged honestly. Decomposition's role is now:
1. Produce `required_capabilities` for gap_detection's registry matching (still essential)
2. Provide capability list as classifier context (advisory only)

We accept the decomposition cost because it enables registry reuse. Removing decomposition entirely would break Pillar 1 Step 2. Future work (Chapter 7) could explore lightweight decomposition or skipping it for tasks where the classifier is highly confident.

### Fix #4: Test rewrite scope is days, not hours

**Original critique**: The plan said "rewrite test_classifier.py" but underestimated the scope.

**Fix**: Realistic estimate documented (verified test counts):
- `test_classifier.py`: ~2-4 days (21 tests, all need rewriting for task-level inputs)
- `test_gap_detection.py`: ~half day (12 tests, update for placeholder gap output)
- `test_pipeline.py`: ~half day (verify single-classification flow)
- `tests/integration/test_classifier.py`: ~half-1 day (integration tests with real LLM)
- **Total**: ~3-6 days of test work

The implementation cannot ship without this. Plan accordingly.

### Fix #5: Proposal deviation is documented prominently

**Original critique**: The plan should not pretend it matches the proposal when it doesn't.

**Fix**: New "Relationship to the Thesis Proposal (Honest Acknowledgment)" section at the top of this document. The deviation is named, justified, and the chapters that need rewriting are listed.

### Fix #6: gap_detection_node is NOT vestigial

**Original critique**: Earlier draft suggested gap_detection becomes a degenerate "did we match everything?" check.

**Fix**: gap_detection_node's per-capability matching is preserved. It still does the registry matching work for every entry in `required_capabilities`, populates `state.matches` per match, and records invocation metrics for the optimizer. The only change is the "we need generation" signal moves from "N enumerated gaps" to "1 placeholder gap." Everything else gap_detection does remains intact.

### What was NOT fixed (intentional limitations)

- **Decomposition still over-splits, and decomposition tokens are still wasted on the over-split output**: We don't add a retry-with-hint to decomposition. Decomposition produces whatever it produces, and the cost (~3-10K tokens per task) is the same regardless of whether it over-splits. What we DO eliminate is the downstream waste — even when decomposition produces 3 capabilities, we now run only 1 classification + 1 generation + 1 verification instead of 3 each. The decomposition tokens themselves are not saved.
- **Per-gap rationales are lost**: Each individual gap's classification rationale is not produced. The task-level classifier produces ONE rationale for the whole task. This is a deliberate trade-off — the proposal's per-gap rationales were never directly measured against any ground truth.

## Risk Analysis

| Risk | Mitigation |
|---|---|
| Classifier rewrite breaks existing tests | Tests need rewriting; ~5-7 days budgeted |
| Task-level classification is less accurate than per-gap | Few-shot examples at task level; T01/T04/T16 validation gate before ship |
| Generators produce worse code with task-level input | Concrete prompt updates required (Fix #2); side-by-side comparison gate |
| Decomposition becomes advisory only | Acknowledged in Fix #3. Decomposition's primary role becomes feeding gap_detection's registry matching, not driving classification. |
| Registry matches stop being executable | **Resolved by Fix #1**: matches still execute via the harness's existing path. Only generation is task-level. |
| Per-gap classification rationales are lost | Acknowledged. Task-level rationale replaces them. Proposal's per-gap rationales were never directly measured. |
| Low-confidence classifications still proceed | Logged in evaluation records; reported in Chapter 6 as a quality metric |
| Proposal deviation may concern advisor | Documented openly in "Relationship to Thesis Proposal" section. Chapter 3 acknowledges the change explicitly. |

## Verification Plan

**Test scope is significant** (see Fix #4): ~3-6 days of test work, not minor updates.

1. **Unit tests — `test_classifier.py` (major rewrite, ~2-4 days):**
   - 21 existing tests need to be rewritten for task-level inputs
   - Task-level classification with clear MCP_TOOL task
   - Task-level classification with clear SKILL task
   - Task-level classification with clear AGENT task
   - Empty gaps (registry match) → no-op
   - LLM failure → fallback classification with "LLM classification failed" marker
   - Deterministic slug generation
   - Few-shot example coverage (one test per few-shot scenario)

2. **Unit tests — `test_gap_detection.py` (~half day):**
   - 12 existing tests need updates
   - Per-capability matching loop still populates `state.matches` for each match
   - Single placeholder gap produced when ANY capability is unmatched
   - Empty gap list when all capabilities are matched
   - Multiple matches still recorded as InvocationRecords for the optimizer

3. **Unit tests — `test_pipeline.py` (~half day):**
   - Pipeline produces at most 1 classified gap
   - Single-classification flow routes correctly to generators

4. **Integration tests (~1 day):**
   - Rewrite `tests/integration/test_classifier.py` for task-level inputs
   - Verify real LLM calls produce expected types for known tasks

5. **Live integration test with real LLM:**
   - Run T04 (SKILL target) — verify classification = SKILL
   - Run T16 (AGENT target) — verify classification = AGENT
   - Run T01 (MCP_TOOL target) — verify classification = MCP_TOOL

6. **Pilot spot-check:**
   - Run `T01,T04,T16` through the full pipeline
   - Confirm each produces exactly 1 artifact of the expected type
   - Check LangFuse traces for `classifier.task_classified` events

7. **Generator quality validation gate (mandatory):**
   - Run `T01,T04,T16` and inspect generated artifacts side-by-side against current per-gap output
   - If ANY artifact quality regression is observed, fix the relevant generator prompt before declaring done

8. **Full pilot comparison (before/after):**
   - Run pilot `T04,T11,T14,T16,T17` with per-gap classifier (current)
   - Run same tasks with task-level classifier (new)
   - Compare: artifact counts, token costs, classification accuracy, execution success rates

9. **All 814 unit tests must pass** (after rewrites).

## Thesis Implications

### Chapter 3 (JIT Capability Pipeline)

Rewrite Section 3.2 (Capability Classification Framework) to describe task-level classification:

> The classifier receives the full task description and any decomposition-produced required_capabilities as context, and returns a single classification for the task. This ensures that each task produces exactly one artifact of one type, matching the testbed's single-type-per-task ground truth.
>
> We evaluated per-gap classification during an earlier iteration and found it answered the wrong question: per-gap classifications were individually correct but did not aggregate to a meaningful task-level decision. Task-level classification directly answers Research Question 1 ("How accurately can an LLM classify capability gaps into the three artifact types?") by classifying the unit that the testbed measures (the task), not decomposition sub-steps.

### Chapter 6 (Evaluation)

Classification accuracy becomes directly measurable:

> For each task, we compute `classification_correct = (classified_type == task.target_type)`. Classification accuracy per condition is the percentage of tasks where this holds. Under task-level classification, classification accuracy is a single scalar per task rather than an aggregation over per-gap decisions.

Add reporting:
- Overall classification accuracy (% correct across 360 records)
- Per-type accuracy (MCP_TOOL vs SKILL vs AGENT)
- Confusion matrix (which types get confused for which)
- Correlation of classification accuracy with task success rate

### Chapter 7 (Future Work)

Strong positioning for compositional synthesis as future work:

> The current pipeline enforces single-artifact-per-task via task-level classification. A natural extension is compositional capability synthesis, in which a task is decomposed into multiple sub-capabilities executed in dependency order. This would require:
> - A new per-subtask classifier (recovering the per-gap classification we removed)
> - An execution orchestrator that runs artifacts in dependency order (`SubTask.depends_on` already exists in the schema for this purpose)
> - Output passing between artifacts (serialization, type coercion)
> - A testbed redesign to accommodate multi-type ground truth labels
> - A new research question: "how well does the system compose multiple artifacts into end-to-end workflows?"

## Cost Analysis

The cost comparison below includes ALL pipeline stages so the absolute numbers are realistic. Decomposition runs identically in both approaches and is shown for completeness.

**Per multi-gap task (current per-gap classification):**

| Stage | Tokens | Sandbox time |
|---|---|---|
| Decomposition (DeepAgent) | ~3-10K | — |
| Classification (× N gaps) | ~200 × N ≈ 400-800 | — |
| Generation (× N gaps) | ~3K × N ≈ 6-12K | — |
| Verification (× N artifacts) | ~500 each | ~60s × N ≈ 120-240s |
| **Total** | **~10-23K tokens** | **~120-240s sandbox** |

**Per multi-gap task (task-level classification):**

| Stage | Tokens | Sandbox time |
|---|---|---|
| Decomposition (DeepAgent) | ~3-10K (unchanged) | — |
| Classification (× 1 task) | ~400-600 | — |
| Generation (× 1 artifact) | ~3K | — |
| Verification (× 1 artifact) | ~500 | ~60s |
| **Total** | **~7-14K tokens** | **~60s sandbox** |

**Savings per multi-gap task**: ~3-9K tokens + 60-180s sandbox time.

**Note on decomposition cost**: Decomposition is unchanged and still costs ~3-10K tokens per task even if it over-splits. We don't reclaim those tokens. The savings come from eliminating wasted classification, generation, and verification on non-primary gaps.

**Over 360-record evaluation** (assuming ~20% multi-gap rate): ~72 tasks × 6K saved = **~432K tokens** + **~72-216 minutes** of sandbox time. The decomposition share (~720K-2.4M tokens) is unchanged.

**Additional cost**: Zero. The task-level classifier costs slightly more per call (more context) but is called once instead of N times. Net token cost per task is lower than per-gap classification even for single-gap tasks.

## Open Questions

1. **Should the classifier receive registry matches as context?**
   - **Decision**: Yes. Include matched capability names in the prompt so the classifier knows what's already available and doesn't redundantly classify for capabilities that exist.

2. **Should we keep the per-gap `classify_gap()` function for backward compatibility?**
   - **Decision**: No. Remove it. The pipeline no longer needs it, and unit tests are being rewritten anyway. Clean break.

3. **What happens when the classifier confidence is very low (<0.3)?**
   - **Decision**: Log it but proceed. Do not retry or fall back. The evaluation records the low confidence for Chapter 6 analysis.

4. **Should the decomposition prompt be simplified now that its output is only used as context?**
   - **Decision**: No, not in this plan. Per Fix #3, decomposition is acknowledged as advisory. Its primary value (feeding gap_detection's registry matching) requires the same output format it produces today. Simplifying the anti-split guidance would not save tokens because decomposition still has to enumerate capabilities for the registry. A separate follow-up could explore whether decomposition can be skipped entirely for simple tasks (Chapter 7 future work).

## Success Criteria

- [ ] All 814 unit tests pass (after classifier and gap_detection test rewrites)
- [ ] New `test_classifier.py` tests verify task-level classification for all three types
- [ ] T04 pilot: classifier returns SKILL, exactly 1 artifact generated
- [ ] T16 pilot: classifier returns AGENT, exactly 1 artifact generated
- [ ] T01 pilot: classifier returns MCP_TOOL (or registry-matched), exactly 1 artifact generated
- [ ] LangFuse traces show `classifier.task_classified` events with confidence scores
- [ ] No regressions on single-type-clear tasks
- [ ] Token cost per task decreases on multi-gap tasks (measurable in LangFuse)
- [ ] Classification accuracy can be reported as a single scalar per task in the evaluation

## Thesis-Integrity Framing

**The plan directly aligns the implementation with the thesis's research question and testbed design.** The per-gap classifier was an implementation artifact that measured the wrong thing. Task-level classification is what the research question asks and what the testbed measures.

**Honest Chapter 3 framing**:

> The classifier operates at the task level: it receives the full task description plus any required_capabilities produced by decomposition as context, and returns a single classification for the task. This matches our testbed design, where each task is labeled with one ground-truth capability type.
>
> An earlier iteration classified each decomposition-produced gap independently, but this led to multi-artifact generation that did not align with the testbed's single-type ground truth. Task-level classification directly answers the research question "How accurately can an LLM classify capability gaps into the three artifact types?" by classifying the unit of evaluation.
