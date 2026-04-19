"""Unit tests for src/orchestrator/composition.py — CompositionalExecutor."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.orchestrator.composition import (
    CompositionalExecutor,
    build_resolution_map,
)
from src.schemas.capability import CapabilityDescriptor, CapabilityType
from src.schemas.task import (
    CapabilityReference,
    ChainResult,
    GeneratedArtifact,
    GapItem,
    MatchItem,
    SubTask,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_subtask(
    description: str,
    capabilities: list[str],
    depends_on: list[int] | None = None,
) -> SubTask:
    return SubTask(
        description=description,
        required_capabilities=capabilities,
        depends_on=depends_on or [],
    )


def _make_artifact(
    name: str,
    cap_type: CapabilityType,
    gap_query: str | None = None,
    source_code: str | None = None,
    skill_content: str | None = None,
    agent_config: dict | None = None,
) -> GeneratedArtifact:
    return GeneratedArtifact(
        capability_name=name,
        capability_type=cap_type,
        gap_query=gap_query,
        source_code=source_code,
        skill_content=skill_content,
        agent_config=agent_config,
        generated_at=datetime.now(UTC),
    )


def _make_match(name: str, cap_type: CapabilityType, score: float = 0.9) -> MatchItem:
    return MatchItem(
        capability=CapabilityDescriptor(
            name=name,
            description=f"Registry capability {name}",
            capability_type=cap_type,
            metadata={"source_code": f"async def {name.replace('-', '_')}(): return {{}}"},
        ),
        score=score,
    )


def _make_task(
    task_id: str = "T99",
    fixture_paths: list[str] | None = None,
    task_prompt: str = "test task",
) -> SimpleNamespace:
    return SimpleNamespace(
        task_id=task_id,
        fixture_paths=fixture_paths or [],
        task_prompt=task_prompt,
    )


# ---------------------------------------------------------------------------
# build_resolution_map
# ---------------------------------------------------------------------------


class TestBuildResolutionMap:
    def test_registry_match_preferred_over_generated(self):
        match = _make_match("csv-parser", CapabilityType.MCP_TOOL)
        art = _make_artifact("csv-parser-gen", CapabilityType.MCP_TOOL, gap_query="csv-parser")
        res = build_resolution_map(["csv-parser"], [match], [art])
        assert res["csv-parser"].source == "registry"
        assert res["csv-parser"].capability_name == "csv-parser"

    def test_generated_artifact_used_when_no_match(self):
        art = _make_artifact("enricher", CapabilityType.MCP_TOOL, gap_query="enrich-records")
        res = build_resolution_map(["enrich-records"], [], [art])
        assert res["enrich-records"].source == "generated"
        assert res["enrich-records"].capability_name == "enricher"

    def test_unresolved_capability_missing_from_map(self):
        res = build_resolution_map(["unknown-cap"], [], [])
        assert "unknown-cap" not in res

    def test_multiple_capabilities_resolved(self):
        match = _make_match("parser", CapabilityType.MCP_TOOL)
        art = _make_artifact("aggregator", CapabilityType.SKILL, gap_query="aggregate-data")
        res = build_resolution_map(["parser", "aggregate-data"], [match], [art])
        assert len(res) == 2
        assert res["parser"].source == "registry"
        assert res["aggregate-data"].source == "generated"


# ---------------------------------------------------------------------------
# DAG / execution order
# ---------------------------------------------------------------------------


class TestBuildExecutionOrder:
    def _executor(self, subtasks: list[SubTask]) -> CompositionalExecutor:
        return CompositionalExecutor(
            subtasks=subtasks,
            resolution={},
            artifacts=[],
            matches=[],
            fixture_paths=[],
            task=_make_task(),
        )

    def test_single_step(self):
        ex = self._executor([_make_subtask("only step", ["cap"])])
        assert ex.build_execution_order() == [0]

    def test_empty(self):
        ex = self._executor([])
        assert ex.build_execution_order() == []

    def test_linear_chain(self):
        subtasks = [
            _make_subtask("step 0", ["a"]),
            _make_subtask("step 1", ["b"], depends_on=[0]),
            _make_subtask("step 2", ["c"], depends_on=[1]),
        ]
        ex = self._executor(subtasks)
        order = ex.build_execution_order()
        assert order.index(0) < order.index(1) < order.index(2)

    def test_diamond_dependency(self):
        subtasks = [
            _make_subtask("step 0", ["a"]),
            _make_subtask("step 1", ["b"], depends_on=[0]),
            _make_subtask("step 2", ["c"], depends_on=[0]),
            _make_subtask("step 3", ["d"], depends_on=[1, 2]),
        ]
        ex = self._executor(subtasks)
        order = ex.build_execution_order()
        assert order.index(0) < order.index(1)
        assert order.index(0) < order.index(2)
        assert order.index(1) < order.index(3)
        assert order.index(2) < order.index(3)

    def test_cycle_falls_back_to_linear(self):
        subtasks = [
            _make_subtask("step 0", ["a"], depends_on=[1]),
            _make_subtask("step 1", ["b"], depends_on=[0]),
        ]
        ex = self._executor(subtasks)
        order = ex.build_execution_order()
        assert order == [0, 1]

    def test_no_dependencies_preserves_all(self):
        subtasks = [
            _make_subtask("step 0", ["a"]),
            _make_subtask("step 1", ["b"]),
            _make_subtask("step 2", ["c"]),
        ]
        ex = self._executor(subtasks)
        order = ex.build_execution_order()
        assert set(order) == {0, 1, 2}


# ---------------------------------------------------------------------------
# Workspace management
# ---------------------------------------------------------------------------


class TestWorkspace:
    def test_workspace_created_with_fixtures(self, tmp_path):
        fixture_file = tmp_path / "data.csv"
        fixture_file.write_text("col1,col2\n1,2\n")

        ex = CompositionalExecutor(
            subtasks=[],
            resolution={},
            artifacts=[],
            matches=[],
            fixture_paths=[fixture_file],
            task=_make_task(),
        )

        workspace = ex.build_workspace("test123")
        try:
            assert workspace.exists()
            assert (workspace / "fixtures").is_dir()
            assert (workspace / "fixtures" / "data.csv").read_text() == "col1,col2\n1,2\n"
        finally:
            ex.cleanup_workspace(workspace)
            assert not workspace.exists()

    def test_workspace_prefix_contains_task_id(self):
        ex = CompositionalExecutor(
            subtasks=[], resolution={}, artifacts=[], matches=[],
            fixture_paths=[], task=_make_task(),
        )
        workspace = ex.build_workspace("myid")
        try:
            assert "mint_workspace_myid_" in workspace.name
        finally:
            ex.cleanup_workspace(workspace)

    def test_step_output_saved_and_readable(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()

        CompositionalExecutor._save_step_output(workspace, 0, {"key": "value"})
        output_file = workspace / "step_0_output.json"
        assert output_file.exists()
        assert json.loads(output_file.read_text()) == {"key": "value"}

    def test_chain_fixture_contents_includes_upstream(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        fixtures_dir = workspace / "fixtures"
        fixtures_dir.mkdir()
        (fixtures_dir / "data.csv").write_text("a,b\n1,2")

        upstream = {0: {"parsed": [1, 2]}, 1: {"enriched": True}}
        contents = CompositionalExecutor._build_chain_fixture_contents(workspace, upstream)

        assert "data.csv" in contents
        assert contents["data.csv"] == "a,b\n1,2"
        assert "step_0_output.json" in contents
        assert json.loads(contents["step_0_output.json"]) == {"parsed": [1, 2]}
        assert "step_1_output.json" in contents


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------


class TestResolveStep:
    def test_resolves_generated_artifact(self):
        art = _make_artifact("enricher", CapabilityType.MCP_TOOL, gap_query="enrich")
        ex = CompositionalExecutor(
            subtasks=[_make_subtask("step", ["enrich"])],
            resolution={"enrich": CapabilityReference(
                source="generated", capability_name="enricher",
                capability_type=CapabilityType.MCP_TOOL,
            )},
            artifacts=[art],
            matches=[],
            fixture_paths=[],
            task=_make_task(),
        )
        ref, resolved = ex._resolve_step(ex.subtasks[0])
        assert ref is not None
        assert ref.source == "generated"
        assert resolved is art

    def test_resolves_registry_match(self):
        match = _make_match("csv-parser", CapabilityType.MCP_TOOL)
        ex = CompositionalExecutor(
            subtasks=[_make_subtask("step", ["csv-parser"])],
            resolution={"csv-parser": CapabilityReference(
                source="registry", capability_name="csv-parser",
                capability_type=CapabilityType.MCP_TOOL, score=0.95,
            )},
            artifacts=[],
            matches=[match],
            fixture_paths=[],
            task=_make_task(),
        )
        ref, resolved = ex._resolve_step(ex.subtasks[0])
        assert ref is not None
        assert ref.source == "registry"
        assert resolved is match

    def test_returns_none_for_unresolved(self):
        ex = CompositionalExecutor(
            subtasks=[_make_subtask("step", ["unknown"])],
            resolution={},
            artifacts=[],
            matches=[],
            fixture_paths=[],
            task=_make_task(),
        )
        ref, resolved = ex._resolve_step(ex.subtasks[0])
        assert ref is None
        assert resolved is None

    def test_resolves_by_capability_name_fallback(self):
        """When gap_query doesn't match, fall back to capability_name match."""
        art = _make_artifact("enricher", CapabilityType.MCP_TOOL, gap_query="other-query")
        ex = CompositionalExecutor(
            subtasks=[_make_subtask("step", ["enrich"])],
            resolution={"enrich": CapabilityReference(
                source="generated", capability_name="enricher",
                capability_type=CapabilityType.MCP_TOOL,
            )},
            artifacts=[art],
            matches=[],
            fixture_paths=[],
            task=_make_task(),
        )
        ref, resolved = ex._resolve_step(ex.subtasks[0])
        assert ref is not None
        assert resolved is art


