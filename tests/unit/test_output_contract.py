"""Unit tests for the OutputContract schema and the resolver/runner wiring (Step 14.6).

Covers:
- ``OutputContract`` schema validation, ``is_empty``, and ``check`` method.
- ``ContractCheckResult`` shape.
- ``resolve_output_contract`` three-tier behavior (authored shortcut, LLM
  fallback success, LLM fallback failure → None).
- ``_match_known_task`` (CLI-side helper) — task ID, prompt copy-paste,
  free-form prompt non-match.
- All 20 T-tasks have a non-empty contract whose ``required_keys`` are
  referenced in the corresponding validator closure source (consistency
  check that catches drift between the authored contract and the bespoke
  validator).
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest

from src.schemas.task import ContractCheckResult, OutputContract


# ---------------------------------------------------------------------------
# OutputContract schema + check method
# ---------------------------------------------------------------------------


def test_output_contract_default_is_empty():
    c = OutputContract()
    assert c.required_keys == []
    assert c.required_files == []
    assert c.is_empty() is True


def test_output_contract_with_keys_only():
    c = OutputContract(required_keys=["a", "b"])
    assert c.required_keys == ["a", "b"]
    assert c.required_files == []
    assert c.is_empty() is False


def test_output_contract_with_files_only():
    c = OutputContract(required_files=["output.csv"])
    assert c.required_keys == []
    assert c.required_files == ["output.csv"]
    assert c.is_empty() is False


def test_output_contract_with_both():
    c = OutputContract(required_keys=["row_count"], required_files=["output.xlsx"])
    assert c.is_empty() is False


def test_output_contract_extra_forbid():
    with pytest.raises(Exception):
        OutputContract(required_keys=["a"], unexpected_field=42)


def test_check_satisfied_keys_only():
    c = OutputContract(required_keys=["a", "b"])
    result = c.check({"a": 1, "b": 2, "c": 3})
    assert isinstance(result, ContractCheckResult)
    assert result.satisfied is True
    assert result.missing_keys == []
    assert result.missing_files == []
    assert result.produced_keys == ["a", "b", "c"]


def test_check_missing_keys():
    c = OutputContract(required_keys=["a", "b", "c"])
    result = c.check({"a": 1})
    assert result.satisfied is False
    assert result.missing_keys == ["b", "c"]
    assert result.missing_files == []


def test_check_missing_files():
    c = OutputContract(required_keys=["a"], required_files=["output.csv", "output.xlsx"])
    result = c.check({"a": 1}, {"output.csv": b"x"})
    assert result.satisfied is False
    assert result.missing_keys == []
    assert result.missing_files == ["output.xlsx"]


def test_check_missing_both():
    c = OutputContract(required_keys=["a", "b"], required_files=["report.md"])
    result = c.check({"a": 1}, {})
    assert result.satisfied is False
    assert result.missing_keys == ["b"]
    assert result.missing_files == ["report.md"]


def test_check_against_non_dict_output():
    """Non-dict outputs (e.g. None, list, str) should produce missing for all keys."""
    c = OutputContract(required_keys=["a", "b"])
    for bad in (None, "string output", [1, 2, 3], 42):
        result = c.check(bad)
        assert result.satisfied is False
        assert set(result.missing_keys) == {"a", "b"}
        assert result.produced_keys == []


def test_check_empty_contract_always_satisfied():
    c = OutputContract()
    result = c.check({"anything": 1}, {"file.txt": b"data"})
    assert result.satisfied is True
    assert result.missing_keys == []
    assert result.missing_files == []


def test_check_with_none_output_files_is_safe():
    c = OutputContract(required_keys=["a"], required_files=["x.csv"])
    result = c.check({"a": 1}, None)
    # required_files=["x.csv"] vs. empty produced_files → x.csv missing
    assert result.satisfied is False
    assert result.missing_files == ["x.csv"]


def test_check_t16_silent_failure_pattern():
    """T16's actual failure: dict was {next_action: ...} when contract wanted three keys."""
    c = OutputContract(
        required_keys=["root_cause_metric", "cascade_chain", "n_metrics_investigated"],
    )
    result = c.check({"next_action": "Please fix the CSV..."})
    assert result.satisfied is False
    assert sorted(result.missing_keys) == [
        "cascade_chain",
        "n_metrics_investigated",
        "root_cause_metric",
    ]
    assert result.produced_keys == ["next_action"]


