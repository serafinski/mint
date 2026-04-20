"""Extract context window utilization from LangFuse traces.

Reads existing LangFuse traces (no LLM calls, no pipeline runs) to measure
how much of the model's context window each DeepAgent invocation consumes.

For each trace, finds the peak input_tokens across all GENERATION observations
(= cumulative context size at the last LLM call) and computes utilization as
a percentage of the model's context window.

Usage::

    # Summary table by agent type
    uv run scripts/analyze_context_utilization.py

    # Include per-trace detail
    uv run scripts/analyze_context_utilization.py --detail

    # CSV output
    uv run scripts/analyze_context_utilization.py --csv

    # Only look at last 3 days
    uv run scripts/analyze_context_utilization.py --days 3
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import httpx  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# GPT-5-mini context window (tokens)
MODEL_CONTEXT_WINDOW = 128_000

# Agent types we care about (trace names from DeepAgent invocations)
AGENT_TRACE_NAMES = {
    "task-decomposer": "Decomposition",
    "adversarial-prober": "Adversarial Probing",
    "skill-executor": "SKILL Execution",
    "agent-executor": "AGENT Execution",
}

# Pipeline-level traces (not DeepAgent, but useful for total cost)
PIPELINE_TRACE_NAME = "LangGraph"


# ---------------------------------------------------------------------------
# LangFuse REST API
# ---------------------------------------------------------------------------


def _langfuse_client() -> tuple[str, httpx.Auth]:
    host = os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
    public_key = os.environ["LANGFUSE_PUBLIC_KEY"]
    secret_key = os.environ["LANGFUSE_SECRET_KEY"]
    return host, httpx.BasicAuth(public_key, secret_key)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def fetch_traces(
    client: httpx.Client,
    name: str,
    start: datetime,
    end: datetime,
    environment: str | None = None,
    session_ids: list[str] | None = None,
) -> list[dict]:
    """Fetch all traces with a given name within the time window.

    Security audit finding #39 (2026-04-20): accepts ``environment`` and
    ``session_ids`` to scope fetches to a single campaign. Pre-fix, any
    trace in the time window with a matching name was returned,
    including smoke-test / dev runs and prior campaigns.
    """
    traces = []
    page = 1
    while True:
        params = {"name": name, "limit": 100, "page": page}
        if environment:
            params["environment"] = environment
        resp = client.get("/api/public/traces", params=params)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            break

        for t in data:
            ts = _parse_dt(t.get("timestamp"))
            if ts and start <= ts <= end:
                if session_ids and t.get("sessionId") not in session_ids:
                    continue
                traces.append({
                    "id": t["id"],
                    "name": t.get("name", ""),
                    "timestamp": ts,
                    "session_id": t.get("sessionId"),
                    "latency": t.get("latency", 0) or 0,
                    "total_cost": t.get("totalCost", 0) or 0,
                    "observation_ids": t.get("observations", []),
                    "metadata": t.get("metadata") or {},
                })

        if len(data) < 100:
            break
        page += 1
        if page > 200:
            break

    return traces


def fetch_observation(client: httpx.Client, obs_id: str) -> dict | None:
    """Fetch a single observation by ID."""
    try:
        resp = client.get(f"/api/public/observations/{obs_id}")
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def extract_trace_context(client: httpx.Client, trace: dict) -> dict:
    """Extract context utilization metrics from a trace's observations.

    Returns a dict with step_count, peak_input_tokens, peak_output_tokens,
    total_tokens, reasoning_tokens, and per-step token progression.
    """
    steps: list[dict] = []

    for obs_id in trace["observation_ids"]:
        obs = fetch_observation(client, obs_id)
        if obs is None:
            continue

        # Only look at GENERATION observations (LLM calls)
        if obs.get("type") != "GENERATION":
            continue

        usage = obs.get("usage") or {}
        usage_details = obs.get("usageDetails") or {}
        input_tokens = usage.get("input", 0) or 0
        output_tokens = usage.get("output", 0) or 0
        total_tokens = usage.get("total", 0) or 0

        # Extract reasoning tokens from usageDetails.
        # LangFuse REST API returns flat keys: "output_reasoning" and
        # "input_cache_read" (not nested under output_token_details).
        reasoning_tokens = 0
        reasoning_tokens = usage_details.get("output_reasoning", 0) or 0
        if not reasoning_tokens:
            # Fallback: nested format (some SDK versions)
            otd = usage_details.get("output_token_details") or usage_details.get("outputTokenDetails") or {}
            reasoning_tokens = otd.get("reasoning", 0) or 0
        if not reasoning_tokens:
            completion_details = obs.get("completionTokensDetails") or {}
            reasoning_tokens = completion_details.get("reasoning_tokens", 0) or 0

        # Cache tokens (tokens reused from previous context)
        cache_read = 0
        cache_read = usage_details.get("input_cache_read", 0) or 0
        if not cache_read:
            # Fallback: nested format
            itd = usage_details.get("input_token_details") or usage_details.get("inputTokenDetails") or {}
            cache_read = itd.get("cache_read", 0) or 0

        steps.append({
            "obs_id": obs.get("id", ""),
            "name": obs.get("name", ""),
            "model": obs.get("model", ""),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "reasoning_tokens": reasoning_tokens,
            "cache_read_tokens": cache_read,
            "start_time": _parse_dt(obs.get("startTime")),
        })

    # Sort by start_time to get chronological order
    steps.sort(key=lambda s: s["start_time"] or datetime.min.replace(tzinfo=UTC))

    if not steps:
        return {
            "step_count": 0,
            "peak_input_tokens": 0,
            "peak_output_tokens": 0,
            "total_tokens_consumed": 0,
            "total_reasoning_tokens": 0,
            "total_cache_read_tokens": 0,
            "context_utilization_pct": 0.0,
            "steps": [],
        }

    peak_input = max(s["input_tokens"] for s in steps)
    peak_output = max(s["output_tokens"] for s in steps)
    total_consumed = sum(s["total_tokens"] for s in steps)
    total_reasoning = sum(s["reasoning_tokens"] for s in steps)
    total_cache = sum(s["cache_read_tokens"] for s in steps)

    return {
        "step_count": len(steps),
        "peak_input_tokens": peak_input,
        "peak_output_tokens": peak_output,
        "total_tokens_consumed": total_consumed,
        "total_reasoning_tokens": total_reasoning,
        "total_cache_read_tokens": total_cache,
        "context_utilization_pct": round(peak_input / MODEL_CONTEXT_WINDOW * 100, 1),
        "steps": steps,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_summary(results: dict[str, list[dict]], detail: bool = False) -> None:
    """Print summary table grouped by agent type."""
    print(f"\n{'=' * 90}")
    print("  Context Window Utilization Analysis")
    print("  Model: GPT-5-mini (128K context window)")
    print(f"{'=' * 90}")

    header = (
        f"{'Agent Type':<24} {'Traces':>6} {'Avg Steps':>10} "
        f"{'Avg Peak':>10} {'Avg Util%':>10} {'Max Util%':>10} "
        f"{'Avg Reasoning%':>15}"
    )
    print(f"\n{header}")
    print("-" * len(header))

    all_traces: list[dict] = []

    for agent_name in AGENT_TRACE_NAMES:
        label = AGENT_TRACE_NAMES[agent_name]
        traces = results.get(agent_name, [])
        all_traces.extend(traces)

        if not traces:
            print(f"{label:<24} {'0':>6} {'-':>10} {'-':>10} {'-':>10} {'-':>10} {'-':>15}")
            continue

        n = len(traces)
        avg_steps = sum(t["step_count"] for t in traces) / n
        avg_peak = sum(t["peak_input_tokens"] for t in traces) / n
        avg_util = sum(t["context_utilization_pct"] for t in traces) / n
        max_util = max(t["context_utilization_pct"] for t in traces)

        # Reasoning as % of total output
        total_output = sum(
            sum(s["output_tokens"] for s in t["steps"]) for t in traces
        )
        total_reasoning = sum(t["total_reasoning_tokens"] for t in traces)
        reasoning_pct = (total_reasoning / total_output * 100) if total_output > 0 else 0

        print(
            f"{label:<24} {n:>6} {avg_steps:>10.1f} "
            f"{avg_peak:>10,.0f} {avg_util:>9.1f}% {max_util:>9.1f}% "
            f"{reasoning_pct:>14.1f}%"
        )

    # Grand summary
    if all_traces:
        print("-" * len(header))
        n = len(all_traces)
        avg_steps = sum(t["step_count"] for t in all_traces) / n
        avg_peak = sum(t["peak_input_tokens"] for t in all_traces) / n
        avg_util = sum(t["context_utilization_pct"] for t in all_traces) / n
        max_util = max(t["context_utilization_pct"] for t in all_traces)
        print(
            f"{'ALL':<24} {n:>6} {avg_steps:>10.1f} "
            f"{avg_peak:>10,.0f} {avg_util:>9.1f}% {max_util:>9.1f}%"
        )

    # Context growth analysis
    print(f"\n{'=' * 90}")
    print("  Context Growth Per Step (input_tokens progression)")
    print(f"{'=' * 90}")

    for agent_name in AGENT_TRACE_NAMES:
        label = AGENT_TRACE_NAMES[agent_name]
        traces = results.get(agent_name, [])
        if not traces:
            continue

        # Collect step-by-step input_tokens across all traces
        max_steps = max(t["step_count"] for t in traces)
        if max_steps == 0:
            continue

        print(f"\n  {label} (n={len(traces)}):")
        step_data: dict[int, list[int]] = defaultdict(list)
        for t in traces:
            for i, s in enumerate(t["steps"]):
                step_data[i].append(s["input_tokens"])

        for i in range(min(max_steps, 10)):  # cap at 10 steps
            vals = step_data.get(i, [])
            if vals:
                avg = sum(vals) / len(vals)
                mn, mx = min(vals), max(vals)
                util = avg / MODEL_CONTEXT_WINDOW * 100
                print(
                    f"    Step {i + 1}: avg {avg:>8,.0f} tokens "
                    f"(range {mn:,}-{mx:,}) = {util:.1f}% window"
                )

    # Cache efficiency
    print(f"\n{'=' * 90}")
    print("  Cache Efficiency (tokens reused from prior context)")
    print(f"{'=' * 90}\n")

    for agent_name in AGENT_TRACE_NAMES:
        label = AGENT_TRACE_NAMES[agent_name]
        traces = results.get(agent_name, [])
        if not traces:
            continue

        total_input = sum(
            sum(s["input_tokens"] for s in t["steps"]) for t in traces
        )
        total_cache = sum(t["total_cache_read_tokens"] for t in traces)
        cache_pct = (total_cache / total_input * 100) if total_input > 0 else 0
        print(f"  {label}: {cache_pct:.1f}% of input tokens served from cache")

    if detail:
        _print_detail(results)


def _print_detail(results: dict[str, list[dict]]) -> None:
    """Print per-trace detail."""
    print(f"\n{'=' * 90}")
    print("  Per-Trace Detail")
    print(f"{'=' * 90}")

    header = (
        f"{'Trace ID':<40} {'Type':<14} {'Steps':>5} "
        f"{'Peak Input':>11} {'Util%':>7} {'Latency':>8}"
    )
    print(f"\n{header}")
    print("-" * len(header))

    for agent_name in AGENT_TRACE_NAMES:
        label = AGENT_TRACE_NAMES[agent_name]
        traces = results.get(agent_name, [])
        for t in sorted(traces, key=lambda x: x.get("timestamp", "")):
            tid = t.get("trace_id", "")[:38]
            print(
                f"{tid:<40} {label:<14} {t['step_count']:>5} "
                f"{t['peak_input_tokens']:>11,} {t['context_utilization_pct']:>6.1f}% "
                f"{t.get('latency', 0):>7.1f}s"
            )


def write_csv(results: dict[str, list[dict]], path: str) -> None:
    """Write per-trace results to CSV."""
    rows = []
    for agent_name in AGENT_TRACE_NAMES:
        label = AGENT_TRACE_NAMES[agent_name]
        for t in results.get(agent_name, []):
            rows.append({
                "trace_id": t.get("trace_id", ""),
                "agent_type": agent_name,
                "agent_label": label,
                "timestamp": t.get("timestamp", ""),
                "step_count": t["step_count"],
                "peak_input_tokens": t["peak_input_tokens"],
                "peak_output_tokens": t["peak_output_tokens"],
                "total_tokens_consumed": t["total_tokens_consumed"],
                "total_reasoning_tokens": t["total_reasoning_tokens"],
                "total_cache_read_tokens": t["total_cache_read_tokens"],
                "context_utilization_pct": t["context_utilization_pct"],
                "latency_s": t.get("latency", 0),
            })

    if not rows:
        print("No data to write to CSV.")
        return

    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output.getvalue())
    print(f"\nCSV written to {path} ({len(rows)} rows)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze context window utilization from LangFuse traces"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Look back this many days (default: 14)",
    )
    parser.add_argument(
        "--detail",
        action="store_true",
        help="Show per-trace detail",
    )
    parser.add_argument(
        "--csv",
        nargs="?",
        const="data/context_utilization.csv",
        metavar="PATH",
        help="Write results to CSV (default: data/context_utilization.csv)",
    )
    # Security audit finding #39 (2026-04-20): scope to a single
    # environment / session-set to exclude smoke + dev + prior-campaign
    # traces.
    #
    # ext7 #47 (2026-04-20): default environment flipped from 'production' to
    # 'default' — thesis-load-bearing runners (run_pilot, run_longitudinal,
    # run_longitudinal_shift, CLI mint run) emit to environment='default' by
    # design per docs/langfuse_audit/langfuse_redundancy_audit_2026_04_19.md.
    # Pre-ext7-#47 default silently returned zero traces + exit 0 — T3 evidence
    # was invisibly empty.
    parser.add_argument(
        "--environment", default="default",
        help="LangFuse environment tag to fetch (default: 'default' — matches runner "
             "emission convention per docs/langfuse_audit/...).",
    )
    parser.add_argument(
        "--session-ids", nargs="+", default=None,
        help="Restrict to specific LangFuse session_ids (e.g. "
             "'eval_2026-04-20_full').",
    )
    parser.add_argument(
        "--allow-empty", action="store_true",
        help="ext7 #47: don't treat zero fetched traces as an error. Default: "
             "exit 2 if total fetched traces across all agents is zero, so a "
             "silently-empty Chapter 6 T3 section is loud instead of invisible.",
    )
    args = parser.parse_args()

    base_url, auth = _langfuse_client()

    # Check connectivity
    try:
        r = httpx.get(f"{base_url}/api/public/health", auth=auth, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"ERROR: LangFuse health check failed: {e}", file=sys.stderr)
        return 1

    print(f"Connected to LangFuse at {base_url}")

    end = datetime.now(UTC)
    start = end - timedelta(days=args.days)
    print(f"Window: {start.strftime('%Y-%m-%d %H:%M')} -> {end.strftime('%Y-%m-%d %H:%M')} UTC")

    results: dict[str, list[dict]] = {}

    with httpx.Client(base_url=base_url, auth=auth, timeout=30.0) as client:
        for agent_name, label in AGENT_TRACE_NAMES.items():
            print(f"\nFetching '{agent_name}' traces...", end=" ", flush=True)
            traces = fetch_traces(
                client, agent_name, start, end,
                environment=args.environment,
                session_ids=args.session_ids,
            )
            print(f"{len(traces)} found.", end=" ", flush=True)

            if not traces:
                results[agent_name] = []
                continue

            # Extract context metrics for each trace
            trace_results = []
            for i, trace in enumerate(traces):
                if (i + 1) % 5 == 0 or i == len(traces) - 1:
                    print(f"[{i + 1}/{len(traces)}]", end=" ", flush=True)

                metrics = extract_trace_context(client, trace)
                metrics["trace_id"] = trace["id"]
                metrics["timestamp"] = trace["timestamp"]
                metrics["latency"] = trace["latency"]
                trace_results.append(metrics)

            results[agent_name] = trace_results
            print("done.")

    # ext7 #47: loud-fail when no traces found — converts invisible-empty T3
    # section into an actionable error. Overridable via --allow-empty.
    total_traces = sum(len(tlist) for tlist in results.values())
    if total_traces == 0 and not args.allow_empty:
        print(
            f"\nERROR: zero traces found for any agent in environment={args.environment!r}, "
            f"window={args.days}d"
            + (f", session_ids={args.session_ids!r}" if args.session_ids else "")
            + ". Check: (a) runner emission convention (runners use environment='default'), "
            "(b) --session-ids filter, (c) --days window. Pass --allow-empty to suppress.",
            file=sys.stderr,
        )
        return 2

    print_summary(results, detail=args.detail)

    if args.csv:
        write_csv(results, args.csv)

    return 0


if __name__ == "__main__":
    sys.exit(main())
