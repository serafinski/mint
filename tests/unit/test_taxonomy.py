"""Tests for src/orchestrator/taxonomy.py — the evolutionary domain taxonomy.

Mocks the LLM call (both classification entry points) and tests the
taxonomy's file-backed mechanics, atomic writes, growth behavior, and
safe-default failure modes.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.orchestrator.taxonomy import (
    CapabilityDomainClassification,
    DomainTaxonomy,
    QueryDomainChoice,
    _contains_domain_specific_literals,
    _normalize_category_name,
    classify_and_tag_artifacts,
)
from src.schemas.capability import CapabilityType
from src.schemas.task import GeneratedArtifact

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_taxonomy(tmp_path: Path) -> DomainTaxonomy:
    return DomainTaxonomy(tmp_path / "domains.json")


def _make_artifact(
    name: str = "parse-test-csv",
    capability_type: CapabilityType = CapabilityType.MCP_TOOL,
    source_code: str | None = "def foo(): pass",
    skill_content: str | None = None,
    agent_config: dict | None = None,
    description: str | None = "Parse a test CSV file",
) -> GeneratedArtifact:
    return GeneratedArtifact(
        capability_name=name,
        capability_type=capability_type,
        description=description,
        source_code=source_code,
        skill_content=skill_content,
        agent_config=agent_config,
    )


def _mock_llm_returning(classification: CapabilityDomainClassification) -> MagicMock:
    llm = MagicMock()
    llm.invoke.return_value = classification
    return llm


def _mock_query_llm_returning(choice: QueryDomainChoice) -> MagicMock:
    llm = MagicMock()
    llm.invoke.return_value = choice
    return llm


# ---------------------------------------------------------------------------
# Empty-state behavior
# ---------------------------------------------------------------------------


class TestEmptyState:
    def test_empty_file_lists_no_categories(self, tmp_taxonomy: DomainTaxonomy):
        assert tmp_taxonomy.list_categories() == {}

    def test_nonexistent_file_treated_as_empty(self, tmp_path: Path):
        taxonomy = DomainTaxonomy(tmp_path / "missing.json")
        assert taxonomy.list_categories() == {}

    def test_format_taxonomy_for_prompt_empty(self, tmp_taxonomy: DomainTaxonomy):
        out = tmp_taxonomy._format_taxonomy_for_prompt()
        assert "empty" in out.lower()


# ---------------------------------------------------------------------------
# Growth (LLM-driven category creation)
# ---------------------------------------------------------------------------


class TestCategoryGrowth:
    def test_first_capability_creates_first_category(
        self, tmp_taxonomy: DomainTaxonomy
    ):
        artifact = _make_artifact()
        llm = _mock_llm_returning(
            CapabilityDomainClassification(
                domain="data-processing",
                is_new=True,
                new_description="Generic CSV/JSON parsing",
                is_generic=True,
                rationale="Parses any CSV without hardcoded column names.",
            )
        )
        result = tmp_taxonomy.get_or_create_for_capability(artifact, llm)
        assert result.domain == "data-processing"
        assert result.is_generic is True
        assert "data-processing" in tmp_taxonomy.list_categories()
        assert tmp_taxonomy.list_categories()["data-processing"] == "Generic CSV/JSON parsing"

    def test_is_new_false_on_existing_category_does_not_duplicate(
        self, tmp_taxonomy: DomainTaxonomy
    ):
        # Pre-seed with one category
        llm1 = _mock_llm_returning(
            CapabilityDomainClassification(
                domain="sales",
                is_new=True,
                new_description="Sales and revenue",
                is_generic=False,
                rationale="Hardcoded sales columns.",
            )
        )
        tmp_taxonomy.get_or_create_for_capability(_make_artifact("a"), llm1)

        # Second cap picks the existing category
        llm2 = _mock_llm_returning(
            CapabilityDomainClassification(
                domain="sales",
                is_new=False,
                new_description="",
                is_generic=False,
                rationale="Same sales domain as before.",
            )
        )
        tmp_taxonomy.get_or_create_for_capability(_make_artifact("b"), llm2)

        cats = tmp_taxonomy.list_categories()
        assert len(cats) == 1
        assert "sales" in cats

    def test_is_new_false_but_name_not_in_taxonomy_still_adds(
        self, tmp_taxonomy: DomainTaxonomy
    ):
        """LLM claims is_new=False but picks a name not yet in the taxonomy —
        treat as an implicit new-category proposal rather than silently dropping it."""
        artifact = _make_artifact()
        llm = _mock_llm_returning(
            CapabilityDomainClassification(
                domain="unknown-domain",
                is_new=False,  # LLM was wrong here
                new_description="",
                is_generic=False,
                rationale="LLM confused itself.",
            )
        )
        tmp_taxonomy.get_or_create_for_capability(artifact, llm)
        assert "unknown-domain" in tmp_taxonomy.list_categories()

    def test_blank_domain_falls_back_without_creating_empty_category(
        self, tmp_taxonomy: DomainTaxonomy,
    ):
        artifact = _make_artifact()
        llm = _mock_llm_returning(
            CapabilityDomainClassification(
                domain="   ",
                is_new=False,
                new_description="",
                is_generic=False,
                rationale="LLM returned whitespace only.",
            )
        )

        result = tmp_taxonomy.get_or_create_for_capability(artifact, llm)

        assert result.domain == "general"
        assert result.is_new is False
        assert tmp_taxonomy.list_categories() == {}

    def test_growth_is_persistent_across_instances(self, tmp_path: Path):
        path = tmp_path / "domains.json"
        tx1 = DomainTaxonomy(path)
        llm = _mock_llm_returning(
            CapabilityDomainClassification(
                domain="sales",
                is_new=True,
                new_description="Sales",
                is_generic=False,
                rationale="Hardcoded sales columns.",
            )
        )
        tx1.get_or_create_for_capability(_make_artifact(), llm)

        # Second instance loads the same file
        tx2 = DomainTaxonomy(path)
        assert "sales" in tx2.list_categories()


# ---------------------------------------------------------------------------
# Query classification (does NOT grow taxonomy)
# ---------------------------------------------------------------------------


class TestQueryClassification:
    def test_empty_taxonomy_returns_none(self, tmp_taxonomy: DomainTaxonomy):
        llm = _mock_query_llm_returning(
            QueryDomainChoice(domain="sales", rationale="")
        )
        # LLM isn't even consulted when taxonomy is empty
        assert tmp_taxonomy.get_for_query("parse sales csv", llm) is None
        llm.invoke.assert_not_called()

    def test_returns_existing_category(self, tmp_taxonomy: DomainTaxonomy):
        # Seed the taxonomy with a category
        seed_llm = _mock_llm_returning(
            CapabilityDomainClassification(
                domain="sales",
                is_new=True,
                new_description="Sales",
                is_generic=False,
                rationale="",
            )
        )
        tmp_taxonomy.get_or_create_for_capability(_make_artifact(), seed_llm)

        query_llm = _mock_query_llm_returning(
            QueryDomainChoice(domain="sales", rationale="Query mentions 'sales'.")
        )
        assert tmp_taxonomy.get_for_query("parse sales csv", query_llm) == "sales"

    def test_none_when_llm_returns_none(self, tmp_taxonomy: DomainTaxonomy):
        seed_llm = _mock_llm_returning(
            CapabilityDomainClassification(
                domain="sales",
                is_new=True,
                new_description="Sales",
                is_generic=False,
                rationale="",
            )
        )
        tmp_taxonomy.get_or_create_for_capability(_make_artifact(), seed_llm)

        query_llm = _mock_query_llm_returning(
            QueryDomainChoice(domain=None, rationale="Ambiguous query.")
        )
        assert tmp_taxonomy.get_for_query("compute something generic", query_llm) is None

    def test_hallucinated_category_treated_as_none(self, tmp_taxonomy: DomainTaxonomy):
        """If the LLM returns a category name not in the taxonomy, bypass filter."""
        seed_llm = _mock_llm_returning(
            CapabilityDomainClassification(
                domain="sales",
                is_new=True,
                new_description="Sales",
                is_generic=False,
                rationale="",
            )
        )
        tmp_taxonomy.get_or_create_for_capability(_make_artifact(), seed_llm)

        query_llm = _mock_query_llm_returning(
            QueryDomainChoice(domain="finance", rationale="Finance terms.")
        )
        # "finance" isn't in the taxonomy — should be treated as None
        assert tmp_taxonomy.get_for_query("query", query_llm) is None

    def test_blank_query_domain_treated_as_none(self, tmp_taxonomy: DomainTaxonomy):
        seed_llm = _mock_llm_returning(
            CapabilityDomainClassification(
                domain="sales",
                is_new=True,
                new_description="Sales",
                is_generic=False,
                rationale="",
            )
        )
        tmp_taxonomy.get_or_create_for_capability(_make_artifact(), seed_llm)

        query_llm = _mock_query_llm_returning(
            QueryDomainChoice(domain="   ", rationale="Bad whitespace response.")
        )
        assert tmp_taxonomy.get_for_query("parse sales csv", query_llm) is None


class TestCategoryNameNormalization:
    def test_blank_names_normalize_to_none(self):
        assert _normalize_category_name(None) is None
        assert _normalize_category_name("") is None
        assert _normalize_category_name("   ") is None

    def test_names_are_stripped(self):
        assert _normalize_category_name("  sales  ") == "sales"


# ---------------------------------------------------------------------------
# LLM failure → safe defaults
# ---------------------------------------------------------------------------


class TestLLMFailureHandling:
    def test_capability_llm_failure_returns_fallback(self, tmp_taxonomy: DomainTaxonomy):
        broken_llm = MagicMock()
        broken_llm.invoke.side_effect = RuntimeError("API down")
        result = tmp_taxonomy.get_or_create_for_capability(_make_artifact(), broken_llm)
        assert result.domain == "general"
        assert result.is_generic is False
        assert "LLM failure" in result.rationale

    def test_query_llm_failure_returns_none(self, tmp_taxonomy: DomainTaxonomy):
        # Pre-seed so the empty-taxonomy short-circuit doesn't hide the failure
        seed_llm = _mock_llm_returning(
            CapabilityDomainClassification(
                domain="sales", is_new=True, new_description="", is_generic=False, rationale=""
            )
        )
        tmp_taxonomy.get_or_create_for_capability(_make_artifact(), seed_llm)

        broken_llm = MagicMock()
        broken_llm.invoke.side_effect = RuntimeError("API down")
        assert tmp_taxonomy.get_for_query("some query", broken_llm) is None


class TestTaxonomyRetry:
    """get_for_query retries transient LLM failures before bypassing filtering."""

    def test_retries_on_transient_failure_up_to_three_attempts(
        self, tmp_taxonomy: DomainTaxonomy,
    ):
        # Pre-seed so the empty-taxonomy short-circuit doesn't fire.
        seed_llm = _mock_llm_returning(
            CapabilityDomainClassification(
                domain="sales", is_new=True, new_description="",
                is_generic=False, rationale="",
            )
        )
        tmp_taxonomy.get_or_create_for_capability(_make_artifact(), seed_llm)

        # Two transient failures, then success on the 3rd attempt.
        flaky_llm = MagicMock()
        flaky_llm.invoke.side_effect = [
            RuntimeError("rate limit"),
            RuntimeError("500 upstream"),
            QueryDomainChoice(domain="sales", rationale="3rd try worked"),
        ]
        result = tmp_taxonomy.get_for_query("parse sales csv", flaky_llm)
        assert result == "sales", (
            "taxonomy classifier must retry through transient failures "
            "before returning None"
        )
        assert flaky_llm.invoke.call_count == 3

    def test_exhausted_retries_return_none_loud_fail(
        self, tmp_taxonomy: DomainTaxonomy, caplog,
    ):
        seed_llm = _mock_llm_returning(
            CapabilityDomainClassification(
                domain="sales", is_new=True, new_description="",
                is_generic=False, rationale="",
            )
        )
        tmp_taxonomy.get_or_create_for_capability(_make_artifact(), seed_llm)

        always_broken = MagicMock()
        always_broken.invoke.side_effect = RuntimeError("persistent API down")

        import logging
        with caplog.at_level(logging.ERROR, logger="src.orchestrator.taxonomy"):
            result = tmp_taxonomy.get_for_query("parse sales csv", always_broken)

        assert result is None
        assert always_broken.invoke.call_count == 3
        # Must emit an ERROR log after exhausting retries (loud-fail contract).
        assert any(
            "exhausted" in rec.getMessage() and rec.levelname == "ERROR"
            for rec in caplog.records
        ), "retry exhaustion must emit an ERROR log, not just WARN."

    def test_first_attempt_success_does_not_retry(
        self, tmp_taxonomy: DomainTaxonomy,
    ):
        """Happy path: first-attempt success returns immediately without
        retry overhead.
        """
        seed_llm = _mock_llm_returning(
            CapabilityDomainClassification(
                domain="sales", is_new=True, new_description="",
                is_generic=False, rationale="",
            )
        )
        tmp_taxonomy.get_or_create_for_capability(_make_artifact(), seed_llm)

        happy_llm = _mock_query_llm_returning(
            QueryDomainChoice(domain="sales", rationale="match")
        )
        result = tmp_taxonomy.get_for_query("parse sales csv", happy_llm)
        assert result == "sales"
        assert happy_llm.invoke.call_count == 1


# ---------------------------------------------------------------------------
# File I/O robustness
# ---------------------------------------------------------------------------


class TestFileIORobustness:
    def test_corrupt_json_treated_as_empty(self, tmp_path: Path):
        path = tmp_path / "domains.json"
        path.write_text("this is not valid json {{{")
        taxonomy = DomainTaxonomy(path)
        # Corrupt file → empty skeleton returned
        assert taxonomy.list_categories() == {}

    def test_missing_categories_key_patched(self, tmp_path: Path):
        path = tmp_path / "domains.json"
        path.write_text(json.dumps({"version": 1}))  # no "categories" key
        taxonomy = DomainTaxonomy(path)
        assert taxonomy.list_categories() == {}

    def test_atomic_write_produces_valid_json(self, tmp_taxonomy: DomainTaxonomy):
        llm = _mock_llm_returning(
            CapabilityDomainClassification(
                domain="data-processing",
                is_new=True,
                new_description="Generic parsing",
                is_generic=True,
                rationale="Generic.",
            )
        )
        tmp_taxonomy.get_or_create_for_capability(_make_artifact(), llm)

        # File should be valid JSON with the expected shape
        data = json.loads(tmp_taxonomy.path.read_text())
        assert data["version"] == 1
        assert "data-processing" in data["categories"]
        assert data["categories"]["data-processing"]["description"] == "Generic parsing"
        assert "created_at" in data["categories"]["data-processing"]

    def test_sorted_output(self, tmp_taxonomy: DomainTaxonomy):
        """File should have sorted keys for stable diffs."""
        for name in ("sales", "inventory", "finance"):
            llm = _mock_llm_returning(
                CapabilityDomainClassification(
                    domain=name,
                    is_new=True,
                    new_description=f"{name} domain",
                    is_generic=False,
                    rationale="",
                )
            )
            tmp_taxonomy.get_or_create_for_capability(_make_artifact(name), llm)

        raw = tmp_taxonomy.path.read_text()
        # "finance" < "inventory" < "sales" lexicographically
        assert raw.index('"finance"') < raw.index('"inventory"') < raw.index('"sales"')


# ---------------------------------------------------------------------------
# Implementation extraction
# ---------------------------------------------------------------------------


class TestImplementationExtraction:
    def test_prefers_source_code(self):
        artifact = _make_artifact(
            source_code="SOURCE", skill_content="SKILL", agent_config={"k": "AGENT"}
        )
        assert DomainTaxonomy._extract_implementation(artifact).startswith("SOURCE")

    def test_falls_back_to_skill_content(self):
        artifact = _make_artifact(source_code=None, skill_content="SKILL_BODY")
        assert DomainTaxonomy._extract_implementation(artifact) == "SKILL_BODY"

    def test_falls_back_to_agent_config(self):
        artifact = _make_artifact(
            source_code=None, skill_content=None, agent_config={"system_prompt": "AGENT"}
        )
        result = DomainTaxonomy._extract_implementation(artifact)
        assert "AGENT" in result

    def test_truncates_long_content(self):
        long_source = "x" * 10_000
        artifact = _make_artifact(source_code=long_source)
        result = DomainTaxonomy._extract_implementation(artifact, max_chars=100)
        assert len(result) < 300  # truncated + suffix
        assert "truncated" in result

    def test_empty_artifact_returns_empty(self):
        artifact = _make_artifact(source_code=None, skill_content=None, agent_config=None)
        assert DomainTaxonomy._extract_implementation(artifact) == ""


class _FakeStructuredTaxonomyLLM:
    def __init__(self):
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> CapabilityDomainClassification:
        self.prompts.append(prompt)
        if "NAME: aggregate-eligibility-counts" in prompt:
            return CapabilityDomainClassification(
                domain="eligibility-aggregation",
                is_new=True,
                new_description="Eligibility decisions and rollups.",
                is_generic=False,
                rationale="Aggregates eligibility outcomes.",
            )
        if "NAME: parse-eligibility-policy-and-applications-csv" in prompt:
            if "- eligibility-aggregation:" in prompt:
                return CapabilityDomainClassification(
                    domain="eligibility-aggregation",
                    is_new=False,
                    new_description="",
                    is_generic=False,
                    rationale="Same eligibility workflow as the seeded aggregate step.",
                )
            return CapabilityDomainClassification(
                domain="orders-csv-processing",
                is_new=False,
                new_description="",
                is_generic=False,
                rationale="Fallback to the only existing category.",
            )
        raise AssertionError(f"Unexpected prompt:\n{prompt}")


class _FakeTaxonomyModel:
    def __init__(self, structured_llm: _FakeStructuredTaxonomyLLM):
        self._structured_llm = structured_llm

    def with_structured_output(self, *_args, **_kwargs):
        return self._structured_llm


class TestClassifyAndTagArtifacts:
    def test_domain_defining_artifact_seeds_taxonomy_before_parser(
        self, tmp_path: Path, monkeypatch
    ):
        import src.orchestrator.taxonomy as taxonomy_mod

        taxonomy_path = tmp_path / "domains.json"
        taxonomy_path.write_text(json.dumps({
            "version": 1,
            "categories": {
                "orders-csv-processing": {
                    "description": "Tools that process orders-related CSV files.",
                    "created_at": "2026-04-22T00:00:00+00:00",
                }
            },
        }))
        monkeypatch.setattr(taxonomy_mod, "_TAXONOMY_PATH", taxonomy_path)

        structured_llm = _FakeStructuredTaxonomyLLM()
        fake_model = _FakeTaxonomyModel(structured_llm)
        monkeypatch.setattr("langchain.chat_models.init_chat_model", lambda *a, **k: fake_model)

        parse_artifact = _make_artifact(
            name="parse-eligibility-policy-and-applications-csv",
            description=(
                "Parse eligibility policy JSON and applications CSV into "
                "structured records."
            ),
            source_code="async def parse_eligibility_policy_and_applications_csv():\n    return {}",
        )
        aggregate_artifact = _make_artifact(
            name="aggregate-eligibility-counts",
            description="Aggregate eligibility decisions into counts and summaries.",
            source_code="async def aggregate_eligibility_counts():\n    return {}",
        )

        classify_and_tag_artifacts([parse_artifact, aggregate_artifact])

        assert aggregate_artifact.domain == "eligibility-aggregation"
        assert parse_artifact.domain == "eligibility-aggregation"
        categories = DomainTaxonomy(taxonomy_path).list_categories()
        assert "eligibility-aggregation" in categories
        assert structured_llm.prompts[0].count("eligibility-aggregation") == 0
        assert "- eligibility-aggregation:" in structured_llm.prompts[1]


class TestDomainSpecificLiteralDetection:
    def test_source_with_order_id_literal_detected(self):
        artifact = _make_artifact(
            name="parse_sales",
            source_code='cols = ["order_id", "product_id"]',
            description="",
        )

        assert _contains_domain_specific_literals(artifact) is True

    def test_source_without_domain_literals_not_detected(self):
        artifact = _make_artifact(
            name="parse_csv",
            source_code='cols = ["col1", "col2", "col3"]',
            description="",
        )

        assert _contains_domain_specific_literals(artifact) is False

    def test_skill_content_checked(self):
        artifact = _make_artifact(
            name="inventory-skill",
            capability_type=CapabilityType.SKILL,
            source_code=None,
            skill_content='Check "needs_reorder" field on each item.',
            description="",
        )

        assert _contains_domain_specific_literals(artifact) is True

    def test_prose_mention_without_quotes_not_detected(self):
        """Only quoted string literals count; prose mentions do not trigger."""
        artifact = _make_artifact(
            name="general-analyzer",
            capability_type=CapabilityType.SKILL,
            source_code=None,
            skill_content="This analyzer handles generic inventory use cases.",
            description="",
        )

        assert _contains_domain_specific_literals(artifact) is False
