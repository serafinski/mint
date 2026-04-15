"""Shared utilities for the capability generator nodes."""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

from src.schemas.task import TaskState
from src.schemas.verification import Severity

if TYPE_CHECKING:
    from src.schemas.task import ClassifiedGap, SubTask


def slugify_gap(query: str) -> str:
    """Derive a stable, collision-free snake_case name from a gap query string.

    Since the classifier now emits semantic capability names with a hash
    suffix already (e.g., ``xml-revenue-extractor-abc123``), this function
    primarily re-formats hyphens to underscores for Python identifier
    compatibility. A 6-character sha256 suffix is still appended to ensure
    collision safety if a caller passes a raw non-suffixed query.

    The previous implementation truncated to 40 chars BEFORE stripping
    trailing underscores, which could produce names with a trailing
    underscore followed by the hash separator (e.g.,
    ``parse_this_xml_report_data_and_find_the__6b415a`` with a double
    underscore). This version strips underscores AFTER truncation, so the
    output always has a single underscore before the suffix.

    Examples:
        "xml-revenue-extractor-abc123"  ->  "xml_revenue_extractor_abc123_<hash>"
        "fuzzy string matching"         ->  "fuzzy_string_matching_<hash>"
        "parse CSV file"                ->  "parse_csv_file_<hash>"
    """
    # Normalize non-alphanumerics to underscores, truncate, THEN strip
    # trailing underscores to avoid the double-underscore-before-suffix bug.
    base = re.sub(r"[^a-z0-9]+", "_", query.lower().strip())
    base = base[:40].strip("_") or "cap"
    suffix = hashlib.sha256(query.encode()).hexdigest()[:6]
    return f"{base}_{suffix}"


def slugify_gap_hyphen(query: str) -> str:
    """Like ``slugify_gap`` but returns hyphen-case (for skills and agents)."""
    return slugify_gap(query).replace("_", "-")


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
            if finding.severity in (Severity.CRITICAL, Severity.HIGH):
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


def find_subtask_for_gap(
    gap: ClassifiedGap,
    subtasks: list[SubTask],
) -> SubTask | None:
    """Return the subtask whose ``required_capabilities`` contains the gap query.

    Used by both :func:`build_schema_context` and :func:`is_first_step_for_gap`
    so the lookup lives in one place.
    """
    for st in subtasks:
        if gap.query in st.required_capabilities:
            return st
    return None


def is_first_step_for_gap(
    gap: ClassifiedGap,
    subtasks: list[SubTask],
) -> bool | None:
    """Return True if the gap's subtask has no upstream dependencies.

    Returns ``True`` for a step-0 (no ``depends_on``), ``False`` for a
    downstream step, and ``None`` when the gap cannot be matched to a
    subtask (in which case callers should skip position-specific directives
    and validators).
    """
    subtask = find_subtask_for_gap(gap, subtasks)
    if subtask is None:
        return None
    return len(subtask.depends_on) == 0


def build_schema_context(
    gap: ClassifiedGap,
    subtasks: list[SubTask],
) -> str:
    """Build an input/output schema contract string for a generator prompt.

    Finds the subtask whose ``required_capabilities`` contains the gap's query,
    then formats that subtask's ``output_schema`` and the ``output_schema`` of
    its upstream dependencies into a human-readable contract block.

    Returns an empty string if no matching subtask is found or if the subtask
    has no ``output_schema`` defined (backward-compatible with old decompositions).
    """
    target_subtask = find_subtask_for_gap(gap, subtasks)
    if target_subtask is None or not target_subtask.output_schema:
        return ""

    lines: list[str] = []

    # Input contract: upstream steps' output_schema
    if target_subtask.depends_on:
        input_parts: list[str] = []
        for dep_idx in target_subtask.depends_on:
            if 0 <= dep_idx < len(subtasks):
                dep = subtasks[dep_idx]
                if dep.output_schema:
                    for key, desc in dep.output_schema.items():
                        input_parts.append(f"  - \"{key}\": {desc}")
        if input_parts:
            lines.append("**Your step receives a dict from the previous step with these keys:**")
            lines.extend(input_parts)
            lines.append(
                "Use these exact key names to read the upstream data. "
                "Do NOT assume different key names."
            )
            lines.append("")

    # Output contract: this step's output_schema
    output_parts: list[str] = []
    for key, desc in target_subtask.output_schema.items():
        output_parts.append(f"  - \"{key}\": {desc}")
    if output_parts:
        lines.append("**Your step MUST return a dict with exactly these keys:**")
        lines.extend(output_parts)
        lines.append(
            "Do NOT use different key names. Downstream steps depend on these exact names."
        )

    return "\n".join(lines)
