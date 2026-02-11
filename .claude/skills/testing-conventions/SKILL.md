# Testing Conventions Skill

## When to Use
Use this skill whenever writing or modifying tests in this project.

## Test Structure

```
tests/
├── unit/              # Fast, no I/O, no external services
├── integration/       # May use Docker, ChromaDB, DuckDB, MCP servers
└── evaluation/        # Thesis evaluation harnesses and baselines
```

## Rules

### Every module needs tests
A module is not done until it has tests. Write tests alongside implementation, not after.

### pytest conventions
- Use `pytest` (run via `uv run pytest`)
- Name test files `test_<module>.py`
- Name test functions `test_<behavior_being_tested>`
- Use fixtures for shared setup
- Use `tmp_path` for any file I/O in tests

### Unit tests (`tests/unit/`)
- No network calls, no Docker, no database
- Mock external dependencies
- Test one function/class at a time
- Should run in under 1 second total

```python
import pytest
from src.schemas.capability import CapabilityDescriptor, CapabilityType


def test_capability_descriptor_validates_required_fields():
    with pytest.raises(ValueError):
        CapabilityDescriptor()  # Missing required fields


def test_capability_descriptor_rejects_extra_fields():
    with pytest.raises(ValueError):
        CapabilityDescriptor(
            name="test",
            description="test",
            capability_type=CapabilityType.MCP_TOOL,
            unknown_field="oops",
        )
```

### Integration tests (`tests/integration/`)
- Can use real ChromaDB, DuckDB, Docker sandbox, MCP servers
- Use fixtures to set up and tear down resources
- Mark with `@pytest.mark.integration` so they can be skipped in fast runs

```python
import pytest


@pytest.mark.integration
def test_registry_persists_across_restarts(tmp_path):
    # Create registry, add capability, close, reopen, verify it's still there
    ...
```

### Testing LangGraph nodes
Test nodes in isolation by constructing the state dict and calling the node function directly:

```python
from src.orchestrator.pipeline import gap_detection_node


def test_gap_detection_finds_matching_capability(mock_registry):
    state = {"task_description": "calculate fibonacci", "matches": [], "gaps": []}
    result = gap_detection_node(state)
    assert len(result["matches"]) > 0
    assert len(result["gaps"]) == 0
```

### Testing MCP tools
Spin up the server, call via MCP client, tear down:

```python
@pytest.mark.integration
def test_fibonacci_mcp_tool():
    # Start server as subprocess
    # Create MCP client
    # Call the tool with {"n": 10}
    # Assert result == {"result": 55}
    # Tear down server
    ...
```

### Evaluation tests (`tests/evaluation/`)
These are not pass/fail tests — they produce metrics:

```python
def test_baseline_static_skills(evaluation_harness, testbed_tasks):
    results = evaluation_harness.run(baseline="static", tasks=testbed_tasks)
    results.save_to_duckdb()  # Metrics stored, not asserted
```

## Linting
Run `uv run ruff check` before committing. Fix all issues.
