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

from src.config import AUTO_OPTIMIZER_THRESHOLD as _AUTO_OPTIMIZER_THRESHOLD
from src.config import FILE_SIZE_LIMIT_BYTES as _FILE_SIZE_LIMIT_BYTES


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
    allow_network: bool = False,
) -> None:
    """Persist last run config for `mint retry`."""
    data = {
        "task": task,
        "files": [str(f.resolve()) for f in files] if files else [],
        "params": params or [],
        "skip_verification": skip_verification,
        "no_optimizer": no_optimizer,
        "enable_optimizer": enable_optimizer,
        "timeout": timeout,
        "allow_network": allow_network,
    }
    path = _data_dir() / "cli" / _LAST_RUN_FILE
    path.write_text(json.dumps(data, indent=2))


def load_last_run() -> dict | None:
    """Load last run config. Returns None if no previous run."""
    path = _data_dir() / "cli" / _LAST_RUN_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _format_json(state: Any, artifact: Any | None, output: dict | None, elapsed_ms: int) -> str:
    _get = state.get if isinstance(state, dict) else lambda k, d=None: getattr(state, k, d)
    verification_results = _get("verification_results", []) or []

    verification: dict[str, Any] | None = None
    if verification_results:
        artifact_name = getattr(artifact, "capability_name", None) if artifact else None
        report = None
        if artifact_name:
            for r in reversed(verification_results):
                if r.capability_name == artifact_name:
                    report = r
                    break
        if report is None:
            report = verification_results[-1]

        findings_list = []
        for f in getattr(report, "findings", []) or []:
            findings_list.append({
                "severity": str(getattr(f, "severity", "")).upper(),
                "category": getattr(f, "category", ""),
                "message": getattr(f, "message", ""),
                "location": getattr(f, "location", None),
            })

        verification = {
            "passed": getattr(report, "overall_passed", None),
            "tier_reached": getattr(report, "tier_reached", None),
            "findings": findings_list,
        }

    result: dict[str, Any] = {
        "success": artifact is not None and bool(output),
        "elapsed_ms": elapsed_ms,
        "capability_name": getattr(artifact, "capability_name", None) if artifact else None,
        "capability_type": (
            str(artifact.capability_type).split(".")[-1].lower() if artifact else None
        ),
        "subtask_count": len(_get("subtasks", []) or []),
        "match_count": len(_get("matches", []) or []),
        "gap_count": len(_get("gaps", []) or []),
        "artifact_count": len(_get("generated_artifacts", []) or []),
        "attempt_count": _get("attempt_count", 0),
        "verification": verification,
        "output": output,
    }
    return json.dumps(result, indent=2, default=str)


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.DEBUG if verbose else logging.WARNING,
        stream=sys.stderr,
    )
    # Third-party loggers: suppress noise but keep ERROR+ visible
    third_party_level = logging.WARNING if verbose else logging.CRITICAL
    for noisy in (
        "opentelemetry",
        "urllib3",
        "httpcore",
        "httpx",
        "openai",
        "chromadb",
    ):
        logging.getLogger(noisy).setLevel(third_party_level)
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


from src.config import BINARY_EXTENSIONS as _BINARY_EXTENSIONS


