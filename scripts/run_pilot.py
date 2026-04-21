"""Evaluation runner.

Runs tasks through the full pipeline and records results to
data/eval/metrics.duckdb. Supports the 20-task testbed (T01-T20) across
four baseline conditions. Use `--all-tasks` for the full eval and
`--tasks T01,T07,...` for targeted runs.

Usage::

    uv run scripts/run_pilot.py                                      # PILOT_TASKS subset
    uv run scripts/run_pilot.py --all-tasks --condition full         # 20 tasks, one condition
    uv run scripts/run_pilot.py --all-tasks --all-conditions --n-runs 5  # full eval (400 records)

Optional flags::

    --condition full|no_verification|no_optimization|static  (default: full)
    --all-tasks        run all 20 tasks instead of PILOT_TASKS subset
    --all-conditions   run all 4 baseline conditions in sequence
    --tasks IDS        comma-separated task IDs (overrides --all-tasks)
    --n-runs N         number of runs per condition (default: 1)
    --condition-budget-hours N  total wall-clock budget per condition (default: 6)

Results are printed to stdout and persisted to data/eval/metrics.duckdb.
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

# Ensure project root is on sys.path when run as a script
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

logger = logging.getLogger("run_pilot")

DATA_DIR = ROOT / "data"
CHROMA_DIR = DATA_DIR / "eval" / "chroma"
MCP_TOOLS_DIR = ROOT / "src" / "mcp_tools"
SKILLS_DIR = DATA_DIR / "eval" / "skills"
# ext7 #49 (2026-04-20): prior hardcoded ``data/eval/metrics.duckdb`` pooled
# every invocation into the same file across sessions. Path is now a CLI
# argument with a timestamped default; legacy hardcode is retained as a
# canonical-location constant used only when the user explicitly asks for it
# via ``--db auto``.
_LEGACY_DB_PATH = str(DATA_DIR / "eval" / "metrics.duckdb")

ALL_CONDITIONS = ["full", "no_verification", "no_optimization", "static"]


# Security audit finding #103 (2026-04-19, ext10): SIGALRM-based
# total-budget watchdog. Wraps a long-running ``harness.run_condition``
# call so a hung Docker container or LangFuse-API stall can't silently
# block an 18-hour campaign indefinitely. Unix-only (signal.SIGALRM is
# unavailable on Windows). On budget exhaustion, raises BudgetExceeded
# in the main thread — the caller is expected to log the partial
# results recorded so far and skip the remaining work.
class BudgetExceeded(RuntimeError):
    """Raised by the run-condition watchdog when the budget elapses."""


@contextlib.contextmanager
def _condition_budget_watchdog(
    seconds: int,
    *,
    condition: str,
    logger_: logging.Logger,
):
    """Raise ``BudgetExceeded`` if the wrapped block exceeds ``seconds``.

    Implementation note: SIGALRM only fires in the main Python thread.
    The harness's per-task loop runs synchronously in main, so an
    interpreter-level stall (LLM call, Docker exec, file I/O) DOES
    interrupt cleanly. A C-extension stall that releases the GIL
    (e.g., a stuck ``subprocess.run``) will fire the signal handler
    only when the GIL returns — usually still within seconds of the
    underlying call returning. Sub-second precision isn't the point
    here; preventing 18-hour silent stalls is.
    """
    if not hasattr(signal, "SIGALRM"):  # Windows fallback: no-op.
        logger_.warning(
            "Condition budget watchdog requested (%ds) but signal.SIGALRM "
            "is unavailable on this platform — skipping watchdog. A hung "
            "Docker container will block the campaign without bound.",
            seconds,
        )
        yield
        return

    def _on_alarm(_sig: int, _frame: object) -> None:
        raise BudgetExceeded(
            f"Condition '{condition}' exceeded {seconds}s budget — "
            f"watchdog fired (security audit ext10 #103)"
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
            "Condition '%s' watchdog: elapsed %.1fs / budget %ds (%.1f%%)",
            condition, elapsed, seconds, 100.0 * elapsed / seconds,
        )


def main() -> int:
    """Run the pilot campaign.

    Returns an integer process exit code:

    - ``0`` — campaign ran to completion and produced at least one record.
    - ``2`` — argparse / CLI validation error (raised via ``sys.exit(2)``).
    - ``4`` — campaign produced zero records with no watchdog abort
      recorded (empty ``--tasks`` filter, harness setup failure, etc.).
    - ``7`` — watchdog fired on a condition (ext10 #103 + ext12 #115).
      Partial records may have been persisted; downstream analyzers
      should gate on run-completeness before consuming. Distinct exit
      code so orchestrators can tell "aborted" from "clean success".

    ext12 #115: previously ``main()`` returned ``None`` unconditionally
    and the module entrypoint used bare ``main()`` — watchdog aborts
    and empty-record campaigns exited 0, indistinguishable from a
    successful campaign at the process-exit layer.
    """
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
        help="Run all 20 tasks instead of PILOT_TASKS subset",
    )
    parser.add_argument(
        "--all-conditions",
        action="store_true",
        help="Run all 4 conditions in sequence (ignores --condition)",
    )
    parser.add_argument(
        "--tasks",
        type=str,
        default=None,
        help="Comma-separated task IDs to run (e.g., T11,T12,T13). Overrides --all-tasks.",
    )
    parser.add_argument(
        "--n-runs",
        type=int,
        default=1,
        help="Number of runs per condition (default: 1 for pilot; use 8 for full eval)",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Path to save log output. If 'auto', generates timestamped file in data/logs/.",
    )
    # ext7 #49 (2026-04-20): campaign isolation. Prior to this fix the DB path
    # was a hardcoded constant — every invocation pooled into the same file.
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="DuckDB path (default: data/eval/metrics_<timestamp>.duckdb — a fresh "
             "campaign-scoped path). Use an explicit path to re-use or wipe an "
             "existing DB (see --fresh-db / --append). Pass 'legacy' to use the "
             "pre-fix shared path (NOT recommended). ext7 #49.",
    )
    parser.add_argument(
        "--fresh-db",
        action="store_true",
        help="Wipe the DB before writing (deletes the file). Required when --db "
             "points at an existing non-empty file, unless --append is also passed.",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append to an existing DB (mutually exclusive with --fresh-db). "
             "Use when resuming a campaign with a different --condition.",
    )
    # ext7 #48 (2026-04-20): condition-order randomization. Default is fixed
    # order (deterministic, easier to debug + reproduce); opt-in when the
    # campaign wants order-randomization as a methodology-purity measure.
    parser.add_argument(
        "--shuffle-conditions",
        action="store_true",
        help="Shuffle the condition execution order with --base-seed. "
             "Default: fixed order (full → no_verification → no_optimization "
             "→ static). ext7 #48 methodology note: use this flag if Chapter "
             "4.5 wants to eliminate time/order confounders (LangFuse latency "
             "drift, Docker cache warmth) on M5 latency metrics.",
    )
    parser.add_argument(
        "--condition-budget-hours",
        type=float,
        default=6.0,
        help="Wall-clock budget per condition (hours). Security audit ext10 "
             "#103: SIGALRM-based watchdog around the harness call so a "
             "hung Docker container or LangFuse-API stall can't silently "
             "block the campaign indefinitely. Default 6h is comfortable "
             "for the 20-task × 5-runs eval; raise for slower hardware. "
             "Set to 0 to disable the watchdog (NOT recommended for "
             "unattended runs). Unix-only — no-op on Windows.",
    )
    # ext14 #148 (2026-04-21): propagate FlushResult signal from the
    # harness path. Pre-fix, Row 8 exited 0 on partial per-task cycle_flush
    # failures because the harness logged a WARNING but did not escalate.
    # Reuses exit code 5 from ext13 #125 (the sibling fix that landed for
    # longitudinal runners).
    parser.add_argument(
        "--allow-partial-flush",
        action="store_true",
        help="ext14 #148: proceed with exit 0 even when per-task cycle_flush "
             "partially failed. Default: refuse with exit 5. Chapter 4.5 "
             "disclosure REQUIRED.",
    )
    args = parser.parse_args()

    # ext7 #49: resolve the DB path + refuse silent pooling.
    if args.fresh_db and args.append:
        print("ERROR: --fresh-db and --append are mutually exclusive.", file=sys.stderr)
        sys.exit(2)

    if args.db is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        db_path = DATA_DIR / "eval" / f"metrics_{ts}.duckdb"
        # Fresh timestamped path — guaranteed empty.
        print(f"ext7 #49: using fresh timestamped DB path: {db_path}")
    elif args.db == "legacy":
        db_path = Path(_LEGACY_DB_PATH)
    else:
        db_path = Path(args.db)

    if db_path.exists() and db_path.stat().st_size > 0:
        if args.fresh_db:
            db_path.unlink()
            print(f"ext7 #49: wiped existing DB at {db_path} per --fresh-db")
        elif not args.append:
            print(
                f"ERROR: DB exists at {db_path} and is non-empty. Pass "
                "--fresh-db to wipe it, --append to add to it, or choose a "
                "different --db path.",
                file=sys.stderr,
            )
            sys.exit(2)

    DB_PATH = str(db_path)

    # ext7 #51: acquire the advisory lock on shared eval registry dirs. Retain
    # the handle in a local so the OS releases on process exit.
    from scripts._eval_registry_lock import acquire_runner_lock
    _eval_lock = acquire_runner_lock(
        runner_name="run_pilot", data_dir=DATA_DIR / "eval",
    )

    from tests.evaluation.harness import EvaluationHarness, HarnessConfig
    from tests.evaluation.tasks import ALL_TASKS, PILOT_TASKS, get_task

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "eval").mkdir(parents=True, exist_ok=True)

    # --- Set up logging (console + optional file) ---
    log_format = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]

    if args.log_file:
        log_dir = DATA_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        if args.log_file == "auto":
            log_path = log_dir / f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        else:
            log_path = Path(args.log_file)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        handlers.append(file_handler)
        print(f"Logging to: {log_path}")

    logging.basicConfig(level=logging.INFO, format=log_format, handlers=handlers)

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

    if args.tasks:
        tasks = [get_task(tid.strip()) for tid in args.tasks.split(",")]
    elif args.all_tasks:
        tasks = ALL_TASKS
    else:
        tasks = PILOT_TASKS
    conditions = ALL_CONDITIONS if args.all_conditions else [args.condition]
    if args.shuffle_conditions and len(conditions) > 1:
        # ext7 #48: deterministic shuffle keyed on base_seed so a given seed
        # always produces the same condition sequence — reproducible +
        # order-free.
        import random
        rng = random.Random(42)  # matches HarnessConfig.base_seed
        rng.shuffle(conditions)
        logger.info(
            "ext7 #48: --shuffle-conditions active — condition order: %s",
            conditions,
        )

    with EvaluationHarness(config) as harness:
        logger.info("Taking clean snapshot...")
        snap_id = harness.prepare()
        logger.info("Snapshot: %s", snap_id)

        all_records = []
        # ext12 #115: track watchdog-abort state so we can surface it as a
        # non-zero process exit code. Pre-fix, a break-from-condition-loop
        # fell through to the final summary + natural return, making a
        # watchdog abort indistinguishable from a clean campaign at exit(0).
        watchdog_aborted = False
        abort_condition: str | None = None
        for condition in conditions:
            logger.info("=" * 60)
            logger.info("Running condition: %s  (%d task(s), %d run(s))", condition, len(tasks), args.n_runs)
            logger.info("=" * 60)

            # Security audit finding #103 (2026-04-19, ext10): wrap the
            # synchronous harness call in a SIGALRM-based watchdog so a
            # hung Docker container or stalled LangFuse API call can't
            # block the campaign indefinitely. Watchdog raises
            # ``BudgetExceeded`` in main thread on timeout; we log and
            # skip the remaining conditions rather than abort the whole
            # run (partial results from other conditions are preserved
            # in the DB by the per-task ``record_run`` calls inside
            # ``_execute_run``).
            budget_seconds = max(0, int(args.condition_budget_hours * 3600))
            watchdog_cm = (
                _condition_budget_watchdog(
                    budget_seconds, condition=condition, logger_=logger,
                )
                if budget_seconds > 0
                else contextlib.nullcontext()
            )
            try:
                with watchdog_cm:
                    if args.n_runs == 1 and not args.all_conditions:
                        # Single pilot run
                        records = harness.run_pilot(tasks, condition=condition)
                    else:
                        records = harness.run_condition(condition, tasks)
            except BudgetExceeded as exc:
                logger.error(
                    "Condition '%s' aborted by watchdog: %s. "
                    "Partial per-task records ARE persisted via record_run "
                    "inside _execute_run; downstream analyzers will see them. "
                    "Skipping the remaining conditions.",
                    condition, exc,
                )
                # ext12 #115: capture abort state so the process exits with
                # a dedicated non-zero code (7) after the final summary.
                watchdog_aborted = True
                abort_condition = condition
                break

            # Print summary
            n_success = sum(r.success for r in records)
            n_gate = sum(r.gate_passed for r in records)
            n_output = sum(r.output_in_range for r in records)
            summary_lines = [
                f"\n{'='*60}",
                f"Condition: {condition}",
                f"  Tasks:           {len(records)}",
                f"  Gate passed:     {n_gate}/{len(records)}",
                f"  Output in range: {n_output}/{len(records)}",
                f"  Full success:    {n_success}/{len(records)}",
                "",
            ]
            for r in records:
                status = "✓" if r.success else "✗"
                summary_lines.append(
                    f"  {status} {r.task_id}  gate={r.gate_passed}  "
                    f"output={r.output_in_range}  "
                    f"tier={r.verification_tier_reached}  "
                    f"reason={r.failure_reason}  "
                    f"type={r.capability_type}"
                )
            summary_lines.append("")
            summary = "\n".join(summary_lines)
            print(summary)
            logger.info("Condition summary:\n%s", summary)
            all_records.extend(records)

        logger.info("Results persisted to %s", DB_PATH)

        # Final summary across all conditions (computed from in-memory records)
        total = len(all_records)
        n_success = sum(r.success for r in all_records)
        n_gate = sum(r.gate_passed for r in all_records)
        final_lines = [f"Total records: {total}"]
        if total:
            final_lines.append(f"Overall success rate: {n_success/total:.1%}")
            final_lines.append(f"Overall gate pass rate: {n_gate/total:.1%}")
        from collections import Counter
        breakdown = Counter(r.failure_reason for r in all_records if r.failure_reason)
        if breakdown:
            final_lines.append("Failure breakdown:")
            for reason, count in sorted(breakdown.items(), key=lambda x: -x[1]):
                final_lines.append(f"  {reason}: {count}")
        final_summary = "\n".join(final_lines)
        print(final_summary)
        logger.info("Final summary:\n%s", final_summary)

        # ext14 #148 (2026-04-21): snapshot the harness's per-task
        # FlushResult list BEFORE the ``with`` block closes the harness.
        # The exit-code gate at the end of main() inspects this copy.
        harness_flush_results = list(harness.flush_results)

    # Flush LangFuse traces before exit
    if langfuse_handler is not None:
        try:
            from langfuse import get_client

            langfuse = get_client()
            langfuse.flush()
            logger.info("LangFuse traces flushed")
        except Exception:  # noqa: BLE001
            pass

    # ext12 #115: surface abort state as a non-zero exit code. Orchestrators
    # (cron, CI, downstream analyzers) can then distinguish:
    #   - 0: clean success with at least one record written
    #   - 4: zero records and no abort (filter/harness misconfig)
    #   - 5: per-task cycle_flush partial failure (ext14 #148)
    #   - 7: watchdog fired mid-campaign (partial data persisted)
    if watchdog_aborted:
        logger.error(
            "Campaign aborted during condition '%s' by watchdog. "
            "Partial data persisted; exiting with code 7 so orchestrators "
            "can detect the abort.",
            abort_condition,
        )
        return 7
    if len(all_records) == 0:
        logger.error(
            "Campaign produced zero records and no watchdog abort was "
            "recorded. Likely causes: empty --tasks filter, DB "
            "misconfiguration, harness setup error. Exiting with code 4."
        )
        return 4

    # ext14 #148 (2026-04-21): per-task cycle_flush health gate. ext13 #125
    # wired FlushResult into the longitudinal runners but not the Row 8
    # harness path. The harness accumulates every task's FlushResult; we
    # snapshotted the list before the ``with`` block closed. Gate exit
    # code on the aggregate here.
    partial_flushes = [fr for fr in harness_flush_results if not fr.all_ok]
    if partial_flushes:
        logger.error(
            "ext14 #148: %d/%d per-task cycle_flush operations partially "
            "failed. Downstream M3 (Row 17), Pillar 3 RQ #2 (Row 18/19) "
            "will see partial telemetry. Consider re-running or inspecting "
            "flush errors in the log. First error sample: %s",
            len(partial_flushes), len(harness_flush_results),
            partial_flushes[0].errors[:3] if partial_flushes[0].errors else "<no errors recorded>",
        )
        if not args.allow_partial_flush:
            logger.error(
                "Exiting with code 5. Pass --allow-partial-flush to "
                "proceed with exit 0 (Chapter 4.5 disclosure REQUIRED)."
            )
            return 5

    return 0


if __name__ == "__main__":
    # ext12 #115: ``sys.exit(main() or 0)`` makes the integer return value
    # of ``main()`` the actual process exit code. ``main() or 0`` only
    # matters for the happy path (``return 0``); any non-zero integer
    # propagates verbatim. Pre-fix, ``if __name__ == "__main__": main()``
    # dropped every return value on the floor and process exit was always
    # 0 regardless of watchdog abort or empty-records outcome.
    sys.exit(main() or 0)
