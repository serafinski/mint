"""Unit tests for src/verification/behavioral.py.

All tests mock sandbox_execute — no Docker required.
"""

import json
from unittest.mock import MagicMock, patch

from src.schemas.capability import CapabilityType
from src.schemas.task import GeneratedArtifact
from src.schemas.verification import Severity
from src.verification.behavioral import (
    TIMEOUT_THRESHOLD_S,
    _build_sandbox_code,
    _extract_function_name,
    _generate_boundary_inputs,
    _generate_large_inputs,
    _generate_negative_inputs,
    _test_latency,
    _test_output_variation,
    _test_parameterless_smoke,
    _test_timeout,
    extract_parameter_schema,
    run,
)

# ---------------------------------------------------------------------------
# Sample tool source code
# ---------------------------------------------------------------------------

CLEAN_TOOL = '''\
from typing import Annotated
from pydantic import Field

async def compute_sum(
    a: Annotated[int, Field(description="First number")],
    b: Annotated[int, Field(description="Second number")],
) -> int:
    """Add two numbers."""
    return a + b
'''

INCONSISTENT_TOOL = '''\
import random

async def roll_dice(sides: int) -> int:
    """Return a random number."""
    return random.randint(1, sides)
'''

SLOW_TOOL = '''\
import time

async def slow_op(n: int) -> int:
    """Intentionally slow."""
    time.sleep(10)
    return n
'''

NO_PARAMS_TOOL = '''\
async def get_status() -> str:
    """Returns current status."""
    return "ok"
'''

PRIVATE_ONLY_TOOL = '''\
async def _internal() -> None:
    pass
'''

TOOL_WITH_VARIOUS_TYPES = '''\
async def process(
    name: str,
    count: int,
    ratio: float,
    flag: bool,
    items: list,
    config: dict,
) -> str:
    return "done"
'''


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_artifact(source_code: str, name: str = "test-tool") -> GeneratedArtifact:
    return GeneratedArtifact(
        capability_name=name,
        capability_type=CapabilityType.MCP_TOOL,
        source_code=source_code,
    )


def _sandbox_success(result_value: str, elapsed: float = 0.1, error: str | None = None) -> dict:
    """Build a successful sandbox_execute return dict."""
    output = {"result": result_value, "elapsed": elapsed, "error": error}
    return {"stdout": json.dumps(output) + "\n", "stderr": "", "exit_code": 0, "success": True}


def _sandbox_failure(stderr: str = "Error", exit_code: int = 1) -> dict:
    return {"stdout": "", "stderr": stderr, "exit_code": exit_code, "success": False}


def _sandbox_timeout() -> dict:
    return {"stdout": "", "stderr": "Timeout", "exit_code": -1, "success": False}


# ---------------------------------------------------------------------------
# extract_parameter_schema
# ---------------------------------------------------------------------------


class TestExtractParameterSchema:
    def test_annotated_params(self):
        schema = extract_parameter_schema(CLEAN_TOOL)
        assert "a" in schema
        assert "b" in schema
        assert schema["a"]["type"] == "int"
        assert schema["b"]["type"] == "int"

    def test_plain_type_annotations(self):
        schema = extract_parameter_schema(TOOL_WITH_VARIOUS_TYPES)
        assert schema["name"]["type"] == "str"
        assert schema["count"]["type"] == "int"
        assert schema["ratio"]["type"] == "float"
        assert schema["flag"]["type"] == "bool"
        assert schema["items"]["type"] == "list"
        assert schema["config"]["type"] == "dict"

    def test_no_params(self):
        schema = extract_parameter_schema(NO_PARAMS_TOOL)
        assert schema == {}

    def test_private_function_skipped(self):
        schema = extract_parameter_schema(PRIVATE_ONLY_TOOL)
        assert schema == {}

    def test_syntax_error_returns_empty(self):
        schema = extract_parameter_schema("def broken(:")
        assert schema == {}

    def test_sync_function_skipped(self):
        code = "def sync_func(x: int) -> int:\n    return x"
        schema = extract_parameter_schema(code)
        assert schema == {}

    def test_unannotated_param(self):
        code = "async def func(x):\n    return x"
        schema = extract_parameter_schema(code)
        assert schema["x"]["type"] == "unknown"

    def test_respects_mcp_tool_decorator_over_source_order(self):
        """Security audit finding #20 (2026-04-20) regression guard.

        Pre-fix used ``ast.walk`` and returned the schema of the first
        public async function — for a module with a helper defined
        before the ``@mcp.tool`` entrypoint, returned the helper's
        schema. Post-fix delegates to ``_extract_function_name`` so the
        decorator wins regardless of source order. This is the same
        fix class as Issue #8 for the function-name resolver.
        """
        source = '''
async def helper_format_rows(a: int) -> str:
    return f"{a}"

@mcp.tool
async def real_tool(df: dict, window: int = 7) -> dict:
    return {}
'''
        schema = extract_parameter_schema(source)
        # Must be the decorated tool's schema, not the helper's.
        assert set(schema.keys()) == {"df", "window"}, (
            f"Pre-fix returned the helper's {{'a': ...}} schema. "
            f"Got: {schema}"
        )
        assert schema["df"]["type"] == "dict"
        assert schema["window"]["type"] == "int"

    def test_explicit_func_name_overrides_resolution(self):
        """Callers that already resolved the function name can pass it
        explicitly — belt-and-braces against any resolver drift."""
        source = '''
async def first_fn(a: int) -> str:
    return str(a)

async def second_fn(b: float) -> str:
    return str(b)
'''
        # No decorator → _extract_function_name returns None (ambiguous).
        # Explicit func_name lets the caller pick.
        assert extract_parameter_schema(source, func_name="first_fn") == {"a": {"type": "int"}}
        assert extract_parameter_schema(source, func_name="second_fn") == {"b": {"type": "float"}}

    def test_nonexistent_func_name_returns_empty(self):
        """Explicit func_name pointing to a missing function → empty schema."""
        source = "async def real_fn(x: int) -> int: return x\n"
        assert extract_parameter_schema(source, func_name="nonexistent") == {}


