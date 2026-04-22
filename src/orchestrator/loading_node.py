"""Loading node — loads MCP_TOOL artifacts and registers all artifact types.

MCP_TOOL artifacts are dynamically loaded via importlib.  Skills and agents
are already written to disk during generation and do not require dynamic
loading, but all three types are registered in the capability registry here.

MCP_TOOL handling:

1. Load the module via importlib (``ToolLoader.load_tool``).
2. Smoke-test with empty inputs (``ToolLoader.execute_tool``) — log a warning
   on failure but still register.
3. Register in the capability registry (``ToolLoader.register_tool``).

SKILL / AGENT handling:

1. Register in the capability registry (already on disk from generation).
"""

from __future__ import annotations

import logging
from typing import Literal

from langgraph.graph import END
from langgraph.types import Command

from src.orchestrator.schema_extraction import (
    derive_input_schema_from_upstream,
    find_subtask_for_gap,
)
from src.orchestrator.tool_loader import ToolLoader
from src.orchestrator.tracing import emit_langfuse_event
from src.registry.store import SkillRegistry
from src.schemas.capability import CapabilityDescriptor, CapabilityType
from src.schemas.task import TaskState, TaskStatus


def _emit_registration_event(
    artifact_name: str,
    artifact_type: str,
    domain: str | None,
    is_generic: bool,
    *,
    passed: bool,
    error_msg: str | None = None,
) -> None:
    """Emit a registration event (Step 13.7 Batch C1).

    Traces capability lineage: Chapter 5's §3.5f evolutionary-taxonomy
    narrative + gap-detection search-quality analysis depend on knowing
    which caps entered the registry with which domain / is_generic flags.
    """
    emit_langfuse_event(
        None,
        f"registry.register_{'ok' if passed else 'error'}",
        "DEFAULT" if passed else "ERROR",
        {
            "capability_name": artifact_name,
            "capability_type": artifact_type,
            "domain": domain,
            "is_generic": is_generic,
            **({"error": error_msg} if error_msg else {}),
        },
    )

logger = logging.getLogger(__name__)


