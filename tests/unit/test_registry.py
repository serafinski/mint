"""Unit tests for the ChromaDB-backed capability registry."""

import uuid

import pytest

from src.registry.store import CapabilityRegistry, SkillRegistry, seed_registry
from src.schemas.capability import CapabilityDescriptor, CapabilityType


@pytest.fixture
def registry():
    """Isolated ephemeral registry per test."""
    return CapabilityRegistry(collection_name=f"test-{uuid.uuid4().hex[:8]}")


@pytest.fixture
def seeded_registry(registry):
    """Registry pre-loaded with seed capabilities."""
    seed_registry(registry)
    return registry


def _make_cap(
    name: str = "test-cap",
    description: str = "A test capability",
    capability_type: CapabilityType = CapabilityType.MCP_TOOL,
    **kwargs,
) -> CapabilityDescriptor:
    """Helper to create a CapabilityDescriptor with optional extra fields."""
    fields = {
        "name": name,
        "description": description,
        "capability_type": capability_type,
        **kwargs,
    }
    # Only include fields the schema supports
    model_fields = CapabilityDescriptor.model_fields
    safe_fields = {k: v for k, v in fields.items() if k in model_fields}
    return CapabilityDescriptor(**safe_fields)


# --- Core registry behavior ---


def test_skill_registry_alias_remains_for_backwards_compatibility():
    assert SkillRegistry is CapabilityRegistry


def test_add_and_search(registry):
    cap = CapabilityDescriptor(
        name="fibonacci",
        description="Compute the nth Fibonacci number",
        capability_type=CapabilityType.MCP_TOOL,
    )
    registry.add(cap)
    results = registry.search("fibonacci number")
    assert len(results) >= 1
    assert results[0][0].name == "fibonacci"
    assert results[0][1] > 0  # positive similarity


def test_search_empty_registry(registry):
    results = registry.search("anything")
    assert results == []


def test_seed_registry(registry):
    seed_registry(registry)
    assert registry.count == 14


def test_search_relevance(seeded_registry):
    results = seeded_registry.search("parse CSV data", n_results=3)
    assert results[0][0].name == "csv-parser"


def test_upsert_overwrites(registry):
    cap_v1 = CapabilityDescriptor(
        name="fibonacci",
        description="Compute fibonacci",
        capability_type=CapabilityType.MCP_TOOL,
        version=1,
    )
    cap_v2 = CapabilityDescriptor(
        name="fibonacci",
        description="Compute fibonacci numbers efficiently",
        capability_type=CapabilityType.MCP_TOOL,
        version=2,
    )
    registry.add(cap_v1)
    registry.add(cap_v2)
    assert registry.count == 1
    results = registry.search("fibonacci")
    assert results[0][0].version == 2


def test_detect_gaps_finds_match(seeded_registry):
    matches, gaps = seeded_registry.detect_gaps(["compute fibonacci number"])
    assert len(matches) == 1
    assert len(gaps) == 0
    assert matches[0][0].name == "fibonacci"


def test_detect_gaps_finds_gap(seeded_registry):
    matches, gaps = seeded_registry.detect_gaps(
        ["translate English to French"], threshold=0.8
    )
    assert len(gaps) == 1
    assert len(matches) == 0
    assert "below threshold" in gaps[0][1]


def test_detect_gaps_mixed(seeded_registry):
    queries = ["compute fibonacci number", "deploy kubernetes cluster"]
    matches, gaps = seeded_registry.detect_gaps(
        queries,
        threshold=0.5,
    )
    handled_queries = {match[2] for match in matches} | {gap[0] for gap in gaps}
    assert handled_queries == set(queries)
    assert any(match[0].name == "fibonacci" for match in matches)


def test_persistent_registry(tmp_path):
    persist_dir = str(tmp_path / "registry")
    col_name = f"test-{uuid.uuid4().hex[:8]}"
    registry = CapabilityRegistry(persist_directory=persist_dir, collection_name=col_name)
    seed_registry(registry)
    assert registry.count == 14

    # Reopen from disk
    registry2 = CapabilityRegistry(persist_directory=persist_dir, collection_name=col_name)
    assert registry2.count == 14
    results = registry2.search("fibonacci")
    assert results[0][0].name == "fibonacci"


# --- get() tests ---


def test_get_by_name(registry):
    cap = CapabilityDescriptor(
        name="fibonacci",
        description="Compute the nth Fibonacci number",
        capability_type=CapabilityType.MCP_TOOL,
    )
    registry.add(cap)
    result = registry.get("fibonacci")
    assert result is not None
    assert result.name == "fibonacci"
    assert result.description == "Compute the nth Fibonacci number"
    assert result.capability_type == CapabilityType.MCP_TOOL


