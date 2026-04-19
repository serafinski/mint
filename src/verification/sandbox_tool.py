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

from src.config import DOCKER_PREINSTALLED, SANDBOX_IMAGE

_BASE_PACKAGES = frozenset(sys.stdlib_module_names) | DOCKER_PREINSTALLED

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
    # Check cache — only query uncached imports.
    uncached = [name for name in import_names if name not in _pip_resolution_cache]
    if not uncached:
        return {name: _pip_resolution_cache[name] for name in import_names}

    try:
        from src.config import DEFAULT_LLM_MODEL

        llm = init_chat_model(DEFAULT_LLM_MODEL, temperature=0)
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


def _sandbox_execute_impl(
    code: str,
    timeout: int,
    extra_packages: list[str] | None,
    *,
    allow_network: bool,
) -> dict:
    """Core Docker execution without observability emission.

    Shared body between the event-emitting ``sandbox_execute`` and the
    silent variant ``sandbox_execute_silent`` — keeps them consistent.
    """
    if extra_packages:
        return _sandbox_execute_with_install(
            code, timeout, extra_packages, allow_network=allow_network,
        )
    return _sandbox_execute_core(code, timeout, network=allow_network)


def sandbox_execute(
    code: Annotated[str, Field(description="Python code snippet to execute in the sandbox")],
    timeout: Annotated[int, Field(default=10, description="Timeout in seconds")] = 10,
    extra_packages: list[str] | None = None,
    *,
    allow_network: bool = False,
) -> dict:
    """Execute a Python snippet in an isolated Docker container.

    Returns ``{"stdout": str, "stderr": str, "exit_code": int, "success": bool}``.

    When *extra_packages* is provided, a ``uv pip install`` step runs first
    (with network access), then the script runs with ``--network none``
    (unless *allow_network* is ``True``).

    When *allow_network* is ``True``, the script runs with ``--network bridge``
    instead of ``--network none``.  Default is ``False`` (offline) for safety.

    Raises nothing — all failures are captured in the return dict.

    Step 13.7 Batch C3: emits a ``sandbox.execute`` LangFuse event with
    execution metadata so programmatic (non-LangChain) sandbox invocations
    are discoverable in the UI. Use this variant for:

    - Generated SKILL runtime's manual tool-dispatch loop (``tests/evaluation/
      execution.py:617``) — the loop calls ``sandbox_execute`` OUTSIDE any
      LangChain runnable chain, so LangChain doesn't auto-trace it; the
      event is the only record.
    - Tier 1.5 import check (``src/verification/import_check.py``)
    - Tier 2 behavioral probes (``src/verification/behavioral.py``)
    - Coverage replay (``src/verification/coverage.py``)
    - CLI execution (``src/cli/runner.py``)

    For DeepAgent-dispatched invocations (adversarial Tier 3 + generated
    AGENT runtime via ``create_deep_agent(tools=[...])``), use
    ``sandbox_execute_silent`` instead — those are auto-traced by
    LangChain as ``type=TOOL`` observations with strictly more detail
    (full code, full stdout/stderr, precise latency, tool_call_id, LangGraph
    graph position). The event would be pure duplicate. See
    ``docs/langfuse_redundancy_audit_2026_04_19.md`` Fix #1.
    """
    result = _sandbox_execute_impl(code, timeout, extra_packages, allow_network=allow_network)

    # C3 span emission — best effort. Route through emit_langfuse_event
    # rather than lf.create_event directly so the trace_context fallback
    # applies: sandbox_execute is called from inside bind_tools LLMs and
    # DeepAgents where LangGraph's get_config() returns None, so without
    # the fallback the event lands as a top-level untagged trace
    # (Step 13.7 tag-propagation fix, 2026-04-19).
    try:
        from src.orchestrator.tracing import emit_langfuse_event

        emit_langfuse_event(
            None,
            "sandbox.execute",
            "DEFAULT" if result.get("success") else "WARNING",
            {
                "exit_code": result.get("exit_code"),
                "success": result.get("success"),
                "code_bytes": len(code) if code else 0,
                "timeout_s": timeout,
                "allow_network": allow_network,
                "extra_packages": list(extra_packages) if extra_packages else [],
                "stderr_tail": (result.get("stderr") or "")[-200:],
            },
        )
    except Exception:  # noqa: BLE001
        pass

    return result


