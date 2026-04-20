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


# ---------------------------------------------------------------------------
# ext7 post-remediation fixes (discovered during thesis-alignment audit
# 2026-04-20): scoping convention examples + Chapter 3 evidence elevation.
# ---------------------------------------------------------------------------


def test_scoping_convention_uses_dynamic_discovery_not_fixed_literal():
    """The scoping block MUST populate CAMPAIGN_RUN_IDS dynamically from the
    DB, not with a literal 4-element example.

    Original ext7 #45 doc carried a literal 4-run-id example. A user who
    literally followed it on the canonical ``--n-runs 5 --all-conditions``
    Phase-4 campaign (20 distinct run_ids) would silently scope analyses to
    20% of the campaign — ``--run-ids`` bypasses ext7 #46's per-condition
    auto-select so the safety net doesn't fire.

    Post-fix the block uses a ``$(duckdb ... SELECT DISTINCT run_id ...)``
    subshell that reads every non-pilot run_id from ``$CAMPAIGN_DB``,
    adapting automatically to any ``--n-runs`` value.
    """
    text = RUN_SHEET.read_text()
    # Must have the dynamic-discovery subshell.
    assert 'SELECT DISTINCT run_id FROM evaluation_runs' in text, (
        "run_sheet.md scoping convention must populate CAMPAIGN_RUN_IDS via a "
        "dynamic DB query, not a literal example — see thesis-alignment audit "
        "2026-04-20 for the 4-vs-20 silent-scope-to-20%-of-campaign hazard."
    )
    assert "NOT is_pilot" in text, (
        "The CAMPAIGN_RUN_IDS discovery query must filter `WHERE NOT is_pilot` "
        "so Row 7 smoke-pilot rows don't contaminate the Phase-4 campaign scope."
    )
    # MUST NOT carry the original 4-id fixed-literal example.
    assert '"full_00_abc123 no_verification_00_def456' not in text, (
        "run_sheet.md still carries the pre-fix 4-run-id literal example. "
        "This is a documented hazard (see test docstring). Replace with the "
        "dynamic-discovery subshell pattern."
    )


def test_campaign_session_ids_mirrors_run_ids_not_single_value():
    """ext7 post-audit: LangFuse session_id == DuckDB run_id (harness.py:396).

    Canonical Phase-4 run has 20 run_ids → 20 session_ids, not 1. The run
    sheet's scoping block must reflect this — either by reusing
    ``$CAMPAIGN_RUN_IDS`` as ``$CAMPAIGN_SESSION_IDS`` or by populating
    session_ids via its own dynamic query. A single-literal example would
    mis-scope rows 13 + 15 to zero traces.
    """
    text = RUN_SHEET.read_text()
    # Must indicate session_id == run_id at the doc level, or explicitly
    # derive CAMPAIGN_SESSION_IDS from CAMPAIGN_RUN_IDS.
    assert (
        'CAMPAIGN_SESSION_IDS="$CAMPAIGN_RUN_IDS"' in text
        or "session_id == DuckDB run_id" in text
        or "session_id=run_id" in text
    ), (
        "run_sheet.md scoping convention must document that LangFuse "
        "session_id == run_id (harness.py:396) and populate "
        "CAMPAIGN_SESSION_IDS accordingly (e.g., $CAMPAIGN_SESSION_IDS="
        '"$CAMPAIGN_RUN_IDS"). A single-literal example mis-scopes rows '
        "13 + 15 to zero traces."
    )
    # MUST NOT carry the pre-fix single-literal example.
    assert 'CAMPAIGN_SESSION_IDS="eval_2026_04_20_full_final"' not in text, (
        "Pre-fix single-literal CAMPAIGN_SESSION_IDS example still present. "
        "Replace with the dynamic-reuse pattern."
    )


def test_chapter_3_fp_fn_evidence_on_main_path():
    """Chapter 3's §Pillar 1 line 77 claim (hierarchical embeddings prevent
    FP + FN) needs numbered-row evidence, not diagnostic-only placement.

    Pre-audit, ``test_registry_matching.py`` + ``test_registry_fn.py`` were
    listed only in the Diagnostic Scripts section. Thesis writer following
    numbered rows would reach Chapter 6 without the Chapter 3 evidence.
    Post-fix these are Phase-3 rows 6a + 6b.
    """
    text = RUN_SHEET.read_text()
    # Each script appears in a numbered row of the Phase 3 table.
    rows = _parse_command_rows(text)
    scripts_in_numbered_rows = set()
    for _, cmd in rows:
        m = re.search(r"scripts/(\w+\.py)", cmd)
        if m:
            scripts_in_numbered_rows.add(m.group(1))

    # Also pick up "6a"/"6b" rows which have non-numeric row numbers.
    for line in text.splitlines():
        if re.match(r"\|\s*6[ab]\s*\|", line):
            m = re.search(r"scripts/(\w+\.py)", line)
            if m:
                scripts_in_numbered_rows.add(m.group(1))

    for script in ("test_registry_matching.py", "test_registry_fn.py"):
        assert script in scripts_in_numbered_rows, (
            f"Chapter 3 evidence script `{script}` must be on the numbered "
            "main path (row 6a or 6b), not diagnostic-only. Proposal §Pillar 1 "
            "line 77 claim needs produced-by evidence in Chapter 3."
        )
