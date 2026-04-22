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
import time
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

# ext16 #183 (2026-04-22): per-model context-window lookup. Pre-fix, the
# single 128k constant was applied to every trace regardless of model,
# so Row 13 percentages were silently wrong under JIT_LLM_MODEL override
# (e.g., gpt-5 200k, claude-sonnet-4-6 200k, claude-opus-4-7-1m 1M).
MODEL_CONTEXT_WINDOW = 128_000
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-5-mini": 128_000,
    "gpt-5": 200_000,
    "gpt-4o-mini": 128_000,
    "gpt-4o": 128_000,
    "claude-opus-4-7": 200_000,
    "claude-opus-4-7-1m": 1_000_000,
    "claude-sonnet-4-6": 200_000,
    "claude-sonnet-4-5": 200_000,
    "claude-haiku-4-5": 200_000,
}
_UNKNOWN_MODELS_WARNED: set[str] = set()


def _context_window_for(model: str | None) -> int:
    """ext16 #183: resolve a model string to its context window.

    Strips provider prefix (``openai:``, ``anthropic:``). Returns the
    default 128k on unknown model with a deduplicated stderr warning.
    """
    import sys as _sys
    if not model:
        return MODEL_CONTEXT_WINDOW
    key = model.split(":", 1)[1] if ":" in model else model
    if key in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[key]
    if key not in _UNKNOWN_MODELS_WARNED:
        _UNKNOWN_MODELS_WARNED.add(key)
        print(
            f"WARNING: unknown model '{model}' — defaulting context window "
            f"to {MODEL_CONTEXT_WINDOW:,} (ext16 #183). Add the model to "
            f"MODEL_CONTEXT_WINDOWS to silence.",
            file=_sys.stderr,
        )
    return MODEL_CONTEXT_WINDOW

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
    *,
    max_pages: int = 200,
) -> tuple[list[dict], set[str], bool]:
    """Fetch all traces with a given name within the time window.

    Security audit finding #39 (2026-04-20): accepts ``environment`` and
    ``session_ids`` to scope fetches to a single campaign. Pre-fix, any
    trace in the time window with a matching name was returned,
    including smoke-test / dev runs and prior campaigns.

    ext14 #139 (2026-04-21): returns the set of sessionIds that actually
    contributed ≥1 row so ``main()`` can verify coverage vs. the
    ``--session-ids`` request. Pre-fix, typo / retention-aged / partial
    sessions silently dropped at line 114 with no caller-visible signal.

    ext14 #140 (2026-04-21): returns a ``truncated`` bool so ``main()``
    can refuse aggregates when the hard pagination ceiling was hit.
    Pre-fix, the bare ``break`` at page > 200 silently dropped every row
    past the 20k ceiling.
    """
    import logging as _lg

    traces: list[dict] = []
    contributed_sessions: set[str] = set()
    truncated = False
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
                sid = t.get("sessionId")
                if session_ids and sid not in session_ids:
                    continue
                if sid is not None:
                    contributed_sessions.add(sid)
                traces.append({
                    "id": t["id"],
                    "name": t.get("name", ""),
                    "timestamp": ts,
                    "session_id": sid,
                    "latency": t.get("latency", 0) or 0,
                    "total_cost": t.get("totalCost", 0) or 0,
                    "observation_ids": t.get("observations", []),
                    "metadata": t.get("metadata") or {},
                })

        if len(data) < 100:
            break
        page += 1
        if page > max_pages:
            _lg.getLogger(__name__).warning(
                "ext14 #140: fetch_traces hit pagination ceiling at page %d "
                "(max_pages=%d, ~%d records fetched). Data beyond the ceiling "
                "was silently dropped.",
                page, max_pages, len(traces),
            )
            truncated = True
            break

    return traces, contributed_sessions, truncated


