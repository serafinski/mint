"""Unit tests for the verification node.

Tests cover:
- Stub gate always passes (default behaviour until Iteration 3)
- Correct routing to loading_node vs generate_mcp_tool
- Artifact replacement: failed artifacts removed from state on retry
- Fail-open behaviour at MAX_ATTEMPTS
- attempt_count incremented on every call
- Errors accumulated from Critical/High findings
- Existing verification_results preserved
- Status set to VERIFYING on every call
"""

from __future__ import annotations

from src.orchestrator.verification_node import MAX_ATTEMPTS, _stub_gate, verification_node
from src.schemas.capability import CapabilityType
from src.schemas.task import GeneratedArtifact, TaskState, TaskStatus
from src.schemas.verification import Severity, VerificationFinding, VerificationReport

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _artifact(name: str, cap_type: CapabilityType = CapabilityType.MCP_TOOL) -> GeneratedArtifact:
    return GeneratedArtifact(
        capability_name=name,
        capability_type=cap_type,
        source_code="async def tool(): pass",
    )


def _passing_report(name: str) -> VerificationReport:
    return VerificationReport(
        capability_name=name,
        findings=[],
        overall_passed=True,
        tier_reached=0,
    )


def _failing_report(name: str) -> VerificationReport:
    return VerificationReport(
        capability_name=name,
        findings=[
            VerificationFinding(
                tier=1,
                category="mock_artifact",
                severity=Severity.CRITICAL,
                message="Mock data detected",
            )
        ],
        overall_passed=False,
        tier_reached=1,
    )


def _gate_failing_for(*names: str):
    """Return a gate_fn that fails exactly the named artifacts."""

    def gate(artifact: GeneratedArtifact) -> VerificationReport:
        if artifact.capability_name in names:
            return _failing_report(artifact.capability_name)
        return _passing_report(artifact.capability_name)

    return gate


# ---------------------------------------------------------------------------
# Stub gate
# ---------------------------------------------------------------------------


def test_stub_gate_always_passes():
    a = _artifact("tool_a")
    report = _stub_gate(a)
    assert report.overall_passed is True
    assert report.findings == []
    assert report.tier_reached == 0
    assert report.capability_name == "tool_a"


# ---------------------------------------------------------------------------
# Default (stub) gate: all pass → loading_node
# ---------------------------------------------------------------------------


def test_default_gate_all_pass_routes_to_loading_node():
    state = TaskState(
        task_description="t",
        generated_artifacts=[_artifact("tool_a"), _artifact("tool_b")],
    )
    cmd = verification_node(state)

    assert cmd.goto == "loading_node"


def test_default_gate_all_artifacts_kept():
    state = TaskState(
        task_description="t",
        generated_artifacts=[_artifact("tool_a"), _artifact("tool_b")],
    )
    cmd = verification_node(state)

    names = [a.capability_name for a in cmd.update["generated_artifacts"]]
    assert "tool_a" in names
    assert "tool_b" in names


def test_no_artifacts_routes_to_loading_node():
    state = TaskState(task_description="t", generated_artifacts=[])
    cmd = verification_node(state)

    assert cmd.goto == "loading_node"
    assert cmd.update["generated_artifacts"] == []


# ---------------------------------------------------------------------------
# Custom gate: some fail → retry path
# ---------------------------------------------------------------------------


def test_some_fail_below_max_routes_to_generator():
    state = TaskState(
        task_description="t",
        generated_artifacts=[_artifact("good"), _artifact("bad")],
        attempt_count=0,
    )
    cmd = verification_node(state, gate_fn=_gate_failing_for("bad"))

    assert cmd.goto == "generate_mcp_tool"


def test_passed_artifacts_kept_on_retry():
    state = TaskState(
        task_description="t",
        generated_artifacts=[_artifact("good"), _artifact("bad")],
        attempt_count=0,
    )
    cmd = verification_node(state, gate_fn=_gate_failing_for("bad"))

    names = [a.capability_name for a in cmd.update["generated_artifacts"]]
    assert "good" in names
    assert "bad" not in names


def test_failed_artifacts_removed_on_retry():
    state = TaskState(
        task_description="t",
        generated_artifacts=[_artifact("a"), _artifact("b"), _artifact("c")],
        attempt_count=0,
    )
    cmd = verification_node(state, gate_fn=_gate_failing_for("a", "c"))

    names = [a.capability_name for a in cmd.update["generated_artifacts"]]
    assert names == ["b"]


