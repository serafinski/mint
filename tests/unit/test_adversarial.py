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
    _create_adversarial_agent,
    _error_report,
    _findings_to_report,
    _make_probe_sandbox_execute,
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
    def test_timeout_report_fail_closed_high_severity(self):
        """Step 14.11: _timeout_report now returns fail-CLOSED with HIGH
        severity so the verification_node retry loop fires. Was fail-open
        with MEDIUM severity before 2026-04-15; see docs/thesis_proposal.md
        §Pillar 2 'must pass all three tiers' — fail-open violated that
        contract by letting un-verified artifacts into the registry."""
        report = _timeout_report("my-tool")
        assert report.capability_name == "my-tool"
        assert report.overall_passed is False  # fail-closed per Step 14.11
        assert report.tier_reached == 3
        assert len(report.findings) == 1
        assert report.findings[0].category == "adversarial_timeout"
        assert report.findings[0].severity == Severity.HIGH
        assert "fail-closed" in report.findings[0].message.lower()

    def test_error_report_still_fail_open(self):
        """Error branch deliberately left fail-open by Step 14.11.
        Provider errors (rate limits, transient 500s) would trigger
        expensive regenerations for reasons the LLM can't fix. If
        Phase 5 surfaces a silent-error fail-open pattern, revisit in
        §12 or Chapter 7."""
        report = _error_report("my-tool", "Something broke")
        assert report.capability_name == "my-tool"
        assert report.overall_passed is True  # deliberately fail-open
        assert report.tier_reached == 3
        assert len(report.findings) == 1
        assert "Something broke" in report.findings[0].message
        assert report.findings[0].severity == Severity.MEDIUM

    def test_adversarial_timeout_ceiling_is_480s(self):
        """Regression guard for the Step 14.11 bump 300 → 480. Any
        change to this constant must be justified by a concrete
        langfuse trace showing real probe work near the new ceiling,
        same discipline as Step 13's AGENT_EXECUTION_TIMEOUT_S guard."""
        from src.verification.adversarial import ADVERSARIAL_TIMEOUT_S
        assert ADVERSARIAL_TIMEOUT_S == 480


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
    def test_timeout_returns_fail_closed_report(self, mock_create):
        """Step 14.11: flipped from fail-open to fail-closed. A probe
        that exceeds the ADVERSARIAL_TIMEOUT_S ceiling must return
        overall_passed=False with a HIGH-severity adversarial_timeout
        finding so the verification_node retry loop fires."""
        # Patch the timeout to be very short and use a blocking invoke
        with patch("src.verification.adversarial.ADVERSARIAL_TIMEOUT_S", 0.01):
            mock_agent_real = MagicMock()
            import time

            def slow_invoke(*args, **kwargs):
                time.sleep(5)
                return {"structured_response": _clean_findings()}

            mock_agent_real.invoke = slow_invoke
            mock_create.return_value = mock_agent_real
            report = probe(_artifact())

        assert report.overall_passed is False  # Step 14.11: fail-closed
        assert report.tier_reached == 3
        timeout_findings = [
            f for f in report.findings if f.category == "adversarial_timeout"
        ]
        assert len(timeout_findings) == 1
        assert timeout_findings[0].severity == Severity.HIGH

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

    # ------------------------------------------------------------------
    # Fence-extraction dispatch (Fix 2c-3)
    # ------------------------------------------------------------------
    # These tests verify the new type-aware source extraction that routes
    # SKILL/AGENT artifacts through ``_extract_python_fences`` instead of
    # feeding raw markdown prose to the adversarial agent. See
    # docs/perf_fixes_2026_04_14.md §2c-3 for the completeness rationale —
    # pre-fix, AGENT Tier 3 always skipped unconditionally because
    # ``artifact.source_code or artifact.skill_content`` is empty for AGENTs.

    def test_skill_with_python_fence_probes(self):
        """SKILL whose skill_content has a ```python fence is probed."""
        skill_body = (
            "---\nname: my-skill\ndescription: does stuff\n---\n"
            "# My Skill\n\n"
            "Do this computation:\n\n"
            "```python\n"
            "def compute(x: int) -> int:\n    return x * 2\n"
            "```\n"
        )
        artifact = GeneratedArtifact(
            capability_name="my-skill",
            capability_type=CapabilityType.SKILL,
            skill_content=skill_body,
        )
        with patch("src.verification.adversarial._create_adversarial_agent") as mock_create:
            mock_create.return_value = _mock_agent_invoke(_clean_findings())
            report = probe(artifact)
        # Agent was created and invoked — no skip.
        mock_create.assert_called_once()
        assert report.tier_reached == 3
        # Should not be an adversarial_skip — real probe happened.
        assert not any(
            f.category == "adversarial_skip" for f in report.findings
        )

    def test_skill_pure_prose_skips_with_specific_message(self):
        """SKILL with no Python fences skips Tier 3 with SKILL-specific message."""
        artifact = GeneratedArtifact(
            capability_name="prose-skill",
            capability_type=CapabilityType.SKILL,
            skill_content="---\nname: prose-skill\ndescription: pure prose\n---\n# Prose Only",
        )
        with patch("src.verification.adversarial._create_adversarial_agent") as mock_create:
            report = probe(artifact)
        # Agent must NOT be created for a pure-prose skill.
        mock_create.assert_not_called()
        assert report.tier_reached == 3
        assert report.overall_passed is True
        assert report.findings[0].category == "adversarial_skip"
        assert "skill_content" in report.findings[0].message.lower() or \
               "pure prose" in report.findings[0].message.lower()

    def test_agent_with_python_fence_in_system_prompt_probes(self):
        """AGENT whose agent_config.system_prompt has a ```python fence is probed.

        This is the critical completeness-fix test: pre-fix, every AGENT hit
        the unconditional skip path because the probe function only looked at
        ``artifact.source_code`` and ``artifact.skill_content``, which are
        empty for AGENTs. Post-fix, probe() extracts Python fences from
        ``agent_config.system_prompt`` (and ``agent_config.skill_content``)
        and feeds them to the adversarial agent.
        """
        agent_config = {
            "system_prompt": (
                "You are a math agent. Use this helper:\n\n"
                "```python\n"
                "def fib(n: int) -> int:\n"
                "    a, b = 0, 1\n"
                "    for _ in range(n):\n        a, b = b, a + b\n"
                "    return a\n"
                "```\n"
            ),
            "subagents": [],
        }
        artifact = GeneratedArtifact(
            capability_name="math-agent",
            capability_type=CapabilityType.AGENT,
            agent_config=agent_config,
        )
        with patch("src.verification.adversarial._create_adversarial_agent") as mock_create:
            mock_create.return_value = _mock_agent_invoke(_clean_findings())
            report = probe(artifact)
        mock_create.assert_called_once()
        assert report.tier_reached == 3
        assert not any(
            f.category == "adversarial_skip" for f in report.findings
        )

    def test_agent_with_python_fence_in_skill_content_probes(self):
        """AGENT fences can also live under ``agent_config.skill_content``."""
        agent_config = {
            "system_prompt": "You are a domain expert. Follow the skill below.",
            "skill_content": (
                "---\nname: x\ndescription: y\n---\n"
                "```python\nvalue = 42\n```\n"
            ),
            "subagents": [],
        }
        artifact = GeneratedArtifact(
            capability_name="skill-agent",
            capability_type=CapabilityType.AGENT,
            agent_config=agent_config,
        )
        with patch("src.verification.adversarial._create_adversarial_agent") as mock_create:
            mock_create.return_value = _mock_agent_invoke(_clean_findings())
            report = probe(artifact)
        mock_create.assert_called_once()
        assert report.tier_reached == 3
        assert not any(
            f.category == "adversarial_skip" for f in report.findings
        )

    def test_agent_without_python_fences_skips_with_specific_message(self):
        """AGENT with no Python fences anywhere skips with AGENT-specific message.

        This is the honest reporting of what pre-fix was silently happening on
        every AGENT — but now the skip message explains WHY, and it only fires
        when the agent_config genuinely has no executable Python content.
        """
        agent_config = {
            "system_prompt": "You are a plain prose agent. No code here.",
            "skill_content": "---\nname: x\n---\n# Prose\n\nNo fences.",
            "subagents": [],
        }
        artifact = GeneratedArtifact(
            capability_name="prose-agent",
            capability_type=CapabilityType.AGENT,
            agent_config=agent_config,
        )
        with patch("src.verification.adversarial._create_adversarial_agent") as mock_create:
            report = probe(artifact)
        mock_create.assert_not_called()
        assert report.tier_reached == 3
        assert report.overall_passed is True
        assert report.findings[0].category == "adversarial_skip"
        # The skip message must mention agent_config so post-mortem can tell
        # this apart from a SKILL skip or an MCP_TOOL empty-source skip.
        assert "agent_config" in report.findings[0].message.lower()

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
# Step 14.11 — fail-closed timeout triggers verification_node retry
# ---------------------------------------------------------------------------


