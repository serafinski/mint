"""Integration tests for sandbox_execute — require Docker + built jit-sandbox image.

Build the image first: docker build -t jit-sandbox sandbox/

These tests are skipped in CI unless Docker is available.
"""

import pytest

from src.verification.sandbox_tool import sandbox_execute


@pytest.mark.integration
def test_docker_executes_simple_script():
    """Actually runs the sandbox image; verifies stdout is captured."""
    result = sandbox_execute("print('sandbox-ok')", timeout=30)
    assert result["success"] is True
    assert "sandbox-ok" in result["stdout"]


@pytest.mark.integration
def test_docker_network_is_disabled():
    """Network access inside the container must fail with --network none."""
    code = (
        "import urllib.request\n"
        "try:\n"
        "    urllib.request.urlopen('http://example.com', timeout=2)\n"
        "    print('CONNECTED')\n"
        "except Exception:\n"
        "    print('BLOCKED')\n"
    )
    result = sandbox_execute(code, timeout=20)
    assert "CONNECTED" not in result["stdout"]
    assert "BLOCKED" in result["stdout"]


@pytest.mark.integration
def test_docker_script_error_captured():
    """Python exceptions inside the sandbox appear in stderr, success=False."""
    result = sandbox_execute("raise ValueError('boom')", timeout=15)
    assert result["success"] is False
    assert "ValueError" in result["stderr"]


@pytest.mark.integration
def test_docker_exit_code_nonzero_on_error():
    """Non-zero exit code is propagated correctly."""
    result = sandbox_execute("import sys; sys.exit(42)", timeout=15)
    assert result["exit_code"] == 42
    assert result["success"] is False


@pytest.mark.integration
def test_sandbox_dynamic_pandas_install():
    """Pandas + numpy install dynamically when declared via ``extra_packages``.

    Iteration 5 (commit ``4535afc``, 2026-03-16) replaced the static
    pre-baked package list (``pandas``, ``numpy`` baked into the image)
    with dynamic resolution — the ``Dockerfile`` now installs only
    ``mcp`` + ``pydantic``, and any other PyPI dependency is installed
    on demand inside an ephemeral phase-1 container, then the tool
    runs in a phase-2 container with ``--network none``. The previous
    test (``test_sandbox_pandas_available``, removed) asserted the
    pre-iteration-5 fat-image behavior — a bare ``import pandas``
    without declaring it. That assertion was stale by ~5 weeks before
    being noticed (security audit ext9 verification re-run, 2026-04-20).

    This replacement asserts the modern contract: a tool that imports
    pandas works iff ``extra_packages=["pandas", "numpy"]`` is passed.
    """
    result = sandbox_execute(
        "import pandas; import numpy; print('ok')",
        timeout=120,
        extra_packages=["pandas", "numpy"],
    )
    assert result["success"] is True, (
        f"dynamic pandas install failed: stderr={result.get('stderr', '')[:300]}"
    )
    assert "ok" in result["stdout"]


@pytest.mark.integration
def test_sandbox_bare_pandas_import_without_declaration_fails():
    """Inverse guard for security audit ext9 #2: bare ``import pandas`` without
    declaring it via ``extra_packages`` MUST fail with ``ModuleNotFoundError``.

    Catches the regression where someone re-bakes pandas into the
    Dockerfile (silently re-introducing the static-deps anti-pattern).
    The dynamic-deps design requires every non-base-image dependency
    to be declared explicitly so the optimizer's M3 token-cost
    accounting and the verifier's pip-package probe (Tier 1.5) can
    track them.
    """
    result = sandbox_execute(
        "import pandas; print('ok')", timeout=30
    )
    assert result["success"] is False
    assert "ModuleNotFoundError" in result.get("stderr", "")
    assert "pandas" in result.get("stderr", "")
