"""Unit tests for src/verification/sandbox_tool.py.

All tests mock subprocess.run — no Docker required.
Integration tests that actually invoke Docker are marked @pytest.mark.integration.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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

    @patch("src.verification.sandbox_tool.init_chat_model")
    def test_empty_mapping_response_uses_identity(self, mock_init, caplog):
        mock_llm = MagicMock()
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = _PackageMapping()
        mock_init.return_value = mock_llm

        result = _resolve_imports_via_llm(["numpy", "pandas"])

        assert result == {"numpy": "numpy", "pandas": "pandas"}
        assert "returned an empty mapping" in caplog.text

    @patch("src.verification.sandbox_tool.init_chat_model")
    def test_partial_mapping_logs_missing_imports(self, mock_init, caplog):
        mock_llm = MagicMock()
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = _PackageMapping(
            mappings={"numpy": "numpy"}
        )
        mock_init.return_value = mock_llm

        result = _resolve_imports_via_llm(["numpy", "pandas"])

        assert result == {"numpy": "numpy", "pandas": "pandas"}
        assert "returned a partial mapping" in caplog.text
        assert "pandas" in caplog.text


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
        """With extra_packages, runs the 5-command install→commit→rm→exec→rmi sequence.

        Tightened 2026-04-19: previously only asserted ``len >= 3`` which would
        pass even if commit/rm/rmi were silently dropped. The install path in
        ``_sandbox_execute_with_install`` must perform all five docker operations
        to commit the pip-installed layer and clean up the ephemeral image.
        """
        calls = []

        def capture(*args, **kwargs):
            calls.append(args[0])
            return _make_proc()

        with patch("subprocess.run", side_effect=capture):
            sandbox_execute("pass", extra_packages=["scikit-learn"])

        docker_cmds = [c for c in calls if c and c[0] == "docker"]

        # Exact 5-command sequence (order-sensitive):
        #   1. docker run <ephemeral>         (pip install phase)
        #   2. docker commit <ephemeral>
        #   3. docker rm -f <ephemeral>       (after commit)
        #   4. docker run --rm <ephemeral>    (execution phase)
        #   5. docker rmi -f <ephemeral>      (cleanup, via finally)
        assert len(docker_cmds) == 5, (
            f"expected 5 docker commands, got {len(docker_cmds)}: {docker_cmds}"
        )

        subcommands = [c[1] for c in docker_cmds]
        assert subcommands == ["run", "commit", "rm", "run", "rmi"], (
            f"unexpected docker subcommand sequence: {subcommands}"
        )

        # The install phase must run with network (bridge); exec phase without.
        install_cmd = docker_cmds[0]
        exec_cmd = docker_cmds[3]
        assert (
            "--network" in install_cmd
            and install_cmd[install_cmd.index("--network") + 1] == "bridge"
        )
        assert (
            "--network" in exec_cmd
            and exec_cmd[exec_cmd.index("--network") + 1] == "none"
        )


# ---------------------------------------------------------------------------
# Step 14.12 — allow_network plumbing
# ---------------------------------------------------------------------------


class TestAllowNetworkFlag:
    """The ``allow_network`` kwarg must toggle Docker's --network flag."""

    def test_allow_network_true_passes_bridge(self):
        """allow_network=True → ``--network bridge`` on the core path."""
        captured: list[str] = []

        def capture(*args, **kwargs):
            captured.extend(args[0])
            return _make_proc()

        with patch("subprocess.run", side_effect=capture):
            sandbox_execute("pass", allow_network=True)

        assert "--network" in captured
        idx = captured.index("--network")
        assert captured[idx + 1] == "bridge"

    def test_allow_network_false_passes_none(self):
        """Default (offline): ``--network none``. Regression guard for the
        silent-revert scenario — if the flag defaults change, this fires.
        """
        captured: list[str] = []

        def capture(*args, **kwargs):
            captured.extend(args[0])
            return _make_proc()

        with patch("subprocess.run", side_effect=capture):
            sandbox_execute("pass", allow_network=False)

        assert "--network" in captured
        idx = captured.index("--network")
        assert captured[idx + 1] == "none"

    def test_allow_network_with_extra_packages_bridge_on_exec_phase(self):
        """When extra_packages are installed, Phase 1 (install) always uses
        bridge; Phase 2 (exec) honors allow_network.
        """
        calls: list[list[str]] = []

        def capture(*args, **kwargs):
            calls.append(list(args[0]))
            return _make_proc()

        with patch("subprocess.run", side_effect=capture):
            sandbox_execute("pass", extra_packages=["numpy"], allow_network=True)

        # Find the final "docker run --rm" (execute phase) — the one
        # passing --rm, not the install phase which uses --name.
        exec_runs = [
            c for c in calls if c and c[0] == "docker" and "--rm" in c
        ]
        assert exec_runs, "exec phase docker run missing"
        exec_cmd = exec_runs[-1]
        idx = exec_cmd.index("--network")
        assert exec_cmd[idx + 1] == "bridge"


