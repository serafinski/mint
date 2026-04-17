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
        # Should not raise; findings list may be empty or contain info-level items
        assert isinstance(findings, list)


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