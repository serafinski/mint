"""Unit tests for src/verification/sandbox_tool.py.

All tests mock subprocess.run — no Docker required.
Integration tests that actually invoke Docker are marked @pytest.mark.integration.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.verification.sandbox_tool import (
    SANDBOX_IMAGE,
    _PackageMapping,
    _resolve_imports_via_llm,
    extract_imports,
    resolve_extra_packages,
    sandbox_execute,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# Return-value shape
# ---------------------------------------------------------------------------


def test_success_returns_correct_structure():
    """All four keys present, correct types, success=True when exit_code=0."""
    with patch("subprocess.run", return_value=_make_proc(returncode=0, stdout="hello\n")):
        result = sandbox_execute("print('hello')")

    assert result == {
        "stdout": "hello\n",
        "stderr": "",
        "exit_code": 0,
        "success": True,
    }


def test_failure_exit_code_sets_success_false():
    """Non-zero exit code → success=False, stderr preserved."""
    with patch("subprocess.run", return_value=_make_proc(returncode=1, stderr="NameError: x")):
        result = sandbox_execute("x")

    assert result["exit_code"] == 1
    assert result["success"] is False
    assert "NameError" in result["stderr"]


def test_timeout_returns_sentinel():
    """TimeoutExpired → exit_code=-1, success=False, stderr='Timeout'."""
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=5),
    ):
        result = sandbox_execute("import time; time.sleep(999)", timeout=5)

    assert result == {"stdout": "", "stderr": "Timeout", "exit_code": -1, "success": False}


def test_empty_code_runs_without_error():
    """Empty code string is passed to Docker without crashing."""
    with patch("subprocess.run", return_value=_make_proc(returncode=0)):
        result = sandbox_execute("")

    assert result["success"] is True


# ---------------------------------------------------------------------------
# Docker command structure
# ---------------------------------------------------------------------------


def test_docker_command_uses_network_none():
    """Command must include --network none."""
    captured: list[str] = []

    def capture(*args, **kwargs):
        captured.extend(args[0])
        return _make_proc()

    with patch("subprocess.run", side_effect=capture):
        sandbox_execute("pass")

    assert "--network" in captured
    idx = captured.index("--network")
    assert captured[idx + 1] == "none"


def test_docker_command_uses_correct_image():
    """SANDBOX_IMAGE constant appears in the docker run command."""
    captured: list[str] = []

    def capture(*args, **kwargs):
        captured.extend(args[0])
        return _make_proc()

    with patch("subprocess.run", side_effect=capture):
        sandbox_execute("pass")

    assert SANDBOX_IMAGE in captured


def test_docker_command_mounts_script_readonly():
    """Script is mounted with :ro flag."""
    captured: list[str] = []

    def capture(*args, **kwargs):
        captured.extend(args[0])
        return _make_proc()

    with patch("subprocess.run", side_effect=capture):
        sandbox_execute("pass")

    mount_args = [a for a in captured if ":ro" in a]
    assert len(mount_args) == 1
    assert "script.py" in mount_args[0]


def test_custom_timeout_forwarded_to_subprocess():
    """Custom timeout value is passed as the `timeout` kwarg to subprocess.run."""
    captured_kwargs: dict = {}

    def capture(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return _make_proc()

    with patch("subprocess.run", side_effect=capture):
        sandbox_execute("pass", timeout=42)

    assert captured_kwargs.get("timeout") == 42


# ---------------------------------------------------------------------------
# Tempfile cleanup
# ---------------------------------------------------------------------------


def test_tempfile_cleaned_up_on_success():
    """Temp script file is deleted after successful execution."""
    unlinked: list[str] = []
    real_unlink = Path.unlink

    def tracking_unlink(self: Path, *args, **kwargs):
        if str(self).endswith(".py"):
            unlinked.append(str(self))
        return real_unlink(self, *args, **kwargs)

    with (
        patch.object(Path, "unlink", tracking_unlink),
        patch("subprocess.run", return_value=_make_proc()),
    ):
        sandbox_execute("print(1)")

    assert len(unlinked) == 1


def test_tempfile_cleaned_up_on_timeout():
    """Temp script file is deleted even when Docker times out."""
    unlinked: list[str] = []
    real_unlink = Path.unlink

    def tracking_unlink(self: Path, *args, **kwargs):
        if str(self).endswith(".py"):
            unlinked.append(str(self))
        return real_unlink(self, *args, **kwargs)

    with (
        patch.object(Path, "unlink", tracking_unlink),
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=5),
        ),
    ):
        sandbox_execute("import time; time.sleep(999)")

    assert len(unlinked) == 1


# ---------------------------------------------------------------------------
# Non-timeout exceptions (Docker unavailable, permission error, etc.)
# ---------------------------------------------------------------------------


def test_file_not_found_returns_error_dict():
    """FileNotFoundError (Docker binary missing) is captured, never raised."""
    with patch("subprocess.run", side_effect=FileNotFoundError("docker: not found")):
        result = sandbox_execute("pass")

    assert result["success"] is False
    assert result["exit_code"] == -1
    assert "docker" in result["stderr"].lower()


def test_os_error_returns_error_dict():
    """Generic OSError (daemon down, permission denied) is captured, never raised."""
    with patch("subprocess.run", side_effect=OSError("Cannot connect to the Docker daemon")):
        result = sandbox_execute("pass")

    assert result["success"] is False
    assert result["exit_code"] == -1
    assert result["stdout"] == ""


def test_tempfile_cleaned_up_on_docker_not_found():
    """Temp file is deleted even when Docker binary is not found."""
    unlinked: list[str] = []
    real_unlink = Path.unlink

    def tracking_unlink(self: Path, *args, **kwargs):
        if str(self).endswith(".py"):
            unlinked.append(str(self))
        return real_unlink(self, *args, **kwargs)

    with (
        patch.object(Path, "unlink", tracking_unlink),
        patch("subprocess.run", side_effect=FileNotFoundError("docker: not found")),
    ):
        sandbox_execute("pass")

    assert len(unlinked) == 1


# ---------------------------------------------------------------------------
# extract_imports
# ---------------------------------------------------------------------------


class TestExtractImports:
    def test_simple_import(self):
        assert extract_imports("import pandas") == {"pandas"}

    def test_from_import(self):
        assert extract_imports("from sklearn.neighbors import KNeighborsClassifier") == {"sklearn"}

    def test_multiple_imports(self):
        code = "import pandas\nimport numpy\nfrom sklearn.metrics import accuracy_score"
        result = extract_imports(code)
        assert result == {"pandas", "numpy", "sklearn"}

    def test_stdlib_included(self):
        result = extract_imports("import os\nimport json\nimport sys")
        assert "os" in result

    def test_syntax_error_returns_empty(self):
        assert extract_imports("def (invalid") == set()

    def test_dotted_import(self):
        result = extract_imports("import numpy.linalg")
        assert result == {"numpy"}

    def test_empty_code(self):
        assert extract_imports("") == set()


# ---------------------------------------------------------------------------
# resolve_extra_packages
# ---------------------------------------------------------------------------


class TestResolveImportsViaLLM:
    """Tests for LLM-based import → PyPI resolution."""

    def setup_method(self):
        """Clear the session cache before each test."""
        import src.verification.sandbox_tool as mod
        mod._pip_resolution_cache.clear()

    @patch("src.verification.sandbox_tool.init_chat_model")
    def test_calls_llm_with_import_names(self, mock_init):
        mock_llm = MagicMock()
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = _PackageMapping(
            mappings={"fuzzywuzzy": "fuzzywuzzy", "msgpack": "msgpack"}
        )
        mock_init.return_value = mock_llm

        result = _resolve_imports_via_llm(["fuzzywuzzy", "msgpack"])

        assert result == {"fuzzywuzzy": "fuzzywuzzy", "msgpack": "msgpack"}
        mock_init.assert_called_once()
        mock_structured.invoke.assert_called_once()

    @patch("src.verification.sandbox_tool.init_chat_model")
    def test_caches_results_across_calls(self, mock_init):
        mock_llm = MagicMock()
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = _PackageMapping(
            mappings={"scipy": "scipy"}
        )
        mock_init.return_value = mock_llm

        _resolve_imports_via_llm(["scipy"])
        _resolve_imports_via_llm(["scipy"])  # second call — should use cache

        # LLM called only once despite two resolve calls.
        assert mock_structured.invoke.call_count == 1

    @patch("src.verification.sandbox_tool.init_chat_model")
    def test_fallback_on_llm_error(self, mock_init):
        mock_init.side_effect = RuntimeError("API key missing")

        result = _resolve_imports_via_llm(["fuzzywuzzy"])

        # Falls back to identity mapping for unknown imports.
        assert result == {"fuzzywuzzy": "fuzzywuzzy"}

    @patch("src.verification.sandbox_tool.init_chat_model")
    def test_missing_key_in_llm_response_uses_identity(self, mock_init):
        mock_llm = MagicMock()
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        # LLM returns mapping for fuzzywuzzy but omits msgpack.
        mock_structured.invoke.return_value = _PackageMapping(
            mappings={"fuzzywuzzy": "fuzzywuzzy"}
        )
        mock_init.return_value = mock_llm

        result = _resolve_imports_via_llm(["fuzzywuzzy", "msgpack"])

        assert result["fuzzywuzzy"] == "fuzzywuzzy"
        assert result["msgpack"] == "msgpack"  # fallback to identity


class TestResolveExtraPackages:
    """Tests for the full resolve_extra_packages pipeline (extract + filter + LLM)."""

    def setup_method(self):
        import src.verification.sandbox_tool as mod
        mod._pip_resolution_cache.clear()

    def test_no_extras_for_stdlib_packages(self):
        code = "import os\nimport json\nimport sys"
        assert resolve_extra_packages(code) == []

    @patch("src.verification.sandbox_tool._resolve_imports_via_llm")
    def test_sklearn_maps_to_scikit_learn(self, mock_resolve):
        mock_resolve.return_value = {"sklearn": "scikit-learn"}
        code = "from sklearn.neighbors import KNeighborsClassifier"
        result = resolve_extra_packages(code)
        assert result == ["scikit-learn"]
        mock_resolve.assert_called_once_with(["sklearn"])

    @patch("src.verification.sandbox_tool._resolve_imports_via_llm")
    def test_unknown_package_uses_llm_result(self, mock_resolve):
        mock_resolve.return_value = {"some_rare_package": "some_rare_package"}
        code = "import some_rare_package"
        result = resolve_extra_packages(code)
        assert result == ["some_rare_package"]

    @patch("src.verification.sandbox_tool._resolve_imports_via_llm")
    def test_mixed_stdlib_and_extra(self, mock_resolve):
        mock_resolve.return_value = {"scipy": "scipy"}
        code = "import json\nimport scipy\nimport os"
        result = resolve_extra_packages(code)
        assert result == ["scipy"]

    def test_empty_source(self):
        assert resolve_extra_packages("") == []


# ---------------------------------------------------------------------------
# sandbox_execute with extra_packages
# ---------------------------------------------------------------------------


class TestSandboxExecuteWithPackages:
    def test_no_extra_packages_uses_core(self):
        """Without extra_packages, uses core path (--network none)."""
        captured: list[str] = []

        def capture(*args, **kwargs):
            captured.extend(args[0])
            return _make_proc()

        with patch("subprocess.run", side_effect=capture):
            sandbox_execute("pass", extra_packages=None)

        assert "--network" in captured
        idx = captured.index("--network")
        assert captured[idx + 1] == "none"

    def test_extra_packages_triggers_install_phase(self):
        """With extra_packages, runs multiple docker commands (install + commit + run)."""
        calls = []

        def capture(*args, **kwargs):
            calls.append(args[0])
            return _make_proc()

        with patch("subprocess.run", side_effect=capture):
            sandbox_execute("pass", extra_packages=["scikit-learn"])

        # Should have at least 3 docker calls: run (install), commit, rm, run (execute), rmi
        docker_cmds = [c for c in calls if c and c[0] == "docker"]
        assert len(docker_cmds) >= 3