# ---------------------------------------------------------------------------
# Max-attempts fail open
# ---------------------------------------------------------------------------


def test_max_attempts_routes_to_loading_node():
    """Terminal state on exhaustion: still routes to loading_node (never-raises contract),
    but the sticky verification_succeeded flag flips to False so downstream layers
    classify the run as VERIFICATION_FAILED (§12 finding #14).
    """
    state = TaskState(
        task_description="t",
        generated_artifacts=[_artifact("bad")],
        attempt_count=MAX_ATTEMPTS - 1,
    )
    cmd = verification_node(state, gate_fn=_gate_failing_for("bad"))

    assert cmd.goto == "loading_node"


def test_max_attempts_drops_failed_artifacts():
    """§12 finding #14: fail-closed — failed artifacts MUST be dropped so they
    never register in production, per the proposal's Pillar 2 contract
    ('Only verified capabilities enter production'). Passed siblings stay.
    """
    state = TaskState(
        task_description="t",
        generated_artifacts=[_artifact("good"), _artifact("bad")],
        attempt_count=MAX_ATTEMPTS - 1,
    )
    cmd = verification_node(state, gate_fn=_gate_failing_for("bad"))

    names = [a.capability_name for a in cmd.update["generated_artifacts"]]
    assert "good" in names
    assert "bad" not in names  # dropped on fail-closed


def test_max_attempts_sets_sticky_fail_flag():
    """§12 finding #14: sticky verification_succeeded flag flips to False
    so CLI runner + harness classify the task as VERIFICATION_FAILED.
    """
    state = TaskState(
        task_description="t",
        generated_artifacts=[_artifact("bad")],
        attempt_count=MAX_ATTEMPTS - 1,
    )
    cmd = verification_node(state, gate_fn=_gate_failing_for("bad"))

    assert cmd.update["verification_succeeded"] is False


def test_max_attempts_all_pass_keeps_sticky_flag_true():
    """Happy path at MAX_ATTEMPTS: all passed, verification_succeeded stays True,
    failed artifacts list is empty, all artifacts kept.
    """
    state = TaskState(
        task_description="t",
        generated_artifacts=[_artifact("good1"), _artifact("good2")],
        attempt_count=MAX_ATTEMPTS - 1,
    )

    def _pass_all(artifact):
        from src.schemas.verification import VerificationReport

        return VerificationReport(
            capability_name=artifact.capability_name,
            findings=[],
            overall_passed=True,
            tier_reached=3,
        )

    cmd = verification_node(state, gate_fn=_pass_all)

    assert cmd.goto == "loading_node"
    names = [a.capability_name for a in cmd.update["generated_artifacts"]]
    assert set(names) == {"good1", "good2"}
    assert cmd.update["verification_succeeded"] is True


def test_max_attempts_records_fail_closed_error():
    """Errors list should carry a VERIFICATION_FAILED marker that the CLI
    runner and eval harness can surface.
    """
    state = TaskState(
        task_description="t",
        generated_artifacts=[_artifact("bad")],
        attempt_count=MAX_ATTEMPTS - 1,
    )
    cmd = verification_node(state, gate_fn=_gate_failing_for("bad"))

    assert any(
        "VERIFICATION_FAILED" in e or "Pillar 2 contract" in e
        for e in cmd.update["errors"]
    )


def test_retry_below_max_does_not_flip_sticky_flag():
    """Below MAX_ATTEMPTS: retry path, sticky flag stays True until exhaustion."""
    state = TaskState(
        task_description="t",
        generated_artifacts=[_artifact("bad")],
        attempt_count=0,  # first failure, retries remain
    )
    cmd = verification_node(state, gate_fn=_gate_failing_for("bad"))

    assert cmd.goto == "generate_mcp_tool"
    # verification_succeeded is NOT flipped mid-retry; only on exhaustion.
    assert cmd.update["verification_succeeded"] is True


# ---------------------------------------------------------------------------
# attempt_count incremented
# ---------------------------------------------------------------------------


def test_attempt_count_incremented_from_zero():
    state = TaskState(task_description="t")
    cmd = verification_node(state)

    assert cmd.update["attempt_count"] == 1


def test_attempt_count_incremented_from_two():
    state = TaskState(task_description="t", attempt_count=2)
    cmd = verification_node(state)

    assert cmd.update["attempt_count"] == 3


# ---------------------------------------------------------------------------
# Errors from findings
# ---------------------------------------------------------------------------


