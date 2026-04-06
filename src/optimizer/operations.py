"""Evolutionary operations: prune, split, merge.

Type-scoped constraints:
- Prune: all types
- Split: SKILL and AGENT only (not MCP_TOOL)
- Merge: SKILL only
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from langchain.chat_models import init_chat_model
from langfuse.langchain import CallbackHandler as LangfuseHandler
from pydantic import BaseModel, Field

from src.registry.metrics import MetricsStore
from src.registry.store import SkillRegistry
from src.schemas.capability import CapabilityDescriptor, CapabilityType

logger = logging.getLogger(__name__)


def _langfuse_config() -> dict:
    """Return a LangChain config dict with LangFuse callback (best-effort)."""
    try:
        return {"callbacks": [LangfuseHandler()]}
    except Exception:  # noqa: BLE001
        return {}


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


class MergeResult(BaseModel):
    """LLM output for a merge operation."""

    name: str = Field(..., description="Name for the merged capability (hyphen-case)")
    description: str = Field(..., description="What the merged capability does")
    rationale: str = Field(..., description="Why merging these capabilities makes sense")

    model_config = {"extra": "forbid"}


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
    llm_model: str = "openai:gpt-5-mini",
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

    llm = init_chat_model(llm_model)
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
            logger.warning(
                "split(): variant name '%s' already exists — aborting split of '%s'",
                variant.name,
                capability_id,
            )
            return []

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


def merge(
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    capability_ids: list[str],
    *,
    llm_model: str = "openai:gpt-5-mini",
) -> str:
    """Merge multiple capabilities into a single broader capability.

    Type constraint: Only SKILL capabilities can be merged.

    Candidates should have reuse_rate < 0.05 and high embedding similarity.

    Args:
        registry: Skill registry.
        metrics_store: Metrics store.
        capability_ids: IDs of capabilities to merge.
        llm_model: Model identifier for init_chat_model.

    Returns:
        New merged capability ID.

    Raises:
        ValueError: If any capability is not SKILL type.
        KeyError: If any capability not found.
    """
    caps: list[CapabilityDescriptor] = []
    for cid in capability_ids:
        cap = registry.get(cid)
        if cap is None:
            raise KeyError(f"Capability '{cid}' not found in registry")
        caps.append(cap)

    non_skill_types = {
        c.capability_type.value for c in caps if c.capability_type != CapabilityType.SKILL
    }
    if non_skill_types:
        types = ", ".join(sorted(non_skill_types))
        raise ValueError(f"Merge is only supported for SKILL capabilities. Got: {types}")

    llm = init_chat_model(llm_model)
    structured_llm = llm.with_structured_output(MergeResult)
    lf_config = _langfuse_config()

    descriptions = "\n".join(
        f"- {c.name}: {c.description}" for c in caps
    )
    prompt = (
        f"The following capabilities are rarely used individually and overlap in scope:\n"
        f"{descriptions}\n\n"
        f"Merge them into a single broader SKILL capability that covers all their use cases. "
        f"Return a JSON object with 'name' (hyphen-case), 'description', and 'rationale'. "
        f"Example:\n"
        f'{{"name": "data-cleaning", '
        f'"description": "Clean and validate both CSV and JSON data files", '
        f'"rationale": "Both capabilities handle data cleaning with overlapping logic"}}'
    )

    # Initial attempt + repair pass on failure
    try:
        result: MergeResult = structured_llm.invoke(prompt, config=lf_config)
    except Exception as first_err:
        logger.warning(
            "merge() LLM output invalid for %s, triggering repair pass: %s",
            capability_ids, first_err,
        )
        repair_prompt = (
            f"{prompt}\n\nPrevious attempt failed with: {first_err}\n"
            f"Return only valid JSON matching the schema above."
        )
        try:
            result = structured_llm.invoke(repair_prompt, config=lf_config)
        except Exception as repair_err:
            logger.error("merge() repair pass failed for %s: %s", capability_ids, repair_err)
            raise

    if registry.get(result.name) is not None:
        raise ValueError(
            f"merge(): name '{result.name}' already exists in registry — "
            "parents not deactivated"
        )

    # Note: the merged CapabilityDescriptor is metadata-only (name + description).
    # No generated code is produced, so the three-tier verification gate is not
    # invoked here.  Verification applies only to code artifacts produced by the
    # pipeline generators (MCP tools, SKILL.md files, agent configs).

    # Collect tags from all parents
    all_tags = list({tag for c in caps for tag in c.tags})
    # Use domain from first parent (caps is always non-empty here — KeyError raised above otherwise)
    domain = caps[0].domain

    new_cap = CapabilityDescriptor(
        name=result.name,
        description=result.description,
        capability_type=CapabilityType.SKILL,
        domain=domain,
        tags=all_tags,
    )
    registry.add(new_cap)
    metrics_store.record_event(
        result.name, "created", f"merged from {capability_ids}"
    )

    # Deactivate parents
    for cid in capability_ids:
        registry.deactivate(cid)
        metrics_store.record_event(cid, "merged", f"into {result.name}")

    logger.info("Merged %s into '%s'", capability_ids, result.name)
    return result.name