# ---------------------------------------------------------------------------
# resolve_output_contract — three-tier behavior
# ---------------------------------------------------------------------------


def test_resolve_uses_authored_shortcut_when_task_definition_supplied():
    from src.orchestrator.output_contract_resolver import resolve_output_contract

    class FakeDef:
        output_contract = OutputContract(required_keys=["a", "b"])

    result = resolve_output_contract("ignored prompt", task_definition=FakeDef())
    assert result is not None
    assert result.required_keys == ["a", "b"]


def test_resolve_authored_shortcut_does_not_call_llm():
    """Tier 1 must be deterministic and zero-LLM."""
    from src.orchestrator import output_contract_resolver

    class FakeDef:
        output_contract = OutputContract(required_keys=["x"])

    with patch.object(
        output_contract_resolver,
        "_extract_output_contract_llm",
        side_effect=AssertionError("must not be called for tier 1"),
    ):
        result = output_contract_resolver.resolve_output_contract(
            "any prompt", task_definition=FakeDef()
        )
    assert result.required_keys == ["x"]


def test_resolve_falls_through_to_extractor_when_no_task_definition():
    from src.orchestrator import output_contract_resolver

    fake_contract = OutputContract(required_keys=["extracted"])
    with patch.object(
        output_contract_resolver,
        "_extract_output_contract_llm",
        return_value=fake_contract,
    ):
        result = output_contract_resolver.resolve_output_contract("free-form prompt")
    assert result is not None
    assert result.required_keys == ["extracted"]


def test_resolve_returns_none_when_extractor_raises():
    """LLM failure → None → terminal check falls through to legacy behavior."""
    from src.orchestrator import output_contract_resolver

    with patch.object(
        output_contract_resolver,
        "_extract_output_contract_llm",
        side_effect=RuntimeError("network down"),
    ):
        result = output_contract_resolver.resolve_output_contract("free-form prompt")
    assert result is None


def test_resolve_skips_invalid_task_definition():
    """If task_definition has no output_contract attr, fall through to extractor."""
    from src.orchestrator import output_contract_resolver

    class WeirdObject:
        pass

    fake_contract = OutputContract(required_keys=["fallback"])
    with patch.object(
        output_contract_resolver,
        "_extract_output_contract_llm",
        return_value=fake_contract,
    ):
        result = output_contract_resolver.resolve_output_contract(
            "prompt", task_definition=WeirdObject()
        )
    assert result.required_keys == ["fallback"]


# ---------------------------------------------------------------------------
# CLI _match_known_task helper
# ---------------------------------------------------------------------------


def test_match_known_task_by_id():
    from src.cli.runner import _match_known_task

    match = _match_known_task("T01", "T01")
    assert match is not None
    assert match.task_id == "T01"


def test_match_known_task_id_case_insensitive():
    from src.cli.runner import _match_known_task

    match = _match_known_task("t16", "t16")
    assert match is not None
    assert match.task_id == "T16"


def test_match_known_task_id_with_whitespace():
    from src.cli.runner import _match_known_task

    match = _match_known_task("  T07  ", "  T07  ")
    assert match is not None
    assert match.task_id == "T07"


def test_match_known_task_by_prompt_copy_paste():
    """Copy-pasting a T-task prompt into the CLI should match that T-task."""
    from src.cli.runner import _match_known_task
    from tests.evaluation.tasks import T01

    match = _match_known_task(T01.task_prompt, T01.task_prompt)
    assert match is not None
    assert match.task_id == "T01"


