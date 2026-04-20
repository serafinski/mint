"""Tests for the ext7 #53 shared TaskState builder.

Security audit finding ext7 #53 (2026-04-20): longitudinal runners pre-fix
omitted ``output_contract`` from TaskState, diverging from the harness
pipeline semantics. The shared ``build_initial_state`` helper prevents
future drift — every pipeline entry point MUST use it.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from src.orchestrator.task_state_builder import build_initial_state  # noqa: E402
from src.schemas.task import OutputContract  # noqa: E402
from tests.evaluation.tasks import T01, T07  # noqa: E402


def test_build_initial_state_threads_output_contract():
    """T07 carries a required_files contract (produces output.csv). The builder
    MUST pass it through to TaskState — pre-ext7-#53 longitudinal pipeline
    silently dropped this field and decomposition's writer-append guard
    never fired.
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
    """ext7 #53 regression guard: grep the two longitudinal runners + the
    harness for ``TaskState(`` direct construction. There MUST be no bare
    ``TaskState(task_description=...)`` without using ``build_initial_state``.

    Pre-ext7-#53, both longitudinal runners constructed TaskState directly and
    omitted ``output_contract``. The shared builder prevents future drift.
    """
    import re

    runners = [
        ROOT / "scripts" / "run_longitudinal.py",
        ROOT / "scripts" / "run_longitudinal_shift.py",
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
        "ext7 #53 regression: the following runner/harness sites construct "
        "TaskState directly. Use "
        "``from src.orchestrator.task_state_builder import build_initial_state`` "
        "instead:\n" + "\n".join(f"  {o}" for o in offenders)
    )
