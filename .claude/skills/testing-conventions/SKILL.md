---
name: testing-conventions
description: "pytest conventions, test directory structure, and patterns for testing LangGraph nodes, MCP tools, and evaluation harnesses in the JIT capability synthesis project. Use this skill whenever writing or modifying tests, creating new test files, setting up test fixtures, or when a module is being implemented and needs accompanying tests. Also trigger when someone asks about test structure, how to run tests, how to test a LangGraph node in isolation, how to test an MCP tool, or when reviewing code that lacks test coverage."
---

# Testing Conventions

Every module in this project needs tests. A module is not done until it has tests — write them alongside implementation, not after. The reason: when Agent Team teammates build modules in parallel, tests are the contract that proves the module works before integration.

## Test Structure

```
tests/
├── unit/              # Fast, no I/O, no external services
├── integration/       # May use Docker, ChromaDB, DuckDB, MCP servers
└── evaluation/        # Thesis evaluation harnesses and baselines
```

## Running Tests

```bash
uv run pytest                            # All tests
uv run pytest tests/unit/                # Unit only (fast)
uv run pytest -m "not integration"       # Skip integration
uv run ruff check                        # Lint before committing
```

## Naming

Name test files `test_<module>.py` and test functions `test_<behavior_being_tested>`. Descriptive names matter because when a test fails in CI, the name is the first clue about what broke.

## Unit Tests (`tests/unit/`)

No network calls, no Docker, no database. Mock external dependencies. Test one function/class at a time. The full unit suite should run in under a second.

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

Use fixtures for shared setup. Use `tmp_path` for any file I/O in tests — never write to fixed paths.

## Integration Tests (`tests/integration/`)

These can use real ChromaDB, DuckDB, Docker sandbox, and MCP servers. Mark them with `@pytest.mark.integration` so they can be skipped during fast iteration:

```python
import pytest


@pytest.mark.integration
def test_registry_persists_across_restarts(tmp_path):
    # Create registry, add capability, close, reopen, verify it's still there
    ...
```

## Testing LangGraph Nodes and Graphs

There are three levels of LangGraph testing, each useful in different situations. Use the lightest level that covers what you need.

### Level 1: Direct function call (fastest, for pure logic)

Call the node function directly with a state dict. This bypasses the graph entirely — good for testing business logic without any LangGraph overhead. Use this when the node is a pure transformation that doesn't depend on graph state management or checkpointing.

```python
from src.orchestrator.pipeline import gap_detection_node


def test_gap_detection_finds_matching_capability(mock_registry):
    state = {"task_description": "calculate fibonacci", "matches": [], "gaps": []}
    result = gap_detection_node(state)
    assert len(result["matches"]) > 0
    assert len(result["gaps"]) == 0
```

### Level 2: Compiled node invocation (state-aware)

Compile the graph, then invoke individual nodes via `graph.nodes["node_name"].invoke()`. This uses LangGraph's state management so you're testing the node as it actually runs in the graph, but in isolation. Use this when you want to verify that a node interacts correctly with the typed state.

```python
from langgraph.checkpoint.memory import MemorySaver
from src.orchestrator.pipeline import create_jit_pipeline


def test_classification_node_produces_valid_type():
    checkpointer = MemorySaver()
    graph = create_jit_pipeline()
    compiled = graph.compile(checkpointer=checkpointer)

    result = compiled.nodes["classify_capability"].invoke({
        "gap_description": "need to fetch weather data from OpenWeather API",
        "capability_type": None,
    })
    assert result["capability_type"] in ("mcp_tool", "skill", "agent")
```

### Level 3: Partial graph execution (test a pipeline segment)

When you need to test how several nodes work together without running the full pipeline, use `update_state` to inject state at a specific point, then `interrupt_after` to stop at another. This is especially useful for testing the JIT pipeline stages — for instance, running just the classification → generation → verification segment without task decomposition.

```python
from langgraph.checkpoint.memory import MemorySaver
from src.orchestrator.pipeline import create_jit_pipeline


def test_generation_through_verification():
    """Test that a classified gap flows through generation and verification."""
    checkpointer = MemorySaver()
    graph = create_jit_pipeline()
    compiled = graph.compile(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "test-1"}}

    # Inject state as if gap_detection just finished
    compiled.update_state(
        config=config,
        values={
            "gaps": [{"description": "fetch weather data", "capability_type": "mcp_tool"}],
            "generated_artifacts": [],
            "verification_results": [],
        },
        as_node="gap_detection",  # Resume from after this node
    )

    result = compiled.invoke(
        None,  # Resume from injected state
        config=config,
        interrupt_after="verification",  # Stop here
    )
    assert len(result["generated_artifacts"]) > 0
    assert all(v["passed"] for v in result["verification_results"])
```

