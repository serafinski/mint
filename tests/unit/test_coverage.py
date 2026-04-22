"""Unit tests for the verify_coverage helper (Step 7 of the B1 Pillar 3 block).

Tests the five branches documented in docs/MASTER_PLAN_2026_04_13.md §12
pre-registered finding #5:

1. **Verified happy path** — every failure case is handled by at least one
   variant → all_covered=True, status="verified".
2. **Uncovered case** — at least one failure case has no handling variant →
   all_covered=False, status="verified", uncovered_cases populated.
3. **Vacuous empty failures** — no failure cases provided → all_covered=True,
   status="vacuous".
4. **Unverifiable type** — variants are SKILL or AGENT → all_covered=True,
   status="unverifiable_type" (the Pillar 1 × Pillar 3 tension response).
5. **Replay infrastructure error** — sandbox raises or variant source is
   unparseable → execution_errors populated.

Plus the two "handled" semantics subcases from finding #5 sub-finding 5b:

- Error-case (parent has error_message) → variant handles iff replay runs
  without raising.
- Wrong-output-case (parent has actual_output, no error_message) → variant
  handles iff replay runs without raising AND produces output ≠ parent's
  recorded actual_output.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.schemas.capability import CapabilityType
from src.schemas.metrics import InvocationRecord
from src.schemas.task import GeneratedArtifact
from src.verification.coverage import (
    CoverageReport,
    _variant_handles_case,
    verify_coverage,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mcp_variant(name: str, source: str = "") -> GeneratedArtifact:
    """MCP_TOOL variant with a minimal valid FastMCP source by default."""
    if not source:
        source = (
            '"""Variant tool."""\n'
            "from typing import Annotated\n"
            "from mcp.server.fastmcp import FastMCP\n"
            "from pydantic import Field\n"
            f'mcp = FastMCP("{name}")\n'
            "@mcp.tool()\n"
            "async def run(x: Annotated[int, Field(description='x')]) -> dict:\n"
            '    """doc."""\n'
            "    return {'result': x * 2}\n"
        )
    return GeneratedArtifact(
        capability_name=name,
        capability_type=CapabilityType.MCP_TOOL,
        source_code=source,
        description=f"Variant {name}",
    )


def _skill_variant(name: str) -> GeneratedArtifact:
    return GeneratedArtifact(
        capability_name=name,
        capability_type=CapabilityType.SKILL,
        skill_content=f"---\nname: {name}\ndescription: test\n---\n\nDo things.\n",
        description=f"SKILL variant {name}",
    )


def _agent_variant(name: str) -> GeneratedArtifact:
    return GeneratedArtifact(
        capability_name=name,
        capability_type=CapabilityType.AGENT,
        agent_config={"system_prompt": f"You are {name}."},
        description=f"AGENT variant {name}",
    )


def _error_case(task_id: str = "task-err", error: str = "IndexError: out of range") -> InvocationRecord:
    """Failure case where the parent errored."""
    return InvocationRecord(
        capability_id="parent-cap",
        task_id=task_id,
        success=False,
        token_cost=100,
        latency_ms=50.0,
        error_message=error,
        input_args='{"x": 5}',
        actual_output=None,
    )


def _wrong_output_case(
    task_id: str = "task-wrong",
    input_args: str = '{"x": 5}',
    actual_output: str = '{"result": "wrong-answer"}',
) -> InvocationRecord:
    """Failure case where the parent didn't error but produced wrong output."""
    return InvocationRecord(
        capability_id="parent-cap",
        task_id=task_id,
        success=False,
        token_cost=100,
        latency_ms=50.0,
        error_message=None,
        input_args=input_args,
        actual_output=actual_output,
    )


# ---------------------------------------------------------------------------
# CoverageReport schema
# ---------------------------------------------------------------------------