# ---------------------------------------------------------------------------
# Chain execution (mocked backends)
# ---------------------------------------------------------------------------


class TestChainExecution:
    def _make_chain_executor(
        self,
        subtasks: list[SubTask],
        resolution: dict[str, CapabilityReference],
        artifacts: list[GeneratedArtifact],
        matches: list[MatchItem] | None = None,
        fixture_paths: list[str | Path] | None = None,
    ) -> CompositionalExecutor:
        return CompositionalExecutor(
            subtasks=subtasks,
            resolution=resolution,
            artifacts=artifacts,
            matches=matches or [],
            fixture_paths=fixture_paths or [],
            task=_make_task(),
        )

    @patch("tests.evaluation.execution.execute_mcp_tool_in_chain")
    def test_single_mcp_tool_chain(self, mock_exec):
        mock_exec.return_value = {"result": 42}

        art = _make_artifact(
            "parser", CapabilityType.MCP_TOOL,
            gap_query="parse-csv",
            source_code="async def parser(): return {'result': 42}",
        )
        ex = self._make_chain_executor(
            subtasks=[_make_subtask("parse", ["parse-csv"])],
            resolution={"parse-csv": CapabilityReference(
                source="generated", capability_name="parser",
                capability_type=CapabilityType.MCP_TOOL,
            )},
            artifacts=[art],
        )

        chain_result = ex.run()
        assert chain_result.chain_success is True
        assert chain_result.chain_length == 1
        assert chain_result.step_results == [{"result": 42}]
        assert chain_result.failed_step_index is None
        mock_exec.assert_called_once()

    @patch("tests.evaluation.execution.execute_mcp_tool_in_chain")
    def test_linear_mcp_tool_chain(self, mock_exec):
        """Three MCP_TOOL steps in sequence — verify workspace state passing."""
        mock_exec.side_effect = [
            {"parsed": [1, 2, 3]},
            {"enriched": [10, 20, 30]},
            {"aggregated": 60},
        ]

        arts = [
            _make_artifact("parser", CapabilityType.MCP_TOOL, gap_query="parse",
                           source_code="async def parser(): pass"),
            _make_artifact("enricher", CapabilityType.MCP_TOOL, gap_query="enrich",
                           source_code="async def enricher(): pass"),
            _make_artifact("aggregator", CapabilityType.MCP_TOOL, gap_query="aggregate",
                           source_code="async def aggregator(): pass"),
        ]

        subtasks = [
            _make_subtask("parse", ["parse"]),
            _make_subtask("enrich", ["enrich"], depends_on=[0]),
            _make_subtask("aggregate", ["aggregate"], depends_on=[1]),
        ]

        resolution = {
            "parse": CapabilityReference(
                source="generated", capability_name="parser",
                capability_type=CapabilityType.MCP_TOOL,
            ),
            "enrich": CapabilityReference(
                source="generated", capability_name="enricher",
                capability_type=CapabilityType.MCP_TOOL,
            ),
            "aggregate": CapabilityReference(
                source="generated", capability_name="aggregator",
                capability_type=CapabilityType.MCP_TOOL,
            ),
        }

        ex = self._make_chain_executor(subtasks=subtasks, resolution=resolution, artifacts=arts)
        chain_result = ex.run()

        assert chain_result.chain_success is True
        assert chain_result.chain_length == 3
        assert len(chain_result.step_results) == 3
        assert chain_result.step_results[-1] == {"aggregated": 60}

    @patch("tests.evaluation.execution.execute_mcp_tool_in_chain")
    def test_mid_chain_failure(self, mock_exec):
        """Step 1 returns empty dict → chain fails at step 1."""
        mock_exec.side_effect = [{"parsed": True}, {}]

        arts = [
            _make_artifact("parser", CapabilityType.MCP_TOOL, gap_query="parse",
                           source_code="async def parser(): pass"),
            _make_artifact("enricher", CapabilityType.MCP_TOOL, gap_query="enrich",
                           source_code="async def enricher(): pass"),
        ]

        subtasks = [
            _make_subtask("parse", ["parse"]),
            _make_subtask("enrich", ["enrich"], depends_on=[0]),
        ]

        resolution = {
            "parse": CapabilityReference(
                source="generated", capability_name="parser",
                capability_type=CapabilityType.MCP_TOOL,
            ),
            "enrich": CapabilityReference(
                source="generated", capability_name="enricher",
                capability_type=CapabilityType.MCP_TOOL,
            ),
        }

        ex = self._make_chain_executor(subtasks=subtasks, resolution=resolution, artifacts=arts)
        chain_result = ex.run()

        assert chain_result.chain_success is False
        assert chain_result.chain_length == 2
        assert chain_result.failed_step_index == 1
        assert chain_result.failed_step_name == "enrich"
        assert len(chain_result.step_results) == 2

    def test_unresolved_capability_fails_chain(self):
        """Step with no resolution → chain fails immediately."""
        subtasks = [_make_subtask("step", ["missing-cap"])]
        ex = self._make_chain_executor(
            subtasks=subtasks, resolution={}, artifacts=[],
        )
        chain_result = ex.run()
        assert chain_result.chain_success is False
        assert chain_result.failed_step_index == 0

    @patch("tests.evaluation.execution.execute_skill_artifact")
    @patch("tests.evaluation.execution.execute_mcp_tool_in_chain")
    def test_mixed_type_chain(self, mock_mcp, mock_skill):
        """MCP_TOOL → SKILL chain — HP4 scenario."""
        mock_mcp.return_value = {"parsed_data": [1, 2, 3]}
        mock_skill.return_value = {"analysis": "done", "score": 0.95}

        arts = [
            _make_artifact("parser", CapabilityType.MCP_TOOL, gap_query="parse",
                           source_code="async def parser(): pass"),
            _make_artifact("analyzer", CapabilityType.SKILL, gap_query="analyze",
                           skill_content="# Analyze data"),
        ]

        subtasks = [
            _make_subtask("parse CSV", ["parse"]),
            _make_subtask("analyze data", ["analyze"], depends_on=[0]),
        ]

        resolution = {
            "parse": CapabilityReference(
                source="generated", capability_name="parser",
                capability_type=CapabilityType.MCP_TOOL,
            ),
            "analyze": CapabilityReference(
                source="generated", capability_name="analyzer",
                capability_type=CapabilityType.SKILL,
            ),
        }

        ex = self._make_chain_executor(subtasks=subtasks, resolution=resolution, artifacts=arts)
        chain_result = ex.run()

        assert chain_result.chain_success is True
        assert chain_result.chain_length == 2

        # Verify the SKILL step received upstream output via fixture_contents
        mock_skill.assert_called_once()
        call_kwargs = mock_skill.call_args
        fixture_contents = call_kwargs[0][2]  # third positional arg
        assert "step_0_output.json" in fixture_contents

    @patch("tests.evaluation.execution.execute_agent_artifact")
    @patch("tests.evaluation.execution.execute_mcp_tool_in_chain")
    def test_mcp_tool_to_agent_chain(self, mock_mcp, mock_agent):
        """MCP_TOOL → AGENT chain — verifies fixture injection for agents."""
        mock_mcp.return_value = {"data": [1, 2]}
        mock_agent.return_value = {"investigation": "complete"}

        arts = [
            _make_artifact("loader", CapabilityType.MCP_TOOL, gap_query="load",
                           source_code="async def loader(): pass"),
            _make_artifact("investigator", CapabilityType.AGENT, gap_query="investigate",
                           agent_config={"system_prompt": "Investigate"}),
        ]

        subtasks = [
            _make_subtask("load data", ["load"]),
            _make_subtask("investigate", ["investigate"], depends_on=[0]),
        ]

        resolution = {
            "load": CapabilityReference(
                source="generated", capability_name="loader",
                capability_type=CapabilityType.MCP_TOOL,
            ),
            "investigate": CapabilityReference(
                source="generated", capability_name="investigator",
                capability_type=CapabilityType.AGENT,
            ),
        }

        ex = self._make_chain_executor(subtasks=subtasks, resolution=resolution, artifacts=arts)
        chain_result = ex.run()

        assert chain_result.chain_success is True

        # Agent received upstream data via fixture_contents
        mock_agent.assert_called_once()
        fixture_contents = mock_agent.call_args[0][2]
        assert "step_0_output.json" in fixture_contents

    @patch("tests.evaluation.execution.execute_skill_artifact")
    @patch("tests.evaluation.execution.execute_mcp_tool_in_chain")
    def test_per_type_fixture_routing_mcp_vs_skill(self, mock_mcp, mock_skill):
        """MCP_TOOL steps receive a filesystem workspace Path; SKILL/AGENT
        steps receive a ``{filename: content}`` dict. A regression that
        blurred this boundary (e.g. handed MCP_TOOL a fixture_contents dict
        or SKILL a raw Path) would silently break fixture access without
        tripping any of the happy-path chain tests. Phase 3.5c established
        this routing; keep it guarded.
        """
        mock_mcp.return_value = {"data": [1, 2, 3]}
        mock_skill.return_value = {"analysis": "done"}

        arts = [
            _make_artifact("loader", CapabilityType.MCP_TOOL, gap_query="load",
                           source_code="async def loader(): pass"),
            _make_artifact("analyzer", CapabilityType.SKILL, gap_query="analyze",
                           skill_content="# Analyze"),
        ]
        subtasks = [
            _make_subtask("load", ["load"]),
            _make_subtask("analyze", ["analyze"], depends_on=[0]),
        ]
        resolution = {
            "load": CapabilityReference(
                source="generated", capability_name="loader",
                capability_type=CapabilityType.MCP_TOOL,
            ),
            "analyze": CapabilityReference(
                source="generated", capability_name="analyzer",
                capability_type=CapabilityType.SKILL,
            ),
        }

        ex = self._make_chain_executor(subtasks=subtasks, resolution=resolution, artifacts=arts)
        chain_result = ex.run()
        assert chain_result.chain_success is True

        # MCP_TOOL backend: workspace_path kwarg is a Path; no fixture_contents param exists.
        mock_mcp.assert_called_once()
        mcp_kwargs = mock_mcp.call_args.kwargs
        assert "workspace_path" in mcp_kwargs
        assert isinstance(mcp_kwargs["workspace_path"], Path)
        assert "fixture_contents" not in mcp_kwargs
        # Positional args carry no dict either.
        for arg in mock_mcp.call_args.args:
            assert not isinstance(arg, dict), (
                "MCP_TOOL step received a dict — should only get workspace Path + source code"
            )

        # SKILL backend: third positional arg is the fixture_contents dict.
        mock_skill.assert_called_once()
        skill_fixture_contents = mock_skill.call_args[0][2]
        assert isinstance(skill_fixture_contents, dict)
        assert "step_0_output.json" in skill_fixture_contents

    @patch("tests.evaluation.execution.execute_mcp_tool_in_chain")
    def test_registry_match_in_chain(self, mock_exec):
        """Registry-matched capability used in a chain step."""
        mock_exec.return_value = {"parsed": True}

        match = _make_match("csv-parser", CapabilityType.MCP_TOOL)

        subtasks = [_make_subtask("parse", ["csv-parser"])]
        resolution = {
            "csv-parser": CapabilityReference(
                source="registry", capability_name="csv-parser",
                capability_type=CapabilityType.MCP_TOOL, score=0.95,
            ),
        }

        ex = self._make_chain_executor(
            subtasks=subtasks, resolution=resolution,
            artifacts=[], matches=[match],
        )
        chain_result = ex.run()
        assert chain_result.chain_success is True

    def test_empty_subtasks_returns_failed(self):
        ex = self._make_chain_executor(subtasks=[], resolution={}, artifacts=[])
        chain_result = ex.run()
        assert chain_result.chain_success is False
        assert chain_result.chain_length == 1  # min 1 per schema constraint

    @patch("tests.evaluation.execution.execute_mcp_tool_in_chain")
    def test_workspace_cleanup_on_success(self, mock_exec, tmp_path):
        """Workspace is cleaned up even on successful execution."""
        mock_exec.return_value = {"ok": True}

        art = _make_artifact("tool", CapabilityType.MCP_TOOL, gap_query="do-thing",
                             source_code="async def tool(): pass")

        ex = self._make_chain_executor(
            subtasks=[_make_subtask("do thing", ["do-thing"])],
            resolution={"do-thing": CapabilityReference(
                source="generated", capability_name="tool",
                capability_type=CapabilityType.MCP_TOOL,
            )},
            artifacts=[art],
        )

        # Patch build_workspace to use a known path we can check
        known_ws = tmp_path / "test_ws"
        known_ws.mkdir()
        (known_ws / "fixtures").mkdir()

        with patch.object(ex, "build_workspace", return_value=known_ws):
            ex.run()

        assert not known_ws.exists()

    @patch("tests.evaluation.execution.execute_mcp_tool_in_chain")
    def test_workspace_cleanup_on_failure(self, mock_exec, tmp_path):
        """Workspace is cleaned up even when the chain fails."""
        mock_exec.return_value = {}  # empty = failure

        art = _make_artifact("tool", CapabilityType.MCP_TOOL, gap_query="do-thing",
                             source_code="async def tool(): pass")

        ex = self._make_chain_executor(
            subtasks=[_make_subtask("do thing", ["do-thing"])],
            resolution={"do-thing": CapabilityReference(
                source="generated", capability_name="tool",
                capability_type=CapabilityType.MCP_TOOL,
            )},
            artifacts=[art],
        )

        known_ws = tmp_path / "test_ws"
        known_ws.mkdir()
        (known_ws / "fixtures").mkdir()

        with patch.object(ex, "build_workspace", return_value=known_ws):
            result = ex.run()

        assert result.chain_success is False
        assert not known_ws.exists()


    # --- Step 13: agent execution timeout/error propagation -----------

    @patch("tests.evaluation.execution.execute_agent_artifact")
    def test_agent_timeout_populates_failed_step_reason(self, mock_agent):
        """An AGENT timeout must surface as ``failed_step_reason`` on the
        ChainResult so the harness can classify it separately from a
        generic chain-step failure."""
        from tests.evaluation.execution import (
            _record_agent_execution_meta,
            clear_last_agent_execution_meta,
        )

        def _fake_agent(*args, **kwargs):
            _record_agent_execution_meta(
                succeeded=False, failure_reason="timeout", timeout_seconds=900,
            )
            return {}

        mock_agent.side_effect = _fake_agent
        clear_last_agent_execution_meta()

        art = _make_artifact(
            "investigator", CapabilityType.AGENT, gap_query="investigate",
            agent_config={"system_prompt": "Investigate"},
        )
        subtasks = [_make_subtask("investigate", ["investigate"])]
        resolution = {
            "investigate": CapabilityReference(
                source="generated", capability_name="investigator",
                capability_type=CapabilityType.AGENT,
            ),
        }

        ex = self._make_chain_executor(
            subtasks=subtasks, resolution=resolution, artifacts=[art],
        )
        result = ex.run()

        assert result.chain_success is False
        assert result.failed_step_index == 0
        assert result.failed_step_reason == "agent_execution_timeout"

    @patch("tests.evaluation.execution.execute_agent_artifact")
    def test_agent_error_populates_failed_step_reason(self, mock_agent):
        from tests.evaluation.execution import (
            _record_agent_execution_meta,
            clear_last_agent_execution_meta,
        )

        def _fake_agent(*args, **kwargs):
            _record_agent_execution_meta(succeeded=False, failure_reason="error")
            return {}

        mock_agent.side_effect = _fake_agent
        clear_last_agent_execution_meta()

        art = _make_artifact(
            "investigator", CapabilityType.AGENT, gap_query="investigate",
            agent_config={"system_prompt": "Investigate"},
        )
        ex = self._make_chain_executor(
            subtasks=[_make_subtask("investigate", ["investigate"])],
            resolution={
                "investigate": CapabilityReference(
                    source="generated", capability_name="investigator",
                    capability_type=CapabilityType.AGENT,
                ),
            },
            artifacts=[art],
        )
        result = ex.run()

        assert result.chain_success is False
        assert result.failed_step_reason == "agent_execution_error"

    @patch("tests.evaluation.execution.execute_agent_artifact")
    def test_agent_success_leaves_failed_step_reason_none(self, mock_agent):
        from tests.evaluation.execution import (
            _record_agent_execution_meta,
            clear_last_agent_execution_meta,
        )

        def _fake_agent(*args, **kwargs):
            _record_agent_execution_meta(succeeded=True)
            return {"investigation": "done"}

        mock_agent.side_effect = _fake_agent
        clear_last_agent_execution_meta()

        art = _make_artifact(
            "investigator", CapabilityType.AGENT, gap_query="investigate",
            agent_config={"system_prompt": "Investigate"},
        )
        ex = self._make_chain_executor(
            subtasks=[_make_subtask("investigate", ["investigate"])],
            resolution={
                "investigate": CapabilityReference(
                    source="generated", capability_name="investigator",
                    capability_type=CapabilityType.AGENT,
                ),
            },
            artifacts=[art],
        )
        result = ex.run()

        assert result.chain_success is True
        assert result.failed_step_reason is None

    @patch("tests.evaluation.execution.execute_mcp_tool_in_chain")
    def test_non_agent_failure_leaves_reason_none(self, mock_exec):
        """An empty MCP_TOOL step output fails the chain but does NOT set
        ``failed_step_reason`` — the reason slot is reserved for structured
        backend meta, not generic "empty output" outcomes."""
        mock_exec.return_value = {}

        art = _make_artifact(
            "tool", CapabilityType.MCP_TOOL, gap_query="do-thing",
            source_code="async def tool(): pass",
        )
        ex = self._make_chain_executor(
            subtasks=[_make_subtask("do thing", ["do-thing"])],
            resolution={"do-thing": CapabilityReference(
                source="generated", capability_name="tool",
                capability_type=CapabilityType.MCP_TOOL,
            )},
            artifacts=[art],
        )
        result = ex.run()

        assert result.chain_success is False
        assert result.failed_step_reason is None


