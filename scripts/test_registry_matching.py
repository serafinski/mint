"""Registry matching validation — probe matching + context audit + self-consistency audit.

Purpose: measure the proposal's claim that "hierarchical embeddings prevent
false positives (superficial keyword matches)" as an empirical property of
the current registry, and expose two distinct failure modes:

  1. Query/capability context-incompatibility (tension #2: specificity
     vs. generality). A capability born for one task silently matches an
     unrelated query because the description is prose-level.
  2. Description/implementation drift. The capability's description
     over-promises what the code actually does. This is orthogonal to
     query matching — it is a property of the registry itself.

Three phases:
  - Phase A (naive): ChromaDB cosine similarity + threshold gate. No LLM.
  - Phase B (query audit): LLM judge per fired match. Reads the
    capability's implementation and decides whether routing THIS query
    to THIS capability would produce sensible output.
  - Phase C (self-consistency audit): LLM judge per capability in the
    registry. Decides whether the description honestly reflects the
    implementation. Query-independent. Runs against both the ephemeral
    seeded registry and any persistent registry you point at (so you
    can audit newly generated capabilities, not just seeds).

Usage::

    # Default: seed an ephemeral registry, run all three phases
    uv run scripts/test_registry_matching.py

    # Phase A only (no LLM)
    uv run scripts/test_registry_matching.py --naive

    # Self-consistency audit only, on the CLI persistent registry
    # (audits real generated capabilities from prior mint runs)
    uv run scripts/test_registry_matching.py --self-audit \\
        --registry-path data/cli/chroma

    # Tune threshold or verbosity
    uv run scripts/test_registry_matching.py --threshold 0.55 --verbose

Exit code: 0 if phase A recall/precision criteria are met, all fired
context traps are caught by phase B, and phase C finds no drift. 1 otherwise.

Cost: phase A is free. Phase B/C cost ~1 LLM call per match or capability
(typically 25-40 calls total, ~$0.02 with gpt-5-mini). Runs in ~60s.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Step 13.6 / Fix #1 (2026-04-19): route dev-script LangFuse emissions
# to ``environment=test`` so they don't pollute Phase 4 eval's
# ``environment=default`` data. Must be set BEFORE ``load_dotenv`` so
# ambient .env values can't override. Same pattern as ``tests/conftest.py``.
import os  # noqa: E402
os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", "test")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from pydantic import BaseModel, Field  # noqa: E402

from src.config import DEFAULT_LLM_MODEL  # noqa: E402
from src.registry.store import SkillRegistry, seed_registry  # noqa: E402
from src.schemas.capability import CapabilityDescriptor, CapabilityType  # noqa: E402

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Probe definitions
# ---------------------------------------------------------------------------


@dataclass
class Probe:
    """A single probe against the registry.

    - expected_seed=None means the probe should NOT match anything.
    - context_compatible=False means the probe is a trap: it is expected
      to fire (the implementation will be reached) but the implementation's
      baked-in assumptions are incompatible with the query's context.
    """

    query: str
    expected_seed: str | None
    context_compatible: bool = True
    note: str = ""


# Paraphrases that should match a specific seed AND be context-compatible
# with its implementation. These probe recall: can the decomposer's
# vocabulary reach the seeded capabilities at all?
POSITIVE_PROBES: list[Probe] = [
    # csv-parser
    Probe("parse-csv-and-return-row-count-and-column-names", "csv-parser"),
    Probe("read-csv-file-count-rows-and-extract-headers", "csv-parser"),
    # fibonacci
    Probe("compute-nth-fibonacci-number", "fibonacci"),
    Probe("fibonacci-sequence-value-at-index-n", "fibonacci"),
    # text-statistics
    Probe("compute-word-count-and-flesch-readability-of-document", "text-statistics"),
    Probe("word-count-and-readability-score-for-text-file", "text-statistics"),
    # json-validator
    Probe("validate-json-config-against-json-schema-return-errors", "json-validator"),
    Probe("check-json-compliance-with-schema-file", "json-validator"),
    # date-formatter (description-only seed)
    Probe("convert-date-string-between-iso-8601-and-rfc-2822", "date-formatter"),
    Probe("reformat-date-from-one-format-to-another", "date-formatter"),
    # data-cleaning-workflow
    Probe("clean-tabular-dataset-remove-nulls-and-deduplicate", "data-cleaning-workflow"),
    Probe("etl-data-cleaning-workflow-nulls-dedup-type-coercion", "data-cleaning-workflow"),
    # report-generation
    Probe(
        "generate-analysis-report-with-summary-stats-and-anomalies",
        "report-generation",
    ),
    Probe("build-structured-report-with-executive-summary", "report-generation"),
    # data-analysis-agent
    Probe(
        "autonomous-exploratory-data-analysis-on-dataset",
        "data-analysis-agent",
    ),
    Probe(
        "profile-dataset-find-correlations-generate-insights",
        "data-analysis-agent",
    ),
]


# Queries that have no reasonable match in the registry. Any match above
# threshold is a false positive.
NEGATIVE_PROBES: list[Probe] = [
    Probe("train-neural-network-for-image-classification", None),
    Probe("send-slack-notification-to-channel", None),
    Probe("deploy-kubernetes-pod-to-cluster", None),
    Probe("encrypt-file-with-aes-256-gcm", None),
    Probe("scrape-html-content-from-webpage-with-login", None),
    Probe("query-postgres-database-with-sql", None),
    Probe("compile-rust-binary-with-cargo", None),
    Probe("render-3d-scene-with-ray-tracing", None),
]


# Context traps: queries that semantically overlap a seed (phase A WILL
# fire) but whose business context is incompatible with the seed's
# implementation assumptions. These are the core of tension #2
# (specificity vs. generality) made testable.
#
# Each trap is annotated with the SPECIFIC implementation detail that
# makes the match unsafe — the phase-B judge should cite a similar
# reason. If the judge misses it, we have silent poisoning risk.
CONTEXT_TRAP_PROBES: list[Probe] = [
    # csv-parser hardcodes csv.DictReader (comma-delimited, no dialect)
    Probe(
        "parse-semicolon-delimited-european-csv-with-decimal-commas",
        expected_seed="csv-parser",
        context_compatible=False,
        note="seed uses csv.DictReader with default dialect; European CSVs need a custom dialect",
    ),
    Probe(
        "parse-csv-and-select-only-columns-name-and-email",
        expected_seed="csv-parser",
        context_compatible=False,
        note="seed returns row_count + column_names only; no column selection",
    ),
    # Originally a POSITIVE probe against the drifted csv-parser description
    # that claimed "structured rows with typed values". When the description
    # was narrowed to match the actual code, this probe naturally became a
    # context trap — it is asking for row data the implementation does not
    # return. This is a concrete demonstration that fixing description drift
    # reveals previously-hidden context incompatibilities.
    Probe(
        "load-csv-return-structured-rows-with-headers",
        expected_seed="csv-parser",
        context_compatible=False,
        note="seed returns only row_count + column_names, not row data; was a hidden trap under the drifted description",
    ),
    # fibonacci hardcodes two-term recurrence a,b = 0,1
    Probe(
        "compute-nth-term-of-tribonacci-sequence-sum-of-prior-three-terms",
        expected_seed="fibonacci",
        context_compatible=False,
        note="seed hardcodes two-term recurrence; tribonacci needs three prior terms",
    ),
    # text-statistics computes word_count + Flesch ONLY
    Probe(
        "analyze-sentiment-of-text-document-and-return-polarity-score",
        expected_seed="text-statistics",
        context_compatible=False,
        note="seed computes word_count + Flesch readability only; no sentiment analysis",
    ),
    Probe(
        "detect-language-of-text-and-count-unicode-characters",
        expected_seed="text-statistics",
        context_compatible=False,
        note="seed counts words via \\w+ regex (ASCII-leaning); no language detection",
    ),
    # json-validator REQUIRES schema_path — caller wants syntax-only check
    Probe(
        "check-if-json-string-is-well-formed-and-parseable",
        expected_seed="json-validator",
        context_compatible=False,
        note="seed requires schema_path argument; caller wants mere syntax validation",
    ),
    # data-cleaning-workflow assumes tabular numeric data
    Probe(
        "clean-text-document-by-removing-html-tags-and-normalizing-whitespace",
        expected_seed="data-cleaning-workflow",
        context_compatible=False,
        note="skill targets tabular data (null/dedup/numeric outliers); not text normalization",
    ),
    # report-generation does numeric summary stats + z-score anomalies
    Probe(
        "generate-marketing-campaign-report-with-ctr-roas-and-creative-ranking",
        expected_seed="report-generation",
        context_compatible=False,
        note="skill hardcodes statistical summary + z-score outliers; no marketing KPIs",
    ),
    # data-analysis-agent is dataset-EDA, not systems diagnostics
    Probe(
        "diagnose-memory-leak-in-production-python-service-from-heap-dump",
        expected_seed="data-analysis-agent",
        context_compatible=False,
        note="agent prompt targets dataset EDA (profiling/correlations); not runtime diagnostics",
    ),
]


ALL_PROBES: list[Probe] = POSITIVE_PROBES + NEGATIVE_PROBES + CONTEXT_TRAP_PROBES


# ---------------------------------------------------------------------------
# Phase A — naive semantic probe
# ---------------------------------------------------------------------------


Verdict = Literal[
    "true-positive",
    "true-negative",
    "false-positive",
    "false-negative",
    "wrong-seed-fired",
    "context-trap-fired",
    "context-trap-did-not-fire",
]


@dataclass
class PhaseAResult:
    probe: Probe
    top_matches: list[tuple[str, float]]
    fired: bool
    fired_seed: str | None
    fired_score: float
    verdict: Verdict


def run_phase_a(
    probes: list[Probe], registry: SkillRegistry, threshold: float
) -> list[PhaseAResult]:
    results: list[PhaseAResult] = []
    for probe in probes:
        hits = registry.search(probe.query, n_results=3)
        top_matches = [(cap.name, round(score, 3)) for cap, score in hits]
        top_score = hits[0][1] if hits else 0.0
        top_name = hits[0][0].name if hits else None
        fired = bool(hits) and top_score >= threshold
        fired_seed = top_name if fired else None

        if probe.expected_seed is None:
            verdict: Verdict = "false-positive" if fired else "true-negative"
        elif probe.context_compatible:
            if fired and fired_seed == probe.expected_seed:
                verdict = "true-positive"
            elif fired:
                verdict = "wrong-seed-fired"
            else:
                verdict = "false-negative"
        else:
            if fired and fired_seed == probe.expected_seed:
                verdict = "context-trap-fired"
            elif fired:
                verdict = "wrong-seed-fired"
            else:
                verdict = "context-trap-did-not-fire"

        results.append(
            PhaseAResult(
                probe=probe,
                top_matches=top_matches,
                fired=fired,
                fired_seed=fired_seed,
                fired_score=top_score,
                verdict=verdict,
            )
        )
    return results


# ---------------------------------------------------------------------------
# Phase B — context-compatibility audit via LLM judge
# ---------------------------------------------------------------------------


class CompatibilityVerdict(BaseModel):
    """Structured verdict from the LLM compatibility judge."""

    compatible: bool = Field(
        description=(
            "True if the capability's implementation would produce sensible "
            "and correct output for the query as-phrased. False if the "
            "implementation bakes in assumptions (parameter names, hardcoded "
            "values, output keys, data shape, business domain) that would "
            "make its output nonsensical, wrong, or misleading in the "
            "query's context."
        ),
    )
    confidence: Literal["high", "medium", "low"]
    reason: str = Field(
        description=(
            "Short justification (1-2 sentences). Cite a specific "
            "implementation detail (parameter name, hardcoded value, "
            "missing feature)."
        ),
    )


_AUDIT_PROMPT = """You are auditing a capability registry for context-compatibility issues in a just-in-time capability synthesis system.

