"""Tests for slugify_gap naming helpers."""

from src.orchestrator.generators.utils import (
    slugify_gap,
    slugify_gap_hyphen,
    stable_capability_name_for_gap,
)
from src.schemas.task import ClassifiedGap, SubTask


class TestSlugifyGap:
    def test_stable_across_calls(self):
        """Same query always produces the same slug."""
        q = "fuzzy string matching"
        assert slugify_gap(q) == slugify_gap(q)

    def test_collision_free(self):
        """Similar queries produce different slugs."""
        assert slugify_gap("parse CSV file") != slugify_gap("parse CSV files")

    def test_handles_special_chars(self):
        """Punctuation, mixed case, trailing spaces are normalised."""
        slug = slugify_gap("  Parse CSV File!!! (v2)  ")
        assert slug.isascii()
        assert "_" in slug  # base is snake_case
        # No special chars remain in the base
        base = slug.rsplit("_", 1)[0]
        assert all(c.isalnum() or c == "_" for c in base)

    def test_truncates_long_base(self):
        """Base is truncated to 40 chars before the hash suffix."""
        long_query = "a" * 100
        slug = slugify_gap(long_query)
        base = slug.rsplit("_", 1)[0]
        assert len(base) <= 40

    def test_hyphen_variant(self):
        """slugify_gap_hyphen returns hyphen-case."""
        slug = slugify_gap_hyphen("fuzzy string matching")
        assert "_" not in slug
        assert "-" in slug

    def test_no_double_underscore_when_truncating_mid_separator(self):
        """Truncation must not leave a separator before the hash suffix.

        The helper strips trailing underscores after truncation, avoiding names
        like ``parse_this_xml_report_data_and_find_the__6b415a``.
        """
        # A 46-char input where position 39 is an underscore
        query = "parse-this-xml-report-data-and-find-the-95c8e9"
        slug = slugify_gap(query)
        assert "__" not in slug, f"Double underscore in slug: {slug}"
        base = slug.rsplit("_", 1)[0]
        assert not base.endswith("_"), f"Base has trailing underscore: {base}"

    def test_no_double_hyphen_in_hyphen_variant(self):
        """Same regression, applied to the hyphen variant."""
        query = "parse-this-xml-report-data-and-find-the-95c8e9"
        slug = slugify_gap_hyphen(query)
        assert "--" not in slug, f"Double hyphen in slug: {slug}"
        base = slug.rsplit("-", 1)[0]
        assert not base.endswith("-"), f"Base has trailing hyphen: {base}"

    def test_empty_query_falls_back_to_cap(self):
        """Empty or punctuation-only queries fall back to 'cap' base."""
        slug = slugify_gap("")
        assert slug.startswith("cap_")
        slug2 = slugify_gap("!!!")
        assert slug2.startswith("cap_")

    def test_contract_fingerprint_separates_same_query_with_different_schema(self):
        gap = ClassifiedGap(
            description="Apply eligibility policy",
            query="apply-eligibility-policy-to-applications",
            classified_type="skill",
            confidence=0.9,
        )
        counts_subtask = SubTask(
            description="Return counts",
            required_capabilities=[gap.query],
            depends_on=[0],
            output_schema={
                "total_applications": "int",
                "eligible_count": "int",
                "ineligible_count": "int",
                "rules_applied": "int",
            },
            requires_upstream_keys=["applications", "policy_rules"],
        )
        decisions_subtask = SubTask(
            description="Return decisions",
            required_capabilities=[gap.query],
            depends_on=[0],
            output_schema={"decisions": "list[dict]"},
            requires_upstream_keys=["applications", "policy"],
        )

        counts_name = stable_capability_name_for_gap(
            gap,
            [counts_subtask],
            hyphen_case=True,
        )
        decisions_name = stable_capability_name_for_gap(
            gap,
            [decisions_subtask],
            hyphen_case=True,
        )

        assert counts_name != decisions_name

    def test_contract_fingerprint_preserves_same_query_same_schema(self):
        gap = ClassifiedGap(
            description="Apply thresholds",
            query="apply-alert-thresholds-and-summarize",
            classified_type="skill",
            confidence=0.9,
        )
        subtask = SubTask(
            description="Return alert summary",
            required_capabilities=[gap.query],
            depends_on=[2],
            output_schema={
                "n_days": "int",
                "metrics_aggregated": "int",
                "alert_days": "int",
            },
            requires_upstream_keys=["daily_aggregates", "policy"],
        )

        assert stable_capability_name_for_gap(gap, [subtask], hyphen_case=True) == (
            stable_capability_name_for_gap(gap, [subtask], hyphen_case=True)
        )
