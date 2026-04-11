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
    ClassifiedGap,
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