A query was routed to a matched capability via semantic similarity. Your job is to decide whether the capability's IMPLEMENTATION would actually produce output that makes sense for the query's business context, or whether it would silently poison the caller with incompatible output because its implementation bakes in assumptions from a different context.

QUERY:
{query}

MATCHED CAPABILITY:
  Name: {name}
  Type: {ctype}
  Description: {description}

IMPLEMENTATION (may be truncated):
{implementation}

Decide: if a caller routed this query to this capability AS-IS, would the capability's output be sensible and correct for what the query asks?

Guidance:
- Say compatible=True only if the implementation's parameters, hardcoded values, output keys, data shape assumptions, and business domain are genuinely compatible with the query.
- Say compatible=False if the implementation would run but produce output that is wrong, nonsensical, or misleading in the query's context.
- A capability whose description is broader than its implementation is NOT compatible with queries that rely on the broader framing.
- Cite a specific implementation detail in your reason.
"""


def _get_implementation(cap: CapabilityDescriptor, max_chars: int = 8000) -> str:
    """Extract the capability's implementation content from its metadata.

    Truncation default of 8000 chars covers all existing generated caps
    (largest observed is ~4100 chars). The previous 2500-char cap
    produced deterministic hallucinations in the LLM judge when it
    truncated mid-identifier — for example, `f"Row {idx}` cut to
    `f"Row {i` triggered the judge to invent NameError bugs citing
    an undefined `i` variable.
    """
    meta = cap.metadata or {}
    if cap.capability_type == CapabilityType.MCP_TOOL:
        content = meta.get("source_code")
    elif cap.capability_type == CapabilityType.SKILL:
        content = meta.get("skill_content")
    elif cap.capability_type == CapabilityType.AGENT:
        content = meta.get("agent_config")
    else:
        content = None
    if not content:
        return "(no implementation stored — description-only seed)"
    if len(content) > max_chars:
        return content[:max_chars] + "\n... (truncated at line boundary)"
    return content


@dataclass
class PhaseBResult:
    probe: Probe
    matched_cap: str
    top_score: float
    verdict: CompatibilityVerdict | None
    error: str | None = None


def run_phase_b(
    phase_a: list[PhaseAResult], registry: SkillRegistry
) -> list[PhaseBResult]:
    """Run the LLM compatibility judge on every match that fired in phase A."""
    from langchain.chat_models import init_chat_model

    llm = init_chat_model(DEFAULT_LLM_MODEL, temperature=0)
    judge = llm.with_structured_output(CompatibilityVerdict, method="function_calling")

    results: list[PhaseBResult] = []
    for a in phase_a:
        if not a.fired or a.fired_seed is None:
            continue
        cap = registry.get(a.fired_seed)
        if cap is None:
            results.append(
                PhaseBResult(
                    probe=a.probe,
                    matched_cap=a.fired_seed,
                    top_score=a.fired_score,
                    verdict=None,
                    error="capability vanished from registry",
                )
            )
            continue
        prompt = _AUDIT_PROMPT.format(
            query=a.probe.query,
            name=cap.name,
            ctype=cap.capability_type.value,
            description=cap.description,
            implementation=_get_implementation(cap),
        )
        try:
            verdict = judge.invoke(prompt)
            results.append(
                PhaseBResult(
                    probe=a.probe,
                    matched_cap=a.fired_seed,
                    top_score=a.fired_score,
                    verdict=verdict,
                )
            )
        except Exception as exc:
            results.append(
                PhaseBResult(
                    probe=a.probe,
                    matched_cap=a.fired_seed,
                    top_score=a.fired_score,
                    verdict=None,
                    error=f"{type(exc).__name__}: {str(exc)[:140]}",
                )
            )
    return results


# ---------------------------------------------------------------------------
# Phase C — self-consistency audit (description vs. implementation drift)
# ---------------------------------------------------------------------------


DriftType = Literal[
    "none",
    "description_overreaches",
    "description_underreaches",
    "missing_implementation",
    "ambiguous_wording",
]


class SelfConsistencyVerdict(BaseModel):
    """Structured verdict on whether a capability's description matches its code."""

    consistent: bool = Field(
        description=(
            "True if the description honestly reflects the implementation "
            "(minor elaboration is fine). False if the description misleads "
            "a caller about what the capability actually does."
        ),
    )
    confidence: Literal["high", "medium", "low"]
    drift_type: DriftType = Field(
        description=(
            "'none' if consistent. "
            "'description_overreaches' if the description claims features "
            "the code cannot deliver. "
            "'description_underreaches' if the code does more than advertised. "
            "'missing_implementation' if there is no code stored at all. "
            "'ambiguous_wording' if a caller could plausibly interpret the "
            "description in a way the code does not handle."
        ),
    )
    reason: str = Field(
        description=(
            "Short justification. Cite specific text from the description "
            "AND a specific line/fact from the implementation."
        ),
    )


_SELF_AUDIT_PROMPT = """You are auditing a capability registry for description/implementation drift.

A capability is registered with a description and an implementation. Your job is to decide whether the description accurately reflects what the implementation actually does, or whether it over-promises / under-describes / is ambiguous.

This matters because downstream systems use the description (via semantic search) to decide whether this capability can serve a query. If the description does not match the implementation, callers will get silently wrong output.

NAME: {name}
TYPE: {ctype}

DESCRIPTION:
{description}

IMPLEMENTATION (may be truncated):
{implementation}

Decide consistency using these rules:

- consistent=True if the description honestly reflects what the code does. Minor elaboration is fine (e.g., description mentions "numeric metrics" and code computes word count + readability score).

- consistent=False with drift_type="description_overreaches" if the description claims capabilities the code does NOT deliver (e.g., description says "returns structured rows with typed values" but code returns only row counts).

- consistent=False with drift_type="description_underreaches" if the code does MORE than the description advertises.

- consistent=False with drift_type="missing_implementation" if there is no code stored (description-only seed). This is always inconsistent for reuse purposes.

- consistent=False with drift_type="ambiguous_wording" if the description uses wording a caller could reasonably interpret in ways the code does not handle (e.g., description says "clean data" but code only removes duplicates — "clean" is under-specified).

Cite specific text from the description AND a specific implementation fact in your reason.
"""


@dataclass
class PhaseCResult:
    capability_name: str
    capability_type: str
    description: str
    has_implementation: bool
    verdict: SelfConsistencyVerdict | None
    error: str | None = None


