"""Tier 1 static scanner — pure Python, no LLM, no subprocess.

Uses ``ast`` and ``re`` to detect five contamination categories in generated
capability source code.  For skill-type capabilities, also runs
``tools/quick_validate.py`` as a Tier 0 structural pre-check.

Public API
----------
scan(source_code, capability_name, capability_type="mcp_tool") -> VerificationReport
should_proceed_to_tier2(report) -> bool
"""

from __future__ import annotations

import ast
import re
import tempfile
from pathlib import Path

from src.schemas.capability import CapabilityType
from src.schemas.verification import Severity, VerificationFinding, VerificationReport

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# "sample" intentionally excluded — false-positives on legitimate random.sample() calls.
_MOCK_NAMES = re.compile(
    r"\b(mock|fake|dummy|placeholder|DEMO|TEST_DATA)\b",
    re.IGNORECASE,
)

_SYNTHETIC_COMMENTS = re.compile(
    r"(#\s*(TODO|FIXME|placeholder|for testing only))",
    re.IGNORECASE,
)

# Imports that indicate real external calls
_EXTERNAL_IMPORT_PATTERNS = re.compile(
    r"\b(requests|httpx|urllib|aiohttp|subprocess|socket|grpc|xmlrpc|ftplib|smtplib)\b",
)

# Function names that imply external interaction
_EXTERNAL_FN_NAMES = re.compile(
    r"^(fetch|query|call|get|retrieve|search)",
    re.IGNORECASE,
)

