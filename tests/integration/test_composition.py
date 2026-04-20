"""Integration tests for CompositionalExecutor — require Docker + built jit-sandbox image.

Tests the full chain execution with real workspace I/O and Docker sandbox.
Build the image first: docker build -t jit-sandbox sandbox/
"""

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.orchestrator.composition import CompositionalExecutor, build_resolution_map
from src.schemas.capability import CapabilityDescriptor, CapabilityType
from src.schemas.task import (
    CapabilityReference,
    GeneratedArtifact,
    MatchItem,
    SubTask,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_subtask(desc: str, caps: list[str], depends_on: list[int] | None = None) -> SubTask:
    return SubTask(
        description=desc,
        required_capabilities=caps,
        depends_on=depends_on or [],
    )


def _make_artifact(
    name: str, cap_type: CapabilityType, gap_query: str,
    source_code: str | None = None, pip_packages: list[str] | None = None,
) -> GeneratedArtifact:
    return GeneratedArtifact(
        capability_name=name,
        capability_type=cap_type,
        gap_query=gap_query,
        source_code=source_code,
        pip_packages=pip_packages or [],
        generated_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# MCP_TOOL chain with real Docker execution
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_two_step_mcp_tool_chain_with_docker(tmp_path):
    """Two MCP_TOOL steps: step 0 parses a CSV, step 1 aggregates the parsed output.

    This exercises the full workspace mounting path: step 0 writes to
    /workspace/step_0_output.json inside Docker, and step 1 reads it.
    """
    # Create a fixture CSV
    fixture = tmp_path / "data.csv"
    fixture.write_text("name,value\nalpha,10\nbeta,20\ngamma,30\n")

    # Step 0: parse CSV, return rows under a stable upstream key.
    # The pre-fix step0 returned a bare list which the executor wraps as
    # ``{"result": [...]}``; that key name then leaked into step 1's
    # parameter contract. Using a named key (``rows``) keeps the
    # upstream payload self-describing and matches what generated
    # MCP tools are instructed to emit.
    step0_code = '''\
import csv

async def parse_csv(file_path: str) -> dict:
    """Parse a CSV file and return rows under the 'rows' key."""
    rows = []
    with open(file_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return {"rows": rows}
'''

    # Step 1: sum the "value" column from upstream parsed data.
    # Modern chain convention (commit 1283f68 "chain schema contracts,
    # upstream_data parameter"): every downstream step takes a single
    # ``upstream_data: dict`` parameter that contains the merged dict
    # from all predecessors. Pre-fix this test used per-key matching
    # (``parsed_data: list``) which the sandbox stopped supporting
    # when generated tools standardized on ``upstream_data``. Security
    # audit ext9 verification re-run (2026-04-20) flagged the old shape
    # as a stale test asserting deprecated behavior.
    step1_code = '''\
async def aggregate_values(upstream_data: dict) -> dict:
    """Sum the value column from parsed CSV rows in upstream_data."""
    rows = upstream_data["rows"]
    total = sum(int(row["value"]) for row in rows)
    return {"total": total, "count": len(rows)}
'''

    subtasks = [
        _make_subtask("Parse the CSV file", ["parse-csv"]),
        _make_subtask("Aggregate values", ["aggregate-values"], depends_on=[0]),
    ]

    artifacts = [
        _make_artifact("csv-parser", CapabilityType.MCP_TOOL, "parse-csv", step0_code),
        _make_artifact("value-aggregator", CapabilityType.MCP_TOOL, "aggregate-values", step1_code),
    ]

    resolution = build_resolution_map(
        ["parse-csv", "aggregate-values"], [], artifacts,
    )

    task = MagicMock()
    task.task_id = "test-chain"
    task.fixture_paths = [fixture]

    executor = CompositionalExecutor(
        subtasks=subtasks,
        resolution=resolution,
        artifacts=artifacts,
        matches=[],
        fixture_paths=[fixture],
        task=task,
    )

    result = executor.run(task_id="test-chain")

    assert result.chain_success is True, (
        f"Chain failed at step {result.failed_step_index}: {result.failed_step_name}. "
        f"Step results: {result.step_results}"
    )
    assert result.chain_length == 2
    assert len(result.step_results) == 2

    # Step 0 should return parsed CSV rows under the 'rows' key.
    step0_output = result.step_results[0]
    assert isinstance(step0_output, dict)
    assert isinstance(step0_output["rows"], list)
    assert len(step0_output["rows"]) == 3

    # Step 1 should aggregate the values via upstream_data["rows"].
    step1_output = result.step_results[1]
    assert step1_output["total"] == 60
    assert step1_output["count"] == 3


@pytest.mark.integration
def test_chain_with_metrics_recording(tmp_path):
    """Verify that the compositional executor records real invocations."""
    from src.registry.metrics import MetricsStore

    fixture = tmp_path / "data.txt"
    fixture.write_text("hello")

    step0_code = '''\
async def read_file(file_path: str) -> dict:
    """Read a file and return its content."""
    with open(file_path) as f:
        return {"content": f.read().strip(), "length": len(f.read())}
'''

    subtasks = [_make_subtask("Read file", ["read-file"])]
    artifacts = [_make_artifact("reader", CapabilityType.MCP_TOOL, "read-file", step0_code)]
    resolution = build_resolution_map(["read-file"], [], artifacts)

    metrics_store = MetricsStore()  # in-memory

    task = MagicMock()
    task.task_id = "test-metrics"
    task.fixture_paths = [fixture]

    executor = CompositionalExecutor(
        subtasks=subtasks,
        resolution=resolution,
        artifacts=artifacts,
        matches=[],
        fixture_paths=[fixture],
        task=task,
        metrics_store=metrics_store,
    )

    result = executor.run(task_id="test-metrics")

    # Check invocation was recorded
    rows = metrics_store._conn.execute(
        "SELECT capability_id, success, latency_ms FROM invocations"
    ).fetchall()
    assert len(rows) >= 1
    cap_id, success, latency = rows[0]
    assert cap_id == "reader"
    assert latency > 0  # real execution time, not zero
