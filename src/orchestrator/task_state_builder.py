"""Canonical TaskState construction for pipeline entry points.

The eval harness, longitudinal runner, and retained smoke scripts should
all enter the pipeline with the same ``TaskState`` shape. Most importantly,
``TaskDefinition.output_contract`` must reach decomposition so required
keys/files can influence planning and output validation consistently.

Adding a new required task-entry field should be a one-site change here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.schemas.task import OutputContract, TaskState

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

    Callers: ``EvaluationHarness._run_jit`` and
    ``scripts/run_longitudinal.py``. Any new runner MUST use this helper
    rather than constructing ``TaskState`` directly; a regression test
    (``tests/unit/test_task_state_builder.py``) asserts the contract field is
    always populated from ``task.output_contract``.
    """
    contract = getattr(task, "output_contract", None)
    return TaskState(
        task_description=task.task_prompt,
        output_contract=contract,
        allow_network=allow_network,
    )


def build_initial_state_from_prompt(
    task_prompt: str,
    *,
    output_contract: OutputContract | None = None,
    allow_network: bool = False,
) -> TaskState:
    """Construct an initial TaskState from a bare task prompt.

    Smoke scripts such as ``scripts/test_pipeline.py`` do not have a
    ``TaskDefinition`` but should still use the canonical entry-point shape:
    ``output_contract`` as an object, not ``None``. Callers that do not
    declare required keys/files can pass ``output_contract=None`` and this
    helper substitutes an empty ``OutputContract()``, matching
    ``build_initial_state``'s contract-always-an-object promise.
    """
    contract = output_contract if output_contract is not None else OutputContract()
    return TaskState(
        task_description=task_prompt,
        output_contract=contract,
        allow_network=allow_network,
    )
