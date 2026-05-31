"""Unit tests for MCP tool registry registration."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.orchestrator.tool_loader import ToolLoader
from src.schemas.capability import CapabilityDescriptor, CapabilityType
from src.schemas.task import GeneratedArtifact

VALID_TOOL_SOURCE = '''\
"""MCP tool that adds two numbers."""

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

mcp = FastMCP("adder_mcp")


@mcp.tool(name="add")
async def add(
    a: Annotated[int, Field(description="First number")],
    b: Annotated[int, Field(description="Second number")],
) -> dict:
    """Add two numbers together."""
    return {"sum": a + b}
'''


def _artifact(**overrides) -> GeneratedArtifact:
    values = {
        "capability_name": "add-numbers",
        "capability_type": CapabilityType.MCP_TOOL,
        "source_code": VALID_TOOL_SOURCE,
    }
    values.update(overrides)
    return GeneratedArtifact(**values)


class TestRegisterTool:
    def setup_method(self):
        self.loader = ToolLoader()

    def test_register_mcp_tool(self):
        """register_tool calls registry.add() with a CapabilityDescriptor."""
        mock_registry = MagicMock()

        descriptor = self.loader.register_tool(_artifact(), mock_registry)

        mock_registry.add.assert_called_once()
        assert isinstance(descriptor, CapabilityDescriptor)
        assert descriptor.name == "add-numbers"
        assert descriptor.capability_type == CapabilityType.MCP_TOOL
        assert descriptor.metadata["source_code"] == VALID_TOOL_SOURCE

    def test_register_with_custom_description(self):
        """Explicit descriptions override artifact descriptions."""
        mock_registry = MagicMock()

        descriptor = self.loader.register_tool(
            _artifact(description="Artifact description"),
            mock_registry,
            description="Adds two integers",
        )

        assert descriptor.description == "Adds two integers"

    def test_register_uses_artifact_description(self):
        """Artifact descriptions are used when no explicit description is passed."""
        mock_registry = MagicMock()

        descriptor = self.loader.register_tool(
            _artifact(description="Parses CSV files and returns rows as dicts"),
            mock_registry,
        )

        assert descriptor.description == "Parses CSV files and returns rows as dicts"

    def test_register_falls_back_to_name_when_no_description(self):
        """Capability names are humanized when no description is available."""
        mock_registry = MagicMock()

        descriptor = self.loader.register_tool(
            _artifact(capability_name="add_numbers", description=None),
            mock_registry,
        )

        assert descriptor.description == "add numbers"

    def test_register_rejects_non_mcp_tool(self):
        """Only MCP tool artifacts can be registered through this helper."""
        mock_registry = MagicMock()
        artifact = GeneratedArtifact(
            capability_name="some-skill",
            capability_type=CapabilityType.SKILL,
            skill_content="---\nname: test\ndescription: test\n---\ncontent",
        )

        with pytest.raises(ValueError, match="MCP_TOOL"):
            self.loader.register_tool(artifact, mock_registry)

        mock_registry.add.assert_not_called()

    def test_register_extracts_schemas_from_source(self):
        """Missing schemas are inferred from generated MCP source code."""
        mock_registry = MagicMock()

        descriptor = self.loader.register_tool(_artifact(), mock_registry)

        assert descriptor.input_schema == {"a": "int", "b": "int"}
        assert descriptor.output_schema == {"sum": "any"}

    def test_register_preserves_artifact_schemas(self):
        """Generator-provided schemas win over AST extraction."""
        mock_registry = MagicMock()
        artifact = _artifact(
            input_schema={"left": "number"},
            output_schema={"result": "number"},
        )

        descriptor = self.loader.register_tool(artifact, mock_registry)

        assert descriptor.input_schema == {"left": "number"}
        assert descriptor.output_schema == {"result": "number"}

    def test_register_threads_taxonomy_fields(self):
        """Domain and generic flags from the artifact are preserved."""
        mock_registry = MagicMock()

        descriptor = self.loader.register_tool(
            _artifact(domain="sales", is_generic=True),
            mock_registry,
            domain="fallback",
        )

        assert descriptor.domain == "sales"
        assert descriptor.is_generic is True

    def test_register_uses_fallback_domain_when_artifact_domain_missing(self):
        """The caller-provided domain is used when the artifact has no taxonomy tag."""
        mock_registry = MagicMock()

        descriptor = self.loader.register_tool(
            _artifact(domain=None),
            mock_registry,
            domain="generated",
        )

        assert descriptor.domain == "generated"

    def test_register_threads_tags_and_pip_packages(self):
        """Search tags and runtime dependency metadata are preserved."""
        mock_registry = MagicMock()

        descriptor = self.loader.register_tool(
            _artifact(pip_packages=["pandas==2.2.0"]),
            mock_registry,
            tags=["math", "generated"],
        )

        assert descriptor.tags == ["math", "generated"]
        assert descriptor.pip_packages == ["pandas==2.2.0"]

    def test_invalid_source_does_not_execute_or_block_registration(self):
        """Registration stores source and falls back to empty inferred schemas."""
        mock_registry = MagicMock()
        source = "import definitely_missing_package\nnot valid python"

        descriptor = self.loader.register_tool(
            _artifact(source_code=source),
            mock_registry,
        )

        mock_registry.add.assert_called_once()
        assert descriptor.input_schema == {}
        assert descriptor.output_schema == {}
        assert descriptor.metadata["source_code"] == source
