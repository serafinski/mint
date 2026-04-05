"""Pipeline execution with Rich live progress for the MINT CLI."""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from rich.panel import Panel

from src.cli.display import (
    STAGE_MAP,
    console,
    err_console,
    show_pipeline_result,
    show_plan_result,
)

_FILE_SIZE_LIMIT_BYTES = 10 * 1024 * 1024  # 10 MB
_AUTO_OPTIMIZER_THRESHOLD = 20


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------


def _load_env() -> None:
    from dotenv import load_dotenv

    root = Path(__file__).parent.parent.parent
    load_dotenv(root / ".env")


def _data_dir() -> Path:
    root = Path(__file__).parent.parent.parent
    d = root / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


_LAST_RUN_FILE = "last_run.json"


def _save_last_run(
    task: str,
    files: list[Path] | None,
    params: list[str] | None,
    skip_verification: bool,
    no_optimizer: bool,
    enable_optimizer: bool,
    timeout: int | None,
) -> None:
    """Persist last run config for `mint retry`."""
    data = {
        "task": task,
        "files": [str(f) for f in files] if files else [],
        "params": params or [],
        "skip_verification": skip_verification,
        "no_optimizer": no_optimizer,
        "enable_optimizer": enable_optimizer,
        "timeout": timeout,
    }
    path = _data_dir() / _LAST_RUN_FILE
    path.write_text(json.dumps(data, indent=2))


def load_last_run() -> dict | None:
    """Load last run config. Returns None if no previous run."""
    path = _data_dir() / _LAST_RUN_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.WARNING,
        stream=sys.stderr,
    )
    # Third-party loggers: fully silence (we never want their output)
    for noisy in (
        "opentelemetry",
        "urllib3",
        "httpcore",
        "httpx",
        "openai",
        "chromadb",
    ):
        logging.getLogger(noisy).setLevel(logging.CRITICAL)
    # Pipeline loggers: suppress INFO/WARNING noise but keep ERROR+
    for internal in (
        "src.orchestrator",
        "src.verification",
        "src.optimizer",
        "src.registry",
    ):
        logging.getLogger(internal).setLevel(logging.ERROR)


def _parse_params(raw: list[str] | None) -> dict[str, str]:
    params: dict[str, str] = {}
    if not raw:
        return params
    for p in raw:
        if "=" not in p:
            err_console.print(f"[red]--param must be KEY=VALUE, got: {p!r}[/red]")
            raise SystemExit(1)
        k, _, v = p.partition("=")
        params[k.strip()] = v.strip()
    return params


_BINARY_EXTENSIONS = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".webp",
        ".ico",
        ".pdf",
        ".zip",
        ".gz",
        ".tar",
        ".bz2",
        ".xz",
        ".7z",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".mp3",
        ".mp4",
        ".wav",
        ".avi",
        ".mov",
        ".mkv",
        ".parquet",
        ".avro",
        ".orc",
        ".pickle",
        ".pkl",
    }
)


def _resolve_files(paths: list[Path] | None) -> list[Path]:
    resolved: list[Path] = []
    if not paths:
        return resolved
    for fp in paths:
        if not fp.exists():
            err_console.print(f"[red]File not found: {fp}[/red]")
            raise SystemExit(1)
        if fp.suffix.lower() in _BINARY_EXTENSIONS:
            err_console.print(
                f"[red]Binary file not supported: {fp.name}[/red]\n"
                "MINT can only process text files "
                "(CSV, JSON, TXT, etc.)."
            )
            raise SystemExit(1)
        resolved.append(fp.resolve())
    return resolved


