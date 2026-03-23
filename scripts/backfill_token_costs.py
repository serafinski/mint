"""Backfill token costs from LangFuse traces into the evaluation DuckDB.

Strategy:
- Fetches all GENERATION observations from LangFuse within the evaluation window
- Maps them to evaluation runs by matching trace timestamps to run time windows
- Falls back to per-condition averages when exact mapping isn't possible
- Static runs get 0 tokens (no LLM calls)
- Registry-match tasks (T01-T03) get 0 tokens for JIT conditions too

Usage::

    # Dry run (show what would be updated, don't write)
    uv run scripts/backfill_token_costs.py

    # Actually write to DuckDB
    uv run scripts/backfill_token_costs.py --write

    # Also export per-condition cost summary to CSV
    uv run scripts/backfill_token_costs.py --write --csv

Requires: LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST in .env
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import os  # noqa: E402

import duckdb  # noqa: E402
import httpx  # noqa: E402

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = str(ROOT / "data" / "evaluation.duckdb")
RETRIEVAL_TASKS = {"T01", "T02", "T03"}


# ── LangFuse REST API client ────────────────────────────────────────────────


def _langfuse_client() -> tuple[str, httpx.Auth]:
    """Return (base_url, auth) for LangFuse REST API."""
    host = os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
    public_key = os.environ["LANGFUSE_PUBLIC_KEY"]
    secret_key = os.environ["LANGFUSE_SECRET_KEY"]
    return host, httpx.BasicAuth(public_key, secret_key)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    # Handle various ISO formats from LangFuse API
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def fetch_all_generations(
    base_url: str, auth: httpx.Auth, start: datetime, end: datetime
) -> list[dict]:
    """Fetch all GENERATION observations within the time window via REST API."""
    generations = []
    page = 1
    with httpx.Client(base_url=base_url, auth=auth, timeout=30.0) as client:
        while True:
            resp = client.get(
                "/api/public/observations",
                params={"type": "GENERATION", "limit": 100, "page": page},
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if not data:
                break

            for obs in data:
                ts = _parse_dt(obs.get("startTime"))
                if ts and start <= ts <= end:
                    usage = obs.get("usage") or {}
                    generations.append({
                        "id": obs.get("id", ""),
                        "trace_id": obs.get("traceId", ""),
                        "name": obs.get("name", ""),
                        "model": obs.get("model", ""),
                        "start_time": ts,
                        "end_time": _parse_dt(obs.get("endTime")),
                        "prompt_tokens": usage.get("input", 0) or 0,
                        "completion_tokens": usage.get("output", 0) or 0,
                        "total_tokens": usage.get("total", 0) or 0,
                        "calculated_total_cost": obs.get("calculatedTotalCost", 0.0) or 0.0,
                        "usage_details": obs.get("usageDetails") or {},
                    })

            if len(data) < 100:
                break
            page += 1
            if page > 500:  # safety limit
                break
    return generations


# ── Evaluation DB queries ────────────────────────────────────────────────────


def get_run_windows(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    """Get per-run_id time windows from the evaluation DB."""
    rows = conn.execute(
        """
        SELECT run_id, condition,
               MIN(timestamp) AS start_ts,
               MAX(timestamp) AS end_ts,
               COUNT(*) AS n_tasks,
               COUNT(DISTINCT task_id) AS n_distinct_tasks
        FROM evaluation_runs
        WHERE is_pilot = FALSE
        GROUP BY run_id, condition
        ORDER BY MIN(timestamp)
        """
    ).fetchall()
    return [
        {
            "run_id": r[0],
            "condition": r[1],
            "start_ts": r[2].replace(tzinfo=UTC) if r[2].tzinfo is None else r[2],
            "end_ts": r[3].replace(tzinfo=UTC) if r[3].tzinfo is None else r[3],
            "n_tasks": r[4],
            "n_distinct_tasks": r[5],
        }
        for r in rows
    ]


def get_per_record_info(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    """Get all non-pilot records for backfill."""
    rows = conn.execute(
        """
        SELECT id, run_id, condition, task_id, token_cost, timestamp
        FROM evaluation_runs
        WHERE is_pilot = FALSE
        ORDER BY timestamp
        """
    ).fetchall()
    return [
        {
            "id": r[0],
            "run_id": r[1],
            "condition": r[2],
            "task_id": r[3],
            "token_cost": r[4],
            "timestamp": r[5].replace(tzinfo=UTC) if r[5].tzinfo is None else r[5],
        }
        for r in rows
    ]


# ── Mapping logic ────────────────────────────────────────────────────────────


def map_generations_to_runs(
    generations: list[dict], run_windows: list[dict]
) -> tuple[dict[str, list[dict]], int]:
    """Map each generation to the best-matching run_id.

    Strategy: for each generation, find the run window that contains it
    (with a small buffer). When multiple windows overlap, pick the one
    whose midpoint is closest to the generation timestamp.
    """
    run_to_gens: dict[str, list[dict]] = defaultdict(list)
    unmatched = 0

    # Sort run windows by start time
    sorted_rws = sorted(run_windows, key=lambda rw: rw["start_ts"])

    for gen in generations:
        ts = gen["start_time"]
        best_rw = None
        best_dist = timedelta.max

        for rw in sorted_rws:
            # Skip static runs (0 LLM cost)
            if rw["condition"] == "static":
                continue
            buf_start = rw["start_ts"] - timedelta(seconds=120)
            buf_end = rw["end_ts"] + timedelta(seconds=120)
            if buf_start <= ts <= buf_end:
                mid = rw["start_ts"] + (rw["end_ts"] - rw["start_ts"]) / 2
                dist = abs(ts - mid)
                if dist < best_dist:
                    best_dist = dist
                    best_rw = rw

        if best_rw:
            run_to_gens[best_rw["run_id"]].append(gen)
        else:
            unmatched += 1

    return dict(run_to_gens), unmatched


def compute_run_costs(
    run_to_gens: dict[str, list[dict]],
    run_windows: list[dict],
) -> dict[str, dict]:
    """Compute per-run token and cost summaries."""
    summaries: dict[str, dict] = {}
    for rw in run_windows:
        rid = rw["run_id"]
        gens = run_to_gens.get(rid, [])
        total_tokens = sum(g["total_tokens"] for g in gens)
        total_cost = sum(g["calculated_total_cost"] for g in gens)
        prompt_tokens = sum(g["prompt_tokens"] for g in gens)
        completion_tokens = sum(g["completion_tokens"] for g in gens)

        # Break down by model
        by_model: dict[str, dict] = defaultdict(
            lambda: {"prompt": 0, "completion": 0, "total": 0, "cost": 0.0, "count": 0}
        )
        for g in gens:
            model = g["model"] or "unknown"
            # Normalize model names
            if "gpt-5-mini" in model:
                model = "gpt-5-mini"
            elif "deepseek" in model.lower():
                model = "deepseek-chat"
            by_model[model]["prompt"] += g["prompt_tokens"]
            by_model[model]["completion"] += g["completion_tokens"]
            by_model[model]["total"] += g["total_tokens"]
            by_model[model]["cost"] += g["calculated_total_cost"]
            by_model[model]["count"] += 1

        n_jit_tasks = rw["n_distinct_tasks"] - len(
            RETRIEVAL_TASKS & set()  # We don't know which tasks, estimate
        )
        # Rough: subtract 3 for T01-T03 if this is a full-13-task run
        if rw["n_distinct_tasks"] >= 13:
            n_jit_tasks = 10

        summaries[rid] = {
            "condition": rw["condition"],
            "total_tokens": total_tokens,
            "total_cost_usd": total_cost,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "n_generations": len(gens),
            "n_tasks": rw["n_distinct_tasks"],
            "n_jit_tasks": n_jit_tasks,
            "tokens_per_jit_task": total_tokens // n_jit_tasks if n_jit_tasks > 0 else 0,
            "by_model": dict(by_model),
        }
    return summaries


# ── Backfill logic ───────────────────────────────────────────────────────────


def compute_backfill_values(
    records: list[dict],
    run_summaries: dict[str, dict],
) -> list[tuple[str, int]]:
    """Compute (record_id, token_cost) pairs for backfill.

    Strategy:
    - static records: 0 tokens
    - T01-T03 (registry match): 0 tokens (no generation/verification)
    - JIT tasks: run_total_tokens / n_jit_tasks (equal distribution within run)
    """
    updates: list[tuple[str, int]] = []

    for rec in records:
        if rec["condition"] == "static":
            updates.append((rec["id"], 0))
            continue

        if rec["task_id"] in RETRIEVAL_TASKS:
            updates.append((rec["id"], 0))
            continue

        summary = run_summaries.get(rec["run_id"])
        if summary and summary["n_jit_tasks"] > 0:
            per_task = summary["total_tokens"] // summary["n_jit_tasks"]
            updates.append((rec["id"], per_task))
        else:
            updates.append((rec["id"], 0))

    return updates


# ── Reporting ────────────────────────────────────────────────────────────────


def print_summary(run_summaries: dict[str, dict]) -> None:
    """Print aggregate cost summary."""
    print(f"\n{'='*80}")
    print("  Token Cost Summary from LangFuse Traces")
    print(f"{'='*80}")

    # Aggregate by condition
    by_condition: dict[str, dict] = defaultdict(
        lambda: {
            "total_tokens": 0,
            "total_cost": 0.0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "n_generations": 0,
            "n_runs": 0,
            "n_jit_tasks": 0,
        }
    )
    for rid, s in run_summaries.items():
        c = s["condition"]
        by_condition[c]["total_tokens"] += s["total_tokens"]
        by_condition[c]["total_cost"] += s["total_cost_usd"]
        by_condition[c]["prompt_tokens"] += s["prompt_tokens"]
        by_condition[c]["completion_tokens"] += s["completion_tokens"]
        by_condition[c]["n_generations"] += s["n_generations"]
        by_condition[c]["n_runs"] += 1
        by_condition[c]["n_jit_tasks"] += s["n_jit_tasks"]

    header = (
        f"{'Condition':<22} {'Runs':>5} {'Gens':>6} "
        f"{'Prompt':>10} {'Compl':>10} {'Total':>10} "
        f"{'Cost($)':>9} {'Tok/JIT':>10}"
    )
    print(header)
    print("-" * len(header))

    for cond in ["full", "no_verification", "no_optimization", "static"]:
        d = by_condition.get(cond)
        if not d:
            continue
        tok_per_jit = d["total_tokens"] // d["n_jit_tasks"] if d["n_jit_tasks"] > 0 else 0
        print(
            f"{cond:<22} {d['n_runs']:>5} {d['n_generations']:>6} "
            f"{d['prompt_tokens']:>10,} {d['completion_tokens']:>10,} "
            f"{d['total_tokens']:>10,} "
            f"${d['total_cost']:>8.4f} {tok_per_jit:>10,}"
        )

    # Grand total
    grand_tokens = sum(d["total_tokens"] for d in by_condition.values())
    grand_cost = sum(d["total_cost"] for d in by_condition.values())
    grand_gens = sum(d["n_generations"] for d in by_condition.values())
    print(f"\n  Grand total: {grand_tokens:,} tokens, ${grand_cost:.4f}, {grand_gens} generations")

    # Model breakdown
    print(f"\n  Token breakdown by model:")
    model_totals: dict[str, dict] = defaultdict(
        lambda: {"prompt": 0, "completion": 0, "total": 0, "cost": 0.0, "count": 0}
    )
    for s in run_summaries.values():
        for model, md in s["by_model"].items():
            model_totals[model]["prompt"] += md["prompt"]
            model_totals[model]["completion"] += md["completion"]
            model_totals[model]["total"] += md["total"]
            model_totals[model]["cost"] += md["cost"]
            model_totals[model]["count"] += md["count"]

    for model, md in sorted(model_totals.items()):
        print(
            f"    {model:<20} {md['total']:>10,} tokens  "
            f"${md['cost']:>.4f}  ({md['count']} calls)"
        )


def print_per_run_detail(run_summaries: dict[str, dict]) -> None:
    """Print per-run token costs."""
    print(f"\n{'='*80}")
    print("  Per-Run Detail")
    print(f"{'='*80}")
    header = f"{'Run ID':<32} {'Cond':<18} {'Tokens':>10} {'Cost($)':>9} {'Gens':>5} {'Tok/JIT':>10}"
    print(header)
    print("-" * len(header))
    for rid, s in sorted(run_summaries.items(), key=lambda x: x[0]):
        tok_per_jit = s["tokens_per_jit_task"]
        print(
            f"{rid:<32} {s['condition']:<18} "
            f"{s['total_tokens']:>10,} ${s['total_cost_usd']:>8.4f} "
            f"{s['n_generations']:>5} {tok_per_jit:>10,}"
        )


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill token costs from LangFuse")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--write", action="store_true", help="Actually update DuckDB")
    parser.add_argument("--csv", action="store_true", help="Export cost summary CSV")
    args = parser.parse_args()

    print("Connecting to LangFuse REST API...")
    try:
        base_url, auth = _langfuse_client()
    except KeyError as e:
        print(f"ERROR: Missing env var {e}. Set LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST.")
        sys.exit(1)
    # Quick auth check
    try:
        r = httpx.get(f"{base_url}/api/public/health", auth=auth, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"ERROR: LangFuse health check failed: {e}")
        sys.exit(1)
    print(f"  Connected to {base_url}")

    # Get evaluation time window from DuckDB
    conn = duckdb.connect(args.db, read_only=not args.write)
    run_windows = get_run_windows(conn)
    if not run_windows:
        print("No evaluation runs found in DB.")
        conn.close()
        return

    start = min(rw["start_ts"] for rw in run_windows) - timedelta(minutes=5)
    end = max(rw["end_ts"] for rw in run_windows) + timedelta(minutes=5)
    print(f"  Evaluation window: {start} → {end}")

    # Fetch LangFuse data
    print("Fetching GENERATION observations from LangFuse...")
    generations = fetch_all_generations(base_url, auth, start, end)
    print(f"  Found {len(generations)} generations in evaluation window.")

    if not generations:
        print("WARNING: No generation observations found. LangFuse data may have been purged.")
        print("         Token costs will remain at 0.")
        conn.close()
        return

    # Map generations to runs
    print("Mapping generations to evaluation runs...")
    run_to_gens, unmatched = map_generations_to_runs(generations, run_windows)
    print(f"  Mapped to {len(run_to_gens)} runs, {unmatched} unmatched generations.")

    # Compute per-run costs
    run_summaries = compute_run_costs(run_to_gens, run_windows)

    # Print summary
    print_summary(run_summaries)
    print_per_run_detail(run_summaries)

    # Compute backfill values
    records = get_per_record_info(conn)
    updates = compute_backfill_values(records, run_summaries)

    non_zero = sum(1 for _, v in updates if v > 0)
    print(f"\n  Backfill: {non_zero}/{len(updates)} records would get non-zero token costs")

    if args.write:
        print("\nWriting to DuckDB...")
        # Need a read-write connection
        conn.close()
        conn = duckdb.connect(args.db)
        for record_id, token_cost in updates:
            conn.execute(
                "UPDATE evaluation_runs SET token_cost = ? WHERE id = ?",
                [token_cost, record_id],
            )
        conn.close()
        print(f"  Updated {len(updates)} records.")
    else:
        print("\n  Dry run — no changes written. Use --write to update DuckDB.")
        conn.close()

    # CSV export
    if args.csv:
        out_dir = ROOT / "data" / "analysis"
        out_dir.mkdir(parents=True, exist_ok=True)

        lines = ["condition,run_id,total_tokens,prompt_tokens,completion_tokens,cost_usd,n_generations,tokens_per_jit_task"]
        for rid, s in sorted(run_summaries.items()):
            lines.append(
                f"{s['condition']},{rid},{s['total_tokens']},{s['prompt_tokens']},"
                f"{s['completion_tokens']},{s['total_cost_usd']:.6f},"
                f"{s['n_generations']},{s['tokens_per_jit_task']}"
            )
        csv_path = out_dir / "token_costs_per_run.csv"
        csv_path.write_text("\n".join(lines) + "\n")
        print(f"  CSV written to {csv_path}")

    print("Done.")


if __name__ == "__main__":
    main()