# ---------------------------------------------------------------------------
# _extract_function_name
# ---------------------------------------------------------------------------


class TestExtractFunctionName:
    def test_finds_public_async_function(self):
        assert _extract_function_name(CLEAN_TOOL) == "compute_sum"

    def test_skips_private(self):
        assert _extract_function_name(PRIVATE_ONLY_TOOL) is None

    def test_syntax_error(self):
        assert _extract_function_name("def broken(:") is None


# ---------------------------------------------------------------------------
# Security audit finding #8 — decorator-aware entrypoint selection (2026-04-19)
# ---------------------------------------------------------------------------


class TestFindMcpToolFunction:
    """Decorator-aware selection — the FastMCP ``@mcp.tool`` decorator is
    the only unambiguous signal identifying the tool entrypoint. Pre-fix
    call sites used ordering heuristics (``dir()`` alphabetical or
    ``ast.walk`` source order) that picked the wrong function when a
    helper was defined before the decorated entrypoint.
    """

    def test_helper_first_then_decorated_entrypoint(self):
        """Reporter's repro: helper defined first in source, decorated
        entrypoint second. Pre-fix picked ``helper_format_rows``; post-fix
        must pick the decorated ``summarize_sales``."""
        from src.verification.behavioral import _find_mcp_tool_function

        src = """
async def helper_format_rows(rows):
    return [str(r) for r in rows]

@mcp.tool()
async def summarize_sales(data):
    return {"total": sum(data)}
"""
        assert _find_mcp_tool_function(src) == "summarize_sales"

    def test_entrypoint_first_then_helper(self):
        """Same pair in reversed source order — decorator wins regardless."""
        from src.verification.behavioral import _find_mcp_tool_function

        src = """
@mcp.tool()
async def summarize_sales(data):
    return {"total": sum(data)}

async def helper_format_rows(rows):
    return [str(r) for r in rows]
"""
        assert _find_mcp_tool_function(src) == "summarize_sales"

    def test_alphabetical_before_decorator_selects_decorated(self):
        """``apple_helper`` alphabetically first, ``zebra_tool`` decorated.
        Regression guard for the ToolLoader ``dir()`` alphabetical bug."""
        from src.verification.behavioral import _find_mcp_tool_function

        src = """
async def apple_helper():
    return 1

@mcp.tool()
async def zebra_tool():
    return 2
"""
        assert _find_mcp_tool_function(src) == "zebra_tool"

    def test_bare_decorator_accepted(self):
        """``@mcp.tool`` without call parens — must still match."""
        from src.verification.behavioral import _find_mcp_tool_function

        src = """
@mcp.tool
async def the_tool():
    return 1
"""
        assert _find_mcp_tool_function(src) == "the_tool"

    def test_ignores_unrelated_decorators(self):
        """``@lru_cache``, ``@contextmanager`` etc. must not match."""
        from src.verification.behavioral import _find_mcp_tool_function

        src = """
@lru_cache(maxsize=32)
async def cached_helper():
    return 1

@mcp.tool()
async def the_tool():
    return 2
"""
        assert _find_mcp_tool_function(src) == "the_tool"

    def test_no_decorator_returns_none(self):
        from src.verification.behavioral import _find_mcp_tool_function

        src = """
async def undecorated():
    return 1
"""
        assert _find_mcp_tool_function(src) is None

    def test_multiple_decorated_ambiguous_returns_none(self):
        """Two ``@mcp.tool`` functions — caller must surface as a failure,
        not silently pick one."""
        from src.verification.behavioral import _find_mcp_tool_function

        src = """
@mcp.tool()
async def first():
    return 1

@mcp.tool()
async def second():
    return 2
"""
        assert _find_mcp_tool_function(src) is None

    def test_nested_async_def_not_selected(self):
        """Nested ``async def`` inside a helper must not count — only
        top-level public async defs are considered."""
        from src.verification.behavioral import _find_mcp_tool_function

        src = """
async def outer():
    @mcp.tool()
    async def inner():
        return 1
    return inner

@mcp.tool()
async def real_tool():
    return 2
"""
        assert _find_mcp_tool_function(src) == "real_tool"

    def test_private_decorated_ignored(self):
        """Leading-underscore functions are private by convention and
        must be skipped even if decorated."""
        from src.verification.behavioral import _find_mcp_tool_function

        src = """
@mcp.tool()
async def _private_tool():
    return 1

@mcp.tool()
async def public_tool():
    return 2
"""
        assert _find_mcp_tool_function(src) == "public_tool"

    def test_parse_error_returns_none(self):
        from src.verification.behavioral import _find_mcp_tool_function

        assert _find_mcp_tool_function("async def broken(:") is None


