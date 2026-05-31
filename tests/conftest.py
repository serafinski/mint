"""Shared pytest configuration and fixtures."""

import os

import pytest
from dotenv import load_dotenv

# Route all test-emitted LangFuse events to environment="test" so they stay
# separate from evaluation traces in the LangFuse UI and API.
#
# Tests that exercise gate.verify(), sandbox_execute(), emit_langfuse_event(),
# composition spans, and optimizer operations can emit real events. Without
# environment isolation, every pytest run would write test-fixture events into
# the same LangFuse project used by evaluation runs.
#
# Use environment isolation rather than clearing credentials by default so tests
# still exercise the LangFuse SDK path and catch integration regressions. Set
# LANGFUSE_DISABLE_DURING_TESTS=true to disable tracing entirely.
#
# Must be set BEFORE load_dotenv() so any ambient LANGFUSE_TRACING_*
# values from .env don't override. Set via os.environ directly rather
# than pytest ini so conftest works under any invocation pattern
# (pytest, uv run, direct python -m pytest, etc.).
os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", "test")

# Load .env at test collection time so all tests see the environment
# variables. .env provides LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY +
# LANGFUSE_BASE_URL — SDK connects and emits normally.
load_dotenv()


# Opt-out env-var for local or CI runs that should not contact LangFuse.
# Default behavior is environment="test", which requires LangFuse to be
# reachable when credentials are present.
if os.environ.get("LANGFUSE_DISABLE_DURING_TESTS", "").lower() in ("true", "1", "yes"):
    os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
    os.environ.pop("LANGFUSE_SECRET_KEY", None)
    os.environ["LANGFUSE_TRACING_ENABLED"] = "false"


@pytest.fixture(autouse=True)
def _isolate_taxonomy_path(tmp_path, monkeypatch):
    """Redirect the evolutionary-taxonomy file to a per-test tempdir.

    Keeps unit tests hermetic: without this, any test that runs
    ``gap_detection_node`` or a generator node would write to (and read
    from) ``data/eval/domains.json``, and an existing taxonomy would
    trigger real LLM calls via ``get_for_query``. The fixture is
    autouse so no individual test needs to opt in.
    """
    from src.orchestrator import taxonomy

    monkeypatch.setattr(taxonomy, "_TAXONOMY_PATH", tmp_path / "domains.json")
    yield
