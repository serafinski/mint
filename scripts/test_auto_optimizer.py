"""Synthetic test for the CLI auto-optimizer feature.

Tests that the optimizer, when triggered via the pipeline's optimizer_node,
correctly executes all three operations (prune, split, merge) with seeded
invocation data.

This runs through the real pipeline path (create_pipeline with
run_optimizer=True), not create_optimization_loop() directly — verifying
the full wiring from pipeline to optimizer.

Usage::

    uv run scripts/test_auto_optimizer.py
    uv run scripts/test_auto_optimizer.py --verbose

No Docker needed (uses a simple task that matches a seeded capability).
Makes 2-4 LLM calls for split/merge (~$0.01). Takes ~30-60 seconds.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

logger = logging.getLogger(__name__)


def _seed_invocations(metrics, capability_id, task_ids, success_rate=1.0, n_per_task=1):
    """Seed synthetic invocations for a capability."""
    from src.schemas.metrics import InvocationRecord

    for tid in task_ids:
        for i in range(n_per_task):
            success = (i / n_per_task) < success_rate
            metrics.record_invocation(InvocationRecord(
                capability_id=capability_id,
                task_id=tid,
                success=success,
                token_cost=1000,
                latency_ms=100.0,
            ))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synthetic test for CLI auto-optimizer (prune + split + merge)"
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=level,
        stream=sys.stderr,
    )

    from src.orchestrator.pipeline import create_pipeline
    from src.orchestrator.tool_loader import ToolLoader
    from src.registry.metrics import MetricsStore
    from src.registry.store import SkillRegistry, seed_registry
    from src.schemas.capability import CapabilityDescriptor, CapabilityType
    from src.schemas.task import TaskState

    print("=" * 70)
    print("  AUTO-OPTIMIZER TEST")
    print("  Verifies: auto-enable + prune + split + merge via pipeline")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp_dir:
        registry = SkillRegistry(
            persist_directory=tmp_dir, collection_name="test_auto_opt"
        )
        seed_registry(registry)
        metrics = MetricsStore()  # in-memory

        # ==================================================================
        # Setup: register capabilities that will trigger each operation
        # ==================================================================

        # 1. PRUNE TARGET: unpinned, zero invocations
        registry.add(CapabilityDescriptor(
            name="unused-temp-tool",
            description="A temporary tool that nobody uses",
            capability_type=CapabilityType.MCP_TOOL,
            domain="test",
            tags=["test"],
        ))

        # 2. SPLIT TARGET: SKILL with high reuse, low success
        registry.add(CapabilityDescriptor(
            name="broad-data-processor",
            description=(
                "Process and transform any structured data file including CSV, "
                "JSON, XML, and YAML formats with validation and cleaning"
            ),
            capability_type=CapabilityType.SKILL,
            domain="data-processing",
            tags=["data", "transformation"],
        ))

        # 3. MERGE TARGETS: two similar low-reuse SKILLs
        registry.add(CapabilityDescriptor(
            name="csv-row-cleaner",
            description=(
                "Clean and validate rows in CSV data files by removing nulls, "
                "deduplicating records, and normalizing column types"
            ),
            capability_type=CapabilityType.SKILL,
            domain="data-processing",
            tags=["csv", "cleaning"],
        ))
        registry.add(CapabilityDescriptor(
            name="tsv-row-cleaner",
            description=(
                "Clean and validate rows in TSV data files by removing nulls, "
                "deduplicating records, and normalizing column types"
            ),
            capability_type=CapabilityType.SKILL,
            domain="data-processing",
            tags=["tsv", "cleaning"],
        ))

        initial_caps = {c.name for c in registry.list_all(is_active=True)}
        print(f"\n  Registry: {len(initial_caps)} active capabilities")

        # ==================================================================
        # Seed invocations to trigger thresholds
        # ==================================================================

        # Create 30 unique tasks for the reuse_rate denominator
        # (needs >25 so merge candidates hit reuse_rate < 0.05 strictly)
        all_tasks = [f"task_{i:02d}" for i in range(30)]

        # Filler: seeded capabilities get invocations across all tasks
        # so the task pool denominator for reuse_rate is large enough
        for cap_name in ["csv-parser", "json-validator", "text-statistics"]:
            _seed_invocations(metrics, cap_name, all_tasks, n_per_task=1)

        # PRUNE: unused-temp-tool gets ZERO invocations

        # SPLIT: broad-data-processor gets high reuse (20/25=80%), low success (50%)
        _seed_invocations(metrics, "broad-data-processor", all_tasks[:20],
                          success_rate=0.5, n_per_task=5)

        # MERGE: csv-row-cleaner and tsv-row-cleaner get low reuse (1/25=4% each)
        _seed_invocations(metrics, "csv-row-cleaner", ["task_00"],
                          success_rate=0.8, n_per_task=3)
        _seed_invocations(metrics, "tsv-row-cleaner", ["task_01"],
                          success_rate=0.8, n_per_task=3)

        total_invocations = metrics.invocation_count_total()
        print(f"  Invocations seeded: {total_invocations}")

        # Verify metrics
        print("\n  Metrics:")
        print(f"    unused-temp-tool: invocations={metrics.invocation_count('unused-temp-tool')}")
        rr = metrics.reuse_rate("broad-data-processor")
        sr = metrics.success_rate("broad-data-processor")
        print(f"    broad-data-processor: reuse={rr:.2f}, success={sr:.2f} (split: >0.6 AND <0.7)")
        print(f"    csv-row-cleaner: reuse={metrics.reuse_rate('csv-row-cleaner'):.3f} (merge: <0.05)")
        print(f"    tsv-row-cleaner: reuse={metrics.reuse_rate('tsv-row-cleaner'):.3f} (merge: <0.05)")

        # Check similarity for merge candidates
        results = registry.search("csv-row-cleaner", n_results=5)
        sim = None
        for cap, score in results:
            if cap.name == "tsv-row-cleaner":
                sim = score
                break
        print(f"    csv↔tsv similarity: {sim:.3f}" if sim else "    csv↔tsv similarity: NOT FOUND")

        # ==================================================================
        # Run pipeline with optimizer (through the real pipeline path)
        # ==================================================================

        print(f"\n  Auto-enable check: {total_invocations} >= 20 = {total_invocations >= 20}")

        print("\n  Running pipeline with run_optimizer=True...", flush=True)
        loader = ToolLoader()
        pipeline = create_pipeline(
            registry, loader,
            run_optimizer=True,
            metrics_store=metrics,
        ).compile()

        state = TaskState(
            task_description="Parse a standard CSV file and return row count"
        )
        try:
            result = pipeline.invoke(state)
        except Exception as exc:
            print(f"\n  FAIL: Pipeline raised: {exc}")
            return 1

        print("  Pipeline completed.")
        loader.cleanup()

        # ==================================================================
        # Verify all three operations triggered
        # ==================================================================

        events = metrics._conn.execute(
            "SELECT capability_id, event_type, details "
            "FROM capability_events ORDER BY occurred_at"
        ).fetchall()

        print(f"\n  Events recorded: {len(events)}")
        for cap_id, event_type, details in events:
            print(f"    {cap_id}: {event_type} — {details}")

        all_pass = True

        # --- PRUNE ---
        pruned = [e for e in events if e[1] == "pruned"]
        print(f"\n  PRUNE: {len(pruned)} capability(ies) pruned")
        if any(e[0] == "unused-temp-tool" for e in pruned):
            cap = registry.get("unused-temp-tool")
            print(f"    unused-temp-tool: pruned, is_active={cap.is_active if cap else 'NOT FOUND'}")
            if cap and cap.is_active:
                print("    FAIL: Should be deactivated")
                all_pass = False
            else:
                print("    OK")
        else:
            print("    FAIL: unused-temp-tool should have been pruned")
            all_pass = False

        # Verify pinned caps survived
        for name in ["csv-parser", "json-validator", "text-statistics"]:
            cap = registry.get(name)
            if not cap or not cap.is_active:
                print(f"    FAIL: Pinned '{name}' was pruned or missing")
                all_pass = False
        print("    Pinned capabilities: all survived")

        # --- SPLIT ---
        split_events = [e for e in events if e[1] == "split"]
        print(f"\n  SPLIT: {len(split_events)} capability(ies) split")
        if any(e[0] == "broad-data-processor" for e in split_events):
            parent = registry.get("broad-data-processor")
            print(f"    broad-data-processor: split, is_active={parent.is_active if parent else 'NOT FOUND'}")
            if parent and parent.is_active:
                print("    FAIL: Parent should be deactivated")
                all_pass = False

            # Find the created variants
            created = [e for e in events if e[1] == "created" and "split from broad-data-processor" in (e[2] or "")]
            print(f"    Variants created: {len(created)}")
            for cap_id, _, details in created:
                variant = registry.get(cap_id)
                active = variant.is_active if variant else "NOT FOUND"
                print(f"      {cap_id}: is_active={active}")
            if len(created) >= 2:
                print("    OK")
            else:
                print(f"    FAIL: Expected >= 2 variants, got {len(created)}")
                all_pass = False
        else:
            print("    FAIL: broad-data-processor should have been split")
            all_pass = False

        # --- MERGE ---
        merge_events = [e for e in events if e[1] == "merged"]
        print(f"\n  MERGE: {len(merge_events)} capability(ies) merged")
        if merge_events:
            for cap_id, _, details in merge_events:
                parent = registry.get(cap_id)
                print(f"    {cap_id}: merged into {details}, is_active={parent.is_active if parent else 'NOT FOUND'}")

            # Find the created merged capability
            merge_created = [e for e in events if e[1] == "created" and "merged from" in (e[2] or "")]
            if merge_created:
                merged_name = merge_created[0][0]
                merged = registry.get(merged_name)
                print(f"    Merged result: {merged_name}, is_active={merged.is_active if merged else 'NOT FOUND'}")
                print("    OK")
            else:
                print("    FAIL: No merged capability created")
                all_pass = False
        else:
            print("    FAIL: csv-row-cleaner + tsv-row-cleaner should have been merged")
            all_pass = False

        # --- Final registry state ---
        final_active = [c.name for c in registry.list_all(is_active=True)]
        final_inactive = [c.name for c in registry.list_all() if not c.is_active]
        print(f"\n  Final registry: {len(final_active)} active, {len(final_inactive)} inactive")
        for name in final_active:
            print(f"    [ACTIVE] {name}")
        for name in final_inactive:
            print(f"    [INACTIVE] {name}")

    # Summary
    print(f"\n{'=' * 70}")
    if all_pass:
        print("  PASS: All three optimizer operations triggered via pipeline")
    else:
        print("  FAIL: One or more operations did not trigger")
    print("=" * 70)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