# ---------------------------------------------------------------------------
# match_to_artifact helper
# ---------------------------------------------------------------------------


class TestMatchToArtifact:
    def test_reconstructs_artifact_from_match(self):
        match = _make_match("csv-parser", CapabilityType.MCP_TOOL)
        ref = CapabilityReference(
            source="registry", capability_name="csv-parser",
            capability_type=CapabilityType.MCP_TOOL, score=0.95,
        )
        art = CompositionalExecutor._match_to_artifact(match, ref)
        assert art.capability_name == "csv-parser"
        assert art.capability_type == CapabilityType.MCP_TOOL
        assert art.source_code is not None
        assert "csv_parser" in art.source_code


# ---------------------------------------------------------------------------
# Phase 3.5b: subagent_gap_queries in resolution map
# ---------------------------------------------------------------------------


class TestResolutionMapSubagentGapQueries:
    def test_lead_artifact_covers_all_gap_queries(self):
        """A lead-with-subagents artifact should resolve all its covered gap queries."""
        lead = _make_artifact(
            "lead-investigator",
            CapabilityType.AGENT,
            gap_query="profile-data",
            agent_config={
                "system_prompt": "lead",
                "is_deep_agent": True,
                "subagents": [{"name": "sub-1"}],
                "subagent_gap_queries": ["profile-data", "detect-anomalies", "verify-fix"],
            },
        )
        resolution = build_resolution_map(
            required_capabilities=["profile-data", "detect-anomalies", "verify-fix"],
            matches=[],
            artifacts=[lead],
        )
        assert len(resolution) == 3
        for cap_name in ["profile-data", "detect-anomalies", "verify-fix"]:
            assert cap_name in resolution
            assert resolution[cap_name].capability_name == "lead-investigator"
            assert resolution[cap_name].source == "generated"

    def test_registry_match_takes_priority_over_subagent_gap(self):
        """Registry matches should take priority even if a subagent covers the gap."""
        lead = _make_artifact(
            "lead-investigator",
            CapabilityType.AGENT,
            gap_query="profile-data",
            agent_config={
                "system_prompt": "lead",
                "subagent_gap_queries": ["profile-data", "detect-anomalies"],
            },
        )
        match = _make_match("detect-anomalies", CapabilityType.MCP_TOOL, score=0.9)
        match.query = "detect-anomalies"
        resolution = build_resolution_map(
            required_capabilities=["profile-data", "detect-anomalies"],
            matches=[match],
            artifacts=[lead],
        )
        # profile-data → lead (no match)
        assert resolution["profile-data"].capability_name == "lead-investigator"
        # detect-anomalies → registry match (higher priority)
        assert resolution["detect-anomalies"].capability_name == "detect-anomalies"
        assert resolution["detect-anomalies"].source == "registry"


