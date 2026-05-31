"""Split coverage verifier for optimizer split operations.

This module replays newly generated split variants against the parent
capability's recorded failure cases. It is not an additional verification
tier. The three-tier gate certifies each variant as a standalone artifact;
this coverage check verifies whether the variants collectively address the
parent's observed failure modes.

"Handled" means "the recorded failure mode changed", not "the variant is
proven correct". Without external ground truth or an LLM judge, this module
can only compare replay behavior against the parent's recorded
``InvocationRecord``:

- **Error-case** (parent's ``error_message is not None``): variant
  handles the case iff it runs on the recorded ``input_args`` without
  raising. Absence of error IS the fix — the failure was an error, the
  variant doesn't error, the failure mode is fixed. Strong signal.

- **Wrong-output-case** (parent's ``error_message is None``, ``success
  is False``, ``actual_output`` is some recorded wrong-shape dict):
  variant handles the case iff it runs without raising AND produces
  output ≠ parent's ``actual_output``. "Variant does something
  different" is not "variant is correct", but absent ground truth it's
  the strongest achievable signal. Medium signal.

- **Same-output-case** (variant produces the same output as the parent
  on the same input): not handled. The variant did not differentiate from
  the parent's recorded wrong output.

Coverage replay is implemented for deterministic ``MCP_TOOL`` variants.
``SKILL`` and ``AGENT`` variants return
``CoverageReport(all_covered=True, status="unverifiable_type")`` because
their execution is LLM-mediated and does not provide stable per-input
failure semantics. For those capability types, the three-tier verification
gate still runs per variant; collective replay coverage is simply not
applicable.

**Never raises.** All failure paths are captured in the returned
``CoverageReport`` with ``execution_errors`` populated. Callers
(``_split_tools`` / ``_split_skills`` / ``_split_agents``) should
treat ``all_covered=False`` as a rollback trigger, the same way they
treat a failed ``verify_and_register_or_rollback`` call.
"""

from __future__ import annotations

import json
import logging
import textwrap
from typing import Literal

from pydantic import BaseModel, Field

from src.schemas.capability import CapabilityType
from src.schemas.metrics import InvocationRecord
from src.schemas.task import GeneratedArtifact
from src.verification.behavioral import _extract_function_name
from src.verification.sandbox_tool import resolve_extra_packages, sandbox_execute

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


CoverageStatus = Literal["verified", "vacuous", "unverifiable_type", "error"]


class CoverageReport(BaseModel):
    """Result of running a split's variants against the parent's failure cases.

    ``all_covered`` is True iff every recorded failure case has at least
    one variant that handled it (under the failure-mode-fix semantics
    in this module's docstring). Empty failure case lists and non-
    MCP_TOOL variant types count as vacuously covered.

    Callers should treat ``all_covered=False`` as a rollback trigger —
    the same semantics as ``verify_and_register_or_rollback`` returning
    ``(False, reason)``. The split operation should then leave the
    parent active and discard the variants.
    """

    all_covered: bool = Field(
        ...,
        description=(
            "True iff every recorded failure case has at least one "
            "variant that handled it, vacuously True for empty failure "
            "case lists, and vacuously True for non-MCP_TOOL variant "
            "types because deterministic replay coverage is not "
            "applicable to LLM-mediated executors."
        ),
    )
    status: CoverageStatus = Field(
        ...,
        description=(
            "Why ``all_covered`` has its current value. "
            "``verified`` — every case was actually replayed against at "
            "least one variant and passed the failure-mode-fix check. "
            "``vacuous`` — no failure cases were provided, so coverage "
            "is trivially satisfied. "
            "``unverifiable_type`` — variants are SKILL or AGENT, which "
            "fall outside the coverage verifier's deterministic-execution "
            "scope. "
            "``error`` — replay infrastructure itself failed; "
            "``execution_errors`` contains details and ``all_covered`` "
            "is set to ``False`` to trigger rollback."
        ),
    )
    coverage_map: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Maps failure-case task_id to the list of variant "
            "capability names that handled it. Populated only under "
            "``status='verified'`` so callers can inspect which "
            "variants specialized where."
        ),
    )
    uncovered_cases: list[str] = Field(
        default_factory=list,
        description=(
            "task_ids of failure cases that no variant handled. Empty "
            "when ``all_covered=True``. Populated under "
            "``status='verified'`` when at least one case was uncovered."
        ),
    )
    execution_errors: list[str] = Field(
        default_factory=list,
        description=(
            "Human-readable descriptions of replay-infrastructure "
            "failures — JSON parse errors on ``input_args``, sandbox "
            "timeouts, unexpected exceptions in the dispatch path. "
            "Distinct from 'variant didn't handle a case' (which is "
            "recorded via ``uncovered_cases``). Non-empty execution_errors "
            "do not necessarily imply ``all_covered=False`` — a case "
            "may have execution errors on some variants but still be "
            "handled by another variant. If execution errors dominate, "
            "``status='error'`` and ``all_covered=False``."
        ),
    )

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# MCP_TOOL replay helpers (deterministic executor path)
# ---------------------------------------------------------------------------


