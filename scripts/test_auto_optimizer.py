"""Synthetic test for the pipeline auto-optimizer feature.

Tests that the optimizer, when triggered via the pipeline's optimizer_node,
correctly executes all three operations (prune, split, merge) with seeded
invocation data.

This runs through the real pipeline path (create_pipeline with
run_optimizer=True), not create_optimization_loop() directly — verifying
the full wiring from pipeline to optimizer.

The primary role of this script is **pipeline-wiring verification** —
does ``create_pipeline(run_optimizer=True)`` correctly trigger
prune/split/merge via the ``optimizer_node`` branch? Deep content
assertions for each operation (non-empty source_code / skill_content /
agent_config, variants differing, merged parents combined) belong in
``scripts/test_split_merge.py`` which exercises operations directly.

This script adds:
  (a) seed caps with real executable ``skill_content`` so the optimizer
      operates on real inputs and the downstream pipeline branch is
      honest;
  (b) a minimal content-presence assertion after the happy-path flow;
  (c) ONE rollback scenario asserting atomic rollback when the
      verification gate rejects — this protects the
      ``verify_and_register_or_rollback`` contract at the pipeline
      wiring layer (test_split_merge.py has the per-operation version).

Usage::

    uv run scripts/test_auto_optimizer.py
    uv run scripts/test_auto_optimizer.py --verbose
    uv run scripts/test_auto_optimizer.py --happy-only
    uv run scripts/test_auto_optimizer.py --rollback-only

No Docker needed. Makes 2-4 LLM calls for the happy-path split/merge
(~$0.01). The rollback scenario makes 0-1 LLM calls (gate is patched
before the merged-variant LLM call would run). Takes ~30-90 seconds.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Route dev-script LangFuse emissions to ``environment=test`` so they do not
# pollute protocol eval data. Must be set before ``load_dotenv`` so ambient
# .env values cannot override it.
os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", "test")

load_dotenv(ROOT / ".env")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Seed content — small but real skill_content for the three SKILL capabilities
# ---------------------------------------------------------------------------

_BROAD_DATA_PROCESSOR_SKILL = """---
name: broad-data-processor
description: Process and transform any structured data file (CSV/JSON/XML/YAML).
---

# broad-data-processor

Generic file-format dispatcher. Detects the input format from the
extension and routes to a format-specific parser.

```python
import json
import csv
from pathlib import Path


def process(path: str) -> dict:
    ext = Path(path).suffix.lower()
    if ext == ".json":
        with open(path) as f:
            return {"records": json.load(f)}
    if ext == ".csv":
        with open(path, newline="") as f:
            return {"records": list(csv.DictReader(f))}
    raise ValueError(f"Unsupported format: {ext}")
```
"""

_CSV_ROW_CLEANER_SKILL = """---
name: csv-row-cleaner
description: Clean and validate rows in CSV data files.
---

# csv-row-cleaner

Removes rows with nulls in required columns, deduplicates records,
normalizes column types.

```python
import csv


def clean(path: str, required: list[str]) -> list[dict]:
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    rows = [r for r in rows if all(r.get(c) for c in required)]
    seen = set()
    out = []
    for r in rows:
        key = tuple(sorted(r.items()))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out
```
"""

_TSV_ROW_CLEANER_SKILL = """---
name: tsv-row-cleaner
description: Clean and validate rows in TSV data files.
---

# tsv-row-cleaner

Removes rows with nulls in required columns, deduplicates records,
normalizes column types. Same shape as csv-row-cleaner but for TSV.

