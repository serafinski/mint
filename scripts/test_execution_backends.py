"""Spot-check the three execution backends.

Runs one SKILL task (T04) and one AGENT task (T16) directly through
`execute_skill_artifact` / `execute_agent_artifact` with a MockArtifact,
bypassing the generator and verification gate. Complements
`test_mint.py` (which exercises the full CLI path including generation).
Use this when the full CLI is broken and you need to isolate whether
the execution backends themselves still work — ~30s vs ~10min for test_mint.

Usage::

    uv run scripts/test_execution_backends.py
    uv run scripts/test_execution_backends.py --skill-only
    uv run scripts/test_execution_backends.py --agent-only
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("test_backends")


def _test_skill() -> bool:
    """Test SKILL backend with T04 (inventory reorder)."""
    from tests.evaluation.execution import (
        GenericResult,
        T04Result,
        _read_fixture_contents,
        execute_skill_artifact,
    )
    from tests.evaluation.tasks import T04

    print(f"\n{'='*60}")
    print("  SKILL BACKEND TEST — T04 (inventory reorder)")
    print(f"{'='*60}")

    # Build a mock artifact with T04's expected skill content
    class MockArtifact:
        skill_content = (
            "---\n"
            "name: inventory-reorder\n"
            "description: Inventory reorder pipeline\n"
            "---\n\n"
            "# Inventory Reorder Pipeline\n\n"
            "## Steps\n"
            "1. Load the inventory CSV and reorder rules YAML.\n"
            "2. For each product, compute days_of_stock = current_stock / daily_usage.\n"
            "3. Identify products where days_of_stock < min_days_of_stock.\n"
            "4. For products needing reorder: reorder_qty = ceil(daily_usage * reorder_days_supply), "
            "order_cost = reorder_qty * unit_cost, capped at max_order_value.\n"
            "5. Sum total_reorder_cost across all products needing reorder.\n"
            "6. Return product_count, items_below_threshold, total_reorder_cost.\n"
        )
        capability_name = "inventory-reorder"

    fixture_contents = _read_fixture_contents(T04)
    print(f"  Fixtures: {list(fixture_contents.keys())}")

    start = time.time()
    result = execute_skill_artifact(MockArtifact(), T04, fixture_contents)
    elapsed = time.time() - start

    print(f"  Time: {elapsed:.1f}s")
    print(f"  Result: {result}")

    if not result:
        print("  FAIL: empty result")
        return False

    valid = T04.validator(result)
    print(f"  Validator: {'PASS' if valid else 'FAIL'}")
    return valid


def _test_agent() -> bool:
    """Test AGENT backend with T16 (root cause diagnosis)."""
    from tests.evaluation.execution import (
        _read_fixture_contents,
        execute_agent_artifact,
    )
    from tests.evaluation.tasks import T16

    print(f"\n{'='*60}")
    print("  AGENT BACKEND TEST — T16 (root cause diagnosis)")
    print(f"{'='*60}")

    class MockArtifact:
        agent_config = {
            "system_prompt": (
                "You are investigating a service degradation incident. "
                "Examine the system metrics and incident report to identify "
                "which metric changed first (root cause) and trace the cascade. "
                "Use write_todos to plan your investigation. "
                "Use sandbox_execute to run analysis code. "
                "Adapt your strategy based on what you find."
            ),
            "is_deep_agent": True,
        }
        skill_content = None
        capability_name = "root-cause-investigator"

    fixture_contents = _read_fixture_contents(T16)
    print(f"  Fixtures: {list(fixture_contents.keys())}")

    start = time.time()
    result = execute_agent_artifact(MockArtifact(), T16, fixture_contents)
    elapsed = time.time() - start

    print(f"  Time: {elapsed:.1f}s")
    print(f"  Result: {result}")

    if not result:
        print("  FAIL: empty result")
        return False

    valid = T16.validator(result)
    print(f"  Validator: {'PASS' if valid else 'FAIL'}")
    return valid


def main() -> int:
    parser = argparse.ArgumentParser(description="Test execution backends")
    parser.add_argument("--skill-only", action="store_true")
    parser.add_argument("--agent-only", action="store_true")
    args = parser.parse_args()

    results: dict[str, bool] = {}

    if not args.agent_only:
        results["SKILL (T04)"] = _test_skill()
    if not args.skill_only:
        results["AGENT (T16)"] = _test_agent()

    print(f"\n{'='*60}")
    print("  RESULTS")
    print(f"{'='*60}")
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