def fetch_observation(
    client: httpx.Client,
    obs_id: str,
    *,
    max_retries: int = 3,
) -> dict | None:
    """Fetch a single observation by ID.

    ext12 #120 (2026-04-21): pre-fix was a blanket ``except Exception:
    return None`` with no retry. Transient LangFuse failures (rate-limit
    throttling during a heavy read window, intermittent network egress
    loss, token-auth expiration partway through the loop) got silently
    swallowed. When every observation fetch for a given trace failed,
    ``extract_trace_context`` then returned a zero-valued metrics dict
    indistinguishable from a trace that genuinely had no GENERATION
    observations — and the downstream aggregator pooled those phantom
    zeros into Context Economy averages, silently deflating Chapter 6
    numbers toward 0.

    Now:
    - Retry with exponential backoff on ``HTTPError`` / transient
      status codes (anything other than 404/410 which mean "gone for
      real"). ``max_retries=3`` absorbs the common ~50%-rate-limit
      window without drowning the script.
    - Callers can then distinguish "obs gone" (404 → ``None``) from
      "every obs fetch failed transiently" by counting the None
      returns across a full trace.
    """
    for attempt in range(max_retries):
        try:
            resp = client.get(f"/api/public/observations/{obs_id}")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            # 404/410 mean the observation is genuinely gone — don't retry.
            if exc.response.status_code in (404, 410):
                return None
            if attempt < max_retries - 1:
                time.sleep(0.5 * (2 ** attempt))
                continue
            return None
        except httpx.HTTPError:
            if attempt < max_retries - 1:
                time.sleep(0.5 * (2 ** attempt))
                continue
            return None
        except Exception:
            # Non-HTTP failures (JSON decode, etc.) are unlikely to
            # self-heal — fail fast with a retry window anyway.
            if attempt < max_retries - 1:
                time.sleep(0.5 * (2 ** attempt))
                continue
            return None
    return None