def test_critical_finding_added_to_errors():
    state = TaskState(
        task_description="t",
        generated_artifacts=[_artifact("bad")],
    )
    cmd = verification_node(state, gate_fn=_gate_failing_for("bad"))

    assert any("bad" in e for e in cmd.update["errors"])


def test_high_finding_added_to_errors():
    def high_gate(artifact):
        return VerificationReport(
            capability_name=artifact.capability_name,
            findings=[
                VerificationFinding(
                    tier=2,
                    category="consistency",
                    severity=Severity.HIGH,
                    message="Non-deterministic output",
                )
            ],
            overall_passed=False,
            tier_reached=2,
        )

    state = TaskState(
        task_description="t",
        generated_artifacts=[_artifact("flaky")],
        attempt_count=0,
    )
    cmd = verification_node(state, gate_fn=high_gate)

    assert any("flaky" in e for e in cmd.update["errors"])


def test_low_finding_not_added_to_errors():
    def low_gate(artifact):
        return VerificationReport(
            capability_name=artifact.capability_name,
            findings=[
                VerificationFinding(
                    tier=1,
                    category="style",
                    severity=Severity.LOW,
                    message="Minor style issue",
                )
            ],
            overall_passed=True,  # LOW finding doesn't fail the report
            tier_reached=1,
        )

    state = TaskState(task_description="t", generated_artifacts=[_artifact("ok")])
    cmd = verification_node(state, gate_fn=low_gate)

    # No errors added for LOW severity
    assert cmd.update["errors"] == []


def test_existing_errors_preserved():
    state = TaskState(
        task_description="t",
        errors=["prior error"],
        generated_artifacts=[],
    )
    cmd = verification_node(state)

    assert "prior error" in cmd.update["errors"]


# ---------------------------------------------------------------------------
# Verification results accumulated
# ---------------------------------------------------------------------------


def test_verification_results_appended():
    state = TaskState(
        task_description="t",
        generated_artifacts=[_artifact("a"), _artifact("b")],
    )
    cmd = verification_node(state)

    assert len(cmd.update["verification_results"]) == 2
    names = [r.capability_name for r in cmd.update["verification_results"]]
    assert "a" in names
    assert "b" in names


def test_existing_verification_results_preserved():
    prior = _passing_report("prior")
    state = TaskState(
        task_description="t",
        verification_results=[prior],
        generated_artifacts=[_artifact("new")],
    )
    cmd = verification_node(state)

    assert len(cmd.update["verification_results"]) == 2
    assert cmd.update["verification_results"][0].capability_name == "prior"
    assert cmd.update["verification_results"][1].capability_name == "new"


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_status_is_verifying():
    state = TaskState(task_description="t")
    cmd = verification_node(state)

    assert cmd.update["status"] == TaskStatus.VERIFYING


# ---------------------------------------------------------------------------
# Fix 6: Parallel verification
# ---------------------------------------------------------------------------


def test_parallel_verification_processes_all_artifacts():
    """All 4 artifacts are verified and end up in passed_artifacts."""
    state = TaskState(
        task_description="t",
        generated_artifacts=[_artifact(f"tool_{i}") for i in range(4)],
    )

    cmd = verification_node(state)  # stub gate passes all

    assert len(cmd.update["generated_artifacts"]) == 4
    assert cmd.goto == "loading_node"


def test_gate_exception_treated_as_failure_not_pass():
    """Gate exception must result in failure, not a silent pass."""
    def exploding_gate(artifact):
        if artifact.capability_name == "boom":
            raise RuntimeError("sandbox crashed")
        return _passing_report(artifact.capability_name)

    state = TaskState(
        task_description="t",
        generated_artifacts=[_artifact("ok"), _artifact("boom")],
        attempt_count=0,
    )
    cmd = verification_node(state, gate_fn=exploding_gate)

    # "boom" should be failed, "ok" should pass → retry
    assert cmd.goto == "generate_mcp_tool"
    names = [a.capability_name for a in cmd.update["generated_artifacts"]]
    assert "ok" in names
    assert "boom" not in names
    assert any("gate_error" in e or "sandbox crashed" in e for e in cmd.update["errors"])


def test_parallel_preserves_prior_verification_results():
    """Prior reports in state.verification_results are not clobbered."""
    prior = _passing_report("prior_tool")
    state = TaskState(
        task_description="t",
        verification_results=[prior],
        generated_artifacts=[_artifact("new_tool")],
    )

    cmd = verification_node(state)

    results = cmd.update["verification_results"]
    assert results[0].capability_name == "prior_tool"
    assert any(r.capability_name == "new_tool" for r in results)
