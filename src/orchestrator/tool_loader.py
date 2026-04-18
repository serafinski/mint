"""Dynamic MCP tool loader — importlib-based loading and sandboxed execution.

Loads generated MCP tool source code via ``importlib`` dynamic import and
executes tool functions in sandboxed subprocesses.  No persistent FastMCP
server processes — no ports, no health checks, no zombie process risk.

The same loader is reused by Tier 2 behavioral testing in Iteration 3.
"""

from __future__ import annotations

import asyncio
import atexit
import importlib.util
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import textwrap
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from pydantic import BaseModel
from pydantic import Field as PydanticField  # aliased; generated-tool code also imports Field

from src.orchestrator.schema_extraction import extract_mcp_tool_signature
from src.registry.token_count import count_definition_tokens
from src.schemas.capability import CapabilityDescriptor, CapabilityType
from src.schemas.task import GeneratedArtifact

logger = logging.getLogger(__name__)

# Default timeout for subprocess execution (seconds)
_EXECUTE_TIMEOUT_S = 30


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class LoadedTool:
    """A dynamically loaded MCP tool."""

    module: ModuleType
    tool_fn: object  # callable — async function
    temp_path: Path
    tool_name: str


class ToolResult(BaseModel):
    """Result of executing a tool in a sandboxed subprocess."""

    success: bool = PydanticField(..., description="Whether the tool executed successfully")
    result: Any = PydanticField(default=None, description="Return value from the tool")
    error: str | None = PydanticField(default=None, description="Error message if execution failed")
    stdout: str = PydanticField(default="", description="Captured stdout")
    stderr: str = PydanticField(default="", description="Captured stderr")

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# ToolLoader
# ---------------------------------------------------------------------------