class TestTimeoutTriggersRetry:
    """End-to-end: a Tier 3 timeout must propagate through the gate and
    trip the verification_node retry loop. This is the load-bearing
    thesis-alignment test — Pillar 2's 'must pass all three tiers'
    contract is only meaningful if a non-verdict routes to retry, not
    to registry."""

    def _failing_timeout_gate(self):
        """Gate fn that returns the exact shape _timeout_report produces
        on its fail-closed branch. Doesn't call the real adversarial
        probe — tests the gate → verification_node → retry round-trip
        in isolation."""

        def gate(artifact):
            return _timeout_report(artifact.capability_name)

        return gate

    def _passing_gate(self):
        from src.schemas.verification import VerificationReport

        def gate(artifact):
            return VerificationReport(
                capability_name=artifact.capability_name,
                findings=[],
                overall_passed=True,
                tier_reached=3,
            )

        return gate

    def test_timeout_report_trips_retry_loop(self):
        """Step 14.11 regression guard: feeding _timeout_report's output
        through verification_node must route to the generator for retry,
        not to loading_node. This is the behavior Pillar 2 requires
        ('must pass all three tiers' — a timeout isn't a pass)."""
        from src.orchestrator.verification_node import verification_node
        from src.schemas.task import GeneratedArtifact, TaskState

        artifact = GeneratedArtifact(
            capability_name="timed-out-tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code="async def tool(): pass",
            gap_query="timed-out-tool",
        )
        state = TaskState(
            task_description="t",
            generated_artifacts=[artifact],
            attempt_count=0,
        )

        cmd = verification_node(state, gate_fn=self._failing_timeout_gate())

        # Retry path: must route back to the generator, not forward to
        # loading_node. The timed-out artifact must be stripped from
        # state so the next attempt regenerates fresh code.
        assert cmd.goto == "generate_mcp_tool"
        assert cmd.update["attempt_count"] == 1
        remaining = [
            a.capability_name for a in cmd.update["generated_artifacts"]
        ]
        assert "timed-out-tool" not in remaining

    def test_retry_converges_on_second_attempt(self):
        """Attempt 1 times out (fail-closed), regeneration produces
        fresh code, attempt 2 passes Tier 3 → final routing is to
        loading_node with attempt_count=2. Simulates the full retry
        round-trip the pipeline does across regeneration boundaries."""
        from src.orchestrator.verification_node import verification_node
        from src.schemas.task import GeneratedArtifact, TaskState

        artifact_v1 = GeneratedArtifact(
            capability_name="tool-v1",
            capability_type=CapabilityType.MCP_TOOL,
            source_code="async def tool(): pass  # slow",
            gap_query="tool",
        )
        state_1 = TaskState(
            task_description="t",
            generated_artifacts=[artifact_v1],
            attempt_count=0,
        )
        cmd_1 = verification_node(state_1, gate_fn=self._failing_timeout_gate())
        assert cmd_1.goto == "generate_mcp_tool"
        assert cmd_1.update["attempt_count"] == 1

        # Simulate regeneration: pipeline produces a fresh artifact
        # with the same gap_query but different source. attempt_count
        # carries forward from cmd_1.update.
        artifact_v2 = GeneratedArtifact(
            capability_name="tool-v2",
            capability_type=CapabilityType.MCP_TOOL,
            source_code="async def tool(): pass  # fast",
            gap_query="tool",
        )
        state_2 = TaskState(
            task_description="t",
            generated_artifacts=[artifact_v2],
            attempt_count=cmd_1.update["attempt_count"],
        )
        cmd_2 = verification_node(state_2, gate_fn=self._passing_gate())
        assert cmd_2.goto == "loading_node"
        assert cmd_2.update["attempt_count"] == 2

    def test_non_timeout_exception_still_fail_open(self):
        """Step 14.11 deliberately does NOT fail-close the error branch.
        A RuntimeError in the adversarial agent (e.g., provider 500 or
        DeepAgents invocation crash) still returns a fail-open MEDIUM
        report, preserving the never-raises contract. If this changes,
        Phase 5 will see silent retries on transient provider errors."""
        with patch(
            "src.verification.adversarial._create_adversarial_agent"
        ) as mock_create:
            mock_agent = MagicMock()
            mock_agent.invoke.side_effect = RuntimeError("LLM 500")
            mock_create.return_value = mock_agent

            report = probe(_artifact())

        assert report.overall_passed is True  # deliberately fail-open
        assert report.tier_reached == 3
        error_findings = [
            f for f in report.findings if f.category == "adversarial_error"
        ]
        assert len(error_findings) == 1
        assert error_findings[0].severity == Severity.MEDIUM
        assert "LLM 500" in error_findings[0].message


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
# pip_packages propagation — Step 14.9
# ---------------------------------------------------------------------------