class TestCoverageReportSchema:
    def test_minimal_verified_report(self):
        report = CoverageReport(all_covered=True, status="verified")
        assert report.all_covered is True
        assert report.status == "verified"
        assert report.coverage_map == {}
        assert report.uncovered_cases == []
        assert report.execution_errors == []

    def test_forbids_extra_fields(self):
        with pytest.raises(Exception):
            CoverageReport(all_covered=True, status="verified", extra_field="nope")

    def test_status_enum_rejects_invalid(self):
        with pytest.raises(Exception):
            CoverageReport(all_covered=True, status="not_a_real_status")


# ---------------------------------------------------------------------------
# Edge cases — empty variants, empty failures
# ---------------------------------------------------------------------------


class TestVacuousCoverage:
    def test_empty_variants_is_vacuously_covered(self):
        report = verify_coverage([], [_error_case()])
        assert report.all_covered is True
        assert report.status == "vacuous"

    def test_empty_failures_is_vacuously_covered(self):
        report = verify_coverage([_mcp_variant("var-a")], [])
        assert report.all_covered is True
        assert report.status == "vacuous"

    def test_empty_variants_and_failures(self):
        report = verify_coverage([], [])
        assert report.all_covered is True
        assert report.status == "vacuous"


# ---------------------------------------------------------------------------
# Type dispatch — SKILL/AGENT return unverifiable_type
# ---------------------------------------------------------------------------


class TestTypeDispatch:
    def test_skill_variants_return_unverifiable_type(self):
        report = verify_coverage(
            [_skill_variant("skill-a"), _skill_variant("skill-b")],
            [_error_case()],
        )
        assert report.all_covered is True
        assert report.status == "unverifiable_type"
        assert report.coverage_map == {}

    def test_agent_variants_return_unverifiable_type(self):
        report = verify_coverage(
            [_agent_variant("agent-a"), _agent_variant("agent-b")],
            [_error_case()],
        )
        assert report.all_covered is True
        assert report.status == "unverifiable_type"

    def test_skill_variants_with_empty_failures_are_still_vacuous(self):
        """Empty failures short-circuits before type dispatch; the report
        should say ``vacuous`` not ``unverifiable_type`` because the
        coverage mechanism isn't actually dispatching at all."""
        report = verify_coverage([_skill_variant("skill-a")], [])
        assert report.all_covered is True
        assert report.status == "vacuous"

    def test_mixed_types_dispatches_on_first(self, caplog):
        """Mixed-type variants are a caller bug but the verifier should
        still return a report, dispatching on the first variant's type
        and logging a warning.

        Tightened (2026-04-19): the warning log was previously assumed but
        never asserted, so a silent refactor that dropped the log would
        still pass. ``caplog`` now fixes both the status and the surfacing
        of the mixed-types signal to operators.
        """
        import logging

        with caplog.at_level(logging.WARNING, logger="src.verification.coverage"):
            report = verify_coverage(
                [_skill_variant("skill-a"), _mcp_variant("tool-b")],
                [_error_case()],
            )
        # Dispatch on SKILL (first variant) → unverifiable_type
        assert report.status == "unverifiable_type"
        # Warning log surfaced for the mixed-types caller bug.
        warning_messages = [
            r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
        ]
        assert any(
            "mixed capability types" in msg for msg in warning_messages
        ), f"Expected mixed-types warning in {warning_messages}"


# ---------------------------------------------------------------------------
# _variant_handles_case — error-case semantics
# ---------------------------------------------------------------------------