def run_phase_c(registry: SkillRegistry) -> list[PhaseCResult]:
    """Audit every capability in the registry for description/code drift."""
    from langchain.chat_models import init_chat_model

    llm = init_chat_model(DEFAULT_LLM_MODEL, temperature=0)
    judge = llm.with_structured_output(
        SelfConsistencyVerdict, method="function_calling"
    )

    results: list[PhaseCResult] = []
    for cap in registry.list_all(is_active=None):
        impl = _get_implementation(cap)
        has_impl = not impl.startswith("(no implementation stored")
        prompt = _SELF_AUDIT_PROMPT.format(
            name=cap.name,
            ctype=cap.capability_type.value,
            description=cap.description,
            implementation=impl,
        )
        try:
            verdict = judge.invoke(prompt)
            results.append(
                PhaseCResult(
                    capability_name=cap.name,
                    capability_type=cap.capability_type.value,
                    description=cap.description,
                    has_implementation=has_impl,
                    verdict=verdict,
                )
            )
        except Exception as exc:
            results.append(
                PhaseCResult(
                    capability_name=cap.name,
                    capability_type=cap.capability_type.value,
                    description=cap.description,
                    has_implementation=has_impl,
                    verdict=None,
                    error=f"{type(exc).__name__}: {str(exc)[:140]}",
                )
            )
    return results


# ---------------------------------------------------------------------------
# Phase D — generalization audit (reusability probes for generated caps)
# ---------------------------------------------------------------------------


SpecificityLevel = Literal["generic", "domain-specific", "scenario-bound"]


class GeneralizationProbes(BaseModel):
    """LLM-generated probes and specificity classification for a capability.

    Each cap gets 2 tight + 5 mid + 5 cross = 12 probes per cap. Scaling
    up the mid and cross counts (formerly 1 each) was a Step 3.5 fix for
    the n=2 baseline-wrong-fire sample size that the earlier single-probe
    version produced. Mirrors Pillar 2 Tier 2's "suite of test inputs"
    pattern (docs/thesis_proposal.md line 133) applied to retrieval
    evaluation instead of capability evaluation.
    """

    specificity: SpecificityLevel = Field(
        description=(
            "'generic' = would serve any task of this shape (e.g., 'parse a "
            "CSV', 'compute nth Fibonacci'). "
            "'domain-specific' = tied to a business vertical but not one "
            "scenario (e.g., 'compute sales revenue for any product set'). "
            "'scenario-bound' = hardcodes specific columns, files, fixtures, "
            "or business rules from a single task."
        ),
    )
    specificity_reason: str = Field(
        description="One sentence citing a specific implementation detail."
    )
    tight_paraphrases: list[str] = Field(
        min_length=2,
        max_length=2,
        description=(
            "Exactly 2 realistic decomposer-style queries (kebab-case, 5-12 "
            "words each) using the SAME domain vocabulary as the capability. "
            "Should clearly be compatible with the implementation. The two "
            "should differ in surface wording but stay in the same domain."
        ),
    )
    mid_paraphrases: list[str] = Field(
        min_length=5,
        max_length=5,
        description=(
            "Exactly 5 realistic queries with the SAME functional shape but "
            "DIFFERENT domain vocabulary from each other AND from the "
            "capability. Each of the 5 must use vocabulary from a distinct "
            "adjacent business sub-domain — e.g., if the capability is in "
            "the 'sales' domain, the 5 mid paraphrases should cover "
            "transactions, inventory, finance, operations, and one of "
            "{fraud, hr, reporting}. Keep the operational shape identical "
            "(parse + aggregate + rank, etc.) but replace business nouns. "
            "Each paraphrase is kebab-case, 5-12 words."
        ),
    )
    cross_context_paraphrases: list[str] = Field(
        min_length=5,
        max_length=5,
        description=(
            "Exactly 5 realistic queries from entirely DIFFERENT business "
            "verticals with the same operation shape. Each of the 5 must "
            "come from a distinct vertical — e.g., healthcare, legal, "
            "education, manufacturing, scientific research. If the "
            "capability is 'parse inventory CSV and reorder YAML', the 5 "
            "cross paraphrases might be 'parse users CSV and access policy "
            "YAML' (security), 'parse patients CSV and treatment protocol "
            "YAML' (healthcare), 'parse students CSV and grading rules "
            "YAML' (education), etc. Kebab-case, 5-12 words."
        ),
    )


_GENERALIZATION_PROMPT = """You are auditing a JIT-generated capability for reusability.

This capability was generated to serve a specific task in a just-in-time capability synthesis system. We need to know whether the capability is reusable across tasks, or whether it is hardcoded to its birth context (the "gets invoked once" failure mode).

NAME: {name}
TYPE: {ctype}

DESCRIPTION:
{description}

IMPLEMENTATION (may be truncated):
{implementation}

Produce:

1. specificity — classify the capability as generic / domain-specific / scenario-bound. Read the IMPLEMENTATION carefully: hardcoded column names, file names, or business rules make it scenario-bound regardless of how generic the description sounds.

2. specificity_reason — cite one specific fact from the code or description.

3. tight_paraphrases — EXACTLY 2 realistic decomposer-style queries (kebab-case, 5-12 words each) using the SAME domain vocabulary as the capability. Both should clearly be compatible with the implementation; they just differ in surface wording (e.g., "parse-inventory-csv-and-reorder-rules-yaml" vs "read-inventory-csv-and-restock-rules-yaml").

4. mid_paraphrases — EXACTLY 5 queries with the SAME functional shape but vocabulary from FIVE DISTINCT adjacent business sub-domains. For each of the 5, pick a different sub-domain and use its vocabulary. Example for a capability in the 'sales' domain ("parse sales CSV and products XLSX and targets CSV"):
  - transactions: "parse-transactions-csv-and-catalog-xlsx-and-targets-csv"
  - inventory: "parse-stock-csv-and-warehouse-xlsx-and-reorder-csv"
  - finance: "parse-ledger-csv-and-accounts-xlsx-and-budgets-csv"
  - operations: "parse-incident-csv-and-service-xlsx-and-sla-csv"
  - reporting: "parse-metrics-csv-and-dashboards-xlsx-and-thresholds-csv"
All 5 must come from DIFFERENT sub-domains from each other. Keep the operational shape identical (parse + join + aggregate, etc.). Each is kebab-case, 5-12 words, action-oriented.

5. cross_context_paraphrases — EXACTLY 5 queries from entirely DIFFERENT BUSINESS VERTICALS (not sub-domains — full verticals outside the original testbed). Each of the 5 must come from a different vertical. Example verticals to draw from: healthcare, legal, education, manufacturing, scientific research, security/compliance, agriculture, real estate, transportation. For a capability like "parse inventory CSV and reorder YAML", the 5 cross paraphrases might be:
  - security: "parse-users-csv-and-access-policy-yaml"
  - healthcare: "parse-patients-csv-and-treatment-protocol-yaml"
  - education: "parse-students-csv-and-grading-rules-yaml"
  - legal: "parse-cases-csv-and-discovery-rules-yaml"
  - manufacturing: "parse-machines-csv-and-maintenance-schedule-yaml"
All 5 must come from DIFFERENT verticals. Kebab-case, 5-12 words.

Rules:
- Each list must have exactly the required length (tight=2, mid=5, cross=5).
- Within mid_paraphrases, the 5 must cover 5 distinct sub-domains; no two should be from the same sub-domain.
- Within cross_context_paraphrases, the 5 must be from 5 distinct verticals; no two from the same vertical.
- All paraphrases are realistic decomposer outputs (kebab-case, 5-12 words, action-oriented).
"""


@dataclass
class PhaseDProbeResult:
    level: Literal["tight", "mid", "cross"]
    query: str
    fired: bool
    fired_on: str | None
    fired_on_target: bool
    score: float
    compat_verdict: CompatibilityVerdict | None
    compat_error: str | None = None


@dataclass
class PhaseDResult:
    capability_name: str
    capability_type: str
    specificity: str
    specificity_reason: str
    probes: list[PhaseDProbeResult]
    generation_error: str | None = None


