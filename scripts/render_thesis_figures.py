"""Render thesis-ready figures from frozen evaluation artifacts.

This script intentionally reads the frozen Chapter 6 / validation campaign
outputs instead of re-running analyzers or recomputing headline metrics.

Usage:
    uv run scripts/render_thesis_figures.py
"""

# ruff: noqa: E402,I001

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EVAL_DIR = ROOT / "docs" / "eval_runs" / "20260425"
DEFAULT_OUTPUT_DIR = ROOT / "docs" / "figures" / "thesis"

M1_SOURCE = "20_analyzer_metrics.json"
M6_SOURCE = "33_analyzer_m6.json"
LONGITUDINAL_SOURCE = "longitudinal_convergence.csv"
FAILURE_SOURCE = "12_metrics.duckdb"

CONDITION_ORDER = ["full", "no_verification", "no_optimization", "static"]
CONDITION_LABELS = {
    "full": "pełny",
    "no_verification": "bez weryfikacji",
    "no_optimization": "bez optymalizacji",
    "static": "statyczny",
}

PNG_METADATA = {"Software": "JustInTimeMAS scripts/render_thesis_figures.py"}


def _configure_style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.edgecolor": "#444444",
            "axes.labelcolor": "#222222",
            "axes.linewidth": 0.8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "font.family": "DejaVu Sans",
            "font.size": 10.5,
            "legend.frameon": False,
            "savefig.bbox": "tight",
            "savefig.facecolor": "white",
            "xtick.color": "#333333",
            "ytick.color": "#333333",
        }
    )


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _load_longitudinal_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    required = {"cycle", "n_active_caps", "kl_utility", "gini"}
    missing = required - set(rows[0].keys() if rows else [])
    if missing:
        raise ValueError(f"{path} is missing required columns: {sorted(missing)}")

    parsed: list[dict[str, Any]] = []
    for row in rows:
        registry_snapshot_size = (
            int(row["registry_snapshot_size"])
            if row.get("registry_snapshot_size")
            else None
        )
        parsed.append(
            {
                "cycle": int(row["cycle"]),
                "task_id": row.get("task_id", ""),
                "n_active_caps": int(row["n_active_caps"]),
                "registry_snapshot_size": registry_snapshot_size,
                "kl_utility": float(row["kl_utility"]),
                "gini": float(row["gini"]),
                "merge_operations": int(row.get("merge_operations") or 0),
            }
        )
    return parsed


def _save(fig: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=220, metadata=PNG_METADATA)
    plt.close(fig)


def render_m1_success(metrics_path: Path, output_dir: Path) -> Path:
    metrics = _load_json(metrics_path)
    m1 = metrics.get("M1")
    if not isinstance(m1, dict):
        raise ValueError(f"{metrics_path} does not contain an M1 object")

    successes: list[int] = []
    totals: list[int] = []
    percentages: list[float] = []
    for condition in CONDITION_ORDER:
        row = m1.get(condition)
        if not isinstance(row, dict):
            raise ValueError(f"{metrics_path} M1 is missing condition {condition!r}")
        successes.append(int(row["successes"]))
        totals.append(int(row["n"]))
        percentages.append(float(row["pct"]))

    fig, ax = plt.subplots(figsize=(8.2, 4.4))
    colors = ["#4E6E8E", "#7A8A92", "#8B7D5A", "#8C8C8C"]
    x = range(len(CONDITION_ORDER))
    bars = ax.bar(x, percentages, color=colors, width=0.64)

    ax.set_ylabel("Skuteczność [%]")
    ax.set_xlabel("Wariant")
    ax.set_ylim(0, 100)
    ax.set_xticks(list(x), [CONDITION_LABELS[c] for c in CONDITION_ORDER])
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.0f}%"))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=6))
    ax.grid(axis="y", color="#DDDDDD", linewidth=0.7)
    ax.set_axisbelow(True)

    for bar, pct, n_success, n_total in zip(bars, percentages, successes, totals, strict=True):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 2.0,
            f"{pct:.2f}%\n{n_success}/{n_total}",
            ha="center",
            va="bottom",
            fontsize=9.5,
            color="#222222",
        )

    fig.tight_layout()
    path = output_dir / "walidacja_m1_success_by_condition.png"
    _save(fig, path)
    return path


