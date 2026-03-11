"""Verification node — runs the three-tier gate on every generated artifact.

This node sits between the generator chain and ``loading_node``:

    generate_agent → verification_node → loading_node  (pass or max attempts)
                               ↑                 |
                               └── generate_mcp_tool  (retry on failure)

On each call the node:

1. Runs ``gate_fn`` against every artifact in ``state.generated_artifacts``.
2. Increments ``attempt_count``.
3. If all pass (or attempts are exhausted): routes to ``loading_node``.
   Exhausted retries fail open — the pipeline always reaches a terminal state.
4. If some fail and retries remain: removes the failed artifacts from state
   and routes back to ``generate_mcp_tool``.  The generators re-run all
   classified gaps; the replace-by-name logic in each generator ensures that
   re-generated artifacts overwrite the old versions rather than accumulating.

**Stub gate (Iteration 2 default):** ``gate_fn=None`` uses ``_stub_gate``,
which always returns a passing ``VerificationReport``.  In Iteration 3 this
parameter will be replaced with the real ``gate.verify`` function.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Literal

from langgraph.types import Command

from src.orchestrator.tracing import emit_langfuse_event
from src.schemas.capability import CapabilityType  # noqa: F401 — available for gate impls
from src.schemas.task import GeneratedArtifact, TaskState, TaskStatus
from src.schemas.verification import Severity, VerificationReport

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# Stub gate (Iteration 2 placeholder — always passes)
# ---------------------------------------------------------------------------


def _stub_gate(artifact: GeneratedArtifact) -> VerificationReport:
    """Stub verification gate: every artifact passes.

    Replaced by the real tiered gate (``src/verification/gate.py``) in
    Iteration 3.  Keeps the pipeline runnable end-to-end while the gate is
    being developed.
    """
    return VerificationReport(
        capability_name=artifact.capability_name,
        findings=[],
        overall_passed=True,
        tier_reached=0,
    )


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------


def verification_node(
    state: TaskState,
    gate_fn: Callable[[GeneratedArtifact], VerificationReport] | None = None,
) -> Command[Literal["loading_node", "generate_mcp_tool"]]:
    """Verify all generated artifacts and route accordingly.

    Args:
        state: Current pipeline state.
        gate_fn: Callable that takes a ``GeneratedArtifact`` and returns a
            ``VerificationReport``.  Defaults to ``_stub_gate`` (always passes)
            until the real gate is available in Iteration 3.

    Returns:
        A ``Command`` that either:
        - routes to ``"loading_node"`` when all artifacts pass or attempts are
          exhausted (fail open), or
        - routes back to ``"generate_mcp_tool"`` with only the passing artifacts
          in state so generators regenerate only the failed capabilities.
    """
    if gate_fn is None:
        gate_fn = _stub_gate

    new_attempt_count = state.attempt_count + 1
    errors: list[str] = list(state.errors)

    passed_artifacts: list[GeneratedArtifact] = []
    failed_artifacts: list[GeneratedArtifact] = []
    new_reports: list[VerificationReport] = []

    logger.info(
        "verification_node: verifying %d artifact(s) (attempt %d)",
        len(state.generated_artifacts),
        new_attempt_count,
    )
    for artifact in state.generated_artifacts:
        logger.info("  → verifying: %s (%s)", artifact.capability_name, artifact.capability_type)
        report = gate_fn(artifact)
        new_reports.append(report)
        if report.overall_passed:
            passed_artifacts.append(artifact)
        else:
            failed_artifacts.append(artifact)
            # Surface Critical and High findings as pipeline errors so they
            # appear in state.errors and are visible to downstream nodes.
            for finding in report.findings:
                if finding.severity in (Severity.CRITICAL, Severity.HIGH):
                    errors.append(
                        f"[{artifact.capability_name}] {finding.category}: {finding.message}"
                    )

    all_passed = len(failed_artifacts) == 0

    if all_passed or new_attempt_count >= MAX_ATTEMPTS:
        goto: str = "loading_node"
        # Fail open: keep all artifacts including failures when out of retries.
        artifacts_to_keep = list(state.generated_artifacts)
        if not all_passed:
            logger.warning(
                "Verification failed for %d artifact(s) after %d attempt(s) — failing open: %s",
                len(failed_artifacts),
                new_attempt_count,
                [a.capability_name for a in failed_artifacts],
            )
            emit_langfuse_event(
                state.trace_id,
                "verification.fail_open",
                "WARNING",
                {
                    "attempt_count": new_attempt_count,
                    "failed": [a.capability_name for a in failed_artifacts],
                },
            )
            errors.append(
                f"Verification failed after {new_attempt_count} attempt(s), failing open: "
                + ", ".join(a.capability_name for a in failed_artifacts)
            )
        else:
            logger.info(
                "All %d artifact(s) passed verification (attempt %d)",
                len(passed_artifacts),
                new_attempt_count,
            )
    else:
        # Some artifacts failed; retry with only the passing ones.
        # Generators re-run all classified_gaps but replace by capability_name,
        # so passed artifacts get fresh regeneration while failed ones are
        # replaced by new attempts.
        goto = "generate_mcp_tool"
        artifacts_to_keep = passed_artifacts
        logger.info(
            "Verification attempt %d: %d passed, %d failed — routing back for retry",
            new_attempt_count,
            len(passed_artifacts),
            len(failed_artifacts),
        )

    return Command(
        update={
            "generated_artifacts": artifacts_to_keep,
            "verification_results": list(state.verification_results) + new_reports,
            "attempt_count": new_attempt_count,
            "status": TaskStatus.VERIFYING,
            "errors": errors,
        },
        goto=goto,
    )
