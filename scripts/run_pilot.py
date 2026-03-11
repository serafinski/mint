"""Pilot evaluation runner for Iteration 5.

Runs T01 (seeded retrieval), T07 (moderate JIT), T10 (complex JIT)
through the full pipeline and records results to data/evaluation.duckdb.

Usage::

    uv run scripts/run_pilot.py

Optional flags::

    --condition full|no_verification|no_optimization|static  (default: full)
    --all-tasks   run all 10 tasks instead of pilot subset
    --all-conditions  run all 4 baseline conditions in sequence

Results are printed to stdout and persisted to data/evaluation.duckdb.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path when run as a script
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("run_pilot")

DATA_DIR = ROOT / "data"
CHROMA_DIR = DATA_DIR / "chroma"
MCP_TOOLS_DIR = ROOT / "src" / "mcp_tools"
SKILLS_DIR = ROOT / ".deepagents" / "skills"
DB_PATH = str(DATA_DIR / "evaluation.duckdb")

ALL_CONDITIONS = ["full", "no_verification", "no_optimization", "static"]


def main() -> None:
    parser = argparse.ArgumentParser(description="JustInTimeMAS pilot evaluation runner")
    parser.add_argument(
        "--condition",
        default="full",
        choices=ALL_CONDITIONS,
        help="Baseline condition to run (default: full)",
    )
    parser.add_argument(
        "--all-tasks",
        action="store_true",
        help="Run all 10 tasks instead of pilot subset [T01, T07, T10]",
    )
    parser.add_argument(
        "--all-conditions",
        action="store_true",
        help="Run all 4 conditions in sequence (ignores --condition)",
    )
    parser.add_argument(
        "--n-runs",
        type=int,
        default=1,
        help="Number of runs per condition (default: 1 for pilot; use 8 for full eval)",
    )
    args = parser.parse_args()

    from tests.evaluation.harness import EvaluationHarness, HarnessConfig
    from tests.evaluation.tasks import ALL_TASKS, PILOT_TASKS

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # --- LangFuse tracing (optional, enabled when env vars are set) ---
    langfuse_handler = None
    try:
        from langfuse.langchain import CallbackHandler

        langfuse_handler = CallbackHandler()
        logger.info("LangFuse tracing enabled")
    except Exception as exc:  # noqa: BLE001
        logger.info("LangFuse tracing disabled: %s", exc)

    config = HarnessConfig(
        chroma_dir=CHROMA_DIR,
        mcp_tools_dir=MCP_TOOLS_DIR,
        skills_dir=SKILLS_DIR,
        db_path=DB_PATH,
        n_runs=args.n_runs,
        base_seed=42,
        langfuse_handler=langfuse_handler,
    )

    tasks = ALL_TASKS if args.all_tasks else PILOT_TASKS
    conditions = ALL_CONDITIONS if args.all_conditions else [args.condition]

    with EvaluationHarness(config) as harness:
        logger.info("Taking clean snapshot...")
        snap_id = harness.prepare()
        logger.info("Snapshot: %s", snap_id)

        all_records = []
        for condition in conditions:
            logger.info("=" * 60)
            logger.info("Running condition: %s  (%d task(s), %d run(s))", condition, len(tasks), args.n_runs)
            logger.info("=" * 60)

            if args.n_runs == 1 and not args.all_conditions:
                # Single pilot run
                records = harness.run_pilot(tasks, condition=condition)
            else:
                records = harness.run_condition(condition, tasks)

            # Print summary
            n_success = sum(r.success for r in records)
            n_gate = sum(r.gate_passed for r in records)
            n_output = sum(r.output_in_range for r in records)
            print(f"\n{'='*60}")
            print(f"Condition: {condition}")
            print(f"  Tasks:           {len(records)}")
            print(f"  Gate passed:     {n_gate}/{len(records)}")
            print(f"  Output in range: {n_output}/{len(records)}")
            print(f"  Full success:    {n_success}/{len(records)}")
            print()
            for r in records:
                status = "✓" if r.success else "✗"
                print(
                    f"  {status} {r.task_id}  gate={r.gate_passed}  "
                    f"output={r.output_in_range}  "
                    f"tier={r.verification_tier_reached}  "
                    f"reason={r.failure_reason}  "
                    f"type={r.capability_type}"
                )
            print()
            all_records.extend(records)

        logger.info("Results persisted to %s", DB_PATH)

        # Final summary across all conditions (computed from in-memory records)
        total = len(all_records)
        n_success = sum(r.success for r in all_records)
        n_gate = sum(r.gate_passed for r in all_records)
        print(f"Total records: {total}")
        if total:
            print(f"Overall success rate: {n_success/total:.1%}")
            print(f"Overall gate pass rate: {n_gate/total:.1%}")
        from collections import Counter
        breakdown = Counter(r.failure_reason for r in all_records if r.failure_reason)
        if breakdown:
            print("Failure breakdown:")
            for reason, count in sorted(breakdown.items(), key=lambda x: -x[1]):
                print(f"  {reason}: {count}")

    # Flush LangFuse traces before exit
    if langfuse_handler is not None:
        try:
            from langfuse import get_client

            langfuse = get_client()
            langfuse.flush()
            logger.info("LangFuse traces flushed")
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
