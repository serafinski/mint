"""Layer 1: smoke-test the hand-written static implementations in harness.py.

These run against real fixture files (no LLM, no Docker, no mocks).
Each test calls _run_static_implementation directly and checks the output
passes the corresponding task validator.

If any of these fail, the static baseline is broken before we ever run the pilot.
"""

from __future__ import annotations

from tests.evaluation.harness import _run_static_implementation
from tests.evaluation.tasks import FIXTURES_DIR, T01, T02, T03, T05, T09


class TestStaticT01:
    def test_output_passes_validator(self):
        output = _run_static_implementation("T01", [FIXTURES_DIR / "t01_standard.csv"])
        assert "row_count" in output
        assert "column_names" in output
        assert T01.validator(output), f"T01 validator failed on: {output}"

    def test_row_count_correct(self):
        output = _run_static_implementation("T01", [FIXTURES_DIR / "t01_standard.csv"])
        assert output["row_count"] == 50

    def test_column_names_correct(self):
        output = _run_static_implementation("T01", [FIXTURES_DIR / "t01_standard.csv"])
        assert set(output["column_names"]) == {"id", "name", "age", "city"}


class TestStaticT02:
    def test_output_passes_validator(self):
        output = _run_static_implementation(
            "T02",
            [FIXTURES_DIR / "t02_config.json", FIXTURES_DIR / "t02_schema.json"],
        )
        assert "errors" in output
        assert T02.validator(output), f"T02 validator failed on: {output}"

    def test_returns_three_errors(self):
        output = _run_static_implementation(
            "T02",
            [FIXTURES_DIR / "t02_config.json", FIXTURES_DIR / "t02_schema.json"],
        )
        assert len(output["errors"]) == 3

    def test_port_error_present(self):
        output = _run_static_implementation(
            "T02",
            [FIXTURES_DIR / "t02_config.json", FIXTURES_DIR / "t02_schema.json"],
        )
        assert any("port" in e for e in output["errors"])

    def test_ttl_error_present(self):
        output = _run_static_implementation(
            "T02",
            [FIXTURES_DIR / "t02_config.json", FIXTURES_DIR / "t02_schema.json"],
        )
        assert any("ttl" in e for e in output["errors"])

    def test_level_error_present(self):
        output = _run_static_implementation(
            "T02",
            [FIXTURES_DIR / "t02_config.json", FIXTURES_DIR / "t02_schema.json"],
        )
        assert any("level" in e or "VERBOSE" in e for e in output["errors"])


class TestStaticT03:
    def test_output_passes_validator(self):
        output = _run_static_implementation("T03", [FIXTURES_DIR / "t03_document.txt"])
        assert "word_count" in output
        assert "readability_score" in output
        assert T03.validator(output), f"T03 validator failed on: {output}"

    def test_word_count_positive(self):
        output = _run_static_implementation("T03", [FIXTURES_DIR / "t03_document.txt"])
        assert output["word_count"] > 0

    def test_readability_in_range(self):
        output = _run_static_implementation("T03", [FIXTURES_DIR / "t03_document.txt"])
        assert 0 <= output["readability_score"] <= 100


class TestStaticT05:
    def test_output_passes_validator(self):
        output = _run_static_implementation("T05", [FIXTURES_DIR / "t05_dirty.csv"])
        assert "output_row_count" in output
        assert "null_count" in output
        assert T05.validator(output), f"T05 validator failed on: {output}"

    def test_null_count_zero(self):
        output = _run_static_implementation("T05", [FIXTURES_DIR / "t05_dirty.csv"])
        assert output["null_count"] == 0

    def test_row_count_reduced(self):
        from tests.evaluation.tasks import _EV
        input_count = _EV["T05"]["input_row_count"]
        output = _run_static_implementation("T05", [FIXTURES_DIR / "t05_dirty.csv"])
        assert output["output_row_count"] < input_count


class TestStaticT09:
    def test_output_passes_validator(self):
        output = _run_static_implementation("T09", [FIXTURES_DIR / "t09_report_data.xml"])
        assert T09.validator(output), f"T09 validator failed on: {output}"

    def test_has_required_keys(self):
        output = _run_static_implementation("T09", [FIXTURES_DIR / "t09_report_data.xml"])
        assert "summary" in output
        assert "stats_table" in output
        assert "anomalies" in output

    def test_exactly_five_anomalies(self):
        output = _run_static_implementation("T09", [FIXTURES_DIR / "t09_report_data.xml"])
        assert len(output["anomalies"]) == 5

    def test_summary_is_string(self):
        output = _run_static_implementation("T09", [FIXTURES_DIR / "t09_report_data.xml"])
        assert isinstance(output["summary"], str) and len(output["summary"]) > 0


class TestStaticUnknownTask:
    def test_raises_for_unknown_task(self):
        import pytest
        with pytest.raises(ValueError, match="No static implementation"):
            _run_static_implementation("T99", [])
