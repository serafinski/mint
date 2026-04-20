"""ext8 #60 — severity-by-category overcount disclosure.

The aggregator stores severity hits per category for multi-category events.
Pre-fix the label was "per-category severity" which misleads readers into
summing across categories as a total finding count — overcounted because a
single event with N categories and K findings contributes K to each of the
N category buckets.

Fix: add a parallel ``severity_by_event`` view that counts each event once
and ensure the rendered table makes the overcount explicit.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.analyze_tier2_probes import aggregate  # noqa: E402


def test_severity_by_event_counts_each_event_once():
    """A 3-category event with {HIGH: 5} contributes 5 (total) to
    severity_by_event — NOT 15 (summed across the 3 categories).
    """
    events = [{
        "name": "verification.tier_2.reject",
        "metadata": {
            "categories": ["hardcoded", "silent_fallback", "contamination"],
            "severity_distribution": {"HIGH": 5, "MEDIUM": 2},
        },
    }]
    bd = aggregate(events)

    # Per-event view: exactly 5 HIGH + 2 MEDIUM (actual finding count).
    assert bd.severity_by_event["HIGH"] == 5
    assert bd.severity_by_event["MEDIUM"] == 2


def test_severity_by_category_overcounts_on_multi_category_events():
    """Documented behavior: severity HITS per category sum across categories
    EXCEED the actual finding count. This is what the per-category view
    shows — reader must not confuse it with the per-event total.
    """
    events = [{
        "name": "verification.tier_2.reject",
        "metadata": {
            "categories": ["hardcoded", "silent_fallback", "contamination"],
            "severity_distribution": {"HIGH": 5},
        },
    }]
    bd = aggregate(events)

    total_across_categories = sum(
        bd.severity_by_category[c]["HIGH"] for c in
        ["hardcoded", "silent_fallback", "contamination"]
    )
    assert total_across_categories == 15, (
        "severity_by_category is the distributional view (per-category HITS). "
        "Summing across 3 categories with 5 HIGH findings = 15 hits, not 5 "
        "events. The severity_by_event counter is the correct total."
    )
    # Per-event count is 5, not 15.
    assert bd.severity_by_event["HIGH"] == 5


def test_single_category_event_both_views_agree():
    """When every event has exactly one category, the two views agree."""
    events = [
        {
            "name": "verification.tier_2.reject",
            "metadata": {
                "categories": ["hardcoded"],
                "severity_distribution": {"HIGH": 3},
            },
        },
        {
            "name": "verification.tier_2.reject",
            "metadata": {
                "categories": ["silent_fallback"],
                "severity_distribution": {"CRITICAL": 1},
            },
        },
    ]
    bd = aggregate(events)
    assert bd.severity_by_event["HIGH"] == 3
    assert bd.severity_by_event["CRITICAL"] == 1
    # Per-category view sums to the same when categories don't overlap.
    assert (
        sum(bd.severity_by_category[c]["HIGH"] for c in bd.severity_by_category)
        == 3
    )


def test_render_table_labels_per_event_vs_per_category_distinctly(capsys):
    """Regression guard: the rendered output must label the two views so
    a reader can't mistake severity_by_category sums for the finding total.
    """
    import scripts.analyze_tier2_probes as atp

    events = [{
        "name": "verification.tier_2.reject",
        "metadata": {
            "categories": ["hardcoded", "silent_fallback"],
            "severity_distribution": {"HIGH": 3},
        },
    }]
    bd = aggregate(events)
    atp.render_table(bd, include_pass=False)
    out = capsys.readouterr().out

    # Per-event table present.
    assert "per-event counts" in out
    # Per-category HITS table present AND explicit about overcounting.
    assert "severity HITS" in out.lower() or "HITS by category" in out
    assert (
        "overcount" in out.lower() or
        "exceed" in out.lower() or
        "contribute to each category" in out.lower()
    )
