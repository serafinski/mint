"""Integration tests for the MCP tool generator (real LLM calls).

Verifies that the MCP tool generator produces valid FastMCP code using the
real LLM (openai:gpt-5-mini), that generated tools can be registered for
sandbox execution, and that all calls appear in LangFuse.

Requires OPENAI_API_KEY environment variable to be set.
"""

from __future__ import annotations

import os
import re
from unittest.mock import MagicMock

import pytest

from src.orchestrator.generators.mcp_tool import generate_mcp_tool, generate_single_tool
from src.orchestrator.tool_loader import ToolLoader
from src.schemas.capability import CapabilityType
from src.schemas.task import ClassifiedGap, TaskState

_has_api_key = bool(os.environ.get("OPENAI_API_KEY"))

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _has_api_key, reason="OPENAI_API_KEY not set"),
]


@pytest.fixture(autouse=True)
def _reset_mcp_llm():
    """Reset the lazy-init LLM singleton so each test starts with a fresh model."""
    import src.orchestrator.generators.mcp_tool as mod

    mod._generator_llm = None
    yield
    mod._generator_llm = None


class TestMcpToolGeneratorLive:
    def test_generate_add_tool(self):
        """Real LLM generates a valid 'add two numbers' MCP tool."""
        gap = ClassifiedGap(
            description="Add two numbers and return their sum",
            query="add two numbers",
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.95,
            rationale="Stateless arithmetic operation",
        )

        artifact, error = generate_single_tool(gap, task_context="Math utilities")

        assert error is None, f"Generation failed: {error}"
        assert artifact is not None
        assert artifact.capability_type == CapabilityType.MCP_TOOL
        assert artifact.source_code is not None

        # Verify code compiles and has required structure
        compile(artifact.source_code, "<generated>", "exec")
        assert "Annotated" in artifact.source_code

        # Verify tool name is valid snake_case without a leading underscore.
        import re
        assert re.match(r"^[a-z][a-z0-9_]*$", artifact.capability_name), (
            f"capability_name '{artifact.capability_name}' is not valid snake_case"
        )

    def test_generate_and_register_tool(self):
        """Generate a tool and register it without host-side code execution."""
        gap = ClassifiedGap(
            description="Multiply two numbers and return their product",
            query="multiply two numbers",
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.95,
            rationale="Stateless arithmetic operation",
        )

        artifact, error = generate_single_tool(gap, task_context="Math utilities")
        assert error is None, f"Generation failed: {error}"
        assert artifact is not None

        loader = ToolLoader()
        registry = MagicMock()
        descriptor = loader.register_tool(artifact, registry)

        registry.add.assert_called_once()
        assert descriptor.name == artifact.capability_name
        assert descriptor.metadata["source_code"] == artifact.source_code

    def test_generate_mcp_tool_node(self):
        """generate_mcp_tool node processes MCP_TOOL gaps end-to-end with a real LLM."""
        state = TaskState(
            task_description="Build a math utilities library",
            classified_gaps=[
                ClassifiedGap(
                    description="Compute the absolute difference between two numbers",
                    query="absolute-difference",
                    classified_type=CapabilityType.MCP_TOOL,
                    confidence=0.95,
                    rationale="Stateless arithmetic operation",
                ),
            ],
        )

        cmd = generate_mcp_tool(state)

        assert len(cmd.update["generated_artifacts"]) == 1, (
            f"Expected 1 artifact, errors: {cmd.update['errors']}"
        )
        assert cmd.update["errors"] == []
        assert cmd.goto == "generate_skill"

        artifact = cmd.update["generated_artifacts"][0]
        assert artifact.capability_type == CapabilityType.MCP_TOOL
        assert artifact.source_code is not None

        # Verify generated code compiles and has FastMCP structure
        compile(artifact.source_code, "<generated>", "exec")
        assert "FastMCP" in artifact.source_code
        assert "@mcp.tool" in artifact.source_code
        assert "async def" in artifact.source_code
        assert "Annotated" in artifact.source_code

        # Verify tool name is valid snake_case without leading underscore
        assert re.match(r"^[a-z][a-z0-9_]*$", artifact.capability_name), (
            f"capability_name '{artifact.capability_name}' is not valid snake_case"
        )