def _describe_file(fp: Path) -> str:
    """Generate a structural summary of a file for prompt augmentation."""
    suffix = fp.suffix.lower()
    size = fp.stat().st_size
    size_str = (
        f"{size}B"
        if size < 1024
        else f"{size / 1024:.0f}KB"
        if size < 1024 * 1024
        else f"{size / (1024 * 1024):.1f}MB"
    )

    try:
        content = fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return f"  {fp.name} ({size_str})"

    lines = content.splitlines()

    if suffix == ".csv":
        header = lines[0] if lines else ""
        columns = [c.strip().strip('"') for c in header.split(",")]
        n_rows = max(len(lines) - 1, 0)
        sample = lines[1] if len(lines) > 1 else ""
        desc = f"  {fp.name} ({size_str}, CSV, {n_rows} rows)\n"
        desc += f"    Columns: {', '.join(columns)}\n"
        if sample:
            desc += f"    Sample row: {sample[:200]}"
        return desc

    if suffix == ".json":
        import json as json_mod

        try:
            data = json_mod.loads(content)
            if isinstance(data, list) and data:
                keys = list(data[0].keys()) if isinstance(data[0], dict) else []
                desc = f"  {fp.name} ({size_str}, JSON array, {len(data)} items)\n"
                if keys:
                    desc += f"    Fields: {', '.join(keys)}"
                return desc
            elif isinstance(data, dict):
                keys = list(data.keys())
                desc = f"  {fp.name} ({size_str}, JSON object)\n"
                desc += f"    Keys: {', '.join(keys[:20])}"
                return desc
        except (json_mod.JSONDecodeError, AttributeError):
            pass
        return f"  {fp.name} ({size_str}, JSON)"

    if suffix == ".txt":
        n_words = len(content.split())
        return f"  {fp.name} ({size_str}, text, {n_words} words, {len(lines)} lines)"

    return f"  {fp.name} ({size_str})"


def _build_task_prompt(task: str, file_paths: list[Path], params: dict[str, str]) -> str:
    prompt = task
    if file_paths:
        prompt += "\n\nInput files:\n"
        for fp in file_paths:
            prompt += f"- {fp}\n"
        prompt += "\nFile structure:\n"
        for fp in file_paths:
            prompt += _describe_file(fp) + "\n"
    if params:
        prompt += "\nParameters:\n"
        for k, v in params.items():
            prompt += f"- {k}: {v}\n"
    return prompt


def _check_docker() -> bool:
    """Check if Docker is running. Returns True if available."""
    import subprocess

    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _build_header(task: str, file_paths: list[Path]) -> str:
    """Build panel content with task description and file names."""
    # Split off "Additional context:" hint if present (from retry --hint)
    if "\n\nAdditional context:" in task:
        main_task, hint = task.split("\n\nAdditional context:", 1)
        lines = [f"[bold]{main_task.strip()}[/bold]"]
        lines.append(f"[cyan]Hint:[/cyan] {hint.strip()}")
    else:
        lines = [f"[bold]{task}[/bold]"]
    if file_paths:
        for fp in file_paths:
            lines.append(f"[dim]  \u2514 {fp.name}[/dim]")
    return "\n".join(lines)


def _langfuse_config() -> dict:
    try:
        from langfuse.langchain import CallbackHandler as LangFuseCallbackHandler

        return {"callbacks": [LangFuseCallbackHandler()]}
    except Exception:
        return {}


def _create_infra(*, seed: bool = True) -> tuple[Any, Any, Any]:
    """Create registry, loader, and metrics store."""
    from src.orchestrator.tool_loader import ToolLoader
    from src.registry.metrics import MetricsStore
    from src.registry.store import SkillRegistry, seed_registry

    data = _data_dir()
    registry = SkillRegistry(persist_directory=str(data / "cli_chroma"))
    if seed:
        seed_registry(registry)
    loader = ToolLoader()
    metrics_store = MetricsStore(db_path=str(data / "cli_metrics.duckdb"))
    return registry, loader, metrics_store


# ---------------------------------------------------------------------------
# Artifact selection
# ---------------------------------------------------------------------------


def _select_artifact(state: Any) -> Any | None:
    _get = state.get if isinstance(state, dict) else lambda k, d=None: getattr(state, k, d)
    artifacts = _get("generated_artifacts", []) or []
    verification_results = _get("verification_results", []) or []

    if not artifacts:
        return None

    for art in artifacts:
        for report in verification_results:
            if report.capability_name == art.capability_name and report.overall_passed:
                return art

    return artifacts[-1]


# ---------------------------------------------------------------------------
# MCP_TOOL execution
# ---------------------------------------------------------------------------