class TestExtractFunctionNameFallbackChain:
    """``_extract_function_name`` is the backwards-compatible entrypoint
    that every MCP-tool caller already uses. Post-fix it resolves via a
    fallback chain: decorator-first, then single-public-async with a
    warning, then None for ambiguous cases."""

    def test_decorator_takes_precedence_over_source_order(self):
        """Pre-fix picked helper (first in source); post-fix picks decorated."""
        src = """
async def helper_format_rows(rows):
    return rows

@mcp.tool()
async def summarize_sales(data):
    return {}
"""
        assert _extract_function_name(src) == "summarize_sales"

    def test_legacy_single_async_fallback_emits_warning(self, caplog):
        """Legacy artifacts without @mcp.tool — fall back with a WARNING."""
        import logging

        src = """
async def legacy_tool(x):
    return x
"""
        with caplog.at_level(logging.WARNING, logger="src.verification.behavioral"):
            result = _extract_function_name(src)
        assert result == "legacy_tool"
        assert any(
            "no @mcp.tool-decorated function" in rec.message
            for rec in caplog.records
        )

    def test_ambiguous_no_decorator_multiple_public_returns_none(self):
        """Two undecorated public async defs — cannot pick one safely."""
        src = """
async def first():
    return 1

async def second():
    return 2
"""
        assert _extract_function_name(src) is None

    def test_ambiguous_multiple_decorated_returns_none(self):
        """Two decorated functions — caller must fail verification."""
        src = """
@mcp.tool()
async def first():
    return 1

@mcp.tool()
async def second():
    return 2
"""
        assert _extract_function_name(src) is None


# ---------------------------------------------------------------------------
# Input generators
# ---------------------------------------------------------------------------


class TestInputGenerators:
    def test_boundary_inputs(self):
        schema = {"n": {"type": "int"}, "text": {"type": "str"}}
        inputs = _generate_boundary_inputs(schema)
        assert inputs["n"] == 0
        assert inputs["text"] == ""

    def test_negative_inputs(self):
        schema = {"n": {"type": "int"}, "x": {"type": "float"}, "s": {"type": "str"}}
        inputs = _generate_negative_inputs(schema)
        assert inputs["n"] == -1
        assert inputs["x"] == -1.0
        assert inputs["s"] == "nonexistent_entity_xyz"

    def test_large_inputs(self):
        schema = {"n": {"type": "int"}, "text": {"type": "str"}}
        inputs = _generate_large_inputs(schema)
        assert inputs["n"] == 1_000_000
        assert len(inputs["text"]) == 10_000

    def test_unknown_type_defaults_to_empty_string(self):
        schema = {"x": {"type": "unknown"}}
        assert _generate_boundary_inputs(schema) == {"x": ""}
        assert _generate_negative_inputs(schema) == {"x": ""}
        assert _generate_large_inputs(schema) == {"x": ""}


# ---------------------------------------------------------------------------
# _build_sandbox_code
# ---------------------------------------------------------------------------


class TestBuildSandboxCode:
    def test_contains_asyncio_run(self):
        code = _build_sandbox_code("async def f(x: int): return x", "f", {"x": 1})
        assert "asyncio.run" in code

    def test_contains_function_call(self):
        code = _build_sandbox_code("async def f(x: int): return x", "f", {"x": 42})
        assert "await f(" in code

    def test_contains_source_code(self):
        src = "async def my_tool(n: int): return n * 2"
        code = _build_sandbox_code(src, "my_tool", {"n": 5})
        assert "my_tool" in code

    def test_main_guard_neutralized_before_source(self):
        """Generated MCP tools include 'if __name__ == "__main__": mcp.run()'.

        The sandbox script is run as 'python script.py' so __name__ == "__main__"
        is True.  Without neutralization, mcp.run() would start a server loop
        and asyncio.run(_run()) would never execute — all behavioral tests silently
        no-op.  The __name__ override MUST appear before the inlined source.

        NOTE: This is a cheap LEXICAL guard (string-position check). It is
        DELIBERATELY PAIRED with ``test_main_guard_neutralized_end_to_end_subprocess``
        below, which runs the same generated payload through a real Python
        interpreter and asserts the override works at RUNTIME (not just
        lexically). Do NOT delete either without the other — they form the
        Step 14 Gap A two-layer safety net (see CLAUDE.md + MEMORY.md commit
        ``1bae0da``). The lexical test guards against override-position
        regressions; the subprocess test guards against override-line
        mutations. A future cleanup sweep that drops one in isolation would
        reopen the silent-no-op regression surface for Pillar 2 Tier 2.
        """
        src = 'async def my_tool(n: int): return n\nif __name__ == "__main__": raise RuntimeError("main fired")'
        code = _build_sandbox_code(src, "my_tool", {"n": 1})
        # The override must appear before the inlined source block
        override_pos = code.find('"not_main"')
        source_pos = code.find("my_tool")
        assert override_pos != -1, "__name__ override not found in sandbox code"
        assert override_pos < source_pos, "__name__ override must precede inlined source"

    def test_main_guard_neutralized_end_to_end_subprocess(self):
        """Execute the generated sandbox code in a real Python interpreter.

        Complements the string-position check above: runs the payload via
        ``python -c`` so the actual interpreter evaluates the ``__name__``
        override. If the neutralization were ever broken (e.g. override
        appears after the source, or the override line is mutated), the
        inlined ``raise RuntimeError("main fired")`` would fire and the
        subprocess would exit non-zero. A clean exit + well-formed JSON on
        stdout proves the override works at runtime — not just lexically.
        """
        import subprocess
        import sys

        src = (
            'async def my_tool(n: int):\n'
            '    return n * 2\n'
            'if __name__ == "__main__":\n'
            '    raise RuntimeError("main fired")\n'
        )
        code = _build_sandbox_code(src, "my_tool", {"n": 21})

        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert proc.returncode == 0, (
            f"sandbox code exited non-zero — __name__ neutralization likely broken. "
            f"stderr: {proc.stderr!r}"
        )
        assert "main fired" not in proc.stderr, (
            "RuntimeError from __main__ guard fired — override did not take effect"
        )
        payload = json.loads(proc.stdout.strip())
        assert payload["error"] is None
        assert payload["result"] == "42"