class TestPipPackagesPropagation:
    """Regression tests for Step 14.9: adversarial sandbox must install the
    generator's declared pip_packages so numerics-heavy artifacts
    (pandas / numpy / openpyxl) don't crash their first probe turn with
    ModuleNotFoundError.

    See docs/MASTER_PLAN_2026_04_13.md §14.9 for the motivating failure
    (langfuse trace 8ae5cd28a342fb9c8c0e0ea1262b050c).
    """

    def test_empty_packages_delegates_to_base(self):
        """With no packages, the wrapper delegates to base sandbox_execute
        with ``extra_packages=None`` — equivalent to the pre-fix behavior.
        """
        calls: list[dict] = []

        def fake_base(code, timeout=10, extra_packages=None, *, allow_network=False):
            calls.append({
                "code": code,
                "timeout": timeout,
                "extra_packages": extra_packages,
                "allow_network": allow_network,
            })
            return {"stdout": "", "stderr": "", "exit_code": 0, "success": True}

        with patch(
            "src.verification.sandbox_tool.sandbox_execute_silent",
            fake_base,
        ):
            wrapped = _make_probe_sandbox_execute([])
            wrapped(code="print('hi')")

        assert len(calls) == 1
        assert calls[0]["extra_packages"] is None

    def test_single_package_always_passed(self):
        """With ``pip_packages=['pandas']``, every call to the wrapped tool
        installs pandas — even when the LLM doesn't pass ``extra_packages``.
        """
        calls: list[dict] = []

        def fake_base(code, timeout=10, extra_packages=None, *, allow_network=False):
            calls.append({"extra_packages": extra_packages})
            return {"stdout": "", "stderr": "", "exit_code": 0, "success": True}

        with patch(
            "src.verification.sandbox_tool.sandbox_execute_silent",
            fake_base,
        ):
            wrapped = _make_probe_sandbox_execute(["pandas"])
            wrapped(code="import pandas as pd")

        assert calls[0]["extra_packages"] == ["pandas"]

    def test_llm_supplied_packages_union_with_artifact_packages(self):
        """When the LLM-supplied ``extra_packages`` differs from the
        artifact's ``pip_packages``, both sets are unioned (preserving
        order: artifact first, LLM additions second, dedup'd).
        """
        calls: list[dict] = []

        def fake_base(code, timeout=10, extra_packages=None, *, allow_network=False):
            calls.append({"extra_packages": extra_packages})
            return {"stdout": "", "stderr": "", "exit_code": 0, "success": True}

        with patch(
            "src.verification.sandbox_tool.sandbox_execute_silent",
            fake_base,
        ):
            wrapped = _make_probe_sandbox_execute(["pandas", "numpy"])
            # LLM adds a fake adversarial testing library
            wrapped(
                code="import pandas, numpy, fakelib",
                extra_packages=["fakelib"],
            )

        assert calls[0]["extra_packages"] == ["pandas", "numpy", "fakelib"]

    def test_duplicate_packages_are_deduplicated(self):
        """If the LLM happens to pass a package the artifact already
        requires, the wrapper dedups to avoid redundant ``uv pip install``
        arguments.
        """
        calls: list[dict] = []

        def fake_base(code, timeout=10, extra_packages=None, *, allow_network=False):
            calls.append({"extra_packages": extra_packages})
            return {"stdout": "", "stderr": "", "exit_code": 0, "success": True}

        with patch(
            "src.verification.sandbox_tool.sandbox_execute_silent",
            fake_base,
        ):
            wrapped = _make_probe_sandbox_execute(["pandas", "numpy"])
            wrapped(
                code="import pandas",
                extra_packages=["pandas", "openpyxl"],
            )

        # pandas appears once, numpy and openpyxl both present.
        assert calls[0]["extra_packages"] == ["pandas", "numpy", "openpyxl"]

    def test_baseline_packages_snapshot_protects_against_mutation(self):
        """The wrapper snapshots ``pip_packages`` at closure-creation time,
        so mutating the caller's list after the wrapper is created does not
        affect subsequent calls. Prevents a sneaky class of bugs where the
        caller's pip_packages list is a shared reference.
        """
        calls: list[dict] = []

        def fake_base(code, timeout=10, extra_packages=None, *, allow_network=False):
            calls.append({"extra_packages": extra_packages})
            return {"stdout": "", "stderr": "", "exit_code": 0, "success": True}

        caller_list = ["pandas"]
        with patch(
            "src.verification.sandbox_tool.sandbox_execute_silent",
            fake_base,
        ):
            wrapped = _make_probe_sandbox_execute(caller_list)
            # Caller mutates the list after wrapper is created
            caller_list.append("malicious-package")
            wrapped(code="import pandas")

        # The wrapper should still only install pandas, not the appended item.
        assert calls[0]["extra_packages"] == ["pandas"]

    def test_probe_threads_artifact_pip_packages_into_agent(self):
        """End-to-end: ``probe()`` reads ``artifact.pip_packages`` and
        passes them to ``_create_adversarial_agent`` so the wrapped
        sandbox_execute is used.
        """
        captured: dict = {}

        def fake_create(pip_packages=None, *, allow_network=False):
            captured["pip_packages"] = list(pip_packages or [])
            captured["allow_network"] = allow_network
            return _mock_agent_invoke(_clean_findings())

        artifact = GeneratedArtifact(
            capability_name="pandas-tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code="import pandas as pd\ndef run(): return pd.DataFrame()",
            pip_packages=["pandas", "openpyxl"],
        )

        with patch(
            "src.verification.adversarial._create_adversarial_agent",
            fake_create,
        ):
            report = probe(artifact)

        assert captured["pip_packages"] == ["pandas", "openpyxl"]
        # The probe still completes with clean findings.
        assert report.overall_passed is True
        assert report.tier_reached == 3

    def test_probe_handles_missing_pip_packages_field_gracefully(self):
        """Legacy / partial artifacts without ``pip_packages`` set must not
        crash. The probe falls back to the empty-list case (unwrapped
        sandbox_execute).
        """
        captured: dict = {}

        def fake_create(pip_packages=None, *, allow_network=False):
            captured["pip_packages"] = list(pip_packages or [])
            captured["allow_network"] = allow_network
            return _mock_agent_invoke(_clean_findings())

        artifact = GeneratedArtifact(
            capability_name="no-deps-tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code="def run(): return 42",
            # pip_packages not specified — default is empty list
        )

        with patch(
            "src.verification.adversarial._create_adversarial_agent",
            fake_create,
        ):
            probe(artifact)

        assert captured["pip_packages"] == []

    def test_create_adversarial_agent_none_packages_uses_base_tool(self):
        """Passing ``pip_packages=None`` (or omitting) must use the
        unwrapped sandbox_execute so pre-fix behavior is preserved for
        callers that haven't been updated.
        """
        # Use a dummy deep agent factory to capture the tools list passed in.
        captured_tools: dict = {}

        def fake_create_deep_agent(**kwargs):
            captured_tools["tools"] = kwargs.get("tools")
            return MagicMock()

        with patch("deepagents.create_deep_agent", fake_create_deep_agent):
            _create_adversarial_agent(pip_packages=None)

        from src.verification.sandbox_tool import sandbox_execute as base_tool

        assert captured_tools["tools"] == [base_tool]

    def test_create_adversarial_agent_empty_list_uses_base_tool(self):
        """Empty list is treated as None — unwrapped base tool used.
        Matches the ``if pip_packages:`` truthiness check in
        ``_create_adversarial_agent``.
        """
        captured_tools: dict = {}

        def fake_create_deep_agent(**kwargs):
            captured_tools["tools"] = kwargs.get("tools")
            return MagicMock()

        with patch("deepagents.create_deep_agent", fake_create_deep_agent):
            _create_adversarial_agent(pip_packages=[])

        from src.verification.sandbox_tool import sandbox_execute as base_tool

        assert captured_tools["tools"] == [base_tool]

    def test_create_adversarial_agent_with_packages_uses_wrapped_tool(self):
        """Non-empty list routes through ``_make_probe_sandbox_execute``.
        The wrapped tool is NOT the same object as base sandbox_execute.
        """
        captured_tools: dict = {}

        def fake_create_deep_agent(**kwargs):
            captured_tools["tools"] = kwargs.get("tools")
            return MagicMock()

        with patch("deepagents.create_deep_agent", fake_create_deep_agent):
            _create_adversarial_agent(pip_packages=["pandas"])

        from src.verification.sandbox_tool import sandbox_execute as base_tool

        tools = captured_tools["tools"]
        assert len(tools) == 1
        assert tools[0] is not base_tool  # it's a wrapped closure
        # And the closure's name is still sandbox_execute (so langchain's
        # tool introspection picks up the expected tool name).
        assert tools[0].__name__ == "sandbox_execute"


# ---------------------------------------------------------------------------
# Integration test (requires OPENAI_API_KEY + Docker)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_adversarial_probe_integration():
    """Full adversarial probe against a simple tool.

    Requires OPENAI_API_KEY and the jit-sandbox Docker image.
    (Adversarial agent runs DEFAULT_LLM_MODEL = openai:gpt-5-mini; the
    DEEPSEEK_API_KEY reference this test previously used was a pre-B1
    artifact — updated 2026-04-19 via Step 13.5 audit.)
    """
    import os

    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")

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
