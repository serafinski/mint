"""Static-condition integrity tests.

The static baseline must stay comparable with JIT conditions: it records
invocation telemetry, applies the same output-contract checks, and returns the
shape-diagnostic fields expected by ``EvaluationRecord`` assembly.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


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
    """_run_static accepts metrics_store so static runs record invocations."""
    import inspect

    from tests.evaluation.harness import EvaluationHarness

    sig = inspect.signature(EvaluationHarness._run_static)
    assert "metrics_store" in sig.parameters, (
        "_run_static must accept a `metrics_store` parameter to record "
        "static-condition invocations."
    )


def test_run_static_records_invocation_when_metrics_store_provided():
    """A successful static run calls record_invocation on the metrics store."""
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
    """Build a dict-output static task with a required-file contract.

    This exercises the branch where the static implementation produces valid
    metadata but no output files, so the output contract must turn the run into
    a shape mismatch.
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
    """Missing required files fail static runs even when the validator passes.
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

    assert result["output_shape_mismatch"] is True, (
        "Static run without output.csv must record shape mismatch"
    )
    assert result["output_in_range"] is False, (
        "Shape mismatch must flip output_in_range=False."
    )
    assert result["failure_reason"] == "output_shape_mismatch"
    assert "output.csv" in result["output_shape_missing_files"]


def test_run_static_invocation_reflects_failure_when_shape_mismatched():
    """Recorded invocation success reflects the post-contract-check status.
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
    """Direct calls without metrics_store still run."""
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
    return shape fields so the EvaluationRecord schema stays consistent.
    """
    from tests.evaluation.tasks import T01, T10

    harness = _make_harness()
    registry = MagicMock()

    # No-static-coverage branch.
    result = harness._run_static(T10, registry, MagicMock())
    assert "output_shape_mismatch" in result
    assert result["output_shape_mismatch"] is False
    assert result["output_shape_missing_keys"] == []
    assert result["output_shape_missing_files"] == []

    # Capability-missing branch.
    registry.get.return_value = None
    result = harness._run_static(T01, registry, MagicMock())
    assert "output_shape_mismatch" in result
    assert result["output_shape_mismatch"] is False
    assert result["output_shape_missing_keys"] == []
    assert result["output_shape_missing_files"] == []

    # Static implementation exception branch.
    cap = MagicMock()
    cap.capability_type = "mcp_tool"
    registry.get.return_value = cap
    with patch(
        "tests.evaluation.harness._run_static_implementation",
        side_effect=RuntimeError("boom"),
    ):
        result = harness._run_static(T01, registry, MagicMock())
    assert "output_shape_mismatch" in result
    assert result["output_shape_mismatch"] is False
    assert result["output_shape_missing_keys"] == []
    assert result["output_shape_missing_files"] == []
