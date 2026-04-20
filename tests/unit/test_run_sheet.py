"""Run-sheet procedural lints.

ext7 #45 landed the scope-flags remediation in ``docs/run_sheet.md``. This
test codifies the resulting invariants so future edits that drop a scope flag
regress loudly instead of silently.

The lint walks the Phase 4 + Phase 4/5 tables and asserts:

- Every row invoking a DB-reading analyzer passes the appropriate scope flag
  (``--run-ids`` / ``--run-id`` / ``--session-ids``).
- The scoping-convention block (env-var pattern) is present.

No DB I/O; purely file-string inspection.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
RUN_SHEET = ROOT / "docs" / "run_sheet.md"


# Scripts that read the eval DuckDB and MUST be scoped. Value is the accepted
# set of scope-flag names (any one satisfies the lint).
_DB_READERS_TO_SCOPE: dict[str, set[str]] = {
    "analyze_compression_loss.py": {"--run-ids", "--since"},
    "audit_contamination.py": {"--run-ids", "--since"},
    "analyze_results.py": {"--run-ids", "--since"},
    "analyze_longitudinal.py": {"--run-id", "--run-ids"},
}

# ext7 #54: LangFuse-trace analyzers need session scoping. Either `--session-ids`
# or `--environment` (when the environment tag itself is a campaign marker)
# satisfies the lint.
_LANGFUSE_ANALYZERS_TO_SCOPE: dict[str, set[str]] = {
    "analyze_context_utilization.py": {"--session-ids", "--environment"},
    "analyze_tier2_probes.py": {"--session-ids"},
}


def _parse_command_rows(sheet_text: str) -> list[tuple[int, str]]:
    """Return [(row_num, full_command)] for every row of the Phase tables."""
    rows: list[tuple[int, str]] = []
    row_re = re.compile(r"^\|\s*(\d+)\s*\|\s*(`[^`]+`)\s*\|", re.MULTILINE)
    for m in row_re.finditer(sheet_text):
        row_num = int(m.group(1))
        cmd = m.group(2).strip("`").strip()
        rows.append((row_num, cmd))
    return rows


def test_scoping_convention_block_present():
    """ext7 #45 remediation landed the scoping env-var block. Removing it
    would silently revert the guidance to "pool cross-campaign".
    """
    text = RUN_SHEET.read_text()
    assert "CAMPAIGN_RUN_IDS" in text, (
        "run_sheet.md is missing the ext7 #45 scoping convention block. "
        "Restore the `export CAMPAIGN_RUN_IDS=...` pattern documented above "
        "the Phase 4/5 analysis table."
    )


def test_every_db_reader_row_passes_scope_flag():
    """For every run-sheet row whose command invokes a known DB-reading
    analyzer, at least one of the accepted scope flags MUST appear in the
    command.

    Pre-ext7-#45 rows 14, 16, 17 omitted `--run-ids` even though the scripts'
    own --help text warned "unsafe on multi-run DBs". Post-fix, forgetting
    the flag here fails the lint.
    """
    text = RUN_SHEET.read_text()
    rows = _parse_command_rows(text)
    offenders: list[str] = []
    for row_num, cmd in rows:
        # Extract the script filename (e.g. "analyze_results.py").
        m = re.search(r"scripts/(\w+\.py)", cmd)
        if not m:
            continue
        script = m.group(1)
        accepted = _DB_READERS_TO_SCOPE.get(script)
        if accepted is None:
            continue
        if not any(flag in cmd for flag in accepted):
            offenders.append(
                f"  Row {row_num} — `{script}` invoked without "
                f"any of {sorted(accepted)}:\n    {cmd}"
            )

    assert not offenders, (
        "docs/run_sheet.md rows invoke DB-reading analyzers without a scope "
        "flag. Add --run-ids / --run-id / --since per the Phase 4/5 scoping "
        "convention:\n\n" + "\n\n".join(offenders)
    )


def test_every_langfuse_analyzer_row_passes_scope_flag():
    """ext7 #54: Row 13 + Row 15 invoke LangFuse-trace analyzers — they MUST
    carry --session-ids (or --environment, which is a campaign marker for tests).

    Pre-ext7-#54 rows 13 + 15 lacked --session-ids; LangFuse trace pools from
    parallel smoke runs leaked into Chapter 6 T3 + Tier-2 evidence.
    """
    text = RUN_SHEET.read_text()
    rows = _parse_command_rows(text)
    offenders: list[str] = []
    for row_num, cmd in rows:
        m = re.search(r"scripts/(\w+\.py)", cmd)
        if not m:
            continue
        script = m.group(1)
        accepted = _LANGFUSE_ANALYZERS_TO_SCOPE.get(script)
        if accepted is None:
            continue
        if not any(flag in cmd for flag in accepted):
            offenders.append(
                f"  Row {row_num} — `{script}` invoked without "
                f"any of {sorted(accepted)}:\n    {cmd}"
            )

    assert not offenders, (
        "docs/run_sheet.md rows invoke LangFuse-trace analyzers without a "
        "session-scoping flag. Add --session-ids $CAMPAIGN_SESSION_IDS per "
        "ext7 #54:\n\n" + "\n\n".join(offenders)
    )


def test_campaign_session_ids_convention_present():
    """ext7 #54 regression guard: the $CAMPAIGN_SESSION_IDS env-var convention
    must be documented in the scoping-convention block so rows 13 + 15 have a
    concrete value to reference.
    """
    text = RUN_SHEET.read_text()
    assert "CAMPAIGN_SESSION_IDS" in text, (
        "run_sheet.md is missing the ext7 #54 CAMPAIGN_SESSION_IDS convention. "
        "Document it alongside CAMPAIGN_RUN_IDS in the Phase 4/5 scoping block."
    )
