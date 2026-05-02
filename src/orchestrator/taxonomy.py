"""Evolutionary domain taxonomy for hierarchical capability retrieval (B1 Step 3.5f).

Implements the proposal's Pillar 1 line 77 hierarchical embedding mechanism
(domain → capability → specific skill) as an **evolutionary** taxonomy that
grows organically from capability generation and query classification — rather
than as a fixed hardcoded vocabulary.

Why evolutionary: the Business Analytics testbed uses business sub-domains
(sales, inventory, transactions, operations, finance), but MINT should work
across any business vertical it's used in (medical research, legal, education,
etc.). A fixed taxonomy would silently fail on queries from unknown verticals.
An evolutionary taxonomy starts empty and grows a category when no existing
one fits — preventing vocabulary drift (the LLM must check existing categories
first) while remaining open to new verticals.

The file-backed taxonomy is stored as JSON alongside the ChromaDB registry,
defaulting to ``data/eval/domains.json`` for evaluation runs.

Both start empty and accumulate categories as MINT encounters new kinds of
tasks. The file is the single source of truth for the vocabulary at any point
in time and can be inspected, edited manually, or exported for the thesis.

Generic-capability flag (design refinement from Phase E, 2026-04-14):
Step 3.5b measured the strict-equality filter against the Business Analytics
testbed and found it correctly caught 7/11 cross-sub-domain silent-poisoning
cases, but produced 3 false recall losses on ``parse_csv_and_return_stats_7f373a``
— a genuinely generic CSV parser labeled ``domain=data-processing``. The filter
rejected it on queries inferred as ``inventory`` / ``operations`` because of
strict domain equality. The fix: the LLM classifier emits an ``is_generic``
flag alongside the domain label; generic capabilities bypass the domain filter
entirely, because the filter's job is to catch business-context confusion and
generic caps have no business context to confuse.

Thesis framing: the taxonomy itself is a second evolutionary mechanism
alongside Pillar 3. It evolves on a different axis (vocabulary categories)
driven by different signals (LLM classification of new capabilities) but the
organic-growth principle is the same. See master plan Step 3.5f + the
methodology nuggets in the session handoff.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from src.schemas.task import GeneratedArtifact

logger = logging.getLogger(__name__)

# ext8 #69 (2026-04-20): retry budget for the domain-classifier LLM call,
# matching the strict-gate pattern used by decomposition (§12 #15),
# classification (§12 #16), and verification (§12 #14).
_MAX_TAXONOMY_ATTEMPTS = 3


def _normalize_category_name(name: str | None) -> str | None:
    """Return a stripped category name, or None for blank/invalid names."""
    if name is None:
        return None
    normalized = name.strip()
    return normalized or None


# ---------------------------------------------------------------------------
# Structured-output schemas for the LLM calls
# ---------------------------------------------------------------------------


class CapabilityDomainClassification(BaseModel):
    """Structured output when classifying a newly-generated capability.

    Two LLM outputs in one call:

    1. ``domain`` — which category this capability belongs to. The LLM is
       given the current taxonomy and MUST prefer an existing category over
       creating a new one unless nothing fits.
    2. ``is_generic`` — whether this capability has any hardcoded business
       vocabulary in its implementation. Generic caps bypass the domain
       filter at query time (see the module docstring for the rationale).
    """

    domain: str = Field(
        description=(
            "The category name for this capability. MUST be one of the "
            "existing categories in the taxonomy if any of them fit the "
            "capability's actual business context, OR a new category name "
            "(hyphen-case, 1-3 words) if no existing category fits. Prefer "
            "an existing category unless the capability is clearly from a "
            "business vertical or sub-domain not yet represented."
        ),
    )
    is_new: bool = Field(
        description=(
            "True iff 'domain' is a brand-new category being proposed. False "
            "iff 'domain' matches one of the existing categories in the "
            "taxonomy. Do NOT set True unless absolutely no existing category "
            "is a reasonable fit."
        ),
    )
    new_description: str = Field(
        default="",
        description=(
            "If is_new=True, a one-sentence description of what this new "
            "category represents (what kinds of capabilities belong to it). "
            "Leave as empty string if is_new=False."
        ),
    )
    is_generic: bool = Field(
        description=(
            "True iff this capability is GENERIC — its implementation has no "
            "hardcoded business vocabulary, column names, or domain rules. "
            "Generic caps serve any business sub-domain and bypass the "
            "domain filter at retrieval time. Examples of generic caps: "
            "parse-csv (any CSV, no column assumptions), fibonacci, "
            "word-count, format-date-string. Examples of NON-generic caps: "
            "parse-sales-csv (hardcoded columns order_id/product_id/..), "
            "aggregate-reorder-summary (hardcoded upstream key "
            "'inventory_with_reorder'), parse-inventory-csv (hardcoded "
            "columns product_id/current_stock/..). **Read the "
            "IMPLEMENTATION carefully** — a generic-sounding description "
            "can hide hardcoded business assumptions."
        ),
    )
    rationale: str = Field(
        description=(
            "One sentence citing a specific fact from the capability's name, "
            "description, or implementation that justifies both the chosen "
            "domain and the is_generic flag."
        ),
    )


class QueryDomainChoice(BaseModel):
    """Structured output when classifying an incoming query.

    The LLM reads the current taxonomy + the query and returns either the
    best-matching existing category or None (to bypass filtering when no
    category fits well). Unlike ``CapabilityDomainClassification``, this
    does NOT create new categories — categories only grow on the capability
    side, where the LLM has an implementation to read.
    """

    domain: str | None = Field(
        default=None,
        description=(
            "The existing category name that best matches the query's "
            "business context, OR None if no existing category is a good "
            "fit (in which case the domain filter is bypassed and the "
            "unfiltered semantic search runs). MUST be one of the existing "
            "categories in the taxonomy — do NOT invent new categories "
            "here; new categories are created only when registering new "
            "capabilities."
        ),
    )
    rationale: str = Field(
        description="One-sentence justification citing a specific term in the query."
    )


# ---------------------------------------------------------------------------
# LLM prompts
# ---------------------------------------------------------------------------


_CLASSIFY_CAPABILITY_PROMPT = """You are classifying a newly-generated capability into a domain taxonomy that grows organically as MINT encounters new kinds of tasks.

