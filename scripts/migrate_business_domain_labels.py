"""Migrate business-sub-domain labels onto existing registry capabilities.

One-off migration script for B1 Phase 2 Step 3.5 (the domain-filter
experiment). Rewrites the ``domain`` field on non-pinned capabilities in
a persistent registry from whatever flat bucket they have today (usually
``"generated"``) to a meaningful business-sub-domain label inferred from
the capability's name + description + source.

Why this exists
---------------
The Phase D reusability audit (2026-04-13) found that all 11 generated
capabilities in ``data/cli/chroma`` share the ``domain="generated"``
bucket. With a flat bucket, the proposal's "hierarchical embeddings
(domain → capability → specific skill)" cannot meaningfully filter
cross-sub-domain false positives: the silent-poisoning case where
``parse_sales_csv_and_products_xlsx_and_ta_...`` fires at 0.634 on a
transactions-domain paraphrase is an intra-"generated" confusion.

The Step 3.5 experiment tests whether filtering by business sub-domain
(sales vs transactions vs inventory) reduces these false positives
without hurting recall. For the filter to be meaningful, the data must
first be labeled — which is what this migration does.

Taxonomy
--------
Hybrid: keeps the existing functional labels for seeds and adds
business-sub-domain labels for generated capabilities.

- Functional (seeds stay here): data-processing, text-analysis,
  reporting, analytics, mathematics
- Business sub-domains (generated caps land here): sales, inventory,
  transactions, finance, fraud, operations, hr
- Fallback: general

Usage
-----
::

    # Dry run (default — no writes, just prints the proposed labels)
    uv run scripts/migrate_business_domain_labels.py

    # Actually write the new labels (requires --write)
    uv run scripts/migrate_business_domain_labels.py --write

    # Point at a different registry
    uv run scripts/migrate_business_domain_labels.py --write \
        --registry-path data/cli/chroma

    # Also relabel seeds (default: seeds are preserved)
    uv run scripts/migrate_business_domain_labels.py --write --include-seeds

Safety rails
------------
- Default mode is ``--dry-run``; you must pass ``--write`` to modify data.
- A tarball snapshot of the registry directory is created BEFORE any
  write, named ``<registry-path>_backup_<timestamp>.tar.gz``.
- Only non-pinned capabilities are relabeled by default. Pass
  ``--include-seeds`` to also relabel seeded (pinned) capabilities.
- Idempotent: because the LLM runs at ``temperature=0`` with structured
  output, running the script twice produces the same labels.
- Rolling back: ``tar -xzf <backup>.tar.gz -C <parent-dir>``.

Cost: ~1 LLM call per capability (~$0.01 total for 19 caps).
"""
import argparse
import logging
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from pydantic import BaseModel, Field  # noqa: E402

from src.config import DEFAULT_LLM_MODEL  # noqa: E402
from src.registry.store import SkillRegistry  # noqa: E402
from src.schemas.capability import CapabilityDescriptor  # noqa: E402

logger = logging.getLogger("migrate_business_domain_labels")


# ---------------------------------------------------------------------------
# Taxonomy — inlined into the Pydantic field below to avoid forward-reference
# issues with `from __future__ import annotations` (which would turn the
# Literal alias into a lazy string that Pydantic can't resolve at model-build
# time). Keep this list in sync with the DomainClassification.domain Literal.
# ---------------------------------------------------------------------------

TAXONOMY_FUNCTIONAL = (
    "data-processing",
    "text-analysis",
    "reporting",
    "analytics",
    "mathematics",
)
TAXONOMY_BUSINESS = (
    "sales",
    "inventory",
    "transactions",
    "finance",
    "fraud",
    "operations",
    "hr",
)
TAXONOMY_FALLBACK = ("general",)
TAXONOMY_ALL = TAXONOMY_FUNCTIONAL + TAXONOMY_BUSINESS + TAXONOMY_FALLBACK


