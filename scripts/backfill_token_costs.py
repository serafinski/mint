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

DB_PATH = str(ROOT / "data" / "eval" / "metrics.duckdb")
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
    base_url: str,
    auth: httpx.Auth,
    start: datetime,
    end: datetime,
    *,
    max_pages: int = 500,
    environment: str | None = "default",
    session_ids: list[str] | None = None,
) -> tuple[list[dict], bool]:
    """Fetch all GENERATION observations within the time window via REST API.

    ext14 #141 (2026-04-21): applies server-side ``fromStartTime`` /
    ``toStartTime`` filters so the 50k-page budget is spent on in-window
    observations only. Pre-fix, the request was unscoped and the 50k
    ceiling could be burned on CLI traces / prior campaigns / other
    projects on the same LangFuse instance before any in-window rows
    were reached, silently producing spurious "zero generations" for
    runs whose data fell past the ceiling.

    Returns ``(generations, truncated)`` — ``truncated`` is True when the
    pagination ceiling was hit. Pre-fix, truncation was silent and
    downstream per-run coverage gates (ext12 #122, ext13 #130) fired
    spuriously while misattributing the cause to retention.
    """
    import logging as _lg

    generations: list[dict] = []
    truncated = False
    page = 1
    with httpx.Client(base_url=base_url, auth=auth, timeout=30.0) as client:
        while True:
            # ext15 #170 (2026-04-22): add environment filter. Runner
            # emits on environment="default"; dev scripts typically on
            # "test". Foreign traces would otherwise silently inflate M4.
            params: dict[str, object] = {
                "type": "GENERATION",
                "limit": 100,
                "page": page,
                "fromStartTime": start.isoformat(),
                "toStartTime": end.isoformat(),
            }
            if environment:
                params["environment"] = environment
            resp = client.get(
                "/api/public/observations",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if not data:
                break

            for obs in data:
                ts = _parse_dt(obs.get("startTime"))
                if ts and start <= ts <= end:
                    # ext15 #170: post-fetch session whitelist (defensive —
                    # server-side environment filter is the primary defense).
                    if session_ids is not None and obs.get("sessionId") not in session_ids:
                        continue
                    usage = obs.get("usage") or {}
                    # tmp.md P0 (2026-04-22): token extraction must NOT
                    # trust usage.total alone. Some providers emit
                    # input+output but omit total; pre-fix those generations
                    # landed as zero-token rows, silently undercounting M4.
                    prompt_tokens = int(usage.get("input", 0) or 0)
                    completion_tokens = int(usage.get("output", 0) or 0)
                    total_tokens = int(usage.get("total", 0) or 0)
                    if total_tokens == 0 and (prompt_tokens + completion_tokens) > 0:
                        total_tokens = prompt_tokens + completion_tokens
                    generations.append({
                        "id": obs.get("id", ""),
                        "trace_id": obs.get("traceId", ""),
                        "session_id": obs.get("sessionId", ""),
                        "name": obs.get("name", ""),
                        "model": obs.get("model", ""),
                        "start_time": ts,
                        "end_time": _parse_dt(obs.get("endTime")),
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                        "calculated_total_cost": obs.get("calculatedTotalCost", 0.0) or 0.0,
                        "usage_details": obs.get("usageDetails") or {},
                    })

            if len(data) < 100:
                break
            page += 1
            if page > max_pages:
                _lg.getLogger(__name__).warning(
                    "ext14 #141: fetch_all_generations hit pagination ceiling "
                    "at page %d (max_pages=%d, ~%d observations fetched). "
                    "Data beyond the ceiling was silently dropped.",
                    page, max_pages, len(generations),
                )
                truncated = True
                break
    return generations, truncated


# ── Evaluation DB queries ────────────────────────────────────────────────────


def get_run_windows(
    conn: duckdb.DuckDBPyConnection,
    *,
    run_ids: list[str] | None = None,
    since: str | None = None,
) -> list[dict]:
    """Get per-run_id time windows from the evaluation DB.

    ext8 #58 Bug B (2026-04-20): also fetches the set of distinct task_ids
    actually present in each run. ``n_jit_tasks`` needs the real set, not
    blind ``n_distinct_tasks - 3`` (which collapses partial runs without
    the 3 retrieval tasks to 0).

    ext15 #167 (2026-04-22): accepts ``run_ids`` and ``since`` to honor
    the run-sheet contract that Row 11 scope by campaign run_ids. Pre-fix,
    the script had no accepting parameter for scope — default-workflow
    was structurally unsatisfiable under ``--append`` DBs.
    """
    clauses = ["is_pilot = FALSE"]
    params: list[object] = []
    if run_ids:
        clauses.append(
            "run_id IN ({})".format(", ".join("?" * len(run_ids)))
        )
        params.extend(run_ids)
    if since is not None:
        clauses.append("timestamp >= ?")
        params.append(since)
    where = " AND ".join(clauses)
    rows = conn.execute(
        f"""
        SELECT run_id, condition,
               MIN(timestamp) AS start_ts,
               MAX(timestamp) AS end_ts,
               COUNT(*) AS n_tasks,
               COUNT(DISTINCT task_id) AS n_distinct_tasks
        FROM evaluation_runs
        WHERE {where}
        GROUP BY run_id, condition
        ORDER BY MIN(timestamp)
        """,
        params,
    ).fetchall()

    # ext8 #58 Bug B: per-run task_id sets (one query, then bucket by run_id).
    task_rows = conn.execute(
        f"SELECT DISTINCT run_id, task_id FROM evaluation_runs WHERE {where}",
        params,
    ).fetchall()
    actual_task_ids_by_run: dict[str, set[str]] = {}
    for rid, tid in task_rows:
        actual_task_ids_by_run.setdefault(rid, set()).add(tid)

    return [
        {
            "run_id": r[0],
            "condition": r[1],
            "start_ts": r[2].replace(tzinfo=UTC) if r[2].tzinfo is None else r[2],
            "end_ts": r[3].replace(tzinfo=UTC) if r[3].tzinfo is None else r[3],
            "n_tasks": r[4],
            "n_distinct_tasks": r[5],
            "actual_task_ids": actual_task_ids_by_run.get(r[0], set()),
        }
        for r in rows
    ]


def get_per_record_info(
    conn: duckdb.DuckDBPyConnection,
    *,
    run_ids: list[str] | None = None,
    since: str | None = None,
) -> list[dict]:
    """Get all non-pilot records for backfill.

    ext15 #167 (2026-04-22): honors ``--run-ids`` / ``--since`` scope.
    """
    clauses = ["is_pilot = FALSE"]
    params: list[object] = []
    if run_ids:
        clauses.append(
            "run_id IN ({})".format(", ".join("?" * len(run_ids)))
        )
        params.extend(run_ids)
    if since is not None:
        clauses.append("timestamp >= ?")
        params.append(since)
    rows = conn.execute(
        f"""
        SELECT id, run_id, condition, task_id, token_cost, timestamp
        FROM evaluation_runs
        WHERE {" AND ".join(clauses)}
        ORDER BY timestamp
        """,
        params,
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
    generations: list[dict], run_windows: list[dict],
    *,
    buffer_seconds: int = 1800,
) -> tuple[dict[str, list[dict]], int]:
    """Map each generation to the best-matching run_id.

    Strategy: for each generation, find the run window that contains it
    (with a small buffer). When multiple windows overlap, pick the one
    whose midpoint is closest to the generation timestamp.

    tmp.md P0 (2026-04-22): ``buffer_seconds`` default widened from 120
    to 1800 (30 min). EvaluationRecord.timestamp is set at task-END time,
    so early generations of a long first task (AGENT tasks can take 10+
    minutes on first attempt) were dropped as unmatched under the old
    buffer. Overlap-guard clipping (finding #105 below) still applies
    between adjacent runs, so the wider buffer can't reintroduce overlap.
    """
    run_to_gens: dict[str, list[dict]] = defaultdict(list)
    unmatched = 0

    # Sort run windows by start time
    sorted_rws = sorted(run_windows, key=lambda rw: rw["start_ts"])

    # Security audit finding #105 (2026-04-19, ext10): pre-fix every
    # run got an unconditional buffer, so two runs started within its
    # span had OVERLAPPING buffer windows. Clip each run's buffer by
    # adjacent run boundaries (with a 30s no-man's-land between).
    NON_STATIC_RWS = [rw for rw in sorted_rws if rw["condition"] != "static"]
    OVERLAP_GUARD = timedelta(seconds=30)
    BUFFER = timedelta(seconds=buffer_seconds)
    clipped_buffers: dict[str, tuple] = {}
    for i, rw in enumerate(NON_STATIC_RWS):
        lo = rw["start_ts"] - BUFFER
        hi = rw["end_ts"] + BUFFER
        if i > 0:
            prev = NON_STATIC_RWS[i - 1]
            lo = max(lo, prev["end_ts"] + OVERLAP_GUARD)
        if i + 1 < len(NON_STATIC_RWS):
            nxt = NON_STATIC_RWS[i + 1]
            hi = min(hi, nxt["start_ts"] - OVERLAP_GUARD)
        # Keyed by run_id since rw dicts aren't hashable.
        clipped_buffers[rw["run_id"]] = (lo, hi)

    for gen in generations:
        ts = gen["start_time"]
        best_rw = None
        best_dist = timedelta.max

        for rw in sorted_rws:
            # Skip static runs (0 LLM cost)
            if rw["condition"] == "static":
                continue
            buf_start, buf_end = clipped_buffers.get(
                rw["run_id"],
                (rw["start_ts"] - BUFFER, rw["end_ts"] + BUFFER),
            )
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

        # For longitudinal runs, all tasks use the pipeline (including T01-T03)
        if rw["condition"] == "longitudinal":
            n_jit_tasks = rw["n_distinct_tasks"]
        else:
            # ext8 #58 Bug B (2026-04-20): compute n_jit_tasks from the
            # ACTUAL distinct task_ids this run covered, not by blind
            # subtraction. Pre-fix ``n_distinct_tasks - 3`` collapsed to
            # 0 on partial runs (e.g. ``--tasks T11,T12,T13`` → 3 JIT
            # tasks reported as n_jit_tasks=0, per-task cost=0) and
            # miscounted any partial set without exactly 3 retrieval
            # tasks present. The ``actual_task_ids`` set is populated by
            # ``get_run_windows`` via a per-run distinct-task_id query.
            actual_task_ids = rw.get("actual_task_ids", set())
            if actual_task_ids:
                n_jit_tasks = len(actual_task_ids - RETRIEVAL_TASKS)
            else:
                # Legacy caller that didn't populate actual_task_ids
                # (e.g., a unit test with a hand-built rw). Fall back to
                # the n_distinct_tasks - 3 heuristic but clamp at 0.
                n_jit_tasks = max(
                    rw["n_distinct_tasks"] - len(RETRIEVAL_TASKS),
                    0,
                )

        # Security audit finding #28 Bug A (2026-04-20): use total-record
        # count (``n_tasks``) for the per-record divisor, NOT distinct
        # count. Pre-fix a 40-cycle longitudinal run over 20 unique
        # tasks divided total_tokens by 20 then assigned per-record to
        # all 40 rows → 2x overcount. ``n_records`` is the cycle count
        # and is the correct divisor for per-record apportionment.
        summaries[rid] = {
            "condition": rw["condition"],
            "total_tokens": total_tokens,
            "total_cost_usd": total_cost,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "n_generations": len(gens),
            "n_tasks": rw["n_tasks"],  # total records (cycles) — for divisor
            "n_distinct_tasks": rw["n_distinct_tasks"],  # preserved for per-JIT rollup
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
    - longitudinal records: run_total_tokens / n_tasks (all tasks use the
      pipeline, including T01-T03 which call the decomposition DeepAgent)
    - Evaluation JIT records for T01-T03 (registry match): 0 tokens (negligible
      cost compared to generation/verification — attributing to JIT tasks only
      gives a more meaningful per-task estimate)
    - Other JIT tasks: run_total_tokens / n_jit_tasks (equal distribution)
    """
    updates: list[tuple[str, int]] = []

    for rec in records:
        if rec["condition"] == "static":
            updates.append((rec["id"], 0))
            continue

        summary = run_summaries.get(rec["run_id"])

        # Longitudinal: every task runs the full pipeline, distribute evenly
        if rec["condition"] == "longitudinal":
            if summary and summary["n_tasks"] > 0:
                per_task = summary["total_tokens"] // summary["n_tasks"]
                updates.append((rec["id"], per_task))
            else:
                updates.append((rec["id"], 0))
            continue

        # Evaluation: retrieval tasks get 0 (negligible decomposition cost)
        if rec["task_id"] in RETRIEVAL_TASKS:
            updates.append((rec["id"], 0))
            continue

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
    print("\n  Token breakdown by model:")
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


def fetch_traces_by_session(
    base_url: str, auth: httpx.Auth, session_id: str
) -> list[dict]:
    """Fetch all traces for a given session_id (= run_id) via REST API."""
    traces = []
    page = 1
    with httpx.Client(base_url=base_url, auth=auth, timeout=30.0) as client:
        while True:
            resp = client.get(
                "/api/public/traces",
                params={"sessionId": session_id, "limit": 100, "page": page},
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if not data:
                break

            for t in data:
                tags = t.get("tags") or []
                metadata = t.get("metadata") or {}
                total_cost = t.get("totalCost", 0) or 0
                traces.append({
                    "id": t["id"],
                    "name": t.get("name", ""),
                    "tags": tags,
                    "metadata": metadata,
                    "total_cost": total_cost,
                    "task_id": metadata.get("task_id"),
                    "condition": metadata.get("condition"),
                })

            if len(data) < 100:
                break
            page += 1
            if page > 200:
                break
    return traces


def fetch_observations_for_trace(
    base_url: str, auth: httpx.Auth, trace_id: str
) -> int:
    """Fetch total tokens for all observations in a trace."""
    total_tokens = 0
    page = 1
    with httpx.Client(base_url=base_url, auth=auth, timeout=30.0) as client:
        while True:
            resp = client.get(
                "/api/public/observations",
                params={"traceId": trace_id, "type": "GENERATION", "limit": 100, "page": page},
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if not data:
                break

            for obs in data:
                usage = obs.get("usage") or {}
                # tmp.md P0 (2026-04-22): total may be missing even when
                # input+output are present. Fall back to input+output.
                obs_total = int(usage.get("total", 0) or 0)
                if obs_total == 0:
                    obs_total = int(usage.get("input", 0) or 0) + int(
                        usage.get("output", 0) or 0
                    )
                total_tokens += obs_total

            if len(data) < 100:
                break
            page += 1
    return total_tokens


def compute_per_task_costs(
    base_url: str,
    auth: httpx.Auth,
    records: list[dict],
    run_windows: list[dict],
    run_summaries: dict[str, dict] | None = None,
) -> list[tuple[str, int]] | None:
    """Try per-task attribution via tagged LangFuse traces.

    Security audit findings #30 + #37 (2026-04-20): probes tagged
    traces **per-run** instead of "first non-static run only". Pre-fix
    a single untagged run at the front of the iteration disabled
    per-task mode for every run in the DB (including newer, tagged
    runs whose data would have attributed cleanly).

    Returns (record_id, token_cost) pairs, or ``None`` when NO run in
    the DB has any tagged traces (caller falls back to pure run-level
    mode). Mixed-era DBs use per-task for tagged runs and fall back
    to run-level-divided-by-cycle-count for untagged runs — no records
    silently receive 0.
    """
    runs_by_id = {rw["run_id"]: rw for rw in run_windows}

    # Per-run tag detection: fetch traces for each non-static run and
    # record which ones carry task_id tags.
    run_ids = {r["run_id"] for r in records if r["condition"] != "static"}
    if not run_ids:
        return None

    run_task_tokens: dict[tuple[str, str], int] = {}
    tagged_run_ids: set[str] = set()

    for i, rid in enumerate(sorted(run_ids)):
        if (i + 1) % 5 == 0 or i == len(run_ids) - 1:
            print(f"    Fetching traces for run {i + 1}/{len(run_ids)}...", flush=True)

        traces = fetch_traces_by_session(base_url, auth, rid)
        task_traces: dict[str, list[dict]] = defaultdict(list)
        for t in traces:
            tid = t.get("task_id")
            if tid:
                task_traces[tid].append(t)

        if not task_traces:
            # Untagged run — caller will fall back to run-level mode.
            continue
        tagged_run_ids.add(rid)

        for task_id, t_list in task_traces.items():
            total = 0
            for t in t_list:
                total += fetch_observations_for_trace(base_url, auth, t["id"])
            run_task_tokens[(rid, task_id)] = total

    if not tagged_run_ids:
        # No run in the entire DB has tagged traces — pure fallback.
        return None

    print(
        f"  Per-task trace tags detected on {len(tagged_run_ids)}/"
        f"{len(run_ids)} non-static run(s); mixed-mode attribution."
    )

    # Security audit finding #28 Bug B (2026-04-20): count cycles per
    # (run_id, task_id) so the aggregated per-task total splits evenly
    # across repeated cycles of the same task. Pre-fix every cycle
    # received the SUMMED total → 2x overcount for 2-cycle tasks.
    from collections import Counter as _Counter
    cycle_counts: _Counter[tuple[str, str]] = _Counter()
    run_record_counts: _Counter[str] = _Counter()
    # ext8 #58 Bug A: track retrieval-task record counts per-run so the
    # untagged-run fallback divisor reflects JIT-only rows.
    run_retrieval_record_counts: _Counter[str] = _Counter()
    for rec in records:
        if rec["condition"] != "static":
            cycle_counts[(rec["run_id"], rec["task_id"])] += 1
            run_record_counts[rec["run_id"]] += 1
            if (
                rec["condition"] != "longitudinal"
                and rec["task_id"] in RETRIEVAL_TASKS
            ):
                run_retrieval_record_counts[rec["run_id"]] += 1

    # ext13 #129: enumerate (run_id, task_id) keys present vs expected
    # for every tagged run. A missing key means that task's traces never
    # carried a task_id tag; ``run_task_tokens.get(key, 0)`` would
    # silently write 0 for every record of that task below, and ext12
    # #122's run-level gate would pass because other tasks in the same
    # run were tagged. Stash the missing keys on the function so main()
    # can refuse the write unless --allow-partial-task-coverage is set.
    expected_keys_per_tagged_run: dict[str, set[tuple[str, str]]] = {}
    for rec in records:
        if rec["condition"] == "static":
            continue
        rid = rec["run_id"]
        if rid in tagged_run_ids:
            expected_keys_per_tagged_run.setdefault(rid, set()).add(
                (rid, rec["task_id"])
            )
    present_keys = set(run_task_tokens.keys())
    per_run_missing_tasks: dict[str, list[str]] = {}
    for rid, expected in expected_keys_per_tagged_run.items():
        missing = sorted(tid for (r, tid) in expected - present_keys)
        if missing:
            per_run_missing_tasks[rid] = missing
    # Attach diagnostics to the function object so main() can read them
    # without restructuring the call signature.
    compute_per_task_costs.last_missing_task_tags = per_run_missing_tasks  # type: ignore[attr-defined]
    compute_per_task_costs.last_tagged_run_ids = frozenset(tagged_run_ids)  # type: ignore[attr-defined]

    # Build updates
    updates: list[tuple[str, int]] = []
    for rec in records:
        if rec["condition"] == "static":
            updates.append((rec["id"], 0))
            continue

        # ext8 #58 Bug A (2026-04-20): zero retrieval tasks regardless of
        # whether this run is tagged or falls through to the untagged
        # fallback, UNLESS LangFuse actually shows non-zero tokens for
        # them (via per-task tags). Pre-fix the fallback at line 575
        # skipped the retrieval-zero check that ``compute_backfill_values``
        # applies, inflating T01/T02/T03 cost with a share of JIT tokens.
        #
        # tmp.md P1 (2026-04-22): retrieval tasks CAN incur real
        # generation tokens (classifier invocation on cache hits, etc.).
        # If per-task tagging found real tokens for this (run, retrieval
        # task), respect the measured value rather than forcing zero.
        if (
            rec["condition"] not in ("longitudinal", "longitudinal_shift")
            and rec["task_id"] in RETRIEVAL_TASKS
        ):
            rid = rec["run_id"]
            measured = run_task_tokens.get((rid, rec["task_id"]), 0) if rid in tagged_run_ids else 0
            if measured > 0:
                n_cycles = cycle_counts.get((rid, rec["task_id"]), 1) or 1
                updates.append((rec["id"], measured // n_cycles))
            else:
                updates.append((rec["id"], 0))
            continue

        rid = rec["run_id"]
        if rid in tagged_run_ids:
            key = (rid, rec["task_id"])
            total_for_task = run_task_tokens.get(key, 0)
            n_cycles = cycle_counts.get(key, 1) or 1
            tokens = total_for_task // n_cycles
        else:
            # Security audit finding #37 (2026-04-20): untagged run in a
            # mixed-era DB falls back to run-level-divided-by-cycle-count
            # instead of silently writing 0.
            #
            # ext8 #58 Bug A: the fallback divisor must reflect JIT-only
            # record count (retrieval tasks are zeroed above) so the
            # JIT share isn't diluted by retrieval-zeros in the run.
            summary = (run_summaries or {}).get(rid)
            run_total = (summary or {}).get("total_tokens", 0)
            jit_records_in_run = max(
                run_record_counts.get(rid, 1)
                - run_retrieval_record_counts.get(rid, 0),
                1,
            )
            tokens = run_total // jit_records_in_run
        updates.append((rec["id"], tokens))

    return updates


def main() -> int:
    """Backfill token costs from LangFuse.

    Exit codes (ext12 #122 + ext13 #129/#130 2026-04-21):
        0: backfill completed cleanly (all non-static runs had LangFuse
           generations matched, or --allow-partial was explicitly passed)
        1: CLI / auth / connectivity failure
        2: LangFuse returned ZERO generations in the window (ext6 #36
           global-empty loud-fail, preserved)
        3: some non-static runs had zero LangFuse generations matched —
           M4 backfill would silently write zero cost for those runs.
           Pass --allow-partial to override, with Chapter 4.5 disclosure.
        9: per-task tag coverage gap within a tagged run (ext13 #129).
           Pass --allow-partial-task-coverage to override.
        10: generations-per-JIT-record below threshold (ext13 #130).
            Pass --allow-under-coverage (with --min-gens-per-jit-record)
            to override.
    """
    parser = argparse.ArgumentParser(description="Backfill token costs from LangFuse")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--write", action="store_true", help="Actually update DuckDB")
    parser.add_argument("--csv", action="store_true", help="Export cost summary CSV")
    # ext12 #122 (2026-04-21): per-run completeness guard.
    parser.add_argument(
        "--allow-partial", action="store_true",
        help="Proceed even when some non-static runs have ZERO matched "
             "LangFuse generations. Default: refuse with exit 3. When "
             "passed, the affected runs receive token_cost=0 for every "
             "record — Chapter 4.5 MUST disclose this as an M4 limitation. "
             "ext12 #122.",
    )
    # ext13 #129: per-run per-task tag coverage gate. ext12 #122 covers
    # the run-level case (a run with zero tagged generations); this flag
    # covers the finer-granularity case — a run with SOME tasks tagged
    # and others missing the task_id tag. Without this gate the missing
    # tasks silently get token_cost=0 (``run_task_tokens.get(key, 0)``).
    parser.add_argument(
        "--allow-partial-task-coverage", action="store_true",
        help="Proceed with token_cost=0 for tasks within tagged runs that "
             "lack LangFuse per-task traces. Default: refuse with exit 9. "
             "Chapter 4.5 disclosure REQUIRED. ext13 #129.",
    )
    # ext13 #130: quantitative generations-per-JIT-record threshold.
    # ext12 #122's zero-vs-nonzero check lets a run with one matched
    # generation across 20 tasks pass the gate; this flag sets a minimum
    # ratio of generations per JIT record.
    parser.add_argument(
        "--allow-under-coverage", action="store_true",
        help="Proceed when LangFuse generation count per JIT record is "
             "below --min-gens-per-jit-record. Default: refuse with exit "
             "10. Chapter 4.5 disclosure REQUIRED. ext13 #130.",
    )
    parser.add_argument(
        "--min-gens-per-jit-record", type=float, default=0.5,
        help="Minimum LangFuse generations per JIT record for a run to be "
             "considered adequately covered (default 0.5). Below this, "
             "exit 10 unless --allow-under-coverage is passed. ext13 #130.",
    )
    # ext14 #141 (2026-04-21): pagination truncation signal + server-side
    # time filter. Prevents silent 50k-row drops when the LangFuse instance
    # has accumulated history larger than the ceiling.
    parser.add_argument(
        "--max-pages", type=int, default=500,
        help="ext14 #141: LangFuse pagination ceiling (default: 500 = 50k "
             "observations at 100/page). Raise for larger campaigns; expect "
             "longer fetch and higher API cost.",
    )
    parser.add_argument(
        "--allow-truncated", action="store_true",
        help="ext14 #141: proceed with Row 11 backfill when the pagination "
             "ceiling was hit. Default: refuse with exit 5. Chapter 4.5 "
             "disclosure REQUIRED.",
    )
    # ext15 #167 (2026-04-22): scope flags to honor the run-sheet contract.
    parser.add_argument(
        "--run-ids", nargs="+",
        help="ext15 #167: scope backfill to specific campaign run_ids. "
             "Required for --append DBs to avoid fetching foreign windows.",
    )
    parser.add_argument(
        "--since",
        help="ext15 #167: scope to runs with timestamp >= ISO8601 value.",
    )
    # ext15 #165 (2026-04-22): unmatched-rate gate.
    parser.add_argument(
        "--allow-unmatched", action="store_true",
        help="ext15 #165: proceed when a non-trivial fraction of LangFuse "
             "generations match no run window (biases M4 downward). "
             "Default: refuse with exit 4.",
    )
    parser.add_argument(
        "--allow-unmatched-fraction", type=float, default=0.25,
        help="ext15 #165: threshold for unmatched-generation fraction "
             "(default 0.25 = 25%%). Set lower for stricter gating.",
    )
    # ext15 #170 (2026-04-22): environment filter.
    parser.add_argument(
        "--environment", default="default",
        help="ext15 #170: LangFuse environment filter (default: 'default'). "
             "Set to '' to disable. Prevents foreign dev-script traces "
             "(environment='test') from inflating M4.",
    )
    # tmp.md P0 (2026-04-22): widen run window. Pre-fix, a single run window
    # buffer was ±120s. Long first tasks can emit generations more than 120s
    # before the first EvaluationRecord.timestamp (which is task-end).
    parser.add_argument(
        "--run-window-buffer-seconds", type=int, default=1800,
        help="tmp.md P0: seconds of buffer on each side of the [MIN(timestamp), "
             "MAX(timestamp)] window when matching generations to runs. "
             "Default raised from 120 to 1800s so early generations of a "
             "long first task are not dropped.",
    )
    args = parser.parse_args()

    print("Connecting to LangFuse REST API...")
    try:
        base_url, auth = _langfuse_client()
    except KeyError as e:
        print(f"ERROR: Missing env var {e}. Set LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST.")
        return 1
    # Quick auth check
    try:
        r = httpx.get(f"{base_url}/api/public/health", auth=auth, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"ERROR: LangFuse health check failed: {e}")
        return 1
    print(f"  Connected to {base_url}")

    # Get evaluation time window from DuckDB
    conn = duckdb.connect(args.db, read_only=not args.write)
    run_windows = get_run_windows(conn, run_ids=args.run_ids, since=args.since)
    if not run_windows:
        # tmp.md P1 (2026-04-22): empty run set was previously an exit-0
        # path, allowing Row 11 to claim success without writing anything.
        # That left stale/missing token_cost values for Chapter 6 M4 while
        # the orchestrator thought the script ran cleanly. Exit 6 now.
        print(
            "ERROR: No evaluation runs found in DB (possibly wrong --db "
            "path, scope filter that matched nothing, or eval not yet "
            "populated). Exit 6. tmp.md P1.",
            file=sys.stderr,
        )
        conn.close()
        return 6

    # tmp.md P0 (2026-04-22): widen the outer fetch window as well. A
    # 30-minute buffer on the outside is proportional to the widened
    # per-run buffer and tolerates LangFuse clock skew.
    start = min(rw["start_ts"] for rw in run_windows) - timedelta(minutes=30)
    end = max(rw["end_ts"] for rw in run_windows) + timedelta(minutes=30)
    print(f"  Evaluation window: {start} → {end}")

    # Fetch LangFuse data
    print("Fetching GENERATION observations from LangFuse...")
    # ext15 #170: pass environment + campaign run_ids as session whitelist
    # (harness emits session_id=run_id).
    session_whitelist = args.run_ids if args.run_ids else None
    environment_filter = args.environment or None
    generations, truncated = fetch_all_generations(
        base_url, auth, start, end,
        max_pages=args.max_pages,
        environment=environment_filter,
        session_ids=session_whitelist,
    )
    print(f"  Found {len(generations)} generations in evaluation window.")

    # ext14 #141 (2026-04-21): pagination-truncation gate. Refuse to proceed
    # with M4 backfill when the fetch hit the hard ceiling — silently
    # truncated pages produce spurious "zero generations for run X" errors
    # (ext12 #122) and per-task under-coverage (ext13 #130) while the real
    # cause is truncation rather than retention.
    if truncated and not args.allow_truncated:
        print(
            f"\nERROR: fetch_all_generations hit the {args.max_pages}-page "
            f"ceiling (~{args.max_pages * 100:,} observations). Data beyond "
            f"the ceiling was silently dropped. This can produce spurious "
            f"'zero generations for run X' errors (ext12 #122) and per-task "
            f"under-coverage (ext13 #130) when the real cause is truncation, "
            f"NOT retention.\n"
            f"  Options:\n"
            f"    (a) narrow the eval window (--since / shorter campaign)\n"
            f"    (b) raise --max-pages (tradeoff: longer fetch + higher API cost)\n"
            f"    (c) verify server-side time filter is applied "
            f"(fromStartTime/toStartTime)\n"
            f"    (d) pass --allow-truncated to proceed with undersampled M4\n"
            f"       (Chapter 4.5 disclosure REQUIRED). ext14 #141. Exit 5.",
            file=sys.stderr,
        )
        conn.close()
        return 5
    if truncated and args.allow_truncated:
        print(
            f"\n  ⚠ [TRUNCATED: {args.max_pages * 100:,}+ observations] — "
            f"M4 numbers under-report. Chapter 4.5 disclosure REQUIRED. "
            f"ext14 #141.\n",
            file=sys.stderr,
        )

    if not generations:
        # Security audit finding #36 (2026-04-20): flipped from
        # silent-exit-0 to loud-fail-exit-2. Pre-fix a purged-LangFuse
        # window silently left every record's token_cost at 0, and the
        # caller had no way to distinguish "backfill ran cleanly" from
        # "backfill found nothing". M4 Chapter 6 tables then showed
        # uniformly-zero costs as legitimate data. Exit code 2 is
        # distinct from the generic error exit 1.
        print(
            "ERROR: No generation observations found. LangFuse data may have "
            "been purged.",
            file=sys.stderr,
        )
        print(
            "       Token costs NOT backfilled. Rerun after restoring "
            "LangFuse data.",
            file=sys.stderr,
        )
        conn.close()
        return 2

    # Map generations to runs
    print("Mapping generations to evaluation runs...")
    run_to_gens, unmatched = map_generations_to_runs(
        generations, run_windows,
        buffer_seconds=args.run_window_buffer_seconds,
    )
    print(f"  Mapped to {len(run_to_gens)} runs, {unmatched} unmatched generations.")

    # ext15 #165 (2026-04-22): unmatched-fraction gate. Pre-fix, high
    # unmatched counts were logged but never gated — foreign / out-of-window
    # generations silently biased M4 (they never landed in any run, so
    # their tokens were discarded from per-run totals).
    n_total_gens = len(generations)
    unmatched_rate = unmatched / n_total_gens if n_total_gens else 0.0
    if unmatched_rate > args.allow_unmatched_fraction:
        msg = (
            f"\nERROR: {unmatched}/{n_total_gens} generations ({unmatched_rate:.1%}) "
            f"did not match any run window (threshold "
            f"{args.allow_unmatched_fraction:.1%}). This biases M4 downward. "
            f"Causes: (a) windows too narrow; (b) foreign traces in window; "
            f"(c) ext15 #170 environment filter needed. Pass --allow-unmatched "
            f"to proceed with disclosure. ext15 #165. Exit 4."
        )
        print(msg, file=sys.stderr)
        if not args.allow_unmatched:
            conn.close()
            return 4
        print("  --allow-unmatched passed: proceeding.", file=sys.stderr)

    # Compute per-run costs
    run_summaries = compute_run_costs(run_to_gens, run_windows)

    # Print summary
    print_summary(run_summaries)
    print_per_run_detail(run_summaries)

    # ext12 #122 (2026-04-21): per-run completeness guard. Pre-fix, ext6 #36
    # loud-failed ONLY when LangFuse returned zero generations globally.
    # Partial coverage (generations exist for some runs but not others —
    # common when LangFuse retention aged out the older traces) yielded
    # n_generations=0 / total_tokens=0 for the uncovered runs, which
    # ``compute_backfill_values`` happily wrote as token_cost=0 on every
    # record of those runs. Row 17's M4 then under-reported those JIT
    # conditions' per-task cost proportional to the missing coverage —
    # worst-case, manufacturing thesis-favorable evidence.
    zero_summary_runs = [
        rid for rid, s in run_summaries.items()
        if s.get("n_generations", 0) == 0 and s.get("condition") != "static"
    ]

    # Per-condition coverage breakdown (visible before any write).
    by_condition: dict[str, tuple[int, int]] = {}
    for rid, s in run_summaries.items():
        cond = s.get("condition", "unknown")
        has, total = by_condition.get(cond, (0, 0))
        has += 1 if s.get("n_generations", 0) > 0 else 0
        total += 1
        by_condition[cond] = (has, total)
    print("\n  Per-condition coverage (runs with >=1 matched LangFuse generation):")
    for cond, (has, total) in sorted(by_condition.items()):
        marker = "⚠" if has < total and cond != "static" else " "
        print(f"  {marker} {cond:<18} {has}/{total}")

    if zero_summary_runs:
        preview = zero_summary_runs[:5]
        more = f" (+{len(zero_summary_runs) - 5} more)" if len(zero_summary_runs) > 5 else ""
        print(
            f"\nERROR: {len(zero_summary_runs)} non-static runs have ZERO "
            f"matched LangFuse generations: {preview}{more}",
            file=sys.stderr,
        )
        print(
            "  Likely causes: (a) LangFuse retention aged out this run's "
            "traces, (b) LangFuse credentials changed between run and "
            "backfill, (c) run_id naming drift between runner and trace "
            "metadata.",
            file=sys.stderr,
        )
        if not args.allow_partial:
            print(
                "  Refusing to write partial backfill. Pass --allow-partial "
                "to proceed with zeros for these runs (document in Chapter "
                "4.5 as an M4 limitation). ext12 #122. Exit 3.",
                file=sys.stderr,
            )
            conn.close()
            return 3
        print(
            f"  --allow-partial passed: proceeding with token_cost=0 for "
            f"{len(zero_summary_runs)} runs. Chapter 4.5 disclosure REQUIRED.",
            file=sys.stderr,
        )

    # Compute backfill values — try per-task attribution first, then run-level
    records = get_per_record_info(conn, run_ids=args.run_ids, since=args.since)

    # ext13 #130: quantitative coverage gate — per-run generations-per-
    # JIT-record ratio. ext12 #122's binary zero-vs-nonzero gate lets a
    # 20-task run with 1 matched generation through; Row 17 M4 then
    # under-reports by ~20x silently. Enforce a minimum ratio (default
    # 0.5 gens / JIT record) unless --allow-under-coverage is passed.
    from collections import Counter as _Counter
    jit_records_per_run: _Counter[str] = _Counter()
    for rec in records:
        if rec["condition"] == "static":
            continue
        if (
            rec["condition"] != "longitudinal"
            and rec["task_id"] in RETRIEVAL_TASKS
        ):
            continue  # retrieval tasks don't generate
        jit_records_per_run[rec["run_id"]] += 1

    under_covered_runs: list[tuple[str, int, int, float]] = []
    min_ratio = args.min_gens_per_jit_record
    for rid, s in run_summaries.items():
        if s.get("condition") == "static":
            continue
        n_gens = s.get("n_generations", 0)
        jit_records = jit_records_per_run.get(rid, 0)
        if jit_records <= 0:
            continue
        ratio = n_gens / jit_records
        if ratio < min_ratio:
            under_covered_runs.append((rid, n_gens, jit_records, ratio))

    # Per-condition quantitative display (replaces ext12 #122's binary
    # "1/1" readout when the under-coverage information is available).
    print(
        "\n  Per-condition LangFuse coverage (generations-per-JIT-record, "
        f"threshold {min_ratio:.2f}):"
    )
    by_cond_gens: dict[str, int] = {}
    by_cond_jit: dict[str, int] = {}
    for rid, s in run_summaries.items():
        cond = s.get("condition", "unknown")
        by_cond_gens[cond] = by_cond_gens.get(cond, 0) + s.get("n_generations", 0)
        by_cond_jit[cond] = by_cond_jit.get(cond, 0) + jit_records_per_run.get(rid, 0)
    for cond in sorted(set(by_cond_gens) | set(by_cond_jit)):
        gens = by_cond_gens.get(cond, 0)
        jit_records = by_cond_jit.get(cond, 0)
        ratio = gens / jit_records if jit_records > 0 else 0.0
        marker = "⚠" if ratio < min_ratio and cond != "static" else " "
        print(
            f"  {marker} {cond:<18} {gens:>4d} gens / {jit_records:>4d} "
            f"JIT records = {ratio:.2f}/record"
        )

    if under_covered_runs:
        preview = under_covered_runs[:5]
        more = (
            f" (+{len(under_covered_runs) - 5} more)"
            if len(under_covered_runs) > 5 else ""
        )
        print(
            f"\nERROR: {len(under_covered_runs)} non-static run(s) have FEWER "
            f"LangFuse generations than threshold {min_ratio:.2f}/JIT-record:"
            f"{more}",
            file=sys.stderr,
        )
        for rid, n_gens, jit_records, ratio in preview:
            print(
                f"  {rid}: {n_gens} gens / {jit_records} JIT records "
                f"= {ratio:.2f} (expected >= {min_ratio:.2f})",
                file=sys.stderr,
            )
        if not args.allow_under_coverage:
            print(
                "  Refusing to write severely under-covered backfill. Pass "
                "--allow-under-coverage to proceed (Chapter 4.5 disclosure "
                "REQUIRED). ext13 #130. Exit 10.",
                file=sys.stderr,
            )
            conn.close()
            return 10
        print(
            f"  --allow-under-coverage passed: proceeding with low-ratio "
            f"writes. Chapter 4.5 disclosure REQUIRED.",
            file=sys.stderr,
        )

    print("\nChecking for per-task trace tags...")
    per_task_updates = compute_per_task_costs(
        base_url, auth, records, run_windows,
        run_summaries=run_summaries,
    )
    if per_task_updates is not None:
        updates = per_task_updates
        print("  Using per-task attribution (traces tagged with task_id).")
        # ext13 #129: per-task tag coverage gate. compute_per_task_costs
        # stashes missing (run_id, task_id) keys on the function object;
        # read and refuse the write if any tagged run has missing tasks
        # unless --allow-partial-task-coverage is set.
        per_run_missing = getattr(
            compute_per_task_costs, "last_missing_task_tags", {}
        )
        if per_run_missing:
            total_missing = sum(len(v) for v in per_run_missing.values())
            print(
                f"\nERROR: {len(per_run_missing)} tagged run(s) have missing "
                f"per-task LangFuse traces ({total_missing} (run_id, task_id) "
                f"keys). Affected tasks would be silently written as "
                f"token_cost=0:",
                file=sys.stderr,
            )
            for rid, tasks in sorted(per_run_missing.items())[:5]:
                print(f"  {rid}: missing {tasks}", file=sys.stderr)
            if len(per_run_missing) > 5:
                print(
                    f"  (+{len(per_run_missing) - 5} more runs)",
                    file=sys.stderr,
                )
            print(
                "  Likely causes:\n"
                "    (a) ThreadPool trace-context bleed (see ext10 #104)\n"
                "    (b) task_id tag dropped at emit time\n"
                "    (c) LangFuse retention aged out per-task traces\n"
                "  Pass --allow-partial-task-coverage to proceed with zeros "
                "for affected tasks (Chapter 4.5 disclosure REQUIRED). "
                "ext13 #129. Exit 9.",
                file=sys.stderr,
            )
            if not args.allow_partial_task_coverage:
                conn.close()
                return 9
            print(
                "  --allow-partial-task-coverage passed: proceeding. "
                "Chapter 4.5 disclosure REQUIRED.",
                file=sys.stderr,
            )
    else:
        updates = compute_backfill_values(records, run_summaries)
        print("  No per-task tags found — using run-level averaging (legacy mode).")

    non_zero = sum(1 for _, v in updates if v > 0)
    print(f"\n  Backfill: {non_zero}/{len(updates)} records would get non-zero token costs")

    if args.write:
        print("\nWriting to DuckDB...")
        # Need a read-write connection
        conn.close()
        conn = duckdb.connect(args.db)
        # tmp.md P1 (2026-04-22): wrap in an explicit transaction so a
        # crash mid-loop doesn't leave mixed old/new token_cost values
        # in the same campaign. Pre-fix each UPDATE auto-committed.
        try:
            conn.execute("BEGIN TRANSACTION")
            for record_id, token_cost in updates:
                conn.execute(
                    "UPDATE evaluation_runs SET token_cost = ? WHERE id = ?",
                    [token_cost, record_id],
                )
            conn.execute("COMMIT")
        except Exception as exc:  # noqa: BLE001
            conn.execute("ROLLBACK")
            conn.close()
            print(
                f"\nERROR: write loop failed mid-batch: {exc}. "
                f"All updates rolled back. tmp.md P1.",
                file=sys.stderr,
            )
            return 1
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
    return 0


if __name__ == "__main__":
    # ext12 #122: propagate main()'s integer return as the process exit
    # code so orchestrators (run sheet, CI) can detect partial-coverage
    # aborts via exit 3. Pre-fix, bare ``main()`` dropped every return
    # value and the process exited 0 even when main explicitly called
    # ``sys.exit(2)`` via the ext6 #36 path.
    sys.exit(main() or 0)