def test_get_nonexistent(registry):
    result = registry.get("nonexistent")
    assert result is None


# --- update() tests ---


def test_update_capability(registry):
    cap = CapabilityDescriptor(
        name="fibonacci",
        description="Compute fibonacci",
        capability_type=CapabilityType.MCP_TOOL,
        version=1,
    )
    registry.add(cap)
    updated = registry.update("fibonacci", description="Compute fibonacci numbers fast", version=2)
    assert updated.description == "Compute fibonacci numbers fast"
    assert updated.version == 2
    assert registry.count == 1

    # Verify the update persisted
    fetched = registry.get("fibonacci")
    assert fetched is not None
    assert fetched.description == "Compute fibonacci numbers fast"
    assert fetched.version == 2


def test_update_nonexistent(registry):
    with pytest.raises(KeyError, match="not found"):
        registry.update("nonexistent", description="new")


# --- deactivate() tests ---


def test_deactivate_capability(registry):
    cap = CapabilityDescriptor(
        name="fibonacci",
        description="Compute fibonacci",
        capability_type=CapabilityType.MCP_TOOL,
    )
    registry.add(cap)
    registry.deactivate("fibonacci")

    # Verify metadata shows inactive
    result = registry._collection.get(ids=["fibonacci"])
    assert result["metadatas"][0]["is_active"] is False


def test_deactivate_nonexistent(registry):
    with pytest.raises(KeyError, match="not found"):
        registry.deactivate("nonexistent")


def test_deactivate_hides_from_list(registry):
    cap1 = CapabilityDescriptor(
        name="fibonacci",
        description="Compute fibonacci",
        capability_type=CapabilityType.MCP_TOOL,
    )
    cap2 = CapabilityDescriptor(
        name="csv-parser",
        description="Parse CSV files",
        capability_type=CapabilityType.MCP_TOOL,
    )
    registry.add(cap1)
    registry.add(cap2)
    registry.deactivate("fibonacci")

    active = registry.list_all(is_active=True)
    names = [c.name for c in active]
    assert "fibonacci" not in names
    assert "csv-parser" in names


# --- delete() tests ---


def test_delete_capability(registry):
    cap = CapabilityDescriptor(
        name="fibonacci",
        description="Compute fibonacci",
        capability_type=CapabilityType.MCP_TOOL,
    )
    registry.add(cap)
    assert registry.count == 1
    registry.delete("fibonacci")
    assert registry.count == 0


def test_delete_nonexistent(registry):
    with pytest.raises(KeyError, match="not found"):
        registry.delete("nonexistent")


# --- list_all() tests ---


def test_list_all(seeded_registry):
    all_caps = seeded_registry.list_all(is_active=None)
    assert len(all_caps) == 14


def test_list_by_type(seeded_registry):
    tools = seeded_registry.list_all(capability_type=CapabilityType.MCP_TOOL)
    assert len(tools) == 5
    assert all(c.capability_type == CapabilityType.MCP_TOOL for c in tools)

    skills = seeded_registry.list_all(capability_type=CapabilityType.SKILL)
    assert len(skills) == 7
    assert all(c.capability_type == CapabilityType.SKILL for c in skills)

    agents = seeded_registry.list_all(capability_type=CapabilityType.AGENT)
    assert len(agents) == 2
    assert all(agent.capability_type == CapabilityType.AGENT for agent in agents)


def test_list_by_domain(seeded_registry):
    data_caps = seeded_registry.list_all(domain="data-processing", is_active=None)
    names = {c.name for c in data_caps}
    # csv-parser, date-formatter, json-validator, data-cleaning-workflow are in data-processing
    assert "csv-parser" in names
    assert "json-validator" in names
    assert "data-cleaning-workflow" in names


def test_list_active_only(registry):
    cap1 = CapabilityDescriptor(
        name="active-tool",
        description="An active tool",
        capability_type=CapabilityType.MCP_TOOL,
    )
    cap2 = CapabilityDescriptor(
        name="inactive-tool",
        description="Will be deactivated",
        capability_type=CapabilityType.MCP_TOOL,
    )
    registry.add(cap1)
    registry.add(cap2)
    registry.deactivate("inactive-tool")

    active = registry.list_all(is_active=True)
    assert len(active) == 1
    assert active[0].name == "active-tool"

    all_caps = registry.list_all(is_active=None)
    assert len(all_caps) == 2


# --- Search enhancements ---


