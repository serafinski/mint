"""Unit tests for scripts/analyze_longitudinal.py helpers (Step 18.7)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.analyze_longitudinal import (  # noqa: E402
    _classify_adaptation,
    _detect_convergence,
    _detect_shift_point,
    _kl_divergence_between_snapshots,
    _load_cycle_data,
    _table_exists,
)
from src.registry.evaluation_store import EvaluationStore  # noqa: E402
from src.schemas.evaluation import EvaluationRecord  # noqa: E402


# ---------------------------------------------------------------------------
# _kl_divergence_between_snapshots
# ---------------------------------------------------------------------------


class TestKLDivergenceBetweenSnapshots:
    def test_both_empty_returns_zero(self):
        assert _kl_divergence_between_snapshots({}, {}) == 0.0

    def test_prev_empty_curr_populated_returns_zero(self):
        # First-cycle sentinel — we're seeing the first real snapshot, so
        # KL against nothing is 0.0 by convention.
        assert _kl_divergence_between_snapshots({}, {"a": 1.0}) == 0.0

    def test_prev_populated_curr_empty_returns_inf(self):
        # Catastrophic — all caps disappeared between snapshots.
        assert _kl_divergence_between_snapshots({"a": 1.0}, {}) == float("inf")

    def test_identical_distributions_near_zero(self):
        kl = _kl_divergence_between_snapshots(
            {"a": 0.5, "b": 0.3, "c": 0.2},
            {"a": 0.5, "b": 0.3, "c": 0.2},
        )
        # epsilon-smoothing means it's not exactly 0, but very small
        assert 0.0 <= kl < 0.001

    def test_different_distributions_non_trivial(self):
        # Opposite distributions → higher KL than near-identical
        kl_similar = _kl_divergence_between_snapshots(
            {"a": 0.5, "b": 0.5}, {"a": 0.55, "b": 0.45},
        )
        kl_different = _kl_divergence_between_snapshots(
            {"a": 0.9, "b": 0.1}, {"a": 0.1, "b": 0.9},
        )
        assert kl_different > kl_similar
        assert kl_different > 0.5

    def test_negative_utility_scores_handled(self):
        # Utility scores can be negative (U = α·reuse·success − β·cost − γ·maint).
        # The analyzer must shift to non-negative + normalize; it should not raise.
        kl = _kl_divergence_between_snapshots(
            {"a": -0.5, "b": 0.3, "c": 0.7},
            {"a": -0.3, "b": 0.4, "c": 0.6},
        )
        assert np.isfinite(kl)
        assert kl >= 0.0

    def test_union_of_keys_handled(self):
        # New cap appears in curr but not in prev — should still compute KL
        kl = _kl_divergence_between_snapshots(
            {"a": 0.5, "b": 0.5},
            {"a": 0.4, "b": 0.4, "c": 0.2},  # c is new
        )
        assert np.isfinite(kl)
        assert kl > 0.0

    def test_converged_trajectory_produces_small_kl(self):
        # After convergence, consecutive snapshots should have near-zero KL
        stable = {f"cap-{i}": 1.0 / (i + 1) for i in range(10)}
        kl = _kl_divergence_between_snapshots(stable, stable)
        assert kl < 0.001


# ---------------------------------------------------------------------------
# _detect_convergence
# ---------------------------------------------------------------------------


class TestDetectConvergence:
    def test_never_converged_returns_none(self):
        kls = [0.5, 0.4, 0.3, 0.2]  # all above threshold 0.01
        assert _detect_convergence(kls, threshold=0.01, min_consecutive=3) is None

    def test_converged_immediately_returns_first_cycle(self):
        kls = [0.001, 0.001, 0.001, 0.001, 0.001]
        # First cycle where 3 consecutive sub-threshold values starts: cycle 1
        assert _detect_convergence(kls, threshold=0.01, min_consecutive=3) == 1

    def test_converged_after_warmup(self):
        kls = [0.5, 0.3, 0.2, 0.005, 0.004, 0.003, 0.002]
        # Sub-threshold run starts at index 3 (cycle 4); needs 3 consecutive
        # → converges at cycle (3+1) - (3-1) = 2. Wait let me re-check my algorithm.
        # _detect_convergence: i is 0-indexed; returns (i+1) - (min_consecutive-1)
        # when run_len reaches min_consecutive. At i=3: run_len=1; i=4: 2; i=5: 3 → returns (6)-(2) = 4
        result = _detect_convergence(kls, threshold=0.01, min_consecutive=3)
        assert result == 4

    def test_single_dip_not_convergence(self):
        # Only 1 consecutive sub-threshold, not min 3
        kls = [0.5, 0.3, 0.005, 0.4, 0.2]
        assert _detect_convergence(kls, threshold=0.01, min_consecutive=3) is None

    def test_converged_then_diverged_then_reconverged(self):
        # Converges at cycle 3 (indices 0,1,2), then KL blows up at index 3,
        # then sub-threshold again at 4,5,6 — first detection is the first
        # qualifying window.
        kls = [0.005, 0.004, 0.003, 0.5, 0.001, 0.001, 0.001]
        # First: indices 0,1,2 — all sub-threshold → converged at cycle 1
        assert _detect_convergence(kls, threshold=0.01, min_consecutive=3) == 1

    def test_min_consecutive_one(self):
        # Edge case: min_consecutive=1 means first sub-threshold cycle counts
        kls = [0.5, 0.3, 0.005, 0.4]
        assert _detect_convergence(kls, threshold=0.01, min_consecutive=1) == 3

    def test_empty_list_returns_none(self):
        assert _detect_convergence([], threshold=0.01, min_consecutive=3) is None

    def test_shorter_than_min_consecutive_returns_none(self):
        kls = [0.001, 0.001]  # only 2, need 3
        assert _detect_convergence(kls, threshold=0.01, min_consecutive=3) is None


# ---------------------------------------------------------------------------
# _table_exists
# ---------------------------------------------------------------------------


class TestTableExists:
    def test_existing_table_returns_true(self):
        with EvaluationStore() as store:
            assert _table_exists(store._conn, "evaluation_runs") is True

    def test_missing_table_returns_false(self):
        with EvaluationStore() as store:
            assert _table_exists(store._conn, "does_not_exist") is False

    def test_step_18_5_and_follow_up_tables_exist(self):
        with EvaluationStore() as store:
            # All the tables the Step 18.7 analyzer queries should exist
            # on a freshly-initialized store.
            assert _table_exists(store._conn, "invocation_events") is True
            assert _table_exists(store._conn, "cycle_utility_scores") is True
            assert _table_exists(store._conn, "cycle_events") is True


# ---------------------------------------------------------------------------
# _load_cycle_data — integration with EvaluationStore
# ---------------------------------------------------------------------------


def _base_record(**overrides) -> EvaluationRecord:
    defaults = dict(
        run_id="test_run",
        condition="longitudinal",
        task_id="T01",
        task_tier="trivial",
        attempt_count=1,
        capability_type="mcp_tool",
        gate_passed=True,
        output_in_range=True,
        token_cost=0,
        attempt_latency_ms=100,
        failure_reason="none",
        registry_snapshot_id="snap",
        seed=42,
        is_pilot=False,
    )
    defaults.update(overrides)
    return EvaluationRecord(**defaults)


class TestLoadCycleData:
    def test_empty_db_returns_empty_list(self):
        with EvaluationStore() as store:
            assert _load_cycle_data(store._conn) == []

    def test_only_static_runs_returns_empty(self):
        with EvaluationStore() as store:
            store.record_run(_base_record(condition="static"))
            assert _load_cycle_data(store._conn) == []

    def test_longitudinal_runs_in_timestamp_order(self):
        from datetime import UTC, datetime, timedelta
        base = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)
        with EvaluationStore() as store:
            store.record_run(_base_record(task_id="T02", timestamp=base + timedelta(minutes=2)))
            store.record_run(_base_record(task_id="T01", timestamp=base + timedelta(minutes=1)))
            store.record_run(_base_record(task_id="T03", timestamp=base + timedelta(minutes=3)))

            cycles = _load_cycle_data(store._conn)
            # Should be ordered by timestamp
            assert [c["task_id"] for c in cycles] == ["T01", "T02", "T03"]
            assert [c["cycle"] for c in cycles] == [1, 2, 3]

    def test_utility_scores_loaded_when_available(self):
        with EvaluationStore() as store:
            store.record_run(_base_record(task_id="T01"))
            store.record_cycle_utility_scores(
                run_id="test_run", condition="longitudinal", task_id="T01",
                scores={"cap-a": 0.9, "cap-b": 0.5},
            )
            cycles = _load_cycle_data(store._conn)
            assert len(cycles) == 1
            assert cycles[0]["utility_scores"] == {"cap-a": 0.9, "cap-b": 0.5}

    def test_events_loaded_when_available(self):
        with EvaluationStore() as store:
            store.record_run(_base_record(task_id="T01"))
            store.record_cycle_events(
                run_id="test_run", condition="longitudinal", task_id="T01",
                events=[
                    {"capability_id": "cap-a", "event_type": "pruned",
                     "details": "zero invocations"},
                    {"capability_id": "cap-b", "event_type": "split",
                     "details": "into [v1, v2]"},
                ],
            )
            cycles = _load_cycle_data(store._conn)
            assert len(cycles) == 1
            events = cycles[0]["events"]
            assert len(events) == 2
            assert {e["event_type"] for e in events} == {"pruned", "split"}

    def test_cumulative_invocations_snapshot_per_cycle(self):
        """Invocations from cycle N appear in cycle N's snapshot AND all later ones."""
        from datetime import UTC, datetime, timedelta
        base = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)
        with EvaluationStore() as store:
            # Cycle 1
            store.record_run(_base_record(task_id="T01", timestamp=base + timedelta(minutes=1)))
            store._conn.execute(
                "INSERT INTO invocation_events (id, run_id, condition, task_id, "
                "capability_id, capability_type, success, token_cost, latency_ms, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ["inv1", "test_run", "longitudinal", "T01",
                 "cap-a", "mcp_tool", True, 0, 0,
                 base + timedelta(minutes=1, seconds=30)],
            )
            # Cycle 2
            store.record_run(_base_record(task_id="T02", timestamp=base + timedelta(minutes=2)))
            store._conn.execute(
                "INSERT INTO invocation_events (id, run_id, condition, task_id, "
                "capability_id, capability_type, success, token_cost, latency_ms, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ["inv2", "test_run", "longitudinal", "T02",
                 "cap-b", "mcp_tool", True, 0, 0,
                 base + timedelta(minutes=2, seconds=30)],
            )
            cycles = _load_cycle_data(store._conn)
            assert len(cycles) == 2
            # Cycle 1's cumulative: just cap-a
            assert cycles[0]["cumulative_invocations"] == {"cap-a": 1}
            # Cycle 2's cumulative: both caps
            assert cycles[1]["cumulative_invocations"] == {"cap-a": 1, "cap-b": 1}
            # Active cap counts
            assert cycles[0]["active_cap_count"] == 1
            assert cycles[1]["active_cap_count"] == 2


