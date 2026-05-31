"""Shared utilities for the capability generator nodes."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import TYPE_CHECKING

from src.schemas.task import TaskState
from src.schemas.verification import Severity

if TYPE_CHECKING:
    from src.schemas.task import ClassifiedGap, SubTask


def slugify_gap(query: str, contract_fingerprint: str | None = None) -> str:
    """Derive a stable, collision-free snake_case name from a gap query string.

    Classifier gap names may already include a semantic hash suffix
    (e.g., ``xml-revenue-extractor-abc123``), so this function primarily
    reformats hyphens to underscores for Python identifier compatibility.
    A 6-character sha256 suffix is still appended to keep raw or repeated
    queries collision-safe.

    When ``contract_fingerprint`` is provided, it is mixed into the suffix
    hash. This keeps the human-readable base stable while preventing
    semantically different subtasks with the same ``gap.query`` from
    claiming the same registry slot.

    Truncation happens before the trailing-underscore strip so the output
    always has a single underscore before the suffix.

    Examples:
        "xml-revenue-extractor-abc123"  ->  "xml_revenue_extractor_abc123_<hash>"
        "fuzzy string matching"         ->  "fuzzy_string_matching_<hash>"
        "parse CSV file"                ->  "parse_csv_file_<hash>"
    """
    # Normalize non-alphanumerics to underscores, truncate, THEN strip
    # trailing underscores to avoid the double-underscore-before-suffix bug.
    base = re.sub(r"[^a-z0-9]+", "_", query.lower().strip())
    base = base[:40].strip("_") or "cap"
    hash_input = query if contract_fingerprint is None else f"{query}\0{contract_fingerprint}"
    suffix = hashlib.sha256(hash_input.encode()).hexdigest()[:6]
    return f"{base}_{suffix}"


def slugify_gap_hyphen(query: str, contract_fingerprint: str | None = None) -> str:
    """Like ``slugify_gap`` but returns hyphen-case (for skills and agents)."""
    return slugify_gap(query, contract_fingerprint=contract_fingerprint).replace("_", "-")


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

    Used by :func:`build_schema_context`, which needs one representative
    context block for the prompt. Callers that need to reason about capability
    reuse across multiple subtasks should use :func:`find_all_subtasks_for_gap`.
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

    This companion to :func:`find_subtask_for_gap` supports callers that need
    to detect capability reuse across positions. Returns an empty list when no
    subtask matches.
    """
    return [st for st in subtasks if gap.query in st.required_capabilities]


def _collect_transitive_depends_on(
    target_subtask: SubTask,
    subtasks: list[SubTask],
) -> list[int]:
    """Return the transitive predecessor closure for ``target_subtask``.

    Mirrors the runtime contract in ``composition._collect_transitive_depends_on``:
    a downstream step may read keys produced by any ancestor reachable through
    ``depends_on``, not just its direct parents.

    The returned list preserves the existing breadth-first / declaration-order
    traversal so prompt collision markers stay aligned with runtime last-write-
    wins semantics as closely as possible.
    """
    visited: set[int] = set()
    queue: list[int] = list(target_subtask.depends_on)

    while queue:
        dep = queue.pop(0)
        if dep in visited:
            continue
        if not (0 <= dep < len(subtasks)):
            continue
        visited.add(dep)
        queue.extend(subtasks[dep].depends_on)

    return list(visited)


