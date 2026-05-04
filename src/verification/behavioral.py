"""Tier 2: Behavioral testing for generated capabilities.

Generates edge-case test inputs, executes them in the Docker sandbox,
and detects behavioral contamination. No LLM calls — pure Python.

Regular probes:
  - Parameterless smoke: non-empty output from zero-input tools
  - Nonexistent entity: empty / missing / invalid inputs
  - Consistency: same input twice must produce the same output
  - Boundary: zero, empty, negative, extreme numeric, and large inputs
  - KNN CSV identifier column: valid fixture with an integer ID column
  - Timeout: execution must complete within a reasonable timeout
  - Latency variance: suspiciously fast and uniform execution
  - Output variation: different inputs should produce different outputs

For artifacts declared as network-dependent, an optional network-disconnect
differential compares online and offline behavior when the task permits
network access.
"""

import ast
import json
import re
import statistics
import textwrap
import time
from functools import partial
from typing import Any

from src.schemas.task import GeneratedArtifact
from src.schemas.verification import Severity, VerificationFinding
from src.verification.sandbox_tool import resolve_extra_packages, sandbox_execute

TIMEOUT_THRESHOLD_S = 5.0


# ---------------------------------------------------------------------------
# Parameter schema extraction from source code AST
# ---------------------------------------------------------------------------


def _schema_from_async_def(node: ast.AsyncFunctionDef) -> dict[str, dict]:
    """Return ``{param_name: {"type": type_str}}`` for an async def node."""
    schema: dict[str, dict] = {}
    for arg in (*node.args.args, *node.args.kwonlyargs):
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


def extract_parameter_schema(
    source_code: str,
    func_name: str | None = None,
) -> dict[str, dict]:
    """Extract {param_name: {"type": type_str}} from the target function signature.

    When ``func_name`` is omitted, the function is resolved by
    ``_extract_function_name`` so parameter generation and sandbox execution
    use the same entrypoint.
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return {}

    target_name = func_name or _extract_function_name(source_code)
    if target_name is None:
        return {}

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == target_name:
            return _schema_from_async_def(node)
    return {}


def _find_mcp_tool_function(source_code: str) -> str | None:
    """Return the name of the ``@mcp.tool``-decorated public async function.

    Returns the function name when exactly one public async def carries
    an ``@mcp.tool`` / ``@mcp.tool()`` decorator. Returns ``None`` on
    parse error, zero matches (fall back to the single-async heuristic
    in ``_extract_function_name``), or multiple matches (ambiguous;
    caller should surface this as a verification failure rather than
    silently pick one).

    Accepts both bare ``@mcp.tool`` and call-form ``@mcp.tool(...)``.
    Ignores non-``.tool`` decorators (``@lru_cache``, ``@contextmanager``).
    Only scans top-level nodes — nested ``async def`` inside a helper
    doesn't count.
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return None

    matches: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if node.name.startswith("_"):
            continue
        for dec in node.decorator_list:
            # ``@mcp.tool`` → dec is ast.Attribute; ``@mcp.tool(...)`` →
            # dec is ast.Call whose .func is ast.Attribute.
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Attribute) and target.attr == "tool":
                matches.append(node.name)
                break

    if len(matches) == 1:
        return matches[0]
    return None


def _extract_function_name(source_code: str) -> str | None:
    """Return the name of the MCP tool entrypoint in ``source_code``.

    Resolution order:
    1. **Decorator match** via :func:`_find_mcp_tool_function` — unambiguous,
       uses the FastMCP ``@mcp.tool`` signal.
    2. **Zero decorator matches, exactly one public async def** —
       legacy / non-FastMCP artifacts. Use that function, log a WARNING
       that the decorator was missing.
    3. **Ambiguous** (zero decorators + multiple public async defs, or
       parse error) — return ``None``. Callers must fail verification
       rather than silently pick one.
    """
    decorated = _find_mcp_tool_function(source_code)
    if decorated is not None:
        return decorated

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return None

    public_async = [
        node.name
        for node in ast.iter_child_nodes(tree)
        if isinstance(node, ast.AsyncFunctionDef) and not node.name.startswith("_")
    ]
    if len(public_async) == 1:
        import logging
        logging.getLogger(__name__).warning(
            "Tool source has no @mcp.tool-decorated function; falling back "
            "to the single public async def %r. This is a legacy pattern — "
            "future artifacts should declare @mcp.tool explicitly.",
            public_async[0],
        )
        return public_async[0]

    # Zero or multiple public async defs and no decorator — ambiguous.
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
    """Generate the PRIMARY (-1/-1.0) negative-input variant.

    Back-compat shim for callers that want a single representative
    variant (e.g., latency-variance probe). Use
    :func:`_generate_negative_input_variants` for the full edge-case
    matrix consumed by the boundary probe.
    """
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