def _build_replay_code(
    source_code: str, func_name: str, input_args_json: str,
) -> str:
    """Build a Python snippet that replays the variant on recorded input_args.

    Mirrors ``src.verification.behavioral._build_sandbox_code`` but
    takes a pre-serialized JSON string for the kwargs instead of a dict,
    so we can pass through the exact bytes recorded on
    ``InvocationRecord.input_args`` without round-tripping through
    Python types.
    """
    return textwrap.dedent(f"""\
        import asyncio
        import json

        __name__ = "not_main"  # prevent if __name__ == "__main__": mcp.run() from firing
        # --- Inline the tool source ---
        {textwrap.indent(source_code, "        ").strip()}
        # --- End tool source ---

        async def _run():
            try:
                _kwargs = json.loads({input_args_json!r})
            except Exception as exc:
                print(json.dumps({{
                    "result": None,
                    "error": f"input_args parse failed: {{type(exc).__name__}}: {{exc}}",
                }}))
                return
            try:
                result = await {func_name}(**_kwargs)
                # Sort keys so replay output compares consistently with
                # the parent's recorded ``actual_output``.
                serialized = json.dumps(result, default=str, sort_keys=True)
                print(json.dumps({{"result": serialized, "error": None}}))
            except Exception as exc:
                print(json.dumps({{
                    "result": None,
                    "error": f"{{type(exc).__name__}}: {{exc}}",
                }}))

        asyncio.run(_run())
    """)


def _parse_replay_output(sandbox_result: dict) -> dict | None:
    """Parse the JSON output from a replay run. Returns None on failure.

    Delegates to the shared ``parse_sandbox_last_json_line`` helper so
    coverage replay tolerates debug-print prefixes before the final JSON
    line.
    """
    from src.verification.sandbox_parse import parse_sandbox_last_json_line
    return parse_sandbox_last_json_line(sandbox_result)


