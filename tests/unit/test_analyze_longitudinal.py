"""Unit tests for scripts/analyze_longitudinal.py helper behavior."""

from __future__ import annotations

from datetime import datetime, timedelta

import duckdb
import numpy as np
import pytest

from scripts.analyze_longitudinal import (
    _average_distributions,
    _detect_convergence,
    _detect_stable_convergence,
    _kl_divergence_between_snapshots,
    _load_cycle_data,
    _table_exists,
)
from src.registry.evaluation_store import EvaluationStore
from src.schemas.evaluation import EvaluationRecord
from tests.unit._script_subprocess import (
    insert_invocation_events,
    run_project_script,
    seed_evaluation_runs,
)

# ---------------------------------------------------------------------------
# _kl_divergence_between_snapshots
# ---------------------------------------------------------------------------


class TestKLDivergenceBetweenSnapshots:
    def test_both_empty_returns_nan(self):
        """Empty-vs-empty returns NaN so no-data cycles do not converge.

        The convergence detector treats NaN as missing signal and resets
        its sub-threshold streak.
        """
        import math

        result = _kl_divergence_between_snapshots({}, {})
        assert math.isnan(result)

    def test_prev_empty_curr_populated_returns_zero(self):
        # First-cycle sentinel: we're seeing the first real snapshot, so
        # KL against nothing is 0.0 by convention.
        assert _kl_divergence_between_snapshots({}, {"a": 1.0}) == 0.0

    def test_prev_populated_curr_empty_returns_inf(self):
        # Catastrophic: all caps disappeared between snapshots.
        assert _kl_divergence_between_snapshots({"a": 1.0}, {}) == float("inf")

    def test_identical_distributions_near_zero(self):
        kl = _kl_divergence_between_snapshots(
            {"a": 0.5, "b": 0.3, "c": 0.2},
            {"a": 0.5, "b": 0.3, "c": 0.2},
        )
        # epsilon-smoothing means it's not exactly 0, but very small
        assert 0.0 <= kl < 0.001

    def test_different_distributions_non_trivial(self):
        # Opposite distributions produce higher KL than near-identical ones.
        kl_similar = _kl_divergence_between_snapshots(
            {"a": 0.5, "b": 0.5}, {"a": 0.55, "b": 0.45},
        )
        kl_different = _kl_divergence_between_snapshots(
            {"a": 0.9, "b": 0.1}, {"a": 0.1, "b": 0.9},
        )
        assert kl_different > kl_similar
        assert kl_different > 0.5

    def test_negative_utility_scores_handled(self):
        # Utility scores can be negative.
        # The analyzer must shift to non-negative + normalize; it should not raise.
        kl = _kl_divergence_between_snapshots(
            {"a": -0.5, "b": 0.3, "c": 0.7},
            {"a": -0.3, "b": 0.4, "c": 0.6},
        )
        assert np.isfinite(kl)
        assert kl >= 0.0

    def test_union_of_keys_handled(self):
        # A new cap appears in curr but not prev; KL should still compute.
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
        # Sub-threshold run starts at index 3 (cycle 4). Once the third
        # consecutive sub-threshold value is seen, the detector returns
        # the first cycle in that qualifying window.
        result = _detect_convergence(kls, threshold=0.01, min_consecutive=3)
        assert result == 4

    def test_single_dip_not_convergence(self):
        # Only 1 consecutive sub-threshold, not min 3
        kls = [0.5, 0.3, 0.005, 0.4, 0.2]
        assert _detect_convergence(kls, threshold=0.01, min_consecutive=3) is None

    def test_nan_series_does_not_converge(self):
        # NaN marks missing signal, so it must not satisfy a sub-threshold streak.
        kls = [float("nan")] * 40

        assert _detect_convergence(kls, threshold=0.05, min_consecutive=3) is None

    def test_nan_breaks_convergence_streak(self):
        kls = [0.01, 0.01, float("nan"), 0.01, 0.01]

        assert _detect_convergence(kls, threshold=0.05, min_consecutive=3) is None

    def test_converged_then_diverged_then_reconverged(self):
        # Converges at cycle 3 (indices 0,1,2), then KL blows up at index 3,
        # then sub-threshold again at 4,5,6; first detection is the first
        # qualifying window.
        kls = [0.005, 0.004, 0.003, 0.5, 0.001, 0.001, 0.001]
        # First window: indices 0,1,2 are all sub-threshold, so cycle 1.
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