# ---------------------------------------------------------------------------
# run() — full behavioral test suite
# ---------------------------------------------------------------------------


class TestRunCleanTool:
    """A well-behaved tool should produce no HIGH/CRITICAL findings."""

    @patch("src.verification.behavioral.sandbox_execute")
    def test_clean_tool_no_critical_findings(self, mock_sandbox):
        mock_sandbox.return_value = _sandbox_success("3", elapsed=0.05)
        artifact = _make_artifact(CLEAN_TOOL)
        findings = run(artifact)
        critical = [f for f in findings if f.severity in (Severity.CRITICAL, Severity.HIGH)]
        assert len(critical) == 0

    @patch("src.verification.behavioral.sandbox_execute")
    def test_all_findings_are_tier_2(self, mock_sandbox):
        mock_sandbox.return_value = _sandbox_success("3", elapsed=0.05)
        findings = run(_make_artifact(CLEAN_TOOL))
        for f in findings:
            assert f.tier == 2


class TestRunNoSourceCode:
    def test_no_source_code_returns_info(self):
        artifact = _make_artifact(source_code="", name="empty-tool")
        # source_code="" is falsy
        artifact_none = GeneratedArtifact(
            capability_name="none-tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=None,
        )
        for a in [artifact, artifact_none]:
            findings = run(a)
            assert len(findings) == 1
            assert findings[0].severity == Severity.INFO
            assert findings[0].category == "behavioral"


class TestRunNoPublicFunction:
    def test_private_only_returns_medium(self):
        findings = run(_make_artifact(PRIVATE_ONLY_TOOL))
        assert len(findings) == 1
        assert findings[0].severity == Severity.MEDIUM


class TestRunNoParams:
    @patch("src.verification.behavioral.sandbox_execute")
    def test_parameterless_tool_with_nonempty_output_produces_medium(self, mock_sandbox):
        mock_sandbox.return_value = _sandbox_success('"ok"', elapsed=0.05)
        findings = run(_make_artifact(NO_PARAMS_TOOL))
        assert len(findings) == 1
        assert findings[0].severity == Severity.MEDIUM
        assert findings[0].category == "parameterless_smoke"
        assert "hardcoded/canned" in findings[0].message

    @patch("src.verification.behavioral.sandbox_execute")
    def test_parameterless_tool_with_none_output_no_finding(self, mock_sandbox):
        mock_sandbox.return_value = _sandbox_success("None", elapsed=0.05)
        findings = run(_make_artifact(NO_PARAMS_TOOL))
        assert findings == []

    @patch("src.verification.behavioral.sandbox_execute")
    def test_parameterless_tool_sandbox_failure_no_finding(self, mock_sandbox):
        mock_sandbox.return_value = _sandbox_failure("Docker not found")
        findings = run(_make_artifact(NO_PARAMS_TOOL))
        assert findings == []

    @patch("src.verification.behavioral.sandbox_execute")
    def test_parameterless_tool_with_error_no_finding(self, mock_sandbox):
        mock_sandbox.return_value = _sandbox_success("None", elapsed=0.05, error="TypeError: bad")
        findings = run(_make_artifact(NO_PARAMS_TOOL))
        assert findings == []


class TestRunInconsistentTool:
    """A tool that returns different results for the same input."""

    @patch("src.verification.behavioral.sandbox_execute")
    def test_inconsistent_results_produce_high_finding(self, mock_sandbox):
        call_count = 0

        def side_effect(code, timeout=15, **kwargs):
            nonlocal call_count
            call_count += 1
            # The consistency test calls sandbox twice for the same input.
            # Return different results on alternating calls within consistency test.
            # Calls: nonexistent(1), consistency_run1(2), consistency_run2(3),
            # boundary_zero(4), boundary_neg(5), boundary_large(6), timeout(7),
            # latency_variance(8,9,10), output_variation(11,12)
            if call_count == 2:
                return _sandbox_success("4", elapsed=0.05)
            elif call_count == 3:
                return _sandbox_success("7", elapsed=0.05)
            return _sandbox_success("1", elapsed=0.05)

        mock_sandbox.side_effect = side_effect
        findings = run(_make_artifact(INCONSISTENT_TOOL))
        consistency_findings = [f for f in findings if f.category == "consistency"]
        assert len(consistency_findings) >= 1
        assert consistency_findings[0].severity == Severity.HIGH
        assert "different results" in consistency_findings[0].message


