"""Unit tests for the tool loader."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.orchestrator.tool_loader import LoadedTool, ToolLoader, ToolResult
from src.schemas.capability import CapabilityDescriptor, CapabilityType
from src.schemas.task import GeneratedArtifact

# ---------------------------------------------------------------------------
# Valid MCP tool source for testing
# ---------------------------------------------------------------------------

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
) -> int:
    """Add two numbers together."""
    return a + b


if __name__ == "__main__":
    mcp.run()
'''

TOOL_WITH_BOOL = '''\
"""MCP tool that takes a boolean flag and returns a string."""

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

mcp = FastMCP("bool_mcp")


@mcp.tool(name="greet")
async def greet(
    loud: Annotated[bool, Field(description="Whether to shout")],
) -> str:
    """Return a greeting, optionally loud."""
    return "HELLO" if loud else "hello"


if __name__ == "__main__":
    mcp.run()
'''

TOOL_THAT_RAISES = '''\
"""MCP tool that always raises."""

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

mcp = FastMCP("bad_mcp")


@mcp.tool(name="fail")
async def fail(
    x: Annotated[int, Field(description="Input")],
) -> int:
    """Always fails."""
    raise ValueError("Intentional error")


if __name__ == "__main__":
    mcp.run()
'''

SYNC_ONLY_TOOL_SOURCE = '''\
"""MCP tool with only synchronous functions — no async def."""

def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b
'''

TOOL_THAT_HANGS = '''\
"""MCP tool that takes forever."""

import asyncio
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

mcp = FastMCP("slow_mcp")


@mcp.tool(name="hang")
async def hang(
    x: Annotated[int, Field(description="Input")],
) -> int:
    """Hangs forever."""
    await asyncio.sleep(9999)
    return x


if __name__ == "__main__":
    mcp.run()
'''


# ---------------------------------------------------------------------------
# ToolResult schema
# ---------------------------------------------------------------------------


class TestToolResultSchema:
    def test_success_result(self):
        r = ToolResult(success=True, result=42)
        assert r.success is True
        assert r.result == 42

    def test_failure_result(self):
        r = ToolResult(success=False, error="Something broke")
        assert r.success is False
        assert r.error == "Something broke"


# ---------------------------------------------------------------------------
# ToolLoader.load_tool
# ---------------------------------------------------------------------------


class TestLoadTool:
    def setup_method(self):
        self.loader = ToolLoader()

    def teardown_method(self):
        self.loader.cleanup()

    def test_load_valid_tool(self):
        """Load a valid MCP tool and get back a LoadedTool."""
        artifact = GeneratedArtifact(
            capability_name="add_numbers",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
        )

        loaded = self.loader.load_tool(artifact)

        assert isinstance(loaded, LoadedTool)
        assert loaded.tool_name == "add_numbers"
        assert loaded.tool_fn is not None
        assert loaded.temp_path.exists()

    def test_reject_no_source_code(self):
        """Artifact with no source_code raises ValueError."""
        artifact = GeneratedArtifact(
            capability_name="empty_tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=None,
        )

        with pytest.raises(ValueError, match="no source_code"):
            self.loader.load_tool(artifact)

    def test_reject_non_mcp_tool(self):
        """Non-MCP_TOOL artifact raises ValueError."""
        artifact = GeneratedArtifact(
            capability_name="some_skill",
            capability_type=CapabilityType.SKILL,
            skill_content="# SKILL.md content",
        )

        with pytest.raises(ValueError, match="Expected MCP_TOOL"):
            self.loader.load_tool(artifact)

    def test_reject_invalid_source_code(self):
        """Source code that fails to execute raises RuntimeError."""
        artifact = GeneratedArtifact(
            capability_name="broken_tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code="import nonexistent_module_xyz_123",
        )

        with pytest.raises(RuntimeError, match="Failed to execute"):
            self.loader.load_tool(artifact)

    def test_load_tool_no_async_function(self):
        """Source code with no async functions raises RuntimeError."""
        artifact = GeneratedArtifact(
            capability_name="sync_only_tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=SYNC_ONLY_TOOL_SOURCE,
        )

        with pytest.raises(RuntimeError, match="No async tool function found"):
            self.loader.load_tool(artifact)

    def test_reject_invalid_module_name(self):
        """Capability name that produces an invalid Python identifier raises ValueError."""
        artifact = GeneratedArtifact(
            capability_name="123-invalid!name",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
        )

        with pytest.raises(ValueError, match="invalid"):
            self.loader.load_tool(artifact)


# ---------------------------------------------------------------------------
# ToolLoader.execute_tool
# ---------------------------------------------------------------------------


class TestExecuteTool:
    def setup_method(self):
        self.loader = ToolLoader()

    def teardown_method(self):
        self.loader.cleanup()

    def test_execute_valid_tool(self):
        """Execute a valid tool and get correct result."""
        artifact = GeneratedArtifact(
            capability_name="add_numbers",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
        )

        loaded = self.loader.load_tool(artifact)
        result = self.loader.execute_tool(loaded, {"a": 3, "b": 5})

        assert result.success is True
        assert result.result == 8
        assert result.error is None

    def test_execute_tool_that_raises(self):
        """Tool that raises an exception returns error in result."""
        artifact = GeneratedArtifact(
            capability_name="fail_tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=TOOL_THAT_RAISES,
        )

        loaded = self.loader.load_tool(artifact)
        result = self.loader.execute_tool(loaded, {"x": 1})

        assert result.success is False
        assert result.error is not None
        assert "Intentional error" in result.error

    def test_execute_tool_timeout(self):
        """Tool that hangs is killed by timeout."""
        artifact = GeneratedArtifact(
            capability_name="hang_tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=TOOL_THAT_HANGS,
        )

        loaded = self.loader.load_tool(artifact)
        result = self.loader.execute_tool(loaded, {"x": 1}, timeout=2)

        assert result.success is False
        assert "timed out" in result.error

    def test_execute_tool_nonzero_returncode(self):
        """Subprocess exiting with non-zero code returns ToolResult(success=False)."""
        artifact = GeneratedArtifact(
            capability_name="add_numbers",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
        )
        loaded = self.loader.load_tool(artifact)

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = "Segmentation fault"

        with patch("src.orchestrator.tool_loader.subprocess.run", return_value=mock_proc):
            result = self.loader.execute_tool(loaded, {"a": 1, "b": 2})

        assert result.success is False
        assert "Subprocess exited with code 1" in result.error
        assert "Segmentation fault" in result.error

    def test_execute_tool_empty_stdout_exits_zero(self):
        """Subprocess exits 0 with empty stdout returns ToolResult with parse error."""
        artifact = GeneratedArtifact(
            capability_name="add_numbers",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
        )
        loaded = self.loader.load_tool(artifact)

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        mock_proc.stderr = ""

        with patch("src.orchestrator.tool_loader.subprocess.run", return_value=mock_proc):
            result = self.loader.execute_tool(loaded, {"a": 1, "b": 2})

        assert result.success is False
        assert "Failed to parse tool output as JSON" in result.error

    def test_execute_tool_invalid_json_output(self):
        """Subprocess exits 0 but stdout is not JSON returns ToolResult with parse error."""
        artifact = GeneratedArtifact(
            capability_name="add_numbers",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
        )
        loaded = self.loader.load_tool(artifact)

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "this is not json"
        mock_proc.stderr = ""

        with patch("src.orchestrator.tool_loader.subprocess.run", return_value=mock_proc):
            result = self.loader.execute_tool(loaded, {"a": 1, "b": 2})

        assert result.success is False
        assert "Failed to parse tool output as JSON" in result.error

    def test_execute_tool_with_bool_input(self):
        """Boolean inputs are correctly passed (JSON true/false → Python True/False)."""
        artifact = GeneratedArtifact(
            capability_name="greet_tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=TOOL_WITH_BOOL,
        )

        loaded = self.loader.load_tool(artifact)

        result_loud = self.loader.execute_tool(loaded, {"loud": True})
        assert result_loud.success is True
        assert result_loud.result == "HELLO"

        result_quiet = self.loader.execute_tool(loaded, {"loud": False})
        assert result_quiet.success is True
        assert result_quiet.result == "hello"


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


class TestRegisterTool:
    def setup_method(self):
        self.loader = ToolLoader()

    def teardown_method(self):
        self.loader.cleanup()

    def test_register_mcp_tool(self):
        """register_tool calls registry.add() with a CapabilityDescriptor."""
        artifact = GeneratedArtifact(
            capability_name="add-numbers",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
        )
        mock_registry = MagicMock()

        descriptor = self.loader.register_tool(artifact, mock_registry)

        mock_registry.add.assert_called_once()
        assert isinstance(descriptor, CapabilityDescriptor)
        assert descriptor.name == "add-numbers"
        assert descriptor.capability_type == CapabilityType.MCP_TOOL

    def test_register_with_custom_description(self):
        """Custom description is passed through to the descriptor."""
        artifact = GeneratedArtifact(
            capability_name="add-numbers",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
        )
        mock_registry = MagicMock()

        descriptor = self.loader.register_tool(
            artifact, mock_registry, description="Adds two integers"
        )

        assert descriptor.description == "Adds two integers"

    def test_register_uses_artifact_description(self):
        """register_tool uses artifact.description when no explicit description given (Bug 1 fix)."""
        artifact = GeneratedArtifact(
            capability_name="csv_parser",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
            description="Parses CSV files and returns rows as dicts",
        )
        mock_registry = MagicMock()

        descriptor = self.loader.register_tool(artifact, mock_registry)

        assert descriptor.description == "Parses CSV files and returns rows as dicts"

    def test_register_falls_back_to_name_when_no_description(self):
        """Falls back to humanised capability_name when artifact.description is None."""
        artifact = GeneratedArtifact(
            capability_name="add_numbers",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
            description=None,
        )
        mock_registry = MagicMock()

        descriptor = self.loader.register_tool(artifact, mock_registry)

        assert descriptor.description == "add numbers"

    def test_register_rejects_non_mcp_tool(self):
        """register_tool raises ValueError for non-MCP_TOOL artifacts."""
        artifact = GeneratedArtifact(
            capability_name="some-skill",
            capability_type=CapabilityType.SKILL,
            skill_content="---\nname: test\ndescription: test\n---\ncontent",
        )
        mock_registry = MagicMock()

        with pytest.raises(ValueError, match="MCP_TOOL"):
            self.loader.register_tool(artifact, mock_registry)

        mock_registry.add.assert_not_called()


class TestCleanup:
    def test_cleanup_removes_multiple_temp_dirs(self):
        """cleanup() removes all temp dirs created across multiple load_tool calls."""
        loader = ToolLoader()
        artifacts = [
            GeneratedArtifact(
                capability_name="add_numbers",
                capability_type=CapabilityType.MCP_TOOL,
                source_code=VALID_TOOL_SOURCE,
            ),
            GeneratedArtifact(
                capability_name="greet_tool",
                capability_type=CapabilityType.MCP_TOOL,
                source_code=TOOL_WITH_BOOL,
            ),
        ]

        loaded_tools = [loader.load_tool(a) for a in artifacts]
        temp_dirs = [lt.temp_path.parent for lt in loaded_tools]
        assert all(d.exists() for d in temp_dirs)

        loader.cleanup()
        assert all(not d.exists() for d in temp_dirs)

    def test_temp_files_removed(self):
        """After cleanup(), temp directories are removed."""
        loader = ToolLoader()
        artifact = GeneratedArtifact(
            capability_name="add_numbers",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
        )

        loaded = loader.load_tool(artifact)
        temp_path = loaded.temp_path
        assert temp_path.exists()

        loader.cleanup()
        assert not temp_path.parent.exists()

    def test_cleanup_idempotent(self):
        """Calling cleanup() twice does not raise."""
        loader = ToolLoader()
        artifact = GeneratedArtifact(
            capability_name="add_numbers",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
        )
        loader.load_tool(artifact)

        loader.cleanup()
        loader.cleanup()  # Should not raise

    def test_atexit_registered_on_init(self):
        """ToolLoader.__init__ registers cleanup with atexit."""
        registered = []
        with patch("src.orchestrator.tool_loader.atexit.register", side_effect=registered.append):
            loader = ToolLoader()

        assert loader.cleanup in registered


# ---------------------------------------------------------------------------
# execute_tool — edge cases for stdout parsing and JSON format guards
# ---------------------------------------------------------------------------


class TestExecuteToolEdgeCases:
    def setup_method(self):
        self.loader = ToolLoader()

    def teardown_method(self):
        self.loader.cleanup()

    def _load_add(self):
        artifact = GeneratedArtifact(
            capability_name="add_numbers",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
        )
        return self.loader.load_tool(artifact)

    def test_execute_tool_non_dict_json_output(self):
        """JSON that is not a dict (e.g. a list) triggers the isinstance guard."""
        loaded = self._load_add()

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "[1, 2, 3]"
        mock_proc.stderr = ""

        with patch("src.orchestrator.tool_loader.subprocess.run", return_value=mock_proc):
            result = self.loader.execute_tool(loaded, {"a": 1, "b": 2})

        assert result.success is False
        assert "Unexpected output format" in result.error

    def test_execute_tool_non_dict_scalar_json(self):
        """A bare JSON number (not a dict) also triggers the isinstance guard."""
        loaded = self._load_add()

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "42"
        mock_proc.stderr = ""

        with patch("src.orchestrator.tool_loader.subprocess.run", return_value=mock_proc):
            result = self.loader.execute_tool(loaded, {"a": 1, "b": 2})

        assert result.success is False
        assert "Unexpected output format" in result.error

    def test_execute_tool_debug_print_before_json(self):
        """Tool that calls print() before returning still produces correct result.

        BUG-01 fix: stdout is parsed from the LAST non-empty line, not the whole blob.
        """
        loaded = self._load_add()

        # Simulate: tool printed debug line, then runner printed JSON
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = 'debug: adding 3 5\n{"success": true, "result": 8}'
        mock_proc.stderr = ""

        with patch("src.orchestrator.tool_loader.subprocess.run", return_value=mock_proc):
            result = self.loader.execute_tool(loaded, {"a": 3, "b": 5})

        assert result.success is True
        assert result.result == 8