def run_phase_d(
    registry: SkillRegistry, threshold: float
) -> list[PhaseDResult]:
    """Generate reusability probes for every non-pinned capability and audit."""
    from langchain.chat_models import init_chat_model

    llm = init_chat_model(DEFAULT_LLM_MODEL, temperature=0)
    probe_gen = llm.with_structured_output(
        GeneralizationProbes, method="function_calling"
    )
    judge = llm.with_structured_output(
        CompatibilityVerdict, method="function_calling"
    )

    results: list[PhaseDResult] = []
    candidates = [
        cap
        for cap in registry.list_all(is_active=None)
        if (cap.metadata or {}).get("pinned") != "true"
    ]
    print(
        f"\n  Phase D: generating reusability probes for "
        f"{len(candidates)} capability(ies)...",
        flush=True,
    )

    for idx, cap in enumerate(candidates, 1):
        print(
            f"    [{idx:2d}/{len(candidates)}] {cap.name} — generating probes...",
            flush=True,
        )
        try:
            probes_data = probe_gen.invoke(
                _GENERALIZATION_PROMPT.format(
                    name=cap.name,
                    ctype=cap.capability_type.value,
                    description=cap.description,
                    implementation=_get_implementation(cap),
                )
            )
        except Exception as exc:
            results.append(
                PhaseDResult(
                    capability_name=cap.name,
                    capability_type=cap.capability_type.value,
                    specificity="(error)",
                    specificity_reason="",
                    probes=[],
                    generation_error=f"{type(exc).__name__}: {str(exc)[:140]}",
                )
            )
            continue

        # Iterate over the three paraphrase lists — 2 tight + 5 mid + 5 cross
        # = 12 probes per capability. (Was 3 in the earlier single-paraphrase
        # version; scaled up for Step 3.5 sample size.)
        probe_results: list[PhaseDProbeResult] = []
        all_probes: list[tuple[str, str]] = []
        for query in probes_data.tight_paraphrases:
            all_probes.append(("tight", query))
        for query in probes_data.mid_paraphrases:
            all_probes.append(("mid", query))
        for query in probes_data.cross_context_paraphrases:
            all_probes.append(("cross", query))

        for level, query in all_probes:
            hits = registry.search(query, n_results=3)
            if not hits:
                probe_results.append(
                    PhaseDProbeResult(
                        level=level,
                        query=query,
                        fired=False,
                        fired_on=None,
                        fired_on_target=False,
                        score=0.0,
                        compat_verdict=None,
                    )
                )
                continue

            top_cap, top_score = hits[0]
            fired = top_score >= threshold
            fired_on_target = fired and top_cap.name == cap.name

            compat_verdict: CompatibilityVerdict | None = None
            compat_error: str | None = None
            if fired:
                audit_prompt = _AUDIT_PROMPT.format(
                    query=query,
                    name=top_cap.name,
                    ctype=top_cap.capability_type.value,
                    description=top_cap.description,
                    implementation=_get_implementation(top_cap),
                )
                try:
                    compat_verdict = judge.invoke(audit_prompt)
                except Exception as exc:
                    compat_error = f"{type(exc).__name__}: {str(exc)[:140]}"

            probe_results.append(
                PhaseDProbeResult(
                    level=level,
                    query=query,
                    fired=fired,
                    fired_on=top_cap.name if fired else None,
                    fired_on_target=fired_on_target,
                    score=top_score,
                    compat_verdict=compat_verdict,
                    compat_error=compat_error,
                )
            )

        results.append(
            PhaseDResult(
                capability_name=cap.name,
                capability_type=cap.capability_type.value,
                specificity=probes_data.specificity,
                specificity_reason=probes_data.specificity_reason,
                probes=probe_results,
            )
        )

    return results


# ---------------------------------------------------------------------------
# Phase E — domain-filter comparison (B1 Step 3.5b+c)
#
# Re-runs every Phase D probe both WITH and WITHOUT the domain filter and
# compares the outcomes per probe. Measures whether adding a domain filter
# to registry.search() reduces cross-sub-domain false positives without
# regressing tight-level recall.
#
# *** Testbed-scoped measurement only ***
#
# The 13-category taxonomy below is the same hardcoded list used by
# scripts/migrate_business_domain_labels.py. It is scoped to the Business
# Analytics testbed for measurement reproducibility — NOT a production
# mechanism. Production MINT CLI uses an evolutionary taxonomy
# (src/orchestrator/taxonomy.py post-B1) that grows organically. See
# docs/MASTER_PLAN_2026_04_13.md Section 6 Phase 2 Step 3.5 for the
# two-layer design rationale.
# ---------------------------------------------------------------------------


# Hardcoded taxonomy — keep in sync with
# scripts/migrate_business_domain_labels.py TAXONOMY_ALL.
PHASE_E_TAXONOMY = (
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
)


class QueryDomainInference(BaseModel):
    """Structured output from the query domain inference LLM call."""

    domain: Literal[
        "data-processing",
        "text-analysis",
        "reporting",
        "analytics",
        "mathematics",
        "sales",
        "inventory",
        "transactions",
        "finance",
        "fraud",
        "operations",
        "hr",
        "general",
    ] = Field(
        description=(
            "The most specific domain label that matches the query. "
            "Prefer business sub-domains (sales, inventory, operations, "
            "etc.) over functional labels (data-processing) when the "
            "query is clearly bound to a business context. Use 'general' "
            "only as a last resort when nothing fits."
        )
    )
    rationale: str = Field(
        description="One-sentence justification citing a specific term in the query."
    )


_QUERY_DOMAIN_PROMPT = """You are labeling a capability registry query with a domain tag.

QUERY:
{query}

TAXONOMY (pick exactly one):

Functional labels (use for generic queries that could apply to any business vertical):
- data-processing — generic CSV/JSON parsing, cleaning, transformation, joining, validation
- text-analysis — word counting, NLP, generic text statistics
- reporting — generic report generation, markdown/text output formatting
- analytics — generic statistical analysis, trend detection, generic anomaly scoring
- mathematics — pure mathematical computation (fibonacci, etc.)

Business sub-domain labels (prefer these when the query uses business vocabulary — "sales", "inventory", "fraud", "revenue", "stock", etc.):
- sales — sales, revenue, products, orders, pricing, customer purchases
- inventory — stock, SKU, reorder, warehouse, supply-chain
- transactions — payment transactions, transfers, ledger entries (finance-level, not sales-level)
- finance — budget, accounting, general ledger, expense, reconciliation
- fraud — fraud detection, suspicious payments
- operations — system operations, incidents, monitoring, metrics, devops, service health
- hr — employees, payroll, benefits, HR data

Fallback:
- general — use ONLY if no other label fits

Rules:
1. If the query contains a business-vocabulary term, prefer the matching business sub-domain.
2. Only use "general" as a last resort.
3. Pick exactly one from the taxonomy above.

Return a QueryDomainInference with the chosen domain and a one-sentence rationale.
"""


def infer_query_domain(query: str, inference_llm) -> QueryDomainInference:
    """Classify a query into the hardcoded Phase E taxonomy."""
    return inference_llm.invoke(_QUERY_DOMAIN_PROMPT.format(query=query))


# Outcome classification: what did the filter do to this probe?
#
# The baseline-correctness axis factors in the Phase D compatibility judge
# verdict, not just fired_on_target. A probe that fired on its target cap
# but was judged INCOMPAT is a silent-poisoning case — the cap's description
# matched but its implementation does not actually serve the query. The
# filter should block those, just like it should block wrong-cap fires.
#
# Baseline correct = baseline_fired AND fired_on_target AND compat_verdict.compatible
#   (also True if compat_verdict is None / judge errored — optimistic default)
# Baseline wrong   = baseline_fired AND (NOT fired_on_target OR NOT compat)
PhaseEOutcome = Literal[
    # Baseline was correct (tight self-match, or judged-compat reuse)
    "filter_kept_correct",             # baseline correct → filter kept (GOOD — recall preserved)
    "filter_lost_correct",             # baseline correct → filter dropped (BAD — recall hurt)
    # Baseline was wrong — split into two subtypes:
    "filter_blocked_wrong_cap",        # baseline fired on a DIFFERENT cap → filter dropped (GOOD — wrong-cap FP blocked)
    "filter_missed_wrong_cap",         # baseline fired on a DIFFERENT cap → filter kept it (MISS — filter didn't help)
    "filter_blocked_silent_poisoning", # baseline fired on target but INCOMPAT → filter dropped (GOOD — silent poisoning caught)
    "filter_missed_silent_poisoning",  # baseline fired on target but INCOMPAT → filter kept it (MISS — filter didn't help)
    # Baseline didn't fire
    "both_null",                       # baseline null → filter null (NEUTRAL)
    "baseline_null_filter_fired",      # baseline null → filter fired (UNUSUAL — filter is a subset, shouldn't happen)
    # Other
    "inference_error",                 # LLM domain inference raised an exception
]


def _baseline_was_correct(d_probe: PhaseDProbeResult) -> bool | None:
    """Ground-truth: was the baseline fire a correct match the filter should preserve?

    Returns:
        True  — filter should keep it (tight self-match, or judged-compat reuse)
        False — filter should block it (silent poisoning or wrong-cap fire)
        None  — baseline didn't fire at all (no ground truth to compare)

    A probe that fired on its target cap but was judged INCOMPAT by the
    Phase D compatibility judge counts as "wrong" (silent poisoning) even
    though fired_on_target is True. If the judge did not run or errored
    (compat_verdict is None on a fire), fall back to the optimistic
    assumption that fired_on_target implies correct.
    """
    if not d_probe.fired:
        return None
    if not d_probe.fired_on_target:
        return False  # fired on a different cap → definitely wrong
    if d_probe.compat_verdict is None:
        return True  # judge didn't run / errored → optimistic default
    return d_probe.compat_verdict.compatible


@dataclass
class PhaseEProbeResult:
    """One probe re-run through the domain filter for the Phase E comparison."""

    level: Literal["tight", "mid", "cross"]
    query: str
    target_cap: str
    inferred_domain: str
    # Baseline (from Phase D, no filter)
    baseline_fired: bool
    baseline_fired_on: str | None
    baseline_fired_on_target: bool
    baseline_score: float
    baseline_compat: bool | None  # from Phase D compat judge (None = no fire or judge error)
    # Filtered (new — with domain filter)
    filtered_fired: bool
    filtered_fired_on: str | None
    filtered_fired_on_target: bool
    filtered_score: float
    # Classification
    outcome: PhaseEOutcome
    inference_error: str | None = None


@dataclass
class PhaseEResult:
    """Aggregated Phase E result for one capability (mirrors PhaseDResult)."""

    capability_name: str
    capability_domain: str  # the migrated/hardcoded domain stored in the registry
    probes: list[PhaseEProbeResult]
    error: str | None = None


