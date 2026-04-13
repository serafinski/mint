"""Shared pytest configuration and fixtures."""

import tempfile
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Load .env at test collection time so all tests see the environment variables.
load_dotenv()


@pytest.fixture(autouse=True)
def _isolate_taxonomy_path(tmp_path, monkeypatch):
    """Redirect the evolutionary-taxonomy file to a per-test tempdir.

    Keeps unit tests hermetic: without this, any test that runs
    ``gap_detection_node`` or a generator node would write to (and read
    from) ``data/cli/domains.json``, and an existing taxonomy would
    trigger real LLM calls via ``get_for_query``. The fixture is
    autouse so no individual test needs to opt in.
    """
    from src.orchestrator import taxonomy

    monkeypatch.setattr(taxonomy, "_TAXONOMY_PATH", tmp_path / "domains.json")
    yield
