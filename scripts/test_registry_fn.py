"""Registry false-negative audit (proposal §Pillar 1 line 77).

Closes the symmetric gap with ``scripts/test_registry_matching.py`` which
audits **false positives** (superficial keyword matches that produce
spurious hits). This script audits **false negatives** — the symmetric
claim in the proposal:

    "The registry uses hierarchical embeddings (domain → capability →
    specific skill) to prevent both false positives (superficial keyword
    matches) AND false negatives (semantically equivalent but differently
    named skills)." — thesis_proposal.md, line 77

Methodology
-----------
1. Seed an ephemeral ChromaDB registry via ``seed_registry()``.
2. For each seeded capability, ask an LLM to generate N paraphrased
   queries with **different vocabulary but semantically equivalent**
   intent. Example for ``fibonacci``: "compute nth Fibonacci number" →
   paraphrases like "recursive Fibonacci sequence calculator", "find the
   Fibonacci value at position k", "nth term of 1,1,2,3,5,8,...".
3. Run ``registry.search(paraphrase, n_results=3)`` for each paraphrase.
4. Measure three recall-at-K rates over the full (cap × paraphrase) grid:
     - top-1 strict:   target cap ranked exactly first
     - top-3 lenient:  target cap anywhere in top-3
     - above-threshold: target cap above the default cosine threshold
   The **top-1 miss rate** is the proposal-literal FN metric.

Exit code 0 if aggregate top-3 recall >= 0.90 (the proposal's
"hierarchical embeddings prevent FN" claim is empirically defensible at
this bar). Otherwise 1 — indicating the registry has a measurable FN
problem worth investigating.

Chapter 3 methodology evidence. Cite the aggregate top-1 / top-3 /
above-threshold numbers in §3.1 or §3.2 of the thesis.

Usage
-----
::

    # Default (5 paraphrases per cap, threshold 0.6)
    uv run scripts/test_registry_fn.py

    # Larger sample
    uv run scripts/test_registry_fn.py --paraphrases-per-cap 10

    # Strict threshold
    uv run scripts/test_registry_fn.py --threshold 0.7

    # Per-cap detail
    uv run scripts/test_registry_fn.py --verbose

Cost: 1 LLM call per capability (~19 caps seeded by ``seed_registry``,
~$0.02 at gpt-5-mini, temp=0.7 for paraphrase diversity). Runs in ~60s.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Step 13.6 / Fix #1: route dev-script LangFuse emissions to test env.
os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", "test")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from pydantic import BaseModel, Field  # noqa: E402

from src.config import DEFAULT_LLM_MODEL  # noqa: E402
from src.registry.store import SkillRegistry, seed_registry  # noqa: E402
from src.schemas.capability import CapabilityDescriptor  # noqa: E402

logging.basicConfig(
    level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("test_registry_fn")


class ParaphrasedQueries(BaseModel):
    """Structured-output schema for the paraphrase generator."""

    queries: list[str] = Field(
        description=(
            "Semantically-equivalent queries with DIFFERENT vocabulary from "
            "the original. Each query should be how a real user might ask "
            "for this capability without knowing its exact name. Avoid "
            "reusing verbs/nouns from the original description."
        ),
        min_length=1,
    )


@dataclass
class CapProbeResult:
    cap_name: str
    paraphrases: list[str]
    top1_hits: int  # target cap ranked #1
    top3_hits: int  # target cap in top-3
    above_threshold_hits: int  # target cap above threshold

    @property
    def total(self) -> int:
        return len(self.paraphrases)


# ---------------------------------------------------------------------------
# Paraphrase generation
# ---------------------------------------------------------------------------


def _build_paraphrase_llm():
    """Build an LLM with structured output bound to ParaphrasedQueries."""
    from langchain.chat_models import init_chat_model

    llm = init_chat_model(DEFAULT_LLM_MODEL, temperature=0.7)
    return llm.with_structured_output(
        ParaphrasedQueries, method="function_calling"
    )


def generate_paraphrases(
    cap: CapabilityDescriptor,
    n: int,
    structured_llm,
) -> list[str]:
    """Ask the LLM for ``n`` semantically-equivalent paraphrases."""
    prompt = (
        "You are helping audit a skill registry for false-negative "
        "retrieval (queries that should match an existing capability but "
        "miss it due to vocabulary differences).\n\n"
        f"Original capability name: {cap.name}\n"
        f"Original description: {cap.description}\n"
        f"Capability type: {cap.capability_type.value}\n\n"
        f"Generate exactly {n} paraphrased queries that a user might type "
        "when they need this capability BUT don't know its name. "
        "Constraints:\n"
        "- Use different verbs and nouns from the original description.\n"
        "- Do NOT use the capability name itself.\n"
        "- Keep the semantic intent identical.\n"
        "- Vary sentence structure (imperative, question, statement).\n"
        "- Target real-user phrasing, not technical jargon.\n\n"
        "Return exactly the requested number of paraphrases."
    )
    try:
        result = structured_llm.invoke(prompt)
        return list(result.queries[:n])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Paraphrase generation failed for %s: %s", cap.name, exc)
        return []


# ---------------------------------------------------------------------------
# Probe a single capability
# ---------------------------------------------------------------------------


def probe_cap(
    registry: SkillRegistry,
    cap: CapabilityDescriptor,
    paraphrases: list[str],
    *,
    threshold: float,
    verbose: bool,
) -> CapProbeResult:
    """Run every paraphrase through registry.search and count hits."""
    top1, top3, above = 0, 0, 0
    for paraphrase in paraphrases:
        results = registry.search(paraphrase, n_results=3, min_confidence=0.0)
        # Position of the target cap in results (or None if missing).
        target_rank, target_score = None, None
        for rank, (descriptor, score) in enumerate(results):
            if descriptor.name == cap.name:
                target_rank, target_score = rank, score
                break

        if target_rank == 0:
            top1 += 1
        if target_rank is not None and target_rank <= 2:
            top3 += 1
        if target_score is not None and target_score >= threshold:
            above += 1

        if verbose:
            rank_str = (
                f"#{target_rank + 1}@{target_score:.3f}"
                if target_rank is not None
                else "MISS"
            )
            print(f"  [{rank_str}] {paraphrase}")

    return CapProbeResult(
        cap_name=cap.name,
        paraphrases=paraphrases,
        top1_hits=top1,
        top3_hits=top3,
        above_threshold_hits=above,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--paraphrases-per-cap",
        type=int,
        default=5,
        help="Number of paraphrased queries to generate per capability (default: 5)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.6,
        help="Cosine similarity threshold for the 'above-threshold' metric (default: 0.6)",
    )
    parser.add_argument(
        "--pass-threshold",
        type=float,
        default=0.90,
        help="Aggregate top-3 recall required for exit 0 (default: 0.90)",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Print per-paraphrase ranks"
    )
    args = parser.parse_args()

    print("=" * 70)
    print("  REGISTRY FALSE-NEGATIVE AUDIT (proposal §Pillar 1 line 77)")
    print("=" * 70)

    # Seed an ephemeral registry so we don't touch any persistent state.
    print("\nSeeding ephemeral registry...")
    registry = SkillRegistry()  # ephemeral = no persist_directory
    seed_registry(registry)
    caps = registry.list_all()
    print(f"Seeded {len(caps)} capabilities.")

    # Build the paraphrase LLM.
    try:
        structured_llm = _build_paraphrase_llm()
    except Exception as exc:  # noqa: BLE001
        print(f"\nFATAL: failed to initialise paraphrase LLM: {exc}")
        print("Check OPENAI_API_KEY in .env.")
        return 1

    print(
        f"\nGenerating {args.paraphrases_per_cap} paraphrase(s) per cap "
        f"({len(caps)} caps × {args.paraphrases_per_cap} = "
        f"~{len(caps) * args.paraphrases_per_cap} queries)..."
    )

    results: list[CapProbeResult] = []
    for i, cap in enumerate(caps, 1):
        if args.verbose:
            print(
                f"\n[{i}/{len(caps)}] {cap.name} — {cap.description[:60]}…"
            )

        paraphrases = generate_paraphrases(
            cap, args.paraphrases_per_cap, structured_llm
        )
        if not paraphrases:
            print(f"  WARNING: skipped {cap.name} (no paraphrases generated)")
            continue

        result = probe_cap(
            registry,
            cap,
            paraphrases,
            threshold=args.threshold,
            verbose=args.verbose,
        )
        results.append(result)

        if not args.verbose:
            print(
                f"  [{i}/{len(caps)}] {cap.name:30s} "
                f"top1={result.top1_hits}/{result.total} "
                f"top3={result.top3_hits}/{result.total} "
                f"@thr={result.above_threshold_hits}/{result.total}"
            )

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("  AGGREGATE FALSE-NEGATIVE AUDIT REPORT")
    print("=" * 70)

    total_queries = sum(r.total for r in results)
    if total_queries == 0:
        print("\nNo queries were probed. Exiting with failure.")
        return 1

    top1_total = sum(r.top1_hits for r in results)
    top3_total = sum(r.top3_hits for r in results)
    above_total = sum(r.above_threshold_hits for r in results)

    top1_rate = top1_total / total_queries
    top3_rate = top3_total / total_queries
    above_rate = above_total / total_queries
    fn_top1 = 1.0 - top1_rate
    fn_top3 = 1.0 - top3_rate

    print(f"\nCapabilities probed:         {len(results)}")
    print(f"Paraphrased queries total:   {total_queries}")
    print(f"Threshold (above):           {args.threshold}")
    print()
    print(f"top-1 recall (strict):       {top1_total}/{total_queries} = {top1_rate:.1%}")
    print(f"top-3 recall (lenient):      {top3_total}/{total_queries} = {top3_rate:.1%}")
    print(f"above-threshold recall:      {above_total}/{total_queries} = {above_rate:.1%}")
    print()
    print(f"FN rate (1 - top-1 recall):  {fn_top1:.1%}  ← proposal-literal FN metric")
    print(f"FN rate (1 - top-3 recall):  {fn_top3:.1%}  ← lenient framing")

    # Worst-performing caps (candidates for Chapter 3 limitations paragraph).
    worst = sorted(
        results, key=lambda r: (r.top3_hits / max(1, r.total), r.cap_name)
    )[:5]
    print("\nTop 5 worst top-3-recall caps (FN candidates for Chapter 3):")
    for r in worst:
        rate = r.top3_hits / max(1, r.total)
        print(f"  {r.cap_name:35s} top-3 recall = {rate:.1%} ({r.top3_hits}/{r.total})")

    # Exit code
    if top3_rate >= args.pass_threshold:
        print(
            f"\nPASS — aggregate top-3 recall {top3_rate:.1%} >= "
            f"{args.pass_threshold:.1%} pass-bar."
        )
        return 0

    print(
        f"\nFAIL — aggregate top-3 recall {top3_rate:.1%} < "
        f"{args.pass_threshold:.1%} pass-bar."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