def _variant_handles_case(
    variant: GeneratedArtifact,
    failure: InvocationRecord,
    *,
    timeout: int = 15,
    allow_network: bool = False,
) -> tuple[bool, str | None]:
    """Return (handled, error_message) for a single variant × case replay.

    Applies the failure-mode-fix semantics:
    - Error-case (parent has ``error_message``): variant handles iff
      replay runs without raising.
    - Wrong-output-case (parent has ``actual_output``, no
      ``error_message``): variant handles iff replay runs without
      raising AND produces output ≠ parent's ``actual_output``.
    - Same-output replay: not handled (variant didn't fix anything).
    - Replay infrastructure failure (syntax error in variant source,
      sandbox timeout, JSON parse failure on ``input_args``, etc.):
      return ``(False, error_description)`` — caller logs the
      infrastructure error and treats the case as not handled by this
      specific variant (other variants may still handle it).

    Never raises.
    """
    source_code = variant.source_code
    if not source_code:
        return False, f"variant '{variant.capability_name}' has no source_code"

    func_name = _extract_function_name(source_code)
    if not func_name:
        return False, (
            f"variant '{variant.capability_name}' has no public async function"
        )

    input_args_json = failure.input_args
    if not input_args_json:
        return False, (
            f"failure case task_id='{failure.task_id}' has no input_args "
            "(legacy record or never captured)"
        )

    # Detect the truncation sentinel emitted by ``serialize_for_invocation``
    # on oversize payloads. Truncated cases are unreproducible, so report
    # that explicitly in ``execution_errors``.
    try:
        parsed_args = json.loads(input_args_json)
    except (json.JSONDecodeError, ValueError):
        return False, (
            f"failure case task_id='{failure.task_id}' has invalid JSON "
            "in input_args (legacy truncation); case unreproducible"
        )
    if isinstance(parsed_args, dict) and parsed_args.get("__truncated__"):
        size = parsed_args.get("__original_size_bytes__", "unknown")
        return False, (
            f"failure case task_id='{failure.task_id}' had input_args "
            f"truncated (original size {size} bytes); case unreproducible"
        )

    # Resolve pip packages from the variant's declaration or via import
    # analysis — same logic as ``behavioral.run`` uses.
    declared_pkgs = variant.pip_packages if variant.pip_packages else None
    extra_pkgs = declared_pkgs or resolve_extra_packages(source_code) or None

    code = _build_replay_code(source_code, func_name, input_args_json)
    try:
        sandbox_result = sandbox_execute(
            code,
            timeout=timeout,
            extra_packages=extra_pkgs,
            allow_network=allow_network,
        )
    except Exception as exc:  # noqa: BLE001
        return False, (
            f"sandbox_execute raised unexpectedly on variant "
            f"'{variant.capability_name}' × task '{failure.task_id}': "
            f"{type(exc).__name__}: {exc}"
        )

    parsed = _parse_replay_output(sandbox_result)
    if parsed is None:
        stderr = sandbox_result.get("stderr", "")[:200]
        return False, (
            f"could not parse replay output for variant "
            f"'{variant.capability_name}' × task '{failure.task_id}' "
            f"(stderr: {stderr!r})"
        )

    replay_error = parsed.get("error")
    replay_result = parsed.get("result")

    # --- Error-case semantics ---
    #
    # Parent errored → variant handles iff replay runs without raising.
    # A missing ``error_message`` on the parent means this is NOT an
    # error case, so we fall through to wrong-output-case semantics.
    if failure.error_message:
        if replay_error is None:
            return True, None
        return False, None

    # --- Wrong-output-case semantics ---
    #
    # Parent didn't error but the invocation was classified success=False
    # (otherwise get_recent_failures wouldn't have returned it). The
    # variant handles iff it runs without raising AND produces output
    # different from the parent's recorded ``actual_output``.
    if replay_error is not None:
        # Variant errored on an input the parent didn't error on — this
        # is a regression, not a fix. Not handled.
        return False, None

    if replay_result is None:
        # Replay returned no result and no error — shouldn't happen
        # with the current _build_replay_code, but guard defensively.
        return False, "replay returned neither result nor error"

    parent_output = failure.actual_output
    if parent_output is None:
        # Parent didn't error AND didn't record actual_output — we
        # don't have a reference to compare against. Can't verify.
        # Count as not handled (conservative).
        return False, None

    # Compare parsed values, not strings, so key order, whitespace, and
    # int-vs-float representation differences do not create false coverage
    # evidence. Fall back to string comparison for legacy non-JSON rows.
    if _semantic_equal(replay_result, parent_output):
        # Same output as the parent → variant didn't differentiate.
        # Not handled.
        return False, None

    # Replay ran, produced output, and it differs from the parent's
    # recorded wrong-output. Failure mode is differentiated.
    return True, None


