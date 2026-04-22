"""Tests for slugify_gap and slugify_gap_hyphen (Fix 5)."""

from src.orchestrator.generators.utils import slugify_gap, slugify_gap_hyphen


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
        """Regression: old code stripped trailing underscores BEFORE truncating,
        leaving a trailing underscore at position 40 that then got concatenated
        with ``_<hash>`` — producing names like
        ``parse_this_xml_report_data_and_find_the__6b415a`` (double underscore).

        The fix strips trailing underscores AFTER truncation.
        """
        # A 46-char input where position 39 is an underscore
        query = "parse-this-xml-report-data-and-find-the-95c8e9"
        slug = slugify_gap(query)
        # Verify no double underscore anywhere in the slug
        assert "__" not in slug, f"Double underscore in slug: {slug}"
        # Verify the base ends cleanly (no trailing underscore before suffix)
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
