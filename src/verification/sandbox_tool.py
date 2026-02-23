"""Docker-based sandbox execution for adversarial and behavioral verification.

IMPORTANT: Always use sandbox_execute for running untrusted code — never
sys.executable or ToolLoader.execute_tool, which run on the host.
"""

import subprocess
import tempfile
from pathlib import Path
from typing import Annotated

from pydantic import Field

SANDBOX_IMAGE = "jit-sandbox"


def sandbox_execute(
    code: Annotated[str, Field(description="Python code snippet to execute in the sandbox")],
    timeout: Annotated[int, Field(default=10, description="Timeout in seconds")] = 10,
) -> dict:
    """Execute a Python snippet in an isolated Docker container.

    Returns ``{"stdout": str, "stderr": str, "exit_code": int, "success": bool}``.

    The container runs with ``--network none`` so no outbound traffic is possible.
    The script is mounted read-only; the container filesystem is ephemeral (``--rm``).

    Raises nothing — all failures are captured in the return dict.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        script_path = f.name

    try:
        proc = subprocess.run(
            [
                "docker", "run", "--rm",
                "--network", "none",
                "--memory", "128m",
                "--cpus", "0.5",
                "-v", f"{script_path}:/sandbox/script.py:ro",
                SANDBOX_IMAGE,
                "/sandbox/script.py",  # ENTRYPOINT is already `python`
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
            "success": proc.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Timeout", "exit_code": -1, "success": False}
    finally:
        Path(script_path).unlink(missing_ok=True)
