"""Distribution-shift longitudinal experiment — Step 18.8.

Answers proposal Pillar 3 Key Research Question #3:
    *"Is the converged distribution stable under task distribution shift?"*

**Experimental design.** One snapshot-restore at start, ONE shared
registry + metrics store across both phases. Phase A runs ``--shift-at``
tasks sampled from ``--phase-a-tasks``. At cycle ``shift_at + 1``, a
``distribution_shift`` event is written to ``cycle_events`` marking the
boundary. Phase B runs the remaining ``(--total - --shift-at)`` tasks
sampled from ``--phase-b-tasks``. The registry carries over state from
Phase A; we measure whether it re-converges under the shifted
distribution.

**Expected analyzer output** (via ``scripts/analyze_longitudinal.py``):
- Phase A cycles-to-converge (how fast the registry stabilized on task
  subset A)
- KL spike magnitude at the shift point (how disruptive the shift was)
- Phase B cycles-to-re-converge (how fast it adapted to task subset B)
- Three-tier interpretation: "graceful adaptation" / "sluggish" /
  "collapse".

**Task subset design is the caller's responsibility** — the script is
agnostic. Reasonable designs (Chapter 6 methodology choice):
    (1) Split by tier: A = trivial+moderate, B = complex
    (2) Split by expected_type: A = mcp_tool tasks, B = agent tasks
    (3) Sequential halves: A = T01-T10, B = T11-T20

The task lists must share enough semantic surface for the shift to test
*adaptation* rather than *generation from scratch* — e.g., don't mix
natural-language tasks with numerical tasks.

Usage::

    # Phase A = tier-trivial/moderate (T01-T07, T18), Phase B = complex (T08-T17, T19-T20)
    uv run scripts/run_longitudinal_shift.py \\
        --phase-a-tasks T01,T02,T03,T04,T05,T06,T07,T18 \\
        --phase-b-tasks T08,T09,T10,T11,T12,T13,T14,T15,T16,T17,T19,T20 \\
        --shift-at 16 \\
        --total 32 \\
        --seed 42 \\
        --db data/eval/longitudinal_shifted.duckdb

    # Minimal smoke (fast, uses a handful of tasks)
    uv run scripts/run_longitudinal_shift.py \\
        --phase-a-tasks T01,T05 \\
        --phase-b-tasks T16,T17 \\
        --shift-at 3 \\
        --total 6

Compare with the plain longitudinal (``scripts/run_longitudinal.py``) to
isolate the effect of the shift.
"""

from __future__ import annotations

import argparse
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

logger = logging.getLogger("run_longitudinal_shift")

DATA_DIR = ROOT / "data"
CHROMA_DIR = DATA_DIR / "eval" / "chroma"
MCP_TOOLS_DIR = ROOT / "src" / "mcp_tools"
SKILLS_DIR = DATA_DIR / "eval" / "skills"
DEFAULT_DB = str(DATA_DIR / "eval" / "longitudinal_shifted.duckdb")


def _parse_task_list(spec: str, all_tasks_by_id: dict):
    """Parse a comma-separated task-ID list, mapping to TaskDefinition objects.

    Raises ValueError on unknown task IDs.
    """
    ids = [s.strip() for s in spec.split(",") if s.strip()]
    if not ids:
        raise ValueError(f"empty task list: {spec!r}")
    missing = [tid for tid in ids if tid not in all_tasks_by_id]
    if missing:
        raise ValueError(f"unknown task IDs: {missing}")
    return [all_tasks_by_id[tid] for tid in ids]


