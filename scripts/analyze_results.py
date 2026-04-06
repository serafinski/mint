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
    uv run scripts/analyze_results.py --db data/evaluation.duckdb
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
ALL_TASK_IDS = [f"T{i:02d}" for i in range(1, 19)]
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


def query_condition_summary(
    conn: duckdb.DuckDBPyConnection, task_filter: str = "TRUE"
) -> list[dict]:
    """Per-condition summary: success, gate_pass, output_in_range counts."""
    rows = conn.execute(
        f"""
        SELECT
            condition,
            COUNT(*) AS n,
            SUM(CASE WHEN gate_passed AND output_in_range THEN 1 ELSE 0 END) AS successes,
            SUM(CASE WHEN gate_passed THEN 1 ELSE 0 END) AS gate_passed,
            SUM(CASE WHEN output_in_range THEN 1 ELSE 0 END) AS output_in_range
        FROM evaluation_runs
        WHERE is_pilot = FALSE AND ({task_filter})
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
        }
        for r in rows
    ]


def query_per_task(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    """Per-task, per-condition success counts."""
    rows = conn.execute(
        """
        SELECT
            task_id, condition,
            COUNT(*) AS n,
            SUM(CASE WHEN gate_passed AND output_in_range THEN 1 ELSE 0 END) AS successes,
            SUM(CASE WHEN gate_passed THEN 1 ELSE 0 END) AS gate_passed,
            SUM(CASE WHEN output_in_range THEN 1 ELSE 0 END) AS output_in_range
        FROM evaluation_runs
        WHERE is_pilot = FALSE
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
        """
        SELECT condition, failure_reason, COUNT(*) AS n
        FROM evaluation_runs
        WHERE is_pilot = FALSE AND failure_reason != 'none'
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
    """Print condition summary table with CIs. Returns CSV lines."""
    lines = [f"\n{'='*80}", f"  {title}", f"{'='*80}"]
    header = f"{'Condition':<22} {'Success':>14} {'95% CI':>18} {'Gate Pass':>14} {'Output OK':>14}"
    lines.append(header)
    lines.append("-" * len(header))

    csv_lines = ["condition,n,successes,success_rate,ci_lo,ci_hi,gate_passed,output_in_range"]
    for d in data:
        label = CONDITION_LABELS.get(d["condition"], d["condition"])
        lo, hi = binom_ci(d["successes"], d["n"])
        lines.append(
            f"{label:<22} {fmt_pct(d['successes'], d['n']):>14} {fmt_ci(lo, hi):>18} "
            f"{fmt_pct(d['gate_passed'], d['n']):>14} {fmt_pct(d['output_in_range'], d['n']):>14}"
        )
        csv_lines.append(
            f"{d['condition']},{d['n']},{d['successes']},"
            f"{d['successes']/d['n']:.4f},{lo:.4f},{hi:.4f},"
            f"{d['gate_passed']},{d['output_in_range']}"
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


def print_failure_breakdown(failures: list[dict]) -> None:
    lines = [
        f"\n{'='*80}",
        "  Failure Breakdown by Condition",
        f"{'='*80}",
    ]
    header = f"{'Condition':<22} {'Reason':<22} {'Count':>6}"
    lines.append(header)
    lines.append("-" * len(header))
    for d in failures:
        label = CONDITION_LABELS.get(d["condition"], d["condition"])
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

    lines.append(f"  Synthesis tasks only (T04-T13):")
    lines.append(f"    JIT cluster:  {jit_min:.1f}% – {jit_max:.1f}%")
    lines.append(f"    Static:       {static_rate:.1f}%")
    lines.append(f"    Gap:          {jit_min - static_rate:.1f} – {jit_max - static_rate:.1f} pp")
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

    # Overall JIT vs static (pooled) — single test, no correction needed
    jit_total_k = sum(d["successes"] for d in jit_conds)
    jit_total_n = sum(d["n"] for d in jit_conds)
    _, p_pooled = fisher_exact_2x2(
        jit_total_k, jit_total_n, static["successes"], static["n"]
    )
    lines.append(
        f"  Pooled JIT vs Static (NOTE: pooling inflates significance): "
        f"{jit_total_k}/{jit_total_n} "
        f"({jit_total_k/jit_total_n*100:.1f}%) vs "
        f"{static['successes']}/{static['n']} ({static_rate:.1f}%), "
        f"p={fmt_p(p_pooled)}"
    )

    for line in lines:
        print(line)


def print_token_cost_summary(conn: duckdb.DuckDBPyConnection) -> None:
    """Token cost per condition (from backfilled LangFuse data)."""
    rows = conn.execute(
        """
        SELECT condition,
               SUM(token_cost) AS total_tokens,
               AVG(CASE WHEN token_cost > 0 THEN token_cost ELSE NULL END) AS avg_per_jit_task,
               COUNT(CASE WHEN token_cost > 0 THEN 1 END) AS n_with_cost,
               COUNT(*) AS n_total
        FROM evaluation_runs
        WHERE is_pilot = FALSE
        GROUP BY condition
        ORDER BY condition
        """
    ).fetchall()
    lines = [
        f"\n{'='*80}",
        "  Token Cost Summary (backfilled from LangFuse)",
        f"{'='*80}",
    ]
    header = f"{'Condition':<22} {'Total Tokens':>14} {'Avg/JIT Task':>14} {'With Cost':>10}"
    lines.append(header)
    lines.append("-" * len(header))
    for r in rows:
        label = CONDITION_LABELS.get(r[0], r[0])
        avg = f"{r[2]:,.0f}" if r[2] else "—"
        lines.append(
            f"{label:<22} {r[1]:>14,} {avg:>14} {r[3]:>5}/{r[4]}"
        )
    for line in lines:
        print(line)


def print_latency_summary(conn: duckdb.DuckDBPyConnection) -> None:
    """Average latency per condition for JIT runs (excludes static)."""
    rows = conn.execute(
        """
        SELECT condition,
               AVG(attempt_latency_ms) / 1000.0 AS avg_sec,
               MEDIAN(attempt_latency_ms) / 1000.0 AS median_sec,
               MIN(attempt_latency_ms) / 1000.0 AS min_sec,
               MAX(attempt_latency_ms) / 1000.0 AS max_sec
        FROM evaluation_runs
        WHERE is_pilot = FALSE AND condition != 'static'
        GROUP BY condition
        ORDER BY condition
        """
    ).fetchall()
    lines = [
        f"\n{'='*80}",
        "  Latency Summary (seconds, JIT conditions only)",
        f"{'='*80}",
    ]
    header = f"{'Condition':<22} {'Mean':>8} {'Median':>8} {'Min':>8} {'Max':>8}"
    lines.append(header)
    lines.append("-" * len(header))
    for r in rows:
        label = CONDITION_LABELS.get(r[0], r[0])
        lines.append(f"{label:<22} {r[1]:>8.1f} {r[2]:>8.1f} {r[3]:>8.1f} {r[4]:>8.1f}")
    for line in lines:
        print(line)


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Statistical analysis of evaluation results")
    parser.add_argument("--db", default=str(ROOT / "data" / "evaluation.duckdb"))
    parser.add_argument("--csv", action="store_true", help="Export tables to data/analysis/")
    args = parser.parse_args()

    conn = load_data(args.db)

    # Record counts
    total = conn.execute(
        "SELECT COUNT(*) FROM evaluation_runs WHERE is_pilot = FALSE"
    ).fetchone()[0]
    print(f"\nTotal non-pilot records: {total}")

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
    failures = query_failure_breakdown(conn)
    print_failure_breakdown(failures)

    # ── Token costs ──────────────────────────────────────────────────────
    print_token_cost_summary(conn)

    # ── Latency summary ──────────────────────────────────────────────────
    print_latency_summary(conn)

    conn.close()

    # ── CSV export ───────────────────────────────────────────────────────
    if args.csv:
        out_dir = ROOT / "data" / "analysis"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "condition_all_tasks.csv").write_text("\n".join(csv_all) + "\n")
        (out_dir / "condition_synthesis_only.csv").write_text("\n".join(csv_synth) + "\n")
        (out_dir / "per_task_detail.csv").write_text("\n".join(csv_per_task) + "\n")
        print(f"\nCSV files written to {out_dir}/")


if __name__ == "__main__":
    main()
