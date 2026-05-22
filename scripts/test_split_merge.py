"""Developer diagnostic for optimizer split and merge operations.

This script is not invoked by the frozen evaluation protocol. It seeds a fresh
ChromaDB registry and MetricsStore with synthetic invocation histories that
satisfy optimizer thresholds, then runs the real split/merge code with LLM
generation and verification.

Coverage:
- Split: SKILL rollback canary, MCP_TOOL coverage verifier, AGENT budget canary.
- Merge: SKILL rollback + happy-path scenarios, MCP_TOOL, and AGENT.
- Full loop: prune + split + merge through ``create_optimization_loop()``.

Split trigger:  reuse_rate > 0.6 AND success_rate < 0.7. SKILL and AGENT
                fire through the optimizer loop; MCP_TOOL can be exercised by
                direct invocation. All three types dispatch through
                ``_split_tools`` / ``_split_skills`` / ``_split_agents``.
Merge trigger:  reuse_rate < 0.05 AND description cosine >= 0.5 AND schema
                Jaccard in [0.5, 1.0). All three types dispatch through
                ``_merge_tools`` / ``_merge_skills`` / ``_merge_agents``.
                Cross-type merge raises ValueError; that remains future work.

The SKILL merge scenarios intentionally exercise different gate outcomes:

``test_merge`` — CSV/TSV cleaners merged into a delimited-data cleaner. The
scenario tends to produce heuristic header detection, so Tier 3 often rejects
the result. That makes it a rollback canary: ``merge()`` returns None, parents
stay active, and no orphan descriptor is left in the registry.

``test_merge_stats`` — deterministic list-statistics SKILLs merged into one
broader statistics capability. Pure arithmetic/statistics behavior gives the
gate a stable happy-path target: merged capability registered, parents
deactivated.

Usage::

    uv run scripts/test_split_merge.py
    uv run scripts/test_split_merge.py --split-only
    uv run scripts/test_split_merge.py --merge-only
    uv run scripts/test_split_merge.py --loop-only
    uv run scripts/test_split_merge.py --merge-mcp-only --verbose

Use ``--help`` for all single-scenario flags. The script makes multiple real
LLM calls and may invoke Docker-backed verification.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Route dev-script LangFuse emissions to ``environment=test`` so they do not
# pollute protocol eval data. Must be set before ``load_dotenv`` so ambient
# .env values cannot override it.
os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", "test")

load_dotenv(ROOT / ".env")

from src.optimizer.operations import merge, split  # noqa: E402
from src.registry.metrics import MetricsStore  # noqa: E402
from src.registry.store import CapabilityRegistry  # noqa: E402
from src.schemas.capability import CapabilityDescriptor, CapabilityType  # noqa: E402
from src.schemas.metrics import InvocationRecord  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_fresh_registry(tmp_dir: str) -> CapabilityRegistry:
    """Create a fresh ChromaDB registry in a temp directory."""
    return CapabilityRegistry(
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
    """Test the split operation end-to-end (SKILL rollback canary).

    Scenario: A SKILL called "data-file-processor" is used across many task
    types (high reuse) but fails often (low success). The optimizer should
    split it into 2+ specialized variants.

    Thresholds: reuse_rate > 0.6, success_rate < 0.7

    - Parent carries ``skill_content`` in metadata so ``_split_skills`` has
      a reference implementation to specialize.
    - ``split()`` now returns ``None`` on any failure (gate rejection,
      name collision, LLM failure) rather than raising. Handles both
      outcomes as valid PASS as long as the atomic rollback invariant
      holds — the same dual-path pattern as ``test_merge``.
    - SKILL coverage verification is vacuous because ``verify_coverage`` is
      MCP_TOOL-only. This test therefore only exercises the three-tier gate
      per variant. For the MCP_TOOL coverage path see ``test_split_mcp_tool``
      below.
    """
    print("\n" + "=" * 70)
    print("  SPLIT TEST — SKILL (rollback canary)")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp_dir:
        registry = _create_fresh_registry(tmp_dir)
        metrics = MetricsStore()  # in-memory

        parent_skill_md = (
            "---\n"
            "name: data-file-processor\n"
            "description: Process and transform structured data files\n"
            "---\n\n"
            "# Data File Processor\n\n"
            "This skill processes and transforms structured data files.\n\n"
            "## Steps\n\n"
            "1. Identify the file format from the path extension.\n"
            "2. Parse the file with an appropriate reader for the format.\n"
            "3. Validate that the parsed data is well-formed.\n"
            "4. Clean and normalize the rows (remove nulls, deduplicate, coerce types).\n"
            "5. Return `{row_count, cleaned_records, format}`.\n"
        )

        # Register the split candidate (SKILL type)
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
            metadata={
                "generated": "true",
                "source": "integration_test",
                "skill_content": parent_skill_md,
            },
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

        reuse = metrics.reuse_rate("data-file-processor")
        success = metrics.success_rate("data-file-processor")
        print(f"\n  Seeded {n_seeded} invocations across {len(used_task_ids)} task types")
        print(f"  reuse_rate  = {reuse:.2f} (threshold: > 0.6)")
        print(f"  success_rate = {success:.2f} (threshold: < 0.7)")

        if reuse <= 0.6 or success >= 0.7:
            print("\n  ERROR: Metrics do not satisfy split thresholds!")
            return False

        # split() may call the model once for structured output plus Tier 3
        # adversarial probes per variant. Two valid outcomes are acceptable:
        #
        # 1. HAPPY PATH: gate passes on every variant → variants registered
        #    with real skill_content → parent deactivated → split returns
        #    the list of variant names.
        #
        # 2. ROLLBACK PATH: gate rejects any variant (typically Tier 3
        #    ground_truth on heuristic content) → split returns None →
        #    atomic invariant requires parent still active, no variants
        #    in the registry. Same shape as test_merge's rollback canary.
        print("\n  Running split(...)...", flush=True)
        try:
            new_ids = split(registry, metrics, "data-file-processor")
        except Exception as exc:
            print(f"\n  FAIL: split() raised: {exc}")
            return False

        if new_ids is None:
            # --- ROLLBACK PATH ---
            print(
                "\n  Info: split() returned None — gate rejected one or more "
                "variants. Verifying atomic rollback invariant."
            )
            rollback_ok = True

            parent = registry.get("data-file-processor")
            if parent is None:
                print("  FAIL: parent 'data-file-processor' disappeared from registry")
                rollback_ok = False
            elif not parent.is_active:
                print(
                    "  FAIL: parent 'data-file-processor' was deactivated "
                    "despite split rejection — atomic rollback violated"
                )
                rollback_ok = False
            else:
                print("  OK: parent 'data-file-processor' still active")

            rollback_events = metrics._conn.execute(
                "SELECT capability_id, details FROM capability_events "
                "WHERE event_type = 'split_rollback' "
                "OR event_type = 'verification_rollback' ORDER BY occurred_at"
            ).fetchall()
            print(f"\n  Rollback events: {len(rollback_events)}")
            for cid, details in rollback_events:
                print(f"    {cid}: {details[:100]}")
            if not rollback_events:
                print("  FAIL: no rollback event recorded")
                rollback_ok = False

            if rollback_ok:
                print(
                    "\n  PASS: gate correctly rejected the split AND atomic "
                    "rollback invariant held — parent untouched, no orphan "
                    "variants in registry."
                )
                return True
            else:
                print("\n  FAIL: atomic rollback invariant violated")
                return False

        # --- HAPPY PATH ---
        print("\n  Results:")
        print(f"  New variants: {new_ids}")

        if len(new_ids) < 2:
            print(f"  FAIL: Expected >= 2 variants, got {len(new_ids)}")
            return False

        parent = registry.get("data-file-processor")
        parent_active = parent.is_active if parent else "NOT FOUND"
        print(f"  Parent 'data-file-processor' is_active: {parent_active}")

        if parent and parent.is_active:
            print("  FAIL: Parent should be deactivated after split")
            return False

        # Variants must be registered, active, and carry real skill_content.
        for vid in new_ids:
            variant = registry.get(vid)
            if variant is None:
                print(f"  FAIL: Variant '{vid}' not found in registry")
                return False
            variant_skill_content = (variant.metadata or {}).get("skill_content", "")
            if not variant_skill_content:
                print(
                    f"  FAIL: variant '{vid}' has no skill_content in metadata — "
                    "metadata-only variants are not valid split outputs"
                )
                return False
            if not variant_skill_content.lstrip().startswith("---"):
                print(
                    f"  FAIL: variant '{vid}' skill_content should start with "
                    "YAML frontmatter (---) per the DeepAgents skill spec"
                )
                return False
            print(f"  Variant '{vid}': {variant.description[:60]}...")
            print(
                f"    type={variant.capability_type.value}, "
                f"active={variant.is_active}, "
                f"skill_content={len(variant_skill_content)} chars"
            )

        events = metrics._conn.execute(
            "SELECT capability_id, event_type, details FROM capability_events "
            "ORDER BY occurred_at"
        ).fetchall()
        print(f"\n  Events recorded: {len(events)}")
        for eid, etype, details in events:
            print(f"    {eid}: {etype} — {details}")

        print("\n  PASS: Split operation completed successfully (happy path)")
        return True


# ---------------------------------------------------------------------------
# Split test — MCP_TOOL + coverage verifier (happy-path canary)
# ---------------------------------------------------------------------------


def test_split_mcp_tool(verbose: bool = False) -> bool:
    """Test the MCP_TOOL split path + verify_coverage end-to-end.

    Scenario: An MCP_TOOL called ``clamp-integer`` takes a value and
    clamps it to a configurable range. It has high reuse but fails on
    two recorded edge-case inputs:

    1. ``{value: 5, low: 100, high: 0}`` — swapped bounds, parent
       raises a ``ValueError``.
    2. ``{value: 150, low: 0, high: null}`` — null high bound, parent
       raises a ``TypeError`` on the ``>`` comparison with ``None``.

    Both are error-cases. Under the coverage verifier's error-case semantics,
    a variant handles the case if it runs on the recorded ``input_args``
    without raising. Correct variants — ones that validate their bounds before
    comparing — will handle both cases by not raising. Happy-path PASS is the
    expected outcome.

    **Why both are error-cases and not one error + one wrong-output:**
    the wrong-output-case branch of ``_variant_handles_case`` has
    meaningful semantics only when the recorded ``actual_output`` is
    *genuinely* wrong relative to some oracle. Without a deliberately-
    buggy parent (which would complicate the test), any synthetic
    wrong-output case risks labeling a correct output as wrong —
    which produces a guaranteed rollback because correct variants
    can't differentiate without becoming incorrect. See §12 finding
    5d for the full analysis. The wrong-output-case branch remains
    covered by unit tests in
    ``tests/unit/test_coverage.py::TestVariantHandlesCase_WrongOutputCase``.

    This scenario specifically exercises :func:`verify_coverage` which
    is MCP_TOOL-only (see §12 finding #5). The failure cases are
    seeded with real ``input_args`` + ``error_message`` + null
    ``actual_output`` that the coverage replay can consume — both
    reach the error-case branch of ``_variant_handles_case``.

    Expected happy path: the split LLM produces 2+ variants, phase B
    verifies them through the gate, phase C replays them against both
    error-cases and finds each failure mode fixed, phase D registers
    the variants, phase E deactivates the parent. If phase B or phase C
    rejects (LLM non-determinism or stochastic Tier 3 outcome), the
    atomic invariant holds.
    """
    print("\n" + "=" * 70)
    print("  SPLIT TEST — MCP_TOOL + coverage verifier (happy-path canary)")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp_dir:
        registry = _create_fresh_registry(tmp_dir)
        metrics = MetricsStore()

        # Parent: a simple clamp function the split LLM can specialize.
        # Deliberately minimal so the LLM doesn't have trouble writing
        # specialized variants.
        parent_source = (
            '"""Clamp an integer to a configurable [low, high] range."""\n'
            "from typing import Annotated\n"
            "from mcp.server.fastmcp import FastMCP\n"
            "from pydantic import Field\n\n"
            'mcp = FastMCP("clamp-integer")\n\n'
            "@mcp.tool()\n"
            "async def clamp(\n"
            "    value: Annotated[int, Field(description='The integer to clamp')],\n"
            "    low: Annotated[int, Field(description='Lower bound (inclusive)')] = 0,\n"
            "    high: Annotated[int, Field(description='Upper bound (inclusive)')] = 100,\n"
            ") -> dict:\n"
            "    \"\"\"Return {'clamped': clamped_value}.\"\"\"\n"
            "    if value < low:\n"
            "        return {'clamped': low}\n"
            "    if value > high:\n"
            "        return {'clamped': high}\n"
            "    return {'clamped': value}\n\n"
            'if __name__ == "__main__":\n'
            "    mcp.run()\n"
        )

        parent = CapabilityDescriptor(
            name="clamp-integer",
            description="Clamp an integer to a configurable [low, high] range",
            capability_type=CapabilityType.MCP_TOOL,
            domain="math",
            tags=["math", "utility"],
            metadata={
                "generated": "true",
                "source": "integration_test",
                "source_code": parent_source,
            },
        )
        registry.add(parent)
        print(f"\n  Registered parent: {parent.name} ({parent.capability_type.value})")

        # Seed high reuse + low success so the parent qualifies as a
        # split candidate. The thresholds are loop-level; calling
        # split() directly bypasses them but we seed for realism.
        all_task_ids = [f"TASK_{i:02d}" for i in range(10)]
        used_task_ids = all_task_ids[:8]
        dummy = CapabilityDescriptor(
            name="filler-tool-split-mcp",
            description="filler",
            capability_type=CapabilityType.MCP_TOOL,
        )
        registry.add(dummy)
        _seed_invocations(
            metrics, "filler-tool-split-mcp", all_task_ids,
            success_rate=1.0, n_per_task=1,
        )
        _seed_invocations(
            metrics, "clamp-integer", used_task_ids,
            success_rate=0.5, n_per_task=5,
        )

        # Seed 2 recorded failure cases that verify_coverage can replay.
        # Both are error-cases (parent raised on edge-case inputs) —
        # they have clean semantics: "variant handles iff runs without
        # raising on the same input".
        #
        # Why both are error-cases and not one error + one wrong-output:
        # the wrong-output-case branch of _variant_handles_case has
        # meaningful semantics only when the recorded actual_output is
        # *genuinely* wrong relative to some oracle (eval harness
        # validator, external oracle). Synthetic wrong-output
        # seeding requires a deliberately-buggy parent. The first
        # version of this test labeled a correct clamp output as
        # "wrong" and the coverage verifier correctly rolled back the
        # split because no correct variant could differentiate against
        # a parent that was right. The wrong-output-case branch remains
        # unit-tested in
        # tests/unit/test_coverage.py::TestVariantHandlesCase_WrongOutputCase.
        error_case_swapped_bounds = InvocationRecord(
            capability_id="clamp-integer",
            task_id="failure-1-swapped-bounds",
            success=False,
            token_cost=100,
            latency_ms=50.0,
            error_message="ValueError: low cannot exceed high",
            input_args='{"value": 5, "low": 100, "high": 0}',
            actual_output=None,
        )
        error_case_null_high = InvocationRecord(
            capability_id="clamp-integer",
            task_id="failure-2-null-high-bound",
            success=False,
            token_cost=100,
            latency_ms=50.0,
            error_message=(
                "TypeError: '>' not supported between instances of "
                "'int' and 'NoneType'"
            ),
            input_args='{"value": 150, "low": 0, "high": null}',
            actual_output=None,
        )
        metrics.record_invocation(error_case_swapped_bounds)
        metrics.record_invocation(error_case_null_high)

        recorded_failures = metrics.get_recent_failures("clamp-integer")
        print(f"  Seeded {len(recorded_failures)} failure case(s) for replay")
        for f in recorded_failures:
            mode = "ERROR" if f.error_message else "WRONG_OUTPUT"
            print(f"    {f.task_id}: {mode} — input_args={f.input_args}")

        # split() calls the model for structured output plus adversarial probes
        # for each variant.
        print("\n  Running split(...)...", flush=True)
        try:
            new_ids = split(registry, metrics, "clamp-integer")
        except Exception as exc:
            print(f"\n  FAIL: split() raised: {exc}")
            return False

        if new_ids is None:
            # Rollback path — atomic invariant must hold.
            print(
                "\n  Info: split() returned None — gate or coverage rejected the "
                "split. Verifying atomic rollback invariant."
            )
            rollback_ok = True
            parent_check = registry.get("clamp-integer")
            if parent_check is None or not parent_check.is_active:
                print("  FAIL: parent 'clamp-integer' should still be active")
                rollback_ok = False
            else:
                print("  OK: parent 'clamp-integer' still active")

            rollback_events = metrics._conn.execute(
                "SELECT capability_id, details FROM capability_events "
                "WHERE event_type IN ('split_rollback', 'verification_rollback') "
                "ORDER BY occurred_at"
            ).fetchall()
            print(f"\n  Rollback events: {len(rollback_events)}")
            for cid, details in rollback_events:
                print(f"    {cid}: {details[:100]}")
            if not rollback_events:
                print("  FAIL: no rollback event recorded")
                rollback_ok = False

            if rollback_ok:
                print(
                    "\n  PASS: gate or coverage correctly rejected the split AND "
                    "atomic rollback invariant held — parent untouched."
                )
                return True
            else:
                print("\n  FAIL: atomic rollback invariant violated")
                return False

        # --- HAPPY PATH (expected for this scenario) ---
        print("\n  Results:")
        print(f"  New variants: {new_ids}")

        if len(new_ids) < 2:
            print(f"  FAIL: Expected >= 2 variants, got {len(new_ids)}")
            return False

        parent_check = registry.get("clamp-integer")
        if parent_check is None or parent_check.is_active:
            print(
                "  FAIL: parent 'clamp-integer' should be deactivated after split"
            )
            return False
        print("  OK: parent 'clamp-integer' deactivated")

        for vid in new_ids:
            variant = registry.get(vid)
            if variant is None:
                print(f"  FAIL: Variant '{vid}' not found in registry")
                return False
            variant_source = (variant.metadata or {}).get("source_code", "")
            if not variant_source:
                print(
                    f"  FAIL: variant '{vid}' has no source_code — regression "
                    "to a metadata-only stub?"
                )
                return False
            if "FastMCP" not in variant_source:
                print(
                    f"  FAIL: variant '{vid}' source does not mention FastMCP "
                    "— malformed MCP tool?"
                )
                return False
            print(f"  Variant '{vid}': {variant.description[:60]}...")
            print(
                f"    type={variant.capability_type.value}, "
                f"active={variant.is_active}, "
                f"source_code={len(variant_source)} chars"
            )

        events = metrics._conn.execute(
            "SELECT capability_id, event_type, details FROM capability_events "
            "ORDER BY occurred_at"
        ).fetchall()
        print(f"\n  Events recorded: {len(events)}")
        for eid, etype, details in events:
            print(f"    {eid}: {etype} — {details[:80]}")

        print(
            "\n  PASS: MCP_TOOL split + verify_coverage completed successfully "
            "(happy path — variants fixed recorded failure modes)"
        )
        return True


# ---------------------------------------------------------------------------
# Merge test
# ---------------------------------------------------------------------------


def test_merge(verbose: bool = False) -> bool:
    """Test the merge operation end-to-end.

    Scenario: Two similar SKILL capabilities ("csv-data-cleaner" and
    "tsv-data-cleaner") both have low reuse and overlap semantically.
    The optimizer should attempt to merge them into a single broader
    capability.

    Thresholds: reuse_rate < 0.05, semantic similarity >= 0.5

    Two outcomes are valid under the fail-closed gate and atomic rollback
    contract:

    1. **Happy path** — gate passes, merged descriptor registered with
       real ``skill_content``, both parents deactivated.
    2. **Rollback path** — gate rejects the generated output, merge() returns
       None, parents stay active, no orphan descriptor in the
       registry, and a ``verification_rollback`` event is recorded.

    Both outcomes count as PASS as long as the atomic rollback invariant
    holds on the rollback path. The rollback path is the more interesting
    one — it empirically validates the Pillar 2 ↔ Pillar 3 coupling
    that ``verify_and_register_or_rollback`` provides.
    """
    print("\n" + "=" * 70)
    print("  MERGE TEST")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp_dir:
        registry = _create_fresh_registry(tmp_dir)
        metrics = MetricsStore()  # in-memory

        # SKILL merges read metadata['skill_content'], so each parent needs
        # a concrete, well-formed SKILL.md body for the merge prompt.
        csv_skill_md = (
            "---\n"
            "name: csv-data-cleaner\n"
            "description: Clean and validate CSV data files\n"
            "---\n\n"
            "# CSV Data Cleaner\n\n"
            "This skill cleans and validates CSV data files.\n\n"
            "## Steps\n\n"
            "1. Parse the CSV with the `csv` stdlib module.\n"
            "2. Remove rows where all values are null or empty.\n"
            "3. Deduplicate by the first column.\n"
            "4. Normalize column types using str/int/float casts.\n"
            "5. Return `{row_count, cleaned_records}`.\n"
        )
        tsv_skill_md = (
            "---\n"
            "name: tsv-data-cleaner\n"
            "description: Clean and validate TSV (tab-separated) data files\n"
            "---\n\n"
            "# TSV Data Cleaner\n\n"
            "This skill cleans and validates TSV (tab-separated) data files.\n\n"
            "## Steps\n\n"
            "1. Parse the TSV with the `csv` stdlib module using delimiter='\\t'.\n"
            "2. Remove rows where all values are null or empty.\n"
            "3. Deduplicate by the first column.\n"
            "4. Normalize column types using str/int/float casts.\n"
            "5. Return `{row_count, cleaned_records}`.\n"
        )

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
            metadata={
                "generated": "true",
                "source": "integration_test",
                "skill_content": csv_skill_md,
            },
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
            metadata={
                "generated": "true",
                "source": "integration_test",
                "skill_content": tsv_skill_md,
            },
        )
        registry.add(cap_a)
        registry.add(cap_b)
        print(f"\n  Registered: {cap_a.name} — {cap_a.description[:60]}...")
        print(f"  Registered: {cap_b.name} — {cap_b.description[:60]}...")

        # Low-reuse merge candidates: each is used by 1 of 25 task types.
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

        # The merge generates a combined SKILL.md and routes it through the gate
        # (Tier 0 quick_validate + Tier 1 layered code-fence extraction +
        # Tier 2/3 adversarial if applicable). merge() returns None on gate
        # rejection instead of raising.
        #
        # TWO valid outcomes under the fail-closed gate:
        #
        # 1. HAPPY PATH: gate passes → merged_id is non-None, merged
        #    descriptor carries real executable content, parents are
        #    deactivated. All original assertions apply.
        #
        # 2. ROLLBACK PATH: gate rejects the merged content → merged_id is None →
        #    verify_and_register_or_rollback recorded a rollback event
        #    → the atomic guarantee requires parents to stay active and the
        #    merged name to remain absent from the registry.
        #    Expected Pillar 2/Pillar 3 coupling: both outcomes count as PASS
        #    as long as the invariant holds.
        #
        # The rollback path is asserted directly, not assumed.
        print("\n  Running merge(...)...", flush=True)
        try:
            merged_id = merge(
                registry, metrics,
                ["csv-data-cleaner", "tsv-data-cleaner"],
            )
        except Exception as exc:
            print(f"\n  FAIL: merge() raised: {exc}")
            return False

        if merged_id is None:
            # Gate rejected the merged artifact; the atomic rollback invariant
            # requires active parents, no merged capability, and a rollback event.
            print(
                "\n  Info: merge() returned None — gate rejected the LLM's "
                "combined SKILL.md. Verifying atomic rollback invariant."
            )

            rollback_ok = True

            for parent_name in ["csv-data-cleaner", "tsv-data-cleaner"]:
                parent = registry.get(parent_name)
                if parent is None:
                    print(f"  FAIL: parent '{parent_name}' disappeared from registry")
                    rollback_ok = False
                elif not parent.is_active:
                    print(
                        f"  FAIL: parent '{parent_name}' was deactivated despite "
                        "gate rejection — atomic rollback violated"
                    )
                    rollback_ok = False
                else:
                    print(f"  OK: parent '{parent_name}' still active")

            rollback_events = metrics._conn.execute(
                "SELECT capability_id, details FROM capability_events "
                "WHERE event_type = 'verification_rollback' ORDER BY occurred_at"
            ).fetchall()
            print(f"\n  verification_rollback events: {len(rollback_events)}")
            for cid, details in rollback_events:
                print(f"    {cid}: {details[:100]}...")
            if not rollback_events:
                print("  FAIL: no verification_rollback event recorded")
                rollback_ok = False

            merged_events = metrics._conn.execute(
                "SELECT capability_id FROM capability_events WHERE event_type = 'merged'"
            ).fetchall()
            if merged_events:
                print(
                    f"  FAIL: 'merged' events recorded despite rollback: {merged_events}"
                )
                rollback_ok = False
            else:
                print("  OK: no 'merged' events recorded (parents not mis-marked)")

            if rollback_ok:
                print(
                    "\n  PASS: gate correctly rejected the LLM output AND "
                    "atomic rollback invariant held — parents untouched, no "
                    "orphan descriptor in registry. This is the expected "
                    "Pillar 2 + Pillar 3 coupling."
                )
                return True
            else:
                print("\n  FAIL: atomic rollback invariant violated")
                return False

        # --- HAPPY PATH ---
        print("\n  Results:")
        print(f"  Merged capability: {merged_id}")

        merged = registry.get(merged_id)
        if merged is None:
            print(f"  FAIL: Merged capability '{merged_id}' not found in registry")
            return False

        print(f"  Description: {merged.description[:80]}...")
        print(f"  type={merged.capability_type.value}, active={merged.is_active}")
        print(f"  tags={merged.tags}")

        # The merged capability must carry real executable content in
        # metadata; this catches regressions to metadata-only stubs.
        merged_skill_content = (merged.metadata or {}).get("skill_content", "")
        if not merged_skill_content:
            print(
                "  FAIL: merged capability has no skill_content in metadata — "
                "metadata-only merged skills are not valid"
            )
            return False
        print(f"  skill_content: {len(merged_skill_content)} chars, starts with "
              f"{merged_skill_content[:40]!r}")
        if not merged_skill_content.lstrip().startswith("---"):
            print(
                "  FAIL: merged skill_content should start with YAML "
                "frontmatter (---) per the DeepAgents skill spec"
            )
            return False

        for parent_name in ["csv-data-cleaner", "tsv-data-cleaner"]:
            parent = registry.get(parent_name)
            active = parent.is_active if parent else "NOT FOUND"
            print(f"  Parent '{parent_name}' is_active: {active}")
            if parent and parent.is_active:
                print(f"  FAIL: Parent '{parent_name}' should be deactivated after merge")
                return False

        events = metrics._conn.execute(
            "SELECT capability_id, event_type, details FROM capability_events "
            "ORDER BY occurred_at"
        ).fetchall()
        print(f"\n  Events recorded: {len(events)}")
        for eid, etype, details in events:
            print(f"    {eid}: {etype} — {details}")

        print("\n  PASS: Merge operation completed successfully (happy path)")
        return True


# ---------------------------------------------------------------------------
# Merge test 2 — math statistics (deterministic happy-path scenario)
# ---------------------------------------------------------------------------


def test_merge_stats(verbose: bool = False) -> bool:
    """Merge two statistics SKILLs — deterministic happy-path scenario.

    Scenario: ``list-stats-basic`` (computes ``{min, max, mean, count}``)
    and ``list-stats-distribution`` (computes ``{median, stdev, variance,
    count}``) both have low reuse and overlap semantically. The optimizer
    should merge them into a single ``list-stats`` capability that
    computes all 7 statistics.

    Why this scenario (vs the CSV/TSV merge in test_merge):

    Statistics functions are **pure and deterministic** — ``statistics.mean
    ([1, 2, 3, 4])`` returns ``2.5`` regardless of input shape, encoding,
    header row, BOM marker, or any other content-sensitive concern. The
    adversarial prober's ``ground_truth`` attack can generate any list of
    numbers and compute the expected stats trivially; if the tool returns
    the same numbers it passes. No heuristics, no content-sensitive edge
    cases, no "header detection" equivalent. This makes the scenario
    well-suited to empirically validating the **happy path** of the merge
    block — gate passes, merged descriptor registered, parents deactivated.

    The CSV/TSV scenario in test_merge() is kept as the rollback-path
    canary — it validates that when the LLM *does* produce code the gate
    rejects (CSV Sniffer heuristics fire the ground_truth attack), the
    atomic rollback invariant holds. Together the two scenarios cover
    both paths the merge block can take.

    Thresholds: reuse_rate < 0.05, semantic similarity >= 0.5
    """
    print("\n" + "=" * 70)
    print("  MERGE TEST — STATISTICS (deterministic happy-path scenario)")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp_dir:
        registry = _create_fresh_registry(tmp_dir)
        metrics = MetricsStore()  # in-memory

        # Seed two math-statistics SKILLs with prose instructions that
        # point at the standard library's ``statistics`` module. The
        # parent instructions are deliberately minimal and deterministic
        # — no content-sensitive edge cases.
        basic_skill_md = (
            "---\n"
            "name: list-stats-basic\n"
            "description: Compute basic descriptive statistics on a list of numbers\n"
            "---\n\n"
            "# List Statistics (Basic)\n\n"
            "Given a list of numbers, compute and return basic descriptive statistics.\n\n"
            "## Behavior\n\n"
            "- Input: a Python list of floats or ints.\n"
            "- Output: a dict with keys `min` (float), `max` (float), "
            "`mean` (float), and `count` (int).\n\n"
            "## Implementation notes\n\n"
            "Use the Python standard library's `statistics` module where "
            "appropriate. Raise a ValueError if the input list is empty. "
            "Coerce inputs to float before computing.\n"
        )
        distribution_skill_md = (
            "---\n"
            "name: list-stats-distribution\n"
            "description: Compute distribution statistics on a list of numbers\n"
            "---\n\n"
            "# List Statistics (Distribution)\n\n"
            "Given a list of numbers, compute and return distribution statistics.\n\n"
            "## Behavior\n\n"
            "- Input: a Python list of floats or ints.\n"
            "- Output: a dict with keys `median` (float), `stdev` (float), "
            "`variance` (float), and `count` (int).\n\n"
            "## Implementation notes\n\n"
            "Use `statistics.median`, `statistics.stdev`, and "
            "`statistics.variance` from the Python standard library. "
            "Raise a ValueError if the input list has fewer than 2 elements "
            "(stdev/variance require at least 2). Coerce inputs to float.\n"
        )

        cap_a = CapabilityDescriptor(
            name="list-stats-basic",
            description=(
                "Compute basic descriptive statistics (min, max, mean, count) "
                "on a list of numbers."
            ),
            capability_type=CapabilityType.SKILL,
            domain="statistics",
            tags=["math", "statistics", "descriptive"],
            metadata={
                "generated": "true",
                "source": "integration_test",
                "skill_content": basic_skill_md,
            },
        )
        cap_b = CapabilityDescriptor(
            name="list-stats-distribution",
            description=(
                "Compute distribution statistics (median, stdev, variance, count) "
                "on a list of numbers."
            ),
            capability_type=CapabilityType.SKILL,
            domain="statistics",
            tags=["math", "statistics", "distribution"],
            metadata={
                "generated": "true",
                "source": "integration_test",
                "skill_content": distribution_skill_md,
            },
        )
        registry.add(cap_a)
        registry.add(cap_b)
        print(f"\n  Registered: {cap_a.name} — {cap_a.description[:60]}...")
        print(f"  Registered: {cap_b.name} — {cap_b.description[:60]}...")

        # Seed invocations: low reuse (each used by only 1 task type out
        # of many) — same threshold satisfaction as test_merge.
        all_task_ids = [f"TASK_{i:02d}" for i in range(25)]
        dummy = CapabilityDescriptor(
            name="filler-tool-stats",
            description="A filler tool for task diversity in the stats merge test",
            capability_type=CapabilityType.MCP_TOOL,
        )
        registry.add(dummy)
        _seed_invocations(
            metrics, "filler-tool-stats", all_task_ids,
            success_rate=1.0, n_per_task=1,
        )
        _seed_invocations(
            metrics, "list-stats-basic", ["TASK_00"],
            success_rate=0.8, n_per_task=3,
        )
        _seed_invocations(
            metrics, "list-stats-distribution", ["TASK_01"],
            success_rate=0.8, n_per_task=3,
        )

        reuse_a = metrics.reuse_rate("list-stats-basic")
        reuse_b = metrics.reuse_rate("list-stats-distribution")
        print(f"\n  list-stats-basic reuse_rate: {reuse_a:.3f} (threshold: < 0.05)")
        print(f"  list-stats-distribution reuse_rate: {reuse_b:.3f} (threshold: < 0.05)")

        results = registry.search("list-stats-basic", n_results=5)
        similarity = None
        for cap_desc, score in results:
            if cap_desc.name == "list-stats-distribution":
                similarity = score
                break
        print(f"  Semantic similarity: {similarity} (threshold: >= 0.5)")

        if similarity is None or similarity < 0.5:
            print("\n  WARNING: Similarity below 0.5 — merge would not be triggered")
            print("  by the optimizer loop, but we can still test merge() directly.")

        # merge() generates the combined SKILL.md, then routes it through the
        # three-tier gate.
        # Same dual-path outcome handling as test_merge(): happy path
        # registers the merged capability and deactivates parents;
        # rollback path verifies the atomic invariant. For the stats
        # scenario we expect the happy path because the reference
        # implementation is deterministic and Tier 3 has no content-
        # sensitivity to exploit.
        print("\n  Running merge(...)...", flush=True)
        try:
            merged_id = merge(
                registry, metrics,
                ["list-stats-basic", "list-stats-distribution"],
            )
        except Exception as exc:
            print(f"\n  FAIL: merge() raised: {exc}")
            return False

        if merged_id is None:
            # Expected-to-merge scenarios still need consistent rollback state.
            print(
                "\n  UNEXPECTED: merge() returned None on the stats scenario. "
                "The gate rejected the LLM output, which means even the "
                "deterministic scenario couldn't produce a clean merge. "
                "Verifying atomic rollback invariant still holds."
            )
            rollback_ok = True
            for parent_name in ["list-stats-basic", "list-stats-distribution"]:
                parent = registry.get(parent_name)
                if parent is None or not parent.is_active:
                    print(f"  FAIL: parent '{parent_name}' should still be active")
                    rollback_ok = False
                else:
                    print(f"  OK: parent '{parent_name}' still active")

            rollback_events = metrics._conn.execute(
                "SELECT capability_id, details FROM capability_events "
                "WHERE event_type = 'verification_rollback' ORDER BY occurred_at"
            ).fetchall()
            if not rollback_events:
                print("  FAIL: no verification_rollback event recorded")
                rollback_ok = False

            if rollback_ok:
                print(
                    "\n  PASS (unexpected rollback): atomic invariant held. "
                    "The stats scenario was supposed to be the happy-path "
                    "validator — if this keeps firing, investigate why "
                    "Tier 3 is rejecting even deterministic implementations."
                )
                return True
            else:
                print("\n  FAIL: atomic rollback invariant violated")
                return False

        # --- HAPPY PATH (expected for this scenario) ---
        print("\n  Results:")
        print(f"  Merged capability: {merged_id}")

        merged = registry.get(merged_id)
        if merged is None:
            print(f"  FAIL: Merged capability '{merged_id}' not found in registry")
            return False

        print(f"  Description: {merged.description[:80]}...")
        print(f"  type={merged.capability_type.value}, active={merged.is_active}")
        print(f"  tags={merged.tags}")

        merged_skill_content = (merged.metadata or {}).get("skill_content", "")
        if not merged_skill_content:
            print(
                "  FAIL: merged capability has no skill_content in metadata — "
                "metadata-only merged skills are not valid"
            )
            return False
        print(
            f"  skill_content: {len(merged_skill_content)} chars, starts with "
            f"{merged_skill_content[:40]!r}"
        )
        if not merged_skill_content.lstrip().startswith("---"):
            print(
                "  FAIL: merged skill_content should start with YAML "
                "frontmatter (---) per the DeepAgents skill spec"
            )
            return False

        # Soft content check: the merged skill should cover both parents'
        # statistics concepts.
        missing_concepts: list[str] = []
        for keyword in ["min", "max", "mean", "median", "stdev", "variance", "count"]:
            if keyword not in merged_skill_content.lower():
                missing_concepts.append(keyword)
        if missing_concepts:
            print(
                f"  WARNING: merged skill_content does not mention: "
                f"{missing_concepts}. The merge LLM may have dropped some "
                f"of the parents' behavior — soft warning, not a fail."
            )
        else:
            print("  OK: merged content mentions all 7 expected stat keywords")

        for parent_name in ["list-stats-basic", "list-stats-distribution"]:
            parent = registry.get(parent_name)
            active = parent.is_active if parent else "NOT FOUND"
            print(f"  Parent '{parent_name}' is_active: {active}")
            if parent and parent.is_active:
                print(f"  FAIL: Parent '{parent_name}' should be deactivated after merge")
                return False

        events = metrics._conn.execute(
            "SELECT capability_id, event_type, details FROM capability_events "
            "ORDER BY occurred_at"
        ).fetchall()
        print(f"\n  Events recorded: {len(events)}")
        for eid, etype, details in events:
            print(f"    {eid}: {etype} — {details}")

        print(
            "\n  PASS: Stats merge completed successfully (happy path — "
            "deterministic scenario cleared Tier 3)"
        )
        return True


# ---------------------------------------------------------------------------
# MCP_TOOL merge test (deterministic happy-path canary)
# ---------------------------------------------------------------------------


def test_merge_mcp_tool(verbose: bool = False) -> bool:
    """Merge two MCP_TOOL capabilities — deterministic happy-path scenario.

    Closes the MCP_TOOL merge path in the Pillar 3 per-type integration
    matrix. ``_merge_tools`` produces real executable FastMCP source from
    each parent's ``metadata['source_code']``.

    Scenario: ``compute-sum`` (returns ``{sum: a+b}``) and
    ``compute-product`` (returns ``{product: a*b}``) both have low reuse
    and overlap semantically (both are binary arithmetic on two numbers).
    The merge target is a single ``compute-arithmetic`` tool that
    returns ``{sum, product}`` for the same input.

    Why this is a happy-path canary (vs rollback canary):
    integer arithmetic has no content-sensitive heuristics the Tier 3
    adversarial prober can exploit. ``ground_truth`` attacks compute
    ``a+b`` and ``a*b`` themselves; the tool must return the same values.
    No CSV-Sniffer-style edge cases. If this scenario EVER rolls back,
    investigate whether Tier 3 is rejecting even trivially-correct code
    — the rollback fallback in this test treats that as a PASS (atomic
    invariant holds) with a diagnostic note.

    Thresholds: reuse_rate < 0.05 per parent.
    """
    print("\n" + "=" * 70)
    print("  MERGE TEST — MCP_TOOL (deterministic happy-path)")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp_dir:
        registry = _create_fresh_registry(tmp_dir)
        metrics = MetricsStore()

        # Two deterministic MCP_TOOLs with real FastMCP source.
        sum_source = (
            "from mcp.server.fastmcp import FastMCP\n"
            "from pydantic import Field\n"
            "from typing import Annotated\n"
            "\n"
            "mcp = FastMCP('compute-sum')\n"
            "\n"
            "@mcp.tool()\n"
            "async def compute_sum(\n"
            "    a: Annotated[float, Field(description='First operand')],\n"
            "    b: Annotated[float, Field(description='Second operand')],\n"
            ") -> dict:\n"
            "    \"\"\"Return {'sum': a + b} for two numeric operands.\"\"\"\n"
            "    return {'sum': float(a) + float(b)}\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    mcp.run()\n"
        )
        product_source = (
            "from mcp.server.fastmcp import FastMCP\n"
            "from pydantic import Field\n"
            "from typing import Annotated\n"
            "\n"
            "mcp = FastMCP('compute-product')\n"
            "\n"
            "@mcp.tool()\n"
            "async def compute_product(\n"
            "    a: Annotated[float, Field(description='First operand')],\n"
            "    b: Annotated[float, Field(description='Second operand')],\n"
            ") -> dict:\n"
            "    \"\"\"Return {'product': a * b} for two numeric operands.\"\"\"\n"
            "    return {'product': float(a) * float(b)}\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    mcp.run()\n"
        )

        cap_a = CapabilityDescriptor(
            name="compute-sum",
            description=(
                "Compute the arithmetic sum of two numeric operands. Returns "
                "a dict with key 'sum' (float)."
            ),
            capability_type=CapabilityType.MCP_TOOL,
            domain="arithmetic",
            tags=["math", "arithmetic", "binary"],
            input_schema={"a": "float", "b": "float"},
            output_schema={"sum": "float"},
            metadata={
                "generated": "true",
                "source": "integration_test",
                "source_code": sum_source,
                "pip_packages": "[]",
            },
        )
        cap_b = CapabilityDescriptor(
            name="compute-product",
            description=(
                "Compute the arithmetic product of two numeric operands. "
                "Returns a dict with key 'product' (float)."
            ),
            capability_type=CapabilityType.MCP_TOOL,
            domain="arithmetic",
            tags=["math", "arithmetic", "binary"],
            input_schema={"a": "float", "b": "float"},
            output_schema={"product": "float"},
            metadata={
                "generated": "true",
                "source": "integration_test",
                "source_code": product_source,
                "pip_packages": "[]",
            },
        )
        registry.add(cap_a)
        registry.add(cap_b)
        print(f"\n  Registered: {cap_a.name} ({cap_a.capability_type.value})")
        print(f"  Registered: {cap_b.name} ({cap_b.capability_type.value})")

        # Seed low-reuse invocations + a filler for task-pool diversity.
        all_task_ids = [f"TASK_{i:02d}" for i in range(25)]
        filler = CapabilityDescriptor(
            name="filler-tool-mcp-merge",
            description="Filler for task diversity in the MCP_TOOL merge test",
            capability_type=CapabilityType.MCP_TOOL,
        )
        registry.add(filler)
        _seed_invocations(
            metrics, "filler-tool-mcp-merge", all_task_ids,
            success_rate=1.0, n_per_task=1,
        )
        _seed_invocations(
            metrics, "compute-sum", ["TASK_00"],
            success_rate=0.9, n_per_task=3,
        )
        _seed_invocations(
            metrics, "compute-product", ["TASK_01"],
            success_rate=0.9, n_per_task=3,
        )

        reuse_a = metrics.reuse_rate("compute-sum")
        reuse_b = metrics.reuse_rate("compute-product")
        print(f"\n  compute-sum reuse_rate: {reuse_a:.3f} (threshold: < 0.05)")
        print(f"  compute-product reuse_rate: {reuse_b:.3f} (threshold: < 0.05)")

        results = registry.search("compute-sum", n_results=5)
        similarity = next(
            (score for desc, score in results if desc.name == "compute-product"),
            None,
        )
        print(f"  Semantic similarity: {similarity} (threshold: >= 0.5)")

        print("\n  Running merge(...)...", flush=True)
        try:
            merged_id = merge(
                registry, metrics,
                ["compute-sum", "compute-product"],
            )
        except Exception as exc:
            print(f"\n  FAIL: merge() raised: {exc}")
            return False

        if merged_id is None:
            # Unexpected rollback path — still a PASS if atomic invariant holds.
            print(
                "\n  UNEXPECTED: merge() returned None on deterministic "
                "arithmetic scenario. Verifying atomic rollback invariant."
            )
            rollback_ok = True
            for parent_name in ["compute-sum", "compute-product"]:
                parent = registry.get(parent_name)
                if parent is None or not parent.is_active:
                    print(f"  FAIL: parent '{parent_name}' should still be active")
                    rollback_ok = False
                else:
                    print(f"  OK: parent '{parent_name}' still active")

            rollback_events = metrics._conn.execute(
                "SELECT capability_id, details FROM capability_events "
                "WHERE event_type = 'verification_rollback' ORDER BY occurred_at"
            ).fetchall()
            if not rollback_events:
                print("  FAIL: no verification_rollback event recorded")
                rollback_ok = False

            if rollback_ok:
                print(
                    "\n  PASS (unexpected rollback): atomic invariant held. "
                    "The arithmetic scenario was designed as the MCP_TOOL "
                    "happy-path validator — if this keeps firing, investigate "
                    "why Tier 3 is rejecting deterministic numeric code."
                )
                return True
            else:
                print("\n  FAIL: atomic rollback invariant violated")
                return False

        # --- HAPPY PATH ---
        print("\n  Results:")
        print(f"  Merged capability: {merged_id}")

        merged = registry.get(merged_id)
        if merged is None:
            print(f"  FAIL: Merged capability '{merged_id}' not found in registry")
            return False
        if merged.capability_type != CapabilityType.MCP_TOOL:
            print(
                f"  FAIL: merged capability has type "
                f"{merged.capability_type.value} — expected mcp_tool"
            )
            return False

        merged_source = (merged.metadata or {}).get("source_code", "")
        if not merged_source:
            print(
                "  FAIL: merged capability has no source_code in metadata — "
                "metadata-only merged tools are not valid"
            )
            return False
        print(
            f"  source_code: {len(merged_source)} chars, starts with "
            f"{merged_source[:40]!r}"
        )
        if "FastMCP" not in merged_source:
            print(
                "  FAIL: merged source_code missing FastMCP import — "
                "not a valid MCP tool file"
            )
            return False
        if "@mcp.tool()" not in merged_source:
            print(
                "  FAIL: merged source_code missing @mcp.tool() decorator — "
                "not a valid MCP tool file"
            )
            return False

        # Soft content check: merged tool should subsume both parents' concepts.
        lower = merged_source.lower()
        missing = [
            kw for kw in ("sum", "product")
            if kw not in lower
        ]
        if missing:
            print(
                f"  WARNING: merged source_code does not mention: {missing}. "
                f"The merge LLM may have dropped parent behavior — soft warning."
            )
        else:
            print("  OK: merged source mentions both parents' semantics")

        for parent_name in ["compute-sum", "compute-product"]:
            parent = registry.get(parent_name)
            active = parent.is_active if parent else "NOT FOUND"
            print(f"  Parent '{parent_name}' is_active: {active}")
            if parent and parent.is_active:
                print(f"  FAIL: Parent '{parent_name}' should be deactivated")
                return False

        events = metrics._conn.execute(
            "SELECT capability_id, event_type, details FROM capability_events "
            "ORDER BY occurred_at"
        ).fetchall()
        print(f"\n  Events recorded: {len(events)}")
        if verbose:
            for eid, etype, details in events:
                print(f"    {eid}: {etype} — {details}")

        print("\n  PASS: MCP_TOOL merge completed (happy path)")
        return True


# ---------------------------------------------------------------------------
# AGENT split test (global subagent budget ceiling canary)
# ---------------------------------------------------------------------------


def test_split_agent(verbose: bool = False) -> bool:
    """Split an AGENT capability.

    Closes the AGENT split hole in the Pillar 3 per-type integration
    matrix. ``_split_agents`` produces real agent configs (system_prompt +
    optional subagents) from the parent's ``metadata['agent_config']`` and
    recent failure cases, and enforces a global ceiling: the sum of subagent
    counts across all variants must not exceed ``_MAX_AGENT_SUBAGENT_BUDGET``
    (=8).

    Scenario: ``multi-domain-investigator`` is a solo AGENT (no
    subagents in parent) whose system_prompt covers three
    investigation domains — database, network, and application.
    It has high reuse but mediocre success because the single
    system prompt is stretched thin across all three domains. The
    split target is 2-4 specialized investigators, each focused on
    one domain. As solo variants (no subagents), the budget
    constraint is trivially satisfied.

    Why solo parent (no subagents): keeps the budget test implicit
    — split result could allocate up to 8 subagents total across
    variants. A parent with 3 subagents × 3 variants × 3 subagents
    per variant = 9 exceeds the budget and would be explicitly
    tested as a rejection scenario. For the happy-path canary we
    keep it simple.

    Thresholds: reuse_rate > 0.6, success_rate < 0.7.
    """
    print("\n" + "=" * 70)
    print("  SPLIT TEST — AGENT (solo-parent happy-path canary)")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp_dir:
        registry = _create_fresh_registry(tmp_dir)
        metrics = MetricsStore()

        import json

        parent_system_prompt = (
            "You are a multi-domain incident investigator. When given a "
            "system metrics CSV and an incident report, identify the root "
            "cause and the cascade chain across any of three domains:\n\n"
            "1. Database incidents (connection pool exhaustion, slow "
            "queries, deadlocks)\n"
            "2. Network incidents (packet loss, latency spikes, DNS "
            "resolution failures)\n"
            "3. Application incidents (memory leaks, thread pool "
            "exhaustion, deployment regressions)\n\n"
            "For each incident, read the provided metrics, correlate them "
            "with the incident timeline, and return a structured report "
            "with root_cause_metric, cascade_chain, and n_metrics_investigated."
        )
        parent_agent_config = {
            "system_prompt": parent_system_prompt,
            "subagents": [],  # solo parent
        }

        parent_cap = CapabilityDescriptor(
            name="multi-domain-investigator",
            description=(
                "Investigate incidents across database, network, and "
                "application domains. Identifies root cause metric and "
                "cascade chain from system metrics + incident report."
            ),
            capability_type=CapabilityType.AGENT,
            domain="incident-response",
            tags=["investigation", "incident", "multi-domain"],
            input_schema={
                "metrics_csv_path": "str",
                "incident_yaml_path": "str",
            },
            output_schema={
                "root_cause_metric": "str",
                "cascade_chain": "list[str]",
                "n_metrics_investigated": "int",
            },
            metadata={
                "generated": "true",
                "source": "integration_test",
                "agent_config": json.dumps(parent_agent_config),
            },
        )
        registry.add(parent_cap)
        print(f"\n  Registered: {parent_cap.name} ({parent_cap.capability_type.value})")
        print("  Parent is solo (0 subagents)")

        # Seed high-reuse + low-success invocations.
        all_task_ids = [f"TASK_{i:02d}" for i in range(10)]
        used_task_ids = all_task_ids[:8]  # 8/10 = 80% reuse

        filler = CapabilityDescriptor(
            name="filler-tool-agent-split",
            description="Filler for task diversity in the AGENT split test",
            capability_type=CapabilityType.MCP_TOOL,
        )
        registry.add(filler)
        _seed_invocations(
            metrics, "filler-tool-agent-split", all_task_ids,
            success_rate=1.0, n_per_task=1,
        )
        n_seeded = _seed_invocations(
            metrics, "multi-domain-investigator", used_task_ids,
            success_rate=0.5, n_per_task=5,
        )

        reuse = metrics.reuse_rate("multi-domain-investigator")
        success = metrics.success_rate("multi-domain-investigator")
        print(f"\n  Seeded {n_seeded} invocations across {len(used_task_ids)} task types")
        print(f"  reuse_rate: {reuse:.2f} (threshold: > 0.6)")
        print(f"  success_rate: {success:.2f} (threshold: < 0.7)")

        if reuse <= 0.6 or success >= 0.7:
            print("\n  ERROR: Metrics do not satisfy split thresholds!")
            return False

        # Two valid outcomes under the atomic split flow:
        #   HAPPY: variants registered with non-empty agent_config, parent
        #          deactivated, total subagent count across variants <= 8.
        #   ROLLBACK: gate rejected a variant OR budget exceeded → parent
        #             stays active, no variants in registry.
        print("\n  Running split(...)...", flush=True)
        try:
            new_ids = split(registry, metrics, "multi-domain-investigator")
        except Exception as exc:
            print(f"\n  FAIL: split() raised: {exc}")
            return False

        if new_ids is None:
            # --- ROLLBACK PATH ---
            print(
                "\n  Info: split() returned None — gate/budget rejected one "
                "or more variants. Verifying atomic rollback invariant."
            )
            parent = registry.get("multi-domain-investigator")
            if parent is None or not parent.is_active:
                print(
                    "  FAIL: parent was deactivated despite split rejection — "
                    "atomic rollback violated"
                )
                return False
            print("  OK: parent 'multi-domain-investigator' still active")

            rollback_events = metrics._conn.execute(
                "SELECT capability_id, details FROM capability_events "
                "WHERE event_type = 'split_rollback' "
                "OR event_type = 'verification_rollback' ORDER BY occurred_at"
            ).fetchall()
            print(f"\n  Rollback events: {len(rollback_events)}")
            for cid, details in rollback_events:
                print(f"    {cid}: {details[:100]}")
            if not rollback_events:
                print("  FAIL: no rollback event recorded")
                return False

            print(
                "\n  PASS: gate/budget correctly rejected and atomic "
                "rollback invariant held."
            )
            return True

        # --- HAPPY PATH ---
        print("\n  Results:")
        print(f"  New variants: {new_ids}")

        if len(new_ids) < 2:
            print(f"  FAIL: Expected >= 2 variants, got {len(new_ids)}")
            return False

        parent = registry.get("multi-domain-investigator")
        if parent and parent.is_active:
            print("  FAIL: Parent should be deactivated after split")
            return False
        print("  Parent 'multi-domain-investigator' deactivated: OK")

        # Each variant must carry agent_config, and total subagent count across
        # variants must stay within _MAX_AGENT_SUBAGENT_BUDGET.
        total_subagents = 0
        for vid in new_ids:
            variant = registry.get(vid)
            if variant is None:
                print(f"  FAIL: Variant '{vid}' not found in registry")
                return False
            if variant.capability_type != CapabilityType.AGENT:
                print(
                    f"  FAIL: variant '{vid}' has type "
                    f"{variant.capability_type.value} — expected agent"
                )
                return False

            variant_cfg_raw = (variant.metadata or {}).get("agent_config", "")
            if not variant_cfg_raw:
                print(
                    f"  FAIL: variant '{vid}' has no agent_config in metadata "
                    "— metadata-only agent variants are not valid"
                )
                return False

            try:
                variant_cfg = (
                    json.loads(variant_cfg_raw)
                    if isinstance(variant_cfg_raw, str)
                    else variant_cfg_raw
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"  FAIL: variant '{vid}' agent_config is not valid JSON: {exc}"
                )
                return False

            variant_system_prompt = (variant_cfg.get("system_prompt") or "").strip()
            variant_subagents = variant_cfg.get("subagents") or []
            if not variant_system_prompt:
                print(f"  FAIL: variant '{vid}' has no system_prompt in agent_config")
                return False

            n_sub = len(variant_subagents)
            total_subagents += n_sub
            print(
                f"  Variant '{vid}': system_prompt={len(variant_system_prompt)} "
                f"chars, subagents={n_sub}, active={variant.is_active}"
            )

        # Budget check (mirrors the constraint in _split_agents).
        print(f"\n  Total subagents across variants: {total_subagents} (budget: <= 8)")
        if total_subagents > 8:
            print(
                f"  FAIL: total subagent count {total_subagents} exceeds "
                f"_MAX_AGENT_SUBAGENT_BUDGET=8 — _split_agents should have "
                f"rejected this split before registration"
            )
            return False
        print("  OK: budget constraint satisfied")

        events = metrics._conn.execute(
            "SELECT capability_id, event_type, details FROM capability_events "
            "ORDER BY occurred_at"
        ).fetchall()
        print(f"\n  Events recorded: {len(events)}")
        if verbose:
            for eid, etype, details in events:
                print(f"    {eid}: {etype} — {details}")

        print("\n  PASS: AGENT split completed (happy path)")
        return True


# ---------------------------------------------------------------------------
# AGENT merge test (returned-config budget ceiling canary)
# ---------------------------------------------------------------------------


def test_merge_agent(verbose: bool = False) -> bool:
    """Merge two AGENT capabilities.

    Closes the AGENT merge hole in the Pillar 3 per-type integration
    matrix. ``_merge_agents`` produces a real combined agent_config
    (combined_system_prompt + combined_subagents), enforces the budget ceiling
    (``_MAX_AGENT_SUBAGENT_BUDGET=8`` on returned config) BEFORE
    calling the gate, then routes the result through the standard
    verify_and_register_or_rollback path.

    Scenario: ``csv-data-investigator`` and ``json-data-investigator``
    are two solo AGENTs (no subagents), each specialized for one
    structured-data format. Low reuse on both; semantically similar
    (both are data-investigation agents). The merge target is a
    single ``structured-data-investigator`` that handles both formats
    via one unified system prompt.

    Why solo parents (no subagents): exercises the "simple merge"
    path. Budget check is trivially satisfied when both parents are
    solo. For budget-rejection testing, a separate test with parents
    that together have > 8 subagents would be needed — deferred as
    a Chapter 7 follow-up since that path is covered by unit tests
    (``tests/unit/test_optimizer_operations.py::TestMergeAgents``).

    Thresholds: reuse_rate < 0.05 per parent.
    """
    print("\n" + "=" * 70)
    print("  MERGE TEST — AGENT (solo parents, happy-path)")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp_dir:
        registry = _create_fresh_registry(tmp_dir)
        metrics = MetricsStore()

        import json

        csv_agent_config = {
            "system_prompt": (
                "You are a CSV data investigator. When given a CSV file and "
                "a user question, parse the CSV, summarize its structure "
                "(column names, row count, sample rows), and answer the "
                "question about its contents. Use pandas or the csv module "
                "for parsing. Return a structured dict with 'summary' (str) "
                "and 'answer' (str)."
            ),
            "subagents": [],
        }
        json_agent_config = {
            "system_prompt": (
                "You are a JSON data investigator. When given a JSON file "
                "and a user question, parse the JSON, summarize its "
                "structure (top-level keys, nesting depth, array lengths), "
                "and answer the question about its contents. Use the json "
                "module for parsing. Return a structured dict with "
                "'summary' (str) and 'answer' (str)."
            ),
            "subagents": [],
        }

        cap_a = CapabilityDescriptor(
            name="csv-data-investigator",
            description=(
                "Investigate CSV data files — parse, summarize structure, "
                "answer questions about contents."
            ),
            capability_type=CapabilityType.AGENT,
            domain="data-investigation",
            tags=["data", "investigation", "csv"],
            input_schema={"file_path": "str", "question": "str"},
            output_schema={"summary": "str", "answer": "str"},
            metadata={
                "generated": "true",
                "source": "integration_test",
                "agent_config": json.dumps(csv_agent_config),
            },
        )
        cap_b = CapabilityDescriptor(
            name="json-data-investigator",
            description=(
                "Investigate JSON data files — parse, summarize structure, "
                "answer questions about contents."
            ),
            capability_type=CapabilityType.AGENT,
            domain="data-investigation",
            tags=["data", "investigation", "json"],
            input_schema={"file_path": "str", "question": "str"},
            output_schema={"summary": "str", "answer": "str"},
            metadata={
                "generated": "true",
                "source": "integration_test",
                "agent_config": json.dumps(json_agent_config),
            },
        )
        registry.add(cap_a)
        registry.add(cap_b)
        print(f"\n  Registered: {cap_a.name} ({cap_a.capability_type.value}, solo)")
        print(f"  Registered: {cap_b.name} ({cap_b.capability_type.value}, solo)")

        all_task_ids = [f"TASK_{i:02d}" for i in range(25)]
        filler = CapabilityDescriptor(
            name="filler-tool-agent-merge",
            description="Filler for task diversity in the AGENT merge test",
            capability_type=CapabilityType.MCP_TOOL,
        )
        registry.add(filler)
        _seed_invocations(
            metrics, "filler-tool-agent-merge", all_task_ids,
            success_rate=1.0, n_per_task=1,
        )
        _seed_invocations(
            metrics, "csv-data-investigator", ["TASK_00"],
            success_rate=0.8, n_per_task=3,
        )
        _seed_invocations(
            metrics, "json-data-investigator", ["TASK_01"],
            success_rate=0.8, n_per_task=3,
        )

        reuse_a = metrics.reuse_rate("csv-data-investigator")
        reuse_b = metrics.reuse_rate("json-data-investigator")
        print(f"\n  csv-data-investigator reuse_rate: {reuse_a:.3f} (threshold: < 0.05)")
        print(f"  json-data-investigator reuse_rate: {reuse_b:.3f} (threshold: < 0.05)")

        results = registry.search("csv-data-investigator", n_results=5)
        similarity = next(
            (score for desc, score in results if desc.name == "json-data-investigator"),
            None,
        )
        print(f"  Semantic similarity: {similarity} (threshold: >= 0.5)")

        print("\n  Running merge(...)...", flush=True)
        try:
            merged_id = merge(
                registry, metrics,
                ["csv-data-investigator", "json-data-investigator"],
            )
        except Exception as exc:
            print(f"\n  FAIL: merge() raised: {exc}")
            return False

        if merged_id is None:
            # Rollback leaves parents active and no partial merged artifact.
            print(
                "\n  Info: merge() returned None. Verifying atomic rollback."
            )
            rollback_ok = True
            for parent_name in ["csv-data-investigator", "json-data-investigator"]:
                parent = registry.get(parent_name)
                if parent is None or not parent.is_active:
                    print(f"  FAIL: parent '{parent_name}' should still be active")
                    rollback_ok = False
                else:
                    print(f"  OK: parent '{parent_name}' still active")

            rollback_events = metrics._conn.execute(
                "SELECT capability_id, details FROM capability_events "
                "WHERE event_type = 'verification_rollback' ORDER BY occurred_at"
            ).fetchall()
            if not rollback_events:
                print("  FAIL: no verification_rollback event recorded")
                rollback_ok = False

            if rollback_ok:
                print(
                    "\n  PASS (unexpected rollback): atomic invariant held."
                )
                return True
            return False

        # --- HAPPY PATH ---
        print("\n  Results:")
        print(f"  Merged capability: {merged_id}")

        merged = registry.get(merged_id)
        if merged is None:
            print(f"  FAIL: Merged capability '{merged_id}' not found")
            return False
        if merged.capability_type != CapabilityType.AGENT:
            print(
                f"  FAIL: merged capability has type "
                f"{merged.capability_type.value} — expected agent"
            )
            return False

        merged_cfg_raw = (merged.metadata or {}).get("agent_config", "")
        if not merged_cfg_raw:
            print(
                "  FAIL: merged capability has no agent_config in metadata — "
                "metadata-only merged agents are not valid"
            )
            return False

        try:
            merged_cfg = (
                json.loads(merged_cfg_raw)
                if isinstance(merged_cfg_raw, str)
                else merged_cfg_raw
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL: merged agent_config is not valid JSON: {exc}")
            return False

        merged_system_prompt = (merged_cfg.get("system_prompt") or "").strip()
        merged_subagents = merged_cfg.get("subagents") or []
        if not merged_system_prompt:
            print("  FAIL: merged agent_config has no system_prompt")
            return False

        print(
            f"  system_prompt: {len(merged_system_prompt)} chars, starts with "
            f"{merged_system_prompt[:60]!r}..."
        )
        print(f"  subagents: {len(merged_subagents)}")

        # Budget check (mirrors the constraint enforced in _merge_agents).
        if len(merged_subagents) > 8:
            print(
                f"  FAIL: merged subagent count {len(merged_subagents)} "
                f"exceeds _MAX_AGENT_SUBAGENT_BUDGET=8 — _merge_agents "
                f"should have rejected this merge before the gate"
            )
            return False
        print("  OK: budget constraint satisfied")

        # Soft content check: merged prompt should cover both parents' domains.
        lower = merged_system_prompt.lower()
        missing = [kw for kw in ("csv", "json") if kw not in lower]
        if missing:
            print(
                f"  WARNING: merged system_prompt does not mention: {missing}. "
                f"The merge LLM may have dropped parent behavior — soft warning."
            )
        else:
            print("  OK: merged prompt mentions both parents' formats")

        for parent_name in ["csv-data-investigator", "json-data-investigator"]:
            parent = registry.get(parent_name)
            active = parent.is_active if parent else "NOT FOUND"
            print(f"  Parent '{parent_name}' is_active: {active}")
            if parent and parent.is_active:
                print(f"  FAIL: Parent '{parent_name}' should be deactivated")
                return False

        events = metrics._conn.execute(
            "SELECT capability_id, event_type, details FROM capability_events "
            "ORDER BY occurred_at"
        ).fetchall()
        print(f"\n  Events recorded: {len(events)}")
        if verbose:
            for eid, etype, details in events:
                print(f"    {eid}: {etype} — {details}")

        print("\n  PASS: AGENT merge completed (happy path)")
        return True


# ---------------------------------------------------------------------------
# Full optimization loop test
# ---------------------------------------------------------------------------


def test_full_loop(verbose: bool = False) -> bool:
    """Test the full optimization loop (compute_metrics → identify → apply).

    Seeds a registry with capabilities that will trigger all three operations
    in a single loop pass:

    - **Prune target**: "unused-formatter" — has zero invocations in the window
    - **Split target**: "broad-data-processor" (SKILL) — high reuse (80%),
      low success (50%)
    - **Merge targets**: "csv-row-cleaner" + "tsv-row-cleaner" (SKILLs) —
      low reuse, semantically similar
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

        # 3. Split target: SKILL with high reuse, low success. Production
        # SKILLs carry skill_content, so the seed does too; split needs a real
        # reference body to rewrite into variants.
        split_cap = CapabilityDescriptor(
            name="broad-data-processor",
            description=(
                "Process and transform any structured data file including CSV, "
                "JSON, XML, and YAML formats with validation and cleaning"
            ),
            capability_type=CapabilityType.SKILL,
            domain="data-processing",
            tags=["data", "parsing", "transformation"],
            metadata={
                "skill_content": (
                    "---\n"
                    "name: broad-data-processor\n"
                    "description: Process and transform any structured data "
                    "file including CSV, JSON, XML, and YAML formats with "
                    "validation and cleaning.\n"
                    "---\n\n"
                    "# Broad Data Processor\n\n"
                    "## Steps\n\n"
                    "1. **Detect format** — Inspect the file extension and "
                    "first few bytes to choose the right parser (CSV, JSON, "
                    "XML, or YAML).\n"
                    "2. **Parse** — Read the file using the matching parser. "
                    "Tolerate minor format variations (BOM, trailing "
                    "newlines, mixed quoting).\n"
                    "3. **Validate** — Check that required fields are "
                    "present and non-empty. Flag rows / nodes that fail "
                    "validation.\n"
                    "4. **Clean** — Drop empty rows, normalize whitespace, "
                    "coerce numeric strings to numbers, parse date columns.\n"
                    "5. **Return** — Emit a dict with `row_count` (int), "
                    "`flagged` (list[dict]), and `cleaned_records` "
                    "(list[dict]).\n\n"
                    "## Output\n\n"
                    "Single dict with the three keys above. The optimizer is "
                    "expected to split this skill into format-specific "
                    "variants when invocation data shows it's mostly used "
                    "for one format.\n"
                ),
            },
        )
        registry.add(split_cap)

        # 4. Merge targets: two similar low-reuse SKILLs. Their schemas overlap
        # enough for the Jaccard merge gate, but are not identical. Include
        # skill_content so the merge prompt has real workflow text to combine.
        _row_cleaner_skill_content = (
            "---\n"
            "name: {name}\n"
            "description: {description}\n"
            "---\n\n"
            "# {title}\n\n"
            "## Steps\n\n"
            "1. **Read the file** — Open the file at `file_path` using "
            "the appropriate {format} parser ({format_lib}).\n"
            "2. **Drop nulls** — Remove rows where any cell is empty / "
            "null after stripping whitespace.\n"
            "3. **Deduplicate** — Drop exact duplicate rows, keeping "
            "the first occurrence.\n"
            "4. **Normalize types** — Coerce numeric strings to numbers, "
            "parse date columns to ISO format.\n"
            "5. **Return** — Emit a dict with `clean_rows`, "
            "`null_count`, and `dup_count`.\n"
        )
        merge_a = CapabilityDescriptor(
            name="csv-row-cleaner",
            description=(
                "Clean and validate rows in CSV data files by removing nulls, "
                "deduplicating records, and normalizing column types"
            ),
            capability_type=CapabilityType.SKILL,
            domain="data-processing",
            tags=["csv", "cleaning"],
            input_schema={"file_path": "str", "has_header": "bool"},
            output_schema={
                "clean_rows": "list[dict]",
                "null_count": "int",
                "dup_count": "int",
            },
            metadata={
                "skill_content": _row_cleaner_skill_content.format(
                    name="csv-row-cleaner",
                    description=(
                        "Clean rows in CSV data files by removing nulls, "
                        "deduplicating, and normalizing types."
                    ),
                    title="CSV Row Cleaner",
                    format="CSV",
                    format_lib="`csv.DictReader`",
                ),
            },
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
            input_schema={"file_path": "str", "separator": "str"},
            output_schema={
                "clean_rows": "list[dict]",
                "null_count": "int",
                "dup_count": "int",
            },
            metadata={
                "skill_content": _row_cleaner_skill_content.format(
                    name="tsv-row-cleaner",
                    description=(
                        "Clean rows in TSV data files by removing nulls, "
                        "deduplicating, and normalizing types."
                    ),
                    title="TSV Row Cleaner",
                    format="TSV",
                    format_lib="`csv.DictReader` with `delimiter='\\t'`",
                ),
            },
        )
        registry.add(merge_a)
        registry.add(merge_b)

        # Filler keeps the reuse-rate denominator large.
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

        all_task_ids = [f"TASK_{i:02d}" for i in range(25)]

        # Filler keeps the reuse-rate denominator large.
        _seed_invocations(metrics, "filler-tool", all_task_ids, success_rate=1.0, n_per_task=1)

        # Prune target: NO invocations (will be pruned)
        # unused-formatter has 0 invocations — prune triggers

        # Pinned: also no invocations, but pinned metadata protects it
        # core-tool has 0 invocations — should NOT be pruned

        # Split target: high reuse (80%), low success (50%)
        split_tasks = all_task_ids[:20]  # 20/25 = 80% reuse
        _seed_invocations(
            metrics, "broad-data-processor", split_tasks,
            success_rate=0.5, n_per_task=5,
        )

        # Merge targets: low reuse (1 task each out of 25 = 4%)
        _seed_invocations(metrics, "csv-row-cleaner", ["TASK_00"], success_rate=0.8, n_per_task=3)
        _seed_invocations(metrics, "tsv-row-cleaner", ["TASK_01"], success_rate=0.8, n_per_task=3)

        print("\n  Seeded metrics:")
        for cap_name in ["unused-formatter", "core-tool", "broad-data-processor",
                         "csv-row-cleaner", "tsv-row-cleaner"]:
            rr = metrics.reuse_rate(cap_name)
            sr = metrics.success_rate(cap_name)
            ic = metrics.invocation_count(cap_name)
            print(f"    {cap_name}: invocations={ic}, reuse={rr:.2f}, success={sr:.2f}")

        results = registry.search("csv-row-cleaner", n_results=5)
        similarity = None
        for cap_desc, score in results:
            if cap_desc.name == "tsv-row-cleaner":
                similarity = score
                break
        if similarity:
            print(f"    csv↔tsv similarity: {similarity:.3f}")
        else:
            print("    csv↔tsv similarity: NOT FOUND")

        pre_scores = compute_all_utilities(registry, metrics)
        print("\n  Pre-loop utility scores:")
        for cap_id, score in sorted(pre_scores.items()):
            print(f"    {cap_id}: {score:.4f}")

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

        print("\n  Results:")

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
            print(
                "  FAIL: 'broad-data-processor' should have been split "
                "(reuse=0.80, success=0.50)"
            )
            return False
        variants = split_results["broad-data-processor"]
        # Optimizer split has two valid outcomes: variants pass verification
        # and the parent is deactivated, or one variant fails verification and
        # the optimizer atomically rolls back while leaving the parent active.
        if variants is None:
            # Rollback is valid only if the parent stays active.
            print(
                "  Split returned None — optimizer atomically rolled back "
                "(see WARNING above for the specific variant that failed). "
                "This is a valid optimizer outcome under LLM nondeterminism."
            )
            parent = registry.get("broad-data-processor")
            if parent is None or not parent.is_active:
                print(
                    "  FAIL: rollback contract violated — parent should "
                    "remain ACTIVE after split rollback, but it was "
                    f"{'missing' if parent is None else 'deactivated'}."
                )
                return False
            print("  OK: rollback contract held — parent stays active.")
            split_succeeded = False
        elif len(variants) < 2:
            print(f"  FAIL: Expected >= 2 split variants, got {len(variants)}")
            return False
        else:
            split_succeeded = True

        # Successful splits deactivate the parent and register live variants.
        if split_succeeded:
            parent = registry.get("broad-data-processor")
            if parent and parent.is_active:
                print("  FAIL: 'broad-data-processor' should be deactivated after split")
                return False
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
        # Merge may either register a merged capability or roll back atomically.
        if not merge_results or all(v is None for v in merge_results.values()):
            print(
                "  Merge results empty or rolled back — checking rollback "
                "contract (both parents should remain ACTIVE)..."
            )
            for parent_name in ["csv-row-cleaner", "tsv-row-cleaner"]:
                p = registry.get(parent_name)
                if p is None or not p.is_active:
                    print(
                        f"  FAIL: rollback contract violated — '{parent_name}' "
                        f"should remain ACTIVE after merge rollback, but it was "
                        f"{'missing' if p is None else 'deactivated'}."
                    )
                    return False
            print("  OK: merge rolled back atomically; both parents stay active.")
        else:
            merged_id = list(merge_results.values())[0]
            merged = registry.get(merged_id)
            if merged is None:
                print(f"  FAIL: Merged capability '{merged_id}' not found")
                return False
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
    parser.add_argument(
        "--split-only",
        action="store_true",
        help="Run all split tests (SKILL rollback-canary + MCP_TOOL + AGENT)",
    )
    parser.add_argument(
        "--split-skill-only",
        action="store_true",
        help="Run only the SKILL split test (rollback-path canary)",
    )
    parser.add_argument(
        "--split-mcp-only",
        action="store_true",
        help="Run only the MCP_TOOL split test (coverage verifier happy-path)",
    )
    parser.add_argument(
        "--merge-only",
        action="store_true",
        help="Run all four merge tests (CSV rollback + stats + MCP_TOOL + AGENT)",
    )
    parser.add_argument(
        "--merge-csv-only",
        action="store_true",
        help="Run only the CSV/TSV merge test (SKILL rollback-path canary)",
    )
    parser.add_argument(
        "--merge-stats-only",
        action="store_true",
        help="Run only the math-statistics merge test (SKILL happy-path)",
    )
    parser.add_argument(
        "--merge-mcp-only",
        action="store_true",
        help="Run only the MCP_TOOL merge test (arithmetic happy-path)",
    )
    parser.add_argument(
        "--merge-agent-only",
        action="store_true",
        help="Run only the AGENT merge test (data-investigator happy-path)",
    )
    parser.add_argument(
        "--split-agent-only",
        action="store_true",
        help="Run only the AGENT split test (multi-domain investigator)",
    )
    parser.add_argument("--loop-only", action="store_true", help="Run only full loop test")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=level,
        stream=sys.stderr,
    )

    only_one = (
        args.split_only
        or args.split_skill_only
        or args.split_mcp_only
        or args.split_agent_only
        or args.merge_only
        or args.merge_csv_only
        or args.merge_stats_only
        or args.merge_mcp_only
        or args.merge_agent_only
        or args.loop_only
    )
    run_split_skill = (
        args.split_skill_only or args.split_only or (not only_one)
    )
    run_split_mcp = (
        args.split_mcp_only or args.split_only or (not only_one)
    )
    run_split_agent = (
        args.split_agent_only or args.split_only or (not only_one)
    )
    run_merge_csv = (
        args.merge_csv_only or args.merge_only or (not only_one)
    )
    run_merge_stats = (
        args.merge_stats_only or args.merge_only or (not only_one)
    )
    run_merge_mcp = (
        args.merge_mcp_only or args.merge_only or (not only_one)
    )
    run_merge_agent = (
        args.merge_agent_only or args.merge_only or (not only_one)
    )
    run_loop = args.loop_only or not only_one

    results = {}

    if run_split_skill:
        results["split_skill"] = test_split(verbose=args.verbose)

    if run_split_mcp:
        results["split_mcp"] = test_split_mcp_tool(verbose=args.verbose)

    if run_split_agent:
        results["split_agent"] = test_split_agent(verbose=args.verbose)

    if run_merge_csv:
        results["merge_csv"] = test_merge(verbose=args.verbose)

    if run_merge_stats:
        results["merge_stats"] = test_merge_stats(verbose=args.verbose)

    if run_merge_mcp:
        results["merge_mcp"] = test_merge_mcp_tool(verbose=args.verbose)

    if run_merge_agent:
        results["merge_agent"] = test_merge_agent(verbose=args.verbose)

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
