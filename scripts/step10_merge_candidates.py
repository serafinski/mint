"""Step 10 verification experiment — merge-candidate count on regenerated CLI registry.

Per `docs/MASTER_PLAN_2026_04_13.md` §6.1 Thread B Step 10.

**Minimum success criterion:** merge candidate count >= 1 on the regenerated
`data/cli/chroma` state with populated schemas (up from 0 in the pre-Steps-2+3
empty-schema baseline).

Reports:
  1. Schema population rate — % of capabilities with non-empty input_schema /
     output_schema. Confirms the Steps 2+3 wire-up populated schemas on
     freshly generated caps.
  2. Merge candidates — pairs passing BOTH conjuncts (cosine >= 0.5 AND schema
     Jaccard in [0.5, 1.0)), scoped per capability type. Uses the same logic
     as ``identify_candidates_node`` in ``src/optimizer/loop.py``.
  3. Split candidates — caps with high reuse + mediocre success (non-MCP_TOOL).

Usage::

    uv run scripts/step10_merge_candidates.py
    uv run scripts/step10_merge_candidates.py --registry-path data/cli/chroma \
        --metrics-path data/cli/metrics.duckdb

Prerequisites: `mint reset && uv run scripts/test_mint.py` must have populated
the CLI registry (data/cli/chroma) and metrics (data/cli/metrics.duckdb).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Step 13.6 / Fix #1 (2026-04-19): route dev-script LangFuse emissions
# to ``environment=test`` so they don't pollute Phase 4 eval's
# ``environment=default`` data. Must be set BEFORE ``load_dotenv`` so
# ambient .env values can't override. Same pattern as ``tests/conftest.py``.
import os  # noqa: E402
os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", "test")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from src.optimizer.loop import _schema_jaccard  # noqa: E402
from src.registry.metrics import MetricsStore  # noqa: E402
from src.registry.store import SkillRegistry  # noqa: E402
from src.schemas.capability import CapabilityType  # noqa: E402

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--registry-path", default="data/cli/chroma")
    parser.add_argument("--metrics-path", default="data/cli/metrics.duckdb")
    parser.add_argument("--merge-reuse-threshold", type=float, default=0.05,
                        help="Reuse rate below which a cap is eligible for merge (default matches loop.py)")
    parser.add_argument("--split-reuse-threshold", type=float, default=0.6)
    parser.add_argument("--split-success-threshold", type=float, default=0.7)
    parser.add_argument("--cosine-threshold", type=float, default=0.5)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    registry = SkillRegistry(persist_directory=args.registry_path)
    metrics = MetricsStore(db_path=args.metrics_path)

    all_caps = [c for c in registry.list_all() if c.is_active]
    print(f"\n=== Step 10 verification experiment ===")
    print(f"Registry: {args.registry_path}")
    print(f"Metrics:  {args.metrics_path}")
    print(f"Active capabilities: {len(all_caps)}")

    # ---- Part 1: schema population rate
    with_in_schema = sum(1 for c in all_caps if c.input_schema)
    with_out_schema = sum(1 for c in all_caps if c.output_schema)
    print(f"\n--- Schema population ---")
    print(f"  input_schema  populated: {with_in_schema}/{len(all_caps)} "
          f"({100 * with_in_schema / max(1, len(all_caps)):.0f}%)")
    print(f"  output_schema populated: {with_out_schema}/{len(all_caps)} "
          f"({100 * with_out_schema / max(1, len(all_caps)):.0f}%)")

    if args.verbose:
        print("\n  Per-capability schema coverage:")
        for c in all_caps:
            in_keys = sorted(c.input_schema.keys()) if c.input_schema else []
            out_keys = sorted(c.output_schema.keys()) if c.output_schema else []
            print(f"    [{c.capability_type.value:<9}] {c.name}")
            print(f"      in:  {in_keys or '(empty)'}")
            print(f"      out: {out_keys or '(empty)'}")

    # ---- Part 2: merge candidates (same gates as identify_candidates_node)
    low_reuse: list[str] = []
    metrics_cache = {}
    for c in all_caps:
        try:
            m = metrics.compute_granularity_metrics(c.name)
            metrics_cache[c.name] = m
            if m.reuse_rate < args.merge_reuse_threshold:
                low_reuse.append(c.name)
        except Exception as e:  # noqa: BLE001
            logger.warning("compute_granularity_metrics failed for %s: %s", c.name, e)

    print(f"\n--- Merge candidate search ---")
    print(f"  low-reuse pool (reuse_rate < {args.merge_reuse_threshold}): {len(low_reuse)} caps")

    low_reuse_set = set(low_reuse)
    total_caps = registry.count or 1
    n_search = min(max(10, len(low_reuse)), total_caps)
    paired: set[str] = set()
    merge_pairs: list[tuple[str, str, float, float]] = []  # (a, b, cosine, jaccard)

    for cap_id in low_reuse:
        if cap_id in paired:
            continue
        cap = registry.get(cap_id)
        if cap is None:
            continue
        try:
            similar = registry.search(cap.description, n_results=n_search)
        except Exception as e:  # noqa: BLE001
            logger.warning("similarity search failed for %s: %s", cap_id, e)
            continue
        for similar_cap, score in similar:
            partner_id = similar_cap.name
            if (
                partner_id != cap_id
                and partner_id in low_reuse_set
                and partner_id not in paired
                and similar_cap.is_active
                and similar_cap.capability_type == cap.capability_type
                and score >= args.cosine_threshold
            ):
                jaccard = _schema_jaccard(cap, similar_cap)
                if 0.5 <= jaccard < 1.0:
                    merge_pairs.append((cap_id, partner_id, score, jaccard))
                    paired.add(cap_id)
                    paired.add(partner_id)
                    break

    print(f"  merge candidate pairs found: {len(merge_pairs)}")
    for a, b, cos, jac in merge_pairs:
        a_cap = registry.get(a)
        type_str = a_cap.capability_type.value if a_cap else "?"
        print(f"    [{type_str}] {a} <-> {b}")
        print(f"      cosine={cos:.3f} jaccard={jac:.3f}")

    # ---- Part 3: split candidates
    split_cands = []
    for c in all_caps:
        m = metrics_cache.get(c.name)
        if m is None:
            continue
        if (
            m.reuse_rate > args.split_reuse_threshold
            and m.success_rate < args.split_success_threshold
            and c.capability_type != CapabilityType.MCP_TOOL
        ):
            split_cands.append(c.name)

    print(f"\n--- Split candidates (non-MCP_TOOL, high reuse + mediocre success) ---")
    print(f"  split candidates: {len(split_cands)}")
    for cid in split_cands:
        cap = registry.get(cid)
        m = metrics_cache[cid]
        print(f"    [{cap.capability_type.value}] {cid}  "
              f"reuse={m.reuse_rate:.2f} success={m.success_rate:.2f}")

    # ---- Verdict
    print(f"\n=== Verdict ===")
    verdict_ok = len(merge_pairs) >= 1 and with_out_schema > 0
    if verdict_ok:
        print(f"  PASS — {len(merge_pairs)} merge candidate(s) identified with populated schemas.")
        return 0
    else:
        reasons = []
        if with_out_schema == 0:
            reasons.append("no caps with populated output_schema (Steps 2+3 wiring broken?)")
        if len(merge_pairs) == 0:
            reasons.append("zero merge candidate pairs (schema Jaccard never in [0.5, 1.0) within low-reuse pool)")
        print(f"  FAIL — {'; '.join(reasons)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
