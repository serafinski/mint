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
    """Return the FIRST subtask whose ``required_capabilities`` contains the gap query.

    Used by both :func:`build_schema_context` and :func:`is_first_step_for_gap`
    so the lookup lives in one place.

    Security audit finding #101 (2026-04-19, ext10): when a capability
    is referenced by MULTIPLE subtasks (e.g. a CSV reader reused at
    step 0 AND step 3), this function returns only the FIRST match.
    Callers that need to reason about multi-position reuse should use
    :func:`find_all_subtasks_for_gap` and check for mixed-position
    cases (some matches with ``depends_on=[]``, others with non-empty
    deps). The kept first-only signature preserves back-compat for
    callers (``build_schema_context``) that only need a single
    representative context block.
    """
    for st in subtasks:
        if gap.query in st.required_capabilities:
            return st
    return None


def find_all_subtasks_for_gap(
    gap: ClassifiedGap,
    subtasks: list[SubTask],
) -> list[SubTask]:
    """Return ALL subtasks whose ``required_capabilities`` contains the gap query.

    Security audit finding #101 (2026-04-19, ext10): companion to
    :func:`find_subtask_for_gap` for callers that need to detect
    capability reuse across positions. Returns an empty list when no
    subtask matches.
    """
    return [st for st in subtasks if gap.query in st.required_capabilities]


def is_first_step_for_gap(
    gap: ClassifiedGap,
    subtasks: list[SubTask],
) -> bool | None:
    """Return True if the gap's subtask has no upstream dependencies.

    Returns ``True`` for a step-0 (no ``depends_on``), ``False`` for a
    downstream step, and ``None`` when the gap cannot be matched to a
    subtask (in which case callers should skip position-specific directives
    and validators).

    Security audit finding #101 (2026-04-19, ext10): when a capability
    is reused at MIXED positions (some subtasks have ``depends_on=[]``,
    others have non-empty deps), this function emits a WARNING log so
    the generator-prompt drift becomes visible — the resulting tool
    needs to handle BOTH first-step and downstream invocations, but the
    prompt steers toward whichever position the first match indicated.
    Returning the first-match's value is back-compat; the WARNING is
    the loud signal for future remediation.
    """
    matches = find_all_subtasks_for_gap(gap, subtasks)
    if not matches:
        return None
    positions = {len(st.depends_on) == 0 for st in matches}
    if len(positions) > 1:
        # Mixed-position reuse: the LLM-generated tool will only have
        # the shape appropriate for the FIRST match's position. Other
        # invocations may break. Loud-log so the drift is visible in
        # Phase 5 logs.
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(
            "Capability '%s' reused across mixed positions: %d subtask(s) "
            "as first-step (depends_on=[]) and %d subtask(s) as downstream. "
            "Generator prompt will produce a single signature; the unused "
            "position's invocation may fail. Security audit ext10 #101.",
            gap.query,
            sum(1 for st in matches if not st.depends_on),
            sum(1 for st in matches if st.depends_on),
        )
    return len(matches[0].depends_on) == 0


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

    # Input contract: upstream steps' output_schema.
    #
    # Security audit finding #102 (2026-04-19, ext10): when two
    # predecessors emit the SAME output-key (e.g., both produce
    # ``"count": int`` with different semantics), the pre-fix code
    # appended both verbatim to a flat list — the LLM saw
    # ``- "count": rows`` AND ``- "count": errors`` with no
    # provenance, and silently picked one or worse generated code
    # assuming both were available. Now: tag each key with its
    # source step (``from step N``) and emit an explicit
    # ``**COLLISION**`` marker when two predecessors produce the
    # same key, listing both descriptions so the LLM can disambiguate.
    # Mirrors the executor's last-write-wins merge order via the
    # depends_on iteration (also caught by
    # ``_detect_upstream_key_collisions`` at the decomposition layer
    # as a soft warning per ext6 #13).
    if target_subtask.depends_on:
        input_parts: list[str] = []
        seen_keys: dict[str, tuple[int, str]] = {}  # key → (first dep_idx, desc)
        collision_warnings: list[str] = []
        for dep_idx in target_subtask.depends_on:
            if 0 <= dep_idx < len(subtasks):
                dep = subtasks[dep_idx]
                if dep.output_schema:
                    for key, desc in dep.output_schema.items():
                        if key in seen_keys:
                            prev_idx, prev_desc = seen_keys[key]
                            input_parts.append(
                                f"  - \"{key}\" **COLLISION** "
                                f"(produced by both step {prev_idx} "
                                f"and step {dep_idx}): "
                                f"step {prev_idx} = {prev_desc} | "
                                f"step {dep_idx} = {desc}. "
                                "Last-write-wins at runtime — "
                                "step "
                                f"{dep_idx}'s value will overwrite "
                                f"step {prev_idx}'s. Use other keys to "
                                "disambiguate or rename one upstream."
                            )
                            collision_warnings.append(key)
                        else:
                            input_parts.append(
                                f"  - \"{key}\" (from step {dep_idx}): {desc}"
                            )
                            seen_keys[key] = (dep_idx, desc)
        if input_parts:
            lines.append("**Your step receives a dict from the previous step with these keys:**")
            lines.extend(input_parts)
            lines.append(
                "Use these exact key names to read the upstream data. "
                "Do NOT assume different key names."
            )
            if collision_warnings:
                lines.append(
                    f"**WARNING:** {len(collision_warnings)} upstream "
                    f"key collision(s) detected ({', '.join(sorted(set(collision_warnings)))}) "
                    "— see COLLISION markers above. The merged "
                    "``upstream_data`` dict will only carry the "
                    "last-written value per colliding key."
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