class TestDetectStableConvergence:
    def test_drift_to_new_state_not_falsely_converged_at_early_cycle(self):
        """Stable convergence uses the final-window reference, not step deltas."""
        snapshots = [
            {"A": 0.9 - 0.08 * i, "B": 0.1 + 0.08 * i}
            for i in range(10)
        ]
        snapshots += [{"A": 0.1, "B": 0.9}] * 5

        result = _detect_stable_convergence(
            snapshots,
            threshold=0.01,
            min_consecutive=3,
            window_size=3,
        )

        assert result is None or result >= 9

    def test_stable_distribution_detected(self):
        snapshots = [{"A": 0.5, "B": 0.5}] * 10

        result = _detect_stable_convergence(
            snapshots,
            threshold=0.01,
            min_consecutive=3,
            window_size=3,
        )

        assert result == 1

    def test_too_few_snapshots_returns_none(self):
        result = _detect_stable_convergence(
            [{"A": 1.0}],
            threshold=0.01,
            min_consecutive=3,
            window_size=5,
        )

        assert result is None

    def test_empty_final_window_is_not_convergence(self):
        """Collapse to an empty final distribution is not convergence."""
        snapshots = [{"capA": 1.0} for _ in range(5)] + [{} for _ in range(5)]

        result = _detect_stable_convergence(
            snapshots,
            threshold=0.1,
            min_consecutive=3,
            window_size=5,
        )

        assert result is None


class TestAverageDistributions:
    def test_average_with_missing_keys(self):
        snapshots = [{"A": 1.0}, {"B": 1.0}, {"A": 1.0, "B": 1.0}]

        avg = _average_distributions(snapshots)

        assert abs(avg["A"] - 2 / 3) < 1e-9
        assert abs(avg["B"] - 2 / 3) < 1e-9

    def test_empty_snapshots(self):
        assert _average_distributions([]) == {}


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

    def test_analyzer_support_tables_exist(self):
        with EvaluationStore() as store:
            # All tables queried by the analyzer should exist on a fresh store.
            assert _table_exists(store._conn, "invocation_events") is True
            assert _table_exists(store._conn, "cycle_utility_scores") is True
            assert _table_exists(store._conn, "cycle_events") is True


# ---------------------------------------------------------------------------
# _load_cycle_data - integration with EvaluationStore
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