class DomainClassification(BaseModel):
    """Structured output from the domain inference LLM call."""

    domain: Literal[
        # Functional (generic, seed-level)
        "data-processing",
        "text-analysis",
        "reporting",
        "analytics",
        "mathematics",
        # Business sub-domains (generated-cap-level)
        "sales",
        "inventory",
        "transactions",
        "finance",
        "fraud",
        "operations",
        "hr",
        # Fallback
        "general",
    ] = Field(
        description=(
            "The single most specific domain label this capability belongs to. "
            "Prefer business sub-domains (sales, inventory, operations, etc.) "
            "over functional labels (data-processing) when the capability is "
            "clearly bound to a business context. Use functional labels only "
            "when the capability is generic enough to serve any business vertical."
        )
    )
    rationale: str = Field(
        description=(
            "One sentence citing a specific fact from the capability's name, "
            "description, or implementation that justifies the label."
        )
    )


# ---------------------------------------------------------------------------
# LLM prompt
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """You are labeling a capability in a JIT capability registry with a business sub-domain tag.

The registry is searched at runtime by semantic similarity and then filtered by domain. The purpose of the domain label is to enable hierarchical filtering: when a query's inferred domain is X, only capabilities with domain X are considered. So the label must be accurate and specific.

CAPABILITY NAME:
{name}

CAPABILITY TYPE:
{capability_type}

DESCRIPTION:
{description}

IMPLEMENTATION (may be truncated):
{implementation}

TAXONOMY (pick exactly one):

Functional labels (use these ONLY for generic capabilities that could serve any business vertical):
- data-processing — generic CSV/JSON parsing, cleaning, transformation, joining, validation
- text-analysis — word counting, NLP, generic text statistics
- reporting — generic report generation, markdown/text output formatting
- analytics — generic statistical analysis, trend detection, generic anomaly scoring
- mathematics — pure mathematical computation (e.g., fibonacci)

Business sub-domain labels (prefer these whenever the capability is bound to a specific business context — hardcoded column names like "sales_amount" or "inventory_count", domain-specific rules, or business vocabulary in the description):
- sales — sales, revenue, products, orders, pricing, customer purchases
- inventory — stock, SKU, reorder, warehouse, supply-chain
- transactions — payment transactions, transfers, ledger entries, financial transactions (distinct from sales — this is finance-level transactions, not sales-level orders)
- finance — budget, accounting, general ledger, expense, reconciliation
- fraud — fraud detection, anomaly in payment context, suspicious patterns
- operations — system operations, incidents, monitoring, metrics, devops, service health
- hr — employees, payroll, benefits, HR data

Fallback:
- general — use this ONLY if the capability fits none of the above

Rules:
1. Read the IMPLEMENTATION carefully. If the source code or skill content hardcodes specific business vocabulary (e.g., "sales_amount", "region_revenues", "reorder_threshold"), choose the business sub-domain that matches, not a functional label.
2. Prefer specificity. If a capability parses sales CSVs specifically, label it "sales", not "data-processing".
3. Only use "general" as a last resort.
4. Do not invent new labels. Pick exactly one from the taxonomy above.

Return a DomainClassification with the chosen domain and a one-sentence rationale citing a specific fact from the capability.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_implementation(cap: CapabilityDescriptor, max_chars: int = 4000) -> str:
    """Return the capability's executable content (truncated)."""
    meta = cap.metadata or {}
    source = meta.get("source_code") or meta.get("skill_content") or meta.get(
        "agent_config"
    ) or ""
    if len(source) > max_chars:
        source = source[:max_chars] + f"\n... (truncated from {len(source)} chars)"
    return source or "(no implementation available)"


def _snapshot_registry(registry_path: Path) -> Path:
    """Create a tarball backup of the registry directory before any writes."""
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup_path = registry_path.parent / f"{registry_path.name}_backup_{timestamp}.tar.gz"
    logger.info("Creating registry snapshot: %s", backup_path)
    with tarfile.open(backup_path, "w:gz") as tar:
        tar.add(registry_path, arcname=registry_path.name)
    logger.info("Snapshot created (%d bytes)", backup_path.stat().st_size)
    return backup_path


