"""Security audit finding #10 (2026-04-19) — CLI exit-code discrimination.

Pre-fix, ``src/cli/runner.py::execute_task`` returned 0 on the path
``matches and not gaps``, regardless of whether execution was attempted
or succeeded. Scripted callers that checked ``$?`` saw "task passed"
for runs that produced no output (Docker-down, AGENT timeout,
sandbox OOM, Issue #9 validation crash, etc.). Post-fix distinguishes:

- artifact executed with output → 0 (real success)
- artifact has payload but execution failed → 6 (new exit code)
- artifact without payload (seeded descriptor) → 0 (genuine match)
- no artifact at all → 1 (pipeline failure)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _fake_final_state(artifacts, matches=None, gaps=None):
    """Minimal pipeline state shape consumed by execute_task."""
    return {
        "generated_artifacts": artifacts,
        "matches": matches or [],
        "gaps": gaps or [],
        "subtasks": [],
        "classified_gaps": [],
        "required_capabilities": [],
        "verification_results": [],
        "decomposition_succeeded": True,
        "classification_succeeded": True,
        "verification_succeeded": True,
        "output_contract": None,
        "allow_network": False,
    }


def _run_execute_task(monkeypatch, artifacts, matches, exec_output, cap_type=None):
    """Invoke execute_task with stubbed infra and return the exit code."""
    from src.cli import runner as runner_module
    from src.schemas.capability import CapabilityType

    cap_type = cap_type or CapabilityType.MCP_TOOL

    compiled = MagicMock()
    compiled.invoke.side_effect = lambda state, config=None: _fake_final_state(
        artifacts, matches
    )
    compiled.stream.side_effect = lambda state, config=None: iter(
        [{"loading_node": _fake_final_state(artifacts, matches)}]
    )
    pipeline = MagicMock()
    pipeline.compile.return_value = compiled

    registry = MagicMock()
    loader = MagicMock()
    metrics = MagicMock()
    metrics.invocation_count_total.return_value = 0

    monkeypatch.setattr(runner_module, "_check_docker", lambda: True)
    monkeypatch.setattr(runner_module, "_create_infra", lambda *, seed=True: (
        registry, loader, metrics
    ))
    monkeypatch.setattr(
        "src.orchestrator.pipeline.create_pipeline",
        lambda *a, **kw: pipeline,
    )
    # Stub out the three executors — return whatever exec_output the test
    # wants, regardless of artifact type (keeps the test focused on the
    # exit-code discrimination, not the executor internals).
    monkeypatch.setattr(
        runner_module, "_execute_mcp_tool", lambda *a, **kw: exec_output
    )
    monkeypatch.setattr(
        runner_module, "_execute_skill_or_agent", lambda *a, **kw: exec_output
    )
    # Suppress side-effect writes.
    monkeypatch.setattr(runner_module, "_save_output", lambda *a, **kw: None)
    monkeypatch.setattr(runner_module, "_record_invocation", lambda *a, **kw: None)

    return runner_module.execute_task(
        "test task",
        json_output=True,
    )


class TestCliExitCodeDiscrimination:
    def test_artifact_with_output_returns_0(self, monkeypatch, tmp_path):
        from src.schemas.capability import CapabilityType
        from src.schemas.task import GeneratedArtifact

        artifact = GeneratedArtifact(
            capability_name="happy-tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code="async def happy_tool(): return {'ok': True}",
        )
        exit_code = _run_execute_task(
            monkeypatch,
            artifacts=[artifact],
            matches=[],
            exec_output={"ok": True},
        )
        assert exit_code == 0

    def test_artifact_with_payload_but_no_output_returns_6(self, monkeypatch, tmp_path):
        """The core security audit #10 fix: pre-fix returned 0, post-fix
        must return 6 for "execution failed after pipeline success"."""
        from src.schemas.capability import CapabilityType
        from src.schemas.task import GeneratedArtifact

        artifact = GeneratedArtifact(
            capability_name="broken-tool",
            capability_type=CapabilityType.MCP_TOOL,
            # Has payload — the executor COULD run something but returned None.
            source_code="async def broken_tool(): raise RuntimeError('kaboom')",
        )
        exit_code = _run_execute_task(
            monkeypatch,
            artifacts=[artifact],
            matches=[MagicMock()],  # matches also populated (common shape)
            exec_output=None,  # executor returned nothing (crashed + caught)
        )
        assert exit_code == 6, (
            f"expected 6 (execution failure after successful pipeline), "
            f"got {exit_code}. Security audit finding #10 regression."
        )

    def test_artifact_without_payload_returns_0(self, monkeypatch, tmp_path):
        """Genuine seeded-descriptor case: the registry surfaced a match
        that has no source_code / skill_content / agent_config attached."""
        from src.schemas.capability import CapabilityType
        from src.schemas.task import GeneratedArtifact

        artifact = GeneratedArtifact(
            capability_name="seeded-shell",
            capability_type=CapabilityType.MCP_TOOL,
            # No payload — nothing to execute; match reported but no run.
            source_code=None,
            skill_content=None,
            agent_config=None,
        )
        exit_code = _run_execute_task(
            monkeypatch,
            artifacts=[artifact],
            matches=[MagicMock()],
            exec_output=None,
        )
        assert exit_code == 0

    def test_no_artifact_no_matches_returns_1(self, monkeypatch, tmp_path):
        """Total pipeline failure — no artifacts generated, no matches found."""
        exit_code = _run_execute_task(
            monkeypatch,
            artifacts=[],
            matches=[],
            exec_output=None,
        )
        assert exit_code == 1
