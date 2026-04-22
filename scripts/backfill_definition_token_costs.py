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
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.registry.store import SkillRegistry  # noqa: E402
from src.registry.token_count import count_definition_tokens  # noqa: E402
from src.schemas.capability import CapabilityType  # noqa: E402
from src.schemas.task import GeneratedArtifact  # noqa: E402

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


def _backfill_one_store(store_name: str, persist_dir: Path, *, write: bool) -> dict:
    if not persist_dir.exists():
        print(f"[{store_name}] store not found at {persist_dir} — skipping")
        return {"store": store_name, "examined": 0, "changed": 0, "skipped": 0}

    registry = SkillRegistry(persist_directory=str(persist_dir))
    caps = registry.list_all(is_active=None)  # include deactivated too

    examined = 0
    changed = 0
    skipped = 0
    zero_definition = 0

    for cap in caps:
        examined += 1
        artifact = _reconstruct_artifact(cap)
        new_cost = count_definition_tokens(artifact)

        existing = int(getattr(cap, "definition_token_cost", 0) or 0)
        if new_cost == existing and existing != 0:
            skipped += 1
            continue
        if new_cost == 0:
            zero_definition += 1
            # Still write zero if we're writing — reflects the empty definition.

        if write:
            registry.update(cap.name, definition_token_cost=new_cost)
        changed += 1

        print(
            f"[{store_name}] {cap.name:<45} "
            f"type={cap.capability_type.value:<9} "
            f"old={existing:>5}  new={new_cost:>5}"
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
    args = parser.parse_args()

    targets = list(_STORES.keys()) if "all" in args.stores else args.stores

    print(f"Mode: {'WRITE' if args.write else 'DRY-RUN'}")
    print(f"Stores: {', '.join(targets)}")
    print()

    summaries = [
        _backfill_one_store(name, _STORES[name], write=args.write) for name in targets
    ]

    total_examined = sum(s["examined"] for s in summaries)
    total_changed = sum(s["changed"] for s in summaries)
    print()
    print(
        f"Summary: examined={total_examined} changed={total_changed} "
        f"across {len(summaries)} store(s)"
    )
    if not args.write:
        print("Dry-run only — re-run with --write to persist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
