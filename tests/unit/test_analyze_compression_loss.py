"""Unit tests for scripts/analyze_compression_loss.py (Step 13.8)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.analyze_compression_loss import (  # noqa: E402
    FailureRow,
    _parse_first_bracketed_list,
    aggregate_by,
    classify_failure,
    extract_missing_keys,
    is_compression_loss,
    load_failures,
)
from src.registry.evaluation_store import EvaluationStore  # noqa: E402
from src.schemas.evaluation import EvaluationRecord  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(
    *,
    condition: str = "full",
    capability_type: str | None = "agent",
    failed_step_reason: str | None = None,
    output_shape_mismatch: bool = False,
    output_shape_missing_keys: list[str] | None = None,
    output_in_range: bool = False,
    gate_passed: bool = True,
    chain_success: bool = False,
    failure_reason: str = "chain_step_failed",
) -> FailureRow:
    return FailureRow(
        condition=condition,
        capability_type=capability_type,
        failed_step_reason=failed_step_reason,
        output_shape_mismatch=output_shape_mismatch,
        output_shape_missing_keys=output_shape_missing_keys or [],
        output_in_range=output_in_range,
        gate_passed=gate_passed,
        chain_success=chain_success,
        failure_reason=failure_reason,
    )


def _record(
    *,
    condition: str = "full",
    capability_type: str | None = "agent",
    failed_step_reason: str | None = None,
    output_shape_mismatch: bool = False,
    output_shape_missing_keys: list[str] | None = None,
    output_in_range: bool = False,
    gate_passed: bool = True,
    chain_success: bool = False,
    failure_reason: str = "chain_step_failed",
    task_id: str = "T04",
) -> EvaluationRecord:
    return EvaluationRecord(
        run_id=f"run-{task_id}",
        condition=condition,
        task_id=task_id,
        task_tier="moderate",
        attempt_count=1,
        capability_type=capability_type,
        gate_passed=gate_passed,
        output_in_range=output_in_range,
        token_cost=100,
        attempt_latency_ms=1000,
        failure_reason=failure_reason,
        registry_snapshot_id="snap-1",
        seed=42,
        chain_success=chain_success,
        failed_step_reason=failed_step_reason,
        output_shape_mismatch=output_shape_mismatch,
        output_shape_missing_keys=output_shape_missing_keys or [],
    )


# ---------------------------------------------------------------------------
# classify_failure priority rules
# ---------------------------------------------------------------------------


class TestClassifyFailure:
    def test_subtask_schema_violation_wins_over_exception_reason(self):
        # A missing-key error is technically a KeyError but the T3 framing
        # (subtask-level compression loss) must take priority.
        row = _row(
            failed_step_reason=(
                "agent output missing declared schema keys ['schema_sha'] "
                "(declared: ['parsed_rows', 'schema_sha'], got: ['parsed_rows'])"
            ),
            failure_reason="chain_step_failed",
        )
        assert classify_failure(row) == "compression_loss_subtask"

    def test_task_boundary_compression_loss_detected(self):
        row = _row(
            failed_step_reason=None,
            output_shape_mismatch=True,
            output_shape_missing_keys=["root_cause_metric"],
            failure_reason="output_rejected",
        )
        assert classify_failure(row) == "compression_loss_task"

    def test_subtask_wins_over_task_boundary(self):
        # Both signals present — subtask-level is the more precise category.
        row = _row(
            failed_step_reason="skill output missing declared schema keys ['x']",
            output_shape_mismatch=True,
            output_shape_missing_keys=["y"],
        )
        assert classify_failure(row) == "compression_loss_subtask"

    def test_agent_execution_timeout(self):
        row = _row(
            failed_step_reason="agent_execution_timeout",
            failure_reason="agent_execution_timeout",
        )
        assert classify_failure(row) == "agent_execution_timeout"

    def test_agent_execution_error(self):
        row = _row(
            failed_step_reason="agent_execution_error",
            failure_reason="agent_execution_error",
        )
        assert classify_failure(row) == "agent_execution_error"

    def test_wrong_value_when_chain_succeeded_but_validator_rejected(self):
        row = _row(
            chain_success=True,
            output_in_range=False,
            failure_reason="output_rejected",
            gate_passed=True,
        )
        assert classify_failure(row) == "wrong_value"

    def test_gate_rejection(self):
        row = _row(
            gate_passed=False,
            failure_reason="tier3",
        )
        assert classify_failure(row) == "gate_rejection"

    def test_generation_failed(self):
        row = _row(failure_reason="generation_failed", gate_passed=False)
        assert classify_failure(row) == "generation_failed"

    def test_unclassified_chain_step_failure_becomes_exception(self):
        # chain_step_failed with no distinguishing reason string — the
        # signal was lost upstream. Fallback category.
        row = _row(
            failed_step_reason=None,
            output_shape_mismatch=False,
            failure_reason="chain_step_failed",
        )
        assert classify_failure(row) == "exception"


# ---------------------------------------------------------------------------
# is_compression_loss helper
# ---------------------------------------------------------------------------


def test_is_compression_loss_covers_both_subtask_and_task():
    assert is_compression_loss("compression_loss_subtask") is True
    assert is_compression_loss("compression_loss_task") is True
    assert is_compression_loss("wrong_value") is False
    assert is_compression_loss("exception") is False


# ---------------------------------------------------------------------------
# extract_missing_keys
# ---------------------------------------------------------------------------


class TestExtractMissingKeys:
    def test_parses_first_bracketed_list_from_subtask_reason(self):
        reason = (
            "agent output missing declared schema keys ['schema_sha', 'row_count'] "
            "(declared: ['parsed_rows', 'schema_sha', 'row_count'], "
            "got: ['parsed_rows'])"
        )
        row = _row(failed_step_reason=reason)
        assert extract_missing_keys(row) == ["schema_sha", "row_count"]

    def test_falls_back_to_task_level_missing_keys(self):
        row = _row(
            failed_step_reason=None,
            output_shape_mismatch=True,
            output_shape_missing_keys=["root_cause_metric", "cascade_chain"],
        )
        assert extract_missing_keys(row) == ["root_cause_metric", "cascade_chain"]

    def test_empty_when_neither_source_set(self):
        row = _row()
        assert extract_missing_keys(row) == []

    def test_parser_handles_single_key(self):
        assert _parse_first_bracketed_list(
            "agent output missing declared schema keys ['only_one']"
        ) == ["only_one"]

    def test_parser_handles_empty_bracket(self):
        assert _parse_first_bracketed_list(
            "agent output missing declared schema keys [] (declared: [], got: [])"
        ) == []

    def test_parser_handles_double_quoted_keys(self):
        assert _parse_first_bracketed_list(
            'agent output missing declared schema keys ["k1", "k2"]'
        ) == ["k1", "k2"]


# ---------------------------------------------------------------------------
# aggregate_by
# ---------------------------------------------------------------------------


def test_aggregate_by_condition_counts_each_category():
    rows = [
        _row(condition="full", failed_step_reason="agent output missing declared schema keys ['x']"),
        _row(condition="full", output_shape_mismatch=True, output_shape_missing_keys=["y"]),
        _row(condition="no_verification", chain_success=True, output_in_range=False,
             failure_reason="output_rejected"),
        _row(condition="no_verification", failed_step_reason=None,
             failure_reason="chain_step_failed"),
    ]
    agg = aggregate_by(rows, key_fn=lambda r: r.condition)
    assert agg["full"]["compression_loss_subtask"] == 1
    assert agg["full"]["compression_loss_task"] == 1
    assert agg["no_verification"]["wrong_value"] == 1
    assert agg["no_verification"]["exception"] == 1


# ---------------------------------------------------------------------------
# End-to-end load_failures from a real DuckDB
# ---------------------------------------------------------------------------


def test_load_failures_round_trip_through_real_duckdb():
    """Seed a store, read it back, confirm failed_step_reason survives."""
    store = EvaluationStore()
    # Success row — should NOT appear in load_failures output.
    store.record_run(_record(
        gate_passed=True, output_in_range=True, chain_success=True,
        failure_reason="none", task_id="T01",
    ))
    # Subtask compression loss — should appear + classify correctly.
    store.record_run(_record(
        gate_passed=True, output_in_range=False, chain_success=False,
        failure_reason="chain_step_failed",
        failed_step_reason=(
            "agent output missing declared schema keys ['schema_sha']"
        ),
        task_id="T04",
    ))
    # Task-boundary compression loss.
    store.record_run(_record(
        gate_passed=True, output_in_range=False, chain_success=True,
        failure_reason="output_rejected",
        output_shape_mismatch=True,
        output_shape_missing_keys=["root_cause_metric"],
        task_id="T16",
    ))

    rows = load_failures(store._conn)  # noqa: SLF001 — test-only access
    assert len(rows) == 2, "success row must be excluded"

    categories = {classify_failure(r) for r in rows}
    assert categories == {"compression_loss_subtask", "compression_loss_task"}


def test_load_failures_filters_pilot_rows():
    store = EvaluationStore()
    # Pilot row — must NOT appear.
    store.record_run(EvaluationRecord(
        run_id="pilot", condition="full", task_id="T01", task_tier="moderate",
        attempt_count=1, capability_type="agent",
        gate_passed=False, output_in_range=False,
        token_cost=0, attempt_latency_ms=1000,
        failure_reason="chain_step_failed", registry_snapshot_id="s", seed=1,
        is_pilot=True,
        failed_step_reason="agent output missing declared schema keys ['x']",
    ))
    # Non-pilot row — must appear.
    store.record_run(_record(
        gate_passed=True, output_in_range=False,
        failed_step_reason="agent output missing declared schema keys ['y']",
    ))

    rows = load_failures(store._conn)  # noqa: SLF001
    assert len(rows) == 1


def test_load_failures_empty_db():
    store = EvaluationStore()
    rows = load_failures(store._conn)  # noqa: SLF001
    assert rows == []