```python
import csv


def clean(path: str, required: list[str]) -> list[dict]:
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\\t"))
    rows = [r for r in rows if all(r.get(c) for c in required)]
    seen = set()
    out = []
    for r in rows:
        key = tuple(sorted(r.items()))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out
```
"""


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


# ---------------------------------------------------------------------------
# Happy-path test — full pipeline with real seed content
# ---------------------------------------------------------------------------


def test_happy_path(verbose: bool = False) -> bool:
    from src.orchestrator.pipeline import create_pipeline
    from src.orchestrator.tool_loader import ToolLoader
    from src.registry.metrics import MetricsStore
    from src.registry.store import CapabilityRegistry, seed_registry
    from src.schemas.capability import CapabilityDescriptor, CapabilityType
    from src.schemas.task import TaskState

    print("=" * 70)
    print("  AUTO-OPTIMIZER — HAPPY PATH")
    print("  Verifies: auto-enable + prune + split + merge via pipeline")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp_dir:
        registry = CapabilityRegistry(
            persist_directory=tmp_dir, collection_name="test_auto_opt"
        )
        seed_registry(registry)
        metrics = MetricsStore()  # in-memory

        # ==================================================================
        # Setup: register capabilities that will trigger each operation
        # ==================================================================

        # 1. PRUNE TARGET: unpinned, zero invocations, no content needed
        registry.add(CapabilityDescriptor(
            name="unused-temp-tool",
            description="A temporary tool that nobody uses",
            capability_type=CapabilityType.MCP_TOOL,
            domain="test",
            tags=["test"],
        ))

        # 2. SPLIT TARGET: SKILL with real skill_content (stored in metadata
        #    per the registry convention — skill_content is not a top-level
        #    field on CapabilityDescriptor), high reuse + low success.
        registry.add(CapabilityDescriptor(
            name="broad-data-processor",
            description=(
                "Process and transform any structured data file including CSV, "
                "JSON, XML, and YAML formats with validation and cleaning"
            ),
            capability_type=CapabilityType.SKILL,
            domain="data-processing",
            tags=["data", "transformation"],
            metadata={"skill_content": _BROAD_DATA_PROCESSOR_SKILL},
        ))

        # 3. MERGE TARGETS: two similar low-reuse SKILLs with real skill_content
        #    PLUS populated input_schema/output_schema so the Jaccard conjunct
        #    in identify_candidates_node passes (both cosine >= 0.5 AND
        #    Jaccard in [0.5, 1.0) required per the proposal's merge rule).
        #    Schemas overlap on {path, required_columns, records} but differ
        #    on {rows_dropped} vs {}; Jaccard = 3/4 = 0.75.
        registry.add(CapabilityDescriptor(
            name="csv-row-cleaner",
            description=(
                "Clean and validate rows in CSV data files by removing nulls, "
                "deduplicating records, and normalizing column types"
            ),
            capability_type=CapabilityType.SKILL,
            domain="data-processing",
            tags=["csv", "cleaning"],
            input_schema={"path": "str", "required_columns": "list[str]"},
            output_schema={"records": "list[dict]", "rows_dropped": "int"},
            metadata={"skill_content": _CSV_ROW_CLEANER_SKILL},
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
            input_schema={"path": "str", "required_columns": "list[str]"},
            output_schema={"records": "list[dict]"},
            metadata={"skill_content": _TSV_ROW_CLEANER_SKILL},
        ))

        initial_caps = {c.name for c in registry.list_all(is_active=True)}
        print(f"\n  Registry: {len(initial_caps)} active capabilities")

        # ==================================================================
        # Seed invocations to trigger thresholds
        # ==================================================================

        all_tasks = [f"task_{i:02d}" for i in range(30)]

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

        print("\n  Metrics:")
        print(f"    unused-temp-tool: invocations={metrics.invocation_count('unused-temp-tool')}")
        rr = metrics.reuse_rate("broad-data-processor")
        sr = metrics.success_rate("broad-data-processor")
        print(f"    broad-data-processor: reuse={rr:.2f}, success={sr:.2f} (split: >0.6 AND <0.7)")
        print(
            "    csv-row-cleaner: "
            f"reuse={metrics.reuse_rate('csv-row-cleaner'):.3f} (merge: <0.05)"
        )
        print(
            "    tsv-row-cleaner: "
            f"reuse={metrics.reuse_rate('tsv-row-cleaner'):.3f} (merge: <0.05)"
        )

        results = registry.search("csv-row-cleaner", n_results=5)
        sim = None
        for cap, score in results:
            if cap.name == "tsv-row-cleaner":
                sim = score
                break
        print(
            f"    csv↔tsv similarity: {sim:.3f}"
            if sim else "    csv↔tsv similarity: NOT FOUND"
        )

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
            pipeline.invoke(state)
        except Exception as exc:
            print(f"\n  FAIL: Pipeline raised: {exc}")
            return False

        print("  Pipeline completed.")

        events = metrics._conn.execute(
            "SELECT capability_id, event_type, details "
            "FROM capability_events ORDER BY occurred_at"
        ).fetchall()

        print(f"\n  Events recorded: {len(events)}")
        # Always dump events in the happy-path test (cheap, useful for
        # diagnosing split / merge behavior under LLM non-determinism).
        for cap_id, event_type, details in events:
            print(f"    {cap_id}: {event_type} — {details}")

        all_pass = True

        # --- PRUNE ---
        pruned = [e for e in events if e[1] == "pruned"]
        print(f"\n  PRUNE: {len(pruned)} capability(ies) pruned")
        if any(e[0] == "unused-temp-tool" for e in pruned):
            cap = registry.get("unused-temp-tool")
            if cap and cap.is_active:
                print("    FAIL: unused-temp-tool should be deactivated")
                all_pass = False
            else:
                print("    OK")
        else:
            print("    FAIL: unused-temp-tool should have been pruned")
            all_pass = False

        for name in ["csv-parser", "json-validator", "text-statistics"]:
            cap = registry.get(name)
            if not cap or not cap.is_active:
                print(f"    FAIL: Pinned '{name}' was pruned or missing")
                all_pass = False

        # Two valid outcomes for the wiring test:
        #   (a) "split" event fired → parent deactivated, variants registered
        #   (b) "split_rollback" event fired → gate rejected LLM output,
        #       parent stays active (atomic rollback invariant holds)
        # Both prove the optimizer_node reached the split path for this cap
        # under real LLM conditions. The fully-successful split path is
        # validated deterministically in tests/unit/test_optimizer_operations.py
        # + scripts/test_split_merge.py (test_split_mcp_tool).
        split_success = [
            e for e in events
            if e[1] == "split" and e[0] == "broad-data-processor"
        ]
        split_rollback = [
            e for e in events
            if e[1] == "split_rollback" and e[0] == "broad-data-processor"
        ]
        parent = registry.get("broad-data-processor")
        if split_success:
            print("\n  SPLIT (success): 1 capability split")
            if parent and parent.is_active:
                print("    FAIL: Parent should be deactivated after split")
                all_pass = False
            created = [
                e for e in events
                if e[1] == "split_variant"
                and "split from broad-data-processor" in (e[2] or "")
            ]
            print(f"    Variants created: {len(created)}")
            if len(created) >= 2:
                # Minimal content-presence check: each variant has non-empty
                # skill_content in metadata. Per scope, we do NOT assert on
                # the content's shape (test_split_merge.py covers that
                # end-to-end).
                empty_variants = []
                for cap_id, _, _ in created:
                    variant = registry.get(cap_id)
                    content = (
                        (variant.metadata or {}).get("skill_content", "")
                        if variant else ""
                    )
                    if not content.strip():
                        empty_variants.append(cap_id)
                if empty_variants:
                    print(
                        "    FAIL: Variants have empty skill_content: "
                        f"{empty_variants}"
                    )
                    all_pass = False
                else:
                    print("    OK (all variants have non-empty skill_content)")
            else:
                print(f"    FAIL: Expected >= 2 variants, got {len(created)}")
                all_pass = False
        elif split_rollback:
            # Atomic-rollback invariant: parent must stay active
            print("\n  SPLIT (rollback): gate rejected variants")
            if not parent or not parent.is_active:
                print("    FAIL: Parent should STAY active after split_rollback")
                all_pass = False
            else:
                reason = split_rollback[0][2] or ""
                print(f"    OK (parent active, rollback reason: {reason[:60]}...)")
        else:
            print("\n  SPLIT: neither 'split' nor 'split_rollback' fired")
            print("    FAIL: broad-data-processor should have triggered split path")
            all_pass = False

        # Two valid outcomes for the wiring test (parallel to SPLIT above):
        #   (a) "merged" event(s) → parents merged into new cap, content check
        #   (b) "verification_rollback" on the merged cap → gate rejected,
        #       parents stay active (atomic rollback invariant holds)
        # Both prove optimizer_node reached the merge path. Deterministic
        # content validation lives in test_split_merge.py.
        merge_events = [e for e in events if e[1] == "merged"]
        parent_merge = [e for e in merge_events if (e[2] or "").startswith("into ")]
        merge_created = [e for e in merge_events if (e[2] or "").startswith("from [")]
        merge_rollback = [e for e in events if e[1] == "verification_rollback"]

        if merge_created:
            print(
                "\n  MERGE (success): "
                f"{len(parent_merge)} parent(s) merged, "
                f"{len(merge_created)} new cap(s) created"
            )
            merged_name = merge_created[0][0]
            merged = registry.get(merged_name)
            merged_content = (merged.metadata or {}).get("skill_content", "") if merged else ""
            if merged is None:
                print(f"    FAIL: Merged descriptor '{merged_name}' not in registry")
                all_pass = False
            elif not merged_content.strip():
                print(f"    FAIL: Merged '{merged_name}' has empty skill_content")
                all_pass = False
            else:
                print(f"    OK (merged '{merged_name}' has non-empty skill_content)")
        elif merge_rollback:
            print("\n  MERGE (rollback): gate rejected merged cap")
            csv_cap = registry.get("csv-row-cleaner")
            tsv_cap = registry.get("tsv-row-cleaner")
            if csv_cap and csv_cap.is_active and tsv_cap and tsv_cap.is_active:
                reason = merge_rollback[0][2] or ""
                print(f"    OK (both parents active, rollback reason: {reason[:60]}...)")
            else:
                print("    FAIL: Parents should STAY active after merge rollback")
                all_pass = False
        else:
            print("\n  MERGE: neither 'merged' nor 'verification_rollback' fired")
            print("    FAIL: csv-row-cleaner + tsv-row-cleaner should have triggered merge path")
            all_pass = False

        # --- Final registry state ---
        if verbose:
            final_active = [c.name for c in registry.list_all(is_active=True)]
            final_inactive = [c.name for c in registry.list_all() if not c.is_active]
            print(f"\n  Final registry: {len(final_active)} active, {len(final_inactive)} inactive")

    print("\n  " + ("PASS: happy path" if all_pass else "FAIL: happy path"))
    return all_pass


# ---------------------------------------------------------------------------
# Rollback test — gate rejection → atomic rollback invariant
# ---------------------------------------------------------------------------


def test_rollback(verbose: bool = False) -> bool:
    """Verify atomic rollback when the verification gate rejects.

    Setup: seed two merge-candidate SKILLs with real skill_content, seed
    invocations to put them in the low-reuse pool and push the optimizer
    above the auto-enable threshold.

    Intervention: patch ``src.verification.gate.verify`` to return a
    failing report. The optimizer's merge will attempt to create a
    merged descriptor; ``verify_and_register_or_rollback`` will reject
    it atomically.

    Assert:
      1. Both parent SKILLs remain active (not deactivated by the merge)
      2. No "merged from" descriptor appears in the registry
      3. At least one ``verification_rollback`` event is recorded

    This is a pipeline-wiring assertion: does the optimizer_node's
    branch honor the rollback contract? Per-operation content
    invariants are covered by ``scripts/test_split_merge.py``.
    """
    from src.orchestrator.pipeline import create_pipeline
    from src.orchestrator.tool_loader import ToolLoader
    from src.registry.metrics import MetricsStore
    from src.registry.store import CapabilityRegistry, seed_registry
    from src.schemas.capability import CapabilityDescriptor, CapabilityType
    from src.schemas.task import TaskState
    from src.schemas.verification import Severity, VerificationFinding, VerificationReport

    print("\n" + "=" * 70)
    print("  AUTO-OPTIMIZER — ROLLBACK SCENARIO")
    print("  Verifies: gate rejection → atomic rollback + no orphan descriptor")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp_dir:
        registry = CapabilityRegistry(
            persist_directory=tmp_dir, collection_name="test_auto_opt_rb"
        )
        seed_registry(registry)
        metrics = MetricsStore()

        # Two merge-candidate SKILLs with real content + populated schemas
        # (same shape as happy-path setup — Jaccard = 0.75, in [0.5, 1.0))
        registry.add(CapabilityDescriptor(
            name="csv-row-cleaner",
            description=(
                "Clean and validate rows in CSV data files by removing nulls, "
                "deduplicating records, and normalizing column types"
            ),
            capability_type=CapabilityType.SKILL,
            domain="data-processing",
            tags=["csv", "cleaning"],
            input_schema={"path": "str", "required_columns": "list[str]"},
            output_schema={"records": "list[dict]", "rows_dropped": "int"},
            metadata={"skill_content": _CSV_ROW_CLEANER_SKILL},
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
            input_schema={"path": "str", "required_columns": "list[str]"},
            output_schema={"records": "list[dict]"},
            metadata={"skill_content": _TSV_ROW_CLEANER_SKILL},
        ))

        # Push the optimizer above auto-enable threshold and put the two
        # candidates in the low-reuse window.
        all_tasks = [f"task_{i:02d}" for i in range(30)]
        for cap_name in ["csv-parser", "json-validator", "text-statistics"]:
            _seed_invocations(metrics, cap_name, all_tasks, n_per_task=1)
        _seed_invocations(metrics, "csv-row-cleaner", ["task_00"],
                          success_rate=0.8, n_per_task=3)
        _seed_invocations(metrics, "tsv-row-cleaner", ["task_01"],
                          success_rate=0.8, n_per_task=3)

        # Pre-flight diagnostics — confirm the merge candidate conditions
        # are actually met before we run the pipeline, else the rollback
        # path can't be exercised regardless of whether the patch works.
        total_inv = metrics.invocation_count_total()
        print("\n  Pre-flight diagnostics:")
        print(f"    total invocations: {total_inv} (auto-enable: >= 20)")
        print(
            "    csv-row-cleaner reuse: "
            f"{metrics.reuse_rate('csv-row-cleaner'):.3f} (merge: < 0.05)"
        )
        print(
            "    tsv-row-cleaner reuse: "
            f"{metrics.reuse_rate('tsv-row-cleaner'):.3f} (merge: < 0.05)"
        )
        sim_pairs = registry.search("csv-row-cleaner", n_results=5)
        csv_tsv_sim = next(
            (score for cap, score in sim_pairs if cap.name == "tsv-row-cleaner"),
            None,
        )
        print(
            f"    csv↔tsv cosine: {csv_tsv_sim:.3f}"
            if csv_tsv_sim else "    csv↔tsv cosine: NOT FOUND"
        )

        # Patch gate.verify to reject any merged artifact. The merge op
        # calls verify() on the newly-constructed merged descriptor;
        # rejection triggers the rollback branch.
        def _forced_failure(_artifact) -> VerificationReport:
            return VerificationReport(
                capability_name=getattr(_artifact, "capability_name", "unknown"),
                overall_passed=False,
                tier_reached=1,
                findings=[
                    VerificationFinding(
                        tier=1,
                        category="forced_failure_for_rollback_test",
                        severity=Severity.CRITICAL,
                        message="Injected gate failure for atomic-rollback assertion",
                    )
                ],
            )

        print("\n  Running pipeline with gate.verify patched to reject...", flush=True)

        loader = ToolLoader()
        pipeline = create_pipeline(
            registry, loader,
            run_optimizer=True,
            metrics_store=metrics,
        ).compile()

        state = TaskState(
            task_description="Parse a standard CSV file and return row count"
        )

        with patch("src.verification.gate.verify", side_effect=_forced_failure):
            try:
                pipeline.invoke(state)
            except Exception as exc:
                # Never-raises contract: pipeline must reach terminal state
                # even when the gate rejects. If it raises, that itself is
                # a failure of the atomicity contract.
                print(f"\n  FAIL: Pipeline raised under forced gate failure: {exc}")
                return False

        print("  Pipeline completed (under forced failure).")

        all_pass = True

        # Assertion 1: both parents stay active
        csv_cap = registry.get("csv-row-cleaner")
        tsv_cap = registry.get("tsv-row-cleaner")
        if csv_cap is None or not csv_cap.is_active:
            print("    FAIL: csv-row-cleaner was deactivated despite rollback")
            all_pass = False
        if tsv_cap is None or not tsv_cap.is_active:
            print("    FAIL: tsv-row-cleaner was deactivated despite rollback")
            all_pass = False
        if all_pass:
            print("    OK: both parents still active")

        # Assertion 2: no "merged from" descriptor in the registry
        events = metrics._conn.execute(
            "SELECT capability_id, event_type, details "
            "FROM capability_events ORDER BY occurred_at"
        ).fetchall()
        print(f"    events recorded: {len(events)}")
        if verbose or not events:
            for cap_id, event_type, details in events:
                print(f"    event: {cap_id}: {event_type} — {details}")

        # A successfully-created merged artifact has event_type="merged"
        # and event_details starting with "from [" (per operations.py).
        orphan_created = [
            e for e in events
            if e[1] == "merged" and (e[2] or "").startswith("from [")
        ]
        if orphan_created:
            print(f"    FAIL: orphan merged descriptor(s) created: {orphan_created}")
            all_pass = False
        else:
            print("    OK: no orphan merged descriptor in registry")

        # Assertion 3: rollback event recorded
        rollback_events = [e for e in events if e[1] == "verification_rollback"]
        if not rollback_events:
            print("    FAIL: no verification_rollback event recorded")
            all_pass = False
        else:
            print(f"    OK: {len(rollback_events)} verification_rollback event(s) recorded")

    print("\n  " + ("PASS: rollback" if all_pass else "FAIL: rollback"))
    return all_pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synthetic test for pipeline auto-optimizer wiring"
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--happy-only", action="store_true")
    parser.add_argument("--rollback-only", action="store_true")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=level,
        stream=sys.stderr,
    )

    results: list[tuple[str, bool]] = []

    if not args.rollback_only:
        results.append(("happy path", test_happy_path(verbose=args.verbose)))
    if not args.happy_only:
        results.append(("rollback", test_rollback(verbose=args.verbose)))

    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

    all_pass = all(ok for _, ok in results)
    print("=" * 70)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
