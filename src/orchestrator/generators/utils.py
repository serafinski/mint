"""Shared utilities for the capability generator nodes."""

from __future__ import annotations

from src.schemas.task import TaskState


def build_task_context(state: TaskState) -> str:
    """Build task_context for a generator, appending verification failure feedback on retry.

    On the first attempt (``attempt_count == 0``) returns ``state.task_description``
    unchanged.  On subsequent attempts, CRITICAL and HIGH findings from previously
    failed ``VerificationReport``s are appended so the LLM knows what to fix.
    """
    if state.attempt_count == 0:
        return state.task_description

    failed_reports = [r for r in state.verification_results if not r.overall_passed]
    if not failed_reports:
        return state.task_description

    feedback_lines: list[str] = []
    for report in failed_reports:
        for finding in report.findings:
            # Severity is a StrEnum — direct string comparison works
            if finding.severity in ("critical", "high"):
                feedback_lines.append(
                    f"- [{report.capability_name}] {finding.category}"
                    f" ({finding.severity}): {finding.message}"
                )

    if not feedback_lines:
        return state.task_description

    return (
        state.task_description
        + "\n\nPREVIOUS ATTEMPT FAILED VERIFICATION — Fix these issues in the regenerated code:\n"
        + "\n".join(feedback_lines)
        + "\n\nDo NOT use mock data, hardcoded return values, placeholders, or TODO stubs."
    )
