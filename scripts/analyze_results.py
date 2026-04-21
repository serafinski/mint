"""Statistical analysis of evaluation results for thesis Chapter 6.

Produces:
- Disaggregated success rates (all tasks vs synthesis-only T04-T13)
- Exact binomial 95% confidence intervals
- gate_passed and output_in_range as separate columns
- Fisher's exact test (each JIT condition vs static, per task)
- Per-task effect sizes
- Failure breakdown by condition

Usage::

    uv run scripts/analyze_results.py
    uv run scripts/analyze_results.py --db data/eval/metrics.duckdb
    uv run scripts/analyze_results.py --csv  # also export tables to data/analysis/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402
from scipy import stats  # noqa: E402

# ── Constants ────────────────────────────────────────────────────────────────

CONDITIONS = ["full", "no_verification", "no_optimization", "static"]
RETRIEVAL_TASKS = {"T01", "T02", "T03"}

# Security audit finding #17 (2026-04-20): pre-fix hard-coded
# ``range(1, 19)`` silently dropped T19 and T20 from every per-task
# table — both the only exploratory-AGENT task (T19) and the only
# policy-SKILL task (T20), exactly the two tasks Chapter 6's per-type
# analysis most depends on. Import from the single source of truth so
# any future testbed extension flows through automatically.
from tests.evaluation.tasks import ALL_TASKS  # noqa: E402

ALL_TASK_IDS = [t.task_id for t in ALL_TASKS]
SYNTHESIS_TASK_IDS = [t for t in ALL_TASK_IDS if t not in RETRIEVAL_TASKS]
CONDITION_LABELS = {
    "static": "Static",
    "no_verification": "JIT only",
    "no_optimization": "JIT + Verif",
    "full": "JIT + Verif + Opt",
}


# ── Helpers ──────────────────────────────────────────────────────────────────


def binom_ci(successes: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Exact (Clopper-Pearson) binomial confidence interval."""
    if n == 0:
        return (0.0, 0.0)
    alpha = 1 - confidence
    # Clopper-Pearson uses the beta distribution
    if successes == 0:
        lo = 0.0
    else:
        lo = stats.beta.ppf(alpha / 2, successes, n - successes + 1)
    if successes == n:
        hi = 1.0
    else:
        hi = stats.beta.ppf(1 - alpha / 2, successes + 1, n - successes)
    return (lo, hi)


def fisher_exact_2x2(
    a_success: int, a_total: int, b_success: int, b_total: int
) -> tuple[float, float]:
    """Fisher's exact test on a 2x2 table. Returns (odds_ratio, p_value)."""
    table = [
        [a_success, a_total - a_success],
        [b_success, b_total - b_success],
    ]
    return stats.fisher_exact(table)


def holm_bonferroni(p_values: list[float | None]) -> list[float | None]:
    """Apply Holm-Bonferroni correction to a list of p-values.

    None entries (missing tests) are passed through unchanged.
    Returns adjusted p-values (capped at 1.0).
    """
    indexed = [(i, p) for i, p in enumerate(p_values) if p is not None]
    if not indexed:
        return list(p_values)

    # Sort by p-value ascending
    indexed.sort(key=lambda x: x[1])
    m = len(indexed)
    adjusted = [None] * len(p_values)

    prev_adj = 0.0
    for rank, (orig_idx, p) in enumerate(indexed):
        # Holm step-down: multiply by (m - rank)
        adj = p * (m - rank)
        # Enforce monotonicity: adjusted values must be non-decreasing
        adj = max(adj, prev_adj)
        adj = min(adj, 1.0)
        adjusted[orig_idx] = adj
        prev_adj = adj

    return adjusted


# ── Data loading ─────────────────────────────────────────────────────────────


def load_data(db_path: str) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(db_path, read_only=True)


# Security audit finding #38 (2026-04-20): module-level scope filter
# resolved at ``main()`` startup. Every SQL query appends this fragment
# to its WHERE clause so analyzers operate on a single run-set. Empty
# string when no scoping is requested (legacy behavior). Populated by
# ``_apply_scope`` below.
_SCOPE_SQL: str = ""

# ext7 #52 (2026-04-20): banner to mark Chapter-6 output tables as
# [INCOMPLETE: ...] when --allow-incomplete was used. Populated at startup
# from the completeness check; empty when the dataset matches the expected
# campaign shape.
_COMPLETENESS_BANNER: str = ""


# ext7 #46: canonical eval shape is --n-runs 5 per condition × 4 conditions
# = 20 distinct run_ids, not 4. Pre-fix auto-select of "4 most recent" silently
# dropped 3 of 4 conditions on a canonical eval DB. Configurable via
# --per-condition-cap.
_DEFAULT_PER_CONDITION_CAP = 5


def _scope_for_table(table: str, scope_sql: str | None = None) -> str:
    """Return the scope fragment appropriate for ``table``.

    Most queries read ``evaluation_runs`` and use ``_SCOPE_SQL`` verbatim —
    its ``timestamp`` column name matches the --since scope fragment's
    hardcoded column. Two tables (``invocation_events``, ``cycle_events``)
    use ``occurred_at`` as their time column instead; applying the raw
    ``_SCOPE_SQL`` to queries against those tables crashes DuckDB with
    ``Binder Error: Referenced column "timestamp" not found in FROM clause``.

    ext11 #110 (2026-04-20): pre-fix, ``print_gini_summary`` hand-rewrote
    ``run_id`` → ``ie.run_id`` but did nothing for the ``--since`` case, so
    ``--since`` scoped invocations of the Gini query crashed hard. This
    helper rewrites the column name when the active scope is
    ``--since``-based. ``--run-ids`` / auto-select scopes use ``run_id``
    which all three tables share, so this is a no-op for those cases.
    """
    if scope_sql is None:
        scope_sql = _SCOPE_SQL
    if table in ("invocation_events", "cycle_events"):
        return scope_sql.replace(" AND timestamp >= ", " AND occurred_at >= ")
    return scope_sql


def _resolve_scope(
    conn: duckdb.DuckDBPyConnection,
    run_ids: list[str] | None,
    since: str | None,
    per_condition_cap: int = _DEFAULT_PER_CONDITION_CAP,
) -> str:
    """Return a SQL WHERE fragment that scopes queries to a single run-set.

    Security audit finding #38 (2026-04-20): pre-fix, ``analyze_results.py``
    pooled every non-pilot row in the DB. When the DB contains multiple
    runs (re-runs after code changes, different configs, etc.), every
    table mixes populations.

    Resolution:
    - Explicit ``run_ids`` → filter on exactly those.
    - ``since`` → filter on timestamp.
    - Neither: auto-select the ``per_condition_cap`` most recent run_ids
      PER CONDITION (ext7 #46 — the canonical run-sheet eval shape is
      ``--n-runs 5 × 4 conditions = 20 distinct run_ids``, so a fixed
      4-total cap silently drops 3 of 4 conditions). Default: 5 per
      condition. Fails loud with exit code 2 if the selected set doesn't
      cover every condition present in the DB.

    Returns "" when no scoping is applied (legacy mode — unsafe on
    multi-run DBs).
    """
    import sys as _sys

    if run_ids:
        safe_ids = [r.replace("'", "''") for r in run_ids]
        return " AND run_id IN ({})".format(
            ", ".join(f"'{r}'" for r in safe_ids)
        )

    if since is not None:
        safe_since = since.replace("'", "''")
        return f" AND timestamp >= '{safe_since}'"

    # Auto-select: balanced per-condition. For each distinct condition, pick
    # the N most recent run_ids; union across conditions. This preserves the
    # canonical eval shape (5 per condition × 4 conditions = 20 run_ids) while
    # tolerating DBs with fewer runs per condition.
    total_available = conn.execute(
        "SELECT COUNT(DISTINCT run_id) FROM evaluation_runs WHERE is_pilot = FALSE"
    ).fetchone()[0]
    if total_available == 0:
        return ""  # Empty DB or all pilot — no scope needed.

    # One run per condition × 4 conditions = 4 — don't warn, don't scope.
    condition_count = conn.execute(
        "SELECT COUNT(DISTINCT condition) FROM evaluation_runs WHERE is_pilot = FALSE"
    ).fetchone()[0]
    if total_available <= max(condition_count, 1):
        return ""

    # ext12 #114: pre-fix this CTE ranked ROWS (one row per task) via
    # ROW_NUMBER(), then DISTINCT-collapsed. On the canonical eval shape of
    # 20 tasks per run, rn <= 5 picked 5 rows from the single most recent
    # run, then DISTINCT collapsed to 1 run_id per condition. The inner
    # ``distinct_runs`` CTE now collapses to one row per (run_id, condition)
    # BEFORE ranking, so ROW_NUMBER() ranks distinct run_ids and rn <= N
    # correctly selects N run_ids per condition.
    selected_rows = conn.execute(
        """
        WITH distinct_runs AS (
            SELECT run_id, condition, MAX(timestamp) AS latest_ts
            FROM evaluation_runs
            WHERE is_pilot = FALSE
            GROUP BY run_id, condition
        ),
        ranked AS (
            SELECT run_id, condition, latest_ts,
                   ROW_NUMBER() OVER (
                       PARTITION BY condition ORDER BY latest_ts DESC
                   ) AS rn
            FROM distinct_runs
        )
        SELECT run_id, condition
        FROM ranked
        WHERE rn <= ?
        ORDER BY condition, run_id
        """,
        [per_condition_cap],
    ).fetchall()
    latest = [r[0] for r in selected_rows]
    selected_conditions = {r[1] for r in selected_rows}

    # Loud-fail if the auto-select dropped any condition. Under fixed
    # condition-order (see #48), a 4-total auto-select could cover exactly
    # one condition — worst-case bias is condition-dependent.
    if len(selected_conditions) < condition_count:
        print(
            f"ERROR: auto-select covered {len(selected_conditions)}/{condition_count} "
            f"conditions. Pass --run-ids explicitly to include all conditions, "
            f"or --per-condition-cap to adjust.",
            file=_sys.stderr,
        )
        _sys.exit(2)

    if total_available > len(latest):
        print(
            f"WARNING: DB contains {total_available} distinct non-pilot run_ids; "
            f"auto-selected {len(latest)} total across {len(selected_conditions)} "
            f"conditions (target cap: {per_condition_cap} per condition). "
            f"Pass --run-ids to override.",
            file=_sys.stderr,
        )

    safe_latest = [r.replace("'", "''") for r in latest]
    return " AND run_id IN ({})".format(
        ", ".join(f"'{r}'" for r in safe_latest)
    )


