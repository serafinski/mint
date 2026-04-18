"""Step 14.12 — unit tests for ``--allow-network`` plumbing through the CLI.

Verifies the CLI entry point threads the flag into ``TaskState`` and down
through the execution backends. Mocks the pipeline invocation and the
sandbox layer — no Docker, no LLM calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.schemas.task import TaskState


class TestTaskStateCarriesAllowNetwork:
    """The TaskState schema accepts and preserves ``allow_network``."""

    def test_default_is_false(self):
        state = TaskState(task_description="x")
        assert state.allow_network is False

    def test_explicit_true_preserved(self):
        state = TaskState(task_description="x", allow_network=True)
        assert state.allow_network is True


class TestRunnerThreadsAllowNetworkToTaskState:
    """``execute_task(..., allow_network=True)`` must build a TaskState with
    ``allow_network=True`` — that's the entry point for every downstream
    sandbox_execute call.
    """

    @patch("src.cli.runner._check_docker", return_value=True)
    @patch("src.cli.runner._create_infra")
    @patch("src.orchestrator.pipeline.create_pipeline")
    def test_allow_network_flag_lands_in_task_state(
        self, mock_create_pipeline, mock_create_infra, mock_check_docker,
    ):
        from src.cli.runner import execute_task

        captured: dict = {}

        def _fake_invoke(state, config=None):
            captured["allow_network"] = state.allow_network
            captured["task_description"] = state.task_description
            return {
                "generated_artifacts": [],
                "matches": [],
                "subtasks": [],
                "classified_gaps": [],
                "required_capabilities": [],
                "verification_results": [],
                "decomposition_succeeded": True,
                "classification_succeeded": True,
                "verification_succeeded": True,
                "output_contract": state.output_contract,
                "allow_network": state.allow_network,
            }

        mock_pipeline = MagicMock()
        mock_pipeline.invoke.side_effect = _fake_invoke
        compiled = MagicMock()
        compiled.invoke.side_effect = _fake_invoke
        mock_pipeline.compile.return_value = compiled
        mock_create_pipeline.return_value = mock_pipeline

        # Stub infra — metrics_store.invocation_count_total must be callable.
        registry = MagicMock()
        loader = MagicMock()
        metrics_store = MagicMock()
        metrics_store.invocation_count_total.return_value = 0
        mock_create_infra.return_value = (registry, loader, metrics_store)

        # Run with --allow-network (JSON mode to avoid Rich panels).
        execute_task(
            "fetch something live",
            json_output=True,
            allow_network=True,
        )

        assert captured.get("allow_network") is True


class TestGateForwardsAllowNetworkFromTaskState:
    """When ``TaskState.allow_network=True``, the verification node must
    wrap the gate_fn so the real ``gate.verify`` receives the kwarg.
    """

    def test_verification_node_wraps_gate_fn_when_allow_network(self):
        from src.orchestrator.verification_node import verification_node
        from src.schemas.task import TaskState
        from src.schemas.verification import VerificationReport

        captured: dict = {}

        def fake_gate(artifact, *, allow_network=False):
            captured["allow_network"] = allow_network
            return VerificationReport(
                capability_name=artifact.capability_name,
                findings=[],
                overall_passed=True,
                tier_reached=3,
            )

        from src.schemas.capability import CapabilityType
        from src.schemas.task import GeneratedArtifact

        artifact = GeneratedArtifact(
            capability_name="net-tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code="def f(): return 1",
        )
        state = TaskState(
            task_description="x",
            generated_artifacts=[artifact],
            allow_network=True,
        )
        verification_node(state, gate_fn=fake_gate)
        assert captured.get("allow_network") is True

    def test_verification_node_preserves_default_when_offline(self):
        """allow_network=False → gate_fn called with default (False)."""
        from src.orchestrator.verification_node import verification_node
        from src.schemas.task import TaskState
        from src.schemas.verification import VerificationReport

        captured: dict = {}

        def fake_gate(artifact, *, allow_network=False):
            captured["allow_network"] = allow_network
            return VerificationReport(
                capability_name=artifact.capability_name,
                findings=[],
                overall_passed=True,
                tier_reached=3,
            )

        from src.schemas.capability import CapabilityType
        from src.schemas.task import GeneratedArtifact

        artifact = GeneratedArtifact(
            capability_name="offline-tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code="def f(): return 1",
        )
        state = TaskState(
            task_description="x",
            generated_artifacts=[artifact],
            allow_network=False,
        )
        verification_node(state, gate_fn=fake_gate)
        assert captured.get("allow_network") is False


class TestCompositionalExecutorThreadsAllowNetwork:
    """CompositionalExecutor(allow_network=True) must pass the policy into
    its per-step executors.
    """

    def test_allow_network_attribute_preserved(self):
        from src.orchestrator.composition import CompositionalExecutor

        executor = CompositionalExecutor(
            subtasks=[],
            resolution={},
            artifacts=[],
            matches=[],
            fixture_paths=[],
            task=None,
            allow_network=True,
        )
        assert executor.allow_network is True

    def test_allow_network_default_false(self):
        from src.orchestrator.composition import CompositionalExecutor

        executor = CompositionalExecutor(
            subtasks=[],
            resolution={},
            artifacts=[],
            matches=[],
            fixture_paths=[],
            task=None,
        )
        assert executor.allow_network is False


class TestGeneratedArtifactNeedsNetworkField:
    """The new ``needs_network`` field defaults False and round-trips."""

    def test_default_false(self):
        from src.schemas.capability import CapabilityType
        from src.schemas.task import GeneratedArtifact

        artifact = GeneratedArtifact(
            capability_name="t",
            capability_type=CapabilityType.MCP_TOOL,
        )
        assert artifact.needs_network is False

    def test_set_true(self):
        from src.schemas.capability import CapabilityType
        from src.schemas.task import GeneratedArtifact

        artifact = GeneratedArtifact(
            capability_name="t",
            capability_type=CapabilityType.MCP_TOOL,
            needs_network=True,
        )
        assert artifact.needs_network is True


# ---------------------------------------------------------------------------
# Step 14.12 Part C — precedence tests for the CLI flag vs. persistent setting
# ---------------------------------------------------------------------------


def _invoke_run_capture_allow_network(
    tmp_path,
    monkeypatch,
    args: list[str],
    persistent: bool | None = None,
):
    """Invoke ``mint run`` with controlled settings and capture what lands
    in ``execute_task(allow_network=...)``.

    Returns ``(exit_code, captured_allow_network, captured_source)``.
    """
    import json as _json

    from typer.testing import CliRunner

    from src.cli import app as app_module

    # Redirect settings path.
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(
        app_module, "_settings_path", lambda: settings_path,
    )
    if persistent is not None:
        settings_path.write_text(
            _json.dumps({"network": {"allow": persistent}}),
        )

    captured: dict = {}

    def _fake_execute_task(task, **kwargs):
        captured["allow_network"] = kwargs.get("allow_network")
        captured["allow_network_source"] = kwargs.get("allow_network_source")
        return 0

    # Patch execute_task at its import site inside runner.
    monkeypatch.setattr(
        "src.cli.runner.execute_task", _fake_execute_task, raising=False,
    )
    # app.py does `from src.cli.runner import execute_task` inside the run
    # command, so the attribute on the runner module is what matters.

    runner = CliRunner()
    result = runner.invoke(app_module.app, ["run", *args])
    return result.exit_code, captured.get("allow_network"), captured.get("allow_network_source")


class TestRunCommandNetworkPrecedence:
    """Per-invocation flag > persistent setting > default (False)."""

    def test_default_when_neither_flag_nor_setting_present(self, tmp_path, monkeypatch):
        exit_code, allow, source = _invoke_run_capture_allow_network(
            tmp_path, monkeypatch, args=["some task"],
        )
        assert exit_code == 0
        assert allow is False
        assert source is None

    def test_allow_network_flag_overrides_persistent_false(self, tmp_path, monkeypatch):
        exit_code, allow, source = _invoke_run_capture_allow_network(
            tmp_path, monkeypatch,
            args=["some task", "--allow-network"],
            persistent=False,
        )
        assert exit_code == 0
        assert allow is True
        assert source is not None and "flag" in source

    def test_no_allow_network_flag_overrides_persistent_true(self, tmp_path, monkeypatch):
        exit_code, allow, source = _invoke_run_capture_allow_network(
            tmp_path, monkeypatch,
            args=["some task", "--no-allow-network"],
            persistent=True,
        )
        assert exit_code == 0
        assert allow is False
        assert source is None

    def test_persistent_true_is_used_when_no_flag(self, tmp_path, monkeypatch):
        exit_code, allow, source = _invoke_run_capture_allow_network(
            tmp_path, monkeypatch,
            args=["some task"],
            persistent=True,
        )
        assert exit_code == 0
        assert allow is True
        assert source is not None and "persistent" in source

    def test_both_flags_simultaneously_rejected(self, tmp_path, monkeypatch):
        exit_code, _, _ = _invoke_run_capture_allow_network(
            tmp_path, monkeypatch,
            args=["some task", "--allow-network", "--no-allow-network"],
        )
        assert exit_code == 1