class TestVariantHandlesCase_ErrorCase:
    """Parent errored → variant handles iff replay runs without raising."""

    @patch("src.verification.coverage.sandbox_execute")
    def test_variant_runs_clean_on_error_case_handles(self, mock_sandbox):
        """Replay completes with no error → failure mode fixed."""
        mock_sandbox.return_value = {
            "success": True,
            "stdout": '{"result": "\\"ok\\"", "error": null}',
            "stderr": "",
            "exit_code": 0,
        }
        variant = _mcp_variant("fixed-variant")
        failure = _error_case()
        handled, err = _variant_handles_case(variant, failure)
        assert handled is True
        assert err is None

    @patch("src.verification.coverage.sandbox_execute")
    def test_variant_also_errors_on_error_case_does_not_handle(self, mock_sandbox):
        """Replay itself raises → failure mode NOT fixed (variant is just as broken)."""
        mock_sandbox.return_value = {
            "success": True,
            "stdout": '{"result": null, "error": "ValueError: still broken"}',
            "stderr": "",
            "exit_code": 0,
        }
        variant = _mcp_variant("still-broken")
        failure = _error_case()
        handled, err = _variant_handles_case(variant, failure)
        assert handled is False
        assert err is None  # expected case, not an infrastructure error

    @patch("src.verification.coverage.sandbox_execute")
    def test_variant_with_different_error_type_still_does_not_handle(self, mock_sandbox):
        """Any replay error, regardless of type, counts as 'did not fix'."""
        mock_sandbox.return_value = {
            "success": True,
            "stdout": '{"result": null, "error": "TypeError: wrong type"}',
            "stderr": "",
            "exit_code": 0,
        }
        variant = _mcp_variant("regressed")
        failure = _error_case(error="IndexError: out of range")
        handled, _ = _variant_handles_case(variant, failure)
        assert handled is False


# ---------------------------------------------------------------------------
# _variant_handles_case — wrong-output-case semantics
# ---------------------------------------------------------------------------


class TestVariantHandlesCase_WrongOutputCase:
    """Parent didn't error → variant handles iff runs clean AND output differs."""

    @patch("src.verification.coverage.sandbox_execute")
    def test_variant_produces_different_output_handles(self, mock_sandbox):
        """Variant runs without error, produces different output from parent."""
        mock_sandbox.return_value = {
            "success": True,
            "stdout": '{"result": "\\"different-result\\"", "error": null}',
            "stderr": "",
            "exit_code": 0,
        }
        variant = _mcp_variant("differentiated")
        failure = _wrong_output_case(actual_output='"\\"wrong-answer\\""')
        handled, err = _variant_handles_case(variant, failure)
        assert handled is True
        assert err is None

    @patch("src.verification.coverage.sandbox_execute")
    def test_variant_produces_same_output_does_not_handle(self, mock_sandbox):
        """Variant runs clean but produces SAME output as parent → didn't fix anything."""
        mock_sandbox.return_value = {
            "success": True,
            "stdout": '{"result": "\\"same-answer\\"", "error": null}',
            "stderr": "",
            "exit_code": 0,
        }
        variant = _mcp_variant("same-output")
        failure = _wrong_output_case(actual_output='"same-answer"')
        handled, err = _variant_handles_case(variant, failure)
        assert handled is False
        assert err is None

    @patch("src.verification.coverage.sandbox_execute")
    def test_variant_errors_on_wrong_output_case_regression(self, mock_sandbox):
        """Variant errors on input the parent didn't error on → regression, not handled."""
        mock_sandbox.return_value = {
            "success": True,
            "stdout": '{"result": null, "error": "ValueError: crashed"}',
            "stderr": "",
            "exit_code": 0,
        }
        variant = _mcp_variant("regressed")
        failure = _wrong_output_case()
        handled, _ = _variant_handles_case(variant, failure)
        assert handled is False

    @patch("src.verification.coverage.sandbox_execute")
    def test_parent_has_no_recorded_output_cannot_verify(self, mock_sandbox):
        """Wrong-output case with no recorded actual_output → conservative not-handled."""
        mock_sandbox.return_value = {
            "success": True,
            "stdout": '{"result": "\\"something\\"", "error": null}',
            "stderr": "",
            "exit_code": 0,
        }
        variant = _mcp_variant("clean-variant")
        # error_message is None, success is False, but actual_output is also None.
        failure = InvocationRecord(
            capability_id="parent",
            task_id="task-nodata",
            success=False,
            token_cost=100,
            latency_ms=50.0,
            error_message=None,
            input_args='{"x": 5}',
            actual_output=None,
        )
        handled, _ = _variant_handles_case(variant, failure)
        assert handled is False


# ---------------------------------------------------------------------------
# _variant_handles_case — infrastructure error paths
# ---------------------------------------------------------------------------


