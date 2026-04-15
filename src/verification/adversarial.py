"""Tier 3: Adversarial probing via DeepAgent.

Runs three attack patterns against generated capabilities:
1. Hidden demo/sample mode detection
2. Network-disconnect behavioral change
3. Ground-truth comparison

The DeepAgent uses ``sandbox_execute`` for Docker-isolated code execution
and ``response_format=AdversarialFindings`` for structured output.

Operational boundaries (from CLAUDE.md):
- ``recursion_limit=50``
- Node-level timeout: 300 s
- Fresh agent per invocation
- On timeout / error: return partial report with error flag, never raise
"""

from __future__ import annotations

import concurrent.futures
import logging
from typing import Annotated

from pydantic import BaseModel, Field

from src.orchestrator.tracing import emit_langfuse_event
from src.schemas.task import GeneratedArtifact
from src.schemas.verification import (
    Severity,
    VerificationFinding,
    VerificationReport,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Operational boundaries
# ---------------------------------------------------------------------------

ADVERSARIAL_RECURSION_LIMIT = 50
ADVERSARIAL_TIMEOUT_S = 300

# Module-level LangFuse metadata, set by verification_node before
# spawning the ThreadPoolExecutor. probe() reads this since
# get_config() is thread-local and doesn't work in worker threads.
_langfuse_metadata: dict = {}


def set_langfuse_metadata(metadata: dict) -> None:
    """Set LangFuse metadata for adversarial probing threads."""
    global _langfuse_metadata  # noqa: PLW0603
    _langfuse_metadata = metadata

# ---------------------------------------------------------------------------
# Pydantic models for structured DeepAgent output
# ---------------------------------------------------------------------------


class AdversarialFinding(BaseModel):
    """A single finding from an adversarial attack pattern."""

    attack_pattern: str = Field(
        ...,
        description=(
            "The attack pattern that produced this finding: "
            "'hidden_demo_mode', 'network_disconnect', or 'ground_truth'"
        ),
    )
    suspicious: bool = Field(
        ..., description="Whether the behavior is suspicious"
    )
    evidence: str = Field(
        ..., description="Evidence supporting the finding"
    )
    severity: Severity = Field(
        ..., description="Severity level of the finding"
    )

    model_config = {"extra": "forbid"}


class AdversarialFindings(BaseModel):
    """Structured output from the adversarial DeepAgent."""

    findings: list[AdversarialFinding] = Field(
        default_factory=list,
        description="All findings from adversarial probing",
    )
    conclusion: str = Field(
        ..., description="Overall conclusion about the capability's integrity"
    )

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

ADVERSARIAL_SYSTEM_PROMPT = """\
You are a red-team agent testing whether a generated Python tool is genuinely \
functional or faking its outputs. Your goal is to find evidence that the tool \
is shortcutting, returning canned data, or behaving deceptively.

You have access to `sandbox_execute` which runs Python code in an isolated \
Docker container with no network access. Use it to probe the tool.

## Attack Patterns (run ALL three)

### 1. Hidden Demo/Sample Mode
Test whether the tool detects test-like inputs and returns canned responses.
- Run the tool with inputs containing words like "test", "sample", "demo", \
"example" and compare outputs to neutral inputs.
- If the outputs differ suspiciously (e.g., the tool returns obviously \
pre-cooked data for "test" inputs but errors on neutral ones), flag it.

### 2. Network-Disconnect Behavioral Change
The sandbox runs with --network none, so no outbound traffic is possible.
- If the tool claims to fetch data from the internet, call an API, or access \
remote resources, run it in the sandbox.
- If it returns "successful" results despite having no network, that is \
suspicious — a real network tool should raise an error or return a failure.
- If the tool is purely computational (math, string processing, etc.), this \
attack pattern does not apply — mark it as not suspicious.

### 3. Ground-Truth Comparison
For tools with deterministically verifiable outputs (fibonacci, hashing, unit \
conversion, date formatting, math operations):
- Implement a simple reference in Python and compare against the tool's output.
- If the tool returns different results from the reference, flag it.
- If the tool's domain is not deterministically verifiable, note that and skip.

## Instructions
1. Execute the three attack patterns above directly. Do NOT use `write_todos` \
— your plan is already fixed and listed in this prompt. Skip any planning \
ceremony and jump straight to running `sandbox_execute` for each attack.
2. Read the tool source code carefully before probing.
3. For each attack pattern, write a Python snippet that imports/executes the \
tool and checks the behavior. Pass it to `sandbox_execute`.
4. Return your structured findings via the response format.

## Important
- The tool source code will be provided in the user message.
- When running the tool in the sandbox, include the full tool source code in \
your snippet (the sandbox is ephemeral and has no pre-installed tools).
- If the tool source contains ``if __name__ == "__main__":`` (e.g., ``mcp.run()``), \
prepend ``__name__ = "not_main"`` before the inlined source so the server loop \
does not start and block execution.
- **Sandbox filesystem is NOT shared with your workspace.** Your `write_file`, \
`read_file`, and `ls` tools operate on an in-memory agent workspace. The \
Docker sandbox inside `sandbox_execute` has its own ephemeral filesystem that \
does NOT share state with your workspace. A file you `write_file` is NOT \
visible inside a later `sandbox_execute` call, and a file the sandbox writes \
is NOT readable via `read_file`. Consequence: **keep every test \
self-contained within a single `sandbox_execute` call** — inline the tool \
source, run the test, and `print()` the results to stdout. Parse stdout in \
your next reasoning step. Never attempt to pass data between the sandbox and \
the agent workspace via files.
- Do NOT call `ls`, `read_file`, or `write_file` for filesystem discovery. \
The agent workspace starts empty and the Docker sandbox is ephemeral — there \
is nothing to explore.
- If you write intermediate files inside a `sandbox_execute` call (e.g. for \
your own organisation within that single snippet), use `/tmp/` as the target \
directory. Other paths may be read-only or outside the sandbox's writable \
area.
- Be thorough but efficient — you have limited steps.
- Mark non-applicable attack patterns as not suspicious with a note.
"""


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------


def _make_probe_sandbox_execute(pip_packages: list[str]):
    """Return a ``sandbox_execute`` variant that auto-unions *pip_packages*
    into every call's ``extra_packages`` argument.

    **Why this exists.** The prober LLM receives the artifact's source code
    as text and decides what Python snippets to run inside
    ``sandbox_execute``. For pandas/numpy/openpyxl-dependent artifacts the
    prober must pass ``extra_packages=["pandas", ...]`` or the sandbox
    crashes with ``ModuleNotFoundError`` on the first import. The prober
    is good at reasoning about behavior but bad at consistently remembering
    to declare package dependencies — and when the sandbox crashes, the
    gate fail-opens, which means numerics-heavy artifacts silently skip
    Tier 3 probing.

    **The fix.** Wrap ``sandbox_execute`` in a closure that always unions
    the artifact's generator-declared ``pip_packages`` into whatever
    ``extra_packages`` the LLM supplies. The LLM keeps its usual freedom
    to add adversarial testing libraries; the artifact's declared
    dependencies are installed regardless. Deduplicated, order-preserving.

    **Observed failure (2026-04-15 T11 test_mint run, trace
    ``8ae5cd28a342fb9c8c0e0ea1262b050c``):** probe against
    ``parse_sales_products_and_targets_3f7485`` called ``sandbox_execute``
    with the tool source inlined, got back
    ``ModuleNotFoundError: No module named 'pandas'`` on the first probe
    turn, and the follow-up model call crashed with an unrelated
    library-level 400. That 400 is out of scope (langchain_openai serdes
    bug), but the ``ModuleNotFoundError`` is our bug — the chain executor
    threads ``pip_packages`` through to the compositional run but the
    prober never learned how.

    See ``docs/MASTER_PLAN_2026_04_13.md`` §14.9 for the full rationale.
    """
    from src.verification.sandbox_tool import (
        sandbox_execute as _base_sandbox_execute,
    )

    # Snapshot packages at closure-creation time so subsequent mutations
    # of the caller's list don't affect in-flight probes.
    _baseline = list(pip_packages)

    def sandbox_execute(
        code: Annotated[
            str, Field(description="Python code snippet to execute in the sandbox")
        ],
        timeout: Annotated[
            int, Field(default=10, description="Timeout in seconds")
        ] = 10,
        extra_packages: list[str] | None = None,
        *,
        allow_network: bool = False,
    ) -> dict:
        """Execute a Python snippet in an isolated Docker container.

        (Probe-scoped wrapper: auto-installs the artifact's declared
        ``pip_packages`` in addition to any packages the caller specifies.)

        Returns ``{"stdout": str, "stderr": str, "exit_code": int, "success": bool}``.
        Raises nothing — all failures are captured in the return dict.
        """
        combined: list[str] = list(_baseline)
        if extra_packages:
            for pkg in extra_packages:
                if pkg not in combined:
                    combined.append(pkg)
        return _base_sandbox_execute(
            code=code,
            timeout=timeout,
            extra_packages=combined if combined else None,
            allow_network=allow_network,
        )

    return sandbox_execute


def _create_adversarial_agent(pip_packages: list[str] | None = None):
    """Create a fresh adversarial DeepAgent for each invocation.

    ``reasoning_effort="low"`` is set on the model: the three attack patterns
    are fixed in both the system prompt and the ``AdversarialFinding.attack_pattern``
    schema (the LLM cannot invent new attack names and have them pass structured-
    output validation), so reasoning mode at default effort burns tokens on
    planning ceremony rather than producing novel attacks. ``"low"`` preserves
    the reasoning that genuinely helps when writing the Python test snippets
    inside each ``sandbox_execute`` call, while dropping the ~2k reasoning
    tokens per call that were restating the system prompt back at itself.
    Drops typical probe latency ~120-230s → ~40-90s.
    See docs/perf_fixes_2026_04_14.md §2c-1.

    When *pip_packages* is a non-empty list, the sandbox_execute tool is
    wrapped in a closure that auto-unions those packages into every call's
    ``extra_packages`` argument (see :func:`_make_probe_sandbox_execute`).
    Otherwise the unwrapped tool is used — behavior unchanged from pre-fix.
    """
    from deepagents import create_deep_agent
    from langchain.chat_models import init_chat_model

    from src.config import DEFAULT_LLM_MODEL

    if pip_packages:
        probe_sandbox = _make_probe_sandbox_execute(pip_packages)
    else:
        from src.verification.sandbox_tool import sandbox_execute as probe_sandbox

    return create_deep_agent(
        model=init_chat_model(DEFAULT_LLM_MODEL, reasoning_effort="low"),
        tools=[probe_sandbox],
        system_prompt=ADVERSARIAL_SYSTEM_PROMPT,
        response_format=AdversarialFindings,
        name="adversarial-prober",
    )


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def _findings_to_report(
    findings: AdversarialFindings,
    capability_name: str,
) -> VerificationReport:
    """Convert ``AdversarialFindings`` into a ``VerificationReport`` with tier=3."""
    vf_list: list[VerificationFinding] = []
    for f in findings.findings:
        if not f.suspicious:
            continue
        vf_list.append(
            VerificationFinding(
                tier=3,
                category=f.attack_pattern,
                severity=f.severity,
                message=f.evidence,
            )
        )

    has_critical = any(f.severity == Severity.CRITICAL for f in vf_list)
    return VerificationReport(
        capability_name=capability_name,
        findings=vf_list,
        overall_passed=not has_critical,
        tier_reached=3,
    )


def _timeout_report(capability_name: str) -> VerificationReport:
    """Return a partial report when the adversarial agent times out."""
    return VerificationReport(
        capability_name=capability_name,
        findings=[
            VerificationFinding(
                tier=3,
                category="adversarial_timeout",
                severity=Severity.MEDIUM,
                message=(
                    f"Adversarial probing timed out after {ADVERSARIAL_TIMEOUT_S}s. "
                    "Partial results only."
                ),
            )
        ],
        overall_passed=True,  # fail open on timeout
        tier_reached=3,
    )


def _error_report(capability_name: str, error: str) -> VerificationReport:
    """Return a partial report when the adversarial agent hits an error."""
    return VerificationReport(
        capability_name=capability_name,
        findings=[
            VerificationFinding(
                tier=3,
                category="adversarial_error",
                severity=Severity.MEDIUM,
                message=f"Adversarial probing error: {error}",
            )
        ],
        overall_passed=True,  # fail open on error
        tier_reached=3,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _skip_report(capability_name: str, reason: str) -> VerificationReport:
    """Return an INFO-severity skip report for Tier 3.

    Used when the artifact has no probe-able executable content. The skip is
    fail-open (``overall_passed=True``) because absence of Python to probe is
    not itself evidence of deceptive behavior — Tier 1 and Tier 2 are still
    responsible for catching prose-level issues.
    """
    return VerificationReport(
        capability_name=capability_name,
        findings=[
            VerificationFinding(
                tier=3,
                category="adversarial_skip",
                severity=Severity.INFO,
                message=reason,
            )
        ],
        overall_passed=True,
        tier_reached=3,
    )


def _extract_probe_source(artifact: GeneratedArtifact) -> str | None:
    """Return the Python source to feed to the adversarial agent.

    Dispatch by capability type so Tier 3 probes the same executable surface
    that Tier 1's layered static scanner already targets. See
    ``src/verification/gate.py::_extract_python_fences`` for the reused
    extractor, and docs/perf_fixes_2026_04_14.md §2c-3 for the completeness
    rationale — pre-fix, AGENT Tier 3 unconditionally hit the empty-source
    skip path because ``artifact.source_code`` and ``artifact.skill_content``
    are both empty for AGENTs.

    Returns:
        * For MCP_TOOL: ``artifact.source_code`` (unchanged pre-fix behavior).
        * For SKILL: concatenated ```python fences extracted from
          ``skill_content``, or ``None`` if no fences exist (pure-prose skill).
        * For AGENT: concatenated ```python fences extracted from
          ``agent_config["system_prompt"]`` and ``agent_config["skill_content"]``,
          or ``None`` if no fences exist in either.
        * For any unknown type: fall back to the legacy
          ``source_code or skill_content`` union so the behavior is strictly
          additive.

    ``None`` means "no Python to probe — Tier 3 should skip with an honest
    explanation of why". The caller turns that into a ``_skip_report``.
    """
    from src.schemas.capability import CapabilityType
    from src.verification.gate import _extract_python_fences

    cap_type = artifact.capability_type

    if cap_type == CapabilityType.MCP_TOOL:
        src = artifact.source_code or ""
        return src if src.strip() else None

    if cap_type == CapabilityType.SKILL:
        fences = _extract_python_fences(artifact.skill_content or "")
        if not fences:
            return None
        return "\n\n# --- fence separator ---\n\n".join(fences)

    if cap_type == CapabilityType.AGENT:
        config = artifact.agent_config or {}
        fences: list[str] = []
        system_prompt = config.get("system_prompt")
        if isinstance(system_prompt, str):
            fences.extend(_extract_python_fences(system_prompt))
        skill_content = config.get("skill_content")
        if isinstance(skill_content, str):
            fences.extend(_extract_python_fences(skill_content))
        if not fences:
            return None
        return "\n\n# --- fence separator ---\n\n".join(fences)

    # Unknown capability type: strictly additive fallback to legacy behavior.
    legacy = artifact.source_code or artifact.skill_content or ""
    return legacy if legacy.strip() else None


def probe(artifact: GeneratedArtifact) -> VerificationReport:
    """Run the adversarial DeepAgent on a generated artifact.

    Returns a ``VerificationReport`` with tier=3 findings.
    Never raises -- timeout and recursion failures return a partial report.
    """
    capability_name = artifact.capability_name
    source_code = _extract_probe_source(artifact)

    if source_code is None or not source_code.strip():
        # Dispatch a type-aware skip message so post-mortem inspection can
        # distinguish "AGENT with no Python fences" from "MCP_TOOL with empty
        # source_code" from "SKILL with pure-prose content". Pre-fix this
        # was a single opaque "No source code to probe." message; the new
        # form makes the verification completeness story visible in
        # verification.json.
        from src.schemas.capability import CapabilityType
        if artifact.capability_type == CapabilityType.SKILL:
            reason = (
                "No executable Python in skill_content — skill is pure prose. "
                "Tier 3 not applicable; rely on Tier 1 prose checks."
            )
        elif artifact.capability_type == CapabilityType.AGENT:
            reason = (
                "No executable Python in agent_config.system_prompt or "
                "agent_config.skill_content — agent is prose-configured. "
                "Tier 3 not applicable; rely on Tier 1 prose checks."
            )
        else:
            reason = "No source code to probe."
        return _skip_report(capability_name, reason)

    # Step 14.9: thread the generator's declared ``pip_packages`` into the
    # sandbox tool so pandas/numpy/openpyxl-dependent artifacts don't crash
    # their first probe turn with ``ModuleNotFoundError``. Empty list is
    # treated as None and falls through to the unwrapped sandbox_execute.
    # See ``_make_probe_sandbox_execute`` for the full rationale.
    artifact_pip_packages: list[str] = list(
        getattr(artifact, "pip_packages", None) or []
    )

    try:
        agent = _create_adversarial_agent(pip_packages=artifact_pip_packages)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to create adversarial agent")
        emit_langfuse_event(
            None,
            "adversarial.agent_creation_error",
            "ERROR",
            {"error": str(exc), "capability_name": capability_name},
        )
        return _error_report(capability_name, f"Agent creation failed: {exc}")

    user_message = (
        f"Probe this generated tool for deceptive behavior.\n\n"
        f"## Tool name: {capability_name}\n\n"
        f"## Source code:\n```python\n{source_code}\n```"
    )

    # Inherit LangFuse tags from the pipeline config (must extract before
    # entering ThreadPoolExecutor — get_config() is thread-local).
    # Inherit LangFuse metadata. probe() runs inside a ThreadPoolExecutor
    # spawned by verification_node, so get_config() won't work (thread-local).
    # Instead, read from the module-level _langfuse_metadata set by
    # set_langfuse_metadata() before the thread pool starts.
    langfuse_metadata: dict = dict(_langfuse_metadata)

    config: dict = {
        "recursion_limit": ADVERSARIAL_RECURSION_LIMIT,
    }

    # Add LangFuse callback if available
    try:
        from langfuse.langchain import CallbackHandler as LangFuseCallbackHandler

        config["callbacks"] = [LangFuseCallbackHandler()]
    except Exception as exc:  # noqa: BLE001
        logger.debug("LangFuse handler creation failed (tracing disabled): %s", exc)

    if langfuse_metadata:
        config["metadata"] = langfuse_metadata

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(
            agent.invoke,
            {"messages": [{"role": "user", "content": user_message}]},
            config,
        )
        result = future.result(timeout=ADVERSARIAL_TIMEOUT_S)
        pool.shutdown(wait=False)

        structured: AdversarialFindings | None = result.get("structured_response")

        if structured is not None:
            report = _findings_to_report(structured, capability_name)
            emit_langfuse_event(
                None,
                "adversarial.completed",
                "DEFAULT",
                {
                    "capability_name": capability_name,
                    "findings_count": len(structured.findings),
                    "suspicious_count": sum(
                        1 for f in structured.findings if f.suspicious
                    ),
                    "overall_passed": report.overall_passed,
                },
            )
            return report

        # No structured response — agent ran but didn't produce output
        logger.warning(
            "Adversarial agent returned no structured_response for %s",
            capability_name,
        )
        emit_langfuse_event(
            None,
            "adversarial.no_structured_response",
            "WARNING",
            {"capability_name": capability_name},
        )
        return _error_report(
            capability_name, "Agent returned no structured response"
        )

    except concurrent.futures.TimeoutError:
        pool.shutdown(wait=False)
        logger.error(
            "Adversarial agent timed out after %ds for %s",
            ADVERSARIAL_TIMEOUT_S,
            capability_name,
        )
        emit_langfuse_event(
            None,
            "adversarial.timeout",
            "ERROR",
            {
                "timeout_s": ADVERSARIAL_TIMEOUT_S,
                "capability_name": capability_name,
            },
        )
        return _timeout_report(capability_name)

    except Exception as exc:  # noqa: BLE001
        pool.shutdown(wait=False)
        logger.exception("Adversarial agent error for %s", capability_name)
        emit_langfuse_event(
            None,
            "adversarial.error",
            "ERROR",
            {"error": str(exc), "capability_name": capability_name},
        )
        return _error_report(capability_name, str(exc))
