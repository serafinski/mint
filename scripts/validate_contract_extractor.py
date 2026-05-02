"""Validate the OutputContract LLM extractor against authored T-task contracts.

Step 14.6 — Fix B (extractor agreement validation).

The free-form task path uses ``_extract_output_contract_llm`` to convert a
natural-language task prompt into a typed ``OutputContract``. The eval harness
mostly uses authored contracts directly. To make sure the eval results remain
representative of the extractor behavior — and to apply the thesis's
verification philosophy to its own measurement instrument — this script runs
the LLM extractor on every T-task ``task_prompt`` and asserts agreement with
the authored ``output_contract``.

Usage::

    uv run python scripts/validate_contract_extractor.py
    uv run python scripts/validate_contract_extractor.py --tasks T01,T07,T16

Output: ``Extractor agreement: N/20`` plus a per-task disagreement listing.
The result gets recorded in the thesis appendix as evidence that the
contract-extraction layer is calibrated. If N < 20, the disagreement cases
are either fixed by tightening the extractor system prompt
(``src/orchestrator/output_contract_resolver.py::_EXTRACTOR_SYSTEM_PROMPT``)
or disclosed in Chapter 4 as known extractor/eval-contract differences.

Aligned with ``docs/thesis_proposal.md`` §Pillar 2 (verification of LLM
output before trust) and ``docs/MASTER_PLAN_2026_04_13.md`` §14.6 Part D.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _load_env() -> None:
    """Load OPENAI_API_KEY (and friends) from .env at the repo root.

    Standalone scripts have to do this themselves or every LLM call dies with
    ``OpenAIError: The api_key client option must be set...``.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    # Step 13.6 / Fix #1 (2026-04-19): route dev-script LangFuse emissions
    # to ``environment=test`` so they don't pollute Phase 4 eval. Must be
    # set BEFORE ``load_dotenv`` so ambient .env values can't override.
    import os
    os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", "test")
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")


_load_env()

from src.orchestrator.output_contract_resolver import _extract_output_contract_llm  # noqa: E402
from src.schemas.task import OutputContract  # noqa: E402
from tests.evaluation.tasks import ALL_TASKS, TaskDefinition  # noqa: E402


def _format_contract(c: OutputContract) -> str:
    parts: list[str] = []
    if c.required_keys:
        parts.append(f"keys={sorted(c.required_keys)}")
    if c.required_files:
        parts.append(f"files={sorted(c.required_files)}")
    return ", ".join(parts) if parts else "(empty)"


def _check_one(task: TaskDefinition) -> tuple[bool, OutputContract | None, str]:
    """Run the extractor on a single task. Returns (agreed, extracted, note)."""
    try:
        extracted = _extract_output_contract_llm(task.task_prompt)
    except Exception as exc:  # noqa: BLE001
        return False, None, f"extractor raised: {type(exc).__name__}: {exc}"

    authored = task.output_contract
    keys_match = set(extracted.required_keys) == set(authored.required_keys)
    files_match = set(extracted.required_files) == set(authored.required_files)
    agreed = keys_match and files_match
    note = ""
    if not keys_match:
        only_extracted = sorted(set(extracted.required_keys) - set(authored.required_keys))
        only_authored = sorted(set(authored.required_keys) - set(extracted.required_keys))
        if only_extracted:
            note += f" extra keys={only_extracted}"
        if only_authored:
            note += f" missing keys={only_authored}"
    if not files_match:
        only_extracted = sorted(set(extracted.required_files) - set(authored.required_files))
        only_authored = sorted(set(authored.required_files) - set(extracted.required_files))
        if only_extracted:
            note += f" extra files={only_extracted}"
        if only_authored:
            note += f" missing files={only_authored}"
    return agreed, extracted, note.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--tasks",
        type=str,
        default=None,
        help="Comma-separated task IDs to check (default: all 20).",
    )
    args = parser.parse_args(argv)

    if args.tasks:
        wanted = {t.strip().upper() for t in args.tasks.split(",")}
        tasks = [t for t in ALL_TASKS if t.task_id in wanted]
    else:
        tasks = list(ALL_TASKS)

    if not tasks:
        print("No tasks selected.", file=sys.stderr)
        return 1

    print(f"Validating contract extractor against {len(tasks)} tasks...\n")

    agreed_count = 0
    disagreements: list[tuple[str, OutputContract, OutputContract | None, str]] = []
    for task in tasks:
        print(f"  {task.task_id:>4} ... ", end="", flush=True)
        agreed, extracted, note = _check_one(task)
        if agreed:
            agreed_count += 1
            print("OK")
        else:
            print(f"DISAGREE — {note}")
            disagreements.append((task.task_id, task.output_contract, extracted, note))

    print(f"\nExtractor agreement: {agreed_count}/{len(tasks)} tasks")

    if disagreements:
        print("\nDisagreement details:")
        for tid, authored, extracted, note in disagreements:
            print(f"\n  {tid}:")
            print(f"    authored:  {_format_contract(authored)}")
            print(
                f"    extracted: "
                f"{_format_contract(extracted) if extracted else '(extractor failed)'}"
            )
            if note:
                print(f"    note:      {note}")
        return 1

    print("\nAll tasks agree. The extractor is calibrated against the eval.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