# ---------------------------------------------------------------------------
# Security audit finding #6 — PEP 508 requirement validation (2026-04-19)
# ---------------------------------------------------------------------------


class TestPep508RequirementValidation:
    """The pre-fix regex false-rejected legitimate PEP 508 forms (compound
    versions, extras, environment markers) and fell through to a no-install
    ``sandbox_execute`` when ALL entries were rejected, masking the cause as
    ``ModuleNotFoundError`` in the generated code. Post-fix uses
    ``packaging.requirements.Requirement`` and loud-fails with a
    ``pip_requirement_rejected`` field instead of silently falling through.
    """

    @pytest.mark.parametrize(
        "req",
        [
            "pandas",
            "pandas>=2.0",
            "pandas>=2.0,<3",                  # compound version — regex rejected
            "pydantic[email]",                 # extras — regex rejected
            "scikit-learn",
            "Flask-Login",
            "zope.interface",
            "package~=1.2",
            'requests; python_version>="3.8"', # env marker — regex rejected
            "pkg[a,b]>=1.0",                   # extras + version — regex rejected
            "numpy==1.24.*",
        ],
    )
    def test_valid_pep508_forms_accepted(self, req):
        """Each documented PEP 508 form must parse cleanly and reach the
        install phase."""
        calls = []

        def capture(*args, **kwargs):
            calls.append(args[0])
            return _make_proc()

        with patch("subprocess.run", side_effect=capture):
            sandbox_execute("pass", extra_packages=[req])

        docker_cmds = [c for c in calls if c and c[0] == "docker"]
        # Valid requirement → full 5-command install sequence fires.
        assert len(docker_cmds) == 5, (
            f"{req!r} should have parsed and triggered install, got {len(docker_cmds)} "
            f"docker commands"
        )

    @pytest.mark.parametrize(
        "req",
        [
            "!@#$",
            "",
            " ",
            "package name with spaces",
        ],
    )
    def test_genuinely_malformed_rejected_loudly(self, req):
        """Malformed entries: skip install, return
        pip_requirement_rejected dict, don't silently fall through."""
        calls = []

        def capture(*args, **kwargs):
            calls.append(args[0])
            return _make_proc()

        with patch("subprocess.run", side_effect=capture):
            result = sandbox_execute("pass", extra_packages=[req])

        # No docker commands — loud-fail before any sandbox call.
        assert not any(c and c[0] == "docker" for c in calls), (
            f"malformed {req!r} should NOT have reached sandbox_execute"
        )
        assert result["success"] is False
        assert "pip_requirement_rejected" in result
        assert result["pip_requirement_rejected"]
        # The rejected tuple should carry the malformed string.
        assert any(r[0] == req for r in result["pip_requirement_rejected"])

    def test_partial_rejection_installs_good_subset(self):
        """When SOME entries are malformed, install proceeds on the good
        subset. A warning is logged but the call still runs."""
        calls = []

        def capture(*args, **kwargs):
            calls.append(args[0])
            return _make_proc()

        with patch("subprocess.run", side_effect=capture):
            result = sandbox_execute(
                "pass",
                extra_packages=["pandas>=2.0", "!@#malformed"],
            )

        docker_cmds = [c for c in calls if c and c[0] == "docker"]
        # Install still fires (good subset is non-empty).
        assert len(docker_cmds) == 5

        # Install command's argv should carry pandas but not the malformed entry.
        install_cmd = docker_cmds[0]
        install_script = install_cmd[-1]
        assert "pandas>=2.0" in install_script
        assert "!@#malformed" not in install_script


