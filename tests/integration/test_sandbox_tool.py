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
def test_sandbox_pandas_available():
    """Sandbox image must have pandas and numpy installed (Fix 0)."""
    result = sandbox_execute(
        "import pandas; import numpy; print('ok')", timeout=30
    )
    assert result["success"] is True
    assert "ok" in result["stdout"]