# ---------------------------------------------------------------------------
# End-to-end: synthesize a mini longitudinal DB and verify analyzer reads it
# ---------------------------------------------------------------------------


class TestDetectShiftPoint:
    """Step 18.8: shift-point detection from cycle_events."""

    def test_no_shift_event_returns_none(self):
        cycles = [
            {"cycle": 1, "task_id": "T01", "events": []},
            {"cycle": 2, "task_id": "T02", "events": [{"capability_id": "c", "event_type": "pruned", "details": ""}]},
        ]
        assert _detect_shift_point(cycles) is None

    def test_shift_event_returns_cycle_number(self):
        cycles = [
            {"cycle": 1, "task_id": "T01", "events": []},
            {"cycle": 5, "task_id": "T05", "events": [
                {"capability_id": "__system__", "event_type": "distribution_shift",
                 "details": "phase_a=[T01-T05], phase_b=[T06-T10]"},
            ]},
            {"cycle": 6, "task_id": "T06", "events": []},
        ]
        assert _detect_shift_point(cycles) == 5

    def test_first_shift_wins_if_multiple(self):
        # Shouldn't happen in practice but defensive behavior
        cycles = [
            {"cycle": 3, "task_id": "T03", "events": [
                {"capability_id": "__system__", "event_type": "distribution_shift",
                 "details": ""},
            ]},
            {"cycle": 5, "task_id": "T05", "events": [
                {"capability_id": "__system__", "event_type": "distribution_shift",
                 "details": ""},
            ]},
        ]
        assert _detect_shift_point(cycles) == 3

    def test_empty_cycles_list_returns_none(self):
        assert _detect_shift_point([]) is None