def query_condition_summary(
    conn: duckdb.DuckDBPyConnection, task_filter: str = "TRUE"
) -> list[dict]:
    """Per-condition summary: success, gate_pass, output_in_range counts.

    Security audit finding #79 (2026-04-19, ext9): every correctness
    column now applies the SAME shape-mismatch tightening — pre-fix
    ``successes`` tightened by ``NOT output_shape_mismatch`` but the
    sibling ``output_in_range`` column did not, so Table 1 readers saw
    contradictory headline numbers ("90% Output OK but only 50%
    Success") with no column explaining the gap. A new
    ``shape_mismatch`` count is also reported so the gap is visible
    directly instead of inferred.
    """
    rows = conn.execute(
        f"""
        SELECT
            condition,
            COUNT(*) AS n,
            SUM(CASE WHEN gate_passed AND output_in_range AND NOT COALESCE(output_shape_mismatch, FALSE) THEN 1 ELSE 0 END) AS successes,
            SUM(CASE WHEN gate_passed THEN 1 ELSE 0 END) AS gate_passed,
            SUM(CASE WHEN output_in_range AND NOT COALESCE(output_shape_mismatch, FALSE) THEN 1 ELSE 0 END) AS output_in_range,
            SUM(CASE WHEN COALESCE(output_shape_mismatch, FALSE) THEN 1 ELSE 0 END) AS shape_mismatch
        FROM evaluation_runs
        WHERE is_pilot = FALSE AND ({task_filter}){_SCOPE_SQL}
        GROUP BY condition
        ORDER BY condition
        """
    ).fetchall()
    return [
        {
            "condition": r[0],
            "n": r[1],
            "successes": r[2],
            "gate_passed": r[3],
            "output_in_range": r[4],
            "shape_mismatch": r[5],
        }
        for r in rows
    ]


