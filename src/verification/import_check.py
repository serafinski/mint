"""Tier 1.5 — Sandbox import probe (Docker dry-run + fence compile-check).

Sits between Tier 1 (static scan) and Tier 2 (behavioral testing) in the
verification gate. Catches three failure modes cheaply, before the gate
burns Tier 2/3 token budget:

1. **Wrong PyPI package name** (MCP_TOOL) — generator declares
   ``pip_packages=["yaml"]`` instead of ``"pyyaml"``. Tier 2 or Tier 3
   can also surface this, but only after paying higher runtime and token
   cost.
2. **Runtime import / NameError / SyntaxError in a code path the AST
   walker does not exercise** — a one-time ``python -c "import tool"``
   inside the sandbox catches this for free.
3. **Embedded Python fence compile errors** (SKILL / AGENT) — fences
   in ``skill_content`` / ``system_prompt`` that compile-fail.
   ``gate.py``'s fence extractor only does AST parse; ``compile()``
   catches different failures (unterminated strings, mismatched brackets
   that ``ast.parse`` masks, etc.).

Per-type scope
--------------
* **MCP_TOOL**: Docker container from ``SANDBOX_IMAGE``, install
  ``artifact.pip_packages``, write source to ``/tmp/tool.py``, run
  ``python -c "import tool"``. Non-zero exit → CRITICAL.
* **SKILL**: extract embedded ``python`` fences, ``compile()`` each
  in-process. No Docker cost. Compile errors → CRITICAL.
* **AGENT**: same fence-compile as SKILL for any fences in
  ``system_prompt`` / embedded ``skill_content``, plus a structural
  check on required ``agent_config`` keys. No Docker cost.

Contract
--------
``run_import_check(artifact) -> VerificationReport`` — never raises.
Any internal failure is caught and reported as MEDIUM ``import_check_error``
with ``overall_passed=True``. This fail-open path is specific to Tier 1.5
tooling failures: import-check is a cheap acceleration before Tier 2/3, so
Docker outages or internal probe bugs should not block the rest of the gate.
"""

from __future__ import annotations

import logging
import re

from src.schemas.capability import CapabilityType
from src.schemas.task import GeneratedArtifact
from src.schemas.verification import Severity, VerificationFinding, VerificationReport

logger = logging.getLogger(__name__)


