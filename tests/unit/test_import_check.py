"""Unit tests for src/verification/import_check.py (Tier 1.5).

All Docker calls (sandbox_execute) are patched — these tests run without
a real Docker daemon. Integration behavior (actually running Docker) is
covered indirectly by Phase 4 smoke tests.
"""

from __future__ import annotations

from unittest.mock import patch

from src.schemas.capability import CapabilityType
from src.schemas.task import GeneratedArtifact
from src.schemas.verification import Severity, VerificationReport
from src.verification.import_check import (
    run_import_check,
    should_proceed_to_tier2,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_CLEAN_TOOL_SOURCE = '''\
"""A clean MCP tool that imports fine."""
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from typing import Annotated

mcp = FastMCP("adder")


@mcp.tool()
async def add(
    a: Annotated[int, Field(description="First number")],
    b: Annotated[int, Field(description="Second number")],
) -> int:
    return a + b


if __name__ == "__main__":
    mcp.run()
'''


def _sandbox_success(stdout: str = "IMPORT_PROBE_OK", stderr: str = "") -> dict:
    return {"success": True, "exit_code": 0, "stdout": stdout, "stderr": stderr}


def _sandbox_failure(stderr: str) -> dict:
    return {"success": False, "exit_code": 1, "stdout": "", "stderr": stderr}


def _mcp_artifact(
    name: str = "test-tool",
    source: str | None = _CLEAN_TOOL_SOURCE,
    pip_packages: list[str] | None = None,
) -> GeneratedArtifact:
    return GeneratedArtifact(
        capability_name=name,
        capability_type=CapabilityType.MCP_TOOL,
        source_code=source,
        pip_packages=pip_packages or [],
    )


def _skill_artifact(name: str = "test-skill", content: str = "") -> GeneratedArtifact:
    return GeneratedArtifact(
        capability_name=name,
        capability_type=CapabilityType.SKILL,
        skill_content=content,
    )


def _agent_artifact(name: str = "test-agent", config: dict | None = None) -> GeneratedArtifact:
    return GeneratedArtifact(
        capability_name=name,
        capability_type=CapabilityType.AGENT,
        agent_config=config,
    )


# ---------------------------------------------------------------------------
# MCP_TOOL — Docker dry-run import
# ---------------------------------------------------------------------------


class TestMcpToolImportProbe:
    """Patch the sandbox_tool.sandbox_execute entry point to avoid Docker.

    Note: the import_check module imports sandbox_execute lazily inside
    _check_mcp_tool (not at module load time), so we patch the source
    module rather than a re-exported reference.
    """

    @patch("src.verification.sandbox_tool.sandbox_execute")
    def test_clean_tool_returns_no_findings(self, mock_sandbox):
        mock_sandbox.return_value = _sandbox_success()
        report = run_import_check(_mcp_artifact())
        assert report.overall_passed is True
        assert len(report.findings) == 0
        assert report.tier_reached == 1

    @patch("src.verification.sandbox_tool.sandbox_execute")
    def test_wrong_pip_package_yields_critical(self, mock_sandbox):
        """pip install failure — stderr lacks IMPORT_PROBE_ERROR sentinel."""
        mock_sandbox.return_value = _sandbox_failure(
            "ERROR: Could not find a version that satisfies the requirement nonexistent-pkg\n"
            "ERROR: No matching distribution found for nonexistent-pkg"
        )
        report = run_import_check(_mcp_artifact(pip_packages=["nonexistent-pkg"]))
        assert report.overall_passed is False
        assert len(report.findings) == 1
        finding = report.findings[0]
        assert finding.severity == Severity.CRITICAL
        assert finding.category == "wrong_pip_package"
        assert "does not import cleanly" in finding.message
        assert "nonexistent-pkg" in finding.message

    @patch("src.verification.sandbox_tool.sandbox_execute")
    def test_import_error_yields_critical(self, mock_sandbox):
        """Runtime import failure — stderr contains IMPORT_PROBE_ERROR sentinel."""
        mock_sandbox.return_value = _sandbox_failure(
            "IMPORT_PROBE_ERROR:ModuleNotFoundError: No module named 'nonexistent_import'"
        )
        report = run_import_check(_mcp_artifact(pip_packages=[]))
        assert report.overall_passed is False
        finding = report.findings[0]
        assert finding.severity == Severity.CRITICAL
        assert finding.category == "import_error"
        assert "ModuleNotFoundError" in finding.message

    @patch("src.verification.sandbox_tool.sandbox_execute")
    def test_empty_source_skipped(self, mock_sandbox):
        """Empty source_code produces an INFO-level 'skipped' finding."""
        report = run_import_check(_mcp_artifact(source=""))
        assert report.overall_passed is True
        assert len(report.findings) == 1
        assert report.findings[0].severity == Severity.INFO
        assert report.findings[0].category == "import_check_skipped"
        mock_sandbox.assert_not_called()  # no Docker call for empty source

    @patch("src.verification.sandbox_tool.sandbox_execute")
    def test_pip_packages_threaded_to_sandbox(self, mock_sandbox):
        """Artifact's declared pip_packages reach sandbox_execute via extra_packages."""
        mock_sandbox.return_value = _sandbox_success()
        run_import_check(_mcp_artifact(pip_packages=["pyyaml", "scikit-learn"]))
        assert mock_sandbox.call_count == 1
        kwargs = mock_sandbox.call_args.kwargs
        assert kwargs["extra_packages"] == ["pyyaml", "scikit-learn"]

    @patch("src.verification.sandbox_tool.sandbox_execute")
    def test_empty_pip_packages_pass_none_not_empty_list(self, mock_sandbox):
        """Empty pip_packages should pass None to sandbox_execute (avoid install step)."""
        mock_sandbox.return_value = _sandbox_success()
        run_import_check(_mcp_artifact(pip_packages=[]))
        kwargs = mock_sandbox.call_args.kwargs
        assert kwargs["extra_packages"] is None

    @patch("src.verification.sandbox_tool.sandbox_execute")
    def test_stderr_truncated_to_last_500_chars(self, mock_sandbox):
        """Long stderr is truncated for retry-loop readability."""
        long_stderr = "X" * 2000 + "IMPORT_PROBE_ERROR:ImportError: final chunk"
        mock_sandbox.return_value = _sandbox_failure(long_stderr)
        report = run_import_check(_mcp_artifact())
        finding = report.findings[0]
        # Message carries at most the last 500 chars of stderr (plus framing).
        assert "final chunk" in finding.message
        # Cannot exceed original stderr length × some framing
        assert len(finding.message) < 1000

    @patch("src.verification.sandbox_tool.sandbox_execute")
    def test_sandbox_raises_fail_open(self, mock_sandbox):
        """Docker daemon unavailable → MEDIUM import_check_error, overall_passed=True."""
        mock_sandbox.side_effect = RuntimeError("Cannot connect to Docker daemon")
        report = run_import_check(_mcp_artifact())
        assert report.overall_passed is True  # fail-open
        assert len(report.findings) == 1
        finding = report.findings[0]
        assert finding.severity == Severity.MEDIUM
        assert finding.category == "import_check_error"
        assert "raised unexpectedly" in finding.message


# ---------------------------------------------------------------------------
# SKILL — in-process fence compile
# ---------------------------------------------------------------------------


class TestSkillImportProbe:
    def test_skill_with_no_fences_passes(self):
        content = (
            "---\n"
            "name: clean-skill\n"
            "description: Does things.\n"
            "---\n\n"
            "# Overview\n\nPure prose, no code.\n"
        )
        report = run_import_check(_skill_artifact(content=content))
        assert report.overall_passed is True
        assert report.findings == []

    def test_skill_with_valid_python_fence_passes(self):
        content = (
            "---\n"
            "name: skill-with-code\n"
            "description: Has a valid snippet.\n"
            "---\n\n"
            "```python\n"
            "def helper(x: int) -> int:\n"
            "    return x * 2\n"
            "```\n"
        )
        report = run_import_check(_skill_artifact(content=content))
        assert report.overall_passed is True
        assert report.findings == []

    def test_skill_with_malformed_fence_yields_critical(self):
        """Unterminated string / mismatched brackets — compile() catches these."""
        content = (
            "---\n"
            "name: broken-skill\n"
            "description: Has a syntax error.\n"
            "---\n\n"
            "```python\n"
            "def helper(x):\n"
            "    return 'unterminated\n"
            "```\n"
        )
        report = run_import_check(_skill_artifact(content=content))
        assert report.overall_passed is False
        assert len(report.findings) == 1
        finding = report.findings[0]
        assert finding.severity == Severity.CRITICAL
        assert finding.category == "fence_compile_error"
        assert "failed to compile" in finding.message
        assert "skill_content/code_fence[0]" == finding.location

    def test_skill_with_multiple_fences_reports_per_fence(self):
        content = (
            "```python\n"
            "def ok(): pass\n"
            "```\n"
            "```python\n"
            "def broken(: pass\n"
            "```\n"
        )
        report = run_import_check(_skill_artifact(content=content))
        assert report.overall_passed is False
        assert len(report.findings) == 1
        # Second fence (index 1) is the broken one
        assert "code_fence[1]" in report.findings[0].location

    def test_empty_skill_content_passes(self):
        report = run_import_check(_skill_artifact(content=""))
        assert report.overall_passed is True
        assert report.findings == []


# ---------------------------------------------------------------------------
# AGENT — fence compile + structural check
# ---------------------------------------------------------------------------


class TestAgentImportProbe:
    def test_agent_with_valid_config_passes(self):
        config = {
            "system_prompt": "You are a helpful data analyst.",
            "model": "openai:gpt-5-mini",
        }
        report = run_import_check(_agent_artifact(config=config))
        assert report.overall_passed is True
        assert report.findings == []

    def test_agent_missing_system_prompt_yields_critical(self):
        config = {"model": "openai:gpt-5-mini"}
        report = run_import_check(_agent_artifact(config=config))
        assert report.overall_passed is False
        finding = report.findings[0]
        assert finding.severity == Severity.CRITICAL
        assert finding.category == "import_check_structural"
        assert "system_prompt" in finding.message

    def test_agent_empty_system_prompt_yields_critical(self):
        config = {"system_prompt": "   "}
        report = run_import_check(_agent_artifact(config=config))
        assert report.overall_passed is False
        assert report.findings[0].category == "import_check_structural"

    def test_agent_no_config_yields_critical(self):
        report = run_import_check(_agent_artifact(config=None))
        assert report.overall_passed is False
        finding = report.findings[0]
        assert finding.severity == Severity.CRITICAL
        assert finding.category == "import_check_structural"

    def test_agent_broken_fence_in_system_prompt_yields_critical(self):
        config = {
            "system_prompt": (
                "You are a helper.\n\n"
                "```python\n"
                "def broken(: pass\n"
                "```\n"
            ),
        }
        report = run_import_check(_agent_artifact(config=config))
        assert report.overall_passed is False
        # One CRITICAL from the fence compile error.
        fence_findings = [
            f for f in report.findings if f.category == "fence_compile_error"
        ]
        assert len(fence_findings) == 1
        assert "agent_config.system_prompt" in fence_findings[0].location

    def test_agent_broken_fence_in_skill_content_yields_critical(self):
        config = {
            "system_prompt": "A minimal helper.",
            "skill_content": (
                "# Helper\n"
                "```python\n"
                "def broken(: pass\n"
                "```\n"
            ),
        }
        report = run_import_check(_agent_artifact(config=config))
        assert report.overall_passed is False
        fence_findings = [
            f for f in report.findings if f.category == "fence_compile_error"
        ]
        assert len(fence_findings) == 1
        assert "agent_config.skill_content" in fence_findings[0].location


# ---------------------------------------------------------------------------
# should_proceed_to_tier2 helper
# ---------------------------------------------------------------------------


class TestShouldProceedToTier2:
    def _make_report(self, severities: list[Severity]) -> VerificationReport:
        from src.schemas.verification import VerificationFinding

        return VerificationReport(
            capability_name="x",
            findings=[
                VerificationFinding(
                    tier=1,
                    category="test",
                    severity=s,
                    message="m",
                )
                for s in severities
            ],
            overall_passed=not any(s == Severity.CRITICAL for s in severities),
            tier_reached=1,
        )

    def test_empty_report_proceeds(self):
        assert should_proceed_to_tier2(self._make_report([])) is True

    def test_medium_only_proceeds(self):
        assert should_proceed_to_tier2(self._make_report([Severity.MEDIUM])) is True

    def test_critical_blocks(self):
        assert should_proceed_to_tier2(self._make_report([Severity.CRITICAL])) is False

    def test_high_without_critical_proceeds(self):
        """HIGH is not a Tier 1.5 blocker per design (same as Tier 1's rule)."""
        assert should_proceed_to_tier2(self._make_report([Severity.HIGH])) is True


# ---------------------------------------------------------------------------
# Gate integration — Tier 1.5 sits between Tier 1 and Tier 2
# ---------------------------------------------------------------------------


class TestGateWiring:
    @patch("src.verification.gate.adversarial.probe")
    @patch("src.verification.gate.behavioral.run")
    @patch("src.verification.sandbox_tool.sandbox_execute")
    def test_tier_1_5_critical_skips_tier_2_and_3(
        self, mock_sandbox, mock_behavioral, mock_adversarial
    ):
        """A CRITICAL Tier 1.5 finding (wrong pip package) short-circuits
        the gate so Tier 2 + Tier 3 never fire. Guards the cost-savings
        contract that motivates Tier 1.5 existing at all.
        """
        from src.verification.gate import verify

        mock_sandbox.return_value = _sandbox_failure(
            "ERROR: No matching distribution found for nonexistent-pkg"
        )

        artifact = _mcp_artifact(pip_packages=["nonexistent-pkg"])
        report = verify(artifact)

        assert report.overall_passed is False
        assert report.tier_reached == 1  # Tier 1.5 reports stay at tier_reached=1
        mock_behavioral.assert_not_called()
        mock_adversarial.assert_not_called()

    @patch("src.verification.gate.adversarial.probe")
    @patch("src.verification.gate.behavioral.run")
    @patch("src.verification.sandbox_tool.sandbox_execute")
    def test_tier_1_5_pass_still_runs_tier_2_and_3(
        self, mock_sandbox, mock_behavioral, mock_adversarial
    ):
        """Clean Tier 1.5 must not block the pipeline — downstream tiers run."""
        from src.verification.gate import verify

        mock_sandbox.return_value = _sandbox_success()
        mock_behavioral.return_value = []
        mock_adversarial.return_value = VerificationReport(
            capability_name="test-tool",
            findings=[],
            overall_passed=True,
            tier_reached=3,
        )

        report = verify(_mcp_artifact())

        assert report.overall_passed is True
        assert report.tier_reached == 3
        mock_behavioral.assert_called_once()
        mock_adversarial.assert_called_once()