def test_min_confidence_filter(seeded_registry):
    # With no minimum, we get results
    results_all = seeded_registry.search("fibonacci number", n_results=8)
    assert len(results_all) >= 1

    # With very high minimum, we may get fewer results
    results_high = seeded_registry.search(
        "fibonacci number", n_results=8, min_confidence=0.99
    )
    assert len(results_high) <= len(results_all)


def test_hierarchical_search_prefers_domain_match(registry):
    """Verify that hierarchical embedding documents improve domain-specific search."""
    # Add two tools with similar descriptions but different domains
    cap_math = _make_cap(
        name="math-analyzer",
        description="Analyze and compute numerical patterns",
        capability_type=CapabilityType.MCP_TOOL,
    )
    cap_text = _make_cap(
        name="text-analyzer",
        description="Analyze and compute text patterns and word frequencies",
        capability_type=CapabilityType.MCP_TOOL,
    )
    registry.add(cap_math)
    registry.add(cap_text)

    # Query specifically about text analysis
    results = registry.search("analyze text word frequency")
    assert len(results) >= 1
    assert results[0][0].name == "text-analyzer"


def test_search_relevance_with_hierarchical_docs(seeded_registry):
    """Verify hierarchical document construction produces sensible search results."""
    results = seeded_registry.search("validate JSON data against schema", n_results=3)
    assert results[0][0].name == "json-validator"

    results = seeded_registry.search("clean messy dataset", n_results=3)
    assert results[0][0].name == "data-cleaning-workflow"

    results = seeded_registry.search("generate a report with charts", n_results=3)
    assert results[0][0].name == "report-generation"


# --- Expanded seed data ---


def test_expanded_seed(registry):
    seed_registry(registry)
    assert registry.count == 14

    all_caps = registry.list_all(is_active=None)
    names = {c.name for c in all_caps}
    expected = {
        "anomaly-investigation-agent",
        "budget-allocation-policy",
        "fibonacci",
        "csv-parser",
        "date-formatter",
        "json-validator",
        "text-statistics",
        "data-cleaning-workflow",
        "eligibility-policy-classifier",
        "inventory-reorder-policy",
        "monitoring-aggregation-policy",
        "report-generation",
        "sales-integration-report",
        "data-analysis-agent",
    }
    assert names == expected

    types = {c.capability_type for c in all_caps}
    assert CapabilityType.MCP_TOOL in types
    assert CapabilityType.SKILL in types
    assert CapabilityType.AGENT in types


# --- Persistence with CRUD ---


def test_persistence_survives_crud(tmp_path):
    """Full CRUD operations survive a registry restart."""
    persist_dir = str(tmp_path / "registry")
    col_name = f"test-{uuid.uuid4().hex[:8]}"

    # Session 1: add, update, deactivate
    reg1 = CapabilityRegistry(persist_directory=persist_dir, collection_name=col_name)
    reg1.add(
        CapabilityDescriptor(
            name="tool-a",
            description="Tool A original",
            capability_type=CapabilityType.MCP_TOOL,
        )
    )
    reg1.add(
        CapabilityDescriptor(
            name="tool-b",
            description="Tool B",
            capability_type=CapabilityType.MCP_TOOL,
        )
    )
    reg1.update("tool-a", description="Tool A updated", version=2)
    reg1.deactivate("tool-b")

    # Session 2: reopen and verify
    reg2 = CapabilityRegistry(persist_directory=persist_dir, collection_name=col_name)
    assert reg2.count == 2

    tool_a = reg2.get("tool-a")
    assert tool_a is not None
    assert tool_a.description == "Tool A updated"
    assert tool_a.version == 2

    # tool-b should still exist but be inactive in metadata
    result = reg2._collection.get(ids=["tool-b"])
    assert result["metadatas"][0]["is_active"] is False


# --- Edge cases ---


def test_get_preserves_version(registry):
    cap = CapabilityDescriptor(
        name="versioned",
        description="A versioned tool",
        capability_type=CapabilityType.MCP_TOOL,
        version=5,
    )
    registry.add(cap)
    result = registry.get("versioned")
    assert result is not None
    assert result.version == 5


def test_add_and_get_roundtrip(registry):
    """Add a capability and get it back -- all core fields must match."""
    cap = CapabilityDescriptor(
        name="roundtrip-test",
        description="Test roundtrip fidelity",
        capability_type=CapabilityType.SKILL,
        version=3,
    )
    registry.add(cap)
    result = registry.get("roundtrip-test")
    assert result is not None
    assert result.name == cap.name
    assert result.description == cap.description
    assert result.capability_type == cap.capability_type
    assert result.version == cap.version


