"""Longitudinal KL convergence analyzer — Step 18.7.

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

**Proposal-aligned implementation (2026-04-17 follow-up to Step 18.7).**

Originally this analyzer used two proxies because the required data
wasn't persisted: invocation-count KL (instead of utility-score KL)
and active-cap-delta (instead of per-type op counts). The Step 18.7
follow-up wired the harness to flush per-cycle utility scores
(via ``src.optimizer.utility.compute_all_utilities``) and per-cycle
capability_events to new EvaluationStore tables. This analyzer now
reads those tables directly:

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
   for ``--min-consecutive`` (default 3) cycles in a row. Falls back to
   invocation-count KL if utility scores aren't persisted (legacy DBs).

2. **Convergence curve** — per-cycle table of ``(cycle, task_id,
   n_active_caps, kl_utility, kl_invocations, gini)``. CSV always written;
   matplotlib PNG optional via ``--plot``. Chapter 6 figure source.

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
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402
import numpy as np  # noqa: E402
from scipy.stats import entropy  # noqa: E402

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
        - 0.0 if both distributions are empty
        - float('inf') if prev is non-empty but curr is empty (catastrophic)
        - 0.0 if curr is non-empty but prev is empty (first-cycle sentinel)
        - otherwise the KL divergence as a non-negative float
    """
    if not prev_dist and not curr_dist:
        return 0.0
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

    Security audit finding #24 (2026-04-20): when the DB contains
    multiple longitudinal runs (re-run with different config, two shift
    schedules, etc.), pre-fix merged all of them into one interleaved
    timeline. Now the analyzer picks a single run_id — the caller's
    ``--run-id`` value if provided, otherwise the most-recent
    longitudinal run, with a warning on the stderr if multiple exist.

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
      - utility_scores: {cap_name: float} — from cycle_utility_scores (or {}
        if that table isn't populated — e.g. pre-Step-18.7-follow-up DB)
      - events: [{capability_id, event_type, details}, ...] — from cycle_events
        (or [] if that table isn't populated)

    Returns an empty list if either ``evaluation_runs`` is empty or has
    no longitudinal rows.

    Security audit finding #24 (2026-04-20): ``run_id`` is now required
    to disambiguate across multiple longitudinal runs in the same DB.
    ``None`` auto-selects the most recent run with a stderr warning if
    multiple exist. Pre-fix the analyzer merged all run_ids into one
    timeline, corrupting KL / shift-detection / adaptation verdicts
    whenever the DB had more than one longitudinal run.
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
        # invocation_events table may not exist on old DBs (pre-Step-18.5).
        inv_rows = []

    # Build per-task incremental invocation maps, keyed by (run_id, task_id)
    # — a single task may run in multiple runs, and we need to disambiguate.
    # The longitudinal runner uses ONE run_id for the whole sequence, so
    # task_id alone is sufficient in practice. But we key by (run_id, task_id)
    # for robustness.
    #
    # One subtlety: the same task_id CAN appear multiple times in the
    # sequence (since task_sequence cycles through ALL_TASKS to fill
    # n_tasks). Rather than trying to match incremental invocations to
    # specific cycle instances (which would require ordered timestamps
    # on both sides), we attribute ALL invocation_events with a given
    # task_id to ALL cycles of that task_id — then cumulative aggregation
    # naturally produces the right snapshot.
    #
    # Cleaner approach: use the task_row's (run_id, task_id, timestamp)
    # as the boundary and attribute invocations whose occurred_at is
    # within the task's execution window. But we don't have end-timestamps
    # per task. So we approximate: each invocation is attributed to the
    # most-recent task row whose timestamp <= the invocation's occurred_at.
    #
    # This gives per-cycle incremental counts that can be cumulated.
    task_boundaries = [
        (task_ts, (run_id, task_id))
        for run_id, task_id, task_ts in task_rows
    ]
    # Map each invocation to its containing cycle (index into task_rows)
    cycle_increment: list[dict[str, int]] = [defaultdict(int) for _ in task_rows]
    # Binary-search-ish: walk invocations chronologically, track current cycle
    cycle_idx = 0
    for inv_task_id, cap_id, inv_ts in inv_rows:
        # Advance cycle_idx to the last task whose timestamp <= inv_ts
        while (
            cycle_idx + 1 < len(task_boundaries)
            and task_boundaries[cycle_idx + 1][0] <= inv_ts
        ):
            cycle_idx += 1
        cycle_increment[cycle_idx][cap_id] += 1

    # Security audit finding #18 (2026-04-20): bucket utility scores and
    # events into per-cycle lists using the task_boundaries timestamp
    # window — same pattern as cycle_increment above. Pre-fix, keying by
    # task_id only merged all repetitions of the same task into one
    # lookup, so cycles 1 and 5 (both T01) both returned cycle 5's
    # utility scores. That silently collapsed the per-cycle time series
    # and made Step 18.8 shift-detection verdicts unreliable (always
    # "graceful adaptation" because Phase A's last cycle read Phase B-
    # era utility values). Under the default ``--n-tasks=40`` run every
    # task repeats at least once, so the bug fires on every real eval.
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
        c_idx = 0
        for _util_task_id, cap_id, score, util_ts in util_rows:
            while (
                c_idx + 1 < len(task_boundaries)
                and task_boundaries[c_idx + 1][0] <= util_ts
            ):
                c_idx += 1
            # A single cycle's final utility flush for a given cap is the
            # "end-of-cycle" reading — if multiple flushes land within
            # one cycle window, the last wins (same intra-cycle semantics
            # the pre-fix code had for a single task_id).
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

    # Build cumulative snapshots
    cumulative: dict[str, int] = defaultdict(int)
    cycles: list[dict] = []
    for i, (run_id, task_id, ts) in enumerate(task_rows):
        # Add this cycle's increments
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
    """
    if len(kls) < min_consecutive:
        return None
    run_len = 0
    for i, kl in enumerate(kls):
        if kl < threshold:
            run_len += 1
            if run_len >= min_consecutive:
                # First cycle of the converged window is (i + 1) - (min_consecutive - 1)
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
        print("  (skipped --plot: matplotlib not installed. `uv add matplotlib` to enable.)")
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
        ax1.plot(cycles, _nan_mask(kls_utility), marker="o", markersize=3, linewidth=1,
                 color="#1f77b4", label="KL utility scores (proposal-literal)")
        ax1.plot(cycles, _nan_mask(kls_invocations), marker="s", markersize=2, linewidth=0.8,
                 color="#888888", alpha=0.6, linestyle="--",
                 label="KL invocation counts (sensitivity)")
    else:
        ax1.plot(cycles, _nan_mask(kls_invocations), marker="o", markersize=3, linewidth=1,
                 color="#1f77b4", label="KL invocation counts")
    ax1.axhline(threshold, color="red", linestyle="--", linewidth=1, label=f"threshold ({threshold})")
    if convergence_cycle is not None:
        ax1.axvline(convergence_cycle, color="green", linestyle="--", linewidth=1,
                    label=f"converged at cycle {convergence_cycle}")
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
    line1 = ax2.plot(cycles, ginis, marker="s", markersize=3, linewidth=1,
                     color="#ff7f0e", label="Gini (capability invocation distribution)")
    line2 = ax2b.plot(cycles, n_caps, marker="^", markersize=3, linewidth=1,
                      color="#2ca02c", label="# active capabilities")
    ax2.set_ylabel("Gini coefficient", color="#ff7f0e")
    ax2b.set_ylabel("# active capabilities", color="#2ca02c")
    ax2.set_xlabel("Cycle (task execution)")
    ax2.set_title("Gini trajectory + registry size")
    lines = line1 + line2
    ax2.legend(lines, [l.get_label() for l in lines], loc="upper right")
    ax2.grid(True, alpha=0.3)

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return True


def _print_header(title: str) -> None:
    print(f"\n{'=' * 80}")
    print(f"  {title}")
    print(f"{'=' * 80}")


def _detect_shift_point(cycles: list[dict]) -> int | None:
    """Return the 1-indexed cycle number at which a distribution_shift
    event was recorded, or None if no such event exists.

    Set by ``scripts/run_longitudinal_shift.py`` via a synthetic
    ``cycle_events`` row with ``event_type='distribution_shift'`` and
    ``capability_id='__system__'``. The marker is written AT cycle
    ``shift_at`` (last Phase A task) — the shift effectively takes place
    at the boundary between this cycle and the next.
    """
    for c in cycles:
        for ev in c.get("events", []):
            if ev["event_type"] == "distribution_shift":
                return c["cycle"]
    return None


def _classify_adaptation(
    phase_a_cycles_to_converge: int | None,
    phase_b_cycles_to_reconverge: int | None,
) -> str:
    """Three-tier classifier for the Phase A → Phase B adaptation pattern.

    - "graceful": Phase B re-converges within ≤ 2x Phase A's convergence time
    - "sluggish": Phase B re-converges but takes 2x-5x Phase A's time
    - "collapse": Phase B never re-converges (> 5x or None)
    - "indeterminate": Phase A never converged; we can't make the comparison
    """
    if phase_a_cycles_to_converge is None:
        return "indeterminate (Phase A never converged — can't compare)"
    if phase_b_cycles_to_reconverge is None:
        return "collapse (Phase B never re-converged)"
    ratio = phase_b_cycles_to_reconverge / max(1, phase_a_cycles_to_converge)
    if ratio <= 2.0:
        return f"graceful adaptation ({ratio:.1f}x Phase A time)"
    if ratio <= 5.0:
        return f"sluggish adaptation ({ratio:.1f}x Phase A time)"
    return f"collapse-leaning ({ratio:.1f}x Phase A time)"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Longitudinal KL convergence analyzer (Step 18.7)",
    )
    parser.add_argument("--db", default=DEFAULT_DB,
                        help="Path to longitudinal eval DB (default: data/eval/longitudinal.duckdb)")
    parser.add_argument("--threshold", type=float, default=0.05,
                        help="KL threshold for convergence detection (default: 0.05)")
    parser.add_argument("--min-consecutive", type=int, default=3,
                        help="Number of consecutive sub-threshold cycles required (default: 3)")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR),
                        help="Directory for CSV + PNG outputs (default: data/analysis/)")
    parser.add_argument("--csv", action="store_true",
                        help="Emit per-cycle CSV (always written when --plot is passed too)")
    parser.add_argument("--plot", action="store_true",
                        help="Emit convergence PNG via matplotlib (if installed)")
    parser.add_argument("--tail", type=int, default=0,
                        help="Print only the last N cycles in the per-cycle table (0 = all, default)")
    parser.add_argument("--run-id", default=None,
                        help="Specific longitudinal run_id to analyze. Default: latest "
                             "longitudinal run, with a WARNING on stderr if multiple are "
                             "present. Security audit finding #24 (2026-04-20).")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}")
        print(f"       Run `uv run scripts/run_longitudinal.py` first to produce it.")
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
        print(f"       (evaluation_runs has no rows with condition='longitudinal')")
        conn.close()
        return 1

    # Check whether the Step 18.7-follow-up tables are populated. If so,
    # we can use utility-score KL (proposal-literal). If not, fall back
    # to invocation-count KL (proxy).
    has_utility_scores = any(c.get("utility_scores") for c in cycles)
    has_events = any(c.get("events") for c in cycles)
    primary_kl_source = "utility_scores" if has_utility_scores else "invocation_counts"

    # Compute per-cycle KL (both series when possible) + Gini
    rows: list[dict] = []
    prev_snapshot: dict[str, int] = {}  # for invocation-count KL
    prev_utility: dict[str, float] = {}  # for utility-score KL
    for c in cycles:
        snapshot = c["cumulative_invocations"]
        utility = c.get("utility_scores", {})
        kl_invocations = _kl_divergence_between_snapshots(prev_snapshot, snapshot)
        kl_utility = (
            _kl_divergence_between_snapshots(prev_utility, utility)
            if has_utility_scores else None
        )
        gini = _gini_coefficient(list(snapshot.values())) if snapshot else 0.0
        rows.append({
            "cycle": c["cycle"],
            "task_id": c["task_id"],
            "n_active_caps": c["active_cap_count"],
            "kl_utility": kl_utility,
            "kl_invocations": kl_invocations,
            "gini": gini,
            "n_new_invocations": sum(c["incremental_invocations"].values()),
            "events": c.get("events", []),
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

    _print_header("Longitudinal Registry Convergence — Step 18.7")
    print(f"  DB:              {db_path}")
    print(f"  Total cycles:    {len(cycles)}")
    print(f"  KL threshold:    {args.threshold}")
    print(f"  Min consecutive: {args.min_consecutive}")
    print(f"  Primary KL:      {primary_kl_source}"
          f" ({'proposal-literal' if primary_kl_source == 'utility_scores' else 'invocation-count proxy'})")
    if not has_utility_scores:
        print("  (cycle_utility_scores empty — DB predates Step 18.7 follow-up wiring)")
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

    # --- Distribution-shift analysis (Step 18.8, Pillar 3 RQ #3) ---
    shift_cycle = _detect_shift_point(cycles)
    if shift_cycle is not None:
        _print_header(
            "Distribution-Shift Analysis — Pillar 3 RQ #3"
        )
        print(f"  Shift point detected at cycle {shift_cycle}")
        print(f"  Phase A: cycles 1..{shift_cycle} ({shift_cycle} cycles)")
        print(f"  Phase B: cycles {shift_cycle + 1}..{len(cycles)} "
              f"({len(cycles) - shift_cycle} cycles)")

        # Phase A convergence (within Phase A only)
        phase_a_kls = convergence_kls[:shift_cycle]
        phase_a_convergence_offset = (
            _detect_convergence(
                phase_a_kls[1:], args.threshold, args.min_consecutive,
            )
            if len(phase_a_kls) > 1 else None
        )
        phase_a_converged_cycle = (
            phase_a_convergence_offset + 1 if phase_a_convergence_offset is not None else None
        )

        # KL spike magnitude in the window around the shift (shift_cycle and
        # the 3 cycles post-shift). Helps Chapter 6 describe how disruptive
        # the shift was.
        spike_window = convergence_kls[shift_cycle:shift_cycle + 4]
        finite_spike = [k for k in spike_window if np.isfinite(k)]
        kl_spike = max(finite_spike) if finite_spike else None

        # Phase B re-convergence (cycles after shift). Index into
        # convergence_kls starts at shift_cycle; we run _detect_convergence
        # on that slice, then offset back to 1-indexed full-sequence cycle.
        phase_b_kls = convergence_kls[shift_cycle:]
        phase_b_offset = (
            _detect_convergence(phase_b_kls, args.threshold, args.min_consecutive)
            if phase_b_kls else None
        )
        phase_b_reconverged_cycle = (
            phase_b_offset + shift_cycle if phase_b_offset is not None else None
        )
        phase_b_reconverged_within = (
            phase_b_reconverged_cycle - shift_cycle
            if phase_b_reconverged_cycle is not None else None
        )

        print()
        if phase_a_converged_cycle is not None:
            print(f"  Phase A cycles-to-converge:       {phase_a_converged_cycle}")
        else:
            print(f"  Phase A cycles-to-converge:       (did not converge)")
        if kl_spike is not None:
            print(f"  KL spike magnitude at shift:      {kl_spike:.4f}")
        else:
            print(f"  KL spike magnitude at shift:      (unavailable)")
        if phase_b_reconverged_within is not None:
            print(f"  Phase B cycles-to-re-converge:    "
                  f"{phase_b_reconverged_within} cycles post-shift"
                  f" (at absolute cycle {phase_b_reconverged_cycle})")
        else:
            print(f"  Phase B cycles-to-re-converge:    (did not re-converge within window)")

        print()
        verdict = _classify_adaptation(
            phase_a_converged_cycle,
            phase_b_reconverged_within,
        )
        print(f"  Adaptation verdict: {verdict}")
        print()
        print("  Interpretation guide:")
        print("    - graceful  (<= 2x Phase A time): registry adapts without structural collapse")
        print("    - sluggish  (2x-5x):               registry adapts but slowly — possibly over-fit to Phase A")
        print("    - collapse  (> 5x or never):       registry was Phase-A-specific — not a general optimizer")

    # --- Gini range + seeded-vs-evolved comparison (Pillar 3 RQ #1) ---
    if ginis:
        print()
        print(f"  Gini (final):    {ginis[-1]:.3f}")
        print(f"  Gini range:      [{min(ginis):.3f}, {max(ginis):.3f}]")
        print(f"  # caps (final):  {n_caps[-1]}")
        print(f"  # caps range:    [{min(n_caps)}, {max(n_caps)}]")
        # Seeded vs evolved comparison — proposal Pillar 3 RQ #1.
        if len(ginis) >= 2:
            seeded_gini = ginis[0]
            evolved_gini = ginis[-1]
            delta = evolved_gini - seeded_gini
            print()
            print(f"  Seeded-vs-Evolved Gini (Pillar 3 RQ #1):")
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
        print("   Re-run run_longitudinal.py with Step 18.7 follow-up wiring to get true")
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
            "cycle", "task_id", "n_active_caps",
            "kl_utility", "kl_invocations",
            "gini", "n_new_invocations",
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
    return 0 if convergence_cycle is not None else 2


if __name__ == "__main__":
    sys.exit(main())
