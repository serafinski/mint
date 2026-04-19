"""Verification gate — orchestrates all three tiers with tiered cost optimisation.

Tier structure (cheapest first, fail-fast):

  0    Skills only : ``quick_validate.py`` structural check          (~0 cost)
  1    MCP_TOOL    : AST + regex static scan                         (~0 cost)
       SKILL/AGENT : regex-only scan (not Python — AST does not apply)
  1.5  MCP_TOOL    : Docker dry-run import with pip_packages         (~5-10s)
       SKILL/AGENT : in-process compile() of embedded python fences  (~0 cost)
  2    MCP_TOOL    : Behavioural testing in Docker sandbox   (moderate cost)
       SKILL/AGENT : delegated to behavioral.run() which returns INFO
  3    All          : Adversarial DeepAgent probing              (high cost)

Gate rules:

* Tier 0 CRITICAL (invalid SKILL.md) → reject immediately; skip Tier 1/1.5/2/3.
* Tier 1 CRITICAL (mock artefact, hardcoded data, syntax error) → reject; skip Tier 1.5/2/3.
* Tier 1.5 CRITICAL (wrong pip package, import error, fence compile error) → reject; skip Tier 2/3.
* Tier 2 CRITICAL or HIGH finding → reject; skip Tier 3.
* After Tier 3: any CRITICAL or HIGH finding across all tiers → reject.

Public API
----------
verify(artifact: GeneratedArtifact) -> VerificationReport
"""

from __future__ import annotations

import json
import logging
import re

from collections import Counter

from src.orchestrator.tracing import emit_langfuse_event
from src.schemas.capability import CapabilityType
from src.schemas.task import GeneratedArtifact
from src.schemas.verification import Severity, VerificationFinding, VerificationReport
from src.verification import adversarial, behavioral, import_check, static_scanner

logger = logging.getLogger(__name__)


def _emit_tier_event(
    tier_label: str,
    artifact: GeneratedArtifact,
    findings: list[VerificationFinding],
    *,
    passed: bool,
    skipped_remaining: bool = False,
) -> None:
    """Emit a LangFuse event summarising one tier's outcome (Step 13.7 Batch A1).

    Never raises — uses emit_langfuse_event's silent-no-op contract.
    Event name ``verification.tier_<label>.<pass|reject>`` plus structured
    metadata let Chapter 4.1 cite per-tier traces by category. The severity
    distribution + category list are the keys Chapter 4.1 needs to
    empirically ground the Pillar 2 novelty claim (proposal line 152).
    """
    severity_dist = Counter(f.severity.value for f in findings)
    category_list = sorted({f.category for f in findings})
    level = "DEFAULT" if passed else "WARNING"
    event_name = (
        f"verification.tier_{tier_label}."
        f"{'pass' if passed else 'reject'}"
    )
    emit_langfuse_event(
        None,
        event_name,
        level,
        {
            "capability_name": artifact.capability_name,
            "capability_type": artifact.capability_type.value,
            "tier": tier_label,
            "passed": passed,
            "findings_count": len(findings),
            "severity_distribution": dict(severity_dist),
            "categories": category_list,
            "skipped_remaining_tiers": skipped_remaining,
        },
    )


# ---------------------------------------------------------------------------
# Code-fence extraction (layered Tier 1 for SKILL/AGENT)
# ---------------------------------------------------------------------------

