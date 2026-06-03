"""Longitudinal KL convergence analyzer.

Reads ``data/eval/longitudinal.duckdb`` (written by ``scripts/run_longitudinal.py``)
and produces the Chapter 6 convergence figure + cycles-to-convergence number.

Addresses:

- Proposal §Metrics item 6: *"Registry convergence — Number of cycles
  until the granularity distribution stabilizes (measured by KL
  divergence between consecutive registry snapshots)."*
- Proposal §Pillar 3 RQ #1: *"Does evolutionary optimization produce
  meaningfully different granularity distributions than human-designed
  skill libraries?"* (addressed via seeded-vs-evolved Gini comparison)
- Proposal §Pillar 3 RQ #2: *"How many task cycles are required for
  the registry to converge?"*

The primary data source is per-cycle telemetry from EvaluationStore:

- **Utility-score KL** (proposal-literal) when ``cycle_utility_scores``
  is populated — mirrors the online metric in
  ``src/optimizer/loop.py::_compute_kl_divergence``.
- **Per-type op counts per cycle** from ``cycle_events`` (prune / split
  / merge / verification_rollback etc.).
- **Invocation-count KL** (original proxy) is still computed as a
  secondary series when ``invocation_events`` is populated — useful
  for sensitivity comparison with the utility-score KL.

**What this produces (five outputs):**

1. **Cycles-to-convergence** — first cycle N where the consecutive-snapshot
   utility-score KL divergence stays below ``--threshold`` (default 0.05)
   for ``--min-consecutive`` (default 3) cycles in a row. If utility scores
   are missing, the analyzer refuses by default; ``--allow-proxy-kl`` makes
   the invocation-count KL sensitivity path explicit.

2. **Convergence curve** — per-cycle table of ``(cycle, task_id,
   n_active_caps, kl_utility, kl_invocations, gini)``. CSV is written with
   ``--csv`` or ``--plot``; matplotlib PNG is optional via ``--plot``.
   Chapter 6 figure source.

3. **Per-type operation counts per cycle** — prune / split / merge /
   verification_rollback counts from ``cycle_events``. Shows WHAT the
   optimizer is doing as the registry converges.

4. **Gini trajectory** — per-cycle Gini coefficient of invocation
   counts. Proposal predicts a stabilizing moderate Gini (~0.3-0.6).

5. **Seeded-vs-evolved Gini comparison** — Pillar 3 RQ #1. Reports
   the cycle-1 Gini (what the seeded registry produces) vs the final-
   cycle Gini (what evolution produces) with the delta. Non-zero delta
   with stabilization = evolutionary optimization IS changing the
   granularity distribution.

Usage::

    uv run scripts/analyze_longitudinal.py
    uv run scripts/analyze_longitudinal.py --db data/eval/longitudinal.duckdb
    uv run scripts/analyze_longitudinal.py --threshold 0.05 --min-consecutive 3
    uv run scripts/analyze_longitudinal.py --plot --output-dir data/analysis/
    uv run scripts/analyze_longitudinal.py --csv
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

import duckdb
import numpy as np
from scipy.stats import entropy

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.analyze_results import _gini_coefficient  # noqa: E402

DEFAULT_DB = str(ROOT / "data" / "eval" / "longitudinal.duckdb")
DEFAULT_OUTPUT_DIR = ROOT / "data" / "analysis"


def _kl_divergence_between_snapshots(
    prev_dist: dict[str, float],
    curr_dist: dict[str, float],
) -> float:
    """Compute KL(curr || prev) between two capability distributions.

    Works on either:
    - Utility-score distributions (proposal-literal, per §Pillar 3 formal
      framework) — mirrors ``src.optimizer.loop._compute_kl_divergence``.
    - Invocation-count distributions (proxy, used when utility scores
      aren't persisted or as a sensitivity comparison).

    Shift-to-non-negative + epsilon-smoothing + normalization is applied
    so any real-valued input works (utility scores can be negative per
    the U(c) formula; invocation counts are already non-negative).

    Returns:
        - ``float('nan')`` if both distributions are empty
          (no data; the convergence detector must not count this as a
          sub-threshold signal)
        - ``float('inf')`` if prev is non-empty but curr is empty (catastrophic)
        - 0.0 if curr is non-empty but prev is empty (first-cycle sentinel;
          intentional — we don't want the first real cycle flagged as
          divergent just because there's no prior baseline)
        - otherwise the KL divergence as a non-negative float
    """
    if not prev_dist and not curr_dist:
        return float("nan")
    if prev_dist and not curr_dist:
        return float("inf")
    if curr_dist and not prev_dist:
        return 0.0

    all_keys = sorted(set(prev_dist) | set(curr_dist))
    prev = np.array([prev_dist.get(k, 0.0) for k in all_keys], dtype=np.float64)
    curr = np.array([curr_dist.get(k, 0.0) for k in all_keys], dtype=np.float64)

    # Shift to non-negative (utility scores can be negative)
    min_val = min(prev.min(), curr.min())
    if min_val < 0:
        prev = prev - min_val + 1e-10
        curr = curr - min_val + 1e-10

    # epsilon-smooth + normalize
    prev = prev + 1e-10
    curr = curr + 1e-10
    prev = prev / prev.sum()
    curr = curr / curr.sum()

    return float(entropy(curr, prev))


def _table_exists(conn: duckdb.DuckDBPyConnection, table: str) -> bool:
    """Return True if the named table exists + is queryable."""
    try:
        conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
        return True
    except Exception:  # noqa: BLE001
        return False


def _resolve_longitudinal_run_id(
    conn: duckdb.DuckDBPyConnection,
    run_id: str | None,
) -> str | None:
    """Pick the run_id to analyze.

    A DB can contain multiple longitudinal runs from re-runs, appended
    experiments, or different configs. The analyzer must pick exactly one
    run_id: the caller's ``--run-id`` value if provided, otherwise the
    most-recent run with a warning on stderr.

    Returns ``None`` if the DB has no longitudinal runs at all.
    """
    # Enumerate available longitudinal runs (newest first).
    available = conn.execute(
        """
        SELECT run_id, MAX(timestamp) AS last_ts
        FROM evaluation_runs
        WHERE condition = 'longitudinal'
        GROUP BY run_id
        ORDER BY last_ts DESC
        """
    ).fetchall()

    if not available:
        return None

    run_ids_in_db = [r[0] for r in available]

    if run_id is not None:
        if run_id not in run_ids_in_db:
            raise ValueError(
                f"run_id {run_id!r} not found in evaluation_runs. "
                f"Available longitudinal run_ids: {run_ids_in_db}"
            )
        return run_id

    # Auto-select the most recent; warn loudly if multiple exist.
    latest = run_ids_in_db[0]
    if len(run_ids_in_db) > 1:
        import sys as _sys
        print(
            f"WARNING: multiple longitudinal runs present ({len(run_ids_in_db)}); "
            f"auto-selecting latest {latest!r}. Pass --run-id to pick a "
            f"specific run. Available: {run_ids_in_db}",
            file=_sys.stderr,
        )
    return latest


def _load_cycle_data(
    conn: duckdb.DuckDBPyConnection,
    run_id: str | None = None,
) -> list[dict]:
    """Read evaluation_runs + invocation_events + cycle_utility_scores +
    cycle_events; group into per-cycle records.

    A "cycle" is one task execution in the longitudinal run. Cycles are
    ordered chronologically by ``evaluation_runs.timestamp``. For each
    cycle we produce a dict with:

      - cycle (1-indexed task position in the sequence)
      - task_id, run_id, timestamp
      - cumulative_invocations: {cap_name: int} — snapshot as of end of cycle
      - incremental_invocations: {cap_name: int} — this cycle only
      - active_cap_count: int — from cumulative_invocations keys
      - utility_scores: {cap_name: float} — from cycle_utility_scores. Empty
        when the table is missing or unpopulated.
      - events: [{capability_id, event_type, details}, ...] — from
        cycle_events. Empty when the table is missing or unpopulated.

    Returns an empty list if either ``evaluation_runs`` is empty or has
    no longitudinal rows.

    ``run_id`` disambiguates multiple longitudinal runs in the same DB.
    ``None`` auto-selects the most recent run with a stderr warning if
    multiple exist.
    """
    # Resolve the run_id (may warn / raise on ambiguity).
    selected_run_id = _resolve_longitudinal_run_id(conn, run_id)
    if selected_run_id is None:
        return []

    # Ordered task sequence from evaluation_runs (scoped to run_id).
    task_rows = conn.execute(
        """
        SELECT run_id, task_id, timestamp
        FROM evaluation_runs
        WHERE condition = 'longitudinal' AND run_id = ?
        ORDER BY timestamp
        """,
        [selected_run_id],
    ).fetchall()

    if not task_rows:
        return []

    # Invocation events with timestamps, grouped by task_id context.
    try:
        inv_rows = conn.execute(
            """
            SELECT task_id, capability_id, occurred_at
            FROM invocation_events
            WHERE condition = 'longitudinal' AND run_id = ?
            ORDER BY occurred_at
            """,
            [selected_run_id],
        ).fetchall()
    except Exception:  # noqa: BLE001
        # Older DBs may not have invocation telemetry.
        inv_rows = []

    # Per-cycle invocation maps use timestamp windows. A task_id can repeat
    # within one longitudinal run, so task_id alone
    # is not a safe bucket key. We use each evaluation_runs timestamp as
    # the cycle boundary and attribute each invocation to the most recent
    # task row whose timestamp is <= invocation occurred_at. This gives
    # per-cycle increments that can be accumulated into snapshots.
    task_boundaries = [
        (task_ts, (run_id, task_id))
        for run_id, task_id, task_ts in task_rows
    ]
    # Map each invocation to its containing cycle. Invocation timestamps are
    # written during the cycle flush, after the evaluation_runs row for that
    # cycle exists, so advancing only when the next task timestamp is <= the
    # invocation timestamp preserves repeated task IDs.
    cycle_increment: list[dict[str, int]] = [defaultdict(int) for _ in task_rows]
    cycle_idx = 0
    for inv_task_id, cap_id, inv_ts in inv_rows:
        while (
            cycle_idx + 1 < len(task_boundaries)
            and task_boundaries[cycle_idx + 1][0] <= inv_ts
        ):
            cycle_idx += 1
        cycle_increment[cycle_idx][cap_id] += 1

    # Utility scores and optimizer events use the same timestamp window. Keying
    # only by task_id would merge repeated tasks and collapse the per-cycle
    # time series.
    cycle_utility: list[dict[str, float]] = [{} for _ in task_rows]
    if _table_exists(conn, "cycle_utility_scores"):
        util_rows = conn.execute(
            """
            SELECT task_id, capability_id, utility_score, occurred_at
            FROM cycle_utility_scores
            WHERE condition = 'longitudinal' AND run_id = ?
            ORDER BY occurred_at
            """,
            [selected_run_id],
        ).fetchall()
        # Same timestamp-window attribution as invocation events.
        c_idx = 0
        for _util_task_id, cap_id, score, util_ts in util_rows:
            while (
                c_idx + 1 < len(task_boundaries)
                and task_boundaries[c_idx + 1][0] <= util_ts
            ):
                c_idx += 1
            # A single cycle's final utility flush for a capability is the
            # end-of-cycle reading; if multiple flushes land in the same
            # window, the last one wins.
            cycle_utility[c_idx][cap_id] = float(score)

    cycle_events: list[list[dict]] = [[] for _ in task_rows]
    if _table_exists(conn, "cycle_events"):
        event_rows = conn.execute(
            """
            SELECT task_id, capability_id, event_type, details, occurred_at
            FROM cycle_events
            WHERE condition = 'longitudinal' AND run_id = ?
            ORDER BY occurred_at
            """,
            [selected_run_id],
        ).fetchall()
        # Same timestamp-window attribution as invocation events.
        c_idx = 0
        for _evt_task_id, cap_id, event_type, details, evt_ts in event_rows:
            while (
                c_idx + 1 < len(task_boundaries)
                and task_boundaries[c_idx + 1][0] <= evt_ts
            ):
                c_idx += 1
            cycle_events[c_idx].append({
                "capability_id": cap_id,
                "event_type": event_type,
                "details": details or "",
            })

    cumulative: dict[str, int] = defaultdict(int)
    cycles: list[dict] = []
    for i, (run_id, task_id, ts) in enumerate(task_rows):
        for cap_id, n in cycle_increment[i].items():
            cumulative[cap_id] += n
        cycles.append({
            "cycle": i + 1,
            "task_id": task_id,
            "run_id": run_id,
            "timestamp": ts,
            "cumulative_invocations": dict(cumulative),  # snapshot copy
            "incremental_invocations": dict(cycle_increment[i]),
            "active_cap_count": len(cumulative),
            "utility_scores": dict(cycle_utility[i]),
            "events": list(cycle_events[i]),
        })
    return cycles


def _detect_convergence(
    kls: list[float],
    threshold: float,
    min_consecutive: int,
) -> int | None:
    """Return the first cycle N (1-indexed) where the KL stays < threshold
    for ``min_consecutive`` consecutive cycles. None if never converged.

    This is the local-stability detector: it measures whether consecutive
    snapshot KL stays below the threshold for N cycles. The
    distance-to-final-distribution detector below catches slow drifts and
    oscillations.
    """
    if len(kls) < min_consecutive:
        return None
    run_len = 0
    for i, kl in enumerate(kls):
        # NaN means there is no data for this cycle pair, so it resets the
        # streak instead of counting as sub-threshold convergence.
        if isinstance(kl, float) and math.isnan(kl):
            run_len = 0
            continue
        if kl < threshold:
            run_len += 1
            if run_len >= min_consecutive:
                # First cycle of the converged window is (i + 1) - (min_consecutive - 1)
                return (i + 1) - (min_consecutive - 1)
        else:
            run_len = 0
    return None


def _average_distributions(snapshots: list[dict[str, float]]) -> dict[str, float]:
    """Return the element-wise average of a list of distribution dicts.

    Missing keys in a snapshot are treated as 0 so the final-window average
    is not set by noise from a single cycle.
    """
    if not snapshots:
        return {}
    all_keys: set[str] = set()
    for s in snapshots:
        all_keys.update(s.keys())
    n = len(snapshots)
    return {
        k: sum(float(s.get(k, 0.0)) for s in snapshots) / n
        for k in all_keys
    }


def _detect_stable_convergence(
    snapshots: list[dict[str, float]],
    threshold: float,
    min_consecutive: int,
    *,
    window_size: int = 5,
    kl_fn=None,
) -> int | None:
    """Return the earliest cycle where each cycle's distance to the final
    windowed-average distribution stays below ``threshold`` for
    ``min_consecutive`` consecutive cycles.

    Unlike ``_detect_convergence`` (local stability: per-step KL <
    threshold), this compares each cycle to the final-window-averaged
    distribution. That catches oscillations and slow drifts where adjacent
    cycles look similar but the sequence has not settled.

    ``kl_fn(reference, snapshot) -> float`` is injectable so tests can
    verify the behavior without depending on the analyzer's specific
    KL implementation; defaults to ``_kl_divergence_between_snapshots``.
    """
    if kl_fn is None:
        kl_fn = _kl_divergence_between_snapshots
    if len(snapshots) < max(window_size, min_consecutive):
        return None

    final_ref = _average_distributions(snapshots[-window_size:])

    # An empty final-window attractor means the registry has collapsed to
    # nothing, which is not convergence.
    if not final_ref:
        return None

    run_len = 0
    for i, snap in enumerate(snapshots):
        # If snap is empty but final_ref is not, the distance is infinite.
        # NaN represents a dead cycle and must reset the convergence streak.
        kl = kl_fn(final_ref, snap)
        import math as _math
        if _math.isnan(kl):
            run_len = 0
            continue
        if kl < threshold:
            run_len += 1
            if run_len >= min_consecutive:
                return (i + 1) - (min_consecutive - 1)
        else:
            run_len = 0
    return None


def _emit_csv(rows: list[dict], columns: list[str], path: Path) -> None:
    """Write a list of dicts to a CSV file."""
    import csv
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for row in rows:
            w.writerow({c: row.get(c, "") for c in columns})


def _emit_plot(
    rows: list[dict],
    path: Path,
    threshold: float,
    convergence_cycle: int | None,
    *,
    primary_kl_source: str = "invocation_counts",
) -> bool:
    """Write a convergence-curve PNG. Returns True on success, False if
    matplotlib is unavailable or plotting fails (non-fatal)."""
    try:
        import matplotlib  # noqa: F401
        import matplotlib.pyplot as plt
    except ImportError:
        print("  (skipped --plot: matplotlib not installed. `uv sync --group analysis` to enable.)")
        return False

    cycles = [r["cycle"] for r in rows]
    kls_invocations = [r["kl_invocations"] for r in rows]
    kls_utility = [r["kl_utility"] for r in rows]
    ginis = [r["gini"] for r in rows]
    n_caps = [r["n_active_caps"] for r in rows]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True)

    # Top: KL convergence — plot both series when utility is available
    def _nan_mask(vals):
        return [v if (v is not None and np.isfinite(v)) else np.nan for v in vals]

    has_utility = any(v is not None for v in kls_utility)
    if has_utility:
        ax1.plot(
            cycles, _nan_mask(kls_utility), marker="o", markersize=3,
            linewidth=1, color="#1f77b4",
            label="KL utility scores (proposal-literal)",
        )
        ax1.plot(
            cycles, _nan_mask(kls_invocations), marker="s", markersize=2,
            linewidth=0.8, color="#888888", alpha=0.6, linestyle="--",
            label="KL invocation counts (sensitivity)",
        )
    else:
        ax1.plot(
            cycles, _nan_mask(kls_invocations), marker="o", markersize=3,
            linewidth=1, color="#1f77b4", label="KL invocation counts",
        )
    ax1.axhline(
        threshold, color="red", linestyle="--", linewidth=1,
        label=f"threshold ({threshold})",
    )
    if convergence_cycle is not None:
        ax1.axvline(
            convergence_cycle, color="green", linestyle="--", linewidth=1,
            label=f"converged at cycle {convergence_cycle}",
        )
    ax1.set_ylabel("KL(cycle N || cycle N-1)")
    title = "Registry Convergence — KL divergence between consecutive snapshots"
    if primary_kl_source == "utility_scores":
        title += " (utility scores primary)"
    else:
        title += " (invocation counts — utility scores unavailable)"
    ax1.set_title(title)
    ax1.legend(loc="upper right")
    ax1.grid(True, alpha=0.3)

    # Bottom: Gini + active cap count
    ax2b = ax2.twinx()
    line1 = ax2.plot(
        cycles, ginis, marker="s", markersize=3, linewidth=1,
        color="#ff7f0e", label="Gini (capability invocation distribution)",
    )
    line2 = ax2b.plot(
        cycles, n_caps, marker="^", markersize=3, linewidth=1,
        color="#2ca02c", label="# active capabilities",
    )
    ax2.set_ylabel("Gini coefficient", color="#ff7f0e")
    ax2b.set_ylabel("# active capabilities", color="#2ca02c")
    ax2.set_xlabel("Cycle (task execution)")
    ax2.set_title("Gini trajectory + registry size")
    lines = line1 + line2
    ax2.legend(lines, [line.get_label() for line in lines], loc="upper right")
    ax2.grid(True, alpha=0.3)

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return True


# Banner state for section headers. Override paths set these in ``main()``
# so degraded-data annotations are repeated throughout the report.
_PROXY_BANNER: str = ""
_SPARSE_BANNER: str = ""


def _print_header(title: str) -> None:
    prefix_parts = [p for p in (_PROXY_BANNER, _SPARSE_BANNER) if p]
    prefix = (" ".join(prefix_parts) + " ") if prefix_parts else ""
    print(f"\n{'=' * 80}")
    print(f"  {prefix}{title}")
    print(f"{'=' * 80}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Longitudinal KL convergence analyzer",
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help="Path to longitudinal eval DB (default: data/eval/longitudinal.duckdb)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.05,
        help="KL threshold for convergence detection (default: 0.05)",
    )
    parser.add_argument(
        "--min-consecutive",
        type=int,
        default=3,
        help="Number of consecutive sub-threshold cycles required (default: 3)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for CSV + PNG outputs (default: data/analysis/)",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Emit per-cycle CSV (always written when --plot is passed too)",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Emit convergence PNG via matplotlib (if installed)",
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=0,
        help="Print only the last N cycles in the per-cycle table (0 = all, default)",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Specific longitudinal run_id to analyze. Default: latest "
             "longitudinal run, with a WARNING on stderr if multiple are "
             "present.",
    )
    # Make the campaign's expected shape explicit. A partial or overfull DB is
    # not comparable to a complete longitudinal campaign unless the caller
    # opts in and keeps the warning banner.
    parser.add_argument(
        "--expected-cycles", type=int, default=40,
        help="Expected cycle count for a COMPLETE campaign (default: 40, "
             "matching scripts/run_longitudinal.py --n-tasks 40). If the DB "
             "has fewer cycles the analyzer refuses to proceed with exit 3, "
             "unless --allow-incomplete is passed.",
    )
    parser.add_argument(
        "--allow-incomplete", action="store_true",
        help="Proceed on a shortfall DB. Output is prefixed with an "
             "[INCOMPLETE: N/expected] banner so Chapter 6 consumers can "
             "see the shortfall at a glance. Exit code is still non-zero "
             "so orchestrators don't treat incomplete as success.",
    )
    # Utility-score KL is the primary metric. Invocation-count KL is only a
    # proxy and must be explicitly enabled with a visible report banner.
    parser.add_argument(
        "--allow-proxy-kl", action="store_true",
        help="Proceed when cycle_utility_scores is empty, using the "
             "invocation-count KL proxy. Every section header is prefixed "
             "[PROXY KL]. Thesis disclosure REQUIRED. Default: refuse "
             "with exit 6.",
    )
    # A single telemetry row is not enough evidence for a full campaign.
    # Require a per-cycle coverage floor unless the caller opts into a
    # sparse-signal report.
    parser.add_argument(
        "--allow-sparse-signal", action="store_true",
        help="Proceed with convergence analysis when per-cycle telemetry "
             "coverage is below --min-cycle-coverage. Every section header "
             "is prefixed [SPARSE: N/M]. Thesis disclosure REQUIRED. "
             "Default: refuse with exit 8.",
    )
    parser.add_argument(
        "--min-cycle-coverage", type=float, default=0.5,
        help="Minimum fraction of cycles that must carry signal (default "
             "0.5). Below this, exit 8 unless --allow-sparse-signal is "
             "passed.",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}")
        print("       Run `uv run scripts/run_longitudinal.py` first to produce it.")
        return 1

    conn = duckdb.connect(str(db_path), read_only=True)

    try:
        cycles = _load_cycle_data(conn, run_id=args.run_id)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        conn.close()
        return 1
    if not cycles:
        print(f"ERROR: no longitudinal rows in {db_path}")
        print("       (evaluation_runs has no rows with condition='longitudinal')")
        conn.close()
        return 1

    # Reject both underfull and overfull cycle counts. Overfull runs usually
    # mean run_id reuse, duplicate writes, or merged timelines.
    incomplete_banner: str | None = None
    if len(cycles) != args.expected_cycles:
        direction = (
            "overfull — possible run_id collision, duplicate writes, or "
            "merged timelines"
            if len(cycles) > args.expected_cycles
            else "incomplete/aborted run"
        )
        if not args.allow_incomplete:
            print(
                f"ERROR: longitudinal run has {len(cycles)} cycles, expected "
                f"{args.expected_cycles} ({direction}). Pass --allow-incomplete "
                f"to proceed with a banner, or --expected-cycles N to "
                f"override the expected count.",
                file=sys.stderr,
            )
            conn.close()
            return 3
        tag = "OVERFULL" if len(cycles) > args.expected_cycles else "INCOMPLETE"
        incomplete_banner = (
            f"[{tag}: {len(cycles)}/{args.expected_cycles} cycles]"
        )
        print(
            f"WARNING: {incomplete_banner} — Chapter 6 consumers must cite "
            f"the shortfall/overrun; downstream convergence claims are not "
            f"directly comparable to complete-campaign numbers.",
            file=sys.stderr,
        )

    # A DB with evaluation_runs rows but no telemetry cannot support a
    # convergence claim. Fail early with a concrete reason instead of letting
    # every cycle collapse to empty-vs-empty KL.
    has_any_signal = any(
        c.get("cumulative_invocations")
        or c.get("utility_scores")
        or c.get("events")
        for c in cycles
    )
    if not has_any_signal:
        print(
            f"ERROR: longitudinal DB has {len(cycles)} evaluation_runs cycles "
            f"but NO invocation / utility-score / cycle-event rows. "
            f"Convergence cannot be computed on zero-evidence runs. "
            f"Likely causes: (a) cycle-flush wiring failed mid-run, "
            f"(b) DB predates the flush hooks, (c) a schema-drift dropped "
            f"the event tables. Exit 4.",
            file=sys.stderr,
        )
        conn.close()
        return 4

    # Per-cycle coverage must use incremental signal. Cumulative snapshots
    # carry old invocations forward, which would make one early event look like
    # every later cycle has fresh telemetry.
    cycles_with_signal = sum(
        1 for c in cycles
        if c.get("incremental_invocations")
        or c.get("utility_scores")
        or c.get("events")
    )
    coverage_ratio = cycles_with_signal / len(cycles) if cycles else 0.0
    if coverage_ratio < args.min_cycle_coverage:
        if not args.allow_sparse_signal:
            print(
                f"ERROR: longitudinal DB has signal in only "
                f"{cycles_with_signal}/{len(cycles)} cycles "
                f"({coverage_ratio:.0%}); below threshold "
                f"{args.min_cycle_coverage:.0%}. Convergence analysis "
                f"on sparse telemetry is unreliable — a single invocation "
                f"event can pass a global presence check while the remaining "
                f"cycles carry no signal, and the convergence "
                f"detector fires on sub-threshold empty-vs-empty comparisons.\n"
                f"  Likely causes:\n"
                f"    (a) cycle_flush.py partial-flush failures\n"
                f"    (b) metrics_store lost state mid-campaign\n"
                f"    (c) registry/store sync broken at flush boundary\n"
                f"  Pass --allow-sparse-signal to proceed with a "
                f"[SPARSE: N/M] banner (thesis disclosure REQUIRED). "
                f"Exit 8.",
                file=sys.stderr,
            )
            conn.close()
            return 8
        global _SPARSE_BANNER
        _SPARSE_BANNER = f"[SPARSE: {cycles_with_signal}/{len(cycles)}]"
        print(
            f"WARNING: {_SPARSE_BANNER} — thesis text must disclose the "
            f"per-cycle coverage gap. Convergence results below are NOT "
            f"directly comparable to full-coverage numbers.",
            file=sys.stderr,
        )

    # Use utility-score KL when available. Invocation-count KL is only a
    # sensitivity proxy.
    has_utility_scores = any(c.get("utility_scores") for c in cycles)
    has_events = any(c.get("events") for c in cycles)
    primary_kl_source = "utility_scores" if has_utility_scores else "invocation_counts"

    # Refuse silent downgrades from utility-score KL to invocation-count KL.
    # The proxy path is explicit and visibly marked in each section header.
    if primary_kl_source == "invocation_counts":
        if not args.allow_proxy_kl:
            print(
                "ERROR: cycle_utility_scores is empty. The proposal-literal "
                "convergence metric (utility-score KL, per §Metrics item 6) "
                "cannot be computed. The invocation-count KL is a SENSITIVITY "
                "proxy, not the primary metric, so reporting convergence on "
                "it would be a methodology violation.\n"
                "  Likely causes:\n"
                "    (a) cycle_flush.py utility-flush failure\n"
                "    (b) DB predates utility-score flush wiring\n"
                "    (c) metrics_store / registry sync broken at flush time\n"
                "  Pass --allow-proxy-kl to proceed with a [PROXY KL] banner "
                "on every section header (thesis disclosure REQUIRED). "
                "Exit 6.",
                file=sys.stderr,
            )
            conn.close()
            return 6
        global _PROXY_BANNER
        _PROXY_BANNER = "[PROXY KL]"
        print(
            f"WARNING: {_PROXY_BANNER} — proceeding with invocation-count KL "
            f"proxy per --allow-proxy-kl. Every section header below is "
            f"prefixed [PROXY KL]; thesis text must disclose the downgrade.",
            file=sys.stderr,
        )

    rows: list[dict] = []
    prev_snapshot: dict[str, int] = {}  # for invocation-count KL
    prev_utility: dict[str, float] = {}  # for utility-score KL
    for c in cycles:
        snapshot = c["cumulative_invocations"]
        utility = c.get("utility_scores", {})
        incremental = c.get("incremental_invocations", {})
        kl_invocations = _kl_divergence_between_snapshots(prev_snapshot, snapshot)
        kl_utility = (
            _kl_divergence_between_snapshots(prev_utility, utility)
            if has_utility_scores else None
        )
        # Keep cumulative Gini as the headline metric, and compute
        # incremental Gini to show whether the current cycle is stabilizing or
        # the cumulative curve is merely smoothing volatility.
        gini_cumulative = (
            _gini_coefficient(list(snapshot.values())) if snapshot else 0.0
        )
        gini_incremental = (
            _gini_coefficient(list(incremental.values())) if incremental else 0.0
        )
        events = c.get("events", [])
        merge_event_rows = sum(
            1 for event in events if event.get("event_type") == "merged"
        )
        merge_operations = sum(
            1
            for event in events
            if event.get("event_type") == "merged"
            and str(event.get("details", "")).startswith("from ")
        )
        rows.append({
            "cycle": c["cycle"],
            "task_id": c["task_id"],
            "n_active_caps": c["active_cap_count"],
            "registry_snapshot_size": len(utility) if utility else 0,
            "kl_utility": kl_utility,
            "kl_invocations": kl_invocations,
            # Back-compat: ``gini`` keeps the cumulative semantics so
            # downstream consumers (CSV writers, plot helpers, the
            # convergence summary) don't break. New consumers should
            # prefer the explicit ``gini_cumulative`` /
            # ``gini_incremental`` columns.
            "gini": gini_cumulative,
            "gini_cumulative": gini_cumulative,
            "gini_incremental": gini_incremental,
            "n_new_invocations": sum(incremental.values()),
            "merge_operations": merge_operations,
            "merge_event_rows": merge_event_rows,
            "events": events,
        })
        prev_snapshot = snapshot
        prev_utility = utility

    kls_invocations = [r["kl_invocations"] for r in rows]
    kls_utility = [r["kl_utility"] for r in rows if r["kl_utility"] is not None]
    ginis = [r["gini"] for r in rows]
    n_caps = [r["n_active_caps"] for r in rows]

    # --- Cycles-to-convergence ---
    # Prefer utility-score KL when available (proposal-literal).
    # Skip the cycle-1 entry (KL = 0.0 by empty-prior convention, not real convergence).
    convergence_cycle = None
    convergence_kls = kls_utility if primary_kl_source == "utility_scores" else kls_invocations
    if len(convergence_kls) > 1:
        convergence_cycle = _detect_convergence(
            convergence_kls[1:], args.threshold, args.min_consecutive,
        )
        if convergence_cycle is not None:
            # Offset back to 1-indexed cycle numbers in the original sequence
            convergence_cycle += 1

    header_title = "Longitudinal Registry Convergence"
    if incomplete_banner is not None:
        header_title = f"{incomplete_banner} {header_title}"
    _print_header(header_title)
    print(f"  DB:              {db_path}")
    print(f"  Total cycles:    {len(cycles)}")
    print(f"  KL threshold:    {args.threshold}")
    print(f"  Min consecutive: {args.min_consecutive}")
    primary_label = (
        "proposal-literal"
        if primary_kl_source == "utility_scores"
        else "invocation-count proxy"
    )
    print(f"  Primary KL:      {primary_kl_source} ({primary_label})")
    if not has_utility_scores:
        print("  (cycle_utility_scores empty — utility-score telemetry unavailable)")
    if not has_events:
        print("  (cycle_events empty — per-type op counts unavailable)")
    print()
    if convergence_cycle is not None:
        print(f"  ✅ Cycles-to-convergence: {convergence_cycle}")
        print(f"     (First cycle where {primary_kl_source} KL stayed < {args.threshold}"
              f" for {args.min_consecutive} consecutive cycles)")
    else:
        print(f"  ⚠ Did NOT converge within {len(cycles)} cycles at threshold {args.threshold}.")
        if convergence_kls[1:]:
            finite_kls = [k for k in convergence_kls[1:] if np.isfinite(k)]
            if finite_kls:
                print(f"     Final KL: {convergence_kls[-1]:.4f}, min KL: {min(finite_kls):.4f}")

    # --- Gini range + seeded-vs-evolved comparison (Pillar 3 RQ #1) ---
    if ginis:
        # Incremental Gini distinguishes true stabilization from cumulative
        # smoothing of a still-volatile per-cycle distribution.
        ginis_incremental = [r["gini_incremental"] for r in rows]
        print()
        print(f"  Gini (cumulative, final):    {ginis[-1]:.3f}")
        print(f"  Gini (cumulative, range):    [{min(ginis):.3f}, {max(ginis):.3f}]")
        if any(g > 0 for g in ginis_incremental):
            print(
                f"  Gini (incremental, final):   {ginis_incremental[-1]:.3f}"
            )
            print(
                f"  Gini (incremental, range):   "
                f"[{min(ginis_incremental):.3f}, {max(ginis_incremental):.3f}]"
            )
            # Diverging ranges flag the smoothing artifact directly.
            if (
                max(ginis_incremental) - min(ginis_incremental)
                > 2 * (max(ginis) - min(ginis) + 1e-9)
            ):
                print(
                    "  [diagnostic] Incremental Gini varies >2x more than "
                    "cumulative — the cumulative trajectory understates "
                    "per-cycle distribution instability. Cite incremental "
                    "Gini in Pillar 3 RQ #1 figures."
                )
        print(f"  # caps (final):  {n_caps[-1]}")
        print(f"  # caps range:    [{min(n_caps)}, {max(n_caps)}]")
        # Seeded vs evolved comparison — proposal Pillar 3 RQ #1.
        if len(ginis) >= 2:
            seeded_gini = ginis[0]
            evolved_gini = ginis[-1]
            delta = evolved_gini - seeded_gini
            print()
            print("  Seeded-vs-Evolved Gini (Pillar 3 RQ #1):")
            print(f"    Cycle 1 (seeded registry):   {seeded_gini:.3f}")
            print(f"    Final (evolved registry):    {evolved_gini:.3f}")
            print(f"    Delta:                       {delta:+.3f}")
            interp = (
                "evolution made the distribution MORE skewed"
                if delta > 0.05 else
                "evolution made the distribution MORE uniform"
                if delta < -0.05 else
                "evolution did NOT meaningfully change granularity distribution"
            )
            print(f"    Interpretation: {interp}.")

    # --- Per-cycle table ---
    _print_header("Per-cycle convergence + Gini trajectory")
    rows_to_show = rows if args.tail == 0 else rows[-args.tail:]
    # Show both KL series when utility-score is populated
    if has_utility_scores:
        header = (f"  {'cycle':>5} {'task_id':<8} {'n_caps':>7} "
                  f"{'kl_utility':>11} {'kl_invoc':>10} {'gini':>8} {'n_new_inv':>10}")
    else:
        header = (f"  {'cycle':>5} {'task_id':<8} {'n_caps':>7} "
                  f"{'kl_invoc':>10} {'gini':>8} {'n_new_inv':>10}")
    print(header)
    print("  " + "-" * (len(header) - 2))
    for r in rows_to_show:
        kl_inv_str = f"{r['kl_invocations']:.4f}" if np.isfinite(r['kl_invocations']) else "inf"
        if has_utility_scores:
            ku = r["kl_utility"]
            kl_u_str = (
                f"{ku:.4f}" if ku is not None and np.isfinite(ku)
                else ("inf" if ku is not None else "—")
            )
            print(f"  {r['cycle']:>5d} {r['task_id']:<8} {r['n_active_caps']:>7d} "
                  f"{kl_u_str:>11} {kl_inv_str:>10} "
                  f"{r['gini']:>8.3f} {r['n_new_invocations']:>10d}")
        else:
            print(f"  {r['cycle']:>5d} {r['task_id']:<8} {r['n_active_caps']:>7d} "
                  f"{kl_inv_str:>10} {r['gini']:>8.3f} {r['n_new_invocations']:>10d}")

    # --- Per-type operation counts per cycle ---
    # True per-type counts when cycle_events is populated; else fall back
    # to active-cap-delta proxy.
    if has_events:
        _print_header("Per-type operation counts per cycle (from cycle_events)")
        # Aggregate across all cycles first for the top-line view
        type_totals: dict[str, int] = defaultdict(int)
        for r in rows:
            for ev in r["events"]:
                type_totals[ev["event_type"]] += 1
        print("  Aggregate (across all cycles):")
        for etype in sorted(type_totals, key=lambda t: (-type_totals[t], t)):
            print(f"    {etype:<28} {type_totals[etype]:>6d}")
        # First-fire / last-fire for the key operation types
        print("\n  First-fire and last-fire cycles per op type:")
        tracked_types = ("pruned", "split", "merged", "verification_rollback",
                         "split_rollback", "user_flagged_wrong")
        for etype in tracked_types:
            first_cycle = None
            last_cycle = None
            for r in rows:
                if any(e["event_type"] == etype for e in r["events"]):
                    if first_cycle is None:
                        first_cycle = r["cycle"]
                    last_cycle = r["cycle"]
            if first_cycle is None:
                continue  # skip types that never fired
            print(f"    {etype:<28} first: cycle {first_cycle:>3d}, last: cycle {last_cycle:>3d}")
    else:
        _print_header("Operation-count proxy — per-cycle delta in active-capability count")
        print("  (cycle_events table unavailable — falling back to active-cap-delta proxy.")
        print("   Re-run run_longitudinal.py with event telemetry enabled to get true")
        print("   per-type counts.)")
        print("  Positive delta = net generation; negative = net optimization; zero = stable.")
        print()
        prev_n = 0
        deltas: list[int] = []
        for r in rows:
            delta = r["n_active_caps"] - prev_n
            deltas.append(delta)
            prev_n = r["n_active_caps"]
        n_positive = sum(1 for d in deltas if d > 0)
        n_zero = sum(1 for d in deltas if d == 0)
        n_negative = sum(1 for d in deltas if d < 0)
        print(f"  Cycles with generation (delta > 0):   {n_positive}")
        print(f"  Cycles stable (delta == 0):           {n_zero}")
        print(f"  Cycles with optimization (delta < 0): {n_negative}")

    # --- Emit CSV + PNG if requested ---
    output_dir = Path(args.output_dir)
    csv_path = output_dir / "longitudinal_convergence.csv"
    png_path = output_dir / "longitudinal_convergence.png"

    if args.csv or args.plot:
        csv_columns = [
            "cycle", "task_id", "n_active_caps", "registry_snapshot_size",
            "kl_utility", "kl_invocations",
            "gini", "n_new_invocations", "merge_operations", "merge_event_rows",
        ]
        # Flatten events into a count column per row for CSV simplicity.
        csv_rows = []
        for r in rows:
            csv_row = {k: r.get(k, "") for k in csv_columns}
            # Replace inf with a string for CSV portability
            if not np.isfinite(csv_row.get("kl_invocations") or 0.0):
                csv_row["kl_invocations"] = "inf"
            if csv_row.get("kl_utility") is not None and not np.isfinite(csv_row["kl_utility"]):
                csv_row["kl_utility"] = "inf"
            csv_rows.append(csv_row)
        _emit_csv(csv_rows, columns=csv_columns, path=csv_path)
        print(f"\n  CSV written: {csv_path}")
        if args.plot:
            ok = _emit_plot(rows, png_path, args.threshold, convergence_cycle,
                            primary_kl_source=primary_kl_source)
            if ok:
                print(f"  PNG written: {png_path}")

    conn.close()
    # Exit-code contract:
    #   0  complete data + converged
    #   1  empty DB (no longitudinal rows at all) — handled earlier
    #   2  complete data but convergence not detected
    #   3  incomplete campaign (shortfall) — returned even with --allow-incomplete
    #   4  zero-evidence DB (rows present but no signal tables) — handled earlier
    #   6  proposal-literal KL unavailable, --allow-proxy-kl required
    #   8  sparse per-cycle telemetry, --allow-sparse-signal required
    if incomplete_banner is not None:
        return 3
    return 0 if convergence_cycle is not None else 2


if __name__ == "__main__":
    sys.exit(main())