def query_per_task(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    """Per-task, per-condition success counts.

    Security audit finding #79 (2026-04-19, ext9): ``output_in_range``
    applies the shape-mismatch tightening consistently with
    ``successes`` so per-task rows stay internally coherent. See
    ``query_condition_summary`` for the root rationale.
    """
    rows = conn.execute(
        f"""
        SELECT
            task_id, condition,
            COUNT(*) AS n,
            SUM(CASE WHEN gate_passed AND output_in_range AND NOT COALESCE(output_shape_mismatch, FALSE) THEN 1 ELSE 0 END) AS successes,
            SUM(CASE WHEN gate_passed THEN 1 ELSE 0 END) AS gate_passed,
            SUM(CASE WHEN output_in_range AND NOT COALESCE(output_shape_mismatch, FALSE) THEN 1 ELSE 0 END) AS output_in_range
        FROM evaluation_runs
        WHERE is_pilot = FALSE{_SCOPE_SQL}
        GROUP BY task_id, condition
        ORDER BY task_id, condition
        """
    ).fetchall()
    return [
        {
            "task_id": r[0],
            "condition": r[1],
            "n": r[2],
            "successes": r[3],
            "gate_passed": r[4],
            "output_in_range": r[5],
        }
        for r in rows
    ]


def query_failure_breakdown(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    rows = conn.execute(
        f"""
        SELECT condition, failure_reason, COUNT(*) AS n
        FROM evaluation_runs
        WHERE is_pilot = FALSE AND failure_reason != 'none'{_SCOPE_SQL}
        GROUP BY condition, failure_reason
        ORDER BY condition, n DESC
        """
    ).fetchall()
    return [{"condition": r[0], "reason": r[1], "n": r[2]} for r in rows]


# ── Table formatting ─────────────────────────────────────────────────────────


def fmt_pct(k: int, n: int) -> str:
    return f"{k}/{n} ({k/n*100:.1f}%)" if n > 0 else "—"


def fmt_ci(lo: float, hi: float) -> str:
    return f"[{lo*100:.1f}%, {hi*100:.1f}%]"


def fmt_p(p: float) -> str:
    if p < 0.001:
        return "<0.001"
    if p < 0.01:
        return f"{p:.3f}"
    return f"{p:.2f}"


# ── Report sections ──────────────────────────────────────────────────────────


def print_condition_table(title: str, data: list[dict]) -> list[str]:
    """Print condition summary table with CIs. Returns CSV lines.

    Security audit finding #79 (2026-04-19, ext9): adds a Shape
    Mismatch column so readers can see the gap between ``Output OK``
    (shape-checked) and ``Gate Pass`` (raw validator) directly.
    """
    # ext7 #52: append the [INCOMPLETE: ...] banner (if any) to the title.
    lines = [f"\n{'='*80}", f"  {title}{_COMPLETENESS_BANNER}", f"{'='*80}"]
    header = (
        f"{'Condition':<22} {'Success':>14} {'95% CI':>18} "
        f"{'Gate Pass':>14} {'Output OK':>14} {'ShapeMismatch':>14}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    csv_lines = [
        "condition,n,successes,success_rate,ci_lo,ci_hi,gate_passed,"
        "output_in_range,shape_mismatch"
    ]
    for d in data:
        label = CONDITION_LABELS.get(d["condition"], d["condition"])
        lo, hi = binom_ci(d["successes"], d["n"])
        shape_mismatch = d.get("shape_mismatch", 0)
        lines.append(
            f"{label:<22} {fmt_pct(d['successes'], d['n']):>14} {fmt_ci(lo, hi):>18} "
            f"{fmt_pct(d['gate_passed'], d['n']):>14} "
            f"{fmt_pct(d['output_in_range'], d['n']):>14} "
            f"{fmt_pct(shape_mismatch, d['n']):>14}"
        )
        csv_lines.append(
            f"{d['condition']},{d['n']},{d['successes']},"
            f"{d['successes']/d['n']:.4f},{lo:.4f},{hi:.4f},"
            f"{d['gate_passed']},{d['output_in_range']},{shape_mismatch}"
        )
    for line in lines:
        print(line)
    return csv_lines


def print_per_task_table(per_task: list[dict]) -> list[str]:
    """Per-task success with CIs and Holm-Bonferroni-corrected Fisher's exact vs static."""
    lines = [
        f"\n{'='*80}",
        "  Per-Task Success Rates with 95% CIs and Fisher's Exact Test (JIT vs Static)",
        "  (p-values adjusted via Holm-Bonferroni correction)",
        f"{'='*80}",
    ]

    # Organize data: task_id -> condition -> row
    by_task: dict[str, dict[str, dict]] = {}
    for d in per_task:
        by_task.setdefault(d["task_id"], {})[d["condition"]] = d

    header = (
        f"{'Task':<5} {'Type':<6} "
        f"{'Full':>10} {'CI':>15} "
        f"{'NoVerif':>10} {'CI':>15} "
        f"{'NoOpt':>10} {'CI':>15} "
        f"{'Static':>10} "
        f"{'p(F)':>7} {'p(NV)':>7} {'p(NO)':>7}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    csv_lines = [
        "task_id,task_type,"
        "full_k,full_n,full_rate,full_ci_lo,full_ci_hi,"
        "noverif_k,noverif_n,noverif_rate,noverif_ci_lo,noverif_ci_hi,"
        "noopt_k,noopt_n,noopt_rate,noopt_ci_lo,noopt_ci_hi,"
        "static_k,static_n,static_rate,"
        "fisher_full_p_adj,fisher_noverif_p_adj,fisher_noopt_p_adj"
    ]

    # Pass 1: collect all raw p-values for Holm-Bonferroni correction
    all_raw_p: list[float | None] = []  # flat list, 3 per task
    task_data: list[tuple[list[str], list[str]]] = []  # (parts, csv_parts) per task

    for tid in ALL_TASK_IDS:
        td = by_task.get(tid, {})
        task_type = "retr" if tid in RETRIEVAL_TASKS else "synth"

        parts = [f"{tid:<5} {task_type:<6}"]
        csv_parts = [f"{tid},{task_type}"]

        # Static reference
        s = td.get("static", {"successes": 0, "n": 0})

        for cond in ["full", "no_verification", "no_optimization"]:
            d = td.get(cond, {"successes": 0, "n": 0})
            lo, hi = binom_ci(d["successes"], d["n"])
            parts.append(f"{fmt_pct(d['successes'], d['n']):>10} {fmt_ci(lo, hi):>15}")
            csv_parts.append(
                f"{d['successes']},{d['n']},{d['successes']/d['n']:.4f},{lo:.4f},{hi:.4f}"
                if d["n"] > 0
                else "0,0,0,0,0"
            )

        parts.append(f"{fmt_pct(s['successes'], s['n']):>10}")
        csv_parts.append(
            f"{s['successes']},{s['n']},{s['successes']/s['n']:.4f}"
            if s["n"] > 0
            else "0,0,0"
        )

        # Fisher's exact test: each JIT condition vs static (raw p-values)
        for cond in ["full", "no_verification", "no_optimization"]:
            d = td.get(cond, {"successes": 0, "n": 0})
            if d["n"] > 0 and s["n"] > 0:
                _, p = fisher_exact_2x2(d["successes"], d["n"], s["successes"], s["n"])
                all_raw_p.append(p)
            else:
                all_raw_p.append(None)

        task_data.append((parts, csv_parts))

    # Pass 2: apply Holm-Bonferroni correction across ALL per-task tests
    adjusted_p = holm_bonferroni(all_raw_p)

    # Pass 3: format corrected p-values into output
    for task_idx, (parts, csv_parts) in enumerate(task_data):
        p_values = adjusted_p[task_idx * 3 : task_idx * 3 + 3]
        for p in p_values:
            if p is not None:
                parts.append(f"{fmt_p(p):>7}")
            else:
                parts.append(f"{'—':>7}")
        csv_parts.append(",".join(f"{p:.6f}" if p is not None else "" for p in p_values))

        lines.append(" ".join(parts))
        csv_lines.append(",".join(csv_parts))

    for line in lines:
        print(line)
    return csv_lines


def print_gate_vs_output_table(per_task: list[dict]) -> None:
    """Show gate_passed and output_in_range as separate columns (Critique A recommendation)."""
    lines = [
        f"\n{'='*80}",
        "  Gate Pass vs Output Validation (Separated) — Critique A Recommendation",
        f"{'='*80}",
    ]

    by_task: dict[str, dict[str, dict]] = {}
    for d in per_task:
        by_task.setdefault(d["task_id"], {})[d["condition"]] = d

    header = (
        f"{'Task':<5} "
        f"{'Full gate':>10} {'Full out':>10} {'Gap':>5} "
        f"{'NV gate':>10} {'NV out':>10} {'Gap':>5} "
        f"{'NO gate':>10} {'NO out':>10} {'Gap':>5}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    for tid in ALL_TASK_IDS:
        td = by_task.get(tid, {})
        parts = [f"{tid:<5}"]
        for cond in ["full", "no_verification", "no_optimization"]:
            d = td.get(cond, {"gate_passed": 0, "output_in_range": 0, "n": 0})
            n = d["n"]
            gp = d["gate_passed"]
            oir = d["output_in_range"]
            gap = gp - oir if n > 0 else 0
            parts.append(f"{fmt_pct(gp, n):>10} {fmt_pct(oir, n):>10} {gap:>5}")
        lines.append(" ".join(parts))

    for line in lines:
        print(line)


def print_failure_breakdown(
    failures: list[dict],
    condition_totals: dict[str, int] | None = None,
) -> None:
    """Print per-condition failure breakdown.

    Security audit finding #85 (2026-04-19, ext9): include a
    denominator (N) and a rate column so identical failure counts
    across conditions with different N aren't read as equivalent
    outcomes. Pre-fix a JIT condition with N=100 and 5 failures
    looked "as bad as" a static condition with N=20 and 5 failures —
    5% vs 25%, a 5x invisible gap. ``condition_totals`` is optional
    for back-compat; absent totals fall back to count-only rendering
    with an explicit banner.
    """
    lines = [
        f"\n{'='*80}",
        "  Failure Breakdown by Condition",
        f"{'='*80}",
    ]
    if condition_totals:
        header = (
            f"{'Condition':<22} {'Reason':<22} "
            f"{'Count':>6} {'N':>6} {'Rate':>8}"
        )
    else:
        header = f"{'Condition':<22} {'Reason':<22} {'Count':>6}"
        lines.append(
            "  [WARN] no condition totals provided — counts only. "
            "Pass condition_totals to see rates."
        )
    lines.append(header)
    lines.append("-" * len(header))
    for d in failures:
        label = CONDITION_LABELS.get(d["condition"], d["condition"])
        if condition_totals:
            total = condition_totals.get(d["condition"], 0)
            rate = f"{100.0 * d['n'] / total:.1f}%" if total else "—"
            lines.append(
                f"{label:<22} {d['reason']:<22} "
                f"{d['n']:>6} {total:>6} {rate:>8}"
            )
        else:
            lines.append(f"{label:<22} {d['reason']:<22} {d['n']:>6}")
    for line in lines:
        print(line)


def print_cluster_analysis(all_data: list[dict], synth_data: list[dict]) -> None:
    """Frame JIT conditions as a cluster — Critique recommendation."""
    lines = [
        f"\n{'='*80}",
        "  JIT Cluster Analysis (Critique Recommendation: cluster, not ranking)",
        f"{'='*80}",
    ]

    jit_conds = [d for d in synth_data if d["condition"] != "static"]
    static = next((d for d in synth_data if d["condition"] == "static"), None)

    if not jit_conds or not static:
        lines.append("  Insufficient data for cluster analysis.")
        for line in lines:
            print(line)
        return

    jit_rates = [d["successes"] / d["n"] for d in jit_conds if d["n"] > 0]
    jit_min = min(jit_rates) * 100
    jit_max = max(jit_rates) * 100
    static_rate = static["successes"] / static["n"] * 100 if static["n"] > 0 else 0

    lines.append("  Synthesis tasks only (T04-T13):")
    lines.append(f"    JIT cluster:  {jit_min:.1f}% – {jit_max:.1f}%")
    lines.append(f"    Static:       {static_rate:.1f}%")
    lines.append(
        f"    Gap (cluster): {jit_min - static_rate:.1f} – "
        f"{jit_max - static_rate:.1f} pp"
    )

    # Security audit finding #80 (2026-04-19, ext9): report the full-
    # system gap separately so readers see the headline "full vs
    # static" number alongside the cluster spread. Pre-fix the cluster
    # range alone buried the full-system gap at the upper bound and
    # anchored the lower bound to the weakest ablation — systematically
    # under-selling the full-system benefit for a hurried reader.
    full_cond = next(
        (d for d in jit_conds if d["condition"] == "full"), None
    )
    if full_cond and full_cond["n"] > 0:
        full_rate = full_cond["successes"] / full_cond["n"] * 100
        lines.append(
            f"    Gap (full vs static): {full_rate - static_rate:.1f} pp "
            f"(full = {full_rate:.1f}%)"
        )
    lines.append("")

    # Pairwise Fisher's exact between JIT conditions (with Holm-Bonferroni)
    lines.append("  Pairwise Fisher's exact between JIT conditions (Holm-Bonferroni adjusted):")
    pairs: list[tuple[str, str, float]] = []
    raw_cluster_p: list[float] = []
    for i in range(len(jit_conds)):
        for j in range(i + 1, len(jit_conds)):
            a, b = jit_conds[i], jit_conds[j]
            _, p = fisher_exact_2x2(a["successes"], a["n"], b["successes"], b["n"])
            la = CONDITION_LABELS.get(a["condition"], a["condition"])
            lb = CONDITION_LABELS.get(b["condition"], b["condition"])
            pairs.append((la, lb, p))
            raw_cluster_p.append(p)

    adjusted_cluster_p = holm_bonferroni(raw_cluster_p)
    for (la, lb, _raw_p), adj_p in zip(pairs, adjusted_cluster_p):
        sig = " *" if adj_p is not None and adj_p < 0.05 else ""
        lines.append(f"    {la} vs {lb}: p_adj={fmt_p(adj_p)}{sig}")

    lines.append("")

    # Security audit finding #81 (2026-04-19, ext9): the pooled-JIT
    # Fisher test is a statistical fallacy — pooling non-exchangeable
    # ablation conditions (`full`, `no_verification`, `no_optimization`)
    # into a single bucket treats three different interventions as
    # samples of one population and inflates N by 3x, shrinking the
    # p-value artificially. Pre-fix the number was printed alongside
    # the legitimate per-condition tests with a one-line disclaimer —
    # a hurried reader could still misquote it as "p_pooled < 0.01
    # supports JIT". Downgraded here to a DIAGNOSTIC ONLY block,
    # clearly labeled DO NOT CITE so it can still be inspected during
    # development without masquerading as a thesis-quotable result.
    # The per-condition Fisher + Holm-Bonferroni tests above remain the
    # proposal-correct methodology.
    jit_total_k = sum(d["successes"] for d in jit_conds)
    jit_total_n = sum(d["n"] for d in jit_conds)
    _, p_pooled = fisher_exact_2x2(
        jit_total_k, jit_total_n, static["successes"], static["n"]
    )
    lines.append("")
    lines.append("  ===== DIAGNOSTIC ONLY — DO NOT CITE (Chapter 4.5) =====")
    lines.append(
        "  Pooled JIT vs Static — statistically fallacious (non-exchangeable"
    )
    lines.append(
        "  ablations pooled into one bucket; inflates N by ~3x, shrinks p):"
    )
    lines.append(
        f"    {jit_total_k}/{jit_total_n} "
        f"({jit_total_k/jit_total_n*100:.1f}%) vs "
        f"{static['successes']}/{static['n']} ({static_rate:.1f}%), "
        f"p={fmt_p(p_pooled)}"
    )
    lines.append("  ===== END DIAGNOSTIC =====")

    for line in lines:
        print(line)


def print_token_cost_summary(conn: duckdb.DuckDBPyConnection) -> None:
    """Token cost per condition (from backfilled LangFuse data).

    Security audit finding #100 (2026-04-19, ext10): pre-fix the
    headline ``Avg/JIT Task`` column averaged ONLY over rows with
    ``token_cost > 0``, excluding zero-cost tasks (retrieval-tasks,
    static-matched tasks, tasks that failed before any LLM call) from
    the denominator. For a condition with 20 tasks where 10 cost 1000
    tokens and 10 are free, the reported avg was ``10000/10 = 1000``
    when the actual per-task-across-the-condition cost was ``500`` —
    a 2x over-report. Now reports BOTH ``Avg/Task`` (all rows,
    proposal-correct denominator) AND ``Avg/Nonzero`` (filtered, the
    pre-fix value, kept as a diagnostic). Chapter 6 M4 cites the
    all-rows column.
    """
    rows = conn.execute(
        f"""
        SELECT condition,
               SUM(token_cost) AS total_tokens,
               AVG(token_cost) AS avg_per_task,
               AVG(CASE WHEN token_cost > 0 THEN token_cost ELSE NULL END) AS avg_per_nonzero_task,
               COUNT(CASE WHEN token_cost > 0 THEN 1 END) AS n_with_cost,
               COUNT(*) AS n_total
        FROM evaluation_runs
        WHERE is_pilot = FALSE{_SCOPE_SQL}
        GROUP BY condition
        ORDER BY condition
        """
    ).fetchall()
    lines = [
        f"\n{'='*80}",
        "  Token Cost Summary (backfilled from LangFuse)",
        f"{'='*80}",
    ]
    header = (
        f"{'Condition':<22} {'Total Tokens':>14} {'Avg/Task':>12} "
        f"{'Avg/Nonzero':>14} {'With Cost':>11}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for r in rows:
        label = CONDITION_LABELS.get(r[0], r[0])
        avg_all = f"{r[2]:,.0f}" if r[2] else "—"
        avg_nz = f"{r[3]:,.0f}" if r[3] else "—"
        lines.append(
            f"{label:<22} {r[1]:>14,} {avg_all:>12} {avg_nz:>14} {r[4]:>5}/{r[5]}"
        )
    lines.append("")
    lines.append(
        "  Avg/Task = AVG over ALL rows (proposal-correct denominator; M4 headline). "
        "Avg/Nonzero = AVG over rows with token_cost > 0 (diagnostic; pre-#100 behavior)."
    )
    for line in lines:
        print(line)


def _gini_coefficient(counts: list[int]) -> float:
    """Compute the Gini coefficient for a list of non-negative counts.

    Gini is a measure of inequality in [0, 1]:
    - 0 = perfect equality (every capability invoked the same number of times)
    - 1 = maximum inequality (one capability gets all invocations)

    Proposal §Metrics: *"Capability reuse distribution | Gini coefficient of
    invocation counts across the registry. Healthy registries have moderate
    inequality; unhealthy ones have extreme skew."*

    Uses the standard formula: Gini = sum(|x_i - x_j|) / (2 * n^2 * mean).
    Returns 0.0 for empty lists or all-zeros (degenerate cases).
    """
    if not counts:
        return 0.0
    total = sum(counts)
    n = len(counts)
    if total == 0 or n == 0:
        return 0.0
    sorted_counts = sorted(counts)
    # Efficient O(n log n) form: 2 * sum((i+1) * x_i) / (n * sum(x)) - (n+1)/n
    cumulative = sum((i + 1) * x for i, x in enumerate(sorted_counts))
    return (2.0 * cumulative) / (n * total) - (n + 1.0) / n


def print_gini_summary(conn: duckdb.DuckDBPyConnection) -> None:
    """Gini coefficient of capability invocation counts per condition.

    Addresses proposal §Metrics item 3: *"Capability reuse distribution —
    Gini coefficient of invocation counts across the registry. Healthy
    registries have moderate inequality; unhealthy ones have extreme skew."*

    Reads from the persistent ``invocation_events`` table (Step 18.5
    harness wiring). Groups invocations by (condition, run_id) and
    computes one Gini per run; reports mean + spread across the N runs
    per condition. Also reports a condition-level aggregate Gini (all
    invocations pooled).

    Degenerate cases (empty counts, all zeros) → Gini 0.0. Conditions
    with no invocation data skipped (happens if an old eval DB predates
    Step 18.5 wiring).
    """
    # Check the table exists — old eval DBs won't have it.
    try:
        conn.execute("SELECT 1 FROM invocation_events LIMIT 1")
    except Exception as exc:  # noqa: BLE001
        print(f"\n{'='*80}")
        print("  Gini Coefficient of Capability Invocation Counts")
        print(f"{'='*80}")
        print(
            f"  (skipped: invocation_events table not available — {exc.__class__.__name__}. "
            "Re-run the eval with Step 18.5 wiring to populate this.)"
        )
        return

    # Per-condition, per-run Gini
    # ext7 #44: filter by scope BEFORE grouping.
    # ext7 #50: exclude pilot invocations via the new is_pilot column. Pre-fix
    # the table had no pilot marker; legacy rows are backfilled via JOIN on
    # EvaluationStore init. COALESCE defends against any remaining NULL cases.
    #
    # ext8 #78 (2026-04-20): exclude invocations of capabilities that were
    # later pruned (optimizer operation). Pre-fix, pruned caps' historical
    # high-count invocations remained as "ghost" rows in the Gini
    # computation, inflating inequality even after the optimizer
    # successfully reduced active-registry concentration.
    #
    # ext11 #109 (2026-04-20): the prune subquery is now correlated on
    # (capability_id, run_id) AND scoped by ``_scope_for_table("cycle_events")``.
    # Pre-fix, the subquery selected every prune event across the whole DB
    # — pruning ``cap_x`` in run B erased run A's invocations of ``cap_x``
    # from run A's Gini. Correlating on run_id confines the exclusion to the
    # run that actually pruned the cap; scoping the subquery is
    # belt-and-suspenders for future cross-run bugs.
    #
    # ext11 #110 (2026-04-20): both fragments go through ``_scope_for_table``
    # so ``--since`` rewrites the column to ``occurred_at`` instead of the
    # ``timestamp`` column that only exists on ``evaluation_runs``.
    scope_ie = _scope_for_table("invocation_events").replace(
        " AND run_id ", " AND ie.run_id "
    )
    scope_ce = _scope_for_table("cycle_events")
    try:
        per_run_rows = conn.execute(
            f"""
            SELECT ie.condition, ie.run_id, ie.capability_id, COUNT(*) AS invocations
            FROM invocation_events ie
            LEFT JOIN (
                SELECT DISTINCT run_id, capability_id
                FROM cycle_events
                WHERE event_type = 'pruned'
                  {scope_ce}
            ) pruned
                ON ie.capability_id = pruned.capability_id
                AND ie.run_id = pruned.run_id
            WHERE NOT COALESCE(ie.is_pilot, FALSE)
              AND pruned.capability_id IS NULL
              {scope_ie}
            GROUP BY ie.condition, ie.run_id, ie.capability_id
            ORDER BY ie.condition, ie.run_id, ie.capability_id
            """
        ).fetchall()
    except Exception:  # noqa: BLE001 — legacy DB without cycle_events
        per_run_rows = conn.execute(
            f"""
            SELECT condition, run_id, capability_id, COUNT(*) AS invocations
            FROM invocation_events
            WHERE NOT COALESCE(is_pilot, FALSE){_scope_for_table("invocation_events")}
            GROUP BY condition, run_id, capability_id
            ORDER BY condition, run_id, capability_id
            """
        ).fetchall()

    if not per_run_rows:
        print(f"\n{'='*80}")
        print("  Gini Coefficient of Capability Invocation Counts")
        print(f"{'='*80}")
        print("  (skipped: invocation_events table is empty)")
        # ext13 #126: globally-empty invocation_events used to silently
        # exit 0 with M3 absent from Chapter 6. Return False so main()
        # accumulates this in ``missing_metrics`` and exits 7.
        return False

    # Group by (condition, run_id) → counts keyed by capability, so we
    # can zero-pad against the per-condition active-registry proxy
    # below. Also collect the per-condition union of capability ids.
    from collections import defaultdict
    by_run: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    by_condition: dict[str, list[int]] = defaultdict(list)
    condition_caps: dict[str, set[str]] = defaultdict(set)
    for cond, run_id, cap_id, inv_count in per_run_rows:
        by_run[(cond, run_id)][cap_id] = int(inv_count)
        by_condition[cond].append(int(inv_count))
        condition_caps[cond].add(cap_id)

    # Security audit finding #96 (2026-04-19, ext9): zero-pad the
    # per-run counts with all capabilities that were EVER invoked
    # within this condition. Pre-fix the Gini was computed only over
    # caps actually invoked in that specific run, so a run that used
    # one cap 100 times reported Gini=0.0 (perfect equality of a
    # single-element list) when the proposal-correct measure — "how
    # concentrated are invocations across the registry?" — demanded
    # ~0.95 (extreme concentration). Using the per-condition
    # cap-union as the denominator is an approximation of the active
    # registry size (legacy DBs don't carry cap-registry size per run)
    # and correctly registers unused active caps as zeros.
    condition_ginis: dict[str, list[float]] = defaultdict(list)
    for (cond, _run_id), run_counts in by_run.items():
        all_caps_for_condition = condition_caps[cond]
        padded = [
            run_counts.get(cap_id, 0) for cap_id in all_caps_for_condition
        ]
        condition_ginis[cond].append(_gini_coefficient(padded))

    print(f"\n{'='*80}")
    print("  Gini Coefficient of Capability Invocation Counts (proposal §Metrics)")
    print(f"{'='*80}")
    print(
        "  Per-condition summary: mean Gini across N independent runs,"
        " plus the pooled aggregate"
    )
    print("  (all invocations across runs in that condition treated as one distribution).")
    header = (
        f"  {'Condition':<22} {'N runs':>8} {'Mean Gini':>10} "
        f"{'Min':>7} {'Max':>7} {'Aggregate':>10}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for cond in CONDITIONS:
        run_ginis = condition_ginis.get(cond, [])
        if not run_ginis:
            print(f"  {CONDITION_LABELS.get(cond, cond):<22} {'—':>8} {'—':>10} {'—':>7} {'—':>7} {'—':>10}")
            continue
        mean = sum(run_ginis) / len(run_ginis)
        aggregate = _gini_coefficient(by_condition[cond])
        label = CONDITION_LABELS.get(cond, cond)
        print(
            f"  {label:<22} {len(run_ginis):>8d} {mean:>10.3f} "
            f"{min(run_ginis):>7.3f} {max(run_ginis):>7.3f} {aggregate:>10.3f}"
        )

    print(
        "\n  Interpretation per proposal: mean Gini near 0 = every capability "
        "invoked equally (\n  likely signals over-generation of narrow caps "
        "that get used once); mean Gini\n  near 1 = extreme skew (a few caps "
        "carry all traffic, others are dead weight). The\n  proposal's "
        "\"healthy registry\" prediction is moderate values (~0.3-0.6)."
    )

    # ext13 #128: per-condition coverage check. ext13 #126 already covers
    # the globally-empty case above; this sibling handles the case where
    # invocation_events has rows for SOME conditions but not all — the
    # JIT-vs-static M3 comparison needs every condition present.
    missing_conditions = [
        cond for cond in CONDITIONS
        if not condition_ginis.get(cond)
    ]
    if missing_conditions:
        print(
            f"\n  ⚠ M3 partial coverage: no invocation_events for "
            f"{len(missing_conditions)}/{len(CONDITIONS)} conditions: "
            f"{', '.join(missing_conditions)}",
            file=sys.stderr,
        )
        return False
    return True


def print_classification_accuracy(conn: duckdb.DuckDBPyConnection) -> None:
    """Core-Work Classification Accuracy — task-level primary-type match.

    Operational proxy for proposal Pillar 1 RQ #1:
        *"How accurately can an LLM classify capability gaps into the
        three artifact types (MCP tool vs. Skill vs. Agent)?"*

    **What we actually measure.** For each task, the pipeline's chain
    can contain multiple gaps of potentially different types (e.g., T11
    produces ``[mcp_tool, skill, mcp_tool]`` — a parse + a policy-driven
    aggregation + a file-writer). ``evaluation_runs.capability_type`` is
    the most-capable type in the chain (priority AGENT > SKILL > MCP_TOOL,
    see ``harness.py:585``). We compare that primary type to the author-
    assigned ``TaskDefinition.expected_type`` and compute:

    - Per-condition hit rate (correct / total)
    - 3×3 confusion matrix (expected row × primary-classified column)
    - Per-expected-type row accuracy

    **Why task-level primary-match, not literal per-gap accuracy.**

    1. **Proposal's PURPOSE is correct routing.** §Pillar 1 claims the
       classification prevents *"the common mistake of treating all
       generated capabilities as the same kind of artifact. An MCP tool
       that should have been a skill will be too rigid; a skill that
       should have been an agent will lack the autonomy to handle
       state."* The outcome that matters is: does the core-work land on
       the right backend? Primary-match answers this directly.

    2. **Per-gap ground truth isn't well-defined.** Gaps are decomposed
       dynamically by the LLM — T11 might produce 3 or 4 gaps across
       different runs. Authoring per-gap expected types would either
       require a canonical decomposition (contradicts adaptive
       decomposition) or rule-based expectations (which measures rule
       compliance, not classifier judgment).

    3. **Structural overrides would contaminate per-gap measurement.**
       Per ``_enforce_file_writer_mcp_tool`` in ``classifier.py``, gaps
       starting with ``write-`` are forced to MCP_TOOL when the task
       has required_files. A per-gap metric would count these forced
       decisions as "classifier correct", systematically inflating
       accuracy. Primary-match sidesteps this: file-writer MCP_TOOLs
       are less-capable than any SKILL/AGENT, so ``min(priority)``
       ignores them.

    4. **Proposal's natural granularity is per-task.** The four
       baseline conditions × 5 runs × 20 tasks = 400 records; the
       cross-baseline comparison that Chapter 6 hinges on is per-task.

    **What we lose by not going per-gap:** classifier mistakes on
    secondary/glue gaps (e.g., if the classifier mis-routed the join
    step in T11 but got the primary SKILL right, primary-match sees
    only the hit). Granularity concerns (one SKILL vs many SKILLs for
    the same core work) are Pillar 3's domain — handled by Gini
    coefficient, Split, and Merge operations. Not the classification
    metric's job.

    **Baseline scope.** Static baseline EXCLUDED — bypasses the
    classifier entirely (direct registry lookup to the pre-written
    implementation). The three JIT conditions (no_verification /
    no_optimization / full) all exercise the classifier; their
    aggregate is the Chapter 6 headline number.
    """
    # Import here to avoid circular-import friction at module load time.
    try:
        from tests.evaluation.tasks import ALL_TASKS
    except Exception as exc:  # noqa: BLE001
        print(f"\n{'='*80}")
        print("  Core-Work Classification Accuracy — task-level primary-type match")
        print("  (operational proxy for Pillar 1 RQ #1; see docstring for framing)")
        print(f"{'='*80}")
        print(f"  (skipped: couldn't import ALL_TASKS — {exc.__class__.__name__})")
        return

    expected_map: dict[str, str | None] = {t.task_id: t.expected_type for t in ALL_TASKS}
    scorable = {tid for tid, et in expected_map.items() if et is not None}
    if not scorable:
        print(f"\n{'='*80}")
        print("  Core-Work Classification Accuracy — task-level primary-type match")
        print("  (operational proxy for Pillar 1 RQ #1; see docstring for framing)")
        print(f"{'='*80}")
        print("  (skipped: no tasks have expected_type assigned)")
        return

    rows = conn.execute(
        f"""
        SELECT task_id, condition, capability_type
        FROM evaluation_runs
        WHERE is_pilot = FALSE AND condition != 'static'{_SCOPE_SQL}
        """
    ).fetchall()

    if not rows:
        print(f"\n{'='*80}")
        print("  Core-Work Classification Accuracy — task-level primary-type match")
        print("  (operational proxy for Pillar 1 RQ #1; see docstring for framing)")
        print(f"{'='*80}")
        print("  (skipped: no non-pilot JIT-condition rows in evaluation_runs)")
        return

    # Per-condition accuracy
    from collections import defaultdict
    per_condition = defaultdict(lambda: {"hit": 0, "total": 0})
    confusion: dict[tuple[str, str], int] = defaultdict(int)
    for task_id, cond, classified in rows:
        if task_id not in scorable:
            continue
        expected = expected_map[task_id]
        # Normalize classified_type — can be None if the pipeline didn't
        # reach classification (e.g., decomposition failure).
        classified_norm = (classified or "unclassified").lower()
        per_condition[cond]["total"] += 1
        if classified_norm == expected:
            per_condition[cond]["hit"] += 1
        confusion[(expected, classified_norm)] += 1

    print(f"\n{'='*80}")
    print("  Core-Work Classification Accuracy — task-level primary-type match")
    print("  (operational proxy for Pillar 1 RQ #1; see docstring for framing)")
    print(f"{'='*80}")
    print(
        f"  Scorable tasks: {len(scorable)}/{len(expected_map)} "
        f"(excludes tasks with expected_type=None). Classifier bypassed for"
        " static baseline."
    )
    header = f"  {'Condition':<22} {'Hit / Total':>14} {'Accuracy':>10}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for cond in ["no_verification", "no_optimization", "full"]:
        stats = per_condition.get(cond, {"hit": 0, "total": 0})
        if stats["total"] == 0:
            print(f"  {CONDITION_LABELS.get(cond, cond):<22} {'—':>14} {'—':>10}")
            continue
        acc = stats["hit"] / stats["total"]
        print(
            f"  {CONDITION_LABELS.get(cond, cond):<22} "
            f"{stats['hit']:>5}/{stats['total']:<8} {acc*100:>9.1f}%"
        )

    # Confusion matrix (expected row × classified column)
    types = ["mcp_tool", "skill", "agent"]
    all_classified = set()
    for (_exp, cls), _n in confusion.items():
        all_classified.add(cls)
    # Display columns: canonical three types, then any extras (e.g. "unclassified")
    columns = types + sorted(c for c in all_classified if c not in types)

    print(f"\n  Confusion matrix (rows = expected, columns = classified, aggregated over JIT conditions):")
    col_header = "  " + " " * 11 + " | " + " | ".join(f"{c:>12}" for c in columns) + " |  Row total   Accuracy"
    print(col_header)
    print("  " + "-" * (len(col_header) - 2))
    for exp in types:
        row = [confusion.get((exp, cls), 0) for cls in columns]
        row_total = sum(row)
        row_acc = (confusion.get((exp, exp), 0) / row_total * 100) if row_total else 0.0
        cells = " | ".join(f"{n:>12d}" for n in row)
        print(
            f"  {exp:<11} | {cells} | {row_total:>9}   {row_acc:>7.1f}%"
        )


def print_latency_summary(conn: duckdb.DuckDBPyConnection) -> None:
    """M5 time-to-first-correct execution per condition (Step 18.9).

    Per proposal §Evaluation Strategy M5: *"Latency from task input to
    verified correct output, including any JIT generation time."* The
    pipeline stops at the first verified-correct output, so for **successful
    runs** ``attempt_latency_ms`` equals time-to-first-correct by
    construction. Failed runs (``gate_passed=False`` OR
    ``output_in_range=False``) have ``attempt_latency_ms`` = wall-clock-
    until-give-up, which violates M5's precondition (there was no first
    correct output) and must be excluded before aggregation.

    Reports mean / median / p95 / stdev over successful runs per condition,
    plus the count of excluded rows with the exclusion rationale.
    """
    rows = conn.execute(
        f"""
        SELECT condition,
               COUNT(*) AS n_included,
               AVG(attempt_latency_ms) / 1000.0 AS avg_sec,
               MEDIAN(attempt_latency_ms) / 1000.0 AS median_sec,
               QUANTILE_CONT(attempt_latency_ms, 0.95) / 1000.0 AS p95_sec,
               STDDEV_SAMP(attempt_latency_ms) / 1000.0 AS stdev_sec
        FROM evaluation_runs
        WHERE gate_passed
          AND output_in_range
          AND NOT COALESCE(output_shape_mismatch, FALSE)
          AND NOT is_pilot{_SCOPE_SQL}
        GROUP BY condition
        ORDER BY condition
        """
    ).fetchall()

    excluded_counts = conn.execute(
        f"""
        SELECT condition, COUNT(*)
        FROM evaluation_runs
        WHERE (NOT gate_passed
               OR NOT output_in_range
               OR COALESCE(output_shape_mismatch, FALSE))
          AND NOT is_pilot{_SCOPE_SQL}
        GROUP BY condition
        ORDER BY condition
        """
    ).fetchall()
    excluded_by_condition = dict(excluded_counts)

    lines = [
        f"\n{'='*80}",
        "  M5 Time-to-first-correct Execution (seconds, successful runs only)",
        f"{'='*80}",
    ]
    header = (
        f"{'Condition':<22} {'n':>5} {'Mean':>8} {'Median':>8} "
        f"{'p95':>8} {'Stdev':>8}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    if not rows:
        lines.append("(no successful runs — nothing to summarize)")
    else:
        for condition, n, avg, med, p95, stdev in rows:
            label = CONDITION_LABELS.get(condition, condition)
            # stdev is NULL when n < 2; render as '-' to avoid NaN-looking output.
            stdev_str = f"{stdev:>8.1f}" if stdev is not None else f"{'-':>8}"
            lines.append(
                f"{label:<22} {n:>5d} {avg:>8.1f} {med:>8.1f} {p95:>8.1f} {stdev_str}"
            )

    total_excluded = sum(excluded_by_condition.values())
    lines.append("")
    lines.append(
        f"Excluded: {total_excluded} non-pilot runs where "
        f"gate_passed=False OR output_in_range=False"
    )
    if total_excluded > 0:
        per_cond = ", ".join(
            f"{CONDITION_LABELS.get(c, c)}={n}"
            for c, n in sorted(excluded_by_condition.items())
        )
        lines.append(f"         by condition: {per_cond}")
    lines.append(
        "         (failed runs have no 'first correct output' — total "
        "wall-clock is not time-to-first-correct for them)"
    )

    for line in lines:
        print(line)


def _clopper_pearson_ci(
    successes: int, n: int, *, confidence: float = 0.95
) -> tuple[float, float]:
    """Return (lower, upper) bounds of a Clopper-Pearson binomial CI.

    Uses scipy.stats.beta.ppf (exact method). Graceful on n=0 → (0, 0).
    Graceful on successes=0 → (0, upper). Graceful on successes=n → (lower, 1).
    """
    if n == 0:
        return (0.0, 0.0)
    from scipy.stats import beta  # local import — heavy dep
    alpha = 1.0 - confidence
    lower = 0.0 if successes == 0 else float(beta.ppf(alpha / 2, successes, n - successes + 1))
    upper = 1.0 if successes == n else float(beta.ppf(1 - alpha / 2, successes + 1, n - successes))
    return (lower, upper)


def print_contamination_rate(conn: duckdb.DuckDBPyConnection) -> None:
    """M2 mock-contamination rate per condition (Step 18.10).

    Reads ``human_label`` from ``evaluation_runs`` for non-pilot rows
    under JIT conditions. Reports per-condition: n_sampled, n_correct,
    n_contaminated, n_partial, contamination_rate, 95% Clopper-Pearson CI.

    Proposal §Evaluation Strategy M2: *"What fraction of generated
    capabilities contain hardcoded/fabricated elements? (Measured by
    manual audit of a sample.)"* Graceful fallback when no labels are
    populated yet: prints a single "audit not performed" line instead
    of empty tables.
    """
    # ext7 #44: M2 must honor --run-ids / --since scope. Pre-fix, cross-campaign
    # pooling silently consumed labels intended for prior campaigns into the
    # current campaign's M2 aggregate.
    rows = conn.execute(
        f"""
        SELECT condition,
               SUM(CASE WHEN human_label = 'correct'      THEN 1 ELSE 0 END) AS n_correct,
               SUM(CASE WHEN human_label = 'contaminated' THEN 1 ELSE 0 END) AS n_contaminated,
               SUM(CASE WHEN human_label = 'partial'      THEN 1 ELSE 0 END) AS n_partial,
               COUNT(*)                                                      AS n_sampled
        FROM evaluation_runs
        WHERE human_label IS NOT NULL
          AND NOT is_pilot{_SCOPE_SQL}
        GROUP BY condition
        ORDER BY condition
        """
    ).fetchall()

    print()
    print("=" * 100)
    print("  M2 Mock-Contamination Rate (manual audit of sampled capabilities)")
    print("=" * 100)

    if not rows:
        print(
            "  Audit not performed — `human_label` is unpopulated. "
            "Run `uv run scripts/audit_contamination.py` after Step 18 to "
            "label a stratified sample, then re-run this analyzer. "
            "Chapter 6 §M2 should report as *deferred* until the audit lands."
        )
        # ext13 #126: thesis-critical metric absent; main() treats this
        # as a missing-metric and exits 7.
        return False

    # Security audit finding #84 (2026-04-19, ext9): M2's
    # proposal-literal definition is *"fraction containing fabricated
    # elements"*. A ``partial`` label means the tool IS partially
    # contaminated (it contains SOME fabricated elements), so per the
    # proposal it MUST appear in the numerator. Pre-fix the rate used
    # ``n_contam / n_sampled`` (strict) and reported ``n_partial``
    # side-by-side without summing — under-reporting M2 by up to ~50
    # percentage points depending on partial-label frequency. We now
    # report BOTH the strict and proposal-literal rates. Chapter 6
    # cites the proposal-literal column as the headline; the strict
    # column is a diagnostic refinement.
    header = (
        f"{'Condition':<22} {'n':>4} {'Correct':>9} {'Contam.':>9} "
        f"{'Partial':>9} {'Strict%':>9} {'Proposal%':>10} "
        f"{'95% CI (proposal)':>22}"
    )
    print(header)
    print("-" * len(header))

    total_c, total_x, total_p, total_n = 0, 0, 0, 0
    for condition, n_correct, n_contam, n_partial, n_sampled in rows:
        label = CONDITION_LABELS.get(condition, condition)
        strict_rate = 100.0 * n_contam / n_sampled if n_sampled else 0.0
        proposal_num = int(n_contam) + int(n_partial)
        proposal_rate = 100.0 * proposal_num / n_sampled if n_sampled else 0.0
        lo, hi = _clopper_pearson_ci(proposal_num, int(n_sampled))
        ci_str = f"[{lo * 100:.1f}%, {hi * 100:.1f}%]"
        print(
            f"{label:<22} {n_sampled:>4d} {n_correct:>9d} {n_contam:>9d} "
            f"{n_partial:>9d} {strict_rate:>8.1f}% {proposal_rate:>9.1f}% "
            f"{ci_str:>22}"
        )
        total_c += int(n_correct)
        total_x += int(n_contam)
        total_p += int(n_partial)
        total_n += int(n_sampled)

    # Pool across conditions only when there are multiple — a single-condition
    # "pooled" row would just be a duplicate of the per-condition row.
    if len(rows) > 1 and total_n > 0:
        strict_rate = 100.0 * total_x / total_n
        proposal_num = total_x + total_p
        proposal_rate = 100.0 * proposal_num / total_n
        lo, hi = _clopper_pearson_ci(proposal_num, total_n)
        print("-" * len(header))
        print(
            f"{'Pooled':<22} {total_n:>4d} {total_c:>9d} {total_x:>9d} "
            f"{total_p:>9d} {strict_rate:>8.1f}% {proposal_rate:>9.1f}% "
            f"[{lo * 100:.1f}%, {hi * 100:.1f}%]"
        )
    print(
        "\n  Strict% = n_contam / n_sampled (fully-contaminated only). "
        "\n  Proposal% = (n_contam + n_partial) / n_sampled — per "
        "proposal §M2 \"fraction containing fabricated elements\"."
    )

    print(
        "\n  Methodology: manual audit of stratified random sample (Step 18.10). "
        "Labels populated via `scripts/audit_contamination.py`. See master plan "
        "§11 for the sampling protocol and labelling criteria."
    )

    # ext13 #132: per-condition coverage check. A DB with labels only
    # in SOME conditions (e.g., only `full`) passes the rows-exist gate
    # above but destroys the JIT-vs-static contamination comparison.
    # Flag missing conditions so main() exits 7 on partial M2 coverage.
    present_conditions = {r[0] for r in rows}
    expected_conditions = set(CONDITIONS)
    missing_conditions = expected_conditions - present_conditions
    if missing_conditions:
        print(
            f"\n  ⚠ M2 partial coverage: labels missing for "
            f"{len(missing_conditions)}/{len(expected_conditions)} conditions: "
            f"{sorted(missing_conditions)}",
            file=sys.stderr,
        )
        return False
    return True


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Statistical analysis of evaluation results")
    parser.add_argument("--db", default=str(ROOT / "data" / "eval" / "metrics.duckdb"))
    parser.add_argument("--csv", action="store_true", help="Export tables to data/analysis/")
    parser.add_argument(
        "--run-ids", nargs="+", default=None,
        help="Specific run_ids to include. Default: auto-select the N most "
             "recent run_ids PER CONDITION (ext7 #46) with stderr WARNING. "
             "Security audit finding #38 (2026-04-20).",
    )
    parser.add_argument(
        "--since", default=None,
        help="Only include rows with timestamp >= SINCE (DB's native "
             "timestamp format). Mutually exclusive with --run-ids.",
    )
    parser.add_argument(
        "--per-condition-cap", type=int, default=_DEFAULT_PER_CONDITION_CAP,
        help=f"When auto-selecting (no --run-ids / --since), how many recent "
             f"run_ids to pick per condition. Default matches run_sheet.md's "
             f"--n-runs={_DEFAULT_PER_CONDITION_CAP}. ext7 #46.",
    )
    # ext7 #52: campaign completeness guard.
    parser.add_argument(
        "--expected-tasks", type=int, default=20,
        help="Expected number of distinct task_ids per condition (default: 20, "
             "matches run_sheet.md T01-T20). ext7 #52.",
    )
    parser.add_argument(
        "--expected-runs-per-condition", type=int, default=5,
        help="Expected number of runs per condition (default: 5, matches "
             "run_sheet.md --n-runs). ext7 #52.",
    )
    parser.add_argument(
        "--expected-conditions", type=int, default=4,
        help="Expected number of distinct conditions (default: 4: full, "
             "no_verification, no_optimization, static). ext7 #52.",
    )
    parser.add_argument(
        "--allow-incomplete", action="store_true",
        help="Proceed even if the scoped dataset doesn't match the expected "
             "campaign shape. Every output table carries an [INCOMPLETE] banner. "
             "ext7 #52.",
    )
    # ext13 #126: thesis-critical metrics (M2 contamination, M3 Gini)
    # used to silently exit 0 when `human_label` / `invocation_events`
    # were empty or had per-condition gaps. Track absences in a list and
    # exit 7 unless the user opts in with --allow-missing-metrics.
    parser.add_argument(
        "--allow-missing-metrics", action="store_true",
        help="Proceed with exit 0 even when thesis-critical metrics "
             "(M2 contamination, M3 Gini) cannot be computed or have "
             "per-condition gaps. Chapter 4.5 disclosure REQUIRED. "
             "Default: refuse with exit 7. ext13 #126 / #128 / #132.",
    )
    args = parser.parse_args()

    conn = load_data(args.db)

    # Security audit finding #38 (2026-04-20): resolve the scope ONCE at
    # startup; every subsequent query reads module-level _SCOPE_SQL.
    global _SCOPE_SQL
    _SCOPE_SQL = _resolve_scope(
        conn, args.run_ids, args.since, args.per_condition_cap,
    )

    # Record counts (scoped).
    total = conn.execute(
        f"SELECT COUNT(*) FROM evaluation_runs WHERE is_pilot = FALSE{_SCOPE_SQL}"
    ).fetchone()[0]
    print(f"\nTotal non-pilot records: {total}")

    # ext7 #52: dataset-completeness guard. Refuse to produce Chapter-6 output
    # on a partial campaign unless --allow-incomplete is passed. Partial
    # datasets with the same visual shape as complete ones silently misrepresent
    # incomplete data as headline numbers.
    expected_conditions = args.expected_conditions
    expected_tasks = args.expected_tasks
    expected_runs = args.expected_runs_per_condition
    expected_total = expected_conditions * expected_tasks * expected_runs
    expected_per_condition = expected_tasks * expected_runs

    per_condition_counts = conn.execute(
        f"SELECT condition, COUNT(*) FROM evaluation_runs "
        f"WHERE is_pilot = FALSE{_SCOPE_SQL} GROUP BY condition"
    ).fetchall()
    condition_map = {row[0]: row[1] for row in per_condition_counts}

    issues: list[str] = []
    if total != expected_total:
        issues.append(f"total non-pilot rows={total}, expected {expected_total}")
    for condition, count in condition_map.items():
        if count != expected_per_condition:
            issues.append(
                f"condition '{condition}': {count} rows, expected {expected_per_condition}"
            )
    if len(condition_map) < expected_conditions:
        missing = (
            {"full", "no_verification", "no_optimization", "static"}
            - set(condition_map)
        )
        for c in sorted(missing):
            issues.append(f"condition '{c}' missing entirely (0 rows)")

    # ext13 #131: extend ext7 #52's row-count gate with distinct-task
    # coverage + per-(condition, task_id) uniformity checks. A 400-row
    # DB of T01 repeated 100x per condition passes the row-count gate
    # and yields Chapter 6 headlines computed on 1/20 of the testbed.
    per_cond_task_coverage = conn.execute(
        f"SELECT condition, COUNT(DISTINCT task_id) "
        f"FROM evaluation_runs WHERE is_pilot = FALSE{_SCOPE_SQL} "
        f"GROUP BY condition"
    ).fetchall()
    for condition, distinct_tasks in per_cond_task_coverage:
        if distinct_tasks != expected_tasks:
            issues.append(
                f"condition '{condition}': {distinct_tasks} distinct tasks, "
                f"expected {expected_tasks}"
            )
    uneven = conn.execute(
        f"SELECT condition, task_id, COUNT(*) AS n "
        f"FROM evaluation_runs WHERE is_pilot = FALSE{_SCOPE_SQL} "
        f"GROUP BY condition, task_id HAVING COUNT(*) != ?",
        [expected_runs],
    ).fetchall()
    for condition, task_id, n in uneven:
        issues.append(
            f"condition '{condition}', task '{task_id}': {n} rows, "
            f"expected {expected_runs}"
        )

    if issues and not args.allow_incomplete:
        print(
            "\nERROR: dataset appears incomplete — refusing to produce Chapter-6 output.\n"
            "Issues:\n  - " + "\n  - ".join(issues) + "\n"
            "Pass --allow-incomplete to override (output will be marked "
            "INCOMPLETE in every table header). ext7 #52 + ext13 #131.",
            file=sys.stderr,
        )
        return 2

    # Stash the banner on the module so table printers can render it when
    # --allow-incomplete forces a partial-data run. Empty string = complete.
    global _COMPLETENESS_BANNER
    _COMPLETENESS_BANNER = (
        f" [INCOMPLETE: {'; '.join(issues)}]"
        if args.allow_incomplete and issues
        else ""
    )
    if _COMPLETENESS_BANNER:
        print(f"\nWARNING: proceeding with partial data:{_COMPLETENESS_BANNER}", file=sys.stderr)

    # ── Table 1: All tasks ───────────────────────────────────────────────
    all_data = query_condition_summary(conn)
    csv_all = print_condition_table("Table 1: Success Rates — All Tasks (T01-T18)", all_data)

    # ── Table 2: Synthesis tasks only (T04-T13) ──────────────────────────
    task_filter = " OR ".join(f"task_id = '{t}'" for t in SYNTHESIS_TASK_IDS)
    synth_data = query_condition_summary(conn, task_filter)
    csv_synth = print_condition_table(
        "Table 2: Success Rates — Synthesis Tasks Only (T04-T18)", synth_data
    )

    # ── Table 3: Retrieval tasks only (T01-T03) ─────────────────────────
    retr_filter = " OR ".join(f"task_id = '{t}'" for t in RETRIEVAL_TASKS)
    retr_data = query_condition_summary(conn, retr_filter)
    print_condition_table("Table 3: Success Rates — Retrieval Tasks Only (T01-T03)", retr_data)

    # ── Table 4: Per-task detail ─────────────────────────────────────────
    per_task = query_per_task(conn)
    csv_per_task = print_per_task_table(per_task)

    # ── Table 5: Gate pass vs output validation (separate) ───────────────
    print_gate_vs_output_table(per_task)

    # ── JIT cluster analysis ─────────────────────────────────────────────
    print_cluster_analysis(all_data, synth_data)

    # ── Failure breakdown ────────────────────────────────────────────────
    # Security audit finding #85 (2026-04-19, ext9): pass condition
    # totals so the table prints rates alongside counts. ``all_data``
    # carries the per-condition ``n`` from ``query_condition_summary``.
    failures = query_failure_breakdown(conn)
    condition_totals = {d["condition"]: d["n"] for d in all_data}
    print_failure_breakdown(failures, condition_totals)

    # ── Token costs ──────────────────────────────────────────────────────
    print_token_cost_summary(conn)

    # ── Latency summary ──────────────────────────────────────────────────
    print_latency_summary(conn)

    # ── Core-work classification accuracy (Step 18.5) ────────────────────
    # Task-level primary-type match as operational proxy for Pillar 1 RQ #1.
    # See print_classification_accuracy docstring for framing rationale.
    print_classification_accuracy(conn)

    # ext13 #126 / #128 / #132: accumulate thesis-critical metric
    # absences so we can exit 7 at the bottom rather than silently
    # exiting 0 with the section skipped.
    missing_metrics: list[str] = []

    # ── Gini coefficient of capability invocation counts (§Metrics) ──────
    if not print_gini_summary(conn):
        missing_metrics.append("M3 (Gini / Pillar 3 RQ #1)")

    # ── M2 mock contamination rate (Step 18.10, proposal §M2) ────────────
    # Graceful fallback prints "audit not performed" when human_label
    # column is unpopulated.
    if not print_contamination_rate(conn):
        missing_metrics.append("M2 (Contamination)")

    conn.close()

    # ── CSV export ───────────────────────────────────────────────────────
    if args.csv:
        out_dir = ROOT / "data" / "analysis"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "condition_all_tasks.csv").write_text("\n".join(csv_all) + "\n")
        (out_dir / "condition_synthesis_only.csv").write_text("\n".join(csv_synth) + "\n")
        (out_dir / "per_task_detail.csv").write_text("\n".join(csv_per_task) + "\n")
        print(f"\nCSV files written to {out_dir}/")

    # ext13 #126 / #128 / #132: exit 7 when any thesis-critical metric
    # was absent or partial. Override via --allow-missing-metrics.
    if missing_metrics:
        msg = (
            "\n" + "!" * 80 + "\n"
            "  CHAPTER 6 INCOMPLETE — the following metrics could not be "
            "computed or had per-condition gaps:\n"
            + "\n".join(f"    - {m}" for m in missing_metrics)
            + "\n" + "!" * 80
        )
        print(msg, file=sys.stderr)
        if not args.allow_missing_metrics:
            return 7
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
