"""Docker-based sandbox execution for adversarial and behavioral verification.

IMPORTANT: Always use sandbox_execute for running untrusted code — never
sys.executable or ToolLoader.execute_tool, which run on the host.
"""

import ast
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Annotated

from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SANDBOX_IMAGE = "jit-sandbox"

# Packages that never need pip install: Python stdlib (resolved programmatically)
# plus the minimal set pre-installed in the sandbox Dockerfile (mcp, pydantic).
_DOCKER_PREINSTALLED = frozenset({"mcp", "pydantic"})
_BASE_PACKAGES = frozenset(sys.stdlib_module_names) | _DOCKER_PREINSTALLED

# Session-level cache: avoids repeated LLM calls for the same import names.
_pip_resolution_cache: dict[str, str] = {}


# ---------------------------------------------------------------------------
# LLM-based import → PyPI resolution
# ---------------------------------------------------------------------------


class _PackageMapping(BaseModel):
    """LLM response: mapping from Python import names to PyPI package names."""
    mappings: dict[str, str] = Field(
        description="Dict mapping each Python import name to its PyPI package name"
    )


def _resolve_imports_via_llm(import_names: list[str]) -> dict[str, str]:
    """Ask an LLM to map Python import names to PyPI package names.

    Returns a dict like ``{"sklearn": "scikit-learn", "scipy": "scipy"}``.
    Results are cached in ``_pip_resolution_cache`` for the session.
    Falls back to identity mapping (import name = pip name) on any error.
    """
    # Check cache first — only query uncached imports.
    uncached = [name for name in import_names if name not in _pip_resolution_cache]
    if not uncached:
        return {name: _pip_resolution_cache[name] for name in import_names}

    try:
        llm = init_chat_model("openai:gpt-5-mini", temperature=0)
        structured_llm = llm.with_structured_output(_PackageMapping, method="function_calling")

        prompt = (
            "Map each Python import name to its correct PyPI package name "
            "(the name you would use with `pip install`). "
            "If the import name is the same as the PyPI name, repeat it. "
            "Examples: sklearn → scikit-learn, cv2 → opencv-python, "
            "PIL → Pillow, yaml → pyyaml, scipy → scipy.\n\n"
            f"Import names to map: {uncached}"
        )

        result: _PackageMapping = structured_llm.invoke(prompt)

        # Update cache with LLM results.
        for name in uncached:
            pip_name = result.mappings.get(name, name)
            _pip_resolution_cache[name] = pip_name

    except Exception as exc:
        logger.warning(
            "LLM package resolution failed for imports %s: %s. "
            "Falling back to identity mapping (import name = pip name).",
            list(uncached), exc,
        )
        for name in uncached:
            _pip_resolution_cache[name] = name

    return {name: _pip_resolution_cache[name] for name in import_names}


def extract_imports(source_code: str) -> set[str]:
    """Extract top-level module names from import statements using AST.

    Returns the set of root module names (e.g., ``sklearn`` from
    ``from sklearn.neighbors import KNeighborsClassifier``).
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return set()

    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module.split(".")[0])
    return modules


def resolve_extra_packages(source_code: str) -> list[str]:
    """Determine pip packages needed beyond the sandbox base set.

    Parses imports from *source_code*, filters out stdlib and pre-installed
    packages, and uses an LLM to map import names to PyPI package names.
    Results are cached for the session to avoid repeated LLM calls.
    """
    imports = extract_imports(source_code)
    non_base = sorted(mod for mod in imports if mod not in _BASE_PACKAGES)
    if not non_base:
        return []

    mapping = _resolve_imports_via_llm(non_base)
    return [mapping[mod] for mod in non_base]


def sandbox_execute(
    code: Annotated[str, Field(description="Python code snippet to execute in the sandbox")],
    timeout: Annotated[int, Field(default=10, description="Timeout in seconds")] = 10,
    extra_packages: list[str] | None = None,
) -> dict:
    """Execute a Python snippet in an isolated Docker container.

    Returns ``{"stdout": str, "stderr": str, "exit_code": int, "success": bool}``.

    When *extra_packages* is provided, a ``pip install`` step runs first
    (with network access), then the script runs with ``--network none``.

    Raises nothing — all failures are captured in the return dict.
    """
    if extra_packages:
        return _sandbox_execute_with_install(code, timeout, extra_packages)

    return _sandbox_execute_core(code, timeout, network=False)


def _sandbox_execute_core(code: str, timeout: int, *, network: bool) -> dict:
    """Run code in Docker, optionally with network access."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        script_path = f.name

    network_flag = "bridge" if network else "none"

    try:
        proc = subprocess.run(
            [
                "docker", "run", "--rm",
                "--network", network_flag,
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
    except Exception as exc:  # Docker not found, daemon down, permission error, etc.
        return {"stdout": "", "stderr": str(exc), "exit_code": -1, "success": False}
    finally:
        Path(script_path).unlink(missing_ok=True)


def _sandbox_execute_with_install(
    code: str, timeout: int, packages: list[str]
) -> dict:
    """Install extra packages (with network), then run code (without network).

    Uses a two-phase approach:
    1. ``docker run`` with network to pip install packages into a committed layer
    2. ``docker run --network none`` to execute the script against that layer

    The ephemeral image is cleaned up after execution.
    """
    import uuid

    ephemeral_tag = f"jit-sandbox-eph-{uuid.uuid4().hex[:8]}"

    # Phase 1: install packages (with network, 60s timeout)
    pip_args = ", ".join(repr(p) for p in packages)
    install_script = (
        "import subprocess; "
        f"subprocess.check_call(['pip', 'install', '--no-cache-dir', {pip_args}])"
    )
    try:
        install_proc = subprocess.run(
            [
                "docker", "run",
                "--name", ephemeral_tag,
                "--network", "bridge",
                "--memory", "256m",
                "--cpus", "0.5",
                "--user", "root",
                SANDBOX_IMAGE,
                "-c", install_script,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if install_proc.returncode != 0:
            # Cleanup container
            subprocess.run(["docker", "rm", "-f", ephemeral_tag], capture_output=True)
            return {
                "stdout": "",
                "stderr": f"pip install failed: {install_proc.stderr}",
                "exit_code": install_proc.returncode,
                "success": False,
            }

        # Commit container to ephemeral image
        subprocess.run(
            ["docker", "commit", ephemeral_tag, ephemeral_tag],
            capture_output=True,
            timeout=30,
        )
        # Remove the stopped container
        subprocess.run(["docker", "rm", "-f", ephemeral_tag], capture_output=True)

    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "rm", "-f", ephemeral_tag], capture_output=True)
        return {
            "stdout": "", "stderr": "Timeout during pip install",
            "exit_code": -1, "success": False,
        }
    except Exception as exc:
        subprocess.run(["docker", "rm", "-f", ephemeral_tag], capture_output=True)
        return {"stdout": "", "stderr": str(exc), "exit_code": -1, "success": False}

    # Phase 2: run the actual code (no network) against ephemeral image
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
                ephemeral_tag,
                "/sandbox/script.py",
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
    except Exception as exc:
        return {"stdout": "", "stderr": str(exc), "exit_code": -1, "success": False}
    finally:
        Path(script_path).unlink(missing_ok=True)
        # Cleanup ephemeral image
        subprocess.run(["docker", "rmi", "-f", ephemeral_tag], capture_output=True)
