"""Loading node — dynamically loads and registers generated MCP_TOOL artifacts.

Skills and agents are already written to disk during generation and do not
require loading.  This node handles only MCP_TOOL artifacts:

1. Load the module via importlib (``ToolLoader.load_tool``).
2. Smoke-test with empty inputs (``ToolLoader.execute_tool``) — log a warning
   on failure but still register, since Tier 2 behavioral testing (Iteration 3)
   will perform proper input-driven tests.
3. Register in the capability registry (``ToolLoader.register_tool``).
"""

from __future__ import annotations

import logging
from typing import Literal

from langgraph.graph import END
from langgraph.types import Command

from src.orchestrator.tool_loader import ToolLoader
from src.registry.store import SkillRegistry
from src.schemas.capability import CapabilityType
from src.schemas.task import TaskState, TaskStatus

logger = logging.getLogger(__name__)


def loading_node(
    state: TaskState,
    loader: ToolLoader,
    registry: SkillRegistry,
) -> Command[Literal["__end__"]]:
    """Load and register all MCP_TOOL artifacts produced by the generators.

    Skills and agents are skipped — they are written to disk during generation
    and the DeepAgents runtime picks them up from ``.deepagents/skills/``
    automatically.

    Sets ``status`` to:
    - ``COMPLETED`` — no gaps were detected (all capabilities matched), or at
      least one capability was successfully deployed: an MCP tool was registered
      OR a skill/agent artifact was written to disk during generation.
    - ``FAILED`` — gaps were detected but nothing was deployed: either no
      artifacts were generated at all, or all MCP tool load/register attempts
      failed and no skill/agent artifacts exist.

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
                artifact.capability_name, exc,
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

    # Skills and agents are deployed by being written to disk during generation;
    # if their artifacts appear in state.generated_artifacts the files are on disk.
    non_mcp_artifacts = [
        a for a in state.generated_artifacts if a.capability_type != CapabilityType.MCP_TOOL
    ]
    # "deployed" = MCP tools registered in registry + skills/agents written to disk.
    deployed = registered + len(non_mcp_artifacts)
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