class TestClassifyAdaptation:
    """Step 18.8: three-tier adaptation classifier."""

    def test_graceful_adaptation_below_2x(self):
        # Phase A converged at cycle 5; Phase B re-converged in 8 cycles → 1.6x
        v = _classify_adaptation(5, 8)
        assert "graceful" in v.lower()

    def test_graceful_at_exactly_2x_boundary(self):
        # 10 / 5 = 2.0 → graceful per the <= 2.0 rule
        v = _classify_adaptation(5, 10)
        assert "graceful" in v.lower()

    def test_sluggish_between_2x_and_5x(self):
        # Phase A converged at cycle 5; Phase B re-converged in 15 cycles → 3x
        v = _classify_adaptation(5, 15)
        assert "sluggish" in v.lower()

    def test_collapse_leaning_above_5x(self):
        # Phase A converged at cycle 5; Phase B re-converged in 30 cycles → 6x
        v = _classify_adaptation(5, 30)
        assert "collapse" in v.lower()

    def test_collapse_when_phase_b_never_reconverged(self):
        v = _classify_adaptation(5, None)
        assert "collapse" in v.lower()
        assert "never re-converged" in v.lower()

    def test_indeterminate_when_phase_a_never_converged(self):
        v = _classify_adaptation(None, 10)
        assert "indeterminate" in v.lower()


