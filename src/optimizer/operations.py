"""Evolutionary operations: prune, split, merge.

Type scoping (post-Steps-5/6/7/8, 2026-04-15):
- Prune: all types (unchanged)
- Split: all types — see _split_tools / _split_skills / _split_agents (Step 8)
- Merge: all types — see _merge_tools / _merge_skills / _merge_agents (Step 6)

All merge and split operations produce a ``GeneratedArtifact`` with
real executable content (not metadata-only stubs) and route it through
``verify_and_register_or_rollback`` which runs the full three-tier
verification gate and registers atomically. Parents are deactivated
only after the new artifact has been registered successfully — if the
gate rejects the new artifact, the parents stay active and the
optimizer treats the operation as a no-op. This honors the proposal's
Pillar 2 "must pass all three tiers" contract at the optimizer layer.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from langchain.chat_models import init_chat_model
from langfuse.langchain import CallbackHandler as LangfuseHandler

from src.config import DEFAULT_LLM_MODEL
from pydantic import BaseModel, Field

from src.registry.metrics import MetricsStore
from src.registry.store import SkillRegistry
from src.schemas.capability import CapabilityDescriptor, CapabilityType
from src.schemas.task import GeneratedArtifact
from src.schemas.verification import Severity

logger = logging.getLogger(__name__)


def _langfuse_config() -> dict:
    """Return a LangChain config dict with LangFuse callback (best-effort)."""
    try:
        return {"callbacks": [LangfuseHandler()]}
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------
# Step 5 (B1 Pillar 3, 2026-04-15) — shared verify-and-register-or-rollback
# ---------------------------------------------------------------------------
#
# The merge and split operations produce a GeneratedArtifact with real
# executable content (not metadata-only stubs like the pre-2026-04-15 code)
# and must route it through the three-tier verification gate before it
# enters the registry. The pre-2026-04-15 code path bypassed the gate
# entirely with a self-justifying comment ("metadata-only, no verification
# needed"), which violated the proposal's Pillar 2 contract and produced
# unexecutable descriptors that poisoned the registry.
#
# The helper below is the single entry point that both merge and split
# funnel through. Atomicity guarantee: if the gate rejects the artifact,
# NEITHER the new artifact NOR the parent capabilities are touched. The
# caller (merge/split) is responsible for deactivating parents ONLY after
# verify_and_register_or_rollback returns (True, None).
#
# Never raises — wraps every failure path (gate exception, register
# exception, unexpected errors) and returns (False, reason). Callers
# treat anything other than (True, None) as a "leave everything alone"
# signal.


def verify_and_register_or_rollback(
    artifact: GeneratedArtifact,
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    *,
    event_type: str,
    event_details: str,
    tags: list[str] | None = None,
    domain: str | None = None,
) -> tuple[bool, str | None]:
    """Verify an artifact through the three-tier gate and register atomically.

    The artifact MUST carry executable content appropriate to its type:

    - ``MCP_TOOL`` → ``artifact.source_code`` populated
    - ``SKILL`` → ``artifact.skill_content`` populated (full SKILL.md)
    - ``AGENT`` → ``artifact.agent_config`` populated (dict with
      ``system_prompt`` + optional ``subagents`` list)

    Behavior:

    1. Call ``src.verification.gate.verify(artifact)`` which runs Tier 0
       (skill quick_validate) + Tier 1 (static scanner with code-fence
       extraction) + Tier 2 (behavioral testing) + Tier 3 (adversarial
       probing). The gate never raises; this helper catches any
       unexpected exception as a safety net.
    2. If ``report.overall_passed`` is False → record a
       ``verification_rollback`` metrics event with the blocking findings
       summarized in ``details`` and return ``(False, reason)``. The
       registry is not touched.
    3. Otherwise build a ``CapabilityDescriptor`` and call
       ``registry.add``. For ``MCP_TOOL`` artifacts the registration
       path delegates to ``ToolLoader.register_tool`` which also runs
       AST schema extraction (Step 2). For ``SKILL`` and ``AGENT`` the
       descriptor is built directly from ``artifact.input_schema`` and
       ``artifact.output_schema`` which the caller (merge/split) is
       responsible for populating.
    4. Record a metrics event with ``event_type`` and ``event_details``
       so the optimizer audit trail distinguishes successful
       registrations from rollbacks.

    Args:
        artifact: The generated artifact. Must have executable content
            populated for its capability type.
        registry: Target registry.
        metrics_store: Metrics store for event recording.
        event_type: Metrics event type on success (e.g. ``"merged"``,
            ``"split_variant"``). The rollback path always records
            ``"verification_rollback"`` regardless.
        event_details: Freeform description for the success event
            (e.g. ``"from cap-a, cap-b"``).
        tags: Optional tags to attach to the descriptor.
        domain: Optional domain override. Falls back to
            ``artifact.domain`` or ``"generated"``.

    Returns:
        ``(True, None)`` on gate pass + successful registration.
        ``(False, reason)`` on gate rejection, registration exception,
        or any other failure path. Never raises.
    """
    from src.verification.gate import verify

    name = artifact.capability_name

    # --- Gate ---
    try:
        report = verify(artifact)
    except Exception as exc:  # noqa: BLE001
        reason = f"verify() raised unexpectedly: {exc}"
        logger.warning(
            "verify_and_register_or_rollback(%s): %s", name, reason
        )
        metrics_store.record_event(name, "verification_rollback", reason)
        return False, reason

    if not report.overall_passed:
        blocking = [
            f"tier{f.tier}/{f.severity.value}:{f.category}"
            for f in report.findings
            if f.severity in (Severity.CRITICAL, Severity.HIGH)
        ]
        reason = (
            f"gate rejected {name}: "
            f"{', '.join(blocking) if blocking else 'report.overall_passed=False with no blocking findings'}"
        )
        logger.warning(
            "verify_and_register_or_rollback(%s): %s", name, reason
        )
        metrics_store.record_event(name, "verification_rollback", reason)
        return False, reason

    # --- Register ---
    try:
        effective_domain = domain or artifact.domain or "generated"
        if artifact.capability_type == CapabilityType.MCP_TOOL:
            # Delegate to ToolLoader.register_tool so we inherit the
            # AST-based input/output schema extraction from Step 2 AND
            # the source_code-in-metadata persistence. register_tool
            # calls registry.add internally.
            from src.orchestrator.tool_loader import ToolLoader
            loader = ToolLoader()
            loader.register_tool(
                artifact,
                registry,
                description=artifact.description,
                domain=effective_domain,
                tags=list(tags or []),
            )
        else:
            # SKILL / AGENT — build the descriptor directly. The caller
            # is responsible for populating artifact.input_schema and
            # artifact.output_schema (merge derives from parents, split
            # derives from the parent being specialized).
            metadata: dict[str, str] = {
                "generated": "true",
                "source": "optimizer",
            }
            if artifact.skill_content:
                metadata["skill_content"] = artifact.skill_content
            if artifact.agent_config:
                metadata["agent_config"] = (
                    json.dumps(artifact.agent_config)
                    if isinstance(artifact.agent_config, dict)
                    else str(artifact.agent_config)
                )
            descriptor = CapabilityDescriptor(
                name=name,
                description=artifact.description or name,
                capability_type=artifact.capability_type,
                domain=effective_domain,
                is_generic=bool(artifact.is_generic),
                input_schema=dict(artifact.input_schema or {}),
                output_schema=dict(artifact.output_schema or {}),
                tags=list(tags or []),
                metadata=metadata,
            )
            registry.add(descriptor)

        metrics_store.record_event(name, event_type, event_details)
        logger.info(
            "verify_and_register_or_rollback(%s): registered as %s (%s)",
            name, event_type, event_details,
        )
        return True, None

    except Exception as exc:  # noqa: BLE001
        reason = f"registration failed for {name}: {exc}"
        logger.warning(
            "verify_and_register_or_rollback(%s): %s", name, reason
        )
        metrics_store.record_event(name, "verification_rollback", reason)
        return False, reason


# --- Structured output schemas for LLM calls ---


class SplitVariant(BaseModel):
    """A specialized variant produced by splitting a capability."""

    name: str = Field(..., description="Name for the specialized variant (hyphen-case)")
    description: str = Field(..., description="What this variant does")

    model_config = {"extra": "forbid"}


class SplitResult(BaseModel):
    """LLM output for a split operation."""

    analysis: str = Field(..., description="Why the capability fails and how to split it")
    variants: list[SplitVariant] = Field(
        ..., min_length=2, max_length=4, description="Specialized variants"
    )

    model_config = {"extra": "forbid"}


class _MergeResultCommon(BaseModel):
    """Shared fields across all three per-type merge LLM output schemas."""

    name: str = Field(..., description="Name for the merged capability (hyphen-case)")
    description: str = Field(..., description="What the merged capability does")
    rationale: str = Field(
        ..., description="Why merging these capabilities produces a single coherent one"
    )

    model_config = {"extra": "forbid"}


class MergeToolsResult(_MergeResultCommon):
    """LLM output for merging two or more MCP_TOOL capabilities.

    Carries a full FastMCP source file that handles the union of the
    parent capabilities' input spaces. The source goes through the
    same three-tier verification gate as generator-produced MCP tools.
    """

    combined_source_code: str = Field(
        ...,
        description=(
            "Complete Python source file for the merged MCP tool. Must "
            "follow the FastMCP pattern (imports, mcp = FastMCP(...), "
            "@mcp.tool()-decorated async def, Annotated[type, Field(...)] "
            "parameters, if __name__ == '__main__': mcp.run()). The "
            "function body should handle the union of the parents' "
            "input spaces — merging behavior, not just concatenation."
        ),
    )
    combined_pip_packages: list[str] = Field(
        default_factory=list,
        description=(
            "Union of PyPI package names required by the merged source. "
            "Use pip install names (pyyaml not yaml). Omit stdlib and "
            "the base image packages (mcp, pydantic)."
        ),
    )


class MergeSkillsResult(_MergeResultCommon):
    """LLM output for merging two or more SKILL capabilities.

    Carries a full SKILL.md body (YAML frontmatter + instruction prose +
    optional embedded Python code fences). The content goes through
    Tier 0 (``tools/quick_validate.py``) + Tier 1 (code-fence extractor
    + AST scan on extracted Python blocks) + Tier 2 / Tier 3 if Tier 1
    passes.
    """

    combined_skill_content: str = Field(
        ...,
        description=(
            "Complete SKILL.md body including YAML frontmatter (name, "
            "description, allowed-tools, etc.) and the instruction prose "
            "that teaches an agent how to perform the merged workflow. "
            "Should cover the union of the parent skills' workflows as "
            "a single coherent set of instructions, not a concatenation."
        ),
    )


class MergeAgentsSubagent(BaseModel):
    """One subagent in a lead-with-subagents AGENT configuration."""

    name: str = Field(..., description="Subagent name (hyphen-case)")
    description: str = Field(..., description="One-sentence description of the subagent's role")
    system_prompt: str = Field(
        ..., description="Full system prompt for the subagent's isolated context"
    )
    tools: list[str] = Field(
        default_factory=list,
        description=(
            "Names of tools the subagent can call (typically "
            "['sandbox_execute']). Generator agents currently only "
            "support sandbox_execute; other tool names are ignored at "
            "runtime."
        ),
    )

    model_config = {"extra": "forbid"}


class MergeAgentsResult(_MergeResultCommon):
    """LLM output for merging two or more AGENT capabilities.

    Carries a lead system prompt plus an optional subagents list.
    Subagent merging rules (enforced by the caller after LLM output):

    - Disjoint subagent names across parents → union the lists.
    - Overlapping subagent names → the LLM is instructed to synthesize
      merged prompts via text combination.
    - Combined subagent count > 8 → reject the merge up front
      (budget ceiling per the plan).
    """

    combined_system_prompt: str = Field(
        ...,
        description=(
            "Full system prompt for the lead agent of the merged "
            "capability. Must describe the adaptive reasoning the "
            "agent should perform across the union of the parents' "
            "problem spaces."
        ),
    )
    combined_subagents: list[MergeAgentsSubagent] = Field(
        default_factory=list,
        description=(
            "Subagents the lead can delegate to via the task() tool. "
            "Empty list means the merged agent runs as a solo DeepAgent. "
            "Total count must not exceed 8 (Step 6d budget ceiling); "
            "the caller rejects the merge if this is violated."
        ),
    )


# --- Operations ---


def prune(
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    *,
    window_days: int = 30,
) -> list[str]:
    """Deactivate capabilities with zero invocations in the given window.

    Applies to ALL capability types.

    Returns:
        List of pruned (deactivated) capability IDs.
    """
    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    active = registry.list_all(is_active=True)
    pruned: list[str] = []

    for cap in active:
        # Never prune pinned capabilities (seeded entries that should survive
        # optimizer cycles regardless of invocation count)
        if (cap.metadata or {}).get("pinned") == "true":
            logger.debug("Skipping pinned capability '%s'", cap.name)
            continue
        count = metrics_store.invocations_since(cap.name, cutoff)
        if count == 0:
            registry.deactivate(cap.name)
            metrics_store.record_event(cap.name, "pruned", f"zero invocations in {window_days}d")
            logger.info("Pruned capability '%s' (zero invocations in %dd)", cap.name, window_days)
            pruned.append(cap.name)

    return pruned


def split(
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    capability_id: str,
    *,
    llm_model: str = "",
) -> list[str]:
    """Split a capability into specialized variants.

    Type constraint: Only SKILL and AGENT types can be split.

    Candidates should have reuse_rate > 0.6 AND success_rate < 0.7.

    Args:
        registry: Skill registry.
        metrics_store: Metrics store for pulling metrics.
        capability_id: ID of the capability to split.
        llm_model: Model identifier for init_chat_model.

    Returns:
        List of new capability IDs.

    Raises:
        ValueError: If capability is MCP_TOOL type.
        KeyError: If capability not found.
    """
    cap = registry.get(capability_id)
    if cap is None:
        raise KeyError(f"Capability '{capability_id}' not found in registry")

    if cap.capability_type == CapabilityType.MCP_TOOL:
        raise ValueError(
            "Split is not supported for MCP_TOOL capabilities. "
            "Only SKILL and AGENT types can be split."
        )

    llm = init_chat_model(llm_model or DEFAULT_LLM_MODEL)
    structured_llm = llm.with_structured_output(SplitResult)
    lf_config = _langfuse_config()

    prompt = (
        f"A capability named '{cap.name}' with description '{cap.description}' "
        f"(type: {cap.capability_type.value}) has high reuse but mediocre success rate. "
        f"Analyze why it might fail for some use cases and propose 2 specialized variants "
        f"that each handle a narrower subset of tasks more reliably.\n\n"
        f"Return a JSON object with 'analysis' (string), and 'variants' (list of objects "
        f"with 'name' and 'description'). Example:\n"
        f'{{"analysis": "The capability handles both CSV and JSON but fails for malformed JSON.", '
        f'"variants": ['
        f'{{"name": "csv-data-cleaner", "description": "Cleans and validates CSV data files"}}, '
        f'{{"name": "json-data-cleaner", "description": "Cleans and validates JSON data files"}}'
        f"]}}"
    )

    # Initial attempt
    try:
        result: SplitResult = structured_llm.invoke(prompt, config=lf_config)
    except Exception as first_err:
        logger.warning(
            "split() LLM output invalid for '%s', triggering repair pass: %s",
            capability_id, first_err,
        )
        repair_prompt = (
            f"{prompt}\n\nPrevious attempt failed with: {first_err}\n"
            f"Return only valid JSON matching the schema above."
        )
        try:
            result = structured_llm.invoke(repair_prompt, config=lf_config)
        except Exception as repair_err:
            logger.error("split() repair pass failed for '%s': %s", capability_id, repair_err)
            raise

    # Note: variant CapabilityDescriptors are metadata-only (name + description).
    # They contain no generated code and therefore do not go through the
    # three-tier verification gate.  Verification applies only to code artifacts
    # (MCP tools, SKILL.md files, agent configs) produced by the pipeline generators.
    # Validate all variant names first (all-or-nothing: if any name collides,
    # abort the entire split so the parent is not left deactivated with missing variants).
    for variant in result.variants:
        if registry.get(variant.name) is not None:
            raise ValueError(
                f"split(): variant name '{variant.name}' already exists in registry — "
                f"aborting split of '{capability_id}'"
            )

    new_ids: list[str] = []
    for variant in result.variants:
        new_cap = CapabilityDescriptor(
            name=variant.name,
            description=variant.description,
            capability_type=cap.capability_type,
            domain=cap.domain,
            tags=cap.tags,
        )
        registry.add(new_cap)
        metrics_store.record_event(
            variant.name, "created", f"split from {capability_id}"
        )
        new_ids.append(variant.name)

    # Deactivate parent only after all variants are successfully added
    registry.deactivate(capability_id)
    metrics_store.record_event(capability_id, "split", f"into {new_ids}")
    logger.info("Split '%s' into %s", capability_id, new_ids)

    return new_ids


_MAX_AGENT_SUBAGENT_BUDGET = 8


# ---------------------------------------------------------------------------
# Step 6 + pre-registered finding #4 from §12 (2026-04-15 end of session)
# ---------------------------------------------------------------------------
#
# The proposal's Pillar 3 Merge clause says *"The merged version inherits
# the strongest behavioral patterns from each parent."* True "strongest
# patterns" selection would require per-parent success metrics on each
# behavior subspace, which is Chapter 7 work. For now we approximate it
# via explicit robustness hints in the merge prompts — tell the LLM to
# prefer battle-tested library patterns, robust error handling, and
# explicit validation at boundaries.
#
# **Deliberately domain-general — no testbed-specific library names or
# edge-case examples.** An earlier version of this hint named
# `csv.Sniffer`, `pandas.read_csv`, `openpyxl`, specific CSV edge cases
# (BOM markers, line endings, header detection), etc. — that was
# effective at steering the LLM past the specific bugs seen on
# `test_split_merge.py`, but it overfit the hint to the Business
# Analytics testbed. MINT is supposed to work across arbitrary CLI
# inputs (medical, legal, factory simulation, text analysis, …), and
# a prompt that biases every merge toward file-format parsing would
# add noise on non-file-format tasks and hurt generalization. The
# current hint keeps only domain-general principles so it transfers
# across verticals. A Phase 5 / Chapter 6 consequence: merge success
# rates may be lower than a testbed-tuned prompt would show, because
# the LLM has no hand-holding on the specific edge cases of any one
# format. This is proposal-aligned (the gate catches bad output) but
# worth disclosing.
#
# This block is injected into all three per-type merge prompts as a
# shared section. It is the domain-general partial implementation of
# finding #4 from §12 of docs/MASTER_PLAN_2026_04_13.md — pulled
# forward from the originally-planned §12 timing because the Step 6
# merge smoke gave us strong empirical motivation to at least land
# the general principles now. True per-parent success-metric-driven
# selection remains Chapter 7 future work.

_MERGE_ROBUSTNESS_HINT = """\
## Robustness requirements (Pillar 3 "strongest behavioral patterns")