class TestEndToEnd:
    def test_analyzer_produces_cycles_with_all_fields(self):
        """Verify _load_cycle_data + downstream processing produces a
        record with all expected fields when all four tables are populated.

        Explicit ``occurred_at`` timestamps ensure utility/event rows land
        in their intended cycle windows.
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
    """Repeated task IDs must not collapse per-cycle telemetry.

    Utility and event rows are bucketed by timestamp window, not only by
    ``task_id``. That keeps repeated occurrences of the same task isolated
    in the per-cycle time series.
    """

    def test_per_cycle_utility_snapshots_isolated_across_task_repetitions(self):
        """Two cycles with the same task_id carry distinct utility values."""
        from datetime import UTC, datetime, timedelta

        # Repeating task_ids within one run must still isolate per-cycle
        # telemetry.
        run_id = "longitudinal-run"
        base = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)
        with EvaluationStore() as store:
            # Cycle 1: T01 at t=1 min; utility cap-x = 0.1.
            ts_1 = base + timedelta(minutes=1)
            store.record_run(_base_record(
                task_id="T01", timestamp=ts_1, run_id=run_id,
            ))
            store.record_cycle_utility_scores(
                run_id=run_id, condition="longitudinal", task_id="T01",
                scores={"cap-x": 0.1},
                occurred_at=ts_1 + timedelta(seconds=10),
            )

            # Cycle 2: T01 again at t=2 min; utility cap-x = 0.9.
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

            # The two repeated task cycles must keep distinct utility values.
            assert cycles[0]["utility_scores"] == {"cap-x": 0.1}, (
                f"Cycle 1 (T01 @ {ts_1}) should report its own utility 0.1. "
                f"Got: {cycles[0]['utility_scores']}."
            )
            assert cycles[1]["utility_scores"] == {"cap-x": 0.9}

    def test_per_cycle_events_isolated_across_task_repetitions(self):
        """Events from different cycles of the same task_id must not merge."""
        from datetime import UTC, datetime, timedelta

        # One longitudinal run = one run_id.
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

            # Each repeated task cycle should carry only its own event.
            assert len(cycles[0]["events"]) == 1
            assert cycles[0]["events"][0]["event_type"] == "split"
            assert cycles[0]["events"][0]["details"] == "cycle 1"

            assert len(cycles[1]["events"]) == 1
            assert cycles[1]["events"][0]["event_type"] == "merged"
            assert cycles[1]["events"][0]["details"] == "cycle 2"


class TestRunIdIsolation:
    """Multiple longitudinal runs in one DB must be analyzed separately.

    ``_load_cycle_data`` scopes to a single ``run_id`` so appended or
    repeated campaigns do not merge into one interleaved timeline.
    """

    def test_two_runs_each_analyzed_independently(self):
        """Two runs in one DB: analyzer scoped to a single run_id
        returns only that run's cycles."""
        from datetime import UTC, datetime, timedelta

        base = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)
        with EvaluationStore() as store:
            # Run r1: 2 cycles at t=1min, t=2min.
            for i in range(1, 3):
                ts = base + timedelta(minutes=i)
                store.record_run(_base_record(
                    task_id=f"T{i:02d}", timestamp=ts, run_id="r1",
                    condition="longitudinal",
                ))

            # Run r2: 2 cycles at t=10min, t=11min (later).
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

            # Default (None) auto-selects the latest; r2 has the newer
            # timestamps so it wins.
            cycles_default = _load_cycle_data(store._conn)
            assert len(cycles_default) == 2
            assert all(c["run_id"] == "r2" for c in cycles_default)

    def test_nonexistent_run_id_raises(self):
        """Explicit run_id that doesn't exist raises ValueError (loud fail,
        not silent empty result)."""
        from datetime import UTC, datetime

        with EvaluationStore() as store:
            store.record_run(_base_record(
                task_id="T01", timestamp=datetime.now(UTC), run_id="r1",
                condition="longitudinal",
            ))

            with pytest.raises(ValueError, match="not found"):
                _load_cycle_data(store._conn, run_id="nonexistent")

    def test_empty_db_returns_empty(self):
        """No longitudinal runs returns an empty list, no error."""
        with EvaluationStore() as store:
            assert _load_cycle_data(store._conn) == []
            assert _load_cycle_data(store._conn, run_id=None) == []