def test_match_known_task_no_match_for_freeform():
    from src.cli.runner import _match_known_task

    assert _match_known_task("do something completely different", "do something") is None


def test_match_known_task_empty_input():
    from src.cli.runner import _match_known_task

    assert _match_known_task("", "") is None
    assert _match_known_task("   ", "   ") is None


# ---------------------------------------------------------------------------
# All 20 T-tasks consistency check
# ---------------------------------------------------------------------------


def test_all_tasks_have_non_empty_output_contract():
    """Every TaskDefinition in ALL_TASKS must declare a non-empty contract."""
    from tests.evaluation.tasks import ALL_TASKS

    assert len(ALL_TASKS) == 20
    for task in ALL_TASKS:
        c = task.output_contract
        assert c is not None, f"{task.task_id} has no output_contract"
        assert not c.is_empty(), (
            f"{task.task_id} has an empty contract — every T-task must declare "
            f"required_keys and/or required_files"
        )


def test_all_tasks_contract_keys_referenced_in_prompt_or_validator():
    """Each authored required_key must be justified by the task_prompt or validator.

    The ``task_prompt`` is the user-facing source of truth for what the task
    is asking for; the ``validator`` is a value-correctness check that may
    legitimately verify only a subset of the requested keys (e.g. T07 / T11
    where ``output_file`` is a metadata pointer mentioned in the prompt but
    not value-checked by the dict validator — the file validator checks the
    bytes instead). A key is "justified" if it appears in either source.

    Catches drift in both directions: if the validator references a key the
    contract forgets, or if the contract inflates with keys the prompt
    never mentioned.
    """
    from tests.evaluation.tasks import ALL_TASKS

    for task in ALL_TASKS:
        sources = [task.task_prompt]
        try:
            sources.append(inspect.getsource(task.validator))
        except (OSError, TypeError):
            pass
        if task.file_validator is not None:
            try:
                sources.append(inspect.getsource(task.file_validator))
            except (OSError, TypeError):
                pass
        combined = "\n".join(sources)
        for key in task.output_contract.required_keys:
            assert key in combined, (
                f"{task.task_id} contract declares required_key '{key}' but "
                f"neither the task_prompt nor any validator references it. "
                f"Either the contract is inflated or the prompt is stale."
            )
        for fname in task.output_contract.required_files:
            assert fname in combined, (
                f"{task.task_id} contract declares required_file '{fname}' but "
                f"neither the task_prompt nor any validator references it."
            )


def test_file_producing_tasks_declare_required_files():
    """T07 and T11 are the file-producing testbed tasks; both must declare files."""
    from tests.evaluation.tasks import T07, T11

    assert T07.output_contract.required_files == ["output.csv"]
    assert T11.output_contract.required_files == ["output.xlsx"]


def test_t16_contract_matches_motivating_failure():
    """T16 is the canonical Step 14.6 motivator; pin its contract explicitly."""
    from tests.evaluation.tasks import T16

    assert set(T16.output_contract.required_keys) == {
        "root_cause_metric",
        "cascade_chain",
        "n_metrics_investigated",
    }
    assert T16.output_contract.required_files == []


# ---------------------------------------------------------------------------
# CLI runner _evaluate_output_contract — end-to-end exit-3 path
# ---------------------------------------------------------------------------
#
# These tests exercise the actual production helper that ``execute_task``
# calls at the terminal state, so a green test here is empirical evidence
# that the exit-3 wiring works end-to-end without standing up the full
# pipeline. Each test also captures the magenta warning text so we can
# verify the user-facing diagnostics aren't silently regressed.