### Full graph execution (end-to-end)

For end-to-end tests, compile and invoke the full graph. Always use `MemorySaver` in tests for isolation — each test gets its own `thread_id` so there's no state leakage between tests.

```python
def test_full_pipeline_solves_simple_task():
    checkpointer = MemorySaver()
    graph = create_jit_pipeline()
    compiled = graph.compile(checkpointer=checkpointer)

    result = compiled.invoke(
        {"task_description": "compute the factorial of a number"},
        config={"configurable": {"thread_id": "e2e-test-1"}},
    )
    assert result["status"] == "completed"
```

### Mocking LLM calls in node tests

Nodes that call a DeepAgent or LLM internally should be mocked in unit tests. The goal is to test the node's logic, not the model's output. Use real LLM calls only in integration tests.

```python
from unittest.mock import patch


def test_classification_logic_with_mocked_llm():
    with patch("src.orchestrator.nodes.classify.call_llm") as mock_llm:
        mock_llm.return_value = {"capability_type": "mcp_tool", "confidence": 0.92}
        result = classify_node({"gap_description": "fetch stock prices"})
        assert result["capability_type"] == "mcp_tool"
```

### Graph creation pattern

Create graphs in a factory function rather than at module level. This lets each test start fresh and avoids shared mutable state:

```python
# Good: factory function
def create_graph() -> StateGraph:
    graph = StateGraph(PipelineState)
    graph.add_node("decompose", decompose_node)
    graph.add_node("detect_gaps", gap_detection_node)
    # ...
    return graph

# In tests
def test_something():
    graph = create_graph()
    compiled = graph.compile(checkpointer=MemorySaver())
    ...
```

## Testing MCP Tools

Spin up the server as a subprocess, call via MCP client, verify the response, tear down:

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

For generated MCP tools (from the JIT pipeline), test them in the Docker sandbox the same way the verification layer does — this ensures your test environment matches production.

## Testing the Verification Layer

The verification layer (Pillar 2) itself needs tests. Each tier should be testable independently:

```python
def test_static_scanner_detects_hardcoded_response(tmp_path):
    """Tier 1: scanner should flag a function that returns a string literal."""
    code = tmp_path / "tool.py"
    code.write_text('def get_weather(city: str) -> str:\n    return "72°F and sunny"\n')
    report = static_scanner.scan(code)
    assert any(f.severity == "critical" for f in report.findings)


@pytest.mark.integration
def test_behavioral_probe_catches_mock_contamination():
    """Tier 2: nonexistent entity should not return plausible data."""
    ...


@pytest.mark.integration
def test_adversarial_probe_detects_demo_mode():
    """Tier 3: red-team agent tries to trigger hidden demo behavior."""
    ...
```

## Testing Pydantic Schemas

Schemas are the contracts between pipeline stages. Test that they validate correctly and reject bad data — this catches integration bugs early.

```python
from src.schemas.capability import CapabilityDescriptor, CapabilityType


def test_schema_roundtrip():
    """Serialize to JSON, deserialize back, verify equality."""
    cap = CapabilityDescriptor(
        name="weather_fetcher",
        description="Fetch weather data from OpenWeather API",
        capability_type=CapabilityType.MCP_TOOL,
    )
    roundtripped = CapabilityDescriptor.model_validate_json(cap.model_dump_json())
    assert roundtripped == cap
```

## Evaluation Tests (`tests/evaluation/`)

These are not pass/fail tests — they produce metrics for the thesis. They run all baselines against the testbed and store results:

```python
def test_baseline_static_skills(evaluation_harness, testbed_tasks):
    results = evaluation_harness.run(baseline="static", tasks=testbed_tasks)
    results.save_to_duckdb()  # Metrics stored, not asserted
```

## Fixtures: `conftest.py` Conventions

Put shared fixtures in `conftest.py` at each test directory level. Prefer narrow scope (`function` default) to avoid test coupling. A few project-wide fixtures belong in the top-level `tests/conftest.py`:

```python
# tests/conftest.py
import pytest


@pytest.fixture
def mock_registry(tmp_path):
    """An in-memory skill registry pre-loaded with a few test capabilities."""
    from src.registry.memory import InMemoryRegistry
    reg = InMemoryRegistry(path=tmp_path / "registry")
    reg.add(name="fibonacci", description="compute fibonacci numbers", type="mcp_tool")
    return reg


@pytest.fixture
def sample_pipeline_state():
    """A minimal valid PipelineState dict for testing nodes."""
    return {
        "task_description": "",
        "subtasks": [],
        "matches": [],
        "gaps": [],
        "generated_artifacts": [],
        "verification_results": [],
        "status": "pending",
    }
```

## Linting

Run `uv run ruff check` before committing. Fix all issues. Ruff is fast enough to run on every save.
