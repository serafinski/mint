"""Verification node — runs the three-tier gate on every generated artifact.

This node sits between the generator chain and ``loading_node``:

    generate_agent → verification_node → loading_node  (pass or max attempts)
                               ↑                 |
                               └── generate_mcp_tool  (retry on failure)

On each call the node:

1. Runs ``gate_fn`` against every artifact in ``state.generated_artifacts``.
2. Increments ``attempt_count``.
3. If all pass: routes to ``loading_node`` with every artifact kept.
4. If some fail and retries remain (``attempt_count < MAX_ATTEMPTS``):
   removes the failed artifacts from state and routes back to
   ``generate_mcp_tool``.  The generators re-run all classified gaps; the
   replace-by-name logic in each generator ensures that re-generated
   artifacts overwrite the old versions rather than accumulating.
5. If attempts are exhausted AND some artifacts still fail (**§12 finding
   #14, 2026-04-16**): the node FAILS CLOSED per the proposal's Pillar 2
   contract (*"Only verified capabilities enter production"*).
   Concretely: failed artifacts are DROPPED from ``generated_artifacts``
   so they never register; the sticky flag
   ``state.verification_succeeded`` is set to ``False``; routing still
   proceeds to ``loading_node`` (which registers only the remaining
   passed artifacts) and then through the rest of the pipeline, but the
   CLI runner and evaluation harness both detect the sticky flag at
   terminal state and classify the run as VERIFICATION_FAILED with a
   clear error. This replaces the pre-2026-04-16 fail-open behavior
   (unverified artifacts kept + executed with warnings), which was a
   latent drift from the proposal — see master plan §12 finding #14 for
   the full audit trail.

**No-verification baseline:** ``gate_fn=None`` uses ``_stub_gate``, which
always returns a passing ``VerificationReport``.  The real ``gate.verify``
is wired as the default in ``pipeline.py``.
"""

from __future__ import annotations

import concurrent.futures
import logging
from collections.abc import Callable
from typing import Literal

from langgraph.types import Command

from src.orchestrator.tracing import emit_langfuse_event
from src.schemas.capability import CapabilityType  # noqa: F401 — available for gate impls
from src.schemas.task import GeneratedArtifact, TaskState, TaskStatus
from src.schemas.verification import Severity, VerificationFinding, VerificationReport

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
MAX_VERIFICATION_WORKERS = 4


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

    # Pass LangFuse metadata to adversarial prober (runs in worker threads
    # where get_config() is unavailable). Must be set before the pool starts.
    try:
        from langgraph.config import get_config as _get_langgraph_config
        from src.verification.adversarial import set_langfuse_metadata

        parent_config = _get_langgraph_config()
        parent_meta = (parent_config or {}).get("metadata", {})
        lf_meta = {
            k: v for k, v in parent_meta.items()
            if k.startswith("langfuse_") or k in ("task_id", "condition", "run_id", "tier")
        }
        set_langfuse_metadata(lf_meta)
    except Exception:  # noqa: BLE001
        pass

    # On retry, skip artifacts that already have a passing verification
    # report — they haven't changed and re-verifying them wastes Tier 2/3 cost.
    already_passed: set[str] = set()
    for report in state.verification_results:
        if report.overall_passed:
            already_passed.add(report.capability_name)

    artifacts_to_verify = [
        a for a in state.generated_artifacts
        if a.capability_name not in already_passed
    ]
    # Artifacts with existing passing reports go straight to passed list.
    for a in state.generated_artifacts:
        if a.capability_name in already_passed:
            passed_artifacts.append(a)

    logger.info(
        "verification_node: %d to verify, %d already passed (skipped)",
        len(artifacts_to_verify),
        len(state.generated_artifacts) - len(artifacts_to_verify),
    )

    # Run gate_fn in parallel — each call is independent (isolated Docker sandbox).
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_VERIFICATION_WORKERS) as pool:
        future_to_artifact = {
            pool.submit(gate_fn, artifact): artifact
            for artifact in artifacts_to_verify
        }
        for future in concurrent.futures.as_completed(future_to_artifact):
            artifact = future_to_artifact[future]
            try:
                report = future.result()
            except Exception as exc:
                logger.error(
                    "gate_fn raised unexpectedly for '%s': %s — treating as "
                    "verification failure (fail-safe)",
                    artifact.capability_name, exc,
                )
                report = VerificationReport(
                    capability_name=artifact.capability_name,
                    findings=[VerificationFinding(
                        tier=1,
                        category="gate_error",
                        severity=Severity.HIGH,
                        message=f"Verification gate raised exception: {exc}",
                    )],
                    overall_passed=False,
                    tier_reached=0,
                )
            new_reports.append(report)
            if report.overall_passed:
                passed_artifacts.append(artifact)
            else:
                failed_artifacts.append(artifact)
                for finding in report.findings:
                    if finding.severity in (Severity.CRITICAL, Severity.HIGH):
                        errors.append(
                            f"[{artifact.capability_name}] {finding.category}: {finding.message}"
                        )

    all_passed = len(failed_artifacts) == 0
    verification_succeeded = state.verification_succeeded

    if all_passed:
        # Happy path — every artifact passed. Route to loading_node with
        # all artifacts preserved.
        goto: str = "loading_node"
        artifacts_to_keep = list(state.generated_artifacts)
        logger.info(
            "All %d artifact(s) passed verification (attempt %d)",
            len(passed_artifacts),
            new_attempt_count,
        )
    elif new_attempt_count >= MAX_ATTEMPTS:
        # §12 finding #14 (2026-04-16): FAIL CLOSED per the proposal's
        # Pillar 2 contract. Drop the failed artifacts (they never
        # register) and set the sticky verification_succeeded=False flag
        # so the CLI runner / evaluation harness classify the run as
        # verification_failed. Still route to loading_node so any
        # passed-sibling artifacts register normally, and so the pipeline
        # reaches a terminal state (never-raises contract preserved).
        goto = "loading_node"
        artifacts_to_keep = passed_artifacts
        verification_succeeded = False
        logger.warning(
            "Verification failed for %d artifact(s) after %d attempt(s) — "
            "failing CLOSED per proposal Pillar 2 (§12 finding #14). "
            "Dropping failed artifacts: %s",
            len(failed_artifacts),
            new_attempt_count,
            [a.capability_name for a in failed_artifacts],
        )
        emit_langfuse_event(
            state.trace_id,
            "verification.fail_closed",
            "ERROR",
            {
                "attempt_count": new_attempt_count,
                "failed": [a.capability_name for a in failed_artifacts],
            },
        )
        errors.append(
            f"Verification failed after {new_attempt_count} attempt(s); "
            "task classified as VERIFICATION_FAILED per Pillar 2 contract. "
            "Failed artifacts dropped from registry: "
            + ", ".join(a.capability_name for a in failed_artifacts)
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
            "verification_succeeded": verification_succeeded,
            "errors": errors,
        },
        goto=goto,
    )
