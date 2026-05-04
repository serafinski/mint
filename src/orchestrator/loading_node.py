"""Loading node - registers generated artifacts in the capability registry.

MCP_TOOL artifacts are registered from generated source without importing or
executing that source in the host process. Runtime execution happens through
the sandbox path. Skills and agents are already written to disk during
generation, but still need registry entries so future tasks can retrieve them.

MCP_TOOL handling:

1. Register source metadata in the capability registry via
   ``ToolLoader.register_tool``.
2. Avoid host-side import or smoke execution.

SKILL / AGENT handling:

1. Build a ``CapabilityDescriptor`` from artifact metadata and any matching
   decomposed subtask schemas.
2. Register the descriptor in the capability registry.
"""

from __future__ import annotations

import json
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
    """Emit a registration event for trace/provenance consumers.

    The event records which capabilities entered the registry and preserves
    taxonomy labels used by later registry retrieval.
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
    """Register generated artifacts in the capability registry.

    MCP_TOOL: registered from source metadata without host-side import.
    SKILL/AGENT: registered from artifact metadata and persisted content so
    ``gap_detection`` can find them on future runs.

    Sets ``status`` to:
    - ``COMPLETED`` — no gaps were detected (all capabilities matched), or at
      least one capability was successfully registered in the registry.
    - ``FAILED`` — gaps were detected but no artifacts were registered (either
      none were generated, or all register attempts failed).

    Errors from individual registrations are recorded in ``state.errors`` but
    do not abort the node; remaining artifacts continue.
    """
    errors: list[str] = list(state.errors)

    mcp_artifacts = [
        a for a in state.generated_artifacts if a.capability_type == CapabilityType.MCP_TOOL
    ]

    registered = 0
    mcp_registered = 0
    non_mcp_registered = 0
    # Do not import or smoke-test generated MCP source in this host process.
    # The verification gate and runtime sandbox own execution; this node only
    # persists the source metadata needed for sandbox execution and registry
    # retrieval.
    for artifact in mcp_artifacts:
        # source_code is what downstream sandbox execution reads.
        try:
            loader.register_tool(artifact, registry)
            registered += 1
            mcp_registered += 1
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
                metadata["agent_config"] = (
                    json.dumps(artifact.agent_config)
                    if isinstance(artifact.agent_config, dict)
                    else str(artifact.agent_config)
                )

            # Populate input_schema / output_schema for registry reuse.
            # Priority order:
            #   1. Prefer artifact-provided schemas if the generator set them.
            #   2. Fall back to deriving from the matching SubTask using the
            #      decomposer's output_schema. Input contract for middle/last
            #      steps comes from upstream subtasks.
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

            # Forward domain / is_generic from the artifact when available.
            # Fall back to the "generated" default if taxonomy classification
            # was skipped or returned a fallback.
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
                # Preserve generator-declared pip packages through registry
                # persistence and match reconstruction. Empty for SKILL/AGENT.
                pip_packages=list(getattr(artifact, "pip_packages", []) or []),
                metadata=metadata,
            )
            registry.add(descriptor)
            registered += 1
            non_mcp_registered += 1
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
            "(%d artifacts generated but none successfully registered)",
            len(state.gaps),
            len(state.generated_artifacts),
        )
    else:
        status = TaskStatus.COMPLETED
        logger.info(
            "Loading complete: %d/%d MCP tools registered, "
            "%d/%d skill/agent artifacts registered, %d new errors",
            mcp_registered,
            len(mcp_artifacts),
            non_mcp_registered,
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