def run_phase_e(
    registry: SkillRegistry,
    phase_d_results: list[PhaseDResult],
    threshold: float,
) -> list[PhaseEResult]:
    """Re-run each Phase D probe WITH and WITHOUT the domain filter.

    Requires Phase D results as input — Phase E is a secondary analysis
    layer on top of Phase D's probes, not an independent probe generator.

    For each Phase D probe:
      1. Infer the query's domain via an LLM call (cached per unique query).
      2. Run registry.search(query, domain=inferred_domain).
      3. Compare against Phase D's baseline (unfiltered) result.
      4. Classify the outcome into one of six buckets.

    Aggregate metrics are computed in _print_phase_e():
      - Tight-level recall preservation rate (how many correct fires
        survived the filter)
      - Mid/cross-level false-positive reduction rate (how many wrong-cap
        fires were blocked by the filter)
    """
    from langchain.chat_models import init_chat_model

    llm = init_chat_model(DEFAULT_LLM_MODEL, temperature=0)
    inference_llm = llm.with_structured_output(
        QueryDomainInference, method="function_calling"
    )

    # Cache domain inferences per unique query — probes often share queries
    # across capabilities (shouldn't, but the cache is cheap).
    domain_cache: dict[str, QueryDomainInference] = {}

    # Look up capability domains once — needed for the results aggregation.
    cap_domain_map: dict[str, str] = {}
    for cap in registry.list_all(is_active=None):
        cap_domain_map[cap.name] = cap.domain

    total = len(phase_d_results)
    print(
        f"\n  Phase E: re-running {total} capability(ies)' probes through the "
        f"domain filter...",
        flush=True,
    )

    results: list[PhaseEResult] = []
    for idx, pd_result in enumerate(phase_d_results, 1):
        print(
            f"    [{idx:2d}/{total}] {pd_result.capability_name}",
            flush=True,
        )
        if pd_result.generation_error:
            results.append(
                PhaseEResult(
                    capability_name=pd_result.capability_name,
                    capability_domain=cap_domain_map.get(
                        pd_result.capability_name, "(unknown)"
                    ),
                    probes=[],
                    error=pd_result.generation_error,
                )
            )
            continue

        e_probes: list[PhaseEProbeResult] = []
        for d_probe in pd_result.probes:
            # Infer the query's domain
            inference_error: str | None = None
            if d_probe.query in domain_cache:
                inference = domain_cache[d_probe.query]
            else:
                try:
                    inference = infer_query_domain(d_probe.query, inference_llm)
                    domain_cache[d_probe.query] = inference
                except Exception as exc:  # noqa: BLE001
                    inference_error = f"{type(exc).__name__}: {str(exc)[:120]}"
                    inference = None  # type: ignore[assignment]

            # Pre-compute the baseline compat verdict for the classifier.
            baseline_compat: bool | None = None
            if d_probe.compat_verdict is not None:
                baseline_compat = d_probe.compat_verdict.compatible

            if inference is None:
                # LLM inference failed — record and skip the filtered path.
                e_probes.append(
                    PhaseEProbeResult(
                        level=d_probe.level,
                        query=d_probe.query,
                        target_cap=pd_result.capability_name,
                        inferred_domain="(error)",
                        baseline_fired=d_probe.fired,
                        baseline_fired_on=d_probe.fired_on,
                        baseline_fired_on_target=d_probe.fired_on_target,
                        baseline_score=d_probe.score,
                        baseline_compat=baseline_compat,
                        filtered_fired=False,
                        filtered_fired_on=None,
                        filtered_fired_on_target=False,
                        filtered_score=0.0,
                        outcome="inference_error",
                        inference_error=inference_error,
                    )
                )
                continue

            # Re-run the probe with the domain filter
            filtered_hits = registry.search(
                d_probe.query, n_results=3, domain=inference.domain
            )
            if filtered_hits and filtered_hits[0][1] >= threshold:
                filtered_fired = True
                filtered_fired_on = filtered_hits[0][0].name
                filtered_fired_on_target = (
                    filtered_fired_on == pd_result.capability_name
                )
                filtered_score = filtered_hits[0][1]
            else:
                filtered_fired = False
                filtered_fired_on = None
                filtered_fired_on_target = False
                filtered_score = filtered_hits[0][1] if filtered_hits else 0.0

            # Classify the outcome using the baseline-was-correct ground truth,
            # which factors in the Phase D compat judge verdict (silent-
            # poisoning cases have fired_on_target=True but compat=False).
            baseline_fired = d_probe.fired
            baseline_fired_on_target = d_probe.fired_on_target
            baseline_correct = _baseline_was_correct(d_probe)

            if baseline_correct is None:
                # Baseline didn't fire at all.
                if filtered_fired:
                    outcome: PhaseEOutcome = "baseline_null_filter_fired"
                else:
                    outcome = "both_null"
            elif baseline_correct is True:
                # Baseline fire was a correct match the filter should preserve.
                if filtered_fired and filtered_fired_on_target:
                    outcome = "filter_kept_correct"
                else:
                    # Filter dropped the correct fire OR the filter somehow
                    # redirected to a different cap — treat both as recall loss.
                    outcome = "filter_lost_correct"
            else:
                # baseline_correct is False — baseline fire was wrong somehow.
                # Subdivide: was it a wrong-cap fire or a silent-poisoning
                # fire (on target but INCOMPAT)?
                is_silent_poisoning = baseline_fired_on_target  # fired on self but INCOMPAT
                if filtered_fired:
                    # Filter kept a fire we wanted blocked.
                    if is_silent_poisoning:
                        outcome = "filter_missed_silent_poisoning"
                    else:
                        outcome = "filter_missed_wrong_cap"
                else:
                    # Filter blocked a fire we wanted blocked — the desired
                    # behavior.
                    if is_silent_poisoning:
                        outcome = "filter_blocked_silent_poisoning"
                    else:
                        outcome = "filter_blocked_wrong_cap"

            e_probes.append(
                PhaseEProbeResult(
                    level=d_probe.level,
                    query=d_probe.query,
                    target_cap=pd_result.capability_name,
                    inferred_domain=inference.domain,
                    baseline_fired=baseline_fired,
                    baseline_fired_on=d_probe.fired_on,
                    baseline_fired_on_target=baseline_fired_on_target,
                    baseline_score=d_probe.score,
                    baseline_compat=baseline_compat,
                    filtered_fired=filtered_fired,
                    filtered_fired_on=filtered_fired_on,
                    filtered_fired_on_target=filtered_fired_on_target,
                    filtered_score=filtered_score,
                    outcome=outcome,
                )
            )

        results.append(
            PhaseEResult(
                capability_name=pd_result.capability_name,
                capability_domain=cap_domain_map.get(
                    pd_result.capability_name, "(unknown)"
                ),
                probes=e_probes,
            )
        )

    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


_VERDICT_FLAG: dict[str, str] = {
    "true-positive": "[OK]   ",
    "true-negative": "[OK]   ",
    "false-positive": "[FAIL] ",
    "false-negative": "[FAIL] ",
    "wrong-seed-fired": "[FAIL] ",
    "context-trap-fired": "[TRAP] ",
    "context-trap-did-not-fire": "[OK]   ",
}


def _print_phase_a(results: list[PhaseAResult], verbose: bool) -> None:
    print("\n" + "=" * 78)
    print("  PHASE A — Naive Semantic Probe (ChromaDB cosine similarity)")
    print("=" * 78)

    def _section(title: str, predicate) -> None:
        filtered = [r for r in results if predicate(r)]
        if not filtered:
            return
        print(f"\n  {title} ({len(filtered)})")
        print("  " + "-" * 74)
        for r in filtered:
            flag = _VERDICT_FLAG.get(r.verdict, "[?]    ")
            print(f"  {flag} {r.probe.query[:62]}")
            if r.fired:
                note = ""
                if r.probe.expected_seed and r.fired_seed != r.probe.expected_seed:
                    note = f"  (expected: {r.probe.expected_seed})"
                print(f"         -> {r.fired_seed} @ {r.fired_score:.3f}{note}")
            else:
                top_str = r.top_matches[0] if r.top_matches else ("—", 0.0)
                print(
                    f"         no fire (top: {top_str[0]} @ {top_str[1]:.3f})"
                )
            if verbose and r.top_matches:
                rest = ", ".join(f"{n}@{s:.2f}" for n, s in r.top_matches)
                print(f"         top-3: {rest}")
            if r.probe.note and verbose:
                print(f"         note: {r.probe.note}")

    _section(
        "POSITIVE probes (expected to fire on the listed seed)",
        lambda r: r.probe.expected_seed is not None and r.probe.context_compatible,
    )
    _section(
        "NEGATIVE probes (expected to miss entirely)",
        lambda r: r.probe.expected_seed is None,
    )
    _section(
        "CONTEXT TRAP probes (expected to fire, but incompatible)",
        lambda r: r.probe.expected_seed is not None and not r.probe.context_compatible,
    )