def _generate_negative_input_variants(
    schema: dict[str, dict],
) -> list[tuple[str, dict[str, Any]]]:
    """Generate multiple negative / edge-case input variants.

    Returns ``(label, inputs)`` pairs so the boundary probe can run each
    variant independently and label findings by the specific edge case that
    triggered them.

    Variants per type:
      - ``int``: ``-1``, ``-2**31`` (32-bit min), ``-2**62`` (very large)
      - ``float``: ``-1.0``, ``-inf``, ``+inf``, ``NaN``, ``-1e308``
      - non-numeric types: single representative (matches the back-compat
        shim's behaviour)
    """
    int_variants = [
        ("int_neg_one", -1),
        ("int_neg_31bit", -(2**31)),
        ("int_neg_62bit", -(2**62)),
    ]
    float_variants = [
        ("float_neg_one", -1.0),
        ("float_neg_inf", float("-inf")),
        ("float_pos_inf", float("inf")),
        ("float_nan", float("nan")),
        ("float_neg_underflow", -1e308),
    ]

    # Build a base "fallback" dict for non-numeric types so callers always
    # get a valid kwargs dict per variant.
    base: dict[str, Any] = {}
    numeric_params: list[tuple[str, str]] = []  # (param_name, "int" | "float")
    for name, info in schema.items():
        type_str = info.get("type", "unknown")
        if type_str == "int":
            numeric_params.append((name, "int"))
        elif type_str == "float":
            numeric_params.append((name, "float"))
        elif type_str == "str":
            base[name] = "nonexistent_entity_xyz"
        elif type_str == "bool":
            base[name] = True
        else:
            base[name] = ""

    if not numeric_params:
        # No numeric params — emit a single representative variant from
        # the back-compat helper. Boundary probe still gets ONE shot at
        # the schema's non-numeric edge cases.
        return [("non_numeric", _generate_negative_inputs(schema))]

    variants: list[tuple[str, dict[str, Any]]] = []
    for param_name, type_str in numeric_params:
        per_param_variants = (
            int_variants if type_str == "int" else float_variants
        )
        for label, value in per_param_variants:
            kwargs = dict(base)
            kwargs[param_name] = value
            # Fill OTHER numeric params with their primary -1/-1.0 so
            # the probe focuses on one variable at a time.
            for other_name, other_type in numeric_params:
                if other_name == param_name:
                    continue
                kwargs[other_name] = -1 if other_type == "int" else -1.0
            # Keep the label as part of the variant identity rather than
            # relying on dict equality; NaN compares unequal to itself.
            variants.append((f"{param_name}/{label}", kwargs))

    return variants


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
        elif type_str == "list":
            inputs[name] = list(range(1000))
        elif type_str == "dict":
            inputs[name] = {f"key_{i}": i for i in range(100)}
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
                error = f"{{type(exc).__name__}}: {{exc}}"
                print(json.dumps({{"result": None, "elapsed": elapsed, "error": error}}))

        asyncio.run(_run())
    """)


def _build_sandbox_code_with_tempfiles(
    source_code: str,
    func_name: str,
    inputs: dict[str, Any],
    temp_files: dict[str, str],
    path_inputs: dict[str, str],
) -> str:
    """Build sandbox code that writes temp files and passes their paths to the tool."""
    inputs_json = json.dumps(inputs)
    temp_files_json = json.dumps(temp_files)
    path_inputs_json = json.dumps(path_inputs)
    return textwrap.dedent(f"""\
        import asyncio
        import json
        import tempfile
        import time
        from pathlib import Path

        __name__ = "not_main"  # prevent if __name__ == "__main__": mcp.run() from firing
        # --- Inline the tool source ---
        {textwrap.indent(source_code, "        ").strip()}
        # --- End tool source ---

        async def _run():
            start = time.monotonic()
            try:
                _inputs = json.loads({inputs_json!r})
                with tempfile.TemporaryDirectory() as _tmpdir:
                    _tmp = Path(_tmpdir)
                    for _name, _content in json.loads({temp_files_json!r}).items():
                        (_tmp / _name).write_text(_content, encoding="utf-8")
                    for _param, _relpath in json.loads({path_inputs_json!r}).items():
                        _inputs[_param] = str(_tmp / _relpath)
                    result = await {func_name}(**_inputs)
                elapsed = time.monotonic() - start
                print(json.dumps({{"result": repr(result), "elapsed": elapsed, "error": None}}))
            except Exception as exc:
                elapsed = time.monotonic() - start
                error = f"{{type(exc).__name__}}: {{exc}}"
                print(json.dumps({{"result": None, "elapsed": elapsed, "error": error}}))

        asyncio.run(_run())
    """)


def _parse_sandbox_output(result: dict) -> dict | None:
    """Parse the JSON output from a sandbox execution. Returns None on failure.

    Delegates to the shared ``parse_sandbox_last_json_line`` helper so
    import warnings or debug prints before the final JSON line do not make
    the probe inconclusive.
    """
    from src.verification.sandbox_parse import parse_sandbox_last_json_line
    return parse_sandbox_last_json_line(result)


# ---------------------------------------------------------------------------
# Individual test categories
# ---------------------------------------------------------------------------

_TRIVIAL_RESULTS = {"None", "''", '""', "[]", "{}", "0", "False", "True"}


def _test_parameterless_smoke(
    source_code: str,
    func_name: str,
    _sandbox=sandbox_execute,
) -> list[VerificationFinding]:
    """Smoke-test a parameterless tool: flag non-empty canned-looking output.

    A tool with no parameters cannot perform dynamic computation based on caller
    inputs, so non-trivial output is a strong signal of hardcoded/canned data.
    """
    code = _build_sandbox_code(source_code, func_name, {})
    result = _sandbox(code, timeout=15)
    parsed = _parse_sandbox_output(result)

    if parsed is None or parsed.get("error") is not None:
        return []

    result_val = parsed.get("result", "None")
    if result_val not in _TRIVIAL_RESULTS:
        return [
            VerificationFinding(
                tier=2,
                category="parameterless_smoke",
                severity=Severity.MEDIUM,
                message=(
                    f"Parameterless tool returned non-empty output: {result_val!r} — "
                    "may be returning hardcoded/canned data"
                ),
                location="parameterless_smoke",
            )
        ]
    return []


def _test_knn_identifier_column_csv(
    source_code: str,
    func_name: str,
    schema: dict[str, dict],
    _sandbox=sandbox_execute,
) -> list[VerificationFinding]:
    """Probe KNN CSV tools with a valid fixture-shaped file containing an integer ID column.

    This catches the common failure mode where a tool imputes *all* numeric
    columns, including identifier columns such as ``id`` / ``*_id``, then
    crashes or mutates schema on reassignment.
    """
    lowered = source_code.lower()
    if "knnimputer" not in lowered and "sklearn.impute" not in lowered:
        return []
    if "read_csv" not in lowered:
        return []

    csv_params = [
        name
        for name, info in schema.items()
        if info.get("type") == "str"
        and any(token in name.lower() for token in ("csv", "path", "file"))
    ]
    if len(csv_params) != 1:
        return []

    path_param = csv_params[0]
    inputs = _generate_boundary_inputs(schema)
    inputs.pop(path_param, None)

    probe_csv = "\n".join(
        [
            "id,feature_a,feature_b,feature_c,feature_d",
            "1,10.0,20.0,,40.0",
            "2,11.0,,31.0,41.0",
            "3,,22.0,32.0,42.0",
            "4,13.0,23.0,33.0,",
            "5,14.0,24.0,34.0,44.0",
            "6,15.0,25.0,35.0,45.0",
        ]
    )
    code = _build_sandbox_code_with_tempfiles(
        source_code,
        func_name,
        inputs,
        {"probe.csv": probe_csv},
        {path_param: "probe.csv"},
    )
    result = _sandbox(code, timeout=15)
    parsed = _parse_sandbox_output(result)

    if parsed is None:
        return [
            VerificationFinding(
                tier=2,
                category="tabular_identifier_column",
                severity=Severity.HIGH,
                message=(
                    "KNN CSV probe produced no parseable output on a valid tabular fixture "
                    "with an integer 'id' column"
                ),
                location="tabular_identifier_column",
            )
        ]

    if parsed.get("error") is not None:
        return [
            VerificationFinding(
                tier=2,
                category="tabular_identifier_column",
                severity=Severity.HIGH,
                message=(
                    "KNN CSV probe crashed on a valid tabular fixture with an integer 'id' "
                    f"column: {parsed.get('error')}"
                ),
                location="tabular_identifier_column",
            )
        ]

    return []


def _test_output_variation(
    source_code: str, func_name: str, schema: dict[str, dict], _sandbox=sandbox_execute
) -> list[VerificationFinding]:
    """Check that different inputs produce different outputs.

    Tools that ignore parameters and return canned data will produce identical
    outputs regardless of inputs.
    """
    findings: list[VerificationFinding] = []
    if not schema:
        return findings

    inputs_a = _generate_boundary_inputs(schema)
    inputs_b = _generate_large_inputs(schema)

    # Only meaningful if the two input sets are actually different.
    if inputs_a == inputs_b:
        return findings

    code_a = _build_sandbox_code(source_code, func_name, inputs_a)
    code_b = _build_sandbox_code(source_code, func_name, inputs_b)

    result_a = _sandbox(code_a, timeout=15)
    result_b = _sandbox(code_b, timeout=15)

    parsed_a = _parse_sandbox_output(result_a)
    parsed_b = _parse_sandbox_output(result_b)

    if parsed_a is not None and parsed_b is not None:
        err_a = parsed_a.get("error")
        err_b = parsed_b.get("error")

        # Flag when one input succeeds and the other crashes
        if (err_a is None) != (err_b is None):
            findings.append(
                VerificationFinding(
                    tier=2,
                    category="output_variation",
                    severity=Severity.MEDIUM,
                    message=(
                        "Tool succeeds for one input but crashes for another — "
                        "may not handle diverse inputs robustly"
                    ),
                    location="output_variation",
                )
            )
        elif (
            err_a is None
            and err_b is None
            and _normalize_for_variation(parsed_a.get("result"))
            == _normalize_for_variation(parsed_b.get("result"))
            and parsed_a.get("result") not in _TRIVIAL_RESULTS
        ):
            # Compare normalized outputs so a canned response with a changing
            # timestamp, UUID, or counter does not bypass the probe.
            findings.append(
                VerificationFinding(
                    tier=2,
                    category="output_variation",
                    severity=Severity.MEDIUM,
                    message=(
                        f"Tool returned near-identical output for very different "
                        f"inputs (timestamps/UUIDs normalized): "
                        f"{parsed_a.get('result')!r} — may be ignoring parameters"
                    ),
                    location="output_variation",
                )
            )

    return findings


# Strips embedded timestamps, UUIDs, and counter-like fields before equality
# comparison in the output-variation probe.
_NORMALIZATION_PATTERNS = [
    # ISO-8601 timestamps with optional fractional seconds and timezone.
    (
        re.compile(
            r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?"
            r"(?:Z|[+-]\d{2}:?\d{2})?"
        ),
        "<TS>",
    ),
    # Epoch timestamps (10 or 13 digits standing alone or in quotes).
    (re.compile(r"\b1[67]\d{8}(?:\d{3})?\b"), "<EPOCH>"),
    # UUIDs.
    (
        re.compile(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}",
            re.IGNORECASE,
        ),
        "<UUID>",
    ),
    # Counter/id/nonce fields with numeric values.
    (
        re.compile(
            r"(?P<key>(?:count|counter|id|nonce|request_id|trace_id)"
            r"\"?'?)\s*[:=]\s*\d+",
            re.IGNORECASE,
        ),
        r"\g<key>: <N>",
    ),
]


def _normalize_for_variation(result: Any) -> str:
    """Normalize a probe result string by stripping timestamps/UUIDs/counters.

    Accepts any type, stringifies it, and applies the normalization patterns.
    Identical output modulo the masked fields compares equal.
    """
    s = result if isinstance(result, str) else repr(result)
    for pattern, replacement in _NORMALIZATION_PATTERNS:
        s = pattern.sub(replacement, s)
    return s


def _test_nonexistent_entity(
    source_code: str, func_name: str, schema: dict[str, dict], _sandbox=sandbox_execute
) -> list[VerificationFinding]:
    """Test with empty / nonexistent inputs. The tool should not crash silently."""
    findings: list[VerificationFinding] = []
    if not schema:
        return findings

    # Empty string for all params
    empty_inputs = {name: "" if info.get("type") != "int" else 0 for name, info in schema.items()}
    code = _build_sandbox_code(source_code, func_name, empty_inputs)
    result = _sandbox(code, timeout=15)

    parsed = _parse_sandbox_output(result)
    if parsed is None and not result.get("success"):
        # The tool crashed outright — acceptable for invalid input.
        pass
    elif parsed is None and result.get("success"):
        # Sandbox returned success (exit 0) but output is unparseable — suspicious.
        findings.append(
            VerificationFinding(
                tier=2,
                category="nonexistent_entity",
                severity=Severity.MEDIUM,
                message=(
                    "Sandbox succeeded but produced unparseable output for "
                    "nonexistent entity inputs — tool may be silently failing"
                ),
                location="nonexistent_entity",
            )
        )
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
                location="nonexistent_entity",
            )
        )

    return findings


def _test_consistency(
    source_code: str, func_name: str, schema: dict[str, dict], _sandbox=sandbox_execute
) -> list[VerificationFinding]:
    """Run the same input twice and check results match."""
    findings: list[VerificationFinding] = []
    if not schema:
        return findings

    inputs = _generate_boundary_inputs(schema)
    code = _build_sandbox_code(source_code, func_name, inputs)

    result1 = _sandbox(code, timeout=15)
    result2 = _sandbox(code, timeout=15)

    parsed1 = _parse_sandbox_output(result1)
    parsed2 = _parse_sandbox_output(result2)

    if parsed1 is None or parsed2 is None:
        # At least one sandbox run timed out or failed to produce output
        findings.append(
            VerificationFinding(
                tier=2,
                category="consistency",
                severity=Severity.INFO,
                message="Consistency test inconclusive: sandbox timed out or produced no output",
            )
        )
    else:
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
                    location="consistency",
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
                    location="consistency",
                )
            )

    return findings


def _test_boundary(
    source_code: str, func_name: str, schema: dict[str, dict], _sandbox=sandbox_execute
) -> list[VerificationFinding]:
    """Test boundary values: zero, negative, large."""
    findings: list[VerificationFinding] = []
    if not schema:
        return findings

    # Run each negative numeric variant as its own probe so findings can
    # identify the specific edge case that failed.
    test_cases = [
        ("zero/empty", _generate_boundary_inputs(schema)),
    ]
    for variant_label, variant_inputs in _generate_negative_input_variants(schema):
        test_cases.append((f"negative/{variant_label}", variant_inputs))
    test_cases.append(("large", _generate_large_inputs(schema)))

    for label, inputs in test_cases:
        code = _build_sandbox_code(source_code, func_name, inputs)
        result = _sandbox(code, timeout=15)

        if not result.get("success"):
            stderr = result.get("stderr", "")
            if "Timeout" in stderr or "timed out" in stderr.lower() or "killed" in stderr.lower():
                findings.append(
                    VerificationFinding(
                        tier=2,
                        category="boundary",
                        severity=Severity.HIGH,
                        message=f"Tool timed out on {label} boundary inputs: {inputs}",
                        location=f"boundary/{label}",
                    )
                )
            else:
                # Distinguish dependency install failures (tool is broken)
                # from code crashes on bad input (expected for boundaries).
                is_dep_failure = (
                    "pip install failed" in stderr
                    or "No solution found" in stderr
                    or "No matching distribution" in stderr
                )
                findings.append(
                    VerificationFinding(
                        tier=2,
                        category="dependency" if is_dep_failure else "boundary",
                        severity=Severity.HIGH if is_dep_failure else Severity.MEDIUM,
                        message=(
                            f"Dependency install failed: {stderr[:300] or '(no stderr)'}"
                            if is_dep_failure
                            else (
                                f"Tool crashed on {label} boundary inputs: "
                                f"{stderr[:200] or '(no stderr)'}"
                            )
                        ),
                        location="pip_packages" if is_dep_failure else f"boundary/{label}",
                    )
                )

    return findings


def _test_timeout(
    source_code: str, func_name: str, schema: dict[str, dict], _sandbox=sandbox_execute
) -> list[VerificationFinding]:
    """Check that execution completes within a reasonable timeout (5s)."""
    findings: list[VerificationFinding] = []
    if not schema:
        return findings

    inputs = _generate_boundary_inputs(schema)
    code = _build_sandbox_code(source_code, func_name, inputs)

    start = time.monotonic()
    result = _sandbox(code, timeout=15)
    wall_time = time.monotonic() - start

    parsed = _parse_sandbox_output(result)
    # Use the tool-reported elapsed if available, otherwise wall time
    elapsed = parsed.get("elapsed", wall_time) if parsed else wall_time

    if elapsed > TIMEOUT_THRESHOLD_S:
        findings.append(
            VerificationFinding(
                tier=2,
                category="timeout",
                severity=Severity.MEDIUM,
                message=(
                    f"Tool execution took {elapsed:.2f}s "
                    f"(timeout threshold: {TIMEOUT_THRESHOLD_S}s)"
                ),
                location="timeout",
            )
        )

    return findings


def _test_latency(
    source_code: str, func_name: str, schema: dict[str, dict], _sandbox=sandbox_execute
) -> list[VerificationFinding]:
    """Detect mock-smell timing: very fast AND very uniform across varied inputs.

    Real computation has timing that varies with input. Constant lookup tables /
    hash tricks have near-zero variance and very low mean. We flag only when
    BOTH conditions hold to keep false positives near zero.
    """
    findings: list[VerificationFinding] = []
    if not schema:
        return findings

    # Collect 3 distinct input variants. Dedup by repr (dicts aren't hashable
    # so we can't use a set directly).
    candidates = [
        _generate_boundary_inputs(schema),
        _generate_negative_inputs(schema),
        _generate_large_inputs(schema),
    ]
    seen: set[str] = set()
    distinct_inputs: list[dict] = []
    for c in candidates:
        key = repr(sorted(c.items()))
        if key not in seen:
            seen.add(key)
            distinct_inputs.append(c)

    if len(distinct_inputs) < 2:
        return findings  # Cannot compute variance with < 2 samples

    elapsed_samples: list[float] = []
    for inputs in distinct_inputs:
        code = _build_sandbox_code(source_code, func_name, inputs)
        result = _sandbox(code, timeout=15)
        parsed = _parse_sandbox_output(result)
        if parsed is None or parsed.get("error") is not None:
            continue
        elapsed = parsed.get("elapsed")
        if isinstance(elapsed, (int, float)):
            elapsed_samples.append(float(elapsed))

    if len(elapsed_samples) < 2:
        return findings  # Not enough successful runs to compute variance

    mean = statistics.mean(elapsed_samples)
    stdev = statistics.stdev(elapsed_samples)
    cv = (stdev / mean) if mean > 0 else 0.0

    # For sub-millisecond means, natural Python GC/allocation jitter across a
    # small sample can inflate coefficient of variation. Use an absolute
    # stdev guard so very fast and very uniform outputs still get flagged.
    absolute_stdev_threshold = max(2e-4, 0.5 * mean)  # 200µs or half the mean
    if mean < 0.001 and stdev < absolute_stdev_threshold:
        findings.append(
            VerificationFinding(
                tier=2,
                category="latency",
                severity=Severity.MEDIUM,
                message=(
                    f"Tool execution is suspiciously fast and uniform: "
                    f"mean={mean*1000:.3f}ms, cv={cv:.3f} across {len(elapsed_samples)} inputs "
                    "— may be returning a constant lookup instead of computing"
                ),
                location="latency",
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run(
    artifact: GeneratedArtifact,
    *,
    allow_network: bool = False,
) -> list[VerificationFinding]:
    """Run behavioral probes on an MCP_TOOL artifact.

    Parameterized tools run nonexistent-entity, consistency, boundary,
    KNN CSV, timeout, latency-variance, and output-variation probes.
    Parameterless tools use a smoke probe. SKILL/AGENT artifacts have no
    ``source_code`` in this tier and are skipped with an INFO finding.

    When ``allow_network=True``, sandbox execution runs with
    ``--network bridge`` so network-dependent artifacts can be tested.
    If ``artifact.needs_network=True`` as well, a differential disconnect
    probe invokes the tool once online and once offline to detect silent
    fallback behavior.

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
                message=(
                    "No public async function found in source code — "
                    "cannot run behavioral tests"
                ),
            )
        ]

    # Prefer generator-declared pip_packages (accurate PyPI names).
    # Fall back to LLM-based import resolution for old artifacts.
    declared_pkgs = artifact.pip_packages if artifact.pip_packages else None
    extra_pkgs = declared_pkgs or resolve_extra_packages(source_code) or None

    # When dependencies are required, build one cached sandbox image and reuse
    # it across every probe. Dependency-free artifacts use the bare sandbox
    # path, which also keeps unit-test monkeypatches straightforward.
    from src.verification.sandbox_tool import (
        cleanup_cached_image,
        prepare_cached_image,
        sandbox_execute_with_cached_image,
    )

    cached_image_tag: str | None = None
    if extra_pkgs:
        cached_image_tag, build_err = prepare_cached_image(extra_pkgs)
        if build_err is not None:
            stderr = build_err.get("stderr", "")
            return [
                VerificationFinding(
                    tier=2,
                    category="dependency",
                    severity=Severity.HIGH,
                    message=f"Cannot install declared dependencies: {stderr[:300]}",
                    location="pip_packages",
                )
            ]

    try:
        if cached_image_tag is not None:
            _sandbox = partial(
                sandbox_execute_with_cached_image,
                image_tag=cached_image_tag,
                allow_network=allow_network,
            )
        else:
            # No declared dependencies — bare base image, no cached
            # build needed. Patches to behavioral.sandbox_execute
            # (used by unit tests) intercept this path normally.
            _sandbox = partial(
                sandbox_execute,
                extra_packages=None,
                allow_network=allow_network,
            )

        schema = extract_parameter_schema(source_code)
        if not schema:
            return _test_parameterless_smoke(source_code, func_name, _sandbox)

        findings: list[VerificationFinding] = []
        findings.extend(_test_nonexistent_entity(source_code, func_name, schema, _sandbox))
        findings.extend(_test_consistency(source_code, func_name, schema, _sandbox))
        findings.extend(_test_boundary(source_code, func_name, schema, _sandbox))
        findings.extend(_test_knn_identifier_column_csv(source_code, func_name, schema, _sandbox))
        findings.extend(_test_timeout(source_code, func_name, schema, _sandbox))
        findings.extend(_test_latency(source_code, func_name, schema, _sandbox))
        findings.extend(_test_output_variation(source_code, func_name, schema, _sandbox))

        # Only run the network differential when the artifact declares a
        # network dependency and the enclosing task policy allows network.
        if allow_network and getattr(artifact, "needs_network", False):
            findings.extend(
                _test_network_disconnect_differential(
                    source_code, func_name, schema, extra_pkgs,
                    cached_image_tag=cached_image_tag,
                )
            )

        return findings
    finally:
        # Cleanup the cached image so we don't accumulate
        # ``jit-sandbox-cached-*`` images across the eval campaign.
        # No-op when no cached image was built (zero-deps path).
        if cached_image_tag is not None:
            cleanup_cached_image(cached_image_tag)


