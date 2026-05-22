"""Tests for the tiered verification gate.

All external calls (behavioral.run, adversarial.probe, import_check.run_import_check)
are mocked so these tests run without Docker or API keys.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.schemas.capability import CapabilityType
from src.schemas.task import GeneratedArtifact
from src.schemas.verification import Severity, VerificationFinding, VerificationReport
from src.verification.gate import verify


@pytest.fixture(autouse=True)
def _mock_import_check():
    """Stub Tier 1.5 to a passing report by default — keeps these tests
    Docker-independent. Tier-1.5-specific behavior is covered in
    ``tests/unit/test_import_check.py``.
    """
    with patch(
        "src.verification.gate.import_check.run_import_check",
        return_value=VerificationReport(
            capability_name="stub",
            findings=[],
            overall_passed=True,
            tier_reached=1,
        ),
    ):
        yield

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CLEAN_MCP_CODE = """\
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from typing import Annotated

mcp = FastMCP("adder")

@mcp.tool()
async def add(
    a: Annotated[int, Field(description="First number")],
    b: Annotated[int, Field(description="Second number")],
) -> int:
    \"\"\"Add two integers.\"\"\"
    return a + b
"""

_MOCK_MCP_CODE = """\
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo")

@mcp.tool()
async def get_data() -> list:
    mock_result = [1, 2, 3]
    return mock_result
"""

_HARDCODED_MCP_CODE = """\
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("hardcoded")

@mcp.tool()
async def get_items() -> list:
    return ["alpha", "beta", "gamma", "delta"]
"""

_VALID_SKILL_MD = """\
---
name: test-skill
description: A test skill for verification testing purposes
---

## Overview

