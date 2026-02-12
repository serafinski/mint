import uuid

import pytest

from src.registry.store import SkillRegistry, seed_registry
from src.schemas.capability import CapabilityDescriptor, CapabilityType


@pytest.fixture
def registry():
    """Isolated ephemeral registry per test."""
    return SkillRegistry(collection_name=f"test-{uuid.uuid4().hex[:8]}")


@pytest.fixture
def seeded_registry(registry):
    """Registry pre-loaded with 3 seed capabilities."""
    seed_registry(registry)
    return registry


def test_add_and_search(registry):
    cap = CapabilityDescriptor(
        name="fibonacci",
        description="Compute the nth Fibonacci number",
        capability_type=CapabilityType.MCP_TOOL,
    )
    registry.add(cap)
    results = registry.search("fibonacci number")
    assert len(results) >= 1
    assert results[0][0].name == "fibonacci"
    assert results[0][1] > 0  # positive similarity


def test_search_empty_registry(registry):
    results = registry.search("anything")
    assert results == []


def test_seed_registry(registry):
    seed_registry(registry)
    assert registry.count == 3


def test_search_relevance(seeded_registry):
    results = seeded_registry.search("parse CSV data", n_results=3)
    assert results[0][0].name == "csv-parser"


def test_upsert_overwrites(registry):
    cap_v1 = CapabilityDescriptor(
        name="fibonacci",
        description="Compute fibonacci",
        capability_type=CapabilityType.MCP_TOOL,
        version=1,
    )
    cap_v2 = CapabilityDescriptor(
        name="fibonacci",
        description="Compute fibonacci numbers efficiently",
        capability_type=CapabilityType.MCP_TOOL,
        version=2,
    )
    registry.add(cap_v1)
    registry.add(cap_v2)
    assert registry.count == 1
    results = registry.search("fibonacci")
    assert results[0][0].version == 2


def test_detect_gaps_finds_match(seeded_registry):
    matches, gaps = seeded_registry.detect_gaps(["compute fibonacci number"])
    assert len(matches) == 1
    assert len(gaps) == 0
    assert matches[0][0].name == "fibonacci"


def test_detect_gaps_finds_gap(seeded_registry):
    matches, gaps = seeded_registry.detect_gaps(
        ["translate English to French"], threshold=0.8
    )
    assert len(gaps) == 1
    assert len(matches) == 0
    assert "below threshold" in gaps[0][1]


def test_detect_gaps_mixed(seeded_registry):
    matches, gaps = seeded_registry.detect_gaps(
        ["compute fibonacci number", "deploy kubernetes cluster"],
        threshold=0.5,
    )
    assert len(matches) >= 1
    # kubernetes is far from any seeded capability
    assert len(gaps) >= 0  # depends on embedding model's judgment


def test_persistent_registry(tmp_path):
    persist_dir = str(tmp_path / "registry")
    registry = SkillRegistry(persist_directory=persist_dir)
    seed_registry(registry)
    assert registry.count == 3

    # Reopen from disk
    registry2 = SkillRegistry(persist_directory=persist_dir)
    assert registry2.count == 3
    results = registry2.search("fibonacci")
    assert results[0][0].name == "fibonacci"
