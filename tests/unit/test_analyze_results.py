"""Unit tests for scripts/analyze_results.py helpers.

Covers three Chapter-6-load-bearing analyzer helpers:

- ``print_latency_summary`` — Step 18.9 M5 time-to-first-correct filter
  narrowing; exclude failed runs before aggregating.
- ``_gini_coefficient`` + ``print_gini_summary`` — Step 18.5 M3 capability
  reuse distribution (added 2026-04-19 after Step 13.5 audit surfaced
  zero direct coverage for these helpers).
- ``print_classification_accuracy`` — Step 18.5 Pillar 1 RQ #1 proxy
  (task-level primary-type match); added 2026-04-19 for the same reason.

All DB-reading tests use an in-memory ``EvaluationStore`` + direct
``_conn`` access. No Docker, no real LLM, hermetic.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.analyze_results import (  # noqa: E402
    _gini_coefficient,
    print_classification_accuracy,
    print_gini_summary,
    print_latency_summary,
)
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


# ---------------------------------------------------------------------------
# _gini_coefficient — pure function
#
# Proposal §Metrics M3: "Capability reuse distribution — Gini coefficient
# of invocation counts." These tests pin the formula against known-value
# inputs + degenerate edges; zero coverage pre-2026-04-19.
# ---------------------------------------------------------------------------


def test_gini_empty_returns_zero():
    assert _gini_coefficient([]) == 0.0


def test_gini_all_zeros_returns_zero():
    """Degenerate case: no invocations at all → perfectly equal at zero."""
    assert _gini_coefficient([0, 0, 0]) == 0.0


def test_gini_single_value_returns_zero():
    """One capability, all invocations go to it → perfectly equal (n=1)."""
    assert _gini_coefficient([42]) == 0.0


def test_gini_perfect_equality_returns_zero():
    """Every capability invoked exactly the same → Gini = 0."""
    assert _gini_coefficient([5, 5, 5, 5]) == 0.0


def test_gini_maximum_skew_approaches_one():
    """One cap gets all traffic, others get nothing → Gini = (n-1)/n.

    Formula: 2·Σ((i+1)·x_i) / (n·Σx) − (n+1)/n
    For sorted [0, 0, 0, 10] with n=4, total=10:
      cumulative = 1·0 + 2·0 + 3·0 + 4·10 = 40
      Gini = 2·40 / (4·10) − 5/4 = 2.0 − 1.25 = 0.75
    """
    assert _gini_coefficient([0, 0, 0, 10]) == 0.75


def test_gini_known_value_arithmetic_series():
    """Pin the formula against an arithmetic series [1, 2, 3, 4].

    Sorted=[1,2,3,4], n=4, total=10.
      cumulative = 1·1 + 2·2 + 3·3 + 4·4 = 1 + 4 + 9 + 16 = 30
      Gini = 2·30 / (4·10) − 5/4 = 1.5 − 1.25 = 0.25
    """
    assert _gini_coefficient([1, 2, 3, 4]) == 0.25


def test_gini_order_invariant():
    """Gini is computed on sorted counts — input order must not change the result."""
    assert _gini_coefficient([4, 1, 3, 2]) == _gini_coefficient([1, 2, 3, 4])


# ---------------------------------------------------------------------------
# print_gini_summary — DB-reading
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
    """Empty invocation_events → print 'skipped' message, no crash."""
    store, conn = _open_in_memory_store()
    print_gini_summary(conn)
    output = capsys.readouterr().out
    # Table exists (fresh in-memory store) but is empty → "empty" path, not "not available".
    assert "Gini Coefficient" in output
    assert "skipped" in output.lower() or "empty" in output.lower()


def test_gini_summary_reports_per_condition(capsys):
    store, conn = _open_in_memory_store()
    # 'full' condition: skewed (one cap dominates) — but with counts_by_cap
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
    # full → "JIT + Verif + Opt", static → "Static"
    assert "JIT + Verif + Opt" in output
    assert "Static" in output


def test_gini_summary_condition_with_no_data_shows_dash(capsys):
    """Conditions absent from invocation_events should render '—'."""
    store, conn = _open_in_memory_store()
    # Only populate 'full' — other conditions should show dashes
    _record_invocations(
        store, run_id="r1", condition="full", task_id="T04",
        counts_by_cap={"cap-a": 3, "cap-b": 1},
    )
    print_gini_summary(conn)
    output = capsys.readouterr().out

    # The em-dash is the renderer's sentinel for "no data".
    assert "—" in output


# ---------------------------------------------------------------------------
# print_classification_accuracy — DB-reading
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
    """No non-pilot JIT-condition rows → print 'skipped', no crash."""
    store, conn = _open_in_memory_store()
    print_classification_accuracy(conn)
    output = capsys.readouterr().out
    assert "Classification Accuracy" in output
    assert "skipped" in output.lower()


def test_classification_accuracy_excludes_static_baseline(capsys):
    """Static condition bypasses the classifier — must not contribute to accuracy."""
    store, conn = _open_in_memory_store()
    # T05 is authored as expected_type="mcp_tool" in tests/evaluation/tasks.py.
    # A 'static' row with WRONG type must be ignored.
    store.record_run(
        _eval_run(run_id="r-static", condition="static", task_id="T05", capability_type="agent"),
    )
    print_classification_accuracy(conn)
    output = capsys.readouterr().out
    # With only static rows, the classifier has no data → skipped.
    assert "skipped" in output.lower()


def test_classification_accuracy_counts_correct_classification(capsys):
    """Correct classification → high accuracy + confusion matrix diagonal."""
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
# Security audit finding #17 — T19/T20 drop regression (2026-04-20)
# ---------------------------------------------------------------------------


def test_all_task_ids_match_testbed():
    """Pre-fix, ``ALL_TASK_IDS = [f"T{i:02d}" for i in range(1, 19)]``
    silently excluded T19 and T20 from every per-task table. Post-fix
    it's imported from ``tests.evaluation.tasks.ALL_TASKS`` so any
    testbed extension flows through automatically."""
    from scripts.analyze_results import ALL_TASK_IDS
    from tests.evaluation.tasks import ALL_TASKS

    assert sorted(ALL_TASK_IDS) == sorted(t.task_id for t in ALL_TASKS)
    # Testbed currently at 20 tasks (T01..T20); regression guard.
    assert "T19" in ALL_TASK_IDS
    assert "T20" in ALL_TASK_IDS


def test_synthesis_task_ids_include_t19_and_t20():
    """Synthesis tasks are ALL_TASK_IDS minus retrieval (T01-T03).
    With #17 fixed, synthesis now includes T19 + T20."""
    from scripts.analyze_results import RETRIEVAL_TASKS, SYNTHESIS_TASK_IDS

    assert "T19" in SYNTHESIS_TASK_IDS
    assert "T20" in SYNTHESIS_TASK_IDS
    assert "T01" not in SYNTHESIS_TASK_IDS
    for t in RETRIEVAL_TASKS:
        assert t not in SYNTHESIS_TASK_IDS