def _build_phased_sequence(
    phase_a_tasks: list,
    phase_b_tasks: list,
    shift_at: int,
    total: int,
    rng: random.Random,
):
    """Build the full task sequence by cycling through phase A / phase B.

    Phase A: cycle through ``phase_a_tasks`` (shuffled per batch) until
    ``shift_at`` tasks are accumulated.
    Phase B: cycle through ``phase_b_tasks`` until ``total`` tasks total.

    Returns the sequence as a list of TaskDefinition objects. Length == total.
    """
    sequence: list = []
    # Phase A
    while len(sequence) < shift_at:
        batch = list(phase_a_tasks)
        rng.shuffle(batch)
        sequence.extend(batch)
    sequence = sequence[:shift_at]
    # Phase B
    while len(sequence) < total:
        batch = list(phase_b_tasks)
        rng.shuffle(batch)
        sequence.extend(batch)
    return sequence[:total]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Distribution-shift longitudinal experiment (Pillar 3 RQ #3)",
    )
    parser.add_argument(
        "--phase-a-tasks", type=str, required=True,
        help="Comma-separated task IDs for phase A (pre-shift) — e.g. T01,T02,...,T10",
    )
    parser.add_argument(
        "--phase-b-tasks", type=str, required=True,
        help="Comma-separated task IDs for phase B (post-shift) — e.g. T11,T12,...,T20",
    )
    parser.add_argument(
        "--shift-at", type=int, default=20,
        help="Cycle number at which the shift happens (default: 20). "
             "Phase A = cycles 1..shift_at; Phase B = cycles shift_at+1..total.",
    )
    parser.add_argument(
        "--total", type=int, default=40,
        help="Total number of cycles across both phases (default: 40)",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for per-phase task-order shuffling",
    )
    parser.add_argument(
        "--db", type=str, default=DEFAULT_DB,
        help="DuckDB output path (default: data/eval/longitudinal_shifted.duckdb)",
    )
    parser.add_argument(
        "--log-file", type=str, default=None,
        help="Log file path ('auto' for timestamped, else an explicit path)",
    )
    parser.add_argument(
        "--reuse-metrics-store",
        action="store_true",
        default=False,
        help=(
            "Inherit the prior invocation's MetricsStore DB. Default: "
            "fresh DB per run. ext11 #113 (2026-04-20): match "
            "run_longitudinal.py — ext8 #71 made the path persistent "
            "within a run but never reset across invocations, "
            "contaminating Phase-A/Phase-B adaptation measurements."
        ),
    )
    args = parser.parse_args()

    # --- Validation ---
    if args.shift_at <= 0 or args.shift_at >= args.total:
        print(f"ERROR: --shift-at must satisfy 0 < shift_at < total. "
              f"Got shift_at={args.shift_at}, total={args.total}")
        return 1

    # --- Logging ---
    log_format = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        log_dir = DATA_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        if args.log_file == "auto":
            log_path = log_dir / f"longitudinal_shift_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
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
    from tests.evaluation.cycle_flush import flush_task_cycle_state, now_utc
    from tests.evaluation.harness import _extract_result
    from tests.evaluation.snapshots import restore_clean_snapshot, take_clean_snapshot
    from tests.evaluation.tasks import ALL_TASKS

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "eval").mkdir(parents=True, exist_ok=True)

    # --- Parse task lists ---
    all_by_id = {t.task_id: t for t in ALL_TASKS}
    try:
        phase_a_tasks = _parse_task_list(args.phase_a_tasks, all_by_id)
        phase_b_tasks = _parse_task_list(args.phase_b_tasks, all_by_id)
    except ValueError as exc:
        print(f"ERROR parsing task lists: {exc}")
        return 1

    rng = random.Random(args.seed)
    task_sequence = _build_phased_sequence(
        phase_a_tasks, phase_b_tasks, args.shift_at, args.total, rng,
    )

    run_id = f"longitudinal_shift_{uuid.uuid4().hex[:8]}"
    # Security audit finding #87 (2026-04-19, ext9): add a per-invocation
    # timestamp suffix to the LangFuse session_id so two runs with the
    # same ``--seed`` don't pool their traces into one session.
    from datetime import datetime as _dt
    _campaign_start = _dt.now().strftime("%Y%m%d-%H%M%S")
    logger.info("Run ID: %s (campaign %s)", run_id, _campaign_start)
    logger.info("Phase A tasks: %s", [t.task_id for t in phase_a_tasks])
    logger.info("Phase B tasks: %s", [t.task_id for t in phase_b_tasks])
    logger.info("Shift at cycle: %d (total: %d)", args.shift_at, args.total)
    logger.info(
        "Sequence: Phase A = %s, Phase B = %s",
        [t.task_id for t in task_sequence[: args.shift_at]],
        [t.task_id for t in task_sequence[args.shift_at:]],
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
    # See run_pilot.py / run_longitudinal.py for the same pattern.
    from scripts._eval_registry_lock import acquire_runner_lock
    _eval_lock = acquire_runner_lock(
        runner_name="run_longitudinal_shift", data_dir=DATA_DIR / "eval",
    )

    # Security audit finding #31 (2026-04-20): clean registry directories
    # before snapshotting — see run_longitudinal.py for rationale.
    #
    # Security audit finding #86 (2026-04-19, ext9): MCP_TOOLS_DIR is
    # project source (``src/mcp_tools``) and MUST NOT be rmtree'd —
    # it holds git-tracked reference tools (e.g., ``fibonacci.py``).
    # Clean ChromaDB + skills only. See ``run_longitudinal.py`` for the
    # full rationale and the "MCP Tool Loading Strategy" note in
    # CLAUDE.md that explains why generated tools are ChromaDB-only.
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

    snap_id = take_clean_snapshot(CHROMA_DIR, MCP_TOOLS_DIR, SKILLS_DIR)
    restore_snap = restore_clean_snapshot(CHROMA_DIR, MCP_TOOLS_DIR, SKILLS_DIR)
    logger.info("Snapshot: %s (restored: %s)", snap_id, restore_snap)

    # --- ONE registry, ONE metrics store, ONE loader ---
    registry = SkillRegistry(
        persist_directory=str(CHROMA_DIR),
        collection_name="capabilities",
    )
    seed_registry(registry)
    # ext8 #71 (2026-04-20): persist MetricsStore within a run — see
    # run_longitudinal.py for full rationale.
    # ext11 #113 (2026-04-20): wipe at invocation start by default.
    # Without this, back-to-back runs inherit stale utility scores and
    # prune history, swamping the Phase-A → Phase-B adaptation signal.
    _metrics_store_path = Path(
        DATA_DIR / "eval" / "longitudinal_shift_metrics_store.duckdb"
    )
    if not args.reuse_metrics_store and _metrics_store_path.exists():
        _metrics_store_path.unlink()
        logger.info("ext11 #113: wiped %s for a clean baseline", _metrics_store_path)
    metrics_store = MetricsStore(db_path=str(_metrics_store_path))
    loader = ToolLoader()

    # --- Evaluation store ---
    eval_store = EvaluationStore(db_path=args.db)

    # --- Execute tasks sequentially with the shift marker ---
    records: list[EvaluationRecord] = []
    cumulative_success = 0
    cumulative_total = 0
    shift_marked = False

    for i, task in enumerate(task_sequence):
        cycle = i + 1
        phase_label = "A" if cycle <= args.shift_at else "B"
        logger.info("=" * 60)
        logger.info("Task %d/%d (Phase %s): %s", cycle, len(task_sequence),
                    phase_label, task.task_id)
        logger.info("=" * 60)

        task_events_cutoff = now_utc()
        t_start = time.monotonic()

        pipeline = create_pipeline(
            registry, loader,
            run_optimizer=True,
            metrics_store=metrics_store,
            verification_enabled=True,
        ).compile()

        _file_fmts = list({fp.suffix.lower().lstrip(".") for fp in task.fixture_paths}) if task.fixture_paths else []
        _lf_tags = [
            "longitudinal_shift",
            "condition:longitudinal_shift",
            f"phase:{phase_label}",
            task.task_id,
            f"tier:{task.tier}",
            f"cycle:{cycle}/{args.total}",
            f"shift_at:{args.shift_at}",
            f"static_coverage:{str(task.has_static_coverage).lower()}",
            *(f"format:{fmt}" for fmt in sorted(_file_fmts)),
        ]
        # Security audit finding #87 (2026-04-19, ext9): session_id is
        # {seed}-{campaign_start} so same-seed re-runs are distinct.
        _lf_session = f"longitudinal-shift-{args.seed}-{_campaign_start}"

        invoke_config: dict = {}
        if langfuse_handler is not None:
            invoke_config["callbacks"] = [langfuse_handler]
            invoke_config["metadata"] = {
                "langfuse_session_id": _lf_session,
                # Step 13.7 tagging audit (2026-04-18) — user_id
                # differentiates shift runs from CLI / eval / plain
                # longitudinal in per-user LangFuse dashboards.
                "langfuse_user_id": "longitudinal-shift-runner",
                "langfuse_tags": _lf_tags,
                "task_id": task.task_id,
                "cycle": cycle,
                "phase": phase_label,
                "shift_at": args.shift_at,
                "total": args.total,
                "registry_size": registry.count,
            }

        # ext7 #53 (2026-04-20): use shared builder so task.output_contract
        # flows through and decomposition's writer-append validator fires
        # identically with the harness path.
        from src.orchestrator.task_state_builder import build_initial_state
        initial_state = build_initial_state(task)
        # Step 13.7 tag-propagation fix (2026-04-19): wrap pipeline.invoke +
        # per-task flush + shift-boundary emit in trace_context so events
        # from ThreadPool workers (verification, adversarial), LangChain
        # @tool callbacks (sandbox_execute), and post-pipeline emit sites
        # (cycle_flush.completed, longitudinal.distribution_shift) all
        # inherit tags/session/user.
        from src.orchestrator.tracing import trace_context
        with trace_context(
            tags=_lf_tags,
            session_id=_lf_session,
            user_id="longitudinal-shift-runner",
        ):
            try:
                final_state: TaskState = pipeline.invoke(initial_state, config=invoke_config)
                # Security audit finding #27 (2026-04-20): pass metrics_store
                # so compositional execution records invocations.
                result = _extract_result(
                    final_state, task, loader,
                    metrics_store=metrics_store,
                )
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

            cumulative_total += 1
            if record.success:
                cumulative_success += 1

            # --- Persist per-task cycle state (Step 18.7 flush helper) ---
            flush_task_cycle_state(
                eval_store=eval_store,
                metrics_store=metrics_store,
                registry=registry,
                run_id=run_id,
                condition="longitudinal",
                task_id=task.task_id,
                task_events_cutoff=task_events_cutoff,
                is_pilot=False,  # ext7 #50 — longitudinal is always non-pilot
            )

            # --- Write shift marker at the cycle boundary (Step 18.8) ---
            # The marker is a synthetic cycle_events row with event_type
            # "distribution_shift".
            #
            # Security audit finding #82 (2026-04-19, ext9): explicitly
            # anchor the marker to the LAST Phase A task's
            # ``evaluation_runs.timestamp`` (via ``record.timestamp``).
            # Pre-fix the default-now() timestamp landed strictly AFTER
            # all Phase A rows and BEFORE the first Phase B row, which
            # under ext8 #56's half-open-interval cycle bucketing placed
            # the marker in cycle N+1 instead of cycle N — the analyzer's
            # adaptation verdict was systematically biased toward
            # "sluggish" by one cycle. Anchoring to the Phase A task's
            # own timestamp places the marker inside Phase A's bucket.
            if cycle == args.shift_at and not shift_marked:
                try:
                    details = (
                        f"phase_a=[{','.join(t.task_id for t in phase_a_tasks)}], "
                        f"phase_b=[{','.join(t.task_id for t in phase_b_tasks)}], "
                        f"shift_at={args.shift_at}, total={args.total}"
                    )
                    eval_store.record_cycle_events(
                        run_id=run_id,
                        condition="longitudinal",
                        task_id=task.task_id,  # the LAST Phase A task
                        events=[{
                            "capability_id": "__system__",
                            "event_type": "distribution_shift",
                            "details": details,
                        }],
                        occurred_at=record.timestamp,
                    )
                    shift_marked = True
                    logger.info("=== DISTRIBUTION SHIFT at cycle %d ===", cycle)
                    logger.info("    Phase A complete. Beginning Phase B at next cycle.")

                    # Step 13.7 Batch A5 — mirror the shift boundary into
                    # LangFuse so Chapter 6 RQ #3 adaptation-verdict prose
                    # can cite the event cross-reference. DuckDB row already
                    # written above; this adds the LangFuse-UI marker.
                    try:
                        from src.orchestrator.tracing import emit_langfuse_event
                        emit_langfuse_event(
                            None,
                            "longitudinal.distribution_shift",
                            "DEFAULT",
                            {
                                "cycle": cycle,
                                "shift_at": args.shift_at,
                                "total": args.total,
                                "run_id": run_id,
                                "phase_a_tasks": [t.task_id for t in phase_a_tasks],
                                "phase_b_tasks": [t.task_id for t in phase_b_tasks],
                            },
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("LangFuse distribution_shift emit failed: %s", exc)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to write distribution_shift marker: %s", exc)

        status = "OK" if record.success else "FAIL"
        logger.info(
            "  %s  phase=%s  gate=%s  output=%s  cumulative=%.1f%%  time=%ds",
            status, phase_label,
            record.gate_passed, record.output_in_range,
            cumulative_success / cumulative_total * 100,
            elapsed_ms // 1000,
        )

    # --- Summary ---
    n_success = sum(r.success for r in records)
    n_gate = sum(r.gate_passed for r in records)
    total = len(records)
    phase_a_records = records[: args.shift_at]
    phase_b_records = records[args.shift_at:]
    phase_a_success = sum(r.success for r in phase_a_records)
    phase_b_success = sum(r.success for r in phase_b_records)

    print(f"\n{'=' * 60}")
    print(f"  Longitudinal Shift Experiment — {total} tasks")
    print(f"{'=' * 60}")
    print(f"  Overall success: {n_success}/{total} ({n_success/total*100:.1f}%)")
    print(f"  Overall gate:    {n_gate}/{total} ({n_gate/total*100:.1f}%)")
    print(f"  Phase A (cycles 1-{args.shift_at}):     "
          f"{phase_a_success}/{len(phase_a_records)} "
          f"({phase_a_success/len(phase_a_records)*100:.1f}%)")
    if phase_b_records:
        print(f"  Phase B (cycles {args.shift_at+1}-{total}): "
              f"{phase_b_success}/{len(phase_b_records)} "
              f"({phase_b_success/len(phase_b_records)*100:.1f}%)")
    print()
    print(f"  Results DB: {args.db}")
    print(f"  Analyze:    uv run scripts/analyze_longitudinal.py --db {args.db}")

    eval_store.close()

    # Flush LangFuse
    if langfuse_handler is not None:
        try:
            from langfuse import get_client
            get_client().flush()
            logger.info("LangFuse traces flushed")
        except Exception:  # noqa: BLE001
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
