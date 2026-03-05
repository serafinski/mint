"""Tier 2: Behavioral testing for generated capabilities.

Generates edge-case test inputs, executes them in the Docker sandbox,
and detects behavioral contamination. No LLM calls — pure Python.

Four test categories:
  - Nonexistent entity: empty / missing / invalid inputs
  - Consistency: same input twice must produce same output
  - Boundary: min/max values, zero, negative, empty strings
  - Latency: execution must complete within a reasonable bound
"""

import ast
import json
import textwrap
import time
from typing import Any

from src.schemas.task import GeneratedArtifact
from src.schemas.verification import Severity, VerificationFinding
from src.verification.sandbox_tool import sandbox_execute

LATENCY_THRESHOLD_S = 5.0


# ---------------------------------------------------------------------------
# Parameter schema extraction from source code AST
# ---------------------------------------------------------------------------


def extract_parameter_schema(source_code: str) -> dict[str, dict]:
    """Extract {param_name: {"type": type_str}} from the async function signature.

    Skips private functions (names starting with ``_``). Returns the schema
    for the first public async function found.
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if node.name.startswith("_"):
            continue
        schema: dict[str, dict] = {}
        for arg in node.args.args:
            annotation = arg.annotation
            if isinstance(annotation, ast.Subscript):
                # Annotated[X, ...] -> extract the first type argument
                if isinstance(annotation.slice, ast.Tuple):
                    type_node = annotation.slice.elts[0]
                    schema[arg.arg] = {"type": ast.unparse(type_node)}
                else:
                    schema[arg.arg] = {"type": ast.unparse(annotation.slice)}
            elif annotation:
                schema[arg.arg] = {"type": ast.unparse(annotation)}
            else:
                schema[arg.arg] = {"type": "unknown"}
        return schema
    return {}


def _extract_function_name(source_code: str) -> str | None:
    """Return the name of the first public async function in source_code."""
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and not node.name.startswith("_"):
            return node.name
    return None


# ---------------------------------------------------------------------------
# Test input generation
# ---------------------------------------------------------------------------


def _generate_boundary_inputs(schema: dict[str, dict]) -> dict[str, Any]:
    """Generate a valid-looking set of boundary inputs from param types."""
    inputs: dict[str, Any] = {}
    for name, info in schema.items():
        type_str = info.get("type", "unknown")
        if type_str in ("int", "float"):
            inputs[name] = 0
        elif type_str == "str":
            inputs[name] = ""
        elif type_str == "bool":
            inputs[name] = False
        elif type_str == "list":
            inputs[name] = []
        elif type_str == "dict":
            inputs[name] = {}
        else:
            inputs[name] = ""
    return inputs


def _generate_negative_inputs(schema: dict[str, dict]) -> dict[str, Any]:
    """Generate negative / edge-case inputs for numeric params."""
    inputs: dict[str, Any] = {}
    for name, info in schema.items():
        type_str = info.get("type", "unknown")
        if type_str == "int":
            inputs[name] = -1
        elif type_str == "float":
            inputs[name] = -1.0
        elif type_str == "str":
            inputs[name] = "nonexistent_entity_xyz"
        elif type_str == "bool":
            inputs[name] = True
        else:
            inputs[name] = ""
    return inputs


def _generate_large_inputs(schema: dict[str, dict]) -> dict[str, Any]:
    """Generate large / max-boundary inputs."""
    inputs: dict[str, Any] = {}
    for name, info in schema.items():
        type_str = info.get("type", "unknown")
        if type_str == "int":
            inputs[name] = 1_000_000
        elif type_str == "float":
            inputs[name] = 1e12
        elif type_str == "str":
            inputs[name] = "a" * 10_000
        else:
            inputs[name] = ""
    return inputs


# ---------------------------------------------------------------------------
# Sandbox code builder
# ---------------------------------------------------------------------------


def _build_sandbox_code(source_code: str, func_name: str, inputs: dict[str, Any]) -> str:
    """Build a Python snippet that runs the tool function inside the sandbox."""
    inputs_json = json.dumps(inputs)
    return textwrap.dedent(f"""\
        import asyncio
        import json
        import time

        __name__ = "not_main"  # prevent if __name__ == "__main__": mcp.run() from firing
        # --- Inline the tool source ---
        {textwrap.indent(source_code, "        ").strip()}
        # --- End tool source ---

        async def _run():
            start = time.monotonic()
            try:
                result = await {func_name}(**json.loads({inputs_json!r}))
                elapsed = time.monotonic() - start
                print(json.dumps({{"result": repr(result), "elapsed": elapsed, "error": None}}))
            except Exception as exc:
                elapsed = time.monotonic() - start
                print(json.dumps({{"result": None, "elapsed": elapsed, "error": f"{{type(exc).__name__}}: {{exc}}"}}))

        asyncio.run(_run())
    """)


def _parse_sandbox_output(result: dict) -> dict | None:
    """Parse the JSON output from a sandbox execution. Returns None on failure."""
    if not result.get("success"):
        return None
    stdout = result.get("stdout", "").strip()
    if not stdout:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Individual test categories
# ---------------------------------------------------------------------------


def _test_nonexistent_entity(
    source_code: str, func_name: str, schema: dict[str, dict]
) -> list[VerificationFinding]:
    """Test with empty / nonexistent inputs. The tool should not crash silently."""
    findings: list[VerificationFinding] = []
    if not schema:
        return findings

    # Empty string for all params
    empty_inputs = {name: "" if info.get("type") != "int" else 0 for name, info in schema.items()}
    code = _build_sandbox_code(source_code, func_name, empty_inputs)
    result = sandbox_execute(code, timeout=15)

    parsed = _parse_sandbox_output(result)
    if parsed is None and not result.get("success"):
        # The tool crashed outright — that's acceptable for invalid input.
        # But if it returned success=True with no output, that's suspicious.
        pass
    elif parsed and parsed.get("error") is None and parsed.get("result") in ("None", "''", '""'):
        findings.append(
            VerificationFinding(
                tier=2,
                category="nonexistent_entity",
                severity=Severity.MEDIUM,
                message=(
                    "Tool returned empty/None for nonexistent entity inputs "
                    "without raising an error — may silently swallow invalid input"
                ),
            )
        )

    return findings


def _test_consistency(
    source_code: str, func_name: str, schema: dict[str, dict]
) -> list[VerificationFinding]:
    """Run the same input twice and check results match."""
    findings: list[VerificationFinding] = []
    if not schema:
        return findings

    inputs = _generate_boundary_inputs(schema)
    code = _build_sandbox_code(source_code, func_name, inputs)

    result1 = sandbox_execute(code, timeout=15)
    result2 = sandbox_execute(code, timeout=15)

    parsed1 = _parse_sandbox_output(result1)
    parsed2 = _parse_sandbox_output(result2)

    if parsed1 is not None and parsed2 is not None:
        # Compare result values (not elapsed time)
        if parsed1.get("result") != parsed2.get("result"):
            findings.append(
                VerificationFinding(
                    tier=2,
                    category="consistency",
                    severity=Severity.HIGH,
                    message=(
                        "Tool returned different results for identical inputs: "
                        f"run1={parsed1.get('result')!r} vs run2={parsed2.get('result')!r}"
                    ),
                )
            )
        if parsed1.get("error") != parsed2.get("error"):
            findings.append(
                VerificationFinding(
                    tier=2,
                    category="consistency",
                    severity=Severity.HIGH,
                    message=(
                        "Tool raised different errors for identical inputs: "
                        f"run1={parsed1.get('error')!r} vs run2={parsed2.get('error')!r}"
                    ),
                )
            )

    return findings


def _test_boundary(
    source_code: str, func_name: str, schema: dict[str, dict]
) -> list[VerificationFinding]:
    """Test boundary values: zero, negative, large."""
    findings: list[VerificationFinding] = []
    if not schema:
        return findings

    test_cases = [
        ("zero/empty", _generate_boundary_inputs(schema)),
        ("negative", _generate_negative_inputs(schema)),
        ("large", _generate_large_inputs(schema)),
    ]

    for label, inputs in test_cases:
        code = _build_sandbox_code(source_code, func_name, inputs)
        result = sandbox_execute(code, timeout=15)

        if not result.get("success") and "Timeout" in result.get("stderr", ""):
            findings.append(
                VerificationFinding(
                    tier=2,
                    category="boundary",
                    severity=Severity.HIGH,
                    message=f"Tool timed out on {label} boundary inputs: {inputs}",
                )
            )

    return findings


def _test_latency(
    source_code: str, func_name: str, schema: dict[str, dict]
) -> list[VerificationFinding]:
    """Check that execution completes within a reasonable time."""
    findings: list[VerificationFinding] = []
    if not schema:
        return findings

    inputs = _generate_boundary_inputs(schema)
    code = _build_sandbox_code(source_code, func_name, inputs)

    start = time.monotonic()
    result = sandbox_execute(code, timeout=15)
    wall_time = time.monotonic() - start

    parsed = _parse_sandbox_output(result)
    # Use the tool-reported elapsed if available, otherwise wall time
    elapsed = parsed.get("elapsed", wall_time) if parsed else wall_time

    if elapsed > LATENCY_THRESHOLD_S:
        findings.append(
            VerificationFinding(
                tier=2,
                category="latency",
                severity=Severity.MEDIUM,
                message=f"Tool execution took {elapsed:.2f}s (threshold: {LATENCY_THRESHOLD_S}s)",
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run(artifact: GeneratedArtifact) -> list[VerificationFinding]:
    """Run all four behavioral test categories on an artifact.

    Returns a list of ``VerificationFinding`` objects (tier=2).
    """
    source_code = artifact.source_code
    if not source_code:
        return [
            VerificationFinding(
                tier=2,
                category="behavioral",
                severity=Severity.INFO,
                message="No source code to test — skipping behavioral tests",
            )
        ]

    func_name = _extract_function_name(source_code)
    if not func_name:
        return [
            VerificationFinding(
                tier=2,
                category="behavioral",
                severity=Severity.MEDIUM,
                message="No public async function found in source code — cannot run behavioral tests",
            )
        ]

    schema = extract_parameter_schema(source_code)
    if not schema:
        return [
            VerificationFinding(
                tier=2,
                category="behavioral",
                severity=Severity.INFO,
                message="No parameters found — skipping behavioral tests (parameterless tool)",
            )
        ]

    findings: list[VerificationFinding] = []
    findings.extend(_test_nonexistent_entity(source_code, func_name, schema))
    findings.extend(_test_consistency(source_code, func_name, schema))
    findings.extend(_test_boundary(source_code, func_name, schema))
    findings.extend(_test_latency(source_code, func_name, schema))

    return findings