def extract_trace_context(client: httpx.Client, trace: dict) -> dict:
    """Extract context utilization metrics from a trace's observations.

    Returns a dict with step_count, peak_input_tokens, peak_output_tokens,
    total_tokens, reasoning_tokens, and per-step token progression.

    ext12 #120 (2026-04-21): tracks ``_obs_attempted`` / ``_obs_failed``
    / ``_fetch_degraded`` so the aggregate pass can filter out traces
    whose every observation fetch failed. Pre-fix, all-fetches-failed
    collapsed to a zero-valued metrics dict that looked identical to
    "this trace had zero GENERATION observations"; the downstream
    averager pooled the phantom zeros and deflated Chapter 6's Context
    Economy percentages toward 0.
    """
    steps: list[dict] = []
    n_attempted = 0
    n_failed = 0

    for obs_id in trace["observation_ids"]:
        n_attempted += 1
        obs = fetch_observation(client, obs_id)
        if obs is None:
            n_failed += 1
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
        # ext12 #120: distinguish "every observation fetch failed" from
        # "trace had observations but none were GENERATION-typed" —
        # without the flag, downstream aggregation pools both cases as
        # zero-util and deflates Chapter 6's Context Economy headline.
        #
        # ext16 #184 (2026-04-22): empty ``observation_ids`` list
        # (n_attempted == 0) also flagged as degraded. Pre-fix, the
        # condition required ``n_attempted > 0``, so an ingest-incomplete
        # envelope (no observations fetched at all) entered aggregates as
        # a legitimate zero-util data point and silently biased Chapter 6
        # Context Economy downward.
        fetch_degraded = (n_attempted == 0) or (
            n_attempted > 0 and n_failed == n_attempted
        )
        return {
            "step_count": 0,
            "peak_input_tokens": 0,
            "peak_output_tokens": 0,
            "total_chain_input_tokens": 0,
            "total_chain_output_tokens": 0,
            "total_tokens_consumed": 0,
            "total_reasoning_tokens": 0,
            "total_cache_read_tokens": 0,
            "context_utilization_pct": 0.0,
            "context_utilization_chain_pct": 0.0,
            "_fetch_degraded": fetch_degraded,
            "_empty_observation_ids": n_attempted == 0,  # ext16 #184
            "_fetch_partial": False,
            "_fetch_coverage_pct": 0.0,
            "_obs_attempted": n_attempted,
            "_obs_failed": n_failed,
            "steps": [],
        }

    peak_input = max(s["input_tokens"] for s in steps)
    peak_output = max(s["output_tokens"] for s in steps)
    # Security audit finding #94 (2026-04-19, ext9): Pillar 3's Context
    # Economy tension is about TOTAL prompt-token cost across the whole
    # MAS per task, not any single agent's peak window utilisation. A
    # chain of 5 agents at 20K tokens each consumes 100K chain-tokens;
    # per-agent peak alone reports 20K, a 5x under-report for the
    # proposal's "reduce prompt bloat" claim. Report BOTH — per-agent
    # peak (for "does any agent exceed the context window?") AND the
    # chain cumulative (for "total MAS cost per task").
    total_chain_input = sum(s["input_tokens"] for s in steps)
    total_chain_output = sum(s["output_tokens"] for s in steps)
    total_consumed = sum(s["total_tokens"] for s in steps)
    total_reasoning = sum(s["reasoning_tokens"] for s in steps)
    total_cache = sum(s["cache_read_tokens"] for s in steps)

    # ext16 #183 (2026-04-22): resolve per-step context window so
    # utilization percentages are correct under mixed-model traces /
    # JIT_LLM_MODEL override. Peak window = max across steps.
    step_windows = [_context_window_for(s.get("model")) for s in steps]
    peak_window = max(step_windows) if step_windows else MODEL_CONTEXT_WINDOW

    # ext16 #179 (2026-04-22): partial-fetch coverage. Pre-fix, traces
    # with ``n_failed > 0 < n_attempted`` were treated as complete;
    # aggregates (peak = max, total = sum) silently undercounted.
    fetch_coverage = 1.0 - (n_failed / n_attempted) if n_attempted > 0 else 1.0

    return {
        "step_count": len(steps),
        "peak_input_tokens": peak_input,
        "peak_output_tokens": peak_output,
        "total_chain_input_tokens": total_chain_input,
        "total_chain_output_tokens": total_chain_output,
        "total_tokens_consumed": total_consumed,
        "total_reasoning_tokens": total_reasoning,
        "total_cache_read_tokens": total_cache,
        "context_utilization_pct": round(peak_input / peak_window * 100, 1),
        "context_utilization_chain_pct": round(
            total_chain_input / peak_window * 100, 1
        ),
        "_context_window_used": peak_window,  # ext16 #183
        # ext12 #120: a trace with usable GENERATION observations is never
        # fetch-degraded (some fetches may have failed, but enough landed
        # to produce real metrics — aggregating these is correct).
        "_fetch_degraded": False,
        "_empty_observation_ids": False,  # ext16 #184
        # ext16 #179: distinguish partial from complete.
        "_fetch_partial": n_failed > 0,
        "_fetch_coverage_pct": round(fetch_coverage * 100, 1),
        "_obs_attempted": n_attempted,
        "_obs_failed": n_failed,
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
    # ext14 #139 (2026-04-21): per-requested-session coverage gate.
    parser.add_argument(
        "--allow-partial-sessions", action="store_true",
        help="ext14 #139: proceed when some --session-ids contributed zero "
             "rows. Default: refuse with exit 4. Chapter 4.5 disclosure "
             "REQUIRED.",
    )
    # ext14 #140 (2026-04-21): pagination truncation signal.
    parser.add_argument(
        "--max-pages", type=int, default=200,
        help="ext14 #140: LangFuse pagination ceiling (default: 200 = 20k "
             "records at 100/page). Raise for larger campaigns; expect longer "
             "fetch and higher API cost.",
    )
    parser.add_argument(
        "--allow-truncated", action="store_true",
        help="ext14 #140: proceed when the pagination ceiling was hit. "
             "Default: refuse with exit 5. Chapter 4.5 disclosure REQUIRED.",
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
    # ext14 #139: which sessionIds contributed ≥1 row (union across agents).
    contributed_sessions_total: set[str] = set()
    # ext14 #140: whether any per-agent fetch hit the pagination ceiling.
    any_truncated = False

    with httpx.Client(base_url=base_url, auth=auth, timeout=30.0) as client:
        for agent_name, label in AGENT_TRACE_NAMES.items():
            print(f"\nFetching '{agent_name}' traces...", end=" ", flush=True)
            traces, contributed, truncated = fetch_traces(
                client, agent_name, start, end,
                environment=args.environment,
                session_ids=args.session_ids,
                max_pages=args.max_pages,
            )
            contributed_sessions_total |= contributed
            if truncated:
                any_truncated = True
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

    # ext14 #140 (2026-04-21): pagination-truncation gate. Refuse to publish
    # aggregates computed from a silently-truncated fetch unless the operator
    # explicitly opts in with --allow-truncated (Chapter 4.5 disclosure).
    if any_truncated and not args.allow_truncated:
        print(
            f"\nERROR: fetcher hit the {args.max_pages}-page ceiling "
            f"(~{args.max_pages * 100:,} records). Data beyond the ceiling "
            f"was silently dropped. Options:\n"
            f"  (a) narrow --days window\n"
            f"  (b) narrow --session-ids to the specific campaign\n"
            f"  (c) raise --max-pages (tradeoff: longer fetch time + higher "
            f"LangFuse API cost)\n"
            f"  (d) pass --allow-truncated to proceed with undersampled "
            f"aggregates (Chapter 4.5 disclosure REQUIRED). ext14 #140. Exit 5.",
            file=sys.stderr,
        )
        return 5

    # ext14 #139 (2026-04-21): per-requested-session coverage gate. Typo /
    # retention-aged / partial-ingest session IDs silently drop at the
    # per-trace filter; verify every requested ID actually contributed.
    if args.session_ids:
        requested = set(args.session_ids)
        missing = requested - contributed_sessions_total
        if missing and not args.allow_partial_sessions:
            print(
                f"\nERROR: {len(missing)}/{len(requested)} requested session "
                f"IDs contributed zero rows: {sorted(missing)}\n"
                f"  Likely causes: (a) typo in --session-ids, (b) LangFuse "
                f"retention aged out the session's traces, (c) partial "
                f"ingestion mid-campaign.\n"
                f"  Pass --allow-partial-sessions to proceed with the "
                f"surviving subset (Chapter 4.5 disclosure REQUIRED). "
                f"ext14 #139. Exit 4.",
                file=sys.stderr,
            )
            return 4

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

    # ext12 #120: filter out traces whose every observation fetch failed.
    # Pre-fix those landed in aggregates as phantom-zero rows, silently
    # deflating Chapter 6's Context Economy averages proportional to
    # LangFuse transient-failure rate during the analysis window. Now
    # they're excluded from ALL downstream averages with a loud per-agent
    # WARNING count. If every trace for every agent turned out degraded
    # (catastrophic LangFuse outage during the analysis window) and the
    # user hasn't passed --allow-empty, exit 3.
    total_degraded = 0
    total_valid = 0
    for agent_name, trace_list in results.items():
        degraded = [t for t in trace_list if t.get("_fetch_degraded")]
        valid = [t for t in trace_list if not t.get("_fetch_degraded")]
        if degraded:
            print(
                f"WARNING: {len(degraded)}/{len(trace_list)} {agent_name!r} "
                f"traces had ALL observation fetches fail (likely LangFuse "
                f"transient). Excluded from Context Economy averages.",
                file=sys.stderr,
            )
        results[agent_name] = valid
        total_degraded += len(degraded)
        total_valid += len(valid)
    if total_valid == 0 and total_degraded > 0 and not args.allow_empty:
        print(
            f"\nERROR: {total_degraded} traces fetched but ALL were "
            f"fetch-degraded (every observation fetch failed). No valid "
            f"data for Context Economy averages. Retry when LangFuse is "
            f"healthy, or pass --allow-empty to proceed with no data.",
            file=sys.stderr,
        )
        return 3

    # ext13 #133: per-agent-family presence gate. ext12 #120's fix filters
    # degraded traces but doesn't require every expected agent family to
    # have at least one valid trace. When e.g. task-decomposer has data
    # but adversarial-prober / skill-executor / agent-executor are all
    # empty, the per-agent comparison table prints dashes and the script
    # exited 0 silently. Now: refuse with exit 4 unless --allow-empty is
    # passed. The existing --allow-empty flag covers this case too.
    missing_agents = [
        name for name in AGENT_TRACE_NAMES
        if not results.get(name)
    ]
    if missing_agents and not args.allow_empty:
        print(
            f"\nERROR: {len(missing_agents)}/{len(AGENT_TRACE_NAMES)} "
            f"agent families have zero valid traces: {missing_agents}. "
            f"Chapter 6 Context Economy requires per-agent comparison — "
            f"dashed rows in the summary cannot substitute for real data. "
            f"Pass --allow-empty to proceed with missing rows (Chapter 4.5 "
            f"disclosure REQUIRED). ext13 #133. Exit 4.",
            file=sys.stderr,
        )
        return 4

    # ext14 #139 / #140 (2026-04-21): in-band banners when overrides are used.
    # Chapter 4.5 disclosure REQUIRED for any results that carry these banners.
    banners: list[str] = []
    if any_truncated and args.allow_truncated:
        banners.append(
            f"[TRUNCATED: {args.max_pages * 100:,}+ records — aggregates "
            f"under-sample; Chapter 4.5 disclosure REQUIRED]"
        )
    if args.session_ids:
        requested = set(args.session_ids)
        missing = requested - contributed_sessions_total
        if missing and args.allow_partial_sessions:
            banners.append(
                f"[PARTIAL SESSIONS: "
                f"{len(requested) - len(missing)}/{len(requested)} contributed; "
                f"missing={sorted(missing)}; Chapter 4.5 disclosure REQUIRED]"
            )
    if banners:
        print()
        for b in banners:
            print(f"  ⚠ {b}", file=sys.stderr)

    print_summary(results, detail=args.detail)

    if args.csv:
        write_csv(results, args.csv)

    return 0


if __name__ == "__main__":
    sys.exit(main())