CURRENT TAXONOMY:
{taxonomy_list}

CAPABILITY TO CLASSIFY:

NAME: {name}
TYPE: {capability_type}

DESCRIPTION:
{description}

IMPLEMENTATION (may be truncated):
{implementation}

Your job has two parts:

PART 1 — Pick or create a domain:

Read the IMPLEMENTATION carefully (not just the description — generic descriptions can hide hardcoded business assumptions).

If this capability fits any of the existing categories in the taxonomy above, pick that category name and set is_new=False. Prefer an existing category whenever reasonable — the taxonomy stays coherent only when we avoid creating near-duplicate categories (e.g., don't create "sales-data" if "sales" already exists).

Important guardrail: shared FILE FORMATS or generic OPERATIONS are NOT enough to reuse a category. Do NOT reuse an existing category just because both capabilities mention CSV/JSON/XLSX/files or verbs like parse/write/join/aggregate. Reuse an existing category only when the underlying BUSINESS OBJECTS overlap materially (e.g., orders with orders, eligibility with eligibility, monitoring metrics with monitoring metrics). If the only overlap is file format or generic data-processing mechanics, create a new category.

If NO existing category fits — this capability is from a business vertical or sub-domain not yet represented in the taxonomy — propose a new category name (hyphen-case, 1-3 words) and set is_new=True with a one-sentence new_description. Only do this when no existing category is a reasonable fit.

Special case: if the taxonomy is empty (no categories yet), always set is_new=True and propose the first category.

PART 2 — Set the is_generic flag:

Is this capability genuinely GENERIC (zero hardcoded business vocabulary in the implementation) or is it BOUND to a specific business context?

GENERIC examples — is_generic=True:
- parse_csv_and_return_stats: reads any CSV, returns row_count and column_names. No hardcoded column names. Uses stdlib csv.reader. → is_generic=True
- fibonacci: pure math, no business context → is_generic=True
- format_date_string: any date, any format → is_generic=True
- word_count_and_readability: any text → is_generic=True

NON-GENERIC examples — is_generic=False:
- parse_sales_csv_and_products_xlsx: hardcoded columns {{order_id, product_id, quantity, unit_price, region}} → is_generic=False
- aggregate_reorder_summary: hardcodes upstream_data["inventory_with_reorder"] and fields like "needs_reorder" → is_generic=False
- parse_inventory_csv_and_reorder_rules: hardcodes exact CSV columns and YAML keys → is_generic=False
- compute_region_revenue_totals: accesses specific keys 'quantity', 'unit_price', 'region' → is_generic=False

The test is simple: **if the implementation contains ANY hardcoded column name, field name, business term, or schema assumption, is_generic=False**. Otherwise True.

Return a CapabilityDomainClassification with domain, is_new, new_description (only if is_new), is_generic, and rationale.
"""


_CLASSIFY_QUERY_PROMPT = """You are picking a domain category for an incoming query, to filter the capability registry.

CURRENT TAXONOMY (only these categories exist — you cannot invent new ones here):
{taxonomy_list}

QUERY:
{query}

Which existing category best matches the query's business context?

If the query clearly belongs to ONE of the listed categories (e.g., "parse sales CSV" → "sales"), return that category name.

If the query is ambiguous or doesn't clearly fit any existing category, return None. When domain=None, the gap-detection filter is bypassed and the unfiltered semantic search runs. This is the safe default for ambiguous queries.

Do NOT invent new categories here. New categories are only created when a new capability is registered (via CapabilityDomainClassification, not this prompt).

Return a QueryDomainChoice with domain (or None) and rationale.
"""


# ---------------------------------------------------------------------------
# The taxonomy manager
# ---------------------------------------------------------------------------


class DomainTaxonomy:
    """File-backed evolutionary domain taxonomy.

    Loads/saves a JSON file at the configured path. Starts empty. Grows one
    category at a time as capabilities are registered. Writes are atomic
    (write-to-temp + rename) and file-locked for multi-process safety.

    Thread-safe within a single process via an internal lock. Multi-process
    safety via fcntl advisory locking on the file itself.

    Usage::

        tx = DomainTaxonomy(Path("data/eval/domains.json"))
        # LLM-driven classification (needs a LangChain chat model configured
        # with structured output)
        result = tx.get_or_create_for_capability(artifact, llm)
        # result.domain is the chosen category, result.is_generic is the flag
        # The taxonomy file is updated atomically if is_new=True.

        chosen = tx.get_for_query("parse sales CSV and products XLSX", llm)
        # chosen is a str (existing category) or None (filter bypassed)
    """

    VERSION = 1

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()  # intra-process

    # ------------------------------------------------------------------
    # File I/O helpers (atomic + multi-process safe)
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        """Load the taxonomy from disk, returning an empty skeleton if absent."""
        if not self.path.exists():
            return {"version": self.VERSION, "categories": {}}
        try:
            with self.path.open("r", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    data = json.load(f)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "DomainTaxonomy: failed to read %s (%s) — starting empty",
                self.path, exc,
            )
            return {"version": self.VERSION, "categories": {}}
        # Schema sanity check: missing keys → patch in place
        if not isinstance(data, dict):
            return {"version": self.VERSION, "categories": {}}
        data.setdefault("version", self.VERSION)
        data.setdefault("categories", {})
        return data

    def _save(self, data: dict[str, Any]) -> None:
        """Atomically write the taxonomy to disk (write-to-temp + rename + fsync)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file in the same directory, then atomic rename.
        # Same-filesystem guarantees the rename is atomic on POSIX.
        fd, tmp_path_str = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    json.dump(data, f, indent=2, sort_keys=True)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            # Atomic rename on POSIX
            os.replace(tmp_path, self.path)
        except Exception:
            # Best-effort cleanup of the temp file if something went wrong
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Public read helpers
    # ------------------------------------------------------------------

    def list_categories(self) -> dict[str, str]:
        """Return {name: description} for every category currently in the taxonomy."""
        data = self._load()
        return {
            name: entry.get("description", "") if isinstance(entry, dict) else ""
            for name, entry in data.get("categories", {}).items()
        }

    def _format_taxonomy_for_prompt(self) -> str:
        """Render the current taxonomy as a bulleted list for LLM prompts."""
        cats = self.list_categories()
        if not cats:
            return "(empty — no categories exist yet; you must create the first one)"
        lines = []
        for name in sorted(cats.keys()):
            desc = cats[name]
            if desc:
                lines.append(f"- {name}: {desc}")
            else:
                lines.append(f"- {name}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Capability classification (may grow the taxonomy)
    # ------------------------------------------------------------------

    def get_or_create_for_capability(
        self,
        artifact: GeneratedArtifact,
        structured_llm: Any,
    ) -> CapabilityDomainClassification:
        """Classify a capability into the taxonomy, adding a new category if needed.

        Args:
            artifact: The ``GeneratedArtifact`` being classified. The method
                reads ``capability_name``, ``capability_type``, ``description``,
                and whichever of ``source_code``, ``skill_content``, or
                ``agent_config`` is populated.
            structured_llm: A LangChain chat model already wrapped with
                ``.with_structured_output(CapabilityDomainClassification,
                method="function_calling")``. Caller is responsible for
                constructing this to share across multiple calls (expensive
                to recreate).

        Returns:
            The ``CapabilityDomainClassification`` result. If ``is_new=True``,
            the taxonomy file has been updated atomically before this function
            returns.

        Never raises on LLM errors: on any exception from the structured-output
        call, logs a warning and returns a safe fallback classification with
        domain="general" and is_generic=False. Callers can check the
        ``rationale`` field for "(LLM failure: ...)" to detect this path.
        """
        implementation = self._extract_implementation(artifact)
        taxonomy_list = self._format_taxonomy_for_prompt()
        prompt = _CLASSIFY_CAPABILITY_PROMPT.format(
            taxonomy_list=taxonomy_list,
            name=artifact.capability_name,
            capability_type=artifact.capability_type.value,
            description=artifact.description or artifact.capability_name,
            implementation=implementation or "(no implementation available)",
        )

        try:
            result: CapabilityDomainClassification = structured_llm.invoke(prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "DomainTaxonomy.get_or_create_for_capability LLM failure for '%s': %s "
                "— using fallback classification",
                artifact.capability_name, exc,
            )
            return CapabilityDomainClassification(
                domain="general",
                is_new=False,
                new_description="",
                is_generic=False,
                rationale=f"(LLM failure: {type(exc).__name__}: {str(exc)[:120]})",
            )

        normalized_domain = _normalize_category_name(result.domain)
        if normalized_domain is None:
            logger.warning(
                "DomainTaxonomy.get_or_create_for_capability received blank domain "
                "for '%s' — using fallback classification",
                artifact.capability_name,
            )
            return CapabilityDomainClassification(
                domain="general",
                is_new=False,
                new_description="",
                is_generic=result.is_generic,
                rationale=(
                    f"{result.rationale} (invalid blank domain from taxonomy classifier)"
                ),
            )
        result = result.model_copy(
            update={
                "domain": normalized_domain,
                "new_description": result.new_description.strip(),
            }
        )

        # If the LLM proposed a new category, add it to the taxonomy atomically.
        if result.is_new:
            self._commit_new_category(result.domain, result.new_description)
        else:
            # LLM claimed is_new=False but picked a name not in the taxonomy —
            # treat as an implicit new-category proposal.
            cats = self.list_categories()
            if result.domain not in cats:
                logger.info(
                    "DomainTaxonomy: LLM picked '%s' with is_new=False but it's "
                    "not in the taxonomy — adding as a new category",
                    result.domain,
                )
                self._commit_new_category(
                    result.domain,
                    result.new_description or f"(inferred from {artifact.capability_name})",
                )

        return result

    def _commit_new_category(self, name: str, description: str) -> None:
        """Atomically add a new category to the taxonomy file.

        Re-loads the file under the lock (to pick up concurrent updates from
        other processes) and writes the merged result.
        """
        normalized_name = _normalize_category_name(name)
        if normalized_name is None:
            raise ValueError("Category name must be non-empty")

        with self._lock:
            data = self._load()
            categories = data.setdefault("categories", {})
            if normalized_name in categories:
                # Someone else added this category between our LLM call and
                # this commit — no-op. The description of the first writer
                # wins.
                logger.debug(
                    "DomainTaxonomy: category '%s' already exists — no-op", normalized_name
                )
                return
            categories[normalized_name] = {
                "description": description or "",
                "created_at": datetime.now(UTC).isoformat(),
            }
            self._save(data)
            logger.info(
                "DomainTaxonomy: added new category '%s' — '%s'",
                normalized_name, description[:80],
            )

    # ------------------------------------------------------------------
    # Query classification (does NOT grow the taxonomy)
    # ------------------------------------------------------------------

    def get_for_query(
        self,
        query: str,
        structured_llm: Any,
    ) -> str | None:
        """Pick the best-matching existing category for a query, or None.

        Args:
            query: The task description or required-capability string being
                matched against the registry.
            structured_llm: A LangChain chat model wrapped with
                ``.with_structured_output(QueryDomainChoice,
                method="function_calling")``.

        Returns:
            The chosen category name if one fits, or ``None`` if no category
            matches well. A ``None`` return tells the caller to bypass the
            domain filter and run unfiltered semantic search.

        Never raises on LLM errors: returns ``None`` on any exception, which
        is the safe default (unfiltered search = pre-B1 behavior).
        """
        cats = self.list_categories()
        if not cats:
            # Empty taxonomy → nothing to filter against
            return None

        taxonomy_list = self._format_taxonomy_for_prompt()
        prompt = _CLASSIFY_QUERY_PROMPT.format(
            taxonomy_list=taxonomy_list,
            query=query,
        )
        # ext8 #69 (2026-04-20): retry per the strict-gate pattern used by
        # decomposition (§12 #15), classification (§12 #16), and verification
        # (§12 #14). Pre-fix a single LLM failure (rate limit, 500, network
        # flap) demoted retrieval to unfiltered search, introducing random
        # noise into Pillar 1 retrieval metrics under high-concurrency eval
        # load. 3x retry matches the pattern's retry budget.
        result: QueryDomainChoice | None = None
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_TAXONOMY_ATTEMPTS + 1):
            try:
                result = structured_llm.invoke(prompt)
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < _MAX_TAXONOMY_ATTEMPTS:
                    logger.warning(
                        "DomainTaxonomy.get_for_query attempt %d/%d failed "
                        "(query: %r): %s — retrying",
                        attempt, _MAX_TAXONOMY_ATTEMPTS, query[:80], exc,
                    )

        if result is None:
            # Retry budget exhausted — loud-fail in logs but preserve the
            # never-raises contract (caller falls back to unfiltered search).
            logger.error(
                "DomainTaxonomy.get_for_query exhausted %d attempts "
                "(query: %r): %s — bypassing filter",
                _MAX_TAXONOMY_ATTEMPTS, query[:80], last_exc,
            )
            return None

        # Validate: if the LLM returned a domain not in the taxonomy, treat
        # as None (the LLM hallucinated a category that doesn't exist).
        normalized_domain = _normalize_category_name(result.domain)
        if normalized_domain is None:
            logger.warning(
                "DomainTaxonomy.get_for_query: LLM returned blank domain for "
                "query %r — bypassing filter",
                query[:80],
            )
            return None
        if normalized_domain not in cats:
            logger.warning(
                "DomainTaxonomy.get_for_query: LLM returned '%s' which is not "
                "in the current taxonomy — bypassing filter",
                normalized_domain,
            )
            return None

        return normalized_domain

    # ------------------------------------------------------------------
    # Implementation text extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_implementation(
        artifact: GeneratedArtifact, max_chars: int = 4000
    ) -> str:
        """Return the most informative executable content from the artifact, truncated."""
        # Prefer source_code > skill_content > agent_config (whichever is populated)
        candidates: list[str] = []
        if artifact.source_code:
            candidates.append(artifact.source_code)
        if artifact.skill_content:
            candidates.append(artifact.skill_content)
        if artifact.agent_config:
            try:
                candidates.append(
                    json.dumps(artifact.agent_config, indent=2, default=str)
                )
            except (TypeError, ValueError):
                candidates.append(str(artifact.agent_config))
        if not candidates:
            return ""
        text = candidates[0]
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... (truncated from {len(text)} chars)"
        return text


