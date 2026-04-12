"""Unit tests for tests/evaluation/tasks.py — validators and task registry."""

from __future__ import annotations

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
        assert len(static) == 5  # T01, T02, T03, T05, T09

    def test_tiers_present(self):
        tiers = {t.tier for t in ALL_TASKS}
        assert tiers == {"trivial", "moderate", "complex"}

    def test_fixture_paths_exist(self):
        for task in ALL_TASKS:
            for p in task.fixture_paths:
                assert p.exists(), f"Fixture missing for {task.task_id}: {p}"


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
        assert T03.validator({"word_count": ev["word_count"], "readability_score": ev["readability_score"]})

    def test_word_count_slightly_off_passes(self):
        from tests.evaluation.tasks import _EV
        wc = _EV["T03"]["word_count"]
        assert T03.validator({"word_count": wc + 1, "readability_score": 50.0})

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
        anomalies = [{"timestamp": "2024-06-01", "metric": "cpu", "value": 99.0}] * 5
        assert T13.validator({
            "anomaly_count": 5,
            "anomalies": anomalies,
            "metrics_analyzed": 4,
        })

    def test_tolerant_anomaly_count(self):
        """Agent finding 3-7 anomalies should pass (threshold is >= 3)."""
        anomalies = [{"timestamp": "t", "metric": "m", "value": 1.0}] * 3
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
