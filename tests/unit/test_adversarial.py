"""Unit tests for Tier 3 adversarial probing.

Tests cover:
- Successful probe with suspicious findings -> VerificationReport with tier=3
- Successful probe with clean findings -> passes with no findings
- Timeout -> partial report with error flag, never raises
- Agent creation failure -> error report, never raises
- No structured response -> error report
- Empty source code -> skip report
- Hidden demo mode findings -> appropriate severity
- Network disconnect findings -> appropriate severity
- Ground truth mismatch -> appropriate severity
- Only suspicious findings are included in the report
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.schemas.capability import CapabilityType
from src.schemas.task import GeneratedArtifact
from src.schemas.verification import Severity
from src.verification.adversarial import (
    ADVERSARIAL_RECURSION_LIMIT,
    AdversarialFinding,
    AdversarialFindings,
    _error_report,
    _findings_to_report,
    _timeout_report,
    probe,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _artifact(
    name: str = "test-tool",
    source: str = "async def tool(n: int) -> int:\n    return n * 2",
) -> GeneratedArtifact:
    return GeneratedArtifact(
        capability_name=name,
        capability_type=CapabilityType.MCP_TOOL,
        source_code=source,
    )


def _clean_findings() -> AdversarialFindings:
    return AdversarialFindings(
        findings=[
            AdversarialFinding(
                attack_pattern="hidden_demo_mode",
                suspicious=False,
                evidence="No demo/sample mode detected",
                severity=Severity.INFO,
            ),
            AdversarialFinding(
                attack_pattern="network_disconnect",
                suspicious=False,
                evidence="Tool is purely computational, no network expected",
                severity=Severity.INFO,
            ),
            AdversarialFinding(
                attack_pattern="ground_truth",
                suspicious=False,
                evidence="Output matches reference implementation",
                severity=Severity.INFO,
            ),
        ],
        conclusion="Tool appears genuine.",
    )


def _suspicious_findings() -> AdversarialFindings:
    return AdversarialFindings(
        findings=[
            AdversarialFinding(
                attack_pattern="hidden_demo_mode",
                suspicious=True,
                evidence="Tool returns canned 'Hello World' for any input containing 'test'",
                severity=Severity.CRITICAL,
            ),
            AdversarialFinding(
                attack_pattern="network_disconnect",
                suspicious=False,
                evidence="Tool is computational, no network needed",
                severity=Severity.INFO,
            ),
            AdversarialFinding(
                attack_pattern="ground_truth",
                suspicious=True,
                evidence="Tool returns 8 for fibonacci(5), expected 5",
                severity=Severity.HIGH,
            ),
        ],
        conclusion="Tool shows signs of deceptive behavior.",
    )


def _mock_agent_invoke(structured_response):
    """Return a mock agent whose invoke returns the given structured_response."""
    mock_agent = MagicMock()
    mock_agent.invoke.return_value = {"structured_response": structured_response}
    return mock_agent


# ---------------------------------------------------------------------------
# _findings_to_report conversion tests
# ---------------------------------------------------------------------------


class TestFindingsToReport:
    def test_clean_findings_produce_empty_report(self):
        report = _findings_to_report(_clean_findings(), "test-tool")
        assert report.overall_passed is True
        assert report.tier_reached == 3
        assert report.capability_name == "test-tool"
        assert len(report.findings) == 0  # non-suspicious are filtered out

    def test_suspicious_findings_produce_tier3_findings(self):
        report = _findings_to_report(_suspicious_findings(), "test-tool")
        assert report.overall_passed is False  # has CRITICAL
        assert report.tier_reached == 3
        assert len(report.findings) == 2  # only the 2 suspicious ones
        assert all(f.tier == 3 for f in report.findings)

    def test_suspicious_high_only_passes(self):
        findings = AdversarialFindings(
            findings=[
                AdversarialFinding(
                    attack_pattern="ground_truth",
                    suspicious=True,
                    evidence="Minor discrepancy in rounding",
                    severity=Severity.HIGH,
                ),
            ],
            conclusion="Minor issues found.",
        )
        report = _findings_to_report(findings, "tool-a")
        assert report.overall_passed is True  # no CRITICAL -> passes
        assert len(report.findings) == 1
        assert report.findings[0].severity == Severity.HIGH


# ---------------------------------------------------------------------------
# _timeout_report and _error_report tests
# ---------------------------------------------------------------------------


class TestPartialReports:
    def test_timeout_report(self):
        report = _timeout_report("my-tool")
        assert report.capability_name == "my-tool"
        assert report.overall_passed is True  # fail open
        assert report.tier_reached == 3
        assert len(report.findings) == 1
        assert report.findings[0].category == "adversarial_timeout"
        assert report.findings[0].severity == Severity.MEDIUM

    def test_error_report(self):
        report = _error_report("my-tool", "Something broke")
        assert report.capability_name == "my-tool"
        assert report.overall_passed is True  # fail open
        assert report.tier_reached == 3
        assert len(report.findings) == 1
        assert "Something broke" in report.findings[0].message


# ---------------------------------------------------------------------------
# probe() tests
# ---------------------------------------------------------------------------


class TestProbe:
    @patch("src.verification.adversarial._create_adversarial_agent")
    def test_successful_probe_clean(self, mock_create):
        mock_create.return_value = _mock_agent_invoke(_clean_findings())
        report = probe(_artifact())
        assert report.overall_passed is True
        assert report.tier_reached == 3
        assert len(report.findings) == 0

    @patch("src.verification.adversarial._create_adversarial_agent")
    def test_successful_probe_suspicious(self, mock_create):
        mock_create.return_value = _mock_agent_invoke(_suspicious_findings())
        report = probe(_artifact())
        assert report.overall_passed is False
        assert report.tier_reached == 3
        assert len(report.findings) == 2
        categories = {f.category for f in report.findings}
        assert "hidden_demo_mode" in categories
        assert "ground_truth" in categories

    @patch("src.verification.adversarial._create_adversarial_agent")
    def test_timeout_returns_partial_report(self, mock_create):
        mock_agent = MagicMock()
        # Simulate timeout by making invoke block
        import concurrent.futures

        mock_agent.invoke.side_effect = lambda *a, **kw: (_ for _ in ()).throw(
            concurrent.futures.TimeoutError()
        )
        mock_create.return_value = mock_agent

        # Patch the timeout to be very short for testing
        with patch("src.verification.adversarial.ADVERSARIAL_TIMEOUT_S", 0.01):
            # The ThreadPoolExecutor will time out
            mock_agent_real = MagicMock()
            import time

            def slow_invoke(*args, **kwargs):
                time.sleep(5)
                return {"structured_response": _clean_findings()}

            mock_agent_real.invoke = slow_invoke
            mock_create.return_value = mock_agent_real
            report = probe(_artifact())

        assert report.overall_passed is True  # fail open
        assert report.tier_reached == 3
        assert any(
            "timeout" in f.category.lower() or "error" in f.category.lower()
            for f in report.findings
        )

    @patch("src.verification.adversarial._create_adversarial_agent")
    def test_agent_error_returns_error_report(self, mock_create):
        mock_agent = MagicMock()
        mock_agent.invoke.side_effect = RuntimeError("LLM exploded")
        mock_create.return_value = mock_agent

        report = probe(_artifact())
        assert report.overall_passed is True  # fail open
        assert report.tier_reached == 3
        assert any("LLM exploded" in f.message for f in report.findings)

    @patch("src.verification.adversarial._create_adversarial_agent")
    def test_no_structured_response(self, mock_create):
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {"structured_response": None}
        mock_create.return_value = mock_agent

        report = probe(_artifact())
        assert report.overall_passed is True  # fail open
        assert any("no structured response" in f.message.lower() for f in report.findings)

    def test_empty_source_code_skips(self):
        artifact = GeneratedArtifact(
            capability_name="empty-tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code="",
        )
        report = probe(artifact)
        assert report.overall_passed is True
        assert report.tier_reached == 3
        assert report.findings[0].category == "adversarial_skip"

    def test_none_source_code_with_skill_content(self):
        artifact = GeneratedArtifact(
            capability_name="my-skill",
            capability_type=CapabilityType.SKILL,
            skill_content="---\nname: my-skill\ndescription: does stuff\n---\n# My Skill",
        )
        # This should attempt to probe (not skip) since skill_content is present
        with patch("src.verification.adversarial._create_adversarial_agent") as mock_create:
            mock_create.return_value = _mock_agent_invoke(_clean_findings())
            report = probe(artifact)
        assert report.tier_reached == 3

    @patch("src.verification.adversarial._create_adversarial_agent")
    def test_agent_creation_failure(self, mock_create):
        mock_create.side_effect = ImportError("deepagents not installed")
        report = probe(_artifact())
        assert report.overall_passed is True  # fail open
        assert any("Agent creation failed" in f.message for f in report.findings)

    @patch("src.verification.adversarial._create_adversarial_agent")
    def test_recursion_limit_passed_to_config(self, mock_create):
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {"structured_response": _clean_findings()}
        mock_create.return_value = mock_agent

        probe(_artifact())

        call_args = mock_agent.invoke.call_args
        config = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("config", {})
        assert config.get("recursion_limit") == ADVERSARIAL_RECURSION_LIMIT


# ---------------------------------------------------------------------------
# Pydantic model validation tests
# ---------------------------------------------------------------------------


class TestAdversarialModels:
    def test_adversarial_finding_forbids_extra(self):
        with pytest.raises(Exception):
            AdversarialFinding(
                attack_pattern="hidden_demo_mode",
                suspicious=True,
                evidence="test",
                severity=Severity.HIGH,
                extra_field="not allowed",
            )

    def test_adversarial_findings_forbids_extra(self):
        with pytest.raises(Exception):
            AdversarialFindings(
                findings=[],
                conclusion="ok",
                extra_field="not allowed",
            )

    def test_adversarial_finding_valid_severities(self):
        for sev in Severity:
            f = AdversarialFinding(
                attack_pattern="ground_truth",
                suspicious=False,
                evidence="test",
                severity=sev,
            )
            assert f.severity == sev


# ---------------------------------------------------------------------------
# Integration test (requires DEEPSEEK_API_KEY + Docker)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_adversarial_probe_integration():
    """Full adversarial probe against a simple tool.

    Requires DEEPSEEK_API_KEY and the jit-sandbox Docker image.
    """
    import os

    if not os.environ.get("DEEPSEEK_API_KEY"):
        pytest.skip("DEEPSEEK_API_KEY not set")

    import subprocess

    result = subprocess.run(
        ["docker", "image", "inspect", "jit-sandbox"],
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip("jit-sandbox Docker image not available")

    artifact = GeneratedArtifact(
        capability_name="doubler",
        capability_type=CapabilityType.MCP_TOOL,
        source_code=(
            "async def double(n: int) -> int:\n"
            "    \"\"\"Double a number.\"\"\"\n"
            "    return n * 2\n"
        ),
    )
    report = probe(artifact)
    assert report.capability_name == "doubler"
    assert report.tier_reached == 3
    # The report should exist (pass or fail) but never raise
    assert isinstance(report.overall_passed, bool)
