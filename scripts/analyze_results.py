"""Statistical analysis of evaluation results for thesis Chapter 6.

Produces:
- Disaggregated success rates (all tasks vs synthesis-only T04-T20)
  (labels derived from ALL_TASKS; T01-T03 are retrieval tasks)
- Exact binomial 95% confidence intervals
- gate_passed and output_in_range as separate columns
- Fisher's exact test (each JIT condition vs static, per task)
- Per-task success details
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

import duckdb
from scipy import stats

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── Constants ────────────────────────────────────────────────────────────────

CONDITIONS = ["full", "no_verification", "no_optimization", "static"]
RETRIEVAL_TASKS = {"T01", "T02", "T03"}

# Import the task list from the testbed source of truth so summary tables
# cannot drift when tasks are added or renamed.
from tests.evaluation.tasks import ALL_TASKS  # noqa: E402

ALL_TASK_IDS = [t.task_id for t in ALL_TASKS]
SYNTHESIS_TASK_IDS = [t for t in ALL_TASK_IDS if t not in RETRIEVAL_TASKS]
CONDITION_LABELS = {
    "static": "Static",
    "no_verification": "JIT + Opt",
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


# Module-level scope filter resolved once at ``main()`` startup. Every SQL
# query appends this fragment so analyzers operate on one run-set.
_SCOPE_SQL: str = ""

# Banner used when --allow-incomplete lets a partial campaign proceed.
_COMPLETENESS_BANNER: str = ""


# Canonical eval shape is --n-runs 3 per condition across 4 conditions, so
# auto-select needs a per-condition cap rather than a global "latest N".
_DEFAULT_PER_CONDITION_CAP = 3


def _scope_for_table(table: str, scope_sql: str | None = None) -> str:
    """Return the scope fragment appropriate for ``table``.

    Most queries read ``evaluation_runs`` and use ``_SCOPE_SQL`` verbatim —
    its ``timestamp`` column name matches the --since scope fragment's
    hardcoded column. Two tables (``invocation_events``, ``cycle_events``)
    use ``occurred_at`` as their time column instead; applying the raw
    ``_SCOPE_SQL`` to queries against those tables crashes DuckDB with
    ``Binder Error: Referenced column "timestamp" not found in FROM clause``.

    For ``--since`` scopes this rewrites the column name to ``occurred_at``.
    ``--run-ids`` and auto-select scopes use ``run_id``, which all three
    tables share, so they pass through unchanged.
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

    Multi-run DBs can contain reruns after code changes or different configs,
    so default reporting must scope to one comparable run-set:

    - Explicit ``run_ids`` → filter on exactly those.
    - ``since`` → filter on timestamp.
    - Neither: auto-select the ``per_condition_cap`` most recent run_ids
      per condition. This preserves the canonical eval-protocol shape of
      ``--n-runs 3 × 4 conditions = 12 distinct run_ids``.

    Returns "" when no scoping is applied (legacy mode — unsafe on
    multi-run DBs).
    """
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
    # canonical eval shape (3 per condition × 4 conditions = 12 run_ids) while
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

    # Rank distinct run_ids per condition, not task rows. A run has many task
    # rows, so ranking before this collapse would over-sample the newest run.
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

    # Loud-fail if auto-select dropped any condition; downstream comparisons
    # require every condition in scope.
    if len(selected_conditions) < condition_count:
        print(
            f"ERROR: auto-select covered {len(selected_conditions)}/{condition_count} "
            f"conditions. Pass --run-ids explicitly to include all conditions, "
            f"or --per-condition-cap to adjust.",
            file=sys.stderr,
        )
        sys.exit(2)

    if total_available > len(latest):
        print(
            f"WARNING: DB contains {total_available} distinct non-pilot run_ids; "
            f"auto-selected {len(latest)} total across {len(selected_conditions)} "
            f"conditions (target cap: {per_condition_cap} per condition). "
            f"Pass --run-ids to override.",
            file=sys.stderr,
        )

    safe_latest = [r.replace("'", "''") for r in latest]
    return " AND run_id IN ({})".format(
        ", ".join(f"'{r}'" for r in safe_latest)
    )


def query_condition_summary(
    conn: duckdb.DuckDBPyConnection, task_filter: str = "TRUE"
) -> list[dict]:
    """Per-condition summary: success, gate_pass, output_in_range counts.

    Every correctness column applies the same shape-mismatch tightening.
    ``shape_mismatch`` is reported separately so the gap is visible directly.
    """
    rows = conn.execute(
        f"""
        SELECT
            condition,
            COUNT(*) AS n,
            SUM(CASE
                WHEN gate_passed
                 AND output_in_range
                 AND NOT COALESCE(output_shape_mismatch, FALSE)
                THEN 1 ELSE 0
            END) AS successes,
            SUM(CASE WHEN gate_passed THEN 1 ELSE 0 END) AS gate_passed,
            SUM(CASE
                WHEN output_in_range
                 AND NOT COALESCE(output_shape_mismatch, FALSE)
                THEN 1 ELSE 0
            END) AS output_in_range,
            SUM(CASE
                WHEN COALESCE(output_shape_mismatch, FALSE)
                THEN 1 ELSE 0
            END) AS shape_mismatch
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

    ``output_in_range`` applies the same shape-mismatch tightening as
    ``successes`` so per-task rows stay internally coherent.
    """
    rows = conn.execute(
        f"""
        SELECT
            task_id, condition,
            COUNT(*) AS n,
            SUM(CASE
                WHEN gate_passed
                 AND output_in_range
                 AND NOT COALESCE(output_shape_mismatch, FALSE)
                THEN 1 ELSE 0
            END) AS successes,
            SUM(CASE WHEN gate_passed THEN 1 ELSE 0 END) AS gate_passed,
            SUM(CASE
                WHEN output_in_range
                 AND NOT COALESCE(output_shape_mismatch, FALSE)
                THEN 1 ELSE 0
            END) AS output_in_range
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

    Includes a Shape Mismatch column so readers can distinguish
    shape-checked output from raw gate status.
    """
    # Append the [INCOMPLETE: ...] banner when --allow-incomplete was used.
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
    """Show gate_passed and output_in_range as separate columns."""
    lines = [
        f"\n{'='*80}",
        "  Gate Pass vs Output Validation (Separated)",
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

    Includes denominator (N) and rate columns so identical failure counts
    across conditions with different N are not read as equivalent outcomes.
    ``condition_totals`` is optional for back-compat; absent totals fall
    back to count-only rendering with an explicit banner.
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
    """Frame JIT ablations as a cluster rather than a strict ranking."""
    lines = [
        f"\n{'='*80}",
        "  JIT Cluster Analysis (cluster, not ranking)",
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

    # Derive the displayed task range from ALL_TASKS.
    _synth_range = (
        f"{SYNTHESIS_TASK_IDS[0]}-{SYNTHESIS_TASK_IDS[-1]}"
        if SYNTHESIS_TASK_IDS else "none"
    )
    lines.append(f"  Synthesis tasks only ({_synth_range}):")
    lines.append(f"    JIT cluster:  {jit_min:.1f}% – {jit_max:.1f}%")
    lines.append(f"    Static:       {static_rate:.1f}%")
    lines.append(
        f"    Gap (cluster): {jit_min - static_rate:.1f} – "
        f"{jit_max - static_rate:.1f} pp"
    )

    # Report the full-system gap separately from the JIT cluster spread so
    # the main intervention is visible alongside ablations.
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

    # The pooled-JIT Fisher test is diagnostic only. Pooling full,
    # no_verification, and no_optimization treats distinct interventions as
    # one population and inflates N, so the per-condition Fisher tests above
    # remain the citable result.
    jit_total_k = sum(d["successes"] for d in jit_conds)
    jit_total_n = sum(d["n"] for d in jit_conds)
    _, p_pooled = fisher_exact_2x2(
        jit_total_k, jit_total_n, static["successes"], static["n"]
    )
    lines.append("")
    lines.append("  ===== DIAGNOSTIC ONLY — DO NOT CITE =====")
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


def print_gini_summary(conn: duckdb.DuckDBPyConnection) -> bool:
    """Gini coefficient of capability invocation counts per condition.

    Addresses proposal §Metrics item 3: *"Capability reuse distribution —
    Gini coefficient of invocation counts across the registry. Healthy
    registries have moderate inequality; unhealthy ones have extreme skew."*

    Reads from the persistent ``invocation_events`` table. Groups invocations
    by (condition, run_id) and computes one Gini per run; reports mean +
    spread across the N runs per condition. Also reports a condition-level
    aggregate Gini (all invocations pooled).

    Degenerate cases (empty counts, all zeros) → Gini 0.0. Conditions
    with no invocation data are skipped (happens if an old eval DB
    predates invocation telemetry). Returns False when Gini evidence is
    absent or incomplete.
    """
    # Old eval DBs may not have invocation telemetry.
    try:
        conn.execute("SELECT 1 FROM invocation_events LIMIT 1")
    except Exception as exc:  # noqa: BLE001
        print(f"\n{'='*80}")
        print("  Gini Coefficient of Capability Invocation Counts")
        print(f"{'='*80}")
        print(
            f"  (skipped: invocation_events table not available — {exc.__class__.__name__}. "
            "Re-run the eval with invocation telemetry enabled to populate this.)"
        )
        return False

    # Per-condition, per-run Gini
    # Scope before grouping, exclude pilot rows, and remove capabilities
    # pruned within the same run. The prune subquery is correlated by
    # (capability_id, run_id) so a prune in one run cannot erase another
    # run's invocation history.
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
        # Return False so main() can report M3 as a missing active metric.
        return False

    # Group by (condition, run_id) → counts keyed by capability, so we
    # can zero-pad against the per-condition active-registry proxy
    # below. Also collect the per-condition union of capability ids.
    #
    # The aggregate column pools by capability across runs. That measures the
    # condition-level capability distribution rather than the raw
    # per-(run, capability) list.
    from collections import defaultdict
    by_run: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    by_condition_capsum: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    condition_caps: dict[str, set[str]] = defaultdict(set)
    for cond, run_id, cap_id, inv_count in per_run_rows:
        by_run[(cond, run_id)][cap_id] = int(inv_count)
        by_condition_capsum[cond][cap_id] += int(inv_count)
        condition_caps[cond].add(cap_id)

    # Zero-pad each run with the per-condition capability union so unused
    # capabilities count as zeros. Otherwise a run that used one capability
    # repeatedly would look perfectly equal because the vector has length one.
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
            print(
                f"  {CONDITION_LABELS.get(cond, cond):<22} "
                f"{'—':>8} {'—':>10} {'—':>7} {'—':>7} {'—':>10}"
            )
            continue
        mean = sum(run_ginis) / len(run_ginis)
        # Aggregate over capability totals, not raw per-(run, cap) counts.
        aggregate = _gini_coefficient(list(by_condition_capsum[cond].values()))
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

    # Per-condition coverage check: M3 comparisons need invocation evidence
    # for every condition, not just at least one condition.
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

    # Cross-check per-run coverage too. A condition can have some invocation
    # rows while still missing telemetry for one or more run_ids.
    expected_per_condition = dict(conn.execute(
        f"""
        SELECT condition, COUNT(DISTINCT run_id)
        FROM evaluation_runs
        WHERE NOT is_pilot{_SCOPE_SQL}
        GROUP BY condition
        """
    ).fetchall())
    per_run_gaps: list[tuple[str, int, int]] = []
    for cond in CONDITIONS:
        expected_n = expected_per_condition.get(cond, 0)
        present_n = len(condition_ginis.get(cond, []))
        if expected_n > 0 and present_n < expected_n:
            per_run_gaps.append((cond, present_n, expected_n))
    if per_run_gaps:
        gap_str = "; ".join(
            f"{c}: {p}/{e} run_ids have invocations"
            for c, p, e in per_run_gaps
        )
        print(
            f"\n  ⚠ M3 partial per-run coverage: {gap_str}",
            file=sys.stderr,
        )
        return False
    return True


def print_classification_accuracy(conn: duckdb.DuckDBPyConnection) -> bool:
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
       baseline conditions × 3 runs × 20 tasks = 240 records; the
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

    Returns ``True`` when the metric is available and ``False`` when it
    should be reported as missing by ``main()``.
    """
    expected_map: dict[str, str | None] = {t.task_id: t.expected_type for t in ALL_TASKS}
    scorable = {tid for tid, et in expected_map.items() if et is not None}
    if not scorable:
        print(f"\n{'='*80}")
        print("  Core-Work Classification Accuracy — task-level primary-type match")
        print("  (operational proxy for Pillar 1 RQ #1; see docstring for framing)")
        print(f"{'='*80}")
        print("  (skipped: no tasks have expected_type assigned)")
        return False

    # Use an explicit JIT-condition whitelist and require generated artifacts.
    # Pure registry-match rows bypass the classifier and are excluded from the
    # denominator.
    total_rows_before_filter = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM evaluation_runs
        WHERE is_pilot = FALSE
          AND condition IN ('full', 'no_verification', 'no_optimization')
          {_SCOPE_SQL}
        """
    ).fetchone()[0] or 0
    rows = conn.execute(
        f"""
        SELECT task_id, condition, capability_type
        FROM evaluation_runs
        WHERE is_pilot = FALSE
          AND condition IN ('full', 'no_verification', 'no_optimization')
          AND COALESCE(generated_artifact_count, 0) > 0
          {_SCOPE_SQL}
        """
    ).fetchall()
    excluded_registry_only = total_rows_before_filter - len(rows)

    if not rows:
        print(f"\n{'='*80}")
        print("  Core-Work Classification Accuracy — task-level primary-type match")
        print("  (operational proxy for Pillar 1 RQ #1; see docstring for framing)")
        print(f"{'='*80}")
        print("  (skipped: no non-pilot JIT-condition rows with generated_artifact_count > 0)")
        return False

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

    print(
        "\n  Confusion matrix "
        "(rows = expected, columns = classified, aggregated over JIT conditions):"
    )
    col_header = (
        "  " + " " * 11 + " | "
        + " | ".join(f"{c:>12}" for c in columns)
        + " |  Row total   Accuracy"
    )
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

    # Disclosure footer for excluded registry-match rows.
    if excluded_registry_only > 0:
        print(
            f"\n  Note: {excluded_registry_only} row(s) excluded from the "
            f"classification-accuracy denominator — pure registry-match rows "
            f"where the classifier didn't run. Disclose that the "
            f"Pillar 1 RQ #1 proxy is scored over classifier-invoked rows only."
        )

    return True


def print_latency_summary(conn: duckdb.DuckDBPyConnection) -> bool:
    """M5 time-to-first-correct execution per condition.

    Per proposal §Evaluation Strategy M5: *"Latency from task input to
    verified correct output, including any JIT generation time."* The
    pipeline stops at the first verified-correct output, so for **successful
    runs** ``attempt_latency_ms`` equals time-to-first-correct by
    construction. Failed runs (``gate_passed=False`` OR
    ``output_in_range=False``) have ``attempt_latency_ms`` = wall-clock-
    until-give-up, which violates M5's precondition (there was no first
    correct output) and must be excluded before aggregation.

    Reports mean / median / p95 / stdev over successful runs per condition,
    and returns ``False`` if the scoped data cannot support the metric.
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

    # M5 is absent if there are no successful runs, or if any JIT condition
    # has no successful run coverage.
    if not rows:
        return False
    jit_conditions = {"full", "no_verification", "no_optimization"}
    present_jit = {r[0] for r in rows} & jit_conditions
    if jit_conditions - present_jit:
        # At least one JIT condition absent from successful-run set.
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
             "recent run_ids PER CONDITION with stderr WARNING.",
    )
    parser.add_argument(
        "--since", default=None,
        help="Only include rows with timestamp >= SINCE (DB's native "
             "timestamp format). Mutually exclusive with --run-ids.",
    )
    parser.add_argument(
        "--per-condition-cap", type=int, default=_DEFAULT_PER_CONDITION_CAP,
        help=f"When auto-selecting (no --run-ids / --since), how many recent "
             f"run_ids to pick per condition. Default matches Stage 1's "
             f"--n-runs={_DEFAULT_PER_CONDITION_CAP}.",
    )
    # Campaign completeness guard.
    parser.add_argument(
        "--expected-tasks", type=int, default=20,
        help="Expected number of distinct task_ids per condition (default: 20, "
             "matches the canonical T01-T20 evaluation set).",
    )
    parser.add_argument(
        "--expected-runs-per-condition", type=int, default=3,
        help="Expected number of runs per condition (default: 3, matches "
             "EVAL_PROTOCOL.md Stage 1 and stage1_main_eval.sh --n-runs).",
    )
    parser.add_argument(
        "--expected-conditions", type=int, default=4,
        help="Expected number of distinct conditions (default: 4: full, "
             "no_verification, no_optimization, static).",
    )
    parser.add_argument(
        "--allow-incomplete", action="store_true",
        help="Proceed even if the scoped dataset doesn't match the expected "
             "campaign shape. Every output table carries an [INCOMPLETE] banner.",
    )
    # Track active Chapter 6 metric absences and exit 7 unless the user
    # explicitly opts in with --allow-missing-metrics.
    parser.add_argument(
        "--allow-missing-metrics", action="store_true",
        help="Proceed with exit 0 even when active Chapter 6 metrics "
             "(M3 Gini, M5 latency, classification accuracy) cannot be "
             "computed or have per-condition gaps. Default: refuse with "
             "exit 7.",
    )
    parser.add_argument(
        "--chapter6-primary-scope", action="store_true",
        help="Protocol mode for EVAL_PROTOCOL.md Chapter 6 primary scope. "
             "Suppresses verbose per-task Fisher detail while keeping the "
             "headline and Stage 2 diagnostic sections.",
    )
    args = parser.parse_args()

    conn = load_data(args.db)

    # Resolve the scope once; every subsequent query reads module-level
    # _SCOPE_SQL.
    global _SCOPE_SQL
    _SCOPE_SQL = _resolve_scope(
        conn, args.run_ids, args.since, args.per_condition_cap,
    )

    # Record counts (scoped).
    total = conn.execute(
        f"SELECT COUNT(*) FROM evaluation_runs WHERE is_pilot = FALSE{_SCOPE_SQL}"
    ).fetchone()[0]
    print(f"\nTotal non-pilot records: {total}")

    # Refuse partial campaign data unless --allow-incomplete is passed.
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

    # Row counts are not enough: verify distinct-task coverage and
    # per-(condition, task_id) uniformity so repeated-task datasets cannot
    # masquerade as full campaigns.
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

    # Task identity must match the canonical evaluation set.
    canonical_task_ids = set(ALL_TASK_IDS)
    per_cond_observed_tasks = conn.execute(
        f"SELECT condition, task_id FROM evaluation_runs "
        f"WHERE is_pilot = FALSE{_SCOPE_SQL} "
        f"GROUP BY condition, task_id"
    ).fetchall()
    observed_by_cond: dict[str, set[str]] = {}
    for cond, tid in per_cond_observed_tasks:
        observed_by_cond.setdefault(cond, set()).add(tid)
    for cond, observed in observed_by_cond.items():
        extra = observed - canonical_task_ids
        missing = canonical_task_ids - observed
        if extra:
            issues.append(
                f"condition '{cond}': non-canonical task_ids "
                f"{sorted(extra)}"
            )
        if missing:
            issues.append(
                f"condition '{cond}': missing canonical task_ids "
                f"{sorted(missing)}"
            )

    # Condition identity must match the canonical evaluation set.
    canonical_conditions = set(CONDITIONS)
    observed_conditions = set(condition_map)
    extra_conditions = observed_conditions - canonical_conditions
    if extra_conditions:
        issues.append(
            f"extra non-canonical conditions: {sorted(extra_conditions)}"
        )

    # Distinct run_id cardinality protects Fisher/Holm/CI assumptions against
    # pseudo-replicated data.
    per_cond_distinct_runs = conn.execute(
        f"SELECT condition, COUNT(DISTINCT run_id) "
        f"FROM evaluation_runs WHERE is_pilot = FALSE{_SCOPE_SQL} "
        f"GROUP BY condition"
    ).fetchall()
    for cond, n_runs in per_cond_distinct_runs:
        if n_runs != expected_runs:
            issues.append(
                f"condition '{cond}': {n_runs} distinct run_ids, "
                f"expected {expected_runs} — pseudo-replication risk"
            )

    if issues and not args.allow_incomplete:
        print(
            "\nERROR: dataset appears incomplete — refusing to produce Chapter-6 output.\n"
            "Issues:\n  - " + "\n  - ".join(issues) + "\n"
            "Pass --allow-incomplete to override (output will be marked "
            "INCOMPLETE in every table header).",
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
    # Derive ranges from ALL_TASKS.
    _all_range = (
        f"{ALL_TASK_IDS[0]}-{ALL_TASK_IDS[-1]}" if ALL_TASK_IDS else "none"
    )
    _synth_range = (
        f"{SYNTHESIS_TASK_IDS[0]}-{SYNTHESIS_TASK_IDS[-1]}"
        if SYNTHESIS_TASK_IDS else "none"
    )
    all_data = query_condition_summary(conn)
    csv_all = print_condition_table(
        f"Table 1: Success Rates — All Tasks ({_all_range})", all_data
    )

    # ── Table 2: Synthesis tasks only ────────────────────────────────────
    task_filter = " OR ".join(f"task_id = '{t}'" for t in SYNTHESIS_TASK_IDS)
    synth_data = query_condition_summary(conn, task_filter)
    csv_synth = print_condition_table(
        f"Table 2: Success Rates — Synthesis Tasks Only ({_synth_range})", synth_data
    )

    # ── Table 3: Retrieval tasks only (T01-T03) ─────────────────────────
    retr_filter = " OR ".join(f"task_id = '{t}'" for t in RETRIEVAL_TASKS)
    retr_data = query_condition_summary(conn, retr_filter)
    print_condition_table("Table 3: Success Rates — Retrieval Tasks Only (T01-T03)", retr_data)

    # ── Table 4: Per-task detail ─────────────────────────────────────────
    per_task = query_per_task(conn)
    csv_per_task: list[str] = []
    if not args.chapter6_primary_scope:
        csv_per_task = print_per_task_table(per_task)

    # ── Table 5: Gate pass vs output validation (separate) ───────────────
    print_gate_vs_output_table(per_task)

    # ── JIT cluster analysis ─────────────────────────────────────────────
    print_cluster_analysis(all_data, synth_data)

    # ── Failure breakdown ────────────────────────────────────────────────
    # Pass condition totals so the table prints rates alongside counts.
    failures = query_failure_breakdown(conn)
    condition_totals = {d["condition"]: d["n"] for d in all_data}
    print_failure_breakdown(failures, condition_totals)

    # ── Latency summary ──────────────────────────────────────────────────
    m5_ok = print_latency_summary(conn)

    # ── Core-work classification accuracy ────────────────────────────────
    # Task-level primary-type match as operational proxy for Pillar 1 RQ #1.
    # See print_classification_accuracy docstring for framing rationale.
    classification_ok = print_classification_accuracy(conn)

    # Accumulate active metric absences so we can exit 7 at the bottom
    # rather than silently exiting 0 with a missing section.
    missing_metrics: list[str] = []

    # ── Gini coefficient of capability invocation counts (§Metrics) ──────
    if not print_gini_summary(conn):
        missing_metrics.append("M3 (Gini / Pillar 3 RQ #1)")

    if not m5_ok:
        missing_metrics.append("M5 (Latency / Chapter 6 time-to-correct)")

    if not classification_ok:
        missing_metrics.append("Classification Accuracy / Pillar 1 RQ #1")

    conn.close()

    # ── CSV export ───────────────────────────────────────────────────────
    if args.csv:
        out_dir = ROOT / "data" / "analysis"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "condition_all_tasks.csv").write_text("\n".join(csv_all) + "\n")
        (out_dir / "condition_synthesis_only.csv").write_text("\n".join(csv_synth) + "\n")
        if csv_per_task:
            (out_dir / "per_task_detail.csv").write_text("\n".join(csv_per_task) + "\n")
        print(f"\nCSV files written to {out_dir}/")

    # Exit 7 when any active metric was absent or partial. Override via
    # --allow-missing-metrics.
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
