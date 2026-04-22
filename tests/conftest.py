"""Shared pytest configuration and fixtures."""

import os
import tempfile
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Step 13.7 tagging audit (2026-04-18) — route all test-emitted LangFuse
# events to environment="test" so Chapter 6 analysis can filter them out
# at the project-level environment selector (LangFuse UI + REST API).
#
# Rationale. Tests that exercise gate.verify() / sandbox_execute() /
# emit_langfuse_event() / composition step spans / Pillar 3 operations
# all emit real events. Without this isolation, every `uv run pytest`
# invocation would land thousands of `verification.tier_*`,
# `sandbox.execute`, `pillar3.*`, etc. events into the SAME LangFuse
# project the 400-record eval writes to — polluting the Pillar 2
# "empirically observed contamination patterns" evidence (proposal
# line 152) with test-fixture data.
#
# Why environment= rather than credential-clearing: environment="test"
# keeps test integration coverage (tests still exercise the LangFuse
# wire path, catching SDK regressions), while Chapter 6 filters
# `environment="default"` natively via the first-class observation
# field. LangFuse UI has a dedicated environment selector.
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


# Opt-out env-var: set LANGFUSE_DISABLE_DURING_TESTS=true to fall back
# to the older "disable SDK entirely" behavior (e.g., for local test
# runs that shouldn't hit LangFuse at all, or for CI where LangFuse
# isn't reachable). Default is environment="test" which requires the
# LangFuse service to be up.
if os.environ.get("LANGFUSE_DISABLE_DURING_TESTS", "").lower() in ("true", "1", "yes"):
    os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
    os.environ.pop("LANGFUSE_SECRET_KEY", None)
    os.environ["LANGFUSE_TRACING_ENABLED"] = "false"


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