def _classify_capability(
    cap: CapabilityDescriptor, llm_client
) -> DomainClassification:
    """Call the LLM to classify the capability into a business sub-domain."""
    prompt = _PROMPT_TEMPLATE.format(
        name=cap.name,
        capability_type=cap.capability_type.value,
        description=cap.description,
        implementation=_get_implementation(cap),
    )
    return llm_client.invoke(prompt)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate business-sub-domain labels onto registry capabilities.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--registry-path",
        type=Path,
        default=ROOT / "data" / "cli" / "chroma",
        help="Path to the ChromaDB registry directory (default: data/cli/chroma)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Actually apply the new labels (default is dry-run)",
    )
    parser.add_argument(
        "--include-seeds",
        action="store_true",
        help="Also relabel pinned (seeded) capabilities (default: skip seeds)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose logging (DEBUG level)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
    )
    # Quiet the noisy third-party loggers
    for noisy in ("chromadb", "httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if not args.registry_path.exists():
        print(f"ERROR: Registry path does not exist: {args.registry_path}", file=sys.stderr)
        sys.exit(1)

    dry_run = not args.write
    mode = "DRY RUN" if dry_run else "WRITE"
    print("=" * 72)
    print(f"  Business Sub-Domain Migration — {mode}")
    print(f"  Registry: {args.registry_path}")
    print(f"  Include seeds: {args.include_seeds}")
    print("=" * 72)

    # Load registry
    registry = SkillRegistry(persist_directory=str(args.registry_path))
    all_caps = registry.list_all(is_active=None)
    print(f"\n  Total capabilities in registry: {len(all_caps)}")

    # Select migration candidates
    candidates: list[CapabilityDescriptor] = []
    for cap in all_caps:
        is_pinned = (cap.metadata or {}).get("pinned") == "true"
        if is_pinned and not args.include_seeds:
            continue
        candidates.append(cap)
    print(f"  Candidates for relabeling: {len(candidates)}")
    if not candidates:
        print("\n  Nothing to do.")
        return

    # Print current state
    print("\n  Current domain distribution:")
    current: dict[str, int] = {}
    for cap in candidates:
        current[cap.domain] = current.get(cap.domain, 0) + 1
    for domain, count in sorted(current.items()):
        print(f"    [{domain:20s}] {count:3d} caps")

    # Snapshot before any writes
    if not dry_run:
        backup = _snapshot_registry(args.registry_path)
        print(f"\n  Backup: {backup}")
        print("  (Restore with: tar -xzf <backup> -C data/cli/)")

    # Set up the LLM
    print("\n  Inferring new labels via LLM...")
    from langchain.chat_models import init_chat_model

    llm = init_chat_model(DEFAULT_LLM_MODEL, temperature=0)
    structured_llm = llm.with_structured_output(
        DomainClassification, method="function_calling"
    )

    # Classify each candidate
    results: list[tuple[CapabilityDescriptor, DomainClassification, str]] = []
    for idx, cap in enumerate(candidates, 1):
        old_domain = cap.domain
        try:
            classification = _classify_capability(cap, structured_llm)
            new_domain = classification.domain
            results.append((cap, classification, old_domain))
            print(
                f"  [{idx:2d}/{len(candidates)}] {cap.name}"
                f"\n         {old_domain} → {new_domain}"
                f"\n         reason: {classification.rationale}"
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Classification failed for '%s': %s", cap.name, exc)
            continue

    # Print post-classification distribution
    print("\n  Proposed domain distribution:")
    proposed: dict[str, int] = {}
    for _, classification, _ in results:
        proposed[classification.domain] = proposed.get(classification.domain, 0) + 1
    for domain, count in sorted(proposed.items()):
        print(f"    [{domain:20s}] {count:3d} caps")

    # Apply (or not)
    if dry_run:
        print("\n  DRY RUN — no changes written to the registry.")
        print("  Re-run with --write to apply.")
        return

    print("\n  Writing new labels to the registry...")
    success = 0
    failed = 0
    for cap, classification, old_domain in results:
        try:
            # Build an updated descriptor with the new domain
            updated = CapabilityDescriptor(
                **{**cap.model_dump(), "domain": classification.domain}
            )
            # Upsert back (SkillRegistry.add handles both insert and update)
            registry.add(updated)
            success += 1
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to upsert '%s' with new domain '%s': %s",
                cap.name, classification.domain, exc,
            )
            failed += 1

    print(f"\n  Upserted: {success} / {len(results)}")
    if failed:
        print(f"  Failed:   {failed}")
    print("\n  Migration complete.")
    print(
        "  Rollback: "
        f"tar -xzf {args.registry_path.parent}/{args.registry_path.name}_backup_*.tar.gz "
        f"-C {args.registry_path.parent}"
    )


if __name__ == "__main__":
    main()
