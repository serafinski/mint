"""Unit tests for scripts/analyze_results.py helpers.

Covers analyzer helpers used by the evaluation reporting protocol:

- ``print_latency_summary``: exclude failed runs before aggregating
  time-to-first-correct.
- ``_gini_coefficient`` + ``print_gini_summary``: report capability reuse
  concentration from invocation events.
- ``print_classification_accuracy``: report task-level primary-type matches.

All DB-reading tests use an in-memory ``EvaluationStore`` + direct
``_conn`` access. No Docker, no real LLM, hermetic.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from scripts.analyze_results import (
    _gini_coefficient,
    print_classification_accuracy,
    print_gini_summary,
    print_latency_summary,
)
from src.registry.evaluation_store import EvaluationStore
from src.schemas.evaluation import EvaluationRecord
from tests.unit._script_subprocess import run_project_script, seed_evaluation_runs

ROOT = Path(__file__).parent.parent.parent


@pytest.fixture(autouse=True)
def _reset_analyze_results_module_state():
    """Reset mutable analyzer module state around each test.

    Tests that invoke ``main()`` or set scope/banner globals must not leak
    those values into sibling tests.
    """
    import scripts.analyze_results as _ar

    saved_scope = _ar._SCOPE_SQL
    saved_banner = _ar._COMPLETENESS_BANNER
    try:
        yield
    finally:
        _ar._SCOPE_SQL = saved_scope
        _ar._COMPLETENESS_BANNER = saved_banner


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
    return store, store._conn  # noqa: SLF001 - test-only access


def test_analyze_results_help_smoke():
    """The analyzer script should parse --help without import errors."""
    result = run_project_script("analyze_results.py", "--help", timeout=15)

    assert result.returncode == 0
    assert "--allow-missing-metrics" in result.stdout


def _canonical_eval_rows(*, repeated_task_id: str | None = None) -> list[dict]:
    """Return a complete 4-condition × 5-run × 20-task eval shape."""
    base_ts = datetime(2026, 4, 21, 10, 0, 0)
    conditions = ("full", "no_verification", "no_optimization", "static")
    rows: list[dict] = []
    for condition_index, condition in enumerate(conditions):
        for run_index in range(5):
            for task_index in range(20):
                rows.append(
                    {
                        "run_id": f"{condition}_run_{run_index}",
                        "condition": condition,
                        "task_id": repeated_task_id or f"T{task_index + 1:02d}",
                        "timestamp": base_ts
                        + timedelta(
                            seconds=condition_index * 10_000
                            + run_index * 100
                            + task_index
                        ),
                    }
                )
    return rows


def test_analyze_results_exits_7_when_invocation_metrics_missing(tmp_path):
    """A complete eval DB with no invocation metrics must fail loudly."""
    db = tmp_path / "canonical_no_inv.duckdb"
    seed_evaluation_runs(db, rows=_canonical_eval_rows())

    result = run_project_script(
        "analyze_results.py",
        "--db",
        str(db),
        "--per-condition-cap",
        "5",
        "--expected-runs-per-condition",
        "5",
    )

    assert result.returncode == 7, (
        f"Expected exit 7 for missing metrics, got {result.returncode}. "
        f"stderr:\n{result.stderr[-600:]}\nstdout:\n{result.stdout[-600:]}"
    )
    assert "CHAPTER 6 INCOMPLETE" in result.stderr
    assert "M3" in result.stderr


def test_allow_missing_metrics_bypasses_exit_7(tmp_path):
    """The explicit override lets diagnostic runs continue without M3 rows."""
    db = tmp_path / "canonical_allow.duckdb"
    seed_evaluation_runs(db, rows=_canonical_eval_rows())

    result = run_project_script(
        "analyze_results.py",
        "--db",
        str(db),
        "--per-condition-cap",
        "5",
        "--allow-missing-metrics",
        "--expected-runs-per-condition",
        "5",
    )

    assert result.returncode == 0, (
        f"--allow-missing-metrics should bypass exit 7. Got "
        f"{result.returncode}. stderr:\n{result.stderr[-500:]}"
    )


def test_completeness_guard_refuses_repeated_single_task_shape(tmp_path):
    """A row-complete DB still fails if it repeats one task instead of T01-T20."""
    db = tmp_path / "t01_only.duckdb"
    seed_evaluation_runs(db, rows=_canonical_eval_rows(repeated_task_id="T01"))

    result = run_project_script(
        "analyze_results.py",
        "--db",
        str(db),
        "--per-condition-cap",
        "5",
        "--expected-runs-per-condition",
        "5",
    )

    assert result.returncode == 2, (
        f"Expected exit 2 for distinct-task coverage gap, got {result.returncode}. "
        f"stderr:\n{result.stderr[-800:]}"
    )
    combined = result.stderr + result.stdout
    assert "distinct tasks" in combined


# ---------------------------------------------------------------------------
# Filter: failed runs must be excluded
# ---------------------------------------------------------------------------


def test_filter_excludes_failed_runs(capsys):
    store, conn = _open_in_memory_store()
    # Two successful runs at 1000ms and 3000ms: mean should be 2.0s.
    # One failed run at 60000ms must not pollute the mean.
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
            gate_passed=False,  # failed: must be excluded
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
    """gate_passed=True alone is not enough; output_in_range must also be True."""
    store, conn = _open_in_memory_store()
    store.record_run(
        _record(
            condition="full",
            gate_passed=True,
            output_in_range=True,
            latency_ms=2000,
        )
    )
    # Gate passed but output is wrong-shape; still a failure, must be excluded.
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
    assert print_latency_summary(conn) is False
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
# Stdev edge cases - NaN-safe on n=1
# ---------------------------------------------------------------------------


def test_stdev_n_equals_one_renders_dash(capsys):
    """Sample stdev over a single value is NULL; render as '-' not 'nan'."""
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
    lines = [
        line for line in output.splitlines()
        if "full" in line.lower() or "Full" in line
    ]
    assert any("-" in line for line in lines) or "      -" in output


def test_stdev_n_equals_two_computes_real_number(capsys):
    store, conn = _open_in_memory_store()
    # 1000ms + 3000ms gives mean=2.0s and sample stdev about 1.4s.
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
    # Pilot row should be invisible, not summarized or counted as excluded.
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
    assert "Excluded: 0" in output  # pilot row is invisible, not excluded


# ---------------------------------------------------------------------------
# _gini_coefficient - pure function
#
# These tests pin the formula against known-value inputs and degenerate edges.
# ---------------------------------------------------------------------------


def test_gini_empty_returns_zero():
    assert _gini_coefficient([]) == 0.0


def test_gini_all_zeros_returns_zero():
    """Degenerate case: no invocations at all is perfectly equal at zero."""
    assert _gini_coefficient([0, 0, 0]) == 0.0


def test_gini_single_value_returns_zero():
    """One capability with all invocations is perfectly equal for n=1."""
    assert _gini_coefficient([42]) == 0.0


def test_gini_perfect_equality_returns_zero():
    """Every capability invoked exactly the same has Gini = 0."""
    assert _gini_coefficient([5, 5, 5, 5]) == 0.0


def test_gini_maximum_skew_approaches_one():
    """One cap gets all traffic, others get nothing: Gini = (n-1)/n.

    Formula: 2 * sum((i + 1) * x_i) / (n * sum(x)) - (n + 1) / n
    For sorted [0, 0, 0, 10] with n=4, total=10:
      cumulative = 1*0 + 2*0 + 3*0 + 4*10 = 40
      Gini = 2*40 / (4*10) - 5/4 = 2.0 - 1.25 = 0.75
    """
    assert _gini_coefficient([0, 0, 0, 10]) == 0.75


def test_gini_known_value_arithmetic_series():
    """Pin the formula against an arithmetic series [1, 2, 3, 4].

    Sorted=[1,2,3,4], n=4, total=10.
      cumulative = 1*1 + 2*2 + 3*3 + 4*4 = 1 + 4 + 9 + 16 = 30
      Gini = 2*30 / (4*10) - 5/4 = 1.5 - 1.25 = 0.25
    """
    assert _gini_coefficient([1, 2, 3, 4]) == 0.25


def test_gini_order_invariant():
    """Gini is computed on sorted counts; input order must not change the result."""
    assert _gini_coefficient([4, 1, 3, 2]) == _gini_coefficient([1, 2, 3, 4])


# ---------------------------------------------------------------------------
# print_gini_summary - DB-reading
# ---------------------------------------------------------------------------


def _record_invocations(
    store: EvaluationStore,
    *,
    run_id: str,
    condition: str,
    task_id: str,
    counts_by_cap: dict[str, int],
) -> None:
    """Helper: insert N invocation_events for each (cap, count) pair."""
    events = []
    for cap_id, n in counts_by_cap.items():
        for _ in range(n):
            events.append({"capability_id": cap_id, "success": True})
    store.record_invocation_events(
        run_id=run_id, condition=condition, task_id=task_id, events=events,
    )


def test_gini_summary_skipped_when_table_empty(capsys):
    """Empty invocation_events prints a skipped message and does not crash."""
    store, conn = _open_in_memory_store()
    print_gini_summary(conn)
    output = capsys.readouterr().out
    # Table exists but is empty: use the "empty" path, not "not available".
    assert "Gini Coefficient" in output
    assert "skipped" in output.lower() or "empty" in output.lower()


def test_gini_summary_reports_per_condition(capsys):
    store, conn = _open_in_memory_store()
    # 'full' condition: skewed (one cap dominates), but with counts_by_cap
    # collapsing to list [10, 0, 0] per-run, skew is limited by how many
    # (cap, count>0) pairs exist; the GROUP BY in print_gini_summary filters
    # zero-count rows, so give each cap at least 1 invocation to exercise the
    # per-run skew path meaningfully.
    _record_invocations(
        store, run_id="r1", condition="full", task_id="T04",
        counts_by_cap={"cap-a": 10, "cap-b": 1, "cap-c": 1},
    )
    _record_invocations(
        store, run_id="r2", condition="static", task_id="T04",
        counts_by_cap={"cap-x": 5, "cap-y": 5, "cap-z": 5},
    )
    print_gini_summary(conn)
    output = capsys.readouterr().out

    assert "Gini Coefficient" in output
    # Condition labels are rendered via CONDITION_LABELS mapping:
    # full -> "JIT + Verif + Opt", static -> "Static".
    assert "JIT + Verif + Opt" in output
    assert "Static" in output


def test_gini_summary_pools_aggregate_by_capability(capsys):
    """Aggregate Gini pools counts by capability across runs."""
    store, conn = _open_in_memory_store()
    _record_invocations(
        store,
        run_id="r1",
        condition="full",
        task_id="T04",
        counts_by_cap={"cap-a": 100},
    )
    _record_invocations(
        store,
        run_id="r2",
        condition="full",
        task_id="T04",
        counts_by_cap={"cap-b": 100},
    )

    print_gini_summary(conn)
    output = capsys.readouterr().out

    assert "JIT + Verif + Opt" in output
    assert "0.500" in output  # mean per-run Gini after zero-padding
    assert "0.000" in output  # pooled aggregate over {cap-a: 100, cap-b: 100}


def test_gini_summary_condition_with_no_data_shows_dash(capsys):
    """Conditions absent from invocation_events should render an em dash."""
    store, conn = _open_in_memory_store()
    # Only populate 'full'; other conditions should show dashes.
    _record_invocations(
        store, run_id="r1", condition="full", task_id="T04",
        counts_by_cap={"cap-a": 3, "cap-b": 1},
    )
    print_gini_summary(conn)
    output = capsys.readouterr().out

    # The em dash is the renderer's sentinel for "no data".
    assert "\u2014" in output


# ---------------------------------------------------------------------------
# print_classification_accuracy - DB-reading
# ---------------------------------------------------------------------------


def _eval_run(
    *,
    run_id: str,
    condition: str,
    task_id: str,
    capability_type: str,
) -> EvaluationRecord:
    """Minimal EvaluationRecord for classification-accuracy tests."""
    return EvaluationRecord(
        run_id=run_id,
        condition=condition,
        task_id=task_id,
        task_tier="moderate",
        attempt_count=1,
        capability_type=capability_type,
        verification_tier_reached=3,
        gate_passed=True,
        output_in_range=True,
        token_cost=100,
        attempt_latency_ms=1000,
        failure_reason="none",
        registry_snapshot_id="snap-1",
        invocation_record_id=f"inv-{run_id}",
        seed=42,
        is_pilot=False,
    )


def test_classification_accuracy_skipped_when_no_rows(capsys):
    """No non-pilot JIT-condition rows prints skipped and does not crash."""
    store, conn = _open_in_memory_store()
    assert print_classification_accuracy(conn) is False
    output = capsys.readouterr().out
    assert "Classification Accuracy" in output
    assert "skipped" in output.lower()


def test_classification_accuracy_excludes_static_baseline(capsys):
    """Static condition bypasses the classifier and must not affect accuracy."""
    store, conn = _open_in_memory_store()
    # T05 is authored as expected_type="mcp_tool" in tests/evaluation/tasks.py.
    # A 'static' row with WRONG type must be ignored.
    store.record_run(
        _eval_run(run_id="r-static", condition="static", task_id="T05", capability_type="agent"),
    )
    print_classification_accuracy(conn)
    output = capsys.readouterr().out
    # With only static rows, the classifier has no data.
    assert "skipped" in output.lower()


def test_classification_accuracy_counts_correct_classification(capsys):
    """Correct classification renders the accuracy section."""
    store, conn = _open_in_memory_store()
    # T05 expected_type is "mcp_tool". Record 3 matching runs under JIT conditions.
    for i, cond in enumerate(("full", "no_verification", "no_optimization")):
        store.record_run(
            _eval_run(
                run_id=f"r{i}",
                condition=cond,
                task_id="T05",
                capability_type="mcp_tool",
            )
        )
    print_classification_accuracy(conn)
    output = capsys.readouterr().out

    assert "Classification Accuracy" in output
    # Header plus per-condition numbers must render
    # (exact text depends on the renderer; smoke-check the section appears).


# ---------------------------------------------------------------------------
# Task catalog identity
# ---------------------------------------------------------------------------


def test_all_task_ids_match_testbed():
    """Analyzer task IDs should come from the evaluation task catalog."""
    from scripts.analyze_results import ALL_TASK_IDS
    from tests.evaluation.tasks import ALL_TASKS

    assert sorted(ALL_TASK_IDS) == sorted(t.task_id for t in ALL_TASKS)
    # Testbed currently has 20 tasks (T01..T20).
    assert "T19" in ALL_TASK_IDS
    assert "T20" in ALL_TASK_IDS


def test_synthesis_task_ids_include_t19_and_t20():
    """Synthesis tasks are ALL_TASK_IDS minus retrieval (T01-T03).
    The current catalog includes T19 and T20 as synthesis tasks.
    """
    from scripts.analyze_results import RETRIEVAL_TASKS, SYNTHESIS_TASK_IDS

    assert "T19" in SYNTHESIS_TASK_IDS
    assert "T20" in SYNTHESIS_TASK_IDS
    assert "T01" not in SYNTHESIS_TASK_IDS
    for t in RETRIEVAL_TASKS:
        assert t not in SYNTHESIS_TASK_IDS


# ---------------------------------------------------------------------------
# Success predicate excludes shape mismatch
# ---------------------------------------------------------------------------


def test_success_predicate_excludes_shape_mismatched_rows():
    """Shape-mismatched rows are not counted as successes."""
    from scripts.analyze_results import query_condition_summary

    store, conn = _open_in_memory_store()

    # Row A: genuine success (gate + range + no shape mismatch).
    rec_a = _record(
        condition="full",
        gate_passed=True,
        output_in_range=True,
        latency_ms=1000,
        task_id="T04",
    )
    # Row B: shape mismatch despite gate + range both True.
    rec_b = _record(
        condition="full",
        gate_passed=True,
        output_in_range=True,
        latency_ms=2000,
        task_id="T07",
    )
    # Manually set the shape-mismatch flag on row B by writing the row
    # then patching the column. EvaluationStore doesn't accept the field
    # in the record shape, so use a direct update.
    store.record_run(rec_a)
    store.record_run(rec_b)
    conn.execute(
        "UPDATE evaluation_runs SET output_shape_mismatch = TRUE WHERE task_id = 'T07'"
    )

    summary = query_condition_summary(conn)
    full_row = [r for r in summary if r["condition"] == "full"][0]
    assert full_row["n"] == 2
    assert full_row["successes"] == 1, (
        f"Shape-mismatched row should be excluded from success count. "
        f"Got successes={full_row['successes']}."
    )


# ---------------------------------------------------------------------------
# Run-id scoping
# ---------------------------------------------------------------------------


def test_resolve_scope_single_run_returns_empty_filter():
    """DB with <= condition_count distinct run_ids needs no scope filter."""
    import scripts.analyze_results as ar

    store, conn = _open_in_memory_store()
    store.record_run(_record(
        condition="full", gate_passed=True, output_in_range=True, latency_ms=1,
    ))

    assert ar._resolve_scope(conn, run_ids=None, since=None) == ""


def test_resolve_scope_explicit_run_ids_filters_sql(capsys):
    """Explicit --run-ids injects IN clause; queries filtered to that set."""
    import scripts.analyze_results as ar

    store, conn = _open_in_memory_store()
    rec_a = _record(condition="full", gate_passed=True, output_in_range=True,
                    latency_ms=1, task_id="T04")
    rec_b = _record(condition="full", gate_passed=True, output_in_range=True,
                    latency_ms=2, task_id="T04")
    store.record_run(rec_a)
    store.record_run(rec_b)
    # Compare the produced SQL fragment.
    fragment = ar._resolve_scope(conn, run_ids=[rec_a.run_id], since=None)
    assert " AND run_id IN ('" in fragment
    assert rec_a.run_id in fragment
    assert rec_b.run_id not in fragment


def test_resolve_scope_auto_selects_latest_when_many_runs_with_warning(capsys):
    """Many distinct run_ids emit a warning and auto-select per condition.

    With 6 single-condition runs and the default cap of 3, we expect
    3 selected run IDs.
    """
    import scripts.analyze_results as ar

    store, conn = _open_in_memory_store()
    # 6 runs with the same condition, with different run_ids via latency.
    for i in range(6):
        rec = _record(
            condition="full", gate_passed=True, output_in_range=True,
            latency_ms=(i + 1) * 10,
        )
        store.record_run(rec)

    fragment = ar._resolve_scope(conn, run_ids=None, since=None)
    captured = capsys.readouterr()
    # WARNING emitted to stderr.
    assert "WARNING" in captured.err
    assert "per condition" in captured.err
    # Default cap is 3; single condition present means 3 run_ids selected.
    assert fragment.count("'") == 6  # 3 run_ids, 2 quotes each.


# ---------------------------------------------------------------------------
# Auto-select must cover every condition
# ---------------------------------------------------------------------------


def test_auto_select_covers_all_conditions_in_canonical_eval_shape():
    """Balanced auto-select covers all conditions under the current default cap.

    The protocol default is now 3 run_ids per condition, so a canonical
    4-condition / 5-run-per-condition DB should auto-select 12 distinct
    run_ids while still covering all 4 conditions.
    """
    import scripts.analyze_results as ar

    store, conn = _open_in_memory_store()
    latency = 0
    for condition in ("full", "no_verification", "no_optimization", "static"):
        for _ in range(5):
            latency += 10
            rec = _record(
                condition=condition,
                gate_passed=True,
                output_in_range=True,
                latency_ms=latency,
            )
            store.record_run(rec)

    ar._resolve_scope(conn, run_ids=None, since=None)
    # The selected run_ids must cover all 4 conditions.
    # Re-query against the scope the resolver just set up.
    fragment = ar._resolve_scope(conn, run_ids=None, since=None)
    # Strip the leading " AND run_id IN (" and trailing ")".
    inner = fragment.split("IN (", 1)[1].rstrip(")")
    selected_ids = [s.strip().strip("'") for s in inner.split(",")]
    assert len(selected_ids) == 12  # 3 per condition, 4 conditions.

    conditions = conn.execute(
        "SELECT DISTINCT condition FROM evaluation_runs "
        f"WHERE run_id IN ({','.join('?' * len(selected_ids))})",
        selected_ids,
    ).fetchall()
    assert len(conditions) == 4, (
        f"Auto-select must cover all 4 conditions. "
        f"Got {len(conditions)}: {[c[0] for c in conditions]}"
    )


def test_auto_select_respects_per_condition_cap():
    """--per-condition-cap N limits how many run_ids per condition are selected."""
    import scripts.analyze_results as ar

    store, conn = _open_in_memory_store()
    latency = 0
    for condition in ("full", "static"):
        for _ in range(5):
            latency += 10
            rec = _record(
                condition=condition, gate_passed=True, output_in_range=True,
                latency_ms=latency,
            )
            store.record_run(rec)

    fragment = ar._resolve_scope(
        conn, run_ids=None, since=None, per_condition_cap=2,
    )
    # 2 conditions, 2 run_ids each = 4 run_ids.
    assert fragment.count("'") == 8


def test_auto_select_picks_n_distinct_run_ids_per_condition_at_production_density():
    """Auto-select ranks distinct run IDs, not task rows.

    The canonical eval shape has one ``evaluation_runs`` row per task.
    This test uses production density to ensure ``per_condition_cap=5``
    selects 5 distinct run IDs for each of 4 conditions.
    """
    import scripts.analyze_results as ar

    store, conn = _open_in_memory_store()
    latency = 0
    # 4 conditions, 5 runs, 20 tasks per run = 400 rows, 20 distinct run_ids.
    for condition in ("full", "no_verification", "no_optimization", "static"):
        for run_idx in range(5):
            run_id = f"{condition}_run_{run_idx:02d}"
            for task_idx in range(20):
                latency += 10
                # Use a shared run_id across the 20 tasks: the production shape.
                rec = _record(
                    condition=condition,
                    gate_passed=True,
                    output_in_range=True,
                    latency_ms=latency,
                    task_id=f"T{task_idx + 1:02d}",
                )
                rec.run_id = run_id
                store.record_run(rec)

    fragment = ar._resolve_scope(conn, run_ids=None, since=None, per_condition_cap=5)
    inner = fragment.split("IN (", 1)[1].rstrip(")")
    selected_ids = [s.strip().strip("'") for s in inner.split(",")]
    # 5 run_ids per condition, 4 conditions = 20.
    assert len(selected_ids) == 20, (
        f"Expected 20 distinct run_ids (5 per condition, 4 conditions), "
        f"got {len(selected_ids)}."
    )
    # Per-condition balance check.
    by_cond: dict[str, int] = {}
    for rid in selected_ids:
        cond = rid.rsplit("_run_", 1)[0]
        by_cond[cond] = by_cond.get(cond, 0) + 1
    for cond, count in by_cond.items():
        assert count == 5, (
            f"Condition {cond}: expected 5 distinct run_ids, got {count}"
        )


def test_analyze_results_auto_select_warning_at_production_density(tmp_path):
    """Subprocess smoke: script reports selected run IDs, not task rows."""
    db = tmp_path / "metrics.duckdb"
    base_ts = datetime(2026, 4, 20, 10, 0, 0)
    rows: list[dict] = []
    for condition_index, condition in enumerate(
        ("full", "no_verification", "no_optimization", "static")
    ):
        for run_index in range(8):
            run_id = f"{condition}_run_{run_index:02d}"
            for task_index in range(20):
                rows.append(
                    {
                        "run_id": run_id,
                        "condition": condition,
                        "task_id": f"T{task_index + 1:02d}",
                        "timestamp": base_ts
                        + timedelta(
                            seconds=condition_index * 10_000
                            + run_index * 100
                            + task_index
                        ),
                    }
                )
    seed_evaluation_runs(db, rows=rows)

    result = run_project_script(
        "analyze_results.py",
        "--db",
        str(db),
        "--per-condition-cap",
        "5",
        timeout=60,
    )

    combined = result.stdout + result.stderr
    assert "auto-selected 20 total" in combined, (
        "Expected warning to report 20 selected run_ids. Got:\n"
        f"{combined[:3000]}"
    )
    assert "across 4 conditions" in combined


def test_auto_select_no_scope_needed_when_few_runs():
    """DB with <= condition_count distinct run_ids uses empty scope
    (empty scope). The auto-select only kicks in when there are more run_ids
    than conditions.
    """
    import scripts.analyze_results as ar

    store, conn = _open_in_memory_store()
    # 4 conditions, 1 run each = 4 run_ids. No scoping needed.
    for condition in ("full", "no_verification", "no_optimization", "static"):
        rec = _record(
            condition=condition, gate_passed=True, output_in_range=True,
            latency_ms=100,
        )
        store.record_run(rec)

    assert ar._resolve_scope(conn, run_ids=None, since=None) == ""


# ---------------------------------------------------------------------------
# M3 queries must honor _SCOPE_SQL
# ---------------------------------------------------------------------------


def test_m3_gini_respects_scope_sql(capsys):
    """Rows outside the active run-id scope are excluded from Gini."""
    import scripts.analyze_results as ar

    store, conn = _open_in_memory_store()
    # Old-campaign invocations should be excluded when scoped to new_full.
    _record_invocations(
        store, run_id="old_full", condition="full", task_id="T04",
        counts_by_cap={"cap-old-a": 10, "cap-old-b": 10},
    )
    # New-campaign invocations are the only rows that should enter Gini.
    _record_invocations(
        store, run_id="new_full", condition="full", task_id="T04",
        counts_by_cap={"cap-new-a": 5, "cap-new-b": 5, "cap-new-c": 5},
    )
    # Scope to new_full only.
    ar._SCOPE_SQL = " AND run_id IN ('new_full')"
    try:
        print_gini_summary(conn)
    finally:
        ar._SCOPE_SQL = ""
    output = capsys.readouterr().out
    # New-campaign cap names appear in the aggregate; old ones must not
    # influence the per-run Gini distributional shape reporting.
    assert "Gini Coefficient" in output
    # Number of per-run groups in the scoped Gini must be 1 (just new_full).
    # The helper prints "Runs aggregated: N" in its per-condition detail block.
    # We assert the total ACTIVE run count by checking that the old run_id's
    # unique capability IDs do not appear in the summary.
    assert "cap-old" not in output


def test_m3_gini_ignores_scope_when_empty(capsys):
    """Default (_SCOPE_SQL = "") keeps the un-scoped behavior."""
    import scripts.analyze_results as ar

    store, conn = _open_in_memory_store()
    _record_invocations(
        store, run_id="r1", condition="full", task_id="T04",
        counts_by_cap={"cap-a": 3, "cap-b": 3, "cap-c": 3},
    )
    _record_invocations(
        store, run_id="r2", condition="static", task_id="T04",
        counts_by_cap={"cap-x": 1, "cap-y": 1},
    )
    ar._SCOPE_SQL = ""
    print_gini_summary(conn)
    output = capsys.readouterr().out
    # Both conditions present; no scope filter dropped either run.
    assert "JIT + Verif + Opt" in output
    assert "Static" in output


def test_every_analyzer_query_applies_scope_or_is_exempt():
    """Every scoped analyzer query must apply scope or be explicitly exempt.

    Every `conn.execute(...)` in analyze_results.py
    that reads evaluation_runs or invocation_events must either reference
    _SCOPE_SQL OR be explicitly exempt (startup sanity checks, scope resolution
    itself). Catches future queries added without scope.

    The guard also recognizes ``_scope_for_table(...)`` calls and local
    variables rebound from those calls (``scope_ie``, ``scope_ce``).
    """
    import ast

    src = (ROOT / "scripts" / "analyze_results.py").read_text()
    tree = ast.parse(src)

    # Functions that are allowed to read evaluation_runs without _SCOPE_SQL.
    # Expand with care; every entry is an integrity waiver.
    SCOPE_EXEMPT = {
        "_resolve_scope",          # the scope-resolution call itself
        "_scope_for_table",        # the table-aware scope wrapper
        "_count_check",            # startup sanity, runs before scope is resolved
        "_print_header_counts",    # header block prints raw row counts pre-scope
    }
    SCOPED_TABLES = ("evaluation_runs", "invocation_events")
    # Any of these tokens in a f-string's placeholder expression counts as
    # explicit scope handling. Variable names are the ones used inside
    # ``print_gini_summary``.
    SCOPE_TOKENS = ("_SCOPE_SQL", "_scope_for_table", "scope_ie", "scope_ce")

    # Collect (enclosing_fn_name, lineno, sql_snippet).
    offenders: list[tuple[str, int, str]] = []

    for fn_node in ast.walk(tree):
        if not isinstance(fn_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        fn_name = fn_node.name
        if fn_name in SCOPE_EXEMPT:
            continue
        for node in ast.walk(fn_node):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute"
                and node.args
            ):
                continue
            # Extract the SQL literal; supports plain str and f-string.
            sql_arg = node.args[0]
            if isinstance(sql_arg, ast.Constant) and isinstance(sql_arg.value, str):
                sql = sql_arg.value
            elif isinstance(sql_arg, ast.JoinedStr):
                # Concatenate all Constant parts of the f-string for keyword search.
                parts = []
                has_scope_placeholder = False
                for v in sql_arg.values:
                    if isinstance(v, ast.Constant) and isinstance(v.value, str):
                        parts.append(v.value)
                    elif isinstance(v, ast.FormattedValue):
                        expr = ast.unparse(v.value)
                        parts.append(f"{{{expr}}}")
                        if any(tok in expr for tok in SCOPE_TOKENS):
                            has_scope_placeholder = True
                sql = "".join(parts)
                if has_scope_placeholder:
                    continue
            else:
                continue

            if not any(tbl in sql for tbl in SCOPED_TABLES):
                # Query doesn't read a scoped table.
                continue
            if any(tok in sql for tok in SCOPE_TOKENS):
                continue
            # Existence probes only check that the table exists.
            sql_upper = sql.upper().strip()
            if "SELECT 1 FROM" in sql_upper and "LIMIT 1" in sql_upper and "WHERE" not in sql_upper:
                continue
            offenders.append((fn_name, node.lineno, sql[:120]))

    assert not offenders, (
        "Every query reading evaluation_runs / invocation_events must reference "
        "_SCOPE_SQL or be explicitly exempt in SCOPE_EXEMPT. Offenders:\n"
        + "\n".join(f"  {fn}:{lineno} - {sql}..." for fn, lineno, sql in offenders)
    )


# ---------------------------------------------------------------------------
# invocation_events.is_pilot column + Gini filter
# ---------------------------------------------------------------------------


def test_invocation_events_schema_has_is_pilot_column():
    """The is_pilot column lets Gini filter pilot rows without a JOIN."""
    store, conn = _open_in_memory_store()
    cols = conn.execute("PRAGMA table_info(invocation_events)").fetchall()
    col_names = {row[1] for row in cols}
    assert "is_pilot" in col_names, (
        "invocation_events.is_pilot column missing. "
        f"Columns present: {sorted(col_names)}"
    )


def test_record_invocation_events_threads_is_pilot():
    """Writer contract: record_invocation_events must persist the is_pilot flag."""
    store, conn = _open_in_memory_store()
    store.record_invocation_events(
        run_id="pilot_run", condition="full", task_id="T01",
        events=[{"capability_id": "cap-a", "success": True}],
        is_pilot=True,
    )
    store.record_invocation_events(
        run_id="full_run", condition="full", task_id="T01",
        events=[{"capability_id": "cap-b", "success": True}],
        is_pilot=False,
    )
    pilot_rows = conn.execute(
        "SELECT COUNT(*) FROM invocation_events WHERE is_pilot = TRUE"
    ).fetchone()[0]
    nonpilot_rows = conn.execute(
        "SELECT COUNT(*) FROM invocation_events WHERE NOT is_pilot"
    ).fetchone()[0]
    assert pilot_rows == 1
    assert nonpilot_rows == 1


def test_m3_gini_excludes_pilot_invocations(capsys):
    """Pilot invocation rows are excluded from the Gini computation."""
    store, conn = _open_in_memory_store()
    # Pilot invocations have a narrow distribution.
    _record_invocations_with_pilot(
        store, run_id="pilot_r", condition="full", task_id="T01",
        counts_by_cap={"cap-pilot-a": 5, "cap-pilot-b": 5, "cap-pilot-c": 5},
        is_pilot=True,
    )
    # Non-pilot invocations have one dominant cap and high Gini.
    _record_invocations_with_pilot(
        store, run_id="full_r", condition="full", task_id="T01",
        counts_by_cap={"cap-full-a": 20, "cap-full-b": 1, "cap-full-c": 1},
        is_pilot=False,
    )
    print_gini_summary(conn)
    output = capsys.readouterr().out
    # Pilot capability names must not appear in the Gini table.
    assert "cap-pilot" not in output


def _record_invocations_with_pilot(
    store, *, run_id: str, condition: str, task_id: str,
    counts_by_cap: dict[str, int], is_pilot: bool,
) -> None:
    events = []
    for cap_id, n in counts_by_cap.items():
        for _ in range(n):
            events.append({"capability_id": cap_id, "success": True})
    store.record_invocation_events(
        run_id=run_id, condition=condition, task_id=task_id,
        events=events, is_pilot=is_pilot,
    )


# ---------------------------------------------------------------------------
# Completeness guard
# ---------------------------------------------------------------------------


def test_completeness_banner_empty_when_not_incomplete(monkeypatch):
    """Default (complete dataset) leaves _COMPLETENESS_BANNER empty so table
    titles render as before.
    """
    import scripts.analyze_results as ar

    # Module-level default must be empty.
    assert ar._COMPLETENESS_BANNER == ""


def test_print_condition_table_includes_banner_when_set(capsys):
    """When main() sets _COMPLETENESS_BANNER, table titles render it inline."""
    import scripts.analyze_results as ar
    from scripts.analyze_results import print_condition_table

    ar._COMPLETENESS_BANNER = " [INCOMPLETE: total rows=200, expected 400]"
    try:
        print_condition_table("Table 1: Success Rates - All Tasks", [])
        captured = capsys.readouterr().out
        assert "[INCOMPLETE:" in captured
        assert "total rows=200" in captured
    finally:
        ar._COMPLETENESS_BANNER = ""


def _seed_partial_campaign(store, rows_per_condition: dict[str, int]) -> None:
    """Seed a DB with a campaign of the given shape (condition to row count).

    Every row is stamped with a single run_id per condition to keep the
    auto-select happy. Task IDs cycle through T01..T05 to spread the load.
    """
    task_cycle = ["T01", "T02", "T03", "T04", "T05"]
    latency = 0
    for condition, n in rows_per_condition.items():
        for i in range(n):
            latency += 1
            store.record_run(_record(
                condition=condition,
                gate_passed=True,
                output_in_range=True,
                latency_ms=latency,
                task_id=task_cycle[i % len(task_cycle)],
            ))


def test_completeness_guard_refuses_partial_dataset(monkeypatch, capsys, tmp_path):
    """Partial DB + no --allow-incomplete returns exit 2.

    The returned exit code is asserted directly instead of catching
    ``SystemExit``.
    """
    import scripts.analyze_results as ar

    # Build a partial DB with 1 condition and 10 rows.
    db_path = tmp_path / "partial.duckdb"
    store = EvaluationStore(db_path=str(db_path))
    _seed_partial_campaign(store, {"full": 10})
    store.close()

    monkeypatch.setattr(sys, "argv", [
        "analyze_results.py", "--db", str(db_path),
        "--expected-tasks", "20", "--expected-runs-per-condition", "5",
    ])

    rc = ar.main()
    assert rc == 2, f"expected exit 2, got {rc}"
    err = capsys.readouterr().err
    assert "dataset appears incomplete" in err


def test_completeness_guard_allow_incomplete_proceeds_with_banner(monkeypatch, capsys, tmp_path):
    """--allow-incomplete proceeds AND sets _COMPLETENESS_BANNER for table titles."""
    import scripts.analyze_results as ar

    db_path = tmp_path / "partial.duckdb"
    store = EvaluationStore(db_path=str(db_path))
    _seed_partial_campaign(store, {"full": 10})
    store.close()

    monkeypatch.setattr(sys, "argv", [
        "analyze_results.py", "--db", str(db_path), "--allow-incomplete",
        "--expected-tasks", "20", "--expected-runs-per-condition", "5",
    ])
    # main() will run through to some later error (Fisher etc on partial data);
    # we only care that the banner got set + the exit code isn't 2.
    try:
        ar.main()
    except SystemExit as exc:
        assert exc.code != 2, f"--allow-incomplete should not exit 2. Got: {exc.code}"
    except Exception:
        pass
    assert "[INCOMPLETE:" in ar._COMPLETENESS_BANNER


def test_invocation_events_is_pilot_backfill_from_evaluation_runs(tmp_path):
    """A DB missing invocation_events.is_pilot is backfilled on open.

    This keeps older DuckDB files readable while preserving the current
    Gini pilot-row filter.
    """
    import duckdb

    db_path = tmp_path / "older_shape.duckdb"

    # Create an older-shape DB: evaluation_runs has is_pilot, while
    # invocation_events does not.
    raw = duckdb.connect(str(db_path))
    raw.execute("""
        CREATE TABLE evaluation_runs (
            run_id VARCHAR, task_id VARCHAR, is_pilot BOOLEAN,
            condition VARCHAR, id VARCHAR
        );
        CREATE TABLE invocation_events (
            id VARCHAR PRIMARY KEY, run_id VARCHAR NOT NULL,
            condition VARCHAR NOT NULL, task_id VARCHAR NOT NULL,
            capability_id VARCHAR NOT NULL, capability_type VARCHAR,
            success BOOLEAN NOT NULL,
            token_cost INTEGER NOT NULL DEFAULT 0,
            latency_ms INTEGER NOT NULL DEFAULT 0,
            occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # Seed: pilot run + full run.
    raw.execute(
        "INSERT INTO evaluation_runs VALUES "
        "('pilot_r', 'T01', TRUE, 'full', 'er1'), "
        "('full_r', 'T01', FALSE, 'full', 'er2')"
    )
    raw.execute(
        "INSERT INTO invocation_events "
        "(id, run_id, condition, task_id, capability_id, success) VALUES "
        "('i1', 'pilot_r', 'full', 'T01', 'cap-a', TRUE), "
        "('i2', 'full_r',  'full', 'T01', 'cap-b', TRUE)"
    )
    raw.close()

    # Re-open via EvaluationStore; migration + backfill fire.
    store = EvaluationStore(db_path=str(db_path))
    conn = store._conn  # noqa: SLF001
    rows = conn.execute(
        "SELECT run_id, is_pilot FROM invocation_events ORDER BY run_id"
    ).fetchall()
    by_run = {r[0]: r[1] for r in rows}
    assert by_run["full_r"] is False
    assert by_run["pilot_r"] is True


# ---------------------------------------------------------------------------
# Gini prune-exclusion must not bleed across runs
# ---------------------------------------------------------------------------


def test_gini_prune_exclusion_does_not_bleed_across_runs(capsys):
    """Pruning in another run must not affect scoped Gini.

    Seed:
    - run_A uses cap-x=100, cap-y=1, so expected Gini is about 0.4901.
    - run_B prunes cap-x (no invocations needed).

    Scope analysis to run_A: Gini must match the un-pruned distribution.
    """
    import scripts.analyze_results as ar

    store, conn = _open_in_memory_store()
    # run_A: heavy use of cap-x plus one cap-y. Never pruned in this run.
    _record_invocations(
        store, run_id="run_A", condition="full", task_id="T04",
        counts_by_cap={"cap-x": 100, "cap-y": 1},
    )
    # run_B records a prune event for cap-x (no invocations of its own).
    store.record_cycle_events(
        run_id="run_B", condition="full", task_id="T04",
        events=[{"capability_id": "cap-x", "event_type": "pruned"}],
    )

    ar._SCOPE_SQL = " AND run_id IN ('run_A')"
    try:
        print_gini_summary(conn)
    finally:
        ar._SCOPE_SQL = ""
    output = capsys.readouterr().out

    # cap-x stays in run_A, so Gini is about 0.49 (printed to 3 decimals).
    assert "Gini Coefficient" in output
    assert "0.490" in output, (
        f"Expected run_A Gini about 0.490; got:\n{output}"
    )


def test_gini_prune_exclusion_still_works_within_same_run(capsys):
    """Pruning a cap in the same run excludes that cap from Gini."""
    import scripts.analyze_results as ar

    store, conn = _open_in_memory_store()
    # run_A: heavy use of cap-x (100) + one cap-y.
    _record_invocations(
        store, run_id="run_A", condition="full", task_id="T04",
        counts_by_cap={"cap-x": 100, "cap-y": 1},
    )
    # Same run prunes cap-x, so its historical invocations are excluded.
    store.record_cycle_events(
        run_id="run_A", condition="full", task_id="T04",
        events=[{"capability_id": "cap-x", "event_type": "pruned"}],
    )

    ar._SCOPE_SQL = " AND run_id IN ('run_A')"
    try:
        print_gini_summary(conn)
    finally:
        ar._SCOPE_SQL = ""
    output = capsys.readouterr().out

    # cap-x excluded: counts become [1] -> Gini = 0.0 (single element / empty
    # after zero-pad against the per-condition cap union which is {cap-y}).
    assert "Gini Coefficient" in output
    assert "0.000" in output


# ---------------------------------------------------------------------------
# --since scope must use occurred_at on invocation_events
# ---------------------------------------------------------------------------


def test_since_scope_uses_occurred_at_on_invocation_events(capsys):
    """Since-scoped Gini rewrites timestamp filters to occurred_at.

    ``invocation_events`` uses ``occurred_at`` rather than ``timestamp``,
    so the table-aware scope helper must rewrite the fragment before the
    query runs.
    """
    import scripts.analyze_results as ar

    store, conn = _open_in_memory_store()
    _record_invocations(
        store, run_id="r1", condition="full", task_id="T04",
        counts_by_cap={"cap-a": 3, "cap-b": 1},
    )

    # Simulate a --since scope fragment; the exact timestamp is
    # inconsequential; we just need the query to run instead of crashing.
    ar._SCOPE_SQL = " AND timestamp >= '1970-01-01T00:00:00'"
    try:
        print_gini_summary(conn)
    finally:
        ar._SCOPE_SQL = ""
    output = capsys.readouterr().out
    assert "Gini Coefficient" in output


def test_scope_for_table_rewrites_since_column_for_occurred_at_tables():
    """Unit-level guard for ``_scope_for_table``: --since fragments are
    rewritten to ``occurred_at`` for the two tables that use that column,
    and untouched for evaluation_runs and for --run-ids / auto-select scopes.
    """
    import scripts.analyze_results as ar

    since_sql = " AND timestamp >= '2000-01-01T00:00:00'"
    run_ids_sql = " AND run_id IN ('r1', 'r2')"

    # --since rewrites for invocation_events / cycle_events, not evaluation_runs.
    assert ar._scope_for_table("invocation_events", since_sql) == (
        " AND occurred_at >= '2000-01-01T00:00:00'"
    )
    assert ar._scope_for_table("cycle_events", since_sql) == (
        " AND occurred_at >= '2000-01-01T00:00:00'"
    )
    assert ar._scope_for_table("evaluation_runs", since_sql) == since_sql

    # --run-ids uses run_id, identical for all three tables.
    for table in ("invocation_events", "cycle_events", "evaluation_runs"):
        assert ar._scope_for_table(table, run_ids_sql) == run_ids_sql

    # Empty scope remains empty, regardless of table.
    assert ar._scope_for_table("invocation_events", "") == ""