This skill does useful things.
"""

_INVALID_SKILL_MD = """\
---
name: this name is way too long and exceeds the sixty-four character limit set by the spec
description: A test skill
---
"""

_CLEAN_AGENT_CONFIG = {
    "system_prompt": "You are a helpful assistant.",
    "node_config": {"model": "openai:gpt-5-mini"},
}


def _passing_report(name: str, tier: int) -> VerificationReport:
    return VerificationReport(
        capability_name=name,
        findings=[],
        overall_passed=True,
        tier_reached=tier,
    )


def _failing_report(name: str, tier: int, severity: Severity = Severity.HIGH) -> VerificationReport:
    return VerificationReport(
        capability_name=name,
        findings=[
            VerificationFinding(
                tier=tier,
                category="test_failure",
                severity=severity,
                message="Injected test failure",
            )
        ],
        overall_passed=False,
        tier_reached=tier,
    )


def _make_artifact(
    name: str = "test-cap",
    cap_type: CapabilityType = CapabilityType.MCP_TOOL,
    source_code: str | None = _CLEAN_MCP_CODE,
    skill_content: str | None = None,
    agent_config: dict | None = None,
) -> GeneratedArtifact:
    return GeneratedArtifact(
        capability_name=name,
        capability_type=cap_type,
        source_code=source_code,
        skill_content=skill_content,
        agent_config=agent_config,
    )


# ---------------------------------------------------------------------------
# MCP_TOOL — clean code passes all tiers
# ---------------------------------------------------------------------------


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_mcp_clean_passes_all_tiers(mock_behavioral, mock_adversarial):
    artifact = _make_artifact(source_code=_CLEAN_MCP_CODE)
    mock_behavioral.return_value = []
    mock_adversarial.return_value = _passing_report("test-cap", tier=3)

    report = verify(artifact)

    assert report.overall_passed is True
    assert report.tier_reached == 3
    mock_behavioral.assert_called_once_with(artifact, allow_network=False)
    mock_adversarial.assert_called_once_with(artifact, allow_network=False)


# ---------------------------------------------------------------------------
# MCP_TOOL — Tier 1 CRITICAL blocks immediately (Tier 2/3 not called)
# ---------------------------------------------------------------------------


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_mcp_tier1_critical_blocks_tier23(mock_behavioral, mock_adversarial):
    # Mock artifact contains "mock" keyword → CRITICAL Tier 1 finding
    artifact = _make_artifact(source_code=_MOCK_MCP_CODE)

    report = verify(artifact)

    assert report.overall_passed is False
    assert report.tier_reached in (0, 1)
    mock_behavioral.assert_not_called()
    mock_adversarial.assert_not_called()


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_mcp_hardcoded_data_blocks_tier23(mock_behavioral, mock_adversarial):
    artifact = _make_artifact(source_code=_HARDCODED_MCP_CODE)

    report = verify(artifact)

    assert report.overall_passed is False
    mock_behavioral.assert_not_called()
    mock_adversarial.assert_not_called()


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_mcp_syntax_error_blocks_tier23(mock_behavioral, mock_adversarial):
    artifact = _make_artifact(source_code="def foo(: invalid syntax!!!")

    report = verify(artifact)

    assert report.overall_passed is False
    mock_behavioral.assert_not_called()
    mock_adversarial.assert_not_called()


# ---------------------------------------------------------------------------
# MCP_TOOL — Tier 2 CRITICAL/HIGH blocks Tier 3
# ---------------------------------------------------------------------------


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_mcp_tier2_high_blocks_tier3(mock_behavioral, mock_adversarial):
    artifact = _make_artifact(source_code=_CLEAN_MCP_CODE)
    mock_behavioral.return_value = [
        VerificationFinding(
            tier=2,
            category="consistency",
            severity=Severity.HIGH,
            message="Inconsistent results",
        )
    ]

    report = verify(artifact)

    assert report.overall_passed is False
    assert report.tier_reached == 2
    mock_adversarial.assert_not_called()


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_mcp_tier2_critical_blocks_tier3(mock_behavioral, mock_adversarial):
    artifact = _make_artifact(source_code=_CLEAN_MCP_CODE)
    mock_behavioral.return_value = [
        VerificationFinding(
            tier=2,
            category="test",
            severity=Severity.CRITICAL,
            message="Critical behavioral issue",
        )
    ]

    report = verify(artifact)

    assert report.overall_passed is False
    assert report.tier_reached == 2
    mock_adversarial.assert_not_called()


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_mcp_tier2_medium_proceeds_to_tier3(mock_behavioral, mock_adversarial):
    """MEDIUM Tier 2 findings should NOT block Tier 3."""
    artifact = _make_artifact(source_code=_CLEAN_MCP_CODE)
    mock_behavioral.return_value = [
        VerificationFinding(
            tier=2,
            category="latency",
            severity=Severity.MEDIUM,
            message="Slow execution",
        )
    ]
    mock_adversarial.return_value = _passing_report("test-cap", tier=3)

    report = verify(artifact)

    assert report.tier_reached == 3
    mock_adversarial.assert_called_once()


# ---------------------------------------------------------------------------
# MCP_TOOL — Tier 3 CRITICAL fails overall
# ---------------------------------------------------------------------------


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_mcp_tier3_critical_fails_overall(mock_behavioral, mock_adversarial):
    artifact = _make_artifact(source_code=_CLEAN_MCP_CODE)
    mock_behavioral.return_value = []
    mock_adversarial.return_value = VerificationReport(
        capability_name="test-cap",
        findings=[
            VerificationFinding(
                tier=3,
                category="hidden_demo_mode",
                severity=Severity.CRITICAL,
                message="Returns canned data for 'test' inputs",
            )
        ],
        overall_passed=False,
        tier_reached=3,
    )

    report = verify(artifact)

    assert report.overall_passed is False
    assert report.tier_reached == 3
    assert any(f.tier == 3 for f in report.findings)


# ---------------------------------------------------------------------------
# Findings accumulation across tiers
# ---------------------------------------------------------------------------


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_findings_accumulated_from_all_tiers(mock_behavioral, mock_adversarial):
    artifact = _make_artifact(source_code=_CLEAN_MCP_CODE)
    mock_behavioral.return_value = [
        VerificationFinding(
            tier=2, category="latency", severity=Severity.MEDIUM, message="Slow"
        )
    ]
    mock_adversarial.return_value = VerificationReport(
        capability_name="test-cap",
        findings=[
            VerificationFinding(
                tier=3, category="network_disconnect", severity=Severity.LOW, message="Fine"
            )
        ],
        overall_passed=True,
        tier_reached=3,
    )

    report = verify(artifact)

    tiers = {f.tier for f in report.findings}
    assert 2 in tiers
    assert 3 in tiers
    assert report.overall_passed is True


# ---------------------------------------------------------------------------
# SKILL type — valid SKILL.md passes Tier 0
# ---------------------------------------------------------------------------


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_skill_valid_skmd_passes_tier0(mock_behavioral, mock_adversarial):
    artifact = _make_artifact(
        cap_type=CapabilityType.SKILL,
        source_code=None,
        skill_content=_VALID_SKILL_MD,
    )
    mock_behavioral.return_value = []
    mock_adversarial.return_value = _passing_report("test-cap", tier=3)

    report = verify(artifact)

    # Should NOT have a tier=0 syntax rejection
    tier0_criticals = [
        f for f in report.findings if f.tier == 0 and f.severity == Severity.CRITICAL
    ]
    assert len(tier0_criticals) == 0


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_skill_invalid_skmd_rejected_at_tier0(mock_behavioral, mock_adversarial):
    artifact = _make_artifact(
        cap_type=CapabilityType.SKILL,
        source_code=None,
        skill_content=_INVALID_SKILL_MD,
    )

    report = verify(artifact)

    assert report.overall_passed is False
    assert report.tier_reached == 0
    mock_behavioral.assert_not_called()
    mock_adversarial.assert_not_called()


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_skill_syntax_error_not_reported_as_critical(mock_behavioral, mock_adversarial):
    """SKILL.md is not Python — SyntaxErrors from ast.parse should be filtered out."""
    artifact = _make_artifact(
        cap_type=CapabilityType.SKILL,
        source_code=None,
        skill_content=_VALID_SKILL_MD,
    )
    mock_behavioral.return_value = []
    mock_adversarial.return_value = _passing_report("test-cap", tier=3)

    report = verify(artifact)

    syntax_criticals = [
        f for f in report.findings
        if f.category == "syntax" and f.severity == Severity.CRITICAL
    ]
    assert len(syntax_criticals) == 0


# ---------------------------------------------------------------------------
# SKILL type — mock indicator in SKILL.md is still detected
# ---------------------------------------------------------------------------


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_skill_with_mock_word_in_prose_passes_tier1(mock_behavioral, mock_adversarial):
    """Words like 'mock' inside SKILL.md prose must NOT block verification.
    Skills are natural-language instructions where these words appear
    legitimately. Mock-artifact detection is scoped to MCP_TOOL Python
    source only — see static_scanner._check_mock_artifacts.
    """
    skill_with_mock_word = """\