# --- created_at round-trip ---


def test_created_at_preserved_on_get(registry):
    """created_at must survive an add → get round-trip without drift."""
    cap = CapabilityDescriptor(
        name="ts-tool",
        description="A timestamped tool",
        capability_type=CapabilityType.MCP_TOOL,
    )
    original_ts = cap.created_at
    registry.add(cap)
    fetched = registry.get("ts-tool")
    assert fetched is not None
    assert fetched.created_at == original_ts


def test_created_at_preserved_on_search(registry):
    """created_at must survive an add → search round-trip."""
    cap = CapabilityDescriptor(
        name="search-ts-tool",
        description="A tool for search timestamp test",
        capability_type=CapabilityType.MCP_TOOL,
    )
    original_ts = cap.created_at
    registry.add(cap)
    results = registry.search("search timestamp tool", n_results=1)
    assert results and results[0][0].created_at == original_ts


# --- detect_gaps respects is_active ---


def test_detect_gaps_treats_deactivated_capability_as_gap(registry):
    """A deactivated capability must NOT count as a match — it must be a gap."""
    cap = CapabilityDescriptor(
        name="inactive-tool",
        description="Compute the nth Fibonacci number",
        capability_type=CapabilityType.MCP_TOOL,
    )
    registry.add(cap)
    registry.deactivate("inactive-tool")

    matches, gaps = registry.detect_gaps(["compute fibonacci number"])

    assert len(matches) == 0
    assert len(gaps) == 1
    # Deactivated caps are filtered from active search results, so the exact
    # reason text can vary by embedding score but must remain informative.
    assert gaps[0][1]  # reason is non-empty


def test_detect_gaps_active_capability_still_matches(registry):
    """An active capability above threshold must still be a match."""
    cap = CapabilityDescriptor(
        name="active-tool",
        description="Compute the nth Fibonacci number",
        capability_type=CapabilityType.MCP_TOOL,
    )
    registry.add(cap)

    matches, gaps = registry.detect_gaps(["compute fibonacci number"])

    assert len(matches) == 1
    assert len(gaps) == 0
    assert matches[0][0].name == "active-tool"


# --- Description summarization for embedding efficiency ---


class TestDescriptionSummarization:
    def test_short_description_unchanged(self):
        desc = "Parse a CSV file and return rows"
        assert CapabilityRegistry._summarize_description(desc) == desc

    def test_long_description_truncated_to_50_words(self):
        words = [f"word{i}" for i in range(100)]
        desc = " ".join(words)
        summary = CapabilityRegistry._summarize_description(desc)
        assert summary.endswith("…")
        # 50 words + ellipsis
        assert len(summary.split()) == 50  # trailing … is part of last token

    def test_exactly_50_words_unchanged(self):
        words = [f"word{i}" for i in range(50)]
        desc = " ".join(words)
        summary = CapabilityRegistry._summarize_description(desc)
        assert summary == desc
        assert "…" not in summary

    def test_build_document_uses_summary(self, registry):
        long_desc = " ".join(f"word{i}" for i in range(100))
        cap = _make_cap(name="long-desc-cap", description=long_desc)
        doc = CapabilityRegistry._build_document(cap)
        # Document should contain the truncated summary, not the full description
        assert "…" in doc
        assert "word99" not in doc

    def test_full_description_preserved_in_metadata(self, registry):
        long_desc = " ".join(f"word{i}" for i in range(100))
        cap = _make_cap(name="long-desc-cap", description=long_desc)
        registry.add(cap)
        fetched = registry.get("long-desc-cap")
        assert fetched is not None
        assert fetched.description == long_desc


# --- Dual-query search ---
#
# The decomposer produces short hyphen-case slugs in `required_capabilities`
# which systematically fall below the 0.6 cosine threshold against the
# registry's prose-style `_build_document()` output. Dual-query search
# runs a second search on the natural-language `SubTask.description` and
# takes the max-scoring hit.


