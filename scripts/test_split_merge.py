"""Integration test for optimizer split and merge operations.

Seeds a fresh ChromaDB registry and MetricsStore with synthetic invocations
that satisfy the split/merge thresholds, then runs the operations with real
LLM calls to verify end-to-end behavior.

Split trigger:  reuse_rate > 0.6 AND success_rate < 0.7 (SKILL or AGENT)
Merge trigger:  reuse_rate < 0.05 AND semantic similarity >= 0.5 (SKILL only)

Usage::

    # Run both split and merge tests
    uv run scripts/test_split_merge.py

    # Run only split
    uv run scripts/test_split_merge.py --split-only

    # Run only merge
    uv run scripts/test_split_merge.py --merge-only

    # Verbose logging
    uv run scripts/test_split_merge.py --verbose

Makes 2-4 LLM calls (~$0.01). No pipeline execution, no Docker.
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

from src.optimizer.operations import merge, split  # noqa: E402
from src.registry.metrics import MetricsStore  # noqa: E402
from src.registry.store import SkillRegistry  # noqa: E402
from src.schemas.capability import CapabilityDescriptor, CapabilityType  # noqa: E402
from src.schemas.metrics import InvocationRecord  # noqa: E402

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_fresh_registry(tmp_dir: str) -> SkillRegistry:
    """Create a fresh ChromaDB registry in a temp directory."""
    return SkillRegistry(
        persist_directory=tmp_dir,
        collection_name="test_split_merge",
    )


def _seed_invocations(
    metrics: MetricsStore,
    capability_id: str,
    task_ids: list[str],
    success_rate: float,
    n_per_task: int = 5,
) -> int:
    """Seed synthetic invocations for a capability across multiple tasks.

    Returns the total number of invocations seeded.
    """
    total = 0
    for task_id in task_ids:
        for i in range(n_per_task):
            success = (i / n_per_task) < success_rate
            record = InvocationRecord(
                capability_id=capability_id,
                task_id=task_id,
                success=success,
                token_cost=5000,
                latency_ms=1000.0,
            )
            metrics.record_invocation(record)
            total += 1
    return total


# ---------------------------------------------------------------------------
# Split test
# ---------------------------------------------------------------------------


def test_split(verbose: bool = False) -> bool:
    """Test the split operation end-to-end.

    Scenario: A SKILL called "data-file-processor" is used across many task
    types (high reuse) but fails often (low success). The optimizer should
    split it into 2+ specialized variants.

    Thresholds: reuse_rate > 0.6, success_rate < 0.7
    """
    print("\n" + "=" * 70)
    print("  SPLIT TEST")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp_dir:
        registry = _create_fresh_registry(tmp_dir)
        metrics = MetricsStore()  # in-memory

        # Register the split candidate (SKILL type — MCP_TOOL can't be split)
        cap = CapabilityDescriptor(
            name="data-file-processor",
            description=(
                "Process and transform structured data files including CSV, "
                "JSON, and XML formats. Handles parsing, validation, cleaning, "
                "and format conversion."
            ),
            capability_type=CapabilityType.SKILL,
            domain="data-processing",
            tags=["data", "parsing", "transformation"],
        )
        registry.add(cap)
        print(f"\n  Registered: {cap.name} ({cap.capability_type.value})")
        print(f"  Description: {cap.description[:80]}...")

        # Seed invocations to satisfy split thresholds:
        # - reuse_rate > 0.6: capability must appear across 60%+ of unique task types
        # - success_rate < 0.7: less than 70% of invocations succeed
        #
        # We create 10 unique task types. The capability is invoked across 8 of them
        # (reuse_rate = 8/10 = 0.8). Each task has 5 invocations with 50% success.
        all_task_ids = [f"TASK_{i:02d}" for i in range(10)]
        used_task_ids = all_task_ids[:8]  # 8/10 = 80% reuse

        # Also seed a few invocations for other capabilities so the window
        # includes all 10 task types
        dummy_cap = CapabilityDescriptor(
            name="other-tool",
            description="A different tool",
            capability_type=CapabilityType.MCP_TOOL,
        )
        registry.add(dummy_cap)
        _seed_invocations(metrics, "other-tool", all_task_ids, success_rate=1.0, n_per_task=1)

        n_seeded = _seed_invocations(
            metrics, "data-file-processor", used_task_ids,
            success_rate=0.5, n_per_task=5,
        )

        # Verify metrics
        reuse = metrics.reuse_rate("data-file-processor")
        success = metrics.success_rate("data-file-processor")
        print(f"\n  Seeded {n_seeded} invocations across {len(used_task_ids)} task types")
        print(f"  reuse_rate  = {reuse:.2f} (threshold: > 0.6)")
        print(f"  success_rate = {success:.2f} (threshold: < 0.7)")

        if reuse <= 0.6 or success >= 0.7:
            print("\n  ERROR: Metrics do not satisfy split thresholds!")
            return False

        # Run split — this makes 1-2 LLM calls
        print("\n  Running split(...)...", flush=True)
        try:
            new_ids = split(registry, metrics, "data-file-processor")
        except Exception as exc:
            print(f"\n  FAIL: split() raised: {exc}")
            return False

        # Verify results
        print(f"\n  Results:")
        print(f"  New variants: {new_ids}")

        if len(new_ids) < 2:
            print(f"  FAIL: Expected >= 2 variants, got {len(new_ids)}")
            return False

        # Check parent is deactivated
        parent = registry.get("data-file-processor")
        parent_active = parent.is_active if parent else "NOT FOUND"
        print(f"  Parent 'data-file-processor' is_active: {parent_active}")

        if parent and parent.is_active:
            print("  FAIL: Parent should be deactivated after split")
            return False

        # Check variants are registered and active
        for vid in new_ids:
            variant = registry.get(vid)
            if variant is None:
                print(f"  FAIL: Variant '{vid}' not found in registry")
                return False
            print(f"  Variant '{vid}': {variant.description[:60]}...")
            print(f"    type={variant.capability_type.value}, active={variant.is_active}")

        # Check events were recorded
        events = metrics._conn.execute(
            "SELECT capability_id, event_type, details FROM capability_events "
            "ORDER BY occurred_at"
        ).fetchall()
        print(f"\n  Events recorded: {len(events)}")
        for eid, etype, details in events:
            print(f"    {eid}: {etype} — {details}")

        print("\n  PASS: Split operation completed successfully")
        return True


# ---------------------------------------------------------------------------
# Merge test
# ---------------------------------------------------------------------------


def test_merge(verbose: bool = False) -> bool:
    """Test the merge operation end-to-end.

    Scenario: Two similar SKILL capabilities ("csv-data-cleaner" and
    "tsv-data-cleaner") both have low reuse and overlap semantically.
    The optimizer should merge them into a single broader capability.

    Thresholds: reuse_rate < 0.05, semantic similarity >= 0.5
    """
    print("\n" + "=" * 70)
    print("  MERGE TEST")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp_dir:
        registry = _create_fresh_registry(tmp_dir)
        metrics = MetricsStore()  # in-memory

        # Register two similar SKILL capabilities
        cap_a = CapabilityDescriptor(
            name="csv-data-cleaner",
            description=(
                "Clean and validate CSV data files by removing null rows, "
                "deduplicating records, and normalizing column types."
            ),
            capability_type=CapabilityType.SKILL,
            domain="data-processing",
            tags=["csv", "cleaning", "validation"],
        )
        cap_b = CapabilityDescriptor(
            name="tsv-data-cleaner",
            description=(
                "Clean and validate TSV (tab-separated) data files by removing "
                "null rows, deduplicating records, and normalizing column types."
            ),
            capability_type=CapabilityType.SKILL,
            domain="data-processing",
            tags=["tsv", "cleaning", "validation"],
        )
        registry.add(cap_a)
        registry.add(cap_b)
        print(f"\n  Registered: {cap_a.name} — {cap_a.description[:60]}...")
        print(f"  Registered: {cap_b.name} — {cap_b.description[:60]}...")

        # Seed invocations: low reuse (each used by only 1 task type out of many)
        # Create 25 unique tasks, each capability only used by 1
        # (reuse_rate = 1/25 = 0.04, below the 0.05 threshold)
        all_task_ids = [f"TASK_{i:02d}" for i in range(25)]

        # Seed many other tasks so the total task pool is large
        dummy = CapabilityDescriptor(
            name="filler-tool",
            description="A filler tool for task diversity",
            capability_type=CapabilityType.MCP_TOOL,
        )
        registry.add(dummy)
        _seed_invocations(metrics, "filler-tool", all_task_ids, success_rate=1.0, n_per_task=1)

        # Each merge candidate used by only 1 task (reuse_rate = 1/20 = 0.05)
        _seed_invocations(metrics, "csv-data-cleaner", ["TASK_00"], success_rate=0.8, n_per_task=3)
        _seed_invocations(metrics, "tsv-data-cleaner", ["TASK_01"], success_rate=0.8, n_per_task=3)

        reuse_a = metrics.reuse_rate("csv-data-cleaner")
        reuse_b = metrics.reuse_rate("tsv-data-cleaner")
        print(f"\n  csv-data-cleaner reuse_rate: {reuse_a:.3f} (threshold: < 0.05)")
        print(f"  tsv-data-cleaner reuse_rate: {reuse_b:.3f} (threshold: < 0.05)")

        # Check semantic similarity
        # search() returns list[tuple[CapabilityDescriptor, float]]
        results = registry.search("csv-data-cleaner", n_results=5)
        similarity = None
        for cap_desc, score in results:
            if cap_desc.name == "tsv-data-cleaner":
                similarity = score
                break
        print(f"  Semantic similarity: {similarity} (threshold: >= 0.5)")

        if similarity is None or similarity < 0.5:
            print("\n  WARNING: Similarity below 0.5 — merge would not be triggered")
            print("  by the optimizer loop, but we can still test merge() directly.")

        # Run merge — this makes 1-2 LLM calls
        print("\n  Running merge(...)...", flush=True)
        try:
            merged_id = merge(
                registry, metrics,
                ["csv-data-cleaner", "tsv-data-cleaner"],
            )
        except Exception as exc:
            print(f"\n  FAIL: merge() raised: {exc}")
            return False

        # Verify results
        print(f"\n  Results:")
        print(f"  Merged capability: {merged_id}")

        merged = registry.get(merged_id)
        if merged is None:
            print(f"  FAIL: Merged capability '{merged_id}' not found in registry")
            return False

        print(f"  Description: {merged.description[:80]}...")
        print(f"  type={merged.capability_type.value}, active={merged.is_active}")
        print(f"  tags={merged.tags}")

        # Check parents are deactivated
        for parent_name in ["csv-data-cleaner", "tsv-data-cleaner"]:
            parent = registry.get(parent_name)
            active = parent.is_active if parent else "NOT FOUND"
            print(f"  Parent '{parent_name}' is_active: {active}")
            if parent and parent.is_active:
                print(f"  FAIL: Parent '{parent_name}' should be deactivated after merge")
                return False

        # Check events
        events = metrics._conn.execute(
            "SELECT capability_id, event_type, details FROM capability_events "
            "ORDER BY occurred_at"
        ).fetchall()
        print(f"\n  Events recorded: {len(events)}")
        for eid, etype, details in events:
            print(f"    {eid}: {etype} — {details}")

        print("\n  PASS: Merge operation completed successfully")
        return True


# ---------------------------------------------------------------------------
# Full optimization loop test
# ---------------------------------------------------------------------------


def test_full_loop(verbose: bool = False) -> bool:
    """Test the full optimization loop (compute_metrics → identify → apply).

    Seeds a registry with capabilities that will trigger all three operations
    in a single loop pass:

    - **Prune target**: "unused-formatter" — has zero invocations in the window
    - **Split target**: "broad-data-processor" (SKILL) — high reuse (80%), low success (50%)
    - **Merge targets**: "csv-row-cleaner" + "tsv-row-cleaner" (SKILLs) — low reuse, semantically similar
    - **Pinned (should survive)**: "core-tool" — has metadata={"pinned": "true"}

    Runs the full LangGraph optimization loop via create_optimization_loop().
    """
    from src.optimizer.loop import create_optimization_loop
    from src.optimizer.utility import compute_all_utilities

    print("\n" + "=" * 70)
    print("  FULL OPTIMIZATION LOOP TEST")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp_dir:
        registry = _create_fresh_registry(tmp_dir)
        metrics = MetricsStore()  # in-memory

        # --- Register capabilities ---

        # 1. Prune target: zero invocations, not pinned
        prune_cap = CapabilityDescriptor(
            name="unused-formatter",
            description="Format dates between different calendar systems",
            capability_type=CapabilityType.MCP_TOOL,
            domain="utility",
            tags=["formatting"],
        )
        registry.add(prune_cap)

        # 2. Pinned capability: zero invocations but should NOT be pruned
        pinned_cap = CapabilityDescriptor(
            name="core-tool",
            description="Core utility tool that must always be available",
            capability_type=CapabilityType.MCP_TOOL,
            domain="core",
            tags=["core"],
            metadata={"pinned": "true"},
        )
        registry.add(pinned_cap)

        # 3. Split target: SKILL with high reuse, low success
        split_cap = CapabilityDescriptor(
            name="broad-data-processor",
            description=(
                "Process and transform any structured data file including CSV, "
                "JSON, XML, and YAML formats with validation and cleaning"
            ),
            capability_type=CapabilityType.SKILL,
            domain="data-processing",
            tags=["data", "parsing", "transformation"],
        )
        registry.add(split_cap)

        # 4. Merge targets: two similar low-reuse SKILLs
        merge_a = CapabilityDescriptor(
            name="csv-row-cleaner",
            description=(
                "Clean and validate rows in CSV data files by removing nulls, "
                "deduplicating records, and normalizing column types"
            ),
            capability_type=CapabilityType.SKILL,
            domain="data-processing",
            tags=["csv", "cleaning"],
        )
        merge_b = CapabilityDescriptor(
            name="tsv-row-cleaner",
            description=(
                "Clean and validate rows in TSV data files by removing nulls, "
                "deduplicating records, and normalizing column types"
            ),
            capability_type=CapabilityType.SKILL,
            domain="data-processing",
            tags=["tsv", "cleaning"],
        )
        registry.add(merge_a)
        registry.add(merge_b)

        # 5. Filler tool to create a large task pool for reuse_rate denominators
        filler = CapabilityDescriptor(
            name="filler-tool",
            description="A filler tool for task diversity",
            capability_type=CapabilityType.MCP_TOOL,
        )
        registry.add(filler)

        all_caps = registry.list_all()
        print(f"\n  Registered {len(all_caps)} capabilities:")
        for c in all_caps:
            pinned = " [PINNED]" if c.metadata.get("pinned") == "true" else ""
            print(f"    {c.name} ({c.capability_type.value}){pinned}")

        # --- Seed invocations ---

        # Create 25 unique tasks (large pool for reuse_rate denominators)
        all_task_ids = [f"TASK_{i:02d}" for i in range(25)]

        # Filler: invoked across all tasks (so the task pool is large)
        _seed_invocations(metrics, "filler-tool", all_task_ids, success_rate=1.0, n_per_task=1)

        # Prune target: NO invocations (will be pruned)
        # unused-formatter has 0 invocations — prune triggers

        # Pinned: also no invocations, but pinned metadata protects it
        # core-tool has 0 invocations — should NOT be pruned

        # Split target: high reuse (80%), low success (50%)
        split_tasks = all_task_ids[:20]  # 20/25 = 80% reuse
        _seed_invocations(metrics, "broad-data-processor", split_tasks, success_rate=0.5, n_per_task=5)

        # Merge targets: low reuse (1 task each out of 25 = 4%)
        _seed_invocations(metrics, "csv-row-cleaner", ["TASK_00"], success_rate=0.8, n_per_task=3)
        _seed_invocations(metrics, "tsv-row-cleaner", ["TASK_01"], success_rate=0.8, n_per_task=3)

        # Verify metrics
        print("\n  Seeded metrics:")
        for cap_name in ["unused-formatter", "core-tool", "broad-data-processor",
                         "csv-row-cleaner", "tsv-row-cleaner"]:
            rr = metrics.reuse_rate(cap_name)
            sr = metrics.success_rate(cap_name)
            ic = metrics.invocation_count(cap_name)
            print(f"    {cap_name}: invocations={ic}, reuse={rr:.2f}, success={sr:.2f}")

        # Check similarity between merge candidates
        results = registry.search("csv-row-cleaner", n_results=5)
        similarity = None
        for cap_desc, score in results:
            if cap_desc.name == "tsv-row-cleaner":
                similarity = score
                break
        print(f"    csv↔tsv similarity: {similarity:.3f}" if similarity else "    csv↔tsv similarity: NOT FOUND")

        # --- Compute utility scores (pre-loop) ---
        pre_scores = compute_all_utilities(registry, metrics)
        print("\n  Pre-loop utility scores:")
        for cap_id, score in sorted(pre_scores.items()):
            print(f"    {cap_id}: {score:.4f}")

        # --- Run the full optimization loop ---
        print("\n  Running create_optimization_loop()...", flush=True)
        try:
            loop = create_optimization_loop(
                registry, metrics,
                prune_window_days=30,
                split_reuse_threshold=0.6,
                split_success_threshold=0.7,
                merge_reuse_threshold=0.05,
            )
            result = loop.invoke({})
        except Exception as exc:
            print(f"\n  FAIL: Optimization loop raised: {exc}")
            return False

        # --- Verify results ---
        print(f"\n  Results:")

        # Prune
        pruned = result.get("pruned", [])
        print(f"\n  Pruned ({len(pruned)}): {pruned}")
        if "unused-formatter" not in pruned:
            print("  FAIL: 'unused-formatter' should have been pruned (zero invocations)")
            return False
        if "core-tool" in pruned:
            print("  FAIL: 'core-tool' should NOT be pruned (pinned)")
            return False
        print("  OK: unused-formatter pruned, core-tool survived (pinned)")

        # Split
        split_results = result.get("split_results", {})
        print(f"\n  Split results: {split_results}")
        if "broad-data-processor" not in split_results:
            print("  FAIL: 'broad-data-processor' should have been split (reuse=0.80, success=0.50)")
            return False
        variants = split_results["broad-data-processor"]
        if len(variants) < 2:
            print(f"  FAIL: Expected >= 2 split variants, got {len(variants)}")
            return False
        # Verify parent deactivated
        parent = registry.get("broad-data-processor")
        if parent and parent.is_active:
            print("  FAIL: 'broad-data-processor' should be deactivated after split")
            return False
        # Verify variants exist and are active
        for vid in variants:
            v = registry.get(vid)
            if v is None:
                print(f"  FAIL: Split variant '{vid}' not found in registry")
                return False
            print(f"    Variant: {vid} — {v.description[:60]}...")
        print("  OK: broad-data-processor split into", variants)

        # Merge
        merge_results = result.get("merge_results", {})
        print(f"\n  Merge results: {merge_results}")
        if not merge_results:
            print("  FAIL: Expected at least one merge (csv-row-cleaner + tsv-row-cleaner)")
            return False
        merged_id = list(merge_results.values())[0]
        merged = registry.get(merged_id)
        if merged is None:
            print(f"  FAIL: Merged capability '{merged_id}' not found")
            return False
        # Verify parents deactivated
        for parent_name in ["csv-row-cleaner", "tsv-row-cleaner"]:
            p = registry.get(parent_name)
            if p and p.is_active:
                print(f"  FAIL: '{parent_name}' should be deactivated after merge")
                return False
        print(f"  OK: merged into '{merged_id}' — {merged.description[:60]}...")

        # KL divergence
        kl = result.get("kl_divergence")
        print(f"\n  KL divergence: {kl}")

        # Utility scores
        scores = result.get("utility_scores", {})
        print(f"  Utility scores ({len(scores)} capabilities):")
        for cap_id, score in sorted(scores.items()):
            print(f"    {cap_id}: {score:.4f}")

        # Errors
        errors = result.get("errors", [])
        if errors:
            print(f"\n  Errors ({len(errors)}):")
            for e in errors:
                print(f"    {e}")

        # Events in MetricsStore
        events = metrics._conn.execute(
            "SELECT capability_id, event_type, details FROM capability_events "
            "ORDER BY occurred_at"
        ).fetchall()
        print(f"\n  Events recorded: {len(events)}")
        for eid, etype, details in events:
            print(f"    {eid}: {etype} — {details}")

        # Final registry state
        active = [c for c in registry.list_all() if c.is_active]
        inactive = [c for c in registry.list_all() if not c.is_active]
        print(f"\n  Final registry: {len(active)} active, {len(inactive)} inactive")
        for c in active:
            print(f"    [ACTIVE] {c.name} ({c.capability_type.value})")
        for c in inactive:
            print(f"    [INACTIVE] {c.name} ({c.capability_type.value})")

        print("\n  PASS: Full optimization loop completed — prune + split + merge all triggered")
        return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Integration test for optimizer split/merge operations"
    )
    parser.add_argument("--split-only", action="store_true", help="Run only split test")
    parser.add_argument("--merge-only", action="store_true", help="Run only merge test")
    parser.add_argument("--loop-only", action="store_true", help="Run only full loop test")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=level,
        stream=sys.stderr,
    )

    only_one = args.split_only or args.merge_only or args.loop_only
    run_split = args.split_only or not only_one
    run_merge = args.merge_only or not only_one
    run_loop = args.loop_only or not only_one

    results = {}

    if run_split:
        results["split"] = test_split(verbose=args.verbose)

    if run_merge:
        results["merge"] = test_merge(verbose=args.verbose)

    if run_loop:
        results["full_loop"] = test_full_loop(verbose=args.verbose)

    # Summary
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    all_pass = True
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")
        if not passed:
            all_pass = False

    print()
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