class TestRunTimeoutViolation:
    """A tool that exceeds the timeout threshold (renamed from latency, 5s)."""

    @patch("src.verification.behavioral.sandbox_execute")
    def test_slow_tool_produces_timeout_finding(self, mock_sandbox):
        mock_sandbox.return_value = _sandbox_success("1", elapsed=TIMEOUT_THRESHOLD_S + 1.0)
        findings = run(_make_artifact(SLOW_TOOL))
        timeout_findings = [f for f in findings if f.category == "timeout"]
        assert len(timeout_findings) == 1
        assert timeout_findings[0].severity == Severity.MEDIUM
        assert "execution took" in timeout_findings[0].message


class TestRunBoundaryTimeout:
    """Boundary inputs that cause a timeout."""

    @patch("src.verification.behavioral.sandbox_execute")
    def test_timeout_on_boundary_produces_high_finding(self, mock_sandbox):
        call_count = 0

        def side_effect(code, timeout=15, **kwargs):
            nonlocal call_count
            call_count += 1
            # Make the boundary "large" test (call 6) timeout.
            # Calls: nonexistent(1), consistency_run1(2), consistency_run2(3),
            # boundary_zero(4), boundary_neg(5), boundary_large(6), timeout(7),
            # latency_variance(8,9,10), output_variation(11,12)
            if call_count == 6:
                return _sandbox_timeout()
            return _sandbox_success("1", elapsed=0.05)

        mock_sandbox.side_effect = side_effect
        findings = run(_make_artifact(CLEAN_TOOL))
        boundary_findings = [f for f in findings if f.category == "boundary"]
        assert len(boundary_findings) >= 1
        assert boundary_findings[0].severity == Severity.HIGH
        assert "timed out" in boundary_findings[0].message


class TestRunSandboxFailure:
    """Sandbox failures should not cause unhandled exceptions."""

    @patch("src.verification.behavioral.sandbox_execute")
    def test_all_sandbox_failures_handled_gracefully(self, mock_sandbox):
        mock_sandbox.return_value = _sandbox_failure("Docker not found")
        findings = run(_make_artifact(CLEAN_TOOL))
        # Should not raise, and must return a list (never-raises contract).
        assert isinstance(findings, list)
        # With every sandbox call failing on a non-timeout, non-dep-install
        # stderr, `_test_boundary` must surface one MEDIUM `boundary` finding
        # per test case (zero/negative/large) via the else-branch at
        # behavioral.py:439-451. The `dependency` branch specifically requires
        # "pip install failed" / "No solution found" / "No matching
        # distribution" in stderr, so generic "Docker not found" lands as
        # `boundary`, not `dependency`. We assert on category presence rather
        # than exact count to stay robust to future additions.
        categories = {f.category for f in findings}
        assert "boundary" in categories, (
            f"Expected `boundary` category to surface on sandbox failure, "
            f"got: {sorted(categories)}"
        )
        boundary_findings = [f for f in findings if f.category == "boundary"]
        assert all(f.severity == Severity.MEDIUM for f in boundary_findings)
        assert all(f.tier == 2 for f in boundary_findings)


class TestRunNonexistentEntity:
    """Nonexistent entity test checks for silent swallowing."""

    @patch("src.verification.behavioral.sandbox_execute")
    def test_silent_none_return_produces_finding(self, mock_sandbox):
        mock_sandbox.return_value = _sandbox_success("None", elapsed=0.05)
        findings = run(_make_artifact(CLEAN_TOOL))
        entity_findings = [f for f in findings if f.category == "nonexistent_entity"]
        assert len(entity_findings) == 1
        assert entity_findings[0].severity == Severity.MEDIUM
        assert "silently swallow" in entity_findings[0].message


# ---------------------------------------------------------------------------
# B2.1 — _test_parameterless_smoke (unit tests for the helper directly)
# ---------------------------------------------------------------------------


class TestParameterlessSmoke:
    """Unit tests for the B2.1 parameterless smoke helper."""

    def test_nonempty_output_produces_medium(self):
        mock_sandbox = MagicMock(return_value=_sandbox_success('"status: ok"', elapsed=0.05))
        findings = _test_parameterless_smoke(NO_PARAMS_TOOL, "get_status", mock_sandbox)
        assert len(findings) == 1
        assert findings[0].severity == Severity.MEDIUM
        assert findings[0].category == "parameterless_smoke"

    def test_trivial_none_output_no_finding(self):
        mock_sandbox = MagicMock(return_value=_sandbox_success("None", elapsed=0.05))
        findings = _test_parameterless_smoke(NO_PARAMS_TOOL, "get_status", mock_sandbox)
        assert findings == []

    def test_trivial_empty_string_no_finding(self):
        mock_sandbox = MagicMock(return_value=_sandbox_success("''", elapsed=0.05))
        findings = _test_parameterless_smoke(NO_PARAMS_TOOL, "get_status", mock_sandbox)
        assert findings == []

    def test_sandbox_failure_no_finding(self):
        mock_sandbox = MagicMock(return_value=_sandbox_failure("Docker not found"))
        findings = _test_parameterless_smoke(NO_PARAMS_TOOL, "get_status", mock_sandbox)
        assert findings == []

    def test_tool_error_no_finding(self):
        mock_sandbox = MagicMock(return_value=_sandbox_success("None", elapsed=0.05, error="TypeError: bad"))
        findings = _test_parameterless_smoke(NO_PARAMS_TOOL, "get_status", mock_sandbox)
        assert findings == []

    def test_finding_is_tier_2(self):
        mock_sandbox = MagicMock(return_value=_sandbox_success('"canned data"', elapsed=0.05))
        findings = _test_parameterless_smoke(NO_PARAMS_TOOL, "get_status", mock_sandbox)
        assert findings[0].tier == 2


