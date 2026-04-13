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
from src.registry.store import SkillRegistry
from src.schemas.capability import CapabilityDescriptor, CapabilityType
from src.schemas.task import TaskState, TaskStatus

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
    host_loadable = 0
    for artifact in mcp_artifacts:
        # --- Load (host importlib) ---
        loaded = None
        try:
            loaded = loader.load_tool(artifact)
            host_loadable += 1
        except Exception as exc:  # noqa: BLE001
            # Host import failed (e.g., missing pandas/numpy). The tool can
            # still be executed in the Docker sandbox which installs deps
            # dynamically. Register it with source_code so it's reusable.
            logger.warning(
                "Host import failed for '%s': %s (registering with source_code only)",
                artifact.capability_name,
                exc,
            )

        # --- Smoke-test (only if host-loadable) ---
        if loaded is not None:
            smoke = loader.execute_tool(loaded, inputs={})
            if not smoke.success:
                logger.warning(
                    "Smoke-test failed for tool '%s' (will still register): %s",
                    artifact.capability_name,
                    smoke.error,
                )

        # --- Register (always, regardless of host loadability) ---
        try:
            loader.register_tool(artifact, registry)
            registered += 1
            logger.info("Registered tool '%s' in registry", artifact.capability_name)
        except Exception as exc:  # noqa: BLE001
            error_msg = f"Failed to register tool '{artifact.capability_name}': {exc}"
            logger.error(error_msg)
            errors.append(error_msg)

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

            descriptor = CapabilityDescriptor(
                name=artifact.capability_name,
                description=artifact.description or artifact.capability_name,
                capability_type=artifact.capability_type,
                domain="generated",
                input_schema=input_schema,
                output_schema=output_schema,
                metadata=metadata,
            )
            registry.add(descriptor)
            registered += 1
            logger.info(
                "Registered %s '%s' in registry",
                artifact.capability_type.value,
                artifact.capability_name,
            )
        except Exception as exc:  # noqa: BLE001
            error_msg = (
                f"Failed to register {artifact.capability_type.value} "
                f"'{artifact.capability_name}': {exc}"
            )
            logger.error(error_msg)
            errors.append(error_msg)

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
            "Loading complete: %d/%d MCP tools registered (%d host-loadable), "
            "%d skill/agent artifacts on disk, %d new errors",
            registered,
            len(mcp_artifacts),
            host_loadable,
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
