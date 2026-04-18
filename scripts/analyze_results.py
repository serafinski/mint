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

    lines.append("  Synthesis tasks only (T04-T13):")
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
    per_run_rows = conn.execute(
        """
        SELECT condition, run_id, capability_id, COUNT(*) AS invocations
        FROM invocation_events
        GROUP BY condition, run_id, capability_id
        ORDER BY condition, run_id, capability_id
        """
    ).fetchall()

    if not per_run_rows:
        print(f"\n{'='*80}")
        print("  Gini Coefficient of Capability Invocation Counts")
        print(f"{'='*80}")
        print("  (skipped: invocation_events table is empty)")
        return

    # Group by (condition, run_id) → list of counts
    from collections import defaultdict
    by_run: dict[tuple[str, str], list[int]] = defaultdict(list)
    by_condition: dict[str, list[int]] = defaultdict(list)
    for cond, run_id, _cap_id, inv_count in per_run_rows:
        by_run[(cond, run_id)].append(int(inv_count))
        by_condition[cond].append(int(inv_count))

    # Compute per-run Ginis
    condition_ginis: dict[str, list[float]] = defaultdict(list)
    for (cond, _run_id), counts in by_run.items():
        condition_ginis[cond].append(_gini_coefficient(counts))

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
        """
        SELECT task_id, condition, capability_type
        FROM evaluation_runs
        WHERE is_pilot = FALSE AND condition != 'static'
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
        """
        SELECT condition,
               COUNT(*) AS n_included,
               AVG(attempt_latency_ms) / 1000.0 AS avg_sec,
               MEDIAN(attempt_latency_ms) / 1000.0 AS median_sec,
               QUANTILE_CONT(attempt_latency_ms, 0.95) / 1000.0 AS p95_sec,
               STDDEV_SAMP(attempt_latency_ms) / 1000.0 AS stdev_sec
        FROM evaluation_runs
        WHERE gate_passed
          AND output_in_range
          AND NOT is_pilot
        GROUP BY condition
        ORDER BY condition
        """
    ).fetchall()

    excluded_counts = conn.execute(
        """
        SELECT condition, COUNT(*)
        FROM evaluation_runs
        WHERE (NOT gate_passed OR NOT output_in_range)
          AND NOT is_pilot
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


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Statistical analysis of evaluation results")
    parser.add_argument("--db", default=str(ROOT / "data" / "eval" / "metrics.duckdb"))
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

    # ── Core-work classification accuracy (Step 18.5) ────────────────────
    # Task-level primary-type match as operational proxy for Pillar 1 RQ #1.
    # See print_classification_accuracy docstring for framing rationale.
    print_classification_accuracy(conn)

    # ── Gini coefficient of capability invocation counts (§Metrics) ──────
    print_gini_summary(conn)

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
