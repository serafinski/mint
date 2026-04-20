"""Static-condition integrity (ext8 #65 + #68).

The ``static`` baseline in the Chapter-6 eval must produce data that's
comparable with the JIT conditions. Two pre-ext8 asymmetries violated this:

- **#65 (invocation telemetry):** ``_run_static`` never called
  ``metrics_store.record_invocation``. ``invocation_events`` had zero rows
  for the static condition → JIT-vs-static Gini had no baseline.
- **#68 (contract validation):** ``_run_static`` skipped
  ``task.output_contract.check``. A static capability that produced valid
  metadata but missed its declared ``required_files`` passed M1 while the
  same failure under JIT would correctly fail — a systematic thumb on the
  scale in Chapter 6's Pillar 1 comparison.

Both gaps are closed by the post-fix ``_run_static`` signature + body.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


def _make_harness():
    """Build a minimal EvaluationHarness bypassing its DB/ChromaDB init."""
    from tests.evaluation.harness import EvaluationHarness, HarnessConfig

    cfg = MagicMock(spec=HarnessConfig)
    cfg.db_path = ":memory:"
    cfg.base_seed = 42
    cfg.allow_network = False
    cfg.langfuse_handler = None
    harness = EvaluationHarness.__new__(EvaluationHarness)
    harness._config = cfg  # noqa: SLF001
    harness._store = MagicMock()  # noqa: SLF001
    harness._snap_id = None  # noqa: SLF001
    return harness


def test_run_static_accepts_metrics_store_kwarg():
    """ext8 #65: _run_static signature gained `metrics_store` parameter."""
    import inspect

    from tests.evaluation.harness import EvaluationHarness

    sig = inspect.signature(EvaluationHarness._run_static)
    assert "metrics_store" in sig.parameters, (
        "ext8 #65: _run_static must accept a `metrics_store` parameter to "
        "record static-condition invocations."
    )


def test_run_static_records_invocation_when_metrics_store_provided():
    """ext8 #65: a successful static run calls record_invocation on the store."""
    from src.schemas.metrics import InvocationRecord
    from tests.evaluation.tasks import T01

    harness = _make_harness()
    registry = MagicMock()
    cap = MagicMock()
    cap.capability_type = "mcp_tool"
    registry.get.return_value = cap

    metrics_store = MagicMock()

    # Mock the static implementation to return a valid T01 output.
    mock_output = {"row_count": 50, "column_names": ["id", "name", "age", "city"]}
    with patch(
        "tests.evaluation.harness._run_static_implementation",
        return_value=mock_output,
    ):
        result = harness._run_static(T01, registry, metrics_store)

    assert result["gate_passed"] is True
    assert result["output_in_range"] is True
    metrics_store.record_invocation.assert_called_once()
    arg = metrics_store.record_invocation.call_args.args[0]
    assert isinstance(arg, InvocationRecord)
    assert arg.capability_id == T01.static_capability_name
    assert arg.task_id == "T01"
    assert arg.success is True


def _synth_task_with_static_coverage_and_required_files():
    """Build a TaskDefinition that has BOTH has_static_coverage=True AND
    output_contract.required_files non-empty. No current testbed task has
    both — the combination is a Chapter-7 or future-task scenario, which is
    exactly why ext8 #68 is a defensive fix.
    """
    from dataclasses import replace

    from src.schemas.task import OutputContract
    from tests.evaluation.tasks import T01

    return replace(
        T01,
        output_contract=OutputContract(
            required_keys=["row_count", "column_names"],
            required_files=["output.csv"],  # synthetic — T01 doesn't really need this
        ),
    )


def test_run_static_applies_output_contract_check():
    """ext8 #68: a static run that passes the validator but misses
    required_files records output_shape_mismatch=True + output_in_range=False.
    """
    task = _synth_task_with_static_coverage_and_required_files()

    harness = _make_harness()
    registry = MagicMock()
    cap = MagicMock()
    cap.capability_type = "mcp_tool"
    registry.get.return_value = cap

    metrics_store = MagicMock()

    # Output passes T01's validator (row_count=50 + column_names match the
    # fixture) but since we synthesized a required_files=["output.csv"]
    # contract and _run_static's single-artifact path passes empty
    # output_files, the contract check fails.
    valid_dict_only_output = {
        "row_count": 50, "column_names": ["id", "name", "age", "city"],
    }
    with patch(
        "tests.evaluation.harness._run_static_implementation",
        return_value=valid_dict_only_output,
    ):
        result = harness._run_static(task, registry, metrics_store)

    # Pre-ext8 this row would have recorded {gate=True, range=True, reason='none'}.
    # Post-ext8: shape mismatch promoted into failure_reason + output_in_range flipped.
    assert result["output_shape_mismatch"] is True, (
        "ext8 #68: static run without output.csv must record shape mismatch"
    )
    assert result["output_in_range"] is False, (
        "ext8 #68: shape-mismatch must flip output_in_range=False (mirrors "
        "compositional branch's ext8 #64 fix)."
    )
    assert result["failure_reason"] == "output_shape_mismatch"
    assert "output.csv" in result["output_shape_missing_files"]


def test_run_static_invocation_reflects_failure_when_shape_mismatched():
    """ext8 #65 + #68 interaction: the recorded invocation's success flag
    must reflect the POST-shape-check status, not the pre-check validator.
    """
    task = _synth_task_with_static_coverage_and_required_files()

    harness = _make_harness()
    registry = MagicMock()
    cap = MagicMock()
    cap.capability_type = "mcp_tool"
    registry.get.return_value = cap
    metrics_store = MagicMock()

    with patch(
        "tests.evaluation.harness._run_static_implementation",
        return_value={"row_count": 50, "column_names": ["id", "name", "age", "city"]},
    ):
        harness._run_static(task, registry, metrics_store)

    arg = metrics_store.record_invocation.call_args.args[0]
    assert arg.success is False, (
        "Invocation.success must reflect shape-check failure, not validator-pass."
    )


def test_run_static_no_metrics_store_still_works():
    """Backward compatibility: calls without metrics_store (test-only) shouldn't crash."""
    from tests.evaluation.tasks import T01

    harness = _make_harness()
    registry = MagicMock()
    cap = MagicMock()
    cap.capability_type = "mcp_tool"
    registry.get.return_value = cap

    with patch(
        "tests.evaluation.harness._run_static_implementation",
        return_value={"row_count": 50, "column_names": ["id", "name", "age", "city"]},
    ):
        # Default metrics_store=None
        result = harness._run_static(T01, registry)

    assert result["gate_passed"] is True


def test_run_static_returns_shape_fields_on_failure_paths():
    """The no-coverage + capability-missing + exception branches must all
    return the new shape fields so the EvaluationRecord schema stays consistent.
    """
    from tests.evaluation.tasks import T10  # no static coverage

    harness = _make_harness()
    registry = MagicMock()
    registry.get.return_value = None  # for the "cap missing" variant

    # No-static-coverage branch.
    result = harness._run_static(T10, registry, MagicMock())
    assert "output_shape_mismatch" in result
    assert result["output_shape_mismatch"] is False
    assert result["output_shape_missing_keys"] == []
    assert result["output_shape_missing_files"] == []
