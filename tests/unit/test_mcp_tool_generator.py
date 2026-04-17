"""Unit tests for the MCP tool generator.

LLM-calling behaviour is covered by ``tests/integration/test_mcp_tool_generator.py``
with real OpenAI calls.  These tests cover schema validation, the
``_validate_code`` helper, and the node's routing logic (patching
``generate_single_tool``, not the LLM).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.orchestrator.generators.mcp_tool import (
    GeneratedToolCode,
    _step_position_block,
    _validate_code,
    _validate_step_signature,
    _validate_tool_name,
    generate_mcp_tool,
    generate_single_tool,
)
from src.orchestrator.generators.utils import (
    find_subtask_for_gap,
    is_first_step_for_gap,
)
from src.orchestrator.prompts.mcp_tool_generator import (
    DOWNSTREAM_STEP_DIRECTIVE,
    FIRST_STEP_DIRECTIVE,
)
from src.schemas.capability import CapabilityType
from src.schemas.task import (
    ClassifiedGap,
    GeneratedArtifact,
    SubTask,
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

    def test_annotated_in_docstring_not_false_positive(self):
        """'Annotated' in a docstring does not satisfy the import check.

        Substring search would yield a false positive here; AST import scanning
        correctly requires a real ``from typing import Annotated`` statement.
        """
        code = (
            "from mcp.server.fastmcp import FastMCP\n"
            "from pydantic import Field\n"
            "mcp = FastMCP('t')\n"
            "@mcp.tool()\n"
            "async def add(a: int) -> int:\n"
            '    """Use Annotated[type, Field(description=...)] for parameters."""\n'
            "    return a\n"
        )
        error = _validate_code(code)
        # "Annotated" appears in docstring but is not imported — must still fail
        assert error is not None
        assert "Annotated" in error

    def test_field_in_docstring_not_false_positive(self):
        """'Field' in a docstring does not satisfy the import check.

        Substring search would yield a false positive here; AST import scanning
        correctly requires a real ``from pydantic import Field`` statement.
        """
        code = (
            "from mcp.server.fastmcp import FastMCP\n"
            "from typing import Annotated\n"
            "mcp = FastMCP('t')\n"
            "@mcp.tool()\n"
            "async def add(a: Annotated[int, 'First number']) -> int:\n"
            '    """Field(description=...) from pydantic adds rich MCP metadata."""\n'
            "    return a\n"
        )
        error = _validate_code(code)
        # "Field" appears in docstring but is not imported — must still fail
        assert error is not None
        assert "Field" in error

    def test_private_top_level_async_helper_accepted(self):
        """A module-level private async helper (_prefixed) is not counted as a tool.

        The tool runner skips _-prefixed names via dir() filter; _validate_code
        must do the same so valid code with a private helper is not rejected.
        """
        code = (
            "from mcp.server.fastmcp import FastMCP\n"
            "from typing import Annotated\n"
            "from pydantic import Field\n"
            "mcp = FastMCP('t')\n"
            "async def _helper(x: int) -> int:\n"
            "    return x * 2\n"
            "@mcp.tool()\n"
            "async def add(\n"
            "    a: Annotated[int, Field(description='First number')],\n"
            ") -> int:\n"
            "    return await _helper(a)\n"
        )
        error = _validate_code(code)
        assert error is None, f"Expected valid (private helper ignored) but got: {error}"


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
# _validate_step_signature + step-position plumbing (Step 14.7)
# ---------------------------------------------------------------------------


def _make_tool_source(params: str, body: str = "    return {}") -> str:
    """Build a minimal FastMCP tool source with the given parameter list."""
    return (
        '"""MCP tool stub."""\n'
        "from typing import Annotated\n"
        "from mcp.server.fastmcp import FastMCP\n"
        "from pydantic import Field\n"
        "mcp = FastMCP('stub')\n"
        "@mcp.tool()\n"
        f"async def run({params}) -> dict:\n"
        '    """doc."""\n'
        f"{body}\n"
        'if __name__ == "__main__":\n'
        "    mcp.run()\n"
    )


class TestValidateStepSignature:
    """Step 14.7: catch step-0 functions that falsely require upstream_data."""

    _FIRST_STEP_CORRECT = _make_tool_source(
        'csv_path: Annotated[str, Field(description="Path to CSV")]'
    )
    _FIRST_STEP_WITH_REQUIRED_UPSTREAM = _make_tool_source(
        'upstream_data: Annotated[dict, Field(description="Upstream")]'
    )
    _DOWNSTREAM_CORRECT = _make_tool_source(
        'upstream_data: Annotated[dict, Field(description="Upstream")]'
    )
    _DOWNSTREAM_MISSING_UPSTREAM = _make_tool_source(
        'value: Annotated[int, Field(description="A number")] = 0'
    )
    _DOWNSTREAM_OPTIONAL_UPSTREAM = _make_tool_source(
        'upstream_data: Annotated[dict, Field(description="Upstream")] = None'
    )

    def test_first_step_without_upstream_ok(self):
        assert _validate_step_signature(self._FIRST_STEP_CORRECT, is_first_step=True) is None

    def test_first_step_with_required_upstream_rejected(self):
        error = _validate_step_signature(
            self._FIRST_STEP_WITH_REQUIRED_UPSTREAM, is_first_step=True
        )
        assert error is not None
        assert "step-0" in error
        assert "upstream_data" in error

    def test_downstream_with_upstream_ok(self):
        assert (
            _validate_step_signature(self._DOWNSTREAM_CORRECT, is_first_step=False)
            is None
        )

    def test_downstream_missing_upstream_rejected(self):
        error = _validate_step_signature(
            self._DOWNSTREAM_MISSING_UPSTREAM, is_first_step=False
        )
        assert error is not None
        assert "upstream_data" in error

    def test_downstream_with_optional_upstream_rejected(self):
        # A downstream step that made upstream_data optional still fails —
        # the executor only populates upstream_data when it's present as a
        # parameter name, and an optional default masks a silently empty dict.
        error = _validate_step_signature(
            self._DOWNSTREAM_OPTIONAL_UPSTREAM, is_first_step=False
        )
        assert error is not None
        assert "upstream_data" in error

    def test_none_is_first_step_skips_check(self):
        # Gap couldn't be matched to a subtask — skip position checks entirely.
        assert _validate_step_signature(self._FIRST_STEP_CORRECT, is_first_step=None) is None
        assert (
            _validate_step_signature(self._FIRST_STEP_WITH_REQUIRED_UPSTREAM, is_first_step=None)
            is None
        )

    def test_syntax_error_returns_none(self):
        # Syntax errors are handled by _validate_code; this helper defers.
        assert _validate_step_signature("def bad(\n", is_first_step=True) is None

    def test_missing_async_function_returns_none(self):
        # Missing function is handled by _validate_code; this helper defers.
        code = (
            '"""Stub."""\n'
            "from mcp.server.fastmcp import FastMCP\n"
            "mcp = FastMCP('stub')\n"
        )
        assert _validate_step_signature(code, is_first_step=True) is None

    def test_private_async_helper_ignored(self):
        # The AST walker uses the same public-function selection as _validate_code,
        # so a private helper does not shadow the real tool.
        code = (
            '"""Stub."""\n'
            "from typing import Annotated\n"
            "from mcp.server.fastmcp import FastMCP\n"
            "from pydantic import Field\n"
            "mcp = FastMCP('stub')\n"
            "async def _helper(upstream_data: dict) -> dict:\n"
            "    return upstream_data\n"
            "@mcp.tool()\n"
            'async def run(csv_path: Annotated[str, Field(description="p")]) -> dict:\n'
            '    """doc."""\n'
            "    return {}\n"
        )
        # Public function has csv_path only → valid as step 0.
        assert _validate_step_signature(code, is_first_step=True) is None

    def test_kwonly_required_upstream_also_flagged(self):
        code = (
            '"""Stub."""\n'
            "from typing import Annotated\n"
            "from mcp.server.fastmcp import FastMCP\n"
            "from pydantic import Field\n"
            "mcp = FastMCP('stub')\n"
            "@mcp.tool()\n"
            "async def run(*, upstream_data: Annotated[dict, Field(description='u')]) -> dict:\n"
            '    """doc."""\n'
            "    return upstream_data\n"
        )
        error = _validate_step_signature(code, is_first_step=True)
        assert error is not None
        assert "upstream_data" in error


class TestStepPositionBlock:
    def test_first_step_returns_first_directive(self):
        assert _step_position_block(True) == FIRST_STEP_DIRECTIVE

    def test_downstream_returns_downstream_directive(self):
        assert _step_position_block(False) == DOWNSTREAM_STEP_DIRECTIVE

    def test_none_returns_empty(self):
        assert _step_position_block(None) == ""

    def test_first_directive_mentions_no_upstream_data(self):
        assert "upstream_data" in FIRST_STEP_DIRECTIVE
        assert "DO NOT" in FIRST_STEP_DIRECTIVE

    def test_downstream_directive_mentions_required_upstream_data(self):
        assert "upstream_data" in DOWNSTREAM_STEP_DIRECTIVE
        assert "exactly ONE required parameter" in DOWNSTREAM_STEP_DIRECTIVE


class TestIsFirstStepForGap:
    """Subtask-matching helper used to decide the step-position directive."""

    @staticmethod
    def _gap(query: str = "parse_csv") -> ClassifiedGap:
        return ClassifiedGap(
            description="d",
            query=query,
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.9,
            rationale="r",
        )

    def test_first_step_when_no_depends_on(self):
        subtasks = [
            SubTask(
                description="parse",
                required_capabilities=["parse_csv"],
                depends_on=[],
            ),
        ]
        assert is_first_step_for_gap(self._gap(), subtasks) is True

    def test_downstream_when_depends_on_nonempty(self):
        subtasks = [
            SubTask(description="parse", required_capabilities=["parse_csv"]),
            SubTask(
                description="aggregate",
                required_capabilities=["aggregate"],
                depends_on=[0],
            ),
        ]
        assert is_first_step_for_gap(self._gap("aggregate"), subtasks) is False

    def test_none_when_gap_not_in_subtasks(self):
        subtasks = [
            SubTask(description="parse", required_capabilities=["parse_csv"]),
        ]
        assert is_first_step_for_gap(self._gap("unknown"), subtasks) is None

    def test_none_when_subtasks_empty(self):
        assert is_first_step_for_gap(self._gap(), []) is None

    def test_find_subtask_returns_matching_entry(self):
        st = SubTask(
            description="aggregate",
            required_capabilities=["aggregate", "sum"],
            depends_on=[0],
        )
        subtasks = [
            SubTask(description="parse", required_capabilities=["parse_csv"]),
            st,
        ]
        assert find_subtask_for_gap(self._gap("sum"), subtasks) is st

    def test_t11_join_first_step_regression(self):
        """Regression guard for the T11 bug: decomposer named step 0
        ``join-sales-with-products-on-product_id`` and the word "join"
        pattern-matched to middle-step semantics in the LLM's understanding.
        is_first_step_for_gap must return True because ``depends_on == []``.
        """
        gap = self._gap("join-sales-with-products-on-product_id")
        subtasks = [
            SubTask(
                description="Join sales with products on product_id",
                required_capabilities=["join-sales-with-products-on-product_id"],
                depends_on=[],
            ),
            SubTask(
                description="Compare revenue to targets",
                required_capabilities=["compare-revenue-to-targets"],
                depends_on=[0],
            ),
        ]
        assert is_first_step_for_gap(gap, subtasks) is True


class TestStepPositionInPrompt:
    """Verify the step-position block actually lands in the user prompt."""

    @staticmethod
    def _capture_messages(recorded: list) -> type:
        class _Structured:
            def invoke(_self, messages, config=None):
                recorded.extend(messages)
                return GeneratedToolCode(
                    tool_name="stub",
                    source_code=VALID_TOOL_SOURCE,
                    description="stub",
                )

        class _LLM:
            def with_structured_output(_self, schema):
                return _Structured()

        return _LLM()

    @staticmethod
    def _gap() -> ClassifiedGap:
        return ClassifiedGap(
            description="add",
            query="add_numbers",
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.9,
            rationale="r",
        )

    def test_first_step_directive_in_prompt(self):
        captured: list = []
        with patch(
            "src.orchestrator.generators.mcp_tool._get_llm",
            return_value=self._capture_messages(captured),
        ):
            generate_single_tool(self._gap(), is_first_step=True)

        user_msg = next(m for m in captured if m["role"] == "user")
        assert "THIS IS THE FIRST STEP" in user_msg["content"]
        assert "DO NOT declare an ``upstream_data`` parameter" in user_msg["content"]

    def test_downstream_directive_in_prompt(self):
        captured: list = []
        with patch(
            "src.orchestrator.generators.mcp_tool._get_llm",
            return_value=self._capture_messages(captured),
        ):
            generate_single_tool(self._gap(), is_first_step=False)

        user_msg = next(m for m in captured if m["role"] == "user")
        assert "THIS IS A DOWNSTREAM STEP" in user_msg["content"]
        assert "exactly ONE required parameter" in user_msg["content"]

    def test_none_omits_directive(self):
        captured: list = []
        with patch(
            "src.orchestrator.generators.mcp_tool._get_llm",
            return_value=self._capture_messages(captured),
        ):
            generate_single_tool(self._gap(), is_first_step=None)

        user_msg = next(m for m in captured if m["role"] == "user")
        assert "Step Position (HARD CONSTRAINT)" not in user_msg["content"]

    def test_schema_context_reaches_prompt(self):
        """Phase 3.5c wires ``build_schema_context(gap, subtasks)`` into the
        MCP tool generator prompt so each step knows its upstream input keys
        and required output keys. A regression that disconnected this
        plumbing (e.g. dropped the ``schema_context`` kwarg, stopped
        formatting the section into the user prompt, or swallowed the
        output of ``build_schema_context``) would silently produce schema-
        blind tools. Guard the injection path with distinctive key tokens.
        """
        from src.orchestrator.generators.utils import build_schema_context

        upstream = SubTask(
            description="Parse the input CSV",
            required_capabilities=["parse_csv"],
            depends_on=[],
            output_schema={"parsed_rows_fixture": "list[dict] of row records"},
        )
        downstream = SubTask(
            description="Aggregate parsed rows",
            required_capabilities=["aggregate_rows"],
            depends_on=[0],
            output_schema={"aggregate_summary_fixture": "dict of per-group totals"},
        )
        gap = ClassifiedGap(
            description="Aggregate rows",
            query="aggregate_rows",
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.9,
            rationale="deterministic",
        )
        schema_ctx = build_schema_context(gap, [upstream, downstream])
        assert schema_ctx, "build_schema_context must return a non-empty contract for this fixture"

        captured: list = []
        with patch(
            "src.orchestrator.generators.mcp_tool._get_llm",
            return_value=self._capture_messages(captured),
        ):
            generate_single_tool(gap, schema_context=schema_ctx)

        user_msg = next(m for m in captured if m["role"] == "user")
        assert "Input/Output Schema Contract" in user_msg["content"]
        assert "parsed_rows_fixture" in user_msg["content"]
        assert "aggregate_summary_fixture" in user_msg["content"]

    def test_empty_schema_context_omits_contract_section(self):
        """When ``build_schema_context`` returns empty (legacy decomposition
        without ``output_schema``), the generator must NOT emit a stray
        ``## Input/Output Schema Contract`` heading with nothing under it.
        """
        captured: list = []
        with patch(
            "src.orchestrator.generators.mcp_tool._get_llm",
            return_value=self._capture_messages(captured),
        ):
            generate_single_tool(self._gap(), schema_context="")

        user_msg = next(m for m in captured if m["role"] == "user")
        assert "Input/Output Schema Contract" not in user_msg["content"]


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
        # Fix 5: name is now a deterministic slug, not the LLM-chosen name
        from src.orchestrator.generators.utils import slugify_gap
        assert artifact.capability_name == slugify_gap("add_numbers")
        assert artifact.capability_type == CapabilityType.MCP_TOOL
        assert artifact.source_code == VALID_TOOL_SOURCE

    def test_description_preserved_in_artifact(self):
        """The LLM-generated description is stored in the artifact (Bug 1 fix)."""
        valid_with_desc = GeneratedToolCode(
            tool_name="add_numbers",
            source_code=VALID_TOOL_SOURCE,
            description="Computes the sum of two integer inputs",
        )
        with patch(
            "src.orchestrator.generators.mcp_tool._get_llm",
            return_value=self._make_mock_llm([valid_with_desc]),
        ):
            artifact, error = generate_single_tool(self._gap())

        assert error is None
        assert artifact is not None
        assert artifact.description == "Computes the sum of two integer inputs"

    def test_description_preserved_after_repair(self):
        """description from the repaired output is stored in the artifact."""
        repaired = GeneratedToolCode(
            tool_name="add_numbers",
            source_code=VALID_TOOL_SOURCE,
            description="Sum of two integers (repaired)",
        )
        with patch(
            "src.orchestrator.generators.mcp_tool._get_llm",
            return_value=self._make_mock_llm([self._INVALID_CODE, repaired]),
        ):
            artifact, error = generate_single_tool(self._gap())

        assert error is None
        assert artifact is not None
        assert artifact.description == "Sum of two integers (repaired)"

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
        # Fix 5: name is deterministic slug, not LLM-chosen
        from src.orchestrator.generators.utils import slugify_gap
        assert artifact.capability_name == slugify_gap("add_numbers")
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

    def test_default_task_context_falls_back_to_gap_description(self):
        """Empty task_context falls back to gap.description so the LLM still
        has a task-level context string to generate against."""
        captured_messages: list = []

        class _MockStructuredLLM:
            def invoke(self_, messages, config=None):
                captured_messages.extend(messages)
                return self._VALID_CODE

        class _MockLLM:
            def with_structured_output(self_, schema):
                return _MockStructuredLLM()

        gap = self._gap()
        with patch("src.orchestrator.generators.mcp_tool._get_llm", return_value=_MockLLM()):
            generate_single_tool(gap)  # no task_context

        user_msg = next(m for m in captured_messages if m["role"] == "user")
        assert gap.description in user_msg["content"]

    def test_generate_single_tool_uses_forced_name(self):
        """Fix 5: artifact uses slugify_gap(gap.query), not the LLM-chosen name."""
        llm_code = GeneratedToolCode(
            tool_name="something_else",  # LLM picks a different name
            source_code=VALID_TOOL_SOURCE,
            description="Adds numbers",
        )
        with patch(
            "src.orchestrator.generators.mcp_tool._get_llm",
            return_value=self._make_mock_llm([llm_code]),
        ):
            artifact, error = generate_single_tool(self._gap())

        assert error is None
        assert artifact is not None
        from src.orchestrator.generators.utils import slugify_gap
        assert artifact.capability_name == slugify_gap("add_numbers")


# ---------------------------------------------------------------------------
# Fix 4: Skip already-passing gaps on retry
# ---------------------------------------------------------------------------


class TestSkipOnRetry:
    """Tests for the skip-on-retry logic in generate_mcp_tool."""

    def test_skip_gaps_with_existing_passing_artifact(self):
        """Pre-existing artifact with matching gap_query skips regeneration."""
        existing = GeneratedArtifact(
            capability_name="fuzzy_join_abc123",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
            gap_query="fuzzy join csvs",
        )
        state = TaskState(
            task_description="Join two CSVs",
            classified_gaps=[
                ClassifiedGap(
                    description="Fuzzy join",
                    query="fuzzy join csvs",
                    classified_type=CapabilityType.MCP_TOOL,
                    confidence=0.9,
                    rationale="join",
                ),
            ],
            generated_artifacts=[existing],
        )

        with patch(
            "src.orchestrator.generators.mcp_tool.generate_single_tool",
        ) as mock_gen:
            cmd = generate_mcp_tool(state)

        mock_gen.assert_not_called()
        assert len(cmd.update["generated_artifacts"]) == 1

    def test_gap_query_set_on_generated_artifact(self):
        """After generation, artifact.gap_query == gap.query."""
        state = TaskState(
            task_description="Test",
            classified_gaps=[
                ClassifiedGap(
                    description="Add numbers",
                    query="add_numbers",
                    classified_type=CapabilityType.MCP_TOOL,
                    confidence=0.9,
                    rationale="Arithmetic",
                ),
            ],
        )
        mock_artifact = GeneratedArtifact(
            capability_name="add_numbers_abc123",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
            gap_query="add_numbers",
        )
        with patch(
            "src.orchestrator.generators.mcp_tool.generate_single_tool",
            return_value=(mock_artifact, None),
        ):
            cmd = generate_mcp_tool(state)

        art = cmd.update["generated_artifacts"][0]
        assert art.gap_query == "add_numbers"

    def test_first_attempt_processes_all_gaps(self):
        """On first attempt (no existing artifacts), all gaps are processed."""
        state = TaskState(
            task_description="Test",
            classified_gaps=[
                ClassifiedGap(
                    description="A",
                    query="gap-a",
                    classified_type=CapabilityType.MCP_TOOL,
                    confidence=0.9,
                    rationale="x",
                ),
                ClassifiedGap(
                    description="B",
                    query="gap-b",
                    classified_type=CapabilityType.MCP_TOOL,
                    confidence=0.9,
                    rationale="x",
                ),
            ],
        )

        mock_artifact = GeneratedArtifact(
            capability_name="tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
        )
        with patch(
            "src.orchestrator.generators.mcp_tool.generate_single_tool",
            return_value=(mock_artifact, None),
        ) as mock_gen:
            generate_mcp_tool(state)

        assert mock_gen.call_count == 2

    def test_gap_query_none_does_not_crash_skip_set(self):
        """Artifact with gap_query=None in state should not break skip set construction."""
        existing = GeneratedArtifact(
            capability_name="old_tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code="# old",
            gap_query=None,
        )
        state = TaskState(
            task_description="Test",
            classified_gaps=[
                ClassifiedGap(
                    description="New",
                    query="new-tool",
                    classified_type=CapabilityType.MCP_TOOL,
                    confidence=0.9,
                    rationale="x",
                ),
            ],
            generated_artifacts=[existing],
        )
        mock_artifact = GeneratedArtifact(
            capability_name="new_tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
        )
        with patch(
            "src.orchestrator.generators.mcp_tool.generate_single_tool",
            return_value=(mock_artifact, None),
        ) as mock_gen:
            generate_mcp_tool(state)

        mock_gen.assert_called_once()


# ---------------------------------------------------------------------------
# Fix 7: Parallel generation
# ---------------------------------------------------------------------------


class TestParallelGeneration:
    """Tests for parallel generation in generate_mcp_tool."""

    def test_parallel_generation_produces_all_artifacts(self):
        """3 gaps all produce artifacts via parallel generation."""
        gaps = [
            ClassifiedGap(
                description=f"Tool {i}",
                query=f"tool-{i}",
                classified_type=CapabilityType.MCP_TOOL,
                confidence=0.9,
                rationale="x",
            )
            for i in range(3)
        ]
        state = TaskState(task_description="Test", classified_gaps=gaps)

        def mock_gen(gap, task_context="", **kwargs):
            return (
                GeneratedArtifact(
                    capability_name=f"gen_{gap.query}",
                    capability_type=CapabilityType.MCP_TOOL,
                    source_code=VALID_TOOL_SOURCE,
                    gap_query=gap.query,
                ),
                None,
            )

        with patch(
            "src.orchestrator.generators.mcp_tool.generate_single_tool",
            side_effect=mock_gen,
        ):
            cmd = generate_mcp_tool(state)

        assert len(cmd.update["generated_artifacts"]) == 3

    def test_parallel_generation_exception_recorded_as_error(self):
        """One gap raises; other artifacts still produced, error in state.errors."""
        gaps = [
            ClassifiedGap(
                description=f"Tool {i}",
                query=f"tool-{i}",
                classified_type=CapabilityType.MCP_TOOL,
                confidence=0.9,
                rationale="x",
            )
            for i in range(2)
        ]
        state = TaskState(task_description="Test", classified_gaps=gaps)

        call_count = 0

        def mock_gen(gap, task_context="", **kwargs):
            nonlocal call_count
            call_count += 1
            if gap.query == "tool-0":
                raise RuntimeError("LLM timeout")
            return (
                GeneratedArtifact(
                    capability_name="gen_tool_1",
                    capability_type=CapabilityType.MCP_TOOL,
                    source_code=VALID_TOOL_SOURCE,
                ),
                None,
            )

        with patch(
            "src.orchestrator.generators.mcp_tool.generate_single_tool",
            side_effect=mock_gen,
        ):
            cmd = generate_mcp_tool(state)

        assert len(cmd.update["generated_artifacts"]) == 1
        assert any("tool-0" in e for e in cmd.update["errors"])

    def test_already_passing_gaps_not_submitted_to_pool(self):
        """Pre-existing artifact with gap_query means that gap is not submitted."""
        existing = GeneratedArtifact(
            capability_name="existing_tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
            gap_query="tool-a",
        )
        state = TaskState(
            task_description="Test",
            classified_gaps=[
                ClassifiedGap(
                    description="Tool A",
                    query="tool-a",
                    classified_type=CapabilityType.MCP_TOOL,
                    confidence=0.9,
                    rationale="x",
                ),
                ClassifiedGap(
                    description="Tool B",
                    query="tool-b",
                    classified_type=CapabilityType.MCP_TOOL,
                    confidence=0.9,
                    rationale="x",
                ),
            ],
            generated_artifacts=[existing],
        )
        new_artifact = GeneratedArtifact(
            capability_name="tool_b_gen",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=VALID_TOOL_SOURCE,
        )

        with patch(
            "src.orchestrator.generators.mcp_tool.generate_single_tool",
            return_value=(new_artifact, None),
        ) as mock_gen:
            cmd = generate_mcp_tool(state)

        # Only tool-b should be generated
        mock_gen.assert_called_once()
        assert len(cmd.update["generated_artifacts"]) == 2