def _contract_fingerprint_for_subtasks(subtasks: list[SubTask]) -> str | None:
    """Return a stable hash of the subtask contract(s), or ``None`` if absent.

    The fingerprint deliberately excludes prose descriptions so legitimate
    reuse survives paraphrases. It captures only the execution-relevant
    contract: dependencies, upstream keys, required capabilities, and
    declared output schema.
    """
    if not subtasks:
        return None

    payload = [
        {
            "required_capabilities": list(st.required_capabilities),
            "depends_on": [int(dep) for dep in st.depends_on],
            "requires_upstream_keys": list(st.requires_upstream_keys),
            "output_schema": {
                key: st.output_schema[key]
                for key in sorted(st.output_schema)
            },
        }
        for st in subtasks
    ]
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def stable_capability_name_for_gap(
    gap: ClassifiedGap,
    subtasks: list[SubTask],
    *,
    hyphen_case: bool,
) -> str:
    """Return a deterministic capability name tied to the gap's subtask contract."""
    matches = find_all_subtasks_for_gap(gap, subtasks)
    contract_fingerprint = _contract_fingerprint_for_subtasks(matches)
    if hyphen_case:
        return slugify_gap_hyphen(gap.query, contract_fingerprint=contract_fingerprint)
    return slugify_gap(gap.query, contract_fingerprint=contract_fingerprint)


def stable_capability_name_for_gaps(
    gaps: list[ClassifiedGap],
    subtasks: list[SubTask],
    *,
    hyphen_case: bool,
) -> str:
    """Return a deterministic capability name for a multi-gap lead artifact."""
    if not gaps:
        base_query = "cap"
        contract_fingerprint = None
    else:
        base_query = gaps[0].query
        relevant_subtasks = [
            st for st in subtasks
            if any(g.query in st.required_capabilities for g in gaps)
        ]
        contract_fingerprint = _contract_fingerprint_for_subtasks(relevant_subtasks)

    if hyphen_case:
        return slugify_gap_hyphen(base_query, contract_fingerprint=contract_fingerprint)
    return slugify_gap(base_query, contract_fingerprint=contract_fingerprint)


def is_first_step_for_gap(
    gap: ClassifiedGap,
    subtasks: list[SubTask],
) -> bool | None:
    """Return True if the gap's subtask has no upstream dependencies.

    Returns ``True`` for a step-0 (no ``depends_on``), ``False`` for a
    downstream step, and ``None`` when the gap cannot be matched to a
    subtask (in which case callers should skip position-specific directives
    and validators).

    When a capability is reused at mixed positions (some subtasks have
    ``depends_on=[]`` and others have non-empty dependencies), this function
    logs a warning. The prompt still uses the first match's position, so the
    warning makes the potential signature mismatch visible during eval/debug
    runs.
    """
    matches = find_all_subtasks_for_gap(gap, subtasks)
    if not matches:
        return None
    positions = {len(st.depends_on) == 0 for st in matches}
    if len(positions) > 1:
        # Mixed-position reuse: the LLM-generated tool will only have
        # the shape appropriate for the FIRST match's position. Other
        # invocations may break, so log the drift where runners can see it.
        logger = logging.getLogger(__name__)
        logger.warning(
            "Capability '%s' reused across mixed positions: %d subtask(s) "
            "as first-step (depends_on=[]) and %d subtask(s) as downstream. "
            "Generator prompt will produce a single signature; the unused "
            "position's invocation may fail.",
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
    has no ``output_schema`` defined; generators then omit the schema section.
    """
    target_subtask = find_subtask_for_gap(gap, subtasks)
    if target_subtask is None or not target_subtask.output_schema:
        return ""

    lines: list[str] = []

    # Input contract: transitive upstream steps' output_schema.
    #
    # When two predecessors emit the same output key (e.g. both produce
    # ``"count": int`` with different semantics), tag each key with its
    # source step and emit an explicit ``**COLLISION**`` marker so the LLM can
    # disambiguate.
    # Mirrors the executor's last-write-wins merge order via the
    # depends_on iteration.
    transitive_depends_on = _collect_transitive_depends_on(target_subtask, subtasks)
    if transitive_depends_on:
        input_parts: list[str] = []
        seen_keys: dict[str, tuple[int, str]] = {}  # key → (first dep_idx, desc)
        collision_warnings: list[str] = []
        for dep_idx in transitive_depends_on:
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