class _CapturingConsole:
    """Minimal Rich-console stand-in that records printed messages."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def print(self, msg: str) -> None:
        self.messages.append(str(msg))


def _make_state_with_contract(contract: OutputContract):
    """Return an object exposing ``output_contract`` like a TaskState."""
    from src.schemas.task import TaskState

    return TaskState(task_description="test task", output_contract=contract)


_FAKE_ARTIFACT = object()  # any truthy sentinel — helper only checks `is None`


def test_evaluate_returns_3_on_missing_keys():
    """The canonical T16 silent-failure pattern."""
    from src.cli.runner import _evaluate_output_contract

    state = _make_state_with_contract(OutputContract(
        required_keys=["root_cause_metric", "cascade_chain", "n_metrics_investigated"]
    ))
    console = _CapturingConsole()

    result = _evaluate_output_contract(
        state,
        artifact=_FAKE_ARTIFACT,
        output={"next_action": "Please fix the CSV..."},
        chain_output_files={},
        err_console_ref=console,
    )

    assert result == 3
    joined = "\n".join(console.messages)
    assert "OUTPUT CONTRACT NOT SATISFIED" in joined
    assert "root_cause_metric" in joined
    assert "cascade_chain" in joined
    assert "n_metrics_investigated" in joined
    assert "next_action" in joined  # produced keys diagnostic


def test_evaluate_returns_3_on_missing_files():
    """T07/T11-style: dict is correct but the required file wasn't emitted."""
    from src.cli.runner import _evaluate_output_contract

    state = _make_state_with_contract(OutputContract(
        required_keys=["row_count"],
        required_files=["output.csv"],
    ))
    console = _CapturingConsole()

    result = _evaluate_output_contract(
        state,
        artifact=_FAKE_ARTIFACT,
        output={"row_count": 50},
        chain_output_files={},  # no output.csv
        err_console_ref=console,
    )

    assert result == 3
    joined = "\n".join(console.messages)
    assert "Missing files" in joined
    assert "output.csv" in joined


def test_evaluate_returns_3_on_missing_both_keys_and_files():
    from src.cli.runner import _evaluate_output_contract

    state = _make_state_with_contract(OutputContract(
        required_keys=["a", "b"],
        required_files=["report.md"],
    ))
    console = _CapturingConsole()

    result = _evaluate_output_contract(
        state,
        artifact=_FAKE_ARTIFACT,
        output={"a": 1},
        chain_output_files={},
        err_console_ref=console,
    )

    assert result == 3
    joined = "\n".join(console.messages)
    assert "Missing keys" in joined and "b" in joined
    assert "Missing files" in joined and "report.md" in joined


def test_evaluate_returns_none_when_contract_satisfied():
    """A correct output should fall through to the legacy success path."""
    from src.cli.runner import _evaluate_output_contract

    state = _make_state_with_contract(OutputContract(required_keys=["a", "b"]))
    console = _CapturingConsole()

    result = _evaluate_output_contract(
        state,
        artifact=_FAKE_ARTIFACT,
        output={"a": 1, "b": 2, "c": 3},
        chain_output_files={},
        err_console_ref=console,
    )

    assert result is None
    assert console.messages == []  # no warnings on success


def test_evaluate_returns_none_when_contract_satisfied_with_files():
    from src.cli.runner import _evaluate_output_contract

    state = _make_state_with_contract(OutputContract(
        required_keys=["row_count"],
        required_files=["output.csv"],
    ))
    console = _CapturingConsole()

    result = _evaluate_output_contract(
        state,
        artifact=_FAKE_ARTIFACT,
        output={"row_count": 50},
        chain_output_files={"output.csv": b"id,name\n1,foo\n"},
        err_console_ref=console,
    )

    assert result is None
    assert console.messages == []


def test_evaluate_returns_none_when_no_contract():
    """Free-form CLI prompts where the LLM extractor returned None."""
    from src.cli.runner import _evaluate_output_contract

    state = _make_state_with_contract(OutputContract())  # empty
    console = _CapturingConsole()

    result = _evaluate_output_contract(
        state,
        artifact=_FAKE_ARTIFACT,
        output={"random_key": "anything"},
        chain_output_files={},
        err_console_ref=console,
    )

    assert result is None
    assert console.messages == []