# ---------------------------------------------------------------------------
# B2.3 — _test_output_variation (unit tests for the helper directly)
# ---------------------------------------------------------------------------


_SCHEMA_INT = {"n": {"type": "int"}}
_SCHEMA_EMPTY: dict = {}


class TestOutputVariation:
    """Unit tests for the B2.3 output-variation helper."""

    def test_identical_nontrivial_outputs_flagged(self):
        mock_sandbox = MagicMock(return_value=_sandbox_success('"Alice"', elapsed=0.05))
        findings = _test_output_variation(CLEAN_TOOL, "compute_sum", {"a": {"type": "int"}, "b": {"type": "int"}}, mock_sandbox)
        assert len(findings) == 1
        assert findings[0].severity == Severity.MEDIUM
        assert findings[0].category == "output_variation"
        assert "ignoring parameters" in findings[0].message

    def test_different_outputs_no_finding(self):
        call_count = 0

        def side_effect(code, timeout=15, **kwargs):
            nonlocal call_count
            call_count += 1
            return _sandbox_success(str(call_count), elapsed=0.05)

        mock_sandbox = MagicMock(side_effect=side_effect)
        findings = _test_output_variation(CLEAN_TOOL, "compute_sum", {"a": {"type": "int"}, "b": {"type": "int"}}, mock_sandbox)
        assert findings == []

    def test_sandbox_failure_no_finding(self):
        mock_sandbox = MagicMock(return_value=_sandbox_failure("Docker not found"))
        findings = _test_output_variation(CLEAN_TOOL, "compute_sum", {"a": {"type": "int"}, "b": {"type": "int"}}, mock_sandbox)
        assert findings == []

    def test_trivial_identical_outputs_not_flagged(self):
        """Trivial values like None/0 being identical is not suspicious."""
        mock_sandbox = MagicMock(return_value=_sandbox_success("None", elapsed=0.05))
        findings = _test_output_variation(CLEAN_TOOL, "compute_sum", {"a": {"type": "int"}, "b": {"type": "int"}}, mock_sandbox)
        assert findings == []

    def test_empty_schema_returns_no_findings(self):
        findings = _test_output_variation(CLEAN_TOOL, "compute_sum", {})
        assert findings == []

    def test_schema_with_identical_boundary_and_large_no_findings(self):
        """If boundary==large (e.g. unknown types default both to ""), skip check."""
        findings = _test_output_variation(CLEAN_TOOL, "compute_sum", {"x": {"type": "unknown"}})
        assert findings == []

    def test_finding_is_tier_2(self):
        mock_sandbox = MagicMock(return_value=_sandbox_success('"canned"', elapsed=0.05))
        findings = _test_output_variation(CLEAN_TOOL, "compute_sum", {"a": {"type": "int"}, "b": {"type": "int"}}, mock_sandbox)
        assert findings[0].tier == 2


# ---------------------------------------------------------------------------
# A2 — _test_timeout (renamed from old _test_latency, 5s threshold)
# ---------------------------------------------------------------------------


_SCHEMA_TWO_INT = {"a": {"type": "int"}, "b": {"type": "int"}}


class TestTimeoutHelper:
    """Unit tests for the _test_timeout helper (renamed from old _test_latency)."""

    def test_slow_execution_flagged_as_timeout(self):
        mock_sandbox = MagicMock(
            return_value=_sandbox_success("3", elapsed=TIMEOUT_THRESHOLD_S + 1.0)
        )
        findings = _test_timeout(CLEAN_TOOL, "compute_sum", _SCHEMA_TWO_INT, mock_sandbox)
        assert len(findings) == 1
        assert findings[0].category == "timeout"
        assert findings[0].location == "timeout"
        assert findings[0].severity == Severity.MEDIUM
        assert "timeout threshold" in findings[0].message

    def test_fast_execution_no_finding(self):
        mock_sandbox = MagicMock(return_value=_sandbox_success("3", elapsed=0.05))
        findings = _test_timeout(CLEAN_TOOL, "compute_sum", _SCHEMA_TWO_INT, mock_sandbox)
        assert findings == []

    def test_empty_schema_returns_no_findings(self):
        findings = _test_timeout(CLEAN_TOOL, "compute_sum", {})
        assert findings == []


# ---------------------------------------------------------------------------
# A2 — _test_latency (variance-based mock-smell detection)
# ---------------------------------------------------------------------------