def loading_node(
    state: TaskState,
    loader: ToolLoader,
    registry: SkillRegistry,
) -> Command[Literal["__end__"]]:
    """Load MCP_TOOL artifacts and register all artifact types in the registry.

    MCP_TOOL: dynamically loaded via importlib, smoke-tested, then registered.
    SKILL/AGENT: already written to disk during generation — registered in the
    capability registry here so ``gap_detection`` can find them on future runs.

    Sets ``status`` to:
    - ``COMPLETED`` — no gaps were detected (all capabilities matched), or at
      least one capability was successfully registered in the registry.
    - ``FAILED`` — gaps were detected but no artifacts were registered (either
      none were generated, or all register attempts failed).

    Errors from individual tool loads/registrations are recorded in
    ``state.errors`` but do not abort the node — remaining tools continue.
    """
    errors: list[str] = list(state.errors)

    mcp_artifacts = [
        a for a in state.generated_artifacts if a.capability_type == CapabilityType.MCP_TOOL
    ]

    registered = 0
    # Security audit finding #1 (2026-04-19): pre-fix, this node called
    # ``loader.load_tool(artifact)`` which invoked ``spec.loader.exec_module``
    # in the current Python process — executing arbitrary top-level code
    # from LLM-generated source on the host, AFTER the three-tier gate had
    # signed off. The Docker-based gate (Tier 1.5 / Tier 2 / Tier 3) already
    # covers import + behavioural + adversarial checks in the sandbox; the
    # host import was a duplicate smoke test and the only host-execution
    # path for generated code. Removing it aligns the code with the
    # CLAUDE.md claim that MCP tools are "loaded via importlib dynamic
    # import in a sandboxed subprocess" and with the Pillar-2 trust
    # boundary the thesis claims. Finding #5's two ``dir()`` alphabetical-
    # selection sites in ``ToolLoader.load_tool`` / ``execute_tool`` are
    # simultaneously rendered unreachable from production flow; those
    # methods remain in ``tool_loader.py`` for the unit tests that
    # exercise them directly, marked as non-production paths.
    for artifact in mcp_artifacts:
        # --- Register (source_code is what downstream execution reads;
        # no host-side import or smoke test is needed — the sandbox is
        # the only execution path). ---
        try:
            loader.register_tool(artifact, registry)
            registered += 1
            logger.info("Registered tool '%s' in registry", artifact.capability_name)
            _emit_registration_event(
                artifact.capability_name, "mcp_tool",
                artifact.domain, bool(artifact.is_generic),
                passed=True,
            )
        except Exception as exc:  # noqa: BLE001
            error_msg = f"Failed to register tool '{artifact.capability_name}': {exc}"
            logger.error(error_msg)
            errors.append(error_msg)
            _emit_registration_event(
                artifact.capability_name, "mcp_tool",
                artifact.domain, bool(artifact.is_generic),
                passed=False, error_msg=str(exc),
            )

    # Register SKILL and AGENT artifacts in the registry for reuse.
    # They're already on disk (written during generation), but without
    # registry entries gap_detection can't find them on future runs.
    non_mcp_artifacts = [
        a for a in state.generated_artifacts if a.capability_type != CapabilityType.MCP_TOOL
    ]
    for artifact in non_mcp_artifacts:
        try:
            metadata: dict[str, str] = {
                "generated": "true",
                "source": "jit_pipeline",
            }
            if artifact.skill_content:
                metadata["skill_content"] = artifact.skill_content
            if artifact.agent_config:
                import json

                metadata["agent_config"] = (
                    json.dumps(artifact.agent_config)
                    if isinstance(artifact.agent_config, dict)
                    else str(artifact.agent_config)
                )

            # Populate input_schema / output_schema for Pillar 3 Merge (B1).
            # Priority order:
            #   1. Prefer artifact-provided schemas if the generator set them.
            #   2. Fall back to deriving from the matching SubTask using the
            #      decomposer's output_schema (Phase 3.5c). Input contract
            #      for middle/last steps comes from upstream subtasks.
            # SKILL/AGENT artifacts have no source code to AST-extract, so
            # subtask-based derivation is the only fallback.
            input_schema: dict = dict(artifact.input_schema or {})
            output_schema: dict = dict(artifact.output_schema or {})
            if not input_schema or not output_schema:
                matched_subtask, _ = find_subtask_for_gap(
                    artifact.gap_query, state.subtasks
                )
                if matched_subtask is not None:
                    if not output_schema and matched_subtask.output_schema:
                        output_schema = dict(matched_subtask.output_schema)
                    if not input_schema:
                        derived_inputs = derive_input_schema_from_upstream(
                            matched_subtask, state.subtasks
                        )
                        if derived_inputs:
                            input_schema = derived_inputs

            # Forward domain / is_generic from the artifact if the generator
            # set them (B1 Step 3.5f evolutionary taxonomy). Fall back to the
            # historical "generated" default when the generator hasn't run
            # through taxonomy classification yet (pre-3.5f integration, or
            # when the taxonomy call failed and returned a fallback).
            effective_domain = (
                artifact.domain if artifact.domain is not None else "generated"
            )
            effective_is_generic = bool(artifact.is_generic)

            from src.registry.token_count import count_definition_tokens

            definition_token_cost = count_definition_tokens(
                artifact.model_copy(
                    update={
                        "input_schema": input_schema,
                        "output_schema": output_schema,
                    }
                )
            )
            descriptor = CapabilityDescriptor(
                name=artifact.capability_name,
                description=artifact.description or artifact.capability_name,
                capability_type=artifact.capability_type,
                domain=effective_domain,
                is_generic=effective_is_generic,
                input_schema=input_schema,
                output_schema=output_schema,
                definition_token_cost=definition_token_cost,
                metadata=metadata,
            )
            registry.add(descriptor)
            registered += 1
            logger.info(
                "Registered %s '%s' in registry",
                artifact.capability_type.value,
                artifact.capability_name,
            )
            _emit_registration_event(
                artifact.capability_name,
                artifact.capability_type.value,
                effective_domain, effective_is_generic,
                passed=True,
            )
        except Exception as exc:  # noqa: BLE001
            error_msg = (
                f"Failed to register {artifact.capability_type.value} "
                f"'{artifact.capability_name}': {exc}"
            )
            logger.error(error_msg)
            errors.append(error_msg)
            _emit_registration_event(
                artifact.capability_name,
                artifact.capability_type.value,
                getattr(artifact, "domain", None),
                bool(getattr(artifact, "is_generic", False)),
                passed=False, error_msg=str(exc),
            )

    # "deployed" = successfully registered in registry (MCP tools + SKILL/AGENT).
    deployed = registered
    new_errors = len(errors) - len(state.errors)

    # Gaps were detected but nothing was actually deployed → pipeline failed.
    # No gaps (all capabilities already matched) → success even with 0 artifacts.
    if state.gaps and deployed == 0:
        status = TaskStatus.FAILED
        logger.warning(
            "Loading complete: 0 capabilities deployed for %d detected gaps "
            "(%d artifacts generated but none successfully loaded/registered)",
            len(state.gaps),
            len(state.generated_artifacts),
        )
    else:
        status = TaskStatus.COMPLETED
        logger.info(
            "Loading complete: %d/%d MCP tools registered, "
            "%d skill/agent artifacts on disk, %d new errors",
            registered,
            len(mcp_artifacts),
            len(non_mcp_artifacts),
            new_errors,
        )

    return Command(
        update={
            "status": status,
            "errors": errors,
        },
        goto=END,
    )