class TestVariantHandlesCase_InfrastructureErrors:
    def test_variant_missing_source_code_returns_error(self):
        variant = GeneratedArtifact(
            capability_name="no-source",
            capability_type=CapabilityType.MCP_TOOL,
            source_code=None,
        )
        handled, err = _variant_handles_case(variant, _error_case())
        assert handled is False
        assert err is not None
        assert "no source_code" in err

    def test_variant_without_public_async_returns_error(self):
        variant = _mcp_variant(
            "bad-variant",
            source=(
                '"""No public async def."""\n'
                "from mcp.server.fastmcp import FastMCP\n"
                'mcp = FastMCP("bad")\n'
                "async def _private_helper(x):\n"
                "    return x\n"
            ),
        )
        handled, err = _variant_handles_case(variant, _error_case())
        assert handled is False
        assert err is not None
        assert "no public async function" in err

    def test_case_missing_input_args_returns_error(self):
        variant = _mcp_variant("ok-variant")
        failure = InvocationRecord(
            capability_id="parent",
            task_id="task-no-args",
            success=False,
            token_cost=100,
            latency_ms=50.0,
            error_message="something",
            input_args=None,
            actual_output=None,
        )
        handled, err = _variant_handles_case(variant, failure)
        assert handled is False
        assert err is not None
        assert "no input_args" in err

    @patch("src.verification.coverage.sandbox_execute")
    def test_sandbox_raises_unexpectedly_returns_error(self, mock_sandbox):
        mock_sandbox.side_effect = RuntimeError("sandbox exploded")
        variant = _mcp_variant("variant")
        handled, err = _variant_handles_case(variant, _error_case())
        assert handled is False
        assert err is not None
        assert "sandbox_execute raised" in err

    @patch("src.verification.coverage.sandbox_execute")
    def test_sandbox_unparseable_output_returns_error(self, mock_sandbox):
        mock_sandbox.return_value = {
            "success": True,
            "stdout": "not valid json {",
            "stderr": "",
            "exit_code": 0,
        }
        variant = _mcp_variant("variant")
        handled, err = _variant_handles_case(variant, _error_case())
        assert handled is False
        assert err is not None
        assert "could not parse replay output" in err


# ---------------------------------------------------------------------------
# verify_coverage — MCP_TOOL end-to-end paths
# ---------------------------------------------------------------------------


