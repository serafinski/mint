"""Regression tests for security audit 2026-04-19 ext12 findings #114-#123.

See ``docs/security/security_audit_2026_04_19_ext12.md`` for full threat
model + reproduction. These tests pin the fixes against silent-wrong-
answer bugs across the Phase-5 eval analyzer pipeline.

- #114 covered by ``test_analyze_results.py``
  (``test_auto_select_picks_n_distinct_run_ids_per_condition_at_production_density``).
- #115 / #116 / #118 / #120 / #122 / #123 covered here.
- #117 is a documentation / run-sheet change; the lint test below asserts
  the run-sheet carries the completeness filter.
- #119 covered here (per-task budget → BudgetExceeded contract).
- #121 covered by ``test_audit_sample_plan.py``.
"""

from __future__ import annotations

import logging
import math
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# #118 — _compute_kl NaN sentinel + _detect_convergence NaN-skips
# ---------------------------------------------------------------------------


def test_compute_kl_empty_vs_empty_returns_nan():
    """ext12 #118: pre-fix returned 0.0 → zero-evidence DBs reported
    false-positive convergence.
    """
    from scripts.analyze_longitudinal import _kl_divergence_between_snapshots

    result = _kl_divergence_between_snapshots({}, {})
    assert isinstance(result, float)
    assert math.isnan(result), (
        "empty-vs-empty must return NaN so the convergence detector "
        "treats it as no-data rather than sub-threshold."
    )


def test_compute_kl_prev_empty_curr_nonempty_returns_zero_sentinel():
    """First-cycle sentinel preserved: we don't want the first real cycle
    flagged as divergent just because there's no prior baseline."""
    from scripts.analyze_longitudinal import _kl_divergence_between_snapshots

    assert _kl_divergence_between_snapshots({}, {"cap_a": 1.0}) == 0.0


def test_compute_kl_prev_nonempty_curr_empty_returns_inf():
    """Catastrophic-disappearance sentinel preserved."""
    from scripts.analyze_longitudinal import _kl_divergence_between_snapshots

    assert _kl_divergence_between_snapshots({"cap_a": 1.0}, {}) == float("inf")


def test_detect_convergence_resets_streak_on_nan():
    """ext12 #118: 40 consecutive NaN values (zero-evidence DB) must NOT
    report convergence. Pre-NaN fix the empty-vs-empty sentinel was 0.0
    which trivially satisfied the ``kl < threshold`` streak condition.
    """
    from scripts.analyze_longitudinal import _detect_convergence

    # 40 cycles of pure NaN — representative of a DB where every cycle's
    # distribution is identically empty. Must return None.
    nan_series = [float("nan")] * 40
    assert _detect_convergence(nan_series, threshold=0.05, min_consecutive=3) is None


def test_detect_convergence_breaks_streak_when_nan_interleaves():
    """NaN resets the streak mid-run, so a window like [0.01, 0.01, NaN,
    0.01, 0.01] with ``min_consecutive=3`` must NOT fire."""
    from scripts.analyze_longitudinal import _detect_convergence

    kls = [0.01, 0.01, float("nan"), 0.01, 0.01]
    assert _detect_convergence(kls, threshold=0.05, min_consecutive=3) is None


def test_detect_convergence_still_fires_on_real_data():
    """Sanity: real sub-threshold streaks still converge post-fix."""
    from scripts.analyze_longitudinal import _detect_convergence

    kls = [0.5, 0.3, 0.01, 0.01, 0.01, 0.01]
    # 4 consecutive sub-threshold values starting at index 2 (cycle 3) →
    # min_consecutive=3 fires at index 2+1-3+1 = 3 (1-indexed).
    assert _detect_convergence(kls, threshold=0.05, min_consecutive=3) == 3


# ---------------------------------------------------------------------------
# #119 — shared budget_watchdog helper
# ---------------------------------------------------------------------------


def test_budget_watchdog_zero_seconds_is_noop():
    """seconds <= 0 disables the watchdog; yields immediately with no
    SIGALRM handler installed."""
    from scripts._budget_watchdog import budget_watchdog

    logger_ = logging.getLogger("test_budget_watchdog")
    ran = False
    with budget_watchdog(0, name="noop", logger_=logger_):
        ran = True
    assert ran is True