def render_longitudinal_kl(m6_path: Path, output_dir: Path) -> Path:
    m6 = _load_json(m6_path)
    rows = m6.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"{m6_path} does not contain a rows array")

    cycles = [int(row["cycle"]) for row in rows]
    kl_utility = [float(row["kl_utility"]) for row in rows]
    convergence_cycle = int(m6.get("convergence_cycle", 4))
    threshold = 0.05

    fig, ax = plt.subplots(figsize=(8.2, 4.5))
    ax.plot(
        cycles,
        kl_utility,
        color="#395C7A",
        marker="o",
        markersize=3.2,
        linewidth=1.45,
    )
    ax.axhline(
        threshold,
        color="#8F4E4E",
        linestyle=(0, (5, 3)),
        linewidth=1.0,
    )
    ax.axvline(
        convergence_cycle,
        color="#5D6F4C",
        linestyle=(0, (3, 3)),
        linewidth=1.0,
    )

    ax.annotate(
        f"zbieżność\ncykl {convergence_cycle}",
        xy=(convergence_cycle, threshold),
        xytext=(convergence_cycle + 2.2, threshold * 0.86),
        arrowprops={"arrowstyle": "-", "color": "#5D6F4C", "linewidth": 0.8},
        color="#3E4B34",
        fontsize=9,
        ha="left",
        va="top",
    )
    ax.annotate(
        "próg 0,05",
        xy=(31, threshold),
        xytext=(31, threshold + 0.005),
        color="#6D3939",
        fontsize=9,
        ha="left",
        va="bottom",
    )

    ax.set_xlabel("Cykl")
    ax.set_ylabel("Dywergencja KL")
    ax.set_xlim(1, 40)
    ax.set_ylim(0, max(0.06, max(kl_utility) * 1.15))
    ax.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=10))
    ax.grid(axis="y", color="#DDDDDD", linewidth=0.7)
    ax.set_axisbelow(True)

    fig.tight_layout()
    path = output_dir / "walidacja_longitudinal_kl.png"
    _save(fig, path)
    return path


def render_registry_evolution(longitudinal_path: Path, output_dir: Path) -> Path:
    rows = _load_longitudinal_csv(longitudinal_path)
    cycles = [row["cycle"] for row in rows]
    registry_sizes = [
        row["registry_snapshot_size"]
        if row["registry_snapshot_size"] is not None
        else row["n_active_caps"]
        for row in rows
    ]
    merge_operations = [row["merge_operations"] for row in rows]
    gini = [row["gini"] for row in rows]
    initial_registry_size = registry_sizes[0]
    line_cycles = [0, *cycles]
    line_registry_sizes = [initial_registry_size, *registry_sizes]

    inferred_additions: list[int] = []
    previous_size = initial_registry_size
    for registry_size, merge_count in zip(registry_sizes, merge_operations, strict=True):
        net_delta = registry_size - previous_size
        inferred_additions.append(max(0, net_delta + merge_count))
        previous_size = registry_size
    merge_reductions = [-count for count in merge_operations]

    fig, (ax_caps, ax_gini) = plt.subplots(
        2,
        1,
        figsize=(8.6, 5.7),
        sharex=True,
        gridspec_kw={"height_ratios": [1.0, 1.0], "hspace": 0.08},
    )

    ax_delta = ax_caps.twinx()

    line_caps = ax_caps.plot(
        line_cycles,
        line_registry_sizes,
        color="#3F6B57",
        marker="o",
        markersize=3.0,
        linewidth=1.45,
        label="liczba zdolności w migawce",
    )
    bars_add = ax_delta.bar(
        cycles,
        inferred_additions,
        width=0.46,
        color="#9FBCA9",
        alpha=0.58,
        label="przyrost w cyklu",
    )
    bars_merge = ax_delta.bar(
        cycles,
        merge_reductions,
        width=0.46,
        color="#B58B74",
        alpha=0.72,
        label="redukcja przez scalenie",
    )
    ax_delta.axhline(0, color="#777777", linewidth=0.75)
    ax_caps.set_ylabel("Liczba zdolności\nw migawce rejestru")
    ax_caps.set_ylim(0, max(registry_sizes) + 8)
    ax_caps.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=6))
    ax_caps.grid(axis="y", color="#DDDDDD", linewidth=0.7)
    ax_caps.set_axisbelow(True)
    ax_delta.set_ylabel("Zmiana liczby\nzdolności w cyklu")
    max_positive_delta = max(inferred_additions) if inferred_additions else 1
    max_negative_delta = abs(min(merge_reductions)) if merge_reductions else 1
    ax_delta.set_ylim(
        -max(2, max_negative_delta + 1),
        max(6, max_positive_delta + 1),
    )
    ax_delta.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=5))
    ax_delta.spines.right.set_visible(True)
    ax_caps.legend(
        [line_caps[0], bars_add, bars_merge],
        [
            "liczba zdolności w migawce",
            "przyrost w cyklu",
            "redukcja przez scalenie",
        ],
        loc="upper left",
        fontsize=8.8,
    )

    ax_gini.plot(
        cycles,
        gini,
        color="#6B5E3E",
        marker="o",
        markersize=3.0,
        linewidth=1.45,
    )
    ax_gini.set_xlabel("Cykl")
    ax_gini.set_ylabel("Współczynnik Giniego\nskumulowanych wywołań")
    ax_gini.set_xlim(0, 40)
    ax_gini.set_ylim(0, max(0.10, max(gini) * 1.25))
    ax_gini.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=10))
    ax_gini.grid(axis="y", color="#DDDDDD", linewidth=0.7)
    ax_gini.set_axisbelow(True)

    path = output_dir / "walidacja_registry_evolution.png"
    _save(fig, path)
    return path