# Reuse the gate's fence regex (same syntax — match ```python ... ``` blocks).
_PYTHON_FENCE = re.compile(r"```python\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)


def _extract_python_fences(text: str) -> list[str]:
    """Return the body of each ```python ... ``` block in *text*."""
    if not text:
        return []
    return [m.group(1) for m in _PYTHON_FENCE.finditer(text)]


# ---------------------------------------------------------------------------
# MCP_TOOL — Docker dry-run import
# ---------------------------------------------------------------------------


def _check_mcp_tool(
    artifact: GeneratedArtifact, *, allow_network: bool = False
) -> list[VerificationFinding]:
    """Docker-import the generated MCP tool with its declared pip packages.

    Writes the source to ``/tmp/tool.py`` inside the container and runs
    ``import tool``. Matches ``tool_loader.py``'s runtime mechanism, so
    any import failure here will also fire at runtime.

    When ``allow_network=True``, the probe sandbox runs with
    ``--network bridge`` so import-time network calls do not spuriously
    fail Tier 1.5 under a task policy that permits network access. Default
    False preserves the offline regime.
    """
    import base64

    source = artifact.source_code or ""
    if not source.strip():
        return [
            VerificationFinding(
                tier=1,
                category="import_check_skipped",
                severity=Severity.INFO,
                message="MCP_TOOL has empty source_code — import probe skipped.",
            )
        ]

    from src.verification.sandbox_tool import sandbox_execute

    src_b64 = base64.b64encode(source.encode()).decode()
    probe_code = (
        "import base64, pathlib, sys\n"
        f"pathlib.Path('/tmp/tool.py').write_text(base64.b64decode({src_b64!r}).decode())\n"
        "sys.path.insert(0, '/tmp')\n"
        "try:\n"
        "    import tool  # noqa: F401\n"
        "except BaseException as exc:\n"
        "    print(f'IMPORT_PROBE_ERROR:{type(exc).__name__}: {exc}', file=sys.stderr)\n"
        "    sys.exit(1)\n"
        "print('IMPORT_PROBE_OK')\n"
    )

    result = sandbox_execute(
        probe_code,
        timeout=30,
        extra_packages=artifact.pip_packages or None,
        allow_network=allow_network,
    )

    if result.get("success", False):
        return []

    # Malformed requirements are distinct from source import errors and
    # wrong PyPI names. Keep a separate category so retry feedback points
    # at the requirement string.
    rejected = result.get("pip_requirement_rejected")
    if rejected:
        return [
            VerificationFinding(
                tier=1,
                category="pip_requirement_rejected",
                severity=Severity.CRITICAL,
                message=(
                    "Generator declared pip requirements that failed PEP 508 "
                    f"parsing: {rejected!r}. Sandbox did not attempt install."
                ),
                location="import_check[mcp_tool]",
            )
        ]

    stderr = (result.get("stderr") or "").strip()
    # Distinguish "pip install failed" vs "import failed" so the retry
    # loop feedback can be specific. pip-install failures surface without
    # IMPORT_PROBE_ERROR; import-time failures include it.
    category = "wrong_pip_package" if "IMPORT_PROBE_ERROR" not in stderr else "import_error"
    truncated = stderr[-500:] if len(stderr) > 500 else stderr
    return [
        VerificationFinding(
            tier=1,
            category=category,
            severity=Severity.CRITICAL,
            message=(
                "Sandbox import probe failed — generated code does not import cleanly. "
                f"This will cause runtime failures. stderr: {truncated!r}"
            ),
            location="import_check[mcp_tool]",
        )
    ]


# ---------------------------------------------------------------------------
# SKILL / AGENT — in-process fence compile-check
# ---------------------------------------------------------------------------


def _compile_fences(
    text: str, label: str, source_label: str
) -> list[VerificationFinding]:
    """Extract python fences from *text* and compile each.

    Returns one CRITICAL finding per fence that fails to compile. Empty
    text / no fences → empty list (vacuous pass).
    """
    findings: list[VerificationFinding] = []
    for idx, snippet in enumerate(_extract_python_fences(text)):
        try:
            compile(snippet, f"<{label}#fence{idx}>", "exec")
        except SyntaxError as exc:
            findings.append(
                VerificationFinding(
                    tier=1,
                    category="fence_compile_error",
                    severity=Severity.CRITICAL,
                    message=(
                        f"Embedded Python fence in {source_label} "
                        f"(fence index {idx}) failed to compile: "
                        f"{type(exc).__name__}: {exc.msg} "
                        f"at line {exc.lineno}, offset {exc.offset}"
                    ),
                    location=f"{source_label}/code_fence[{idx}]",
                )
            )
    return findings


def _check_skill(artifact: GeneratedArtifact) -> list[VerificationFinding]:
    """Compile every Python fence in the skill content."""
    return _compile_fences(artifact.skill_content or "", artifact.capability_name, "skill_content")


def _check_agent(artifact: GeneratedArtifact) -> list[VerificationFinding]:
    """Compile embedded fences + validate required agent_config keys."""
    findings: list[VerificationFinding] = []
    config = artifact.agent_config or {}

    # Fence compile-checks in system_prompt + any embedded skill_content.
    if isinstance(config.get("system_prompt"), str):
        findings.extend(
            _compile_fences(
                config["system_prompt"],
                artifact.capability_name,
                "agent_config.system_prompt",
            )
        )
    if isinstance(config.get("skill_content"), str):
        findings.extend(
            _compile_fences(
                config["skill_content"],
                artifact.capability_name,
                "agent_config.skill_content",
            )
        )

    # Structural: agent_config must be a dict with at minimum ``system_prompt``.
    # Other keys (model, is_deep_agent, description) are generator-enforced but
    # their absence is not a hard failure at this tier — the downstream loader
    # has its own defaults.
    if not isinstance(artifact.agent_config, dict):
        findings.append(
            VerificationFinding(
                tier=1,
                category="import_check_structural",
                severity=Severity.CRITICAL,
                message="AGENT artifact has no agent_config dict.",
                location="agent_config",
            )
        )
    elif not isinstance(config.get("system_prompt"), str) or not config["system_prompt"].strip():
        findings.append(
            VerificationFinding(
                tier=1,
                category="import_check_structural",
                severity=Severity.CRITICAL,
                message="AGENT artifact agent_config missing a non-empty 'system_prompt'.",
                location="agent_config.system_prompt",
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_import_check(
    artifact: GeneratedArtifact, *, allow_network: bool = False
) -> VerificationReport:
    """Run the Tier 1.5 import probe for *artifact*.

    Returns a ``VerificationReport`` summarising findings. Never raises —
    any internal failure is caught and reported as MEDIUM
    ``import_check_error`` with ``overall_passed=True`` (fail-open).

    The gate's contract is that Tier 1.5 is an *acceleration* over
    Tier 2/3 (cheaper fail-fast on broken artifacts), not a
    replacement — so when the probe itself fails (Docker not available,
    internal bug), the gate proceeds to Tier 2 normally rather than
    blocking on a tooling failure.

    ``allow_network`` is forwarded to the MCP_TOOL Docker-import sandbox
    so the probe runs in the same network regime the artifact will execute
    under. SKILL / AGENT paths are in-process compile-checks, so
    ``allow_network`` has no effect on those branches.
    """
    name = artifact.capability_name
    try:
        if artifact.capability_type == CapabilityType.MCP_TOOL:
            findings = _check_mcp_tool(artifact, allow_network=allow_network)
        elif artifact.capability_type == CapabilityType.SKILL:
            findings = _check_skill(artifact)
        elif artifact.capability_type == CapabilityType.AGENT:
            findings = _check_agent(artifact)
        else:
            findings = []
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Import-check probe raised for %s: %s — PASSING (fail-open).",
            name, exc,
        )
        return VerificationReport(
            capability_name=name,
            findings=[
                VerificationFinding(
                    tier=1,
                    category="import_check_error",
                    severity=Severity.MEDIUM,
                    message=f"Tier 1.5 import probe raised unexpectedly: {exc}",
                )
            ],
            overall_passed=True,
            tier_reached=1,
        )

    has_critical = any(f.severity == Severity.CRITICAL for f in findings)
    return VerificationReport(
        capability_name=name,
        findings=findings,
        overall_passed=not has_critical,
        tier_reached=1,
    )


def should_proceed_to_tier2(report: VerificationReport) -> bool:
    """Return True if no CRITICAL findings — Tier 2 should run."""
    return not any(f.severity == Severity.CRITICAL for f in report.findings)