---
name: demo-skill
description: A skill that uses mock data for demonstration
---

## Overview

This skill uses mock_result to show sample data.
"""
    artifact = _make_artifact(
        cap_type=CapabilityType.SKILL,
        source_code=None,
        skill_content=skill_with_mock_word,
    )
    mock_behavioral.return_value = []
    mock_adversarial.return_value = _passing_report("test-cap", tier=3)

    report = verify(artifact)

    assert [f for f in report.findings if f.category == "mock_artifact"] == []


# ---------------------------------------------------------------------------
# AGENT type — JSON config scanned (regex only)
# ---------------------------------------------------------------------------


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_agent_clean_config_passes(mock_behavioral, mock_adversarial):
    artifact = _make_artifact(
        cap_type=CapabilityType.AGENT,
        source_code=None,
        agent_config=_CLEAN_AGENT_CONFIG,
    )
    mock_behavioral.return_value = []
    mock_adversarial.return_value = _passing_report("test-cap", tier=3)

    report = verify(artifact)

    # SyntaxError from JSON (not Python) must not appear
    syntax_criticals = [
        f for f in report.findings
        if f.category == "syntax" and f.severity == Severity.CRITICAL
    ]
    assert len(syntax_criticals) == 0


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_agent_with_mock_word_in_config_passes_tier1(mock_behavioral, mock_adversarial):
    """Agent system prompts referencing 'mock' as a domain term must NOT
    block verification. Mock-artifact detection is MCP_TOOL only.
    """
    artifact = _make_artifact(
        cap_type=CapabilityType.AGENT,
        source_code=None,
        agent_config={"system_prompt": "You are a mock agent for testing purposes."},
    )
    mock_behavioral.return_value = []
    mock_adversarial.return_value = _passing_report("test-cap", tier=3)

    report = verify(artifact)

    assert [f for f in report.findings if f.category == "mock_artifact"] == []


# ---------------------------------------------------------------------------
# Empty source code — graceful handling
# ---------------------------------------------------------------------------


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_empty_source_code_passes_tier1(mock_behavioral, mock_adversarial):
    """Empty source should not have CRITICAL findings from Tier 1."""
    artifact = _make_artifact(source_code="")
    mock_behavioral.return_value = []
    mock_adversarial.return_value = _passing_report("test-cap", tier=3)

    report = verify(artifact)

    # No CRITICAL from Tier 1 on empty code
    t1_criticals = [
        f for f in report.findings
        if f.tier == 1 and f.severity == Severity.CRITICAL
    ]
    assert len(t1_criticals) == 0


# ---------------------------------------------------------------------------
# Layered Tier 1: Python code fences in SKILL/AGENT
# ---------------------------------------------------------------------------


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_skill_code_fence_flags_hardcoded_data(mock_behavioral, mock_adversarial):
    """A SKILL.md with a Python code fence containing a hardcoded literal
    list return must be rejected — the fenced code is scanned with the
    same MCP_TOOL AST checks.
    """
    skill_with_bad_fence = '''\
---
name: bad-fence-skill
description: A skill that embeds bad example code
---

## Example

```python
def get_items():
    return ["alpha", "beta", "gamma", "delta"]
```
'''
    artifact = _make_artifact(
        cap_type=CapabilityType.SKILL,
        source_code=None,
        skill_content=skill_with_bad_fence,
    )
    mock_behavioral.return_value = []
    mock_adversarial.return_value = _passing_report("test-cap", tier=3)

    report = verify(artifact)

    fence_findings = [
        f for f in report.findings
        if f.category == "hardcoded_data" and "code_fence" in (f.location or "")
    ]
    assert len(fence_findings) >= 1
    assert report.overall_passed is False
    mock_behavioral.assert_not_called()
    mock_adversarial.assert_not_called()


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_skill_code_fence_flags_mock_import(mock_behavioral, mock_adversarial):
    """A SKILL.md whose Python fence imports unittest.mock is rejected."""
    skill = '''\
---
name: mock-fence-skill
description: A skill that imports a mocking library in its example
---

## Example

```python
import unittest.mock
def thing():
    return unittest.mock.MagicMock()
```
'''
    artifact = _make_artifact(
        cap_type=CapabilityType.SKILL,
        source_code=None,
        skill_content=skill,
    )

    report = verify(artifact)

    fence_findings = [
        f for f in report.findings
        if f.category == "mock_import" and "code_fence" in (f.location or "")
    ]
    assert len(fence_findings) >= 1
    assert report.overall_passed is False


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_skill_prose_placeholder_not_flagged(
    mock_behavioral, mock_adversarial
):
    """SKILL.md prose with ``placeholder`` must not be flagged.

    The layered scanner must only run AST checks on fenced code, never on
    prose.
    """
    skill = '''\
---
name: clean-skill
description: Build an inventory reorder pipeline
---

## Instructions

Compute reorder quantities. Do not leave placeholder values in the output.
Replace any placeholder fields with real computed values before returning.
This is not a mock implementation — call the real database.
'''
    artifact = _make_artifact(
        cap_type=CapabilityType.SKILL,
        source_code=None,
        skill_content=skill,
    )
    mock_behavioral.return_value = []
    mock_adversarial.return_value = _passing_report("test-cap", tier=3)

    report = verify(artifact)

    criticals = [f for f in report.findings if f.severity == Severity.CRITICAL]
    assert criticals == []
    assert report.overall_passed is True


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_skill_clean_code_fence_passes(mock_behavioral, mock_adversarial):
    """A SKILL.md with a benign Python code fence must pass."""
    skill = '''\
---
name: benign-fence-skill
description: A skill with a clean example
---

## Example

```python
def add(a: int, b: int) -> int:
    return a + b
```
'''
    artifact = _make_artifact(
        cap_type=CapabilityType.SKILL,
        source_code=None,
        skill_content=skill,
    )
    mock_behavioral.return_value = []
    mock_adversarial.return_value = _passing_report("test-cap", tier=3)

    report = verify(artifact)

    fence_criticals = [
        f for f in report.findings
        if f.severity == Severity.CRITICAL and "code_fence" in (f.location or "")
    ]
    assert fence_criticals == []
    assert report.overall_passed is True


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_agent_system_prompt_fence_scanned(mock_behavioral, mock_adversarial):
    """An AGENT whose system_prompt contains a Python code fence with a
    hardcoded literal list must be rejected. Fence findings are tagged
    with the system_prompt source label.
    """
    agent_config = {
        "system_prompt": (
            "You are an analyst. Use this helper:\n\n"
            "```python\n"
            "def get_items():\n"
            "    return ['alpha', 'beta', 'gamma', 'delta']\n"
            "```\n"
        )
    }
    artifact = _make_artifact(
        cap_type=CapabilityType.AGENT,
        source_code=None,
        agent_config=agent_config,
    )

    report = verify(artifact)

    fence_findings = [
        f for f in report.findings
        if f.category == "hardcoded_data"
        and "system_prompt" in (f.location or "")
    ]
    assert len(fence_findings) >= 1
    assert report.overall_passed is False
    mock_behavioral.assert_not_called()
    mock_adversarial.assert_not_called()


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_agent_skill_content_fence_scanned(mock_behavioral, mock_adversarial):
    """An AGENT whose agent_config['skill_content'] embeds a bad fence is rejected."""
    agent_config = {
        "system_prompt": "You are an analyst.",
        "skill_content": (
            "## Example\n\n"
            "```python\n"
            "import faker\n"
            "def gen():\n"
            "    return faker.Faker().name()\n"
            "```\n"
        ),
    }
    artifact = _make_artifact(
        cap_type=CapabilityType.AGENT,
        source_code=None,
        agent_config=agent_config,
    )

    report = verify(artifact)

    fence_findings = [
        f for f in report.findings
        if f.category == "mock_import"
        and "skill_content" in (f.location or "")
    ]
    assert len(fence_findings) >= 1
    assert report.overall_passed is False


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_agent_no_fence_prose_passes(mock_behavioral, mock_adversarial):
    """An AGENT system prompt referencing 'placeholder' in prose with no
    Python fence must pass — the layered scanner only inspects fenced code.
    """
    agent_config = {
        "system_prompt": (
            "You are a careful analyst. Do not leave placeholder values in "
            "your output. This is not a mock — call the real API."
        )
    }
    artifact = _make_artifact(
        cap_type=CapabilityType.AGENT,
        source_code=None,
        agent_config=agent_config,
    )
    mock_behavioral.return_value = []
    mock_adversarial.return_value = _passing_report("test-cap", tier=3)

    report = verify(artifact)

    criticals = [f for f in report.findings if f.severity == Severity.CRITICAL]
    assert criticals == []
    assert report.overall_passed is True


@patch("src.verification.gate._static_scan")
def test_verify_never_raises_on_unexpected_exception(mock_static_scan):
    """verify() must catch exceptions and fail closed.

    The gate emits a CRITICAL ``gate_error`` finding with
    ``overall_passed=False`` so infrastructure errors do not manufacture
    PASS verdicts.
    """
    mock_static_scan.side_effect = OSError("Simulated filesystem failure in static scan")
    artifact = _make_artifact(
        cap_type=CapabilityType.SKILL,
        source_code=None,
        skill_content=_VALID_SKILL_MD,
    )

    # Must not raise even when internal scan raises (never-raises contract
    # preserved).
    report = verify(artifact)

    assert isinstance(report, VerificationReport)
    assert report.capability_name == "test-cap"
    # Fails closed (CRITICAL); the node's retry loop will regenerate.
    assert any(f.category == "gate_error" for f in report.findings)
    gate_finding = next(f for f in report.findings if f.category == "gate_error")
    assert gate_finding.severity == Severity.CRITICAL
    assert report.overall_passed is False


# ---------------------------------------------------------------------------
# allow_network plumbing
# ---------------------------------------------------------------------------


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_verify_forwards_allow_network_true(mock_behavioral, mock_adversarial):
    """verify(artifact, allow_network=True) must forward the kwarg to
    both behavioral.run and adversarial.probe so Tier 2 / Tier 3 probe
    under the same network policy the artifact will execute under.
    """
    artifact = _make_artifact(source_code=_CLEAN_MCP_CODE)
    mock_behavioral.return_value = []
    mock_adversarial.return_value = _passing_report("test-cap", tier=3)

    verify(artifact, allow_network=True)

    mock_behavioral.assert_called_once_with(artifact, allow_network=True)
    mock_adversarial.assert_called_once_with(artifact, allow_network=True)


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_verify_forwards_allow_network_true_to_static_scan(
    mock_behavioral, mock_adversarial
):
    """Tier 1 missing-external-call severity follows the task network policy."""
    code = """\
def fetch_records():
    return []
"""
    artifact = _make_artifact(source_code=code)
    mock_behavioral.return_value = [
        VerificationFinding(
            tier=2,
            category="network_behavior",
            severity=Severity.HIGH,
            message="Injected Tier 2 finding so Tier 1 HIGH is not cleared",
        )
    ]
    mock_adversarial.return_value = _passing_report("test-cap", tier=3)

    report = verify(artifact, allow_network=True)

    findings = [
        f for f in report.findings
        if f.category == "missing_external_calls"
    ]
    assert findings
    assert findings[0].severity == Severity.HIGH


@patch("src.verification.gate.adversarial.probe")
@patch("src.verification.gate.behavioral.run")
def test_verify_defaults_allow_network_false(mock_behavioral, mock_adversarial):
    """Omitting allow_network keeps the offline-by-default posture."""
    artifact = _make_artifact(source_code=_CLEAN_MCP_CODE)
    mock_behavioral.return_value = []
    mock_adversarial.return_value = _passing_report("test-cap", tier=3)

    verify(artifact)

    _, kwargs_b = mock_behavioral.call_args
    _, kwargs_a = mock_adversarial.call_args
    assert kwargs_b.get("allow_network") is False
    assert kwargs_a.get("allow_network") is False