# Lorem ipsum placeholder text — never legitimate in real code/data
_LOREM_IPSUM = re.compile(r"\blorem ipsum\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Tier 0: Skill structural pre-check via quick_validate.py
# ---------------------------------------------------------------------------

def _run_skill_precheck(
    source_code: str,
    capability_name: str,
) -> VerificationFinding | None:
    """Run ``tools/quick_validate.py`` logic on a skill's SKILL.md content.

    Returns a tier-0 CRITICAL finding if validation fails, else ``None``.
    """
    # Import the validate function directly to avoid subprocess
    from tools.quick_validate import validate_skill

    with tempfile.TemporaryDirectory() as tmp_dir:
        skill_dir = Path(tmp_dir) / capability_name
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(source_code)

        is_valid, message = validate_skill(str(skill_dir))

    if not is_valid:
        return VerificationFinding(
            tier=0,
            category="skill_structure",
            severity=Severity.CRITICAL,
            message=f"Skill structural pre-check failed: {message}",
            location=capability_name,
        )
    return None


# ---------------------------------------------------------------------------
# AST-based checks
# ---------------------------------------------------------------------------

def _check_hardcoded_return(node: ast.AST, findings: list[VerificationFinding]) -> None:
    """Detect literal lists/dicts of >=3 items returned from a function body.

    Also follows one level of variable indirection (B1.2): if the return value
    is a Name, resolves it to the first assignment in the function scope.
    """
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return

    # Collect variable assignments in this function scope (first assignment wins).
    assignments: dict[str, ast.expr] = {}
    for stmt in ast.walk(node):
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id not in assignments:
                    assignments[target.id] = stmt.value

    for child in ast.walk(node):
        if not isinstance(child, ast.Return) or child.value is None:
            continue
        val = child.value
        # Follow one level of variable indirection.
        if isinstance(val, ast.Name):
            val = assignments.get(val.id, val)
        if isinstance(val, ast.List) and len(val.elts) >= 3:
            if all(isinstance(e, ast.Constant) for e in val.elts):
                findings.append(
                    VerificationFinding(
                        tier=1,
                        category="hardcoded_data",
                        severity=Severity.CRITICAL,
                        message=f"Hardcoded list of {len(val.elts)} literal items returned at line {child.lineno}",
                        location=f"line {child.lineno}",
                    )
                )
        elif isinstance(val, ast.Dict) and len(val.keys) >= 3:
            if all(isinstance(k, ast.Constant) for k in val.keys if k is not None) and all(
                isinstance(v, ast.Constant) for v in val.values
            ):
                findings.append(
                    VerificationFinding(
                        tier=1,
                        category="hardcoded_data",
                        severity=Severity.CRITICAL,
                        message=f"Hardcoded dict of {len(val.keys)} literal items returned at line {child.lineno}",
                        location=f"line {child.lineno}",
                    )
                )


def _check_silent_except(node: ast.AST, findings: list[VerificationFinding]) -> None:
    """Detect bare ``except:`` or ``except Exception: pass`` with no re-raise/logging."""
    if not isinstance(node, ast.ExceptHandler):
        return

    # Bare except (type is None) or except Exception
    is_bare = node.type is None
    is_exception = (
        isinstance(node.type, ast.Name) and node.type.id == "Exception"
    )

    if not (is_bare or is_exception):
        return

    body = node.body
    # Check if the body is effectively silent: only `pass`, `return None`,
    # `return {}`, `return "..."`, or similar swallowed-exception patterns.
    has_reraise = False
    has_logging = False
    is_only_pass = len(body) == 1 and isinstance(body[0], ast.Pass)
    is_silent_return = False

    if len(body) == 1 and isinstance(body[0], ast.Return):
        ret_val = body[0].value
        # return None (explicit or implicit)
        if ret_val is None:
            is_silent_return = True
        elif isinstance(ret_val, ast.Constant) and ret_val.value is None:
            is_silent_return = True
        # return {}, return [], return "", return 0, return False, etc.
        elif isinstance(ret_val, ast.Constant):
            is_silent_return = True
        elif isinstance(ret_val, ast.Dict) and len(ret_val.keys) == 0:
            is_silent_return = True
        elif isinstance(ret_val, ast.List) and len(ret_val.elts) == 0:
            is_silent_return = True

    for child in ast.walk(node):
        if isinstance(child, ast.Raise):
            has_reraise = True
        if isinstance(child, ast.Call):
            func = child.func
            # Check for logging calls like logger.error, print, log.warning, etc.
            if isinstance(func, ast.Attribute) and func.attr in (
                "error", "warning", "info", "debug", "critical",
                "exception", "log", "warn",
            ):
                has_logging = True
            if isinstance(func, ast.Name) and func.id in ("print", "logging"):
                has_logging = True

    if (is_bare or is_only_pass or is_silent_return) and not has_reraise and not has_logging:
        if is_bare:
            label = "bare except"
        elif is_only_pass:
            label = "except Exception: pass"
        elif is_silent_return:
            label = "except Exception: return <default>"
        else:
            label = "silent except"
        findings.append(
            VerificationFinding(
                tier=1,
                category="silent_exception",
                severity=Severity.HIGH,
                message=f"Silent exception handling ({label}) at line {node.lineno}",
                location=f"line {node.lineno}",
            )
        )


def _check_missing_external_calls(
    tree: ast.Module,
    source_code: str,
    findings: list[VerificationFinding],
) -> None:
    """Flag tools that claim to fetch/query but contain no network imports."""
    # Collect all function names
    fn_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn_names.append(node.name)

    external_fn_found = any(_EXTERNAL_FN_NAMES.match(name) for name in fn_names)
    if not external_fn_found:
        return

    # Check if source contains any external import
    has_external_import = bool(_EXTERNAL_IMPORT_PATTERNS.search(source_code))
    if not has_external_import:
        findings.append(
            VerificationFinding(
                tier=1,
                category="missing_external_calls",
                severity=Severity.MEDIUM,
                message=(
                    "Function name implies external interaction "
                    f"({', '.join(n for n in fn_names if _EXTERNAL_FN_NAMES.match(n))}) "
                    "but no network/subprocess imports found"
                ),
            )
        )


# ---------------------------------------------------------------------------
# Regex-based checks
# ---------------------------------------------------------------------------

def _check_mock_artifacts(source_code: str, findings: list[VerificationFinding]) -> None:
    """Detect mock/demo variable names and string literals.

    Reports at most one CRITICAL finding per distinct indicator word to avoid
    flooding the report (a single match is sufficient to reject).
    """
    seen_indicators: set[str] = set()
    positions: list[str] = []
    for m in _MOCK_NAMES.finditer(source_code):
        word = m.group().lower()
        if word not in seen_indicators:
            seen_indicators.add(word)
            positions.append(f"'{m.group()}' at position {m.start()}")
    if seen_indicators:
        findings.append(
            VerificationFinding(
                tier=1,
                category="mock_artifact",
                severity=Severity.CRITICAL,
                message=f"Mock/demo indicator(s): {'; '.join(positions)}",
                location="multiple" if len(positions) > 1 else positions[0],
            )
        )


def _check_synthetic_comments(source_code: str, findings: list[VerificationFinding]) -> None:
    """Detect TODO/FIXME/placeholder comments and docstrings."""
    for m in _SYNTHETIC_COMMENTS.finditer(source_code):
        findings.append(
            VerificationFinding(
                tier=1,
                category="synthetic_data",
                severity=Severity.HIGH,
                message=f"Synthetic data comment: {m.group().strip()}",
                location=f"position {m.start()}",
            )
        )


def _check_lorem_ipsum(source_code: str, findings: list[VerificationFinding]) -> None:
    """Detect lorem ipsum placeholder text in string literals."""
    for m in _LOREM_IPSUM.finditer(source_code):
        findings.append(
            VerificationFinding(
                tier=1,
                category="synthetic_data",
                severity=Severity.HIGH,
                message=f"Lorem ipsum placeholder text found at position {m.start()}",
                location=f"position {m.start()}",
            )
        )


def _check_mock_imports(tree: ast.Module, findings: list[VerificationFinding]) -> None:
    """Detect imports of mocking libraries (unittest.mock, mock, faker)."""
    BANNED = {"unittest.mock", "mock", "faker"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                full = alias.name
                root = full.split(".")[0]
                if full in BANNED or root in BANNED:
                    findings.append(
                        VerificationFinding(
                            tier=1,
                            category="mock_import",
                            severity=Severity.CRITICAL,
                            message=f"Imports mocking library: {alias.name}",
                            location=f"line {node.lineno}",
                        )
                    )
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            root = mod.split(".")[0]
            if mod in BANNED or root in BANNED:
                findings.append(
                    VerificationFinding(
                        tier=1,
                        category="mock_import",
                        severity=Severity.CRITICAL,
                        message=f"Imports from mocking library: {mod}",
                        location=f"line {node.lineno}",
                    )
                )


def _check_random_seed(tree: ast.Module, findings: list[VerificationFinding]) -> None:
    """Detect ``random.seed(<literal>)`` — fake-determinism for stable mock outputs.

    Also catches ``np.random.seed(<literal>)`` and ``numpy.random.seed(<literal>)``.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_seed = False
        if isinstance(func, ast.Attribute) and func.attr == "seed":
            # random.seed(...) or np.random.seed(...)
            if isinstance(func.value, ast.Name) and func.value.id in ("random", "np"):
                is_seed = True
            # numpy.random.seed(...) — func.value is Attribute(attr='random', value=Name('numpy'))
            elif isinstance(func.value, ast.Attribute) and func.value.attr == "random":
                is_seed = True
        if is_seed and node.args and isinstance(node.args[0], ast.Constant):
            findings.append(
                VerificationFinding(
                    tier=1,
                    category="determinism_artifact",
                    severity=Severity.HIGH,
                    message=f"random.seed() called with literal {node.args[0].value!r} at line {node.lineno}",
                    location=f"line {node.lineno}",
                )
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan(
    source_code: str,
    capability_name: str,
    capability_type: str = "mcp_tool",
) -> VerificationReport:
    """Run Tier 0 (skills only) + Tier 1 static analysis on source code.

    Parameters
    ----------
    source_code:
        The raw source code (or SKILL.md content for skills).
    capability_name:
        Name of the capability being scanned.
    capability_type:
        One of ``CapabilityType`` values (``"mcp_tool"``, ``"skill"``, ``"agent"``).

    Returns
    -------
    VerificationReport with tier_reached=0 or 1.
    """
    findings: list[VerificationFinding] = []

    # --- Tier 0: Skill structural pre-check ---
    if capability_type == CapabilityType.SKILL:
        tier0_finding = _run_skill_precheck(source_code, capability_name)
        if tier0_finding is not None:
            findings.append(tier0_finding)
            return VerificationReport(
                capability_name=capability_name,
                findings=findings,
                overall_passed=False,
                tier_reached=0,
            )

    # --- Tier 1: AST + regex ---
    # AST checks only apply to MCP_TOOL (Python source).  SKILL (YAML/markdown)
    # and AGENT (JSON config) are not valid Python — skip AST entirely for them
    # to avoid spurious SyntaxError findings.
    if capability_type == CapabilityType.MCP_TOOL:
        try:
            tree = ast.parse(source_code)
        except SyntaxError as exc:
            findings.append(
                VerificationFinding(
                    tier=1,
                    category="syntax",
                    severity=Severity.CRITICAL,
                    message=f"SyntaxError: {exc.msg} at line {exc.lineno}",
                    location=f"line {exc.lineno}",
                )
            )
            return VerificationReport(
                capability_name=capability_name,
                findings=findings,
                overall_passed=False,
                tier_reached=1,
            )

        # AST checks
        for node in ast.walk(tree):
            _check_hardcoded_return(node, findings)
            _check_silent_except(node, findings)

        _check_missing_external_calls(tree, source_code, findings)
        _check_mock_imports(tree, findings)
        _check_random_seed(tree, findings)

        # Regex checks (MCP_TOOL only — skills/agents are natural-language
        # prose where words like "placeholder" or "mock" appear legitimately
        # in instructions and would produce false positives).
        _check_mock_artifacts(source_code, findings)
        _check_synthetic_comments(source_code, findings)
        _check_lorem_ipsum(source_code, findings)

    has_critical = any(f.severity == Severity.CRITICAL for f in findings)
    return VerificationReport(
        capability_name=capability_name,
        findings=findings,
        overall_passed=not has_critical,
        tier_reached=1,
    )



def should_proceed_to_tier2(report: VerificationReport) -> bool:
    """Return True if no Critical findings — Tier 2 should run."""
    return not any(f.severity == Severity.CRITICAL for f in report.findings)