class TestLatencyVariance:
    """Unit tests for the new variance-based _test_latency helper.

    Two-condition gate: mean < 1ms AND cv < 0.10. Both must hold.
    """

    def test_latency_flags_fast_uniform(self):
        # All three runs return identical, very small elapsed.
        mock_sandbox = MagicMock(return_value=_sandbox_success("42", elapsed=0.0001))
        findings = _test_latency(CLEAN_TOOL, "compute_sum", _SCHEMA_TWO_INT, mock_sandbox)
        assert len(findings) == 1
        assert findings[0].category == "latency"
        assert findings[0].location == "latency"
        assert findings[0].severity == Severity.MEDIUM
        assert findings[0].tier == 2
        assert "suspiciously fast and uniform" in findings[0].message

    def test_latency_ignores_slow(self):
        # 5ms is above the 1ms mean threshold — fails first condition.
        mock_sandbox = MagicMock(return_value=_sandbox_success("42", elapsed=0.005))
        findings = _test_latency(CLEAN_TOOL, "compute_sum", _SCHEMA_TWO_INT, mock_sandbox)
        assert findings == []

    def test_latency_ignores_variable(self):
        # High variance across runs — fails the cv < 0.10 condition.
        elapsed_values = [0.0001, 0.0005, 0.0001]
        call_count = 0

        def side_effect(code, timeout=15, **kwargs):
            nonlocal call_count
            value = elapsed_values[call_count % len(elapsed_values)]
            call_count += 1
            return _sandbox_success("42", elapsed=value)

        mock_sandbox = MagicMock(side_effect=side_effect)
        findings = _test_latency(CLEAN_TOOL, "compute_sum", _SCHEMA_TWO_INT, mock_sandbox)
        assert findings == []

    def test_latency_handles_insufficient_samples(self):
        # All sandbox calls fail to parse — no samples to compute variance.
        mock_sandbox = MagicMock(return_value=_sandbox_failure("Docker not found"))
        findings = _test_latency(CLEAN_TOOL, "compute_sum", _SCHEMA_TWO_INT, mock_sandbox)
        assert findings == []

    def test_latency_skips_empty_schema(self):
        # Empty schema short-circuits before any sandbox call.
        mock_sandbox = MagicMock()
        findings = _test_latency(CLEAN_TOOL, "compute_sum", {}, mock_sandbox)
        assert findings == []
        assert mock_sandbox.call_count == 0


# ---------------------------------------------------------------------------
# Step 14.12 — allow_network plumbing + network-disconnect differential
# ---------------------------------------------------------------------------


class TestAllowNetworkPropagates:
    """``run(artifact, allow_network=True)`` must forward the flag to every
    ``sandbox_execute`` call inside Tier 2.
    """

    @patch("src.verification.sandbox_tool.cleanup_cached_image")
    @patch("src.verification.sandbox_tool.sandbox_execute_with_cached_image")
    @patch("src.verification.sandbox_tool.prepare_cached_image")
    def test_allow_network_true_forwards_to_cached_image_probes(
        self, mock_prepare, mock_cached_sandbox, mock_cleanup,
    ):
        """All Tier 2 probes against the cached image receive allow_network=True.

        Security audit ext10 #106: pre-fix this test asserted the
        dep-check call (a separate ``sandbox_execute`` invocation per
        probe) received ``allow_network=True``. After ext10 #106 the
        dep check is subsumed into ``prepare_cached_image`` (which
        ALWAYS uses ``--network bridge`` because pip install needs
        network — the policy applies to the tool execution, not the
        image build). The new equivalent contract: every probe that
        runs against the cached image receives the requested
        ``allow_network`` value.
        """
        mock_prepare.return_value = ("jit-sandbox-cached-deadbeef", None)
        mock_cached_sandbox.return_value = _sandbox_success("42")
        artifact = GeneratedArtifact(
            capability_name="net-tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=CLEAN_TOOL,
            pip_packages=["requests"],
        )

        run(artifact, allow_network=True)

        # prepare_cached_image was called with the declared packages
        # (network policy not part of the build phase).
        assert mock_prepare.call_args_list, "prepare_cached_image not called"
        assert mock_prepare.call_args_list[0].args[0] == ["requests"]

        # Every probe invocation routed through the cached-image helper
        # with allow_network=True.
        assert mock_cached_sandbox.call_args_list, (
            "sandbox_execute_with_cached_image not invoked for any probe"
        )
        for call in mock_cached_sandbox.call_args_list:
            assert call.kwargs.get("allow_network") is True, (
                f"probe call missing allow_network=True: {call}"
            )

        # Cached image cleaned up at the end (one call per run).
        assert mock_cleanup.call_args_list
        assert (
            mock_cleanup.call_args_list[0].args[0] == "jit-sandbox-cached-deadbeef"
        )

    @patch("src.verification.behavioral.sandbox_execute")
    def test_allow_network_false_by_default(self, mock_sandbox):
        """Omitting the flag keeps offline-by-default."""
        mock_sandbox.return_value = _sandbox_success("42")
        artifact = GeneratedArtifact(
            capability_name="offline-tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=CLEAN_TOOL,
        )

        run(artifact)

        # The partial wraps sandbox_execute; the dep_check isn't present when
        # extra_packages is empty. Instead assert the partial's keyword.
        # The findings should run the behavioral tests via partial(sandbox_execute, ...)
        # with allow_network=False keyword bound.
        # This test passes iff run() returns without crashing and no call to
        # sandbox_execute leaked an unexpected allow_network=True.
        calls_with_true = [
            c for c in mock_sandbox.call_args_list
            if c.kwargs.get("allow_network") is True
        ]
        assert calls_with_true == []