def _match_files_to_params(
    source_code: str, file_paths: list[Path], sandbox_paths: list[str]
) -> dict[str, str]:
    """Match input files to tool parameters using AST + keyword overlap.

    Strategy (same as evaluation harness):
    1. Extract parameter names from the tool's source code via AST
    2. Identify path-like parameters (contain path/file/csv/json/txt etc.)
    3. Match each param to the best fixture by keyword overlap
    4. Fall back to semantic keys from filenames for unmatched params
    """
    from src.verification.behavioral import extract_parameter_schema

    schema = extract_parameter_schema(source_code)
    param_names = list(schema.keys())

    _PATH_KEYWORDS = ("path", "file", "csv", "json", "txt", "log", "doc")
    path_params = [p for p in param_names if any(kw in p.lower() for kw in _PATH_KEYWORDS)]

    inputs: dict[str, str] = {}
    unmatched_files = list(zip(file_paths, sandbox_paths))

    # Match path params to files by keyword overlap
    for param in path_params:
        param_lower = param.lower()
        best_idx, best_score = -1, 0
        for idx, (fp, _sp) in enumerate(unmatched_files):
            stem = fp.stem.lower()
            score = sum(1 for kw in stem.split("_") if kw in param_lower)
            # Also check extension match (e.g. "json" in param)
            ext = fp.suffix.lstrip(".").lower()
            if ext in param_lower:
                score += 1
            if score > best_score:
                best_score = score
                best_idx = idx
        if best_idx >= 0 and best_score > 0:
            inputs[param] = unmatched_files[best_idx][1]
            unmatched_files.pop(best_idx)

    # Assign remaining path params by position
    remaining_params = [p for p in path_params if p not in inputs]
    for param, (_fp, sp) in zip(remaining_params, unmatched_files):
        inputs[param] = sp

    # If no path params found, use semantic keys from filenames
    if not inputs:
        for fp, sp in zip(file_paths, sandbox_paths):
            stem = fp.stem.split("_", 1)[-1] if "_" in fp.stem else fp.stem
            ext = fp.suffix.lstrip(".")
            inputs[f"{stem}_{ext}_path"] = sp

    # Always add positional fallbacks for the runtime fuzzy matcher
    for i, (_fp, sp) in enumerate(zip(file_paths, sandbox_paths)):
        key = f"file_path_{i}"
        if key not in inputs:
            inputs[key] = sp

    return inputs