class TestEndToEnd:
    def test_analyzer_produces_cycles_with_all_fields(self):
        """Verify _load_cycle_data + downstream processing produces a
        record with all expected fields when all four tables are populated.

        Security audit finding #18 (2026-04-20): pass explicit
        ``occurred_at`` timestamps so utility/event rows land in their
        intended cycle windows. Post-fix the analyzer buckets rows by
        timestamp, so the test must ensure temporal ordering matches
        the task_rows.
        """
        from datetime import UTC, datetime, timedelta
        base = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)
        with EvaluationStore() as store:
            for i in range(1, 4):
                ts = base + timedelta(minutes=i)
                # Utility / event rows occur within this task's cycle window.
                cycle_ts = ts + timedelta(seconds=30)
                store.record_run(_base_record(task_id=f"T{i:02d}", timestamp=ts))
                store.record_cycle_utility_scores(
                    run_id="test_run", condition="longitudinal", task_id=f"T{i:02d}",
                    scores={"cap-a": 0.5, "cap-b": 0.3},
                    occurred_at=cycle_ts,
                )
                store.record_cycle_events(
                    run_id="test_run", condition="longitudinal", task_id=f"T{i:02d}",
                    events=[{"capability_id": "cap-a",
                             "event_type": "pruned" if i == 2 else "merged",
                             "details": ""}],
                    occurred_at=cycle_ts,
                )
            cycles = _load_cycle_data(store._conn)
            assert len(cycles) == 3
            for c in cycles:
                # All four data sources should be populated
                assert "cycle" in c
                assert "task_id" in c
                assert "run_id" in c
                assert "timestamp" in c
                assert "cumulative_invocations" in c
                assert "incremental_invocations" in c
                assert "active_cap_count" in c
                assert "utility_scores" in c
                assert "events" in c
                assert c["utility_scores"] == {"cap-a": 0.5, "cap-b": 0.3}
                assert len(c["events"]) == 1


