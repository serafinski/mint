"""Tier 2 behavioral probe-type breakdown (Chapter 4.3 methodology evidence).

Reads ``verification.tier_2.reject`` (and optionally ``.pass``) events from
LangFuse, extracts the ``categories`` metadata field, and counts how often
each Tier 2 probe type catches a failure in the eval. Answers the Chapter 4
question: "of the Tier 2 rejections observed, which probe types did the
catching?"

Proposal alignment: proposal §Pillar 2 line 133 names four Tier 2 probe
types — nonexistent entity, consistency, boundary, latency analysis.
Step 14.12 added a fifth (network-disconnect differential). This analyzer
empirically grounds the per-probe detection claim in Chapter 4.3.

Usage
-----
::

    # Default — last 7 days, pretty table
    uv run scripts/analyze_tier2_probes.py

    # Time window
    uv run scripts/analyze_tier2_probes.py --days 3

    # Include 'pass' events too (probe executed but found nothing)
    uv run scripts/analyze_tier2_probes.py --include-pass

    # CSV output
    uv run scripts/analyze_tier2_probes.py --csv
    uv run scripts/analyze_tier2_probes.py --csv --output-dir data/analysis

    # Filter by LangFuse environment (default: all)
    uv run scripts/analyze_tier2_probes.py --environment default

Requires ``LANGFUSE_PUBLIC_KEY``, ``LANGFUSE_SECRET_KEY``, ``LANGFUSE_HOST``
in ``.env``. No LLM calls; pure LangFuse REST API reads. Runs in ~10s–1min
depending on the time window.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Step 13.6 / Fix #1: route dev-script LangFuse emissions to test env.
os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", "test")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import httpx  # noqa: E402

_TIER2_EVENT_REJECT = "verification.tier_2.reject"
_TIER2_EVENT_PASS = "verification.tier_2.pass"

# Known Tier 2 probe categories — matched against the actual strings
# emitted by ``src/verification/behavioral.py`` (verified 2026-04-19 by
# grepping `category="` literals). Categories not in this set show up
# with a ⚠ marker in the output — typo guard against silent drift.
_KNOWN_CATEGORIES: set[str] = {
    # Proposal §Pillar 2 line 133 named probes
    "nonexistent_entity",
    "consistency",
    "boundary",
    "latency",  # proposal calls this "latency analysis"
    # Implementation additions (behavioral.py)
    "output_variation",
    "parameterless_smoke",
    "dependency",
    "timeout",
    "behavioral",  # generic catch-all
    # Step 14.12 additions (network plumbing)
    "network_silent_fallback",
    "network_disconnect_differential",
}

# Proposal §Pillar 2 line 133 enumerates four Tier 2 probe types by name.
# The implementation's `category=` literals don't always spell them
# identically — this map bridges the two for the coverage report.
_PROPOSAL_NAMED_CATEGORIES: dict[str, str] = {
    "nonexistent_entity": "nonexistent entity (proposal line 137)",
    "consistency": "consistency (proposal line 138)",
    "boundary": "boundary (proposal line 139)",
    "latency": "latency analysis (proposal line 140)",
}

# Step 14.12 Tier 2 extensions (proposal §Pillar 2 F2 silent fallbacks).
_STEP_14_12_CATEGORIES: dict[str, str] = {
    "network_silent_fallback": "silent-fallback probe (F2)",
    "network_disconnect_differential": "network-disconnect differential (F2)",
}


@dataclass
class ProbeBreakdown:
    """Aggregated per-category probe counts."""

    categories: Counter = field(default_factory=Counter)
    capability_types_by_category: dict[str, Counter] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    severity_by_category: dict[str, Counter] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    events_total: int = 0
    events_pass: int = 0
    events_reject: int = 0


# ---------------------------------------------------------------------------
# LangFuse REST client
# ---------------------------------------------------------------------------


def _langfuse_client() -> tuple[str, httpx.Auth]:
    host = os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    if not public_key or not secret_key:
        raise RuntimeError(
            "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY missing from env. "
            "Populate .env or export them."
        )
    return host, httpx.BasicAuth(public_key, secret_key)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fetch_tier2_events(
    client: httpx.Client,
    start: datetime,
    end: datetime,
    *,
    event_names: tuple[str, ...],
    environment: str | None,
    session_ids: list[str] | None = None,
) -> list[dict]:
    """Fetch LangFuse EVENT-type observations matching our Tier 2 names.

    Security audit finding #42 (2026-04-20): accepts ``session_ids`` to
    client-side-filter observations after the environment-scoped fetch.
    Concurrent production runs landing in the same time window would
    otherwise cross-contaminate.

    The REST API's ``/api/public/observations`` endpoint supports ``name`` +
    ``type`` filters; we iterate over event_names and paginate.
    """
    events: list[dict] = []
    for name in event_names:
        page = 1
        while True:
            params: dict[str, str | int] = {
                "name": name,
                "type": "EVENT",
                "limit": 100,
                "page": page,
            }
            if environment:
                params["environment"] = environment
            resp = client.get("/api/public/observations", params=params)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if not data:
                break
            for obs in data:
                ts = _parse_dt(obs.get("startTime") or obs.get("timestamp"))
                if ts and start <= ts <= end:
                    if session_ids:
                        # Session id may live on the observation or its
                        # parent trace; LangFuse puts it under "trace".
                        sid = (
                            (obs.get("trace") or {}).get("sessionId")
                            or obs.get("sessionId")
                        )
                        if sid not in session_ids:
                            continue
                    events.append(obs)
            if len(data) < 100:
                break
            page += 1
            if page > 200:
                break
    return events


# ---------------------------------------------------------------------------
# Breakdown aggregation
# ---------------------------------------------------------------------------


def aggregate(events: list[dict]) -> ProbeBreakdown:
    """Fold a list of tier-2 events into a ProbeBreakdown."""
    bd = ProbeBreakdown()

    for obs in events:
        bd.events_total += 1
        name = obs.get("name", "")
        is_reject = name == _TIER2_EVENT_REJECT
        if is_reject:
            bd.events_reject += 1
        elif name == _TIER2_EVENT_PASS:
            bd.events_pass += 1

        metadata = obs.get("metadata") or {}
        # LangFuse may wrap metadata in nested dict — probe common shapes.
        if isinstance(metadata, dict) and "metadata" in metadata and isinstance(
            metadata["metadata"], dict
        ):
            metadata = metadata["metadata"]

        cap_type = str(metadata.get("capability_type") or "unknown")
        categories = metadata.get("categories") or []
        severity_dist = metadata.get("severity_distribution") or {}

        if not isinstance(categories, list):
            continue

        for cat in categories:
            if not isinstance(cat, str):
                continue
            bd.categories[cat] += 1
            bd.capability_types_by_category[cat][cap_type] += 1

            # Attribute the severity distribution to each category in the
            # event. (LangFuse stores severity_distribution at the event
            # level, not per-finding, so this is an overcount on
            # multi-category events — reported as "severity hits" rather
            # than "per-category severity".)
            if isinstance(severity_dist, dict):
                for sev, count in severity_dist.items():
                    bd.severity_by_category[cat][sev] += int(count) if isinstance(
                        count, (int, float)
                    ) else 1

    return bd


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_table(bd: ProbeBreakdown, *, include_pass: bool) -> None:
    """Pretty-print the breakdown to stdout."""
    print("=" * 70)
    print("  TIER 2 BEHAVIORAL PROBE-TYPE BREAKDOWN")
    print("=" * 70)
    print(f"\nTotal tier-2 events:        {bd.events_total}")
    print(f"  verification.tier_2.reject  {bd.events_reject}")
    if include_pass:
        print(f"  verification.tier_2.pass    {bd.events_pass}")
    print()

    if not bd.categories:
        print(
            "No tier-2 findings observed in the time window. "
            "Expected sources:\n"
            "  - scripts/test_verification.py (smoke, small sample)\n"
            "  - scripts/run_pilot.py --all-conditions (Phase 4 eval, full)\n"
            "Check the --days window and --environment filter."
        )
        return

    total_findings = sum(bd.categories.values())
    print(f"Total finding-category hits: {total_findings}")
    print(
        f"(Each event lists the categories of its findings; an event can "
        f"contribute to multiple rows.)\n"
    )

    # Sort by count descending.
    sorted_cats = bd.categories.most_common()

    # Main table.
    print(
        f"  {'category':<22s} {'hits':>6s} {'pct':>7s}  "
        f"{'top capability_type':<40s}"
    )
    print(f"  {'-' * 22} {'-' * 6} {'-' * 7}  {'-' * 40}")
    for cat, count in sorted_cats:
        pct = count / total_findings
        cap_type_dist = bd.capability_types_by_category[cat].most_common(3)
        cap_type_str = ", ".join(f"{t}:{n}" for t, n in cap_type_dist)
        marker = "" if cat in _KNOWN_CATEGORIES else " ⚠ unknown category"
        print(
            f"  {cat:<22s} {count:>6d} {pct:>6.1%}  "
            f"{cap_type_str:<40s}{marker}"
        )

    # Proposal-named subset (§Pillar 2 line 133).
    print("\nProposal §Pillar 2 line 133 probe coverage (first four):")
    for cat, label in sorted(_PROPOSAL_NAMED_CATEGORIES.items()):
        count = bd.categories.get(cat, 0)
        marker = "✓" if count > 0 else "✗"
        print(f"  {marker} {cat:<36s} {count:>6d}  {label}")

    print("\nStep 14.12 additions (Tier 2 F2 silent-fallback probes):")
    for cat, label in sorted(_STEP_14_12_CATEGORIES.items()):
        count = bd.categories.get(cat, 0)
        marker = "✓" if count > 0 else "✗"
        print(f"  {marker} {cat:<36s} {count:>6d}  {label}")


def render_csv(bd: ProbeBreakdown, output_dir: Path) -> None:
    """Export one CSV per breakdown view under ``output_dir``."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Summary
    summary_path = output_dir / "tier2_probes_summary.csv"
    with summary_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["category", "hits", "pct"])
        total = sum(bd.categories.values())
        for cat, count in bd.categories.most_common():
            w.writerow([cat, count, f"{count / max(1, total):.4f}"])

    # Per-type breakdown
    typed_path = output_dir / "tier2_probes_by_type.csv"
    with typed_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["category", "capability_type", "hits"])
        for cat, by_type in bd.capability_types_by_category.items():
            for ctype, count in by_type.items():
                w.writerow([cat, ctype, count])

    print(f"\nCSV written to {summary_path}")
    print(f"CSV written to {typed_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--days", type=int, default=7, help="Look back this many days (default: 7)"
    )
    parser.add_argument(
        "--include-pass",
        action="store_true",
        help="Also fetch verification.tier_2.pass events",
    )
    parser.add_argument(
        "--csv", action="store_true", help="Write CSV output to --output-dir"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data" / "analysis",
        help="Directory for CSV output (default: data/analysis)",
    )
    parser.add_argument(
        "--environment",
        type=str,
        default=None,
        help=(
            "LangFuse environment filter (default: no filter — reads all). "
            "Pass 'default' for Phase 4 production eval data or 'test' for "
            "dev-script output."
        ),
    )
    # Security audit finding #42 (2026-04-20): session-id scoping to
    # avoid concurrent-run contamination in the same environment.
    parser.add_argument(
        "--session-ids", nargs="+", default=None,
        help="Restrict to specific LangFuse session_ids.",
    )
    args = parser.parse_args()

    try:
        host, auth = _langfuse_client()
    except RuntimeError as exc:
        print(f"FATAL: {exc}")
        return 1

    end = datetime.now(UTC)
    start = end - timedelta(days=args.days)

    event_names: tuple[str, ...] = (_TIER2_EVENT_REJECT,)
    if args.include_pass:
        event_names = (_TIER2_EVENT_REJECT, _TIER2_EVENT_PASS)

    env_note = f"environment={args.environment}" if args.environment else "no environment filter"
    print(
        f"Fetching Tier 2 events from {start.isoformat()} to "
        f"{end.isoformat()} ({env_note})..."
    )

    with httpx.Client(base_url=host, auth=auth, timeout=30.0) as client:
        events = fetch_tier2_events(
            client,
            start,
            end,
            event_names=event_names,
            environment=args.environment,
            session_ids=args.session_ids,
        )

    print(f"Fetched {len(events)} events.\n")

    bd = aggregate(events)
    render_table(bd, include_pass=args.include_pass)

    if args.csv:
        render_csv(bd, args.output_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