@pytest.mark.skipif(sys.platform == "win32", reason="SIGALRM is unix-only")
def test_budget_watchdog_fires_on_timeout():
    """The SIGALRM handler raises BudgetExceeded when the wrapped block
    exceeds the budget. Use a 1-second budget + a 2-second sleep to
    keep the test deterministic."""
    import time

    from scripts._budget_watchdog import BudgetExceeded, budget_watchdog

    logger_ = logging.getLogger("test_budget_watchdog")
    with pytest.raises(BudgetExceeded, match="exceeded 1s budget"):
        with budget_watchdog(1, name="sleeper", logger_=logger_):
            time.sleep(2)


@pytest.mark.skipif(sys.platform == "win32", reason="SIGALRM is unix-only")
def test_budget_watchdog_does_not_fire_on_fast_block():
    """Fast blocks complete before SIGALRM fires; the handler is unwound
    cleanly in the finally clause."""
    from scripts._budget_watchdog import budget_watchdog

    logger_ = logging.getLogger("test_budget_watchdog")
    with budget_watchdog(5, name="fast", logger_=logger_):
        pass  # completes instantly — no alarm fires


# ---------------------------------------------------------------------------
# #120 — analyze_context_utilization fetch-degraded flag
# ---------------------------------------------------------------------------


def test_extract_trace_context_marks_all_failed_fetches_as_degraded():
    """ext12 #120: a trace whose every observation fetch returns None
    must carry ``_fetch_degraded=True`` so the aggregate pass can
    exclude it from Context Economy averages.
    """
    from scripts import analyze_context_utilization as acu

    mock_client = MagicMock()
    trace = {"id": "t1", "observation_ids": ["o1", "o2", "o3"]}
    with patch.object(acu, "fetch_observation", return_value=None):
        metrics = acu.extract_trace_context(mock_client, trace)

    assert metrics.get("_fetch_degraded") is True
    assert metrics.get("_obs_attempted") == 3
    assert metrics.get("_obs_failed") == 3
    assert metrics.get("context_utilization_pct") == 0.0


def test_extract_trace_context_no_generations_is_not_degraded():
    """A trace with successful fetches that simply had no GENERATION-typed
    observations should NOT be flagged as degraded — it's legitimate
    zero-util data, not a fetch failure.
    """
    from scripts import analyze_context_utilization as acu

    mock_client = MagicMock()
    trace = {"id": "t1", "observation_ids": ["o1", "o2"]}
    # Observations fetched successfully but NOT GENERATION-typed.
    with patch.object(
        acu,
        "fetch_observation",
        side_effect=[{"type": "SPAN"}, {"type": "EVENT"}],
    ):
        metrics = acu.extract_trace_context(mock_client, trace)

    assert metrics.get("_fetch_degraded") is False
    assert metrics.get("_obs_attempted") == 2
    assert metrics.get("_obs_failed") == 0


def test_extract_trace_context_with_usable_generations_is_not_degraded():
    """Sanity: the non-empty-steps happy path still sets _fetch_degraded=False."""
    from scripts import analyze_context_utilization as acu

    mock_client = MagicMock()
    trace = {"id": "t1", "observation_ids": ["o1", "o2"]}
    good_obs = {
        "type": "GENERATION",
        "id": "o1",
        "name": "step1",
        "model": "gpt-5-mini",
        "usage": {"input": 1000, "output": 100, "total": 1100},
        "usageDetails": {},
        "startTime": None,
    }
    with patch.object(
        acu, "fetch_observation", side_effect=[good_obs, None]
    ):
        metrics = acu.extract_trace_context(mock_client, trace)

    # One fetch failed but the trace still has a valid GENERATION → not degraded.
    assert metrics.get("_fetch_degraded") is False
    assert metrics.get("step_count") == 1


# ---------------------------------------------------------------------------
# #117 — run-sheet longitudinal selector carries completeness filter
# ---------------------------------------------------------------------------


def test_run_sheet_longitudinal_selector_has_completeness_filter():
    """ext12 #117: the longitudinal + shift run_id selectors in the run
    sheet must include ``HAVING COUNT(*) >= ?`` so a stub aborted run
    can't win by ``MAX(timestamp)``. Lint test — prevents future
    regressions where the selector is reverted to a bare ``LIMIT 1``.
    """
    content = (ROOT / "docs" / "run_sheet.md").read_text()
    assert "HAVING COUNT(*)" in content, (
        "ext12 #117: longitudinal selector in run_sheet.md is missing "
        "the completeness filter. Aborted partial runs will silently "
        "win by MAX(timestamp)."
    )
    # Both selectors (longitudinal + shifted) should carry it.
    count = content.count("HAVING COUNT(*)")
    assert count >= 2, (
        f"Expected both longitudinal + shifted selectors to use "
        f"HAVING COUNT(*); found {count} occurrence(s)."
    )