class TestVerifyCoverageMcpTool:
    @patch("src.verification.coverage.sandbox_execute")
    def test_all_cases_handled_verified(self, mock_sandbox):
        """Happy path: every case handled by at least one variant."""
        mock_sandbox.return_value = {
            "success": True,
            "stdout": '{"result": "\\"ok\\"", "error": null}',
            "stderr": "",
            "exit_code": 0,
        }
        report = verify_coverage(
            [_mcp_variant("var-a"), _mcp_variant("var-b")],
            [_error_case(task_id="t1"), _error_case(task_id="t2")],
        )
        assert report.all_covered is True
        assert report.status == "verified"
        assert set(report.coverage_map.keys()) == {"t1", "t2"}
        assert report.uncovered_cases == []

    @patch("src.verification.coverage.sandbox_execute")
    def test_one_case_uncovered(self, mock_sandbox):
        """t1 is handled (replay clean), t2 is not (replay errors).

        verify_coverage iterates case × variant, so call order for
        [var-a, var-b] × [t1, t2] is:
        1. t1 × var-a, 2. t1 × var-b, 3. t2 × var-a, 4. t2 × var-b.
        """
        call_outputs = [
            # t1 × var-a — clean
            '{"result": "\\"ok\\"", "error": null}',
            # t1 × var-b — clean (already handled, won't change verdict)
            '{"result": "\\"ok\\"", "error": null}',
            # t2 × var-a — error
            '{"result": null, "error": "ValueError"}',
            # t2 × var-b — error (still not handled)
            '{"result": null, "error": "ValueError"}',
        ]
        call_idx = iter(call_outputs)
        mock_sandbox.side_effect = lambda *a, **kw: {
            "success": True,
            "stdout": next(call_idx),
            "stderr": "",
            "exit_code": 0,
        }

        report = verify_coverage(
            [_mcp_variant("var-a"), _mcp_variant("var-b")],
            [_error_case(task_id="t1"), _error_case(task_id="t2")],
        )
        assert report.all_covered is False
        assert report.status == "verified"
        assert "t1" in report.coverage_map
        assert "t2" not in report.coverage_map
        assert report.uncovered_cases == ["t2"]

    @patch("src.verification.coverage.sandbox_execute")
    def test_partial_coverage_one_variant_handles(self, mock_sandbox):
        """Only var-a handles t1, only var-b handles t2 → both covered.

        Call order: t1 × var-a, t1 × var-b, t2 × var-a, t2 × var-b.
        """
        call_outputs = [
            # t1 × var-a — handles
            '{"result": "\\"ok\\"", "error": null}',
            # t1 × var-b — doesn't handle (errors)
            '{"result": null, "error": "ValueError"}',
            # t2 × var-a — doesn't handle (errors)
            '{"result": null, "error": "ValueError"}',
            # t2 × var-b — handles
            '{"result": "\\"ok\\"", "error": null}',
        ]
        call_idx = iter(call_outputs)
        mock_sandbox.side_effect = lambda *a, **kw: {
            "success": True,
            "stdout": next(call_idx),
            "stderr": "",
            "exit_code": 0,
        }

        report = verify_coverage(
            [_mcp_variant("var-a"), _mcp_variant("var-b")],
            [_error_case(task_id="t1"), _error_case(task_id="t2")],
        )
        assert report.all_covered is True
        assert report.status == "verified"
        assert report.coverage_map["t1"] == ["var-a"]
        assert report.coverage_map["t2"] == ["var-b"]
        assert report.uncovered_cases == []

    @patch("src.verification.coverage.sandbox_execute")
    def test_all_infrastructure_errors_reports_error_status(self, mock_sandbox):
        """If every attempt hits an infrastructure error and nothing is covered,
        status should be 'error' so callers can distinguish it from a clean
        'variants didn't cover anything' verdict."""
        mock_sandbox.side_effect = RuntimeError("sandbox broken")

        report = verify_coverage(
            [_mcp_variant("var-a")],
            [_error_case(task_id="t1")],
        )
        assert report.all_covered is False
        assert report.status == "error"
        assert len(report.execution_errors) >= 1
        assert "sandbox_execute raised" in report.execution_errors[0]

    @patch("src.verification.coverage.sandbox_execute")
    def test_partial_coverage_with_some_execution_errors_still_verified(
        self, mock_sandbox
    ):
        """Some execution errors but coverage still achieved → status='verified',
        all_covered=True, execution_errors populated but non-fatal."""
        call_outputs: list = [
            # var-a × t1 — handles
            {"stdout_text": '{"result": "\\"ok\\"", "error": null}', "raise": False},
            # var-b × t1 — infrastructure error
            {"stdout_text": None, "raise": True},
        ]
        call_idx = iter(call_outputs)

        def _side_effect(*args, **kwargs):
            entry = next(call_idx)
            if entry["raise"]:
                raise RuntimeError("boom")
            return {
                "success": True,
                "stdout": entry["stdout_text"],
                "stderr": "",
                "exit_code": 0,
            }

        mock_sandbox.side_effect = _side_effect

        report = verify_coverage(
            [_mcp_variant("var-a"), _mcp_variant("var-b")],
            [_error_case(task_id="t1")],
        )
        assert report.all_covered is True
        assert report.status == "verified"
        assert "t1" in report.coverage_map
        assert len(report.execution_errors) == 1  # one variant errored
        assert "boom" in report.execution_errors[0]


# ---------------------------------------------------------------------------
# Never-raises contract
# ---------------------------------------------------------------------------


