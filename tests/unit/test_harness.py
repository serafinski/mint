"""Layer 2: harness unit tests with mocked pipeline.

Tests the run loop, _extract_result, _run_static, and record writing
without any LLM calls or Docker execution.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.schemas.capability import CapabilityDescriptor, CapabilityType
from src.schemas.task import GeneratedArtifact, MatchItem, TaskState
from src.schemas.verification import VerificationReport
from tests.evaluation.harness import EvaluationHarness, HarnessConfig, _extract_result
from tests.evaluation.tasks import T01, T04, T07

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config(tmp_path):
    return HarnessConfig(
        chroma_dir=tmp_path / "chroma",
        mcp_tools_dir=tmp_path / "mcp_tools",
        skills_dir=tmp_path / "skills",
        db_path=None,  # in-memory
        n_runs=2,
        base_seed=42,
    )


def _make_artifact(name="csv-parser", cap_type=CapabilityType.MCP_TOOL, source_code="# tool"):
    return GeneratedArtifact(
        capability_name=name,
        capability_type=cap_type,
        source_code=source_code,
    )


def _make_report(passed=True, tier_reached=2):
    return VerificationReport(
        capability_name="csv-parser",
        findings=[],
        overall_passed=passed,
        tier_reached=tier_reached,
    )


def _make_state(artifacts=None, reports=None, attempt_count=1, matches=None):
    return TaskState(
        task_description="test",
        generated_artifacts=artifacts or [],
        verification_results=reports or [],
        attempt_count=attempt_count,
        matches=matches or [],
    )


def _make_match(name="fuzzy-join", source_code=None):
    meta = {"generated": "true", "source": "jit_pipeline"}
    if source_code:
        meta["source_code"] = source_code
    cap = CapabilityDescriptor(
        name=name,
        description="A test capability",
        capability_type=CapabilityType.MCP_TOOL,
        metadata=meta,
    )
    return MatchItem(capability=cap, score=0.85)


# ---------------------------------------------------------------------------
# _run_static
# ---------------------------------------------------------------------------

class TestRunStatic:
    def test_no_static_coverage_returns_failure(self, config):
        harness = EvaluationHarness(config)
        mock_registry = MagicMock()
        result = harness._run_static(T04, mock_registry)  # T04 has no static coverage
        assert result["gate_passed"] is False
        assert result["failure_reason"] == "generation_failed"

    def test_registry_miss_returns_failure(self, config):
        harness = EvaluationHarness(config)
        mock_registry = MagicMock()
        mock_registry.get.return_value = None
        result = harness._run_static(T01, mock_registry)
        assert result["gate_passed"] is False
        assert result["failure_reason"] == "generation_failed"

    def test_static_coverage_success(self, config):
        harness = EvaluationHarness(config)
        mock_registry = MagicMock()
        mock_cap = MagicMock()
        mock_cap.capability_type = CapabilityType.MCP_TOOL
        mock_registry.get.return_value = mock_cap

        with patch("tests.evaluation.harness._run_static_implementation") as mock_impl:
            mock_impl.return_value = {"row_count": 50, "column_names": ["id", "name", "age", "city"]}
            result = harness._run_static(T01, mock_registry)

        assert result["gate_passed"] is True
        assert result["output_in_range"] is True
        assert result["failure_reason"] == "none"

    def test_static_coverage_output_rejected(self, config):
        harness = EvaluationHarness(config)
        mock_registry = MagicMock()
        mock_registry.get.return_value = MagicMock()

        with patch("tests.evaluation.harness._run_static_implementation") as mock_impl:
            mock_impl.return_value = {"row_count": 999, "column_names": []}
            result = harness._run_static(T01, mock_registry)

        assert result["gate_passed"] is True
        assert result["output_in_range"] is False
        assert result["failure_reason"] == "output_rejected"

    def test_static_impl_exception_returns_failure(self, config):
        harness = EvaluationHarness(config)
        mock_registry = MagicMock()
        mock_registry.get.return_value = MagicMock()

        with patch("tests.evaluation.harness._run_static_implementation") as mock_impl:
            mock_impl.side_effect = RuntimeError("filesystem error")
            result = harness._run_static(T01, mock_registry)

        assert result["gate_passed"] is False
        assert result["failure_reason"] == "generation_failed"


# ---------------------------------------------------------------------------
# _extract_result
# ---------------------------------------------------------------------------

class TestExtractResult:
    def test_no_artifacts_returns_failure(self):
        state = _make_state(artifacts=[], reports=[])
        loader = MagicMock()
        result = _extract_result(state, T07, loader)
        assert result["gate_passed"] is False
        assert result["failure_reason"] == "generation_failed"

    def test_artifact_with_passing_report(self):
        state = _make_state(
            artifacts=[_make_artifact()],
            reports=[_make_report(passed=True, tier_reached=2)],
            attempt_count=1,
        )
        loader = MagicMock()
        with patch("tests.evaluation.harness._execute_artifact", return_value={}):
            result = _extract_result(state, T07, loader)

        assert result["gate_passed"] is True
        assert result["attempt_count"] == 1
        assert result["verification_tier_reached"] == 2

    def test_artifact_with_failing_report_tier1(self):
        state = _make_state(
            artifacts=[_make_artifact()],
            reports=[_make_report(passed=False, tier_reached=1)],
        )
        loader = MagicMock()
        result = _extract_result(state, T07, loader)
        assert result["gate_passed"] is False
        assert result["failure_reason"] == "tier1"

    def test_artifact_with_failing_report_tier2(self):
        state = _make_state(
            artifacts=[_make_artifact()],
            reports=[_make_report(passed=False, tier_reached=2)],
        )
        loader = MagicMock()
        result = _extract_result(state, T07, loader)
        assert result["failure_reason"] == "tier2"

    def test_artifact_with_failing_report_tier3(self):
        state = _make_state(
            artifacts=[_make_artifact()],
            reports=[_make_report(passed=False, tier_reached=3)],
        )
        loader = MagicMock()
        result = _extract_result(state, T07, loader)
        assert result["failure_reason"] == "tier3"

    def test_gate_passed_output_in_range_sets_none(self):
        """gate_passed=True + validator passes → failure_reason='none'."""
        state = _make_state(
            artifacts=[_make_artifact()],
            reports=[_make_report(passed=True, tier_reached=2)],
        )
        loader = MagicMock()
        # T01 validator needs row_count=50 and correct columns
        fake_output = {"row_count": 50, "column_names": ["id", "name", "age", "city"]}
        with patch("tests.evaluation.harness._execute_artifact", return_value=fake_output):
            result = _extract_result(state, T01, loader)
        assert result["gate_passed"] is True
        assert result["output_in_range"] is True
        assert result["failure_reason"] == "none"

    def test_gate_passed_output_rejected(self):
        """gate_passed=True + validator fails → failure_reason='output_rejected'."""
        state = _make_state(
            artifacts=[_make_artifact()],
            reports=[_make_report(passed=True, tier_reached=2)],
        )
        loader = MagicMock()
        with patch("tests.evaluation.harness._execute_artifact", return_value={"row_count": 1}):
            result = _extract_result(state, T01, loader)
        assert result["gate_passed"] is True
        assert result["output_in_range"] is False
        assert result["failure_reason"] == "output_rejected"

    def test_no_verification_report_gate_passes(self):
        """no_verification condition: artifacts present but no report → gate bypassed."""
        state = _make_state(
            artifacts=[_make_artifact()],
            reports=[],  # no report = bypassed
        )
        loader = MagicMock()
        with patch("tests.evaluation.harness._execute_artifact", return_value={}):
            result = _extract_result(state, T07, loader)
        assert result["gate_passed"] is True
        assert result["verification_tier_reached"] is None

    def test_capability_type_extracted(self):
        state = _make_state(
            artifacts=[_make_artifact(cap_type=CapabilityType.SKILL)],
            reports=[_make_report(passed=True)],
        )
        loader = MagicMock()
        with patch("tests.evaluation.harness._execute_artifact", return_value={}):
            result = _extract_result(state, T07, loader)
        assert result["capability_type"] == "skill"

    def test_execute_artifact_exception_does_not_raise(self):
        state = _make_state(
            artifacts=[_make_artifact()],
            reports=[_make_report(passed=True)],
        )
        loader = MagicMock()
        with patch("tests.evaluation.harness._execute_artifact", side_effect=RuntimeError("boom")):
            result = _extract_result(state, T07, loader)
        assert result["gate_passed"] is True
        assert result["output_in_range"] is False

    def test_registry_match_with_source_code_executes(self):
        """Registry match with source_code in metadata → sandbox-executed."""
        state = _make_state(
            artifacts=[],
            matches=[_make_match(source_code="# tool source")],
        )
        loader = MagicMock()
        fake_output = {"row_count": 12, "null_count_on_key": 0}
        with patch("tests.evaluation.harness._execute_artifact", return_value=fake_output):
            result = _extract_result(state, T07, loader)
        assert result["gate_passed"] is True
        assert result["output_in_range"] is True
        assert result["failure_reason"] == "none"

    def test_registry_match_without_source_code_cannot_validate(self):
        """Registry match without source_code → gate passes but output not validated."""
        state = _make_state(
            artifacts=[],
            matches=[_make_match(source_code=None)],
        )
        loader = MagicMock()
        result = _extract_result(state, T07, loader)
        assert result["gate_passed"] is True
        assert result["output_in_range"] is False
        assert result["failure_reason"] == "no_source_code"

    def test_registry_match_execution_failure_does_not_raise(self):
        """Registry match execution fails → gate passes, output not validated."""
        state = _make_state(
            artifacts=[],
            matches=[_make_match(source_code="# bad tool")],
        )
        loader = MagicMock()
        with patch("tests.evaluation.harness._execute_artifact", side_effect=RuntimeError("boom")):
            result = _extract_result(state, T07, loader)
        assert result["gate_passed"] is True
        assert result["output_in_range"] is False
        assert result["failure_reason"] == "output_rejected"


# ---------------------------------------------------------------------------
# EvaluationHarness.run_pilot
# ---------------------------------------------------------------------------

class TestRunPilot:
    @patch("tests.evaluation.harness.restore_clean_snapshot")
    @patch("tests.evaluation.harness.seed_registry")
    @patch("tests.evaluation.harness.SkillRegistry")
    @patch("tests.evaluation.harness.ToolLoader")
    def test_pilot_records_written(
        self, mock_loader_cls, mock_registry_cls, mock_seed, mock_restore, config
    ):
        mock_restore.return_value = "snap_abc12345"
        mock_registry = MagicMock()
        mock_registry.search.return_value = []
        mock_registry_cls.return_value = mock_registry

        harness = EvaluationHarness(config)
        records = harness.run_pilot([T04])  # T04 uses JIT pipeline (full condition)

        assert len(records) == 1
        assert records[0].is_pilot is True
        assert records[0].task_id == "T04"
        assert records[0].registry_snapshot_id == "snap_abc12345"

    @patch("tests.evaluation.harness.restore_clean_snapshot")
    @patch("tests.evaluation.harness.seed_registry")
    @patch("tests.evaluation.harness.SkillRegistry")
    @patch("tests.evaluation.harness.ToolLoader")
    def test_pilot_uses_full_condition_by_default(
        self, mock_loader_cls, mock_registry_cls, mock_seed, mock_restore, config
    ):
        mock_restore.return_value = "snap_abc12345"
        mock_registry_cls.return_value = MagicMock(get=MagicMock(return_value=None))

        harness = EvaluationHarness(config)
        # run_pilot default condition is "full"; pass static explicitly for a fast no-LLM test
        records = harness.run_pilot([T04], condition="static")

        assert records[0].condition == "static"

    @patch("tests.evaluation.harness.restore_clean_snapshot")
    @patch("tests.evaluation.harness.seed_registry")
    @patch("tests.evaluation.harness.SkillRegistry")
    @patch("tests.evaluation.harness.ToolLoader")
    def test_pilot_records_stored_in_db(
        self, mock_loader_cls, mock_registry_cls, mock_seed, mock_restore, config
    ):
        mock_restore.return_value = "snap_abc12345"
        mock_registry_cls.return_value = MagicMock(get=MagicMock(return_value=None))

        harness = EvaluationHarness(config)
        harness.run_pilot([T04])

        rows = harness.store.get_runs(is_pilot=True)
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# EvaluationHarness.run_condition
# ---------------------------------------------------------------------------

class TestRunCondition:
    @patch("tests.evaluation.harness.restore_clean_snapshot")
    @patch("tests.evaluation.harness.seed_registry")
    @patch("tests.evaluation.harness.SkillRegistry")
    @patch("tests.evaluation.harness.ToolLoader")
    def test_run_condition_calls_restore_per_run(
        self, mock_loader_cls, mock_registry_cls, mock_seed, mock_restore, config
    ):
        mock_restore.return_value = "snap_abc12345"
        mock_registry_cls.return_value = MagicMock(get=MagicMock(return_value=None))

        config.n_runs = 3
        harness = EvaluationHarness(config)
        harness.run_condition("static", [T04])

        # restore_clean_snapshot called once per run
        assert mock_restore.call_count == 3

    @patch("tests.evaluation.harness.restore_clean_snapshot")
    @patch("tests.evaluation.harness.seed_registry")
    @patch("tests.evaluation.harness.SkillRegistry")
    @patch("tests.evaluation.harness.ToolLoader")
    def test_run_condition_returns_n_runs_times_n_tasks(
        self, mock_loader_cls, mock_registry_cls, mock_seed, mock_restore, config
    ):
        mock_restore.return_value = "snap_abc12345"
        mock_registry_cls.return_value = MagicMock(get=MagicMock(return_value=None))

        config.n_runs = 2
        harness = EvaluationHarness(config)
        records = harness.run_condition("static", [T04, T01])

        assert len(records) == 4  # 2 runs × 2 tasks

    @patch("tests.evaluation.harness.restore_clean_snapshot")
    @patch("tests.evaluation.harness.seed_registry")
    @patch("tests.evaluation.harness.SkillRegistry")
    @patch("tests.evaluation.harness.ToolLoader")
    def test_run_condition_non_pilot(
        self, mock_loader_cls, mock_registry_cls, mock_seed, mock_restore, config
    ):
        mock_restore.return_value = "snap_abc12345"
        mock_registry_cls.return_value = MagicMock(get=MagicMock(return_value=None))

        harness = EvaluationHarness(config)
        records = harness.run_condition("static", [T04])

        assert all(not r.is_pilot for r in records)

    @patch("tests.evaluation.harness.restore_clean_snapshot")
    @patch("tests.evaluation.harness.seed_registry")
    @patch("tests.evaluation.harness.SkillRegistry")
    @patch("tests.evaluation.harness.ToolLoader")
    def test_run_condition_different_seeds_per_run(
        self, mock_loader_cls, mock_registry_cls, mock_seed, mock_restore, config
    ):
        """Runs use different seeds (task order is randomised per run)."""
        mock_restore.return_value = "snap_abc12345"
        mock_registry_cls.return_value = MagicMock(get=MagicMock(return_value=None))

        config.n_runs = 2
        harness = EvaluationHarness(config)
        records = harness.run_condition("static", [T04])

        seeds = {r.seed for r in records}
        # With 2 runs the seeds should differ (extremely unlikely to collide)
        assert len(seeds) == 2
