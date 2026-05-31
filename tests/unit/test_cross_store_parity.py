"""Cross-store metric parity tests.

DuckDB (``evaluation_runs`` + ``analyze_results.py``) and LangFuse
(``m1.success`` / ``m5.latency_ms`` scores) both publish evaluation
headline metrics. These tests lock the current parity contract:
``EvaluationRecord.is_success`` feeds both stores, shape-mismatched rows
are failures everywhere, and the latency score uses the measured JIT
elapsed time.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from src.schemas.evaluation import EvaluationRecord  # noqa: E402


class TestIsSuccessPredicate:
    """EvaluationRecord.is_success is the single source of truth."""

    def test_happy_path_returns_true(self):
        assert EvaluationRecord.is_success(
            gate_passed=True, output_in_range=True, output_shape_mismatch=False,
        ) is True

    def test_shape_mismatch_rejects_even_with_happy_validator(self):
        """A shape mismatch fails M1 even when the value validator passed."""
        assert EvaluationRecord.is_success(
            gate_passed=True, output_in_range=True, output_shape_mismatch=True,
        ) is False

    def test_gate_failure_rejects(self):
        assert EvaluationRecord.is_success(
            gate_passed=False, output_in_range=True, output_shape_mismatch=False,
        ) is False

    def test_output_not_in_range_rejects(self):
        assert EvaluationRecord.is_success(
            gate_passed=True, output_in_range=False, output_shape_mismatch=False,
        ) is False

    def test_default_output_shape_mismatch_is_false(self):
        """Default shape_mismatch=False preserves older caller compatibility."""
        assert EvaluationRecord.is_success(
            gate_passed=True, output_in_range=True,
        ) is True


class TestEvaluationRecordSuccessProperty:
    """The ``success`` @property delegates to ``is_success``."""

    def _row(self, **overrides) -> EvaluationRecord:
        defaults = {
            "run_id": "test-run", "condition": "full", "task_id": "T04",
            "task_tier": "moderate", "attempt_count": 1,
            "capability_type": "mcp_tool", "verification_tier_reached": 3,
            "gate_passed": True, "output_in_range": True, "token_cost": 100,
            "attempt_latency_ms": 1000, "failure_reason": "none",
            "registry_snapshot_id": "snap-1", "invocation_record_id": "inv-1",
            "seed": 42, "is_pilot": False,
        }
        defaults.update(overrides)
        return EvaluationRecord(**defaults)

    def test_record_success_agrees_with_is_success_happy(self):
        rec = self._row()
        assert rec.success is True
        assert rec.success == EvaluationRecord.is_success(
            gate_passed=rec.gate_passed,
            output_in_range=rec.output_in_range,
            output_shape_mismatch=rec.output_shape_mismatch,
        )

    def test_record_success_false_on_shape_mismatch(self):
        rec = self._row(output_shape_mismatch=True)
        assert rec.success is False

    def test_record_success_false_on_gate_failure(self):
        rec = self._row(gate_passed=False, failure_reason="tier1")
        assert rec.success is False


class TestLangFuseScoreFormulaMatchesDuckDB:
    """The LangFuse M1 formula uses the same predicate as DuckDB.

    Shape-mismatched rows must score 0.0 in LangFuse, matching the
    DuckDB success predicate used by the analyzers.
    """

    def test_shape_mismatched_row_scores_zero_in_langfuse_formula(self):
        """A shape-mismatched result dict scores zero."""
        # Mirror the harness scoring formula.
        result = {
            "gate_passed": True,
            "output_in_range": True,
            "output_shape_mismatch": True,
        }
        success_val = 1.0 if EvaluationRecord.is_success(
            gate_passed=bool(result.get("gate_passed", False)),
            output_in_range=bool(result.get("output_in_range", False)),
            output_shape_mismatch=bool(result.get("output_shape_mismatch", False)),
        ) else 0.0
        assert success_val == 0.0, (
            "A shape-mismatched row must score 0.0 in LangFuse, matching "
            "the DuckDB predicate."
        )

    def test_genuine_success_scores_one(self):
        result = {
            "gate_passed": True,
            "output_in_range": True,
            "output_shape_mismatch": False,
        }
        success_val = 1.0 if EvaluationRecord.is_success(
            gate_passed=bool(result.get("gate_passed", False)),
            output_in_range=bool(result.get("output_in_range", False)),
            output_shape_mismatch=bool(result.get("output_shape_mismatch", False)),
        ) else 0.0
        assert success_val == 1.0


class TestHarnessShapeMismatchFlipsOutputInRange:
    """Shape-mismatch promotion sites also flip ``output_in_range=False``.

    This keeps every store aligned on the canonical success predicate
    after the harness promotes ``failure_reason`` to
    ``"output_shape_mismatch"``.
    """

    def test_compositional_extract_flips_output_in_range_on_shape_mismatch(self):
        """Every shape-mismatch promotion site flips output_in_range nearby."""
        import re

        harness_src = (
            ROOT / "tests" / "evaluation" / "harness.py"
        ).read_text()

        # Find every shape-mismatch promotion site + confirm an
        # output_in_range-like flip appears within ~3 lines. Accepts either
        # ``output_in_range = False`` (compositional + single-artifact paths)
        # or ``in_range = False`` (the _run_static local-var name).
        for m in re.finditer(
            r'failure_reason = "output_shape_mismatch"',
            harness_src,
        ):
            start = m.end()
            window = harness_src[start : start + 200]
            has_flip = (
                "output_in_range = False" in window
                or "in_range = False" in window
            )
            assert has_flip, (
                "Every `failure_reason = \"output_shape_mismatch\"` "
                "site in harness.py MUST be followed by "
                "`output_in_range = False` / `in_range = False` (within ~3 "
                "lines). Found one that isn't - LangFuse M1 score will "
                "disagree with DuckDB. Offending context:\n"
                f"{harness_src[max(0, m.start()-100):m.end()+200]}"
            )


class TestM5LatencyKeyFix:
    """The LangFuse M5 latency score reads the measured JIT latency.

    The harness should use ``jit_elapsed_ms`` rather than an unpopulated
    result-dict key.
    """

    def test_harness_no_longer_reads_chain_result_attempt_latency_ms(self):
        harness_src = (
            ROOT / "tests" / "evaluation" / "harness.py"
        ).read_text()
        assert "chain_result_attempt_latency_ms" not in harness_src, (
            "harness.py must not read `chain_result_attempt_latency_ms`; "
            "use `jit_elapsed_ms` (measured "
            "around pipeline.invoke) instead."
        )

    def test_jit_elapsed_ms_is_measured_and_used(self):
        harness_src = (
            ROOT / "tests" / "evaluation" / "harness.py"
        ).read_text()
        assert "jit_elapsed_ms = int(" in harness_src, (
            "harness.py must measure `jit_elapsed_ms` around "
            "pipeline.invoke. Measurement assignment missing."
        )
        assert "float(jit_elapsed_ms" in harness_src, (
            "harness.py LangFuse `m5.latency_ms` must read from "
            "`jit_elapsed_ms`."
        )