def _print_phase_b(phase_b: list[PhaseBResult], verbose: bool) -> None:
    print("\n" + "=" * 78)
    print("  PHASE B — Context Compatibility Audit (LLM judge per fired match)")
    print("=" * 78)
    if not phase_b:
        print("\n  (no phase-A matches to audit)")
        return

    def _bucket_title(probe: Probe) -> str:
        if probe.expected_seed is None:
            return "NEGATIVE (unexpected fire)"
        if probe.context_compatible:
            return "POSITIVE (expected compatible)"
        return "CONTEXT TRAP (expected incompatible)"

    # group output by bucket for readability
    for bucket in (
        "POSITIVE (expected compatible)",
        "CONTEXT TRAP (expected incompatible)",
        "NEGATIVE (unexpected fire)",
    ):
        group = [r for r in phase_b if _bucket_title(r.probe) == bucket]
        if not group:
            continue
        print(f"\n  {bucket} ({len(group)})")
        print("  " + "-" * 74)
        for r in group:
            if r.error:
                print(f"  [ERR]  {r.probe.query[:62]}")
                print(f"         judge error: {r.error}")
                continue
            v = r.verdict
            if v is None:
                continue
            expected_compat = r.probe.context_compatible and r.probe.expected_seed is not None
            correct = v.compatible == expected_compat
            mark = "[OK]   " if correct else "[FAIL] "
            compat_str = "compatible" if v.compatible else "INCOMPATIBLE"
            print(f"  {mark} {r.probe.query[:62]}")
            print(f"         matched: {r.matched_cap} @ {r.top_score:.3f}")
            print(f"         judge: {compat_str} ({v.confidence}) — {v.reason}")
            if not correct:
                exp = "compatible" if expected_compat else "incompatible"
                print(f"         expected: {exp}")
            if verbose and r.probe.note:
                print(f"         note: {r.probe.note}")


def _print_phase_c(phase_c: list[PhaseCResult], verbose: bool) -> None:
    print("\n" + "=" * 78)
    print("  PHASE C — Self-Consistency Audit (description vs. implementation)")
    print("=" * 78)
    if not phase_c:
        print("\n  (no capabilities to audit)")
        return

    # Group by consistency
    drifts = [r for r in phase_c if r.verdict and not r.verdict.consistent]
    clean = [r for r in phase_c if r.verdict and r.verdict.consistent]
    errors = [r for r in phase_c if r.verdict is None]

    if drifts:
        print(f"\n  DRIFT FOUND ({len(drifts)})")
        print("  " + "-" * 74)
        for r in drifts:
            v = r.verdict
            print(f"  [DRIFT] {r.capability_name}  ({r.capability_type})")
            print(f"          type: {v.drift_type} (confidence: {v.confidence})")
            print(f"          desc: {r.description[:140]}")
            print(f"          reason: {v.reason}")

    if clean:
        print(f"\n  CONSISTENT ({len(clean)})")
        print("  " + "-" * 74)
        for r in clean:
            print(f"  [OK]    {r.capability_name}  ({r.capability_type})")
            if verbose:
                print(f"          reason: {r.verdict.reason}")

    if errors:
        print(f"\n  JUDGE ERRORS ({len(errors)})")
        print("  " + "-" * 74)
        for r in errors:
            print(f"  [ERR]   {r.capability_name}: {r.error}")


def _probe_status_flag(p: PhaseDProbeResult) -> str:
    if not p.fired:
        return "[MISS]    "
    if not p.fired_on_target:
        return "[WRONG]   "
    if p.compat_verdict and not p.compat_verdict.compatible:
        return "[INCOMPAT]"
    if p.compat_verdict and p.compat_verdict.compatible:
        return "[REUSE]   "
    return "[FIRED]   "


def _print_phase_d(phase_d: list[PhaseDResult], verbose: bool) -> None:
    print("\n" + "=" * 78)
    print("  PHASE D — Generalization Audit (reusability probes)")
    print("=" * 78)
    if not phase_d:
        print("\n  (no non-pinned capabilities to audit — is the registry empty?)")
        return

    print(f"\n  Auditing {len(phase_d)} generated capabilities for reusability.")
    print("  Probe levels:")
    print("    tight  — same domain, reworded  (self-reuse sanity check)")
    print("    mid    — same shape, different domain vocabulary")
    print("    cross  — different business vertical")

    for r in phase_d:
        print()
        if r.generation_error:
            print(f"  [ERR] {r.capability_name}: {r.generation_error}")
            continue
        print(f"  {r.capability_name}  ({r.capability_type})")
        print(f"    specificity: {r.specificity}")
        print(f"    reason: {r.specificity_reason}")
        for p in r.probes:
            flag = _probe_status_flag(p)
            print(f"    {flag} {p.level:5}  {p.query}")
            if p.fired:
                if p.fired_on_target:
                    print(f"               fired: (self) @ {p.score:.3f}")
                else:
                    print(f"               fired: -> {p.fired_on} @ {p.score:.3f}")
                if p.compat_verdict:
                    c = "compat" if p.compat_verdict.compatible else "INCOMPAT"
                    reason = p.compat_verdict.reason
                    if len(reason) > 200 and not verbose:
                        reason = reason[:200] + "..."
                    print(
                        f"               judge: {c} "
                        f"({p.compat_verdict.confidence}) — {reason}"
                    )
                elif p.compat_error:
                    print(f"               judge ERR: {p.compat_error}")
            else:
                print(f"               no fire (top score: {p.score:.3f})")


# Mapping from outcome enum to a short tag for the per-probe line.
#
# Flags are grouped by desirability:
#   GOOD (filter helped):         [KEEP] [FIX-W] [FIX-S]
#   NEUTRAL (no effect):          [NEUT]
#   BAD-recall (filter overshot): [LOSS]
#   BAD-miss (filter missed):     [MISS-W] [MISS-S]
#   ODD (rare / error):           [????] [ERR ]
_PHASE_E_OUTCOME_FLAG: dict[str, str] = {
    "filter_kept_correct":             "[KEEP ]",   # good — recall preserved
    "filter_lost_correct":             "[LOSS ]",   # bad  — recall regression
    "filter_blocked_wrong_cap":        "[FIX-W]",   # good — wrong-cap false positive blocked
    "filter_missed_wrong_cap":         "[MIS-W]",   # bad  — filter didn't help
    "filter_blocked_silent_poisoning": "[FIX-S]",   # good — silent poisoning caught
    "filter_missed_silent_poisoning":  "[MIS-S]",   # bad  — filter didn't help (silent poisoning kept)
    "both_null":                       "[NEUT ]",   # neutral — no fire either way
    "baseline_null_filter_fired":      "[?????]",   # unusual — filter is a subset, shouldn't happen
    "inference_error":                 "[ERR  ]",   # LLM domain inference failed
}


