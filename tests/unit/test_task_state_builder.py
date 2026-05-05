"""Tests for the shared TaskState entry-point builder.

The eval harness and longitudinal runner must construct initial
``TaskState`` objects through the same helper so task-level output
contracts and runner flags stay consistent across entry points.
"""

from __future__ import annotations

from pathlib import Path

from src.orchestrator.task_state_builder import build_initial_state
from src.schemas.task import OutputContract
from tests.evaluation.tasks import T01, T07

ROOT = Path(__file__).parent.parent.parent


def test_build_initial_state_threads_output_contract():
    """T07 carries a required_files contract (produces output.csv). The builder
    must pass it through to TaskState so decomposition can plan and validate
    required output files consistently.
    """
    state = build_initial_state(T07)
    assert state.output_contract is not None
    assert state.output_contract.required_files == T07.output_contract.required_files


def test_build_initial_state_preserves_empty_contract():
    """Empty contracts (no required_keys / required_files) must still be passed
    as objects, not None, so downstream guards can do a single
    ``if state.output_contract.required_files:`` check.
    """
    state = build_initial_state(T01)
    assert state.output_contract is not None
    assert isinstance(state.output_contract, OutputContract)


def test_build_initial_state_sets_allow_network():
    """allow_network flag threads through."""
    state = build_initial_state(T01, allow_network=True)
    assert state.allow_network is True

    state_default = build_initial_state(T01)
    assert state_default.allow_network is False


def test_longitudinal_runners_use_shared_builder():
    """Runner and harness entry points use the shared TaskState builder.

    There must be no bare ``TaskState(task_description=...)`` in the eval
    harness or longitudinal runner; both entry points should use
    ``build_initial_state`` so output-contract handling stays consistent.
    """
    import re

    runners = [
        ROOT / "scripts" / "run_longitudinal.py",
        ROOT / "tests" / "evaluation" / "harness.py",
    ]
    offenders: list[str] = []
    for path in runners:
        text = path.read_text()
        # Any direct TaskState(...) construction outside of tests/type-hints
        # is flagged. The shared builder is the ONE canonical path.
        for m in re.finditer(r"TaskState\(task_description=", text):
            # Compute the line number + a short context.
            before = text[: m.start()]
            lineno = before.count("\n") + 1
            line_start = before.rfind("\n") + 1
            line = text[line_start : text.find("\n", m.start())]
            offenders.append(f"{path.name}:{lineno} — {line.strip()}")

    assert not offenders, (
        "The following runner/harness sites construct TaskState directly. Use "
        "``from src.orchestrator.task_state_builder import build_initial_state`` "
        "instead:\n" + "\n".join(f"  {o}" for o in offenders)
    )
