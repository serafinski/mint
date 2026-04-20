"""Canonical TaskState construction for every pipeline entry point.

Security audit finding ext7 #53 (2026-04-20): the harness, longitudinal
runners, shift runner, and any future runner must all build the initial
TaskState the same way — most importantly, all must inject
``output_contract`` so decomposition's writer-subtask append validator
(``_ensure_file_writer_last_step`` at ``src/orchestrator/decomposition.py``)
fires identically across pipelines. Pre-fix, the longitudinal runners
omitted ``output_contract`` → the decomposer LLM was solely responsible for
emitting a file-writer subtask; when it forgot, the chain silently ran
without one and Pillar 3 RQ #1/#2/#3 measured a different pipeline than
the headline row-8 eval.

Adding a new required field to TaskState becomes a one-site change here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.schemas.task import TaskState

if TYPE_CHECKING:
    from tests.evaluation.tasks import TaskDefinition


def build_initial_state(
    task: TaskDefinition,
    *,
    allow_network: bool = False,
) -> TaskState:
    """Construct the pipeline-entry TaskState from a TaskDefinition.

    Threads the task's ``output_contract`` through unconditionally — empty
    contracts (``required_keys=[]`` + ``required_files=[]``) are still
    passed as objects, not ``None``, so the decomposition-layer guard at
    ``decomposition.py::_ensure_file_writer_last_step`` can do a single
    ``if state.output_contract.required_files:`` check without a prior
    null-guard.

    Callers: ``EvaluationHarness._run_jit``, ``scripts/run_longitudinal.py``,
    ``scripts/run_longitudinal_shift.py``. Any new runner MUST use this
    helper rather than constructing TaskState directly; a regression test
    (``tests/unit/test_task_state_builder.py``) asserts the contract field
    is always populated from ``task.output_contract``.
    """
    contract = getattr(task, "output_contract", None)
    return TaskState(
        task_description=task.task_prompt,
        output_contract=contract,
        allow_network=allow_network,
    )
