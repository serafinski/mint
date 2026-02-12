import pytest

from src.schemas.capability import CapabilityDescriptor, CapabilityType
from src.schemas.task import GapItem, MatchItem, TaskState

# --- CapabilityDescriptor ---


def test_capability_descriptor_valid():
    cap = CapabilityDescriptor(
        name="fibonacci",
        description="Compute fibonacci numbers",
        capability_type=CapabilityType.MCP_TOOL,
    )
    assert cap.name == "fibonacci"
    assert cap.capability_type == CapabilityType.MCP_TOOL
    assert cap.version == 1


def test_capability_descriptor_rejects_extra_fields():
    with pytest.raises(ValueError):
        CapabilityDescriptor(
            name="test",
            description="test",
            capability_type=CapabilityType.MCP_TOOL,
            unknown_field="oops",
        )


def test_capability_descriptor_rejects_invalid_type():
    with pytest.raises(ValueError):
        CapabilityDescriptor(
            name="test",
            description="test",
            capability_type="not_a_real_type",
        )


def test_capability_descriptor_requires_name():
    with pytest.raises(ValueError):
        CapabilityDescriptor(
            description="test",
            capability_type=CapabilityType.SKILL,
        )


def test_capability_descriptor_roundtrip():
    cap = CapabilityDescriptor(
        name="weather",
        description="Fetch weather data",
        capability_type=CapabilityType.MCP_TOOL,
    )
    roundtripped = CapabilityDescriptor.model_validate_json(cap.model_dump_json())
    assert roundtripped.name == cap.name
    assert roundtripped.capability_type == cap.capability_type


def test_capability_type_values():
    assert CapabilityType.MCP_TOOL.value == "mcp_tool"
    assert CapabilityType.SKILL.value == "skill"
    assert CapabilityType.AGENT.value == "agent"


# --- TaskState ---


def test_task_state_minimal():
    state = TaskState(task_description="compute fibonacci")
    assert state.task_description == "compute fibonacci"
    assert state.required_capabilities == []
    assert state.matches == []
    assert state.gaps == []


def test_task_state_rejects_extra_fields():
    with pytest.raises(ValueError):
        TaskState(task_description="test", bogus="field")


def test_task_state_with_matches_and_gaps():
    cap = CapabilityDescriptor(
        name="fib",
        description="fibonacci",
        capability_type=CapabilityType.MCP_TOOL,
    )
    state = TaskState(
        task_description="compute stuff",
        required_capabilities=["fibonacci", "weather"],
        matches=[MatchItem(capability=cap, score=0.85)],
        gaps=[GapItem(description="weather data", query="fetch weather")],
    )
    assert len(state.matches) == 1
    assert state.matches[0].score == 0.85
    assert len(state.gaps) == 1


# --- GapItem / MatchItem ---


def test_gap_item_rejects_extra():
    with pytest.raises(ValueError):
        GapItem(description="test", query="test", extra="bad")


def test_match_item_requires_capability():
    with pytest.raises(ValueError):
        MatchItem(score=0.5)