class TestDetectGapsDualQuery:
    """Tests for the dual-query auxiliary_queries parameter on detect_gaps."""

    def test_backward_compat_no_auxiliary(self, seeded_registry):
        """When auxiliary_queries is None, only the primary query is used."""
        matches, gaps = seeded_registry.detect_gaps(
            ["compute fibonacci number"],
            auxiliary_queries=None,
        )
        assert len(matches) == 1
        assert len(gaps) == 0
        assert matches[0][0].name == "fibonacci"

    def test_slug_below_threshold_description_above_is_match(self, seeded_registry):
        """The T01 csv-parser case: short slug misses, description hits.

        Empirically measured: slug ``parse-csv-and-return-stats`` scores
        0.5425 against csv-parser's seed doc (below 0.6 threshold), while
        the corresponding prose description scores 0.7653 (above threshold).
        Dual-query takes the max, which lifts the match above threshold.
        """
        matches, gaps = seeded_registry.detect_gaps(
            ["parse-csv-and-return-stats"],
            auxiliary_queries=[
                "Parse the provided CSV file and return a dict with 'row_count' "
                "(int) and 'column_names' (list of str)."
            ],
        )
        assert len(matches) == 1, f"expected 1 match, got gaps={gaps}"
        assert matches[0][0].name == "csv-parser"
        # Score should reflect the auxiliary (description) winning, i.e.
        # comfortably above the slug-only score of ~0.54.
        assert matches[0][1] > 0.6

    def test_attribution_preserves_slug_in_match_tuple(self, seeded_registry):
        """The match tuple's third element is the original slug, not the aux.

        Metrics and LangFuse traces attribute matches to the slug the
        decomposer chose. Dual-query should not change that — only
        change the SCORE comparison.
        """
        matches, _ = seeded_registry.detect_gaps(
            ["parse-csv-and-return-stats"],
            auxiliary_queries=[
                "Parse the provided CSV file and return row count and column names."
            ],
        )
        assert len(matches) == 1
        _, _, original_query = matches[0]
        assert original_query == "parse-csv-and-return-stats"

    def test_both_below_threshold_is_gap(self, seeded_registry):
        """When neither slug nor description matches, it's still a gap."""
        matches, gaps = seeded_registry.detect_gaps(
            ["deploy kubernetes cluster"],
            auxiliary_queries=[
                "Deploy a Kubernetes cluster with 3 worker nodes and configure ingress."
            ],
            threshold=0.8,  # force below-threshold outcome
        )
        assert len(matches) == 0
        assert len(gaps) == 1
        # Gap reason should show BOTH scores when auxiliary was provided
        reason = gaps[0][1]
        assert "primary" in reason.lower() or "auxiliary" in reason.lower()

    def test_none_auxiliary_in_list_treated_as_single_query(self, seeded_registry):
        """Per-query None auxiliary works — some slugs may lack descriptions."""
        matches, gaps = seeded_registry.detect_gaps(
            ["compute fibonacci number"],
            auxiliary_queries=[None],
        )
        assert len(matches) == 1
        assert matches[0][0].name == "fibonacci"

    def test_empty_string_auxiliary_treated_as_none(self, seeded_registry):
        """Empty-string auxiliary is equivalent to None (defensive)."""
        matches, gaps = seeded_registry.detect_gaps(
            ["compute fibonacci number"],
            auxiliary_queries=[""],
        )
        assert len(matches) == 1

    def test_mismatched_lengths_raises(self, seeded_registry):
        """auxiliary_queries length must match queries length."""
        with pytest.raises(ValueError, match="length"):
            seeded_registry.detect_gaps(
                ["slug-a", "slug-b"],
                auxiliary_queries=["only one auxiliary"],
            )

    def test_mixed_matches_and_gaps_with_auxiliary(self, seeded_registry):
        """Dual-query works across a list with some matches and some gaps."""
        matches, gaps = seeded_registry.detect_gaps(
            ["parse-csv-and-return-stats", "deploy kubernetes cluster"],
            auxiliary_queries=[
                "Parse the provided CSV file and return row_count and column_names.",
                "Deploy a Kubernetes cluster with worker nodes and ingress config.",
            ],
            threshold=0.6,
        )
        # csv-parser should match via its auxiliary description
        assert any(m[0].name == "csv-parser" for m in matches)
        # kubernetes should remain a gap
        assert any("kubernetes" in g[0] for g in gaps)

    def test_max_score_wins_on_both_above(self, seeded_registry):
        """When both queries match, take the higher-scoring hit."""
        # Both queries should match fibonacci, the natural-language one
        # typically scores higher than the short slug.
        matches, _ = seeded_registry.detect_gaps(
            ["compute-fibonacci"],
            auxiliary_queries=[
                "Compute the nth Fibonacci number given an integer index n."
            ],
        )
        assert len(matches) == 1
        assert matches[0][0].name == "fibonacci"
        # Score should be at least as good as the single-query baseline
        single_matches, _ = seeded_registry.detect_gaps(["compute-fibonacci"])
        if single_matches:
            assert matches[0][1] >= single_matches[0][1]