def _execute_mcp_tool(artifact: Any, file_paths: list[Path], params: dict) -> dict:
    from src.verification.behavioral import _extract_function_name
    from src.verification.sandbox_tool import resolve_extra_packages, sandbox_execute

    source_code = getattr(artifact, "source_code", None)
    if not source_code:
        return {}

    func_name = _extract_function_name(source_code) or artifact.capability_name.replace("-", "_")

    fixture_preamble_lines: list[str] = []
    sandbox_paths: list[str] = []

    for i, fp in enumerate(file_paths):
        if not fp.exists():
            sandbox_paths.append("")
            continue
        size = fp.stat().st_size
        if size > _FILE_SIZE_LIMIT_BYTES:
            err_console.print(
                f"[yellow]Warning: {fp.name} is {size // (1024 * 1024)}MB — "
                "large files may cause sandbox issues[/yellow]"
            )
        content = fp.read_text(encoding="utf-8", errors="replace")
        sandbox_path = f"/tmp/fixture_{i}{fp.suffix}"
        sandbox_paths.append(sandbox_path)
        fixture_preamble_lines.append(
            f"import pathlib as _pl\n_pl.Path({sandbox_path!r}).write_text({content!r})"
        )

    # Match files to tool params using AST extraction + keyword overlap
    fixture_inputs = _match_files_to_params(source_code, file_paths, sandbox_paths)
    all_inputs = {**fixture_inputs, **params}
    fixture_preamble = "\n".join(fixture_preamble_lines)
    sandbox_code = f"""
import asyncio, json, inspect
__name__ = "not_main"
{fixture_preamble}
{source_code}

async def _run():
    try:
        _all_inputs = {repr(all_inputs)}
        _sig = inspect.signature({func_name})
        _valid = set(_sig.parameters.keys())
        _kwargs = {{k: v for k, v in _all_inputs.items() if k in _valid}}
        _unmatched = _valid - set(_kwargs)
        _unused = {{k: v for k, v in _all_inputs.items() if k not in _kwargs}}
        def _words(s):
            return set(w.rstrip("s") for w in s.lower().replace("_", " ").split() if len(w) > 2)
        for _p in _unmatched:
            _pw = _words(_p)
            best, best_n = None, 0
            for _ek, _ev in _unused.items():
                n = len(_pw & _words(_ek))
                if n > best_n:
                    best, best_n = (_ek, _ev), n
            if best_n > 0:
                _kwargs[_p] = best[1]
        result = await {func_name}(**_kwargs)
        print(json.dumps({{"result": result}}))
    except Exception as e:
        print(json.dumps({{"error": str(e)}}))

asyncio.run(_run())
"""

    extra_pkgs = resolve_extra_packages(source_code)
    exec_timeout = 60 if extra_pkgs else 30
    result = sandbox_execute(sandbox_code, timeout=exec_timeout, extra_packages=extra_pkgs or None)

    if not result.get("success") or not result.get("stdout"):
        stderr = (result.get("stderr") or "")[:300]
        if "Cannot connect to the Docker daemon" in stderr or "docker" in stderr.lower():
            err_console.print("[red]Docker is not running. Please start Docker and retry.[/red]")
        elif stderr:
            err_console.print(f"[yellow]Execution failed: {stderr}[/yellow]")
        return {}

    try:
        parsed = json.loads(result["stdout"].strip().splitlines()[-1])
        if "error" in parsed:
            err_console.print(f"[yellow]Tool error: {str(parsed['error'])[:300]}[/yellow]")
            return {}
        return parsed.get("result", {}) or {}
    except (json.JSONDecodeError, KeyError, IndexError):
        err_console.print("[yellow]Could not parse tool output.[/yellow]")
        return {}


# ---------------------------------------------------------------------------
# SKILL / AGENT execution
# ---------------------------------------------------------------------------


def _execute_skill_or_agent(
    artifact: Any, task_desc: str, file_paths: list[Path], *, timeout_s: int | None = None
) -> dict:
    # Minimal task-like object for execution.py compatibility
    from dataclasses import dataclass, field

    from src.schemas.capability import CapabilityType

    @dataclass
    class _CliTask:
        task_id: str = ""
        task_prompt: str = ""
        fixture_paths: list[Path] = field(default_factory=list)

    import tests.evaluation.execution as _exec_mod

    _orig_skill_timeout = _exec_mod.SKILL_EXECUTION_TIMEOUT_S
    _orig_agent_timeout = _exec_mod.AGENT_EXECUTION_TIMEOUT_S
    if timeout_s is not None:
        _exec_mod.SKILL_EXECUTION_TIMEOUT_S = timeout_s
        _exec_mod.AGENT_EXECUTION_TIMEOUT_S = timeout_s

    cli_task = _CliTask(task_prompt=task_desc, fixture_paths=list(file_paths))

    fixture_contents: dict[str, str] = {}
    for fp in file_paths:
        if fp.exists():
            fixture_contents[fp.name] = fp.read_text(encoding="utf-8", errors="replace")

    try:
        cap_type = artifact.capability_type
        if cap_type == CapabilityType.SKILL:
            raw = _exec_mod.execute_skill_artifact(artifact, cli_task, fixture_contents)
        else:
            raw = _exec_mod.execute_agent_artifact(artifact, cli_task, fixture_contents)
    finally:
        _exec_mod.SKILL_EXECUTION_TIMEOUT_S = _orig_skill_timeout
        _exec_mod.AGENT_EXECUTION_TIMEOUT_S = _orig_agent_timeout

    if not raw:
        effective_timeout = timeout_s or (
            _orig_skill_timeout
            if artifact.capability_type == CapabilityType.SKILL
            else _orig_agent_timeout
        )
        err_console.print(
            f"[yellow]SKILL/AGENT execution returned no output "
            f"(likely timed out after {effective_timeout}s). "
            "Try --timeout with a higher value.[/yellow]"
        )
        return {}

    if (
        isinstance(raw, dict)
        and set(raw.keys()) == {"result"}
        and isinstance(raw.get("result"), dict)
    ):
        return raw["result"]
    return raw


