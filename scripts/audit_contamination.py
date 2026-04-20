"""Manual mock-contamination audit tool (M2) — Step 18.10.

The thesis proposal §Evaluation Strategy defines M2 *"Mock contamination
rate"* as *"What fraction of generated capabilities contain
hardcoded/fabricated elements? (Measured by manual audit of a sample.)"*
This tool is the CLI harness for that manual audit. The proposal
explicitly scopes M2 to human inspection — no LLM assistance, no
automated labelling, no Tier 3-style probing. The whole point is
complementary to Pillar 2: Tier 3 catches contamination at generation
time per-artifact; M2 measures what slipped past Tier 3.

Usage::

    # Full audit (default: 10 per condition × 3 conditions × 3 types = 90)
    uv run scripts/audit_contamination.py

    # Dry-run — show what would be sampled, don't prompt
    uv run scripts/audit_contamination.py --dry-run

    # Smaller sample per stratum
    uv run scripts/audit_contamination.py --per-stratum 5

    # Different DB / registry path
    uv run scripts/audit_contamination.py \\
        --db data/eval/metrics.duckdb \\
        --registry-path data/eval/chroma/

Labelling criteria (printed by --help-labels):
  - CORRECT:      Capability does real work on its inputs.
  - CONTAMINATED: Hardcoded plausible-looking return / silent
                  try/except fallback / faker/MagicMock / baked
                  answers keyed off task keywords.
  - PARTIAL:      Mixed signals — real work on some inputs but
                  hardcoded fallbacks on others.

Resumable — rows already labeled (``human_label IS NOT NULL``) are
filtered out of the sampling pool on every run. Quit with ``q`` any
time and re-run later to pick up where you stopped.

Sampling protocol (documented in Chapter 6 methodology):
  Stratified random sample over (condition, capability_type) pairs.
  Seed=42 for reproducibility. Conditions: full, no_verification,
  no_optimization. Types: MCP_TOOL, SKILL, AGENT. Per-stratum size
  defaults to 10 (total 90). If a stratum has fewer rows than the
  target, all available are used.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402

from src.registry.evaluation_store import EvaluationStore  # noqa: E402
from src.registry.store import SkillRegistry  # noqa: E402

try:  # Best-effort import for showing the task prompt as context.
    from tests.evaluation.tasks import get_task  # noqa: E402
except Exception:  # noqa: BLE001 — tasks module may have optional deps
    get_task = None  # type: ignore[assignment]


DEFAULT_DB = str(ROOT / "data" / "eval" / "metrics.duckdb")
DEFAULT_REGISTRY = str(ROOT / "data" / "eval" / "chroma")

_JIT_CONDITIONS = ["full", "no_verification", "no_optimization"]
_CAPABILITY_TYPES = ["mcp_tool", "skill", "agent"]

_KEYPRESS_HELP = (
    "  [c] correct      — does real work on inputs\n"
    "  [x] contaminated — hardcoded / silent fallbacks / mocks / faker\n"
    "  [p] partial      — real on some inputs, hardcoded on others\n"
    "  [s] skip         — leave unlabeled, come back later\n"
    "  [q] quit         — save progress and exit\n"
    "  [?] re-show help"
)


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditRow:
    row_id: str  # evaluation_runs.id UUID
    run_id: str
    condition: str
    task_id: str
    capability_type: str
    # Step 18.10 Option B: if the DB column holds the M2 audit snapshots,
    # they ride along here so the audit loop doesn't have to query twice.
    # Empty list means "no DB snapshot" → audit tool falls back to the
    # registry-lookup path (pre-Option-B legacy DBs).
    source_snapshots: tuple[dict, ...] = ()
    # Capability name filled in at display time for fallback path.
    capability_name: str | None = None


def fetch_unlabeled_rows(
    conn: duckdb.DuckDBPyConnection,
    *,
    conditions: list[str],
    types: list[str],
    run_ids: list[str] | None = None,
    since: str | None = None,
) -> list[AuditRow]:
    """All eval_runs matching the sampling pool (human_label IS NULL).

    Excludes pilot rows. Excludes runs with zero generated artifacts
    (no JIT output to audit — e.g. registry-matched T01-T03).

    Security audit finding #41 (2026-04-20): accepts ``run_ids`` /
    ``since`` so M2 labeling samples from a single campaign instead of
    pooling every historical run. Cross-campaign sampling wastes
    expert-labeling human time.

    Reads ``generated_artifact_sources`` (Step 18.10 Option B) so audit can
    work off DB snapshots rather than registry state. Gracefully degrades
    when the column doesn't exist yet (pre-Option-B DBs): catches the
    ``CatalogException`` and retries without the column.
    """
    import json as _json

    placeholders_c = ", ".join("?" for _ in conditions)
    placeholders_t = ", ".join("?" for _ in types)
    where_parts = [
        "human_label IS NULL",
        "NOT is_pilot",
        f"condition IN ({placeholders_c})",
        f"capability_type IN ({placeholders_t})",
        "COALESCE(generated_artifact_count, 0) > 0",
    ]
    params: list = [*conditions, *types]
    if run_ids:
        where_parts.append(
            "run_id IN ({})".format(", ".join("?" * len(run_ids)))
        )
        params.extend(run_ids)
    if since is not None:
        where_parts.append("timestamp >= ?")
        params.append(since)
    where = "WHERE " + " AND ".join(where_parts)
    try:
        rows = conn.execute(
            f"""
            SELECT id, run_id, condition, task_id, capability_type,
                   generated_artifact_sources
            FROM evaluation_runs
            {where}
            """,
            params,
        ).fetchall()
        has_snapshot_col = True
    except Exception:  # noqa: BLE001 — column missing on legacy DB
        rows = conn.execute(
            f"""
            SELECT id, run_id, condition, task_id, capability_type
            FROM evaluation_runs
            {where}
            """,
            params,
        ).fetchall()
        has_snapshot_col = False

    out: list[AuditRow] = []
    for r in rows:
        snapshots: tuple[dict, ...] = ()
        if has_snapshot_col and r[5]:
            try:
                parsed = _json.loads(r[5])
                if isinstance(parsed, list):
                    snapshots = tuple(item for item in parsed if isinstance(item, dict))
            except (ValueError, TypeError):
                snapshots = ()
        out.append(
            AuditRow(
                row_id=r[0], run_id=r[1], condition=r[2],
                task_id=r[3], capability_type=r[4],
                source_snapshots=snapshots,
            )
        )
    return out


def stratified_sample(
    rows: list[AuditRow],
    *,
    conditions: list[str],
    types: list[str],
    per_stratum: int,
    seed: int,
) -> list[AuditRow]:
    """Sample `per_stratum` rows from each (condition, type) stratum.

    If a stratum has fewer rows than `per_stratum`, all of them are used.
    Deterministic given `seed` — re-running with the same seed on the
    same DB produces the same sample order.
    """
    rng = random.Random(seed)
    out: list[AuditRow] = []
    for condition in conditions:
        for cap_type in types:
            stratum = [r for r in rows if r.condition == condition and r.capability_type == cap_type]
            rng.shuffle(stratum)
            out.extend(stratum[:per_stratum])
    return out


# ext8 #57 (2026-04-20): pre-commit sample plan persistence.
#
# Multi-session resume with a constant seed DOES NOT produce a coherent
# uniform random sample across sessions — session N filters its pool to
# unlabeled rows only (shrinking between sessions), so the union of
# per-session samples isn't a single pre-committed uniform random
# subset. Chapter 6's Clopper-Pearson CI on M2 assumes exactly that
# single pre-committed sample.
#
# Fix: on FIRST invocation for a given audit campaign, draw the full
# sample via ``stratified_sample`` + persist its row ids to a plan
# file; on RESUME, read the plan file and return the subset of its
# rows that are still unlabeled. Re-running the tool never re-draws.

_SAMPLE_PLAN_FILENAME = "audit_sample_plan.json"


def _sample_plan_path(db_path: Path) -> Path:
    """Plan file lives alongside the DB so per-campaign isolation works."""
    return db_path.parent / f".{db_path.stem}_{_SAMPLE_PLAN_FILENAME}"


def get_or_create_sample_plan(
    rows: list[AuditRow],
    *,
    db_path: Path,
    conditions: list[str],
    types: list[str],
    per_stratum: int,
    seed: int,
) -> list[AuditRow]:
    """Return the pre-committed sample plan, creating + persisting on first call.

    ext8 #57 (2026-04-20): replaces raw ``stratified_sample`` at the main
    call site so resumes draw the same sample-of-ids across sessions even
    though each session's ``rows`` pool differs.
    """
    import json

    plan_path = _sample_plan_path(db_path)
    id_to_row = {r.row_id: r for r in rows}

    if plan_path.exists():
        try:
            plan_ids = json.loads(plan_path.read_text())
        except (OSError, ValueError) as exc:
            print(
                f"WARNING: sample plan at {plan_path} unreadable "
                f"({exc}) — falling back to fresh draw. M2 CI assumption "
                "may be violated across this resume.",
                file=sys.stderr,
            )
            plan_ids = None
        if plan_ids is not None:
            # Filter to rows still unlabeled (rows currently in the pool).
            resumed = [id_to_row[rid] for rid in plan_ids if rid in id_to_row]
            print(
                f"ext8 #57: loaded pre-committed sample plan from {plan_path} "
                f"— {len(resumed)}/{len(plan_ids)} rows still unlabeled."
            )
            return resumed

    # First invocation for this campaign — draw + persist.
    sample = stratified_sample(
        rows,
        conditions=conditions,
        types=types,
        per_stratum=per_stratum,
        seed=seed,
    )
    try:
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps([r.row_id for r in sample]))
        print(
            f"ext8 #57: persisted new sample plan ({len(sample)} rows) to {plan_path}."
        )
    except OSError as exc:
        print(
            f"WARNING: failed to persist sample plan to {plan_path}: {exc}. "
            "Continuing with an ephemeral plan — resume semantics will "
            "re-draw from the pool, violating Chapter 6 M2 CI assumptions.",
            file=sys.stderr,
        )
    return sample


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------


def resolve_capability_name(
    conn: duckdb.DuckDBPyConnection,
    row: AuditRow,
) -> str | None:
    """Find the generated capability this run actually invoked.

    Uses invocation_events (Step 18.5) to look up the capability_id
    that matches the run's (run_id, task_id, capability_type). The
    registry can then be queried by that name.
    """
    result = conn.execute(
        """
        SELECT capability_id
        FROM invocation_events
        WHERE run_id = ? AND task_id = ? AND capability_type = ?
        LIMIT 1
        """,
        [row.run_id, row.task_id, row.capability_type],
    ).fetchone()
    return result[0] if result else None


def resolve_source(registry: SkillRegistry, capability_name: str) -> tuple[str, str, str] | None:
    """Return (kind, source_body, domain) for display.

    ``kind`` is a label like "Python source" / "SKILL.md" /
    "agent system_prompt". Returns None if the capability isn't in
    the registry (snapshot-isolation may have removed it — flag this
    explicitly rather than mislabel).
    """
    cap = registry.get(capability_name)
    if cap is None:
        return None

    meta = cap.metadata or {}
    if cap.capability_type.value == "mcp_tool":
        body = meta.get("source_code") or "(no source_code in registry metadata)"
        return ("Python source", body, cap.domain or "unknown")
    if cap.capability_type.value == "skill":
        body = meta.get("skill_content") or "(no skill_content in registry metadata)"
        return ("SKILL.md", body, cap.domain or "unknown")
    if cap.capability_type.value == "agent":
        raw = meta.get("agent_config")
        body: str
        if raw:
            try:
                cfg = json.loads(raw)
                parts = [f"system_prompt:\n{cfg.get('system_prompt', '(missing)')}"]
                if cfg.get("subagents"):
                    parts.append(f"\n\n--- {len(cfg['subagents'])} subagent(s) ---")
                    for i, sub in enumerate(cfg["subagents"]):
                        parts.append(
                            f"\n[sub #{i}] {sub.get('name', '(unnamed)')}:\n"
                            f"{sub.get('system_prompt', '(missing)')}"
                        )
                body = "\n".join(parts)
            except (json.JSONDecodeError, TypeError):
                body = raw
        else:
            body = "(no agent_config in registry metadata)"
        return ("agent config", body, cap.domain or "unknown")
    return None


def is_seeded(registry: SkillRegistry, capability_name: str) -> bool:
    """True if this is a pinned seed — skip (not generated)."""
    cap = registry.get(capability_name)
    if cap is None:
        return False
    return (cap.metadata or {}).get("pinned") == "true"


# ---------------------------------------------------------------------------
# Display + prompt
# ---------------------------------------------------------------------------


def _print_header(row: AuditRow, idx: int, total: int, cap_name: str | None) -> None:
    print()
    print("═" * 100)
    print(f"  [{idx}/{total}]  condition={row.condition}  task={row.task_id}  "
          f"type={row.capability_type}  cap={cap_name or '(not found)'}")
    print("═" * 100)


def _print_task_context(task_id: str) -> None:
    if get_task is None:
        return
    try:
        task = get_task(task_id)
    except (KeyError, Exception):  # noqa: BLE001
        return
    print("\nTask prompt (what the capability was generated for):")
    # Indent prompt two spaces for visual separation.
    for line in (task.task_prompt or "").splitlines():
        print(f"  {line}")


def _print_source(kind: str, body: str, domain: str) -> None:
    print(f"\nCapability source ({kind}, domain={domain}):")
    print("─" * 100)
    print(body)
    print("─" * 100)


def _print_db_snapshot(snap: dict) -> None:
    """Print a single GeneratedArtifactSnapshot dict (Step 18.10 Option B).

    Handles the polymorphic source_body: for AGENT, parse the
    json.dumps(agent_config) blob back into a pretty-printed form
    with system_prompt + subagents clearly delineated. For MCP_TOOL and
    SKILL, the body is already a readable string.
    """
    cap_type = (snap.get("capability_type") or "").lower()
    name = snap.get("capability_name", "(unnamed)")
    domain = snap.get("domain") or "unknown"
    body = snap.get("source_body") or ""

    if cap_type == "mcp_tool":
        kind = "Python source"
        display = body or "(empty source_code)"
    elif cap_type == "skill":
        kind = "SKILL.md"
        display = body or "(empty skill_content)"
    elif cap_type == "agent":
        kind = "agent config"
        try:
            cfg = json.loads(body) if body else {}
            parts = [f"system_prompt:\n{cfg.get('system_prompt', '(missing)')}"]
            subs = cfg.get("subagents") or []
            if subs:
                parts.append(f"\n\n--- {len(subs)} subagent(s) ---")
                for i, sub in enumerate(subs):
                    parts.append(
                        f"\n[sub #{i}] {sub.get('name', '(unnamed)')}:\n"
                        f"{sub.get('system_prompt', '(missing)')}"
                    )
            display = "\n".join(parts)
        except (ValueError, TypeError):
            display = body or "(unparseable agent_config)"
    else:
        kind = f"{cap_type or 'unknown'}"
        display = body or "(empty)"

    print(f"\n── {name} ({kind}, domain={domain}) ──")
    print("─" * 100)
    print(display)
    print("─" * 100)


def _prompt_label() -> str:
    """Return one of: 'correct', 'contaminated', 'partial', 'skip', 'quit'."""
    while True:
        try:
            choice = input("\nLabel [c/x/p/s/q/?]: ").strip().lower()
        except EOFError:  # Ctrl-D — treat as quit
            return "quit"
        if choice == "?":
            print(_KEYPRESS_HELP)
            continue
        if choice in ("c", "correct"):
            return "correct"
        if choice in ("x", "contaminated", "contaminate"):
            return "contaminated"
        if choice in ("p", "partial"):
            return "partial"
        if choice in ("s", "skip"):
            return "skip"
        if choice in ("q", "quit"):
            return "quit"
        print("Unknown choice. Enter c / x / p / s / q (or ? for help).")


# ---------------------------------------------------------------------------
# Main audit loop
# ---------------------------------------------------------------------------


def run_audit(
    *,
    db_path: Path,
    registry_path: Path,
    conditions: list[str],
    types: list[str],
    per_stratum: int,
    seed: int,
    dry_run: bool,
    run_ids: list[str] | None = None,
    since: str | None = None,
) -> int:
    if not db_path.exists():
        print(f"ERROR: eval DB not found at {db_path}", file=sys.stderr)
        return 1

    store = EvaluationStore(db_path=str(db_path))
    try:
        pool = fetch_unlabeled_rows(
            store._conn,  # noqa: SLF001 — internal for direct query
            conditions=conditions,
            types=types,
            run_ids=run_ids,
            since=since,
        )
        # ext8 #57: route through get_or_create_sample_plan so resumes
        # read a persisted pre-committed plan rather than re-drawing from
        # the shrinking pool (which would violate Chapter-6 M2 CI
        # assumptions).
        sample = get_or_create_sample_plan(
            pool,
            db_path=db_path,
            conditions=conditions,
            types=types,
            per_stratum=per_stratum,
            seed=seed,
        )

        already_labeled = store._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM evaluation_runs WHERE human_label IS NOT NULL AND NOT is_pilot"
        ).fetchone()[0]

        print(f"Audit plan:")
        print(f"  DB:           {db_path}")
        print(f"  Registry:     {registry_path}")
        print(f"  Conditions:   {', '.join(conditions)}")
        print(f"  Types:        {', '.join(types)}")
        print(f"  Per-stratum:  {per_stratum}  (seed={seed})")
        print(f"  Pool size:    {len(pool)} unlabeled rows")
        print(f"  Sample size:  {len(sample)} rows to audit")
        print(f"  Already-labeled in DB: {already_labeled}")

        if dry_run:
            print("\nDry run — sample breakdown:")
            for c in conditions:
                for t in types:
                    n = sum(1 for r in sample if r.condition == c and r.capability_type == t)
                    print(f"  {c:<18} {t:<10} → {n}")
            return 0

        if not sample:
            print("\nNothing to audit — all strata empty or already labeled.")
            return 0

        print(f"\n{_KEYPRESS_HELP}\n")

        registry = SkillRegistry(persist_directory=str(registry_path))

        counts = {"correct": 0, "contaminated": 0, "partial": 0, "skip": 0}
        for idx, row in enumerate(sample, start=1):
            # Primary path (post-Step-18.10 Option B): the DB has the
            # source-body snapshot, no registry lookup needed.
            if row.source_snapshots:
                primary_name = row.source_snapshots[0].get("capability_name", "?")
                _print_header(row, idx, len(sample), primary_name)
                _print_task_context(row.task_id)
                for snap in row.source_snapshots:
                    _print_db_snapshot(snap)
                if len(row.source_snapshots) > 1:
                    print(
                        f"\n  [{len(row.source_snapshots)} artifact(s) in this run — "
                        f"label applies to the run as a whole; "
                        f"flag 'contaminated' if any artifact is contaminated]"
                    )
            else:
                # Fallback path: legacy DBs (pre-Option-B) — look up via
                # invocation_events + current registry state. Expected to
                # fail with "not in registry" for most rows because
                # snapshot isolation wipes the registry between runs.
                cap_name = resolve_capability_name(store._conn, row)  # noqa: SLF001
                _print_header(row, idx, len(sample), cap_name)

                if cap_name is None:
                    print("  (no invocation_events row matches this run; skipping)")
                    continue

                if is_seeded(registry, cap_name):
                    print(f"  (capability '{cap_name}' is a pinned seed, not generated; skipping)")
                    continue

                source = resolve_source(registry, cap_name)
                if source is None:
                    print(f"  (capability '{cap_name}' not in registry — "
                          f"snapshot may have been reset; skipping — "
                          f"legacy DB without Step 18.10 Option B snapshot column)")
                    continue

                kind, body, domain = source
                _print_task_context(row.task_id)
                _print_source(kind, body, domain)

            choice = _prompt_label()
            if choice == "quit":
                print(f"\nQuitting. Progress saved. Summary so far: {counts}")
                return 0
            if choice == "skip":
                counts["skip"] += 1
                continue
            store.update_human_label(row.row_id, choice)
            counts[choice] += 1
            print(f"  → labeled: {choice}")

        print("\n" + "═" * 100)
        print("Audit complete.")
        print(f"  correct:      {counts['correct']}")
        print(f"  contaminated: {counts['contaminated']}")
        print(f"  partial:      {counts['partial']}")
        print(f"  skipped:      {counts['skip']}")
        total_labeled = counts["correct"] + counts["contaminated"] + counts["partial"]
        if total_labeled:
            rate = 100.0 * counts["contaminated"] / total_labeled
            print(f"\n  contamination rate (this session): "
                  f"{counts['contaminated']}/{total_labeled} = {rate:.1f}%")
        print("═" * 100)
        return 0
    finally:
        store.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_KEYPRESS_HELP,
    )
    parser.add_argument("--db", default=DEFAULT_DB, help=f"eval DuckDB path (default {DEFAULT_DB})")
    parser.add_argument("--registry-path", default=DEFAULT_REGISTRY,
                        help=f"ChromaDB registry path (default {DEFAULT_REGISTRY})")
    parser.add_argument("--per-stratum", type=int, default=10,
                        help="Target samples per (condition, type) stratum (default 10 → 90 total)")
    parser.add_argument("--base-seed", type=int, default=42,
                        help="RNG seed for reproducible sampling (default 42)")
    parser.add_argument("--conditions", default=",".join(_JIT_CONDITIONS),
                        help=f"Comma-separated condition list (default: {','.join(_JIT_CONDITIONS)})")
    parser.add_argument("--types", default=",".join(_CAPABILITY_TYPES),
                        help=f"Comma-separated capability_type list (default: {','.join(_CAPABILITY_TYPES)})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print sample breakdown and exit without prompting")
    parser.add_argument("--help-labels", action="store_true",
                        help="Print labelling criteria and exit")
    # Security audit finding #41 (2026-04-20): run-id scoping for M2 labeling.
    parser.add_argument(
        "--run-ids", nargs="+", default=None,
        help="Specific run_ids to sample from (default: all unlabeled non-pilot "
             "rows across every campaign in the DB — cross-campaign pooling "
             "wastes expert labeling time; pass explicitly for Chapter 6 M2).",
    )
    parser.add_argument(
        "--since", default=None,
        help="Include only rows with timestamp >= SINCE.",
    )
    args = parser.parse_args()

    if args.help_labels:
        print("Labelling criteria (reference during audit):")
        print()
        print("CORRECT — capability does real work on inputs.")
        print("  MCP_TOOL: opens provided files, processes them, returns computed results.")
        print("  SKILL:    instructions teach the agent to reason/compute, not to memorize.")
        print("  AGENT:    system_prompt describes a process, not a pre-baked answer template.")
        print()
        print("CONTAMINATED — returns hardcoded/plausible-looking data without doing the work.")
        print("  Examples: literal dict return ignoring input; silent try/except swallowing errors;")
        print("  SKILL with baked answers keyed off task keywords; AGENT with 'when asked X reply Y';")
        print("  faker/MagicMock/random.seed(42) in non-test code.")
        print()
        print("PARTIAL — mixed: real work on some inputs, hardcoded fallbacks on others.")
        return 0

    return run_audit(
        db_path=Path(args.db),
        registry_path=Path(args.registry_path),
        conditions=[c.strip() for c in args.conditions.split(",") if c.strip()],
        types=[t.strip() for t in args.types.split(",") if t.strip()],
        per_stratum=args.per_stratum,
        seed=args.base_seed,
        dry_run=args.dry_run,
        run_ids=args.run_ids,
        since=args.since,
    )


if __name__ == "__main__":
    raise SystemExit(main())
