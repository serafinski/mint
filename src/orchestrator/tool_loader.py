"""Registration helper for generated MCP tool artifacts.

Generated MCP source is not imported or executed in this host process.
Runtime execution goes through the Docker sandbox paths; this module only
turns a generated artifact into a registry descriptor with the metadata needed
for later sandbox execution and retrieval.
"""

from __future__ import annotations

import logging

from src.orchestrator.schema_extraction import extract_mcp_tool_signature
from src.registry.token_count import count_definition_tokens
from src.schemas.capability import CapabilityDescriptor, CapabilityType
from src.schemas.task import GeneratedArtifact

logger = logging.getLogger(__name__)


class ToolLoader:
    """Register generated MCP artifacts without host-side code execution."""

    def register_tool(
        self,
        artifact: GeneratedArtifact,
        registry,  # CapabilityRegistry — not imported at module level to avoid circular deps
        *,
        description: str | None = None,
        domain: str = "generated",
        tags: list[str] | None = None,
    ) -> CapabilityDescriptor:
        """Register a generated MCP tool in the capability registry.

        Builds a ``CapabilityDescriptor`` from the artifact, persists the
        generated source code in descriptor metadata, and calls
        ``registry.add()``. The source code is stored for later Docker sandbox
        execution; it is not imported or run here.

        Args:
            artifact: The generated artifact, which must be ``MCP_TOOL``.
            registry: A ``CapabilityRegistry`` instance.
            description: Human-readable description. Defaults to the
                artifact's description, then a humanized capability name.
            domain: Fallback taxonomy domain when the artifact has no domain.
            tags: Additional searchable tags.

        Returns:
            The registered ``CapabilityDescriptor``.
        """
        if artifact.capability_type != CapabilityType.MCP_TOOL:
            raise ValueError(
                f"register_tool only supports MCP_TOOL artifacts, "
                f"got {artifact.capability_type.value}"
            )

        # Populate input_schema / output_schema for compatibility-aware reuse.
        # Priority order:
        #   1. Prefer artifact-provided schemas if the generator set them.
        #   2. Fall back to AST extraction from generated source code.
        # AST extraction never raises; it returns ({}, {}) on parse failure.
        input_schema: dict = dict(artifact.input_schema or {})
        output_schema: dict = dict(artifact.output_schema or {})
        if (not input_schema or not output_schema) and artifact.source_code:
            ast_inputs, ast_outputs = extract_mcp_tool_signature(artifact.source_code)
            if not input_schema:
                input_schema = dict(ast_inputs)
            if not output_schema:
                output_schema = dict(ast_outputs)

        # Forward domain / is_generic from artifact if populated. If the
        # generator has not set them yet, use the fallback domain and
        # is_generic=False.
        effective_domain = artifact.domain if artifact.domain is not None else domain
        effective_is_generic = bool(artifact.is_generic)

        fallback_desc = artifact.capability_name.replace("-", " ").replace("_", " ")
        resolved_description = description or artifact.description or fallback_desc
        definition_token_cost = count_definition_tokens(
            artifact.model_copy(
                update={
                    "description": resolved_description,
                    "input_schema": input_schema,
                    "output_schema": output_schema,
                }
            )
        )
        descriptor = CapabilityDescriptor(
            name=artifact.capability_name,
            description=resolved_description,
            capability_type=CapabilityType.MCP_TOOL,
            domain=effective_domain,
            is_generic=effective_is_generic,
            tags=tags or [],
            input_schema=input_schema,
            output_schema=output_schema,
            definition_token_cost=definition_token_cost,
            # Preserve declared runtime dependencies so registry-matched MCP
            # tools can install them during sandbox execution.
            pip_packages=list(getattr(artifact, "pip_packages", []) or []),
            metadata={
                "generated": "true",
                "source": "jit_pipeline",
                **(
                    {"source_code": artifact.source_code}
                    if artifact.source_code
                    else {}
                ),
            },
        )
        registry.add(descriptor)
        logger.info(
            "Registered MCP tool %r in registry (domain=%s, is_generic=%s, "
            "input_schema=%d keys, output_schema=%d keys)",
            artifact.capability_name,
            effective_domain,
            effective_is_generic,
            len(input_schema),
            len(output_schema),
        )
        return descriptor