The proposal's Pillar 3 Merge clause says the merged capability should
inherit the **strongest behavioral patterns** from each parent — not
just the union of their logic. Where the parents diverge, prefer the
approach that is **more robust under real-world input**. The criteria
below are intentionally domain-general — they should guide the
merged implementation regardless of whether the parents handle data
parsing, math, text analysis, agentic reasoning, or any other task
shape.

- **Prefer well-known, battle-tested library defaults over
  hand-rolled logic.** If a mature standard-library or widely-used
  third-party library already solves the task the parents are
  performing, use it with its documented defaults rather than
  reimplementing the same logic by hand. Hand-rolled reimplementations
  frequently miss edge cases the library has already handled.

- **Handle realistic edge cases appropriate to the data format and
  domain.** Identify the edge cases that real user input for this
  specific task will contain, and handle them by construction rather
  than by accident. The set of relevant edge cases depends on the
  task — be conservative and assume user input will be messier than
  the parent implementations assumed.

- **Explicit error handling at boundaries.** Validate inputs before
  processing them. On failure, raise a clear error with enough context
  for the caller to understand what went wrong — do NOT silently
  return a plausible-looking default, a zero count, or an empty
  collection. The verification gate's Tier 3 adversarial prober will
  run the merged implementation against realistic reference inputs
  and flag any deterministic mismatch; silent failures will trip
  that check.

