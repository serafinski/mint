"""Unit tests for scripts/analyze_results.py helpers.

Step 18.9 — M5 time-to-first-correct filter narrowing. The
``print_latency_summary`` helper must exclude failed runs (which have
no "first correct output") before aggregating, and must report
mean / median / p95 / stdev plus a per-condition excluded-row count.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.analyze_results import print_latency_summary  # noqa: E402
from src.registry.evaluation_store import EvaluationStore  # noqa: E402
from src.schemas.evaluation import EvaluationRecord  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _record(
    *,
    condition: str,
    gate_passed: bool,
    output_in_range: bool,
    latency_ms: int,
    is_pilot: bool = False,
    task_id: str = "T04",
) -> EvaluationRecord:
    return EvaluationRecord(
        run_id=f"run-{condition}-{latency_ms}",
        condition=condition,
        task_id=task_id,
        task_tier="moderate",
        attempt_count=1,
        capability_type="mcp_tool",
        verification_tier_reached=3,
        gate_passed=gate_passed,
        output_in_range=output_in_range,
        token_cost=100,
        attempt_latency_ms=latency_ms,
        failure_reason="none" if (gate_passed and output_in_range) else "output_rejected",
        registry_snapshot_id="snap-1",
        invocation_record_id="inv-1",
        seed=42,
        is_pilot=is_pilot,
    )


def _open_in_memory_store() -> tuple[EvaluationStore, duckdb.DuckDBPyConnection]:
    """Return an EvaluationStore + its underlying connection (for direct queries)."""
    store = EvaluationStore()  # :memory: default
    return store, store._conn  # noqa: SLF001 — test-only access


# ---------------------------------------------------------------------------
# Filter: failed runs must be excluded
# ---------------------------------------------------------------------------


def test_filter_excludes_failed_runs(capsys):
    store, conn = _open_in_memory_store()
    # Two successful runs at 1000ms and 3000ms → mean should be 2.0s.
    # One failed run at 60000ms — MUST NOT pollute the mean.
    store.record_run(
        _record(
            condition="full",
            gate_passed=True,
            output_in_range=True,
            latency_ms=1000,
        )
    )
    store.record_run(
        _record(
            condition="full",
            gate_passed=True,
            output_in_range=True,
            latency_ms=3000,
        )
    )
    store.record_run(
        _record(
            condition="full",
            gate_passed=False,  # ← failed: must be excluded
            output_in_range=False,
            latency_ms=60000,
        )
    )
    print_latency_summary(conn)
    output = capsys.readouterr().out

    # Mean of the two successful runs is 2.0s, NOT (1 + 3 + 60) / 3 = 21.3s.
    assert "2.0" in output
    assert "21.3" not in output
    # Should report one excluded row.
    assert "Excluded: 1" in output


def test_filter_excludes_output_in_range_false(capsys):
    """gate_passed=True alone is not enough — output_in_range must also be True."""
    store, conn = _open_in_memory_store()
    store.record_run(
        _record(
            condition="full",
            gate_passed=True,
            output_in_range=True,
            latency_ms=2000,
        )
    )
    # gate passed but output wrong-shape — still a failure, must be excluded.
    store.record_run(
        _record(
            condition="full",
            gate_passed=True,
            output_in_range=False,
            latency_ms=90000,
        )
    )
    print_latency_summary(conn)
    output = capsys.readouterr().out

    assert "2.0" in output
    assert "90.0" not in output
    assert "Excluded: 1" in output


# ---------------------------------------------------------------------------
# Empty / degenerate cases
# ---------------------------------------------------------------------------


def test_empty_db_prints_no_successful_runs(capsys):
    store, conn = _open_in_memory_store()
    print_latency_summary(conn)
    output = capsys.readouterr().out
    assert "no successful runs" in output
    assert "Excluded: 0" in output


def test_only_failed_runs_prints_no_successful_runs(capsys):
    store, conn = _open_in_memory_store()
    store.record_run(
        _record(
            condition="full",
            gate_passed=False,
            output_in_range=False,
            latency_ms=5000,
        )
    )
    store.record_run(
        _record(
            condition="no_optimization",
            gate_passed=True,
            output_in_range=False,
            latency_ms=8000,
        )
    )
    print_latency_summary(conn)
    output = capsys.readouterr().out
    assert "no successful runs" in output
    assert "Excluded: 2" in output


# ---------------------------------------------------------------------------
# Stdev edge cases — NaN-safe on n=1
# ---------------------------------------------------------------------------


def test_stdev_n_equals_one_renders_dash(capsys):
    """Sample stdev over a single value is NULL — render as '-' not 'nan'."""
    store, conn = _open_in_memory_store()
    store.record_run(
        _record(
            condition="full",
            gate_passed=True,
            output_in_range=True,
            latency_ms=2000,
        )
    )
    print_latency_summary(conn)
    output = capsys.readouterr().out

    assert "nan" not in output.lower()
    # There should be a dash somewhere in the row for stdev.
    lines = [l for l in output.splitlines() if "full" in l.lower() or "Full" in l]
    assert any("-" in l for l in lines) or "      -" in output


def test_stdev_n_equals_two_computes_real_number(capsys):
    store, conn = _open_in_memory_store()
    # 1000ms + 3000ms → mean=2.0, stdev_sample = sqrt(2000000) ms = ~1414ms = 1.4s.
    store.record_run(
        _record(
            condition="full",
            gate_passed=True,
            output_in_range=True,
            latency_ms=1000,
        )
    )
    store.record_run(
        _record(
            condition="full",
            gate_passed=True,
            output_in_range=True,
            latency_ms=3000,
        )
    )
    print_latency_summary(conn)
    output = capsys.readouterr().out
    assert "1.4" in output  # stdev_sec


# ---------------------------------------------------------------------------
# is_pilot filter
# ---------------------------------------------------------------------------


def test_pilot_rows_excluded_from_summary(capsys):
    store, conn = _open_in_memory_store()
    # Pilot row — should be completely invisible (not summarized, not in excluded count).
    store.record_run(
        _record(
            condition="full",
            gate_passed=True,
            output_in_range=True,
            latency_ms=99999,
            is_pilot=True,
        )
    )
    store.record_run(
        _record(
            condition="full",
            gate_passed=True,
            output_in_range=True,
            latency_ms=2000,
        )
    )
    print_latency_summary(conn)
    output = capsys.readouterr().out

    assert "99.9" not in output  # pilot row's latency must not surface
    assert "2.0" in output  # real row's latency must surface
    assert "Excluded: 0" in output  # pilot row isn't "excluded" — it's invisible