# ---------------------------------------------------------------------------
# Registry match fallback
# ---------------------------------------------------------------------------


def _try_registry_match(
    state: Any,
    file_paths: list[Path],
    params: dict[str, str],
    task_prompt: str,
    timeout_s: int | None,
) -> tuple[Any | None, dict | None]:
    """Attempt to execute a registry-matched capability."""
    _get = state.get if isinstance(state, dict) else lambda k, d=None: getattr(state, k, d)
    matches = _get("matches", []) or []
    if not matches:
        return None, None

    first_match = matches[0]
    cap = getattr(first_match, "capability", first_match)
    meta = getattr(cap, "metadata", {}) or {}
    source_code = meta.get("source_code")

    if not source_code:
        cap_name = getattr(cap, "name", "?")
        err_console.print(
            f"[dim]Registry matched '{cap_name}' (seeded descriptor without source code). "
            "To force JIT generation, rephrase the task to avoid matching.[/dim]"
        )
        return None, None

    from src.schemas.capability import CapabilityType
    from src.schemas.task import GeneratedArtifact

    cap_type_val = getattr(cap, "capability_type", None)
    if isinstance(cap_type_val, str):
        cap_type_val = CapabilityType(cap_type_val)

    proxy = GeneratedArtifact(
        capability_name=getattr(cap, "name", "registry-match"),
        capability_type=cap_type_val or CapabilityType.MCP_TOOL,
        source_code=source_code,
    )

    try:
        if proxy.capability_type == CapabilityType.MCP_TOOL:
            output = _execute_mcp_tool(proxy, file_paths, params) or None
        else:
            output = (
                _execute_skill_or_agent(proxy, task_prompt, file_paths, timeout_s=timeout_s) or None
            )
    except Exception as exc:
        err_console.print(f"[yellow]Execution error: {exc}[/yellow]")
        output = None

    return proxy, output


# ---------------------------------------------------------------------------
# Invocation recording
# ---------------------------------------------------------------------------


def _record_invocation(
    metrics_store: Any,
    artifact: Any | None,
    task_prompt: str,
    output: dict | None,
    elapsed_ms: int,
) -> None:
    if artifact is None:
        return
    try:
        from src.schemas.metrics import InvocationRecord

        record = InvocationRecord(
            capability_id=getattr(artifact, "capability_name", "unknown"),
            task_id=task_prompt[:64],
            success=output is not None and bool(output),
            token_cost=0,
            latency_ms=float(elapsed_ms),
        )
        metrics_store.record_invocation(record)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Pipeline execution with streaming progress
# ---------------------------------------------------------------------------


def _extract_stage_detail(node_name: str, state_update: Any) -> str:
    """Extract a short detail string from a node's state update."""
    if isinstance(state_update, dict):
        _get = state_update.get
    else:
        _get = lambda k, d=None: getattr(state_update, k, d)  # noqa: E731

    if node_name == "decomposition_node":
        subtasks = _get("subtasks", []) or []
        return f"{len(subtasks)} subtask(s)"
    elif node_name == "gap_detection":
        matches = _get("matches", []) or []
        gaps = _get("gaps", []) or []
        return f"{len(matches)} match(es), {len(gaps)} gap(s)"
    elif node_name == "classifier_node":
        classified = _get("classified_gaps", []) or []
        if classified:
            types = [str(g.classified_type).split(".")[-1] for g in classified]
            return ", ".join(types)
        return ""
    elif node_name == "verification_node":
        results = _get("verification_results", []) or []
        if results:
            last = results[-1]
            if getattr(last, "overall_passed", False):
                return f"[green]\u2713 Tier {last.tier_reached}[/green]"
            else:
                return f"[red]\u2717 Tier {last.tier_reached}[/red]"
        return ""
    elif node_name.startswith("generate_"):
        artifacts = _get("generated_artifacts", []) or []
        return f"{len(artifacts)} artifact(s)"
    elif node_name == "loading_node":
        status = _get("status", None)
        if status:
            return str(status).split(".")[-1]
        return ""
    return ""


