"""ext8 #58 — backfill retrieval-task exclusion gaps.

Bug A: untagged-run fallback leaked JIT tokens into T01-T03 (retrieval tasks)
  → M4 over-reported retrieval cost + under-reported JIT cost.

Bug B: ``n_jit_tasks`` computed by blind subtraction (``n_distinct_tasks -
  len(RETRIEVAL_TASKS)``) collapsed partial runs without the 3 retrieval
  tasks to 0 → ``tokens_per_jit_task = 0`` in the run summary table.

Both fixes tested against synthetic records + summaries (no LangFuse).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.backfill_token_costs import (  # noqa: E402
    RETRIEVAL_TASKS,
    compute_per_task_costs,
)


def _mock_fetch_per_run_traces(*, tagged: bool = False, total_per_task: int = 1000):
    """Return a stub fetcher that either marks runs as untagged (fallback
    path) or tagged (per-task attribution).
    """
    def _inner(records, base_url=None, auth=None, run_summaries=None):
        # Mirror the real compute_per_task_costs early-out signature.
        return None if not tagged else [(r["id"], total_per_task) for r in records]
    return _inner


def test_bug_a_untagged_run_fallback_zeros_retrieval_tasks(monkeypatch):
    """Untagged run with mixed retrieval + JIT records: retrieval rows
    MUST get 0 tokens; JIT rows get the run_total divided by JIT-count.

    Pre-ext8 Bug A: retrieval tasks received ``run_total // n_records``,
    sharing JIT tokens they didn't incur. Test synthesizes the post-fetch
    state and drives compute_per_task_costs's fallback path.
    """
    import scripts.backfill_token_costs as bt

    records = [
        {"id": "a", "run_id": "r1", "task_id": "T01",
         "condition": "full"},  # retrieval
        {"id": "b", "run_id": "r1", "task_id": "T02",
         "condition": "full"},  # retrieval
        {"id": "c", "run_id": "r1", "task_id": "T05",
         "condition": "full"},  # JIT
        {"id": "d", "run_id": "r1", "task_id": "T07",
         "condition": "full"},  # JIT
    ]
    run_summaries = {
        "r1": {"total_tokens": 1000, "n_tasks": 4, "n_distinct_tasks": 4,
               "condition": "full"},
    }

    # Force the per-run tag probe to return untagged (empty tagged set)
    # by monkey-patching fetch_traces_by_session to return [].
    monkeypatch.setattr(
        bt, "fetch_traces_by_session", lambda base_url, auth, rid: []
    )

    updates = compute_per_task_costs(
        "http://stub", ("u", "p"), records,
        run_windows=[{"run_id": "r1", "condition": "full",
                      "n_distinct_tasks": 4, "actual_task_ids": set()}],
        run_summaries=run_summaries,
    )
    # compute_per_task_costs returns None when NO run has tags AT ALL.
    # That's the "pure fallback" branch the audit describes.
    assert updates is None

    # Exercise the alternative: at least one tagged run exists so the
    # mixed-mode fallback path fires for r1 as untagged. The fix should
    # still zero retrieval tasks.
    records2 = list(records) + [
        {"id": "e", "run_id": "r2", "task_id": "T05", "condition": "full"},
    ]
    run_summaries["r2"] = {
        "total_tokens": 500, "n_tasks": 1, "n_distinct_tasks": 1,
        "condition": "full",
    }

    def _fetch(base_url, auth, rid):
        # r2 is tagged (returns a trace with task_id). r1 is untagged.
        if rid == "r2":
            return [{"id": "trace-r2", "task_id": "T05"}]
        return []

    monkeypatch.setattr(bt, "fetch_traces_by_session", _fetch)
    monkeypatch.setattr(bt, "fetch_observations_for_trace", lambda *a, **k: 500)

    updates = compute_per_task_costs(
        "http://stub", ("u", "p"), records2,
        run_windows=[
            {"run_id": "r1", "condition": "full", "n_distinct_tasks": 4,
             "actual_task_ids": {"T01", "T02", "T05", "T07"}},
            {"run_id": "r2", "condition": "full", "n_distinct_tasks": 1,
             "actual_task_ids": {"T05"}},
        ],
        run_summaries=run_summaries,
    )
    assert updates is not None
    d = dict(updates)
    # r1 retrieval rows (T01, T02) → 0 tokens (ext8 #58 Bug A fix).
    assert d["a"] == 0
    assert d["b"] == 0
    # r1 JIT rows split run_total 1000 across 2 JIT records (T05, T07) = 500 each.
    assert d["c"] == 500
    assert d["d"] == 500
    # r2 is tagged — T05 gets its total attributed directly.
    assert d["e"] == 500


def test_bug_b_n_jit_tasks_reflects_actual_run_contents():
    """Run with tasks={T11, T12, T13} (3 JIT, 0 retrieval): n_jit_tasks=3,
    not ``3 - 3 = 0``. Pre-fix this collapsed tokens_per_jit_task to 0.
    """
    from scripts.backfill_token_costs import compute_run_costs

    # Synthetic rw: partial run with JIT-only tasks.
    # compute_run_costs needs full LangFuse state so we stub a minimal
    # happy path.
    # We only care about the n_jit_tasks arithmetic. Build the run_rows
    # and generations maps directly and call compute_run_costs.

    # The function expects:
    #   run_rows: [{run_id, condition, start_ts, end_ts, n_tasks,
    #               n_distinct_tasks, actual_task_ids}, ...]
    #   by_run_generations: {run_id: [...]}
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    run_rows = [{
        "run_id": "partial-run",
        "condition": "full",
        "start_ts": now,
        "end_ts": now,
        "n_tasks": 3,
        "n_distinct_tasks": 3,
        "actual_task_ids": {"T11", "T12", "T13"},
    }]
    by_run_generations = {
        "partial-run": [{
            "model": "gpt-5-mini", "prompt_tokens": 100,
            "completion_tokens": 50, "total_tokens": 150,
            "calculated_total_cost": 0.001,
        }],
    }
    summaries = compute_run_costs(by_run_generations, run_rows)
    assert summaries["partial-run"]["n_jit_tasks"] == 3, (
        "ext8 #58 Bug B: a partial run with 3 JIT tasks + 0 retrieval must "
        "report n_jit_tasks=3, not 0. Pre-fix blind subtraction produced 0 "
        "and tokens_per_jit_task collapsed to 0."
    )
    # Divisor > 0 → per-jit-task cost is populated.
    assert summaries["partial-run"]["tokens_per_jit_task"] == 150 // 3


def test_bug_b_mixed_partial_run_counts_only_jit():
    """Run with tasks={T01, T05, T07}: n_jit_tasks=2 (T05, T07), not 0."""
    from datetime import UTC, datetime

    from scripts.backfill_token_costs import compute_run_costs

    now = datetime.now(UTC)
    run_rows = [{
        "run_id": "mixed-partial",
        "condition": "full",
        "start_ts": now,
        "end_ts": now,
        "n_tasks": 3,
        "n_distinct_tasks": 3,
        "actual_task_ids": {"T01", "T05", "T07"},
    }]
    by_run_generations = {"mixed-partial": []}
    summaries = compute_run_costs(by_run_generations, run_rows)
    assert summaries["mixed-partial"]["n_jit_tasks"] == 2


def test_bug_b_full_run_still_reports_correct_n_jit_tasks():
    """20-task canonical run with all 3 retrieval tasks: n_jit_tasks=17."""
    from datetime import UTC, datetime

    from scripts.backfill_token_costs import compute_run_costs

    now = datetime.now(UTC)
    all_20 = {f"T{i:02d}" for i in range(1, 21)}
    run_rows = [{
        "run_id": "full-run",
        "condition": "full",
        "start_ts": now,
        "end_ts": now,
        "n_tasks": 20,
        "n_distinct_tasks": 20,
        "actual_task_ids": all_20,
    }]
    summaries = compute_run_costs({"full-run": []}, run_rows)
    assert summaries["full-run"]["n_jit_tasks"] == 20 - len(RETRIEVAL_TASKS)


def test_bug_b_legacy_caller_without_actual_task_ids_uses_fallback():
    """If a caller (e.g., older test) doesn't populate ``actual_task_ids``,
    fall back to the subtract-and-clamp heuristic. No regression on legacy
    callers that pass only ``n_distinct_tasks``.
    """
    from datetime import UTC, datetime

    from scripts.backfill_token_costs import compute_run_costs

    now = datetime.now(UTC)
    run_rows = [{
        "run_id": "legacy-run",
        "condition": "full",
        "start_ts": now,
        "end_ts": now,
        "n_tasks": 20,
        "n_distinct_tasks": 20,
        # actual_task_ids deliberately omitted
    }]
    summaries = compute_run_costs({"legacy-run": []}, run_rows)
    assert summaries["legacy-run"]["n_jit_tasks"] == 20 - len(RETRIEVAL_TASKS)
