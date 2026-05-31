"""Unit tests for src/orchestrator/generators/utils.build_task_context.

Verifies that verification feedback is injected into task context on retry
(attempt_count > 0) and that the first attempt is unchanged.
"""

from __future__ import annotations

from src.orchestrator.generators.utils import build_task_context
from src.schemas.task import TaskState
from src.schemas.verification import Severity, VerificationFinding, VerificationReport

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _failing_report(
    name: str,
    severity: Severity = Severity.CRITICAL,
    message: str = "Mock data found",
) -> VerificationReport:
    return VerificationReport(
        capability_name=name,
        findings=[
            VerificationFinding(
                tier=1,
                category="mock_artifact",
                severity=severity,
                message=message,
            )
        ],
        overall_passed=False,
        tier_reached=1,
    )


def _passing_report(name: str) -> VerificationReport:
    return VerificationReport(
        capability_name=name,
        findings=[],
        overall_passed=True,
        tier_reached=3,
    )


def _state(
    attempt_count: int = 0,
    verification_results: list[VerificationReport] | None = None,
) -> TaskState:
    return TaskState(
        task_description="compute fibonacci number",
        attempt_count=attempt_count,
        verification_results=verification_results or [],
    )


# ---------------------------------------------------------------------------
# First attempt: no feedback injected
# ---------------------------------------------------------------------------


def test_first_attempt_returns_task_description_unchanged():
    state = _state(attempt_count=0)
    result = build_task_context(state)
    assert result == "compute fibonacci number"


def test_first_attempt_with_results_still_unchanged():
    """Even if verification_results is populated, attempt_count=0 means no feedback."""
    state = _state(attempt_count=0, verification_results=[_failing_report("fibonacci")])
    result = build_task_context(state)
    assert result == "compute fibonacci number"


# ---------------------------------------------------------------------------
# Retry: feedback injected
# ---------------------------------------------------------------------------


def test_retry_with_failed_report_injects_feedback():
    state = _state(attempt_count=1, verification_results=[_failing_report("fibonacci")])
    result = build_task_context(state)
    assert "compute fibonacci number" in result
    assert "fibonacci" in result
    assert "mock_artifact" in result
    assert "Mock data found" in result
    assert "PREVIOUS ATTEMPT FAILED VERIFICATION" in result


def test_retry_only_includes_critical_and_high():
    state = _state(
        attempt_count=1,
        verification_results=[
            VerificationReport(
                capability_name="tool",
                findings=[
                    VerificationFinding(
                        tier=1,
                        category="critical_cat",
                        severity=Severity.CRITICAL,
                        message="Critical issue",
                    ),
                    VerificationFinding(
                        tier=2, category="high_cat", severity=Severity.HIGH, message="High issue"
                    ),
                    VerificationFinding(
                        tier=2,
                        category="medium_cat",
                        severity=Severity.MEDIUM,
                        message="Medium issue",
                    ),
                    VerificationFinding(
                        tier=2, category="low_cat", severity=Severity.LOW, message="Low issue"
                    ),
                ],
                overall_passed=False,
                tier_reached=2,
            )
        ],
    )
    result = build_task_context(state)
    assert "Critical issue" in result
    assert "High issue" in result
    assert "Medium issue" not in result
    assert "Low issue" not in result


def test_retry_with_passing_reports_returns_unchanged():
    """If all reports passed, no feedback is appended even on retry."""
    state = _state(attempt_count=1, verification_results=[_passing_report("fibonacci")])
    result = build_task_context(state)
    assert result == "compute fibonacci number"


def test_retry_with_no_results_returns_unchanged():
    state = _state(attempt_count=2, verification_results=[])
    result = build_task_context(state)
    assert result == "compute fibonacci number"


def test_retry_multiple_failed_reports_all_included():
    state = _state(
        attempt_count=1,
        verification_results=[
            _failing_report("tool-a", severity=Severity.CRITICAL, message="Issue A"),
            _failing_report("tool-b", severity=Severity.HIGH, message="Issue B"),
        ],
    )
    result = build_task_context(state)
    assert "tool-a" in result
    assert "Issue A" in result
    assert "tool-b" in result
    assert "Issue B" in result


def test_retry_failed_report_with_only_low_findings_returns_unchanged():
    """If failed report has only LOW findings, no feedback lines are added."""
    state = _state(
        attempt_count=1,
        verification_results=[
            VerificationReport(
                capability_name="tool",
                findings=[
                    VerificationFinding(
                        tier=2, category="style", severity=Severity.LOW, message="Minor style issue"
                    )
                ],
                overall_passed=False,
                tier_reached=2,
            )
        ],
    )
    result = build_task_context(state)
    # overall_passed=False but only LOW severity, so no feedback lines are added.
    assert result == "compute fibonacci number"
