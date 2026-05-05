"""Tests for the DuckDB-backed evaluation store."""

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


class TestInitParentDirAutocreation:
    """Defensive mkdir: ``duckdb.connect(path)`` assumes parent exists."""

    def test_init_creates_parent_directory_if_missing(self, tmp_path):
        """``EvaluationStore(db_path=...)`` mkdirs missing parent dirs.

        Regression guard: eval runners instantiate
        ``EvaluationStore(db_path=args.db)`` on fresh data paths. If the
        parent dir doesn't exist, DuckDB raises FileNotFoundError. Defensive
        init closes the class of bug.
        """
        nested = tmp_path / "nested" / "sub" / "dir" / "eval.duckdb"
        assert not nested.parent.exists()
        with EvaluationStore(db_path=str(nested)) as s:
            s.record_run(_record())
        assert nested.exists()


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
        with the documented defaults."""
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
        """All compositional fields round-trip via DuckDB."""
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


class TestHumanLabelUpdate:
    def test_optimistic_concurrency_and_override(self, store):
        """Manual audit labels cannot be overwritten unless explicitly allowed."""
        row_id = store.record_run(_record())

        assert store.update_human_label(row_id, "correct") is True
        assert store.update_human_label(row_id, "partial") is False
        assert store.update_human_label(
            row_id,
            "partial",
            allow_overwrite=True,
        ) is True
        assert store.update_human_label("missing-row", "correct") is False


class TestSuccessRate:
    def test_all_succeed(self, store):
        for i in range(4):
            store.record_run(
                _record(task_id=f"T0{i+1}", gate_passed=True, output_in_range=True)
            )
        assert store.compute_success_rate("full") == 1.0

    def test_none_succeed(self, store):
        store.record_run(
            _record(
                gate_passed=False,
                output_in_range=False,
                failure_reason="generation_failed",
            )
        )
        assert store.compute_success_rate("full") == 0.0

    def test_partial_success(self, store):
        store.record_run(_record(task_id="T01", gate_passed=True, output_in_range=True))
        store.record_run(
            _record(
                task_id="T02",
                gate_passed=True,
                output_in_range=False,
                failure_reason="output_rejected",
            )
        )
        assert store.compute_success_rate("full") == 0.5

    def test_pilot_excluded_by_default(self, store):
        store.record_run(
            _record(
                task_id="T01",
                gate_passed=True,
                output_in_range=True,
                is_pilot=False,
            )
        )
        store.record_run(
            _record(
                task_id="T02",
                gate_passed=False,
                output_in_range=False,
                failure_reason="generation_failed",
                is_pilot=True,
            )
        )
        # Only the non-pilot record counts
        assert store.compute_success_rate("full") == 1.0

    def test_filter_by_tier(self, store):
        store.record_run(
            _record(
                task_id="T01",
                task_tier="trivial",
                gate_passed=True,
                output_in_range=True,
            )
        )
        store.record_run(
            _record(
                task_id="T06",
                task_tier="moderate",
                gate_passed=False,
                output_in_range=False,
                failure_reason="generation_failed",
            )
        )
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


class TestRecordInvocationEvents:
    """Persistent per-capability invocation log for Gini analysis."""

    def test_empty_events_is_zero_rows(self, store):
        n = store.record_invocation_events(
            run_id="r0", condition="full", task_id="T01", events=[],
        )
        assert n == 0
        rows = store._conn.execute(
            "SELECT COUNT(*) FROM invocation_events"
        ).fetchone()
        assert rows[0] == 0

    def test_record_single_event(self, store):
        n = store.record_invocation_events(
            run_id="r1",
            condition="full",
            task_id="T01",
            events=[{"capability_id": "csv-parser", "success": True}],
        )
        assert n == 1
        rows = store._conn.execute(
            "SELECT run_id, condition, task_id, capability_id, success, "
            "token_cost, latency_ms FROM invocation_events"
        ).fetchall()
        assert len(rows) == 1
        run_id, cond, task_id, cap_id, success, token_cost, latency_ms = rows[0]
        assert run_id == "r1"
        assert cond == "full"
        assert task_id == "T01"
        assert cap_id == "csv-parser"
        assert success is True
        assert token_cost == 0
        assert latency_ms == 0

    def test_record_multiple_events_with_fields(self, store):
        events = [
            {
                "capability_id": "cap-a",
                "capability_type": "mcp_tool",
                "success": True,
                "token_cost": 100,
                "latency_ms": 50,
            },
            {
                "capability_id": "cap-b",
                "capability_type": "skill",
                "success": False,
                "token_cost": 250,
                "latency_ms": 1200,
            },
        ]
        n = store.record_invocation_events(
            run_id="r2", condition="full", task_id="T04", events=events,
        )
        assert n == 2
        rows = store._conn.execute(
            "SELECT capability_id, capability_type, success, token_cost, latency_ms "
            "FROM invocation_events ORDER BY capability_id"
        ).fetchall()
        assert rows[0] == ("cap-a", "mcp_tool", True, 100, 50)
        assert rows[1] == ("cap-b", "skill", False, 250, 1200)

    def test_events_keyed_by_run_condition_task(self, store):
        # Same capability invoked across two conditions — rows distinguishable.
        for cond in ("full", "no_verification"):
            store.record_invocation_events(
                run_id="r3",
                condition=cond,
                task_id="T01",
                events=[{"capability_id": "csv-parser", "success": True}],
            )
        counts = store._conn.execute(
            "SELECT condition, COUNT(*) FROM invocation_events "
            "GROUP BY condition ORDER BY condition"
        ).fetchall()
        assert counts == [("full", 1), ("no_verification", 1)]

    def test_id_is_unique_per_row(self, store):
        events = [
            {"capability_id": "cap-a", "success": True},
            {"capability_id": "cap-a", "success": True},  # same cap twice
        ]
        store.record_invocation_events(
            run_id="r4", condition="full", task_id="T01", events=events,
        )
        ids = store._conn.execute(
            "SELECT id FROM invocation_events"
        ).fetchall()
        assert len(ids) == 2
        assert len({row[0] for row in ids}) == 2

    def test_persists_across_reopen(self, tmp_path):
        """Events written on one connection are readable on the next — proves
        the on-disk persistence the harness relies on."""
        db_path = str(tmp_path / "test.duckdb")
        with EvaluationStore(db_path=db_path) as s1:
            s1.record_invocation_events(
                run_id="r5",
                condition="full",
                task_id="T01",
                events=[{"capability_id": "csv-parser", "success": True}],
            )
        with EvaluationStore(db_path=db_path) as s2:
            rows = s2._conn.execute(
                "SELECT capability_id FROM invocation_events"
            ).fetchall()
            assert rows == [("csv-parser",)]
