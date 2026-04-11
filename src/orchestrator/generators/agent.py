"""Agent generator — LLM-based agent configuration synthesis.

Generates agent configurations (system prompt, LangGraph node config, and
optionally a DeepAgents SKILL.md) for capability gaps classified as AGENT.

This is a plain LangGraph node (no DeepAgent) because agent config generation
is a single-step LLM task with structured output.
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
import threading
from pathlib import Path
from typing import Literal

from langgraph.types import Command
from pydantic import BaseModel, Field

from src.orchestrator.generators.skill import extract_skill_name, validate_skill_content
from src.orchestrator.generators.utils import build_task_context, slugify_gap_hyphen
from src.orchestrator.prompts.agent_generator import (
    AGENT_GENERATOR_PROMPT,
    AGENT_LEAD_SUBAGENTS_PROMPT,
    AGENT_LEAD_SUBAGENTS_REPAIR_PROMPT,
    AGENT_REPAIR_PROMPT,
)
from src.orchestrator.tracing import emit_langfuse_event
from src.schemas.capability import CapabilityType
from src.schemas.task import ClassifiedGap, GeneratedArtifact, TaskState, TaskStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Skills output directory (mirrors skill.py — generated DeepAgent skills land here)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
SKILLS_OUTPUT_DIR = _PROJECT_ROOT / "data" / "eval" / "skills"


# ---------------------------------------------------------------------------
# Internal structured-output model (not part of src/schemas/)
# ---------------------------------------------------------------------------


class AgentGenerationResult(BaseModel):
    """LLM output schema for agent configuration generation."""

    agent_name: str = Field(..., description="Hyphen-case agent identifier (max 64 chars)")
    system_prompt: str = Field(..., description="Markdown system prompt for the agent")
    node_name: str = Field(..., description="Snake_case LangGraph node function name")
    model: str = Field(
        default="",
        description="LLM model identifier (empty = system default)",
    )
    description: str = Field(..., description="One-sentence summary of what the agent does")
    is_deep_agent: bool = Field(
        default=False,
        description="Whether this agent requires DeepAgent (multi-step autonomous reasoning)",
    )
    skill_content: str | None = Field(
        default=None,
        description="SKILL.md content if is_deep_agent is true, otherwise null",
    )

    model_config = {"extra": "forbid"}


class SubAgentConfig(BaseModel):
    """Configuration for a single subagent within a lead-with-subagents composition."""

    name: str = Field(..., description="Hyphen-case subagent identifier")
    description: str = Field(..., description="What to delegate to this subagent (action-oriented)")
    system_prompt: str = Field(..., description="The subagent's instructions")
    tools: list[str] = Field(
        default_factory=lambda: ["sandbox_execute"],
        description="Tool names available to this subagent",
    )

    model_config = {"extra": "forbid"}


class LeadWithSubagentsResult(BaseModel):
    """LLM output schema for lead-with-subagents agent generation.

    Used when ≥2 gaps are classified as AGENT. The lead coordinates
    via the ``task`` tool; each subagent runs in isolated context.
    """

    agent_name: str = Field(..., description="Hyphen-case lead agent identifier (max 64 chars)")
    system_prompt: str = Field(..., description="Lead agent system prompt — must use task() for delegation")
    node_name: str = Field(..., description="Snake_case LangGraph node function name")
    model: str = Field(default="", description="LLM model identifier (empty = system default)")
    description: str = Field(..., description="One-sentence summary of the lead agent's role")
    is_deep_agent: bool = Field(default=True, description="Always true for lead-with-subagents")
    skill_content: str | None = Field(
        default=None,
        description="SKILL.md content for the lead agent",
    )
    subagents: list[SubAgentConfig] = Field(
        ...,
        min_length=1,
        description="Subagent configurations — one per delegated step",
    )

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Shared LLM instance (lazy initialisation)
# ---------------------------------------------------------------------------

MAX_GENERATION_WORKERS = 4

_agent_gen_llm = None
_agent_llm_lock = threading.Lock()


def _get_llm():
    """Lazily create the agent generator LLM on first use (thread-safe)."""
    global _agent_gen_llm  # noqa: PLW0603
    if _agent_gen_llm is None:
        with _agent_llm_lock:
            if _agent_gen_llm is None:
                from langchain.chat_models import init_chat_model

                from src.config import DEFAULT_LLM_MODEL

                _agent_gen_llm = init_chat_model(DEFAULT_LLM_MODEL, request_timeout=300)
    return _agent_gen_llm


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_HYPHEN_CASE_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SNAKE_CASE_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


# ---------------------------------------------------------------------------
# LangFuse event helper
# ---------------------------------------------------------------------------


def _validate_result(result: AgentGenerationResult) -> list[str]:
    """Return a list of validation error strings (empty if valid)."""
    errors: list[str] = []

    if not result.system_prompt.strip():
        errors.append("system_prompt must be non-empty")

    agent_name_valid = False
    if not result.agent_name.strip():
        errors.append("agent_name must be non-empty")
    elif not _HYPHEN_CASE_RE.match(result.agent_name):
        errors.append(
            f"agent_name '{result.agent_name}' must be hyphen-case "
            "(lowercase letters, digits, hyphens; no leading/trailing/double hyphens)"
        )
    elif len(result.agent_name) > 64:
        errors.append(f"agent_name too long ({len(result.agent_name)} chars, max 64)")
    else:
        agent_name_valid = True

    if not result.node_name.strip():
        errors.append("node_name must be non-empty")
    elif not _SNAKE_CASE_RE.match(result.node_name):
        errors.append(f"node_name '{result.node_name}' must be valid snake_case")

    if not result.description.strip():
        errors.append("description must be non-empty")

    if result.is_deep_agent and not result.skill_content:
        errors.append("skill_content is required when is_deep_agent is true")

    if result.skill_content:
        if not result.skill_content.startswith("---"):
            errors.append("skill_content must start with YAML frontmatter (---)")
        elif not agent_name_valid:
            # agent_name failed validation above — it may contain path-unsafe
            # characters (e.g. "/"), which would crash validate_skill_content.
            # Skip skill_content validation; the agent_name error already captures this.
            errors.append(
                "skill_content validation skipped: agent_name is invalid"
            )
        else:
            # Run full quick_validate.py check (same as skill generator does)
            is_valid, msg = validate_skill_content(result.skill_content, result.agent_name)
            if not is_valid:
                errors.append(f"skill_content failed validation: {msg}")
            else:
                # Verify the frontmatter name matches agent_name so DeepAgents can load the skill
                content_name = extract_skill_name(result.skill_content)
                if content_name and content_name != result.agent_name:
                    errors.append(
                        f"skill_content frontmatter name '{content_name}' must match "
                        f"agent_name '{result.agent_name}'"
                    )

    return errors


# ---------------------------------------------------------------------------
# Single-agent generation
# ---------------------------------------------------------------------------


def _generate_single_agent(
    gap: ClassifiedGap,
    task_context: str,
    *,
    output_dir: Path | None = None,
) -> tuple[GeneratedArtifact | None, str | None]:
    """Generate an agent config for a single classified gap.

    Returns (artifact, error). Exactly one will be non-None.
    """
    forced_name = slugify_gap_hyphen(gap.query)

    llm = _get_llm()
    structured_llm = llm.with_structured_output(AgentGenerationResult)

    from src.config import DEFAULT_LLM_MODEL

    prompt = AGENT_GENERATOR_PROMPT.format(
        task_context=task_context or gap.description,
        gap_description=gap.description or gap.query,
        forced_name=forced_name,
        model_default=DEFAULT_LLM_MODEL,
    )

    # No per-node CallbackHandler: invoked from a LangGraph node so the pipeline-level
    # handler propagates automatically via LangChain's contextvar inheritance.

    # First attempt
    try:
        result: AgentGenerationResult = structured_llm.invoke(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agent generation LLM call failed for %r: %s", gap.query, exc)
        return None, f"Agent generation failed for '{gap.query}': {exc}"

    # Validate
    validation_errors = _validate_result(result)

    # Repair pass (one attempt)
    if validation_errors:
        logger.warning(
            "Validation failed for agent %r — triggering repair pass. "
            "Errors: %s. Malformed output: %s",
            gap.query,
            validation_errors,
            result.model_dump_json(),
        )
        emit_langfuse_event(
            None,
            "agent.validation_failure",
            "WARNING",
            {"gap_query": gap.query, "errors": validation_errors},
        )
        repair_prompt = AGENT_REPAIR_PROMPT.format(
            original_prompt=prompt,
            validation_errors="\n".join(f"- {e}" for e in validation_errors),
            previous_output=result.model_dump_json(indent=2),
        )
        try:
            result = structured_llm.invoke(repair_prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Agent generation repair failed for %r: %s", gap.query, exc)
            emit_langfuse_event(
                None,
                "agent.repair_llm_failure",
                "ERROR",
                {"gap_query": gap.query, "error": str(exc)},
            )
            return None, f"Agent generation repair failed for '{gap.query}': {exc}"

        validation_errors = _validate_result(result)
        if validation_errors:
            error_str = "; ".join(validation_errors)
            logger.warning(
                "Agent generation still invalid after repair for %r: %s",
                gap.query,
                error_str,
            )
            emit_langfuse_event(
                None,
                "agent.repair_validation_failure",
                "ERROR",
                {"gap_query": gap.query, "errors": validation_errors},
            )
            return None, f"Agent generation invalid after repair for '{gap.query}': {error_str}"

    # Build the agent_config dict
    node_config = {
        "name": result.node_name,
        "model": result.model,
        "description": result.description,
    }

    agent_config: dict = {
        "system_prompt": result.system_prompt,
        "node_config": node_config,
        "is_deep_agent": result.is_deep_agent,
    }

    if result.is_deep_agent and result.skill_content:
        # Rewrite the frontmatter name to forced_name so the directory name
        # matches the SKILL.md name field (DeepAgents expects these to match).
        if result.agent_name != forced_name:
            result.skill_content = re.sub(
                r"(?m)^(name:\s*).+$",
                rf"\g<1>{forced_name}",
                result.skill_content,
                count=1,
            )

        agent_config["skill_content"] = result.skill_content
        # Write the DeepAgent skill to disk so the DeepAgents runtime can load it.
        skill_dir = (output_dir or SKILLS_OUTPUT_DIR) / forced_name
        try:
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(result.skill_content)
        except OSError as exc:
            error_msg = (
                f"Failed to write DeepAgent skill '{forced_name}' to disk: {exc}"
            )
            logger.error(error_msg)
            return None, error_msg
        logger.info("Wrote DeepAgent skill '%s' to %s", forced_name, skill_dir)

    # Use deterministic slug for the artifact metadata and disk path.
    if result.agent_name != forced_name:
        logger.debug(
            "Artifact metadata uses stable slug '%s' (LLM chose '%s'; disk file unchanged)",
            forced_name, result.agent_name,
        )

    artifact = GeneratedArtifact(
        capability_name=forced_name,
        capability_type=CapabilityType.AGENT,
        agent_config=agent_config,
        description=result.description,
        gap_query=gap.query,
    )

    return artifact, None


# ---------------------------------------------------------------------------
# Lead-with-subagents generation (≥2 AGENT gaps)
# ---------------------------------------------------------------------------


def _validate_lead_result(result: LeadWithSubagentsResult) -> list[str]:
    """Validate a lead-with-subagents result. Returns error strings (empty if valid)."""
    # Reuse base validation for the lead fields
    base_result = AgentGenerationResult(
        agent_name=result.agent_name,
        system_prompt=result.system_prompt,
        node_name=result.node_name,
        model=result.model,
        description=result.description,
        is_deep_agent=result.is_deep_agent,
        skill_content=result.skill_content,
    )
    errors = _validate_result(base_result)

    if not result.is_deep_agent:
        errors.append("is_deep_agent must be true for lead-with-subagents")

    # Validate subagents
    if not result.subagents:
        errors.append("subagents list must not be empty")
    else:
        seen_names: set[str] = set()
        for i, sub in enumerate(result.subagents):
            if not sub.name.strip():
                errors.append(f"subagents[{i}].name must be non-empty")
            elif not _HYPHEN_CASE_RE.match(sub.name):
                errors.append(
                    f"subagents[{i}].name '{sub.name}' must be hyphen-case"
                )
            if sub.name in seen_names:
                errors.append(f"subagents[{i}].name '{sub.name}' is duplicated")
            seen_names.add(sub.name)
            if not sub.system_prompt.strip():
                errors.append(f"subagents[{i}].system_prompt must be non-empty")
            if not sub.description.strip():
                errors.append(f"subagents[{i}].description must be non-empty")

    return errors


def _generate_lead_with_subagents(
    gaps: list[ClassifiedGap],
    task_context: str,
    *,
    output_dir: Path | None = None,
) -> tuple[GeneratedArtifact | None, str | None]:
    """Generate a lead agent with subagents for multiple AGENT gaps.

    Returns (artifact, error). The artifact's ``agent_config["subagents"]``
    contains the subagent dicts and ``agent_config["subagent_gap_queries"]``
    lists all covered gap queries for resolution-map indexing.
    """
    forced_name = slugify_gap_hyphen(gaps[0].query)

    gap_descriptions = "\n\n".join(
        f"Step {i + 1}: {g.description or g.query}"
        for i, g in enumerate(gaps)
    )

    llm = _get_llm()
    structured_llm = llm.with_structured_output(LeadWithSubagentsResult)

    from src.config import DEFAULT_LLM_MODEL

    prompt = AGENT_LEAD_SUBAGENTS_PROMPT.format(
        task_context=task_context or gaps[0].description,
        gap_descriptions=gap_descriptions,
        forced_name=forced_name,
        model_default=DEFAULT_LLM_MODEL,
    )

    # First attempt
    try:
        result: LeadWithSubagentsResult = structured_llm.invoke(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Lead-with-subagents generation failed: %s", exc)
        return None, f"Lead-with-subagents generation failed: {exc}"

    # Validate
    validation_errors = _validate_lead_result(result)

    # Repair pass (one attempt)
    if validation_errors:
        logger.warning(
            "Lead-with-subagents validation failed — repair pass. Errors: %s",
            validation_errors,
        )
        emit_langfuse_event(
            None,
            "agent.lead_subagents_validation_failure",
            "WARNING",
            {"errors": validation_errors},
        )
        repair_prompt = AGENT_LEAD_SUBAGENTS_REPAIR_PROMPT.format(
            original_prompt=prompt,
            validation_errors="\n".join(f"- {e}" for e in validation_errors),
            previous_output=result.model_dump_json(indent=2),
        )
        try:
            result = structured_llm.invoke(repair_prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Lead-with-subagents repair failed: %s", exc)
            return None, f"Lead-with-subagents repair failed: {exc}"

        validation_errors = _validate_lead_result(result)
        if validation_errors:
            error_str = "; ".join(validation_errors)
            logger.warning("Lead-with-subagents still invalid after repair: %s", error_str)
            return None, f"Lead-with-subagents invalid after repair: {error_str}"

    # Build agent_config with subagents
    node_config = {
        "name": result.node_name,
        "model": result.model,
        "description": result.description,
    }

    subagent_dicts = [sub.model_dump() for sub in result.subagents]

    agent_config: dict = {
        "system_prompt": result.system_prompt,
        "node_config": node_config,
        "is_deep_agent": True,
        "subagents": subagent_dicts,
        "subagent_gap_queries": [g.query for g in gaps],
    }

    if result.skill_content:
        if result.agent_name != forced_name:
            result.skill_content = re.sub(
                r"(?m)^(name:\s*).+$",
                rf"\g<1>{forced_name}",
                result.skill_content,
                count=1,
            )
        agent_config["skill_content"] = result.skill_content
        skill_dir = (output_dir or SKILLS_OUTPUT_DIR) / forced_name
        try:
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(result.skill_content)
        except OSError as exc:
            error_msg = f"Failed to write lead skill '{forced_name}' to disk: {exc}"
            logger.error(error_msg)
            return None, error_msg
        logger.info("Wrote lead+subagents skill '%s' to %s", forced_name, skill_dir)

    if result.agent_name != forced_name:
        logger.debug(
            "Lead artifact uses stable slug '%s' (LLM chose '%s')",
            forced_name, result.agent_name,
        )

    artifact = GeneratedArtifact(
        capability_name=forced_name,
        capability_type=CapabilityType.AGENT,
        agent_config=agent_config,
        description=result.description,
        gap_query=gaps[0].query,
    )

    return artifact, None


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------


def generate_agent(
    state: TaskState,
) -> Command[Literal["verification_node"]]:
    """Generate agent configurations for all AGENT-typed gaps.

    When ≥2 AGENT gaps exist, uses lead-with-subagents mode: ONE lead
    agent with N subagents for context isolation. When 1 AGENT gap exists,
    generates a standalone agent as before.

    Routes to ``verification_node`` (final stage of the sequential
    generator chain).
    """
    artifacts: list[GeneratedArtifact] = list(state.generated_artifacts)
    errors: list[str] = list(state.errors)

    agent_gaps = [
        g for g in state.classified_gaps if g.classified_type == CapabilityType.AGENT
    ]

    # On retry, skip gaps already covered by passing artifacts.
    existing_gap_queries = {a.gap_query for a in artifacts if a.gap_query is not None}
    # Also check subagent_gap_queries on existing artifacts
    for a in artifacts:
        if a.agent_config and a.agent_config.get("subagent_gap_queries"):
            existing_gap_queries.update(a.agent_config["subagent_gap_queries"])

    gaps_to_generate = [g for g in agent_gaps if g.query not in existing_gap_queries]
    for skipped in agent_gaps:
        if skipped.query in existing_gap_queries:
            logger.info("  → skipping (already passed verification): %s", skipped.query)

    logger.info("generate_agent: %d gap(s) to generate (%d skipped)", len(gaps_to_generate), len(agent_gaps) - len(gaps_to_generate))

    if not gaps_to_generate:
        return Command(
            update={
                "generated_artifacts": artifacts,
                "status": TaskStatus.GENERATING,
                "errors": errors,
            },
            goto="verification_node",
        )

    task_context = build_task_context(state)

    # Lead-with-subagents mode: ≥2 AGENT gaps → ONE lead + N subagents
    if len(gaps_to_generate) >= 2:
        logger.info(
            "generate_agent: %d AGENT gaps → lead-with-subagents mode",
            len(gaps_to_generate),
        )
        artifact, error = _generate_lead_with_subagents(
            gaps_to_generate, task_context,
        )
        if artifact is not None:
            artifacts = [a for a in artifacts if a.capability_name != artifact.capability_name]
            artifacts.append(artifact)
        if error is not None:
            errors.append(error)
            # Fallback: try generating standalone agents individually
            logger.warning(
                "Lead-with-subagents failed; falling back to standalone generation"
            )
            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_GENERATION_WORKERS) as pool:
                futures = {
                    pool.submit(_generate_single_agent, gap, task_context): gap
                    for gap in gaps_to_generate
                }
                for future in concurrent.futures.as_completed(futures):
                    try:
                        art, err = future.result()
                    except Exception as exc:
                        gap = futures[future]
                        err = f"Generation failed for agent {gap.query!r}: {exc}"
                        art = None
                    if art is not None:
                        artifacts = [a for a in artifacts if a.capability_name != art.capability_name]
                        artifacts.append(art)
                    if err is not None:
                        errors.append(err)
    else:
        # Single AGENT gap → standalone agent (existing behavior)
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_GENERATION_WORKERS) as pool:
            futures = {
                pool.submit(_generate_single_agent, gap, task_context): gap
                for gap in gaps_to_generate
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    art, err = future.result()
                except Exception as exc:
                    gap = futures[future]
                    err = f"Generation failed for agent {gap.query!r}: {exc}"
                    art = None
                if art is not None:
                    artifacts = [a for a in artifacts if a.capability_name != art.capability_name]
                    artifacts.append(art)
                if err is not None:
                    errors.append(err)

    return Command(
        update={
            "generated_artifacts": artifacts,
            "status": TaskStatus.GENERATING,
            "errors": errors,
        },
        goto="verification_node",
    )
