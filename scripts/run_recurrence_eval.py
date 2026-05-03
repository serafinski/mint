"""Run the Stage 3 supplemental recurrence evaluation track.

Uses existing task-family contracts with generated fixture variants so the
optimizer sees repeated pressure on related tasks. The protocol default is:

- tasks: T04,T11,T13,T14,T15,T20
- variants: 3 per task
- conditions: full,no_verification,no_optimization,static
- runs: 3

That yields 216 recurrence records and writes them to a campaign-scoped DB.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from tests.evaluation.fixtures.generate_recurrence import (  # noqa: E402
    RECURRENCE_MANIFEST_PATH,
    generate_recurrence_fixtures,
)
from tests.evaluation.harness import EvaluationHarness, HarnessConfig  # noqa: E402
from tests.evaluation.recurrence_tasks import build_recurrence_tasks  # noqa: E402

logger = logging.getLogger("run_recurrence_eval")

DATA_DIR = ROOT / "data"
CHROMA_DIR = DATA_DIR / "eval" / "chroma"
MCP_TOOLS_DIR = ROOT / "src" / "mcp_tools"
SKILLS_DIR = DATA_DIR / "eval" / "skills"


class BudgetExceeded(BaseException):
    """Raised by the recurrence condition watchdog when the budget elapses."""


@contextlib.contextmanager
def _condition_budget_watchdog(
    seconds: int,
    *,
    condition: str,
    logger_: logging.Logger,
):
    """Raise ``BudgetExceeded`` if the wrapped condition exceeds ``seconds``."""
    if not hasattr(signal, "SIGALRM"):
        logger_.warning(
            "Condition budget watchdog requested (%ds) but signal.SIGALRM "
            "is unavailable on this platform — skipping watchdog. A hung "
            "recurrence condition will block the campaign without bound.",
            seconds,
        )
        yield
        return

    def _on_alarm(_sig: int, _frame: object) -> None:
        raise BudgetExceeded(
            f"Condition '{condition}' exceeded {seconds}s recurrence budget — "
            f"watchdog fired"
        )

    prev_handler = signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(seconds)
    try:
        t_start = time.monotonic()
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, prev_handler)
        elapsed = time.monotonic() - t_start
        logger_.info(
            "Recurrence condition '%s' watchdog: elapsed %.1fs / budget %ds (%.1f%%)",
            condition,
            elapsed,
            seconds,
            100.0 * elapsed / seconds,
        )


def _parse_csv_arg(value: str | None, default: list[str]) -> list[str]:
    if not value:
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the recurrence evaluation track")
    parser.add_argument("--tasks", type=str, default="T04,T11,T13,T14,T15,T20")
    parser.add_argument("--variants", type=int, default=3)
    parser.add_argument(
        "--conditions",
        type=str,
        default="full,no_verification,no_optimization,static",
    )
    parser.add_argument("--n-runs", type=int, default=3)
    parser.add_argument(
        "--condition-budget-hours",
        type=float,
        default=8.0,
        help="Wall-clock budget per condition (hours). Mirrors Stage 1's "
             "watchdog model so a hung recurrence condition cannot block the "
             "campaign indefinitely. Default 8h; set to 0 to disable.",
    )
    parser.add_argument("--base-seed", type=int, default=42)
    parser.add_argument("--regenerate-fixtures", action="store_true")
    parser.add_argument("--db", type=str, default=None)
    parser.add_argument(
        "--metrics-root",
        type=str,
        default=None,
        help="Root directory for per-(condition,run) MetricsStore files",
    )
    args = parser.parse_args()

    tasks = _parse_csv_arg(args.tasks, ["T04", "T11", "T13", "T14", "T15", "T20"])
    conditions = _parse_csv_arg(
        args.conditions,
        ["full", "no_verification", "no_optimization", "static"],
    )

    log_format = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[logging.StreamHandler()],
    )

    if args.db is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        db_path = DATA_DIR / "eval" / f"metrics_recurrence_{ts}.duckdb"
    else:
        db_path = Path(args.db)
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    metrics_root = (
        Path(args.metrics_root)
        if args.metrics_root
        else DATA_DIR / "eval" / "recurrence_metrics"
    )
    metrics_root.mkdir(parents=True, exist_ok=True)

    if args.regenerate_fixtures or not RECURRENCE_MANIFEST_PATH.exists():
        rows = generate_recurrence_fixtures(
            task_ids=tasks,
            variants_per_task=args.variants,
            base_seed=args.base_seed * 100,
        )
        logger.info("Generated %d recurrence fixture instances", len(rows))

    recurrence_tasks = build_recurrence_tasks(task_ids=tasks)
    if not recurrence_tasks:
        raise RuntimeError("No recurrence tasks were built; check the manifest and --tasks filter.")

    langfuse_handler = None
    try:
        from langfuse.langchain import CallbackHandler
        langfuse_handler = CallbackHandler()
        logger.info("LangFuse tracing enabled")
    except Exception as exc:  # noqa: BLE001
        logger.info("LangFuse tracing disabled: %s", exc)

    total_records = 0
    flush_failures = 0
    watchdog_aborted = False
    abort_condition: str | None = None
    for condition in conditions:
        budget_seconds = max(0, int(args.condition_budget_hours * 3600))
        watchdog_cm = (
            _condition_budget_watchdog(
                budget_seconds,
                condition=condition,
                logger_=logger,
            )
            if budget_seconds > 0
            else contextlib.nullcontext()
        )
        try:
            with watchdog_cm:
                for run_idx in range(args.n_runs):
                    metrics_store_path = (
                        metrics_root / db_path.stem / condition
                        / f"run_{run_idx:02d}.duckdb"
                    )
                    metrics_store_path.parent.mkdir(parents=True, exist_ok=True)
                    if metrics_store_path.exists():
                        metrics_store_path.unlink()

                    config = HarnessConfig(
                        chroma_dir=CHROMA_DIR,
                        mcp_tools_dir=MCP_TOOLS_DIR,
                        skills_dir=SKILLS_DIR,
                        db_path=str(db_path),
                        n_runs=1,
                        base_seed=args.base_seed + run_idx,
                        langfuse_handler=langfuse_handler,
                        metrics_store_path=str(metrics_store_path),
                    )
                    with EvaluationHarness(config) as harness:
                        snap_id = harness.prepare()
                        logger.info(
                            "Running recurrence condition=%s run=%02d "
                            "tasks=%d snapshot=%s metrics_store=%s",
                            condition,
                            run_idx,
                            len(recurrence_tasks),
                            snap_id,
                            metrics_store_path,
                        )
                        records = harness.run_condition(condition, recurrence_tasks)
                        total_records += len(records)
                        flush_failures += sum(
                            0 if fr.all_ok else 1
                            for fr in harness.flush_results
                        )
                        logger.info(
                            "Completed recurrence condition=%s run=%02d success=%d/%d",
                            condition, run_idx, sum(r.success for r in records), len(records),
                        )
        except BudgetExceeded as exc:
            logger.error(
                "Recurrence condition '%s' aborted by watchdog: %s. "
                "Partial per-task records may already be persisted; skipping "
                "the remaining conditions.",
                condition,
                exc,
            )
            watchdog_aborted = True
            abort_condition = condition
            break

    logger.info("Recurrence DB: %s", db_path)
    logger.info("Total recurrence records: %d", total_records)
    if watchdog_aborted:
        logger.error(
            "Recurrence campaign aborted during condition '%s' by watchdog. "
            "Partial data persisted; exiting with code 7.",
            abort_condition,
        )
        return 7
    if flush_failures:
        logger.error("cycle_flush partial failures detected: %d", flush_failures)
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