class TestNeverRaises:
    @patch("src.verification.coverage.sandbox_execute")
    def test_sandbox_crash_does_not_propagate(self, mock_sandbox):
        mock_sandbox.side_effect = RuntimeError("catastrophic")
        # Should not raise
        report = verify_coverage([_mcp_variant("var")], [_error_case()])
        assert isinstance(report, CoverageReport)
        assert report.all_covered is False

    def test_invalid_variant_source_does_not_propagate(self):
        variant = _mcp_variant("bad", source="def f(\n")  # SyntaxError
        report = verify_coverage([variant], [_error_case()])
        assert isinstance(report, CoverageReport)
        assert report.all_covered is False


# ---------------------------------------------------------------------------
# Security audit finding #16 — truncation sentinel detection (2026-04-20)
# ---------------------------------------------------------------------------


class TestTruncationSentinelHandling:
    """Coverage replay must detect the ``__truncated__`` sentinel
    emitted by ``serialize_for_invocation`` on oversize payloads and
    report the case as unreproducible — NOT pass to the sandbox and
    let the replay silently fail on a malformed `{"x": ...}` reconstruction.
    Pre-fix the function emitted invalid JSON; now it emits a valid
    sentinel and the coverage verifier has to recognise it.
    """

    def test_truncation_sentinel_reports_unreproducible(self):
        """Sentinel failure case → returns (False, <reason mentioning truncation>)
        without invoking the sandbox."""
        import json

        sentinel = json.dumps(
            {"__truncated__": True, "__original_size_bytes__": 128000},
            sort_keys=True,
        )
        failure = InvocationRecord(
            capability_id="parent-cap",
            task_id="truncated-case",
            success=False,
            token_cost=0,
            latency_ms=1.0,
            error_message="boom",
            input_args=sentinel,
            actual_output=None,
        )
        variant = _mcp_variant("any-variant")

        with patch("src.verification.coverage.sandbox_execute") as mock_sbx:
            handled, reason = _variant_handles_case(variant, failure)

        assert handled is False
        assert reason is not None
        assert "truncated" in reason.lower()
        assert "unreproducible" in reason.lower()
        # Sandbox must NOT have been invoked — no point replaying a truncated input.
        mock_sbx.assert_not_called()

    def test_legacy_invalid_json_reports_unreproducible(self):
        """Pre-fix rows carry the literal ``f'{head}..."__TRUNCATED__"'``
        string which does not parse. The fix must tolerate these as
        unreproducible rather than crashing the replay."""
        failure = InvocationRecord(
            capability_id="parent-cap",
            task_id="legacy-truncated",
            success=False,
            token_cost=0,
            latency_ms=1.0,
            error_message="boom",
            # Pre-fix broken format verbatim.
            input_args='{"payload": "xxx...yyy..."__TRUNCATED__"',
            actual_output=None,
        )
        variant = _mcp_variant("any-variant")

        with patch("src.verification.coverage.sandbox_execute") as mock_sbx:
            handled, reason = _variant_handles_case(variant, failure)

        assert handled is False
        assert reason is not None
        assert "invalid json" in reason.lower() or "unreproducible" in reason.lower()
        mock_sbx.assert_not_called()


# ---------------------------------------------------------------------------
# Security audit finding #15 — coverage sort_keys asymmetry (2026-04-20)
# ---------------------------------------------------------------------------


