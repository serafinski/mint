"""Unit tests for tests/evaluation/execution.py — SKILL/AGENT execution via DeepAgent."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.evaluation.execution import (
    TASK_RESULT_MODELS,
    GenericResult,
    T11Result,
    T12Result,
    T13Result,
    _format_fixture_message,
    _read_fixture_contents,
    execute_agent_artifact,
    execute_skill_artifact,
)


# ---------------------------------------------------------------------------
# Result model tests
# ---------------------------------------------------------------------------


class TestResultModels:
    def test_t11_result_valid(self):
        r = T11Result(summary_row_count=4, region_revenues={"North": 1000.0}, regions_meeting_target=2)
        assert r.summary_row_count == 4

    def test_t12_result_valid(self):
        r = T12Result(clean_row_count=60, violations_found=20, rules_applied=3)
        assert r.rules_applied == 3

    def test_t13_result_valid(self):
        r = T13Result(anomaly_count=5, anomalies=[{"ts": "a", "m": "b", "v": 1}], metrics_analyzed=4)
        assert r.anomaly_count == 5

    def test_generic_result(self):
        r = GenericResult(result={"key": "value"})
        assert r.result == {"key": "value"}

    def test_task_result_models_mapping(self):
        assert TASK_RESULT_MODELS["T11"] is T11Result
        assert TASK_RESULT_MODELS["T12"] is T12Result
        assert TASK_RESULT_MODELS["T13"] is T13Result
        assert "T01" not in TASK_RESULT_MODELS


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


class TestReadFixtureContents:
    def test_reads_existing_files(self, tmp_path):
        f1 = tmp_path / "data.csv"
        f1.write_text("a,b\n1,2\n")
        task = SimpleNamespace(fixture_paths=[f1])
        contents = _read_fixture_contents(task)
        assert "data.csv" in contents
        assert contents["data.csv"] == "a,b\n1,2\n"

    def test_missing_file_skipped(self, tmp_path):
        task = SimpleNamespace(fixture_paths=[tmp_path / "nonexistent.csv"])
        contents = _read_fixture_contents(task)
        assert len(contents) == 0

    def test_no_fixture_paths(self):
        task = SimpleNamespace(fixture_paths=[])
        contents = _read_fixture_contents(task)
        assert contents == {}


class TestFormatFixtureMessage:
    def test_formats_files(self):
        result = _format_fixture_message({"a.csv": "col1\n1", "b.json": "{}"})
        assert "--- FILE: a.csv ---" in result
        assert "--- FILE: b.json ---" in result
        assert "col1\n1" in result


# ---------------------------------------------------------------------------
# SKILL execution
# ---------------------------------------------------------------------------


class TestExecuteSkillArtifact:
    @patch("tests.evaluation.execution._execute_skill_inner")
    def test_returns_dict_on_success(self, mock_inner):
        mock_inner.return_value = {"summary_row_count": 4, "region_revenues": {}, "regions_meeting_target": 2}
        task = SimpleNamespace(task_id="T11", task_prompt="test", fixture_paths=[])
        artifact = SimpleNamespace(skill_content="# Test Skill", capability_type="skill")
        result = execute_skill_artifact(artifact, task, {})
        assert result == {"summary_row_count": 4, "region_revenues": {}, "regions_meeting_target": 2}

    @patch("tests.evaluation.execution._execute_skill_inner")
    def test_returns_empty_on_exception(self, mock_inner):
        mock_inner.side_effect = RuntimeError("boom")
        task = SimpleNamespace(task_id="T11", task_prompt="test", fixture_paths=[])
        artifact = SimpleNamespace(skill_content="# Test Skill", capability_type="skill")
        result = execute_skill_artifact(artifact, task, {})
        assert result == {}

    @patch("tests.evaluation.execution._execute_skill_inner")
    def test_returns_empty_on_timeout(self, mock_inner):
        import concurrent.futures
        mock_inner.side_effect = concurrent.futures.TimeoutError()
        task = SimpleNamespace(task_id="T11", task_prompt="test", fixture_paths=[])
        artifact = SimpleNamespace(skill_content="# Test Skill", capability_type="skill")
        result = execute_skill_artifact(artifact, task, {})
        assert result == {}


# ---------------------------------------------------------------------------
# AGENT execution
# ---------------------------------------------------------------------------


class TestExecuteAgentArtifact:
    @patch("tests.evaluation.execution._execute_agent_inner")
    def test_returns_dict_on_success(self, mock_inner):
        mock_inner.return_value = {"anomaly_count": 5, "anomalies": [], "metrics_analyzed": 4}
        task = SimpleNamespace(task_id="T13", task_prompt="test", fixture_paths=[])
        artifact = SimpleNamespace(
            agent_config={"system_prompt": "You are an agent.", "is_deep_agent": False},
            skill_content=None,
            capability_type="agent",
        )
        result = execute_agent_artifact(artifact, task, {})
        assert result["anomaly_count"] == 5

    @patch("tests.evaluation.execution._execute_agent_inner")
    def test_returns_empty_on_exception(self, mock_inner):
        mock_inner.side_effect = RuntimeError("boom")
        task = SimpleNamespace(task_id="T13", task_prompt="test", fixture_paths=[])
        artifact = SimpleNamespace(agent_config={}, skill_content=None, capability_type="agent")
        result = execute_agent_artifact(artifact, task, {})
        assert result == {}

    @patch("tests.evaluation.execution._execute_agent_inner")
    def test_returns_empty_on_timeout(self, mock_inner):
        import concurrent.futures
        mock_inner.side_effect = concurrent.futures.TimeoutError()
        task = SimpleNamespace(task_id="T13", task_prompt="test", fixture_paths=[])
        artifact = SimpleNamespace(agent_config={}, skill_content=None, capability_type="agent")
        result = execute_agent_artifact(artifact, task, {})
        assert result == {}
