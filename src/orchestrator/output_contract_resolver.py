"""Boundary adapter: resolve a task's OutputContract at task entry (Step 14.6).

Single shared entry point for authored evaluation tasks and free-form task
prompts. This keeps contract resolution in one structural boundary instead of
spreading task-shape logic across callers.

Two tiers, in order:

1. **Authored shortcut** — if the caller supplies a ``task_definition`` (which
   carries an authored ``output_contract``), return it directly. Zero LLM
   calls. Deterministic. Used by the harness for every T-task.
2. **LLM extractor fallback** — otherwise, one ``gpt-5-mini`` call with
   ``response_format=OutputContract`` (LangChain structured output). Used for
   genuinely free-form prompts where no authored contract exists. On any
   failure (LLM exception, validation error), returns ``None`` so the
   terminal-state contract check degrades to the legacy status-only behavior —
   fail open, never blocks legitimate runs.

The resolver does NOT know about the testbed catalogue. Callers that have a
matching ``TaskDefinition`` pass it as ``task_definition=...``. This keeps the
orchestrator layer free of ``tests/`` imports.

See ``docs/MASTER_PLAN_2026_04_13.md`` §14.6 for the full design rationale,
and ``docs/thesis_proposal.md`` §Structured State Communication for the
alignment principle (typed schemas, not prose, between system stages).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.config import DEFAULT_LLM_MODEL
from src.schemas.task import OutputContract

if TYPE_CHECKING:
    pass


_EXTRACTOR_SYSTEM_PROMPT = """\
You are extracting the output contract from a natural-language task description.

The user describes a task they want a system to perform, often phrased as
"... return a dict with keys 'X', 'Y', 'Z'" or "... write the result to
output.xlsx" or similar. Your job is to read the task description and return
two lists:

- required_keys: the top-level dict keys the user is asking the system to
  return. List ONLY top-level keys mentioned in the user's request — do not
  invent keys, do not include keys from nested structures, do not include
  fields the user only mentions in passing as context. If the task does not
  explicitly ask for a dict with named keys, return an empty list.

- required_files: filenames (e.g. "output.csv", "output.xlsx", "report.md")
  that the user is asking the system to produce as files. Look for phrases
  like "write to output.X", "produce a CSV file", "save as report.md". List
  only the bare filenames, no directory prefixes. If no file output is
  requested, return an empty list.

Be conservative: if you are uncertain whether something is a required output,
leave it out. The system has a fail-open contract — false negatives degrade
gracefully, false positives cause spurious failures.

Examples:

Task: "Parse the CSV file at /data/sales.csv. Return a dict with keys
'row_count' (int) and 'column_names' (list of str)."
→ required_keys=["row_count", "column_names"], required_files=[]

Task: "Perform a fuzzy join... write the joined dataset to
/workspace/output.csv. Also return a dict with keys 'row_count' and
'null_count_on_key'."
→ required_keys=["row_count", "null_count_on_key"], required_files=["output.csv"]

Task: "Investigate the incident in t16_incident.yaml... Return a dict with
keys 'root_cause_metric' (str), 'cascade_chain' (list of str), and
'n_metrics_investigated' (int)."
→ required_keys=["root_cause_metric", "cascade_chain", "n_metrics_investigated"], required_files=[]

Task: "Summarize this document and tell me what you find."
→ required_keys=[], required_files=[]
"""


def _get_extractor_llm():
    """Build the structured-output LLM client for contract extraction.

    Lazy import so importing this module never triggers an LLM dependency
    at module load time (matches the pattern used by classifier / generators).
    """
    from langchain.chat_models import init_chat_model

    llm = init_chat_model(DEFAULT_LLM_MODEL, temperature=0, request_timeout=60)
    return llm.with_structured_output(OutputContract)


def _extract_output_contract_llm(task_prompt: str) -> OutputContract:
    """One-shot LLM extraction of an OutputContract from a free-form prompt.

    Used as the tier-2 fallback inside ``resolve_output_contract``, and
    independently by ``scripts/validate_contract_extractor.py`` to validate
    extractor agreement against the 20 authored T-task contracts before the
    Phase 5 evaluation run.

    Raises any exception the LLM client raises (network, validation, etc.) —
    callers in ``resolve_output_contract`` wrap this in ``try/except`` and
    return ``None`` on failure so the terminal-state check falls through to
    the legacy status-only path.
    """
    extractor = _get_extractor_llm()
    response = extractor.invoke(
        [
            ("system", _EXTRACTOR_SYSTEM_PROMPT),
            ("user", task_prompt),
        ]
    )
    if not isinstance(response, OutputContract):
        # with_structured_output should always return the typed model;
        # this branch protects against schema-binding edge cases observed
        # in classifier.py historically.
        return OutputContract(**dict(response)) if response else OutputContract()
    return response


def resolve_output_contract(
    task_prompt: str,
    *,
    task_definition: object | None = None,
) -> OutputContract | None:
    """Resolve a task's OutputContract at the system boundary (Step 14.6).

    Shared entry point for authored evaluation tasks and free-form prompts.
    See module docstring for the two-tier design rationale.

    Args:
        task_prompt: The natural-language task description as it would be
            passed to the pipeline. Used by the LLM extractor when no
            authored contract is available.
        task_definition: Optional object carrying an ``output_contract``
            attribute. Harness call sites pass the matching ``TaskDefinition``;
            free-form call sites pass ``None``.

    Returns:
        An ``OutputContract`` if either tier succeeds, or ``None`` if the
        free-form fallback fails (network error, malformed LLM output, etc.).
        The ``None`` return path lets the terminal-state check degrade to
        the legacy status-only success classification, preserving
        backwards compatibility with code paths that have no contract.
    """
    if task_definition is not None:
        contract = getattr(task_definition, "output_contract", None)
        if isinstance(contract, OutputContract):
            return contract

    try:
        return _extract_output_contract_llm(task_prompt)
    except Exception:
        return None
