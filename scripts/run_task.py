"""User-facing CLI runner for the JustInTimeMAS pipeline.

Submit a task description + optional input files, and the JIT pipeline will
generate (or retrieve) a capability, execute it, and return the result.

Usage::

    uv run scripts/run_task.py "Parse this CSV and compute summary statistics" \\
        --file data.csv

    uv run scripts/run_task.py "Join sales with products and compute revenue" \\
        --file sales.csv --file products.json --file targets.csv

    uv run scripts/run_task.py --list
    uv run scripts/run_task.py "Detect anomalies" --file data.csv --dry-run
    uv run scripts/run_task.py "Summarise logs" --file app.log --output-format json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path when run as a script
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
CHROMA_DIR = DATA_DIR / "chroma"

# ---------------------------------------------------------------------------
# Logging — verbose flag controls level
# ---------------------------------------------------------------------------


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=level,
        stream=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Minimal task-like object for execution.py compatibility
# ---------------------------------------------------------------------------


@dataclass
class _CliTask:
    """Minimal task proxy so execution.py helpers work without TaskDefinition."""

    task_id: str = ""  # empty → GenericResult fallback in execution.py
    task_prompt: str = ""
    fixture_paths: list[Path] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.fixture_paths is None:
            self.fixture_paths = []


# ---------------------------------------------------------------------------
# Artifact selection
# ---------------------------------------------------------------------------


def _select_artifact(state: Any) -> Any | None:
    """Pick the best artifact from the final pipeline state."""
    _get = state.get if isinstance(state, dict) else lambda k, d=None: getattr(state, k, d)
    artifacts = _get("generated_artifacts", []) or []
    verification_results = _get("verification_results", []) or []

    if not artifacts:
        return None

    # Prefer an artifact that passed verification
    for art in artifacts:
        for report in verification_results:
            if report.capability_name == art.capability_name and report.overall_passed:
                return art

    # Fallback: last generated artifact
    return artifacts[-1]


# ---------------------------------------------------------------------------
# MCP_TOOL execution (replicates harness._execute_artifact for CLI)
# ---------------------------------------------------------------------------

_FILE_SIZE_LIMIT_BYTES = 10 * 1024 * 1024  # 10 MB


def _execute_mcp_tool(artifact: Any, file_paths: list[Path], params: dict) -> dict:
    """Execute an MCP_TOOL artifact in the Docker sandbox with user-provided files."""
    from src.verification.behavioral import _extract_function_name
    from src.verification.sandbox_tool import resolve_extra_packages, sandbox_execute

    source_code = getattr(artifact, "source_code", None)
    if not source_code:
        return {}

    func_name = _extract_function_name(source_code) or artifact.capability_name.replace("-", "_")

    # Embed fixture files into the sandbox as /tmp/ writes
    fixture_preamble_lines: list[str] = []
    fixture_inputs: dict[str, str] = {}

    for i, fp in enumerate(file_paths):
        if not fp.exists():
            print(f"Warning: file not found: {fp}", file=sys.stderr)
            continue
        size = fp.stat().st_size
        if size > _FILE_SIZE_LIMIT_BYTES:
            print(
                f"Warning: {fp.name} is {size // (1024*1024)}MB — embedding large files "
                "may cause sandbox code to be too large. Proceeding anyway.",
                file=sys.stderr,
            )
        content = fp.read_text(encoding="utf-8", errors="replace")
        sandbox_path = f"/tmp/fixture_{i}{fp.suffix}"
        fixture_preamble_lines.append(
            f"import pathlib as _pl\n"
            f"_pl.Path({sandbox_path!r}).write_text({content!r})"
        )
        fixture_inputs[f"file_path_{i}"] = sandbox_path

    # Merge user params with fixture paths
    all_inputs = {**fixture_inputs, **params}

    # Build fuzzy-matching sandbox code (same pattern as harness)
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
        stderr = (result.get("stderr") or "")[:500]
        logging.warning("Sandbox execution failed: %s", stderr)
        if "Cannot connect to the Docker daemon" in stderr or "docker" in stderr.lower():
            print("Error: Docker is not running. Please start Docker and retry.", file=sys.stderr)
        return {}

    try:
        parsed = json.loads(result["stdout"].strip().splitlines()[-1])
        if "error" in parsed:
            logging.warning("Tool raised an error: %s", str(parsed["error"])[:500])
            return {}
        return parsed.get("result", {}) or {}
    except (json.JSONDecodeError, KeyError, IndexError):
        logging.warning("Could not parse sandbox output: %s", result["stdout"][:200])
        return {}


# ---------------------------------------------------------------------------
# SKILL / AGENT execution
# ---------------------------------------------------------------------------


class _ExecutionTimeout(Exception):
    """Raised when SKILL/AGENT execution exceeds the user-specified timeout."""


def _execute_skill_or_agent(
    artifact: Any, task_desc: str, file_paths: list[Path], *, timeout_s: int | None = None
) -> dict:
    """Execute a SKILL or AGENT artifact using a DeepAgent.

    Raises ``_ExecutionTimeout`` if the execution exceeds the timeout so the
    caller can show a clear message to the user.
    """
    from src.schemas.capability import CapabilityType
    import tests.evaluation.execution as _exec_mod

    # Override module-level timeouts if the user specified --timeout
    _orig_skill_timeout = _exec_mod.SKILL_EXECUTION_TIMEOUT_S
    _orig_agent_timeout = _exec_mod.AGENT_EXECUTION_TIMEOUT_S
    if timeout_s is not None:
        _exec_mod.SKILL_EXECUTION_TIMEOUT_S = timeout_s
        _exec_mod.AGENT_EXECUTION_TIMEOUT_S = timeout_s

    cli_task = _CliTask(
        task_id="",  # falls back to GenericResult
        task_prompt=task_desc,
        fixture_paths=file_paths,
    )

    # Read fixture contents
    fixture_contents: dict[str, str] = {}
    for fp in file_paths:
        if fp.exists():
            fixture_contents[fp.name] = fp.read_text(encoding="utf-8", errors="replace")
        else:
            print(f"Warning: file not found: {fp}", file=sys.stderr)

    try:
        cap_type = artifact.capability_type
        if cap_type == CapabilityType.SKILL:
            raw = _exec_mod.execute_skill_artifact(artifact, cli_task, fixture_contents)
        else:
            raw = _exec_mod.execute_agent_artifact(artifact, cli_task, fixture_contents)
    finally:
        # Restore original timeouts
        _exec_mod.SKILL_EXECUTION_TIMEOUT_S = _orig_skill_timeout
        _exec_mod.AGENT_EXECUTION_TIMEOUT_S = _orig_agent_timeout

    # Empty dict means execution failed — check if it was a timeout by
    # inspecting the time elapsed (execution.py returns {} on timeout).
    if not raw:
        effective_timeout = timeout_s or (
            _orig_skill_timeout
            if artifact.capability_type == CapabilityType.SKILL
            else _orig_agent_timeout
        )
        raise _ExecutionTimeout(
            f"SKILL/AGENT execution returned no output (likely timed out after "
            f"{effective_timeout}s). Try --timeout with a higher value."
        )

    # GenericResult wraps output as {"result": {...}} — unwrap if present
    if isinstance(raw, dict) and set(raw.keys()) == {"result"} and isinstance(raw.get("result"), dict):
        return raw["result"]
    return raw


# ---------------------------------------------------------------------------
# --list command
# ---------------------------------------------------------------------------


def _cmd_list(registry: Any) -> None:
    """Print all active capabilities from the registry."""
    try:
        capabilities = registry.list_all()
    except AttributeError:
        # Fallback: search with a broad query
        results = registry.search("*", top_k=100)
        capabilities = [r.capability for r in results] if results else []

    if not capabilities:
        print("No capabilities in registry.")
        return

    active = [c for c in capabilities if getattr(c, "is_active", True)]
    print(f"Registry capabilities ({len(active)} active):\n")
    for cap in active:
        cap_type = str(getattr(cap, "capability_type", "?")).split(".")[-1].upper()
        name = getattr(cap, "name", "?")
        desc = getattr(cap, "description", "")[:80]
        print(f"  [{cap_type}] {name}")
        if desc:
            print(f"          {desc}")
    print()


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _format_verification(verification_results: list, artifact_name: str | None) -> list[str]:
    """Format verification findings into human-readable lines."""
    if not verification_results:
        return []

    # Find the report for the selected artifact, or use the last one
    report = None
    if artifact_name:
        for r in reversed(verification_results):
            if r.capability_name == artifact_name:
                report = r
                break
    if report is None:
        report = verification_results[-1]

    tier_names = {1: "static scanner", 2: "behavioral testing", 3: "adversarial probing"}
    tier_reached = getattr(report, "tier_reached", None)
    passed = getattr(report, "overall_passed", None)

    lines: list[str] = []
    if passed is True:
        lines.append(f"Verification: PASSED (Tier {tier_reached})")
    elif passed is False:
        tier_label = tier_names.get(tier_reached, f"Tier {tier_reached}")
        lines.append(f"Verification: FAILED (Tier {tier_reached} — {tier_label})")
    else:
        lines.append("Verification: N/A")

    findings = getattr(report, "findings", []) or []
    # Show CRITICAL and HIGH findings always; MEDIUM only if verbose
    notable = [f for f in findings if str(getattr(f, "severity", "")).lower() in ("critical", "high")]
    if not notable:
        notable = findings[:5]  # show first few if no critical/high

    for f in notable:
        sev = str(getattr(f, "severity", "?")).upper()
        msg = getattr(f, "message", "")
        loc = getattr(f, "location", None)
        loc_str = f" ({loc})" if loc else ""
        lines.append(f"  [{sev}] {msg}{loc_str}")

    remaining = len(findings) - len(notable)
    if remaining > 0:
        lines.append(f"  ... and {remaining} more finding(s)")

    return lines


def _format_human(state: Any, artifact: Any | None, output: dict | None, elapsed_ms: int) -> str:
    _get = state.get if isinstance(state, dict) else lambda k, d=None: getattr(state, k, d)
    subtasks = _get("subtasks", []) or []
    matches = _get("matches", []) or []
    gaps = _get("gaps", []) or []
    classified_gaps = _get("classified_gaps", []) or []
    generated_artifacts = _get("generated_artifacts", []) or []
    verification_results = _get("verification_results", []) or []
    attempt_count = _get("attempt_count", 0)
    task_desc = _get("task_description", "")

    lines: list[str] = []
    lines.append(f"\nTask: {str(task_desc)[:100]}")
    lines.append("")
    lines.append("Pipeline:")
    lines.append(f"  Decomposed into {len(subtasks)} subtask(s)")
    lines.append(f"  Registry: {len(matches)} match(es), {len(gaps)} gap(s)")
    if classified_gaps:
        types = [g.classified_type for g in classified_gaps]
        lines.append(f"  Classified: {', '.join(str(t).split('.')[-1] for t in types)}")
    lines.append(f"  Generated {len(generated_artifacts)} artifact(s)")
    lines.append(f"  Attempts: {attempt_count}")
    lines.append("")

    # Verification findings
    if verification_results:
        artifact_name = getattr(artifact, "capability_name", None) if artifact else None
        verif_lines = _format_verification(verification_results, artifact_name)
        lines.extend(verif_lines)
        lines.append("")

    if artifact:
        cap_type = str(artifact.capability_type).split(".")[-1].upper()
        lines.append(f"Capability: {artifact.capability_name} ({cap_type})")
        if output:
            lines.append(f"Output:\n{json.dumps(output, indent=2, default=str)}")
        else:
            lines.append("Output: (execution failed or empty)")
    else:
        lines.append("No capability was generated.")

    lines.append(f"\nCompleted in {elapsed_ms / 1000:.1f}s")
    return "\n".join(lines)


def _format_json(state: Any, artifact: Any | None, output: dict | None, elapsed_ms: int) -> str:
    _get = state.get if isinstance(state, dict) else lambda k, d=None: getattr(state, k, d)
    verification_results = _get("verification_results", []) or []

    # Build verification summary for JSON
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="JustInTimeMAS — generate and execute capabilities on demand"
    )
    parser.add_argument("task", nargs="?", type=str, help="Task description")
    parser.add_argument(
        "--file",
        action="append",
        dest="files",
        metavar="PATH",
        help="Input file(s) — repeat for multiple files",
    )
    parser.add_argument(
        "--param",
        action="append",
        dest="params",
        metavar="KEY=VALUE",
        help="Additional key=value parameters",
    )
    parser.add_argument(
        "--skip-verification",
        action="store_true",
        help="Bypass the three-tier verification gate",
    )
    parser.add_argument(
        "--enable-optimizer",
        action="store_true",
        default=None,
        help="Force-enable the granularity optimizer. By default, the optimizer "
        "auto-enables after 20+ invocations have been recorded.",
    )
    parser.add_argument(
        "--no-optimizer",
        action="store_true",
        help="Force-disable the granularity optimizer",
    )
    parser.add_argument(
        "--output-format",
        choices=["json", "human"],
        default="human",
        help="Output format (default: human)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Decompose and classify only — do not generate or execute",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all capabilities in the registry and exit",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Execution timeout for SKILL/AGENT tasks (default: 240s SKILL, 300s AGENT)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    _setup_logging(args.verbose)

    # ------------------------------------------------------------------
    # Validate inputs
    # ------------------------------------------------------------------
    if not args.list and not args.task:
        parser.error("A task description is required (or use --list).")

    file_paths: list[Path] = []
    if args.files:
        for f in args.files:
            fp = Path(f)
            if not fp.exists():
                print(f"Error: file not found: {fp}", file=sys.stderr)
                return 1
            file_paths.append(fp.resolve())

    params: dict[str, str] = {}
    if args.params:
        for p in args.params:
            if "=" not in p:
                print(f"Error: --param must be in KEY=VALUE format, got: {p!r}", file=sys.stderr)
                return 1
            k, _, v = p.partition("=")
            params[k.strip()] = v.strip()

    # ------------------------------------------------------------------
    # Registry + loader setup
    # ------------------------------------------------------------------
    from src.orchestrator.tool_loader import ToolLoader
    from src.registry.metrics import MetricsStore
    from src.registry.store import SkillRegistry, seed_registry

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    registry = SkillRegistry(persist_directory=str(CHROMA_DIR))
    seed_registry(registry)
    loader = ToolLoader()
    # Persist metrics to disk so the optimizer can see invocation history
    # across CLI runs (same pattern as evaluation harness).
    metrics_store = MetricsStore(db_path=str(DATA_DIR / "cli_metrics.duckdb"))

    # ------------------------------------------------------------------
    # --list
    # ------------------------------------------------------------------
    if args.list:
        _cmd_list(registry)
        return 0

    # ------------------------------------------------------------------
    # Build task prompt with file references
    # ------------------------------------------------------------------
    task_prompt = args.task
    if file_paths:
        task_prompt += "\n\nInput files:\n"
        for fp in file_paths:
            task_prompt += f"- {fp}\n"
    if params:
        task_prompt += "\nParameters:\n"
        for k, v in params.items():
            task_prompt += f"- {k}: {v}\n"

    from src.schemas.task import TaskState

    # Optional LangFuse tracing
    invoke_config: dict = {}
    try:
        from langfuse.langchain import CallbackHandler as LangFuseCallbackHandler
        invoke_config["callbacks"] = [LangFuseCallbackHandler()]
    except Exception:
        pass

    initial_state = TaskState(task_description=task_prompt)
    t_start = time.monotonic()

    # ------------------------------------------------------------------
    # --dry-run: plan-only pipeline (decomposition + gap_detection + classifier)
    # ------------------------------------------------------------------
    if args.dry_run:
        from langgraph.graph import END, StateGraph
        from langgraph.types import Command

        from src.orchestrator.classifier import classifier_node
        from src.orchestrator.decomposition import decomposition_node
        from src.orchestrator.gap_detection import gap_detection_node

        def _classifier_then_end(state: TaskState) -> Command:
            """Run classifier but route to END instead of generators."""
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

        try:
            final_state = plan_pipeline.invoke(initial_state, config=invoke_config)
        except Exception as exc:
            print(f"Error: planning pipeline failed: {exc}", file=sys.stderr)
            return 1

        elapsed_ms = int((time.monotonic() - t_start) * 1000)
        _get = (
            final_state.get
            if isinstance(final_state, dict)
            else lambda k, d=None: getattr(final_state, k, d)
        )
        subtasks = _get("subtasks", []) or []
        classified_gaps = _get("classified_gaps", []) or []
        matches = _get("matches", []) or []

        print(f"\nDry-run for: {args.task[:100]}\n")
        print(f"Subtasks ({len(subtasks)}):")
        for i, st in enumerate(subtasks):
            desc = getattr(st, "description", str(st))
            print(f"  {i+1}. {desc}")
        if matches:
            print(f"\nRegistry matches ({len(matches)}):")
            for m in matches:
                cap = getattr(m, "capability", m)
                name = getattr(cap, "name", "?")
                print(f"  - {name}")
        if classified_gaps:
            print(f"\nClassified gaps ({len(classified_gaps)}):")
            for g in classified_gaps:
                cap_type = str(getattr(g, "classified_type", "?")).split(".")[-1].upper()
                desc = getattr(g, "description", str(g))[:80]
                print(f"  [{cap_type}] {desc}")
        print(f"\nCompleted in {elapsed_ms / 1000:.1f}s")
        return 0

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------
    from src.orchestrator.pipeline import create_pipeline

    # Determine whether to run the optimizer:
    # --enable-optimizer forces on, --no-optimizer forces off,
    # otherwise auto-enable after 20+ invocations (enough history for
    # meaningful prune decisions, avoids cold-start waste).
    AUTO_OPTIMIZER_THRESHOLD = 20
    if args.no_optimizer:
        run_optimizer = False
    elif args.enable_optimizer:
        run_optimizer = True
    else:
        invocation_count = metrics_store.invocation_count_total()
        run_optimizer = invocation_count >= AUTO_OPTIMIZER_THRESHOLD
        if run_optimizer:
            print(
                f"Optimizer auto-enabled ({invocation_count} invocations recorded).",
                file=sys.stderr,
            )

    # Always pass metrics_store so gap_detection records registry matches
    # as invocations (builds history for future optimizer runs).
    pipeline = create_pipeline(
        registry,
        loader,
        run_optimizer=run_optimizer,
        metrics_store=metrics_store,
        verification_enabled=not args.skip_verification,
    ).compile()

    try:
        final_state = pipeline.invoke(initial_state, config=invoke_config)
    except Exception as exc:
        print(f"Error: pipeline failed: {exc}", file=sys.stderr)
        return 1

    elapsed_ms = int((time.monotonic() - t_start) * 1000)

    # ------------------------------------------------------------------
    # Report optimizer actions (if it ran)
    # ------------------------------------------------------------------
    if run_optimizer:
        try:
            events = metrics_store._conn.execute(
                "SELECT event_type, COUNT(*) FROM capability_events "
                "GROUP BY event_type ORDER BY event_type"
            ).fetchall()
            event_summary = {et: n for et, n in events}
            pruned = event_summary.get("pruned", 0)
            split = event_summary.get("split", 0)
            merged = event_summary.get("merged", 0)
            created = event_summary.get("created", 0)
            if pruned or split or merged:
                print(
                    f"Optimizer: pruned {pruned}, split {split}, merged {merged} "
                    f"({created} new capabilities created)",
                    file=sys.stderr,
                )
            else:
                print("Optimizer: no actions taken (registry stable).", file=sys.stderr)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Select artifact and execute
    # ------------------------------------------------------------------
    artifact = _select_artifact(final_state)
    output: dict | None = None

    if artifact:
        from src.schemas.capability import CapabilityType

        cap_type = artifact.capability_type
        try:
            if cap_type == CapabilityType.MCP_TOOL:
                output = _execute_mcp_tool(artifact, file_paths, params) or None
            elif cap_type in (CapabilityType.SKILL, CapabilityType.AGENT):
                output = (
                    _execute_skill_or_agent(
                        artifact, task_prompt, file_paths, timeout_s=args.timeout
                    )
                    or None
                )
        except _ExecutionTimeout as exc:
            print(f"Error: {exc}", file=sys.stderr)
            output = None
        except Exception as exc:
            logging.warning("Artifact execution error: %s", exc)
            output = None

    # ------------------------------------------------------------------
    # Registry match fallback: no artifact but matches found
    # ------------------------------------------------------------------
    if artifact is None and not output:
        _get = (
            final_state.get
            if isinstance(final_state, dict)
            else lambda k, d=None: getattr(final_state, k, d)
        )
        matches = _get("matches", []) or []
        if matches:
            first_match = matches[0]
            cap = getattr(first_match, "capability", first_match)
            meta = getattr(cap, "metadata", {}) or {}
            source_code = meta.get("source_code")

            if source_code:
                from src.schemas.capability import CapabilityType
                from src.schemas.task import GeneratedArtifact

                cap_type_val = getattr(cap, "capability_type", None)
                if isinstance(cap_type_val, str):
                    cap_type_val = CapabilityType(cap_type_val)

                proxy_artifact = GeneratedArtifact(
                    capability_name=getattr(cap, "name", "registry-match"),
                    capability_type=cap_type_val or CapabilityType.MCP_TOOL,
                    source_code=source_code,
                )
                artifact = proxy_artifact
                try:
                    if proxy_artifact.capability_type == CapabilityType.MCP_TOOL:
                        output = _execute_mcp_tool(proxy_artifact, file_paths, params) or None
                    elif proxy_artifact.capability_type in (CapabilityType.SKILL, CapabilityType.AGENT):
                        output = (
                            _execute_skill_or_agent(
                                proxy_artifact, task_prompt, file_paths, timeout_s=args.timeout
                            )
                            or None
                        )
                except _ExecutionTimeout as exc:
                    print(f"Error: {exc}", file=sys.stderr)
                    output = None
                except Exception as exc:
                    logging.warning("Registry match execution error: %s", exc)
                    output = None
            else:
                # Seeded capabilities are descriptors without source code.
                # The pipeline found a match so it skipped generation.
                # Report the match to the user.
                cap_name = getattr(cap, "name", "?")
                print(
                    f"\nNote: Registry matched '{cap_name}' (a seeded capability "
                    "descriptor without executable source code). The pipeline "
                    "skipped generation because a match was found.\n"
                    "The matched capability confirms the system can handle this "
                    "task type. To force JIT generation of an executable tool, "
                    "rephrase the task to avoid matching existing registry entries.",
                    file=sys.stderr,
                )

    # ------------------------------------------------------------------
    # Record invocation in MetricsStore for optimizer visibility
    # ------------------------------------------------------------------
    if artifact is not None:
        try:
            from src.schemas.metrics import InvocationRecord

            record = InvocationRecord(
                capability_id=getattr(artifact, "capability_name", "unknown"),
                task_id=task_prompt[:64],  # truncate for storage
                success=output is not None and bool(output),
                token_cost=0,  # not tracked inline
                latency_ms=float(elapsed_ms),
            )
            metrics_store.record_invocation(record)
        except Exception:  # noqa: BLE001
            pass  # best-effort, don't fail the CLI

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    if args.output_format == "json":
        print(_format_json(final_state, artifact, output, elapsed_ms))
    else:
        print(_format_human(final_state, artifact, output, elapsed_ms))

    return 0 if (artifact is not None and output) else 1


if __name__ == "__main__":
    sys.exit(main())
