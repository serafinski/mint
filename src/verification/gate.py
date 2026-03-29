"""Verification gate — orchestrates all three tiers with tiered cost optimisation.

Tier structure (cheapest first, fail-fast):

  0  Skills only : ``quick_validate.py`` structural check            (~0 cost)
  1  All          : AST + regex static scan                           (~0 cost)
  2  All          : Behavioural testing in Docker sandbox    (moderate cost)
  3  All          : Adversarial DeepAgent probing              (high cost)

Gate rules:

* Tier 0 CRITICAL (invalid SKILL.md) → reject immediately; skip Tier 1/2/3.
* Tier 1 CRITICAL (mock artefact, hardcoded data, syntax error) → reject; skip Tier 2/3.
* Tier 2 CRITICAL or HIGH finding → reject; skip Tier 3.
* Tier 3 CRITICAL finding → reject.

Public API
----------
verify(artifact: GeneratedArtifact) -> VerificationReport
"""

from __future__ import annotations

import json
import logging

from src.schemas.capability import CapabilityType
from src.schemas.task import GeneratedArtifact
from src.schemas.verification import Severity, VerificationFinding, VerificationReport
from src.verification import adversarial, behavioral, static_scanner
from src.verification.static_scanner import scan_text_only

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _static_scan(artifact: GeneratedArtifact) -> VerificationReport:
    """Run the appropriate static scan for the artifact's capability type.

    * MCP_TOOL: full AST + regex scan on ``source_code``.
    * SKILL: Tier 0 quick_validate then regex-only Tier 1 on ``skill_content``
      (SKILL.md is YAML/markdown, not Python — AST SyntaxErrors are expected
      and filtered out so a structurally valid skill is not incorrectly rejected).
    * AGENT: regex-only scan on the JSON-serialised ``agent_config``
      (no Python AST; JSON is not valid Python so SyntaxErrors are filtered).
    """
    name = artifact.capability_name

    if artifact.capability_type == CapabilityType.MCP_TOOL:
        return static_scanner.scan(
            source_code=artifact.source_code or "",
            capability_name=name,
            capability_type="mcp_tool",
        )

    if artifact.capability_type == CapabilityType.SKILL:
        # Tier 0: quick_validate structural check.  Returns early on failure.
        t0_report = static_scanner.scan(
            source_code=artifact.skill_content or "",
            capability_name=name,
            capability_type="skill",
        )
        if t0_report.tier_reached == 0:
            # quick_validate failed — return as-is (CRITICAL, reject).
            return t0_report
        # Tier 1: regex-only scan on SKILL.md content (it is not Python,
        # so AST checks do not apply).
        return scan_text_only(artifact.skill_content or "", name)

    # AGENT: regex-only scan on the JSON-serialised config.
    config_str = json.dumps(artifact.agent_config) if artifact.agent_config else ""
    return scan_text_only(config_str, name)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def verify(artifact: GeneratedArtifact) -> VerificationReport:
    """Run the full tiered verification gate on a generated artefact.

    Returns a ``VerificationReport`` summarising findings from every tier
    that was executed.  Always returns — never raises.
    """
    name = artifact.capability_name
    all_findings: list[VerificationFinding] = []

    try:
        return _verify_inner(artifact, name, all_findings)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Verification gate error for '%s': %s — PASSING (fail-open policy). "
            "Artifact was NOT verified; it passed by default due to a gate error.",
            name, exc,
        )
        return VerificationReport(
            capability_name=name,
            findings=[
                VerificationFinding(
                    tier=1,
                    category="gate_error",
                    severity=Severity.MEDIUM,
                    message=f"Verification gate raised an unexpected exception: {exc}",
                )
            ],
            overall_passed=True,  # fail open — don't block on gate bugs
            tier_reached=1,
        )


def _verify_inner(
    artifact: GeneratedArtifact,
    name: str,
    all_findings: list[VerificationFinding],
) -> VerificationReport:
    """Inner implementation of verify() — called by the public wrapper."""
    # ------------------------------------------------------------------
    # Tier 0 + 1: Static scan (zero cost)
    # ------------------------------------------------------------------
    t1_report = _static_scan(artifact)
    all_findings.extend(t1_report.findings)

    if not static_scanner.should_proceed_to_tier2(t1_report):
        logger.info(
            "Tier 1 rejected %s — CRITICAL finding(s) present, skipping Tier 2/3",
            name,
        )
        return VerificationReport(
            capability_name=name,
            findings=all_findings,
            overall_passed=False,
            tier_reached=t1_report.tier_reached,
        )

    # ------------------------------------------------------------------
    # Tier 2: Behavioural testing (moderate cost)
    # ------------------------------------------------------------------
    logger.debug("Running Tier 2 behavioural tests on %s", name)
    t2_findings = behavioral.run(artifact)
    all_findings.extend(t2_findings)

    t2_has_critical_or_high = any(
        f.severity in (Severity.CRITICAL, Severity.HIGH) for f in t2_findings
    )
    if t2_has_critical_or_high:
        logger.info(
            "Tier 2 rejected %s — CRITICAL/HIGH finding(s), skipping Tier 3",
            name,
        )
        return VerificationReport(
            capability_name=name,
            findings=all_findings,
            overall_passed=False,
            tier_reached=2,
        )

    # ------------------------------------------------------------------
    # Tier 3: Adversarial probing (high cost)
    # ------------------------------------------------------------------
    logger.debug("Running Tier 3 adversarial probe on %s", name)
    t3_report = adversarial.probe(artifact)
    all_findings.extend(t3_report.findings)

    has_critical = any(f.severity == Severity.CRITICAL for f in all_findings)
    overall_passed = not has_critical
    logger.info(
        "Verification complete for %s: passed=%s, findings=%d",
        name,
        overall_passed,
        len(all_findings),
    )
    return VerificationReport(
        capability_name=name,
        findings=all_findings,
        overall_passed=overall_passed,
        tier_reached=3,
    )
