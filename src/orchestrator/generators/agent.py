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
from src.orchestrator.prompts.agent_generator import AGENT_GENERATOR_PROMPT, AGENT_REPAIR_PROMPT
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
# LangGraph node
# ---------------------------------------------------------------------------


def generate_agent(
    state: TaskState,
) -> Command[Literal["verification_node"]]:
    """Generate agent configurations for all AGENT-typed gaps.

    Skips gaps that are not classified as AGENT. On LLM failure, records
    the error and continues with the next gap.  Routes to ``verification_node``
    which is the final stage of the sequential generator chain.
    """
    artifacts: list[GeneratedArtifact] = list(state.generated_artifacts)
    errors: list[str] = list(state.errors)

    agent_gaps = [
        g for g in state.classified_gaps if g.classified_type == CapabilityType.AGENT
    ]

    # On retry, skip gaps already covered by passing artifacts.
    existing_gap_queries = {a.gap_query for a in artifacts if a.gap_query is not None}

    gaps_to_generate = [g for g in agent_gaps if g.query not in existing_gap_queries]
    for skipped in agent_gaps:
        if skipped.query in existing_gap_queries:
            logger.info("  → skipping (already passed verification): %s", skipped.query)

    logger.info("generate_agent: %d gap(s) to generate (%d skipped)", len(gaps_to_generate), len(agent_gaps) - len(gaps_to_generate))
    task_context = build_task_context(state)

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_GENERATION_WORKERS) as pool:
        futures = {
            pool.submit(_generate_single_agent, gap, task_context): gap
            for gap in gaps_to_generate
        }
        raw_results: list[tuple[GeneratedArtifact | None, str | None]] = []
        for future in concurrent.futures.as_completed(futures):
            try:
                raw_results.append(future.result())
            except Exception as exc:
                gap = futures[future]
                raw_results.append((None, f"Generation failed for agent {gap.query!r}: {exc}"))

    failed_queries: list[str] = []
    for artifact, error in raw_results:
        if artifact is not None:
            artifacts = [a for a in artifacts if a.capability_name != artifact.capability_name]
            artifacts.append(artifact)
        if error is not None:
            errors.append(error)
            failed_queries.append(error[:80])

    if failed_queries:
        logger.warning(
            "generate_agent: %d/%d gaps failed generation: %s",
            len(failed_queries), len(gaps_to_generate), failed_queries,
        )

    return Command(
        update={
            "generated_artifacts": artifacts,
            "status": TaskStatus.GENERATING,
            "errors": errors,
        },
        goto="verification_node",
    )
