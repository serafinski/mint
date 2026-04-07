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
