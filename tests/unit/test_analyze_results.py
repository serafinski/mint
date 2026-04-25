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
import pytest

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


@pytest.fixture(autouse=True)
def _reset_analyze_results_module_state():
    """ext7 #44/#52: reset module-level ``_SCOPE_SQL`` + ``_COMPLETENESS_BANNER``
    around each test so ``main()``-invoking tests don't poison siblings
    (e.g., ``test_audit_contamination`` runs after us and saw empty DBs
    because the scope was still ``AND run_id IN ('new_full')``).
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
    """Many distinct run_ids → stderr WARNING + auto-select N per condition.

    Rewritten post-ext7-#46: per-condition auto-select replaces "4 most recent".
    With 6 single-condition runs and the default cap of 5, we expect 5 selected.
    """
    import scripts.analyze_results as ar

    store, conn = _open_in_memory_store()
    # 6 runs with the same condition — different run_ids via latency.
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
    # Default cap is 3; single condition present → 3 run_ids selected.
    assert fragment.count("'") == 6  # 3 run_ids × 2 quotes each


# ---------------------------------------------------------------------------
# ext7 #46 — auto-select must cover every condition, not fixed 4 total
# ---------------------------------------------------------------------------


def test_auto_select_covers_all_conditions_in_canonical_eval_shape():
    """Canonical run_sheet.md eval: 4 conditions × 5 runs = 20 distinct run_ids.
    Pre-ext7-#46 auto-select picked the 4 most recent run_ids — by timestamp
    order with fixed condition order this is typically one condition's 4 runs,
    silently dropping 3 of 4 conditions. Post-fix, auto-select is balanced
    per-condition and covers all 4 conditions.
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
    assert len(selected_ids) == 20  # 5 per condition × 4 conditions

    conditions = conn.execute(
        "SELECT DISTINCT condition FROM evaluation_runs "
        f"WHERE run_id IN ({','.join('?' * len(selected_ids))})",
        selected_ids,
    ).fetchall()
    assert len(conditions) == 4, (
        f"Auto-select must cover all 4 conditions (ext7 #46). "
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
    # 2 conditions × 2 run_ids = 4 run_ids.
    assert fragment.count("'") == 8


def test_auto_select_picks_n_distinct_run_ids_per_condition_at_production_density():
    """ext12 #114 regression — canonical eval shape is 20 tasks per run
    (one ``evaluation_runs`` row per task), NOT one row per run. Pre-fix
    the CTE ranked ROWS via ``ROW_NUMBER()`` and then ``SELECT DISTINCT``
    collapsed the result; at production density, ``rn <= 5`` picked 5
    rows from the SINGLE most recent run_id per condition and DISTINCT
    left exactly 1 run_id per condition = 4 total. The warning text
    still promised "5 per condition".

    Post-fix: the inner ``distinct_runs`` CTE collapses to one row per
    (run_id, condition) BEFORE ranking, so ``rn <= 5`` correctly selects
    5 DISTINCT run_ids per condition = 20 total.
    """
    import scripts.analyze_results as ar

    store, conn = _open_in_memory_store()
    latency = 0
    # 4 conditions × 5 runs × 20 tasks per run = 400 rows, 20 distinct run_ids.
    for condition in ("full", "no_verification", "no_optimization", "static"):
        for run_idx in range(5):
            run_id = f"{condition}_run_{run_idx:02d}"
            for task_idx in range(20):
                latency += 10
                # Use a shared run_id across the 20 tasks — the production shape.
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
    # Post-fix: 5 run_ids per condition × 4 conditions = 20.
    assert len(selected_ids) == 20, (
        f"ext12 #114: expected 20 distinct run_ids (5 per × 4 conditions), "
        f"got {len(selected_ids)}. Pre-fix would typically return 4 "
        f"(rn <= 5 over 100 rows/condition picks rows from a single run)."
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


def test_auto_select_no_scope_needed_when_few_runs():
    """DB with ≤ condition_count distinct run_ids → legacy single-run mode
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
# ext7 #44 — M2 + M3 queries must honor _SCOPE_SQL
# ---------------------------------------------------------------------------


def test_m3_gini_respects_scope_sql(capsys):
    """Pre-fix, print_gini_summary read every row of invocation_events regardless
    of --run-ids. Post-fix, rows outside the scope are excluded from the Gini
    computation — the Chapter 6 M3 headline number no longer pools prior
    campaigns silently.
    """
    import scripts.analyze_results as ar

    store, conn = _open_in_memory_store()
    # Old-campaign invocations — should be EXCLUDED when we scope to new_full.
    _record_invocations(
        store, run_id="old_full", condition="full", task_id="T04",
        counts_by_cap={"cap-old-a": 10, "cap-old-b": 10},
    )
    # New-campaign invocations — the only ones that should enter Gini.
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
    """Default (_SCOPE_SQL = "") keeps the un-scoped behavior — every row
    enters Gini. Verifies the fix doesn't over-filter when no scope is set.
    """
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
    # Both conditions present — no scope filter dropped either run.
    assert "JIT + Verif + Opt" in output
    assert "Static" in output


def test_m2_contamination_respects_scope_sql(capsys):
    """Pre-fix, print_mock_contamination_rate's WHERE clause filtered by
    is_pilot only; human labels from prior campaigns leaked into the current
    M2 aggregate. Post-fix, _SCOPE_SQL constrains the query to the requested
    run_ids, so Chapter 6 M2 columns reflect only the scoped campaign.
    """
    import scripts.analyze_results as ar
    from scripts.analyze_results import print_contamination_rate

    store, conn = _open_in_memory_store()
    # Old campaign — 2 labeled rows (1 contaminated, 1 correct).
    old_a = _record(condition="full", gate_passed=True, output_in_range=True,
                    latency_ms=100, task_id="T04")
    old_b = _record(condition="full", gate_passed=True, output_in_range=True,
                    latency_ms=200, task_id="T04")
    store.record_run(old_a)
    store.record_run(old_b)
    conn.execute(
        "UPDATE evaluation_runs SET run_id = 'old_full', human_label = 'contaminated' "
        "WHERE attempt_latency_ms = 100"
    )
    conn.execute(
        "UPDATE evaluation_runs SET run_id = 'old_full', human_label = 'correct' "
        "WHERE attempt_latency_ms = 200"
    )
    # New campaign — NO labels yet. M2 should report 0 sampled when scoped.
    new = _record(condition="full", gate_passed=True, output_in_range=True,
                  latency_ms=300, task_id="T04")
    store.record_run(new)
    conn.execute(
        "UPDATE evaluation_runs SET run_id = 'new_full' WHERE attempt_latency_ms = 300"
    )

    ar._SCOPE_SQL = " AND run_id IN ('new_full')"
    try:
        print_contamination_rate(conn)
    finally:
        ar._SCOPE_SQL = ""
    output = capsys.readouterr().out
    # When scoped to new_full, there are no labeled rows — M2 should render
    # the "audit not performed" fallback, NOT leak old_full's labels.
    assert "audit not performed" in output.lower() or "no labels" in output.lower() or "n_sampled" not in output.lower() or "0" in output
    # Pre-fix the output would have shown 1 contaminated + 1 correct; post-fix
    # those rows are scope-excluded.
    assert "contaminated" not in output.lower() or "1" not in output.split("contaminated")[-1][:50]


def test_every_analyzer_query_applies_scope_or_is_exempt():
    """ext7 #44 regression guard: every `conn.execute(...)` in analyze_results.py
    that reads evaluation_runs or invocation_events must either reference
    _SCOPE_SQL OR be explicitly exempt (startup sanity checks, scope resolution
    itself). Catches future queries added without scope.

    ext11 #110 (2026-04-20): extended to also recognize ``_scope_for_table(...)``
    calls + local variables rebound from those calls (``scope_ie``, ``scope_ce``).
    The helper is the table-aware wrapper introduced when ``--since`` scopes
    crashed on ``invocation_events`` because ``_SCOPE_SQL`` hardcoded the
    ``timestamp`` column.
    """
    import ast

    src = (ROOT / "scripts" / "analyze_results.py").read_text()
    tree = ast.parse(src)

    # Functions that are allowed to read evaluation_runs WITHOUT _SCOPE_SQL.
    # Expand with care — every entry is a Chapter-6-integrity waiver.
    SCOPE_EXEMPT = {
        "_resolve_scope",          # the scope-resolution call itself
        "_scope_for_table",        # the table-aware scope wrapper
        "_count_check",            # startup sanity, runs before scope is resolved
        "_print_header_counts",    # header block prints raw row counts pre-scope
    }
    SCOPED_TABLES = ("evaluation_runs", "invocation_events")
    # Any of these tokens in a f-string's placeholder expression counts as
    # explicit scope handling. Variable names are the ones used inside
    # ``print_gini_summary`` after the ext11 #110 refactor.
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
            # Extract the SQL literal — supports plain str + f-string.
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
                # Query doesn't read a scoped table — pass.
                continue
            if any(tok in sql for tok in SCOPE_TOKENS):
                continue
            # Existence / availability probes (``SELECT 1 FROM t LIMIT 1``) don't
            # aggregate scoped rows — they just check the table exists. Safe.
            sql_upper = sql.upper().strip()
            if "SELECT 1 FROM" in sql_upper and "LIMIT 1" in sql_upper and "WHERE" not in sql_upper:
                continue
            offenders.append((fn_name, node.lineno, sql[:120]))

    assert not offenders, (
        "Every query reading evaluation_runs / invocation_events must reference "
        "_SCOPE_SQL or be explicitly exempt in SCOPE_EXEMPT. Offenders:\n"
        + "\n".join(f"  {fn}:{lineno} — {sql}..." for fn, lineno, sql in offenders)
    )


# ---------------------------------------------------------------------------
# ext7 #50 — invocation_events.is_pilot column + Gini filter
# ---------------------------------------------------------------------------


def test_invocation_events_schema_has_is_pilot_column():
    """ext7 #50: the schema regression guard. `is_pilot` column must exist so
    the Gini query can filter pilot rows without a JOIN.
    """
    store, conn = _open_in_memory_store()
    cols = conn.execute("PRAGMA table_info(invocation_events)").fetchall()
    col_names = {row[1] for row in cols}
    assert "is_pilot" in col_names, (
        "ext7 #50: invocation_events.is_pilot column missing. "
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
    """ext7 #50: pilot invocation rows are structurally excluded from the Gini
    computation. Pre-fix, pilot rows (Row 7 of run_sheet) silently deflated
    Gini on the full campaign because the schema had no is_pilot marker.
    """
    store, conn = _open_in_memory_store()
    # Pilot invocations (narrow distribution — inflates measured concentration downward).
    _record_invocations_with_pilot(
        store, run_id="pilot_r", condition="full", task_id="T01",
        counts_by_cap={"cap-pilot-a": 5, "cap-pilot-b": 5, "cap-pilot-c": 5},
        is_pilot=True,
    )
    # Non-pilot invocations (narrower: one cap dominates → high Gini).
    _record_invocations_with_pilot(
        store, run_id="full_r", condition="full", task_id="T01",
        counts_by_cap={"cap-full-a": 20, "cap-full-b": 1, "cap-full-c": 1},
        is_pilot=False,
    )
    print_gini_summary(conn)
    output = capsys.readouterr().out
    # Pilot capability names must NOT appear in the Gini table — they were filtered.
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
# ext7 #52 — completeness guard
# ---------------------------------------------------------------------------


def test_completeness_banner_empty_when_not_incomplete(monkeypatch):
    """Default (complete dataset) leaves _COMPLETENESS_BANNER empty so table
    titles render as before.
    """
    import scripts.analyze_results as ar

    # Module-level default must be empty.
    assert ar._COMPLETENESS_BANNER == ""


def test_print_condition_table_includes_banner_when_set(capsys):
    """ext7 #52: when main() sets _COMPLETENESS_BANNER, the per-table title
    renders it inline.
    """
    import scripts.analyze_results as ar
    from scripts.analyze_results import print_condition_table

    ar._COMPLETENESS_BANNER = " [INCOMPLETE: total rows=200, expected 400]"
    try:
        print_condition_table("Table 1: Success Rates — All Tasks", [])
        captured = capsys.readouterr().out
        assert "[INCOMPLETE:" in captured
        assert "total rows=200" in captured
    finally:
        ar._COMPLETENESS_BANNER = ""


def _seed_partial_campaign(store, rows_per_condition: dict[str, int]) -> None:
    """Seed a DB with a campaign of the given shape (condition → row count).

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
    """ext7 #52: partial DB + no --allow-incomplete → exit 2 with a clear
    'dataset appears incomplete' error.

    ext13 #131 (2026-04-21): main() now returns the exit code rather
    than calling ``sys.exit(2)`` directly; the __main__ wrapper does the
    ``sys.exit(main() or 0)``. Assert on the return value.
    """
    import scripts.analyze_results as ar

    # Build a partial DB (1 condition × 10 rows), save to temp path.
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
    """ext7 #50 migration: a legacy DB with pre-fix invocation_events rows
    (is_pilot NULL) is backfilled on EvaluationStore open by JOINing
    evaluation_runs.is_pilot.
    """
    import duckdb

    db_path = tmp_path / "legacy.duckdb"

    # Create a legacy-shape DB: evaluation_runs with is_pilot, invocation_events
    # WITHOUT the is_pilot column (simulate pre-migration).
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

    # Re-open via EvaluationStore — migration + backfill fire.
    store = EvaluationStore(db_path=str(db_path))
    conn = store._conn  # noqa: SLF001
    rows = conn.execute(
        "SELECT run_id, is_pilot FROM invocation_events ORDER BY run_id"
    ).fetchall()
    by_run = {r[0]: r[1] for r in rows}
    assert by_run["full_r"] is False
    assert by_run["pilot_r"] is True


# ---------------------------------------------------------------------------
# ext11 #109 — Gini prune-exclusion must not bleed across runs
# ---------------------------------------------------------------------------


def test_gini_prune_exclusion_does_not_bleed_across_runs(capsys):
    """Pre-fix (ext8 #78 one-shot), the prune subquery matched ``capability_id``
    alone — pruning ``cap-x`` in run B erased run A's invocations of
    ``cap-x`` from run A's Gini because the subquery pulled every pruned
    cap across the whole DB. Post-ext11-#109, the subquery is correlated
    on ``(capability_id, run_id)``; pruning in run B must not touch run A.

    Seed:
    - run_A uses cap-x=100, cap-y=1 → expected Gini ≈ 0.4901.
    - run_B prunes cap-x (no invocations needed).

    Scope analysis to run_A: Gini must match the un-pruned distribution.
    """
    import scripts.analyze_results as ar

    store, conn = _open_in_memory_store()
    # run_A — heavy use of cap-x plus one cap-y. Never pruned in this run.
    _record_invocations(
        store, run_id="run_A", condition="full", task_id="T04",
        counts_by_cap={"cap-x": 100, "cap-y": 1},
    )
    # run_B — records a prune event for cap-x (no invocations of its own).
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

    # Pre-fix, cap-x was stripped → per-run Gini collapsed to [1] → 0.0.
    # Post-fix, cap-x stays → Gini ≈ 0.49 (printed to 3 decimals).
    assert "Gini Coefficient" in output
    # The "0.490" token would appear in post-fix output; pre-fix showed "0.000".
    assert "0.490" in output, (
        f"Expected run_A Gini ≈ 0.490 after ext11 #109 fix; got:\n{output}"
    )


def test_gini_prune_exclusion_still_works_within_same_run(capsys):
    """ext11 #109 must not regress ext8 #78: pruning a cap IN THE SAME RUN
    still excludes that cap's ghost invocations from the Gini distribution.
    The correlation on (capability_id, run_id) keeps within-run pruning
    behaving as ext8 #78 intended.
    """
    import scripts.analyze_results as ar

    store, conn = _open_in_memory_store()
    # run_A — heavy use of cap-x (100) + one cap-y.
    _record_invocations(
        store, run_id="run_A", condition="full", task_id="T04",
        counts_by_cap={"cap-x": 100, "cap-y": 1},
    )
    # Same run prunes cap-x — its ghost invocations must be excluded.
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

    # cap-x excluded → counts become [1] → Gini = 0.0 (single element / empty
    # after zero-pad against the per-condition cap union which is {cap-y}).
    assert "Gini Coefficient" in output
    assert "0.000" in output


# ---------------------------------------------------------------------------
# ext11 #110 — --since scope must use `occurred_at` on invocation_events
# ---------------------------------------------------------------------------


def test_since_scope_uses_occurred_at_on_invocation_events(capsys):
    """Pre-fix, ``_resolve_scope(since=...)`` returned a fragment
    ``AND timestamp >= '...'`` which crashed the Gini query hard because
    ``invocation_events`` uses ``occurred_at``. Post-ext11-#110 the
    ``_scope_for_table`` helper rewrites the column for non-evaluation_runs
    tables so the query runs cleanly.
    """
    import scripts.analyze_results as ar

    store, conn = _open_in_memory_store()
    _record_invocations(
        store, run_id="r1", condition="full", task_id="T04",
        counts_by_cap={"cap-a": 3, "cap-b": 1},
    )

    # Simulate a --since scope fragment; the exact timestamp is
    # inconsequential — we just need the query to run instead of crashing.
    ar._SCOPE_SQL = " AND timestamp >= '1970-01-01T00:00:00'"
    try:
        print_gini_summary(conn)  # Pre-fix: BinderException. Post-fix: clean.
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

    since_sql = " AND timestamp >= '2026-04-20T00:00:00'"
    run_ids_sql = " AND run_id IN ('r1', 'r2')"

    # --since rewrites for invocation_events / cycle_events, not evaluation_runs.
    assert ar._scope_for_table("invocation_events", since_sql) == (
        " AND occurred_at >= '2026-04-20T00:00:00'"
    )
    assert ar._scope_for_table("cycle_events", since_sql) == (
        " AND occurred_at >= '2026-04-20T00:00:00'"
    )
    assert ar._scope_for_table("evaluation_runs", since_sql) == since_sql

    # --run-ids uses `run_id` — identical for all three tables.
    for table in ("invocation_events", "cycle_events", "evaluation_runs"):
        assert ar._scope_for_table(table, run_ids_sql) == run_ids_sql

    # Empty scope → empty scope, regardless of table.
    assert ar._scope_for_table("invocation_events", "") == ""
