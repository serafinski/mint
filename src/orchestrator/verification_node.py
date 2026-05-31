"""Verification node: run the three-tier gate on generated artifacts.

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
5. If attempts are exhausted and some artifacts still fail, the node
   fails closed: failed artifacts are dropped from
   ``generated_artifacts`` so they never register, and the sticky
   ``state.verification_succeeded`` flag is set to ``False``. Routing
   still proceeds to ``loading_node`` so any passed sibling artifacts
   can register normally and the pipeline reaches a terminal state.

**No-verification baseline:** ``gate_fn=None`` uses ``_stub_gate``, which
always returns a passing ``VerificationReport``.  The real ``gate.verify``
is wired as the default in ``pipeline.py``.
"""

from __future__ import annotations

import concurrent.futures
import inspect
import logging
from collections.abc import Callable
from typing import Literal

from langgraph.config import get_config as _get_langgraph_config
from langgraph.types import Command

from src.orchestrator.tracing import emit_langfuse_event, submit_with_context, trace_context
from src.schemas.task import GeneratedArtifact, TaskState, TaskStatus
from src.schemas.verification import Severity, VerificationFinding, VerificationReport

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
MAX_VERIFICATION_WORKERS = 4


# ---------------------------------------------------------------------------
# Stub gate for the no-verification baseline
# ---------------------------------------------------------------------------


def _stub_gate(artifact: GeneratedArtifact) -> VerificationReport:
    """Return a passing report without running the real verification gate.

    ``pipeline.py`` uses this when ``verification_enabled=False`` so the
    no-verification baseline can exercise the rest of the runtime without
    Tier 1-3 checks.
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
            ``VerificationReport``. Defaults to ``_stub_gate`` for the
            no-verification baseline.

    Returns:
        A ``Command`` that either:
        - routes to ``"loading_node"`` when all artifacts pass, or when
          attempts are exhausted and failed artifacts have been dropped
          fail-closed, or
        - routes back to ``"generate_mcp_tool"`` with only the passing artifacts
          in state so generators regenerate only the failed capabilities.
    """
    if gate_fn is None:
        gate_fn = _stub_gate

    # When the task allows network access, thread the same policy into
    # the real gate so Tier 2/Tier 3 probes match the execution context.
    # Custom gates may still expose a strict one-argument signature, so
    # only pass the kwarg when the callable accepts it.
    task_allow_network = bool(getattr(state, "allow_network", False))
    if task_allow_network:
        try:
            sig = inspect.signature(gate_fn)
            accepts_network = "allow_network" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in sig.parameters.values()
            )
        except (TypeError, ValueError):
            accepts_network = False
        if accepts_network:
            _original_gate_fn = gate_fn

            def _gate_fn_with_network(artifact):  # type: ignore[misc]
                return _original_gate_fn(artifact, allow_network=True)

            gate_fn = _gate_fn_with_network

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

    # Pass LangFuse metadata to the adversarial prober before worker
    # threads start, because LangGraph config is unavailable inside those
    # workers. Also capture the parent trace ID from the pipeline callback
    # so worker events can attach to the pipeline trace.
    try:
        from src.verification.adversarial import set_langfuse_metadata

        parent_config = _get_langgraph_config()
        parent_meta = (parent_config or {}).get("metadata", {})
        lf_meta = {
            k: v
            for k, v in parent_meta.items()
            if k.startswith("langfuse_") or k in ("task_id", "condition", "run_id", "tier")
        }
        set_langfuse_metadata(lf_meta)

        # LangChain wraps callbacks in a BaseCallbackManager during
        # LangGraph traversal, so handle both the user-provided list form
        # and manager objects exposing handler collections.
        if state.trace_id is None:
            _raw_callbacks = (parent_config or {}).get("callbacks")
            if _raw_callbacks is None:
                _handler_iter: list = []
            elif isinstance(_raw_callbacks, list):
                _handler_iter = _raw_callbacks
            elif hasattr(_raw_callbacks, "handlers"):
                _handler_iter = list(_raw_callbacks.handlers)
            elif hasattr(_raw_callbacks, "inheritable_handlers"):
                _handler_iter = list(_raw_callbacks.inheritable_handlers)
            else:
                _handler_iter = []
            for _cb in _handler_iter:
                _tid = getattr(_cb, "last_trace_id", None)
                if _tid:
                    state.trace_id = _tid
                    break
            if state.trace_id is None and _handler_iter:
                logger.debug(
                    "Found %d callback(s) but none had last_trace_id; "
                    "ThreadPool events will fall back to standalone-span path. "
                    "Handler types: %s",
                    len(_handler_iter),
                    [type(_cb).__name__ for _cb in _handler_iter],
                )
    except Exception:  # noqa: BLE001
        pass

    # On retry, skip artifacts that already have a passing verification
    # report and the same content hash. A regenerated artifact with the
    # same name but modified source must be verified again.
    from src.verification.gate import compute_artifact_hash

    already_passed_hash: dict[str, str] = {}
    for report in state.verification_results:
        if report.overall_passed:
            # Empty hash on older persisted rows forces re-verification.
            already_passed_hash[report.capability_name] = report.artifact_hash or ""

    def _is_already_passed(art: GeneratedArtifact) -> bool:
        prior_hash = already_passed_hash.get(art.capability_name)
        if not prior_hash:
            return False
        return compute_artifact_hash(art) == prior_hash

    artifacts_to_verify = [
        a for a in state.generated_artifacts
        if not _is_already_passed(a)
    ]
    # Artifacts with existing passing reports AND matching hash go straight
    # to passed list.
    for a in state.generated_artifacts:
        if _is_already_passed(a):
            passed_artifacts.append(a)

    logger.info(
        "verification_node: %d to verify, %d already passed (skipped)",
        len(artifacts_to_verify),
        len(state.generated_artifacts) - len(artifacts_to_verify),
    )

    # Run gate_fn in parallel — each call is independent (isolated Docker sandbox).
    # ``trace_context`` and ``submit_with_context`` keep LangFuse events
    # emitted by gate/adversarial/coverage workers attached to the parent
    # pipeline trace instead of relying on a shared global fallback.
    with trace_context(trace_id=state.trace_id), \
         concurrent.futures.ThreadPoolExecutor(max_workers=MAX_VERIFICATION_WORKERS) as pool:
        future_to_artifact = {
            submit_with_context(pool, gate_fn, artifact): artifact
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
        # Fail closed: drop failed artifacts before registration and set
        # the sticky verification_succeeded=False flag so terminal
        # callers classify the run as verification_failed. Still route to
        # loading_node so passed sibling artifacts register normally and
        # the pipeline reaches a terminal state.
        goto = "loading_node"
        artifacts_to_keep = passed_artifacts
        verification_succeeded = False
        logger.warning(
            "Verification failed for %d artifact(s) after %d attempt(s) — "
            "failing closed under the verified-capabilities contract. "
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
            "task classified as VERIFICATION_FAILED. "
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