def _print_phase_e(phase_e: list[PhaseEResult], verbose: bool) -> None:
    """Print Phase E per-probe comparison and the aggregate decision-gate metrics."""
    print("\n" + "=" * 78)
    print("  PHASE E — Domain-Filter Comparison (B1 Step 3.5b)")
    print("=" * 78)
    if not phase_e:
        print("\n  (no capabilities to compare — run with --generalization-audit first)")
        return

    errors = [r for r in phase_e if r.error or not r.probes]
    ok = [r for r in phase_e if not r.error and r.probes]

    # Per-capability per-probe listing
    if verbose or ok:
        for r in ok:
            print(
                f"\n  {r.capability_name}  [registry domain: {r.capability_domain}]"
            )
            for p in r.probes:
                flag = _PHASE_E_OUTCOME_FLAG.get(p.outcome, "[????]")
                base_str = (
                    f"-> {p.baseline_fired_on} @ {p.baseline_score:.3f}"
                    if p.baseline_fired
                    else f"(none @ {p.baseline_score:.3f})"
                )
                filt_str = (
                    f"-> {p.filtered_fired_on} @ {p.filtered_score:.3f}"
                    if p.filtered_fired
                    else f"(none @ {p.filtered_score:.3f})"
                )
                print(
                    f"    {flag} {p.level:5s} inferred={p.inferred_domain:16s} {p.query[:60]}"
                )
                print(f"              base: {base_str}")
                print(f"              filt: {filt_str}")
                if p.outcome == "inference_error" and p.inference_error:
                    print(f"              infer err: {p.inference_error}")

    if errors:
        print(f"\n  ERRORS ({len(errors)})")
        print("  " + "-" * 74)
        for r in errors:
            msg = r.error or "(no probes — probably Phase D error)"
            print(f"  [ERR]   {r.capability_name}: {msg}")

    # Aggregate metrics
    print("\n  " + "-" * 74)
    print("  Phase E Decision Gate Metrics")
    print("  " + "-" * 74)

    all_probes: list[PhaseEProbeResult] = []
    for r in phase_e:
        all_probes.extend(r.probes)

    if not all_probes:
        print("\n  (no probes to aggregate)")
        return

    # Count outcomes per level
    def _counts_by_outcome(probes: list[PhaseEProbeResult]) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in probes:
            out[p.outcome] = out.get(p.outcome, 0) + 1
        return out

    tight = [p for p in all_probes if p.level == "tight"]
    mid = [p for p in all_probes if p.level == "mid"]
    cross = [p for p in all_probes if p.level == "cross"]

    for level_name, level_probes in (
        ("tight", tight),
        ("mid  ", mid),
        ("cross", cross),
        ("ALL  ", all_probes),
    ):
        counts = _counts_by_outcome(level_probes)
        total = len(level_probes)
        if total == 0:
            continue
        kept = counts.get("filter_kept_correct", 0)
        lost = counts.get("filter_lost_correct", 0)
        blocked_w = counts.get("filter_blocked_wrong_cap", 0)
        blocked_s = counts.get("filter_blocked_silent_poisoning", 0)
        missed_w = counts.get("filter_missed_wrong_cap", 0)
        missed_s = counts.get("filter_missed_silent_poisoning", 0)
        neutral = counts.get("both_null", 0)
        errs = counts.get("inference_error", 0) + counts.get(
            "baseline_null_filter_fired", 0
        )
        print(
            f"\n  {level_name} ({total} probes):"
            f"\n        kept_correct={kept}  lost_correct={lost}"
            f"\n        blocked_wrong={blocked_w}  blocked_silent_poison={blocked_s}"
            f"\n        missed_wrong={missed_w}  missed_silent_poison={missed_s}"
            f"\n        neutral={neutral}  err={errs}"
        )

    # Decision-gate computation (corrected to factor in the Phase D compat
    # verdict — baseline fires where the cap matched via cosine but were
    # judged INCOMPAT count as silent-poisoning to block, not as correct
    # matches to preserve).

    # Recall preservation: of baseline fires that were correct (tight self-
    # match or mid/cross judged-compat reuse), how many did the filter keep?
    baseline_correct = [
        p
        for p in all_probes
        if p.outcome in ("filter_kept_correct", "filter_lost_correct")
    ]
    recall_preserved = [
        p for p in baseline_correct if p.outcome == "filter_kept_correct"
    ]
    recall_rate = (
        len(recall_preserved) / len(baseline_correct)
        if baseline_correct
        else float("nan")
    )

    # FP reduction: of baseline fires that were wrong (wrong cap OR silent
    # poisoning), how many did the filter block?
    baseline_wrong = [
        p
        for p in all_probes
        if p.outcome
        in (
            "filter_blocked_wrong_cap",
            "filter_missed_wrong_cap",
            "filter_blocked_silent_poisoning",
            "filter_missed_silent_poisoning",
        )
    ]
    fp_blocked = [
        p
        for p in baseline_wrong
        if p.outcome
        in ("filter_blocked_wrong_cap", "filter_blocked_silent_poisoning")
    ]
    fp_reduction_rate = (
        len(fp_blocked) / len(baseline_wrong) if baseline_wrong else float("nan")
    )

    # Separate sub-counts for the narrative — we care about cross-sub-domain
    # silent poisoning specifically (the Phase D target), distinct from
    # within-sub-domain wrong-cap fires (the Jaccard gate's target).
    silent_poison_blocked = sum(
        1 for p in all_probes if p.outcome == "filter_blocked_silent_poisoning"
    )
    silent_poison_missed = sum(
        1 for p in all_probes if p.outcome == "filter_missed_silent_poisoning"
    )
    silent_poison_total = silent_poison_blocked + silent_poison_missed
    wrong_cap_blocked = sum(
        1 for p in all_probes if p.outcome == "filter_blocked_wrong_cap"
    )
    wrong_cap_missed = sum(
        1 for p in all_probes if p.outcome == "filter_missed_wrong_cap"
    )
    wrong_cap_total = wrong_cap_blocked + wrong_cap_missed

    print("\n  " + "-" * 74)
    print("  Decision Gate")
    print("  " + "-" * 74)
    print(
        f"    Baseline-correct fires:  {len(baseline_correct)} "
        f"(tight self-match + mid/cross judged compat)"
    )
    print(
        f"    Recall preserved:        {len(recall_preserved)} / "
        f"{len(baseline_correct)}  = {recall_rate:.1%}"
    )
    print(
        f"\n    Baseline-wrong fires:    {len(baseline_wrong)} "
        f"(fires the judge / target ID says should be blocked)"
    )
    print(
        f"      ├─ silent poisoning:   {silent_poison_total} "
        f"(mid/cross fired on target but INCOMPAT)"
    )
    print(
        f"      │   ├─ blocked:        {silent_poison_blocked}"
    )
    print(
        f"      │   └─ missed:         {silent_poison_missed}"
    )
    print(
        f"      └─ wrong-cap fires:    {wrong_cap_total} "
        f"(fired on a different cap)"
    )
    print(
        f"          ├─ blocked:        {wrong_cap_blocked}"
    )
    print(
        f"          └─ missed:         {wrong_cap_missed}"
    )
    print(
        f"\n    FP reduction (overall): {len(fp_blocked)} / {len(baseline_wrong)}  "
        f"= {fp_reduction_rate:.1%}"
    )

    print("\n  Gate thresholds for productionization (per master plan Step 3.5e):")
    print("    - Recall preserved           ≥ 95% of baseline")
    print("    - False positives blocked    ≥ 50% of baseline wrong fires")

    passes_recall = (
        recall_rate >= 0.95
        if len(baseline_correct) > 0
        else True  # vacuously true with no baseline-correct probes
    )
    passes_fp = (
        fp_reduction_rate >= 0.5
        if len(baseline_wrong) > 0
        else False  # if nothing was wrong, there's nothing to fix
    )

    print("\n  Verdict:")
    print(
        f"    Recall gate:              "
        f"{'PASS' if passes_recall else 'FAIL'}  "
        f"({recall_rate:.1%} {'≥' if passes_recall else '<'} 95%)"
    )
    if len(baseline_wrong) == 0:
        print(
            "    FP-reduction gate:        N/A  "
            "(baseline had zero wrong-cap fires — filter cannot help)"
        )
    else:
        print(
            f"    FP-reduction gate:        "
            f"{'PASS' if passes_fp else 'FAIL'}  "
            f"({fp_reduction_rate:.1%} {'≥' if passes_fp else '<'} 50%)"
        )

    if passes_recall and passes_fp:
        print("\n  [PASS] Productionize via Step 3.5f (evolutionary taxonomy).")
    elif passes_recall and len(baseline_wrong) == 0:
        print(
            "\n  [N/A] Recall preserved but there were no cross-sub-domain false "
            "positives in this run to fix. Inconclusive — re-run with more probes "
            "or accept Path 1 (wording-only) framing."
        )
    else:
        print("\n  [FAIL] Defer to Chapter 7 via Step 3.5g. Document findings.")


