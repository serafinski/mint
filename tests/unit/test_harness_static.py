"""Tests for the hand-written static evaluation baseline implementations.

These run against real fixture files without LLM calls, Docker, or mocks. Each
test calls ``_run_static_implementation`` directly and checks that the output
passes the corresponding task validator.
"""

from __future__ import annotations

import pytest

from tests.evaluation.harness import _run_static_implementation
from tests.evaluation.tasks import (
    _EV,
    FIXTURES_DIR,
    T01,
    T02,
    T04,
    T05,
    T09,
    T11,
    T13,
    T14,
    T15,
    T19,
    T20,
)


def _unwrap(result):
    if isinstance(result, tuple):
        return result
    return result, {}


class TestStaticT01:
    def test_output_passes_validator(self):
        output, _files = _unwrap(_run_static_implementation(
            "T01",
            [FIXTURES_DIR / "t01_standard.csv"],
        ))
        assert "row_count" in output
        assert "column_names" in output
        assert T01.validator(output), f"T01 validator failed on: {output}"

    def test_row_count_correct(self):
        output, _files = _unwrap(_run_static_implementation(
            "T01",
            [FIXTURES_DIR / "t01_standard.csv"],
        ))
        assert output["row_count"] == 50

    def test_column_names_correct(self):
        output, _files = _unwrap(_run_static_implementation(
            "T01",
            [FIXTURES_DIR / "t01_standard.csv"],
        ))
        assert set(output["column_names"]) == {"id", "name", "age", "city"}


class TestStaticT02:
    def test_output_passes_validator(self):
        output, _files = _unwrap(_run_static_implementation(
            "T02",
            [FIXTURES_DIR / "t02_config.json", FIXTURES_DIR / "t02_schema.json"],
        ))
        assert "errors" in output
        assert T02.validator(output), f"T02 validator failed on: {output}"

    def test_returns_three_errors(self):
        output, _files = _unwrap(_run_static_implementation(
            "T02",
            [FIXTURES_DIR / "t02_config.json", FIXTURES_DIR / "t02_schema.json"],
        ))
        assert len(output["errors"]) == 3

    def test_port_error_present(self):
        output, _files = _unwrap(_run_static_implementation(
            "T02",
            [FIXTURES_DIR / "t02_config.json", FIXTURES_DIR / "t02_schema.json"],
        ))
        assert any("port" in e for e in output["errors"])

    def test_ttl_error_present(self):
        output, _files = _unwrap(_run_static_implementation(
            "T02",
            [FIXTURES_DIR / "t02_config.json", FIXTURES_DIR / "t02_schema.json"],
        ))
        assert any("ttl" in e for e in output["errors"])

    def test_level_error_present(self):
        output, _files = _unwrap(_run_static_implementation(
            "T02",
            [FIXTURES_DIR / "t02_config.json", FIXTURES_DIR / "t02_schema.json"],
        ))

        assert any("level" in e or "VERBOSE" in e for e in output["errors"])


class TestStaticT04:
    def test_output_passes_validator(self):
        output, _files = _unwrap(_run_static_implementation(
            "T04",
            [FIXTURES_DIR / "t04_inventory.csv", FIXTURES_DIR / "t04_reorder_rules.yaml"],
        ))
        assert T04.validator(output), f"T04 validator failed on: {output}"

    def test_has_required_keys(self):
        output, _files = _unwrap(_run_static_implementation(
            "T04",
            [FIXTURES_DIR / "t04_inventory.csv", FIXTURES_DIR / "t04_reorder_rules.yaml"],
        ))
        assert {"product_count", "items_below_threshold", "total_reorder_cost"} <= set(output)


class TestStaticT11:
    def test_output_and_file_pass_validators(self):
        output, output_files = _unwrap(_run_static_implementation(
            "T11",
            [
                FIXTURES_DIR / "t11_sales.csv",
                FIXTURES_DIR / "t11_products.xlsx",
                FIXTURES_DIR / "t11_targets.csv",
            ],
        ))
        assert T11.validator(output), f"T11 validator failed on: {output}"
        assert T11.file_validator(output, output_files), "T11 file validator failed"
        assert output["output_file"] == "output.xlsx"
        assert "output.xlsx" in output_files