def execute_task(
    task: str,
    *,
    files: list[Path] | None = None,
    params: list[str] | None = None,
    skip_verification: bool = False,
    no_optimizer: bool = False,
    enable_optimizer: bool = False,
    timeout: int | None = None,
    json_output: bool = False,
    verbose: bool = False,
) -> int:
    """Run a task through the full JIT pipeline with live progress."""
    _load_env()
    _setup_logging(verbose)

    # Save run config for `mint retry`
    _save_last_run(
        task,
        files,
        params,
        skip_verification,
        no_optimizer,
        enable_optimizer,
        timeout,
    )

    file_paths = _resolve_files(files)
    parsed_params = _parse_params(params)
    task_prompt = _build_task_prompt(task, file_paths, parsed_params)

    # Check Docker early so the user doesn't wait through decomposition
    # only to fail at verification/execution
    if not _check_docker():
        err_console.print(
            "[red]Docker is not running.[/red]\n"
            "MINT needs Docker to verify and execute generated tools.\n"
            "Please start Docker Desktop and try again."
        )
        return 1

    registry, loader, metrics_store = _create_infra()

    # Determine optimizer state
    if no_optimizer:
        run_optimizer = False
    elif enable_optimizer:
        run_optimizer = True
    else:
        count = metrics_store.invocation_count_total()
        run_optimizer = count >= _AUTO_OPTIMIZER_THRESHOLD
        if run_optimizer:
            err_console.print(f"[dim]Optimizer auto-enabled ({count} invocations recorded).[/dim]")

    from src.orchestrator.pipeline import create_pipeline
    from src.schemas.task import TaskState

    pipeline = create_pipeline(
        registry,
        loader,
        run_optimizer=run_optimizer,
        metrics_store=metrics_store,
        verification_enabled=not skip_verification,
    ).compile()

    initial_state = TaskState(task_description=task_prompt)
    invoke_config = _langfuse_config()

    t_start = time.monotonic()

    # Stream pipeline with live progress
    stages = dict(STAGE_MAP)
    completed: set[str] = set()
    timings: dict[str, float] = {}
    stage_details: dict[str, str] = {}
    # Start spinner on first node immediately so app doesn't look frozen
    first_node = next(iter(stages))
    active_node: str | None = first_node
    stage_start = t_start
    final_state: Any = None

    if json_output:
        # No live display for JSON mode
        try:
            final_state = pipeline.invoke(initial_state, config=invoke_config)
        except Exception as exc:
            err_console.print(f"[red]Pipeline failed: {exc}[/red]")
            return 1
    else:
        console.print(
            Panel(
                _build_header(task, file_paths),
                title="MINT",
                subtitle="JIT Capability Synthesis",
                border_style="blue",
                expand=False,
            )
        )
        console.print()

        # Accumulate partial state updates into full state
        accumulated: dict[str, Any] = {}

        # Labels for completed stages (printed permanently)
        _STAGE_LABEL = {
            "decomposition_node": "Decomposing task",
            "gap_detection": "Searching registry",
            "classifier_node": "Classifying gaps",
            "generate_mcp_tool": "Generating MCP tool",
            "generate_skill": "Generating skill",
            "generate_agent": "Generating agent",
            "verification_node": "Verifying artifacts",
            "loading_node": "Loading & registering",
            "optimizer_node": "Optimizing registry",
        }

        # Map: after this node completes, the spinner shows this label
        # (what's running NEXT, not what just finished)
        _stage_order = list(stages.keys())
        _NEXT_STAGE_LABEL: dict[str, str] = {}
        for idx, node in enumerate(_stage_order):
            if idx + 1 < len(_stage_order):
                next_node = _stage_order[idx + 1]
                _NEXT_STAGE_LABEL[node] = _STAGE_LABEL.get(next_node, next_node) + "..."
            else:
                _NEXT_STAGE_LABEL[node] = "Finishing..."

        import threading

        stop_timer = threading.Event()
        current_label = _STAGE_LABEL.get(first_node, "Starting") + "..."
        printed_nodes: set[str] = set()
        prev_artifact_count = 0

        def _print_completed(node_name: str) -> None:
            """Print a completed stage line (permanent)."""
            nonlocal prev_artifact_count
            detail = stage_details.get(node_name, "")
            elapsed = timings.get(node_name, 0)

            # Skip generators that didn't produce new artifacts
            _gen_nodes = {
                "generate_mcp_tool",
                "generate_skill",
                "generate_agent",
            }
            if node_name in _gen_nodes:
                cur = len(accumulated.get("generated_artifacts") or [])
                new = cur - prev_artifact_count
                if new == 0:
                    printed_nodes.add(node_name)
                    return
                prev_artifact_count = cur
                detail = f"{new} artifact(s)"

            label = _STAGE_LABEL.get(node_name, node_name)
            detail_str = f" — {detail}" if detail else ""
            console.print(
                f"  [green]\u2713[/green] {label}{detail_str} [dim]({elapsed:.1f}s)[/dim]"
            )
            printed_nodes.add(node_name)

        def _timer_loop(status_ref):
            """Update spinner label with elapsed time every second."""
            while not stop_timer.is_set():
                elapsed = int(time.monotonic() - stage_start)
                status_ref.update(f"[cyan]{current_label}[/cyan] [dim]({elapsed}s)[/dim]")
                stop_timer.wait(1)

        try:
            with console.status(
                f"[cyan]{current_label}[/cyan] [dim](0s)[/dim]",
                spinner="dots",
            ) as status:
                timer_thread = threading.Thread(target=_timer_loop, args=(status,), daemon=True)
                timer_thread.start()

                for event in pipeline.stream(initial_state, config=invoke_config):
                    node_name = list(event.keys())[0]
                    update = event[node_name]

                    # Merge this node's update into accumulated state
                    if isinstance(update, dict):
                        accumulated.update(update)

                    # Mark previous node as done
                    if active_node and active_node != node_name:
                        completed.add(active_node)
                        timings[active_node] = time.monotonic() - stage_start
                        _print_completed(active_node)
                        stage_start = time.monotonic()

                    active_node = node_name
                    detail = _extract_stage_detail(node_name, update)
                    if detail:
                        stage_details[node_name] = detail

                    # Update spinner to show what's running NEXT
                    # (this event means node_name just completed)
                    current_label = _NEXT_STAGE_LABEL.get(node_name, "Processing...")

                # Mark last node as done
                if active_node:
                    completed.add(active_node)
                    timings[active_node] = time.monotonic() - stage_start
                stop_timer.set()
                timer_thread.join(timeout=1)
                active_node = None

            # The last completed node wasn't printed during the loop
            # (completed stages print when the NEXT event arrives).
            # Find and print any unprinted completed nodes.
            for node_name in stages:
                if node_name in completed and node_name not in printed_nodes:
                    _print_completed(node_name)

            final_state = accumulated
        except Exception as exc:
            stop_timer.set()
            err_console.print(f"[red]Pipeline failed: {exc}[/red]")
            return 1

    if final_state is None:
        err_console.print("[red]Pipeline returned no state.[/red]")
        return 1

    elapsed_ms = int((time.monotonic() - t_start) * 1000)

    # Select artifact and execute
    artifact = _select_artifact(final_state)
    output: dict | None = None

    if artifact:
        from src.schemas.capability import CapabilityType

        cap_type = artifact.capability_type
        if not json_output:
            console.print(f"\n[dim]Executing {artifact.capability_name}...[/dim]")

        try:
            if cap_type == CapabilityType.MCP_TOOL:
                output = _execute_mcp_tool(artifact, file_paths, parsed_params) or None
            elif cap_type in (CapabilityType.SKILL, CapabilityType.AGENT):
                output = (
                    _execute_skill_or_agent(artifact, task_prompt, file_paths, timeout_s=timeout)
                    or None
                )
        except Exception as exc:
            err_console.print(f"[yellow]Execution error: {exc}[/yellow]")
            output = None

    # Registry match fallback
    if artifact is None and not output:
        proxy, match_output = _try_registry_match(
            final_state, file_paths, parsed_params, task_prompt, timeout
        )
        if proxy:
            artifact = proxy
            output = match_output

    # Report optimizer actions (if it ran)
    if run_optimizer and not json_output:
        try:
            events = metrics_store._conn.execute(
                "SELECT event_type, COUNT(*) FROM capability_events "
                "GROUP BY event_type ORDER BY event_type"
            ).fetchall()
            event_summary = {et: n for et, n in events}
            pruned = event_summary.get("pruned", 0)
            split = event_summary.get("split", 0)
            merged = event_summary.get("merged", 0)
            if pruned or split or merged:
                console.print(
                    f"\n[dim]Optimizer: pruned {pruned}, split {split}, merged {merged}[/dim]"
                )
        except Exception:
            pass

    # Record invocation
    _record_invocation(metrics_store, artifact, task_prompt, output, elapsed_ms)

    # Update elapsed after execution
    elapsed_ms = int((time.monotonic() - t_start) * 1000)

    # Output
    if json_output:
        from scripts.run_task import _format_json

        print(_format_json(final_state, artifact, output, elapsed_ms))
    else:
        show_pipeline_result(final_state, artifact, output, elapsed_ms, verbose=verbose)

    # Success: artifact executed with output, or registry matched (even
    # without source_code — the system found a relevant capability)
    if artifact is not None and output:
        return 0
    # Registry matched but couldn't execute (seeded descriptor without
    # source code) — still a valid match, not a pipeline failure
    if isinstance(final_state, dict):
        _get = final_state.get
    else:
        _get = lambda k, d=None: getattr(final_state, k, d)  # noqa: E731
    if (_get("matches", None) or []) and not (_get("gaps", None) or []):
        return 0
    return 1