def _test_network_disconnect_differential(
    source_code: str,
    func_name: str,
    schema: dict[str, dict],
    extra_pkgs: list[str] | None,
    *,
    cached_image_tag: str | None = None,
) -> list[VerificationFinding]:
    """Run the tool twice (network on, network off) and flag silent fallbacks.

    A tool that claims to fetch live data but actually returns hardcoded
    values can succeed under both network policies. A legitimate network tool
    should fail cleanly with a socket-level error when network is disabled.

    Flags:
      * CRITICAL — offline run returned plausible-looking output (dict, no
        error marker). Strong contamination signal.
      * MEDIUM — both runs failed (can't distinguish: might be broken tool
        rather than contaminated).
      * INFO — offline run failed as expected and online run succeeded
        (legitimate network tool).

    """
    # Use boundary inputs as the canonical "normal" invocation.
    inputs = _generate_boundary_inputs(schema)
    code = _build_sandbox_code(source_code, func_name, inputs)

    # Reuse the cached image when available so the differential only changes
    # Docker's network flag, not the package-install environment.
    if cached_image_tag is not None:
        from src.verification.sandbox_tool import sandbox_execute_with_cached_image
        on_result = sandbox_execute_with_cached_image(
            code, image_tag=cached_image_tag, timeout=30, allow_network=True,
        )
        off_result = sandbox_execute_with_cached_image(
            code, image_tag=cached_image_tag, timeout=30, allow_network=False,
        )
    else:
        on_result = sandbox_execute(
            code, timeout=30, extra_packages=extra_pkgs, allow_network=True,
        )
        off_result = sandbox_execute(
            code, timeout=30, extra_packages=extra_pkgs, allow_network=False,
        )

    def _parse(payload: dict) -> tuple[bool, dict | str]:
        """Return (produced_plausible_dict, parsed_value).

        The sandbox wrapper emits ``"error": None`` on success and a string
        error on failure, so this checks the value rather than key presence.
        """
        if not payload.get("success"):
            return False, payload.get("stderr", "")[:300]
        stdout = (payload.get("stdout") or "").strip()
        if not stdout:
            return False, ""
        try:
            last_line = stdout.splitlines()[-1]
            parsed = json.loads(last_line)
        except (json.JSONDecodeError, IndexError):
            return False, stdout[:300]
        if not isinstance(parsed, dict):
            return False, parsed
        err = parsed.get("error")
        if err is not None:
            return False, err
        return True, parsed.get("result", parsed)

    on_ok, on_value = _parse(on_result)
    off_ok, off_value = _parse(off_result)

    if off_ok:
        # Offline produced a plausible dict — strong silent-fallback signal.
        return [
            VerificationFinding(
                tier=2,
                category="network_silent_fallback",
                severity=Severity.CRITICAL,
                message=(
                    "Tool declared needs_network=True but returned plausible "
                    "output with network disabled. Likely hardcoded data or "
                    "silent fallback."
                ),
                location="network_disconnect_differential",
            )
        ]

    if on_ok and not off_ok:
        # Legitimate network tool.
        return [
            VerificationFinding(
                tier=2,
                category="network_disconnect_differential",
                severity=Severity.INFO,
                message=(
                    "Network-disconnect differential passed: tool succeeds "
                    "online and fails cleanly offline."
                ),
            )
        ]

    if not on_ok and not off_ok:
        # Both failed — can't classify.
        return [
            VerificationFinding(
                tier=2,
                category="network_disconnect_differential",
                severity=Severity.MEDIUM,
                message=(
                    "Tool failed under both network policies; cannot verify "
                    "network-disconnect semantics."
                ),
            )
        ]

    # on_ok=False, off_ok=True already handled above — shouldn't reach here.
    return []