class TestPerCycleIsolationAcrossTaskRepetitions:
    """Security audit finding #18 (2026-04-20) regression guard.

    Pre-fix, utility and event rows were keyed by ``task_id`` only. When
    a task repeated in the sequence (default ``run_longitudinal.py
    --n-tasks=40`` with 20 unique tasks repeats every task at least
    once), cycle N and cycle M (both T01) both read the merged/last-
    write-wins dict. Post-fix, rows are bucketed by timestamp window
    against ``task_boundaries``, so each cycle sees only its own
    utility/event rows.
    """

    def test_per_cycle_utility_snapshots_isolated_across_task_repetitions(self):
        """Two cycles with the same task_id carry distinct utility values."""
        from datetime import UTC, datetime, timedelta

        # One longitudinal run = one run_id (security audit finding #24).
        # Repeating task_ids within that run must still isolate per-cycle
        # (ext5 #18 fix) — this test verifies per-cycle isolation WITHIN
        # a single run_id.
        run_id = "longitudinal-run"
        base = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)
        with EvaluationStore() as store:
            # Cycle 1 — T01 @ t=1 min; utility cap-x = 0.1
            ts_1 = base + timedelta(minutes=1)
            store.record_run(_base_record(
                task_id="T01", timestamp=ts_1, run_id=run_id,
            ))
            store.record_cycle_utility_scores(
                run_id=run_id, condition="longitudinal", task_id="T01",
                scores={"cap-x": 0.1},
                occurred_at=ts_1 + timedelta(seconds=10),
            )

            # Cycle 2 — T01 AGAIN @ t=2 min; utility cap-x = 0.9
            ts_2 = base + timedelta(minutes=2)
            store.record_run(_base_record(
                task_id="T01", timestamp=ts_2, run_id=run_id,
            ))
            store.record_cycle_utility_scores(
                run_id=run_id, condition="longitudinal", task_id="T01",
                scores={"cap-x": 0.9},
                occurred_at=ts_2 + timedelta(seconds=10),
            )

            cycles = _load_cycle_data(store._conn)
            assert len(cycles) == 2

            # Critical assertion: cycles must have DIFFERENT utility values.
            # Pre-fix both cycles would read {"cap-x": 0.9} (last-write-wins).
            assert cycles[0]["utility_scores"] == {"cap-x": 0.1}, (
                f"Cycle 1 (T01 @ {ts_1}) should report its own utility 0.1. "
                f"Got: {cycles[0]['utility_scores']} — pre-fix regression."
            )
            assert cycles[1]["utility_scores"] == {"cap-x": 0.9}

    def test_per_cycle_events_isolated_across_task_repetitions(self):
        """Events from different cycles of the same task_id must not merge."""
        from datetime import UTC, datetime, timedelta

        # One longitudinal run = one run_id (security audit finding #24).
        run_id = "longitudinal-run"
        base = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)
        with EvaluationStore() as store:
            ts_1 = base + timedelta(minutes=1)
            store.record_run(_base_record(
                task_id="T01", timestamp=ts_1, run_id=run_id,
            ))
            store.record_cycle_events(
                run_id=run_id, condition="longitudinal", task_id="T01",
                events=[{"capability_id": "cap-a", "event_type": "split", "details": "cycle 1"}],
                occurred_at=ts_1 + timedelta(seconds=10),
            )

            ts_2 = base + timedelta(minutes=2)
            store.record_run(_base_record(
                task_id="T01", timestamp=ts_2, run_id=run_id,
            ))
            store.record_cycle_events(
                run_id=run_id, condition="longitudinal", task_id="T01",
                events=[{"capability_id": "cap-b", "event_type": "merged", "details": "cycle 2"}],
                occurred_at=ts_2 + timedelta(seconds=10),
            )

            cycles = _load_cycle_data(store._conn)
            assert len(cycles) == 2

            # Pre-fix: cycles[0]["events"] and cycles[1]["events"] both
            # had 2 entries (the merged list). Post-fix: each has 1.
            assert len(cycles[0]["events"]) == 1
            assert cycles[0]["events"][0]["event_type"] == "split"
            assert cycles[0]["events"][0]["details"] == "cycle 1"

            assert len(cycles[1]["events"]) == 1
            assert cycles[1]["events"][0]["event_type"] == "merged"
            assert cycles[1]["events"][0]["details"] == "cycle 2"


