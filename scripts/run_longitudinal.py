"""Longitudinal optimizer test — 30-50 sequential tasks without snapshot reset.

Tests whether the granularity optimizer improves registry performance when
given accumulated invocation history (vs the single-run evaluation where
it has no history to work with).

Design:
- ONE snapshot restore at the start
- ONE registry + ONE MetricsStore shared across ALL tasks
- Pipeline created with run_optimizer=True for each task
- Tasks cycled from ALL_TASKS in random order (seed-controlled)
- No snapshot reset between tasks — optimizer accumulates real history
- Records written to a separate DuckDB for comparison

Usage::

    uv run scripts/run_longitudinal.py
    uv run scripts/run_longitudinal.py --n-tasks 50 --seed 42
    uv run scripts/run_longitudinal.py --n-tasks 30 --db data/longitudinal.duckdb

Compare with the main evaluation's `full` condition to show optimizer value.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import random  # noqa: E402

logger = logging.getLogger("run_longitudinal")

DATA_DIR = ROOT / "data"
CHROMA_DIR = DATA_DIR / "eval" / "chroma"
MCP_TOOLS_DIR = ROOT / "src" / "mcp_tools"
SKILLS_DIR = DATA_DIR / "eval" / "skills"
DEFAULT_DB = str(DATA_DIR / "eval" / "longitudinal.duckdb")


def main() -> None:
    parser = argparse.ArgumentParser(description="Longitudinal optimizer evaluation")
    parser.add_argument("--n-tasks", type=int, default=40, help="Number of sequential tasks (default: 40)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for task ordering")
    parser.add_argument("--db", type=str, default=DEFAULT_DB, help="DuckDB output path")
    parser.add_argument("--log-file", type=str, default=None, help="Log file path ('auto' for timestamped)")
    args = parser.parse_args()

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
    from src.registry.store import SkillRegistry, seed_registry
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

    run_id = f"longitudinal_{uuid.uuid4().hex[:8]}"
    logger.info("Run ID: %s", run_id)
    logger.info("Task sequence (%d tasks): %s", len(task_sequence), [t.task_id for t in task_sequence])

    # --- ONE snapshot restore ---
    import gc
    try:
        import chromadb
        chromadb.api.shared_system_client.SharedSystemClient.clear_system_cache()
    except Exception:  # noqa: BLE001
        pass
    gc.collect()

    snap_id = take_clean_snapshot(CHROMA_DIR, MCP_TOOLS_DIR, SKILLS_DIR)
    restore_snap = restore_clean_snapshot(CHROMA_DIR, MCP_TOOLS_DIR, SKILLS_DIR)
    logger.info("Snapshot: %s (restored: %s)", snap_id, restore_snap)

    # --- ONE registry, ONE metrics store, ONE loader ---
    registry = SkillRegistry(
        persist_directory=str(CHROMA_DIR),
        collection_name="capabilities",
    )
    seed_registry(registry)
    metrics_store = MetricsStore()
    loader = ToolLoader()

    # --- Evaluation store ---
    eval_store = EvaluationStore(db_path=args.db)

    # --- Track optimizer metrics over time ---
    optimizer_log: list[dict] = []

    # --- Execute tasks sequentially (NO snapshot reset) ---
    records: list[EvaluationRecord] = []
    cumulative_success = 0
    cumulative_total = 0

    from tests.evaluation.cycle_flush import flush_task_cycle_state, now_utc
    from src.orchestrator.tracing import trace_context

    for i, task in enumerate(task_sequence):
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

        _file_fmts = list({fp.suffix.lower().lstrip(".") for fp in task.fixture_paths}) if task.fixture_paths else []
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
        _lf_session = f"longitudinal-{args.seed}"

        invoke_config: dict = {}
        if langfuse_handler is not None:
            invoke_config["callbacks"] = [langfuse_handler]
            invoke_config["metadata"] = {
                "langfuse_session_id": _lf_session,
                # Step 13.7 tagging audit (2026-04-18) — user_id
                # differentiates longitudinal runs from CLI + eval-harness
                # in per-user LangFuse dashboards.
                "langfuse_user_id": "longitudinal-runner",
                "langfuse_tags": _lf_tags,
                "task_id": task.task_id,
                "step": i + 1,
                "total_steps": len(task_sequence),
                "registry_size": registry.count,
            }

        initial_state = TaskState(task_description=task.task_prompt)
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
                final_state: TaskState = pipeline.invoke(initial_state, config=invoke_config)
                result = _extract_result(final_state, task, loader)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Pipeline raised for task %s: %s", task.task_id, exc)
                result = {
                    "gate_passed": False,
                    "output_in_range": False,
                    "failure_reason": "generation_failed",
                    "attempt_count": 0,
                }

            elapsed_ms = int((time.monotonic() - t_start) * 1000)

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
            )

            eval_store.record_run(record)
            records.append(record)

            # Step 18.7 / 18.8 (2026-04-17): persist per-task cycle state
            # (invocation_events + cycle_utility_scores + cycle_events) for
            # Chapter 6 KL convergence + per-type op-count analyses via
            # scripts/analyze_longitudinal.py. Never-raises — flush failure
            # must not break the longitudinal loop.
            flush_task_cycle_state(
                eval_store=eval_store,
                metrics_store=metrics_store,
                registry=registry,
                run_id=run_id,
                condition="longitudinal",
                task_id=task.task_id,
                task_events_cutoff=task_events_cutoff,
            )

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


if __name__ == "__main__":
    main()