class TestImportCheckSurfacesPipRequirementRejection:
    """``src/verification/import_check.py`` must recognise the
    ``pip_requirement_rejected`` key in the sandbox result and raise it as a
    dedicated VerificationReport finding category, so the gate attributes
    the failure to the requirement string rather than mis-blaming the
    source as ``import_error`` / ``wrong_pip_package``."""

    def test_rejected_requirement_surfaced_as_finding(self):
        from src.schemas.capability import CapabilityType
        from src.schemas.task import GeneratedArtifact
        from src.schemas.verification import Severity
        from src.verification.import_check import _check_mcp_tool

        artifact = GeneratedArtifact(
            capability_name="net-tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code="async def run(): return {}",
            pip_packages=["!@#malformed"],
        )

        with patch("src.verification.sandbox_tool.sandbox_execute") as mock_sbx:
            mock_sbx.return_value = {
                "stdout": "",
                "stderr": "All pip requirements rejected as malformed",
                "exit_code": -1,
                "success": False,
                "pip_requirement_rejected": [("!@#malformed", "parse error")],
            }

            findings = _check_mcp_tool(artifact, allow_network=False)

        assert len(findings) == 1
        f = findings[0]
        assert f.category == "pip_requirement_rejected"
        assert f.severity == Severity.CRITICAL
        assert "!@#malformed" in f.message


class TestSandboxExecuteInWorkspaceLoudFailsOnInvalidPackages:
    """Security audit finding #14 (2026-04-20) — confirmation test that
    the workspace-mount-drop fallback was closed as a side-effect of the
    #6 fix. Pre-fix, ``sandbox_execute_in_workspace`` with all-invalid
    extra_packages silently fell through to
    ``sandbox_execute(code, timeout, allow_network)`` — **without** the
    workspace mount — causing downstream code to fail with
    FileNotFoundError inside the sandbox. Post-#6 the path returns a
    ``pip_requirement_rejected`` loud-fail dict instead. This test locks
    that invariant so a future refactor can't re-introduce the silent
    fall-through."""

    def test_all_invalid_packages_return_loud_fail_not_workspace_drop(self, tmp_path):
        from src.verification.sandbox_tool import sandbox_execute_in_workspace

        calls = []

        def capture(*args, **kwargs):
            calls.append(args[0])
            return _make_proc()

        with patch("subprocess.run", side_effect=capture):
            result = sandbox_execute_in_workspace(
                code="import pandas; print('should never run')",
                workspace_path=tmp_path,
                extra_packages=["this is not a valid requirement!!!"],
            )

        # Loud-fail path: no docker commands, pip_requirement_rejected set.
        assert not any(c and c[0] == "docker" for c in calls), (
            "Pre-#6, all-invalid packages silently dropped workspace "
            "and called sandbox_execute(code, ...). Post-#6, no docker "
            "call should fire at all — the loud-fail dict is returned "
            "before any sandbox execution."
        )
        assert result.get("pip_requirement_rejected"), (
            "Missing pip_requirement_rejected — the #14 regression guard "
            "requires the loud-fail dict shape."
        )
        assert result["success"] is False