def sandbox_execute_silent(
    code: Annotated[str, Field(description="Python code snippet to execute in the sandbox")],
    timeout: Annotated[int, Field(default=10, description="Timeout in seconds")] = 10,
    extra_packages: list[str] | None = None,
    *,
    allow_network: bool = False,
) -> dict:
    """Identical execution to ``sandbox_execute`` with NO observability event.

    Use when handing sandbox_execute as a tool to ``create_deep_agent(tools=[...])``
    or into a LangGraph graph: the DeepAgent's LangGraph runtime auto-traces
    every tool-call as a ``type=TOOL, name=sandbox_execute`` observation
    carrying strictly more detail than our ``sandbox.execute`` event:

    - Full code (not just ``code_bytes`` length)
    - Full stdout + full stderr (not ``stderr_tail[-200:]``)
    - Precise latency (wall-clock)
    - ``tool_call_id`` linking to the LLM's structured tool_call
    - LangGraph graph position (``langgraph_step``, ``langgraph_node``,
      ``langgraph_checkpoint_ns``)

    Emitting the event on top would produce a redundant child of the TOOL
    observation — same information, less detail. Using the silent variant
    here drops ~23% of ``sandbox.execute`` event volume per run with zero
    information loss.

    Call sites using this variant:
      - ``src/verification/adversarial.py``: DeepAgent prober's tool-call
      - ``tests/evaluation/execution.py`` (AGENT runtime): ``tools=[sandbox_execute_silent]``
        passed to ``create_deep_agent``

    DO NOT use for programmatic callers (Tier 1.5/2, coverage, CLI) or
    SKILL runtime's manual loop — those are NOT auto-traced, and the
    silent variant would leave them invisible. See
    ``docs/langfuse_redundancy_audit_2026_04_19.md`` Fix #1 for the
    empirical trail (38 TOOL observations mapped 1:1 to the redundant
    subset of the 162 ``sandbox.execute`` events observed in the
    longitudinal-shift probe).
    """
    return _sandbox_execute_impl(code, timeout, extra_packages, allow_network=allow_network)


def sandbox_execute_in_workspace(
    code: str,
    workspace_path: Path | str,
    timeout: int = 10,
    extra_packages: list[str] | None = None,
    *,
    allow_network: bool = False,
) -> dict:
    """Execute code in Docker with a host workspace mounted at ``/workspace``.

    Same as :func:`sandbox_execute` but mounts *workspace_path* read-write
    into the container so chain steps can pass state via files like
    ``/workspace/step_0_output.json``.

    This function is for programmatic use by the compositional executor —
    it is NOT exposed as an LLM tool (``sandbox_execute`` is the tool-facing
    function with a clean schema).
    """
    ws = Path(workspace_path)
    if extra_packages:
        return _sandbox_execute_with_install(
            code, timeout, extra_packages, workspace_path=ws,
            allow_network=allow_network,
        )

    return _sandbox_execute_core(code, timeout, network=allow_network, workspace_path=ws)


def _sandbox_execute_core(
    code: str, timeout: int, *, network: bool, workspace_path: Path | None = None,
) -> dict:
    """Run code in Docker, optionally with network access."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        script_path = f.name

    network_flag = "bridge" if network else "none"

    docker_args = [
        "docker", "run", "--rm",
        "--network", network_flag,
        "--memory", "128m",
        "--cpus", "0.5",
        "-v", f"{script_path}:/sandbox/script.py:ro",
    ]
    if workspace_path is not None:
        docker_args += ["-v", f"{workspace_path}:/workspace"]
    docker_args += [SANDBOX_IMAGE, "/sandbox/script.py"]

    try:
        proc = subprocess.run(
            docker_args,
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
    code: str, timeout: int, packages: list[str],
    *, workspace_path: Path | None = None, allow_network: bool = False,
) -> dict:
    """Install extra packages (with network), then run code (without network).

    Uses a two-phase approach:
    1. ``docker run`` with network to ``uv pip install`` packages into a committed layer
    2. ``docker run --network none`` to execute the script against that layer

    The ephemeral image is cleaned up after execution.
    """
    import uuid

    ephemeral_tag = f"jit-sandbox-eph-{uuid.uuid4().hex[:8]}"

    # Validate package names: only allow alphanumeric, hyphens, underscores,
    # dots, and version specifiers. Reject anything that looks suspicious.
    import re as _re

    _VALID_PKG = _re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?([<>=!~]+[A-Za-z0-9.*]+)?$")
    packages = [p for p in packages if _VALID_PKG.match(p)]
    if not packages:
        # All package names failed validation — fall back to the no-install
        # path while preserving the caller's network policy (Step 14.12:
        # silently dropping allow_network here would turn the
        # _VALID_PKG rejection into a covert offline-mode demotion).
        return sandbox_execute(code, timeout=timeout, allow_network=allow_network)

    # Phase 1: install packages (with network, 60s timeout)
    pip_args = ", ".join(repr(p) for p in packages)
    install_script = (
        "import subprocess; "
        f"subprocess.check_call(['uv', 'pip', 'install', '--system', '--no-cache', {pip_args}])"
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
                "stderr": f"uv pip install failed: {install_proc.stderr}",
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
            "stdout": "", "stderr": "Timeout during uv pip install",
            "exit_code": -1, "success": False,
        }
    except Exception as exc:
        subprocess.run(["docker", "rm", "-f", ephemeral_tag], capture_output=True)
        return {"stdout": "", "stderr": str(exc), "exit_code": -1, "success": False}

    # Phase 2: run the actual code against ephemeral image
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        script_path = f.name

    network_flag = "bridge" if allow_network else "none"
    docker_args = [
        "docker", "run", "--rm",
        "--network", network_flag,
        "--memory", "128m",
        "--cpus", "0.5",
        "--user", "sandbox",
        "-v", f"{script_path}:/sandbox/script.py:ro",
    ]
    if workspace_path is not None:
        docker_args += ["-v", f"{workspace_path}:/workspace"]
    docker_args += [ephemeral_tag, "/sandbox/script.py"]

    try:
        proc = subprocess.run(
            docker_args,
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
