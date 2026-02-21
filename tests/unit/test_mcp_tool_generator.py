"""Unit tests for the MCP tool generator.

LLM-calling behaviour is covered by ``tests/integration/test_mcp_tool_generator.py``
with real DeepSeek calls.  These tests cover schema validation, the
``_validate_code`` helper, and the node's routing logic (patching
``generate_single_tool``, not the LLM).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.orchestrator.generators.mcp_tool import (
    GeneratedToolCode,
    _validate_code,
    _validate_tool_name,
    generate_mcp_tool,
    generate_single_tool,
)
from src.schemas.capability import CapabilityType
from src.schemas.task import (
    ClassifiedGap,
    GeneratedArtifact,
    TaskState,
    TaskStatus,
)

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

INVALID_TOOL_SOURCE = """\
def add(a, b)
    return a + b
"""


# ---------------------------------------------------------------------------
# GeneratedToolCode schema
# ---------------------------------------------------------------------------


class TestGeneratedToolCodeSchema:
    def test_valid(self):
        r = GeneratedToolCode(
            tool_name="add_numbers",
            source_code=VALID_TOOL_SOURCE,
            description="Add two numbers",
        )
        assert r.tool_name == "add_numbers"

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValueError):
            GeneratedToolCode(
                tool_name="add",
                source_code="x",
                description="d",
                extra_field="bad",
            )

    def test_roundtrip(self):
        r = GeneratedToolCode(
            tool_name="add",
            source_code="pass",
            description="A tool",
        )
        roundtripped = GeneratedToolCode.model_validate_json(r.model_dump_json())
        assert roundtripped.tool_name == "add"


# ---------------------------------------------------------------------------
# _validate_code
# ---------------------------------------------------------------------------


class TestValidateCode:
    def test_syntax_error(self):
        error = _validate_code("def f(\n")
        assert error is not None
        assert "SyntaxError" in error

    def test_valid_full_mcp_tool(self):
        assert _validate_code(VALID_TOOL_SOURCE) is None

    def test_missing_fastmcp(self):
        code = "async def add(a, b): return a + b"
        error = _validate_code(code)
        assert error is not None
        assert "FastMCP" in error

    def test_missing_mcp_tool_decorator(self):
        code = "from mcp.server.fastmcp import FastMCP\nmcp = FastMCP('t')\nasync def add(): pass"
        error = _validate_code(code)
        assert error is not None
        assert "@mcp.tool" in error

    def test_missing_async_def(self):
        code = (
            "from mcp.server.fastmcp import FastMCP\n"
            "mcp = FastMCP('t')\n"
            "@mcp.tool()\n"
            "def add(): pass"
        )
        error = _validate_code(code)
        assert error is not None
        assert "async" in error

    def test_missing_field_import(self):
        """Code using Annotated without Field is rejected (Field gives MCP rich metadata)."""
        code = (
            "from mcp.server.fastmcp import FastMCP\n"
            "from typing import Annotated\n"
            "mcp = FastMCP('t')\n"
            "@mcp.tool()\n"
            "async def add(a: Annotated[int, 'First number']) -> int:\n"
            "    return a\n"
        )
        error = _validate_code(code)
        assert error is not None
        assert "Field" in error

    def test_multiple_async_def_rejected(self):
        """Two top-level async defs are rejected (runner can't tell which is the tool)."""
        code = (
            "from mcp.server.fastmcp import FastMCP\n"
            "from typing import Annotated\n"
            "from pydantic import Field\n"
            "mcp = FastMCP('t')\n"
            "@mcp.tool()\n"
            "async def add(a: Annotated[int, Field(description='x')]) -> int:\n"
            "    return await helper(a)\n"
            "async def helper(x: int) -> int:\n"
            "    return x + 1\n"
        )
        error = _validate_code(code)
        assert error is not None
        assert "async def" in error

    def test_nested_async_def_not_rejected(self):
        """A tool with a nested async def inside its body is accepted.

        The runner discovers functions via ``dir(module)`` which only exposes
        module-level names; a nested coroutine never appears there and does not
        create ambiguity for the runner.  Using ``ast.iter_child_nodes`` (not
        ``ast.walk``) ensures we only count top-level async defs.
        """
        code = (
            "from mcp.server.fastmcp import FastMCP\n"
            "from typing import Annotated\n"
            "from pydantic import Field\n"
            "mcp = FastMCP('t')\n"
            "@mcp.tool()\n"
            "async def transform(n: Annotated[int, Field(description='x')]) -> int:\n"
            "    async def _helper(x: int) -> int:\n"
            "        return x * 2\n"
            "    return await _helper(n)\n"
        )
        error = _validate_code(code)
        assert error is None, f"Unexpected error for nested async def: {error}"

    def test_missing_annotated_params(self):
        code = (
            "from mcp.server.fastmcp import FastMCP\n"
            "mcp = FastMCP('t')\n"
            "@mcp.tool()\n"
            "async def add(a: int, b: int) -> int:\n"
            "    return a + b\n"
        )
        error = _validate_code(code)
        assert error is not None
        assert "Annotated" in error

    def test_multiple_structural_errors_reported_together(self):
        """All missing structural elements are reported in a single error string.

        This ensures the repair prompt gets complete information in one pass,
        rather than only the first error (which would require multiple rounds).
        """
        # Code with async def only — missing FastMCP, @mcp.tool, and Annotated
        code = "async def add(a: int, b: int) -> int:\n    return a + b\n"
        error = _validate_code(code)
        assert error is not None
        assert "FastMCP" in error
        assert "@mcp.tool" in error
        assert "Annotated" in error

    def test_async_def_in_string_literal_not_counted(self):
        """'async def' appearing only in a docstring is not counted as a function.

        AST-based counting (BUG-10 fix) avoids false positives from substring search.
        A docstring like '\"\"\"Example: async def foo()\"\"\"' must not trigger the
        'exactly 1 async def required' check.
        """
        code = (
            "from mcp.server.fastmcp import FastMCP\n"
            "from typing import Annotated\n"
            "from pydantic import Field\n"
            "mcp = FastMCP('t')\n"
            "@mcp.tool()\n"
            "async def add(\n"
            "    a: Annotated[int, Field(description='First number')],\n"
            ") -> int:\n"
            '    """Example usage: async def helper() — not a real function."""\n'
            "    return a\n"
        )
        # The docstring mentions "async def" but there is only one real async function
        error = _validate_code(code)
        assert error is None, f"Expected valid but got: {error}"


# ---------------------------------------------------------------------------
# _validate_tool_name
# ---------------------------------------------------------------------------


class TestValidateToolName:
    def test_valid_snake_case(self):
        assert _validate_tool_name("add_numbers") is None

    def test_valid_single_word(self):
        assert _validate_tool_name("add") is None

    def test_valid_with_digits(self):
        assert _validate_tool_name("base64_encode") is None

    def test_empty_name(self):
        error = _validate_tool_name("")
        assert error is not None
        assert "non-empty" in error

    def test_whitespace_only(self):
        error = _validate_tool_name("   ")
        assert error is not None
        assert "non-empty" in error

    def test_spaces_in_name(self):
        error = _validate_tool_name("Add Numbers")
        assert error is not None
        assert "snake_case" in error

    def test_hyphens_rejected(self):
        # tool_name must be snake_case (function name), not hyphen-case
        error = _validate_tool_name("add-numbers")
        assert error is not None
        assert "snake_case" in error

    def test_uppercase_rejected(self):
        error = _validate_tool_name("AddNumbers")
        assert error is not None
        assert "snake_case" in error

    def test_name_too_long(self):
        long_name = "a" * 65
        error = _validate_tool_name(long_name)
        assert error is not None
        assert "too long" in error

    def test_name_exactly_64_chars(self):
        name = "a" * 64
        assert _validate_tool_name(name) is None

    def test_starts_with_digit(self):
        # Python identifiers cannot start with a digit
        error = _validate_tool_name("1add_numbers")
        assert error is not None
        assert "snake_case" in error

    def test_starts_with_underscore(self):
        # Leading underscore causes the runner to skip the function (dir() filter),
        # resulting in silent execution failure — must be rejected at validation time.
        error = _validate_tool_name("_add_numbers")
        assert error is not None
        assert "snake_case" in error


# ---------------------------------------------------------------------------
# generate_mcp_tool node — routing logic (patches generate_single_tool)
# ---------------------------------------------------------------------------


class TestGenerateMcpToolNode:
    def test_filters_mcp_tool_gaps_only(self):
        """Node only processes MCP_TOOL gaps, skips SKILL and AGENT."""
        state = TaskState(
            task_description="Multi-gap task",
            classified_gaps=[
                ClassifiedGap(
                    description="Parse CSV file",
                    query="parse CSV",
                    classified_type=CapabilityType.MCP_TOOL,
                    confidence=0.9,
                    rationale="Atomic parse",
                ),
                ClassifiedGap(
                    description="Data cleaning workflow",
                    query="data cleaning",
                    classified_type=CapabilityType.SKILL,
                    confidence=0.85,
                    rationale="Multi-step",
                ),
                ClassifiedGap(
                    description="Investigation agent",
                    query="investigation",
                    classified_type=CapabilityType.AGENT,
                    confidence=0.8,
                    rationale="Needs planning",
                ),
            ],
        )

        mock_artifact = GeneratedArtifact(
            capability_name="csv_parser",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
        )

        with patch(
            "src.orchestrator.generators.mcp_tool.generate_single_tool",
            return_value=(mock_artifact, None),
        ) as mock_gen:
            cmd = generate_mcp_tool(state)

        # Only the MCP_TOOL gap should be processed
        mock_gen.assert_called_once()
        call_gap = mock_gen.call_args[0][0]
        assert call_gap.classified_type == CapabilityType.MCP_TOOL

        assert len(cmd.update["generated_artifacts"]) == 1
        assert cmd.update["status"] == TaskStatus.GENERATING

    def test_empty_gaps(self):
        """No classified gaps -> empty artifacts list."""
        state = TaskState(
            task_description="No gaps",
            classified_gaps=[],
        )

        cmd = generate_mcp_tool(state)

        assert cmd.update["generated_artifacts"] == []
        assert cmd.update["status"] == TaskStatus.GENERATING
        assert cmd.goto == "generate_skill"

    def test_mixed_success_and_failure(self):
        """Some generations succeed, some fail -> partial artifacts + errors."""
        state = TaskState(
            task_description="Two tools needed",
            classified_gaps=[
                ClassifiedGap(
                    description="Add numbers",
                    query="add numbers",
                    classified_type=CapabilityType.MCP_TOOL,
                    confidence=0.9,
                    rationale="Arithmetic",
                ),
                ClassifiedGap(
                    description="Parse XML",
                    query="parse XML",
                    classified_type=CapabilityType.MCP_TOOL,
                    confidence=0.85,
                    rationale="Parsing",
                ),
            ],
        )

        artifact = GeneratedArtifact(
            capability_name="add_numbers",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
        )

        side_effects = [
            (artifact, None),
            (None, "Generation failed for parse XML"),
        ]

        with patch(
            "src.orchestrator.generators.mcp_tool.generate_single_tool",
            side_effect=side_effects,
        ):
            cmd = generate_mcp_tool(state)

        assert len(cmd.update["generated_artifacts"]) == 1
        assert len(cmd.update["errors"]) == 1
        assert "parse XML" in cmd.update["errors"][0]

    def test_preserves_existing_errors(self):
        """Node preserves errors already in state."""
        state = TaskState(
            task_description="Add more",
            classified_gaps=[],
            errors=["pre-existing error from earlier node"],
        )
        cmd = generate_mcp_tool(state)

        assert "pre-existing error from earlier node" in cmd.update["errors"]

    def test_preserves_existing_artifacts(self):
        """Node preserves artifacts already in state."""
        existing = GeneratedArtifact(
            capability_name="existing_tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code="# existing",
        )
        state = TaskState(
            task_description="Add more",
            classified_gaps=[
                ClassifiedGap(
                    description="New tool",
                    query="new tool",
                    classified_type=CapabilityType.MCP_TOOL,
                    confidence=0.9,
                    rationale="New",
                ),
            ],
            generated_artifacts=[existing],
        )

        new_artifact = GeneratedArtifact(
            capability_name="new_tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
        )

        with patch(
            "src.orchestrator.generators.mcp_tool.generate_single_tool",
            return_value=(new_artifact, None),
        ):
            cmd = generate_mcp_tool(state)

        assert len(cmd.update["generated_artifacts"]) == 2

    def test_node_passes_task_description_as_task_context(self):
        """generate_mcp_tool passes state.task_description as task_context."""
        state = TaskState(
            task_description="Build a Q4 revenue analytics pipeline",
            classified_gaps=[
                ClassifiedGap(
                    description="Parse CSV file",
                    query="parse CSV",
                    classified_type=CapabilityType.MCP_TOOL,
                    confidence=0.9,
                    rationale="Atomic parse",
                ),
            ],
        )

        mock_artifact = GeneratedArtifact(
            capability_name="csv_parser",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
        )

        with patch(
            "src.orchestrator.generators.mcp_tool.generate_single_tool",
            return_value=(mock_artifact, None),
        ) as mock_gen:
            generate_mcp_tool(state)

        assert mock_gen.call_args.kwargs["task_context"] == "Build a Q4 revenue analytics pipeline"

    def test_command_structure(self):
        """Command has correct update keys and goto."""
        state = TaskState(
            task_description="Test",
            classified_gaps=[],
        )

        cmd = generate_mcp_tool(state)

        assert "generated_artifacts" in cmd.update
        assert "errors" in cmd.update
        assert "status" in cmd.update
        assert cmd.goto == "generate_skill"


# ---------------------------------------------------------------------------
# generate_single_tool — repair-path unit tests (no real LLM calls)
# ---------------------------------------------------------------------------


class TestGenerateSingleTool:
    """Tests for the LLM failure and repair paths inside generate_single_tool.

    Patches ``_get_llm`` to avoid real API calls; verifies that every error
    path returns ``(None, error_string)`` rather than raising, and that the
    repair pass produces an artifact on success.
    """

    _INVALID_CODE = GeneratedToolCode(
        tool_name="add_numbers",
        source_code="def bad(): pass",  # missing FastMCP, async def, Annotated
        description="Add two numbers",
    )
    _VALID_CODE = GeneratedToolCode(
        tool_name="add_numbers",
        source_code=VALID_TOOL_SOURCE,
        description="Add two numbers",
    )

    def _make_mock_llm(self, responses):
        """Return a mock LLM whose structured invoke() yields responses in order."""
        responses_iter = iter(responses)

        class _MockStructuredLLM:
            def invoke(self_, messages, config=None):
                r = next(responses_iter)
                if isinstance(r, Exception):
                    raise r
                return r

        _mock_structured = _MockStructuredLLM()

        class _MockLLM:
            def with_structured_output(self_, schema):
                return _mock_structured

        return _MockLLM()

    def _gap(self):
        return ClassifiedGap(
            description="Add two numbers",
            query="add_numbers",
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.9,
            rationale="Arithmetic",
        )

    def test_first_attempt_succeeds(self):
        """Valid code on first attempt returns artifact immediately without repair."""
        with patch(
            "src.orchestrator.generators.mcp_tool._get_llm",
            return_value=self._make_mock_llm([self._VALID_CODE]),
        ):
            artifact, error = generate_single_tool(self._gap())

        assert error is None
        assert artifact is not None
        assert artifact.capability_name == "add_numbers"
        assert artifact.capability_type == CapabilityType.MCP_TOOL
        assert artifact.source_code == VALID_TOOL_SOURCE

    def test_first_llm_call_fails(self):
        """LLM error on first attempt returns (None, error) without raising."""
        with patch(
            "src.orchestrator.generators.mcp_tool._get_llm",
            return_value=self._make_mock_llm([ValueError("network error")]),
        ):
            artifact, error = generate_single_tool(self._gap())

        assert artifact is None
        assert "LLM generation failed" in error

    def test_repair_succeeds(self):
        """Invalid first attempt fixed by repair pass returns artifact."""
        with patch(
            "src.orchestrator.generators.mcp_tool._get_llm",
            return_value=self._make_mock_llm([self._INVALID_CODE, self._VALID_CODE]),
        ):
            artifact, error = generate_single_tool(self._gap())

        assert error is None
        assert artifact is not None
        assert artifact.capability_name == "add_numbers"
        assert artifact.capability_type == CapabilityType.MCP_TOOL

    def test_repair_llm_call_fails(self):
        """Repair LLM call failing returns (None, error) without raising."""
        with patch(
            "src.orchestrator.generators.mcp_tool._get_llm",
            return_value=self._make_mock_llm(
                [self._INVALID_CODE, RuntimeError("timeout")]
            ),
        ):
            artifact, error = generate_single_tool(self._gap())

        assert artifact is None
        assert "Repair pass LLM call failed" in error

    def test_repair_still_invalid(self):
        """Repair returning still-invalid code returns (None, error) without raising."""
        with patch(
            "src.orchestrator.generators.mcp_tool._get_llm",
            return_value=self._make_mock_llm([self._INVALID_CODE, self._INVALID_CODE]),
        ):
            artifact, error = generate_single_tool(self._gap())

        assert artifact is None
        assert "Repair pass failed" in error

    def test_task_context_forwarded_to_prompt(self):
        """task_context passed to generate_single_tool appears in the LLM user message."""
        captured_messages: list = []

        class _MockStructuredLLM:
            def invoke(self_, messages, config=None):
                captured_messages.extend(messages)
                return self._VALID_CODE

        class _MockLLM:
            def with_structured_output(self_, schema):
                return _MockStructuredLLM()

        custom_context = "Business analytics pipeline for Q4 revenue reporting"

        with patch("src.orchestrator.generators.mcp_tool._get_llm", return_value=_MockLLM()):
            generate_single_tool(self._gap(), task_context=custom_context)

        user_msg = next(m for m in captured_messages if m["role"] == "user")
        assert custom_context in user_msg["content"]

    def test_default_task_context_no_additional(self):
        """Empty task_context uses the 'No additional context' fallback string."""
        captured_messages: list = []

        class _MockStructuredLLM:
            def invoke(self_, messages, config=None):
                captured_messages.extend(messages)
                return self._VALID_CODE

        class _MockLLM:
            def with_structured_output(self_, schema):
                return _MockStructuredLLM()

        with patch("src.orchestrator.generators.mcp_tool._get_llm", return_value=_MockLLM()):
            generate_single_tool(self._gap())  # no task_context

        user_msg = next(m for m in captured_messages if m["role"] == "user")
        assert "No additional context" in user_msg["content"]