- **Never swallow exceptions to return a plausible-looking dict.**
  If the function can't complete its work, let the exception propagate
  so the calling pipeline sees a real error, not a synthesized "empty
  result" that masks the failure.

Where the two parents genuinely take different approaches to the same
step, prefer the one that is more robust under the criteria above.
If neither parent uses a robust pattern, introduce one — the merged
version should be stronger than either parent in isolation, not
merely the LCD of their shared logic.

"""


def _invoke_structured_with_repair(
    structured_llm,
    prompt: str,
    lf_config: dict,
    op: str,
    context: str,
):
    """Invoke a structured LLM with a one-shot repair pass on failure.

    Pattern lifted from the generator nodes — first attempt, on
    Pydantic validation / LLM error log a warning and re-invoke with
    a repair prompt appended. On repair failure, re-raise so the
    caller can classify the operation as "no-op, parents untouched".
    """
    try:
        return structured_llm.invoke(prompt, config=lf_config)
    except Exception as first_err:
        logger.warning(
            "%s LLM output invalid for %s, triggering repair pass: %s",
            op, context, first_err,
        )
        repair_prompt = (
            f"{prompt}\n\nPrevious attempt failed with: {first_err}\n"
            f"Return only valid JSON matching the schema above."
        )
        try:
            return structured_llm.invoke(repair_prompt, config=lf_config)
        except Exception as repair_err:
            logger.error(
                "%s repair pass failed for %s: %s", op, context, repair_err
            )
            raise


def _merge_tools(
    caps: list[CapabilityDescriptor],
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    *,
    llm_model: str,
) -> str | None:
    """Per-type merge for MCP_TOOL parents.

    Builds a prompt containing each parent's source_code (pulled from
    ``CapabilityDescriptor.metadata['source_code']``), asks the LLM
    for a single combined FastMCP source that handles the union of
    the parents' input spaces, packages the output as a
    ``GeneratedArtifact``, and routes it through
    :func:`verify_and_register_or_rollback`.

    On gate pass, deactivates both parents atomically and returns the
    new capability name. On gate fail, returns ``None`` and leaves the
    parents active.
    """
    llm = init_chat_model(llm_model or DEFAULT_LLM_MODEL)
    structured_llm = llm.with_structured_output(MergeToolsResult)
    lf_config = _langfuse_config()

    parent_blocks: list[str] = []
    for c in caps:
        source = (c.metadata or {}).get("source_code", "").strip()
        if source:
            parent_blocks.append(
                f"## {c.name}\n"
                f"Description: {c.description}\n"
                f"```python\n{source}\n```"
            )
        else:
            parent_blocks.append(
                f"## {c.name}\nDescription: {c.description}\n(no source code available)"
            )

    parents_section = "\n\n".join(parent_blocks)
    prompt = (
        "Merge the following MCP_TOOL capabilities into a single broader "
        "FastMCP tool that handles the union of their input spaces. "
        "The merged tool must follow the FastMCP pattern: `from "
        "mcp.server.fastmcp import FastMCP` + `mcp = FastMCP(...)` + "
        "an `@mcp.tool()`-decorated `async def` with `Annotated[type, "
        "Field(...)]` parameters + `if __name__ == '__main__': "
        "mcp.run()` at the end.\n\n"
        "Do NOT just concatenate the parent sources. Produce a single "
        "coherent function that subsumes both parents' logic.\n\n"
        "## Parent capabilities\n\n"
        f"{parents_section}\n\n"
        f"{_MERGE_ROBUSTNESS_HINT}"
        "Return a JSON object with fields: `name` (hyphen-case), "
        "`description`, `rationale`, `combined_source_code` (complete "
        "Python file), `combined_pip_packages` (list of pip install "
        "names, omit stdlib and mcp/pydantic)."
    )

    try:
        result: MergeToolsResult = _invoke_structured_with_repair(
            structured_llm, prompt, lf_config,
            op="merge(MCP_TOOL)", context=[c.name for c in caps],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("_merge_tools: LLM repair failed for %s: %s",
                       [c.name for c in caps], exc)
        return None

    if registry.get(result.name) is not None:
        logger.warning(
            "_merge_tools: name '%s' already exists — aborting merge of %s",
            result.name, [c.name for c in caps],
        )
        return None

    # Union parent tags for the merged descriptor
    all_tags = list({tag for c in caps for tag in c.tags})

    artifact = GeneratedArtifact(
        capability_name=result.name,
        capability_type=CapabilityType.MCP_TOOL,
        description=result.description,
        source_code=result.combined_source_code,
        pip_packages=list(result.combined_pip_packages or []),
        gap_query=None,
        domain=caps[0].domain,
    )

    ok, reason = verify_and_register_or_rollback(
        artifact,
        registry,
        metrics_store,
        event_type="merged",
        event_details=f"from {[c.name for c in caps]}",
        tags=all_tags,
        domain=caps[0].domain,
    )
    if not ok:
        logger.warning(
            "_merge_tools: gate/register rejected '%s' — parents untouched: %s",
            result.name, reason,
        )
        return None

    # Atomically deactivate parents only after successful registration
    for c in caps:
        registry.deactivate(c.name)
        metrics_store.record_event(c.name, "merged", f"into {result.name}")

    logger.info("Merged MCP_TOOL %s → '%s'", [c.name for c in caps], result.name)
    return result.name


def _merge_skills(
    caps: list[CapabilityDescriptor],
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    *,
    llm_model: str,
) -> str | None:
    """Per-type merge for SKILL parents.

    Pulls each parent's ``skill_content`` from metadata, asks the LLM
    for a combined SKILL.md body, packages as a GeneratedArtifact,
    and routes through the gate. On pass, deactivates parents; on
    fail, leaves them active and returns None.
    """
    llm = init_chat_model(llm_model or DEFAULT_LLM_MODEL)
    structured_llm = llm.with_structured_output(MergeSkillsResult)
    lf_config = _langfuse_config()

    parent_blocks: list[str] = []
    for c in caps:
        content = (c.metadata or {}).get("skill_content", "").strip()
        if content:
            parent_blocks.append(
                f"## {c.name}\n"
                f"Description: {c.description}\n"
                f"```markdown\n{content}\n```"
            )
        else:
            parent_blocks.append(
                f"## {c.name}\nDescription: {c.description}\n(no skill content available)"
            )

    parents_section = "\n\n".join(parent_blocks)
    prompt = (
        "Merge the following SKILL capabilities into a single SKILL.md "
        "that covers the union of their workflows. The merged SKILL.md "
        "must have valid YAML frontmatter with `name`, `description`, "
        "and optionally `allowed-tools`, followed by instruction prose "
        "and optional fenced Python code blocks.\n\n"
        "Produce a coherent single skill, not a concatenation of two. "
        "The merged instructions should read as one unified workflow.\n\n"
        "## Parent capabilities\n\n"
        f"{parents_section}\n\n"
        f"{_MERGE_ROBUSTNESS_HINT}"
        "**Note on SKILL content shape.** Fenced Python code blocks in "
        "a SKILL.md are treated as executable surface by the verification "
        "gate — extracted by the layered code-fence scanner (Tier 1) and "
        "run through the adversarial prober (Tier 3). Any Python you "
        "embed is subject to the same robustness criteria above and "
        "will be tested against realistic reference inputs. Choose the "
        "content shape that best fits the merged workflow: embed Python "
        "only when the workflow is inherently code-shaped, otherwise "
        "prefer prose instructions that the executing agent can follow "
        "with its own tool-calling loop.\n\n"
        "Return a JSON object with fields: `name` (hyphen-case), "
        "`description`, `rationale`, `combined_skill_content` (full "
        "SKILL.md body including YAML frontmatter)."
    )

    try:
        result: MergeSkillsResult = _invoke_structured_with_repair(
            structured_llm, prompt, lf_config,
            op="merge(SKILL)", context=[c.name for c in caps],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("_merge_skills: LLM repair failed for %s: %s",
                       [c.name for c in caps], exc)
        return None

    if registry.get(result.name) is not None:
        logger.warning(
            "_merge_skills: name '%s' already exists — aborting merge of %s",
            result.name, [c.name for c in caps],
        )
        return None

    all_tags = list({tag for c in caps for tag in c.tags})
    # Merge input/output schemas from parents (union). Empty if parents
    # had empty schemas — the gate still runs but Pillar 3 merge discovery
    # won't find partners for the merged capability until it accumulates
    # real invocations. That's acceptable for an optimizer output.
    merged_input_schema: dict[str, str] = {}
    merged_output_schema: dict[str, str] = {}
    for c in caps:
        for k, v in (c.input_schema or {}).items():
            merged_input_schema.setdefault(k, v)
        for k, v in (c.output_schema or {}).items():
            merged_output_schema.setdefault(k, v)

    artifact = GeneratedArtifact(
        capability_name=result.name,
        capability_type=CapabilityType.SKILL,
        description=result.description,
        skill_content=result.combined_skill_content,
        gap_query=None,
        input_schema=merged_input_schema,
        output_schema=merged_output_schema,
        domain=caps[0].domain,
    )

    ok, reason = verify_and_register_or_rollback(
        artifact,
        registry,
        metrics_store,
        event_type="merged",
        event_details=f"from {[c.name for c in caps]}",
        tags=all_tags,
        domain=caps[0].domain,
    )
    if not ok:
        logger.warning(
            "_merge_skills: gate/register rejected '%s' — parents untouched: %s",
            result.name, reason,
        )
        return None

    for c in caps:
        registry.deactivate(c.name)
        metrics_store.record_event(c.name, "merged", f"into {result.name}")

    logger.info("Merged SKILL %s → '%s'", [c.name for c in caps], result.name)
    return result.name


def _merge_agents(
    caps: list[CapabilityDescriptor],
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    *,
    llm_model: str,
) -> str | None:
    """Per-type merge for AGENT parents.

    Packages parent agent_configs (system prompts + subagents lists),
    asks the LLM to produce a single merged agent config, and routes
    the result through the gate. Enforces the budget ceiling: if the
    LLM returns more than ``_MAX_AGENT_SUBAGENT_BUDGET`` (=8) subagents,
    the merge is rejected without calling the gate — the parents stay
    active and None is returned. Rationale: a lead with more than 8
    subagents becomes a context-management liability (every task()
    call to a subagent is a separate DeepAgent invocation) and the
    proposal's Pillar 3 Merge is supposed to REDUCE complexity, not
    increase it.
    """
    llm = init_chat_model(llm_model or DEFAULT_LLM_MODEL)
    structured_llm = llm.with_structured_output(MergeAgentsResult)
    lf_config = _langfuse_config()

    parent_blocks: list[str] = []
    for c in caps:
        agent_cfg_raw = (c.metadata or {}).get("agent_config", "")
        try:
            agent_cfg = (
                json.loads(agent_cfg_raw)
                if isinstance(agent_cfg_raw, str) and agent_cfg_raw
                else (agent_cfg_raw if isinstance(agent_cfg_raw, dict) else {})
            )
        except Exception:  # noqa: BLE001
            agent_cfg = {}
        system_prompt = (agent_cfg.get("system_prompt") or "").strip()
        subagents = agent_cfg.get("subagents") or []
        sub_summary = (
            ", ".join(s.get("name", "?") for s in subagents)
            if subagents
            else "(solo agent, no subagents)"
        )
        parent_blocks.append(
            f"## {c.name}\n"
            f"Description: {c.description}\n"
            f"System prompt:\n```\n{system_prompt}\n```\n"
            f"Subagents: {sub_summary}"
        )

    parents_section = "\n\n".join(parent_blocks)
    prompt = (
        "Merge the following AGENT capabilities into a single lead agent "
        "that can adapt across the union of their problem spaces. The "
        "merged agent may delegate to subagents via the task() tool, "
        "but the total subagent count MUST NOT exceed "
        f"{_MAX_AGENT_SUBAGENT_BUDGET}. If the parents have overlapping "
        "subagent names, synthesize their prompts into a single merged "
        "subagent. If the parents have disjoint subagents, include the "
        "union (subject to the budget ceiling).\n\n"
        "## Parent capabilities\n\n"
        f"{parents_section}\n\n"
        f"{_MERGE_ROBUSTNESS_HINT}"
        "**Note on AGENT content shape.** The merged agent's "
        "`system_prompt` and each `subagent.system_prompt` are "
        "prose-only — no executable Python. The robustness criteria "
        "above apply to the **instructions** you give the agent: tell "
        "the agent to prefer well-known library patterns and robust "
        "error handling in whatever code it generates at runtime, "
        "rather than telling it to hand-roll logic that matches the "
        "parents' implementation detail. An agent following robust "
        "instructions will produce robust outputs at runtime; an agent "
        "following hand-rolled-pattern instructions will inherit the "
        "parents' edge-case failures. The `strongest behavioral "
        "patterns` criterion applies to the instruction content even "
        "though the agent config itself has no verifiable code "
        "surface.\n\n"
        "Return a JSON object with fields: `name` (hyphen-case), "
        "`description`, `rationale`, `combined_system_prompt` (full "
        "lead system prompt), `combined_subagents` (list of objects "
        "with `name`, `description`, `system_prompt`, `tools`). "
        "Subagent `tools` is typically `[\"sandbox_execute\"]`."
    )

    try:
        result: MergeAgentsResult = _invoke_structured_with_repair(
            structured_llm, prompt, lf_config,
            op="merge(AGENT)", context=[c.name for c in caps],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("_merge_agents: LLM repair failed for %s: %s",
                       [c.name for c in caps], exc)
        return None

    if len(result.combined_subagents) > _MAX_AGENT_SUBAGENT_BUDGET:
        logger.warning(
            "_merge_agents: LLM returned %d subagents (> %d budget) — "
            "aborting merge of %s, parents stay active",
            len(result.combined_subagents),
            _MAX_AGENT_SUBAGENT_BUDGET,
            [c.name for c in caps],
        )
        return None

    if registry.get(result.name) is not None:
        logger.warning(
            "_merge_agents: name '%s' already exists — aborting merge of %s",
            result.name, [c.name for c in caps],
        )
        return None

    # Build the merged agent_config dict that the execution backend expects.
    agent_config: dict = {
        "system_prompt": result.combined_system_prompt,
        "is_deep_agent": True,
    }
    if result.combined_subagents:
        agent_config["subagents"] = [
            sa.model_dump() for sa in result.combined_subagents
        ]

    all_tags = list({tag for c in caps for tag in c.tags})

    artifact = GeneratedArtifact(
        capability_name=result.name,
        capability_type=CapabilityType.AGENT,
        description=result.description,
        agent_config=agent_config,
        gap_query=None,
        domain=caps[0].domain,
    )

    ok, reason = verify_and_register_or_rollback(
        artifact,
        registry,
        metrics_store,
        event_type="merged",
        event_details=f"from {[c.name for c in caps]}",
        tags=all_tags,
        domain=caps[0].domain,
    )
    if not ok:
        logger.warning(
            "_merge_agents: gate/register rejected '%s' — parents untouched: %s",
            result.name, reason,
        )
        return None

    for c in caps:
        registry.deactivate(c.name)
        metrics_store.record_event(c.name, "merged", f"into {result.name}")

    logger.info("Merged AGENT %s → '%s'", [c.name for c in caps], result.name)
    return result.name


def merge(
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    capability_ids: list[str],
    *,
    llm_model: str = "",
) -> str | None:
    """Merge multiple capabilities into a single broader capability.

    Dispatches by capability type to one of the per-type merge helpers:

    - All parents ``MCP_TOOL`` → :func:`_merge_tools`
    - All parents ``SKILL`` → :func:`_merge_skills`
    - All parents ``AGENT`` → :func:`_merge_agents`
    - Mixed types → ``ValueError`` (cross-type merge is Chapter 7
      future work — the merged executable content only makes sense
      within a single type).

    Each per-type helper produces a ``GeneratedArtifact`` with real
    executable content, routes it through
    :func:`verify_and_register_or_rollback`, and atomically deactivates
    the parents only after the gate passes. If the gate rejects the
    merged artifact, the function returns ``None`` and neither the
    new artifact nor the parents are touched. This is the atomic
    rollback guarantee the proposal's Pillar 2 contract requires for
    optimizer-generated capabilities.

    Args:
        registry: Skill registry.
        metrics_store: Metrics store for event recording.
        capability_ids: IDs of capabilities to merge (typically 2,
            but per-type helpers accept any ≥ 2).
        llm_model: Model identifier for init_chat_model. Empty string
            uses DEFAULT_LLM_MODEL.

    Returns:
        New merged capability name on gate pass + register success,
        ``None`` on gate rejection or any other non-fatal failure.

    Raises:
        KeyError: If any capability_id is not found in the registry.
        ValueError: If capabilities have mixed types (cross-type
            merge is explicitly unsupported — see "mixed types" above).
    """
    if len(capability_ids) < 2:
        raise ValueError(
            f"merge() requires at least 2 capabilities, got {len(capability_ids)}"
        )

    caps: list[CapabilityDescriptor] = []
    for cid in capability_ids:
        cap = registry.get(cid)
        if cap is None:
            raise KeyError(f"Capability '{cid}' not found in registry")
        caps.append(cap)

    types = {c.capability_type for c in caps}
    if len(types) > 1:
        type_names = sorted(t.value for t in types)
        raise ValueError(
            "merge() does not support cross-type merging — all parents "
            f"must be the same CapabilityType. Got: {type_names}"
        )

    cap_type = next(iter(types))
    if cap_type == CapabilityType.MCP_TOOL:
        return _merge_tools(caps, registry, metrics_store, llm_model=llm_model)
    if cap_type == CapabilityType.SKILL:
        return _merge_skills(caps, registry, metrics_store, llm_model=llm_model)
    if cap_type == CapabilityType.AGENT:
        return _merge_agents(caps, registry, metrics_store, llm_model=llm_model)

    raise ValueError(f"merge() does not support capability_type={cap_type.value}")