class TestLeadDedup:
    """Verify the compositional executor runs a lead-with-subagents artifact only once."""

    def test_lead_executed_once_for_multiple_subtasks(self):
        """When multiple subtasks resolve to the same lead artifact, execute once."""
        lead = _make_artifact(
            "lead-agent",
            CapabilityType.AGENT,
            gap_query="step-a",
            agent_config={
                "system_prompt": "lead",
                "is_deep_agent": True,
                "subagents": [
                    {"name": "sub-1", "description": "d", "system_prompt": "s", "tools": []},
                ],
                "subagent_gap_queries": ["step-a", "step-b"],
            },
        )
        subtasks = [
            _make_subtask("Step A", ["step-a"]),
            _make_subtask("Step B", ["step-b"], depends_on=[0]),
        ]
        resolution = build_resolution_map(
            required_capabilities=["step-a", "step-b"],
            matches=[],
            artifacts=[lead],
        )
        executor = CompositionalExecutor(
            subtasks=subtasks,
            resolution=resolution,
            artifacts=[lead],
            matches=[],
            fixture_paths=[],
            task=_make_task(),
        )

        call_count = 0
        original_execute = executor._execute_step

        def counting_execute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return {"result": "ok"}

        with patch.object(executor, "_execute_step", side_effect=counting_execute):
            result = executor.run()

        # Lead should be executed only once; second subtask reuses cached output
        assert call_count == 1
        assert result.chain_success is True
        assert len(result.step_results) == 2
        assert result.step_results[0] == result.step_results[1]