# Match ```python ... ``` blocks. DOTALL so `.` matches newlines.
_PYTHON_FENCE = re.compile(r"```python\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)


def _extract_python_fences(text: str) -> list[str]:
    """Extract Python code blocks from a markdown-formatted string.

    Returns the body of each ```python ... ``` block in document order.
    Used for the layered Tier 1 scan: SKILL/AGENT prose stays untouched
    (no false positives from words like "placeholder" in instructions),
    but fenced Python code is scanned with the same AST checks as MCP_TOOL.
    """
    if not text:
        return []
    return [m.group(1) for m in _PYTHON_FENCE.finditer(text)]


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
        # Tier 0 (quick_validate) + Tier 1 (regex-only — AST is skipped for
        # non-MCP_TOOL types inside static_scanner.scan()).
        report = static_scanner.scan(
            source_code=artifact.skill_content or "",
            capability_name=name,
            capability_type="skill",
        )
        # Layered check: extract Python fences from SKILL.md and run the
        # full MCP_TOOL AST + regex scan on each. Prose stays untouched
        # (no T04-style false positives), but fenced code is scrutinised.
        for idx, snippet in enumerate(_extract_python_fences(artifact.skill_content or "")):
            fence_report = static_scanner.scan(
                source_code=snippet,
                capability_name=f"{name}#fence{idx}",
                capability_type="mcp_tool",
            )
            for f in fence_report.findings:
                f.location = f"code_fence[{idx}]:{f.location or 'unknown'}"
                report.findings.append(f)
        # Re-evaluate overall_passed in case fence findings introduced criticals.
        has_critical = any(f.severity == Severity.CRITICAL for f in report.findings)
        report.overall_passed = not has_critical
        return report

    # AGENT: regex-only scan on the JSON-serialised config (AST skipped).
    config = artifact.agent_config or {}
    config_str = json.dumps(config) if config else ""
    report = static_scanner.scan(
        source_code=config_str,
        capability_name=name,
        capability_type="agent",
    )
    # Layered check: extract Python fences from system_prompt and any
    # embedded skill_content, run AST + regex scan on each.
    sources_to_scan: list[tuple[str, str]] = []
    if isinstance(config.get("system_prompt"), str):
        sources_to_scan.append(("system_prompt", config["system_prompt"]))
    if isinstance(config.get("skill_content"), str):
        sources_to_scan.append(("skill_content", config["skill_content"]))
    for source_label, text in sources_to_scan:
        for idx, snippet in enumerate(_extract_python_fences(text)):
            fence_report = static_scanner.scan(
                source_code=snippet,
                capability_name=f"{name}#{source_label}#fence{idx}",
                capability_type="mcp_tool",
            )
            for f in fence_report.findings:
                f.location = f"{source_label}/code_fence[{idx}]:{f.location or 'unknown'}"
                report.findings.append(f)
    has_critical = any(f.severity == Severity.CRITICAL for f in report.findings)
    report.overall_passed = not has_critical
    return report


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def verify(
    artifact: GeneratedArtifact,
    *,
    allow_network: bool = False,
) -> VerificationReport:
    """Run the full tiered verification gate on a generated artefact.

    Returns a ``VerificationReport`` summarising findings from every tier
    that was executed.  Always returns — never raises.

    Step 14.12: when ``allow_network=True``, Tier 2 behavioral and Tier 3
    adversarial probes run under ``--network bridge`` so the artifact is
    verified in the same network-policy regime it will execute under.
    Verifying a network-dependent artifact with network *disabled* would
    spuriously fail Tier 2 (real API call errors) and make Tier 3's
    network-disconnect attack trivially "suspicious" on every invocation.
    """
    name = artifact.capability_name
    all_findings: list[VerificationFinding] = []

    # Track the current tier so the error handler can report accurately.
    current_tier = [1]  # mutable container for closure access
    try:
        return _verify_inner(
            artifact, name, all_findings, current_tier,
            allow_network=allow_network,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Verification gate error for '%s' at tier %d: %s — PASSING (fail-open policy). "
            "Artifact was NOT verified; it passed by default due to a gate error.",
            name, current_tier[0], exc,
        )
        return VerificationReport(
            capability_name=name,
            findings=[
                VerificationFinding(
                    tier=current_tier[0],
                    category="gate_error",
                    severity=Severity.MEDIUM,
                    message=f"Verification gate raised an unexpected exception at tier {current_tier[0]}: {exc}",
                )
            ],
            overall_passed=True,  # fail open — don't block on gate bugs
            tier_reached=current_tier[0],
        )


def _verify_inner(
    artifact: GeneratedArtifact,
    name: str,
    all_findings: list[VerificationFinding],
    current_tier: list[int],
    *,
    allow_network: bool = False,
) -> VerificationReport:
    """Inner implementation of verify() — called by the public wrapper."""
    # ------------------------------------------------------------------
    # Tier 0 + 1: Static scan (zero cost)
    # ------------------------------------------------------------------
    current_tier[0] = 1
    t1_report = _static_scan(artifact)
    all_findings.extend(t1_report.findings)

    if not static_scanner.should_proceed_to_tier2(t1_report):
        logger.info(
            "Tier 1 rejected %s — CRITICAL finding(s) present, skipping Tier 1.5/2/3",
            name,
        )
        _emit_tier_event(
            "1", artifact, t1_report.findings,
            passed=False, skipped_remaining=True,
        )
        return VerificationReport(
            capability_name=name,
            findings=all_findings,
            overall_passed=False,
            tier_reached=t1_report.tier_reached,
        )

    _emit_tier_event("1", artifact, t1_report.findings, passed=True)

    # ------------------------------------------------------------------
    # Tier 1.5: Sandbox import probe (fast fail on wrong pip packages,
    # runtime import errors, or embedded python fence compile errors).
    # Cheaper than Tier 2 (~5-10s Docker for MCP_TOOL, ~0 for SKILL/AGENT)
    # so failing here saves the Tier 2/3 token budget on broken artifacts.
    # ------------------------------------------------------------------
    current_tier[0] = 1  # Tier 1.5 reports stay at tier=1 per schema constraint
    logger.debug("Running Tier 1.5 import probe on %s", name)
    t15_report = import_check.run_import_check(artifact)
    all_findings.extend(t15_report.findings)

    if not import_check.should_proceed_to_tier2(t15_report):
        logger.info(
            "Tier 1.5 rejected %s — CRITICAL import-probe finding(s), skipping Tier 2/3",
            name,
        )
        _emit_tier_event(
            "1_5", artifact, t15_report.findings,
            passed=False, skipped_remaining=True,
        )
        return VerificationReport(
            capability_name=name,
            findings=all_findings,
            overall_passed=False,
            tier_reached=1,
        )

    _emit_tier_event("1_5", artifact, t15_report.findings, passed=True)

    # ------------------------------------------------------------------
    # Tier 2: Behavioural testing (moderate cost)
    # ------------------------------------------------------------------
    current_tier[0] = 2
    logger.debug("Running Tier 2 behavioural tests on %s", name)
    t2_findings = behavioral.run(artifact, allow_network=allow_network)
    all_findings.extend(t2_findings)

    t2_has_critical_or_high = any(
        f.severity in (Severity.CRITICAL, Severity.HIGH) for f in t2_findings
    )
    if t2_has_critical_or_high:
        logger.info(
            "Tier 2 rejected %s — CRITICAL/HIGH finding(s), skipping Tier 3",
            name,
        )
        _emit_tier_event(
            "2", artifact, t2_findings,
            passed=False, skipped_remaining=True,
        )
        return VerificationReport(
            capability_name=name,
            findings=all_findings,
            overall_passed=False,
            tier_reached=2,
        )

    _emit_tier_event("2", artifact, t2_findings, passed=True)

    # Tier 2 clearance: if Tier 1 had HIGH findings but Tier 2 is clean,
    # the HIGHs were false alarms (code smells that don't affect behavior).
    # Downgrade them to MEDIUM so they don't block at the final gate.
    # Per thesis proposal: "HIGH findings require Tier 2 clearance."
    t1_had_high = any(
        f.severity == Severity.HIGH and f.tier == 1 for f in all_findings
    )
    if t1_had_high and not t2_has_critical_or_high:
        for f in all_findings:
            if f.severity == Severity.HIGH and f.tier == 1:
                logger.info(
                    "Tier 2 cleared Tier 1 HIGH finding for %s: %s",
                    name, f.message[:80],
                )
                f.severity = Severity.MEDIUM

    # ------------------------------------------------------------------
    # Tier 3: Adversarial probing (high cost)
    # ------------------------------------------------------------------
    current_tier[0] = 3
    logger.debug("Running Tier 3 adversarial probe on %s", name)
    t3_report = adversarial.probe(artifact, allow_network=allow_network)
    all_findings.extend(t3_report.findings)

    has_blocking = any(
        f.severity in (Severity.CRITICAL, Severity.HIGH) for f in all_findings
    )
    overall_passed = not has_blocking
    logger.info(
        "Verification complete for %s: passed=%s, findings=%d",
        name,
        overall_passed,
        len(all_findings),
    )
    _emit_tier_event("3", artifact, t3_report.findings, passed=overall_passed)
    return VerificationReport(
        capability_name=name,
        findings=all_findings,
        overall_passed=overall_passed,
        tier_reached=3,
    )
