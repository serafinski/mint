"""Context-economy compression-loss analyzer (Step 13.8).

Reads `evaluation_runs` from `data/eval/metrics.duckdb`, classifies every
failure row into one of five categories, reports per-condition + per-type
rates, and identifies the most-frequently-dropped keys.

Why this script exists.
The thesis proposal §Bootstrapping T3 (context economy) names TWO failure
modes: naive-verbose (handled by `scripts/analyze_context_utilization.py`)
AND aggressive-compression — "aggressive compression loses critical
details." The typed `output_schema` contracts on SubTasks (Pillar 1 §3.4)
are the mechanism meant to resist the second failure mode. This script
measures how often that mechanism catches a downstream step trying to use
a key an upstream step dropped — the T3 aggressive-compression signal.

Failure-classification taxonomy (mutually exclusive, priority order):
  1. compression_loss_subtask — mid-chain schema violation. Identified
     via the substring "missing declared schema keys" in
     ``failed_step_reason`` (from §12 finding #12 runtime check in
     CompositionalExecutor). THIS IS THE T3-SECOND-FAILURE-MODE SIGNAL.
  2. compression_loss_task    — task-boundary contract violation
     (Step 14.6). ``output_shape_mismatch=True`` AND
     ``output_shape_missing_keys`` non-empty. Also T3 compression-loss
     but surfaced at CLI exit rather than mid-chain.
  3. agent_execution_timeout  — AGENT-specific, from sticky meta.
  4. agent_execution_error    — AGENT-specific, from sticky meta.
  5. wrong_value              — chain ran to completion, validator
     rejected value. Pillar 1 reasoning failure or Pillar 2 Tier 3 gap,
     NOT compression loss.
  6. gate_rejection           — verification gate blocked execution.
  7. generation_failed        — LLM never produced a valid artifact.
  8. exception                — uncategorized chain_step_failed with no
     distinguishing reason string.
  9. other                    — residual.

Usage::

    # Full report against default eval DB
    uv run scripts/analyze_compression_loss.py

    # Single condition
    uv run scripts/analyze_compression_loss.py --condition full

    # Export per-condition table as CSV
    uv run scripts/analyze_compression_loss.py --csv --output-dir data/analysis

    # Top-N most-dropped keys (default 10)
    uv run scripts/analyze_compression_loss.py --top-n 20
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402

DEFAULT_DB = str(ROOT / "data" / "eval" / "metrics.duckdb")

CONDITION_LABELS: dict[str, str] = {
    "static": "Static",
    "no_verification": "JIT (no verif.)",
    "no_optimization": "JIT (no optim.)",
    "full": "Full JIT",
    "longitudinal": "Longitudinal",
}

# Schema violation substring emitted by
# ``src/orchestrator/composition.py::_execute_step`` when an AGENT or
# SKILL step returns a dict missing keys the decomposer declared in
# the subtask's ``output_schema``. Stable across the §12 finding #12
# message format; do not tighten further without checking composition.py.
_SCHEMA_VIOLATION_MARKER = "missing declared schema keys"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FailureRow:
    condition: str
    capability_type: str | None
    failed_step_reason: str | None
    output_shape_mismatch: bool
    output_shape_missing_keys: list[str]
    output_in_range: bool
    gate_passed: bool
    chain_success: bool
    failure_reason: str  # enum value — "chain_step_failed" / "output_rejected" / ...


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


def classify_failure(row: FailureRow) -> str:
    """Assign one of the taxonomy categories to a failure row.

    Priority order matters: a missing-key error is technically a KeyError
    (class 8 exception) but its dominant framing for T3 is the schema
    violation (class 1). Similarly, task-boundary compression loss (class
    2) takes precedence over the generic chain-step failure enum (class 8).
    """
    # 1. Subtask-level schema violation (the T3 signal)
    if row.failed_step_reason and _SCHEMA_VIOLATION_MARKER in row.failed_step_reason:
        return "compression_loss_subtask"

    # 2. Task-boundary contract violation (Step 14.6)
    if row.output_shape_mismatch and row.output_shape_missing_keys:
        return "compression_loss_task"

    # 3-4. AGENT-specific sticky-meta reasons
    if row.failure_reason == "agent_execution_timeout":
        return "agent_execution_timeout"
    if row.failure_reason == "agent_execution_error":
        return "agent_execution_error"

    # 5. Chain completed, validator rejected value
    if row.chain_success and not row.output_in_range:
        return "wrong_value"
    if row.failure_reason == "output_rejected":
        return "wrong_value"

    # 6. Verification gate blocked execution
    if not row.gate_passed and row.failure_reason in ("tier1", "tier2", "tier3"):
        return "gate_rejection"

    # 7. Generation never produced a valid artifact
    if row.failure_reason == "generation_failed":
        return "generation_failed"

    # 8. Uncategorized chain_step_failed — no distinguishing reason string.
    if row.failure_reason == "chain_step_failed":
        return "exception"

    return "other"


def is_compression_loss(category: str) -> bool:
    """Both subtask and task-level compression-loss categories."""
    return category in ("compression_loss_subtask", "compression_loss_task")


# ---------------------------------------------------------------------------
# Missing-key extraction
# ---------------------------------------------------------------------------


def extract_missing_keys(row: FailureRow) -> list[str]:
    """Return the list of keys the downstream step expected but didn't get.

    Two sources, in priority order:
      1. Subtask-level: parse ``failed_step_reason`` of the form
         ``"<type> output missing declared schema keys ['k1', 'k2']
         (declared: [...], got: [...])"`` — extract the first bracketed
         list after the marker phrase.
      2. Task-level: use ``output_shape_missing_keys`` directly.
    """
    if row.failed_step_reason and _SCHEMA_VIOLATION_MARKER in row.failed_step_reason:
        return _parse_first_bracketed_list(row.failed_step_reason)

    if row.output_shape_mismatch and row.output_shape_missing_keys:
        return list(row.output_shape_missing_keys)

    return []


def _parse_first_bracketed_list(reason: str) -> list[str]:
    """Parse the first ``[...]`` after the marker phrase.

    Example input::

        "agent output missing declared schema keys ['schema_sha'] "
        "(declared: ['parsed_rows', 'schema_sha'], got: ['parsed_rows'])"

    Returns::

        ['schema_sha']
    """
    idx = reason.find(_SCHEMA_VIOLATION_MARKER)
    if idx < 0:
        return []
    # Find the first '[' after the marker
    lbrack = reason.find("[", idx)
    if lbrack < 0:
        return []
    rbrack = reason.find("]", lbrack)
    if rbrack < 0:
        return []
    body = reason[lbrack + 1 : rbrack].strip()
    if not body:
        return []
    # Split on comma, strip quotes + whitespace.
    return [tok.strip().strip("'\"") for tok in body.split(",") if tok.strip()]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_failures(
    conn: duckdb.DuckDBPyConnection, *, condition: str | None = None
) -> list[FailureRow]:
    """Fetch every non-pilot failure row."""
    clauses = ["NOT is_pilot", "(NOT gate_passed OR NOT output_in_range)"]
    params: list[object] = []
    if condition is not None:
        clauses.append("condition = ?")
        params.append(condition)
    where = " AND ".join(clauses)
    rows = conn.execute(
        f"""
        SELECT condition, capability_type, failed_step_reason,
               output_shape_mismatch, output_shape_missing_keys,
               output_in_range, gate_passed, chain_success, failure_reason
        FROM evaluation_runs
        WHERE {where}
        """,
        params,
    ).fetchall()

    out: list[FailureRow] = []
    for (
        condition_,
        capability_type,
        failed_step_reason,
        output_shape_mismatch,
        output_shape_missing_keys_blob,
        output_in_range,
        gate_passed,
        chain_success,
        failure_reason,
    ) in rows:
        try:
            keys = (
                json.loads(output_shape_missing_keys_blob)
                if output_shape_missing_keys_blob
                else []
            )
        except (ValueError, TypeError):
            keys = []
        out.append(
            FailureRow(
                condition=condition_,
                capability_type=capability_type,
                failed_step_reason=failed_step_reason,
                output_shape_mismatch=bool(output_shape_mismatch),
                output_shape_missing_keys=[str(k) for k in keys]
                if isinstance(keys, list)
                else [],
                output_in_range=bool(output_in_range),
                gate_passed=bool(gate_passed),
                chain_success=bool(chain_success),
                failure_reason=failure_reason,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


_ALL_CATEGORIES: list[str] = [
    "compression_loss_subtask",
    "compression_loss_task",
    "wrong_value",
    "agent_execution_timeout",
    "agent_execution_error",
    "exception",
    "gate_rejection",
    "generation_failed",
    "other",
]


def aggregate_by(
    rows: list[FailureRow], key_fn
) -> dict[object, dict[str, int]]:
    """Group rows by `key_fn(row)`, counting each category."""
    out: dict[object, dict[str, int]] = {}
    for row in rows:
        k = key_fn(row)
        bucket = out.setdefault(k, dict.fromkeys(_ALL_CATEGORIES, 0))
        bucket[classify_failure(row)] += 1
    return out


def _print_category_table(
    title: str,
    agg: dict[object, dict[str, int]],
    *,
    label_fn=lambda k: str(k),
) -> list[list[str]]:
    """Print a per-row-key category table. Returns CSV-ready matrix."""
    print()
    print("=" * 100)
    print(f"  {title}")
    print("=" * 100)

    headers = ["Group", "Total", "CompLoss%", "Subtask", "Task", "Wrong", "Timeout", "Exc", "Other"]
    widths = [22, 6, 10, 9, 7, 7, 9, 5, 7]
    print(" ".join(f"{h:<{w}}" for h, w in zip(headers, widths)))
    print("-" * sum(widths) + "-" * (len(widths) - 1))

    matrix: list[list[str]] = [headers]
    for key in sorted(agg.keys(), key=lambda k: str(k) if k is not None else ""):
        buckets = agg[key]
        total = sum(buckets.values())
        if total == 0:
            continue
        compression_loss = (
            buckets["compression_loss_subtask"] + buckets["compression_loss_task"]
        )
        other_bucket = (
            buckets["agent_execution_error"]
            + buckets["gate_rejection"]
            + buckets["generation_failed"]
            + buckets["other"]
        )
        cl_rate = 100.0 * compression_loss / total if total else 0.0
        row_label = label_fn(key)
        cells = [
            f"{row_label:<22}",
            f"{total:>6d}",
            f"{cl_rate:>9.1f}%",
            f"{buckets['compression_loss_subtask']:>9d}",
            f"{buckets['compression_loss_task']:>7d}",
            f"{buckets['wrong_value']:>7d}",
            f"{buckets['agent_execution_timeout']:>9d}",
            f"{buckets['exception']:>5d}",
            f"{other_bucket:>7d}",
        ]
        print(" ".join(cells))
        matrix.append(
            [
                row_label,
                str(total),
                f"{cl_rate:.1f}",
                str(buckets["compression_loss_subtask"]),
                str(buckets["compression_loss_task"]),
                str(buckets["wrong_value"]),
                str(buckets["agent_execution_timeout"]),
                str(buckets["exception"]),
                str(other_bucket),
            ]
        )
    return matrix


def print_per_condition_table(rows: list[FailureRow]) -> list[list[str]]:
    agg = aggregate_by(rows, key_fn=lambda r: r.condition)
    return _print_category_table(
        "Failures by Condition (compression-loss share of total failures)",
        agg,
        label_fn=lambda k: CONDITION_LABELS.get(k, k),
    )


def print_per_type_table(rows: list[FailureRow]) -> list[list[str]]:
    agg = aggregate_by(rows, key_fn=lambda r: r.capability_type or "unknown")
    return _print_category_table(
        "Failures by Capability Type (expected signal: AGENT > SKILL > MCP_TOOL)",
        agg,
    )


def print_most_dropped_keys_table(
    rows: list[FailureRow], *, top_n: int = 10
) -> list[list[str]]:
    """Count frequency of each (capability_type, missing_key)."""
    counter: Counter[tuple[str, str]] = Counter()
    for row in rows:
        if not is_compression_loss(classify_failure(row)):
            continue
        cap_type = row.capability_type or "unknown"
        for key in extract_missing_keys(row):
            counter[(cap_type, key)] += 1

    print()
    print("=" * 100)
    print(f"  Top-{top_n} Most-Frequently-Dropped Keys (compression-loss failures only)")
    print("=" * 100)

    headers = ["Capability Type", "Missing Key", "Frequency"]
    widths = [18, 40, 10]
    print(" ".join(f"{h:<{w}}" for h, w in zip(headers, widths)))
    print("-" * sum(widths) + "-" * (len(widths) - 1))

    matrix: list[list[str]] = [headers]
    if not counter:
        print("(no compression-loss failures observed)")
        return matrix

    for (cap_type, key), freq in counter.most_common(top_n):
        print(f"{cap_type:<18} {key:<40} {freq:>10d}")
        matrix.append([cap_type, key, str(freq)])
    return matrix


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


def _write_csv(matrix: list[list[str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(matrix)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=DEFAULT_DB, help="Path to evaluation DuckDB.")
    parser.add_argument("--condition", default=None, help="Restrict to a single condition.")
    parser.add_argument(
        "--top-n", type=int, default=10,
        help="Number of most-dropped keys to report (default 10).",
    )
    parser.add_argument("--csv", action="store_true", help="Export tables as CSV.")
    parser.add_argument(
        "--output-dir", default=str(ROOT / "data" / "analysis"),
        help="CSV output directory.",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 1

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        # Guard against DBs that aren't eval DBs (e.g. CLI metrics.duckdb,
        # which holds the operational invocations table, not evaluation_runs).
        has_table = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'evaluation_runs'"
        ).fetchone()
        if not has_table:
            print(
                f"ERROR: {db_path} does not contain an 'evaluation_runs' "
                f"table — is this an evaluation DB? Run "
                f"`uv run scripts/run_pilot.py` first.",
                file=sys.stderr,
            )
            return 1
        rows = load_failures(conn, condition=args.condition)
    finally:
        conn.close()

    print(f"Loaded {len(rows)} failure rows from {db_path}")
    if args.condition:
        print(f"Filtered to condition: {args.condition}")
    if not rows:
        print("No failure rows found — nothing to classify.")
        return 0

    per_cond = print_per_condition_table(rows)
    per_type = print_per_type_table(rows)
    dropped = print_most_dropped_keys_table(rows, top_n=args.top_n)

    if args.csv:
        out_dir = Path(args.output_dir)
        _write_csv(per_cond, out_dir / "compression_loss_by_condition.csv")
        _write_csv(per_type, out_dir / "compression_loss_by_type.csv")
        _write_csv(dropped, out_dir / "compression_loss_top_keys.csv")
        print(f"\nCSV exported to {out_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