# ---------------------------------------------------------------------------
# Module-level path configuration
# ---------------------------------------------------------------------------

_TAXONOMY_PATH: Path | None = None


def set_taxonomy_path(path: Path | str) -> None:
    """Override the default taxonomy file path.

    Evaluation runs use ``data/eval/domains.json`` by default. This override
    remains available for tests or custom runners that need an isolated
    taxonomy file.
    """
    global _TAXONOMY_PATH  # noqa: PLW0603
    _TAXONOMY_PATH = Path(path)


def get_taxonomy_path() -> Path:
    """Return the currently-configured taxonomy file path.

    Defaults to ``<project-root>/data/eval/domains.json`` if the path
    has not been overridden via :func:`set_taxonomy_path`. The default keeps
    evaluation runs hermetic without requiring harness-specific wiring.
    """
    if _TAXONOMY_PATH is not None:
        return _TAXONOMY_PATH
    project_root = Path(__file__).parent.parent.parent
    return project_root / "data" / "eval" / "domains.json"


# ---------------------------------------------------------------------------
# High-level helper for generator integration
# ---------------------------------------------------------------------------


def classify_and_tag_artifacts(artifacts: list[GeneratedArtifact]) -> None:
    """Classify a batch of freshly-generated artifacts via the taxonomy.

    Called by each generator node (mcp_tool, skill, agent) at the end of
    its thread-pool merge, BEFORE returning the Command. Iterates over
    the artifacts whose ``domain`` is None (i.e. not yet classified),
    creates one shared ``DomainTaxonomy`` instance + one shared LLM
    with structured output, and classifies each artifact.

    Mutates ``artifact.domain`` and ``artifact.is_generic`` in place on
    each successfully-classified artifact. Never raises — on any
    exception (LLM failure, file I/O error, Pydantic validation error),
    logs a warning and leaves the artifact with ``domain=None`` and
    ``is_generic=False``. Downstream ``tool_loader`` / ``loading_node``
    will fall back to ``domain='general'`` / ``'generated'`` when
    forwarding None onto the descriptor.

    This helper creates its own LLM instance (no caller setup needed).
    The path comes from :func:`get_taxonomy_path`.
    """
    # Filter to artifacts that need classification — skip anything already
    # tagged (e.g. on retry when verification_node replaces some but not all).
    #
    # Order matters. If one artifact proposes a new category, later siblings
    # should see that expanded taxonomy and reuse it when appropriate. Prefer
    # domain-defining transforms (aggregate/join/analyze/apply) before parsers,
    # and push format-only writers to the end so they do not seed the taxonomy
    # with shallow file-format buckets.
    pending = sorted(
        (a for a in artifacts if a.domain is None),
        key=_taxonomy_priority,
        reverse=True,
    )
    if not pending:
        return

    try:
        taxonomy = DomainTaxonomy(get_taxonomy_path())
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "classify_and_tag_artifacts: could not construct DomainTaxonomy: %s "
            "— skipping taxonomy classification for %d artifact(s)",
            exc, len(pending),
        )
        return

    try:
        from langchain.chat_models import init_chat_model

        from src.config import DEFAULT_LLM_MODEL

        # reasoning_effort="minimal" drops gpt-5-mini latency on trivial
        # classification from ~193s to ~10s per call (observed 2026-04-14
        # on trace b4c2488d/obs 8dfb032e — 320 reasoning tokens on a single
        # boolean + category pick). Taxonomy classification is pure pattern
        # matching, not a creative task — reasoning mode is pure overhead.
        llm = init_chat_model(
            DEFAULT_LLM_MODEL, temperature=0, reasoning_effort="minimal"
        )
        structured_llm = llm.with_structured_output(
            CapabilityDomainClassification, method="function_calling"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "classify_and_tag_artifacts: LLM setup failed: %s "
            "— skipping taxonomy classification for %d artifact(s)",
            exc, len(pending),
        )
        return

    for artifact in pending:
        try:
            result = taxonomy.get_or_create_for_capability(artifact, structured_llm)
            artifact.domain = result.domain
            # ext8 #76 (2026-04-20): defense-in-depth. If the LLM
            # said is_generic=True but the artifact's source contains
            # hardcoded domain-specific string literals, force
            # is_generic=False. A mis-classified hardcoded tool
            # flagged is_generic=True pollutes every domain's search
            # results via the `$or` clause in registry.search —
            # single LLM decision shouldn't have cascading effects.
            is_generic = bool(result.is_generic)
            if is_generic and _contains_domain_specific_literals(artifact):
                logger.warning(
                    "ext8 #76: classifier said is_generic=True for '%s' but "
                    "code contains domain-specific literals — forcing "
                    "is_generic=False (defense-in-depth).",
                    artifact.capability_name,
                )
                is_generic = False
            artifact.is_generic = is_generic
            logger.info(
                "classify_and_tag_artifacts: %s -> domain=%s is_generic=%s",
                artifact.capability_name, result.domain, is_generic,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "classify_and_tag_artifacts: classification failed: %s "
                "— artifact left with domain=None",
                exc,
            )