def _load_failure_breakdown(db_path: Path) -> dict[str, dict[str, int]]:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("duckdb is required for the optional failure breakdown") from exc

    query = """
        SELECT
            condition,
            COALESCE(NULLIF(failure_reason, ''), 'unknown') AS reason,
            COUNT(*) AS n
        FROM evaluation_runs
        WHERE NOT is_pilot
          AND NOT (gate_passed AND output_in_range)
        GROUP BY condition, reason
        ORDER BY condition, reason
    """
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        records = conn.execute(query).fetchall()
    finally:
        conn.close()

    by_condition: dict[str, dict[str, int]] = {condition: {} for condition in CONDITION_ORDER}
    for condition, reason, count in records:
        if condition in by_condition:
            by_condition[condition][reason] = int(count)
    return by_condition


def render_failure_breakdown(db_path: Path, output_dir: Path) -> Path | None:
    if not db_path.exists():
        return None

    by_condition = _load_failure_breakdown(db_path)
    reasons = sorted({reason for values in by_condition.values() for reason in values})
    if not reasons:
        return None

    reason_colors = {
        "agent_execution_error": "#7B5E68",
        "chain_step_failed": "#4E6E8E",
        "generation_failed": "#8B7D5A",
        "output_rejected": "#7A8A92",
        "tier3": "#8F4E4E",
        "unknown": "#8C8C8C",
    }
    reason_labels = {
        "agent_execution_error": "błąd wykonania agenta",
        "chain_step_failed": "błąd kroku łańcucha",
        "generation_failed": "błąd generowania",
        "output_rejected": "odrzucony wynik",
        "tier3": "weryfikacja Tier 3",
        "unknown": "nieznane",
    }

    fig, ax = plt.subplots(figsize=(8.2, 4.6))
    x = list(range(len(CONDITION_ORDER)))
    bottoms = [0] * len(CONDITION_ORDER)
    for reason in reasons:
        values = [by_condition[condition].get(reason, 0) for condition in CONDITION_ORDER]
        ax.bar(
            x,
            values,
            bottom=bottoms,
            color=reason_colors.get(reason, "#777777"),
            width=0.64,
            label=reason_labels.get(reason, reason.replace("_", " ")),
        )
        bottoms = [bottom + value for bottom, value in zip(bottoms, values, strict=True)]

    for xpos, total in zip(x, bottoms, strict=True):
        ax.text(xpos, total + 0.55, str(total), ha="center", va="bottom", fontsize=9.5)

    ax.set_ylabel("Liczba nieudanych przebiegów")
    ax.set_xlabel("Wariant")
    ax.set_xticks(x, [CONDITION_LABELS[c] for c in CONDITION_ORDER])
    ax.set_ylim(0, max(bottoms) + 4)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.grid(axis="y", color="#DDDDDD", linewidth=0.7)
    ax.set_axisbelow(True)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=3)

    fig.tight_layout()
    path = output_dir / "walidacja_failure_breakdown.png"
    _save(fig, path)
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render thesis PNG figures from frozen evaluation artifacts."
    )
    parser.add_argument(
        "--eval-dir",
        type=Path,
        default=DEFAULT_EVAL_DIR,
        help=f"Frozen evaluation directory (default: {DEFAULT_EVAL_DIR})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Figure output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--skip-failure-breakdown",
        action="store_true",
        help="Do not render the optional failure-breakdown figure.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    eval_dir = args.eval_dir.resolve()
    output_dir = args.output_dir.resolve()
    _configure_style()

    generated = [
        render_m1_success(eval_dir / M1_SOURCE, output_dir),
        render_longitudinal_kl(eval_dir / M6_SOURCE, output_dir),
        render_registry_evolution(eval_dir / LONGITUDINAL_SOURCE, output_dir),
    ]

    if not args.skip_failure_breakdown:
        failure_path = render_failure_breakdown(eval_dir / FAILURE_SOURCE, output_dir)
        if failure_path is not None:
            generated.append(failure_path)

    print("Generated thesis figures:")
    for path in generated:
        try:
            display_path = path.relative_to(ROOT)
        except ValueError:
            display_path = path
        print(f"  {display_path}")


if __name__ == "__main__":
    main()
