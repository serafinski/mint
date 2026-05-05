"""Subprocess-level smoke tests for ``scripts/test_registry_fn.py``."""

from __future__ import annotations

from tests.unit._script_subprocess import run_project_script


def test_registry_fn_help_exposes_minimum_capability_coverage_flag():
    result = run_project_script("test_registry_fn.py", "--help", timeout=15)

    assert result.returncode == 0
    assert "--min-cap-coverage" in result.stdout