def test_evaluate_returns_none_when_state_has_no_contract_field():
    """Legacy state objects without an output_contract attribute fall through."""
    from src.cli.runner import _evaluate_output_contract

    class LegacyState:
        pass  # no output_contract attribute at all

    result = _evaluate_output_contract(
        LegacyState(),
        artifact=_FAKE_ARTIFACT,
        output={"a": 1},
        chain_output_files={},
        err_console_ref=_CapturingConsole(),
    )

    assert result is None


def test_evaluate_returns_none_when_state_is_dict():
    """LangGraph pipelines return dict states; the helper handles both shapes."""
    from src.cli.runner import _evaluate_output_contract

    contract = OutputContract(required_keys=["a", "b"])
    state_dict = {"output_contract": contract}
    console = _CapturingConsole()

    # Wrong-shape output → exit 3 even when state is a dict not a TaskState.
    result = _evaluate_output_contract(
        state_dict,
        artifact=_FAKE_ARTIFACT,
        output={"only_a": 1},
        chain_output_files={},
        err_console_ref=console,
    )

    assert result == 3


def test_evaluate_returns_none_on_hard_crash_output_is_none():
    """Pipeline crashed → output is None → exit 1, not exit 3."""
    from src.cli.runner import _evaluate_output_contract

    state = _make_state_with_contract(OutputContract(required_keys=["a", "b"]))
    console = _CapturingConsole()

    result = _evaluate_output_contract(
        state,
        artifact=_FAKE_ARTIFACT,
        output=None,
        chain_output_files={},
        err_console_ref=console,
    )

    assert result is None  # caller's legacy path returns 1
    assert console.messages == []  # don't double-warn on hard failures


def test_evaluate_returns_none_when_no_artifact():
    """Registry-match-without-execution: no artifact → fall through."""
    from src.cli.runner import _evaluate_output_contract

    state = _make_state_with_contract(OutputContract(required_keys=["a"]))
    console = _CapturingConsole()

    result = _evaluate_output_contract(
        state,
        artifact=None,
        output={"a": 1},
        chain_output_files={},
        err_console_ref=console,
    )

    assert result is None
    assert console.messages == []


def test_evaluate_handles_non_dict_output_safely():
    """If somehow the output is a string or list, still produce a clean exit 3."""
    from src.cli.runner import _evaluate_output_contract

    state = _make_state_with_contract(OutputContract(required_keys=["a", "b"]))
    console = _CapturingConsole()

    result = _evaluate_output_contract(
        state,
        artifact=_FAKE_ARTIFACT,
        output="not a dict at all",  # type: ignore[arg-type]
        chain_output_files={},
        err_console_ref=console,
    )

    assert result == 3
    joined = "\n".join(console.messages)
    # Both required keys reported missing
    assert "a" in joined
    assert "b" in joined


def test_evaluate_diagnostics_list_produced_files_too():
    """The magenta diagnostics should surface produced files when relevant."""
    from src.cli.runner import _evaluate_output_contract

    state = _make_state_with_contract(OutputContract(
        required_keys=["a"],
        required_files=["output.xlsx"],
    ))
    console = _CapturingConsole()

    result = _evaluate_output_contract(
        state,
        artifact=_FAKE_ARTIFACT,
        output={"a": 1},
        # User got a CSV when they asked for an XLSX — both files produced
        # and missing should be visible in the diagnostics.
        chain_output_files={"output.csv": b"x"},
        err_console_ref=console,
    )

    assert result == 3
    joined = "\n".join(console.messages)
    assert "Missing files" in joined and "output.xlsx" in joined
    assert "Produced files" in joined and "output.csv" in joined
