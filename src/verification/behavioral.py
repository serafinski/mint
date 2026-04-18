"""Tier 2: Behavioral testing for generated capabilities.

Generates edge-case test inputs, executes them in the Docker sandbox,
and detects behavioral contamination. No LLM calls — pure Python.

Six test categories:
  - Nonexistent entity: empty / missing / invalid inputs
  - Consistency: same input twice must produce same output
  - Boundary: min/max values, zero, negative, empty strings
  - Timeout: execution must complete within a reasonable timeout (5s)
  - Latency (variance): mock-smell detection — very fast AND very uniform
  - Output variation: different inputs should produce different outputs
"""

import ast
import json
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

_TRIVIAL_RESULTS = {"None", "''", '""', "[]", "{}", "0", "False", "True"}


def _test_parameterless_smoke(source_code: str, func_name: str, _sandbox=sandbox_execute) -> list[VerificationFinding]:
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


def _test_output_variation(
    source_code: str, func_name: str, schema: dict[str, dict], _sandbox=sandbox_execute
) -> list[VerificationFinding]:
    """Check that different inputs produce different outputs.

    Tools that ignore parameters and return canned data will produce identical
    outputs regardless of inputs (B2.3 bypass).
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
            and parsed_a.get("result") == parsed_b.get("result")
            and parsed_a.get("result") not in _TRIVIAL_RESULTS
        ):
            findings.append(
                VerificationFinding(
                    tier=2,
                    category="output_variation",
                    severity=Severity.MEDIUM,
                    message=(
                        f"Tool returned identical output for very different inputs: "
                        f"{parsed_a.get('result')!r} — may be ignoring parameters"
                    ),
                    location="output_variation",
                )
            )

    return findings


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

    test_cases = [
        ("zero/empty", _generate_boundary_inputs(schema)),
        ("negative", _generate_negative_inputs(schema)),
        ("large", _generate_large_inputs(schema)),
    ]

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
                            else f"Tool crashed on {label} boundary inputs: {stderr[:200] or '(no stderr)'}"
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
                message=f"Tool execution took {elapsed:.2f}s (timeout threshold: {TIMEOUT_THRESHOLD_S}s)",
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
    BOTH conditions hold to keep false positives near zero (proposal: section 4).
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

    # Two-condition gate: very fast AND very uniform.
    if mean < 0.001 and cv < 0.10:
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
    """Run all six behavioral test categories on an MCP_TOOL artifact.

    Categories: nonexistent entity, consistency, boundary, timeout,
    latency (variance), and output variation.  SKILL/AGENT artifacts
    have no ``source_code`` and are skipped with an INFO finding.

    Step 14.12: when ``allow_network=True``, every sandbox execution inside
    this tier runs with ``--network bridge`` instead of ``--network none``
    so artifacts that legitimately require network access can be behavior-
    tested. Additionally, when ``artifact.needs_network=True``, a
    differential disconnect test fires: the tool is invoked twice (network
    on, network off) and flagged if the offline run returns a plausible
    dict rather than a clean error — a direct detector for the F2
    "silent fallback" failure mode.

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

    # Prefer generator-declared pip_packages (accurate PyPI names).
    # Fall back to LLM-based import resolution for old artifacts.
    declared_pkgs = artifact.pip_packages if artifact.pip_packages else None
    extra_pkgs = declared_pkgs or resolve_extra_packages(source_code) or None

    # Upfront dependency check: if the sandbox can't install the tool's
    # packages, all behavioral tests will fail with the same error.
    # Report once as HIGH and skip the rest.
    if extra_pkgs:
        dep_check = sandbox_execute(
            'print("deps_ok")', timeout=60, extra_packages=extra_pkgs,
            allow_network=allow_network,
        )
        if not dep_check.get("success"):
            stderr = dep_check.get("stderr", "")
            return [
                VerificationFinding(
                    tier=2,
                    category="dependency",
                    severity=Severity.HIGH,
                    message=f"Cannot install declared dependencies: {stderr[:300]}",
                    location="pip_packages",
                )
            ]

    _sandbox = partial(
        sandbox_execute, extra_packages=extra_pkgs, allow_network=allow_network,
    )

    schema = extract_parameter_schema(source_code)
    if not schema:
        # B2.1: run smoke test instead of silently skipping.
        return _test_parameterless_smoke(source_code, func_name, _sandbox)

    findings: list[VerificationFinding] = []
    findings.extend(_test_nonexistent_entity(source_code, func_name, schema, _sandbox))
    findings.extend(_test_consistency(source_code, func_name, schema, _sandbox))
    findings.extend(_test_boundary(source_code, func_name, schema, _sandbox))
    findings.extend(_test_timeout(source_code, func_name, schema, _sandbox))
    findings.extend(_test_latency(source_code, func_name, schema, _sandbox))
    findings.extend(_test_output_variation(source_code, func_name, schema, _sandbox))

    # Step 14.12: network-disconnect differential. Only fires when the
    # generator declared the capability legitimately needs network AND the
    # enclosing context allows network — otherwise the test is either
    # vacuous (offline tool doesn't need network) or invalid (network
    # should be blocked, so the on-comparison isn't meaningful).
    if allow_network and getattr(artifact, "needs_network", False):
        findings.extend(
            _test_network_disconnect_differential(
                source_code, func_name, schema, extra_pkgs,
            )
        )

    return findings


def _test_network_disconnect_differential(
    source_code: str,
    func_name: str,
    schema: dict[str, dict],
    extra_pkgs: list[str] | None,
) -> list[VerificationFinding]:
    """Run the tool twice (network on, network off) and flag silent fallbacks.

    Direct detector for proposal §Pillar 2 F2 (silent fallbacks). A tool that
    claims to fetch live data but actually returns hardcoded values will
    succeed identically under both network policies — that matches the
    contaminated pattern. A legitimate network tool fails cleanly with a
    socket-level error when network is disabled.

    Flags:
      * CRITICAL — offline run returned plausible-looking output (dict, no
        error marker). Strong contamination signal.
      * MEDIUM — both runs failed (can't distinguish: might be broken tool
        rather than contaminated).
      * INFO — offline run failed as expected and online run succeeded
        (legitimate network tool).

    Step 14.12.
    """
    # Use boundary inputs as the canonical "normal" invocation.
    inputs = _generate_boundary_inputs(schema)
    code = _build_sandbox_code(source_code, func_name, inputs)

    on_result = sandbox_execute(
        code, timeout=30, extra_packages=extra_pkgs, allow_network=True,
    )
    off_result = sandbox_execute(
        code, timeout=30, extra_packages=extra_pkgs, allow_network=False,
    )

    def _parse(payload: dict) -> tuple[bool, dict | str]:
        """Return (produced_plausible_dict, parsed_value)."""
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
        if "error" in parsed:
            return False, parsed["error"]
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
                    "silent fallback (proposal §Pillar 2 F2)."
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
