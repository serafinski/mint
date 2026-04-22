"""Backfill Pillar 3 M3 definition_token_cost for every registered capability.

Step 18.6 introduces ``CapabilityDescriptor.definition_token_cost`` — a static
per-capability tokenization of the definition string the proposal names as M3.
New registrations (JIT pipeline, optimizer, seed) populate the field at
creation time. This script populates the field for pre-18.6 entries already
sitting in the ChromaDB stores.

Idempotent: re-running overwrites with the same value for entries that
already have a token count (since the tokenization is deterministic).

Usage::

    # Dry run — show what would change
    uv run scripts/backfill_definition_token_costs.py

    # Actually write back to ChromaDB
    uv run scripts/backfill_definition_token_costs.py --write

    # Restrict to one store
    uv run scripts/backfill_definition_token_costs.py --write --stores cli
    uv run scripts/backfill_definition_token_costs.py --write --stores eval

Exit codes:
    0: completed; at least one store processed with at least one capability.
    4: every targeted store was missing or empty (tmp.md P1: prevents
       silent green-exit on missing ``data/eval/chroma``).
    5: zero-definition ratio exceeded threshold on --write mode (tmp.md P1:
       refuse to persist failure-derived zeros over prior clean data).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.registry.store import SkillRegistry  # noqa: E402
from src.registry.token_count import count_definition_tokens  # noqa: E402
from src.schemas.capability import CapabilityType  # noqa: E402
from src.schemas.task import GeneratedArtifact  # noqa: E402

logger = logging.getLogger(__name__)

_STORES: dict[str, Path] = {
    "cli": ROOT / "data" / "cli" / "chroma",
    "eval": ROOT / "data" / "eval" / "chroma",
}


def _reconstruct_artifact(descriptor) -> GeneratedArtifact:
    """Reconstruct a throwaway GeneratedArtifact from the descriptor.

    Definition sources live in ``descriptor.metadata`` because ChromaDB is
    our persistence layer — ``source_code`` / ``skill_content`` /
    ``agent_config`` are all stored there by the registration paths.
    """
    meta = descriptor.metadata or {}
    source_code = meta.get("source_code")
    skill_content = meta.get("skill_content")

    agent_config_dict: dict | None = None
    raw_agent_config = meta.get("agent_config")
    if raw_agent_config is not None:
        try:
            agent_config_dict = json.loads(raw_agent_config)
        except (json.JSONDecodeError, TypeError):
            agent_config_dict = {"system_prompt": str(raw_agent_config)}

    return GeneratedArtifact(
        capability_name=descriptor.name,
        capability_type=descriptor.capability_type,
        description=descriptor.description,
        source_code=source_code,
        skill_content=skill_content,
        agent_config=agent_config_dict,
        input_schema=dict(descriptor.input_schema or {}),
        output_schema=dict(descriptor.output_schema or {}),
    )


def _backfill_one_store(
    store_name: str,
    persist_dir: Path,
    *,
    write: bool,
    only_if_empty: bool = True,
    max_zero_ratio: float = 0.25,
) -> dict:
    if not persist_dir.exists():
        print(f"[{store_name}] store not found at {persist_dir} — skipping")
        return {
            "store": store_name,
            "examined": 0,
            "changed": 0,
            "skipped": 0,
            "zero_definition": 0,
            "missing": True,
        }

    registry = SkillRegistry(persist_directory=str(persist_dir))
    caps = registry.list_all(is_active=None)  # include deactivated too

    # tmp.md P1 (2026-04-22): gather planned updates first, then enforce
    # atomic-ish write. The ChromaDB layer has no transaction API, but
    # we batch the updates and abort the whole store if more than
    # ``max_zero_ratio`` of them would persist a zero — likely a
    # failure-derived value, not legitimate empty definitions.
    planned: list[tuple[str, int, int]] = []  # (cap_name, old, new)
    zero_definition = 0
    for cap in caps:
        artifact = _reconstruct_artifact(cap)
        new_cost = count_definition_tokens(artifact)
        existing = int(getattr(cap, "definition_token_cost", 0) or 0)
        if new_cost == 0:
            zero_definition += 1
        planned.append((cap.name, existing, new_cost))

    examined = len(planned)
    # Filter to actual changes.
    def _should_change(old: int, new: int) -> bool:
        if old == new and old != 0:
            return False
        # tmp.md P1: only-if-empty provenance guard. When the existing
        # value is non-zero and the new value is zero (likely
        # tokenizer/serialization failure), keep the old value.
        if old > 0 and new == 0:
            return False
        return new != old or new != 0

    changes = [(n, o, nw) for (n, o, nw) in planned if _should_change(o, nw)]
    skipped = examined - len(changes)

    # tmp.md P1: zero-definition guard. If writing would produce zeros for
    # more than max_zero_ratio of examined entries, abort. This is a
    # conservative safety valve against tokenizer or fixture breakage.
    if write and examined > 0:
        new_zeros = sum(1 for (_n, _o, nw) in changes if nw == 0)
        ratio = new_zeros / examined if examined else 0.0
        if ratio > max_zero_ratio:
            logger.error(
                "[%s] refusing to persist: %.1f%% of entries would be "
                "zeroed (threshold %.1f%%). Pass --allow-high-zero-ratio "
                "to override. tmp.md P1.",
                store_name, ratio * 100, max_zero_ratio * 100,
            )
            return {
                "store": store_name,
                "examined": examined,
                "changed": 0,
                "skipped": examined,
                "zero_definition": zero_definition,
                "aborted_high_zero_ratio": True,
            }

    changed = 0
    if write and changes:
        # Best-effort batch: we apply all updates in sequence; if one
        # raises, we stop and report so the caller can investigate. We
        # can't rollback ChromaDB writes, but we at least surface the
        # partial-write condition instead of silently exiting 0.
        try:
            for cap_name, _old, new_cost in changes:
                registry.update(cap_name, definition_token_cost=new_cost)
                changed += 1
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[%s] write failed after %d/%d updates: %s",
                store_name, changed, len(changes), exc,
            )
            return {
                "store": store_name,
                "examined": examined,
                "changed": changed,
                "skipped": skipped,
                "zero_definition": zero_definition,
                "partial_write": True,
            }
    else:
        changed = len(changes)

    for cap_name, old, new in changes:
        print(
            f"[{store_name}] {cap_name:<45} "
            f"old={old:>5}  new={new:>5}"
        )

    print()
    print(
        f"[{store_name}] examined={examined} "
        f"changed={changed} skipped-unchanged={skipped} "
        f"zero-definition={zero_definition} (write={write})"
    )
    return {
        "store": store_name,
        "examined": examined,
        "changed": changed,
        "skipped": skipped,
        "zero_definition": zero_definition,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write the new values back to ChromaDB. Default is dry-run.",
    )
    parser.add_argument(
        "--stores",
        nargs="+",
        choices=list(_STORES.keys()) + ["all"],
        default=["all"],
        help="Which ChromaDB stores to backfill (default: all).",
    )
    parser.add_argument(
        "--allow-high-zero-ratio",
        action="store_true",
        help="tmp.md P1: override the zero-definition safety valve.",
    )
    parser.add_argument(
        "--max-zero-ratio", type=float, default=0.25,
        help="tmp.md P1: threshold for zero-definition fraction (default 0.25).",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    targets = list(_STORES.keys()) if "all" in args.stores else args.stores

    print(f"Mode: {'WRITE' if args.write else 'DRY-RUN'}")
    print(f"Stores: {', '.join(targets)}")
    print()

    max_zero_ratio = 1.0 if args.allow_high_zero_ratio else args.max_zero_ratio
    summaries = [
        _backfill_one_store(
            name, _STORES[name],
            write=args.write,
            max_zero_ratio=max_zero_ratio,
        )
        for name in targets
    ]

    total_examined = sum(s["examined"] for s in summaries)
    total_changed = sum(s["changed"] for s in summaries)
    print()
    print(
        f"Summary: examined={total_examined} changed={total_changed} "
        f"across {len(summaries)} store(s)"
    )

    # tmp.md P1 (2026-04-22): if every targeted store was missing OR the
    # union of examined rows is zero, that's a structural "no work
    # done" signal — pre-fix exit 0, now exit 4.
    all_missing_or_empty = all(
        s.get("missing") or s.get("examined", 0) == 0 for s in summaries
    )
    if all_missing_or_empty:
        logger.error(
            "ERROR: every targeted store was missing or empty. "
            "Exit 4. tmp.md P1."
        )
        return 4

    if any(s.get("aborted_high_zero_ratio") for s in summaries):
        return 5

    if not args.write:
        print("Dry-run only — re-run with --write to persist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