class TestNetworkDisconnectDifferential:
    """Tier 2's new differential test fires only when needs_network=True AND
    allow_network=True. It catches silent fallbacks (F2 in proposal §Pillar 2).
    """

    @patch("src.verification.behavioral.sandbox_execute")
    def test_differential_flags_silent_fallback(self, mock_sandbox):
        """Both on and off produce plausible dicts → CRITICAL silent fallback."""
        # Both calls return plausible "result" payload.
        mock_sandbox.return_value = {
            "stdout": json.dumps({"result": "live data"}) + "\n",
            "stderr": "",
            "exit_code": 0,
            "success": True,
        }
        artifact = GeneratedArtifact(
            capability_name="fake-api-tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=CLEAN_TOOL,
            needs_network=True,
        )

        findings = run(artifact, allow_network=True)

        silent_fallback = [
            f for f in findings if f.category == "network_silent_fallback"
        ]
        assert len(silent_fallback) == 1
        assert silent_fallback[0].severity == Severity.CRITICAL

    @patch("src.verification.behavioral.sandbox_execute")
    def test_differential_legitimate_network_tool_passes(self, mock_sandbox):
        """Online succeeds, offline fails → INFO finding (legitimate)."""
        call_count = {"n": 0}

        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            # First few calls (standard behavioral tests) succeed;
            # then the differential: allow_network=True → success,
            # allow_network=False → failure.
            if kwargs.get("allow_network") is True:
                return {
                    "stdout": json.dumps({"result": "live data"}) + "\n",
                    "stderr": "",
                    "exit_code": 0,
                    "success": True,
                }
            else:
                return {
                    "stdout": "",
                    "stderr": "socket.gaierror",
                    "exit_code": 1,
                    "success": False,
                }

        mock_sandbox.side_effect = side_effect
        artifact = GeneratedArtifact(
            capability_name="legit-api-tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=CLEAN_TOOL,
            needs_network=True,
        )

        findings = run(artifact, allow_network=True)
        diffs = [
            f for f in findings
            if f.category == "network_disconnect_differential"
        ]
        assert len(diffs) == 1
        assert diffs[0].severity == Severity.INFO

    @patch("src.verification.behavioral.sandbox_execute")
    def test_differential_skipped_when_needs_network_false(self, mock_sandbox):
        """needs_network=False → differential never fires."""
        mock_sandbox.return_value = _sandbox_success("42")
        artifact = GeneratedArtifact(
            capability_name="pure-compute",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=CLEAN_TOOL,
            needs_network=False,
        )

        findings = run(artifact, allow_network=True)
        diffs = [
            f for f in findings
            if f.category in (
                "network_disconnect_differential",
                "network_silent_fallback",
            )
        ]
        assert diffs == []

    @patch("src.verification.behavioral.sandbox_execute")
    def test_differential_skipped_when_allow_network_false(self, mock_sandbox):
        """Even with needs_network=True, if the run is offline the
        differential is vacuous and doesn't fire.
        """
        mock_sandbox.return_value = _sandbox_success("42")
        artifact = GeneratedArtifact(
            capability_name="offline-run",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=CLEAN_TOOL,
            needs_network=True,
        )

        findings = run(artifact, allow_network=False)
        diffs = [
            f for f in findings
            if f.category in (
                "network_disconnect_differential",
                "network_silent_fallback",
            )
        ]
        assert diffs == []

    @patch("src.verification.behavioral.sandbox_execute")
    def test_differential_fires_critical_on_real_sandbox_payload_shape(
        self, mock_sandbox,
    ):
        """Security audit finding #19 (2026-04-20) regression guard.

        The sandbox payload at ``behavioral.py:246`` always emits
        ``"error": None`` on success. Pre-fix, ``_parse`` used
        ``if "error" in parsed:`` (key presence) which returned
        ``(False, None)`` on every successful run, making the CRITICAL
        ``network_silent_fallback`` branch dead code.

        This test uses the EXACT shape the real sandbox emits — with
        ``"error": None`` explicitly present — and asserts CRITICAL
        fires when both online + offline produce plausible output.
        Pre-fix would silently fall through to the "both failed"
        MEDIUM branch instead.
        """
        # Real sandbox payload contract: both on/off succeed, both emit
        # "error": None → silent fallback (hardcoded data).
        real_payload = {
            "stdout": json.dumps({
                "result": "42",
                "elapsed": 0.01,
                "error": None,
            }) + "\n",
            "stderr": "",
            "exit_code": 0,
            "success": True,
        }
        mock_sandbox.return_value = real_payload
        artifact = GeneratedArtifact(
            capability_name="hardcoded-api-tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=CLEAN_TOOL,
            needs_network=True,
        )

        findings = run(artifact, allow_network=True)

        silent_fallback = [
            f for f in findings if f.category == "network_silent_fallback"
        ]
        assert len(silent_fallback) == 1, (
            f"CRITICAL network_silent_fallback must fire on real sandbox "
            f"payload shape (with 'error': None). Got findings: "
            f"{[f.category for f in findings]}. Pre-fix parser used key "
            f"presence, making this branch dead code."
        )
        assert silent_fallback[0].severity == Severity.CRITICAL