# ---------------------------------------------------------------------------
# Plan (dry-run) execution
# ---------------------------------------------------------------------------


def plan_task(
    task: str,
    *,
    files: list[Path] | None = None,
    verbose: bool = False,
) -> int:
    """Decompose and classify only (dry-run)."""
    _load_env()
    _setup_logging(verbose)

    file_paths = _resolve_files(files)
    task_prompt = _build_task_prompt(task, file_paths, {})

    registry, _, _metrics_store = _create_infra()

    from langgraph.graph import END, StateGraph
    from langgraph.types import Command

    from src.orchestrator.classifier import classifier_node
    from src.orchestrator.decomposition import decomposition_node
    from src.orchestrator.gap_detection import gap_detection_node
    from src.schemas.task import TaskState

    def _classifier_then_end(state: TaskState) -> Command:
        cmd = classifier_node(state)
        return Command(update=cmd.update, goto=END)

    plan_builder = StateGraph(TaskState)
    plan_builder.add_node("decomposition_node", decomposition_node)
    plan_builder.add_node(
        "gap_detection",
        lambda state: gap_detection_node(state, registry, metrics_store=None),
    )
    plan_builder.add_node("classifier_node", _classifier_then_end)
    plan_builder.set_entry_point("decomposition_node")
    plan_builder.add_edge("decomposition_node", "gap_detection")
    plan_builder.add_edge("gap_detection", "classifier_node")
    plan_builder.add_edge("classifier_node", END)
    plan_pipeline = plan_builder.compile()

    initial_state = TaskState(task_description=task_prompt)
    invoke_config = _langfuse_config()
    t_start = time.monotonic()

    console.print(
        Panel(
            _build_header(task, file_paths),
            title="MINT",
            subtitle="Plan (dry-run)",
            border_style="yellow",
            expand=False,
        )
    )

    try:
        final_state = plan_pipeline.invoke(initial_state, config=invoke_config)
    except Exception as exc:
        err_console.print(f"[red]Planning pipeline failed: {exc}[/red]")
        return 1

    elapsed_ms = int((time.monotonic() - t_start) * 1000)
    show_plan_result(final_state, elapsed_ms)
    return 0
