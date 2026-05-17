"""Longitudinal optimizer campaign without per-task snapshot reset.

Runs a sequence of evaluation tasks against one shared registry and one
shared MetricsStore so optimizer state can accumulate across cycles. The
resulting DuckDB is the input for the Stage 4 / M6 longitudinal analysis.

Design:
- ONE snapshot restore at the start
- ONE registry + ONE MetricsStore shared across ALL tasks
- Pipeline created with run_optimizer=True for each task
- Tasks cycled from ALL_TASKS in random order (seed-controlled)
- No snapshot reset between tasks — optimizer accumulates real history
- Records written to a separate DuckDB for comparison

Usage::

    uv run scripts/run_longitudinal.py
    uv run scripts/run_longitudinal.py --n-tasks 40 --seed 42 --fresh-db
    uv run scripts/run_longitudinal.py \\
        --n-tasks 3 --db data/eval/longitudinal_smoke.duckdb --fresh-db

Analyze the resulting DB with ``scripts/analyze_longitudinal.py``.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

logger = logging.getLogger("run_longitudinal")

DATA_DIR = ROOT / "data"
CHROMA_DIR = DATA_DIR / "eval" / "chroma"
SKILLS_DIR = DATA_DIR / "eval" / "skills"
DEFAULT_DB = str(DATA_DIR / "eval" / "longitudinal.duckdb")


def main(argv: list[str] | None = None) -> int:
    """Run the longitudinal optimizer campaign.

    ext12 #119 (2026-04-21): added per-task + campaign-level SIGALRM
    watchdogs + exit-code propagation. Pre-fix, ``main()`` returned None
    and the entrypoint was ``main()`` (not ``sys.exit(main())``), so a
    watchdog abort or hanging task couldn't surface as a non-zero exit.

    Returns:
        0: campaign completed with all tasks attempted
        2: output DB already contained rows and --append was not passed
        3: per-task watchdog fired (at least one task exceeded budget)
        5: campaign-level deadline exceeded or partial telemetry flush detected
    """
    parser = argparse.ArgumentParser(description="Longitudinal optimizer evaluation")
    parser.add_argument(
        "--n-tasks",
        type=int,
        default=40,
        help="Number of sequential tasks (default: 40)",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed for task ordering")
    parser.add_argument("--db", type=str, default=DEFAULT_DB, help="DuckDB output path")
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Log file path ('auto' for timestamped)",
    )
    parser.add_argument(
        "--reuse-metrics-store",
        action="store_true",
        default=False,
        help=(
            "Inherit the prior invocation's MetricsStore DB (utility scores, "
            "invocation counts, prune events). Default: fresh DB per run — "
            "each invocation starts from a clean baseline so Day-N decisions "
            "don't depend on Day-(N-1) state. ext11 #113 (2026-04-20): "
            "ext8 #71 made the MetricsStore path persistent for within-run "
            "accumulation but never reset between invocations, silently "
            "contaminating cycle-1 KL + prune thresholds across back-to-back "
            "runs. Pass this flag only for explicit cross-invocation "
            "experiments."
        ),
    )
    # ext12 #119: campaign watchdogs. Pre-fix, a single hung task (Docker
    # stall, LangFuse rate-limit, DeepAgent recursion plateau) froze the
    # whole row-9 campaign indefinitely.
    parser.add_argument(
        "--per-task-budget-seconds", type=int, default=1800,
        help="SIGALRM budget per task (default: 1800s = 30 min). 0 disables "
             "the per-task watchdog. A single hung task will otherwise "
             "block the whole campaign indefinitely. ext12 #119.",
    )
    parser.add_argument(
        "--campaign-budget-hours", type=float, default=12.0,
        help="Total wall-clock budget for the full task sequence (default: "
             "12h for --n-tasks 40). 0 disables the campaign-level deadline. "
             "ext12 #119.",
    )
    # ext15 #175 (2026-04-22): freshness guard. Pre-fix, successive
    # invocations with the default ``--db`` silently accumulated in the
    # same DB → #174 selector stale-fallback risk and cross-campaign
    # pollution. Match run_pilot.py's ext7 #49 contract.
    parser.add_argument(
        "--fresh-db", action="store_true",
        help=(
            "Refuse to run if the --db target already exists with rows. "
            "Use --append to explicitly allow appending to an existing DB. "
            "ext15 #175: one DB = one campaign."
        ),
    )
    parser.add_argument(
        "--append", action="store_true",
        help=(
            "Explicitly allow writing into an existing non-empty DB. "
            "Default: refuse to append; require either --append or a fresh "
            "--db path. ext15 #175."
        ),
    )
    args = parser.parse_args(argv)

    # ext15 #175: freshness guard.
    db_path = Path(args.db)
    if db_path.exists() and db_path.stat().st_size > 0 and not args.append:
        # Check whether the DB has evaluation_runs rows.
        try:
            import duckdb as _duckdb
            with _duckdb.connect(str(db_path), read_only=True) as _conn:
                existing = _conn.execute(
                    "SELECT COUNT(*) FROM evaluation_runs"
                ).fetchone()[0]
        except Exception:  # noqa: BLE001
            existing = 1  # file exists; err on the side of blocking
        if existing > 0:
            logger.error(
                "ext15 #175: --db %s already contains %d evaluation_runs rows. "
                "Refusing to append silently. Pass --append to acknowledge, "
                "or use a fresh path. Each longitudinal run must be one campaign.",
                db_path, existing,
            )
            return 2

    # --- Logging ---
    log_format = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        log_dir = DATA_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        if args.log_file == "auto":
            log_path = log_dir / f"longitudinal_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        else:
            log_path = Path(args.log_file)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
        print(f"Logging to: {log_path}")
    logging.basicConfig(level=logging.INFO, format=log_format, handlers=handlers)

    # --- LangFuse ---
    langfuse_handler = None
    try:
        from langfuse.langchain import CallbackHandler
        langfuse_handler = CallbackHandler()
        logger.info("LangFuse tracing enabled")
    except Exception as exc:  # noqa: BLE001
        logger.info("LangFuse tracing disabled: %s", exc)

    # --- Imports ---
    from src.orchestrator.pipeline import create_pipeline
    from src.orchestrator.tool_loader import ToolLoader
    from src.registry.evaluation_store import EvaluationStore
    from src.registry.metrics import MetricsStore
    from src.registry.store import CapabilityRegistry, seed_registry
    from src.schemas.evaluation import EvaluationRecord
    from src.schemas.task import TaskState
    from tests.evaluation.harness import _extract_result
    from tests.evaluation.snapshots import restore_clean_snapshot, take_clean_snapshot
    from tests.evaluation.tasks import ALL_TASKS

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "eval").mkdir(parents=True, exist_ok=True)

    # --- Build task sequence ---
    rng = random.Random(args.seed)
    task_sequence = []
    while len(task_sequence) < args.n_tasks:
        batch = list(ALL_TASKS)
        rng.shuffle(batch)
        task_sequence.extend(batch)
    task_sequence = task_sequence[: args.n_tasks]

    # tmp.md P2 (2026-04-22): use full uuid4 entropy (128 bits) instead
    # of truncated 8-hex (32 bits). No uniqueness constraint on run_id
    # in the schema; collision would silently merge distinct campaigns.
    run_id = f"longitudinal_{uuid.uuid4().hex}"
    # Security audit finding #87 (2026-04-19, ext9): per-invocation
    # timestamp threaded into the LangFuse session_id so two runs with
    # the same ``--seed`` don't pool their traces into one session in
    # the LangFuse UI. Campaign-scoped (one value per invocation) so
    # every task in the sequence shares the same session.
    _campaign_start = datetime.now().strftime("%Y%m%d-%H%M%S")
    logger.info("Run ID: %s (campaign %s)", run_id, _campaign_start)
    logger.info(
        "Task sequence (%d tasks): %s",
        len(task_sequence),
        [t.task_id for t in task_sequence],
    )

    # --- ONE snapshot restore ---
    import gc
    try:
        import chromadb
        chromadb.api.shared_system_client.SharedSystemClient.clear_system_cache()
    except Exception:  # noqa: BLE001
        pass
    gc.collect()

    # ext7 #51: acquire the advisory lock before mutating shared registry dirs.
    # Refuses (exit 2) if another runner already holds it — concurrent runs
    # corrupt the shared chroma/skills/snapshot state. Retain in a local so the
    # OS releases the lock on process exit.
    from scripts._eval_registry_lock import acquire_runner_lock
    _eval_lock = acquire_runner_lock(
        runner_name="run_longitudinal", data_dir=DATA_DIR / "eval",
    )

    # Security audit finding #31 (2026-04-20): clean the two registry
    # directories BEFORE snapshotting so the "clean baseline" captures
    # an actually-empty state, not whatever a prior run left behind.
    # Seed AFTER the clean to produce a known (seeded-only) baseline;
    # re-snapshot over the seeded state so downstream restores hit
    # that same known baseline.
    #
    # Generated MCP tools are persisted as ``source_code`` metadata in
    # ChromaDB, not as source-tree files. Clean ChromaDB + skills only.
    import shutil as _shutil
    # Safety net for security audit finding #86: refuse to rmtree any
    # directory that lives inside project source.
    _project_source_roots = (
        (ROOT / "src").resolve(),
        (ROOT / "scripts").resolve(),
        (ROOT / "tests").resolve(),
    )
    for _d in (CHROMA_DIR, SKILLS_DIR):
        _resolved = Path(_d).resolve()
        for _root in _project_source_roots:
            if _resolved == _root or _root in _resolved.parents:
                raise RuntimeError(
                    f"Refusing to rmtree {_resolved!s} — inside project "
                    f"source {_root!s}. Security audit finding #86 "
                    f"(ext9). Use a path under data/eval/ for runtime "
                    f"state."
                )
        if _d.exists():
            _shutil.rmtree(_d)
        _d.mkdir(parents=True, exist_ok=True)

    snap_id = take_clean_snapshot(CHROMA_DIR, SKILLS_DIR)
    restore_snap = restore_clean_snapshot(CHROMA_DIR, SKILLS_DIR)
    logger.info("Snapshot: %s (restored: %s)", snap_id, restore_snap)

    # --- ONE registry, ONE metrics store, ONE loader ---
    registry = CapabilityRegistry(
        persist_directory=str(CHROMA_DIR),
        collection_name="capabilities",
    )
    seed_registry(registry)
    # ext8 #71 (2026-04-20): persist MetricsStore WITHIN a run so
    # maintenance_burden (and any other state the Pillar 3 optimizer
    # accumulates across pipeline recompiles) survives. The path is
    # co-located with the longitudinal DB for per-campaign isolation.
    #
    # ext11 #113 (2026-04-20): the previous fix never reset the DB between
    # script invocations, so a second ``uv run scripts/run_longitudinal.py``
    # inherited the prior run's final utility scores, invocation counts,
    # and prune-event history — cycle-1 KL was computed against Day-1's
    # converged state, prune thresholds fired on stale combined counts,
    # and Chapter 6 numbers were silently non-reproducible. Default now:
    # wipe the DB at invocation start. Cross-invocation accumulation is
    # still available via --reuse-metrics-store for explicit experiments.
    _metrics_store_path = Path(DATA_DIR / "eval" / "longitudinal_metrics_store.duckdb")
    if not args.reuse_metrics_store and _metrics_store_path.exists():
        _metrics_store_path.unlink()
        logger.info("ext11 #113: wiped %s for a clean baseline", _metrics_store_path)
    metrics_store = MetricsStore(db_path=str(_metrics_store_path))
    loader = ToolLoader()

    # --- Evaluation store ---
    eval_store = EvaluationStore(db_path=args.db)

    # --- Track optimizer metrics over time ---
    optimizer_log: list[dict] = []

    # --- Execute tasks sequentially (NO snapshot reset) ---
    records: list[EvaluationRecord] = []
    cumulative_success = 0
    cumulative_total = 0

    from scripts._budget_watchdog import BudgetExceeded, budget_watchdog
    from src.orchestrator.tracing import trace_context
    from tests.evaluation.cycle_flush import (
        FlushResult,
        flush_task_cycle_state,
        now_utc,
    )

    # ext12 #119: campaign-level wall-clock deadline. Per-task watchdog alone
    # can't bound total campaign time if many tasks each take just-under-budget.
    campaign_deadline: float | None = (
        time.monotonic() + args.campaign_budget_hours * 3600
        if args.campaign_budget_hours > 0 else None
    )
    per_task_aborted = False
    campaign_deadline_hit = False

    # ext13 #125: accumulate per-task flush results so we can exit 5 when
    # any batch failed silently. Pre-fix, cycle_flush swallowed every
    # persistence error and returned None, hiding partial telemetry that
    # then cascaded into silent-downgrade KL analysis (#124) + silent-M3
    # skips (#126). The runner now owns the detection.
    flush_results: list[FlushResult] = []

    for i, task in enumerate(task_sequence):
        if campaign_deadline is not None and time.monotonic() > campaign_deadline:
            logger.error(
                "ext12 #119: campaign deadline exhausted at task %d/%d; stopping early.",
                i, len(task_sequence),
            )
            campaign_deadline_hit = True
            break
        logger.info("=" * 60)
        logger.info("Task %d/%d: %s", i + 1, len(task_sequence), task.task_id)
        logger.info("=" * 60)

        # Step 18.7 / 18.8 (2026-04-17): capture the pre-task timestamp so
        # the post-task cycle_events flush can filter MetricsStore's
        # capability_events to just this task's events.
        task_events_cutoff = now_utc()
        t_start = time.monotonic()

        # Create pipeline with optimizer enabled
        pipeline = create_pipeline(
            registry,
            loader,
            run_optimizer=True,
            metrics_store=metrics_store,
            verification_enabled=True,
        ).compile()

        _file_fmts = (
            list({fp.suffix.lower().lstrip(".") for fp in task.fixture_paths})
            if task.fixture_paths else []
        )
        _lf_tags = [
            "longitudinal",
            # Treat longitudinal as its own "condition" so the
            # condition filter semantics are consistent with the
            # eval harness baselines.
            "condition:longitudinal",
            task.task_id,
            f"tier:{task.tier}",
            f"step:{i + 1}/{len(task_sequence)}",
            f"static_coverage:{str(task.has_static_coverage).lower()}",
            *(f"format:{fmt}" for fmt in sorted(_file_fmts)),
        ]
        # Security audit finding #87 (2026-04-19, ext9): derive the
        # LangFuse session_id from ``--seed`` AND the campaign start
        # time so two back-to-back invocations with the same seed don't
        # pool their traces into one LangFuse session. ``_campaign_start``
        # is computed once below at the top of main() so every task in
        # a single invocation shares the same session_id.
        _lf_session = f"longitudinal-{args.seed}-{_campaign_start}"

        invoke_config: dict = {}
        if langfuse_handler is not None:
            invoke_config["callbacks"] = [langfuse_handler]
            invoke_config["metadata"] = {
                "langfuse_session_id": _lf_session,
                # Step 13.7 tagging audit (2026-04-18) — user_id
                # differentiates longitudinal runs from eval-harness and
                # ad-hoc programmatic traces in per-user LangFuse dashboards.
                "langfuse_user_id": "longitudinal-runner",
                "langfuse_tags": _lf_tags,
                "task_id": task.task_id,
                "step": i + 1,
                "total_steps": len(task_sequence),
                "registry_size": registry.count,
            }

        # Use the shared builder so task.output_contract flows through
        # identically with the harness path.
        from src.orchestrator.task_state_builder import build_initial_state
        initial_state = build_initial_state(task)
        # Step 13.7 tag-propagation fix (2026-04-19): wrap the pipeline
        # invocation + post-task flush in trace_context so events from
        # ThreadPool workers (verification, adversarial), LangChain @tool
        # callbacks (sandbox_execute), and the flush itself inherit tags.
        with trace_context(
            tags=_lf_tags,
            session_id=_lf_session,
            user_id="longitudinal-runner",
        ):
            try:
                # ext12 #119: SIGALRM-bounded per-task invocation. A hung
                # Docker container / stalled LangFuse API / DeepAgent
                # recursion plateau now raises BudgetExceeded in main
                # thread rather than blocking the campaign indefinitely.
                with budget_watchdog(
                    args.per_task_budget_seconds,
                    name=f"task-{i + 1}-{task.task_id}",
                    logger_=logger,
                ):
                    final_state: TaskState = pipeline.invoke(initial_state, config=invoke_config)
                # Security audit finding #27 (2026-04-20): pass metrics_store
                # so compositional execution records invocations to the
                # shared DB. Pre-fix, default None disabled recording and
                # every longitudinal run produced an empty invocations
                # table — collapsing Gini / KL / utility analyzers to
                # flat-line output.
                result = _extract_result(
                    final_state, task, loader,
                    metrics_store=metrics_store,
                )
                # Step 13.7 Batch B6 parity with harness.py:815-834 —
                # capture LangFuse root trace ID so
                # ``evaluation_runs.langfuse_trace_id`` is populated and
                # Stage 6 trace audits can fetch by ID instead of
                # session-scan. Never raises.
                if langfuse_handler is not None:
                    try:
                        _lf_trace_id = getattr(
                            langfuse_handler, "last_trace_id", None,
                        )
                        if _lf_trace_id is not None:
                            result["langfuse_trace_id"] = _lf_trace_id
                    except Exception:  # noqa: BLE001
                        pass
            except BudgetExceeded as exc:
                logger.error(
                    "ext12 #119: task %s aborted by per-task watchdog: %s. "
                    "Recording synthetic failure record; continuing with "
                    "remaining tasks.",
                    task.task_id, exc,
                )
                per_task_aborted = True
                result = {
                    "gate_passed": False,
                    "output_in_range": False,
                    "failure_reason": "watchdog_abort",
                    "attempt_count": 0,
                }
            except Exception as exc:  # noqa: BLE001
                logger.warning("Pipeline raised for task %s: %s", task.task_id, exc)
                result = {
                    "gate_passed": False,
                    "output_in_range": False,
                    "failure_reason": "generation_failed",
                    "attempt_count": 0,
                }

            elapsed_ms = int((time.monotonic() - t_start) * 1000)

            # tmp.md P0 (2026-04-22): thread every key _extract_result
            # returns through EvaluationRecord. Pre-fix, compositional /
            # artifact / contract / snapshot fields were silently defaulted
            # (chain_length=1, chain_success=False, generated_artifact_count=0,
            # output_shape_mismatch=False, generated_artifact_sources=[]),
            # hiding longitudinal contract and artifact evidence from
            # downstream audits that inspect evaluation_runs directly.
            record = EvaluationRecord(
                run_id=run_id,
                condition="longitudinal",
                task_id=task.task_id,
                task_tier=task.tier,
                attempt_count=result.get("attempt_count", 0),
                capability_type=result.get("capability_type"),
                verification_tier_reached=result.get("verification_tier_reached"),
                gate_passed=result.get("gate_passed", False),
                output_in_range=result.get("output_in_range", False),
                token_cost=0,
                attempt_latency_ms=elapsed_ms,
                failure_reason=result.get("failure_reason", "generation_failed"),
                registry_snapshot_id=snap_id,
                seed=args.seed,
                is_pilot=False,
                # Compositional + artifact + contract metadata from _extract_result:
                required_capability_count=result.get("required_capability_count", 0),
                matched_in_registry_count=result.get("matched_in_registry_count", 0),
                chain_length=result.get("chain_length", 1),
                chain_success=result.get("chain_success", False),
                matched_and_executed_count=result.get("matched_and_executed_count", 0),
                generated_artifact_count=result.get("generated_artifact_count", 0),
                capability_types=result.get("capability_types", []),
                failed_step_index=result.get("failed_step_index"),
                failed_step_name=result.get("failed_step_name"),
                failed_step_reason=result.get("failed_step_reason"),
                output_shape_mismatch=result.get("output_shape_mismatch", False),
                output_shape_missing_keys=result.get("output_shape_missing_keys", []),
                output_shape_missing_files=result.get("output_shape_missing_files", []),
                generated_artifact_sources=result.get("generated_artifact_sources", []),
                langfuse_trace_id=result.get("langfuse_trace_id"),
            )

            eval_store.record_run(record)
            records.append(record)

            # Step 18.7 / 18.8 (2026-04-17): persist per-task cycle state
            # (invocation_events + cycle_utility_scores + cycle_events) for
            # Chapter 6 KL/Gini/convergence analysis via
            # scripts/analyze_longitudinal.py. Never-raises — flush failure
            # is accumulated below and surfaces as a non-zero campaign exit.
            # ext13 #125: capture the FlushResult so partial flush surfaces
            # as a non-zero exit code at campaign end.
            fr = flush_task_cycle_state(
                eval_store=eval_store,
                metrics_store=metrics_store,
                registry=registry,
                run_id=run_id,
                condition="longitudinal",
                task_id=task.task_id,
                task_events_cutoff=task_events_cutoff,
                is_pilot=False,  # ext7 #50 — longitudinal is always non-pilot
            )
            flush_results.append(fr)

        cumulative_total += 1
        if record.success:
            cumulative_success += 1

        # Log optimizer state
        try:
            active_caps = registry.list_all(is_active=True)
            n_active = len(active_caps)
        except Exception:  # noqa: BLE001
            n_active = registry.count()
        optimizer_entry = {
            "task_index": i + 1,
            "task_id": task.task_id,
            "success": record.success,
            "gate_passed": record.gate_passed,
            "output_in_range": record.output_in_range,
            "cumulative_success_rate": cumulative_success / cumulative_total,
            "active_capabilities": n_active,
            "elapsed_ms": elapsed_ms,
        }
        optimizer_log.append(optimizer_entry)

        status = "OK" if record.success else "FAIL"
        logger.info(
            "  %s  gate=%s  output=%s  cumulative=%.1f%%  active_caps=%s  time=%ds",
            status,
            record.gate_passed,
            record.output_in_range,
            cumulative_success / cumulative_total * 100,
            n_active,
            elapsed_ms // 1000,
        )

    # --- Summary ---
    n_success = sum(r.success for r in records)
    n_gate = sum(r.gate_passed for r in records)
    total = len(records)

    print(f"\n{'='*60}")
    print(f"  Longitudinal Optimizer Test — {total} tasks")
    print(f"{'='*60}")
    print(f"  Success:     {n_success}/{total} ({n_success/total*100:.1f}%)")
    print(f"  Gate pass:   {n_gate}/{total} ({n_gate/total*100:.1f}%)")
    print()

    # Show rolling window (first 10 vs last 10)
    if total >= 20:
        first_10 = records[:10]
        last_10 = records[-10:]
        f10_success = sum(r.success for r in first_10)
        l10_success = sum(r.success for r in last_10)
        print(f"  First 10 tasks: {f10_success}/10 ({f10_success*10}%)")
        print(f"  Last 10 tasks:  {l10_success}/10 ({l10_success*10}%)")
        print(f"  Improvement:    {(l10_success - f10_success)*10:+d}pp")

    # Save optimizer log
    log_path = DATA_DIR / "longitudinal_optimizer_log.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(optimizer_log, indent=2))
    print(f"\n  Optimizer log: {log_path}")
    print(f"  Results DB:    {args.db}")

    eval_store.close()

    # Flush LangFuse
    if langfuse_handler is not None:
        try:
            from langfuse import get_client
            get_client().flush()
            logger.info("LangFuse traces flushed")
        except Exception:  # noqa: BLE001
            pass

    # ext13 #125: surface partial flush at campaign end. The per-batch
    # `FlushResult` accumulated across the loop lets us distinguish
    # "telemetry fully landed" from "some batches silently dropped",
    # which directly drives analyze_longitudinal.py's KL/Gini/convergence
    # conclusions downstream.
    n_bad_invocations = sum(1 for r in flush_results if not r.invocations_ok)
    n_bad_utility = sum(1 for r in flush_results if not r.utility_scores_ok)
    n_bad_events = sum(1 for r in flush_results if not r.events_ok)
    partial_flush = bool(
        flush_results and (n_bad_invocations or n_bad_utility or n_bad_events)
    )
    if partial_flush:
        logger.error(
            "ext13 #125: per-task flush partial failures detected. "
            "invocations=%d/%d bad, utility=%d/%d bad, events=%d/%d bad. "
            "M6 analysis (analyze_longitudinal.py) will see partial "
            "telemetry and may fail strict utility-score checks. "
            "Investigate transient causes or re-run this campaign.",
            n_bad_invocations, len(flush_results),
            n_bad_utility, len(flush_results),
            n_bad_events, len(flush_results),
        )

    # ext12 #119: surface watchdog aborts as non-zero exit codes so
    # orchestrators (cron, CI) can distinguish clean completion from
    # campaign-level degradation. Campaign-deadline exit (5) takes
    # precedence over per-task abort (3) because hitting the deadline
    # implies the campaign did not attempt every task in the sequence.
    # ext13 #125: partial-flush exit code (5) shares the slot with
    # campaign-deadline — both are "campaign did not complete cleanly".
    # Precedence: deadline > partial-flush > per-task-abort.
    if campaign_deadline_hit:
        return 5
    if partial_flush:
        return 5
    if per_task_aborted:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