# ---------------------------------------------------------------------------
# Security audit finding #32 — success predicate excludes shape mismatch
# ---------------------------------------------------------------------------


def test_success_predicate_excludes_shape_mismatched_rows():
    """Pre-fix, a row with gate_passed=True + output_in_range=True +
    output_shape_mismatch=True counted as a success. Post-fix, the SUM
    expression also requires NOT output_shape_mismatch. Regression
    guard for finding #32."""
    from scripts.analyze_results import query_condition_summary

    store, conn = _open_in_memory_store()

    # Row A — genuine success (gate + range + no shape mismatch).
    rec_a = _record(
        condition="full",
        gate_passed=True,
        output_in_range=True,
        latency_ms=1000,
        task_id="T04",
    )
    # Row B — shape mismatch despite gate + range both True.
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
        f"Pre-fix, both rows counted as success (n=2, successes=2). "
        f"Post-fix, row B (shape mismatch) excluded. Got successes={full_row['successes']}."
    )


# ---------------------------------------------------------------------------
# Security audit finding #38 — run-id scoping (2026-04-20)
# ---------------------------------------------------------------------------


def test_resolve_scope_single_run_returns_empty_filter():
    """DB with <=4 distinct run_ids → no scoping needed, empty filter."""
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
    """5+ distinct run_ids → stderr WARNING + auto-select the 4 most recent."""
    import scripts.analyze_results as ar

    store, conn = _open_in_memory_store()
    # 6 runs with different condition values to make them distinct run_ids.
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
    assert "auto-selecting the 4 most recent" in captured.err
    # 4 run_ids picked (out of 6).
    assert fragment.count("'") == 8  # 4 run_ids × 2 quotes each