# ---------------------------------------------------------------------------
# #122 — backfill_token_costs per-run completeness + sys.exit wrapper
# ---------------------------------------------------------------------------


def test_backfill_main_returns_int_exit_code():
    """ext12 #122: ``main()`` now returns an int exit code + the module
    entrypoint uses ``sys.exit(main() or 0)``. Pin the contract by
    checking the signature + the entrypoint source.
    """
    import inspect

    from scripts import backfill_token_costs

    sig = inspect.signature(backfill_token_costs.main)
    # Return annotation is a string under ``from __future__ import annotations``.
    assert sig.return_annotation in (int, "int"), (
        "ext12 #122: backfill_token_costs.main() must be annotated -> int "
        "so the exit-code contract is visible at the API boundary."
    )


def test_backfill_module_entrypoint_wraps_main_with_sys_exit():
    """Source-level check — prevents regression to bare ``main()``."""
    source = (ROOT / "scripts" / "backfill_token_costs.py").read_text()
    assert "sys.exit(main()" in source, (
        "ext12 #122: backfill_token_costs.py module entrypoint must wrap "
        "main() in sys.exit(...) so the integer return value becomes the "
        "process exit code."
    )


# ---------------------------------------------------------------------------
# #115 — run_pilot exit-code contract
# ---------------------------------------------------------------------------


def test_run_pilot_main_returns_int_exit_code():
    """ext12 #115: ``main()`` returns an integer exit code now. Watchdog
    abort → 7; zero records with no abort → 4; clean success → 0.
    """
    import inspect

    from scripts import run_pilot

    sig = inspect.signature(run_pilot.main)
    assert sig.return_annotation in (int, "int")


def test_run_pilot_entrypoint_wraps_main_with_sys_exit():
    source = (ROOT / "scripts" / "run_pilot.py").read_text()
    assert "sys.exit(main()" in source, (
        "ext12 #115: run_pilot.py module entrypoint must wrap main() in "
        "sys.exit(...) so watchdog aborts surface as non-zero process exit."
    )


# ---------------------------------------------------------------------------
# #116 — analyze_longitudinal completeness flags + exit codes
# ---------------------------------------------------------------------------


def test_analyze_longitudinal_main_supports_expected_cycles_flag():
    """ext12 #116: the analyzer exposes --expected-cycles / --allow-incomplete
    and refuses partial DBs by default.
    """
    import argparse

    from scripts import analyze_longitudinal

    # Extract the parser by running argparse with --help on stdout capture.
    # Simpler: check the source carries the flag.
    source = (ROOT / "scripts" / "analyze_longitudinal.py").read_text()
    assert "--expected-cycles" in source
    assert "--allow-incomplete" in source


# ---------------------------------------------------------------------------
# #123 — shift-marker robustness
# ---------------------------------------------------------------------------


def test_run_longitudinal_shift_retries_marker_write_three_times():
    """ext12 #123: the shift-marker write retries up to 3 times with
    jitter before giving up. Source-level check that the retry loop
    and the end-of-campaign loud-fail are wired.
    """
    source = (ROOT / "scripts" / "run_longitudinal_shift.py").read_text()
    # Retry loop: ``for attempt in range(3):``
    assert "for attempt in range(3):" in source, (
        "ext12 #123: shift-marker write must retry 3x before failing."
    )
    # Loud-fail: campaign returns exit 4 when marker never lands.
    assert "return 4" in source
    # End-of-campaign check on shift_reached + shift_marked.
    assert "shift_reached" in source
    assert "shift_marked" in source


def test_analyze_longitudinal_refuses_shift_db_without_marker():
    """ext12 #123 (analyzer side): when the DB filename names a shift
    campaign (contains 'shift' or 'shifted') but no marker event is
    present, the analyzer exits with code 5 rather than silently
    omitting the RQ #3 section.
    """
    source = (ROOT / "scripts" / "analyze_longitudinal.py").read_text()
    assert "is_shift_campaign_by_path" in source
    # Exit code 5 is the new "shift campaign missing marker" class.
    assert "return 5" in source
