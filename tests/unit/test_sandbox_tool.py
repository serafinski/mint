"""Unit tests for src/verification/sandbox_tool.py.

All tests mock subprocess.run — no Docker required.
Integration tests that actually invoke Docker are marked @pytest.mark.integration.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.verification.sandbox_tool import SANDBOX_IMAGE, sandbox_execute

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


