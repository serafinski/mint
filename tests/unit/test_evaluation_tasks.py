"""Unit tests for tests/evaluation/tasks.py — validators and task registry."""

from __future__ import annotations

import math

import pytest

from tests.evaluation.tasks import (
    ALL_TASKS,
    PILOT_TASKS,
    T01,
    T02,
    T03,
    T04,
    T05,
    T06,
    T07,
    T08,
    T09,
    T10,
    T11,
    T12,
    T13,
    T14,
    T15,
    T16,
    T17,
    T18,
    T19,
    T20,
    get_task,
)


class TestTaskRegistry:
    def test_all_tasks_count(self):
        assert len(ALL_TASKS) == 20

    def test_task_ids_unique(self):
        ids = [t.task_id for t in ALL_TASKS]
        assert len(ids) == len(set(ids))

    def test_pilot_tasks(self):
        ids = {t.task_id for t in PILOT_TASKS}
        assert ids == {"T01", "T07", "T05"}

    def test_get_task_returns_correct(self):
        assert get_task("T01") is T01
        assert get_task("T10") is T10

    def test_get_task_raises_on_unknown(self):
        with pytest.raises(KeyError):
            get_task("T99")

    def test_static_coverage_count(self):
        static = [t for t in ALL_TASKS if t.has_static_coverage]
        assert {t.task_id for t in static} == {
            "T01", "T02", "T04", "T05", "T09",
            "T11", "T13", "T14", "T15", "T19", "T20",
        }

    def test_tiers_present(self):
        tiers = {t.tier for t in ALL_TASKS}
        assert tiers == {"trivial", "moderate", "complex"}

    def test_fixture_paths_exist(self):
        for task in ALL_TASKS:
            for p in task.fixture_paths:
                assert p.exists(), f"Fixture missing for {task.task_id}: {p}"

    def test_fixture_generator_matches_expected_values(self, tmp_path):
        from tests.evaluation.fixtures.generate import generate_all_fixtures
        from tests.evaluation.tasks import _EV

        generated = generate_all_fixtures(tmp_path)

        assert generated == _EV


class TestT01Validator:
    def test_exact_match(self):
        assert T01.validator({"row_count": 50, "column_names": ["id", "name", "age", "city"]})

    def test_column_names_as_set(self):
        # Order should not matter
        assert T01.validator({"row_count": 50, "column_names": ["city", "age", "name", "id"]})

    def test_wrong_row_count(self):
        assert not T01.validator({"row_count": 49, "column_names": ["id", "name", "age", "city"]})

    def test_wrong_columns(self):
        assert not T01.validator({"row_count": 50, "column_names": ["id", "name"]})

    def test_missing_keys(self):
        assert not T01.validator({})


class TestT02Validator:
    def test_correct_error_count(self):
        assert T02.validator({"errors": ["e1", "e2", "e3"]})

    def test_wrong_error_count(self):
        assert not T02.validator({"errors": ["e1"]})
        assert not T02.validator({"errors": []})

    def test_missing_key(self):
        assert not T02.validator({})


