"""Analyze the recurrence evaluation track."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402


def _gini(values: list[int]) -> float:
    vals = [v for v in values if v >= 0]
    if not vals:
        return 0.0
    total = sum(vals)
    if total == 0:
        return 0.0
    vals.sort()
    n = len(vals)
    numer = sum((2 * i - n - 1) * v for i, v in enumerate(vals, start=1))
    return numer / (n * total)


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze recurrence evaluation results")
    parser.add_argument("--db", required=True, type=str)
    args = parser.parse_args()

    conn = duckdb.connect(args.db, read_only=True)

    total = conn.execute(
        """
        SELECT COUNT(*) FROM evaluation_runs
        WHERE benchmark_track = 'recurrence'
        """
    ).fetchone()[0]
    if total == 0:
        print("No recurrence rows found.")
        return 1

    overall = conn.execute(
        """
        SELECT condition,
               COUNT(*) AS n,
               AVG(CAST(gate_passed AND output_in_range AND NOT output_shape_mismatch AS DOUBLE)) AS success_rate
        FROM evaluation_runs
        WHERE benchmark_track = 'recurrence'
        GROUP BY condition
        ORDER BY condition
        """
    ).fetchall()
    print("Recurrence Summary")
    for condition, n, success_rate in overall:
        print(f"  {condition}: n={n} success={success_rate:.1%}")

    by_family = conn.execute(
        """
        SELECT condition, task_family_id,
               COUNT(*) AS n,
               AVG(CAST(gate_passed AND output_in_range AND NOT output_shape_mismatch AS DOUBLE)) AS success_rate
        FROM evaluation_runs
        WHERE benchmark_track = 'recurrence'
        GROUP BY condition, task_family_id
        ORDER BY task_family_id, condition
        """
    ).fetchall()
    print("\nPer-Family Success")
    for condition, family, n, success_rate in by_family:
        print(f"  {family} / {condition}: n={n} success={success_rate:.1%}")

    rows = conn.execute(
        """
        SELECT condition, run_id, capability_id, COUNT(*) AS n
        FROM invocation_events
        WHERE benchmark_track = 'recurrence'
        GROUP BY condition, run_id, capability_id
        ORDER BY condition, run_id, capability_id
        """
    ).fetchall()
    gini_by_condition: dict[str, list[float]] = {}
    current_key = None
    current_counts: list[int] = []
    for condition, run_id, _capability_id, n in rows:
        key = (condition, run_id)
        if current_key is None:
            current_key = key
        if key != current_key:
            gini_by_condition.setdefault(current_key[0], []).append(_gini(current_counts))
            current_key = key
            current_counts = []
        current_counts.append(int(n))
    if current_key is not None:
        gini_by_condition.setdefault(current_key[0], []).append(_gini(current_counts))

    print("\nReuse Gini")
    for condition, ginis in sorted(gini_by_condition.items()):
        mean_gini = sum(ginis) / max(len(ginis), 1)
        print(f"  {condition}: mean_gini={mean_gini:.3f} runs={len(ginis)}")

    event_rows = conn.execute(
        """
        SELECT condition, event_type, COUNT(*) AS n
        FROM cycle_events
        WHERE benchmark_track = 'recurrence'
        GROUP BY condition, event_type
        ORDER BY condition, event_type
        """
    ).fetchall()
    print("\nCycle Events")
    for condition, event_type, n in event_rows:
        print(f"  {condition} / {event_type}: {n}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
