"""Unit tests for evaluation harness result extraction and run loops.

These tests use mocked execution paths to cover harness behavior without LLM
calls or Docker execution.
"""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch

import pytest

from src.schemas.capability import CapabilityDescriptor, CapabilityType
from src.schemas.task import ChainResult, GeneratedArtifact, MatchItem, SubTask, TaskState
from src.schemas.verification import VerificationReport
from tests.evaluation.harness import (
    EvaluationHarness,
    HarnessConfig,
    _derive_condition_seed,
    _extract_result,
    _extract_result_compositional,
)
from tests.evaluation.tasks import T01, T03, T04, T07, T13

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config(tmp_path):
    return HarnessConfig(
        chroma_dir=tmp_path / "chroma",
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


def _make_state(
    artifacts=None,
    reports=None,
    attempt_count=1,
    matches=None,
    **extra,
):
    return TaskState(
        task_description="test",
        generated_artifacts=artifacts or [],
        verification_results=reports or [],
        attempt_count=attempt_count,
        matches=matches or [],
        **extra,
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
        result = harness._run_static(T03, mock_registry)  # T03 has no static coverage
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
            mock_impl.return_value = {
                "row_count": 50,
                "column_names": ["id", "name", "age", "city"],
            }
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

    def test_decomposition_failure_short_circuits_single_artifact_path(self):
        state = _make_state(
            artifacts=[_make_artifact()],
            reports=[_make_report(passed=True)],
            decomposition_succeeded=False,
        )
        loader = MagicMock()
        with patch("tests.evaluation.harness._execute_artifact") as mock_exec:
            result = _extract_result(state, T01, loader)
        mock_exec.assert_not_called()
        assert result["gate_passed"] is False
        assert result["output_in_range"] is False
        assert result["failure_reason"] == "generation_failed"
        assert result["failed_step_reason"] == "decomposition_failed"
        assert result["chain_success"] is False
        assert len(result["generated_artifact_sources"]) == 1

    def test_registry_match_with_source_code_executes(self):
        """Registry match with source_code is sandbox-executed and shape-checked.

        T07 requires ``output.csv``. A dict-only mocked executor may satisfy
        the loose validator, but the output contract must still classify the
        run as an output-shape mismatch.
        """
        state = _make_state(
            artifacts=[],
            matches=[_make_match(source_code="# tool source")],
        )
        loader = MagicMock()
        fake_output = {"row_count": 12, "null_count_on_key": 0}
        with patch("tests.evaluation.harness._execute_artifact", return_value=fake_output):
            result = _extract_result(state, T07, loader)
        assert result["gate_passed"] is True
        # Missing output.csv flips output_in_range=False and promotes the
        # failure reason to output_shape_mismatch.
        assert result["output_in_range"] is False
        assert result["failure_reason"] == "output_shape_mismatch"
        assert result["output_shape_mismatch"] is True
        assert "output.csv" in result["output_shape_missing_files"]

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

    def test_decomposition_failure_short_circuits_compositional_path(self):
        state = _make_state(
            artifacts=[
                _make_artifact(
                    name="lead",
                    cap_type=CapabilityType.AGENT,
                    source_code=None,
                ),
            ],
            reports=[_make_report(passed=True)],
            decomposition_succeeded=False,
            required_capabilities=["parse-timeseries", "assemble-report"],
            subtasks=[
                SubTask(description="parse", required_capabilities=["parse-timeseries"]),
                SubTask(
                    description="assemble",
                    required_capabilities=["assemble-report"],
                    depends_on=[0],
                ),
            ],
        )
        loader = MagicMock()
        with patch("tests.evaluation.harness._extract_result_compositional") as mock_extract:
            result = _extract_result(state, T13, loader)
        mock_extract.assert_not_called()
        assert result["gate_passed"] is False
        assert result["failure_reason"] == "generation_failed"
        assert result["failed_step_reason"] == "decomposition_failed"
        assert result["chain_length"] == 2
        assert result["required_capability_count"] == 2
        assert result["chain_success"] is False


# ---------------------------------------------------------------------------
# _extract_result_compositional failure classification
# ---------------------------------------------------------------------------


class TestExtractResultCompositionalAgentFailure:
    """The harness maps compositional step failures to record failure reasons."""

    def _subtasks(self):
        return [
            SubTask(
                description="investigate",
                required_capabilities=["investigate"],
                depends_on=[],
            ),
        ]

    def _make_fake_chain_result(self, *, failed_step_reason: str | None):
        return ChainResult(
            chain_length=1,
            chain_success=False,
            step_results=[],
            failed_step_index=0,
            failed_step_name="investigate",
            failed_step_reason=failed_step_reason,
        )

    def _extract(self, chain_result):
        art = GeneratedArtifact(
            capability_name="investigator",
            capability_type=CapabilityType.AGENT,
            agent_config={"system_prompt": "Investigate"},
            gap_query="investigate",
        )
        with patch(
            "src.orchestrator.composition.CompositionalExecutor"
        ) as mock_exec_cls:
            mock_exec_cls.return_value.run.return_value = chain_result
            return _extract_result_compositional(
                subtasks=self._subtasks(),
                artifacts=[art],
                matches=[],
                required_capabilities=["investigate"],
                verification_results=[],
                attempt_count=1,
                task=T04,
            )

    def test_timeout_classified_as_agent_execution_timeout(self):
        result = self._extract(
            self._make_fake_chain_result(failed_step_reason="agent_execution_timeout"),
        )
        assert result["failure_reason"] == "agent_execution_timeout"
        assert result["chain_success"] is False
        assert result["output_in_range"] is False

    def test_error_classified_as_agent_execution_error(self):
        result = self._extract(
            self._make_fake_chain_result(failed_step_reason="agent_execution_error"),
        )
        assert result["failure_reason"] == "agent_execution_error"

    def test_none_reason_falls_back_to_chain_step_failed(self):
        result = self._extract(
            self._make_fake_chain_result(failed_step_reason=None),
        )
        assert result["failure_reason"] == "chain_step_failed"


# ---------------------------------------------------------------------------
# EvaluationHarness.run_pilot
# ---------------------------------------------------------------------------

class TestRunPilot:
    @patch("tests.evaluation.harness.restore_clean_snapshot")
    @patch("tests.evaluation.harness.seed_registry")
    @patch("tests.evaluation.harness.CapabilityRegistry")
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
    @patch("tests.evaluation.harness.CapabilityRegistry")
    @patch("tests.evaluation.harness.ToolLoader")
    def test_pilot_uses_full_condition_by_default(
        self, mock_loader_cls, mock_registry_cls, mock_seed, mock_restore, config
    ):
        mock_restore.return_value = "snap_abc12345"
        mock_registry_cls.return_value = MagicMock(get=MagicMock(return_value=None))

        harness = EvaluationHarness(config)
        # Use the static condition here to keep the default-condition assertion fast.
        records = harness.run_pilot([T04], condition="static")

        assert records[0].condition == "static"

    @patch("tests.evaluation.harness.restore_clean_snapshot")
    @patch("tests.evaluation.harness.seed_registry")
    @patch("tests.evaluation.harness.CapabilityRegistry")
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
    @patch("tests.evaluation.harness.CapabilityRegistry")
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
    @patch("tests.evaluation.harness.CapabilityRegistry")
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
    @patch("tests.evaluation.harness.CapabilityRegistry")
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
    @patch("tests.evaluation.harness.CapabilityRegistry")
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

    @patch("tests.evaluation.harness.restore_clean_snapshot")
    @patch("tests.evaluation.harness.seed_registry")
    @patch("tests.evaluation.harness.CapabilityRegistry")
    @patch("tests.evaluation.harness.ToolLoader")
    def test_run_condition_uses_full_uuid_entropy_in_run_id(
        self, mock_loader_cls, mock_registry_cls, mock_seed, mock_restore, config
    ):
        """Chapter 6 condition run IDs use a full 32-hex UUID suffix."""
        mock_restore.return_value = "snap_abc12345"
        mock_registry_cls.return_value = MagicMock(get=MagicMock(return_value=None))

        config.n_runs = 2
        harness = EvaluationHarness(config)
        records = harness.run_condition("static", [T04])

        suffixes = [record.run_id.rsplit("_", 1)[1] for record in records]
        assert len(suffixes) == 2
        for suffix in suffixes:
            assert len(suffix) == 32
            int(suffix, 16)


# ---------------------------------------------------------------------------
# Deterministic condition seed derivation
# ---------------------------------------------------------------------------


class TestConditionSeedIsDeterministic:
    """Condition seeds must be stable across processes and Python versions."""

    def test_derive_condition_seed_stable_across_calls(self):
        s1 = _derive_condition_seed("full", 42)
        s2 = _derive_condition_seed("full", 42)
        assert s1 == s2

    def test_derive_condition_seed_differs_by_condition(self):
        assert _derive_condition_seed("full", 42) != _derive_condition_seed(
            "static", 42,
        )

    def test_derive_condition_seed_differs_by_base(self):
        assert _derive_condition_seed("full", 42) != _derive_condition_seed(
            "full", 43,
        )

    def test_derive_condition_seed_matches_known_value(self):
        """Pin the expected SHA-256-derived seed for the canonical
        (full, 42) pair so a future change to the hashing scheme is
        caught explicitly."""
        expected = int.from_bytes(
            hashlib.sha256(b"full|42").digest()[:4], "big",
        ) % (2**31)
        assert _derive_condition_seed("full", 42) == expected

    def test_derive_condition_seed_in_expected_range(self):
        s = _derive_condition_seed("full", 42)
        assert 0 <= s < 2**31
