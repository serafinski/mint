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
    get_task,
)


class TestTaskRegistry:
    def test_all_tasks_count(self):
        assert len(ALL_TASKS) == 10

    def test_task_ids_unique(self):
        ids = [t.task_id for t in ALL_TASKS]
        assert len(ids) == len(set(ids))

    def test_pilot_tasks(self):
        ids = {t.task_id for t in PILOT_TASKS}
        assert ids == {"T01", "T07", "T10"}

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
        assert T04.validator({"row_count": 30, "delimiter": "|", "parse_errors": False})

    def test_wrong_delimiter(self):
        assert not T04.validator({"row_count": 30, "delimiter": ",", "parse_errors": False})

    def test_parse_errors_flag(self):
        assert not T04.validator({"row_count": 30, "delimiter": "|", "parse_errors": True})


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
        variances = dict(_EV["T06"]["original_variances"])
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
    def test_above_threshold(self):
        from tests.evaluation.tasks import _EV
        ev = _EV["T08"]
        threshold = ev["majority_class_accuracy"] + ev["accuracy_margin"]
        assert T08.validator({"accuracy": threshold + 0.01, "baseline_accuracy": 0.70})

    def test_below_threshold(self):
        assert not T08.validator({"accuracy": 0.50, "baseline_accuracy": 0.70})

    def test_missing_keys(self):
        assert not T08.validator({"accuracy": 0.90})


class TestT09Validator:
    def test_valid_report(self):
        anomalies = [{"region": "N", "revenue": i * 1e6} for i in range(5)]
        assert T09.validator({"summary": "text", "stats_table": {}, "anomalies": anomalies})

    def test_wrong_anomaly_count(self):
        assert not T09.validator({"summary": "text", "stats_table": {}, "anomalies": [1, 2]})

    def test_missing_key(self):
        assert not T09.validator({"summary": "text", "anomalies": [1, 2, 3, 4, 5]})


class TestT10Validator:
    def test_valid_parse(self):
        assert T10.validator({"row_count": 200, "timestamps_valid": True, "null_severity_count": 0})

    def test_wrong_line_count(self):
        assert not T10.validator({"row_count": 150, "timestamps_valid": True, "null_severity_count": 0})

    def test_invalid_timestamps(self):
        assert not T10.validator({"row_count": 200, "timestamps_valid": False, "null_severity_count": 0})

    def test_null_severities(self):
        assert not T10.validator({"row_count": 200, "timestamps_valid": True, "null_severity_count": 3})