def _resolve_files(paths: list[Path] | None) -> list[Path]:
    resolved: list[Path] = []
    if not paths:
        return resolved
    for fp in paths:
        if not fp.exists():
            err_console.print(f"[red]File not found: {fp}[/red]")
            raise SystemExit(1)
        # Excel files are binary but can be described via openpyxl
        _EXCEL_EXTENSIONS = {".xlsx", ".xls"}
        if fp.suffix.lower() in _BINARY_EXTENSIONS and fp.suffix.lower() not in _EXCEL_EXTENSIONS:
            err_console.print(
                f"[red]Binary file not supported: {fp.name}[/red]\n"
                "MINT can only process text files and Excel "
                "(CSV, JSON, XLSX, TXT, etc.)."
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

    # Excel files are binary — handle before text read
    if suffix in (".xlsx", ".xls"):
        try:
            import openpyxl

            wb = openpyxl.load_workbook(str(fp), read_only=True, data_only=True)
            sheets = wb.sheetnames
            desc = f"  {fp.name} ({size_str}, Excel, {len(sheets)} sheet(s))\n"
            for sheet_name in sheets[:5]:
                ws = wb[sheet_name]
                header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
                columns = [str(c) for c in header_row if c is not None]
                n_rows = max(ws.max_row - 1, 0) if ws.max_row else 0
                desc += f"    Sheet '{sheet_name}': {n_rows} rows\n"
                if columns:
                    desc += f"      Columns: {', '.join(columns[:20])}\n"
                sample_row = next(ws.iter_rows(min_row=2, max_row=2, values_only=True), ())
                if sample_row:
                    sample_vals = [str(v) for v in sample_row if v is not None]
                    desc += f"      Sample: {', '.join(sample_vals[:10])}\n"
            wb.close()
            return desc.rstrip()
        except ImportError:
            return f"  {fp.name} ({size_str}, Excel — install openpyxl for column preview)"
        except Exception:  # noqa: BLE001
            return f"  {fp.name} ({size_str}, Excel)"

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
                # Show the structure of the first value so the generator
                # knows the nested schema (e.g., {min, max, unit}).
                if keys:
                    sample_val = data[keys[0]]
                    sample_repr = json_mod.dumps(sample_val, default=str)
                    if len(sample_repr) <= 200:
                        desc += f"\n    Sample value ({keys[0]}): {sample_repr}"
                return desc
        except (json_mod.JSONDecodeError, AttributeError):
            pass
        return f"  {fp.name} ({size_str}, JSON)"

    if suffix == ".tsv":
        header = lines[0] if lines else ""
        columns = [c.strip().strip('"') for c in header.split("\t")]
        n_rows = max(len(lines) - 1, 0)
        sample = lines[1] if len(lines) > 1 else ""
        desc = f"  {fp.name} ({size_str}, TSV, {n_rows} rows)\n"
        desc += f"    Columns: {', '.join(columns)}\n"
        if sample:
            desc += f"    Sample row: {sample[:200]}"
        return desc

    if suffix in (".yaml", ".yml"):
        try:
            import yaml as yaml_mod

            data = yaml_mod.safe_load(content)
            if isinstance(data, dict):
                keys = list(data.keys())
                desc = f"  {fp.name} ({size_str}, YAML)\n"
                desc += f"    Top-level keys: {', '.join(str(k) for k in keys[:20])}"
                return desc
            elif isinstance(data, list):
                desc = f"  {fp.name} ({size_str}, YAML list, {len(data)} items)"
                return desc
        except Exception:  # noqa: BLE001
            pass
        return f"  {fp.name} ({size_str}, YAML)"

    if suffix == ".xml":
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(content)
            children = [child.tag for child in root]
            unique_tags = list(dict.fromkeys(children))[:15]
            desc = f"  {fp.name} ({size_str}, XML)\n"
            desc += f"    Root: <{root.tag}>, {len(children)} children\n"
            desc += f"    Child tags: {', '.join(unique_tags)}"
            if root.attrib:
                attrs = ', '.join(f'{k}={v!r}' for k, v in list(root.attrib.items())[:5])
                desc += f"\n    Root attributes: {attrs}"
            return desc
        except Exception:  # noqa: BLE001
            pass
        return f"  {fp.name} ({size_str}, XML)"

    if suffix == ".sql":
        # Show first few non-empty, non-comment lines for schema context
        meaningful = [l.strip() for l in lines if l.strip() and not l.strip().startswith("--")]
        preview = meaningful[:5]
        desc = f"  {fp.name} ({size_str}, SQL, {len(lines)} lines)\n"
        if preview:
            desc += f"    Preview: {'; '.join(preview)[:300]}"
        return desc

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


def _evaluate_output_contract(
    final_state: Any,
    artifact: Any,
    output: dict | None,
    chain_output_files: dict[str, bytes],
    *,
    err_console_ref: Any = None,
) -> int | None:
    """Step 14.6 — terminal-state output contract check.

    Pure set comparison on the typed ``OutputContract`` carried on
    ``TaskState.output_contract``; never raises. Returns the new CLI
    exit code 3 when:

    1. The pipeline state has a non-empty contract (authored shortcut at
       task entry, or successful LLM extraction at task entry), AND
    2. The pipeline produced an artifact AND a non-None output dict
       (so the failure mode is "wrong shape", not "hard crash"), AND
    3. The contract check finds at least one missing key or file.

    Returns ``None`` in all other cases — caller falls through to its
    legacy success / hard-fail exit logic. This gating is intentional:

    - **Empty / missing contract** → fall through. Free-form CLI prompts
      where the LLM extractor failed (or returned an empty contract) get
      legacy exit-code-only behavior; we never block them with shape
      complaints we can't justify.
    - **Hard crash (output is None)** → fall through to exit 1. Step 14.6
      catches "ran to completion with wrong shape" (the T16 motivator);
      hard crashes are a different failure mode and get exit 1 from the
      caller's existing logic.
    - **No artifact** → fall through. Registry-match-without-execution
      paths (seeded descriptors with no source code) take the legacy
      success branch.

    On a genuine contract violation, prints a distinct magenta warning
    listing missing keys + files + produced keys so the user can see
    what happened, then returns 3.

    Extracted from ``execute_task`` so the exit-3 path is testable
    end-to-end without standing up the full pipeline. See
    ``tests/unit/test_output_contract.py::test_evaluate_output_contract_*``
    for the integration tests that exercise it.
    """
    if isinstance(final_state, dict):
        contract = final_state.get("output_contract")
    else:
        contract = getattr(final_state, "output_contract", None)

    if contract is None or contract.is_empty():
        return None
    if artifact is None or output is None:
        return None

    check = contract.check(output, chain_output_files)
    if check.satisfied:
        return None

    target = err_console_ref if err_console_ref is not None else err_console
    target.print(
        "\n[bold magenta]⚠ OUTPUT CONTRACT NOT SATISFIED — "
        "wrong-shape answer[/bold magenta]"
    )
    target.print(
        "[magenta]The pipeline ran to completion but the final "
        "output is missing keys or files the task asked for. "
        "This usually means the decomposer produced a plan that "
        "doesn't deliver the requested shape — the artifacts ran, "
        "but the assembly is wrong. Treat this as a failure.[/magenta]"
    )
    if check.missing_keys:
        target.print(
            f"[magenta]Missing keys:[/magenta] {', '.join(check.missing_keys)}"
        )
    if check.missing_files:
        target.print(
            f"[magenta]Missing files:[/magenta] {', '.join(check.missing_files)}"
        )
    target.print(
        f"[dim]Produced keys: {', '.join(check.produced_keys) or '(none)'}[/dim]"
    )
    if check.produced_files:
        target.print(
            f"[dim]Produced files: {', '.join(check.produced_files)}[/dim]"
        )
    return 3  # distinct from 0/success, 1/hard-fail, 2/decomposition-fail


def _match_known_task(raw_task: str, full_prompt: str) -> Any | None:
    """Match the user's CLI task against the testbed catalogue (Step 14.6).

    Cheap zero-LLM matcher used by the CLI boundary adapter to re-use an
    authored ``TaskDefinition.output_contract`` when the user is running a
    known T-task. Two match modes:

    1. **Task ID** (most common): ``mint run T16`` → match T16. Case-insensitive
       and tolerant of surrounding whitespace.
    2. **Exact prompt copy-paste**: if the user's raw task string equals a
       known task's ``task_prompt`` verbatim, match that task. Useful for
       reproducing a specific eval run from the CLI.

    Returns ``None`` for genuinely free-form prompts, in which case the
    resolver falls through to its LLM extractor.
    """
    raw = raw_task.strip()
    if not raw:
        return None

    # Lazy import so the CLI module doesn't pay the test-package import cost
    # unless the matcher is actually called. Also avoids circulars in test
    # collection — tests/evaluation/tasks.py imports from src/, and src/cli
    # importing back at module load would create a cycle.
    try:
        from tests.evaluation.tasks import ALL_TASKS
    except Exception:  # noqa: BLE001
        return None

    upper = raw.upper()
    for task_def in ALL_TASKS:
        if upper == task_def.task_id.upper():
            return task_def

    for task_def in ALL_TASKS:
        if raw == task_def.task_prompt.strip():
            return task_def
        if full_prompt.strip() == task_def.task_prompt.strip():
            return task_def

    return None


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


def _langfuse_config(
    *,
    session_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict | None = None,
    user_id: str = "mint-cli",
) -> dict:
    """Build LangFuse callback config with rich tracing metadata.

    Uses ``config["metadata"]["langfuse_*"]`` keys — the convention that
    LangFuse's LangChain integration reads to set trace-level attributes.
    This works with LangGraph's pipeline invoke (constructor args on
    ``CallbackHandler`` don't propagate through LangGraph).

    Args:
        session_id: Groups related traces (e.g., a retry chain).
        tags: Filterable tags (e.g., file formats, capability type).
        metadata: Arbitrary key-value pairs attached to the trace.
        user_id: Identifies the trace source (default: "mint-cli").
    """
    try:
        from langfuse.langchain import CallbackHandler as LangFuseCallbackHandler

        handler = LangFuseCallbackHandler()
        config_metadata = {
            **(metadata or {}),
            "langfuse_session_id": session_id,
            "langfuse_tags": tags or [],
            "langfuse_user_id": user_id,
        }
        return {"callbacks": [handler], "metadata": config_metadata}
    except Exception:  # noqa: BLE001
        return {}


def _create_infra(*, seed: bool = True) -> tuple[Any, Any, Any]:
    """Create registry, loader, and metrics store."""
    from src.orchestrator.tool_loader import ToolLoader
    from src.registry.metrics import MetricsStore
    from src.registry.store import SkillRegistry, seed_registry

    data = _data_dir()
    (data / "cli").mkdir(parents=True, exist_ok=True)
    registry = SkillRegistry(persist_directory=str(data / "cli" / "chroma"))
    if seed:
        seed_registry(registry)
    loader = ToolLoader()
    metrics_store = MetricsStore(db_path=str(data / "cli" / "metrics.duckdb"))

    # CLI uses its own skill directory (separate from evaluation's data/eval/skills/)
    cli_skills_dir = data / "cli" / "skills"
    cli_skills_dir.mkdir(parents=True, exist_ok=True)
    from src.orchestrator.generators.skill import set_skills_output_dir
    set_skills_output_dir(cli_skills_dir)

    # CLI uses its own evolutionary-taxonomy file for the same reason:
    # keep CLI vocabulary separate from eval vocabulary so the harness's
    # snapshot/restore cycle doesn't stomp on CLI state. Default in
    # get_taxonomy_path() is data/eval/domains.json; the CLI flips it.
    cli_taxonomy_path = data / "cli" / "domains.json"
    cli_taxonomy_path.parent.mkdir(parents=True, exist_ok=True)
    from src.orchestrator.taxonomy import set_taxonomy_path
    set_taxonomy_path(cli_taxonomy_path)

    return registry, loader, metrics_store


# ---------------------------------------------------------------------------
# Generator stage diff — progress display helper
# ---------------------------------------------------------------------------


_GEN_STAGE_TYPE_LABELS: dict[str, str] = {
    "mcp_tool": "Generating MCP tool",
    "skill": "Generating skill",
    "agent": "Generating agent",
}


def _compute_gen_stage_diff(
    artifacts: list[Any],
    prev_artifact_count: int,
) -> tuple[int, int, str | None, str | None]:
    """Compute the progress-line fields for a generator stage.

    Given the current ``generated_artifacts`` list and the artifact count at
    the end of the previous printed generator stage, returns:

    * ``new_count`` — how many fresh artifacts this stage produced
    * ``new_prev`` — the updated baseline to carry into the next stage
      (always ``len(artifacts)``, even on resets/retries — see below)
    * ``detail`` — the text to append to the stage line, or ``None`` to
      skip printing this stage entirely
    * ``label_override`` — the "Generating skill/agent/MCP tool" label to
      use, or ``None`` to fall back to the node-name label

    **Retry semantics**: when ``verification_node`` fails all artifacts, it
    resets ``generated_artifacts`` to the passing subset (often empty) and
    routes back to the first generator. On the next pass, ``cur`` can be
    less than ``prev_artifact_count``. The old logic only updated the
    baseline when ``new != 0``, so a drop from 1 → 0 between stages produced
    a negative ``new = -1`` that got printed as ``-1 artifact(s)``. The fix
    refreshes the baseline unconditionally and skips the stage when
    ``new <= 0``.

    This is a pure function so it can be unit-tested without running the CLI.
    """
    cur = len(artifacts)
    new = cur - prev_artifact_count
    new_prev = cur
    if new <= 0:
        return new, new_prev, None, None

    new_arts = artifacts[-new:]
    types = {
        str(a.capability_type).split(".")[-1].lower()
        for a in new_arts
        if hasattr(a, "capability_type")
    }
    label_override = None
    if len(types) == 1:
        label_override = _GEN_STAGE_TYPE_LABELS.get(next(iter(types)))

    # Check for lead-with-subagents artifacts
    subagent_count = 0
    for a in new_arts:
        config = getattr(a, "agent_config", None) or {}
        subs = config.get("subagents", [])
        if subs:
            subagent_count += len(subs)

    if subagent_count > 0:
        detail = f"{new} lead + {subagent_count} subagent(s)"
    else:
        detail = f"{new} artifact(s)"
    return new, new_prev, detail, label_override


# ---------------------------------------------------------------------------
# Artifact selection
# ---------------------------------------------------------------------------


def _select_artifact(state: Any) -> Any | None:
    _get = state.get if isinstance(state, dict) else lambda k, d=None: getattr(state, k, d)
    artifacts = _get("generated_artifacts", []) or []
    verification_results = _get("verification_results", []) or []

    if not artifacts:
        return None

    # Filter to artifacts that passed verification
    passed_names = {
        report.capability_name
        for report in verification_results
        if report.overall_passed
    }
    passed = [a for a in artifacts if a.capability_name in passed_names]
    if not passed:
        passed = artifacts  # fallback: nothing passed, try anyway

    # Prefer AGENT > SKILL > MCP_TOOL — matches harness behavior.
    # More capable types are better when decomposition over-splits.
    _TYPE_PRIORITY = {"AGENT": 0, "SKILL": 1, "MCP_TOOL": 2}
    passed.sort(
        key=lambda a: (
            _TYPE_PRIORITY.get(str(a.capability_type).split(".")[-1].upper(), 9),
            a.capability_name or "",
        ),
    )
    return passed[0]


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

    _PATH_KEYWORDS = ("path", "file", "csv", "json", "txt", "log", "doc", "tsv", "yaml", "yml", "xml", "xlsx", "xls", "sql", "parquet")
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


def _execute_mcp_tool(
    artifact: Any,
    file_paths: list[Path],
    params: dict,
    *,
    allow_network: bool = False,
) -> dict:
    """Execute an MCP tool artifact inside the Docker sandbox.

    Binary files (Excel, Parquet, Pickle, etc.) are base64-encoded before
    injection into the sandbox so they survive the transfer intact.  Which
    extensions count as binary is governed by ``BINARY_EXTENSIONS`` in
    ``src/config.py`` — the business-analytics testbed commonly uses
    ``.xlsx``, ``.parquet``, and ``.pkl`` files.

    When ``allow_network`` is True, the sandbox runs with ``--network bridge``
    so tools that legitimately need outbound HTTP (APIs, web scraping) can
    reach the network. Default is offline-by-default for safety (Step 14.12).
    """
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
        sandbox_path = f"/tmp/fixture_{i}{fp.suffix}"
        sandbox_paths.append(sandbox_path)
        if fp.suffix.lower() in _BINARY_EXTENSIONS:
            # Binary files: use base64 encoding to transfer into sandbox
            import base64
            b64 = base64.b64encode(fp.read_bytes()).decode("ascii")
            fixture_preamble_lines.append(
                f"import pathlib as _pl, base64 as _b64\n"
                f"_pl.Path({sandbox_path!r}).write_bytes(_b64.b64decode({b64!r}))"
            )
        else:
            content = fp.read_text(encoding="utf-8", errors="replace")
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
        # Only try to fill params that are truly required (no default value)
        _required = {{
            name for name, p in _sig.parameters.items()
            if p.default is inspect.Parameter.empty
        }}
        _unmatched = _required - set(_kwargs)
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

    # Prefer pip_packages declared by the generator (accurate PyPI names).
    # Fall back to LLM-based import resolution for artifacts without them
    # (e.g., registry matches from before this feature).
    declared_pkgs = getattr(artifact, "pip_packages", None)
    if declared_pkgs:
        extra_pkgs = declared_pkgs
    else:
        extra_pkgs = resolve_extra_packages(source_code)
    exec_timeout = 60 if extra_pkgs else 30
    result = sandbox_execute(
        sandbox_code,
        timeout=exec_timeout,
        extra_packages=extra_pkgs or None,
        allow_network=allow_network,
    )

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
    artifact: Any,
    task_desc: str,
    file_paths: list[Path],
    *,
    timeout_s: int | None = None,
    langfuse_tags: list[str] | None = None,
    langfuse_session_id: str | None = None,
    allow_network: bool = False,
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

    agent_meta: Any | None = None
    try:
        cap_type = artifact.capability_type
        if cap_type == CapabilityType.SKILL:
            raw = _exec_mod.execute_skill_artifact(
                artifact, cli_task, fixture_contents,
                langfuse_tags=langfuse_tags,
                langfuse_session_id=langfuse_session_id,
                allow_network=allow_network,
            )
        else:
            raw = _exec_mod.execute_agent_artifact(
                artifact, cli_task, fixture_contents,
                langfuse_tags=langfuse_tags,
                langfuse_session_id=langfuse_session_id,
                allow_network=allow_network,
            )
            # Step 13: read the sticky meta immediately so a later
            # agent call can't overwrite it before we surface the
            # failure reason.
            agent_meta = _exec_mod.get_last_agent_execution_meta()
    finally:
        _exec_mod.SKILL_EXECUTION_TIMEOUT_S = _orig_skill_timeout
        _exec_mod.AGENT_EXECUTION_TIMEOUT_S = _orig_agent_timeout

    if not raw:
        effective_timeout = timeout_s or (
            _orig_skill_timeout
            if artifact.capability_type == CapabilityType.SKILL
            else _orig_agent_timeout
        )
        # Step 13: prefer the structured reason from the thread-local
        # meta over the old "likely timed out" guess. Only available
        # for AGENT calls — SKILL still falls back to the guess.
        if agent_meta is not None and not agent_meta.succeeded:
            if agent_meta.failure_reason == "timeout":
                ts = agent_meta.timeout_seconds or effective_timeout
                err_console.print(
                    f"[yellow]AGENT execution timed out after {ts}s. "
                    "Try --timeout with a higher value.[/yellow]"
                )
            elif agent_meta.failure_reason == "error":
                err_console.print(
                    "[yellow]AGENT execution crashed before returning "
                    "output (see logs for the exception).[/yellow]"
                )
            else:
                err_console.print(
                    "[yellow]AGENT execution returned empty output.[/yellow]"
                )
        else:
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
# Compositional chain execution (Phase 3)
# ---------------------------------------------------------------------------


def _execute_compositional_chain(
    final_state: Any,
    file_paths: list[Path],
    task_prompt: str,
    json_output: bool,
    console: Any,
    *,
    invoke_config: dict | None = None,
    metrics_store: Any | None = None,
    allow_network: bool = False,
) -> tuple[dict | None, Any | None, str | None, dict[str, bytes], list[dict]]:
    """Run a compositional chain when multiple subtasks are present.

    Returns ``(output, artifact_for_display, exec_error, output_files, step_timings)``.
    """
    from src.orchestrator.composition import CompositionalExecutor, build_resolution_map

    _get = (
        final_state.get
        if isinstance(final_state, dict)
        else lambda k, d=None: getattr(final_state, k, d)
    )
    subtasks = _get("subtasks", []) or []
    artifacts = _get("generated_artifacts", []) or []
    matches = _get("matches", []) or []
    required_capabilities = _get("required_capabilities", []) or []
    classified_gaps = _get("classified_gaps", []) or []

    resolution = build_resolution_map(
        required_capabilities, matches, artifacts, classified_gaps,
    )

    # Minimal task-like object for execute_skill_artifact / execute_agent_artifact
    from dataclasses import dataclass, field as dc_field

    @dataclass
    class _CliChainTask:
        task_id: str = ""
        task_prompt: str = ""
        fixture_paths: list = dc_field(default_factory=list)

    cli_task = _CliChainTask(
        task_prompt=task_prompt,
        fixture_paths=[str(p) for p in file_paths],
    )

    _lf_meta = (invoke_config or {}).get("metadata", {})

    executor = CompositionalExecutor(
        subtasks=subtasks,
        resolution=resolution,
        artifacts=artifacts,
        matches=matches,
        fixture_paths=file_paths,
        task=cli_task,
        metrics_store=metrics_store,
        langfuse_tags=_lf_meta.get("langfuse_tags"),
        langfuse_session_id=_lf_meta.get("langfuse_session_id"),
        allow_network=allow_network,
    )

    exec_error: str | None = None
    chain_result = None

    if json_output:
        try:
            chain_result = executor.run()
        except Exception as exc:
            exec_error = f"{type(exc).__name__}: {exc}"
    else:
        import threading

        console.print()

        chain_start = time.monotonic()
        status_text = f"[cyan]Chain step 1/{len(subtasks)}...[/cyan] [dim](0s)[/dim]"
        chain_stop = threading.Event()

        def _subagent_label(ref: Any) -> str:
            """Return ' (lead + N subagents)' suffix if applicable."""
            for a in artifacts:
                if a.capability_name == ref.capability_name:
                    config = getattr(a, "agent_config", None) or {}
                    subs = config.get("subagents", [])
                    if subs:
                        return f" (lead + {len(subs)} subagents)"
            return ""

        def _on_step_start(position: int, total: int, ref: Any) -> None:
            cap_type = str(ref.capability_type).split(".")[-1].upper()
            sub_label = _subagent_label(ref)
            nonlocal status_text
            status_text = (
                f"[cyan]Chain step {position + 1}/{total}: "
                f"{ref.capability_name} ({cap_type}{sub_label}, {ref.source})[/cyan]"
            )

        def _on_step_end(
            position: int, total: int, ref: Any, success: bool, step_ms: float = 0,
        ) -> None:
            marker = "[green]✓[/green]" if success else "[red]✗[/red]"
            cap_type = str(ref.capability_type).split(".")[-1].upper()
            sub_label = _subagent_label(ref)
            elapsed = f" [dim]({step_ms / 1000:.1f}s)[/dim]" if step_ms > 0 else ""
            console.print(
                f"  {marker} Step {position + 1}/{total}: "
                f"{ref.capability_name} ([cyan]{cap_type}{sub_label}[/cyan], {ref.source})"
                f"{elapsed}"
            )

        def _timer(status_ref: Any) -> None:
            while not chain_stop.is_set():
                sec = int(time.monotonic() - chain_start)
                status_ref.update(f"{status_text} [dim]({sec}s)[/dim]")
                chain_stop.wait(1)

        with console.status(status_text, spinner="dots") as chain_status:
            t = threading.Thread(target=_timer, args=(chain_status,), daemon=True)
            t.start()
            try:
                chain_result = executor.run(
                    on_step_start=_on_step_start,
                    on_step_end=_on_step_end,
                )
            except Exception as exc:
                exec_error = f"{type(exc).__name__}: {exc}"
            finally:
                chain_stop.set()
                t.join(timeout=1)

        if chain_result and not chain_result.chain_success:
            step_info = ""
            if chain_result.failed_step_name:
                step_info = (
                    f" at step {chain_result.failed_step_index}: "
                    f"{chain_result.failed_step_name}"
                )
            console.print(f"[yellow]Chain failed{step_info}[/yellow]")

    output: dict | None = None
    output_files: dict[str, bytes] = {}
    step_timings_data: list[dict] = []
    if chain_result is not None:
        if chain_result.chain_success and chain_result.step_results:
            output = chain_result.step_results[-1] or None
            output_files = chain_result.output_files
        step_timings_data = [
            t.model_dump(mode="json") for t in chain_result.step_timings
        ]

    # Use first artifact for display / save metadata
    artifact_for_display = artifacts[0] if artifacts else None

    return output, artifact_for_display, exec_error, output_files, step_timings_data


# ---------------------------------------------------------------------------
# Registry match fallback
# ---------------------------------------------------------------------------


def _try_registry_match(
    state: Any,
    file_paths: list[Path],
    params: dict[str, str],
    task_prompt: str,
    timeout_s: int | None,
    *,
    langfuse_tags: list[str] | None = None,
    langfuse_session_id: str | None = None,
    allow_network: bool = False,
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

    # Reconstruct skill_content / agent_config from registry metadata
    # so SKILL/AGENT execution backends have the original instructions.
    skill_content = meta.get("skill_content")
    agent_config = None
    agent_config_raw = meta.get("agent_config")
    if agent_config_raw:
        try:
            agent_config = json.loads(agent_config_raw) if isinstance(agent_config_raw, str) else agent_config_raw
        except (json.JSONDecodeError, TypeError):
            agent_config = None

    proxy = GeneratedArtifact(
        capability_name=getattr(cap, "name", "registry-match"),
        capability_type=cap_type_val or CapabilityType.MCP_TOOL,
        source_code=source_code,
        skill_content=skill_content,
        agent_config=agent_config,
    )

    try:
        if proxy.capability_type == CapabilityType.MCP_TOOL:
            output = _execute_mcp_tool(
                proxy, file_paths, params, allow_network=allow_network,
            ) or None
        else:
            output = (
                _execute_skill_or_agent(
                    proxy, task_prompt, file_paths,
                    timeout_s=timeout_s,
                    langfuse_tags=langfuse_tags,
                    langfuse_session_id=langfuse_session_id,
                    allow_network=allow_network,
                ) or None
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
# Output saving
# ---------------------------------------------------------------------------


def _save_output(
    output_dir: Path,
    output: dict,
    artifact: Any | None,
    verification_reports: list[Any] | None = None,
    *,
    final_state: Any | None = None,
    task: str | None = None,
    file_paths: list[Path] | None = None,
    exec_error: str | None = None,
    output_files: dict[str, bytes] | None = None,
    step_timings: list[dict] | None = None,
    pipeline_metrics: dict | None = None,
) -> None:
    """Save output to files in the specified directory.

    Always writes:
        - ``<cap_name>_<ts>.json`` — the executed capability's output (or
          an ``_empty`` placeholder including ``_error`` if execution failed)

    When ``verification_reports`` is provided:
        - ``<cap_name>_<ts>.verification.json`` — Tier 1/2/3 findings sidecar
          so verification notes are inspectable without going to LangFuse

    When ``final_state`` is provided:
        - ``<cap_name>_<ts>.run.json`` — per-run manifest listing the task,
          input files, ALL generated capabilities (with type + gap_query +
          executed flag), and the execution outcome. This makes a multi-cap
          run inspectable as a single file, which matters because Phase 2's
          per-gap restoration produces N artifacts per task even though only
          one is currently executed (priority sort).
    """
    from datetime import UTC, datetime

    cap_name = getattr(artifact, "capability_name", "output") if artifact else "output"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Create a subdirectory per run: mint_output/<cap_name>_<ts>/
    run_dir = output_dir / f"{cap_name}_{ts}"
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "Cannot create run directory %s: %s", run_dir, exc
        )
        return

    saved: list[tuple[str, str]] = []  # (label, path)

    # Scan output for file-like content and save separately
    for key, value in output.items():
        if isinstance(value, str) and len(value) > 500:
            # Detect CSV content
            first_line = value.split("\n")[0]
            if "," in first_line and "\n" in value and first_line.count(",") >= 2:
                ext = ".csv"
            # Detect JSON content
            elif value.strip().startswith(("{", "[")):
                ext = ".json"
            # Detect Markdown
            elif value.strip().startswith("#"):
                ext = ".md"
            else:
                ext = ".txt"
            file_path = run_dir / f"{key}{ext}"
            file_path.write_text(value, encoding="utf-8")
            saved.append((f"Data ({key})", str(file_path)))

    # Always save the full result as JSON
    result_path = run_dir / "output.json"
    result_path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    saved.append(("Output", str(result_path)))

    # Save chain output files (CSV, Excel, Markdown, etc.)
    if output_files:
        for fname, content in output_files.items():
            file_dest = run_dir / fname
            file_dest.write_bytes(content)
            saved.append((f"File ({fname})", str(file_dest)))

    # Save verification report sidecar
    if verification_reports:
        verification_payload = [
            r.model_dump(mode="json") if hasattr(r, "model_dump") else r
            for r in verification_reports
        ]
        verification_path = run_dir / "verification.json"
        verification_path.write_text(
            json.dumps(verification_payload, indent=2, default=str),
            encoding="utf-8",
        )
        saved.append(("Verification", str(verification_path)))

    # Save per-run manifest so multi-capability runs are inspectable as
    # a single file. Phase 2 produces N artifacts per task; this manifest
    # lists all of them with their types, source gap, and which one was
    # executed. Phase 3's compositional executor will eventually run them
    # all and the manifest will report chain execution status instead.
    if final_state is not None:
        _state_get = (
            final_state.get
            if isinstance(final_state, dict)
            else lambda k, d=None: getattr(final_state, k, d)
        )
        all_artifacts = _state_get("generated_artifacts", []) or []
        matches = _state_get("matches", []) or []
        classified_gaps = _state_get("classified_gaps", []) or []

        # Check if this was a compositional chain run (all artifacts executed)
        subtasks = _state_get("subtasks", []) or []
        _all_executed = len(subtasks) > 1 and (
            len(all_artifacts) + len(matches) > 1
        )

        def _artifact_summary(art: Any) -> dict:
            art_name = getattr(art, "capability_name", None)
            return {
                "capability_name": art_name,
                "capability_type": str(
                    getattr(art, "capability_type", "")
                ).split(".")[-1].lower(),
                "gap_query": getattr(art, "gap_query", None),
                "description": getattr(art, "description", None),
                "executed": True if _all_executed else art_name == cap_name,
            }

        manifest = {
            "timestamp": ts,
            "task": task,
            "files": [str(fp) for fp in file_paths] if file_paths else [],
            "executed_capability": cap_name if artifact else None,
            "execution_outcome": (
                "error"
                if exec_error
                else ("output" if not output.get("_empty") else "empty")
            ),
            "execution_error": exec_error,
            "generated_capabilities": [_artifact_summary(a) for a in all_artifacts],
            "registry_matches": [
                {
                    "capability_name": getattr(
                        getattr(m, "capability", None), "name", None
                    ),
                    "score": getattr(m, "score", None),
                }
                for m in matches
            ],
            "classified_gaps": [
                {
                    "query": getattr(cg, "query", None),
                    "description": getattr(cg, "description", None),
                    "classified_type": str(
                        getattr(cg, "classified_type", "")
                    ).split(".")[-1].lower(),
                    "confidence": getattr(cg, "confidence", None),
                }
                for cg in classified_gaps
            ],
        }
        if step_timings:
            manifest["step_timings"] = step_timings
        if pipeline_metrics:
            manifest["pipeline_metrics"] = pipeline_metrics
        manifest_path = run_dir / "run.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, default=str),
            encoding="utf-8",
        )
        saved.append(("Run manifest", str(manifest_path)))

    for label, path in saved:
        console.print(f"  [green]\u2713[/green] {label}: [dim]{path}[/dim]")


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
            # When ≥2 AGENT gaps, indicate they'll be consolidated into lead + subagents
            agent_count = sum(1 for t in types if t == "agent")
            if agent_count >= 2:
                non_agent = [t for t in types if t != "agent"]
                agent_label = f"agent x{agent_count} (→ 1 lead + {agent_count} subagents)"
                parts = non_agent + [agent_label]
                return ", ".join(parts)
            return ", ".join(types)
        return ""
    elif node_name == "verification_node":
        results = _get("verification_results", []) or []
        if results:
            # Deduplicate: keep only the latest report per capability_name.
            # verification_results accumulates across retries; without this,
            # the display would show inflated counts (e.g., "9 passed" when
            # only 4 artifacts exist but 3 verification passes ran).
            latest_by_name: dict[str, Any] = {}
            for r in results:
                cap = getattr(r, "capability_name", None)
                if cap:
                    latest_by_name[cap] = r
            deduped = list(latest_by_name.values())
            passed = sum(1 for r in deduped if getattr(r, "overall_passed", False))
            failed = len(deduped) - passed
            max_tier = max(
                (getattr(r, "tier_reached", 0) or 0 for r in deduped), default=0
            )
            if failed > 0:
                return (
                    f"[red]\u2717 {failed} failed[/red], "
                    f"[green]{passed} passed[/green] (Tier {max_tier})"
                )
            else:
                return f"[green]\u2713 Tier {max_tier}[/green]"
        return ""
    elif node_name.startswith("generate_"):
        artifacts = _get("generated_artifacts", []) or []
        return f"{len(artifacts)} artifact(s)"
    elif node_name == "loading_node":
        status = _get("status", None)
        if status:
            return str(status).split(".")[-1]
        return ""
    elif node_name == "optimizer_node":
        return "prune/split/merge"
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
    output_dir: Path | None = None,
    json_output: bool = False,
    verbose: bool = False,
    allow_network: bool = False,
    allow_network_source: str | None = None,
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
        allow_network=allow_network,
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

    # Validate mutually exclusive flags
    if no_optimizer and enable_optimizer:
        err_console.print(
            "[red]--no-optimizer and --enable-optimizer are mutually exclusive.[/red]"
        )
        return 1

    # §12 finding #8: build per-operation optimizer config from (priority
    # order): per-invocation flags > persistent settings > defaults.
    # - `--no-optimizer` overrides everything to all-off.
    # - `--enable-optimizer` overrides everything to all-on.
    # - Otherwise, read persistent settings from `data/cli/settings.json`
    #   (via `mint config set optimizer.<op> <value>`); fall through to
    #   defaults (all-"auto", threshold-gated).
    from src.cli.settings import get_optimizer_config_from_settings, load_settings
    from src.schemas.optimizer_config import OptimizerConfig

    count = metrics_store.invocation_count_total()
    if no_optimizer:
        optimizer_config = OptimizerConfig.all_off()
    elif enable_optimizer:
        optimizer_config = OptimizerConfig.all_on()
    else:
        _settings_path = _data_dir() / "cli" / "settings.json"
        _settings_opt = get_optimizer_config_from_settings(load_settings(_settings_path))
        optimizer_config = OptimizerConfig(**_settings_opt) if _settings_opt else OptimizerConfig()
        if optimizer_config.any_enabled(
            invocation_count=count, threshold=_AUTO_OPTIMIZER_THRESHOLD,
        ):
            _auto_ops = [
                op for op in ("prune", "split", "merge")
                if optimizer_config.is_enabled(
                    op, invocation_count=count, threshold=_AUTO_OPTIMIZER_THRESHOLD,
                )
            ]
            err_console.print(
                f"[dim]Optimizer enabled: {', '.join(_auto_ops)} "
                f"({count} invocations recorded).[/dim]"
            )

    from src.orchestrator.output_contract_resolver import resolve_output_contract
    from src.orchestrator.pipeline import create_pipeline
    from src.schemas.task import TaskState

    pipeline = create_pipeline(
        registry,
        loader,
        optimizer_config=optimizer_config,
        metrics_store=metrics_store,
        verification_enabled=not skip_verification,
    ).compile()

    # Step 14.6 — resolve the output contract via the shared boundary adapter.
    # CLI re-runs of T-tasks hit the known-task shortcut (0 LLM calls); free-
    # form prompts fall to the LLM extractor inside resolve_output_contract.
    # Same code path as the harness, just driven by different inputs.
    _known_task = _match_known_task(task, task_prompt)
    output_contract = resolve_output_contract(task_prompt, task_definition=_known_task)

    initial_state = TaskState(
        task_description=task_prompt,
        output_contract=output_contract,
        allow_network=allow_network,
    )

    # Build rich LangFuse tracing config
    import uuid as _uuid

    _run_id = _uuid.uuid4().hex[:12]
    _file_formats = list({fp.suffix.lower().lstrip(".") for fp in file_paths}) if file_paths else []
    _trace_tags = ["mint-cli", *(f"format:{fmt}" for fmt in sorted(_file_formats))]
    # §12 finding #8: derive trace tag / metadata from the resolved
    # OptimizerConfig. "optimizer-on" fires if any operation would run
    # at the current invocation_count; individual operation states are
    # recorded in metadata for Chapter 6 analytics.
    _optimizer_any = optimizer_config.any_enabled(
        invocation_count=count, threshold=_AUTO_OPTIMIZER_THRESHOLD,
    )
    if skip_verification:
        _trace_tags.append("no-verification")
    if _optimizer_any:
        _trace_tags.append("optimizer-on")
    if allow_network:
        _trace_tags.append("allow-network")
    invoke_config = _langfuse_config(
        session_id=f"mint-run-{_run_id}",
        tags=_trace_tags,
        metadata={
            "task": task[:200],
            "files": [fp.name for fp in file_paths],
            "params": parsed_params,
            "optimizer": _optimizer_any,
            "optimizer_config": optimizer_config.model_dump(),
            "verification": not skip_verification,
            "allow_network": allow_network,
        },
    )

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
        # Step 14.12: loud opt-in disclosure — offline-by-default paths stay
        # silent, opt-in paths always print the warning so users can see the
        # posture change at a glance. The ``allow_network_source`` string
        # is resolved in ``src/cli/app.py`` before ``execute_task`` is
        # called (flag vs. persistent setting).
        if allow_network:
            source_label = allow_network_source or "enabled"
            console.print(
                f"[yellow]⚠ Network access enabled[/yellow] — "
                "generated tools can make outbound requests\n"
                f"[dim]  source: {source_label}[/dim]"
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
        node_visit_count: dict[str, int] = {}  # track retries

        def _print_completed(node_name: str) -> bool:
            """Print a completed stage line. Returns True if printed."""
            nonlocal prev_artifact_count
            detail = stage_details.get(node_name, "")
            elapsed = timings.get(node_name, 0)

            # Skip generators that didn't produce new artifacts.
            # When new artifacts appear, use their actual type for the
            # label (not the node name — streaming can misattribute).
            _gen_nodes = {
                "generate_mcp_tool",
                "generate_skill",
                "generate_agent",
            }
            gen_label_override: str | None = None
            if node_name in _gen_nodes:
                arts = accumulated.get("generated_artifacts") or []
                new, prev_artifact_count, gen_detail, gen_label_override = (
                    _compute_gen_stage_diff(arts, prev_artifact_count)
                )
                if gen_detail is None:
                    # No new artifacts this stage — skip printing entirely.
                    # Covers both `new == 0` and `new < 0` (retry resets).
                    printed_nodes.add(node_name)
                    return False
                detail = gen_detail

            label = gen_label_override or _STAGE_LABEL.get(node_name, node_name)
            detail_str = f" — {detail}" if detail else ""
            console.print(
                f"  [green]\u2713[/green] {label}{detail_str} [dim]({elapsed:.1f}s)[/dim]"
            )
            printed_nodes.add(node_name)
            return True

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

                _GEN_NODES = {"generate_mcp_tool", "generate_skill", "generate_agent"}
                # Track when each event arrives so we can compute per-node
                # elapsed time as the delta between consecutive events.
                # LangGraph streams events AFTER nodes complete, so the
                # delta between consecutive events = wall time of the node
                # whose event arrived second.
                _prev_event_time = stage_start

                for event in pipeline.stream(initial_state, config=invoke_config):
                    event_time = time.monotonic()
                    node_name = list(event.keys())[0]
                    update = event[node_name]

                    # Track how many times each node has been visited
                    # (retry loop causes generators + verification to repeat)
                    node_visit_count[node_name] = node_visit_count.get(node_name, 0) + 1

                    # On retry, allow nodes to be re-printed by removing
                    # them from printed_nodes. Do NOT reset _prev_event_time —
                    # the delta from the previous event (the failed
                    # verification) is exactly the retried node's wall time.
                    if node_visit_count[node_name] > 1:
                        printed_nodes.discard(node_name)

                    # Capture timing for this node IMMEDIATELY — even if
                    # printing is deferred (generator nodes). This is the
                    # delta between this event and the previous one, which
                    # equals the wall time this node spent executing.
                    timings[node_name] = event_time - _prev_event_time

                    # When a NEW node's event arrives, print the previous
                    # node if it was deferred (generator nodes wait for
                    # artifact diff attribution).  Timing was already
                    # captured when the deferred node's event arrived.
                    if active_node and active_node != node_name and active_node not in printed_nodes:
                        completed.add(active_node)
                        _print_completed(active_node)

                    # Merge this event's update into accumulated state
                    if isinstance(update, dict):
                        accumulated.update(update)

                    active_node = node_name
                    detail = _extract_stage_detail(node_name, update)
                    if detail:
                        stage_details[node_name] = detail

                    # For non-generator nodes, print completion immediately
                    # so the checkmark line appears BEFORE the next spinner.
                    # Generator nodes are deferred because their artifact
                    # diff needs to be computed against state that doesn't
                    # yet include the next generator's artifacts.
                    if node_name not in _GEN_NODES and node_name not in printed_nodes:
                        completed.add(node_name)
                        _print_completed(node_name)

                    # This event's arrival time becomes the baseline for
                    # the next node's elapsed time calculation.
                    _prev_event_time = event_time
                    stage_start = event_time

                    # Update spinner to show what's running NEXT
                    next_label = _NEXT_STAGE_LABEL.get(node_name, "Processing...")
                    # Enrich verification spinner with artifact count
                    if "Verifying" in next_label:
                        n_arts = len(accumulated.get("generated_artifacts") or [])
                        if n_arts:
                            next_label = f"Verifying {n_arts} artifact(s)..."
                    current_label = next_label

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

    # ---------------------------------------------------------------
    # Compositional execution: if the task was decomposed into
    # multiple subtasks with resolved capabilities, run the chain.
    # ---------------------------------------------------------------
    _state_get_early = (
        final_state.get
        if isinstance(final_state, dict)
        else lambda k, d=None: getattr(final_state, k, d)
    )
    _subtasks = _state_get_early("subtasks", []) or []
    _artifacts_all = _state_get_early("generated_artifacts", []) or []
    _matches_all = _state_get_early("matches", []) or []
    _use_compositional = len(_subtasks) > 1 and (len(_artifacts_all) + len(_matches_all) > 1)

    artifact: Any = None
    output: dict | None = None
    exec_error: str | None = None
    chain_output_files: dict[str, bytes] = {}
    chain_step_timings: list[dict] = []

    if _use_compositional:
        output, artifact, exec_error, chain_output_files, chain_step_timings = _execute_compositional_chain(
            final_state, file_paths, task_prompt, json_output, console,
            invoke_config=invoke_config,
            metrics_store=metrics_store,
            allow_network=allow_network,
        )
    else:
        # ---------------------------------------------------------------
        # Single-artifact execution (legacy path)
        # ---------------------------------------------------------------
        artifact = _select_artifact(final_state)

    if not _use_compositional and artifact:
        from src.schemas.capability import CapabilityType

        cap_type = artifact.capability_type
        cap_type_label = str(cap_type).split(".")[-1].upper()

        try:
            if cap_type == CapabilityType.MCP_TOOL:
                if not json_output:
                    console.print(f"\n[dim]Executing {artifact.capability_name}...[/dim]")
                output = _execute_mcp_tool(
                    artifact, file_paths, parsed_params, allow_network=allow_network,
                ) or None
            elif cap_type in (CapabilityType.SKILL, CapabilityType.AGENT):
                if not json_output:
                    exec_label = f"Executing {cap_type_label}: {artifact.capability_name}"
                    exec_stop = threading.Event()
                    exec_start = time.monotonic()

                    def _exec_timer(status_ref):
                        while not exec_stop.is_set():
                            sec = int(time.monotonic() - exec_start)
                            status_ref.update(f"[cyan]{exec_label}...[/cyan] [dim]({sec}s)[/dim]")
                            exec_stop.wait(1)

                    with console.status(
                        f"[cyan]{exec_label}...[/cyan] [dim](0s)[/dim]",
                        spinner="dots",
                    ) as exec_status:
                        t = threading.Thread(
                            target=_exec_timer,
                            args=(exec_status,),
                            daemon=True,
                        )
                        t.start()
                        _lf_meta = invoke_config.get("metadata", {})
                        output = (
                            _execute_skill_or_agent(
                                artifact,
                                task_prompt,
                                file_paths,
                                timeout_s=timeout,
                                langfuse_tags=_lf_meta.get("langfuse_tags"),
                                langfuse_session_id=_lf_meta.get("langfuse_session_id"),
                                allow_network=allow_network,
                            )
                            or None
                        )
                        exec_stop.set()
                        t.join(timeout=1)
                else:
                    _lf_meta = invoke_config.get("metadata", {})
                    output = (
                        _execute_skill_or_agent(
                            artifact,
                            task_prompt,
                            file_paths,
                            timeout_s=timeout,
                            langfuse_tags=_lf_meta.get("langfuse_tags"),
                            langfuse_session_id=_lf_meta.get("langfuse_session_id"),
                        )
                        or None
                    )
        except Exception as exc:
            exec_error = f"{type(exc).__name__}: {exc}"
            err_console.print(f"[yellow]Execution error: {exec_error}[/yellow]")
            output = None

    # Registry match fallback
    if artifact is None and not output:
        _lf_meta_match = invoke_config.get("metadata", {})
        proxy, match_output = _try_registry_match(
            final_state, file_paths, parsed_params, task_prompt, timeout,
            langfuse_tags=_lf_meta_match.get("langfuse_tags"),
            langfuse_session_id=_lf_meta_match.get("langfuse_session_id"),
            allow_network=allow_network,
        )
        if proxy:
            artifact = proxy
            output = match_output

    # Report optimizer actions (if any operation ran).
    if _optimizer_any and not json_output:
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

    # Update elapsed after execution (includes sandbox/DeepAgent time)
    elapsed_ms = int((time.monotonic() - t_start) * 1000)

    # Record invocation with final elapsed time
    _record_invocation(metrics_store, artifact, task_prompt, output, elapsed_ms)

    # Output
    if json_output:
        print(_format_json(final_state, artifact, output, elapsed_ms))
    else:
        show_pipeline_result(final_state, artifact, output, elapsed_ms, verbose=verbose)

    # Always save output (default: data/output/, override with --output-dir)
    save_dir = output_dir or _data_dir() / "cli" / "mint_output"
    # final_state is a dict (LangGraph's pipeline.invoke/.stream returns dicts),
    # so we need the dict-aware accessor — getattr on a dict always returns the
    # default. This bug previously caused the verification sidecar to never fire.
    _state_get = (
        final_state.get
        if isinstance(final_state, dict)
        else lambda k, d=None: getattr(final_state, k, d)
    )
    verification_reports = _state_get("verification_results", None) or None

    # Build pipeline metrics for run.json — structured stage timings
    # so the thesis can aggregate without querying LangFuse.
    _subtask_count = len(_state_get("subtasks", []) or [])
    _attempt_count = _state_get("attempt_count", 0) or 0
    _pmx: dict = {
        "total_elapsed_ms": elapsed_ms,
        "attempt_count": _attempt_count,
        "subtask_count": _subtask_count,
        "stage_timings_s": {k: round(v, 1) for k, v in timings.items()},
        "stage_details": stage_details,
    }

    if output:
        _save_output(
            save_dir,
            output,
            artifact,
            verification_reports,
            final_state=final_state,
            task=task,
            file_paths=file_paths,
            exec_error=exec_error,
            output_files=chain_output_files or None,
            step_timings=chain_step_timings or None,
            pipeline_metrics=_pmx,
        )
    elif artifact is not None:
        # Save metadata even for empty outputs so the attempt is recorded.
        # Include the captured execution error so failures are inspectable
        # without going to LangFuse — previously the error was printed to
        # stderr and lost.
        cap_name = getattr(artifact, "capability_name", "unknown")
        placeholder: dict = {"_empty": True, "_capability": cap_name}
        if exec_error:
            placeholder["_error"] = exec_error
        _save_output(
            save_dir,
            placeholder,
            artifact,
            verification_reports,
            final_state=final_state,
            task=task,
            file_paths=file_paths,
            exec_error=exec_error,
            pipeline_metrics=_pmx,
        )

    # Sticky decomposition-failure flag: if decomposition_node bailed out
    # (timeout / exception / empty subtasks), the fallback path still produced
    # whatever it could — but the output is unreliable because the pipeline
    # ran on a single-sentence fallback query instead of a real decomposition.
    # Surface this to the user distinctly AND return a non-zero exit code so
    # scripts like test_mint and run_pilot classify the run as a failure.
    # See docs/perf_fixes_2026_04_14.md §2b.
    if isinstance(final_state, dict):
        _get_final = final_state.get
    else:
        _get_final = lambda k, d=None: getattr(final_state, k, d)  # noqa: E731

    decomposition_ok = _get_final("decomposition_succeeded", True)
    if not decomposition_ok:
        err_console.print(
            "\n[bold yellow]⚠ DECOMPOSITION FAILED — "
            "partial output only[/bold yellow]"
        )
        err_console.print(
            "[yellow]The task decomposer hit its timeout or errored. "
            "The pipeline ran through a single-sentence fallback path, so "
            "the output above is almost certainly incomplete. "
            "Results should be treated as a diagnostic, not a successful "
            "run.[/yellow]"
        )
        _errs = _get_final("errors", []) or []
        if _errs:
            err_console.print(f"[dim]Errors: {'; '.join(_errs[:3])}[/dim]")
        return 2  # distinct from 1 (hard failure) and 0 (success)

    # §12 finding #16 — proposal-strict Pillar 1.3 fail-closed.
    # classifier_node sets this sticky flag to False when any gap fails
    # classification after MAX_CLASSIFICATION_ATTEMPTS retries. No
    # silent MCP_TOOL default.
    classification_ok = _get_final("classification_succeeded", True)
    if not classification_ok:
        err_console.print(
            "\n[bold red]✗ CLASSIFICATION FAILED — gap could not be typed[/bold red]"
        )
        err_console.print(
            "[red]One or more capability gaps could not be classified as "
            "MCP_TOOL / SKILL / AGENT after 3 attempts. Per Pillar 1.3 of the "
            "thesis proposal, a mis-classified capability produces the wrong "
            "artifact shape (e.g., 'an MCP tool that should have been a "
            "skill will be too rigid'). The task is classified as "
            "CLASSIFICATION_FAILED rather than defaulting to MCP_TOOL.[/red]"
        )
        _errs = _get_final("errors", []) or []
        _class_errs = [
            e for e in _errs if "Classification failed" in e or "CLASSIFICATION_FAILED" in e
        ]
        if _class_errs:
            err_console.print(f"[dim]Last error: {_class_errs[-1]}[/dim]")
        err_console.print(
            "[dim]Run `mint retry --hint \"...\"` with guidance. Common "
            "causes: rate limiting, model provider transient errors.[/dim]"
        )
        return 5  # distinct from 0/1/2/3/4

    # §12 finding #14 — proposal-strict Pillar 2 fail-closed. The
    # verification_node sets this sticky flag to False when MAX_ATTEMPTS
    # retries exhaust with at least one artifact still failing the gate.
    # The failed artifact is already dropped from generated_artifacts, so
    # the chain likely either produced no output or skipped the failing
    # step. Surface the failure explicitly rather than treating the
    # fallthrough as success.
    verification_ok = _get_final("verification_succeeded", True)
    if not verification_ok:
        err_console.print(
            "\n[bold red]✗ VERIFICATION FAILED — no usable capability produced[/bold red]"
        )
        err_console.print(
            "[red]The three-tier verification gate rejected the generated "
            "capability on 3 consecutive attempts. Per Pillar 2 of the thesis "
            "proposal ('Only verified capabilities enter production'), the "
            "failed artifact was dropped from the registry and the task is "
            "classified as VERIFICATION_FAILED.[/red]"
        )
        _errs = _get_final("errors", []) or []
        _verify_errs = [e for e in _errs if "Verification failed" in e or "VERIFICATION_FAILED" in e]
        if _verify_errs:
            err_console.print(f"[dim]Last error: {_verify_errs[-1]}[/dim]")
        err_console.print(
            "[dim]Run `mint retry --hint \"...\"` with guidance on what "
            "the generator keeps getting wrong.[/dim]"
        )
        return 4  # distinct from 0 / 1 / 2 / 3 — new exit code for verification-exhaustion

    # Step 14.6 — output contract check. Delegated to a small testable
    # helper so the exit-3 wiring can be exercised end-to-end without
    # standing up a live pipeline. Returns 3 if the check fires, None
    # otherwise (in which case the legacy success / hard-fail logic below
    # decides the exit code).
    _contract_exit = _evaluate_output_contract(
        final_state,
        artifact,
        output,
        chain_output_files,
    )
    if _contract_exit is not None:
        return _contract_exit

    # Success: artifact executed with output, or registry matched (even
    # without source_code — the system found a relevant capability)
    if artifact is not None and output:
        # §12 finding #6b: nudge the user toward `mint feedback --wrong`
        # so non-crashing but semantically-wrong outputs can feed back
        # into the Pillar 3 optimizer. Without this loop CLI runs only
        # ever produce success=False on crashes, not on user-judgment
        # wrong answers.
        console.print(
            "[dim]Was this output wrong? Run "
            "[bold]mint feedback --wrong --reason \"...\"[/bold] "
            "to flag it for the optimizer.[/dim]"
        )
        return 0
    # Registry matched but couldn't execute (seeded descriptor without
    # source code) — still a valid match, not a pipeline failure
    if (_get_final("matches", None) or []) and not (_get_final("gaps", None) or []):
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

    registry, _, _ = _create_infra()

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
    invoke_config = _langfuse_config(
        tags=["mint-cli", "plan"],
        metadata={"task": task[:200], "files": [fp.name for fp in file_paths]},
    )
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