def _semantic_equal(a: str, b: str) -> bool:
    """True iff ``a`` and ``b`` parse as JSON to equal Python values.

    Falls back to string equality if either side fails to parse —
    preserves legacy behavior while fixing the key-order asymmetry.
    """
    try:
        return json.loads(a) == json.loads(b)
    except (json.JSONDecodeError, TypeError, ValueError):
        return a == b


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def verify_coverage(
    variants: list[GeneratedArtifact],
    failure_cases: list[InvocationRecord],
    *,
    allow_network: bool = False,
) -> CoverageReport:
    """Verify split variants collectively cover the parent's failure cases.

    The top-level entry point used by the per-type split helpers.
    Dispatches by the variants' capability type:

    - **MCP_TOOL** (deterministic): replays each failure case against
      each variant via ``sandbox_execute``, applies the failure-mode-
      fix semantics from :func:`_variant_handles_case`, and returns a
      full verified report.
    - **SKILL / AGENT** (LLM-mediated, nondeterministic): returns a
      ``CoverageReport(all_covered=True, status="unverifiable_type")``
      per the MCP_TOOL-only scope documented in the module docstring.

    Empty variant list or empty failure case list → vacuously covered.

    Never raises. All failure paths are captured in the returned
    report with ``execution_errors`` populated. Callers should treat
    ``all_covered=False`` as a rollback trigger.
    """
    # --- Edge cases ---

    if not variants:
        return CoverageReport(
            all_covered=True,
            status="vacuous",
            execution_errors=["no variants provided — vacuously covered"],
        )

    if not failure_cases:
        return CoverageReport(
            all_covered=True,
            status="vacuous",
        )

    # --- Type dispatch ---
    #
    # Variants from ``_split_tools`` / ``_split_skills`` / ``_split_agents``
    # are expected to be uniformly typed. Cross-type variants would be
    # a caller bug; we dispatch on the first variant's type and log a
    # warning if the others don't match.

    first_type = variants[0].capability_type
    mixed = [v for v in variants if v.capability_type != first_type]
    if mixed:
        logger.warning(
            "verify_coverage: variants have mixed capability types: %s. "
            "Dispatching on first variant's type (%s).",
            sorted({v.capability_type.value for v in variants}),
            first_type.value,
        )

    if first_type != CapabilityType.MCP_TOOL:
        logger.info(
            "verify_coverage: variants are %s — returning unverifiable_type "
            "per MCP_TOOL-only scope. SKILL/AGENT splits rely "
            "on the three-tier gate per variant without collective coverage "
            "verification.",
            first_type.value,
        )
        return CoverageReport(
            all_covered=True,
            status="unverifiable_type",
        )

    # --- MCP_TOOL replay path ---

    coverage_map: dict[str, list[str]] = {}
    uncovered: list[str] = []
    execution_errors: list[str] = []

    for failure in failure_cases:
        case_key = failure.task_id
        handlers: list[str] = []

        for variant in variants:
            try:
                handled, error_msg = _variant_handles_case(
                    variant, failure, allow_network=allow_network,
                )
            except Exception as exc:  # noqa: BLE001
                # Defensive guard — _variant_handles_case is "never raises"
                # but if it does, the coverage verifier must still return
                # a report and record the infrastructure error.
                execution_errors.append(
                    f"_variant_handles_case raised on variant "
                    f"'{variant.capability_name}' × task '{case_key}': "
                    f"{type(exc).__name__}: {exc}"
                )
                continue

            if error_msg:
                execution_errors.append(
                    f"variant '{variant.capability_name}' × task '{case_key}': "
                    f"{error_msg}"
                )
            if handled:
                handlers.append(variant.capability_name)

        if handlers:
            coverage_map[case_key] = handlers
        else:
            uncovered.append(case_key)
            logger.info(
                "verify_coverage: task '%s' is uncovered — no variant "
                "handled the failure mode",
                case_key,
            )

    all_covered = len(uncovered) == 0

    # If every single attempt produced an execution error AND nothing
    # was covered, the replay infrastructure itself is broken — surface
    # this as status='error' rather than 'verified' so callers can
    # distinguish "variants genuinely didn't cover anything" from
    # "replay infrastructure failed on everything".
    # Emit a LangFuse event so coverage verification is traceable alongside
    # split_variant and split_rollback events. Never raises.
    try:
        from src.orchestrator.tracing import emit_langfuse_event
        _cov_status = (
            "error"
            if (not all_covered and not coverage_map and execution_errors)
            else "verified"
        )
        emit_langfuse_event(
            None,
            f"verification.coverage.{_cov_status}",
            "WARNING" if not all_covered else "DEFAULT",
            {
                "all_covered": all_covered,
                "n_variants": len(variants),
                "n_failure_cases": len(failure_cases),
                "n_covered": len(coverage_map),
                "n_uncovered": len(uncovered),
                "n_execution_errors": len(execution_errors),
                "variant_names": [v.capability_name for v in variants],
            },
        )
    except Exception:  # noqa: BLE001
        pass

    if not all_covered and not coverage_map and execution_errors:
        return CoverageReport(
            all_covered=False,
            status="error",
            coverage_map=coverage_map,
            uncovered_cases=uncovered,
            execution_errors=execution_errors,
        )

    return CoverageReport(
        all_covered=all_covered,
        status="verified",
        coverage_map=coverage_map,
        uncovered_cases=uncovered,
        execution_errors=execution_errors,
    )