class TestAnalyzeLongitudinalScriptExitCodes:
    """Subprocess-level checks for analyzer exit-code contracts."""

    def test_help_exposes_kl_and_sparse_signal_flags(self):
        result = run_project_script("analyze_longitudinal.py", "--help", timeout=15)

        assert result.returncode == 0
        assert "--allow-proxy-kl" in result.stdout
        assert "--allow-sparse-signal" in result.stdout
        assert "--min-cycle-coverage" in result.stdout

    def test_partial_db_exits_3(self, tmp_path):
        db = tmp_path / "longitudinal_partial.duckdb"
        base_ts = datetime(2026, 4, 21, 10, 0, 0)
        rows = [
            {
                "run_id": "lg_partial",
                "condition": "longitudinal",
                "task_id": f"T{i + 1:02d}",
                "timestamp": base_ts + timedelta(seconds=i),
            }
            for i in range(5)
        ]
        seed_evaluation_runs(db, rows=rows)

        result = run_project_script(
            "analyze_longitudinal.py",
            "--db",
            str(db),
            "--expected-cycles",
            "40",
        )

        assert result.returncode == 3, (
            f"Expected exit 3 for incomplete campaign, got {result.returncode}. "
            f"stderr:\n{result.stderr}\nstdout:\n{result.stdout}"
        )
        assert "5 cycles, expected 40" in result.stderr or "expected 40" in result.stderr

    def test_allow_incomplete_still_returns_nonzero_with_banner(self, tmp_path):
        db = tmp_path / "longitudinal_partial.duckdb"
        base_ts = datetime(2026, 4, 21, 10, 0, 0)
        rows = [
            {
                "run_id": "lg_partial",
                "condition": "longitudinal",
                "task_id": f"T{i + 1:02d}",
                "timestamp": base_ts + timedelta(seconds=i),
            }
            for i in range(5)
        ]
        seed_evaluation_runs(db, rows=rows)

        result = run_project_script(
            "analyze_longitudinal.py",
            "--db",
            str(db),
            "--expected-cycles",
            "40",
            "--allow-incomplete",
        )

        assert result.returncode != 0, (
            f"--allow-incomplete must still signal shortfall. Got {result.returncode}."
        )
        combined = result.stdout + result.stderr
        assert (
            "INCOMPLETE" in combined
            or "zero-evidence" in combined.lower()
            or "no invocation" in combined.lower()
        ), f"Expected incomplete or zero-evidence signal. stdout+stderr:\n{combined}"

    def test_empty_db_exits_1(self, tmp_path):
        db = tmp_path / "empty.duckdb"
        seed_evaluation_runs(db, rows=[])

        result = run_project_script("analyze_longitudinal.py", "--db", str(db))

        assert result.returncode == 1
        combined = result.stdout.lower() + result.stderr.lower()
        assert "no longitudinal rows" in combined

    def test_zero_evidence_db_exits_4(self, tmp_path):
        db = tmp_path / "zero_evidence.duckdb"
        base_ts = datetime(2026, 4, 21, 10, 0, 0)
        rows = [
            {
                "run_id": "zero_ev",
                "condition": "longitudinal",
                "task_id": f"T{(i % 20) + 1:02d}",
                "timestamp": base_ts + timedelta(seconds=i),
            }
            for i in range(40)
        ]
        seed_evaluation_runs(db, rows=rows)

        result = run_project_script(
            "analyze_longitudinal.py",
            "--db",
            str(db),
            "--expected-cycles",
            "40",
        )

        assert result.returncode == 4, (
            f"Expected exit 4 for zero-evidence DB, got {result.returncode}. "
            f"stderr:\n{result.stderr}\nstdout:\n{result.stdout}"
        )
        combined = result.stderr + result.stdout
        assert (
            "NO invocation" in combined
            or "zero-evidence" in combined.lower()
            or "no signal" in combined.lower()
        )

    def test_overfull_cycle_count_exits_3(self, tmp_path):
        db = tmp_path / "longitudinal_overfull.duckdb"
        base_ts = datetime(2026, 4, 21, 10, 0, 0)
        rows = [
            {
                "run_id": "long_1",
                "condition": "longitudinal",
                "task_id": f"T{(i % 17) + 4:02d}",
                "timestamp": base_ts + timedelta(seconds=i),
            }
            for i in range(50)
        ]
        seed_evaluation_runs(db, rows=rows)

        conn = duckdb.connect(str(db))
        try:
            for i in range(50):
                task_id = f"T{(i % 17) + 4:02d}"
                occurred_at = base_ts + timedelta(seconds=i, milliseconds=500)
                conn.execute(
                    "INSERT INTO cycle_utility_scores "
                    "(id, run_id, condition, task_id, capability_id, "
                    "utility_score, occurred_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        f"cus_{i}",
                        "long_1",
                        "longitudinal",
                        task_id,
                        f"cap_{i}",
                        1.0,
                        occurred_at,
                    ],
                )
                conn.execute(
                    "INSERT INTO invocation_events "
                    "(id, run_id, condition, task_id, capability_id, "
                    "capability_type, success, is_pilot, occurred_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        f"inv_{i}",
                        "long_1",
                        "longitudinal",
                        task_id,
                        f"cap_{i}",
                        "mcp_tool",
                        True,
                        False,
                        occurred_at,
                    ],
                )
        finally:
            conn.close()

        result = run_project_script(
            "analyze_longitudinal.py",
            "--db",
            str(db),
            "--expected-cycles",
            "40",
        )

        assert result.returncode == 3, (
            f"Expected exit 3 for overfull timeline, got {result.returncode}. "
            f"stderr:\n{result.stderr[:400]}"
        )
        assert "overfull" in result.stderr.lower()

    def test_missing_utility_scores_refuses_proxy_kl_without_override(self, tmp_path):
        """Invocation-only DBs must not silently use proxy KL as primary evidence."""
        db = tmp_path / "proxy_kl_test.duckdb"
        base_ts = datetime(2026, 4, 21, 10, 0, 0)
        rows = [
            {
                "run_id": "r1",
                "condition": "longitudinal",
                "task_id": f"T{i + 1:02d}",
                "timestamp": base_ts + timedelta(seconds=i),
            }
            for i in range(5)
        ]
        seed_evaluation_runs(db, rows=rows)
        insert_invocation_events(
            db,
            events=[
                {
                    "run_id": "r1",
                    "condition": "longitudinal",
                    "task_id": f"T{i + 1:02d}",
                    "capability_id": "cap-a",
                    "occurred_at": base_ts + timedelta(seconds=i, milliseconds=500),
                }
                for i in range(5)
            ],
        )

        result = run_project_script(
            "analyze_longitudinal.py",
            "--db",
            str(db),
            "--expected-cycles",
            "5",
        )

        assert result.returncode == 6, (
            f"Expected exit 6 for refused proxy KL, got {result.returncode}. "
            f"stderr:\n{result.stderr[-600:]}"
        )
        assert "cycle_utility_scores is empty" in result.stderr

    def test_allow_proxy_kl_adds_banner(self, tmp_path):
        db = tmp_path / "proxy_kl_allow.duckdb"
        base_ts = datetime(2026, 4, 21, 10, 0, 0)
        rows = [
            {
                "run_id": "r1",
                "condition": "longitudinal",
                "task_id": f"T{i + 1:02d}",
                "timestamp": base_ts + timedelta(seconds=i),
            }
            for i in range(5)
        ]
        seed_evaluation_runs(db, rows=rows)
        insert_invocation_events(
            db,
            events=[
                {
                    "run_id": "r1",
                    "condition": "longitudinal",
                    "task_id": f"T{i + 1:02d}",
                    "capability_id": "cap-a",
                    "occurred_at": base_ts + timedelta(seconds=i, milliseconds=500),
                }
                for i in range(5)
            ],
        )

        result = run_project_script(
            "analyze_longitudinal.py",
            "--db",
            str(db),
            "--expected-cycles",
            "5",
            "--allow-proxy-kl",
        )

        assert result.returncode != 6, (
            f"--allow-proxy-kl should bypass exit 6. Got {result.returncode}. "
            f"stderr:\n{result.stderr[-400:]}"
        )
        assert "[PROXY KL]" in result.stdout

    def test_sparse_cycle_signal_exits_8(self, tmp_path):
        """A single telemetry cycle in a full campaign is not enough evidence."""
        db = tmp_path / "sparse.duckdb"
        base_ts = datetime(2026, 4, 21, 10, 0, 0)
        rows = [
            {
                "run_id": "r1",
                "condition": "longitudinal",
                "task_id": f"T{(i % 20) + 1:02d}",
                "timestamp": base_ts + timedelta(seconds=i),
            }
            for i in range(20)
        ]
        seed_evaluation_runs(db, rows=rows)
        insert_invocation_events(
            db,
            events=[
                {
                    "run_id": "r1",
                    "condition": "longitudinal",
                    "task_id": "T20",
                    "capability_id": "cap-a",
                    "occurred_at": base_ts + timedelta(seconds=19, milliseconds=500),
                }
            ],
        )

        result = run_project_script(
            "analyze_longitudinal.py",
            "--db",
            str(db),
            "--expected-cycles",
            "20",
        )

        assert result.returncode == 8, (
            f"Expected exit 8 for sparse signal, got {result.returncode}. "
            f"stderr:\n{result.stderr[-500:]}"
        )
        assert "sparse telemetry" in result.stderr.lower() or "signal in only" in (
            result.stderr.lower()
        )

    def test_allow_sparse_signal_adds_banner(self, tmp_path):
        db = tmp_path / "sparse_allow.duckdb"
        base_ts = datetime(2026, 4, 21, 10, 0, 0)
        rows = [
            {
                "run_id": "r1",
                "condition": "longitudinal",
                "task_id": f"T{(i % 20) + 1:02d}",
                "timestamp": base_ts + timedelta(seconds=i),
            }
            for i in range(20)
        ]
        seed_evaluation_runs(db, rows=rows)
        insert_invocation_events(
            db,
            events=[
                {
                    "run_id": "r1",
                    "condition": "longitudinal",
                    "task_id": "T20",
                    "capability_id": "cap-a",
                    "occurred_at": base_ts + timedelta(seconds=19, milliseconds=500),
                }
            ],
        )

        result = run_project_script(
            "analyze_longitudinal.py",
            "--db",
            str(db),
            "--expected-cycles",
            "20",
            "--allow-sparse-signal",
            "--allow-proxy-kl",
        )

        assert result.returncode != 8, (
            f"--allow-sparse-signal should bypass exit 8. Got {result.returncode}. "
            f"stderr:\n{result.stderr[-400:]}"
        )
        assert "[SPARSE:" in result.stdout
