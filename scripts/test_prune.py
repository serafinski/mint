"""Deterministic smoke test for the optimizer prune operation.

Seeds a temporary ChromaDB + in-memory DuckDB with three capabilities
and runs ``prune()``. Asserts the expected deactivations and event
records. No LLM calls, no Docker, no pipeline execution — just the
registry + metrics + prune mechanics.

Seeded state::

    cap A  "pinned-cap"       zero invocations, metadata.pinned=true
    cap B  "recently-used"    5 invocations in the last day
    cap C  "idle-orphan"      zero invocations, unpinned

Expected post-prune::

    cap A  active     (pinned — never pruned, regardless of invocations)
    cap B  active     (recent invocations — outside the window check)
    cap C  inactive   (zero invocations AND unpinned → deactivated)

Plus::

    MetricsStore.record_event("idle-orphan", "pruned", ...)

Usage::

    uv run scripts/test_prune.py

Exits 0 on all-pass, 1 on any failure. Runs in ~2 seconds, costs nothing.
"""

from __future__ import annotations

import logging
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.optimizer.operations import prune  # noqa: E402
from src.registry.metrics import MetricsStore  # noqa: E402
from src.registry.store import SkillRegistry  # noqa: E402
from src.schemas.capability import CapabilityDescriptor, CapabilityType  # noqa: E402
from src.schemas.metrics import InvocationRecord  # noqa: E402


def _seed_registry(registry: SkillRegistry) -> None:
    """Register the three canonical test capabilities."""
    registry.add(
        CapabilityDescriptor(
            name="pinned-cap",
            description="Core capability that must survive pruning regardless of invocation count",
            capability_type=CapabilityType.MCP_TOOL,
            domain="core",
            tags=["core"],
            metadata={"pinned": "true"},
        )
    )
    registry.add(
        CapabilityDescriptor(
            name="recently-used",
            description="Capability with recent invocations — should survive pruning",
            capability_type=CapabilityType.MCP_TOOL,
            domain="active",
            tags=["active"],
        )
    )
    registry.add(
        CapabilityDescriptor(
            name="idle-orphan",
            description="Capability with zero invocations and no pin — should be deactivated",
            capability_type=CapabilityType.MCP_TOOL,
            domain="idle",
            tags=["stale"],
        )
    )


def _seed_invocations(metrics: MetricsStore) -> None:
    """Only ``recently-used`` gets invocations; the other two have none."""
    now = datetime.now(UTC)
    for i in range(5):
        metrics.record_invocation(
            InvocationRecord(
                capability_id="recently-used",
                task_id=f"task-{i}",
                success=True,
                token_cost=100,
                latency_ms=50.0,
                invoked_at=now,
            )
        )


def run_prune_smoke() -> bool:
    """Seed, prune, assert. Returns True on all-pass."""
    print("=" * 70)
    print("  PRUNE SMOKE TEST")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp_dir:
        registry = SkillRegistry(
            persist_directory=tmp_dir,
            collection_name="test_prune_smoke",
        )
        metrics = MetricsStore()  # in-memory

        _seed_registry(registry)
        _seed_invocations(metrics)

        active_before = [c.name for c in registry.list_all(is_active=True)]
        print(f"\n  Active before: {sorted(active_before)}")
        assert set(active_before) == {"pinned-cap", "recently-used", "idle-orphan"}, (
            f"Expected 3 active caps, got {active_before}"
        )

        # --- Run prune ---
        print("\n  Running prune(window_days=30)...")
        pruned = prune(registry, metrics, window_days=30)
        print(f"  Pruned: {pruned}")

        # --- Assertions ---
        failed = False

        # Exactly one capability should have been pruned
        if pruned != ["idle-orphan"]:
            print(f"  FAIL: expected pruned == ['idle-orphan'], got {pruned}")
            failed = True

        # Registry state
        pinned = registry.get("pinned-cap")
        recent = registry.get("recently-used")
        orphan = registry.get("idle-orphan")

        if pinned is None or not pinned.is_active:
            print("  FAIL: 'pinned-cap' should be active (protected by metadata.pinned=true)")
            failed = True
        else:
            print("  OK: 'pinned-cap' survived (pinned)")

        if recent is None or not recent.is_active:
            print("  FAIL: 'recently-used' should be active (5 recent invocations)")
            failed = True
        else:
            print("  OK: 'recently-used' survived (recent invocations)")

        if orphan is None or orphan.is_active:
            print("  FAIL: 'idle-orphan' should be deactivated (zero invocations, unpinned)")
            failed = True
        else:
            print("  OK: 'idle-orphan' deactivated")

        # Event recording — exactly one 'pruned' event for idle-orphan
        events = metrics._conn.execute(
            "SELECT capability_id, event_type, details FROM capability_events "
            "WHERE event_type = 'pruned' ORDER BY occurred_at"
        ).fetchall()
        print(f"\n  Pruned events: {len(events)}")
        for cid, etype, details in events:
            print(f"    {cid}: {etype} — {details}")

        if len(events) != 1 or events[0][0] != "idle-orphan":
            print(f"  FAIL: expected exactly 1 'pruned' event for 'idle-orphan', got {events}")
            failed = True

        return not failed


def main() -> int:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.WARNING,
        stream=sys.stderr,
    )

    passed = run_prune_smoke()

    print("\n" + "=" * 70)
    print(f"  RESULT: {'PASS' if passed else 'FAIL'}")
    print("=" * 70 + "\n")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
