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
    - ``COMPLETED`` — at least one artifact exists, or no gaps were detected
      (all capabilities were already in the registry).
    - ``FAILED`` — gaps were detected but no artifacts were generated at all
      (generation failed for every gap).

    Errors from individual tool loads/registrations are recorded in
    ``state.errors`` but do not abort the node — remaining tools continue.
    """
    errors: list[str] = list(state.errors)

    mcp_artifacts = [
        a for a in state.generated_artifacts if a.capability_type == CapabilityType.MCP_TOOL
    ]

    registered = 0
    for artifact in mcp_artifacts:
        # --- Load ---
        try:
            loaded = loader.load_tool(artifact)
        except Exception as exc:  # noqa: BLE001
            error_msg = f"Failed to load tool '{artifact.capability_name}': {exc}"
            logger.error(error_msg)
            errors.append(error_msg)
            continue

        # --- Smoke-test ---
        smoke = loader.execute_tool(loaded, inputs={})
        if not smoke.success:
            logger.warning(
                "Smoke-test failed for tool '%s' (will still register): %s",
                artifact.capability_name,
                smoke.error,
            )

        # --- Register ---
        try:
            loader.register_tool(artifact, registry)
            registered += 1
            logger.info("Registered tool '%s' in registry", artifact.capability_name)
        except Exception as exc:  # noqa: BLE001
            error_msg = f"Failed to register tool '{artifact.capability_name}': {exc}"
            logger.error(error_msg)
            errors.append(error_msg)

    total_artifacts = len(state.generated_artifacts)
    new_errors = len(errors) - len(state.errors)

    # Gaps were detected but nothing was generated → pipeline failed to produce
    # any capabilities.  No gaps (all matched) → success even with 0 artifacts.
    if state.gaps and total_artifacts == 0:
        status = TaskStatus.FAILED
        logger.warning(
            "Loading complete: no artifacts generated for %d detected gaps",
            len(state.gaps),
        )
    else:
        status = TaskStatus.COMPLETED
        logger.info(
            "Loading complete: %d/%d MCP tools registered, %d total artifacts, %d new errors",
            registered,
            len(mcp_artifacts),
            total_artifacts,
            new_errors,
        )

    return Command(
        update={
            "status": status,
            "errors": errors,
        },
        goto=END,
    )