def _print_summary(
    phase_a: list[PhaseAResult],
    phase_b: list[PhaseBResult],
    phase_c: list[PhaseCResult],
    phase_d: list[PhaseDResult],
) -> bool:
    print("\n" + "=" * 78)
    print("  SUMMARY")
    print("=" * 78)

    pos = [
        r
        for r in phase_a
        if r.probe.expected_seed is not None and r.probe.context_compatible
    ]
    neg = [r for r in phase_a if r.probe.expected_seed is None]
    trap = [
        r
        for r in phase_a
        if r.probe.expected_seed is not None and not r.probe.context_compatible
    ]

    pos_hit = sum(1 for r in pos if r.verdict == "true-positive")
    neg_miss = sum(1 for r in neg if r.verdict == "true-negative")
    trap_fired = sum(1 for r in trap if r.verdict == "context-trap-fired")
    trap_missed = sum(1 for r in trap if r.verdict == "context-trap-did-not-fire")

    def _pct(n: int, d: int) -> str:
        return f"{n / d:.0%}" if d else "n/a"

    print()
    print(f"  Phase A (naive semantic matching):")
    print(
        f"    Recall    (positives firing on expected seed): "
        f"{pos_hit}/{len(pos)}  ({_pct(pos_hit, len(pos))})"
    )
    print(
        f"    Precision (negatives correctly rejected):     "
        f"{neg_miss}/{len(neg)}  ({_pct(neg_miss, len(neg))})"
    )
    print(
        f"    Context traps fired (cannot distinguish):     "
        f"{trap_fired}/{len(trap)}  ({_pct(trap_fired, len(trap))})"
    )
    if trap_missed:
        print(
            f"    Context traps that did NOT fire (recall hole): {trap_missed}/{len(trap)}"
        )

    all_pass_a = (pos_hit == len(pos)) and (neg_miss == len(neg))

    all_pass_b = True
    if phase_b:
        pos_audited = [
            r
            for r in phase_b
            if r.probe.expected_seed is not None
            and r.probe.context_compatible
            and r.verdict is not None
        ]
        trap_audited = [
            r
            for r in phase_b
            if r.probe.expected_seed is not None
            and not r.probe.context_compatible
            and r.verdict is not None
        ]

        pos_kept = sum(1 for r in pos_audited if r.verdict.compatible)
        pos_lost = len(pos_audited) - pos_kept
        trap_caught = sum(1 for r in trap_audited if not r.verdict.compatible)
        trap_let_through = len(trap_audited) - trap_caught

        print()
        print(f"  Phase B (LLM compatibility audit):")
        print(
            f"    Audit retention (true positives kept compatible): "
            f"{pos_kept}/{len(pos_audited)}  ({_pct(pos_kept, len(pos_audited))})"
        )
        print(
            f"    Audit recovery  (context traps caught):           "
            f"{trap_caught}/{len(trap_audited)}  "
            f"({_pct(trap_caught, len(trap_audited))})"
        )

        if pos_lost:
            print(
                f"    WARN: {pos_lost} true positive(s) judged incompatible — "
                f"precision cost"
            )
        if trap_let_through:
            print(
                f"    WARN: {trap_let_through} context trap(s) judged compatible — "
                f"silent poisoning risk"
            )

        # Pass criteria for phase B: every trap must be caught AND
        # every true positive must be retained.
        all_pass_b = trap_let_through == 0 and pos_lost == 0

        # Derived metric: naive vs. audit-adjusted precision on the
        # positive+trap universe. This is the number the thesis cares
        # about — "how much worse is precision when you account for
        # context incompatibility?"
        total_fires = len(pos_audited) + len(trap_audited)
        if total_fires:
            naive_precision = len(pos_audited) / total_fires
            # audit-adjusted: a trap-let-through is a false positive;
            # a pos-lost is a false negative (not a precision loss).
            audit_precision = pos_kept / total_fires
            print()
            print(
                f"  Naive precision on fires     (pos / (pos+trap)):            "
                f"{naive_precision:.0%}"
            )
            print(
                f"  Audit-adjusted precision     (pos_kept / (pos+trap)):       "
                f"{audit_precision:.0%}"
            )
            print(
                f"  Delta (how much context-incompatibility the audit exposes): "
                f"{(naive_precision - audit_precision) * 100:+.0f} pp"
            )

    all_pass_c = True
    if phase_c:
        audited = [r for r in phase_c if r.verdict is not None]
        drifts = [r for r in audited if not r.verdict.consistent]
        by_type: dict[str, int] = {}
        for r in drifts:
            by_type[r.verdict.drift_type] = by_type.get(r.verdict.drift_type, 0) + 1
        print()
        print(f"  Phase C (self-consistency audit on {len(audited)} capabilities):")
        print(
            f"    Consistent: {len(audited) - len(drifts)}/{len(audited)}  "
            f"({_pct(len(audited) - len(drifts), len(audited))})"
        )
        if drifts:
            print(f"    Drift: {len(drifts)}/{len(audited)}")
            for dt, n in sorted(by_type.items()):
                print(f"      - {dt}: {n}")
        all_pass_c = len(drifts) == 0

    # Phase D is informational — we report aggregates but do not gate
    # overall PASS/FAIL on it. The point is to produce thesis numbers.
    if phase_d:
        audited = [r for r in phase_d if not r.generation_error]
        total_caps = len(audited)
        if total_caps:
            by_spec: dict[str, int] = {}
            for r in audited:
                by_spec[r.specificity] = by_spec.get(r.specificity, 0) + 1

            by_level: dict[str, list[PhaseDProbeResult]] = {
                "tight": [],
                "mid": [],
                "cross": [],
            }
            for r in audited:
                for p in r.probes:
                    by_level[p.level].append(p)

            print()
            print(f"  Phase D (generalization audit on {total_caps} capabilities):")
            print()
            print("    Specificity classification:")
            for spec, n in sorted(by_spec.items()):
                print(f"      {spec}: {n}  ({_pct(n, total_caps)})")

            print()
            print("    Firing rate (paraphrase matches the target capability above threshold):")
            for level in ("tight", "mid", "cross"):
                probes = by_level[level]
                if not probes:
                    continue
                fired_target = sum(1 for p in probes if p.fired_on_target)
                print(
                    f"      {level:5}: {fired_target}/{len(probes)}  "
                    f"({_pct(fired_target, len(probes))})"
                )

            print()
            print("    Reuse rate (fired on target AND judge says compatible):")
            for level in ("tight", "mid", "cross"):
                probes = by_level[level]
                if not probes:
                    continue
                reusable = sum(
                    1
                    for p in probes
                    if p.fired_on_target
                    and p.compat_verdict is not None
                    and p.compat_verdict.compatible
                )
                print(
                    f"      {level:5}: {reusable}/{len(probes)}  "
                    f"({_pct(reusable, len(probes))})"
                )

            print()
            print("    Non-self matches (paraphrase fired on a DIFFERENT capability):")
            for level in ("tight", "mid", "cross"):
                probes = by_level[level]
                if not probes:
                    continue
                wrong = sum(1 for p in probes if p.fired and not p.fired_on_target)
                if wrong:
                    print(f"      {level:5}: {wrong}/{len(probes)}")

    overall = all_pass_a and all_pass_b and all_pass_c
    print()
    print(
        f"  OVERALL: {'PASS' if overall else 'FAIL'}"
        f"  (A: {'pass' if all_pass_a else 'fail'}, "
        f"B: {'pass' if all_pass_b else 'fail'}, "
        f"C: {'pass' if all_pass_c else 'fail'}"
        f"{', D: informational' if phase_d else ''})"
    )
    return overall


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Registry matching validation: probe matching + context audit + "
            "self-consistency audit."
        )
    )
    parser.add_argument(
        "--naive",
        action="store_true",
        help="Run only phase A (skip the LLM audits in B and C).",
    )
    parser.add_argument(
        "--self-audit",
        action="store_true",
        help="Run only phase C (description/implementation drift audit).",
    )
    parser.add_argument(
        "--skip-self-audit",
        action="store_true",
        help="Skip phase C.",
    )
    parser.add_argument(
        "--registry-path",
        type=str,
        default=None,
        help=(
            "Load a persistent ChromaDB registry from this path instead of "
            "seeding an ephemeral one. Disables the hardcoded probe set "
            "(phases A+B) since those expect seeded capability names."
        ),
    )
    parser.add_argument(
        "--generalization-audit",
        action="store_true",
        help=(
            "Run Phase D: for each non-pinned capability in the registry, "
            "use the LLM to generate tight/mid/cross paraphrase probes "
            "and measure whether the capability fires on them (reuse "
            "potential). Informational — does not gate pass/fail."
        ),
    )
    parser.add_argument(
        "--domain-filter-comparison",
        action="store_true",
        help=(
            "Run Phase E: re-run every Phase D probe both WITH and WITHOUT "
            "the domain filter, then report the decision-gate metrics "
            "(recall preservation + false-positive reduction) for B1 "
            "Step 3.5. Implies --generalization-audit. Requires the "
            "registry to have meaningful domain labels (run "
            "scripts/migrate_business_domain_labels.py first)."
        ),
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.6,
        help="Cosine similarity threshold for a match (default: 0.6).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show top-3 matches and per-probe notes.",
    )
    args = parser.parse_args()

    # --verbose controls per-probe display detail (passed to _print_phase_*),
    # NOT logging noise. Keep the root logger at WARNING regardless so the
    # httpx / openai / chromadb DEBUG spam never pollutes the diagnostic
    # output. If you actually want debug logs, set PYTHONLOGLEVEL=DEBUG in
    # the environment.
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.WARNING,
        stream=sys.stderr,
    )
    # Silence noisy third-party loggers explicitly (belt-and-braces in case
    # the env or another import raised the root level).
    for noisy in ("openai", "httpx", "httpcore", "urllib3", "chromadb", "langchain"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Decide which phases to run.
    use_persistent = args.registry_path is not None
    # If loading a persistent registry, probes don't apply — force self-audit.
    run_a = not args.self_audit and not use_persistent
    run_b = not args.naive and not args.self_audit and not use_persistent
    run_c = not args.skip_self_audit and not args.naive

    # Load or seed the registry.
    if use_persistent:
        registry = SkillRegistry(
            persist_directory=args.registry_path,
            collection_name="capabilities",
        )
        print(
            f"\n  Loaded persistent registry from {args.registry_path} "
            f"({registry.count} capabilities)"
        )
    else:
        registry = SkillRegistry()  # ephemeral
        seed_registry(registry)
        print(
            f"\n  Seeded ephemeral registry with {registry.count} capabilities "
            f"(threshold={args.threshold})"
        )
        if run_a:
            print(
                f"  Probes: {len(POSITIVE_PROBES)} positive, "
                f"{len(NEGATIVE_PROBES)} negative, "
                f"{len(CONTEXT_TRAP_PROBES)} context traps"
            )

    phase_a_results: list[PhaseAResult] = []
    phase_b_results: list[PhaseBResult] = []
    phase_c_results: list[PhaseCResult] = []
    phase_d_results: list[PhaseDResult] = []
    phase_e_results: list[PhaseEResult] = []

    # --domain-filter-comparison implies --generalization-audit (Phase D
    # produces the probes that Phase E compares under the filter).
    run_phase_d_flag = args.generalization_audit or args.domain_filter_comparison

    if run_a:
        phase_a_results = run_phase_a(ALL_PROBES, registry, args.threshold)
        _print_phase_a(phase_a_results, args.verbose)

    if run_b:
        phase_b_results = run_phase_b(phase_a_results, registry)
        _print_phase_b(phase_b_results, args.verbose)

    if run_c:
        phase_c_results = run_phase_c(registry)
        _print_phase_c(phase_c_results, args.verbose)

    if run_phase_d_flag:
        phase_d_results = run_phase_d(registry, args.threshold)
        _print_phase_d(phase_d_results, args.verbose)

    if args.domain_filter_comparison:
        phase_e_results = run_phase_e(
            registry, phase_d_results, args.threshold
        )
        _print_phase_e(phase_e_results, args.verbose)

    all_pass = _print_summary(
        phase_a_results, phase_b_results, phase_c_results, phase_d_results
    )
    print()
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