class TestStaticT13:
    def test_output_passes_validator(self):
        output, _files = _unwrap(_run_static_implementation(
            "T13",
            [FIXTURES_DIR / "t13_metrics.csv", FIXTURES_DIR / "t13_baselines.json"],
        ))
        assert T13.validator(output), f"T13 validator failed on: {output}"


class TestStaticT14:
    def test_output_passes_validator(self):
        output, _files = _unwrap(_run_static_implementation(
            "T14",
            [
                FIXTURES_DIR / "t14_departments.csv",
                FIXTURES_DIR / "t14_transactions.tsv",
                FIXTURES_DIR / "t14_rules.json",
            ],
        ))
        assert T14.validator(output), f"T14 validator failed on: {output}"


class TestStaticT15:
    def test_output_passes_validator(self):
        output, _files = _unwrap(_run_static_implementation(
            "T15",
            [FIXTURES_DIR / "t15_hourly_metrics.csv", FIXTURES_DIR / "t15_agg_config.json"],
        ))
        assert T15.validator(output), f"T15 validator failed on: {output}"


class TestStaticT19:
    """Tests for the T19 static baseline branch.

    T19 declares ``has_static_coverage=True``, so the static dispatch table
    must execute it and return the keys required by the task validator.
    """

    def test_output_passes_validator(self):
        output, _files = _unwrap(_run_static_implementation(
            "T19",
            [FIXTURES_DIR / "t19_product_sales.csv"],
        ))
        assert "n_rows" in output
        assert "n_categories" in output
        assert "categories" in output
        assert T19.validator(output), f"T19 validator failed on: {output}"

    def test_row_count_positive(self):
        output, _files = _unwrap(_run_static_implementation(
            "T19",
            [FIXTURES_DIR / "t19_product_sales.csv"],
        ))
        assert output["n_rows"] > 0

    def test_categories_match_n(self):
        output, _files = _unwrap(_run_static_implementation(
            "T19",
            [FIXTURES_DIR / "t19_product_sales.csv"],
        ))
        assert len(output["categories"]) == output["n_categories"]


class TestStaticT05:
    def test_output_passes_validator(self):
        output, _files = _unwrap(_run_static_implementation(
            "T05",
            [FIXTURES_DIR / "t05_dirty.csv"],
        ))
        assert "output_row_count" in output
        assert "null_count" in output
        assert T05.validator(output), f"T05 validator failed on: {output}"

    def test_null_count_zero(self):
        output, _files = _unwrap(_run_static_implementation(
            "T05",
            [FIXTURES_DIR / "t05_dirty.csv"],
        ))
        assert output["null_count"] == 0

    def test_row_count_reduced(self):
        input_count = _EV["T05"]["input_row_count"]
        output, _files = _unwrap(_run_static_implementation(
            "T05",
            [FIXTURES_DIR / "t05_dirty.csv"],
        ))
        assert output["output_row_count"] < input_count


class TestStaticT09:
    def test_output_passes_validator(self):
        output, _files = _unwrap(_run_static_implementation(
            "T09",
            [FIXTURES_DIR / "t09_report_data.xml"],
        ))
        assert T09.validator(output), f"T09 validator failed on: {output}"

    def test_has_required_keys(self):
        output, _files = _unwrap(_run_static_implementation(
            "T09",
            [FIXTURES_DIR / "t09_report_data.xml"],
        ))
        assert "summary" in output
        assert "stats_table" in output
        assert "anomalies" in output

    def test_exactly_five_anomalies(self):
        output, _files = _unwrap(_run_static_implementation(
            "T09",
            [FIXTURES_DIR / "t09_report_data.xml"],
        ))
        assert len(output["anomalies"]) == 5

    def test_summary_is_string(self):
        output, _files = _unwrap(_run_static_implementation(
            "T09",
            [FIXTURES_DIR / "t09_report_data.xml"],
        ))
        assert isinstance(output["summary"], str) and len(output["summary"]) > 0


class TestStaticT20:
    def test_output_passes_validator(self):
        output, _files = _unwrap(_run_static_implementation(
            "T20",
            [FIXTURES_DIR / "t20_applications.csv", FIXTURES_DIR / "t20_eligibility_policy.json"],
        ))
        assert T20.validator(output), f"T20 validator failed on: {output}"


class TestStaticUnknownTask:
    def test_raises_for_unknown_task(self):
        with pytest.raises(ValueError, match="No static implementation"):
            _run_static_implementation("T99", [])