class TestRunIdIsolation:
    """Security audit finding #24 (2026-04-20) regression guard.

    Pre-fix, `_load_cycle_data` queried `WHERE condition = 'longitudinal'`
    without a run_id filter — when the DB contained multiple runs, the
    analyzer merged them into one interleaved timeline, corrupting
    KL convergence / shift detection / adaptation verdicts.
    """

    def test_two_runs_each_analyzed_independently(self):
        """Two runs in one DB → analyzer scoped to a single run_id
        returns only that run's cycles."""
        from datetime import UTC, datetime, timedelta

        base = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)
        with EvaluationStore() as store:
            # Run r1 — 2 cycles at t=1min, t=2min
            for i in range(1, 3):
                ts = base + timedelta(minutes=i)
                store.record_run(_base_record(
                    task_id=f"T{i:02d}", timestamp=ts, run_id="r1",
                    condition="longitudinal",
                ))

            # Run r2 — 2 cycles at t=10min, t=11min (later)
            for i in range(1, 3):
                ts = base + timedelta(minutes=10 + i)
                store.record_run(_base_record(
                    task_id=f"T{i:02d}", timestamp=ts, run_id="r2",
                    condition="longitudinal",
                ))

            # Passing run_id="r1" returns only r1's 2 cycles.
            cycles_r1 = _load_cycle_data(store._conn, run_id="r1")
            assert len(cycles_r1) == 2
            assert all(c["run_id"] == "r1" for c in cycles_r1)

            # Passing run_id="r2" returns only r2's 2 cycles.
            cycles_r2 = _load_cycle_data(store._conn, run_id="r2")
            assert len(cycles_r2) == 2
            assert all(c["run_id"] == "r2" for c in cycles_r2)

            # Default (None) auto-selects the latest — r2 has the newer
            # timestamps so it wins.
            cycles_default = _load_cycle_data(store._conn)
            assert len(cycles_default) == 2
            assert all(c["run_id"] == "r2" for c in cycles_default)

    def test_nonexistent_run_id_raises(self):
        """Explicit run_id that doesn't exist → ValueError (loud fail,
        not silent empty result)."""
        import pytest as _pytest
        from datetime import UTC, datetime

        with EvaluationStore() as store:
            store.record_run(_base_record(
                task_id="T01", timestamp=datetime.now(UTC), run_id="r1",
                condition="longitudinal",
            ))

            with _pytest.raises(ValueError, match="not found"):
                _load_cycle_data(store._conn, run_id="nonexistent")

    def test_empty_db_returns_empty(self):
        """No longitudinal runs → empty list, no error."""
        with EvaluationStore() as store:
            assert _load_cycle_data(store._conn) == []
            assert _load_cycle_data(store._conn, run_id=None) == []