# ---------------------------------------------------------------------------
# File output support (B3)
# ---------------------------------------------------------------------------


class TestCollectOutputFiles:
    """Tests for _collect_output_files — collecting non-intermediate files from workspace.

    Post-2026-04-16 refinement: the collector is format-agnostic and picks up
    any file at the workspace root that isn't a known intermediate
    (``step_N_output.json``). Previously it only matched stem ``"output"``,
    which silently dropped ``report.md`` / ``summary.txt`` / any non-``output.*``
    filename that the decomposer's Rule 15 writer produced.
    """

    def test_collects_output_csv(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        csv_content = b"a,b,c\n1,2,3\n"
        (ws / "output.csv").write_bytes(csv_content)
        # step_N_output.json intermediates are NOT collected.
        (ws / "step_0_output.json").write_text("{}")
        # Other files ARE now collected (post-2026-04-16 fix).
        (ws / "data.txt").write_text("hello")

        result = CompositionalExecutor._collect_output_files(ws)
        assert result["output.csv"] == csv_content
        assert "data.txt" in result  # no longer filtered out
        assert "step_0_output.json" not in result  # still skipped

    def test_collects_multiple_output_files(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "output.csv").write_bytes(b"a,b\n1,2\n")
        (ws / "output.xlsx").write_bytes(b"\x50\x4b")  # fake xlsx bytes

        result = CompositionalExecutor._collect_output_files(ws)
        assert len(result) == 2
        assert "output.csv" in result
        assert "output.xlsx" in result

    def test_collects_report_md(self, tmp_path):
        """Regression guard for T16 rerun (2026-04-16 15:51).

        The writer MCP_TOOL wrote ``/workspace/report.md`` correctly but the
        previous stem-only filter silently dropped it. Rule 15 writers can
        produce any filename; the collector must honor that.
        """
        ws = tmp_path / "workspace"
        ws.mkdir()
        md_content = b"# Incident Summary\n\n| metric | onset |\n|---|---|\n"
        (ws / "report.md").write_bytes(md_content)

        result = CompositionalExecutor._collect_output_files(ws)
        assert "report.md" in result
        assert result["report.md"] == md_content

    def test_empty_workspace(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        result = CompositionalExecutor._collect_output_files(ws)
        assert result == {}

    def test_step_intermediates_skipped(self, tmp_path):
        """step_N_output.json files are executor intermediates — don't collect."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "step_0_output.json").write_text("{}")
        (ws / "step_1_output.json").write_text("{}")
        (ws / "step_10_output.json").write_text("{}")
        (ws / "result.csv").write_bytes(b"a,b\n")

        result = CompositionalExecutor._collect_output_files(ws)
        # Only the legitimate output file is collected.
        assert result == {"result.csv": b"a,b\n"}

    def test_nonexistent_workspace(self, tmp_path):
        ws = tmp_path / "nonexistent"
        result = CompositionalExecutor._collect_output_files(ws)
        assert result == {}


class TestChainResultOutputFiles:
    """Tests for ChainResult.output_files field."""

    def test_default_empty(self):
        cr = ChainResult(chain_length=1, chain_success=True, step_results=[{"a": 1}])
        assert cr.output_files == {}

    def test_with_files(self):
        cr = ChainResult(
            chain_length=1,
            chain_success=True,
            step_results=[{"output_file": "output.csv"}],
            output_files={"output.csv": b"a,b\n1,2\n"},
        )
        assert cr.output_files == {"output.csv": b"a,b\n1,2\n"}


class TestEmbeddedFileExtraction:
    """Tests for SKILL/AGENT _output_file_content extraction in run()."""

    def test_extracts_text_file_from_last_step(self):
        """When last step dict has _output_file_name + _output_file_content (text),
        it should appear in output_files."""
        art = _make_artifact(
            "reporter", CapabilityType.MCP_TOOL,
            gap_query="report", source_code="async def reporter(): return {}",
        )
        subtask = _make_subtask("report", ["report"])
        resolution = {"report": CapabilityReference(
            source="generated", capability_name="reporter",
            capability_type=CapabilityType.MCP_TOOL,
        )}
        executor = CompositionalExecutor(
            subtasks=[subtask],
            resolution=resolution,
            artifacts=[art],
            matches=[],
            fixture_paths=[],
            task=_make_task(),
        )
        csv_content = "a,b\n1,2\n"
        step_output = {
            "output_file": "output.csv",
            "_output_file_name": "output.csv",
            "_output_file_content": csv_content,
        }

        with patch.object(executor, "_execute_step", return_value=step_output):
            result = executor.run()

        assert result.chain_success is True
        assert "output.csv" in result.output_files
        assert result.output_files["output.csv"] == csv_content.encode("utf-8")

    def test_extracts_base64_binary_file(self):
        """When _output_file_content is base64-encoded, it should be decoded."""
        import base64

        art = _make_artifact(
            "exporter", CapabilityType.MCP_TOOL,
            gap_query="export", source_code="async def exporter(): return {}",
        )
        subtask = _make_subtask("export", ["export"])
        resolution = {"export": CapabilityReference(
            source="generated", capability_name="exporter",
            capability_type=CapabilityType.MCP_TOOL,
        )}
        executor = CompositionalExecutor(
            subtasks=[subtask],
            resolution=resolution,
            artifacts=[art],
            matches=[],
            fixture_paths=[],
            task=_make_task(),
        )
        raw_bytes = b"\x50\x4b\x03\x04binary_content"
        encoded = base64.b64encode(raw_bytes).decode("ascii")
        step_output = {
            "_output_file_name": "output.xlsx",
            "_output_file_content": encoded,
        }

        with patch.object(executor, "_execute_step", return_value=step_output):
            result = executor.run()

        assert result.chain_success is True
        assert result.output_files["output.xlsx"] == raw_bytes

    def test_no_extraction_without_markers(self):
        """Without _output_file_name, no file extraction occurs."""
        art = _make_artifact(
            "plain", CapabilityType.MCP_TOOL,
            gap_query="plain", source_code="async def plain(): return {}",
        )
        subtask = _make_subtask("plain", ["plain"])
        resolution = {"plain": CapabilityReference(
            source="generated", capability_name="plain",
            capability_type=CapabilityType.MCP_TOOL,
        )}
        executor = CompositionalExecutor(
            subtasks=[subtask],
            resolution=resolution,
            artifacts=[art],
            matches=[],
            fixture_paths=[],
            task=_make_task(),
        )

        with patch.object(executor, "_execute_step", return_value={"data": 42}):
            result = executor.run()

        assert result.output_files == {}


# ---------------------------------------------------------------------------
# §12 finding #12 — AGENT/SKILL output_schema runtime compliance check
# ---------------------------------------------------------------------------

# NOTE: TestBuildSchemaContext moved to tests/unit/test_generator_utils.py
# (Batch 02b audit — file-placement fix; build_schema_context lives in
# src/orchestrator/generators/utils.py, not composition.py).


class TestAgentOutputSchemaRuntimeCheck:
    """Runtime validator — Layer 2 of the finding #12 defense.

    Layer 1 (dynamic response_format in execute_agent_artifact) is
    tested in tests/unit/test_execution.py::TestBuildDynamicResultModel
    and TestExecuteAgentArtifactDynamicResponseFormat.
    """

    @patch("tests.evaluation.execution.execute_agent_artifact")
    def test_agent_missing_schema_keys_fails_chain(self, mock_agent):
        """When AGENT output omits declared output_schema keys, chain fails."""
        # AGENT returns task-level keys only — missing `metrics_table`
        # that the decomposer declared. Reproduces the T16 2nd-run failure.
        mock_agent.return_value = {
            "root_cause_metric": "db_connections",
            "cascade_chain": ["a", "b"],
        }

        subtask = SubTask(
            description="Investigate incident and produce metrics_table",
            required_capabilities=["incident-triage"],
            depends_on=[],
            output_schema={
                "metrics_table": "list[dict] — each with metric, onset",
                "root_cause_metric": "str",
                "cascade_chain": "list[str]",
            },
        )
        art = _make_artifact(
            "incident-triage-agent", CapabilityType.AGENT,
            gap_query="incident-triage",
            agent_config={"system_prompt": "investigate"},
        )
        ex = CompositionalExecutor(
            subtasks=[subtask],
            resolution={"incident-triage": CapabilityReference(
                source="generated", capability_name="incident-triage-agent",
                capability_type=CapabilityType.AGENT,
            )},
            artifacts=[art],
            matches=[],
            fixture_paths=[],
            task=_make_task(),
        )

        result = ex.run()
        assert result.chain_success is False
        assert result.failed_step_index == 0
        assert "metrics_table" in (result.failed_step_reason or "")

    @patch("tests.evaluation.execution.execute_agent_artifact")
    def test_agent_full_compliance_passes(self, mock_agent):
        """When AGENT produces all declared keys, chain continues normally."""
        mock_agent.return_value = {
            "metrics_table": [{"metric": "db_connections", "onset": "05:00"}],
            "root_cause_metric": "db_connections",
            "cascade_chain": ["a"],
        }

        subtask = SubTask(
            description="Investigate",
            required_capabilities=["investigate"],
            depends_on=[],
            output_schema={
                "metrics_table": "list[dict]",
                "root_cause_metric": "str",
                "cascade_chain": "list[str]",
            },
        )
        art = _make_artifact(
            "investigator", CapabilityType.AGENT,
            gap_query="investigate",
            agent_config={"system_prompt": "investigate"},
        )
        ex = CompositionalExecutor(
            subtasks=[subtask],
            resolution={"investigate": CapabilityReference(
                source="generated", capability_name="investigator",
                capability_type=CapabilityType.AGENT,
            )},
            artifacts=[art],
            matches=[],
            fixture_paths=[],
            task=_make_task(),
        )

        result = ex.run()
        assert result.chain_success is True
        assert result.failed_step_reason is None

    @patch("tests.evaluation.execution.execute_mcp_tool_in_chain")
    def test_mcp_tool_bypasses_runtime_check(self, mock_mcp):
        """MCP_TOOL output is deterministic — runtime check only fires for AGENT/SKILL.

        If an MCP_TOOL returns fewer keys than output_schema declared, that's
        a bug in the generated code and shows up as a KeyError downstream,
        not something the runtime validator should enforce (the generator
        and Tier 2 behavioral test are the right layers for that).
        """
        mock_mcp.return_value = {"only_one_key": 42}

        subtask = SubTask(
            description="Parse CSV",
            required_capabilities=["parse"],
            depends_on=[],
            output_schema={"row_count": "int", "column_names": "list[str]"},
        )
        art = _make_artifact(
            "parser", CapabilityType.MCP_TOOL,
            gap_query="parse",
            source_code="async def parser(): return {'only_one_key': 42}",
        )
        ex = CompositionalExecutor(
            subtasks=[subtask],
            resolution={"parse": CapabilityReference(
                source="generated", capability_name="parser",
                capability_type=CapabilityType.MCP_TOOL,
            )},
            artifacts=[art],
            matches=[],
            fixture_paths=[],
            task=_make_task(),
        )

        result = ex.run()
        # MCP_TOOL output missing declared keys still lets the chain succeed
        # at this step — the runtime validator skips MCP_TOOL.
        assert result.chain_success is True

    def test_union_output_schema_lead_with_subagents(self):
        """Union spans every subtask resolving to the same artifact.

        T16 third-run failure: decomposer produced 5 subtasks, 4 AGENT
        gaps collapsed into lead+subagents. Using the first subtask's
        narrow schema as response_format forced the lead to produce only
        parse-YAML keys, rejecting its natural aggregated output.

        The union fix: combine all 4 collapsed subtasks' schemas so the
        response_format captures every key the lead naturally produces.
        """
        subtasks = [
            SubTask(
                description="parse yaml",
                required_capabilities=["parse-yaml"],
                depends_on=[],
                output_schema={"parsed_yaml": "dict"},
            ),
            SubTask(
                description="analyze onsets",
                required_capabilities=["analyze-onsets"],
                depends_on=[0],
                output_schema={"per_metric_onsets": "dict"},
            ),
            SubTask(
                description="final summary",
                required_capabilities=["summarize"],
                depends_on=[1],
                output_schema={
                    "root_cause_metric": "str",
                    "cascade_chain": "list[str]",
                    "n_metrics_investigated": "int",
                },
            ),
        ]
        # All three subtasks resolve to the same lead+subagents artifact.
        lead_ref = CapabilityReference(
            source="generated", capability_name="incident-triage-lead",
            capability_type=CapabilityType.AGENT,
        )
        art = _make_artifact(
            "incident-triage-lead", CapabilityType.AGENT,
            gap_query="parse-yaml",  # primary query
            agent_config={"system_prompt": "lead"},
        )
        executor = CompositionalExecutor(
            subtasks=subtasks,
            resolution={
                "parse-yaml": lead_ref,
                "analyze-onsets": lead_ref,
                "summarize": lead_ref,
            },
            artifacts=[art],
            matches=[],
            fixture_paths=[],
            task=_make_task(),
        )

        union = executor._collect_union_output_schema(
            artifact=art, current_ref=lead_ref,
        )
        assert union is not None
        # Union covers keys from all three collapsed subtasks.
        assert set(union.keys()) == {
            "parsed_yaml",
            "per_metric_onsets",
            "root_cause_metric",
            "cascade_chain",
            "n_metrics_investigated",
        }

    def test_union_output_schema_solo_agent_equals_subtask_schema(self):
        """When only one subtask resolves to the artifact, union = that schema."""
        subtasks = [
            SubTask(
                description="solo investigation",
                required_capabilities=["investigate"],
                depends_on=[],
                output_schema={
                    "root_cause_metric": "str",
                    "cascade_chain": "list[str]",
                },
            ),
        ]
        ref = CapabilityReference(
            source="generated", capability_name="investigator",
            capability_type=CapabilityType.AGENT,
        )
        art = _make_artifact(
            "investigator", CapabilityType.AGENT,
            gap_query="investigate",
            agent_config={"system_prompt": "go"},
        )
        executor = CompositionalExecutor(
            subtasks=subtasks,
            resolution={"investigate": ref},
            artifacts=[art],
            matches=[],
            fixture_paths=[],
            task=_make_task(),
        )

        union = executor._collect_union_output_schema(
            artifact=art, current_ref=ref,
        )
        assert union == {
            "root_cause_metric": "str",
            "cascade_chain": "list[str]",
        }

    @patch("tests.evaluation.execution.execute_skill_artifact")
    def test_skill_missing_schema_keys_also_fails(self, mock_skill):
        """The runtime validator applies to SKILL too, not just AGENT."""
        mock_skill.return_value = {"partial": "output"}

        subtask = SubTask(
            description="Apply domain workflow",
            required_capabilities=["apply"],
            depends_on=[],
            output_schema={"summary": "str", "counts": "dict"},
        )
        art = _make_artifact(
            "applier", CapabilityType.SKILL,
            gap_query="apply",
            skill_content="# applier SKILL",
        )
        ex = CompositionalExecutor(
            subtasks=[subtask],
            resolution={"apply": CapabilityReference(
                source="generated", capability_name="applier",
                capability_type=CapabilityType.SKILL,
            )},
            artifacts=[art],
            matches=[],
            fixture_paths=[],
            task=_make_task(),
        )

        result = ex.run()
        assert result.chain_success is False
        assert "summary" in (result.failed_step_reason or "")
        assert "counts" in (result.failed_step_reason or "")


class TestRecordStepInvocationPopulatesFields:
    """Regression guards for docs/invocation_recording_gap_2026_04_19.md.

    The pre-fix writer left input_args and actual_output as None, which
    silently excluded every production row from MetricsStore.get_recent_failures
    (WHERE input_args IS NOT NULL), making the Pillar 3 Split coverage
    verifier + failure-informed prompt structurally dead on real telemetry.
    These tests assert the post-fix writer populates the fields that Split
    depends on, and that error-shaped outputs are recorded as failures.
    """

    def _make_executor_with_metrics(self, metrics_store):
        subtask = _make_subtask("do thing", ["x"])
        ref = CapabilityReference(
            source="generated",
            capability_name="thing-doer",
            capability_type=CapabilityType.MCP_TOOL,
        )
        ex = CompositionalExecutor(
            subtasks=[subtask],
            resolution={"x": ref},
            artifacts=[],
            matches=[],
            fixture_paths=[],
            task=_make_task(),
            metrics_store=metrics_store,
        )
        return ex, ref

    def test_record_step_invocation_populates_input_args_and_output(self):
        """Chain writer must serialize upstream_outputs + output into the
        InvocationRecord so get_recent_failures returns the row."""
        from src.registry.metrics import MetricsStore

        metrics = MetricsStore(db_path=":memory:")
        ex, ref = self._make_executor_with_metrics(metrics)

        # Simulate a failed chain step: output is error-shaped.
        upstream = {0: {"rows": [{"a": 1}, {"a": 2}]}}
        output = {"error": "downstream function crashed on empty CSV"}

        ex._record_step_invocation(
            ref, output, latency_ms=12.5, task_id="T99",
            upstream_outputs=upstream,
        )

        # Row must survive the get_recent_failures filter.
        failures = metrics.get_recent_failures("thing-doer")
        assert len(failures) == 1
        rec = failures[0]
        assert rec.input_args is not None
        assert '"rows"' in rec.input_args  # upstream_outputs serialized
        assert rec.actual_output is not None
        assert '"error"' in rec.actual_output  # output serialized
        assert rec.error_message is not None
        assert "downstream function crashed" in rec.error_message
        assert rec.success is False

    def test_error_shaped_output_recorded_as_failure(self):
        """{'error': '...'} must be recorded as success=False, not True —
        pre-fix bool(output) treated non-empty error dicts as successes."""
        from src.registry.metrics import MetricsStore

        metrics = MetricsStore(db_path=":memory:")
        ex, ref = self._make_executor_with_metrics(metrics)

        output = {"error": "tool raised something"}
        ex._record_step_invocation(
            ref, output, latency_ms=1.0, task_id="T99",
            upstream_outputs={0: {"x": 1}},
        )

        failures = metrics.get_recent_failures("thing-doer")
        assert len(failures) == 1
        assert failures[0].success is False

    def test_successful_output_not_mistaken_for_error(self):
        """Legitimate outputs with 'error_count' or similar keys must NOT
        trigger the conservative error-shape check."""
        from src.registry.metrics import MetricsStore

        metrics = MetricsStore(db_path=":memory:")
        ex, ref = self._make_executor_with_metrics(metrics)

        # Real tool output: no 'error'/'exception'/'traceback'/'sandbox_error' key.
        output = {"error_count": 0, "total_rows": 100}
        ex._record_step_invocation(
            ref, output, latency_ms=1.0, task_id="T99",
            upstream_outputs={0: {"x": 1}},
        )

        # Row recorded as success — get_recent_failures returns nothing.
        failures = metrics.get_recent_failures("thing-doer")
        assert len(failures) == 0

    def test_empty_upstream_serializes_to_none(self):
        """Empty dict upstream (step 0 with no prior outputs) → input_args=None.
        That's intentional — step-0 rows aren't usable as replay probes
        because the kwargs came from fixtures, not upstream."""
        from src.registry.metrics import MetricsStore

        metrics = MetricsStore(db_path=":memory:")
        ex, ref = self._make_executor_with_metrics(metrics)

        ex._record_step_invocation(
            ref, {"error": "step 0 blew up"}, latency_ms=1.0, task_id="T99",
            upstream_outputs={},
        )

        # Row has NULL input_args → get_recent_failures filters it out.
        failures = metrics.get_recent_failures("thing-doer")
        assert len(failures) == 0