# ext8 #76 (2026-04-20): literals that mark a capability as domain-specific.
# If these appear as string literals in source_code / skill_content /
# agent_config, is_generic=True is suspicious and gets overridden.
# Scoped to the Business Analytics Pipeline testbed's vocabulary (the
# classifier's known-failure mode per line 207-210 examples above).
_DOMAIN_SPECIFIC_LITERALS = (
    "order_id", "product_id", "unit_price", "customer_id",
    "company_name", "reorder", "inventory",
    "needs_reorder", "days_of_stock", "region_revenue",
)


def _contains_domain_specific_literals(artifact: GeneratedArtifact) -> bool:
    """Return True if the artifact's executable content contains string
    literals matching a known domain-specific term.

    Checks quoted occurrences only (``"order_id"`` / ``'order_id'``) so
    matches in prose / comments don't trigger false positives.
    """
    texts: list[str] = []
    if artifact.source_code:
        texts.append(artifact.source_code)
    if artifact.skill_content:
        texts.append(artifact.skill_content)
    if artifact.agent_config:
        texts.append(
            artifact.agent_config if isinstance(artifact.agent_config, str)
            else str(artifact.agent_config)
        )
    for text in texts:
        for term in _DOMAIN_SPECIFIC_LITERALS:
            if f'"{term}"' in text or f"'{term}'" in text:
                return True
    return False


