"""Unit tests for src/registry/evaluation_store.py."""

from __future__ import annotations

import pytest

from src.registry.evaluation_store import EvaluationStore
from src.schemas.evaluation import EvaluationRecord

_BASE = dict(
    run_id="run_001",
    condition="full",
    task_id="T01",
    task_tier="trivial",
    attempt_count=1,
    gate_passed=True,
    output_in_range=True,
    token_cost=200,
    attempt_latency_ms=1500,
    failure_reason="none",
    registry_snapshot_id="snap_abc",
    seed=42,
)


def _record(**overrides) -> EvaluationRecord:
    return EvaluationRecord(**{**_BASE, **overrides})


@pytest.fixture
def store():
    with EvaluationStore() as s:
        yield s


class TestRecordAndRetrieve:
    def test_record_returns_id(self, store):
        row_id = store.record_run(_record())
        assert isinstance(row_id, str) and len(row_id) == 32

    def test_get_all_runs(self, store):
        store.record_run(_record(run_id="r1", task_id="T01"))
        store.record_run(_record(run_id="r1", task_id="T02"))
        rows = store.get_runs()
        assert len(rows) == 2

    def test_filter_by_condition(self, store):
        store.record_run(_record(condition="full", task_id="T01"))
        store.record_run(_record(condition="static", task_id="T02"))
        rows = store.get_runs(condition="full")
        assert len(rows) == 1
        assert rows[0].condition == "full"

    def test_filter_by_task_id(self, store):
        store.record_run(_record(task_id="T01"))
        store.record_run(_record(task_id="T02"))
        rows = store.get_runs(task_id="T01")
        assert len(rows) == 1

    def test_filter_by_is_pilot(self, store):
        store.record_run(_record(is_pilot=True))
        store.record_run(_record(is_pilot=False))
        pilot = store.get_runs(is_pilot=True)
        non_pilot = store.get_runs(is_pilot=False)
        assert len(pilot) == 1
        assert len(non_pilot) == 1

    def test_filter_by_run_id(self, store):
        store.record_run(_record(run_id="alpha"))
        store.record_run(_record(run_id="beta"))
        rows = store.get_runs(run_id="alpha")
        assert len(rows) == 1

    def test_roundtrip_preserves_fields(self, store):
        original = _record(
            capability_type="mcp_tool",
            verification_tier_reached=2,
            human_label="correct",
            invocation_record_id="inv_123",
        )
        store.record_run(original)
        rows = store.get_runs()
        r = rows[0]
        assert r.run_id == original.run_id
        assert r.condition == original.condition
        assert r.capability_type == "mcp_tool"
        assert r.verification_tier_reached == 2
        assert r.human_label == "correct"
        assert r.invocation_record_id == "inv_123"
        assert r.gate_passed is True
        assert r.output_in_range is True

    def test_compositional_fields_default_on_minimal_record(self, store):
        """Records constructed without compositional fields should round-trip
        with the documented Stage 1 defaults."""
        store.record_run(_record())
        r = store.get_runs()[0]
        assert r.chain_length == 1
        assert r.chain_success is False
        assert r.matched_and_executed_count == 0
        assert r.generated_artifact_count == 0
        assert r.capability_types == []
        assert r.failed_step_index is None
        assert r.failed_step_name is None

    def test_compositional_fields_roundtrip(self, store):
        """All Stage 1 compositional fields round-trip via DuckDB."""
        original = _record(
            capability_type="skill",
            chain_length=3,
            chain_success=False,
            matched_and_executed_count=2,
            generated_artifact_count=1,
            capability_types=["mcp_tool", "skill", "agent"],
            failed_step_index=2,
            failed_step_name="agent-final-step",
        )
        store.record_run(original)
        r = store.get_runs()[0]
        assert r.chain_length == 3
        assert r.chain_success is False
        assert r.matched_and_executed_count == 2
        assert r.generated_artifact_count == 1
        assert r.capability_types == ["mcp_tool", "skill", "agent"]
        assert r.failed_step_index == 2
        assert r.failed_step_name == "agent-final-step"


class TestSuccessRate:
    def test_all_succeed(self, store):
        for i in range(4):
            store.record_run(_record(task_id=f"T0{i+1}", gate_passed=True, output_in_range=True))
        assert store.compute_success_rate("full") == 1.0

    def test_none_succeed(self, store):
        store.record_run(_record(gate_passed=False, output_in_range=False,
                                 failure_reason="generation_failed"))
        assert store.compute_success_rate("full") == 0.0

    def test_partial_success(self, store):
        store.record_run(_record(task_id="T01", gate_passed=True, output_in_range=True))
        store.record_run(_record(task_id="T02", gate_passed=True, output_in_range=False,
                                 failure_reason="output_rejected"))
        assert store.compute_success_rate("full") == 0.5

    def test_pilot_excluded_by_default(self, store):
        store.record_run(_record(task_id="T01", gate_passed=True, output_in_range=True, is_pilot=False))
        store.record_run(_record(task_id="T02", gate_passed=False, output_in_range=False,
                                 failure_reason="generation_failed", is_pilot=True))
        # Only the non-pilot record counts
        assert store.compute_success_rate("full") == 1.0

    def test_filter_by_tier(self, store):
        store.record_run(_record(task_id="T01", task_tier="trivial",
                                 gate_passed=True, output_in_range=True))
        store.record_run(_record(task_id="T06", task_tier="moderate",
                                 gate_passed=False, output_in_range=False,
                                 failure_reason="generation_failed"))
        assert store.compute_success_rate("full", task_tier="trivial") == 1.0
        assert store.compute_success_rate("full", task_tier="moderate") == 0.0

    def test_empty_store_returns_zero(self, store):
        assert store.compute_success_rate("full") == 0.0


class TestRunCount:
    def test_counts_non_pilot_by_default(self, store):
        store.record_run(_record(is_pilot=True))
        store.record_run(_record(is_pilot=False))
        assert store.run_count() == 1
        assert store.run_count(exclude_pilot=False) == 2


class TestFailureBreakdown:
    def test_breakdown_groups_correctly(self, store):
        store.record_run(_record(failure_reason="none"))
        store.record_run(_record(failure_reason="tier1"))
        store.record_run(_record(failure_reason="tier1"))
        breakdown = store.failure_breakdown("full")
        assert breakdown["none"] == 1
        assert breakdown["tier1"] == 2

    def test_empty_returns_empty_dict(self, store):
        assert store.failure_breakdown("full") == {}