class TestT03Validator:
    def test_valid_word_count_and_readability(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T03"]
        assert T03.validator(
            {
                "word_count": ev["word_count"],
                "readability_score": ev["readability_score"],
            }
        )

    def test_word_count_slightly_off_passes(self):
        from tests.evaluation.tasks import _EV
        wc = _EV["T03"]["word_count"]
        assert T03.validator({"word_count": wc + 1, "readability_score": 50.0})

    def test_word_count_beyond_tolerance_fails(self):
        """Pin the tolerance ceiling so the directional pass-test can't drift.

        Tolerance is ``word_count_tolerance_pct`` (currently 2%) so for wc=154
        the max-acceptable drift is ~3 words (|157-154|/154 = 1.95% ≤ 2%) and
        +4 is the smallest integer strictly beyond (|158-154|/154 = 2.60% > 2%).
        We compute the ceiling+1 from _EV so this stays correct if the fixture
        word_count or tolerance changes — any value that pushes
        |actual - expected| / expected strictly above the tol pct suffices.
        """
        from tests.evaluation.tasks import _EV
        ev = _EV["T03"]
        wc = ev["word_count"]
        tol_pct = ev["word_count_tolerance_pct"]
        # Smallest integer strictly beyond the ± tolerance window.
        beyond = math.floor(wc * tol_pct / 100.0) + 1
        # Sanity: the value just inside the ceiling still passes.
        assert T03.validator(
            {"word_count": wc + beyond - 1, "readability_score": 50.0}
        ), "sanity: ceiling value should still pass"
        # Load-bearing: the first value strictly beyond the ceiling must fail.
        assert not T03.validator(
            {"word_count": wc + beyond, "readability_score": 50.0}
        ), "+(ceiling+1) must fail — the tolerance ceiling was unpinned otherwise"

    def test_readability_out_of_range(self):
        from tests.evaluation.tasks import _EV
        wc = _EV["T03"]["word_count"]
        assert not T03.validator({"word_count": wc, "readability_score": 110.0})

    def test_missing_keys(self):
        assert not T03.validator({})


class TestT04Validator:
    def test_valid(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T04"]
        assert T04.validator({
            "product_count": ev["product_count"],
            "items_below_threshold": ev["items_below_threshold"],
            "total_reorder_cost": ev["total_reorder_cost"],
        })

    def test_wrong_product_count(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T04"]
        assert not T04.validator({
            "product_count": 999,
            "items_below_threshold": ev["items_below_threshold"],
            "total_reorder_cost": ev["total_reorder_cost"],
        })

    def test_cost_too_far_off(self):
        assert not T04.validator({
            "product_count": 10,
            "items_below_threshold": 7,
            "total_reorder_cost": 0.0,
        })


class TestT05Validator:
    def test_valid_cleaned(self):
        assert T05.validator({"output_row_count": 35, "null_count": 0})

    def test_row_count_not_reduced(self):
        from tests.evaluation.tasks import _EV
        input_count = _EV["T05"]["input_row_count"]
        assert not T05.validator({"output_row_count": input_count, "null_count": 0})

    def test_nulls_remaining(self):
        assert not T05.validator({"output_row_count": 35, "null_count": 2})


class TestT06Validator:
    def test_valid_imputation(self):
        from tests.evaluation.tasks import _EV
        variances = dict(
            _EV["T06"].get("expected_variances") or _EV["T06"]["original_variances"]
        )
        assert T06.validator({"null_count": 0, "column_variances": variances})

    def test_nulls_remaining(self):
        assert not T06.validator({"null_count": 5, "column_variances": {}})

    def test_variance_too_far_off(self):
        assert not T06.validator({"null_count": 0, "column_variances": {
            "feature_a": 9999.0, "feature_b": 9999.0,
            "feature_c": 9999.0, "feature_d": 9999.0,
        }})


class TestT07Validator:
    def test_valid_join(self):
        assert T07.validator({"row_count": 12, "null_count_on_key": 0})

    def test_wrong_count(self):
        assert not T07.validator({"row_count": 15, "null_count_on_key": 0})

    def test_nulls_on_key(self):
        assert not T07.validator({"row_count": 12, "null_count_on_key": 1})


class TestT08Validator:
    def test_valid(self):
        assert T08.validator({
            "n_rows": 100,
            "n_columns": 6,
            "quality_issues_found": 25,
            "issue_categories": ["null_ages", "salary_outliers"],
        })

    def test_no_issues_found(self):
        assert not T08.validator({
            "n_rows": 100,
            "n_columns": 6,
            "quality_issues_found": 0,
        })

    def test_wrong_row_count(self):
        assert not T08.validator({
            "n_rows": 999,
            "n_columns": 6,
            "quality_issues_found": 25,
        })


class TestT09Validator:
    def test_valid_report(self):
        anomalies = [{"region": "N", "revenue": i * 1e6} for i in range(5)]
        assert T09.validator({"summary": "text", "stats_table": {}, "anomalies": anomalies})

    def test_wrong_anomaly_count(self):
        assert not T09.validator({"summary": "text", "stats_table": {}, "anomalies": [1, 2]})

    def test_missing_key(self):
        assert not T09.validator({"summary": "text", "anomalies": [1, 2, 3, 4, 5]})


class TestT10Validator:
    def test_valid(self):
        assert T10.validator({
            "n_weeks": 52,
            "trend_direction": "upward",
            "has_seasonality": True,
            "forecast": [1900.0, 1920.0, 1940.0, 1960.0],
        })

    def test_wrong_trend(self):
        assert not T10.validator({
            "n_weeks": 52,
            "trend_direction": "downward",
            "forecast": [1900.0, 1920.0, 1940.0, 1960.0],
        })

    def test_no_forecast(self):
        assert not T10.validator({
            "n_weeks": 52,
            "trend_direction": "upward",
        })

    def test_wrong_weeks(self):
        assert not T10.validator({
            "n_weeks": 10,
            "trend_direction": "upward",
            "forecast": [100.0, 200.0, 300.0, 400.0],
        })


class TestT11Validator:
    def test_valid_integration(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T11"]
        assert T11.validator({
            "summary_row_count": ev["summary_row_count"],
            "region_revenues": ev["region_revenues"],
            "regions_meeting_target": ev["regions_meeting_target"],
        })

    def test_wrong_summary_count(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T11"]
        assert not T11.validator({
            "summary_row_count": 999,
            "region_revenues": ev["region_revenues"],
            "regions_meeting_target": ev["regions_meeting_target"],
        })

    def test_revenue_way_off(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T11"]
        bad_revenues = {k: v * 100 for k, v in ev["region_revenues"].items()}
        assert not T11.validator({
            "summary_row_count": ev["summary_row_count"],
            "region_revenues": bad_revenues,
            "regions_meeting_target": ev["regions_meeting_target"],
        })

    def test_missing_region(self):
        assert not T11.validator({
            "summary_row_count": 4,
            "region_revenues": {"North": 1000.0},
            "regions_meeting_target": 1,
        })

    def test_empty_output(self):
        assert not T11.validator({})


class TestT12Validator:
    def test_valid_cleaning(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T12"]
        assert T12.validator({
            "clean_row_count": ev["clean_row_count"],
            "violations_found": ev["violations_found"],
            "rules_applied": ev["rules_applied"],
        })

    def test_wrong_clean_count(self):
        assert not T12.validator({
            "clean_row_count": 999,
            "violations_found": 10,
            "rules_applied": 3,
        })

    def test_zero_violations(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T12"]
        assert not T12.validator({
            "clean_row_count": ev["clean_row_count"],
            "violations_found": 0,
            "rules_applied": 3,
        })

    def test_wrong_rules_count(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T12"]
        assert not T12.validator({
            "clean_row_count": ev["clean_row_count"],
            "violations_found": ev["violations_found"],
            "rules_applied": 2,
        })

    def test_empty_output(self):
        assert not T12.validator({})


class TestT13Validator:
    def test_valid_investigation(self):
        # Metric names must be real fixture columns.
        anomalies = [{"timestamp": "2024-06-01", "metric": "cpu_usage", "value": 99.0}] * 5
        assert T13.validator({
            "anomaly_count": 5,
            "anomalies": anomalies,
            "metrics_analyzed": 4,
        })

    def test_tolerant_anomaly_count(self):
        """Agent finding 3-7 anomalies should pass (threshold is >= 3)."""
        # Each entry must reference a real fixture metric name.
        anomalies = [{"timestamp": "t", "metric": "memory_mb", "value": 1.0}] * 3
        assert T13.validator({
            "anomaly_count": 3,
            "anomalies": anomalies,
            "metrics_analyzed": 4,
        })

    def test_too_few_anomalies(self):
        anomalies = [{"timestamp": "t", "metric": "m", "value": 1.0}] * 2
        assert not T13.validator({
            "anomaly_count": 2,
            "anomalies": anomalies,
            "metrics_analyzed": 4,
        })

    def test_empty_anomalies_list(self):
        assert not T13.validator({
            "anomaly_count": 5,
            "anomalies": [],
            "metrics_analyzed": 4,
        })

    def test_zero_metrics(self):
        assert not T13.validator({
            "anomaly_count": 5,
            "anomalies": [{"a": 1}],
            "metrics_analyzed": 0,
        })

    def test_empty_output(self):
        assert not T13.validator({})


class TestT14Validator:
    def test_valid_reconciliation(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T14"]
        assert T14.validator({
            "department_count": ev["department_count"],
            "department_spending": ev["department_spending"],
            "over_budget_count": ev["over_budget_count"],
            "flagged_transactions": ev["flagged_transactions"],
        })

    def test_wrong_department_count(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T14"]
        assert not T14.validator({
            "department_count": 999,
            "department_spending": ev["department_spending"],
            "over_budget_count": ev["over_budget_count"],
            "flagged_transactions": ev["flagged_transactions"],
        })

    def test_wrong_over_budget(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T14"]
        assert not T14.validator({
            "department_count": ev["department_count"],
            "department_spending": ev["department_spending"],
            "over_budget_count": 999,
            "flagged_transactions": ev["flagged_transactions"],
        })

    def test_empty_output(self):
        assert not T14.validator({})


class TestT15Validator:
    def test_valid_etl(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T15"]
        assert T15.validator({
            "n_days": ev["n_days"],
            "metrics_aggregated": ev["metrics_aggregated"],
            "alert_days": ev["alert_days"],
        })

    def test_wrong_n_days(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T15"]
        assert not T15.validator({
            "n_days": 999,
            "metrics_aggregated": ev["metrics_aggregated"],
            "alert_days": ev["alert_days"],
        })

    def test_wrong_alert_days(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T15"]
        assert not T15.validator({
            "n_days": ev["n_days"],
            "metrics_aggregated": ev["metrics_aggregated"],
            "alert_days": 999,
        })

    def test_empty_output(self):
        assert not T15.validator({})


class TestT16Validator:
    def test_valid_root_cause(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T16"]
        assert T16.validator({
            "root_cause_metric": ev["root_cause_metric"],
            "cascade_chain": [ev["root_cause_metric"], "some_other_metric"],
            "n_metrics_investigated": 4,
        })

    def test_wrong_root_cause(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T16"]
        assert not T16.validator({
            "root_cause_metric": "wrong_metric",
            "cascade_chain": [ev["root_cause_metric"], "other"],
            "n_metrics_investigated": 4,
        })

    def test_short_cascade_chain(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T16"]
        assert not T16.validator({
            "root_cause_metric": ev["root_cause_metric"],
            "cascade_chain": ["only_one"],
            "n_metrics_investigated": 4,
        })

    def test_too_few_investigated(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T16"]
        assert not T16.validator({
            "root_cause_metric": ev["root_cause_metric"],
            "cascade_chain": [ev["root_cause_metric"], "other"],
            "n_metrics_investigated": 1,
        })

    def test_empty_output(self):
        assert not T16.validator({})


class TestT17Validator:
    def test_valid_fraud_investigation(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T17"]
        assert T17.validator({
            "total_alerts": ev["total_alerts"],
            "high_severity_alerts": ev["high_severity_alerts"],
            "fraud_customer_ids": ev["fraud_customer_ids"],
        })

    def test_wrong_total_alerts(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T17"]
        assert not T17.validator({
            "total_alerts": 999,
            "high_severity_alerts": ev["high_severity_alerts"],
            "fraud_customer_ids": ev["fraud_customer_ids"],
        })

    def test_too_few_fraud_customers(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T17"]
        assert not T17.validator({
            "total_alerts": ev["total_alerts"],
            "high_severity_alerts": ev["high_severity_alerts"],
            "fraud_customer_ids": [999],
        })

    def test_fraud_customers_by_name(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T17"]
        names = ev["fraud_customer_names"][:2]
        assert T17.validator({
            "total_alerts": ev["total_alerts"],
            "high_severity_alerts": ev["high_severity_alerts"],
            "fraud_customer_ids": names,
        })

    def test_empty_output(self):
        assert not T17.validator({})


class TestT18Validator:
    def test_valid(self):
        assert T18.validator({
            "total_rows": 67,
            "unique_count": 50,
            "duplicate_count": 17,
        })

    def test_wrong_unique(self):
        assert not T18.validator({
            "total_rows": 67,
            "unique_count": 60,
            "duplicate_count": 7,
        })

    def test_wrong_total(self):
        assert not T18.validator({
            "total_rows": 100,
            "unique_count": 50,
            "duplicate_count": 17,
        })

    def test_empty_output(self):
        assert not T18.validator({})


class TestT19Validator:
    """Minimal coverage: happy path + one wrong-value rejection.

    T19 validator checks n_rows + n_categories + categories set-equality
    against _EV["T19"].
    """

    def test_valid_eda_output(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T19"]
        assert T19.validator({
            "n_rows": ev["n_rows"],
            "n_categories": ev["n_categories"],
            "categories": list(ev["categories"]),
        })

    def test_wrong_n_rows(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T19"]
        assert not T19.validator({
            "n_rows": 999,
            "n_categories": ev["n_categories"],
            "categories": list(ev["categories"]),
        })

    def test_wrong_categories(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T19"]
        assert not T19.validator({
            "n_rows": ev["n_rows"],
            "n_categories": ev["n_categories"],
            "categories": ["Wrong", "Labels", "Here"],
        })

    def test_empty_output(self):
        assert not T19.validator({})


class TestT20Validator:
    """Minimal coverage: happy path + wrong-value rejections.

    T20 validator checks total_applications, eligible_count, ineligible_count,
    rules_applied, and the cross-invariant eligible + ineligible == total.
    """

    def test_valid_eligibility(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T20"]
        assert T20.validator({
            "total_applications": ev["total_applications"],
            "eligible_count": ev["eligible_count"],
            "ineligible_count": ev["ineligible_count"],
            "rules_applied": ev["rules_applied"],
        })

    def test_wrong_eligible_count(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T20"]
        # Swap eligible↔ineligible — individually wrong AND cross-invariant
        # (eligible + ineligible still sums to total here so the rejection
        # is load-bearingly on the count field, not on the sum check).
        assert not T20.validator({
            "total_applications": ev["total_applications"],
            "eligible_count": ev["ineligible_count"],
            "ineligible_count": ev["eligible_count"],
            "rules_applied": ev["rules_applied"],
        })

    def test_sum_mismatch(self):
        """eligible + ineligible must equal total_applications."""
        from tests.evaluation.tasks import _EV
        ev = _EV["T20"]
        assert not T20.validator({
            "total_applications": ev["total_applications"],
            "eligible_count": ev["eligible_count"],
            "ineligible_count": ev["ineligible_count"] + 5,  # sum drifts
            "rules_applied": ev["rules_applied"],
        })

    def test_empty_output(self):
        assert not T20.validator({})


# ---------------------------------------------------------------------------
# TaskDefinition.expected_type invariants
#
# These tests pin the expected primary-type distribution ("8 mcp_tool /
# 6 skill / 6 agent") and guard against silent drift when someone edits a
# task definition.
# ---------------------------------------------------------------------------


class TestExpectedTypeInvariants:
    """Every task has ``expected_type`` set + overall distribution matches."""

    def test_every_task_has_expected_type_set(self):
        """Zero ``None`` values — every one of the 20 tasks is labelled."""
        unlabelled = [t.task_id for t in ALL_TASKS if t.expected_type is None]
        assert unlabelled == [], f"Tasks missing expected_type: {unlabelled}"

    def test_expected_type_values_are_valid(self):
        """Only {mcp_tool, skill, agent} are valid primary-type labels."""
        valid = {"mcp_tool", "skill", "agent"}
        bad = [
            (t.task_id, t.expected_type)
            for t in ALL_TASKS
            if t.expected_type not in valid
        ]
        assert bad == [], f"Invalid expected_type labels: {bad}"

    def test_distribution_matches_step_18_5_claim(self):
        """The expected-type distribution is fixed at 8/6/6.

        If someone adjusts a task's type without updating this count,
        the classification-accuracy result silently shifts.
        """
        counts: dict[str, int] = {"mcp_tool": 0, "skill": 0, "agent": 0}
        for t in ALL_TASKS:
            if t.expected_type in counts:
                counts[t.expected_type] += 1
        assert counts == {"mcp_tool": 8, "skill": 6, "agent": 6}, (
            f"Distribution drifted from the expected 8/6/6 split: {counts}"
        )

    def test_total_task_count_is_20(self):
        """Testbed is fixed at T01-T20 for the evaluation protocol."""
        assert len(ALL_TASKS) == 20


# ---------------------------------------------------------------------------
# T07 + T11 file validators
# ---------------------------------------------------------------------------


class TestT07FileValidatorFailsClosed:
    """Missing ``output.csv`` fails closed because the file is contract-required."""

    def test_missing_csv_fails_closed(self):
        from tests.evaluation.tasks import _file_validate_t07, _t07_ev

        output = {"row_count": _t07_ev["expected_join_count"]}
        assert _file_validate_t07(output, output_files={}) is False

    def test_undecodable_csv_fails_closed(self):
        from tests.evaluation.tasks import _file_validate_t07, _t07_ev

        output = {"row_count": _t07_ev["expected_join_count"]}
        assert _file_validate_t07(
            output, output_files={"output.csv": b"\xff\xfe\x00\x00"},
        ) is False

    def test_wrong_row_count_fails(self):
        from tests.evaluation.tasks import _file_validate_t07

        output = {"row_count": 9999}
        csv = b"a,b,c\n1,2,3\n"
        assert _file_validate_t07(
            output, output_files={"output.csv": csv},
        ) is False


class TestT11FileValidatorFailsClosed:
    """Missing/corrupt ``output.xlsx`` files fail closed.

    The only graceful degradation path is the ImportError case where openpyxl is
    unavailable in a local developer environment.
    """

    def _t11_good_output(self):
        from tests.evaluation.tasks import _t11_ev
        return {
            "summary_row_count": _t11_ev["summary_row_count"],
            "region_revenues": dict(_t11_ev["region_revenues"]),
            "regions_meeting_target": _t11_ev["regions_meeting_target"],
        }

    def test_missing_xlsx_fails_closed(self):
        from tests.evaluation.tasks import _file_validate_t11

        assert _file_validate_t11(
            self._t11_good_output(), output_files={},
        ) is False

    def test_corrupt_xlsx_fails_closed(self):
        from tests.evaluation.tasks import _file_validate_t11

        bad = b"this is not an xlsx file"
        assert _file_validate_t11(
            self._t11_good_output(), output_files={"output.xlsx": bad},
        ) is False


class TestTightenedValidators:
    """Tightened validators reject hallucinated/minimal outputs."""

    # -- T05 -----------------------------------------------------------------

    @pytest.mark.parametrize("output", [
        {"output_row_count": 1, "null_count": 0},
        {"output_row_count": 5, "null_count": 0},
    ])
    def test_t05_rejects_hallucinated_minimal_output(self, output):
        from tests.evaluation.tasks import _validate_t05

        assert _validate_t05(output) is False

    def test_t05_rejects_no_reduction(self):
        from tests.evaluation.tasks import _t05_ev, _validate_t05

        # Equal to input_row_count — implies no dedup/null drop happened.
        assert _validate_t05({
            "output_row_count": _t05_ev["input_row_count"],
            "null_count": 0,
        }) is False

    def test_t05_accepts_plausible_output(self):
        from tests.evaluation.tasks import _t05_ev, _validate_t05

        assert _validate_t05({
            "output_row_count": _t05_ev["expected_max_output_row_count"],
            "null_count": 0,
        }) is True

    # -- T09 -----------------------------------------------------------------

    def test_t09_rejects_fabricated_anomaly_strings(self):
        from tests.evaluation.tasks import _t09_ev, _validate_t09

        output = {
            **{k: "fake" for k in _t09_ev["required_keys"]},
            "anomalies": ["fake"] * _t09_ev["expected_anomaly_count"],
        }
        assert _validate_t09(output) is False

    def test_t09_rejects_duplicate_anomalies(self):
        from tests.evaluation.tasks import _t09_ev, _validate_t09

        # Non-digit strings are rejected; even if they had digits, duplicates fail.
        output = {
            **{k: "x" for k in _t09_ev["required_keys"]},
            "anomalies": ["rev=100"] * _t09_ev["expected_anomaly_count"],
        }
        assert _validate_t09(output) is False

    def test_t09_accepts_structured_anomalies(self):
        from tests.evaluation.tasks import _t09_ev, _validate_t09

        anomalies = [
            {"region": "R1", "product": "P1", "revenue": 1000.0 + i}
            for i in range(_t09_ev["expected_anomaly_count"])
        ]
        output = {
            **{k: "x" for k in _t09_ev["required_keys"]},
            "anomalies": anomalies,
        }
        assert _validate_t09(output) is True

    # -- T10 -----------------------------------------------------------------

    def test_t10_rejects_zero_forecast(self):
        from tests.evaluation.tasks import _t10_ev, _validate_t10

        assert _validate_t10({
            "n_weeks": _t10_ev["n_weeks"],
            "trend_direction": "upward",
            "forecast": [0, 0, 0, 0],
        }) is False

    def test_t10_rejects_constant_forecast(self):
        from tests.evaluation.tasks import _t10_ev, _validate_t10

        assert _validate_t10({
            "n_weeks": _t10_ev["n_weeks"],
            "trend_direction": "upward",
            "forecast": [500, 500, 500, 500],
        }) is False

    def test_t10_rejects_wrong_forecast_length(self):
        from tests.evaluation.tasks import _t10_ev, _validate_t10

        # n_forecast_weeks is 4 — a 3-element forecast is rejected.
        assert _validate_t10({
            "n_weeks": _t10_ev["n_weeks"],
            "trend_direction": "upward",
            "forecast": [1800, 1900, 2000],
        }) is False

    def test_t10_accepts_plausible_forecast(self):
        from tests.evaluation.tasks import _t10_ev, _validate_t10

        recent = float(_t10_ev["avg_last_4_weeks"])
        assert _validate_t10({
            "n_weeks": _t10_ev["n_weeks"],
            "trend_direction": "upward",
            "forecast": [recent, recent * 1.05, recent * 1.10, recent * 1.15],
        }) is True

    # -- T13 -----------------------------------------------------------------

    def test_t13_rejects_fabricated_metric_names(self):
        from tests.evaluation.tasks import _validate_t13

        output = {
            "anomaly_count": 5,
            "anomalies": [{"metric": "banana", "timestamp": "2024-06-01"}] * 5,
            "metrics_analyzed": 4,
        }
        assert _validate_t13(output) is False

    def test_t13_rejects_string_anomaly_entries(self):
        from tests.evaluation.tasks import _validate_t13

        output = {
            "anomaly_count": 5,
            "anomalies": ["anomaly"] * 5,
            "metrics_analyzed": 4,
        }
        assert _validate_t13(output) is False

    def test_t13_rejects_missing_timestamp(self):
        from tests.evaluation.tasks import _validate_t13

        output = {
            "anomaly_count": 5,
            "anomalies": [{"metric": "cpu_usage", "value": 244.0}] * 5,
            "metrics_analyzed": 4,
        }
        assert _validate_t13(output) is False

    def test_t13_accepts_real_metric_names(self):
        from tests.evaluation.tasks import _validate_t13

        output = {
            "anomaly_count": 5,
            "anomalies": [
                {"metric": "memory_mb", "timestamp": "2024-06-01 00:45:00", "value": 15134.607},
                {"metric": "memory_mb", "timestamp": "2024-06-01 11:15:00", "value": 18518.638},
                {"metric": "cpu_usage", "timestamp": "2024-06-01 18:45:00", "value": 244.754},
                {"metric": "memory_mb", "timestamp": "2024-06-01 22:15:00", "value": 19215.307},
                {"metric": "error_rate", "timestamp": "2024-06-01 23:30:00", "value": 0.203},
            ],
            "metrics_analyzed": 4,
        }
        assert _validate_t13(output) is True


class TestPermissiveValidatorDisclosure:
    """Permissive-validator set and one-time WARN log stay explicit."""

    def test_permissive_set_contents(self):
        from tests.evaluation.tasks import _PERMISSIVE_VALIDATOR_TASK_IDS

        # The 5 tasks that remain metadata-only after the pragmatic subset fix.
        assert _PERMISSIVE_VALIDATOR_TASK_IDS == frozenset({
            "T02", "T08", "T12", "T15", "T16",
        })

    def test_permissive_warn_emits_once(self, caplog):
        import logging as _logging

        from tests.evaluation import tasks as _tasks_module

        _tasks_module._PERMISSIVE_WARN_EMITTED = False
        with caplog.at_level(_logging.WARNING, logger="tests.evaluation.tasks"):
            _tasks_module.warn_permissive_validators_once()
            _tasks_module.warn_permissive_validators_once()

        warnings = [
            r for r in caplog.records
            if "metadata-only validators" in r.getMessage()
        ]
        assert len(warnings) == 1
        msg = warnings[0].getMessage()
        for tid in ("T02", "T08", "T12", "T15", "T16"):
            assert tid in msg