class ToolLoader:
    """Load generated MCP tools via importlib and execute in subprocesses."""

    def __init__(self) -> None:
        self._temp_dirs: list[Path] = []
        atexit.register(self.cleanup)

    def load_tool(self, artifact: GeneratedArtifact) -> LoadedTool:
        """Load a generated MCP tool from its source code.

        Args:
            artifact: A ``GeneratedArtifact`` with ``capability_type == MCP_TOOL``
                and non-None ``source_code``.

        Returns:
            A ``LoadedTool`` with the loaded module and tool function.

        Raises:
            ValueError: If the artifact is not an MCP tool or has no source code.
            RuntimeError: If the module cannot be loaded or no tool function is found.
        """
        if artifact.capability_type != CapabilityType.MCP_TOOL:
            raise ValueError(
                f"Expected MCP_TOOL artifact, got {artifact.capability_type.value}"
            )
        if not artifact.source_code:
            raise ValueError("Artifact has no source_code")

        # Write to temp directory
        temp_dir = Path(tempfile.mkdtemp(prefix="jit_mcp_"))
        self._temp_dirs.append(temp_dir)

        module_name = artifact.capability_name.replace("-", "_")
        if not module_name.isidentifier():
            raise ValueError(
                f"Capability name {artifact.capability_name!r} yields invalid "
                f"Python module name {module_name!r}"
            )
        file_path = temp_dir / f"{module_name}.py"
        file_path.write_text(artifact.source_code)

        # Load via importlib
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Failed to create module spec from {file_path}")

        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            raise RuntimeError(f"Failed to execute module {module_name}: {exc}") from exc

        # Find the tool function — look for async functions DEFINED in this module.
        # Filter by __module__ to skip coroutines imported from other packages
        # (e.g. `from asyncio import sleep`), which would otherwise be picked up
        # alphabetically before the actual tool function.
        tool_fn = None
        for attr_name in dir(module):
            if attr_name.startswith("_"):
                continue
            attr = getattr(module, attr_name)
            if (
                asyncio.iscoroutinefunction(attr)
                and getattr(attr, "__module__", None) == module.__name__
            ):
                tool_fn = attr
                break

        if tool_fn is None:
            raise RuntimeError(
                f"No async tool function found in generated module {module_name}"
            )

        return LoadedTool(
            module=module,
            tool_fn=tool_fn,
            temp_path=file_path,
            tool_name=artifact.capability_name,
        )

    def execute_tool(
        self,
        loaded: LoadedTool,
        inputs: dict,
        *,
        timeout: int = _EXECUTE_TIMEOUT_S,
    ) -> ToolResult:
        """Execute a loaded tool in a sandboxed subprocess.

        Writes a runner script that imports the tool module and calls the tool
        function with the given inputs, then captures the result as JSON.

        Args:
            loaded: A previously loaded tool from ``load_tool()``.
            inputs: Dictionary of keyword arguments to pass to the tool.
            timeout: Maximum execution time in seconds.

        Returns:
            A ``ToolResult`` with success status, result, and any errors.
        """
        # Build a runner script
        runner_code = textwrap.dedent(f"""\
            import asyncio
            import json
            import sys

            sys.path.insert(0, {str(loaded.temp_path.parent)!r})
            import {loaded.temp_path.stem} as tool_module

            async def _run():
                fn = None
                for attr_name in dir(tool_module):
                    if attr_name.startswith("_"):
                        continue
                    attr = getattr(tool_module, attr_name)
                    is_local = getattr(attr, "__module__", None) == tool_module.__name__
                    if asyncio.iscoroutinefunction(attr) and is_local:
                        fn = attr
                        break
                if fn is None:
                    print(json.dumps({{"error": "No async function found"}}))
                    return
                try:
                    result = await fn(**json.loads({json.dumps(inputs)!r}))
                    print(json.dumps({{"success": True, "result": result}}))
                except Exception as exc:
                    print(json.dumps({{"success": False, "error": str(exc)}}))

            asyncio.run(_run())
        """)

        # Use a unique runner filename per call to avoid races when execute_tool
        # is called concurrently on the same loaded tool (e.g., Tier 2 fan-out).
        runner_path = loaded.temp_path.parent / f"_runner_{uuid.uuid4().hex[:8]}.py"
        runner_path.write_text(runner_code)

        try:
            proc = subprocess.run(
                [sys.executable, str(runner_path)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(loaded.temp_path.parent),
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                error=f"Tool execution timed out after {timeout}s",
                stdout="",
                stderr="",
            )
        finally:
            runner_path.unlink(missing_ok=True)

        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        if proc.returncode != 0:
            return ToolResult(
                success=False,
                error=f"Subprocess exited with code {proc.returncode}: {stderr}",
                stdout=stdout,
                stderr=stderr,
            )

        # Parse JSON result from the last non-empty line of stdout.
        # Generated tools may call print() for debug output; the runner always
        # writes its JSON result as the final line.
        lines = [line for line in stdout.splitlines() if line.strip()]
        json_line = lines[-1] if lines else ""
        try:
            output = json.loads(json_line)
        except (json.JSONDecodeError, ValueError):
            return ToolResult(
                success=False,
                error=f"Failed to parse tool output as JSON: {stdout!r}",
                stdout=stdout,
                stderr=stderr,
            )

        # Guard against non-dict JSON (e.g., a bare integer or list)
        if not isinstance(output, dict):
            return ToolResult(
                success=False,
                error=f"Unexpected output format (expected JSON object): {stdout!r}",
                stdout=stdout,
                stderr=stderr,
            )

        if "error" in output and not output.get("success", False):
            return ToolResult(
                success=False,
                error=output["error"],
                result=None,
                stdout=stdout,
                stderr=stderr,
            )

        return ToolResult(
            success=output.get("success", True),
            result=output.get("result"),
            error=output.get("error"),
            stdout=stdout,
            stderr=stderr,
        )

    def register_tool(
        self,
        artifact: GeneratedArtifact,
        registry,  # SkillRegistry — not imported at module level to avoid circular deps
        *,
        description: str | None = None,
        domain: str = "generated",
        tags: list[str] | None = None,
    ) -> CapabilityDescriptor:
        """Register a generated MCP tool in the capability registry.

        Builds a ``CapabilityDescriptor`` from the artifact and calls
        ``registry.add()``.  Call this after a successful ``load_tool()``
        and ``execute_tool()`` to make the capability discoverable.

        Args:
            artifact: The generated artifact (must be MCP_TOOL type).
            registry: A ``SkillRegistry`` instance.
            description: Human-readable description. Defaults to the
                artifact's capability_name if not provided.
            domain: Domain tag for hierarchical embeddings.
            tags: Additional searchable tags.

        Returns:
            The registered ``CapabilityDescriptor``.
        """
        if artifact.capability_type != CapabilityType.MCP_TOOL:
            raise ValueError(
                f"register_tool only supports MCP_TOOL artifacts, "
                f"got {artifact.capability_type.value}"
            )

        # Populate input_schema / output_schema for Pillar 3 Merge (B1).
        # Priority order:
        #   1. Prefer artifact-provided schemas if the generator set them.
        #   2. Fall back to AST extraction from the generated source code,
        #      which walks the @mcp.tool() decorated async def and pulls
        #      parameter types + return-dict literal keys.
        # Never raises — AST extraction returns ({}, {}) on parse failure.
        input_schema: dict = dict(artifact.input_schema or {})
        output_schema: dict = dict(artifact.output_schema or {})
        if (not input_schema or not output_schema) and artifact.source_code:
            ast_inputs, ast_outputs = extract_mcp_tool_signature(artifact.source_code)
            if not input_schema:
                input_schema = dict(ast_inputs)
            if not output_schema:
                output_schema = dict(ast_outputs)

        # Forward domain / is_generic from artifact if populated (B1 Step 3.5f
        # evolutionary taxonomy). If the generator hasn't set them yet (None),
        # fall back to the caller-provided `domain` default and is_generic=False.
        effective_domain = artifact.domain if artifact.domain is not None else domain
        effective_is_generic = bool(artifact.is_generic)

        fallback_desc = artifact.capability_name.replace("-", " ").replace("_", " ")
        resolved_description = description or artifact.description or fallback_desc
        definition_token_cost = count_definition_tokens(
            artifact.model_copy(
                update={
                    "description": resolved_description,
                    "input_schema": input_schema,
                    "output_schema": output_schema,
                }
            )
        )
        descriptor = CapabilityDescriptor(
            name=artifact.capability_name,
            description=resolved_description,
            capability_type=CapabilityType.MCP_TOOL,
            domain=effective_domain,
            is_generic=effective_is_generic,
            tags=tags or [],
            input_schema=input_schema,
            output_schema=output_schema,
            definition_token_cost=definition_token_cost,
            metadata={
                "generated": "true",
                "source": "jit_pipeline",
                **({"source_code": artifact.source_code} if artifact.source_code else {}),
            },
        )
        registry.add(descriptor)
        logger.info(
            "Registered MCP tool %r in registry (domain=%s, is_generic=%s, "
            "input_schema=%d keys, output_schema=%d keys)",
            artifact.capability_name,
            effective_domain,
            effective_is_generic,
            len(input_schema),
            len(output_schema),
        )
        return descriptor

    def cleanup(self) -> None:
        """Remove all temporary directories created by this loader."""
        for temp_dir in self._temp_dirs:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:  # noqa: BLE001
                logger.debug("Failed to clean up temp dir: %s", temp_dir)
        self._temp_dirs.clear()
