"""LLM extractor for task-level OutputContract prompts.

Authored evaluation tasks carry their ``OutputContract`` directly on the
``TaskDefinition`` and the canonical task-state builder threads that contract
into ``TaskState``. This module keeps only the free-form prompt extractor used
for token-accounting parity in the evaluation harness and for the standalone
extractor validation script.

The extractor does not know about the testbed catalogue and imports no
``tests/`` modules.
"""

from __future__ import annotations

from src.config import DEFAULT_LLM_MODEL
from src.schemas.task import OutputContract

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

    The evaluation harness calls this for token-accounting parity with a
    free-form prompt path, while still using authored contracts for measured
    T-task outcomes. ``scripts/validate_contract_extractor.py`` calls it to
    validate extractor agreement against the authored T-task contracts.

    Raises any exception the LLM client raises (network, validation, etc.);
    callers decide whether to fail, log, or ignore the extractor result.
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