class TestWrongOutputCase_SemanticEquality:
    """Pre-fix, the variant's replay output was serialized without
    ``sort_keys=True`` while the parent's ``actual_output`` was stored
    with ``sort_keys=True``. Dicts with the same semantic content but
    different Python iteration order string-compared unequal, and
    ``_variant_handles_case`` returned ``(True, None)`` (falsely handled)
    — manufacturing positive split-coverage evidence. Post-fix: sort_keys
    applied symmetrically + defence-in-depth ``_semantic_equal`` parses
    both sides before comparing.
    """

    @patch("src.verification.coverage.sandbox_execute")
    def test_key_order_asymmetry_rejected(self, mock_sandbox):
        """Variant returns ``{"b": 2, "a": 1}`` — parent recorded
        ``{"a": 1, "b": 2}`` (sort_keys ordering). Same semantic content,
        different serialization. Must report handled=False."""
        import json

        # Parent stored with sort_keys=True (matches serialize_for_invocation).
        parent_output_json = json.dumps({"a": 1, "b": 2}, default=str, sort_keys=True)
        # Variant's sandbox would now emit with sort_keys=True too (the fix
        # at coverage.py:228), but we're testing _semantic_equal's
        # defence-in-depth: even if a future regression drops sort_keys,
        # the parse-then-compare catches it.
        variant_result_json = json.dumps({"b": 2, "a": 1}, default=str)  # different order
        # Outer envelope (result + error) — matches _build_replay_code output.
        mock_sandbox.return_value = {
            "success": True,
            "stdout": json.dumps({"result": variant_result_json, "error": None}),
            "stderr": "",
            "exit_code": 0,
        }

        failure = _wrong_output_case(
            task_id="key-order-case",
            input_args='{"x": 1}',
            actual_output=parent_output_json,
        )
        variant = _mcp_variant("same-semantic-variant")
        handled, _ = _variant_handles_case(variant, failure)

        assert handled is False, (
            "Semantically-identical outputs (same dict, different key order) "
            "must be reported as NOT handled. Pre-fix returned True = "
            "false-positive split coverage."
        )

    @patch("src.verification.coverage.sandbox_execute")
    def test_int_vs_float_treated_as_equal(self, mock_sandbox):
        """JSON ``1`` vs ``1.0`` — Python ``1 == 1.0`` is True. The
        _semantic_equal helper preserves this across JSON boundaries."""
        import json

        parent_output_json = json.dumps({"x": 1.0}, default=str, sort_keys=True)
        variant_result_json = json.dumps({"x": 1}, default=str, sort_keys=True)
        mock_sandbox.return_value = {
            "success": True,
            "stdout": json.dumps({"result": variant_result_json, "error": None}),
            "stderr": "",
            "exit_code": 0,
        }

        failure = _wrong_output_case(
            task_id="int-float-case",
            input_args='{"x": 1}',
            actual_output=parent_output_json,
        )
        variant = _mcp_variant("int-float-variant")
        handled, _ = _variant_handles_case(variant, failure)

        assert handled is False, (
            "1 vs 1.0 must compare equal — Python treats them as ==, "
            "so the variant did NOT differentiate the output."
        )

    @patch("src.verification.coverage.sandbox_execute")
    def test_nested_dict_key_order_also_contained(self, mock_sandbox):
        """Nested dicts with reordered keys — still semantically equal."""
        import json

        parent_output_json = json.dumps(
            {"outer": {"a": 1, "b": 2}}, default=str, sort_keys=True,
        )
        # Intentionally emit without sort_keys to exercise _semantic_equal.
        variant_result_json = json.dumps({"outer": {"b": 2, "a": 1}}, default=str)
        mock_sandbox.return_value = {
            "success": True,
            "stdout": json.dumps({"result": variant_result_json, "error": None}),
            "stderr": "",
            "exit_code": 0,
        }

        failure = _wrong_output_case(
            task_id="nested-case",
            input_args='{"x": 1}',
            actual_output=parent_output_json,
        )
        variant = _mcp_variant("nested-variant")
        handled, _ = _variant_handles_case(variant, failure)

        assert handled is False

    @patch("src.verification.coverage.sandbox_execute")
    def test_genuinely_different_output_still_handled(self, mock_sandbox):
        """Regression guard: the fix must NOT break the legitimate
        "variant differentiated the output" case. Different content →
        handled=True."""
        import json

        parent_output_json = json.dumps({"answer": "wrong"}, default=str, sort_keys=True)
        variant_result_json = json.dumps({"answer": "right"}, default=str, sort_keys=True)
        mock_sandbox.return_value = {
            "success": True,
            "stdout": json.dumps({"result": variant_result_json, "error": None}),
            "stderr": "",
            "exit_code": 0,
        }

        failure = _wrong_output_case(
            task_id="genuine-differentiation",
            input_args='{"x": 1}',
            actual_output=parent_output_json,
        )
        variant = _mcp_variant("correct-variant")
        handled, _ = _variant_handles_case(variant, failure)

        assert handled is True