_GENERIC_TAXONOMY_TOKENS = {
    "and", "apply", "build", "compare", "compute", "config", "csv", "data",
    "daily", "export", "file", "hourly", "json", "load", "metrics", "monitoring",
    "parse", "parser", "parsing", "per", "policy", "report", "save", "summarize",
    "summary", "targets", "tool", "write", "writer", "xlsx",
}

_DOMAIN_SEED_VERBS = (
    "aggregate", "analyze", "analyse", "apply", "classify", "compare",
    "compute", "enrich", "evaluate", "investigate", "join", "merge",
)

_FORMAT_ONLY_VERBS = ("export", "format", "save", "serialize", "write")


def _taxonomy_priority(artifact: GeneratedArtifact) -> tuple[int, int, int]:
    """Return a deterministic priority for within-batch taxonomy classification.

    Higher values classify earlier. The goal is not semantic perfection; it is
    to let domain-defining transforms seed the taxonomy before file-format-only
    helpers are classified.
    """
    import re

    name = artifact.capability_name.lower()
    description = (artifact.description or "").lower()
    text = f"{name} {description}"
    tokens = set(re.findall(r"[a-z0-9]+", text))

    verb_score = 0
    if any(name.startswith(f"{verb}-") or name.startswith(f"{verb}_") for verb in _DOMAIN_SEED_VERBS):
        verb_score += 3
    if any(name.startswith(f"{verb}-") or name.startswith(f"{verb}_") for verb in _FORMAT_ONLY_VERBS):
        verb_score -= 3

    domain_token_score = sum(
        1 for token in tokens
        if len(token) >= 4 and token not in _GENERIC_TAXONOMY_TOKENS
    )
    implementation_len = len(artifact.source_code or artifact.skill_content or "")
    return (verb_score, domain_token_score, implementation_len)
